"""**A compiled plan has a gate-respecting execution ORDER: probes first, blockers halt, caveats run.**

selection_scale Part 2 (the gate-respecting execution model; the per-op adapters that compute each step are a
separate layer). `execution_order` turns a `Plan` into the sequence a runner (or a guided hand-off to the real
method panels) must honour — QC probes lead, a hard-precondition blocker stops the run at that step and skips
everything after it, and a soft caveat runs with its reason carried. These pin that contract on the same
representative measurements the increment-2 gate tests use.
"""
from types import SimpleNamespace

import pytest

from pycat.navigator.context import AnalysisContext, Source
from pycat.navigator.contracts import AnalysisIntent
from pycat.navigator.op_catalog import build_operation_registry
from pycat.navigator.planner import Planner
from pycat.navigator.execution import (
    execution_order, is_runnable, first_blocker, execution_briefing, ExecStep)

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
    return Planner(reg).compile(AnalysisIntent(target=target, observables=[observable]), _ctx(**facts))


def test_qc_probes_lead_the_execution_order():
    steps = execution_order(_plan('viscosity', pixel_size=0.1))   # reliability UNKNOWN → a QC probe prepended
    probes = [s for s in steps if s.kind == 'probe']
    assert probes, "the planner prepended a QC probe; it must lead"
    assert steps[0].kind == 'probe' and steps[0].status == 'run'


def test_a_blocker_halts_the_run_and_everything_after_is_skipped():
    steps = execution_order(_plan('viscosity', pixel_size=0))      # no pixel size → hard blocker on the measure
    blocked = [s for s in steps if s.status == 'blocked']
    assert blocked, "an unmet hard precondition must surface as a blocked step"
    assert not is_runnable(_plan('viscosity', pixel_size=0))
    fb = first_blocker(_plan('viscosity', pixel_size=0))
    assert fb is not None and 'pixel' in fb.reason.lower()
    # everything AFTER the first blocker is 'skipped', never 'run'
    idx = next(i for i, s in enumerate(steps) if s.status == 'blocked')
    assert all(s.status == 'skipped' for s in steps[idx + 1:])


def test_a_soft_caveat_runs_with_its_reason_carried():
    steps = execution_order(_plan('viscosity', pixel_size=0.1,
                                  reliability_score=SimpleNamespace(grade='unreliable', value=0.2)))
    caveat = [s for s in steps if s.status == 'caveat']
    assert caveat and caveat[0].reason                            # runs, but carries the caveat text
    assert is_runnable(_plan('viscosity', pixel_size=0.1,
                             reliability_score=SimpleNamespace(grade='unreliable', value=0.2)))


def test_a_clean_plan_runs_every_step_and_none_is_blocked():
    steps = execution_order(_plan('viscosity', pixel_size=0.1,
                                  reliability_score=SimpleNamespace(grade='high', value=0.9)))
    assert steps and all(s.status in ('run', 'caveat') for s in steps)
    assert is_runnable(_plan('viscosity', pixel_size=0.1,
                             reliability_score=SimpleNamespace(grade='high', value=0.9)))
    assert first_blocker(_plan('viscosity', pixel_size=0.1,
                               reliability_score=SimpleNamespace(grade='high', value=0.9))) is None


def test_the_briefing_names_the_order_and_any_blocker():
    ok = execution_briefing(_plan('size', target='condensate'))
    assert ok.startswith("Run order:")
    blocked = execution_briefing(_plan('viscosity', pixel_size=0))
    assert "cannot run" in blocked and "stops here" in blocked


def test_exec_steps_are_frozen_records():
    import dataclasses
    steps = execution_order(_plan('size', target='condensate'))
    assert steps and all(isinstance(s, ExecStep) for s in steps)
    with pytest.raises(dataclasses.FrozenInstanceError):
        steps[0].status = 'x'
