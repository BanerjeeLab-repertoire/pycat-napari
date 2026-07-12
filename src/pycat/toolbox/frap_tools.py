"""
PyCAT FRAP (Fluorescence Recovery After Photobleaching) Tools
==============================================================
Quantify molecular mobility inside condensates from a time-series recovery
movie, following the established manual workflow and the Taylor et al.
(Brangwynne lab) normalization.

Pipeline
--------
1. Define a bleached ROI and a reference (unbleached) ROI. Either drawn by
   the user (napari Shapes) or supplied as circular masks.
2. Per-frame mean intensity in each ROI.
3. Estimate the immediate post-bleach intensity I_0 by spline extrapolation
   to t=0 (the bleach depth).
4. Photofading correction: multiply the bleach curve by
   cf = ref_intensity[0] / ref_intensity(t)  — corrects for acquisition
   photobleaching using the reference region.
5. Taylor normalization:  I_norm = (I_corr − I_0) / (I_pre − I_0)
   so pre-bleach = 1 and immediate post-bleach = 0.
6. Fit the recovery model  I(t) = (a + b·(t/τ½)) / (1 + t/τ½)
   for a, b, τ½ (half-time of recovery).

Reference
---------
Taylor et al. normalization as used in the Brangwynne lab FRAP analyses;
recovery model I(t) = (a + b·(t/τ½))/(1 + t/τ½).

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2026
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

# Fit adequacy beyond R^2 (see pycat/utils/fit_quality.py).
from pycat.utils.fit_quality import assess_fit
from scipy.optimize import curve_fit
from scipy.interpolate import InterpolatedUnivariateSpline

# Via the notification shim: keeps the FRAP physics (normalisation, recovery fitting,
# mobile-fraction calculation) importable and testable with no GUI stack.
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning


# ---------------------------------------------------------------------------
# ROI mask construction
# ---------------------------------------------------------------------------

def circular_mask(shape: tuple, center_yx: tuple, radius: float) -> np.ndarray:
    """Boolean circular mask of given radius (px) centred at center_yx=(y,x)."""
    h, w = shape
    cy, cx = center_yx
    y, x = np.ogrid[0:h, 0:w]
    return (y - cy) ** 2 + (x - cx) ** 2 < radius ** 2


def masks_from_shapes(shapes_layer, image_shape: tuple):
    """
    Convert a napari Shapes layer into (bleach_mask, reference_mask).

    Convention: the FIRST shape is the bleached ROI, the SECOND is the
    reference ROI. Ellipses and rectangles are both supported. Returns
    (bleach_mask, reference_mask); reference_mask is None if only one
    shape was drawn.
    """
    import skimage.draw as skd

    if shapes_layer is None or len(shapes_layer.data) == 0:
        return None, None

    def _shape_to_mask(vertices, stype):
        verts = np.asarray(vertices)
        rr_cc = None
        ys, xs = verts[:, 0], verts[:, 1]
        if stype == 'ellipse':
            cy, cx = ys.mean(), xs.mean()
            ry, rx = (ys.max() - ys.min()) / 2, (xs.max() - xs.min()) / 2
            rr, cc = skd.ellipse(cy, cx, max(ry, 1), max(rx, 1), shape=image_shape)
        else:  # rectangle / polygon
            rr, cc = skd.polygon(ys, xs, shape=image_shape)
        m = np.zeros(image_shape, dtype=bool)
        m[rr, cc] = True
        return m

    shapes = list(zip(shapes_layer.data, shapes_layer.shape_type))
    bleach_mask = _shape_to_mask(*shapes[0])
    ref_mask = _shape_to_mask(*shapes[1]) if len(shapes) > 1 else None
    return bleach_mask, ref_mask


# ---------------------------------------------------------------------------
# Intensity extraction + correction + normalization
# ---------------------------------------------------------------------------

def extract_roi_intensity(stack: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Per-frame mean intensity within a mask: sum(frame*mask)/sum(mask)."""
    stack = np.asarray(stack)
    m = np.asarray(mask) > 0
    denom = m.sum()
    if denom == 0:
        return np.zeros(stack.shape[0])
    return np.array([np.sum(stack[i] * m) / denom for i in range(stack.shape[0])])


