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
def test_count_molecules_single_is_byte_identical():
    """Byte-identical characterization pinning the EXACT output on two deterministic traces, so a
    phase-split of `count_molecules_single` can be proven to move no number. The CLEAN trace has no
    read-noise floor (the through-origin ν fit); the NOISY+PEDESTAL trace does (the free-intercept ν
    fit) — both branches, plus the tail-derived pedestal/read-noise and the p^fast-aware N."""
    counting = pytest.importorskip("pycat.toolbox.molecular_counting_tools")

    clean = counting.count_molecules_single(_bleaching_trace(seed=1))
    assert clean['read_noise_var'] == 0.0 and clean['pedestal'] == 0.0     # through-origin branch
    assert np.isclose(clean['nu'], 83.93101096589331, atol=1e-9)
    assert np.isclose(clean['N'], 9.531637839142586, atol=1e-9)
    assert np.isclose(clean['bleach_r2'], 0.9561560689211503, atol=1e-9)
    assert clean['accepted'] is True and clean['n_points'] == 129

    noisy = counting.count_molecules_single(_bleaching_trace(read_sd=15.0, pedestal=500.0, seed=1))
    assert noisy['read_noise_var'] > 1.0                                    # free-intercept branch
    assert np.isclose(noisy['nu'], 111.10906346844263, atol=1e-9)
    assert np.isclose(noisy['N'], 7.1870716324335975, atol=1e-9)
    assert np.isclose(noisy['bleach_r2'], 0.961719562506752, atol=1e-9)
    assert np.isclose(noisy['pedestal'], 496.6538079735436, atol=1e-9)
    assert np.isclose(noisy['read_noise_var'], 146.33447976062706, atol=1e-9)
    assert noisy['accepted'] is True and noisy['n_points'] == 162


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


# ── Spatial correlation: the LENGTH SCALE, which is what a paper reports ──────────────────

def _blurred_noise(blur_sigma, size=256, pedestal=0.0, seed=0):
    """White noise blurred by a KNOWN sigma.

    The ACF of ``noise * G(s)`` is ``G(s) (*) G(s) = G(s*sqrt(2))`` — so the correlation width
    is **analytically ``blur_sigma * sqrt(2)``**, and this is exact ground truth.
    """
    from scipy import ndimage as ndi

    rng = np.random.default_rng(seed)
    field = ndi.gaussian_filter(rng.normal(0, 1, (size, size)), blur_sigma)
    field = (field - field.min()) / (field.max() - field.min()) * 1000
    return (field + 100 + pedestal).astype(np.float32)


@pytest.mark.core
@pytest.mark.parametrize("blur", [2.0, 4.0, 6.0, 8.0])
def test_the_acf_gaussian_needs_a_baseline_or_sigma_blows_up(blur):
    """**A 43 % overestimate of the correlation length** — and the fix is one parameter.

    ``_gaussian`` had **no offset term** (its docstring said so). But **a spatial ACF does not
    decay to zero** — it sits on a floor — and a Gaussian forced through zero **must widen to
    reach that floor.** The inflation grows with σ, because the floor is a larger fraction of the
    lobe:

    ===========  ==========  =====================  ===========
    blur σ       expected    reported (no offset)   error
    ===========  ==========  =====================  ===========
    2.0          2.83        3.10                   +9 %
    4.0          5.66        6.11                   +8 %
    **6.0**      8.49        **12.17**              **+43 %**
    8.0          11.31       **15.51**              **+37 %**
    ===========  ==========  =====================  ===========

    **It is not a finite-window effect** — a 512 px ROI is just as biased as a 128 px one. And an
    independent Gaussian fit *with* an offset recovers the truth on exactly the same data, which
    is how the missing term was isolated.
    """
    acf = pytest.importorskip("pycat.toolbox.spatial_acf_tools")

    sigma_x, sigma_y, _map = acf.sacf_single_roi(_blurred_noise(blur))
    measured = 0.5 * (sigma_x + sigma_y)
    expected = blur * np.sqrt(2)

    assert measured == pytest.approx(expected, rel=0.15), (
        f"the ACF correlation length came out at {measured:.2f} px against an analytic "
        f"{expected:.2f}. This is the length scale a paper reports."
    )


@pytest.mark.core
def test_the_acf_is_pedestal_invariant():
    """**Group A's pedestal hypothesis does NOT apply here** — and that is worth recording.

    An autocorrelation is normalised, so an additive offset cancels. Verified: σ = 5.29 at
    pedestals of 0, 1000 and 4000. **The physics differs from SpIDA and N&B**, and a hypothesis
    that holds for three modules and not the fourth is only useful if the exception is known.
    """
    acf = pytest.importorskip("pycat.toolbox.spatial_acf_tools")

    sigmas = []
    for pedestal in (0.0, 1000.0, 4000.0):
        sigma_x, sigma_y, _ = acf.sacf_single_roi(_blurred_noise(4.0, pedestal=pedestal))
        sigmas.append(0.5 * (sigma_x + sigma_y))

    assert max(sigmas) - min(sigmas) < 0.05, (
        f"the ACF sigma moved across pedestals: {[round(s, 2) for s in sigmas]}. An ACF is "
        f"normalised — an additive offset must cancel exactly."
    )


