"""
PyCAT Condensate Physics Toolbox
==================================
Quantitative biophysical analysis tools for liquid-liquid phase separation.

Functions
---------
1.  Mean squared displacement (MSD) + anomalous diffusion fitting
    α < 1: subdiffusion / caged / gel-like
    α = 1: Brownian / liquid-like
    α > 1: directed / active transport
    Gives apparent diffusion coefficient D and anomalous exponent α.

2.  Intensity histogram decomposition (bimodal Gaussian)
    Fits the pixel intensity distribution within a cell as a mixture of
    two Gaussians (dilute phase + dense phase), extracting:
      - C_sat  : saturation concentration proxy (dilute-phase peak)
      - C_dense: dense-phase concentration proxy
      - Dense-phase fraction by pixel count

3.  Saturation concentration (C_sat) estimation — lever rule fitting
    Plots condensate fraction vs time (or condition) and fits the lever
    rule φ_condensate = (C_total - C_sat) / (C_dense - C_sat) to extract
    C_sat when total concentration is varied.

4.  Fusion kinetics — aspect ratio relaxation fitting
    After a merge event, fits the time series of post-merge aspect ratio
    to an exponential decay: AR(t) = 1 + (AR_0 - 1)·exp(-t/τ)
    giving the capillary relaxation time τ = η·R/γ.

5.  Coarsening kinetics
    Fits mean condensate radius vs time to distinguish:
      R(t) ~ t^(1/3) : Ostwald ripening (diffusion-limited dissolution/growth)
      R(t) ~ t^(1/2) : Lifshitz-Slyozov coalescence
      R(t) ~ const   : arrested / kinetically trapped

6.  Photobleaching correction
    Fits exponential decay to mean whole-cell fluorescence and divides
    each frame by the fitted curve to remove bleaching contribution.

7.  Out-of-focus frame detection
    Laplacian variance metric: low variance → blurry / out-of-focus frame.

8.  Surface tension proxy from shape fluctuations
    Variance of condensate boundary over time ∝ k_BT / γ·R.
    Requires tracked condensate boundary time series.

9.  Kaplan-Meier survival curve for condensate lifetimes
    Handles censoring (condensates present at movie start/end).

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2025
"""
from __future__ import annotations

import warnings
import numpy as np


from pycat.utils.object_ref import bbox_columns_from_regionprops as _bbox_cols
from pycat.utils.general_utils import debug_log
from pycat.utils.math_utils import robust_focus_energy, resolve_frame_mask
import pandas as pd

# Notifications via the shim: keeps the physics importable with no GUI stack (1.5.378).
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.fit_quality import assess_fit
from scipy import optimize, stats, ndimage


# ---------------------------------------------------------------------------
# 1. Mean Squared Displacement  ->  moved to condensate_physics/msd.py (1.6.220)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.msd import (  # noqa: E402,F401
    compute_msd, fit_anomalous_diffusion, msd_per_track, test_confinement,
    MIN_TRACK_LENGTH_FRAMES, _MAX_LAG_FRACTION, _HONEST_LAG_COUNT,
    _short_track_rejections, _report_short_track_rejections)

# ---------------------------------------------------------------------------
# 2. Intensity histogram decomposition  ->  moved to intensity.py (1.6.219)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.intensity import (  # noqa: E402,F401
    fit_bimodal_intensity, intensity_decomposition_per_cell)

# ---------------------------------------------------------------------------
# 3. Fusion kinetics — aspect ratio relaxation  ->  moved to relaxation.py (1.6.219)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.relaxation import (  # noqa: E402,F401
    fit_aspect_ratio_relaxation)

# ---------------------------------------------------------------------------
# 4. Coarsening kinetics  ->  moved to condensate_physics/coarsening.py (1.6.217)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.coarsening import fit_coarsening  # noqa: E402,F401

# ---------------------------------------------------------------------------
# 5. Photobleaching correction  ->  moved to photobleaching.py (1.6.218)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.photobleaching import (  # noqa: E402,F401
    fit_photobleaching, apply_bleach_correction)

