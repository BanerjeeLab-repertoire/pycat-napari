"""Per-frame **quality** analysis — split out of condensate_physics_tools by quantity (1.6.218).

Discriminates photobleaching from focal drift / debris across a stack (analyse_frame_quality) and flags
out-of-focus frames (detect_out_of_focus), from per-frame entropy, gradient energy and linear-trend fits.
Moved VERBATIM — no number changed; pinned by the focus/debris tests. The tools module re-exports both.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import ndimage, stats

from pycat.utils.math_utils import robust_focus_energy, resolve_frame_mask
from pycat.toolbox.condensate_physics.photobleaching import fit_photobleaching


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


def _frame_entropy(frame: np.ndarray, n_bins: int = 64, mask: np.ndarray = None) -> float:
    """
    Shannon entropy of the pixel intensity distribution of one frame.
    Uses n_bins histogram bins; higher entropy = more information content.
    Normalised to [0, log2(n_bins)] so values are comparable across bit depths.

    ``mask`` : optional boolean array — entropy over the masked pixels only. The
    pixels are EXTRACTED (never zero-filled), so no artificial peak is added at 0.
    ``mask=None`` is byte-identical to whole-frame.
    """
    vals = frame.ravel() if mask is None else frame[mask]
    counts, _ = np.histogram(vals, bins=n_bins,
                               range=(0.0, 1.0), density=False)
    p = counts / (counts.sum() + 1e-12)
    p = p[p > 0]
    return float(-np.sum(p * np.log2(p)))


def _frame_gradient_energy(frame: np.ndarray, mask: np.ndarray = None) -> float:
    """
    Mean Sobel gradient magnitude — measures average edge strength.
    High = sharp edges (in focus); low = blurry (out of focus / drift).
    More noise-resistant than Laplacian variance for dim frames.

    ``mask`` : optional boolean array. The Sobel gradient is computed on the FULL
    real frame (so no fake edge is created at the mask boundary), then the magnitude
    is aggregated over the masked pixels only. ``mask=None`` is byte-identical.
    """
    gy = ndimage.sobel(frame, axis=0)
    gx = ndimage.sobel(frame, axis=1)
    mag = np.sqrt(gy**2 + gx**2)
    # Robust mean edge strength: an out-of-plane speck cannot hijack the argmax. See
    # `robust_focus_energy` — clean frames are unaffected, debris is trimmed.
    return robust_focus_energy(mag if mask is None else mag[mask])


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
    mask: np.ndarray = None,
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
    mask : optional focus region — a single (H, W) boolean applied to every frame,
        or a (T, H, W) per-frame stack. When given, the three FOCUS metrics
        (Laplacian variance, entropy, gradient energy) are scored over the masked
        region ONLY, so a LARGE out-of-plane structure (which `robust_focus_energy`'s
        trimming cannot reach) no longer decides "best frame". Mean intensity stays
        whole-frame (it feeds bleaching, not focus). `mask=None` is byte-identical.

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
        fm = resolve_frame_mask(mask, t, frame.shape)   # None → whole-frame
        mean_int.append(float(frame.mean()))            # whole-frame: feeds bleaching, not focus
        lap      = ndimage.laplace(frame)               # computed on the FULL frame (no fake edges)
        # Robust Laplacian variance: a bright out-of-plane speck's few large-magnitude
        # pixels no longer dominate the spread, so 'best frame' is the sample, not the dust.
        # With a mask, the spread is taken over the masked pixels only (the SPATIAL layer).
        lap_m = lap if fm is None else lap[fm]
        lap_var.append(float(robust_focus_energy((lap_m - lap_m.mean())**2))
                       if lap_m.size else 0.0)
        entropy.append(_frame_entropy(frame, entropy_bins, mask=fm))
        grad_en.append(_frame_gradient_energy(frame, mask=fm))

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
