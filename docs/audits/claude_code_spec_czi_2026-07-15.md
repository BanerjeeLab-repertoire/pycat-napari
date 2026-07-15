# Claude Code spec — CZI reader: stress-test, build, thread, re-enable

**Date:** 2026-07-15 · **Target tree:** 1.6.57 · **Author of spec:** chat-side Claude (design), verified against the uploaded 1.6.57 tree.

## Read first
- `docs/audits/czi_streaming_unreadable_2026-07-15.md` — the investigation. It is accurate about the
  FILE and the libCZI failure, but its status line ("reader works, integration doesn't") is **stale**:
  the BioFormats reader code it describes is **NOT in the tree** (it was a loose-file drop that never
  landed). Only the `[bioformats]` extra in `pyproject.toml` made it in. So you are **building the
  reader, not re-enabling one.** Update that doc's status when done.
- `CHANGELOG.md` — recent context (1.6.50→1.6.57).

## Durable facts
- Repo `C:\Users\Gable\Documents\GitHub\pycat-napari`, env `pycat-160` (Python 3.12, CUDA cu118).
- **numpy is pinned `<2.1`** (cellpose + numba). Any dep that moves numpy is high-blast-radius —
  the `[bioformats]` extra already re-pins `numpy<2.1`; keep it.
- Delivery discipline: each CODE change → own version bump + PyPI push + commit with EXPLICIT
  filenames + CHANGELOG entry. Docs-only rides the next code commit. Build now works via plain
  `python -m build` (the Developer-Mode/pyexpat symlink issue was fixed with a sitecustomize shim).

## Test files (all in `C:\Users\Gable\Desktop\A pycat test data\`)
Characterized from their raw CZI headers (all 12-bit Gray16):

| file | dims | acquisition | subblocks | role in bake-off |
|---|---|---|---|---|
| `Image_28.czi` | 512×512, C=4 | LaserScanningConfocal | 4 | normal confocal, multi-channel |
| `Image_5.czi` | 1024×1024, C=3 | LaserScanningConfocal | 3 | normal confocal, larger |
| `ntr_wt_50mM_Mg4.czi` | 1936×1460, C=1 | **OpticalSectioning (widefield)** | 1 | **widefield, single frame — the key discriminator** |
| `Movie 5 - CAG31 100uM ... tphase40.czi` | 500×500, T=15766 | **OpticalSectioning/WideField** | 15,766 | the streaming file libCZI CANNOT read |

**The discriminating hypothesis** (from the audit + these headers): libCZI's failure is tied to the
**widefield-streaming / many-subblock** layout, not 12-bit-Gray16 per se. `ntr_wt_50mM_Mg4.czi` is
widefield but single-subblock — if libCZI reads IT but not the 15,766-frame movie, the problem is the
streaming layout specifically. That result decides the routing rule.

---

## Task 1 — Reader bake-off (measure, then pick the routing rule)

Do NOT hardcode "BioFormats always." Run an empirical comparison across all 4 files and pick the
routing rule from data. There is already an acceptance-test helper to build on:
`image_reader.py::compare_readers(path)`.

For each file, for BOTH libCZI (`bioio-czi`, the current default path) and BioFormats
(`bioio-bioformats`, the `[bioformats]` extra — `pip install pycat-napari[bioformats]` first):
1. Can it OPEN? (dims/shape correct?)
2. Can it READ pixels? (`get_image_dask_data("YX", T=t, C=0, Z=0)` then `np.asarray(result.compute())`
   — the `.compute()` returns a `LazyBioArray`, wrap in `np.asarray` before `.min()/.mean()`; a bare
   AttributeError there is THIS, not a read failure).
3. Timings: open+init, first plane, a few random planes. (BioFormats confirmed ~0.01–0.07 s/plane
   after a ~31 s one-time open on the streaming file; libCZI raises
   `RuntimeError: ... not implemented` on pixel read for the streaming file.)
4. Correctness: min/max/mean sane, not all-zero.

**Deliverable:** a short results table (per file × reader: opens? reads? timing) written into
`docs/audits/czi_bakeoff_2026-07-15.md`, and a decided routing rule. Expected outcomes to confirm or
refute: confocal files read by libCZI fine (fast, no JVM); the streaming movie needs BioFormats;
`ntr_wt_...` tells us whether "widefield" or "streaming" is the trigger.

**Routing rule candidates** (pick per the data):
- (a) `.czi` → try libCZI; on `not implemented` (or any pixel-read failure) fall back to BioFormats
  when the extra is installed. Preserves fast no-JVM reads for normal CZI, covers streaming.
- (b) `.czi` → BioFormats always when installed. Simpler, but pays the JVM/one-time-open cost on
  every CZI including small confocal ones that libCZI reads instantly.
Rule (a) is likely right IF libCZI reads the confocal files — the bake-off confirms.

---

## Task 2 — Build the CZI→BioFormats reader path

Anchors (verified in 1.6.57):
- `image_reader.py` — `open_image(path)` constructs `BioImage(path, **_reader_kwargs_for(path, kwargs))`
  (~line 285). This is where reader selection lives. Add: for `.czi`, apply the Task-1 routing rule
  — construct `BioImage(path, reader=bioio_bioformats.Reader)` when BioFormats is chosen.
