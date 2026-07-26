# PyCAT — Spec: fix the `pytest -m core` pandas collection error (test markers + guards)

*The `ModuleNotFoundError: No module named 'pandas'` that aborted `pytest -m core -o addopts= -v` is a
**test-classification** bug, not a code bug and not a `core.yml` bug. `core.yml` is correct as written.
The failing file — `tests/test_kaplan_meier.py` — is **unmarked** and imports `pandas`/`numpy` at module
scope, so it falls through the conftest's collection guards in a partially-provisioned environment and
dies at import before `-m core` can deselect it. This spec pins the classification and hardens it so it
can't recur.*

---

## Root cause (exact)

`tests/conftest.py` decides what to collect via `pytest_ignore_collect` (lines 93–124). Two mechanisms:

1. **Minimal lane** (`_base_stack_absent()` True — numpy+pytest only): a file is ignored unless
   `_file_has_core_marker(tree)` finds a `core` marker via AST. `test_kaplan_meier.py` **has no marker
   at all**, so in a *true* minimal lane it is correctly ignored — good.
2. **Partial env** (some base packages present, some absent): the scan walks module-scope imports and
   ignores the file if any imported top-level package is in `_absent_packages()`.

The failure you hit is the seam between these two. `test_kaplan_meier.py`:
```python
import numpy as np
import pandas as pd          # ← hard module-scope import
import pytest

def _km():
    m = pytest.importorskip("pycat.toolbox.condensate_physics.survival")   # ← guards the SCIENCE module
    return m.kaplan_meier_lifetimes
```
The file **guards the science module** with `importorskip` (so it clearly *intends* to be collectable in
a lean env) but then **hard-imports pandas at module top**, which contradicts that intent. In any
environment where pandas is missing *but the conftest doesn't classify the run as fully minimal* (e.g.
pandas absent while another base package is present, or `-o addopts=` altering plugin/collection
behavior), the ignore logic doesn't fire and collection hits `import pandas` → hard error → whole run
aborts (exit 2).

`test_kaplan_meier.py` is one of **19 unmarked test files that hard-import pandas/scipy/skimage at module
scope** (full list in the appendix). It is the one that happened to be collected first; the others are
latent instances of the same classification gap.

---

## The fix — three parts, smallest first

### Part 1 (REQUIRED) — mark every science test with the tier it actually needs

The workflow's contract (`core.yml` lines 198–200): *a test is `core` only if it passes with **numpy +
pytest only**; anything needing scipy/pandas/skimage/... is `base`.* Apply that literally.

`test_kaplan_meier.py` needs pandas → it is a **`base`** test. Add, directly under the imports:
```python
pytestmark = pytest.mark.base
```
This is the exact convention already used across the suite (`test_channel_designations.py:15`,
`test_size_distribution_mle_characterization.py:15`, etc. — `pytestmark = pytest.mark.base`; core-only
files use `pytestmark = pytest.mark.core`).

Effect: the `core or base` lane (which installs the full compute stack) runs it; the minimal lane's AST
marker scan sees no `core` marker and **ignores it without importing** — so pandas is never touched
there. The wheel lane (`core or base`) runs it too.

**Do the same for every unmarked heavy-import test**, choosing the tier by the numpy-only rule:
- **`base`** if the file imports pandas / scipy / skimage / matplotlib / cv2 / sklearn / seaborn /
  networkx anywhere it needs at run time. This is the correct tier for nearly all 19 (KM, VPT viscosity
  chain, linkers, coloc, feature analysis, data-QC, etc. — appendix lists them).
- **`core`** only if, after removing hard heavy-imports, the test genuinely runs on numpy alone. Most of
  these won't qualify; don't force it.

If a file legitimately contains *both* numpy-only tests and pandas-needing tests, either split it, or
mark the file `base` (simplest — the `core or base` lane still runs everything; you only lose minimal-lane
coverage of the numpy-only subset, which is acceptable and can be recovered later by splitting).

### Part 2 (RECOMMENDED) — make the KM file's imports match its stated intent

