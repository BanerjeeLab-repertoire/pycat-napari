# Claude Code spec — Decompose `file_io.py`: the second concentration point

> **✅ STATUS — DONE, shipped in 1.6.146 + 1.6.147** (stamped 2026-07-20 from a CHANGELOG cross-reference). `file_io.py` 2805 → 1670 (1.6.146) → 1633 (1.6.147, lazy wrapper + exception annotation); decomposition closed.

**Date:** 2026-07-19 · **Target tree:** 1.6.144 · Verified against the 1.6.144 tree. Second
refactoring target after the `vpt_ui.py` decomposition succeeded (2458 → 1138, −54%, well past its 25%
goal). Same method, same safety-net discipline. Behaviour-preserving; **no new features.**

## Why this target, and why it is ready
The external audits named `file_io.py` repeatedly: *"`file_io.py` should eventually become a thin
façade… It should not retain format-specific pixel logic."* Verified state — the surrounding package
is **already well decomposed**; `file_io.py` is the residual that never got moved:

```
file_io/  dialogs.py  image_reader.py  image_source.py  image_structure.py  lazy_guard.py
          lazy_sources.py  local_cache.py  metadata_extract.py  multidim_io.py
          napari_adapter.py  routing.py  scenes.py  session*.py  stack_access.py
          stack_load.py  storage_probe.py  tagging.py  tiff_planes.py  viewer_load.py
          writers.py  zarr_compat.py   readers/{czi_bioformats, image_reader_2d, ims_reader,
                                                mask_reader, stack_layer_builders, stack_metadata}
```
So the destinations mostly **already exist**. This is a move-into-existing-homes job, not a design job.

**The safety net is the strongest in the codebase** — 20+ targeted tests: `test_file_io`,
`test_no_eager_reads`, `test_one_plane_reads_one_plane`, `test_reader_cache`,
`test_reader_cache_closes`, `test_materialize_stack`, `test_image_reader_seam`,
`test_generic_stack_reader_retention`, `test_ims_reader_{extraction,retention}`,
`test_scene_stack`, `test_scenes`, `test_scene_switcher`, `test_czi_bioformats_reader`,
`test_lazy_sources_headless`, `test_session_*`, plus the route-equivalence suite. A behaviour-changing
move fails loudly.

## Verified structure (2805 lines)
| region | lines | note |
|---|---:|---|
| module-level helpers (`_tiff_pixel_size_um`, `_ome_pixel_size_um`, `_clean_filename_token`, `derive_layer_name`, `_lazy_contrast_limits`, `_lazy_backing_label`) | ~1–285 | pure functions, no Qt |
| `LayerDataframeSelectionDialog` | 286–533 (247) | Qt dialog — `dialogs.py` already exists (231 lines) |
| `ChannelAssignmentDialog` | 533–689 (156) | Qt dialog — same home |
| `_ZarrTYX` | 689–752 | lazy wrapper — `lazy_sources.py` is its home |
| `StackLoadCancelled` | 808 | belongs with typed errors |
| `FileIOClass` | 817–2805 (~1990) | the bulk |

Largest methods inside `FileIOClass`: **261, 232, 211, 183, 117, 116, 113, 87** lines — eight methods
are ~1300 lines, about two-thirds of the class.

## Target
**`file_io.py` ≤ 1700 lines (≥ 39% reduction).** Stretch goal ≤ 1400. The endpoint the audits
describe — a thin façade that routes to reader/source/router/adapter and holds no format-specific
pixel logic — is a later increment; this one moves the clearly-relocatable mass.

## The moves, lowest risk first
1. **The two Qt dialogs → `dialogs.py`** (~400 lines). Pure widget classes with an existing home. Zero
   science risk. Do this first; it alone is 14% of the file.
2. **`_ZarrTYX` → `lazy_sources.py`** (~63 lines). Joins `_TiffPageStack`, `_LazyArraySource`,
   `_TiffPageStackZYX/TZYX`. **Keep `lazy_sources.py` Qt-free** — `test_lazy_sources_headless.py`
   enforces it in a subprocess. Re-export from `file_io.py` (the module does this for the other
   wrappers already; mirror it).
3. **Pixel-size / naming helpers → a new `file_io/naming.py`** (or `metadata_extract.py` if they fit
   its remit — check first) (~285 lines). `_tiff_pixel_size_um`, `_ome_pixel_size_um`,
   `_clean_filename_token`, `derive_layer_name`, `_lazy_contrast_limits`, `_lazy_backing_label`. Pure,
   headlessly testable.
