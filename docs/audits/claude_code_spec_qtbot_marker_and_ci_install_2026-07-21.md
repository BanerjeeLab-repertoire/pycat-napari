# Claude Code spec — Fix the `qtbot` collection errors: marker discipline + CI install route

> **STATUS: DONE (2026-07-23, tree 1.6.291) — except Part B's second CI lane (deferred).**
> Part A (re-mark Qt tests `integration`, per-test): done, and the audit found **more** than the four named
> — `test_brushable_workspace.py` (3 `qtbot`) and `test_plot_backend_pyqtgraph.py` (4 pytest-qt `qapp`) were
> also mis-marked; all re-marked. `test_brushable_table.py` was correctly LEFT `core` (it supplies its own
> `qapp` via `importorskip`, the safe headless pattern). Part D (guard): done —
> `tests/test_ci_dependencies.py` gained `test_no_core_marked_test_requests_a_qt_fixture` and
> `test_no_core_test_file_imports_the_gui_stack_at_module_scope`. Part C: the `qt_api` warning is documented
> and accepted in the (now non-erroring) minimal lane. **Part B (deferred):** the core lane is now correct as
> a minimal headless lane, but a dedicated `integration` CI lane (`.[test]` + `-m integration`, offscreen Qt)
> is NOT added — it needs xvfb/heavy installs that can't be validated green from a dev box. Tracked in
> `TODO_gui_verification_and_followons.md` §E. `pytest -m core` green (1742), test/CI-only (no version bump).

**Date:** 2026-07-21 · **Target tree:** 1.6.281+ · Verified against the tree and the CI log. Four
collection **errors** in the `core` lane, one root cause with two contributing defects. Small, precise,
and it closes a finding that has now escalated from a cosmetic warning to a hard failure.

## The failure
```
tests/test_batch_brushing.py::test_mount_batch_workspace_pulls_up_the_image_on_selection  ERROR
tests/test_cellular_brushable.py::test_session_load_remounts_the_cellular_panel           ERROR
tests/test_cellular_brushable.py::test_a_session_without_a_cell_table_does_not_remount    ERROR
tests/test_cellular_brushable.py::test_mount_cellular_workspace_wires_both_tiers          ERROR

E   fixture 'qtbot' not found
```
Corroborated in the same run by `PytestConfigWarning: Unknown config option: qt_api` and
`plugins: cov-7.1.0` — **pytest-qt is absent from the CI environment**, so the fixture it provides does
not exist and `qt_api = "pyqt5"` in pyproject is ignored.

**This is the earlier engineering audit's item #9** (Part 4 of the release-engineering spec). It was
cosmetic when no `core` test needed Qt; adding Qt-dependent tests to the `core` lane turned it into an
error.

## Two defects, both real

### Defect 1 — the tests are mis-marked
`pyproject.toml` declares the marker contract explicitly:
```toml
"core: pure scientific kernels - no napari, no Qt, no GPU. Must pass headlessly."
"integration: requires napari/Qt/file-IO/GPU."
```
The four failing tests **require Qt** and are marked `core`. That contradicts the contract in the
project's own configuration. They belong in `integration` (or a dedicated `gui` marker).

**This is the important half.** Installing pytest-qt alone would make the errors disappear while
leaving `core` silently Qt-dependent — which defeats the purpose of having a headless lane at all, and
would break again the next time `core` runs somewhere minimal.

### Defect 2 — CI's install route bypasses the declared extras
`pytest-qt` **is** declared (pyproject:184, in a test extra). But `.github/workflows/core.yml` installs
packages individually (`pip install numpy scipy scikit-image ...`) and **never installs the extra**, so
the declaration has no effect on CI. That divergence means the declared test environment and the actual
test environment can drift apart indefinitely without anyone noticing.

## The fix

### Part A — re-mark the Qt-dependent tests (do this first; it fixes the red build)
- Move the four tests off `core`. Mark them `integration` (Qt/napari-requiring), matching the declared
  contract.
- **Audit the rest of the new brushing-workspace tests in the same files.** Some tests in
  `test_cellular_brushable.py` clearly *are* headless — e.g.
  `test_puncta_analysis_paints_a_globally_unique_perpunctum_layer` passes today — so re-mark
  **per test**, not per file. A file may legitimately contain both.
- Verify by running `pytest -m core` in an environment **without** pytest-qt: it must collect and pass
  with zero errors. That is the acceptance test for this part.

### Part B — make CI install what the project declares
Replace the hand-rolled package list with the declared extra so the two cannot drift:
```bash
pip install -e ".[test]"
```
- If the individual installs exist to keep the `core` lane deliberately minimal, then **make that
  explicit**: a minimal lane that installs only base deps and runs `-m core`, plus a **separate**
  integration lane that installs `.[test]` (including pytest-qt) and runs `-m integration`. Two lanes,
  two honest contracts.
- Either way, remove the situation where a declared test dependency is silently absent.

### Part C — the `qt_api` warning
With pytest-qt installed in the lane that needs it, `qt_api = "pyqt5"` resolves and the
`PytestConfigWarning` disappears. In a genuinely minimal lane the option is still unknown, so either
scope it to the Qt lane's config or accept the warning there deliberately — **but do not leave it
warning in a lane that also errors**, which is the current state.

## Guard against recurrence
Add a small test asserting the marker contract holds: **no `core`-marked test may request a
Qt-dependent fixture** (`qtbot`, or import PyQt5/napari at module scope). An AST/collection check is
enough. This is the same ratchet discipline used elsewhere (`test_ci_dependencies` already asserts CI
installs every module-scope dependency — extend that idea to fixtures).

Without this guard the failure recurs the next time someone adds a Qt test and reaches for `core` by
habit.

## Tests
- `pytest -m core` collects and passes with **zero errors** in an environment without pytest-qt.
- `pytest -m integration` runs the four Qt tests successfully **with** pytest-qt installed.
- The new guard fails if a `core`-marked test requests `qtbot`.
- `test_ci_dependencies` still passes (CI installs what the code imports).
- No test's behaviour changes — this is marking and environment, not logic.

## Steps
1. Re-mark the four Qt tests as `integration`; audit their files per-test for others.
2. Verify `-m core` is clean without pytest-qt.
3. Fix the CI install route (`.[test]`, or two explicit lanes).
4. Add the core-marker/Qt-fixture guard test.
5. Full `pytest -m core` green; integration lane green.
6. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (Qt-dependent tests moved to
   integration; CI installs the declared test extra; guard added).

## Definition of done
- The four `qtbot` errors are gone from the `core` lane.
- `core` is genuinely headless — provably so, in an environment lacking pytest-qt.
- CI installs the declared test extra (or two lanes each install exactly what their marker promises).
- A guard prevents a Qt-dependent test from being marked `core` again.
- No product behaviour changes.

## Cautions
- **Do not fix this by only installing pytest-qt into the core lane.** That hides the mis-marking and
  leaves `core` Qt-dependent, contradicting its declared contract and breaking the next minimal run.
- **Re-mark per test, not per file** — these files contain genuinely headless tests too
  (`test_puncta_analysis_paints_a_globally_unique_perpunctum_layer` passes today).
- Don't weaken the marker definitions to make the current state legal; the definitions are right and the
  markings are wrong.
- This is marking + environment only: no test logic, no product code.
- Note for context: the previously-failing
  `test_unmixing::test_the_negative_fraction_is_the_honesty_check` now **passes** — it is unrelated to
  these errors and needs no action.
