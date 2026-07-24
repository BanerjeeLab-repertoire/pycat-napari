# Claude Code spec — CI lanes must install what their tests require (the recurring failure class)

> **STATUS — 2026-07-23 · addressed (test/CI-only, git-only, no version bump).**
>
> **Part 1 (fix the instance) — done.** The 504-test lane is the minimal `core` lane (`pytest -m core`,
> numpy + pytest only). Three `test_channel_identity` persistence tests were marked `core` but reach
> `skimage` through the `isolated_store` **fixture** (`channel_designations` → `general_utils`, whose
> module-scope `import skimage as sk` at `general_utils.py:20` is unguarded). Re-marked those three
> `base` (`test_identity_answers_round_trip_and_are_reversible`,
> `test_identity_and_a_condensate_designation_coexist_either_order`,
> `test_recall_is_guarded_to_the_acquisition_layout`).
> `test_channel_needs_identity_only_for_the_position_guess` stays `core` (channel_naming only, skimage-free).
> No skip-if-missing was added (per the caution).
>
> **Part 2 (the guard that closes the class) — done, honestly scoped.** No single static rule expresses
> the whole class cleanly, so this is stated out loud as **three complementary guards** (header comment in
> `test_ci_dependencies.py`): (1) `test_no_core_marked_test_requests_a_qt_fixture` — the qtbot case;
> (3) `test_no_undeclared_unguarded_lazy_import` — the openpyxl function-scope case; (4) the new
> `test_a_core_test_needs_only_the_minimal_lane_install` — the skimage collection-time case. Guard (4)
> **reads the workflow** for the minimal lane's install list (no hard-coded package list — can't drift),
> walks each `core`-selected test's **module-scope + requested-fixture** imports (both run before the body,
> so `importorskip` cannot guard them — this is exactly how skimage entered via a fixture), follows
> `pycat.*` into src transitively, and asserts the result ⊆ the lane's install. A body import behind an
> `importorskip("<absent>")` is **excused** (the test skips before it runs — this is what makes the three
> `test_qt_worker.py::test_materialize_off_thread_*` core tests legitimate). It mirrors the conftest's
> collection-skip so files that never reach the lane aren't flagged. Proven to have teeth: it flagged both
> the fixture case (channel_identity) and the body case (qt_worker) before each was correctly reconciled.
>
> **Part 3 (the unmixing failure) — not reproducible in this tree; no change (per the caution).** All 10
> `test_unmixing.py` tests pass in the full env. The tests are deterministic (`np.random.default_rng(0)`),
> and none asserts on the spec's described uniform-`64.` array, so the failing lane ran an older tree. Per
> "do not change `unmixing_tools` before seeing the assertion," nothing was changed — the current
> `_M_TRUE = [[1.0, 0.15], [0.08, 1.0]]` round-trip and negative-fraction assertions are green.
>
> **Honest gap:** the class is covered by three guards, not one; a genuinely novel entry path (e.g. a
> dynamic/`__import__` import the static walker can't resolve) would still not be flagged — reported here
> rather than claimed closed.



**Date:** 2026-07-23 · **Target tree:** post-1.6.297 · Verified against the tree and the CI log. This is
the **fourth** failure of the same class in recent runs. Fixing the instance again is not the point;
this spec closes the class.

```
ERROR tests/test_channel_identity.py::test_identity_answers_round_trip_and_are_reversible
ERROR tests/test_channel_identity.py::test_identity_and_a_condensate_designation_coexist_either_order
ERROR tests/test_channel_identity.py::test_recall_is_guarded_to_the_acquisition_layout
    ModuleNotFoundError: No module named 'skimage'
= 1 failed, 504 passed, 117 skipped, 15 deselected, 3 errors =
```

---

## The class, and why it keeps recurring

| # | failure | missing thing | why the guard missed it |
|---|---|---|---|
| 1 | `qtbot` fixture not found | pytest-qt | Qt tests marked `core`; lane had no pytest-qt |
| 2 | `test_unmixing` assumption | — | test-side defect |
| 3 | `openpyxl` not found | openpyxl (optional, lazy import) | guard walks **module-scope** imports only |
| 4 | **`skimage` not found** | scikit-image (**declared base dep**) | guard checks declarations, not **per-lane installs** |

Number 4 is the most telling. **`scikit-image` IS declared** (`pyproject.toml:84`) **and the `core`
workflow does install it** (`core.yml:62`). Yet a lane ran these tests without it — the 504-test count
(versus 1,760 in the main run) shows this is a *different, smaller* lane whose install step does not
match what its selected tests require.

So the existing guard asks *"is this dependency declared and installed by the core workflow?"* — and the
real question is **"does every lane install what the tests it selects actually need?"** Those are
different questions, and only the second catches all four failures.

---

## Part 1 — Fix the instance

Identify the lane that produced the 504-test run (a workflow other than `core.yml`, or a second job
within it) and either:
- **install the base scientific deps** in that lane, if it is meant to run tests that need them, or
- **narrow its test selection** to what it does install, if it is deliberately minimal.

`scikit-image` is a declared base dependency, so a lane running `test_channel_identity` must install it.
Do **not** make these tests skip on missing skimage — that would hide a lane whose environment does not
match its selection.

---

## Part 2 — The guard that closes the class

Add a **lane/selection agreement** check. For each CI lane, given (a) its install commands and (b) its
pytest marker selection, assert that **every package required by the selected tests is installed by that
lane** — counting both module-scope and function-scope imports of the modules those tests exercise.

Concretely, extend `tests/test_ci_dependencies.py`:
1. **Parse each workflow lane**: its `pip install` lines and its `pytest -m <marker>` selection. (The
   existing guard already parses `core.yml`'s installs — generalise it to every workflow/job rather than
   hard-coding one.)
2. **Resolve the selected test set** for that marker, and the `src/pycat` modules those tests import.
3. **Collect required third-party packages** from those modules — **module scope and function scope**
   (the openpyxl gap), classifying stdlib/intra-package with the existing helper.
4. **Assert coverage**: every required package is installed by that lane, **or** is optional-with-a-
   working-fallback (the `loader.py` openpyxl case), **or** the tests requiring it are excluded from
   that lane's selection.

A lane that selects a test needing a package it does not install **fails the guard** — which is exactly
what all four failures were.

### Keep it honest, not clever
- The check reads the **workflow files**, so it stays true when a lane's install list changes. Hard-coded
  package lists in the test would drift and recreate the problem.
- Where static resolution cannot determine what a test imports (dynamic imports, plugins), the guard
  should **report that it could not check** rather than passing silently. An unassessable case is not a
  passing case — the same discipline the reliability index uses.
- Conservative on ambiguity: flag only clear cases, so nobody is tempted to disable it.

---

## Part 3 — The unmixing failure (needs one more line)

The same run has `1 failed` in the unmixing tests, showing a uniform `64.` array and a mixing matrix
`[[1.0, 0.15], [0.08, 1.0]]`. **The assertion line was not captured**, so the cause is undetermined:
recovered channels collapsing to a constant, an unmix returning its input unchanged, and a
negative-fraction assertion all fit the visible fragment.

**Do not guess at this one.** Capture the `FAILED` line and its assertion, then determine whether it is:
- a **real numerical defect** in `unmixing_tools` (matrix inversion or the recovery path), or
- a **test-side defect** like failure #2 above (a wrong expected value).

Given #2 was a wrong test assumption on this same module, check the test's expectation against the
mixing physics before changing the implementation: with `M = [[1, 0.15], [0.08, 1]]`, unmixing a uniform
field should return uniform channels — a constant result may be **correct**, and the assertion wrong.

---

## Tests
- The lane that failed installs scikit-image (or no longer selects tests that need it); the three
  `test_channel_identity` errors are gone.
- The new lane/selection guard **fails** a lane that selects a test requiring an uninstalled package
  (verify with a deliberate omission on a scratch branch).
- The guard **passes** an optional-with-fallback case (`loader.py` + openpyxl) — a correct optional
  import is not a violation.
- The guard reports, rather than silently passes, a case it cannot statically resolve.
- The existing `test_ci_installs_every_module_scope_dependency` still passes.
- All four historical failures would be caught by the guard (write them as fixtures if practical).

## Steps
1. Find and fix the lane producing the 504-test run (install base deps, or narrow its selection).
2. Generalise the dependency guard from one hard-coded workflow to **every lane**, covering
   module-scope *and* function-scope imports, with the declared-or-fallback-or-excluded rule.
3. Capture the unmixing `FAILED` assertion; fix the test or the implementation once the cause is known.
4. Full `pytest -m core` green in every lane.
5. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (CI lanes now provably install
   what their selected tests require).

## Definition of done
- The skimage errors are gone, with the lane's environment matching its selection.
- A single guard covers all four historical failure modes: Qt fixtures, optional lazy imports, declared
  base deps, and per-lane installs.
- The guard reads the workflow files rather than a hard-coded list, and reports unassessable cases.
- The unmixing failure is diagnosed and fixed at the correct layer.
- Every lane green.

## Cautions
- **Do not fix this by adding skip-if-missing to the tests.** That hides a lane whose environment does
  not match its selection — the actual defect — and would have hidden three of the four failures.
- **The guard must read the workflows**, not a copied package list, or it drifts and the class reopens.
- **Unassessable ≠ passing.** A test the guard cannot statically resolve must be reported.
- **Do not change `unmixing_tools` before seeing the assertion.** The previous unmixing failure was a
  wrong test expectation; assume nothing about which side is wrong here.
- This is the fourth instance. If the generalised guard cannot express the rule cleanly, say so rather
  than shipping a narrow patch that leaves the class open — a guard that half-covers it is worse than an
  honest gap.
