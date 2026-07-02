"""
PyCAT Droplet Fusion Tools (C-Trap)
====================================
Measure the fusion relaxation time of two optically-trapped condensate
droplets brought into proximity on a Lumicks C-Trap, following the analysis
in Alshareedah et al., "Programmable Viscoelasticity in Protein-RNA
Condensates with Disordered Sticker-Spacer Polypeptides."

When two trapped droplets touch and coalesce, the system relaxes toward a
single spherical droplet. On the C-Trap this relaxation is recorded as a
force transient on the trapped beads (and/or an aspect-ratio change in the
image). The signal is fit to:

    S(t) = a·exp(−t/τ) + b·t + d

where
    a  = amplitude of the exponential relaxation
    τ  = fusion relaxation time  (the quantity of interest)
    b  = linear drift term (slow baseline drift / trap creep)
    d  = constant offset

The relaxation time τ scales with the inverse capillary velocity — for a
viscously-dominated coalescence τ ≈ (η/γ)·ℓ, where η is the condensate
viscosity, γ the surface tension, and ℓ a characteristic length (droplet
radius). Plotting τ vs droplet size across events gives the inverse
capillary velocity (η/γ) as the slope.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2026
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

from napari.utils.notifications import show_info as napari_show_info
from napari.utils.notifications import show_warning as napari_show_warning


# ---------------------------------------------------------------------------
# Fusion relaxation model
# ---------------------------------------------------------------------------

def fusion_relaxation_model(t, a, tau, b, d):
    """
    S(t) = a·exp(−t/τ) + b·t + d

    a   : exponential amplitude
    tau : fusion relaxation time (same units as t)
    b   : linear drift slope
    d   : constant offset
    """
    return a * np.exp(-t / tau) + b * t + d


def fit_fusion_relaxation(
    time: np.ndarray,
    signal: np.ndarray,
    t_start: Optional[float] = None,
    t_end: Optional[float] = None,
) -> dict:
    """
    Fit S(t) = a·exp(−t/τ) + b·t + d to a fusion relaxation trace.

    Parameters
    ----------
    time : 1D time array (s).
    signal : 1D signal array (force, aspect ratio, or intensity).
    t_start, t_end : optional fitting window (s). If given, only samples with
        t_start ≤ t ≤ t_end are fit — use this to isolate the fusion event
        from the surrounding baseline.

    Returns
    -------
    dict with:
        a, tau, b, d      : fit parameters
        tau_s             : relaxation time (s) — the headline result
        r_squared         : goodness of fit
        fit_time, fit_curve : arrays for plotting
        t_start, t_end    : the window actually used
    """
    t = np.asarray(time, dtype=float)
    y = np.asarray(signal, dtype=float)

    mask = np.isfinite(t) & np.isfinite(y)
    if t_start is not None:
        mask &= (t >= t_start)
    if t_end is not None:
        mask &= (t <= t_end)
    t_fit, y_fit = t[mask], y[mask]

    if len(t_fit) < 5:
        return dict(a=np.nan, tau=np.nan, b=np.nan, d=np.nan, tau_s=np.nan,
                    r_squared=np.nan, fit_time=np.array([]),
                    fit_curve=np.array([]), t_start=t_start, t_end=t_end)

    # Shift time so the fit window starts at 0 (improves conditioning)
    t0 = t_fit[0]
    tt = t_fit - t0

    # Initial guesses
    a0   = float(y_fit[0] - y_fit[-1])          # drop from start to end
    d0   = float(y_fit[-1])                     # plateau
    tau0 = max((tt[-1] - tt[0]) / 3.0, 1e-4)
    b0   = 0.0

    try:
        popt, _ = curve_fit(
            fusion_relaxation_model, tt, y_fit,
            p0=[a0, tau0, b0, d0],
            bounds=([-np.inf, 1e-5, -np.inf, -np.inf],
                    [ np.inf, 1e6,  np.inf,  np.inf]),
            maxfev=20000)
        a, tau, b, d = popt
        fit_curve = fusion_relaxation_model(tt, *popt)
        ss_res = np.sum((y_fit - fit_curve) ** 2)
        ss_tot = np.sum((y_fit - np.mean(y_fit)) ** 2)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
        return dict(
            a=float(a), tau=float(tau), b=float(b), d=float(d),
            tau_s=float(tau), r_squared=float(r2),
            fit_time=t_fit, fit_curve=fit_curve,
            t_start=float(t0), t_end=float(t_fit[-1]))
    except Exception as e:
        napari_show_warning(f"Fusion fit failed: {e}")
        return dict(a=np.nan, tau=np.nan, b=np.nan, d=np.nan, tau_s=np.nan,
                    r_squared=np.nan, fit_time=np.array([]),
                    fit_curve=np.array([]), t_start=t_start, t_end=t_end)


# ---------------------------------------------------------------------------
# Signal construction from C-Trap force channels
# ---------------------------------------------------------------------------

def combined_force_signal(fx: np.ndarray, fy: np.ndarray) -> np.ndarray:
    """
    Combine x/y force components into a magnitude |F| = sqrt(fx² + fy²).
    Use when the fusion axis is not aligned with a single force axis.
    """
    return np.sqrt(np.asarray(fx, float) ** 2 + np.asarray(fy, float) ** 2)


def build_fusion_signal_from_forces(
    force_traces: dict,
    which: str = 'F1x',
    sample_rate_hz: Optional[float] = None,
) -> tuple:
    """
    Build a (time, signal) pair from a dict of C-Trap force channels.

    Parameters
    ----------
    force_traces : dict with any of keys 'F1x','F1y','F2x','F2y' (1D arrays),
        as returned by load_lumicks_fusion().
    which : which signal to fit —
        'F1x','F1y','F2x','F2y' : a single force channel
        'F1' / 'F2'             : magnitude of bead 1 / bead 2 force
        'sum'                   : sum of both bead force magnitudes
    sample_rate_hz : sampling rate of the force channel (Hz). If given, a
        real time axis is built; otherwise time is the sample index.

    Returns
    -------
    (time, signal) arrays.
    """
    def _get(k):
        v = force_traces.get(k)
        return np.asarray(v, float) if v is not None else None

    which = which.upper()
    if which in ('F1X', 'F1Y', 'F2X', 'F2Y'):
        sig = _get('F' + which[1] + which[2].lower())  # e.g. F1x
        if sig is None:
            sig = _get(which[:2] + which[2].lower())
        if sig is None:
            # direct key match fallback
            keymap = {'F1X': 'F1x', 'F1Y': 'F1y', 'F2X': 'F2x', 'F2Y': 'F2y'}
            sig = _get(keymap[which])
    elif which == 'F1':
        sig = combined_force_signal(_get('F1x'), _get('F1y'))
    elif which == 'F2':
        sig = combined_force_signal(_get('F2x'), _get('F2y'))
    elif which == 'SUM':
        sig = (combined_force_signal(_get('F1x'), _get('F1y')) +
               combined_force_signal(_get('F2x'), _get('F2y')))
    else:
        sig = _get('F1x')

    if sig is None:
        raise ValueError(f"Force channel '{which}' not available in traces.")

    n = len(sig)
    if sample_rate_hz and sample_rate_hz > 0:
        time = np.arange(n) / sample_rate_hz
    else:
        time = np.arange(n, dtype=float)
    return time, sig


# ---------------------------------------------------------------------------
# Image-based fusion signal (aspect ratio of the merging pair)
# ---------------------------------------------------------------------------

def aspect_ratio_signal(
    stack: np.ndarray,
    threshold_method: str = 'otsu',
    frame_interval_s: float = 1.0,
) -> tuple:
    """
    Build a fusion signal from a brightfield/fluorescence image stack by
    tracking the aspect ratio of the merging droplet pair over time.

    Two touching droplets start as an elongated (high aspect ratio) object
    and relax toward a circle (aspect ratio → 1). The aspect-ratio decay is
    fit with the same S(t) model (here the "signal" is the aspect ratio).

    Parameters
    ----------
    stack : (T, H, W) image stack.
    threshold_method : 'otsu' | 'triangle' | 'li' for per-frame segmentation.
    frame_interval_s : time per frame (s).

    Returns
    -------
    (time, aspect_ratio) arrays. aspect_ratio = major_axis / minor_axis of
    the largest connected object in each frame.
    """
    import skimage as sk

    stack = np.asarray(stack)
    if stack.ndim != 3:
        raise ValueError("Aspect-ratio fusion signal requires a (T,H,W) stack.")

    method = threshold_method.lower()
    thr_fn = {'triangle': sk.filters.threshold_triangle,
              'li': sk.filters.threshold_li}.get(method, sk.filters.threshold_otsu)

    ar = np.full(stack.shape[0], np.nan)
    for i in range(stack.shape[0]):
        frame = stack[i].astype(np.float32)
        mn, mx = float(frame.min()), float(frame.max())
        if mx <= mn:
            continue
        frame = (frame - mn) / (mx - mn)
        try:
            binary = frame > thr_fn(frame)
        except Exception:
            continue
        labeled = sk.measure.label(binary)
        props = sk.measure.regionprops(labeled)
        if not props:
            continue
        largest = max(props, key=lambda p: p.area)
        minor = largest.minor_axis_length
        if minor > 0:
            ar[i] = largest.major_axis_length / minor

    time = frame_interval_s * np.arange(stack.shape[0])
    return time, ar


# ---------------------------------------------------------------------------
# Inverse capillary velocity from multiple events
# ---------------------------------------------------------------------------

def inverse_capillary_velocity(
    tau_s: np.ndarray,
    length_um: np.ndarray,
) -> dict:
    """
    Fit τ = (η/γ)·ℓ across multiple fusion events to extract the inverse
    capillary velocity (η/γ), the slope of τ vs characteristic length ℓ.

    Parameters
    ----------
    tau_s : array of relaxation times (s), one per fusion event.
    length_um : array of characteristic lengths (µm), e.g. geometric-mean
        droplet radius, one per event.

    Returns
    -------
    dict with slope (s/µm = inverse capillary velocity), intercept, r_squared.
    """
    tau = np.asarray(tau_s, float)
    L = np.asarray(length_um, float)
    good = np.isfinite(tau) & np.isfinite(L)
    tau, L = tau[good], L[good]
    if len(tau) < 2:
        return dict(inverse_capillary_velocity_s_per_um=np.nan,
                    intercept=np.nan, r_squared=np.nan, n_events=int(len(tau)))
    slope, intercept = np.polyfit(L, tau, 1)
    pred = slope * L + intercept
    ss_res = np.sum((tau - pred) ** 2)
    ss_tot = np.sum((tau - np.mean(tau)) ** 2)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return dict(
        inverse_capillary_velocity_s_per_um=float(slope),
        intercept=float(intercept), r_squared=float(r2),
        n_events=int(len(tau)))
