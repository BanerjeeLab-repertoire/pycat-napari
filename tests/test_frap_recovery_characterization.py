"""**Characterization net for `fit_frap_recovery` — pins the fit, mobile fraction, and identifiability.**

`fit_frap_recovery` was a 206-line function fusing the hyperbolic recovery fit, the normalisation-aware
mobile-fraction derivation (+ over-recovery warning), a fit-adequacy runs test, and a per-parameter
identifiability assessment (+ its warning). Splitting these phases into helpers is only safe if the split
is **byte-identical**: the fitted parameters, R², the mobile/immobile fractions, the over-recovery flag,
the per-parameter CI widths and identifiability verdicts, and WHICH warnings fire must all be unchanged.

Values were read off the pre-refactor implementation on synthetic FRAP curves of known length.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.base

_REL = 1e-6


@pytest.fixture
def _warns(monkeypatch):
    import pycat.toolbox.frap_tools as ft
    seen = []
    monkeypatch.setattr(ft, 'napari_show_warning', lambda m, *a, **k: seen.append(m))
    return seen


def _curve(t, a, b, tau, seed):
    from pycat.toolbox.frap_tools import frap_recovery_model
    return frap_recovery_model(t, a, b, tau) + np.random.default_rng(seed).normal(0, 0.01, len(t))


def test_an_adequate_recovery_is_fully_identifiable_and_silent(_warns):
    from pycat.toolbox.frap_tools import fit_frap_recovery
    t = np.linspace(0, 60, 40)
    r = fit_frap_recovery(t, _curve(t, 0.2, 0.9, 8.0, 0))

    assert r['a'] == pytest.approx(0.20479599, rel=_REL)
    assert r['b'] == pytest.approx(0.90256108, rel=_REL)
    assert r['tau_half'] == pytest.approx(8.26909912, rel=_REL)
    assert r['mobile_fraction'] == pytest.approx(0.87746676, rel=_REL)
    assert r['immobile_fraction'] == pytest.approx(0.12253324, rel=_REL)
    assert r['bleach_depth'] == pytest.approx(0.79520401, rel=_REL)
    assert r['r_squared'] == pytest.approx(0.99710027, rel=_REL)
    assert r['over_recovery'] is False and bool(r['identifiable']) is True
    assert r['identifiability']['tau_half']['relative_ci_width'] == pytest.approx(0.13766, rel=1e-4)
    assert not _warns, "a clean, well-constrained fit must be silent"


def test_a_short_window_is_UNIDENTIFIABLE_and_warns(_warns):
    from pycat.toolbox.frap_tools import fit_frap_recovery
    t = np.linspace(0, 4, 6)
    r = fit_frap_recovery(t, _curve(t, 0.2, 0.9, 8.0, 1))

    assert bool(r['identifiable']) is False
    assert r['identifiability']['b']['identifiable'] is False
    assert r['identifiability']['tau_half']['identifiable'] is False
    assert r['identifiability']['tau_half']['relative_ci_width'] == pytest.approx(2.572852, rel=1e-4)
    # over-recovery (b>1 from the ill-constrained fit) AND unidentifiability both warn.
    assert len(_warns) == 2
    assert any('does not DETERMINE' in m for m in _warns)


def test_over_recovery_warns_but_stays_identifiable(_warns):
    from pycat.toolbox.frap_tools import fit_frap_recovery
    t = np.linspace(0, 60, 40)
    r = fit_frap_recovery(t, _curve(t, 0.2, 1.2, 8.0, 2))

    assert r['over_recovery'] is True and bool(r['identifiable']) is True
    assert r['b'] == pytest.approx(1.19938994, rel=_REL)
    assert r['mobile_fraction'] == pytest.approx(1.24821527, rel=_REL)
    assert len(_warns) == 1 and 'exceeds the pre-bleach level' in _warns[0]


def test_too_few_points_returns_the_nan_dict(_warns):
    from pycat.toolbox.frap_tools import fit_frap_recovery
    r = fit_frap_recovery(np.array([0, 1, 2.0]), np.array([0.2, 0.5, 0.7]))
    assert np.isnan(r['a']) and np.isnan(r['mobile_fraction']) and np.isnan(r['r_squared'])
    assert not _warns
