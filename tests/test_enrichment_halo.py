"""
The dilute reference must be OFFSET from the dense mask — the adjacent pixels are the PSF halo.

A droplet edge is not sharp: the PSF gives it a halo, and the pixels **immediately outside** the
dense mask are *halo, not dilute phase*. Including them inflates the dilute reference and
collapses the enrichment.

Measured, **true enrichment = 30**:

============  =============  ==============
edge width    dilute_mean    enrichment
============  =============  ==============
sharp         100.0          **30.00**
1 px          113.0          25.54
2.5 px        130.0          20.66
5 px          163.1          **14.86**
============  =============  ==============

**A realistic PSF halves the enrichment**, and every real droplet has one.

``dilute_dilation_px`` was meant to help and made it **worse**: it built the shell **immediately
adjacent** to the dense mask — *which is the halo itself*, the worst possible choice. With a
2.5 px edge it took the answer from 20.66 down to **2.86**.

============================  =============  ==============
dilute region                 dilute_mean    enrichment
============================  =============  ==============
adjacent shell (the old way)  1440.5         **2.86**
gap 5 px, shell 6 px          621.5          22.10
**gap 10 px, shell 6 px**     600.9          **26.63**
============================  =============  ==============

The fix is the annulus **gap** already used by ``partition_coefficient_local`` (1.5.423): step
away from the mask before sampling.
"""

import numpy as np
import pytest


def _scene(edge_width_px=2.5):
    """Four droplets with a realistic soft edge, in a cell, on a camera pedestal."""
    h = w = 200
    yy, xx = np.mgrid[0:h, 0:w]
    rng = np.random.default_rng(0)

    pedestal, dilute, dense_level = 500.0, 100.0, 3000.0

    img = np.full((h, w), pedestal + dilute)
    cell = np.zeros((h, w), bool)
    dense = np.zeros((h, w), np.int32)

    for i, (cy, cx) in enumerate([(60, 60), (60, 140), (140, 60), (140, 140)], start=1):
        r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        img += (dense_level - dilute) * 0.5 * (1 - np.tanh((r - 13) / edge_width_px))
        cell |= r < 60
        dense[r < 13] = i

    return img + rng.normal(0, 5, (h, w)), dense, cell.astype(np.int32), pedestal


@pytest.mark.core
def test_gapped_dilute_shell_recovers_the_enrichment():
    """A gap clears the halo; the adjacent shell IS the halo."""
    pe = pytest.importorskip("pycat.toolbox.partition_enrichment_tools")

    img, dense, cell, pedestal = _scene()

    no_gap = pe.client_enrichment(img, dense, cell, background=pedestal)
    gapped = pe.client_enrichment(img, dense, cell, background=pedestal, dilute_gap_px=10)

    assert gapped["enrichment"] == pytest.approx(30.0, rel=0.15), (
        f"with a 10 px gap the enrichment is {gapped['enrichment']:.2f}, expected ~30. The "
        f"gap must step past the PSF halo — without it the dilute reference is inflated by "
        f"halo pixels and the enrichment collapses (it reads "
        f"{no_gap['enrichment']:.2f} here)."
    )
    assert gapped["enrichment"] > no_gap["enrichment"], (
        "the gapped shell must be BETTER than sampling everything outside the mask"
    )


@pytest.mark.core
def test_adjacent_dilute_shell_is_the_halo_and_warns():
    """`dilute_dilation_px` with no gap selects the halo — the worst possible choice."""
    pe = pytest.importorskip("pycat.toolbox.partition_enrichment_tools")

    img, dense, cell, pedestal = _scene()

    messages = []
    real_warn = pe.napari_show_warning
    pe.napari_show_warning = lambda msg, *a, **k: messages.append(msg)
    try:
        adjacent = pe.client_enrichment(img, dense, cell, background=pedestal,
                                        dilute_dilation_px=3)
    finally:
        pe.napari_show_warning = real_warn

    assert adjacent["enrichment"] < 10.0, (
        "the premise of this test is that an ADJACENT shell selects the halo and collapses "
        f"the enrichment (it came out at {adjacent['enrichment']:.2f} against a true 30)"
    )
    assert messages, (
        f"`dilute_dilation_px` built the dilute shell immediately adjacent to the dense mask "
        f"— which is the PSF HALO — and the enrichment collapsed to "
        f"{adjacent['enrichment']:.2f} against a true 30. The user was told nothing."
    )


@pytest.mark.core
def test_contrast_is_pedestal_exact_but_not_halo_immune():
    """The contrast cancels the pedestal — and the PSF halo still corrupts it.

    1.5.426 introduced ``dense_dilute_contrast`` and described it as *"exact — the pedestal
    cancels in the difference"*. **The first half is right and the second is a blanket
    reassurance that does not hold.**

    The pedestal does cancel. But the contrast is **not immune to the halo**, which corrupts
    *both* terms — the dense mean is pulled DOWN by soft edge pixels inside the mask, and the
    dilute mean is pulled UP by halo pixels outside it. Measured, **true contrast = 2900**:

    ==============  ==========  ==========
    droplet edge    contrast    error
    ==============  ==========  ==========
    sharp           2898        −0 %
    1 px            2773        −4 %
    2.5 px          2560        **−12 %**
    5 px            2269        **−22 %**
    ==============  ==========  ==========

    Exact against the *pedestal*; degraded by the *halo* like everything else. This test
    pins both halves, so the claim cannot drift back to the reassuring version.
    """
    invitro = pytest.importorskip("pycat.toolbox.invitro_tools")

    true_contrast = 2900.0                       # dense 3000 − dilute 100

    def _contrast(edge_width):
        img, dense, _cell, _pedestal = _scene(edge_width_px=edge_width)
        summary = invitro.field_summary(dense, img, 0.1)
        return summary["dense_dilute_contrast"]

    sharp = _contrast(0.01)
    blurred = _contrast(5.0)

    # The pedestal DOES cancel — a sharp edge recovers the contrast exactly, on an image
    # sitting on a 500-count pedestal.
    assert sharp == pytest.approx(true_contrast, rel=0.02), (
        f"with a sharp edge the contrast is {sharp:.0f}, expected {true_contrast:.0f}. The "
        f"pedestal (500 counts) must cancel in the difference — if it does not, the whole "
        f"rationale for reporting a contrast is gone."
    )

    # But the HALO does not cancel.
    assert blurred < 0.9 * true_contrast, (
        f"with a 5 px PSF edge the contrast is {blurred:.0f} against a true "
        f"{true_contrast:.0f} — it must be visibly degraded. If this passes, the code is "
        f"free to go back to calling the contrast 'exact' without qualification, and that "
        f"reassurance is what this test exists to prevent."
    )
