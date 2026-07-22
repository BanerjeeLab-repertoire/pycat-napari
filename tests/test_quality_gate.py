"""**The quality gate composes existing signals into block / warn / downgrade — unassessed is never a pass.**

Pins the contract: a requested-but-unmet HARD precondition BLOCKs (do not run); an unassessed signal WARNs
(never a silent pass); an unreliable measurement DOWNGRADEs (do not report at face value); the overall
verdict is the WORST signal; every signal outcome is reported; and an op that requires nothing is OK.
"""
import pytest

from pycat.utils.quality_gate import (
    evaluate_quality, QualityRequirement, GateVerdict, GateResult)

pytestmark = pytest.mark.core


def _verdicts(result):
    return {s.name: s.verdict for s in result.signals}


def test_an_op_requiring_nothing_is_OK_and_runnable():
    r = evaluate_quality(None, QualityRequirement(), context={})
    assert r.verdict is GateVerdict.OK and r.runnable and r.signals == ()


def test_a_missing_pixel_size_BLOCKS_a_physical_unit_measurement():
    r = evaluate_quality(None, QualityRequirement(needs_pixel_size=True), context={'pixel_size_ok': False})
    assert r.verdict is GateVerdict.BLOCK and not r.runnable
    assert 'pixel size' in r.reasons[0].lower()


def test_an_UNASSESSED_pixel_size_WARNS_it_is_not_a_silent_pass():
    r = evaluate_quality(None, QualityRequirement(needs_pixel_size=True), context={})  # ok is None
    assert r.verdict is GateVerdict.WARN and r.runnable
    assert _verdicts(r)['pixel_size'] is GateVerdict.WARN


def test_a_real_pixel_size_is_OK():
    r = evaluate_quality(None, QualityRequirement(needs_pixel_size=True), context={'pixel_size_ok': True})
    assert r.verdict is GateVerdict.OK


def test_an_invalid_calibration_BLOCKS_and_a_warn_level_WARNS():
    class _V:
        def __init__(self, valid, level, reason): self.valid, self.level, self.reason = valid, level, reason
    bad = evaluate_quality(None, QualityRequirement(needs_calibration=True),
                           context={'calibration_verdict': _V(False, 'invalid', 'different microscope')})
    assert bad.verdict is GateVerdict.BLOCK and 'different microscope' in bad.reasons[0]
    warn = evaluate_quality(None, QualityRequirement(needs_calibration=True),
                            context={'calibration_verdict': _V(True, 'warn', 'acquired 40 days ago')})
    assert warn.verdict is GateVerdict.WARN
    ok = evaluate_quality(None, QualityRequirement(needs_calibration=True),
                          context={'calibration_verdict': _V(True, 'ok', '')})
    assert ok.verdict is GateVerdict.OK


def test_a_needed_calibration_that_is_absent_WARNS_not_passes():
    r = evaluate_quality(None, QualityRequirement(needs_calibration=True), context={})
    assert r.verdict is GateVerdict.WARN and _verdicts(r)['calibration'] is GateVerdict.WARN


def test_an_unreliable_measurement_DOWNGRADES():
    class _S:
        value, grade = 0.1, 'unreliable'
    r = evaluate_quality(None, QualityRequirement(min_reliability='moderate', measurement_key='viscosity'),
                         context={'reliability_score': _S()})
    assert r.verdict is GateVerdict.DOWNGRADE and 'face value' in r.reasons[0].lower()


def test_a_grade_below_the_floor_WARNS_and_at_or_above_is_OK():
    class _S:
        def __init__(self, g): self.value, self.grade = 0.5, g
    below = evaluate_quality(None, QualityRequirement(min_reliability='high', measurement_key='k'),
                             context={'reliability_score': _S('moderate')})
    assert below.verdict is GateVerdict.WARN
    ok = evaluate_quality(None, QualityRequirement(min_reliability='moderate', measurement_key='k'),
                          context={'reliability_score': _S('high')})
    assert ok.verdict is GateVerdict.OK


def test_an_unassessed_reliability_WARNS_nan_is_not_a_pass():
    class _S:
        value, grade = float('nan'), None
    r = evaluate_quality(None, QualityRequirement(min_reliability='low', measurement_key='k'),
                         context={'reliability_score': _S()})
    assert r.verdict is GateVerdict.WARN


def test_the_overall_verdict_is_the_WORST_signal_and_all_are_reported():
    class _V:
        valid, level, reason = False, 'invalid', 'wrong curve'
    r = evaluate_quality(
        None,
        QualityRequirement(needs_pixel_size=True, needs_calibration=True, min_reliability='low',
                           measurement_key='k'),
        context={'pixel_size_ok': True, 'calibration_verdict': _V(),
                 'reliability_score': type('S', (), {'value': 0.5, 'grade': 'moderate'})()})
    assert r.verdict is GateVerdict.BLOCK               # calibration blocks; it is the worst
    assert len(r.signals) == 3 and not r.runnable       # all three reported
    assert _verdicts(r) == {'pixel_size': GateVerdict.OK, 'calibration': GateVerdict.BLOCK,
                            'reliability': GateVerdict.OK}


def test_it_composes_the_real_reliability_signal_when_given_raw_inputs():
    # no precomputed score → the gate calls reliability() with the raw signals it was handed
    r = evaluate_quality(
        None, QualityRequirement(min_reliability='high', measurement_key='partition_coefficient'),
        context={'object_flags': {'edge_touching': 0.9}})   # a poor object-flags signal
    assert isinstance(r, GateResult) and r.signals[0].name == 'reliability'
