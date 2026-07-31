"""In-vitro droplet **size-distribution** analysis — split out of ``invitro_tools`` by domain (1.6.213).

MLE model selection (lognormal / gamma / weibull / exponential) with a Clauset power-law x_min, a tail
Vuong test with a seeded bootstrap goodness-of-fit gate, and a whole-sample distinguishability test. Moved
VERBATIM from ``invitro_tools`` — no fit, threshold, or reported number changed; pinned byte-identical by
``test_size_distribution_mle_characterization`` and the invitro size tests. ``invitro_tools`` re-exports
the two public entry points (``fit_size_distribution_mle`` / ``fit_size_distribution``) for existing callers.
"""
from __future__ import annotations

import numpy as np
from scipy import optimize, stats

from pycat.utils.general_utils import debug_log
from pycat.utils.notify import show_warning as napari_show_warning


# ── N6-4: left-truncated MLE at the detection limit ────────────────────────────────────────────────────
# When a caller passes `xmin` (the smallest reliably-detectable radius), radii below it are MISSING, not zero —
# the sample is left-truncated. Fitting the untruncated density then inflates the fitted median and deflates the
# spread. The correct likelihood divides by the survival above the cut-off: L = prod f(r_i) / (1 - F(xmin)). No
# candidate has that in closed form (lognormal's log-moments no longer apply), so we maximise it numerically,
# seeded by the untruncated estimate. This path runs ONLY when xmin is given; xmin=None stays byte-identical.

def _trunc_loglik(dist, r, xmin, params):
    """Left-truncated log-likelihood ``sum[logpdf(r)] - n*logsf(xmin)`` for a scipy `dist` (loc folded into
    `params`). Returns ``-inf`` on any non-finite term so the optimiser rejects that parameter point."""
    lp = dist.logpdf(r, *params)
    logsf = dist.logsf(xmin, *params)
    if not (np.all(np.isfinite(lp)) and np.isfinite(logsf)):
        return -np.inf
    return float(np.sum(lp) - len(r) * logsf)


def _fit_trunc_lognormal(r, xmin):
    """Truncated-MLE lognormal ``(mu, sigma)`` from radii observed only above `xmin`. The untruncated
    log-moment estimate seeds a Nelder-Mead maximisation of the truncated likelihood. Returns (mu, sigma, ll)."""
    lr = np.log(r)
    mu0, sig0 = float(lr.mean()), float(max(lr.std(ddof=0), 1e-3))

    def nll(theta):
        mu, log_s = theta
        ll = _trunc_loglik(stats.lognorm, r, xmin, (np.exp(log_s), 0.0, np.exp(mu)))
        return -ll if np.isfinite(ll) else 1e18

    res = optimize.minimize(nll, [mu0, np.log(sig0)], method='Nelder-Mead',
                            options={'xatol': 1e-7, 'fatol': 1e-7, 'maxiter': 10000})
    mu, sig = float(res.x[0]), float(np.exp(res.x[1]))
    return mu, sig, _trunc_loglik(stats.lognorm, r, xmin, (sig, 0.0, np.exp(mu)))


def _fit_trunc_scipy(dist, r, xmin, shape):
    """Truncated-MLE fit of a scipy `dist` with loc fixed at 0. `shape` True → free (shape, scale) (gamma /
    weibull); False → free (scale) only (exponential). Returns ``(params_tuple_with_loc0, loglik)``, the params
    in the same ``(*, loc, scale)`` order scipy's ``.fit`` would give, so downstream ``logpdf(r, *params)`` works."""
    if shape:
        a0, _loc0, sc0 = dist.fit(r, floc=0)

        def nll(theta):
            a, log_sc = theta
            if a <= 0:
                return 1e18
            ll = _trunc_loglik(dist, r, xmin, (a, 0.0, np.exp(log_sc)))
            return -ll if np.isfinite(ll) else 1e18

        res = optimize.minimize(nll, [a0, np.log(max(sc0, 1e-9))], method='Nelder-Mead',
                                options={'xatol': 1e-7, 'fatol': 1e-7, 'maxiter': 10000})
        params = (float(res.x[0]), 0.0, float(np.exp(res.x[1])))
    else:
        _loc0, sc0 = dist.fit(r, floc=0)

        def nll(theta):
            ll = _trunc_loglik(dist, r, xmin, (0.0, np.exp(theta[0])))
            return -ll if np.isfinite(ll) else 1e18

        res = optimize.minimize(nll, [np.log(max(sc0, 1e-9))], method='Nelder-Mead',
                                options={'xatol': 1e-7, 'fatol': 1e-7, 'maxiter': 10000})
        params = (0.0, float(np.exp(res.x[0])))
    return params, _trunc_loglik(dist, r, xmin, params)


