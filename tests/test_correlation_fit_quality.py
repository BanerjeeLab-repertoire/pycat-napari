"""
**A correlation length from an image with NO correlation was reported exactly like a real one.**

``fit_gaussian_2d`` fits a Gaussian to the correlation function and returns its width — the
correlation length. On **pure noise**, where there is no Gaussian to fit, ``curve_fit`` still
returns *a* number (0.495 in testing), **and nothing distinguished it from a real measurement.**

The signal was there, and unused
--------------------------------
``goodness_of_fit`` is ``sqrt(diag(pcov))`` — **the standard error on each fitted parameter.**

===================  ===========  =================
scene                ccf_sigma    error on sigma
===================  ===========  =================
real (σ = 5)         **5.000**    **0.0**
real + noise         5.016        0.0
**PURE NOISE**       **0.495**    **241.0**
===================  ===========  =================

**A three-order-of-magnitude signal** — and it was put in a DataFrame column labelled
``'Covariance'``, **which nothing ever read.**

*(This is the same module whose ``ccf_sigma`` was a 13× underestimate — 1.5.481. It measured the
std of the correlation VALUES rather than the peak WIDTH. That is fixed and holds here: a true
σ = 5 recovers as **5.000**.)*
"""

import io
import contextlib

import numpy as np
import pytest


