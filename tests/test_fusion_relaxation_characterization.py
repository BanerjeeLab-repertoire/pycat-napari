"""**Characterization net for `fit_fusion_relaxation` — pins the fit, window check, and two-mode test.**

`fit_fusion_relaxation` was a 184-line function fusing the single-exponential-plus-drift fit, the tau
confidence interval, the observation-window adequacy check (a short record biases tau low, which biases
η/γ by the same factor), and a two-mode-relaxation test. Splitting these phases into helpers is only safe
if the split is **byte-identical**: the fitted parameters, R², the tau CI, the relaxations-observed count,
the adequacy/two-mode verdicts, and WHICH warnings fire must all be unchanged.

Values were read off the pre-refactor implementation on synthetic fusion traces of known length/mode.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.core

_REL = 1e-6


@pytest.fixture
def _warns(monkeypatch):
    import pycat.toolbox.fusion_tools as ft
    seen = []
    monkeypatch.setattr(ft, 'napari_show_warning', lambda m, *a, **k: seen.append(m))
    monkeypatch.setattr(ft, 'napari_show_info', lambda *a, **k: None)
    return seen


def _single(t, a, tau, b, d, seed):
    from pycat.toolbox.fusion_tools import fusion_relaxation_model
    return fusion_relaxation_model(t, a, tau, b, d) + np.random.default_rng(seed).normal(0, 0.01, len(t))


def test_an_adequate_single_mode_relaxation_fits_cleanly_and_is_silent(_warns):
    from pycat.toolbox.fusion_tools import fit_fusion_relaxation
    t = np.linspace(0, 200, 200)
    r = fit_fusion_relaxation(t, _single(t, 2.0, 20.0, 0.0, 1.0, 0))

    assert r['tau'] == pytest.approx(19.94079717, rel=_REL)
    assert r['a'] == pytest.approx(1.99436299, rel=_REL)
    assert r['d'] == pytest.approx(1.00472582, rel=_REL)
    assert r['r_squared'] == pytest.approx(0.99945538, rel=_REL)
    assert r['relaxations_observed'] == pytest.approx(10.0296893, rel=_REL)
    assert bool(r['fit_adequate']) is True and bool(r['is_two_mode']) is False
    assert r['tau_ci'][0] == pytest.approx(19.739129, rel=_REL)
    assert r['tau_ci'][1] == pytest.approx(20.142465, rel=_REL)
    assert not _warns, "a long, single-mode record must be silent"


def test_a_short_record_warns_that_tau_is_biased_low(_warns):
    from pycat.toolbox.fusion_tools import fit_fusion_relaxation
    t = np.linspace(0, 40, 80)
    r = fit_fusion_relaxation(t, _single(t, 2.0, 20.0, 0.0, 1.0, 1))

    assert r['relaxations_observed'] == pytest.approx(2.06182908, rel=_REL)
    assert r['tau'] == pytest.approx(19.40025022, rel=_REL)
    assert len(_warns) == 1 and 'relaxation time' in _warns[0]


def test_a_two_mode_relaxation_is_flagged(_warns):
    from pycat.toolbox.fusion_tools import fit_fusion_relaxation
    t = np.linspace(0, 120, 200)
    y = (1.0 * np.exp(-t / 3.0) + 1.0 * np.exp(-t / 20.0) + 2.0
         + np.random.default_rng(2).normal(0, 0.01, 200))
    r = fit_fusion_relaxation(t, y)

    assert bool(r['is_two_mode']) is True and bool(r['fit_adequate']) is False
    assert r['tau'] == pytest.approx(8.51677163, rel=_REL)   # single-exp tau lands between the two modes
    assert len(_warns) == 1


def test_too_few_points_returns_the_nan_dict(_warns):
    from pycat.toolbox.fusion_tools import fit_fusion_relaxation
    r = fit_fusion_relaxation(np.array([0, 1, 2.0]), np.array([3, 2, 1.0]))
    assert np.isnan(r['tau']) and np.isnan(r['r_squared'])
    assert not _warns