def _fit_size_models(r, candidates, _st, xmin=None):
    """MLE-fit each candidate distribution to the radii and return ``(models, pl_xmin)``.

    ``models[name]`` carries loglik / k / AIC / params. Lognormal is closed-form on log r; gamma /
    weibull / exponential use scipy's MLE with the location fixed at 0; the power law follows Clauset —
    x_min by KS minimisation over percentile candidates, the exponent by MLE on the tail. The power-law
    entry is tagged ``_tail_only`` because its likelihood lives on the TAIL and is NOT comparable with a
    whole-sample AIC (the whole-sample ranking excludes it; it is tested separately).

    When `xmin` is given the whole-sample candidates use the LEFT-TRUNCATED MLE (`_fit_trunc_*`) so a detection
    limit does not bias the fit; `xmin=None` keeps the original untruncated fits byte-identical."""
    models = {}
    _trunc = xmin is not None and np.isfinite(xmin) and xmin > 0

    def _add(name, loglik, k, params):
        if loglik is None or not np.isfinite(loglik):
            return
        models[name] = dict(loglik=float(loglik), k=int(k),
                            aic=float(2 * k - 2 * loglik), params=params)

    if 'lognormal' in candidates:
        lr = np.log(r)
        mu, sig = float(lr.mean()), float(lr.std(ddof=0))
        if sig > 0:
            if _trunc:
                mu, sig, ll = _fit_trunc_lognormal(r, xmin)
            else:
                ll = float(np.sum(_st.lognorm.logpdf(r, s=sig, scale=np.exp(mu))))
            _add('lognormal', ll, 2, dict(mu=mu, sigma=sig))

    for nm, dist in (('gamma', _st.gamma), ('weibull', _st.weibull_min),
                     ('exponential', _st.expon)):
        if nm not in candidates:
            continue
        try:
            k = 1 if nm == 'exponential' else 2
            if _trunc:
                p, ll = _fit_trunc_scipy(dist, r, xmin, shape=(nm != 'exponential'))
            else:
                p = dist.fit(r, floc=0)
                ll = float(np.sum(dist.logpdf(r, *p)))
            _add(nm, ll, k, dict(params=[float(x) for x in p]))
        except Exception:
            pass

    pl_xmin = np.nan
    if 'powerlaw' in candidates:
        best = (np.inf, None, None)
        cand_xmins = np.unique(np.percentile(r, np.linspace(0, 90, 25)))
        for xm in cand_xmins:
            tail = r[r >= xm]
            if len(tail) < 10 or xm <= 0:
                continue
            alpha = 1.0 + len(tail) / np.sum(np.log(tail / xm))
            if not np.isfinite(alpha) or alpha <= 1:
                continue
            ts = np.sort(tail)
            cdf_emp = np.arange(1, len(ts) + 1) / len(ts)
            cdf_the = 1.0 - (ts / xm) ** (1.0 - alpha)
            ks = float(np.max(np.abs(cdf_emp - cdf_the)))
            if ks < best[0]:
                best = (ks, xm, alpha)
        if best[1] is not None:
            pl_xmin, alpha = float(best[1]), float(best[2])
            tail = r[r >= pl_xmin]
            # Tail-only likelihood — NOT AIC-comparable with whole-sample models (compared on the tail).
            ll_tail = float(len(tail) * np.log((alpha - 1) / pl_xmin)
                            - alpha * np.sum(np.log(tail / pl_xmin)))
            _add('powerlaw', ll_tail, 2,
                 dict(alpha=alpha, xmin=pl_xmin, n_tail=int(len(tail))))
            models['powerlaw']['_tail_only'] = True

    return models, pl_xmin