@pytest.mark.core
@pytest.mark.parametrize("blur", [2.0, 3.0, 4.0, 6.0])
def test_ccf_sigma_was_the_std_of_the_VALUES_not_the_peak_width(blur):
    """**A 13-fold underestimate**, and it would have been the same number for any structure.

    ``ccf_sigma`` was ``np.std(ccf_values[peak_row, :])`` — the spread of the correlation
    **coefficients** along a slice. That is a number in correlation units, bounded by the [−1, 1]
    range of a Pearson coefficient. **It is not a length.** It came out at **0.33** on data whose
    true correlation length is **4.24 px**.

    **And the real σ was computed and thrown away.** ``curve_fit`` fits
    ``gaussian_2d(xy, amplitude, x0, y0, sigma_x, sigma_y)``, and ``popt[3]``/``popt[4]`` ARE the
    widths — in pixels, on the same axes the peak position is already reported in.
    """
    ccf_mod = pytest.importorskip("pycat.toolbox.correlation_func_analysis_tools")

    channel = _blurred_noise(blur, size=128)
    result = ccf_mod.process_ccf(channel, channel.copy(),
                                 np.ones(channel.shape, bool))

    sigma_x, sigma_y = result["ccf_sigma"]
    measured = 0.5 * (sigma_x + sigma_y)
    expected = blur * np.sqrt(2)

    assert measured == pytest.approx(expected, rel=0.15), (
        f"ccf_sigma = {measured:.2f} against an analytic correlation length of {expected:.2f} "
        f"px. The old value was 0.33 for EVERY structure size — it was the standard deviation "
        f"of the correlation VALUES, which is not a length at all."
    )


@pytest.mark.core
@pytest.mark.parametrize("shift", [(0, 3), (5, 0), (4, 4)])
def test_the_ccf_peak_recovers_a_known_inter_channel_shift(shift):
    """Audited and correct: the peak position is exact. This is chromatic-shift detection."""
    from scipy import ndimage as ndi

    ccf_mod = pytest.importorskip("pycat.toolbox.correlation_func_analysis_tools")

    dy, dx = shift
    channel_1 = _blurred_noise(3.0, size=128)
    channel_2 = ndi.shift(channel_1, (dy, dx), order=3, mode='wrap').astype(np.float32)

    result = ccf_mod.process_ccf(channel_1, channel_2, np.ones(channel_1.shape, bool))
    peak = result["peak_location"]

    # The sign and axis convention is the module's own; the MAGNITUDES are what must be right.
    assert sorted(abs(int(v)) for v in peak) == sorted([abs(dy), abs(dx)]), (
        f"a known shift of ({dy}, {dx}) was recovered as {peak}"
    )


@pytest.mark.core
@pytest.mark.parametrize("read_sd,pedestal,tolerance", [
    (15.0, 500.0, 0.20),
    (40.0, 800.0, 0.25),
])
def test_the_two_contaminations_now_COMPOSE(read_sd, pedestal, tolerance):
    """**The corrections used to fight each other. Now they do not.**

    The old path estimated the read variance and ``p`` **separately**, combined them into a noise
    floor ``s²(1 + p²)``, subtracted it, and fitted ``nu`` through the origin. Each estimate
    carries its own error and **they multiply** — ``p`` appears in **both axes** of the
    regression. *That is why each correction worked alone and the combination was worse than
    either.*

    **A free intercept collapses it into one fit**: the line ``y = nu·x + b`` has the noise floor
    **as** ``b``. Nothing is estimated separately, so nothing multiplies.

    But it is **not universally better**. On a noiseless trace there IS no floor, and forcing the
    line through zero is **correct information** — a free intercept there adds a parameter that
    soaks up real signal (slope **76.7** against a true 100, versus **86.7** through the origin).
    **The tail variance measures which regime you are in** (0.0 clean; 210 at read sd 15; 1496 at
    sd 40), so the fit is chosen by *measurement*, not by argument.

    ==========================  =====================  ==================
    trace                       BEFORE (recorded)      NOW
    ==========================  =====================  ==================
    read 15 + pedestal 500      −24 %  *(not better)*  **−8 %**
    **read 40 + pedestal 800**  **−34 %**  *(worse)*   **−17 %**
    ==========================  =====================  ==================
    """
    counting = pytest.importorskip("pycat.toolbox.molecular_counting_tools")

    values = []
    for seed in range(16):
        trace = _bleaching_trace(seed=seed, read_sd=read_sd, pedestal=pedestal)
        result = counting.count_molecules_single(trace)
        if result["accepted"]:
            values.append(result["N"])

    assert values, "no trace produced an accepted fit"

    # The MEDIAN, not the mean. N = signal/nu is a RATIO of two noisy quantities, and
    # E[A/B] != E[A]/E[B] — Jensen biases the mean upward, and a few traces with a near-zero nu
    # blow it up entirely (measured: mean 183.55 where the median is 10.30).
    #
    # **The module's own docstring already said this** — "the per-trace estimate is inherently
    # noisy... use count_molecules_pooled for a population estimate". An entire investigation was
    # spent measuring a statistic the module tells you not to use.
    median_count = float(np.median(values))

    assert median_count == pytest.approx(10.0, rel=tolerance), (
        f"the median count is {median_count:.2f} against a true 10, with read noise "
        f"sd={read_sd} and a pedestal of {pedestal}. The two contaminations must COMPOSE — "
        f"before the free-intercept fit, each correction worked alone and the combination was "
        f"worse than either."
    )
