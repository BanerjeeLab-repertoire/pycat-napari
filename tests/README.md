# PyCAT tests

Two layers of tests:

## 1. Infrastructure tests (pre-existing)
`test_import`, `test_run_pycat`, `test_central_manager`, `test_data_management`,
`test_file_io`, `test_general_utils`, `test_image_processing`,
`test_feature_analysis` — verify the app boots, files load, data flows through the
manager, and a few low-level utilities. These guard "did I break the whole app."

## 2. Analysis regression tests (new)
`test_coloc_metrics`, `test_frap_fitting`, `test_partition`,
`test_segmentation_refine` — verify the *science* on deterministic synthetic data
from `fixtures_synthetic.py`. Two kinds:

- **Known-answer tests** — the math has a ground truth (identical channels →
  Pearson 1.0; noise-free FRAP curve → its known mobile fraction; K = dense/dilute
  for a two-phase scene; fast refinement == original bit-for-bit). These assert
  real values now and should stay green.

- **Characterization / empirical tests** — behavior on realistic data where the
  "correct" value is a judgement call (a partial-overlap Pearson, a noisy-fit
  tolerance, a full-pipeline object count). The **structure and synthetic fixture
  are in place, but the reference value is a `TODO(maintainer)` constant set to
  `None`, so the test SKIPS until you fill it.** Search for `TODO(maintainer)` to
  find them:
  - `EMPIRICAL_PARTIAL_OVERLAP_PEARSON` (test_coloc_metrics)
  - `NOISY_FIT_NOISE_SIGMA` / `NOISY_FIT_MOBILE_TOL` (test_frap_fitting)
  - `GOLDEN_SEGMENTATION_OBJECT_COUNT` (test_segmentation_refine)

  Once you decide the validated reference (from a real image you trust, or a
  synthetic scene whose target you've agreed on), replace the `None` with the
  value and the test activates.

## Running
```
pytest tests/ -v
```
The analysis tests need the scientific stack (numpy/scipy/skimage) and the PyCAT
package importable; the full-pipeline golden master additionally needs the viewer
and is stubbed until wired to `_segment_core`.

## 3. UI refactor safety net (new)
`test_ui_structure` (static, runs anywhere) and `test_ui_smoke` (headless Qt) exist
specifically to protect UI *refactoring* — e.g. splitting the large
`ui_modules.py` into mixins — against the recurring failure mode where a change
silently breaks a menu or workflow.

- **`test_ui_structure`** parses `ui_modules.py` with `ast` (no Qt/napari needed)
  and asserts: the file parses; every `toolbox_functions_ui._add_X` referenced in a
  menu/workflow registration resolves to a method defined somewhere (here as `def`
  or lambda-bound, or in a sibling ui/tool module); each workflow layout attribute
  is still assigned; and the core UI classes still exist. This catches a
  moved/renamed/dropped widget method at *test time* instead of when a user clicks
  the menu — validated to fire on a simulated rename.
- **`test_ui_smoke`** forces an offscreen Qt platform and actually constructs the
  CentralManager / toolbox UI / MenuManager, catching mixin-composition / MRO
  errors, missing-attribute-at-construction, and import cycles that a static parse
  can't. Skips automatically where PyQt5/napari aren't installed.

**Recommended workflow for the `ui_modules.py` split:** run `pytest
tests/test_ui_structure.py tests/test_ui_smoke.py -v` before and after each step of
the refactor. If a class/method move is intentional, update the small contract lists
in `test_ui_structure` (the required class names / workflow layouts) deliberately —
that edit is the record that the move was on purpose.

## Why this exists
It makes permanent the "quantitative verification before shipping" done by hand
during development — e.g. the fast-vs-original refinement equivalence, previously
checked with a one-off `np.array_equal` and thrown away, is now a standing test.
It also guards the refactors flagged in the code audit (splitting `ui_modules.py`,
the eventual zarr `DirectoryStore`→`LocalStore` port) and catches silent numeric
drift from dependency updates.
