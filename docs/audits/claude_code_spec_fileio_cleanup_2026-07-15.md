# Claude Code spec — File-I/O audit cleanup (6 unresolved items)

## ✅ STATUS (updated, 1.6.64) — items 4/1/2/3 DONE; items 5 & 6 need Gable's decision
- **Item 4 ✅** PKG-INFO untracked + gitignored; README credit fixed.
- **Item 1 ✅** Generic loader is ImageSource-only; `_stack_lazy_refs` removed; T-Z retention bug fixed.
  Validated by the reader-retention guard (pytest-qt, offscreen).
- **Item 2 ✅** Reader cache closes on evict/clear/rewind-drop; `ImageSource.retain()` marks readers so
  a still-owned one is never closed. New `test_reader_cache_closes.py`.
- **Item 3 ✅** Removed the unconditional `pycat_stack_*` temp dir + `_stack_zarr_paths`; fixed the false
  "(zarr-backed)" labels → "(lazy, dask-backed)".
- **Item 5 ✅ (partial, per Gable's decision) — 3 of 5 sites converted to `to_unit_float32`:** the IMS
  single-frame (the real bug), the generic tifffile fallback (eager arrays; a `_TiffPageStack` is
  passed through unchanged since it is already [0,1]), and the PIL 2-D fallback. **FRAP** and
  **session-restore** left as intentional (raw / per-image min-max). The systemic raw-vs-[0,1] split in
  the generic loader's **dask branches** (below) is NOT addressed and is left for a dedicated
  intensity-consistency pass. Original per-site analysis:
  - `file_io.py::_open_stack_ims` single-frame (`pos_reader[...].astype(float32)`) — **likely a real bug.**
    `load_into_viewer` normalises via `img_as_float32`, which does NOT rescale a float input, so the
    premature `.astype(float32)` leaks RAW COUNTS into analysis, while a multi-frame IMS (via
    `_ImsReaderTYX`→`to_unit_float32`) is [0,1]. Recommend: drop the `.astype` (let load_into_viewer
    normalise the uint) OR use `to_unit_float32`.
  - `readers/stack_layer_builders.py::build_tifffile_fallback_wrapper` (`arr.astype(float32)`) — part of
    a LARGER inconsistency: the generic loader's **dask branches** wrap `get_image_dask_data(...)` in
    `_LazyArraySource` with NO normalisation (raw counts), while the **tifffile-page branch**
    (`_TiffPageStack`) is [0,1]. So the fallback isn't uniquely wrong — the whole generic loader mixes
    raw and [0,1]. This is a systemic design decision, not a one-line fix.
  - `readers/image_reader_2d.py` PIL fallback (`astype('float32')`) — inconsistent with the normal 2D
    path ([0,1] via `dtype_conversion_func` in the controller). Rare NumPy-2.0 path.
  - `frap_io.py` — FRAP recovery fitting; **likely intentional raw** (leave).
  - `session_loader.py` — already normalises via per-image **min-max** `(arr-mn)/(mx-mn)`, which differs
    from `to_unit_float32`'s dtype-max. For a single restored image min-max is defensible; for analysis
    consistency with a fresh load, dtype-max would match. **Judgment call.**
  → I did NOT convert any of these (silent Csat/partition corruption risk). Which should change?
- **Item 6 ⏸ DEFERRED — recommend LEAVE.** The `microns_per_pixel_sq = 1` sentinel is made safe by the
  plausibility gate + sentinel tests, which encode the "1 means prompt" contract; switching to `np.nan`
  would fight those tests for little value. The spec agrees it's optional/lowest-priority.

---


**Date:** 2026-07-15 · **Target tree:** 1.6.61 · Verified against the uploaded 1.6.61 tree.
Addresses the re-audit's six *Still-unresolved* items. The BioIO perf audit is ~95% done; this closes
the remaining file-I/O gaps.

## ⚠️ SEQUENCING — do this AFTER decomposition #5 lands
Items 1, 3, and 5 live INSIDE `_open_stack_generic`, which decomposition #5
(`claude_code_spec_fileio5_generic_2026-07-15.md`) is currently breaking into sub-readers. **Doing
this cleanup on the decomposed sub-readers is far cleaner than on the 542-line monster.** So: **#5
ships and commits FIRST → then this cleanup.** Line numbers below are from the pre-#5 1.6.61 tree and
WILL move once #5 restructures the method; find the code by behaviour/marker, not line number.

Each item ships as its own version bump + PyPI push + commit (EXPLICIT filenames) + CHANGELOG entry —
they're independent; do them in this order (safest first), compiling between each.

---

## Item 4 (do FIRST — trivial, unblocks a clean baseline): stale `aicsimageio` in PKG-INFO
**Verified:** `PKG-INFO` line 55 still declares `Requires-Dist: aicsimageio>=4.14.0`, and line 851
credits AICSImageIO — but aicsimageio was replaced by BioIO in 1.6.0 and is no longer a dependency
(`test_install_routes_agree.py::test_NO_install_route_still_ships_aicsimageio` already guards this).
`PKG-INFO` is GENERATED from `pyproject.toml` at build — so the stale line means either a stale
committed `PKG-INFO` or a stale dependency declaration somewhere the build reads.
**Fix:** confirm `pyproject.toml`'s `[project.dependencies]` has NO `aicsimageio` (it doesn't — only
comments mention it). Then the committed `PKG-INFO` is a stale artifact: regenerate it (`python -m
build` rewrites it) and confirm the fresh `PKG-INFO` no longer lists aicsimageio. If `PKG-INFO` is
checked into git, it shouldn't be — it's build output; consider gitignoring it. Update the README
credit line if desired (BioIO, not AICSImageIO). Small, no code risk.

