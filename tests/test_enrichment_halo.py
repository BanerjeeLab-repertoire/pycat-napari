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


# ── Byte-identical characterization of field_summary (populated + empty branches) ─────────────────
#
# Pins the exact whole-field summary dict on a deterministic scene, so a phase-split of field_summary
# (extracting the non-empty metrics into a helper) can be proven to move no number. Pure numpy/skimage,
# so the golden values are platform-portable. Both branches: four droplets, and an empty field (n == 0).

@pytest.mark.core
def test_field_summary_is_byte_identical():
    invitro = pytest.importorskip("pycat.toolbox.invitro_tools")
    img, dense, _cell, _ped = _scene()

    m = invitro.field_summary(dense, img, 0.1)
    assert m['n_droplets'] == 4
    for k, v in {
        'bulk_intensity': 626.3103004823804,
        'dense_dilute_contrast': 2560.0314667126086,
        'dilute_phase_intensity': 626.3103004823804,
        'field_area_um2': 400.00000000000006,
        'intensity_ratio': 5.087481021373732,
        'mean_droplet_intensity': 3186.341767194989,
        'mean_radius_um': 1.2828336258339186,
        'median_radius_um': 1.2828336258339186,
        'number_density_per_um2': 0.009999999999999998,
        'partition_coefficient': 5.087481021373732,
        'projected_area_fraction': 0.0517,
        'std_radius_um': 0.0,
        'total_droplet_area_um2': 20.680000000000003,
        'volume_fraction': 0.0517,
    }.items():
        assert np.isclose(m[k], v, atol=1e-9), f"{k}: {m[k]!r} != {v!r}"

    # EMPTY field — the n == 0 branch. It deliberately omits intensity_ratio and dense_dilute_contrast.
    e = invitro.field_summary(np.zeros((200, 200), np.int32), img, 0.1)
    assert e['n_droplets'] == 0
    assert np.isnan(e['mean_droplet_intensity']) and np.isnan(e['partition_coefficient'])
    assert np.isclose(e['dilute_phase_intensity'], 758.6639273114222, atol=1e-9)
    assert np.isclose(e['bulk_intensity'], 758.6639273114222, atol=1e-9)
    assert e['projected_area_fraction'] == 0.0 and e['volume_fraction'] == 0.0
    assert e['total_droplet_area_um2'] == 0.0
    assert 'intensity_ratio' not in e and 'dense_dilute_contrast' not in e
