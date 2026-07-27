"""**Phase 3, next workflow: cell analysis — the step that measures what segmentation produced.**

After single-frame cell segmentation (`segmentation_tools` → `cellpose_segmentation`) the real cell chain
measures the mask: `feature_analysis_tools` for a cell → the `cell_analysis` batch step. Both the batch route
and a direct call reduce to the same `cell_analysis_func`, so this is route-provable **without torch** — on a
synthetic labelled mask (cell analysis operates on any integer label image, not only a Cellpose result).

Two guarantees:
- **Guided == manual, bit for bit.** Driving `cell_analysis` through the adapter measures the same per-cell
  table as a direct call on the same mask + image + session measurements.
- **The chain contract.** `cell_analysis` reads the ``cellpose_mask`` segmentation writes into the shared state;
  with no upstream mask it is reported as an error (segmentation must run first), never a silent empty result.
Condensate feature analysis stays reported (its `condensate_analysis` route depends on the unproven
condensate-segmentation route) — never guessed.
"""
import numpy as np
import pytest

from pycat.navigator.executor import run_plan, resolve_batch_step
from pycat.navigator.planner import Plan, PlanStep
from pycat.navigator.contracts import ModuleContract, AnalysisIntent
from pycat.navigator.capabilities import InformationRole

pytestmark = pytest.mark.base

_COLS = ["area", "intensity_mean", "intensity_total", "eccentricity", "perimeter",
         "intensity_median", "intensity_std_dev"]


class _DataInstance:
    def __init__(self, repo=None):
        self.data_repository = dict(repo or {})

    def set_data(self, key, value):
        self.data_repository[key] = value

    def get_data(self, key, default=None):
        return self.data_repository.get(key, default)


def _repo():
    # the session measurements cell_analysis_func reads (the same values both routes see)
    return {"cell_diameter": 40, "object_size": 5, "ball_radius": 50, "microns_per_pixel_sq": 0.01}


def _image_and_mask():
    """A deterministic image + labelled mask (three filled disks) — cell analysis needs real, sizeable cells."""
    img = np.zeros((256, 256), np.float32)
    mask = np.zeros((256, 256), np.int32)
    yy, xx = np.ogrid[:256, :256]
    for i, (cy, cx) in enumerate([(60, 60), (60, 190), (190, 120)], start=1):
        disk = (yy - cy) ** 2 + (xx - cx) ** 2 <= 28 ** 2
        mask[disk] = i
        img[disk] = 100 + i * 40
    img += np.random.default_rng(0).normal(0, 3, img.shape).astype(np.float32)
    return img, mask


def _step(name):
    return PlanStep(module=ModuleContract(name=name, info_role=InformationRole.MEASURE),
                    produces=None, inputs=[], reason="")


def _plan(*steps, target="cell"):
    return Plan(intent=AnalysisIntent(target=target, observables=["count"]), steps=list(steps))


def test_feature_analysis_resolves_by_target_cell_vs_condensate():
    assert resolve_batch_step("feature_analysis_tools",
                              AnalysisIntent(target="cell", observables=["x"])) == "cell_analysis"
    # condensate is now adapted too (route-proven in test_navigator_condensate_analysis_adapter)
    assert resolve_batch_step("feature_analysis_tools",
                              AnalysisIntent(target="condensate", observables=["x"])) == "condensate_analysis"
    # a target with no measurement route is still reported, never guessed
    assert resolve_batch_step("feature_analysis_tools",
                              AnalysisIntent(target="fibril", observables=["x"])) is None


def test_guided_cell_analysis_equals_manual_bit_for_bit():
    from pycat.toolbox.feature_analysis_tools import cell_analysis_func
    img, mask = _image_and_mask()

    # manual/headless: the direct call
    manual_df = cell_analysis_func(img, mask, None, _DataInstance(_repo()))[1]

    # guided: run_plan drives cell_analysis, reading the mask from the shared state
    di = _DataInstance(_repo())
    state = {"image": img, "cellpose_mask": mask, "data_instance": di}
    report = run_plan(_plan(_step("feature_analysis_tools"), target="cell"), state)
    guided_df = di.data_repository["cell_df"]

    assert [s.outcome for s in report.steps] == ["ran"]
    assert len(guided_df) == len(manual_df) == 3
    for col in _COLS:
        np.testing.assert_array_equal(np.asarray(guided_df[col].values, dtype=np.float64),
                                      np.asarray(manual_df[col].values, dtype=np.float64))


def test_cell_analysis_consumes_the_upstream_segmentation_mask():
    # the chain contract: cell_analysis measures the SAME state key ('cellpose_mask') segmentation writes
    img, mask = _image_and_mask()
    di = _DataInstance(_repo())
    state = {"image": img, "cellpose_mask": mask, "data_instance": di}
    report = run_plan(_plan(_step("feature_analysis_tools"), target="cell"), state)
    assert report.steps[0].outcome == "ran" and "cell_df" in di.data_repository


def test_cell_analysis_without_an_upstream_mask_is_reported_as_error_not_a_silent_empty():
    img, _mask = _image_and_mask()
    di = _DataInstance(_repo())
    state = {"image": img, "data_instance": di}          # NO cellpose_mask — segmentation did not run
    report = run_plan(_plan(_step("feature_analysis_tools"), target="cell"), state)
    assert report.steps[0].outcome == "error" and report.stopped
    assert "cell_df" not in di.data_repository            # nothing measured on a missing mask
