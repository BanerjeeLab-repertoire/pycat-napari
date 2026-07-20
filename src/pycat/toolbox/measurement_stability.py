"""**If I nudge this parameter, does the number I am about to REPORT change?** — per-measurement, not per-mask.

`benchmark_tools.run_benchmark` already sweeps a parameter and compares the resulting **masks** (Dice, IoU,
matched-detection F1). What it does not do is report how much each *derived measurement* moves — and that
is the scientifically load-bearing distinction: **two settings can produce masks that agree at Dice 0.95
while the partition coefficient computed from them differs by 40%**, because a small boundary shift moves
the dense/dilute split. Mask agreement is not measurement agreement, and it is the measurement that gets
published.

This sweeps a parameter over a **plausible** range (a modest ±perturbation a user would actually set, not
a ±90% sweep that proves nothing), runs the FULL chain (segmentation → measurement) at each setting, and
reports the relative variation of each reported number with a stated verdict.

**Two traps encoded:**
- *Population change vs measurement change.* If a sweep alters the number of objects, a shifting mean may
  reflect a different population, not an unstable measurement — so `n_objects` is reported alongside and,
  when it varies materially, the verdict says "population change" rather than confidently calling the
  measurement unstable.
- *A near-zero baseline* yields `nan` with a stated reason, never an infinite relative range.

The verdict thresholds (<5% stable, 5–20% sensitive, >20% unstable) are a **stated convention**, not an
empirically fitted quantity. Segmentation reuses `benchmark_tools.run_candidate` — no second runner to drift.
"""
from __future__ import annotations

import dataclasses

import numpy as np


# ── Verdict thresholds — a STATED CONVENTION, not an empirically derived quantity ───────────────
_STABLE_MAX = 0.05         # relative range < 5%  → the reported number barely moves
_SENSITIVE_MAX = 0.20      # 5–20% → worth noting; > 20% → unstable
#: A population is "materially" different when the object count changes by more than this factor across the
#: sweep — at which point a shifting mean is a different population, not measurement instability.
_POPULATION_FACTOR = 1.5


@dataclasses.dataclass(frozen=True)
class StabilityResult:
    measurement: str
    baseline: float
    perturbation: str                # e.g. 'threshold sweep 0.45..0.55 (baseline 0.50)'
    values: tuple                    # one measured value per swept setting
    relative_range: float            # (max - min) / |baseline| — the headline number (nan if undefined)
    verdict: str                     # 'stable' | 'sensitive' | 'unstable' | 'population-change' | 'undefined'
    n_objects: tuple                 # object count at each setting — the population-change guard
    reason: str = ''                 # a stated reason, especially for 'undefined' / 'population-change'


def _object_count(labels):
    """Object count via the SAME machinery benchmark_tools uses — no parallel implementation."""
    from pycat.toolbox.benchmark_tools import _labelled, basic_metrics
    return int(basic_metrics(_labelled(labels), None)['n_objects'])


def _classify(baseline, values, n_objects):
    """The verdict for one measurement, from its swept values and the object counts."""
    finite = [v for v in values if np.isfinite(v)]
    if abs(baseline) < 1e-9 or not np.isfinite(baseline):
        return (float('nan'), 'undefined',
                'baseline is zero/near-zero, so a relative range is undefined (a divide-by-zero) — '
                'report the absolute values instead')
    if len(finite) < 2:
        return float('nan'), 'undefined', 'too few finite measurements across the sweep to assess'
    rel_range = (max(finite) - min(finite)) / abs(baseline)

    counts = [n for n in n_objects if n is not None]
    if counts and min(counts) > 0 and (max(counts) / min(counts)) >= _POPULATION_FACTOR:
        return (rel_range, 'population-change',
                f'the object count changed {min(counts)}→{max(counts)} across the sweep — a shifting '
                f'measurement here reflects a DIFFERENT POPULATION, not measurement instability')

    if rel_range < _STABLE_MAX:
        verdict = 'stable'
    elif rel_range < _SENSITIVE_MAX:
        verdict = 'sensitive'
    else:
        verdict = 'unstable'
    return rel_range, verdict, ''


