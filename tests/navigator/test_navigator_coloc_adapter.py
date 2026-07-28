"""**Phase 3, next workflow: two-channel COLOCALIZATION — correct because it runs WITHIN a segmentation ROI.**

Whole-frame Pearson measures the cell shape both channels share (r≈0.99 even for independent channels), not
colocalisation — `coloc/metrics.py` carries that warning. The fix is to restrict the correlation to a
segmentation ROI. So the `pixel_wise_corr.pearson_manders` op now REQUIRES an `instance_labels` mask (was a bare
intensity field), which makes the planner chain a segmenter before it; the adapter's handler runs Pearson +
Manders overlap / k1 / k2 (the raw, threshold-free `coloc/metrics` measures) over the union of the segmented
objects — never the whole frame.

What this pins:
- **The planner chains a segmenter for the ROI.** A two-channel condensate coloc plan is
  `acquisition → subcellular_segment → pixel_wise_corr.pearson_manders`.
- **Guided == manual, within the ROI.** The adapter's coefficients equal the manual `coloc/metrics` calls on the
  same two channels restricted to the same ROI mask, bit for bit.
- **It is NOT whole-frame.** The guided (within-ROI) Pearson differs from the whole-frame Pearson — proving the
  ROI restriction is actually applied (the whole point).
"""
import numpy as np
import pytest

from pycat.navigator.executor import run_plan, resolve_batch_step, has_adapter
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


def _two_channels():
    """Two partially-correlated channels + a segmentation mask (objects inside a central region)."""
    rng = np.random.default_rng(3)
    ch1 = rng.random((60, 60)).astype(np.float64)
    ch2 = (ch1 * 0.6 + rng.random((60, 60)) * 0.4).astype(np.float64)
    yy, xx = np.ogrid[:60, :60]
    region = ((yy - 30) ** 2 + (xx - 30) ** 2) < 20 ** 2
    mask = (region & (ch1 > 0.5)).astype(np.int32)          # segmented objects (the ROI)
    return ch1, ch2, mask


def _step(name, role=InformationRole.MEASURE):
    return PlanStep(module=ModuleContract(name=name, info_role=role), produces=None, inputs=[], reason="")


def _plan(*steps):
    return Plan(intent=AnalysisIntent(target="condensate", observables=["colocalization"]), steps=list(steps))


def _state(ch1, ch2, mask):
    return {"image": ch1.copy(), "preprocessed": ch1.copy(), "puncta_mask": mask,
            "channels_by_name": {"client": ch2.copy()}, "data_instance": _DataInstance()}


def test_coloc_op_requires_a_mask_so_the_planner_chains_a_segmenter():
    from pycat.navigator.session import NavigatorSession
    from pycat.navigator.execution import execution_order
    s = NavigatorSession(); s.ctx.set("modality", "fluorescence"); s.ctx.set("channels", 2); s.ctx.set("axes", ["y", "x"])
    s.intent = AnalysisIntent(target="condensate", observables=["colocalization"])
    names = [x.name for x in execution_order(s.planner.compile(s.intent, s.ctx, pins={}))]
    assert "pixel_wise_corr.pearson_manders" in names
    assert "subcellular_segment" in names                                        # a segmenter for the ROI
    assert names.index("subcellular_segment") < names.index("pixel_wise_corr.pearson_manders")


def test_coloc_has_an_adapter_and_batch_step():
    assert has_adapter("pixel_wise_corr.pearson_manders")
    assert resolve_batch_step("pixel_wise_corr.pearson_manders") == "pixel_colocalization"


def test_guided_coloc_equals_manual_within_the_roi():
    from pycat.toolbox.coloc.metrics import (
        pearsons_correlation, manders_overlap, manders_k1_calculation, manders_k2_calculation)
    ch1, ch2, mask = _two_channels()
    state = _state(ch1, ch2, mask)
    report = run_plan(_plan(_step("pixel_wise_corr.pearson_manders")), state)
    assert [s.outcome for s in report.steps] == ["ran"]
    g = state["coloc_df"].to_dict("records")[0]

    roi = mask > 0
    assert g["pearson_r"] == pearsons_correlation(ch1, ch2, roi)[0]
    assert g["manders_overlap"] == manders_overlap(ch1, ch2, roi)[0]
    assert g["manders_k1"] == manders_k1_calculation(ch1, ch2, roi)[0]
    assert g["manders_k2"] == manders_k2_calculation(ch1, ch2, roi)[0]


def test_coloc_is_restricted_to_the_roi_not_whole_frame():
    from pycat.toolbox.coloc.metrics import pearsons_correlation
    ch1, ch2, mask = _two_channels()
    state = _state(ch1, ch2, mask)
    run_plan(_plan(_step("pixel_wise_corr.pearson_manders")), state)
    guided_r = state["coloc_df"]["pearson_r"][0]
    whole_frame_r = pearsons_correlation(ch1, ch2, np.ones_like(mask, dtype=bool))[0]
    assert guided_r != whole_frame_r          # the ROI restriction is applied — not the whole frame
