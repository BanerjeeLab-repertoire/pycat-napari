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


@pytest.mark.base
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


@pytest.mark.base
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


@pytest.mark.base
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


@pytest.mark.base
def test_viscosity_nonpositive_inputs_return_nan():
    assert np.isnan(viscosity_from_diffusion(0.0, bead_radius_um=0.2))
    assert np.isnan(viscosity_from_diffusion(0.1, bead_radius_um=0.0))
    assert np.isnan(viscosity_from_diffusion(-1.0, bead_radius_um=0.2))


@pytest.mark.base
def test_viscosity_carries_the_interval_the_msd_fit_supports():
    """The CI on D must reach the viscosity — it is the number that goes into the paper.

    ``fit_anomalous_diffusion`` computes a 95 % CI on D from the fit covariance (1.5.447), and
    ``viscosity_measurement`` already knew how to propagate it — **but nothing was passing
    it.** The interval was computed, the consumer could take it, and the two were never
    connected.

    Stokes-Einstein is ``η = kT / (6πRD)``, so the interval propagates **exactly** and it
    **inverts**: a LOW D gives a HIGH viscosity, so the viscosity interval is *not* symmetric
    about the point estimate.

    Measured (bead radius 0.1 µm, 24 °C, true D = 0.05 µm²/s):

    ==========  ==========================  ==================================
    lag window  D (95 % CI)                 viscosity (95 % CI)
    ==========  ==========================  ==================================
    30 lags     0.0473 [0.0353, 0.0594]     0.046 Pa·s [0.037, 0.062]  1.7×
    4 lags      0.0510 [0.0349, 0.0671]     0.043 Pa·s [0.032, 0.062]  **1.9×**
    ==========  ==========================  ==================================
    """
    import pandas as pd

    physics = pytest.importorskip("pycat.toolbox.condensate_physics_tools")
    vpt = pytest.importorskip("pycat.toolbox.vpt_tools")

    true_D = 0.05
    n_lags = 20
    rng = np.random.default_rng(0)
    tau = np.arange(1, n_lags + 1) * 0.1
    msd = (4 * true_D * tau ** 1.0 + 0.001) * (1 + rng.normal(0, 0.10, n_lags))
    msd_df = pd.DataFrame({
        "lag_s": tau, "msd_um2": msd,
        "n_pairs": np.full(n_lags, 200), "n_tracks": np.full(n_lags, 50),
    })

    fit = physics.fit_anomalous_diffusion(msd_df, confine_to_defensible_bounds=False)

    D_ci = fit.get("identifiability", {}).get("D_um2_per_s", {}).get("ci")
    assert D_ci is not None, (
        "fit_anomalous_diffusion did not report a confidence interval on D. The covariance "
        "from curve_fit is the only thing that knows how well the lag window constrains D, "
        "and it used to be discarded (`popt, _ = curve_fit(...)`)."
    )

    measurement = vpt.viscosity_measurement(
        D_um2_per_s=fit["D_um2_per_s"], bead_radius_um=0.1, temperature_C=24.0,
        D_ci=D_ci, alpha=fit.get("alpha"),
    )

    assert measurement.ci is not None, (
        "The viscosity was reported WITHOUT an interval, even though the MSD fit supplied "
        "one. Stokes-Einstein propagates it exactly — collapsing it to a point estimate "
        "throws away the one quantity that says how much to trust the number."
    )

    low, high = measurement.ci
    assert low < measurement.value < high, (
        f"the viscosity {measurement.value:.4g} is not inside its own interval "
        f"[{low:.4g}, {high:.4g}]"
    )

    # The interval INVERTS: a low D gives a HIGH viscosity.
    eta_from_low_D = vpt.viscosity_from_diffusion(D_ci[0], 0.1, 24.0)
    assert eta_from_low_D == pytest.approx(high, rel=1e-6), (
        f"the UPPER end of the viscosity interval must come from the LOWER end of D's "
        f"interval — eta = kT/(6*pi*R*D) inverts. Got {high:.4g} from the propagation and "
        f"{eta_from_low_D:.4g} from D_low directly."
    )
