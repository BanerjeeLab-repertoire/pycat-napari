"""**The navigator plan tracks viewer state — re-gate (don't recompile) so loading data enables the run (navigator-UX Part 1).**

The reported bug: guided mode was inert — the plan was evaluated ONCE at compile and nothing recomputed when
the viewer changed, so the run action could never become enabled by doing the very thing it waited for. These
pin the fix headlessly: `regate` re-evaluates the SAME compiled plan against a fresh context (resolving a gate
without changing the plan structure), `context_from_session` refreshes the context from metadata (user answers
outrank it; dimensionality is never guessed from a bare plane count), and `run_blocked_reason` is never empty
while blocked and clears when the block does.
"""
import pytest
from types import SimpleNamespace

from pycat.navigator.context import AnalysisContext, Source
from pycat.navigator.session import NavigatorSession, context_from_session
from pycat.navigator.planner import regate, Planner
from pycat.navigator.contracts import AnalysisIntent, GateStatus

pytestmark = pytest.mark.base      # build_operation_registry is curated (workbook-free); no openpyxl


def _plan_for(observable, target, ctx):
    from pycat.navigator.op_catalog import build_operation_registry
    reg = build_operation_registry()
    return Planner(reg).compile(AnalysisIntent(target=target, observables=[observable]), ctx)


def test_regate_re_evaluates_a_calibration_gate_without_recompiling():
    ctx = AnalysisContext()
    plan = _plan_for("viscosity", "bead", ctx)          # viscosity needs a calibrated pixel size
    steps_before = list(plan.ordered_modules)
    # with pixel_size violated, the calibration assumption is VIOLATED (a blocker) → not executable
    ctx.set("pixel_size", 0, source=Source.METADATA)
    regate(plan, ctx)
    assert not plan.is_executable
    # set a real pixel size and re-gate the SAME plan → the blocker clears, structure unchanged
    ctx.set("pixel_size", 0.1, source=Source.METADATA)
    regate(plan, ctx)
    assert plan.ordered_modules == steps_before          # re-gated, NOT recompiled
    assert not any(status is GateStatus.VIOLATED and a.severity == "blocker"
                   for _, a, status in plan.gate_report)


def _cm(repo):
    return SimpleNamespace(active_data_class=SimpleNamespace(data_repository=repo))


def test_context_from_session_reads_metadata_facts():
    ctx = context_from_session(_cm({
        "microns_per_pixel": 0.1,
        "file_metadata": {"common": {"n_channels": 3, "n_timepoints": 200, "modality": "fluorescence"}},
    }))
    assert ctx.get("channels") == 3 and ctx.get("time_points") == 200
    assert ctx.get("pixel_size") == 0.1 and ctx.get("modality") == "fluorescence"


def test_a_user_answer_outranks_metadata():
    ctx = AnalysisContext()
    ctx.set("channels", 1, source=Source.USER)          # the user said single-channel
    context_from_session(_cm({"file_metadata": {"common": {"n_channels": 3}}}), ctx)
    assert ctx.get("channels") == 1                      # metadata does NOT overwrite the user's answer


def test_axes_are_never_guessed_from_a_bare_plane_count():
    # 3 planes, NO dimension_order → Z vs T vs C is ambiguous → 'axes' stays unknown (the engine asks)
    ctx = context_from_session(_cm({"file_metadata": {"common": {"n_z": 3}}}))
    assert not ctx.known("axes")
    # WITH an explicit dimension_order, the layout is asserted
    ctx2 = context_from_session(_cm({"file_metadata": {"common": {"n_z": 3, "dimension_order": "ZYX"}}}))
    assert "z" in (ctx2.get("axes") or [])


def test_the_sentinel_pixel_size_is_not_treated_as_calibrated():
    ctx = context_from_session(_cm({"microns_per_pixel": 1.0,
                                    "file_metadata": {"common": {"n_channels": 1}}}))
    assert not ctx.known("pixel_size")                   # 1.0 µm/px is the no-metadata sentinel, not a scale


def test_run_blocked_reason_is_present_while_blocked_and_clears_when_resolved():
    s = NavigatorSession()
    s.intent.observables = ["viscosity"]
    s.intent.target = "bead"
    s.compile_plan()
    assert "Load an image first" in s.run_blocked_reason()          # nothing loaded yet
    context_from_session(_cm({"microns_per_pixel": 0.1,
                              "file_metadata": {"common": {"n_channels": 1, "n_timepoints": 200}}}), s.ctx)
    s.regate()
    assert s.run_blocked_reason() is None                           # calibrated + loaded → runnable, no reason


def test_plan_rows_prepends_a_load_data_step_0_satisfied_once_loaded():
    """Item 2: the plan begins with a visible 'Load data' prerequisite — blocked until an image is open, then
    satisfied — while `plan_rows(plan)` without a context (existing callers) is unchanged (no step 0)."""
    from pycat.navigator.session import plan_rows
    ctx = AnalysisContext()
    plan = _plan_for("viscosity", "bead", ctx)
    without = plan_rows(plan)                              # no ctx → existing behaviour, no step 0
    unloaded = plan_rows(plan, ctx)
    assert unloaded[0].name == "Load data" and unloaded[0].state == "blocked"
    assert len(unloaded) == len(without) + 1
    ctx.set("channels", 2, source=Source.METADATA)        # an image is open
    assert plan_rows(plan, ctx)[0].state == "ok"          # step 0 flips to satisfied
