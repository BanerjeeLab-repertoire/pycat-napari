"""
**Every ACF-derived object size was computed from the wrong transform.**

``calculate_autocorrelation`` claimed the Wiener-Khinchin identity (ACF = ifft of the power
spectrum ``|F|^2``) but computed ``np.real(fft2(image))**2`` -- the square of the *real part*,
which throws away the imaginary component and never subtracts the DC term. That is not the power
spectrum: it is phase-blind and background-dominated, so the recovered autocorrelation width -- and
thus every ACF-derived diameter in the spatial-ACF module -- was distorted.

The fix uses the true power spectrum ``F * conj(F)`` on the mean-subtracted image. These tests pin
three properties the fixed code has and the old code did not:

1. **Gaussian-width recovery.** The autocorrelation of a Gaussian blob of width sigma is itself a
   Gaussian of width ``sigma*sqrt(2)``; fitting the central slice must recover it.
2. **Translation invariance.** A true autocorrelation depends only on ``|F|^2``, so a blob at the
   centre and the same blob shifted must give the *same* ACF. The old ``Re(F)^2`` code was
   phase-sensitive and produced different ACFs for the two -- the smoking gun.
3. **Cross-check** against the independent, correct ``spatial_randomness_tools.autocorrelation_length``
   implementation, which already used ``F * conj(F)``.
"""
import numpy as np
import pytest


def _acf():
    m = pytest.importorskip("pycat.toolbox.correlation_func_analysis_tools")
    return m.calculate_autocorrelation


def _gaussian_blob(size, sigma, cx=None, cy=None):
    cx = size // 2 if cx is None else cx
    cy = size // 2 if cy is None else cy
    y, x = np.indices((size, size), dtype=float)
    return np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2.0 * sigma ** 2))


@pytest.mark.core
@pytest.mark.parametrize("sigma", [6.0, 10.0])
def test_acf_of_gaussian_recovers_sqrt2_width(sigma):
    """ACF of a Gaussian(sigma) is a Gaussian(sigma*sqrt(2)); fit the central slice and check."""
    calc = _acf()
    size = 256
    acf = calc(_gaussian_blob(size, sigma))

    # peak sits at the centre after fftshift
    peak = np.unravel_index(np.argmax(acf), acf.shape)
    assert peak == (size // 2, size // 2)

    # fit a Gaussian (with offset) to the central row; expect s ~= sigma*sqrt(2)
    from scipy.optimize import curve_fit
    row = acf[size // 2, :]
    d = np.arange(size, dtype=float) - size // 2

    def _gauss(x, amp, s, off):
        return amp * np.exp(-x ** 2 / (2.0 * s ** 2)) + off

    (amp, s_recovered, off), _ = curve_fit(
        _gauss, d, row, p0=[1.0, sigma * np.sqrt(2.0), 0.0], maxfev=10000)
    s_recovered = abs(s_recovered)
    expected = sigma * np.sqrt(2.0)
    assert abs(s_recovered - expected) / expected < 0.05, (s_recovered, expected)


@pytest.mark.core
def test_acf_is_translation_invariant():
    """The true ACF depends only on |F|^2, so a centred and a shifted blob give the same ACF.

    The old Re(F)^2 code was phase-sensitive and failed this (different ACFs for the two blobs)."""
    calc = _acf()
    size = 200
    centred = calc(_gaussian_blob(size, 8.0, cx=size // 2, cy=size // 2))
    shifted = calc(_gaussian_blob(size, 8.0, cx=size // 2 + 30, cy=size // 2 - 20))
    # both peak at the centre, and the whole ACF matches (autocorrelation is shift-invariant)
    assert np.max(np.abs(centred - shifted)) < 1e-6


@pytest.mark.core
def test_acf_width_matches_reference_implementation():
    """1/e width from calculate_autocorrelation agrees with the independent autocorrelation_length."""
    calc = _acf()
    ref_mod = pytest.importorskip("pycat.toolbox.spatial_randomness_tools")

    rng = np.random.default_rng(0)
    size = 256
    # a clustered field: several Gaussian blobs of a common size
    field = np.zeros((size, size))
    for _ in range(12):
        cx, cy = rng.integers(20, size - 20, size=2)
        field += _gaussian_blob(size, 7.0, cx=cx, cy=cy)

    acf = calc(field)
    # radial 1/e width of our ACF
    cy0, cx0 = size // 2, size // 2
    y, x = np.indices(acf.shape)
    r = np.sqrt((y - cy0) ** 2 + (x - cx0) ** 2).astype(int)
    prof = np.bincount(r.ravel(), acf.ravel()) / np.maximum(np.bincount(r.ravel()), 1)
    prof = prof / prof[0]
    below = np.where(prof <= 1.0 / np.e)[0]
    our_width = float(below[0]) if below.size else np.nan

    ref_width = ref_mod.autocorrelation_length(field)
    assert np.isfinite(our_width) and np.isfinite(ref_width)
    assert abs(our_width - ref_width) / ref_width < 0.30, (our_width, ref_width)
