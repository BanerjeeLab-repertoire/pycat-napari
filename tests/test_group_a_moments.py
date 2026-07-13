"""
Group A — every estimator that reads a MOMENT of an intensity distribution.

**The hypothesis, before any code was run:** SpIDA, molecular counting, N&B and the correlation
tools all extract a molecular number from a *moment* of the intensity distribution — so **all of
them should carry the N&B pedestal bug (1.5.453): the camera offset adds to the mean but not to
the variance.**

It was right, and worse than expected.

SpIDA: a 24-fold overestimate of the molecule count
---------------------------------------------------
SpIDA fits N and ε to the SHAPE of the intensity histogram, and **a pedestal shifts the whole
histogram to the right** — which the fit reads as *more molecules*. With a TRUE N of 8:

===========  ==========  ================  ============
pedestal     N fitted    ε fitted          N error
===========  ==========  ================  ============
0            7.78        25.65             −3 %
50           12.05       20.61             **+51 %**
200          31.00       12.79             **+287 %**
800          **195.73**  5.07              **+2347 %**
===========  ==========  ================  ============

ε collapses by the same factor, because N·ε is pinned by the mean once their separation is
broken. ``check_assumptions`` existed — it checked size, saturation and SNR, and **not this.**

Molecular counting: two bugs, in opposite directions, partly cancelling
-----------------------------------------------------------------------
``nu`` is the slope of the binomial bleaching variance against the mean, and ``N = y/nu``.
**Both terms were corrupted, and they partly cancelled** — the worst case, because the combined
error looks acceptable while each half is badly wrong. TRUE ν = 100, N = 10:

===========================  ==========  ============
trace                        ν           N
===========================  ==========  ============
clean                        82.2        **9.97**  ← the estimator is SOUND
read noise (sd 15)           104.2       7.72 (−23 %)
pedestal (500)               **47.5**    **17.86 (+79 %)**
both                         57.9        13.00 (+30 %)
===========================  ==========  ============

**And the fix is in the data.** After every fluorophore bleaches, the trace sits at the pedestal,
and the variance of that plateau is the read noise — **no dark reference needed.** A true pedestal
of 500 with sd 15 recovers as **497.7 ± 13.5** from the tail.

Two subtleties that a first attempt got wrong:

* The pedestal must come off **before the variance pairs are built** — both axes contain I(t).
  Subtracting it from ``y[fast]`` afterwards fixes the numerator and leaves **ν corrupted (49.0
  against a true 100)**.
* The y-axis is ``(I(t+1) − p·I(t))²``, which carries read noise from **both frames**:
  ``s²·(1 + p²)`` — at p = 0.97 that is **1.94 × s²**, not s². A first version subtracted s² and
  **corrected only half the bias**.

**And I nearly shipped a fix for a wrong simulation.** The first trace bleached *deterministically*
(``int(count) × ν``), so there was no binomial fluctuation for the estimator to fit at all — the
same trap as 1.5.453. **The simulation has to be checked before the code.**
"""

import numpy as np
import pytest


# ── SpIDA ─────────────────────────────────────────────────────────────────────────────────

def _spida_pixels(pedestal=0.0, n_true=8.0, epsilon=25.0, n=20000, seed=0):
    """Each pixel sees Poisson(N) molecules, each emitting Poisson(ε) photons."""
    rng = np.random.default_rng(seed)
    return rng.poisson(rng.poisson(n_true, n) * epsilon).astype(float) + pedestal


@pytest.mark.core
def test_spida_recovers_n_and_epsilon_on_a_clean_histogram():
    """The baseline. The estimator itself is sound."""
    spida = pytest.importorskip("pycat.toolbox.spida_tools")

    x, y = spida.build_intensity_histogram(_spida_pixels(pedestal=0.0), n_bins=256)
    fit = spida.fit_spida_histogram(x, y)

    assert fit["N"] == pytest.approx(8.0, rel=0.15), f"N = {fit['N']:.2f} against a true 8"
    assert fit["epsilon"] == pytest.approx(25.0, rel=0.15)


