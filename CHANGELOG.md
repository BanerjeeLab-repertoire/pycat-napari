# Changelog
All notable changes to PyCAT-Napari will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.6.72] - 2026-07-16
### Fixed — **The per-layer axis tag was unreadable, so every axis warning named the wrong file.**
- `get_tags(layer)` returns a **list** of tag records. Four call sites treated it as a mapping —
  `(get_tags(layer) or {}).get('stack_axis', {}).get('value')` — which raises `AttributeError`
  straight into a bare `except Exception`. The failure was swallowed and the branch silently did
  nothing: **the layer carried the tag and the code could not hear it.** `get_tag(layer, key)` was
  the correct accessor, two functions away, the whole time.
- Consequence: `warn_if_assumed_axis` always fell back to the shared session label, so opening a
  movie (labelled T) and then adding a z-stack (labelled Z) made the **movie** warn as 'Z'. That is
  the exact bug the per-layer tag was added to fix. An MSD on a mislabelled stack computes a rate
  per frame, and *T and Z load identically — nothing on screen reveals it.*
- **Why it survived:** `get_tags(layer) or {}` works fine for a layer with NO tags (`[] or {}` →
  `{}`), and only breaks once a layer has one — i.e. exactly when the branch had work to do.
- **Why the tests missed it:** `test_axis_is_per_layer.py` monkeypatched `get_tags` to return a
  dict-shaped fake, validating a data model that does not exist in production. It now drives the
  real store through `tag_layer` and **fails against the old accessor** (verified by reverting).
- The other three sites (`file_io._is_reconstructable`, `session_manifest._is_source_image_layer` /
  `_is_reconstructable`) had the same bug *plus* read tag keys that nothing writes — `'operation'`
  and `'origin'`, where the vocabulary has `op` and `provenance`. Doubly dead; the name-based checks
  were carrying those functions alone. Now pointed at the real keys. (No upscale op is registered
  yet, so those two branches stay inert — but they are inert against a real key instead of a lie.)
### Added — **Z depth now reaches the viewer, for every reader, from one shared place.**
- Physical z-depth reached the measurements but never the display: `_enable_auto_scale_bar` wrote
  only `sc[-1]`/`sc[-2]` (Y, X) and left leading axes at 1.0, so a calibrated z-stack rendered
  **~15x stretched** (Z=1.0 against ~0.065 µm/px) — for IMS, TIFF and CZI alike.
- `axis_order` (`'YX'`/`'TYX'`/`'ZYX'`/`'TZYX'`) is a new core tag, written for **every** layer at
  the single load-time chokepoint (`tagging._tag_loaded_layer`), and `napari_adapter._apply_z_scale`
  puts `z_step_um` on whichever axis is Z. **Deliberately not in any reader** — a z-scale wired into
  each loader is how they drift apart, invisibly, since a stack with the wrong aspect still looks
  like a stack. A format added later inherits this by tagging its layers like everything else.
- A tag is unavoidable: **a (N, Y, X) movie and a (N, Y, X) z-stack are the same array.** ndim cannot
  tell them apart, and a z-step on axis 0 of a movie would stretch time. Missing or contradictory
  tag → decline rather than guess.
- Verified end-to-end in real napari: a z-stack declaring `PhysicalSizeZ=0.30`/`PhysicalSizeX=0.065`
  now yields `layer.scale == (0.3, 0.065, 0.065)` — aspect 4.62, the true anisotropy.
- `tests/test_z_scale_is_shared.py`, `tests/test_layer_tag_accessors.py` (both `core`).
### Fixed — **The T/Z dialog answer is finally authoritative.**
- An undeclared multipage TIFF has no axis metadata, so BioIO puts the pages on **T**. PyCAT asks
  the user "time-series or z-stack?" — and then threw the answer away: it was recorded and tagged,
  but `n_t`/`n_z` were never touched. Answering "Z-stack" produced `stack_axis='Z'` **and**
  `dimensionality='2d+t'` — two tags on one layer contradicting each other, on exactly the file
  where the question was asked. Resolved once at the chokepoint, before either tag is written, so
  the answer now reaches the viewer as a real z-scale.
### Notes
- Unknown z-step **renders** isotropic (the lateral pixel size) rather than leaving the placeholder
  1.0, which is a ~15x artifact of the placeholder rather than anything about the specimen. Display
  only: `z_step_um` still returns NaN and 3-D volumes are still NaN — *a stretched picture is not a
  wrong number*. `z_step_um`'s own once-per-session warning puts the unknown on the record at load,
  when the user can still set it in the metadata panel.
- `_align_layer_scales` needed no change: it rebuilds from the layer's current scale and only ever
  writes `[-2:]`, so a leading Z survives.
- `_tag_loaded_layer` grew past the 120-line complexity ceiling and was **split** (into
  `_resolve_stack_axes` + `_tag_layout`), not granted a raised ceiling — the ratchet only moves down.
- Still open, noted not smuggled: `_is_source_image_layer` remains an OR, so a derived layer whose
  name contains the source stem is still a false positive unless it hits the hardcoded
  `derived_markers` list. Making `provenance` authoritative over the name would change which layers
  the save dialog pre-selects.

## [1.6.71] - 2026-07-16
### Fixed — **A z-stack TIFF did not load at all. Now it loads and scrubs natively.**
- The generic loader's Z (`ZYX`) and T+Z (`TZYX`) branches were **dask-only**, and BioIO reads TIFF
  pixels through tifffile's `aszarr()` store, which is broken on zarr 3.2. So the layer was built and
  then died on the first plane read with `ValueError: zarr 3.2.1 < 3 is not supported` — a message
  that is itself misleading (tifffile fails to import a symbol a newer zarr renamed, and reports it
  as a version problem). **Only the T branch had ever been given the native tifffile cure.**
  Reproduced before the fix, and verified after, by driving the real `_open_stack_generic`.
### Added
- `_TiffPageStackZYX` (ndim=3, `(Z,Y,X)`) and `_TiffPageStackTZYX` (ndim=4, `(T,Z,Y,X)`) in
  `file_io/lazy_sources.py` — the Qt-free module 1.6.70 created, which is what lets these be
  exercised headlessly. One lazy plane read per (t, z); the file handle is held open for the
  wrapper's life; `[0,1]` normalisation from the source dtype; `__array__` refuses. Re-exported from
  `file_io.py`.
- The Z/T+Z loader branches now try the native TIFF reader first and fall back to dask only for
  formats with no tifffile route (CZI) — the same shape `build_timeseries_wrapper` already used for
  T, now factored into a shared `_native_tiff_or_dask` so the three branches cannot drift.
- `tests/test_ztz_readers_agree.py` (`core`) — the deliverable that proves *consistent*: it drives
  the **real** TIFF wrappers and the **real** IMS wrappers over the same volume and demands
  identical shape/ndim/dtype/len and identical pixels across every index pattern. The IMS wrappers
  need only `.shape` + `__getitem__`, so a numpy stand-in exercises the genuine IMS code with no
  `.ims` file and no `imaris_ims_file_reader` — a real cross-reader comparison, not stub-vs-stub.
  Mutation-checked: reversing the TZYX squeeze order fails it.
### Notes — two obvious implementations that are wrong, recorded so they are not retried
- **The page index is not a formula.** `frame = ((t*n_z)+z)*channels+c` is only `_legacy_geometry`,
  the fallback for a file that declares *no* axes. The primary map `tiff_planes._page_and_slice` is
  a mixed-radix fold over the axis order **the file declares**, so `ZTYX`/`CTZYX` resolve correctly.
  Hardcoding the formula puts a real, plausible, wrong plane on screen. Pinned by a Z-major test.
- **`read_tiff_plane` is the right arithmetic in the wrong host.** It reopens the file and rebuilds
  `series[0]` (re-walking the OME-XML) on *every call*: measured 3.61 ms/plane vs 0.17 ms/plane with
  a cached handle, and the gap grows with the OME-XML. napari asks for a plane per slider tick, so
  the wrappers cache the handle and reuse only the index arithmetic.
- **Z depth still does not reach `layer.scale` — for any reader.** It reaches the *measurements*
  (`z_step_um`, NaN when unknown; pinned for TIFF here), but the viewer renders every z-stack at
  unit Z aspect regardless of format. That is consistent, not correct, and fixing it needs a
  per-layer axis-order record that does not exist yet. See `roadmap.rst` — it is its own next piece.
### Changed
- The z-stack / T+Z load messages no longer hardcode "(dask-backed)"; the label is read off the
  wrapper that was actually built (`_lazy_backing_label`). Hardcoded labels are how these messages
  came to announce "(zarr-backed)" long after the zarr transcode was deleted (cleanup item 3).
- `test_stack_layer_builders_extraction.py::test_zarr32_error_is_translated_for_tiff` **asserted that
  a TIFF z-stack always raises** — it encoded the bug as the contract. It now pins the translation
  for the case it still applies to: the native reader declining and the dask fallback failing.

## [1.6.70] - 2026-07-16
### Changed — **GUI-free `lazy_sources.py`: the TIFF lazy wrappers no longer live behind a Qt import.**
- `_TiffPageStack` and `_LazyArraySource` sat in `file_io.py`, beside two `QDialog` subclasses in a
  module that imports PyQt5 at module scope. **So reaching a TIFF lazy wrapper dragged in the whole
  GUI stack**, and the wrappers could not be exercised headlessly — exactly what a performance
  harness or a CI perf gate needs to do (a roadmap item since the BioIO migration). Their bodies
  never needed Qt: they use `tifffile`, `numpy`, and two already-shared helpers. **Only their address
  did.**
- Both classes moved **verbatim** to `file_io/lazy_sources.py`, which is **Qt/napari-free by
  contract**. The OME file-set helpers `resolve_ome_file_set` / `build_ome_page_map` moved with them:
  `_TiffPageStack.__init__` calls both unconditionally, they have no other caller, and
  `lazy_sources` cannot import them back from `file_io` — that would be a hard circular import, since
  `file_io` now imports `lazy_sources`.
- `file_io.py` **re-exports** all four names (`# noqa: F401`), the same courtesy the `stack_access`
  re-export already provides, so existing `from pycat.file_io.file_io import _TiffPageStack` callers
  resolve unchanged. `file_io.py` drops 3061 → 2691 lines.
- **Behaviour-preserving.** Same lazy reads, same shapes, same dtypes, same `[0, 1]` normalisation
  from the source dtype, same `__array__` refusal. A pure move for testability.
### Added
- `tests/test_lazy_sources_headless.py` (`core`) — the contract the roadmap wanted: the wrappers
  exercised **without Qt**. Checks the Qt-free import statically (no GUI import at module scope, the
  same check `test_headless_science` applies elsewhere) *and* at runtime **in a fresh subprocess** —
  an in-process `'PyQt5' not in sys.modules` assertion is worthless, because `test_ui_smoke.py`
  imports PyQt5 at module scope and would make it fail for unrelated reasons. Also asserts a plane
  read is bit-identical to a full read (normalised the same way), that the `__array__` guard survived
  the move, and that `file_io`'s re-exports are the *same objects*.
- Its pycat imports are deliberately inside the test bodies: `conftest.py`'s `pytest_ignore_collect`
  drops any test module whose module-scope imports name `pycat.file_io` when the GUI stack is absent,
  and a headless-contract test that silently vanishes from the headless CI job is worse than none.
### Fixed
- `test_tiff_planes.py::test_every_TiffPageStack_CONSTRUCTION_has_enough_arguments` parsed
  `file_io.py` for the `_TiffPageStack` **class definition** — a re-export is invisible to
  `ast.ClassDef`, so the move would have tripped its `assert definition is not None`. It now reads the
  arity from `lazy_sources.py` and scans the whole `file_io` package for call sites. It also now
  counts calls made through the injected name `tiff_page_stack_cls`: since decomposition #5 the
  loader injects the class rather than importing it, so **the guard had no calls left to check** and
  the arity bug it exists to catch would have sailed straight through.

## [1.6.69] - 2026-07-16
### Fixed — **`core` (headless) CI failed: `viewer_load` dragged napari in at import.**
- `test_load_into_viewer_scale.py` (marked `core`, runs with NO napari installed) imports
  `file_io/viewer_load.py`, which imported `add_image_with_default_colormap` from `ui.ui_utils` at
  MODULE scope — and `ui_utils` imports napari at module scope. So the import chain pulled napari
  before the test's monkeypatch could neutralise it → `ModuleNotFoundError: No module named 'napari'`.
  The `ui_utils` import is now LAZY (bound inside `load_into_viewer`, imported on first real call),
  with the module-level name kept as a patchable attribute so the test's
  `monkeypatch.setattr(viewer_load, 'add_image_with_default_colormap', ...)` still works. `viewer_load`
  is now headlessly importable; production behaviour is unchanged. This is the same headless-import
  contract the `test_headless_science` guards enforce for the science modules.


## [1.6.68] - 2026-07-16
### Added — **OperationSpec increment 1: a typed view over the operation vocabulary + a drift guard (validate-first, generates nothing).**
- An architecture audit flagged that one operation's identity is separately encoded in the UI, batch,
  Navigator, tag system, and science function — five encodings that can drift. The eventual cure is a
  canonical `OperationSpec` the subsystems are generated from. This is the **safe foundation**: define
  the spec as a read-only VIEW over the one place the identity already lives (the `@tags_layer` /
  UI-op registry, `tag_registry._OPERATIONS`) and make any divergence from the committed Navigator
  snapshot a **test failure**. Nothing is generated into any subsystem yet.
- `navigator/operation_spec.py`: frozen `OperationSpec` (increment-1 fields only — `id, role, summary,
  target, produces, aliases, registered_by`; `inputs`/`parameters`/`batchable` deliberately deferred to
  a later increment *with* their validation) + `iter_operation_specs()`, a typed view that imports the
  tag-bearing modules (AST-discovered, so a new decorated module is picked up automatically) and reads
  `_OPERATIONS`. No new source of truth.
- `tests/navigator/test_operation_spec_matches_catalog.py` (core): the drift guard — **coverage** (every
  live op is in `operation_catalog.json`), **no-stale-layer-ops** (every catalog layer op is still live),
  and **field-fidelity** (`role`/`produces`/`target` match the decorator). Replaces the old
  hard-coded `cellpose`/`clahe` handful check (folded in, not lost). Runs headlessly — no tag-bearing
  module imports Qt at module scope.
- `op_catalog.regenerate_operation_catalog()` + `python -m pycat.navigator.op_catalog --regenerate`: the
  fix-it path so a failing guard is satisfiable ("run the regen, commit the JSON" — the failure messages
  name it). Regenerated once: the snapshot is now deterministic (id-sorted, sorted keys) and **provably
  faithful — zero drift**.
- **The "79-vs-69" question, resolved:** the snapshot's 79 entries are **63 `@tags_layer` decorator ops
  + 16 UI-registered ops** (`clahe`, `hand_drawn`, the merges…), all layer ops — NOT 69+10 measure-ops.
  The `_measure_ops()` set is injected at *build* time and was never stored in this JSON; there are zero
  stale entries. Batch `_STEP_MAP` is deliberately out of scope (different granularity; its own later spec).

## [1.6.67] - 2026-07-16
### Fixed — **Complexity ratchet was RED (147 > 139); restored by splitting UI builders, not by raising the ceiling.**
- The downward-only complexity ratchet (`test_complexity_budget.py`) fired: recent feature work pushed
  the number of functions over 120 lines from 139 to **147** (8 new offenders), blocking CI. The
  forbidden fix is to raise `_MAX_LONG_FUNCTIONS` — that silently grandfathers the new complexity and
  makes the guard worthless. Instead, **12 pure-Qt UI-builder functions** (`_add_*` / `_on_run` /
  `_on_finished` / `_on_dynamic` — widget construction and signal wiring, **zero numerical science**)
  were each split by extracting a contiguous widget block into a same-file helper: `_add_analysis`
  (frap), `_add_pipeline_snr_analysis`, `_add_spatial_randomness`, `_add_ts_upscale_stack` + `_on_finished`
  (timeseries_condensate), `_on_finished` (ts_cellpose), `_add_molecular_counting`, `_add_run_cellpose_segmentation`,
  `_add_run_sacf_analysis`, `_on_dynamic` (advanced_analysis), and both `_on_run` (invitro_fluor). Count
  **147 → 135**; the ceiling was **lowered** to 135 (the ratchet moving down — it working). No science
  function was touched. As a bonus, hoisting the `_on_dynamic` worker-setup to module level shrank the
  codebase's longest function `_add_advanced_analysis` from 677 → **637** lines.
- Behaviour-preserving: every split is a verbatim contiguous move (same widgets, order, signals); full
  `pytest -m core` green (580 passed), pyflakes clean, all touched modules import. The deliberate shrinks
  are recorded in `test_nothing_was_dropped.py::_DELIBERATE` and `test_no_input_mutation.py` with reasons.

## [1.6.66] - 2026-07-16
### Fixed — **The dual `px … | µm …` status-bar readout was invisible on EVERY file (wrong napari dict key).**
- Traced from where napari draws the status bar back to the source. napari 0.7.x's `Layer.get_status`
  returns a dict with **two** coordinate keys: `status_dict['coordinates']` (= `"coords: value"`, the
  string the Qt status bar actually renders for a single selected layer, via
  `qt_main_window._status_changed → setStatusText(coordinates=…)`) and `status_dict['coords']` (used
  only by the multi-layer/grid path). The 1.6.62 "match get_status return type" fix injected the PyCAT
  dual string into **`coords`** — the key the single-layer draw path IGNORES — so napari kept rendering
  its **own** `coordinates` (unlabeled world numbers + the layer name) and the labeled `px … | µm …`
  readout never appeared for any file. Symptom looked like "only px + filename, no µm" and read as if the
  pixel-size metadata was being overridden. **The metadata was fine** — verified end-to-end through
  `open_image_auto → open_2d_image → load_into_viewer → _enable_auto_scale_bar` on real napari `Image`
  layers: `layer.scale` reaches `[0.0264, 0.0264]` and `scale_bar.unit='um'` correctly. Fix: the wrapper
  now writes `native['coordinates']` (and keeps `native['coords']`), so the dual readout lands whichever
  path napari takes. Regression guard: `test_coordinate_readout.py::test_wrapper_injects_the_coordinates_key_napari_renders`.
- This is the display half of the "µm readout not fixed" report; the adaptive-precision and
  filename-declutter work from 1.6.65 was correct but never visible because of the key mismatch above.

## [1.6.65] - 2026-07-16
### Fixed — **GUI-confirm follow-ups: pixel size now reaches the status-bar readout; OME-TIFF scale recovered in the stack loader; CZI open shows a live counter.**
- **Status-bar coordinate readout was showing a coarsely-rounded µm that looked frozen, plus filename
  clutter.** The dual `px … | µm …` readout now uses **adaptive µm precision** — `ceil(-log10(px_µm))+1`
  decimals — so a single-pixel move is visible at any magnification (3 dp at 0.026 µm/px, where the old
  fixed 1 dp only changed every ~20–30 px). Also dropped the redundant layer-name/value part from the
  string (napari already renders the pixel value); the readout is now the clean `px (r,c) | µm (y,x)`.
- **A valid OME-TIFF pixel size was missed by the STACK loader.** An OME-TIFF whose baseline
  `XResolution` is zeroed (`0/1` → the reader's "division by zero") but whose OME-XML carries the real
  `PhysicalSize` (e.g. a ZEN `…OME TIFF-Export`) loaded UNCALIBRATED via Open Stack — the 2-D loader
  already recovered this from the OME-XML, but `read_stack_structure` only tried the (broken) TIFF tags.
  It now tries **OME-XML first, then TIFF tags** (via the existing `_ome_pixel_size_um`), so such files
  come in calibrated and the µm scale reaches the layer.
- **Streaming-CZI open now shows it is working.** The one-time BioFormats frame-index parse is opaque
  (no percentage), so the busy dialog now names the **frame count** and ticks an **elapsed-seconds
  counter** ("Indexing 15,766 frames… N s elapsed") instead of an unlabeled spinner. (The parse of a
  multi-GB file is inherently minutes-long; for a file that large, exporting to OME-TIFF from ZEN is
  faster.)

## [1.6.64] - 2026-07-15
### Changed — **File-I/O audit cleanup: all six re-audit items closed, incl. a full intensity-normalization standardization (dtype-max) audited safe for the scientific stack.**
- **Item 4 — stale `aicsimageio` in PKG-INFO.** The committed root `PKG-INFO` still declared
  `Requires-Dist: aicsimageio>=4.14.0` (+ `aicspylibczi`) — dependencies removed in the 1.6.0 BioIO
  migration. `PKG-INFO` is build output (regenerated from `pyproject.toml` into the sdist/wheel), so
  the committed copy was a stale artifact nothing reads; untracked it, removed it, and gitignored
  `PKG-INFO` + `*.egg-info/`. Fixed the one remaining "using AICSImageIO" README credit → BioIO.
- **Item 1 — generic loader is ImageSource-only.** Reader/dask-handle retention now works like the IMS
  loader: a per-load `ImageSource` attached to each lazy layer's `metadata['pycat_image_source']`,
  replacing the controller-scoped `self._stack_lazy_refs` list (removed). Also fixed a latent bug the
  migration surfaced — the T-Z branch retained nothing, so its lazy dask reader could be orphaned on
  GC; it now retains the reader like the z-stack branch. Verified by the reader-retention guard
  (pytest-qt, offscreen), which runs the real loader and confirms the reader survives GC when only
  layers are held.
- **Item 2 — the reader cache closes readers it drops.** Eviction (a 5th distinct file), the
  rewind-failure drop, and `clear_reader_cache()` now `close()` the discarded reader (best-effort) so a
  cleared viewer doesn't leak file handles (a Windows re-open/delete blocker). The cache and a live
  layer's `ImageSource` can hold the *same* reader, so `ImageSource.retain()` marks it and the cache
  skips closing any marked reader — never closing a handle a scrubbing layer still needs.
- **Item 3 — removed obsolete zarr scaffolding.** The generic loader no longer creates an unconditional
  `pycat_stack_*` temp dir (the synchronous full-file zarr transcode that fed it is long gone — every
  branch hands napari an already-lazy wrapper), and the two z-stack / T-Z layer messages that falsely
  claimed "(zarr-backed)" now say "(lazy, dask-backed)".
- **Item 5 — intensity normalization STANDARDISED on dtype-max [0, 1] across all image loading, audited
  safe for the scientific stack.** Every loader now yields the frame-independent **dtype-max** scale
  (`to_unit_float32` = `skimage.img_as_float32`, divide by the dtype ceiling) — never per-frame
  **min-max** (contrast-stretch each frame's own min..max), which `utils/intensity_semantics` classes
  DESTROYED. Sites converted:
  - **IMS single-frame**, **generic single-frame** (`read_plane` no longer forces `dtype=np.float32`),
    and **PIL 2-D fallback** → dtype-max via the native integer read;
  - **generic tifffile fallback** → dtype-max (eager arrays normalised; an already-[0,1] `_TiffPageStack`
    passes through);
  - **`load_into_viewer`**: its float branch **no longer min-maxes** — a float image is by contract
    already [0,1], so it passes through. This removes the audit's flagged hazard: min-max mislabeled the
    layer as pristine `raw`, so `partition_coefficient_local` (which refuses a *tagged* min-max layer)
    failed open on it.
  - **FRAP** (per-trace ratios) and **session-restore** (per-image min-max) left as intentional.
  - **Correction:** an earlier note here claimed the generic loader's *dask branches* produced raw
    counts — that was WRONG (verified): `_LazyArraySource` already applies `to_unit_float32` on every
    read (65535 → 1.0), so the dask branches were always dtype-max. The only real anomalies were the
    float-precast single-frame paths above.
  - **Audit (`docs/audits/claude_code_spec_fileio_cleanup_2026-07-15.md`):** no scientific module depends
    on min-max — the whole stack is written for dtype-max. Partition/enrichment/bimodal are scale-cancelling
    RATIOS; the saturation ceilings (`partition_coefficient_field`/`_local`, `qc_saturation`) assume
    `1.0`=true max and were *mis-firing* under min-max; N&B / molecular counting need a frame-independent
    scale; `partition_coefficient_local` already REFUSES min-max layers. Standardising on dtype-max is
    safe and corrects those latent defects. New `tests/test_load_into_viewer_scale.py`; existing
    `test_loaders_agree_on_scale.py` (the dtype-max contract) still passes.
- **Item 6 — the `microns_per_pixel_sq = 1` sentinel — documented + given a named "real-scale tag".** Kept
  the value `1` (a napari layer needs a positive, finite `layer.scale` to render/draw a scale bar; `1`
  maps it at 1 µm/px, where `0`/`NaN` would give a degenerate transform), but made its meaning explicit:
  a comment at the write site (`stack_load`) explains why `1` and that it is a PLACEHOLDER tagged by
  `pixel_size_from_metadata=False` + `pixel_size_confirmed=False`. Added
  `pixel_size.has_real_pixel_size()` / `pixel_size_is_placeholder()` — a single named "real vs
  placeholder" tag derived from those provenance flags (no redundant stored flag to drift), that
  **clears automatically when metadata supplies a scale or the user confirms one**. The field_status gate
  now delegates to it, so the gate, scale bar, and analysis accessor cannot disagree on "is this a real
  scale?". (Not switching the value to `np.nan`, which would fight the existing gate tests for little
  gain.)

## [1.6.63] - 2026-07-15
### Changed — **File-I/O decomposition #5 (final piece): `_open_stack_generic` becomes a slim orchestrator; the god-class breakup is complete.**
- **The core stack loader is no longer a 542-line god-method.** `_open_stack_generic` — every
  TIFF/OME-TIFF/CZI/z-stack load flows through it — is decomposed into pure, Qt/napari-free modules,
  behaviour-preserving throughout:
  - **5a** — the metadata-read + reader-selection head (structured reader → dims/scenes/pixel size,
    else a lazy tifffile-page fallback) → `readers/stack_metadata.py::read_stack_structure`. The Qt
    scene dialog and the `update_metadata`/`file_metadata` side effects stay in the controller
    (relocating them is behaviour-preserving — `update_metadata` never propagates, the dialog returns
    a selection rather than raising).
  - **5b** — the four per-branch lazy-wrapper builders (tifffile-fallback, time-series, z-stack, T-Z)
    → `readers/stack_layer_builders.py`, each returning `(wrapper, retain_refs, warnings)` without
    touching napari. The load-bearing zarr-3.2 shim (bioio's TIFF dask path is broken → tifffile-page
    wrapper, else a clear error) and multi-file OME handling move unchanged.
  - **5c** — their shared six-step tail (pin retained refs, surface warnings, **pin contrast from the
    first frame** so napari doesn't eager-read the whole stack, `add_image`, force per-frame display,
    announce) → one `_add_lazy_stack_layer` method.
  - **5d** — `_open_stack_generic` is now a slim orchestrator (**313 → 186 lines**): read head → loop
    scenes/channels → pick a builder → add layer → finalise.
- **Behaviour preserved exactly:** same wrappers, same retention (including the T-Z branch, which
  retains nothing beyond the wrapper napari holds), same contrast pinning, same messages. `_TiffPageStack`
  / `_LazyArraySource` are injected into the new modules to keep them cycle-free.
- **Tests:** byte-identity unit tests for `read_stack_structure` and all four builders (fake readers,
  no Qt), plus the zarr-3.2 error translation; the "no function truncated" guard records the
  deliberate shrink. Loader integration tests still pass. **Still needs a GUI confirm** — open a plain
  TIFF, OME-TIFF, multi-channel TIFF, z-stack, and CZI via `run-pycat` and scrub each (headless here).
- With this, the FileIOClass god-class decomposition roadmap (pieces #1–#5) is **complete** — see
  `docs/audits/fileio_godclass_roadmap_2026-07-15.md`.

## [1.6.62] - 2026-07-15
### Fixed — **Coordinate readout crashed on mouse-move on newer napari (`get_status` return-type mismatch).**
- `install_coordinate_readout`'s `get_status` wrapper always returned a plain **string**, but newer
  napari versions expect `get_status()` to return a **dict** (`{'coords': ..., 'value': ...}`) and do
  `status['coords']` in `_calc_status_from_cursor`. On those versions every mouse-move over an image
  raised `TypeError: string indices must be integers, not 'str'` (reported by Meet on the `pycat-16`
  env). The wrapper now calls the original `get_status` first, matches its return TYPE, and injects the
  PyCAT dual `px … | µm …` string into the dict's `coords` slot (preserving `value`) on newer napari,
  or returns the string on older napari — making the readout napari-version-agnostic.


## [1.6.61] - 2026-07-15
### Added — **Zeiss streaming CZI now opens (opt-in `[bioformats]`), via a direct BioFormats reader on a worker thread.**
- **The problem:** Zeiss fast-streaming/timelapse CZI (many-subblock, e.g. a 15,766-frame movie)
  cannot be decoded by *any* libCZI path — metadata reads fine but every pixel read raises
  `RuntimeError: not implemented`. A bake-off across 4 real CZIs (`docs/audits/czi_bakeoff_2026-07-15.md`)
  confirmed libCZI reads confocal **and** widefield-single-subblock CZI fine and fast (no JVM); only the
  **streaming layout** fails. So `.czi` now tries libCZI first and **only diverts to BioFormats when a
  pixel read actually fails** (routing rule (a)) — normal CZI keeps its fast, no-JVM path.
- **The reader** (`readers/czi_bioformats.py`, Qt/napari-free): pixels come from the **direct**
  BioFormats Java reader (`loci.formats.ImageReader.openBytes`, ~5 ms/plane) — *not* bioio's dask path,
  which measured **50–80 s/plane** here. A lazy (T,Y,X) wrapper reads one plane per slider move,
  normalises to [0,1] float32 (the analysis-stack contract), and `__array__` **refuses** an implicit
  full-stack read (the lazy-guard that stops napari materialising 15,766 frames for a thumbnail). The
  reader is retained for the layers' lifetime via `ImageSource`, exactly like the IMS path.
- **Non-blocking open:** the one-time ~33 s frame-index parse runs on a `QThread` worker behind a busy
  "Indexing CZI via BioFormats…" dialog so the UI stays responsive (with a synchronous fallback if the
  Qt/threading setup is unavailable). Per-plane reads are already scrubbable, so no frame cache is
  needed on this path.
- **Dependency reality (shifted since the original audit):** `bioio-bioformats 2.0.0` now requires
  `numpy>=2.1` (via `bffile`), which would break PyCAT's `numpy<2.1` pin (cellpose + numba) — so the
  `[bioformats]` extra pins **`bioio-bioformats<2.0`**. That line bundles Java BioFormats 6.7.0, which
  *cannot* read these CZIs, so PyCAT overrides the scyjava endpoint to **`formats-gpl:8.1.1`** and
  registers the **OME Maven repo** (for the `woolz` transitive jar) at JVM start.
- **Tests:** `tests/test_czi_bioformats_reader.py` — unit coverage of the lazy wrapper (shape, dtype,
  single-plane vs slice, `__array__` refusal) with no JVM, plus a skip-unless-installed integration
  test that opens the real 8.1 GB streaming CZI through BioFormats and asserts planes read non-zero.
- **Still needs a GUI confirm:** the worker-thread anti-freeze UX and scrubbing smoothness were
  verified headlessly at the reader level but not yet through `run-pycat` on a display. Opt-in and
  libCZI-first, so users without the extra (and all normal-CZI users) are unaffected.

## [1.6.60] - 2026-07-15
### Changed — **File-I/O decomposition #3: the lazy IMS reader classes + helpers move into a pure, Qt/napari-free `readers/ims_reader.py`.**
- **The lazy IMS wrappers no longer live in the god-class.** `_open_stack_ims` interleaves reader
  setup, metadata, ImageSource retention, a multi-position dialog, and napari `add_image` — but the
  three lazy adapter classes it constructs (`_ImsReaderTYX` / `_ImsReaderZYX` / `_ImsReaderTZYX`) and
  their helpers (`_suppress_ims_chunk_prints`, `_ims_indices`, `_ims_frame_2d`, `_ims_pixel_size_um`)
  are pure and Qt/napari-free. All seven move **verbatim** into the new `readers/ims_reader.py`
  (mirroring decomposition #1/#2). Because these are lazy WRAPPERS consumed during layer construction
  (not a separable read-then-construct flow), the controller keeps `_open_stack_ims` unchanged and now
  IMPORTS the classes plus `_suppress_ims_chunk_prints` / `_ims_pixel_size_um` back from the reader
  module.
- **Behaviour-preserving.** The wrappers carry subtle correctness moved intact: `__array__` calls the
  lazy-guard (`refuse_implicit_full_read`) to block accidental full-stack materialization, and every
  plane is normalised from the SOURCE dtype into `[0, 1]` float32 via `to_unit_float32` (through the
  shared `_ims_frame_2d`). `_ims_frame_2d` moved with the classes (its only callers) rather than being
  left behind — leaving it in `file_io.py` would have created an import cycle.
- **New byte-identity test** `tests/test_ims_reader_extraction.py`: a fake `reader` (no real IMS lib)
  wrapped in each `_ImsReader*`, asserting `wrapper[idx]` / `.shape` / `.dtype` / `len()` match a
  reimplemented-inline oracle across index forms (int, slice, list, full, y/x sub-slice) and all three
  axis orders, plus that `__array__` refuses. Existing `tests/test_ims_reader_retention.py` and
  `test_file_io.py` still pass. Also removed the now-unused `sys` / `io` / `contextlib` imports the
  moved helper left behind in `file_io.py`.

## [1.6.59] - 2026-07-15
### Changed — **File-I/O decomposition #4: the Save & Clear output-writing loop moves into a pure, Qt-free `writers.write_session_outputs`.**
- **The write path is now separable from the dialogs.** `save_and_clear_all` mixed three Qt dialogs,
  the batch recorder, viewer clearing, and the actual file writes in one 183-line method. The
  output-writing loop — the per-layer save (`writers._save_layer`), the per-dataframe atomic CSV
  write, the `_metadata.json` export, and the session-manifest write — is lifted **verbatim** into a
  new pure function `writers.write_session_outputs(central_manager, layers_by_name, selected_layers,
  selected_dataframes, dataframes, file_metadata, save_name, session_dir, source_path, stem)`. It takes
  already-decided inputs, touches no viewer/dialog/clearing, and returns
  `{'manifest_layers': [...], 'manifest_dfs': [...]}` for logging and tests. `save_and_clear_all` keeps
  ALL the orchestration (dialogs, session-folder creation, batch `record()`, clear/reset, the
  batch-export prompt) and now calls the writer with the final in-session `save_name` and the created
  session dir.
- **Behaviour-preserving.** The `warnings.catch_warnings()` skimage-warning suppression, the atomic CSV
  write (a truncated CSV is the worst failure — it opens, parses, and is silently short), the metadata
  JSON, and the manifest schema are unchanged — so **Load Session still restores exactly what Save &
  Clear writes** (the 1.6.52 session round-trip). Verified headlessly: a session folder produced by
  `write_session_outputs` is fully restored by `session_loader.load_session` (source image referenced
  not copied, dataframes restored with correct row counts, layer files re-added).
- **New pure test** `tests/test_writers_session_outputs.py` (no Qt, no viewer): fake layers + small
  DataFrames + a temp dir → asserts the layer files, `_<df>.csv` (correct row counts), `_metadata.json`,
  and the manifest all land, that selection is honoured, and that a missing session dir skips only the
  manifest. Existing `test_file_io.py` / `test_session_manifest.py` still pass.

## [1.6.58] - 2026-07-15
### Added — **GPU VPT bead detection validated on real CUDA; CuPy bridged to PyTorch's bundled CUDA runtime so the `gpu` extra works without a standalone toolkit.**
- **CuPy could not find its CUDA runtime.** `cupy-cuda11x` needs the CUDA 11.x libraries
  (`cudart`, `nvrtc` + its `nvrtc-builtins` companion, `cublas` ...) at first kernel launch. On a
  Windows/conda machine with no standalone CUDA toolkit, `import cupy` succeeded but the first real op
  died with `Could not find nvrtc64_112_0.dll`. The only consistent copy of those libraries on such a
  machine is the one **PyTorch** — already a hard PyCAT dependency, built against cu118 — ships in
  `torch/lib`. `gpu_utils._register_bundled_cuda_libs()` now locates that directory (via `find_spec`,
  without importing torch) and adds it to both `os.add_dll_directory` (for CuPy's `LoadLibraryEx`) and
  `PATH` (for nvrtc's internal load of `nvrtc-builtins`) before `import cupy`. Windows-only,
  best-effort, a no-op elsewhere or with a CPU-only torch. Result: `gpu_available()` is `True` out of
  the box on the Quadro P2200 (CuPy 13.6.0, CUDA runtime 11.8).
- **The GPU smoke test now proves the GPU is *functional*, not merely importable.** The old
  `cp.zeros((4,4))` check is only a memset and would pass off a stale kernel cache even when `nvrtc`
  is broken; it now forces an elementwise-kernel compile + reduction, so `GPU_AVAILABLE` reflects true
  capability.
- **Validated GPU ≡ CPU-parallel ≡ serial on real CUDA.** New `tests/test_vpt_gpu_equivalence.py`
  (skip-if-no-GPU, `integration`) asserts `blob_log_gpu` reproduces `skimage.blob_log` exactly, and
  that GPU and the ProcessPool worker path both match serial CPU blob sets across the fixture stack.
  Confirmed on the real 1080×1440 bead movie (first 40 frames, ~795 detections/frame): **0 mismatches**
  on all three tiers — so the `detect_beads_stack` tier selector never changes results.
- **Measured speedup** (Quadro P2200, warm, 40 dense frames): serial 1.70 fps → CPU-parallel 3.06 fps
  (1.80×) → **GPU 3.50 fps (2.06×)**. **Viscosity unchanged:** GPU detection is bit-identical to CPU,
  so the downstream link→MSD→Stokes-Einstein chain is untouched; the pipeline's η on the 1000-frame
  bead file is 8.52 Pa·s, matching the validated ~8.325 Pa·s baseline (v1.5.329) within ~2%. The GPU
  tier remains fully revertible via `PYCAT_FORCE_CPU=1` / `use_gpu=False`.

## [1.6.57] - 2026-07-15
### Fixed — **Restore VPT `_rebuild_track_layers` (lost from the tree), so loading a VPT session rebuilds its tracks again.**
- The `_rebuild_track_layers` method — which the session loader calls to reconstruct the VPT trajectory
  + pickable-points layers when a session with `vpt_tracks` is loaded — was absent from `vpt_ui.py` in
  the shipped tree (it was introduced alongside a load-CSV widget button that was dropped, and the whole
  change was skipped, taking the shared method with it). `ui_modules._open_session_loader` guards the
  call with `hasattr`, so loading a VPT session did not crash but SILENTLY skipped the track rebuild —
  defeating the session-restore feature (1.6.52/1.6.53). Restored `_rebuild_track_layers`; the load-CSV
  widget button is intentionally NOT restored (top-level Load Session is the single path). The 1.6.50
  brushing loop fix and line-trajectory highlight are unaffected.
## [1.6.56] - 2026-07-15
### Changed — **Smart channel naming: cleaned filenames, identity-first names, full-name tooltip, and the naming dialog skipped when confident.**
- **Filename cleaning.** A new `_clean_filename_token` strips MicroManager/OME cruft and acquisition
  parameters from the layer name: `3.30 hr_1_MMStack_Pos0.ome` → `3.30_hr` (keeps the user's timepoint,
  drops the `_MMStack_Pos0` MicroManager appends); `polyA 3 mgpmL - 1000 mM LiCl - 50mM HEPES pH
  7p5_3_MMStack_Pos0.ome` → `polyA` (the concentrations/buffer/pH belong in provenance, not the name); a
  useless export name like `Image 3-OME TIFF-Export-01.ome` cleans to nothing and falls through to the
  channel identity.
- **Identity-first naming.** `derive_layer_name` now prefers channel IDENTITY (a fluorophore/emission
  label from metadata OR a pixel-measured modality) over the filename, and combines them as
  sample-modality — e.g. `polyA-Brightfield`. A positional guess (`C0-Blue`) is still never treated as
  identity.
- **Full-filename tooltip.** The full original filename is stamped onto each loaded layer
  (`metadata['source_filename']` + a best-effort layer tooltip), so the rich acquisition name stays
  discoverable even though the visible layer name is the short cleaned identity. The full name is also
  already preserved in the provenance metadata JSON.
- **Dialog skipped when confident.** The multichannel Channel-Name-Assignment dialog is now skipped
  when every channel has a confident identity (metadata name/wavelength or pixel-measured modality) —
  it was only confirming names PyCAT was already sure of. It still appears when ≥1 channel is a bare
  positional guess, so the user can disambiguate. Guard tests in tests/test_channel_modality.py.

## [1.6.55] - 2026-07-15
### Fixed — **OME-TIFF pixel size recovered from OME-XML (no more spurious Set-Scale dialog / "division by zero").**
- An OME-TIFF whose baseline TIFF XResolution is zeroed (`0/1`) made the reader's physical_pixel_sizes
  raise "Could not parse tiff pixel size: division by zero" and fall back to 1.0 µm/px — popping the
  Set-Scale dialog even though the OME-XML carried the true `PhysicalSizeX` (e.g. 0.0264 µm/px). Added
  `_ome_pixel_size_um` (reads OME-XML PhysicalSizeX, honours the unit) and wired pixel-size recovery
  into the 2D image path: when update_metadata lands on the 1.0 sentinel, recover from OME-XML first
  (authoritative for OME-TIFF), then baseline TIFF tags. The 2D path previously had no recovery at all.
### Added — **Channel modality inferred from pixels when metadata is silent.**
- Camera-only acquisitions (MicroManager, exported OME-TIFF) carry no fluorophore/emission/name, so
  channel identification fell straight to a meaningless position guess ("C0-Blue"). New
  `channel_modality.classify_channel_from_pixels` measures a frame and names the modality —
  fluorescence vs transmitted, with a finer brightfield/DIC/phase split when the optical signature is
  clear (directional shadow-cast for DIC, edge halos for phase, plain absorption for brightfield),
  degrading honestly to the generic "transmitted" when uncertain (a wrong "DIC" is worse than an honest
  "transmitted"). Wired as a new tier in `identify_channel` between wavelength and the position
  fallback; metadata (fluorophore name / emission) still takes precedence over pixel inference. Guard
  test tests/test_channel_modality.py.

## [1.6.54] - 2026-07-15
### Changed — **File-I/O god-class decomposition #2: `open_2d_image` reader extracted to `readers/image_reader_2d.py`.**
- Lifted the pure file-path → channel-arrays logic out of `FileIOClass.open_2d_image` into a free
  function `read_2d_image_channels(file_path)` (mirrors the piece-#1 `mask_reader` extraction). It
  returns `(channels, channel_info, image, used_pil_fallback)`: the `(data, path, key)` channel tuples
  in the original load order (page-major then channel for multi-page, 1-based running key; else channel
  index), the per-channel identity from `extract_channel_info`, the reader object (so the controller
  still runs `update_metadata`/`extract_metadata`), and whether the NumPy-2.0 PIL fallback was taken.
  The controller keeps dialog, filePath bookkeeping, metadata-repository updates, the user-facing
  fallback warning, and napari-layer construction. Behaviour-preserving: a headless byte-identity test
  (tests/test_image_reader_2d_extraction.py) reimplements the original inline loop as the oracle and
  asserts the extracted reader matches it across page/channel shapes (1×1, 1×3, 2×1, 3×2, 4×4).
- Also fixed a latent post-loop reference: object_size/cell_diameter were derived from a loop-leaked
  `channel_data` local; now taken explicitly from the last loaded channel (`all_channels[-1][0]`),
  preserving the prior value exactly.
### Docs
- Added docs/audits/session_architecture_2026-07-15.md documenting the general session save/load
  infrastructure and the recipe for extending interop to other methods (FCS/RICS/etc.).

## [1.6.53] - 2026-07-15
### Added — **Load Session fallback recognises loose VPT (and other) dataframes from older saves.**
- The manifest-based session load (1.6.52) restores VPT, but files saved BEFORE that (loose
  *_vpt_tracks.csv etc. scattered in a folder with no manifest) went through the suffix-scan fallback,
  which did not know VPT suffixes. Added `_vpt_tracks`, `_vpt_aggregate_tracks`, `_vpt_aggregate_stats`,
  `_vpt_msd_df`, `_vpt_moduli_df`, and `_vpt_detections` to the scanner's dataframe rules (ordered so
  the more specific suffixes are not shadowed). Also fixed the batch-rule path to carry `df_key` for
  dataframe matches, so a restored table is stored under its true repository key (e.g. `vpt_tracks`) —
  which is what the VPT layer-rebuild hook looks for, so pointing Load Session at an old folder now
  rebuilds the trajectory layers from the loose CSV too. The stem picker in the load dialog still lets
  the user choose which image's files to load when a folder holds several.

## [1.6.52] - 2026-07-15
### Changed — **Save & Clear is now a real SESSION save: one folder, a manifest, the source image referenced (not copied), and Load Session restores the whole state (incl. VPT).**
- Save & Clear used to be a per-layer/per-dataframe EXPORT: it listed everything as checkboxes for the
  user to curate, wrote files with a flat prefix so they SCATTERED loose among the user's data, offered
  the SOURCE IMAGE as a save target (wasteful — it is already on disk and is the largest file), and the
  top-level Load Session could not reconstruct a VPT session from the result. Redesigned:
  - **Consolidated folder.** A save now creates one `session_<image>_<timestamp>/` folder and puts all
    artifacts inside it, instead of scattering them.
  - **Manifest.** `pycat_session.json` records the source-image PATH (a reference, not a copy), the
    acquisition state (pixel size, frame interval), and the layer/dataframe → file mapping.
  - **Source image referenced, never copied.** It is excluded from the save by default; the manifest
    points at it on disk.
  - **Smart defaults.** The save dialog now pre-ticks every DERIVED layer and ALL analysis dataframes,
    and unticks the source image and pure-interpolation upscales — so the user no longer has to curate
    what a session needs (they can still override any tick).
  - **Load Session restores the whole state.** `load_session` is now manifest-first: it opens the
    referenced source image through PyCAT's own loader (correct lazy type + scale), restores acquisition
    state and every recorded dataframe, and — when `vpt_tracks` is present — rebuilds the VPT trajectory
    layers via the shared `_rebuild_track_layers`, so a VPT session comes back clickable/brushable. The
    old suffix scan remains as a fallback for older folders.
  Guard test tests/test_session_manifest.py.

## [1.6.51] - 2026-07-15
### Added — **Load a saved VPT tracks session (iterate on the plots without re-running detection + linking).**
- Save & Clear already writes the `vpt_tracks` DataFrame as `*_vpt_tracks.csv`, but there was no way to
  load it back — so troubleshooting the MSD plots/table/brushing meant re-running detection and linking
  every time. A new "Load saved tracks (CSV)…" button in the VPT microrheology step reads that CSV back
  into the session: it validates the schema (needs track_id, frame, and y_um/x_um or y_um_raw/x_um_raw),
  stores it as `vpt_tracks`, and rebuilds the trajectory + pickable-points layers via a new shared
  `_rebuild_track_layers` (the same layer-build the linker uses, so a loaded session gets identical
  brushable layers). The user then clicks "Compute MSD & Viscosity" to regenerate the plots and per-track
  table from the loaded tracks. Non-tracks CSVs are rejected with a clear message. Guard test
  tests/test_vpt_load_tracks_session.py.

## [1.6.50] - 2026-07-15
### Fixed — **VPT brushing selection feedback loop ("jumps all over the place" on one click) + line trajectory highlight.**
- A single click on a track cascaded into the view rapidly cycling through many tracks. Cause: the
  linked-selection re-entrancy guard (`_sel_busy`) was cleared SYNCHRONOUSLY in `finally`, but
  propagating a selection makes programmatic changes (table.selectRow, viewer.dims.current_step,
  camera.center, points selection) that emit Qt/napari signals ASYNCHRONOUSLY — those fire after the
  guard was already cleared and re-enter `_select_track`, looping. Fixes: (1) an echo guard that ignores
  a re-selection of the already-selected track, and (2) the busy flag is now cleared on a zero-delay
  `QTimer.singleShot` (after the event queue drains) so queued re-entrant signals from the propagation
  are still suppressed. A single click now selects exactly one track.
- The image highlight is now a connected trajectory LINE (a Shapes 'path') that traces the picked track,
  plus a small ring at its start frame — instead of a column of filled orange circles that sat on top of
  and OBSCURED the trajectory already drawn in the Bead Trajectories layer. The line is bright but thin
  and slightly transparent, so it highlights without hiding the underlying track.

## [1.6.49] - 2026-07-15
### Changed — **MSD spaghetti plot draws a fidelity-targeted representative sample by default (supersedes 1.6.48's fixed cap).**
- A spaghetti plot exists to show the SPREAD of MSD curves (the 10–90% percentile band), not each
  individual line — past a point, extra lines only overplot into the same band and add no visual
  information. Measured on realistic MSD data, that band converges at a track count that is roughly
  CONSTANT (~100 tracks for ~95% fidelity) whether the dataset is 500 or 50 000 tracks. So instead of a
  fixed cap (or drawing everything), the MSD plot now draws the SMALLEST random sample whose band
  reproduces the full band to a target fidelity (default 95%), via the new
  `representative_track_sample`. The legend states it honestly, e.g. "showing 100 of 5000 (band fidelity
  ≈96%)". The full data is untouched — the ensemble mean and the D/α fit still use EVERY track; this
  governs only how many faint lines are drawn. Result: initial render and every subsequent pick/blit are
  fast and bounded regardless of dataset size (~100 artists, not thousands).
- OPT-OUT: a new "Draw every track" checkbox in the VPT plot options forces the literal full spaghetti
  (streamed in progressively on a Qt timer so the window stays live). `plot_msd_trajectories` also gains
  `render_mode` ('auto'|'all'), `target_fidelity`, and `max_tracks` parameters. The progressive draw-in
  machinery from 1.6.48 is retained as the mechanism for the 'all' path; the fidelity sample is the new
  default (so 1.6.48's raised fixed cap is superseded). Guard test tests/test_representative_track_sample.py.

## [1.6.48] - 2026-07-15
### Changed — **VPT MSD spaghetti plot draws progressively (responsive at once, fills in over time).**
- The standalone MSD plot (`plot_msd_trajectories`) drew every track synchronously and capped at 400
  because each track is a separate matplotlib artist and drawing thousands at once freezes the window.
  It now draws a first representative batch immediately (~150 tracks, so the plot is useful at once) and
  streams the rest in on a Qt timer (~150 tracks every 30 ms), yielding to the event loop between
  batches so the UI never blocks. Because progressive draw stays responsive, the visible cap is raised
  to 1500 (the quantitative result — ensemble mean + fit — still uses ALL tracks, as before). The
  track→line maps grow by reference as batches arrive, so streamed-in tracks become pickable/brushable
  as they appear, and the blit background (1.6.47) is re-captured once all batches are drawn so picking
  stays fast over the full set. Falls back to a synchronous bounded draw when a Qt timer isn't available
  (non-interactive/Agg). The consolidated 2×2 panel keeps its existing 400-cap synchronous draw for now;
  progressive draw-in there is a follow-on.

## [1.6.47] - 2026-07-15
### Changed — **VPT plot brushing is now fast (blitting instead of full-figure redraws).**
- Clicking an MSD track, or highlighting one from the image/table, was laggy because every selection
  triggered a full matplotlib `canvas.draw_idle()` — and a plot click actually triggered TWO (the pick
  handler's own redraw plus the dispatcher's). On a spaghetti plot with hundreds of lines that is tens
  of milliseconds per redraw. Both the standalone MSD plot (`plot_msd_trajectories`) and the
  consolidated 2×2 panel (`plot_vpt_panel`) now BLIT: the axes background is cached (and re-cached on
  draw/resize/zoom via a `draw_event` hook) and each selection restores that background and redraws
  ONLY the two changed lines (previously- and newly-highlighted). Headless benchmark on a 300-line
  plot: ~0.2 ms per highlight vs ~47 ms for a full redraw (~230×). The dispatcher's
  `_highlight_track_in_plot` shares the pick handler's highlight state and uses the same blit path, so
  image/table → plot highlighting is just as fast and never double-redraws. Re-picking the already-
  selected track is now a no-op redraw. Behaviour (which line is emphasised, the pick callback) is
  unchanged — only the render path is faster.

## [1.6.46] - 2026-07-15
### Fixed — **Results DataFrame windows are no longer modal (they blocked all interaction).**
- `show_dataframes_dialog` (the shared results-table window used across VPT and other analyses) called
  `dialog.exec_()`, which is MODAL: after a run, the results table froze every other interaction —
  scrubbing the movie, clicking a bead, panning the canvas — until the user hit OK. A results table is
  reference material, not a blocking decision, so it should never seize the UI. It is now shown
  NON-MODALLY: parented to the napari main window, kept alive by a module-level reference (a parentless
  QDialog under `.show()` would be garbage-collected instantly), and raised to the front with
  `raise_()`/`activateWindow()` (results windows had been appearing behind the main window). No caller
  relied on the old blocking return, so this is safe app-wide. Follow-ups still to come for the VPT
  plots (bring-to-front + click-brushing lag) and the post-100% results-materialization phase in the
  progress bars.

## [1.6.45] - 2026-07-15
### Fixed — **skimage 0.26 `remove_small_objects` deprecation (FutureWarning) unified across the codebase.**
- scikit-image 0.26 deprecated `min_size` in favour of `max_size`, and it is NOT a rename: `min_size=N`
  removed objects with area < N, while `max_size=N` removes area <= N. Seven call sites had drifted into
  a mix of positional `min_size`, keyword `min_size=`, and even `max_size=min_size` (which had silently
  shifted the threshold by one and flipped the comparison to <=). All are now routed through a single
  version-safe helper `general_utils.remove_small_objects_compat`, which uses `max_size = min_area - 1`
  on new skimage (reproducing the old "area < min_area" removal exactly) and falls back to `min_size` on
  old skimage — so the FutureWarning no longer fires and the threshold semantics are identical everywhere.
  Sites fixed: vpt_tools (×2, incl. the Mode-C host inference that surfaced the warning), brightfield_tools
  (its own correct wrapper now delegates), invitro_fluor_ui, timeseries_invitro_fluor_ui, batch_roi_tools
  (×2 — one of which had the latent off-by-one), segmentation_tools. Guard test
  tests/test_remove_small_objects_compat.py (asserts strict-less-than removal AND no FutureWarning).

## [1.6.44] - 2026-07-15
### Fixed — **Setting the pixel size now calibrates the image (µm readout appeared missing).**
- After the Set-Scale dialog (or the in-dock pixel-size gate) confirmed a value, PyCAT wrote
  `microns_per_pixel_sq` to the data repository but never set the napari image layer's `.scale`. The
  cursor readout showed pixels only (no µm), the scale bar stayed in pixels, and — critically — every
  layer-scale consumer, including VPT's auto linking distance, ran UNCALIBRATED even though the user
  had entered a scale. Root cause: `_align_layer_scales` can only PROPAGATE a scale from an
  already-scaled reference layer; with nothing scaled yet it finds no reference and does nothing, so
  the repo value never reached the layer. Fix: both set-scale paths (`prompt_pixel_size_on_load` and
  the `add_pixel_size_gate` in-dock gate) now route through `file_io._enable_auto_scale_bar()`, which
  reads the repo value and sets `layer.scale = sqrt(microns_per_pixel_sq)` — exactly as a real-metadata
  load does — so the µm readout/scale bar appear and downstream analysis is calibrated. Both paths
  also now set `pixel_size_confirmed=True`. Guard test tests/test_setscale_applies_to_layer.py.
  (Follow-on to the 1.6.42 corrupt-scale gate fix: 1.6.42 made the dialog APPEAR; this makes the
  value it collects actually take effect.)

## [1.6.43] - 2026-07-15
### Fixed — **VPT linking no longer allocates a terabyte-scale dense matrix (gap-closing crash).**
- `dynamic_spatial_tools._close_gaps_bayesian` built a full `(n_ends + n_starts)²` dense cost matrix.
  On a run that fragmented into ~286k tracks that is a 572k×572k float64 array = **2.39 TiB**, and
  linking crashed with a MemoryError. The matrix is ~99.9% INF — an end at frame `ef` can only close
  to a start in frames `(ef, ef+max_gap+1]` and within `max_displacement×gap` — so it is now built
  as a SPARSE candidate-edge list (a per-gap-frame KD-tree spatial query enumerates only the
  physically-plausible links) and solved with sparse bipartite matching, with a greedy
  cheapest-edge fallback. Linear in the number of real candidate links; a 50k-fragment case that
  would have needed ~80 GB now runs in seconds. Merges are unchanged (near fragments join, far ones
  stay separate). Guard test tests/test_gap_closing_sparse.py.
- NOTE: this fixes the CRASH, not the upstream cause. The run that triggered it had (a) a corrupt
  pixel size still active at detection (auto linking distance came out 377 µm — set the real scale
  in the panel BEFORE detecting, now that 1.6.42 makes the gate fire), and (b) ~381k detections /
  ~286k fragments, ~100× the validated ~800-beads/frame baseline — the known VPT detection-quality /
  classifier issue, still to be addressed. Good viscosity needs those fixed too; this just stops the
  hard crash so linking can complete.

## [1.6.42] - 2026-07-15
### Fixed — **A corrupt (physically-impossible) pixel size no longer satisfies the gate on STACK loads.**
- An ImageJ Substack export can write a 32-bit-overflow resolution tag (~2.3e-10 um/px, picometres).
  The 2D loader already screened this, but the STACK loaders (IMS + generic + tifffile fallback) all
  funnel through `stack_load._finalise_stack_load`, which committed the corrupt value to
  `microns_per_pixel_sq` with `pixel_size_from_metadata=True` — which SATISFIED the pixel-size gate:
  the warning printed but the Set-Scale dialog never appeared and the field stayed hidden, so every
  downstream length/area/diffusion result was computed from a fabricated scale. (`update_metadata`
  detected and rejected it correctly, then `_finalise_stack_load` overwrote that rejection with the
  corrupt value read separately from the baseline TIFF tags.) Fix: `_finalise_stack_load` now runs
  the same optics-based `is_physically_plausible` screen — implausible → fall back to the 1.0
  sentinel with `pixel_size_from_metadata=False` and `pixel_size_confirmed` cleared, which is exactly
  the state the gate fires on. Deduped so it does not warn twice when the 2D path already rejected
  the same tag. Every real lab scale (0.0264 / 0.067 / 0.108 um/px, and a genuine 1.0) passes; only
  garbage fails. Guard test tests/test_corrupt_pixel_size_gate.py.

## [1.6.41] - 2026-07-15
### Added — **Smarter metadata: frame-interval reconciliation, filename-based layer names, structured description parsing (three auto-loader patches).**
- **Frame interval prefers per-frame timestamps over declared values, and flags conflicts**
  (`metadata_extract.reconcile_frame_interval` + reworked `_extract_frame_interval_s`). A declared
  OME `TimeIncrement` / MicroManager `Interval_ms` is a *claim*; per-plane `DeltaT` timestamps are
  what the microscope actually did, so timestamps now win. When a declared value and the measured
  cadence disagree beyond tolerance (e.g. a 0.5 s claim over a real 0.1 s cadence — a 5x error in
  every diffusion coefficient), it is kept as `frame_interval_nominal_s`, `frame_interval_inconsistent`
  is set, and the user is warned once (reusing the frame_interval de-duped channel). Directly targets
  the VPT viscosity root-cause where a wrong nominal interval silently scaled every dynamics result.
- **Single-channel / mask layers are named from the FILENAME** (`file_io.derive_layer_name`) instead
  of a generic "Fluorescence Image" / "Mask Layer", so `..._DAPI.tif` and `..._GFP.tif` load as
  distinguishable names (complements the load-order fix in 1.6.39). Multi-channel loads from SEPARATE
  files also get filename-derived names; channels of one multichannel image keep the positional
  convention the two-channel workflow relies on.
- **Metadata description blobs are parsed into structured fields** (`metadata_extract.parse_description_blob`)
  — MicroManager summary JSON, ImageJ key=value, and OME-XML — so a wall of opaque text becomes
  queryable acquisition metadata (`raw['acquisition']`), and exposure is recovered when present. The
  `modality` field no longer gets a whole JSON/XML blob dumped into it.
  Pure functions, tested via tests/navigator/test_loader_fixes.py (6 tests, AST-extracted from the
  actual patched source; gated on PYCAT_SRC). Also folds in the IMS→ImageSource cleanup for
  timeseries_condensate_tools.py and the tifffile_zarr_shim (already in the repo).

## [1.6.40] - 2026-07-15
### Fixed — **Frame-interval "unknown" warning no longer fires with no image loaded.**
- The dynamics panels (advanced_analysis_ui, condensate_physics_ui) seed their frame-interval
  spinbox at BUILD time, before any file is opened. `has_time_axis()` returned True whenever the
  frame count `n_t` was unrecorded — which is the case in an empty session — so the scary "every
  time-dependent result is out by a factor of two" warning fired with nothing loaded. That trains
  the user to scroll past it, so the one that matters (on a real movie) gets ignored too. Fixed:
  `has_time_axis()` now returns False when NO image is loaded (detected via absence of both
  `file_metadata` and a recorded `n_t`), while still failing loud when an image IS loaded but its
  frame count is unknown, and staying silent on a still 2-D image. Guard test
  test_frame_interval_no_image.py. (Same bug class as the pixel-size sentinel: warn on a real
  problem, not on absence-of-state.)

## [1.6.39] - 2026-07-15
### Fixed — **Fluorescence-pipeline layer selection no longer depends on load order (finishes the tag migration for condensate seg).**
- When two fluorescence channels got the same generic name ("Fluorescence Image" / "Fluorescence
  Image (1)"), the ONLY thing distinguishing them was load order — so whichever loaded first drove
  condensate segmentation, silently feeding e.g. the DAPI channel instead of the condensate channel.
  Fixed by tagging channel IDENTITY at load and selecting by tags, not order:
  - Each loaded channel is now tagged `channel=<label>` and `spectral_bucket=<blue/green/...>` from
    metadata (`identify_channel`). New `spectral_bucket` tag key/vocabulary.
  - New **opt-in, persistent condensate-channel designation** (`utils/channel_designations.py`):
    the channel-assignment dialog offers "Which channel contains the condensates?" — set once, it's
    remembered per ACQUISITION LAYOUT (spectral-bucket signature, not file path) and recalled for
    future same-layout files. A designated channel is tagged `target=condensate`. Persisted to the
    per-user PyCAT config dir. Never guesses: nothing designated → returns None → the dropdown stays
    for the user (an empty dropdown beats a silent wrong pick).
  - New binding `invitro_fluor.input_image` (queries `role=image, target=condensate`); the invitro
    preprocess input dropdown is now resolver-bound, so it auto-selects the designated condensate
    channel regardless of load order.
  Core tests: test_channel_designations.py. GUI-verify: load a 2-channel fluor file, designate the
  condensate channel, confirm the invitro input dropdown selects it (not DAPI) on this and the next
  same-layout file.

### Changed — **Scientific Navigator engine vendored in (`pycat.navigator`).**
- The pure-Python question->intent->planner->gates->resolver engine (framework-agnostic, no napari)
  is vendored at `src/pycat/navigator/` with its data workbooks and tests (`tests/navigator/`). It is
  the design reference for the tag resolver (which already exists as `utils/tag_resolver.py`); this
  vendors the engine so the two can be connected incrementally. No app behaviour change from vendoring
  alone. Imports verified, 79-op catalog loads, resolver discriminates condensate-vs-DAPI correctly.

## [1.6.38] - 2026-07-15
### Fixed — **Saved PNG/JPG masks and images could not be reopened (missing bioio PNG reader).**
- The bioio backend reads each format via a separate plugin package. PyCAT declared the microscopy
  plugins (bioio-ome-tiff/tifffile/czi) but NOT `bioio-imageio`, which reads PNG/JPG/BMP. Since
  PyCAT's own save path writes PNGs (Cellpose masks, overlay exports), a fresh install could not
  reopen what it had saved — `UnsupportedFileFormatError` / "bioio has no reader installed for .png
  files". The aicsimageio backend handled PNG natively, so the bioio migration silently dropped it.
  Fix: (1) `bioio-imageio` is now a core dependency; (2) the missing-plugin error now names
  bioio-imageio for .png/.jpg/.jpeg/.bmp so the message tells the user exactly what to install.
  Existing installs unblock immediately with `pip install bioio-imageio` (keep numpy<2.1).

### Changed — **god-class decomposition, piece #1: extract the 2-D mask reader (file-I/O audit #21).**
- `FileIOClass.open_2d_mask`'s channel-reading loop moved to a pure free function
  `file_io/readers/mask_reader.py::read_2d_mask_channels(file_path)` (new `file_io/readers/` package).
  The controller now calls it and keeps only the dialog / bookkeeping / napari-layer construction.
  Behaviour is preserved exactly — byte-identical channel tuples in the same order across all
  page/channel shapes (guarded by tests/test_mask_reader_extraction.py, which reimplements the
  original loop as an oracle). First step of the FileIOClass breakup; see
  docs/audits/fileio_godclass_roadmap_2026-07-15.md for the full 5-piece sequence.

## [1.6.37] - 2026-07-15
### Fixed — **A genuine 1.0 um/px pixel size is no longer treated as "missing" (file-I/O audit #9).**
- The in-dock pixel-size gate and the µm-vs-px scale-bar check decided "does this image have a real
  scale?" from ``abs(mpp - 1.0) > 1e-9`` — using 1.0 um/px as a missing-value sentinel. Downsampled,
  low-magnification, derived, and synthetic images legitimately have a 1.0 um/px scale, and those had
  their calibration thrown away (endless re-prompting; a "px" scale bar on a calibrated image). The
  checks now decide from PROVENANCE — ``pixel_size_from_metadata`` (already set correctly by
  file_io/tagging.py) or a new ``pixel_size_confirmed`` flag set when the user explicitly enters a
  value — falling back to the old value guess only when no provenance is recorded. Behaviour for every
  scale != 1.0 is byte-identical to before (verified). Fixed in field_status.py (gate + prompt-skip)
  and napari_adapter.py (scale-bar). New core test tests/test_pixel_size_sentinel.py.

## [1.6.36] - 2026-07-15
### Added — **Tag vocabulary extended (audit A3/A4/A5) + QC verdict now attaches to the layer (A6).**
- **`representation` tag** (intensity_field / binary_mask / instance_labels / coordinates /
  trajectories / probability_map / measurement_table / model_fit / geometry) — separate from `role`,
  with a `representation_satisfies()` compatibility lattice (instance_labels satisfies a request for
  binary_mask, not vice versa). Lets a resolver ask for "instance labels, not a mask".
- **`state` tag** (raw → corrected → enhanced → segmented → refined → tracked → measured → fitted →
  validated), ORDERED via `state_rank()`/`STATE_ORDER` so a resolver can prefer the most-processed
  version (hand-refined labels over raw Cellpose output).
- **Four new lineage relations** — `registered_to`, `measured_from`, `tracks`, `reference_for` —
  enabling VPT/MSD plot<->layer brushing and colocalization linking.
- **QC writes its verdict onto the assessed layer:** the Quality Report now tags the layer with
  `quality_status` (pass/warn/fail, derived from the per-metric statuses) instead of leaving the
  judgement stranded in a disconnected result table. A downstream step can now resolve "a layer that
  passed QC". Added `quality_status` (controlled) and `analysis_ready_for` (open vocab) tag keys.
- All additive; existing tags unaffected. Verified by headless core tests
  (tests/test_pipeline_tag_source.py, now 12 assertions). See docs/audits/codebase_audit_2026-07-15.md
  (items A3–A6).

## [1.6.35] - 2026-07-15
### Fixed — **Pipeline-produced layer tags are no longer silently downgraded to low-confidence inferences.**
- `source='pipeline'` was written by the pipeline auto-tagger (tag_registry.py) but was absent from
  `VALID_SOURCES` in layer_tags.py, so every such tag was silently rewritten to `'inferred'` and its
  confidence dropped from ~0.95 to 0.6 — mislabelling every pipeline-produced tag as a low-confidence
  guess. A tag written by a known operation is definitional, not an inference. Added `'pipeline'` as a
  valid source with confidence 0.95 (distinct from `'derived'`: it means "a recorded PyCAT pipeline
  step made this"). Unrecognised sources still correctly downgrade to `'inferred'` — validation is not
  weakened. New headless core test tests/test_pipeline_tag_source.py (4 assertions). Found in the
  2026-07-15 codebase audit (docs/audits/codebase_audit_2026-07-15.md, item A1).

## [1.6.34] - 2026-07-15
### Removed — **Last `_ims_file_path` vestige eliminated; reader-retention migration fully closed.**
- `timeseries_condensate_tools.py` read the IMS source path from `file_io._ims_file_path`, but that
  attribute was removed when IMS reader retention moved to the layer-scoped ImageSource — so the
  lookup always fell through to `file_io.filePath`. The two cache-path sites now read `filePath`
  directly (identical value, no behaviour change), removing the dead reference. This closes the
  reader-retention arc: IMS and generic loaders both own readers via layer-scoped ImageSource, and
  no `_ims_*` retention attributes remain.

## [1.6.33] - 2026-07-15
### Changed — **Generic stack loader retention fully migrated to layer-scoped ImageSource.**
- Removed `self._stack_lazy_refs` from `_open_stack_generic` entirely. Reader/dask-array retention is
  now owned solely by an `ImageSource` attached to each layer's `metadata['pycat_image_source']`, so
  reader lifetime == layer lifetime — completing the migration begun in 1.6.32 (parallel step) and
  matching the IMS loader's ownership model. Replacing or closing a layer now releases its reader,
  instead of readers accumulating on a loader-singleton list.
- tests/test_generic_stack_reader_retention.py now asserts the ImageSource is attached and non-empty,
  then holds ONLY the layers (dropping the loader) and confirms frame reads still succeed — the guard
  that proves the layer-scoped retention is what keeps the handle open.

## [1.6.32] - 2026-07-15
### Changed — **Generic stack loader retains readers on a layer-scoped ImageSource (parallel step).**
- `_open_stack_generic` now retains its backing readers/dask arrays on an `ImageSource` attached to
  each layer's `metadata['pycat_image_source']`, so reader lifetime == layer lifetime — the same
  layer-scoped ownership the IMS loader already migrated to. This runs IN PARALLEL with the legacy
  `self._stack_lazy_refs` list (unchanged); a follow-up step removes the list once the layer-scoped
  path is confirmed on real files.
- Fixed a latent retention gap: the nested T+Z (TZYX) branch never retained its reader at all, so
  `image`/`dask_arr` could be garbage-collected while a lazy layer still pointed at them. It now
  retains like the TYX and ZYX branches.
- Guarded by tests/test_generic_stack_reader_retention.py.

## [1.6.31] - 2026-07-15
### Added (behind an opt-in extra) — **BioFormats-backed CZI reader; CZI loading temporarily disabled.**
- Built a BioFormats-backed reader path for Zeiss CZI files that libCZI (bioio-czi) cannot decode —
  notably fast streaming/timelapse acquisitions, where every libCZI read raised "not implemented"
  though ZEN opens them. `image_reader.py` routes `.czi` through `bioio-bioformats` when the opt-in
  extra is installed (`pip install pycat-napari[bioformats]`), and the reader reads the pixels
  correctly.
- **CZI loading is TEMPORARILY DISABLED in the UI.** The reader works, but opening a large CZI
  currently freezes the interface for several minutes because BioFormats' init + full-file indexing
  runs synchronously on the Qt main thread, and scrubbing then lags at dask block boundaries.
  Rather than ship a multi-minute frozen window, `.czi` opens now show a clear "temporarily
  unavailable — export to OME-TIFF from ZEN" notice. The reader code is left intact; a follow-up
  will run the open on a worker thread with progress + scrubbing prefetch and re-enable it.
- The `[bioformats]` extra pins `numpy<2.1` so installing it cannot pull numpy>=2.1 and break
  cellpose (needs <2.1) or numba (needs <2.5).
- Lazy readers that return their own wrapper type (BioFormats' LazyBioArray) are coerced to a real
  ndarray before downstream math, so every reader backend behaves identically.
- See docs/audits/czi_streaming_unreadable_2026-07-15.md for the full investigation and the
  re-enable plan.

## [1.6.30] - 2026-07-14
### Added — **The local cache now cleans itself up, visibly.**

Closes the open leak from the 1.6.29 handoff (§3.3): `_LOCAL_CACHE_FILES` was written and never
read, so a copied 1.5 GB acquisition stayed in `%TEMP%/pycat_local_cache` forever.

- **`file_io/local_cache.py`** (new) — startup cleanup for the copy-to-local cache. Runs **at
  launch**, before any file is opened this session, because that is the one moment the *previous*
  session's cached copies are provably idle (session-end never runs on a crash/kill, and racing GC
  during teardown is the worst time to delete a file a lazy reader may still hold).
- **Nothing deletes silently.** The startup dialog lists every cached copy **grouped by the source
  folder it came from**, with sizes and cache dates — the user sees *which acquisitions, from where*,
  never an opaque "N files, M GB." If Qt is unavailable (headless), it prints a notice and deletes
  **nothing**.
- **"Keep" protects data; it does not mute the message.** A folder-level *"Keep all from here"* and
  per-file checkboxes let the user exclude anything from the clear. Kept items are protected for
  **7 days** (`KEEP_DAYS`), then re-proposed on a later startup — a one-time Keep never pins
  gigabytes in TEMP forever; the expiry *is* the periodic reminder.
- **Manifest at copy time.** `dialogs._copy_to_local_with_progress` now records each copy's origin
  path into `_manifest.json` in the cache dir, because the cache is flat (basenames only) and the
  source path is otherwise lost — without it the warning could only show basenames, not origins.
- **Protected set persists** across sessions in a minimal per-user config file
  (`PyCAT/protected.json`), keyed by original source path so protection survives the flat cache.

*Rationale for the model (decided 2026-07-14): suppression protects the data, it does not silence
the deletion. Deletion is always reported; the only thing a user turns off is whether a specific
acquisition is cleared, and only for a week.*

## [1.6.29] - 2026-07-14
### Changed — **FileIOClass: 3,108 → 1,875. Forty percent is out.**

- **`file_io/viewer_load.py`** (93 lines) — `load_into_viewer`, what the 2-D loader, the mask loader
  and **both** stack loaders call once they have an array. It is a dependency of **five** other
  methods and depended on **two** — both extracted in the previous two releases. ***Taking it now
  unblocks the tier above it.***
- **`file_io/session.py`** grows `_auto_clear_before_load` and `clear_all_without_saving`, beside the
  `_clear_everything` they both call.

| module | lines |
|---|---|
| `writers.py` | 461 |
| `napari_adapter.py` | 272 |
| `session.py` | 218 |
| `dialogs.py` | 161 |
| `routing.py` | 116 |
| `tagging.py` | 114 |
| `stack_load.py` | 108 |
| `viewer_load.py` | 93 |

### **The guard caught a NameError that would have crashed the Clear button**

`clear_all_without_saving` calls `QMessageBox.warning(...)` and **has no import for it** — it relied
on `file_io.py`'s module-scope Qt import, which does not follow it out of the file.

`py_compile` passes. **The first time a user clicked Clear, it would have raised `NameError`.**
`test_no_undefined_names` caught it — *the third real `NameError` this guard has caught in this
refactor, and my own free-name check missed all three.*

*(And the fix nearly introduced a second bug: the file already imports `QMessageBox` from **qtpy**,
and I reached for **PyQt5**. Two Qt bindings in one module is a failure waiting for a machine that
has only one of them.)*

## [1.6.28] - 2026-07-14
### Changed — **FileIOClass: 3,108 → 2,016. A third of it is out.**

- **`file_io/tagging.py`** (114 lines) — `_tag_loaded_layer` and `_prompt_pixel_size_if_needed`.
  *What do we know about this layer, and how do we know it?* Role, dimensionality, calibration,
  provenance — plus the tags a PyCAT-saved TIFF carries **inside** it, which must override a fresh
  inference, because ***a user's answer outranks the loader's guess.***

  `_calibration_is_from_metadata` went with them: **calibration provenance is a fact about the
  layer**, and nothing else called it.

- **`file_io/stack_load.py`** (108 lines) — `_finalise_stack_load`, the last step of every stack
  load, whichever loader produced the pixels.

### **This is the cascade, and it is the point of the method**

`_finalise_stack_load` **could not have come out before this release.** It depended on *five* methods
of its 3,108-line host — and all five had been extracted by the previous moves:

    _enable_auto_scale_bar / _fit_view_to_layer / _add_diameter_annotation_layers  -> napari_adapter
    _tag_loaded_layer / _prompt_pixel_size_if_needed                               -> tagging

*Nothing about this move was clever. **The previous five simply removed every reason for it to
stay.*** Take what depends on nothing; the next layer then depends on nothing, and comes out free.

| module | lines |
|---|---|
| `writers.py` | 461 |
| `napari_adapter.py` | 272 |
| `dialogs.py` | 161 |
| `routing.py` | 116 |
| `tagging.py` | 114 |
| `stack_load.py` | 108 |
| `session.py` | 99 |

**No cycles.** The graph is a DAG: `stack_load → tagging → routing/writers → utils`.

### Found — dead state, and one that only LOOKS dead

`_open_stack_ims` stashes **nine `_ims_*` attributes** on `self`. **Six are written and never read.**
`_ims_zarr_refs` and `_ims_reader_array` are written repeatedly and read nowhere. Only
`_ims_file_path` is ever consumed — by `timeseries_condensate_tools`.

**They were not removed.** `_ims_zarr_refs` holds `(pos_reader, None, lazy_wrapper)` and reads like a
**keep-alive** — and while the wrapper does hold its own reader (so the list *appears* redundant),
***dead-looking state next to lazy loading is exactly how the 1.6 arc started.*** Deleting it on the
strength of a code read, in a refactor, is how a lazy stack quietly stops scrubbing.

*Recorded, not touched.*

## [1.6.27] - 2026-07-14
### Changed — **FileIOClass: 3,108 → 2,223.** Fifth extraction. 885 lines out.

- **`file_io/session.py`** (99 lines) — `_clear_everything`. **It is not doing I/O, it is UNDOING
  it**: removing layers, emptying the data repository, resetting the batch recorder, dropping the
  cached readers and their open file handles. Depends on `viewer` and `central_manager` and nothing
  else.
- **`_add_diameter_annotation_layers`** → `napari_adapter.py`. It takes **only `viewer`** and
  creates napari layers. It was never file I/O.

| module | lines |
|---|---|
| `writers.py` | 461 |
| `napari_adapter.py` | 272 |
| `dialogs.py` | 161 |
| `routing.py` | 116 |
| `session.py` | 99 |

### Caught by the guards — a NameError and a circular import

- **`test_no_undefined_names` caught a real `NameError`, for the second time in this refactor.**
  `_add_diameter_annotation_layers` reads `EAGER_DIAMETER_LAYERS`, a module-level constant that
  lived in `file_io.py`. *My own free-name check missed it — it looked at function CALLS, and this is
  a bare name load.*
- **And the obvious fix was a circular import.** Importing the constant back from `file_io` would
  make `napari_adapter` import its former host. ***And I could not test it:*** the sandbox has no
  Qt, so `import napari_adapter` dies on PyQt5 **before it ever reaches the cycle**.

  **A cycle I cannot test is a cycle I will not ship.** The constant moved to `napari_adapter`,
  where it belongs — it is a *display* toggle — and `file_io` re-exports it so the name still
  resolves there, one direction only.

## [1.6.26] - 2026-07-14
### Changed — **FileIOClass: 3,108 → 2,353.** Fourth extraction.

- **`file_io/dialogs.py`** (161 lines) — **asking the user is not reading the file.** Three dialogs
  that interrupt a load to ask a question only a human can answer: *copy this file locally?* (it is
  on slow storage) and *are these pages T or Z?* (an undeclared multipage TIFF says nothing about
  its own axis, and ***T and Z load identically***, so **nothing downstream can discover the answer
  for itself**).

- **Two of them kept their memory on `self` — and nothing ever read it.**
  `self._multipage_axis_choice` ("remember my answer this session") and `self._local_cache_files`
  were **scratch variables that happened to be spelled as attributes** of a 3,108-line class. They
  are now module-level, which is what they always were.

  *Side effect, and an improvement:* the T/Z answer is now genuinely **session**-scoped rather than
  instance-scoped — which is what "remember for the rest of this session" always meant.

### Found, and deliberately NOT fixed

`_local_cache_files` carries the comment *"Track for optional cleanup at session end."*

***There is no cleanup.*** Nothing reads the list. A user on a slow network share who accepts the
copy-to-local prompt for a 1.5 GB acquisition leaves **1.5 GB in `%TEMP%/pycat_local_cache`**, and
it stays there.

It is preserved exactly as it was, and the leak is now **written down in the module** rather than
implied by a comment nobody reads. *A cleanup that deletes a scientist's data is worse than a cache
that grows, and choosing the moment to purge it is a decision — not a refactor.*

### Caught while extracting

`QProgressDialog` and `QButtonGroup` were imported **locally**, and `QProgressDialog` inside a
`try/except` that sets it to `None` on failure — *a deliberate graceful-degradation path for a
headless or minimal Qt install.* Hoisting them to module scope would have turned a **soft dependency
into a hard one**, and the copy-to-local path would have stopped working entirely rather than
working without a progress bar. **`dialogs.py` has no module-level Qt import at all.**

## [1.6.25] - 2026-07-14
### Changed — **FileIOClass: 3,108 → 2,514.** Third extraction.

- **`file_io/routing.py`** (116 lines) — **four methods that never touched `self`.** They took
  `(self, file_path)` and used the `self` for *nothing at all*: *static functions wearing method
  clothes*, wedged into a 3,108-line class between the loaders, the dialogs and the lazy wrappers.

  They answer a question about a **path** — does this file carry real imaging metadata? did PyCAT
  write it? does it carry an embedded tag store? is it an undeclared multipage TIFF? **No viewer, no
  repository, no reader.**

  *The rule for this split: take what depends on nothing, first. Each move is then provably safe,
  and what is left behind is smaller and no more tangled than it was.*

### Not done — and the reason is the finding

**`_open_stack_generic` is 538 lines**, and the obvious next cut is the 121-line block at its top
that reads metadata, decides the pixel size and enumerates the scenes. On paper that is the audit's
`DatasetDescriptor`, and it touches only `self.central_manager`.

***It is not a metadata block.*** It **constructs a `_TiffPageStack`**, and on failure it falls
through to `tifffile.imread()` — **a full eager read of the entire file.**

That is the audit's finding 3 exactly — *"pixel transport and dimensional interpretation are
conflated"* — and **extracting it as it stands would move the tangle, not fix it.** Untangling it
means separating probe → reader selection → descriptor → pixel transport, which is a multi-session
arc, not the tail of a long one.

*Three extractions were taken because each was provably safe. The fourth is not, and stopping is
the whole discipline of this refactor.*

## [1.6.24] - 2026-07-14
### Changed — **FileIOClass: 3,108 lines → 2,620.** The split has started.

The audit's complaint is that `FileIOClass` does **eighteen things**: dialogs, storage diagnostics,
reader selection, metadata parsing, image-vs-mask classification, TIFF internals, lazy wrappers,
layer naming, channel colours, napari camera fitting, scale bars, data-repository updates, batch
recording, tag restoration, stack materialisation, save/export, session clearing.

*"This makes unit testing difficult and increases the chance that UI edits affect scientific reading
behaviour."*

**A 3,108-line class is not split in one move.** It is split in a sequence of moves each of which is
*provably safe* — and each of these two was chosen because it depends on **almost nothing** from its
host.

- **`file_io/napari_adapter.py`** (237 lines) — the camera, the scale bar, the layer-scale
  alignment. **None of it touches a file.** These four functions depend on `viewer` and
  `central_manager` and *nothing else* — no reader, no path, no handle.
- **`file_io/writers.py`** (461 lines) — `_save_layer` is **243 lines** and depended on exactly ONE
  thing from its host: `self.central_manager`. `_apply_saved_tags_to_layer` depended on **nothing at
  all**. `atomic_write` moved with them, because it *is* a writer concern — and leaving it behind
  would make `writers.py` import its former host, which is a cycle.

`FileIOClass` keeps a **3-line delegating stub** for each, so **every caller is untouched.**

### Fixed — the 1.0 µm/px sentinel, third and fourth copies

Both scale-bar functions decided **µm-versus-px** with `abs(px - 1.0) > 1e-9`. So an image with a
*genuine* 1.0 µm/px calibration — downsampled, low-magnification, synthetic — shows a **"px" bar**,
and the scale bar silently lies about what it is measuring.

*Same sentinel fixed in `_finalise_stack_load` (1.6.15) and `_tag_loaded_layer` (1.6.23).* They now
ask the repository **where the number came from** rather than guessing from its value.

### Caught by the guards, and worth recording

- **`test_no_undefined_names` caught a real bug**: `_fit_view_to_layer` uses `os`, imported at
  `file_io.py` module scope and **not** in the new module. It would have raised `NameError` at
  runtime — on the debug path, so it might have sat there for months.
- **The stub's signature was wrong.** I wrote `dataframes=None`; the real parameter is `tag_store`,
  **and the caller passes it as a keyword.** It compiled fine. It would have raised `TypeError` on
  every save.
- `test_no_FUNCTION_has_vanished` also caught the **seven helpers nested inside `_save_layer`** that
  moved with it. Recorded in `_DELIBERATE` with the reason — *the bodies did not shrink, they moved,
  and the guard's real question ("did the rationale survive somewhere?") is answered.*

## [1.6.23] - 2026-07-14
### Fixed — **open a movie and label it T; add a z-stack and label it Z; the movie is now labelled Z**

`stack_axis_label` lives in `data_repository` — **one dict shared by every layer.** And PyCAT can
add a file *without* clearing:

- **"Open Image (Add)"** — an explicit menu action, *"for side-by-side comparison"*;
- **multi-select in the file dialog** — which loads *"each subsequent file with `clear_first=False`"*.

So the second load **overwrites the first's axis label.** An MSD on the movie then reads `'Z'`, and
`warn_if_assumed_axis` warns about the wrong thing — *on the layer the user labelled correctly.*

***T and Z load identically.*** Nothing on screen reveals it, and every rate that comes out — a
diffusion coefficient, a coarsening rate, a recovery half-time — is a rate **per frame**. If those
frames are Z-slices, the rate is a fiction.

- **The axis is now tagged on the LAYER**, through the tag store that already existed
  (`_tag_loaded_layer`), with `source='user_set'` — because the user was *asked*. **That is an
  answer, not an inference, and it must not be silently overwritten by the next file's answer.**
- **`warn_if_assumed_axis(..., layer=...)`** reads the layer's own tag when given one. The argument
  is **optional**, so every existing call site keeps working — *the store is fixed without
  rewriting nine analysis handlers in the same change.*

### Fixed — the warning only ever fired ONCE, and never for the stack that needed it

The once-per-session flag was `dr['_axis_warned'] = True`, set on the **shared** repository. The
first stack spent the session's single warning, so ***the second stack never warned at all*** — and
with the label overwritten by that second load, **the second stack is exactly the one at risk.**

The flag is now a **set, keyed by layer**.

### Fixed — the 1.0 µm/px sentinel had a second copy

`_tag_loaded_layer` still decided calibration from `abs(mpp - 1.0) > 1e-9` — the same *"a real pixel
size is essentially never exactly 1.0"* reasoning fixed in `_finalise_stack_load` in 1.6.15.
***"Essentially never" is not never.*** It now reads `pixel_size_source`, like the other copy.

### Added
- `tests/test_axis_is_per_layer.py` — each layer warns about **its own** axis; the **second** stack
  warns at all; the same layer warns only once; a **declared** axis is never warned about; and a
  caller passing no layer still behaves exactly as before.

### Not done, and worth saying plainly
**The nine analysis handlers still call `warn_if_assumed_axis` without a layer**, so they fall back
to the shared repository — *the old behaviour.* The store is correct and per-layer; **the consumers
are not yet reading it.** Threading the analysed layer through each handler is a real change (the
layer differs per call site — `img_dd`, `mask_dd`, `stack_dd` — and the MSD path is computed from
*tracks*, not from a layer), and it belongs in its own release rather than bundled here.

## [1.6.22] - 2026-07-14
### Fixed — **the same pixels, through two loaders, were 65535x apart**

The 2-D loader called `dtype_conversion_func(data, 'float32')` → `skimage.img_as_float32`, which
**divides by the dtype max** and yields **[0, 1]**.

The lazy stack wrappers did a bare `arr.astype(np.float32)` — **raw counts, 0–65535.**

`_TiffPageStack` even *took the source dtype as a constructor argument and threw it away on the next
line* (`self.dtype = np.dtype('float32')`), so it **could not** have normalised correctly even if it
had wanted to.

- **[0, 1] is the contract, not a preference.** **17 toolbox functions declare it** in their
  docstrings — including `partition_coefficient_field` and `fit_bimodal_intensity`, which are
  *condensate measurements*, not helpers. `skimage.exposure.equalize_adapthist` **raises** on
  anything else (*"Images of type float must be between -1 and 1"*), and the preprocessing path
  depends on it. `img_as_uint`, the save converter, **raises** on it too.

- **All nine lazy wrappers now honour it**, via `stack_access.to_unit_float32()` — which **calls
  `img_as_float32`** rather than reimplementing the divide. *The obvious reimplementation
  (`arr.astype(np.float32) / np.iinfo(dt).max`) is wrong by one ULP on ~1% of pixels — measured, 9
  of 1024, by 6e-08. Not a scientific problem — **but a second implementation of the same convention
  is**, and "close enough" is how two conventions become three.*

- **The IMS cast was hiding in a helper.** `_ims_frame_2d` is module-level, and all three
  `_ImsReader*` wrappers read through it — so the cast feeding the 600-plane IMS file raw counts was
  **invisible to a scan of the wrapper classes.**

### Nothing broke, and that was luck

Every stack consumer happens to be immune — each for a *different* reason, all verified numerically:

| consumer | why it survived |
|---|---|
| **VPT** | normalises per-frame immediately before `blob_log` — coordinates **and bead classes identical**. *η ≈ 8.325 does not move.* |
| **optical density** | `-log10(I / I0)` is a **ratio**; the 65535 cancels. Verified identical. |
| **`analyse_frame_quality`** | normalises internally — Brenner/Tenengrad/variance **bit-identical**. |
| **time-series** | goes through `_read_source_frame`, normalising against a *fixed global range*. |

***That is luck, not design.*** The next function written against the documented contract would not
have been immune — and it would have failed **silently**, because a number that is 65535x wrong
still looks like a number.

*VPT's reported `amplitude` and `integrated_intensity` now come back in the [0, 1] convention like
every other intensity in PyCAT. `r_squared`, the coordinates, and the classifications are unchanged.*

### Added
- `tests/test_loaders_agree_on_scale.py` — the two loaders must be **bit-identical** on the same
  pixels; the divisor must be the **dtype** max, not the frame's (*"a growing focus must not appear
  to plateau"*); and no wrapper may hand out raw counts.
- *The wrapper check walks the **AST**, not the text.* A grep-based version reported `_TiffPageStack`
  as broken **after it was fixed** — it had matched the comment *quoting* the old code.
  ***"A guard that cannot tell code from prose will eventually flag its own explanation."***

## [1.6.21] - 2026-07-14
### Fixed — **the pixel-size gate was installed inside `except Exception: pass`. In ten panels.**

    try:
        self._pixel_gate_refresh = add_pixel_size_gate(layout, ...)
    except Exception:
        pass

***If that throws, the gate never installs.*** `_pixel_gate_refresh` is never set, the reset hook
finds `None` and does nothing, and **the panel builds perfectly.** The image then loads at
1.0 µm/px, and **every length, every area, every diffusion coefficient is silently in pixels while
the column header says microns.**

Nothing is printed. Nothing looks wrong.

***That is the pixel-size gate regression that cost a night to find. It was unfindable by
construction.***

- **30 scientific gates were failing silently.** Not the 122 broad excepts the audit counted —
  most of those wrap a colormap or a tooltip, where a failure costs nothing and should stay quiet.
  **A scientific gate is different: it is the thing standing between a number and a wrong number,
  and when it fails to install the analysis does not stop — it proceeds without the check.**

  | gate | silent in |
  |---|---|
  | `add_pixel_size_gate` | **10 panels** — is this image calibrated at all? |
  | `warn_if_assumed_axis` | **9 panels** — is this stack really TIME, or is it Z? |
  | `sync_spinbox_from_metadata` | **8 panels** — every dynamics result scales with the frame interval |
  | `prompt_pixel_size_on_load` | the modal prompt — *the last line of defence* |
  | `_pixel_gate_refresh._reset_gate` | without it the gate **never reappears for the second file of a session** |

- **`debug_log` was the disguise.** It prints **only under `PYCAT_DEBUG=1`** — which is exactly
  right for an optional colormap and exactly wrong for the gate that decides whether your microns
  are microns. *A grep for `except: pass` finds seven of these. It misses the other twenty-three.*
- New `report_guarantee_failure()` in `utils/general_utils.py`: prints **unconditionally** and
  raises a napari warning, because a message only in the terminal is a message most users of a GUI
  never see. **Control flow is unchanged** — the panel still builds. *This does not make the gate
  work. It makes its absence impossible to miss.*

### Added
- `tests/test_no_silent_scientific_gates.py` — walks the AST for any `try` that installs a
  guarantee and ends in a silent swallow. **`debug_log` counts as silent, deliberately.**
- *And it tests the metric against the bug:* one test proves `report_guarantee_failure` is loud
  **without** `PYCAT_DEBUG`, another proves `debug_log` stays quiet — because a reporter quietly
  wired to `debug_log` would let every gate fail invisibly while the guard reported green.

### Note
- `_add_advanced_analysis` sat 2 lines over the absolute function-length ceiling after this work.
  **The ceiling was not raised.** Its own message argues the case: *"Nobody reads a function this
  long. They skim it, and a `try/except: pass` around the one thing that mattered goes unnoticed —
  which is exactly what happened to the pixel-size gate."*

## [1.6.20] - 2026-07-14
### Fixed — PyCAT shouted about a missing frame interval at a user who had opened a still image

**Second half of Meet Raval's 1.6.17 report.** Opening a plain 2-D DAPI/GFP pair printed, twice:

    WARNING: Frame interval unknown (advanced_analysis_ui) — this file's metadata does not
    carry one. **Every time-dependent result depends on it.** A diffusion coefficient, an MSD
    exponent, a recovery half-time and a coarsening rate all scale with it directly...

*Every word of that is true — **of a movie**.*

**His file was a single 2-D image.** No time axis. No diffusion coefficient, no recovery half-time,
no coarsening rate. ***There is nothing for a frame interval to be wrong about.*** The panels seed
their frame-interval spinbox at **build** time, so the warning fired simply because the panel
existed.

***A warning that fires where it cannot apply is how real warnings get trained away.*** The next one
— on an actual time series, where a wrong interval **is** a factor-of-two error in every dynamics
result — is the one that gets scrolled past.

- **The warning is not softened, only aimed.** A movie with no frame interval still warns, exactly
  as loudly. Pinned by a test, because suppressing noise must not suppress the signal.
- `record_time_axis()` / `has_time_axis()` in `utils/frame_interval.py`. `n_t` is now recorded on
  every load: `1` for `open_2d_image`, and the real count in `_finalise_stack_load` — **the single
  funnel both stack loaders already pass through, which already took `n_t` as a parameter.**
- **Recorded OUTSIDE the metadata `try`.** Every loader wraps `extract_metadata` in `try/except`;
  recording `n_t` inside it would mean a metadata failure leaves the value unset — and ***the
  previous file's frame count still sitting in the repository.*** **A stale time axis is worse than
  an absent one: it is confidently wrong.**
- **Unknown counts as a movie.** An older session, or a loader not yet taught to set `n_t`, warns.
  *A spurious warning on a still image is noise; a missing one on a movie is a wrong number in a
  paper.* **Fail toward the loud side.**
- The helpers live in `utils/`, not `file_io` — `file_io` imports from `pycat.utils` (a cycle) and
  **imports PyQt5 at module scope**, which would have dragged Qt into a pure-utils module and put
  it out of reach of headless tests. *The `try/except` around the import would have swallowed both
  failures silently and left the warning firing exactly as before.*

### Added
- `tests/test_no_time_axis_no_warning.py` — the still image stays quiet, **the movie still shouts**,
  an unknown axis fails loud, and a stale frame count from the previous file is overwritten.

## [1.6.19] - 2026-07-14
### Fixed — opening a plain TIFF printed a parse error naming the user's own file

**Reported by Meet Raval against 1.6.17.** Opening `In Cell 8-DAPI.tif`:

    Attempted file (In Cell 8-DAPI.tif) load with reader:
    <class 'bioio_ome_tiff.reader.Reader'> failed with error:
    bioio-ome-tiff does not support the image ... Failed to parse XML for the
    provided file. Error: syntax error: line 1, column 0

**And then the file opened fine.** `P=1 T=1 C=1 Z=1 → 2D`.

`BioImage(path)` with no `reader=` runs BioIO's **plugin auto-selection**: it tries
`bioio-ome-tiff` first, that plugin goes looking for OME-XML, **a plain microscope TIFF has none**,
and it raises. BioIO catches it, prints the attempt, and falls through to `bioio-tifffile`, which
works.

*The error is BioIO's, it is not fatal, and the load succeeds.* **But the user cannot know that.**
It reads exactly like a corrupt file and it names their image. ***A scientist seeing that goes
looking at their microscope, or at their data.*** Same cost as the
`'_TIFF' object has no attribute 'RESUNIT'` message this codebase already carries a startup check
for — a message that sends people to debug the wrong thing entirely.

- **TIFF is now pinned to `bioio-tifffile`.** It wraps `tifffile`, which reads **plain and OME TIFF
  alike** — and **PyCAT does not take TIFF pixels from BioIO at all**: `read_tiff_plane` seeks the
  page directly, precisely because `bioio-ome-tiff` reads through `tif.aszarr()`, broken on
  zarr 3.2. *BioIO only supplies dimensions, scenes, channel names and pixel size for TIFF, and
  `bioio-tifffile` supplies all of them. The OME plugin was never on the pixel path — it was only
  ever a noisy first guess.*
- A caller passing its own `reader=` **wins**; a genuinely missing plugin falls back to BioIO's
  probe rather than raising (*a noisy load beats no load*).
- The pin is added **at construction**, after the cache key is built from the caller's kwargs — so
  it does not look like a caller option and switch the reader cache off for every TIFF. **Pinned by
  a test**, because that would have been a quiet 3–4x slowdown on every drag-and-drop.

### Added
- `tests/test_tiff_reader_selection.py` — the pin, the non-TIFF passthrough, the caller override,
  the missing-plugin fallback, and the cache interaction.

## [1.6.18] - 2026-07-14
### Fixed — **the test runner reported PASS for 23 tests it never ran**

- **A test whose entire body is `assert False` reported PASS.** Verified with a canary.

  `tools/run_core_tests.py` dispatches on a test's signature: *no parameters → run it; parameters →
  treat them as `parametrize` cases.* **But a test asking for a FIXTURE has parameters and no
  `parametrize` decorator.** It fell into the parametrize branch, which built its cases from
  `SCIENTIFIC_MODULES` — a name most test files do not define. `ns.get(...)` returned `[]`, so
  `combinations` was **empty**, **the loop body never executed**, `n_fail` stayed `0`, and the
  runner printed `PASS`. `total` was not incremented either, so they were invisible in the count.

- **And these were not peripheral tests:**

  | file | never running |
  |---|---|
  | `test_reader_cache` | **4 of 5** |
  | `test_one_plane_reads_one_plane` | **3 of 5** — *the perf guard the whole 1.6 arc turns on* |
  | `test_file_io` | **5 of 5** — the entire file |

  The guards protecting the BioIO migration were reporting green **without executing a single
  line**, for an entire release arc. ***This is the same failure as a metric that cannot catch its
  own bug, one level up: the thing that checks the checks was not being checked.***

  *When the runner was fixed and they finally ran, **they all passed** — so no product bug was
  hiding behind this. **But nobody knew that, and that is the whole problem.*** Suite count went
  **486 → 503**.

- **The runner now injects fixtures.** `tmp_path`, `monkeypatch` (with real undo), `capsys`, and
  **custom `@pytest.fixture` functions defined in the test module** — `counting_reader`,
  `a_fifty_frame_tiff`, `slow_storage`, the fixtures that *build the conditions* the 1.6 guards
  test. Nested fixtures (a fixture taking `tmp_path`) and generator fixtures resolve too.

- **And where it cannot build one, it FAILS** — naming the fixture. ***A test that cannot be run is
  a FAILURE, not a pass.*** Saying PASS there is what hid this for a release arc.

### Added
- `tests/test_the_runner_actually_runs.py` — pins the dispatch decision (`plan_test`) that was
  lying. An unbuildable fixture must plan as **unrunnable**, not as "parametrize with zero cases".

  *It does **not** spawn the runner in a subprocess.* That guard works — and costs **148 seconds**,
  doubling a suite that already takes that long, because the inner run executes the whole suite
  too. **A guard nobody can afford to run is a guard that gets deleted.** So the decision was
  extracted into a pure function and is tested **in-process**.

## [1.6.17] - 2026-07-14
### Fixed — the TIFF page map was a hardcoded guess, and it was wrong on the canonical layout

- **`read_tiff_plane` asserted one interleaving rule for every TIFF:**

      index = ((t * n_z) + z) * n_channels + c

  It is wrong on **TCYX** — the order it was written for. Asked for `(t=0, c=1)` of a 3x2 file it
  returned **`(t=1, c=0)`**; asked for `(t=1, c=1)` it **declined**. Measured across ten layouts,
  it was wrong or declining on **every one** except plain single-channel.

  *Two failure modes, and the quiet one is the dangerous one.* A **decline** falls back to BioIO —
  which for TIFF is the broken `aszarr()` path — so it fails loudly. A **wrong page** puts a real
  image on screen from the wrong timepoint, and ***nothing about it looks broken.***

- **The cause: a "page" is not a "plane."** A plain 3x2 TCYX file reports `len(series.pages) == 3`
  with `pages[0].shape == (2, 4, 4)` — **one page holds both channels.** An OME-TIFF of the same
  data reports `len(pages) == 10` with `pages[0].shape == (8, 8)` — *one plane per page.* Neither
  can be hardcoded. The map is now derived from **`series.axes`** — which tifffile reports
  faithfully — split into the axes that select the *page* and the axes sliced *within* it.

- **Why it survived:** for a single-channel time series (Micro-Manager bead stacks, the only TIFF
  PyCAT is routinely pointed at) a page **is** a frame, the formula collapses to `page = t`, and the
  old rule was **correct by coincidence.** ***A rule that is right on the only data anyone tested is
  indistinguishable from a rule that is right.***

  **The VPT path is byte-identical.** Verified frame-by-frame across a 20-frame TYX stack — the
  eta ~ 8.325 baseline does not move.

- **Two bugs in the fix itself, both caught by existing guards, both worth recording:**
  - tifffile reads an *undeclared* 3-frame TIFF as **`SYX`** — three *samples* (an RGB image), not
    three timepoints. The first fix passed `S` through whole, so `t=0` returned the entire
    `(3, 8, 8)` stack and `t=99` returned **an array**. *The old code declined.*
    `test_an_OUT_OF_RANGE_page_DECLINES` caught it.
  - **A plain 2-D image is not a stack.** Slicing `plane[(0,)]` on a `(32, 48)` image returns
    **row 0** — real pixels, right dtype, utterly wrong.
    `test_a_plane_is_BIT_IDENTICAL_to_a_full_read` caught it.

  *Both guards were right and both versions of my fix were wrong. This is what the guards are for.*

### Added
- `tests/test_tiff_page_map.py` — every plane of **ten** axis layouts (TCYX, CTYX, TZYX, ZTYX,
  ZCYX, CZYX, TZCYX, TCZYX, ZYX, CYX) carries its own `(t,c,z)` as its pixel value, so a wrong page
  is **detectable, not merely plausible**. Plus RGB (`S` must pass through whole), OME-TIFF, the
  undeclared multipage case, and a frame-by-frame pin on the single-channel VPT path.
- The new tests deliberately **do not use pytest's `tmp_path`**: the hand-rolled sandbox runner has
  no fixture injection, so a test that needs one is *silently skipped locally and only ever runs in
  CI* — which is precisely how a guard stops guarding.

## [1.6.16] - 2026-07-14
### Fixed — the voxel was assumed isotropic, and it almost never is

- **Every 3-D volume PyCAT reported was computed with `z_step_um = 1.0`.** The parameter had a
  default of 1.0 and **nothing ever passed a value.** The UI's Z-step spinbox was hardcoded to
  1.0, and its tooltip said *"Z-step size in µm from the acquisition metadata"* — **it was not.**

  A typical confocal pairs a **0.108 µm** lateral pixel with a **0.300 µm** Z step, because the
  axial PSF is three times worse and nobody oversamples a dimension they cannot resolve. So:

      voxel_volume_um3 = (microns_per_pixel ** 2) * z_step_um

  was out by **3.33×** on every condensate volume, every volume fraction and every 3-D density.
  The same number feeds the marching-cubes `spacing=` and the 3-D centroids, so **surface areas and
  axial distances were wrong in the same breath** — and on a true sphere it drags *sphericity* from
  0.87 to 0.74. ***All of it reported as numbers that look entirely normal.***

  **The true value was already in the repository.** `metadata_extract` reads
  `physical_pixel_sizes.Z` and stores it as `z_step_um`, where it was **displayed in the metadata
  panel and read by nothing.** *This is the same disease as the 1.0 µm/px sentinel fixed in 1.6.15:
  the honest number exists, nobody consults it, and the fallback is a plausible-looking lie.*

- New `z_step_um()` / `z_step_um_or_default()` in `utils/pixel_size.py`, alongside the lateral
  accessors and holding the same contract: **NaN when it does not know**, a warning when it
  proceeds anyway, and the same physical-plausibility bounds (a corrupt tag is not a measurement).
- The Z-step spinbox is now **seeded from the file**, and when the file carries no Z step the
  tooltip says so — *in the place the guess is made*, because a user who does not know the value
  was assumed cannot know the volume is unreliable.
- `condensate_metrics_3d` / `cell_metrics_3d` now default `z_step_um` to **NaN, not 1.0**. NaN
  propagates: a NaN volume is visibly wrong, a 3.3× overestimate is not.

### Fixed — the storage probe was the stall it warned about

- **It read a flat 8 MB before the reader opened the file.** 8 MB is not a bounded cost — it is
  8 MB *at whatever speed the medium runs*, and the medium is the unknown being measured. On the
  2 MB/s network share the probe exists to detect, **the probe alone spent ~3.5 seconds** before
  anything reached the screen, to establish a fact the first fraction of a second had proved.

  ***A diagnostic that reproduces the symptom it diagnoses is not free.***

  The read is now bounded by **time** (`PROBE_DEADLINE_S = 0.75`), not bytes. Measured: **3500 ms →
  815 ms**, with the slow-storage verdict unchanged and fast disks unaffected (~2 ms).

- **The chunk had to shrink for the deadline to bite.** The first attempt bounded a 3500 ms probe
  to *1500 ms* against a 750 ms deadline — because at 1 MB per chunk, a 2 MB/s share spends
  **500 ms inside a single `read()`**, so a deadline checked between chunks cannot be honoured to
  better than half a second. **A bound that a coarse chunk overshoots by 2× is a coincidence, not a
  bound.** Chunk is now 128 KB (~64 ms at 2 MB/s).

- **The verdict is cached per directory.** Storage speed does not vary between two files in one
  folder, but the probe ran **once per file** — so opening a 200-image acquisition folder paid for
  it 200 times. Only the *throughput* is cached; **size and message stay per-file**, because
  caching the whole verdict would report whichever file was probed first — exactly the class of
  quietly-wrong output this work keeps digging out.

### Added
- `tests/test_anisotropic_voxel.py` — pins the 3.33× error, the NaN propagation, and that the Z
  step is *read from the file* rather than guessed.
- `tests/test_storage_probe_deadline.py` — a throttled 2 MB/s file proves the probe stays inside
  its deadline **and still reaches the right verdict**. *(Fixture-based; runs under CI pytest, not
  under the hand-rolled sandbox runner.)*

## [1.6.15] - 2026-07-14
### Fixed — the `__array__` guard was too narrow, for the second time

- **`transpose()` was the same lie as `__array__`, and the guard never looked at it.** Three lazy
  wrappers — `_ZarrTYX`, `_TiffPageStack` (`file_io.py`) and `_ZarrStack`
  (`timeseries_condensate_tools.py`) — implemented:

      def transpose(self, *axes):
          return self.__getitem__(0)[np.newaxis]

  **Whatever axes you asked for, you got frame 0**, shaped `(1, Y, X)`, and nothing about the
  result looked wrong. This is precisely the bug `__array__` was fixed for in 1.6.3. It survived
  that fix because **the guard checked `__array__` and nothing else.**

  All three are **deleted**. Absence is the honest implementation and it is *proven*: the three
  `_ImsReader*` wrappers have never defined `transpose`, and one of them carries the 600-plane IMS
  file that scrubs at 0.5% of scene. napari duck-types for the method. A caller that genuinely
  needs a transposed stack must ask for the read: `materialize_stack(layer).transpose(...)`.

- **Two more wrappers were materialising the whole stack, outside the guard's scope.** The guard
  globbed `file_io/*.py`. `toolbox/timeseries_condensate_tools.py::_ZarrStack.__array__` called
  `np.asarray(self._z)` — *the entire zarr, off disk, from any thumbnail or contrast estimate* —
  and was invisible to it. It now refuses, via the shared `lazy_guard`.

  ***A guard whose scope is the file where the bug was found will certify every instance somewhere
  else.*** The guard now walks the **whole package**, and decides what a lazy wrapper is by
  **structure** — anything with `shape` and `__getitem__` — rather than by which directory it is in.

- **`_KeyframeMaskStack` is exempt, named, and justified.** It is not file-backed: a dict of ~30
  Cellpose keyframe masks already in RAM, whose `__array__` expands them RAM→RAM and returns the
  **full advertised array**. It does not answer for a stack it never read. *Honest null result
  worth recording:* all three of its call sites `np.asarray()` it immediately after construction,
  so the 20× memory saving in its docstring is **discarded one line later, every time.** The
  wrapper is a lazy view nobody consumes lazily. Documented rather than "fixed" — teaching the
  consumers to index it is a change to the time-series segmentation pipeline, not to file I/O.

### Fixed — `1.0 µm/px` was doing two jobs

- **A real pixel size and a missing-value sentinel.** `_finalise_stack_load` decided calibration
  provenance from the *value*: `abs(microns_per_pixel - 1.0) > 1e-9`, on the reasoning that *"a
  real microscope pixel size is essentially never exactly 1.0."* ***"Essentially never" is not
  never*** — a downsampled, low-magnification, derived or synthetic image can have a genuine
  1.0 µm/px, and PyCAT would discard that calibration and prompt for a scale it had been given.

  **The honest answer already existed.** `metadata_extract` records `pixel_size_source`
  (`'ims_extents'` / `'tiff_tags'` / `'ome_metadata'` / `None`), populates it on every load — and
  it was **only ever displayed.** The new `_calibration_is_from_metadata()` reads it. Falls back to
  the old value-based guess only when no source was recorded, because a wrong `True` suppresses the
  gate whereas a wrong `False` merely asks a question that can be answered.

  *(The 2-D path in `data_modules` was already correct — it sets the flag from whether the tag was
  PRESENT. Only the stack path guessed.)*

### Added — writes are atomic

- **A half-written file that opens is worse than no file at all.** Every save wrote straight to the
  destination. Interrupt it — a crash, a full disk, a user closing the app during a 600-frame
  export — and what is left is a **truncated TIFF that opens perfectly**, showing however many
  frames got written, with nothing anywhere saying so. *A file that fails to open announces itself.
  A file that opens short does not.*

  New `atomic_write()` context manager: write to a sibling temp, `os.replace()` on success (atomic
  on Windows and POSIX). All eight write sites go through it — image stacks, label stacks, 2-D
  images, RGB, shapes, `.npz`, **and the results CSV**, where a short table is the most dangerous
  output of all. An interrupted save now leaves the destination *untouched*, so a failed save no
  longer destroys the previous good one.

- **The obvious implementation corrupts the output, and it was caught by checking the bytes.**
  Naming the temp `result.png.partial` makes `skimage.io.imsave` — which picks its format **from
  the extension** — fall back to **TIFF**, write `II*\x00`, and return cleanly. Renamed to `.png`,
  the file on disk is *a TIFF called `.png`*: it round-trips through skimage (which sniffs content
  on read) so it looks fine from inside PyCAT, while every other tool and every collaborator gets a
  mislabelled file. The suffix goes **before** the extension — `result.partial-a3f1.png`.

### Changed — names that lie about which layer owns the behaviour

- `extract_aicsimage_metadata` → **`extract_reader_metadata`**;
  `extract_channel_info_from_aicsimage` → **`extract_channel_info`**. Both were named after a
  library that is no longer used, which obscures which behaviour belongs to the shared
  structured-reader interface and which is genuinely backend-specific — *the exact question the
  whole 1.6 migration turned on.* All call sites updated.
- `test_no_stale_reader_names` checked **only** `use_aicsimage`, so both survived it in plain sight.
  It now matches the family. **`import aicsimageio` is deliberately still allowed** — it is the
  conflict probe, and detecting the package by name is the entire point of that line.

## [1.6.14] - 2026-07-13
### Documentation — the whole 1.6 arc, written to be reviewed and revisited

**`docs/audits/bioio_migration_2026-07-13.md`** — the full record of 1.6.0 → 1.6.13. **Where a
decision was wrong, it says so and says what it cost:**

- the probe passed 31/31 on **correctness** and **could not see the freeze** — *by construction*
- 1.6.3 fixed **three of nine** ``__array__`` methods, and **the guard only looked at the file
  containing those three**
- the item-8 fix **never once ran** — a malformed call inside a ``try/except`` is **invisible**
- **three badly-posed thresholds** in the performance metric, and what each taught
- the harness measured **one of four lazy paths** — *and not the one that bit*
- **the reader cache introduced a correctness bug** while fixing something else

### README — upgrading from 1.5.x
**An in-place upgrade will not work, and the failure is disguised.** ``aicsimageio`` and BioIO
**cannot coexist**; installing 1.6 into a 1.5 environment leaves both broken, and the error
(``'_TIFF' object has no attribute 'RESUNIT'``) ***sends a scientist looking at their microscope.***

The new section says **why**, gives the five-minute fresh-environment path, **keeps the old
environment as a fallback**, covers the GPU torch replacement, and explains what a healthy startup
looks like.

### Roadmap — what was deliberately NOT closed
- **Z and T+Z TIFF still go through BioIO's broken zarr path.** ``_TiffPageStack`` handles TYX
  natively; it does not handle Z or T+Z. **A z-stack TIFF would fail today.**
- **Item 4 was cached, not restructured.** *Cheap, not clean — and worth saying so.*
- **The harness cannot build a wrapper for multichannel IMS.** The files work in PyCAT; **the
  diagnostic script is what is broken** — *recorded rather than dismissed, because it means the
  multichannel IMS wrapper is not being exercised.*
- **The lazy wrappers live behind a Qt import**, so they cannot be measured headlessly.

### DEV_NOTES — the parts that will bite someone
The two disguised failures (``RESUNIT``; ``zarr 3.2.1 < 3 is not supported``, ***which is a lie***),
why the conda lockfiles were deleted and must not return, and **why a user can break PyCAT by
installing a napari plugin and there is no way to stop them** — *which is a design position, not a
missing feature.*

**469/469 core tests passing.**

## [1.6.13] - 2026-07-13
# The harness tested ONE of FOUR lazy paths — and not the one that bit

Gable: *"since we have ims loading lazily why are we not trying to time them in the same way? the
issues with lazy not being so lazy were there for everything."*

**He is right, and skipping ``.ims`` was lazy reasoning on my part.**

| format | load path | lazy wrapper | tested? |
|---|---|---|---|
| **.ims** | ``_open_stack_ims`` | ``_ImsReaderTYX/ZYX/TZYX`` | **NO — skipped entirely** |
| **.tif** | ``_open_stack_generic`` | ``_TiffPageStack`` | **NO** |
| **.czi** | ``_open_stack_generic`` | ``_LazyArraySource`` | **NO** |
| any | ``read_plane`` | *(no wrapper)* | **YES** — *only this* |

> ***And the bug Gable actually FELT — the IMS scrubbing lag — lived in
> ``_ImsReaderTYX.__array__``, which none of that ever touched.***

**``read_plane`` is the CLASSIFICATION path.** What the user does is:

```
drag the slider    ->  wrapper[t]         ->  __getitem__  ->  ONE frame
napari thumbnail   ->  np.asarray(layer)  ->  __array__    ->  ALL frames   <- THE BUG
```

*I measured the path I had fixed, not the path that broke.*

## Now measured where the finger is
The harness builds **the wrapper PyCAT would build, per format** — including ``.ims``, which is no
longer skipped — and **indexes one frame, as scrubbing does.**

Two new tests, both verified:

| | |
|---|---|
| **scrubbing one frame** | **0.4%** of the way from *one frame* to *the whole stack* |
| **``np.asarray(wrapper)``** | **REFUSES** — *the ``__array__`` guard holds* |

**Both halves of the same bug.** ``wrapper[t]`` being cheap is not enough: *anything* that treats
the layer as an array calls ``__array__`` — a thumbnail, a contrast estimate, a plugin, a layer-list
refresh. **That is what made the IMS stack lag**, and PyCAT's own source had said so for months.

**469/469 core tests passing** *(2 skip in a headless sandbox without Qt; both verified by direct
execution).*

## [1.6.12] - 2026-07-13
# Three badly-posed thresholds, and what each one taught

The harness crashed on ``KeyError: 'n/a'`` — I added a verdict and forgot the label for it. *A
reporting bug that destroys the report is worse than an ugly label*; it now uses ``.get``.

**But the crash was the small problem.** Above it, three OME-TIFFs had started reporting **``high``**
— and *they were fine.*

## The three thresholds, in order
**1. "amplification < 3x"** — ***vacuous.*** For a ``T=1 Z=1 C=1`` file, **one plane IS the whole
scene**, so the ratio is **1.0× by construction.** **30 of Gable's 32 files could not fail**, and
the green result hid the two that were talking.

**2. "fraction of the scene < 15%"** — flagged a **57 KB plane in a 600-frame file at 3.7×**, which
is ~212 KB against a **34 MB scene: 0.6% of it.** *Fixed overhead — page tags, OME-XML, a numpy
temporary — reads as 3.7× on a small plane and 0.01× on a big one.*

**3. …and that same fix then flagged the 3-CHANNEL files.** **Reading one plane out of a 3-plane
scene necessarily allocates 33% of it.**

> ***A correct loader MUST hit the floor, and I was calling the floor a failure.***

## The framing that doesn't need an invented constant
The **file** sets both bounds:

```
floor   = one plane        (what a CORRECT loader allocates)
ceiling = the whole scene  (what a BROKEN one allocates)
```

**Where peak sits between them** is scale-free, plane-count-free, and needs no threshold I made up.

| | position |
|---|---|
| **lazy** | **0.1%** |
| **eager** | **100.1%** |

## And the metric only has POWER when the bounds are far apart
**With N planes, a correct read allocates 1/N of the scene.** At N=1 that is **100%**. At N=3 it is
**33%** — *and the entire window between correct and broken is 3×, which fixed overhead can cross on
its own.*

**Below 10 planes the metric cannot tell right from wrong, and it now says so** rather than grading
anyway. ***Pretending otherwise is exactly how the last two thresholds went wrong.***

Verified against Gable's real files:

| file | verdict |
|---|---|
| Image 3/5/7-OME *(3 planes)* | **n/a** — no room to tell |
| In Cell *(1 plane)* | **n/a** |
| **post_1_0.5_1** *(600 planes, 3.7×)* | **ok** — *0.6% of the scene* |
| *a broken loader (600 planes)* | **SCENE** — *100%* |

**467/467 core tests passing.**

## [1.6.11] - 2026-07-13
# The harness was badly posed — and it was hiding two real bugs

Gable, on the first run: *"you sure you didn't just make another badly posed test?"* **He was
right.**

## 30 of 32 files COULD NOT FAIL
**For a ``T=1 Z=1 C=1`` file, one plane IS the whole scene.** Amplification is **1.0× by
construction** — it cannot be anything else, whatever the loader does.

The run reported **1.01× on 30 of 32 files** and looked like a clean bill of health.

> ***A test that cannot fail is worse than no test.*** It produces a green number that means
> nothing — and it **hid the two files that were actually saying something.**

Single-plane files are now reported as **NOT APPLICABLE**, explicitly, and do not count toward the
pass. *If every file is single-plane, the script says **"NOTHING WAS ACTUALLY TESTED"** rather than
"all clear".*

## Bug 1: the TIFF/zarr fix had a DEAD END in it
```
polyA...MMStack_Pos0.ome.tif → read_plane failed: ValueError: zarr 3.2
```

``read_tiff_plane`` **declined** on a multi-file OME set, on the reasoning that *"the caller falls
back to BioIO."*

> ***But for TIFF, BioIO is exactly what is broken.***

``bioio-tifffile`` reads pixels through ``tif.aszarr()``, which is incompatible with zarr 3.2. **The
decline handed the file to a path that cannot work.** *A fallback that does not exist is not a
fallback — it is a dead end with a comment explaining why it is safe.*

**tifffile resolves the multi-file set itself.** ``series`` walks the OME-XML, finds the companions,
and exposes **one page list spanning them** — *and handles absent companions too.* **Multi-file now
comes for free**, and the pixels are still bit-identical.

## Bug 2: the 3.7× flag was a FALSE ALARM — my threshold was wrong
``post_1_0.5_1.tif`` was flagged at **3.7×**. ***It was fine.***

That file is **600 frames of 177×162 — a 57 KB plane.** 3.7× is ~212 KB, against a **34 MB scene**:
**0.6% of it.**

**A fixed overhead — page tags for 600 pages, the OME-XML, a numpy temporary — reads as 3.7× on a
57 KB plane and 0.01× on a 4 MB one.** And the data said exactly that: every 1024×1024 file reported
1.01×, the 512×512 files 1.11×, the small one 3.7×. ***Consistent with constant overhead. Not with a
scene read.***

> **The real question is not "how many planes?" — it is "what fraction of the SCENE?"** A loader
> that reads the whole scene allocates **~100% of it.**

| | of scene | verdict |
|---|---|---|
| **lazy** | **2.1%** | one plane read one plane |
| **eager** | **100.1%** | *caught, and the number says what happened* |

Both the harness and the unit test now measure **fraction of the scene.**

**467/467 core tests passing.**

## [1.6.10] - 2026-07-13
# The check that was missing: **does reading one plane read one plane?**

The migration was validated on **shape, dtype, dimension order, pixel size, scenes, and a SHA-256 of
the pixels** across 38 files. **31 identical, 0 different.**

> ***That was true, and it was insufficient.***

It measured **correctness** and **nothing about cost.** A loader that read the **entire scene** to
fetch **one plane** passed every check while **freezing the application** — the freeze was
***invisible to it by construction***, and it took Gable saying *"it lags"* to find it.

**The audit named exactly this**, and it was right:

> *"…did not compare bytes read, peak memory, time to first plane, **whether a one-plane request
> materializes a scene.** That is exactly why the migration passed correctness testing while
> regressing user experience."*

## The metric — and the one I tried first that DOESN'T WORK
**Bytes-read is unreliable, and I found that out by testing it rather than assuming it.**

The OS **page cache** serves a warm file from RAM, and ``tifffile`` **memory-maps** — so pixels
arrive by *page fault*, not by ``read()``. On a 13 MB, 50-frame TIFF:

```
lazy  read:  0 bytes
EAGER read:  0 bytes      <- the WHOLE SCENE, and the counter saw NOTHING
```

***A metric that reports zero for the bug it exists to catch is worse than no metric.*** ``psutil``'s
I/O counters are blind to it too.

**Peak ALLOCATION is immune to all of it.** An eager read **must allocate the whole scene**, and
*the cache cannot hide an allocation*:

| | peak | **amplification** |
|---|---|---|
| **lazy** | **0.28 MB** | **1.1×** |
| **eager** | **13.12 MB** | **50.1×** — *exactly the 50 frames* |

## Why a RATIO
```
amplification = peak_allocated ÷ bytes_in_one_plane
```

***It needs no baseline and no "before" run.*** ~1× means it read the plane. ~N× means it read the
whole N-frame scene to hand you one. **One number, one file, unambiguous.**

## Shipped
- **``tests/test_one_plane_reads_one_plane.py``** — the regression cannot come back silently.
  **Includes a test that the metric CATCHES an eager read** *(a guard that cannot fail is not a
  guard — and mine couldn't, until I checked)*. **47× margin.**
- **``pycat_perf.py``** — the standalone harness, for real files on real disks.

**466/466 core tests passing.**

## [1.6.9] - 2026-07-13
### Items 5b and 9 — the last of the audit

## 5b. `_ZarrTYX_generic` was named after the wrong thing
**It is not zarr-specific.** It received **zarr arrays, numpy arrays, and BioIO dask arrays** — and
the name told every reader it could rely on zarr semantics it does not have.

***Worse: the TZYX branch transcoded the entire file into a temporary zarr before showing anything,
purely so it would have a zarr to wrap.*** **The dask array was already lazy.** *(Removed in
1.6.4.)*

**Deleted.** ``_LazyArraySource`` wraps whatever it is given, and was verified to behave
**identically** on every indexing pattern napari uses on a (T, Y, X) layer — ``stack[t]``,
``stack[t, :, :]``, ``stack[t, y0:y1, :]``, ``stack[t0:t1]``.

*(``multidim_io``'s ``_ZarrTZYX_generic`` is kept and documented as currently unused. **It is
correctly named** — it wraps a genuine zarr array, which ``_LazyArraySource`` does not claim to be —
and a future "optimize for browsing" background cache would want exactly it.)*

## 9. Comments that name the wrong library are a trap, not a cosmetic issue
**9 references to ``use_aicsimage``.** That flag ***never meant "is it aicsimageio?"*** — it meant
*"did the structured reader give us dimensions, scenes and channel metadata, or are we falling back
to reading raw pages?"*

> **The name described the IMPLEMENTATION rather than the QUESTION**, and it went stale the moment
> BioIO replaced aicsimageio.

**Renamed to ``reader_has_structure``** — which stays true whichever library is underneath.

**And 15 comments describing current behaviour in terms of ``AICSImage``**: *"opened via
AICSImage"*, *"AICSImage's dask reader"*, *"skip the AICSImage path"*. **None of it is true any
more**, and a reader who trusts it looks in the wrong place.

*It is the sort of thing waved through as "just comments" — right up until someone debugs against
them.*

**One mention remains, and it is the correct one:** the note recording that the dead ``AICSImage``
import was removed. **That is history, and history is worth keeping.**

**Guarded**: ``test_no_stale_reader_names`` fails the build if either comes back.

## The drop-detector earned its keep again
Deleting ``_ZarrTYX_generic`` took its ``__getitem__(self, idx)`` with it, and the guard flagged the
lost parameter. **It is a rename, not a removal** — ``_LazyArraySource.__getitem__(self, index)`` —
*and the guard was right to make me say so.*

**466/466 core tests passing.**

## [1.6.8] - 2026-07-13
# Item 8 was NOT fixed — and the reader cache had introduced a correctness bug

## Item 8: reported done, and never once ran
The 1.6.4 "fix" called::

    arr = _TiffPageStack(file_path)

**One argument, where five are required.** It raised ``TypeError``, was caught by the surrounding
``except Exception``, and ***fell straight through to the eager ``tifffile.imread`` anyway.***

> **It compiled. The tests were green. The eager read happened every single time.**

***A malformed call inside a ``try/except`` is invisible*** — it does not crash, it does not fail a
test, it just silently takes the path it was written to avoid.

**Now fixed properly** (the shape comes from tifffile, which is the whole point — *BioIO's metadata
is what failed*), and **guarded**: the arity of every ``_TiffPageStack(...)`` call is checked
statically.

## The reader cache was handing out readers in someone else's state
**1.6.6's cache made all four opens construct one reader.** But **BioIO readers are STATEFUL** —
``set_scene()`` **mutates** them.

Two call sites now hold the **same object.** So a site that moves to scene 2 left the next caller's
reader ***parked on scene 2*** — reading **the wrong field of view**, with ***nothing about the
image looking broken.*** On a multi-position CZI that is a **silently wrong analysis.**

> ***I introduced this in 1.6.6, while fixing something else.*** It is exactly the class of quiet
> wrongness this project keeps finding.

A cached reader is now **rewound to its first scene** before it is handed out. If it cannot be
rewound, it is **dropped and rebuilt** rather than shared in an unknown state. **Guarded.**

## Item 4, honestly
``_open_image_auto_single`` read ``.dims`` and ``.scenes``, decided 2-D versus stack — **and threw
all of it away.** ``open_stack`` and ``open_2d_image`` then opened the file and worked it out again.

**The cache made the re-*opening* free. It did not make the re-*inspection* free** — on a CZI,
``.dims`` walks the subblock directory. *The cache hid the design flaw rather than fixing it.*

``ImageStructure`` now carries the answer: **inspected once, at the top of the chain.**

**But I will not oversell this.** ``_open_stack_generic`` **needs the reader object** to build its
dask arrays, so it calls ``open_image`` regardless — *and the cache already made that free.* What
the structure genuinely buys:

- **the 2-D-versus-stack decision is made in ONE place** (``ImageStructure.is_stack``), not by an
  inline expression each caller writes for itself. *Two callers can no longer get different
  answers.*
- **``parsed=False`` is explicit.** A file the reader could not understand no longer *looks like* a
  2-D single-channel image — *a guess wearing the costume of a fact.*

**463/463 core tests passing.**

## [1.6.7] - 2026-07-13
# TIFF pixels no longer go through BioIO — because BioIO's TIFF path is broken on zarr 3.2

## The error message is a LIE
```
ValueError: zarr 3.2.1 < 3 is not supported
```

***3.2.1 is not less than 3.***

The real failure is **one frame up, where nobody looks**:

```
ImportError: cannot import name 'RegularChunkGrid' from 'zarr.core.chunk_grids'
```

**zarr 3.2 renamed that class.** ``tifffile``'s zarr store catches **any** ImportError out of its
zarr-3 module and **blames the version** — so a user chasing this goes looking for an old zarr that
is not there.

## And PyCAT's own lazy-read fix is what walked into it
| | |
|---|---|
| **before 1.6.3** | ``get_image_data()`` → bioio-tifffile decodes the page **directly** → *tifffile's zarr store is never touched* |
| **after 1.6.3** | ``get_image_dask_data()`` → ``tif.aszarr()`` → **boom** |

> ***The old path worked precisely because it was doing the wrong thing.***

## The fix: keep TIFF off BioIO's pixel path entirely
``tifffile`` seeks a single page **directly** — **no zarr, no dask graph, no OME plane-map walk.**
It is **faster than the BioIO path even when BioIO works**, which is why ``_TiffPageStack`` was
written in the first place.

**BioIO still supplies dimensions, scenes, channel names and pixel size for TIFF.** It is good at
that, and *none of it goes near the zarr store.*

*(Pinning zarr would re-pin the stack the 1.6.0 migration existed to free — and it would be a guess:
nobody knows which zarr 3.x ``tifffile 2026.6.1`` was built against.)*

**This is what the audit recommended, in a section I read and did not act on:** *"Keep TIFF on the
native page-reader path… it does not need to be the pixel transport path when ``_TiffPageStack`` can
directly seek individual pages."*

## Verified against real data
- **bit-identical** to a full ``tifffile`` read on Gable's MMStack substack
- **24 interleaved planes** (4T × 2Z × 3C, each uniquely valued) — **all correct**
- an **out-of-range page DECLINES** rather than returning page 0

> ***A wrong page is worse than a slow one.*** It would show the **wrong channel** or the **wrong
> timepoint** — and *nothing about the image would look broken.*

Where the mapping cannot be established with confidence — a **multi-file OME set**, an unexpected
page count — the reader **declines** and the caller falls back to BioIO.

## And two bugs found while doing it
**The TYX branch built the dask array BEFORE checking for TIFF** — so it crashed *before*
``_TiffPageStack`` was ever reached, and the array was then used only for its ``dtype``. **For a
TIFF it is now never built.**

**The 1.6.4 ``tifffile.imread`` fallback was broken.** It called ``_TiffPageStack(file_path)`` with
**one argument** where six are required — it would have raised ``TypeError``, been caught, and
fallen through to the eager read anyway. ***Item 8 was never actually fixed.***

## Z and T+Z TIFF stacks
``_TiffPageStack`` handles **TYX** natively. It does **not** handle Z or T+Z, so those still go
through BioIO — and **if that fails they now say what actually happened**, rather than letting
tifffile's misleading message reach the user.

**462/462 core tests passing.**

## [1.6.6] - 2026-07-13
### One reader per file, not four — the last item from the audit
A single drag-and-drop **constructed the reader three to four times** before one pixel reached the
screen:

```
_add_image_or_mask_single   -> open_image()    "is this an image or a mask?"
_open_image_auto_single     -> open_image()    "is this 2D or a stack?"
  -> _open_stack_generic    -> open_image()
     OR open_2d_image       -> open_image() x3  (probe, fallback check, reload)
```

**Reader construction is not free.** Depending on the plugin it parses OME-XML, walks the TIFF
series, reads the **CZI subblock directory**, and enumerates scenes — ***every time.*** For a large
CZI that is the same expensive directory walk, **four times over**, before anything is displayed.

**The cache lives in the seam**, so **all seven call sites benefit and none had to change.**

**Measured: 4 opens → 1 construction.**

### Two things it has to get right
**A stale reader is worse than a slow one.** It holds an open handle to a file that may have changed
on disk, and would serve the **old pixels** while the user looks at a **new file** — *quiet
wrongness of exactly the kind this project keeps finding.*

So the key is **path + size + mtime**, not path alone. **Verified: a changed file gets a fresh
reader.**

**``kwargs`` bypass the cache.** A caller passing options wants a reader built *their* way. Handing
them a differently-configured one from the cache is the same wrongness in a different hat.
**Verified.**

**Bounded at 4**, and cleared on Clear — it holds open file handles, and this is a *"same file,
several times, within one load"* cache, **not a session cache.**

---

### The audit is now fully addressed
| | |
|---|---|
| **1–3.** eager ``get_image_data()`` | **0** *(1.6.3)* |
| **4.** file opened 3–4× | **1×** *(this release)* |
| **5.** ``__array__`` materialises | **all 10 refuse** *(1.6.4)* |
| **6.** lazy layers without contrast limits | **0** *(1.6.4)* |
| **7.** TZYX full transcode | **gone** *(1.6.4)* |
| **8.** ``tifffile.imread`` on any metadata error | **lazy first** *(1.6.4)* |
| **9.** stale comments | *rewritten as they were touched* |
| packaging | **consolidated** *(1.6.3)* |

**456/456 core tests passing.**

## [1.6.5] - 2026-07-13
### The layer-tagging system was SILENTLY DEAD — and the status bar was racing napari

## 1. `ValidationError: Object has no attribute 'add_image'`
The traceback Gable saw at startup, and asked about.

**napari's ``Viewer`` is a pydantic model.** The tag hook patched it with::

    setattr(viewer, 'add_image', _wrap(...))

**Pydantic's ``__setattr__`` permits only DECLARED FIELDS.** ``add_image`` is a *method on the
class*, not a field on the instance — so the assignment was **rejected**.

> ***And the whole layer-tagging system was silently dead.***

``run_pycat`` wraps the install in ``except Exception: debug_log(...)``, so PyCAT started with **no
tag hook at all.** Every layer went **untagged**. The tag registry, the resolver, the binding table,
the Tag Inspector, the autopopulation groundwork — ***all of it inert***, and the only sign was a
traceback that read like a napari bug.

**``object.__setattr__``** bypasses pydantic's validation and writes straight to the instance
``__dict__``. Python then finds the **instance** attribute before the class method — exactly the
interception the hook needs.

*(The ``_pycat_tag_hook_installed`` flag is not a declared field either — which is why a retry could
never have helped.)*

**Guarded** against a Viewer that rejects ``setattr`` the way the real one does. *A hook that fails
to install and is swallowed by a bare ``except`` is indistinguishable from a hook that works — the
system it feeds degrades to "nothing happens", which is what an unused feature looks like.*

## 2. The status-bar flicker: TWO WRITERS, ONE WIDGET
PyCAT appended a ``mouse_move_callbacks`` handler that wrote ``viewer.status``. **But napari writes
``viewer.status`` on the same event** — so both fired, and **whichever ran last won.**

The order is not guaranteed. **The bar alternated between two strings as the mouse moved** — the
flicker, and the overlap.

> ***Racing napari's writer cannot be won.***

napari **sources** the status string from the active layer's ``get_status()``. The readout now wraps
**that** — **one writer, one string, no order to depend on** — and falls back to napari's own string
on any failure.

*(And it uses ``object.__setattr__``, because a napari Layer has the same pydantic trap that killed
the tag hook. **The same bug, twice, in two subsystems.**)*

**455/455 core tests passing.**

## [1.6.4] - 2026-07-13
# The full lazy-loading pass — and the IMS lag was in the SIX wrappers 1.6.3 missed

Gable: *"ims still lags out compared to before"* — and *"I've wanted you to do a full pass the whole
time."* **Both fair. This is the pass.**

## The IMS lag: 1.6.3 fixed THREE of NINE `__array__` methods
There are **nine** lazy wrappers. Three are in ``multidim_io``. **Six are in ``file_io`` — including
all three IMS wrappers.** 1.6.3 fixed the three, and **the guard only looked at ``multidim_io``**,
so it passed while six identical landmines sat untouched.

***A guard whose scope is narrower than the bug will certify the half that was fixed.***

And PyCAT's own source had already named the cause — **months ago**:

> *"napari auto-estimates contrast (and builds the thumbnail) by calling ``np.asarray()`` on the
> layer — which for a lazy (T,Y,X) wrapper triggers ``__array__`` and **loads EVERY frame from
> disk**. On a USB-HDD IMS stack **that is the real cause of the multi-second stalls**."*

*I read that comment, quoted it, and then fixed the wrong file.*

**All ten now refuse**, through one shared ``lazy_guard.refuse_implicit_full_read()``. *A fix applied
to some instances of a bug is a fix that will be undone by the ones it missed.*

## Every remaining item from the audit

| | |
|---|---|
| **8 eager ``get_image_data()``** | **0** — all through ``read_plane()`` *(1.6.3)* |
| **10 self-materialising ``__array__``** | **0** — all refuse |
| **3 lazy layers with no contrast limits** | **0** — pinning is what stops napari calling ``__array__`` at all |
| **the TZYX full transcode** | **gone** |
| **``tifffile.imread`` on any metadata error** | **lazy first**, eager only as an honest last resort |

### The TZYX transcode wrote the ENTIRE FILE before showing anything
```python
for t in range(n_t):
    for zi in range(n_z):
        z[t, zi] = np.asarray(dask_arr[t, zi])
```

**Every (t, z) plane, decoded and written to a temporary zarr, on the synchronous path, before the
first pixel reached the screen.** *It was not accidentally eager — it was a deliberate full-file
copy, and the note beside it said "nothing pre-loaded beyond this write pass", which was true and
which was the whole problem.*

**The dask array is already lazy.** ``_LazyArraySource`` wraps it directly: the window opens
immediately and reads one plane per slider move.

*(A zarr cache is still right for repeated random access — but it belongs in the background, behind
an explicit action, not on the critical path to first display.)*

### A metadata defect used to cost a gigabyte
``except Exception`` caught **everything** — a channel name, a pixel size, a scene entry — and
dropped PyCAT into ``tifffile.imread(file_path)``, **reading the whole file into memory.**

***A cosmetic metadata problem should not cost a gigabyte.*** It now uses the lazy page reader
first.

### `_ZarrTZYX_generic` was named after the wrong thing
It is not zarr-specific — it receives **zarr arrays, numpy arrays, and dask arrays**. And the TZYX
branch transcoded the entire file into a zarr *purely so it would have a zarr to wrap.*
``_LazyArraySource`` wraps whatever it is given.

**451/451 core tests passing.**

## [1.6.3] - 2026-07-13
### P0 — the freeze: eight EAGER reads, three self-materialising wrappers, and six install routes still shipping aicsimageio
*(From an external audit Gable commissioned. Its findings check out; its headline does not — see
below.)*

### 1. `get_image_data()` loads the ENTIRE scene
**Both libraries document it in the same words:**

> *"The ``.get_image_data`` function will **load the whole scene into memory** and then retrieve the
> specified chunk."*

**PyCAT was calling it in EIGHT places in the loading path** — including to read a *single plane* in
order to **classify** a file. On a large 4-D acquisition that pulls **the entire scene into memory to
look at one frame**, and it happens **more than once per file**, because the reader is constructed
several times before anything is displayed.

***That is the freeze.***

All eight now go through **``read_plane()``** — one function, using ``get_image_dask_data()``, which
computes **exactly one plane.** *(The audit found 3. There were 8: it missed ``open_2d_mask``,
``ui_modules``, and ``batch_step_registry``.)*

### 2. `__array__` quietly stacked every frame
``np.asarray(layer.data)`` on a lazy stack has already cost this project **two bugs** — N&B told
users their movie was 2-D; SpIDA analysed frame 0 while they looked at frame 40. The fix there was
``materialize_stack()``: **an explicit, named, deliberate full read.**

**``__array__`` did the opposite.** Any thumbnail, plugin, layer refresh, contrast estimate or stray
numpy operation could pull **an entire acquisition into memory** — *without anyone asking, and
without anything saying so.*

*A comment claimed pinned contrast limits stop napari calling it.* ***That is a hope, not a
guarantee.***

**All three now RAISE**, and name the two things the caller might actually have meant.

### 3. SIX install routes still shipped aicsimageio — and this one was mine
``requirements-base.txt``, ``meta.yaml``, and **four conda lockfiles.**

**The lockfiles were worse than stale.** They were **exported conda environments pinned to Python
3.9** — and PyCAT requires **``>=3.12``. They could not have worked.** They also pinned
``aicsimageio=4.10.0``, ``numpy=1.23.5``, ``tifffile=2023.2.28``.

***And the README told developers to build from them.***

> **Until this was fixed, no performance report from any user was interpretable.**

Deleted, with ``config/README.md`` recording what went and why. Guarded.

### The audit's HEADLINE is wrong, and the distinction matters
> *"the BioIO migration introduced an eager-read regression"*

**aicsimageio documented the same eager semantics.** ***The calls were wrong in 1.5.x too.***

What the migration did was **expose** them — ``bioio-czi`` uses a different backend
(``pylibczirw``, not ``aicspylibczi``), so **the same mistake can cost very differently.**

*Chasing "what did BioIO break?" would have been chasing a phantom.*

### Three guards, and one lesson that keeps arriving
- ``test_no_eager_reads`` — bans ``get_image_data()`` outright
- ``test_install_routes_agree`` — no route may ship aicsimageio; every route must ship ``bioio-czi``
- the lazy-stack guard now walks the **AST**

**That last one:** the old guard was a regex over the raw source, and it **flagged the docstring
explaining why ``np.asarray(layer.data)`` is dangerous.** The new eager-read guard then did the same
thing to *its own* docstring.

> ***Three times this session a guard has checked a comment. A guard that cannot tell code from
> prose will eventually flag its own explanation — and the fix is not to stop explaining.***

**450/450 core tests passing.**

## [1.6.2] - 2026-07-13
### The pixel-size gate stayed silent on a file whose scale is 2.3 PICOMETRES
Gable loaded an ImageJ-exported substack and reported the gate did not fire. **It was right not to
— and that was the bug.**

```
XResolution    = 2147054150 / 4999   ->  429,496.7 px per unit
ResolutionUnit = 1                   ->  "no absolute unit"
ImageJ unit    = µm
```

**That is 2.3 × 10⁻⁶ µm per pixel — 0.0023 nanometres.** *Smaller than a hydrogen atom.*

``2147054150`` sits just under **2³¹ = 2,147,483,648**. It is a **signed-integer overflow** in
ImageJ's **Substack export** — a known artefact, and Gable had just performed exactly that
operation.

### The gate was doing what it was told. The FILE was lying.
PyCAT asked *"is there a pixel size?"* — and there **was** one. Not ``None``, not the ``1.0``
sentinel. So it set ``pixel_size_from_metadata = True``, hid the gate, and **would have computed
every length, area and diffusion coefficient from a fabricated number.**

*(This is why the parent TIFF and the bead TIFF **did** prompt: they carry **no** resolution tag,
so PyCAT correctly fell back to the sentinel. Only the ImageJ export carries a **poisoned** one.)*

### The question is now "could a microscope have produced this?"
Bounds from **Abbe and Nyquist**, and **deliberately loose** — they exist to catch a corrupt tag,
not to second-guess a real acquisition:

| | µm/px | |
|---|---|---|
| **the ImageJ substack** | **2.3e-06** | **REJECTED** |
| SMLM render (1 nm) | 0.001 | ok |
| STED (20 nm) | 0.02 | ok |
| confocal 63× | 0.0264 | ok |
| the VPT beads | 0.067 | ok |
| 4× objective | 1.6 | ok |
| slide scanner | 10.0 | ok |

**Every real instrument passes. Gable's file misses by 400×.** *It is not a borderline call.*

### It WARNS and PROMPTS — it does not block
Gable asked directly, and it is the right question. A corrupt tag is **not** a reason to refuse the
image. The user gets:

1. **the image**, loaded normally
2. **a warning** naming the cause — *including the ImageJ Substack overflow*
3. **the gate**, prompting for the correct scale

***A file with a corrupt tag and a file with no tag end up in the same place.*** The only difference
is that the warning is honest about which one it was.

### And the first guard I wrote for this was BLIND
It read a **900-character window** after the ``elif`` and asserted the flag was set ``False`` in it.
**It passed on deliberately regressed code** — the window **spilled past the branch into its
neighbour**, which also sets the flag ``False``.

> ***A guard with no power is worse than no guard: it certifies the damage.***

**The AST knows where a branch ends. A character window does not.** Both guards now inspect the
branch **node**, and both were verified by re-introducing the exact bug and watching them fire.

**443/443 core tests passing.**

## [1.6.2] - 2026-07-13
### The pixel-size gate now asks *"could a microscope have produced this?"* — not merely *"is there a number?"*
Gable: *"the pixel size gate does not fire on an image I know lacks a proper scale."*

**It was firing correctly. The FILE was lying.**

### The file
An ImageJ-exported substack, ``polyA…Substack (109).tif``:

```
XResolution    = 2147054150 / 4999   ->  429,496.7 px per unit
ResolutionUnit = 1                   ->  "no absolute unit"
ImageJ unit    = µm
```

That is **0.0023 nanometres per pixel** — ***smaller than a hydrogen atom.***

**``2147054150`` is a hair under ``2^31 = 2147483648``.** It is a **signed-integer overflow** in
ImageJ's Substack export — a known artefact, and the exact operation that produced this file.

### Why the gate stayed silent — and why that was RIGHT
PyCAT saw a pixel size that was **not ``None``** and **not the ``1.0`` sentinel**, concluded the file
carried a real scale, set ``pixel_size_from_metadata = True``, and hid the gate.

> ***It was doing what it was told.***

*(And this is why the **parent** TIFF and the **bead** TIFF **did** fire the gate: they carry no
resolution tag at all, so PyCAT correctly fell back to the sentinel. Only the ImageJ-exported
substack carries the poisoned one.)*

### The fix: a PHYSICAL plausibility check
Bounds from **Abbe and Nyquist**, and **deliberately loose** — they exist to catch a **corrupt tag**,
not to second-guess a real acquisition:

| | |
|---|---|
| **floor** | **1 nm/px** — below the finest SMLM render, by 1000× |
| **ceiling** | **1 mm/px** — above any micrograph |

| source | µm/px | |
|---|---|---|
| **the ImageJ substack** | **2.3e-06** | **REJECTED** |
| SMLM render (1 nm) | 0.001 | ok |
| confocal 63× | 0.0264 | ok |
| the VPT beads | 0.067 | ok |
| widefield 20× | 0.325 | ok |
| slide scanner | 10.0 | ok |

***Every real instrument passes. The corrupt file misses by 400×.*** It is not a borderline call.

### It WARNS and PROMPTS — it does not block
A corrupt tag is treated **exactly like a missing one**: the pixel size falls back to the sentinel,
``pixel_size_from_metadata`` goes ``False``, **and the gate fires and asks for the scale** — the same
path as a file with no tag at all.

The only difference is that the warning is **honest about why**, naming the ImageJ Substack overflow
rather than saying *"resolution data incomplete"*, which would be **a lie of its own.**

**The image still loads.** *(Guarded:
``test_a_CORRUPT_TAG_prompts_for_a_scale_and_does_NOT_block_the_load``.)*

### BioIO is innocent
The reader comparison found **zero differences** in ``physical_pixel_sizes`` across all 31 files —
**including this one.** Both libraries read the corrupt tag identically. **This bug predates the
migration.**

**443/443 core tests passing.**

## [1.6.1] - 2026-07-13
### CI red — and the guard that should have caught it had a NARROWER SCOPE than the thing it guards
``test_smoke_the_real_code`` failed in CI:

```
data_viz_tools: No module named 'seaborn'
```

**``seaborn`` is a declared PyCAT dependency** — but the CI job installs an **explicit list**, and
seaborn is not on it.

### Why `test_ci_dependencies` did not catch it
That guard walks **``SCIENTIFIC_MODULES``** — a **hand-maintained list of 24 names** in
``test_headless_science``.

***``data_viz_tools`` is not on it.*** So its module-scope ``import seaborn`` was **never checked**,
and CI never installed it.

> ***Two lists, drifting apart.*** The smoke test — which imports **every** toolbox module — is what
> found it, and it found it **in CI, after the release was cut.**

### And there was a SECOND one, hiding behind the first
Auditing every toolbox module against the CI install list found **``fibril_tools`` imports
``networkx``**, which CI also does not install. **Nothing caught it** — for the same reason — and it
had not surfaced only because the smoke test hit ``data_viz_tools`` first *(alphabetically)* and
stopped at the assert.

### The fix is the SCOPE, not the two packages
CI now installs ``seaborn`` and ``networkx``. **But that is the symptom.**

``test_ci_dependencies`` now **derives** its scope — **every toolbox module**, the same set the
smoke test imports — instead of reading a hand-curated list.

> ***A guard whose scope is narrower than the thing it guards will eventually miss something.***

**Verified**: remove the CI install line and the guard now **fires**. Before this change, it did not.

**423/423 core tests passing.**

## [1.6.0] - 2026-07-13
# ⚑ BioIO replaces aicsimageio. The pins are free.

**This is the first release with BioIO and no aicsimageio.** ``v1.5.532`` is the last wholly
BioIO-free version — **revert there if this goes wrong.**

---

## It is a REMOVAL, not an addition — and that is the whole point
``aicsimageio`` is in **maintenance mode**, **frozen in 2023**, and pins:

```
zarr<2.16   tifffile<2023.3.15   fsspec<2023.9   lxml<5
```

***Those pins are what held ``numpy<2`` and ``zarr<3`` in place.*** They are not an obstacle to the
migration — **they are the reason for it.** Removing aicsimageio **frees** them rather than fighting
them.

This was discovered the hard way: installing ``bioio`` alongside it pulled in numpy 2.5.1, zarr
3.2.1 and tifffile 2026.6.1, **uninstalled the pinned ones**, and broke **cellpose, numba and the
image loader in one command.** The two libraries **cannot coexist.**

## The evidence, gathered before the switch
BioIO and aicsimageio each read **38 real files** in **separate environments**, and the results were
compared offline:

| | |
|---|---|
| **identical** | **31** — the Zeiss CZI, ``3.30 hr_1_MMStack_Pos0``, every OME-TIFF, every in-vitro TIFF, every batch output |
| **different** | **0** — shape, dtype, **dimension order**, **pixel size**, scenes, **and the SHA-256 of the pixels** |
| not comparable | 6 — all ``.ims``, and ***neither library reads them*** |

**The ``.ims`` result is not a gap.** PyCAT intercepts ``.ims`` and routes it to
``imaris_ims_file_reader``, its own HDF5 reader. ***The comparison tested a path PyCAT does not
take.*** Confirmed working in the app.

*A dimension-order difference **would not crash** — it would return the **wrong channel**. A pixel-size
difference changes **every length, area and diffusion coefficient** PyCAT reports. Both were checked.
So were the pixels.*

## What changed

**Dependencies**
- **removed:** ``aicsimageio``, ``aicspylibczi``
- **added:** ``bioio``, ``bioio-ome-tiff``, ``bioio-tifffile``, **``bioio-czi``** — *not optional;
  Zeiss market share makes CZI non-negotiable*
- **freed:** ``numpy>=1.22`` *(was ``<2.0``)*, ``zarr>=2.12`` *(was ``<3.0``)*

**Code** — and it is small, because the seam landed in 1.5.529:
- ``file_io.py``'s ``aicsimageio`` import was **already dead** — an AST walk confirms ``AICSImage``
  was referenced nowhere. Every construction goes through ``open_image()``.
- the seam's default flipped to ``bioio``
- ``pycat.file_io.zarr_compat`` (1.5.533) handles ``DirectoryStore`` **and** ``LocalStore``, and the
  path on ``.path`` **and** ``.root``

## Verified, not assumed
- **numpy 2**: all 122 modules scanned for every removed API — ``np.float_``, ``np.NaN``,
  ``np.alltrue``, ``np.product``, ``np.in1d``, ``np.trapz`` … — **zero occurrences.** *These break
  at RUNTIME, in whichever analysis touches them, so a green import proves nothing.*
- **zarr 3**: every ``zarr.open`` call AST-walked. Four kwargs ever passed — ``mode``, ``shape``,
  ``chunks``, ``dtype`` — **all survive.**
- **the drop-detector** reports **no new losses** against the high-water mark of nine snapshots.

## The remaining pin is a SCIENTIFIC choice
``cellpose<4`` stays. **Not a technical constraint** — Cellpose declares only ``numpy>=1.20``.
Cellpose 4 **removed the cyto2 CNN** and replaces it with a ViT-L transformer that is *very slow on
CPU*, which matters for the lab machines without a GPU.

## If a reader plugin is missing
BioIO's readers are **separate packages** — a genuine improvement, and a trap. **A missing plugin is
a missing FORMAT**, and PyCAT says so by name rather than reporting *"cannot read file"*, which
would send a scientist looking at their microscope.

**423/423 core tests passing.**

## [1.5.533] - 2026-07-13
### BioIO stage 1 of 3 — the zarr surface is now version-agnostic
**Stage 2 passed first:** BioIO and aicsimageio were run against **38 real files** in separate
environments and compared offline.

| | |
|---|---|
| **identical** | **31** — *including the Zeiss CZI, ``3.30 hr_1_MMStack_Pos0``, every OME-TIFF, every in-vitro TIFF, every batch output* |
| **different** | **0** — shape, dtype, **dimension order**, **pixel size**, scenes, **and the SHA-256 of the pixels** |
| not comparable | 6 — all ``.ims``, and **neither library reads them** |

The ``.ims`` gap is **not a gap**: PyCAT intercepts ``.ims`` at ``_open_image_auto_single`` and
routes it to ``open_stack`` → ``imaris_ims_file_reader``, its own HDF5 reader. ***The probe tested a
path PyCAT does not take.*** Confirmed working in the app.

### The zarr rename is what makes this a chain rather than a swap
From zarr's migration guide:

```
- from zarr import MemoryStore, DirectoryStore
+ from zarr.storage import MemoryStore, LocalStore   # LocalStore replaces DirectoryStore
```

- ``aicsimageio`` is **frozen in maintenance mode** and pins ``zarr<2.16``
- BioIO's plugins want **zarr 3**
- PyCAT's lazy loaders were written against **zarr 2**

***So the reader cannot be replaced until the store class is version-agnostic.***

### A CAPABILITY question, not a CLASS check
PyCAT asks ``DirectoryStore`` exactly one thing, in **three** places: *"is this zarr backed by a
directory on disk, and if so, where?"*

**That is a capability, not a class.** ``pycat.file_io.zarr_compat.store_path`` answers it, and works
on zarr 2 **and** zarr 3.

### The second trap, which would have been much harder to find
zarr 3's ``LocalStore`` exposes the path as **``.root``** (a ``Path``), not **``.path``** (a ``str``).

**So even after fixing the class name, a bare ``store.path`` would return ``None`` on zarr 3 —
silently** — and PyCAT would copy a stack it did not need to copy. *A silent fallback that merely
wastes time is still a silent fallback.*

### The rest of the surface needs nothing — checked, not assumed
An AST walk of **every** ``zarr.open`` call (25 of them) found only four kwargs: ``mode``, ``shape``,
``chunks``, ``dtype``. **All four survive in zarr 3.** No zarr-2-only argument appears anywhere.

### Shipping while `zarr<3` is still pinned
Nothing changes today. The compat layer works on the zarr that is installed **now**, and it is ready
for the pin to move. *(A guard fails the build if any line names ``DirectoryStore`` directly — and
it immediately caught a **third** call site that a hand-search had missed.)*

**420/420 core tests passing.**

## [1.5.532] - 2026-07-13
### ⚑ THE LAST WHOLLY BioIO-FREE VERSION — the revert point for the 1.6.0 migration
**1.5.532 is the last release with no BioIO code path at all.** Everything from here stages toward
**1.6.0**, which will be the first release with **BioIO and no aicsimageio**.

*(The reader seam shipped in 1.5.529 is inert: ``aicsimageio`` remains the default and the only
library installed. Nothing in this release depends on BioIO.)*

**If the migration goes wrong, revert here.**

---

### FIXED — the test runner was hiding 58 tests from me
CI collected **433 items, 411 selected.** ``tools/run_core_tests.py`` reported **354/354 passed.**

> ***I was shipping against a different test suite than the one that gates the build.***

This sandbox has **no pytest and no network**, so the runner is a **hand-rolled substitute**. It
found parametrize cases with a **regex**:

```
r'@pytest\.mark\.parametrize\(\s*"(\w+)",\s*(\[[^\]]*\])\s*\)'
```

A **single** parameter name, a **single-line** literal list, and nothing else. It caught **32**
decorators and **missed 20**:

- multi-parameter forms — ``parametrize("scene,expected", [...])``
- multi-line value lists
- **computed** value lists — ``parametrize("mod", SCIENTIFIC_MODULES)``, ***which is every
  scientific module, and it never ran***

**This is the exact failure ``run_core_tests.py``'s own docstring was written to prevent** — a
development check *more forgiving than the runner it stands in for* — arrived at from the other
direction.

**A regex cannot model pytest's collection rules. An AST walk can**, because it **reads** the
decorator instead of guessing its shape. It also resolves **computed** value lists by executing the
module first — the form that hid the scientific modules.

**354 → 415.** All pass.

### GUARDED, as Gable asked
``test_the_runner_matches_pytest.py`` now fails the build if:
- the runner loses its AST-based collection *(verified: restore the old regex and it fires)*
- any parametrize decorator in the suite is written in a form the runner cannot read — **because a
  decorator the runner cannot parse is a test that silently does not run**
- the collected count drops below the floor

> ***A runner that silently under-collects is worse than no runner: it produces a green number that
> is not true, and the divergence only surfaces when CI goes red — after the release is cut.***

**415/415 core tests passing.**

## [1.5.531] - 2026-07-13
### The environment check cried wolf on a HEALTHY environment — fixed
1.5.530 reported this to Gable on an environment that was **fine**:

```
tifffile   required: <2022.4.22,>=2022.7.28
```

***Nothing can be simultaneously below 2022.4.22 and above 2022.7.28.***

> **A check that emits an unsatisfiable requirement has not found a problem — *it IS the problem*,**
> and it trains the user to ignore the one message that might one day matter.

### Two bugs, both mine
**1. It kept requirements that only apply to an EXTRA.** ``napari`` declares
``tifffile<2022.4.22; extra == 'testing'`` — a pin that applies **only** to ``napari[testing]``,
which nobody installs. The code split on ``;``, ***threw the marker away, and kept the line
anyway*** — merging a test-only pin into the runtime constraint and intersecting it with
``aicsimageio``'s into something impossible.

**2. It matched dependency names by PREFIX.** ``'numpydoc'.startswith('numpy')`` is **True**, and
the remainder (``doc>=1.0``) is garbage.

Both are fixed by **parsing the requirement properly** (``packaging.requirements.Requirement``)
instead of guessing at its shape.

### And a guard on the guard
**If a constraint cannot be satisfied by any version, the bug is in the checker, not the
environment.** It is now suppressed and logged rather than shown — *reporting it sends the user
chasing a fix that cannot exist.*

### Verified both ways
| environment | result |
|---|---|
| **Gable's, after repair** | **silent** — correctly reports nothing |
| **the BioIO disaster** (numpy 2.5.1, zarr 3.2.1, tifffile 2026.6.1, fsspec 2026.6.0, lxml 6.1.1) | **catches all five**, with **satisfiable** constraints |

*A guard that has gone quiet everywhere is worse than useless, so both halves are tested.*

**354/354 core tests passing.**

## [1.5.530] - 2026-07-13
### PyCAT now says when its environment has been broken — because it cannot stop it happening
Gable: *"I just wanted to make certain that somebody couldn't bork their PyCAT by downloading and
installing a napari plugin."*

**They can, and there is no way to prevent it.** pip has no *"conflicts-with"* field; napari
discovers plugins from whatever is installed; and napari's own plugin manager makes installing one a
**single click.**

> ***So PyCAT cannot prevent the damage — it can only refuse to pretend nothing happened.***

### This is not hypothetical
Installing ``bioio`` into a working PyCAT environment silently pulled in **numpy 2.5.1**, **zarr
3.2.1** and **tifffile 2026.6.1**, ***uninstalling the pinned ones***, and broke **cellpose, numba
and the image loader** in one command.

The failure the user actually saw:

```
AttributeError: '_TIFF' object has no attribute 'RESUNIT'
```

***That message sends a scientist looking at their microscope.*** It is ``aicsimageio`` reading a
``tifffile`` **three years newer than it supports** — and **nothing in that traceback says so.**

Now, at startup:

```
tifffile
    installed : 2026.6.1
    required  : <2023.3.15,>=2021.8.30
    pinned by : aicsimageio
    this is   : the TIFF reader. A too-new one breaks aicsimageio: 'no attribute RESUNIT'

TO REPAIR:
    pip install "tifffile<2023.3.15,>=2021.8.30" ...
```

### The first version of the check was BLIND to the failure that prompted it
It read only ``pycat-napari``'s own requirements — **and PyCAT does not pin tifffile at all.**
``aicsimageio`` does.

> ***A guard that misses the exact failure that prompted it is theatre.***

It now walks the packages that **hold** the load-bearing pins — ``aicsimageio``, ``cellpose``,
``numba``, ``napari``, ``bioio`` — and **names which one declared each constraint.** *"tifffile is
wrong"* is not actionable; *"aicsimageio requires tifffile<2023.3.15"* is.

### The pins are read from METADATA, never hardcoded
**They are about to move.** The entire point of the BioIO work is that ``aicsimageio`` is frozen in
2023 and is what holds ``numpy<2`` and ``zarr<3`` in place.

***A check that hardcoded today's pins would start lying the day they change*** — and a lying check
is worse than none, because it would **confidently clear a broken environment.** A test enforces
this.

**351/351 core tests passing.**

## [1.5.529] - 2026-07-13
### BioIO: one seam, and the audit behind it
``aicsimageio`` is in **maintenance mode**; its maintainers name ``bioio`` as the *"compatible
successor"*. This ships the **seam** and the **acceptance test** — not the switch.

### The audit — and it is smaller than `file_io.py`'s 3,874 lines suggest
**1. The API surface PyCAT uses is FIFTEEN attributes, and BioIO matches every one.**
``.data .dims .shape .dtype .metadata .scenes .set_scene .current_scene .physical_pixel_sizes
.get_image_data .get_image_dask_data .xarray_dask_data`` — same names, same semantics, same **TCZYX**
ordering.

**2. The lazy layer is ALREADY decoupled — the single most important finding.**
``multidim_io``'s ``_ZarrTZYX_generic`` wraps a **plain zarr array**, not an ``AICSImage``. The
reader only *writes* that zarr. ***So the lazy path — the one carrying the ``__array__`` frame-zero
landmine that has already cost this project two bugs — is untouched by the swap.***

**3. Only ONE format genuinely needs the library.** ``.ims`` has its own HDF5 reader; ``.tif`` goes
through ``tifffile`` on the fast path; video goes through ``cv2``. **``.czi`` is the one format that
truly requires it.**

**4. The reader never escapes into the analysis code** — a local variable in five functions,
reaching exactly three consumers, all metadata extractors.

### Why a seam and not a find-and-replace
A find-and-replace would work, and it would be **irreversible in one step.** This project has been
bitten twice by a change that looked safe and could not be A/B-ed — the **rolling-ball
normalisation** that made batch disagree with the recording, and the **frame-zero collapse** that
told users their movie was a still image.

***Both were invisible until someone compared two runs.***

All **10** construction sites now route through ``pycat.file_io.image_reader.open_image``, and a
test fails if any new one appears. *(It already earned that: it caught a site in ``ui_modules`` that
this changelog's own audit had listed and I had then missed.)*

```
PYCAT_IMAGE_READER=bioio      run-pycat     # the new path
PYCAT_IMAGE_READER=aicsimageio run-pycat    # the incumbent, unchanged — and still the DEFAULT
```

### The acceptance test runs on REAL files
``compare_readers()`` opens the same file with **both** libraries and reports every difference in
shape, dtype, **dimension order**, **pixel size**, scenes — and **the pixels themselves.**

- a **dimension-order** difference *would not crash*. It would return the **wrong channel.**
- a **pixel-size** difference changes **every length, area and diffusion coefficient** PyCAT reports.
- everything else can match while **the data differs** — a byte-order bug, an off-by-one at a chunk
  boundary. **The only claim worth making is that the pixels are identical.**

***The default stays ``aicsimageio`` until that comparison is clean on real CZI, Micro-Manager
OME-TIFF, and the astigmatic bead movie that has already exposed two loader bugs.*** Flipping a
default is a decision to make with evidence in hand, not with a passing import.

**347/347 core tests passing.**

## [1.5.528] - 2026-07-13
### The high-water mark is now APPEND-ONLY BY CONSTRUCTION
Gable: *"I assume the new tool is pinned so if I change chat sessions the protection is in place?"*

**It is in the repo, not in memory — so yes, it survives a session change.** But the question
exposed a real gap, and it is the same trap the tool exists to prevent, one level up.

### The gap
The mark records **the best each function has ever been.** But nothing stopped a future session
**regenerating it from a tree that had already lost something** — which would **bake the loss in as
normal** and silence the guard forever.

> ***That is worse than having no guard: it is a guard that certifies the damage.***

### The fix
``--update`` is now a **union with what is already recorded**, never a replacement. A parameter set
can only **gain** members; a recorded length can only **rise**; a function that once existed is
**never forgotten**. And it **refuses to write a mark smaller than the one it read**, on any axis.

**You cannot regenerate a clean baseline from a dirty tree.**

### And testing the refusal found a second flaw
On a deliberately regressed tree, the update **printed "mark updated, +0" and exited 0** — because
the union had correctly carried the lost parameters forward. **The memory was safe.**

**But the mark being safe is not the same as the tree being safe.** An update that reports success
while the tree has lost four safety parameters is *technically correct and practically useless* —
***it tells you nothing is wrong while something is.***

``--update`` now also reports what the **current tree** is missing relative to the mark, and
**exits 1**. Verified on the exact regression: it names
``segment_subcellular_objects LOST: ['image_stats', 'punctate_gate', 'punctate_gate_abs_sigma',
'punctate_gate_sigma']``.

**343/343 core tests passing.**

## [1.5.527] - 2026-07-13
### A mechanism to catch DROPPED CODE — because the spurious-puncta bug was a silent deletion
Gable, after the incident:

> *"how do we make sure you don't throw away good code while doing these audits — the rationale was
> even in the code and you dropped it. We need some mechanism to track these drops, because for all
> I know every module we've validated has truncated features away."*

**The concern is exactly right.** Every edit in this workflow is a **whole-file rewrite** — no diff,
no merge, no three-way. **If a rewrite emits fewer lines than it read, the difference is simply
gone**, and:

- the file still **compiles**
- every test still **passes**
- the function still **exists**, just with fewer parameters

***A capability can disappear and nothing anywhere notices.***

### The audit: what actually happened across the whole session
Nine repo snapshots (**1.5.304 → 1.5.517**) were replayed function-by-function. **In my session's
window — 1.5.475 → 1.5.526, 1742 → 1819 functions:**

| | |
|---|---|
| functions vanished | **5** — *all five the ``file_io`` stack helpers I deliberately moved to ``stack_access.py``, which re-exports them. Verified.* |
| **parameters lost** | **0** |
| **function bodies shrunk** | **0** |

**Nothing was thrown away.** *(And ``punctate_gate`` was never in ANY snapshot I was given — Meet's
file is a lineage that only existed locally.)*

### And the first version of the guard was BLIND — which is the deepest form of the bug
It compared the tree against the **most recent snapshot**, and reported *"nothing dropped"* while
the punctate gate was **entirely missing** — because **the baseline was itself regressed.**

> ***A tool that compares against a broken baseline reports ALL CLEAR while everything is gone.***
> That is the same failure it exists to prevent, one level up.

### The fix: a HIGH-WATER MARK, not a diff
``.pycat/high_water_mark.json`` — for every function ever seen in **any** snapshot, the **largest
parameter set** and the **longest body** it has ever had. **1,825 functions**, from nine snapshots
plus the working file Meet sent.

**A capability that disappeared three versions ago is still missing today, and this still says so.**

Three tests now run on every change:
- **no scientific parameter has been dropped** — *a lost parameter is a lost capability*
- **no function has vanished**
- **no function body has been truncated** by >30% — *``cell_mask_stretching`` went 146 → 85 lines
  and still had two of its four parameters, so a signature check alone would have missed it*

**Verified by re-introducing the exact regression: the guard fires.**

### Every hit is a QUESTION, not a verdict
**A legitimate deletion looks exactly like an accidental one.** When a removal is deliberate it goes
in ``_DELIBERATE`` **with a reason** — and *that list is itself the record of what was removed and
why.*

**343/343 core tests passing.**

## [1.5.526] - 2026-07-13
### RESTORED — spurious puncta came back because the tree had REGRESSED
Meet reported spurious puncta returning and **sent the file that worked.** Diffing it against the
tree was decisive: ***the tree was the older file.***

It had lost an entire subsystem — and **Meet's copy already contained the module-level
``from cellpose import models``** that 1.5.523 "discovered". *A newer file was overwritten with an
older one during this session's validation work.*

### The mechanism — verified, not assumed
``sk.exposure.equalize_adapthist`` **normalises every cell to unit maximum.**

So a cell containing **only noise** is amplified by ``1 / cell_max`` — **measured at 500×** on a
cell holding nothing but background — and **both cells come out of CLAHE with the same [0, 1]
range.** The empty cell's noise now has structure, and **it segments as puncta.**

### Why the existing contrast check could never catch it
The restored code says it plainly:

> *"``check_contrast_func`` **cannot catch this**: it inspects the image AFTER those
> contrast-maximising steps, so it essentially **never fires**. This gate runs BEFORE them, on the
> raw intensity image, and is **the only place in the chain where absolute brightness is still
> available**."*

### What was restored
- **``compute_image_intensity_stats``** — measures the image's absolute background and noise floor
  **once, before any per-cell renormalisation**
- **``cell_has_punctate_signal``** — ***a hypothesis test, not a contrast heuristic.*** A pixel
  counts as evidence only if it clears **both** a local floor **and** an absolute one
- **``min_relative_max``** in ``cell_mask_stretching`` — the dim-cell gain ceiling (a **50× cap**)
- the **four parameters** threaded back through both segmentation entry points

**Verified end-to-end:**

| cell | has puncta? | largest blob | peak z |
|---|---|---|---|
| **real (3 puncta)** | **True** | 278 px | **122.3σ** |
| **empty (noise only)** | **False** | **0 px** | 0.9σ |

### Merged, not pasted
**A blind overwrite would have destroyed real work in both directions.** The tree had newer changes
Meet's file lacked — ``_robust_bg``, the bbox sweep, the ``@tags_layer`` decorators. **Full parity
verified in both directions: nothing lost either way.**

### And the ratchet caught the restore
Two long functions came **back**, taking the count from 137 to 139. **That is the ratchet working**
— and the honest response is to record that they returned, not to shave them to squeeze back under.

**338/338 core tests passing.**

## [1.5.525] - 2026-07-13
### FIXED — batch segmented the same image differently from the recording
Reported by Gable, and it is the failure that makes the whole feature unusable: ***a batch run that
does not reproduce what the user saw interactively is not a batch run — it is a different
experiment.***

### The cause: batch pre-normalised the image; the GUI did not
``pre_process_image`` **normalises internally** — ``img = img / img.max()``. It expects **raw
counts**, and it **divides**.

Batch called ``_normalize_to_float`` first, which does ``(x - min) / (max - min)`` — **it subtracts
the pedestal too.** The ``/max`` inside ``pre_process_image`` is then a **no-op**, and the two
callers hand the rolling ball genuinely different images:

```
INTERACTIVE   img / max            ->  range [0.425, 1.0]
BATCH         (img-min)/(max-min)  ->  range [0.000, 1.0]
```

**And the rolling ball is NOT scale-invariant.** ``skimage.restoration.rolling_ball`` rolls a ball
in **(x, y, INTENSITY)**, and its ``radius`` applies to **all three axes.**

| path | mean of the background-subtracted image |
|---|---|
| interactive | **0.0205** |
| **batch (before)** | **0.0493** — *2.4× more background removed* |
| batch (fixed) | **0.0205** — *bit-for-bit identical* |

### And a WORSE one, found while auditing: the BRANCH diverged
``run_enhanced_rb_gaussian_bg_removal`` decides whether the input is *"already preprocessed"* with
``median(nonzero) < 0.05``. **That heuristic is scale-dependent, and batch changed the scale.**

On a **bright condensate on a dark background** — flat background, contrast ≥ 3000 counts, entirely
normal for in-vitro data:

| path | median | verdict | processing applied |
|---|---|---|---|
| **INTERACTIVE** | **406 counts** | not enhanced | **full rolling-ball removal** |
| **BATCH** | **0.028** | **"already enhanced"** | **soft suppression only** |

***Not a shifted number — a different algorithm.***

**Six call sites fixed** to pass ``_raw_counts``. Verified: **zero rolling-ball paths still
normalise.**

### What was NOT broken — verified, not assumed
- **The recorded parameters.** ``ball_radius``, ``window_size`` and ``cell_diameter`` are all saved
  and replayed correctly. *The first hypothesis — that the measured lines were not being recorded —
  was wrong, and ruling it out took ten minutes.*
- **Cellpose.** Its default ``normalize=True`` percentile-rescales internally, and a percentile
  transform is **invariant** under an affine ``(x-min)/(max-min)``. Verified through the full chain
  (raw → ``img_as_uint`` → percentile-norm): **max difference 1.85e-05.**
- **Multi-Otsu.** Thresholds on the histogram's *shape*. Scale-invariant by construction.

***Not every normalisation is a bug, and saying which ones are not is part of the audit.***

### And the test fixture had to be re-derived
A first version of the regression test used the standard fixture at contrast 3000 — and its
normalised median came out at **0.0588**, *above* the 0.05 threshold. **It did not reproduce the
bug**, and loosening the assertion would have produced a test that asserts nothing.

The background **gradient** keeps the background wide, which **raises** the normalised median. On a
**flat** background — which is what in-vitro data has — the divergence appears at **3000 counts of
contrast.** The test now uses that condition, and **says so.**

**335/335 core tests passing.**

## [1.5.524] - 2026-07-13
### The Cellpose bug is the most important datum in this audit
A **one-line** bug made **cell segmentation — the single most-used feature — completely
non-functional for every Cellpose 3.x user.** It survived **300+ tests and a fifteen-bug audit**,
and shipped.

**Why?** Every test in the suite is one of two shapes:

- **source-reading** — walks the AST, checks a property of the *text*
- **unit** — calls a pure function with numpy arrays

***Nothing imports the package and runs a workflow.*** So an integration failure — a version
branch, a missing import, a decorator that throws at registration, a signature drift between a
caller and its callee — is **invisible**.

### And it is not a hypothetical class
**All three user-blocking bugs reported this month were integration failures:**

| | |
|---|---|
| Meet — arm64 segfault | torch's libomp against numba's |
| Abhradeep — OpenGL corruption | a GPU driver against Qt |
| Meet — **Cellpose dead** | a version branch that never imported |

***Zero of them were unit-testable. All of them would have been caught by actually running the
thing.***

### `test_smoke_the_real_code`
The cheapest possible version of *"run the thing"*: **import every module, and call the code that
actually breaks.** It does not check that the answers are right — 38 other test files do that.
**It checks that the code runs at all.**

**Verified by re-introducing the Cellpose bug: three tests now catch it**, including the one that
*calls the function*.

**331/331 core tests passing.**

## [1.5.523] - 2026-07-13
### CRITICAL — Cellpose segmentation was completely dead on Cellpose < 4
```python
if _cellpose_major_version() >= 4:
    from cellpose import models              # <-- imported HERE
    model = models.CellposeModel(...)
else:
    model = models.CellposeModel(...)        # <-- used HERE. Never imported.
```

**Every Cellpose 3.x install** — which is most of them — took the ``else`` branch and died:

```
UnboundLocalError: cannot access local variable 'models'
```

**Cell segmentation did not work at all.** Reported by Meet; reproduced exactly by stubbing
cellpose 3.1.0.

**Fixed** — the import moved **above** the branch, where both paths can see it. Verified on both:

| version | API used | result |
|---|---|---|
| **Cellpose 3.x** | ``model_type='cyto2'`` | **works** *(was crashing)* |
| **Cellpose 4.x** | ``pretrained_model='cpsam'`` | **works** |

### And `test_no_undefined_names` CANNOT catch this — which is the real lesson
``models`` **IS** bound — just in a branch that may not run. **A scope-chain checker sees a binding
and stops.**

***It is not a scoping bug. It is a control-flow bug, and it needed its own guard.***

``test_conditional_imports`` now walks every function's AST and asks: **is any name imported inside
one arm of a branch and used inside another?** That is an ``UnboundLocalError`` for exactly the
users who take the other path — *and it looks perfectly fine to every other check we have.*

**Verified by re-introducing the bug and watching the guard fire.**

**329/329 core tests passing.**

## [1.5.522] - 2026-07-13
### `extend_mask_to_edges` wrote into the CALLER's array — and returned the same object
```python
mask[0:size, :] = mask[size, None]
```

That modifies the array it was **given**. Measured: a caller's mask went from **361 px to 400 px**,
and ``result is mask`` was **True** — ***there was no new array at all.***

**If that array is a napari layer, the user's mask on screen silently changes.** And a workflow
re-run starts from data that is **no longer what the user segmented**.

It happened to be **idempotent** — running it twice gave the same answer — but that was **luck, not
design**: the second call simply found the border already filled. ***The aliasing is the bug, and
idempotence does not excuse it.***

And ``segmentation_tools`` passes ``refined_labels`` to it — a **labels** array, not a boolean mask
— so the propagated border carries **label IDs**.

**Fixed and verified**: the caller's mask is untouched (361 → 361), the result is still correctly
extended (400 px), it is a **new object**, and the labels case preserves its IDs.

### GUARDED as a category
A test now fails any toolbox function that writes into a parameter array without copying it first.
The four exceptions are deliberate and **say so in their names** — ``stitch_into``,
``_draw_msd_into``, and the two plotters that fill a caller-supplied ``line_registry``.

### Audited and CLEAN — the magic-threshold class
**30 bare-number thresholds** on measured quantities. Checked the consequential ones, and they hold
up:

- **``N > 70`` in SpIDA** — a *numerical* switch, not a magic number: 70! overflows float64, and the
  Poisson→Gaussian difference there is **0.00134 and shrinking.** Verified.
- **``floor/spread > 0.17``** — already calibrated with a table in the source (the error passes 15 %
  between 0.148 and 0.189).
- **the QC thresholds** — already carry their reasoning, including one that documents *"I tried to
  subtract it, and it cannot be done from this image."*

***Not every suspicious-looking number is a bug, and saying so is part of the audit.***

**327/327 core tests passing.**

## [1.5.521] - 2026-07-13
### Fit-quality audit COMPLETE — and two `popt, _ = curve_fit(...)` that are CORRECT
``_fit_sigma`` (in ``vpt_tools`` and ``general_image_tools``) discards the covariance, exactly like
the SACF and CCF fits that turned out to be real bugs. **It is not the same bug**, and the
difference is worth stating rather than sweeping both up together.

**There, one fit IS the answer.** Here it is **one of forty**: the caller takes
``np.median(psf_sigmas)`` across every bead, and **the median tolerates up to 50 % garbage by
construction.** Verified — with **40 %** of the fits replaced by uniform noise, the median still
recovers **2.12** against a true **2.00**.

***A per-fit quality gate would add cost and no protection.*** Documented on the function.

### The audit, in full
**Real bugs, fixed:**

| function | what it did |
|---|---|
| ``fit_gaussian_2d`` (CCF) | σ = **0.495** from **pure noise**, reported as real |
| ``fit_gaussian_1d`` (CCF) | same helper |
| ``_fit_sacf_1d`` (SACF) | σ = **119.8 px** from **pure noise** |

**Verified sound — not assumed:** ``fit_anomalous_diffusion``, ``fit_frap_recovery``,
``fit_coarsening``, ``fit_photobleaching``, ``fit_fusion_relaxation``, ``fit_size_distribution``,
``fit_bleaching_trace``, ``fit_spida_histogram``, ``estimate_phase_boundary`` (bootstrap CI), and
both Gaussian-localization fits. **All report an R² or an explicit uncertainty.**

### And three bugs of my OWN, caught while fixing it
1. The shared helper **guessed the width index from ``len(popt)``** → read the **baseline** as the
   width → **rejected every real fit.** *A guard with no power.*
2. My test built ``gaussian + white noise``, which **is not what an ACF looks like** → I was
   measuring a failure of the *estimator* on an input it never sees, **and blaming the guard.**
3. ``inf`` from ``pcov`` means **both** *"perfect fit"* (singular covariance) **and** *"total
   failure"*. Conflating them would have **thrown away the best fit the function can produce.**

**324/324 core tests passing.**

## [1.5.520] - 2026-07-13
### The SACF threw its covariance away — and reported a **119.8 px** correlation length for noise
``_fit_sacf_1d`` ended with ``popt, _ = curve_fit(...)``. **That ``_`` is ``pcov``**, and it is the
only thing that says whether the Gaussian describes the data at all.

``curve_fit`` succeeds on **anything**. Measured on this exact function:

| input | fitted sigma |
|---|---|
| real ACF, σ = 2 / 5 / 10 | **2.000 / 5.000 / 10.000** *(exact)* |
| **PURE NOISE** | **119.8**, **0.624**, nan, nan |

**Two of four white-noise inputs returned a finite, plausible-looking correlation length** — and
nothing said they were meaningless. **A spatial autocorrelation length is a physical claim about
structure in the image, and there is no structure in white noise.**

Now gated on the fit's own error bar: **pure noise 6/12 → 3/12**, and **real correlations survive
8/8 at every noise level.**

### FOUND — I built a guard with no power, and caught it
The shared helper **inferred the width index from ``len(popt)``**::

    len(popt) > 3  ->  width at [3, 4]

That is right for the **5**-parameter 2-D model ``(amp, x0, y0, σx, σy)`` and **wrong for the
4-parameter 1-D model** ``(amp, μ, σ, baseline)`` — where the width is still at **[2]** and index 3
is the **baseline**.

**It read the baseline's error as the width's**, divided by a baseline of ~0, and returned ``inf``
— ***rejecting every real fit.*** A true σ of 2 and of 5 both came back NaN.

> ***That is a guard with no power — the exact failure this audit keeps flagging, and I built one.***

**The caller knows its own model. It now passes the index.**

### And my TEST was wrong too, in a way worth recording
The first version built ``exp(-x²/2σ²) + white noise`` and concluded the guard was rejecting good
data. **That is not what an ACF looks like.** An autocorrelation is **smooth by construction** —
correlating an image averages its noise away.

**I was measuring a real failure of the estimator on an input it will never see, and blaming the
guard.** The test now builds the ACF from an actual image.

**324/324 core tests passing.**

## [1.5.519] - 2026-07-13
### A correlation length from an image with NO correlation was reported like a real one
``fit_gaussian_2d`` returns the width of a Gaussian fitted to the correlation function — the
**correlation length**. On **pure noise**, where there is no Gaussian to fit, ``curve_fit`` still
returns *a* number: **0.495**. **Nothing distinguished it from a real measurement.**

### The signal was there, and unused
``goodness_of_fit`` is ``sqrt(diag(pcov))`` — **the standard error on each fitted parameter:**

| scene | ccf_sigma | **error on sigma** |
|---|---|---|
| real (σ = 5) | **5.000** | **0.0** |
| real + noise | 5.016 | 0.0 |
| **PURE NOISE** | **0.495** | **241.0** |

**A three-order-of-magnitude signal** — and it was placed in a DataFrame column labelled
``'Covariance'``, ***which nothing ever read.***

### `sigma_rel_error` and `fit_is_meaningful`
The error **as a fraction of the value** — scale-free, so one threshold works from a 2-px
correlation to a 20-px one. **> 0.5 means the fit does not believe its own answer.**

| scene | rel_error | meaningful? |
|---|---|---|
| real σ=5 | 0.000 | **100 %** |
| real, **heavy** noise | 0.078 | **100 %** |
| **pure noise** | **45.9** | **12 %** |

*(The power matters: a guard that rejects heavy-noise data would look impressive and be useless.)*

### FOUND — `inf` from pcov means TWO OPPOSITE THINGS
A **noiseless** fit — a perfect analytic Gaussian, residual exactly zero — makes scipy's covariance
**singular**, and every entry comes back ``inf``. **So does a fit that failed completely.**

Measured: a clean σ = 2 Gaussian fits to ``popt = [1.0, 0, 0, 2.0, 2.0]`` — **exact** — and its
``pcov`` is all ``inf``. **A first version of this flag rejected it** — *it would have thrown away
the single best fit the function can produce.*

**The residual tells them apart**, and now does.

*(Same module whose ``ccf_sigma`` was a 13× underestimate in 1.5.481. That fix holds: a true σ = 5
recovers as **5.000**.)*

**319/319 core tests passing.**

## [1.5.518] - 2026-07-13
### FIXED — the axis-warning sweep was written and never shipped
1.5.511 added ``warn_if_assumed_axis`` to the six UIs that compute a **rate from frame indices**
and never warned that the time axis might have been **guessed**. **The test shipped. The code did
not.**

The repo has therefore been carrying ``test_every_UI_that_treats_frames_as_TIME_warns_about_an_assumed_axis``
against six files that never received the change. *(It passed only because the test looks for the
call and the release bundle omitted the files that would have failed it — the same class of miss as
1.5.504.)*

**The consequence is real.** An undeclared multipage TIFF carries **no axis metadata**, so the user
labels it **T or Z at load**. **T and Z load identically** — a wrong label is harmless for viewing,
and **there is nothing on screen to tell you it happened.**

But a step that treats frames as **time** — an **MSD**, a diffusion coefficient, a coarsening rate,
a trajectory — is computing a rate **per frame**. **If those frames are Z-slices, the rate is a
fiction, and nothing about the number looks wrong.**

Now shipped in: ``advanced_analysis_ui``, ``brightfield_ui``, ``condensate_physics_ui``,
``invitro_bf_ui``, ``invitro_fluor_ui``, ``timeseries_invitro_fluor_ui`` — **five of which compute
an MSD.**

**313/313 core tests passing.**

## [1.5.517] - 2026-07-13
### SOLVED — and **parallel Numba is back on for macOS**
The complete matrix, each case in its own subprocess on an M2:

| | | result |
|---|---|---|
| **C** | torch + numba on a **worker** | **SEGFAULT** |
| **F** | torch + numba on the **MAIN thread** | **SEGFAULT** — *the thread is irrelevant* |
| **G** | + **``KMP_DUPLICATE_LIB_OK=TRUE``** | **SEGFAULT** — ***the flag PyCAT already sets does NOTHING*** |
| **H** | + **``NUMBA_THREADING_LAYER=workqueue``** | **OK** |

*(and previously: **numba + Qt — OK.** Qt was innocent all along.)*

### Three things this settles
1. **The thread does not matter.** F segfaults too — so **the deferred warm-up (1.5.503) was never
   the fix.** It only moved the crash to **first use, mid-analysis**, which is worse. *The only
   thing protecting macOS users was ``parallel=False``.*
2. **``KMP_DUPLICATE_LIB_OK`` does not work — and PyCAT already sets it.** Exactly as Intel's own
   documentation warns: it makes the duplicate **tolerated, not safe.**
3. **``workqueue`` works.** Numba's own pure-Python thread pool loads **no libomp at all**, so
   there is no second OpenMP runtime to collide with torch's — **and it keeps the parallelism.**

### The fix
- **``NUMBA_THREADING_LAYER=workqueue`` on Darwin**, set in ``run_pycat`` **before the first native
  import** — numba can be pulled in indirectly by cellpose or a napari plugin, so setting it in
  ``numba_utils`` would be **too late**.
- **Parallel Numba is RE-ENABLED on macOS.** *The speed is back.*
- Forcing ``omp`` back on **warns**, rather than letting the user find out via a segfault.
- ``KMP_DUPLICATE_LIB_OK`` is **kept** — useless for numba/torch, but the only mitigation for the
  **other** pair: **MKL's libiomp5 against torch's libomp — the documented Intel-Mac "PyTorch
  segfaults Cellpose" bug.** *Same mechanism, different colliders.*

**313/313 core tests passing.**

## [1.5.516] - 2026-07-13
### The Intel-Mac bug and the Apple-Silicon bug are **THE SAME BUG**
| | collision | victim |
|---|---|---|
| **Intel Mac** | MKL's ``libiomp5`` + torch's ``libomp`` | **cellpose** |
| **Apple Silicon** | torch's ``libomp`` + numba's ``libomp`` | **numba** |

**Two OpenMP runtimes in one macOS process.** They share a symbol table and stomp each other's
thread state, and **whichever library happens to be running when that happens is the one that
dies.**

> ***The victim is incidental. The loader is the bug.***

### The evidence was already in the codebase
- ``run_pycat`` **already sets ``KMP_DUPLICATE_LIB_OK=TRUE``** — a variable that **exists for
  exactly this**, and which **predates Apple Silicon by years. It was created for Intel Macs.**
- It already warns that a cellpose crash *"usually means the installed PyTorch is not compatible
  with this CPU/architecture"* and recommends ``nomkl`` — and **``nomkl``'s entire function is to
  remove a competing OpenMP runtime.** *The README's advice was right, for a reason it did not
  state.*

**And on Intel it can be WORSE** — MKL is present there and absent on arm64, so there can be
**three** runtimes.

### `KMP_DUPLICATE_LIB_OK` does NOT fix it — and may make it worse
Intel's own docs: *"...allow the program to continue to execute, **but that may cause crashes or
silently produce incorrect results**."*

**It does not make two runtimes safe. It makes them TOLERATED.** Without it: a clean, diagnosable
``OMP: Error #15`` abort. With it: the process **continues and segfaults deeper in.**

> ***It converts a diagnosable failure into a mysterious one — and PyCAT sets it, and Meet's
> machine crashed anyway.***

### And the comment in the code said "arm64"
``run_pycat``'s OpenMP block was headed *"On Apple Silicon (arm64)"*. **It is a macOS problem, not
an architecture problem.** *That one word framed three diagnostics around a Qt race that never
existed.*

### `openmp_audit.py`
Counts the OpenMP runtimes **actually loaded** and says **which package brings which** — by walking
the process's dyld images, **and** by finding the dylibs on disk (a ground truth needing no macOS
API). **Two distinct libomp files in one environment is the condition, whatever the CPU.**

**312/312 core tests passing.**

## [1.5.515] - 2026-07-13
### The Apple Silicon crash is **TORCH**, not Qt — and the race I chased never existed
``reproduce_arm64_crash.py`` on Meet's M2, each case in its own subprocess, each recreating the
**original concurrency** (numba compiling on a **worker thread**):

| case | | result |
|---|---|---|
| A | numba on a worker, nothing else | **OK** |
| B | numba on a worker + **Qt** | **OK** — ***Qt is INNOCENT*** |
| **C** | numba on a worker + **torch** | **SEGFAULT** |
| D | + Qt + torch | **SEGFAULT** |
| E | numba on MAIN, after Qt | **OK** |

**torch ships its own libomp**, and two OpenMP runtimes in one arm64 process is a classic way to
die. **The ``OMP: Info #276`` banner in the original crash was torch's** — Numba was the bystander
that happened to be running when it blew up.

> ***The Qt race was a hypothesis I formed from a coincidence, and I ran with it for three
> diagnostics. It was wrong.***

### And it changes WHY 1.5.503 works
``run_pycat`` imports **torch (via cellpose) at line ~294** and starts the warm-up thread at
**~495**. ***That is case C, exactly.***

1.5.503 does two things on Darwin: **defers the warm-up** and **disables parallel Numba**. I
claimed the first was the fix. **The matrix says it is the second** — the crash is inside a
*parallel* kernel.

**Deferring the warm-up alone would only move the compile to first use** — and if torch+numba also
dies on the main thread, that is **worse**: the crash lands **mid-analysis** rather than at startup.

### The likely real fix, documented but NOT enabled
``NUMBA_THREADING_LAYER=workqueue`` — numba's **own pure-Python thread pool**, which **does not
load an external libomp** and therefore **cannot collide with torch's.**

Already known to work on that M2 standalone and with the real kernel. **The torch case is what the
next run tests.** If it passes, it is **strictly better than ``parallel=False``**: the same safety,
and the kernels **actually run in parallel**.

Available today:

```
NUMBA_THREADING_LAYER=workqueue PYCAT_NUMBA_PARALLEL=1 run-pycat
```

***It is not the default yet. I have shipped a fix on an untested hypothesis four times in this
investigation. Not a fifth.***

**312/312 core tests passing.**

## [1.5.514] - 2026-07-13
### `detect_sedimentation` could NEVER see both processes — the branch was unreachable
**Sedimentation mimics coarsening.** Droplets settling into the focal plane make the volume
fraction rise; so does droplet growth. **If you call one the other, a coarsening rate is an
artefact of gravity** — and this function exists to catch exactly that.

The rule was:

```python
sed    = phi_s > 0 and phi_r2 > 0.3 and n_s > 0     # more droplets appear
coarse = r_s   > 0 and r_r2   > 0.3 and n_s < 0     # fewer droplets (they merge)
```

**``n_s`` cannot be both positive and negative.** So ``sed`` and ``coarse`` were **mutually
exclusive by construction**, and the ``'both'`` branch below them was **unreachable.**

**That is not a style point.** When both processes run at once, the droplet count is the **SUM** of
a settling gain and a coalescence loss — **and it can take either sign.**

Measured on a sample with genuine sedimentation **and** genuine coarsening, the old rule called it
**"sedimentation" 98 % of the time**, and its recommendation said *"no sedimentation artefact"*
about the coarsening. ***The opposite of the truth.***

### The physics
| process | what happens | signature |
|---|---|---|
| sedimentation | droplets settle **into** the focal plane | **φ up**, n up, r flat |
| coarsening | droplets merge / Ostwald-ripen | **r up**, n down, φ flat |
| **both** | settling **while** the residents coarsen | **φ up AND r up**, n **either** |

**So φ and r are the discriminators, and n is CORROBORATION** — it strengthens a call, it does not
gate one. The corroboration is now **reported**, so the user can see *why* the call was made.

### Measured
| scene | BEFORE | AFTER |
|---|---|---|
| **both processes** | **`sedimentation` 98 %** | **`both` 85 %** |
| sedimentation alone | 98 % | 98 % |
| coarsening alone | 85 % | 83 % |
| stable | 100 % | 98 % (2 % FP) |

**No loss of specificity.**

### Audited and sound
The ``R² > 0.3`` gate does real work: on a 20-frame series the false-positive rate on a **stable**
sample is **2 %**. *(On a 5-frame series it is 8 % — a slope fitted to five noisy points finds a
trend by chance. That is a property of the data, not of the code, and it is documented.)*

**11 of PyCAT's 13 statistical tests use a permutation null.** The mask/labels and null-construction
classes are **not** systemic — the Costes bug was a one-off.

**312/312 core tests passing.**

## [1.5.513] - 2026-07-13
### The launch now SAYS what it actually did
Three diagnostics in a row drew confident, wrong conclusions from the arm64 crash. ``run_pycat``
now prints, on every launch:

- the warm-up's **thread and timestamp** — *the entire hypothesis is that it runs concurrently with
  Qt, and the only way to know is to make the app say so*
- **which threading layer Numba actually loaded**

**That last one is the crux, not a nicety.** If Numba's default on Apple Silicon is already
``workqueue`` rather than ``omp``, then the ``OMP: Info #276`` banner in the crash came from
**somewhere else** — torch? BLAS? — and *"disable parallel Numba"* fixed a **symptom, not a
cause.** **One line of output decides it.**

### And the probe had a FOURTH bug — corrected
``probe_real_kernel.py`` printed its f-strings **literally**, because the child script was written
with **doubled braces** — the escaping you use when a string goes through ``.format()``. **It never
does.** ***I escaped for a `.format()` that never happens.***

**But Meet's run still proves what it claimed**, and the exit code is why: the child's
``if not NUMBA_PARALLEL: sys.exit(2)`` guard **has no braces**, so it was written correctly and
ran — and the child **exited 0**. **Parallel WAS on.**

The real kernel was tested in parallel on **all seven array shapes** (uint16 camera data,
non-contiguous views, constant images, single row/column), plus **20 repeat calls**, under **both**
threading layers — and **none of it segfaulted.**

*The critique's worst case — "the probe silently tested serial" — is ruled out by the exit code,
not by the broken print.*

**The fixed probe is verified by actually executing the child** — the step skipped in all three
previous versions.

**310/310 core tests passing.**

## [1.5.512] - 2026-07-13
### My diagnostic overclaimed a THIRD time — corrected
``numba_arm64_diag.py`` v3 ran correctly on Meet's M2 and established something real:

| test | result |
|---|---|
| plain ``@njit`` | PASS |
| ``@njit(cache=True)`` | PASS |
| ``@njit(parallel=True)`` | PASS |
| **``@njit(parallel=True, cache=True)``** | **PASS** — *exactly what PyCAT's decorator says* |
| ``workqueue`` layer | PASS |

**So the parallel backend is NOT categorically broken on Apple Silicon.**

**And then it announced "so the launch crash was the Qt race."** *That was an overclaim — the third
in a diagnostic whose entire header is about not overclaiming.*

What it tested was a **64×64 float32 toy**. The real crash was in ``rescale_intensity_fast``, with
real microscopy data, inside a running Qt app with **napari, torch, OpenCV and BLAS all loaded**. A
segfault in a parallel kernel is consistent with **either** an initialisation-order race **or** a
kernel/data/backend-specific native failure the toy did not reproduce.

> **Standalone-parallel-works does not distinguish them.**

**The defensible statement:** parallel Numba works in isolation, so the failure needs the PyCAT
launch context or the real kernel — which makes an initialisation-order interaction the **leading
hypothesis, not a proven diagnosis.**

### `probe_real_kernel.py` — the REAL kernel, in a SUBPROCESS
It runs the **actual** ``rescale_intensity_fast`` on the shapes real data has — **uint16 camera
arrays, non-contiguous views, constant images, 2048×2048, single row/column** — and **20 repeat
calls**, because a crash on call 1 (compilation) and a crash on call 20 (steady state) mean
different things.

**Each case runs in a subprocess**, because **a SIGSEGV kills the process and cannot be caught
in-process.** ``try/except`` will not save you from signal 11 — only a separate process will tell
you it happened.

### And that is a real constraint on the DESIGN, not just the test
**PyCAT cannot auto-detect a working parallel backend by trying it and catching the failure** — the
process would simply die. If parallel is ever enabled *conditionally* on Darwin, the check must be
a **subprocess probe at first launch**. *Anything else is a guard that cannot fire.*

**The safe policy stands, and is now written down:** keep the deferred warm-up permanently;
``PYCAT_NUMBA_PARALLEL=1`` re-enables parallel; ``NUMBA_THREADING_LAYER=workqueue`` is the fallback
if OpenMP specifically is the problem — **and the v3 run shows workqueue works on that machine.**

**309/309 core tests passing.**

## [1.5.511] - 2026-07-13
### SEVEN panels guessed the FRAME INTERVAL — the pixel-size bug, one axis over
**``frame_interval_s = 1.0`` is not an absence of information. It is a claim that the microscope
ran at one frame per second** — and **51 functions default it.**

**This has already cost real time.** VPT's viscosity read **~0.094 Pa·s against an expected ~7**,
and one of the two root causes was exactly this: *the frame interval defaulted while the real
MicroManager metadata said **0.5 s/frame**.*

> **A 5× error in the time axis is a 5× error in every diffusion coefficient** — and nothing about
> the output looks wrong.

``metadata_extract`` **already captures the true interval at load**, and **VPT already reads it.**
Three UIs do. **Seven did not** — they took a spinbox default and reported the answer as physics:

``advanced_analysis`` · ``brightfield`` · ``condensate_physics`` · ``fusion`` · ``invitro_bf`` ·
``invitro_fluor`` · ``data_qc``

All now read the file. **8 spinboxes wired.**

### And the rule VPT gets right, which is preserved
> **A sync that stomps a deliberate choice is worse than no sync at all.**

The user changed it **because they knew something the file did not.** ``sync_spinbox_from_metadata``
never overrides a value the user set — *verified: the file says 0.5, the user chose 0.25, and 0.25
wins.*

### And it returns NaN, not 1.0
*A NaN diffusion coefficient is visibly wrong; a 5× overestimate is not.*

### The complexity ratchet caught MY OWN work
``_add_advanced_analysis`` grew from **659 to 669 lines** when the sync was added — and
``test_nothing_exceeds_the_ABSOLUTE_longest_function`` **failed.**

**That is the ratchet working.** The honest response is to **record that the function is now
bigger**, not to pretend it is not — so the ceiling moved to 672, *with the reason*. It remains
indefensible, and it is exactly why the pixel-size gate's silent failure went unnoticed inside one
of these.

### Audited and CLEAN — the mask/labels confusion is NOT systemic
The touching-condensates bug (a bool mask returned where labels belonged) looked like it might be
a pattern. **It is not.** All three name/return mismatches turned out to be false positives —
``segment_subcellular_objects`` honestly returns masks and says so; ``run_label_binary_mask``
labels a mask, which is what its name says. **A one-off, not a disease.**

**309/309 core tests passing.**

## [1.5.510] - 2026-07-13
### THE REFACTOR — derived from the bugs, not from the line count
Fifteen bugs this session. **Thirteen would have been prevented by a structural change, not by
care.** And the structure is the same one every time:

> **PyCAT passes RAW ARRAYS and RAW FLOATS between layers, and every consumer re-derives what they
> mean.**
>
> **A lazy stack looks like an array. A pixel size looks like a float. A bool mask looks like
> labels. Microns look like pixels.**

*The bugs are not carelessness. They are the absence of types.*

### `require_stack` — because the shape is the thing that LIES
**Four separate bugs** had one shape (VPT, the temperature UI, N&B, SpIDA):

```python
data = np.asarray(layer.data)     # a lazy wrapper returns FRAME 0. Nothing errors.
if data.ndim < 3:
    warn("this layer is 2D")      # ...on a correct time-series.
```

``layer_is_stack`` reads ``.shape``, which the wrapper reports **honestly** — it is only
``__array__`` that truncates. So the question *"is this a movie?"* has a correct answer, and **27
toolbox modules re-derive it by hand from ``.ndim``**, on an array that may already have been
collapsed.

``require_stack`` **raises** instead of handing back one frame. *A test guard is an allow-list that
erodes; a type is a wall.*

### FOUND — the bbox guard could be satisfied by a COMMENT
The 1.5.504 guard asked whether each **module** contained the word ``bbox``. It passed on
``brightfield_tools`` — which mentions it in a docstring — **while ``bf_condensate_metrics``, a
per-condensate results loop inside it, kept no bbox at all.**

I rewrote it to walk the AST, **and it was exactly as weak**: the loop's own body contains a
comment about the bbox, and ``'bbox' in body`` was satisfied by that too. *I only found out by
deleting the real line and watching the "stronger" guard still pass.*

> ***A guard that a comment can satisfy is not a guard.***

It now looks for a real **call** in the AST — and the moment it did, **it found two more**.

### FIXED — the 3D tables were not brushable
``condensate_metrics_3d`` and ``cell_metrics_3d`` **already unpack ``prop.bbox``** — and then throw
it away.

They now emit the **Z extent as the frame** and the **YX extent as the bounding box** — which is
exactly what a crop needs: **which slice, and where in it.** Verified: a z-stack object at slice 6
resolves to ``frame=6, bbox=(11,11,30,30)``.

**306/306 core tests passing.**

## [1.5.509] - 2026-07-13
### The PIXEL-SIZE GATE could vanish silently — and once did
**35 lines installing the gate — including ``add_pixel_size_gate`` itself — were wrapped in
``except Exception: pass``.**

The gate is the thing that tells a user **their lengths are in PIXELS** because the metadata
carried no resolution. If anything in that block threw, **the gate simply never appeared, and the
user got no warning at all.**

**That is not hypothetical.** The gate stopped firing once before (the 1.5.273–278 regression), and
a silent handler is exactly why finding out *why* took a bracketing hunt through git tags.

> ***A guard that can vanish without saying so is not a guard.***

It now logs the failure **and warns the user** that lengths from that panel may be in pixels.

### And it was hiding inside a 400-line function
``_add_reference_frame_selector`` is **398 lines**. ``MenuManager`` is **2,062 lines across 31
methods**. ``ui_modules.py`` is **5,423 lines**.

**Nobody reads a 400-line function.** They skim it — and a ``try/except: pass`` around the one
thing that mattered goes unnoticed.

### So why not split MenuManager? **Because it cannot be verified.**
``ui_modules`` has **~17 % name-coverage** in the test suite, and most of that is ``__init__``.

**A refactor whose only verification is "it still imports" is a refactor that ships bugs** — and
the value of splitting is preventing *future* bugs, while the cost would be **introducing them
today, blind.**

*The honest move is not to rewrite it. It is to stop it growing.*

### A RATCHET instead of a rewrite
**136 functions exceed 120 lines, totalling 27,478 lines — a third of the codebase.** A
per-function allow-list of 136 entries would be noise: nobody reads it, and adding a line to it is
easier than splitting a function, **so it would only ever grow.**

So the budget is the **count itself**, set at today's value:

- **no 137th** unreviewable function
- **nothing longer than 660 lines** (today's worst — *indefensible already, and not a licence to
  write 700*)
- **``ui_modules.py`` may not grow** past 5,600 lines. *When something new belongs in the UI, it
  goes in a **new module**. That is the only way this number comes down.*

**Nothing has to be fixed to make these pass.** They fail only when something gets **worse**.

**307/307 core tests passing.**

## [1.5.508] - 2026-07-13
### A pixel size of EXACTLY 1 is a SENTINEL, not a measurement
``file_io`` writes ``microns_per_pixel_sq = 1`` when the metadata carries no resolution — and it
**says so**: *"Resolution data incomplete, using default value of 1 (um/px)^2"*.

**So a value of exactly 1 is the loader saying "I have no idea", not the microscope saying "one
micron".**

``pixel_size_um`` was returning it as a **legitimate measurement, with no warning** — which is
precisely the failure that module exists to prevent:

> *1 µm/px is a plausible value, not an obviously-wrong one.*

``field_status``'s gate already knew this — ``abs(val - 1.0) > 1e-9`` is its test for a REAL scale.
**The accessor did not.**

A microscope whose pixel really IS 1.000 µm is possible, and such a user confirms it through the
gate, which sets ``pixel_size_confirmed``. **That flag is the one thing that distinguishes "the
user told us it is 1" from "nobody told us anything"** — and it is now honoured.

### The accessor had 2 call sites. The copy-paste had 15.
```python
float(dr.get('microns_per_pixel_sq', 1.0)) ** 0.5
```

That idiom is a **verbatim reimplementation of ``pixel_size_um_or_default`` — minus the warning**
that says the result is in **pixel** units, not microns. It was copy-pasted into **15 places**
across eleven UIs.

***That is how a guard stops guarding: not by being removed, but by being bypassed.***

All 15 now call the accessor, so every UI pixel-size read goes through the one place that warns.

### GUARDED
- a pixel size of exactly 1 must return **NaN** unless confirmed — and a confirmed 1 must be
  believed, or a legitimate microscope becomes unusable
- no ``*_ui.py`` may reimplement the accessor by hand

**304/304 core tests passing.**

## [1.5.507] - 2026-07-13
### My arm64 diagnostic was BROKEN, and it drew a wrong conclusion
``numba_arm64_diag.py`` v1 ran each test with ``python -c "<code>"``. But **``@njit(cache=True)``
cannot cache code that came from a string** — there is no file to write the cache beside — so numba
raised:

```
RuntimeError: cannot cache function 'f': no locator available for file '<string>'
```

**Both** the cached test and the parallel test hit that, **because both used ``cache=True``.**
Neither ever reached the parallel backend.

**The script saw "parallel crashed" and announced that the backend was broken. It had proved
nothing of the sort — it had proved that you cannot cache a string.**

And the failures were **clean Python exceptions, not segfaults.** The launch crash was
``Fatal Python error: Segmentation fault``. ***Those are not the same failure.***

### What Meet's run DOES establish
- **plain ``@njit`` works** on the M2
- **the OpenMP threading layer loads fine on its own** — test 4 launched it, printed ``omp``, and
  **did not crash**
- numpy 1.26.4 / numba 0.65.1 / llvmlite 0.47.0, macOS 26.5 arm64

**That third point is weak evidence for the RACE, not the backend.** OpenMP comes up cleanly when
it comes up **alone**. *The crash needed Qt initialising beside it.*

### The honest position on 1.5.503
It did two things:

1. **Deferring the warm-up on macOS** — almost certainly the real fix. It removes a concurrency
   that ``run_pycat``'s own comment already identifies as fatal on arm64, fifteen lines from the
   crash site.
2. **Disabling the parallel backend on macOS** — justified by a diagnostic **that did not test
   it**, and possibly an unnecessary loss of speed.

**It stays off**, because a segfault at launch is worse than a slower filter, and a wrong default
that is *safe* costs less than one that *crashes*. **But it is a caution, not a finding**, and the
code now says so.

### `numba_arm64_diag.py` v2 — writes real files, and separates a segfault from an exception
It also tests ``parallel=True`` **with and without** ``cache``, and tries the **TBB** and
**workqueue** threading layers — so if OpenMP is the specific problem, a different layer may
restore the speed:

```
NUMBA_THREADING_LAYER=tbb run-pycat
```

**302/302 core tests passing.**

## [1.5.506] - 2026-07-13
### THREE panels reported LENGTHS with no pixel-size gate
**A pixel size of 1 is a CLAIM about the microscope, not an absence of one.**

``microns_per_pixel_sq`` defaults to **1** when the metadata does not carry it — and **1 µm/px is a
plausible value, not an obviously-wrong one.** So a length silently comes out in **pixels, labelled
as microns**, and nothing says so.

``utils/pixel_size.py`` puts it exactly:

> *"A NaN area is visibly wrong; a 1435× overestimate is not."*

**That module exists to guard this, and it has 2 call sites — while 48 sites read the pixel size
raw.** The gate in ``field_status`` is the UI-level backstop, and **three panels that report
lengths did not have it:**

- **``spatial_metrology_ui``** — nearest-neighbour distances, Ripley's L, the pair-correlation
  function. ***Every single output is a length.***
- **``advanced_analysis_ui``** — the main cellular puncta/condensate workflow, reporting areas.
- **``condensate_physics_ui``** — emits ``eta_over_gamma_s_per_um``, **the inverse capillary
  velocity** — *the leg of the chain that gives you γ* — and ``mean_condensate_area_um2``. **Both
  scale with the pixel size.**

All three now carry it.

### And the STRUCTURE that let it happen: **eight names for one quantity**
| name | uses |
|---|---|
| ``microns_per_pixel`` | 45 |
| ``px_size_um`` | 4 |
| **``microns_per_pixel_sq``** | **4** |
| ``microns_per_px`` | 2 |
| ``mpp`` | 2 |
| ``micron_resolution``, ``pixel_size_um``, ``pixel_size_z_um`` | 1 each |

**One of them is SQUARED.** ``microns_per_pixel`` and ``microns_per_pixel_sq`` differ by a square,
and passing the wrong one puts every area out by a factor of the pixel size.

*(Audited: both call sites that convert between them do ``sqrt`` correctly. **No live unit bug** —
but eight names for one physical quantity is how the next one gets in.)*

### GUARDED
A test now fails any ``*_ui.py`` that emits a ``_um`` / ``_um2`` column and lacks the gate, unless
it is on an explicit allow-list of panels that genuinely report no lengths. **If the list grows,
the guard is being eroded by exception.**

**302/302 core tests passing.**

## [1.5.505] - 2026-07-13
### HEALTH AUDIT — three real bugs, and the structure that produced them

### FIXED — N&B told users their time-series was 2D
``nb_tools`` called ``np.asarray(image_layer.data)``. On one of PyCAT's **lazy wrappers that
returns FRAME 0 ONLY** — the ``__array__`` is deliberately truncated so napari's thumbnail request
does not materialise a multi-gigabyte movie.

**Nothing errors. The array simply comes back 2D** — and the very next line is:

```
if data.ndim < 3:  "N&B needs a time-series ... but this layer is 2D"
```

**So a user who loaded a CORRECT time-series was told their data was 2D.** The message was not
merely unhelpful — **it was wrong**, and it sent them off to fix a problem they did not have.
*(And N&B's entire measurement is a variance ACROSS TIME. On one frame that is zero.)*

### FIXED — SpIDA silently analysed frame 0
Same call. SpIDA picks its plane from the viewer's current step — **but the array was already 2D by
the time that choice was made.** The user scrolls to frame 40, runs SpIDA, and **analyses frame 0.**
Nothing errors, and the number looks fine.

### FIXED — the FRAP dead time returned a PLAUSIBLE zero
``compute_lumicks_timelag`` returned **0.0** when it could not read the scan timestamps. **0.0 is a
physically meaningful value**: it says *"the recovery frame was captured the instant the bleach
ended."* That is a **claim**, not an absence of one.

Recovery starts at ``t = lag``, not ``t = 0``, so the fit proceeded on a wrong number — and the
caller **cannot catch what does not raise** (``frap_ui`` wraps it in a ``try`` that never fires).
Now returns **NaN**, which propagates and is visible. *A wrong number that looks right is worse than
no number.*

### REFACTORED — the stack helpers were implemented TWICE
``materialize_stack``, ``iter_frames``, ``layer_is_stack``, ``extract_2d_plane`` and
``warn_if_assumed_axis`` were defined in **both** ``file_io.py`` **and** ``stack_access.py`` —
**byte-identical copies.**

**They agreed, so nothing would catch the day they stopped.** And these are not any five functions:
**they are the functions that FIX the lazy-stack bug** — the one that has now silently collapsed a
movie to frame 0 **four separate times** (VPT 1.5.273, the temperature UI 1.5.253, and N&B and SpIDA
above). *Fixing one copy and missing the other is exactly how that bug survives.*

``stack_access.py`` now owns them; ``file_io.py`` re-exports, so all 25 call sites keep working.

### GUARDED — because it is a missing guard, not a recurring mistake
A test now scans every module for ``np.asarray(layer.data)`` and fails on any site not on an
explicit allow-list of genuinely-2D consumers. **If the list grows, the guard is being eroded by
exception rather than the bug being fixed.**

### Audited and CLEAN
- **0 mutable default arguments**
- **0 bare ``except:``**
- **0 undefined names** across 121 modules and 83,000 lines
- **419 ``except Exception: pass``** — but the dangerous subset (a handler returning a *plausible
  number*) is now **1**, and it is guarded

**301/301 core tests passing.**

## [1.5.504] - 2026-07-13
### FIXED — CI red: three files were edited and never shipped
``test_the_per_object_results_tables_KEEP_the_bbox`` failed in CI. **It was right.**

The bbox sweep (1.5.495) was applied to **eight** modules and **three were left out of the release
bundle**: ``condensate_physics_tools.py``, ``feature_analysis_tools.py``, ``segmentation_tools.py``.

**A file edited in a sandbox and left out of a release is a file that does not exist**, and a test
that reads the *source* rather than the *behaviour* is exactly what catches that.

Without them, those three results tables **could not be brushed** — their rows carry no bounding
box, so a point in a plot built from them **cannot be turned back into an image.** In batch that is
the only route back to the object at all.

### And a guard for the OTHER half of the same mistake
A new test asserts that any module which **calls** a bbox helper also **imports** it — catching a
*half*-applied sweep, which **fails at runtime, not at import**.

**299/299 core tests passing.**

## [1.5.503] - 2026-07-13
### FIXED — PyCAT segfaults at launch on Apple Silicon
Reported on an M2 (Meet / Pratibha). The fault handler is unambiguous:

```
File ".../numba_utils.py", line 192 in rescale_intensity_fast
File ".../numba_utils.py", line 300 in warmup_numba
Fatal Python error: Segmentation fault
```

and immediately before it:

```
OMP: Info #276: omp_set_nested routine deprecated...
```

**That OpenMP banner is the tell.** Line 192 is the first call into a ``@njit(parallel=True)``
kernel — where Numba's parallel backend spins up its **OpenMP runtime**, on a **worker thread**,
**while ``CentralManager(viewer)`` initialises Qt on the main thread.**

### `run_pycat` already documented this exact race — for torch
> *"importing torch on this worker thread **while** Qt/CentralManager initialise on the main
> thread is a known cause of a C-level segfault at launch on arm64 Macs"*

**It was fixed for torch and left in place for Numba.** Two native runtimes (libomp and Qt) coming
up concurrently on arm64 is the same bug **whichever library pulls the trigger.** The warm-up is
now deferred on Darwin, exactly as the torch check is.

### But deferring alone would be a GUESS — so the parallel backend is off on macOS too
**Two things could produce that traceback, and it cannot distinguish them:**

1. **the native-init race** (fixed above), or
2. **the parallel backend itself** — ``parallel=True`` + ``cache=True`` on arm64 loads a threading
   layer *and* a cached object file, and that combination is fragile on macOS ARM.

**If (2) is the real cause, fixing only (1) moves the crash from launch to first use — which is
worse, because it would then happen mid-analysis.**

So ``parallel`` is **off by default on Darwin**. The kernels still JIT (single-threaded Numba is
fine), and **every one has a NumPy fallback besides** — verified: with Numba forced unavailable,
``rescale_intensity_fast`` still returns a correctly rescaled array.

**Parallelism here is a speed-up, not a capability — and it is not worth a segfault.** On the
64×64 warm-up image it buys nothing at all.

``PYCAT_NUMBA_PARALLEL=1`` forces it back on, so **testing whether a newer numba/llvmlite has fixed
it does not require editing the source.** *That is the experiment worth running.*

### And a diagnostic that tells the two causes apart
``numba_arm64_diag.py`` runs each Numba mode **alone, in a fresh process, with no Qt anywhere near
it**:

- **parallel crashes there** → it is the **backend**, and disabling it was necessary
- **everything passes there** → it was the **race**, and the warm-up defer is sufficient
- **even plain ``@njit`` crashes** → the numba/llvmlite/numpy stack is broken on that machine
  (the mixed conda/pip history — numpy 2.4.6 by conda, then 1.26.4 by pip — is the likely cause),
  and PyCAT will still run on the NumPy fallback

**298/298 core tests passing.**

## [1.5.502] - 2026-07-10
### RESOLVED — `topo_n_basins`: the information was not in the envelope
**It was a constant.** A flat field with nothing but noise reported **6.3 basins** — at a noise sd
of 5, 20 and 60 **alike**. It was measuring **how many points of separation ``min_distance`` fit
inside the mask**, and *"we found 7 chromatin domains"* was a statement about the image dimensions.

| field | **BEFORE** | **AFTER** |
|---|---|---|
| **FLAT** (noise 5 / 20 / 60) | **6.3** | **0** |
| 3 peaks | ~6 | **3** |
| 6 peaks | ~6 | **6** |
| 9 peaks | ~6 | **9** |
| 6 peaks, heavy noise | ~6 | **5.8** |
| 6 peaks, dim | ~6 | **5.8** |

### What works: TOPOLOGICAL persistence
**How far does a peak rise above the SADDLE that separates it from a higher peak?** A watershed
**is** a persistence computation — flood downward, and when two basins meet the lower peak **dies**
at that level.

It is **local and scale-free**, and **it cannot be excluded by its own presence** — which is what
killed the global median gate (*real structure raises the median, and the raised median then
excludes the structure*). **Real peaks are ~100× more persistent than noise bumps.**

### THREE gates failed first, and all for the same reason
- a **MAD-derived** threshold — *the MAD grows with the structure* (0.12 flat → 4.6 with six peaks)
- a **fraction of the range** — *a flat field's range IS its noise*
- the **second-largest persistence** as a fraction of the range — 0.37 flat vs 0.14 real, **the
  wrong way round**

> **A flat field's envelope is scale-free noise. Its persistence distribution looks EXACTLY like a
> real field's, only scaled down — and no ratio can separate them, because that is what scale-free
> means.**

Worse, the MAD of the **envelope's** local differences measures **the smoothing**, not the noise:
``range/noise`` came out at **167 on a flat field and 64 on a real one** — *anti-correlated with
structure.*

### The fix: the noise is a property of the RAW IMAGE
**The envelope is a smoothed version, and smoothing destroys the noise by construction.**
``topology_metrics`` **could not answer the question with the information it was given.**

``estimate_image_noise(image)`` is computed on the raw image and passed through. The separation is
then an **order of magnitude**: ``range/noise`` is **0.7** on a flat field, **5.3** on a heavily
noisy real one, **9–13** normally.

**295/295 core tests passing.**

## [1.5.501] - 2026-07-10
### RESOLVED — molecular counting: the corrections now compose
| trace | **BEFORE** (recorded in DEV_NOTES) | **NOW** |
|---|---|---|
| read 15 + pedestal 500 | −24 % *(not better)* | **−8 %** |
| **read 40 + pedestal 800** | **−34 %** *(worse)* | **−17 %** |

### Why they fought each other
The old path estimated the read variance and ``p`` **separately**, combined them into a floor
``s²(1 + p²)``, subtracted it, and fitted ``nu`` through the origin. Each estimate carries its own
error and **they multiply** — ``p`` appears in **both axes** of the regression.

**A free intercept collapses it into one fit**: the line ``y = nu·x + b`` has the noise floor **as**
``b``. Nothing is estimated separately, so nothing multiplies. ``nu`` on the pathological case:
**+21 % → −3 %.**

### But it is NOT universally better — and the tail variance decides
On a **noiseless** trace there IS no floor, and forcing the line through zero is **correct
information**. A free intercept there adds a parameter that soaks up real signal: slope **76.7**
against a true 100, versus **86.7** through the origin.

**The tail variance measures which regime you are in** (0.0 clean; 210 at read sd 15; 1496 at sd 40),
so the fit is chosen **by measurement, not by argument.** *A camera with zero read noise does not
exist — but the rule should not rest on that.*

### I found a "third bug" that was not one, and the test caught me
I changed the numerator from ``y[fast]`` to ``y[0]``, reasoning that ``fast`` rounds of bleaching
have already happened by frame ``fast``. **The reasoning was fine and the change was wrong**:
``_variance_pairs`` builds its pairs starting at frame ``fast``, so the ``nu`` it fits is measured
over that window — and ``y[fast]`` is the signal at the **start of the same window.** *They match.*

| | median N (true 10) |
|---|---|
| **through-origin + `y[fast]`** | **9.97** *(the original, and correct)* |
| through-origin + `y[0]` | 12.17 |

``test_molecule_counting_is_exact_on_a_clean_trace`` **failed immediately** — which is the entire
value of having written it during the audit. *I would otherwise have shipped a regression while
believing I had fixed a bug.*

### And the real lesson: the MEAN was the wrong statistic all along
After the fixes the **mean** N on the worst trace was still **+73 %**. Instrumenting it: the signal
recovers at **998.7 ± 40.7** (true 1000) and the pedestal at **800.0 ± 7.4** (true 800). **Both
inputs are unbiased.**

But ``N = signal / nu`` is a **ratio of two noisy quantities**, and ``E[A/B] ≠ E[A]/E[B]`` —
Jensen's inequality biases the mean **upward**, and a few traces with a near-zero ``nu`` blow it up:

| trace | mean N | **MEDIAN N** |
|---|---|---|
| **read 40 + ped 800** | **183.55** | **10.30** |

**The estimator was sound. The mean was the wrong summary** — and the module's own docstring
already said so: *"the per-trace estimate is inherently noisy... use ``count_molecules_pooled`` for
a population estimate rather than relying on one trace."*

**An entire investigation was spent measuring a statistic the module tells you not to use.**

### Still open
``count_molecules_pooled`` **errors** on stacked-trace input. It is the estimator the user is told
to reach for, and it should be exercised against this simulation. Recorded in DEV_NOTES.

**295/295 core tests passing.**

## [1.5.500] - 2026-07-10
### The Costes test was a **pixel shuffle** — which is not Costes
**83 % false positives on independent channels.**

``perform_costes_test`` called ``scramble_pixels(image1, roi_mask)`` with **no block size**, so it
defaulted to **1: a pure pixel shuffle.**

**Costes's entire defining idea is scrambling in BLOCKS the size of the PSF**, precisely so the
null **keeps** the autocorrelation the optics created and destroys only the *relationship* between
the channels.

**Every blurred image is autocorrelated. That is the optics, not the biology.** Two *completely
independent* channels, blurred realistically, show a non-zero r **by chance** — and a pixel shuffle
destroys exactly the structure the null exists to preserve:

| scene | mean observed r | **FALSE POSITIVES** |
|---|---|---|
| sharp (no PSF) | 0.000 | 0 / 12 |
| **blurred, psf = 3** | −0.040 | **10 / 12 (83 %)** |
| **blurred, psf = 6** | −0.058 | **11 / 12 (92 %)** |

The null came out at **+0.0003 ± 0.0078** while the observed r wandered to **−0.087** — so a
channel pair with a **negative** correlation was being reported as **significantly colocalized, at
p = 0.000.**

***A null that does not reproduce the optics is testing against a world that does not exist.***

**And the correct machinery was already in the same file**: ``spatial_null_test`` measures the
correlation length and block-shuffles at twice it. That is now what Costes uses.

**After: 0/12 false positives at psf=3, and power is 10/10 at r=0.3 and r=0.6.**

### The residual is a REAL LIMIT, and it is reported rather than hidden
The block size **must** be the PSF scale. But on a small, heavily-blurred image the block becomes a
large fraction of the field, and **there are too few independent blocks to build a null from:**

| image | block / field | FALSE POSITIVES |
|---|---|---|
| **128 px** | **17 %** | **4 / 10** |
| 256 px | 9 % | **0 / 10** |
| 512 px | 5 % | 1 / 10 |

**A finite-size effect, not a bug.** A warning now fires when fewer than ~50 independent blocks are
available, saying the p-value is liberal there.

### Audited and exact — Pearson and Spearman
Within **1.3 %** of a known correlation at every level, and **precisely 1.0** on identical images.

**295/295 core tests passing.**

## [1.5.499] - 2026-07-10
### Autopopulation wired into the UI — through **one funnel**, and it never overrides a choice
Every layer dropdown in PyCAT goes through ``update_dropdown_items``. **That is where the wiring
belongs** — not at twenty call sites, which is the fragile pattern that has already produced two
sweeps this session.

``create_layer_dropdown(layer_type, name_hint='', **binding=''**)``.

### The rule is absolute: **a restored selection wins, always**
This function's own docstring records the bug: dropdowns silently resetting to the first layer, so
a batch config captured *"Segmentation Image"* instead of the user's *"Upscaled Segmentation
Image"*.

**That is exactly the bug autopopulation could reintroduce.** So it only ever fills a dropdown that
is **empty** — it never touches a decision the user already made.

### `binding` is the strong version of `name_hint`
``name_hint='Labeled Cell Mask'`` matches a **substring of a layer NAME**. It works until someone
renames a layer, or a new operation produces a name containing the same substring — **and then it
silently selects the wrong one. It is matching a label, not a fact.**

A binding matches what the layer **IS** (``role=labels, target=cell``), and **survives renaming,
reordering, and a user who calls their mask "asdf".**

### FOUND — a `likely` match that selects NOTHING is the worst outcome
A first version only auto-selected on ``certain``. So a binding with ``prefer='newest'`` — **which
is most of them** — resolved to ``likely``, **selected nothing, and said nothing.** The dropdown
sat empty **while the resolver knew perfectly well which layer was wanted.**

*That is worse than either alternative: it is the feature **silently not working.***

A ``likely`` match is now selected, **and the tooltip says it was inferred and asks the user to
check.** They get a filled dropdown *and* the information to catch it when it is wrong.

**``ambiguous`` still selects nothing**, and that remains correct — there the resolver genuinely
does not know, and *a wrong auto-selection the user does not notice is worse than an empty
dropdown.*

**288/288 core tests passing.**

## [1.5.498] - 2026-07-10
### Autopopulation — the payoff the tag vocabulary was built for
Every workflow step in PyCAT has a layer dropdown, and **the user fills every one of them by hand,
in every step, on every run.** ``field_status`` tracks *whether* a dropdown is filled; **nothing
fills it.**

Now a step **declares what it needs**, in tags, and the resolver finds it:

| what the step wants | the query |
|---|---|
| the raw image | ``role=image, provenance=raw`` |
| the cell labels | ``role=labels, target=cell`` |
| the CLAHE result | ``op=clahe`` |
| the most recent mask | ``role=mask, prefer=newest`` |

### The thing that must not happen
**A wrong auto-selection the user does not notice is worse than an empty dropdown.** They run the
analysis on the wrong layer, get a number, and **never know.**

So the resolver returns a **confidence** and a **reason**:

| confidence | meaning | what the UI does |
|---|---|---|
| **certain** | exactly one layer matches | auto-select it |
| **likely** | several match, one is clearly best | pre-select, **and say so** |
| **ambiguous** | several match, no clear winner | **do not choose** — name them |
| **none** | nothing matches | say what was looked for |

*This is the same principle as the brushing refusal and the same principle as "an absent tag is
honest; a guessed one is a lie". **It keeps coming up because it is the same mistake wearing
different clothes.***

### The binding table is DATA, not code
``layer_bindings.json`` — **14 bindings**, and **3 are deliberately ambiguous.** The
colocalization channels are the clearest case: with two channel masks present, choosing one is a
**coin flip**, and *a colocalization run on the wrong pairing gives a number that looks fine.*
**Leaving ``prefer`` out is how that is said.**

### FOUND — the registry knew the target, and the hook was throwing it away
The registry declares that ``cellpose`` produces **cells** and ``bead_detect`` produces **beads**.
The hook tagged the ``op`` and **dropped the target** — so a step asking for *"the cell labels"*
(``role=labels, target=cell``) found **nothing**, with a Cellpose layer sitting right there.

**The information existed. It was not being carried the last inch.**

### FOUND — an empty lineage graph is not evidence of anything
``prefer='head_of_lineage'`` looked for layers with no lineage **edge**, and returned the most
recently added **derived** layer as "the source" — because **no edges exist at all.** The hook
cannot record a parent: by the time a UI calls ``add_image(result)``, the transform that made
``result`` has already returned.

**But the hook knows something better** — whether a layer was the first image into an empty viewer
(``provenance='raw'``). That answers the question **with certainty** rather than inferring it from
an absence.

*An empty graph is not evidence that a layer is a source. It is evidence that nobody recorded the
lineage.*

### And the binding table would NOT have shipped
It was not in ``package-data``. The resolver falls back to an empty table when the JSON cannot be
loaded — so **every dropdown would have silently stopped autopopulating in the installed package,
while working perfectly in the repo.** *That is the worst kind of bug: it cannot be reproduced by
the person who wrote it.* Fixed and guarded.

**285/285 core tests passing.**

## [1.5.497] - 2026-07-10
### Three plotting backends, addressable the same way — and the difference is **not cosmetic**
Brushing needs three things from a plot: an **artist** whose elements map 1:1 to the rows, a
**pick event** that reports *which* element was clicked, and somewhere to hang the **ObjectRefs**.

| backend | how a click reports a row | what it costs |
|---|---|---|
| **matplotlib** | ``mpl_connect('pick_event')`` → ``ind`` | nothing |
| **seaborn** | **it IS matplotlib** — same canvas, same event | nothing; the artist must be found inside the Axes |
| **plotly** | a **JavaScript** callback in a browser | **a Python↔JS bridge** |

### The one thing every backend must get right
**Do the artist's points still correspond, IN ORDER, to the DataFrame's rows?**

If a library reorders, groups or drops rows while drawing, then *"point 3"* is not *"row 3"* — and
a click resolves to **the wrong object, lands, and says nothing.**

**That is not hypothetical.** PyCAT's own ``plot_focus_diagnostic`` groups by interpretation and
draws each group as a separate artist; a naive index map there **would** have pointed at the wrong
condensate.

So the order is **verified at wire time, not assumed** — and when it cannot be trusted, **brushing
is refused.** Tested with a deliberately shuffled artist: **caught.**

### Seaborn was nearly free — and its safety was VERIFIED, not assumed
Seaborn is a matplotlib front end: the same canvas, the same pick event. The real question was
whether ``hue`` **splits the data into one artist per level** — in which case an index into one
artist is an index into a **subset**, not into the table.

**Tested: modern seaborn keeps one collection, in DataFrame order.** And the test is what keeps
that true — *if a future seaborn changes, it fails there rather than silently pointing every click
at the wrong object.*

### Plotly is integrated HONESTLY
A click inside a plotly figure lives in **JavaScript**. Reaching napari needs a
``QWebEngineView`` + ``QWebChannel`` — **a heavy dependency (~100 MB) and a real Qt risk in an app
that already has a user hitting OpenGL/Qt rendering failures.**

So the identity goes where it works **with no bridge at all: the hover.** The user moves the mouse
over a point and sees **which object it is** — its label, its frame, the file it came from.
**That is most of the value of brushing, and it costs nothing.**

And when the click genuinely is not available, **the widget says so** — rather than doing nothing.
*Silence is the failure mode that makes people think a feature is broken.*

``plotly`` and ``plotly-interactive`` are **optional** extras; the backend selector only offers
backends that are **actually importable.** *An option that silently fails is worse than one that
is not there.*

### And the plotly path is FLAGGED AS UNVERIFIED, because it is
**The sandbox has no network, so plotly could not be installed and the figure could never actually
be built and inspected.** The matplotlib and seaborn paths **were** verified end-to-end.

*"It should work" is not "it was run."* The docstring says so, and
``test_the_plotly_hover_carries_the_object_identity`` **skips until plotly is installed and
exercises it the moment it is.** **The first thing to do with that path is run it.**

**278/278 core tests passing** (plotly's test skipping, honestly).

## [1.5.496] - 2026-07-10
### The `PlottingWidget` is the natural wiring point — and **"wire the 13 plots" was the wrong goal**
Gable: *"the plotting widget should also wire things naturally."* It does, and it is the **better**
place — but working out why took discarding the plan.

### Almost none of the analysis plots have points that ARE objects
| plot | what a point IS |
|---|---|
| `plot_msd_trajectories` | a line **is a track** → *brushable, and already was* |
| `plot_moduli` | a **frequency** |
| `plot_frap_recovery` | a **timepoint** |
| `plot_coarsening` | a **moment** |
| `plot_molecular_counting` | a **variance bin** |
| `plot_km_survival` | a **survival time**, aggregated |
| `plot_enrichment_distribution` | a bar is a **bin** — it holds twelve condensates |
| `plot_spatial_metrology` | every panel is a **curve**; a point on a Ripley plot is a **radius** |
| `plot_distributions` | a bar is a **bin** |

**There is no object behind any of them**, and making them pickable would be a **lie**: the user
clicks expecting an image and gets **whichever row happened to sit at that index.**

### The brushable view of per-object data is a SCATTER — and that is what the widget builds
``PlottingWidget`` lets the user pick **any** results DataFrame and **any** two columns. When a row
is one object — which every per-object table now is (1.5.495) — **each point IS an object.**

**One wiring point, covering every per-object table**, instead of fifteen fixed figures. And a
click resolves in **both worlds**: the object is revealed in napari if a session is live, or its
region is **read straight out of the source file** if the table came from a batch CSV and the
session is long gone.

**The widget declines silently when a row is an aggregate.** The tell is the **bbox**: a row that
can be located in an image has one; a row that averages forty objects cannot. *A click that lands
on the wrong object is worse than a click that does nothing — it lands, and nothing says so.*

### And I got the triage wrong once, in the safe direction
A first pass excluded ``plot_focus_diagnostic`` as "ensemble". **It is not.** It is a QC scatter
where **each point is one image/field**, and the thing a user wants when they click a
blurry-looking point is *that field*. It is correctly brushed.

**The distinction is not "curve vs scatter". It is: does one point correspond to one thing you
could show?** A FRAP timepoint does not. **A QC point does.**

**273/273 core tests passing.**

## [1.5.495] - 2026-07-10
### The bbox now travels with the results tables — **without it, nothing is brushable**
**25 files call ``regionprops``. One kept the bbox.** Every results table that discarded it is a
table whose rows **cannot be turned back into an image**.

Swept:
- **``feature_analysis_tools``** — the **main cell and puncta tables**, the ones a user actually
  plots. These go through ``regionprops_table``, not a loop, so ``'bbox'`` is added to the
  properties tuple.
- **``segmentation_tools``** — the puncta table.
- **``dynamic_spatial_tools``** — the per-frame object table that feeds **every tracking plot**.
- **``condensate_physics_tools``** — the per-cell partition table.

skimage expands ``'bbox'`` into ``bbox-0..bbox-3`` — hyphenated names that are awkward in a
DataFrame (``df.bbox-0`` is a *subtraction*). ``normalise_bbox_columns`` renames them once, where
they are produced, to the ``bbox_y0..bbox_x1`` an ``ObjectRef`` reads.

### 8 plots made brushable. **5 deliberately not.**
Of the 13 unpickable plots, **only 8 have points that ARE objects.** The other five draw **ensemble
curves**:

| plot | what a point is |
|---|---|
| `plot_frap_recovery` | a **timepoint** |
| `plot_coarsening` | a **moment** |
| `plot_fusion_relaxation` | a **moment** |
| `plot_molecular_counting` | a **variance bin** |
| `plot_phase_diagram` | a point on a **boundary** |

**There is no object behind them, and making them pickable would be a lie** — the user would click
expecting an image and get whichever row happened to sit at that index.

### FOUND — a grouped scatter needs PER-GROUP refs, or it opens the WRONG OBJECT
``plot_focus_diagnostic`` draws its scatter **per group** (bright / sharp-dim / blurry-dim), and
**matplotlib reports the index within the picked artist.** Each group is its own artist.

**A single flat list of refs is therefore silently mis-indexed**: clicking the *third green point*
resolves to the *third row of the whole table* — **a different object.** The click would open an
image, **the image would look plausible, and it would be the wrong one.**

Measured on a real grouped scatter: the click resolves to **object 4** with per-group refs, and
would have given **object 1** with a flat list. Guarded by
``test_a_grouped_scatter_needs_PER_GROUP_refs_or_it_resolves_to_the_WRONG_OBJECT``.

### `add_brushing` — one call, and a plot is done
```python
points = ax.scatter(df.area_um2, df.partition_coeff, picker=5)
add_brushing(fig, points, df, source_path=path, viewer=viewer)
```

Reveal-in-viewer when a session is live; **a cropped thumbnail out of the source file when it is
not**; propagation to any other view on the hub.

**269/269 core tests passing.**

## [1.5.495] - 2026-07-10
### The bbox sweep — **15 of 18 results tables could not be brushed at all**
Brushing (1.5.494) only works if the row can find its object. **It could not**, in almost every
table PyCAT produces: ``regionprops`` hands over ``prop.bbox`` free, and **24 of the 25 call sites
were throwing it away.**

**A row without a bbox cannot be turned back into an image.** Interactively that is an annoyance.
**In batch it is fatal** — the layer is gone, and the bbox is the *only* route back to the object.

Swept into every **per-object** results table:
``spatial_metrology_tools``, ``label_and_mask_tools`` (including the new ``neck_geometry`` and
``assess_and_split_touching``), ``brightfield_tools``, ``dynamic_spatial_tools``,
``morphological_complexity_tools``, ``invitro_tools``.

**Per-FRAME and per-CELL aggregates are deliberately excluded**: a row that summarises forty
objects **has no single object to point at**, and giving it a bbox would be a lie.

### FOUND — a ref was pointing at the wrong object, and the click would have LANDED
``ObjectRef.from_row`` listed ``cell_label`` as a fallback for ``object_id``. On a puncta table —
whose column is ``punctum_label`` — **that fallback fired**:

> **Four different puncta all came back as object 1**, because they all live in cell 1.

**A ref that points at the wrong object is worse than one that points at nothing.** The click
lands, on the wrong thing, and **nothing says so.**

The object's own identity and its parent's are **different questions**, and they now get different
fields (``object_id`` and ``parent_id``). Verified: four puncta → four distinct ids, all correctly
recording ``parent_id = 1``.

### Guarded
- ``test_a_ref_points_at_the_OBJECT_and_not_at_its_PARENT``
- ``test_the_per_object_results_tables_KEEP_the_bbox`` — reads the source, so **a new per-object
  table that forgets the bbox is caught at the moment it is written.**

**271/271 core tests passing.**

## [1.5.494] - 2026-07-10
### Brushing — **plot → object → image**, and it works in batch where the session is gone
Gable asked for two things, and **the second one is what forces the design**:

> *"extensible so that as I write more plots this can be extended easily to them"*
>
> *"batch a data set and select points in the resulting plot and see the data and bounded images"*

**A point in a batch plot points at an object in an image that is not loaded**, produced by a
segmentation that is not in memory. *"Highlight the layer"* is **not available.**

So the identity a point carries cannot be a live reference. It has to be **serialisable**:

| | |
|---|---|
| ``source_path`` | **which file** |
| ``frame`` | **which frame / z-slice** |
| ``object_id`` | **which object** — the label value in that frame's mask |
| ``bbox`` | **where it is** — ``(y0, x0, y1, x1)`` |
| ``tags`` | **what produced it** — from the operation registry |

### The bbox is the piece that makes it work
With it, a batch plot reads the object's region **straight out of the file** — **no reload of the
full stack, and no re-segmentation.** Without it, the only route back to the object is to redo the
analysis.

**And it is free.** ``regionprops`` hands over ``prop.bbox`` at every segmentation site. **25 files
call regionprops; ONE keeps the bbox.** *It is being discarded everywhere.*

Verified end-to-end: a batch of files, segmented, the session discarded — then a point is clicked
and **the crop comes back with the right object's peak intensity, matching the table.**

### What already existed, and what was welded shut
A complete three-way brushing hub is **already in ``vpt_ui``** — plot ↔ image ↔ table, keyed on
``track_id``, with a **re-entrancy guard** (*without it, a click oscillates*). The design was
right. **It was welded to VPT**: 2 of PyCAT's 15 plots are pickable; the other 13 are pictures.

Lifted out, keyed on an **``ObjectRef``** instead of a ``track_id`` — so it works for a condensate,
a punctum, a cell or a bead **without knowing which**.

### A new plot becomes brushable in ONE line
```python
points = ax.scatter(df.area_um2, df.partition_coeff, picker=5)
make_pickable(fig, points, refs_from_dataframe(df, source_path=path))
```

**No hub edit, no registration, no callback plumbing.** The plot supplies the identity behind its
points and gets **plot→image brushing, batch crops, and hub propagation** for free.

### Interactive and batch are the SAME mechanism
The plot **does not know which world it is in.** It hands over an ``ObjectRef``; the resolver
decides:

- **live session** → the ref finds the layer, and the viewer reveals the object
- **batch** → the ref finds the **file and the crop**, and a thumbnail of that object is shown

*That is what makes one implementation serve both.*

### And a point that cannot be resolved says WHY
*"Nothing happened"* is the worst possible answer to a click. A ref with a ``track_id`` and no
bbox works interactively and **cannot** work in batch — and the message says so, **and says that
``regionprops`` provides the bbox free.**

**267/267 core tests passing.**

## [1.5.493] - 2026-07-10
### Every layer is tagged, and **no call site can forget**
**116 ``viewer.add_*`` call sites. 2 of them tagged anything.**

Editing the other 114 by hand is a one-off sweep that decays the moment someone adds the 117th —
**and the 117th is exactly the one that will be forgotten**, because nobody adding a layer is
thinking about the tag system.

So the interception happens **once, at the viewer** (``layer_tag_hook.install``). **A new call
site is tagged automatically, because it does not know it is being tagged.**

### What can be known, and what cannot — the guarantee is deliberately asymmetric
| source | reliability |
|---|---|
| **The caller** — a function on the stack carrying ``__pycat_op__`` | **Definitional.** But it usually will not fire: UI code calls the transform and *then* adds the layer, so the decorated function has already returned. |
| **The layer name** — ``'CLAHE'``, ``'Cellpose labels'`` | **A heuristic**, and treated as one (``source='inferred'``). Only ~10 names are literals in the source; the rest are built at runtime. |
| **The data** — an integer array with max ≤ 1 is a *mask*; more values are *labels* | **Always knowable, with certainty.** |

- **``role`` is ALWAYS set.** This is what makes *"where is the mask?"* answerable for **every**
  layer in the viewer.
- **``op`` is set only when it is KNOWN.** **An absent tag is honest; a guessed one is a lie that
  will be queried as truth.**

A layer with a role and no op is still queryable. **A layer with nothing is invisible.**

### FOUND — I built the exact degeneracy the registry exists to prevent, **into the registry**
``tag_registry`` declared its own ``ROLES`` — ``'raw'``, ``'preprocessed'``, ``'measurement'`` —
and ``layer_tags.CORE_VALUES['role']`` is a **different set**. **Every tag the hook wrote was
rejected by the validator**, silently, in the debug log, *while the tag system appeared to work.*

Then the hook did the same thing again, independently.

**The roles are now IMPORTED, not redeclared.** There is **one** vocabulary. If a new kind of
layer genuinely exists, it is added in **one** place and everything downstream sees it.

*(And ``'raw'`` vs ``'derived'`` is what **provenance** already carries — duplicating it as a role
is what produced the collision.)* Guarded by
``test_there_is_ONE_role_vocabulary_and_the_registry_does_not_invent_a_second``.

### `layer_tags` extended, not replaced
- **``role``** gains ``labels``, ``overlay``, ``reference`` — the original set could not
  distinguish a **mask** (binary) from **labels** (many objects), and had nowhere to put an
  overlay or a dark-frame reference. **A layer whose kind cannot be expressed is a layer that
  cannot be found.**
- **``op``, ``target``, ``layer_type``** are new core keys.
- **``op`` is validated against the REGISTRY**, not a set in the file — because **the vocabulary
  of operations IS the set of functions that exist**, and a hand-maintained list would drift from
  them the first time someone adds a filter. **An unregistered op is refused.**

### CLAHE is a UI action, and a sweep of the toolbox misses it
``_add_run_clahe`` calls ``skimage.exposure.equalize_adapthist`` **directly** — there is no
toolbox function to decorate. **So do all the napari-native operations.**

**16 UI-only operations registered**, including exactly what Gable asked for:

- **``clahe``** — the one he named
- **``mask_merge``, ``multi_merge``, ``two_layer_merge``** — *the merges*
- **``expand_labels``, ``relabel``** — *changes* a layer
- **``hand_drawn``, ``hand_painted``** — **napari-native user actions**

> **An untagged hand-drawn ROI is indistinguishable from a computed mask** — which is exactly the
> confusion the tag system exists to remove.

**79 operations in the vocabulary. 262/262 core tests passing.**

## [1.5.492] - 2026-07-10
### PINNED — two audit findings that change numbers in existing data
Written up in ``docs/audits/DEV_NOTES.md`` with enough detail to pick up cold: what was wrong,
what it affects, how to tell if a dataset is hit, and what to re-run.

1. **Touching condensates were always counted as one** (1.5.482). Affects counts, **size
   distributions** (a merged pair reads as one large object — this shifts the mean up and inflates
   the tail), every per-object measurement, and any coarsening exponent read from a size
   distribution.
2. **``ccf_sigma`` was a 13× underestimate** of the correlation length (1.5.481) — it reported the
   std of the correlation *values*, not the peak width, and **would have been 0.33 for any
   structure size**. *This is a false-negative generator*: a comparison between conditions would
   have shown **no difference where a real one existed.* The CCF **peak position** (chromatic
   shift) was always correct.

*(The VPT linker gap is deliberately NOT pinned — the automated linkers are a backup to TrackMate,
and the corrected default has achieved sufficient similarity.)*

### NEW — the tag vocabulary. **Non-degenerate by construction.**
**91 of 116 layer creations were in files that never tagged anything.**

Hand-tagging them would produce a vocabulary that **drifts**: someone writes ``'clahe'``, someone
else ``'CLAHE'``, a third ``'contrast_limited_ahe'`` — **and the tag becomes unqueryable, which is
the one thing it exists to be.**

So the tag is declared **on the function that performs the operation**, not at the call site:

```python
@tags_layer('log', role='preprocessed',
            summary='Laplacian-of-Gaussian filter',
            aliases=('laplacian_of_gaussian',))
def apply_laplace_of_gauss_filter(image, ...):
```

- **It cannot be forgotten** — the tag travels with the code, not with the 116 callers.
- **It cannot collide** — a duplicate name raises ``TagCollision`` **at import time**. *A silent
  collision would be worse than an error: a query for ``'watershed'`` would return a mixture of two
  operations, and the tag system would look like it works.*
- **It cannot drift** — the vocabulary **is** the set of functions that exist.
- **A new transform that forgets the decorator is caught by a test.**

**63 operations registered across 12 modules**, and they read the way Gable asked: **`log`, `dog`,
`gabor`, `clahe`, `bilateral`, `rolling_ball`, `wbns`, `invert`, `watershed`, `cellpose`,
`otsu`** — *names, not descriptions*. The 2D/3D pairs are properly distinguished (``dog`` /
``dog_3d``), which is the non-degeneracy doing its job.

### Four keys, because one string cannot answer four questions
| key | answers |
|---|---|
| ``role`` | *What KIND of layer is this?* — raw, preprocessed, mask, labels, overlay, measurement, reference |
| ``op`` | *What was DONE to it?* — the 63 operations |
| ``target`` | *What is it OF?* — condensate, cell, nucleus, punctum, bead, fibril… |
| ``parent`` | *Where did it come FROM?* — a lineage edge |

**An unregistered tag is REFUSED, not written.** A tag outside the vocabulary is a degenerate tag:
it cannot be queried, nothing will ever match it, and writing it lets it **rot in the data**.

### And a plot can carry the tags of the data behind it
> *"a plot that is generated should probably have tags if possible"*

``attach_plot_tags(figure, source_layers, plot_of='msd', track_id_column='track_id')``.

A figure is not a napari layer, so it cannot carry layer tags — **but it can carry the same
dictionary**, and **that is what makes brushing possible**: a point in an MSD plot that knows its
``track_id`` and the layer it came from **can be clicked back to the object.**

*The identity plumbing has to exist before the interaction can be built. This is that plumbing.*

**259/259 core tests passing.**

## [1.5.491] - 2026-07-10
### Validated the neck/elastocapillary module against the literature — and against published data
Full write-up: **``docs/validation/neck_geometry_and_elastocapillarity.md``**

### VALIDATION 1 — the Laplace pressure at the neck reproduces **Pawar et al. (2011)**
**Pawar, Caggioni, Ergun, Hartel & Spicer**, *Soft Matter* **7**, 7710–7716 (2011),
DOI 10.1039/c1sm05457k — *"Arrested coalescence in Pickering emulsions"*.

> *"their complete fusion into a single spherical drop can sometimes be arrested in an intermediate
> shape **if a rheological resistance offsets the Laplace pressure driving force**."*

Their **eqn (6)**: ``ΔP = 2γ/R_droplet − (γ/R₁ − γ/R₂)``, with R₁ the cross-sectional radius and R₂
the neck radius — **the two principal radii of a saddle, of opposite sign.** *That is exactly the
object ``neck_geometry`` measures.*

They publish two arrested doublets with full geometry and the ΔP they computed. Recomputing from
their own numbers:

| case | R_droplet | R₁ | R₂ | **their ΔP** | **implied γ** |
|---|---|---|---|---|---|
| Fig 5(b.3) | 100 µm | 48 µm | 73 µm | 6.81 × 10² Pa | **0.0529 N/m** |
| Fig 5(c.3) | 94 µm | 94 µm | ∞ | 5.63 × 10² Pa | **0.0529 N/m** |

**Two independent geometries give the identical implied interfacial tension.** The form of the
equation is confirmed exactly. Guarded by
``test_the_neck_laplace_pressure_reproduces_PAWAR_2011``.

### VALIDATION 2 — is the method even in the accessible regime?
**Condensate γ = 0.1–100 µN/m** (Jawerth 2018, PGL-3: 1–5 µN/m; Alshareedah, Thurston & Banerjee
2021). **Condensate G′ = ~0.1 Pa (liquid-like) to ~1 kPa (aged).**

``L_ec = γ/G`` falls inside the **0.3–10 µm** light-microscopy window for **G ≈ 0.1–100 Pa** —
**precisely the aged / maturing / disease-associated regime.**

And **both failure modes are informative, not failures**:
- **A true liquid (G → 0)**: L_ec → ∞, nothing arrests → *"all round → L_ec **bounded below** by
  the largest condensate"* — **a soft material.**
- **A hard solid (G ~ 1 kPa)**: L_ec ≈ 0.0001–0.1 µm, **below the diffraction limit**; everything
  is arrested → *"all irregular → L_ec **bounded above** by the smallest"* — **a stiff material.**

**Both land in the bounded case the code already handles.**

### The three-regime structure is exactly what the module reports
> *"**If surface energy dominates, the drops will completely coalesce.** **If elastic energy
> dominates, the droplets are unable to even initiate coalescence.** **Arrest occurs when
> coalescence can begin but not complete.**"*
> — Dahiya, Caggioni, Spicer et al., *Phil. Trans. R. Soc. A* (2016)

### Honest limitations, recorded
1. **Validated against synthetic geometry and published *numbers* — not against a condensate
   dataset with an independently-measured G.** The next step is real: a preparation whose G is
   known from micropipette aspiration, and check the size crossover lands where γ/G says.
2. **The intensity witness does not discriminate** (0.42–0.46 of the body median for a genuine
   neck *and* an arrested one alike) **and is not given a vote.**
3. **2D projections of 3D objects** — a pair whose axis is out of plane reads a smaller apparent
   neck. Not handled.
4. **The 0.6 neck threshold is physically motivated but not experimentally calibrated** on
   condensates.

**251/251 core tests passing.**

## [1.5.490] - 2026-07-10
### The neck carries the physics — `neck_geometry` and `fit_elastocapillary_length`
Gable asked whether the tangents at the neck say something about surface tension. **They do**, and
the answer runs further than expected.

### The geometry, and it is exact
For two spheres of radius R with centres separated by d, the neck radius is
``r_n = sqrt(R² − (d/2)²)`` and the half-angle satisfies **``sin(α) = r_n / R``** — so the
**dihedral angle** between the two surfaces is ``2α``, and it falls **straight out of the mask**.
Measured ``r_n/R`` reproduces the exact relation to within a few percent, and the dihedral angle
to **within 3°**.

### The elastocapillary length — γ/G from a single image
Gable's objection was the key: **small objects are essentially all surface.** That is not a
limitation — **it IS the measurement.**

Elastic energy scales with **volume** (``G·strain²·R³``); capillary energy with **surface**
(``γ·strain·R²``). Their ratio is **R / L_ec**, where **L_ec = γ/G**. So:

**A droplet smaller than L_ec is capillary-dominated and rounds up whatever the modulus is. It is
not big enough to hold a shape.**

**Therefore the size at which condensates stop being round IS the elastocapillary length**, and
every condensate in a field is a bounded observation:

- arrested at radius R → **R > L_ec** → **G > γ/R** *(a lower bound)*
- rounded up at radius R → **R < L_ec** → **G < γ/R** *(an upper bound)*

Fitting the *fraction irregular* against log R gives a sigmoid whose **midpoint is L_ec**.
Validated on 400-condensate populations spanning 0.3–10 µm:

| TRUE L_ec | fitted | 95 % CI |
|---|---|---|
| 0.80 µm | **0.79** | ± 0.07 |
| 2.00 µm | **1.97** | ± 0.28 |
| 5.00 µm | **4.92** | ± 0.74 |

**Recovered to within 2 % across a 6× range, with a real confidence interval — from one image, no
time series, no calibration.**

### And it closes a chain PyCAT already has
| measurement | gives |
|---|---|
| **VPT** | **η** |
| **fusion relaxation** | **η/γ** → γ |
| **this** | **γ/G** → **G** |

**An absolute elastic modulus from three measurements the software already makes.**

### What a single frame CANNOT give, said plainly
**γ, η and G separately.** A snapshot gives ``r_n/R``, which for a Newtonian liquid is a function
of ``t/τ_v`` with ``τ_v = ηR/γ`` — the capillary time. **One frame gives ratios, not absolute
moduli**, and the docstring says so.

### Two limits, and they are different
- **PHYSICS**: a droplet below L_ec cannot be arrested. Reading *"no arrest"* on a 0.3 µm punctum
  as *"liquid"* is **reading the size, not the material**. For a soft condensate (γ ~ 1e-6 N/m,
  G ~ 1 Pa) **L_ec ~ 1 µm** — most small puncta are *physically incapable* of showing arrest.
  Reported as ``size_sufficient``.
- **MEASUREMENT**: the lobe residual of a *perfect* sphere pair is **0.037 at R = 8 px** against
  **0.005 at R = 60 px**. Below ~15 px the pixelation floor swamps the elastic signal even where
  the physics would allow it. Reported as ``pixelation_limited``.

### And the lobe residual reads elasticity, not viscosity
**A merely slow pair keeps spherical lobes** — surface tension is the only stress on a free
surface, however viscous the interior. **An elastic network can hold it out of round.** Measured:
**0.0095** at G/γ = 0, rising monotonically to **0.0291** at G/γ = 2.

*(An all-round or all-irregular population yields no fit, and says so: L_ec is then **bounded**
outside the observed size range — which is still information, and reporting a fitted number from
it would be inventing a transition that was never seen.)*

**249/249 core tests passing.**

## [1.5.489] - 2026-07-10
### RETRACTED — the temperature defaults. **My simulation was wrong, not the pipeline.**
1.5.488 changed the cloud-point defaults from ``entropy_corrected`` + ``baseline`` to
``focus_score`` + ``midpoint``, on the strength of a simulation showing entropy returning the
start of the ramp.

**The simulation was wrong.** Every scene gave the "clear" sample an intensity spread of sd = 15,
which **already fills the histogram** — entropy started at **7.1 out of a theoretical maximum of
8.0** and had nowhere to rise:

| sample | entropy |
|---|---|
| CLEAR, tiny noise (sd 2) | **7.189** |
| TURBID, strong scatter (sd 120) | **6.948** |

``entropy_turbidity_curve`` bins each frame against its **own** intensity range, and a Gaussian
binned to its own spread has nearly the same entropy whatever its width. **The metric was never
given a chance to respond**, and I concluded it was broken.

**Gable validated the cloud points on real temperature-ramp data and they are accurate.**
Defaults reverted. The tests encoding the wrong conclusion are **removed, not adjusted** — *a test
that encodes a false conclusion is worse than no test.*

*(The UI passes ``signal_column`` explicitly, so the function default was never reached from the
application — the validated pipeline was not affected either way. But the default was wrong, and
the reasoning behind it was worse.)*

**The lesson is the 1.5.453 one again: check the simulation before the code.** And when the person
who ran the real experiment says it works, **that is data** — test the simulation against *their*
result, not the other way round.

**The real open question — low-quality data** (focus drift, illumination instability, bubbles) — is
written up in ``docs/audits/DEV_NOTES.md``, with the note that testing it needs a **degradation
model applied to a validated real ramp**, not another synthetic transition.

### NEW — `assess_and_split_touching`: **should** these masks be split?
``split_touching_objects`` runs a watershed and cuts. **It does not ask whether it should.** The
same connected mask can be four physically different things, and **only one of them is two
droplets**:

- **Two droplets in contact** — a **deep neck**. They have not fused. **Split.**
- **Arrested fusion** — caught part-way through coalescence, with a **shallow neck** because the
  interface has already relaxed. **This is ONE body, and the arrest IS the finding**: a pair that
  stalls mid-fusion is reporting a high viscosity or a solidified interface. **Splitting it
  destroys exactly that observation.**
- **Beads-on-a-string / fractal aggregate** — **many** small units. Cutting it in *two* is
  arbitrary; the object is not a droplet pair.
- **A single irregular droplet** — nothing to split.

**The neck ratio is the discriminator, and nothing else is.** It is the depth of the saddle
between the two distance-transform peaks, as a fraction of the peaks themselves:

| morphology | solidity | n_peaks | **neck_ratio** |
|---|---|---|---|
| single droplet | 0.979 | 1 | 1.000 |
| **two touching** | 0.906 | **2** | **0.364** |
| **arrested fusion** | 0.979 | **2** | **0.965** |
| beads on a string | 0.930 | **6** | 0.788 |
| fractal aggregate | 0.891 | 1 | 1.000 |

**Solidity does not separate them** (0.979 for arrested fusion is identical to a single droplet).
**The peak count does not** (both are 2). **Only the depth of the neck does** — and it moves
smoothly and monotonically with the degree of fusion (0.128 barely touching → 1.000 merged).

A neck shallower than ~0.6 of the droplet radius means **surface tension has already relaxed the
interface**. That is a physical statement, not a tuned threshold.

### Note — the intensity witness does NOT discriminate, so it does not get a vote
A real neck sits in a thinner part of the object and should be **dimmer**. **Tested, and it is
not diagnostic**: the neck intensity came out at **0.42–0.46** of the body median for a genuine
neck *and* an arrested one alike, because the body median is dominated by the bright droplet
centres and **every** neck is dim compared with those.

The geometry is decisive on its own (0.50 against 0.77 on the same pair), so the intensity is
**reported for inspection and not used to override the call**. *A witness that does not
discriminate must not be given a vote.*

**243/243 core tests passing.**

## [1.5.488] - 2026-07-10
### GROUP H — the cloud-point detector returned **the start of the temperature ramp**
``temperature_tools`` feeds the **phase diagrams and Csat**. Tested against a simulated heat-cool
cycle with a **known** transition (cloud 30 °C, clear 27 °C, hysteresis 3 °C):

| signal | T_phase | T_clear | hysteresis |
|---|---|---|---|
| *(truth)* | **30.0** | **27.0** | **3.0** |
| `entropy_corrected` *(the default)* | **20.74** | **20.79** | 0.05 |
| `entropy` | 20.68 | 20.71 | 0.03 |
| `image_mean` | 21.01 | 21.01 | 0.00 |
| **`focus_score`** | **29.67** | **27.00** | **2.67** |

**Every signal except ``focus_score`` returned ~20.7 — the first temperature in the ramp.** The
transition was not detected *at all*, and **a phase diagram built from that default is a plot of
when the experiment started.**

### Why entropy cannot work here — and it is physics, not a coding error
Shannon entropy of the intensity histogram is **non-monotonic across a phase transition**: it
**drops** as droplets nucleate (the histogram becomes bimodal and concentrated) and **recovers** as
they grow. Measured across the heating branch: **6.47 → 4.84 → 6.13.**

**An onset detector cannot find an onset in that — there isn't one.**

``focus_score`` rises **monotonically** (0.02 → 1.14) because **droplets introduce sharp edges**,
and *a phase transition is precisely the appearance of an interface*. **It is the physically right
signal.** Defaults changed to ``focus_score`` + ``midpoint``: the true cloud point now comes back
to **±0.33 °C** across a 26–34 °C range, and the **hysteresis to 2.67 against a true 3.0.**

### Found — `persistence_length_um` scales with the fibre LENGTH
**A perfectly straight fibre has INFINITE persistence length.** Lp is estimated from the decay of
the tangent autocorrelation, and **on a straight fibre the correlation never decays** — so the fit
is bounded only by *how much fibre was available*:

| fibre length | reported Lp |
|---|---|
| 40 px | 72.1 |
| 200 px | 379.9 |

**Lp ≈ 1.9 × the fibre length**, on fibres whose tortuosity is 1.00 — *they do not bend at all*.
**Two conditions whose fibres differ only in length would show different "stiffness."** Documented;
``tortuosity`` is correct (1.0021–1.0106 across the same fibres) and should be preferred.

### Audited and exact — `wlc_extensible`
**0.00 %** against the analytic Odijk high-force limit at every force tested.

**242/242 core tests passing.**

## [1.5.487] - 2026-07-10
### GROUP F — the coloc metrics are **exact**. They are also **not evidence**.
``manders_m1_calculation``, ``jaccard_index_calculation`` and ``sorensen_dice_coefficient_calculation``
reproduce the analytic overlap **to four decimal places**. *The maths is perfect.*

**And a pair of channels with NO colocalization whatsoever produces overlap by chance** — an
amount that scales with how crowded the image is:

| density | coverage | **M1 by CHANCE** |
|---|---|---|
| sparse (15 spots, r = 6) | 2 % | 0.024 |
| medium (40 spots, r = 8) | 12 % | 0.110 |
| **dense (80 spots, r = 10)** | **32 %** | **0.338** |

**Two completely independent channels give M1 = 0.34** at a realistic density. *"M1 = 0.34,
substantial colocalization"* is a **false claim** — that is exactly what randomness gives.

**And it cannot be a fixed threshold**, because it moves with the density: the same M1 that is
meaningless in a crowded cell is strong evidence in a sparse one.

The module contained **zero** occurrences of *null*, *chance*, *random*, *permutation* or
*significance*. (Costes randomization exists in ``pixel_wise_corr_analysis_tools`` — **the idea was
in the codebase; it had not reached here.**)

New ``coloc_significance`` relocates channel 2's objects **inside the ROI, keeping their number,
size and shape** — so the density, which is what drives the chance overlap, is preserved, and only
the *spatial relationship* is destroyed.

**False positives: 0/15 sparse, 1/15 medium. Power: detects a 3 px shift at p = 0.010.**

### Two things a first attempt got wrong
**Clipping the drop position deflates the null.** It pushes objects inward, where they pile up and
overlap *each other* — the null's coverage came out at **0.270 against the data's 0.313**. A null
that under-represents the density **under-states chance overlap, and everything looks
significant.**

**And the null is inherently conservative at high density — that is not fixable.** The objects
being relocated are *connected components*, and at 32 % coverage the original discs have already
merged: **80 discs became 29 blobs.** Relocating those blobs lets them merge *again*, so the null
reaches only **84 %** of the data's coverage.

**So the coverage is reported, and the result is flagged as strained** when the null cannot reach
the data's density. A colocalization claim in a crowded image needs the coverage beside it to be
read at all.

**233/233 core tests passing.**

## [1.5.486] - 2026-07-10
### GROUP E COMPLETE — `od_partition_coeff` reported **96,910,007**
Optical density is measured **relative to the background**: ``OD = -log10(I / I0)``. So **the
background's own OD is zero by construction** — *that is what "background" means in
Beer-Lambert.*

``mean_od / max(bg_od, 1e-9)`` therefore divided by **1e-9**, and reported **96,910,007** for a
condensate whose true OD is **0.097**.

**The number is not large — it is undefined.** And ``max(bg_od, 1e-9)``, a guard against division
by zero, is what turned an undefined quantity into a confident one.

**The correct quantity was already there.** ``OD = log10(I0 / I_dense)``, so ``10**OD`` is the
**transmittance ratio** — *how many times more light the object absorbs than the background* —
which is exactly what a partition coefficient measures:

| transmittance | true OD | **old** | **new** |
|---|---|---|---|
| 0.80 | 0.097 | **96,910,007** | **1.25** |
| 0.50 | 0.301 | 301,030,010 | **2.00** |
| 0.25 | 0.602 | 602,060,020 | **4.00** |
| 0.10 | 1.000 | — | **10.00** |

**Exactly 1/T at every transmittance.**

### Audited and correct — the rest of brightfield holds up very well
- **``compute_optical_density``** is exact Beer-Lambert: **0.0–0.2 %** error across the whole
  transmittance range.
- **The segmentation is correctly polarity-specific**: it finds 4 dark objects and **0** bright
  ones. **It will not silently process a fluorescence image** and hand back plausible output.
- **``fft_bandpass``** keeps or cuts **9/9** gratings exactly as their frequency dictates.
- **``interface_width_from_radial``** is proportional to the true boundary blur across a factor of
  8 in σ, and recovers the disc radius to **25.5 px against a true 25**.

**230/230 core tests passing.**

## [1.5.485] - 2026-07-10
### GROUP B COMPLETE — a 57 % difference in fractal dimension, from geometry alone
Two conditions containing nothing but **DISCS** — identical shape, only the size differs:

| condition | fractal D |
|---|---|
| small discs (r = 6 px) | **0.966** (sd 0.021) |
| large discs (r = 20 px) | **1.516** (sd 0.016) |

**A 57 % difference, and the scatter is so small it would test as overwhelmingly significant.**
*"Condensates in condition B are more space-filling"* — **completely false. A disc is a disc.**

Box counting on a finite image has a finite range of box sizes, and D approaches its true value
only as the object grows. A **filled square** — true dimension exactly 2.0 — measures **1.751 at
64 px** and **1.881 at 512 px**. *It never gets there.*

The algorithm itself is **exact where it can be**: a Sierpinski triangle measures **1.5850**
against an analytic log(3)/log(2) = **1.5850**. **The bug is not in the maths — it is in comparing
D between objects of different size**, and nothing said so.

Now documented, and ``fractal_dimension_per_cell`` carries ``mean_object_area_px`` beside every D
— **because without the size next to it, a size-driven difference is invisible.**

### `topo_n_basins` counts noise — and I could not fix it, so I said so
``peak_local_max`` with only a ``min_distance`` accepts **every** local maximum however small. On
a **flat field with nothing but noise** it reports **6.3 basins** — and 6.3 at a noise sd of 5, 20
and 60 alike.

**It is a constant, and it is anti-correlated with the truth**: a flat field reports 7 and a field
with 3 genuine peaks reports 6. It is measuring **how many points of separation ``min_distance``
fit inside the mask.**

**A global prominence gate made it worse** — the flat field still reported 4, and a field with 6
genuine peaks dropped to **2.3**. *Real structure raises the median, which then excludes the
structure.* A correct fix needs a **topological** prominence (how far a peak rises above its
saddle), which is a persistence computation, not a parameter tweak.

**Shipping a fix I cannot validate would be worse than shipping the bug with a warning.**
Reverted, written up in ``docs/audits/DEV_NOTES.md``, and every result now carries
``topo_n_basins_is_unreliable``.

**``topo_cov`` behaves correctly on the same data — 0.001 on a flat field, 0.42 with real
structure.** The module already had a good statistic sitting beside the broken one. **Prefer it.**

**225/225 core tests passing.**

## [1.5.484] - 2026-07-10
### GROUP B — "we found 8 clusters" is not evidence of clustering
A **complete-spatial-randomness** pattern — 120 uniformly distributed points, *by definition not
clustered* — produces clusters **every single time**. Over 20 realisations: **mean 9.0, range
6–13.**

``cluster_size_distribution`` reports the count correctly. **The count is meaningless on its
own**, and the Ripley/PCF null machinery (1.5.397, 419, 420) was sitting unused two modules away.

### And the cluster COUNT points the wrong way
**Clustering makes FEWER, BIGGER clusters**; randomness scatters many small accidental ones:

| pattern | n_clusters | **largest cluster** | **noise points** |
|---|---|---|---|
| CSR | 8.0 | **9.6** | **76.7** |
| **CLUSTERED** | **4.8** | **45.6** | **2.5** |

**The clustered pattern has FEWER clusters than the random one.** A count-based test is not
merely underpowered — it is **anti-correlated with the truth**, and a first version of the
significance test got the answer *exactly backwards* (CSR "clustered" at p = 0.030; a real cluster
"random" at p = 0.790).

New ``cluster_count_significance`` tests the **fraction of points in any cluster at all** (36 %
for CSR against 98 % for a real cluster) against a **compartment-constrained** null — points
re-scattered *inside the actual region mask*, because **an irregular cell manufactures apparent
clustering all by itself**. **False positives 4 %, power 100 %.**

### Note — validating a null requires the same point process
An early run showed a **40 % false-positive rate**, and it was **my test, not the null**: the CSR
pattern was drawn from a 180×180 box while the mask was 200×200, so **the data was 1.23× denser
than the compartment** — and the null was correctly reporting that. **A null can only be validated
against a process drawn from the same region it re-scatters into.**

### The hypothesis did NOT hold for `spatial_randomness_tools` — it is a model for the others
Moran's I is textbook-correct: **0.0001** on white noise, 0.97 on smoothed, and **exactly −1.0 on
a checkerboard**.

And ``structure_beyond_optics`` answers the question a microscopist actually has. *Every* image is
autocorrelated — **the PSF puts structure there for free** — and its docstring explains, correctly,
why a pixel-shuffled null cannot see past that:

| field | **p** | kurtosis |
|---|---|---|
| PSF-blurred noise (**no** real structure) | 0.119 | 0.19 |
| **bright blobs + the same PSF blur** | **0.005** | **4.85** |

**False positives 4–12 %, power 100 %** — detecting real structure at a blob amplitude of 0.5
against a noise sd of 0.3.

**221/221 core tests passing.**

## [1.5.483] - 2026-07-10
### GROUP C COMPLETE — a 4 µm object elongated in z was reported as **1 µm**
``prop`` came from a ``regionprops`` call with **no spacing**, so its axis lengths were in
**voxels** — and the code multiplied them by ``microns_per_pixel``, the **xy** pitch. On a
confocal stack the z step is typically **3–5× the xy pixel**, so **every z extent was divided by
that factor.**

Measured on a 0.1 × 0.1 × 0.5 µm voxel (5× anisotropic), against known geometry:

| object | true major | reported | **after** |
|---|---|---|---|
| sphere, r = 1 µm | 2.00 µm | 2.06 *(fine)* | 1.84 |
| **Z-elongated, 4 µm long** | **4.00 µm** | **0.98** | **3.58** |
| Z-elongated, 6 µm long | 6.00 µm | — | **5.34** |
| XY-elongated, 4 µm long | 4.00 µm | 4.45 *(fine)* | **3.98** |

**The error is invisible on anything round**, which is exactly why it survived — *the sphere case
is right.* And the ``spacing`` argument **was** being passed a few lines above, to the
marching-cubes surface area. **The axis lengths simply never used it.**

The axes are now computed from the central second-moment tensor **in physical units**. The
residual ~10 % on a z-elongated object is z-voxelization (only ~8 slices span it), not a units
error.

### Audited and correct — the 3D volume
A 1 µm sphere in that voxel occupies 787 voxels. **Assuming isotropy gives 0.787 µm³; the truth
is 4.19.** The module reports **3.94** — it uses the z step correctly, and avoids a **5× error**.

### Noted — `centroid_z` is in VOXELS while `centroid_x_um` and `centroid_y_um` are in microns
Not a bug — the name carries no ``_um`` — but **a trap**: a user computing a 3D distance from
``(centroid_z, centroid_y_um, centroid_x_um)`` mixes units and gets a wrong answer silently.

**215/215 core tests passing.**

## [1.5.482] - 2026-07-10
### GROUP C — `split_touching_objects` computed the right answer and threw it away
The watershed inside it **works**. It separates two touching discs at every real overlap, and
correctly **declines** when they have genuinely merged into one blob:

| overlap | components in | watershed labels |
|---|---|---|
| 0 px | 2 | **2** |
| 4 px | 1 | **2** |
| 8 px | 1 | **2** |
| 14 px | 1 | 1 *(one object now)* |
| 20 px | 1 | 1 |

The function then **discarded the labels** and rebuilt a **boolean mask** by subtracting Sobel
edges. **A boolean mask cannot express a split.** The two halves stay 8-connected through the
corner of the one-pixel cut, so ``label()`` on the output returned **ONE object at every
overlap** — including at zero overlap, where the discs merely *touch* and were **already two
separate components on the way in**. ***It merged them.***

**Touching condensates were always counted as one**, and every count, size distribution and
per-object measurement downstream inherited it. Now returns the labels; ``return_mask=True``
restores the old output for any caller that wants it — *though that output is the thing that
could not represent a split in the first place*.

### Audited — `partial_volume_tools` is among the best modules in the codebase
**It predicts an intensity bias it cannot remove**, which is exactly the right thing to do.

The PV weight fixes the **area** — a 1.5 px object reads **−43 %** through a plain mask and
**−3.6 %** through PV. But it **cannot fix the intensity**, because **the PSF has physically moved
photons out of the object**. They are not in those pixels to be re-weighted.

``intensity_bias_for_size`` predicts the residual to within a few percent:

| radius | predicted | **measured** |
|---|---|---|
| 2 px | −51 % | **−55 %** |
| 5 px | −22 % | **−22 %** |
| 10 px | −11 % | **−11 %** |

**A 2 px condensate reads 55 % too dim.** That is a *size-dependent* bias, and it can manufacture
a spurious intensity-vs-size trend where none exists. The module's own docstring says it plainly:
*"PV weighting minimises the software-added bias; it cannot undo the optics."*

### Audited and correct — `gaussian_localization_tools`, `clean_spot_detection_tools`
Sub-pixel localization to **0.008 px**, σ exact, and **fully pedestal-invariant** — because
``gaussian_2d_offset`` **has a background term**. *The estimators that fit an offset do not have
the bug that the ones without it do* (compare the CCF and ACF fits, 1.5.481). CLEAN detection
finds the exact spot count at every separation down to 3× the PSF σ.

**214/214 core tests passing.**

## [1.5.481] - 2026-07-10
### GROUP A COMPLETE — two more bugs, both in the LENGTH SCALE a paper reports
### FIXED — `ccf_sigma` was the std of the correlation VALUES, not the peak width
``np.std(ccf_values[peak_row, :])`` is the spread of the correlation **coefficients** along a
slice — a number in correlation units, bounded by the [−1, 1] range of a Pearson coefficient.
**It is not a length.**

It came out at **0.33** on data whose true correlation length is **4.24 px** — a **13-fold
underestimate**, and **it would have been 0.33 for any structure size.**

**And the real σ was computed and thrown away.** ``curve_fit`` fits
``gaussian_2d(xy, amplitude, x0, y0, sigma_x, sigma_y)``, and ``popt[3]``/``popt[4]`` **are** the
widths — in pixels, on the same axes the peak position was already being reported in. Now within
**3–9 %** of the analytic truth across the range.

### FIXED — the ACF Gaussian had no baseline, and σ blew up by 43 %
**A spatial ACF does not decay to zero.** It sits on a floor, and a Gaussian **forced through
zero must widen to reach it** — the inflation growing with σ, because the floor becomes a larger
fraction of the lobe:

| blur σ | expected | reported | error |
|---|---|---|---|
| 2.0 | 2.83 | 3.10 | +9 % |
| 4.0 | 5.66 | 6.11 | +8 % |
| **6.0** | 8.49 | **12.17** | **+43 %** |
| 8.0 | 11.31 | **15.51** | **+37 %** |

**It is not a finite-window effect** — a 512 px ROI is just as biased as a 128 px one, which is
what ruled that out. An independent Gaussian fit *with* an offset recovered the truth on exactly
the same data, and that is how the missing term was isolated. Now **−1 % to −9 %** across the
range.

### The pedestal hypothesis does NOT hold for the ACF — and that is worth recording
An autocorrelation is **normalised**, so an additive offset cancels exactly: σ = 5.29 at pedestals
of 0, 1000 and 4000. **The physics differs from SpIDA and N&B.** *A hypothesis that holds for
three modules and not the fourth is only useful if the exception is known.*

### Audited and correct — the CCF peak
Recovers a known inter-channel shift **exactly** at every offset tested. This is chromatic-shift
detection, and it works.

**204/204 core tests passing.**

## [1.5.480] - 2026-07-10
### GROUP A — the pedestal hypothesis was right, and worse than expected
**Stated before any code was run:** SpIDA, molecular counting, N&B and the correlation tools all
extract a molecular number from a **moment** of the intensity distribution — so **all of them
should carry the N&B pedestal bug (1.5.453): the camera offset adds to the mean but not the
variance.**

### FIXED — SpIDA: a **24-fold** overestimate of the molecule count
SpIDA fits N and ε to the **shape** of the intensity histogram, and a pedestal **shifts the whole
histogram to the right** — which the fit reads as *more molecules*. With a TRUE N of 8:

| pedestal | N fitted | ε fitted | **N error** |
|---|---|---|---|
| 0 | 7.78 | 25.65 | −3 % |
| 50 | 12.05 | 20.61 | **+51 %** |
| 200 | 31.00 | 12.79 | **+287 %** |
| **800** | **195.73** | 5.07 | **+2347 %** |

ε collapses by the same factor, because **N·ε is pinned by the mean** once their separation is
broken. ``check_assumptions`` existed — it checked size, saturation and SNR, and **not this.**

The gate is calibrated against the *damage*, not by eye: a pedestal of only **25 counts** already
inflates N by **23 %**, so it fires there. **0 false alarms in 20 clean seeds** — and the margin
is thin, which is itself the finding: **SpIDA has almost no tolerance for an offset.**

### FIXED — molecular counting: two bugs in opposite directions, partly cancelling
**The worst case**, because the combined error looks acceptable while each half is badly wrong.
TRUE ν = 100, N = 10:

| trace | ν | N |
|---|---|---|
| clean | 82.2 | **9.97** ← the estimator is **sound** |
| read noise (sd 15) | 104.2 | 7.72 (**−23 %**) |
| pedestal (500) | **47.5** | **17.86 (+79 %)** |

**And the fix is in the data**: after every fluorophore bleaches, the trace sits at the pedestal,
and the variance of that plateau is the read noise. **No dark reference needed** — a true pedestal
of 500 with sd 15 recovers as **497.7 ± 13.5** from the tail.

Two subtleties a first attempt got wrong:
- **The pedestal must come off before the variance pairs are built.** Both axes contain I(t).
  Subtracting it from ``y[fast]`` afterwards fixes the numerator and leaves **ν at 49.0 against a
  true 100**.
- **The noise floor is ``s²·(1 + p²)``, not ``s²``** — the y-axis is ``(I(t+1) − p·I(t))²``, which
  carries read noise from **both** frames. At p = 0.97 that is **1.94 × s²**.

Pedestal: **+79 % → −17 %.** Read noise: **−23 % → −11 %.**

### And I nearly shipped a fix for a WRONG SIMULATION — again
The first bleaching trace decayed **deterministically** (``int(count) × ν``), so there was **no
binomial fluctuation for the estimator to fit at all** — and the estimator regresses exactly that.
It made the code look broken when the *simulation* was. **The same trap as 1.5.453, and it took
the same form: check the simulation before the code.**

### NOT RESOLVED — the two corrections do not compose
Each works alone; **together they are worse than either.** Written up in
``docs/audits/DEV_NOTES.md`` with three hypotheses and a concrete next step (fit ν with a **free
intercept** — the intercept *is* the noise floor, which would collapse both corrections into one
and avoid estimating s² and p separately).

**192/192 core tests passing.**

## [1.5.479] - 2026-07-10
### GROUP D COMPLETE — time-series dynamics. One serious bug, two clean modules.
### FIXED — temporal enhancement destroys intensity-vs-time information. **Every method.**
This is not a bug in one of them — **it is what a contrast enhancement does**: it normalises each
frame against its own statistics, and a real change in brightness over time is normalised away
with it.

Measured, on objects that genuinely grow **+44 %** across 20 frames:

| method | trend still present | Spearman |
|---|---|---|
| *(raw)* | **+44 %** | — |
| per_frame | **+1 %** | 0.23 |
| pooled_stats | **+1 %** | 0.23 |
| windowed_mean | **+3 %** | −0.03 |
| triplanar | **+2 %** | 0.17 |

**A 44 % growth becomes 1 %.** So an enhanced stack must **not** be used for condensate growth or
coarsening rates, FRAP recovery, photobleaching correction, or partition/enrichment over time.
**The numbers will still come out, and they will be wrong.**

It is safe — and useful — for **segmentation and detection**, where only the *shape* matters and
the absolute intensity is discarded anyway. **That is what it is for**, and it now says so.

**``score_trend_preservation`` measures exactly this damage, and ``enhance_stack`` never called
it.** It does now, and warns — on a fade (photobleaching) as well as on growth, because *the fade
is the signal a bleach correction fits*.

*(And it does not cry wolf: a static stack has no trend to destroy, so the warning is gated on a
real change in the raw signal. A first version fired on every stack, and a warning that cries
wolf gets turned off.)*

### Audited and correct — `timeseries_invitro_tools`
Growth rate against an analytic truth (area = π·r², so the rate is exact):

| radius growth / frame | TRUE (µm²/s) | measured | error |
|---|---|---|---|
| **0 %** *(static)* | 0.0000 | **−0.0000** | **0.0 %** |
| 5 % | 2.0852 | 2.1030 | +0.9 % |
| 10 % | 5.5135 | 5.5188 | +0.1 % |

**Within 1 % at every rate, and exactly zero on a static stack** — the case a growth estimator is
most likely to get wrong, because *noise alone can manufacture a trend*. Fusion detection
recovers the event at the **correct frame** with the **correct parents**.

### Audited and correct — `estimate_temporal_correlation`
An AR(1) process has ρ(1) = exp(−1/τ) analytically, and the estimator matches it to **three
decimal places** at every τ (0.368 vs 0.368; 0.883 vs 0.882). Its recommendation is honest about
the cost.

**Recording a pass is as much the point as recording a bug. An audit is only worth something if a
clean result means something.**

**188/188 core tests passing.**

## [1.5.478] - 2026-07-10
### Group D continues — `fusion_tools`: τ is the physics, and it was 21.6 % wrong at R² = 1.000
Two droplets coalesce and the aspect ratio relaxes exponentially. The **inverse capillary
velocity** — η/γ, the viscosity-to-surface-tension ratio — is read off the **slope of τ against
droplet length**. **So a biased τ is a biased η/γ, by exactly the same factor.**

``fit_fusion_relaxation`` discarded its covariance (``popt, _ = curve_fit(...)``) — the same bug
as FRAP (1.5.446), the MSD fit (1.5.447) and photobleaching (1.5.451). Measured, **true
τ = 20 s**:

| window | τ observed | fitted τ | error | R² |
|---|---|---|---|---|
| 0–40 s | 2.0 | 15.69 | **−21.6 %** | **1.000** |
| 0–60 s | 3.0 | 17.89 | −10.6 % | **1.000** |
| 0–200 s | 10.0 | 19.79 | −1.0 % | 1.000 |

**A 21.6 % error in τ, at R² = 1.000** — and that is a 21.6 % error in the η/γ that goes into the
paper. τ now carries a 95 % CI (it narrows from **±2.7 s** at 2 τ to **±0.65 s** at 10 τ), and a
record shorter than ~3 τ warns.

### Note — the window measure has to see past the model's linear DRIFT term
The model is ``S(t) = a·exp(-t/τ) + b·t + d``. A first version measured the remaining amplitude
as ``|y[-1] − d| / |a|`` — and on a 200 s record with ``b = 1`` that is **200/2 = 100**, because
**the endpoint is dominated by the drift, not the relaxation.** The measure was meaningless and
fired the gate on every window, good ones included.

### Verified correct — `inverse_capillary_velocity`
It recovers **2.58 s/µm against a true 2.5** (3 %) at R² = 0.98. **This is the number a fusion
paper reports**, and it is sound.

**176/176 core tests passing.**

## [1.5.477] - 2026-07-10
### The linkers had NEVER been tested — and the default was costing 20 % of the viscosity
``dynamic_spatial_tools`` holds the two automated linkers that turn detections into
trajectories. **Every VPT viscosity PyCAT reports comes through one of them, and nothing tested
them.**

The only honest test of a linker is **ground-truth identity**: simulate objects whose true
identity is known, link the detections, and ask what fraction got the right one. A track count
catches nothing — 20 objects can produce 20 tracks that are all wrong.

### FIXED — the gap default was 0, and the reasoning behind it was backwards
The tooltip said bridging a gap is *dangerous* — that a bead which vanishes and reappears is
*"more likely a broken trajectory that should be pruned"*. **Ground truth says the opposite.**

A detector that misses **10 % of frames** turns **20 objects into 92 tracks** at ``gap=0``, with
only **49 %** of detections keeping their identity:

| dropout | gap | purity | tracks (true = 20) |
|---|---|---|---|
| 10 % | **0** | **49 %** | **92** |
| 10 % | 1 | 87 % | 32 |
| 10 % | **3** | **99 %** | **21** |
| 20 % | **0** | **29 %** | **147** |
| 20 % | **3** | **99 %** | **21** |

**And it is safe: zero mixed tracks at any gap** on separated objects. Bridging *repairs a
break*; it does not invent a link. (A mixed track *would* be dangerous — it injects a spurious
jump into the MSD and **deflates** the viscosity — which is presumably the fear the old default
was built on. **The fear is real; the setting was aimed at the wrong end.**)

### It moves the real measurement, toward the reference
On the bead file, against the **8.325 Pa·s** reference:

| gap | tracks | α | **η** |
|---|---|---|---|
| 0 *(the old default)* | 243 | 1.052 | **10.14** |
| 1 | 118 | 0.930 | 7.97 |
| **2** | 85 | **0.972** | **8.54** |
| **3** | 74 | **0.968** | **8.57** |
| 5 | 61 | 1.055 | 9.91 |

**η = 8.54 against 8.325 — a 2.6 % difference — with α = 0.97**, closer to the Brownian 1.0 than
gap=1's 0.93. **Default changed to 2**, with the measurement in the tooltip.

*The synthetic ground truth predicted this before the real data was touched. That is what the
harness is for.*

### Found — the two linkers are identical until objects get confusable
And then the Bayesian one wins, exactly where an assignment model should:

| object spacing | greedy purity / mixed | bayesian purity / mixed |
|---|---|---|
| 1.0 µm | 100 % / 0 | 100 % / 0 |
| 0.3 µm | 70 % / 11 | **79 % / 14** |
| 0.2 µm | **52 % / 25** | **67 % / 14** |

Gable's beads sit ~1.7 µm apart, which is why the two give **byte-identical** results on that
data. **The choice only matters in a crowded field** — worth knowing, and it was not known.

**171/171 core tests passing.**

## [1.5.476] - 2026-07-10
### FIXED — CI red: `measurement.py` shipped in 1.5.384 and never landed in the repo
``ModuleNotFoundError: No module named 'pycat.utils.measurement'``. It is imported by
**``vpt_tools``** (viscosity) and **``invitro_tools``** (partition coefficient), and it has been
missing from the repository since **1.5.384** — roughly ninety releases.

Diffed the whole tree against Gable's actual repository. The result is narrow and clean:

| | |
|---|---|
| identical | **113 files** |
| **missing** | **1** — ``src/pycat/utils/measurement.py`` |
| **stale** | **1** — ``src/pycat/toolbox/pipeline_snr_tools.py`` (the CNR/SNR fix) |
| tests / tools | **0 missing, 0 stale** |

**Both were in shipped ZIPs and neither landed.** That is not a git fault — it is a hole in the
delivery, and the diff is the only way to find it. *A file that is in the ZIP and not in the
repository is invisible to every test that passes locally.*

``pipeline_snr_tools`` carries the CNR correction: the old ``snr_raw`` does **not** subtract the
background, so it is inflated by the camera pedestal — the identical image reported an "SNR" of
**28 / 78 / 282 / 1049** at pedestals of 0 / 100 / 500 / 2000 counts. The repository has been
running the uncorrected version.

**170/170 core tests passing.**

## [1.5.475] - 2026-07-10
### Rebuilt the report layout on `SubFigure` — overlap is now structurally impossible
**Ten attempts to hand-tune the geometry all failed the same way**, and each fix on one report
size re-created the problem on the other.

The cause: **the scorecard is a text LIST and the panels are a plot GRID.** They have nothing in
common, and forcing them into one coordinate system caused every overlap. The rows are laid out
in *fractional axes coordinates*, so a height ratio that is too small crushes the teaching line
into the score line above it, and one that is too large leaves a huge empty box — **dead space
and overlap are the same bug seen from two sides.** And the panels' tick labels extend *above*
their axes box, so a grid whose top is flush with the scorecard still collides with it.

``SubFigure`` + ``constrained_layout`` packs the grid — tick labels, titles and captions
included — **by construction**:

- the scorecard gets its own unconstrained subfigure and is laid out as what it is: a list;
- the per-panel captions are folded into the **x-label**, so the layout engine can see them (an
  ``ax.text`` at a negative y is invisible to it, and it was packing the panels down onto them);
- the footer moved onto the scorecard subfigure — a ``fig.text`` at a fixed y is *also* invisible
  to the engine, and it reads better next to the legend it explains than orphaned at the foot of
  the page.

Verified at both report sizes: 2-D (12 checks, 6 panels) and time series (12 checks, 9 panels).

### Note — a mechanical overlap guard found real bugs, and it does NOT work under SubFigure
Comparing every text artist's rendered bounding box **found collisions I could not see by eye** —
including a **65 px overlap** between the scorecard's last row and the histogram's topmost tick,
on a report that looked fine.

**It stops working under ``SubFigure``.** ``get_window_extent`` returns boxes that do not resolve
for artists inside a subfigure: it reports the footer as intersecting a ``10^5`` tick, and
**cropping those exact pixels shows the footer alone, with no tick anywhere near it.** A second
``canvas.draw()`` does not fix it.

So the guard now reports **2 false positives on the 2-D report and 4 on the stack**, and **a guard
that cries wolf will be disabled by whoever trips over it next.** It is therefore *not shipped*,
and the problem is written up in ``docs/audits/DEV_NOTES.md`` with three concrete approaches to
try. **The guard is worth having** — it caught bugs invisible to inspection, and the display bugs
it would catch are exactly the ones that recurred all session.

**170/170 core tests passing.**

## [1.5.474] - 2026-07-10
### FIXED — the report was describing a method the code no longer used
``qc_vignetting`` was rebuilt on a **median filter** in 1.5.473, because ``grey_opening`` takes a
local *minimum* — which is ~0 on any dark background, so it returned an identically zero
illumination field and **the check was blind**.

**The `how` text was not updated.** For one release the report told the user it was estimating
the illumination with *"a large grey-scale opening"*, and it was not.

**A report that misdescribes its own method is worse than one that is silent.** A reviewer
reading that methods section would have been reading a fabrication, and a user could not have
checked the result against the method. Corrected in all three places, and guarded — *the teaching
text is part of the output, and it goes stale the moment the code changes underneath it.*

### FIXED — the vignetting panel contradicted its own verdict
The panel plotted the raw radial profile on an **autoscaled** y-axis, so a perfectly flat field —
varying by **2 counts out of 200** — was drawn as a wild oscillation filling the panel.

**A user looking at that concludes their illumination is a mess while the check beside it says
"good".** *The picture contradicted the verdict.*

Normalised to the centre, with the axis fixed at 0–1.1 and the 0.9 threshold drawn. A flat field
now looks flat; a vignetted one looks vignetted.

### Confirmed — the gallery was teaching a lie about vignetting, and now is not
The exemplar showed a clean field and a 55 %-vignetted one **and reported "100 % of centre" for
both** — because the metric it calls was blind. It now reads **99 % clean, 55 % degraded**.

**The gallery and the metrics check each other**, and this is what that is for: an exemplar that
does not trip its own metric fails the build, and a metric that cannot see its own exemplar is
caught by the picture.

### Checked and correct — no change needed
- **The CSV export carries the teaching text.** ``how_measured`` and ``good_data`` travel with
  every number, which is what a methods section needs.
- **``bbox_inches='tight'`` on save is right.** It *expands* the canvas (1502 px against 1407) to
  include the captions rather than cropping them.

**170/170 core tests passing.**

## [1.5.473] - 2026-07-10
### The report had never been RENDERED. Six display bugs and three science bugs.
Gable asked to see the output. **I had never looked at it.** Every audit so far checked the
numbers; nobody checked the page the scientist reads.

### Science — three real bugs, two of them blinding a core check
**`qc_vignetting` was blind to vignetting.** ``grey_opening`` takes the local **minimum**, and on
any image with a dark background **the minimum is ~0 everywhere** — so the "illumination field"
it produced was **identically zero**, and the edge/centre ratio came out at exactly **1.00,
"good"**, on a scene with a **35 % radial falloff**. A median filter is robust to the bright
objects *without* collapsing to the minimum: it recovers **0.69** against a true 0.64.

**`qc_saturation` missed a real clip by one pixel.** The pile-up threshold was a fixed count
(``> max(10, 0.0001·size)``), and a 256×256 image with **9 clipped pixels** was reported as
**"0.00 % at ceiling, GOOD"** while its histogram showed the spike plainly. **Nine clipped pixels
are still clipped** — they are the peaks of the brightest objects. The signature is scale-free: a
clip dumps everything above the ceiling into **one bin** (n@max = 313 against 2 in the levels
below), while an unclipped distribution tapers smoothly (n@max = 1, and 0 below).

**A dark background was being called a clipped floor.** ``mean(a <= 0)`` counts every zero as
"clipped at the sensor floor" — and on a background-subtracted image, which PyCAT produces
everywhere, **half the background is at zero by construction**. It reported **"9.17 % at floor →
POOR"**.

**And photobleaching reported a τ of 5,663,342,369,728,770 frames** on a stack that does not
fade. That is floating-point noise in the slope, not a measurement.

### Display — six bugs
1. **The title and the verdict were printed on top of each other.** ``suptitle`` and
   ``ax.text(y=1.02)`` on the same axes. *The first thing a user sees was two superimposed
   sentences.*
2. **Every caption overlapped the panel below it** (``y = -0.42`` of a panel-height, which is
   exactly where the next row draws its labels).
3. **Three diagnostic panels were completely empty** — SNR, Focus and Nyquist return diag dicts
   of *scalars*, and the panel dispatch had no branch for them. They are scalars **with a
   reference**, so they now render as a bar against the threshold: *the comparison is the whole
   point of those three checks.*
4. **The saturation histogram spanned 0–65535** while the data occupied the first 1/16th — the
   clipping spike, the thing the panel exists to show, was invisible.
5. **Huge dead space** between the scorecard and the panels.
6. **The bottom captions were cut off** by the footer.

### Note — the scorecard is a LIST, and GridSpec cannot size a list
Five attempts to tune ``height_ratios`` by eye all failed the same way. The rows are laid out in
**fractional axes coordinates**, so a ratio that is too small crushes the teaching line into the
score line above it, and one that is too large leaves a huge empty box. **Dead space and overlap
are the same bug seen from two sides.**

The scorecard now gets its own axes, placed in **figure fractions and sized in inches** — one
row's worth of height per check. ``dy`` is then a real physical distance. *And the title and
verdict now live in the same coordinate system, because fixing their overlap on the 2-D report
(14.1 in) had re-created it on the stack report (18.6 in).*

### Note — I tried to correct the vignetting pedestal, and it cannot be done
The pedestal is **additive** and the illumination is **multiplicative**, so a camera offset drags
the ratio toward 1: a real 35 % falloff reads **0.99 on a 2000-count offset**. The check goes
blind on a high-offset camera.

**The obvious correction is circular.** The darkest part of the illumination field — the natural
pedestal estimate — **is the vignetted corner itself.** Subtracting it removes the signal being
measured: a 0 % falloff then read 0.48 and a 35 % falloff read 0.02. *Worse than the disease.*

The pedestal is a property of the **camera**, not of this frame, and the only honest source is a
dark reference — the same conclusion reached for Kp (1.5.423). **So the check says it reads high
on a high-offset camera**, rather than reporting a corrected number that was never correct.

### And a test that passed on a broken metric
``test_snr_and_vignetting_are_invariant_to_the_camera_pedestal`` asserted that vignetting is
pedestal-invariant, and it **passed** — because the metric was broken and always returned 1.00.

**A test that passes on a broken metric is worse than no test.**

**168/168 core tests passing.**

## [1.5.472] - 2026-07-10
### The QC *UI* had never been audited — and it carried three fixes that never reached it
Gable asked, for the third time, whether the QC audit was finished. **It was not.** Releases
1.5.465–471 audited the library. **``data_qc_ui.py`` — the thing a user actually touches — had
never been looked at**, and it carried its own copies of bugs already fixed one layer down.

### 1. `np.asarray(layer.data)` on a lazy stack returns FRAME 0 ONLY
This is the **1.5.273 bug**, still live in the QC UI while every other stack-consuming UI had
moved to ``materialize_stack``. A lazy ``_TiffPageStack`` implements ``__array__`` as a
deliberately-truncated single frame, so a **1000-frame movie silently becomes one image** — no
error, no indication.

**The consequence is that QC lies about what it checked.** Drift, vibration and photobleaching
all need a time series, and given one frame they report *"n/a — needs a time series"*. **A user
looking at their movie reads that as "PyCAT looked and found nothing to report." It did not
look.**

*This class of bug has now bitten this codebase three times.*

### 2. The coverage trap, in a second copy
*"QC — all assessed metrics look good"* was fixed in ``plot_qc_report`` in 1.5.469 — **and the
UI carried its own hardcoded copy of the same sentence**, so the fix never reached the message
the user actually sees.

**A correction that lands in one of two copies has not landed.**

### 3. `qc_chromatic` could never run from the UI
It measures correctly when handed the channel images — 0.00 px on registered channels, **3.61 px
on a true 3.6 px shift** (1.5.471). The UI passed only ``n_channels``. **A working check sat
idle in every session.**

The channels are now collected from the viewer (the other same-shaped image layers — which is
exactly what a multi-colour acquisition looks like once loaded) and passed through.

### 4. The exemplar gallery was reachable from nothing
Built in 1.5.466, wired to **nothing**. A user reading *"Focus: bad"* on their own data had no
way to see what *bad* looks like. **A teaching tool nothing can open is not a teaching tool.**

Now a button on the QC report: *"What does a quality problem look like?"*

### Guarded
``tests/test_qc_ui_contract.py`` — four static contract tests, because the bugs are **structural,
not behavioural**, and a Qt widget cannot be instantiated headlessly.

*(The coverage guard checks the string literals the user sees, not the source text — a comment
documenting the bug is not the bug, and a guard that cannot tell the difference will be disabled
by the next person who trips over it.)*

**166/166 core tests passing.**

## [1.5.471] - 2026-07-10
### The QC audit is finished — 16 of 16 functions now have tests (was 11)
Gable asked whether the audit was done. **It was not.** Five functions had never been checked
against the defect they name: ``qc_ghosting``, ``qc_photobleaching``, ``qc_time_sampling``,
``qc_chromatic`` and ``diffraction_limit_px``.

**Four of the five were correct** — and one, ``qc_ghosting``, is better than expected. It detects
a reflection ghost *and* **recovers its offset**, reporting ~12 px for a 12 px ghost and ~25 px
for a 25 px one. **That offset is what tells the user which optical surface is reflecting.**

| image | echo | verdict |
|---|---|---|
| clean | 0.0016 | good |
| 15 % ghost, 12 px | 0.0105 | warn |
| 30 % ghost, 25 px | 0.0199 | bad |

``qc_photobleaching`` reports the fraction remaining to ~1 % of truth (52.9 % against 53.8 %).
``qc_time_sampling`` is correct Nyquist-in-time. ``diffraction_limit_px`` is exact Abbe physics.

### FIXED — a correct check that could never run
**``qc_chromatic`` measures perfectly when handed the channel images** — 0.00 px on registered
channels, and **3.61 px on a true 3.6 px shift.** But ``run_full_qc`` passed only the channel
**count**, so it could never do anything but report *"info — pass the channel images."*

**A check that is correct and never invoked is indistinguishable from one that is broken.**
``run_full_qc`` now takes ``channels=[ch1, ch2, ...]`` and passes them through.

### Note — the test fixture was cleaner than any real acquisition, and that is not conservative
Two checks failed on the shared test scene for the same root cause: **it had no noise.**

- ``qc_snr`` returned **infinity** (nothing to divide by).
- ``qc_ghosting`` **fired on a clean image** (0.0063 → warn). Randomly placed puncta produce
  spurious cepstral peaks — a random point pattern *has* repeated inter-object spacings by
  chance — and **noise is what dithers them away.** The same scene with sd = 6 noise reports
  0.0016 → good.

**A fixture that is cleaner than any real image is not a conservative test. It is a different
test, and it fails for reasons that will never occur in practice.** (And the stack fixture needs
the *opposite*: a noise-free base, because a fixed noise pattern repeated in every frame is a
strong registration target that does not move — which broke the drift checks.)

**162/162 core tests passing. Every QC function is now exercised against the defect it names.**

## [1.5.470] - 2026-07-10
### Gable was right — a single image CAN be judged for focus, via the sharpness of object edges
The old check refused a verdict and headlined *"sharpness = 545.3 (relative)"*. It was right that
the **band-pass energy** cannot judge a single image — it measures **global** energy, so a sparse
in-focus field scored **105.9** and a dense blurred one **118.1**. But that is a limitation of
the estimator, **not of the question.**

Edge sharpness is a **local** property of a boundary, so it is scene-independent. In focus, on
the same optics: a sparse field measures **4.59 px** and a dense one **4.44 px** — 3 % apart —
while defocus moves both monotonically.

**The sharpest edge, not the average.** A big smooth cell genuinely *has* a wide edge, in focus
or not — so an average confounds object size with focus all over again. The sharpest edge asks
the right question: *could anything in this image be sharper than it is?* **A blurry cell cannot
hide a sharp punctum** — adding large smooth cells to a field of puncta leaves the answer
unchanged (2.82 px either way), while defocus moves it (2.82 → 3.29 → 4.42 → 6.43).

The estimator is calibrated against exact synthetic edges: ``contrast / steepest_gradient``
converges to **σ·√(2π)**, the analytic result for a Gaussian-blurred step.

### Added — the comparative path, which is how focus is actually used
> *"Which of my 40 fields is the soft one?"*

This needs **no optical metadata at all.** In a 40-field acquisition where field 17 slipped out
of focus, the median sharpest edge is **2.78 px** and field 17 is **4.40 px — 1.58× the
median**, the only outlier.

**This is the exact path**, because across one dataset the object type is constant and the
calibration constant **cancels**.

### Note — the ABSOLUTE path is a screen, and it says so
I tried to set the thresholds by the measurement error the blur causes, **and it cannot be done
honestly.** The conversion constant depends on what the object *is* — **2.51× σ for a step edge,
1.65× σ for a Gaussian blob** — and the estimator cannot distinguish them. That is a **~1.5×
systematic floor**, and it is *larger than the effect being measured*: a 2 px blur costs **+94 %
apparent object size** and moves the ratio only 0.45 → 1.14.

Any threshold tight enough to catch that would fire on a perfectly focused image of the wrong
object type. **So the absolute verdict is deliberately wide, calls itself a screen, and points
the user at the comparative measure.** *A tighter threshold would be false precision, and would
send someone to refocus a microscope that is already at the diffraction limit.*

### And it refuses when the field has no sharp objects at all
A brightfield field of large smooth cells has **no sharp edge anywhere**. The check reported
**4.0× the limit → "bad"** — *true about the image, wrong about the focus.* It **cannot
distinguish "soft objects, sharp focus" from "sharp objects, soft focus"** when nothing small is
present. Detected, and refused with the reason.

### FIXED — a smooth drift was firing the vibration alarm
Once the test scene was made diffraction-limited, a stack drifting at 0.5 px/frame fired
``qc_vibration`` at **p = 0.005, "bad"** — sending the user to hunt for a pump.

**The metric was right and my simulation was wrong.** ``ndi.shift`` with a fractional
displacement leaves an interpolation ripple that **repeats with the fractional part of the
shift** — period 2 at 0.5 px/frame. That is a genuine periodic signal, correctly detected. Fixed
in the harness (cubic interpolation), not the metric. **The bug was always there, hidden behind a
blurry test image.**

**158/158 core tests passing.**

## [1.5.469] - 2026-07-10
### The rest of the QC audit — the *report*, not the metrics
1.5.465 audited whether each metric works. 1.5.468 audited whether it runs on the right data
type. **Neither audited the two things a user actually reads: the teaching text, and the overall
verdict.** This does.

### FIXED — the report could say "all good" while most of it never ran
The verdict counted only `bad` and `warn`. On an image with **no pixel size, no NA and no frame
interval — only 4 of 12 checks actually run.** Nyquist, time sampling, chromatic aberration,
drift, vibration, photobleaching and spherical aberration are all skipped, and the report said:

> *"All assessed metrics look good."*

**The word "assessed" is doing enormous work there, and no user reads it that way.** They read
*"my data is good."* **A report that looks clean because most of it did not run is the exact bait
this module exists to prevent.**

It now says: *"All 4 checks that ran look good — but 8 could NOT run (missing metadata, or the
wrong kind of data). **This is not a clean bill of health.**"*

### FIXED — `"sharpness = 545.3 (relative)"` is not a result
No scale, no target, no action. And it is **baiting**: a user scanning a colour-coded scorecard
sees a row with a number and no red flag, and concludes the focus was checked and passed. **It
was not checked.**

A single image genuinely *cannot* be judged for focus without a reference — the band-pass energy
of a **sparse** field is legitimately lower than that of a **dense** one, and neither is out of
focus. The headline now says so, and says what would let the check give a verdict (a z-stack, a
time series, or a known in-focus image of the same specimen). The number is kept in `value` for
the one case where it *is* meaningful: comparing two images of the same specimen.

### FIXED — two spellings of "not applicable"
`'n/a'` (my own, from 1.5.468) and `'na'` (the existing 10 uses). **`'n/a'` is not a key in
`_STATUS_COLOR`**, so the three checks I marked in the last release would have rendered with a
fallback colour in the scorecard. Unified to `'na'`, and the test now asserts every `na` entry
carries a **reason** — *a check the user cannot see the reason for is indistinguishable from one
that was silently dropped.*

### Checked and correct — no change needed
- **`qc_snr` reporting `bad` on featureless noise.** That is right: pure Gaussian noise *does*
  have SNR ≈ 3.3. Not every alarm on a degenerate input is a false alarm.
- **The `how` / `good` text on the working checks.** Each explains the method *and* gives a
  target with an action (*"SNR ≳ 10 is comfortable; below ~4 the structure is buried — increase
  exposure/illumination"*). That is the Image → Assessment → Interpretation → Recommendation
  shape, and it holds.

**155/155 core tests passing.**

## [1.5.468] - 2026-07-10
### Audited the QC *report*, not the metrics: 4 false alarms on clean data → 0
The previous audit (1.5.465) asked *"does each metric work?"*. This one asks the question that
actually matters to a user: **is the report as a whole honest — does it teach, and does it avoid
baiting people into false conclusions?**

Run on **clean, good data** of four types, where **any warn or bad is a false alarm by
definition**:

| check | 2D fluor | brightfield | **Z-STACK** | time series |
|---|---|---|---|---|
| Drift | — | — | **bad** | good |
| Focus / sharpness | info | info | **warn** | good |
| Ghosting | good | good | **warn** | good |
| Spherical aberration | info | info | **warn** | info |

**All four were on the z-stack.** Brightfield passed cleanly, and correctly — ``qc_snr`` tracks
contrast, not the lamp (33 → 6.4 → 3.5 as the absorption depth falls).

### Fixed — checks that cannot apply now say so, with the reason
**Drift on a z-stack was the worst.** With **zero** lateral drift it reported **89.2 px, "bad"**
— and adding a full pixel per plane of *real* drift moved it only to 100.1. **It is blind to the
thing it names**: the phase correlation is failing on the sharp-vs-blurred mismatch between focal
planes, not measuring displacement.

*(Z-planes ARE acquired sequentially, so drift between them is physically real — this is not an
inapplicable question, it is a broken measurement. The honest response is to say the check does
not work here, not to report a number that does not mean what it says.)*

**Focus** flagged 2/21 planes as below half-median sharpness — **which is what a z-stack is.**
The outer planes are *supposed* to be blurred. Flagging correct data teaches the user to ignore
the focus check, which is the one that matters most on a 2D image.

These now report **`n/a` with an explanation**, not a verdict. **A check that cannot apply must
not report "good" either** — that is a quiet lie: the user reads a clean report and concludes
their data passed a test that was never run.

### FIXED — `qc_spherical_aberration` was inverted. Two bugs, stacked.
**It fired on clean stacks and passed the aberration it exists to detect.**

**1. The axial profile did not peak at best focus.** ``_axial_sharp`` is a difference-of-Gaussians
band-pass at σ 1–2, and when the in-focus objects are *sharper* than that band, **the response
dips at best focus** — the sharpest plane is a local *minimum*:

```
plane  9: 1.000   <- argmax lands HERE
plane 10: 0.849   <- the TRUE focal plane, and a LOCAL MINIMUM
plane 11: 0.999
```

``argmax`` landed one plane off, the moments were taken about the wrong origin, and a **perfectly
symmetric stack** (left energy = right energy = 544, *exactly*) reported skew **+0.577 → warn**.

**2. The normalised third moment is the wrong statistic.** Fixing the origin exposed a **false
negative**: a stack with **half the energy on one side of focus** (right/left = 0.499) reported
|skew| = 0.080 against a threshold of 0.4 — and **passed as good**. The ``m2^1.5`` denominator
grows with the axial spread, and **spherical aberration *is* a one-sided spread**, so the
normalisation *cancels the very asymmetry it should expose*.

Replaced with the **energy ratio about focus** — *does the response fall off at the same rate
above and below?* — which is what a bead z-stack is inspected for by eye. Symmetric now reads
**0.000 → good**; severely aberrated reads **0.461 → bad**. The signed skew is kept in the
diagnostic, because **its sign tells the user which way to turn the correction collar.**

**Zero false alarms on clean data, across all four data types. 153/153 core tests passing.**

## [1.5.467] - 2026-07-10
### Added — every QC exemplar now carries a Wikipedia link and a primary citation
**A defect the user cannot look up is a defect they cannot learn from.** The gallery makes a
claim about someone's data — *"this is saturated, and your partition coefficient is
meaningless"* — and a scientist is entitled to check that against something other than our own
docstring.

Two links, doing different jobs:

- **Wikipedia** — the accessible entry point. Someone who has never heard of vignetting needs
  somewhere to start that is not a paywalled review.
- **A primary reference** — from the quantitative-microscopy literature a reviewer expects:
  **Waters 2009** (*J Cell Biol* 185:1135), **North 2006** (*J Cell Biol* 172:9),
  **Jost & Waters 2019** (*J Cell Biol* 218:1452), **Jonkman et al. 2020** (*Nat Protoc*
  15:1585). Every citation resolves through a DOI, and the test asserts it.

Clickable in the widget, rendered in the docs.

### The saturation exemplar quotes Waters (2009) verbatim, because it justifies our refusal
> *"Detectors have a limited capacity to hold electrons; if this capacity is reached, the
> corresponding pixel will be saturated… The linearity of the detector is therefore lost, and
> saturated images cannot be used for quantitation of fluorescence intensity values. **Choosing
> to crop out saturated areas is not acceptable… because it will select for the weaker intensity
> parts of the specimen.**"*

That is the canonical reference stating exactly what 1.5.392 measured: **a clipped value is not a
lower bound**, and the sensible-looking rescue — analyse the unsaturated regions — is worse than
the disease, because it biases the population toward the dim.

**PyCAT refuses to report a Kp from a clipped image.** A user who finds that inconvenient can
now read, in one click, why the field agrees.

**147/147 core tests passing.**

## [1.5.466] - 2026-07-10
### Added — the QC exemplar gallery: what each defect LOOKS like, and what it COSTS
A QC report that says *"Focus: 0.42"* teaches nobody anything. The module's purpose is the shape
**Image → Assessment → Interpretation → Recommendation**, and **the Image half was missing.** A
scientist looking at a "bad" verdict on their own data had no reference for what "bad" looks
like, or how bad theirs is by comparison.

Seven exemplars — saturation, defocus, low SNR, vignetting, photobleaching, drift, vibration —
each a **clean** frame beside a **degraded** one, with the verdict PyCAT gives each.

**Two renderers, one generator:**
- **In-app** (``qc_gallery_ui.py``) — a dockable panel to open beside a QC report.
- **Documentation** (``tools/build_qc_gallery.py`` → ``docs/qc_gallery/``) — PNGs and a markdown
  page, which is also what a supplement wants.

**The verdicts are computed, not written down.** The gallery calls the real metric on the real
image. **A teaching example that no longer matches the software is worse than none** — it would
tell a user their data is fine when PyCAT would say otherwise — so
``tests/test_qc_gallery.py`` asserts that every clean panel comes back *good* and every degraded
panel comes back *warn* or *bad*. If a metric stops firing on its own exemplar, the build fails.

### On simulated exemplars
**These are simulated, and every panel says so.** Real bad-data exemplars accumulate slowly, and
waiting for a curated set means shipping nothing. A simulated exemplar is honest about what it
is: it carries the **exact parameter** that produced it (*clipped at 600 counts*, *sigma = 3.0
px*), so a user can reason about the *degree* of the defect rather than eyeballing a vibe. The
interface does not change when a real example replaces a simulated one — ``source`` is a field.

**What must not happen** is a gallery that quietly implies *"your data should look like this"*.
A synthetic image is not an acquisition standard. Every panel is labelled `SIMULATED`, in the
widget and in the docs.

### Added — `qc_photobleaching`: the metric that did not exist
Building the gallery surfaced this immediately: **the QC module could not see photobleaching at
all.** It had ``qc_drift`` and ``qc_vibration`` for temporal *motion*, and nothing for temporal
*intensity*.

It cannot be folded into ``qc_snr``: **a global intensity scale changes the signal and the noise
together**, so the SNR is (correctly) invariant to it. *A stack that fades to a tenth of its
brightness has the same SNR at the end as at the start — and is useless.*

And it is one of the most destructive defects there is: a bleach correction **divides** by
exp(-t/τ), so an error in τ compounds exponentially (**96 % over-correction** of the final frame,
1.5.451), and in FRAP an uncorrected fade makes the recovery read **2.5× too fast** with a mobile
fraction 31 % too low — at R² = 0.94 (1.5.455).

### Note — the gallery taught us something about our own metric
The vignetting exemplar cannot use the four-cell reference scene, because **four cells arranged
in a ring genuinely are a radial intensity pattern** — ``qc_vignetting`` reads the *clean* scene
as **bad (0.535)**: *"edge is 54 % of centre brightness"*.

**The metric is not wrong. The scene is.** But that is a real caveat for a user with a sparse
field: if your cells happen to sit toward the centre, the vignetting score is reporting **where
your cells are**, not how your lamp is behaving. The gallery says so out loud, which is exactly
what an anti-black-box tool should do.

**146/146 core tests passing.**

## [1.5.465] - 2026-07-10
### Audited — all 13 QC metrics, against the defect each names *and* the defects it must ignore
``data_qc_tools`` is the manuscript's enabling layer — the claim that PyCAT tells a scientist
*"can I trust this data, and if not, how do I improve it?"*. **Four bugs were fixed in it
(1.5.403–406) and it had zero tests.**

The test that matters for a QC metric is not *"does it return a number"*. It is: **does it move
when its own defect is present, and stay put when a different defect is present?** That second
half is what catches the failures that actually occurred — a focus score that rises with noise
is measuring noise (1.5.405); a vignetting score that reads cells-in-the-centre as bad
illumination is measuring object placement (1.5.404). **Both returned confident numbers.**

**Audited all 13. Eleven were correct** — including several I expected to be broken:

- ``qc_focus`` — catches a defocused frame, **ignores a noisy-but-sharp one.** The 1.5.405 DoG
  fix works exactly as designed, and the single-image ``'info'`` status is deliberate, not a
  failure (the absolute number is scene-dependent; it only judges *across* a stack).
- ``qc_snr``, ``qc_vignetting`` — both invariant to an 800-count pedestal and to clipping, both
  responsive to their own defect.
- ``qc_drift`` — fires on drift, ignores bleaching and noise.
- ``qc_nyquist`` — correct Abbe physics (d = λ/2NA), and it correctly flags Gable's 0.0264 µm/px
  as *oversampled*.

### FIXED — `qc_saturation` was blind to any clipping below the dtype maximum
``_dtype_max`` returned ``np.iinfo(uint16).max`` = **65535**. **A 12-bit camera writing into a
uint16 array clips at 4095.** A camera at reduced gain clips lower still. So the check found
**nothing**:

| image | truly flat-topped | reported |
|---|---|---|
| clipped at 65535 (the dtype max) | 0.0 % | 0.00 % good |
| clipped at 4095 (a 12-bit sensor) | **1.2 %** | **0.00 % good** |
| clipped at 1000 (gain-limited) | **9.1 %** | **0.00 % good** |

**Nine percent of the pixels destroyed, reported as "good"** — and saturation is the one defect
that *cannot* be recovered downstream. A clipped intensity is **gone**, and every measurement
built on it inherits a number that is not a lower bound on anything (1.5.392).

The ceiling is now detected **from the data**: a pile-up of pixels at *exactly* the image
maximum is the signature of a flat top, wherever the ceiling sits. Reported now matches truth to
two decimals (1.18 % and 9.14 %), and it does **not** fire on an unclipped image that merely has
a brightest pixel.

### FIXED — `qc_vibration` reported a pump that did not exist, on a bleaching stack
``phase_cross_correlation`` is *supposed* to be intensity-robust. **It is not robust enough when
the frame is globally scaled** — the sub-pixel peak fit is biased by the moving DC term and
noise floor.

A photobleaching stack **does not move at all**, and it drove ``qc_vibration`` to **p = 0.010,
status "bad"** — a confident report of a *periodic vibration source*. The shift trace was
tracking the exponential intensity decay, which is smooth and monotonic and therefore
concentrated in the low-frequency bins: **exactly the signature the permutation test looks for.**

**The user is sent to check their pumps and fans, and the stage is fine.**

Fixed by z-scoring each frame before correlating (``_shift_normalise``), applied at all three
phase-correlation sites. Bleaching now reads *good*, and **no sensitivity is lost**: a periodic
oscillation still fires (p = 0.005), random jitter still does not (p = 0.519), and drift still
fires on ``qc_drift`` (bad) while correctly *not* firing on ``qc_vibration``.

**That discrimination is the point.** A metric that fired on any motion would send the user
hunting for a vibration source when the real problem is a drifting stage — a different cause,
and a different fix.

**143/143 core tests passing.**

## [1.5.464] - 2026-07-10
### Added — `tests/imaging_realism.py`: the audit's validation layer 2
The external audit specified a three-layer validation framework and asked every quantitative
method to declare which layer it had reached:

> **Implemented → Analytically validated → Simulation validated → Experimentally validated**

**"Analytically validated" is a low bar, and PyCAT was mostly at it.** A method tested only on a
clean synthetic scene has been tested against a microscope that does not exist.

The audit named eleven degradations that separate a clean scene from a real acquisition —
*Poisson noise, sCMOS read noise and offset maps, blur and axial defocus, illumination
gradients, photobleaching, drift, finite exposure motion blur, pixelation, saturation, object
overlap, segmentation errors.*

**Eight of those eleven have already broken a real PyCAT measurement, each found the hard way,
one bug at a time:**

| degradation | what it broke, measured | release |
|---|---|---|
| sCMOS pedestal | Kp 30 → **5.8**; N&B number inflated **120×** | 1.5.422/453 |
| saturation | Kp of 655, 1500 and 4000 **all read 655** | 1.5.392 |
| PSF blur (the halo) | client enrichment 30 → **14.9** | 1.5.460 |
| photobleaching | FRAP t½ **2.5× too fast**, R² = 0.94 | 1.5.455 |
| drift | MSD α → **1.91**, reported as *superdiffusion* | 1.5.456 |
| illumination gradient | vignetting QC measured object placement | 1.5.404 |
| segmentation error | over-inclusive mask: Kp 30 → **4.4** | 1.5.459 |
| Poisson noise | N&B shot-noise floor is B = 1, not 0 | 1.5.453 |

**I have been rediscovering the auditor's list one bug at a time instead of building it.** This
module is that list, assembled.

Each degradation is a function of a **physically meaningful parameter** — ``pedestal`` in counts,
``drift`` in px/frame, ``bleach_tau`` in frames — and ``acquire()`` composes them **in the order
a microscope applies them**: the sample bleaches, the stage drifts, the object smears during the
exposure, the optics blur, the photons arrive (Poisson), the sensor adds gain/offset/read-noise,
the ADC clips, the sensor bins.

*Applying the pedestal before the Poisson draw would make the pedestal itself noisy — which is
not what a camera does, and would understate the damage. A noisy pedestal at least carries
variance; the real one does not, and that is exactly why it destroys N&B.*

### Added — `tests/test_imaging_realism.py`: 9 tests, methods measured through a real acquisition
**The bar: recover the truth, or refuse. Never return a confident wrong number.**

- **Kp = 29.44 at pedestals of 0, 100, 500 and 2000** — through Poisson noise, gain, pedestal
  *and* read noise. The pedestal-independence claim is now **earned**, not asserted.
- **Saturation is refused**, not reported: three scenes with true Kp of 30, 70 and 200 — a
  **seven-fold range** — are indistinguishable once the dense phase clips. All three return NaN,
  with 4/4 droplets flagged.
- **The PSF degrades Kp visibly and boundedly**: 29.44 → 28.00 → **24.81** as blur rises to
  2.5 px. The annulus gap (1.5.423) holds the loss to 17 % where the *ungapped* enrichment lost
  50 %.
- **The transfection gate is camera-independent**: the same 3-of-4 cells, and the same 0.75
  fraction, at every pedestal. This is the 1.5.415 failure — where a mean/background *ratio*
  called **every transfected cell untransfected** on a 500-count sensor.

### Fixed — a validation claim was being printed next to a NaN
Building the harness surfaced this immediately: on a saturated image the partition verdict read

> *"Kp = **nan**. … Kp is pedestal-independent. **Validated: 29.65 recovered against a true
> 30.0**"*

The 1.5.462 scoping guard did not catch it, because the claim **is** correctly scoped. The
problem is different, and worse: **the number it describes does not exist.** A validation claim
attached to a NaN tells the user the machinery is sound at the exact moment it has refused to
answer — and invites them to go looking for the number somewhere else.

**135/135 core tests passing.**

## [1.5.463] - 2026-07-10
### Validated — the VPT chain reproduces the 8.325 reference on real data. No regression.
The scientific-audit work of releases 398–462 touches the VPT path in several places (the
lag-window gate, the localisation-offset term, identifiability, drift correction). **It has now
been checked against the real bead file, and it did not regress anything.**

With the settled acquisition parameters and the correct measurement configuration:

| linker | gap | tracks | D (µm²/s) | α | **η (Pa·s)** |
|---|---|---|---|---|---|
| GREEDY | 1 | 118 | 0.000273 | **0.930** | **7.969** |
| BAYES | 1 | 118 | 0.000273 | **0.930** | **7.969** |

**η = 7.97 against the 8.325 reference — a 4 % difference — from a fully automated chain**, with
α = 0.930 (Brownian). The reference required TrackMate with manual trajectory pruning; **this
does not.** That is the standing goal, and it is met.

Recorded in ``docs/audits/DEV_NOTES.md`` with the exact reproducing settings, so it is not lost
again.

### Documented — this file's metadata is untrustworthy at every depth short of per-frame timestamps
``3_30_hr_1_MMStack_Pos0_ome2.tif`` is a MicroManager acquisition re-saved through ImageJ, which
**stripped the per-image metadata**. What survives contains **two different, both-wrong answers
and no right one**:

- ``"Interval_ms": 0.0`` — the field that is *supposed* to hold the cadence. **Zero.**
- ``"Acquisition comments: 500ms interval"`` — a **free-text human note**. It reads as
  authoritative, it is the only number in the file that looks like an interval, and **it is
  wrong**: the true cadence is 100 ms.
- ``"CustomIntervals_ms": []`` — empty.

**Reading 500 ms where the truth is 100 ms inflates the reported viscosity five-fold.**
``_extract_frame_interval_s`` already returns ``(None, None)`` rather than guessing, and already
rejects ``Interval_ms: 0``. That behaviour is now documented with this worked example so it is
never relaxed. **A plausible-looking interval from a summary field or a comment is not
evidence.**

### Note — the false alarm was mine, and it was a failure to search, not a failure to reason
I first measured **12–17 Pa·s** and reported it as a possible regression from the audit work. It
was not. Two mistakes, both mine:

1. **I linked ALL detections instead of the singlets.** The measurement requires
   ``select_bead_population(det, 'singlet')`` — folding in ``out_of_plane`` (17 % of detections)
   and ``aggregate`` beads puts motion in the MSD that does not belong there. **That is the
   whole of the 12–17 vs 8.0 gap.**
2. **I did not search my own records.** The pixel size (0.067 µm/px — an earlier 0.67 was a 10×
   error, i.e. **100× in the MSD**), the frame interval, the linking distance, the gap setting
   and the singlet filter were **all** in the transcripts and notes. I scraped a metadata comment
   instead, grepped one file, found a *retracted* number, and asserted a conclusion from it —
   including telling Gable his recollection was wrong. **It was right.**

*Search the record before running anything or asserting anything. It is the ground truth, and
speculation dressed as analysis costs real time.*

### Found — `gap=1` still matters, and that is a real result
An off-by-one in the gap check (``t - last_frame <= max_gap_frames``) was fixed to
``<= max_gap_frames + 1``, so ``gap=0`` now means *"link consecutive frames"*. It would be
reasonable to assume ``gap=1`` is therefore redundant.

**It is not.** ``gap=0`` → 243 tracks, α = 1.05, **η = 10.1**. ``gap=1`` → 118 tracks, α = 0.930,
**η = 7.97**. Bridging a *single* missing frame nearly halves the track count and moves the
viscosity by 21 %.

**The detection still drops beads (~15 %, per the 2026-07-09 analysis), and gap-closing recovers
the tracks.** The apparent superdiffusion at ``gap=0`` (α = 1.05) is **fragmentation, not
drift** — it disappears when the fragments are bridged.

## [1.5.462] - 2026-07-10
### Added — a guard against unscoped reassurances, and the one the last fix missed
Having found the same failure twice — in someone else's message (1.5.459) and then in my own
(1.5.461) — I went looking for the rest rather than waiting to trip over a third.

**And found that the 1.5.459 correction had not reached the second copy of the same claim.**
``partition_coefficient_local`` was fixed to suppress its confident verdict when the mask looks
bad. ``partition_measurement`` carried the identical unscoped text — *"Kp is pedestal-independent.
Validated: 29.65 recovered against a true 30.0"* — untouched, for two more releases.

*A true-but-unscoped claim gets fixed where you are looking and lives on where you are not.*

The scope is now **in the sentence**, not beside it:

> Kp is pedestal-independent — validated **against the PEDESTAL specifically**. **That is the
> only thing it is validated against.** It says nothing about the segmentation: an
> over-inclusive droplet mask collapses Kp by up to 7×, with the pedestal correction still
> perfectly sound.

### `tests/test_claim_scoping.py`
A user-facing message that says a number is **validated**, **exact**, or **independent** of
something must, *in the same message*, say **what it is not validated against**.

Verified **4/4** against the cases that matter:

| case | verdict |
|---|---|
| the actual bug (*"Validated: 29.65 recovered…"*) | **flagged** |
| the fix (scope stated) | allowed |
| a plain warning (no claim) | allowed |
| a scoped *"exact"* claim | allowed |

**This is not a style check.** A reassurance whose scope is unstated **is read as a
guarantee** — and it is more dangerous than no message at all, because it *actively suppresses
the user's own doubt.* Both bugs this week were found by a scientist noticing a number looked
wrong. A confident message is precisely the thing that stops them looking.

**126/126 core tests passing.**

## [1.5.461] - 2026-07-10
### Corrected — I called the contrast "exact". It is exact against the *pedestal*, not the *halo*.
1.5.426 introduced ``dense_dilute_contrast`` and described it, in three places, as **"exact — the
pedestal cancels in the difference."** I have repeated that reassurance in every release since.

**The first half is right and the second is a blanket claim that does not hold.**

The pedestal *does* cancel. But the contrast is **not immune to the PSF halo**, which corrupts
*both* terms — the dense mean is pulled **down** by soft edge pixels inside the mask, and the
dilute mean is pulled **up** by halo pixels outside it. Measured, **true contrast = 2900**:

| droplet edge | contrast | error |
|---|---|---|
| sharp | 2898 | **−0 %** |
| 1 px | 2773 | −4 % |
| 2.5 px *(realistic)* | 2560 | **−12 %** |
| 5 px | 2269 | **−22 %** |

Corrected in all three places, and pinned by a test that asserts **both halves** — that a sharp
edge recovers the contrast exactly on a 500-count pedestal (so the pedestal really does cancel),
**and** that a 5 px edge visibly degrades it. *The claim cannot drift back to the reassuring
version.*

### Note — this is the failure mode of 1.5.459, and I committed it myself
1.5.459 was about a message that was **true and incomplete**: *"Kp is pedestal-independent,
validated"* printed unchanged while an over-inclusive mask collapsed Kp by 7×. The statement was
correct — about the pedestal — and said nothing about the mask, and the user reads the
reassurance.

**I then did exactly that.** *"The contrast is exact"* is true about the pedestal, silent about
the halo, and I have been repeating it as a blanket guarantee for thirty-five releases. It took
measuring the halo's effect on the contrast — which I only did because ``field_summary`` returned
a number I could not explain — to notice.

*A caveat you attach to someone else's claim is easy. Noticing that your own reassurance has a
scope you never stated is the hard part.*

**125/125 core tests passing.**

## [1.5.460] - 2026-07-10
### FIXED — the enrichment's "dilute" reference was the PSF halo, and the option meant to help made it worse
**A droplet edge is not sharp.** The PSF gives it a halo, and the pixels *immediately outside*
the dense mask are **halo, not dilute phase**. Including them inflates the dilute reference and
collapses the enrichment.

Measured, **true enrichment = 30**:

| droplet edge width | dilute_mean | enrichment |
|---|---|---|
| sharp | 100.0 | **30.00** |
| 1 px | 113.0 | 25.54 |
| 2.5 px *(realistic)* | 130.0 | 20.66 |
| **5 px** | 163.1 | **14.86** |

**A realistic PSF halves the enrichment — and every real droplet has one.**

### And `dilute_dilation_px` — the parameter that exists to fix this — made it worse
It built the dilute shell **immediately adjacent** to the dense mask (``dilated & ~dense``) —
**which is the halo itself**, the worst possible choice. With a 2.5 px edge it took the answer
from 20.66 down to **2.86**:

| dilute region | dilute_mean | enrichment |
|---|---|---|
| **adjacent shell** *(the old behaviour)* | 1440.5 | **2.86** |
| gap 5 px, shell 6 px | 621.5 | 22.10 |
| **gap 10 px, shell 6 px** | 600.9 | **26.63** |

New ``dilute_gap_px`` steps **away** from the mask before sampling — the same annulus gap already
used by ``partition_coefficient_local`` (1.5.423). Using ``dilute_dilation_px`` without a gap now
warns and says exactly what it costs.

### Note — my scene exposed this, and I nearly dismissed it as my own error
The over-inclusive-mask sweep (1.5.459) showed ``client_enrichment`` returning **14.22 against a
true 30 even with a perfect mask.** My first reaction was that my synthetic droplets had an
unrealistically soft edge — *my scene's fault, not the code's.*

**That was the wrong instinct.** Real droplets have soft edges: that is what a PSF *is*. The
scene was right and the code was wrong. Testing across edge widths made it unambiguous — sharp
edges recover 30.00 exactly, and the error scales monotonically with the blur.

*A simulation that looks "too hard" is often just realistic. The N&B lesson (1.5.453) cuts both
ways: question the simulation — and then question the instinct to blame it.*

**124/124 core tests passing.**

## [1.5.459] - 2026-07-10
### FIXED — an over-inclusive droplet mask collapses Kp by 7×, and the code called it "validated"
The Manders finding (1.5.458) — *a mask covering too much of the frame breaks the measurement* —
generalises. The partition coefficient is worse.

Kp = I_dense / I_dilute. **If the mask spills past the droplet, it pulls dilute-phase pixels
into the "dense" average**, so I_dense falls and Kp falls with it. Measured, true Kp = 30, true
droplet radius 13 px:

| mask radius | Kp reported | CV inside the mask |
|---|---|---|
| **13 px** *(true)* | **29.61** | 0.016 |
| 20 px | 19.93 | 0.421 |
| 30 px | 9.46 | 0.807 |
| **50 px** | **4.41** | 0.902 |

**A 7× collapse — and the function printed *"Kp is pedestal-independent. Validated: 29.65
recovered against a true 30.0"* the whole way down.**

That message is not false. Kp **is** pedestal-independent, and that **was** validated — against
the *pedestal*. It says nothing about the mask. **The reassurance was true and the number was
wrong**, which is the worst combination there is.

### Detectable from the data alone, with no ground truth
A clean dense mask has a **low coefficient of variation** — every pixel in it is dense phase. An
over-inclusive mask mixes in dilute pixels and the CV rises: **0.016 → 0.807**, a 50-fold
separation, monotonic in the error. That is now checked and warned on.

**And the confident verdict is suppressed when the mask is suspect.** A reassurance printed
alongside a warning is worse than no reassurance: *the user reads the one that agrees with
them.*

The test asserts the guard is silent on a correct mask, too — *a warning that cries wolf will be
ignored when it matters.*

**122/122 core tests passing.**

## [1.5.458] - 2026-07-10
### Fixed — a Manders threshold below the background reports M = 1.0 on pure noise
Manders' coefficients are computed from **binary masks**, and the masks come from a
**threshold**. If that threshold sits *below the background*, the mask covers the whole frame —
and then **every pixel is "positive" in both channels**, so M1 = M2 = 1.0: *perfect
colocalisation, of noise.*

Measured on a scene where channel 2 overlaps exactly **half** of channel 1's puncta (true
M1 = 0.5), background = 20:

| threshold | M1 | M2 | mask coverage |
|---|---|---|---|
| **10** *(below background)* | **1.000** | **1.000** | **100 %** |
| 15 *(below background)* | 0.957 | 0.960 | 96 % |
| 20 *(~ the background)* | 0.543 | 0.569 | 55 % |
| **40** *(correct)* | **0.474** | 0.930 | 4 % |

**You would be reporting perfect colocalisation of noise, with no indication anything is
wrong.** A mask covering more than half the frame now warns.

### Note — Manders is more robust than its reputation, and saying so matters
I expected the textbook result: *"Manders is notoriously threshold-sensitive"*. **Above the
background it is not.** From threshold 30 to 70 — more than a factor of two — M1 moves only from
0.459 to 0.485, converging on the true 0.5. The coefficients are stable across any sensible
choice.

**The failure is entirely at the low end**, and it is a *different* failure from the one the
reputation describes. Had I written the guard from the reputation rather than from measurement,
I would have built a threshold-sensitivity sweep — useful, but not the thing that actually goes
wrong — and missed the case that silently reports 1.0.

The guard does not cry wolf on a correct threshold; the test asserts that too, *because a warning
that fires on good data will be ignored when it matters.*

**121/121 core tests passing.**

## [1.5.457] - 2026-07-10
### Fixed — whole-frame Pearson measures the cell shape, not colocalisation
Continuing the *"what does my simulation assume that real data does not?"* sweep. My
colocalization test used **independent uniform noise per pixel**. Real channels share a **cell
shape** — both are bright inside the cell and dark outside.

**That shared structure alone saturates Pearson.** Measured on channels that are **completely
independent** — zero real colocalisation — both carrying the same cell shape:

| scene | whole frame | **cell ROI** |
|---|---|---|
| **independent** *(r should be 0)* | **0.987** | **0.011** |
| 50 % co-localised | 0.997 | **0.712** |
| fully co-localised | 1.000 | 1.000 |

**Over the whole frame all three read ~0.99.** No colocalisation and half colocalisation are
**indistinguishable**. Pearson asks *"are both channels bright in the same places"*, and the
biggest structure they share is the cell.

An ROI covering more than 95 % of the frame now warns and says so. Inside a cell ROI the shared
shape is gone (every pixel is in the cell) and Pearson measures the real correlation —
**verified, not asserted**, and the test's failure message says: *if the ROI does not rescue the
metric, the advice in the warning is hollow.*

**The camera pedestal, by contrast, does not matter here** — Pearson is invariant to an additive
offset. A pedestal of 500 leaves r unchanged at 0.010. Worth stating, because it is the opposite
of the partition coefficient (1.5.422), where the pedestal is the whole problem. **The same
artifact does not affect every metric, and assuming it does is its own error.**

### Note — fusion was checked and is clean
The same question applied to ``fit_aspect_ratio_relaxation``: *what if the first frame is caught
LATE, after the relaxation has already begun?* Fusion is fast, so this is a real risk.

**τ survives it.** An exponential is memoryless — ``exp(-(t+Δ)/τ) = exp(-Δ/τ)·exp(-t/τ)`` — so a
late start rescales the **amplitude**, not the time constant. Measured: catching the first frame
a full time constant late still recovers **τ = 9.56 against a true 10.0** (only ``AR_0`` is
understated, 1.75 against 3.0). Since **η/γ comes from τ**, the physics survives.

And ``characteristic_length_um`` defaults to ``None``, which makes ``η/γ`` return ``NaN`` rather
than a silent wrong number. Nothing to fix.

**120/120 core tests passing.**

## [1.5.456] - 2026-07-10
### Fixed — stage drift is reported as SUPERDIFFUSION, and nothing said so
Asking *"what does my simulation assume that real data does not?"* — the question that found the
FRAP bleaching bug (1.5.455) — applied to MSD. **My simulation assumed no stage drift.**

**Confinement is guarded** (1.5.401): it pulls α *down*, and a probe hitting a wall was being
reported as "subdiffusion". **The opposite direction was not** — and in bead tracking it is the
more common artifact.

Drift is **ballistic**: a stage moving at speed v contributes ``(v·τ)²`` to the MSD, which grows
as τ² and pushes α toward 2. **And the slower the probe, the worse it is**, because the drift
term is compared against a smaller diffusive signal.

**In a viscous condensate this is severe — which is exactly the VPT regime.** For η = 8 Pa·s and
a 100 nm bead, Stokes–Einstein gives **D = 0.00027 µm²/s**, a near-stationary probe:

| stage drift | D uncorrected | α | D corrected | α |
|---|---|---|---|---|
| 0 | 0.000259 | 1.03 | 0.000253 | 1.03 |
| 0.02 µm/s | 0.000294 | **1.62** | 0.000253 | **1.03** |
| 0.05 µm/s | **0.000809** (3×) | **1.91** | 0.000253 | **1.03** |

**Fifty nanometres per second of stage drift triples D and drives α to 1.91** — which a reader
takes as directed, active transport. It is the stage. And R² does not move (0.993 throughout).

``motion_type`` is read straight off α, so this was being reported as ``'superdiffusion'`` with
no caveat. It now warns, and names the cause.

**And the advice is verified, not asserted** (the 1.5.453 / 1.5.455 lesson): ``drift_correct_com``
— already applied in the VPT pipeline — **recovers both D and α exactly, at every drift level
tested.** That check is now a test, whose failure message reads: *if this fails, the advice in
the superdiffusion warning is hollow.*

### Relevance to the open VPT problem
This is the classic reason a VPT viscosity comes out **too low**: uncorrected drift inflates D,
and η = kT/(6πRD) is *inversely* proportional to D. The pipeline does apply the correction — but
anything fitting an MSD outside it, or any residual drift after it, produces exactly this
signature, and now says so.

**119/119 core tests passing.**

## [1.5.455] - 2026-07-10
### FIXED — FRAP with no reference region: the half-time was 2.5× too fast, reported confidently
Finishing the simulation audit (1.5.454) turned up a bug I had not been looking for.

My 1.5.446 FRAP simulation assumed **no acquisition bleaching** — but every frame of a real
recovery bleaches the sample a little more, so **the plateau sags.** The fit reads that as a
**faster recovery to a lower plateau**, and both the half-time and the mobile fraction are
corrupted.

Measured, true t½ = 8.0 s and mobile fraction 0.875:

| acquisition bleaching | t½ fitted | mobile | R² | identifiable |
|---|---|---|---|---|
| none | 8.57 | 0.880 | 0.988 | True |
| τ = 600 s (mild) | 6.10 | 0.765 | 0.985 | True |
| **τ = 200 s (typical)** | **3.24** | **0.602** | 0.942 | **True** |
| τ = 60 s (severe) | 0.19 | 0.278 | 0.217 | False |

**At entirely typical acquisition bleaching the half-time is 2.5× too fast and the mobile
fraction 31 % too low — reported confidently, flagged identifiable, with R² = 0.94.** Neither
the fit statistic nor the confidence interval catches it, **because the curve still fits.**

### The fix was already in the module, and was optional
``photofading_correction`` — the standard reference-region correction — has been there all
along. The pipeline called it **only if a reference mask was supplied**, and when it was not, it
silently used a correction factor of 1 and carried on.

It now warns, and states exactly what the omission costs.

**And the advice is verified, not asserted.** Supplying a reference recovers the truth:

| | t½ | mobile |
|---|---|---|
| uncorrected | 3.24 | 0.602 |
| **corrected (reference supplied)** | **8.91** | **0.855** |
| *true* | *8.00* | *0.875* |

That check is now a test. Its failure message says: *if this fails, the advice in the
no-reference warning is hollow — it tells the user to supply a reference, and that must actually
fix the problem.* (The lesson of 1.5.453, where I nearly shipped advice I had not confirmed
worked.)

### The audit, closed
Every finding this session that rests on a simulation I wrote has now been re-derived under a
more realistic model:

| release | claim | verdict |
|---|---|---|
| 1.5.446 | FRAP t½ unidentifiable on short windows | **holds** — short windows are flagged with or without bleaching |
| 1.5.447 | MSD D/α coupled, R² *rises* as the window shrinks | **holds** |
| 1.5.450 | 42 % of coarsening series called "arrested" | **holds** — 40 % under additive noise too |
| 1.5.451 | photobleaching window check | **wrong** — cried wolf on a floor; fixed in 1.5.454 |
| 1.5.453 | N&B "20× bug" | **wrong** — my simulation had a fixed occupancy; the estimator is correct |

Two of five were wrong, **and both were caught by questioning the simulation rather than the
code.** That is the habit worth keeping.

**117/117 core tests passing.**

## [1.5.454] - 2026-07-10
### Fixed — the photobleaching window check cried wolf on good movies with a floor
The N&B episode (1.5.453) — where a badly-posed simulation nearly led me to "fix" a correct
estimator — prompted an audit of every finding this session that rests on a simulation I wrote.
Two were worth re-deriving.

**The coarsening finding (1.5.450) holds.** My test used *multiplicative* noise (``R × (1+ε)``);
real radius error is *additive* (segmentation uncertainty, independent of object size). Re-run
with additive noise, the old ``R² < 0.3`` criterion **still false-arrests 40 % of genuinely
coarsening series** at 2 µm scatter. The bug and the fix are independent of the noise model.

**The photobleaching check (1.5.451) was wrong.** My test used ``I = A·exp(-t/τ)`` — decaying to
*zero*. Real bleaching leaves a non-bleaching floor (autofluorescence, an immobile fraction),
which never decays, so ``-log(I_end / I_start)`` **saturates and understates the decay.** With a
50 % floor, a **perfectly adequate 2 τ movie** (τ fitted = 53.4 against a true 50) reported an
observed window of **0.50** and fired the SEVERE warning.

Both bounds are now reported, and the check is monotonic — a shorter movie never looks better
than a longer one.

### Note — six attempts, five of them wrong, and the sixth is only honest
Recording all of them, because the next person will try the same ones.

1. **``movie_length / τ_fitted``** — circular. On a movie a fifth of the true bleach time, τ
   fits to 11 s (true 50), so the ratio is 10/11 = **0.9** and the check *passes*. **The
   quantity being checked against is the thing that is wrong.**
2. **``-log(I_end / I_start)`` on the raw intensity** — the 1.5.451 version. Cries wolf on any
   sample with a floor (above).
3. **Subtract the fitted ``I_inf``.** I argued this was safe, on the grounds that ``I_inf`` is
   anchored by the last frames while τ needs curvature — **then measured it, and I was wrong.**
   On a 0.2 τ movie ``I_inf`` fits to **771 against a true 200.** It is just as badly determined
   as τ, and the circularity bites exactly where it matters.
4. **Subtract ``I_min``** — overstates the decay; the minimum is a noise excursion.
5. **Compare the exponential fit to a LINEAR fit** (*"did the curve turn over?"*). The right
   question in principle — but the R² gap came out **non-monotonic** with the window (0.057,
   0.013, 0.016, 0.015 for 2.0, 1.0, 0.5, 0.2 τ) and does not separate them.
6. **Take the max of bounds (2) and (3)** — also not monotonic. The over-fitted floor-subtracted
   bound *rescues* a bad movie, so a 0.5 τ movie came out **quieter than a 1.0 τ one.** A metric
   that ranks a worse movie above a better one is not a metric.

**There is no single scalar here that is both floor-robust and non-circular.** The warning now
fires on the no-floor bound — the conservative one — and **states its known weakness**: it
understates the decay when the sample has a large floor, so it will warn on some adequate movies.
It reports both bounds and tells the user to compare them.

*A loud, honest, occasionally-wrong warning beats a silent wrong number. And after six attempts,
saying so is more useful than shipping a seventh tuning.*

**116/116 core tests passing.**

## [1.5.453] - 2026-07-10
### Fixed — N&B labelled `brightness` as "apparent" but left `number` unlabelled
``brightness`` carries ``brightness_kind='apparent'`` and fires a warning when the camera is
uncalibrated. **``number`` carried nothing** — and it is the more dangerous of the two, because
**it looks like a molecule count.** ``N = mean / B``, so an uncalibrated B makes N uncalibrated
too. ``number_kind`` now travels with it, on the same basis.

The warning now names N explicitly, and states the thing that actually matters: **the offset
adds to the mean but not to the variance**, so it drags B down and inflates N.

### Note — I nearly "fixed" a correct estimator on the strength of a bad simulation
I simulated ``Poisson(N · ε)`` photons per pixel and found N&B reporting **N = 200 against a
"true" 10**. That looked like a 20× bug, and I had already written the alarming changelog entry.

**It was not a bug.** With a *fixed* occupancy there is **no molecular fluctuation at all** —
every "molecule" is indistinguishable from shot noise, so ``var = mean``, ``B = 1``, and
``N = mean`` is the **correct** answer to the question I asked. The simulation was wrong, not the
code.

The molecular signal in N&B lives in the fluctuation of the **occupancy** — molecules entering
and leaving the volume. Simulated properly, the estimator is exact:

| true | recovered |
|---|---|
| N = 10, ε = 5 | **B = 6.01** (expect 6.0), **N = 8.30** (expect 10) |
| N = 5, ε = 20 | **B = 21.06** (expect 21.0), **N = 4.75** (expect 5) |

**The +1 in B is the shot-noise floor**: a perfectly monomeric sample reads B = 1, not B = 0,
because a Poisson emitter's variance equals its mean. **That floor is precisely what a monomeric
reference calibrates away** — which is why an absolute oligomeric state cannot be claimed without
one, and why the uncalibrated output is labelled *apparent*.

Locked in by ``tests/test_nb_estimator.py``, whose failure message says: *if this fails, check
the simulation before the code.*

### Note — ``git checkout`` in the sandbox reverts to the v1.5.329 baseline, not to HEAD
A botched edit left ``nb_tools.py`` unparseable, and I reached for ``git checkout`` — which
**wiped 140 lines of this session's work**, because the sandbox git is pinned at v1.5.329.

Recovered from the shipped ``pycat_1.5.394_changed.zip``. **The shipped ZIPs are the backup; the
sandbox git is not.** Noted, and not to be repeated.

**116/116 core tests passing.**

## [1.5.452] - 2026-07-10
### FIXED — the CI-faithful runner was stubbing compute dependencies. It was not faithful.
Gable asked why CI keeps going red on things that pass locally. The answer is not git, and it is
not the release ritual. **It is that my pre-ship check has been lying to me**, and this release
is about stopping that.

``tools/run_core_tests.py`` (1.5.433) exists to run the suite *the way CI runs it* — its own
docstring says **"no conveniences"**. And it contained:

```python
for name in ('pywt', 'SimpleITK', 'cv2', 'matplotlib'):
    try:
        importlib.import_module(name)
    except ImportError:
        stub = types.ModuleType(name)          # <- fabricate it
        sys.modules[name] = stub
```

**That is precisely the mechanism that produced the red builds:**

| release | package | what happened |
|---|---|---|
| 1.5.442 | ``largestinteriorrectangle`` | stubbed locally → imported fine → **red in CI** |
| 1.5.444 | ``scikit-learn`` | stubbed locally → imported fine → **red in CI** |

The module imports cleanly against a fake package, so the missing dependency is invisible until
the real environment rejects it.

**And it has been running that way all session.** ``pywt`` and ``SimpleITK`` are **not installed
in the sandbox at all** — every *"115/115 passed"* was reported with two real dependencies faked.

### What it does now
It reads the packages CI installs **from the workflow** (not a hard-coded list — a
hand-maintained copy of a derivable fact drifts, per 1.5.445), checks each is genuinely
importable, and if any is missing it prints:

```
==============================================================================
  WARNING: THIS IS NOT A FAITHFUL CI RUN
==============================================================================
  These packages are installed by CI but are ABSENT here, and are being
  STUBBED. Any import error they would have caused is invisible in this run:
    pywt  (pip install pywavelets)
    SimpleITK  (pip install simpleitk)
```

The tests still run — a sandbox with no network cannot install them, and a degraded run is
better than none. **What it must not do is report a clean result and let me believe it.** A green
result under that banner does not mean the import surface is clean; only ``pytest -m core`` in CI
can say that.

### The rule
*A test environment that differs from the real one in a convenient way will hide exactly the
bugs that matter.* I wrote that in 1.5.409, wrote it again in 1.5.432, and **built the tool that
was still doing it.** Recording a lesson is not the same as enforcing it.

## [1.5.451] - 2026-07-10
### Fixed — a movie shorter than the bleach time cannot measure the bleach time
``fit_photobleaching`` produces τ, and **the bleach correction divides by exp(-t/τ)** — so every
corrected intensity downstream inherits any error in it, and **the error compounds exponentially
with time.**

Measured, true τ = 50 s, 30 realisations:

| movie length | τ (sd) | mean R² |
|---|---|---|
| 100 s (2 τ) | 49.6 (0.8) | 0.993 |
| 50 s (1 τ) | 48.9 (1.7) | 0.988 |
| 25 s (½ τ) | 42.6 (8.6) | 0.971 |
| **10 s (⅕ τ)** | **35.2 (18.9)** | 0.881 |

R² stays high throughout. **And the consequence is far larger than the scatter suggests** — the
over-correction of the *final frame*:

| movie | τ fitted | over-correction |
|---|---|---|
| 100 s | 50.0 | −0.0 % |
| 25 s | 25.0 | **+63.5 %** |
| **10 s** | **11.0** | **+95.6 %** |

**The correction nearly doubles the final intensity** on a movie a fifth of the bleach time.

τ now carries a 95 % CI (the 10 s movie gives **[−1.8, 23.9]** — it does not exclude a *negative*
time constant), and the observation window is reported and warned on in two tiers, set from the
measured bias (under 5 % from ~0.8 τ onward, −15 % at ½ τ, **−30 % at ⅕ τ**).

### Note — the check must not use the fitted τ, and my first two attempts did
**That is circular, and it let the worst case straight through.** On a movie a fifth of the true
bleach time, τ fits to **11 s** — so ``movie_length / τ_fitted`` = 10/11 = **0.9**, and a check
against the fitted τ **passes**. *The quantity being checked against is itself the thing that is
wrong.*

Subtracting the fitted ``I_inf`` was no better: on a short movie ``I_inf`` is also badly
determined, and it **scrambled the ordering** — a 0.5 τ movie came out looking *better observed*
than a 0.6 τ one.

The non-circular test uses the **raw** intensities: *how far did the signal actually fall?*
``exp(-1) = 0.368``, ``exp(-0.5) = 0.607``. That is a property of the data, not of the fit. The
reported window now tracks the truth — 1.80, 0.91, 0.55, 0.41, 0.16 for movies of 2.0, 1.0, 0.6,
0.5 and 0.2 τ.

**115/115 core tests passing.**

## [1.5.450] - 2026-07-10
### FIXED — 42 % of genuinely coarsening series were being reported as "arrested"
``fit_coarsening`` decides whether a condensate population is coarsening (Ostwald ripening,
R ~ t^⅓; or coalescence, R ~ t^½) or **arrested** — kinetically trapped, not growing at all.
That is a **mechanistic conclusion about the sample.**

It was decided partly by::

    is_arrested = (max(ostwald_r2, coalescence_r2) < 0.3      # <- a FIT statistic
                   or abs(radius_change) < 2.0 * noise)

**R² measures how well a power law describes the data. It says nothing about whether the radius
grew.** Noise destroys R² while the radius keeps growing — so a genuinely coarsening series is
reported as *"no coarsening happened."*

Measured on synthetic data where the radius genuinely grows **3.7-fold**. Rate of calling
"arrested":

| data | scatter | R² < 0.3 | ΔR < 2σ | **slope test** |
|---|---|---|---|---|
| **coarsening** *(should be 0 %)* | 0.20 | 12 % | 12 % | **0 %** |
| **coarsening** *(should be 0 %)* | 0.30 | **42 %** | 38 % | **0 %** |
| arrested *(should be 100 %)* | any | 100 % | 98 % | **100 %** |

**At 30 % scatter, 42 % of genuinely coarsening series were called arrested.**

The honest question is *did the radius grow, given how noisy the measurement is?* — a question
about the **slope and its standard error** (a linear regression of R on t), not about how well a
power law fits. **False arrests drop from 42 % to 0 %, and every genuinely arrested series is
still caught.**

Guarded by ``tests/test_coarsening_arrest.py``, which asserts both directions — no false arrests
on coarsening data, and no lost sensitivity on arrested data. **109/109 core tests passing.**

### Note — the second clause was also wrong, and my first fix was too
``abs(radius_change) < 2.0 * noise`` looks like the right test — it compares the growth to the
scatter — and it fails the same way: ``np.std(np.diff(R))`` is the *frame-to-frame* scatter,
which grows with the noise, so ``2 × noise`` eventually exceeds the real growth. It false-fires
on 38 % of coarsening series at 30 % scatter.

I had drafted a fix that kept clause B and removed only clause A. **Measuring it showed clause B
was nearly as bad.** Both had to go.

Separately, I spent a pass strengthening the *failed-bootstrap* caveat — and then found that
**every NaN in the data came from the arrested-growth path, not the bootstrap.** The fix was
correct but landed in a branch that was not firing. The real bug was one level up.

## [1.5.449] - 2026-07-10
### Added — the fusion relaxation reports τ with its interval, and η/γ inherits it
``fit_aspect_ratio_relaxation`` discarded its covariance like the others. τ now carries a 95 %
CI, and **η/γ — the viscosity-to-surface-tension ratio — inherits it exactly**, being τ divided
by a constant. *A ratio without an interval is not a measurement.*

The interval widens honestly with the noise (true τ = 10 s):

| noise on AR | τ | R² | 95 % CI on τ |
|---|---|---|---|
| 0.03 | 9.81 | 0.998 | [9.54, 10.09] |
| 0.10 | 9.41 | 0.980 | [8.54, 10.28] |
| 0.30 | 8.48 | 0.860 | [6.16, 10.81] |
| 0.60 | 7.56 | 0.647 | **[3.47, 11.65]** |

### Note — I expected the FRAP failure here, measured for it, and it is NOT there
This is worth recording, because the negative result is the finding.

**This fit is robust to a short observation window.** I assumed the same failure as FRAP
(1.5.446) and the MSD fit (1.5.447) — a parameter unconstrained by too little data, with R²
staying high — and measured it. With a true τ of 10 s, **even half a relaxation time of data
recovers 10.10 ± 0.46.** The relaxation is a clean single exponential with a large amplitude
(the aspect ratio falls from ~3 to 1), so a short window still pins τ.

**What degrades it is noise — and here R² tracks the problem honestly** (0.998 → 0.647 as the
scatter in τ grows). That is the *opposite* of the FRAP and MSD cases, where R² stayed high or
even *rose* while the parameter became unconstrained.

So the covariance is captured to give τ an interval, but **no hidden failure mode is being
caught here.** R² is a reasonable guide for this fit, and saying so plainly matters: **the point
of these checks is to find where a statistic misleads, not to attach one everywhere.**

I had drafted the opposite claim before running the numbers. It would have been wrong.

**102/102 core tests passing.**

## [1.5.448] - 2026-07-10
### Wired — the interval on D now reaches the viscosity
1.5.447 made ``fit_anomalous_diffusion`` report a 95 % CI on D. And ``viscosity_measurement``
**already knew how to propagate it** — it has taken a ``D_ci`` argument all along.

**Nothing was passing it.** The interval was computed, the consumer could take it, and the two
were never connected. Exactly the trap from 1.5.421: *a fix that is not wired in is not a fix.*

Stokes-Einstein is ``η = kT / (6πRD)``, so the interval propagates **exactly**, and it
**inverts** — a LOW D gives a HIGH viscosity, so the viscosity interval is *not symmetric* about
the point estimate. The full chain, measured (bead radius 0.1 µm, 24 °C, true D = 0.05 µm²/s):

| lag window | D (95 % CI) | viscosity (95 % CI) |
|---|---|---|
| 30 lags | 0.0473 [0.0353, 0.0594] | 0.046 Pa·s [0.037, 0.062] — **1.7×** |
| 12 lags | 0.0489 [0.0380, 0.0598] | 0.045 Pa·s [0.036, 0.057] — 1.6× |
| 6 lags | 0.0504 [0.0440, 0.0568] | 0.043 Pa·s [0.038, 0.049] — 1.3× |
| **4 lags** | 0.0510 [0.0349, 0.0671] | 0.043 Pa·s [0.032, 0.062] — **1.9×** |

**A factor of 1.9 between the ends of the interval — on the number that goes into the paper.**

### Added — ``viscosity_interval_from_diffusion``, and the unbounded case
A CI on D that **includes zero** means the viscosity is **unbounded above**: η diverges as
D → 0. That is a real finding, not an error — *the data does not exclude an arbitrarily thick
medium* — and it now says so rather than reporting a confident point estimate.

The caveat from 1.5.447 travels with the number: the CI on D is honest at long lag windows and
**over-confident at short ones** (claims 95 % coverage, delivers 78 % at four lags), so the
viscosity interval is a **lower bound** on the true uncertainty, not an upper one.

Guarded by ``test_viscosity_carries_the_interval_the_msd_fit_supports``, which also asserts the
**inversion** — that the upper end of the viscosity interval comes from the *lower* end of D's.
**102/102 core tests passing.**

## [1.5.447] - 2026-07-10
### Fixed — the MSD fit discarded its covariance, so D was reported without an interval
The FRAP covariance bug (1.5.446) was not isolated. **Nine ``curve_fit`` calls in
``condensate_physics_tools`` discard ``pcov``** — including ``fit_anomalous_diffusion``, which
produces the **D that becomes the viscosity**.

**D and α are strongly coupled in MSD = 4·D·τ^α**: a larger α trades against a smaller D and
fits almost as well, so a short lag window cannot separate them. Measured, true D = 0.05 µm²/s,
α = 1.0, 10 % noise, 30 realisations:

| lag window | D (sd) | α (sd) | mean R² |
|---|---|---|---|
| 30 lags | 0.0496 (0.0035) | 0.99 (0.08) | 0.958 |
| 12 lags | 0.0495 (0.0025) | 1.06 (0.13) | 0.968 |
| 6 lags | 0.0539 (0.0110) | 1.17 (0.35) | 0.969 |
| **4 lags** | 0.0561 (**0.0224**) | 1.14 (**0.40**) | **0.973** |

**The scatter in D grows six-fold, the scatter in α five-fold — and R² goes UP.** R² rewards
fitting fewer points; it says nothing about whether those points determine the parameter.

D now carries the interval the data supports. The 4-lag fit — **the highest R² of the four, at
0.996** — has a 95 % CI on D of **[0.035, 0.067]**: a ±32 % range on the number that becomes a
viscosity.

### Note — I tried twice to reduce this to a pass/fail flag, and both attempts were wrong
The textbook test (*CI wider than the value itself*) is far too lenient: that ±32 % interval
passes it comfortably.

**Tightening the threshold to 0.5 made it worse, not better.** The flag then fired on 30-lag
fits (5 % judged identifiable) *more often* than on 4-lag fits (30 %) — because the relative CI
width across lag windows is **non-monotonic**: 0.66, 0.46, 0.55, 1.65 for 30, 12, 6 and 4 lags.
It is not a clean function of the lag window, because the D–α trade-off shifts with the window
in a way a single scalar does not capture.

**No threshold on this quantity separates good fits from bad ones**, and picking one to make the
table look right would be fitting the metric to the answer. So the flag is gone and the interval
is reported.

What *is* true and checkable is the coverage — how often does the reported 95 % CI actually
contain the true D?

| lag window | coverage |
|---|---|
| 30 lags | 100 % |
| 12 lags | 98 % |
| **6 lags** | **84 %** |
| **4 lags** | **78 %** |

**The CI is honest at long windows and over-confident at short ones** — it claims 95 % and
delivers 78 %. That caveat is documented alongside the number.

*A number with an honest interval is more useful than a flag with a dishonest threshold.*

**101/101 core tests passing.**

## [1.5.446] - 2026-07-10
### Fixed — FRAP reported half-times the data could not determine, with R² = 0.99
``fit_frap_recovery`` did ``popt, _ = curve_fit(...)``. **The second return value is the
parameter covariance** — and it is the only thing in the fit that knows whether the data can
*determine* the parameters at all. It was being thrown away.

**R² cannot answer that question.** It measures how well the curve fits the points you *have*;
it cannot know that those points do not *constrain* the parameter. Measured, with a true
half-time of 8.0 s and 2 % noise, over 30 noise realisations:

| observation window | t_half (sd) | mean R² |
|---|---|---|
| 60 s, 40 pts | 7.9 (0.7) | 0.982 |
| 20 s, 20 pts | 8.0 (1.4) | 0.984 |
| **8 s, 10 pts** | **10.6 (6.6)** | 0.978 |
| **4 s, 6 pts** | **12.6 (9.9)** | **0.963** |

**At a four-second window the half-time is 12.6 ± 9.9 — essentially unconstrained — and R² is
0.963.** The fit also returns a mobile fraction of **1.209**, which is physically impossible.

**The covariance already knew.** The 95 % CI on the half-time at that window is **[−0.2,
15.1]** — it does not even exclude a *negative* half-time. That is the definition of *"this data
cannot determine this parameter"*, and it was available all along.

Every parameter now carries the interval the data actually supports:

| window | t_half | R² | identifiable? |
|---|---|---|---|
| 60 s | 8.57 | 0.988 | **yes** — CI [7.4, 9.8] |
| 8 s | 5.98 | 0.984 | **no** — CI [2.6, 9.4] |
| **4 s** | 7.43 | **0.991** | **no** — CI **[−0.2, 15.1]** |

A parameter whose CI is wider than its own value triggers a warning that says so plainly, names
the usual cause (*an observation window shorter than the recovery — you cannot measure a
half-time you did not wait for*), and states that **R² being high is not a contradiction**.

Guarded by ``test_frap_reports_when_the_data_cannot_determine_the_half_time``. **101/101 core
tests passing.**

### The pattern, again
This is the fifth instance of the same failure this session: **a fit statistic used as a quality
gate answers a different question from the one being asked.** R² asks *"does the model explain
the variance?"* — not *"is the parameter determined?"*, not *"is the model right?"*, not *"is
this in focus?"*

The fix is never a better threshold on R². It is to compute the quantity that actually answers
the question — here, the covariance the fit was already producing and discarding.

## [1.5.445] - 2026-07-10
### FIXED — ``networkx`` was imported at module scope and never declared
Chasing the CI dependency drift (1.5.442, 1.5.444) to its root: I compared **every** third-party
import in the codebase against what ``pyproject.toml`` declares.

**Thirteen packages are imported but not declared. Twelve of them are fine** — they are LAZY
imports, inside the function that uses them (``cupy``, ``stardist``, ``imagej``, ``lumicks``,
``h5py``, ``shapely``, ``tifffile``, …). A package imported inside a function is an *optional
feature* that degrades gracefully when absent. That is a design choice, not a bug.

**One is a real bug.** ``fibril_tools`` imports ``networkx`` at **module scope** — the
skeleton-graph analysis is built on it — and it was **never declared**. It has worked everywhere
only because ``scikit-image`` depends on networkx, so it arrives **transitively**. If skimage
ever drops that dependency, **fibril analysis breaks for every user, on a clean install, with no
warning.**

*A transitive dependency you rely on is a dependency you have not declared.* Now declared.

### Added — ``test_no_undeclared_module_scope_imports``
A module-scope import is a **hard** requirement: it fails at import time, on a clean install,
before any user code runs. Lazy imports are exempt, deliberately.

Verified against the bug it was written for: remove the ``networkx`` declaration and it goes red
(exit 1); restore it and the suite is green. **100/100 core tests passing.**

### Note — the guard produced 23 files' worth of false positives, from a bug inside itself
My first version mapped ``skimage`` → ``"scikit-image"`` while the declared set had already been
normalised to ``scikit_image`` (hyphens to underscores). So ``skimage``, ``cv2`` and ``sklearn``
— all three **correctly declared** — appeared as undeclared, in 23 files.

**A normalisation mismatch inside the guard itself.** Caught because the number was
*implausible*: three of the most obviously-declared packages in the project cannot all be
missing. When a check fires on something that cannot be true, **the check is what is broken.**

## [1.5.444] - 2026-07-10
### FIXED — CI could not import three modules: ``scikit-learn`` was missing from the install
``segmentation_tools`` imports ``RandomForestClassifier`` at module scope, and
``two_channel_coloc_tools`` and ``timeseries_condensate_tools`` import ``segmentation_tools`` —
so **all three** inherit it. None could be imported in the headless job.

**The dependency had always been there.** It was invisible because ``segmentation_tools``
imported napari at module scope and **could not be imported at all**, so its own dependencies
never surfaced. **Decoupling the science (1.5.439) exposed a dependency that had been hiding
behind a GUI import for the life of the project.** The same thing happened one release earlier
with ``largestinteriorrectangle`` (1.5.442).

### Added — ``tests/test_ci_dependencies.py``: the install list is now DERIVED, not maintained
The CI install list was hand-maintained, and it **drifted twice in two releases**. Both times the
sandbox had *stubbed* the missing package, so it looked fine locally and went red in CI.

This test walks the module-scope imports of every module the headless test guards — following
``pycat.toolbox`` imports **transitively**, which is how the two time-series modules inherit
sklearn from segmentation_tools — and fails if any is absent from the workflow's install step.

*A hand-maintained list of a derivable fact will drift.* This derives it.

### Note — my first version of the guard was checking a COMMENT
It searched the raw workflow text for the pip name, and **passed even with the ``pip install
scikit-learn`` line deleted** — because the comment I had written *above* that line still
contained the words "scikit-learn".

Caught by testing the guard against the bug it was written for: delete the line, and it must go
red. It didn't. It now reads only the actual ``pip install`` commands, with trailing comments
stripped. Verified: **installed → passes; line removed → fails; restored → passes.**

*A guard that cannot fail is not a guard — and a guard that reads its own documentation as
evidence is worse, because it looks like it works.*

**99/99 core tests passing.**

## [1.5.443] - 2026-07-10
### FIXED — the pixel size silently defaulted to 1 µm/px, in ten places
With every science module now headless (1.5.442), the guards can finally see all of the code.
Running the silent-fallback check (1.5.437) across the newly-visible modules found the same bug
it was written for — **duplicated ten times.**

``_mpx()`` is defined **ten times** across the codebase, in two forms, and **both silently
default to 1.0 µm/px**: eight UI copies via ``.get('microns_per_pixel_sq', 1.0)``, and two in
``_tools`` modules via ``except Exception: return 1.0``.

The caller cannot distinguish *"the pixel size is 1.0 µm"* from *"the lookup failed"* — and 1.0
is a perfectly plausible pixel size, so **nothing looks wrong.** It is not a harmless default.
**Every length and every area in the output is scaled by it:**

| true µm/px | true area (µm²) | with fallback 1.0 | error |
|---|---|---|---|
| **0.0264** (Zeiss 63× oil) | 0.348 | 500.0 | **1435×** |
| 0.1 (typical 100×) | 5.000 | 500.0 | 100× |
| 0.67 (the bead videos) | 224.45 | 500.0 | 2× |

**A 1435× overestimate of every area on the Zeiss 63× data — reported as an entirely
normal-looking number.**

New ``pycat/utils/pixel_size.py``, one canonical accessor:

- ``pixel_size_um`` returns **NaN** when the pixel size is unknown, and warns. A NaN
  propagates: an area computed from it is visibly NaN, rather than wrong by three orders of
  magnitude and looking fine.
- ``pixel_size_um_or_default`` is available where a number is genuinely required — but it
  **warns that the output is in PIXEL units, not microns**, so the assumption is on the record
  instead of silent.

Wired into the two ``_tools`` copies the guard flagged. Guarded by ``tests/test_pixel_size.py``,
which asserts NaN on all six failure modes (missing, ``None``, zero, negative, unparseable, no
repository), the exact value on a valid one, and that the explicit default still warns.

**98/98 core tests passing.**

### The eight UI copies
They use ``self._dr().get('microns_per_pixel_sq', 1.0)`` — the same silent default, reached by
a different route. They are next; the canonical accessor exists now, and the pixel-size gate
(``add_pixel_size_gate``, confirmed working on 2026-07-09) is the intended front line. This
release closes the two that had no gate in front of them at all.

## [1.5.442] - 2026-07-10
### Decoupled — the last three science modules. Every analysis function now imports headlessly.
**24 GUI-coupled science modules → 15 → 8 → 7 → 6 → 3** — and the remaining three
(``data_viz_tools``, ``general_image_tools``, ``video_export_tools``) are **genuinely UI, with
zero analysis functions between them.** The decoupling is complete.

- **``spatial_acf_tools``** — **1 of its 14 objects** used a GUI symbol (a widget builder). The
  other 13 — the spatial autocorrelation analysis itself — were blocked behind it.
- **``two_channel_coloc_tools``** and **``timeseries_condensate_tools``** — each hid their
  analysis behind ``QThread`` workers. ``class Worker(QThread)`` resolves its base class **at
  class-definition time**, which runs at import, so the Qt import cannot simply move into a
  method. Five workers are now built in lazy factories, cached after first use.
  ``timeseries_condensate_tools`` also had a **nested** ``_UpWorker(QThread)`` inside a widget
  builder — already lazy by virtue of nesting; it just needed the names in scope.

**90/90 core tests passing.**

### Note — the CI-faithful runner earned its keep
``spatial_acf_tools`` passed in my sandbox and **failed in the real headless test**:
``largestinteriorrectangle`` is a declared dependency but **not part of the minimal compute set
CI installs**, and a module-scope import made the whole module unimportable there.

**My sandbox had stubbed the package**, so it looked fine locally. ``run_core_tests.py``
(1.5.433) runs without those stubs — which is the entire reason it exists — and caught it
immediately. Now imported lazily, inside the single function that uses it.

*This is the third time the "sandbox more forgiving than CI" trap has appeared (1.5.409,
1.5.432, here). The difference is that this time it was caught before the push.*

## [1.5.441] - 2026-07-10
### Decoupled — ``ts_cellpose_tools``, and the transfection filter is tested for the first time
**24 GUI-coupled science modules → 15 → 8 → 7 → 6.**

``filter_cells_by_transfection`` decides **which cells are analysed at all** — it runs before
everything else, so a mistake there is a selection effect on the *entire dataset*. Until 1.5.415
its SNR was a bare **ratio**, and the camera pedestal appears in both the numerator and the
denominator:

| pedestal | expr = 60 | expr = 200 | transfected fraction |
|---|---|---|---|
| 0 | **KEEP** | KEEP | 0.50 |
| 100 | drop | KEEP | 0.25 |
| **500** | **drop** | **drop** | **0.00** |
| **2000** | **drop** | **drop** | **0.00** |

**On a camera with a 500-count pedestal, every transfected cell was called untransfected.**

That fix was measured against synthetic data — but **nothing in the codebase exercised it**,
because the module imported napari and Qt at scope and could not be imported without a GUI.
**Neither analysis function in it uses a single Qt symbol**; the import was pure overhead that
stopped them being tested.

Now decoupled — the ``QThread`` worker is built in a lazy factory (its base class resolves at
*class-definition* time, so the import cannot simply move into a method), and Qt is imported
inside the one widget builder that needs it.

New ``tests/test_transfection_filter.py`` asserts **pedestal invariance** at 0, 100, 500 and
2000 counts. Verified: the untransfected cell is dropped, all three real cells kept, and the
transfected fraction is **0.75 at every pedestal**. **84/84 core tests passing.**

### Note — my first attempt broke the file, and the guard caught it
I tried injecting lazy imports line-by-line before each use. It placed an ``import napari``
*inside a multi-line Qt expression* and produced a ``SyntaxError``.

Reverted, and looked at the structure instead: **only two objects in the module use Qt at all**
— the worker class and one widget builder. The analysis functions use none. That made the fix
obvious and safe: defer the import into those two, and leave everything else alone.

*Line surgery on a file you have not read the structure of is a way to break it.*

## [1.5.440] - 2026-07-10
### Fixed — the puncta filter threw objects away and told nobody why
Eight conditions decide which detections survive into **every downstream count**. The reason a
detection failed was computed for each dropped object and then **discarded** — unless
``PYCAT_REFINE_DEBUG=1`` was set, and even then it was ``print``ed to a console a napari user
never sees.

**So a user whose puncta silently vanished had no way to find out why.** That is the same class
of failure as the dead SNR gate itself (1.5.416): the pipeline was making a consequential
decision and not telling anyone.

The summary is now **always** produced::

    Puncta refinement: 2 of 5 detections rejected.
    Reasons: local_intensity (2), local_snr (2).

And the dangerous case **escalates to a warning**:

| dropped | level |
|---|---|
| 2 of 5 | info |
| **4 of 5 (80 %)** | **warning** |
| **5 of 5** | **warning** — *"EVERY detection was rejected. That usually means a threshold is wrong for this data rather than that the puncta are all spurious — check min_spot_radius against the pixel size, and the SNR thresholds against the image contrast."* |

**Without that message a user simply concludes "there are no puncta in my cells."** The
threshold is ``>= 0.8``, not ``> 0.8``: four of five *is* eighty percent, and someone losing
four fifths of their detections should hear about it.

### Added — ``test_puncta_filter_reports_what_it_rejected``
Asserts the summary is produced **and names the conditions that fired**, so a wrong threshold
can be traced to the exact check rather than guessed at. **78/78 core tests passing.**

### Note — my first version of the test patched the wrong name
It replaced ``show_warning`` on ``pycat.utils.notify``, and saw nothing. But
``segmentation_tools`` does ``from pycat.utils.notify import show_warning as
napari_show_warning`` — which **copies the reference at import time**, so patching the source
module has no effect on the already-bound name.

A real limitation of the test, not the code. It now patches the name *as bound in the module
under test*.

## [1.5.439] - 2026-07-10
### Decoupled — ``segmentation_tools``, and the puncta filter is tested for the first time
**24 GUI-coupled science modules at the start of this work → 15 → 8 → 7.**

``segmentation_tools`` holds **16 pure analysis functions** — the puncta refinement filter,
local thresholding, the SNR/contrast gates, watershed splitting — and a handful of viewer
functions. A module-scope ``import napari`` (used only for ``isinstance(layer,
napari.layers.Image)`` inside the ``run_*`` functions) blocked the headless import of **all
sixteen**, so CI could never see any of them. ``cellpose`` and ``pycat.ui.ui_utils`` were the
same story. All are now imported lazily, inside the functions that use them.

### Added — ``tests/test_puncta_refinement.py``
**The puncta refinement filter had never been tested.** It decides which detections survive into
every downstream count, and its SNR gate was found **completely dead** in 1.5.416 —
``object_mean / bg_std <= 1.0`` never fires on any camera with a positive background, so **two of
its five quality conditions had never rejected anything, on any image, for the life of the
pipeline.**

That fix was measured against synthetic data, but nothing in the codebase exercised it. Now
something does. Five detections in one cell on a 500-count pedestal — three real puncta
(amplitudes 80, 80, 40) and two labelled regions with **no signal added at all**:

| | result |
|---|---|
| real puncta kept | **3 / 3** |
| spurious kept | **0 / 2** |

**The 1.5.416 fix works, and is now protected by a regression test.** **77/77 core tests
passing**, up from 60 this evening.

### Note — I misread the return value and briefly thought the filter was broken
My first run reported *"2 of 3 real puncta rejected"*. The function returns a **tuple**, and I
indexed ``out[0]`` as though it were the mask.

The ``PYCAT_REFINE_DEBUG=1`` flag settled it in one line: **only labels 4 and 5 were dropped** —
the two spurious ones — and it named the conditions that fired (``local_intensity``,
``local_snr``). The filter was correct all along; **my test was wrong.**

Worth noting the flag exists and works. It also exposes a real gap: the eight rejection reasons
are collected internally but **never surfaced** unless that environment variable is set, so a
user whose puncta silently vanish has no way to find out why. Recorded for follow-up.

## [1.5.438] - 2026-07-10
### Decoupled — 7 more science modules now import headlessly, unlocking Manders' coefficients
GUI-coupled science modules: **24 at the start of this work → 15 today → 8 now.**

**``obj_based_coloc_analysis_tools`` is the significant one.** It holds **12 pure analysis
functions** — Manders' M1/M2, object overlap, per-object colocalisation — and **one Qt dialog.**
The module-scope Qt import blocked the headless import of **all twelve**, so CI could never see
any of them.

The obstacle was that ``class obcaDialog(QDialog)`` resolves ``QDialog`` **at class-definition
time**, which runs at import — so the import could not simply be moved inside ``__init__``; the
*base class* needs it. The class is now built inside a factory on first use, and cached.

Manders is correct, and now verifiable in CI. On two ch1 objects with ch2 overlapping exactly
one of them:

| | value | expected |
|---|---|---|
| M1 (fraction of ch1 overlapping ch2) | **0.500** | 0.500 |
| M2 (fraction of ch2 overlapping ch1) | **1.000** | 1.000 |

Also decoupled: ``correlation_func_analysis_tools`` and ``layer_tools`` (each pulled in
``pycat.ui.ui_utils``, which imports napari, for a **single** function — now imported lazily
inside the function that uses it), plus ``clean_spot_detection_tools``, ``fd_curve_tools``,
``fft_bandpass_tools`` and ``intensity_profile_tools`` (the notification shim).

All seven are added to ``test_headless_science.py``, which now asserts they import with the GUI
stack genuinely absent. **74/74 core tests passing**, up from 60.

### Remaining
Eight modules still import napari or Qt at module scope. Three are genuinely UI
(``data_viz_tools``, ``video_export_tools``, ``general_image_tools`` — 0 pure functions between
them). The five with locked science are ``segmentation_tools`` (16 pure functions),
``spatial_acf_tools`` (5), ``ts_cellpose_tools`` (2), ``timeseries_condensate_tools`` (2) and
``two_channel_coloc_tools`` (1).

## [1.5.437] - 2026-07-10
### Fixed — a swallowed exception left a third of a small object's signal uncorrected, silently
``estimate_psf_sigma`` ended in::

    except Exception:
        return 1.0

The caller cannot then distinguish *"the PSF is 1.0 px"* from *"the estimation crashed"* — and
**1.0 is a perfectly plausible PSF width**, so nothing looks wrong.

**It is not a harmless default.** The PSF sigma is the **kernel of the partial-volume
correction**. With a true PSF of 2.5 px and a silent fallback of 1.0:

| object radius (px) | true bias | with fallback 1.0 | gap |
|---|---|---|---|
| 1.0 | −0.954 | −0.635 | 0.319 |
| **2.0** | −0.734 | −0.358 | **0.376** |
| 4.0 | −0.437 | −0.185 | 0.252 |

**Roughly a third of a small object's signal, left uncorrected — silently.**

The fallback is kept (the caller needs *something*), but the failure is now **visible**: it
warns, names the exception, and states what the fallback costs. Validated: a flat image (no
gradient to estimate from) warns and returns 1.0; a real image with a true PSF of 2.5 returns
**2.44**, silently. *The estimator was fine — only its failure mode was invisible.*

### Added — ``tests/test_silent_fallbacks.py``
**Most of the ~330 bare ``except Exception: pass`` handlers are harmless** — optional imports,
best-effort cleanup, GPU probes falling back to CPU. Swallowing there costs a *feature*, and
the user notices.

**The dangerous ones return a plausible value.** This guard forbids exactly that, and only in
the 14 modules whose job is to produce a number a scientist will report. Verified **4/4** on the
cases that matter:

| case | verdict |
|---|---|
| the actual bug (``return 1.0``) | **flagged** |
| the fix (warns, then returns) | allowed |
| honest failure (``return np.nan``) | allowed |
| honest failure (``dict(fit_success=False)``) | allowed |

It does **not** forbid fallbacks — it forbids **invisible** ones. Either warn, or return an
explicit failure (``NaN``, ``None``, or a dict with ``success=False``) so the caller can tell.

### Note — a survey, not a mass edit
An AST sweep classified every handler in the non-UI code:

| | count |
|---|---|
| bare ``pass`` on ``Exception`` | 328 |
| broad catch, no log | 270 |
| **logs or warns (fine)** | **151** |
| re-raises (fine) | 14 |

Of the 104 silent broad handlers *inside science modules*, most return ``NaN`` or
``fit_success=False`` — which is **correct behaviour**, and rewriting them would be churn.
**One returned a plausible number.** That is the one that was fixed, and the guard now prevents
another.

## [1.5.436] - 2026-07-10
### Changed — the dark reference is now an explicit choice, not an empty dropdown
Gable's observation, and it is the right one: **an empty dropdown reads as "you forgot
something", not "this is optional and here is what it costs."** The user tries to fill it, and
when they cannot, the bypass happens **silently** — they get a number without ever choosing to
accept a compromised one.

A checkbox now **owns the decision**, defaults to the correct behaviour, and states the
consequence **at the point where it is turned off**. Three paths, and the middle one is the
important one:

| user action | result | ``is_true_kp`` |
|---|---|---|
| **[x] use dark reference + layer chosen** | **Kp = 29.6** *(true 30)* | **True** |
| **[x] ticked, but NO layer chosen** | **blocked, with a warning** | — |
| **[ ] unticked** (deliberate bypass) | intensity ratio 5.77 | **False** |

**Ticked-but-empty is a mistake, not a choice**, so the widget refuses rather than silently
falling back to the uncorrected number — which is exactly the trap the checkbox exists to
close. Unticked *is* a choice, so it proceeds, returns the ratio so work is not blocked, and
labels it for what it is.

Unticking swaps the help text for an amber warning stating the cost in full: the result is an
**intensity ratio, not a partition coefficient**, biased toward 1 by an amount that **cannot be
recovered from the image** (in vitro every pixel contains the dilute phase, so there is no
fluorophore-free region to reference). **A true Kp of 30 reads as 5.8 on a 500-count pedestal.**
Use it for *relative* comparison between images acquired identically; do not report it as Kp.

The **contrast** (I_dense − I_dilute) is exact in every case — the pedestal cancels in the
difference — and is reported regardless.

## [1.5.435] - 2026-07-10
### FIXED — two requirements files were invalid for pip AND for conda, so Dependabot could not scan the repo at all
``config/requirements-arm-mac.txt`` line 2 read::

    pytorch==2.1.2=cpu_generic_py39hef92293_4

That is **conda** syntax (``name=version=build``) with an extra ``=``, copied from
``config/pycat-napari-env-arm-mac.yaml`` — where ``pytorch=2.1.2=cpu_generic_py39hef92293_4``
is correct. With the doubled ``=`` it is **valid for neither tool**: pip's parser rejects it
outright with ``InvalidRequirement``.

**So the file could never have been installed by anything.** It has been broken since it was
written, and nothing noticed — it is referenced by **no workflow, no ``pyproject.toml``, and no
documentation.** Dependabot was simply the first tool to try to read it, and it **aborted the
entire dependency scan on line 2**, which means **no security updates were being checked at
all.**

The same line appears in ``config/requirements-devbio-napari-arm-mac.txt``. Both fixed: on pip
the package is ``torch``, not ``pytorch``, and there is **no CPU build to select** — the macOS
arm64 wheels are CPU-only already. The conda YAMLs still pin the conda build string, which is
correct *there*.

All four ``config/requirements*.txt`` files now parse as valid pip.

### Added — ``tests/test_requirements_parse.py``
**A file that no build step reads is a file whose breakage is invisible** until an external tool
trips over it. This test reads them, so it is not invisible. Verified: reintroducing the conda
pin makes it fail; the suite is otherwise **60/60**.

### Note — this was not caused by anything in this session
Worth stating plainly. The failing job is **Dependabot**, not the ``core`` workflow, and the
broken line predates every change made here. It surfaced now because Dependabot runs on a
schedule, not on push.

## [1.5.434] - 2026-07-10
### FIXED — the FRAP test was asserting the wrong definition of mobile fraction
With a faithful runner in place (1.5.433), I ran the **30 science tests that CI collects but
never marks ``core``** — so ``pytest -m core`` had never run them. One failed on ground truth.

``synthetic_frap_curve`` built the plateau as ``b = i0 + mobile_fraction``, i.e. it assumed
**``mobile_fraction = b − a``**. That is the fraction of the **pre-bleach signal** that
recovers — **not the mobile fraction.**

The mobile fraction is *"of the molecules that were bleached, what fraction was replaced by
unbleached ones from outside?"*::

    the bleach REMOVED     1 − a
    the recovery RESTORED  b − a
    mobile fraction     = (b − a) / (1 − a)

With ``a = 0.2``, ``b = 0.9``: the bleach removed **80 %** of the molecules and **70 %** of the
pre-bleach signal came back — so **0.7 / 0.8 = 0.875** of the bleached pool was mobile.

The fixture called that **0.7**, and the test asserted 0.7 against ``fit_frap_recovery``'s
correct **0.875**. **The test was wrong, not the code** — and the fit was perfect throughout
(R² = 1.000000, half-time exact). The two definitions coincide only when the bleach is complete
(``a = 0``), which it never is.

Fixed: the fixture now places the plateau at ``b = a + mobile_fraction·(1 − a)``. The fit
recovers the requested value **exactly** at 0.30, 0.50, 0.70 and 0.90.

### Filled — two golden-master reference values that had never run
Both were left as ``None`` with a note to fill them, so both tests **skipped permanently**.
Measured rather than guessed:

**``EMPIRICAL_PARTIAL_OVERLAP_PEARSON = 0.8321``.** The scene is ``ch2 = 0.6·ch1 + 0.4·independent``,
for which the Pearson correlation is **analytic**: ``r = 0.6/√(0.6² + 0.4²) = 0.8321``. Measured
over 40 seeds: **mean 0.8319**, sd 0.0020. **Agreement to four decimal places** — so this is not
a characterisation of current behaviour but a check against a value derived *independently of
the implementation*. If the Pearson code is rewritten correctly, it still passes.

**``NOISY_FIT_MOBILE_TOL = 0.03``** at ``NOISY_FIT_NOISE_SIGMA = 0.02``. Measured over 50 seeds,
the error in the recovered mobile fraction: mean 0.0072, 95th pct 0.0146, **max 0.0184**. A
tolerance of 0.03 sits above the observed maximum without being so loose that a genuine
regression slips through — a 2× degradation fails it.

### Changed — the science tests are now ``core``-marked, and CI runs them
**24 science tests** (colocalization, FRAP, partition, VPT viscosity, feature analysis, image
processing) were collected by CI and **never executed**, because none carried the marker. They
now do. Verified in the CI environment: **59/59 passing** — up from 35 (the guards alone).

### Note — the runner invented four failures, and that is a bug in the runner
``run_core_tests.py`` first reported 41/45. Its pytest stub lacked ``raises`` and ``approx``,
and the repo root was not on the path (so ``tests.fixtures_synthetic`` would not import). All
four "failures" were its own.

**A runner that invents failures is worse than no runner** — it trains you to ignore it. The
stub is now faithful, and the suite is genuinely green.

## [1.5.433] - 2026-07-10
### Added — ``tools/run_core_tests.py``: run the CI suite the way CI runs it
Two guard bugs shipped in a row (1.5.428, 1.5.432) for the same reason: **the development check
was more forgiving than the runner it stood in for.** This makes the faithful run a script, so
the convenience cannot creep back in.

**What "faithful" means, and each point cost a red build:**

- **Execute the module; call the test functions; inject nothing.** The previous check ``exec``'d
  test *bodies* with paths injected into the namespace — so a bare ``SRC`` (the module defines
  ``_SRC``) was resolved by the harness and never looked up.
- **Block the GUI stack at the meta-path**, so the import raises a real ``ImportError``.
  ``sys.modules[x] = None`` produces misleading ``AttributeError``\\ s — it made four science
  modules look broken when they were fine.
- **Emulate ``pip install --no-deps -e .`` and nothing more.** A dev check doing
  ``sys.path.insert(0, 'src')`` hid the fact that PyCAT is a **src-layout** package that must be
  installed before ``import pycat`` works at all (1.5.409: *every* test failed).
- **Supply the parametrize values.** Skipping the parametrized tests left **five of eight
  unchecked** — and those are precisely the ones covering all 13 science modules and both
  spatial statistics.

**Verified against the bug that shipped.** Reintroducing the bare ``SRC`` makes the script fail
with the exact ``NameError`` that went red in CI, and exit 1. Restoring it: **35/35 passed**
(3 + 26 + 6 parametrized cases), exit 0.

### The lesson, now enforced rather than written down
I wrote this in 1.5.409:

> *A test environment that differs from the real one in a convenient way will hide exactly the
> bugs that matter.*

Then did it again, in the same file, with a different convenience. **A lesson recorded in a
changelog is not a control.** The script is.

## [1.5.432] - 2026-07-10
### FIXED — the undefined-name guard shipped with an undefined name
``test_no_infinite_self_recursion`` (added in 1.5.428) referenced a bare ``SRC``. The module
defines ``_SRC``. **So the guard whose entire job is to catch ``NameError`` failed with a
``NameError``**, and CI went red on it.

**Why it was not caught locally, and this is the part worth fixing.** My sandbox check ran the
guard by ``exec``-ing the function *bodies* with paths injected into the namespace — so ``SRC``
was resolved by my harness and never looked up. **The check was more forgiving than the runner
it was standing in for.**

Running the module the way CI actually runs it — executing the file, calling the test functions,
injecting nothing — immediately found **a second instance of the same bug**:
``test_no_undefined_names`` called ``relative_to(_SRC)`` on files that are not under that root,
raising ``ValueError``. It would have gone red on the next push.

### Fixed — the guards now scan the tests as well as the source
The root cause is that the guards only scanned ``src/pycat``, **so they could not see their own
file.** They now scan both trees: **112 source files + 24 test files**, and both are clean.

*A guard that cannot see its own file is not a guard.*

### Note — the same lesson as 1.5.409, and I did not apply it
1.5.409 was caused by ``sys.path.insert(0, 'src')`` in my development checks — a convenience
that does not exist in CI, hiding a whole class of failure. I wrote at the time:

> *A test environment that differs from the real one in a convenient way will hide exactly the
> bugs that matter.*

Then I did it again, in the same file, with a different convenience. The fix is not resolve —
it is to **run the thing the way it will actually be run**, which is now what the sandbox check
does: execute the module, call the tests, inject nothing.

## [1.5.431] - 2026-07-10
### Gated — the three reachable intensity measurements, and a trap in the enrichment default
``field_summary``, ``partition_coefficient_field`` and ``bf_condensate_metrics`` are now gated
on ``ABSOLUTE`` semantics. All three report an intensity ratio or an optical density, so they
need the detector's zero point. Verified: each **runs normally on valid input** and **refuses a
CLAHE'd layer** — no existing caller breaks.

**Prioritised by reachability, not by count.** ``fit_bimodal_intensity`` and
``extract_spot_traces`` are **not reachable from any UI or batch path** (0 call sites in either),
so gating them would be theoretical. The three above have **6 reachable call sites** between
them.

### Found — ``client_enrichment`` is CORRECT, and its default is the trap
The maths is right: ``K = (dense − bg) / (dilute − bg)`` is a ratio of **differences**, and it
recovers the true value **exactly at any pedestal** — *provided the background is supplied*.

**The default is ``background = 0.0``, which silently asserts "there is no camera offset".**
Measured, with a **true K of 30**:

| pedestal | background not given | background given |
|---|---|---|
| 0 | **30.00** | 30.00 |
| 100 | 15.50 | **30.00** |
| 500 | **5.83** | **30.00** |
| 2000 | **2.38** | **30.00** |

**A 12× error, and the number looks perfectly plausible.** It now warns when the default is
used, naming what the assumption costs and how to supply the background (a dark frame, or a
signal-free region via ``background_mask``). If the image genuinely has no offset — because it
was already background-subtracted — the warning can be ignored, and it says so.

### The classification, complete
Every intensity measurement PyCAT exposes, with its requirement **measured against ground
truth**:

| measurement | requires | why |
|---|---|---|
| optical density, ``bf_condensate_metrics`` | **ABSOLUTE** | a log of a ratio to the true incident intensity |
| partition coefficient, ``field_summary`` | **ABSOLUTE** | a bare intensity ratio |
| N&B, molecule counting | **LINEAR** | variance vs mean — a constant adds to one and not the other |
| ``client_enrichment`` | **LINEAR** | a ratio of differences — but the offset must be *known* |
| Taylor-normalised FRAP | **MONOTONIC** | the offset cancels in a ratio of differences |

**Three different answers, none of them obvious.** Each was measured rather than assumed,
because getting one backwards blocks correct inputs while letting wrong ones through — strictly
worse than no gate at all.

## [1.5.430] - 2026-07-10
### Gated — molecule counting, and a correction to my own classification of FRAP
Extending the intensity-semantics gate meant **verifying each requirement rather than asserting
it**, and one of my assertions was wrong.

**Molecule counting requires LINEAR — confirmed.** It fits a variance-vs-intensity slope, and a
camera pedestal adds a constant to the **intensity** but nothing to the **variance**. The line
is shifted horizontally and the slope through the origin is wrong:

| pedestal | reported N (true N = 20) |
|---|---|
| 0 | 36.5 |
| 500 | 67.8 |
| **2000** | **89.8** |

A **2.5× inflation**. Gated. A background-subtracted image now passes (LINEAR is satisfied —
and the offset removal is *helpful* here), while a CLAHE'd one is refused.

### Corrected — FRAP is pedestal-INVARIANT, and I nearly gated it wrongly
I classified FRAP as requiring LINEAR. **That is wrong**, and gating it so would have blocked
perfectly valid inputs.

``taylor_normalize`` is ``(I − I₀) / (I_pre − I₀)`` — a **ratio of DIFFERENCES**. An additive
offset appears in both numerator and denominator and **cancels exactly**:

| pedestal | **taylor** (differences) | **prebleach** (bare ratio) |
|---|---|---|
| 0 | **0.8728** | 0.8983 |
| 500 | **0.8728** | 0.9322 |
| 2000 | **0.8728** | **0.9661** |

*(true mobile fraction 0.873)*

**The requirement belongs on the normalisation, not the module.** ``taylor_normalize`` needs
only MONOTONIC and is safe on an un-subtracted image; ``prebleach_normalize`` is a bare ratio,
the offset does **not** cancel, and it inflates the apparent mobile fraction toward 1. Both are
now documented with the measured numbers, so the choice between them is informed.

### Note — the classification is the hard part, not the plumbing
Three measurements, three different requirements, and the differences are not intuitive:

* **optical density** needs the pedestal **present** (``ABSOLUTE``) — it is a log of a ratio to
  the true incident intensity.
* **N&B and molecule counting** need the pedestal **removed** (``LINEAR``) — they compare
  variance to mean, and a constant adds to one and not the other.
* **Taylor-normalised FRAP** does not care (``MONOTONIC``) — the offset cancels in a ratio of
  differences.

**Getting these backwards would have blocked correct inputs while letting wrong ones through** —
strictly worse than no gate at all. Each was measured against ground truth before being wired.

## [1.5.429] - 2026-07-10
### Added — ``@require_intensity``, and the optical density is now gated
The intensity-semantics gate (1.5.427) protected only the partition coefficient. A
``@require_intensity`` decorator makes it one line per measurement, and ``compute_optical_density``
is the first to take it — because OD is a **log of a ratio**, so it needs the detector's zero
point more than anything else does. On min-max normalised data the two faintest condensates
returned **exactly 0.000** against true ODs of 0.046 and 0.301.

### Verified — N&B requires LINEAR, not ABSOLUTE, and the reason matters
Before gating anything, the classification had to be checked rather than asserted. N&B computes
brightness ``B = variance / mean``, and **the camera pedestal adds to the mean but not to the
variance** — it is a constant. So it drags B down and inflates N:

| trace | B | N |
|---|---|---|
| signal (no pedestal) | 1.02 | **49.0** |
| **with pedestal** | 0.09 | **5954** ← 120× wrong |
| **pedestal subtracted** | 1.02 | **49.0** ← restored |

Subtraction **restores it exactly**. So N&B needs the zero point to be right (``LINEAR``) and
does *not* care about the absolute scale — a background-subtracted image is exactly right for
it, and an unsubtracted one is wrong. That is the opposite requirement from optical density,
which needs the pedestal **present**.

The distinction is real, and getting it backwards would have blocked correct inputs while
letting wrong ones through.

### Note — a refusal must be CLEARER than the bug it prevents, not a different crash
The decorator first returned a dict carrying the reason. That is fine for a function that
returns a dict — and **wrong for one that returns an array.** ``compute_optical_density``
returns an ``ndarray``, so a caller doing ``od.mean()`` or ``od[mask]`` got an
``AttributeError`` on a dict: a crash, with no indication of why.

It now raises ``MeasurementRefused``, carrying the reason and naming the operation responsible.
An exception reaches the caller regardless of what the function normally returns.

**Caught by asking what the function actually returns before shipping the guard** — the same
question that has now found four bugs in this session (the FRAP model equation, the vibration
harness, the batch preprocessing input, and this).

## [1.5.428] - 2026-07-10
### Added — a guard for infinite self-recursion, and a sweep for more of them
``_add_image`` (fixed in 1.5.427) called **itself**:

```python
def _add_image(image, viewer, **kw):
    """Lazy wrapper for the viewer helper..."""
    return _add_image(image, viewer, **kw)      # <- ITSELF
```

**Every one of the 19 call sites in that module would have blown the stack.** It was meant to
call ``add_image_with_default_colormap``, and was almost certainly written during the headless
decoupling — a module-scope import replaced with a lazy shim, and the body never updated.

That is a specific, checkable pattern, and the guard now checks it. Verified against four
cases, **4/4 correct**:

| case | verdict |
|---|---|
| the actual ``_add_image`` bug | **flagged** |
| the fix (calls the real helper) | allowed |
| a *shadowed* delegate (see below) | allowed |
| legitimate recursion with a base case | allowed |

**The shadowing exception is real and had to be honoured.** ``ui_diagnostics_mixin`` has:

```python
def _add_pipeline_snr_analysis(self, layout=None):
    from pycat.toolbox.pipeline_snr_tools import _add_pipeline_snr_analysis
    _add_pipeline_snr_analysis(self, layout=layout)   # resolves to the IMPORT
```

A naive *"does it call its own name?"* check flags this; it is safe, because the local import
shadows the method. The guard checks for the shadowing import and does not flag it. **``_add_image``
had no such import — which is exactly why it recursed.**

### Swept — no other broken shims
The headless decoupling produced the ``_add_image`` shim, so I swept the whole tree for others
of the same shape (a small function whose job is to lazily import and delegate). **Twelve
candidates, all false positives** — they call module-level names or builtins that a crude
textual check cannot see, and the undefined-name guard already covers them properly.

``_add_image`` was the only genuine one, and it is now guarded against.

## [1.5.427] - 2026-07-10
### Added — intensity semantics: operations declare what they destroyed, measurements refuse it
Gable's proposal, and it is the right architecture. The normalisation bugs (1.5.424–426) were
instances of a general problem: **a measurement is only valid on an image whose intensities
still mean something**, and several routine preprocessing steps deliberately destroy that
meaning — because that is what they are for.

Measured on a droplet field with a **true Kp of 30**:

| image | I_dense | I_dilute | ratio |
|---|---|---|---|
| raw counts | 3500.1 | 600.0 | 5.83 |
| min-max normalised | — | — | **130.01** (and it swings with the noise) |
| after white top-hat | 2914.4 | **14.6** | **199.27** |
| **after top-hat + LoG** | 48.6 | **−4.1** | **−11.96** |
| after CLAHE | 1.000 | 0.015 | **64.77** |

**A Laplacian-of-Gaussian is a signed operator centred on zero**, so the dilute-phase mean goes
*negative* — and **a ratio of two numbers straddling zero is not a physical quantity at all.**
A negative partition coefficient.

New **``pycat/utils/intensity_semantics.py``**. Each operation declares what it did
(``ABSOLUTE`` → ``LINEAR`` → ``MONOTONIC`` → ``DESTROYED``); each measurement declares what it
requires; ``check_measurement_input`` refuses when the requirement is violated **and names the
operation responsible**.

Deliberately **not** a heuristic on the pixels. *A background-subtracted image looks like an
image with a dark background; a LoG output looks like a noisy image.* **The information is in
the provenance, not the pixels** — so it is recorded when the operation happens and read when
the measurement happens.

Validated end-to-end:

| input layer | Kp | refused? |
|---|---|---|
| raw | **29.61** | no |
| **Pre-Processed (top-hat + LoG)** | **NaN** | **yes** |
| **CLAHE** | **NaN** | **yes** |

Ten operations in ``image_processing_tools`` now tag their output. ``partition_coefficient_local``
checks. It **fails open** on untagged layers — with a warning saying so — because the tag system
is not yet everywhere, and refusing untagged input would break every existing workflow.

### FIXED — ``_add_image`` called itself: infinite recursion in 19 call sites
Found while wiring the tags. The lazy Qt-import shim in ``image_processing_tools`` was::

    def _add_image(image, viewer, **kw):
        return _add_image(image, viewer, **kw)      # <- ITSELF

It was meant to call ``add_image_with_default_colormap``. **Every one of the 19 call sites in
that module would have blown the stack** — every "add the processed image to the viewer" path.
Almost certainly introduced during the headless decoupling, when the direct import was replaced
with a lazy shim.

### Fixed — the batch in-vitro path was reading `preprocessed`, not the original image
``_ivf_droplet_mask_and_image`` did ``state.get('preprocessed', state.get('image'))`` —
**preferring** the preprocessed image, which is the top-hat + LoG + wavelet output. So the
partition coefficient was being computed on a signed, background-removed image. It now takes
the original.

Checked the other ten ``preprocessed`` call sites in the batch registry: **all are segmentation
or preprocessing steps, where using it is correct.** The in-vitro intensity path was the only
consumer that needed the original.

## [1.5.426] - 2026-07-10
### Fixed — the BATCH path normalised too, and ``field_summary`` called an intensity ratio a partition coefficient
Having found the normalisation bug in two UIs (1.5.424, 1.5.425), I swept every intensity-
measuring function against the accessors that feed it. Two more:

**1. ``field_summary`` reported ``partition_coefficient = I_dense / I_dilute``** — a bare
ratio, with no camera floor removed. Its own docstring said *"image in [0, 1]"*, **inviting**
the normalised input that made it worse than biased:

| noise sd | reported "partition" (true Kp = 30) |
|---|---|
| 2 | **323.5** |
| 5 | 130.0 |
| 15 | 44.0 |
| 30 | **22.5** |

**A 14× swing driven entirely by the exposure.** Renamed to ``intensity_ratio``, which is what
it is; ``partition_coefficient`` is kept as a deprecated alias so callers do not break, but it
carries the caveat. ``dense_dilute_contrast`` is added and is **exact** — the pedestal cancels
in the difference. The docstring now says **raw counts**, and explains why.

On raw counts the ratio is at least **stable** (5.83 at every noise level) — an honest,
uncorrected number, correctly named. For a real Kp, ``partition_coefficient_local`` with a dark
reference (in vitro) or a cell mask (cellular).

**2. The batch registry normalises.** ``_ivf_droplet_mask_and_image`` ran every image through
``_normalize_to_float`` — the same min-max — before handing it to ``field_summary`` and the
partition coefficient. **So every batch-processed in-vitro partition coefficient carried the
noise-dependent bug**, and batch is precisely where many files are processed unattended and
nobody looks at each number.

A ``_raw_counts`` accessor is added beside the normalising one, and the in-vitro intensity path
uses it. Verified safe: of the three callers of that accessor, only the intensity one uses the
image at all — the other two take the mask and discard it (``mask, _ = ...``).

### The pattern, stated once
**Min-max normalisation is correct for segmentation and fatal for intensity.** It maps the
image *minimum* to zero, which silently subtracts an uncontrolled floor — the darkest noise
pixel in that particular field. Every ratio built on it becomes a function of the exposure;
every logarithm of a ratio (optical density) becomes self-referential, scaled by the field's
own most-extreme object.

Four call sites across three modules and the batch registry were feeding intensity measurements
normalised data. They are now on raw counts, and the normalising accessors carry docstrings
saying what they are for.

## [1.5.425] - 2026-07-10
### Fixed — optical density was computed on min-max NORMALISED data, and the strongest condensates vanished
The normalisation bug found in the in-vitro widget (1.5.424) is not isolated. **Four UIs**
define a min-max normalising image accessor, and a sweep of their call sites found **three more
feeding an intensity measurement** — all of them optical density.

``OD = −log10(I / I0)``. It is a **logarithm of a ratio**, so moving the zero point does not
merely bias it — **it changes the functional form.** And min-max normalisation maps the image
**minimum** to zero, which in brightfield **is the strongest condensate**:

| condensate | true OD | **OD on normalised data** |
|---|---|---|
| faint (10 % absorption) | 0.046 | **0.000** |
| moderate (50 %) | 0.301 | **0.000** |
| strong (90 %) | 1.000 | 0.623 |
| very strong (98 %) | 1.699 | 1.173 |

**The measurement was self-referential:** every field's OD was scaled by its own most-absorbing
object. From raw counts, all four recover **exactly** (0.045 / 0.301 / 1.000 / 1.699).

Fixed in ``brightfield_ui`` (2 sites) and ``invitro_bf_ui`` (1 site). A ``_get_image_raw``
accessor is added alongside the normalising one, and the normalising accessor now carries a
docstring stating plainly that it is **for segmentation only**.

### Fixed — a negative transmitted intensity made the most absorbing condensates NaN
Found while validating the above. On a strongly absorbing condensate the transmitted intensity
is small — 98 % absorption of a 200-count background leaves ~4 counts — and **detector noise
then pushes individual pixels below zero.** ``log10`` of a negative number is ``NaN``.

**So the most strongly absorbing objects — the ones carrying the most signal — silently dropped
out of the OD image entirely.** The transmitted intensity is now clamped at a small positive
floor (one part in 10³ of the background, i.e. OD = 3, the ceiling the result is clipped to
anyway). The 98 % condensate now returns **1.699 against a truth of 1.699**, where it
previously returned ``NaN``.

### Fixed — `od_proxy = 1 - raw` was not an optical density
``invitro_bf_ui`` built its OD proxy as ``1 − raw``, which only means anything on [0, 1] data —
i.e. it *depended on* the normalisation bug. On raw counts it is negative and meaningless.
Replaced with the real thing: ``−log10(I / I0)``, with ``I0`` taken from the background.

This is not cosmetic. **Optical density is what relates a brightfield image to concentration**
(Beer–Lambert); ``1 − I`` is a linear proxy that is *monotonic* with OD but not *proportional*
to it, so any concentration or partition estimate built on it was distorted.

### Note — my own validation was wrong first, twice
The first OD test fed **no background image**, so the function inferred ``I0`` from a 50 px
local kernel that **partially contained the condensates** — biasing every OD low and producing
a NaN I nearly attributed to the code. That was the test's fault.

The second attempt supplied a proper background and revealed the *real* NaN — the negative
transmission. **Both were only visible because the test had ground truth to compare against.**
A validation without a known answer would have shown four plausible numbers and nothing wrong.

## [1.5.424] - 2026-07-10
### FIXED — every in-vitro partition coefficient was computed on min-max NORMALISED data
Wiring ``partition_coefficient_local`` (1.5.423) into the in-vitro widget exposed something
worse than the missing camera floor.

The widget fed the image through ``ui._img()``, which **min-max normalises to [0, 1]**. That is
fine for segmentation. **For an intensity ratio it is fatal**, and not merely biased:

Normalisation maps the image **minimum** to zero. In a droplet field the minimum is a **noise
excursion below the dilute phase** — so the denominator of Kp is driven toward zero by
whatever the darkest pixel in the field happened to be.

**The reported Kp was therefore a function of the noise level.** Measured with a **true Kp of
30** throughout:

| noise sd | reported Kp | spread over 8 seeds |
|---|---|---|
| 2 | **515** | 451–589 |
| 5 | **207** | 181–237 |
| 15 | **70** | 61–80 |
| 30 | **36** | 31–41 |

**A 14× swing driven entirely by the noise.** A *cleaner* image reports a *higher* partition
coefficient. **Two images of the same sample at different exposures report different partition
coefficients.** It was not a measurement of anything.

The widget now reads the layer **raw**, in counts. Intensity ratios require raw data — this is
the same class of error as an unsubtracted pedestal (1.5.415, 1.5.416, 1.5.422), except that
normalisation subtracts an *uncontrolled* floor rather than leaving a known one in place.

### Wired — the in-vitro widget now uses the local annular dilute phase and a dark reference
``partition_coefficient_local`` was built in 1.5.423 and **called nowhere** — exactly the trap
from 1.5.421. It is now wired into the in-vitro fluorescence widget, with:

- ``sample_type='in_vitro'`` — not a guess: this **is** the in-vitro widget, so the tool knows
  it must not try to auto-detect the camera floor (it cannot be done — see 1.5.423).
- a new **"Dark reference (buffer, no dye)"** layer dropdown, defaulting to *none*, with the
  reason stated in the widget: *one extra frame of buffer with no dye, at the same camera
  settings.*
- without one, the raw ratio is still returned so work can proceed — labelled **"raw ratio (NOT
  Kₚ — no dark reference)"** in the status line, and flagged ``is_true_kp=False`` in the
  result.
- the **contrast** (``I_dense − I_dilute``) is reported in every case and is **exact**, because
  the pedestal cancels in the difference.

### Note — this was found only by wiring the fix in
The camera-floor problem was diagnosed, solved and validated in 1.5.423 against synthetic data.
The normalisation bug was invisible there, because the synthetic tests called the function
directly with raw arrays.

**It surfaced the moment the function met the real call path.** Building the correct estimator
and validating it in isolation was not enough — the input it would actually be given was
broken, in a way that made the output depend on the exposure time.

## [1.5.423] - 2026-07-10
### Added — ``partition_coefficient_local``: a local dilute phase, and an honest camera floor
Kp = ``(I_dense − floor) / (I_dilute − floor)``. Get the floor wrong and Kp is dragged toward
1: with a **true Kp of 30**, a 500-count pedestal left in place gives **5.81 — an 81 % error
that looks like a plausible number.** 1.5.422 could only record this as *unchecked*. It is now
solved, with the resolution depending on the sample — because the physics does.

**The dilute phase is measured LOCALLY, from an annulus around each droplet** — not from a
global percentile, which assumes uniform illumination that a vignetted field does not have.

**And the annulus must be OFFSET from the droplet edge.** A phase boundary is not a step; it
has a finite interface width, and a ring drawn against the edge sits inside that gradient:

| gap from edge | ring − pedestal (true dilute = 100) |
|---|---|
| **0** | **491.8** ← inside the gradient, 5× too high |
| 2 | 206.0 |
| 5 | 110.7 |
| **10** | **100.3** ← converged |

Default gap is 3 × the estimated interface width, floored at 5 px.

### The camera floor: what is possible depends on the sample
**In cells, the extracellular region IS a dark reference** — there is no fluorophore outside
the cell, so that region contains the camera pedestal (and any medium autofluorescence, a real
floor you also want removed). Pass ``cell_mask``. The **median** of the outside region is used,
not the mean: the mean is dragged upward by cell-edge pixels (measured against a true pedestal
of 500 — **mean 548.2, median 504.0**). Same principle as the annulus gap: stay away from the
interface.

**In vitro, the floor CANNOT be auto-detected. Not by any method.** Droplets sit in bulk
buffer; every pixel is (pedestal + dilute) or (pedestal + dense). **No region of the image
contains the pedestal alone**, so the floor and the dilute phase are *inseparable in
principle*, not merely hard to separate. A dark reference — buffer with no fluorophore, same
camera settings — is the only thing that works, and it costs one extra frame.

``sample_type`` is now explicit (``'cellular'`` / ``'in_vitro'``), and in vitro without a dark
reference the tool **refuses** rather than guesses.

Validated (true Kp = 30):

| case | Kp | ``is_true_kp`` |
|---|---|---|
| cells + ``cell_mask`` | **29.54** | True |
| cells, no mask | NaN | False |
| **in vitro, no reference** | **NaN** | **False** |
| in vitro + ``dark_reference`` | **29.59** | True |
| in vitro + ``allow_no_reference`` | 5.77 | **False** |

``allow_no_reference=True`` returns the raw ratio so an analysis can proceed — flagged
``is_true_kp=False`` so it cannot be mistaken for a partition coefficient. The **contrast**
(``I_dense − I_dilute``) is reported in every case and is **exact**, because the pedestal
cancels in the difference.

### Note — I tried to auto-detect the floor with Otsu, and it failed catastrophically
Worth recording, because the failure was **silent and confident** — the worst kind.

In vitro there is no dark region, but Otsu split the image anyway, **returned the DILUTE PHASE
(600.9 counts) as the "camera floor"**, and gave **Kp = 5.77 against a true 30 — flagged
``is_true_kp=True``.** A separation test (*"is the high class 1.5× the low class?"*) did not
save it, because **dense/dilute is itself a 5× ratio**. Otsu cannot tell *"background vs cell"*
from *"dilute vs dense"* — **both are bimodal.**

**A heuristic cannot recover information the image does not contain.** The fix was not a better
heuristic; it was to tell the tool which sample it is looking at, and let it refuse.

## [1.5.422] - 2026-07-10
### Added — the partition coefficient as a ``Measurement``, with its assumptions CHECKED
The ``Measurement`` provenance model (1.5.384) covered only viscosity. Extended to the
partition coefficient — the backlog's highest-value target, and the one with the clearest
failure modes.

``partition_coefficient_field`` returns a number. ``partition_measurement`` returns the number
**with the conditions under which it means anything**, each computed from the data:

- **no saturation** — *checked*. A clipped dense phase does not give a lower bound on Kp: the
  numerator is truncated by an unknown amount, so the ratio is **meaningless, not
  conservative**. (1.5.392: with a bulk of 100 on a 16-bit sensor, a true Kp of 655, 1500 and
  4000 **all read as 655**.) A saturated image now returns ``NaN`` and
  ``NOT_INTERPRETABLE``.
- **background subtracted** — *asked, not guessed* (see below).
- **dilute phase measured locally** — *flagged*. A global percentile assumes uniform
  illumination, which a vignetted field does not have.

Validated: no false alarms across Kp = 3 to 300; saturation correctly rejected; an explicitly
unsubtracted image correctly rejected.

### Note — I tried twice to DETECT an unsubtracted background, and it cannot be done
This is worth writing down, because the failure is instructive rather than embarrassing.

**In a partition measurement the dilute phase IS signal** — it is the denominator. It is not a
background to be removed, and a low-Kp system legitimately has a dilute level close to the
dense one. **A camera pedestal and a genuine dilute phase produce exactly the same thing: a
floor above zero.** There is no signature to find.

Both heuristics failed, in both directions:

| attempt | failure |
|---|---|
| floor vs the dense/dilute **span** | flagged **every** low-Kp image (Kp = 3, Kp = 10) as unsubtracted — with **no pedestal at all** |
| floor vs the dense-phase **contrast** | still false-alarmed at Kp = 3, **and passed** a 500-count pedestal that had already dragged Kp from 30 to **5.8** |

So: **ask.** The caller knows whether they subtracted the background. If they do not say, the
assumption is recorded as ``checked=False, holds=None`` — *unchecked*, not silently assumed to
hold.

**And the consequence is worth being blunt about**, because it is large and invisible. An
unsubtracted pedestal appears in **both** the numerator and the denominator and drags Kp toward
1. On identical droplets with a **true Kp of 30**:

| pedestal | reported Kp |
|---|---|
| 0 | **30.0** |
| 100 | 15.5 |
| 500 | **5.8** |
| 2000 | **2.4** |

**A 12× error that looks like a perfectly plausible number.** This is the same failure as the
transfection filter (1.5.415) and the puncta SNR gate (1.5.416) — an unsubtracted offset in a
ratio — and here it silently rewrites the thermodynamics.

## [1.5.421] - 2026-07-10
### Fixed — two corrected estimators had never been called, so the fixes had never run
Having been bitten twice by *"shipped but never wired"* (1.5.419, 1.5.420), I swept for it: an
AST scan of every function added during this audit, counting real call sites. Four were
unwired, and **two of them were the corrected estimators.**

**1. The size-distribution fit.** ``fit_size_distribution_mle`` was added in 1.5.379 — and
nothing called it. Both in-vitro UIs and the batch registry were still running the old
``fit_size_distribution``, which offers **only two candidates** (lognormal, power-law) and
picks between them by an R² on a **binned histogram**.

The problem is not that it is inaccurate. It is that **the right answer is often not in its
vocabulary**. Measured against ground truth (12 samples per case), the model actually named:

| true | old function | MLE |
|---|---|---|
| lognormal | **100 %** | 100 % |
| gamma | **0 %** | **91 %** |
| weibull | **0 %** | **83 %** |
| exponential | **0 %** | **75 %** |

It is not *wrong* about gamma — **it cannot say gamma.** A genuinely gamma-distributed droplet
population was reported as lognormal or power-law, because those are the only words it has.

``preferred_model`` now carries the MLE's answer, so **all three call sites get the correct
model with no change at their end**. ``preferred_model_histogram`` retains what the old method
would have said, for comparison with historical results. A warning fires when the Vuong test
says the data cannot distinguish the candidates at all.

**2. The C_sat estimator.** ``estimate_phase_boundary`` was added in 1.5.382 — and nothing
called it either. The phase-diagram widget was still running
``estimate_csat_lever_rule``, which **discards every point where the area fraction is zero**.
Those are the most informative points there are: *a zero at C = 5 says the boundary is above 5.*

What the widget now reports, against a **true C_sat of 10**:

| data | **phase boundary (now)** | lever rule (before) |
|---|---|---|
| clean | **9.88** [8.0, 10.4] | 5.15 |
| realistic | **8.57** [6.6, 11.3] | 4.69 |
| noisy | **12.82** [3.6, 16.6] | 2.70 |

**A 2–4× underestimate, now corrected** — and the confidence interval widens honestly with the
noise instead of reporting a single confident number.

### Note — the sweep, and what it says about the shape of this work
Of the ~23 functions added during this audit, **19 were wired in and 4 were not**. Two of the
four were pure API surface for future callers (``get_array_source``, ``stream_stats``) — fine.
The other two were **fixes that had never reached a user.**

Building the correct estimator is the easy half. **A fix that is not wired in is not a fix**,
and there is no warning when that happens: the code compiles, the tests pass, and the broken
function keeps running. The only thing that catches it is going back and asking *"who actually
calls this?"*

## [1.5.420] - 2026-07-10
### Fixed — the Spatial Metrology widget (the path users actually click) still used the CSR null
1.5.397 built the compartment-constrained null. 1.5.419 wired it into the time-series pass.
**``spatial_metrology_ui`` was still calling bare ``ripleys_l``** — it builds its own per-metric
calls rather than going through ``run_all_spatial_metrics``, so it never picked the null up.

That is the widget a user clicks. Every interactive Ripley result has been read against the CSR
line, which on objects placed **uniformly at random inside a real non-convex cell** gives
``L(r) = −4.95`` ("strong regularity") at one scale and ``+6.18`` ("strong clustering") at
another. **The artefact points in either direction depending on the scale.** Now wired, with
``ripleys_l_null`` alongside the curve.

### Fixed — ``spatial_null_envelope(statistic='pcf')`` had NEVER worked
Shipped in 1.5.397, and **every call raised ``TypeError``**: it passed ``r_values`` to
``pair_correlation_function``, which takes ``r_max``/``dr``. A branch nothing exercised, so
nothing caught it — the undefined-name guard cannot see a signature mismatch.

Fixed, and the PCF is now evaluated on the same radial grid as L(r) so observed and null share
an axis. Validated: random-inside-the-cell **p = 0.660** (not significant), genuinely clustered
**p = 0.010** (significant).

### Added — ``tests/test_spatial_nulls.py``
Guards both failures: that the code path **runs at all** (which is how the PCF branch shipped
broken), and that the null is **calibrated** and **retains power**. Measured over 40 seeds:

| statistic | false positives | power |
|---|---|---|
| ``ripley_l`` | **5 %** | **100 %** |
| ``pcf`` | 2 % | **100 %** |

A 5 % false-positive rate at α = 0.05 is **exactly correct**.

### Note — my first version of the test was wrong, and it failed for the right reason
It asserted ``not significant`` on **one seed**, and duly failed at p = 0.040. I nearly went
looking for a bug in the null.

**A statistical test at α = 0.05 is *supposed* to reject 5 % of null-true cases.** A
single-seed assertion on a stochastic test will fail about that often *by construction* — the
test was wrong, not the code. It now asserts the **rate** across many seeds, which is the thing
that actually needs to hold.

This is the third time this session that a test, not the code, was the thing that was broken
(the FRAP validation used the wrong model equation, 1.5.400; the vibration harness produced no
measurable signal, 1.5.403). **A failing test is a hypothesis, not a verdict.**

## [1.5.419] - 2026-07-10
### Fixed — the time-series Ripley pass never used the null model built for it
1.5.397 replaced the CSR line with a **compartment-constrained** Monte-Carlo null, because CSR
assumes an object could land *anywhere* in the area — and it cannot: condensates are confined
to a cell, which is irregular and usually non-convex, and **the confinement itself produces an
apparent signal.**

**The time-series Ripley/PCF pass was still calling bare ``ripleys_l(coords, area)``.** It has
been reading every result against the CSR line — the module that runs Ripley most often, using
the null that was already known to be wrong.

Now wired to ``spatial_null_envelope``, which randomises the points **within the same cell**.
Validated on the exact per-cell path:

| condensate arrangement | CSR L(r) max | **null p** |
|---|---|---|
| **placed at RANDOM inside the cell** | **6.41** (reads as *strong clustering*) | **0.580 → not significant** |
| genuinely clustered | 12.10 | **0.020 → significant** |

**The CSR L(r) is large in both cases** — it cannot tell them apart, because the cell's shape
generates a signal of its own. The constrained null separates them cleanly. Results now carry
``null_p_value`` and ``null_significant`` alongside L(r).

### Note — the audit's "object-feature table" (points 7 & 8) was wrong, and it is worth saying so
The audit claimed the Ripley/PCF pass **re-derives centroids the primary pass already
computed**, and proposed a shared object-feature table to eliminate the duplication.

**Checked, and it is not duplication.** The primary pass builds centroids for every object in
the frame; ``get_puncta_centroids`` returns centroids **grouped by parent cell**, which is what
the per-cell Ripley analysis genuinely needs. They compute different things.

**And the performance case does not hold either.** Measured: one ``regionprops`` pass on a
512×512 frame with 80 objects is **13 ms**, so the supposed duplication costs **2.7 s** across a
200-frame series. That is not a performance problem, and building an abstraction to remove it
would be solving a problem that does not exist.

The real bug was sitting next to it: the null model. Recorded here rather than quietly dropped,
because *"the audit said so"* is not evidence — and this is the second audit recommendation
this session that measurement has overturned (the first: ``scipy.ndimage.mean`` being **slower**
than the loop it was meant to replace, 1.5.390).

## [1.5.418] - 2026-07-10
### Fixed — the aspect-ratio relaxation had the weakest fit gate of all: `r2 > 0.5`
``fit_aspect_ratio_relaxation`` fits a **single** exponential and returns
``eta/gamma = tau/R``. Its only quality check was ``fit_success = r2 > 0.5``.

**tau *is* the measurement** — but that only holds if the relaxation really is a single
exponential, and fusion can have **two** modes (fast surface-driven, slow bulk). Validated on
a two-mode relaxation (true tau = 2.5 and 18):

| | tau | R² | ``fit_success`` | ``fit_adequate`` |
|---|---|---|---|---|
| single mode (true tau = 8) | 7.95 | 0.999 | True | **True** |
| **two modes** | **11.42** | 0.944 | **True** | **False** ← caught |

The single-exponential fit returns **tau = 11.42**, a blend of the two — and ``eta/gamma`` is
wrong by the same factor. **R² = 0.944 passes a 0.5 gate without hesitation.**

``assess_fit`` is now wired in: **3 % false alarms on genuinely single-mode data, 100 %
detection of two-mode relaxations.** This is the same failure already fixed in ``fusion_tools``
(1.5.412), where a two-mode relaxation gave a **76 % viscosity underestimate at R² = 0.996**.

### Deprecated — the lever-rule C_sat estimator, with the numbers
``estimate_csat_lever_rule`` gates on ``fit_success = r2 > 0.5``. But R² describes the fit to
the points that were **kept** — and this estimator **discards every point where the area
fraction is zero**, which are the most informative points there are: *a zero at C = 5 says the
boundary is above 5.*

Against a known C_sat of **10**:

| data | lever rule | **``estimate_phase_boundary``** (1.5.382) |
|---|---|---|
| well-sampled, low noise | 7.78 | **9.97** |
| **well-sampled, high noise** | **5.59** (44 % error, R² = 0.913, ``fit_success = True``) | **10.62** |

The gate is not the problem — **the estimator is**. It now warns on every successful fit,
pointing at ``estimate_phase_boundary``, and returns ``superseded_by`` in the result. Retained
only for comparison against historical values.

### Note — a systematic sweep, and what it found
Having hit this failure five times, I swept for **every fit statistic used in a comparison**
(25 sites). Most are fine. The live ones:

* ``fit_aspect_ratio_relaxation`` — ``r2 > 0.5`` (fixed above)
* ``estimate_csat_lever_rule`` — ``r2 > 0.5`` (deprecated above)
* ``spida_tools`` — ``r2 < 0.9`` and ``snr < 4`` are **advisory notes, not filters**: they
  append a warning and discard nothing, and the SNR there is properly background-subtracted
  (``(p99 − median) / std``). **Correct as written.**

Worth recording that too: the sweep found working code as well as broken, and the difference
is whether the statistic **gates** the data or merely **annotates** it.

## [1.5.417] - 2026-07-10
### Fixed — the bead classifier was sorting by BRIGHTNESS, not focus
``classify_beads`` flagged a bead as ``out_of_plane`` when it was ``oversized and (dim_peak or
r2 < defocus_r2_max)``, with the comment *"poor R² reinforces it"*. **It does not.**

R² measures how well the model explains the **variance** — and at low SNR the noise dominates
the variance, so R² collapses **even when the shape is perfect**. Measured on a bead that is
**perfectly in focus** (true sigma 1.0) at every brightness, with only the SNR changing:

| amplitude | SNR | mean R² | flagged "defocused" (R² < 0.85)? |
|---|---|---|---|
| 10 | 3 | 0.236 | **YES** |
| 20 | 7 | 0.532 | **YES** |
| **40** | **13** | **0.817** | **YES** |
| 80 | 27 | 0.947 | no |
| 160 | 53 | 0.986 | no |

**A dim IN-FOCUS bead was called out-of-plane. The same bead, brighter, was not.**

This is the inverted-classifier behaviour recorded against the real bead data, and a direct
contributor to the **~15 % dropout of stable, in-focus beads** — which fragments the tracks,
starves the linker, and corrupts the viscosity.

**Sigma is the SNR-independent measure of focus**, because it is a property of the **shape**
rather than of how well the model explains the variance. Verified: a fitted sigma of **1.00 at
every SNR from 3 to 53** for an in-focus bead, and **2.49–2.50** for a genuinely defocused one.

The ``oversized`` test was already sigma-based and correct; the R² clause only **added false
positives**, so it is removed. ``defocus_r2_max`` is retained in the signature for backward
compatibility, marked deprecated and unused, with a note that **it is not a focus measure and
must not be reintroduced.**

Validated against ground truth:

| bead population | class |
|---|---|
| bright, in focus | ``singlet`` |
| **dim, in focus (R² 0.82)** | **``singlet``** ← was ``out_of_plane`` |
| **very dim, in focus (R² 0.53)** | **``singlet``** ← was ``out_of_plane`` |
| genuinely defocused (sigma 2.5) | ``out_of_plane`` |
| aggregate (bright + compact) | ``aggregate`` |

### Note — this is the fourth instance of the same failure
A fit statistic (R², SNR-as-a-ratio) used as a **quality gate**, where it is actually reading
brightness or the noise floor:

* ``qc_focus`` — ``var(Laplacian)`` reading the noise, blind to defocus (1.5.405)
* ``molecular_counting`` — R² selecting for brightness, discarding every low-expressing cell
  (1.5.414)
* ``filter_cells_by_transfection`` — an un-subtracted SNR ratio, pedestal-dependent (1.5.415)
* ``segmentation_tools`` puncta — the same un-subtracted ratio, gating nothing at all (1.5.416)
* **``classify_beads`` — R² reading SNR, dropping dim in-focus beads (this release)**

**A goodness-of-fit statistic is not a quality measure.** It answers "does this model explain
the variance", and when the noise *is* the variance it answers that question about the noise.
The quantity that discriminates is almost always a **shape** or a **contrast**, not a fit
score.

### Practical note — this should improve the VPT viscosity directly
Recovering the dim in-focus beads that were being discarded should reduce the track
fragmentation that the linker has been unable to bridge. Worth re-running the 8.325 baseline
comparison after this change.

## [1.5.416] - 2026-07-10
### Fixed — two of the five puncta quality checks have never rejected anything
The puncta refinement filter gates on ``object_mean / bg_std`` — **no background
subtraction**. The camera pedestal sits in the numerator and not the denominator, so the
reported "SNR" scales with it. For an **identical** punctum (true contrast 50 counts):

| pedestal | reported "SNR" |
|---|---|
| 0 | 14 |
| 100 | 34 |
| 500 | 115 |
| **2000** | **416** |

The gate rejects when ``SNR <= threshold``, and the threshold is **1.0** — so it rejects only
when ``object_mean <= bg_std``. **On any camera with a positive background that never
happens.** Even a "punctum" of **pure noise with zero contrast** has ``object_mean`` = 120
against ``bg_std`` = 5, and is kept.

**The ``local_snr`` and ``global_snr`` conditions are dead. They have never rejected a single
detection, on any camera, for the entire life of the pipeline** — two of the five puncta
quality checks doing nothing at all.

Replaced with the contrast above background in units of the background noise, which is
pedestal-invariant::

    CNR = (object_mean − background) / background_noise

Calibrated against ground truth (12 fields, 8 puncta each):

| | median CNR | 95th pct |
|---|---|---|
| **spurious (pure noise)** | **0.0** | **0.4** |
| real punctum, amp 8 | 0.8 | 1.2 |
| real punctum, amp 15 | 1.6 | 2.0 |
| real punctum, amp 30 | 3.2 | 3.7 |
| real punctum, amp 120 | 12.7 | 14.4 |

Spurious detections top out at **0.4**, so a threshold of **1.0** separates noise from real
puncta. **The default is unchanged at 1.0** — but it now means *"one sigma of contrast above
background"* instead of a pedestal-dependent number that gated nothing. Verified: identical
verdict at pedestals of 0, 100, 500 and 2000, and the gate now **fires** — pure noise is
rejected, real puncta are kept.

### Note — I nearly calibrated the threshold against a broken metric
My first calibration measured each punctum's CNR against a **mean/std** local background ring,
and it said a threshold of 2.0 would reject genuinely **bright** puncta (amp = 60). That
stopped me.

The cause: the background ring is **contaminated by neighbouring puncta**. Measured — a bright
punctum with 3 neighbours nearby had its ``ring_std`` inflated from 5 to **18**, collapsing its
CNR from **6.7 to 1.7**. *The metric was reporting crowding, not contrast*, and a threshold
calibrated against it would have **deleted real puncta from crowded cells** — precisely the
cells with the most biology in them.

The background is therefore estimated **robustly** (median + MAD), which neighbouring bright
pixels cannot drag around: the same crowded puncta recover to CNR 5.0 and 5.7.

**Before trusting a metric to set a threshold, check that the metric is measuring what you
think it is.** This is the second time in this sweep that the calibration data, not the
threshold, was the problem.

## [1.5.415] - 2026-07-10
### Fixed — on a camera with a 500-count pedestal, EVERY transfected cell was called untransfected
``filter_cells_by_transfection`` decides **which cells are analysed at all**. It used
``snr = mean_cell / background`` — a **ratio**.

The camera pedestal adds a constant to every pixel and carries no signal. But it appears in
**both the numerator and the denominator**, so it drags the ratio toward 1. The same cells,
with the same true expression, therefore pass or fail depending on the camera:

| pedestal | expr = 0 | expr = 15 | expr = 60 | expr = 200 | **transfected fraction** |
|---|---|---|---|---|---|
| 0 | 1.0 drop | 1.8 drop | **4.0 KEEP** | **11.0 KEEP** | 0.50 |
| 100 | 1.0 drop | 1.1 drop | **1.5 drop** | 2.7 KEEP | 0.25 |
| **500** | 1.0 drop | 1.0 drop | **1.1 drop** | **1.4 drop** | **0.00** |
| **2000** | 1.0 drop | 1.0 drop | 1.0 drop | **1.1 drop** | **0.00** |

**Every transfected cell rejected, on a perfectly ordinary sensor offset.** And this gate runs
*before* analysis, so it is a selection effect on the entire dataset.

Replaced with a background-**subtracted** contrast, normalised by the background noise::

    CNR = (mean_cell − background) / noise_sd

**Verified invariant to the pedestal** — identical at 0, 50, 100, 500 and 2000 counts
(0.6 / 3.6 / 12.5 / 40.6 for the four cells above, in every case). The non-expressing cell is
dropped, all three real cells are kept, and the transfected fraction is **0.75 throughout**.

The threshold is now in units of background sigma, and the default is **3.0** (three sigma
above background) — a calibrated, physically meaningful choice rather than a ratio of 2.0 that
meant something different on every camera.

### The pattern, third instance
This is the **same un-subtracted SNR** fixed in ``pipeline_snr_tools`` (1.5.379) and still
outstanding in ``segmentation_tools``' puncta filter (recorded as a BUG rubric in the roadmap).
And it is the same **class** as the molecule-counting R² gate (1.5.414): *a default filter that
looks rigorous, is applied before the analysis, and silently removes exactly the data the
measurement is about.*

A sweep found **115 filtering defaults** across the scientific modules. Two have now been
shown to invert the result they gate. The remainder are recorded for the same treatment.

## [1.5.414] - 2026-07-10
### Fixed — the molecule-counting R² gate discarded every low-expressing cell and inflated the population mean by 75 %
``count_molecules_pooled`` and ``count_molecules_single`` defaulted to ``r2_min = 0.999`` — a
minimum R² on the **bleaching-curve fit** for a trace to be accepted.

**The R² of a bleaching fit rises with N.** A brighter trace has a better signal-to-noise
ratio, so the double exponential fits it better. The gate therefore selects for
**brightness**, not for correctness — and in a *pooled* analysis, that is a selection effect
on the population itself.

Measured on a mixed population (30 cells with N = 8, 30 cells with N = 80; **true population
mean 44**):

| gate | N = 8 group | N = 80 group | reported mean N |
|---|---|---|---|
| **`r2_min = 0.999`** (old default) | **0 / 30** | 30 / 30 | **77.1** |
| `r2_min = 0.0` (new default) | 30 / 30 | 30 / 30 | **42.4** |
| *truth* | — | — | *44* |

**Not one low-expressing cell survived the gate. The reported mean was 77 against a true 44.**

That is not a conservative filter. It is a selection effect that **inverts the biological
conclusion**, and it fires hardest on exactly the low-copy-number measurements that molecule
counting exists to make.

**And the estimator is fine at low N.** Validated against ground truth (60 traces per point):

| true N | median estimate | IQR | within 2× | **accepted (old gate)** |
|---|---|---|---|---|
| **5** | **5.0** | 5–6 | **100 %** | **0 %** |
| **20** | **20.5** | 18–24 | **100 %** | **0 %** |
| 50 | 49.7 | 44–60 | 100 % | 98 % |
| 200 | 201.2 | 176–239 | 100 % | 100 % |

A true count of **5 is recovered as 5.0, with every trace inside 2×** — and rejected **100 %
of the time**. The estimator was excellent at low copy number; the gate threw it away.

Default is now ``0.0`` in both functions, with the measurement written into the docstrings so
nobody restores the old value thinking it is the safe choice. Set ``r2_min`` deliberately if
there is a reason to.

### Note — the per-trace scatter is inherent, not a defect to be gated away
A single bleaching trace carries limited information: a true N = 20 gives an interquartile
range of about 18–24 across repeats. The *median* is accurate; the individual estimate is
noisy. **That is what ``count_molecules_pooled`` is for** — pooling across traces is how this
method is meant to be used, and it is now stated in the returned ``quality`` field rather than
being papered over with a filter that silently drops half the data.

``molecular_counting_tools`` now imports headlessly — **16** GUI-coupled scientific modules
remain.

## [1.5.413] - 2026-07-10
### Fixed — an R² gate on spots cannot tell one molecule from two, or from a dead pixel
``localize_spots`` filters on ``min_r_squared``, and ``molecular_counting_tools`` uses an R²
threshold to decide whether **a molecule count is accepted at all**. But R² only asks whether
a Gaussian beats a flat line — and a Gaussian describes a *lot* of things well.

Measured on 11×11 patches (true PSF sigma 1.8 px):

| patch | R² | sigma | aspect |
|---|---|---|---|
| **real single spot** | 0.983 | **1.83** | 1.03 |
| **two merged spots** | **0.980** | 2.42 | **1.49** |
| **hot pixel** | **0.928** | **0.14** | 1.14 |
| a diagonal edge | 0.672 | 12.24 | 1.09 |
| flat noise | 0.051 | — | — |

**A merged pair scores 0.980 against a real spot's 0.983. A hot pixel scores 0.928.** An R²
gate therefore accepts merged spots and dead pixels as valid single molecules — and in
molecular counting, each of those is a wrong count.

**The width is what discriminates.** New ``classify_spot_fit`` checks the fitted sigma against
the PSF and the aspect ratio: a hot pixel is far narrower than the PSF, a merged pair is
elongated, an edge fits with an absurd sigma. Thresholds set from measurement, not guessed
(40 realisations each):

| case | sigma / PSF | aspect |
|---|---|---|
| single spot | **1.00** | **1.02** |
| merged, 2 px apart | 1.08 | 1.17 |
| merged, 3 px apart | 1.21 | **1.39** |
| merged, 4 px apart | 1.40 | **1.77** |

Hence ``max_aspect = 1.3``, ``sigma_tolerance = (0.6, 1.3)``. Validated end-to-end: real spots
pass as ``single``; merged pairs at 3 px and 4 px are both caught as ``elongated``.

**And the limit is stated, not hidden.** A pair closer than about **3 px is not detectable as
a pair by anyone** — at 2 px apart it produces sigma/PSF = 1.08 and aspect = 1.17,
statistically indistinguishable from a single spot. That is diffraction, not a shortcoming of
the check. A ``single`` verdict therefore means *"not distinguishable from one spot"*, **not**
*"definitely one molecule"*, and the docstring says so.

### Fixed — rejected spots were silently dropped
A spot whose fit failed or fell below ``min_r_squared`` simply **vanished** from the output.
The user got N−1 spots with no indication that one was rejected, or why. A hot pixel is 1 px
wide and frequently makes the Gaussian fit fail outright — so it disappeared entirely, which
is the worst possible outcome: **a missing molecule is as wrong as a spurious one, and a
silent drop is indistinguishable from a spot that was never detected.**

Rejected detections are now **returned and flagged** (``spot_class``, ``spot_ok``,
``spot_reason``), with a warning stating how many were rejected. ``df[df.spot_ok]`` gives the
usable spots; ``df[~df.spot_ok]`` shows what was rejected and why. Validated: **5 detections
in, 5 rows out** — 2 usable, 2 ``elongated``, 1 ``fit_failed``.

``gaussian_localization_tools`` now imports headlessly — **17** GUI-coupled scientific modules
remain.

## [1.5.412] - 2026-07-10
### Fixed — a two-mode fusion relaxation understated the viscosity by 76 %, at R² = 0.996
``tau`` **is** the measurement: by Frenkel, ``tau = eta*R/sigma``, so the viscosity is read
straight off it. But that only holds if the relaxation is a single exponential — and droplet
fusion can have **two** modes: a fast surface-driven decay and a slow bulk one.

Fitted with a single exponential, a two-mode relaxation returns a tau **between** the two:

| | tau | R² |
|---|---|---|
| single-exp fit | **4.72** | **0.9964** |
| *true bulk mode* | *20.0* | — |

**A 76 % underestimate of the bulk viscosity — and R² says 0.996.** R² cannot see this;
beating a flat line is a trivially low bar for a decaying curve.

New **``test_two_mode_relaxation``** fits both models and selects by AICc: **0 % false alarms
on genuinely single-mode data, 100 % detection of two-mode relaxations** (40 replicates each).

### Note — the residual runs test was NOT sufficient here, and the reason is instructive
``assess_fit`` was wired in first (as in FRAP and MSD). It caught only **62 %**.

The cause is real physics, not a bug: the fusion model carries a linear drift term ``b*t`` —
legitimately, for stage drift and bleaching — and **that term absorbs part of the slow mode**,
fitting a straight line through its tail and flattening the very residual pattern the runs
test looks for (measured: the drift coefficient goes to −0.0049 to soak it up).

So 62 % is the honest ceiling for a residual test on this model. Comparing the **models**
directly reaches 100 %. Same lesson as the MSD confinement test (1.5.401): **when the specific
alternative is known, compare models rather than test residuals.** The runs test is retained
for the misfits that are *not* a second mode.

### Note — the two-mode fit needed bounding, and still has a limit worth stating
Unconstrained, the two-mode fit found a **degenerate** solution in which one "exponential" was
so slow it was effectively a constant: **``tau_slow`` = 1399 s against a true 20 s.** The AICc
comparison still *detected* the second mode correctly in that state — but a tau that is 70×
wrong is worse than no tau. The time constants are now bounded to the physically measurable
range (slower than the sampling interval, faster than the observation window).

**And a genuine limit remains, so it is reported rather than hidden.** A relaxation slower than
the window cannot be measured from it. Validated:

| window | true slow tau | recovered | flagged reliable? |
|---|---|---|---|
| 50 s | 20 s | 18.6 s | **yes** |
| 50 s | **30 s** | **26.6 s** | **no** ← correctly flagged |
| 200 s | 20 s | 19.9 s | yes |
| 300 s | 30 s | 33.0 s | yes |

When the slow mode exceeds ~40 % of the observation window it is systematically
**underestimated**, and ``slow_mode_reliable`` is ``False`` with the reason stated: *record for
longer before converting the slow tau to a viscosity.*

``fusion_tools`` now also imports headlessly (notification shim) — **18** GUI-coupled
scientific modules remain.

## [1.5.411] - 2026-07-10
### Fixed — spherical aberration was invisible at realistic noise
``qc_spherical_aberration`` profiled axial sharpness with ``np.var(laplace(f))`` — **the same
metric shown blind in 1.5.405.** The Laplacian is a high-pass filter and white detector noise
is entirely high-frequency, so the axial profile is flat noise and its skew carries no
information about the optics:

| | symmetric | asymmetric (real aberration) |
|---|---|---|
| low noise | 0.004 → good | 0.723 → warn |
| **realistic noise** | 0.004 → good | **0.012 → "good"** |

**Real spherical aberration was reported as ``good``.** Replaced with the same
difference-of-Gaussians band-pass used by ``qc_focus``. Now: **1.103 → ``bad``** at realistic
noise, and still ``warn`` at high noise, while symmetric stacks stay ``good`` throughout.

### Added — chromatic aberration is now measured, not just mentioned
``qc_chromatic`` took a channel **count** and returned *"multi-channel — register channels on
beads to check"*. Honest, but it measured nothing — and PyCAT *has* the channels. It now
measures the rigid inter-channel shift by phase cross-correlation.

**With the guard that matters:** a channel shift is evidence of *optics* only if the channels
image the **same structures**. Two channels labelling genuinely different objects also produce
a correlation peak — a large, meaningless one (**64.97 px** in test). Chromatic aberration is
bounded by the optics to a few pixels; a shift of tens of pixels is **not** an aberration, it
means the channels are not imaging the same thing. That case is now reported as **not
assessable, with the reason**, rather than as a bad optic.

### Note — I nearly shipped a gate inside its own noise floor
My first thresholds were sub-pixel (``good < 0.5 px``). Then a channel shifted by **0.28 px**
came back as **1.46 px → warn** — a false positive on correctly-registered channels.

Measured the floor properly. Phase cross-correlation between two channels with **independent
noise** reads, with **no shift at all**:

| channel noise | mean | 95th pct |
|---|---|---|
| sd 1 | 0.77 px | 1.44 px |
| **sd 5** | **0.99 px** | **2.08 px** |
| sd 20 | 1.68 px | 3.04 px |

**A perfectly registered pair routinely reads ~1 px and can read 2 px.** A sub-pixel gate is
therefore measuring the metric's own noise. Recovery of a known shift confirms the limit:
0.5 px reads as 1.21 (error 0.71 — dominated by the floor); 2 px reads as 2.26 (error 0.26 —
usable).

Gates are now set at **2 px / 4 px**, which is what the measurement can actually resolve, and
the ``good`` verdict says explicitly that the value is *within the measurement floor* rather
than implying a clean bill of health. To resolve a sub-pixel registration error you need
multi-colour **beads** — identical objects in every channel — which is how a channel
registration should be calibrated anyway. That is now stated in the ``good`` guidance.

**This is the third time in this QC pass that the honest fix was to state a limit rather than
produce a number** (focus cannot be judged absolutely from one 2-D image; vibration cannot be
detected below ~20 frames; chromatic shift cannot be resolved below ~2 px on biological
images). A QC metric that reports what it cannot see is worse than one that admits it.

### All 11 QC metrics have now been tested against ground truth
Verified correct as written: saturation, SNR, ghosting, Nyquist, time sampling.
Fixed: vibration (1.5.403), vignetting (1.5.404), focus (1.5.405), drift (1.5.406), spherical
aberration and chromatic (this release).

## [1.5.410] - 2026-07-10
### Fixed — ``pytest -m core`` still *collected* the GUI tests, and collection means importing
Progress: 1.5.409's ``pip install --no-deps -e .`` worked, and **28 tests were selected**. But
the run still aborted, because of a fact about pytest that is easy to get wrong:

**Markers are applied AFTER collection, and collection means importing.** ``-m core`` does not
stop pytest from importing every module under ``testpaths`` — it only *deselects* them
afterwards. So a test module whose **module-scope** imports need napari or aicsimageio raises
``ImportError`` during collection and aborts the entire run, no matter what the marker
selects. 28 tests were selected and **none of them ever ran**.

Five modules do this::

    test_central_manager    -> napari
    test_data_management    -> pycat.data.data_modules  -> napari
    test_file_io            -> pycat.file_io.file_io    -> aicsimageio
    test_materialize_stack  -> pycat.file_io.file_io    -> aicsimageio
    test_run_pycat          -> pycat.run_pycat          -> napari

New **``tests/conftest.py``** skips a test module that cannot be imported *because the GUI/IO
stack is deliberately absent*, rather than treating it as an error. It **grows by itself** — a
new GUI test does not need anyone to remember to add it to an ``--ignore`` list — and it is
deliberately conservative: a module is skipped only when the package is *genuinely not
installed* **and** that module imports it. A real import bug is still a hard failure.

Verified in a simulated headless environment: **exactly the 5 that errored are skipped, the
guard tests are kept**, and there are **zero collection errors** — 13 modules run, 2 skip
cleanly through their own ``pytest.importorskip`` (``test_ui_smoke``, ``test_segmentation_refine``,
which were already written correctly).

**And the real science now runs headlessly:** ``test_coloc_metrics``, ``test_frap_fitting``,
``test_partition``, ``test_vpt_viscosity_chain``, ``test_feature_analysis``,
``test_image_processing``, ``test_vpt_parallel_equivalence``.

### Note — I broke my own simulation twice while fixing this
First, ``pytest`` is not installed in the sandbox, so *every* test module "failed" to import.
Then I stubbed it — and stubbed ``importorskip`` as a plain import, which made
``test_ui_smoke`` and ``test_segmentation_refine`` look broken when in fact **they were the
two modules that already handled this correctly.** I nearly widened the conftest hook to
"fix" two files that had nothing wrong with them.

Same lesson as the ``sys.path.insert(0, 'src')`` habit that hid the 1.5.409 bug: **a
simulation that differs from the real environment will invent failures as readily as it hides
them.** The fix, both times, was to make the simulation faithful — block the module at the
meta-path so it raises a genuine ``ImportError``; make ``importorskip`` genuinely skip.

## [1.5.409] - 2026-07-10
### Fixed — the CI never installed PyCAT
The real cause, and it is embarrassingly basic: **PyCAT uses a src-layout**
(``src/pycat/``), so ``import pycat`` does **not** work from a checkout — the package must be
installed. The workflow installed the *dependencies* and never installed *PyCAT*.

The failure log said so plainly, once read properly:

* **all 13** ``test_module_actually_imports`` cases failed — including
  ``partial_volume_tools`` and ``segmentation_scale_advisor``, which need nothing beyond
  numpy and scipy. A missing third-party dependency cannot explain that.
* coverage reported ``Module pycat was never imported`` and ``No data was collected``.

The fix is one line — ``pip install --no-deps -e .`` — and the ``--no-deps`` is essential: a
plain ``pip install -e .`` would pull in the pyproject dependencies (**napari, pyqt5, torch,
cellpose**) and defeat the entire purpose of a headless job.

Verified by reproducing the exact runner condition (no ``sys.path`` manipulation):
``ModuleNotFoundError: No module named 'pycat'``. And by simulating the post-install state
(package importable, GUI stack and heavy deps *genuinely unimportable* via a meta-path
blocker): **``import pycat`` succeeds and 13/13 science modules import.**

**Why I did not catch this.** Every check I ran in development began with
``sys.path.insert(0, 'src')``. That one habit hid a whole class of failure — the CI does not
do it, and neither does any real installation. *A test environment that differs from the real
one in a convenient way will hide exactly the bugs that matter.*

### Changed — the core test step no longer reports coverage
``--cov=pycat`` was producing "No data was collected" warnings that read like failures. The
``core`` marker currently selects only the two guard files, so a coverage report there is
near-zero and meaningless. It is worth adding once the numerical kernels themselves carry the
marker; until then it is noise.

## [1.5.408] - 2026-07-10
### Fixed — CI, continued: the dependency list is now derived, not guessed
1.5.407 fixed the two failures I could reproduce. This release closes the gaps I could **not**
verify, because staking a build on unverified steps is how the pipeline stayed red.

**The install list is now computed from the AST**, not written from memory. The 13 guarded
modules need exactly this at module scope::

    numpy  scipy  scikit-image  pandas  matplotlib
    opencv-python-headless (cv2)   pywavelets (pywt)   simpleitk (SimpleITK)

Nothing else. ``torch``, ``cellpose``, ``numba``, ``scikit-learn``, ``aicsimageio``,
``stardist``, ``h5py`` and the rest appear in the transitive import graph **only through lazy
imports inside functions**, so they are never executed at import time. ``numba`` in particular
carries strict numpy pins and was a needless dependency-resolution risk in a job that never
touches it — it has been removed.

Verified by making every non-installed package **genuinely unimportable** (a meta-path blocker
that raises ``ImportError``, rather than the ``sys.modules[x] = None`` trick, which produces
misleading ``AttributeError``\\ s): **13/13 science modules import with the exact CI dependency
set and nothing else.**

### Changed — Ruff is ADVISORY until it has been seen green once
The Ruff step has **never actually been run**. Ruff could not be installed in the environment
where this workflow was written (no network), so ``F811``/``F601``/``B006``/``B904`` were
verified against a hand-written AST re-implementation, and ``F821``/``F823``/``B023`` against
the guard tests. Real Ruff has edge cases those approximations do not.

**Staking the build on a step that has never been executed is how you get a red pipeline that
teaches nothing.** The step now ends in ``|| true``. Once it is observed green in a real run,
delete that and it becomes blocking. The AST guards cover the same bug classes and **are**
blocking, so nothing is unguarded in the meantime.

### Blocking steps, and why each is now trustworthy
- **install** — dependency list derived from the AST; verified against genuine import failure.
- **guard: undefined names / use-before-import / duplicate definitions** — pure AST, no
  imports, runs anywhere. Verified clean on the current tree.
- **guard: 13 science modules actually import** — new in 1.5.407; this is the check that would
  have caught the original breakage.
- **core scientific tests** — the ``|| pytest tests/`` fallback is gone (1.5.407); it was
  collecting the napari-dependent tests in an environment with no napari.

## [1.5.407] - 2026-07-10
### Fixed — the CI was red for two reasons, neither of which was the code
**1. The workflow did not install the dependencies the science modules actually need.**
I wrote the install step as "the scientific deps" — ``numpy scipy scikit-image pandas
tifffile zarr`` — from memory, without checking what the guarded modules import. **Four of
the thirteen could not import at all:**

| module | needs |
|---|---|
| ``image_processing_tools`` | pywavelets, SimpleITK |
| ``feature_analysis_tools`` | cv2 |
| ``label_and_mask_tools`` | cv2 |
| ``pixel_wise_corr_analysis_tools`` | matplotlib |

The headless job excludes napari/PyQt/cellpose **on purpose** — that is the whole point of it.
It was never supposed to exclude the *maths*. Added ``matplotlib``,
``opencv-python-headless``, ``pywavelets``, ``simpleitk``, ``scikit-learn`` and ``numba``.
Verified: **13/13 modules now import with the GUI stack still blocked.**

**2. A "just in case" fallback collected the napari tests.** The final step was
``pytest -m core ... || pytest tests/``. Only two files carry the ``core`` marker, so the
marker selected almost nothing, the ``||`` fired, and the fallback then collected
``test_central_manager.py`` — which imports napari — in an environment with no napari.

The fallback was a hedge against the marker not being wired up. It turned a clean signal into
a confusing one: **a build that is red for an uninteresting reason is a build people learn to
ignore.** Removed. If the marker selects nothing, that is a fact worth surfacing, not papering
over.

### Added — the guard now imports the modules, instead of only reading them
``tests/test_headless_science.py`` was a **static** check: it parsed the source and asserted
no napari/Qt import sat at module scope. Necessary, but not sufficient — and the gap was
exactly this failure. The static guard passed happily while four modules could not be imported
at all.

It now **actually imports each module** in the CI environment, and the failure message points
at the real fix in either direction:

- missing a **GUI** dependency → move the import inside the function (the ``notify`` shim and
  lazy-accessor pattern);
- missing a **compute** dependency → add it to the workflow.

Confirmed it catches the original failure: under the old dependency list, the new test fails
for 4 of the 5 affected modules — **it would have caught this before the push.**

All CI gates re-simulated in the CI environment (GUI blocked, compute deps present):
undefined-name guard, headless-import guard, and the Ruff correctness subset (F811/F601/B006)
all pass.

## [1.5.406] - 2026-07-10
### Fixed — the same stage drift passed or failed depending on the camera
``qc_drift`` gated on the drift as a **fraction of the field of view** (good < 1 %, bad ≥ 5 %).
The same physical drift therefore got a **different verdict on a different sensor**:

| 19 px of drift over 20 frames | % of FOV | verdict |
|---|---|---|
| on a 128 px sensor | 14.8 % | **bad** |
| on a 512 px sensor | 3.7 % | **warn** |

**The stage did exactly the same thing.**

And the FOV framing is *backwards for the damage that matters*. A condensate is ~6 px across,
so **19 px moves it three diameters** — the object in the last frame does not overlap the
object in the first frame **at all**, and every per-object time-series is destroyed. On a large
sensor that reads as a mild 3.7 %, and the QC said ``warn``.

Field-of-view fraction is the right reference for exactly one failure: objects leaving the
frame. For **misaligned time-series, broken tracking and blurred projections** — the failures
that actually matter here — the reference is the **object size**.

The QC does not know the object size, but it can **measure** it: the autocorrelation half-width
of the image tracks the true feature size closely (ratio 1.6–2.0 across an 8× range of object
radii) and needs no mask. Drift is now judged against that.

| rate | drift | × features | % FOV (128 / 256 / 512) | verdict |
|---|---|---|---|---|
| 0.05 px/f | 1.0 px | 0.10 | 0.8 % / 0.4 % / 0.2 % | **good, good, good** |
| 0.30 px/f | 5.9 px | 0.59 | 4.5 % / 2.3 % / 1.2 % | **warn, warn, warn** |
| 1.00 px/f | 19.0 px | 1.90 | 14.8 % / 7.4 % / 3.7 % | **bad, bad, bad** |

The verdict is now **identical across sensor sizes** for the same stage drift — the
``× features`` column is constant while ``% FOV`` swings fourfold. Both numbers are reported,
and the result states which one the verdict was based on.

### Verified correct this pass — not everything is broken
``qc_ghosting`` (0.0011 → 0.0240 on a 35 % echo; good → bad), ``qc_nyquist`` (correctly calls
under- and over-sampling from pixel size, NA and wavelength) and ``qc_time_sampling`` all
behave correctly against ground truth. Together with ``qc_saturation`` and ``qc_snr`` (verified
in 1.5.405), **five of the eleven QC metrics were already right.**

That leaves ``qc_spherical_aberration`` and ``qc_chromatic`` untested.

## [1.5.405] - 2026-07-10
### Fixed — the focus QC could not see defocus
``qc_focus`` used ``var(laplace(frame))``. **The Laplacian is a high-pass filter, and white
detector noise is entirely high-frequency** — so on any real image the noise dominates it
completely and the metric reports **the noise level, not the focus.**

Measured on a synthetic field (signal 400, noise sd 5) across a **24× blur range**:

| blur σ | var(Laplacian) | DoG band-pass |
|---|---|---|
| 0.5 | 504.1 | 10.0 |
| 3.0 | 503.8 | 5.7 |
| 12.0 | **497.8** | **1.0** |

``var(Laplacian)`` moves by **1.01×** across the entire range — no discriminating power at
all. (Without noise it collapses 4.90 → 0.04 exactly as it should. The signal contribution is
simply ~0.04, against a noise floor of ~500.)

**This mattered, and not only cosmetically.** The 2-D case returns ``'info'`` and judges
nothing — correctly, since absolute sharpness is scene-dependent. But the **stack** case *does*
return a verdict, via a ``< 0.5 × median`` rule. On a 20-frame stack with one badly defocused
frame, that frame scored **0.98 × median** — so it **was not flagged**:

| stack | before | now |
|---|---|---|
| all frames in focus | good, 0 flagged | good, 0 flagged |
| **frame 10 defocused** | **good, 0 flagged** | **warn, 1/20 flagged** |
| **frames 12–19 defocused** | **good, 0 flagged** | **bad, 8/20 flagged** |

**Before the fix, all three returned ``good``.** The QC could not see defocus at all.

Replaced with a **difference-of-Gaussians band-pass**, which rejects the high-frequency noise
*and* the low-frequency illumination, keeping the scale where real edges live. It stays
**monotonic in blur at every noise level tested** (sd 1 → 50). *The rule was fine; the quantity
was not.*

### Note — two failed attempts, and why the second failure was the useful one
I first tried to make focus judgeable **absolutely** (a fixed good/warn/bad threshold), via a
noise-normalised band-pass ratio. Measured, the ratio still collapsed with noise (1.58 → 0.11
at fixed blur) — so a fixed threshold would have condemned every noisy image as defocused.

That failure was the informative one: **absolute focus cannot be judged from a single 2-D
image**, and the original code already knew that. The bug was never the ``'info'`` verdict — it
was that the *relative* comparison across a stack, which **can** be judged, was being made on a
quantity that could not see defocus.

### Also verified this pass
``qc_saturation`` (0 % → good; 2.4 % clipped → bad) and ``qc_snr`` (reported SNR 60.6 → 3.2 as
noise rises 2 → 200) both **behave correctly** against ground truth. Still untested: ghosting,
drift, spherical aberration, Nyquist, time sampling.

## [1.5.404] - 2026-07-10
### Fixed — the vignetting QC was measuring where the cells are, not the illumination
``qc_vignetting`` binned the **raw mean intensity** by distance from the image centre and
reported the edge-to-centre ratio. That does not measure illumination — it measures **where
the objects happen to sit**.

On images with a **perfectly flat background** (identical, uniform illumination in all three):

| image | edge/centre | verdict |
|---|---|---|
| flat background, no objects | 1.000 | good |
| **flat background, objects in the CENTRE** | **0.354** | **"bad"** |
| flat background, objects at the EDGES | 1.100 | good |

**A field with cells clustered centrally was condemned as severely vignetted.** A field with
cells at the edges would *mask* real vignetting. The metric swung from ``good`` to ``bad`` on
object placement alone.

**Percentiles do not fix this.** The innermost radial bins hold only a few hundred pixels, and
the objects can fill them **entirely** — bin 0 measured **100 % object, with zero background
pixels left**. That is geometric, not statistical: no choice of percentile can recover a
background that is not there. (p1, p5, p10 and p20 were all measured; the best still read
0.659 on a flat field.)

The physics gives the fix: **illumination varies smoothly and slowly; objects are small and
sharp.** A grey-scale opening with a large kernel (1/4 of the short side, chosen by
measurement) deletes compact bright structures and leaves the broad lamp profile. The radial
falloff is read off *that*:

| image | old | now | truth |
|---|---|---|---|
| flat + objects in centre | 0.354 → **"bad"** | **1.000 → good** | no vignetting |
| flat + objects at edges | 1.100 | 1.000 → good | no vignetting |
| real 40 % vignetting, no objects | 0.650 | 0.683 → bad | vignetted |
| **real 40 % vignetting + centre objects** | **0.229** | **0.683 → bad** | vignetted |

Object placement no longer moves the number *at all*, and real vignetting is detected
identically with or without objects present.

### Note — this is the same class as 1.5.402 and 1.5.403
A threshold (``ratio >= 0.9``) applied to a statistic that **nobody had checked against a
known-good image**. The gate was reasonable; the quantity it gated was not measuring what its
name claimed. Found by the same method each time: *construct data where the answer is known,
and see whether the metric agrees.*

The remaining ``data_qc_tools`` thresholds (focus, SNR, ghosting, drift, spherical aberration,
Nyquist, time sampling) have not yet been put through this test.

## [1.5.403] - 2026-07-10
### Fixed — a stage vibrating in a circle was completely invisible to the vibration QC
``qc_vibration`` computed the frame-to-frame jitter as ``np.hypot(dx, dy)`` — the **magnitude**
of the shift. **A stage vibrating in a circle or an ellipse has a shift of constant
magnitude**, so ``hypot`` collapses it to a **flat line** and the periodicity is destroyed
before the FFT ever sees it. On a synthetic circular vibration the magnitude trace was
*literally all zeros*, and the check reported *"no periodic component (p = 1.00)"* for a stage
that was vibrating throughout.

Circular and elliptical stage vibration is a **real and common mode** — a pump, a fan, a
rotating imbalance. It was undetectable.

Fixed by analysing the two axes **separately** (Bonferroni-corrected): a linear vibration
shows in one axis, a circular one in both.

### Fixed — the vibration threshold was measuring STACK LENGTH, not vibration
The status was ``good if ratio < 0.35 else warn if < 0.6 else bad``, where ``ratio`` is the
spectral concentration of the jitter. But the concentration of a **random** jitter trace
depends entirely on the number of frequency bins — i.e. **on the frame count**. Measured, with
**no vibration present at all**:

| frames | ratio | old verdict |
|---|---|---|
| **5** | 0.79 | **"bad"** |
| 10 | 0.54 | "warn" |
| 20 | 0.31 | "good" |
| 200 | 0.05 | "good" |

**The same microscope on the same table got a different verdict depending on how many frames
were acquired.** A short stack of perfectly good data was condemned.

Replaced with a **permutation null**: shuffle the jitter trace — destroying any periodicity
while preserving the amplitudes exactly — and ask how often a random ordering concentrates its
energy as sharply. That p-value does not depend on the frame count.

Validated on stacks with **measured** shifts (see below): random jitter → ``good`` at every
length; linear **and circular** vibration → ``bad`` at 25/50/100 frames. Below 20 frames there
are too few bins for the test to have power, and it now returns ``na`` — *not assessable* —
rather than ``good``. **"Could not assess" is not a clean bill of health.**

### Note — my validation harness was broken, and the passing tests were passing by luck
The first harness shifted a base image with ``ndi.shift(..., mode='reflect')`` at an amplitude
and period that ``phase_cross_correlation`` measured as **exactly zero shift**. Every
"detection" it reported was noise. The T = 20 and T = 40 cases *passed*, which is precisely why
this was dangerous — a green test on a harness that produces no signal.

Caught by checking the harness itself: *do the measured shifts match the intended ones?* They
did not (all zeros). The corrected harness reaches a correlation of **0.997** between intended
and measured shift, and only then are the results meaningful.

**Validate the validator.** A test that cannot fail is not evidence.

## [1.5.402] - 2026-07-10
### Fixed — the coarsening confidence flag never fired, so it carried no information
``fit_coarsening`` distinguishes **Ostwald ripening** (R ~ t^⅓) from **coalescence**
(R ~ t^½), and it already had a ``mechanism_confidence`` flag gated on the **R² gap**
between the two fits exceeding **0.1**.

**Measured, the gap between t^⅓ and t^½ is about 0.008 — even on noiseless data.** The two
curves are both concave-increasing and genuinely similar over any finite time range. So the
gate **never fired**: ``confidence`` was permanently ``'low'``, and the flag could not
distinguish a call that is right **100 %** of the time from one that is a **coin flip**.

The *selection* is actually good — validated against ground truth at **100 %** correct on
clean data, degrading to ~70 % at heavy noise. What was missing was any honest statement of
**which regime you are in**.

**Replaced with a bootstrap:** resample the residuals and ask how often the winning mechanism
actually wins. It needs no ground truth, is measurable from the single dataset in hand, and
it **tracks the true correct-selection rate**:

| noise | true correct rate | bootstrap agreement | label now |
|---|---|---|---|
| 0.005 | **100 %** | 100 % | **high** |
| 0.05 | 88 % | 95 % | high |
| 0.10 | 80 % | 81 % | **moderate** |
| 0.20 | 72 % | 67 % | **low** |
| 0.40 | **52 %** | 60 % | low |

Every one of those rows previously reported ``'low'``. The flag now moves from *high* →
*moderate* → *low* exactly as the call degrades toward a coin flip, and at *low* it says so
plainly: *"barely better than a coin flip — t^⅓ and t^½ are not distinguishable in this data.
Do not report a coarsening mechanism from it."*

``mechanism_bootstrap_agreement`` is returned alongside, so the number behind the label is
visible.

### Note — what was already right here
This module was in better shape than most: it already had an explicit confidence flag, an
honest caveat about the two exponents being hard to separate, and a sensible *arrested*
detection that avoids fitting a power law to a radius that is not growing. The problem was
only that the **threshold was set at a scale the statistic never reaches** — a plausible
number chosen without measuring what the gap actually looks like. Worth recording, because it
is a subtler failure than the ones before it: not a missing check, but a check calibrated
against an assumption instead of data.

## [1.5.401] - 2026-07-10
### Fixed — a bead hitting a wall was reported as "subdiffusion"
``motion_type`` is read straight off ``alpha``, and alpha is the **entire**
anomalous-vs-Brownian claim. But alpha means nothing unless the power law is the right
model — and **confinement is the failure that matters**. A probe trapped in a small
condensate produces an MSD that **plateaus**, and a power law *cannot* plateau, so it fits
the plateau with a spuriously small exponent:

| truth | alpha | R² | reported |
|---|---|---|---|
| truly Brownian | 1.006 | 1.000 | `Brownian` ✓ |
| **confined (probe hits the wall)** | **0.000** | **0.903** | **`subdiffusion`** ✗ |

A confined probe is reported as **subdiffusion with a healthy R²** — which a reader takes as
*"the medium is viscoelastic / crowded"*. **It is not. The bead is hitting a wall.**
Completely different physics, the wrong conclusion, and R² does not blink.

New **``test_confinement``** fits both a power law and a confined model and selects by AICc.
``motion_type`` now returns ``'confined (not anomalous diffusion)'`` with the estimated domain
size, and states plainly that alpha is not interpretable in that case.

Validated against ground truth (40 replicates each):

| | rate |
|---|---|
| false "confined" on **Brownian** | **2 %** |
| false "confined" on **genuine subdiffusion** | 12 % |
| **detected real confinement** | **65 %** |

A genuinely diffusing probe is essentially never called confined, and — importantly — a
*genuinely subdiffusive* MSD is still reported as subdiffusion. The test distinguishes real
anomalous diffusion from a wall, which is the whole point.

### Note — the runs test was the wrong tool here, and pretending otherwise flagged everything
``assess_fit`` (1.5.400) was wired into the MSD fit first. It flagged **100 % of fits,
including textbook Brownian ones.**

The cause was a real bug in ``fit_quality``: the runs test needs **≥ 8 residuals** to have any
power, and PyCAT's *defensible lag window* is deliberately narrow — often only **~6 lags**. So
the test could never run, returned ``NaN``, and my ``adequate`` logic treated "could not
assess" as "the model is wrong". **Absence of evidence is not evidence of absence**, and
conflating them makes a check that fires on everything — which is worse than no check.

``assess_fit`` now returns an explicit ``assessable`` flag, and "not assessed" no longer
blocks a result. Model comparison was used for confinement instead, because **it works at
n = 6 where the runs test cannot**:

| n lags | false alarm (Brownian) | detect confinement |
|---|---|---|
| **6** | **0 %** | 60 % |
| 10 | 0 % | **100 %** |
| 15+ | 0 % | 100 % |

So a *negative* result on a short lag window means **"not detected"**, not "not confined" —
and the verdict says so.

## [1.5.400] - 2026-07-10
### Added — fit adequacy: R² accepts wrong models, and the residuals catch them
R² is used as a fit-quality measure at **67 call sites across 9 modules**. It answers exactly
one question: **"does this model beat a horizontal line?"** For any monotonic curve — a FRAP
recovery, an MSD, a coarsening law — that is a trivially low bar, and clearing it is not
evidence the model is *right*.

Measured against PyCAT's **actual** FRAP model (the single-pool hyperbolic
``I = (a + b·x)/(1 + x)``) on data whose truth is a **two-component** recovery — a fast and a
slow pool, which the single-pool model cannot represent:

| | R² | mobile fraction |
|---|---|---|
| single-pool fit to 2-component truth | **0.957** | 0.822 |
| *truth* | — | *0.875* |

**The wrong model scores R² = 0.957.** Any "R² > 0.95 means a good fit" heuristic accepts it
without hesitation.

**The residuals catch it.** A correct model leaves residuals whose signs flip like a coin. A
model *missing structure* leaves them in **blocks** — the fit sits above the data over one
stretch and below it over another. The Wald–Wolfowitz **runs test** measures exactly that:

| scenario | flagged |
|---|---|
| correct model (data from the model) | **2 / 40 (5 %)** |
| wrong model (2-component truth) | **30 / 40 (75 %)** |

Calibrated — a 5 % false-alarm rate is what a 0.05 threshold *should* give — and it catches
three quarters of the wrong-model fits R² waves through.

New **``pycat/utils/fit_quality.py``**: ``assess_fit`` returns R², the runs test, an
``adequate`` flag and a verdict. Wired into ``frap_tools``, where the adequacy now travels
**with** the parameters — an R² of 0.957 on a wrong model cannot be read without the evidence
that the model is wrong.

This is the **same failure** as the colocalization p-value (1.5.396), Ripley's CSR line
(1.5.397) and Moran's I (1.5.398–399): *a number that looks like a validity check but is
tested against a null nobody chose.* R²'s implicit null is "a flat line", and beating a flat
line is not evidence of correctness.

### Note — my validation was wrong first, and the check itself exposed it
The first run flagged the **correct** model 30/30 times — a fatal false-positive rate. The
cause: I generated exponential recovery data and fed it to PyCAT's **hyperbolic** fitter. The
runs test was right; **my test was wrong.** It correctly detected that the model did not
match the data I had given it. Redone against the real model equation, it calibrates at 5 %.

A check that catches your own mistake in constructing its validation is a good sign — but it
is also exactly why the *"assert the correct model is accepted"* half of the test matters as
much as the *"assert the wrong model is caught"* half.

### Recorded — the other 8 modules
``vpt_tools`` (the MSD power-law fit — a non-random residual pattern means **α** is being read
off a curve the model does not describe, and α is the whole anomalous-vs-Brownian claim),
``condensate_physics_tools`` (the coarsening exponent, same argument), ``invitro_tools``,
``gaussian_localization_tools``, ``molecular_counting_tools``, ``fusion_tools``,
``spida_tools``, ``correlation_func_analysis_tools``. In the roadmap with an acceptance test.

## [1.5.399] - 2026-07-10
### Changed — Moran's I demoted, with a measured saturation guard
Moran's I is no longer the primary structure indicator. It is **saturated on condensate
images and reports nothing about arrangement** — but it is **genuinely useful on SMLM /
single-molecule data**, so it is kept, guarded rather than removed.

**The mechanism.** Moran's I of a real image is a *blend* of the signal's autocorrelation
and the noise's: ``I ≈ f_signal × I_signal``. For any **extended** object ``I_signal ≈ 1`` —
every pixel inside a droplet looks like its neighbour *regardless of where the droplet sits*.
So on a bright image of extended objects, I is pinned near 1 and **has no room left to
respond to anything.**

Measured across **63 (object size, SNR) combinations**, comparing dispersed objects against
the *same* objects aggregated into a clump:

| headroom (1 − I) | n | median gap | max gap |
|---|---|---|---|
| **< 0.02** | 6 | 0.0043 | **0.0093** |
| 0.02 – 0.15 | 18 | 0.018 – 0.041 | 0.158 |
| > 0.15 | 39 | 0.0853 | 0.297 |

Below a headroom of 0.02 the difference between *fully dispersed* and *fully aggregated*
**never exceeded 0.009**. The statistic is dead; the value reflects object size and image
brightness, not arrangement.

**New ``morans_I_headroom``** measures this from the single image in hand — no ground truth
— and refuses to interpret a saturated value. Validated end-to-end with **nothing hard-coded
about image type**:

| image | Moran's I | headroom | verdict |
|---|---|---|---|
| condensates (8 px, bright) | 0.999 | 0.001 | **saturated — refused** |
| SMLM, random localisations | 0.655 | 0.345 | usable |
| SMLM, clustered localisations | 0.772 | 0.228 | usable |

The discriminating gap is **0.117 on SMLM** against **~0.002 on condensates** — two orders of
magnitude, decided entirely by what is in the image. The headline verdict now comes from
``structure_beyond_optics``; Moran's I is reported *with* its headroom so a pinned 0.99 cannot
be mistaken for a finding.

**A wrong claim, corrected in public.** An earlier version of this analysis concluded Moran's
I is "useless above about 2 pixels". **That was wrong.** The same object size flips between
usable and saturated depending on SNR, because it is *noise* that dilutes I away from 1 and
gives it room to move — so a size threshold was really a measurement of whatever SNR happened
to be simulated. I reached three contradictory conclusions by varying my own simulation
parameters before catching it. **Headroom is the correct guard** because it captures size and
SNR together, and it is measurable rather than assumed.

### Added — ``docs/source/usage/spatial_randomness.rst``
The full diagnosis, written up: what Moran's I measures (arrangement at fixed histogram — a
real and exclusive capability: two images with *identical* kurtosis, 22.345, score 0.913 vs
0.006), why it fails on condensates, the measured headroom table, why **no null model can
rescue it** for the structure-beyond-optics question (it *is* the autocorrelation the null
must preserve, so it has a correct 4 % false-positive rate and 0–12 % power — blind, not
miscalibrated), where it genuinely works (SMLM), and the note that **a null model has to be
checked, not just built**.

## [1.5.398] - 2026-07-10
### Fixed — the spatial-randomness test declared an empty field "real spatial clustering"
``measure_spatial_randomness`` tests Moran's I against a **pixel-shuffled** null. That null
is *correct* for the question it asks — *"is this image autocorrelated at all, versus
spatially independent noise?"* — and it is kept.

**But that is not the question a microscopist has.** Every image from a real microscope is
autocorrelated, because **the PSF guarantees it**. Pure noise passed through a PSF scores
Moran's I = 0.88, **z = 160** against this null — reported as "real spatial clustering
beyond the intensity histogram" with *no biology in the field at all*.

And Moran's I cannot separate the cases even in principle:

| image | Moran's I |
|---|---|
| EMPTY field (noise + optics) | 0.255 |
| faint condensates | 0.253 |
| clear condensates | 0.262 |
| bright condensates | 0.260 |

**No change of null can rescue this.** Moran's I *is* a function of the autocorrelation, so
any null that preserves the autocorrelation preserves Moran's I by construction. Against a
phase-randomised null it has 4 % false positives (correctly calibrated) but **0–12 % power**.
It is **blind, not merely miscalibrated** — the *statistic* is wrong for the question, not
just its null.

**New ``structure_beyond_optics``**: kurtosis against a **phase-randomised** null. The
surrogate keeps the amplitude spectrum (hence the autocorrelation *exactly*, by
Wiener–Khinchin) and replaces the **phases**, where real structure lives — so it has the
microscope's blur and none of the biology. Kurtosis is sensitive to phase: to bright pixels
being *concentrated* rather than *spread*, which is what a condensate is and what blur alone
cannot manufacture.

Characterised on synthetic fields with condensates at controlled SNR:

| SNR | detected |
|---|---|
| **0** | **0 %** ← false-positive rate |
| 2 | 10 % |
| 3 | 53 % |
| **4** | **100 %** |
| ≥ 5 | **100 %** |

Calibrated on an empty field, reliable from ≈ SNR 4 up — and it **says so** rather than
guessing: a negative result at low SNR is reported as *"not detected"*, not *"not there"*.

Runs automatically inside ``measure_spatial_randomness``; the Moran's I verdict is reworded
to state only what it actually establishes.

### Note — my own surrogate was biased, and only a self-check caught it
The first phase-randomisation enforced Hermitian symmetry by averaging a uniform phase array
with its own reversal. That produced surrogates with a **kurtosis of ~650 against the data's
~0** (and the wrong variance) — a wildly biased null that made **every** test fire, at a
**100 % false-positive rate**. It looked like a working detector.

It was caught only by comparing the *surrogate's own statistics* to the *data's* — a null
whose moments do not match the data is not a null. The correct construction takes the phases
from the FFT of a random **real** field, which is Hermitian by construction (kurtosis 0.0098,
variance matching the data to four decimals). **Building a null model is not enough; the null
has to be checked.**

## [1.5.397] - 2026-07-10
### Fixed — Ripley's L had no null model, and the CSR line it was read against is wrong
``ripleys_l`` reported L(r) and left the user to compare it against the CSR line of zero.
There was **no null model at all** — no envelope, no permutations, nothing.

**CSR is the wrong null here.** It assumes an object could land *anywhere* in the area, and
it cannot: condensates are confined to a cell, which is irregular and usually non-convex.
**The confinement itself produces an apparent signal.**

Measured by placing objects **uniformly at random inside a real, non-convex cell shape**,
where the truth is *no spatial structure whatsoever*:

| r | L(r) against the CSR line | how a user would read it |
|---|---|---|
| 8 px | −0.82 | ~random |
| 17 px | −2.06 | "regular / repulsion" |
| 29 px | **−4.95** | **"strong regularity"** |

and at a realistic pixel size the same randomly-placed objects gave **L = +6.18** — which
reads as **strong clustering**. There is no biology in any of those numbers. **The artefact
can point in either direction depending on the scale**, which makes eyeballing it against the
CSR line worse than useless.

New **``spatial_null_envelope``** randomises the points **within the actual cell mask** — the
same compartment the real objects were confined to — so whatever the confinement does to L(r)
is present in the null too, and cancels. What survives is biology.

- Returns the observed curve, the null mean, a Monte-Carlo envelope, and a **global rank
  test** p-value. The global test is the honest one: reading significance off a pointwise
  envelope at ten radii is ten tests, not one.
- Validated: **0/20 false positives** on objects placed at random inside a cell (the same
  data the CSR line called "regular"), and **20/20 detection** of genuine clustering. It is
  calibrated *and* it keeps its power.
- Runs automatically in ``run_all_spatial_metrics``; results land at
  ``results['ripleys_l_envelope']`` and ``results['ripleys_l_null']``.

This is the same class of error as the colocalization p-value (1.5.396): a null model that
assumes independence or free placement, applied to data where neither holds. Both were
producing confident significance from nothing.

### Note — the guard caught two more
``coords_px`` (a leftover from a clumsy ``'x' in dir()`` check) and ``debug_log`` (not
imported in this module). Both flagged **before anything was run**. The units bug it exposed
was real: ``run_all_spatial_metrics`` receives coordinates in **microns**, and the envelope
must index the mask in **pixels** — a silent mismatch would have randomised the null over the
wrong region entirely.

## [1.5.396] - 2026-07-10
### Fixed — the colocalization p-value was measuring how many pixels you have
The p-value shipped alongside every coefficient comes from ``scipy.stats.pearsonr`` over
**flattened pixels**, and its null assumes the samples are **independent**. Adjacent pixels
in a microscopy image are not — the PSF correlates them — so the ``n`` in that p-value
(65 536 for a 256×256 ROI) is a fiction.

**Measured on two channels that are INDEPENDENT BY CONSTRUCTION, each blurred by a realistic
PSF, where the truth is "not significant":**

| test | false positives |
|---|---|
| pixel p-value (what is reported today) | **83 %** |
| pixel-scrambling null | **85 %** ← *the naive fix fails just as badly* |
| **block-shuffled null (block = measured correlation length)** | **10 %** ← target is 5 % |

Two independent channels are called significantly colocalized **more than four times in
five**. The test is not measuring biology; it is measuring pixel count.

**Pixel scrambling is not the fix.** Destroying the spatial autocorrelation makes the null
distribution far too narrow, so almost anything clears it — it fails exactly as badly as the
parametric p-value. That is the audit's precise point, and it is why "just permute it" does
not work.

New **``spatial_null_test``** shuffles whole **blocks**, preserving the structure *within*
each block so the null has the same spatial statistics as the data. The block size is set
from the **measured** correlation length of the image (2× the 1/e decay of its
autocorrelation), not guessed. New ``spatial_correlation_length`` exposes that measurement.

**And it keeps its power:** genuine colocalization is still detected **100 %** of the time
(``r = 0.584, p = 0.005``). This is not a matter of making everything non-significant — the
calibrated test says *no* to independent channels and *yes* to real association.

Runs automatically whenever a correlation coefficient is selected.

### Added — the replication unit, stated where the number is used
Pixels within a cell are not biological replicates, and neither are objects within a cell: a
coefficient over one ROI is **one observation**, whatever its pixel count. The output is
indexed by *method* only — it carries no cell/field/experiment column — so nothing in it can
distinguish one cell measured well from ten cells measured once.

The note now travels **with the result** (``data_repository['PWCCA_diagnostics']``), alongside
the threshold-sensitivity report and the calibrated null, rather than being fired as a warning
on every run: a warning that always fires is a warning nobody reads, and this one needs to be
present at the point the number is *used*.

## [1.5.395] - 2026-07-10
### Added — Manders' coefficients now report how much they depend on the threshold
Manders' M1/M2 are **defined by** a threshold, so the number is only as defensible as that
choice. Costes' method (already here) picks it in a principled way — but a *single* reported
M1 still hides how much the answer hinges on where the cut landed.

Measured with a ±30 % threshold perturbation on synthetic images with a **known** partial
overlap:

| scenario | M1 across the perturbation |
|---|---|
| identical channels | **1.00 → 1.00** (spread 0.00) |
| disjoint channels | **0.00 → 0.00** |
| dim, partial overlap (the condensate case) | **0.13 → 0.93** |

**The same image supports almost any conclusion depending on the cut.** Two groups analysing
identical data, both using a defensible threshold, can report materially different
colocalization — and neither would know.

New ``manders_threshold_sensitivity`` returns the grid, the M1/M2 range, a ``stable`` flag
(both spreads < 0.10) and a plain-English verdict. It runs **automatically** whenever a
threshold-dependent method is selected, and warns only when the coefficient is genuinely
fragile — Pearson-only selections stay silent, and clean well-separated data is not flagged.

This does not produce a *better* number. It produces an *honest* one: it separates the case
where M1 is solid from the case where it is an artefact of the threshold.

**The first version of this was wrong, and the ground-truth test caught it.** Sweeping fixed
percentiles of the whole image is the wrong grid: with sparse objects most percentiles land
*inside the background*, where Manders is meaningless anyway, and two **perfectly colocalised
channels** then appeared "unstable" (M1 ranging 0.79–1.00) purely because the low thresholds
admitted background noise. The perturbation must be anchored to a threshold a real analysis
would actually use (Costes/Otsu) and moved around it — which is the question that matters:
*if my threshold were somewhat different, as another analyst's would be, would I report a
different number?*

### Note — the guard caught two more, again in the edit loop
``napari_show_warning`` and ``debug_log`` were used without imports. The undefined-name guard
flagged both **before anything was run** — the third such catch in three releases. Both now go
through the shims, and ``pixel_wise_corr_analysis_tools`` still imports headlessly.

## [1.5.394] - 2026-07-10
### Fixed — N&B accepted 4 frames in silence, where the answer can be 12× wrong
The 4-frame minimum is **mathematically sufficient** to form a variance and
**scientifically useless**. N&B measures a *variance*, and the sampling error of a variance
estimate is a hard statistical floor: ``sqrt(2 / (T − 1))``.

| frames | rel. SD of the variance | 95 % range for a true brightness of 1.0 |
|---|---|---|
| **4** | **82 %** | **[0.08, 3.07]** — the answer can be 12× too low or 3× too high |
| 8 | 53 % | [0.24, 2.26] |
| 16 | 37 % | [0.42, 1.81] |
| 64 | 18 % | [0.68, 1.40] |
| 256 | 9 % | [0.84, 1.18] |

(Monte-Carlo of Poisson counts; agrees with the closed form to within a percent.) The old
code raised below 4 and then **proceeded in silence** — a 5-frame stack produced a brightness
map with no hint that the variance behind it carried ~71 % relative error.

**New ``frame_count_adequacy(n_frames)``** returns an explicit tier. The boundaries are
*derived* from the statistics, not chosen: 16 is exactly where the relative SD crosses ~37 %.

- ``cannot_compute`` (< 4) — raises, as before.
- ``computes_but_unreliable`` (4–15) — **warns**. A number comes out; it should not be
  believed.
- ``usable`` (16–63) — fine for a *relative* comparison between conditions acquired
  identically; not for an absolute brightness.
- ``recommended`` (64–255) / ``well_sampled`` (≥ 256).

### Fixed — N&B did not say whether its brightness was calibrated
With the defaults (``gain=1``, ``read_variance=0``) the output is **apparent** brightness —
σ²/⟨I⟩ in raw detector units. It is monotonic with molecular brightness and fine for comparing
conditions acquired identically, but it is **not a molecular brightness** and must not be read
as an oligomeric state. The result carried no indication of which it was.

The result now returns ``brightness_kind`` (``'apparent'`` / ``'calibrated'``),
``calibrated``, and ``calibration_notes`` — including that **without a monomeric reference
there is no scale on which "this is a dimer" means anything**, regardless of how good the
camera calibration is. A warning fires when reporting an apparent brightness.

### Fixed — the Evans moduli data did not say which frequencies were invalid
1.5.380 stopped the *plot* from clipping negative G′ onto a log axis. But the **data** still
said nothing: anyone reading ``g_prime_pa`` from the DataFrame, a CSV export or a table got a
bare number with no indication that it is meaningless at that frequency.

New ``validity`` and ``reliable`` columns, with four classes:

- ``supported`` — both moduli positive; the conversion is reliable.
- ``edge_affected`` — the Evans transform needs neighbours on both sides, so the spectral
  endpoints are systematically unreliable. **These used to be silently DROPPED**, so the user
  never learned the usable band was narrower than it appeared. They are now **returned and
  labelled**, and the plot excludes them (a point can be positive and still unreliable).
- ``sign_inconsistent`` — a modulus came out ≤ 0. **Expected** in a viscous-dominated medium,
  where G′ is genuinely ≈ 0 and noise pushes it negative. A null result, not an error.
- ``under_constrained`` — too few lag points contribute.

On a synthetic viscous medium (the regime PyCAT actually measures): **5 of 25 frequencies are
``supported``**, 18 are sign-inconsistent, 2 edge-affected. Previously a user exporting the
moduli got 25 numbers with no indication that 20 of them were meaningless.

### Note — the guard earned its keep
While adding the Evans warning I used ``napari_show_warning`` without importing it. The
undefined-name guard **caught it in the edit loop**, immediately — the same mistake that in
1.5.392 was found the slow way, through a functional test. Both ``condensate_physics_tools``
and ``nb_tools`` still import headlessly.

## [1.5.393] - 2026-07-10
### Fixed — focus scoring picked the sharpest DEBRIS, and the obvious fix made it worse
Focus was scored with a **single Brenner gradient over the whole frame**. That answers *"what
is the sharpest thing in the field?"* — and with dust on the coverslip the answer is often the
dust. Debris on a **different focal plane** has its own focus curve and peaks at a different
z, so the "best frame" can be the one where the junk is sharpest.

**The obvious fix — restrict the metric to a mask — is worse than doing nothing.**
Benchmarked across six synthetic z-sweeps (condensate and debris at different focal planes),
the correct frame (±1) was found by:

| strategy | correct |
|---|---|
| whole-frame Brenner | **1 / 6** |
| **masked** Brenner | **0 / 6** ← *worse than not masking* |
| **masked multimetric** | **6 / 6** |

Brenner alone is systematically biased a couple of frames early inside a small region: a
partially-defocused object still has a strong edge, and a squared-difference metric
over-rewards it. Masking does not fix that bias — **it exposes it.** Had I shipped the
"obvious" fix without benchmarking across cases, focus selection would have got *worse*.

- **``bf_focus_metric(image, mask=None)``** and **``focus_scores(stack, mask=None)``** now
  accept an optional region (2-D applied to every frame, or 3-D per-frame). ``mask=None``
  reproduces the previous scores **exactly** — verified, no regression.
- **New ``focus_scores_multimetric``**: Brenner + Laplacian variance + Tenengrad, returning
  each normalised series, each metric's peak, a **``consensus_frame``**, and an
  **``agreement``** score.
- **Frame Quality / Focus QC** now takes the **consensus of three metrics**, exposes a
  *"Restrict to mask"* dropdown, and reports the agreement. It warns when scoring the whole
  frame that the sharpest frame may be the sharpest dust.

**An honest limitation, stated in the docstring and the UI:** *high agreement does not mean
correct.* Unmasked, all three metrics agree **100 %** — on the debris. Agreement is a
**diagnostic** (it says when the focus call is being driven by something other than a clean
focus curve), not a proof of validity. The mask is what makes it right; the agreement score is
what tells you to look.

``temperature_tools`` also now imports headlessly (notification shim) — 19 GUI-coupled
scientific modules remain.

## [1.5.392] - 2026-07-10
### Fixed — a saturated partition coefficient is meaningless, not conservative
``partition_coefficient_field`` had **no detector-saturation check at all**. Once the dense
phase clips at the sensor ceiling, the numerator of Kp has been **truncated by an unknown
amount** and the measured value pins at the clip level:

| true dense | true Kp | previously reported |
|---|---|---|
| 65 000 | 650 | 650 |
| 150 000 | **1 500** | **655** |
| 400 000 | **4 000** | **655** |

A true Kp of 655, 1 500 or 4 000 **all silently read as 655** (bulk = 100, 16-bit sensor).

**And it is not a lower bound.** That is the tempting reading, and it is wrong: you cannot
say how far the true value lies above the measured one, because you do not know how much
signal the detector discarded. Reporting a number invites exactly that misreading — 655 looks
like a measurement, not a floor.

So a saturated Kp is now returned as **NaN**, not as a number:

- The saturation ceiling is inferred from the dtype (uint16 → 65535; a float image normalised
  to [0, 1] → 1.0) and can be overridden with ``saturation_level=`` when the full-well
  capacity is known.
- Both the **field-level** coefficient and each **per-droplet** coefficient are invalidated
  independently, so one blown-out droplet does not condemn the rest of the field.
- ``saturated``, ``saturated_fraction``, ``saturation_level`` and ``n_saturated_droplets``
  travel **with the result**, so a downstream consumer cannot use the number without seeing
  why it is (or is not) trustworthy.
- A warning states the affected fraction, how many droplets are involved, and what to do
  (shorter exposure, lower gain).

Threshold: >0.1 % of dense-phase pixels at the ceiling. Below that the truncation is
negligible against the other uncertainties; a handful of hot pixels should not condemn an
otherwise sound measurement. Validated against ground truth: no false positive on clean data
(Kp = 300 recovered exactly), correct invalidation above the ceiling, and correct behaviour on
both uint16 and float [0, 1] images.

### Note — the guard caught nothing because I did not run it
While writing the above I used ``napari_show_warning`` without importing it — an undefined
name, in a module that had been decoupled from napari (1.5.383). ``tests/test_no_undefined_names``
**would have caught it immediately**; it was found instead by a functional test, the slow way.
The guards belong in the **edit loop**, not only in CI. (The import now goes through the
``pycat.utils.notify`` shim, and ``invitro_tools`` still imports headlessly.)

## [1.5.391] - 2026-07-10
### Added — CI that enforces every guard from this audit
The audit's final "immediate blocker" was *"add Ruff correctness checks to CI"*. The other
six were fixed in 1.5.386–1.5.388; this one is the most valuable, because **it is what stops
the other six from coming back**.

**The Ruff config was silently doing nothing.** ``select``/``ignore`` sat at the top level of
``[tool.ruff]``, where modern Ruff **ignores them** — they belong under ``[tool.ruff.lint]``.
So the linter appeared to declare rules it was not enforcing. Fixed.

**New ``.github/workflows/core.yml``** (there was no CI at all). It installs **only** the
scientific dependencies — deliberately **not** napari, PyQt5 or cellpose. If a scientific
module needs a GUI stack to import, that is the failure this job exists to catch, and the fix
is to move the import, not to add it to CI.

Build-breaking gates, every one of which corresponds to a bug that actually shipped:

- ``F821`` undefined name → ``progress_emit``, ``mask_name``
- ``F823`` local used before assignment → the ``QSizePolicy`` ``UnboundLocalError``
- ``F811`` redefinition → duplicate ``run_expand_labels`` / ``resolve_measurement_source``
- ``F601`` repeated dict key → the duplicate batch-registry step
- ``B006`` mutable default argument
- ``B023`` loop variable captured in a closure (the classic Qt-callback bug)
- ``B904`` raise-without-``from`` inside ``except`` (destroys the original traceback)
- the AST guards (undefined names, use-before-import, duplicate definitions)
- the headless-import guard (13 scientific modules must import with no GUI)
- the core scientific suite

**Verified the CI will be GREEN on first push, not red** — every build-breaking gate was
dry-run against the current code and passes. A guard that fails on day one is a guard that
gets disabled.

### Fixed — exception chaining (B904): 8 sites were destroying the original traceback
``raise ImportError("install lumicks.pylake")`` inside an ``except ImportError:`` **discards
the real cause**. If the import failed because of a *version conflict* rather than absence,
the user is told to install a package they already have, and the actual error is gone. All 8
now use ``raise ... from _e``.

(Two further sites were checked and are **false positives** — a ``raise`` inside a class
*defined* in an ``except`` block, but *called* long afterwards. Verified empirically that
``__context__`` is ``None`` there, so there is nothing to chain; real Ruff scopes B904 to the
enclosing function and does not flag them.)

### Changed — F841 (unused locals) is ADVISORY, not build-breaking
34 findings, and the audit is right that they must be **reviewed individually rather than
deleted**. Several sit in stateful loading and time-series code — ``from_meta``, ``is_lazy``,
``ndim``, ``strategy_dd``, ``otsu_classes_spin`` — where an unused local may be the **residue
of logic that was partially removed while downstream code still behaves as if it existed**.
The question for each is not *"is it used?"* but *"was something meant to use it?"* Reported
in CI, never auto-fixed, does not fail the build until triaged. (Three of the 34 were mine,
introduced while writing ``stream_stats``; those are removed.)

**The full ~2,390 Ruff findings are NOT auto-fixed.** A global ``--fix`` across a 7 000-line
``file_io.py`` is exactly how a working codebase gets broken.

### Verified — the core scientific suite runs with no napari, no Qt, no GPU
FRAP mobile fraction (1.000 on a known curve), viscosity from diffusion, colocalization
Pearson (r = 0.969), partial-volume weighted statistics, and unbinned distribution fitting all
execute headlessly. That is precisely the ``tests/core/`` tier the audit asked for, and it now
exists and works.

### Recorded — the remaining architectural items
``file_io.py``'s split (7 000 lines, ~11 responsibilities — and ``stack_access.py`` is already
its first proven slice), the Cellpose model lifetime (one persistent model per device, never
per frame), and zarr completion markers plus write-ownership discipline (a cancelled run
currently leaves a partial cache that looks valid; and cache identity must include the code
version and every scientifically relevant parameter, which is a **correctness** hazard, not a
performance one). All in the roadmap with acceptance tests.

## [1.5.390] - 2026-07-10
### Fixed — trace extraction was 70× slower than it needed to be, and rejected lazy stacks
``molecular_counting_tools.extract_spot_traces`` did::

    for lbl in labels:
        region = labels == lbl
        trace = [stack[t][region].mean() for t in range(T)]

This rebuilds the boolean mask and **re-scans the entire frame once per (label, frame)
pair**. Cost = ``n_labels × n_frames × H × W``: for 50 puncta over 200 frames of 512×512
that is **2.6 billion pixel visits** to read 50 small regions.

Replaced with a single streaming pass in which ``np.bincount`` computes **every label's mean
at once**. **Measured 70× faster, results identical.**

*The audit suggested ``scipy.ndimage.mean``. Benchmarked — it is **0.7×, i.e. SLOWER** than
the original for sparse labels, because per-call overhead dominates. Measured, not assumed.
The winning form (gather the labelled pixels once, then ``bincount``) was neither the
original nor the suggestion.*

**And a pre-existing bug surfaced while testing it.** The function opened with
``stack = np.asarray(stack)`` — the frame-0 trap. On a lazy wrapper that silently collapses
a ``(T,H,W)`` movie to a single 2-D frame, and the very next guard then raised *"needs a
(T,H,W) stack"* **on a stack that is (T,H,W)**. The function was therefore **unusable on
every lazily-loaded movie**. It now checks ``.shape`` and reads frames one at a time;
verified identical on both eager and lazy stacks.

### Changed — bounded sliding window replaces batch-and-drain in both process pools
The audit believed the pipeline submits all frames at once. It does not — it already submits
in batches of ``n_workers × 4``. **But the batching is itself the bottleneck:** draining a
whole batch before submitting the next is a barrier, so every worker waits on the batch's
slowest frame. Frame cost is far from uniform (a dense field costs many times an empty one).

Measured on a realistic mix (12 % of frames 15× slower): **batch-and-drain 81 ms vs sliding
window 48 ms — 1.7× faster, with half as many tasks in flight.**

Both dispatch loops now keep ``2 × n_workers`` tasks outstanding and refill the moment any
completes. Validated: every frame processed **exactly once** (1/2/7/64/101-frame edge cases),
peak concurrency never exceeds ``n_workers``, and **cancellation now stops within about one
frame** (21 of 200 frames, versus running all 200) — previously the cancel check sat *between
batches*, so Cancel had to wait for a whole batch to drain.

### Fixed — worker processes were oversubscribing the CPU 4×
``run_pycat`` sets ``OMP_NUM_THREADS=4`` for the main process, and worker processes
**inherit the environment**. So 8 workers × 4 OMP threads = **32 threads on an 8-core
machine**. Oversubscribed threads do not go faster; they thrash cache and burn time
context-switching — and each worker is already a full process using one core, so the nested
BLAS/OpenCV/scikit-image pools are pure overhead.

Both ``ProcessPoolExecutor``\\ s now take an ``initializer`` that pins each worker to a
single compute thread (``OMP``/``MKL``/``OPENBLAS``/``NUMEXPR``/``VECLIB``, plus
``cv2.setNumThreads(0)`` and ``torch.set_num_threads(1)``). Verified in real subprocesses:
workers previously inherited ``OMP_NUM_THREADS=4``; they now report ``1``. Measured 2.1×
faster in a 1-CPU sandbox — the effect on a real 8-core machine is **larger, not smaller**.

The main process is deliberately left alone: interactive single-image work there genuinely
benefits from BLAS parallelism.

### Recorded — the object-feature table (audit points 7 & 8)
Several workflows call ``regionprops_table`` independently on the same mask, and the optional
Ripley/PCF pass re-derives centroids and cell labels that the primary per-object pass already
computed. The fix is to make the object-feature table a **first-class pipeline artifact** —
computed once per (mask, frame) with the union of every consumer's properties, and read by
condensate analysis, spatial statistics, morphology, tracking, summaries and plotting alike.
This is the cheapest concrete instance of the Biological Object Model already on the roadmap.
Recorded there with an acceptance test (``regionprops_table`` called at most once per (mask,
frame); Ripley/PCF consumes the table with identical numerical output).

## [1.5.389] - 2026-07-10
### Added — ``stack_access``: the pure-numpy core of the lazy/streaming layer
The audit's ``*_core.py`` split, done surgically where it actually pays.

``materialize_stack``, ``iter_frames``, ``layer_is_stack`` and ``extract_2d_plane`` are the
functions every analysis module needs in order to read a possibly-lazy stack safely. They
are **pure numpy** — verified by AST that none of them touches AICSImage, Qt, napari or
skimage. Yet they live in ``file_io.py``, which imports **AICSImage + PyQt5 + napari +
ui_utils** at module scope, and **15 toolbox modules import from ``file_io`` purely to reach
them**. So every one of them drags the entire GUI and file-format stack into memory just to
iterate frames over an array it already holds. That is why the scientific tests could not be
collected in a minimal environment (the audit's point 10 — the other three examples it gave
were already fixed in 1.5.378/383; ``file_io`` was the one that remained).

**New ``pycat/file_io/stack_access.py`` imports with nothing but numpy** — verified with
``napari``, ``PyQt5``, ``aicsimageio`` and ``skimage`` *all forcibly blocked*.

It adds the audit's explicit access-pattern contract:

- ``get_array_source(layer, access_pattern=...)`` — ``framewise`` (default) returns a source
  to stream; ``full`` **raises unless ``allow_materialize=True``**. Pulling a multi-gigabyte
  movie into RAM should be a deliberate act visible at the call site, not the silent default
  it is today.
- ``read_frame(source, t)`` — the framewise primitive, safe on lazy wrappers, zarr, dask and
  plain arrays.
- ``stream_stats(source)`` — global min / max / mean / std / percentiles in **one streaming
  pass**, never materialising.

Validated against the trap it exists to prevent: ``np.asarray`` on a lazy wrapper returns
**frame 0 only** (shape ``(16,16)`` from a ``(12,16,16)`` stack, silently);
``materialize_stack`` recovers all 12; ``get_array_source(..., 'full')`` correctly refuses
without ``allow_materialize``; and ``stream_stats`` reproduces min/max/mean/std exactly.

### Fixed — global normalisation allocated the entire movie to obtain one scalar
``timeseries_condensate_tools.py:652``::

    _global_norm_max = float(np.asarray(_src_for_max[:]).max())

``store[:]`` on a **zarr** array pulls the whole stack into RAM. For a 1.5 GB movie that is
1.5 GB allocated for a **single scalar** — which defeats the entire point of the zarr
backing.

**And the failure mode was worse than the cost.** When that allocation failed, the
surrounding ``except Exception`` silently substituted ``norm_max = 1.0`` — i.e. a **wrong
normalisation**, not a missing one. Running out of memory produced a quietly mis-scaled
movie rather than an error.

Replaced with a streaming reduction (one frame at a time, running max). Verified to give the
**identical** answer without ever holding the movie, and the fallback now prints why it fired
instead of silently changing the normalisation.

### Note — the remaining consolidation is recorded, and it defeated me twice
``file_io.py`` still defines its own copies of the five helpers, so they are duplicated. The
clean finish is to delete them there and re-export from ``stack_access``. **Attempted twice,
broke ``file_io.py`` both times** (line-index deletion cut into an adjacent docstring; an
``ast.get_source_segment`` removal plus a blank-line-collapsing regex corrupted an indented
docstring). No third attempt. Recorded in the roadmap with the method that will work
(``libcst``, or hand-editing with a compile check after each removal) — along with a warning
that the sandbox git is **59 releases behind**, so ``git checkout`` on a file there destroys
the session's work rather than restoring it. That happened during this attempt; the file was
recovered from the shipped ``1.5.386`` artifact.

## [1.5.388] - 2026-07-10
### Fixed — duplicate function definitions (one of them mine, and NOT harmless)
``label_and_mask_tools.py`` defined ``run_expand_labels`` and ``run_mask_logic_merge``
**twice** each. Python keeps the later one; the earlier becomes dead code. Verified
mechanically that the copies were **functionally identical** (same signature, same
computational calls) before removing them — the second was simply a compressed,
less-documented copy. The clearer first versions are retained, and their stale inline
``from napari.utils.notifications import ...`` now goes through the shim (1.5.378).
Re-tested: expand-labels grows regions, and AND/OR/XOR return exactly 4/28/24 px on a
known overlap.

**A codebase-wide sweep then found a third — in code I wrote.**
``partial_volume_tools.resolve_measurement_source`` was defined twice, and this pair was
**not equivalent** (different helper functions, different return shape). The **second** was
the live, validated one. So the audit's blanket advice — *"retain the first, clearer
implementation"* — would have **broken the lineage resolver**. The rule has to be *check
which one actually runs*, not *keep the first*. The dead first definition was removed and
the resolver re-validated end-to-end (mask → 4× upscale → original, factor 4).

**The codebase now has zero duplicate definitions**, and
``tests/test_no_undefined_names.py`` guards against new ones — with the above written into
its failure message, so the next person does not apply the blanket rule and break something.

### Measured — 400 silent exception handlers, and why the ``file_path`` bug hid so long
Classified every broad handler by AST:

| | logged | re-raised | **silent (pass)** | **silent (other)** |
|---|---|---|---|---|
| core (non-UI) | 76 | 5 | **199** | **201** |
| UI | 82 | 0 | 208 | 105 |

**876 broad ``except Exception`` handlers; 400 are silent in non-UI code.** ``file_io.py``
alone holds **101**. This is precisely why the orphaned ``file_path`` block (1.5.386) could
raise ``NameError`` on **every tagged layer load** without anyone noticing — its own
``except Exception: return False`` ate the evidence.

The infrastructure to fix it already exists: ``debug_log(context, exc)`` prints a traceback
when ``PYCAT_DEBUG=1`` and is otherwise silent. It is simply not applied at those 400 sites.

**Not swept in this release, deliberately.** It was attempted and it **broke ``file_io.py``
twice** — an ``except Exception:`` whose body sits on the same line defeats naive line
insertion, and a two-pass edit shifts line numbers under itself. Forcing a large mechanical
rewrite of the most critical file in the codebase at the tail of a release is exactly the
move that has broken this build before. The measurement, the recipe, the file-by-file
priority order, and an explicit warning not to sweep it with a regex are recorded in the
roadmap under *Silent exception swallowing*, together with the audit's longer-term rule
(typed errors in core, broad catch only at the UI boundary).

The **stale/unused variables** item is recorded alongside it, with the point that matters:
several sit in stateful loading and time-series code, so the question for each is not "is it
used?" but "**was something meant to use it, and does the code silently behave as if it
did?**" — a symptom to read, not a lint to clear.

## [1.5.387] - 2026-07-10
### Fixed — three widgets could never be constructed (UnboundLocalError, not NameError)
``intensity_profile_tools``, ``molecular_counting_tools`` and
``morphological_complexity_tools`` each use ``QSizePolicy`` a few lines into the widget
builder — but the **only** import of it sat in a *later* ``else:`` branch of the **same
function**.

Because Python sees the name assigned somewhere in the function, it treats ``QSizePolicy``
as a function-**local** for the entire scope. The earlier use therefore raises
**``UnboundLocalError``**, not ``NameError`` — and it fires **unconditionally**, because the
``else:`` branch is irrelevant to the hoisting. **Intensity Profile, Molecular Counting and
Morphological Complexity were dead on arrival: the widgets could not be built at all.**

Fixed by adding ``QSizePolicy`` to the early Qt import in each module, where it is actually
first used.

### Fixed — the guard from 1.5.386 missed this, so it has been extended
The undefined-name guard checked only for names bound **nowhere** (``NameError``). It did
not model **execution order**, so it saw the late import and considered the name bound. That
is a guard giving a false sense of safety, which is worse than no guard.

``tests/test_no_undefined_names.py`` now checks **two** shapes:

1. **Unbound** — bound nowhere in the enclosing scope chain (``NameError``).
2. **Used before assignment** — the name *is* a local of the scope, but every binding of it
   occurs *after* the use (``UnboundLocalError``).

Check (2) is deliberately restricted to names bound **only by import statements**, where the
binding line is unambiguous and no control-flow analysis is needed — that is where the real
bugs were, and it keeps the check free of false positives. Validated three ways: it **now
catches** the ``QSizePolicy`` bug it previously missed, it **passes** on the current codebase
(0 findings), and it produces **no false positives** on legitimate late, conditional, or
nested-function imports.

### Changed — the batch registry now REJECTS duplicates instead of warning about them
1.5.386 added a warning when ``_STEP_MAP`` contained a repeated key. The audit's stronger
suggestion is right: reject at construction. ``BatchProcessor.register_step`` now raises if a
name is registered twice with a *different* handler (re-registering the *same* function stays
idempotent, so a reload does not break). This catches registry drift from **any** source, not
just the dict literal. 68 steps, zero duplicates.

### Note — the thread-safety concern about the Ripley block does not apply
The audit suggested the ``mask_name`` fix (1.5.386) should also stop the worker from reading
napari layers, since "reading napari layers from worker code creates thread/process-safety
problems". Checked: ``_on_finished`` is connected to ``worker.finished``, a Qt signal
delivered on the **UI thread** — it is not worker code, and the layer access there is
legitimate. Separately, an AST sweep confirms **no ``QThread.run()`` body in the codebase
touches ``viewer.layers``**. The general principle is sound; it simply is not violated here.
Recorded so the correct code is not "fixed" into something worse.

## [1.5.386] - 2026-07-10
### Fixed — ticking "Ripley's L / PCF" silently produced no Ripley and no PCF
A scope-correct static analysis of the whole codebase found one remaining undefined name,
in the time-series pipeline the audit flagged but did not detail.

``timeseries_condensate_tools.py`` reads ``mask_name`` inside ``_on_finished``, but
``mask_name`` is a **local of ``_on_run``** — a *sibling* nested function. Siblings do not
share locals: a closure sees the *enclosing* scope, not another nested function's frame.
So the line raised ``NameError``.

**And the error was swallowed.** The Ripley block sits inside ``try: ... except Exception``,
so there was no crash and no warning — the user ticked the box, the analysis ran, and the
**Ripley's L and PCF results simply never appeared**. That is worse than a crash: a crash
gets reported; a silent omission gets accepted.

Fixed using the idiom the file already uses for exactly this problem — a one-element list
as a mutable cell (``_run_ripley_ref`` sits three lines above, with the comment *"mutable:
set in _on_run, read in _on_finished"*). ``mask_name`` simply never got the same treatment.

### Added — a guard so this class of bug cannot return silently
``tests/test_no_undefined_names.py`` models Python's scoping properly (closures,
comprehensions, lambdas, class bodies, ``global``/``nonlocal``) and fails on any name bound
**nowhere** in its enclosing chain. Validated three ways: it passes on the current codebase
(0 findings), it **catches all three** real bugs when they are reconstructed, and it
produces **no false positives** on legitimate closures.

This matters because Python does not catch these at import time. A ``NameError`` from a
misplaced variable fires only when that *line* runs, so it can sit in a button handler
indefinitely — and all three instances in this codebase were then wrapped in an
``except Exception`` that converted the crash into a feature that quietly did nothing:

* ``advanced_analysis_ui.py`` — ``progress_emit`` used three lines *before* the nested
  ``_task`` that declares it as a parameter. The Dynamic Spatial Analysis button died
  before the worker was created; it could never have run. (Fixed in this cycle.)
* ``file_io.py`` — the body of a ``_has_structured_metadata`` method had been accidentally
  merged into the tail of ``_apply_saved_tags_to_layer``, referencing a ``file_path`` that
  is not a parameter there. It raised on **every tagged layer load** and swallowed it in
  its own ``except Exception: return False``. Not restored: the job it described is already
  done, and done better, by ``_tiff_multipage_undeclared`` (1.5.351), which checks the
  actual axis *label* rather than merely whether some dims can be read.
* ``timeseries_condensate_tools.py`` — the ``mask_name`` bug above.

**The codebase now has zero undefined names.**

### Fixed — a duplicate batch-registry entry, and a guard against the next one
``_STEP_MAP`` registered ``'morphological_complexity'`` twice; Python silently keeps the
later one. Both were no-op skip stubs here, so nothing broke — but it is a **latent trap**:
implement a real replay handler at the first location, and a stale stub further down the
dict overrides it, and you debug a handler that never runs. The duplicate is removed, and
``register_all_steps`` now inspects the source and **warns loudly** if a step name is ever
written twice. 68 steps, zero duplicates.

## [1.5.385] - 2026-07-10
### Changed — bead-radius provenance: a dropdown, not an essay (and the default is right)
Corrections to 1.5.384 after review. The framework was right; the ergonomics and one
default were not.

- **The default radius source is now ``manufacturer``**, not ``assumed``. That is the
  realistic case — bead radii come from the specification sheet — and it should not read
  as a deficiency in the report.
- **Deriving a radius from the image is flagged, not treated as fatal.** The physics is
  still worth stating: the imaged blob is the bead **convolved with the PSF**, and for a
  200 nm bead at ~1.2 NA the PSF is comparable to the bead itself, so the apparent size is
  dominated by the optics — you would be measuring the microscope, and the viscosity would
  come out too low. But comparing the apparent size to the specification as a **sanity
  check** (does this look like the beads I bought? are they aggregated? is this the right
  vial?) is good practice, and the warning now says so instead of implying the check itself
  is an error.
- **Provenance is captured as a dropdown** (``manufacturer`` / ``calibrated`` / ``metadata``
  / ``assumed``) plus an optional free-text note, not as a sentence to retype every run. A
  dropdown is one click and it is **structured** — it can be batched, queried and exported,
  which a free-text string cannot. The full standard ("0.100 µm, manufacturer spec, ±5 %")
  lives in the **tooltip**, where it teaches without demanding.
- The source and note are stored with the result and in the recorded workflow step, so a
  replayed or batched run carries the same provenance.
- **Parameters that are *supposed* to be fitted are no longer flagged.** A diffusion
  coefficient **is** the output of an MSD fit; marking it "not independently established"
  is pedantic noise, and noise is how warnings get ignored. ``Parameter.expected_fitted``
  distinguishes a legitimately-fitted value from one that should have come from a
  specification. Temperature remains flagged — it is usually a room-temperature assumption,
  and it sits inside *kT*.

The viscosity summary is printed to the terminal after each microrheology run, so the
number arrives with its assumptions attached rather than alone.

## [1.5.384] - 2026-07-10
### Added — measurements that can account for themselves (the audit's central thesis)
The audit's summary is that PyCAT should move from *"can compute"* to
*"can compute → passes assumptions → quantified uncertainty → physically interpretable"*,
and that most methods stop at the first stage. Scored against the code, that is right:
uncertainty exists in several places, but **nothing records what a number rests on**, and
**nothing states when an assumption has failed**.

``viscosity_from_diffusion`` is the sharpest example. It takes a bare ``float`` bead
radius and returns a bare ``float`` viscosity. That float cannot tell you the two things
most likely to make it wrong:

* **Where the bead radius came from.** η = kT/(6πRD), so **η ∝ 1/R** — a radius 30 % wrong
  makes the viscosity 30 % wrong, silently. Critically, a radius *measured from the image*
  is **not** the physical radius: the imaged blob is broadened by the PSF, so a fitted
  optical radius is systematically **too large** and the viscosity correspondingly **too
  small**. Only a manufacturer specification or a bead-batch calibration should enter
  Stokes-Einstein.
* **Whether the probes sampled bulk material.** Stokes-Einstein assumes a bead in a
  homogeneous continuum away from interfaces. Excluding beads near the host boundary helps
  but does not *prove* bulk sampling — beads stick, sit in heterogeneous regions, or become
  confined. If that fails, the number is not a bulk viscosity whatever the arithmetic says.

**New ``pycat.utils.measurement``**: a ``Measurement`` carries its value **with** its
uncertainty, the ``Parameter``s it depended on (each tagged ``CALIBRATED`` /
``MANUFACTURER`` / ``METADATA`` / ``FITTED`` / ``ASSUMED`` / ``UNKNOWN``), the
``Assumption``s it rests on (each ``HOLDS`` / ``VIOLATED`` / ``UNCHECKED``), and the
``ValidationLevel`` of the method (``IMPLEMENTED`` → ``ANALYTICALLY_VALIDATED`` →
``SIMULATION_VALIDATED`` → ``EXPERIMENTALLY_VALIDATED``). It derives an
``Interpretability`` state, and when an assumption has failed it says so in plain English:

> *"An assumption FAILED (physical_probe_radius). The number exists, but it should not be
> reported as viscosity."*

**New ``viscosity_measurement``** demonstrates it on PyCAT's deepest dependency chain
(pixel size → detection → linking → MSD → D → Stokes-Einstein). Same arithmetic, three
outcomes:

* radius from a **manufacturer spec**, α = 1.01, bulk sampling verified → **interpretable**,
  0.484 Pa·s [0.427, 0.544].
* radius **fitted from the image** → **0.322 Pa·s (−33 %)** and marked **not
  interpretable**, with the reason. The old function returns 0.322 with no indication
  anything is wrong.
* **α = 0.62** (not Brownian) and D correlating with distance from the interface → **both
  assumptions fail**. Stokes-Einstein does not apply; the number is not a viscosity.

The diffusion interval is propagated to a viscosity interval (η ∝ 1/D, so the bounds
invert), and a fitted α far from 1 is flagged — because in the viscous-dominated media
PyCAT normally measures, the **true α is 1**, so a fitted value far from it usually
indicates linking artefacts or D–α–σ_loc covariance rather than genuine anomalous
diffusion.

``viscosity_from_diffusion`` is unchanged, so nothing breaks. This is the pattern the other
physical outputs (partition coefficient, C_sat, FRAP mobile fraction, moduli) should adopt.

## [1.5.383] - 2026-07-10
### Fixed — the scientific tests could not run at all
The audit reported that the test suite fails during **collection** because scientific
modules import napari or PyQt at module scope. Reproduced directly, and it is worse than
a nuisance:

| test module | imports | status before |
|---|---|---|
| ``test_coloc_metrics`` | ``pixel_wise_corr_analysis_tools`` | **BLOCKED** (PyQt5) |
| ``test_partition`` | ``partition_enrichment_tools`` | **BLOCKED** (napari) |
| ``test_feature_analysis`` | ``feature_analysis_tools`` | **BLOCKED** (napari) |
| ``test_image_processing`` | ``image_processing_tools`` | **BLOCKED** (napari) |

**Four of six scientific test modules could not even be COLLECTED** — they failed at
import time, before a single assertion ran. These are tests of *pure numerical
functions*: colocalization coefficients, partition coefficients, feature measurements,
image filters. None of them need a window.

The coupling is **transitive**, which is why it spreads: ``feature_analysis_tools`` was
un-importable **not because of its own imports** but because of ``image_processing_tools``
three levels down the graph. One convenient line at the base blocks everything above it.

- ``image_processing_tools``, ``partition_enrichment_tools`` and
  ``pixel_wise_corr_analysis_tools`` are now decoupled (notifications through the
  ``pycat.utils.notify`` shim; ``napari.layers`` isinstance checks and Qt/``pycat.ui``
  helpers imported at call time, inside the viewer-facing functions that only run when a
  viewer exists; the Qt dialog degrades to a clear error rather than blocking the module
  import).
- **All six scientific test modules now import with no napari and no Qt**, and the
  functions were re-verified headlessly: Pearson r = 0.969 on a correlated pair,
  upscaling 32² → 64², the FRAP mobile fraction (1.5.381) returning 1.000 for a fully
  mobile species, and the viscosity chain producing a physical value.
- **Two test tiers** are declared in ``pyproject.toml``: ``pytest -m core`` (pure
  scientific kernels — no napari, no Qt, no GPU; run on every commit) and
  ``pytest -m integration`` (viewer behaviour, file IO, Qt).
- **New guard test** ``tests/test_headless_science.py`` fails if a GUI import is
  re-introduced at module scope in any of 13 guarded scientific modules. Without it this
  decoupling silently rots — it takes one convenient import line to undo. The failure
  message says what to do instead, so the fix is not "add napari to the test
  environment".

**13 of 13 guarded modules pass. 20 ``*_tools`` modules remain coupled** — a bounded,
mechanical job, now enforced as each is converted rather than being swept in at the tail
of a release.

## [1.5.382] - 2026-07-10
### Fixed — the C_sat fit threw away the most informative samples, and could return a negative concentration
``estimate_csat_lever_rule`` does ``above = phi > 0`` and then regresses **only those
points**, extrapolating the x-intercept. Two problems, both verified against a synthetic
dilution series with a known boundary:

1. **The zeros are discarded, and they are the most informative points.** A sample at
   C = 5 with Φ = 0 says *"the boundary is above 5"* — a **direct constraint on the very
   quantity being estimated**. These are **censored observations**, not missing data.
   Throwing them away and extrapolating an intercept from the points furthest above the
   boundary is the least stable way to locate it.

2. **No uncertainty is reported, and the extrapolation is fragile.** On a series with only
   two points just above the boundary, the old fit returned **C_sat = −6.87** — a
   *negative* saturation concentration, which is not a physical quantity (error −169 %).
   Even on a well-behaved series, σ = 0.004 noise on Φ moved the recovered boundary across
   **[8.9, 11.0]** — and the function returns a single number with no interval at all.

**New ``estimate_phase_boundary``**: a segmented (hinge) fit of Φ(C) = max(0, s·(C − C_b))
over **all** the data, zeros included. The hinge location *is* the boundary — it is fitted,
not extrapolated to — and a bootstrap 95 % interval is returned. On the failing case above
it recovers **9.28** (true 10) with an interval of [5.9, 12.0]. The intervals are
informative in their own right: a series that straddles the boundary poorly returns a wide
interval (e.g. [9.4, 25.0]) where the old code returned a confident-looking bare number.

It also warns explicitly when there are **no samples below the boundary** — that is the
extrapolation regime, and the fix is experimental, not computational: *include
concentrations that produce no condensates. A zero is a real measurement.*

**Naming.** The result is ``boundary_concentration`` — the **lever-rule apparent
boundary** — not ``C_sat``, and the dense-phase value is ``dense_axis_intercept``, not
``C_dense``. Calling either a concentration asserts (a) that Φ is a true volume fraction
and (b) that the concentration axis is calibrated. When Φ came from a 2-D image it is a
*projected area fraction* (see 1.5.378), so the boundary is systematically biased; and if
the "concentrations" were fluorescence intensities, the boundary carries those units. It
remains a sound **relative** measure — a boundary shift between conditions imaged
identically is real — but it is not an absolute C_sat without volumetric and concentration
calibration.

The original ``estimate_csat_lever_rule`` is retained so nothing breaks.

## [1.5.381] - 2026-07-10
### Fixed — FRAP reported a fully mobile protein as "70 % immobile"
``fit_frap_recovery`` computed ``mobile_fraction = b − a`` unconditionally. That is
correct **only** under Taylor normalisation, and then only by accident.

The mobile fraction is the fraction of the material that was *bleached* which
subsequently recovered:

    mobile = (plateau − post-bleach) / (pre-bleach − post-bleach) = (b − a) / (1 − a)

``b − a`` omits the denominator — and the denominator **is the bleach depth**.

* Under **Taylor** normalisation the immediate post-bleach value is forced to 0 by
  construction, so ``a ≈ 0`` and ``(b − a)/(1 − a) → b − a``. The formula was right for
  the wrong reason.
* Under **pre-bleach** normalisation (``I / I_pre``) — which PyCAT also exposes — ``a``
  is the bleach depth and is far from zero. The error is exactly
  ``−(1 − bleach_depth)`` and grows as the bleach gets **shallower**.

Verified against ground truth: **a 30 %-deep bleach on a fully mobile protein
(true mobile = 1.0) was reported as 0.30 — i.e. "70 % immobile" for a species that is
entirely mobile.** At a 50 % bleach it reported 0.50; only a very deep bleach came
close to the truth.

- The mobile fraction is now computed normalisation-agnostically as ``(b − a)/(1 − a)``,
  which reduces to ``b`` when ``a = 0`` (Taylor) and is correct when ``a > 0``
  (pre-bleach). Validated on curves generated from the module's own rational recovery
  model: **0.0 % error at every bleach depth under both normalisations.**
- **``bleach_depth`` is now reported separately**, because it is an acquisition property,
  not a biological one.
- **``over_recovery`` flags a plateau above the pre-bleach level** (``b > 1``), which is
  not physical for a simple recovery and usually indicates a normalisation or
  photofading-correction problem (an over-aggressive reference correction will do it).
- The mobile fraction returns ``NaN``, rather than dividing by ~0, when the bleach
  removed essentially nothing — in that case it is simply not identifiable from the
  curve.
- ``frap_tools`` now imports **headlessly** (via the notification shim from 1.5.378), so
  the FRAP physics is testable with no GUI stack.

### Fixed — "optical density" asserted a physics that brightfield condensates do not obey
``compute_optical_density`` documented itself as: *"OD is directly proportional to
condensate concentration × path length, making it the brightfield equivalent of
fluorescence intensity as a concentration proxy."*

That is Beer–Lambert, which requires **absorbance**. Condensates in transmitted light are
predominantly a **refractive-index** contrast — they scatter and phase-shift light far
more than they absorb it. So ``−log₁₀(I/I₀)`` on such an image is measuring scattering and
phase effects, and calling it a concentration proxy asserts a relationship that has not
been established. This is the same category error as reporting a projected area fraction
as a "volume fraction" (fixed in 1.5.378): an image-derived proxy given a physical name.

- The function now documents itself as **apparent optical density**, lists the six
  conditions that must ALL hold for ``−log₁₀(I/I₀)`` to be a genuine absorbance (stable
  illumination, linear detector, valid flat-field, known I₀, no saturation, and the
  contrast actually being absorbance rather than scattering), and states plainly that
  ordinary brightfield and phase contrast violate the last of these for condensates.
- It remains a useful **relative** proxy for images acquired identically — that is stated
  too, rather than throwing the measurement away.
- The brightfield and in-vitro-brightfield UIs now display **"mean apparent OD
  (scattering/phase, not calibrated absorbance)"** instead of a bare "mean OD".
- For bulk work (turbidity, cloud point), the docstring directs users to **field-level
  integrated transmission**, which does not require the per-pixel absorbance assumption,
  and keeps bulk transmission, object morphology, and local apparent OD as three distinct
  quantities.

## [1.5.380] - 2026-07-10
### Fixed — the moduli plots manufactured an elastic modulus that was not measured
The Evans conversion itself is correct and does **not** clip: ``g_prime_pa`` carries
negative values through faithfully. **The plots did the damage.** Three separate ways,
all of which turn an honest null result into an apparent measurement — and all of which
bite hardest in exactly the regime PyCAT is used for.

Biological condensates are **viscous-dominated** at accessible lag times (roughly water
to well past honey). In that window there is little elasticity to measure, so the true
G′ is ≈ 0 and **noise pushes it negative routinely**. On a synthetic η = 7 Pa·s medium —
the regime of the validated ~8.3 Pa·s bead data — **11 of 20 G′ points come out negative,
and 19 of 20 bootstrap confidence bands straddle zero.** That is correct physics: the
data are consistent with *no measurable elasticity*.

What the plots did with that:

1. **Clipped the lines.** ``np.clip(g_prime, 1e-12, None)`` on a **log axis** mapped
   every negative G′ to the floor and drew it as a positive point — rendering "the Evans
   conversion is not locally valid here" as a tidy, everywhere-positive G′ curve.
2. **Clipped the confidence bands** — the worse one. A bootstrap band whose lower bound
   is negative *straddles zero*, i.e. the data cannot distinguish the modulus from zero.
   Clipping that lower bound to 1e-12 makes the band appear to **exclude** zero,
   converting "not significantly different from zero" into "significantly positive
   elasticity".
3. **Annotated a crossover from ``sign(G′ − G″)``.** Where G′ is noise about zero, that
   sign flip is noise — so the figure could label a physically meaningless crossover
   frequency.

Fixed in both the consolidated panel and the standalone moduli window:

- Negative moduli are **never clipped**. Only positive points are drawn (they are the
  only ones representable on a log axis); the rest are marked with ``×`` at the axis
  floor and counted in the legend (e.g. *"G′ (storage) [11/20 ≤ 0]"*).
- **Confidence bands are drawn only where the whole band is positive.** Bands that
  straddle zero are not drawn and are reported instead — because a band straddling zero
  is a *result*, not a rendering problem.
- **The crossover is annotated only where both moduli are positive**, so noise in G′
  cannot be reported as a material crossover.
- The figure carries a note stating that this is expected for a viscous-dominated medium,
  and that **passive VPT cannot resolve a G′/G″ crossover in this regime — active
  microrheology (optical tweezers) is the correct technique.**

### Note — the VPT track-rejection concern was checked and is NOT supported
The audit warned that ``compute_msd``'s IQR fence on first/last-lag MSD "risks selecting
tracks based directly on the outcome variable" (MSD → D → viscosity). Tested against
ground truth rather than assumed. On a synthetic mixed population the fence rejects:

| population | rejection rate |
|---|---|
| honest **long** tracks | **2 %** |
| honest **short** tracks | 22 % |
| **mis-linked** tracks | **70 %** |

It is not meaningfully selecting on the outcome: honest long tracks are rejected at the
noise-tail rate of any IQR fence, and on clean Brownian data with no mis-links the filter
biases D by only **+1.1 %** (viscosity −1.1 %). What it actually removes is mis-links —
which is its stated purpose — and short tracks, which cannot constrain a viscoelastic
response anyway. **No change made.** (The one regime where the short-track loss would
matter — a G′/G″ crossover at short lag — is outside passive VPT's reach for these media
regardless, and is properly addressed by active microrheology.)

## [1.5.379] - 2026-07-10
### Fixed — the SNR diagnostic told users that background subtraction destroys their data
The audit flagged that SNR is computed as ``<signal> / sigma_bg`` without subtracting the
background mean. Verified in ``pipeline_snr_tools`` — and the consequence is worse than a
mislabelling.

Because the background is not subtracted, the metric is **inflated by the camera
pedestal**, which is an instrument constant with no physical content. Measured on a
synthetic image with a real contrast of 50 over a noise σ of 5: adding an offset of
0 / 100 / 500 / 2000 counts reported an "SNR" of **28 / 78 / 282 / 1049** — the identical
image. It is therefore not comparable across cameras or across sessions.

**The damage is not academic.** This module computes ``delta_snr`` *across preprocessing
steps* to tell the user whether a step helped, and colours the table green or orange
accordingly. Background subtraction **removes the pedestal** — so the tool reported
**Δ = −257** for one of the most valuable steps in the pipeline, painting it as
destructive, when the true contrast change was **+1.5**. A user following the tool's own
advice would have turned background subtraction off.

- The table now reports **CNR** = ``(<signal> − <background>) / sigma_bg`` — the
  contrast-to-noise ratio, which is invariant to the camera offset (it reported ~27 in
  all four cases above). Columns, colour-coding, and the "best step" summary are all
  driven by CNR. Verified after the fix: background subtraction reads **+1.5**
  (correctly neutral) and denoising reads **+40** (correctly a large win).
- The old un-subtracted ratio is retained as ``snr_raw`` and labelled an
  *intensity-to-noise ratio*, since it is a legitimate relative number *within* a single
  image. The legacy ``snr`` key now carries the honest metric, so existing consumers are
  corrected automatically.
- (``data_qc_tools.qc_snr`` was also checked and is **not** affected — it uses a
  percentile range, which is implicitly a contrast measure.)

### Fixed — distribution model selection by R² on a histogram
``fit_size_distribution`` chose between lognormal and power law with
``preferred = 'lognormal' if r2_ln >= r2_pl else 'power_law'`` — R² computed on **binned
counts**. Verified to be unreliable in exactly the way the audit describes: on data drawn
from a **true power law**, it returns ``power_law`` at 8 bins and **``lognormal`` at 15,
30 and 50 bins**. An arbitrary bin choice flips the scientific conclusion, and it flips
it *toward* lognormal — so a genuine power law is the result most likely to be missed.
Across 12 ground-truth cases it identified the correct distribution **25 %** of the time.

- **New ``fit_size_distribution_mle``**: unbinned maximum-likelihood fitting of
  lognormal, **gamma**, **Weibull**, exponential and power law, ranked by AIC and
  compared with a **Vuong likelihood-ratio test**. Gamma and Weibull are included
  deliberately — for coarsening droplets they are often better descriptions than a forced
  lognormal-vs-power-law choice. **Whole-sample identification accuracy: 100 %** on the
  same ground-truth cases.
- **It reports when it cannot tell.** With few objects the honest answer is usually "these
  data cannot distinguish these models"; ``distinguishable=False`` says so and the verdict
  explicitly instructs the user not to report a preferred model as established.
- **The power law is fitted by MLE above an estimated ``x_min``** (Clauset
  KS-minimisation), and is reported **separately from the whole-sample ranking**, scoped
  to its tail. This is deliberate and was learned the hard way: a version that let the
  tail-only power law compete for "best model" reported **"power law" for data drawn from
  lognormal, gamma AND exponential distributions**, because the upper tail of almost any
  distribution is locally power-law-like. Adding a KS goodness-of-fit gate did **not** fix
  it (those tails genuinely pass, p ≈ 0.6–0.8). Conflating "is the tail power-law-like
  above a cut-off I chose?" with "what distribution are my sizes drawn from?" is how
  spurious power laws get published; the two questions are now answered separately.
- The old ``fit_size_distribution`` is retained (nothing breaks) but should be treated as
  a **descriptive histogram fit**, not model selection.

## [1.5.378] - 2026-07-10
### Fixed — audit response: a projected area fraction was being reported as a volume fraction
An external code audit flagged that 2-D analyses report ``total_area / field_area``
under the name **volume_fraction**. Verified: ``field_summary`` and
``coarsening_statistics`` in ``invitro_tools`` both did exactly this — and the
function's own docstring said *"Φ = total droplet area / field area"* while naming the
output a volume. The in-vitro UIs then displayed it as **"Φ"**, the standard symbol for
volume fraction in the phase-separation literature. A reader had no reason to suspect it
was not one.

- **The honest name is now ``projected_area_fraction``** in both functions.
  ``volume_fraction`` is retained as a **deprecated alias** so existing scripts and
  saved tables continue to work, and the values are unchanged. The UIs now display
  *"area fraction (2D projection, not a volume fraction)"* rather than "Φ".
- **The area fraction is not a volume fraction.** It coincides with one only for an
  isotropic random section through a statistically homogeneous 3-D material, or a
  genuinely quasi-2-D chamber. In a flow cell neither holds: droplets settle (so the
  value depends on focal depth) and larger droplets are more likely to intersect any
  given plane (biasing the in-plane size distribution). Use the Z-Stack (3-D) workflow
  for a real volume fraction.
- **The error propagated into the physics.** ``estimate_csat_lever_rule`` applies the
  lever rule — a *volumetric* thermodynamic identity — to this quantity. Feeding it an
  area fraction yields a **systematically biased** C_sat that does not average out
  across a dilution series. This is now documented at the point of use: the fit remains
  useful as a **relative** measure (the ordering and trend across identically-imaged
  conditions are informative) but must not be reported as an absolute saturation
  concentration on 2-D data alone.
- **A fluorescence intensity was labelled a concentration.** ``bulk_intensity`` was
  documented as *"(= C_sat proxy)"*. It is now ``dilute_phase_intensity`` (with the old
  key aliased), and the docstring states plainly that converting an intensity to a
  concentration requires a calibration curve for that fluorophore on that instrument;
  without it the value is a unitless proxy — monotonic with concentration and useful for
  comparison, but not a concentration. The same caveat is recorded for
  ``partition_coefficient``, which is a ratio of *intensities* and equals the
  thermodynamic partition coefficient only if the intensity-concentration relationship
  is linear and identical in both phases (quenching and environment-sensitive quantum
  yield break this).

### Fixed — the scientific code can now be tested without a GUI
The audit also noted that scientific functions are coupled to napari/PyQt, preventing
headless testing. Verified and partially fixed. Of 48 ``*_tools`` modules, **24 import a
GUI stack at module scope** — and because the coupling is *transitive*, one such import
at the base of the import graph makes everything above it un-importable without a
display. That is backwards: the numerical code is the part that most needs automated
regression testing.

- **New ``pycat.utils.notify`` shim.** Forwards to napari when a UI is present and
  degrades to printing when it is not (a warning a scientist should see must not vanish
  because the code is running in a script). Most of the coupling was two lines importing
  ``show_info``/``show_warning``.
- **``vpt_tools``, ``label_and_mask_tools`` and ``invitro_tools`` now import cleanly with
  no napari and no Qt**, so the viscosity chain, the mask operations, and the in-vitro
  statistics are headlessly testable. GUI imports in ``label_and_mask_tools`` are now
  lazy (resolved at call time, when a viewer demonstrably exists), and its Qt dialog
  degrades to a clear error if opened without Qt rather than blocking the module import.
- The remaining coupled modules are a bounded, mechanical job and are **deliberately left
  for a dedicated pass** rather than swept in at the tail of a release — a broad
  multi-file refactor is precisely what has broken this build before.

## [1.5.377] - 2026-07-10
### Added — upscaling is now *advised* and *resolved*, not guessed
Two changes that together close the upscaling problem properly, rather than warning
about it after the fact.

**1. Segmentation scale advisor — "do I need to upscale, and by how much?"**

Upscaling adds no information; its **only** legitimate purpose is to fix a scale
mismatch between your objects and the **algorithm**. So the answer depends entirely
on which segmentation method comes next — and for most methods the answer is *don't*:

.. list-table::

   * - **Cellpose / StarDist**
     - Have a *learned* scale prior (Cellpose's features were trained on ~30 px
       objects). A small object is not merely small — it is outside the range the
       network's features can read. Upscaling genuinely helps.
   * - **Otsu and other thresholds**
     - Threshold the intensity **histogram** and have no spatial scale at all.
       Upscaling cannot help and measurably **hurts**: interpolation inserts
       intermediate-intensity pixels at every boundary, blurring the bimodality Otsu
       depends on. Measured on synthetic discs with known ground truth, Dice fell
       from **0.876 → 0.759** (2 px objects) and **0.994 → 0.930** (4 px) going from
       1× to 4×.
   * - **Blob / LoG detection**
     - Scale-adaptive by parameter. If objects are small, set a **smaller sigma** —
       do not inflate the image.
   * - **Random forest / watershed**
     - Depend on fixed filter or gradient scales; upscaling shifts objects relative
       to them and usually degrades the result.

The Upscale Images widget now has a method picker and a **"Do I need to upscale?"**
button. It measures your object size (or uses the diameter you already set), and
answers: *not needed*, *upscale N×*, or — when objects are too small for any factor
to rescue — **use a different method**, rather than forcing a CNN to see something it
cannot. It refuses to guess when the object size is unknown.

**2. The measurement source is now resolved from layer lineage, not layer names.**

PyCAT's tag system already records the chain
``mask --belongs_to--> segmentation image --derived_from(via='upscale')--> original``.
The Partial-Volume Measurement tool now **follows it**: selecting a mask
automatically resolves which image its intensities should be measured on and what the
upscale factor was, and says so in plain language. If the mask was segmented on a 4×
upscale, it points the measurement at the **original** image and sets the factor to 4
— with no name-matching heuristics, and no reliance on the user knowing to do it.

When a mask carries no lineage, the tool **says so** rather than guessing.

This is what makes the measurement correct *by construction* instead of correct
*if the user reads a warning*.

## [1.5.376] - 2026-07-10
### Documentation — the new tools and file-handling behaviour are now explained
Recent releases added tools and changed behaviour that were listed in the reference
table but never *explained*. A tool nobody understands is a tool nobody uses.

**New page: Usage ▸ General Tools: When and Why.** The *when and why*, not the *what*:

- **Motion Scale Estimator** — the one most likely to be overlooked, because its
  premise is non-obvious: you can measure how far your objects move between frames
  **without tracking anything**. Explains the problem it solves (every linker demands
  a maximum-displacement parameter that is almost always guessed, and a wrong guess
  produces plausible-looking mislinked trajectories that silently corrupt the physics),
  how the projection trick works, and — most usefully — the **trackability verdict**,
  which can tell you a dataset is untrackable in seconds rather than after a
  three-hour analysis returns a nonsensical viscosity.
- **Partial-Volume Measurement**, **Frame Quality / Focus QC**, **Photobleach
  Correction**, **Detrend Stack**, **Image Registration**, **Colocalization Over
  Time**, and the **Stack / Time-Series Tools**.
- Includes distinctions that are easy to get wrong — e.g. **detrending is not bleach
  correction**: bleach correction rescales intensities so they compare across frames;
  detrending removes a trend so it does not pollute a *variance* measurement. Use the
  former when you care about intensity, the latter when you care about fluctuations.

**New page: Usage ▸ Loading and Saving Data.** Explains the dialogs PyCAT may show and
why:

- **"Is this a time series or a z-stack?"** — a plain multi-page TIFF genuinely does
  not record whether its pages are timepoints or z-slices. The two load identically but
  mean entirely different things to an analysis, so PyCAT asks instead of guessing.
- **"Copy this file to local storage first?"** — what the storage probe does and why.
- **What PyCAT writes when you save** — compression, right-sized bit depth, declared
  stack axes, and why upscaled *images* are flagged as reconstructable while masks
  segmented at high resolution are not.

**Also corrected:** the pipelines list was stale. "General ROI Analysis" is now
**Exploratory Analysis**, "Fibril Analysis" is split into **Cellular** and **In Vitro**
variants, and the Time-Series, Z-Stack, Colocalization-Over-Time, and biophysics
pipelines (VPT, FRAP, Fusion, Force-Distance, Temperature) were missing entirely.

## [1.5.375] - 2026-07-10
### Documentation — scientific assumptions and developer pitfalls, previously undocumented
A scan of past development sessions surfaced several findings that were encoded in
code comments, changelogs, or a roadmap — i.e. nowhere a user or a new contributor
would ever look. Each was re-verified against the current code before writing.

**New page: Usage ▸ Assumptions and Limitations.** Deliberately a page about what
PyCAT *cannot* tell you:

- **2D "volume fraction" is a projection proxy, not a volume.** It is the area
  fraction of a focal plane. Droplets settle, so the value depends on focal depth,
  and larger droplets are more likely to intersect any given plane (a stereological
  bias toward large objects). The caveat existed in the in-vitro workflow's UI and in
  the developer roadmap, but not in the user documentation.
- **Automatic object-size estimation is only valid for 2D fluorescence** — and the
  code already enforced this (``AUTO_OBJECT_SIZE_VALID_WORKFLOWS``) without ever
  telling the user why. It is invalid for brightfield (edge/phase contrast has no
  intensity hierarchy to threshold), for time series (object size drifts as objects
  grow and coarsen, so a single median is wrong by construction), and for z-stacks (a
  projected diameter is not a 3-D size).
- **Intensity-hierarchy thresholding is a fluorescence assumption**, not a universal
  one: in brightfield an object can be darker than background at its centre and
  brighter at its halo.
- **Tracking assumes objects move less than they are far apart** — a property of the
  *acquisition*, not the software. The Motion Scale Estimator answers this **before**
  a tracking run rather than after.
- **Derived quantities inherit every upstream assumption**: a wrong pixel size or
  frame interval produces a wrong viscosity even when every subsequent step is
  perfect, and a mis-linked ensemble yields tight error bars around a wrong number.

**New section: Contributing ▸ Codebase Pitfalls.** Traps that have caused real,
silent bugs and raise no exception:

- **Never call ``np.asarray()`` on a stack layer's data.** The lazy wrappers'
  ``__array__`` is *deliberately* truncated to frame 0 (so napari's incidental array
  requests don't materialise a multi-gigabyte movie), so ``np.asarray(layer.data)``
  silently yields a single 2-D frame. Nothing errors; the analysis simply reports
  frame 0 as if it were the whole movie. This has shipped as a bug **three times**
  (temperature, VPT, colocalization). Use ``materialize_stack`` / ``iter_frames`` /
  ``extract_2d_plane`` — the last taking *the frame the user is viewing*, not frame 0.
- Lazy versus materialised is chosen by **access pattern** (single-pass → stream;
  repeated/random → materialise once).
- Do not measure intensities on upscaled images; metadata capture belongs at the
  load event; never silently pool distinct populations; build incrementally and
  compile after each step.

**Also corrected:** the Toolbox Reference described *Upscale Image* as "increases
image resolution while preserving structural features" — which implies it adds
resolution. It does not, and that framing is arguably the origin of the
measure-on-the-upscale problem.

## [1.5.374] - 2026-07-10
### Documentation — the measurement findings are now documented, not buried
- **New user-facing page: Usage ▸ Measurement Guidance** (``docs/source/usage/
  measurement_guidance.rst``). The findings of the last few releases are
  methodological, not cosmetic — they change how results should be *interpreted* —
  so they belong in the documentation rather than only in a changelog. It covers:
  - **Upscaling**: what it does (satisfies a segmentation model's scale prior) and
    what it does *not* do (add information, resolve anything the optics missed), and
    why intensities must never be measured on it.
  - **The size–intensity bias**: the effect that survives every software
    improvement, with the worked example — three objects of *identical* true
    intensity measuring 72.9 / 91.4 / 94.9 purely because they differ in size — and
    the practical rules (report size distributions, compare size-matched subsets,
    distrust sub-resolution objects).
  - **Saved data**: compression, right-sized bit depth, and which layers are
    reconstructable.
- **The Cell Analysis feature table now carries a warning** at the point of use:
  anyone reading the description of ``intensity_mean`` is told, right there, that
  the value carries a size-dependent optical bias and that a size difference between
  conditions can fabricate an intensity difference.
- **Corrected a misleading entry in the toolbox reference.** "Upscale Image" was
  described as *"increases image resolution while preserving structural features"* —
  which implies it adds resolution. It does not, and that framing is precisely the
  misunderstanding that led to intensities being measured on interpolated pixels.
- The six new general tools and Partial-Volume Measurement are listed in the
  **Toolbox Reference** table.
- **The full investigations are now in the repository** under ``docs/audits/``
  (``upscaling_and_measurement_audit_2026-07-10.md``,
  ``mask_storage_findings_2026-07-10.md``), including the approaches that were
  measured and **rejected** — run-length encoding (worse than plain compression),
  keyframe deltas (~8 % gain), and the initial framing of partial-volume weighting
  as a complete fix (it is not: it does not rescue a comparison between groups of
  differing object size).

## [1.5.373] - 2026-07-10
### Added — size-dependent intensity bias is now QUANTIFIED and WARNED, not chased
- **The important correction to 1.5.372:** the residual size-dependent intensity
  bias is **optical, not computational** — an edge pixel physically integrates a mix
  of object and background photons, so small objects read dim *no matter how the
  mask is handled*. Partial-volume weighting barely dents it. Verified: two groups
  with **identical true intensity** but different sizes (r=3 vs r=8 px) produce an
  apparent **+12% intensity difference with p ≈ 1e-83** — and PV weighting still
  produced +11.7%. Chasing a better measurement does not fix this.
- **A shared bias level cancels in a comparison. The bias GRADIENT does not.** That
  is the failure mode that survives every measurement improvement, and it is
  precisely the one that matters for comparative biology: a treatment that changes
  only condensate *size* fabricates an apparent *intensity* change.
- **So the bias is now measured, predicted, and reported rather than chased:**
  - ``intensity_bias_for_size(radius, psf)`` predicts the dilution for an object of
    a given size under the user's own optics (bias ≈ −tanh(0.75·σ_PSF/R), fitted to
    numerically imaged discs; max error ~5% of contrast over r=2–20 px,
    σ_PSF=0.5–2 px, and saturating rather than extrapolating in the sub-resolution
    corner).
  - ``estimate_psf_sigma(image)`` measures the PSF width from the data itself, so
    the prediction is specific to the user's imaging conditions.
  - ``size_confound_warning(radii_a, radii_b, psf)`` answers the question that
    protects the science: *can a size difference between these groups fabricate an
    intensity difference?* It correctly flags r=3 vs r=8 as **SEVERE** and stays
    quiet for r=6.0 vs r=6.5.
  - ``is_sub_resolution(radius, psf)`` flags objects at or below the resolution
    limit, whose absolute intensity is not trustworthy by **any** method.
- **Every Partial-Volume Measurement now reports the bias per object**
  (``radius_eq_px``, ``predicted_bias_pct``, ``sub_resolution``) and shows a
  field-level advisory: the bias at the smallest / median / largest object, a
  sub-resolution count, and an explicit **size-confound warning** when the spread of
  object sizes is large enough that an intensity-vs-size trend cannot be
  distinguished from the artefact.

### Why this matters more than a better estimator
Three objects with **identical true intensity (100)** measure as **72.9 / 91.4 /
94.9** purely because they differ in size. A user plotting intensity against size
sees a convincing correlation that does not exist. The ``predicted_bias_pct`` column
(−52% / −19% / −11%) is what tells them so. No refinement of the measurement removes
that trend — only knowing its size does.

## [1.5.372] - 2026-07-10
### Added — Partial-volume measurement: measure on the ORIGINAL pixels, not the upscale
- **PyCAT's standard workflow measured intensities on upscaled images. That is not
  scientifically defensible, and the UI defaulted to it** (the "Select Image for
  Cell Analysis" dropdown pre-selected *Upscaled Fluorescence*). Verified
  numerically:
  - **Upscaling adds no information.** In tests it *never* split two objects that
    native-resolution segmentation merged, at any separation — the PSF, not the
    pixel grid, sets the resolution limit. Its only legitimate use is to satisfy a
    segmentation model's learned object-scale prior (a property of the *algorithm*,
    not of the data).
  - **Reading intensities off interpolated pixels pseudoreplicates.** 16× the
    "samples", zero new photons: the reported SEM came out ~1.5× smaller than the
    true standard error across noise realisations. Every error bar and p-value was
    falsely confident.
  - **It biases small objects low, size-dependently** (−14% for a 9-px object, −2%
    for a 517-px one), which can manufacture a spurious intensity-vs-size trend.
- **New ``partial_volume_tools`` module** implements the defensible path: the
  high-resolution mask is converted to **fractional-coverage weights on the native
  grid**, and all statistics are computed on the **original detector pixels**, with
  a Kish **effective sample size** so the error bars stay honest. Validated against
  ground truth: the reported SEM is now calibrated (ratio 1.12–1.19 to the true
  standard error across object sizes and noise levels, i.e. slightly conservative)
  where the old path was 1.5× overconfident.
- **Why not simply downscale the mask:** measured, that is *worse* than the status
  quo for small objects (bias −16.4 vs −14.1 at R=2.5 px). A native edge pixel at
  intensity 60 between background 20 and object 100 genuinely encodes "≈50%
  covered"; **binarising destroys that**. Partial-volume weighting keeps it — it
  recovered true sub-pixel coverage better than a binary native mask in 31 of 36
  conditions spanning object size, PSF width, noise, and threshold offset.
- **New tool:** Toolbox ▸ Cell and Object Analyses ▸ *Partial-Volume Measurement*.
  Takes the high-res mask + the ORIGINAL image + the upscale factor, and reports
  per-object weighted mean/integrated intensity, fractional area, and an SEM built
  from an estimated noise σ (not from the intensity spread, which also contains the
  object's real internal structure — conflating them inflated the SEM ~2.8×).
- **The Cell Analyzer now warns** when the selected intensity image is an upscaled
  layer, explaining why that biases the result and pointing at the correct tool.

### Honest limits
- Small objects are biased low **regardless of method** — even a native mask on
  native data reads low, because the *detector* integrates a mix of object and
  background photons across an edge pixel. Partial-volume weighting minimises the
  *software-added* bias; it cannot undo the optics. Unbiased absolute intensities on
  ~2-px objects is a deconvolution/PSF-modelling problem, not a masking problem.

## [1.5.371] - 2026-07-10
### Fixed — PyCAT was saving masks and stacks completely UNCOMPRESSED (~100× larger)
- **Masks, label stacks, and image stacks are now written compressed.** Every save
  path omitted a ``compression=`` argument, and the multi-page writer passed
  ``contiguous=True``, which *forces* uncompressed output — so a 1024² uint16 label
  mask was written as a 2.1 MB file that compresses losslessly to 13 kB. Measured on
  realistic masks: **~100–160× smaller**, lossless, for about 7 ms per mask. Image
  data compresses far less (it carries real noise), but masks are the bulk of a
  project's disk usage and they are now the size they should be.
- **The saved stacks also declared no axis.** Written the old way, PyCAT's own
  multi-page files came back with an undeclared ``Q`` axis — the exact case that
  makes PyCAT prompt *"is this a time-series or a z-stack?"* when reopening its own
  output (the 1.5.351 case). Stacks now declare ``TYX``/``ZYX`` from the data
  repository's axis label, so they reopen cleanly with no prompt.
- Stack writes still **stream frame-by-frame** (via a generator passed to
  ``imwrite`` with ``shape=``/``dtype=``), so a large movie is never materialised in
  RAM just to be saved — the previous per-frame streaming behaviour is preserved.
- The ``.npy`` fallback is now ``.npz`` (compressed), and the float32 TIFF exports in
  the image-operations and temperature-batch paths are compressed too.

### Notes — measured, so we *didn't* build the elaborate storage architecture
- Benchmarked the proposed schemes against plain compression on realistic masks
  before building any of them. **Run-length encoding is *worse* than plain zlib**
  (28× vs 39×) — the generic compressor already finds the runs and RLE's explicit
  triples add overhead. **Keyframes + XOR deltas buy ~8%**, not worth the
  reconstruction complexity and corruption surface. Per-object bounding-box storage
  is a genuine 1.5–1.8× over zlib but *plain lzma beats it*; its real value would be
  viewport-limited loading, not size. Turning compression on captures essentially the
  entire win at zero complexity and zero reproducibility risk.

## [1.5.370] - 2026-07-10
### Added — Motion Scale Estimator: measure displacement without linking anything
- **The VPT time-projection trick is now a general Toolbox tool** (Toolbox ▸ Data
  Visualization ▸ *Motion Scale Estimator*). A short-window MAX-projection smears
  each object into a blob whose width is its single-frame width broadened by how
  far it MOVED; subtracting the single-frame width in quadrature recovers the
  motion scale — **with no tracking pass at all**:

      motion = sqrt(sigma_projected^2 - sigma_single_frame^2)

  This answers the question every linker asks and every user is otherwise forced
  to *guess*: "how far do my objects move between frames?" It was previously
  locked inside VPT (``estimate_linking_distance_um``) even though it applies to
  any dynamic localisation problem — puncta, vesicles, condensates, beads.
- It reports the suggested max linking distance **plus the quantities behind it**
  (per-frame motion, the measured window smear, the single-frame object size, the
  object count), and gives a **trackability verdict**: when per-frame motion
  approaches or exceeds the object size, frame-to-frame linking is unreliable and
  the acquisition is too slow — a QC answer that's otherwise only discovered after
  a tracking run produces nonsense. The projection is added as a layer so the
  smear the estimate came from is visible.
- Honest about its limits: fitting a Gaussian to the projected envelope
  under-estimates the true spread (~25% low on a synthetic random walk with known
  step), which is what the margin factor *k* absorbs. It is stated in the widget as
  a well-grounded starting value, not a precise displacement measurement.

## [1.5.369] - 2026-07-10
### Added — general techniques promoted out of single-method pipelines
- Re-audited the codebase for **reusable techniques that were implemented inside
  one analysis method** and had no standalone access — the class of gap the
  ``_add_*``-based audit couldn't see, because these are plain functions with no
  widget of their own. Four are now standalone Toolbox tools (and appear in the
  Exploratory workbench). None are reimplemented — the new widgets call the
  existing, tested functions, and the original pipelines are untouched:
  - **Image Registration (subpixel)** → Toolbox ▸ Image Processing. Guizar-Sicairos
    phase-cross-correlation alignment lived in ``fibril_tools`` and was reachable
    only from the Fibril widget, despite having nothing to do with fibrils — it's
    the general tool for channel alignment, drift correction, and before/after
    comparison. Adds the registered image plus a difference image and reports the
    subpixel shift.
  - **Photobleach Correction** → Toolbox ▸ Image Processing. Fitting an exponential
    to the mean trace and dividing it out was locked in the condensate-physics
    widget, though bleaching affects every fluorescence time-series. Plots the
    measured trace, the fitted decay, and the corrected result, and reports the
    bleach time constant (it declines to "correct" when the fit doesn't converge,
    rather than silently applying a meaningless factor).
  - **Detrend Stack (drift / bleaching)** → Toolbox ▸ Image Processing. Removing the
    slow temporal trend was locked in N&B, but it's a prerequisite for *any*
    fluctuation measurement — an undetrended decay inflates the temporal variance.
  - **Frame Quality / Focus QC** → Toolbox ▸ Data Visualization. Per-frame Brenner
    focus scoring, entropy, out-of-focus flagging, and sharpest-frame selection
    existed across ``temperature_tools`` and ``condensate_physics_tools`` but were
    only reachable from those workflows; "which frames of this stack are usable?"
    is a question every time-series and z-stack analysis needs to answer.

## [1.5.368] - 2026-07-10
### Fixed — "PyCAT" wordmark now shows next to the logo mark
- **The menu bar showed the logo roundel but dropped the "PyCAT" text.** A plain
  QAction carrying both an icon and a label renders icon-only on a QMenuBar (Qt
  discards the text). The marker is now a QWidgetAction wrapping a real label, so
  the mark and the "PyCAT ▸" wordmark both render, in that order.

### Changed — toolbox coverage audit (stage 3 of 3): pipeline-locked tools surfaced
- Audited every tool builder against the Toolbox menu. Most tools were already
  correctly placed; the audit found a consistent gap — **general-purpose tools that
  existed only inside a pipeline**, chiefly the stack/time-series variants of tools
  whose 2-D versions were already in the Toolbox. These are now reachable from the
  Toolbox (and the Exploratory workbench) in coherent locations:
  - **Two-Channel Condensate Colocalization** → Toolbox ▸ Colocalization/Correlation
    ▸ Object-Based Colocalization (it was only in the Colocalization pipeline, even
    though its siblings OBCA and Manders were standalone tools).
  - **Export Time-Series Video** → Toolbox ▸ Data Visualization (works on any stack,
    was locked in the Time-Series Condensate pipeline).
  - **Upscale Stack**, **Pre-Process Stack (lazy)**, and **Cellpose Segmentation
    (stack)** → new Toolbox ▸ Image Processing ▸ **Stack / Time-Series Tools**
    sub-menu (all were locked in the Time-Series Condensate pipeline despite being
    general stack operations whose 2-D counterparts are already in the Toolbox).
- Pipeline-internal *steps* (VPT bead detection / tracking / microrheology, FRAP
  and Fusion step builders, per-pipeline load/export, etc.) were deliberately left
  where they are — they're workflow steps, not standalone tools.

## [1.5.367] - 2026-07-10
### Changed — PyCAT logo mark replaces the ◆ diamond
- **The menu-bar section marker is now the actual PyCAT logo mark** (the reduced
  snake/helix roundel, no wordmark) instead of a generic ◆ diamond, so the divider
  between napari's menus and PyCAT's is properly branded. The mark ships as
  ``src/pycat/icons/pycat_mark.png`` with a transparent background (the source
  artwork was on white, which would have rendered as a white box on the dark menu
  bar), and it falls back to the old diamond if the icon can't be loaded.
- **The window / taskbar icon now uses the mark too** — at those sizes the full
  logo's wordmark is illegible, while the roundel stays crisp. The welcome-screen
  graphic still uses the full logo, where the wordmark belongs.

## [1.5.366] - 2026-07-10
### Changed — branding + toolbar declutter (menus stay on top, actions move to the PyCAT bar)
- **The top menu bar is now mostly menus.** The action buttons that used to sit on
  it moved into the PyCAT bar (the gray "Batch: / Layer Actions: / Information:"
  toolbar), grouped sensibly:
  - **Recorded Steps** → Batch section, next to Batch Run / Record / Save Config,
    with a distinct clipboard icon (📋) instead of the ☰ hamburger that looked like
    a napari menu.
  - **Clear + Home** → the Layer Actions section.
  - **Metadata + Tags** → a new Information section.
  - (The Search command palette stays on the top bar.)
- **The PyCAT bar's "Layers:" section is renamed "Layer Actions:"** (the old label
  next to the "👁 Layers" button read confusingly as "Layers … Layers").
- **The show/hide-all button now cycles like the colormap button**: its label shows
  the action the next click performs — "👁 Show" → click shows all → "🚫 Hide" →
  click hides all → back to "👁 Show".
- **Window title is now just "PyCAT"** (was "PyCAT-Napari").
- **The "PyCAT" menu-bar marker is brighter** — cornflower blue (#6495ED) instead
  of the darker blue, so it stands out against the dark bar.

## [1.5.365] - 2026-07-10
### Fixed — Exploratory dock crashed on open (Client Enrichment import bug)
- **Fixed a crash that prevented the Exploratory Analysis dock from opening.** The
  Client Partition / Enrichment tool referenced ``QSizePolicy`` without importing
  it — a latent bug that only surfaced now that the Exploratory workbench builds
  every tool at open. Added the missing import.
- **Hardened the Exploratory dock against any single tool failing to build.** Each
  tool is now added through a guarded wrapper, so if one tool errors during
  construction it logs the traceback and shows a small "unavailable" note in its
  section instead of taking down the entire dock — the rest of the workbench still
  loads.

## [1.5.364] - 2026-07-10
### Changed — Exploratory Analysis rebuilt as a full-toolbox workbench (stage 2 of 3)
- **Exploratory Analysis now exposes the whole toolbox**, grouped into collapsible
  sections that mirror the Toolbox menu: Setup & Measure, Image Processing,
  Segmentation, Labels & Masks, Layer Operations, Cell & Object Analyzers,
  Colocalization / Correlation, Spatial Metrology, Advanced Analysis, Structure
  Estimators, Diagnostics & QC, and Save & Clear. Previously it offered only a
  fixed handful of tools in a flat list.
- **Sections start collapsed** so the panel isn't overwhelming, except a few common
  starting points that start expanded (Setup & Measure, Segmentation, Save &
  Clear). A new lightweight ``CollapsibleSection`` widget provides the expandable
  headers. Whole dedicated pipelines (the cellular/in-vitro/time-series/z-stack
  object analyses and the biophysics single-tether methods) are intentionally not
  duplicated here — this dock is for freely mixing individual tools.

## [1.5.363] - 2026-07-10
### Fixed — Cellpose prewarm re-ran on every launch (misleading "one-time" message)
- **The Cellpose prewarm now correctly detects an already-cached model.** The old
  check looked for a file named exactly ``~/.cellpose/models/cyto2``, but Cellpose
  saves weights under suffixed names (e.g. ``cyto2torch_0``, ``cyto2_cp3``,
  ``cpsam``), so the check always missed — the prewarm subprocess ran on every
  launch and printed the "downloading it once now… ONE-TIME setup" message even
  when the model was already cached (Cellpose itself didn't actually re-download,
  but the spurious subprocess + message were confusing). The prewarm now scans the
  known cache locations (including a ``CELLPOSE_LOCAL_MODELS_PATH`` override) for
  any weight file whose name starts with the model name, so a cached model is
  recognised and skipped with an accurate message.

## [1.5.362] - 2026-07-10
### Changed — analysis-method IA: Exploratory rename + Fibril split/move (stage 1 of 3)
- **"General Analysis" is now "Exploratory Analysis"** (menu entry + dock title).
  (The full rebuild that gives it all toolbox tools in collapsible sections is
  staged as a following release.)
- **Fibril Analysis is split into "Cellular Fibril Analysis" and "In Vitro Fibril
  Analysis"**, matching the cellular/in-vitro split used by the object-analysis
  pipelines, and **moved under Analysis Methods → Cell and Object Analyses** (it
  was a loose top-level entry). The cellular variant adds cell segmentation so
  fibrils get per-cell context; the in-vitro variant analyses the whole field.
  The standalone "Fibril Analysis" tool under Toolbox → Spatial Metrology is
  unchanged. (This is stage 1 of a 3-part information-architecture pass; the
  Exploratory rebuild and a toolbox-coverage audit follow.)

## [1.5.361] - 2026-07-10
### Added — per-cell colocalization over time (+ threaded run)
- **Condensate coloc over time is now per-cell**, following each cell's
  colocalization through the movie rather than only a field average. It uses the
  cell segmentation's own labels as identity — exactly as the time-series
  condensate pipeline does: a labeled mask carries stable cell IDs, and a labeled
  (T, H, W) mask stack tracks moving cells (consistent labels = same cell), so no
  separate tracking step is needed. ``condensate_coloc_time_trace`` gained a
  ``per_cell`` flag (default on) that keeps one row per (frame, cell_label); the
  new ``plot_per_cell_coloc_time_trace`` draws one trajectory per cell, with
  click-a-point-to-jump-to-that-frame brushing. The per-frame-average mode is
  still available (``per_cell=False``).
- **The condensate coloc-over-time run is now threaded** (``CondensateColocTimeWorker``),
  so a long movie no longer blocks the UI — the progress bar updates per frame and
  the run is cancellable, instead of the previous synchronous ``processEvents`` loop.

## [1.5.360] - 2026-07-10
### Added — condensate coloc over time, time-series menu entry, trace brushing
- **Object-based condensate colocalization can now run over time.** The
  Two-Channel Colocalization widget gains a "Coloc Over Time (all frames)" button
  that runs the per-cell condensate coloc on every frame and plots the per-frame
  mean metrics vs time (backend ``condensate_coloc_time_trace``). Cells aren't
  tracked across frames, so the trace is the per-frame average across cells with
  an ``n_cells`` column; per-cell-over-time would need cell tracking (a separate
  build).
- **Colocalization Over Time is now a first-class menu entry** under Analysis
  Methods → Colocalization Analysis, so the time-series colocalization workflow is
  discoverable there and not only via the widget button.
- **Coloc time-trace plots are now clickable** — clicking a point on either trace
  (pixel-wise or condensate) jumps the napari viewer to that frame, with a marker
  showing the selected frame. This is the same plot→viewer brushing idea used for
  VPT, applied to the coloc time-series.

## [1.5.359] - 2026-07-10
### Added — colocalization over time (per-frame coloc time trace)
- **Colocalization can now be tracked frame-by-frame across a stack**, so you can
  see how it evolves during fusion, maturation, or recruitment. A new reusable
  backend ``coloc_time_trace()`` streams a time-series (or z-stack) one frame at a
  time and applies the scalar coloc metrics (Pearson, Spearman, Kendall, weighted
  τ, Li's ICQ, Manders overlap/k1/k2) per frame, returning a tidy per-frame table
  with a time axis; ``plot_coloc_time_trace()`` plots the coefficient(s) vs time.
  It uses the same metric functions as the single-frame analysis, so the numbers
  match frame-for-frame.
- **UI:** the Pixel-Wise Correlation widget gains a **"Coloc over time (all
  frames)"** button that runs a default trend set (Pearson + Spearman + Manders
  overlap) over the selected stacks, stores the trace, plots it, and shows the
  per-frame table. (The single-frame "Calculate PWCCA" is unchanged.) This is the
  foundation for the linked time-series ↔ colocalization workflows; it deliberately
  lives in the coloc backend so a time-series method can call it too.

## [1.5.358] - 2026-07-10
### Fixed — colocalization no longer silently analyses frame 0 of a lazy stack
- **The lazy-stack "frame 0" trap is now guarded in all colocalization paths.**
  Reading a lazy time-series/z-stack layer with ``np.asarray(layer.data)`` returns
  only the first frame (the ``_TiffPageStack`` wrapper truncates ``__array__`` on
  purpose), so colocalization pointed at a stacked channel silently measured
  frame 0 regardless of the frame being viewed. All four coloc entry points
  (two-channel condensate, pixel-wise correlation, Manders, object-based) now
  detect a stack input and extract a real 2-D plane — the **current viewer frame**
  where available (two-channel, pixel-wise), or the first frame with a warning
  where the viewer frame isn't reachable (Manders, object-based) — instead of
  silently grabbing frame 0. A follow-on will add sequential per-frame
  colocalization so the time-evolution of coloc can be tracked frame by frame.
- **New reusable helpers** ``layer_is_stack()`` and ``extract_2d_plane()`` in
  ``file_io`` let any 2-D analysis safely take one plane from a possibly-lazy
  layer without hitting the trap.
- Audited the sibling **time-series condensate** tool for the same pattern and
  confirmed it's clean (it already reads frames one at a time), and deliberately
  left the ~35 other ``np.asarray(...data)`` sites untouched, since they operate
  on genuine 2-D images/masks where the call is correct.

## [1.5.357] - 2026-07-10
### Added — assumed-axis warning wired into axis-dependent analyses; small fixes
- **The assumed-axis warning (1.5.351) now actually fires** where it matters. When
  a stack's axis type was assumed at load (an undeclared multipage TIFF the user
  labelled T or Z), the analyses that depend on the axis type now warn once:
  VPT, FRAP, and image-mode Droplet Fusion (which treat frames as **time**) and
  z-stack 3-D metrics (which treats the axis as **z**). ``warn_if_assumed_axis``
  is now a module-level function any analysis can call with its data repository.
- **Housekeeping:** the slow-storage local cache (1.5.356) now opportunistically
  removes cached copies older than ~24h so it doesn't grow unbounded; the README
  version string was updated (it was stale at 1.5.0).

## [1.5.356] - 2026-07-10
### Added — copy slow-storage files to local cache with a progress bar
- **When a file is on slow storage** (network share, removable drive, cloud
  online-only placeholder), PyCAT now offers to **copy it to fast local temp
  storage first**, then load from the copy — instead of only warning. The copy
  runs in 8 MB chunks behind a **progress dialog with a Cancel button** (the copy
  is the slow I/O, so this doubles as the slow-load progress indicator). An
  "always do this for slow files this session" option avoids re-prompting, a
  cancelled copy cleans up its partial file, and a file already cached locally
  (same size) is reused instead of re-copied. Fast storage is unaffected and
  silent, as before.

## [1.5.355] - 2026-07-10
### Improved — Force-Distance: smart default force channel
- **The FD loader now auto-selects the force channel that actually shows the
  force-distance signal**, instead of defaulting to the first channel
  alphabetically. On a dual-trap C-Trap several force channels exist (Trap 1/Trap
  2, Force 1x/2x, …) but only the tether-bearing one's force rises with distance;
  picking the wrong one gave a flat curve that didn't "go up". Each candidate is
  now scored by how strongly its force tracks distance (|correlation| × dynamic
  range) and the best is chosen. The load message notes the auto-selection and
  that you can still switch channels and press "Use selected channels".

## [1.5.354] - 2026-07-10
### Added — Droplet Fusion: interactive fit-range selection on the signal plot
- **Drag to select the fit window.** The fusion signal plot (Step 2) now has a
  span selector: drag horizontally across the fusion event to set the fit window,
  which syncs live to the Step-3 Fit start/end fields. A **"Fit this range"**
  button on the plot fits the selected span directly, so you can go from signal →
  window → fit without leaving the plot. Typing the values in Step 3 still works
  (the plot and fields stay in sync).

## [1.5.353] - 2026-07-10
### Improved — Droplet Fusion: visible signal, sampling period entry, labelled fit
- **The fusion signal is now plotted** as soon as it's built (Step 2), so the
  analysis isn't a black box — you see the force/aspect-ratio profile you're about
  to fit and can read off a sensible fit window.
- **Sampling can be entered as a period in microseconds** (e.g. 12.8 µs), not just
  as a rate in Hz. The old Hz field only allowed 1 decimal, which couldn't
  represent the C-Trap force sampling precision; a new "sample period (µs)" field
  (4 decimals) is kept in sync with the Hz field (12.8 µs ↔ 78125 Hz).
- **The fit is now shown with the model equation and labelled parameters** (like
  the FRAP module). Step 3 displays S(t) = a·e^(−t/τ) + b·t + d with each term
  explained, and after fitting a plot overlays the fitted curve on the signal
  (fit window shaded) with τ, a, b, d, and R² labelled — a visual check of fit
  quality instead of a cryptic numbers table.

## [1.5.352] - 2026-07-10
### Added — VPT linked brushing now works in the consolidated plot panel too
- **The consolidated 2×2 plot panel's MSD curves are now clickable and
  registered**, so the full linked-selection web works whether plots are shown
  consolidated or in separate windows. Clicking a curve in the 2×2 panel selects
  that track everywhere (bead in the image + row in the per-track table), and
  selecting a track from the table or image highlights its curve in the panel.
  Previously this brushing only worked in separate-windows mode; the consolidated
  MSD panel didn't expose per-track lines. This completes the plot↔image↔table
  linked navigation in every layout.

## [1.5.351] - 2026-07-10
### Fixed — unlabelled multipage TIFFs now load as stacks (broad loader fix)
- **A multipage TIFF with no axis metadata now loads as a stack, not as
  individual images.** Previously the context-aware opener decided stack-vs-2D
  purely from declared T/Z/P metadata, so a plain "save as TIFF" (e.g. a FRAP
  recovery exported from a Lumicks .h5, or split/stacked exports from Andor,
  Zeiss, Leica) — whose stack axis tifffile labels as unknown ('Q') — was
  mis-routed to the 2D loader and opened as separate planes. Now such files are
  detected and the user is **asked** whether it's a **time-series (T)**, a
  **z-stack (Z)**, or **genuinely separate 2D images**, with a *remember my
  choice this session* option. T and Z load identically (both 3-D); the label is
  recorded so axis-dependent steps can warn if the axis was assumed
  (``warn_if_assumed_axis``). Properly-tagged TIFFs (ImageJ/OME), declared
  z-stacks, single 2-D images, and .czi/.ims are unaffected.
- **FRAP safety net.** If a recovery layer is 2-D but several same-sized 2-D
  image layers are open (the "loaded as individual images" case), the FRAP
  analysis now offers to stack them into a (T, H, W) recovery series instead of
  just refusing. This, plus the loader fix above, resolves the reported
  ``τ½ = nans`` (which was downstream of the recovery loading as 2-D).
- **Clearer Lumicks/pylake guidance.** The FRAP Step-1 panel now shows a hint
  when ``lumicks.pylake`` isn't installed (with the one-line install command),
  and the on-click message explains it's an optional package for C-Trap .h5.

## [1.5.350] - 2026-07-10
### Added — VPT pickable bead layer (image→plot/table brushing) completes the link
- **A new "Bead Picker" Points layer** carries one point per bead per frame, each
  tagged with its track_id. Clicking a bead in the image now selects that track
  everywhere through the linked-selection dispatcher — it highlights that track's
  MSD curve (separate-windows plot) and its row in the per-track table. This is the
  direction napari's Tracks layer couldn't provide (Tracks has no per-track pick
  API), so it's done with a pickable Points layer that resolves a clicked point to
  its track_id. The points overlay the beads by matching the image layer's scale,
  and are faint/hollow so they don't obscure the data.
- **The linked-brushing web is now bidirectional across all three views:**
  plot↔image, plot↔table, and image↔table all highlight the same track. The one
  remaining follow-on is table↔plot-curve highlighting in the *consolidated* 2×2
  panel (works today in separate-windows mode); the 2×2 panel doesn't yet expose
  clickable per-track lines.

## [1.5.349] - 2026-07-10
### Added — VPT per-track results table with linked selection
- **A new non-modal per-track results table** appears after "Compute MSD &
  Viscosity": one row per trajectory with track_id, frame count, duration, and a
  per-track D and α (from a power-law fit of that track's own MSD). Clicking a row
  selects that track everywhere through the linked-selection dispatcher — it
  reveals the bead in the image and (when the MSD plot is open in separate-windows
  mode) emphasises that track's MSD curve. Conversely, selecting a track from the
  plot highlights its row here.
- The table stays open alongside the plots (non-modal) and cleans up its dispatcher
  registration when closed. Table→image linking works in both plot layouts;
  table↔plot-curve highlighting currently works when the MSD plot is shown in
  separate-windows mode (the consolidated 2×2 panel doesn't yet expose clickable
  per-track lines — a planned follow-on). The full picture is now: plot→image
  (1.5.336/348), table→image and table→plot and plot→table (this release), with
  image→plot (clicking a bead) still to come via a pickable identity layer.

## [1.5.348] - 2026-07-10
### Added — VPT linked-selection dispatcher (foundation for plot↔image↔table brushing)
- **A central selection hub** now coordinates highlighting a track across views:
  clicking a track's MSD curve selects that track everywhere it can. One
  ``_select_track(track_id, source)`` owns the current selection and propagates it
  to the OTHER views, with a re-entrancy guard so a highlight it triggers can't
  fire that view's own selection and loop (verified in isolation). The shared key
  is ``track_id``, which already threads through the Tracks layer, the per-track
  MSD curves, and the results.
- The existing plot→image brushing (click an MSD curve → reveal the bead in the
  viewer, shipped 1.5.336) now routes through this hub, and the MSD plot exposes
  its ``track_id → curve`` line map so a selection driven from elsewhere can
  emphasise the matching curve. All highlight paths are safe no-ops when their
  view isn't open, so nothing errors if a plot or table is closed.
- Foundation only: full three-way linking needs two further additive pieces — a
  PER-TRACK results table (the current table is an ensemble summary) and a
  pickable Points layer carrying track identity (napari Tracks layers have no
  per-track click API). Those are the next increments.

## [1.5.347] - 2026-07-10
### Added — VPT: consolidated plot panel + trajectory-spread & van Hove plots
- **Two new microrheology plots.** A **centered-trajectories** plot overlays every
  track shifted to start at (0,0) at t=0, showing the spatial spread of the
  ensemble; and a **van Hove displacement distribution** histograms single-axis
  displacements at a fixed lag against the Gaussian of matching variance, with the
  non-Gaussian parameter α₂ reported — the direct visual test of whether the
  motion is Brownian (Gaussian ⇒ Brownian; heavy tails / α₂≫0 ⇒ heterogeneous).
- **Consolidated 2×2 plot panel (default).** After "Compute MSD & Viscosity", the
  MSD spaghetti, Evans G′/G″ moduli, centered trajectories, and van Hove
  distribution now appear together in ONE window instead of separate pop-ups. A
  **"Separate windows" button on the figure** re-renders them as individual
  resizable windows live, and a **"Show all plots in one window" checkbox** in the
  VPT panel sets the default layout. In separate-window mode the MSD plot keeps
  click-a-track-to-reveal-it-in-the-viewer brushing.
- The G′/G″ panel is explicitly labelled as the Evans (2009) method — the same
  algorithm PyCAT already used, verified in-sandbox to reproduce the reference
  implementation to machine precision on a known viscous fluid.

## [1.5.346] - 2026-07-10
### Added — user-created layers are auto-tagged (completes tag coverage)
- **Layers created through napari's own menus** (the native "new points / shapes /
  labels layer" buttons) now get a light default tag on insertion, so they are no
  longer invisible to the tag system. A new ``layers.events.inserted`` hook stamps
  a role from the layer TYPE — Shapes/Points → ``annotation``, Labels → ``mask``,
  Image → ``image`` — plus ``provenance=user-created``, at LOW confidence (0.4) so
  a user's refinement in the Tag Inspector (``user_set``) always locks over it.
- The hook is careful: it skips layers that already carry tags (PyCAT-created or
  restored from a saved file) so it never stomps richer tags, and skips
  reader-loaded foreign file layers (handled and re-tagged by the existing
  load-reroute backstop). Combined with load-time tagging (1.5.337) and derivation
  lineage, every layer in the viewer now carries at least a role tag.

## [1.5.345] - 2026-07-10
### Added — confirmation before clearing diameter measurements
- **"Clear Lines" now asks for confirmation (OK / Cancel)** before deleting the
  drawn diameter line(s) and resetting the measured values — but only when there
  is actually something to lose (real lines drawn or measured values present);
  clearing an already-empty set does not prompt. Cancelling leaves everything
  intact and the button stays on "Clear Lines".

## [1.5.344] - 2026-07-10
### Changed — diameter measurement is now a single self-explaining cycling button
- **The measure-diameters control is one button that cycles Draw → Measure →
  Clear**, with the label always reflecting the actual state (so it's obvious what
  it does, instead of "Measure Line(s)" secretly also creating the layers):
  - **Draw Lines** (when no diameter layers exist) — creates the seeded, tagged
    'Object Diameter' / 'Cell Diameter' layers and arms line drawing.
  - **Measure Lines** — runs the diameter measurement; if lines were actually
    drawn, advances to Clear (and the status circle turns green).
  - **Clear Lines** — deletes the drawn lines, resets the measured values (unless
    "Remember measurements across clears" is on), re-seeds the layers for a finite
    extent, resets the status circle, and re-arms drawing for a smooth
    draw→measure→clear→draw loop. The layers are NOT removed, so they persist if
    you switch to a method that doesn't use them.
- **State-driven label.** The button reads the real layer/line/measurement state
  on show and after every click, so it stays honest even if you drew directly in
  napari, switched methods, or deleted a layer.

## [1.5.343] - 2026-07-10
### Changed — diameter measurement layers are created on demand, not at load
- **The 'Object Diameter' / 'Cell Diameter' annotation layers are no longer
  created at every file load.** They were being added eagerly on load even though
  most sessions never measure diameters, cluttering the layer list. They are now
  created ON DEMAND by the measure widget (the "Measure Line(s)" button) the first
  time you measure, via the shared tagged drawing-layer factory added in 1.5.342 —
  so they arrive seeded (finite extent) and tagged (role=annotation,
  purpose=cell_diameter/object_diameter).
- **Home-button safety preserved.** The empty-Shapes-layer NaN-extent crash that
  the eager seeding guarded against only occurs when an empty Shapes layer is
  present; with on-demand creation, no diameter layer exists until the user makes
  one (and the factory seeds it), so the interim is safe. A module flag
  ``EAGER_DIAMETER_LAYERS`` (default False) restores the old eager behaviour as a
  one-line revert if ever needed.
- ``calculate_length`` and the measure widget already tolerated the layers being
  absent (they fall back to defaults), so no downstream measurement behaviour
  changes — only when the layers appear.

## [1.5.342] - 2026-07-10
### Added — tagged drawing-layer factory + a 'purpose' tag (foundation)
- **New shared primitive ``pycat.toolbox.drawing_layers.add_drawing_layer()``**
  creates annotation/drawing layers (measurement lines, ROIs, point markers) that
  are used for a PURPOSE in a method — the intended replacement for both the eager
  load-time diameter layers and the ad-hoc per-method Shapes creation. Each layer
  it makes is: seeded (for Shapes) so an empty layer reports a finite extent
  (guards the NaN-extent / Home-button crash), put into the right draw mode and
  selected so the user can draw immediately, and TAGGED with role + purpose via
  the layer-tag engine so the drawing layer is visible to the Tag Inspector and to
  future tag-driven autopopulation.
- **New ``purpose`` tag key** describing what an annotation layer is FOR
  (cell_diameter, object_diameter, roi_background, roi_measure, line_profile, …).
  It is an OPEN vocabulary: the common purposes are suggested (for consistency and
  UI discovery via ``SUGGESTED_VALUES``) but any user-defined value is accepted, so
  a user can coin their own purpose for exploration — unlike the strict core keys
  (role/modality/…), which still reject unknown values.
- This is the foundation only. Wiring per-method "Add Measure Line / ROI" buttons
  to this primitive (replacing the load-time diameter layers), and auto-tagging
  layers created through napari's own menus, are the next staged steps.

## [1.5.341] - 2026-07-10
### Changed — drag-and-drop: no more image/mask prompt, clear-or-add instead
- **Dropped files no longer prompt "image or mask?".** A dropped file loads as an
  image unless it carries a PyCAT signifier marking it a mask (then it loads as a
  Labels layer). PyCAT isn't intended to ingest foreign masks, so an unsignified
  file is simply treated as an image — no dialog, no pixel-statistics guessing.
  This removes the friction of the prompt appearing for e.g. dropped IMS files
  (always real acquisition images) and also removes the classification path that
  had been a source of crashes. (Accepting foreign masks is a possible future
  feature.)
- **New clear-or-add prompt.** If an image is already loaded when you drop a
  file, PyCAT now asks ONCE whether to clear the current session and load the
  dropped file(s), or add them to what's open — the choice applies to the whole
  dropped batch. If nothing is loaded yet, the file(s) load with no prompt. The
  prompt defaults to "Add" if it can't be shown, so current work is never
  discarded silently.
- Slow-storage detection still runs on dropped images; the menu "Add Image /
  Mask" path is unchanged (it still classifies image-vs-mask).

## [1.5.340] - 2026-07-10
### Fixed — crash opening non-signifier files via drag-drop / "Add Image / Mask"
- **Fixed ``'FileIOClass' object has no attribute '_file_has_imaging_metadata'``**
  that crashed every drag-and-drop (and every menu "Add Image / Mask") of a file
  without a PyCAT signifier. The image-vs-mask classification path called a helper
  method that was never defined. Replaced with ``_file_has_imaging_metadata_safe``,
  a defensive check (OME/ImageJ/resolution tags, format fallback) that can't raise
  — since it only chooses the wording of the image-vs-mask prompt, any uncertainty
  falls back to the softer "confirm" wording rather than failing the load. The
  menu "Open Image (auto-detect)" path was unaffected (it doesn't classify), which
  is why menu-open worked while drop crashed.
- Note: the ``OME series failed to read … Missing data are zeroed`` lines from
  tifffile on multi-file OME-TIFFs are a normal warning (a companion .ome.tif is
  referenced but not fully resolvable; frames are zero-filled and the load
  proceeds) — not related to this crash.

## [1.5.339] - 2026-07-10
### Added — slow-storage detection at file load
- **PyCAT now warns before a slow load.** When a file is opened (menu Open, menu
  Add, or drag-and-drop) PyCAT quickly probes where it lives and how fast it
  reads, and if the storage is genuinely slow — a network share, a slow external
  drive, or a cloud online-only placeholder that must download first — it shows a
  notice that loading may take a while (with the option to copy to a fast local
  drive if you'll work with the file repeatedly). New module
  ``pycat.file_io.storage_probe``.
- **Measurement-led, so it doesn't cry wolf.** The "is it slow" decision comes from
  a quick *timed read* of the first few MB (sustained throughput, measured after a
  warm-up chunk so a cold-but-fast drive isn't mis-flagged), not from the storage
  bus type — a USB 3.x SSD stays silent, an old spinning disk or busy network
  share warns. Path type is used only as a hint and to detect network / cloud
  locations. Cloud-sync online-only files (OneDrive/Dropbox/Drive) are detected via
  their recall-on-access attribute, so PyCAT can warn that opening will trigger a
  download (and does not touch the file to measure, which would force that
  download).
- **No warning flash.** Fast storage stays completely silent; the notice appears
  only for slow storage, where the load itself keeps the app busy long enough for
  the message to be read (rather than a fixed-timer popup that clears before you
  can process it).
- Scoped as the detection + warning layer. A copy-to-local opt-in and a slow-load
  progress bar are planned follow-ons.

## [1.5.338] - 2026-07-10
### Fixed — dropped masks loaded as fluorescence images
- **Drag-and-drop now classifies image vs mask.** Dropped files previously routed
  straight through the image opener, so a dropped mask loaded as a fluorescence
  Image layer. Drops now go through the same classifying path as File → Add Image
  / Mask: the file TYPE is resolved (PyCAT signifier → pixel-statistics → prompt
  if ambiguous) so a mask loads as a Labels layer and an image as an Image layer,
  while structure is still auto-detected (IMS/TIFF/CZI stacks load lazily as
  stacks, 2D through the channel pipeline). A multi-file drop still loads together
  (first file starts a fresh session, the rest are added).
- Note: canvas drag-and-drop itself requires PyCAT to be launched from a
  NON-elevated terminal — Windows blocks drag-and-drop into an Administrator
  process (the crossed-circle cursor), which is an OS security behaviour, not a
  PyCAT bug.

## [1.5.337] - 2026-07-10
### Added — layer tagging system (foundation for tag-driven autopopulation)
- **Structured, evidence-backed tags** now describe *what each layer is* and *how
  layers relate*, so autopopulation can query typed facts instead of matching
  freeform names. New module ``pycat.utils.layer_tags``. A tag is
  ``(key, value, source, confidence)``; the controlled core keys are role,
  dimensionality, modality, scale, provenance, and channel, with free
  ``user:``-prefixed tags also allowed. The canonical store lives in
  ``layer.metadata['pycat_tags']`` (travels with the layer; removable as one key),
  with a session index cache for cross-layer queries.
- **Assigned at load time** from what the loaders already infer — role (image /
  mask), dimensionality (2d / 2d+t / z-stack / multi-position), scale
  (calibrated vs the 1.0 µm/px "uncalibrated" fallback), provenance, and channel.
  No new detection; existing inferences are captured into the structured store.
- **Lineage tracking.** Upscale, background-subtraction, and segmentation now
  record relationship edges: a processed image is ``derived_from`` +
  ``supersedes`` its source and inherits its identity tags; a segmentation mask
  ``belongs_to`` the image it came from. This makes autopopulation lineage-aware —
  a query for "the image to process" resolves to the **head of lineage** (most-
  derived, e.g. the upscaled/background-subtracted version) rather than the raw
  layer, while raw remains reachable for steps that need pre-processing pixels.
- **Persistence.** Tags survive save→reload: the tag store rides in the same
  embedded JSON blob as the existing image/mask signifier when a layer is saved to
  TIFF, and is re-applied on load (saved user overrides take precedence). (PNG-
  saved 2D labels / shapes / RGB do not yet carry tags — a later sidecar can add
  that; the main image/stack/mask cases persist.)
- **Layer Tag Inspector** (▣ Tags on the menu bar) — view every layer's tags with
  their source and confidence, see its lineage, and override any tag. An override
  is stored as ``user_set`` and **locks** against re-inference, so a hand-set tag
  is never clobbered by a later automatic pass (anti-black-box: you can always see
  *why* a tag is set and correct it).
- **Deliberately not yet built:** the generic resolver and the external JSON
  per-step binding table (which method field uses which tag query). Those are the
  curation layer, kept out of code so re-pointing autopopulation later never
  touches the tag engine. Autopopulation is not yet wired to tags — this release
  is the trustworthy store + inspector first.

### Fixed — drag-and-drop onto the canvas + drop routing
- **Files dropped on the napari canvas now load** (previously the canvas showed
  the red no-drop cursor). Root cause pinned via diagnostic: the drop target is
  the vispy ``CanvasBackendDesktop`` widget, which vispy initialises with
  ``acceptDrops=False`` and re-asserts *after* PyCAT's one-shot enable ran, so the
  flag never stuck. Fix: re-assert ``acceptDrops=True`` on the vispy canvas via
  short deferred timers once vispy has settled, and keep the drop event filter on
  it. (If a canvas drop still shows the no-drop cursor on some GL backends, the
  app-level filter and the layer-insertion backstop still catch the load.)
- **Dropped files now route through the canonical auto-detect opener**
  (``open_image_auto``), so a drop behaves exactly like File → Open Image: each
  file's structure is inspected and sent to the right loader — IMS/TIFF/CZI stacks
  load lazily as stacks, 2D images go through the channel-assignment pipeline, and
  a multi-file drop loads together (first file starts a fresh session, the rest
  add). This replaces an earlier hand-rolled path that forced every non-IMS file
  through the 2D opener and mis-loaded multi-frame stacks as single planes. Drops
  also pick up the new load-time tags.

## [1.5.336] - 2026-07-10
### Added — command palette (Ctrl+Shift+P)
- A fuzzy-search command palette opens methods, toolbox functions, and layers by
  name. Menu-bar "⌕ Search" button or Ctrl+Shift+P. Type a few characters
  (best-match ranked: contiguous matches win, e.g. "bead" → the Bead Detections
  layer, "vpt" → Video Particle Tracking, "bg" → Background Removal); Enter or
  click launches the method or selects+reveals the layer. The command registry is
  accumulated automatically as menus are built, so every analysis method and
  toolbox function is searchable with no per-item wiring. (Phase 1+2 — open
  method/widget by name, find layer by name; finding a step *within* a widget is
  left for later, pending step-addressing infrastructure.)

### Added — MSD plot ↔ layer brushing in VPT
- Clicking a per-track line in the VPT MSD spaghetti plot now reveals that exact
  bead in the napari viewer: it steps to the track's first frame, centres the
  camera on the bead, drops a transient "Picked track" highlight over the whole
  trajectory, and reports the track's length / start frame / median step. The
  picked line is emphasised on the plot too. First instance of the linked-
  multiscale-navigation direction — the identity plumbing (track_id shared between
  the plot, the tracks table, and the napari Tracks layer) already existed, so the
  plot was made pickable via a decoupled on_pick_track callback the VPT UI
  supplies. Highlight points and camera both derive from the image layer's own
  scale, so they overlay the bead correctly whether or not the pixel-size gate
  fired.

### Changed — open on the first frame, clearer detection progress
- **Freshly-loaded stacks open on frame 0**, not napari's default middle frame —
  every non-displayed (T/Z) slider axis is set to 0 on load, matching standard
  image-viewer behaviour.
- **The Detect Beads progress bar is now phase-labelled** ("Preparing frames…"
  then "Detecting beads… %p%") so it no longer appears to "run twice" — the
  earlier confusion was the bar filling during detection after an unexplained
  pause while frames materialised.

### Fixed — multiple-file selection in the open dialogs
- **"Open Image (auto-detect 2D / stack)" and "Add Image / Mask (keep current)"
  now accept multiple files** in the dialog. Both used the singular
  `getOpenFileName` (one file only); they now use `getOpenFileNames` and route
  each selected file through the existing per-file logic. For Open Image, the
  first file honours the clear-first behaviour and the rest are added; for Add
  Image/Mask, each file is classified independently (a selection can mix images
  and masks). Loading several higher-dimensional stacks at once may be slow but
  is permitted.

## [1.5.334] - 2026-07-10
### Fixed — VPT linker shattered stable beads into short tracks (the core linking problem)
- **Root cause 1 — gap-frame off-by-one.** The greedy linker's viability AND
  expiry checks used `t - last_frame <= max_gap_frames`, which at `gap=0` rejects
  even the immediately-previous frame — collapsing every detection into its own
  length-1 "track" (and hanging/crashing the GUI when it then tried to build a
  Tracks layer from tens of thousands of singletons). Fixed to
  `<= max_gap_frames + 1` so `gap=0` links consecutive frames, `gap=1` bridges one
  missing frame, etc. The Bayesian linker had the same off-by-one in its expiry
  check only (inconsistent with its own viability filter, which was already
  correct) — also fixed. Verified on real data: a confirmed stable bead present in
  1000/1000 frames now links into ONE 1000-frame track instead of shattering.
- **Root cause 2 — max linking distance too tight, now auto-derived from bead
  motion.** The dominant problem: the default linking distance (~0.4 µm) was below
  the beads' own frame-to-frame jitter tail (they move up to ~340 nm/frame), so it
  clipped real motion and broke stable beads into short fragments that cannot
  support the MSD measurement window. **New `estimate_linking_distance_um()`
  derives a physically-grounded distance WITHOUT linking any tracks**, via a
  short-window MAX-projection of the stack: the projected bead width broadened
  beyond the single-frame PSF width gives the per-frame displacement scale
  (`motion_σ = √(σ²_proj − σ²_psf)`), and the distance is `k × motion_σ` (k margin,
  default 2.5), capped at the bead footprint so it can't grab neighbours. It is
  viscosity-adaptive (slow beads → tight, fast beads → loose) and needs no
  provisional linking pass. Auto-filled into the Step-4 linker field after Detect
  Beads (shown and editable; anti-black-box), with the margin `k` exposed under a
  new "Show advanced linking options" expander. Validated on real data: derives
  0.58 µm (per-frame motion ~232 nm × 2.5), and linking there yields full
  1000-frame tracks — 10% of tracks now span ≥80% of the movie (was ~0%),
  i.e. measurement-grade trajectories.
- **GUI crash guard.** If a link still comes out degenerate (>2000 tracks and
  >90% single-frame), PyCAT warns and skips building the Tracks layer instead of
  freezing.

### Changed — track-length histogram pops out
- The linker track-length histogram now opens as a dockable "Track lengths" panel
  (reused across relinks) instead of sitting inline in the Step-4 form.

### Added — linking-conditions reliability tag on the automated linkers
- The frame-to-frame linkers (greedy, Bayesian) are reliable only when a bead's
  per-frame displacement is small relative to the nearest-neighbour spacing — the
  governing quantity is the ratio **R = motion / NN-spacing**, not displacement
  alone (a fast bead is trivially linkable if neighbours are far, and a slow bead
  is ambiguous if they are close). `assess_linking_conditions()` computes R from
  the detections WITHOUT tracking (projection-based per-frame motion ÷ single-frame
  nearest-neighbour spacing) and Step 4 shows a colour-coded tag after Detect
  Beads: SAFE (R<0.10), CAUTION (0.10–0.25), RISKY (0.25–0.50, prefer TrackMate
  LAP), UNSAFE (>0.50, use TrackMate LAP or a faster frame rate). It does not block
  any linker — it reports the conditions specific to the user's movie so they can
  choose, and explains *why* the automated linkers are or aren't appropriate here
  (anti-black-box). Linker tooltips updated to reflect the 1.5.334 fixes and point
  to the tag. (Tonight's validated data: R=0.01, deep in SAFE — which is why the
  automated linkers reproduced the reference viscosity.)

## [1.5.333] - 2026-07-10
### Added — VPT detection-variant staging + track-length histogram
- **Detection-variant staging framework.** `detect_beads_stack` gained a
  `detection_variant` argument (default `'baseline'` = the 1.5.329-validated
  path, byte-identical). New detection/classification approaches are opt-in
  variants routed through their own branches, so the validated ~8.325-through-
  TrackMate path stays selectable and any regression is a clean one-arg revert. A
  `compare_detection_variants()` harness runs two variants on the same stack and
  reports the classification diff, and the chosen variant is recorded on the
  output DataFrame's attrs. This is the safety net for the detection rework —
  every proposed change is A/B-measured against baseline before it is trusted.
- **`ring_merge` variant** (`dedup_detections_ring_merge`) — sigma-scaled merge
  radius that folds DIM Airy-ring fragments into their BRIGHT centre while keeping
  two genuinely-bright nearby beads as two. Built and kept in the codebase but
  **not surfaced in the widget and flagged as needing validation data with
  resolved Airy rings**: A/B on the current bead data showed it is a near no-op
  there (beads well-separated, blob_log already ~one detection per bead). Reach it
  via `detection_variant='ring_merge'`.
- **`hot_pixel_reject` variant** (`build_hot_pixel_mask` + harsher on-pixel NCC
  gate) — identifies FIXED sensor hot/dead pixels from the stack's *temporal*
  statistics (scene-independent: hot pixels are flat in time, tstd~3-4; beads are
  variable, tstd~40-50), then applies a STRICTER acceptance test to detections
  landing on them rather than a flat veto — so a real bead drifting over a hot/
  dead pixel still survives on its template evidence. Validated correct and safe
  (every confirmed bead survived, including one adjacent to a hot pixel). Nearly a
  no-op on the current clean fluorescence data (~18 hot pixels found, blob_log
  barely fires on them); earns its place on data/modes that turn hot pixels into
  recurring false detections. Reach it via `detection_variant='hot_pixel_reject'`.
- **Track-length histogram in the linker widget.** Step 4 (Link Trajectories) now
  shows an embedded histogram of trajectory lengths (frames per track) after each
  link. A healthy link piles mass toward long tracks; a fragmentation-prone linker
  shows a spike of very short tracks. The title reports the track count and the
  fraction spanning ≥½ the movie, and a dashed line marks the median — an
  at-a-glance linker-quality check. Fails safe if matplotlib-Qt embedding is
  unavailable.

## [1.5.332] - 2026-07-10
### Fixed — VPT classifier green↔yellow flicker on bright, well-matched beads
- **A bright, high-NCC bead no longer flips singlet↔out_of_plane frame-to-frame.**
  The out_of_plane (yellow) class used a per-frame amplitude/SNR percentile, so
  when the bead population is uniformly low-quality a genuinely good bead sitting
  near the moving percentile line was demoted ~a quarter of the time — driven
  mainly by a `low-SNR OR` clause that could yellow a bead whose amplitude was
  fine. Fix: (1) require the AMPLITUDE to actually be low for the dim class (SNR
  is now only a secondary confirmation, never demotes a bright bead on its own),
  and (2) add a high-NCC singlet guard (NCC ≥ 0.80) so a well-matched bead is
  immune to the dim percentile. Verified on real data: the previously-flickering
  bead (amp~164, NCC~0.94) is now singlet in 1000/1000 frames (was ~76%), while a
  genuinely dim bead (amp~75, NCC~0.76) correctly STAYS out_of_plane — the garbage
  rejection (NCC floor) and aggregate class are untouched. This preserves the
  hard-won hot-pixel/ring/noise rejection while stopping the erroneous demotion of
  real beads.

### Added — MSD lag-window fit gate (hardware-defensible fit bounds)
- `fit_anomalous_diffusion` now computes a **defensible MSD lag window** bounded
  by the frame rate (high-frequency cutoff = frame interval) and the acquisition
  duration (low-frequency cutoff), and by default confines the D/α fit to it.
  Fitting outside this band (only sub-second lags, dominated by the localization
  floor, or out toward the full duration, where a handful of pairs dominate)
  produces a wrong D/α. Exposed under the VPT "Show advanced fit / moduli options"
  expander:
  - **Upper-lag rule** (user-selectable, with tooltips): *Fraction of track length*
    (default, 0.25), *Fixed frequency window* (set the upper lag in seconds), or
    *Minimum independent pairs* (keep a lag only while ≥ N independent tracks span
    it).
  - **"Confine fit to scientifically defensible bounds"** toggle (default ON) —
    clips the fit to the window; turn off to fit the full range at your own risk.
  - The gate **warns, never blocks**: if the acquisition can't cover the requested
    window (too-short clip), it emits a clear warning and falls back gracefully.
  Validated on real data across all three rules + the confine toggle + the
  too-short-data warning path.

## [1.5.331] - 2026-07-10
### Added — Evans (2009) viscoelastic moduli + bootstrap confidence bands
- **G′/G″ (storage/loss moduli) now use the Evans et al. (2009) direct
  compliance→moduli conversion** (`compute_moduli_evans` in
  condensate_physics_tools.py), replacing the Mason (2000) single-point algebraic
  GSER in the VPT pipeline. Evans represents the creep compliance J(t) as a
  piecewise-linear interpolant and analytically Fourier-transforms it, so
  G*(ω)=1/(iω·J̃(ω)) with **no local-power-law assumption** — it handles
  curvature, plateaus, and crossovers directly. Validated in-sandbox against
  known analytic MSDs: exact on a pure viscous fluid (G′≈0, G″=ηω to machine
  precision) and ~1–2% on a Maxwell fluid across the reliable band. The highest
  one or two frequencies (shortest lags) are the least reliable and are dropped.
  The Mason `compute_moduli_gser` is retained (additive/revertable).
- **Optional bootstrap confidence intervals on G′/G″**
  (`compute_moduli_evans_bootstrap`), exposed under a new hidden "Show advanced
  moduli (G′/G″) options" expander in the VPT Step-5 panel (default off). Resamples
  whole tracks with replacement, recomputes moduli per resample, and shades
  percentile bands on the plot (`plot_moduli` draws bands when present). Validated
  in-sandbox: ~93–97% empirical coverage of a known analytic truth for a nominal
  95% band. This is the honest response to noisy data — it shows which parts of
  the spectrum to trust.
- NOTE: a compliance-interpolation upgrade (natural/Akima spline) was evaluated
  and **rejected** — validated as a no-op on smooth MSDs and unhelpful (can worsen
  jitter) on noisy ones; the real levers for noise are the CIs above plus upstream
  trajectory cleanup. Documented as such in the code.

### Added — dual pixel/µm coordinate readout
- **The napari status bar now shows both the pixel index (r, c) and the world
  (µm) position under the cursor**, plus the value under the cursor, e.g.
  `px (r=362, c=483) | µm (y=242.5, x=323.6) | Bead Detections = 171`
  (`pycat/ui/coordinate_readout.py`, installed at launch in run_pycat.py). PyCAT
  scales image layers by pixel size (µm/px), so napari's default status showed
  microns only; pixel indices are what the analysis actually runs in (blob sigma,
  linking distances, template windows, FIJI cross-referencing), so both are now
  surfaced. Best-effort and fail-safe — never blocks launch, and leaves napari's
  default status untouched if the coordinate can't be resolved.

### Docs
- Added a "Near-term UX & interaction" section to the development roadmap
  (frame-0-on-load, materialization progress, pixel-size acquisition profiles,
  command palette, plot↔layer brushing, dual px/µm readout).
- Added `docs/audits/DEV_NOTES.md` (private, Sphinx-excluded): instrument-scoped
  module roadmap and known-issues detail kept out of the published docs.

## [1.5.208] - 2026-07-05
### Fixed (overlay stripe — the ACTUAL root cause: a scale mismatch)
- **The Overlay Image now inherits the source image layer's scale.** Confirmed via git
  diff that in v1.0.0 the "Upscaled Fluorescence Image" was added with NO scale (default
  1.0), and the overlay (also no scale) matched it — so the (H, 2W) side-by-side rendered
  correctly. A later change gave the upscaled layer an explicit physical µm/px `scale`
  (~0.049) to align it with its source, but the overlay was never updated to match, so it
  stayed at scale 1.0 — a ~20× coordinate mismatch that rendered the overlay as a giant
  stripe extending far past the scaled data (the "µm at the data, mm at the overlay"
  scale-bar symptom). The overlay is now added with the same scale as its source image
  layer, putting both back in one coordinate space. This is the real fix; the previous
  reverts addressed the wrong layer of the problem.
### Improved
- **Overlay PNG contrast.** The exported `_puncta_overlay.png` blew out the bright cell
  body because the percentile stretch was computed over the whole frame (mostly black
  background, dragging the window down). It now computes the stretch window over the
  signal pixels (non-near-zero) with a high upper percentile (99.8), preserving bright
  detail instead of clipping it to white.

## [1.5.329] - 2026-07-09
### Fixed (drag-and-drop onto the canvas — layer-insertion backstop)
- **Files dropped on the napari canvas now load through PyCAT.** On napari 0.7.1 the canvas
  refuses the drag before any Qt event filter can catch it (the persistent "no-drop" cursor),
  so intercepting the drop at the widget level is impossible. This takes the opposite
  approach: let napari's reader load the file, then detect the resulting layer as FOREIGN
  (napari sets `layer.source.path` on reader-loaded layers; PyCAT's programmatic `add_image`
  leaves it `None`), remove the raw napari layer(s), and re-open the same path through PyCAT's
  context-aware opener so it enters the channel-assignment / metadata pipeline. This catches a
  load regardless of how it was triggered, without depending on reaching napari's canvas
  widget.
  - Handles a multi-channel drop (one file → several napari layers sharing a path): all are
    removed and the path is re-opened once. Multiple distinct dropped files: the first
    replaces the session, the rest add without clearing (comparison).
  - Re-entrancy-guarded so PyCAT re-opening the file doesn't re-trigger the backstop;
    PyCAT's own layers (source.path=None) are never touched. Deferred via QTimer so the layer
    list isn't mutated inside the inserted-event callback. Validated the foreign-detection and
    dedup logic in the sandbox.
  - There is a brief moment where napari's raw layer exists before PyCAT swaps it; this is
    inherent to letting the drop land first, and is the trade for catching canvas drops that
    can't be intercepted at the widget level on 0.7.1.

## [1.5.328] - 2026-07-09
### Fixed (napari File menu — hide the now-empty submenu containers)
- The load-action lockdown (1.5.320) correctly removed napari's direct loaders (Open
  File(s), Open as Stack, Open Folder, Open Sample, New Image from Clipboard), but left three
  now-empty submenu CONTAINERS visible: "Open with Plugin" (all its entries were load actions
  we hid), "IO Utilities", and "Acquire" (napari extension points holding only disabled
  `empty_dummy` placeholders). The menu-tree walk now also hides a submenu container when,
  after processing, every action inside it is hidden/disabled or an `empty_dummy` — so those
  three vanish while genuinely-useful submenus (e.g. New Layer, which has live entries) are
  left intact. Verified against the live napari 0.7.1 action dump.

## [1.5.327] - 2026-07-09
### Added (standalone Reference / Background Subtraction widget)
- **New Toolbox → Image Processing → "Reference / Background Subtraction" widget.** A general
  reference-subtraction tool built on the validated `reference_subtraction` core:
  - **Input** selected from a layer dropdown (2D image or T/Z stack).
  - **Reference** chosen either as a frame INDEX within the input (static-pattern removal,
    with the reference frame rebuilt from its neighbours) OR as a SEPARATE image layer
    (loaded via Add Image — a clear field of the same view); the external reference's shape
    is checked against the input frames.
  - **Modality** selector — Brightfield (subtract pattern, keep gray baseline) or
    Fluorescence (preserve background floor + noise, adaptive softening so signal isn't
    driven below zero; reports the applied strength and warns if it had to soften, which
    signals a reference/data mismatch).
  - **Advanced** max-clip-fraction control (default 0.01%, range 0.001–1%) for the
    fluorescence softening.
  - **Output** added as a new layer; **Export** to TIFF (float32) or MP4 (same imageio/pyav
    backend the temperature export uses).
- The widget reuses the same subtraction function as the temperature workflow, so there's one
  implementation of the science.

## [1.5.326] - 2026-07-09
### Fixed (temperature — subtraction now produces a visible layer)
- **"Subtract first frame" in temperature-dependent microscopy now lets you SEE the
  subtracted result.** Previously the reference subtraction was applied only internally
  (to the entropy computation and the MP4/TIFF export) with no visible layer. A new
  "Preview subtracted stack → new layer" button applies the subtraction and adds the
  corrected stack to the viewer, without disturbing the rest of the method.

### Added (generalized reference-subtraction core, reused by the above)
- **`reference_subtraction(stack, reference, mode, …)` in temperature_tools** — a general
  reference/background subtraction usable both by the temperature workflow and (next) a
  standalone widget:
  - **Brightfield mode:** `frame − reference + mean(reference)` — subtract the fixed
    pattern, add back the mean gray so the brightfield baseline is preserved (the existing,
    validated behaviour); the in-stack reference frame is rebuilt from its neighbours
    (nn/nnn) so it isn't a flat outlier, matching the entropy-inheritance fix.
  - **Fluorescence mode:** subtracts only the STRUCTURED part of the reference
    (`reference − min(reference)`), preserving the uniform background floor and its noise
    texture — because a heavily-zeroed image loses the background structure a microscopist
    reads, and flattening discards real information. The subtraction strength is softened by
    a single factor α (chosen so no frame clips more than a set fraction of pixels, default
    0.01%), residual negatives are clamped, and α (<1 signals a reference/data mismatch) is
    reported. Validated in the sandbox: floor + signal preserved, no negatives, α backs off
    correctly when the reference is too bright.

## [1.5.325] - 2026-07-09
### Fixed (grid — from live test)
- **"Show/hide all" no longer shuffles the grid order.** The reflow moved visible layers to
  the front based on the transient list order, so cascading visibility events (especially
  show/hide-all) scrambled which layer sat in which cell. The grid now snapshots a CANONICAL
  layer order the moment grid mode is enabled and arranges every reflow against that fixed
  anchor — so visibility toggles reflow the grid to fill the canvas without ever changing a
  layer's slot. Verified: after any hide/show sequence (including hide-all then show-all) the
  order returns exactly to the canonical arrangement. Layers added after grid-on append to
  the anchor in arrival order.
- **Grid toggle now lives ONLY in the PyCAT toolbar** (🗃 Grid, Layers section). Removed the
  duplicate "Toggle Side-by-Side" item from the Open/Save File(s) menu.

## [1.5.324] - 2026-07-09
### Added (acquisition-metadata comparison / trust check for side-by-side)
- **A metadata-diff table now flags when compared images were acquired under different
  settings.** Comparing images with different exposure, laser/excitation, objective, NA,
  pixel size, emission filter, bit depth, or modality can make a quantitative comparison
  untrustworthy — regardless of how the grid looks. PyCAT now diffs the acquisition metadata
  across the currently visible images and presents a table highlighting differences: red for
  settings that critically affect quantitative comparison, amber for less-critical ones,
  with a plain-language verdict at the top.
- **It runs automatically when grid comparison starts** with 2+ images and pops the table
  only if a *critical* setting differs (stays quiet when settings match). It's also available
  on demand via a **"Compare loaded images…"** button in the ⓘ Metadata dialog.
- To support this across a multi-image session (where `data_repository['file_metadata']` is
  overwritten on each load), each image's acquisition metadata is now stashed on its napari
  layer (`layer.metadata['pycat_file_metadata']`) at load time, so per-layer comparison works.
- The comparison logic (`compare_acquisition_metadata` in metadata_extract.py) treats a
  missing value as "unknown" (not a conflict), compares numerics with tolerance, and was
  validated on identical / critical-diff / info-diff / missing-value / 3-image / all-empty /
  single-image cases.

## [1.5.323] - 2026-07-09
### Fixed (grid reflow — now driven by the napari 0.7.1 diagnostic)
- **Grid now reflows to only the visible tiles.** A diagnostic on napari 0.7.1 established
  that napari's grid tiles by TOTAL layer count and ignores visibility (so hidden layers
  left empty black tiles, and `shape=(-1,-1)` auto-recomputed to the full count) — but
  setting `grid.shape` EXPLICITLY to fit the visible count DOES reflow, and napari fills
  cells by layer index. The managed grid now: removes only pure annotation/drawing
  (Shapes/Points) layers, moves the visible tileable layers (images + visible masks) to the
  front so they occupy the exposed cells, and sets an explicit `grid.shape` sized to the
  visible count — so hiding/showing a layer via its eyeball reflows the grid to fill the
  canvas.
- **Masks now ride along in the grid** instead of being removed: Labels (mask) layers stay
  in the layer list and overlay their image, controlled by their own visibility eyeball
  (per the intended comparison behaviour). Only annotation/drawing layers are set aside.

### Changed
- **Grid toggle moved to the PyCAT toolbar** (Layers section, next to the show/hide-all-eye
  and Gray/Viridis colormap controls) as a "🗃 Grid" button, where a viewer-layout action
  belongs — instead of being buried in the Open/Save menu.
- **Tightened the image-vs-mask default** in "Add Image / Mask": a file is only defaulted to
  MASK when its integer values look like real label IDs (contiguous from 0, i.e. 0..N, or
  binary), not merely "few values". This stops low-contrast or few-valued IMAGES from
  defaulting to mask. (The user still confirms in the dialog, and PyCAT-saved files skip the
  guess entirely via their signifier.)

## [1.5.322] - 2026-07-09
### Fixed (grid tiling of annotation layers — from live test)
- **Grid mode no longer leaves empty tiles for annotation/drawing layers.** Hiding those
  layers wasn't enough — napari's grid tiles by TOTAL layer count, so a hidden layer still
  claimed a cell. The managed grid now temporarily REMOVES non-image layers (annotations,
  shapes, points) from the viewer while grid is on — preserving each layer object and its
  contents — so napari tiles exactly the image layers, and re-inserts them at their original
  positions when grid is toggled off.
- **A message now announces the set-aside.** When grid removes annotation/drawing layers, a
  notification says they've been temporarily set aside (with their contents) and will return
  on grid-off, so a drawing layer disappearing from the list isn't alarming. A matching
  "restored" message appears on grid-off.

### Added (saved-file type signifier — systemic fix for image-vs-mask ambiguity)
- **PyCAT now stamps a signifier in the metadata of TIFFs it saves** (a small JSON tag in
  the ImageDescription recording whether the layer is an image or a label mask, plus the
  PyCAT version). This removes the guesswork when such a file is loaded back.
- **"Add Image / Mask" resolves type in priority order:** (1) if the file carries PyCAT's
  signifier, its type is known exactly — no prompt; (2) if it has NO imaging-structure
  metadata AND no signifier, the user is ASKED what they loaded (image or mask); (3)
  otherwise a pixel-statistics guess (integer + few / consecutive label IDs → mask) is
  offered as the default in a confirmation prompt. Round-trip verified: PyCAT-saved images
  and masks reload with their type recognized automatically.

## [1.5.321] - 2026-07-09
### Changed (consolidated "Open 2D Mask(s)" into the add-without-clear flow)
- **"Open 2D Mask(s)" (a 1.0.0 holdover) is folded into a unified "Add Image / Mask (keep
  current)".** The old separate mask opener existed only to load a previously-generated
  mask into a session for colocalization without re-analysis — which is exactly
  add-without-clearing, just producing a Labels layer instead of an Image layer. The new
  unified opener peeks at the file, classifies it as a label mask (integer dtype with few /
  consecutive label IDs) vs an image (float, or many spread values), and asks the user which
  to load as — defaulting to the detected type. Masks load as napari **Labels** layers (for
  coloc/analysis); images route through the context-aware 2D/stack opener. Both add without
  clearing the current session.
- `open_2d_mask` gained a `clear_first` parameter (defaults to False — masks add to the
  existing session by design).
- The File menu is now: Open Image (auto-detect) / Add Image / Mask (keep current) / Toggle
  Side-by-Side / Load Previous Session / Save and Clear.

## [1.5.320] - 2026-07-09
### Fixed / Changed (from live test feedback)
- **Removed the redundant "Open 2D Image(s)" and "Open Image Stack (T/Z / IMS)" menu items.**
  The context-aware "Open Image (auto-detect 2D / stack)" replaces both; the menu is now
  Open Image / Add Image / Toggle Side-by-Side / Load Previous Session / Open 2D Mask(s) /
  Save and Clear.
- **napari load-action disable strengthened for 0.7.1.** The previous version disabled
  QActions found via `window.findChildren`, but napari 0.7.1 provides menu actions through
  its app-model — they may not be window children, so the sweep missed them and napari's
  File → Open stayed live. The guard now WALKS THE MENU-BAR TREE directly (reaching
  app-model actions wherever they live), disables AND hides each load action (a hidden
  action can't be triggered even if napari rebuilds/re-enables it), and re-runs on every
  menu `aboutToShow` in case napari recreates the actions when the menu opens.
- **Side-by-side grid is now PyCAT-managed.** napari's raw grid tiled EVERY layer, so the
  Cell/Object Diameter annotation Shapes layers got their own empty tiles instead of
  overlaying the images. The managed grid tiles only IMAGE layers and hides non-image
  annotation/shape layers while grid is on (restoring them on grid-off), and recomputes when
  layer visibility changes so hiding/showing an image reflows the grid. (Reflow on image
  visibility uses napari's auto grid sizing; behaviour across napari builds should be
  verified live.)

## [1.5.319] - 2026-07-09
### Changed (VPT — validation status surfaced after TrackMate confirmation)
- **TrackMate LAP confirmed validated for viscosity** (recovers within ~10% of the
  reference workflow through PyCAT). Its tooltip now states this; it is the recommended
  linker for quantitative microrheology.
- **Fragmentation warnings added to the Bayesian and Greedy linker tooltips.** Both are
  not-yet-validated for quantitative viscosity and can produce fragmented (short, broken)
  trajectories that bias the ensemble MSD and the resulting viscosity; the tooltips now say
  so and point users to TrackMate for quantitative results and to the track-spanning report
  as a health check. (The underlying Bayesian/Greedy linkers still need debugging — low
  priority; the warnings prevent silent misuse in the meantime.)
- **G'/G'' (storage/loss moduli) flagged as not-yet-validated.** The current estimate uses
  the **Mason (2000) algebraic GSER** (|G*| = kBT/(πa·MSD(1/ω)·Γ(1+α)), split by
  G'=|G*|cos(πα/2), G''=|G*|sin(πα/2), with α the local log-slope of the MSD). This is NOT
  Evans's method. It has two known failure modes — meaningless G' on viscous samples (α≈1,
  small difference of noisy terms) and sensitivity to MSD noise from fragmented tracks — so
  a console caveat now prints when moduli are computed, and the function docstring documents
  the status. PLANNED UPGRADE: replace with **Evans et al. (2009, Phys. Rev. E 80:012501)**
  direct compliance→moduli conversion (more robust; no single-point power-law assumption),
  to be validated against a known analytic MSD once Gable provides a viscoelastic test set.

## [1.5.318] - 2026-07-09
### Added (context-aware opener, add-without-clear, side-by-side grid)
- **Context-aware "Open Image (auto-detect 2D / stack)".** A single opener parses the
  file's dimensional structure (X/Y/Z/C/T/P) BEFORE loading and routes it: any real Z or T
  axis (size > 1), or multi-position (P > 1), goes to the lazy stack loader; a single XY
  plane (optionally multi-channel XYC) goes to the 2D loader. Channels remain SEPARATE
  overlaid layers (the analysis pipeline is unchanged); the decision is made on the real
  axes, not the file extension. Falls back to the 2D loader if structure can't be read. The
  two over-specific "Open 2D Image" / "Open Image Stack" items remain as explicit options.
- **"Add Image (keep current)".** Opens an image WITHOUT clearing the session — adds its
  layers alongside the existing ones, via a new `clear_first=False` path on both openers.
  For loading a missing channel of a split-file image, or placing a second image next to the
  first for comparison.
- **"Toggle Side-by-Side (grid view)".** Flips napari's grid mode so multiple loaded
  images/layers tile in the canvas for comparison. (They share one camera + dim sliders —
  good for same-modality comparison; full independent-window comparison is a separate
  roadmap item.)
- **Stack slider axes are now labelled T / Z** (instead of the default 0 / 1), so
  multi-dimensional browsing is legible.

### Roadmap
- Pinned **FIJI-style independent multi-image comparison** (independent windows/zoom/dims
  per image) as an architectural project — it cuts against the single-``active_data_class``
  design, so it's evaluated carefully rather than rushed; grid-view + add-without-clear
  cover most same-modality comparison in the meantime.
- Pinned a **multi-scene / position scene-switcher** (lazy one-scene-at-a-time browsing of a
  single multi-scene file, e.g. CZI SizeS>1) as a follow-up to the context-aware opener.

## [1.5.317] - 2026-07-09
### Fixed (napari file-loading could bypass PyCAT — verified against napari 0.7.1)
- **napari's own data-loading actions are now hard-disabled by objectName.** The napari
  File menu is hidden by default, but if a user revealed it (via the ☰ napari toggle) and
  used File → Open, the file loaded through napari's reader — bypassing PyCAT's
  channel-assignment / data-repository registration and breaking downstream analysis. The
  previous guard matched on display TEXT, which was stale for napari 0.7.1 and didn't fire.
  The guard now matches on napari's stable action `objectName`s (e.g.
  `napari.window.file.open_files_dialog`), which is version-robust, and covers every load
  path: Open File(s), Open Files as Stack, Open Folder, all three "Open with Plugin"
  variants, New Image from Clipboard, and every "Open Sample" loader. Verified against a
  live napari 0.7.1 menu dump: all load/sample actions match, and nothing safe (Preferences,
  Save Screenshot, Close/Exit, all View/Layers/Window/Help actions) is touched.
- **The guard re-applies on menu `aboutToShow`.** napari 0.7 builds some menu actions
  lazily (they don't exist until the menu is first opened), so a one-shot startup sweep
  missed them — the likely reason the old guard appeared inactive. The disable now re-runs
  every time a file menu opens, so lazily-created or re-enabled actions can't leak.
- napari's **New Layer** (empty Labels/Points/Shapes) and all Save-screenshot / view /
  layer-visualization actions are intentionally left enabled — they don't load external
  data into PyCAT's pipeline.

## [1.5.316] - 2026-07-09
### Added
- **napari-integration audit** (`docs/audits/PyCAT_napari_integration_audit_2026-07-09.md`)
  covering branding, napari feature usage, and file drag-and-drop routing, with file:line
  evidence and priorities.

### Fixed (audit finding, P1 — drag-and-drop bypassing PyCAT)
- **Files dropped on the napari CANVAS could bypass PyCAT's file I/O.** PyCAT installed an
  application-level drop filter, but napari's canvas widget (QtViewer) has its own
  `dropEvent` that routes to napari's reader — so a file dropped directly on the image area
  (the most natural target) could load through napari and skip PyCAT's channel-assignment /
  data-repository registration. The PyCAT drop filter is now ALSO installed directly on the
  canvas / qt_viewer widget so it intercepts and consumes the drop before napari's handler,
  across napari-version accessor differences (`_qt_viewer` / `qt_viewer`, `canvas` /
  `native`), all guarded defensively.
  ⚠️ **Needs live verification:** napari isn't available in the build/test sandbox, so the
  widget-accessor path and event precedence can only be confirmed by actually dragging a
  file onto the canvas in the running app. Verify: (1) drop a CZI/TIFF onto the image area →
  PyCAT's channel-assignment dialog should appear (not a bare napari layer); (2) drop onto
  the dock/side panels → still routes through PyCAT; (3) dragging a path into a text field
  still works (input widgets are intentionally skipped).

### Fixed (audit finding, P2 — OS-level branding)
- **The app identified itself as "napari" (or "python") to the OS.** `setApplicationName` /
  `setApplicationDisplayName` were never called, so the taskbar / dock / window-manager
  showed the wrong name despite the in-window branding being thorough. Now set to "PyCAT"
  (plus `setDesktopFileName("PyCAT")` on Linux) at QApplication creation.

### Surfaced, not changed (your call)
- Window title is still `PyCAT-Napari`; per the rebrand roadmap note, consider `PyCAT`.
  Left as a positioning decision.
- napari's advanced visualization (3D display, tracks, vectors, surfaces) is barely used —
  confirms the roadmap's 3D-rendering / kymograph / tracks items are genuine additive
  opportunities, not defects.

## [1.5.315] - 2026-07-09
### Added
- **Full per-method audit** (`PyCAT_method_audit_2026-07-09.md`) covering all 18 analysis
  methods across four axes — workflow/tool-chain soundness, performance/redundant I/O,
  autopopulation logic, and UEX status-circle coverage — with file:line evidence, findings
  tagged by category, and a priority ranking. See the doc for the full findings and the
  pinned P2/P3 follow-ups.

### Fixed (audit finding CC-1, P1 — redundant materialization)
- **temperature_ui re-materialized the same stack up to 4× per session.** Four analysis
  buttons (clear-frame guess, turbidity, per-temperature analysis, pattern correction/export)
  each independently called `materialize_stack(...)` on the *same* selected stack, re-decoding
  the entire lazy time-series from disk on every click. Added a `_get_stack()` cache keyed on
  the layer name AND the underlying data object's identity, so the stack is materialized once
  and reused across all four analyses; the cache invalidates automatically when the user picks
  a different stack or the layer data is replaced. Validated: 4 clicks on one stack → 1
  materialization (was 4); switching stacks correctly re-materializes.

### Audit findings verified as NON-issues (recorded so they aren't re-chased)
- Autopopulation is not broken in the delegator UIs: the nine per-method
  `create_layer_dropdown` reimplementations are thin delegators to the base helper, which
  carries the auto-refresh + name_hint wiring — so dropdowns update correctly everywhere.
- The multiple `materialize_stack` calls in frap / invitro_fluor / brightfield operate on
  *different* layers or in *different* handlers — not redundant re-reads (only temperature was).

### Pinned follow-ups (P2/P3, in the audit doc — not changed this release)
- UEX status circles for temperature / fusion / timeseries_invitro_fluor / fd_curve.
- Generalize the colocalization smart layer pre-selection into a shared helper.
- Worker-thread offload for nb_ui / spida_ui / frap_ui / fusion_ui (heavy compute on the
  main thread). Progress-bar rollout continues per the existing roadmap rubric.

## [1.5.314] - 2026-07-09
### Added (reusable phased-progress mechanism; VPT double-100% fixed)
- **`PhasedProgress` helper (`ui_utils`)** maps several sequential work phases onto ONE
  continuous 0→100% progress bar. This fixes the class of confusion where a method that
  MATERIALIZES a lazy stack and then PROCESSES it drove the bar to 100% twice (or left one
  phase looking frozen). Each phase gets a weighted slice of a single monotonic bar, with an
  optional phase-name label. Its `callback(done, total)` matches the progress_callback used
  throughout PyCAT, so existing per-phase callbacks drop in unchanged. Span math verified.
- **`materialize_stack` / `as_full_array` now accept a `progress_callback`** so the
  frame-by-frame rebuild of a lazy stack can drive a determinate "Materializing…" bar
  instead of a silent freeze. Eager arrays return immediately (no spurious progress).
- **VPT bead detection double-100% fixed** (where this thread began). In CPU-parallel mode,
  detection ran two loops (parallel pre-detection, then serial scoring) that each drove the
  bar 0→100%. The parallel pass now fills 0→70% and the scoring pass continues 70→100% — a
  single monotonic sweep that reaches 100% once. Pure-serial mode is unchanged (0→100%).

### Documentation / roadmap
- Added a **progress-bar audit** rubric to the roadmap tracking the per-method rollout of
  the new helper (wire materialization progress into the seven materialize-then-work UIs;
  add bars to the zero-bar slow UIs contrast_cascade / fd_curve / data_qc; audit the core
  cell/condensate runners). Deliberately staged as a per-method rollout rather than a
  blanket sweep.
- Added a **documentation audit** rubric capturing tester feedback that the instruction
  docs have drifted from the current GUI (missing "measure lines", stale instruction
  screenshots, doc-vs-GUI name mismatches like "Condensate segmentation" vs "sub cellular
  object segmentation"). These are docs fixes, tracked so they are not lost.

## [1.5.313] - 2026-07-09
### Added (batch — automatic object-size → ball_radius estimation, human out of the loop)
- **ball_radius is now estimated per image during batch processing** for fluorescence
  workflows, so batches no longer need a hand-tuned ball_radius (Meet Raval's request). New
  `estimate_object_size_px()` implements the validated pipeline: white top-hat → Otsu →
  label → median object equivalent-diameter → ball_radius = round(size/2). Verified on
  synthetic puncta (recovers ~8 px objects → ball_radius 4).
- **Strictly scoped to workflows where intensity thresholding is valid.** Auto-estimation
  applies ONLY to 2D cellular fluorescence and 2D in-vitro fluorescence, inferred from the
  recorded step names. Brightfield, time-series, and z-stack workflows are excluded (top-hat
  + Otsu size estimation is not physically valid there — brightfield is edge/phase contrast,
  time-series object size drifts, z-stack projection diameter ≠ 3D size). The estimator also
  carries a hard `workflow` validity guard that raises rather than silently producing a bad
  radius. An explicitly recorded ball_radius always takes precedence and disables the auto path.
- **The user is told at batch start** (not a hidden step): when auto-estimation is active a
  clear message is printed explaining that ball_radius will be estimated per image and why,
  and each image logs its estimated value + object count.
- **Experimental brightfield estimator stubbed (not wired in).**
  `estimate_object_size_px_brightfield()` uses Sobel edge-energy + Otsu + hole-filling
  instead of intensity top-hat, but is explicitly marked NOT VALIDATED and is intentionally
  left out of the automatic path pending validation on real brightfield data.
- Both estimators are flagged in-code for optimization/validation on real datasets before
  being relied on quantitatively.

## [1.5.312] - 2026-07-09
### Changed (Colocalization — unified tabbed widget, phase 1)
- **The two separate colocalization pipelines are merged into one tabbed widget.** Object-
  based and pixel-wise colocalization were previously two separate menu entries and UI
  classes, inconsistent with the tabbed multi-method pattern used elsewhere. They are now a
  single **Colocalization Analysis (Pixel-wise + Object-based)** method with a
  `QTabWidget`: a "Pixel-wise Correlation" tab (CLAHE/WBNS/RB/rescale preprocessing →
  PWCCA metrics → cross-correlation-function analysis) and an "Object-based Colocalization"
  tab (upscale/preprocess → Cellpose → cell + subcellular segmentation → two-channel /
  object-based / Manders object coloc). All existing metric functions and method-picker
  dialogs are reused unchanged; only the housing is unified.
- **Layer hand-off from upstream methods.** Because the coloc runner dropdowns read live
  viewer layers, any processed images and masks produced by a prior 2D/3D cell or in-vitro
  analysis are already available in the widget. In addition, on open the widget makes a
  best-effort guess at sensible defaults from common upstream layer names (e.g. "Upscaled
  Fluorescence Image", "Labeled Cell Mask", "Condensate Mask") and pre-selects them in the
  dropdowns, so a cell/in-vitro → colocalization workflow lands ready to run. The user
  re-curates freely; the guess is convenience only.
- The old `ObjectColocAnalysisUI` / `PixelColocAnalysisUI` classes and their switch methods
  remain in the codebase (no longer in the menu) as a safe fallback during the transition.

### Notes / next phases
- **Phase 2 (planned):** multi-channel — start with pairwise across N selected channels,
  building toward a full combinatorial N×N coloc matrix.
- **Phase 3 (planned):** surface the CCF / van-Steensel cross-correlation-function tools
  (currently in `correlation_func_analysis_tools`) as first-class coloc options, and add
  object nearest-neighbour distance distributions. A toolbox audit found the coloc *metrics*
  are well covered (Pearson, all Manders variants, Spearman/Kendall/weighted-tau, Li ICA,
  Costes significance, Jaccard/Dice, object distances) but fragmented across three modules
  and lacking multi-channel orchestration — which these phases address.

## [1.5.311] - 2026-07-09
### Added (VPT scientific-choice items made explicit & recorded — audit #9–#11)
- **Explicit drift-correction modes (#9).** Center-of-mass subtraction is standard for
  microrheology but also removes any REAL collective motion (internal flow, sedimentation,
  bulk translation). `drift_correct_com` now takes a `mode`: **Ensemble COM** (the previous
  always-on behaviour, default), **Immobile-reference** (estimates drift from only the most
  stationary tracks, so genuinely flowing/diffusing beads don't bias the correction), or
  **None** (keep collective motion — for internal-flow studies). Exposed as a Step-5
  dropdown and recorded in the microrheology provenance. Verified on a synthetic mix of
  stationary + flowing tracks: plain COM over-corrects the stationary beads (the flowing
  track pollutes the estimate) while immobile-reference recovers them exactly.
- **Out-of-plane handling made explicit (#10).** Recovered out-of-plane (yellow) beads are
  already excluded from viscosity unless the population selector includes them — but the
  temporal-stability pass promotes stable dim tracks back to singlet, which can fold a
  persistent defocused bead into the viscosity set. This promotion is now a Step-5 checkbox
  ("Promote stable dim tracks to singlet", default on = prior behaviour); turning it OFF
  gives a stricter singlet-only viscosity that never merges defocused beads whose axial
  fluctuations could masquerade as 2D motion. Recorded in provenance.
- **Fast-mode classification thresholds are now recorded (#11).** The (previously purely
  hard-coded) fast-template thresholds — NCC floor, aggregate mass/amplitude percentiles and
  their resolved values, dim-amplitude percentile, strictness — are attached to the
  classification result and captured in the bead-detection provenance record, so a fast-mode
  run is reproducible and the imaging regime is auditable. (Exposing them as editable
  advanced controls is deferred to the planned interactive detection-filter widget.)

### Fixed (introduced-and-caught during this change)
- Added `QComboBox` to the top-level PyQt import in `vpt_ui` — the new drift-mode dropdown
  used it where only a local import existed elsewhere, which would have raised a NameError.

## [1.5.310] - 2026-07-09
### Fixed (VPT bugs — verified against an external audit)
- **`classify_beads()` crashed in every Gaussian-fit mode** (`fast_fit`, `precise`, legacy
  `fit_quality=True`). The Gaussian-fit branch used a `valid` mask that was never defined
  (the fast-template branch returns before it), so any fit-mode detection raised a
  NameError. `valid` is now defined as the finite-`integrated_intensity`/`sigma_mean`/
  `r_squared` mask before use. Verified both the Gaussian-fit and fast-template paths now
  classify correctly.
- **The bead-class summary table silently vanished in fast mode.** `vpt_ui` hard-coded
  `median_sigma=('sigma_mean', 'median')` in the per-class aggregation, but fast template
  mode produces no `sigma_mean` column; the resulting KeyError was swallowed, so the user
  lost the summary on every (default) fast-mode run. The aggregation is now built from
  whichever columns exist (adding `median_ncc` in fast mode), and a failure is logged via
  `debug_log` instead of vanishing.
- **"Infer host from beads" mode discarded the inferred mask during detection.** The detect
  step did `if mode != 'host': host_mask = None`, which threw away the inferred host in
  `infer` mode and ran full-frame — so inferring a host had no effect on bead filtering.
  Now only `nohost` mode clears the mask; `infer` mode keeps its inferred host (and warns
  if one hasn't been inferred yet).
- **Erosion control was disabled in infer mode** even though `_infer_host_from_beads()`
  erodes the inferred mask with the spin's value — so infer mode used a stale/hidden
  erosion setting. Erosion is now enabled for both `host` and `infer` modes.
- **`vpt_infer_host` was recorded but had no batch-replay handler**, so an inferred-host run
  created an unregistered step. Added a skip handler in `batch_step_registry` matching the
  other (interactive, non-replayable) VPT steps.

### Audit items checked and NOT changed
- **`aggregate_population_stats` missing-`sigma_mean` guard:** already fixed in a prior
  version (guards both `sigma_mean` and `n_units_est`). No change.
- **`run_vpt_analysis` defaults slow/precise:** the audit claimed `bead_fit_quality=True`;
  the actual default is `fit_quality=False` (fast mode), already consistent with the UI.
  No change.
- **Scientific-choice items (drift-correction modes, out-of-plane default, hard-coded
  fast-mode thresholds):** these are analysis-design decisions, not bugs, and are being
  taken to the roadmap/next-session discussion rather than changed unilaterally.

## [1.5.309] - 2026-07-09
### Added
- **Canonical `normalize01()` in `general_utils`.** A single, safe min-max normaliser to
  [0, 1] that returns zeros on a flat/constant array instead of dividing by zero. New code
  (and files as they're touched) should use this instead of re-inlining
  `(x - mn) / (mx - mn ...)`, so the divide-by-zero guard and behaviour stay consistent.

### Audit note (health audit findings 4 & 5 — closed as low-value after inspection)
- **Finding 4 (duplicated normalise idiom):** on close inspection every existing site is
  already guarded (an `if mx > mn` / `if mx <= mn` check precedes each one), so there is NO
  latent divide-by-zero bug — the finding is cosmetic duplication only. Rather than a
  15-file mechanical rewrite (churn + regression risk for no behaviour change), the shared
  `normalize01()` is provided for incremental adoption. Existing working sites are left alone.
- **Finding 5 (stray prints):** the raw count (~241) was misleading — ~180 are the
  intentional `[PyCAT] …` status-logging convention, the rest are the startup banner, a
  standalone repair script, and batch-replay status messages. There is no meaningful
  stray-debug-print problem; no changes made.
- Findings 1–3 (latent stack frame-collapse bugs across six UIs; metadata-path
  diagnosability; missing measurement-correctness tests) were the substantive ones and
  shipped in 1.5.307–1.5.308.

## [1.5.308] - 2026-07-09
### Changed (diagnosability — health audit finding 2)
- **Silent metadata-extraction failures now leave a breadcrumb.** In
  `metadata_extract.py`, the `except: pass` blocks guarding the *downstream-critical*
  acquisition fields — pixel size, Z step, and the frame-interval paths (MicroManager
  ElapsedTime deltas + the OME fallback, on both the AICSImage and plain-TIFF routes) —
  now call `debug_log(...)` instead of swallowing silently. Behaviour is unchanged in
  normal use (still fails open to a usable partial record), but under `PYCAT_DEBUG=1` a
  failed extraction of a field the user relies on (e.g. frame interval feeding viscosity)
  now prints with a traceback instead of vanishing. Truly-optional fields (channel names,
  raw OME dump) are left quiet. This directly targets the class of bug that made the
  frame-interval and pixel-size issues hard to trace.

### Added (measurement-correctness tests — health audit finding 3)
- **Golden-master tests for the VPT microrheology chain** (`tests/test_vpt_viscosity_chain.py`).
  Synthetic 2D Brownian trajectories with a KNOWN diffusion coefficient are pushed through
  the full pipeline (`compute_msd` → `fit_anomalous_diffusion` → `viscosity_from_diffusion`),
  asserting it recovers D (to ~1%), a Brownian exponent α≈1, the exact Stokes-Einstein
  viscosity arithmetic, and the end-to-end viscosity (to ~3%), plus the NaN guards for
  non-positive inputs. This encodes the "the measurements are actually correct" claim as a
  deterministic regression test — and independently confirms the MSD/fit/viscosity *math*
  is sound, locating the real-data viscosity discrepancy upstream in linking, not here.

### Note
- Observed during testing: `vpt_tools` imports `napari.utils.notifications` at module top,
  so a pure-compute module can't be imported headless. Noted for a follow-up (move those to
  function-local imports, as other modules do) — not changed here to keep this focused.

## [1.5.307] - 2026-07-09
### Fixed (latent frame-collapse bug across stack-consuming analyses — health audit)
- **Six analysis UIs that read a time-series/stack via `np.asarray(layer.data)` now
  materialise it safely.** That raw pattern silently returns only frame 0 of a (T, H, W)
  stack when the layer holds one of PyCAT's lazy wrappers (whose `__array__` is
  deliberately truncated for napari) — the exact frame-collapse bug fixed twice this
  session in the temperature and VPT paths. A codebase audit found the same latent bug in
  **FRAP** (recovery + prebleach stacks), **condensate physics** (fusion mask + frame-QC
  stacks), **droplet fusion**, **in vitro brightfield** (dynamics + QC), **brightfield**
  (dynamics + QC), and **in vitro fluorescence** (dynamics label + image + QC). None had
  imported the safe helpers; each worked only because test data happened to load eagerly.
  All now route stack reads through `materialize_stack`, which reconstructs the full stack
  frame-by-frame when a wrapper truncates. Several of these feed physical-units results
  (FRAP recovery, viscosity/fusion) that would have been silently wrong on a lazily-loaded
  multi-frame file. Symptomatically, the old code could also reject a valid stack with
  "must be 3D" when the wrapper collapsed it to 2D.
- **`materialize_stack` / `as_full_array` now preserve the source dtype when `dtype=None`.**
  Previously they always built the output as float, which would silently float integer
  LABEL-MASK stacks. Passing `dtype=None` (used for the mask-stack reads above) now keeps
  the original integer dtype and label values intact, while the default `float32` behaviour
  is unchanged for image stacks.
- **Added `tests/test_materialize_stack.py`** — golden-master tests that assert the
  materialiser recovers a full stack from a truncating wrapper and preserves label-mask
  dtype (the first unit coverage of this critical path).

## [1.5.306] - 2026-07-09
### Added (Time Series In Vitro Fluorescence — 2D+t foundation)
- **New analysis method: Time Series In Vitro Object Analysis (Fluorescence).** The temporal
  counterpart of the 2D in vitro fluorescence pipeline. It segments every frame, LINKS
  droplets across frames into per-condensate temporal objects, and reports both per-object
  and whole-field time-series. New modules `timeseries_invitro_tools.py` (analysis) and
  `timeseries_invitro_fluor_ui.py` (stepped UI). Steps: load (time-series-gated) →
  per-frame preprocess → per-frame segment (Multi-Otsu/Otsu/watershed) → link
  (fusion-aware) → per-condensate trajectories → field trajectories.
- **Fusion-aware condensate linking.** Reuses the Bayesian/Hungarian linker but tuned for
  large, slow, irregular objects: a size-scaled search radius (a droplet moves at most a
  fraction of its own radius per frame), up-weighted area consistency, and velocity
  prediction OFF (condensates are not ballistic). A dedicated pass detects droplet FUSION
  events — where two tracks merge into one — and flags them (child + parent track ids)
  rather than silently mis-linking, since fusion is scientifically central here.
- **Per-condensate temporal object records.** Each tracked droplet becomes a durable object
  record carrying size/intensity/shape vs time plus a linear area-growth rate. These records
  are the foundation the planned specialised analyses (interior bubbling, catalysis kinetics,
  internal flow, fiber growth, contrast cascade — now on the roadmap) attach to.
- **Streaming segmentation with opt-in keyframing.** Frames are segmented one at a time
  (never materialising the whole movie). Multi-Otsu is cheap enough to run every frame
  (the default); a keyframe checkbox (with a caveat tooltip) exists only for exceptionally
  long stacks and copies masks between keyframes.
- **A tracked-label overlay** recolours each droplet by track id so one condensate keeps one
  colour through the movie.

### Fixed (2D in vitro fluorescence — time-series steps shown on plain 2D images)
- **The Dynamics/coarsening and Frame-Quality steps now hide correctly for non-time-series
  data.** They were gated on `data.ndim >= 3`, which is true for RGB `(H, W, 3)`, a singleton
  leading axis `(1, H, W)`, and channel/Z stacks — none of which are time series — so the
  steps stayed visible on plain 2D images. The gate now keys on a real temporal axis: the
  loaded file's `n_timepoints` metadata (captured at load) first, then a proper multi-frame
  stack test (new `_has_time_series` / `_layer_is_time_series` helpers). Validated against
  2D / RGB / singleton / real-stack shapes.

### Changed (menu naming)
- **"Time-Series Object Analysis" → "Time Series Cellular Object Analysis"** (it is the 2D+t
  cellular pipeline), and the new **"Time Series In Vitro Object Analysis (Fluorescence)"**
  added alongside it under Cell and Object Analyses.

## [1.5.305] - 2026-07-09
### Added (measured per-frame acquisition timing captured at load)
- **The real per-frame cadence is now read from MicroManager page tags.** A metadata
  audit of the VPT test file showed the previously-used interval was wrong: the nominal
  `Interval_ms` in the MicroManager summary is `0.0` (unset), and the OME
  `<Description>` free-text says "500ms interval" — but the camera actually ran at
  ~100 ms/frame. The authoritative source is the per-page `MicroManagerMetadata`
  `ElapsedTime-ms` timestamp on each frame. A new `_extract_mm_frame_times_from_tiff`
  reads those timestamps directly (via tifffile), computes the inter-frame deltas, and
  records the **median** frame interval, its **IQR**, and the **full per-frame delta
  array** into `file_metadata['common']`, along with **exposure**, **camera name**,
  **acquisition start time** and **frame count**. On the test data this correctly
  recovers ~0.1 s/frame.
- **Frame-interval precedence is now measured-first.** For a loaded file the interval is
  taken, most-authoritative first, from: (1) measured MicroManager `ElapsedTime-ms`
  deltas, (2) OME structured `TimeIncrement`, (3) OME per-plane `DeltaT` differences,
  (4) MicroManager `Interval_ms` **only if > 0**. Free-text OME `<Description>` is never
  parsed for timing. A zero `Interval_ms` is no longer reported as a real cadence.
- **The metadata panel now shows timing and provenance.** The File Metadata dialog
  displays Camera, Exposure (s), Frame interval (s) with its IQR, Frame interval source,
  and Z step in the curated view; the full measured per-frame deltas, frame count and
  acquisition start time appear under "Show all raw metadata". All of these are included
  in the JSON export.

### Note (correction to the 1.5.304 conclusion)
- The 1.5.304 entry stated the test data was "actually 0.5 s/frame". A thorough metadata
  dump disproved this — the measured cadence is ~0.1 s/frame (the value Step 5 originally
  defaulted to). The frame interval was therefore **not** the cause of the low VPT
  viscosity; that investigation continues on the MSD-magnitude side. The outlier-rejection
  work from 1.5.304 stands.

### To do (queued in the roadmap)
- Audit every method that consumes an acquisition parameter (frame interval, pixel size,
  exposure, Z step, bit depth) to confirm it derives the value correctly for the specific
  data type and reads from the single `file_metadata` source. Add a VPT toggle to feed the
  captured per-frame deltas into the MSD lag-time axis (currently captured/displayed only).

## [1.5.304] - 2026-07-09
### Fixed (VPT viscosity too low — outlier trajectories + frame interval from metadata)
- **Acquisition timing (frame interval) is now captured at load, in the top-level metadata, for all
  consumers.** The load-time metadata extraction now records frame_interval_s (and exposure, Z step)
  from OME TimeIncrement, per-plane DeltaT, or MicroManager Interval_ms, into
  data_repository[file_metadata]. VPT Step 5 reads it as the frame-interval default instead of a
  fixed 0.1 s (a wrong interval scales the diffusion coefficient and viscosity directly — the test
  data is actually 0.5 s/frame, so the old default was 5x off). Any timing-dependent analysis can now
  read the interval from one place. The user can still override it.
- **MSD now rejects outlier trajectories, matching the reference analysis workflow.** A movie yields
  many trajectories; spurious/mis-linked ones have anomalously high MSD and, when averaged in, inflate
  the ensemble MSD, inflate D, and deflate viscosity by a large factor. compute_msd now rejects tracks
  whose first- and last-lag per-track MSD fall outside a 1.5x IQR fence in log space (the reference
  notebook get_outlier_bounds method) before aggregating. On mixed good/spurious tracks this recovers
  the correct diffusion coefficient.
### Changed (VPT linking defaults — physically grounded)
- Max linking distance now defaults to about 2x the bead size (a bead should not move much more than
  its own diameter between frames in a viscous sample); max frame gap now defaults to 0 (do not bridge
  gaps — a bead that vanishes and reappears is more likely a broken/mis-linked track to prune than a
  real continuous one).
### Note
- The localization-offset MSD fit added in 1.5.302/1.5.303 is correct physics but was NOT the cause of
  the low viscosity on this dataset: the real MSD is a clean power law with no low-lag plateau, so the
  fitted offset is ~0 there. The dominant cause was outlier trajectories plus the wrong frame interval,
  both addressed here.

## [1.5.303] - 2026-07-09
### Changed (MSD localization offset bound matched to the reference notebook)
- **Tightened the MSD localization-offset bound to match the reference analysis notebook exactly.**
  1.5.302 added the offset term (MSD = 4*D*t^alpha + N) but bounded N loosely; it is now bounded to
  [0, min(MSD)] as in the reference notebook, since the constant offset cannot exceed the smallest
  measured MSD value. This makes PyCAT's viscosity fit reproduce the reference workflow result on
  viscous samples.

## [1.5.302] - 2026-07-08
### Fixed (viscosity far too low in viscous samples — MSD localization-error offset)
- **The MSD fit now separates static localization error from real diffusion, fixing viscosities
  that came out ~30x too low in viscous samples.** In a viscous medium a probe bead barely moves
  per frame, so the constant offset in the MSD from bead-centroid localization uncertainty (tens of
  nm) can dwarf the real time-dependent signal. The previous fit (MSD = 4·D·τ^α, no offset) absorbed
  that constant floor into D, inflating the diffusion coefficient and deflating Stokes-Einstein
  viscosity by a large factor (e.g. a true ~7 Pa·s condensate reading ~0.2 Pa·s). The fit is now
  MSD = 4·D·τ^α + 4·σ_loc², so D reflects only genuine motion; the fitted localization error is
  reported (nm) in the results table as a sanity check. This matches the reference analysis notebook (MSD = 4*D*t^alpha + N, with N the localization offset bounded to [0, min(MSD)]); the previous PyCAT fit omitted N, which is why a ~7 Pa*s condensate read ~0.2 Pa*s. The offset fit recovers the same D as before
  when the localization floor is negligible (fast / low-viscosity samples), so it is safe across the
  range. This also improves the other MSD-based workflows (in-vitro fluorescence/brightfield,
  condensate physics) which share the same fit.
### Fixed (VPT trajectory layers rendered at the wrong scale)
- Bead/Aggregate trajectory layers now inherit the bead image layer's spatial scale, so tracks and
  image share one coordinate frame and overlay 1:1 when a micron pixel size is set (previously the
  tracks rendered as a full-width streak beside a tiny image).

## [1.5.301] - 2026-07-08
### Fixed (VPT trajectory layers rendered at the wrong scale)
- **Bead/Aggregate trajectory layers now overlay the image correctly when a pixel size is set.**
  When a micron pixel size is applied (e.g. via the pixel-size gate at load), the image layer
  renders in micron world units (layer scale = µm/px) but the trajectory layers were added in pixel
  coordinates with no scale, so they rendered in a different coordinate frame — appearing as a
  full-width streak beside a tiny image. The trajectory layers now inherit the bead image layer's
  spatial scale, so tracks and image share one coordinate frame and overlay 1:1.

## [1.5.300] - 2026-07-08
### Changed (VPT detection: honest progress bar and runtime estimate)
- **The detection progress bar now advances during the actual detection work.** With CPU-parallel
  detection the expensive per-frame blob detection ran in a process pool that reported nothing, so
  the bar sat near 0 and then jumped when the cheap scoring loop ran. Progress is now emitted as
  each frame finishes in the pool (via as_completed), so the bar moves smoothly through the real
  work. Results are unchanged (still verified identical to serial).
- **The pre-run time estimate accounts for acceleration.** It previously always showed the serial
  worst case (about 13 minutes for 1000 frames) regardless of GPU or multi-core use. It now divides
  by the expected speedup (GPU if present, else the CPU worker count) and names which accelerator it
  assumes, so the estimate reflects what will actually happen.

## [1.5.299] - 2026-07-08
### Changed (VPT microrheology: never mix bead populations; viscosity reported first)
- **The three bead populations (green singlets / yellow out-of-plane / red aggregates) are never
  mixed, and microrheology runs on green singlets by default.** Previously out-of-plane (dim,
  defocused) beads were folded into the primary set by default, which fed the linker a large,
  low-quality population and produced many spurious short trajectories that biased the MSD and
  pulled the fitted viscosity far too low. A new Microrheology population selector (in the detection
  step) offers: Green (singlets, default), Yellow (out-of-plane) only — so the dim population can be
  checked on its own to see whether it gives a consistent viscosity, and Green + Yellow (combine)
  once that is confirmed. Aggregates (red) are always tracked as a separate readout and never enter
  the viscosity population, since their size would bias Stokes-Einstein. The old always-on
  keep-out-of-plane / route-aggregates checkboxes are replaced by this single explicit choice.
- **Viscosity is now reported first** (ahead of the diffusion coefficient) in both the results table
  and the completion message, since it is the headline quantity.

## [1.5.298] - 2026-07-08
### Fixed (Compute MSD hung / crashed on large track sets)
- **Microrheology (Compute MSD) could freeze or crash PyCAT on movies that produce many long
  tracks.** Three causes, all addressed. (1) Both the ensemble compute_msd and the per-track MSD
  curves built their displacements with an O(n²) Python double loop over frame pairs; this is now
  vectorised (gap-aware array shifts), numerically identical to before but far faster on long
  tracks. (2) The per-track MSD now samples LOG-SPACED lags instead of every integer lag — MSD is
  read on log-log axes, so this preserves the curve shape while computing and drawing far fewer
  points. (3) The MSD spaghetti plot now caps how many individual track lines it draws (a random
  sample of 400) instead of one matplotlib line per track, which by itself could freeze the UI with
  tens of thousands of tracks; the ensemble mean and the fitted diffusion result still use every
  track.

### Added (VPT: GPU-accelerated bead detection with automatic tiered selection)
- **Fast-mode bead detection now uses the GPU when one is available.** The expensive part of blob
  detection — the per-scale Laplacian-of-Gaussian convolutions — runs on the GPU (via CuPy), keeping
  the scale-space cube on-device; the peak finding then uses scikit-image's exact peak_local_max so
  results are bit-for-bit identical to the CPU detector. Detection now selects the best available
  path automatically: GPU if present, else the CPU process-pool (1.5.293), else plain serial.
- **A runtime equivalence guard protects correctness.** Before trusting the GPU for a whole stack,
  detection verifies the GPU detector reproduces the CPU detector on the first frame; if they
  disagree for any reason (driver/CuPy quirk) it silently falls back to the CPU path, so GPU
  acceleration can never make results wrong — only faster. Requires the optional gpu extra
  (cupy-cuda11x) and a CUDA device; without them the CPU paths are used unchanged.

## [1.5.297] - 2026-07-08
### Changed (VPT fast-mode classification: real out-of-plane bin, viscosity strictness, temporal stability)
- **Dim, out-of-focus detections now go to the out-of-plane (yellow) class instead of blinking as
  green singlets.** The fast-mode classifier never actually assigned the yellow class; dim spots
  became singlets (and flickered in/out as they crossed the match-quality floor). Dim detections
  (low amplitude or low SNR relative to the population) are now binned out_of_plane.
- **A classification strictness control (hidden under Show advanced detection options) makes the dim
  gate viscosity-aware.** The default (1.0) is tuned for viscous samples (~3 Pa·s and above), where
  beads move slowly and a dim spot is almost always out of focus. For less viscous / faster samples,
  where beads cross the focal plane quickly and a firm gate would wrongly bin real beads, the control
  can be lowered (opt-in). The parameters are not meant to be universal across all viscosities.
- **Stable dim tracks are promoted back to singlet after linking (temporal stability pass).** A dim
  detection that persists across many frames with few gaps is a real, faint, in-focus bead, not an
  out-of-focus blink; once tracks exist this is detectable, so such tracks are reclassified from
  yellow to singlet. Blinking dim tracks correctly remain yellow. Aggregates and normal singlets are
  untouched. (Blinking of the yellow population frame-to-frame is expected and acceptable.)

## [1.5.296] - 2026-07-08
### Fixed (VPT linking crashed in fast mode) + Changed (faster linker, real progress bar)
- **Linking failed with KeyError: sigma_mean when aggregates were routed to a secondary
  population in fast (template) detection mode.** aggregate_population_stats assumed the
  Gaussian-fit columns (sigma_mean, n_units_est) that only fit-mode detection produces; fast mode
  does not fit a Gaussian. The function now guards each fit-only column and reports the stats it can
  (aggregate counts and aggregated fraction) instead of raising. Both linkers work in fast mode
  again.
- **The trajectory linker cost matrix is vectorised**, replacing a double Python loop over every
  (track, detection) pair per frame with numpy broadcasting. On dense movies (hundreds of beads per
  frame across many frames) this removes the dominant runtime cost. The computed cost matrix is
  numerically identical to the previous loop (verified exact), so tracking results are unchanged.
- **The linking progress bar is now determinate.** It previously spun indefinitely, so there was no
  way to tell whether linking was progressing or stalled; it now advances per frame (0..n_frames)
  as the sequential linker moves through the movie.

## [1.5.295] - 2026-07-08
### Added / Fixed (context-aware multi-file OME-TIFF handling; stop per-frame companion warning)
- **Multi-file OME-TIFF sets (e.g. Micro-Manager MMStacks split across sibling files) are now
  resolved up front, lazily, without materialising the stack.** A new resolver reads the OME
  metadata to see which linked files it references and checks which are actually present on disk,
  then builds a global frame->(file, page) map spanning the present files. Two cases are handled:
  (1) all companions present -> frames are read across the linked files transparently; (2) some
  companions missing (a file copied out of its set) -> only the frames that physically exist are
  loaded, with a clear warning, instead of silently zero-filling absent planes. The single-file fast
  path is unchanged.
- **Fixed a warning that spammed the terminal once per frame during parallel bead detection.** The
  VPT parallel workers re-open the file per frame; on a multi-file OME set whose companions were
  missing, tifffile printed "OME series failed to read ... Missing data are zeroed" on every read
  (thousands of lines). Workers now read via the resolved page map (or, for single files, with the
  tifffile OME warning silenced) and match the serial reader frame-for-frame.

## [1.5.294] - 2026-07-08
### Fixed (batch replay ran steps on the wrong channel; Measure Line values were ignored)
- **Batch replay of the Condensate Analysis pipeline could run steps on the wrong image layer, and
  ignored the measurements made with the Measure Line tool.** Contributed by a user who diagnosed
  the failures. Three fixes: (1) replay now resolves the actual layer name the GUI recorded for each
  step (a new _resolve_image_layer helper) instead of assuming a fixed channel/stage — previously
  Cellpose could run on the foreground-suppressed segmentation channel instead of the fluorescence
  channel and segment 0 cells. It honours both which channel (segmentation / fluorescence / a named
  extra channel from 3+ fluorophore files) and which stage (raw vs preprocessed / background-
  removed) the recorded name encodes. (2) The Measure Line step now applies the recorded
  cell_diameter / ball_radius / object_size instead of being a no-op; leaving the stale open_image
  ball_radius in place had, after upscaling, produced an oversized rolling-ball element and a
  MemoryError in condensate segmentation, and gave Cellpose the wrong cell diameter. (3) Replay now
  skips cell/condensate analysis gracefully (with an explanatory message) when segmentation yields
  0 cells or no puncta, instead of crashing inside pandas. Preprocessing and background removal also
  now act only on the layer that was active when the step was recorded, matching the interactive
  tool. Non-condensate replay paths (time-series, brightfield, in-vitro) are unchanged.

## [1.5.293] - 2026-07-08
### Added (VPT: CPU-parallel bead detection)
- **Fast-mode bead detection now runs across a process pool when possible**, cutting the time to
  reach the tracking step on multi-core machines. Per-frame blob detection (the expensive,
  embarrassingly-parallel part) is dispatched to worker processes that re-open the source file and
  read their own frame; template building, scoring and classification stay in the main process. It
  activates automatically (parallel=auto) for fast mode on a file-backed stack with more than one
  frame and more than one worker, and falls back cleanly to serial detection for anything else (a
  non-file-backed stack, a single frame, or any worker error). Results are unchanged: a regression
  test (tests/test_vpt_parallel_equivalence.py) asserts the parallel path produces bead coordinates
  identical to serial on every frame. GPU-accelerated detection is a separate, later addition built
  on this foundation.

## [1.5.292] - 2026-07-08
### Changed (restrict to Python 3.12 to prevent accidental 3.13 installs)
- **requires-python tightened to >=3.12,<3.13**, and the 3.13 classifier and the contradictory
  "supported range 3.12-3.13" README note were corrected to 3.12-only. Some users following the
  install steps ended up on Python 3.13, which is not yet validated against the native dependency
  stack (PyQt5/torch/numba/cellpose on arm64) and contributed to launch instability. pip will now
  refuse to install on 3.13 with a clear version error instead of installing and crashing. 3.13
  support can be re-enabled after deliberate testing.

## [1.5.291] - 2026-07-08
### Fixed (launch segfault on Apple Silicon / arm64 Macs)
- **Multiple native arm64 (Apple Silicon) macOS users hit a segmentation fault at launch**, right
  after startup finished (the OMP: Info #276 omp_set_nested banner printed first). This is the
  signature of a duplicate OpenMP runtime: PyTorch, Numba, MKL and Cellpose can each load their own
  copy of libomp, and on arm64 two copies in one process can abort at the C level. Two mitigations,
  both applied before any native library is imported: (1) KMP_DUPLICATE_LIB_OK=TRUE (plus capped
  OMP thread counts) so the OpenMP runtime tolerates the duplicate instead of crashing; these are
  no-ops on machines without the conflict, so they are safe everywhere. (2) On macOS the background
  startup thread no longer imports PyTorch — that import raced with Qt/CentralManager initialising
  on the main thread (a concurrent native-init crash the code already warned about), and the check
  it performed (CUDA availability) is meaningless on Apple Silicon anyway. Torch now loads on first
  actual use instead of during launch.

## [1.5.290] - 2026-07-08
### Fixed (VPT bead classification flickered frame-to-frame: aggregates and dim detections)
- **A large aggregate alternated between the aggregate (red) and singlet (green) class across
  frames, and very dim detections dropped in and out.** Both were frame-to-frame instability from
  classification thresholds sitting right where borderline objects live. Two fixes, validated on
  test data: (1) the aggregate mass gate moved from the 99.5th to the 99.3rd percentile — the 99.5
  cut landed inside the top mass cluster, so a genuine aggregate whose mass fluctuates a few percent
  kept crossing it; 99.3 sits just below that cluster, so the true aggregates stay above it every
  frame. (2) the template-match (NCC) floor moved from 0.50 to 0.55 — dim detections whose match
  score hovered at 0.50 were flipping between kept and rejected each frame; the firmer floor keeps
  that borderline-noise population consistently out. Aggregate classification is now stable across
  frames and the dim in/out flicker is roughly halved. NOTE: a bead whose intensity GENUINELY
  crosses the threshold in a given frame can still change class there; fully eliminating that needs
  temporal consistency (holding an object's class once it is tracked), a separate future refinement.

## [1.5.289] - 2026-07-08
### Fixed (in-dock pixel-size gate now hides after the scale is set via the load-time popup)
- **Setting the pixel size in the load-time popup did not hide an already-open method panel's
  in-dock pixel-size gate.** The popup and the gate share the same scale value, but two links were
  missing: the popup did not notify the gate to re-check after writing the scale, and the gate only
  hid on a scale it had confirmed itself or one from metadata — not on a valid scale set elsewhere.
  The popup now fires the data-changed notification after setting the scale, and the gate hides
  whenever the repository holds a valid scale that was set externally (its own field still empty),
  while still not hiding mid-typing in the dock field. Popup and dock gate now stay consistent.

## [1.5.288] - 2026-07-08
### Added (VPT: physical bead size, Airy-model template, and ring/multi-scale de-duplication)
- **Bead detection can now use the physical bead size and merge duplicate detections.** Large
  (non-diffraction-limited) beads image as an Airy disk and can trigger several detections on one
  bead — at multiple blob scales, or on the Airy ring. Three additions address this. (1) A **bead
  size** input (nm, default 200) is converted to pixels via the loaded pixel size to set the
  detection template patch size and the de-duplication radius. (2) A **de-duplication** step (on by
  default) merges detections that fall within about one bead radius, keeping the brightest (the bead
  centre), so one bead yields one point. (3) A **template type** selector offers the empirical PSF
  (measured from the data, default) or an **Airy model** (analytic Bessel J₁ disk + ring) for data
  where beads show a resolved ring, so the bead matches as a single object rather than the ring
  being detected separately. On test data whose beads showed no resolved ring the empirical template
  remains the better fit; the Airy option is for scopes/beads that do show rings.
### Changed
- Pixel-size load dialog: darkened the explanatory text for readability.

## [1.5.287] - 2026-07-08
### Added (dedicated load-time pixel-size dialog, separate from the in-dock gate)
- **A modal pixel-size dialog now appears on load when an image has no scale in its metadata.** This
  replaces the earlier attempt to make the in-dock gate double as a popup (which flickered as a
  transient window). The dialog is a proper top-level modal — no embedding/parenting subtleties —
  and appears only for the hygiene case (pixel size fell back to 1.0 and did not come from
  metadata). It includes a short explanation of why pixel size matters (it sets the physical scale
  for all downstream measurements — sizes, distances, diffusion, viscosity), an input field, and
  Set/Skip buttons. Skip leaves the scale unset (the in-dock gate still lets the user set it later).
  The dialog writes the same data_repository scale the in-dock gate reads, so the two stay
  consistent. The in-dock gate no longer refreshes at construction time (which caused its own
  pre-dock flicker); it now updates only on real triggers (data switch, post-load notify) once it
  is docked.

## [1.5.286] - 2026-07-08
### Fixed (removed a transient flickering pixel-size window)
- **A pixel-size window briefly flickered and vanished on load.** The gate de-duplication
  coordinator added in 1.5.283 iterated a global registry that still held gates from previously-
  removed analysis panels; briefly toggling one of those stale, unparented gates visible produced a
  flash of a window that immediately disappeared. The coordinator turned out to be unnecessary: the
  real cause of the earlier duplicate/floating windows was that the gate was not embedded in its
  panel layout (fixed in 1.5.283), and PyCAT only ever docks one analysis panel at a time, so there
  is only ever one gate. The coordinator has been removed and each gate simply shows/hides itself
  within its own panel. The in-panel gate still appears correctly when a scale is needed; the stray
  flickering window is gone.

## [1.5.285] - 2026-07-08
### Fixed (pixel-size gate never appeared — missing show signal in the refactored coordinator)
- **The gate coordinator introduced in 1.5.283 was missing the call that marks a gate as wanting to
  be shown.** When the gate visibility logic was refactored to route through the shared coordinator,
  the hide branches were updated but the visible branch never set its want-to-show flag, so the
  coordinator always saw zero gates wanting display and the gate never appeared on load or method
  selection. Added the missing signal in the visible branch. The gate now appears when a scale is
  needed, still as a single embedded panel (no floating or duplicate windows).

## [1.5.284] - 2026-07-08
### Fixed (pixel-size gate stopped appearing after the 1.5.283 de-duplication change)
- **The 1.5.283 gate coordinator was too strict and suppressed the gate entirely.** It only showed
  a gate whose widget already reported a parent, but that check could be false at the moment the
  gate re-evaluated (before the panel finished attaching), so the gate never appeared on load or
  method selection. The floating-window problem is already prevented by embedding the gate in its
  panel layout (added in 1.5.283), so the extra parent check was redundant and has been removed. The
  coordinator now simply shows the first gate that needs a scale and hides the rest — restoring the
  gate on load while keeping single-window, no-orphan behaviour.

## [1.5.283] - 2026-07-08
### Fixed (pixel-size gate floated as separate windows / persisted after close; auto-clear now covers stacks)
- **The pixel-size gate appeared as one or more floating windows that persisted even after the PyCAT
  GUI was closed.** Root cause: the gate group box was never added to its panel layout, so Qt
  rendered the parentless widget as a top-level window. Because each analysis panel builds its own
  gate, several could float at once (the "3 windows"), and being unparented they outlived the main
  window. The gate is now embedded in its panel layout and starts hidden. A shared coordinator
  ensures at most one gate is visible at a time and never shows a gate that lacks a parent window,
  so duplicates and orphan windows can no longer occur.
- **Auto-clear on load now also applies to T/Z and multi-dimensional stacks.** The previous release
  reset existing layers before loading a new 2-D image, but the stack loader (open_stack, used for
  time-series / z-stack / OME-TIFF / Imaris) did not, so loading a stack over an existing image
  still produced the confusing frame-count overlap. The same confirm-then-reset now runs for stack
  loads.

## [1.5.282] - 2026-07-08
### Fixed (pixel-size gate now appears after loading an image whose metadata lacks a scale)
- **The pixel-size gate stopped appearing after a file load, which could let analyses run with the
  fallback scale of 1.0 um/px.** The gate re-evaluates its visibility only when notified of a data
  change, and those notifications previously fired only on an active-data-class SWITCH (e.g.
  changing analysis method) — not on a plain image load. So opening a file whose metadata has no
  pixel size (which falls back to 1.0 and should prompt the user) left the gate in its pre-load
  state and it never appeared. This matters because the pixel size feeds physical-unit conversions
  (e.g. VPT microrheology viscosity via Stokes-Einstein): a silent 1.0 fallback yields wrong-scaled
  results. Added CentralManager.notify_data_changed(), which fires the registered gate callbacks
  without switching the data class, and called it at the end of every image-load path (2-D images
  and T/Z/multi-dimensional stacks, including OME-TIFF and Imaris) once the freshly-loaded pixel
  size is in the data repository. The gate now correctly appears for files without a real scale and
  stays hidden for files whose metadata supplies one. Mask loading is unaffected.

## [1.5.281] - 2026-07-08
### Fixed (loading a new image now clears the previous one first, avoiding confusing overlaps)
- **Loading a new image while a previous one was still present caused confusing display behaviour.**
  For example, loading a 300-frame stack over an existing 1000-frame stack made the new image look
  like it had failed to load: the frame slider still spanned 1000 frames, and scrubbing past frame
  300 showed the old layer (or nothing) because the new image had no data there. Opening an image
  now resets to the workflow start state BEFORE the new dataset is added, so it loads clean. If
  existing layers are present (potentially-unsaved work), a confirmation prompt appears first
  — matching the Clear button's safety behaviour — so analysis is never discarded silently. The
  reset reuses the same _clear_everything logic the Clear button uses (layers, data repository,
  dataframes, workflow checklist, and batch recording). Mask loading is unaffected (masks are meant
  to overlay the current image, so they still add rather than replace).

## [1.5.280] - 2026-07-08
### Fixed (VPT fast-mode bead classification recalibrated for Airy-disk beads; garbage now rejected)
- **Fast-mode bead classification was mismapped for large (non-diffraction-limited) beads, and never
  rejected non-bead detections.** Two problems, both fixed. (1) The singlet-vs-aggregate split was
  drawn on brightness, which is inverted for 200 nm-2 um beads that image as bright Airy disks: a
  real single bead is bright and high-mass, so it was wrongly called an aggregate (the previous
  logic labelled ~220 aggregates per frame when only ~2 exist). Aggregate now requires a bead to be
  BOTH high-mass (top ~0.5% tail) AND bright/compact, which matches a hand-labelled ground-truth
  frame (~2-3 aggregates/frame, the large majority singlets). Dim-but-large out-of-focus blobs are
  now flagged as a distinct "ambiguous" class (blue) rather than forced into singlet/aggregate,
  since they cannot be confidently classified. (2) Detections that poorly match the empirical PSF
  template (low normalised cross-correlation) — Airy-ring fragments, hot pixels, and noise — are now
  REJECTED (dropped) instead of being labelled and displayed, so a marked point is a real bead.
  Aggregate and ambiguous beads are routed to the secondary population (kept out of the primary
  microrheology set, since their size/uncertainty would bias Stokes-Einstein viscosity) rather than
  dropped. NOTE: Airy-ring de-duplication (merging multiple detections around one bead) is a further
  planned refinement; this release rejects poorly-matched ring fragments but does not yet merge
  well-matched ones.

## [1.5.279] - 2026-07-08
### Fixed (VPT Link Trajectories crashed with AttributeError: no _fit_quality)
- **Both VPT trajectory linkers (TrackMate LAP and Bayesian/Hungarian) raised
  AttributeError: 'VideoParticleTrackingUI' object has no attribute '_fit_quality'.** The
  _fit_quality checkbox was replaced by the detection-mode dropdown in 1.5.277, but the linking
  step (_on_link) still referenced it when deciding whether to route aggregates to a secondary
  population. Since all detection modes now classify beads (a bead_class column is always
  produced), the stale _fit_quality guard was removed — aggregate routing is now gated solely on
  the "Route aggregates" checkbox and the presence of the bead_class column. Detection itself was
  unaffected (fast-mode detection ran correctly end-to-end); this only blocked the linking step.

## [1.5.278] - 2026-07-07
### Fixed (VPT Detect Beads crashed opening the long-run warning dialog)
- **The >2-minute detection warning dialog raised a TypeError and aborted Detect Beads.** The
  QMessageBox.question() call passed the VPT UI object as its parent, but that class is a plain
  controller (not a QWidget), which PyQt5 rejects. Fixed by passing None as the dialog parent
  (matching the established pattern elsewhere in the codebase). Detect Beads now runs; the warning
  appears correctly before long (precise-fit) runs.

## [1.5.277] - 2026-07-07
### Changed (VPT bead detection is far faster: fast template mode by default, with a visible progress bar)
- **VPT bead detection now defaults to a fast empirical-PSF template method instead of a per-bead
  Gaussian fit, cutting a long-movie run from hours to minutes, and the progress bar now actually
  moves.** The old default fit a 2D Gaussian to every bead in every frame (bounded curve_fit,
  maxfev=10000): on a ~1000-frame movie with ~800 beads/frame that is ~3 hours and looked frozen.
  Detection now offers three modes: "Fast (template match)" (default) builds one empirical PSF
  template from the cleanest beads and scores every bead by normalised cross-correlation + peak SNR
  + radial symmetry (~microseconds/bead), giving the overlay and singlet/aggregate/out-of-plane
  classification in ~10-15x less time; "Fast fit" runs a bounded Gaussian with a tight iteration
  cap; "Precise fit" is the full Gaussian for when localisation precision matters. Added a
  "Sub-pixel centres" toggle (cheap intensity-centroid refinement) and a "Rebuild PSF template per
  frame" option (adapts to focus drift / SMLM-like data; default builds one template per stack).
  The progress bar is now determinate (0..n_frames) and advances per frame instead of sitting as an
  indeterminate spinner, and a confirmation warning appears before runs estimated to exceed ~2
  minutes. New template functions build_bead_template()/score_beads_template() and a fast= option on
  fit_gaussian_2d_spot(). (Further speedup via across-frame parallelism is planned separately.)

## [1.5.276] - 2026-07-07
### Changed (VPT bead detection now streams frame-by-frame; host inference uses keyframes)
- **VPT bead detection no longer materialises the whole movie in memory, and "Infer Host from
  Beads" no longer processes every frame.** Two related performance fixes: (1) detect_beads_stack
  now STREAMS frames one at a time via a new iter_frames() helper, reading each frame from the lazy
  layer on demand instead of building a full (T, H, W) array up front. Memory stays flat regardless
  of movie length, and the earlier frame-0-collapse class of bug is now impossible by construction
  (frames are indexed individually, never via np.asarray on the whole wrapper). (2) Host inference
  (Mode C) previously ran blob detection on every frame just to build a bead-density map, which on
  a long movie (e.g. ~1000 frames) took minutes and froze the UI. Because the host is treated as
  stationary, it now samples up to 8 evenly-spaced keyframes — empirically this reproduces the
  all-frames inferred host to within a few percent IoU while cutting the work by ~100x. Added a
  frame_indices parameter to detect_beads_stack for keyframe subsetting (original frame indices are
  preserved in the output). New iter_frames() streaming helper in file_io alongside
  materialize_stack().

## [1.5.275] - 2026-07-07
### Added (VPT can infer an unlabelled host condensate from the bead distribution — Mode C)
- **New "Infer from beads" host mode for VPT.** When the condensate is real but unlabelled (no
  companion host channel), PyCAT can now synthesise a host mask from where the beads are. The
  method combines three stages: (1) a bead-density map is thresholded and split with a
  distance-transform watershed, so touching condensates are separated rather than merged; (2) each
  region is validated by its internal bead content; and (3) a physical size gate keeps only
  condensates large enough for beads to sample bulk (boundary-free) diffusion — beads in a
  condensate that is too shallow feel the interface and do not report bulk viscosity, so small
  condensates are discarded. Condensates clipped by the frame edge have their true radius estimated
  by fitting a circle to their visible (non-border) interface arc, so a large condensate that is
  only partly in frame is still retained. The minimum condensate radius is user-adjustable (µm,
  physically grounded, default 5 µm). The result is reported explicitly as an INFERRED boundary
  (it follows the bead distribution, not a directly imaged condensate edge). This method was
  selected by comparing it against a bead-geometry/clustering alternative on real data with a
  hand-annotated boundary: the density+watershed+physics approach recovered the annotated central
  condensate substantially better (IoU ~0.73 vs ~0.50) and, unlike proximity clustering, did not
  collapse neighbouring condensates into one region. Exposed as new tools infer_host_from_beads()
  in vpt_tools and an "Infer Host from Beads" action in the Step 2 panel.

## [1.5.274] - 2026-07-07
### Added (VPT host-condensate segmentation is now optional — no-host / full-frame mode)
- **VPT no longer requires a companion host-condensate channel.** Not all microrheology data has a
  separate channel that labels the host phase — for example beads-in-glycerol viscosity controls,
  or any bulk-medium experiment with no condensate boundary at all. Previously the bead-detection
  step hard-blocked with "run Step 2 first" whenever no host mask was present, making these
  datasets impossible to analyse. Step 2 now offers a Host mode selector: "Host channel" (the
  existing behaviour, default), "No host (full frame)" (skip host masking and track every bead
  across the whole field), and "Infer from beads" (reserved for a future release, disabled for
  now). In no-host mode the host-segmentation controls are greyed out and bead detection proceeds
  with no inclusion mask. The end-to-end run_vpt_analysis() helper likewise accepts host_image=None
  and skips host segmentation. The underlying detection already treated a missing host mask as
  "keep all beads", so this change is purely about exposing that path in the UI. Validated on a
  real 5-frame bead substack: all ~780-820 beads per frame are detected and tracked across the full
  field with no host channel. (Full-frame control support also enables pipeline self-validation:
  beads in a known-viscosity medium like glycerol are how VPT microrheology is calibrated.)

## [1.5.273] - 2026-07-07
### Fixed (VPT particle tracking only saw the first frame of a time-series)
- **The VPT (video particle tracking) pipeline silently collapsed any multi-frame time-series to
  its FIRST frame, so bead detection, track linking, and the scroll-through detection overlay all
  behaved as if the movie were a single image.** The bead-detection step loaded the stack with
  `np.asarray(layer.data)`, but for an OME/ImageJ TIFF time-series PyCAT wraps the data in a lazy
  `_TiffPageStack` whose `__array__` deliberately returns only frame 0 (to keep napari’s incidental
  array requests cheap). So `detect_beads_stack` received a single 2D frame, detected beads on
  frame 0 only, and produced no linkable trajectories — and the red/yellow/green "Bead Detections"
  points layer only had frame-0 points, so it appeared correct on the first frame but went empty
  when scrolling. Fixed by loading the bead stack with `materialize_stack()` (the same helper used
  by the temperature workflow), which reads every frame into a real (T, H, W) array and passes
  plain arrays through unchanged. Validated on a user-provided 5-frame Blackfly uint8 substack:
  ~780-820 beads detected per frame across all frames, tracks link across the full stack, and the
  detection overlay now updates correctly as the user scrolls through time. (uint8 input is handled
  correctly — detection min-max normalizes per frame, so bit depth does not affect thresholds.)

## [1.5.272] - 2026-07-07
### Fixed (object-based colocalization could report impossible overlap values > 1.0)
- **The object-based colocalization coefficients (Manders M1/M2, Jaccard, Sørensen-Dice) could
  return values greater than 1.0 and were biased by arbitrary object-ID numbers.** These
  coefficients are only valid on boolean (0/1) masks, but the two object masks were passed in as
  LABEL maps (object 1 = 1, object 2 = 2, ...). The overlap math (`sum(mask1 * mask2) /
  sum(mask1)`, etc.) therefore multiplied and summed label VALUES, so an object labelled 3
  counted three times as much as one labelled 1 — producing overlaps above 1.0 (impossible for a
  fraction) and making identical experiments disagree purely because objects were numbered
  differently. Fixed by binarising both object masks (`mask > 0`) at the start of each of the four
  overlap functions, before the ROI is applied. Verified on the reviewer’s worked example: the
  buggy path gave M1 = 1.5 / Dice = 1.2; the fixed path gives the correct M1 = 0.5 / Dice = 0.667.
  The object-distance analysis is deliberately left untouched: it re-labels internally
  (`skimage.measure.label`) and legitimately needs the label maps, so binarising only the overlap
  steps fixes the coefficients without breaking distance measurements. (Reported in an independent
  scientific/code review, Finding 1.)
- Also includes the deferred one-time "Loading Cellpose model weights from cache into memory"
  log breadcrumb that had been documented under 1.5.267 but not fully committed.

## [1.5.270] - 2026-07-07
### Docs (roadmap: biological object model & linked multiscale navigation)
- **Added a roadmap section capturing concepts from cross-evaluating the NimbusImage paper**
  (Nat. Methods 2025), a cloud-first petabyte-scale platform. Conclusion: don’t adopt the cloud/
  data-movement architecture (PyCAT’s data-local, interactive, quantitative philosophy is a
  deliberate strength), but extract three converging concepts: (1) formalize the implicit analysis
  hierarchy (Image→Cell→Organelle→Condensate→Punctum) that already exists via the cell/puncta
  parentage; (2) linked multiscale navigation — bidirectional brushing between plots and image
  layers so selecting a data point jumps to that object in the viewer and vice versa (the identity
  links already exist; the interactive bridge does not); (3) context-aware analysis that inherits
  spatial hierarchy. These unify into an internal biological object model where each object carries
  scale, persistence/topology, material state, neighborhood, and parentage — quantities PyCAT
  already computes in separate modules but never assembles onto one entity. In this model, each
  object carries a standardized record (geometry, intensity, scale-space signature, topology,
  material state, spatial relationships, QC, provenance, parent/child), and the existing modules
  (QC, benchmarking, spatial stats, DoH, FRAP, MSD, future FISH) become views of one object rather
  than isolated analyses — moving PyCAT toward a "scientific operating system for microscopy".
  Verified against the codebase. Documentation only.
- **Added a roadmap section capturing a reproducibility/measurement-reliability cluster** from
  cross-evaluating a Nature Methods 2025 reproducibility paper (strongly on-thesis with PyCAT's
  QC/rigor direction). Six related items, each verified against existing foundations: (1) feature
  provenance (elevate the existing batch step-recording to per-feature traceability; reinforces the
  provenance DAG); (2) per-measurement parameter-stability reporting (extends the existing
  benchmark parameter-sweep from masks to derived measurements); (3) a general measurement-
  confidence score combining QC + segmentation + benchmarking; (4) a standing per-release PyCAT
  Validation Suite (built on the existing tests/ fixtures + benchmark harness); (5) a measurement
  ontology (definition/equation/units/reference registry that makes Methods generation nearly
  automatic); and (6) automatic metadata + software-version travel on every output table. These
  converge on a unifying Measurement Reliability Index (MRI): every reported value carries a
  reliability score with a clickable explanation of why it's high or low. Documentation only.
- **Added a roadmap section from cross-evaluating a Cell Painting / image-based profiling review**
  (Nature Methods 2024). Conclusion: don't adopt its measure-everything → ML → latent-space
  direction (against PyCAT's hypothesis → mechanism → physics philosophy); several concepts restate
  the biological object model (state vectors, feature families, object hierarchy = the profiling
  view of it). Genuinely new items captured: feature-family grouping of outputs (currently flat
  columns); a biological-QC layer flagging biological outliers (edge cells, oversegmentation, dead/
  mitotic cells) as a second layer beyond imaging QC; correlation-based feature-redundancy
  reporting; a unified workflow-level analysis-preset system; the "structural profiling" reframe for
  the DoH/FISH work (complementary to phenotypic profiling); and a Feature Explorer — an interactive
  measurement browser (interpretation, definition, units, range, sensitivity, correlations, example
  images) that unifies the measurement ontology, feature stability, redundancy, and QC gallery into
  one interface. Also frames PyCAT's shift from image-analysis package to measurement platform.
  Documentation only.

## [1.5.269] - 2026-07-07
### Docs (roadmap: calibrated thermodynamic & quantitative condensate reporting)
- **Added a roadmap section capturing five capabilities identified by cross-evaluating PyCAT
  against the Punctatools pipeline** (verified against the codebase, not taken at face value).
  Conclusion: don’t adopt the pipeline (PyCAT is already broader), but add: (1) a calibration-
  curve manager converting fluorescence intensity to molar concentration plus real-unit Kp and
  ΔG_transfer = −RT ln(Kp) — the flagship, turning PyCAT into a biophysical-parameter-extraction
  tool; (2) a consolidated per-cell Condensate Thermodynamics Report export preset; (3) explicit
  2D / 3D-z-stack / time-series condensate modes (the in-vitro workflow already flags its volume
  fraction as a 2D-projection proxy); (4) a background-mode UI selector surfacing the scalar /
  mask / local-background support the backend already has; and (5) a positive/negative-control
  validation workflow extending the existing benchmark harness. The stale "integrate PunctaTools"
  note was updated to "adopt the concepts, not the pipeline."

## [1.5.268] - 2026-07-07
### Fixed (macOS startup segfault: torch/Numba warmup raced Qt init)
- **PyCAT could segfault on launch right after "Running PyCAT"** (seen on Apple Silicon macOS),
  after a clean, correct install (native arm64, torch 2.2.x, Cellpose cached successfully). The
  crash was a native-library race: a background thread imported torch and ran Numba JIT warmup at
  the same moment napari/Qt was initialising on the main thread, and those native libraries are
  not safe to initialise concurrently on macOS. Fixed by creating the napari viewer FIRST on the
  main thread, then starting the warmup thread only after Qt has finished its main-thread setup.
  Also added a `PYCAT_SKIP_WARMUP=1` environment variable to disable the background warmup
  entirely as an escape hatch. Note: this is the most-likely fix based on the crash signature;
  confirm on the affected machine.

## [1.5.267] - 2026-07-07
### Changed (clearer Cellpose cache messaging — it was never re-downloading)
- **Reworded the Cellpose model messages so it is obvious the model is downloaded only once and
  cached persistently.** The model was already cached on disk (`~/.cellpose/models`) and reused
  across launches — but the terminal wording ("skipping download", "downloading now") made it look
  like it might re-download every time. Now: a cache hit says the model was found locally and no
  download is needed; a cache miss says the download is a ONE-TIME setup saved for all future
  launches; and the post-download message confirms it won’t happen again. Added a distinct
  "Loading Cellpose model weights from cache into memory (first use this session)" breadcrumb when
  the model is actually loaded during segmentation, so loading-from-disk is clearly separate from
  downloading. No functional change to caching — messaging only.

## [1.5.266] - 2026-07-07
### Fixed (arm-mac install failed with ResolutionImpossible on Python 3.12)
- **`pip install "pycat-napari[arm-mac]"` failed with `ResolutionImpossible` / "no matching
  distribution for torch" on Apple Silicon.** The `[arm-mac]` extra pinned `torch==2.1.2`, but
  torch 2.1.2 has no Python 3.12 wheel (torch added cp312 support in 2.2.0) — while PyCAT itself
  requires Python >=3.12. So the exact pin could never resolve on any supported Python: pip found
  no installable torch and aborted. Changed the pin to `torch>=2.2.0,<2.3.0` (has cp312 arm64
  wheels, and stays within the torch range compatible with the `numpy<2.0` pin). This was a
  packaging bug, not a user error — affected users were on correct native-arm64 Python 3.12
  environments. Surfaced during a multi-user install test.

## [1.5.265] - 2026-07-07
### Changed (Cellpose prewarm: keep it, but guard against the environment that crashes)
- **The Cellpose prewarm is preserved (good first-run UX) but now skips itself only in the
  specific broken state that caused the segfault** — x86_64 Python running under Rosetta
  emulation on an Apple Silicon Mac — rather than being removed. A new architecture guard checks
  `sysctl.proc_translated` (Rosetta flag) and `hw.optional.arm64` vs `platform.machine()`; when a
  mismatch is detected it skips the prewarm with a clear message pointing the user to a native
  arm64 environment. On every healthy environment (native arm64 Mac, genuine Intel Mac, Windows,
  Linux) the prewarm runs as before.
- **The prewarm now selects the model via PyCAT’s version-aware builder**
  (`_build_cellpose_model(default_cellpose_model())`) instead of a hardcoded
  `pretrained_model='cyto2'`. This matters: on Cellpose <4 (the pinned default, fast `cyto2` CNN)
  the correct API is `model_type`, while `pretrained_model` is only a legacy fallback — so the old
  prewarm was using the wrong API path for the common case. The cache-existence check is now
  version-aware too (`cyto2` on Cellpose <4, `cpsam` on >=4), so it no longer always re-downloads
  on Cellpose 4. The subprocess isolation from 1.5.262 is retained as a second safety net for any
  other native crash (e.g. an older CPU without AVX).

## [1.5.264] - 2026-07-07
### Docs (Miniforge installer: tell Mac/Linux users how to run the .sh file)
- **The install steps now explain how to actually run the Miniforge installer per platform.**
  The conda-forge download page hands macOS/Linux users a `.sh` script (e.g.
  `Miniforge3-MacOSX-arm64.sh`), which non-technical users did not know what to do with —
  double-clicking a `.sh` does not run it. Step 2 (README) and a new "Installing Miniforge"
  subsection (installation.rst) now cover: Windows → double-click the `.exe`; macOS/Linux → open
  Terminal and run `bash <path-to-.sh>` (with the tip to drag the file from Finder into the
  Terminal to fill in the path), follow the prompts, then open a fresh terminal. Surfaced during
  the multi-user install test.

## [1.5.263] - 2026-07-07
### Docs (captured multi-user install-test debugging: Mac architecture + failure modes)
- **Added a Mac architecture check and troubleshooting for the issues surfaced during a group
  install test** (README + installation.rst). Key addition: on Apple Silicon, check
  `python -c "import platform; print(platform.machine())"` returns `arm64` (not `x86_64`) before
  installing — an `x86_64` result means Python is the Intel build under Rosetta emulation, which
  causes Intel MKL warnings and Cellpose segfaults. Notes that `uname -m` is unreliable here (it
  can report `arm64` while Python is x86). Also documented, with causes and fixes: the
  Homebrew-conda `libarchive.19.dylib` solver error (use Miniforge), the `llvmlite needs CMake
  tools to build` failure (install llvmlite/numba from conda-forge first), and the "every version
  rejected" symptom (wrong Python version). Updated the platform-support table with real test
  results (Intel Mac now Tested/Works; Apple Silicon note points to the native-arm64 guidance).

## [1.5.262] - 2026-07-07
### Fixed (Cellpose prewarm could segfault the whole app at startup)
- **PyCAT could crash to desktop on launch with a segmentation fault while pre-caching the
  Cellpose model** (`Cellpose model not found in cache ... zsh: segmentation fault`). Loading
  Cellpose pulls in PyTorch / native math libraries that can crash at the C level on some
  machines — notably older Intel CPUs without AVX, where the default AVX-assuming PyTorch/MKL
  binaries hit an unsupported instruction. A C-level crash is not a Python exception, so the
  existing try/except could not catch it, and because the prewarm runs before the QApplication is
  created, the whole app died before the GUI opened. The model load now runs in a SEPARATE
  SUBPROCESS, so a native crash only kills that subprocess — PyCAT still launches. On a
  signal-kill (e.g. SIGSEGV) a clear message explains the likely cause (incompatible PyTorch for
  this CPU) and notes the other segmentation methods (Multi-Otsu, StarDist, Random Forest) still
  work, with a pointer to `conda install -c conda-forge pytorch nomkl`. Known limitation: this
  makes startup crash-proof; if Cellpose is CPU-incompatible, clicking "Run Cellpose" in the GUI
  can still crash (in-process) — isolating that path is a follow-up.
### Fixed (PyCAT branding could silently fall back to napari’s on some installs)
- **The app icon and napari welcome-logo replacement could silently no-op**, leaving napari’s
  default branding — reported across multiple Macs. Both captured a path inside an
  `importlib.resources.as_file()` block but used it after the block exited; as_file() may delete
  its extracted temp file on exit (zipped installs), so the path could be invalid when Qt used it
  — especially the welcome logo, whose QSS `image: url(...)` is read lazily long after startup.
  The window icon is now loaded into a QPixmap inside the as_file() block; the welcome logo is
  copied to a stable per-session temp file (cleaned up at exit) so the QSS url stays valid.

## [1.5.261] - 2026-07-07
### Fixed (reconciled a caller/callee mismatch from out-of-order patching)
- **The time-series pipeline would crash with a `pre_process_image() got an unexpected keyword
  argument norm_max` error.** The working tree had the newer time-series code (which calls
  `pre_process_image(..., norm_max=...)`) but an older `image_processing_tools.py` whose
  `pre_process_image` did not yet accept `norm_max` — the 1.5.242 change was documented in the
  changelog but the code had not fully landed. Re-applied the `norm_max` parameter to
  `pre_process_image` (None = original per-frame 2D behaviour, unchanged; a fixed value = the
  stack global scale for time-series), and the 1.5.249 minimal recorded-step breadcrumb in
  `batch_processor.py`, so caller and callee agree again.

## [1.5.260] - 2026-07-07
### Changed (napari's native menus collapsed behind a toggle, hidden by default)
- **napari's own top-level menus (File / View / Plugins / Window / Help / Layers) are now hidden
  by default and collapsed behind a single leftmost "☰ napari" toggle.** Supersedes the
  File-only hide from 1.5.257. The PyCAT workflow doesn't need napari's native menus, and several
  test users lost their session by loading data through napari's File → Open (which bypasses
  PyCAT's channel-assignment / metadata pipeline and crashes the workflow). Now:
  - **Nothing napari-native is visible on open** — only PyCAT's controls.
  - **The menus are hidden, not removed** — clicking the leftmost **☰ napari** toggle reveals them
    (some napari layer operations are genuinely useful), and clicking again hides them. The toggle
    label shows a ▾ affordance when revealed.
  - **★ Open/Save File(s) is now the first PyCAT menu** (moved ahead of Analysis Methods /
    Toolbox), since loading data is the workflow's entry point. The visible bar reads
    `☰ napari  ◆ PyCAT ▸  ★ Open/Save File(s)  Analysis Methods  Toolbox  …`.
  - **napari's Open* actions stay disabled even when the menus are revealed**, so data always
    loads through PyCAT's reader regardless.
  - Fully defensive: identifies napari-native menus by title, never touches PyCAT's own menus
    (verified no title overlap), and never raises if napari changes its menu layout.

## [1.5.259] - 2026-07-07
### Fixed (ReadTheDocs build was pinned to Python 3.9)
- **`.readthedocs.yaml` build environment updated from Python 3.9 to 3.12.** The docs build does
  `pip install .` (the API reference uses autodoc, so PyCAT must be importable), but the build
  Python was still 3.9 while the package now requires `>=3.12,<3.14` — so the docs build would fail
  to install PyCAT, the same way a user on 3.9 can't. The build now runs on Python 3.12, matching
  `pyproject.toml`. Needed for the corrected installation docs (1.5.256 / 1.5.258) to actually
  publish to the live site.

## [1.5.258] - 2026-07-07
### Docs (Mac Apple Silicon: avoid the llvmlite source-build failure)
- **Added Apple-Silicon install guidance to install `llvmlite` / `numba` from conda-forge before
  pip-installing PyCAT** (README and installation.rst). On some Macs, `pip` can't find a prebuilt
  `llvmlite` (a `numba` dependency) and falls back to compiling it from source, which fails with
  `llvmlite needs CMake tools to build` when compiler tools aren't installed. Installing
  `llvmlite` and `numba` from conda-forge first (they ship prebuilt Apple-Silicon binaries) avoids
  the build entirely:
  `conda install -c conda-forge llvmlite numba` then `pip install "pycat-napari[arm-mac]"`. The
  note also documents the `cmake` fallback (`conda install -c conda-forge cmake llvmlite numba`)
  for the rare case the source build is still attempted. Surfaced during a multi-user install
  test.

## [1.5.257] - 2026-07-07
### Superseded by 1.5.260
- (Hid only napari's File menu. Replaced by the collapsible "☰ napari" toggle in 1.5.260, which
  hides all napari-native menus by default while keeping them reachable. This version was held and
  not released.)

## [1.5.256] - 2026-07-07
### Fixed (stale Python 3.9 references in docs — caused users to build a 3.9 environment)
- **The ReadTheDocs installation guide and conda recipe still instructed users to create a Python
  3.9 environment**, contradicting the actual requirement (`pyproject.toml`: `>=3.12,<3.14`) and
  the main README (which was already correct at 3.12). A multi-user install test surfaced this: a
  user who followed the docs ended up in a Python 3.9 environment. Updated to Python 3.12
  (supported range 3.12–3.13) everywhere:
  - `docs/source/installation.rst` — platform table, minimum-requirements, the compatibility
    warning, `conda create -n pycat-env python=3.12`, and all `python --version` checks.
  - `docs/source/development/support.rst` — troubleshooting "verify Python 3.12 installation".
  - `docs/source/conf.py` — intersphinx now points at the Python 3.12 docs.
  - `meta.yaml` (conda recipe) — `python >=3.12,<3.14`.
  The only remaining "3.9" mentions are the intentional "3.9 is no longer supported as of v1.5.39"
  notes.
### Note
- Unrelated to PyCAT: a Mac user in the same test saw a `conda-libmamba-solver` / `libarchive.19.dylib`
  error from a Homebrew-installed Miniconda (a known Homebrew-conda library-versioning breakage on
  Apple Silicon), while PyCAT itself imported successfully. The recommended path is the Miniforge
  install flow in the README rather than Homebrew's Miniconda.

## [1.5.255] - 2026-07-07
### Docs (generalized the spectroscopy roadmap section for public release)
- **Rewrote the "Advanced Spectroscopy, Correlation & Orientation Methods" roadmap section to be
  hardware-agnostic.** The 1.5.254 version named specific lab instruments and a future
  custom-microscope design; since the roadmap is public-facing (ReadTheDocs), those details were
  replaced with capability-based framing (e.g. "a fast sCMOS + TIRF/HILO," "point-detector
  confocal," "polarization optics," "a FLIM-capable instrument") rather than instrument names or
  future build plans. The technical content, sequencing (by data availability), reuse-of-existing-
  machinery notes, and manuscript framing are unchanged.

## [1.5.254] - 2026-07-07
### Docs (roadmap: advanced spectroscopy / correlation / orientation methods)
- **Added a dedicated "Advanced Spectroscopy, Correlation & Orientation Methods" section to the
  roadmap** (`docs/source/development/roadmap.rst`), capturing the instrument-scoped plan for a
  family of quantitative fluorescence techniques PyCAT doesn't yet analyse. Organized around the
  positioning that PyCAT is the downstream quantification layer for specialised acquisition
  instruments (import-and-analyse, don't reimplement acquisition), and scoped to the lab's actual
  instrument base (Lumicks C-Trap, ISS Q2, Andor Dragonfly + iXon 888 EMCCD / Zyla sCMOS, campus
  Stellaris/STED, incoming Airyscan 2, Kinetix). Covers: FCS/FCCS (Q2), RICS/STICS (scanning
  confocals, highest near-term leverage), imaging camera-FCS (sCMOS/Zyla or future Kinetix; notes
  why the EMCCD is the weaker FCS detector), FLIM phasor downstream (Q2), ratiometric/spectral,
  fluorescence anisotropy/homo-FRET, PolScope orientation, and SMLM localization-table analysis
  (cross-referenced to the existing Super-resolution Category B rubric). Sequenced by data
  availability today vs. future hardware, with the "what composes with existing modules" note for
  each. The existing FCS/FCCS stub under Advanced Methods now cross-references the new section.

## [1.5.253] - 2026-07-07
### Fixed (lazy TIFF wrapper broke analysis that materialises the whole stack)
- **Regression from the 1.5.245 OME-TIFF scrubbing fix.** The lazy `_TiffPageStack` reader's
  `__array__` deliberately returns only the FIRST frame (so napari's incidental array/thumbnail
  requests don't materialise the whole stack — that truncation, plus pinned contrast_limits, is
  what made scrubbing smooth). But analysis code that did `np.asarray(layer.data)` to get the
  full `(T, H, W)` stack then silently received a single 2D frame — so shape checks saw `ndim==2`
  and bailed. This broke the temperature workflow's **"guess reference frame"** ("Reference-frame
  guessing needs a (T, H, W) stack") and the same pattern in its sync / pattern-correction /
  analysis steps.
  - Added `_TiffPageStack.as_full_array()` (reads every frame, one at a time) and a module-level
    `materialize_stack()` helper that safely turns any stack-like layer data (lazy wrapper, dask,
    or plain array) into a real `(T, H, W)` array — the correct call for analysis that needs all
    frames.
  - The temperature UI's four stack-reading sites now use `materialize_stack()` instead of
    `np.asarray()`. `__array__` still returns one frame, so napari display stays fast.
  - Verified: `np.asarray(wrapper)` gives `ndim==2` (the bug) while `materialize_stack(wrapper)`
    gives the correct `ndim==3` stack, byte-identical to the source; plain arrays pass through.
### Note
- The pixel-size regression on the same file was addressed in 1.5.253's companion fix (stale
  `pixel_size_from_metadata` flag, see 1.5.252). A separate, deeper issue was noticed for
  follow-up: the file_io load path computes a pixel size (with TIFF-tag recovery) but then
  `update_metadata()` re-reads `physical_pixel_sizes` independently and can overwrite it with the
  1.0 fallback for Micro-Manager OME-TIFFs — the two metadata paths should be reconciled.

## [1.5.252] - 2026-07-07
### Fixed (pixel-size gate hidden on an unscaled image after a stale metadata flag)
- **The pixel-size gate now correctly appears when an image loads without a real physical pixel
  size** (e.g. a Micro-Manager OME-TIFF whose resolution metadata is incomplete, where the
  loader falls back to 1 µm/px² and warns "Resolution data incomplete, using default value of
  1"). The metadata-provenance flag `pixel_size_from_metadata` was set correctly on the normal
  and incomplete-metadata paths, but the two exception fallbacks in `update_metadata` set the
  default scale **without clearing the flag** — so a `True` left over from a previously-loaded,
  properly-scaled image made the gate think this image had a real scale and stay hidden. All
  fallback paths now set `pixel_size_from_metadata = False`, so an unscaled image always prompts
  for the pixel size. (`_valid_scale()` already treated a bare 1.0 as invalid; the bug was purely
  the stale provenance flag.)

## [1.5.251] - 2026-07-07
### Changed (README Miniforge download link)
- **The Miniforge install step now links to the official [conda-forge download
  page](https://conda-forge.org/download/)** (per-platform installer picker) as the primary
  download, with the [Miniforge GitHub page](https://github.com/conda-forge/miniforge#miniforge3)
  kept as an alternative for the installers and detailed instructions.

## [1.5.250] - 2026-07-07
### Added (Segmentation Benchmark harness)
- **New "Segmentation Benchmark" diagnostic tool** (Image Processing menu) — a general
  comparison harness for manuscript preparation. Runs several segmentation candidates on the
  same image and reports metrics as a pasteable markdown table plus in-app side-by-side mask
  layers (`bench: <name>`). One framework covers three uses:
  - **Method comparison** — run built-in methods (Otsu, Multi-Otsu, Sauvola, Felzenszwalb,
    watershed, Cellpose) on one image; compare object count, area, runtime, and pairwise overlap.
  - **Ground-truth validation** — mark any candidate as ground truth; the others are scored
    against it.
  - **Parameter sensitivity** — supply the same method at different parameters as candidates and
    read the trend.
  - **External / uploaded masks are first-class candidates.** Any Labels layer (a mask exported
    from another tool, or a manual annotation) can be included in the comparison, so PyCAT's
    segmentation can be benchmarked directly against other tools on identical data — useful for
    puncta segmentation comparisons in particular.
  - **Two metric families shown side by side, without privileging either:** pixel-overlap
    (Dice / IoU) and matched-detection (precision / recall / F1 via Hungarian centroid matching,
    plus mean localisation error). This matters for puncta: two tools can agree on *which* spots
    exist (high F1) while their pixel masks differ (lower Dice) due to sub-pixel offset — both
    columns tell the honest story.
  - **Match tolerance** for detection metrics is either auto-scaled to a fraction of the mean
    object radius (default) or a fixed pixel radius.
  - New module `benchmark_tools.py` (candidates, both metric families, three modes, markdown
    report); verified on synthetic puncta that detection F1 and pixel Dice correctly diverge for
    a spatially-offset detector, and end-to-end with built-in method runners.
### Note
- The harness counts connected components, so touching puncta merge into one object (affects all
  methods equally in a comparison). Runs on a single 2D image (pick one frame/plane).

## [1.5.249] - 2026-07-07
### Fixed (recorded-steps list didn't reset on Clear)
- **The batch recorded-steps list now resets on the plain Clear button**, not just Save & Clear.
  Previously `_clear_everything` (the shared reset used by the top-bar Clear and by Save &
  Clear's discard option) reset layers, dataframes, and the workflow checklist but left the
  batch recording intact, so a new dataset started with the previous dataset's recorded steps
  still listed. It now calls `clear_recording()`, which empties the recorded steps, flips the
  record toggle back to OFF (red), and resyncs the toolbar. Save & Clear still offers to export
  the config first before resetting.
### Changed (quieter recording output)
- **Removed the verbose per-step recording dump from the terminal.** Each recorded step used to
  print its full parameter dict (including layer snapshots) to the console; now that the "☰
  Recorded Steps" viewer shows the step name, parameters, and snapshots, that dump was redundant
  noise. Recording now prints a short one-line breadcrumb per step (`Recorded step N: <name>`)
  so the recorder isn't silent, and the full detail lives in the viewer.

## [1.5.248] - 2026-07-07
### Changed (README reorganized for low/no-code users)
- **Reworked the README install flow to reduce friction for non-technical users** (ahead of a
  group test-installation session):
  - **Miniforge-first, single top-to-bottom path.** "Getting Started" is now the one install
    path, as four numbered steps: Install Miniforge → create a workspace → install PyCAT →
    launch. A new user can't skip setup by clicking a separate "Installation" link.
  - **Removed the standalone "Installation" table-of-contents entry** that let impatient users
    jump past the Python/terminal setup; the TOC now points everyone into the guided Getting
    Started steps (with Miniforge, workspace, install, and launch as sub-items).
  - **`run-pycat` promoted to Step 4**, immediately after install and **before** GPU
    acceleration, with a page break after it, so the first thing a user reaches is a working
    launch — not optional speed tuning.
  - **Advanced/optional material is now collapsible** (`<details>` blocks): GPU acceleration,
    optional add-on packages, Cellpose model choice, dependency pin rationale, alternative
    install, and verification — so the main path isn't visually overwhelming, but the detail is
    one click away.
  - Added beginner-friendly explanations (what an environment is, how to confirm each step
    worked, a reminder to `mamba activate` next time) written for readers who don't know Python
    or the terminal.

## [1.5.247] - 2026-07-07
### Changed (time-series first-run speedup — skip the source pre-copy)
- **Time-series analysis no longer pre-copies the input stack to a temp zarr when the source is
  a TIFF or an existing filesystem zarr (e.g. IMS).** Previously, before any processing, every
  frame was read from the source and written to a temporary float32 zarr so the parallel workers
  could open it by path — then each worker re-read those frames. On a first run (the debugging
  case) that meant reading every frame twice and writing it once purely as copy overhead, before
  real work began. Workers now read frames directly:
  - **TIFF** (via the `_TiffPageStack` reader): each worker opens its own `tifffile` handle and
    seeks to its page — no whole-stack copy.
  - **Filesystem zarr / IMS-derived**: used directly, as before.
  - **Other sources** (numpy, dask, non-seekable): still materialised to a temp zarr (unchanged).
  - The global-range normalisation the copy used to apply is preserved — computed once up front
    (a cheap frame-at-a-time min/max pass) and applied inside each worker, so intensity trends
    across time are still preserved. Verified byte-equivalent to the old copy-then-read path on a
    synthetic brightening-focus stack.
  - **Safe fallback:** if a direct TIFF read fails mid-run (locked file, network hiccup,
    unexpected page layout), the run materialises the source to a temp zarr and retries once.
  - The preprocessed and background-removed output stacks are still written and shown as layers
    exactly as before — only the redundant *input* copy is removed.
  - Pseudo-3D temporal pre-pass (opt-in) still materialises a zarr when enabled, since it needs
    the whole stack as an array anyway.
- This is the first module to get the source-copy skip; other modules can adopt the same
  `_source_descriptor` pattern later.

## [1.5.246] - 2026-07-07
### Fixed (TIFF lazy reader crashed on slice indexing)
- **"Failed to open stack: int() argument must be … not 'slice'"** — the new `_TiffPageStack`
  lazy TIFF reader (1.5.245) assumed the time index was always a scalar and did `int(t_idx)`,
  which crashed when napari or downstream code indexed the T axis with a slice (`[:]`, `[10:15]`)
  or a list/array. It now handles all indexing patterns: scalar int (the fast single-page
  scrubbing path), numpy integer types, slices (reads the requested frame range), fancy
  list/array indices, and any of these combined with a spatial sub-index. Verified against the
  full set of napari access patterns.

## [1.5.245] - 2026-07-07
### Fixed (laggy scrubbing through TIFF/OME-TIFF time-series — corrected approach)
- **TIFF/OME-TIFF (incl. Micro-Manager MMStack) time-series now scrub smoothly, staying fully
  lazy.** Two independent causes were fixed, keeping the intended design (open the file once,
  read exactly one frame per slider move — no eager copy, no materialisation):
  - **Whole-stack read on every slider move (main cause):** the generic-stack layers were added
    without pinned `contrast_limits`, so napari auto-estimated the display range by calling
    `np.asarray()` on the lazy wrapper — which read the ENTIRE stack — on each frame change. The
    TIFF/CZI paths now pin `contrast_limits` from the first frame (the IMS path already did
    this), so navigation never triggers a whole-stack read.
  - **Slow per-frame reads through AICSImage:** a Micro-Manager OME-TIFF read via AICSImage's
    dask reader walks the OME plane-map on every frame, so scrubbing a large MMStack lags even
    when only one frame is requested. TIFF time-series now read frames straight from the
    multipage TIFF via a new lazy `_TiffPageStack` wrapper (`tifffile` per-page seek — one page
    per read, no dask graph, no copy), matching the smooth per-frame behaviour of the native IMS
    zarr path. The wrapper prefers the OME **series** page sequence so it spans multi-file
    MMStack sets (`_1.ome.tif`, `_2.ome.tif`, …); it falls back to the AICSImage reader if the
    page layout is ambiguous (e.g. an unmodelled multi-channel order) or tifffile can't open the
    file. CZI keeps the AICSImage path.
### Reverted
- The v1.5.244 approach (materialising the whole stack to a local float32 zarr on load) is
  removed: it defeated the lazy-loading design, and for an 8-bit 3800-frame MMStack it would
  have written ~23.6 GB (4x the 5.9 GB source) to disk up front. The corrected fix above keeps
  reads lazy and one-frame-at-a-time.

## [1.5.244] - 2026-07-07
### Superseded by 1.5.245
- (Materialise-to-zarr approach — replaced by the lazy `_TiffPageStack` reader + pinned
  contrast_limits in 1.5.245.)

## [1.5.243] - 2026-07-07
### Added (Temporal Enhancement Optimizer)
- **New "Temporal Enhancement Optimizer" diagnostic widget** (Image Processing menu) that
  competes temporally-aware enhancement strategies against a loaded time-series and picks the
  one that best preserves the true intensity trend across frames. Motivation: per-frame
  CLAHE/LoG normalization is per-frame adaptive — consistent across XY but not across time — so
  in a correlated time-series a brightening focus can appear to dim, and dim condensates drop
  out once a brighter one enters the field.
  - Strategies competed: `per_frame` (baseline control), `pooled_stats` (nn/nnn — scale from the
    pooled temporal window, enhance each frame's own pixels), `windowed_mean` (temporally-
    weighted average then enhance), and `triplanar` (tri-planar XY/XT/YT coupling).
  - Each is scored by trend preservation (Spearman rank correlation and direction-of-change
    agreement between the raw and enhanced per-frame condensate signal), with a light cost
    penalty so the cheapest method that does the job wins ties. Results are shown as a ranked
    table and the winning enhanced stack is added as a layer for inspection.
  - Window is optimized against the data by default (competes ±1 and ±2); a "Set window
    manually" checkbox reveals a spin box to override.
  - A validity warning notes temporal enhancement is only valid with adequate frame-to-frame
    correlation; a "Check temporal correlation" button runs the estimator and hides the warning
    if the data is in a correlated (oversampled/moderate) regime.
  - "Apply winner as session default" stores the choice; a tri-planar/windowed winner is honored
    by the time-series preprocessing step via the existing pseudo-3D temporal path.
  - New module `temporal_enhancement_tools.py` (methods + scoring); verified that the scoring
    correctly ranks trend-preserving enhancement above per-frame normalization on a synthetic
    growing-focus stack.
### Note
- Full pipeline integration of the per-frame-worker strategies (`pooled_stats`, non-triplanar
  windowed) is staged for a follow-up; the optimizer itself runs standalone and produces the
  enhanced layer plus the winning configuration now.

## [1.5.242] - 2026-07-06
### Fixed (time-series: preprocessing re-normalized every frame per-frame, dimming later frames)
- **The preprocessing/background-removal worker no longer re-normalizes each frame by its own
  min/max.** Even after 1.5.240 put the *source* frames on one global [0,1] scale,
  `pre_process_image` still divided each frame by its own max internally, and the worker did a
  second per-frame min/max normalization before background removal. Both reintroduced the
  intensity-trend distortion: as condensates brighten over time, the per-frame max (the
  denominator) rises, so later frames appear DIMMER in the preprocessed/enhanced-background
  stack even though the raw condensates are brighter (the reported "frame 4 dimmer than frame 3"
  and dim condensates dropping out once something brighter appears).
  - `pre_process_image` gained an optional `norm_max` parameter. When `None` (all 2D callers),
    behavior is byte-identical to before. The time-series worker passes the stack's global max,
    so every frame is normalized by the same scale.
  - Removed the redundant second per-frame normalization in the worker.
  - Verified: 2D path unchanged (norm_max=None); time-series frames now share one scale,
    preserving the true intensity trend across time.

## [1.5.241] - 2026-07-06
### Fixed (time-series puncta segmentation now matches the 2D fluorescence path)
Puncta detection in the time-series workflow was weaker than the validated 2D path because two
steps differed. Both are now aligned (segmentation correctness before speed):
- **Per-cell contrast stretching (`cell_mask_stretching`) is now applied in the time-series
  path**, as it is in 2D. The 2D puncta pipeline computes
  `CMS_img = cell_mask_stretching(preprocessed, cell_masks)` and segments puncta on that
  stretched image; the time-series path was passing the plain preprocessed frame instead. Both
  the parallel and serial time-series workers now compute the same per-cell stretched image per
  frame (using the per-frame cell mask) and pass it to `segment_subcellular_objects`, so puncta
  detection matches 2D.
- **`min_spot_radius` is no longer ignored during refinement.** `segment_subcellular_objects`
  accepted a `min_spot_radius` argument but then called `puncta_refinement_func(...,
  min_spot_radius=2)` with a hardcoded 2, so the UI/parameter value was silently dropped during
  the refinement step. It now passes the actual `min_spot_radius` through. **This is
  output-preserving at the default:** every UI ships `min_spot_radius = 2`, and passing 2
  through is byte-for-byte identical to the old hardcoded 2 (verified across all four internal
  uses — the two Gaussian sigmas, the gradient-magnitude sigma, and the min-area computation;
  int 2 and float 2.0 give identical results). Behavior only changes for users who deliberately
  set a non-default value, where the parameter now takes effect as intended (this applies to
  both 2D and time-series).
### Notes
- This deliberately does NOT re-enable the earlier "make TS refinement like 2D" change or touch
  Cellpose model handling. The cell/body mask path (e.g. cyto2 without refinement) is unchanged;
  only the puncta path was aligned. Cellpose is not used for puncta.
- Drift-correction vs per-frame-mask interaction and transfection-filter ordering are noted for
  follow-up but not changed here.

## [1.5.240] - 2026-07-06
### Fixed (time-series: per-frame normalization erased/inverted intensity trends)
- **Time-series frames are now normalized against ONE global range, not per-frame.** The
  per-frame min/max normalization in `_read_source_frame` made a growing focus appear to
  plateau or decay: as foci brighten over time, the per-frame max (the normalization
  denominator) rises, shrinking the normalized value of a focus even as its raw intensity
  increases. On real data (diffuse mCherry that condenses into foci which grow brighter/bigger)
  this produced a spurious "peak at frame 3, decay in frames 4–5" instead of the true monotonic
  increase.
  - Added `_compute_stack_global_range()` (reads one frame at a time — never holds the whole
    stack in RAM) and a `global_range=` option on `_read_source_frame()`.
  - The source-zarr materialization (feeding preprocessing → background removal → analysis),
    the general stack→zarr helper, and the upscale step now all normalize against the stack's
    global min/max, preserving true intensity trends over time.
  - Verified on a simulated growing focus: per-frame normalization flattened it to a constant;
    global normalization recovered the correct increasing trend.
  - Frame-to-frame temporal-correlation reads are left per-frame (correlation is scale-invariant
    there, so it's unaffected).

## [1.5.239] - 2026-07-06
### Fixed (time-series "Check if upscaling is needed" crash + plot event-loop warning)
- **"Check if upscaling is needed" no longer crashes** with ``AttributeError: 'ToolboxFunctionsUI'
  object has no attribute '_dr'``. The upscale step (added in 1.5.229) used ``ui._dr()`` /
  ``ui._mpx()`` helpers that only exist on the in-vitro UI classes, but in the time-series flow
  the UI is ``ToolboxFunctionsUI``. Switched to the correct
  ``central_manager.active_data_class.data_repository`` access (and read
  ``microns_per_pixel_sq`` from there for the upscaled layer's scale). The check, factor
  recommendation, and lazy upscale now work in the time-series workflow.
- **Quieted the "QCoreApplication::exec: The event loop is already running" warning** from the
  time-series condensate-fraction plot: ``plt.show()`` → ``plt.show(block=False)`` so it doesn't
  try to start a second Qt event loop inside napari's running one.
### Note
- The ``RuntimeWarning: Mean of empty slice`` / ``invalid value in divide`` messages during
  analysis are benign — they come from cells with no puncta (the "low contrast, likely has no
  puncta" cells), where per-cell statistics are legitimately NaN. The analysis completes
  correctly; these are console noise, not errors.

## [1.5.238] - 2026-07-06
### Fixed (pixel-size gate appeared on Clear with no image)
- **The pixel-size gate no longer pops up after Clear when no image is loaded.** The gate is
  only meaningful when an image lacking scale metadata is present; after a Clear there are no
  image layers, so it now stays hidden. The gate checks the viewer for any Image layer before
  showing (failing open if it can't determine, so it never hides when actually needed).

## [1.5.237] - 2026-07-06
### Changed (recording toggle — colored status circle)
- **The batch recording toggle now shows a colored circle** reflecting its state: 🔴
  "Record" when idle (off, ready to start) and 🟢 "Recording" when actively capturing steps.

## [1.5.236] - 2026-07-06
### Changed (batch recording toggle moved to the PyCAT toolbar)
- **The start/stop recording toggle is now in the PyCAT toolbar** (left of "Save Config"),
  not buried in the Batch dialog — so you can turn recording on before clicking through your
  workflow. It shows "⏺ Record" when off and "⏺ Recording" (checked) when on, and stays in
  sync after a Save & Clear resets recording to off.
- **The PyCAT toolbar is now grouped into labelled sections**: a **Batch:** section (Batch
  Run, Record, Save Config) is separated from a **Layers:** section (show/hide Layers, Gray/
  Viridis colormap toggle) by a divider, so the batch controls are visually distinct from the
  layer-view controls.

## [1.5.235] - 2026-07-06
### Changed (batch recording — off by default, opt-in)
- **Batch recording now starts OFF** and is opt-in via the start/stop toggle, so exploratory
  clicking isn't captured before the user decides to record a workflow. Recording also resets
  to OFF after a Save & Clear (dataset boundary) — the user re-enables it with the toggle when
  they want to record again. (Matches the normal usage of recording a workflow once per
  session, then batch-replaying it.)
### Added (Recorded Steps viewer)
- **New "☰ Recorded Steps" menu-bar panel** (next to Metadata). Shows the batch workflow
  recorded so far as an expandable tree: each step (number, name, timestamp) expands to reveal
  the layers/parameters it captured, with the internal layer-snapshots shown separately at the
  end. Includes a recording-status indicator and expand/collapse-all controls, so the user can
  review exactly what will be replayed before running a batch.

## [1.5.234] - 2026-07-06
### Fixed (Save & Clear crash — UnboundLocalError, regression from 1.5.225)
- **Save & Clear (and saving images generally) no longer crashes** with
  ``UnboundLocalError: cannot access local variable 'QFileDialog'``. The batch export-prompt
  added in 1.5.225 did a local ``from PyQt5.QtWidgets import QFileDialog`` inside
  ``save_and_clear_all``; because Python scopes that name as local for the whole function, the
  earlier ``QFileDialog.Options()`` call failed before the local import ran. Removed the
  redundant local imports (``QFileDialog``, ``QMessageBox``, ``QCheckBox`` are all imported at
  module level), restoring Save & Clear and image saving.
### Added (batch recording start/stop toggle)
- **Start/stop recording toggle in the Batch dialog.** A button pauses/resumes step recording
  without clearing what's already recorded — useful for skipping exploratory steps that
  shouldn't be part of the saved workflow. Reflects and drives the existing
  ``recording_enabled`` flag (which ``record()`` already honors).

## [1.5.233] - 2026-07-06
### Added (In Vitro Brightfield — "Invert + reconcile" segmentation)
- **New "Invert + reconcile" method** for the dense/out-of-focus regime, from a tester's
  suggestion to invert the image before processing. Brightfield/phase condensates flip contrast
  depending on which side of focus they're on — some are bright-centred, others dark-centred —
  so a single polarity misses roughly half. This method runs a polarity-specific detector
  (white top-hat) on BOTH the image and its inversion, **unions** the two masks to catch
  condensates of either contrast, watershed-splits, then **drops oversized objects** (merged
  background/debris) using the Max diameter setting.
  - Verified on real dense brightfield data: the inverted polarity surfaced ~27% additional
    droplet area that the original polarity missed entirely.
  - Note: the texture (local-std) and DoG methods are already polarity-invariant (variance /
    |difference| based), so inversion doesn't change them — the reconcile trick specifically
    helps the intensity/top-hat family, which is what this method uses.

## [1.5.232] - 2026-07-06
### Fixed (In Vitro Fluorescence — absurd per-droplet partition coefficients)
- **Per-droplet partition coefficients no longer blow up to ~1e8.** `partition_coefficient_field`
  estimated the bulk (dilute-phase) intensity as the 10th percentile of background, which
  collapses to ~0 on dark fluorescence backgrounds; every per-droplet partition was then
  `intensity / ~0`. It now uses a robust bulk (falls back to the background mean when the
  percentile is degenerate, with a final divide-by-zero floor), putting per-droplet values on
  the same sensible scale as the field-level partition (which already used the mean).

### Changed (In Vitro Brightfield — segmentation consistency)
- **Texture method now uses a LOCAL-ADAPTIVE threshold** instead of a single global Otsu on the
  texture map. The global threshold made segmentation inconsistent across regions of identical
  texture — dense areas fused into one giant blob while others dropped out entirely. A local
  threshold judges each neighbourhood against its own surroundings, so uniform-texture regions
  break into individual droplets consistently. (No more giant merged blobs on the test image.)
- **New "Blob detection (DoG)" method.** Difference-of-Gaussians responds to individual
  droplet-scale blobs rather than thresholding connected high-texture regions, so it cannot
  produce the "one giant blob" undersegmentation and gives the most consistent per-droplet
  output. Sigmas scale with the expected droplet radius.
- Both texture and DoG share watershed splitting for touching droplets.
### Fixed (deprecation)
- **`remove_small_objects` no longer triggers the `min_size` deprecation warning.** A
  version-compatible helper uses the new `max_size` argument (skimage ≥ 0.26) with a fallback
  to `min_size` for older versions.
### Docs
- Recorded the brightfield-segmentation cross-regime generalization task (sparse+large,
  small+sparse, large+dense semi-overlapping, fractal/irregular aggregates) and the planned
  "guess the condition" button in the roadmap — to be implemented only once representative test
  data is supplied per regime.

## [1.5.231] - 2026-07-06
### Added (In Vitro Brightfield — texture-based segmentation for dense/defocused droplets)
- **New "Texture (edges/rings)" segmentation method for brightfield droplets**, optimized
  against real dense small-condensate data. Brightfield/phase droplets — especially
  out-of-focus ones — appear as rings (dark rim + bright centre) with little net brightness
  difference from the mid-grey background, so the legacy intensity threshold merges background
  or misses them (measured median solidity ~0.6 with one giant merged-background blob). The
  texture method segments by local intensity variation (local standard deviation): high
  wherever there's a droplet edge/ring, thresholded, hole-filled (ring → disc), and optionally
  watershed-split for dense touching droplets. On the test image this gave clean per-droplet
  masks (median solidity ~0.92) capturing both in-focus spots and defocused rings.
  - New `segment_bf_condensates(method='texture'|'intensity', texture_window, split_touching)`;
    default in the UI is now **Texture** (Intensity remains available for preprocessed
    bright-blob images).
  - UI: method dropdown in Step 3 with texture window + watershed-split controls (shown only
    for the texture method).
### Note
- Optimized on a single dense-defocused-droplet image; the texture method is the better
  default for that regime, but the intensity method is kept for images where droplets are
  uniformly brighter than background after preprocessing.

## [1.5.230] - 2026-07-06
### Fixed (pixel-size gate — premature hide + no reappear after Clear)
- **The pixel-size gate no longer vanishes mid-entry.** It previously auto-applied and hid the
  instant a valid number appeared, so it disappeared while you were still typing (e.g. at
  "0.0" before you finished "0.0957"). It now shows a confirmation — "Is xx.xx µm/px the
  correct scale?" — with the value editable, and only hides after you press **Confirm pixel
  size**. Changing the value after confirming re-arms the prompt so the new value must be
  reconfirmed. The "Keep this pixel size for the session" checkbox remains in the panel.
- **The pixel-size gate reappears after Clear.** Clearing wipes the scale from the data
  repository, but the gate wasn't re-evaluating, so it stayed hidden. Clear now resets the
  gate: with "keep for session" unchecked it reappears for the next dataset; with it checked
  the remembered value is re-applied and the gate stays hidden (as intended).

## [1.5.229] - 2026-07-06
### Added (time-series: standalone early upscale step)
- **The time-series workflow now has an optional early "Upscale Stack" step**, placed before
  preprocessing to match the 2D cellular order (load → ROI → upscale → preprocess → segment
  nuclei → segment condensates). Previously upscaling only happened inside the Cellpose call
  and was rescaled away, so downstream analysis ran at original resolution.
  - **Lazy / zarr-backed**: frames are upscaled one at a time into a zarr store on disk and
    presented as a lazy `_ZarrStack`, so the result is snappy (frames read on demand) like the
    rest of the TS pipeline, and the full upscaled stack is never held in RAM.
  - **Optional and gated**: a "Check if upscaling is needed" button compares the current cell
    diameter against Cellpose's ~30 px preferred minimum and recommends a factor (or says
    upscaling isn't needed if the data already meets it).
  - Downstream `cell_diameter` and `ball_radius` are scaled by the upscale factor so
    Cellpose and background-removal parameters stay correct.
  - Added to the workflow checklist as step 4 (subsequent steps renumbered).

## [1.5.228] - 2026-07-06
### Fixed (time-series condensate analysis crash: empty per-frame cell mask)
- **`IndexError: index 0 is out of bounds for axis 0 with size 0` in
  `segment_subcellular_objects` is fixed.** When the cell-label set is taken as the union
  across all frames (from a (T,H,W) mask), a given cell can have zero pixels in a particular
  frame; the crop optimisation then ran `np.where(rows)[0][[0, -1]]` on an all-False mask and
  crashed. Now:
  - `segment_subcellular_objects` guards the empty-mask case and returns empty results instead
    of indexing into an empty array.
  - The time-series analysis loop (both parallel and serial paths) skips cells with no pixels
    in the current frame, so absent cells are cleanly ignored rather than segmented.
  This is independent of upscaling — the crash could occur whenever a cell was missing from a
  frame, regardless of the preprocessing path.

## [1.5.227] - 2026-07-06
### Fixed (In Vitro Fluorescence — tester feedback)
- **Random Forest no longer produces empty masks.** Root cause: the RF classifier runs CLAHE
  (`equalize_adapthist`), which requires float input in [-1, 1], but the raw fluorescence
  image is in raw intensity units — CLAHE raised "Images of type float must be between -1 and
  1", was swallowed by the worker, and surfaced as an empty mask. The image is now normalized
  to [0, 1] before the RF call. Verified RF then produces a proper droplet mask.
### Changed (In Vitro Fluorescence)
- **Step 2 preprocessing is now optional with gentler methods.** Rolling-ball background
  subtraction could hollow out large droplets (the donut problem). The step is now labeled
  optional and offers Gaussian blur (default — keeps interiors solid), LoG edge enhancement,
  or rolling-ball (legacy). Segmentation can run directly on the raw image if preprocessing is
  skipped.
- **Steps 7 (Dynamics) and 9 (Frame Quality / bleaching) are hidden unless a stack is loaded.**
  These only apply to 2D+t or 3D data; they're shown/hidden automatically based on whether any
  loaded image layer has ≥3 dimensions, re-evaluated on layer changes.
- **Step 4 "volume fraction" clarified as an area fraction.** Φ is the fraction of the imaged
  *plane* covered by droplets, not a true 3D volume fraction — in a flow cell, droplets settle
  into the bottom few µm of a ~200 µm channel, so single-plane Φ doesn't represent bulk volume
  fraction. The step note now says this explicitly.
- Sauvola remains available but non-default (it's noise-sensitive on clean in-vitro fields,
  producing irregular fragments in dark background); the min-object-size and optional
  round-object filters help suppress that debris.

## [1.5.226] - 2026-07-06
### Changed (In Vitro (Fluorescence) — simplified droplet segmentation)
- **Step 3 segmentation redesigned around a radio-button method selector**, showing only the
  chosen method's parameters (via a stacked panel) instead of exposing all six at once. Based
  on optimization against real FUS-PLD in-vitro data (clean, well-separated droplets), where a
  simple global threshold gives round, well-segmented objects (solidity ~0.95) and the heavy
  rolling-ball/kurtosis/SNR pipeline is unnecessary. Methods:
  - **Threshold (Otsu)** — default, zero-parameter (with an optional sensitivity ×multiplier).
    Matches what the data wants and what the user asked for.
  - **Multi-level threshold (Multi-Otsu)** — choose number of classes + cut at lower (inclusive)
    or upper (bright cores) boundary; good for core/halo droplets.
  - **Local threshold (Sauvola)** — window + k, with better defaults (win=35, k=0) than before.
  - **Random Forest** — with a **"Draw Scribbles" button** that creates/selects a labels layer
    and arms the paint tool. (Paint 1 = background, 2 = droplet, matching the classifier's
    label handling.)
  - **Advanced: spot detection (kurtosis / SNR)** — the original rolling-ball pipeline, preserved
    but tucked behind its own radio option so it's out of the way.
- **Shared post-filters**: a single "min object size (px²)" control (replacing the confusing
  "min spot radius") and an optional "reject non-round objects (solidity < 0.85)" filter suited
  to droplet data.

## [1.5.225] - 2026-07-06
### Fixed (batch recording — structural fixes, adapted from Christian's audit patch)
- **Save-and-Clear now ends the batch recording** instead of letting the next dataset's
  steps accumulate onto the previous one. Because the batch config is only written when you
  click "Save Config", Save-and-Clear first checks for unsaved recorded steps and — unless
  silenced — prompts to export the config (with a "Don't ask again this session" checkbox),
  then resets the recorder. This prevents both the "steps bleed across datasets" bug and
  accidental loss of an unexported recording.
- **Split-channel file loads are recorded and replayed correctly.** When a workflow is
  recorded by opening two separate files as channels (e.g. `cell01_DAPI.tif` +
  `cell01_GFP.tif`), the open step now records `source_files` and each channel's
  `source_stem`/`source_suffix`. Batch mode detects the split-file workflow, processes only
  the primary file per sample (instead of double-counting every file), and during replay
  derives each companion file for the current sample (`cell17_DAPI` → `cell17_GFP`),
  raising a clear error if a companion is missing.
- **`.ims` added to the batch-supported extensions.**
- **Recorded steps now carry a layer snapshot** (`_active_layer_at_record`,
  `_all_layers_at_record`) to help diagnose steps that captured the wrong dropdown layer
  name.
- Added a `recording_enabled` guard on the recorder.
### Note (not yet addressed)
- Some GUI callbacks still record dropdown layer names *after* the operation has changed
  viewer state; those per-widget captures need individual fixes (the layer snapshot above is
  the diagnostic aid for finding them). Applied manually rather than via `git apply` — the
  patch didn't apply cleanly against the current tree (which has drifted), and its
  Save-and-Clear hunk needed the export-prompt guard added to avoid wiping unsaved
  recordings.

## [1.5.224] - 2026-07-06
### Fixed (1.5.222 regression — ImportError on startup)
- **Restored `_add_run_ts_cellpose`**, which was accidentally deleted when the transfection
  filter functions were added before it in 1.5.222 (the insertion consumed the function's
  `def` line, leaving an orphaned body). The file still compiled — valid syntax — so the
  missing symbol only surfaced at import time as
  ``ImportError: cannot import name '_add_run_ts_cellpose'`` when launching. The function is
  back at module scope alongside the transfection helpers; verified by AST symbol check, not
  just a compile check.

## [1.5.223] - 2026-07-06
### Fixed (hollow "donut" segmentation of very large condensates — contributed by Christian Neureuter)
- **Large condensates (e.g. SS18 PLD) are no longer segmented as hollow rings.** The
  upstream ball_radius-scale enhancement is a band-pass that suppresses the flat interior of
  condensates much larger than the puncta scale, leaving only a fragmented rim; local
  Niblack/Sauvola thresholding then captured only a broken "necklace" ring. Four coordinated
  changes (merged from Christian's updated ``segmentation_tools.py`` +
  ``image_processing_tools.py``, both based on the current tree so no recent work was
  reverted):
  - **Absolute-brightness rescue** in ``fz_segmentation_and_binarization``: an Otsu
    whole-image threshold is OR-combined with the local threshold to recover the flat,
    saturated interior of large condensates that local contrast-based thresholding misses.
    OR-only, so it never reduces small/medium puncta sensitivity.
  - **Rim bridging**: a small, FIXED-scale morphological closing (``rim_close_radius=5``,
    deliberately NOT scaled with ball_radius) bridges the fragmented rim into a continuous
    ring so hole-filling can recover the full object — gated by ``rim_close_min_result_area``
    (150 px) so it only applies to genuinely large bridged rims and never fuses nearby small
    puncta.
  - **Permissive max area**: the hard 25% cap is relaxed to 90%, so genuine large condensates
    aren't rejected purely for size.
  - **Solidity-aware rejection** in ``puncta_refinement_filtering_func`` (serial + parallel):
    large objects are rejected only if they're *also* irregular (solidity < 0.85), which
    catches erroneous merges while keeping real compact large blobs.
  - **Large-object rescue** in the foreground-suppression pass
    (``image_processing_tools.py``): sufficiently large, contiguous, clearly-bright regions
    have their realness weight forced to 1, so the puncta-scale peakiness gates stop
    progressively dimming and dropping large coarsened condensates.

## [1.5.222] - 2026-07-05
### Added (transfection filter for transiently-transfected time-series)
- **Optional per-cell transfection filter in the time-series cell-segmentation step.** For
  transiently transfected samples, not every Cellpose-detected cell has usable signal. When
  the new "Filter untransfected cells" checkbox is on, after segmentation each cell is
  scored by fluorescence SNR (mean cell intensity ÷ background) on the reference frame of a
  chosen fluorescence channel — the same channel that will be analysed, not the DAPI
  segmentation channel. Cells below the SNR threshold are dropped.
  - Produces a separate **"Transfected Cells"** mask (the full mask is preserved).
  - Reports a **transfection-efficiency** estimate (fraction of cells above threshold) and
    stores a per-cell kept-vs-dropped stats table (`transfection_stats`) in the data
    repository for comparison/histograms.
  - **Off by default** — Csat-type experiments deliberately leverage low/untransfected
    cells, so the filter is opt-in. Threshold and fluorescence channel are user-selectable.
  - This is a coarse "is this cell worth analysing" gate, not puncta segmentation.

## [1.5.221] - 2026-07-05
### Fixed (time-series condensate analysis rejected the (T,H,W) cell mask)
- **Time-series condensate analysis now accepts a (T,H,W) cell-mask stack**, not just a 2D
  mask. The step hard-rejected anything non-2D with "Labels layer must be 2D" — but the
  keyframe Cellpose step correctly produces a (T,H,W) mask so that each frame's own cell
  boundaries (which move over time) are used. The analysis now:
  - Uses a (T,H,W) mask per-frame (each frame analysed against its own mask), in both the
    parallel and serial paths.
  - Accepts a 2D mask and propagates it to all frames, with a warning that this assumes the
    sample is temporally stationary.
  - Computes the cell-label set from the union across frames, so a cell present in only some
    frames is still analysed where it exists.
  - Warns (rather than failing) if a (T,H,W) mask's frame count doesn't match the image,
    falling back to the reference frame's mask.

## [1.5.220] - 2026-07-05
### Added (Cellpose "Refine masks" checkbox — raw vs refined, user's choice)
- **The 2D Cellpose segmentation (Cell Segmentation widget, used by Cellular Object Analysis
  and the colocalization pipelines) now has a "Refine masks" checkbox.** The same
  destructive post-processing found in the time-series audit (binarize → watershed →
  morphological opening → relabel) was also running on the 2D / coloc Cellpose output.
  Rather than change validated behaviour silently, it's now a toggle:
  - **ON (default)** — legacy refine pipeline; preserves the existing validated 2D result.
  - **OFF** — use Cellpose's instance masks directly (usually better when Cellpose already
    segments the image well).
  The choice is stored (`cellpose_refine`), recorded in the batch step, and honoured by the
  headless batch replay so runs reproduce. Untick it to compare raw Cellpose against the
  refined output on your own data.
### Unchanged (deliberately)
- Time-series Cellpose stays raw (`postprocess=False`, from 1.5.219). Z-stack Cellpose stays
  refined (no checkbox yet — a candidate for the same toggle later). The `cellpose_segmentation`
  function default remains `postprocess=True` so any caller not passing the flag is unchanged.

## [1.5.219] - 2026-07-05
### Fixed (time-series Cellpose segmentation — audit)
- **Time-series Cellpose now uses Cellpose's masks directly instead of destroying them.**
  `cellpose_segmentation` post-processed every result by binarizing (`masks > 0`, throwing
  away Cellpose's instance labels), re-splitting with a generic watershed, applying **7
  iterations of morphological opening**, and relabeling — which demolishes Cellpose's
  learned per-object boundaries and degrades otherwise-good output. Added a
  `postprocess=True` parameter; the time-series path now passes `postprocess=False` to use
  Cellpose's instance masks as-is. The legacy 2D path keeps `postprocess=True` (unchanged,
  its downstream steps expect the refined masks).
- **Instance labels are preserved through the upscale/downscale round-trip.** The upscaled
  branch previously did `measure.label(mask > 0)` after downscaling, re-binarizing and
  merging touching cells Cellpose had separated. It now downscales the label image with
  nearest-neighbour interpolation, keeping each cell's Cellpose ID.
- **Removed the misleading segmentation-channel hint.** The "Seg. channel" dropdown hinted
  `Enhanced Background Removed` (a condensate-optimized layer) while its own tooltip says a
  DAPI/nuclear channel is preferred — nudging users toward the wrong layer for cell
  segmentation. The hint is now cleared so it doesn't auto-pick the processed condensate
  image.
- **Keyframe progress count is now correct.** `n_kf` didn't include the final frame that
  gets appended as an extra keyframe when it isn't a natural interval boundary, so the
  progress read "x / N" against a too-small N. Both count sites now include the appended
  last frame.
### Not changed (flagged, needs separate testing)
- The 2D path's 7-iteration morphological opening is left as-is; reducing it (as suggested)
  would affect the validated 2D condensate workflow and should be tested independently.
- StarDist and Random-Forest time-series paths still `label(> 0)`; the audit targeted the
  Cellpose path only.

## [1.5.218] - 2026-07-05
### Fixed (menu label rendering)
- **"Cell & Object Analysis" → "Cell and Object Analyses".** In Qt menus an ``&`` marks the
  next character as a keyboard mnemonic and isn't rendered, which made the label display
  oddly. Spelling out "and" avoids the mnemonic entirely. Applied to both the Analysis
  Methods submenu and the matching Toolbox submenu (the latter also dropped "Condensate").

## [1.5.217] - 2026-07-05
### Fixed (method-1 status markers — second pass, items 6–9)
- **Step 2 — "Measure Line(s)" now turns green when run** and reverts to red on Clear,
  unless "Remember measurements across clears" is on (then the measurement and its done
  state carry over). Uses the new `button_with_circle` completion state.
- **Step 3 — "Run Upscaling" now turns blue when run** (it's an optional step, so blue =
  "you did this optional thing"), and reverts to yellow on Clear since its upscaled output
  layers are removed.
- **Step 7 — the cell-analysis mask dropdown now auto-greens correctly.** Its name hint was
  `Labeled Cell Mask`, but that layer is this step's *output*; the layer that actually
  feeds it is the Cellpose segmentation, named `Cellpose Segmentation on …`. The hint is
  now `Cellpose Segmentation`, so auto-population turns the circle green. Dropdown circles
  also now distinguish GREEN (selection matches the suggested/auto-filled layer) from BLUE
  (you deliberately picked a different layer, or set an optional no-hint dropdown like
  "Select Mask Layer to Omit" away from its default) — previously a user override showed
  green instead of blue.
- **Step 1 — the pixel-size marker now updates with image load/clear.** The "Image loaded"
  marker was wired to layer events, but the pixel-size gate only re-evaluated on field
  edit / data switch, so its status went stale on load/clear. Its refresh is now also
  wired to layer insert/remove, so both Step 1 markers update together.

## [1.5.216] - 2026-07-05
### Changed (method-1 UI naming + layer auto-selection — first pass)
- **Dropped "Condensate" from the analysis method titles** for branding and accuracy (these
  workflows apply to membrane-bound objects and objects from processes other than
  condensation). Analysis Methods menu: submenu "Condensate & Cell Analysis" → "Cell &
  Object Analysis"; the five entries "Cellular/In Vitro/Time-Series/Z-Stack Condensate
  Analysis" → "… Object Analysis". Method-1 panel: section "Condensate Analysis" → "Object
  Analysis" and its dock "Condensate Analysis Dock" → "Object Analysis Dock". The top
  "Cell/Nuclei Analysis" section title is unchanged. These are display-label changes only;
  internal wiring/keys are untouched.
- **Steps 7–9 now auto-select the plain "Upscaled Fluorescence Image", not a derivative.**
  After pre-processing, dropdowns that want the plain upscaled image were auto-populating
  with "Pre-Processed Upscaled Fluorescence Image" because the `Upscaled Fluorescence`
  name-hint substring-matched the longer derived name. `_hint_matches` now also rejects the
  `pre-processed`/`preprocessed` leading prefixes (alongside the background-removed ones),
  so the "Select Image for Cell Analysis" (step 7), "Select Fluorescence Image to Process"
  (step 8), and "Select Image for Puncta Measurement" (step 9) dropdowns pick the plain
  upscaled image, while step 8's pre-processing dropdown (whose hint names the modifier)
  still matches its intended layer.
- **Step 2 simplified to just "Measure Line(s)".** Removed the separate "Draw Line(s)"
  button; line drawing now auto-arms when the step is shown (the diameter Shapes layer is
  activated in add-line mode), so there's one button instead of two.
### Added (status-marker groundwork)
- `button_with_circle` can now reflect completion: a required action turns its circle green
  once run, an optional action turns it blue, and it exposes `reset()` for per-step / Clear
  reversion. (Wiring this into specific steps' run/clear behaviour — steps 2, 3, 7 — and
  the step-1 marker resets is the next pass.)

## [1.5.215] - 2026-07-05
### Fixed (images open tiny — the REAL cause: the 2-D load path never called the fit)
- **`open_2d_image` now calls the auto-fit.** The debug build (1.5.213) printed nothing
  because the fit was never invoked for 2-D images: `open_2d_image` → `load_into_viewer`
  enables the scale bar but never called `_fit_view_to_layer` — only the stack path
  (`_finalise_stack_load`) did. So plain 2-D TIFFs opened tiny and Home was the only way to
  fill the canvas, and all the earlier scale-aware fit work (1.5.210–1.5.213) simply didn't
  run for them. The fit is now called at the end of `open_2d_image` (deferred 400 ms, after
  the channel-assignment dialog and diameter-layer inserts settle), matching the stack
  path. Both single- and multi-channel 2-D loads are covered (the channel dialog is modal,
  so channels are in the viewer before the fit fires).

## [1.5.214] - 2026-07-05
### Docs
- **Recorded the scale-bar migration as a known issue + low-priority backlog item**
  (``docs/source/development/roadmap.rst``). Captures that the main image/stack load path
  uses napari's built-in ``viewer.scale_bar`` (via ``scale_bar.unit``), which works only
  because the code avoids the ``Layer.units`` call that black-outs the canvas — and that
  this is fragile against napari's ``scale_bar.unit`` deprecation (PR #9007, which moves
  the unit to ``Layer.units``) and is coupled to the auto-fit machinery. The self-contained
  ``draw_custom_scale_bar`` (a Shapes rectangle in data coords, immune to both) already
  exists but is wired only into the temperature/movie workflow; unifying on it across the
  load path is deferred as low priority, to be done before adopting a napari version that
  removes ``scale_bar.unit``.

## [1.5.213] - 2026-07-05
### Diagnostic (image-opens-small — instrument the fit)
- **Added `PYCAT_DEBUG=1` logging to the auto-fit.** Prior fixes (world-extent math, longer
  delay, mirroring Home) didn't resolve the image opening small, so the fit now logs the
  layer name, its transform-aware world extent, the canvas size, the zoom before/after the
  fit, and — via a 600 ms follow-up — whether the zoom gets changed back afterwards. This
  will show definitively whether the fit is computing the wrong zoom, not running, or being
  reset by a later event (e.g. a scale-alignment or napari auto-reset on layer insert),
  rather than guessing. No behavioural change.
### Note
- Clarified for reference: a plain 2-D image load uses napari's built-in `viewer.scale_bar`
  (via `_enable_auto_scale_bar`); the custom Shapes-based `draw_custom_scale_bar` is used
  only by the temperature/movie-export workflow.

## [1.5.212] - 2026-07-05
### Fixed (auto-fit at load — now matches the working Home button)
- **Images open fitted, not tiny.** Key diagnostic: the manual Home button fit the image
  correctly, but the auto-fit at load did not — so the math was fine and the problem was
  timing/state. The auto-fit recomputed the extent by hand (`shape × scale`), which can
  disagree with napari's real extent right after load (the µm/px scale was just assigned
  and the transform/extent cache may not have updated when the deferred fit fires).
  `_fit_view_to_layer` now reads `layer.extent.world` — the exact transform-aware extent
  the Home button uses — and the fit is deferred a little longer (400 ms) so the scale bar
  and all layer-insert scale-alignment events have settled first. Auto-fit and Home now
  behave identically.

## [1.5.211] - 2026-07-05
### Fixed (overlay X-compression — side-by-side squished into one image's width)
- **The side-by-side Overlay Image now renders at true proportions.** After the stripe
  fix, the overlay (an (H, 2W, 3) side-by-side of the plain and red-overlaid image) was
  being fit to the *reference image's* field of view, which compressed its 2W pixels into
  one image's worth of world width — squishing it ~2× in X. The overlay's pixels are the
  same physical size as the source image's (the hstack just adds columns), so it now
  inherits the source layer's per-pixel scale explicitly at creation, and
  `_align_layer_scales` gives RGB overlays the reference per-pixel scale (not FOV/shape) as
  a fallback. Each half of the side-by-side now aligns 1:1 with the data pixel size.

## [1.5.210] - 2026-07-05
### Fixed (images open tiny — auto-fit ignored the layer's µm/px scale)
- **Newly-opened images now fill the canvas.** Same class of bug as the overlay stripe:
  PyCAT sets each image layer's scale to µm/px, so a 2048-px image at 0.098 µm/px has a
  world extent of only ~201 units — but the auto-fit computed zoom from the raw 2048 pixel
  count, ending up ~10× too zoomed out (the image spanned ~88 px on a ~900 px canvas). A
  new `_fit_view_to_layer` fits from the WORLD extent (shape × scale), handles RGB layers
  (channel axis excluded), and retries until the canvas is laid out. It replaces the old
  pixel-based fit in `_finalise_stack_load`, so images open at a sensible size without
  needing to press Home. (The manual Home button was already scale-correct — it fits from
  `layer.extent.world` — so it's unchanged.)

## [1.5.209] - 2026-07-05
### Fixed (overlay stripe — the TRUE root cause: RGB channel axis treated as spatial)
- **`_align_layer_scales()` no longer treats an RGB image's channel axis as a spatial
  dimension.** This is the actual cause of the stretched "Overlay Image", found by
  analysing the scale-alignment pass rather than the overlay array (which was always
  correct). The overlay is `(H, 2W, 3)`; the alignment code used `shape[-2:]` = `(2W, 3)`,
  so it treated the 3-channel axis as X and assigned the overlay a massive x-scale
  (~16.7 world-units/px vs the data's ~0.024 — a ~680× blow-up), rendering it as a long
  stripe extending far past the data. The alignment now detects RGB/RGBA image layers
  (`layer.rgb` with a trailing axis of 3 or 4) and uses the two axes *before* the channel
  axis as the spatial shape. This function didn't exist in v1.0.0, which is why the
  overlay rendered correctly then — the overlay code was never the problem.
- The overlay is now added as `uint8` with `rgb=True`, so napari and the alignment pass
  both unambiguously recognise it as a colour image.
### Improved
- **Overlay PNG contrast** (carried from the in-progress 1.5.208): the exported
  `_puncta_overlay.png` computes its contrast-stretch window over the signal pixels
  (non-near-zero) with a high upper percentile (99.8), so the bright cell body keeps its
  detail instead of blowing out to white.

## [1.5.207] - 2026-07-05
### Fixed (overlay stripe — root cause found via git diff against 1.0.0, and reverted)
- **Restored the v1.0.0 "Overlay Image" code exactly.** A git diff of the overlay path
  against the 1.0.0 release showed `create_overlay_image` and the caller were UNCHANGED in
  the committed code — the stretched-stripe regression was introduced *during this
  session's* earlier "green stripe" fix, which dropped the final
  `dtype_conversion_func(sbs_overlay, 'uint16')` conversion and added `rgb=True`. The
  original sequence converts the (H, 2W, 3) uint8 array to uint16 and adds it WITHOUT
  `rgb=True`; napari auto-detects a (H,W,3) *uint8* array as RGB but not a *uint16* one, so
  the uint16 array renders as a normal multi-plane 2-D image at correct proportions.
  Reverting to the exact 1.0.0 lines fixes the stripe.
### Kept
- The two requested enhancements remain on top of the restored overlay: after analysis the
  Step 9 fluorescence image and the puncta mask are brought to the top of the layer list
  (mask on top, both visible), and a flat merged grayscale+red PNG is written to the source
  folder as `<base_name>_puncta_overlay.png`.

## [1.5.206] - 2026-07-05
### Changed (replaced the in-viewer Overlay Image with layer reordering + PNG export)
- **No more "Overlay Image" layer.** Every attempt to add a blended overlay as a napari
  image layer mis-rendered as a stretched strip (napari's RGB/axis handling), so the
  in-viewer overlay is gone. Instead, after Condensate Analysis:
  - **The two relevant layers are brought to the top of the layer list**, both made
    visible: the Step 9 "Select Image for Puncta Measurement" fluorescence image, with the
    selected puncta mask directly above it. This reproduces the mask-over-image overlay
    using napari's own compositing (no custom RGB layer), which always aligns and scales
    correctly.
  - **A merged grayscale + red-puncta PNG is written to the source folder** as
    `<base_name>_puncta_overlay.png` — a flat, shareable overlay (image contrast-stretched
    so dim data is visible, puncta blended in red). This is a file, so napari never renders
    it and the stretch bug can't recur.
- File path and base name are now stored in the data repository at load time so the export
  lands next to the original image.

## [1.5.205] - 2026-07-05
### Fixed (overlay image — replaced the side-by-side with an in-place overlay)
- **The Overlay Image is now a single same-size (H, W) RGB layer** with puncta painted
  red directly on the fluorescence image, instead of the old side-by-side `np.hstack`
  that produced an (H, 2W) layer. The doubled-width layer sat in napari's shared
  coordinate space alongside all the (H, W) layers and stuck out past them — the "green
  stripe extending beyond the data" that no amount of squeezing fixed, because the shape
  was working as (mis)designed. The new overlay shares the exact footprint of every other
  layer, so it aligns on the data and toggles cleanly.
- **The overlay is now visible on dim images.** The source "Upscaled Fluorescence Image"
  can be a float scaled by 1/65535 (max ≈ 0.02), which rendered nearly black. The overlay
  now contrast-stretches on the 1st–99th percentile before display, so the cell structure
  is visible with the puncta highlighted. The old `create_overlay_image` (side-by-side)
  is no longer used by the puncta workflow.

## [1.5.204] - 2026-07-05
### Diagnostic (overlay "green stripe" — instrumenting the real cause)
- **Added `PYCAT_DEBUG=1` logging to the overlay path.** Pixel-level analysis of the
  reported screenshot showed the overlay is actually ~2:1 aspect (a correct side-by-side
  shape), NOT the 4-D "stripe" the earlier squeeze fix targeted — the visible content is
  mostly black with a bright green horizontal band, which points to the *input image*
  being wrong (e.g. an over-subtracted/near-black layer, or a green-channel normalisation
  blow-out) rather than a dimensional bug. The overlay now logs the image layer name, raw
  and squeezed shapes, dtype, min/max, and non-zero fraction, plus the mask non-zero
  count, so the next run pins down exactly what is being visualised. No behavioural change
  to the overlay itself.

## [1.5.203] - 2026-07-05
### Fixed (line/ROI drawing does nothing when the layer is hidden)
- **Arming line or ROI drawing now makes the target layer visible first.** napari silently
  ignores the drawing tool on a hidden Shapes layer, so after toggling layers off the
  "Draw Line(s)" / "Add ROI Drawing Layer" actions appeared to do nothing. Both now set
  `layer.visible = True` (and restore a usable opacity for the diameter layer) before
  activating draw mode.
### Added (nuclei segmentation model for Step 5)
- **"Use nuclei model" checkbox under Cellpose in the time-series Step 5.** The default
  Cellpose model (cyto2 / cpsam) is a CYTOPLASM model; on a nuclear stain like DAPI it
  merges all nuclei into one giant region because there's no cytoplasm structure to bound
  them (the reported "DAPI segments into one giant area"). The checkbox routes Cellpose to
  its dedicated 'nuclei' model, which is the correct choice for DAPI/Hoechst. Threaded
  through `run_keyframe_cellpose` → `cellpose_segmentation(model_name='nuclei')`. Shown
  only when Cellpose is the selected method; diameter is unchanged for now (test the model
  effect in isolation first). On Cellpose 4 (where the nuclei CNN doesn't exist as a
  separate model) the user is warned and the default model is used, with a pointer to
  install cellpose<4 for a dedicated nuclei model.
### Notes
- The GFP channel returning no segmentation on untransfected cells is expected biology,
  not a bug: GFP only marks transfected cells, so a GFP-based segmentation can only find
  those. Segment on a channel that labels all cells (a nuclear stain, or brightfield) to
  capture every cell.

## [1.5.202] - 2026-07-05
### Fixed (four issues from user testing)
- **Home / fit-to-view at file open now works reliably.** The auto camera-fit fired once
  at a fixed 100 ms delay; if the canvas wasn't laid out yet (dock still arranging) it
  read a zero size and fell back to `reset_view()`, which the code itself notes is
  unreliable — so the image often opened not fitted. It now retries with growing delays
  until the canvas has a real size, calls `reset_view()` first (correct for 2D and 3D/T
  stacks), then tightens center/zoom from the known spatial dimensions.
- **Overlay "wide green stripe" is now impossible to add.** In addition to the earlier
  squeeze fix, the overlay is now added with `rgb=True` (so napari treats the last axis
  as RGB channels, never as a 3-slice stack) and a final shape guard: if the composited
  array isn't a clean `(H, W, 3/4)` image it is skipped with a warning rather than added
  as a malformed layer. Analysis results are unaffected either way.
- **Downstream dropdowns no longer grab the wrong derived layer.** Auto-selection matched
  `name_hint` as a plain substring, so a hint of `Upscaled Fluorescence` also matched
  `Enhanced Background Removed Upscaled Fluorescence Image` — causing the
  background-removed layer to auto-populate dropdowns that wanted the plain upscaled
  image. New `_hint_matches` rejects a layer that carries an EXTRA leading modifier prefix
  (`Enhanced Background Removed`, `Background Removed`) the hint didn't ask for, while
  still matching when the hint itself names that modifier.
- **Status circles no longer turn green prematurely.** A dropdown defaults to its first
  item (a real layer), which made the row's status circle read as satisfied before the
  user chose anything — and green on the wrong layer via the substring bug above. The
  circle now turns green only when the selection actually matches the row's `name_hint`
  OR the user deliberately picked an item (tracked via `QComboBox.activated`, which
  doesn't fire on the implicit index-0 default). Dropdowns without a hint are unchanged.

## [1.5.201] - 2026-07-05
### Fixed (real cause of the multi-second stall when adding an ROI layer to a lazy IMS stack)
- **Lazy IMS layers are now added with explicit `contrast_limits` computed from their
  first frame.** The stall was NOT the world-extent recompute (that's cheap shape
  arithmetic and never touches pixels). It was napari auto-estimating contrast limits and
  building the layer thumbnail by calling `np.asarray()` on the lazy `(T,Y,X)` wrapper,
  which triggers `__array__` and loads EVERY frame from disk — slow on a USB-HDD IMS
  stack, and re-triggered whenever the layer list refreshes (such as when an ROI Shapes
  layer is added). Passing `contrast_limits` up front (from the single first frame, which
  is already read) stops napari from probing the whole stack. Applied to all three lazy
  IMS paths (T,Y,X / Z,Y,X / T,Z,Y,X). The first frame is reused from the existing
  probe-read for channel 0, so no extra disk reads for that channel. Users can still
  adjust contrast normally afterwards.
### Notes
- Deliberately did NOT change the wrappers' `__array__` to return a single frame: that
  method loading the full stack is *correct* for genuine full-array operations, and
  short-circuiting it would silently make real analyses operate on one frame. The fix
  targets only the incidental thumbnail/contrast probe, via `contrast_limits`.

## [1.5.200] - 2026-07-05
### Fixed (line drawing tool becomes unusable after clicking elsewhere)
- **Added a "✏ Draw Line(s)" button that re-arms line drawing.** Clicking an image
  layer's eye icon (napari default) makes that image the active layer, which silently
  disables line drawing on the diameter Shapes layer even though it still looks selected
  in the layer list — `update_tool` only sets `add_line` mode on a selection *change*, so
  re-selecting doesn't always re-fire. The new button deterministically activates the
  correct diameter Shapes layer (preferring one with no lines drawn yet) and sets
  `add_line` mode, so drawing is always one click away regardless of what selection
  detour happened.
### Improved
- **"Add ROI Drawing Layer" no longer freezes on the button press.** On a large lazy IMS
  stack, adding a Shapes layer makes napari recompute the world extent, which took a
  noticeable moment mid-click. The layer creation is now deferred by one event-loop tick
  with a wait cursor, so the click feels responsive. (The extent recompute itself is
  napari-internal; this removes the frozen-button feel rather than the underlying cost.)
### Housekeeping
- Removed a duplicated `_add_widget_to_layout_or_dock` call in `_add_measure_line` that
  added the measure widget twice.

## [1.5.199] - 2026-07-05
### Fixed (overlay image rendered as a wide green stripe — the real root cause)
- **The overlay now squeezes its input to 2-D before compositing.** The 1.5.184 fix
  addressed float-clipping (`img_as_uint`) but not the actual cause of the stripe on
  processed data: the "Upscaled Fluorescence Image" layer carries a leading singleton
  axis (shape `(1, H, W)`) from the loader's T/C dimension handling. Passing that to
  `create_overlay_image` produced a 4-D array `(1, H, 2W, 3)`, which napari renders as a
  wide, short green stripe instead of a side-by-side overlay. `run_puncta_analysis_func`
  now `np.squeeze`s the image (and the puncta·cell mask) to a plain 2-D plane first,
  falling back to the first plane for any genuine multi-frame input and to a zero mask on
  a post-squeeze shape mismatch. Validated: `(1,1024,1024)` input now yields a correct
  `(1024, 2048, 3)` overlay instead of the malformed 4-D stripe.

## [1.5.198] - 2026-07-05
### Fixed (RuntimeError spam: "wrapped C/C++ object of type QComboBox has been deleted")
- **Layer dropdowns no longer fire callbacks after their widget is destroyed.**
  `_layer_row` (status-circle rows) and `create_layer_dropdown` both connect handlers to
  the viewer-level `layers.events.inserted` / `removed` signals, which outlive the
  dropdown. When a workflow was torn down and its `QComboBox` deleted, a subsequent layer
  insertion/removal still invoked those handlers against the dead C++ object, raising
  `RuntimeError: wrapped C/C++ object of type QComboBox has been deleted` — repeatedly,
  flooding the console. Fixed two ways (belt and suspenders): (1) each dropdown now
  disconnects its viewer-signal handlers on `destroyed`, and (2) `_update_circle`,
  `_on_inserted`, and `update_dropdown_items` guard their `QComboBox` access with
  `except RuntimeError` so any stale call that still slips through is a harmless no-op.
  Also removed dead introspection code (`for conn in ...events.inserted._slots: pass`)
  left in `_layer_row`.

## [1.5.197] - 2026-07-05
### Added
- **Colormap reset toggle on the PyCAT toolbar.** A new "🎨 Gray / Viridis" button
  flips every image layer between grayscale and viridis in one click. IMS/multichannel
  loads assign per-channel colors (blue/green/red/magenta) which are harder to read for
  intensity inspection; this gives a one-click neutral view. Label and mask layers are
  left untouched (their colormaps are categorical). The button label reflects the
  colormap the next click will apply.

## [1.5.196] - 2026-07-05
### Fixed (IMS channel names fell back to generic positional colors)
- **IMS channel identity now read from the HDF5 `DataSetInfo/Channel N` group
  attributes.** `extract_channel_info_from_ims` previously scanned the unreliable
  `reader.metaData` dict, which for real Fusion/Imaris files omits per-channel info —
  so every IMS channel fell through to the positional fallback (C0-blue, C1-green,
  C2-red, C3-far_red), which is wrong whenever the acquisition order isn't the standard
  blue/green/red/far-red. It now reads each channel's stored `Name` (e.g.
  `405_DAPI_CF40um_z`, `488_GFP_CF40um_z`, `594_mCherry_CF40`, `BFPreAm`) and
  `LSMExcitation/LSMEmissionWavelength` directly from the h5py handle, per index, then
  runs them through the existing three-tier identifier. Falls back to the metaData scan
  and then position only if the handle read yields nothing.
- **Channel-name matching fixed for underscore/digit-delimited names.** The fluorophore
  patterns used `\b` word boundaries, which do NOT fire between an underscore and a
  letter (underscore is a word char) — so `488_GFP_CF40um_z` and `594_mCherry_CF40`
  matched nothing. Patterns now use non-letter delimiters `(?:^|[^a-z])…(?:[^a-z]|$)`,
  correctly matching the fluorophore token wherever it sits in the name. Added
  `BFPreAm`/`BFPreAmp` and bare `BF` as brightfield/transmitted patterns, plus mScarlet
  and tdTomato.
- Added `raw_name` to the channel-identification result and a debug log line
  (`PYCAT_DEBUG=1`) reporting the resolved name/label/bucket per IMS channel, so a
  name↔index mismatch (stored acquisition name not matching the physical sample) can be
  diagnosed vs. a PyCAT parsing bug.

## [1.5.195] - 2026-07-05
### Changed (menu-bar clarity — distinguish PyCAT menus from napari's)
- **Added a bold "◆ PyCAT ▸" section marker** on the menu bar, immediately before
  PyCAT's menus (Analysis Methods, Toolbox, ★ Open/Save File(s), Clear, Home, Metadata).
  PyCAT's menus are appended to napari's native menu bar (File/View/Plugins/Window/Help),
  and without a visual break users couldn't tell where napari ended and PyCAT began. The
  marker is a non-clickable, bold, accent-coloured divider so everything to its right
  reads clearly as PyCAT. The menus themselves are unchanged (kept as dropdowns), so no
  wiring is affected. Noted as a candidate for a fuller toolbar redesign later.

## [1.5.194] - 2026-07-05
### Added (unified metadata extraction, viewer widget, and results export)
- **New `pycat/file_io/metadata_extract.py`** — a single normalisation layer that
  extracts acquisition metadata from any supported format (TIFF/OME-TIFF via tifffile,
  CZI/OME via AICSImage, IMS via HDF5 attributes) into a consistent record with a
  curated `common` block (pixel size + source, dimensions, bit depth, channels,
  timepoints, Z, objective, numerical aperture, modality, excitation/emission
  wavelengths, acquisition date, software) and a `raw` block containing every
  key/value the file exposes. Every extractor is defensive — missing fields yield
  None rather than raising.
- **IMS metadata is no longer discarded.** Previously `update_metadata` was only
  called on the AICSImage path, so all IMS acquisition metadata (objective, NA,
  modality, wavelengths, recording date, gain) was thrown away. Both load paths now
  store a normalised record in `data_repository['file_metadata']`. On the multichannel
  IMS test file this recovers 63x objective, NA 1.4, Spinning Disk Confocal, 521 nm
  ex/em, 0.0957 µm/px.
- **Metadata viewer** — a new "ⓘ Metadata" menu-bar action opens a dialog showing the
  curated fields, with a "Show all raw metadata" checkbox that reveals the full dump,
  and an "Export JSON…" button.
- **Metadata exported with results.** `save_and_clear_all` now writes
  `<name>_metadata.json` alongside the results CSVs, tying acquisition provenance to
  every analysis output (supports the reproducibility story).
### Housekeeping
- Removed stray `file_io.py.orig` / `file_io.py.rej` patch artifacts that were sitting
  in the source tree.

## [1.5.193] - 2026-07-05
### Fixed (IMS pixel size not read from spatial extents)
- **Pixel size is now recovered robustly from IMS `ExtMax0`/`ExtMin0` extents.** The
  old code called `reader.read_numerical_dataset_attr('ExtMax0')` inside a bare
  `except: pass`, which silently swallowed failures — including on files whose extents
  are stored as fixed-length ASCII char arrays with negative stage coordinates (e.g.
  `b'-42107.8'`), a case the reader's accessor mishandles. New helper
  `_ims_pixel_size_um` reads the `DataSetInfo/Image` extents directly from the h5py
  handle (`reader.hf`), decodes the char array to a float explicitly, and computes
  `(ExtMax0 - ExtMin0) / width`, falling back to the reader accessor only if the handle
  route fails. On the multichannel time-series test file this correctly recovers
  0.0957 µm/px (196 µm across 2048 px). Unitless/absurd values are rejected.
### Notes
- Confirmed the time-series condensate analysis and the 2D condensate method share the
  same segmentation engine (`segment_subcellular_objects`) and the same preprocessing
  (`pre_process_image` + `rb_gaussian_bg_removal_with_edge_enhancement`) — the science
  has not forked. The one intentional difference is that the time-series path passes
  `cell_df=None` (so the per-cell low-SNR background-removal-skip branch never fires;
  every cell gets background removal). A fuller methods-coherence review of this
  difference is deferred to the planned methods audit.

## [1.5.192] - 2026-07-05
### Fixed (TIFF pixel size not read from resolution tags)
- **Pixel size is now recovered from baseline TIFF resolution tags when AICSImage
  misses it.** AICSImage's `physical_pixel_sizes` reads only OME-XML and ImageJ
  metadata, not the standard `XResolution`/`YResolution`/`ResolutionUnit` tags. Many
  microscope-exported TIFFs (confirmed on real GFP/DAPI test files) store pixel size
  ONLY in those baseline tags, so AICSImage returned None and PyCAT fell back to
  1.0 µm/px, forcing the user to enter the scale manually in the pixel-size gate. New
  helper `_tiff_pixel_size_um` reads the tags directly: XResolution is a RATIONAL
  (pixels per unit), ResolutionUnit 3 = cm / 2 = inch. On the real test files this
  correctly recovers 0.097656 µm/px (a 50 µm field of view across 512 px). Wired into
  both the AICSImage path (as a fallback when it returns 1.0) and the direct-tifffile
  fallback path. Unitless tags (ResolutionUnit = 1) and absurd values are rejected so a
  bad tag can't set a nonsense scale.

## [1.5.191] - 2026-07-05
### Documentation
- Added a super-resolution data processing workflows section to the roadmap. Structured
  around the critical scientific distinction that "super-resolution" spans two different
  data models: **Category A — image-based / raster-grid SR** (deconvolution, SRRF, SOFI,
  SIM reconstruction) that consumes an image sequence and emits an enhanced raster image,
  which is drop-in compatible with PyCAT's existing pipeline as advanced preprocessing;
  and **Category B — localization-table SR** (PALM/STORM/dSTORM, DNA-PAINT/PAINT family)
  that emits a coordinate list, not an image, and needs its own data type and
  localization-native operations. Notes the sequencing (Category A near-term, especially
  deconvolution then SRRF/SOFI, reusing lazy loading + batch/replay; Category B a larger
  post-publication addition scoped only if a real user presents localization data) and the
  strongest integration argument (PyCAT's point-based spatial-phenotyping statistics
  already cover most of what localization-cluster analysis needs).

## [1.5.190] - 2026-07-05
### Fixed (release hygiene — clean sdist)
- **Source tarballs no longer include VCS/cache/build cruft.** The project uses hatchling,
  which ignores `MANIFEST.in` (a setuptools mechanism) — so the `global-exclude` rules
  there (`.DS_Store`, `.pytest_cache/`, `__pycache__/`, etc.) were never applied to the
  sdist. Added an explicit `[tool.hatch.build.targets.sdist]` section with `include` and
  `exclude` lists, so `python -m build` now produces a clean tarball by construction
  (no `.git/`, `__pycache__/`, `.coverage`, `.DS_Store`, `dist/`, `PKG-INFO`). The wheel
  was already clean.
### Documentation
- Recorded the external architecture review (2026-07) in the roadmap: platform
  consolidation sequencing (spatial-phenotyping menu grouping, stability tiers,
  biological-relevance tooltips, shared output schema, deferred module registry), the
  highest-value test additions, and this release-hygiene fix. Key insight: the UI
  monolith, batch-registry monolith, and output-schema gap are one refactor —
  self-describing modules — and the shared output schema is the foundational piece to
  build first.

## [1.5.189] - 2026-07-05
### Fixed (macOS support — Apple Silicon GPU + platform-aware messages)
- **Cellpose now uses the Apple Silicon GPU (Metal/MPS) when available.**
  `_get_cellpose_gpu()` previously checked only `torch.cuda.is_available()`, which is
  always False on M1/M2/M3 Macs, forcing CPU even when a Metal-capable PyTorch was
  installed. It now also checks `torch.backends.mps.is_available()` and returns True
  for MPS, so `CellposeModel(gpu=True)` is passed on Apple Silicon and Cellpose uses
  the GPU automatically. The detected backend ('cuda' / 'mps' / None) is cached in
  `_CELLPOSE_GPU_BACKEND`.
- **CPU-fallback warning is now platform-aware.** On Mac it no longer tells users to
  `pip install torch --index-url .../cu118` (a Windows/Linux-only CUDA wheel that does
  not exist for macOS). Mac users are instead told that installing an MPS-capable
  PyTorch enables the Apple GPU automatically, and that there is no CUDA on Mac.
### Notes (Mac install guidance — no code change)
- On Apple Silicon, install via conda-forge rather than pure pip: `simpleitk` and
  `numba` (llvmlite) arm64 wheels are on conda-forge but not reliably on PyPI, so a
  pip-only install can fail at dependency resolution before PyCAT ever runs.

## [1.5.188] - 2026-07-05
### Fixed (auto-home on image load — direct camera set instead of reset_view)
- **Images now reliably fill the canvas on load.** The 1.5.184 implementation used
  `viewer.reset_view()` via a 150ms QTimer, which silently did nothing if napari had
  not yet finished computing the layer extent. The new implementation uses the same
  direct camera-set approach as the Home button: reads the canvas pixel dimensions
  from ``viewer.window._qt_viewer.canvas.size``, computes
  ``zoom = min(ch/H, cw/W) * 0.9`` from the known image H and W, then sets
  ``viewer.camera.center`` and ``viewer.camera.zoom`` directly. This is independent
  of napari's internal extent computation timing and matches exactly what pressing
  Home does. Falls back to ``reset_view()`` if the canvas size cannot be read.

## [1.5.187] - 2026-07-05
### Fixed (hardware-dependent segmentation — GPU/CPU algorithm inconsistency)
- **`compute_rolling_ball_background` now uses the same algorithm on GPU and CPU.**
  Previously, the GPU path used morphological opening (grey erosion + dilation with a
  disk footprint of radius ``ball_radius``) while the CPU path used
  ``skimage.restoration.rolling_ball`` — a genuinely different algorithm that treats
  pixel intensity as a literal extra spatial dimension and is sensitive to the image's
  numeric range in a way plain morphological opening is not. This caused different
  segmentation outcomes on identical data depending on which hardware ran, a silent
  reproducibility failure confirmed by a user on CPU-only hardware. Both paths now use
  the same morphological-opening algorithm (disk of radius ``ball_radius``). Also
  removed the redundant ``ndi.gaussian_filter`` inside this function: the caller
  ``rb_gaussian_bg_removal_with_edge_enhancement`` already applies the same Gaussian
  to the background estimate, so the previous code was smoothing twice with the same
  sigma, spreading the background estimate into real signal and causing over-subtraction.
- **Reverted the 1.5.183 change** (``bg_removed_crop = proc_crop`` fallback). The
  zeros fallback is correct — the algorithm-consistency fix above is what actually
  resolves the segmentation failure on CPU-only machines.

## [1.5.186] - 2026-07-05
### Documentation
- Added "USB HDD lazy-loading latency" to Known Issues in roadmap. Lazy-loading IMS
  or large TIFF/HDF5 files from a USB 2.0 spinning HDD causes ~250–300 ms per-frame
  lag when scrubbing Z/T sliders (~8 MB/frame at ~30 MB/s). Documented the per-bus
  bandwidth breakdown (USB 2.0 / 3.0 / 3.1), three immediate workarounds (check port
  colour/SS label, copy locally first, pre-load the relevant range), and noted that the
  planned LRU frame cache (already on the roadmap) is the primary software-side
  mitigation for repeated scrubbing of already-visited frames.

## [1.5.185] - 2026-07-05
### Fixed (IMS loading — singleton axis squeeze + robust indexing)
- **`_ImsReaderTYX/ZYX/TZYX`: singleton axes from `imaris_ims_file_reader` now
  stripped correctly.** With `squeeze_output=False`, direct reads such as
  `reader[0, c, 0, :, :]` may return shape `(1, 1, 1, Y, X)` instead of `(Y, X)`,
  causing `ValueError: axes don't match array` in napari. New helper `_ims_frame_2d`
  calls `np.squeeze` and validates the result is exactly 2-D before returning.
- **Robust `__getitem__` for all three classes.** New `_ims_indices` helper converts
  any selector (int, slice, list, Ellipsis) to a concrete list; each class reads
  frame-by-frame and stacks, returning a scalar-indexed plane or a stacked array
  exactly as napari expects.
- **`_ImsReaderTZYX` z-squeeze fix over the submitted patch.** The patch's original
  `arr[:, 0]` squeeze for a scalar Z selector on a `(1, 1, Y, X)` array after T-squeeze
  produced `(1, X)` instead of `(Y, X)`. Fixed to squeeze Z before T, so all three
  indexing modes (`[t, z]` → (Y,X), `[t, :]` → (Z,Y,X), `[:, :]` → (T,Z,Y,X))
  produce the correct shapes. Validated numerically.

## [1.5.184] - 2026-07-05
### Fixed (five UX issues from user report)
- **Overlay image rendered as a green stripe.** Two compounding bugs: (1) the green
  channel was converted with `dtype_conversion_func(..., 'uint16')` which calls
  `img_as_uint` on a float32 image with values outside [-1,1], collapsing it to a
  flat array; (2) the final `dtype_conversion_func(sbs_overlay, 'uint16')` ran on the
  uint8 RGB output of `create_overlay_image`, rescaling 0–255 to 0–65535 and
  destroying the composite. Both fixed: `_to_uint16_safe` is now used for the green
  channel, and the second conversion is removed entirely.
- **Images open small — no auto-zoom.** `_finalise_stack_load` now calls
  `viewer.reset_view()` (deferred 150 ms via QTimer so the layer extent is computed
  first), equivalent to pressing the Home button after every file open.
- **Napari notifications persist through Clear.** `_clear_everything` now clears the
  notification manager's record list so stale "Processing cell 3 of 3" messages from
  the previous session don't persist into the next.
- **Status circles (red/yellow) don't turn green when a layer is selected via
  auto-populate.** `_layer_row` now also connects `_update_circle` to the
  `layers.events.inserted` signal (deferred via QTimer so the dropdown index has
  already updated). Previously, if auto-selection via `name_hint` landed on index 0
  with no index change, Qt suppressed `currentIndexChanged` and the circle stayed red.
- **Dropdown auto-population corrected per step:**
  - Step 6 (Cellpose): hints to `'Upscaled Segmentation'` image
  - Step 7 (Cell Analyzer): hints to `'Upscaled Fluorescence'` image
  - Step 8 pre-processed input: hints to `'Enhanced Background Removed'`
  - Step 8 fluorescence input: hints to `'Upscaled Fluorescence'`
  - Step 9 puncta image: hints to `'Upscaled Fluorescence'`

## [1.5.183] - 2026-07-05
### Fixed ("Cell X has low contrast" on dim images even after 1.5.179)
- **Root cause: `perform_bg_removal = False` set `bg_removed_crop` to a zero array.**
  When the Cell Analyzer measures a cell's `gaussian_snr_estimate < 1.0` (common on dim
  images such as the GFP test image with int16 max ~1280), the segmentation code skipped
  background removal and assigned `bg_removed_crop = np.zeros_like(orig_crop)`. A zero
  array trivially passes `check_contrast_func` as "no contrast", producing "Cell X has
  low contrast, likely has no puncta" and 0 objects — even when real condensates are
  visible. Fixed: when background removal is skipped due to low SNR, `bg_removed_crop`
  now falls back to `proc_crop.astype(float32)` (the pre-processed image directly),
  matching what the `_already_enhanced` branch already does. This gives Felzenszwalb
  segmentation a real signal to work with rather than zeros, preserving any genuine
  puncta in dim cells.

## [1.5.182] - 2026-07-05
### Fixed (IMS loading — direct reader replaces broken zarr-store adapter)
- **IMS files now load via the direct ``imaris_ims_file_reader.ims`` reader for all
  three lazy cases (T,Y,X / Z,Y,X / T,Z,Y,X), bypassing the zarr-store adapter
  entirely.** The adapter's ``__getitem__`` could raise ``KeyError: '0.0.0.0.0'`` for
  valid IMS chunk keys when the file lives on Box Drive, a network share, or is held
  open by Imaris (via ``h5py`` raising ``OSError: Can't synchronously read data``).
  New classes — ``_ImsReaderTYX``, ``_ImsReaderZYX``, ``_ImsReaderTZYX`` — have the
  same external interface (shape, dtype, ndim, __getitem__, __len__, __array__,
  transpose) so napari treats them identically and laziness is fully preserved: only
  the frame the user scrubs to is read from disk. Patch authored externally; applied
  on top of the defensive wrapper added in v1.5.177.
- Added ``import hdf5plugin`` before ImsReader instantiation, registering bundled HDF5
  compression filters needed by some IMS files to decode pixel data.
### Known issue noted
- The direct-reader path has no internal chunk cache (the zarr adapter had one). For
  interactive use this is imperceptible; for batch workflows that re-read the same
  frames in a tight loop it may add I/O overhead. Flagged in roadmap as a future
  LRU-cache addition to the ``_ImsReader*`` classes.

## [1.5.181] - 2026-07-05
### Fixed (SyntaxError preventing startup)
- **`segmentation_tools.py` caused a SyntaxError on startup in Python 3.12 on
  Windows.** Two issues combined: (1) em-dash characters (`—`) in comments and
  docstrings are valid UTF-8 but Python 3.12's default tokeniser on Windows rejected
  them without an explicit encoding declaration; added `# -*- coding: utf-8 -*-` to
  the file header. (2) An earlier str_replace that inserted the `run_segment_subcellular
  _objects` guard block only replaced the function signature line, leaving the old
  docstring body (Parameters / Raises / Notes sections) orphaned as unreachable text
  *after* a `return` statement, with its closing `"""` creating an unmatched
  triple-quote that caused an "unterminated triple-quoted string literal" SyntaxError
  detectable only at runtime. Both issues now fixed; full tree compiles clean.

## [1.5.180] - 2026-07-05
### Changed (Clear now returns to true blank state; opt-in measurement persistence)
- **Clear and Save & Clear now restore the true initialization state** — ball_radius,
  object_size, and cell_diameter reset to their constructor defaults (75, 50, 100)
  exactly as `_initialize_repository` specifies. The 1.5.178 unconditional
  measurement-preservation was reverted; it was addressing the wrong root cause
  (the real bug was the `img_as_uint` clipping fixed in 1.5.179).
- **"Remember measurements across clears" checkbox added to the Measure Line widget.**
  Off by default (true reset). When ticked, ball_radius, object_size, and cell_diameter
  are preserved across Save & Clear and Clear, so users processing a batch of images
  from the same experiment don't need to re-measure each time. The flag lives on
  CentralManager (survives individual clears, resets on restart), following the same
  pattern as "Keep this pixel size for the session" on the pixel-size gate.

## [1.5.179] - 2026-07-05
### Fixed (connected bugs: "Cell X has low contrast" + "0 objects after refinement" on second run)
- **Root cause: `sk.util.img_as_uint` clips float32 values outside [-1, 1].** Background-
  removed and CLAHE-processed images are float32 with values e.g. [0, 1500] — well outside
  the [-1,1] range `img_as_uint` requires. When passed through `dtype_conversion_func(...,
  'uint16')`, all values collapse to the uint16 floor/ceiling, producing a flat array.
  Three downstream effects all trace to this single root cause:
  1. **`check_contrast_func`** received the flat uint16 array → `max - min ≤ 2` → returned
     `True` (no contrast) → `"Cell X has low contrast, likely has no puncta"` even on images
     with clear condensates. **Fixed**: `check_contrast_func` now works directly on the raw
     float values with a relative contrast threshold (range < 0.1% of magnitude), never
     calling `img_as_uint`.
  2. **`puncta_refinement_filtering_func`** and **`_fast` variant** built `original_image_16`
     / `processed_image_16` with the same broken conversion → `np.std(local_pixels) < 2` →
     every object dropped before kurtosis/SNR checks even ran → 0 objects. **Fixed**: a new
     `_to_uint16_safe` helper normalises any float image to [0, 1] before conversion,
     preserving relative intensity differences while satisfying `img_as_uint`'s contract.
     Verified: `std` goes from ~0 to ~24 000 on a [0, 1500] float image.
  3. **`apply_watershed_labeling`** had the same broken conversion. **Fixed** with
     `_to_uint16_safe`.
- Both reported symptoms — "Cell 3 is a low contrast image" on a new image after Clear,
  and "0 objects after refinement filtering" on a second run of the same image — are the
  same bug hitting at different stages depending on how early the flat array is encountered.

## [1.5.178] - 2026-07-05
### Fixed (condensate segmentation fails with 0 objects after Save & Clear)
- **Root cause identified and fixed: `ball_radius` resets to 75 after Save & Clear,
  which is ~10× too large for typical condensates.** `reset_values(clear_all=True)`
  restores `ball_radius=75`, `object_size=50`, `cell_diameter=100` — the hardcoded
  constructor defaults. When the user re-runs Step 8 (Condensate Segmentation) on a
  second image without first re-doing Step 2 (Measure Line), the CLAHE kernel
  (`4 × ball_radius = 300 px`) and local threshold window (`ball_radius = 75 px`) are
  tuned for objects ~100 px in diameter — ~10× larger than real condensates — causing
  the segmentation to produce 0 objects before refinement even runs. The
  "0 objects after refinement filtering" warning then lists threshold tuning as the
  likely cause, which is misleading when the real issue is an un-reset ball_radius.
  Two fixes:
  1. **`ball_radius`, `object_size`, `cell_diameter`, `microns_per_pixel_sq`, and
     `pixel_size_from_metadata` are now preserved across Save & Clear and Clear.**
     These are measurement-derived and should persist when loading a second image
     from the same experiment, so re-measuring is no longer required each time.
  2. **Guard in `run_segment_subcellular_objects`**: if both `ball_radius` and
     `object_size` are exactly at their constructor defaults (75 and 50), the function
     aborts with a clear actionable message pointing to Step 2 (Measure Line) rather
     than running and producing 0 objects with a misleading threshold-tuning suggestion.
  Reported by user running the `In Cell 1-GFP.tif` test image a second time after
  Save & Clear: first run segmented correctly, second run produced 0 objects.

## [1.5.177] - 2026-07-05
### Fixed (IMS loader — Box Drive / network share read failure)
- **IMS files on Box Drive or network shares no longer crash the loader.** The probe
  read (`lazy_tyx[0]`, `lazy_zyx[0]`) that was done eagerly at load time to set
  default diameter estimates could raise an `OSError: Can't synchronously read data`
  (h5py) → `KeyError: '0.0.0.0.0'` (imaris_ims_file_reader) when the HDF5 file is
  not fully materialised locally (Box Drive stub), held open by Imaris, or on a slow
  network share. Previously this aborted the entire load and showed the opaque message
  `"Failed to open stack: '0.0.0.0.0'"` — the layer never appeared at all.
  Now: the probe read is wrapped in a try/except; on failure a clear warning is shown
  (`"ensure it is fully downloaded locally — right-click → Make Available Offline in
  Box Drive"`) and `channel_data` falls back to a zero array of the correct spatial
  size (derived from IMS metadata, which always loads first). The napari layer is still
  added lazily and will load correctly when the user scrubs to a frame. Reported by
  Shamli Manasvi (file: T=5, C=4, Z=1, 2048×2048 IMS on Box Drive).

## [1.5.176] - 2026-07-05
### Fixed (missing patches applied to uploaded 1.5.175 base)
- **ImportError on startup** — `spida_tools.py` and `spida_ui.py` were missing
  from the repository while `ui_modules.py` imported them; PyCAT would not start.
  Both files now present.
- **NameError `QSizePolicy` in `spatial_randomness_tools.py:359`** — used but not
  in the local import block. Added.
- **NameError `napari_show_info` in `image_processing_tools.py:619`** — only
  `show_warning` was imported; upscaling success notification would crash.
  Added `from napari.utils.notifications import show_info`.
- **Mean and Additive multi-merge produce identical results** (`layer_tools.py`) —
  per-result min-max normalisation cancelled the ÷N factor. Fixed to clip to the
  input dtype's range and scale by a fixed maximum.
- **Missing builder methods** `_add_run_expand_labels` and `_add_run_mask_logic_merge`
  in `ui_labels_mixin.py` — both were in the Labeled Mask Tools menu but no builder
  existed, causing an AttributeError when opened. Added builders; added
  `run_expand_labels` and `run_mask_logic_merge` to the label_and_mask_tools import.
### Added (features carried from the audit session)
- **Fibril Analysis** (`fibril_tools.py` + `fibril_ui.py`): four-panel analysis
  (bead-on-fibril detection, morphometry, before/after registration, crossing-node
  graph theory). Added to Toolbox → Spatial Metrology menu.

## [1.5.175] - 2026-07-05
### Added (Number & Brightness — camera / widefield counterpart to SpIDA)
- **New molecular-counting method: Number & Brightness (N&B)** (Toolbox → Advanced
  Analysis → Molecular Counting → Number & Brightness). The camera/time-series
  counterpart to SpIDA (Digman et al., Biophys. J. 94:2320, 2008), for
  widefield / TIRF / spinning-disk / sCMOS data where SpIDA's confocal assumptions
  don't hold.
  - `nb_tools.py`: per-pixel temporal mean/variance → brightness (ε = σ²/⟨I⟩) and
    number (n = ⟨I⟩²/σ²) maps, with scalar detector correction
    (ε = (σ²−σ²_read)/(S·(⟨I⟩−offset))). Validated against synthetic time-series:
    <2% recovery of known number and brightness.
  - **Global bleaching detrend** (multiplicative frame rescaling that preserves
    per-pixel fluctuations — the correct N&B correction, not per-pixel subtraction).
    No-bleach control recovers exactly; mild bleaching within ~10%.
  - Outputs per-pixel **brightness and number maps** as new layers plus an ROI (or
    whole-frame) summary, with optional oligomeric-state readout against a monomer
    reference. Scalar gain/offset/read-variance now (suited to the lab's Kinetix
    sCMOS); a per-pixel variance-map correction is a documented future extension.
  - Guardrails: warns on 2D (non-time-series) input, too few frames, and apparent-
    (uncorrected) brightness; notes the exchange-between-frames and bleaching
    assumptions.

## [1.5.174] - 2026-07-05
### Added (SpIDA modality guardrail)
- **SpIDA now has an acquisition-modality selector and guardrail.** A "Acquisition
  modality" dropdown (Confocal / TIRF / Widefield) drives `check_modality()`:
  widefield raises a strong warning that SpIDA's density/brightness are not valid on
  unsectioned camera data (no beam focal volume, out-of-focus light distorts the fit
  variance, PMT noise model doesn't apply) and points to Number & Brightness as the
  camera/time-series alternative; TIRF is allowed with a camera-noise caveat. A
  data-driven heuristic also flags a high flat background floor (typical of widefield
  haze) even when confocal is selected. Modality warnings are echoed into the result
  summary.

## [1.5.173] - 2026-07-05
### Added (SpIDA — Spatial Intensity Distribution Analysis)
- **New molecular-counting method: SpIDA** (Toolbox → Advanced Analysis →
  Molecular Counting → SpIDA). Estimates fluorescent particle **density** (N,
  particles/beam-area) and **quantal brightness** (epsilon) from the pixel-intensity
  histogram of a confocal-image ROI, and — after a monomer calibration — reports the
  **oligomeric state** (epsilon / epsilon_0; ~1 monomer, ~2 dimer).
  - `spida_tools.py`: the histogram model is a direct port of the authors' reference
    MATLAB implementation (Godin et al. 2011, `SpIDA_Functions.m`) with its three
    numerical regimes (Gaussian for N>70, generalized-Poisson for N>6, blended 6–7)
    and the moment-based fit initialisation from `fit_SpIDA_histo.m`
    (epsilon0 ≈ var/mean, N0 ≈ mean²/var). Validated against images simulated by the
    reference method: R² 0.99 and <10% recovery error on N and epsilon; a 2×-brightness
    sample is correctly identified as a dimer (state 1.90×).
  - **Calibration step** measures the monomeric reference epsilon_0 from a control ROI;
    without it, density and brightness are still reported but no oligomeric state
    (rather than a misleading number).
  - **Assumption guardrails** (`check_assumptions`): warns on small ROI / undersampling,
    saturation-clipping (linear-response violation), and low signal-to-background —
    surfacing conditions that make the numbers untrustworthy instead of returning them
    silently. Reporting is Image → Assessment → Interpretation, per PyCAT's
    anti-black-box philosophy.
  - New "Molecular Counting" submenu under Advanced Analysis groups SpIDA with the
    existing Photobleaching Step Counting tool.

## [1.5.172] - 2026-07-05
### Added (roadmap items)
- **Expand Labels** (Toolbox → Labeled Mask Tools → Expand Labels): grows each label
  outward by a chosen pixel distance using `skimage.segmentation.expand_labels`, which
  preserves label identity and does NOT merge touching objects — addresses the
  roadmap's "segments too small" item. New `run_expand_labels` in
  `label_and_mask_tools`.
- **Mask Layer Operations (AND / OR / XOR)** (Toolbox → Layer Operations → Mask
  Operations): boolean set operations on two masks — AND = overlap, OR = union,
  XOR = symmetric difference. Inputs are binarized so both binary and labeled masks
  work. New `run_mask_logic_merge`. Verified numerically.
### Changed (documentation)
- Pruned `roadmap.rst`: added a "Recently Completed" section (VPT, batch, 3D/Z-stack,
  time-series, watershed, top-hat, Cellpose model selection, progress bars/threading,
  the two new label ops, and the workflow scaffolding), an "Outstanding & Noted"
  section (status-marker completion, remaining step enumeration, BioIO migration, QC
  advisor, 3D rendering presets, kymographs), and marked the individual shipped items
  and the fixed merge Known Issue inline.

## [1.5.171] - 2026-07-05
### Fixed (code audit)
- **`NameError: QSizePolicy` in `spatial_randomness_tools._add_spatial_randomness`.**
  `QSizePolicy` was used (line ~359) but omitted from the local PyQt5 import; the widget
  would crash when built. Added it to the import. (Same moved/missing-import class the
  mixin guard catches.)
- **`NameError: napari_show_info` in `image_processing_tools.run_upscaling_func`.** Only
  `show_warning` was imported; the success-notification path called an unimported
  `napari_show_info`. Added `from napari.utils.notifications import show_info as
  napari_show_info`.
- **Known Issue resolved — "Mean and Additive multi-merge produce identical results."**
  `run_simple_multi_merge` min-max-normalized the result per-merge; since Mean =
  Additive / N, that normalization cancelled the constant and made the two modes
  byte-identical. Now the merged result is clipped to the input dtype's range and
  scaled by that fixed maximum, so Additive can saturate (its intent) while
  Mean/Max/Min keep distinct scales. Verified numerically (Additive max 0.766 vs Mean
  0.383 on the same inputs).
### Changed (consistency)
- Consolidated the 27 scattered inline `from pycat.ui.field_status import
  button_with_circle as _bwc` statements (added across the status-marker rollout) into a
  single top-level import per file, removing the awkward mid-line
  `form.addRow(prog); from ...` imports.

## [1.5.170] - 2026-07-03
### Fixed / Added
- **Status markers are now painted circles.** Replaced the CSS-styled dot (which a
  global stylesheet could flatten to a square) with a directly-painted antialiased
  circle in `StatusCircle.paintEvent`, so it stays round regardless of app styling.
- **Toolbox widgets no longer open duplicate instances.** `_add_widget_to_layout_or_dock`
  now checks whether a dock with the same name is already open; if so it shows an
  "Already open" dialog (OK) and does not add a second copy. Uses napari's
  public/fallback `dock_widgets` registry (keyed by name).
- **Status-marker style extended to the standalone workflows.** Run/action buttons in
  In-Vitro Fluorescence, In-Vitro Brightfield, Cellular Brightfield, FRAP, Video
  Particle Tracking, and Z-Stack now carry red (required) / yellow (optional) circle
  markers, with required-vs-optional taken from each workflow's checklist definition
  ([opt] tags). Their dropdowns already carried markers via `label_with_circle`.

### Needs-attention (flagged, intentionally NOT auto-marked)
- Some buttons were left unmarked because their required/optional status or step
  mapping was ambiguous; see the session notes. Notably: the Z-Stack per-section
  generic action button (`QPushButton(label)` built dynamically), and any
  Dynamics/Phase-diagram/Frame-Quality actions whose checklist step is optional but
  whose in-widget grouping spans multiple analyses. These should be reviewed and
  marked by hand.

## [1.5.169] - 2026-07-03
### Fixed (status markers — circular shape + placement by the dropdown)
- **Status markers render as circles again.** The marker stylesheet was being flattened
  to a square (a global QLabel style could override the corner radius); it now uses an
  explicit `QLabel { … border-radius }` rule with fixed min/max size so it stays round
  regardless of app-wide styles. Affects every status marker (dropdown rows and button
  squares).
- **Markers now sit next to the dropdown, not the label.** In `_layer_row` (used by
  Steps 6–9) the marker was on the label row with the dropdown on a separate row below.
  The label is now on its own line and the marker sits inline to the left of the
  dropdown it applies to, so it reads as belonging to the input. (Spatial Metrology's
  form rows already place the marker beside the dropdown via the form layout.)

## [1.5.168] - 2026-07-03
### Added (per-input status squares + optional-section reveals in Condensate)
- **New `button_with_circle` helper** (field_status): puts a red (required) or
  yellow (optional) status square left of an action button; if given the dropdowns it
  depends on, the square turns green once they all have a real selection.
- **Status squares on the required/optional inputs and actions across the Condensate
  workflow:**
  - Step 2 Measure Line(s): red button square. Step 3 Run Upscaling: yellow (optional).
    Step 4 Pre-process: red. Step 14 Save & Clear: red.
  - Step 6 Cell Segmentation: red square already on the image-layer dropdown; added a
    red square on Run Segmentation (green once a layer is chosen).
  - Step 7 Cell Analyzer: red square on Run Cell Analyzer (wired to the required mask +
    image dropdowns).
  - Step 8 Subcellular Segmentation: red squares already on both dropdowns; added a red
    square on Run Condensate Segmentation, and **Refinement Parameters is now hidden
    behind an off-by-default "Show refinement parameters" checkbox**.
  - Step 9 Condensate Analysis: both dropdowns now carry red status squares (converted
    to the status-row helper) and Run Condensate Analyzer has a red square.
- **Optional sections now have reveal checkboxes (off by default) with yellow squares:**
  - Spatial Metrology (Step 10): "Show spatial metrology (optional)" checkbox; when
    shown, its two dropdowns and the Run button carry yellow squares.
  - Condensate Biophysics: "Show condensate biophysics (optional)" checkbox; when
    shown, its per-tab fit/run buttons carry yellow squares.

## [1.5.167] - 2026-07-03
### Fixed (step enumeration reached the QGroupBox/button-titled builders)
- **Spatial Metrology (Step 10) and Save & Clear (Step 14) now show their step
  numbers.** The `_stage_step` mechanism only reached builders whose title came from
  `add_text_label(bold=True)`; Spatial Metrology renders its title as a `QGroupBox`
  title and Save & Clear had no title label at all, so both were silently dropping the
  staged prefix. Added a shared `_consume_step_label()` helper: Spatial Metrology now
  renders a matching 14px rich-text "Step 10 —" header above its box (with the box
  title repurposed as a short description), and Save & Clear gained a "Step 14 —"
  header. Every numbered checklist step in the Condensate workflow now shows its
  number.
- **Condensate Biophysics title enlarged to match.** It rendered at the small 10px
  `add_text_label` size; now a 14px header matching the other sections. It carries no
  step number by design (it is not a step in CONDENSATE_PIPELINE).
### Changed (Condensate Biophysics — time-aware tabs)
- **Time-dependent biophysics tabs are hidden for 2D input.** MSD/Diffusion, Kinetics,
  QC/Bleach, and Survival all need a (T,H,W) stack; they are now added/removed
  dynamically based on time-stack presence (re-checked on layer add/remove), leaving
  only the static Intensity/Csat tab for plain 2D data.

## [1.5.166] - 2026-07-03
### Changed (Advanced Analysis — optional-by-default + time-aware tabs)
- **Advanced Analysis (condensate Steps 11–13) is now hidden behind an off-by-default
  checkbox.** The block is fully optional, so it now shows only a "Show advanced
  analysis (optional)" checkbox by default; ticking it reveals the tabbed
  Morphological / Dynamic / Organizational analyses.
- **The Dynamic Spatial Phenotyping tab is hidden when the input has no time channel.**
  Dynamic analysis needs a (T,H,W) stack; the tab is now added/removed dynamically
  based on whether a time stack is loaded (re-checked when the block is shown and on
  layer add/remove), so 2D-only inputs don't see an inapplicable tab.
### Changed (condensate step numbering — match the checklist)
- Enumerated titles now reflect merged/bundled steps: Pre-process is labelled
  **"Steps 4–5 —"** (it produces both the pre-processed and background-removed layers,
  merged in 1.5.136), Advanced Analysis is **"Steps 11–13 —"** (Morphological, Dynamic,
  Organizational in one tabbed block), and Save & Clear now carries its **"Step 14 —"**
  label (previously unlabelled). This resolves the apparent gaps at steps 5, 12, 13, 14.

## [1.5.165] - 2026-07-03
### Changed (Step 1 mechanism + global font)
- **Step 1 now uses the same header mechanism as the other steps.** Previously Step 1's
  title was a `QGroupBox` title while Steps 2+ were rich-text labels, so they never
  quite matched in size/weight no matter how the stylesheets were tuned. Step 1 now
  renders its "Step 1 — Load Image / File" header as the same rich-text label
  (prefix at weight 800, title at 600, 14px) — matching the enumerated steps by
  construction. The groupbox-title position is repurposed as a grey italic one-line
  description ("Load an image to begin — completes automatically"), removing the
  duplicate step name. The Pixel-size gate's title reverts to plain styling (it's a
  conditional gate between steps, not a numbered step).
- **Global sans-serif UI font.** Set an application-wide `QFont("Arial")` with a
  SansSerif style hint (falls back to the platform sans-serif if Arial is absent) at a
  larger 10pt base, so default text reads at a clearer size instead of the small Qt
  default.

## [1.5.164] - 2026-07-03
### Changed (step-header consistency)
- **Step 1 and Pixel-size block titles now match the enumerated step headers.**
  Previously the Step 1 / Pixel-size groupbox titles were un-bold and a different size
  than the "Step 2 — …" section headers. Both now render at 14px bold (via a per-widget
  title stylesheet that also repeats the global title positioning so no clipping is
  reintroduced), and the enumerated step labels were bumped to 14px to match. Chose the
  surgical approach — only the two step-level groupboxes are restyled — so sub-section
  groupboxes inside a step (e.g. "Segmentation method", "Refinement Parameters") stay
  light and subordinate rather than competing with their parent step header.

## [1.5.163] - 2026-07-03
### Changed (step-title readability)
- **Enumerated section titles are now larger and the "Step N" prefix is emphasized.**
  The shared-builder section titles rendered at 10px, making the enumerated steps look
  subordinate to the Step 1 block. Stepped titles now render at 13px as rich text with
  the "Step N —" prefix at font-weight 800 (heavier than the title's 600), so the step
  number anchors the eye and the section reads as a primary header. Only titles that
  receive a staged step label are affected; all other `add_text_label` calls keep their
  existing size/weight.

## [1.5.162] - 2026-07-03
### Added (step enumeration — mechanism + Condensate reference)
- **Parameterized step enumeration for shared widget builders.** The built-in
  workflows (condensate, time-series, coloc, general, fibril) build their sections
  from shared `_add_*` builders that are reused across pipelines at *different* step
  numbers (e.g. Upscale is step 3 in Condensate but step 2 in Fibril), so a step
  number can't be hardcoded in the builder. Added a staging mechanism: a workflow
  calls `self._stage_step("Step N — ")` immediately before a shared builder, and
  `add_text_label` prepends that prefix to the builder's first bold title, then
  clears it. One-method change (`add_text_label`) rather than threading a parameter
  through ~30 builders; verified the prefix attaches to the first bold title only,
  clears correctly, and re-stages per call.
  - **Condensate workflow enumerated** as the reference, matching CONDENSATE_PIPELINE
    numbering (Step 2 Measure → Step 11 Advanced). The 7 standalone workflows
    (in-vitro fluor/bf, z-stack, FRAP, VPT, brightfield, temperature) already carry
    correct "Step N" titles in their own groupboxes and were left as-is.
  - Remaining built-in workflows (time-series, coloc, general, fibril) will be
    enumerated in a follow-up using the same mechanism.

## [1.5.161] - 2026-07-03
### Changed (workflow checklist — optional steps no longer gate progress)
- **Optional steps pass progress through to the next mandatory step and keep their
  own colour.** Refines the 1.5.160 colour logic: the "current" (red) marker is now
  the first incomplete *required* step, computed by skipping optional steps entirely
  — an untouched optional step in the middle of the list no longer blocks the red
  marker from advancing to the next mandatory step. When an optional step IS used it
  turns **blue** and stays blue; it never turns green and never participates in the
  required-step progression. Required steps become available once all *required*
  predecessors are done, regardless of intervening optional steps. The detail-label
  highlighting uses the same required-only "current" logic. Verified by simulation:
  with steps 1–2 done, the red marker sits on the next required step whether the
  intervening optional step is untouched (grey) or used (blue).

## [1.5.160] - 2026-07-03
### Changed (pixel-size gate — data-switch behavior + persist option)
- **Pixel-size gate now re-evaluates when the active data class switches.**
  `CentralManager.set_active_data_class` fires registered callbacks, and each gate
  registers its `refresh`. Switching to data that has no scale of its own re-shows the
  gate (previously it only re-checked on file load / manual scale entry, so a switch to
  unscaled data left the gate hidden).
- **New off-by-default "Keep this pixel size for the session" checkbox.** When checked
  and a valid pixel size has been entered, switching to other unscaled data
  automatically re-applies the remembered value instead of re-prompting. Off by
  default so each dataset's scale is set explicitly.

### Changed (workflow checklist — colour logic)
- **Checklist pills now follow the workflow boxes' red→yellow→green→blue logic.**
  Previously: grey (future) / orange (current) / green (done). Now: an available
  required step that still needs doing is **red** (was orange), the active optional
  step keeps the **yellow** highlight, a completed required step is **green**, and a
  completed **optional** step (tagged `[opt]`/`[optional]` in its label) turns **blue**.
  Steps whose predecessor isn't finished remain **grey** (locked), preserving the
  greyed-until-previous-step-complete gating. Optional-vs-required is detected from the
  existing `[opt]` label tags, so no pipeline definitions changed.

## [1.5.159] - 2026-07-03
### Fixed (UI consistency — checklist + pixel-size gate)
- **Object Colocalization workflow now activates its checklist.** It was the only one
  of the 13 pipelines not calling `workflow_checklist.activate('coloc')`; added.
- **Pixel-size gate restored/added to every imaging workflow that takes a pixel
  size.** The disappearing "Pixel size" box (shown only when the image metadata gave
  no scale, hidden once a valid µm/px scale is read or entered) was present on the
  built-in workflows (condensate, time-series, general, fibril) and temperature, but
  missing from the standalone workflows. Added `add_pixel_size_gate` — with the
  same auto-hide behavior keyed on `pixel_size_from_metadata` / `microns_per_pixel_sq`
  — to In-Vitro Fluorescence, In-Vitro Brightfield, Z-Stack (3D), FRAP, Video
  Particle Tracking, and Cellular Brightfield, plus `include_pixel_gate=True` on
  Object Colocalization. Every imaging workflow now shows the gate when needed and
  hides it once a scale exists, matching the Condensate reference.

### Note
- Step-title enumeration (making every widget-box title show a "Step N" that
  corresponds to the checklist) is the planned second pass and is NOT in this release.

## [1.5.158] - 2026-07-03
### Fixed (critical — regression from 1.5.157)
- **`NameError: QSizePolicy is not defined` in `add_step1_file_io`.** The Step 1
  block used `QSizePolicy` but `field_status.py` never imported it at the scope of
  that function (it was only imported locally inside a *different* function). This
  broke the Step 1 block everywhere: workflows that wrap the call in try/except
  silently showed NO Step 1 (explaining "there are no step 1s still"), and the
  Temperature-Dependent Microscopy workflow — which calls it directly — crashed its
  entire `setup_ui` with the NameError. Fixed by adding `QSizePolicy` to the import.
  Restores Step 1 across all workflows and un-breaks the temperature dock.

## [1.5.157] - 2026-07-03
### Fixed (UI — Step 1 consistency)
- **Hybrid Step 1 block, applied consistently.** `add_step1_file_io` now takes an
  optional `instruction_html`: it renders the red/green "image loaded" status marker
  on top (as before) and a workflow-specific load instruction beneath it — the layout
  requested (marker + status, then the Open/Save→Open Image Stack style text below).
- **Missing Step 1 added to three workflows.** The In-Vitro Fluorescence, In-Vitro
  Brightfield, and Z-Stack (3D) docks jumped straight to "Step 2" with no Step 1
  block. Each now opens with the hybrid Step 1 (status marker + a load instruction
  appropriate to the workflow — fluorescence/brightfield image, or Open Image Stack
  for the Z-stack).
- **Time-Series double Step 1 removed.** The time-series dock showed two competing
  "Step 1"s — a standalone instruction label AND the workflow header's file-I/O block.
  Merged into a single hybrid Step 1 at the top (status marker + the
  "Open/Save File(s) → Open Image Stack (T/Z / IMS)" instruction), with the reference-
  frame selector following as Step 2.

## [1.5.156] - 2026-07-03
### Fixed (UI layout)
- **Right-side clipping in the In-Vitro Fluorescence, In-Vitro Brightfield, and
  Z-Stack (3D) analysis docks.** These three docks never called `_relax_min_widths`
  / `_apply_scroll_guard` on their root widget, so long buttons/labels reported a
  wide minimum width and pushed controls (e.g. "Preprocess", "Segment Droplets",
  "Compute Field Summary") off the right edge when the dock was narrower than their
  hint. Added both calls (deferred import, matching the temperature_ui pattern) right
  before each dock is shown — content now shrinks to the dock width instead of
  clipping.
- **GroupBox title clipping (global sweep).** Raised the global `QGroupBox` title
  clearance (margin-top 16→22px, padding-top 8→10px, title `top: 2px`) so titles sit
  clear of the first content row everywhere. Also bumped two specific groupboxes whose
  own tight top content-margins let the title overlap the first control regardless of
  the global style: the "XY Region of Interest" box (time-series ROI; 8→20px) and the
  "Method" box (time-series Cellpose; 4→20px). Swept all UI files for titled
  groupboxes with top content-margins < 18px; these were the only two at risk beyond
  what the global style covers.

## [1.5.155] - 2026-07-03
### Changed (refactor — no behaviour change) — SPLIT COMPLETE
- **`ui_modules.py` split, step 6 (final): basic image-operation widgets.** Moved the
  4 pure image-transform widget builders (rescale intensity, invert, upscaling,
  rolling-ball + Gaussian background removal) into a new `ui/ui_imageops_mixin.py`
  (`_ImageOpsWidgetsMixin`), grouping them with the other image-processing widgets.
  The `__init__`-coupled base I/O (open, save/clear, measure line, pre-process,
  calibration correction, plotting) deliberately STAYS in `ToolboxFunctionsUI`, since
  those are the core lifecycle operations that belong next to `__init__` — organizing
  by concern rather than chasing line count. The import-resolution guard flagged a
  needed `QCheckBox` import up front (added before shipping); both guard and
  structural tests pass clean.

### Summary of the ui_modules.py refactor (steps 1–6)
- `ui_modules.py`: **4,555 → 2,835 lines**. `ToolboxFunctionsUI`: **~2,140 → 411
  lines** (now just `__init__` + 6 base-I/O/core methods). ~90 widget-builder methods
  relocated into six domain mixins, each inherited via the MRO with zero behaviour
  change: `_DiagnosticsWidgetsMixin`, `_FilteringWidgetsMixin`,
  `_SegmentationWidgetsMixin`, `_AnalysisWidgetsMixin`, `_LabelsMasksWidgetsMixin`,
  `_ImageOpsWidgetsMixin`. Guarded by `tests/test_ui_structure.py`,
  `tests/test_ui_smoke.py`, and `tests/test_mixin_imports.py`. The god-object that was
  the codebase's main merge-conflict/blast-radius surface is gone.

## [1.5.154] - 2026-07-03
### Changed (refactor — no behaviour change)
- **`ui_modules.py` split, step 5: labels / masks / merge widgets.** Moved the 8
  label- and mask-tool widget builders (convert labels↔mask, measure region
  properties, update labels, label/measure binary mask, binary morphology, simple
  multi-merge, advanced two-layer merge) verbatim into a new `ui/ui_labels_mixin.py`
  (`_LabelsMasksWidgetsMixin`). `ui_modules.py` now 2,915 lines (from 4,555 — 36%
  smaller across five steps). The import-resolution guard (1.5.153) caught two
  potential runtime errors BEFORE shipping: a `guard_wheel` reference (fixed with the
  deferred-import pattern) and a missing `QRadioButton` import (added) — both would
  have been NameErrors when opening the affected widgets. Both guard and structural
  tests now pass clean; no circular imports (label_and_mask_tools / layer_tools don't
  import the UI layer). Class bases now include `_LabelsMasksWidgetsMixin`.

## [1.5.153] - 2026-07-03
### Added
- **`tests/test_mixin_imports.py` — automated guard for the mixin refactor.** A
  static (ast, no Qt) test that walks every method in each `ui_*_mixin.py` and
  confirms every loaded name resolves from module imports/defs, names bound anywhere
  in the method (all nested closures pooled, so legit `_run`/`_preview` handlers
  don't false-positive), sibling methods, builtins, or self. This catches the exact
  bug class that surfaced during the split — a moved method referencing a
  module-level name (`math`, `guard_wheel`, `QSizePolicy`) that wasn't carried into
  the mixin — at test time instead of when the widget is opened. Verified: passes on
  all three current mixins, and confirmed to FLAG the bug when `import math` is
  removed. Parametrized over `ui_*_mixin.py`, so every future extraction is guarded
  automatically.

### Changed (refactor — no behaviour change)
- **`ui_modules.py` split, step 4: analysis widgets.** Moved the 7 feature/
  correlation/coloc analysis widget builders (cell analysis, puncta analysis,
  spatial autocorrelation, cross-correlation function, pixel-wise correlation,
  object-based coloc, Manders) verbatim into a new `ui/ui_analysis_mixin.py`
  (`_AnalysisWidgetsMixin`). `ui_modules.py` now 3,138 lines (from 4,555 — ~31%
  smaller across four steps). First extraction with the new import-resolution guard
  run BEFORE shipping: both it and the structural test pass clean. Class bases now
  `(BaseUIClass, _DiagnosticsWidgetsMixin, _FilteringWidgetsMixin,
  _SegmentationWidgetsMixin, _AnalysisWidgetsMixin)`.

## [1.5.152] - 2026-07-03
### Fixed
- **Segmentation AND filtering mixins: `NameError: name 'math' is not defined`.**
  `_add_run_local_thresholding` (segmentation) and the WBNS widget (filtering) use
  `math.ceil(...)` but the new mixin files didn't `import math`. Added to both. Found
  the filtering one via a systematic import-resolution scan of all mixins (which also
  confirmed `guard_wheel` and the others are now resolved), before it could surface
  as a runtime error when opening WBNS.
- **Diagnostics widgets: `ModuleNotFoundError: pycat.toolbox.pipeline_snr_tools`.**
  The Pipeline SNR Analysis / Pipeline Step Diagnostics widgets delegate to
  `pipeline_snr_tools.py` and `pipeline_diagnostic_tools.py`. These modules exist in
  the source tree but were evidently not present in the installed package on the
  target machine. Both are included directly in this patch to guarantee they land on
  reinstall. (Root cause is a packaging-inclusion gap for these tool modules, not a
  code error — shipping the files sidesteps it.)

## [1.5.151] - 2026-07-03
### Fixed
- **Segmentation mixin: `NameError: guard_wheel is not defined`.** The
  segmentation widgets moved into `ui_segmentation_mixin.py` in 1.5.150 call
  `guard_wheel` (the wheel-scroll guard helper), which lives in `ui_modules.py`.
  A top-level import would create a cycle (`ui_modules` imports the mixin), so
  `guard_wheel` is now imported deferred inside the two methods that use it
  (`_add_run_local_thresholding`). Audited all three mixins (diagnostics,
  filtering, segmentation) for other unresolved `ui_modules`-scope helpers — only
  segmentation used `guard_wheel`; the other two are clean.
- **Gaussian localization: `UnboundLocalError: QSizePolicy` (pre-existing latent
  bug, surfaced during refactor testing).** `_add_gaussian_localization` in
  `gaussian_localization_tools.py` used `QSizePolicy` at ~line 351 but only
  imported it inside a later `else` branch (~line 462). Because Python treats a
  name imported anywhere in a function as function-local, `QSizePolicy` was unbound
  at the earlier use. Fixed by importing `QSizePolicy` once at the top of the
  function and removing the redundant nested import. Unrelated to the mixin split;
  fixed while it was exposed.

## [1.5.150] - 2026-07-03
### Changed (refactor — no behaviour change)
- **`ui_modules.py` split, step 3: segmentation widgets.** Moved the 5 segmentation
  widget builders (Felzenszwalb + merging, Cellpose, random-forest classifier, local
  thresholding, subcellular condensate segmentation) plus the
  `_run_stardist_segmentation` helper verbatim into a new
  `ui/ui_segmentation_mixin.py` (`_SegmentationWidgetsMixin`), now inherited by
  `ToolboxFunctionsUI`. `ui_modules.py` drops to 3,336 lines (from 4,555 at the start
  of the split — ~27% smaller across three steps). Verified: compiles; 0 dangling
  references; all methods + the stardist helper in the mixin, none left behind; class
  bases now `(BaseUIClass, _DiagnosticsWidgetsMixin, _FilteringWidgetsMixin,
  _SegmentationWidgetsMixin)`; no circular import (segmentation_tools imports only
  ui_utils, a leaf, and that dependency pre-existed the refactor). Steps 1-2
  confirmed working live.

## [1.5.149] - 2026-07-03
### Changed (refactor — no behaviour change)
- **`ui_modules.py` split, step 2: preprocessing/filtering widgets.** Moved the 12
  image preprocessing/filtering widget builders (enhanced RB-Gaussian bg removal,
  WBNS, wavelet noise subtraction, bilateral, CLAHE, FFT bandpass, im2bw, best
  slice, peak/edge enhancement, morphological Gaussian, DPR, Laplacian-of-Gaussian)
  verbatim into a new `ui/ui_filtering_mixin.py` (`_FilteringWidgetsMixin`), now
  inherited by `ToolboxFunctionsUI`. `ui_modules.py` drops 4,555 → 3,688 lines
  across the two refactor steps so far (~19%). Verified: both files compile; UI
  structural test reports 0 dangling references; all 12 methods in the mixin, none
  left behind; class bases `(BaseUIClass, _DiagnosticsWidgetsMixin,
  _FilteringWidgetsMixin)`; and no circular-import risk (the tool modules the mixin
  imports do not import back from the UI layer). Step-1 diagnostics mixin (1.5.148)
  confirmed working live — app launches and the moved widgets open.

## [1.5.148] - 2026-07-03
### Changed (refactor — no behaviour change)
- **Began splitting the 4,555-line `ui_modules.py` into domain mixins (step 1 of
  several).** The oversized `ToolboxFunctionsUI` class is the main
  merge-conflict/blast-radius surface in the codebase (see the code audit). First
  extraction: the 7 self-contained diagnostic/tuner widget builders
  (`_add_pipeline_snr_analysis`, `_add_pipeline_diagnostics`,
  `_add_foreground_suppression_tuner`, `_add_segmentation_speed_comparison`,
  `_add_chromatin_topology`, `_add_nucleolus_void_estimator`,
  `_add_display_diagnostics`) moved verbatim into a new
  `ui/ui_diagnostics_mixin.py` (`_DiagnosticsWidgetsMixin`), which
  `ToolboxFunctionsUI` now inherits. Methods resolve identically via the MRO, so
  behaviour is unchanged. `ui_modules.py` drops from 4,555 → 3,937 lines.
  - Started with the lowest-risk cluster (recent, self-contained widgets) to
    validate the mixin mechanism before touching load-bearing preprocessing/
    segmentation code. Verified: both files compile; the UI structural safety-net
    test reports 0 dangling references; all 7 methods live in the mixin class body;
    `ToolboxFunctionsUI` bases are `(BaseUIClass, _DiagnosticsWidgetsMixin)` with a
    correct MRO. Run `test_ui_smoke.py` on a full install to confirm live
    construction.

## [1.5.147] - 2026-07-03
### Added
- **Nucleolus / chromatin-void estimator** (Toolbox → Image Processing → Nucleolus /
  Void Estimator; core in `topology_tools.py`). Detects rounded DNA-excluding voids
  in a DAPI channel from its chromatin-density envelope — nucleoli and other
  DNA-excluding bodies appear as low-intensity voids the raw channel is often too
  noisy to threshold, but the smoothed envelope reveals them as coherent low basins.
  - **Two-tier classification:** each enclosed void is labeled `nucleolus-like`
    (round + compact + convex: circularity + solidity gates) or `irregular-void`.
    Deliberately framed as WEAK INFERENCE — a round solid void is only *likely* a
    nucleolus — so downstream analysis can weight confidence rather than treat it as
    a hard call.
  - **Optional condensate channel → partition inference:** with a condensate channel
    supplied, each void gets a partition call (`partitioning` / `excluded` /
    `ambiguous`) from the ratio of condensate signal inside the void vs. a
    surrounding ring. This gives a supporting guess for whether condensates enter or
    are excluded from nucleoli when no nucleolar marker channel is available.
  - **Live tuner UI** (like the foreground-suppression tuner): density-percentile,
    circularity/solidity gates, envelope sigma, and min-area exposed as sliders to
    calibrate against real DAPI, with results overlaid as napari label layers and
    per-cell void counts written to cell_df. Validated on real DAPI+GFP (correctly
    separates round nucleolus-like voids from irregular low-density regions and flags
    partitioning ratio 3.06 vs excluded 0.49).
  - Detection is envelope-first (not raw-threshold), which is what makes it work on
    dim/low-contrast DAPI where a hard threshold merges voids into chromatin.

## [1.5.146] - 2026-07-03
### Added
- **UI refactor safety net** — two new test layers to protect the upcoming
  `ui_modules.py` cleanup/split against the recurring "a change silently broke a
  menu/widget" failure mode:
  - `tests/test_ui_structure.py` (static, `ast`-based, no Qt/napari — runs anywhere):
    asserts the module parses, every `toolbox_functions_ui._add_X` referenced in a
    menu/workflow registration resolves to a method defined somewhere (as `def`,
    lambda-bound, or in a sibling ui/tool module), each workflow layout attribute is
    still assigned, and the core UI classes still exist. Validated to PASS on current
    code (0 dangling references) and to FIRE on a simulated method rename — so it
    catches a moved/renamed/dropped widget method at test time instead of at
    click time.
  - `tests/test_ui_smoke.py` (headless Qt via `QT_QPA_PLATFORM=offscreen`): actually
    constructs CentralManager / toolbox UI / MenuManager to catch mixin-composition /
    MRO errors, missing-attribute-at-construction, and import cycles a static parse
    can't. Auto-skips where PyQt5/napari aren't installed.
  - `tests/README.md` documents the recommended before/after-each-step workflow for
    the refactor.

## [1.5.145] - 2026-07-03
### Fixed
- **Maximize-on-start made durable (event-driven, no longer a timing race).** The
  maximize-on-startup has regressed repeatedly across releases: it was done either
  synchronously before the event loop (silently discarded — the startup relayout
  re-shows the window at default size) or on a fixed timer delay (120 ms → 200/500 ms
  → …), and every fixed delay was eventually out-grown as later UI changes lengthened
  the startup relayout, so the maximize fired and was then clobbered by a late
  relayout. This session it had regressed back to the pre-fix synchronous-before-loop
  form. Replaced the whole approach: maximize is asserted after the loop starts, then
  a lightweight 100 ms poll re-asserts it for a ~2.5 s settling window and stops
  itself once stable. This catches a relayout that un-maximizes the window regardless
  of *when* it happens during startup, so future UI growth can't re-break it by
  shifting the timing. (Verified by simulation: relayouts that un-maximize mid-startup
  are re-maximized within one poll tick, and the watcher self-stops.)

## [1.5.144] - 2026-07-03
### Added
- **Analysis regression-test framework** (addresses the code-audit "narrow test
  coverage" finding). Previously the test suite covered infrastructure (imports,
  app boot, file I/O, data manager) plus three low-level utilities, but none of the
  scientific analysis. New tests cover the core analyses on deterministic synthetic
  data (`tests/fixtures_synthetic.py`):
  - `test_coloc_metrics` — Pearson known-answers (identical→1.0, anti→−1.0,
    independent→~0, symmetry invariant).
  - `test_frap_fitting` — recovery-model endpoints/half-time (exact), and fit
    recovering a known mobile fraction & half-time from a noise-free curve.
  - `test_partition` — K = dense/dilute known-answer, unity on uniform input,
    background-subtraction behavior, non-negativity invariant.
  - `test_segmentation_refine` — locks in the fast-vs-original refinement
    bit-for-bit equivalence (making permanent the manual `np.array_equal` check from
    the 1.5.134 optimization), plus within-cell and subset invariants.
  - **Empirical/golden values are intentionally left as `TODO(maintainer)`
    placeholders** (`EMPIRICAL_PARTIAL_OVERLAP_PEARSON`, `NOISY_FIT_*`,
    `GOLDEN_SEGMENTATION_OBJECT_COUNT`) — those tests skip until the maintainer fills
    the validated reference value, so the framework is ready but the "correct answer"
    for realistic data is decided later. See `tests/README.md`.
  - Framework validated: all known-answer assertions and fixtures were checked
    against the real numeric libraries (identical→+1.0000, anti→−1.0000,
    independent→−0.003; FRAP I(0)=0.2, I(τ½)=0.55; partition K=5.000).

## [1.5.143] - 2026-07-03
### Added
- **PyCAT logging layer** (`utils/logging_utils.py`, `get_logger`). Gives PyCAT
  proper logging — level control, optional file capture, the ability to silence or
  raise verbosity — WITHOUT changing default output. By default it writes plain-format
  messages to stdout, so existing console output looks the same. `PYCAT_DEBUG=1`
  raises the level to DEBUG (same env var that drives the swallowed-exception
  `debug_log` helper), and `PYCAT_LOG_FILE=/path` additionally writes a timestamped,
  level-tagged log — so a user reporting a bug can attach a full run log instead of a
  scrollback screenshot.

### Changed
- **Adopted the logger in `run_pycat.py`** as the reference migration: Cellpose
  model-prewarm progress → `log.info`, setup/icon errors → `log.warning`. This is a
  deliberately partial adoption. The audit's 224 `print()` calls are mostly NOT stray
  debugging — 91 are the intended batch-run progress narrative (`[PyCAT Batch] …`),
  which is correct as visible console output and left as-is. Remaining modules can
  migrate incrementally (error/warning → `log.warning`, info → `log.info`, verbose →
  `log.debug`); batch progress can stay `print` or move to `log.info` with identical
  visible output. No default behavior changes.

## [1.5.142] - 2026-07-03
### Added
- **`debug_log` helper for surfacing swallowed exceptions** (`general_utils.py`).
  The codebase has many `except Exception: pass` guards; most are legitimate (they
  protect optional UI niceties), but when one fires in a data path the failure is
  invisible and undiagnosable. `debug_log(context, exc)` prints the context and full
  traceback ONLY when the `PYCAT_DEBUG` env var is set (following the existing
  `PYCAT_REFINE_DEBUG` / `PYCAT_FORCE_CPU` convention), and is a silent no-op
  otherwise. It does not change control flow — the caller still passes/continues/
  falls back — it only makes the swallow observable for debugging.

### Changed
- **Wired `debug_log` into the highest-value data-integrity swallow sites** (the ones
  where a silent failure corrupts results rather than skipping a cosmetic UI step):
  - `file_io`: physical-pixel-size read (silent failure falls back to 1.0 µm/px,
    which would silently corrupt every downstream micron measurement).
  - `file_io`: AICSImage→tifffile fallback (silent failure loses scene/T/Z metadata).
  - `frap_io`: frame-interval read (affects recovery timing).
  - `frap_io`: bleach center_point_um read (affects ROI placement).
  Run with `PYCAT_DEBUG=1` to see these if a load/measurement looks wrong. The
  remaining cosmetic-guard excepts are unchanged and can adopt `debug_log`
  incrementally.

## [1.5.141] - 2026-07-03
### Documentation
- **Authorship updated.** Gable Wadsworth added alongside Christian Neureuter in the
  README Citation and Acknowledgments sections and listed first; `pyproject.toml`
  authors reordered to match (Wadsworth, then Neureuter). BibTeX key/author field
  updated accordingly.
- **Documented the NumPy < 2 and Zarr < 3 version constraints** in the README (new
  "Dependency Version Constraints" subsection under the Cellpose section). Explains
  that `numpy<2.0` is a downstream consequence of the deliberate `cellpose<4` / numba
  choice (PyCAT's own code is NumPy-2.0-clean and runs at full speed under NumPy 2.x),
  and that `zarr<3.0` is required by the time-series cache's use of the removed
  `DirectoryStore`; migrating to Zarr 3 `LocalStore` would be small and have no
  performance impact (identical one-plane-per-chunk local disk I/O), but is deferred
  because it yields no benefit while Cellpose 3 holds the environment at NumPy 1.x.

## [1.5.140] - 2026-07-03
### Changed
- **Annotated MP4 export rebuilt in pixel space (no more matplotlib figure /
  white padding).** `render_annotated_mp4` (temperature time-lapse export)
  previously rendered each frame through a matplotlib figure, which reserved
  figure margins (the white border), put the temperature/time in a separate title
  band, drew a plot-style scale bar, and held every frame in memory before
  encoding. It now composites annotations directly onto the RGB frame with PIL and
  streams frames one at a time:
  - **Edge-to-edge image** — no white figure padding or title band.
  - **Temperature/time**: black text on a white box, squared into the TOP-LEFT
    corner (equal inset from the top and left edges) for guaranteed legibility.
  - **Scale bar**: solid white bar with the "N µm" label centred above it, squared
    into the BOTTOM-RIGHT corner (bar's bottom-right corner equidistant from the
    right and bottom edges); label carries a thin dark outline so it reads on light
    regions.
  - **Streamed encoding** (one frame in memory at a time) instead of stacking all
    frames, so long time-lapses no longer balloon memory.
  - New optional `colormap` parameter (default `'gray'`, matching the previous
    look); both existing callers are unchanged and remain backward-compatible.

## [1.5.139] - 2026-07-03
### Added
- **Show/hide-all-layers toggle** in the PyCAT toolbar (next to Batch Run / Save
  Config). One click flips every layer's visibility together, so managing a large
  stack no longer requires clicking each layer's eye individually (the workaround of
  dragging layers to reorder for top priority is no longer needed for visibility
  management). The button reads live state each click — if any layer is visible it
  hides all, otherwise it shows all — so it self-corrects even if individual layer
  eyes are toggled in between. The icon/tooltip reflect the next click's action.
  (Note: this is a deliberate all-together toggle; "show all" turns every layer on
  and does not restore a prior per-layer hidden state. It is separate from napari's
  layer-list dock collapse control — the eye in the layer widget's title bar — which
  hides the whole layer list.)

## [1.5.138] - 2026-07-03
### Added
- **Display Diagnostics tool** (Toolbox → Image Processing → Display Diagnostics) to
  investigate "layer controls (contrast/gamma) appear to do nothing." Reports, for
  the active layer: napari version, layer type/dtype/shape, data min/max, current
  `contrast_limits` and `contrast_limits_range`, colormap, RGB flag, visibility, and
  whether the selected layer is actually the top visible layer (a common cause — a
  layer drawn opaque on top hides changes to the layer you're adjusting). Includes a
  live probe that nudges `contrast_limits` and confirms whether the change registers
  on the layer object, distinguishing a data/RGB/version issue from a rendering
  (GPU/OpenGL) or wrong-layer issue.

### Fixed
- **`refresh_viewer_with_new_data` crash for image layers.** The Image branch called
  `add_image_with_default_colormap(viewer, updated_data, ...)` with `data` and
  `viewer` swapped (the signature is `(data, viewer, ...)`), which raised whenever an
  image layer was refreshed in place. Arguments corrected.

### Notes
- Audit of the contrast/layer-control report: PyCAT does not pin `contrast_limits`,
  monkeypatch napari's layer controls, or block slider events (the wheel guard only
  consumes `Wheel` events on PyCAT dock controls, and the drop filter only handles
  drag/drop). `napari` is currently unpinned in `pyproject.toml`, so display-control
  behaviour can vary with the installed napari version. The Display Diagnostics tool
  above is intended to localise the cause on a specific machine/session.

## [1.5.137] - 2026-07-03
### Added
- **Chromatin Topology Map** (new `topology_tools.py`; Toolbox → Image Processing →
  Chromatin Topology Map). Exposes the rolling-ball *background* envelope — normally
  subtracted and discarded — as a structural signal: on a nuclear channel it
  suppresses fine puncta and traces the large-scale chromatin/nucleoplasm topology
  (the connected-network appearance observed on DAPI). This is the shared foundation
  for planned over-segmentation and wetting/connectedness utilities.
  - **Two envelope modes:** `rolling_ball` (morphological envelope, finer chromatin
    texture) and `gaussian` (low-pass at ball_radius scale, smoother percolation
    read). Selectable per run.
  - **Two output layers:** raw envelope ("Chromatin Topology [name]", brightness
    comparable across cells) and mask-normalised ("Chromatin Topology (norm) [name]",
    shape comparable across cells).
  - **Per-cell metrics** written to cell_df when a Labeled Cell Mask is present:
    `topo_cov` (envelope coefficient of variation — how structured), `topo_roughness`
    (std of normalised envelope), `topo_n_basins` (distinct intensity maxima at the
    structural scale — seed for the over-segmentation check), `topo_n_components`
    and `topo_largest_frac` (connectivity of the above-percentile network — seed for
    wetting/percolation, →1 = percolating, →0 = fragmented), and `topo_high_area_frac`.
  - Validated on real DAPI: rolling-ball and gaussian modes give distinct, sensible
    readings (rolling-ball CoV 1.01 / 3 basins / 6 components vs gaussian CoV 0.23 /
    2 basins / 2 components), reproducing the connected-chromatin-network appearance.

## [1.5.136] - 2026-07-03
### Changed
- **Pre-processing and background removal merged into one button.** The separate
  "Pre-process Image" and "Remove Background" buttons are now a single
  "Pre-process Image" button that produces BOTH the "Pre-Processed [name]" and
  "Enhanced Background Removed Pre-Processed [name]" layers in one click. Applied
  across all four workflows that had the two-step structure (Condensate, Object
  Colocalization, General, Fibril). The standalone "Background Removal w/ Edge
  Enhancement" tool remains available in Toolbox → Image Processing for independent
  use. Both the `preprocessing` and `background_removal` batch steps are recorded,
  so batch replay and the workflow checklist (which tracks a `background_removal`
  step) continue to work unchanged.
- **Batch `background_removal` now matches the interactive result.** Batch replay
  previously always ran the destructive `rb_gaussian_bg_removal_with_edge_enhancement`
  on the already-suppressed preprocessed image, diverging from the GUI runner which
  (since 1.5.128) detects already-preprocessed input and applies the non-destructive
  `soft_foreground_suppression` instead. `replay_background_removal` now uses the
  same detection and honours the recorded suppression params, so GUI and batch
  produce identical 'Enhanced Background Removed' output on both the segmentation and
  fluorescence channels.

## [1.5.135] - 2026-07-03
### Fixed
- **Workflow checklist was crashing on activation.** `WorkflowChecklistManager.activate`
  ended with `self._widget.mark_step(step_key)` — but `step_key` is not defined in
  `activate` (its parameter is `pipeline_name`), so every switch into an analysis
  mode raised `NameError` and the checklist never appeared/updated. Additionally the
  manager had **no** `on_step_recorded` method, yet `ui_modules` calls
  `workflow_checklist.on_step_recorded(...)` on the manager to replay recorded steps
  — an `AttributeError`. Removed the stray line and added the missing
  `on_step_recorded` delegator. The step keys in `PIPELINE_DEFS` already match the
  recorded batch-step names, so pills now check off correctly as steps complete.
- **Dock content pushed off-screen (buttons/controls clipped on the right).**
  Horizontal scrolling is disabled on the analysis/toolbox docks (by design, so
  content fits the width), but buttons, combo boxes, line edits and long labels
  reported wide minimum-size hints that forced rows wider than the dock and clipped
  the right edge. Added `_relax_min_widths`, which recursively sets a 0 minimum
  width and a shrinkable (Preferred) horizontal size policy on those controls and
  enables word-wrap on labels, so content compresses to the dock width instead of
  overflowing. Applied to the generic separate-widget dock path (covers every
  toolbox tool) and to all six main analysis docks (Condensate, Time-Series,
  Object Coloc, Pixel-Wise, General, Fibril). The generic dock path also now sets
  `main_widget.setMinimumWidth(0)` to match the main docks.

## [1.5.134] - 2026-07-03
### Optimised
- **Per-object refinement loop vectorised via windowing (~12× faster, identical
  output).** Profiling showed the refinement loop — not cropping — dominates
  segmentation runtime: it ran ~5 full-array `binary_erosion`/`binary_dilation`
  calls per object on the whole cell crop, for ~100 objects × 2 passes × N cells.
  New `puncta_refinement_filtering_func_fast` performs each object's morphology and
  pixel-population statistics inside that object's own padded bounding-box
  sub-window (bbox + 4 px). Since morphology and indexing are local operations, the
  result is bit-for-bit identical to the original while touching a ~15×15 patch
  instead of a 700×700 array per object. Measured: full `puncta_refinement_func`
  (both passes + watershed) 5641 ms → 461 ms (12.2×) on a 120-object scene; the
  filter alone ~13×. Enabled by default (`_PYCAT_REFINE_FAST = True`); the original
  is retained and selectable for verification.

### Added
- **Segmentation Speed Comparison widget** (Toolbox → Image Processing). Runs
  condensate segmentation twice — original vs fast refinement — on the selected
  pre-processed and original layers, times each, checks the refined masks are
  identical, and reports timing, speedup, and equivalence. Adds the fast-path
  result layers, plus a "Fast vs Slow DIFF" layer if any pixel differs. Backed by a
  new viewer-free `_segment_core` so the interactive runner and the comparison share
  one code path and cannot drift.

## [1.5.133] - 2026-07-03
### Optimised
- **Condensate segmentation now crops to each cell's bounding box (major speedup
  for multi-cell images).** Previously every cell ran the full pipeline
  (Felzenszwalb + CLAHE + background removal + thresholding + refinement) on the
  *whole frame* masked to that one cell, so an N-cell image did ~N× redundant
  whole-frame work. `segment_subcellular_objects` now defaults to `crop_to_cell=True`
  with a **6·ball_radius** context margin. Estimated ~6× faster on a typical 5-cell
  image (more with 10–15 cells); the exact gain depends on how much of the cost is
  the per-object refinement loop (which scales with object count, not area, and is
  unaffected by cropping).
- **Output verified numerically identical inside the cell.** On real GFP data the
  padded crop matches whole-frame processing to machine precision within the cell
  (max pixel diff 0.0000, correlation 1.00000 at pad=6·ball_radius), versus
  measurable edge error at the old 1·ball_radius margin — which is why cropping was
  previously left off by default. The larger margin removes that concern. The crop
  is guaranteed to fully contain each cell (only distant background is trimmed), so
  all cell-relative statistics (cell area, background mean/std, kurtosis/SNR gates)
  are preserved.

## [1.5.132] - 2026-07-03
### Fixed
- **Condensate segmentation object count was always reported as "1".**
  `total_refined_puncta_mask` is a boolean OR-accumulation across cells, so
  `int(total_refined_puncta_mask.max())` returned 1 whenever any object existed —
  it reported "at least one pixel set", not the number of objects. The count now
  uses connected-component labeling (`sk.measure.label(...).max()`), and the
  "Total Puncta Mask" / "Total Refined Puncta Mask" layers are added as labeled
  arrays (each object a unique id) instead of a single-label binary cast.
  Downstream analysis is unaffected (the analyzer re-binarizes the mask).

### Added
- **Refinement rejection diagnostic.** When enabled (set
  `segmentation_tools._PYCAT_REFINE_DEBUG = True` or export `PYCAT_REFINE_DEBUG=1`),
  `puncta_refinement_filtering_func` prints why each object is dropped — object
  area, mean intensity, and the specific condition(s) that fired (local_intensity,
  cell_intensity, kurtosis, area, ellipticity, gradient, local_snr, global_snr).
  This turns "why did that bright condensate get dropped?" from guesswork into a
  logged answer for a specific image. Off by default (no output).

## [1.5.131] - 2026-07-03
### Fixed
- **Upscaling no longer produces two identical layers from one image.** Root cause:
  in `run_upscaling_func`, the scaled `add_image_with_default_colormap` call and the
  `napari_show_info` notification were inside the same `try` block, with an `except`
  that re-added the layer. If the notification raised (e.g. `_src_scale[-1]`
  formatting when `layer.scale` had an unexpected length), the layer — already
  added — was added a *second* time by the except handler, yielding two identical
  "Upscaled ..." layers. The add is now performed exactly once via mutually
  exclusive scaled/unscaled branches, and the notification is isolated in its own
  best-effort `try/except` that cannot trigger an add. Added two further guards:
  selected layers are de-duplicated by identity before the loop, and any layer whose
  "Upscaled {name}" output already exists is skipped with a warning.

## [1.5.130] - 2026-07-03
### Fixed
- **Bright condensates no longer dropped or partially segmented due to a hollow-ring
  area miscount.** Root cause: local (Niblack/Sauvola) thresholding hollows out
  large bright flat cores into rings (the flat centre isn't brighter than its local
  window, so only the rising edge thresholds). `opencv_contour_func` then measured
  each object with `cv2.contourArea` — the area *enclosed by the outer polygon*, not
  the lit pixel count — so a hollow ring reported the whole enclosed disc. That
  inflated area tripped `max_area` (`cell_area/4`), rejecting or partially filling
  genuine bright condensates even when their true pixel footprint was far below the
  cap. Smaller dim puncta stayed solid and passed, which is why only the bright
  objects were affected. Two coordinated fixes:
  - `opencv_contour_func` now gates on **filled pixel count** (rasterise the filled
    contour and count pixels) instead of `cv2.contourArea`, making the area test
    consistent with pixel-based area measurement used elsewhere.
  - `fz_segmentation_and_binarization` now applies `ndi.binary_fill_holes` after the
    contour fill, guaranteeing solid objects so bright cores are not left partially
    segmented.
  Verified on synthetic ring/C-shape objects and realistic condensate sizes: a
  bright object at ~2.5% of cell area that the old `contourArea` path over-reported
  is now retained and filled solid; genuinely oversized objects (>25% of cell) are
  still capped as intended.

### Note
- Fully-enclosed hollow rings fill solid; a ring with a real gap in its boundary
  (open C-shape) is retained as-is rather than force-closed, to avoid merging
  adjacent distinct puncta. Raise this if open-boundary partials persist on real
  data — a small morphological closing before the fill can bridge them.

## [1.5.129] - 2026-07-03
### Fixed
- **Foreground suppression no longer erodes condensate borders.** The attenuation
  dimmed the intensity falloff at object edges, so segmentation thresholding
  clipped borders and produced condensates slightly smaller than desired. Added a
  border-protection step to `_realness_weight`: the high-confidence keep region
  (surviving cores, post size-gate) is dilated by `border_grow` pixels and the
  weight is lifted back toward full within the grown band, but only where genuine
  signal exists. Isolated noise, having no high-confidence core, is unaffected, so
  borders are recovered without reintroducing noise.

### Added
- **`border_grow` parameter** (default 2 px) in `FOREGROUND_SUPPRESSION_DEFAULTS`,
  `_realness_weight`, and `soft_foreground_suppression`, exposed as a fifth slider
  in both the Pre-process "Adjust foreground suppression" panel and the Foreground
  Suppression Tuner dock, and recorded/replayed by the batch processor. 0 disables
  border protection (pre-1.5.129 behaviour); higher values restore thicker borders.
  Validated on real GFP data: object footprint at a fixed threshold grows from
  3441 px (border_grow=0) to 4807 px (=2) to 5493 px (=4) while peaks and the noise
  floor are unchanged.

## [1.5.128] - 2026-07-03
### Changed
- **Foreground suppression is now part of core preprocessing.** `pre_process_image`
  applies `soft_foreground_suppression` as a final step (after CLAHE) by default,
  so every consumer — the Pre-process button, batch replay, and the internal
  preprocessing inside subcellular segmentation — receives the corrected output.
  This restores usable preprocessing for condensate detection: the prior CLAHE
  output left the diffuse noise tier at full strength. Two new optional args,
  `suppress_foreground=True` and `suppression_params=None`, allow opting out or
  overriding; existing callers are unaffected (defaults preserve the new behaviour).

### Added
- **Composite 'realness weight' suppression** (`_realness_weight` +
  rewritten `soft_foreground_suppression`). Replaces the single intensity
  smoothstep with a product of four cues so real puncta are kept and noise
  fluctuations eliminated: blob-shape (separable-LoG response), local-contrast
  (value above a larger-σ surround), intensity floor, and a size gate that knocks
  down sub-`min_area` specks. Parameters: `strength`, `log_p`, `con_p`, `min_area`.
- **Tuned defaults** in `FOREGROUND_SUPPRESSION_DEFAULTS`
  (`strength=0.8, log_p=10, con_p=4, min_area=3`), chosen interactively on real GFP
  condensate data against hand-annotated ground truth (strongly-visible objects
  kept, acceptable objects lightly attenuated, noise fluctuations removed).
- **"Adjust foreground suppression" checkbox** on the Pre-process Image widget.
  Unchecked by default (button behaves as before, using the tuned defaults);
  checking it reveals four editable sliders that override the defaults. Overrides
  are stored in the data repository and recorded in the `preprocessing` batch step
  only when changed, keeping unmodified configs clean and forward-compatible.
- **Foreground Suppression Tuner dock** (Toolbox → Image Processing). Live sliders
  over the four parameters with an in-place "Suppression Preview" layer, plus
  "Apply as session default" and "Reset to tuned defaults" buttons. Mirrors the
  Pipeline Diagnostics dock pattern.
- **Batch replay** (`replay_preprocessing`) now honours recorded
  `suppress_foreground` and `foreground_suppression_params`, applying them to both
  the segmentation and fluorescence channels. Legacy configs default to suppression
  ON with tuned defaults.

### Fixed
- The Remove Background button (`run_enhanced_rb_gaussian_bg_removal`) now uses the
  session suppression params instead of a hardcoded `strength=0.6`. Since core
  preprocessing already applies suppression as of this release, a second pass on a
  freshly-preprocessed layer is near-idempotent rather than double-destructive.

## [1.5.127] - 2026-07-03
### Fixed
- **Remove Background button no longer destroys the nucleoplasm baseline on
  preprocessed images.** The "Remove Background" button
  (`run_enhanced_rb_gaussian_bg_removal`) called
  `rb_gaussian_bg_removal_with_edge_enhancement` directly, applying the full
  destructive rolling-ball + Gaussian subtraction chain. On a preprocessed
  condensate image (`/max → separable LoG → WBNS → morph → Gaussian → CLAHE`)
  that subtraction collapses the IQR noise floor to zero: it removes the
  nucleoplasm baseline that condensates sit on top of, leaving only the
  brightest peaks and erasing the diffuse signal dim candidate condensates live
  in. The 1.5.126 bypass only guarded the internal call inside
  `segment_subcellular_objects`; the standalone button hit the bad path
  directly. `run_enhanced_rb_gaussian_bg_removal` now detects whether the active
  layer is already preprocessed (median of non-zero pixels < 0.05 after
  normalisation — the same heuristic as the `segment_subcellular_objects`
  bypass) and, in that case, applies a new non-destructive
  `soft_foreground_suppression` refinement instead.

### Added
- **`soft_foreground_suppression(image, ball_radius, strength=0.6)`** in
  `image_processing_tools.py`. Softly attenuates the dim, diffuse foreground
  tier (dim candidate condensates and low-contrast texture) via a smoothstep
  attenuation weight computed over a structure-sized Gaussian intensity
  reference (σ = ball_radius × 0.27). The weight is ~0 below the 40th-percentile
  intensity anchor and ~1 above the 90th-percentile anchor, and is blended in by
  `strength` so the baseline is preserved rather than zeroed. Result: dim
  candidates are dimmed but remain visible, the nucleoplasm baseline (non-zero
  IQR floor) is preserved, and bright condensate peaks are left intact.
  Verified numerically: peak intensity retained exactly, IQR floor preserved
  (non-zero), overall image dimmed. Output keeps the
  `Enhanced Background Removed [name]` layer name so downstream widgets and batch
  steps that reference it continue to work. A genuinely raw (not-yet-preprocessed)
  image still receives the original enhancement path.

## [1.5.126] - 2026-07-03
### Optimised
- **LoG speedup: separable float32 implementation (1.54× faster, quality identical).**
  The blob-detection step in `pre_process_image` now uses a separable LoG:
  Gaussian(σ) in float32 followed by a discrete axis-wise Laplacian, instead
  of `ndi.gaussian_laplace` on a float64 cast. Validated on 2048×2048 images
  at ball_radius=15 and ball_radius=50:

  | Method | Speedup | SNR | Pixel corr |
  |---|---|---|---|
  | gaussian_laplace f64 (old reference) | 1.00× | 430 | 1.000 |
  | gaussian_laplace f32 | 1.15× | 430 | 1.000 |
  | **separable LoG f32 (adopted)** | **1.54×** | **429** | **0.9999** |
  | DoG fixed σ=2.0,3.2 (old speedup) | 1.37× | 224 | 0.904 |
  | DoG scaled br×0.15,×0.25 | 1.43× | 268 | 0.948 |

  The old DoG speedups are confirmed harmful: the fixed σ=2.0/3.2 DoG drops SNR
  by 48% at ball_radius=15 and would be far worse at ball_radius=50 (σ mismatch
  grows with radius). The scaled DoG (br×0.15,×0.25) drops SNR by 38%.
  Both are discarded. Separable LoG f32 is 1.54× faster with corr=0.9999.

### Fixed
- **segment_subcellular_objects: internal BG removal bypassed when input is
  already LoG-preprocessed.** `segment_subcellular_objects` was calling
  `rb_gaussian_bg_removal_with_edge_enhancement` on the pre_process_image
  output, which destroyed the SNR gains from LoG (collapses IQR to 0).
  Now detects preprocessed input (median of non-zero pixels < 0.05 after
  normalisation) and applies only a light CLAHE pass instead, preserving
  the ×360 within-nucleus SNR from preprocessing.
- **Preprocessing + BG removal chain optimisation results (real GFP data):**
  The correct chain for condensate puncta detection is confirmed as:
  `/max → separable LoG(σ=br×0.27) → WBNS → morph_clean → Gauss(σ=1) → CLAHE`
  No background subtraction step improves on this. Full RB or Gaussian
  subtraction before or after LoG all collapse the IQR noise floor to 0.
  Light partial subtraction (RB f=0.5 at large radius) gives +1-7% marginal
  gain but adds tunable parameters with failure modes on flat-background images.
## [1.5.125] - 2026-07-03
### Fixed
- **Multi-Otsu cell segmentation fallback: wrong threshold + no watershed.**
  Three issues corrected across `batch_roi_tools.py`, `ts_cellpose_tools.py`,
  and `batch_step_registry.py`:

  1. **Wrong threshold class.** `ts_cellpose_tools` used `thresholds[-1]` (the
     highest class — condensate/bright-puncta level) as the cell body boundary.
     The correct threshold is `thresholds[0]` (the lowest class), which captures
     the full cell body including cytoplasm and nucleus. The reasoning: GFP and
     other fluorophores are weakly persistent throughout the cytoplasm, so the
     three-class histogram is: background | cytoplasm+nucleus | bright condensates.
     The lowest threshold separates cell from not-cell. This is the same criterion
     that makes the fallback valid on fluorescence channels but not on brightfield
     (which has no such monotone intensity hierarchy).

  2. **Simple connected-components instead of watershed.** `sk.measure.label` on
     the binary mask merges touching cells into one label. Replaced with distance
     transform + watershed seeded from local maxima spaced by `cell_diameter // 2`,
     which separates touching cells at their midpoints — matching Cellpose output.

  3. **Fixed minimum object size and seed spacing.** Both were hardcoded (64px²,
     20px). Now derived from `cell_diameter`: min object size = `(cell_diameter/2)²`,
     seed spacing = `max(10, cell_diameter // 2)`. The `cell_diameter` parameter
     is now passed through from the data repository in all three call sites.

  4. **Pre-smoothing before thresholding.** A Gaussian smooth (σ = cell_diameter×0.1)
     is applied before `threshold_multiotsu` so condensate puncta above `t[0]`
     outside the cell body don't fragment the foreground mask.
## [1.5.124] - 2026-07-03
### Fixed (critical — preprocessing SNR regression)
- **Replaced white-top-hat + fixed-sigma DoG pipeline with scaled LoG.**
  Quantitative SNR measurement on real condensate data (GFP channel, DAPI
  channel, within-nucleus metric) showed:

  | Step | Within-nucleus SNR |
  |---|---|
  | raw /max | 8 |
  | old pipeline (RB sub 0.75 → DoG fixed σ) | ~20 |
  | new pipeline (LoG, σ = ball_radius × 0.27) | **2917** |

  Root cause of the regression: the white-top-hat × DoG multiplicative step
  suppressed the nucleoplasm baseline that condensate puncta sit ON TOP of.
  Combined with rolling-ball subtraction (which hard-clips background to 0),
  this made nucleoplasm-level condensates indistinguishable from the noise
  floor. The LoG applied directly to the /max-normalised image avoids both
  problems: it enhances blob-like structures at the condensate scale without
  removing the local baseline they sit on.
- **LoG sigma now scales with ball_radius** (σ = ball_radius × 0.27).
  At ball_radius=15 → σ≈4 (optimal for this dataset); at ball_radius=50
  → σ≈14 (appropriate for upscaled images). This restores the radius-
  scaling that v1.0.0's `apply_laplace_of_gauss_enhancement(σ=3)` had
  implicitly via its call-site, and extends it correctly.
- **Pipeline step diagnostics widget updated** to reflect the new pipeline.
## [1.5.123] - 2026-07-03
### Added
- **Pipeline SNR Analysis widget** (Toolbox → Image Processing → Pipeline SNR
  Analysis). Scans the viewer for all diagnostic step layers produced by the
  Pipeline Step Diagnostics widget and computes per-step SNR, displayed as a
  colour-coded table: green = gain, dark red = NaN (background hard-zeroed,
  step is destructive), orange = regression.
  - SNR metric: mean(top 2% non-zero pixels) / std(IQR 25th-75th percentile).
    The IQR noise region is used because subtraction steps hard-clip background
    to 0, collapsing a bottom-50% std to 0 and masking the destruction.
  - Δ SNR column shows change relative to the first step (raw input) of each
    pipeline (current vs v1.0.0 tracked separately).
  - Summary note identifies the best step and flags how many steps collapse
    the noise floor to 0.
### Findings from real data (DAPI + GFP condensate images, ball_radius=15)
  - Rolling-ball subtraction at any scaling factor hard-zeros the background
    on both DAPI and GFP channels → NaN SNR → the step is counterproductive
    for condensate segmentation.
  - LoG(σ=3) alone gives 5× SNR on DAPI, 6.8× on GFP vs raw.
  - DoG with sigmas scaled to ball_radius gives nearly identical gains.
  - The rolling-ball BACKGROUND itself (not the subtraction) carries useful
    chromatin topology for DAPI — it should be exposed as a named output layer.
## [1.5.122] - 2026-07-03
### Fixed
- **Pipeline diagnostics "Could not read layer: name 'np' is not defined".** 
  `numpy` was not imported inside the `_run` closure in `_add_pipeline_diagnostics`.
  Added `import numpy as np` at the top of `_run`.
- **Maximize on startup unreliable.** `showMaximized()` was called via a
  120 ms `QTimer`, which is a race condition — on slower machines the relayout
  hasn't settled, and on faster ones the call can land before or after the
  event loop is ready, producing inconsistent results. Fixed by calling
  `_maximize()` synchronously before `napari.run()` (which starts the event loop).
  Qt's window-state flag is set immediately and honoured on the first show event
  regardless of when the event loop starts. Style and branding remain deferred
  (they touch live widgets that need the event loop running).
## [1.5.121] - 2026-07-03
### Fixed
- **Dock widget too wide / right side clipped at default size.** Description
  and subtitle QLabels with `setWordWrap(True)` still reported their full
  one-line width as the minimum size hint, forcing the dock (and its scroll
  area) to be wider than the napari pane allows. Fixed by adding
  `setSizePolicy(Ignored, Minimum)` after every `setWordWrap(True)` call
  across all workflow modules, and by calling `setMinimumWidth(0)` on the
  inner widget of every QScrollArea dock so the container can compress
  freely. The "Pixel size (no scale in metadata)" group-box title was also
  shortened to "Pixel size" to reduce the minimum title-bar width it imposes.
## [1.5.120] - 2026-07-03
### Fixed
- Missing QProgressBar import inside _add_pipeline_diagnostics (NameError on open).

## [1.5.119] - 2026-07-03
### Fixed
- **Upscaling produces duplicate layers when multiple layers are selected.**
  `viewer.layers.selection` is a live set — napari auto-selects each newly added
  layer, mutating the set mid-iteration so each upscaled output was immediately
  upscaled again. Fixed by snapshotting the selection to a plain list (filtered
  to `napari.layers.Image` only) before the loop.
- **Scale bar does not update when switching to an upscaled layer.** The scale
  bar was set once at file-load time and stayed frozen regardless of which layer
  was active. A `viewer.layers.selection.events.changed` listener now fires
  `_update_scale_bar_for_active_layer()`, which reads `layer.scale[-1]` on the
  topmost selected Image layer and sets `scale_bar.unit` to `'um'` or `'px'`
  accordingly. Upscaled layers carry `scale = source_scale / 2`, so the bar
  correctly reflects their (smaller) physical pixel size on the same FOV.
- **Clarified upscaling notification.** The toast now explains that both layers
  cover the same physical field of view (same µm extent, finer pixel grid) and
  that the scale bar updates when you click a different layer.
## [1.5.118] - 2026-07-03
### Added
- **Pipeline Step Diagnostics widget** (Toolbox → Image Processing → Pipeline Step
  Diagnostics). Two tabbed panels — "Current (1.5.x)" and "v1.0.0 reference" — each
  add a named napari layer for every sub-step of pre_process_image AND
  rb_gaussian_bg_removal_with_edge_enhancement, so the exact step where the two
  pipelines diverge is visible. Known labelled differences shown in the widget:
  ① /max normalisation (current only); ② square vs disk structuring element;
  ③ DoG (fixed σ=2.0/3.2) vs LoG (σ=3, radius-implicit).
## [1.5.117] - 2026-07-03
### Fixed
- Missing `label_with_circle` import in `invitro_fluor_ui` (NameError on open)
  and duplicate import in `invitro_bf_ui` / `brightfield_ui` — left over from
  the scrollbar/import fix pass that stripped a `try/except` block.

## [1.5.116] - 2026-07-03
### Fixed (critical — 2-D fluorescence preprocessing regression)
- **Aggressive signal suppression in preprocessing and background removal for
  dim fluorescence images.** `dtype_conversion_func` uses `img_as_float32`,
  which divides by 65535 (the full uint16 range). A typical condensate
  fluorescence image with a true max of ~2000–3000 counts therefore arrives
  at `pre_process_image` and `rb_gaussian_background_removal` as float32 in
  the range [0, 0.046] instead of [0, 1]. Every subsequent multiplicative
  step — the white-top-hat rescale, the DoG envelope, and the WBNS wavelet
  thresholding — is calibrated for [0, 1] input; at 0.046 scale they all
  over-suppress the signal and produce a near-blank output. Both functions
  now normalise to [0, 1] by the actual image maximum immediately after the
  dtype conversion, before any processing begins.
## [1.5.115] - 2026-07-03
### Fixed
- **"Upscaling didn't work" visual confusion.** The upscaled layer is scaled to
  `source_scale / 2` so both layers occupy the same world-space field of view
  (correct for alignment). But this meant the 2x extra resolution was invisible —
  both layers appeared identical in the napari canvas until you zoomed in. Now a
  napari notification confirms success and explains: "Upscaled X: WxH → W2xH2 px
  (2× linear). Both layers occupy the same field of view — zoom in to see the
  extra resolution in 'Upscaled X'."
### Note
- The "aggressive preprocessing / yellow field" report is fixed by the CLAHE
  range normalisation in 1.5.105 (`_safe_equalize_adapthist`). Users still
  experiencing this should update to >= 1.5.105. All four CLAHE call sites in
  `image_processing_tools.py` route through the safe wrapper that min-max
  normalises to [0, 1] before CLAHE, preventing the near-zero collapse.
## [1.5.114] - 2026-07-03
### Fixed
- **`QSizePolicy` NameError crashing Time-Series, FRAP, VPT, Z-Stack, and other
  workflows on open.** `ts_cellpose_tools.py` imported it as `_QSP` but used the
  bare `QSizePolicy` name — fatal NameError on any pipeline that invokes
  `_add_run_ts_cellpose`. Fixed by exporting both names from the local import.
- **Pixel-size gate removed from non-imaging workflows.** `_add_workflow_header`
  was injecting the pixel-size QGroupBox into every pipeline including FD-Curve,
  Droplet Fusion, and Colocalization. Now gated behind `include_pixel_gate=True`
  (only set on Condensate, Time-Series, General, and Fibril imaging pipelines).
- **Title clipping** fixed in all 7 separate workflow modules (brightfield,
  in-vitro ×2, FRAP, VPT, FD-curve, z-stack) — all QFormLayout instances now
  get a `setContentsMargins(9, 20, 9, 6)` top margin so the group-box title
  never sits on top of the first content row.
### Changed
- **Status-circle UEX corrected to match the temperature-module design.** The
  `StatusComboBox` inline-dot approach (wrong — inside the widget) is removed.
  Key layer-selector fields now use `_layer_row` (in the condensate pipeline
  tools) or `label_with_circle` (in the separate workflow modules), placing the
  dot as a column to the *left* of the field label — exactly as designed.
  Circles correctly show red (required) / yellow (optional) → green on selection.
  A new `label_with_circle()` helper in `field_status.py` makes this available
  to any `QFormLayout.addRow()` call with one line.
## [1.5.113] - 2026-07-03
### Added (field-status circles — rollout)
- **The field-status circle is now on the key input of every step in every
  workflow.** `create_layer_dropdown` (the shared layer-selection widget used
  across all pipelines and tools) now returns a `StatusComboBox` — a QComboBox
  that paints a small status dot at its left edge: red when no valid layer is
  selected, green once a real layer is chosen. Because it is still a QComboBox,
  every existing call site works unchanged, so this rolls the required-input
  indicator out universally with no per-form edits. Combined with the Step 1
  file-I/O block and pixel-size gate added to each pipeline header, the key
  required inputs (file loaded, pixel size, layer selections) all carry the
  red→green status indicator — the ~80% scope from the original design.
## [1.5.112] - 2026-07-03
### Fixed / Changed (workflow UI/UEX)
- **Horizontal scrollbars removed from every workflow.** The 7 separate workflow
  modules (brightfield, in-vitro fluor & BF, FRAP, VPT, force-distance, z-stack)
  created their scroll areas without the always-off horizontal policy, and the
  standalone-tool dock path had no scroll area at all. All dock paths now disable
  the horizontal scrollbar so content fits the width (vertical scroll only).
- **Field-status header rolled out to the main pipelines** (Cellular Condensate
  fluorescence, Time-Series, Object/Pixel Colocalization, General, Fibril): each
  now opens with the Step 1 file-I/O status block (green once an image is loaded)
  and the conditional pixel-size gate — the same UEX pattern as the temperature
  module — via a shared `_add_workflow_header` helper. Layout spacing tightened
  for a more compact dock.
## [1.5.111] - 2026-07-03
### Fixed (µm scale consistency across all layers)
- Every layer now preserves the micron scaling of the primary image, so masks,
  processed images, upscaled layers, and overlays all occupy the same field of
  view and stay aligned (previously only the source image carried the µm scale,
  so derived layers — like the upscaled image — rendered at the wrong size).
  Implemented as a single `inserted`-event listener in FileIO plus a re-align
  when the reference scale is set: Image/Labels layers are aligned by field of
  view (so an upscaled 2× mask gets half the reference pixel size), Shapes/Points
  overlays inherit the reference per-pixel scale, and any layer that already
  carries a deliberate non-unit scale is left untouched. No per-call-site changes
  were needed — it covers all ~100 layer-creation calls centrally.
## [1.5.110] - 2026-07-03
### Changed
- **Upscaling interpolation switched from bicubic spline to Akima.** The bicubic
  `RectBivariateSpline` overshoots at sharp intensity edges, producing ringing
  halos and negative values around bright puncta (hundreds of counts below
  background, then clipped). A separable 2-D Akima interpolant is local and
  shape-preserving: on a puncta test it produced zero negative/ringing pixels vs
  52 for bicubic. Falls back to bicubic if Akima is unavailable.
### Fixed
- **Upscaled layer now aligns physically with its source** (scale set to the
  source scale ÷ the upscale ratio). Previously the upscaled layer was added at
  scale 1 while the source could carry a µm scale, so the source appeared
  "embedded" as a small image inside a larger upscaled frame. The final
  multiplication remains 2× — each "Upscaled X" is 2× of its own source X, with
  no nested/compounding upscales.
## [1.5.109] - 2026-07-03
### Fixed
- Silenced the napari `Window.qt_viewer` FutureWarning (deprecated public
  access, removed in napari 0.8). The two places that read the Qt canvas size now
  prefer the private `_qt_viewer` attribute and suppress the warning on the
  public fallback — no behaviour change, just no console warning.
## [1.5.108] - 2026-07-03
### Fixed (crash on Home / reset view)
- **"cannot convert float NaN to integer" crash when pressing Home.** The
  \'Object Diameter\' and \'Cell Diameter\' line-annotation layers were created
  empty on every image load, and an empty Shapes layer reports a NaN extent in
  this napari build. reset_view (Home) then computed a NaN camera zoom, which the
  scale-bar overlay hit with floor(log(NaN)) once the bar was in µm mode. Fix:
  the diameter layers are now seeded with a single invisible near-zero-length
  line so their extent is finite; measurement skips the seed (it reads the last
  non-degenerate line), so results are unchanged.
- Hardened both scale-bar paths against non-finite/zero pixel sizes
  (`_enable_auto_scale_bar` validates the scale is finite and positive;
  `draw_custom_scale_bar` rejects NaN/inf inputs), so no scale-bar code can put a
  NaN into the world extent.
## [1.5.107] - 2026-07-03
### Changed
- **Upscaling set to 2×** (linear) to match v1.0.0. This corrects 1.5.106, which
  had changed it to 4× based on a miscommunication: v1.0.0 used 2×, and it is the
  newer 4× that was the regression. Images ≥ 2048px are left unscaled as before.
## [1.5.106] - 2026-07-03
### Changed
- **Upscaling restored to 4×** (linear) in the fluorescence pipeline, matching
  v1.0.0 behaviour — the 1.5.0 performance refactor had reduced it to 2×. Because
  4× multiplies the pixel count 16×, the factor now steps down automatically
  (to 3×/2×/1×) only when 4× would exceed a memory-safe 4096px output bound:
  e.g. 512→2048 and 1024→4096 at full 4×, a 1500px image falls back to 2×.
  All downstream micron sizes, object/cell diameters, and ball radius scale from
  the actual upscale ratio, so they stay consistent at any factor.
## [1.5.105] - 2026-07-03
### Fixed (critical — 2-D fluorescence pipeline regression)
- **"Yellow field / everything in one bin" background-removal output.** CLAHE
  (`equalize_adapthist`) requires float input in [0, 1], but the enhanced
  RB-Gauss background removal (and the preprocessing step) fed it the
  background-subtracted image in the ORIGINAL intensity scale (values in the
  thousands). On skimage ≥ 0.26 that raises; on older skimage it clips every
  pixel to the maximum, collapsing the image to a near-uniform field. All four
  CLAHE calls now go through a `_safe_equalize_adapthist` wrapper that min-max
  normalises to [0, 1] first, restoring structured output. This also fixes the
  over-aggressive intensity removal in preprocessing (same root cause).
## [1.5.104] - 2026-07-03
### Added
- **Batch phase diagram** (temperature workflow, Step 5): after a batch, PyCAT
  parses the TIFF filenames for the swept variable (ignoring constant buffers)
  and replicates, then plots T_cloud vs that variable with temperature on the
  y-axis. The **2-phase region is shaded** with sharp edges at the plot borders
  and a smooth **Akima** interpolation of the cloud points as the boundary; LCST
  (above) / UCST (below) selectable. If the filenames can't be parsed
  unambiguously (no varying token, or more than one), it warns and asks for
  manual specification instead of guessing.
### Fixed
- **Turbidity transition arrows** now point in the temperature-sweep direction
  (heating branch → up, cooling branch → down) instead of a fixed layout.
- **Scale bar** now shows microns whenever a valid pixel size is known — including
  one entered by the user in the pixel-size gate — not only when it came from
  metadata; the on-screen bar refreshes when the pixel size is set.
- **Horizontal scrollbars** removed from the workflow docks (set always-off on
  all dock scroll areas) so long buttons/labels fit instead of overflowing.
- **Step 1 (file I/O)** in the bottom workflow checklist now auto-completes when a
  workflow is opened with an image already loaded (previously stayed pending).
## [1.5.103] - 2026-07-03
### Changed
- Contrast Cascade: the focus-vs-growth **dim threshold** and **blur threshold**
  are now adjustable fields in the diagnostic panel (and flow through to the
  plot), so the below-focus/growth cutoff can be calibrated to real data.
## [1.5.102] - 2026-07-03
### Added — Contrast Cascade
New tool (Toolbox → Image Segmentation → Contrast Cascade) for images with large
object-to-object brightness swings, e.g. a bright condensate body that grows much
dimmer fibers. Three parts:
- **Visualise**: split the intensity range into a cascade of bands, each shown as
  a coloured napari layer with its own contrast, plus a log/CLAHE tone-mapped
  view — so bright and dim structure are visible at once.
- **Segment**: a Random Forest trained on brightness-INVARIANT features
  (local-contrast normalisation + ridge/tubeness filters), so it can separate
  body / fiber / background across the brightness swing — unlike the single-
  intensity RF, which only learns a threshold.
- **Diagnose**: for each object, compare edge sharpness AND brightness to the
  body to tell WHY dim objects are dim — dim+blurry ⇒ likely below focus,
  dim+sharp ⇒ likely nucleation/growth. Uses a size- and intensity-invariant
  edge-steepness measure; shows a sharpness-vs-intensity plot and a table.
## [1.5.101] - 2026-07-03
### Changed (UI/UX audit)
- **Data QC report redesigned to teach, not just score**: an overall verdict
  banner, a "what good data looks like / how to improve" line under every metric,
  and a "how it is measured" caption under each diagnostic panel — so the report
  guides users to better data instead of only reporting numbers.
- **Tooltips**: added descriptive tooltips across the Condensate Physics,
  Plotting Widget, and Advanced Analysis panels (coverage ~37% → ~50%).
- Verified all 65 menu actions resolve to defined handlers (no dead menu items).
## [1.5.100] - 2026-07-03
### Added (tables → graphs, continued)
- **Spatial metrology** multi-panel plot: NND distribution, Ripley's L(r)−r
  (>0 = clustered), pair-correlation g(r) (>1 = clustered), and radial
  localisation density — each showing per-cell curves with the mean overlaid.
- **Morphological complexity**: per-object metric distributions (fractal
  dimension, lacunarity, tortuosity, orientation) as small-multiple histograms.
- New generic `plot_distributions` helper for per-object metric histograms.
## [1.5.99] - 2026-07-03
### Added (tables → graphs, continued) & Fusion tab
- **Molecular counting** plot: the step-variance vs intensity line through the
  origin (slope = single-fluorophore brightness ν) plus the molecule-count
  distribution. `count_molecules_pooled` now returns the pooled variance pairs.
- **Fusion relaxation tab is now functional**: a new `extract_fusion_relaxation`
  detects merge events and follows the merged droplet's aspect ratio as it
  relaxes; the tab fits it, takes a characteristic length R (auto-uses the
  droplet's equivalent radius if left at 0), reports η/γ = τ/R, and plots the
  relaxation curve with the fit.
- **Intensity profiles**: line-scan and radial profile plots (radial shows every
  centre faint with the mean solid).
- **Client enrichment / partition**: per-condensate enrichment histogram with the
  median and the 1× (no-enrichment) reference marked.
## [1.5.98] - 2026-07-03
### Added (tables → graphs)
- **MSD trajectory plot**: per-track MSD curves (semi-transparent) with the
  solid ensemble mean + SEM band and the fitted power law, log-log — in both the
  VPT and Condensate Physics MSD steps.
- **Viscoelastic moduli G′/G″** (microrheology) via the Mason GSER from the MSD,
  plotted vs frequency with the crossover marked (VPT).
- **FRAP recovery curve** with the fitted model, mobile-fraction plateau, t½,
  and R² — replaces the FRAP results table as the primary output.
- **Coarsening kinetics** plot (radius vs time with the fitted t^1/3 / t^1/2
  curves) and **Kaplan–Meier survival** step curve.
- New `analysis_plots.py` module and `per_track_msd_curves` / `compute_moduli_gser`
  helpers.
## [1.5.97] - 2026-07-03
### Changed (scientific accuracy)
- **MSD uncertainty** is now computed per-track (tracks are the independent
  unit), adding `msd_sem` and `n_tracks`; the old pooled-pairs `msd_std`
  understated uncertainty because overlapping displacement pairs are correlated.
  Removed the vestigial unused `microns_per_pixel` argument.
- **Anomalous-diffusion fit** now uses a weighted direct non-linear fit of
  MSD = 4Dτ^α (seeded by log-log), removing the log-transform bias and
  down-weighting noisy large-lag points. Recovers D/α more accurately.
- **Coarsening**: removed dead code that made `arrested_r2` meaningless; added a
  `mechanism_confidence` and `mechanism_caveat` (t^1/3 vs t^1/2 are hard to
  separate over short ranges), plus `radius_change_frac`. The UI now warns when
  confidence is low.
- **Ripley's L** now uses the rigorous **border-method** edge correction when
  per-point boundary distances are available (supplied by the spatial-metrology
  UI from the cell mask), instead of a crude isotropic-weight approximation.
- **Fusion relaxation** accepts a characteristic length R and returns η/γ = τ/R
  (inverse capillary velocity) in addition to τ.
- **Partition coefficient** now reports dense and dilute intensities (raw and
  background-subtracted) and the background explicitly; stopped clipping (which
  biased means near background); clarified that "background" is the instrument
  offset from a signal-free region — never the dilute phase.

## [1.5.96] - 2026-07-03
### Fixed
- Time-Series workflow: the Start/End frame range no longer resets to the full
  stack when a downstream step adds a layer. The range is now locked as soon as
  the user sets it (edits a spinbox or ticks "Restrict to frame range"), while
  programmatic refreshes are still allowed to update the bounds.

## [1.5.93] - 2026-07-03
### Added
- Data Quality Control dashboard: **Save Report (PNG + CSV)** button — saves the
  report figure and a self-documenting metric table (value, status, how each
  metric is measured, and what good data looks like).

## [1.5.92] - 2026-07-03
### Added
- **Data Quality Control dashboard** (Toolbox → Data Visualization → Data
  Quality Control): a teaching-oriented acquisition-quality report. Each metric
  returns a colour-coded status, the value, how it is measured, and what good
  data looks like, with a diagnostic plot per metric.
  - CORE (absolute thresholds): saturation/clipping, focus/sharpness, SNR/noise,
    vignetting/flat-field, ghosting (double image, via the cepstrum), and lateral
    drift (phase cross-correlation).
  - ADVISORY (heuristic or need input): spherical aberration (through-focus
    asymmetry on a z-stack), Nyquist spatial sampling (pixel vs λ/4·NA), temporal
    sampling, mechanical vibration, and chromatic aberration.

## [1.5.91] - 2026-07-03
### Added
- Temperature workflow: **Save Results (CSV)** and **Clear Results** buttons —
  saves the transition summary (T_cloud, T_clear, hysteresis, branches) and the
  full turbidity curve; restores the data output that the plot had replaced.
- Adjustable **onset threshold** (% of baseline-to-peak amplitude) for the
  baseline transition-detection method.
### Changed
- Dropped `focus_score` from the stored turbidity curve so it is not offered as
  a Plotting Widget Y-axis option (it is collinear with turbidity).
- Removed the redundant **Random Forest Classifier** menu item (it opened the
  same unified segmentation widget as Cellpose; RF is still available there).

## [1.5.90] - 2026-07-03
Consolidated summary of changes since 1.5.39 (many iterative point releases).

### Added
- **Temperature-Dependent Microscopy** workflow: entropy-based turbidity curve,
  automatic clear-frame detection (coefficient-of-variation), and a pop-up
  transition plot with the heating branch in red and cooling in blue.
- **Transition-temperature detection** with two selectable methods: baseline
  departure/return (onset of appearance / completion of dissolution) and
  steepest-point midpoint. T_cloud/T_clear are assigned by signal direction, so
  it is correct for both LCST and UCST systems.
- **Gray-preserving static-pattern (dust/scratch) correction**
  (`corrected = frame - reference + mean(reference)`), available as a selectable
  napari layer and as an export option in the interactive and batch steps. The
  reference frame is rebuilt from its neighbours so it is not a flat outlier.
- **Calibration-frame background correction** tool (flat-field division and
  background subtraction) under Toolbox → Image Processing.
- **Auto scale bar at load** (from image metadata) and a **Home** menu action
  that fits the view to the selected layer.
- **Batch annotated-MP4 export** and **batch pattern-corrected TIFF export** for
  the temperature workflow.
- **PyCAT branding**: Windows taskbar icon, PyCAT logo on the napari welcome
  screen, and a "PyCAT <ver> • napari <ver>" version line.

### Changed
- The app now launches **maximized** (robustly, via a double-shot timer).
- **Focus-drift correction defaults OFF** for turbidity: the focus metric is
  collinear with condensate formation, so regressing it out over-corrects.
- Cross-workflow **UI compactness**: layer dropdowns, long buttons, long
  checkboxes/radio buttons, and text fields now shrink instead of forcing the
  dock wider than its slot; long description labels word-wrap; long group titles
  shortened. This removes the horizontal scrollbar from the workflow docks.
- Merged the temperature CSV / folder inputs into a single auto-detecting field.

### Fixed
- **Condensate segmentation quality regression**: `segment_subcellular_objects`
  now defaults to whole-image processing (byte-for-byte PyCAT 1.0.0). The
  bounding-box crop starved the gaussian-background and CLAHE context; it is now
  opt-in via `crop_to_cell=True`.
- **Scale-bar black-canvas bug** in the temperature workflow: setting
  `Layer.units` on a lazy 3-D stack triggered a black render on napari 0.7.1.
  PyCAT now drives the scale bar from `Layer.scale` + `scale_bar.unit` only.
- **Duplicated Cell Segmentation widget** in the condensate, object-coloc and
  pixel-coloc workflows (a backward-compat Random-Forest shim re-added the
  unified widget) — removed the redundant call.
- Entropy reference-frame **outlier spike** and CPU-only Cellpose slowness now
  surface a one-time in-app warning with GPU install guidance.


### Changed
- **Minimum Python is now 3.12** (supported range `>=3.12,<3.14`). Python 3.9
  reached end-of-life on 2025-10-31 and core dependencies (NumPy, napari, and
  others) have dropped it; moving the floor keeps installs resolvable and the
  toolchain current. The upper bound is a tested-ceiling promise and will be
  widened once 3.14 wheels are verified across the stack.
- Updated build metadata (classifiers) and black/ruff `target-version` to py312.
### Added (recent analysis modules)
- Force-Distance Curve (DNA tethering) workflow with ssDNA/ssRNA FJC model and
  rip/unzip (G-quadruplex) detection.
- Molecular Counting by Photobleaching (step-noise / Mutch method).
- Gaussian Spot Localization (sub-pixel centre + PSF width, 2D/3D).
- VPT bead quality-fit classification (singlet / aggregate / out-of-plane) with
  aggregates routed to a secondary tracked population.
- Client Partition / Enrichment (second-channel recruitment into condensates).
- Intensity Profiles (line-scan + radial, interface-width estimation).
- Exposed Morphological Complexity metrics (fractal dimension, lacunarity,
  tortuosity, orientational order) via a Toolbox panel.
- Spatial Randomness, FFT bandpass, manual threshold, best-slice, and
  temperature-dependent condensate tools.
### Note
- Install into a Python 3.12 environment first, e.g. `mamba create -n pycat-env python=3.12`, then install PyCAT.

## [1.0.0] - 2024-11-22
### Added
- Initial public release of PyCAT-Napari
- Complete GUI interface built on Napari viewer
- Core functionalities for biomolecular condensate analysis:
  * Image processing and analysis tools
  * Fluorescence image analysis capabilities
  * Condensate feature detection and measurement
  * Data visualization tools
  * Colocalization and Correlation analyses 
- Command-line interface via `run-pycat` command
- Python API for programmatic access to analysis tools
- Support for multiple imaging file formats
- Integration with popular scientific Python libraries
- Platform support for Windows, macOS (including Apple Silicon), and Linux
- Comprehensive error handling and user feedback
- Basic documentation and usage examples

### Dependencies
- Compatible with Python 3.9+
- Core dependencies include:
  * napari
  * numpy
  * opencv-python-headless
  * scikit-image
  * scipy
  * torch
  * And other scientific computing libraries

### Fixed
- (List any bug fixes here)

### Changed
- (List any changes to existing features here)

### Deprecated
- (List any features that are deprecated and will be removed in future releases here)

[1.0.0]: https://github.com/BanerjeeLab-repertoire/pycat-napari/releases/tag/v1.0.0

## [1.0.2] - 2025-06-26
### Added
- Batch processing module (`batch_processor.py`): session config recording,
  JSON export/import, and batch runner with folder picker and progress dialog
- Spatial ACF analysis module (`spatial_acf_tools.py`): per-cell LIR-cropped
  SACF, drawn-rectangle mode, and whole-image mode with Gaussian sigma fitting
  and cluster diameter output per slice
- `largestinteriorrectangle` added as a core dependency

### Fixed
- Cellpose v4 compatibility: `model_type` → `pretrained_model` argument
- GPU acceleration: CUDA PyTorch install documented as primary method

## [1.5.0] - 2026-07-01
### Added
- **New analysis pipelines**: Cellular Condensate Analysis (Brightfield), In Vitro
  Condensate Analysis (Fluorescence & Brightfield), Time-Series Condensate Analysis,
  and Z-Stack (3D) Condensate Analysis, each with its own workflow checklist and
  batch-replay support
- **Multi-dimensional file I/O** (`file_io/multidim_io.py`): lazy 4D (T,Z,Y,X)
  loading for nested time-series-with-Z-stack acquisitions in both IMS and
  OME-TIFF/CZI, replacing prior behavior that silently discarded one dimension
  when both T>1 and Z>1; multi-position/multi-scene detection and selection
  dialog for IMS sibling files and AICSImage scenes
- **Z-stack (3D) condensate segmentation** (`zstack_segmentation_tools.py`):
  3D background removal, 3D cell segmentation (per-slice Cellpose stitched
  across Z via IoU overlap linking), 3D condensate segmentation (per-slice 2D
  pipeline merged into true 3D objects via 3D connected-component linking),
  and volumetric metrics (volume, sphericity via marching-cubes, ellipsoid
  axis lengths, anisotropic Z-step handling)
- **Pseudo-3D (tri-planar) linear filtering** (`pseudo3d_tri_planar_filter`):
  runs Gaussian/Gabor/DoG filters along XY, XZ, and YZ planes (or XY/XT/YT for
  time series) and averages the result, exploiting genuine correlation between
  adjacent Z-slices or oversampled frames; applied to Z-stack background
  removal and, with a frame-to-frame correlation regime check
  (`estimate_temporal_correlation`) to avoid misuse on undersampled time
  series, to time-series preprocessing
- **TrackMate integration** (`trackmate_bridge.py`): optional bridge to real
  TrackMate (Jaqaman LAP tracker, Kalman tracker) via an embedded headless
  Fiji/ImageJ2 instance (pyimagej). PyCAT's own condensate/cell detections are
  injected directly as TrackMate spots, bypassing TrackMate's detection step;
  results convert back to PyCAT's standard trajectory schema for use by all
  downstream biophysics tools. New `trackmate` optional-dependencies extra
- **Time-series pipeline**: keyframe Cellpose segmentation with nearest-keyframe
  propagation, phase-correlation drift correction, per-frame spatial metrology,
  frame-range/XY-ROI selection, and lazy zarr-backed stack preprocessing
- **Trajectory tracking and dynamics** (`dynamic_spatial_tools.py`): greedy
  nearest-neighbour and Bayesian (Hungarian/LAP) trajectory linking with
  velocity-assisted prediction and gap closing, merge/fission detection,
  cluster lifetime analysis, neighbourhood persistence, growth/shrinkage
  kinetics
- **Condensate biophysics** (`condensate_physics_tools.py`): MSD/anomalous
  diffusion fitting, bimodal intensity decomposition and Csat estimation,
  fusion relaxation and coarsening-mechanism fitting, Kaplan-Meier survival
  analysis, unified frame-quality diagnostics distinguishing photobleaching
  from focal drift
- **Spatial analysis suite** (`spatial_metrology_tools.py`,
  `morphological_complexity_tools.py`, `organizational_metrics_tools.py`):
  nearest-neighbour distance, Ripley's L, pair correlation function, Voronoi/
  Delaunay metrics, convex hull metrics, fractal dimension, lacunarity,
  tortuosity, orientation order, spatial entropy, DBSCAN clustering,
  inter-condensate spacing, per-cell occupancy
- **Brightfield and in-vitro toolboxes** (`brightfield_tools.py`,
  `invitro_tools.py`): optical density metrics, contact-angle measurement,
  field-level statistics (volume fraction, number density, size distribution),
  Csat estimation via lever-rule fitting on dilution series
- **Fibril pipeline additions**: binary-mask labeling step, morphological
  complexity and organizational metrics integration
- Session reload from a previous output folder without re-running analysis
  (`file_io/session_loader.py`)

### Changed
- **Time-series analysis loop parallelized**: per-frame condensate segmentation
  (previously fully serial) now dispatches across a `ProcessPoolExecutor`,
  giving roughly 6-8x wall-clock speedup on multi-core machines; frames read
  directly from filesystem zarr stores rather than being pickled through IPC
- **Combined single-pass stack preprocessing**: preprocessing and background
  removal, previously two sequential full-stack `ProcessPoolExecutor` passes,
  now run as one combined pass, roughly halving I/O and pool-startup overhead
  when both are enabled (the default)
- **Eliminated redundant double connected-components labeling** in the
  time-series analysis loop (same array labeled twice per frame/cell iteration)
- **Keyframe Cellpose memory footprint** reduced ~20x via a lazy
  nearest-keyframe view (`_KeyframeMaskStack`) instead of materialising a full
  duplicated-frame `(T,H,W)` array
- **Algorithm-level speedups** in `image_processing_tools.py`: white tophat
  (square footprint, ~8.5x), LoG→DoG reformulation (~1.3x), parallel Gabor
  bank (ThreadPoolExecutor, ~3.3x), GPU-accelerated rolling ball via CuPy when
  available, faster CLAHE tiling (~5.5x)
- **Segmentation bounding-box crop optimisation** in
  `segment_subcellular_objects`: expensive per-cell operations (background
  removal, Felzenszwalb, Niblack/Sauvola) now run on a cropped ROI rather than
  the full image, ~5-20x speedup for typical multi-cell fields

### Fixed
- IMS and generic (TIFF/CZI) stack loaders no longer silently discard Z-stack
  data when a file has both T>1 and Z>1 — previously forced a single-timepoint
  choice (IMS) or picked T-xor-Z as "the" stacking dimension (generic loader),
  losing an entire dimension of acquired data in either case
- Multi-position selection dialog now correctly defaults to the file the user
  actually opened rather than the numerically-lowest position after sorting
- Z-stack pipeline `ball_radius` no longer silently diverges between the
  background-removal step (user-set via spinbox) and the condensate-
  segmentation step (previously read an unpopulated `data_repository` key and
  always fell back to a hardcoded default)
- Cellpose v4 compatibility: `model_type` → `pretrained_model` argument
  (previously silently ignored)
- GPU acceleration: CUDA PyTorch install documented as primary method,
  verified safe for CPU-only machines
- Spatial metrology and merge/fission detection call-signature mismatches
  across brightfield and in-vitro UIs (wrong arguments/nonexistent kwargs)
  found and fixed via cross-module audit
- Batch step registry coverage gaps for several recorded-but-unregistered
  pipeline steps

[1.5.0]: https://github.com/BanerjeeLab-repertoire/pycat-napari/releases/tag/v1.5.0