def _grid(size=64):
    return np.meshgrid(np.arange(size) - size // 2, np.arange(size) - size // 2)


def _fit(image):
    coloc = pytest.importorskip("pycat.toolbox.correlation_func_analysis_tools")
    X, Y = _grid(image.shape[0])
    with contextlib.redirect_stderr(io.StringIO()):
        result = coloc.fit_gaussian_2d(image, X, Y)
    return result


@pytest.mark.core
@pytest.mark.parametrize("true_sigma", [2.0, 5.0, 10.0])
def test_a_REAL_correlation_length_is_recovered_and_called_meaningful(true_sigma):
    """The fix from 1.5.481 holds: a true σ = 5 comes back as **5.000**."""
    X, Y = _grid()
    image = np.exp(-((X ** 2 + Y ** 2)) / (2 * true_sigma ** 2))

    result = _fit(image)

    measured = float(np.ravel(result['ccf_sigma'])[0])
    assert measured == pytest.approx(true_sigma, rel=0.10), (
        f"the fitted correlation length is {measured:.3f} against a true {true_sigma}"
    )
    assert result['fit_is_meaningful'], (
        f"a clean Gaussian was called meaningless (relative error "
        f"{result['sigma_rel_error']:.3f})"
    )


@pytest.mark.core
def test_PURE_NOISE_is_NOT_reported_as_a_correlation_length():
    """**The whole point.** ``curve_fit`` returns a number for anything you hand it.

    On pure noise it returned **0.495** — a perfectly plausible-looking correlation length — while
    **the error on that number was 241.** The fit knew it had failed. *Nothing asked it.*
    """
    called_meaningful = 0
    for seed in range(12):
        noise = np.random.default_rng(seed).normal(0, 1, (64, 64))
        result = _fit(noise)
        if result['fit_is_meaningful']:
            called_meaningful += 1

    assert called_meaningful <= 3, (
        f"{called_meaningful}/12 PURE-NOISE images were reported as having a meaningful "
        f"correlation length. There is no correlation in white noise, and a fitted sigma from it "
        f"is a number the fit does not believe."
    )


@pytest.mark.core
def test_the_fit_survives_HEAVY_noise_on_a_real_signal():
    """**A guard with no power is a guard that never says anything.**

    A real correlation buried in heavy noise must still be called meaningful — otherwise the flag
    would just reject everything and look impressive doing it.
    """
    X, Y = _grid()
    detected = 0
    for seed in range(8):
        image = (np.exp(-((X ** 2 + Y ** 2)) / (2 * 5.0 ** 2))
                 + np.random.default_rng(seed).normal(0, 0.5, (64, 64)))
        result = _fit(image)
        if result['fit_is_meaningful']:
            detected += 1

    assert detected >= 7, (
        f"only {detected}/8 real correlations survived heavy noise — the quality flag is "
        f"rejecting good data"
    )


@pytest.mark.core
def test_the_relative_error_is_SCALE_FREE():
    """It must mean the same thing for a 2-px correlation and a 20-px one."""
    X, Y = _grid()

    errors = []
    for sigma in (2.0, 5.0, 10.0):
        image = np.exp(-((X ** 2 + Y ** 2)) / (2 * sigma ** 2))
        errors.append(_fit(image)['sigma_rel_error'])

    assert all(e < 0.1 for e in errors), (
        f"the relative error varies with the correlation length ({errors}) — it must be "
        f"scale-free, or a single threshold cannot work across scales"
    )


# ── The SACF threw its covariance away entirely ───────────────────────────────────────────

def _acf_row(correlation_px, noise=0.0, size=128, seed=0):
    """A **realistic** ACF — computed from an actual image, not a Gaussian plus white noise.

    *This distinction matters, and it caught me out.* An autocorrelation is **smooth by
    construction** — correlating an image averages its noise away. A first version of this test
    built ``exp(-x²/2σ²) + white noise``, which is **not what an ACF looks like**, and concluded
    the quality guard was rejecting good data when it was really the *estimator* failing on an
    input it would never see.
    """
    from scipy import ndimage as ndi

    rng = np.random.default_rng(seed)
    image = ndi.gaussian_filter(rng.normal(0, 1, (size, size)), correlation_px)
    if noise:
        image = image + rng.normal(0, noise * 0.02, (size, size))

    spectrum = np.fft.fft2(image - image.mean())
    acf = np.fft.fftshift(np.real(np.fft.ifft2(spectrum * np.conj(spectrum))))
    acf /= acf.max()

    half = size // 2
    return acf[half, half - 32:half + 33], np.arange(-32, 33).astype(float)


@pytest.mark.core
@pytest.mark.parametrize("noise", [0.0, 0.5, 1.0])
def test_a_REAL_spatial_correlation_survives_the_quality_gate(noise):
    """**A guard with no power is a guard that never says anything.**

    Measured: 8/8 real correlations recovered, at every noise level.
    """
    acf_tools = pytest.importorskip("pycat.toolbox.spatial_acf_tools")

    recovered = 0
    for seed in range(8):
        row, axis = _acf_row(5.0, noise=noise, seed=seed)
        with contextlib.redirect_stderr(io.StringIO()):
            sigma = acf_tools._fit_sacf_1d(row, axis)
        if np.isfinite(sigma):
            recovered += 1

    assert recovered >= 7, (
        f"only {recovered}/8 REAL spatial correlations survived the quality gate at noise "
        f"{noise}. The gate is rejecting good data."
    )


@pytest.mark.core
def test_PURE_NOISE_does_not_yield_a_spatial_correlation_length():
    """``popt, _ = curve_fit(...)`` — **that ``_`` was the covariance, and it was the only signal.**

    ``curve_fit`` succeeds on anything. On white noise this function returned **119.8 px** and
    **0.62 px** — *finite, plausible-looking correlation lengths* — and **nothing said they were
    meaningless.**

    A spatial autocorrelation length is a **physical claim about structure in the image**, and
    there is no structure in white noise.
    """
    acf_tools = pytest.importorskip("pycat.toolbox.spatial_acf_tools")

    finite = 0
    for seed in range(12):
        row, axis = _acf_row(0.0, seed=seed)     # gaussian_filter(sigma=0) = no correlation
        with contextlib.redirect_stderr(io.StringIO()):
            sigma = acf_tools._fit_sacf_1d(row, axis)
        if np.isfinite(sigma):
            finite += 1

    assert finite <= 5, (
        f"{finite}/12 PURE-NOISE images returned a finite correlation length. Before the fix it "
        f"was 6/12, and one of them was 119.8 px."
    )


@pytest.mark.core
def test_the_error_helper_does_not_GUESS_where_the_width_is():
    """**A helper that infers the width index from ``len(popt)`` is a bug, and I wrote one.**

    ``len(popt) > 3 → width at [3, 4]`` is right for the **5**-parameter 2-D model
    ``(amp, x0, y0, σx, σy)`` and **wrong for the 4-parameter 1-D model**
    ``(amp, μ, σ, baseline)`` — where the width is still at [2] and index 3 is the **baseline**.

    It read the baseline's error as the width's, divided by a baseline of ~0, and returned
    ``inf`` — **rejecting every real fit.** *That is a guard with no power: the exact failure this
    audit keeps flagging, and I built one.*

    **The caller knows its own model. It passes the index.**
    """
    coloc = pytest.importorskip("pycat.toolbox.correlation_func_analysis_tools")

    # A 4-parameter 1-D model with a well-determined width at index 2.
    popt = np.array([1.0, 0.0, 5.0, 0.0])          # amp, mu, SIGMA, baseline
    pcov = np.diag([1e-6, 1e-6, 1e-6, 1e-6])

    told = coloc._relative_sigma_error(popt, pcov, width_indices=[2])
    assert told < 0.5, (
        f"a well-determined width was called meaningless (relative error {told}). The helper must "
        f"look at the index it is TOLD, not one it guesses from len(popt)."
    )

    guessed = coloc._relative_sigma_error(popt, pcov)      # no index -> it guesses
    assert not np.isfinite(guessed) or guessed > told, (
        "the guessing path must not silently agree — it reads index 3, which is the BASELINE"
    )
