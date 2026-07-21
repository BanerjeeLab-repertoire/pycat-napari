# Claude Code spec — Split long SCIENCE functions, coverage-gated

**Date:** 2026-07-19 · **Target tree:** 1.6.156 · Verified against the 1.6.156 tree. Companion to the
UI-builder splitting spec. This half is harder and must be governed by a different rule: **a numerical
function may only be split if a test can prove the numbers did not change.**

## Why this needs its own spec
A fresh AST classification of the 138 functions over 120 lines:
- **~42 are UI-ish** (`_add_*`, `_on_*`, `_build*`, or living in a `*_ui.py`) — the other spec's target;
- **~96 are science-ish** — numerical and analysis code.

So UI splitting alone cannot bring the count down meaningfully. But science functions carry a risk UI
builders do not: **a refactor can silently change a number.** A widget that fails to appear is
obvious; a partition coefficient that shifts 3% because an intermediate was recomputed in a different
order is not.

The ratchet file makes the same argument about `ui_modules` (*"a refactor whose only verification is
'it still imports' is a refactor that ships bugs"*), and it applies with more force here.

## The governing rule: coverage gates the split
For each candidate, check whether a test asserts its **numerical output**. Verified examples:

| function | lines | tests referencing |
|---|---:|---:|
| `fit_anomalous_diffusion` (condensate_physics_tools) | 394 | **4** |
| `partition_coefficient_local` (invitro_tools) | 394 | **4** |
| `run_timeseries_condensate_analysis` (timeseries_condensate_tools) | 362 | **0** |

- **Covered (≥1 test asserting its numbers):** split it. The tests are the proof.
- **Uncovered:** **write a characterization test first** — capture today's output on a synthetic input
  as the reference — *then* split. The test must be written and passing **before** any code moves.
- **Cannot be characterized** (needs a GUI, a real file, or has no deterministic output): **do not
  split.** Record it as deferred with the reason. An unverifiable numerical refactor is not worth the
  risk, and saying so is a legitimate outcome.

## Scope — six functions, not ninety-six
Attempting all 96 would be a rewrite. Take **six**, chosen for coverage and impact:

**Tier 1 — already covered, split now:**
1. `fit_anomalous_diffusion` (394) — the MSD/α fit behind viscosity; 4 tests assert its recovery.
2. `partition_coefficient_local` (394) — 4 tests, and it is manuscript-facing.

**Tier 2 — characterize first, then split:**
3. `run_timeseries_condensate_analysis` (362) — 0 tests; the characterization test is itself valuable
   independent of the split.
4–6. Pick the next three longest science functions with deterministic, array-or-scalar output.

## How to split numerical code safely
- **Extract by phase, not by line count.** A fit function typically has: validate inputs → prepare
  data → fit → assess quality → package results. Those are natural, nameable seams. Splitting at "line
  200" because it is halfway is how you sever a computation.
- **Extract pure helpers** — take arguments, return values, no mutation of enclosing state. If a
  candidate block reads five locals and writes three, it is not a clean seam; find a better one.
- **Never change the order of floating-point operations.** Reassociating a sum or hoisting a division
  changes results in the last bits — and a test with a tight tolerance will catch it, which is the
  point, but it means such changes are out of scope here.
- **Do not "improve" anything while splitting.** No vectorising, no removing a redundant-looking
  computation, no tightening a tolerance. If something looks wrong, note it and file it separately.

## The characterization test pattern (Tier 2)
```python
def test_run_timeseries_condensate_analysis_is_unchanged():
    """Characterization: pins TODAY's output so the split can be proven behaviour-preserving.

    Not a correctness test — it asserts the current values, whatever they are.
    If this fails after a refactor, the refactor changed the numbers.
    """
    result = run_timeseries_condensate_analysis(_seeded_synthetic_stack(), **_fixed_params)
    assert result['n_condensates'] == 17
    npt.assert_allclose(result['mean_area_um2'], 3.412, rtol=1e-9)
```
Mark these clearly as characterization tests. They pin behaviour, not correctness — if a value is
later found to be *wrong*, the test is updated deliberately with the reason recorded.

Use `rtol=1e-9`, not a loose tolerance: the goal is to detect any change at all, and a loose tolerance
would let a real drift through.

## Steps
1. Classify the 96 science functions by coverage (a scratch inventory; commit only the six chosen).
2. Split `fit_anomalous_diffusion` by phase; existing tests must pass **unmodified**.
3. Split `partition_coefficient_local`; same.
4. Write the characterization test for `run_timeseries_condensate_analysis`; confirm it passes; then
   split; confirm it still passes.
5. Repeat 4 for the next three chosen functions.
6. Re-count; **lower `_MAX_LONG_FUNCTIONS`** with a dated comment in the established style.
7. Full `pytest -m core` green after each split.
8. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG reporting the count change
   and listing any function **deferred as unverifiable**, with its reason.

## Definition of done
- Six long science functions split by phase into pure helpers, each under 120 lines.
- Every split is proven behaviour-preserving by a test that existed *before* the split.
- Functions that cannot be characterized are recorded as deferred with reasons — not split blind.
- `_MAX_LONG_FUNCTIONS` lowered to the genuine value.
- No numerical output changes anywhere.

## Cautions
- **No test, no split.** This is the whole discipline. If a characterization test cannot be written,
  the function stays long — and that is the correct outcome, recorded honestly.
- **Write the test before the refactor.** Afterward it encodes whatever the refactor produced.
- Tight tolerances (`rtol=1e-9`). A loose one defeats the purpose.
- Do not reassociate floating-point operations or "improve" logic while splitting. Note issues; file
  them separately.
- Split by computational phase, never by line count.
- One function per commit.
