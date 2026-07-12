"""Golden-master tests for the VPT microrheology chain (condensate_physics_tools
+ vpt_tools) — the quantitative core behind PyCAT's viscosity measurements.

Strategy: simulate 2D Brownian trajectories with a KNOWN diffusion coefficient
D, then assert the full pipeline (compute_msd → fit_anomalous_diffusion →
viscosity_from_diffusion) recovers D, an exponent α≈1 (Brownian), and the
Stokes-Einstein viscosity for a known bead radius and temperature. These encode
the "the measurements are actually correct" claim as executable checks.

The randomness is seeded so the test is deterministic.
"""

import numpy as np
import pandas as pd
import pytest

from pycat.toolbox.condensate_physics_tools import (
    compute_msd, fit_anomalous_diffusion)
from pycat.toolbox.vpt_tools import viscosity_from_diffusion


def _simulate_brownian_tracks(n_tracks, n_frames, D_um2_per_s, dt_s, seed=0):
    """Generate n_tracks independent 2D Brownian walks with diffusion D.

    For 2D Brownian motion each axis has step variance 2*D*dt, so MSD(τ)=4Dτ.
    Returns a tracks DataFrame with columns track_id, frame, y_um, x_um.
    """
    rng = np.random.default_rng(seed)
    step_std = np.sqrt(2.0 * D_um2_per_s * dt_s)
    rows = []
    for tid in range(n_tracks):
        y = np.cumsum(rng.normal(0.0, step_std, n_frames))
        x = np.cumsum(rng.normal(0.0, step_std, n_frames))
        y -= y[0]; x -= x[0]
        for f in range(n_frames):
            rows.append({'track_id': tid, 'frame': f,
                         'y_um': y[f], 'x_um': x[f]})
    return pd.DataFrame(rows)


@pytest.mark.core
def test_msd_recovers_known_diffusion_coefficient():
    D_true = 0.05          # µm²/s
    dt = 0.1               # s/frame
    tracks = _simulate_brownian_tracks(
        n_tracks=200, n_frames=100, D_um2_per_s=D_true, dt_s=dt, seed=42)
    msd = compute_msd(tracks, frame_interval_s=dt, min_track_length=5)
    assert {'lag_frames', 'lag_s', 'msd_um2'}.issubset(msd.columns)
    fit = fit_anomalous_diffusion(msd)
    D_fit = fit['D_um2_per_s']
    alpha = fit['alpha']
    # Recover D within 25% (finite tracks/frames → statistical spread).
    assert abs(D_fit - D_true) / D_true < 0.25, (D_fit, D_true)
    # Brownian exponent near 1.
    assert 0.85 < alpha < 1.15, alpha


@pytest.mark.core
def test_viscosity_stokes_einstein_known_value():
    # η = kT / (6πRD). Check the arithmetic against a hand computation.
    kB = 1.380649e-23
    T_C = 24.0
    T_K = T_C + 273.15
    R_um = 0.5
    D = 0.1            # µm²/s
    # expected η, converting D µm²/s→m²/s (1e-12) and R µm→m (1e-6)
    expected = (kB * T_K) / (6 * np.pi * (R_um * 1e-6) * (D * 1e-12))
    eta = viscosity_from_diffusion(D, bead_radius_um=R_um, temperature_C=T_C)
    assert np.isfinite(eta)
    assert abs(eta - expected) / expected < 1e-6, (eta, expected)


@pytest.mark.core
def test_viscosity_full_chain_from_brownian_tracks():
    # End-to-end: known D → simulated tracks → MSD → fit → viscosity, and
    # compare to the Stokes-Einstein viscosity computed from the TRUE D.
    D_true = 0.02
    dt = 0.1
    R_um = 0.2
    T_C = 24.0
    tracks = _simulate_brownian_tracks(
        n_tracks=300, n_frames=120, D_um2_per_s=D_true, dt_s=dt, seed=7)
    msd = compute_msd(tracks, frame_interval_s=dt, min_track_length=5)
    fit = fit_anomalous_diffusion(msd)
    eta_fit = viscosity_from_diffusion(
        fit['D_um2_per_s'], bead_radius_um=R_um, temperature_C=T_C)
    eta_true = viscosity_from_diffusion(
        D_true, bead_radius_um=R_um, temperature_C=T_C)
    assert np.isfinite(eta_fit)
    # Viscosity ∝ 1/D, so a 25% D tolerance maps to a similar η tolerance.
    assert abs(eta_fit - eta_true) / eta_true < 0.35, (eta_fit, eta_true)


@pytest.mark.core
def test_viscosity_nonpositive_inputs_return_nan():
    assert np.isnan(viscosity_from_diffusion(0.0, bead_radius_um=0.2))
    assert np.isnan(viscosity_from_diffusion(0.1, bead_radius_um=0.0))
    assert np.isnan(viscosity_from_diffusion(-1.0, bead_radius_um=0.2))
