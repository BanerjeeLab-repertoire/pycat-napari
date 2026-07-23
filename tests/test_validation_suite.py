"""**The validation-suite MACHINERY — it records honestly, catches a regression, and never rewrites history.**

These `core` tests exercise the suite's logic on the canonical cases (fast) — NOT a per-commit gate on the
metrics themselves (that runs deliberately via `python -m benchmarks.run_suite`). Pinned: a case run is
deterministic under its seed; the comparator flags a worse-than-tolerance move as a regression, reports an
improvement without failing, and treats runtime as advisory; and the results file is strictly append-only.
"""
import numpy as np
import pytest

from benchmarks.cases import CANONICAL_CASES
from benchmarks.run_suite import (
    Regression, append_record, compare_to_baseline, format_report, has_regression,
    read_records, run_case, run_suite)

pytestmark = pytest.mark.base


def test_the_suite_runs_end_to_end_and_writes_a_well_formed_record(tmp_path):
    record = run_suite('test-version')
    assert record['version'] == 'test-version'
    assert set(record['cases']) == {c.name for c in CANONICAL_CASES}
    for name, m in record['cases'].items():
        assert {'dice', 'iou', 'f1', 'count_error', 'runtime_s', 'family'} <= set(m)
        assert 0.0 <= m['dice'] <= 1.0
    assert 'partition_k' in record['cases']['partition_k5']    # the derived measurement is recorded

    path = tmp_path / 'results.jsonl'
    append_record(record, path)
    assert read_records(path) == [record]                      # round-trips cleanly


def test_ground_truth_is_constructed_and_a_case_is_deterministic():
    """Constructed GT + a fixed seed → identical metrics on a rerun (runtime excluded, it is advisory)."""
    def _stable(m):
        return {k: v for k, v in m.items() if k != 'runtime_s'}
    for case in CANONICAL_CASES:
        a, b = run_case(case), run_case(case)
        assert _stable(a) == _stable(b), f"case {case.name} is not deterministic"


# ── The comparator: a worse move is a regression; a better move is a reported improvement ────────
def _baseline():
    return {'version': 'v0', 'cases': {'puncta_20': {'dice': 0.90, 'iou': 0.85, 'f1': 0.95,
                                                     'count_error': 0, 'runtime_s': 0.10}}}


def test_an_injected_regression_is_flagged_and_an_unchanged_run_passes():
    base = _baseline()
    unchanged = {'version': 'v1', 'cases': {'puncta_20': dict(base['cases']['puncta_20'])}}
    assert not has_regression(compare_to_baseline(unchanged, base))

    worse = {'version': 'v1', 'cases': {'puncta_20': {**base['cases']['puncta_20'], 'dice': 0.80}}}  # −0.10
    comps = compare_to_baseline(worse, base)
    assert has_regression(comps)
    reg = next(c for c in comps if c.metric == 'dice')
    assert reg.kind == 'regression' and reg.delta == pytest.approx(-0.10)


def test_an_improvement_is_reported_not_failed():
    base = _baseline()
    better = {'version': 'v1', 'cases': {'puncta_20': {**base['cases']['puncta_20'], 'dice': 0.98}}}  # +0.08
    comps = compare_to_baseline(better, base)
    assert not has_regression(comps), "an improvement must NOT fail the run"
    imp = next(c for c in comps if c.metric == 'dice')
    assert imp.kind == 'improvement' and imp.delta == pytest.approx(0.08)


def test_a_higher_count_error_is_a_regression_but_runtime_is_only_advisory():
    base = _baseline()
    cur = {'version': 'v1', 'cases': {'puncta_20': {**base['cases']['puncta_20'],
                                                    'count_error': 3, 'runtime_s': 2.0}}}
    comps = compare_to_baseline(cur, base)
    kinds = {c.metric: c.kind for c in comps}
    assert kinds['count_error'] == 'regression'                # +3 objects wrong is a real change
    assert kinds['runtime_s'] == 'advisory'                    # runtime never fails a run
    assert has_regression(comps)                               # ...because of count_error, not runtime


def test_the_results_file_is_append_only(tmp_path):
    path = tmp_path / 'results.jsonl'
    append_record({'version': 'v0', 'cases': {}}, path)
    append_record({'version': 'v0', 'cases': {}}, path)        # a RERUN of the same version
    append_record({'version': 'v1', 'cases': {}}, path)
    records = read_records(path)
    assert len(records) == 3, "records must be appended, never rewritten — history is preserved"
    assert [r['version'] for r in records] == ['v0', 'v0', 'v1']


def test_the_report_names_regressions():
    base = _baseline()
    cur = {'version': 'v1', 'cases': {'puncta_20': {**base['cases']['puncta_20'], 'dice': 0.80}}}
    comps = compare_to_baseline(cur, base)
    report = format_report(cur, base, comps)
    assert 'REGRESSION' in report and 'puncta_20' in report


def test_the_committed_baseline_exists_and_is_well_formed():
    """The first recorded baseline is the deliverable — a real record must be committed."""
    records = read_records()
    assert records, "benchmarks/results.jsonl has no baseline record — the first baseline IS the deliverable"
    latest = records[-1]
    assert 'version' in latest and set(latest['cases']) == {c.name for c in CANONICAL_CASES}
