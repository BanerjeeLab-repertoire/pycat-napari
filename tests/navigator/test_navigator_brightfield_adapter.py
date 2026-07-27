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
