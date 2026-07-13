"""
A movie shorter than the bleach time cannot measure the bleach time.

``fit_photobleaching`` produces τ, and the bleach correction **divides by exp(-t/τ)** — so every
corrected intensity downstream inherits any error in it, and the error **compounds
exponentially with time.**

Measured, true τ = 50 s, 30 realisations:

=================  =================  ========
movie length       τ (sd)             mean R²
=================  =================  ========
100 s (2 τ)        49.6 (0.8)         0.993
50 s (1 τ)         48.9 (1.7)         0.988
25 s (½ τ)         42.6 (8.6)         0.971
10 s (⅕ τ)         **35.2 (18.9)**    0.881
=================  =================  ========

R² stays high throughout. **And the consequence is far larger than the scatter suggests** — the
over-correction of the final frame:

* 100 s movie — τ = 50.0 — **−0.0 %**
* 25 s movie — τ = 25.0 — **+63.5 %**
* 10 s movie — τ = 11.0 — **+95.6 %**

**The correction nearly doubles the final intensity** on a movie a fifth of the bleach time.

The check must NOT use the fitted τ
-----------------------------------
That is circular, and it let the worst case through. On a movie a fifth of the true bleach time,
τ fits to **11 s** — so ``movie_length / τ_fitted`` = 10/11 = **0.9**, and a check against the
fitted τ **passes**. The quantity being checked against is itself the thing that is wrong.

Subtracting the fitted ``I_inf`` was no better: on a short movie ``I_inf`` is also badly
determined, and it scrambled the ordering — a 0.5 τ movie came out looking *better observed*
than a 0.6 τ one.

The non-circular test uses the **raw** intensities: how far did the signal actually fall?
``exp(-1) = 0.368``, ``exp(-0.5) = 0.607``. That is a property of the data, not of the fit.
"""

import numpy as np
import pytest

_TRUE_TAU = 50.0


def _movie(window_in_taus, seed=0):
    rng = np.random.default_rng(seed)
    n_frames = int(_TRUE_TAU * window_in_taus / 0.5)
    t = np.arange(n_frames) * 0.5
    return 1000.0 * np.exp(-t / _TRUE_TAU) + rng.normal(0, 20, n_frames)


@pytest.mark.core
@pytest.mark.parametrize("window_in_taus", [2.0, 1.0, 0.6, 0.5, 0.2])
def test_observed_decay_is_measured_from_the_data_not_the_fit(window_in_taus):
    """The reported observation window must track the TRUE one, not the fitted τ."""
    physics = pytest.importorskip("pycat.toolbox.condensate_physics_tools")

    result = physics.fit_photobleaching(_movie(window_in_taus), frame_interval_s=0.5)
    observed = result.get("observation_window_in_taus")

    assert observed is not None, "fit_photobleaching did not report the observation window"

    # It should be within ~25 % of the true window. The point is that it tracks the TRUTH —
    # a check against the fitted tau reports 0.9 for the 0.2-tau movie, because tau itself
    # comes out at 11 s instead of 50.
    assert abs(observed - window_in_taus) < 0.25 * max(window_in_taus, 0.2) + 0.1, (
        f"the movie is {window_in_taus:.2f} bleach constants long, but the reported "
        f"observation window is {observed:.2f}. This must be computed from the RAW decay — "
        f"how far the signal actually fell — not from the fitted tau, which on a short movie "
        f"is itself wrong (it fits 11 s against a true 50 s, so movie/tau_fitted = 0.9 and a "
        f"circular check passes)."
    )


@pytest.mark.core
def test_short_movie_is_flagged_and_adequate_one_is_not():
    """A movie of one bleach constant is fine; a fifth of one is not."""
    physics = pytest.importorskip("pycat.toolbox.condensate_physics_tools")

    good = physics.fit_photobleaching(_movie(2.0), frame_interval_s=0.5)
    bad = physics.fit_photobleaching(_movie(0.2), frame_interval_s=0.5)

    assert good["observation_window_in_taus"] > 1.0, (
        "a movie twice the bleach time must not be flagged — the guard must not cry wolf"
    )
    assert bad["observation_window_in_taus"] < 0.5, (
        f"a movie a FIFTH of the bleach time reported an observation window of "
        f"{bad['observation_window_in_taus']:.2f}. It fits tau = {bad['tau_bleach_s']:.1f} "
        f"against a true 50 s, with R2 = {bad['r_squared']:.3f} — and the bleach correction "
        f"would over-correct the final frame by ~96%."
    )
