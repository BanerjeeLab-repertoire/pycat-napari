"""Condensate shape-**relaxation** kinetics — split out of condensate_physics_tools (1.6.219).

fit_aspect_ratio_relaxation: the viscous fusion/relaxation fit of aspect ratio vs time (a droplet's return
to sphericity). Moved VERBATIM - no fit or number changed. The tools module re-exports it. (The related
extract_fusion_relaxation joins this module in a later step.)
"""
from __future__ import annotations

import numpy as np
from scipy import optimize
from pycat.utils.general_utils import debug_log
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.fit_quality import assess_fit


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
        # ── The covariance, and an honest note about what it adds here ──────────
        #
        # Unlike FRAP (1.5.446) and the MSD fit (1.5.447), **this fit is ROBUST to a short
        # observation window.** I expected the same failure and measured it, and it is not
        # there: with a true tau of 10 s, even half a relaxation time of data recovers
        # 10.10 ± 0.46. The relaxation is a clean single exponential with a large amplitude
        # (the aspect ratio falls from ~3 to 1), so a short window still pins tau.
        #
        # What DOES degrade it is NOISE — and here R² tracks the problem honestly, which is
        # the opposite of the FRAP and MSD cases:
        #
        #     noise on AR    tau (sd)          mean R²
        #     0.03           10.04 (0.20)      0.997
        #     0.10           10.16 (0.68)      0.971
        #     0.30           10.67 (2.25)      0.778
        #     0.60            8.66 (1.48)      0.635
        #
        # So the covariance is captured to give tau an INTERVAL — eta/gamma is computed from
        # it, and a ratio without an interval is not a measurement — but there is no hidden
        # failure mode being caught here. R² is a reasonable guide for this fit. Stating that
        # plainly matters: the point of these checks is to find where a statistic misleads,
        # not to attach one everywhere.
        popt, pcov = optimize.curve_fit(model, t - t[0], ar,
                                      p0=p0, bounds=bounds, maxfev=3000)
        AR_0, tau = popt

        tau_ci = None
        try:
            _perr = np.sqrt(np.diag(pcov))
            _se = float(_perr[1])
            if np.isfinite(_se) and _se > 0:
                tau_ci = (float(tau - 1.96 * _se), float(tau + 1.96 * _se))
        except Exception as _exc:
            debug_log('fusion: could not assess tau uncertainty', _exc)
        ar_fit  = model(t - t[0], *popt)
        ss_res  = np.sum((ar - ar_fit)**2)
        ss_tot  = np.sum((ar - ar.mean())**2)
        r2      = 1 - ss_res / max(ss_tot, 1e-12)
        eta_over_gamma = (float(tau) / R) if (R and R > 0) else np.nan

        # eta/gamma inherits tau's interval EXACTLY — it is tau divided by a constant.
        eta_over_gamma_ci = None
        if tau_ci is not None and R and R > 0:
            eta_over_gamma_ci = (float(tau_ci[0] / R), float(tau_ci[1] / R))

        # ── `fit_success = r2 > 0.5` is not a check on tau ──────────────────────
        #
        # tau IS the measurement: eta/gamma = tau/R. But that only holds if the aspect-
        # ratio relaxation really is a SINGLE exponential — and fusion can have two modes
        # (a fast surface-driven decay and a slow bulk one). Fitted with one exponential,
        # a two-mode relaxation returns a tau BETWEEN the two, and eta/gamma is wrong by
        # the same factor. Validated in fusion_tools (1.5.412): a true bulk tau of 20 was
        # reported as 4.72 at **R² = 0.996** — a 76 % underestimate that R² waves through
        # without hesitation. A threshold of 0.5 is weaker still.
        #
        # The residuals catch what R² cannot: a single exponential fitted to a two-mode
        # decay sits systematically above the data early and below it late, so the
        # residual signs run in blocks instead of flipping like noise.
        quality = assess_fit(ar, ar_fit, n_params=2,
                             model_name="aspect-ratio single-exponential relaxation")
        if quality.get('assessable', True) and not quality['adequate']:
            napari_show_warning(
                "Fusion aspect ratio: " + quality['verdict'] + " For a fusion relaxation "
                "the usual cause is MORE THAN ONE RELAXATION MODE. The single-exponential "
                "tau is then a blend of the two, and eta/gamma = tau/R is wrong by the "
                "same factor. See test_two_mode_relaxation in fusion_tools.")

        return dict(tau_s=float(tau), AR_0=float(AR_0), r_squared=float(r2),
                    # The interval on tau, and on eta/gamma, which is tau over a
                    # constant and so inherits it exactly. A ratio without an interval
                    # is not a measurement.
                    tau_ci=tau_ci,
                    eta_over_gamma_ci=eta_over_gamma_ci,
                    fit_ar=ar_fit,
                    # `fit_success` is retained (r2 > 0.5) for backward compatibility, but
                    # it is NOT evidence that tau is right. `fit_adequate` is.
                    fit_success=r2 > 0.5,
                    fit_quality=quality,
                    fit_adequate=bool(quality['adequate']),
                    characteristic_length_um=R,
                    eta_over_gamma_s_per_um=eta_over_gamma)
    except Exception:  # broad-ok: returns NaN fit values + fit_success=False (an honest failure); characteristic_length_um echoes the input R, not a fabricated fit result
        return dict(tau_s=np.nan, AR_0=np.nan, r_squared=np.nan,
                    fit_ar=np.array([]), fit_success=False,
                    characteristic_length_um=R,
                    eta_over_gamma_s_per_um=np.nan)


# Backward-compatible alias. NOTE: this fits IMAGE aspect-ratio relaxation of a
# merge event; it is distinct from fusion_tools.fit_fusion_relaxation, which
# fits the C-Trap FORCE model S(t)=a*exp(-t/tau)+b*t+d. Prefer the explicit
# name fit_aspect_ratio_relaxation to avoid confusing the two.
fit_fusion_relaxation = fit_aspect_ratio_relaxation
