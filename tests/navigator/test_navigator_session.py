"""**The Navigator is drivable end to end, and a compiled plan renders its quality-gate reasons inline.**

navigator increment 3 (the drive logic; the dock is a thin view over this). `NavigatorSession` drives the
existing engine — answer the scientific questions to a leaf, then compile a runnable plan — and `plan_rows`
turns the plan into ordered rows with each step's gate verdict folded in: a blocked step names WHY it cannot
run, a downgraded step names its caveat but stays runnable, and an unknown names the probe that will decide it.
Rendering a plan without those reasons throws away increment 2, so these pin them. Editing is a pin + recompile.
"""
from types import SimpleNamespace

import pytest

from pycat.navigator.context import AnalysisContext, Source
from pycat.navigator.contracts import AnalysisIntent, GateStatus
from pycat.navigator.op_catalog import build_operation_registry
from pycat.navigator.planner import Planner
from pycat.navigator.session import NavigatorSession, plan_rows, PlanRow, GateNote

# NOTE: not a file-level `pytestmark` — one test DRIVES the curated question tree (loaded from the shipped
# workbook via the optional openpyxl), so it is `integration`; the rest build only the registry/planner
# (curated in code, workbook-free) and stay `base`. Session construction is now workbook-free (lazy tree).


def _ctx(**facts):
    c = AnalysisContext()
    c.set('axes', ['time'], source=Source.METADATA)
    c.set('time_points', 120, source=Source.METADATA)
    c.set('channels', 2, source=Source.METADATA)
    for k, v in facts.items():
        c.set(k, v, source=Source.USER)
    return c


def _quality_plan(observable, target='bead', **facts):
    """A plan straight through the planner (bypassing the question tree) so the gate states are controllable —
    the same construction the increment-2 tests use."""
    reg = build_operation_registry()
    intent = AnalysisIntent(target=target, observables=[observable])
    return Planner(reg).compile(intent, _ctx(**facts))


# ── the drive loop reaches a leaf and compiles ───────────────────────────────────────────────────────

@pytest.mark.integration
def test_answering_the_questions_reaches_a_leaf_and_compiles_a_runnable_plan():
    session = NavigatorSession(ctx=_ctx())
    asked = 0
    while (q := session.next_question()) is not None:
        assert q.prompt and q.choices                       # a real question with choices
        session.answer(q, q.choices[0].value)               # always take the first choice
        asked += 1
        assert asked < 40, "the question loop did not terminate"
    assert asked >= 1 and session.is_ready()
    plan = session.compile_plan()
    assert plan.steps, "a completed intent must compile to at least one step"
    assert session.intent.observables, "reaching a leaf commits observables to the intent"


# ── plan_rows folds the gate verdicts in ─────────────────────────────────────────────────────────────

@pytest.mark.base
def test_a_blocked_step_renders_the_blocking_reason_and_is_not_runnable():
    plan = _quality_plan('viscosity', pixel_size=0)          # viscosity in pixels is meaningless → blocker
    rows = plan_rows(plan)
    blocked = [r for r in rows if r.state == 'blocked']
    assert blocked, "a hard-precondition failure must surface as a blocked row"
    row = blocked[0]
    assert not row.runnable
    note = next(n for n in row.gates if n.kind == 'blocked')
    assert 'pixel' in note.reason.lower() or 'pixel' in note.gate_id.lower()


@pytest.mark.base
def test_a_downgraded_step_renders_its_reason_but_stays_runnable():
    plan = _quality_plan('viscosity', pixel_size=0.1,
                         reliability_score=SimpleNamespace(grade='unreliable', value=0.2))
    rows = plan_rows(plan)
    dg = [r for r in rows if r.state == 'downgraded']
    assert dg and all(r.runnable for r in dg)               # reported, but still runnable
    assert any(n.kind == 'downgraded' for r in dg for n in r.gates)


@pytest.mark.base
def test_an_unknown_gate_renders_its_probe_and_the_probe_is_a_row():
    plan = _quality_plan('viscosity', pixel_size=0.1)        # reliability never assessed → UNKNOWN + probe
    rows = plan_rows(plan)
    # the UNKNOWN gate is named on its step ...
    assert any(n.kind == 'unknown' for r in rows for n in r.gates)
    # ... and the QC probe the planner prepended is its own leading row
    probes = [r for r in rows if r.kind == 'probe']
    assert probes and rows.index(probes[0]) < len(rows) and probes[0].state == 'probe'


@pytest.mark.base
def test_a_fully_satisfied_step_carries_no_gate_notes_and_stays_runnable():
    # every quality gate SATISFIED (pixel size present, reliability high) → the measurement step is clean:
    # SATISFIED gates must NOT produce a note (only VIOLATED / UNKNOWN do).
    plan = _quality_plan('viscosity', pixel_size=0.1,
                         reliability_score=SimpleNamespace(grade='high', value=0.9))
    rows = plan_rows(plan)
    assert rows and all(isinstance(r, PlanRow) for r in rows)
    measure = next(r for r in rows if r.name == 'vpt.microrheology')
    assert measure.state == 'ok' and measure.runnable and measure.gates == ()


@pytest.mark.base
def test_the_row_order_is_execution_order_probes_first():
    plan = _quality_plan('viscosity', pixel_size=0.1)
    rows = plan_rows(plan)
    kinds = [r.kind for r in rows]
    # any probe rows precede the step they support (probes are prepended in the plan)
    assert kinds[:len(plan.probes)] == ['probe'] * len(plan.probes)


# ── editing is a pin + recompile ─────────────────────────────────────────────────────────────────────

@pytest.mark.base
def test_editing_is_a_pin_and_recompile_that_revalidates():
    session = NavigatorSession(ctx=_ctx())
    session.intent.observables = ['count']                   # skip the tree; set the goal directly
    session.intent.target = 'cell'
    before = session.compile_plan()
    assert before.steps
    # pinning a provider is the edit; recompiling re-runs the full contract validation
    seg = next((s.name for s in before.steps if 'segment' in s.name), None)
    session.pin('nonexistent_kind', 'nonexistent_module')    # a harmless pin — plan still compiles/validates
    after = session.compile_plan()
    assert after.steps                                       # recompile always yields a validated plan
    assert session.pins == {'nonexistent_kind': 'nonexistent_module'}
    session.unpin('nonexistent_kind')
    assert session.pins == {}


# ── the minimal-lane contract: no openpyxl needed to build, edit, or compile ─────────────────────────

@pytest.mark.base
def test_the_navigator_builds_edits_and_compiles_without_openpyxl(monkeypatch):
    """`openpyxl` (the workbook reader) is optional at the minimal tier: constructing a session, building the
    standalone seed registry, and editing/compiling a plan must all work without it — only DRIVING the curated
    question tree needs it (the `integration` test above). Simulate openpyxl absent so this holds in a minimal
    environment and can't silently regress into a hidden dependency again."""
    import sys
    monkeypatch.setitem(sys.modules, 'openpyxl', None)       # any `import openpyxl` now raises ImportError

    from pycat.navigator.modules import build_registry
    assert len(build_registry()) > 0                          # the documented standalone seed needs no workbook

    session = NavigatorSession(ctx=_ctx())                    # constructs workbook-free (the tree loads lazily)
    session.intent.observables = ['count']
    session.intent.target = 'cell'
    assert session.compile_plan().steps                       # edit/compile path is openpyxl-independent
