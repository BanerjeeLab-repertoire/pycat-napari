"""**Characterization net for `classify_beads` — pins both classification branches before the split.**

`classify_beads` was a 306-line function fusing two independent classifiers (a fast-template branch and a
Gaussian-fit branch) plus an empty guard. Splitting them into helpers is only safe if the split is
**byte-identical**: the per-bead class labels, the `n_units_est` estimates, the dropped-rejected row
count, and the recorded thresholds must all be unchanged.

The categorical `bead_class` output is the sensitive part — any drift in the threshold logic flips a
label — so it is asserted exactly; the numeric `n_units_est` is pinned tight. Values were read off the
pre-refactor implementation; they are the contract the decomposition must preserve.
"""
import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.core

_REL = 1e-6


def _fast_df():
    rng = np.random.default_rng(0)
    n = 20
    ncc = np.concatenate([[0.3, 0.4], rng.uniform(0.6, 0.99, n - 2)])       # 2 below the NCC floor
    amp = np.concatenate([rng.uniform(80, 120, n - 3), [300, 320, 40]])     # 2 bright, 1 dim
    ii = np.concatenate([rng.uniform(900, 1100, n - 3), [5000, 5200, 950]])  # 2 high-mass
    snr = np.concatenate([rng.uniform(20, 40, n - 1), [3.0]])
    return pd.DataFrame({'ncc': ncc, 'amplitude': amp,
                         'integrated_intensity': ii, 'snr': snr})


def _gauss_df():
    rng = np.random.default_rng(0)
    m = 12
    gii = np.concatenate([rng.uniform(900, 1100, m - 2), [3000, 3200]])
    gsig = np.concatenate([rng.uniform(0.9, 1.1, m - 3), [2.5, 2.6, 1.0]])
    gr2 = rng.uniform(0.3, 0.99, m)
    gamp = np.concatenate([rng.uniform(90, 110, m - 3), [200, 210, 40]])
    return pd.DataFrame({'integrated_intensity': gii, 'sigma_mean': gsig,
                         'r_squared': gr2, 'amplitude': gamp})


def test_fast_template_classification_is_unchanged():
    from pycat.toolbox.vpt_tools import classify_beads
    r = classify_beads(_fast_df())

    assert list(r['bead_class']) == [
        'singlet', 'out_of_plane', 'singlet', 'singlet', 'singlet', 'singlet', 'singlet',
        'singlet', 'singlet', 'singlet', 'singlet', 'singlet', 'singlet', 'singlet', 'singlet',
        'singlet', 'aggregate', 'singlet']
    assert len(r) == 18 and int(r['singlet'].sum()) == 16          # two rejected were DROPPED
    nu = [1.117718, 1.126958, 1.007451, 1.051777, 1.0, 1.056499, 1.003327, 1.014465, 1.117881,
          0.980358, 1.06249, 0.950672, 1.105929, 1.096483, 0.98289, 5.184711, 5.3921, 0.985095]
    assert r['n_units_est'].to_numpy() == pytest.approx(np.array(nu), rel=_REL)
    th = r.attrs['classify_thresholds']
    assert th['mode'] == 'fast_template' and th['ncc_floor'] == 0.55
    assert th['aggregate_mass_hi'] == pytest.approx(5176.2, rel=_REL)
    assert th['aggregate_amp_hi'] == pytest.approx(105.952976, rel=_REL)
    assert th['dim_amp_percentile'] == pytest.approx(25.0, rel=_REL)


def test_gaussian_fit_classification_is_unchanged():
    from pycat.toolbox.vpt_tools import classify_beads
    r = classify_beads(_gauss_df())

    assert list(r['bead_class']) == (['singlet'] * 10 + ['aggregate', 'singlet'])
    assert len(r) == 12 and int(r['singlet'].sum()) == 11
    nu = [1.046927, 0.972096, 0.925463, 0.920481, 1.082859, 1.103134, 1.040746, 1.065786,
          1.027904, 1.107683, 3.057041, 3.260844]
    assert r['n_units_est'].to_numpy() == pytest.approx(np.array(nu), rel=_REL)


def test_an_empty_frame_gets_the_columns_and_no_rows():
    from pycat.toolbox.vpt_tools import classify_beads
    r = classify_beads(pd.DataFrame())
    assert list(r.columns) == ['n_units_est', 'bead_class', 'singlet'] and len(r) == 0
