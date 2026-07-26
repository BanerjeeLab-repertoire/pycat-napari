"""**Phase 4: a guided run is cancellable at a step boundary and reports monotonic progress.**

`run_plan` (the execution-adapter executor) gained two Qt-free hooks so the dock can drive a determinate
progress bar and a cancel button without a second execution engine:

- ``should_cancel()`` is checked at **each step boundary, before the step runs**. The first truthy check
  records the current step ``'cancelled'`` and stops — it, and everything after it, never runs, so no step
  ever computes on a cancelled/stale state (the same stop discipline as a blocker).
- ``on_progress(done, total)`` fires once per disposed step, **monotonically** 1..N, so the bar is
  determinate.

These are the acceptance tests for that contract. The science handlers are not needed here — a plan of
no-adapter steps exercises the ordering/cancel/progress logic without touching the batch route, and one
real-adapter case proves a cancelled step's computation genuinely did not run.
"""
import numpy as np
import pytest

from tests.fixtures_synthetic import synthetic_puncta_image

from pycat.navigator.executor import run_plan
from pycat.navigator.planner import Plan, PlanStep
from pycat.navigator.contracts import ModuleContract, AnalysisIntent
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


def _counting_cancel(fire_on):
    """A ``should_cancel`` that returns True on its ``fire_on``-th call (1-based), False before."""
    calls = {"n": 0}

    def _should():
        calls["n"] += 1
        return calls["n"] >= fire_on

    return _should


# ── cancellation stops the run at a step boundary ─────────────────────────────────────────────────────

def test_cancel_before_the_first_step_runs_nothing_and_leaves_state_untouched():
    """A cancel that fires at the very first boundary must run NO step — proven on a real adapter whose
    computation would otherwise mutate the state (``preprocessed``)."""
    raw = _raw()
    state = {"image": raw, "preprocessed": raw,
             "data_instance": _DataInstance({"ball_radius": _BALL_RADIUS})}
    before = np.asarray(state["preprocessed"]).copy()

    report = run_plan(_plan(_step("image_processing_tools"), _step("feature_measure")),
                      state, ctx={"ball_radius": _BALL_RADIUS},
                      should_cancel=_counting_cancel(1))

    assert report.cancelled
    assert [s.outcome for s in report.steps] == ["cancelled", "skipped"]
    assert not report.ran
    np.testing.assert_array_equal(np.asarray(state["preprocessed"]), before)   # the handler never ran


def test_cancel_after_one_step_stops_at_the_next_boundary():
    """Cancel fires at the SECOND boundary: the first step ran, the second is cancelled, the rest skipped —
    nothing after the boundary runs."""
    raw = _raw()
    state = {"image": raw, "preprocessed": raw,
             "data_instance": _DataInstance({"ball_radius": _BALL_RADIUS})}

    report = run_plan(_plan(_step("image_processing_tools"),      # runs (adapter)
                            _step("image_processing_tools"),      # cancelled at its boundary
                            _step("feature_measure")),            # skipped after the cancel
                      state, ctx={"ball_radius": _BALL_RADIUS},
                      should_cancel=_counting_cancel(2))

    assert [s.outcome for s in report.steps] == ["ran", "cancelled", "skipped"]
    assert len(report.ran) == 1 and report.cancelled


def test_no_cancel_runs_the_whole_plan():
    """A ``should_cancel`` that never fires leaves the run untouched (no cancelled/short-circuit)."""
    raw = _raw()
    state = {"image": raw, "preprocessed": raw,
             "data_instance": _DataInstance({"ball_radius": _BALL_RADIUS})}
    report = run_plan(_plan(_step("image_processing_tools"), _step("feature_measure")),
                      state, ctx={"ball_radius": _BALL_RADIUS},
                      should_cancel=lambda: False)
    assert not report.cancelled
    assert [s.outcome for s in report.steps] == ["ran", "needs_panel"]


# ── progress is monotonic ─────────────────────────────────────────────────────────────────────────────

def test_progress_is_monotonic_and_reaches_total():
    raw = _raw()
    state = {"image": raw, "preprocessed": raw,
             "data_instance": _DataInstance({"ball_radius": _BALL_RADIUS})}
    ticks = []
    report = run_plan(_plan(_step("image_processing_tools"), _step("feature_measure"),
                            _step("another_panel_step")),
                      state, ctx={"ball_radius": _BALL_RADIUS},
                      on_progress=lambda done, total: ticks.append((done, total)))

    total = len(report.steps)
    assert total == 3
    assert [d for d, _ in ticks] == [1, 2, 3]                 # strictly monotonic 1..N
    assert all(t == total for _, t in ticks)                  # total is constant
    assert ticks[-1] == (total, total)                        # reaches 100%


def test_progress_is_monotonic_even_when_cancelled():
    """Every step is still disposed (cancelled/skipped go through the same progress tick), so the bar stays
    monotonic and completes rather than freezing mid-plan."""
    raw = _raw()
    state = {"image": raw, "preprocessed": raw,
             "data_instance": _DataInstance({"ball_radius": _BALL_RADIUS})}
    ticks = []
    run_plan(_plan(_step("image_processing_tools"), _step("feature_measure"),
                   _step("another_panel_step")),
             state, ctx={"ball_radius": _BALL_RADIUS},
             should_cancel=_counting_cancel(2),
             on_progress=lambda done, total: ticks.append(done))

    assert ticks == [1, 2, 3]                                 # monotonic, still reaches total
