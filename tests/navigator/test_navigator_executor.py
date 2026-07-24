"""**Guided execution IS the manual/batch computation: `run_plan` drives the batch handlers, gate-respecting.**

Phase 1 of the execution-adapter layer. `run_plan` executes a compiled plan by driving the batch `_STEP_MAP`
handlers (which `test_route_equivalence` proves compute identically to the manual GUI route) in the
gate-respecting order, threading a shared `state`. The acceptance gate is here: for the one shipped adapter
(`background_removal`), **guided == batch == manual**, bit for bit. Plus the safety contract — a blocker halts
the run, a caveat runs with its reason, and a step with no adapter is REPORTED, never invoked with guessed
arguments (the whole point of an adapter layer over a generic `fn(image)`).
"""
import numpy as np
import pytest

from tests.fixtures_synthetic import synthetic_puncta_image

from pycat.navigator.executor import run_plan, has_adapter
from pycat.navigator.planner import Plan, PlanStep
from pycat.navigator.contracts import ModuleContract, AnalysisIntent, Assumption, GateStatus
from pycat.navigator.capabilities import InformationRole

pytestmark = pytest.mark.base

_BALL_RADIUS = 25


class _DataInstance:
    def __init__(self, repo=None):
        self.data_repository = dict(repo or {})

    def set_data(self, key, value):
        self.data_repository[key] = value

    def get_data(self, key, default=None):
        return self.data_repository.get(key, default)


def _raw():
    image, _labels = synthetic_puncta_image(shape=(128, 128), n_puncta=20, seed=1)
    return np.asarray(image).astype(np.float64)


def _step(name, reason=""):
    return PlanStep(module=ModuleContract(name=name, info_role=InformationRole.TRANSFORM),
                    produces=None, inputs=[], reason=reason)


def _plan(*steps, gate_report=()):
    p = Plan(intent=AnalysisIntent(target="t", observables=["x"]), steps=list(steps))
    p.gate_report = list(gate_report)
    return p


def _bg_state():
    raw = _raw()
    return raw, {"image": raw, "preprocessed": raw,
                 "data_instance": _DataInstance({"ball_radius": _BALL_RADIUS})}


# ── the acceptance gate: guided == batch == manual ───────────────────────────────────────────────────

def test_guided_equals_batch_equals_manual_for_background_removal():
    from pycat.toolbox.image_processing_tools import rb_gaussian_bg_removal_with_edge_enhancement
    from tests.route_equivalence import batch_replay
    raw = _raw()

    # manual: the interactive operation on raw counts
    manual = rb_gaussian_bg_removal_with_edge_enhancement(raw, _BALL_RADIUS).astype(np.float32)

    # batch: the recorded step through the real replay registry
    bstate = {"image": raw, "preprocessed": raw,
              "data_instance": _DataInstance({"ball_radius": _BALL_RADIUS})}
    batch_replay([{"step": "background_removal",
                   "params": {"ball_radius": _BALL_RADIUS, "active_layer": "segmentation image"}}], bstate)
    batch = np.asarray(bstate["preprocessed"]).astype(np.float32)

    # guided: run_plan drives the SAME batch handler, its params derived by the adapter
    gstate = {"image": raw, "preprocessed": raw,
              "data_instance": _DataInstance({"ball_radius": _BALL_RADIUS})}
    report = run_plan(_plan(_step("background_removal")), gstate, ctx={"ball_radius": _BALL_RADIUS})
    guided = np.asarray(gstate["preprocessed"]).astype(np.float32)

    assert [s.outcome for s in report.steps] == ["ran"]
    np.testing.assert_array_equal(guided, batch)      # guided == batch, bit for bit (rtol/atol 0)
    np.testing.assert_array_equal(guided, manual)     # == manual — one computation, three routes


# ── the safety contract ──────────────────────────────────────────────────────────────────────────────

def test_a_step_with_no_adapter_is_reported_never_invoked():
    assert not has_adapter("subcellular_segment")
    state = {}
    report = run_plan(_plan(_step("subcellular_segment")), state)
    assert [s.outcome for s in report.steps] == ["needs_panel"]
    assert not report.ran and state == {}             # nothing was invoked, no guessed arguments


def test_a_blocker_halts_the_run_and_leaves_the_state_untouched():
    a = Assumption(id="q:pixel", description="needs a calibrated pixel size",
                   check=lambda ctx: GateStatus.VIOLATED, severity="blocker")
    raw, state = _bg_state()
    before = np.asarray(state["preprocessed"]).copy()
    # background_removal HAS an adapter, but a blocker on it must stop the run before it is invoked
    report = run_plan(_plan(_step("background_removal"), _step("subcellular_segment"),
                            gate_report=[("background_removal", a, GateStatus.VIOLATED)]),
                      state, ctx={"ball_radius": _BALL_RADIUS})
    assert report.steps[0].outcome == "blocked" and "pixel size" in report.steps[0].detail
    assert all(s.outcome in ("blocked", "skipped") for s in report.steps)   # nothing ran
    assert not report.ran and report.stopped
    np.testing.assert_array_equal(np.asarray(state["preprocessed"]), before)  # state untouched


def test_a_caveat_step_runs_and_carries_its_reason():
    a = Assumption(id="q:rel", description="reliability was not assessed",
                   check=lambda ctx: GateStatus.VIOLATED, severity="warning")   # soft → runs
    raw, state = _bg_state()
    report = run_plan(_plan(_step("background_removal"),
                            gate_report=[("background_removal", a, GateStatus.VIOLATED)]),
                      state, ctx={"ball_radius": _BALL_RADIUS})
    assert report.steps[0].outcome == "ran_with_caveat" and report.steps[0].detail
    assert report.ran


def test_probes_and_normal_steps_run_before_a_no_adapter_step_is_reported():
    raw, state = _bg_state()
    report = run_plan(_plan(_step("background_removal"), _step("feature_measure")),
                      state, ctx={"ball_radius": _BALL_RADIUS})
    outcomes = [(s.name, s.outcome) for s in report.steps]
    assert outcomes == [("background_removal", "ran"), ("feature_measure", "needs_panel")]
