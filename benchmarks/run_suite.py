"""**Run the canonical cases, record the metrics, compare across releases.**

The test suite answers "did anything break?" per commit. It does not answer *"is segmentation quality on
our canonical cases the same as it was ten releases ago?"* — and at PyCAT's release cadence (often several
versions a day) that slow-degradation question is the one that matters: a change can keep every test green
while moving Dice from 0.94 to 0.89, and nobody notices until a result looks wrong months later.

This measures each canonical case against its **constructed** ground truth (Dice/IoU via `benchmark_tools`,
matched-detection F1, object-count error, key derived measurements, and runtime), appends one record per
version to `results.jsonl` (append-only — JSONL diffs cleanly and never rewrites history), and compares a
run against a baseline. It **fails on a metric moving beyond a declared tolerance in the WORSE direction**,
**reports (never fails on) an improvement** — because an unexplained improvement often means the case or
ground truth changed — and treats runtime as advisory (sandbox/CI machines vary).

Run:  ``python -m benchmarks.run_suite [version]``
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import time

import numpy as np

_RESULTS_PATH = pathlib.Path(__file__).resolve().parent / 'results.jsonl'


# ── Declared tolerances, each with a justification. NEVER tune one to make a run pass — a metric moving
# beyond tolerance is a FINDING to record and investigate (the filter-sensitivity programme's discipline).
_TOLERANCES = {
    'dice':        {'tol': 0.02, 'worse': 'lower',  'why': 'Dice ±0.02 covers seeded-RNG variation; larger is a real change'},
    'iou':         {'tol': 0.02, 'worse': 'lower',  'why': 'IoU ±0.02 covers seeded-RNG variation'},
    'f1':          {'tol': 0.02, 'worse': 'lower',  'why': 'matched-detection F1 ±0.02 covers seeded variation'},
    'count_error': {'tol': 1,    'worse': 'higher', 'why': 'an object-count error growing by >1 is a real segmentation change'},
    'partition_k': {'tol_rel': 0.05, 'worse': 'either', 'why': 'a partition coefficient moving >5% is a real measurement change'},
    'runtime_s':   {'advisory': True, 'tol': 0.5, 'why': 'runtime is advisory — machines vary; a >0.5s change is a prompt to look, not a failure'},
}


@dataclasses.dataclass(frozen=True)
class Regression:
    case: str
    metric: str
    baseline: float
    current: float
    delta: float
    kind: str            # 'regression' | 'improvement' | 'advisory'
    tolerance: float


def run_case(case) -> dict:
    """Measure one case against its constructed ground truth. Returns a metrics dict (runtime excluded from
    equality checks — it is advisory and machine-dependent)."""
    from pycat.toolbox.benchmark_tools import _labelled, pixel_overlap, matched_detection, basic_metrics

    image, gt = case.build()
    t0 = time.perf_counter()
    pred = case.method(image)
    runtime_s = time.perf_counter() - t0

    pred_lab, gt_lab = _labelled(pred), _labelled(gt)
    overlap = pixel_overlap(pred_lab, gt_lab)
    det = matched_detection(pred_lab, gt_lab, tolerance_px=5.0)
    n_pred = int(basic_metrics(pred_lab, None)['n_objects'])
    n_gt = int(basic_metrics(gt_lab, None)['n_objects'])

    metrics = {
        'family': case.family,
        'dice': round(float(overlap['dice']), 6),
        'iou': round(float(overlap['iou']), 6),
        'f1': round(float(det['f1']) if det['f1'] == det['f1'] else float('nan'), 6),
        'n_pred': n_pred, 'n_gt': n_gt, 'count_error': abs(n_pred - n_gt),
        'runtime_s': round(runtime_s, 4),
    }
    if case.derived is not None:
        for k, v in case.derived(pred_lab, image).items():
            metrics[k] = round(float(v), 6)
    return metrics


def run_suite(version, cases=None) -> dict:
    """Run every canonical case and return a version record ``{version, cases: {name: metrics}}``."""
    from benchmarks.cases import CANONICAL_CASES
    cases = CANONICAL_CASES if cases is None else cases
    return {'version': str(version), 'cases': {c.name: run_case(c) for c in cases}}


def append_record(record, path=_RESULTS_PATH):
    """Append ONE version record as a JSON line — append-only, so a rerun never rewrites prior history."""
    path = pathlib.Path(path)
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + '\n')
    return path


def read_records(path=_RESULTS_PATH) -> list:
    path = pathlib.Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def compare_to_baseline(current, baseline, *, tolerances=_TOLERANCES) -> list:
    """Every metric that moved beyond its declared tolerance, classified regression / improvement /
    advisory. A regression is a WORSE-direction move; an improvement is a better-direction move (reported,
    never failed); runtime is advisory."""
    out = []
    for case_name, cur in current.get('cases', {}).items():
        base = baseline.get('cases', {}).get(case_name)
        if base is None:
            continue
        for metric, spec in tolerances.items():
            if metric not in cur or metric not in base:
                continue
            cv, bv = float(cur[metric]), float(base[metric])
            if not (np.isfinite(cv) and np.isfinite(bv)):
                continue
            delta = cv - bv
            tol = spec['tol_rel'] * abs(bv) if 'tol_rel' in spec else spec['tol']
            if spec.get('advisory'):
                if abs(delta) > tol:
                    out.append(Regression(case_name, metric, bv, cv, delta, 'advisory', tol))
                continue
            worse = spec['worse']
            worse_move = ((worse == 'lower' and delta < -tol) or (worse == 'higher' and delta > tol)
                          or (worse == 'either' and abs(delta) > tol))
            better_move = ((worse == 'lower' and delta > tol) or (worse == 'higher' and delta < -tol))
            if worse_move:
                out.append(Regression(case_name, metric, bv, cv, delta, 'regression', tol))
            elif better_move:
                out.append(Regression(case_name, metric, bv, cv, delta, 'improvement', tol))
    return out


def has_regression(comparisons) -> bool:
    """True if any comparison is a real regression (improvements and advisories do not fail a run)."""
    return any(c.kind == 'regression' for c in comparisons)


def format_report(current, baseline=None, comparisons=None) -> str:
    """A short human report: metrics per case, and the deltas vs the baseline when given."""
    lines = [f"PyCAT Validation Suite — version {current['version']}"]
    for name, m in current['cases'].items():
        core = (f"dice={m.get('dice', float('nan')):.3f} iou={m.get('iou', float('nan')):.3f} "
                f"f1={m.get('f1', float('nan')):.3f} count_err={m.get('count_error', '?')}")
        lines.append(f"  [{m.get('family', '?')}] {name}: {core}  ({m.get('runtime_s', 0.0):.3f}s)")
    if comparisons:
        lines.append("\nChanges vs baseline "
                     + (f"{baseline['version']}" if baseline else "") + ":")
        for c in sorted(comparisons, key=lambda r: (r.kind, r.case)):
            arrow = {'regression': '✗ REGRESSION', 'improvement': '↑ improvement', 'advisory': '· advisory'}[c.kind]
            lines.append(f"  {arrow}  {c.case}.{c.metric}: {c.baseline:.4g} → {c.current:.4g} "
                         f"(Δ{c.delta:+.4g}, tol {c.tolerance:g})")
    elif baseline is not None:
        lines.append(f"\nNo metric moved beyond tolerance vs {baseline['version']}.")
    return "\n".join(lines)


def main(argv=None):
    import sys
    version = (argv or sys.argv[1:] or ['unversioned'])[0]
    current = run_suite(version)
    records = read_records()
    baseline = records[-1] if records else None
    comparisons = compare_to_baseline(current, baseline) if baseline else []
    print(format_report(current, baseline, comparisons))
    append_record(current)
    print(f"\nAppended to {_RESULTS_PATH}")
    return 1 if has_regression(comparisons) else 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
