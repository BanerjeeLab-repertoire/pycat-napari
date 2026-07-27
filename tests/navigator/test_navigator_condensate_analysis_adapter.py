"""**Phase 3, next workflow: condensate feature analysis — the condensate branch of `feature_analysis_tools`.**

After condensate segmentation writes a ``puncta_mask``, the condensate chain measures it:
``feature_analysis_tools`` for a **condensate** target → the ``condensate_analysis`` batch step. Both the batch
route and a direct call reduce to the same ``puncta_analysis_func``, so this is route-provable **without torch**
— on a synthetic puncta mask + labelled cells + a seeded per-cell ``cell_df`` (the upstream cell-analysis table
that ``puncta_analysis_func`` folds its puncta stats into).

Two guarantees:
- **Guided == manual, bit for bit.** Driving ``condensate_analysis`` through the adapter measures the same
  per-punctum table as a direct ``puncta_analysis_func`` call on the same mask + image + session state.
- **The chain contract.** ``condensate_analysis`` reads the ``puncta_mask`` that condensate segmentation writes
  into the shared state; with no upstream mask it is reported as an **error** (segmentation must run first),
  never a silent empty result.
"""
import numpy as np
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


def _repo():
    return {"cell_diameter": 20, "object_size": 5, "ball_radius": 30, "microns_per_pixel_sq": 0.01}


def _scene():
    """A deterministic image with three cells, each holding one bright punctum, plus the puncta mask."""
    img = np.zeros((128, 128), np.float32)
    cells = np.zeros((128, 128), np.int32)
    puncta_mask = np.zeros((128, 128), bool)
    yy, xx = np.ogrid[:128, :128]
    for i, (cy, cx) in enumerate([(40, 40), (40, 90), (90, 64)], start=1):
        cell = (yy - cy) ** 2 + (xx - cx) ** 2 <= 22 ** 2
        cells[cell] = i
        img[cell] = 80
        pun = (yy - cy) ** 2 + (xx - cx) ** 2 <= 5 ** 2
        puncta_mask[pun] = True
        img[pun] = 250
    img += np.random.default_rng(0).normal(0, 2, img.shape).astype(np.float32)
    return img, cells, puncta_mask


def _seed_cell_df(img, cells):
    """The per-cell table cell analysis produces upstream; condensate analysis folds puncta stats into it."""
    from pycat.toolbox.feature_analysis_tools import cell_analysis_func
    return cell_analysis_func(img.copy(), cells.copy(), None, _DataInstance(_repo()))[1]


def _step(name):
    return PlanStep(module=ModuleContract(name=name, info_role=InformationRole.MEASURE),
                    produces=None, inputs=[], reason="")


def _plan(*steps):
    return Plan(intent=AnalysisIntent(target="condensate", observables=["count"]), steps=list(steps))


def test_feature_analysis_resolves_to_condensate_analysis_for_a_condensate_target():
    assert resolve_batch_step("feature_analysis_tools",
                              AnalysisIntent(target="condensate", observables=["x"])) == "condensate_analysis"
    assert resolve_batch_step("feature_analysis_tools",
                              AnalysisIntent(target="cell", observables=["x"])) == "cell_analysis"


def test_guided_condensate_analysis_equals_manual_bit_for_bit():
    from pycat.toolbox.feature_analysis_tools import puncta_analysis_func
    img, cells, puncta_mask = _scene()
    base_cell_df = _seed_cell_df(img, cells)

    # manual/headless: the direct call, on a fresh copy of the seeded cell table
    di_m = _DataInstance(_repo())
    di_m.data_repository["cell_df"] = base_cell_df.copy()
    puncta_analysis_func(puncta_mask.copy(), img.copy(), cells.copy(), di_m)
    manual = di_m.data_repository["puncta_df"]

    # guided: run_plan drives condensate_analysis, reading puncta_mask + labeled_cells from shared state
    di_g = _DataInstance(_repo())
    di_g.data_repository["cell_df"] = base_cell_df.copy()
    state = {"image": img.copy(), "puncta_mask": puncta_mask.copy(),
             "labeled_cells": cells.copy(), "data_instance": di_g}
    report = run_plan(_plan(_step("feature_analysis_tools")), state)
    guided = di_g.data_repository["puncta_df"]

    assert [s.outcome for s in report.steps] == ["ran"]
    assert len(guided) == len(manual) == 3
    numeric = [c for c in manual.columns if manual[c].dtype.kind in "fiu"]
    assert numeric, "puncta_df has no numeric columns to compare"
    for col in numeric:
        # assert_array_equal treats NaN in the same position as equal — circularity is legitimately NaN on
        # these tiny synthetic puncta, and must match NaN-for-NaN between the routes.
        np.testing.assert_array_equal(np.asarray(guided[col].values, dtype=np.float64),
                                      np.asarray(manual[col].values, dtype=np.float64))


def test_condensate_analysis_consumes_the_upstream_puncta_mask():
    img, cells, puncta_mask = _scene()
    di = _DataInstance(_repo())
    di.data_repository["cell_df"] = _seed_cell_df(img, cells).copy()
    state = {"image": img.copy(), "puncta_mask": puncta_mask.copy(),
             "labeled_cells": cells.copy(), "data_instance": di}
    report = run_plan(_plan(_step("feature_analysis_tools")), state)
    assert report.steps[0].outcome == "ran" and "puncta_df" in di.data_repository


def test_condensate_analysis_without_a_puncta_mask_is_reported_as_error_not_a_silent_empty():
    img, cells, _puncta = _scene()
    di = _DataInstance(_repo())
    di.data_repository["cell_df"] = _seed_cell_df(img, cells).copy()
    state = {"image": img.copy(), "labeled_cells": cells.copy(), "data_instance": di}   # NO puncta_mask
    report = run_plan(_plan(_step("feature_analysis_tools")), state)
    assert report.steps[0].outcome == "error" and report.stopped
    assert "puncta_df" not in di.data_repository