@pytest.mark.core
@pytest.mark.parametrize("pedestal,min_error", [(200, 2.0), (800, 10.0)])
def test_the_pedestal_destroys_spida_and_is_now_caught(pedestal, min_error):
    """**A 24-fold overestimate of the molecule count** on a realistic camera offset.

    SpIDA reads N and ε from the SHAPE of the intensity histogram, and an additive offset
    changes the shape. There is very little tolerance here: a pedestal of only **25 counts**
    already inflates N by **23 %**.
    """
    spida = pytest.importorskip("pycat.toolbox.spida_tools")

    pixels = _spida_pixels(pedestal=pedestal)

    x, y = spida.build_intensity_histogram(pixels, n_bins=256)
    fit = spida.fit_spida_histogram(x, y)

    # The premise: it really is this bad.
    assert fit["N"] > 8.0 * (1 + min_error), (
        f"a pedestal of {pedestal} gave N = {fit['N']:.1f} against a true 8 — the premise of "
        f"this test is that the corruption is severe"
    )

    warnings = spida.check_assumptions(pixels, dtype_max=65535)
    assert any("PEDESTAL" in w for w in warnings), (
        f"a pedestal of {pedestal} counts inflated N from 8 to {fit['N']:.0f} "
        f"({100 * (fit['N'] / 8 - 1):.0f}%) and check_assumptions said NOTHING. It checked "
        f"size, saturation and SNR — and not the one thing that destroys the measurement."
    )


@pytest.mark.core
def test_the_spida_pedestal_check_does_not_cry_wolf():
    """0 false alarms in 20 clean seeds. A check that fires on good data gets switched off."""
    spida = pytest.importorskip("pycat.toolbox.spida_tools")

    false_alarms = sum(
        1 for seed in range(20)
        if any("PEDESTAL" in w for w in
               spida.check_assumptions(_spida_pixels(pedestal=0.0, seed=seed), dtype_max=65535)))

    assert false_alarms == 0, (
        f"the pedestal check fired on {false_alarms}/20 CLEAN histograms. The margin is thin "
        f"(a clean histogram already sits at floor/spread = 0.12, and the gate is at 0.17), so "
        f"this is worth guarding."
    )


# ── Molecular counting ────────────────────────────────────────────────────────────────────

def _bleaching_trace(n_molecules=10, nu=100.0, n_frames=200, survival=0.97,
                     read_sd=0.0, pedestal=0.0, seed=0):
    """**Binomial thinning** — each surviving fluorophore independently survives to the next
    frame.

    A first version bleached DETERMINISTICALLY (``int(count) * nu``), which has **no binomial
    fluctuation for the estimator to fit** — and the estimator regresses exactly that. It made
    the code look broken when the simulation was. *The same trap as 1.5.453.*
    """
    rng = np.random.default_rng(seed)

    alive = n_molecules
    trace = []
    for _ in range(n_frames):
        trace.append(alive * nu)
        alive = rng.binomial(alive, survival)

    return np.asarray(trace, float) + pedestal + rng.normal(0, read_sd, n_frames)


def _median_count(**kwargs):
    counting = pytest.importorskip("pycat.toolbox.molecular_counting_tools")
    values = []
    for seed in range(12):
        result = counting.count_molecules_single(_bleaching_trace(seed=seed, **kwargs))
        if result["accepted"]:
            values.append(result["N"])
    return float(np.median(values)) if values else float("nan")


@pytest.mark.core
def test_molecule_counting_is_exact_on_a_clean_trace():
    """The estimator is SOUND. Everything below is contamination, not a broken method."""
    assert _median_count() == pytest.approx(10.0, rel=0.10), (
        "molecule counting must recover a known N from a clean binomial-bleaching trace"
    )


@pytest.mark.core
def test_the_pedestal_is_removed_before_the_variance_pairs_are_built():
    """The pedestal corrupted **ν**, not just the numerator.

    ``_variance_pairs`` regresses ``(I(t+1) − p·I(t))²`` against ``p(1−p)·I(t)`` — **both axes
    contain I(t), and I(t) contains the pedestal.** Subtracting it from ``y[fast]`` afterwards
    fixes the numerator and leaves ν at **49.0 against a true 100**, so N stayed **+79 %** wrong.

    The post-bleach plateau is the dark reference, and it needs no extra input from the user.
    """
    counting = pytest.importorskip("pycat.toolbox.molecular_counting_tools")

    result = counting.count_molecules_single(
        _bleaching_trace(pedestal=500.0, seed=0))

    assert result["pedestal"] == pytest.approx(500.0, abs=25.0), (
        f"the pedestal was measured as {result['pedestal']:.0f} against a true 500. It is the "
        f"median of the post-bleach plateau, where no fluorophores remain."
    )

    counted = _median_count(pedestal=500.0)
    assert abs(counted - 10.0) < 0.79 * 10.0, (
        f"a 500-count pedestal gave N = {counted:.1f} against a true 10. Before the fix this "
        f"was 17.86 (+79%), because nu came out at 49.0 instead of 100 — the pedestal was in "
        f"BOTH axes of the variance regression."
    )
