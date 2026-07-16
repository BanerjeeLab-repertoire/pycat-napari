# CZI streaming-acquisition files: unreadable by libCZI — investigation pin

**Date:** 2026-07-15
**Status (updated 2026-07-15, v1.6.61): READER BUILT + SHIPPED (opt-in), pending a GUI confirm.**
The reader was **built, not re-enabled** — the earlier BioFormats reader code described below was a
loose-file drop that never landed in the tree; only the `[bioformats]` extra had. What shipped in
1.6.61 (see `docs/audits/czi_bakeoff_2026-07-15.md` for the empirical basis):
- **Routing rule (a):** `.czi` tries libCZI first (fast, no JVM — reads confocal AND
  widefield-single-subblock fine); only the streaming/many-subblock layout diverts to BioFormats.
- **Pixels via the DIRECT BioFormats reader** (`readers/czi_bioformats.py`,
  `loci.formats.ImageReader.openBytes`, ~5 ms/plane) — NOT bioio's dask, which measured 50–80 s/plane
  here. Lazy (T,Y,X) wrapper, `__array__` refuses, reader retained via `ImageSource`.
- **Non-blocking open:** the ~33 s one-time frame-index parse runs on a `QThread` worker behind a
  busy "Indexing CZI…" dialog (`_run_with_busy_progress`), with a synchronous fallback.