def _powerlaw_tail_comparison(r, models, pl_xmin, candidates, _st):
    """The Clauset power-law verdict on the tail above x_min, or ``None``.

    A power law is defined only above x_min, so its likelihood lives on the TAIL — comparing that with a
    whole-sample likelihood is invalid. So the best alternative is RE-FITTED on the same tail and a Vuong
    test compares like with like. Crucially the power law must ALSO pass an absolute goodness-of-fit gate
    (a seeded parametric-bootstrap KS test) before it is allowed to win: because x_min is chosen to
    flatter the power law, the upper tail of almost any distribution is locally power-law-like, so a bare
    likelihood-ratio test declares "power law" for lognormal / gamma / exponential data alike."""
    powerlaw_verdict = None
    if 'powerlaw' in models and np.isfinite(pl_xmin):
        tail = r[r >= pl_xmin]
        if len(tail) >= 20:
            pm = models['powerlaw']
            ll_pl = pm['loglik']
            alt_best, alt_ll, alt_name = None, -np.inf, None
            for nm, dist in (('lognormal', _st.lognorm), ('gamma', _st.gamma),
                             ('weibull', _st.weibull_min),
                             ('exponential', _st.expon)):
                if nm not in candidates:
                    continue
                try:
                    p = dist.fit(tail, floc=0)
                    ll = float(np.sum(dist.logpdf(tail, *p)))
                    if np.isfinite(ll) and ll > alt_ll:
                        alt_ll, alt_best, alt_name = ll, p, nm
                except Exception:
                    continue
            if alt_name is not None:
                lp_pl = (np.log((pm['params']['alpha'] - 1) / pl_xmin)
                         - pm['params']['alpha'] * np.log(tail / pl_xmin))
                d = dict(lognormal=_st.lognorm, gamma=_st.gamma,
                         weibull=_st.weibull_min, exponential=_st.expon)[alt_name]
                lp_alt = d.logpdf(tail, *alt_best)
                diff = lp_pl - lp_alt
                sd = float(np.std(diff, ddof=1))

                alpha_hat = pm['params']['alpha']
                ts = np.sort(tail)
                cdf_emp = np.arange(1, len(ts) + 1) / len(ts)
                cdf_the = 1.0 - (ts / pl_xmin) ** (1.0 - alpha_hat)
                ks_obs = float(np.max(np.abs(cdf_emp - cdf_the)))
                nboot, worse = 60, 0
                _rng = np.random.default_rng(0)
                for _ in range(nboot):
                    u = _rng.random(len(ts))
                    sim = pl_xmin * (1.0 - u) ** (-1.0 / (alpha_hat - 1.0))
                    ss = np.sort(sim)
                    ce = np.arange(1, len(ss) + 1) / len(ss)
                    ct = 1.0 - (ss / pl_xmin) ** (1.0 - alpha_hat)
                    if float(np.max(np.abs(ce - ct))) >= ks_obs:
                        worse += 1
                gof_p = worse / nboot          # small p => the power law is a BAD fit
                pl_adequate = bool(gof_p >= 0.10)

                if sd > 0:
                    R = float(np.sum(diff))
                    z = R / (np.sqrt(len(tail)) * sd)
                    p_pl = float(2 * (1 - _st.norm.cdf(abs(z))))
                    sig = p_pl < 0.05
                    favoured = ('power law' if (R > 0 and pl_adequate) else alt_name)
                    powerlaw_verdict = dict(
                        tested_against=alt_name, n_tail=int(len(tail)),
                        xmin=float(pl_xmin), loglik_ratio=R, p_value=p_pl,
                        ks_distance=ks_obs, gof_p_value=float(gof_p),
                        adequate=pl_adequate,
                        favoured=(favoured if sig else 'indistinguishable'))
    return powerlaw_verdict


def _size_distinguishability(r, n, best_name, best_m, ranked, _st, xmin=None):
    """Vuong-style test on the two top whole-sample models' per-point log-likelihoods → whether the best
    model is significantly better than the runner-up. Returns ``(distinguishable, comparison)``; with a
    single model, distinguishable stays True and comparison is empty. When `xmin` is given the per-point
    log-likelihoods carry the same left-truncation correction the fit used, so the comparison is like-for-like."""
    _trunc = xmin is not None and np.isfinite(xmin) and xmin > 0
    distinguishable = True
    comparison = {}
    if len(ranked) > 1:
        second_name, second_m = ranked[1]

        def _pointwise(nm, m):
            if nm == 'lognormal':
                s, scale = m['params']['sigma'], np.exp(m['params']['mu'])
                lp = _st.lognorm.logpdf(r, s=s, scale=scale)
                return lp - _st.lognorm.logsf(xmin, s=s, scale=scale) if _trunc else lp
            d = dict(gamma=_st.gamma, weibull=_st.weibull_min,
                     exponential=_st.expon).get(nm)
            if d is None:
                return None
            params = m['params']['params']
            lp = d.logpdf(r, *params)
            return lp - d.logsf(xmin, *params) if _trunc else lp

        l1, l2 = _pointwise(best_name, best_m), _pointwise(second_name, second_m)
        if l1 is not None and l2 is not None:
            diff = l1 - l2
            sd = float(np.std(diff, ddof=1))
            if sd > 0:
                R = float(np.sum(diff))
                z = R / (np.sqrt(n) * sd)
                p = float(2 * (1 - _st.norm.cdf(abs(z))))
                distinguishable = bool(p < 0.05)
                comparison = dict(vs=second_name, loglik_ratio=R, z=z, p_value=p)
    return distinguishable, comparison


