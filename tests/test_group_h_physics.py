"""
Group H — the phase diagram, the force curve, the fibre.

``temperature_tools`` feeds the **phase diagrams and Csat** that go into the manuscript, and its
defaults returned **the start of the temperature ramp**.

The cloud-point detector was reading the wrong signal
------------------------------------------------------
Tested against a simulated heat-cool cycle with a **known** transition (cloud 30 °C, clear 27 °C,
hysteresis 3 °C):

======================  ==========  ==========  ============
signal                  T_phase     T_clear     hysteresis
======================  ==========  ==========  ============
*(truth)*               **30.0**    **27.0**    **3.0**
entropy_corrected       **20.74**   **20.79**   0.05
entropy                 20.68       20.71       0.03
image_mean              21.01       21.01       0.00
**focus_score**         **29.67**   **27.00**   **2.67**
======================  ==========  ==========  ============

**Every signal except ``focus_score`` returned ~20.7 — the first temperature in the ramp.** The
transition was not detected at all, and **a phase diagram built from that default would be a plot
of when the experiment started.**

**Why entropy fails.** Shannon entropy of the intensity histogram is **non-monotonic** across a
phase transition: it *drops* as droplets nucleate (the histogram becomes bimodal and concentrated)
and *recovers* as they grow. Measured across the heating branch: **6.47 → 4.84 → 6.13.** An onset
detector cannot find an onset in that — *there isn't one*.

``focus_score`` rises **monotonically** (0.02 → 1.14) because droplets introduce **sharp edges**,
and a phase transition is precisely the appearance of an interface. **It is the physically right
signal.**

Also audited
------------
* ``wlc_extensible`` is **exact** — 0.00 % against the analytic Odijk high-force limit at every
  force tested.
* ``fibril_morphometry`` recovers fibre length accurately, **but ``persistence_length_um`` scales
  with the fibre length** (Lp ≈ 1.9 × L on *perfectly straight* fibres, which have infinite Lp).
  Documented; ``tortuosity`` is correct and should be preferred.
"""

import numpy as np
import pytest


def _cloud_point_cycle(cloud_C=30.0, clear_C=27.0, size=96, seed=0):
    """A heat-then-cool cycle with a **known** cloud point, clear point and hysteresis.

    Below the transition the sample is clear; above it, droplets nucleate and scatter light. The
    cooling branch clears at a LOWER temperature — that is the hysteresis a phase diagram reports.
    """
    yy, xx = np.mgrid[0:size, 0:size]
    rng = np.random.default_rng(seed)

    heating = np.linspace(20, 40, 41)
    cooling = np.linspace(40, 20, 41)
    temperatures = np.concatenate([heating, cooling])

    def _frame(T, t_crit, sharpness=0.8):
        fraction = 1.0 / (1.0 + np.exp(-(T - t_crit) / sharpness))
        img = np.full((size, size), 1000.0)
        for _ in range(int(60 * fraction)):
            cy, cx = rng.integers(6, size - 6, size=2)
            img[np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2) < 4] *= 0.6
        return np.clip(img + rng.normal(0, 15, img.shape), 1, 65535)

    frames = ([_frame(T, cloud_C) for T in heating]
              + [_frame(T, clear_C) for T in cooling])

    return np.stack(frames).astype(np.float32), temperatures


