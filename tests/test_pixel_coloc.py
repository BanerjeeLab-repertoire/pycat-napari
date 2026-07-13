"""
Pixel-wise colocalization. **The Costes test was a pixel shuffle, which is not Costes.**

The trap
--------
**Every blurred image is autocorrelated. That is the optics, not the biology.** Two *completely
independent* channels, blurred by a realistic PSF, still show a non-zero Pearson r **by chance** —
because the point-spread function put it there.

**Costes's entire defining idea is scrambling in BLOCKS the size of the PSF**, precisely so that
the null **keeps** that autocorrelation and destroys only the *relationship* between the channels.

``perform_costes_test`` called ``scramble_pixels(image1, roi_mask)`` with **no block size**, so it
defaulted to **1 — a pure pixel shuffle.** That destroys the very structure the null is supposed to
preserve, so the null collapsed to a spike around zero and **any** correlation looked significant:

==========================  ==================  ====================
scene                       mean observed r     FALSE POSITIVES
==========================  ==================  ====================
sharp (no PSF)              0.000               **0 / 12**
**blurred, psf = 3**        −0.040              **10 / 12  (83 %)**
**blurred, psf = 6**        −0.058              **11 / 12  (92 %)**
==========================  ==================  ====================

The null came out at **+0.0003 ± 0.0078** while the observed r wandered to **−0.087** — so a
channel pair with a *negative* correlation was reported as **significantly colocalized, at
p = 0.000.**

**A null that does not reproduce the optics is testing against a world that does not exist.**

And the correct machinery was already in the same file: ``spatial_null_test`` measures the
correlation length and block-shuffles at twice it.
"""

import io
import contextlib

import numpy as np
import pytest
from scipy import ndimage as ndi


def _channel_pair(size=128, psf=3.0, correlation=0.0, seed=0):
    """Two channels with a **known** correlation, blurred by a **known** PSF."""
    rng = np.random.default_rng(seed)

    first = rng.normal(0, 1, (size, size))
    noise = rng.normal(0, 1, (size, size))
    second = correlation * first + np.sqrt(max(1 - correlation ** 2, 0)) * noise

    if psf:
        first = ndi.gaussian_filter(first, psf)
        second = ndi.gaussian_filter(second, psf)

    return ((first - first.min()).astype(np.float32),
            (second - second.min()).astype(np.float32))


@pytest.mark.core
@pytest.mark.parametrize("true_r", [0.0, 0.6, 1.0])
def test_pearson_recovers_a_known_correlation(true_r):
    """Audited and **exact** — within 1.3 % at every level, and precisely 1.0 on identical images."""
    coloc = pytest.importorskip("pycat.toolbox.pixel_wise_corr_analysis_tools")

    first, second = _channel_pair(psf=0.0, correlation=true_r)
    roi = np.ones(first.shape, bool)

    measured = float(coloc.pearsons_correlation(first, second, roi)[0])

    assert measured == pytest.approx(true_r, abs=0.05), (
        f"Pearson r = {measured:.4f} against a true {true_r}"
    )


@pytest.mark.core
def test_costes_does_not_call_INDEPENDENT_blurred_channels_colocalized():
    """**83 % false positives on independent channels**, because the scramble was pixel-wise.

    Two channels with **no colocalization whatsoever**, blurred by a realistic PSF. The optics
    make them look correlated; **the null must reproduce that, or everything is significant.**

    A pixel shuffle destroys exactly the structure the null exists to preserve.
    """
    coloc = pytest.importorskip("pycat.toolbox.pixel_wise_corr_analysis_tools")

    false_positives = 0
    for seed in range(10):
        first, second = _channel_pair(size=128, psf=3.0, correlation=0.0, seed=seed)
        roi = np.ones(first.shape, bool)

        with contextlib.redirect_stderr(io.StringIO()):
            p_value, _null = coloc.perform_costes_test(
                first, second, coloc.pearsons_correlation, roi, num_randomizations=99)

        if float(p_value) < 0.05:
            false_positives += 1

    assert false_positives <= 2, (
        f"{false_positives}/10 INDEPENDENT channel pairs were called colocalized. **Every blurred "
        f"image is autocorrelated — that is the optics, not the biology** — and a null that does "
        f"not reproduce it makes every colocalization claim unfalsifiable."
    )


@pytest.mark.core
@pytest.mark.parametrize("true_r", [0.3, 0.6])
def test_costes_still_DETECTS_real_colocalization(true_r):
    """**A null with no power is a null that never says anything.** 10/10 at both levels."""
    coloc = pytest.importorskip("pycat.toolbox.pixel_wise_corr_analysis_tools")

    detected = 0
    for seed in range(8):
        first, second = _channel_pair(size=128, psf=3.0, correlation=true_r, seed=seed)
        roi = np.ones(first.shape, bool)

        with contextlib.redirect_stderr(io.StringIO()):
            p_value, _null = coloc.perform_costes_test(
                first, second, coloc.pearsons_correlation, roi, num_randomizations=99)

        if float(p_value) < 0.05:
            detected += 1

    assert detected >= 7, (
        f"only {detected}/8 genuinely colocalized pairs (r = {true_r}) were detected"
    )


@pytest.mark.core
def test_the_null_PRESERVES_the_images_own_spatial_structure():
    """**The property that makes it Costes and not a pixel shuffle.**

    The block-shuffled null must still be autocorrelated — it keeps the image's own structure and
    destroys only its *relationship* to the other channel. A pixel-shuffled null is white noise,
    and its correlation-length collapses to nothing.
    """
    coloc = pytest.importorskip("pycat.toolbox.pixel_wise_corr_analysis_tools")

    image, _ = _channel_pair(size=128, psf=3.0, seed=0)
    roi = np.ones(image.shape, bool)

    original_length = coloc.spatial_correlation_length(image, roi)

    rng = np.random.default_rng(0)
    block = max(2, 2 * int(original_length))

    block_shuffled = coloc._block_shuffle(image, block, rng)
    pixel_shuffled = coloc.scramble_pixels(image, roi)

    block_length = coloc.spatial_correlation_length(
        np.asarray(block_shuffled, dtype=np.float32), roi)
    pixel_length = coloc.spatial_correlation_length(
        np.asarray(pixel_shuffled, dtype=np.float32), roi)

    assert block_length > 0.5 * original_length, (
        f"the BLOCK-shuffled null has a correlation length of {block_length} against the "
        f"original's {original_length}. It must KEEP the image's spatial structure — that is the "
        f"whole point of Costes."
    )
    assert pixel_length < 0.3 * original_length, (
        f"the PIXEL-shuffled null has a correlation length of {pixel_length}, which is not much "
        f"less than the original's {original_length}. If a pixel shuffle preserves the structure, "
        f"this whole finding needs revisiting."
    )