- Guard the import: `try: import bioio_bioformats except ImportError:` → the existing
  `ImageReaderUnavailable`-style clear message ("CZI streaming needs `pip install
  pycat-napari[bioformats]`"). Do NOT crash with a raw "not implemented".
- `file_io.py::_open_stack_generic` (line 2358) is the CZI load path (`open_stack` at 2026 routes
  `.ims`→`_open_stack_ims` at 2095, everything else incl. `.czi`→`_open_stack_generic` at 2097). The
  per-plane lazy read here must wrap `np.asarray(dask.compute())` (LazyBioArray coercion).
- **Reader retention:** the BioFormats `BioImage`/JVM reader must stay alive for lazy plane reads.
  Attach it to the layer via the existing `ImageSource` pattern — `image_source.py::ImageSource.retain()`
  (line 67), attached as `layer.metadata['pycat_image_source']` (same as IMS/generic loaders already
  do). Lifetime = layer lifetime.
- **Lazy JVM init:** only construct the BioFormats reader when a CZI actually loads — never pay the
  JVM cost for TIFF/IMS sessions.

---

## Task 3 — Non-blocking open (the PRIMARY fix)

The unacceptable symptom: BioFormats init + subblock indexing (JVM + parsing 15,766 offsets) runs
SYNCHRONOUSLY on the Qt main thread → UI frozen 2–5 min (dead spinner, not a responsive "indexing"
state). This is a "don't block the UI" problem, not a "make it faster" one.

- Run the reader open/init on a **worker thread** so the Qt event loop stays alive. **Pick whichever
  threading primitive fits the load path cleanest** — the codebase already has QThread workers to
  copy (`toolbox/two_channel_coloc_tools.py` builds a `QThread` worker on first use, lines ~284–326;
  `toolbox/invitro_bf_ui.py` / `brightfield_ui.py` have `_*Worker(QThread)` patterns), or use napari's
  `@thread_worker`. Your call — match the existing load-path style.
- **Progress/notice during the ~mins index:** a determinate progress if BioFormats exposes a
  subblock-parse count, else a busy/indeterminate "Indexing CZI via BioFormats…" indicator that keeps
  Qt responsive. The point is the user sees WORK, not a hang.
- Thread-safety: layer construction / viewer mutation must happen back on the main thread (worker
  produces the reader + first frame; main thread adds the layer). The existing QThread workers show
  the signal-back-to-main pattern.

## Task 4 — Scrubbing prefetch/cache (secondary)

After open, reads happen in dask BLOCKS, not single planes; scrubbing into an uncached block stalls
at the boundary. (The audit's headless probe read SCATTERED single planes at 0.03 s and masked this;
sequential scrubbing hits block boundaries.)
- Tune the dask chunk shape for single-plane access AND/OR add a small LRU frame cache + prefetch of
  neighbouring frames (T±k) so forward/back scrubbing is smooth. The IMS path already does per-plane
  caching — mirror that. Secondary to Task 3; get the non-blocking open right first.

## Task 5 — Re-enable + guard

- There is currently **no CZI disable gate** in `open_stack` (contrary to the audit doc) — `.czi`
  routes straight to `_open_stack_generic`, so today a user opening the streaming CZI gets the raw
  "not implemented" crash. Once Tasks 2–3 land, that path works; no gate to remove. But if you add a
  temporary gate while building, remove it at the end.
- `.czi` is in the dialog filters at file_io.py lines 1494, 1735, 1860, 2074 — leave them (CZI is a
  supported format once this lands).
- Add a test: `tests/test_czi_bioformats_reader.py` — skip-if-`bioio_bioformats`-not-installed;
  assert the streaming file opens, dims match (T=15766, 500×500), a few planes read non-zero. Guard
  it behind the extra so CI without the JVM skips cleanly.

## Definition of done
- Bake-off table + decided routing rule in `docs/audits/czi_bakeoff_2026-07-15.md`.
- All 4 test CZIs load through `run-pycat`: confocal ones fast, streaming one via BioFormats.
- **Open does NOT freeze the UI** — worker-threaded, with a visible indexing indicator.
- Scrubbing the 15,766-frame movie is smooth (no multi-second stalls at block boundaries).
- Reader retained via ImageSource (no premature GC mid-scrub).
- Skip-if-no-JVM test asserting the streaming file reads correctly.
- Shipped as its own version bump + PyPI push + commit + CHANGELOG; audit doc status updated to reflect
  that the reader was BUILT (not "re-enabled").

## Cautions
- Keep `numpy<2.1` everywhere; the `[bioformats]` extra already re-pins it.
- Don't pay the JVM cost for non-CZI sessions (lazy init).
- libCZI stays the path for other people's non-streaming CZI if the bake-off says it reads them —
  don't rip it out.
- The tifffile-zarr 3.2 shim (`tifffile_zarr_shim.py`) is what let CZI get far enough to expose the
  libCZI issue; it's shipped and correct — leave it. Its other beneficiary, multi-channel TIFF lazy
  loading, still needs a GUI confirm (open a multi-channel TIFF via run-pycat) — worth doing while
  you're in this code.
