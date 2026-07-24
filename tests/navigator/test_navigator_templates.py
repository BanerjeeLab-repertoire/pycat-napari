"""**A liked guided plan saves as a reusable template — the answers persist, the gate verdicts do not.**

selection_scale Part 3. The questionnaire's payoff: save a plan under a name and reuse it on other data without
re-answering. A template stores the ANSWERS (intent + step names) but NOT the quality-gate verdicts — those are
a property of the data, so applying a template re-compiles the intent against the new dataset's context, which
re-evaluates every gate (a template runnable on one dataset may be blocked on another). Persists across
sessions; a corrupt entry degrades to "not available", never a crash.
"""
from types import SimpleNamespace

import pytest

from pycat.navigator.context import AnalysisContext, Source
from pycat.navigator.contracts import AnalysisIntent
from pycat.navigator.op_catalog import build_operation_registry
from pycat.navigator.planner import Planner
from pycat.navigator.execution import is_runnable
from pycat.navigator.templates import (
    GuidedTemplate, template_from_plan, intent_from_template, save_template, list_templates,
    load_template, delete_template, rename_template)
from pycat.utils.user_settings import UserSettings

pytestmark = pytest.mark.base


def _store(tmp_path):
    return UserSettings(path=tmp_path / "s.json")


def _ctx(**facts):
    c = AnalysisContext()
    c.set('axes', ['time'], source=Source.METADATA)
    c.set('time_points', 120, source=Source.METADATA)
    c.set('channels', 2, source=Source.METADATA)
    for k, v in facts.items():
        c.set(k, v, source=Source.USER)
    return c


def _reg():
    return build_operation_registry()


def _plan(reg, observable, target='bead', **facts):
    return Planner(reg).compile(AnalysisIntent(target=target, observables=[observable]), _ctx(**facts))


# ── save + reload carries the answers and the steps ──────────────────────────────────────────────────

def test_a_plan_saves_and_reloads_with_the_same_steps_and_answers(tmp_path):
    reg = _reg()
    intent = AnalysisIntent(target='cell', observables=['count'], question='Quantify > Number > Per field')
    plan = Planner(reg).compile(intent, _ctx())
    tmpl = save_template(template_from_plan('My cell count', intent, plan), store=_store(tmp_path))

    reloaded = load_template('My cell count', store=UserSettings(path=tmp_path / "s.json"))
    assert reloaded is not None
    assert reloaded.observables == ('count',) and reloaded.target == 'cell'
    assert reloaded.question == 'Quantify > Number > Per field'          # the answers are recorded
    assert reloaded.steps == tuple(s.name for s in plan.steps) and reloaded.steps


def test_a_template_records_the_answers_that_generated_it(tmp_path):
    intent = AnalysisIntent(target='condensate', observables=['size'], question='Structures > Size')
    tmpl = template_from_plan('t', intent, _plan(_reg(), 'size', target='condensate'))
    assert tmpl.question == 'Structures > Size' and tmpl.observables == ('size',)


# ── applying to a different dataset re-runs the gates (verdicts are not carried) ──────────────────────

def test_applying_to_a_different_dataset_re_evaluates_the_gates(tmp_path):
    reg = _reg()
    # authored where the measurement is runnable (pixel size present)
    good = _plan(reg, 'viscosity', pixel_size=0.1,
                 reliability_score=SimpleNamespace(grade='high', value=0.9))
    assert is_runnable(good)
    intent = AnalysisIntent(target='bead', observables=['viscosity'])
    tmpl = save_template(template_from_plan('visc', intent, good), store=_store(tmp_path))

    # apply on a NEW dataset with no pixel size → the SAME answers now compile to a BLOCKED plan
    reapplied_intent = intent_from_template(load_template('visc', store=_store(tmp_path)))
    new_plan = Planner(reg).compile(reapplied_intent, _ctx(pixel_size=0))
    assert not is_runnable(new_plan), "the gate verdict must re-evaluate on the new data, not carry over"


def test_the_template_stores_no_verdicts():
    tmpl = template_from_plan('t', AnalysisIntent(target='bead', observables=['viscosity']),
                              _plan(_reg(), 'viscosity', pixel_size=0))    # authored while BLOCKED
    # nothing in the template asserts runnability — only answers + step names
    assert not hasattr(tmpl, 'gate_report') and not hasattr(tmpl, 'is_runnable')
    assert set(vars(tmpl)) == {'name', 'observables', 'target', 'question', 'steps', 'parameters'}


# ── persistence, listing, delete/rename, corrupt-degrades ────────────────────────────────────────────

def test_templates_persist_across_sessions(tmp_path):
    save_template(GuidedTemplate('a', observables=('count',)), store=_store(tmp_path))
    save_template(GuidedTemplate('b', observables=('size',)), store=_store(tmp_path))
    # a brand-new store instance over the same file sees them (survives a session)
    fresh = UserSettings(path=tmp_path / "s.json")
    assert [t.name for t in list_templates(store=fresh)] == ['a', 'b']


def test_delete_and_rename(tmp_path):
    store = _store(tmp_path)
    save_template(GuidedTemplate('old', observables=('count',)), store=store)
    assert rename_template('old', 'new', store=store) is True
    assert load_template('old', store=store) is None and load_template('new', store=store) is not None
    # rename never overwrites an existing target
    save_template(GuidedTemplate('other', observables=('size',)), store=store)
    assert rename_template('new', 'other', store=store) is False
    assert delete_template('new', store=store) is True and delete_template('new', store=store) is False


def test_a_corrupt_template_entry_degrades_to_not_available(tmp_path):
    store = _store(tmp_path)
    save_template(GuidedTemplate('good', observables=('count',)), store=store)
    # inject a malformed entry directly into the store
    raw = store.get('navigator.templates')
    raw['broken'] = {'not': 'a template'}          # missing 'name'
    store.set('navigator.templates', raw)
    names = [t.name for t in list_templates(store=store)]
    assert 'good' in names and 'broken' not in names        # the good one survives; the corrupt is skipped
    assert load_template('broken', store=store) is None      # never a crash
