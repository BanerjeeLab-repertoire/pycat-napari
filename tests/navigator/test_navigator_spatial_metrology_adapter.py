"""**Spatial metrology adapter (spec N2b-2): per-cell Ripley/NN/radial runs from a navigator plan.**

The `spatial_metrology.ripley` MEASURE op now has an `ExecAdapter` → the `spatial_metrology` batch handler
(`replay_spatial_metrology`), so a spatial-organisation plan computes instead of printing the old headless
skip-stub. The handler wraps the shared `run_all_spatial_metrics`, run PER CELL on the segmented objects'
centroids (never whole-frame). This pins: the adapter resolves; a guided run equals the manual handler; and the
metrics are keyed per real cell ROI.
"""
import numpy as np
import pytest

from pycat.navigator.executor import run_plan, resolve_batch_step, has_adapter
from pycat.navigator.planner import Plan, PlanStep
from pycat.navigator.contracts import ModuleContract, AnalysisIntent
from pycat.navigator.capabilities import InformationRole

pytestmark = pytest.mark.base


class _DI:
    def __init__(self, repo=None):
        self.data_repository = dict(repo or {})

    def set_data(self, k, v):
        self.data_repository[k] = v

    def get_data(self, k, d=None):
        return self.data_repository.get(k, d)


def _two_cells_with_puncta(H=128, W=128):
    """Two side-by-side rectangular cells, each holding several labelled puncta."""
    cells = np.zeros((H, W), np.int32)
    cells[10:118, 8:60] = 1            # left cell
    cells[10:118, 68:120] = 2          # right cell
    puncta = np.zeros((H, W), np.int32)
    lbl = 1
    # a clustered set in cell 1, a spread set in cell 2 — enough points for Ripley/NN in each
    for (cy, cx) in [(30, 20), (34, 24), (38, 22), (42, 26), (46, 20)]:
        puncta[cy - 1:cy + 2, cx - 1:cx + 2] = lbl; lbl += 1
    for (cy, cx) in [(20, 80), (50, 100), (80, 78), (100, 110), (40, 90), (70, 95)]:
        puncta[cy - 1:cy + 2, cx - 1:cx + 2] = lbl; lbl += 1
    return puncta, cells


def _state(mpx=0.1):
    puncta, cells = _two_cells_with_puncta()
    return {'image': np.zeros_like(cells, dtype=np.float32),
            'puncta_mask': puncta, 'labeled_cells': cells,
            'data_instance': _DI({'microns_per_pixel_sq': mpx ** 2})}


def _step(name):
    return PlanStep(module=ModuleContract(name=name, info_role=InformationRole.MEASURE),
                    produces=None, inputs=[], reason="")


def _plan(*steps):
    return Plan(intent=AnalysisIntent(target="condensate", observables=["spatial_organization"]),
                steps=list(steps))


def test_the_spatial_metrology_op_has_an_adapter_targeting_a_real_step():
    assert has_adapter("spatial_metrology.ripley")
    assert resolve_batch_step("spatial_metrology.ripley") == "spatial_metrology"


def test_guided_spatial_metrology_equals_the_manual_handler(tmp_path):
    from pycat.batch.steps.analysis_steps import replay_spatial_metrology

    state = _state()
    report = run_plan(_plan(_step("spatial_metrology.ripley")), state, params_by_step={})
    assert [s.outcome for s in report.steps] == ["ran"]
    guided = state.get('spatial_metrology_df')
    assert guided is not None
    # one row per cell that had >= 2 objects (both cells here)
    assert sorted(guided['cell_label'].tolist()) == [1, 2]

    state2 = _state()
    replay_spatial_metrology(state2, tmp_path / 'm.tif', {}, tmp_path)
    manual = state2['spatial_metrology_df']
    # guided == manual, column for column
    assert list(guided.columns) == list(manual.columns)
    np.testing.assert_array_equal(guided.values, manual.values)


def test_metrics_are_keyed_per_cell_not_whole_frame(tmp_path):
    from pycat.batch.steps.analysis_steps import replay_spatial_metrology
    state = _state()
    replay_spatial_metrology(state, tmp_path / 'm.tif', {}, tmp_path)
    df = state['spatial_metrology_df']
    # two distinct cells → two rows; a whole-frame collapse would give one
    assert len(df) == 2
    assert (tmp_path / 'm_spatial_metrology.csv').exists()
