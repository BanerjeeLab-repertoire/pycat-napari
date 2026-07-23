"""**Generated plans now state when a measurement cannot be trusted, and why** (navigator inc 2).

`utils/quality_gate.evaluate_quality` was fully built and had zero consumers. This wires it into the
planner: a measurement operation's `QualityRequirement` becomes ordinary `Assumption` gates on its
contract, so `compile`'s existing gate machinery evaluates them — no parallel reporting path. A hard
precondition unmet (no pixel size, invalid calibration) makes the measurement **not runnable** with a
stated reason; a soft one (reliability) stays runnable with the reason attached, and an unassessed signal
**prepends a QC probe** rather than passing. These pin that behaviour on two representative measurements
(`vpt.microrheology` — pixel size + reliability; `partition_enrichment.client` — calibration).
"""
from types import SimpleNamespace

import pytest

from pycat.navigator.context import AnalysisContext, Source
from pycat.navigator.contracts import AnalysisIntent, GateStatus
from pycat.navigator.op_catalog import build_operation_registry
from pycat.navigator.planner import Planner

pytestmark = pytest.mark.base


def _ctx(**facts):
    c = AnalysisContext()
    c.set('axes', ['time'], source=Source.METADATA)
    c.set('time_points', 120, source=Source.METADATA)
    c.set('channels', 2, source=Source.METADATA)
    for k, v in facts.items():
        c.set(k, v, source=Source.USER)
    return c


def _plan(observable, target='bead', **facts):
    reg = build_operation_registry()
    intent = AnalysisIntent(target=target, observables=[observable])
    return Planner(reg).compile(intent, _ctx(**facts))


def _quality_gates(plan):
    return {a.id: (a, status) for name, a, status in plan.gate_report if a.id.startswith('quality:')}


# ── a hard precondition unmet blocks the measurement, with a reason ─────────────────────────────
def test_a_measurement_without_its_hard_precondition_is_not_runnable_and_says_why():
    plan = _plan('viscosity', pixel_size=0)          # a viscosity in pixels is meaningless
    gates = _quality_gates(plan)
    assert gates['quality:pixel_size:vpt.microrheology'][1] is GateStatus.VIOLATED
    assert not plan.is_executable
    assert any('pixel_size' in b for b in plan.blockers()), plan.blockers()


def test_the_same_measurement_with_the_precondition_met_is_runnable():
    plan = _plan('viscosity', pixel_size=0.1,
                 reliability_score=SimpleNamespace(grade='high', value=0.9))
    gates = _quality_gates(plan)
    assert gates['quality:pixel_size:vpt.microrheology'][1] is GateStatus.SATISFIED
    assert gates['quality:reliability:vpt.microrheology'][1] is GateStatus.SATISFIED
    assert plan.is_executable


def test_an_invalid_calibration_blocks_a_concentration_measurement():
    plan = _plan('partitioning', target='condensate',
                 calibration_verdict=SimpleNamespace(valid=False, reason='2-point curve', level='error'))
    gates = _quality_gates(plan)
    assert gates['quality:calibration:partition_enrichment.client'][1] is GateStatus.VIOLATED
    assert not plan.is_executable
    assert any('calibration' in b for b in plan.blockers()), plan.blockers()

    ok = _plan('partitioning', target='condensate',
               calibration_verdict=SimpleNamespace(valid=True, reason='', level='ok'))
    assert _quality_gates(ok)['quality:calibration:partition_enrichment.client'][1] is GateStatus.SATISFIED
    assert ok.is_executable


# ── unknown is not ok: an unassessed signal probes rather than passes ───────────────────────────
def test_an_unassessed_signal_is_UNKNOWN_and_names_a_probe():
    plan = _plan('viscosity', pixel_size=0.1)        # reliability never assessed
    gate, status = _quality_gates(plan)['quality:reliability:vpt.microrheology']
    assert status is GateStatus.UNKNOWN, "an unassessed reliability must not read as a passing signal"
    assert gate.probe_observable == 'snr'            # names the QC observable a probe would measure
    # the planner acted on it — a QC probe is prepended (the existing probe mechanism)
    assert any(s.name == 'data_qc.assess' for s in plan.probes), [s.name for s in plan.probes]


# ── downgrade stays runnable, with the caveat attached ──────────────────────────────────────────
def test_a_downgrade_stays_runnable_with_the_reason_reported():
    plan = _plan('viscosity', pixel_size=0.1,
                 reliability_score=SimpleNamespace(grade='unreliable', value=0.2))
    gate, status = _quality_gates(plan)['quality:reliability:vpt.microrheology']
    assert status is GateStatus.VIOLATED           # reported...
    assert gate.severity == 'warning'              # ...but a warning, not a blocker
    assert plan.is_executable                       # so the plan is still runnable
    assert not any('reliability' in b for b in plan.blockers())   # not among the hard blockers


# ── one structure: quality verdicts live in the existing gate_report ────────────────────────────
def test_quality_verdicts_live_in_the_existing_gate_report():
    plan = _plan('viscosity', pixel_size=0.1)
    ids = [a.id for _n, a, _s in plan.gate_report]
    assert any(i.startswith('quality:') for i in ids), "quality gates must be folded into gate_report"
    # every gate_report row is the same (name, Assumption, GateStatus) shape — no parallel mechanism
    for name, a, status in plan.gate_report:
        assert isinstance(name, str) and isinstance(status, GateStatus) and hasattr(a, 'severity')


def test_a_measurement_without_a_quality_requirement_gets_no_quality_gates():
    """The change is surgical: only ops that DECLARE a QualityRequirement gain gates. A `size` plan
    (feature/size-distribution, no quality req) is unchanged — no quality gate appears."""
    plan = _plan('size', target='condensate')
    assert _quality_gates(plan) == {}