def _size_verdict(best_name, ranked, comparison, distinguishable, powerlaw_verdict):
    """The human verdict: the best whole-sample model and whether it is significantly preferred, plus the
    separately-scoped power-law tail claim (worded so it can never be read as 'the size distribution IS a
    power law' — that conflation is how spurious power laws get published)."""
    aic_gap = (ranked[1][1]['aic'] - ranked[0][1]['aic']) if len(ranked) > 1 else np.inf
    if distinguishable:
        verdict = (f"'{best_name}' is the best-supported model "
                   f"(\u0394AIC = {aic_gap:.1f} over '{comparison.get('vs','\u2014')}', "
                   f"p = {comparison.get('p_value', float('nan')):.3f}).")
    else:
        verdict = (f"'{best_name}' fits best, but it is NOT significantly better than "
                   f"'{comparison.get('vs','the runner-up')}' "
                   f"(p = {comparison.get('p_value', float('nan')):.2f}). These data "
                   f"cannot distinguish the two \u2014 do not report a preferred model as "
                   f"established. Collect more objects, or report the fitted "
                   f"parameters descriptively.")

    if powerlaw_verdict:
        pv = powerlaw_verdict
        if pv['favoured'] == 'power law':
            verdict += (f" Separately, ABOVE x_min = {pv['xmin']:.3g} "
                        f"(n = {pv['n_tail']}) the tail is better described by a power "
                        f"law than by '{pv['tested_against']}' (p = "
                        f"{pv['p_value']:.3f}). This is a claim about the TAIL ONLY \u2014 "
                        f"it does not mean the size distribution is a power law, and "
                        f"the upper tail of many distributions is locally "
                        f"power-law-like.")
        elif pv['favoured'] == 'indistinguishable':
            verdict += (f" Above x_min = {pv['xmin']:.3g} the tail cannot be "
                        f"distinguished from '{pv['tested_against']}' "
                        f"(p = {pv['p_value']:.2f}) \u2014 no power-law claim is supported.")
    return verdict