- **Dependency reality (changed since this audit):** `bioio-bioformats 2.0.0` now requires
  `numpy>=2.1` (via `bffile`), which breaks PyCAT's `numpy<2.1` pin — so the extra pins `<2.0`, and
  PyCAT overrides the Java BioFormats version to `formats-gpl:8.1.1` (6.7.0 can't read the file) +
  registers the OME Maven repo at JVM start.
- **Verified headlessly:** reader opens the real 8.1 GB streaming CZI, reads planes non-zero at
  ~5 ms; unit + integration tests pass. **NOT yet GUI-confirmed** — the worker-thread anti-freeze UX
  and scrubbing smoothness need a `run-pycat` check on a machine with a display.

The rest of this doc is the original investigation, preserved for context.

---

_Original status (superseded):_ READER WORKS, INTEGRATION DOES NOT. BioFormats reads the pixels
correctly, but the GUI load freezes the UI for 2–5 minutes (main-thread blocking) and scrubbing lags
in chunks. CZI is TEMPORARILY DISABLED in the loader (clear notice shown) until the non-blocking
integration is built. Reader routing + shim + opt-in extra are in and correct; only the UX
integration remains.

---

## One-line summary

Gable's CZI files (fast streaming/timelapse — his *only* CZI format) cannot be read by **any**
libCZI-based reader (pylibczirw, aicspylibczi, both bioio-czi modes). Metadata reads fine and ZEN
opens them, but every pixel read raises `RuntimeError: The method or operation is not implemented`.
The only viable reader is **BioFormats** (independent Java decoder), which must be an **opt-in
extra** because it drags a JVM and fights numpy.

---

## The test file

`C:\Users\Gable\Desktop\A pycat test data\Movie 5 - CAG31 100uM - 50mM Mg 25mM Na 10mM tris tphase40.czi`

Ground truth from probes (all metadata reads succeed):
- Dims: `BTCYX`, shape `(B=1, T=15766, C=1, Z=1, Y=500, X=500)` — 15,766-frame time series.
- Pixel: `Gray16`, `ComponentBitCount: 12`, `BitCountRange: 12` (12-bit data in a 16-bit container).
- `Compression: false` (UNCOMPRESSED), `OriginalCompressionMethod: ABSENT`.
- `is_mosaic: False`, `scenes_bounding_rectangle: {}` (no scenes, not a mosaic).
- `AcquisitionMode: OpticalSectioning / WideField`.
- `total_bounding_box` X:(-252,248) Y:(-250,250) — negative-origin coords (a red herring, see below).
- **15,766 subblock metadata entries enumerate cleanly** (`read_subblock_metadata()` OK), one per
  frame, each `{'B':0,'C':0,'T':n}` with full XML (exposure `1100000`, `Frame: 716,480,500,500` =
  sensor readout region, NOT per-frame repositioning).

**Gable confirmed:** this streaming/timelapse format IS his CZI use case (not an edge case), it
MUST work before shipping, and **ZEN opens it** (lags a bit — big file). So the pixels are decodable;
the gap is in the open-source libCZI decode path, not the file.

---

## What was RULED OUT (do not retread these)

Every one of these was tested against the real file and FAILED identically with
`RuntimeError: The method or operation is not implemented` on the pixel read:

1. **pylibCZIrw direct** — `c.read(plane={'C':0,'T':0,'Z':0})` and every variant:
   explicit ROI `(x,y,w,h)`, zero-origin ROI `(0,0,w,h)`, `zoom=1.0`, no-plane, `scene=0`
   (scene=0 gave "scene index does not match" — confirms no scenes). So NOT a coordinate/ROI/
   negative-origin problem — the negative bounding box was a red herring.
2. **aicspylibczi 3.3.1 direct** — `read_image(T=0,C=0)`, `read_image(B=0,...)`,
   `read_image() no args`, `read_mosaic(...)` all fail. `read_image(M=0,...)` raises
   `M Dimension is specified but the file is not a mosaic file!` — confirms NOT a mosaic/tile
   assembly problem.
3. **bioio-czi, pylibczirw mode (default)** — dask graph builds, `.compute()` fails at
   `pylibCZIrw ... GetSingleChannelScalingTileAccessorData`.
4. **bioio-czi, aicspylibczi mode + `reconstruct_mosaic=False`** — BioImage constructs and reports
   correct dims/shape, but `get_image_data("YX", T=0, C=0)` still fails "not implemented".
5. **Compression** — ruled out; file is uncompressed (`Compression: false`).
6. **Reader/plugin version** — `bioio-czi 2.8.0` is the LATEST (Jun 2026). No newer plugin.

**Conclusion:** pylibczirw and aicspylibczi both wrap the same `libCZI` C++ core. The core lacks a
decoder for this file's pixel storage (most likely the **12-bit widefield streaming** layout).
No Python-side call or mode reaches it. This is a libCZI limitation, not a PyCAT/bioio/mode issue.

---

## The fix path: BioFormats as an OPT-IN extra — **CONFIRMED WORKING 2026-07-15**

BioFormats is the reference Zeiss decoder (Java). **It reads this file, lazily, at scrubbing speed.**
This is now the chosen fix, not a hypothesis.

### CONFIRMED timings (real, on the test file, numpy 2.0.2, via bioio-bioformats 2.0.0)
```
open + reader init : 30.90 s   ← ONE-TIME (JVM spin-up + parse 15,766 subblock offsets)
first plane  T=0   : 0.30 s    ← one-time warm
plane T=1          : 0.07 s
plane T=2          : 0.01 s
plane T=100        : 0.01 s
plane T=7000       : 0.03 s    ← random access, fast
plane T=15765      : 0.03 s    ← last frame, fast
min/max 0/65535, real means ~13000 → genuine pixel data
```
**Verdict:** ~0.01–0.07 s/plane after open = fully scrubbable. The only cost is a ~31 s ONE-TIME
open (JVM + subblock-offset parse), paid once per file load, NOT per frame. Mask it behind a
progress indicator ("Opening CZI via BioFormats…").

### numpy is NOT actually a conflict (when done deliberately)
The earlier break was ONLY because `pip install bioio-bioformats` left `numpy` unpinned and pip
grabbed 2.5.1. **BioFormats ran fine on numpy 2.0.2** (all timings above are on 2.0.2). So the
opt-in extra must PIN `numpy<2.1` and BioFormats works with zero conflict. The JVM is the only real
added weight.

### API notes learned (avoid re-discovering)
- Use `img.get_image_dask_data("YX", T=t, C=0, Z=0)` then `np.asarray(result.compute())`.
  `.compute()` returns a `LazyBioArray` wrapper, NOT a plain ndarray — wrap in `np.asarray()` before
  calling `.min()/.mean()/`etc. (A prior probe's AttributeError was THIS, not a read failure.)
- The `jar tool not found` warning during JVM install is harmless — the JRE loads and BioFormats
  runs regardless.
- First `BioImage(path, reader=bioio_bioformats.Reader)` triggers the JDK download via cjdk
  (~39 MiB, one-time) then the ~31 s parse.

### Implementation shape (ready to build next session)
- In `_open_stack_generic` (file_io.py): for `.czi`, use `bioio_bioformats.Reader` **iff installed**,
  via the lazy `get_image_dask_data` per-plane path (same lazy wrapper pattern as the rest of the
  loader). Wrap each plane read `np.asarray(dask.compute())`.
- Guard import: `try: import bioio_bioformats except ImportError:` → clear message
  ("CZI needs `pip install pycat-napari[bioformats]`").
- pyproject `[project.optional-dependencies] bioformats = ["bioio-bioformats"]` **with an explicit
  `numpy<2.1` pin preserved** so the extra can't trample the base env.
- **Lazy JVM init** — only construct the BioFormats reader when a CZI actually loads (don't pay the
  JVM cost for TIFF/IMS sessions).
- **Progress/notice for the ~31 s open** — a determinate-or-busy indicator so the user knows the
  one-time parse is working, not hung.
- Keep the existing libCZI path as the FIRST attempt is NOT worth it — libCZI can't read these at
  all, so for `.czi` go straight to BioFormats when installed. (libCZI may still be fine for OTHER
  people's non-streaming CZIs, so: try libCZI, on "not implemented" fall back to BioFormats — OR
  just prefer BioFormats for `.czi` when the extra is present. Decide at build time.)

### Reader retention note
BioFormats' `BioImage`/JVM reader must stay alive for lazy plane reads (same keepalive concern as
the IMS/generic loaders). Attach it to the layer via the ImageSource pattern (image_source.py) so
its lifetime = layer lifetime.

---

## GUI TEST RESULTS (2026-07-15) — reader works, INTEGRATION is the problem

Built the reader (image_reader.py CZI→bioformats routing, file_io.py LazyBioArray coercion + open
notice, pyproject `[bioformats]` extra) and tested the real streaming CZI through `run-pycat`.
Outcome:

- **It DID eventually render** — the pixels are correct, the reader works end-to-end in the GUI.
- **But the UI FROZE for 2–5 minutes** on open (spinning wheel, terminal stuck after the CUDA line,
  no progress). UNACCEPTABLE UX regardless of eventual success.
- **Scrubbing lags in chunks** — smooth within a cached block, stalls at each block boundary.

### Diagnosis (two distinct problems — do not conflate)
1. **UI freeze on open = main-thread blocking.** The BioFormats init + full-file indexing (JVM +
   parsing 15,766 subblock offsets) runs SYNCHRONOUSLY on the Qt main thread, so the event loop
   can't paint — dead spinner instead of a responsive "indexing…" state. The measured ~31 s in the
   headless timing probe became 2–5 min in the GUI (possibly JVM cold-start + larger real parse +
   contrast/first-frame work all on the main thread). NOT a "make it faster" problem — a "don't
   block the UI" problem.
   - `_lazy_contrast_limits(wrapper)` reads only `lazy_layer[0]` (one plane) — RULED OUT as the
     hang; the load path is lazy-correct, the block is the reader init itself on the main thread.
2. **Chunked scrubbing lag = dask/BioFormats block fetching.** Reads happen in blocks, not single
   planes; scrubbing into an uncached block stalls at the boundary. The headless probe read
   SCATTERED single planes (0.03 s each, looked fast) which masked this; sequential scrubbing hits
   block-fetch boundaries. Secondary to the freeze.

### The fix that's actually needed (real work — deferred, NOT tonight)
- **Non-blocking open:** run `open_image` / BioFormats init on a WORKER THREAD (napari has
  `@thread_worker` / superqt; or QThread) so the UI stays alive and can show a real determinate/
  busy "Indexing CZI…" indicator instead of freezing. This is the PRIMARY fix — the freeze is the
  unacceptable part.
- **Progress during the ~mins index:** surface actual progress (subblock parse count if BioFormats
  exposes it, else a busy spinner that at least keeps Qt responsive).
- **Scrubbing prefetch/cache:** tune the dask chunk shape and/or add a small LRU frame cache +
  prefetch of neighboring frames so scrubbing doesn't stall at block boundaries. Secondary.
- Keep the reader routing / shim / extra AS-IS — they're correct; only the UX integration is wrong.

### What was DISABLED to unblock the refactor
CZI loading is gated OFF in `open_stack` (file_io.py) with a clear notice, so testers don't hit the
2–5 min frozen UI. The reader code (image_reader.py routing, file_io.py coercion) is LEFT INTACT —
the threading fix re-enables it. Revert = remove the gate. See the disable block in `open_stack`.

---

## Interim option: temporarily DISABLE CZI to unblock the refactor

CZI routing today: `open_stack` (file_io.py ~line 1902) sends `.ims` → `_open_stack_ims`,
everything else (incl. `.czi`) → `_open_stack_generic`. `.czi` also appears in the file-dialog
filter strings at file_io.py ~1288, ~1543, ~1668, ~1882.

To temporarily disable (so users don't hit the confusing "not implemented" crash mid-refactor):
- Simplest: in `open_stack`, detect `ext == '.czi'` and show a clear notice ("CZI support is
  temporarily unavailable — see known issues; export to OME-TIFF from ZEN as a workaround") and
  return, instead of routing to `_open_stack_generic`.
- Optionally drop `*.czi` from the dialog filter strings so it isn't offered.
- Leave `_open_stack_generic`'s CZI code intact (don't delete) — the BioFormats fix reuses it.
- This is a UX guard only; revert is trivial when the BioFormats reader lands.

---

## Related / adjacent (do not lose)

- **The tifffile-zarr 3.2 shim (SHIPPED this session, committed):**
  `src/pycat/file_io/tifffile_zarr_shim.py` + wired in file_io.py + `tests/test_tifffile_zarr_shim.py`.
  It fixed the `zarr 3.2.1 < 3` error that was BLOCKING CZI (and multi-channel TIFF) at the
  tifffile/zarr layer. That fix is what let the CZI get far enough to expose THIS libCZI decoder
  issue. Multi-channel TIFF lazy loading is the shim's other beneficiary — **still needs a GUI
  confirm** (open a multi-channel TIFF via run-pycat).

- **numpy is pinned <2.1 for a reason** (cellpose + numba). Any future dep that wants to move numpy
  is suspect. This is the same wall that killed the tifffile version bump (tifffile 2026.5.2 drops
  numpy 2.0). Treat numpy moves as high-blast-radius, always.

- Throwaway probe scripts left in repo root to delete: `zarr_import_probe.py`,
  `find_regularchunkgrid.py`, `zarr_shim_probe.py`, `czi_probe.py`, `czi_probe2.py`,
  `czi_compression_probe.py`, `czi_subblock_probe.py`, `czi_rawblock_probe.py`,
  `czi_noreconstruct_probe.py`, `czi_bioformats_probe.py`, `dep_versions_probe.py`.
