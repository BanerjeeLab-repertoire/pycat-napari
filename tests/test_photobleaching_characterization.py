"""**Characterization net for `fit_photobleaching` — pins the fit and the two-tier window warning.**

`fit_photobleaching` was a 233-line function (mostly measured-rationale comment blocks) fusing the
exponential-decay fit, the tau confidence interval, the non-circular observation-window adequacy metric,
and its two-tier warning. Splitting the rationale blocks into phase helpers is only safe if the split is
**byte-identical**: the fitted parameters, R², the tau CI, both decay-observed bounds, the correction
factors, and WHICH warning tier fires must all be unchanged.

Values were read off the pre-refactor implementation on synthetic bleaching movies of known length.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.core

_REL = 1e-6


def _synth(tau, n, dt, I0=1000.0, I_inf=200.0, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(n) * dt
    return I0 * np.exp(-t / tau) + I_inf + rng.normal(0, 5, n)


@pytest.fixture
def _warns(monkeypatch):
    # fit_photobleaching moved to condensate_physics/photobleaching.py (1.6.218); patch the warning at its
    # new home (condensate_physics_tools re-exports the function). Assertions unchanged — the window warning
    # still fires; only the monkeypatch target follows the moved symbol.
    import pycat.toolbox.condensate_physics.photobleaching as cpt
    seen = []
    monkeypatch.setattr(cpt, 'napari_show_warning', lambda m, *a, **k: seen.append(m))
    monkeypatch.setattr(cpt, 'napari_show_info', lambda *a, **k: None)
    return seen


def test_an_adequate_movie_fits_and_does_not_warn(_warns):
    from pycat.toolbox.condensate_physics_tools import fit_photobleaching
    r = fit_photobleaching(_synth(50, 100, 1.0), frame_interval_s=1.0)

    assert bool(r['fit_success']) is True
    assert r['I0'] == pytest.approx(1000.70421375, rel=_REL)
    assert r['tau_bleach_s'] == pytest.approx(50.41953387, rel=_REL)
    assert r['I_inf'] == pytest.approx(197.62524176, rel=_REL)
    assert r['r_squared'] == pytest.approx(0.99961736, rel=_REL)
    assert r['observation_window_in_taus'] == pytest.approx(1.14906768, rel=_REL)
    assert r['decay_observed_no_floor'] == pytest.approx(1.14906768, rel=_REL)
    assert r['decay_observed_floor_subtracted'] == pytest.approx(1.7746187, rel=_REL)
    assert r['tau_ci'][0] == pytest.approx(49.582034, rel=_REL)
    assert r['tau_ci'][1] == pytest.approx(51.257033, rel=_REL)
    assert float(r['correction_factors'][-1]) == pytest.approx(3.544442, rel=1e-5)
    assert len(r['correction_factors']) == 100
    assert not _warns, "an adequate (>0.8 tau) movie must not warn"


def test_a_mid_window_movie_fires_the_MILD_warning(_warns):
    from pycat.toolbox.condensate_physics_tools import fit_photobleaching
    r = fit_photobleaching(_synth(50, 100, 0.5), frame_interval_s=0.5)

    assert r['observation_window_in_taus'] == pytest.approx(0.66989976, rel=_REL)
    assert r['tau_bleach_s'] == pytest.approx(51.43714166, rel=_REL)
    assert len(_warns) == 1 and 'cannot' not in _warns[0], "0.5–0.8 tau is the MILD tier, not severe"


def test_a_short_movie_fires_the_SEVERE_warning(_warns):
    from pycat.toolbox.condensate_physics_tools import fit_photobleaching
    r = fit_photobleaching(_synth(50, 100, 0.1), frame_interval_s=0.1)

    assert r['observation_window_in_taus'] == pytest.approx(0.14884472, rel=_REL)
    assert r['tau_bleach_s'] == pytest.approx(61.33148261, rel=_REL)
    assert r['decay_observed_no_floor'] == pytest.approx(0.14884472, rel=_REL)
    assert len(_warns) == 1 and 'cannot measure a decay constant' in _warns[0].lower()


def test_a_flat_signal_reports_fit_failure_without_crashing(_warns):
    """A constant signal fits but with a negative R² — fit_success is False, and the full result dict is
    still returned (this does NOT take the except path)."""
    from pycat.toolbox.condensate_physics_tools import fit_photobleaching
    r = fit_photobleaching(np.full(20, 500.0), frame_interval_s=1.0)
    assert bool(r['fit_success']) is False
    assert r['r_squared'] < 0
    assert len(r['correction_factors']) == 20
