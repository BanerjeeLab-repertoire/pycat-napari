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
import pandas as pd

# Notifications via the shim: keeps the physics importable with no GUI stack (1.5.378).
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.fit_quality import assess_fit
from scipy import optimize, stats, ndimage


# ---------------------------------------------------------------------------
# 1. Mean Squared Displacement
# ---------------------------------------------------------------------------

def compute_msd(
    tracks_df: pd.DataFrame,
    max_lag: int = None,
    frame_interval_s: float = 1.0,
    min_track_length: int = 5,
    reject_outlier_tracks: bool = True,
    outlier_iqr_factor: float = 1.5,
) -> pd.DataFrame:
    """
    Compute ensemble-averaged MSD from linked trajectories, with a per-track
    uncertainty that reflects the number of INDEPENDENT tracks (not the number
    of correlated, overlapping displacement pairs).

    For each track and lag τ we first average that track's own squared
    displacements into a single per-track MSD(τ). The ensemble MSD is then the
    mean across tracks, and the uncertainty (msd_std / msd_sem) is the
    track-to-track spread — a statistically honest error bar, since tracks are
    independent whereas overlapping pairs within a track are not.

    Coordinates are expected in microns (columns y_um, x_um).

    Parameters
    ----------
    tracks_df : DataFrame with columns track_id, frame, y_um, x_um
        Output of link_trajectories() or link_trajectories_bayesian().
    max_lag : int or None
        Maximum lag (in frames) to compute.  Default: n_frames // 4
        (beyond n/4 the MSD estimate has too few samples to be reliable).
    frame_interval_s : float
        Physical time per frame in seconds.
    min_track_length : int
        Tracks shorter than this are excluded.

    Returns
    -------
    msd_df : DataFrame with columns:
        lag_frames, lag_s, msd_um2 (ensemble MSD = mean over tracks),
        msd_std (spread across tracks), msd_sem (standard error of the mean
        over tracks), n_tracks (independent tracks at this lag),
        n_pairs (total displacement pairs — reference only)
    """
    frames = sorted(tracks_df['frame'].unique())
    if max_lag is None:
        max_lag = max(1, len(frames) // 4)

    # One MSD value per track per lag (tracks are the independent unit).
    per_track: dict[int, list[float]] = {lag: [] for lag in range(1, max_lag + 1)}
    pair_counts: dict[int, int] = {lag: 0 for lag in range(1, max_lag + 1)}

    # ── Outlier-track rejection (matches the reference analysis notebook) ────
    # A movie yields many trajectories; spurious ones (mis-links, brief tracks
    # that happen to jump) have anomalously HIGH first/last MSD and, if included,
    # inflate the ensemble MSD → inflate D → deflate viscosity by a large factor.
    # The reference workflow rejects tracks whose first and last per-track MSD
    # fall outside a 1.5×IQR fence in LOG space (get_outlier_bounds). We replicate
    # that: compute each eligible track's first- and last-lag MSD, build the log
    # IQR fences, and keep only tracks inside both. This is what brings PyCAT's
    # viscosity into line with the hand-analysis on real data.
    accepted_ids = None
    if reject_outlier_tracks:
        firsts, lasts, ids = [], [], []
        for tid, grp in tracks_df.groupby('track_id'):
            if tid < 0:
                continue
            g = grp.sort_values('frame')
            if len(g) < min_track_length:
                continue
            t = g['frame'].values.astype(int)
            y = g['y_um'].values.astype(float)
            x = g['x_um'].values.astype(float)
            f0, f1 = t.min(), t.max()
            span = f1 - f0 + 1
            ys = np.full(span, np.nan); xs = np.full(span, np.nan)
            ys[t - f0] = y; xs[t - f0] = x
            # first-lag MSD (lag 1)
            dy1 = ys[1:] - ys[:-1]; dx1 = xs[1:] - xs[:-1]
            sq1 = dy1 * dy1 + dx1 * dx1; v1 = np.isfinite(sq1)
            # last-lag MSD (largest available lag for this track)
            L = span - 1
            dyL = ys[L:] - ys[:-L]; dxL = xs[L:] - xs[:-L]
            sqL = dyL * dyL + dxL * dxL; vL = np.isfinite(sqL)
            if v1.any() and vL.any():
                m1 = float(np.mean(sq1[v1])); mL = float(np.mean(sqL[vL]))
                if m1 > 0 and mL > 0:
                    firsts.append(m1); lasts.append(mL); ids.append(tid)
        if len(ids) >= 8:
            lf = np.log(np.asarray(firsts)); ll = np.log(np.asarray(lasts))
            q1f, q3f = np.percentile(lf, [25, 75]); iqrf = q3f - q1f
            q1l, q3l = np.percentile(ll, [25, 75]); iqrl = q3l - q1l
            lo_f = q1f - outlier_iqr_factor * iqrf
            hi_f = q3f + outlier_iqr_factor * iqrf
            lo_l = q1l - 1.0 * iqrl                     # notebook uses 1×IQR lower
            hi_l = q3l + outlier_iqr_factor * iqrl
            accepted_ids = {tid for tid, a, b in zip(ids, lf, ll)
                            if lo_f <= a <= hi_f and lo_l <= b <= hi_l}

    for tid, grp in tracks_df.groupby('track_id'):
        if tid < 0:
            continue
        if accepted_ids is not None and tid not in accepted_ids:
            continue
        grp = grp.sort_values('frame').reset_index(drop=True)
        if len(grp) < min_track_length:
            continue

        t = grp['frame'].values.astype(int)
        y = grp['y_um'].values.astype(float)
        x = grp['x_um'].values.astype(float)

        # Gap-aware position series indexed by frame, so displacements at a fixed
        # lag are a vectorised array shift instead of an O(n^2) Python double
        # loop over pairs. Missing frames are NaN and excluded per lag. This is
        # numerically identical to the pairwise loop but far faster on long
        # tracks (the double loop made large movies hang).
        f0, f1 = t.min(), t.max()
        span = f1 - f0 + 1
        ys = np.full(span, np.nan); xs = np.full(span, np.nan)
        ys[t - f0] = y; xs[t - f0] = x

        for lag in range(1, max_lag + 1):
            if lag >= span:
                break
            dy = ys[lag:] - ys[:-lag]
            dx = xs[lag:] - xs[:-lag]
            sq = dy * dy + dx * dx
            valid = np.isfinite(sq)
            n_valid = int(valid.sum())
            if n_valid:
                per_track[lag].append(float(np.mean(sq[valid])))  # this track's MSD(τ)
                pair_counts[lag] += n_valid

    rows = []
    for lag in range(1, max_lag + 1):
        vals = per_track[lag]
        if not vals:
            continue
        arr = np.asarray(vals)
        n_tracks = arr.size
        std = float(np.std(arr, ddof=1)) if n_tracks > 1 else np.nan
        sem = std / np.sqrt(n_tracks) if n_tracks > 1 else np.nan
        rows.append({
            'lag_frames': lag,
            'lag_s':      lag * frame_interval_s,
            'msd_um2':    float(np.mean(arr)),
            'msd_std':    std,
            'msd_sem':    sem,
            'n_tracks':   n_tracks,
            'n_pairs':    pair_counts[lag],
        })
    return pd.DataFrame(rows)


def _confined_msd(t, L2, D, off):
    """MSD of a probe confined to a domain: rises, then PLATEAUS at L2."""
    return L2 * (1.0 - np.exp(-4.0 * D * t / max(L2, 1e-12))) + 4.0 * off


def _aicc(y, y_fit, k):
    n = len(y)
    rss = float(np.sum((y - y_fit) ** 2))
    if rss <= 0 or n <= k + 2:
        return np.inf
    return n * np.log(rss / n) + 2 * k + (2 * k * (k + 1)) / (n - k - 1)


def test_confinement(tau, msd):
    """Is this MSD a power law, or a probe hitting a WALL?

    Why this test and not a residual runs test
    ------------------------------------------
    ``motion_type`` is read straight off ``alpha``, and alpha is the entire
    anomalous-vs-Brownian claim — but alpha only means anything if the power law is the
    right model. **Confinement is the failure that matters:** a probe trapped in a small
    condensate produces an MSD that *plateaus*, and a power law cannot plateau, so it
    fits the plateau with a spuriously small exponent::

        truly Brownian:  alpha = 1.006, R² = 1.000  ->  'Brownian'      correct
        CONFINED:        alpha = 0.000, R² = 0.903  ->  'subdiffusion'  WRONG

    The confined probe is reported as **subdiffusion with a healthy R²**, which a reader
    takes as "the medium is viscoelastic / crowded". It is not: the probe is hitting a
    wall. Different physics, wrong conclusion, and R² does not blink.

    A residual **runs test** detects this in principle — but it needs at least 8 residuals
    to have any power, and PyCAT's *defensible lag window* is deliberately narrow, often
    only ~6 lags. Applying it there flagged **100 % of fits, including textbook Brownian
    ones**, because "could not assess" was being conflated with "the model is wrong".

    So compare the **models** instead, which works at n = 6. Fitting both a power law and
    a confined model and choosing by AICc (Δ > 2):

    ======  ================  ================  ====================
    n lags  Brownian→power    subdiffusion→     **confined→
            (false alarm)     power             confined** (detect)
    ======  ================  ================  ====================
    **6**   **100 %**         85 %              **60 %**
    8       100 %             95 %              85 %
    **10**  **100 %**         95 %              **100 %**
    15+     100 %             100 %             100 %
    ======  ================  ================  ====================

    **Zero false alarms on Brownian data at every window size** — a genuinely diffusing
    probe is never called confined. Detection of real confinement is 60 % at six lags and
    100 % from ten, so a *negative* result on a short window means "not detected", not
    "not confined".
    """
    tau = np.asarray(tau, dtype=float)
    msd = np.asarray(msd, dtype=float)
    ok = np.isfinite(tau) & np.isfinite(msd) & (tau > 0)
    tau, msd = tau[ok], msd[ok]
    if tau.size < 5:
        return dict(confined=False, assessable=False,
                    verdict="Too few lags to test for confinement.")

    try:
        p_pl, _ = optimize.curve_fit(
            lambda t, D, a, off: 4.0 * D * t ** a + 4.0 * off,
            tau, msd, p0=[max(msd[0] / (4 * tau[0]), 1e-6), 1.0, 0.0], maxfev=30000)
        pl_fit = 4.0 * p_pl[0] * tau ** p_pl[1] + 4.0 * p_pl[2]
        a_pl = _aicc(msd, pl_fit, 3)
    except Exception:
        return dict(confined=False, assessable=False,
                    verdict="Power-law fit failed; confinement not assessed.")

    try:
        p_cf, _ = optimize.curve_fit(
            _confined_msd, tau, msd,
            p0=[max(msd.max(), 1e-9), max(msd[0] / (4 * tau[0]), 1e-6), 0.0],
            maxfev=30000)
        cf_fit = _confined_msd(tau, *p_cf)
        a_cf = _aicc(msd, cf_fit, 3)
    except Exception:
        return dict(confined=False, assessable=True,
                    verdict="Confined-model fit failed; power law retained.")

    delta = float(a_pl - a_cf)          # positive => confined model is better
    confined = bool(delta > 2.0)
    L_um = float(np.sqrt(max(p_cf[0], 0.0)))

    if confined:
        verdict = (f"The MSD is better described by a CONFINED model than by a power law "
                   f"(ΔAICc = {delta:.1f}, plateau ≈ {p_cf[0]:.4g} µm², i.e. a domain of "
                   f"about {L_um:.2f} µm). **alpha is not a measure of anomalous "
                   f"diffusion here — the probe is hitting a wall, not moving through a "
                   f"viscoelastic medium.** Check that the probe is sampling the bulk: a "
                   f"probe inside a condensate smaller than a few times its own diameter "
                   f"cannot report bulk viscosity.")
    else:
        verdict = (f"No evidence of confinement (ΔAICc = {delta:.1f} in favour of the "
                   f"power law). Note that detection is ~60 % at six lags and ~100 % from "
                   f"ten, so on a short lag window this is 'not detected', not 'not "
                   f"confined'.")

    return dict(confined=confined, assessable=True, delta_aicc=delta,
                plateau_um2=float(p_cf[0]), domain_size_um=L_um,
                n_lags=int(tau.size), verdict=verdict)


def fit_anomalous_diffusion(
    msd_df: pd.DataFrame,
    max_lag_fit: int = None,
    fit_localization_offset: bool = True,
    frame_interval_s: float = None,
    upper_lag_rule: str = 'fraction',
    upper_lag_fraction: float = 0.25,
    upper_lag_fixed_s: float = None,
    min_independent_pairs: int = 10,
    confine_to_defensible_bounds: bool = True,
) -> dict:
    """
    Fit MSD(τ) = 4D·τ^α (anomalous diffusion model) using log-log regression.

    LAG-WINDOW FIT GATE
    -------------------
    The reliable MSD lag window is bounded by hardware on both ends:

    * **High-frequency cutoff = frame rate.** The shortest resolvable lag is one
      frame interval; nothing faster is sampled.
    * **Low-frequency cutoff = acquisition duration.** At long lags there are very
      few independent displacement pairs, so the MSD becomes unreliable well
      before the full record length.

    Fitting outside this band (e.g. only the first fraction of a second, where the
    curve is dominated by the localization-noise floor, or out toward the full
    duration, where a handful of pairs dominate) produces a wrong D/α. The gate
    computes a defensible ``[lag_lo, lag_hi]`` and, when
    ``confine_to_defensible_bounds`` is on (default), fits only within it. It
    **warns rather than blocks** when the data can't cover the requested window.

    Parameters
    ----------
    msd_df : output of compute_msd() (needs lag_s, msd_um2, n_pairs; n_tracks used
        for the min-pairs rule when present).
    max_lag_fit : legacy cap on the number of head lags (kept for back-compat;
        applied before the window gate if given).
    fit_localization_offset : fit the +4σ_loc² localization-offset term.
    frame_interval_s : seconds per frame; sets the high-frequency cutoff
        (lag_lo = frame_interval_s). If None, inferred from the smallest lag.
    upper_lag_rule : how to set the low-frequency (upper-lag) cutoff:
        * 'fraction'    — lag_hi = upper_lag_fraction × (max track duration).
                          The standard convention; conservative.
        * 'fixed'       — lag_hi = upper_lag_fixed_s (a hardware-defensible band,
                          e.g. matching routine lab practice).
        * 'min_pairs'   — keep lags while ≥ min_independent_pairs independent
                          tracks span them (statistically principled; adapts to
                          how many/how long the tracks are).
    upper_lag_fraction : fraction for the 'fraction' rule (default 0.25).
    upper_lag_fixed_s : upper lag (s) for the 'fixed' rule.
    min_independent_pairs : threshold for the 'min_pairs' rule.
    confine_to_defensible_bounds : if True (default), clip the fit to the computed
        window; if False, fit the full available range (at the user's risk).

    Returns
    -------
    dict with keys:
        D_um2_per_s     : apparent diffusion coefficient in µm²/s
        alpha           : anomalous exponent (1=Brownian, <1=subdiff, >1=superdiff)
        motion_type     : 'subdiffusion' | 'Brownian' | 'superdiffusion'
        r_squared       : goodness of fit
        fit_lags_s      : lag times used in fit (array)
        fit_msd         : fitted MSD values (array)
        log_log_slope   : raw slope from log-log regression (= alpha)
        log_log_intercept : raw intercept (log(4D))
        fit_window_s    : (lag_lo, lag_hi) the defensible window used
        fit_window_warning : str or None — set when data can't cover the window
    """
    df = msd_df[msd_df['n_pairs'] > 5].copy()
    if max_lag_fit is not None:
        df = df.head(max_lag_fit)

    # ── Lag-window fit gate ──────────────────────────────────────────────────
    window_warning = None
    lag_lo = lag_hi = None
    if 'lag_s' in df.columns and len(df):
        all_lags = df['lag_s'].values.astype(float)
        # High-frequency cutoff = one frame interval.
        lag_lo = float(frame_interval_s) if (frame_interval_s and frame_interval_s > 0) \
            else float(np.min(all_lags))
        # Low-frequency (upper-lag) cutoff per the chosen rule.
        max_lag_available = float(np.max(all_lags))
        rule = (upper_lag_rule or 'fraction').lower()
        if rule == 'fixed' and upper_lag_fixed_s and upper_lag_fixed_s > 0:
            lag_hi = float(upper_lag_fixed_s)
        elif rule == 'min_pairs' and 'n_tracks' in df.columns:
            ok = df[df['n_tracks'] >= int(min_independent_pairs)]
            lag_hi = float(ok['lag_s'].max()) if len(ok) else lag_lo
        else:  # 'fraction' (default)
            lag_hi = float(upper_lag_fraction) * max_lag_available

        # Sanity + coverage warnings (warn, never block).
        if lag_hi <= lag_lo:
            window_warning = (
                f"Requested lag window collapses (lag_lo={lag_lo:.3g}s ≥ "
                f"lag_hi={lag_hi:.3g}s). The acquisition is too short, or the "
                f"upper-lag rule is too strict, to define a fit band. Fitting the "
                f"full available range instead.")
            lag_hi = max_lag_available
        elif lag_hi > max_lag_available + 1e-12:
            window_warning = (
                f"Requested upper lag ({lag_hi:.3g}s) exceeds the longest "
                f"available lag ({max_lag_available:.3g}s): the acquisition "
                f"duration is too short to reach the low-frequency cutoff, so "
                f"G(τ)/viscosity may be under-resolved at long lags.")
            lag_hi = max_lag_available

        if confine_to_defensible_bounds:
            gated = df[(df['lag_s'] >= lag_lo - 1e-12)
                       & (df['lag_s'] <= lag_hi + 1e-12)]
            if len(gated) >= 3:
                df = gated
            else:
                window_warning = (
                    (window_warning + " ") if window_warning else ""
                ) + (f"Only {len(gated)} lag(s) fall inside the defensible "
                     f"window [{lag_lo:.3g}, {lag_hi:.3g}]s — too few to fit; "
                     f"using the full available range instead.")

    if len(df) < 3:
        return dict(D_um2_per_s=np.nan, alpha=np.nan, motion_type='unknown',
                    r_squared=np.nan, fit_lags_s=np.array([]),
                    fit_msd=np.array([]), log_log_slope=np.nan,
                    log_log_intercept=np.nan,
                    fit_window_s=(lag_lo, lag_hi),
                    fit_window_warning=window_warning)

    tau = df['lag_s'].values.astype(float)
    msd = df['msd_um2'].values.astype(float)

    # Initial guess from a log-log regression (fast, unbiased enough to seed).
    log_slope, log_intercept, r, _p, _se = stats.linregress(np.log(tau), np.log(msd))

    # Refine with a DIRECT non-linear fit of MSD = 4·D·τ^α. This avoids the
    # log-transform bias (Jensen) of the pure log-log fit, and weights points by
    # their measured uncertainty (msd_sem) so noisy large-lag points, which have
    # few independent tracks, no longer count as much as precise short-lag ones.
    D_ll = float(np.exp(log_intercept) / 4.0)
    a_ll = float(log_slope)
    D, alpha = D_ll, a_ll
    sigma_loc_um = float('nan')
    try:
        sigma = None
        if 'msd_sem' in df.columns:
            sem = df['msd_sem'].values.astype(float)
            if np.all(np.isfinite(sem)) and np.all(sem > 0):
                sigma = sem
        if fit_localization_offset:
            # Fit MSD = 4·D·τ^α + 4·σ_loc², separating the STATIC LOCALIZATION
            # ERROR (a constant offset from centroid uncertainty) from real
            # diffusion. This matters enormously in viscous samples: when the
            # medium is thick the bead barely moves per frame, so the constant
            # localization floor can dwarf the real τ-dependent signal. A fit
            # WITHOUT the offset absorbs that floor into D, inflating D (and thus
            # deflating Stokes-Einstein viscosity) by a large factor. The offset
            # term lets D reflect only the genuine time-dependent motion.
            # Parameter 3 is σ_loc² (µm²); reported back as σ_loc (nm) for the
            # user to sanity-check against their expected localization precision.
            # Offset bound matches the reference notebook workflow: the constant
            # term N = 4·σ_loc² cannot exceed the smallest MSD value, since
            # MSD = (non-negative diffusion signal) + N. Our fit parameter is
            # off = N/4, so its upper bound is min(msd)/4.
            off_max = max(float(np.min(msd)) / 4.0, 1e-9)
            off0 = min(max(float(np.min(msd)) * 0.25, 1e-9), off_max)
            popt, _ = optimize.curve_fit(
                lambda tt, D_, a_, off_: 4.0 * D_ * tt ** a_ + 4.0 * off_,
                tau, msd,
                p0=[max(D_ll, 1e-9), a_ll, off0], sigma=sigma,
                absolute_sigma=False,
                bounds=([1e-12, 0.05, 0.0], [1e6, 3.0, off_max]), maxfev=10000)
            D, alpha = float(popt[0]), float(popt[1])
            sigma_loc_um = float(np.sqrt(max(popt[2], 0.0)))
        else:
            popt, _ = optimize.curve_fit(
                lambda tt, D_, a_: 4.0 * D_ * tt ** a_, tau, msd,
                p0=[max(D_ll, 1e-9), a_ll], sigma=sigma, absolute_sigma=False,
                bounds=([1e-12, 0.05], [1e6, 3.0]), maxfev=10000)
            D, alpha = float(popt[0]), float(popt[1])
    except Exception:
        pass  # keep the log-log estimate if the non-linear fit fails

    tau_fit = tau
    _off = (sigma_loc_um ** 2) if np.isfinite(sigma_loc_um) else 0.0
    msd_fit = 4 * D * tau_fit ** alpha + 4 * _off
    # R² of the (non-linear) model on the actual MSD values.
    ss_res = float(np.sum((msd - msd_fit) ** 2))
    ss_tot = float(np.sum((msd - msd.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan

    # ── Does the POWER LAW actually describe this MSD? ──────────────────────────
    #
    # `motion_type` is read straight off alpha, and alpha is the entire
    # anomalous-vs-Brownian claim. But alpha is only meaningful if the power law is the
    # right model -- and R² will not tell you, because beating a flat line is a trivially
    # low bar for a monotonic MSD.
    #
    # The failure that matters here is CONFINEMENT. A bead trapped in a small condensate
    # produces an MSD that PLATEAUS, and a power law cannot plateau -- so it fits the
    # plateau with a tiny exponent. Measured on a synthetic confined trajectory:
    #
    #     truly Brownian:  alpha = 1.006, R² = 1.000, runs p = 0.78   -> 'Brownian'   OK
    #     CONFINED:        alpha = 0.000, R² = 0.903, runs p = 0.0005 -> 'subdiffusion'
    #
    # The confined bead is reported as SUBDIFFUSION with a healthy R², which a reader
    # takes as "the medium is viscoelastic / crowded". It is not: the bead is hitting a
    # wall. Different physics, wrong conclusion, and R² does not blink.
    #
    # The runs test does: a power law fitted to a plateauing curve sits systematically
    # above the data at short lags and below it at long lags, so the residual signs run
    # in blocks instead of flipping like noise.
    fit_quality = assess_fit(msd, msd_fit, n_params=3,
                             model_name="MSD power law")

    if alpha < 0.85:
        motion_type = 'subdiffusion'
    elif alpha > 1.15:
        motion_type = 'superdiffusion'
    else:
        motion_type = 'Brownian'

    # CONFINEMENT is the failure that actually matters here -- see test_confinement.
    # (The residual runs test is kept in `fit_quality` for the record, but it needs >= 8
    # residuals and the defensible lag window is often only ~6, so it usually cannot say
    # anything. It reports 'not assessed' rather than pretending.)
    confinement = test_confinement(tau, msd)
    if confinement.get('confined'):
        motion_type = 'confined (not anomalous diffusion)'
        napari_show_warning("MSD fit: " + confinement['verdict'])
    elif fit_quality.get('assessable', True) and not fit_quality['adequate']:
        motion_type = 'indeterminate (power law does not fit)'
        napari_show_warning(
            "MSD fit: " + fit_quality['verdict'] + " The power law does not describe "
            "this MSD, so alpha is not interpretable and the motion type cannot be "
            "assigned. The most common cause is CONFINEMENT -- a probe hitting the "
            "boundary of a small condensate produces a plateauing MSD, which a power law "
            "fits with a spuriously small exponent and reports as 'subdiffusion'. That "
            "is a wall, not viscoelasticity. Check the probe is sampling the bulk.")

    return dict(
        D_um2_per_s=D,
        alpha=alpha,
        motion_type=motion_type,
        r_squared=float(r2),
        # Adequacy travels WITH alpha: an R² of 0.90 on a power law that cannot describe
        # a plateauing MSD must not be readable without the evidence that it is wrong.
        fit_quality=fit_quality,
        fit_adequate=bool(fit_quality['adequate']),
        confinement=confinement,
        confined=bool(confinement.get('confined', False)),
        localization_error_nm=(float(sigma_loc_um * 1000.0)
                               if np.isfinite(sigma_loc_um) else float('nan')),
        fit_lags_s=tau_fit,
        fit_msd=msd_fit,
        log_log_slope=a_ll,
        log_log_intercept=log_intercept,
        fit_window_s=(lag_lo, lag_hi),
        fit_window_warning=window_warning,
    )


def msd_per_track(
    tracks_df: pd.DataFrame,
    frame_interval_s: float = 1.0,
    min_track_length: int = 5,
) -> pd.DataFrame:
    """
    Fit anomalous diffusion to each individual track.

    Returns DataFrame with columns:
        track_id, n_frames, D_um2_per_s, alpha, motion_type, r_squared
    """
    rows = []
    for tid, grp in tracks_df.groupby('track_id'):
        if tid < 0 or len(grp) < min_track_length:
            continue
        grp = grp.sort_values('frame')
        # Build single-track MSD
        y, x, t = (grp['y_um'].values, grp['x_um'].values,
                    grp['frame'].values)
        max_lag = max(1, len(t) // 4)
        lag_vals = {}
        for lag in range(1, max_lag + 1):
            disps = [
                (y[j]-y[i])**2 + (x[j]-x[i])**2
                for i in range(len(t))
                for j in range(i+1, len(t))
                if t[j]-t[i] == lag
            ]
            if disps:
                lag_vals[lag] = np.mean(disps)

        if len(lag_vals) < 3:
            continue
        msd_df = pd.DataFrame({
            'lag_frames': list(lag_vals.keys()),
            'lag_s':      [k * frame_interval_s for k in lag_vals],
            'msd_um2':    list(lag_vals.values()),
            'n_pairs':    [10] * len(lag_vals),  # dummy for filter
        })
        fit = fit_anomalous_diffusion(msd_df)
        rows.append({
            'track_id':    int(tid),
            'n_frames':    len(grp),
            'D_um2_per_s': fit['D_um2_per_s'],
            'alpha':       fit['alpha'],
            'motion_type': fit['motion_type'],
            'r_squared':   fit['r_squared'],
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 2. Intensity histogram decomposition
# ---------------------------------------------------------------------------

def fit_bimodal_intensity(
    image: np.ndarray,
    cell_mask: np.ndarray,
    n_bins: int = 256,
    min_dense_fraction: float = 0.01,
) -> dict:
    """
    Fit a bimodal Gaussian mixture to the pixel intensity distribution
    within a cell, extracting dilute and dense phase intensities.

    The two Gaussians represent:
      G1: dilute phase (background cytoplasm / nucleoplasm)
      G2: dense phase (condensate interior)

    Parameters
    ----------
    image : (H, W) float32 in [0, 1]
    cell_mask : (H, W) binary mask for the cell
    n_bins : histogram bins
    min_dense_fraction : minimum expected dense-phase pixel fraction.
        If the fit places fewer pixels in G2, G2 is not returned.

    Returns
    -------
    dict with keys:
        dilute_mean    : mean intensity of dilute phase (proxy for C_sat)
        dilute_std     : std of dilute phase
        dense_mean     : mean intensity of dense phase (proxy for C_dense)
        dense_std      : std of dense phase
        dense_fraction : fraction of cell pixels classified as dense phase
        partition_coeff: dense_mean / dilute_mean (intensity partition coeff)
        fit_success    : bool
        histogram_x    : bin centres (for plotting)
        histogram_y    : normalised counts (for plotting)
        fit_y          : fitted bimodal curve
        fit_y1         : dilute-phase Gaussian component
        fit_y2         : dense-phase Gaussian component
    """
    pixels = image[cell_mask > 0].ravel()
    if len(pixels) < 100:
        return dict(fit_success=False)

    counts, edges = np.histogram(pixels, bins=n_bins, density=True)
    centres = 0.5 * (edges[:-1] + edges[1:])

    # Initial guesses: dilute at 10th percentile, dense at 90th percentile
    p10 = float(np.percentile(pixels, 10))
    p90 = float(np.percentile(pixels, 90))

    def bimodal(x, a1, m1, s1, a2, m2, s2):
        g1 = a1 * np.exp(-0.5 * ((x - m1) / (s1 + 1e-9))**2)
        g2 = a2 * np.exp(-0.5 * ((x - m2) / (s2 + 1e-9))**2)
        return g1 + g2

    p0 = [counts.max() * 0.8, p10, 0.05,
          counts.max() * 0.2, p90, 0.05]
    bounds = ([0, 0, 1e-4, 0, 0, 1e-4],
              [np.inf, 1, 1,  np.inf, 1, 1])

    try:
        popt, _ = optimize.curve_fit(bimodal, centres, counts,
                                      p0=p0, bounds=bounds, maxfev=5000)
        a1, m1, s1, a2, m2, s2 = popt

        # Ensure G1 is dilute (lower mean) and G2 is dense (higher mean)
        if m1 > m2:
            a1, m1, s1, a2, m2, s2 = a2, m2, s2, a1, m1, s1

        # Classify pixels
        g1_resp = a1 * np.exp(-0.5*((pixels - m1)/max(s1, 1e-9))**2)
        g2_resp = a2 * np.exp(-0.5*((pixels - m2)/max(s2, 1e-9))**2)
        dense_px = (g2_resp > g1_resp).sum()
        dense_frac = dense_px / len(pixels)

        y_fit = bimodal(centres, *popt)
        y1    = a1 * np.exp(-0.5*((centres - m1)/max(s1, 1e-9))**2)
        y2    = a2 * np.exp(-0.5*((centres - m2)/max(s2, 1e-9))**2)

        return dict(
            dilute_mean=float(m1),
            dilute_std=float(s1),
            dense_mean=float(m2),
            dense_std=float(s2),
            dense_fraction=float(dense_frac),
            partition_coeff=float(m2 / max(m1, 1e-9)),
            fit_success=dense_frac >= min_dense_fraction,
            histogram_x=centres,
            histogram_y=counts,
            fit_y=y_fit,
            fit_y1=y1,
            fit_y2=y2,
        )
    except Exception:
        return dict(fit_success=False)


def intensity_decomposition_per_cell(
    image: np.ndarray,
    labeled_cells: np.ndarray,
    microns_per_pixel: float = 1.0,
) -> pd.DataFrame:
    """Run bimodal intensity decomposition for each labeled cell."""
    import skimage as sk
    rows = []
    for prop in sk.measure.regionprops(labeled_cells):
        cmask = (labeled_cells == prop.label)
        result = fit_bimodal_intensity(image, cmask)
        row = {'cell_label': prop.label,
               'cell_area_um2': prop.area * microns_per_pixel**2}
        if result.get('fit_success'):
            row.update({k: result[k] for k in
                        ('dilute_mean','dilute_std','dense_mean',
                         'dense_std','dense_fraction','partition_coeff')})
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. Fusion kinetics — aspect ratio relaxation
# ---------------------------------------------------------------------------

def fit_aspect_ratio_relaxation(
    time_s: np.ndarray,
    aspect_ratio: np.ndarray,
    t0_frame: int = 0,
    characteristic_length_um: float = None,
) -> dict:
    """
    Fit exponential decay of aspect ratio after a merge event.

    Model: AR(t) = 1 + (AR_0 − 1) · exp(−t / τ)
    The relaxation time τ = (η/γ)·R (the capillary/visco-capillary time), where
    R is the characteristic length of the fused droplet. τ alone gives only the
    timescale; to recover the material ratio η/γ (the inverse capillary
    velocity, in s/µm) you must divide by R.

    Parameters
    ----------
    time_s : array of time values in seconds (starting from merge event)
    aspect_ratio : array of aspect ratio values (major_axis / minor_axis)
    t0_frame : frame index within time_s where merge occurred
    characteristic_length_um : optional droplet length scale R (µm), e.g. the
        radius of the fused droplet. If given, η/γ = τ / R is returned.

    Returns
    -------
    dict with keys: tau_s, AR_0, r_squared, fit_ar, fit_success,
        characteristic_length_um, eta_over_gamma_s_per_um (η/γ, or NaN if R
        was not provided).
    """
    t = time_s[t0_frame:]
    ar = aspect_ratio[t0_frame:]
    R = characteristic_length_um
    if len(t) < 4 or ar[0] <= 1.0:
        return dict(tau_s=np.nan, AR_0=np.nan, r_squared=np.nan,
                    fit_ar=np.array([]), fit_success=False,
                    characteristic_length_um=R,
                    eta_over_gamma_s_per_um=np.nan)

    def model(t, AR_0, tau):
        return 1 + (AR_0 - 1) * np.exp(-t / max(tau, 1e-9))

    try:
        p0 = [ar[0], float(t[-1] / 3)]
        bounds = ([1.0, 1e-4], [20.0, float(t[-1]) * 10])
        popt, _ = optimize.curve_fit(model, t - t[0], ar,
                                      p0=p0, bounds=bounds, maxfev=3000)
        AR_0, tau = popt
        ar_fit  = model(t - t[0], *popt)
        ss_res  = np.sum((ar - ar_fit)**2)
        ss_tot  = np.sum((ar - ar.mean())**2)
        r2      = 1 - ss_res / max(ss_tot, 1e-12)
        eta_over_gamma = (float(tau) / R) if (R and R > 0) else np.nan
        return dict(tau_s=float(tau), AR_0=float(AR_0), r_squared=float(r2),
                    fit_ar=ar_fit, fit_success=r2 > 0.5,
                    characteristic_length_um=R,
                    eta_over_gamma_s_per_um=eta_over_gamma)
    except Exception:
        return dict(tau_s=np.nan, AR_0=np.nan, r_squared=np.nan,
                    fit_ar=np.array([]), fit_success=False,
                    characteristic_length_um=R,
                    eta_over_gamma_s_per_um=np.nan)


# Backward-compatible alias. NOTE: this fits IMAGE aspect-ratio relaxation of a
# merge event; it is distinct from fusion_tools.fit_fusion_relaxation, which
# fits the C-Trap FORCE model S(t)=a*exp(-t/tau)+b*t+d. Prefer the explicit
# name fit_aspect_ratio_relaxation to avoid confusing the two.
fit_fusion_relaxation = fit_aspect_ratio_relaxation


# ---------------------------------------------------------------------------
# 4. Coarsening kinetics
# ---------------------------------------------------------------------------

def fit_coarsening(
    time_s: np.ndarray,
    mean_radius_um: np.ndarray,
) -> dict:
    """
    Fit mean condensate radius vs time to distinguish coarsening mechanisms.

    Models:
      Ostwald ripening:  R(t) = R_0 + K·t^(1/3)
      Coalescence:       R(t) = R_0 + K·t^(1/2)
      Arrested:          R(t) ≈ const

    The model with the best R² is reported as the preferred mechanism.

    Parameters
    ----------
    time_s : array of time values (seconds)
    mean_radius_um : array of mean condensate radii (µm)

    Returns
    -------
    dict with keys:
        preferred_mechanism : 'ostwald_ripening' | 'coalescence' | 'arrested'
        mechanism_confidence : 'high' | 'low' | 'n/a (arrested)'
        mechanism_caveat     : plain-language reliability note
        ostwald_r2, coalescence_r2
        radius_change_um, radius_change_frac
        ostwald_K, coalescence_K (rate constants)
        R0 : initial radius
        fit_radii_ostwald, fit_radii_coalescence
    """
    if len(time_s) < 4:
        return dict(preferred_mechanism='insufficient_data')

    R = mean_radius_um
    t = time_s

    def ostwald(t, R0, K):
        return R0 + K * t**(1/3)

    def coalescence(t, R0, K):
        return R0 + K * t**(1/2)

    results = {}
    for name, fn in [('ostwald', ostwald), ('coalescence', coalescence)]:
        try:
            p0 = [R[0], (R[-1] - R[0]) / max(t[-1]**(1/3 if name=='ostwald' else 1/2), 1e-9)]
            popt, _ = optimize.curve_fit(fn, t, R, p0=p0, maxfev=3000)
            R_fit = fn(t, *popt)
            ss_res = np.sum((R - R_fit)**2)
            ss_tot = np.sum((R - R.mean())**2)
            r2 = 1 - ss_res / max(ss_tot, 1e-12)
            results[name] = {'r2': float(r2), 'K': float(popt[1]),
                              'R0': float(popt[0]), 'fit': R_fit}
        except Exception:
            results[name] = {'r2': -np.inf, 'K': np.nan, 'R0': np.nan, 'fit': np.full_like(t, np.nan)}

    # "Arrested" is not a power law, so an R² against the mean is meaningless.
    # Instead judge it by how little the radius actually grows: if the total
    # change is small relative to the scatter, growth is effectively arrested.
    R = np.asarray(R, dtype=float)
    radius_change = float(R[-1] - R[0])
    radius_change_frac = radius_change / R[0] if R[0] else np.nan
    noise = float(np.std(np.diff(R))) if len(R) > 2 else 0.0
    is_arrested = (max(results['ostwald']['r2'], results['coalescence']['r2']) < 0.3
                   or abs(radius_change) < 2.0 * noise)

    best = max(['ostwald', 'coalescence'], key=lambda k: results[k]['r2'])
    if is_arrested:
        best = 'arrested'

    # Discrimination confidence: t^(1/3) and t^(1/2) are both concave-increasing
    # and hard to separate over a short time range, so only trust the mechanism
    # call when one fit is clearly better AND the winner fits well.
    r2_gap = abs(results['ostwald']['r2'] - results['coalescence']['r2'])
    best_r2 = max(results['ostwald']['r2'], results['coalescence']['r2'])
    if best == 'arrested':
        confidence = 'n/a (arrested)'
        caveat = ("Radius barely changes — growth is effectively arrested; "
                  "no coarsening exponent is fitted.")
    elif r2_gap > 0.1 and best_r2 > 0.85:
        confidence = 'high'
        caveat = ""
    else:
        confidence = 'low'
        caveat = ("t^(1/3) (Ostwald) and t^(1/2) (coalescence) fit similarly "
                  "over this time range; the preferred mechanism is suggestive, "
                  "not definitive. Extend the time range to discriminate.")

    return dict(
        preferred_mechanism=best,
        mechanism_confidence=confidence,
        mechanism_caveat=caveat,
        ostwald_r2=results['ostwald']['r2'],
        coalescence_r2=results['coalescence']['r2'],
        radius_change_um=radius_change,
        radius_change_frac=radius_change_frac,
        ostwald_K=results['ostwald']['K'],
        coalescence_K=results['coalescence']['K'],
        R0=results.get(best, {}).get('R0', R[0]),
        fit_radii_ostwald=results['ostwald']['fit'],
        fit_radii_coalescence=results['coalescence']['fit'],
    )


# ---------------------------------------------------------------------------
# 5. Photobleaching correction
# ---------------------------------------------------------------------------

def fit_photobleaching(
    mean_intensities: np.ndarray,
    frame_interval_s: float = 1.0,
) -> dict:
    """
    Fit exponential decay to mean cell fluorescence to model photobleaching.

    Model: I(t) = I_0 · exp(−t/τ_bleach) + I_inf
    (I_inf accounts for a non-bleachable population)

    Parameters
    ----------
    mean_intensities : array of mean fluorescence per frame (whole cell)
    frame_interval_s : physical time per frame

    Returns
    -------
    dict with keys:
        I0, tau_bleach_s, I_inf, r_squared, fit_success,
        correction_factors : array of I0/I(t) to multiply each frame by
    """
    t = np.arange(len(mean_intensities)) * frame_interval_s
    I = mean_intensities.astype(np.float64)

    def model(t, I0, tau, I_inf):
        return I0 * np.exp(-t / max(tau, 1e-6)) + I_inf

    try:
        I_inf_est = float(I[-len(I)//4:].mean())
        I0_est    = float(I[0]) - I_inf_est
        tau_est   = float(t[-1] / 3)
        p0 = [max(I0_est, 0.01), tau_est, max(I_inf_est, 0.0)]
        bounds = ([0, 1e-3, 0], [I.max()*2, t[-1]*100, I.max()])
        popt, _ = optimize.curve_fit(model, t, I, p0=p0,
                                      bounds=bounds, maxfev=5000)
        I0, tau, I_inf = popt
        I_fit = model(t, *popt)
        ss_res = np.sum((I - I_fit)**2)
        ss_tot = np.sum((I - I.mean())**2)
        r2 = 1 - ss_res / max(ss_tot, 1e-12)

        # Correction factors: multiply frame t by I(0)/I(t)
        correction = I_fit[0] / np.maximum(I_fit, 1e-9)

        return dict(I0=float(I0), tau_bleach_s=float(tau),
                    I_inf=float(I_inf), r_squared=float(r2),
                    fit_success=r2 > 0.7,
                    fit_intensities=I_fit,
                    correction_factors=correction.astype(np.float32))
    except Exception:
        return dict(fit_success=False,
                    correction_factors=np.ones(len(I), dtype=np.float32))


def apply_bleach_correction(
    stack: np.ndarray,
    correction_factors: np.ndarray,
) -> np.ndarray:
    """
    Apply per-frame bleaching correction factors to a (T, H, W) stack.

    Parameters
    ----------
    stack : (T, H, W) float32 image stack
    correction_factors : (T,) array of multiplicative correction factors

    Returns
    -------
    (T, H, W) corrected stack, clipped to [0, 1]
    """
    corrected = stack.copy()
    for t in range(min(len(correction_factors), stack.shape[0])):
        corrected[t] = np.clip(stack[t] * correction_factors[t], 0, 1)
    return corrected


# ---------------------------------------------------------------------------
# 6. Frame quality analysis — bleaching vs focal drift discrimination
# ---------------------------------------------------------------------------
#
# Bleaching and focal drift are often confused because both cause apparent
# intensity loss over time.  They have distinct signatures:
#
#   Metric              Bleaching        Focal drift      Both
#   ─────────────────── ──────────────── ──────────────── ──────────
#   Mean intensity      ↓ (exponential)  stable / slow ↓  ↓
#   Laplacian variance  stable           ↓               ↓
#   Image entropy       stable           ↓               ↓
#   Gradient energy     stable           ↓               ↓
#
# Entropy is particularly useful because:
#   - It measures the information content of the pixel distribution.
#   - In-focus images have high entropy (many distinct intensity levels
#     from sharp edges and fine structure).
#   - Blurry images have low entropy (pixel values blur toward a mean,
#     compressing the distribution).
#   - Unlike Laplacian variance it is robust to shot noise in dim frames,
#     which can artificially inflate Laplacian variance at low SNR.
#
# The combined QC report:
#   1. Per-frame: mean_intensity, laplacian_variance, image_entropy,
#      gradient_energy (Sobel magnitude mean)
#   2. Trend fits: exponential to intensity (bleaching τ), linear to
#      Laplacian and entropy (drift slope)
#   3. Classification: 'bleaching_only', 'drift_only', 'both', 'clean',
#      'low_snr' (intensity drops but sharpness stable — could be either)
#   4. Per-frame flags: is_blurry, is_bleached, cause


def _frame_entropy(frame: np.ndarray, n_bins: int = 64) -> float:
    """
    Shannon entropy of the pixel intensity distribution of one frame.
    Uses n_bins histogram bins; higher entropy = more information content.
    Normalised to [0, log2(n_bins)] so values are comparable across bit depths.
    """
    counts, _ = np.histogram(frame.ravel(), bins=n_bins,
                               range=(0.0, 1.0), density=False)
    p = counts / (counts.sum() + 1e-12)
    p = p[p > 0]
    return float(-np.sum(p * np.log2(p)))


def _frame_gradient_energy(frame: np.ndarray) -> float:
    """
    Mean Sobel gradient magnitude — measures average edge strength.
    High = sharp edges (in focus); low = blurry (out of focus / drift).
    More noise-resistant than Laplacian variance for dim frames.
    """
    gy = ndimage.sobel(frame, axis=0)
    gx = ndimage.sobel(frame, axis=1)
    return float(np.sqrt(gy**2 + gx**2).mean())


def _fit_linear_trend(values: np.ndarray) -> dict:
    """Fit a linear trend and return slope, intercept, r²."""
    t = np.arange(len(values), dtype=float)
    slope, intercept, r, _, _ = stats.linregress(t, values)
    return dict(slope=float(slope), intercept=float(intercept),
                r_squared=float(r**2))


def analyse_frame_quality(
    stack: np.ndarray,
    frame_interval_s: float = 1.0,
    threshold_fraction: float = 0.3,
    entropy_bins: int = 64,
    bleach_r2_min: float = 0.70,
    drift_slope_threshold: float = -0.02,
) -> dict:
    """
    Comprehensive per-frame quality analysis distinguishing photobleaching
    from focal drift using a multi-metric approach.

    Parameters
    ----------
    stack : (T, H, W) float32 stack, values in [0, 1]
    frame_interval_s : physical time per frame in seconds
    threshold_fraction : frames with Laplacian variance or entropy below
        this fraction of their median are flagged as blurry
    entropy_bins : number of histogram bins for entropy calculation
    bleach_r2_min : minimum R² for exponential fit to declare bleaching
    drift_slope_threshold : normalised linear slope below which entropy/
        Laplacian trends are called drift (negative = declining sharpness)

    Returns
    -------
    dict with keys:

    per_frame_df : DataFrame with one row per frame containing:
        frame, time_s, mean_intensity, laplacian_variance, image_entropy,
        gradient_energy, is_blurry, is_bleached, cause

    summary : dict with keys:
        dominant_cause : 'clean' | 'bleaching_only' | 'drift_only' |
                         'both' | 'undetermined'
        bleach_tau_s   : photobleaching time constant (s), or NaN
        bleach_r2      : R² of exponential fit to mean intensity
        drift_entropy_slope    : normalised linear slope of entropy
        drift_laplacian_slope  : normalised linear slope of Laplacian variance
        n_blurry_frames        : count of frames flagged as blurry
        n_bleached_frames      : count of frames flagged as bleached
        recommendation         : human-readable action string

    bleach_correction_factors : (T,) array of multiplicative factors to
        correct photobleaching.  All ones if bleaching not detected.

    bleach_fit : full fit result dict from fit_photobleaching()
    """
    n_frames = stack.shape[0]
    t_arr    = np.arange(n_frames) * frame_interval_s

    # ── Per-frame metrics ────────────────────────────────────────────────
    mean_int, lap_var, entropy, grad_en = [], [], [], []
    for t in range(n_frames):
        frame = stack[t].astype(np.float32)
        mean_int.append(float(frame.mean()))
        lap      = ndimage.laplace(frame)
        lap_var.append(float(lap.var()))
        entropy.append(_frame_entropy(frame, entropy_bins))
        grad_en.append(_frame_gradient_energy(frame))

    mean_int = np.array(mean_int)
    lap_var  = np.array(lap_var)
    entropy  = np.array(entropy)
    grad_en  = np.array(grad_en)

    # ── Bleaching: exponential fit to mean intensity ─────────────────────
    bleach_fit = fit_photobleaching(mean_int, frame_interval_s)
    bleach_tau = bleach_fit.get('tau_bleach_s', np.nan) if bleach_fit.get('fit_success') else np.nan
    bleach_r2  = bleach_fit.get('r_squared', 0.0)
    correction = bleach_fit.get('correction_factors',
                                  np.ones(n_frames, dtype=np.float32))

    # Flag bleached frames: intensity < threshold × initial intensity
    bleach_threshold = mean_int[0] * threshold_fraction
    is_bleached = mean_int < bleach_threshold

    # ── Focal drift: linear trend in entropy and Laplacian variance ──────
    # Normalise slopes to [0,1] range of each metric so thresholds are
    # comparable regardless of absolute image brightness
    def _norm_slope(arr):
        rng = max(arr.max() - arr.min(), 1e-12)
        trend = _fit_linear_trend(arr / rng)
        return trend['slope'] * n_frames   # total fractional change over movie

    lap_norm_slope     = _norm_slope(lap_var)
    entropy_norm_slope = _norm_slope(entropy)

    # Blurry frames: Laplacian variance AND entropy both below threshold
    lap_med  = np.median(lap_var)
    ent_med  = np.median(entropy)
    is_blurry_lap = lap_var < lap_med * threshold_fraction
    is_blurry_ent = entropy  < ent_med * threshold_fraction
    # Require both metrics to agree to reduce false positives from
    # genuinely dim/sparse frames that have low Laplacian by chance
    is_blurry = is_blurry_lap & is_blurry_ent

    # ── Cause classification ─────────────────────────────────────────────
    has_bleaching = bleach_r2 >= bleach_r2_min
    has_drift = (lap_norm_slope < drift_slope_threshold or
                 entropy_norm_slope < drift_slope_threshold)

    if has_bleaching and has_drift:
        dominant_cause = 'both'
    elif has_bleaching:
        dominant_cause = 'bleaching_only'
    elif has_drift:
        dominant_cause = 'drift_only'
    elif is_blurry.any():
        dominant_cause = 'undetermined'   # some frames blurry but no clear trend
    else:
        dominant_cause = 'clean'

    # Per-frame cause string
    causes = []
    for i in range(n_frames):
        parts = []
        if is_bleached[i]: parts.append('bleached')
        if is_blurry[i]:   parts.append('blurry')
        causes.append('+'.join(parts) if parts else 'ok')

    # ── Recommendation ───────────────────────────────────────────────────
    recs = {
        'clean':          'No correction needed.',
        'bleaching_only': f'Apply photobleaching correction (τ={bleach_tau:.0f}s). '                            'Multiply each frame by bleach_correction_factors.',
        'drift_only':     'Focal drift detected (declining sharpness without bleaching). '                            'Consider re-acquisition or z-correction. '                            'Flag blurry frames for exclusion.',
        'both':           f'Both bleaching (τ={bleach_tau:.0f}s) and focal drift detected. '                            'Apply bleaching correction first, then exclude blurry frames.',
        'undetermined':   'Some blurry frames detected but cause is unclear. '                            'Inspect individual frames and exclude is_blurry==True.',
    }

    per_frame_df = pd.DataFrame({
        'frame':              np.arange(n_frames),
        'time_s':             t_arr,
        'mean_intensity':     mean_int,
        'laplacian_variance': lap_var,
        'image_entropy':      entropy,
        'gradient_energy':    grad_en,
        'is_blurry':          is_blurry,
        'is_bleached':        is_bleached,
        'cause':              causes,
    })

    summary = dict(
        dominant_cause=dominant_cause,
        bleach_tau_s=bleach_tau,
        bleach_r2=bleach_r2,
        drift_entropy_slope=entropy_norm_slope,
        drift_laplacian_slope=lap_norm_slope,
        n_blurry_frames=int(is_blurry.sum()),
        n_bleached_frames=int(is_bleached.sum()),
        recommendation=recs[dominant_cause],
    )

    return dict(
        per_frame_df=per_frame_df,
        summary=summary,
        bleach_correction_factors=correction,
        bleach_fit=bleach_fit,
    )


# Keep the original function as a thin wrapper for backward compatibility
def detect_out_of_focus(
    stack: np.ndarray,
    threshold_fraction: float = 0.3,
) -> pd.DataFrame:
    """
    Detect blurry / out-of-focus frames.  Thin wrapper around
    analyse_frame_quality() for backward compatibility.

    Returns DataFrame with columns:
        frame, laplacian_variance, image_entropy, gradient_energy,
        is_blurry, quality_score
    """
    result = analyse_frame_quality(stack, threshold_fraction=threshold_fraction)
    df = result['per_frame_df'].copy()
    lap_med = float(df['laplacian_variance'].median())
    df['quality_score'] = df['laplacian_variance'] / max(lap_med, 1e-12)
    return df[['frame','laplacian_variance','image_entropy',
               'gradient_energy','is_blurry','quality_score']]


# ---------------------------------------------------------------------------
# 7. Survival analysis (Kaplan-Meier) for condensate lifetimes
# ---------------------------------------------------------------------------

def kaplan_meier_lifetimes(
    tracks_df: pd.DataFrame,
    total_frames: int,
) -> pd.DataFrame:
    """
    Kaplan-Meier survival curve for condensate lifetimes.

    Handles censoring:
      - Condensates present at frame 0 are left-censored (unknown birth)
      - Condensates still present at the last frame are right-censored
      - Only condensates with both birth and death observed are uncensored

    Parameters
    ----------
    tracks_df : linked trajectories DataFrame (track_id, frame columns)
    total_frames : total number of frames in the movie

    Returns
    -------
    DataFrame with columns: time_frames, survival_probability,
                             n_at_risk, n_events, n_censored
    Plus attrs: median_lifetime_frames, mean_lifetime_frames
    """
    lifetimes = []   # (duration, censored)
    for tid, grp in tracks_df.groupby('track_id'):
        if tid < 0:
            continue
        grp = grp.sort_values('frame')
        t_start = int(grp['frame'].min())
        t_end   = int(grp['frame'].max())
        duration = t_end - t_start + 1
        # Right-censored: track ends at last frame (may still be alive)
        censored = (t_end >= total_frames - 1)
        lifetimes.append((duration, censored))

    if not lifetimes:
        return pd.DataFrame()

    # KM estimator
    lifetimes.sort(key=lambda x: x[0])
    durations  = np.array([l[0] for l in lifetimes])
    is_censored = np.array([l[1] for l in lifetimes])

    unique_times = np.unique(durations[~is_censored])
    n_total      = len(lifetimes)

    S     = 1.0   # survival probability
    rows  = [{'time_frames': 0, 'survival_probability': 1.0,
               'n_at_risk': n_total, 'n_events': 0, 'n_censored': 0}]
    n_at_risk = n_total

    for t in unique_times:
        n_events   = int(np.sum((durations == t) & ~is_censored))
        n_censored = int(np.sum((durations == t) & is_censored))
        if n_at_risk > 0 and n_events > 0:
            S *= (1 - n_events / n_at_risk)
        rows.append({'time_frames': int(t), 'survival_probability': S,
                      'n_at_risk': n_at_risk, 'n_events': n_events,
                      'n_censored': n_censored})
        n_at_risk -= (n_events + n_censored)

    df = pd.DataFrame(rows)

    # Median: time at which S drops below 0.5
    below = df[df['survival_probability'] <= 0.5]
    median_lt = float(below['time_frames'].iloc[0]) if len(below) else np.nan
    df.attrs['median_lifetime_frames'] = median_lt
    df.attrs['mean_lifetime_frames']   = float(durations.mean())
    return df


# ---------------------------------------------------------------------------
# Per-track MSD curves + microrheology moduli (for plotting)
# ---------------------------------------------------------------------------

def per_track_msd_curves(
    tracks_df: pd.DataFrame,
    max_lag: int = None,
    frame_interval_s: float = 1.0,
    min_track_length: int = 5,
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