# ── The temperature tests are REMOVED. My simulation was wrong, not the pipeline. ────────
#
# 1.5.488 changed the cloud-point defaults from `entropy_corrected` + `baseline` to
# `focus_score` + `midpoint`, on the strength of a simulation that showed entropy returning the
# start of the ramp.
#
# **The simulation was wrong.** Every scene gave the "clear" sample an intensity spread of
# sd = 15, which already fills the histogram — entropy started at 7.1 out of a theoretical
# maximum of 8.0 and **had nowhere to rise**:
#
#     CLEAR sample, tiny noise (sd 2):        entropy **7.189**
#     TURBID sample, strong scatter (sd 120): entropy **6.948**
#
# Entropy is flat-to-falling across every scene I built, because `entropy_turbidity_curve` bins
# each frame against its OWN intensity range — and a Gaussian binned to its own spread has nearly
# the same entropy whatever its width. **The metric was never given a chance to respond.**
#
# **Gable validated the cloud points on real temperature-ramp data and they are accurate.** The
# defaults are reverted (1.5.489), and a test that encodes a wrong conclusion is worse than no
# test — so these are removed rather than adjusted to pass.
#
# The real open question is **low-quality data** (focus drift, illumination instability, bubbles),
# which is written up in `docs/audits/DEV_NOTES.md`. Testing that needs a realistic degradation
# model, not a synthetic phase transition.


@pytest.mark.core
@pytest.mark.parametrize("force_pN", [5.0, 20.0, 60.0])
def test_the_extensible_wlc_matches_the_analytic_odijk_limit(force_pN):
    """Audited and **exact** — 0.00 % against ``x/L0 = 1 - 0.5*sqrt(kT/(F*Lp)) + F/S``."""
    fd = pytest.importorskip("pycat.toolbox.fd_curve_tools")

    Lc, Lp, S, kT = 16.49, 50.0, 1500.0, 4.11

    measured = float(fd.wlc_extensible(
        np.array([force_pN]), contour_length_um=Lc, persistence_length_nm=Lp,
        stretch_modulus_pN=S, kT_pN_nm=kT)[0])

    odijk = Lc * (1 - 0.5 * np.sqrt(kT / (force_pN * Lp)) + force_pN / S)

    assert measured == pytest.approx(odijk, rel=1e-6), (
        f"the WLC gave {measured:.4f} um at {force_pN} pN against an analytic {odijk:.4f}"
    )


@pytest.mark.core
def test_the_fibril_persistence_length_scales_with_the_fibre_LENGTH():
    """**A perfectly straight fibre has INFINITE persistence length.** The reported Lp does not.

    Lp is estimated from the decay of the tangent autocorrelation — and on a straight fibre **the
    correlation never decays**, so the fit is bounded only by *how much fibre was available*:

    ==============  =============
    fibre length    reported Lp
    ==============  =============
    40 px           72.1
    80 px           149.1
    120 px          226.0
    200 px          379.9
    ==============  =============

    **Lp ≈ 1.9 × the fibre length.** Two conditions whose fibres differ only in LENGTH would show
    different "stiffness", and it would look like a real result.

    This test asserts the confound EXISTS, so the warning cannot be quietly dropped. **If Lp is
    ever made length-invariant, this test should fail — and that would be a real improvement.**

    (``tortuosity`` does NOT have this problem: 1.0021–1.0106 across the same fibres, correctly
    reporting that all of them are straight.)
    """
    fibril = pytest.importorskip("pycat.toolbox.fibril_tools")

    size = 256
    lengths = {}
    tortuosities = {}
    for L in (40, 200):
        mask = np.zeros((size, size), bool)
        mask[127:130, 20:20 + L] = True
        segments, _nodes, _summary = fibril.fibril_morphometry(mask.astype(np.int32))
        lengths[L] = float(segments[0]['persistence_length_um'])
        tortuosities[L] = float(segments[0]['tortuosity'])

    assert lengths[200] > 3.0 * lengths[40], (
        f"Lp = {lengths[40]:.1f} on a 40 px straight fibre and {lengths[200]:.1f} on a 200 px "
        f"one. BOTH are perfectly straight — their true persistence length is infinite. If this "
        f"has been fixed, the warning on fibril_morphometry should be updated."
    )
    # Tortuosity is the statistic that behaves.
    assert all(abs(t - 1.0) < 0.05 for t in tortuosities.values()), (
        f"tortuosity is the statistic that WORKS here: {tortuosities}"
    )
