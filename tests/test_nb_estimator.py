"""
N&B recovers the true molecule number — and the apparent brightness has a floor of 1.

This test exists because I nearly "fixed" a correct estimator on the strength of a badly-posed
simulation.

The mistake, and why it matters
-------------------------------
I simulated ``Poisson(N · ε)`` photons per pixel — a **fixed** occupancy — and found N&B
reporting N = 200 against a "true" 10. That looked like a 20× bug.

It was not. With a fixed occupancy there is **no molecular fluctuation at all**: every
"molecule" is indistinguishable from shot noise, so ``var = mean``, ``B = 1``, and ``N = mean``
is the *correct* answer to the question I asked. **The simulation was wrong, not the code.**

The molecular signal in N&B lives in the fluctuation of the OCCUPANCY — molecules entering and
leaving the observation volume. Simulated properly:

* ``N_molecules ~ Poisson(⟨N⟩)`` per frame — the occupancy fluctuates
* each molecule emits ``Poisson(ε)`` photons

then ``B → ε + 1`` and ``N → ⟨N⟩``.

**The +1 is the shot-noise floor**: a perfectly monomeric sample reads B = 1, not B = 0, because
a Poisson emitter's variance equals its mean. **That floor is exactly what a monomeric reference
calibrates away** — which is why an absolute oligomeric state cannot be claimed without one, and
why the uncalibrated output is labelled ``apparent``.
"""

import numpy as np
import pytest


def _stack(n_mean, eps, frames=500, size=24, seed=0):
    """A stack whose OCCUPANCY fluctuates — that is where the molecular signal lives."""
    rng = np.random.default_rng(seed)
    occupancy = rng.poisson(n_mean, (frames, size, size))
    return rng.poisson(occupancy * eps).astype(float)


@pytest.mark.core
@pytest.mark.parametrize("n_true,eps_true", [(10, 5.0), (5, 20.0)])
def test_nb_recovers_number_and_brightness(n_true, eps_true):
    """B → ε + 1 (the shot-noise floor) and N → the true molecule number."""
    nb = pytest.importorskip("pycat.toolbox.nb_tools")

    result = nb.number_and_brightness(_stack(n_true, eps_true),
                                      gain=1.0, offset=0.0, read_variance=0.0)

    brightness = float(np.nanmedian(result["brightness"]))
    number = float(np.nanmedian(result["number"]))

    assert brightness == pytest.approx(eps_true + 1.0, rel=0.15), (
        f"apparent brightness {brightness:.2f}, expected ~{eps_true + 1:.1f}. The +1 is the "
        f"SHOT-NOISE FLOOR: a Poisson emitter's variance equals its mean, so a perfectly "
        f"monomeric sample reads B = 1, not 0. A monomeric reference is what calibrates it "
        f"away."
    )
    assert number == pytest.approx(n_true, rel=0.25), (
        f"number {number:.2f}, expected ~{n_true}. If this fails, check the SIMULATION before "
        f"the code: a stack with a FIXED occupancy has no molecular fluctuation, so N = mean "
        f"is the correct answer and the estimator will look 20x wrong when it is not."
    )


@pytest.mark.core
def test_uncalibrated_number_is_labelled_apparent():
    """N = mean / B. If B is only apparent, N is only apparent — and N looks like a count."""
    nb = pytest.importorskip("pycat.toolbox.nb_tools")

    result = nb.number_and_brightness(_stack(10, 5.0))          # no gain/offset supplied

    assert result["brightness_kind"] == "apparent"
    assert result.get("number_kind") == "apparent", (
        "`brightness` is labelled 'apparent' when the camera is uncalibrated, but `number` "
        "carried no label at all — and it is the more dangerous of the two, because it LOOKS "
        "like a molecule count. N = mean / B: an uncalibrated B makes N uncalibrated too."
    )