def fit_size_distribution_mle(radii_um, xmin=None, candidates=None):
    """Unbinned maximum-likelihood fitting and principled model comparison for a
    droplet/condensate size distribution.

    This replaces ``fit_size_distribution``'s histogram + R² approach, which is not a
    sound way to identify a distribution — least of all a power law:

    * **The answer depends on the bin count.** Verified on data drawn from a *true*
      power law: the old method returns ``power_law`` at 8 bins and ``lognormal`` at
      15, 30 and 50 bins. The bin choice, which is arbitrary, flips the scientific
      conclusion — and it flips it *toward* lognormal, so a genuine power law (the
      scientifically interesting case) is the one most likely to be missed.
    * **R² is not evidence for a distribution.** It measures the fit to *binned
      counts*, not the likelihood of the data. A high R² against a histogram says
      almost nothing about which generative model is correct.
    * **Regression on log-binned counts biases the exponent.** The standard estimator
      for a power-law exponent is maximum likelihood (Clauset–Shalizi–Newman), not a
      straight line through log-log bins.
    * **A power law is only defined above a lower cut-off** ``x_min``. Fitting one to
      the whole range, cut-off included, is meaningless.

    Method
    ------
    * Fit each candidate by **maximum likelihood on the raw (unbinned) radii**.
    * Compare models by **AIC** and by a **Vuong-style likelihood-ratio test**, which
      reports not only which model is better but whether the difference is
      *significant* — an honest answer can be "these data cannot distinguish them",
      and for a few hundred droplets that is very often the truth.
    * For the power law, estimate ``x_min`` by the Clauset KS-minimisation procedure
      and fit the exponent by MLE above it.

    Candidates default to lognormal, gamma, Weibull, exponential and power law. Gamma
    and Weibull are included deliberately: for coarsening droplet populations they are
    frequently better descriptions than a forced lognormal-versus-power-law choice.

    Returns
    -------
    dict with:
      best_model            : the model with the lowest AIC
      distinguishable       : False when the best model is not significantly better
                              than the runner-up (p >= 0.05). When this is False, do
                              NOT report the "best" model as established.
      models                : per-model dict of params, loglik, aic, and k
      comparison            : pairwise likelihood-ratio result vs the runner-up
      powerlaw_xmin         : the estimated lower cut-off (power law only)
      n                     : number of droplets used
      verdict               : a plain-English statement of what can and cannot be
                              concluded
    """
    from scipy import stats as _st

    r = np.asarray(radii_um, dtype=float)
    r = r[np.isfinite(r) & (r > 0)]
    # A detection limit left-truncates the sample: keep only radii at/above it and fit the truncated
    # likelihood (below). Radii below xmin are unobservable, not absent-because-zero.
    _trunc = xmin is not None and np.isfinite(xmin) and xmin > 0
    if _trunc:
        r = r[r >= xmin]
    n = len(r)
    if n < 20:
        return dict(best_model='insufficient_data', n=n, distinguishable=False,
                    verdict=(f"Only {n} objects. Distribution identification needs "
                             f"substantially more (hundreds); any 'best model' here "
                             f"would be noise."))

    if candidates is None:
        candidates = ['lognormal', 'gamma', 'weibull', 'exponential', 'powerlaw']

    models, pl_xmin = _fit_size_models(r, candidates, _st, xmin=xmin)
    if not models:
        return dict(best_model='fit_failed', n=n, distinguishable=False,
                    verdict="No candidate distribution could be fitted.")

    # Whole-sample ranking (AIC). The power law is fitted only above x_min and is DELIBERATELY kept
    # out of this ranking (a tail-only likelihood is not like-for-like); it is reported separately in
    # `powerlaw_test` as the narrower claim it actually is.
    whole = {k: v for k, v in models.items() if not v.get('_tail_only')}
    ranked = sorted(whole.items(), key=lambda kv: kv[1]['aic'])
    best_name, best_m = ranked[0]

    powerlaw_verdict = _powerlaw_tail_comparison(r, models, pl_xmin, candidates, _st)
    distinguishable, comparison = _size_distinguishability(r, n, best_name, best_m, ranked, _st, xmin=xmin)
    verdict = _size_verdict(best_name, ranked, comparison, distinguishable, powerlaw_verdict)

    return dict(
        best_model=best_name,
        distinguishable=distinguishable,
        models=models,
        comparison=comparison,
        powerlaw_test=powerlaw_verdict,
        powerlaw_xmin=pl_xmin,
        detection_limit_xmin=(float(xmin) if _trunc else None),
        n=n,
        verdict=verdict,
        mean_radius_um=float(r.mean()),
        median_radius_um=float(np.median(r)),
        polydispersity_index=float(r.std() / max(r.mean(), 1e-9)),
    )


