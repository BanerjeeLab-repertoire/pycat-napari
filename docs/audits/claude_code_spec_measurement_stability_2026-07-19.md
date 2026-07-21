# Claude Code spec — Per-measurement parameter stability

**Date:** 2026-07-19 · **Target tree:** 1.6.156 · Verified against the 1.6.156 tree. Answers a question
PyCAT cannot currently answer: *"if I nudge this parameter, does the number I am about to report
change?"* Direct input to the Measurement Reliability Index, and a rigor claim in its own right.

## The gap (verified, and narrower than it looks)
`benchmark_tools.run_benchmark` already supports parameter sensitivity — but its own docstring defines
the scope precisely:

> *"Parameter-sensitivity mode is just method-comparison where the candidates are the same method at
> different parameter values… the caller builds those candidates and can read the trend from the
> ordered results."*

So it sweeps a parameter and compares the resulting **masks** (Dice, IoU, matched-detection F1). What
it does not do is report how much each **derived measurement** moves. Verified: no
`parameter_stability` / `measurement_stability` module exists.

That distinction matters scientifically. Two parameter settings can produce masks that agree at Dice
0.95 while the *partition coefficient* computed from them differs by 40% — because a small boundary
shift moves the dense/dilute split. **Mask agreement is not measurement agreement**, and it is the
measurement that gets published.

## Design — `toolbox/measurement_stability.py`
```python
@dataclass(frozen=True)
class StabilityResult:
    measurement: str
    baseline: float
    perturbation: str          # 'threshold ±5%', 'min_size ±2 px'
    values: tuple[float, ...]  # one per swept setting
    relative_range: float      # (max-min)/|baseline| — the headline number
    verdict: str               # 'stable' | 'sensitive' | 'unstable'
    n_objects: tuple[int, ...] # object count at each setting — see below

def measurement_stability(image, method, param, sweep, measure_fn, *,
                          baseline=None) -> list[StabilityResult]
```
Sweep the parameter, run the **full chain** (segmentation → measurement) at each setting, and report
per-measurement variation. Reuse `run_candidate` from `benchmark_tools` for the segmentation half —
do not write a second runner.

### The verdict rule, stated not tuned
Report `relative_range` and classify against **declared, documented** thresholds (e.g. <5% stable,
5–20% sensitive, >20% unstable). The thresholds are a stated convention, not a fitted quantity — say
so in the docstring so nobody treats them as empirical.

### Two traps to encode
1. **Population change vs measurement change.** If a sweep alters the *number of objects*, a shifting
   mean may reflect a different population rather than an unstable measurement. Report `n_objects`
   alongside, and when it varies materially, say so in the verdict — *"mean changed 30%, but the
   object count changed 3× — this is a population change, not measurement instability."* Conflating
   these would produce confidently wrong advice.
2. **Sweep the range a user would actually set.** A ±90% sweep proves nothing. Default to a modest
   perturbation around the current value (±5–10%), and document the swept range in the result.

## Scope — the measurements that get published
Start with the derived quantities, not raw geometry: `partition_coefficient`, `client_enrichment`,
concentration/ΔG outputs, object count and density, mean object size. These are the ones where a
threshold nudge changes a conclusion.

## Integration
- **MRI input:** `StabilityResult.verdict` becomes the parameter-sensitivity factor the reliability
  spec expects — wire it as an adapter, do not duplicate the logic.
- **Report artifact:** a small table + plot (measurement value vs parameter, baseline marked) suitable
  for a supplementary figure — *"reported values varied <5% across a ±10% threshold sweep."*
- **Ontology link:** report using the ontology's `display_name` and `units` so the output is readable.

## Tests (`core`, synthetic)
- A measurement that is genuinely stable (e.g. total intensity in a well-separated field) reports
  `stable` across a threshold sweep.
- A measurement that is genuinely sensitive (a partition coefficient in a low-contrast field, where the
  boundary decides the split) reports `sensitive`/`unstable` — **constructed so the true sensitivity is
  known**, not asserted from the code's own output.
- **The population-change test:** a sweep that changes object count is reported as a population change,
  not as measurement instability.
- `relative_range` is scale-free: multiplying all intensities by a constant does not change the verdict.
- A zero/near-zero baseline does not produce a divide-by-zero verdict — return `nan` with a stated
  reason rather than an infinite range.

## Steps
1. `toolbox/measurement_stability.py` — `StabilityResult` + `measurement_stability`, reusing
   `run_candidate`.
2. Population-change detection via `n_objects`.
3. Report table + plot.
4. MRI adapter.
5. Tests above.
6. Full `pytest -m core` green.
7. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- Sweeping a parameter reports per-measurement variation, not just mask agreement.
- Population changes are distinguished from measurement instability.
- Verdict thresholds are declared and documented as convention.
- Results feed the MRI as the sensitivity factor.
- Full `pytest -m core` green.

## Cautions
- **Mask agreement ≠ measurement agreement.** That is the entire premise; do not shortcut by reusing
  Dice as a stability proxy.
- Distinguish population change from instability, or the tool gives confidently wrong advice.
- Sweep plausible ranges only; document the range in the output.
- Reuse `benchmark_tools.run_candidate` — a second segmentation runner would drift.
- Thresholds are a stated convention. Do not present them as empirically derived.
