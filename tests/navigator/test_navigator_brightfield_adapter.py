"""**Phase 3, next workflow: the BRIGHTFIELD condensate chain — the one segmenter that requires preprocessing.**

Brightfield dark-blob segmentation (`segment_bf_condensates`) is meaningless on a raw image, so — unlike the
fluorescence chain, where `segment_subcellular_objects` does its own local processing — brightfield preprocessing
is MANDATORY. `bf_segment` therefore requires a `state:enhanced` product that only the new coarse `bf_preprocess`
op provides, which makes the planner AUTO-INSERT preprocessing before segmentation (the one deliberate exception
to "preprocessing is never auto-inserted", Gable's call 2026-07-27). Both steps are route-provable without torch.

What this pins:
- **The planner chains it.** A brightfield-condensate plan is `acquisition → bf_preprocess → bf_segment → …`;
  a fluorescence-condensate plan gets NO `bf_preprocess` (the modality gate keeps it out).
- **Guided == manual, bit for bit.** Driving `bf_preprocess → bf_segment` through the adapters produces the same
  `bf_condensate_mask` as directly running `preprocess_brightfield → segment_bf_condensates`.
- **The reviewed knobs drive the run.** An edited `min_diameter_px` (segmentation) and an edited `bg_kernel`
  (preprocessing) each make the guided mask equal the manual at THAT value, not the default.
- **The measurement is the brightfield per-condensate metrics.** Brightfield condensates are first-class
  labelled objects, which neither `condensate_analysis` (needs a `puncta_mask`) nor `cell_analysis` (a cell-sized
  min-area filter drops them) measures correctly — so the analysis is `bf_condensate_analysis`: per-condensate
  optical-density/area/shape via `bf_condensate_metrics` (the cell-less in-vitro path), dispatched at run time on
  the mask the segmenter produced, and proven equal to the manual call bit for bit.
"""
import numpy as np
import pandas as pd
import pytest

from pycat.navigator.executor import run_plan, resolve_batch_step, has_adapter
from pycat.navigator.parameters import build_param_review
from pycat.navigator.planner import Plan, PlanStep
from pycat.navigator.contracts import ModuleContract, AnalysisIntent
from pycat.navigator.capabilities import InformationRole

pytestmark = pytest.mark.base


class _DataInstance:
    def __init__(self, repo=None):
        self.data_repository = dict(repo or {})

    def set_data(self, key, value):
        self.data_repository[key] = value

    def get_data(self, key, default=None):
        return self.data_repository.get(key, default)


def _scene():
    """A brightfield field: DARK condensates (low value) on a bright background, plus a little noise."""
    img = np.full((80, 80), 0.8, np.float32)
    yy, xx = np.ogrid[:80, :80]
    for cy, cx in [(25, 25), (25, 55), (55, 40)]:
        img[(yy - cy) ** 2 + (xx - cx) ** 2 <= 5 ** 2] = 0.2
    img += np.random.default_rng(0).normal(0, 0.02, img.shape).astype(np.float32)
    return img.astype(np.float32)


def _manual_mask(img, *, bg_kernel=50, halo_weight=0.35,
                 min_diameter_px=3.0, max_diameter_px=50.0, min_circularity=0.5):
    """The manual/headless route: the full preprocessing composite, then dark-blob segmentation."""
    from pycat.toolbox.brightfield_tools import preprocess_brightfield, segment_bf_condensates
    enh = preprocess_brightfield(img, bg_kernel=bg_kernel, halo_weight=halo_weight,
                                 background_image=None)["enhanced"]
    return segment_bf_condensates(enh, min_diameter_px=min_diameter_px,
                                  max_diameter_px=max_diameter_px, min_circularity=min_circularity)


def _step(name, role=InformationRole.CREATE):
    return PlanStep(module=ModuleContract(name=name, info_role=role), produces=None, inputs=[], reason="")


def _bf_plan(*steps):
    return Plan(intent=AnalysisIntent(target="condensate", observables=["count", "size"]), steps=list(steps))


def _seg_state(img):
    return {"image": img.copy(), "preprocessed": img.copy(),
            "data_instance": _DataInstance({"ball_radius": 50})}


# ── the planner chains preprocessing in, only for brightfield ───────────────────────────────────────────

def test_bf_preprocess_and_bf_segment_have_adapters_and_batch_steps():
    assert has_adapter("bf_preprocess") and has_adapter("bf_segment")
    assert resolve_batch_step("bf_preprocess") == "bf_preprocess"
    assert resolve_batch_step("bf_segment") == "bf_condensate_segmentation"


