"""**Dynamic-spatial adapter (spec N2b-3): trajectory linking + merge/fission run from a navigator plan.**

The `dynamic_spatial` batch step was a headless skip-stub. This builds `replay_dynamic_spatial` (self-contained:
`extract_frame_properties` -> `link_trajectories` for motion and `detect_merge_fission` for fusion, straight from
a segmented (T,H,W) label stack) and wires BOTH dynamic-spatial ops to it. This pins: both ops resolve to the one
real step; a guided run equals the manual tool calls column for column; a plan holding both ops only tracks once
(the `_dynamic_spatial_done` guard); and with no 3-D stack the handler refuses cleanly (no fabricated numbers).
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


def _moving_stack(T=5, H=48, W=48):
    """A stack with one steadily moving object and one that appears midway — enough to link a track."""
    stack = np.zeros((T, H, W), np.int32)
    for t in range(T):
        cy = 8 + t * 3
        stack[t, cy - 2:cy + 2, 8:12] = 1
        if t >= 2:
            stack[t, 30:34, 30:34] = 2
    return stack


def _state(mpx=0.1):
    return {'image': np.zeros((5, 48, 48), np.float32),
            'puncta_mask': _moving_stack(),
            'data_instance': _DI({'microns_per_pixel_sq': mpx ** 2})}


def _step(name, role=InformationRole.CREATE):
    return PlanStep(module=ModuleContract(name=name, info_role=role), produces=None, inputs=[], reason="")


def _plan(*steps):
    return Plan(intent=AnalysisIntent(target="condensate", observables=["motion", "fusion"]), steps=list(steps))


def test_both_dynamic_spatial_ops_resolve_to_the_one_real_step():
    assert has_adapter("dynamic_spatial.link_trajectories")
    assert has_adapter("dynamic_spatial.detect_merge_fission")
    assert resolve_batch_step("dynamic_spatial.link_trajectories") == "dynamic_spatial"
    assert resolve_batch_step("dynamic_spatial.detect_merge_fission") == "dynamic_spatial"


def test_guided_dynamic_spatial_equals_the_manual_tool_calls(tmp_path):
    from pycat.batch.steps.analysis_steps import replay_dynamic_spatial

    state = _state()
    report = run_plan(_plan(_step("dynamic_spatial.link_trajectories")), state, params_by_step={})
    assert [s.outcome for s in report.steps] == ["ran"]
    guided = state.get('dynamic_spatial_tracks_df')
    assert guided is not None and len(guided) > 0

    state2 = _state()
    replay_dynamic_spatial(state2, tmp_path / 'm.tif', {}, tmp_path)
    manual = state2['dynamic_spatial_tracks_df']
    assert list(guided.columns) == list(manual.columns)
    np.testing.assert_array_equal(guided.values, manual.values)
    assert (tmp_path / 'm_dynamic_spatial_tracks.csv').exists()
    assert (tmp_path / 'm_dynamic_spatial_events.csv').exists()


def test_a_plan_with_both_ops_only_tracks_once():
    state = _state()
    report = run_plan(_plan(_step("dynamic_spatial.link_trajectories"),
                            _step("dynamic_spatial.detect_merge_fission", InformationRole.INTERPRET)),
                      state, params_by_step={})
    # both steps run, but the second short-circuits on the guard rather than re-tracking
    assert [s.outcome for s in report.steps] == ["ran", "ran"]
    assert state['_dynamic_spatial_done'] is True
    assert state.get('dynamic_spatial_tracks_df') is not None


def test_no_time_series_stack_refuses_cleanly(tmp_path):
    from pycat.batch.steps.analysis_steps import replay_dynamic_spatial
    # a 2-D mask is not a time series — the handler must skip, not fabricate a single-frame "trajectory"
    state = {'image': np.zeros((48, 48), np.float32),
             'puncta_mask': np.zeros((48, 48), np.int32),
             'data_instance': _DI({'microns_per_pixel_sq': 0.01})}
    replay_dynamic_spatial(state, tmp_path / 'm.tif', {}, tmp_path)
    assert state.get('dynamic_spatial_tracks_df') is None
    assert not (tmp_path / 'm_dynamic_spatial_tracks.csv').exists()