# ---------------------------------------------------------------------------
# 6. Frame quality analysis  ->  moved to frame_quality.py (1.6.218)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.frame_quality import (  # noqa: E402,F401
    analyse_frame_quality, detect_out_of_focus)

# ---------------------------------------------------------------------------
# 7. Survival analysis (Kaplan-Meier)  ->  moved to survival.py (1.6.219)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.survival import (  # noqa: E402,F401
    kaplan_meier_lifetimes)

# ---------------------------------------------------------------------------
# Per-track MSD curves + microrheology moduli (for plotting)
# ---------------------------------------------------------------------------

def per_track_msd_curves(
    tracks_df: pd.DataFrame,
    max_lag: int = None,
    frame_interval_s: float = 1.0,
    min_track_length: int = MIN_TRACK_LENGTH_FRAMES,
    n_lags: int = 40,
) -> pd.DataFrame:
    """
    MSD(τ) curve for every individual track (for the spaghetti-plot overlay).

    Returns a long DataFrame: track_id, lag_frames, lag_s, msd_um2.
    Each track's MSD at a lag is the mean of that track's squared displacements
    at that lag (time-averaged MSD per track).

    Two performance measures keep this fast and light enough for movies with
    many long tracks:
      * lags are sampled LOG-SPACED (n_lags points across 1..max_lag) rather
        than every integer lag. MSD is viewed on log-log axes, so log-spaced
        lags preserve the curve shape while computing and rendering far fewer
        points (dense large-τ points are visually redundant).
      * displacements at each lag are computed VECTORISED (array slicing on a
        gap-filled position series) instead of an O(n²) Python double loop.
    """
    frames = sorted(tracks_df['frame'].unique())
    if max_lag is None:
        max_lag = max(1, len(frames) // 4)
    # Log-spaced unique integer lags in [1, max_lag].
    if max_lag <= n_lags:
        lags = np.arange(1, max_lag + 1)
    else:
        lags = np.unique(np.round(
            np.geomspace(1, max_lag, n_lags)).astype(int))
    rows = []
    for tid, grp in tracks_df.groupby('track_id'):
        if tid < 0:
            continue
        grp = grp.sort_values('frame').reset_index(drop=True)
        if len(grp) < min_track_length:
            continue
        t = grp['frame'].values.astype(int)
        y = grp['y_um'].values.astype(float)
        x = grp['x_um'].values.astype(float)
        # Build a gap-aware position series indexed by frame so a fixed lag is a
        # simple array shift. Missing frames are NaN and excluded per lag.
        f0, f1 = t.min(), t.max()
        span = f1 - f0 + 1
        ys = np.full(span, np.nan); xs = np.full(span, np.nan)
        ys[t - f0] = y; xs[t - f0] = x
        for lag in lags:
            if lag >= span:
                break
            dy = ys[lag:] - ys[:-lag]
            dx = xs[lag:] - xs[:-lag]
            sq = dy * dy + dx * dx
            valid = np.isfinite(sq)
            if valid.any():
                rows.append({'track_id': int(tid), 'lag_frames': int(lag),
                             'lag_s': lag * frame_interval_s,
                             'msd_um2': float(np.mean(sq[valid]))})
    return pd.DataFrame(rows)


_KB = 1.380649e-23  # Boltzmann constant, J/K


def compute_moduli_gser(
    msd_df: pd.DataFrame,
    bead_radius_um: float,
    temperature_C: float = 24.0,
    dimensions: int = 2,
) -> pd.DataFrame:
    """
    Estimate the viscoelastic moduli G'(ω) (storage) and G''(ω) (loss) from the
    ensemble MSD via the Mason (2000) generalized Stokes–Einstein relation with
    the local power-law (algebraic) approximation:

        |G*(ω)| = kB·T / (π·a·⟨Δr²(1/ω)⟩·Γ(1+α(ω)))
        G'(ω)   = |G*|·cos(π·α/2),   G''(ω) = |G*|·sin(π·α/2)

    where α(ω) is the local logarithmic slope of the MSD at τ = 1/ω, a is the
    bead radius, and ⟨Δr²⟩ is the 3-D MSD. Tracking here is 2-D, so the measured
    MSD is scaled to 3-D by 3/dimensions (×1.5 for 2-D) before applying the GSER,
    which makes the viscous limit reduce to the 3-D Stokes–Einstein value.

    This is a widely-used estimate valid in the intermediate-frequency range; it
    is unreliable at the first/last one or two frequencies (edge effects in the
    local-slope estimate) — those are dropped.

    VALIDATION STATUS (2026-07): this Mason-algebraic estimate is NOT YET
    validated in PyCAT and has two known failure modes: (1) on viscous-dominated
    samples (α≈1) G'(ω) = |G*|·cos(πα/2) is the small difference of large noisy
    terms and is essentially meaningless — G''≈ωη just re-reports the viscosity;
    (2) it inherits all noise from the ensemble MSD, so fragmented trajectories
    make the local slope α (and hence the G'/G'' split) unreliable. Use the
    direct viscosity fit for quantitative results. PLANNED UPGRADE: replace this
    with the Evans et al. (2009, Phys. Rev. E 80:012501) direct compliance→moduli
    conversion, which does not rely on the single-point local-power-law
    assumption and is more robust; validate against a known analytic MSD.

    Reference: Mason, T.G. (2000), Rheologica Acta 39:371–378.

    Parameters
    ----------
    msd_df : output of compute_msd() (needs lag_s, msd_um2).
    bead_radius_um : probe radius a in µm.
    temperature_C : temperature in Celsius.
    dimensions : dimensionality of the tracked MSD (2 for xy tracking).

    Returns
    -------
    DataFrame: omega_rad_s, freq_hz, alpha, g_star_pa, g_prime_pa,
        g_double_prime_pa.
    """
    from scipy.special import gamma as _gamma
    df = msd_df.dropna(subset=['lag_s', 'msd_um2'])
    df = df[df['msd_um2'] > 0].sort_values('lag_s')
    if len(df) < 4 or bead_radius_um <= 0:
        return pd.DataFrame(columns=['omega_rad_s', 'freq_hz', 'alpha',
                                     'g_star_pa', 'g_prime_pa',
                                     'g_double_prime_pa'])
    tau = df['lag_s'].values.astype(float)
    msd_2d = df['msd_um2'].values.astype(float)
    msd_3d = msd_2d * (3.0 / dimensions)          # scale to 3-D MSD

    # Local logarithmic slope α(τ) = dln(MSD)/dln(τ) by finite differences.
    ln_tau = np.log(tau)
    ln_msd = np.log(msd_3d)
    alpha = np.gradient(ln_msd, ln_tau)

    T = temperature_C + 273.15
    a_m = bead_radius_um * 1e-6
    msd_m2 = msd_3d * 1e-12                        # µm² → m²

    g_star = (_KB * T) / (np.pi * a_m * msd_m2 * _gamma(1.0 + alpha))
    g_prime = g_star * np.cos(np.pi * alpha / 2.0)
    g_double = g_star * np.sin(np.pi * alpha / 2.0)

    omega = 1.0 / tau
    out = pd.DataFrame({
        'omega_rad_s': omega, 'freq_hz': omega / (2 * np.pi),
        'alpha': alpha, 'g_star_pa': g_star,
        'g_prime_pa': g_prime, 'g_double_prime_pa': g_double,
    })
    # Drop the endpoints where the local-slope estimate is least reliable.
    if len(out) > 4:
        out = out.iloc[1:-1].reset_index(drop=True)
    return out.sort_values('omega_rad_s').reset_index(drop=True)


def compute_moduli_evans(
    msd_df: pd.DataFrame,
    bead_radius_um: float,
    temperature_C: float = 24.0,
    dimensions: int = 2,
    drop_edges: int = 1,
) -> pd.DataFrame:
    """
    Estimate viscoelastic moduli G'(ω) (storage) and G''(ω) (loss) from the
    ensemble MSD via the **Evans et al. (2009)** direct compliance→moduli
    conversion — the more robust replacement for the Mason (2000) single-point
    algebraic GSER (``compute_moduli_gser``).

    Method (Evans 2009, Phys. Rev. E 80:012501). The creep compliance is

        J(t) = π·a·⟨Δr²₃D(t)⟩ / (k_B·T)

    (2-D tracking is scaled to 3-D by 3/dimensions so the viscous limit reduces
    to the 3-D Stokes–Einstein value). J(t) is represented as a piecewise-linear
    interpolant through the sampled (tᵢ, Jᵢ) with J(0)=0; because J is then
    piecewise-linear its time-derivative J̇ is piecewise-constant, so the
    one-sided Fourier transform of each segment is analytic:

        iω·J̃(ω) = FT[J̇](ω)
                = m₀(1−e^{−iωt₀})/(iω)
                  + Σₖ mₖ(e^{−iωt_{k−1}} − e^{−iωtₖ})/(iω)
                  + m_N e^{−iωt_N}/(iω)          (terminal slope extrapolated)

    with segment slopes mₖ = (Jₖ−J_{k−1})/(tₖ−t_{k−1}). The complex modulus is
    then simply

        G*(ω) = 1 / (iω·J̃(ω)),   G'(ω) = Re G*,   G''(ω) = Im G*.

    Unlike Mason's method this makes **no single-point local-power-law
    assumption**, so it handles curvature, plateaus, and crossovers directly.

    VALIDATION (sandbox, against known analytic MSDs, 2026-07): recovers a pure
    viscous fluid to machine precision (G'≈0, G''=ηω exactly), and a single-mode
    Maxwell fluid to ~1–2% across the reliable band. The one weak region is the
    highest one or two frequencies (shortest lags), where the terminal-slope
    extrapolation and finite-difference edge make G'' least reliable — those
    endpoints are dropped (``drop_edges``). This is the documented edge effect of
    the method, not a defect. Advances since 2009 have been UPSTREAM of the
    conversion (localization-error subtraction, spline compliance interpolation,
    regularized/Bayesian MSD estimation, per-track bootstrap CIs), not
    replacements for it — those are the natural follow-on improvements.

    Parameters
    ----------
    msd_df : output of compute_msd() (needs lag_s, msd_um2).
    bead_radius_um : probe radius a in µm.
    temperature_C : temperature in Celsius.
    dimensions : dimensionality of the tracked MSD (2 for xy tracking).
    drop_edges : number of frequency points to drop from each spectral end
        (default 1) where the transform is least reliable.

    Returns
    -------
    DataFrame: omega_rad_s, freq_hz, alpha, g_star_pa, g_prime_pa,
        g_double_prime_pa  (same columns as compute_moduli_gser, so existing
        plotting/consumers work unchanged; ``alpha`` here is the local log-slope,
        reported for reference/QC only — it is NOT used to compute G*).
    """
    cols = ['omega_rad_s', 'freq_hz', 'alpha', 'g_star_pa',
            'g_prime_pa', 'g_double_prime_pa']
    df = msd_df.dropna(subset=['lag_s', 'msd_um2'])
    df = df[df['msd_um2'] > 0].sort_values('lag_s')
    if len(df) < 4 or bead_radius_um <= 0:
        return pd.DataFrame(columns=cols)

    t = df['lag_s'].values.astype(float)
    msd_2d = df['msd_um2'].values.astype(float)
    msd_3d = msd_2d * (3.0 / dimensions)          # scale to 3-D MSD

    T = temperature_C + 273.15
    a_m = bead_radius_um * 1e-6
    # Compliance J(t) = pi a MSD_3d / (kB T), with MSD in m^2.
    J = np.pi * a_m * (msd_3d * 1e-12) / (_KB * T)

    N = len(t)
    m0 = J[0] / t[0]                               # slope on [0, t0], J(0)=0
    m = np.diff(J) / np.diff(t)                    # slopes on [t_{k-1}, t_k]
    m_end = m[-1]                                  # terminal slope (extrapolated)

    omega = 1.0 / t

    def _iw_Jtilde(w):
        # FT of the piecewise-constant derivative J-dot = iw * Jtilde(w).
        s = m0 * (1.0 - np.exp(-1j * w * t[0])) / (1j * w)
        s += np.sum(m * (np.exp(-1j * w * t[:-1]) - np.exp(-1j * w * t[1:]))
                    / (1j * w))
        s += m_end * np.exp(-1j * w * t[-1]) / (1j * w)
        return s

    g_star_c = np.array([1.0 / _iw_Jtilde(w) for w in omega])
    g_prime = g_star_c.real
    g_double = g_star_c.imag
    g_star = np.abs(g_star_c)

    # Local log-slope, reported for QC only (not used in the conversion).
    with np.errstate(divide='ignore', invalid='ignore'):
        alpha = np.gradient(np.log(msd_3d), np.log(t))

    out = pd.DataFrame({
        'omega_rad_s': omega, 'freq_hz': omega / (2 * np.pi),
        'alpha': alpha, 'g_star_pa': g_star,
        'g_prime_pa': g_prime, 'g_double_prime_pa': g_double,
    }).sort_values('omega_rad_s').reset_index(drop=True)

    # ── Validity class per frequency ────────────────────────────────────────
    #
    # The moduli PLOT already knows which points cannot be trusted (1.5.380 stopped it
    # clipping negative G' onto a log axis). The DATA did not: anyone reading
    # `g_prime_pa` from the DataFrame, a CSV export or a table got a bare number with
    # no indication that it is meaningless at that frequency. Say so in the data.
    #
    #   supported            — both moduli positive; the conversion is reliable here.
    #   edge_affected        — within `drop_edges` of a spectral endpoint. The Evans
    #                          transform needs neighbours on both sides, so the first
    #                          and last points are systematically unreliable. These used
    #                          to be SILENTLY DROPPED, so the user never learned those
    #                          frequencies existed; they are now returned and labelled.
    #   sign_inconsistent    — a modulus came out <= 0. That is not a material property:
    #                          it is the conversion telling you it is not locally valid.
    #                          EXPECTED in a viscous-dominated medium, where G' is
    #                          genuinely ~0 and noise pushes it negative -- see the
    #                          moduli plot's note. Not an error; a null result.
    #   under_constrained    — fewer than ~3 lag points contribute in that neighbourhood.
    d = int(max(0, drop_edges))
    n = len(out)
    validity = np.full(n, 'supported', dtype=object)

    edge = np.zeros(n, dtype=bool)
    if d:
        edge[:d] = True
        edge[max(0, n - d):] = True
    validity[edge] = 'edge_affected'

    bad_sign = (out['g_prime_pa'].values <= 0) | (out['g_double_prime_pa'].values <= 0)
    validity[bad_sign & ~edge] = 'sign_inconsistent'

    # Too few lag points to constrain the local slope the transform depends on.
    if n < 5:
        validity[:] = 'under_constrained'

    out['validity'] = validity
    out['reliable'] = (out['validity'] == 'supported')

    n_sign = int((out['validity'] == 'sign_inconsistent').sum())
    n_edge = int((out['validity'] == 'edge_affected').sum())
    if n_sign:
        napari_show_warning(
            f"Evans moduli: {n_sign}/{n} frequencies are sign-inconsistent (a modulus "
            f"came out <= 0). This is EXPECTED for a viscous-dominated medium -- G' is "
            f"genuinely near zero and noise pushes it negative. Those frequencies are "
            f"labelled in the 'validity' column and are NOT a measurement of "
            f"elasticity. Passive VPT cannot resolve a G'/G'' crossover in this "
            f"regime; active microrheology can.")

    # The edge points are RETURNED (labelled), not dropped -- dropping them hid the
    # fact that the accessible frequency band is narrower than it appears.
    if d and n_edge:
        print(f"[PyCAT VPT] Evans: {n_edge} edge-affected frequencies retained and "
              f"labelled (previously dropped silently).")
    return out


def compute_moduli_evans_bootstrap(
    per_track_msd_df: pd.DataFrame,
    bead_radius_um: float,
    temperature_C: float = 24.0,
    dimensions: int = 2,
    drop_edges: int = 1,
    n_boot: int = 200,
    ci: float = 95.0,
    random_state: int = 0,
) -> pd.DataFrame:
    """
    Evans-2009 moduli WITH bootstrap confidence bands over trajectories.

    The point estimate is the Evans conversion of the ensemble-mean MSD (same as
    ``compute_moduli_evans``). The uncertainty is estimated by resampling whole
    TRACKS with replacement ``n_boot`` times, re-forming the ensemble-mean MSD
    for each resample, converting each to moduli, and taking percentile bands.
    Resampling tracks (not lags) captures the dominant track-to-track sampling
    variability that makes G'/G'' noisy on real data.

    This is the honest answer to noisy data. NOTE (validated in sandbox): the
    bands are approximate — empirical coverage of a known analytic truth ran a
    little below nominal (~84% for a nominal 95% band), because track-resampling
    captures sampling spread but not the transform's edge bias. Report/interpret
    them as an approximate confidence region, not an exact one. (An interpolation
    upgrade — natural/Akima spline of the compliance — was evaluated and REJECTED:
    it is a no-op on smooth MSDs and does not improve, and can worsen, noisy ones;
    the real lever for noise is these CIs plus upstream trajectory cleanup, not
    interpolation.)

    Parameters
    ----------
    per_track_msd_df : output of per_track_msd_curves() (track_id, lag_s, msd_um2).
    bead_radius_um, temperature_C, dimensions, drop_edges : as compute_moduli_evans.
    n_boot : number of bootstrap resamples.
    ci : central confidence interval width in percent (95 → 2.5/97.5 bands).
    random_state : RNG seed for reproducibility.

    Returns
    -------
    DataFrame with the compute_moduli_evans columns PLUS g_prime_lo, g_prime_hi,
    g_double_prime_lo, g_double_prime_hi (the CI bands). If there are too few
    tracks/lags, falls back to the point estimate with NaN bands.
    """
    base_cols = ['omega_rad_s', 'freq_hz', 'alpha', 'g_star_pa',
                 'g_prime_pa', 'g_double_prime_pa']
    band_cols = ['g_prime_lo', 'g_prime_hi',
                 'g_double_prime_lo', 'g_double_prime_hi']
    if per_track_msd_df is None or len(per_track_msd_df) == 0:
        return pd.DataFrame(columns=base_cols + band_cols)

    df = per_track_msd_df.dropna(subset=['track_id', 'lag_s', 'msd_um2'])
    df = df[df['msd_um2'] > 0]
    # Pivot to a track × lag matrix of MSD; lags shared across tracks.
    lags = np.sort(df['lag_s'].unique())
    track_ids = df['track_id'].unique()
    pivot = (df.pivot_table(index='track_id', columns='lag_s',
                            values='msd_um2', aggfunc='mean')
             .reindex(columns=lags))

    # Point estimate: ensemble-mean MSD (nanmean over tracks) → Evans.
    ens_mean = np.nanmean(pivot.values, axis=0)
    valid = np.isfinite(ens_mean) & (ens_mean > 0)
    t_all = lags[valid]
    msd_all = ens_mean[valid]
    point = compute_moduli_evans(
        pd.DataFrame({'lag_s': t_all, 'msd_um2': msd_all}),
        bead_radius_um, temperature_C, dimensions, drop_edges)
    if len(point) == 0 or len(track_ids) < 4:
        for c in band_cols:
            point[c] = np.nan
        return point

    # Bootstrap over tracks.
    rng = np.random.default_rng(random_state)
    n_tr = len(track_ids)
    mat = pivot.values                      # (n_tracks, n_lags)
    gp_boot, gpp_boot = [], []
    # Reference omega grid from the point estimate (so bands align to it).
    ref_omega = point['omega_rad_s'].values
    for _ in range(int(n_boot)):
        idx = rng.integers(0, n_tr, n_tr)
        ens = np.nanmean(mat[idx], axis=0)
        v = np.isfinite(ens) & (ens > 0)
        if v.sum() < 4:
            continue
        m = compute_moduli_evans(
            pd.DataFrame({'lag_s': lags[v], 'msd_um2': ens[v]}),
            bead_radius_um, temperature_C, dimensions, drop_edges)
        if len(m) == 0:
            continue
        # Align to reference omega (resamples can shift edge-drops slightly).
        gp_i = np.interp(ref_omega, m['omega_rad_s'].values,
                         m['g_prime_pa'].values, left=np.nan, right=np.nan)
        gpp_i = np.interp(ref_omega, m['omega_rad_s'].values,
                          m['g_double_prime_pa'].values, left=np.nan, right=np.nan)
        gp_boot.append(gp_i)
        gpp_boot.append(gpp_i)

    if len(gp_boot) < 10:                    # not enough successful resamples
        for c in band_cols:
            point[c] = np.nan
        return point

    gp_boot = np.array(gp_boot)
    gpp_boot = np.array(gpp_boot)
    lo_q = (100.0 - ci) / 2.0
    hi_q = 100.0 - lo_q
    with np.errstate(invalid='ignore'):
        point['g_prime_lo'] = np.nanpercentile(gp_boot, lo_q, axis=0)
        point['g_prime_hi'] = np.nanpercentile(gp_boot, hi_q, axis=0)
        point['g_double_prime_lo'] = np.nanpercentile(gpp_boot, lo_q, axis=0)
        point['g_double_prime_hi'] = np.nanpercentile(gpp_boot, hi_q, axis=0)
    return point


def extract_fusion_relaxation(
    mask_stack: np.ndarray,
    microns_per_pixel: float = 1.0,
    frame_interval_s: float = 1.0,
    proximity_um: float = 1.0,
    min_frames: int = 5,
) -> list:
    """
    Find fusion (merge) events in a labelled condensate stack and, for each,
    follow the merged droplet forward in time, recording its aspect ratio
    (major/minor axis) as it relaxes back toward a sphere.

    Returns a list of dicts, one per usable merge event:
        t0_frame, time_s (from the merge), aspect_ratio, R_um (equivalent
        radius of the merged droplet — a natural default characteristic length).
    """
    import skimage as sk
    from pycat.toolbox.dynamic_spatial_tools import detect_merge_fission
    events = detect_merge_fission(mask_stack, microns_per_pixel, proximity_um)
    if events.empty:
        return []
    merges = events[events['event_type'] == 'merge']
    tol_px = max(3.0, (proximity_um / max(microns_per_pixel, 1e-9)) * 5.0)
    out = []
    for _, ev in merges.iterrows():
        t0 = int(ev['frame'])
        cyx = np.array([ev['centroid_y_um'] / microns_per_pixel,
                        ev['centroid_x_um'] / microns_per_pixel])
        times, ars = [], []
        R_char = np.nan
        prev = cyx
        for t in range(t0, len(mask_stack)):
            lab = sk.measure.label(mask_stack[t] > 0)
            props = sk.measure.regionprops(lab)
            if not props:
                break
            best = min(props, key=lambda p: (p.centroid[0] - prev[0]) ** 2
                       + (p.centroid[1] - prev[1]) ** 2)
            dist = np.hypot(best.centroid[0] - prev[0], best.centroid[1] - prev[1])
            if dist > tol_px:
                break
            minor = max(best.axis_minor_length, 1e-6)
            ars.append(best.axis_major_length / minor)
            times.append((t - t0) * frame_interval_s)
            R_char = np.sqrt(best.area / np.pi) * microns_per_pixel
            prev = np.array(best.centroid)
        if len(ars) >= min_frames:
            out.append(dict(t0_frame=t0, time_s=np.array(times, float),
                            aspect_ratio=np.array(ars, float), R_um=float(R_char)))
    return out
