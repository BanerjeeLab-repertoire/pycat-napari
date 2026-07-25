"""
**Costes automatic thresholding returned ~the image maximum on every real image.**

`costes_thresholding` had three compounding defects: (a) it fit the intensity line by ordinary least
squares (`curve_fit`), not the orthogonal / total-least-squares line Costes requires -- OLS is biased
low under symmetric errors-in-variables noise, biasing both thresholds; (b) it stepped the threshold
by a fixed `-0.01` capped at 50 iterations, so on any uint16 image it descended at most 0.5 intensity
units from the maximum and effectively never left it; (c) it stopped on `|r| > 0.1` instead of the
Costes criterion "descend until the Pearson r of the below-threshold population reaches 0".

These tests pin the fixed behaviour: the threshold lands near the noise/signal boundary (not ~max),
and the TLS slope is recovered under errors-in-variables noise where OLS would under-estimate it.
"""
import numpy as np
import pytest


def _mod():
    return pytest.importorskip("pycat.toolbox.coloc.thresholding")


@pytest.mark.base
def test_uint16_threshold_is_not_pinned_at_the_maximum():
    """Noise floor + a bright colocalized core -> threshold near the boundary, far below the max."""
    mod = _mod()
    rng = np.random.default_rng(0)
    size = 256
    r = rng.uniform(0, 5000, (size, size))
    g = rng.uniform(0, 5000, (size, size))
    core = np.zeros((size, size), bool)
    core[80:176, 80:176] = True
    s = rng.uniform(10000, 60000, (size, size))
    r[core] = s[core]
    g[core] = s[core] + rng.normal(0, 500, (size, size))[core]

    tr, tg = mod.costes_thresholding(r, g, None)
    assert np.isfinite(tr) and np.isfinite(tg)
    # The old code returned ~max (>=59000). The fix must land between the noise floor and the signal.
    assert tr < 0.5 * r.max(), (tr, r.max())
    assert 3000 < tr < 15000, tr


@pytest.mark.base
def test_tls_slope_recovered_under_errors_in_variables_noise():
    """green = 1.5*red with symmetric noise on BOTH axes -> TLS ~= 1.5; OLS would under-estimate."""
    mod = _mod()
    rng = np.random.default_rng(1)
    n = 20000
    red_true = rng.uniform(0, 60000, n)
    noise = rng.normal(0, 2500, n)
    R = red_true + noise                      # error on the x-axis too (errors-in-variables)
    G = 1.5 * red_true - noise * 0.3 + rng.normal(0, 2500, n)

    a_tls, _ = mod._costes_tls_line(R, G)
    assert abs(a_tls - 1.5) / 1.5 < 0.10, a_tls

    # OLS (green-on-red) is biased low here -- demonstrates why TLS is required.
    a_ols = np.polyfit(R, G, 1)[0]
    assert a_ols < a_tls


@pytest.mark.base
def test_degenerate_inputs_return_nan_not_a_fabricated_threshold():
    """Empty / all-zero / non-positively-correlated inputs -> (nan, nan), not a ~max threshold."""
    mod = _mod()
    z = np.zeros((32, 32))
    assert all(np.isnan(v) for v in mod.costes_thresholding(z, z, None))
    tiny = np.ones((2, 2))
    assert all(np.isnan(v) for v in mod.costes_thresholding(tiny, tiny, None))