The file uses `importorskip` for the science module but hard-imports pandas. If you want it collectable in
*any* lane without aborting (belt to Part 1's suspenders), guard the heavy imports the same way:
```python
import pytest
np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytestmark = pytest.mark.base
```
`importorskip` turns a missing dependency into a clean **skip of that module**, never a collection abort.
With Part 1 in place this is redundant in CI (the marker already prevents collection in the minimal
lane), but it makes the file robust to *ad-hoc* runs like the `pytest -m core -o addopts=` you executed
by hand in a partial environment — which is precisely how the failure surfaced. Apply the same guard to
the other new science tests I specced (`test_radial_profile.py`, `test_pooled_counting_pedestal.py`,
`test_tortuosity_consistency.py`, `test_fz_merge.py`) as they're written, since each imports pandas.

*(Choose Part 1 alone if you want minimal churn; Part 1 + Part 2 if you want the files to also survive
manual runs in odd environments. Part 1 is the correctness fix; Part 2 is hardening.)*

### Part 3 (REQUIRED — prevents recurrence) — a guard that fails when a heavy-import test is unmarked

The whole point of this suite is that a class of bug can't silently return. Add an AST guard so an
unmarked, heavy-importing test file is a **red build**, not a latent abort. Extend the existing
`tests/test_ci_dependencies.py` (which already AST-walks module-scope imports) with:

```python
import ast, pathlib, pytest

_BASE_STACK = {"pandas", "scipy", "skimage", "matplotlib", "cv2",
               "sklearn", "seaborn", "networkx", "openpyxl"}

def _module_scope_imports(tree):
    names = set()
    for node in tree.body:                       # module scope only
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names

def _markers(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("core", "base"):
            val = node.value
            if isinstance(val, ast.Attribute) and val.attr == "mark":
                found.add(node.attr)
    return found

def test_heavy_import_tests_are_marked_base():
    """A test file that hard-imports a base-stack package at module scope MUST be marked
    (core or base). Unmarked heavy-import files abort collection in the minimal lane's seam
    — the pandas failure of 2026-07-25. Marker classification is what conftest keys off."""
    offenders = []
    for f in pathlib.Path("tests").glob("test_*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        heavy = _module_scope_imports(tree) & _BASE_STACK
        if heavy and not _markers(tree):
            offenders.append(f"{f.name}: imports {sorted(heavy)} at module scope but has no core/base marker")
    assert not offenders, "Unmarked heavy-import test files:\n" + "\n".join(offenders)
```

This is the same philosophy as the file's existing guards (`test_no_undefined_names`,
`test_headless_science`, `test_ci_dependencies`): a pure-AST check, no imports, that turns "a test was
mis-classified" into an immediate, self-explaining CI failure instead of a confusing collection abort
three lanes later. Mark this guard itself `core` (it needs only `ast`/`pathlib`) so it runs in every
lane, including minimal.

---

## `core.yml` — no change needed

The workflow is correct. Confirm only that the KM file's dependency is already covered: the `core or
base` lane installs pandas (`pip install ... pandas`, line 62), so once `test_kaplan_meier.py` is marked
`base` it runs green there and in the wheel lane, and is ignored in the minimal lane. No dependency-list
edit is required — `test_ci_dependencies.py`'s derived list covers `src/` modules, and test files supply
their own deps via the lane that installs the full stack.

---

## Verification

1. **Reproduce the original failure is gone in the minimal lane:**
   ```
   pip install numpy pytest && pip install --no-deps -e .
   pytest -m core -o addopts= -v
   ```
   `test_kaplan_meier.py` (and every `base`-marked file) must be **ignored at collection**, not errored.
   Expect no `ModuleNotFoundError`.

2. **`base` tier still runs it (full stack):**
   ```
   pip install numpy scipy scikit-image pandas matplotlib opencv-python-headless \
               pywavelets simpleitk seaborn networkx scikit-learn openpyxl pytest && \
   pip install --no-deps -e .
   pytest -m "core or base" -v -k kaplan_meier
   ```
   Must collect and pass.

3. **The new guard catches a regression:** temporarily remove the `pytestmark` from any `base` file and
   confirm `test_heavy_import_tests_are_marked_base` fails with that file named; restore it.

---

## Appendix — the 19 unmarked, heavy-importing test files to classify

All import pandas/scipy/skimage (or another base package) at module scope with **no** `pytestmark`. Each
needs a `pytestmark = pytest.mark.base` (default) unless it provably runs on numpy alone (then `core`):

```
test_brushing.py              test_group_c_geometry.py        test_pixel_coloc.py
test_data_management.py       test_group_e_brightfield.py     test_plot_backends.py
test_data_qc.py               test_group_f_coloc.py           test_puncta_refinement.py
test_explore_refine_export_ui.py  test_kaplan_meier.py        test_seaborn_subset_brushing.py
test_feature_analysis.py      test_linkers.py                 test_sedimentation.py
test_file_io.py               test_loaders_agree_on_scale.py  test_segmentation_refine.py
                              test_vpt_viscosity_chain.py
```

`test_kaplan_meier.py` is the one that aborted the run; the rest are the same latent gap. Marking all 19
(Part 1) + adding the guard (Part 3) closes the class. `test_explore_refine_export_ui.py` likely also
touches the GUI stack — check whether it belongs to the `integration` tier instead of `base`; if it
imports napari/PyQt at module scope, mark it `integration` (or leave it to the GUI lane) rather than
`base`, so the headless lanes ignore it via the existing GUI-import scan.
