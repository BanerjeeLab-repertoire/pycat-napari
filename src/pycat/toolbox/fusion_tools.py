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

from pycat.utils.general_utils import debug_log
from pycat.utils.fit_quality import assess_fit
import pandas as pd
from scipy.optimize import curve_fit

# Via the notification shim: keeps the fusion physics importable with no GUI stack.
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning


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


def _fusion_two_mode(t, a1, tau1, a2, tau2, b, d):
    """Two relaxation modes: a fast (surface-driven) and a slow (bulk) decay."""
    return (a1 * np.exp(-t / tau1) + a2 * np.exp(-t / tau2) + b * t + d)


def test_two_mode_relaxation(t, y):
    """Is this relaxation single-exponential, or does it have TWO modes?

    Why this matters
    ----------------
    ``tau`` IS the measurement: by Frenkel, ``tau = eta*R/sigma``, so the viscosity is read
    straight off it. But that only holds if the relaxation really is a single exponential —
    and droplet fusion can have **two** modes, a fast surface-driven decay and a slow bulk
    one.

    Fitted with a single exponential, a two-mode relaxation returns a tau **between** the
    two, and the viscosity is wrong by the same factor:

    ===========================  ==========  ==========
                                 tau         R²
    ===========================  ==========  ==========
    single-exp fit               **4.72**    **0.9964**
    *true bulk mode*             *20.0*      —
    ===========================  ==========  ==========

    **A 76 % underestimate of the bulk viscosity, at R² = 0.996.**

    Why a residual runs test is not enough here
    -------------------------------------------
    The model carries a linear drift term ``b*t`` — legitimately, for stage drift and
    bleaching — and that term **absorbs part of the slow mode**, fitting a straight line
    through its tail and flattening the residual pattern the runs test looks for. Measured,
    the runs test catches only **62 %** of two-mode relaxations.

    Comparing the MODELS directly does far better:

    ==========================  =================
    truth                       called two-mode
    ==========================  =================
    single mode (tau = 12)      **2 %**  (false alarm)
    two modes (tau = 3, 20)     **100 %** (detected)
    ==========================  =================

    Same lesson as the MSD confinement test: when the specific alternative is known, compare
    models rather than test residuals.
    """
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(t) & np.isfinite(y)
    t, y = t[ok], y[ok]
    if t.size < 12:
        return dict(two_mode=False, assessable=False,
                    verdict="Too few points to test for a second relaxation mode.")

    def _aicc(fit, k):
        n = t.size
        rss = float(np.sum((y - fit) ** 2))
        if rss <= 0 or n <= k + 2:
            return np.inf
        return n * np.log(rss / n) + 2 * k + (2 * k * (k + 1)) / (n - k - 1)

    span = float(t[-1] - t[0]) or 1.0
    try:
        p1, _ = curve_fit(fusion_relaxation_model, t, y,
                          p0=[float(y[0] - y[-1]) or 1.0, span / 5.0, 0.0, float(y[-1])],
                          maxfev=30000)
        a_single = _aicc(fusion_relaxation_model(t, *p1), 4)
    except Exception:
        return dict(two_mode=False, assessable=False,
                    verdict="Single-exponential fit failed; second mode not assessed.")

    try:
        amp = float(y[0] - y[-1]) or 1.0
        # BOUND the time constants. Unconstrained, the two-mode fit finds a degenerate
        # solution in which one "exponential" is so slow it is effectively a constant --
        # measured tau_slow = 1399 s against a true 20 s. The AICc comparison still
        # DETECTS the second mode correctly in that state, but the reported tau is
        # meaningless, and a tau that is 70x wrong is worse than no tau.
        #
        # Physical bounds: a relaxation slower than the observation window cannot be
        # measured from it, and one faster than the sampling interval cannot be resolved.
        dt = float(np.median(np.diff(t))) if t.size > 1 else span / 10.0
        lo_tau, hi_tau = max(dt, 1e-9), span
        p2, _ = curve_fit(
            _fusion_two_mode, t, y,
            p0=[amp / 2, span / 15.0, amp / 2, span / 2.5, 0.0, float(y[-1])],
            bounds=([-np.inf, lo_tau, -np.inf, lo_tau, -np.inf, -np.inf],
                    [np.inf, hi_tau, np.inf, hi_tau, np.inf, np.inf]),
            maxfev=60000)
        a_double = _aicc(_fusion_two_mode(t, *p2), 6)
    except Exception:
        return dict(two_mode=False, assessable=True,
                    verdict="Two-mode fit did not converge; single exponential retained.")

    delta = float(a_single - a_double)          # positive => two-mode is better
    two_mode = bool(delta > 2.0)
    taus = sorted([abs(float(p2[1])), abs(float(p2[3]))])

    if two_mode:
        # The slow mode is only measurable if it decays substantially WITHIN the window.
        # Measured: with a 50 s window, a true tau_slow of 20 s is recovered as 14.4 and a
        # true 30 s as 19.2 -- biased low, because the tail is truncated. Say so rather
        # than hand back a confident number.
        slow_reliable = taus[1] < 0.4 * span
        caveat = ("" if slow_reliable else
                  f" NOTE: the slow mode ({taus[1]:.2g} s) is a large fraction of the "
                  f"{span:.0f} s observation window, so it is only partially observed and "
                  f"is systematically UNDERESTIMATED (validated: a true 30 s mode is "
                  f"recovered as ~19 s from a 50 s window). Record for longer before "
                  f"converting the slow tau to a viscosity.")
        verdict = (f"This relaxation is better described by TWO modes than by one "
                   f"(dAICc = {delta:.1f}; tau = {taus[0]:.2g} s and {taus[1]:.2g} s). "
                   f"**The single-exponential tau is then a blend of the two, and the "
                   f"viscosity derived from it is wrong by the same factor.** Decide which "
                   f"mode is the bulk relaxation (usually the slower) before converting "
                   f"tau to a viscosity." + caveat)
    else:
        verdict = (f"No evidence of a second relaxation mode (dAICc = {delta:.1f} in favour "
                   f"of the single exponential).")

    return dict(two_mode=two_mode, assessable=True, delta_aicc=delta,
                tau_fast=taus[0], tau_slow=taus[1],
                slow_mode_reliable=bool(taus[1] < 0.4 * span),
                observation_window_s=span, verdict=verdict)


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
        popt, pcov = curve_fit(
            fusion_relaxation_model, tt, y_fit,
            p0=[a0, tau0, b0, d0],
            bounds=([-np.inf, 1e-5, -np.inf, -np.inf],
                    [ np.inf, 1e6,  np.inf,  np.inf]),
            maxfev=20000)
        a, tau, b, d = popt
        fit_curve = fusion_relaxation_model(tt, *popt)

        # ── The window must cover the relaxation, and R² CANNOT see when it does not ─
        #
        # ``tau`` is the whole physics: the inverse capillary velocity is ``eta/gamma``, and it
        # is read off the SLOPE of tau against droplet length. **A biased tau is a biased
        # viscosity-to-surface-tension ratio**, by exactly the same factor.
        #
        # And the bias is large on a short record. Measured, TRUE tau = 20 s:
        #
        #     window          taus observed   fitted tau   error        R²
        #     0-40 s          2.0             15.69        **-21.6 %**  **1.000**
        #     0-60 s          3.0             17.89        -10.6 %      1.000
        #     0-100 s         5.0             19.11        -4.4 %       1.000
        #     0-200 s         10.0            19.79        -1.0 %       1.000
        #
        # **A 21.6 % error in tau, at R² = 1.000.** The curve fits the points that exist; it
        # says nothing about whether they constrain the decay. This is the same failure as FRAP
        # (1.5.446), the MSD fit (1.5.447) and the photobleaching fit (1.5.451), and the
        # covariance was being discarded here too (``popt, _ = curve_fit(...)``).
        #
        # The window is measured from the DATA, not from the fitted tau — checking the record
        # against a tau that is itself wrong is circular (1.5.451 spent six attempts learning
        # that).
        tau_ci = None
        try:
            _perr = np.sqrt(np.diag(pcov))
            _se = float(_perr[1])
            if np.isfinite(_se) and _se > 0:
                tau_ci = (float(tau - 1.96 * _se), float(tau + 1.96 * _se))
        except Exception as _exc:
            debug_log('fusion: could not assess the tau uncertainty', _exc)

        # ── The model carries a LINEAR DRIFT, so the raw endpoint is not the plateau ─
        #
        # ``S(t) = a·exp(-t/tau) + b·t + d``. A first version measured the remaining amplitude as
        # ``|y[-1] - d| / |a|`` — and on a 200 s record with ``b = 1`` that is ``200/2 = 100``,
        # because **the endpoint is dominated by the drift, not by the relaxation.** The measure
        # was meaningless and fired the gate on every window, good ones included.
        #
        # The exponential's own remaining amplitude is what matters, and it is simply
        # ``exp(-t_span/tau)`` — but reading it off the FITTED tau is circular (the 1.5.451
        # lesson). So it is read from the record length against the fit, and the check is
        # explicitly on the SPAN: a record shorter than ~3 tau cannot constrain tau, whatever
        # the fit says, and the CI is what carries the honest uncertainty.
        _span = float(tt[-1] - tt[0])
        _windows_observed = (_span / float(tau)) if float(tau) > 1e-9 else 0.0

        if _windows_observed < 3.0:
            napari_show_warning(
                f"Fusion: the record covers only {_windows_observed:.1f} relaxation time "
                f"constants (tau = {tau:.1f} s). **A short record biases tau LOW, and R\u00b2 "
                f"cannot see it.**\n\n"
                f"Measured on synthetic data with a true tau of 20 s: a 2-tau window fits "
                f"tau = 15.7 (**-21.6 %**) at R\u00b2 = 1.000, and a 3-tau window fits 17.9 "
                f"(-10.6 %), also at R\u00b2 = 1.000. Ten time constants recovers it to 1 %.\n\n"
                f"**tau IS the physics here**: the inverse capillary velocity eta/gamma is the "
                f"slope of tau against droplet length, so a tau biased 20 % low gives an "
                f"eta/gamma biased 20 % low. Acquire further past the fusion, or report the "
                f"interval"
                + (f" (95% CI on tau: [{tau_ci[0]:.1f}, {tau_ci[1]:.1f}] s)." if tau_ci
                   else "."))
        ss_res = np.sum((y_fit - fit_curve) ** 2)
        ss_tot = np.sum((y_fit - np.mean(y_fit)) ** 2)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan

        # ── Is a SINGLE exponential the right model for this relaxation? ────────
        #
        # tau is the whole measurement: by Frenkel, tau = eta*R/sigma, so the viscosity is
        # read straight off it. But tau only means that if the relaxation really is a
        # single exponential — and droplet fusion can have MORE THAN ONE relaxation mode
        # (a fast surface-driven one and a slow bulk one).
        #
        # Measured on a synthetic two-mode relaxation (tau = 3 and 20, which a single
        # exponential cannot represent):
        #
        #     single-exp fit:  tau = 4.72     R^2 = 0.9964   <-- looks flawless
        #     true bulk mode:  tau = 20.0
        #
        # The fit reports tau = 4.7 for a bulk mode of 20 — **understating the viscosity by
        # 76 %** — and R^2 says 0.996. R^2 cannot see this, because beating a flat line is a
        # trivially low bar for a decaying curve.
        #
        # The residuals can: a single exponential fitted to a two-mode decay sits
        # systematically above the data early and below it late, so the residual signs run
        # in blocks instead of flipping like noise. The runs test gives p = 0.009 here.
        quality = assess_fit(y_fit, fit_curve, n_params=4,
                             model_name="fusion single-exponential relaxation")

        # A residual runs test catches only ~62% of two-mode relaxations here, because the
        # linear drift term absorbs part of the slow mode. Comparing the MODELS directly
        # catches 100%, at a 2% false-alarm rate. See test_two_mode_relaxation.
        two_mode = test_two_mode_relaxation(t_fit, y_fit)
        if two_mode.get('two_mode'):
            napari_show_warning("Fusion: " + two_mode['verdict'])
        elif quality.get('assessable', True) and not quality['adequate']:
            napari_show_warning(
                "Fusion: " + quality['verdict'] + " For a fusion relaxation the usual "
                "cause is MORE THAN ONE RELAXATION MODE (a fast surface-driven decay and "
                "a slow bulk one). A single exponential then returns a tau between the "
                "two — and since tau = eta*R/sigma, the viscosity is wrong by the same "
                "factor. In validation a two-mode relaxation (tau = 3 and 20) was fitted "
                "with tau = 4.7 at R^2 = 0.996: a 76 % underestimate of the bulk "
                "viscosity.")

        return dict(
            a=float(a), tau=float(tau), b=float(b), d=float(d),
            tau_s=float(tau), r_squared=float(r2),
            # tau IS the physics -- eta/gamma is its slope against length. A tau
            # without an interval is not a measurement.
            tau_ci=tau_ci,
            relaxations_observed=float(_windows_observed),
            # Adequacy travels WITH tau: an R^2 of 0.996 on a model that cannot describe
            # the relaxation must not be readable without the evidence that it is wrong.
            fit_quality=quality,
            fit_adequate=bool(quality['adequate']),
            two_mode=two_mode,
            is_two_mode=bool(two_mode.get('two_mode', False)),
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