---

## Item 1 (highest value): finish the generic-loader ImageSource migration
**Verified hybrid state:** `_open_stack_generic` ALREADY attaches `metadata['pycat_image_source']` on
the TYX/ZYX/TZYX/fallback branches (lines 2036, 2069, 2103, 2764) via an `ImageSource` built at ~1930
— BUT it STILL ALSO appends to `self._stack_lazy_refs` at lines 2284–2285 (init), 2322, 2465, 2467,
2526. So retention is owned by BOTH mechanisms. The audit's point: the IMS loader is cleanly
ImageSource-only; the generic loader is half-migrated, and the guard test
`tests/test_generic_stack_reader_retention.py` expects the ImageSource attachment on EVERY generic
layer with NO `_stack_lazy_refs` fallback (see its lines 136–145).
**Fix (after #5, on the sub-readers):** make `ImageSource` the SOLE owner for the generic loader, as
it is for IMS:
- Ensure every generic layer branch attaches `metadata['pycat_image_source'] = _img_source` and that
  `_img_source.retain(...)` holds the reader/dask handles those branches currently push into
  `_stack_lazy_refs`.
- Remove `self._stack_lazy_refs` from the generic loader entirely (init + all 5 appends). Grep for any
  OTHER reader of `_stack_lazy_refs` first (it may be referenced in clear/GC paths) — migrate those to
  read from the layer's ImageSource, mirroring how IMS was done (1.6.33 removed `_stack_lazy_refs`
  there; follow that changeset as the template).
- The guard test must pass WITH the `qapp` fixture (needs `pytest-qt`; the audit couldn't run it — CI
  has it). It asserts the ImageSource is attached and survives GC when only layers are held.

## Item 2: reader cache must CLOSE readers on evict/clear/failed-scene
**Verified:** `image_reader.py` has `_READER_CACHE` (bounded to 4) with `clear_reader_cache()` (line
149) and eviction (~line 240+), but they DROP readers without calling `.close()` — on Windows an
unclosed BioIO/tifffile/BioFormats reader can keep a file HANDLE, blocking re-open/delete.
**Fix:** wherever the cache discards a reader — `clear_reader_cache()`, LRU eviction when inserting a
5th, and the failed-scene reset path — call the reader's close if it has one:
```python
def _safe_close(reader):
    for attr in ("close", "__exit__"):
        fn = getattr(reader, attr, None)
        if callable(fn):
            try: fn() if attr == "close" else fn(None, None, None)
            except Exception: pass
            return
```
Apply on every discard. BUT: **do not close a reader still owned by a live layer's ImageSource** — the
cache and ImageSource can hold the same reader. Only close on cache eviction if the ImageSource
retention is separate (it dedups by path via `retain()`); safest is that ImageSource holds its OWN
reference and the cache closing only affects the cache's copy. Confirm the ownership model so eviction
doesn't close a reader a scrubbing layer still needs — add a test:
`tests/test_reader_cache_closes.py` asserting a fake reader with a `.close()` gets closed on evict +
clear, and that a still-retained reader is NOT closed.