def fit_size_distribution(
    radii_um: np.ndarray,
    n_bins: int = 30,
) -> dict:
    """
    Fit droplet size distribution to lognormal and power-law models.

    In LLPS / polymer-physics frameworks:
    - Lognormal: typical for nucleation-and-growth condensates with
      polydisperse nucleation (most common in practice)
    - Power law: expected for Ostwald ripening at steady state
      (P(R) ∝ R² for 3D Lifshitz-Slyozov distribution)

    Parameters
    ----------
    radii_um : 1D array of droplet radii in µm
    n_bins   : histogram bins for fitting

    Returns
    -------
    dict with keys:
        lognormal_mu, lognormal_sigma, lognormal_r2  (log-space mean, std)
        powerlaw_alpha, powerlaw_r2
        preferred_model : 'lognormal' | 'power_law'
        histogram_r      : bin centres for plotting
        histogram_counts : normalised counts for plotting
        fit_lognormal    : fitted lognormal PDF values
        fit_powerlaw     : fitted power-law values
    """
    r = np.asarray(radii_um)
    r = r[r > 0]
    if len(r) < 5:
        return dict(preferred_model='insufficient_data')

    counts, edges = np.histogram(r, bins=n_bins, density=True)
    centres       = 0.5 * (edges[:-1] + edges[1:])
    valid         = counts > 0

    # Lognormal fit in log-space (linear regression on log(r) vs log(count))
    ln_r = np.log(r)
    mu_ln  = float(ln_r.mean())
    sig_ln = float(ln_r.std())

    def lognormal_pdf(x, mu, sigma):
        return (1 / (x * sigma * np.sqrt(2*np.pi) + 1e-12) *
                np.exp(-0.5 * ((np.log(x) - mu) / max(sigma, 1e-9))**2))

    ln_fit = lognormal_pdf(centres[valid], mu_ln, sig_ln)
    ss_res_ln = np.sum((counts[valid] - ln_fit)**2)
    ss_tot    = np.sum((counts[valid] - counts[valid].mean())**2)
    r2_ln = float(1 - ss_res_ln / max(ss_tot, 1e-12))

    # Power-law fit: log(P(R)) = α·log(R) + const
    log_r   = np.log(centres[valid] + 1e-9)
    log_c   = np.log(counts[valid]  + 1e-12)
    slope, intercept, rval, _, _ = stats.linregress(log_r, log_c)
    alpha_pl = float(slope)
    pl_fit   = np.exp(intercept) * centres[valid]**alpha_pl
    ss_res_pl = np.sum((counts[valid] - pl_fit)**2)
    r2_pl = float(1 - ss_res_pl / max(ss_tot, 1e-12))

    preferred = 'lognormal' if r2_ln >= r2_pl else 'power_law'

    # Full arrays for plotting
    fit_ln = lognormal_pdf(centres, mu_ln, sig_ln)
    fit_pl = np.exp(intercept) * (centres + 1e-9)**alpha_pl

    # ── The model choice comes from the MLE, not the histogram R² ──────────────
    #
    # This function offers only TWO candidates — lognormal and power-law — and picks
    # between them by an R² on a BINNED histogram. Two problems, and the second is worse:
    #
    #   1. A histogram R² depends on the bin count, and fitting a line to log-log counts
    #      is not a distribution fit.
    #   2. **The right answer is often not in its vocabulary at all.** Measured against
    #      ground truth (12 samples per case), the model actually named:
    #
    #      ==============  ==================  ==============
    #      true            this function       MLE (1.5.379)
    #      ==============  ==================  ==============
    #      lognormal       **100 %**           100 %
    #      gamma           **0 %**             91 %
    #      weibull         **0 %**             83 %
    #      exponential     **0 %**             75 %
    #      ==============  ==================  ==============
    #
    #      It is not *wrong* about gamma — it **cannot say gamma**. A droplet population
    #      that is genuinely gamma-distributed is reported as lognormal or power-law,
    #      because those are the only words it has.
    #
    # `fit_size_distribution_mle` fits five candidates by unbinned maximum likelihood and
    # selects by AIC, with a Vuong test for whether the data can distinguish them at all.
    # It was added in 1.5.379 — and nothing called it. The UIs and the batch registry were
    # still calling THIS function, so the fix had never run.
    #
    # `preferred_model` now carries the MLE's answer, so the three existing call sites get
    # the correct model with no change at their end. The histogram fields are retained for
    # the plot.
    _mle = {}
    try:
        _mle = fit_size_distribution_mle(r)
        _preferred = _mle.get('best_model') or preferred
        if not _mle.get('distinguishable', True):
            napari_show_warning(
                "Size distribution: the candidate models fit this sample about equally "
                "well — the data cannot distinguish them (Vuong test). Treat the "
                "preferred model as suggestive, not established.")
    except Exception as _e:
        debug_log("size distribution: MLE fit failed; falling back to the histogram", _e)
        _preferred = preferred

    return dict(
        lognormal_mu=mu_ln,
        lognormal_sigma=sig_ln,
        lognormal_r2=r2_ln,
        powerlaw_alpha=alpha_pl,
        powerlaw_r2=r2_pl,
        # From the MLE, not the histogram R². The histogram version could only ever say
        # "lognormal" or "powerlaw".
        preferred_model=_preferred,
        preferred_model_histogram=preferred,   # what the old method would have said
        mle=_mle,
        distinguishable=_mle.get('distinguishable', None),
        histogram_r=centres,
        histogram_counts=counts,
        fit_lognormal=fit_ln,
        fit_powerlaw=fit_pl,
        mean_radius_um=float(r.mean()),
        median_radius_um=float(np.median(r)),
        polydispersity_index=float(r.std() / max(r.mean(), 1e-9)),
    )