def estimate_bleach_depth(time: np.ndarray, bl_intensity: np.ndarray,
                          n_points: int = 30, spline_k: int = 2) -> float:
    """
    Estimate the immediate post-bleach intensity I_0 by extrapolating a
    spline through the early recovery points back to t=0.

    Uses the first n_points of the recovery curve, matching the manual
    workflow (InterpolatedUnivariateSpline, k=2, s(0)).
    """
    n = min(n_points, len(time))
    if n < spline_k + 1:
        return float(bl_intensity[0])
    try:
        s = InterpolatedUnivariateSpline(time[:n], bl_intensity[:n], k=spline_k)
        return float(s(0.0))
    except Exception:
        return float(bl_intensity[0])


def photofading_correction(bl_intensity: np.ndarray,
                           ref_intensity: np.ndarray) -> tuple:
    """
    Correct the bleach curve for acquisition photobleaching using the
    reference region: cf = ref_intensity[0]/ref_intensity(t);
    corrected = bl_intensity * cf.

    Returns (corrected_bl, cf).
    """
    ref = np.asarray(ref_intensity, dtype=float)
    with np.errstate(divide='ignore', invalid='ignore'):
        cf = np.where(ref > 0, ref[0] / ref, 1.0)
    corrected = np.asarray(bl_intensity, dtype=float) * cf
    return corrected, cf


def taylor_normalize(corrected_bl: np.ndarray, intensity_0: float,
                     prebleach: float) -> np.ndarray:
    """
    Taylor et al. (Brangwynne lab) normalization:
        I_norm = (I_corr − I_0) / (I_pre − I_0)
    Pre-bleach maps to 1, immediate post-bleach to 0. This rescales the
    recovery so the mobile fraction can be read directly off the plateau.
    """
    denom = (prebleach - intensity_0)
    if abs(denom) < 1e-12:
        return np.zeros_like(corrected_bl)
    return (np.asarray(corrected_bl, dtype=float) - intensity_0) / denom


def prebleach_normalize(corrected_bl: np.ndarray, prebleach: float) -> np.ndarray:
    """
    Simple pre-bleach normalization (alternative to Taylor):
        I_norm = I_corr / I_pre
    Pre-bleach maps to 1; the immediate post-bleach value is NOT forced to 0,
    so the bleach depth is preserved in the curve. Use this when you want the
    absolute recovery relative to the pre-bleach level rather than a
    0-to-1 rescaled curve.
    """
    if abs(prebleach) < 1e-12:
        return np.zeros_like(corrected_bl)
    return np.asarray(corrected_bl, dtype=float) / prebleach


# ---------------------------------------------------------------------------
# Recovery model + fit
# ---------------------------------------------------------------------------

def frap_recovery_model(t, a, b, tau_half):
    """
    I(t) = (a + b·(t/τ½)) / (1 + t/τ½)

    Equation (9): a, b, τ½ are fitting parameters. As t→∞, I→b (mobile
    plateau); at t=0, I=a; τ½ is the recovery half-time.
    """
    x = t / tau_half
    return (a + b * x) / (1.0 + x)


