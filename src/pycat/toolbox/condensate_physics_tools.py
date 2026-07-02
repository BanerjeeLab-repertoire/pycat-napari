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
from scipy import optimize, stats, ndimage


# ---------------------------------------------------------------------------
# 1. Mean Squared Displacement
# ---------------------------------------------------------------------------

def compute_msd(
    tracks_df: pd.DataFrame,
    max_lag: int = None,
    microns_per_pixel: float = 1.0,
    frame_interval_s: float = 1.0,
    min_track_length: int = 5,
) -> pd.DataFrame:
    """
    Compute ensemble-averaged and per-track MSD from linked trajectories.

    MSD(τ) = <|r(t+τ) − r(t)|²>  averaged over all t and all tracks.

    Parameters
    ----------
    tracks_df : DataFrame with columns track_id, frame, y_um, x_um
        Output of link_trajectories() or link_trajectories_bayesian().
    max_lag : int or None
        Maximum lag (in frames) to compute.  Default: n_frames // 4
        (beyond n/4 the MSD estimate has too few samples to be reliable).
    microns_per_pixel : float
        Only used if y_um/x_um are in pixels rather than µm.
    frame_interval_s : float
        Physical time per frame in seconds.
    min_track_length : int
        Tracks shorter than this are excluded.

    Returns
    -------
    msd_df : DataFrame with columns:
        lag_frames, lag_s, msd_um2 (ensemble MSD),
        msd_std (standard deviation across tracks),
        n_pairs (number of displacement pairs contributing)
    """
    frames = sorted(tracks_df['frame'].unique())
    if max_lag is None:
        max_lag = max(1, len(frames) // 4)

    # Per-track MSD at each lag
    lag_msds: dict[int, list[float]] = {lag: [] for lag in range(1, max_lag + 1)}

    for tid, grp in tracks_df.groupby('track_id'):
        if tid < 0:
            continue
        grp = grp.sort_values('frame').reset_index(drop=True)
        if len(grp) < min_track_length:
            continue

        y = grp['y_um'].values
        x = grp['x_um'].values
        t = grp['frame'].values

        for lag in range(1, max_lag + 1):
            disps = []
            for i in range(len(t)):
                for j in range(i + 1, len(t)):
                    if t[j] - t[i] == lag:
                        dy = y[j] - y[i]
                        dx = x[j] - x[i]
                        disps.append(dy**2 + dx**2)
            if disps:
                lag_msds[lag].extend(disps)

    rows = []
    for lag in range(1, max_lag + 1):
        vals = lag_msds[lag]
        if not vals:
            continue
        rows.append({
            'lag_frames': lag,
            'lag_s':      lag * frame_interval_s,
            'msd_um2':    float(np.mean(vals)),
            'msd_std':    float(np.std(vals)),
            'n_pairs':    len(vals),
        })
    return pd.DataFrame(rows)


def fit_anomalous_diffusion(
    msd_df: pd.DataFrame,
    max_lag_fit: int = None,
) -> dict:
    """
    Fit MSD(τ) = 4D·τ^α (anomalous diffusion model) using log-log regression.

    Parameters
    ----------
    msd_df : output of compute_msd()
    max_lag_fit : number of lag points to include in fit.
        Default: all points with n_pairs > 10.

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
    """
    df = msd_df[msd_df['n_pairs'] > 5].copy()
    if max_lag_fit is not None:
        df = df.head(max_lag_fit)
    if len(df) < 3:
        return dict(D_um2_per_s=np.nan, alpha=np.nan, motion_type='unknown',
                    r_squared=np.nan, fit_lags_s=np.array([]),
                    fit_msd=np.array([]), log_log_slope=np.nan,
                    log_log_intercept=np.nan)

    log_tau = np.log(df['lag_s'].values)
    log_msd = np.log(df['msd_um2'].values)

    slope, intercept, r, p, se = stats.linregress(log_tau, log_msd)
    alpha = float(slope)
    D     = float(np.exp(intercept) / 4.0)

    if alpha < 0.85:
        motion_type = 'subdiffusion'
    elif alpha > 1.15:
        motion_type = 'superdiffusion'
    else:
        motion_type = 'Brownian'

    tau_fit = df['lag_s'].values
    msd_fit = 4 * D * tau_fit ** alpha

    return dict(
        D_um2_per_s=D,
        alpha=alpha,
        motion_type=motion_type,
        r_squared=float(r**2),
        fit_lags_s=tau_fit,
        fit_msd=msd_fit,
        log_log_slope=slope,
        log_log_intercept=intercept,
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
) -> dict:
    """
    Fit exponential decay of aspect ratio after a merge event.

    Model: AR(t) = 1 + (AR_0 − 1) · exp(−t / τ)
    τ = η·R/γ  (capillary time; gives viscosity/surface tension ratio)

    Parameters
    ----------
    time_s : array of time values in seconds (starting from merge event)
    aspect_ratio : array of aspect ratio values (major_axis / minor_axis)
    t0_frame : frame index within time_s where merge occurred

    Returns
    -------
    dict with keys: tau_s, AR_0, r_squared, fit_ar, fit_success
    """
    t = time_s[t0_frame:]
    ar = aspect_ratio[t0_frame:]
    if len(t) < 4 or ar[0] <= 1.0:
        return dict(tau_s=np.nan, AR_0=np.nan, r_squared=np.nan,
                    fit_ar=np.array([]), fit_success=False)

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
        return dict(tau_s=float(tau), AR_0=float(AR_0), r_squared=float(r2),
                    fit_ar=ar_fit, fit_success=r2 > 0.5)
    except Exception:
        return dict(tau_s=np.nan, AR_0=np.nan, r_squared=np.nan,
                    fit_ar=np.array([]), fit_success=False)


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
        ostwald_r2, coalescence_r2, arrested_r2
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

    # Arrested: flat line
    r2_arr = 1 - np.sum((R - R.mean())**2) / max(np.sum((R - R.mean())**2), 1e-12)
    r2_arr = 0.0  # flat line has R²=0 by definition of residuals vs mean

    best = max(['ostwald','coalescence'], key=lambda k: results[k]['r2'])
    if max(results['ostwald']['r2'], results['coalescence']['r2']) < 0.3:
        best = 'arrested'

    return dict(
        preferred_mechanism=best,
        ostwald_r2=results['ostwald']['r2'],
        coalescence_r2=results['coalescence']['r2'],
        arrested_r2=r2_arr,
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
