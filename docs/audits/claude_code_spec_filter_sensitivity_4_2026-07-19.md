# Claude Code spec — Filter sensitivity increment 4: the puncta refinement gate cluster

**Date:** 2026-07-19 · **Target tree:** 1.6.144 · Verified against the 1.6.144 tree. Continues the
filter-defaults sensitivity programme (increments 1–3 shipped; the harness records its own next step:
*"~35–110 other defaults remain… they are not equal, and the next increment is prioritisation"*).
Test-first: a divergence found here is a scientific finding, not a tolerance to tune.

## Why this cluster
Verified in `segmentation_tools.py`: the puncta refinement gate carries a **cluster of thresholds
applied together at three call sites** (lines ~1441, ~1666, ~1825):
```python
kurtosis_threshold=-3.0, local_snr_threshold=1.0, global_snr_threshold=1.0, intensity_hwhm_scale=…
```
`snr_threshold` appears 24 times across the module. This is the highest-value untested group for three
reasons:

1. **It decides which puncta exist.** Every downstream count, density, partition coefficient and
   colocalization statistic inherits this gate's decisions. A biased gate biases everything after it.
2. **It is a multi-threshold gate**, so the failure mode is subtler than a single cutoff: thresholds
   can *interact*, and a dataset can pass each individually while the combination excludes a real
   population.
3. **This exact family already produced a proven inverter.** The roadmap records a puncta SNR bug
   (an un-subtracted ratio, since fixed to contrast-to-noise with a robust MAD background). The class
   has bitten once; the harness exists precisely so the next one does not.

## What to test — the three failure signatures, applied to this cluster
The harness (`tests/filter_sensitivity.py`) already provides `sweep_invariance` plus
`assert_no_selection_bias`, `assert_offset_invariant`, and `assert_scale_invariant`. Apply all three:

1. **Offset invariance (`assert_offset_invariant`).** Add a camera pedestal (0/100/500/2000 counts) to
   a synthetic field with a known number of real puncta. The set of puncta that survive refinement
   must be **identical** at every pedestal. This is the signature that already bit once here.
2. **Scale invariance (`assert_scale_invariant`).** Multiply intensities by a constant gain. Surviving
   puncta must be unchanged — a gate keyed to absolute intensity rather than contrast fails this.
3. **Selection bias (`assert_no_selection_bias`).** Build a population with a known mean brightness
   spanning dim and bright puncta. Sweep `local_snr_threshold` / `global_snr_threshold` across a
   plausible range and assert the **mean brightness of survivors does not drift** with the threshold.
   If it does, the gate selects for brightness and every downstream intensity statistic is biased —
   the same mechanism as the `r2_min` case (mean 77 vs true 44).
4. **Interaction (new to this increment).** Sweep `kurtosis_threshold` and `local_snr_threshold`
   *jointly* on a small grid. Assert no combination in the plausible region drops a real population
   that either threshold alone retains. Report the grid as a small table in the failure message — a
   two-parameter cliff is invisible to one-at-a-time sweeps.

## Add validated cases to the registry
Each finding appends one row to `VALIDATED_CASES` (the registry is designed so *"adding the next
dangerous default should be one row"*). For each of `local_snr_threshold`, `global_snr_threshold`,
`kurtosis_threshold`: the function, the parameter, the check type, and the known-answer fixture.

## If a gate fails
**Report it; do not fix it inside this spec.** The harness's whole value is separating detection from
correction. A failing gate becomes:
- a documented finding in the CHANGELOG and roadmap, naming the parameter and the signature it failed;
- its own fix spec, with the failing test already written as the acceptance criterion.

If a gate passes all four, that is equally valuable — record it as *validated*, so the next audit does
not re-litigate it. The harness already does this for `bleach_r2_min` (excluded with a written reason
and a test pinning the reason).

## Steps
1. Synthetic fixtures: a field with a known puncta population spanning brightness, with pedestal and
   gain variants (extend `tests/fixtures_synthetic.py`).
2. Offset, scale, and selection-bias checks for the three thresholds.
3. The joint kurtosis × SNR interaction grid.
4. Append validated cases to `VALIDATED_CASES`.
5. Record findings (pass or fail) in the CHANGELOG and roadmap.
6. Full `pytest -m core` green — **unless a gate genuinely fails**, in which case ship the test as
   `xfail` with the finding documented, so the failure is visible rather than absent.
7. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- The three puncta refinement thresholds are swept for offset, scale, and selection-bias invariance.
- A joint interaction grid covers the two-parameter cliff case.
- Each threshold is recorded in `VALIDATED_CASES` as validated or as a documented finding.
- Any failure is reported with the parameter, the signature, and the magnitude — never silently
  accommodated.
- Full `pytest -m core` green (or documented `xfail` for a genuine finding).

## Cautions
- **A failing gate is a finding, not a tolerance problem.** Do not widen a threshold range to make a
  test pass; that reproduces the exact error the programme exists to catch.
- Fixtures must have a **known** answer — the number of real puncta must be constructed, not
  estimated by the code under test.
- Sweep the *plausible* range a user would actually set, not an extreme range that proves nothing.
- Do not fix a failing gate in this spec; write the finding and let the fix have its own acceptance
  criterion.
- Keep the fixtures small and seeded; this suite runs in `core`.
