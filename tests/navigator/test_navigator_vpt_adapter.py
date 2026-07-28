"""**VPT microrheology adapter (spec N2b-1b): the flagship viscosity terminal runs from a navigator plan.**

The `vpt.microrheology` INTERPRET op now has an `ExecAdapter` → the `vpt_microrheology` batch handler (built in
N2b-1a), so a bead/viscosity plan computes instead of reporting 'run from its panel'. The handler is
self-contained (it runs the whole detect→link→MSD→fit→Stokes-Einstein chain from the raw stack), so the terminal
carries the workflow. This pins: the adapter resolves; a guided run equals the manual handler bit for bit; and
the scale gate still refuses a pixel-unit viscosity through the adapter path.
"""
import pathlib

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


def _bead_stack(D=0.05, dt=0.1, mpx=0.1, n=25, T=60, H=128, W=128, seed=0):
    rng = np.random.default_rng(seed)
    step_px = np.sqrt(2 * D * dt) / mpx
    pos = rng.uniform(20, H - 20, (n, 2))
    stack = np.zeros((T, H, W), np.float32)
    yy, xx = np.ogrid[:H, :W]
    for t in range(T):
        if t > 0:
            pos = np.clip(pos + rng.normal(0, step_px, (n, 2)), 3, H - 4)
        fr = np.zeros((H, W), np.float32)
        for cy, cx in pos:
            fr += np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 1.5 ** 2)))
        stack[t] = fr + rng.normal(0, 0.01, (H, W))
    return np.clip(stack, 0, None)


def _repo(mpx=0.1, dt=0.1, real=True):
    repo = {'microns_per_pixel_sq': (mpx ** 2 if real else 1.0)}
    if real:
        repo['pixel_size_confirmed'] = True
    if dt is not None:
        repo['file_metadata'] = {'common': {'frame_interval_s': dt}}
    return repo


def _step(name):
    return PlanStep(module=ModuleContract(name=name, info_role=InformationRole.INTERPRET),
                    produces=None, inputs=[], reason="")


def _plan(*steps):
    return Plan(intent=AnalysisIntent(target="bead", observables=["viscosity"]), steps=list(steps))


_PARAMS = {'bead_radius_um': 0.1, 'temperature_C': 24.0, 'min_track_length': 5, 'threshold': 0.05}


def test_the_vpt_op_has_an_adapter_and_the_plan_chains_it():
    assert has_adapter("vpt.microrheology")
    assert resolve_batch_step("vpt.microrheology") == "vpt_microrheology"
    # the canonical VPT plan reaches the microrheology terminal
    from pycat.navigator.session import NavigatorSession
    from pycat.navigator.execution import execution_order
    s = NavigatorSession(); s.ctx.set("axes", ["time"]); s.ctx.set("time_points", 200); s.ctx.set("voxel_size", 0.1)
    s.intent = AnalysisIntent(target="bead", observables=["viscosity"])
    names = [x.name for x in execution_order(s.planner.compile(s.intent, s.ctx, pins={}))]
    assert "vpt.microrheology" in names


def test_guided_viscosity_equals_the_manual_handler(tmp_path):
    from pycat.batch.steps.vpt_steps import replay_vpt_microrheology
    stack = _bead_stack()
    state = {'image': stack, 'data_instance': _DI(_repo())}
    report = run_plan(_plan(_step("vpt.microrheology")), state,
                      params_by_step={"vpt.microrheology": _PARAMS})
    assert [s.outcome for s in report.steps] == ["ran"]
    eta_guided = state['data_instance'].get_data('vpt_eta_Pa_s')
    assert eta_guided is not None and np.isfinite(eta_guided)

    state2 = {'image': stack.copy(), 'data_instance': _DI(_repo())}
    replay_vpt_microrheology(state2, tmp_path / 'm.tif', _PARAMS, tmp_path)
    assert eta_guided == state2['data_instance'].get_data('vpt_eta_Pa_s')   # guided == manual, bit for bit


def test_the_scale_gate_still_refuses_through_the_adapter(tmp_path):
    state = {'image': _bead_stack(), 'data_instance': _DI(_repo(real=False))}   # placeholder pixel size
    report = run_plan(_plan(_step("vpt.microrheology")), state,
                      params_by_step={"vpt.microrheology": _PARAMS})
    assert [s.outcome for s in report.steps] == ["ran"]                        # the step ran, but refused a number
    assert state['data_instance'].get_data('vpt_eta_Pa_s') is None
    assert state['_vpt_scale_validity']['scale_valid'] is False
