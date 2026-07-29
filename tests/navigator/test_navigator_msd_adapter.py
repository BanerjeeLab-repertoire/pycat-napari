"""**MSD / condensate-diffusion adapter (spec N2b-4): the stack-level branch of the msd_analysis decision.**

`msd_analysis` was a headless skip-stub documented "time-series; not a per-image batch step". But VPT proved a
whole-stack handler is feasible in the batch loop, and `dynamic_spatial` now writes a trajectory table — exactly
`compute_msd`'s contract — so this BUILDS the handler rather than leaving the stub. `replay_msd_analysis` reads
the linked trajectories, computes the ensemble MSD and fits anomalous diffusion. Both diffusion ops resolve to it.
This pins: the adapters resolve; a guided run's D equals the manual `compute_msd`+`fit` calls; a both-ops plan
fits once (guard); and the scale gate refuses a pixel^2/frame "D" through the adapter path.
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


def _repo(mpx=0.1, dt=0.1, real=True):
    repo = {'microns_per_pixel_sq': (mpx ** 2 if real else 1.0)}
    if real:
        repo['pixel_size_confirmed'] = True
    repo['file_metadata'] = {'common': {'frame_interval_s': dt}}
    return repo


def _tracks(n=12, T=60, D_true=0.05, dt=0.1, seed=0):
    """A pandas trajectory table (track_id / frame / y_um / x_um) of Brownian walkers — compute_msd's contract."""
    import pandas as pd
    rng = np.random.default_rng(seed)
    step = np.sqrt(2 * D_true * dt)
    rows = []
    for tid in range(n):
        pos = np.zeros(2)
        for f in range(T):
            rows.append({'track_id': tid, 'frame': f, 'y_um': pos[0], 'x_um': pos[1]})
            pos = pos + rng.normal(0, step, 2)
    return pd.DataFrame(rows)


def _state(real=True):
    return {'image': np.zeros((4, 4), np.float32),
            'dynamic_spatial_tracks_df': _tracks(),
            'data_instance': _DI(_repo(real=real))}


def _step(name):
    return PlanStep(module=ModuleContract(name=name, info_role=InformationRole.INTERPRET),
                    produces=None, inputs=[], reason="")


def _plan(*steps):
    return Plan(intent=AnalysisIntent(target="condensate", observables=["diffusion"]), steps=list(steps))


_PARAMS = {"min_track_length": 20}


def test_both_diffusion_ops_resolve_to_the_msd_step():
    assert has_adapter("condensate_physics.compute_msd")
    assert has_adapter("condensate_physics.fit_anomalous_diffusion")
    assert resolve_batch_step("condensate_physics.compute_msd") == "msd_analysis"
    assert resolve_batch_step("condensate_physics.fit_anomalous_diffusion") == "msd_analysis"


def test_guided_diffusion_equals_the_manual_msd_fit(tmp_path):
    from pycat.batch.steps.analysis_steps import replay_msd_analysis

    state = _state()
    report = run_plan(_plan(_step("condensate_physics.compute_msd")), state,
                      params_by_step={"condensate_physics.compute_msd": _PARAMS})
    assert [s.outcome for s in report.steps] == ["ran"]
    D_guided = state['data_instance'].get_data('msd_D_um2_per_s')
    assert D_guided is not None and np.isfinite(D_guided)
    assert abs(D_guided - 0.05) < 0.02          # recovers the seeded diffusion coefficient

    state2 = _state()
    replay_msd_analysis(state2, tmp_path / 'm.tif', _PARAMS, tmp_path)
    assert D_guided == state2['data_instance'].get_data('msd_D_um2_per_s')   # guided == manual, bit for bit
    assert (tmp_path / 'm_msd.csv').exists() and (tmp_path / 'm_msd_fit.csv').exists()


def test_a_plan_with_both_ops_fits_once():
    state = _state()
    report = run_plan(_plan(_step("condensate_physics.compute_msd"),
                            _step("condensate_physics.fit_anomalous_diffusion")),
                      state, params_by_step={"condensate_physics.compute_msd": _PARAMS,
                                             "condensate_physics.fit_anomalous_diffusion": _PARAMS})
    assert [s.outcome for s in report.steps] == ["ran", "ran"]
    assert state['_msd_done'] is True
    assert state['data_instance'].get_data('msd_D_um2_per_s') is not None


def test_the_scale_gate_refuses_a_pixel_unit_diffusion_through_the_adapter():
    state = _state(real=False)                  # placeholder pixel size — y_um/x_um are really pixels
    report = run_plan(_plan(_step("condensate_physics.compute_msd")), state,
                      params_by_step={"condensate_physics.compute_msd": _PARAMS})
    assert [s.outcome for s in report.steps] == ["ran"]         # the step ran, but refused a number
    assert state['data_instance'].get_data('msd_D_um2_per_s') is None
    assert state['_msd_scale_validity']['scale_valid'] is False
