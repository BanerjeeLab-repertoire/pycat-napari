# Claude Code spec — `openpyxl` CI failure: honour the declared fallback, and guard lazy imports

> **✅ STATUS — DONE, shipped 1.6.313.**
> **Part 1.** The class was resolved by DECOUPLING the minimal path from openpyxl, not installing it into the
> minimal lane: `scientific_tree.ScientificTree` now loads the curated question tree LAZILY (on first access),
> so constructing a `NavigatorSession` to edit/compile no longer needs openpyxl. The one test that DRIVES the
> tree is re-marked `integration` (it genuinely needs the curated workbook — there is no seed question tree to
> fall back to); the rest stay `base`. A new `base` test proves the navigator constructs + builds the seed
> registry + edits/compiles with openpyxl SIMULATED ABSENT. openpyxl stays DECLARED (the full navigator loads
> the curated tree for users; the 1.6.308 decision stands) — it is simply no longer required by the `core`/`base`
> TIER, which is what the DoD asks.
> **Part 2.** `tests/test_ci_dependencies.py::test_no_undeclared_unguarded_lazy_import` extends the guard to
> function-scope imports (declared / try-except-guarded / known-lazy), reusing the existing classifier.
> **Honest boundary (per the spec's own caution):** a naive src-wide version is noisy — it surfaces packages
> imported directly but present only TRANSITIVELY (`tifffile`, `pillow`, `imageio`, `packaging`) and genuinely
> optional backends (`aicsimageio`, `scyjava`, `cupyx`, `plotly`, `pyqtgraph`). These are allow-listed with
> documented categories rather than silently patched; explicitly DECLARING the transitive-core ones is a
> separate dependency-hygiene audit, flagged here rather than force-fixed.

**Date:** 2026-07-23 · **Target tree:** 1.6.297+ · Verified against the 1.6.297 tree and the CI log.
Two `core`-lane failures, one root cause, and a real gap in an existing guard.

```
FAILED tests/navigator/test_navigator_session.py::test_answering_the_questions_reaches_a_leaf_and_compiles_a_runnable_plan
FAILED tests/navigator/test_navigator_session.py::test_editing_is_a_pin_and_recompile_that_revalidates
    ModuleNotFoundError: No module named 'openpyxl'
= 2 failed, 1760 passed, 69 skipped, 126 deselected =
```

**No product defect.** 1,760 tests pass; these two fail on a missing optional dependency.

---

## The cause (verified)

`navigator/loader.py` reads the curated capability workbooks via openpyxl — imported **inside the
function**, at line 41:
```python
def _rows(workbook: str, sheet: str) -> List[list]:
    import openpyxl
```
and the module docstring states the contract explicitly:

> **Requires `openpyxl`.** If the workbooks or openpyxl are missing, callers should fall back to
> `modules.build_registry()` (the standalone seed).

So **openpyxl is optional by design, with a documented fallback.** It is not declared in `pyproject.toml`
at all (grep → 0 hits), which is consistent with that intent.

The two failing tests are `core`-marked and exercise the navigator through a path that reaches
`loader._rows` without the declared fallback engaging. `core` is supposed to be the minimal-dependency,
headless tier — so a `core` test must not require an undeclared optional package.

**Therefore the tests are wrong, not the dependency.** The fix is not to install openpyxl into the
minimal lane; that would quietly make an optional dependency mandatory for the tier that is meant to
prove PyCAT runs without extras.

---

## Part 1 — Fix the two tests

Choose per test, based on what each is actually asserting:

- **If the test is about the navigator's question→plan logic** (which both names suggest —
  "answering the questions reaches a leaf and compiles a runnable plan", "editing is a pin and recompile
  that revalidates"), it should run against the **standalone seed** (`modules.build_registry()`), which
  is the documented fallback and needs no workbook. Then it is genuinely `core`, and it additionally
  proves the fallback path works — which nothing currently tests.
- **If the test genuinely needs the curated workbook overlay**, it is not a `core` test. Re-mark it
  `integration` (or an `optional` tier) and ensure that lane installs openpyxl.

**Preferred: the first.** It keeps `core` honest, and it converts the failure into coverage of a
documented-but-untested fallback.

Whichever is chosen, **exercise the fallback somewhere**: a test asserting that with openpyxl absent the
loader falls back to `build_registry()` rather than raising. The docstring promises this behaviour and
nothing verifies it — which is why the gap surfaced as a CI failure instead of a caught contract.

---

## Part 2 — Extend the dependency guard to lazy imports

This is the third failure of the same class (after the `qtbot` fixture and the marker/environment
mismatch), and the existing guard cannot catch it **by construction**.

`tests/test_ci_dependencies.py::_module_scope_dependencies` walks only module-scope imports —
`# module scope ONLY — not inside functions` (line 118). A package imported inside a function is
invisible to it, even though a test that calls that function still needs the package installed.

### The extension
Add a companion check: **for every module a guarded test exercises, collect function-scope imports too**,
and assert each is either

1. installed in the lane that runs those tests, **or**
2. declared as optional **and** guarded by a fallback (an `ImportError` handler, or a documented
   fallback path the tests exercise).

Case 2 is what `loader.py` intends — so the guard should *pass* it once the fallback is honoured, and
*fail* the current state where a `core` test reaches a lazy optional import with no fallback in play.

Keep it mechanical and narrow, matching the existing guard's discipline:
- Walk `ast.Import`/`ast.ImportFrom` nodes **inside** function bodies, in the same guarded module set.
- Distinguish third-party from stdlib and intra-package imports (the existing helper already does this
  for module scope — reuse it rather than writing a second classifier).
- A lazy import that *is* declared in a base dependency needs no fallback; only undeclared/optional ones
  do.

### Why this is the right level
The lazy-import pattern is deliberate and good — it is exactly what keeps `pywt`, BioFormats, and
cellpose from being mandatory. The guard should not discourage it. It should ensure that **a lazy import
is either satisfied or has a working fallback**, which is the contract the code already claims.

---

## Tests
- The two navigator-session tests pass in a lane **without** openpyxl (fallback path), or are re-marked
  and pass in a lane with it.
- A new test asserts the loader falls back to `build_registry()` when openpyxl is unavailable, rather
  than raising (the documented-but-untested contract).
- The extended guard flags a `core`-exercised module with an undeclared lazy import and no fallback.
- The extended guard **passes** `loader.py` once its fallback is honoured (a correct optional import is
  not a violation).
- Existing `test_ci_installs_every_module_scope_dependency` still passes unmodified.
- `-m core` runs clean in a minimal environment.

## Steps
1. Decide per test: fallback-to-seed (preferred) or re-mark to a lane with openpyxl.
2. Add the missing fallback test for `loader._rows` / `build_registry()`.
3. Extend `test_ci_dependencies` with the function-scope import check and the declared-or-fallback rule.
4. Full `pytest -m core` green in a minimal environment.
5. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (navigator session tests no longer
   require an optional dependency in the minimal lane; the CI-dependency guard now covers lazy imports).

## Definition of done
- The two failures are gone, without making openpyxl mandatory for `core`.
- The loader's documented openpyxl fallback is exercised by a test.
- The dependency guard covers function-scope imports and enforces declared-or-fallback.
- `-m core` is clean in a minimal environment; existing guards pass unmodified.

## Cautions
- **Do not "fix" this by installing openpyxl into the minimal lane.** That makes an optional dependency
  mandatory for the tier whose entire purpose is proving PyCAT runs without extras — and hides the real
  defect, which is a `core` test reaching an optional path.
- **Do not discourage lazy imports.** They are how the optional-dependency story works here; the guard
  enforces *declared-or-fallback*, not *no lazy imports*.
- Keep the new check mechanical and reuse the existing stdlib/third-party classifier — a second,
  divergent classifier is its own maintenance trap.
- This is the third failure of this class. If the extended guard cannot express the rule cleanly, say so
  rather than adding a narrow patch that leaves the class open.
