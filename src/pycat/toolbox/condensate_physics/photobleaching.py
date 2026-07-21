"""Condensate **photobleaching** correction — split out of condensate_physics_tools by quantity (1.6.218).

fit_photobleaching fits a single-exponential bleach with a covariance-based tau CI and an observation-window
adequacy warning; apply_bleach_correction divides it out. Moved VERBATIM - no fit or number changed; pinned
by test_photobleaching_characterization + window tests. The tools module re-exports both.
"""
from __future__ import annotations

import numpy as np
from scipy import optimize
from pycat.utils.general_utils import debug_log
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.notify import show_info as napari_show_info


# ---------------------------------------------------------------------------
# 5. Photobleaching correction
# ---------------------------------------------------------------------------

def _photobleach_tau_ci(pcov, tau):
    """The 95% CI on the bleach time constant from the fit covariance, or None.

    A movie shorter than tau cannot measure tau, and the covariance is the only evidence that tau is
    determined at all (R2 does not carry it: with a true tau of 50 s, a 10 s movie recovers tau = 35 +/-
    19 at R2 = 0.881). It matters because the bleach correction DIVIDES by exp(-t/tau) — a tau too small
    over-corrects and the error compounds exponentially."""
    tau_ci = None
    try:
        _perr = np.sqrt(np.diag(pcov))
        _se = float(_perr[1])
        if np.isfinite(_se) and _se > 0:
            tau_ci = (float(tau - 1.96 * _se), float(tau + 1.96 * _se))
    except Exception as _exc:
        debug_log('photobleaching: could not assess tau uncertainty', _exc)
    return tau_ci


