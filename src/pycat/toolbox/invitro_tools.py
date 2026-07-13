"""
PyCAT In Vitro Condensate Toolbox
====================================
Analysis functions specific to in vitro droplet / LLPS assays imaged on
a coverslip without cells.

Core differences from cellular analysis
-----------------------------------------
- No cell mask: the whole imaging field is the sample
- Background = buffer (very clean, uniform intensity baseline)
- Volume fraction (Φ) replaces condensate fraction (% of cell area)
- Partition coefficient = droplet intensity / bulk buffer intensity
- C_sat directly measurable from bulk fluorescence outside droplets
- Droplet size distributions follow polymer-physics scaling laws
- Coarsening / coalescence kinetics are much cleaner than in cells
- Contact angle (BF) characterises surface wetting behaviour
- Dilution-series experiments yield phase diagram tie-lines

All spatial, dynamic, morphological, and biophysical analyses from the
cellular toolkit apply identically after segmentation — they operate on
masks and centroids without caring about modality or biological context.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2025
"""

from __future__ import annotations

import warnings
import numpy as np
import pandas as pd

# Notifications via the shim: keeps this module importable with no GUI stack (1.5.378).
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.notify import show_info as napari_show_info

# These report an INTENSITY RATIO, so they need the detector's zero point. Measured with a
# TRUE Kp of 30: on min-max normalised data the reported value swung from 323 to 22 with the
# noise level alone; on a top-hat + LoG image it came out NEGATIVE (-11.96).
from pycat.utils.intensity_semantics import IntensitySemantics, require_intensity
from pycat.utils.general_utils import debug_log
import skimage as sk
from scipy import ndimage, optimize, stats
from typing import Optional


# ---------------------------------------------------------------------------
# 1. Per-field summary (replaces per-cell summary for in vitro)
# ---------------------------------------------------------------------------

@require_intensity(IntensitySemantics.ABSOLUTE, 'field summary')
def field_summary(
    labeled_droplets: np.ndarray,
    image: np.ndarray,
    microns_per_pixel: float,
    field_area_um2: Optional[float] = None,
) -> dict:
    """
    Compute whole-field summary statistics for an in vitro droplet image.

    Parameters
    ----------
    labeled_droplets : (H, W) integer label mask (0 = background / buffer)
    image : (H, W) fluorescence or OD image in **RAW COUNTS**.

        **Not normalised.** This function reports intensity statistics, and min-max
        normalisation maps the image minimum to zero — which silently subtracts an
        uncontrolled floor (the darkest noise pixel in that field) and makes every ratio
        a function of the exposure. The docstring previously said "in [0, 1]", and the
        in-vitro widget duly fed it normalised data; the reported partition coefficient
        then swung from 323 to 22 with the noise level alone, against a true value of 30.
    microns_per_pixel : µm per pixel
    field_area_um2 : total imaged area in µm².  If None, computed from mask shape.

    Returns
    -------
    dict with keys:
        n_droplets                : number of detected droplets
        projected_area_fraction   : total droplet AREA / field AREA. This is a 2-D
                                    projected area fraction, **not a volume
                                    fraction** — see the note below.
        volume_fraction           : DEPRECATED alias of projected_area_fraction,
                                    kept so existing scripts and saved tables do not
                                    break. Do not use in new code; it is misnamed.
        mean_radius_um            : mean droplet radius (from area)
        median_radius_um
        std_radius_um
        number_density_per_um2    : droplets per µm²
        mean_droplet_intensity    : mean image value inside droplets
        dilute_phase_intensity    : mean image value OUTSIDE droplets. This is a
                                    fluorescence intensity, **not a concentration**
                                    — see the note below.
        bulk_intensity            : DEPRECATED alias of dilute_phase_intensity.
        intensity_ratio           : mean_droplet_intensity / dilute_phase_intensity. This
                                    is NOT a partition coefficient — no camera floor is
                                    removed, so it is biased toward 1 (a true Kp of 30
                                    reads as 5.8 on a 500-count pedestal). Use
                                    `partition_coefficient_local` for a real Kp.
        dense_dilute_contrast     : I_dense − I_dilute. Exact against the PEDESTAL (it
                                    cancels in the difference), but NOT immune to the
                                    droplet's PSF halo, which corrupts both terms — a 5 px
                                    edge costs it 22 %. See the note on the return value.
        partition_coefficient     : DEPRECATED alias of intensity_ratio.
                                    An apparent, intensity-based partition
                                    coefficient (dimensionless ratio of signals), not
                                    a thermodynamic one.
        total_droplet_area_um2
        field_area_um2

    Notes on what these quantities are, and are not
    -----------------------------------------------
    **The area fraction is not a volume fraction.** ``total_area / field_area`` is the
    fraction of a 2-D *projection* that is occupied by droplets. It equals the bulk
    volume fraction only under restrictive assumptions (an isotropic random section
    through a statistically homogeneous 3-D material, or a genuinely quasi-2-D
    chamber whose depth is small compared with the droplets). In a typical flow cell
    neither holds: droplets settle, so a plane near the coverslip over-represents
    them and a plane in the bulk under-represents them; and large droplets are more
    likely to intersect any given plane than small ones, biasing the in-plane size
    distribution toward large objects. Reporting this number as "volume fraction"
    invites it to be read as a physical volumetric quantity that it is not. Use the
    **Z-Stack (3-D) Object Analysis** workflow when a true volume fraction is needed.

    **The dilute-phase intensity is not C_sat.** It is a mean fluorescence (or optical
    density) value. Converting it to a saturation concentration requires a calibration
    curve relating intensity to concentration for *that* fluorophore, on *that*
    instrument, with *that* illumination — plus the assumption that the probe reports
    linearly over the range in question. Without that calibration it is a *proxy*: it
    is monotonic with concentration and therefore useful for comparison, but it has no
    units and should not be reported as a concentration.

    The same distinction applies to ``partition_coefficient``: it is a ratio of
    measured intensities. It equals the thermodynamic partition coefficient only if
    the intensity-to-concentration relationship is linear and identical in both
    phases — which is not guaranteed (quenching, inner-filter effects, and
    environment-sensitive quantum yield all break it).
    """
    H, W = labeled_droplets.shape
    if field_area_um2 is None:
        field_area_um2 = H * W * microns_per_pixel**2

    props      = sk.measure.regionprops(labeled_droplets)
    n          = len(props)
    bg_mask    = labeled_droplets == 0
    cond_mask  = labeled_droplets > 0

    if n == 0:
        _empty_bulk = float(image.mean())
        return dict(n_droplets=0,
                    projected_area_fraction=0.0,
                    volume_fraction=0.0,          # deprecated alias
                    mean_radius_um=0.0,
                    median_radius_um=0.0, std_radius_um=0.0,
                    number_density_per_um2=0.0,
                    mean_droplet_intensity=np.nan,
                    dilute_phase_intensity=_empty_bulk,
                    bulk_intensity=_empty_bulk,   # deprecated alias
                    partition_coefficient=np.nan,
                    total_droplet_area_um2=0.0, field_area_um2=field_area_um2)

    areas_um2 = np.array([p.area * microns_per_pixel**2 for p in props])
    radii_um  = np.sqrt(areas_um2 / np.pi)
    total_area = float(areas_um2.sum())

    bulk_int  = float(image[bg_mask].mean())   if bg_mask.sum()  > 0 else np.nan
    cond_int  = float(image[cond_mask].mean()) if cond_mask.sum() > 0 else np.nan
    part      = (cond_int / max(bulk_int, 1e-9)) if (bulk_int and bulk_int > 0) else np.nan

    _area_frac = total_area / field_area_um2

    return dict(
        n_droplets=n,
        # Honest name first; the old key is kept as a deprecated alias so existing
        # scripts, saved CSVs and downstream code keep working.
        projected_area_fraction=_area_frac,
        volume_fraction=_area_frac,               # DEPRECATED: misnamed, see docstring
        mean_radius_um=float(radii_um.mean()),
        median_radius_um=float(np.median(radii_um)),
        std_radius_um=float(radii_um.std()),
        number_density_per_um2=n / field_area_um2,
        mean_droplet_intensity=cond_int,
        dilute_phase_intensity=bulk_int,
        bulk_intensity=bulk_int,                  # DEPRECATED alias

        # ── This is an INTENSITY RATIO, not a partition coefficient ────────────
        #
        # Kp = (I_dense - floor) / (I_dilute - floor). This is I_dense / I_dilute, with no
        # camera floor removed — so it is dragged toward 1 by the pedestal. Measured with a
        # TRUE Kp of 30 and a 500-count pedestal, it returns **5.83**.
        #
        # And if the caller feeds a MIN-MAX NORMALISED image (which the in-vitro widget did
        # until 1.5.424, and which this function's own docstring still invited by saying
        # "image in [0, 1]"), it is worse than biased — it becomes a function of the noise,
        # because normalisation maps the image MINIMUM to zero and the minimum is a noise
        # excursion below the dilute phase:
        #
        #     noise sd    reported "partition"   (true Kp = 30)
        #        2               323.5
        #        5               130.0
        #       15                44.0
        #       30                22.5
        #
        # A 14x swing driven entirely by the exposure. It is not a measurement of anything.
        #
        # The name is therefore changed to say what it is. `partition_coefficient` is kept
        # as a DEPRECATED alias so existing callers do not break, but it carries the same
        # caveat. For a real Kp, use `partition_coefficient_local` with a dark reference
        # (in vitro) or a cell mask (cellular) — see 1.5.423.
        intensity_ratio=part,
        partition_coefficient=part,               # DEPRECATED: this is NOT Kp — see above
        # ── The contrast is PEDESTAL-exact, NOT halo-immune ─────────────────────
        #
        # I claimed in 1.5.426 that this is "exact — the pedestal cancels in the difference".
        # The first half is right and the second is a blanket reassurance that does not hold:
        # the pedestal does cancel, but the contrast is NOT immune to a bad dilute reference.
        #
        # A droplet edge is not sharp, and the PSF halo corrupts BOTH terms — the dense mean
        # is pulled DOWN by edge pixels inside the mask, and the dilute mean is pulled UP by
        # halo pixels outside it. Measured, TRUE contrast = 2900:
        #
        #     droplet edge    contrast    error
        #     sharp           2898        -0 %
        #     1 px            2773        -4 %
        #     2.5 px          2560        **-12 %**
        #     5 px            2269        **-22 %**
        #
        # So: exact against the PEDESTAL, and degraded by the HALO like everything else.
        # Stating "exact" without that qualifier is the kind of true-but-incomplete
        # reassurance that 1.5.459 was about.
        dense_dilute_contrast=float(cond_int - bulk_int),

        total_droplet_area_um2=total_area,
        field_area_um2=field_area_um2,
    )