def test_the_planner_auto_inserts_bf_preprocess_before_bf_segment_on_brightfield():
    from pycat.navigator.session import NavigatorSession
    from pycat.navigator.execution import execution_order
    s = NavigatorSession(); s.ctx.set("modality", "brightfield"); s.ctx.set("axes", ["y", "x"])
    s.intent = AnalysisIntent(target="condensate", observables=["count", "size"])
    names = [x.name for x in execution_order(s.planner.compile(s.intent, s.ctx, pins={}))]
    assert "bf_preprocess" in names and "bf_segment" in names
    assert names.index("bf_preprocess") < names.index("bf_segment")   # preprocessing precedes segmentation
    # and a FLUORESCENCE condensate plan gets no brightfield preprocessing
    s2 = NavigatorSession(); s2.ctx.set("modality", "fluorescence"); s2.ctx.set("axes", ["y", "x"])
    s2.intent = AnalysisIntent(target="condensate", observables=["count"])
    fnames = [x.name for x in execution_order(s2.planner.compile(s2.intent, s2.ctx, pins={}))]
    assert "bf_preprocess" not in fnames


# ── guided == manual, bit for bit ──────────────────────────────────────────────────────────────────────

def test_guided_brightfield_segmentation_equals_manual_bit_for_bit():
    img = _scene()
    state = _seg_state(img)
    report = run_plan(_bf_plan(_step("bf_preprocess", InformationRole.TRANSFORM), _step("bf_segment")), state)
    assert [s.outcome for s in report.steps] == ["ran", "ran"]
    guided = state.get("bf_condensate_mask")
    assert guided is not None and int(guided.max()) > 0        # it actually found condensates
    np.testing.assert_array_equal(guided, _manual_mask(img))   # bit for bit


# ── the reviewed knobs drive the run (both stages) ─────────────────────────────────────────────────────

def test_an_edited_min_diameter_drives_the_segmentation():
    img = _scene()
    edited = 8.0                                               # NOT the default 3.0 → fewer blobs kept
    review = build_param_review(_bf_plan(_step("bf_preprocess", InformationRole.TRANSFORM), _step("bf_segment")))
    assert [p.name for p in review.step("bf_segment").params][0] == "min_diameter_px"
    review.step("bf_segment").set("min_diameter_px", edited)
    assert review.step("bf_segment").is_modified

    state = _seg_state(img)
    run_plan(_bf_plan(_step("bf_preprocess", InformationRole.TRANSFORM), _step("bf_segment")), state,
             params_by_step=review.params_by_step(), provenance_by_step=review.provenance_by_step())
    guided = state["bf_condensate_mask"]
    np.testing.assert_array_equal(guided, _manual_mask(img, min_diameter_px=edited))   # edited value drove it
    assert not np.array_equal(guided, _manual_mask(img))                               # ≠ the default result


def test_an_edited_bg_kernel_drives_the_preprocessing():
    img = _scene()
    edited = 15                                               # NOT the default 50 → different enhanced image
    review = build_param_review(_bf_plan(_step("bf_preprocess", InformationRole.TRANSFORM), _step("bf_segment")))
    assert [p.name for p in review.step("bf_preprocess").params][0] == "bg_kernel"
    review.step("bf_preprocess").set("bg_kernel", edited)

    state = _seg_state(img)
    run_plan(_bf_plan(_step("bf_preprocess", InformationRole.TRANSFORM), _step("bf_segment")), state,
             params_by_step=review.params_by_step(), provenance_by_step=review.provenance_by_step())
    guided = state["bf_condensate_mask"]
    np.testing.assert_array_equal(guided, _manual_mask(img, bg_kernel=edited))   # the edited kernel drove preproc
    assert not np.array_equal(guided, _manual_mask(img))                         # ≠ the default result


# ── the measurement: the brightfield per-condensate metrics, guided == manual ──────────────────────────

def test_brightfield_condensate_analysis_runs_and_equals_manual():
    from pycat.toolbox.brightfield_tools import bf_condensate_metrics
    from pycat.batch.steps._common import _raw_counts
    img = _scene()
    state = _seg_state(img)
    state["data_instance"].set_data("microns_per_pixel_sq", 0.04)      # 0.2 µm/px
    report = run_plan(_bf_plan(_step("bf_preprocess", InformationRole.TRANSFORM), _step("bf_segment"),
                               _step("feature_analysis.cell_analysis", InformationRole.MEASURE)), state)
    outcomes = {s.name: s.outcome for s in report.steps}
    assert outcomes["bf_preprocess"] == "ran" and outcomes["bf_segment"] == "ran"
    assert outcomes["feature_analysis.cell_analysis"] == "ran"         # the brightfield measurement now runs
    guided = state.get("bf_condensate_df")
    assert guided is not None and len(guided) > 0                      # it measured the condensates
    # guided == manual, bit for bit: cell-less per-condensate OD metrics on the SAME mask + RAW image
    manual = bf_condensate_metrics(_raw_counts(img), np.asarray(state["bf_condensate_mask"]), None,
                                   float(np.sqrt(0.04)), bg_kernel=50)
    pd.testing.assert_frame_equal(guided, manual)


def test_condensate_analysis_dispatches_on_the_produced_mask():
    # the dispatch is on what the segmenter actually produced, resolved at run time from the threaded state
    intent = _bf_plan().intent
    assert resolve_batch_step("feature_analysis.cell_analysis", intent,
                              {"bf_condensate_mask": np.ones((4, 4), int)}) == "bf_condensate_analysis"
    assert resolve_batch_step("feature_analysis.cell_analysis", intent,
                              {"puncta_mask": np.zeros((4, 4))}) == "condensate_analysis"
    assert resolve_batch_step("feature_analysis.cell_analysis", intent) == "condensate_analysis"   # no-state default