def _photobleach_window_metrics(I, I_inf):
    """How much of the exponential was ACTUALLY observed — two bounds, because ONE cannot be trusted.

    A movie shorter than tau is nearly linear and you cannot fit a decay constant to a straight line.
    Five single-number attempts failed (recorded so the next person does not repeat them):
      1. ``movie_length / tau_fitted`` — CIRCULAR: the quantity checked against is the thing that is wrong.
      2. ``-log(I_end/I_start)`` on RAW intensity — assumes decay to ZERO; a non-bleaching floor makes it
         UNDERSTATE the decay (false alarm on good data with a floor).
      3. subtract the FITTED I_inf — I_inf is itself badly determined on a short movie (fits 771 vs a true
         200 at 0.2 tau); just as circular where it matters.
      4. subtract I_min — OVERSTATES (the minimum is a noise excursion).
      5. exponential-vs-linear R2 gap — the right question, but came out NON-MONOTONIC with the window.
    So BOTH bounds are reported: ``decay_observed_no_floor`` assumes decay to zero (a LOWER bound — it
    understates when there is a floor); ``decay_observed_floor_subtracted`` uses the fitted floor (an
    UPPER bound — it overstates on short movies where I_inf is over-fitted). The max of the two is ALSO
    wrong (non-monotonic), so the warning fires on the no-floor bound and states its known weakness."""
    _n_edge = max(1, len(I) // 10)
    _I_start = float(np.median(I[:_n_edge]))
    _I_end = float(np.median(I[-_n_edge:]))

    _decay_no_floor = float(-np.log(
        float(np.clip(_I_end / max(_I_start, 1e-9), 1e-9, 1.0))))

    _floor = float(np.clip(I_inf, 0.0, float(np.min(I))))
    _decay_floor_sub = float(-np.log(float(np.clip(
        max(_I_end - _floor, 1e-9) / max(_I_start - _floor, 1e-9), 1e-9, 1.0))))

    _window_in_taus = _decay_no_floor
    return _window_in_taus, _decay_no_floor, _decay_floor_sub


def _photobleach_window_warn(_window_in_taus, tau, t, r2, tau_ci, _decay_no_floor, _decay_floor_sub):
    """Two-tier warning on the observation window, measured on the DECAY ACTUALLY OBSERVED (not the fitted
    tau, which would be circular — on a 0.2-tau movie tau fits to 11 s against a true 50, so a length/tau
    ratio comes out 0.9 and passes). Measured bias in tau vs window (true tau 50 s): under 5% from about
    0.8 tau onward, ~15% low at 0.5 tau, ~30% low with sd 19 at 0.2 tau — so two tiers, not one: severe
    below 0.5, mild between 0.5 and 0.8."""
    if _window_in_taus < 0.5:
        napari_show_warning(
            f"Photobleaching: the movie shows only {_window_in_taus:.2f} bleach time "
            f"constants long (tau = {tau:.1f} s, movie = {t[-1]:.1f} s). **You cannot "
            f"measure a decay constant from a window much shorter than the decay.**\n\n"
            f"R² = {r2:.3f} and that is not a contradiction — the curve does fit the "
            f"frames that exist. Measured on synthetic data with a true tau of 50 s, a "
            f"movie a fifth of that length recovers tau = 35 ± 19 with "
            f"R² = 0.881.\n\n"
            f"**This matters more than the scatter suggests: the bleach correction "
            f"divides by exp(-t/tau), so a tau that is too small OVER-CORRECTS, and the "
            f"error compounds exponentially.** On a movie a fifth of the bleach time the "
            f"final frame is over-corrected by ~96 % — the correction nearly doubles it, "
            f"and every intensity downstream inherits that.\n\n"
            f"CAVEAT: this check assumes the signal decays toward zero. If your "
            f"sample has a large non-bleaching floor (autofluorescence, an "
            f"immobile fraction), it UNDERSTATES the decay actually observed and "
            f"may fire on an adequate movie. Compare the two bounds in the result: "
            f"decay_observed_no_floor = {_decay_no_floor:.2f} (assumes no floor) "
            f"vs decay_observed_floor_subtracted = {_decay_floor_sub:.2f} (uses the "
            f"fitted floor, which is itself over-fitted on a short movie). If they "
            f"disagree strongly, you have a floor.")
    elif _window_in_taus < 0.8:
        napari_show_warning(
            f"Photobleaching: the movie is {_window_in_taus:.2f} bleach time constants "
            f"long (tau = {tau:.1f} s). Measured on synthetic data, tau is biased LOW by "
            f"about 15 % at half a bleach constant of observation, and the bleach "
            f"correction divides by exp(-t/tau) — so a tau that is too small "
            f"over-corrects. Treat the correction as approximate; the bias is under 5 % "
            f"from about 0.8 bleach constants onward."
            + (f" 95% CI on tau: [{tau_ci[0]:.1f}, {tau_ci[1]:.1f}]." if tau_ci else ""))


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
        popt, pcov = optimize.curve_fit(model, t, I, p0=p0,
                                      bounds=bounds, maxfev=5000)
        I0, tau, I_inf = popt
        I_fit = model(t, *popt)
        ss_res = np.sum((I - I_fit)**2)
        ss_tot = np.sum((I - I.mean())**2)
        r2 = 1 - ss_res / max(ss_tot, 1e-12)

        # Correction factors: multiply frame t by I(0)/I(t)
        correction = I_fit[0] / np.maximum(I_fit, 1e-9)

        # tau CI (the only evidence tau is determined), the two decay-observed bounds, and the
        # two-tier window warning — each its own phase helper (see there for the measured rationale).
        tau_ci = _photobleach_tau_ci(pcov, tau)
        _window_in_taus, _decay_no_floor, _decay_floor_sub = _photobleach_window_metrics(I, I_inf)
        _photobleach_window_warn(_window_in_taus, tau, t, r2, tau_ci,
                                 _decay_no_floor, _decay_floor_sub)

        return dict(I0=float(I0), tau_bleach_s=float(tau),
                    # The interval on tau, and how much of the decay was actually observed.
                    tau_ci=tau_ci,
                    # BOTH bounds — a single number cannot do this honestly. See the helper.
                    observation_window_in_taus=float(_window_in_taus),
                    decay_observed_no_floor=float(_decay_no_floor),
                    decay_observed_floor_subtracted=float(_decay_floor_sub),
                    I_inf=float(I_inf), r_squared=float(r2),
                    fit_success=r2 > 0.7,
                    fit_intensities=I_fit,
                    correction_factors=correction.astype(np.float32))
    except Exception:  # broad-ok: fit_success=False + identity correction_factors (no bleach correction applied) — the safe honest fallback when the bleach fit fails, flagged for the caller
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
