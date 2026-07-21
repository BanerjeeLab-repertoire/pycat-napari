"""Condensate **coarsening kinetics** — split out of ``condensate_physics_tools`` by physical quantity (1.6.217).

``fit_coarsening`` classifies a mean-radius-vs-time series as Ostwald ripening (R ~ t^1/3), coalescence
(R ~ t^1/2), or arrested, via power-law fits, a slope-based arrest test, and a seeded residual bootstrap
for the confidence. Moved VERBATIM from ``condensate_physics_tools`` — no fit or number changed; pinned by
``test_fit_coarsening_output_is_byte_identical`` and the arrest-classification tests. The tools module
re-exports ``fit_coarsening`` for every caller.
"""
from __future__ import annotations

import numpy as np
from scipy import optimize, stats

from pycat.utils.general_utils import debug_log
from pycat.utils.notify import show_warning as napari_show_warning


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
    results = _coarsening_powerlaw_fits(t, R)

    # "Arrested" is not a power law, so an R² against the mean is meaningless — judge it by whether the
    # radius actually GREW (a slope test), never by a fit statistic. See `_coarsening_is_arrested`.
    R = np.asarray(R, dtype=float)
    radius_change = float(R[-1] - R[0])
    radius_change_frac = radius_change / R[0] if R[0] else np.nan

    best = max(['ostwald', 'coalescence'], key=lambda k: results[k]['r2'])
    if _coarsening_is_arrested(t, R):
        best = 'arrested'

    confidence, caveat, boot_confidence, _warn = _coarsening_confidence(t, R, results, best)
    if _warn:
        napari_show_warning("Coarsening: " + caveat)

    return dict(
        preferred_mechanism=best,
        mechanism_confidence=confidence,
        # The measured reproducibility of the call, not an R² gap that never fires.
        mechanism_bootstrap_agreement=boot_confidence,
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


# ── Phases of fit_coarsening (split out for reviewability; byte-identical) ─────────────────────────
# The coarsening classification is three phases: FIT the two power laws, decide whether the radius grew
# at all (the arrest slope-test), and ASSESS how reproducible the winning mechanism is (a residual
# bootstrap). Pinned by test_coarsening_arrest.test_fit_coarsening_output_is_byte_identical.

def _coarsening_powerlaw_fits(t, R):
    """Fit both coarsening power laws — Ostwald R = R0 + K·t^(1/3), coalescence R = R0 + K·t^(1/2) — and
    return ``{name: {'r2','K','R0','fit'}}``. A fit that fails to converge records r2 = −inf so the
    selection below never picks it."""
    def ostwald(t, R0, K):
        return R0 + K * t**(1/3)

    def coalescence(t, R0, K):
        return R0 + K * t**(1/2)

    results = {}
    for name, fn in [('ostwald', ostwald), ('coalescence', coalescence)]:
        try:
            p0 = [R[0], (R[-1] - R[0]) / max(t[-1]**(1/3 if name == 'ostwald' else 1/2), 1e-9)]
            popt, _ = optimize.curve_fit(fn, t, R, p0=p0, maxfev=3000)
            R_fit = fn(t, *popt)
            ss_res = np.sum((R - R_fit)**2)
            ss_tot = np.sum((R - R.mean())**2)
            r2 = 1 - ss_res / max(ss_tot, 1e-12)
            results[name] = {'r2': float(r2), 'K': float(popt[1]),
                             'R0': float(popt[0]), 'fit': R_fit}
        except Exception:
            results[name] = {'r2': -np.inf, 'K': np.nan, 'R0': np.nan, 'fit': np.full_like(t, np.nan)}
    return results


def _coarsening_is_arrested(t, R):
    """Did the radius grow at all? **A physical claim decided by a SLOPE test, never a fit statistic.**

    This was once ``max(ostwald_r2, coalescence_r2) < 0.3 or abs(radius_change) < 2·noise``. R² measures
    how well a power law describes the data — it says nothing about whether the radius grew, and noise
    destroys it while the radius keeps growing, so a genuinely coarsening series got reported as arrested.
    Measured: at 30 % scatter the old test called 42 % of genuinely coarsening series "arrested"; the
    slope test has zero false arrests there and still catches every genuinely arrested series. The honest
    question is whether the SLOPE is significantly positive (one-sided linear regression of R on t)."""
    try:
        _lin = stats.linregress(t, R)
        # One-sided: we are asking whether the radius GREW, not whether it changed.
        _grew = bool(_lin.slope > 0 and (_lin.pvalue / 2.0) < 0.05)
    except Exception as _exc:
        debug_log('coarsening: slope test failed', _exc)
        _grew = True                      # do not claim arrest on a failed test
    return not _grew


def _coarsening_confidence(t, R, results, best):
    """How reproducible is the mechanism call? Returns ``(confidence, caveat, boot_confidence, warn)``.

    ── Why a bootstrap, not an R² gap ──────────────────────────────────────────────────────────────
    The old gate required an R² GAP of 0.1 between the two models. Measured, the gap between t^(1/3) and
    t^(1/2) is about 0.008 even on noiseless data — both are concave-increasing and genuinely similar over
    a finite range — so the gate never fired and ``confidence`` was permanently 'low', carrying no
    information. Selection itself is fine (100 % correct at low noise, ~70 % at heavy). What was missing is
    an honest statement of WHICH regime you are in: bootstrap the residuals and ask how often the winning
    mechanism actually wins. That is measurable from the single dataset, needs no ground truth, and TRACKS
    the true correct-selection rate (true 100/90/83/73/70 % → bootstrap 100/95/81/68/59 %).
    """
    best_r2 = max(results['ostwald']['r2'], results['coalescence']['r2'])
    boot_confidence = float('nan')
    if best == 'arrested':
        return ('n/a (arrested)',
                "Radius barely changes — growth is effectively arrested; "
                "no coarsening exponent is fitted.",
                boot_confidence, False)

    try:
        _rng = np.random.default_rng(0)
        _win_fit = results[best]['fit']
        _res = R - _win_fit
        _wins = 0
        _nb = 200
        for _ in range(_nb):
            _Rb = _win_fit + _rng.choice(_res, size=_res.size, replace=True)
            _r2b = {}
            for _name, _p in (('ostwald', 1.0 / 3.0), ('coalescence', 0.5)):
                try:
                    _pf, _ = optimize.curve_fit(
                        lambda tt, a, b, _pp=_p: a + b * tt ** _pp,
                        t, _Rb, p0=[_Rb[0], 0.5], maxfev=20000)
                    _fit = _pf[0] + _pf[1] * t ** _p
                    _ssr = float(np.sum((_Rb - _fit) ** 2))
                    _sst = float(np.sum((_Rb - _Rb.mean()) ** 2))
                    _r2b[_name] = 1.0 - _ssr / _sst if _sst > 0 else -np.inf
                except Exception:
                    _r2b[_name] = -np.inf
            _wins += (max(_r2b, key=_r2b.get) == best)
        boot_confidence = _wins / _nb
    except Exception:
        boot_confidence = float('nan')

    if not np.isfinite(boot_confidence):
        # ── A FAILED bootstrap is a finding, not a missing value ────────────
        # The bootstrap could not fit the resampled data at all, which happens when the noise is large
        # enough that neither power law is stable — precisely when a user most needs to be told the
        # mechanism is undeterminable. Measured on synthetic Ostwald data (R ~ t^(1/3), 30 points): NaN
        # rate 0/0/12/42 % at noise 0.05/0.10/0.20/0.30, and where it DID fit at 0.30 the agreement was
        # 0.60 — barely a coin flip. So a NaN here is the answer (warn the user), not an inconvenience.
        caveat = ("**The mechanism could not be determined.** The bootstrap failed to fit "
                  "the resampled data, which happens when the scatter is large enough "
                  "that neither t^(1/3) (Ostwald ripening) nor t^(1/2) (coalescence) is "
                  "stable. This is a FINDING, not a missing value: the data does not "
                  "support a mechanistic call.\n\n"
                  "Measured on synthetic data, the bootstrap fails on 12 % of runs at 20 % "
                  "scatter and 42 % at 30 % scatter — and where it does succeed at that "
                  "level, it agrees with itself only 60 % of the time, barely better than "
                  "a coin flip.\n\n"
                  "Do NOT report a coarsening mechanism from this fit. Extend the time "
                  "range, or reduce the scatter in the radius measurement.")
        return 'low', caveat, boot_confidence, True
    if boot_confidence >= 0.90 and best_r2 > 0.85:
        return ('high',
                f"The same mechanism is selected in {boot_confidence:.0%} of bootstrap resamples.",
                boot_confidence, False)
    if boot_confidence >= 0.75:
        return ('moderate',
                f"The same mechanism is selected in only {boot_confidence:.0%} of "
                f"bootstrap resamples. t^(1/3) (Ostwald) and t^(1/2) (coalescence) "
                f"fit similarly over this time range — treat the call as "
                f"suggestive. A longer time range discriminates them.",
                boot_confidence, False)
    return ('low',
            f"The same mechanism is selected in only {boot_confidence:.0%} of "
            f"bootstrap resamples — **barely better than a coin flip.** "
            f"t^(1/3) and t^(1/2) are not distinguishable in this data. Do not "
            f"report a coarsening mechanism from it; extend the time range.",
            boot_confidence, False)