# ── 2026-10-19: the planner names the brightfield measurement itself, and runs what the panels run ─────────
#
# Before, every brightfield plan measured with `feature_analysis.cell_analysis` (the fluorescence intensity
# analysis): the executor swapped in the OD metrics at run time, but an in-vitro 'size' plan then ran the
# FLUORESCENCE size-distribution fit (`ivf_size_distribution`) on a brightfield image, and the in-vitro panel's
# field summary never ran. Terminal selection now ranks by the workflow contexts (in vitro, brightfield).

def _plan_for(observables, **facts):
    from pycat.navigator.canonical import _ctx
    from pycat.navigator.op_catalog import build_operation_registry
    from pycat.navigator.planner import Planner
    ctx = _ctx(**facts)
    return Planner(build_operation_registry()).compile(
        AnalysisIntent(target="condensate", observables=list(observables)), ctx), ctx


@pytest.mark.parametrize("obs", ["morphology", "size", "count", "shape", "optical_density"])
def test_brightfield_plans_measure_with_the_brightfield_ops(obs):
    invitro, _ = _plan_for([obs], modality="brightfield", in_vitro=True)
    cellular, _ = _plan_for([obs], modality="brightfield")
    assert "brightfield.invitro_field_summary" in invitro.ordered_modules
    assert "brightfield.condensate_metrics" in cellular.ordered_modules
    for plan in (invitro, cellular):
        assert "feature_analysis.cell_analysis" not in plan.ordered_modules
        assert "invitro.size_distribution" not in plan.ordered_modules          # the fluorescence fit
    if obs in ("size", "count"):
        assert invitro.ordered_modules[-1] == "brightfield.invitro_size_distribution"


def test_fluorescence_plans_are_unchanged_and_an_od_plan_on_fluorescence_is_refused():
    fl, _ = _plan_for(["size"], modality="fluorescence")
    assert "feature_analysis.cell_analysis" in fl.ordered_modules
    ivf, _ = _plan_for(["size"], modality="fluorescence", in_vitro=True)
    assert ivf.ordered_modules[-1] == "invitro.size_distribution"
    od, _ = _plan_for(["optical_density"], modality="fluorescence")
    assert not od.is_executable and any("brightfield" in b for b in od.blockers())


def test_the_in_vitro_review_seeds_the_in_vitro_panel_defaults():
    plan, ctx = _plan_for(["size"], modality="brightfield", in_vitro=True)
    review = build_param_review(plan, ctx=ctx)
    pre = {p.name: p.default for p in review.step("bf_preprocess").params}
    seg = {p.name: p.default for p in review.step("bf_segment").params}
    assert pre == {"bg_kernel": 60, "halo_weight": 0.3}
    assert seg["min_diameter_px"] == 4.0 and seg["max_diameter_px"] == 200.0
    cplan, cctx = _plan_for(["size"], modality="brightfield")
    assert {p.name: p.default for p in build_param_review(cplan, ctx=cctx).step("bf_segment").params}[
        "max_diameter_px"] == 50.0


def test_a_guided_in_vitro_brightfield_run_equals_the_panel_bit_for_bit(tmp_path):
    from pycat.batch.steps._common import _raw_counts
    from pycat.toolbox.brightfield_pipeline import ivbf_field_summary, ivbf_size_and_contact
    plan, ctx = _plan_for(["size"], modality="brightfield", in_vitro=True)
    img = _scene()
    state = _seg_state(img)
    state["data_instance"].set_data("microns_per_pixel_sq", 0.04)
    review = build_param_review(plan, ctx=ctx)
    image_path = tmp_path / "droplets.tif"
    report = run_plan(plan, state, ctx=ctx, params_by_step=review.params_by_step(), image_path=image_path,
                      output_dir=tmp_path)
    outcomes = {s.name: s.outcome for s in report.steps}
    assert outcomes["brightfield.invitro_field_summary"] == "ran"
    assert outcomes["brightfield.invitro_size_distribution"] == "ran"
    mask = _manual_mask(img, bg_kernel=60, halo_weight=0.3, min_diameter_px=4.0, max_diameter_px=200.0)
    np.testing.assert_array_equal(state["bf_condensate_mask"], mask)          # the in-vitro panel's defaults
    di = state["data_instance"].data_repository
    _, _, per_drop = ivbf_field_summary(_raw_counts(img), mask, 0.2, str(image_path))
    pd.testing.assert_frame_equal(di["ivbf_droplet_df"], per_drop)
    fit, _, _ = ivbf_size_and_contact(mask, _raw_counts(img), 0.2, n_bins=30,
                                      min_area_px=int(np.pi * 2.0 ** 2), contact_label=0)
    assert di["ivbf_size_dist"].keys() == fit.keys()
    for k, v in fit.items():
        if isinstance(v, float):
            assert di["ivbf_size_dist"][k] == v or (v != v and di["ivbf_size_dist"][k] != di["ivbf_size_dist"][k])
