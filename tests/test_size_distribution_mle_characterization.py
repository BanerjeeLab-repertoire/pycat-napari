"""**Characterization net for `fit_size_distribution_mle` — pins the fit across its phases.**

`fit_size_distribution_mle` was a 301-line function fusing per-model MLE fitting, a Clauset power-law
tail comparison (with a seeded parametric bootstrap), a Vuong distinguishability test, and verdict
assembly. Splitting it into phase helpers is only safe if the split is **byte-identical**: the selected
model, every model's AIC/log-likelihood, the power-law x_min and its tail test, the distinguishability
comparison, and the descriptive moments must all be unchanged.

The bootstrap is seeded (`default_rng(0)`), so the whole function is deterministic and these values are a
hard contract. They were read off the pre-refactor implementation.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.base

_REL = 1e-6


def test_lognormal_sample_selects_lognormal_and_favours_the_powerlaw_tail():
    from pycat.toolbox.invitro_tools import fit_size_distribution_mle
    rng = np.random.default_rng(42)
    r = rng.lognormal(mean=0.5, sigma=0.4, size=300)
    res = fit_size_distribution_mle(r)

    assert res['best_model'] == 'lognormal' and res['distinguishable'] is True and res['n'] == 300
    assert res['mean_radius_um'] == pytest.approx(1.7423437765314687, rel=1e-9)
    assert res['median_radius_um'] == pytest.approx(1.592345486037445, rel=1e-9)
    assert res['polydispersity_index'] == pytest.approx(0.4091690230001979, rel=1e-9)
    assert res['powerlaw_xmin'] == pytest.approx(1.7289467983581726, rel=1e-9)

    aic = {'exponential': 935.138723, 'gamma': 569.791889, 'lognormal': 551.361025,
           'powerlaw': 133.963946, 'weibull': 629.090498}
    for name, a in aic.items():
        assert res['models'][name]['aic'] == pytest.approx(a, rel=_REL), name

    pt = res['powerlaw_test']
    assert pt['favoured'] == 'power law' and pt['tested_against'] == 'lognormal'
    assert pt['adequate'] is True and pt['n_tail'] == 131
    assert pt['p_value'] == pytest.approx(1.0112073467460903e-07, rel=1e-6)
    assert pt['gof_p_value'] == pytest.approx(1.0, rel=_REL)

    cmp = res['comparison']
    assert cmp['vs'] == 'gamma'
    assert cmp['loglik_ratio'] == pytest.approx(9.215432, rel=_REL)
    assert cmp['p_value'] == pytest.approx(0.001587685631742053, rel=1e-6)


def test_gamma_sample_selects_gamma_and_is_indistinguishable_from_the_runner_up():
    from pycat.toolbox.invitro_tools import fit_size_distribution_mle
    rng = np.random.default_rng(7)
    r = rng.gamma(shape=3.0, scale=1.0, size=250)
    res = fit_size_distribution_mle(r)

    assert res['best_model'] == 'gamma' and res['distinguishable'] is False and res['n'] == 250
    assert res['mean_radius_um'] == pytest.approx(2.607902899176061, rel=1e-9)
    assert res['polydispersity_index'] == pytest.approx(0.5784636593726543, rel=1e-9)
    assert res['powerlaw_xmin'] == pytest.approx(3.7247998292135915, rel=1e-9)

    aic = {'exponential': 981.273206, 'gamma': 852.250768, 'lognormal': 859.833766,
           'powerlaw': 132.224111, 'weibull': 862.943248}
    for name, a in aic.items():
        assert res['models'][name]['aic'] == pytest.approx(a, rel=_REL), name

    assert res['comparison']['vs'] == 'lognormal'
    assert res['comparison']['p_value'] == pytest.approx(0.306221, rel=1e-4)
    assert res['powerlaw_test']['n_tail'] == 53 and res['powerlaw_test']['adequate'] is True


def test_too_few_objects_refuses_to_identify_a_distribution():
    from pycat.toolbox.invitro_tools import fit_size_distribution_mle
    res = fit_size_distribution_mle(np.array([1.0, 2.0, 3.0]))
    assert res['best_model'] == 'insufficient_data' and res['distinguishable'] is False
    assert res['n'] == 3


# ── N6-4: the MLE ignores left-truncation at the detection limit (verify-then-fix) ──────────────────────
# The whole-sample fits (lognormal via closed-form log-moments; gamma/weibull/exponential via scipy .fit(floc=0))
# are UNTRUNCATED MLEs. Only the power law uses a lower cut-off (Clauset x_min). The `xmin` parameter in the
# signature is DEAD — it never reaches the whole-sample fits. So when droplets below the detection limit are
# missing (left-truncated data), the fitted median is inflated and the spread deflated, with no correction.

def _lognormal_radii(mu=None, sigma=0.5, n=3000, seed=0):
    mu = np.log(5.0) if mu is None else mu           # median 5 um
    return np.random.default_rng(seed).lognormal(mu, sigma, n)


def test_size_mle_recovers_an_untruncated_lognormal():
    """Baseline: on complete (untruncated) data the MLE recovers the true lognormal — the estimator is sound."""
    from pycat.toolbox.invitro_tools import fit_size_distribution_mle
    p = fit_size_distribution_mle(_lognormal_radii())['models']['lognormal']['params']
    assert p['mu'] == pytest.approx(np.log(5.0), abs=0.05)
    assert p['sigma'] == pytest.approx(0.5, abs=0.05)


def test_size_mle_default_fit_is_biased_by_detection_limit_truncation():
    """CHARACTERISATION (survives the fix — the DEFAULT stays untruncated): dropping droplets below a detection
    limit (r < 4 um, ~35% of the sample) inflates the fitted median well past the true 5 um and deflates sigma.
    Pins the bias so the fix can be measured against it."""
    from pycat.toolbox.invitro_tools import fit_size_distribution_mle
    full = _lognormal_radii()
    truncated = full[full >= 4.0]
    p = fit_size_distribution_mle(truncated)['models']['lognormal']['params']
    assert np.exp(p['mu']) > 5.0 * 1.2               # median inflated > 20% above the true 5 um
    assert p['sigma'] < 0.5 * 0.85                   # spread deflated below the true 0.5


def test_size_mle_recovers_truth_from_truncated_data_when_xmin_is_honoured():
    """N6-4 FIX (was the failing golden-master): given the detection limit as `xmin`, the left-truncated MLE
    recovers the TRUE lognormal (median 5 um, sigma 0.5) from detection-limited radii — where the untruncated
    default (the characterization above) inflates the median to ~6.5 um and deflates sigma."""
    from pycat.toolbox.invitro_tools import fit_size_distribution_mle
    full = _lognormal_radii()
    truncated = full[full >= 4.0]
    res = fit_size_distribution_mle(truncated, xmin=4.0)
    p = res['models']['lognormal']['params']
    assert p['mu'] == pytest.approx(np.log(5.0), abs=0.08)
    assert p['sigma'] == pytest.approx(0.5, abs=0.08)
    assert res['detection_limit_xmin'] == 4.0        # the fit records the truncation it applied