## Item 3: remove obsolete Zarr scaffolding
**Verified:** the generic loader unconditionally creates `tempfile.mkdtemp(prefix='pycat_stack_')`
(line 2279) and still labels TZYX/time-series layers "(zarr-backed)" (lines 2559, 2662) — but the
synchronous full-file zarr TRANSCODE was already removed (the audit confirms TZYX no longer transcodes).
So the temp dir is created even when nothing is written to it, and the "zarr-backed" claim is now false.
**Fix (after #5):**
- Make the `pycat_stack_*` temp-dir creation CONDITIONAL — only create it if a branch actually writes
  a zarr store (grep what still writes into `zarr_dir`; if nothing does post-transcode-removal, remove
  the mkdtemp and the dir entirely, plus any cleanup registration for it).
- Fix the two log strings: drop "(zarr-backed)" / "(zarr-backed, ...)" — describe what the layer
  actually is now (page-lazy tifffile wrapper / dask-backed). A log that misdescribes the backing store
  is exactly the kind of stale-reader-name the suite already guards against
  (`test_no_stale_reader_names.py`) — make sure this doesn't trip it.

## Item 5: standardize intensity normalization on the canonical helper
**Verified:** `to_unit_float32` (`stack_access.py:463`) is the canonical normalizer (correct divide —
see its comment at 508 about being "wrong by one" if done naively). But several paths still do raw
`astype(np.float32)` (raw 0–65535 counts, NOT normalized):
- `file_io.py:1989` — IMS positioning frame (`pos_reader[...].astype(np.float32)`)
- `file_io.py:2318` — a generic fallback array (`arr.astype(np.float32)`)
- `frap_io.py:87` — FRAP loading (`np.asarray(scan.get_image(channel)).astype(np.float32)`)
- `session_loader.py:383` — session restoration (`tifffile.imread(...).astype(np.float32)`)
- `readers/image_reader_2d.py:30` — PIL fallback frames
**Fix:** route each through `to_unit_float32(arr, src_dtype=...)` so intensity provenance is uniform
(same 0–1 normalization the lazy wrappers use), UNLESS a given path intentionally wants raw counts —
CHECK each: FRAP fitting and session-restore may specifically need to match what was SAVED. **This is a
per-site judgment**, not a blind replace: normalizing a path that downstream expects raw counts would
silently corrupt values. For each of the 5 sites, confirm what the consumer expects before converting;
convert only the ones that should match the canonical lazy-wrapper normalization. Flag any you're
unsure about rather than guessing (Gable decides — intensity semantics are load-bearing for
partition/Csat).

## Item 6: the `microns_per_pixel_sq = 1` sentinel
**Verified:** `stack_load.py:85` sets `dr['microns_per_pixel_sq'] = 1` as the unknown-calibration
fallback. The newer plausibility gate + pixel-size-sentinel tests
(`test_pixel_size_sentinel.py`, `test_pixel_size_plausibility.py`) make `1` much safer (it's now
treated as "unset/prompt", not a real measurement), so this is LOW priority. But `1` as a
magic-sentinel is still a latent trap (a genuine 1.0 µm/px image is indistinguishable from "unknown"
without the provenance flag).
**Fix (optional, lowest priority):** consider making the unknown value `np.nan` internally (as
`pixel_size.py` already does for its accessor — `test_pixel_size.py::test_unknown_pixel_size_is_nan_not_one`),
with the sentinel meaning carried by a provenance flag rather than the value `1`. **Only do this if it
doesn't fight the existing gate tests** — they encode the current `1`-means-prompt contract, so this
may be more churn than value. Confirm with Gable before touching; it's fine to leave as-is given the
guards.

---

## Definition of done
- PKG-INFO no longer declares aicsimageio (item 4).
- Generic loader is ImageSource-only; `_stack_lazy_refs` gone from it; the generic-retention guard
  test passes under `qapp` (item 1).
- Reader cache closes readers on evict/clear/failed-scene without closing still-retained ones; new
  cache-close test passes (item 2).
- No unconditional `pycat_stack_*` temp dir; no false "zarr-backed" labels (item 3).
- Intensity paths that should be canonical use `to_unit_float32`; the ones that intentionally stay raw
  are confirmed with Gable (item 5).
- Item 6 either done cleanly or explicitly deferred with a note.
- Each item its own version + PyPI push + commit + CHANGELOG. Update the audit doc to mark items
  resolved.

## Cautions
- **After #5.** Confirm `git log` shows #5's sub-readers committed before starting items 1/3/5.
- Items 1 and 2 both touch reader OWNERSHIP — get item 1 (ImageSource sole owner) right FIRST, then
  item 2 (cache close) can reason about "is this reader still owned by a layer?" cleanly.
- Item 5 is a per-site judgment (raw vs normalized), NOT a blind sweep — wrong normalization silently
  corrupts intensity, which feeds partition coefficient / Csat. Flag unsure sites for Gable.
- Behaviour-preserving except where the audit says behaviour was WRONG (the false "zarr-backed" label,
  the leaked file handles) — those are the fixes.
