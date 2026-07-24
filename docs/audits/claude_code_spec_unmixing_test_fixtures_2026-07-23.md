# Claude Code spec — The unmixing test fixture is wrong, not `unmix`

> **✅ STATUS — DONE (test-only, git-only, no version bump; `unmixing_tools.py` untouched).**
>
> **Part 1 — the 5 failures are ALREADY resolved in the current tree (1.6.328).** All 12 unmixing tests
> (`test_unmixing.py` ×10 + `test_unmixing_ui.py` ×2) pass. The scenario the spec worked out — `a_true =
> [800, 0]` so observed ch1 = 0.08·800 = 64, unmixing to ~0 — is exactly
> `test_the_negative_fraction_is_the_honesty_check` (test_unmixing.py:78), and it already asserts the
> algebra-correct outcome (`negative_fraction == 0`, i.e. ch1 recovers to ~0), NOT that ch1 stays 64. So the
> wrong-expectation fixtures had been corrected between the spec's 1.6.324 target and now. No fixture change
> was needed; the arithmetic confirms the implementation (per the spec's caution, `unmix` was NOT touched).
>
> **Part 2 — round-trip property test ADDED (the class guard).** `test_unmixing.py::
> test_mix_then_unmix_round_trips_across_the_parameter_sweep` (`core`, parametrized ×5): constructs the true
> abundances A first, forms `measured = M·A (+ background)`, and asserts `unmix` recovers A within `atol=1e-9`
> (tolerance, never equality). Covers the spec's sweep — a zero-abundance channel (the failing case correctly
> expressed), asymmetric crosstalk (0.15 vs 0.08), a 3-channel matrix, and a scalar + per-channel background
> offset. Because the expected value IS the input, no hand-computed number can be wrong — the exact class of
> defect that produced the two historical failures cannot recur. The refusal / negative-fraction tests are
> unchanged and still pass.

**Date:** 2026-07-23 · **Target tree:** 1.6.324 · Verified by working the arithmetic from the CI output
and reading the implementation. **This is a test defect. Do not change `unmixing_tools.py`.**

```
= 5 failed, 514 passed, 117 skipped, 22 deselected =
```

---

## The arithmetic proves the implementation right

The failing case calls:
```python
unmix(channels=[ch0 = 800 (uniform), ch1 = 64 (uniform)],
      M = [[1.00, 0.15],
           [0.08, 1.00]])
```

`unmix` computes `a = M⁻¹ · c` (its documented contract, `unmixing_tools.py:108-117`):
```
det   = 1·1 − 0.15·0.08 = 0.988
M⁻¹   = (1/0.988) · [[ 1.00, −0.15],
                     [−0.08,  1.00]]

ch0_true = (800 − 0.15·64) / 0.988 = (800 − 9.6)/0.988 = 800.0
ch1_true = (−0.08·800 + 64) / 0.988 = (−64 + 64)/0.988 =   0.0
```

The observed output is **`800.0`** and **`−1.348e-15`** — i.e. exactly 800 and exactly 0, to
floating-point precision.

**The fixture was built so channel 1 is pure bleed-through.** `0.08 × 800 = 64`, precisely the measured
ch1 value. So the true ch1 abundance *is* zero, and the unmix recovered it correctly. `−1.35e-15` is
ordinary floating-point residue from the division, not a defect.

The test evidently expects ch1 to remain ≈64 (or to be positive). That expectation contradicts the
physics the fixture encodes.

---

## Part 1 — Fix the fixture, not the implementation

Two options; pick per test based on what it is trying to demonstrate:

**(a) The test wants a channel with real signal *plus* bleed-through.** Then the fixture is
under-specified: make ch1's measured value **greater** than the pure crosstalk term. With ch0 = 800 and
0.08 crosstalk, any ch1 > 64 leaves a genuine abundance. E.g. ch1 = 100 → `ch1_true = (−64 + 100)/0.988
≈ 36.4`. Assert that recovered value.

**(b) The test wants to demonstrate crosstalk removal.** Then the current fixture is *ideal* — and the
correct assertion is that ch1 recovers to **≈ 0**, not to 64. That is arguably the better test: it shows
unmixing removes exactly the bleed-through and nothing else.

Either way, **assert with a tolerance** (`np.allclose`, `atol≈1e-9`), not equality — a result of
`−1.35e-15` should pass an "is zero" check, and an exact-equality assertion on floating-point output is
its own bug.

The other 4 failures are very likely the same fixture family; work the arithmetic for each before
changing anything.

---

## Part 2 — This is the second unmixing test defect. Guard the class.

An earlier CI run failed `test_the_negative_fraction_is_the_honesty_check` on this same module with a
wrong expected value. That makes **two test-side defects in `unmixing_tools`**, both of the same shape:
an expectation that does not follow from the linear algebra the fixture sets up.

The module's contract is small and exactly checkable, so add a **round-trip property test** that cannot
encode a wrong expectation:

```
for a range of true abundances A and well-conditioned matrices M:
    measured = M @ A
    recovered = unmix(measured, M)
    assert allclose(recovered, A)
```

Construct `A` first, derive `measured` from it, and assert recovery. Because the expected value *is* the
input, there is no hand-computed number to get wrong. Cover:
- a channel with zero true abundance (the current failing case, correctly expressed),
- asymmetric crosstalk (0.15 vs 0.08, as here),
- 3-channel matrices,
- a background offset, since `unmix` subtracts it before inversion.

Keep the existing hand-written tests for the *refusal* behaviours (singular matrix, wrong shape,
negative-fraction reporting) — those assert policy, not arithmetic, and are not prone to this error.

---

## Tests
- The corrected fixture(s) pass, with tolerance-based assertions.
- The round-trip property test passes across the parameter sweep above.
- `unmixing_tools.py` is **unchanged** (assert via review, not code — no implementation edit in this
  commit).
- The refusal tests (singular/ill-conditioned matrix, shape mismatch) still pass unmodified.
- The negative-fraction honesty check still passes.

## Steps
1. Work the arithmetic for each of the 5 failures; classify each as (a) under-specified fixture or
   (b) wrong expectation.
2. Correct the fixtures/assertions accordingly, with tolerance-based comparisons.
3. Add the round-trip property test.
4. Full `pytest -m core` green.
5. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (unmixing test fixtures corrected;
   round-trip property test added — no change to the unmixing implementation).

## Definition of done
- All 5 failures pass with corrected expectations derived from the mixing algebra.
- A round-trip property test covers recovery so a future fixture cannot encode a wrong expected value.
- `unmixing_tools.py` is untouched.

## Cautions
- **Do not "fix" `unmix`.** It computes `a = M⁻¹·c` correctly; the CI output proves it. Changing the
  implementation to satisfy a wrong test would be the worst possible outcome here.
- **Derive expectations from the algebra**, not from intuition about what "should" happen — a channel
  that is pure crosstalk correctly unmixes to zero.
- **Use tolerances.** `−1.35e-15` is zero; an exact-equality assertion on float output will keep failing
  for the wrong reason.
- Keep `negative_fraction` semantics intact — negatives are the documented honesty signal and must not
  be clipped in the computation to make a test pass.
- If any of the 5 turns out **not** to be explained by the algebra, stop and report it — that one would
  be a real defect and deserves separate treatment.