def measurement_stability(image, method, param, sweep, measure_fn, *, baseline=None):
    """Sweep ``param`` over ``sweep`` and report how much each derived measurement moves.

    Parameters
    ----------
    image : the field to analyse.
    method : callable ``(image, **{param: value}) -> mask`` (binary or labelled) — the segmentation half.
    param : the parameter name being swept.
    sweep : the values to try (keep it a PLAUSIBLE range — a modest ± around the current value).
    measure_fn : callable ``(labelled_mask, image) -> dict[str, float]`` — the derived measurements at one
        setting. One `StabilityResult` is returned per key.
    baseline : the setting treated as the reference (its measured value is the denominator of the relative
        range). Defaults to the middle of ``sweep``.

    Returns a list of `StabilityResult`, one per measurement.
    """
    from pycat.toolbox.benchmark_tools import Candidate, run_candidate

    sweep = list(sweep)
    if not sweep:
        return []
    base_setting = baseline if baseline is not None else sweep[len(sweep) // 2]

    per_setting = []          # (setting, {measurement: value}, n_objects)
    for value in sweep:
        cand = Candidate(f'{param}={value}', method_fn=lambda img, v=value: method(img, **{param: v}))
        labels, _ = run_candidate(cand, image)
        measures = measure_fn(labels, image)
        per_setting.append((value, dict(measures), _object_count(labels)))

    # The baseline row (nearest swept setting to base_setting).
    base_idx = min(range(len(sweep)), key=lambda i: abs(sweep[i] - base_setting)
                   if isinstance(base_setting, (int, float)) else 0)
    base_measures = per_setting[base_idx][1]

    names = list(base_measures.keys())
    n_objects = tuple(row[2] for row in per_setting)
    perturbation = (f'{param} sweep {min(sweep)}..{max(sweep)} (baseline {sweep[base_idx]})'
                    if all(isinstance(s, (int, float)) for s in sweep) else f'{param} sweep {sweep}')

    results = []
    for name in names:
        values = tuple(float(row[1].get(name, float('nan'))) for row in per_setting)
        baseline_val = float(base_measures.get(name, float('nan')))
        rel_range, verdict, reason = _classify(baseline_val, values, list(n_objects))
        results.append(StabilityResult(
            measurement=name, baseline=baseline_val, perturbation=perturbation, values=values,
            relative_range=rel_range, verdict=verdict, n_objects=n_objects, reason=reason))
    return results


# ── MRI adapter — the parameter-sensitivity factor the Measurement Reliability Index expects ────
_VERDICT_FACTOR = {'stable': 1.0, 'sensitive': 0.6, 'unstable': 0.2}


def stability_factor(result) -> float:
    """Map a `StabilityResult.verdict` to a 0..1 reliability factor for the MRI. `nan` when the stability
    cannot be assessed (undefined baseline / population change) — the MRI must treat that as "unknown",
    not as "reliable". This is the adapter; the classification logic lives here once and is not duplicated
    in the reliability index."""
    return _VERDICT_FACTOR.get(result.verdict, float('nan'))


# ── Report artifact — measurement value vs parameter, baseline marked ───────────────────────────
def stability_report_figure(results, sweep, *, ax=None):
    """A supplementary-figure artifact: each measurement's value across the swept parameter, its baseline
    marked, and its verdict stated — *"reported values varied <5% across a ±10% threshold sweep."* Reads
    the ontology's display name/units for readable labels when it knows the measurement."""
    import matplotlib
    if ax is None:
        matplotlib.use('Agg', force=False)
    import matplotlib.pyplot as plt
    from pycat.utils.measurement_ontology import describe

    sweep = list(sweep)
    fig = ax.figure if ax is not None else plt.figure(figsize=(6, 4))
    ax = ax or fig.add_subplot(111)
    for r in results:
        mdef = describe(r.measurement)
        label = f"{mdef.display_name} [{mdef.units}]" if mdef else r.measurement
        vals = np.asarray(r.values, dtype=float)
        # normalise each measurement to its baseline so different-scale measurements share one axis
        norm = vals / r.baseline if (np.isfinite(r.baseline) and r.baseline != 0) else vals
        ax.plot(sweep, norm, '-o', label=f"{label} — {r.verdict} ({r.relative_range:.0%})")
    ax.axhline(1.0, color='#888', linestyle=':', linewidth=1)
    ax.set_xlabel('swept parameter value')
    ax.set_ylabel('measurement / baseline')
    ax.set_title('Measurement stability across a parameter sweep', fontsize=9, loc='left')
    ax.legend(loc='best', fontsize=8, frameon=False)
    fig.tight_layout()
    return fig