def fit_frap_recovery(time: np.ndarray, norm_intensity: np.ndarray) -> dict:
    """
    Fit the recovery model to a normalized FRAP curve.

    Returns
    -------
    dict with:
        a, b, tau_half        : fit parameters
        mobile_fraction       : (b − a) / (1 − a)  — the fraction of the BLEACHED
                                material that recovered. See the note below: this is
                                NOT simply b − a.
        immobile_fraction     : 1 − mobile_fraction (clipped to [0,1])
        bleach_depth          : 1 − a  — how much signal the bleach removed, in the
                                units of the normalised curve. Reported separately
                                because it is an acquisition property, not a
                                biological one.
        over_recovery         : True if the plateau exceeds the pre-bleach level
                                (b > 1), which is not physical for a simple recovery
                                and usually indicates a normalisation or
                                photofading-correction problem.
        half_time_s           : τ½
        r_squared             : goodness of fit
        fit_time, fit_curve   : arrays for plotting the fitted curve

    Why the mobile fraction is NOT ``b − a``
    ---------------------------------------
    The mobile fraction is the fraction of the material that was BLEACHED which
    subsequently recovered:

        mobile = (plateau − post-bleach) / (pre-bleach − post-bleach)
               = (b − a) / (1 − a)

    ``b − a`` alone omits the denominator, and the denominator is the bleach depth.
    That omission is invisible under **Taylor** normalisation, where the immediate
    post-bleach value is forced to 0 by construction, so ``a ≈ 0`` and
    ``(b − a)/(1 − a) → b − a``. It is NOT invisible under **pre-bleach**
    normalisation (``I / I_pre``), where ``a`` is the bleach depth and is far from
    zero.

    The error is exactly ``−(1 − bleach_depth)`` and grows as the bleach gets
    SHALLOWER. A 30 %-deep bleach on a fully mobile protein (true mobile = 1.0) was
    reported as **0.30** — i.e. "70 % immobile" for a species that is entirely
    mobile. Verified numerically against ground truth.

    The expression below is normalisation-agnostic: it reduces to ``b`` when
    ``a = 0`` (Taylor) and is correct when ``a > 0`` (pre-bleach).
    """
    t = np.asarray(time, dtype=float)
    y = np.asarray(norm_intensity, dtype=float)
    good = np.isfinite(t) & np.isfinite(y)
    t, y = t[good], y[good]
    if len(t) < 4:
        return dict(a=np.nan, b=np.nan, tau_half=np.nan,
                    mobile_fraction=np.nan, immobile_fraction=np.nan,
                    bleach_depth=np.nan, over_recovery=False,
                    half_time_s=np.nan, r_squared=np.nan,
                    fit_time=np.array([]), fit_curve=np.array([]))

    # Initial guesses: a≈y[0], b≈max plateau, τ½≈time span/4
    a0 = float(y[0])
    b0 = float(np.nanmax(y))
    tau0 = max((t[-1] - t[0]) / 4.0, 1e-3)

    try:
        popt, _ = curve_fit(
            frap_recovery_model, t, y, p0=[a0, b0, tau0],
            bounds=([-0.5, -0.5, 1e-4], [1.5, 2.0, 1e6]),
            maxfev=10000)
        a, b, tau_half = popt
        fit_curve = frap_recovery_model(t, *popt)
        ss_res = np.sum((y - fit_curve) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
        # Normalisation-aware mobile fraction: the fraction of the BLEACHED material
        # that recovered. Reduces to b when a == 0 (Taylor); correct when a > 0
        # (pre-bleach normalisation), where the old `b - a` under-reported it by
        # exactly -(1 - bleach_depth).
        bleach_depth = float(1.0 - a)
        if abs(bleach_depth) < 1e-6:
            # The bleach removed (essentially) nothing: the mobile fraction is not
            # identifiable from this curve. Say so rather than dividing by ~0.
            mobile = float('nan')
        else:
            mobile = float((b - a) / bleach_depth)
        over_recovery = bool(np.isfinite(b) and b > 1.0 + 1e-6)
        if over_recovery:
            napari_show_warning(
                "FRAP: the recovery plateau exceeds the pre-bleach level "
                f"(b = {b:.3f} > 1). That is not physical for a simple recovery — "
                "check the normalisation and the photofading correction (an "
                "over-aggressive reference correction will do this).")
        # ── Is the MODEL right, or does it merely beat a flat line? ────────────
        #
        # R^2 answers only "does this beat a horizontal line?", which for a monotonic
        # recovery curve is a trivially low bar. Validated on synthetic FRAP whose truth
        # is a TWO-component recovery (fast + slow pool, a very common case), fitted with
        # this SINGLE-POOL model (PyCAT uses the hyperbolic form I = (a + b*x)/(1 + x)):
        #
        #     wrong model (1-exp):  R^2 = 0.904   mobile fraction 0.724
        #     right model (2-exp):  R^2 = 0.992   mobile fraction 0.930
        #     truth:                              mobile fraction 0.875
        #
        # The wrong model scores R^2 = 0.90 -- respectable by the usual heuristic -- and
        # returns a mobile fraction 17% from the truth.
        #
        # The residuals catch it: a correct model leaves residual signs randomly ordered;
        # a model missing structure leaves them in BLOCKS. The runs test flagged the wrong
        # model 30/30 times and the correct model 0/30.
        quality = assess_fit(y, fit_curve, n_params=3, model_name="FRAP single-pool (hyperbolic)")
        if not quality['adequate']:
            napari_show_warning("FRAP: " + quality['verdict'])

        return dict(
            a=float(a), b=float(b), tau_half=float(tau_half),
            mobile_fraction=mobile,
            immobile_fraction=(float(np.clip(1.0 - mobile, 0.0, 1.0))
                               if np.isfinite(mobile) else float('nan')),
            bleach_depth=bleach_depth,
            over_recovery=over_recovery,
            half_time_s=float(tau_half), r_squared=float(r2),
            # Fit adequacy travels WITH the parameters: an R^2 of 0.9 on a wrong model
            # must not be readable without the evidence that the model is wrong.
            fit_quality=quality,
            fit_adequate=bool(quality['adequate']),
            fit_time=t, fit_curve=fit_curve)
    except Exception as e:
        napari_show_warning(f"FRAP fit failed: {e}")
        return dict(a=np.nan, b=np.nan, tau_half=np.nan,
                    mobile_fraction=np.nan, immobile_fraction=np.nan,
                    bleach_depth=np.nan, over_recovery=False,
                    half_time_s=np.nan, r_squared=np.nan,
                    fit_time=np.array([]), fit_curve=np.array([]))


# ---------------------------------------------------------------------------
# Reaction-diffusion FRAP model (rectangular bleach ROI)
# ---------------------------------------------------------------------------
#
# Closed-form recovery for a rectangular bleach box of size d_x × d_y under
# pure diffusion plus first-order binding/unbinding (Soumpasis / Ellenberg
# style), following the model in the user's manual FRAP tool:
#
#     I(t) = f_f · [ 1 − f_b · (4·e^(−k_off·t) / (d_x·d_y)) · ψ_x(t) · ψ_y(t) ]
#
# with
#     ψ_i(t) = (d_i/2)·erf(d_i / sqrt(4 D t)) − sqrt(D t / π)·(1 − e^(−d_i²/4 D t))
#
# Fitted parameters:
#     f_f   mobile fraction (recovery plateau)
#     f_b   bound fraction
#     D     diffusion coefficient (µm²/s)
#     k_off unbinding rate (1/s)
#
# Unlike the empirical (a,b,τ½) model, this yields a physical diffusion
# coefficient and an off-rate directly, using the ROI's physical dimensions.


def reaction_diffusion_recovery(t, f_f, f_b, D, k_off, d_x, d_y):
    """Rectangular-ROI reaction-diffusion FRAP recovery (see module notes)."""
    import scipy.special as _sp
    t = np.asarray(t, dtype=float)
    t_safe = np.where(t <= 0, 1e-12, t)

    def psi(tt, d_i):
        return (d_i / 2.0 * _sp.erf(d_i / np.sqrt(4.0 * D * tt))
                - np.sqrt(D * tt / np.pi)
                * (1.0 - np.exp(-d_i ** 2 / (4.0 * D * tt))))

    psi_x = psi(t_safe, d_x)
    psi_y = psi(t_safe, d_y)
    return f_f * (1.0 - f_b * 4.0 * np.exp(-k_off * t_safe)
                  / (d_x * d_y) * psi_x * psi_y)


def _numeric_hessian(f, x, eps=1e-4):
    """Finite-difference Hessian of a scalar function f(x). No extra deps."""
    x = np.asarray(x, dtype=float)
    n = x.size
    H = np.zeros((n, n))
    f0 = f(x)
    for i in range(n):
        for j in range(n):
            xi = x.copy(); xi[i] += eps; xi[j] += eps; fpp = f(xi)
            xi = x.copy(); xi[i] += eps; xi[j] -= eps; fpm = f(xi)
            xi = x.copy(); xi[i] -= eps; xi[j] += eps; fmp = f(xi)
            xi = x.copy(); xi[i] -= eps; xi[j] -= eps; fmm = f(xi)
            H[i, j] = (fpp - fpm - fmp + fmm) / (4.0 * eps * eps)
    return H


def fit_reaction_diffusion(time, norm_intensity, d_x_um, d_y_um,
                           fit_koff=True):
    """
    Fit the rectangular-ROI reaction-diffusion model to a normalized FRAP
    recovery curve, returning a physical diffusion coefficient and (optionally)
    an off-rate, each with a Hessian-based uncertainty.

    Parameters
    ----------
    time, norm_intensity : recovery curve.
    d_x_um, d_y_um : bleach ROI dimensions in µm (from the drawn box × pixel size).
    fit_koff : if False, koff is fixed to 0 (pure diffusion, 3-parameter fit).

    Returns
    -------
    dict with f_f, f_b, D_um2_per_s, k_off_per_s and their ± uncertainties,
    r_squared, fit_time, fit_curve.
    """
    from scipy.optimize import leastsq

    t = np.asarray(time, dtype=float)
    y = np.asarray(norm_intensity, dtype=float)
    good = np.isfinite(t) & np.isfinite(y) & (t > 0)
    t, y = t[good], y[good]
    if len(t) < 5:
        return dict(f_f=np.nan, f_b=np.nan, D_um2_per_s=np.nan,
                    k_off_per_s=np.nan, f_f_err=np.nan, f_b_err=np.nan,
                    D_err=np.nan, k_off_err=np.nan, r_squared=np.nan,
                    fit_time=np.array([]), fit_curve=np.array([]))

    if fit_koff:
        def model(p, tt):
            f_f, f_b, D, k_off = p
            return reaction_diffusion_recovery(tt, f_f, f_b, D, k_off, d_x_um, d_y_um)
        p0 = np.array([0.9, 0.9, 1.0, 0.1])
    else:
        def model(p, tt):
            f_f, f_b, D = p
            return reaction_diffusion_recovery(tt, f_f, f_b, D, 0.0, d_x_um, d_y_um)
        p0 = np.array([0.9, 0.9, 1.0])

    def resid(p):
        return y - model(p, t)

    try:
        popt, _ = leastsq(resid, p0, maxfev=20000)
        fit_curve = model(popt, t)
        ss_res = np.sum((y - fit_curve) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan

        # Hessian-based covariance from the log-likelihood (Gaussian errors)
        def log_post(p):
            return -len(y) / 2.0 * np.log(np.sum((y - model(p, t)) ** 2) + 1e-30)
        errs = np.full(len(popt), np.nan)
        try:
            H = _numeric_hessian(log_post, popt)
            cov = -np.linalg.inv(H)
            errs = np.sqrt(np.clip(np.diag(cov), 0, None))
        except Exception:
            pass

        out = dict(f_f=float(popt[0]), f_b=float(popt[1]),
                   D_um2_per_s=float(popt[2]),
                   k_off_per_s=float(popt[3]) if fit_koff else 0.0,
                   f_f_err=float(errs[0]), f_b_err=float(errs[1]),
                   D_err=float(errs[2]),
                   k_off_err=float(errs[3]) if fit_koff else 0.0,
                   r_squared=float(r2), fit_time=t, fit_curve=fit_curve)
        return out
    except Exception as e:
        napari_show_warning(f"Reaction-diffusion fit failed: {e}")
        return dict(f_f=np.nan, f_b=np.nan, D_um2_per_s=np.nan,
                    k_off_per_s=np.nan, f_f_err=np.nan, f_b_err=np.nan,
                    D_err=np.nan, k_off_err=np.nan, r_squared=np.nan,
                    fit_time=np.array([]), fit_curve=np.array([]))


# ---------------------------------------------------------------------------
# Circular-ROI FRAP model (Soumpasis 1983)
# ---------------------------------------------------------------------------
#
# Classic closed-form recovery for a uniform circular bleach spot of radius w
# under pure 2D diffusion (Soumpasis, Biophys J 1983):
#
#     f(t) = f_f · { exp(−2·τ_D / t) · [ I0(2·τ_D / t) + I1(2·τ_D / t) ] }
#            + (1 − f_f)·(immobile offset, folded into the plateau)
#
# where I0, I1 are modified Bessel functions of the first kind, τ_D is the
# characteristic diffusion time, and the diffusion coefficient is
#
#     D = w² / (4 · τ_D)
#
# This is the correct model for a circular bleach ROI (the rectangular
# reaction_diffusion_recovery model assumes a box). It reports a physical
# diffusion coefficient D and a mobile fraction f_f.


def circular_soumpasis_recovery(t, f_f, tau_D, offset):
    """
    Soumpasis circular-spot FRAP recovery.

    Parameters
    ----------
    t : time array (s).
    f_f : mobile fraction (recovery amplitude).
    tau_D : characteristic diffusion time (s). D = w²/(4·tau_D).
    offset : immediate post-bleach baseline (≈0 for Taylor-normalized data).
    """
    import scipy.special as _sp
    t = np.asarray(t, dtype=float)
    t_safe = np.where(t <= 0, 1e-12, t)
    x = 2.0 * tau_D / t_safe
    # For large x the exp·Bessel product underflows/overflows; use the
    # exponentially-scaled Bessel functions i0e(x)=e^{-x}I0(x), i1e likewise,
    # so exp(-x)*(I0+I1) = i0e(x)+i1e(x) is numerically stable for all x.
    recovery = _sp.i0e(x) + _sp.i1e(x)
    return offset + f_f * recovery


def fit_circular_soumpasis(time, norm_intensity, bleach_radius_um):
    """
    Fit the Soumpasis circular-ROI model to a normalized FRAP recovery curve.

    Parameters
    ----------
    time, norm_intensity : recovery curve.
    bleach_radius_um : circular bleach ROI radius in µm.

    Returns
    -------
    dict with f_f, tau_D_s, D_um2_per_s, offset and their ± uncertainties,
    half_time_s (τ½ = τ_D·γ where the recovery reaches half — reported from
    the fitted curve), r_squared, fit_time, fit_curve.
    """
    from scipy.optimize import leastsq

    t = np.asarray(time, dtype=float)
    y = np.asarray(norm_intensity, dtype=float)
    good = np.isfinite(t) & np.isfinite(y) & (t > 0)
    t, y = t[good], y[good]
    if len(t) < 4:
        return dict(f_f=np.nan, tau_D_s=np.nan, D_um2_per_s=np.nan,
                    offset=np.nan, f_f_err=np.nan, tau_D_err=np.nan,
                    D_err=np.nan, half_time_s=np.nan, r_squared=np.nan,
                    fit_time=np.array([]), fit_curve=np.array([]))

    def model(p, tt):
        f_f, tau_D, offset = p
        return circular_soumpasis_recovery(tt, f_f, tau_D, offset)

    # Initial guesses: mobile frac ~ plateau, tau_D ~ span/4, offset ~ y[0]
    p0 = np.array([float(np.nanmax(y) - y[0]), max((t[-1]-t[0])/4.0, 1e-3), float(y[0])])

    def resid(p):
        return y - model(p, t)

    try:
        popt, _ = leastsq(resid, p0, maxfev=20000)
        f_f, tau_D, offset = popt
        D = (bleach_radius_um ** 2) / (4.0 * tau_D) if tau_D > 0 else np.nan
        fit_curve = model(popt, t)
        ss_res = np.sum((y - fit_curve) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan

        # Half-time: time at which the fitted curve reaches half its recovery
        half_level = offset + 0.5 * f_f
        half_time = np.nan
        above = np.where(fit_curve >= half_level)[0]
        if above.size:
            half_time = float(t[above[0]])

        # Hessian-based uncertainties from the Gaussian log-posterior
        def log_post(p):
            return -len(y) / 2.0 * np.log(np.sum((y - model(p, t)) ** 2) + 1e-30)
        errs = np.full(3, np.nan)
        try:
            H = _numeric_hessian(log_post, popt)
            cov = -np.linalg.inv(H)
            errs = np.sqrt(np.clip(np.diag(cov), 0, None))
        except Exception:
            pass
        # Propagate tau_D uncertainty into D: dD/dtau_D = -w²/(4 tau_D²)
        D_err = np.nan
        if np.isfinite(errs[1]) and tau_D > 0:
            D_err = abs((bleach_radius_um ** 2) / (4.0 * tau_D ** 2)) * errs[1]

        return dict(f_f=float(f_f), tau_D_s=float(tau_D),
                    D_um2_per_s=float(D), offset=float(offset),
                    f_f_err=float(errs[0]), tau_D_err=float(errs[1]),
                    D_err=float(D_err), half_time_s=half_time,
                    r_squared=float(r2), fit_time=t, fit_curve=fit_curve)
    except Exception as e:
        napari_show_warning(f"Circular Soumpasis fit failed: {e}")
        return dict(f_f=np.nan, tau_D_s=np.nan, D_um2_per_s=np.nan,
                    offset=np.nan, f_f_err=np.nan, tau_D_err=np.nan,
                    D_err=np.nan, half_time_s=np.nan, r_squared=np.nan,
                    fit_time=np.array([]), fit_curve=np.array([]))


# ---------------------------------------------------------------------------
# Full analysis orchestration
# ---------------------------------------------------------------------------

def run_frap_analysis(
    recovery_stack: np.ndarray,
    bleach_mask: np.ndarray,
    reference_mask: Optional[np.ndarray] = None,
    frame_interval_s: float = 1.0,
    time_lag_s: float = 0.0,
    prebleach_stack: Optional[np.ndarray] = None,
    n_spline_points: int = 30,
    normalization: str = 'taylor',
    fit_model: str = 'empirical',
    roi_dims_um: tuple = None,
    fit_koff: bool = True,
    bleach_radius_um: float = None,
) -> dict:
    """
    Full FRAP analysis from a recovery time-series.

    Parameters
    ----------
    recovery_stack : (T, H, W) recovery movie.
    bleach_mask : boolean mask of the bleached ROI.
    reference_mask : boolean mask of an unbleached reference ROI for
        photofading correction. If None, no photofading correction is applied.
    frame_interval_s : time between recovery frames (s).
    time_lag_s : delay between bleach and first recovery frame (s).
    prebleach_stack : optional (T,H,W) or (H,W) pre-bleach image(s). The mean
        intensity in the bleach ROI defines the pre-bleach reference. If None,
        the maximum of the corrected bleach curve is used as the pre-bleach.
    n_spline_points : early points used for the t=0 bleach-depth spline.
    normalization : 'taylor' (default) or 'prebleach'. 'taylor' rescales
        to [0,1] via (I−I_0)/(I_pre−I_0); 'prebleach' uses I/I_pre.

    Returns
    -------
    dict with time, raw curves, corrected/normalized curves, and the fit.
    """
    stack = np.asarray(recovery_stack)
    if stack.ndim == 2:
        stack = stack[np.newaxis, ...]
    n_frames = stack.shape[0]

    time = frame_interval_s * np.arange(n_frames) + time_lag_s

    bl_intensity = extract_roi_intensity(stack, bleach_mask)

    if reference_mask is not None:
        ref_intensity = extract_roi_intensity(stack, reference_mask)
        corrected_bl, cf = photofading_correction(bl_intensity, ref_intensity)
    else:
        ref_intensity = np.full(n_frames, np.nan)
        corrected_bl, cf = bl_intensity.copy(), np.ones(n_frames)

    intensity_0 = estimate_bleach_depth(time, corrected_bl, n_spline_points)

    if prebleach_stack is not None:
        pre = np.asarray(prebleach_stack)
        if pre.ndim == 2:
            pre = pre[np.newaxis, ...]
        pre_int = extract_roi_intensity(pre, bleach_mask)
        prebleach = float(np.mean(pre_int))
    else:
        prebleach = float(np.nanmax(corrected_bl))

    if str(normalization).lower() == 'taylor':
        bl_norm = taylor_normalize(corrected_bl, intensity_0, prebleach)
    else:
        bl_norm = prebleach_normalize(corrected_bl, prebleach)

    # Empirical (a,b,τ½) fit always computed for the recovery half-time.
    fit = fit_frap_recovery(time, bl_norm)

    # Optional physical reaction-diffusion fit (D, k_off, mobile/bound frac).
    rd_fit = None
    circ_fit = None
    model_l = str(fit_model).lower()
    if model_l in ('reaction_diffusion', 'diffusion', 'rd'):
        if roi_dims_um is None or roi_dims_um[0] <= 0 or roi_dims_um[1] <= 0:
            napari_show_warning(
                "Reaction-diffusion fit needs the bleach ROI dimensions in µm "
                "(roi_dims_um). Falling back to the empirical fit only.")
        else:
            rd_fit = fit_reaction_diffusion(
                time, bl_norm, roi_dims_um[0], roi_dims_um[1], fit_koff=fit_koff)
    elif model_l in ('circular', 'soumpasis', 'circle'):
        if not bleach_radius_um or bleach_radius_um <= 0:
            napari_show_warning(
                "Circular (Soumpasis) fit needs the bleach radius in µm "
                "(bleach_radius_um). Falling back to the empirical fit only.")
        else:
            circ_fit = fit_circular_soumpasis(time, bl_norm, bleach_radius_um)

    return dict(
        time=time,
        bl_intensity=bl_intensity,
        ref_intensity=ref_intensity,
        corrected_bl=corrected_bl,
        correction_factor=cf,
        intensity_0=intensity_0,
        prebleach=prebleach,
        bl_norm=bl_norm,
        fit=fit,
        rd_fit=rd_fit,
        circ_fit=circ_fit,
        results_df=pd.DataFrame({
            'time_s': time,
            'bleach_intensity': bl_intensity,
            'reference_intensity': ref_intensity,
            'corrected_bleach': corrected_bl,
            'normalized': bl_norm,
            'correction_factor': cf,
        }),
    )


# ---------------------------------------------------------------------------
# Multi-ROI FRAP (Mosaic / MicroPoint — multiple photostimulation locations)
# ---------------------------------------------------------------------------

def masks_from_shapes_multi(shapes_layer, image_shape: tuple,
                            n_reference: int = 1):
    """
    Convert a Shapes layer with MANY ROIs into a list of bleach masks plus
    a shared reference mask.

    Andor Fusion (Mosaic / MicroPoint) lets the user select multiple
    photostimulation locations in one field of view. Convention here:
    the LAST `n_reference` shape(s) are reference (unbleached) ROIs; all
    earlier shapes are individual bleached ROIs, analyzed independently
    and returned as a list.

    Parameters
    ----------
    shapes_layer : napari Shapes layer.
    image_shape : (H, W).
    n_reference : number of trailing shapes to treat as reference ROIs.
        If >1 they are unioned into a single reference mask. If 0, no
        reference mask is produced.

    Returns
    -------
    (bleach_masks, reference_mask)
        bleach_masks : list of boolean masks, one per bleached ROI.
        reference_mask : boolean mask (union of reference ROIs) or None.
    """
    import skimage.draw as skd

    if shapes_layer is None or len(shapes_layer.data) == 0:
        return [], None

    def _shape_to_mask(vertices, stype):
        verts = np.asarray(vertices)
        ys, xs = verts[:, 0], verts[:, 1]
        if stype == 'ellipse':
            cy, cx = ys.mean(), xs.mean()
            ry, rx = (ys.max() - ys.min()) / 2, (xs.max() - xs.min()) / 2
            rr, cc = skd.ellipse(cy, cx, max(ry, 1), max(rx, 1), shape=image_shape)
        else:
            rr, cc = skd.polygon(ys, xs, shape=image_shape)
        m = np.zeros(image_shape, dtype=bool)
        m[rr, cc] = True
        return m

    shapes = list(zip(shapes_layer.data, shapes_layer.shape_type))
    masks = [_shape_to_mask(v, s) for v, s in shapes]

    if n_reference <= 0 or len(masks) <= n_reference:
        # No reference designated, or not enough shapes — treat all as bleach
        return masks, None

    bleach_masks = masks[:-n_reference]
    ref_masks = masks[-n_reference:]
    reference_mask = np.zeros(image_shape, dtype=bool)
    for m in ref_masks:
        reference_mask |= m
    return bleach_masks, reference_mask


def run_frap_analysis_multi(
    recovery_stack: np.ndarray,
    bleach_masks: list,
    reference_mask: Optional[np.ndarray] = None,
    frame_interval_s: float = 1.0,
    time_lag_s: float = 0.0,
    prebleach_stack: Optional[np.ndarray] = None,
    n_spline_points: int = 30,
    normalization: str = 'taylor',
) -> dict:
    """
    Run FRAP analysis independently for each of several bleached ROIs that
    share one reference region — the Mosaic / MicroPoint multi-spot case.

    Returns
    -------
    dict with:
        per_roi   : list of per-ROI result dicts (see run_frap_analysis)
        summary_df: one row per ROI with tau_half, mobile/immobile fraction, R²
    """
    results = []
    rows = []
    for i, bmask in enumerate(bleach_masks):
        res = run_frap_analysis(
            recovery_stack, bmask, reference_mask=reference_mask,
            frame_interval_s=frame_interval_s, time_lag_s=time_lag_s,
            prebleach_stack=prebleach_stack, n_spline_points=n_spline_points,
            normalization=normalization)
        fit = res['fit']
        results.append(res)
        rows.append({
            'roi': i + 1,
            'tau_half_s':        fit['half_time_s'],
            'mobile_fraction':   fit['mobile_fraction'],
            'immobile_fraction': fit['immobile_fraction'],
            'r_squared':         fit['r_squared'],
            'bleach_depth_I0':   res['intensity_0'],
            'prebleach':         res['prebleach'],
        })
    return dict(per_roi=results, summary_df=pd.DataFrame(rows))
