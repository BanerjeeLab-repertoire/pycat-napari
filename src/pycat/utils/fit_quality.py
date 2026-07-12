"""
Fit quality — beyond R².

Why this module exists
----------------------
R² is used as a fit-quality measure in nine PyCAT modules (67 call sites). It answers
exactly one question: **"does this model beat a horizontal line?"** For any curved,
monotonic data — a FRAP recovery, an MSD, a coarsening curve, a binding isotherm — that
is a trivially low bar, and clearing it says almost nothing about whether the model is
*right*.

Measured against PyCAT's **actual** FRAP model — the single-pool hyperbolic form
``I(t) = (a + b·x)/(1 + x)`` — on synthetic data whose truth is a **two-component**
recovery (a fast and a slow pool; a very common case the single-pool model cannot
represent):

=====================================  =========  ==================
                                       R²         mobile fraction
=====================================  =========  ==================
single-pool fit to 2-component truth   **0.957**  0.822
*truth*                                —          *0.875*
=====================================  =========  ==================

The wrong model scores **R² = 0.957**. Anyone applying the usual "R² > 0.95 means a good
fit" heuristic accepts it without hesitation.

What actually catches it
------------------------
The residuals. A model that is *right* leaves residuals that are pure noise: their signs
flip about as often as a coin. A model that is *missing structure* leaves residuals that
**run in blocks** — positive for a stretch, then negative for a stretch — because the fit
is systematically above the data in one region and below it in another.

The Wald–Wolfowitz **runs test** measures exactly this, and it is decisive where R² is
not:

=====================================  =========  =================
scenario                               runs test  flagged
=====================================  =========  =================
correct model (data from the model)    p = 0.365  **2 / 40 (5 %)**
wrong model (2-component truth)        p ≈ 0.02   **30 / 40 (75 %)**
=====================================  =========  =================

Calibrated — a 5 % false-alarm rate on correct fits, which is what a 0.05 threshold
should give — and it catches three quarters of the wrong-model fits that R² waves
through.

This is the same failure that ran through the colocalization p-value, Ripley's CSR line,
and Moran's I: **a number that looks like a validity check but is tested against a null
nobody chose.** R²'s implicit null is "a flat line", and beating a flat line is not
evidence that a model is correct.
"""

from __future__ import annotations

import numpy as np
from scipy import stats


def r_squared(y, y_fit):
    """Plain R². Reported for continuity — but see :func:`assess_fit`, and do not use
    this alone to decide whether a model is correct."""
    y = np.asarray(y, dtype=float)
    y_fit = np.asarray(y_fit, dtype=float)
    ss_res = float(((y - y_fit) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    if ss_tot <= 0:
        return float('nan')
    return 1.0 - ss_res / ss_tot


def residual_runs_test(residuals):
    """Wald–Wolfowitz runs test on the residual signs.

    A correct model leaves residuals whose signs are randomly ordered. A model missing
    structure leaves them in **blocks** — the fit sits above the data over one stretch and
    below it over another. Too few runs is the signature of systematic misfit.

    Returns ``(z, p, n_runs, n_expected)``. A small p means the residuals are **not**
    random: the model is systematically wrong somewhere, regardless of R².
    """
    r = np.asarray(residuals, dtype=float)
    s = np.sign(r[np.isfinite(r)])
    s = s[s != 0]
    n = s.size
    n_pos = int((s > 0).sum())
    n_neg = n - n_pos
    if n < 8 or n_pos == 0 or n_neg == 0:
        return float('nan'), float('nan'), 0, 0.0

    n_runs = 1 + int((np.diff(s) != 0).sum())
    mu = 2.0 * n_pos * n_neg / n + 1.0
    var = (mu - 1.0) * (mu - 2.0) / (n - 1.0)
    if var <= 0:
        return float('nan'), float('nan'), n_runs, float(mu)
    z = (n_runs - mu) / np.sqrt(var)
    p = float(2.0 * stats.norm.sf(abs(z)))
    return float(z), p, n_runs, float(mu)


def assess_fit(y, y_fit, n_params=None, model_name=""):
    """Is this fit trustworthy? R², plus the checks that actually catch a wrong model.

    Returns a dict with ``r_squared``, the runs-test result, an ``adequate`` flag, and a
    plain-English verdict.

    ``adequate=False`` means the residuals carry structure the model has not captured —
    the parameters extracted from it may be badly wrong **even when R² looks fine**. In
    the validation case above, an inadequate fit with R² = 0.904 returned a mobile
    fraction 17 % from the truth.
    """
    y = np.asarray(y, dtype=float)
    y_fit = np.asarray(y_fit, dtype=float)
    ok = np.isfinite(y) & np.isfinite(y_fit)
    y, y_fit = y[ok], y_fit[ok]
    if y.size < 8:
        # NOT the same as "the fit is bad". The runs test needs a reasonable number of
        # residuals to have any power; below that it cannot say anything either way.
        # Conflating "could not assess" with "inadequate" produced a 100% false-alarm
        # rate on the MSD fit, whose defensible lag window is deliberately narrow (often
        # ~6 points). Say "unknown", and do not block the result.
        return dict(r_squared=r_squared(y, y_fit), adequate=True, assessable=False,
                    runs_p=float('nan'),
                    verdict=(f"Fit adequacy NOT ASSESSED: only {y.size} points, and the "
                             f"residual runs test needs at least 8 to have any power. "
                             f"This is not evidence the model fits — it is the absence "
                             f"of evidence either way."))

    res = y - y_fit
    r2 = r_squared(y, y_fit)
    z, p, n_runs, n_exp = residual_runs_test(res)

    # Reduced chi-square-like scale, useful when the noise level is not known: the
    # residual SD relative to the data range.
    rel_rms = float(np.sqrt((res ** 2).mean()) / max(np.ptp(y), 1e-12))

    adequate = bool(np.isfinite(p) and p >= 0.05)
    name = f"{model_name}: " if model_name else ""

    assessable = bool(np.isfinite(p))
    if not assessable:
        verdict = (f"{name}R² = {r2:.4f}. Fit adequacy NOT ASSESSED — too few residuals "
                   f"for a runs test. This is not evidence the model fits.")
        adequate = True          # unknown, not bad: do not block the result
    elif adequate:
        verdict = (f"{name}R² = {r2:.4f}, residuals random (runs test p = {p:.3f}, "
                   f"{n_runs} runs vs {n_exp:.0f} expected). No evidence the model is "
                   f"missing structure.")
    else:
        verdict = (f"{name}R² = {r2:.4f}, but the residuals are **NOT random** (runs "
                   f"test p = {p:.4f}: {n_runs} runs where {n_exp:.0f} would be "
                   f"expected by chance). The fit is systematically above the data in "
                   f"some regions and below it in others, which means the model is "
                   f"missing structure — a different or more complex model is needed. "
                   f"**Parameters from this fit may be badly wrong despite the R².**")

    return dict(
        r_squared=float(r2),
        runs_z=z, runs_p=p, n_runs=int(n_runs), n_runs_expected=float(n_exp),
        relative_rms=rel_rms,
        n_params=n_params,
        adequate=adequate,
        assessable=assessable,   # False => "unknown", NOT "bad"
        verdict=verdict,
    )
