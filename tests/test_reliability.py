"""**The Measurement Reliability Index composes existing signals honestly — no invented factor, no optimism.**

The load-bearing rules: an unmeasured factor is never treated as passing (it goes in `missing` and caps
the grade); each factor degraded individually lowers the score AND names itself in `reasons` (so no
contribution is silently ignored); the score is decomposable (value == product of contributions); reasons
are worst-first; and a REFUSED calibration scores `unreliable`, not merely low — a number computed under an
invalid calibration is not a weak measurement, it is not a measurement.
"""
import numpy as np
import pytest

from pycat.utils.reliability import ReliabilityScore, format_with_reliability, reliability

pytestmark = pytest.mark.core


def _clean_qc():
    return [{'name': 'Saturation', 'status': 'good', 'headline': 'ok'},
            {'name': 'SNR', 'status': 'good', 'headline': 'ok'}]


def _ok_calibration():
    return {'valid': True, 'level': 'ok', 'reason': 'acquisition matches'}


def _all_clean_kwargs():
    return dict(image_qc=_clean_qc(), object_flags=(0, 42), calibration=_ok_calibration(),
                sensitivity='stable', benchmark=0.95)


# ── Everything clean → high ──────────────────────────────────────────────────────────────────────
def test_a_fully_clean_measurement_scores_high():
    score = reliability('partition_coefficient', **_all_clean_kwargs())
    assert score.grade == 'high' and score.value >= 0.85
    assert score.reasons == () and score.missing == ()


# ── Each factor, degraded individually, lowers the score AND names itself ────────────────────────
@pytest.mark.parametrize('factor,degraded,needle', [
    ('image_qc', [{'name': 'Drift', 'status': 'bad', 'headline': 'periodic vibration detected'}], 'Drift'),
    ('object_flags', (21, 42), 'objects flagged'),
    ('calibration', {'valid': True, 'level': 'warn', 'reason': 'pixel size not verified'}, 'calibration warning'),
    ('sensitivity', 'unstable', 'sensitivity'),
    ('benchmark', 0.4, 'benchmark agreement'),
])
def test_each_degraded_factor_lowers_the_score_and_names_itself(factor, degraded, needle):
    clean = reliability('partition_coefficient', **_all_clean_kwargs())
    kwargs = _all_clean_kwargs(); kwargs[factor] = degraded
    degraded_score = reliability('partition_coefficient', **kwargs)

    assert degraded_score.value < clean.value, f"degrading {factor} did not lower the score"
    assert any(needle in r for r in degraded_score.reasons), (
        f"degrading {factor} did not name itself in reasons: {degraded_score.reasons}")
    assert degraded_score.contributions[factor] < 1.0


# ── An unmeasured factor is NOT a passing factor: it caps the grade and is listed ────────────────
def test_missing_core_factors_cap_the_grade_and_are_listed():
    # No QC and no calibration — cannot be 'high' no matter how good the rest is.
    score = reliability('partition_coefficient', object_flags=(0, 42), sensitivity='stable', benchmark=1.0)
    assert 'image_qc' in score.missing and 'calibration' in score.missing
    assert score.grade != 'high', "missing core evidence must cap the grade below high"
    assert any('not assessed' in r for r in score.reasons)


def test_a_factor_supplied_but_unassessable_counts_as_missing_not_passing():
    """QC supplied but every check is `na` (nothing assessable) → treated as missing, not as 1.0."""
    score = reliability('partition_coefficient',
                        image_qc=[{'name': 'X', 'status': 'na', 'headline': 'not applicable'}],
                        calibration=_ok_calibration(), object_flags=(0, 10),
                        sensitivity='stable', benchmark=1.0)
    assert 'image_qc' in score.missing and score.grade != 'high'


# ── The score is decomposable: value == product of contributions ─────────────────────────────────
def test_the_value_is_the_product_of_the_contributions():
    score = reliability('partition_coefficient',
                        image_qc=_clean_qc(), object_flags=(4, 40),   # 0.9
                        calibration={'valid': True, 'level': 'warn', 'reason': 'x'},  # 0.6
                        sensitivity='sensitive', benchmark=0.9)       # 0.6, 0.9
    expected = np.prod(list(score.contributions.values()))
    assert score.value == pytest.approx(expected, rel=1e-9)


# ── reasons are ordered worst-first ──────────────────────────────────────────────────────────────
def test_reasons_are_ordered_worst_first():
    score = reliability('partition_coefficient',
                        image_qc=[{'name': 'SNR', 'status': 'warn', 'headline': 'low SNR'}],   # 0.6
                        calibration=_ok_calibration(),               # 1.0
                        object_flags=(30, 40),                       # 0.25 — the worst
                        sensitivity='stable', benchmark=1.0)
    # The worst factor (object flags, 0.25) must be named before the milder one (QC warn, 0.6).
    flag_idx = next(i for i, r in enumerate(score.reasons) if 'objects flagged' in r)
    qc_idx = next(i for i, r in enumerate(score.reasons) if 'SNR' in r)
    assert flag_idx < qc_idx, f"reasons not worst-first: {score.reasons}"


# ── A REFUSED calibration is `unreliable`, not merely low ────────────────────────────────────────
def test_a_refused_calibration_is_unreliable_not_low():
    score = reliability('partition_coefficient', **{**_all_clean_kwargs(),
                        'calibration': {'valid': False, 'level': 'invalid',
                                        'reason': 'exposure mismatch: curve 0.1s vs image 0.5s'}})
    assert score.grade == 'unreliable' and score.value == 0.0
    assert any('REFUSED' in r for r in score.reasons), (
        "a number under an invalid calibration is not a measurement — it must say so")


def test_nothing_assessable_is_unreliable_with_a_reason():
    score = reliability('partition_coefficient')          # no factors at all
    assert score.grade == 'unreliable' and np.isnan(score.value)
    assert set(score.missing) == {'image_qc', 'object_flags', 'calibration', 'sensitivity', 'benchmark'}


def test_the_display_appends_the_grade():
    score = reliability('partition_coefficient', **_all_clean_kwargs())
    assert format_with_reliability('K_p', 4.2, '', score) == 'K_p = 4.2 (reliability: high)'


def test_it_composes_with_the_real_stability_result():
    """The sensitivity factor accepts a real `StabilityResult` (composition, not a reimplementation)."""
    from pycat.toolbox.measurement_stability import StabilityResult
    unstable = StabilityResult('k', 1.0, 'p', (1.0, 2.0), 1.0, 'unstable', (5, 5))
    score = reliability('partition_coefficient', image_qc=_clean_qc(), calibration=_ok_calibration(),
                        object_flags=(0, 10), sensitivity=unstable, benchmark=1.0)
    assert score.contributions['sensitivity'] == 0.2
    assert any('sensitivity' in r for r in score.reasons)