# ---------------------------------------------------------------------------
# 2. Size distribution analysis
# ---------------------------------------------------------------------------

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
    from scipy import optimize, stats as _st

    r = np.asarray(radii_um, dtype=float)
    r = r[np.isfinite(r) & (r > 0)]
    n = len(r)
    if n < 20:
        return dict(best_model='insufficient_data', n=n, distinguishable=False,
                    verdict=(f"Only {n} objects. Distribution identification needs "
                             f"substantially more (hundreds); any 'best model' here "
                             f"would be noise."))

    if candidates is None:
        candidates = ['lognormal', 'gamma', 'weibull', 'exponential', 'powerlaw']

    models = {}

    def _add(name, loglik, k, params):
        if loglik is None or not np.isfinite(loglik):
            return
        models[name] = dict(loglik=float(loglik), k=int(k),
                            aic=float(2 * k - 2 * loglik), params=params)

    # ---- lognormal (MLE is closed-form on log r) ----
    if 'lognormal' in candidates:
        lr = np.log(r)
        mu, sig = float(lr.mean()), float(lr.std(ddof=0))
        if sig > 0:
            ll = float(np.sum(_st.lognorm.logpdf(r, s=sig, scale=np.exp(mu))))
            _add('lognormal', ll, 2, dict(mu=mu, sigma=sig))

    # ---- gamma / weibull / exponential (scipy MLE) ----
    for nm, dist in (('gamma', _st.gamma), ('weibull', _st.weibull_min),
                     ('exponential', _st.expon)):
        if nm not in candidates:
            continue
        try:
            if nm == 'exponential':
                p = dist.fit(r, floc=0)
                k = 1
            else:
                p = dist.fit(r, floc=0)
                k = 2
            ll = float(np.sum(dist.logpdf(r, *p)))
            _add(nm, ll, k, dict(params=[float(x) for x in p]))
        except Exception:
            pass

    # ---- power law: Clauset x_min by KS minimisation, exponent by MLE ----
    pl_xmin = np.nan
    if 'powerlaw' in candidates:
        best = (np.inf, None, None)
        cand_xmins = np.unique(np.percentile(r, np.linspace(0, 90, 25)))
        for xm in cand_xmins:
            tail = r[r >= xm]
            if len(tail) < 10 or xm <= 0:
                continue
            # MLE exponent for a continuous power law above xm
            alpha = 1.0 + len(tail) / np.sum(np.log(tail / xm))
            if not np.isfinite(alpha) or alpha <= 1:
                continue
            # KS distance between empirical and theoretical CDF on the tail
            ts = np.sort(tail)
            cdf_emp = np.arange(1, len(ts) + 1) / len(ts)
            cdf_the = 1.0 - (ts / xm) ** (1.0 - alpha)
            ks = float(np.max(np.abs(cdf_emp - cdf_the)))
            if ks < best[0]:
                best = (ks, xm, alpha)
        if best[1] is not None:
            pl_xmin, alpha = float(best[1]), float(best[2])
            tail = r[r >= pl_xmin]
            # NOTE: the power-law likelihood is computed only on the TAIL, so its AIC
            # is NOT directly comparable with whole-sample models. We therefore also
            # evaluate the alternatives on the same tail for the comparison below.
            ll_tail = float(len(tail) * np.log((alpha - 1) / pl_xmin)
                            - alpha * np.sum(np.log(tail / pl_xmin)))
            _add('powerlaw', ll_tail, 2,
                 dict(alpha=alpha, xmin=pl_xmin, n_tail=int(len(tail))))
            # Re-fit the whole-sample models on the tail so the comparison is fair.
            models['powerlaw']['_tail_only'] = True

    if not models:
        return dict(best_model='fit_failed', n=n, distinguishable=False,
                    verdict="No candidate distribution could be fitted.")

    # ---- whole-sample ranking (AIC) ----
    whole = {k: v for k, v in models.items() if not v.get('_tail_only')}
    ranked = sorted(whole.items(), key=lambda kv: kv[1]['aic'])
    best_name, best_m = ranked[0]

    # ---- power-law comparison, done properly (Clauset) ----
    # A power law is defined only above x_min, so its likelihood lives on the TAIL.
    # Comparing that against a whole-sample likelihood is invalid — it is not the same
    # data. The correct test re-fits the alternative on the SAME tail and compares
    # like with like. Without this, the power law can never win the ranking, which is
    # exactly the bias we set out to remove.
    powerlaw_verdict = None
    if 'powerlaw' in models and np.isfinite(pl_xmin):
        tail = r[r >= pl_xmin]
        if len(tail) >= 20:
            pm = models['powerlaw']
            ll_pl = pm['loglik']
            # Best non-power-law alternative, re-fitted on the tail.
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
                # Vuong test on the tail.
                lp_pl = (np.log((pm['params']['alpha'] - 1) / pl_xmin)
                         - pm['params']['alpha'] * np.log(tail / pl_xmin))
                d = dict(lognormal=_st.lognorm, gamma=_st.gamma,
                         weibull=_st.weibull_min, exponential=_st.expon)[alt_name]
                lp_alt = d.logpdf(tail, *alt_best)
                diff = lp_pl - lp_alt
                sd = float(np.std(diff, ddof=1))

                # ---- ABSOLUTE goodness-of-fit gate (Clauset) ----
                # A likelihood-ratio test only says which model is BETTER, not whether
                # either is ADEQUATE. Because x_min is chosen to flatter the power law,
                # the upper tail of almost ANY distribution can look locally
                # power-law-like — so a bare LR test declares "power law" for lognormal,
                # gamma and exponential data alike (observed directly while building
                # this). The power law must therefore also PASS an absolute KS test
                # against its own fitted form before it is allowed to win.
                alpha_hat = pm['params']['alpha']
                ts = np.sort(tail)
                cdf_emp = np.arange(1, len(ts) + 1) / len(ts)
                cdf_the = 1.0 - (ts / pl_xmin) ** (1.0 - alpha_hat)
                ks_obs = float(np.max(np.abs(cdf_emp - cdf_the)))
                # Parametric bootstrap: how often does data GENERATED by this power law
                # produce a KS distance as bad as the observed one?
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

    # DELIBERATELY: the power law does NOT compete for `best_model`.
    #
    # It is fitted only above x_min, and x_min is CHOSEN to flatter it. Comparing a
    # tail-only likelihood against whole-sample likelihoods is not a like-for-like
    # test, and letting it into the ranking makes it win everything: while building
    # this, a version that allowed it to compete reported "power law" for data drawn
    # from lognormal, gamma AND exponential distributions — because the upper tail of
    # almost any distribution is locally power-law-like over a limited range. Adding a
    # KS goodness-of-fit gate did not fix it (those tails genuinely pass, p ≈ 0.6–0.8).
    #
    # So `best_model` ranks only the models fitted on the WHOLE sample, and the
    # power-law question is reported separately, in `powerlaw_test`, as the narrower
    # claim it actually is: "above x_min, is the tail better described by a power law
    # than by the best alternative *on that same tail*?" That is a real and useful
    # question. It is not the same question as "what distribution are my droplet sizes
    # drawn from?", and conflating the two is how spurious power laws get published.

    distinguishable = True
    comparison = {}
    if len(ranked) > 1:
        second_name, second_m = ranked[1]
        # Vuong-style test on the per-point log-likelihood differences.
        # (Recomputing per-point loglik for the two winners.)
        def _pointwise(nm, m):
            if nm == 'lognormal':
                return _st.lognorm.logpdf(r, s=m['params']['sigma'],
                                          scale=np.exp(m['params']['mu']))
            d = dict(gamma=_st.gamma, weibull=_st.weibull_min,
                     exponential=_st.expon).get(nm)
            if d is None:
                return None
            return d.logpdf(r, *m['params']['params'])

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

    aic_gap = (ranked[1][1]['aic'] - ranked[0][1]['aic']) if len(ranked) > 1 else np.inf
    if distinguishable:
        verdict = (f"'{best_name}' is the best-supported model "
                   f"(ΔAIC = {aic_gap:.1f} over '{comparison.get('vs','—')}', "
                   f"p = {comparison.get('p_value', float('nan')):.3f}).")
    else:
        verdict = (f"'{best_name}' fits best, but it is NOT significantly better than "
                   f"'{comparison.get('vs','the runner-up')}' "
                   f"(p = {comparison.get('p_value', float('nan')):.2f}). These data "
                   f"cannot distinguish the two — do not report a preferred model as "
                   f"established. Collect more objects, or report the fitted "
                   f"parameters descriptively.")

    # The power-law claim is reported separately and scoped to its tail.
    if powerlaw_verdict:
        pv = powerlaw_verdict
        if pv['favoured'] == 'power law':
            verdict += (f" Separately, ABOVE x_min = {pv['xmin']:.3g} "
                        f"(n = {pv['n_tail']}) the tail is better described by a power "
                        f"law than by '{pv['tested_against']}' (p = "
                        f"{pv['p_value']:.3f}). This is a claim about the TAIL ONLY — "
                        f"it does not mean the size distribution is a power law, and "
                        f"the upper tail of many distributions is locally "
                        f"power-law-like.")
        elif pv['favoured'] == 'indistinguishable':
            verdict += (f" Above x_min = {pv['xmin']:.3g} the tail cannot be "
                        f"distinguished from '{pv['tested_against']}' "
                        f"(p = {pv['p_value']:.2f}) — no power-law claim is supported.")

    return dict(
        best_model=best_name,
        distinguishable=distinguishable,
        models=models,
        comparison=comparison,
        powerlaw_test=powerlaw_verdict,
        powerlaw_xmin=pl_xmin,
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


# ---------------------------------------------------------------------------
# 3. Coarsening statistics (per-frame)
# ---------------------------------------------------------------------------

def coarsening_statistics(
    mask_stack: np.ndarray,
    microns_per_pixel: float,
    frame_interval_s: float = 1.0,
) -> pd.DataFrame:
    """
    Compute per-frame coarsening statistics for an in vitro droplet time-series.

    Returns
    -------
    DataFrame with columns: frame, time_s, n_droplets, mean_radius_um,
        median_radius_um, volume_fraction, number_density, polydispersity,
        total_area_um2
    """
    rows = []
    n_frames = mask_stack.shape[0] if mask_stack.ndim == 3 else 1

    for t in range(n_frames):
        frame = mask_stack[t] if mask_stack.ndim == 3 else mask_stack
        labeled = sk.measure.label(frame > 0) if frame.max() <= 1 else frame

        props = sk.measure.regionprops(labeled)
        areas = np.array([p.area * microns_per_pixel**2 for p in props])
        radii = np.sqrt(areas / np.pi) if len(areas) > 0 else np.array([0.0])

        H, W = frame.shape
        field_area = H * W * microns_per_pixel**2
        total_area = float(areas.sum()) if len(areas) > 0 else 0.0

        rows.append({
            'frame':             t,
            'time_s':            t * frame_interval_s,
            'n_droplets':        len(props),
            'mean_radius_um':    float(radii.mean()) if len(radii) > 0 else 0.0,
            'median_radius_um':  float(np.median(radii)) if len(radii) > 0 else 0.0,
            # This is a 2-D PROJECTED AREA fraction, not a volume fraction. It equals
            # a volume fraction only for an isotropic random section through a
            # statistically homogeneous 3-D material, or a genuinely quasi-2-D
            # chamber. In a flow cell droplets settle and large droplets are more
            # likely to intersect any given plane, so neither holds. The old
            # 'volume_fraction' key is retained as a deprecated alias so existing
            # scripts and saved tables keep working.
            'projected_area_fraction': total_area / field_area,
            'volume_fraction':   total_area / field_area,   # DEPRECATED: misnamed
            'number_density':    len(props) / field_area,
            'polydispersity':    float(radii.std() / max(radii.mean(), 1e-9)) if len(radii) > 1 else 0.0,
            'total_area_um2':    total_area,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 4. Critical concentration (C_sat) estimation from dilution series
# ---------------------------------------------------------------------------

def estimate_csat_lever_rule(
    concentrations: np.ndarray,
    volume_fractions: np.ndarray,
) -> dict:
    """
    Estimate C_sat and C_dense from a dilution series using the lever rule.

    The lever rule for two-phase coexistence:
        Φ_condensate = (C_total − C_sat) / (C_dense − C_sat)

    At concentrations below C_sat, Φ = 0 (no condensates).
    Above C_sat, Φ increases linearly with concentration.

    The x-intercept of the linear fit to Φ vs C_total gives C_sat.

    Parameters
    ----------
    concentrations   : 1D array of total protein concentrations (µM or a.u.)
    volume_fractions : 1D array of measured condensate fractions (Φ). **See the
                       warning below: if these came from a 2-D image, they are
                       projected AREA fractions, not volume fractions.**

    Returns
    -------
    dict with keys:
        C_sat, C_dense, slope, r_squared,
        fit_success, C_sat_units (unknown if not provided)

    .. warning::

       **The lever rule is a volumetric identity.** Φ in the equation above is a
       genuine volume fraction. PyCAT's 2-D workflows report a *projected area
       fraction* (see ``field_summary`` / ``coarsening_statistics``), which is not the
       same quantity: droplets settle, so the plane you imaged over- or
       under-represents them depending on its depth, and larger droplets are more
       likely to intersect any given plane.

       Feeding an area fraction into this fit therefore yields a **biased**
       ``C_sat``. The bias is systematic rather than random, so it does **not**
       average out across a dilution series.

       This is still useful as a **relative** measure — the ordering and the trend of
       C_sat across conditions imaged identically are informative, and a shifted
       phase boundary is still a shifted phase boundary. It should **not** be reported
       as an absolute saturation concentration on the strength of 2-D data alone.

       For a defensible absolute C_sat, obtain Φ from the **Z-Stack (3-D) Object
       Analysis** workflow (a real volume fraction), or from a quasi-2-D chamber whose
       depth is genuinely small compared with the droplets — and state which.

    .. note::

       ``C_sat`` and ``C_dense`` inherit the units of ``concentrations``. If those were
       *fluorescence intensities* rather than calibrated concentrations, then the
       outputs are intensity-scale proxies, not concentrations — monotonic with
       concentration and useful for comparison, but without units. Converting them
       requires a calibration curve for that fluorophore on that instrument.
    """
    c = np.asarray(concentrations, dtype=float)
    phi = np.asarray(volume_fractions, dtype=float)
    if len(c) < 3:
        return dict(fit_success=False)

    # Use only points where phi > 0 (above phase boundary)
    above = phi > 0
    if above.sum() < 2:
        return dict(fit_success=False, C_sat=float(c.max()), C_dense=np.nan)

    slope, intercept, r, _, se = stats.linregress(c[above], phi[above])
    if abs(slope) < 1e-12:
        return dict(fit_success=False)

    C_sat  = float(-intercept / slope)           # x-intercept
    C_dense = float(C_sat + 1.0 / slope)         # where Φ = 1 (theoretical dense phase)
    r2     = float(r**2)

    # ── `fit_success = r2 > 0.5` does not mean C_sat is trustworthy ────────────
    #
    # C_sat is an EXTRAPOLATION to the x-intercept. R² says how well the line fits the
    # points that were KEPT; it says nothing about whether the intercept is well
    # determined. Measured against a known C_sat = 10:
    #
    #     well-sampled, high noise -> C_sat 5.59  (a 44 % error)  R² 0.913  fit_success TRUE
    #
    # But the deeper problem is not the gate — it is this ESTIMATOR. It discards every
    # point where Φ = 0 (`above = phi > 0`), and those are the most informative points
    # there are: a zero at C = 5 says "the boundary is above 5". Throwing them away and
    # extrapolating from the survivors is what produces the error above.
    #
    # `estimate_phase_boundary` (1.5.382) fits a segmented hinge over ALL the data,
    # including the zeros. On the SAME data, against a true C_sat of 10:
    #
    #     ==========================  ============  ==================
    #     data                        lever rule    phase boundary
    #     ==========================  ============  ==================
    #     well-sampled, low noise     7.78          **9.97**
    #     well-sampled, HIGH noise    5.59          **10.62**
    #     ==========================  ============  ==================
    #
    # **Use `estimate_phase_boundary`.** This function is retained for backward
    # compatibility and for comparison against historical results; `fit_success` now
    # carries a warning rather than an endorsement.
    fit_ok = bool(r2 > 0.5 and C_sat > 0)
    if fit_ok:
        napari_show_warning(
            "C_sat (lever rule): this estimator DISCARDS every point where the area "
            "fraction is zero, and those points are informative — a zero at C = 5 says "
            "the boundary is above 5. Validated against a known C_sat of 10, it returned "
            "7.78 on clean data and 5.59 on noisy data (a 44 % error, with R² = 0.91). "
            "`estimate_phase_boundary` returned 9.97 and 10.62 on the same data. Use it "
            "instead; this result is retained only for comparison with historical values.")

    return dict(
        C_sat=C_sat,
        C_dense=C_dense,
        slope=float(slope),
        intercept=float(intercept),
        r_squared=r2,
        fit_success=fit_ok,
        # R² is NOT evidence that C_sat is right — it describes the fit to the surviving
        # points, not the reliability of an extrapolated intercept.
        r_squared_is_not_accuracy=True,
        superseded_by='estimate_phase_boundary',
    )


def partition_coefficient_local(image, labeled_droplets, sample_type='cellular',
                                dark_reference=None, cell_mask=None,
                                allow_no_reference=False,
                                gap_px=None, ring_width_px=6, saturation_level=None,
                                image_layer=None):
    """Kp from a LOCAL annular dilute phase, with an optional dark reference.

    Why a local annulus, and why it needs a gap
    ------------------------------------------
    The dilute phase is what surrounds each droplet, so measure it there rather than from a
    global percentile of the whole field (which assumes uniform illumination — a vignetted
    field does not have it).

    **The ring must be OFFSET from the droplet edge.** A phase boundary is not a step: it
    has a finite interface width, and a ring drawn against the edge sits inside that
    gradient. Measured on a synthetic droplet (true dilute = 100 counts above a pedestal of
    500):

    ========  ================
    gap (px)  ring − pedestal
    ========  ================
    0         **491.8**  ← inside the interface gradient, 5x too high
    2         206.0
    5         110.7
    **10**    **100.3**  ← converged
    20        99.9
    ========  ================

    The default gap is **3 × the estimated interface width**, floored at 5 px.

    What the annulus CAN and CANNOT give you
    ----------------------------------------
    The ring reads ``pedestal + dilute`` — the camera offset is still in it. So:

    * **Without a dark reference** you can compute the **contrast** ``I_dense − I_ring``,
      which is exact (the pedestal cancels), but you **cannot** compute Kp. The raw ratio
      ``I_dense / I_ring`` is *not* Kp: on the synthetic droplet above, with a true Kp of
      30, it returns **5.81 — an 81 % error, and it looks like a plausible number.**
    * **With a dark reference** — an image of buffer with no fluorophore, acquired with the
      same camera settings — the pedestal is measured directly, and
      ``(I_dense − I_dark) / (I_ring − I_dark)`` recovers the true Kp to within a percent
      (29.77 vs 30.0).

    This is the honest resolution of a question the image alone cannot answer: **a camera
    pedestal and a genuine dilute phase are both just a floor above zero, and nothing in the
    image separates them.** A dark frame separates them, and nothing else does.

    Parameters
    ----------
    dark_reference : optional 2-D image (or a scalar) of buffer with NO fluorophore, same
        camera settings. Without it, ``partition_coefficient`` is returned as ``NaN`` and
        only ``contrast`` is reported.
    gap_px : distance from the droplet edge to the inner edge of the annulus. Default:
        3 x the estimated interface width (min 5 px).
    """
    # ── Is this image even a valid input for an INTENSITY measurement? ─────────
    #
    # Kp is a ratio of intensities, so it is only meaningful on an image whose pixel values
    # still relate to photon count. Several routine preprocessing steps destroy that — which
    # is what they are FOR — and nothing previously stopped their output being fed here.
    # Measured on a droplet field with a TRUE Kp of 30:
    #
    #     raw counts            ratio    5.83
    #     min-max normalised    ratio  130.01   (and it swings with the noise)
    #     after white top-hat   ratio  199.27   (background REMOVED -> dilute ~ 0)
    #     after top-hat + LoG   ratio  -11.96   (LoG is SIGNED -> a NEGATIVE Kp)
    #     after CLAHE           ratio   64.77   (measures the algorithm, not the sample)
    #
    # The information needed to catch this is in the PROVENANCE, not the pixels: a
    # background-subtracted image looks like an image with a dark background. So the
    # operations record what they did, and the measurement checks. Pass `image_layer` (the
    # napari layer) to enable the check; without it the measurement proceeds unchecked.
    if image_layer is not None:
        try:
            from pycat.utils.intensity_semantics import (IntensitySemantics,
                                                         check_measurement_input)
            _ok, _why = check_measurement_input(
                image_layer, IntensitySemantics.ABSOLUTE, 'the partition coefficient')
            if not _ok:
                napari_show_warning("Partition coefficient: " + _why)
                return dict(partition_coefficient=np.nan, contrast=np.nan,
                            is_true_kp=False, floor_source='none',
                            per_droplet_df=pd.DataFrame(), verdict=_why)
            elif _why:
                napari_show_warning("Partition coefficient: " + _why)
        except Exception as _exc:
            debug_log("intensity-semantics check unavailable", _exc)

    img = np.asarray(image, dtype=float)
    lab = np.asarray(labeled_droplets)
    ids = np.unique(lab)
    ids = ids[ids != 0]
    if ids.size == 0:
        return dict(partition_coefficient=np.nan, per_droplet_df=pd.DataFrame(),
                    verdict='No droplets labelled.')

    # Saturation ceiling (same logic as partition_coefficient_field).
    sat = saturation_level
    if sat is None:
        if np.issubdtype(np.asarray(image).dtype, np.integer):
            sat = float(np.iinfo(np.asarray(image).dtype).max)
        else:
            mx = float(np.nanmax(img)) if img.size else 1.0
            sat = 1.0 if mx <= 1.0 + 1e-6 else mx

    from scipy import ndimage as _ndi

    # ── The camera floor: what is POSSIBLE depends on the sample ───────────────
    #
    # Kp = (I_dense - floor) / (I_dilute - floor). The floor is the camera pedestal (plus
    # any medium autofluorescence). Leave it in and Kp is dragged toward 1: on a synthetic
    # droplet with a TRUE Kp of 30, a pedestal of 500 counts left in place gives 5.81 -- an
    # 81% error that looks like a plausible number.
    #
    # **IN VITRO THE FLOOR CANNOT BE AUTO-DETECTED. NOT BY ANY METHOD.**
    #
    # Droplets sit in bulk buffer. Every pixel is (pedestal + dilute) or (pedestal + dense).
    # No region of the image contains the pedestal ALONE, so there is nothing to measure it
    # against: the camera floor and the dilute phase are **inseparable in principle**, not
    # merely hard to separate.
    #
    # An earlier version tried Otsu anyway. It duly split dilute-from-dense and returned
    # the DILUTE PHASE (600.9 counts) as the "camera floor", giving Kp = 5.77 against a
    # true 30 -- and flagged it `is_true_kp=True`. A separation test did not save it,
    # because dense/dilute is itself a 5x ratio: **Otsu cannot tell "background vs cell"
    # from "dilute vs dense". Both are bimodal.** A heuristic cannot recover information the
    # image does not contain, and the failure was silent and confident.
    #
    # So the tool is TOLD which case it is in, and refuses rather than guesses:
    #
    #   sample_type='in_vitro'  -> a dark_reference is REQUIRED. Buffer with no fluorophore,
    #                              same camera settings. Nothing else will do.
    #   sample_type='cellular'  -> the EXTRACELLULAR region is a genuine dark reference
    #                              (no fluorophore outside the cell), so the floor CAN be
    #                              measured from the image -- but from a CELL MASK, which
    #                              says where "outside" is. Not from a threshold.
    floor_source = 'none'
    I_dark = np.nan
    _stype = str(sample_type).lower()
    if _stype not in ('cellular', 'in_vitro'):
        raise ValueError(
            "sample_type must be 'cellular' or 'in_vitro'. The camera floor can be measured "
            "from the image in CELLS (the extracellular region contains no fluorophore) but "
            "NOT IN VITRO (every pixel contains the dilute phase, so the pedestal cannot be "
            "isolated by any method). The tool must be told which case it is in rather than "
            "guess -- guessing produced a 5x error, confidently reported.")

    if dark_reference is not None:
        if np.isscalar(dark_reference):
            I_dark = float(dark_reference)
        else:
            _d = np.asarray(dark_reference, dtype=float)
            I_dark = float(np.median(_d[np.isfinite(_d)])) if _d.size else np.nan
        floor_source = 'dark_reference'

    elif _stype == 'cellular' and cell_mask is not None:
        # Outside every cell there is no fluorophore, so that region IS the floor.
        #
        # Use the MEDIAN, not the mean: the mean is dragged upward by cell-edge pixels.
        # Measured against a true pedestal of 500 -- mean 548.2 (+48.2), median 504.0 (+4.0).
        # Same principle as the annulus gap: stay away from the interface.
        _cm = np.asarray(cell_mask)
        outside = (~_cm) if _cm.dtype == bool else (_cm == 0)
        if outside.sum() > 50:
            I_dark = float(np.median(img[outside]))
            floor_source = 'extracellular'


    rows = []
    _mask_cv = []          # per-droplet CV: an over-inclusive mask has a HIGH CV
    for lb in ids:
        obj = (lab == lb)
        if not obj.any():
            continue

        # Interface width, estimated from the intensity gradient at the boundary. A wider
        # interface needs a larger gap.
        dist_out = _ndi.distance_transform_edt(~obj)
        if gap_px is None:
            # crude but robust: the distance over which the intensity falls from the
            # dense level toward the surroundings
            shell = (dist_out > 0) & (dist_out <= 12)
            if shell.any():
                prof = [float(img[(dist_out > d - 1) & (dist_out <= d)].mean())
                        for d in range(1, 13)
                        if ((dist_out > d - 1) & (dist_out <= d)).any()]
                prof = np.asarray(prof)
                if prof.size > 3:
                    lo, hi = prof.min(), prof.max()
                    thr = lo + 0.1 * (hi - lo)
                    below = np.flatnonzero(prof <= thr)
                    iface = float(below[0] + 1) if below.size else 3.0
                else:
                    iface = 3.0
            else:
                iface = 3.0
            _gap = max(5.0, 3.0 * iface)
        else:
            _gap = float(gap_px)

        ring = (dist_out > _gap) & (dist_out <= _gap + float(ring_width_px))
        ring &= (lab == 0)                       # never sample another droplet
        if not ring.any():
            continue

        dense_px = img[obj]

        # ── An OVER-INCLUSIVE droplet mask silently collapses Kp ────────────────
        #
        # Kp = I_dense / I_dilute. If the mask spills past the droplet edge it pulls
        # DILUTE-phase pixels into the "dense" average, so I_dense falls — and Kp falls with
        # it. Measured, on a scene with a TRUE Kp of 30 (true droplet radius 13 px):
        #
        #     mask radius    Kp reported    CV inside the mask
        #     13 px (true)   **29.61**      0.016
        #     20 px           19.93         0.421
        #     30 px            9.46         0.807
        #     50 px          **4.41**       0.902
        #
        # **A 7x collapse** — and the function was reporting "Kp is pedestal-independent,
        # validated" the whole way down. The message was reassuring while the number was
        # wrong.
        #
        # It is DETECTABLE from the data alone, with no ground truth: a clean dense mask has
        # a LOW coefficient of variation, because every pixel in it is dense phase. An
        # over-inclusive mask mixes in dilute pixels, and the CV rises — 0.016 to 0.807, a
        # 50-fold separation, monotonic in the error.
        _cv = (float(np.std(dense_px) / max(abs(np.mean(dense_px)), 1e-9))
               if dense_px.size > 1 else 0.0)
        _mask_cv.append(_cv)
        ring_px = img[ring]
        I_dense = float(dense_px.mean())
        I_ring = float(ring_px.mean())

        tol = 1e-6 * max(abs(sat), 1.0)
        frac_sat = float((dense_px >= sat - tol).mean())
        saturated = bool(frac_sat > 0.001)

        contrast = I_dense - I_ring              # the pedestal CANCELS here
        raw_ratio = (I_dense / I_ring) if I_ring > 0 else np.nan
        if saturated:
            kp = np.nan
        elif np.isfinite(I_dark) and (I_ring - I_dark) > 0:
            kp = (I_dense - I_dark) / (I_ring - I_dark)
        elif allow_no_reference:
            # The caller has explicitly accepted an uncorrected ratio. It is NOT Kp — it is
            # biased toward 1 by the pedestal, and the bias cannot be recovered from the
            # image. Returned so the analysis can proceed, labelled so it cannot be
            # mistaken for a partition coefficient.
            kp = raw_ratio
        else:
            kp = np.nan                          # no reference -> Kp is not computable

        rows.append(dict(
            droplet_label=int(lb),
            I_dense=I_dense, I_dilute_local=I_ring, I_dark=I_dark,
            gap_px=_gap,
            contrast=contrast,
            partition_coefficient=kp,
            raw_ratio=raw_ratio,
            saturated=saturated, saturated_fraction=frac_sat,
        ))

    # A high CV inside the dense mask means the mask is including dilute-phase pixels.
    if _mask_cv:
        _cv_med = float(np.median(_mask_cv))
        if _cv_med > 0.25:
            napari_show_warning(
                f"Partition coefficient: the droplet mask looks OVER-INCLUSIVE — the "
                f"intensity inside it has a coefficient of variation of {_cv_med:.2f}, and a "
                f"clean dense-phase mask has a CV near 0.02 (every pixel is dense phase).\n\n"
                f"A mask that spills past the droplet edge pulls DILUTE-phase pixels into the "
                f"dense average, so I_dense falls and **Kp falls with it**. Measured on a "
                f"scene with a TRUE Kp of 30: a mask 1.5x too large gives Kp = 19.9 "
                f"(CV 0.42), and one 2.3x too large gives **Kp = 9.5** (CV 0.81) — **a 3x "
                f"collapse, reported with no indication that anything is wrong.**\n\n"
                f"Tighten the segmentation until the mask contains the dense phase and not "
                f"its surroundings.")

    df = pd.DataFrame(rows)
    kp_vals = df['partition_coefficient'].to_numpy(dtype=float) if len(df) else np.array([])
    kp_vals = kp_vals[np.isfinite(kp_vals)]
    kp_mean = float(kp_vals.mean()) if kp_vals.size else np.nan

    if floor_source == 'dark_reference' and not np.isfinite(kp_mean):
        # ── Do not print a VALIDATION claim next to a NaN ────────────────────────
        #
        # The dark-reference verdict said "Kp = nan. ... Validated: 29.65 recovered against a
        # true 30.0" — a reassurance printed beside a refusal. The 1.5.462 scoping guard did
        # not catch it, because the claim is correctly scoped ("against the PEDESTAL") and the
        # problem is different: **the number it is describing does not exist.**
        #
        # A validation claim attached to a NaN is worse than an unscoped one. It tells the
        # user the machinery is sound at the exact moment the machinery has refused to answer,
        # and invites them to go looking for the number somewhere else.
        verdict = (
            f"Kp is NOT COMPUTABLE for this image — see the warning above. "
            f"{int(df['saturated'].sum()) if len(df) else 0} of {len(df)} droplet(s) are "
            f"saturated at the detector ceiling.\n\n"
            f"The camera floor WAS measured correctly from the dark reference "
            f"({I_dark:.1f} counts), and that part of the measurement is sound. **It does not "
            f"help**: a clipped dense phase truncates the numerator by an unknown amount, so "
            f"the ratio is meaningless rather than a lower bound. Re-acquire with a shorter "
            f"exposure or lower gain.")
        napari_show_warning("Partition coefficient: " + verdict)

    elif floor_source == 'dark_reference':
        verdict = (f"Kp = {kp_mean:.2f}. The camera floor was measured directly from the "
                   f"DARK REFERENCE ({I_dark:.1f} counts) and removed from both phases, so "
                   f"Kp is pedestal-independent. Validated: 29.65 recovered against a true "
                   f"30.0 at pedestals of 0, 100, 500 and 2000 counts.")
        # ── Do NOT sound confident when the MASK is suspect ──────────────────────
        #
        # This message says the number is validated — and it is, against the PEDESTAL. It
        # says nothing about the mask, and an over-inclusive mask collapses Kp by 7x while
        # this reassurance prints unchanged. A confident verdict alongside a warning is worse
        # than no verdict: the user reads the one that agrees with them.
        if _mask_cv and float(np.median(_mask_cv)) > 0.25:
            verdict += (" **But see the mask warning above — the pedestal correction is "
                        "sound and the MASK is not, and Kp is only as good as the worse of "
                        "the two.**")
        else:
            napari_show_info("Partition coefficient: " + verdict)

    elif floor_source == 'extracellular':
        verdict = (f"Kp = {kp_mean:.2f}. The camera floor ({I_dark:.1f} counts) came from "
                   f"the EXTRACELLULAR REGION. In cells this is a legitimate dark "
                   f"reference: there is no fluorophore outside the cell, so that region "
                   f"contains the camera pedestal (and any medium autofluorescence — a real "
                   f"floor you also want removed). The MEDIAN of the outside region is used, "
                   f"not the mean: the mean is dragged upward by cell-edge pixels (measured, "
                   f"against a true pedestal of 500 — mean 548.2, median 504.0). A dedicated "
                   f"dark frame remains the more direct measurement.")
        napari_show_info("Partition coefficient: " + verdict)

    elif _stype == 'in_vitro':
        # In vitro there is NOTHING to fall back on. Say so plainly.
        verdict = (
            "IN VITRO WITHOUT A DARK REFERENCE — Kp is not computable, and no heuristic can "
            "rescue it.\n\n"
            "Droplets sit in bulk buffer: every pixel contains the dilute phase, so no "
            "region of the image holds the camera pedestal alone. The floor and the dilute "
            "phase are INSEPARABLE IN PRINCIPLE, not merely hard to separate. (An automatic "
            "threshold was tried and it returned the DILUTE PHASE as the 'camera floor', "
            "giving Kp = 5.77 against a true 30 — confidently, and silently.)\n\n"
            "THE FIX: acquire a DARK REFERENCE — buffer with no fluorophore, same camera "
            "settings — and pass it as `dark_reference`. It takes one extra frame.\n\n"
            f"What IS reported meanwhile: the CONTRAST "
            f"(I_dense − I_dilute = {float(df['contrast'].mean()) if len(df) else float('nan'):.0f}), "
            "which is exact against the PEDESTAL (it cancels in the difference) but NOT "
            "immune to the droplet's PSF halo — a 5 px edge costs it 22%. Pass "
            "`allow_no_reference=True` to additionally receive the raw intensity ratio, "
            "which is NOT Kp and is biased toward 1 by an unknowable amount.")
        napari_show_warning("Partition coefficient: " + verdict)

    elif allow_no_reference:
        verdict = (f"**NOT a partition coefficient — a raw intensity ratio ({kp_mean:.2f}), "
                   f"biased toward 1.** No camera floor was available. The annulus measures "
                   f"(camera pedestal + dilute phase), and nothing in the image separates "
                   f"them, so the bias is UNKNOWABLE from this image: with a true Kp of 30 "
                   f"and a 500-count pedestal, the raw ratio returns 5.81. Use it for "
                   f"RELATIVE comparison between images acquired identically; do not report "
                   f"it as Kp. The CONTRAST is exact regardless.")
        napari_show_warning("Partition coefficient: " + verdict)

    else:
        verdict = (
            "NO CAMERA FLOOR — Kp is not computable.\n\n"
            "For CELLULAR data, pass `cell_mask=<mask>`: the extracellular region contains "
            "no fluorophore and is a genuine dark reference.\n"
            "For IN VITRO data, pass `dark_reference=<image>` (buffer, no fluorophore, same "
            "camera settings) — nothing else can work, because every pixel contains the "
            "dilute phase.\n"
            "Or `allow_no_reference=True` to receive the raw ratio, clearly labelled as NOT "
            "Kp.\n\n"
            "The CONTRAST (I_dense − I_dilute) is reported regardless and is exact: the "
            "pedestal cancels in the difference — though not immune to the PSF halo, "
            "which costs a 5 px edge 22%.")
        napari_show_warning("Partition coefficient: " + verdict)

    return dict(
        # NaN unless a floor was available, or the caller explicitly accepted a raw ratio.
        partition_coefficient=kp_mean,
        # TRUE only when a camera floor was actually measured. When False, the value above
        # is a raw ratio biased toward 1 — not a partition coefficient.
        is_true_kp=bool(floor_source != 'none'),
        floor_source=floor_source,
        contrast=float(df['contrast'].mean()) if len(df) else np.nan,
        has_dark_reference=bool(dark_reference is not None),
        camera_floor=I_dark,
        n_saturated_droplets=int(df['saturated'].sum()) if len(df) else 0,
        per_droplet_df=df,
        verdict=verdict,
    )


def partition_measurement(image, labeled_droplets, percentile_bulk=10.0,
                          saturation_level=None, background_subtracted=None,
                          dark_reference=None):
    """The partition coefficient as a ``Measurement`` — with its assumptions CHECKED.

    ``partition_coefficient_field`` returns a number. This returns the number **with the
    conditions under which it means anything**, each one *computed from the data* rather
    than asserted:

    * **neither phase saturated** — checked. A clipped dense phase does not give a lower
      bound on Kp; the numerator has been truncated by an unknown amount, so the ratio is
      **meaningless**, not conservative. Validated (1.5.392): with a bulk of 100 on a 16-bit
      sensor, a true Kp of 655, 1500 and 4000 **all read as 655** once the dense phase
      clips.
    * **background subtracted** — checked against the image's own floor. A partition
      coefficient is a ratio of two intensities, and an unsubtracted camera pedestal
      appears in *both*, dragging the ratio toward 1. A Kp of 4 on a pedestal of 500 counts
      reads as ~1.1. This is the same failure as the transfection filter (1.5.415) and the
      puncta SNR gate (1.5.416).
    * **dilute phase measured locally** — flagged. Estimating the dilute phase from a global
      percentile of the whole field assumes the background is uniform; on a vignetted or
      unevenly illuminated field it is not.

    An assumption that fails marks the measurement ``NOT_INTERPRETABLE``. That is the point:
    a Kp computed on a saturated image should not be usable as a number at all.
    """
    from pycat.utils.measurement import (Measurement, Parameter, Assumption,
                                         ParameterSource, ValidationLevel,
                                         Interpretability)

    res = partition_coefficient_field(image, labeled_droplets,
                                      percentile_bulk=percentile_bulk,
                                      saturation_level=saturation_level)

    img = np.asarray(image)
    sat = bool(res.get('saturated', False))
    frac_sat = float(res.get('saturated_fraction', 0.0))
    n_sat_drops = int(res.get('n_saturated_droplets', 0))

    # ── Is the background subtracted? Compute it; do not ask. ──────────────────
    #
    # If a camera pedestal is still present, the image's low percentile sits well above
    # zero. A properly background-subtracted image has its floor at (or near) zero.
    dense = float(res.get('c_dense_proxy', np.nan))
    dilute = float(res.get('c_sat_proxy', np.nan))
    finite = img[np.isfinite(img)]
    floor = float(np.percentile(finite, 1)) if finite.size else 0.0

    # ── The image CANNOT tell you whether its floor is a pedestal or the dilute phase ──
    #
    # This was attempted twice and it does not work, for a reason worth stating: in a
    # partition measurement the **dilute phase IS signal** — it is the denominator — so it
    # is not a background to be removed, and a low-Kp system legitimately has a dilute
    # level close to the dense one. A camera pedestal and a genuine dilute phase produce
    # the same thing: a floor above zero.
    #
    # Both heuristics failed, in both directions:
    #
    #   * floor vs the dense/dilute SPAN  -> flagged EVERY low-Kp image (Kp = 3, Kp = 10)
    #     as having an unsubtracted background, with no pedestal present at all.
    #   * floor vs the dense-phase CONTRAST -> still false-alarmed at Kp = 3, and PASSED a
    #     pedestal of 500 counts that had already dragged Kp from 30 to 5.8.
    #
    # There is no signature to find, because there is no information in the image that
    # separates the two. So **ask** instead of guessing: the caller knows whether they
    # subtracted the background, and if they do not say, the assumption is recorded as
    # UNCHECKED rather than silently assumed to hold.
    #
    # The consequence is worth being blunt about, because it is large and invisible: an
    # unsubtracted pedestal appears in BOTH the numerator and the denominator of Kp and
    # drags it toward 1. Measured on identical droplets (true Kp = 30):
    #
    #     pedestal    0 -> Kp 30.0
    #     pedestal  100 -> Kp 15.5
    #     pedestal  500 -> Kp  5.8
    #     pedestal 2000 -> Kp  2.4
    #
    # **A 12x error that looks like a perfectly plausible number.**
    if dark_reference is not None:
        # A dark reference RESOLVES this: buffer with no fluorophore measures the camera
        # floor directly, so the pedestal can be removed from BOTH phases. This is the
        # only thing that can separate a camera offset from a genuine dilute phase — the
        # image alone cannot, because both are simply a floor above zero.
        bg_checked, bg_holds = True, True
        _dk = (float(dark_reference) if np.isscalar(dark_reference)
               else float(np.median(np.asarray(dark_reference, dtype=float))))
        # ── State the SCOPE inside the claim, not beside it ─────────────────────
        #
        # The old text said "Kp is pedestal-independent. Validated: 29.65 recovered against
        # a true 30.0" — and stopped there. Every word is true, and the scope is the
        # PEDESTAL and nothing else. It says nothing about the mask, and an over-inclusive
        # mask collapses Kp by 7x (1.5.459) while this reassurance prints unchanged.
        #
        # `partition_coefficient_local` now suppresses its version of this message when the
        # mask looks bad — **but this copy, in `partition_measurement`, was never touched.**
        # The correction did not reach it, which is exactly how a true-but-unscoped claim
        # survives: it gets fixed where you are looking and lives on where you are not.
        #
        # So the scope is now IN the sentence. A reader cannot take the reassurance without
        # also reading what it does not cover.
        bg_detail = (f'RESOLVED by a dark reference (camera floor = {_dk:.1f} counts). '
                     f'The pedestal is measured directly and removed from both phases, so '
                     f'Kp is pedestal-independent — validated against the PEDESTAL '
                     f'specifically: 29.65 recovered against a true 30.0 at pedestals of 0, '
                     f'100, 500 and 2000 counts.\n\n'
                     f'**That is the only thing it is validated against.** It says nothing '
                     f'about the segmentation: an over-inclusive droplet mask pulls '
                     f'dilute-phase pixels into the dense average and collapses Kp by up to '
                     f'7x, with the pedestal correction still perfectly sound. Use '
                     f'partition_coefficient_local(), which checks the mask as well.')
    elif background_subtracted is None:
        bg_checked, bg_holds = False, None
        bg_detail = (f'NOT CHECKED — the caller did not say. The image floor (1st '
                     f'percentile) is {floor:.1f} and the dilute phase is {dilute:.1f}, '
                     f'and NOTHING in the image can distinguish a camera pedestal from a '
                     f'genuine dilute phase: both are simply a floor above zero. If the '
                     f'background was not subtracted, Kp is compressed toward 1 — on '
                     f'identical droplets with a true Kp of 30, a pedestal of 500 counts '
                     f'gives Kp = 5.8.\n\nTHE FIX: acquire a DARK REFERENCE — buffer with '
                     f'no fluorophore, same camera settings — and pass it as '
                     f'dark_reference. That measures the camera floor directly and is the '
                     f'only thing that CAN separate it from the dilute phase. In cells, '
                     f'partition_coefficient_local() additionally samples the dilute phase '
                     f'from a LOCAL ANNULUS around each droplet (offset from the edge to '
                     f'clear the interface gradient), which is more defensible than a '
                     f'global percentile on an unevenly illuminated field.')
    else:
        bg_checked, bg_holds = True, bool(background_subtracted)
        bg_detail = ('the caller states the background was subtracted'
                     if background_subtracted else
                     f'the caller states the background was NOT subtracted. Kp is '
                     f'compressed toward 1 by the pedestal — it appears in both the '
                     f'numerator and the denominator. This value is not interpretable.')

    assumptions = [
        Assumption(
            name='no_saturation',
            description=('neither phase is clipped at the detector ceiling — a truncated '
                         'numerator makes Kp meaningless, not a lower bound'),
            checked=True,
            holds=not sat,
            detail=(f'{frac_sat:.1%} of dense-phase pixels at the ceiling; '
                    f'{n_sat_drops} droplet(s) affected'
                    if sat else 'no clipping detected in the dense phase'),
        ),
        Assumption(
            name='background_subtracted',
            description=('the camera offset has been removed — an unsubtracted pedestal '
                         'appears in BOTH the numerator and the denominator and drags the '
                         'ratio toward 1'),
            checked=bg_checked,
            holds=bg_holds,
            detail=bg_detail,
        ),
        Assumption(
            name='dilute_phase_measured_locally',
            description=('the dilute phase is representative — a global percentile assumes '
                         'a uniform background, which a vignetted or unevenly illuminated '
                         'field does not have'),
            checked=False,
            holds=None,
            detail=(f'the dilute phase was taken as the {percentile_bulk:.0f}th percentile '
                    f'of the whole field. Check the vignetting QC; if illumination is not '
                    f'flat, this over- or under-states the dilute phase depending on where '
                    f'the droplets sit.'),
        ),
    ]

    parameters = [
        Parameter(name='percentile_bulk', value=float(percentile_bulk), units='%',
                  source=ParameterSource.ASSUMED,
                  note='the percentile of the field taken as the dilute phase'),
        Parameter(name='saturation_level',
                  value=float(res.get('saturation_level', np.nan)), units='counts',
                  source=(ParameterSource.MANUFACTURER if saturation_level is not None
                          else ParameterSource.METADATA),
                  note=('supplied by the caller' if saturation_level is not None
                        else 'inferred from the image dtype')),
    ]

    m = Measurement(
        name='partition coefficient',
        value=float(res.get('partition_coeff', np.nan)),
        units='dimensionless',
        parameters=parameters,
        assumptions=assumptions,
        validation=ValidationLevel.SIMULATION_VALIDATED,
        notes=('Kp = I_dense / I_dilute. It is a ratio of intensities, so it inherits '
               'every offset and every clipping event in either phase.'),
    )
    return m


def estimate_phase_boundary(concentrations, fractions, n_boot=400,
                            random_state=0):
    """Locate the phase boundary from a dilution series, USING the zero-fraction
    samples and reporting an uncertainty interval.

    Why this exists (``estimate_csat_lever_rule`` does neither)
    -----------------------------------------------------------
    1. **The zeros are thrown away.** The old fit does ``above = phi > 0`` and
       regresses only those points. But a sample at C = 5 with Φ = 0 is a *direct
       constraint on the quantity being estimated*: it says "the boundary is above
       5". These are **censored observations**, not missing data, and they are the
       most informative points in the series for locating the boundary. Discarding
       them and extrapolating an x-intercept from the points far above the boundary
       is the least stable way to find it.

    2. **No uncertainty is reported.** The extrapolated x-intercept is very
       sensitive to the slope. Measured on a synthetic series with a known boundary
       at 10 and only σ = 0.004 noise on Φ, the recovered value ranged over
       **[8.9, 11.0]** across bootstrap replicates — and the old code returns a
       single number with no interval. Worse, a series with only two points just
       above the boundary returned **C_sat = −6.9**: a negative saturation
       concentration, which is not a physical quantity.

    Method
    ------
    * **Segmented (hinge) fit.** Model Φ(C) = max(0, s·(C − C_b)) directly, over ALL
      the data including the zeros. The hinge location C_b *is* the boundary, and
      it is fitted rather than extrapolated to.
    * **Bootstrap interval.** Resample the series and refit, returning a percentile
      interval for the boundary.
    * The zeros constrain the hinge from below; the positive points constrain the
      slope. Both are used.

    Naming
    ------
    The returned quantity is called ``boundary_concentration`` — the **lever-rule
    apparent boundary** — not ``C_sat``. Calling it a saturation concentration
    asserts (a) that Φ is a true volume fraction and (b) that the intensity axis is
    a calibrated concentration. When Φ came from a 2-D image it is a *projected area
    fraction* (see ``field_summary``), so the boundary is biased; and if
    ``concentrations`` were fluorescence intensities rather than calibrated
    concentrations, the boundary carries those units. It is a robust **relative**
    measure — the shift of the boundary between conditions imaged identically is
    real and useful — but it is not an absolute C_sat without volumetric and
    concentration calibration.

    Returns
    -------
    dict with:
      boundary_concentration      : the fitted hinge location (the apparent boundary)
      boundary_ci                 : (lo, hi) bootstrap 95 % interval
      slope                       : dΦ/dC above the boundary
      dense_axis_intercept        : where the fitted line reaches Φ = 1. Reported as a
                                    LINE INTERCEPT, not as ``C_dense``: it is an
                                    extrapolation far outside the data and is a
                                    physical concentration only under the same
                                    assumptions as above.
      n_below, n_above            : how many samples were below / above the boundary
      fit_success, warnings       : diagnostics
    """
    from scipy import optimize as _opt

    c = np.asarray(concentrations, dtype=float)
    phi = np.asarray(fractions, dtype=float)
    ok = np.isfinite(c) & np.isfinite(phi)
    c, phi = c[ok], phi[ok]
    warnings_ = []

    if len(c) < 3:
        return dict(fit_success=False, warnings=["Need at least 3 samples."])

    def _hinge(params, cc):
        cb, s = params
        return np.maximum(0.0, s * (cc - cb))

    def _resid(params, cc, pp):
        return _hinge(params, cc) - pp

    def _fit(cc, pp):
        pos = pp > 0
        if pos.sum() >= 2:
            # Slope seed by least squares. Guarded: a bootstrap resample can draw
            # duplicate x values, which makes polyfit ill-conditioned and noisy.
            cx, cy = cc[pos], pp[pos]
            if np.ptp(cx) > 1e-12:
                s0 = float(np.cov(cx, cy, bias=True)[0, 1] / max(np.var(cx), 1e-12))
            else:
                s0 = 1e-3
            s0 = s0 if s0 > 1e-9 else 1e-3
            cb0 = float(np.min(cx))
        else:
            s0, cb0 = 1e-3, float(np.median(cc))
        try:
            r = _opt.least_squares(
                _resid, x0=[cb0, s0], args=(cc, pp),
                bounds=([-np.inf, 1e-12], [np.inf, np.inf]), max_nfev=5000)
            return float(r.x[0]), float(r.x[1])
        except Exception:
            return np.nan, np.nan

    cb, s = _fit(c, phi)
    if not np.isfinite(cb) or not np.isfinite(s) or s <= 0:
        return dict(fit_success=False,
                    warnings=["The segmented fit did not converge."])

    n_below = int((phi <= 0).sum())
    n_above = int((phi > 0).sum())
    if n_above < 2:
        warnings_.append(
            "Fewer than 2 samples above the boundary: the slope, and therefore the "
            "boundary, is essentially unconstrained.")
    if n_below == 0:
        warnings_.append(
            "No samples below the boundary. The boundary is being EXTRAPOLATED from "
            "points above it, which is the least stable way to locate it. Include "
            "concentrations that produce no condensates \u2014 a zero is a real "
            "measurement that constrains the boundary from below.")

    # Bootstrap interval.
    rng = np.random.default_rng(random_state)
    boots = []
    n = len(c)
    for _ in range(int(n_boot)):
        idx = rng.integers(0, n, n)
        b_cb, b_s = _fit(c[idx], phi[idx])
        if np.isfinite(b_cb) and np.isfinite(b_s) and b_s > 0:
            boots.append(b_cb)
    if len(boots) >= 20:
        lo, hi = (float(np.percentile(boots, 2.5)),
                  float(np.percentile(boots, 97.5)))
    else:
        lo = hi = float('nan')
        warnings_.append("Bootstrap did not converge often enough for an interval.")

    if cb < 0:
        warnings_.append(
            "The fitted boundary is NEGATIVE, which is not a physical concentration. "
            "The series does not constrain it \u2014 do not report this value.")

    dense_ic = float(cb + 1.0 / s) if s > 0 else float('nan')

    return dict(
        boundary_concentration=float(cb),
        boundary_ci=(lo, hi),
        slope=float(s),
        dense_axis_intercept=dense_ic,
        n_below=n_below, n_above=n_above,
        n_boot_ok=len(boots),
        warnings=warnings_,
        fit_success=bool(np.isfinite(cb) and cb > 0 and n_above >= 2),
    )



# ---------------------------------------------------------------------------
# 5. Partition coefficient without cell mask
# ---------------------------------------------------------------------------

@require_intensity(IntensitySemantics.ABSOLUTE, 'the partition coefficient')
def partition_coefficient_field(
    image: np.ndarray,
    labeled_droplets: np.ndarray,
    percentile_bulk: float = 10.0,
    saturation_level: float = None,
) -> dict:
    """
    Compute the fluorescence partition coefficient for in vitro droplets.

    For in vitro data, the bulk (dilute phase) intensity is estimated
    from the background pixels. Using a low percentile (default 10th)
    avoids contamination from dim droplets just below the segmentation
    threshold.

    Parameters
    ----------
    image           : (H, W) float32 in [0, 1]
    labeled_droplets: (H, W) integer label mask
    percentile_bulk : percentile of background pixels to use as bulk estimate

    Returns
    -------
    dict with keys:
        c_sat_proxy       : bulk (dilute phase) intensity
        c_dense_proxy     : mean droplet interior intensity
        partition_coeff   : C_dense / C_sat
        enrichment        : (C_dense − C_sat) / C_sat
        per_droplet_df    : DataFrame with per-droplet partition coefficient
    """
    bg_mask   = labeled_droplets == 0
    cond_mask = labeled_droplets > 0

    # Bulk (dilute-phase) intensity estimate. The 10th percentile of background
    # can collapse to ~0 on dark fluorescence backgrounds (many near-zero
    # pixels), which then made per-droplet partition = intensity / ~0 explode to
    # ~1e8. Use a ROBUST bulk: the percentile, but floored to the background MEAN
    # if the percentile is degenerate (<=0 or a tiny fraction of the mean). This
    # keeps the per-droplet partition on the same scale as the field-level one.
    if bg_mask.sum() > 0:
        bg_vals   = image[bg_mask]
        bulk_pct  = float(np.percentile(bg_vals, percentile_bulk))
        bulk_mean = float(bg_vals.mean())
        # If the percentile is ~0 (dark background) fall back to the mean, which
        # is what the field-level summary uses.
        if bulk_pct <= 0 or (bulk_mean > 0 and bulk_pct < 0.05 * bulk_mean):
            bulk = bulk_mean
        else:
            bulk = bulk_pct
    else:
        bulk = 0.0
    # Final safety floor: never divide by (near-)zero.
    bulk_div = bulk if bulk > 1e-6 else (float(image.mean()) if image.mean() > 1e-6 else 1.0)

    dense  = float(image[cond_mask].mean()) if cond_mask.sum() > 0 else np.nan

    # ── Detector saturation INVALIDATES the partition coefficient ────────────
    #
    # If the dense phase clips at the detector maximum, the numerator of Kp has been
    # TRUNCATED BY AN UNKNOWN AMOUNT. The measured Kp then pins at the clip ceiling: with
    # a bulk of 100 and a 16-bit sensor, a true Kp of 655, 1500 or 4000 ALL read as 655.
    #
    # The tempting move is to call it a lower bound. It is not: you cannot say how far the
    # true value lies above the measured one, because you do not know how much signal the
    # detector threw away. Reporting a number invites exactly that misreading -- a Kp of
    # 655 looks like a measurement, not a floor. So the coefficient is marked INVALID and
    # the reason is returned with it.
    #
    # The saturation ceiling is inferred from the dtype where possible (a uint16 image
    # clips at 65535, a float image normalised to [0,1] clips at 1.0). Callers with a
    # known full-well capacity can override it.
    sat_level = saturation_level
    if sat_level is None:
        if np.issubdtype(image.dtype, np.integer):
            sat_level = float(np.iinfo(image.dtype).max)
        else:
            mx = float(np.nanmax(image)) if image.size else 1.0
            sat_level = 1.0 if mx <= 1.0 + 1e-6 else mx

    tol = 1e-6 * max(abs(sat_level), 1.0)
    dense_px = image[cond_mask] if cond_mask.sum() > 0 else np.array([])
    n_sat = int((dense_px >= sat_level - tol).sum()) if dense_px.size else 0
    frac_sat = (n_sat / dense_px.size) if dense_px.size else 0.0
    # Even a small clipped fraction biases the mean downward; but a handful of hot pixels
    # should not condemn an otherwise fine measurement. 0.1% is the point at which the
    # truncation is no longer negligible against the other uncertainties here.
    saturated = bool(frac_sat > 0.001)

    part   = dense / bulk_div
    enrich = (dense - bulk) / bulk_div
    if saturated:
        part = np.nan
        enrich = np.nan

    rows = []
    for prop in sk.measure.regionprops(labeled_droplets, intensity_image=image):
        vals = prop.image_intensity[prop.image] if hasattr(prop, 'image_intensity') \
               else np.array([])
        d_nsat = int((vals >= sat_level - tol).sum()) if vals.size else 0
        d_frac = (d_nsat / vals.size) if vals.size else 0.0
        d_sat = bool(d_frac > 0.001)
        rows.append({
            'droplet_label':      prop.label,
            'mean_intensity':     float(prop.intensity_mean),
            # A saturated droplet's Kp is NOT a lower bound -- it is meaningless. NaN,
            # with the reason in the neighbouring columns.
            'partition_coeff':    (np.nan if d_sat
                                   else float(prop.intensity_mean / bulk_div)),
            'saturated':          d_sat,
            'saturated_fraction': float(d_frac),
            'area_um2':           np.nan,  # caller can fill from microns_per_pixel
        })

    n_sat_droplets = int(sum(r['saturated'] for r in rows))
    if saturated or n_sat_droplets:
        napari_show_warning(
            f"Partition coefficient: the dense phase is SATURATED "
            f"({frac_sat:.1%} of dense-phase pixels at the detector ceiling "
            f"{sat_level:g}; {n_sat_droplets}/{len(rows)} droplets affected). "
            f"Kp is reported as NaN, not as a lower bound: the numerator has been "
            f"truncated by an unknown amount, so the true value cannot be bounded. "
            f"Re-acquire with a shorter exposure or lower gain.")

    return dict(
        c_sat_proxy=bulk,
        c_dense_proxy=dense,
        partition_coeff=part,
        enrichment=enrich,
        # Saturation diagnostics travel WITH the result, so a downstream consumer cannot
        # use the number without seeing why it is (or is not) trustworthy.
        saturated=saturated,
        saturated_fraction=float(frac_sat),
        saturation_level=float(sat_level),
        n_saturated_droplets=n_sat_droplets,
        per_droplet_df=pd.DataFrame(rows),
    )


# ---------------------------------------------------------------------------
# 6. Contact angle estimation (brightfield in vitro)
# ---------------------------------------------------------------------------

def estimate_contact_angle(
    image: np.ndarray,
    droplet_mask: np.ndarray,
    droplet_label: int = 0,
) -> dict:
    """
    Estimate the contact angle of a sessile droplet on a coverslip from
    its brightfield silhouette.

    Method:
      1. Extract the droplet boundary from the labeled mask
      2. Find the base chord (widest horizontal extent — where droplet
         contacts the glass, typically at the bottom of the image)
      3. Fit a circle to the upper arc of the boundary
      4. Compute the contact angle from the circle radius and base half-width:
         θ = arcsin(a/R) where a = base half-width, R = fitted circle radius
         θ < 90° = partial wetting (hydrophilic),  θ > 90° = hydrophobic

    Parameters
    ----------
    image        : (H, W) float32 brightfield image
    droplet_mask : (H, W) binary or labeled mask; if labeled, provide label
    droplet_label: if droplet_mask is labeled, which label to use (0 = any)

    Returns
    -------
    dict with keys:
        contact_angle_deg   : estimated contact angle in degrees
        circle_radius_px    : fitted circle radius in pixels
        base_width_px       : droplet base chord width in pixels
        fit_success         : bool
        boundary_y, boundary_x : droplet boundary coordinates
    """
    from scipy.optimize import least_squares

    if droplet_label > 0:
        mask = (droplet_mask == droplet_label).astype(bool)
    else:
        mask = (droplet_mask > 0).astype(bool)

    if not mask.any():
        return dict(fit_success=False)

    # Extract boundary
    boundary = sk.segmentation.find_boundaries(mask, mode='inner')
    y_b, x_b = np.where(boundary)

    if len(y_b) < 10:
        return dict(fit_success=False)

    # Base: row with maximum width (widest horizontal span = contact line)
    rows_present = np.unique(y_b)
    widths = {r: x_b[y_b == r].max() - x_b[y_b == r].min() for r in rows_present}
    base_row   = max(widths, key=widths.get)
    base_width = widths[base_row]
    base_x_mid = float(x_b[y_b == base_row].mean())

    # Use upper arc (above base) for circle fit
    upper = y_b < base_row
    if upper.sum() < 5:
        return dict(fit_success=False)
    y_u, x_u = y_b[upper].astype(float), x_b[upper].astype(float)

    # Algebraic circle fit (Kasa method — fast and stable)
    def _circle_residuals(p, x, y):
        cx, cy, r = p
        return np.sqrt((x-cx)**2 + (y-cy)**2) - r

    cx0 = float(x_u.mean())
    cy0 = float(y_u.mean())
    r0  = float(base_width / 2)

    try:
        res = least_squares(_circle_residuals, [cx0, cy0, r0],
                             args=(x_u, y_u), method='lm')
        cx, cy, R = res.x
        if R < 1 or R > max(image.shape):
            return dict(fit_success=False)

        # Contact angle
        a   = base_width / 2
        sin_theta = min(1.0, a / max(R, 1e-6))
        theta = float(np.degrees(np.arcsin(sin_theta)))

        return dict(
            contact_angle_deg=theta,
            circle_radius_px=float(R),
            base_width_px=float(base_width),
            circle_centre_x=float(cx),
            circle_centre_y=float(cy),
            fit_success=True,
            boundary_y=y_b,
            boundary_x=x_b,
        )
    except Exception:
        return dict(fit_success=False)


# ---------------------------------------------------------------------------
# 7. Automatic fusion event detection and relaxation fitting
# ---------------------------------------------------------------------------

def detect_and_fit_fusions(
    mask_stack: np.ndarray,
    tracks_df: pd.DataFrame,
    image_stack: Optional[np.ndarray],
    microns_per_pixel: float,
    frame_interval_s: float = 1.0,
    match_radius_um: float = 3.0,
) -> pd.DataFrame:
    """
    Detect all merge events in an in vitro droplet stack and automatically
    fit the post-fusion aspect ratio relaxation.

    τ = ηR/γ gives the ratio of viscosity to surface tension.
    In vitro droplets typically have cleaner fusion events than cellular
    condensates because there is no cytoskeletal scaffold to interfere.

    Method
    ------
    1. detect_merge_fission() finds merge events directly from the mask
       stack (frame, centroid position) — it has no knowledge of track IDs.
    2. Each merge event is matched to the trajectory in tracks_df whose
       position at the merge frame is closest to the event centroid
       (within match_radius_um) — this identifies which track represents
       the newly-merged droplet going forward.
    3. The aspect ratio time series for that track, starting from the
       merge frame, is fit to the exponential relaxation model.

    Parameters
    ----------
    mask_stack : (T, H, W) label stack
    tracks_df  : output of link_trajectories_bayesian(), must contain
        columns track_id, frame, y_um, x_um, area_um2,
        major_axis_um, minor_axis_um (major/minor axis require the
        caller to have merged in shape data — see note below).
    image_stack: (T, H, W) optional fluorescence stack (currently unused;
        reserved for future intensity-based relaxation fitting)
    microns_per_pixel : µm per pixel
    frame_interval_s  : seconds per frame
    match_radius_um   : max distance between merge event centroid and a
        track's position at that frame to consider it a match

    Returns
    -------
    DataFrame with one row per detected fusion event:
        frame_event, track_merged, radius_um_post,
        tau_s, AR_0, r_squared, fit_success

    Notes
    -----
    tracks_df from extract_frame_properties() + link_trajectories_bayesian()
    contains major_axis_um/minor_axis_um only if extract_frame_properties()
    was called with a version that includes shape properties (current
    implementation does). If these columns are absent, aspect ratio
    defaults to 1.0 for all frames and the fit will not be meaningful —
    check for their presence before relying on fusion relaxation results.
    """
    from pycat.toolbox.dynamic_spatial_tools import detect_merge_fission
    from pycat.toolbox.condensate_physics_tools import fit_aspect_ratio_relaxation

    if mask_stack.ndim != 3:
        return pd.DataFrame()

    merge_df = detect_merge_fission(mask_stack, microns_per_pixel)
    if merge_df is None or merge_df.empty:
        return pd.DataFrame()

    merge_events = merge_df[merge_df['event_type'] == 'merge']
    if merge_events.empty:
        return pd.DataFrame()

    has_shape_cols = ('major_axis_um' in tracks_df.columns and
                       'minor_axis_um' in tracks_df.columns)

    rows = []
    for _, event in merge_events.iterrows():
        t0 = int(event['frame'])
        ey, ex = float(event['centroid_y_um']), float(event['centroid_x_um'])

        # Match merge event to the nearest track at this frame
        frame_tracks = tracks_df[tracks_df['frame'] == t0]
        if frame_tracks.empty:
            continue
        dists = np.sqrt((frame_tracks['y_um'] - ey)**2 +
                         (frame_tracks['x_um'] - ex)**2)
        if dists.min() > match_radius_um:
            continue
        t_merged = int(frame_tracks.loc[dists.idxmin(), 'track_id'])

        # Extract aspect ratio time series after the merge
        track_data = tracks_df[tracks_df['track_id'] == t_merged].sort_values('frame')
        post = track_data[track_data['frame'] >= t0]
        if len(post) < 4:
            continue

        if has_shape_cols:
            ar = (post['major_axis_um'] / post['minor_axis_um'].replace(0, np.nan)).values
            ar = np.nan_to_num(ar, nan=1.0)
        else:
            ar = np.ones(len(post))  # no shape data — fit will report fit_success=False

        t_arr = (post['frame'].values - t0) * frame_interval_s
        fit = fit_aspect_ratio_relaxation(t_arr, ar, t0_frame=0)

        r_post = (float(np.sqrt(post.iloc[0]['area_um2'] / np.pi))
                  if 'area_um2' in post.columns else np.nan)

        rows.append({
            'frame_event':    t0,
            'track_merged':   t_merged,
            'radius_um_post': r_post,
            'tau_s':          fit.get('tau_s', np.nan),
            'AR_0':           fit.get('AR_0', np.nan),
            'r_squared':      fit.get('r_squared', np.nan),
            'fit_success':    fit.get('fit_success', False) and has_shape_cols,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 8. Sedimentation correction for time-series
# ---------------------------------------------------------------------------

def detect_sedimentation(
    field_summary_df: pd.DataFrame,
) -> dict:
    """
    Detect sedimentation of droplets toward the coverslip in a time-series.

    Sedimentation manifests as an increase in volume fraction (Φ) over time
    as droplets settle into the focal plane. This can confound coarsening
    analysis by making it look like droplet growth when it is really more
    droplets entering the focal plane.

    Distinguishes sedimentation from coarsening:
    - Sedimentation: Φ ↑ AND number density ↑ (more droplets appearing)
    - Coarsening: mean radius ↑ AND number density ↓ (Ostwald / coalescence)
    - Both can occur simultaneously

    Parameters
    ----------
    field_summary_df : output of coarsening_statistics() with
        columns time_s, volume_fraction, n_droplets, mean_radius_um

    Returns
    -------
    dict with keys:
        sedimentation_detected  : bool
        coarsening_detected     : bool
        phi_slope               : rate of Φ increase (per second)
        n_slope                 : rate of droplet count increase
        radius_slope            : rate of mean radius increase
        dominant_process        : 'sedimentation' | 'coarsening' | 'both' | 'stable'
        recommendation          : string
    """
    df = field_summary_df.dropna()
    if len(df) < 4:
        return dict(dominant_process='insufficient_data')

    t   = df['time_s'].values
    phi = df['volume_fraction'].values
    n   = df['n_droplets'].values.astype(float)
    r   = df['mean_radius_um'].values

    def _slope_r2(x, y):
        s, _, r_val, _, _ = stats.linregress(x, y)
        return float(s), float(r_val**2)

    phi_s, phi_r2 = _slope_r2(t, phi)
    n_s,   n_r2   = _slope_r2(t, n)
    r_s,   r_r2   = _slope_r2(t, r)

    # Sedimentation: phi increasing AND n increasing
    sed = phi_s > 0 and phi_r2 > 0.3 and n_s > 0
    # Coarsening: radius increasing AND n decreasing
    coarse = r_s > 0 and r_r2 > 0.3 and n_s < 0

    if sed and coarse:
        dominant = 'both'
        rec = ('Both sedimentation and coarsening detected. '
               'Sediment corrected by subtracting linear Φ trend before '
               'fitting coarsening kinetics.')
    elif sed:
        dominant = 'sedimentation'
        rec = ('Sedimentation detected — droplets settling into focal plane. '
               'Consider using a top-mounted objective or imaging a fixed time '
               'after sample loading to allow equilibration.')
    elif coarse:
        dominant = 'coarsening'
        rec = 'Coarsening (Ostwald ripening or coalescence) detected — no sedimentation artefact.'
    else:
        dominant = 'stable'
        rec = 'No significant sedimentation or coarsening detected within the time series.'

    return dict(
        sedimentation_detected=sed,
        coarsening_detected=coarse,
        phi_slope=phi_s, phi_r2=phi_r2,
        n_slope=n_s,     n_r2=n_r2,
        radius_slope=r_s, radius_r2=r_r2,
        dominant_process=dominant,
        recommendation=rec,
    )
