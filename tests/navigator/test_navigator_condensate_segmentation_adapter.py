"""**Phase 3, next workflow: condensate segmentation — the condensate branch of `segmentation_tools`.**

For a **condensate** target, `segmentation_tools` now resolves to the `condensate_segmentation` batch step,
which runs `segment_subcellular_objects` per cell (the inner loop of `run_segment_subcellular_objects`, no
viewer calls) and writes the `puncta_mask` that condensate analysis consumes. It is route-provable **without
torch** — `segment_subcellular_objects` is pure numpy/skimage — on a synthetic pre-processed image + labelled
cells.

Two guarantees:
- **Guided == manual, bit for bit.** Driving `condensate_segmentation` through the adapter produces the same
  `puncta_mask` as directly replicating the handler's computation (cell-mask stretch → global intensity stats →
  `segment_subcellular_objects` per cell) at the handler's grounded default thresholds — the same defaults the
  manual panel uses at its default settings (`params_from` returns `{}`, no guessed knob).
- **The chain contract.** The step writes `state['puncta_mask']`, which is exactly what the condensate-analysis
  adapter reads next.
"""
import numpy as np
import pandas as pd
import pytest

from pycat.navigator.executor import run_plan, resolve_batch_step
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
    """A pre-processed [0,1] image (cell-mask stretching needs that range) with three cells, two puncta each,
    the raw measure image, and the cell labels."""
    img = np.zeros((96, 96), np.float32)
    cells = np.zeros((96, 96), np.int32)
    yy, xx = np.ogrid[:96, :96]
    for i, (cy, cx) in enumerate([(30, 30), (30, 66), (66, 48)], start=1):
        cells[(yy - cy) ** 2 + (xx - cx) ** 2 <= 18 ** 2] = i
        img[(yy - cy) ** 2 + (xx - cx) ** 2 <= 18 ** 2] = 60
        for dy, dx in [(-6, -6), (6, 6)]:
            img[(yy - (cy + dy)) ** 2 + (xx - (cx + dx)) ** 2 <= 3 ** 2] = 255
    img += np.random.default_rng(0).normal(0, 1.5, img.shape).astype(np.float32)
    img = np.clip(img, 0, None).astype(np.float32)
    pre = ((img - img.min()) / (img.max() - img.min())).astype(np.float32)
    return img, pre, cells


def _manual_puncta_mask(pre, original, cells, ball_radius):
    """Replicate the batch handler's computation directly: cell-mask stretch, global intensity stats, then
    segment_subcellular_objects per cell at the default thresholds — the manual/headless route."""
    from pycat.toolbox.segmentation_tools import segment_subcellular_objects, cell_mask_stretching
    from pycat.toolbox.segmentation.intensity import compute_image_intensity_stats
    CMS = cell_mask_stretching(pre, cells)
    stats = compute_image_intensity_stats(original, cells, smooth_sigma=max(0.5, 2 / 2.0))
    refined = np.zeros_like(cells, dtype=bool)
    for label in np.unique(cells)[1:]:
        cell_mask = (cells == label).astype(bool)
        r, _u = segment_subcellular_objects(original.copy(), CMS.copy(), cell_mask, label, ball_radius,
                                            pd.DataFrame(), image_stats=stats)
        refined |= r
    return refined


def _step(name):
    return PlanStep(module=ModuleContract(name=name, info_role=InformationRole.CREATE),
                    produces=None, inputs=[], reason="")


def _plan(*steps):
    return Plan(intent=AnalysisIntent(target="condensate", observables=["count"]), steps=list(steps))


def test_segmentation_resolves_to_condensate_segmentation_for_a_condensate_target():
    assert resolve_batch_step("segmentation_tools",
                              AnalysisIntent(target="condensate", observables=["x"])) == "condensate_segmentation"
    assert resolve_batch_step("segmentation_tools",
                              AnalysisIntent(target="cell", observables=["x"])) == "cellpose_segmentation"


def test_guided_condensate_segmentation_equals_manual_bit_for_bit():
    img, pre, cells = _scene()
    manual = _manual_puncta_mask(pre, img, cells, ball_radius=30)

    di = _DataInstance({"ball_radius": 30})
    state = {"image": img.copy(), "preprocessed": pre.copy(),
             "labeled_cells": cells.copy(), "data_instance": di}
    report = run_plan(_plan(_step("segmentation_tools")), state)

    assert [s.outcome for s in report.steps] == ["ran"]
    guided = state.get("puncta_mask")
    assert guided is not None
    assert int(guided.sum()) > 0                       # it actually found puncta on this scene
    np.testing.assert_array_equal(guided, manual)      # bit for bit


def test_condensate_segmentation_writes_the_puncta_mask_the_analysis_step_reads():
    img, pre, cells = _scene()
    di = _DataInstance({"ball_radius": 30})
    state = {"image": img.copy(), "preprocessed": pre.copy(),
             "labeled_cells": cells.copy(), "data_instance": di}
    report = run_plan(_plan(_step("segmentation_tools")), state)
    assert report.steps[0].outcome == "ran"
    # the exact key condensate_analysis consumes
    assert "puncta_mask" in state and state["puncta_mask"].shape == cells.shape


def test_condensate_segmentation_is_skipped_when_no_cells_were_segmented():
    img, pre, cells = _scene()
    di = _DataInstance({"ball_radius": 30})
    state = {"image": img.copy(), "preprocessed": pre.copy(), "no_cells": True, "data_instance": di}
    report = run_plan(_plan(_step("segmentation_tools")), state)
    assert report.steps[0].outcome == "ran"            # the handler returns cleanly on the no-cells signal
    assert "puncta_mask" not in state                  # and produces nothing