4. **`StackLoadCancelled` → `utils/errors.py`** — the typed-error module shipped in 1.6.139 and this
   is exactly a typed failure. Re-export for back-compat; add it to the `PyCATError` family if the
   semantics fit (it is a cancellation, so consider whether it should derive from `PyCATError` or stay
   a control-flow signal — **decide deliberately and note why**).
5. **The IMS / generic / CZI open paths** (`_open_stack_ims` 261, `_open_stack_generic` 232,
   `_open_czi_streaming` ~117) → a new `file_io/stack_openers.py`, or extend `stack_load.py` if it is
   the natural home (check its current remit first). These are the format-specific pixel logic the
   audit says must leave `file_io.py`. Take them as **functions receiving what they need**, not
   methods reaching through `self` — that is what makes them testable and what makes the façade thin.
6. **`open_2d_image` (211) and `save_and_clear_all` (183)** — assess. If they are mostly orchestration
   with embedded per-format branches, extract the branches; if they are genuinely UI flow, leave them.
   Do not force a move that produces a worse seam.

## Rules (identical to the vpt_ui decomposition, which worked)
- **Move, don't rewrite.** Cut, paste, fix imports. Any behaviour change is a separate commit.
- **One move per commit**, `pytest -m core` between each.
- **No test may be edited to make a move pass.** If a test needs changing, behaviour changed — revert.
- **No new features.** No "while I'm here."
- If a move is blocked by a circular import, stop and report it — the cycle is the finding.

## Fold in the exception conversion
`file_io.py` holds **65** broad `except Exception` handlers (the package total is 284, ratcheted at
that value since 1.6.139). As each region moves, convert its handlers in the moved code:
- scientific/format failures → the typed errors from `utils/errors.py`
  (`UnsupportedFormatError`, `MetadataUnavailableError`, `InvalidCalibrationError`,
  `OptionalDependencyError`);
- genuine Qt-teardown / optional-probe handlers → annotate `# broad-ok: <reason>`.
**Lower the `file_io` ratchet** in `tests/test_exception_budget.py` to the achieved count. Converting
during a move is cheap because the code is already in your hands; a separate sweep later is not.

## Tests
- Every existing test passes **unmodified**.
- New pure modules (`naming.py`, `stack_openers.py`) get direct `core` tests — extraction's payoff is
  that this logic becomes headlessly testable, so realize it.
- **Lower the `file_io.py` per-file ratchet** (`tests/test_complexity_budget.py`, currently pinned at
  2805) to the achieved value so it cannot regrow.

## Steps
1. Dialogs → `dialogs.py`.
2. `_ZarrTYX` → `lazy_sources.py` (+ re-export; headless guard must stay green).
3. Helpers → `naming.py` + direct tests.
4. `StackLoadCancelled` → `utils/errors.py` (decide the base class deliberately).
5. Format open paths → `stack_openers.py` (as functions, not `self`-methods) + tests.
6. Assess `open_2d_image` / `save_and_clear_all`; extract only what yields a clean seam.
7. Convert exception handlers in moved code; lower the `file_io` exception ratchet.
8. Lower the `file_io.py` line ratchet to the achieved value.
9. Full `pytest -m core` green after EACH step.
10. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG **reporting the measured
    before/after line count and handler count** — the numbers are the deliverable.

## Definition of done
- `file_io.py` ≤ 1700 lines, containing orchestration/wiring, not format-specific pixel logic.
- Dialogs, lazy wrapper, naming helpers, and the format open paths live in their proper homes.
- New pure modules have direct headless tests.
- `file_io` broad-handler count materially reduced; ratchet lowered; remaining ones annotated.
- Line ratchet lowered; every pre-existing test passes unmodified.
- CHANGELOG reports the measured reduction.

## Cautions
- **`lazy_sources.py` must stay Qt-free** — `test_lazy_sources_headless.py` verifies this in a
  subprocess. Moving `_ZarrTYX` there must not drag in Qt.
- **Preserve re-exports.** Tests import `_TiffPageStack` and friends from `pycat.file_io.file_io`;
  anything moved must keep working from its old import path.
- **Do not touch the CZI seam behaviour.** The reported column discontinuity is still not converted
  into a fixture-level regression test; a refactor there could mask or mimic it. Move the code
  verbatim and leave the behaviour question to its own spec.
- Move format logic out as **functions taking explicit arguments**, not methods reaching through
  `self` — otherwise the façade stays thick and nothing became testable.
- One concentration point at a time: **do not start `ui_modules.py`** in the same version. Its safety
  net is far weaker (~17% name coverage, per the ratchet file's own note).
