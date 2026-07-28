"""**VPT microrheology batch handler (spec N2b-1): the full headless chain + the non-negotiable scale gate.**

`replay_vpt_microrheology` runs bead-movie → detect → link → MSD → anomalous-diffusion fit → Stokes–Einstein
viscosity, headlessly (no torch). These pin: (1) it recovers a SEEDED viscosity within the golden-master ±15%
band on a synthetic bead stack; (2) the scale gate REFUSES to emit a pixel-unit viscosity — no real pixel size
or no frame interval yields a verdict, never a number. Imports only the non-GUI handler (stays in the gate).
"""
import numpy as np
import pandas as pd
import pytest

from pycat.batch.steps.vpt_steps import replay_vpt_microrheology

pytestmark = pytest.mark.base


class _DI:
    def __init__(self, repo=None):
        self.data_repository = dict(repo or {})

    def set_data(self, k, v):
        self.data_repository[k] = v

    def get_data(self, k, d=None):
        return self.data_repository.get(k, d)


def _bead_stack(D=0.05, dt=0.1, mpx=0.1, n=25, T=60, H=128, W=128, seed=0):
    """A synthetic bead movie: n beads doing Brownian motion (diffusion D µm²/s, step dt s), rendered as
    Gaussian blobs + a little noise — a stack the full detect→link→MSD chain recovers D (hence η) from."""
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


def _repo(mpx=0.1, dt=0.1, real_pixel_size=True):
    repo = {'microns_per_pixel_sq': (mpx ** 2 if real_pixel_size else 1.0)}
    if real_pixel_size:
        repo['pixel_size_confirmed'] = True          # a genuine, not-placeholder pixel size
    if dt is not None:
        repo['file_metadata'] = {'common': {'frame_interval_s': dt}}
    return repo


def test_the_full_chain_recovers_the_seeded_viscosity(tmp_path):
    D, dt, R, temp, mpx = 0.05, 0.1, 0.1, 24.0, 0.1
    state = {'image': _bead_stack(D=D, dt=dt, mpx=mpx), 'data_instance': _DI(_repo(mpx=mpx, dt=dt))}
    replay_vpt_microrheology(
        state, tmp_path / 'movie.tif',
        {'bead_radius_um': R, 'temperature_C': temp, 'min_track_length': 5, 'threshold': 0.05}, tmp_path)

    eta = state['data_instance'].get_data('vpt_eta_Pa_s')
    kB = 1.380649e-23
    eta_true = kB * (temp + 273.15) / (6.0 * np.pi * (R * 1e-6) * (D * 1e-12))
    assert eta is not None and np.isfinite(eta)
    assert abs(eta - eta_true) / eta_true < 0.15          # the golden-master ±15% band
    assert state['_vpt_scale_validity']['scale_valid'] is True
    df = pd.read_csv(tmp_path / 'movie_vpt_microrheology.csv')
    assert bool(df['scale_valid'].iloc[0]) and np.isfinite(df['viscosity_Pa_s'].iloc[0])


def test_the_scale_gate_refuses_a_pixel_unit_viscosity(tmp_path):
    # microns_per_pixel_sq == 1.0 and unconfirmed → the loader placeholder, NOT a measurement
    state = {'image': _bead_stack(), 'data_instance': _DI(_repo(real_pixel_size=False))}
    replay_vpt_microrheology(state, tmp_path / 'movie.tif', {}, tmp_path)

    assert state['data_instance'].get_data('vpt_eta_Pa_s') is None    # NO viscosity emitted
    v = state['_vpt_scale_validity']
    assert v['scale_valid'] is False and 'pixel' in v['reason'].lower()
    df = pd.read_csv(tmp_path / 'movie_vpt_microrheology.csv')       # the CSV carries the verdict, not a number
    assert not np.isfinite(df['viscosity_Pa_s'].iloc[0])


def test_the_scale_gate_refuses_without_a_frame_interval(tmp_path):
    state = {'image': _bead_stack(), 'data_instance': _DI(_repo(dt=None))}   # real pixel size, no frame interval
    replay_vpt_microrheology(state, tmp_path / 'movie.tif', {}, tmp_path)

    assert state['data_instance'].get_data('vpt_eta_Pa_s') is None
    v = state['_vpt_scale_validity']
    assert v['scale_valid'] is False and 'frame interval' in v['reason'].lower()
