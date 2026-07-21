"""
"Arrested" is a physical claim. It must not be made from a fit statistic.

``fit_coarsening`` decides whether a condensate population is coarsening (Ostwald ripening,
R ~ t^⅓; or coalescence, R ~ t^½) or **arrested** — kinetically trapped, not growing at all.
That is a mechanistic conclusion about the sample.

It used to be decided partly by::

    is_arrested = (max(ostwald_r2, coalescence_r2) < 0.3      # <- a FIT statistic
                   or abs(radius_change) < 2.0 * noise)

**R² measures how well a power law describes the data. It says nothing about whether the radius
grew.** Noise destroys R² while the radius keeps growing — so a genuinely coarsening series gets
reported as *"no coarsening happened."*

Measured on synthetic data where the radius genuinely grows **3.7-fold**, rate of calling
"arrested":

==========================  =======  ===========  ============  ===============
data                        noise    R² < 0.3     ΔR < 2σ       slope test
==========================  =======  ===========  ============  ===============
COARSENING (should be 0 %)  0.30     **42 %**     38 %          **0 %**
ARRESTED   (should be 100%) any      100 %        98 %          **100 %**
==========================  =======  ===========  ============  ===============

**At 30 % scatter the old test called 42 % of genuinely coarsening series "arrested".**

The honest question is *did the radius grow, given how noisy the measurement is?* — a question
about the SLOPE and its standard error, not about how well a power law fits.
"""

import numpy as np
import pytest


def _coarsening(noise, seed):
    """R ~ t^(1/3): the radius genuinely grows 3.7-fold."""
    rng = np.random.default_rng(seed)
    t = np.linspace(1, 100, 30)
    return t, 2.0 * t ** (1 / 3) * (1 + rng.normal(0, noise, 30))


def _arrested(noise, seed):
    """R = constant: kinetically trapped, no growth at all."""
    rng = np.random.default_rng(seed)
    t = np.linspace(1, 100, 30)
    return t, 5.0 * np.ones(30) * (1 + rng.normal(0, noise, 30))


@pytest.mark.core
@pytest.mark.parametrize("noise", [0.05, 0.20, 0.30])
def test_coarsening_is_not_called_arrested_because_it_is_noisy(noise):
    """A radius that grows 3.7-fold has not arrested, however noisy the measurement."""
    physics = pytest.importorskip("pycat.toolbox.condensate_physics_tools")

    false_arrests = 0
    for seed in range(20):
        t, radius = _coarsening(noise, seed)
        result = physics.fit_coarsening(t, radius)
        if result.get("preferred_mechanism") == "arrested":
            false_arrests += 1

    assert false_arrests == 0, (
        f"{false_arrests}/20 genuinely COARSENING series (the radius grows 3.7-fold) were "
        f"called 'arrested' at {noise:.0%} scatter. 'Arrested' is a physical claim — that the "
        f"condensates are kinetically trapped and not growing — and it was being made from "
        f"R² < 0.3, a FIT statistic that noise destroys while the radius keeps growing. The "
        f"test must ask whether the SLOPE is significantly positive, not how well a power law "
        f"fits."
    )


@pytest.mark.core
@pytest.mark.parametrize("noise", [0.05, 0.20, 0.30, 0.50])
def test_genuinely_arrested_growth_is_still_detected(noise):
    """The fix must not cost sensitivity: a flat series must still be called arrested."""
    physics = pytest.importorskip("pycat.toolbox.condensate_physics_tools")

    detected = 0
    for seed in range(20):
        t, radius = _arrested(noise, seed)
        result = physics.fit_coarsening(t, radius)
        if result.get("preferred_mechanism") == "arrested":
            detected += 1

    assert detected == 20, (
        f"only {detected}/20 genuinely ARRESTED series (R = constant) were detected at "
        f"{noise:.0%} scatter. Removing the R² clause must not cost sensitivity to real "
        f"arrest — the slope test should catch every one of them."
    )


# ── Byte-identical characterization: pins the EXACT output before/after a phase-split ────────────
#
# The property tests above pin the arrest CLASSIFICATION across noise; this pins the exact numerical
# output dict (r2s, rate constants, bootstrap agreement, radius change) on two deterministic scenarios,
# so a phase-split of `fit_coarsening` can be proven to move no number. The bootstrap is seeded
# (`default_rng(0)` inside the fit), so the agreement value is deterministic.

@pytest.mark.core
def test_fit_coarsening_output_is_byte_identical():
    physics = pytest.importorskip("pycat.toolbox.condensate_physics_tools")

    # OSTWALD ripening, low noise — a confident power-law call.
    t, R = _coarsening(0.05, seed=3)
    out = physics.fit_coarsening(t, R)
    assert out['preferred_mechanism'] == 'ostwald'
    assert out['mechanism_confidence'] == 'high'
    assert np.isclose(out['mechanism_bootstrap_agreement'], 0.905, atol=1e-12)
    assert np.isclose(out['ostwald_r2'], 0.9699059208099262, atol=1e-9)
    assert np.isclose(out['coalescence_r2'], 0.9686712225701184, atol=1e-9)
    assert np.isclose(out['radius_change_um'], 7.544318517032984, atol=1e-9)
    assert np.isclose(out['radius_change_frac'], 3.422869289381456, atol=1e-9)
    assert np.isclose(out['ostwald_K'], 2.0961071979836112, atol=1e-9)
    assert np.isclose(out['coalescence_K'], 0.8011093416513432, atol=1e-9)
    assert np.isclose(out['R0'], -0.29690230986955557, atol=1e-9)
    assert np.isclose(np.nansum(out['fit_radii_ostwald']), 210.123838094, atol=1e-6)
    assert np.isclose(np.nansum(out['fit_radii_coalescence']), 210.123838387, atol=1e-6)

    # ARRESTED — the slope test wins, no coarsening exponent fitted.
    t, R = _arrested(0.05, seed=3)
    out = physics.fit_coarsening(t, R)
    assert out['preferred_mechanism'] == 'arrested'
    assert out['mechanism_confidence'] == 'n/a (arrested)'
    assert np.isnan(out['mechanism_bootstrap_agreement'])
    assert np.isclose(out['ostwald_r2'], 0.028139152831067382, atol=1e-9)
    assert np.isclose(out['coalescence_r2'], 0.03739274030714068, atol=1e-9)
    assert np.isclose(out['radius_change_um'], -0.2596513800273721, atol=1e-9)
    assert np.isclose(out['radius_change_frac'], -0.04712169734799228, atol=1e-9)
    assert np.isclose(out['ostwald_K'], 0.05146967574028197, atol=1e-9)
    assert np.isclose(out['R0'], 5.510229780346295, atol=1e-9)
