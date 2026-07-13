"""
Regression tests for the client partition / enrichment coefficient.

KNOWN-ANSWER: with no camera offset, K = dense_mean / dilute_mean exactly, so a
synthetic two-phase scene with a known ratio must return that ratio.

Run: pytest tests/test_partition.py -v
"""

import numpy as np
import pytest

from tests.fixtures_synthetic import partition_scene

from pycat.toolbox.partition_enrichment_tools import client_enrichment


@pytest.mark.core
def test_partition_known_ratio_no_background():
    """K_true = dense/dilute with background=0 must be recovered exactly."""
    k_true = 5.0
    img, dense, cell = partition_scene(k_true=k_true, dilute_val=100.0)
    res = client_enrichment(img, dense, cell_mask=cell, background=0.0)
    assert res['enrichment'] == pytest.approx(k_true, rel=1e-3)


@pytest.mark.core
def test_partition_unity_when_uniform():
    """A uniform image (dense == dilute intensity) must give K == 1.0."""
    img, dense, cell = partition_scene(k_true=1.0, dense_val=100.0, dilute_val=100.0)
    res = client_enrichment(img, dense, cell_mask=cell, background=0.0)
    assert res['enrichment'] == pytest.approx(1.0, rel=1e-3)


@pytest.mark.core
def test_partition_background_subtraction_effect():
    """Invariant / sanity: subtracting a positive camera offset increases the
    apparent K (moves the ratio away from 1), per K=(dense-bg)/(dilute-bg)."""
    img, dense, cell = partition_scene(k_true=3.0, dilute_val=100.0)  # dense=300
    k_no_bg = client_enrichment(img, dense, cell_mask=cell, background=0.0)['enrichment']
    k_with_bg = client_enrichment(img, dense, cell_mask=cell, background=50.0)['enrichment']
    # (300-50)/(100-50) = 5.0  > 3.0
    assert k_with_bg > k_no_bg
    assert k_with_bg == pytest.approx((300 - 50) / (100 - 50), rel=1e-3)


@pytest.mark.core
def test_partition_non_negative():
    """Invariant: enrichment of a real positive-intensity scene is non-negative."""
    img, dense, cell = partition_scene(k_true=2.0)
    res = client_enrichment(img, dense, cell_mask=cell, background=0.0)
    assert res['enrichment'] >= 0.0


@pytest.mark.core
def test_over_inclusive_droplet_mask_is_detected():
    """A mask that spills past the droplet edge collapses Kp — silently.

    Kp = I_dense / I_dilute. If the mask spills past the droplet, it pulls **dilute-phase
    pixels into the "dense" average**, so I_dense falls and Kp falls with it.

    Measured on a scene with a **true Kp of 30** (true droplet radius 13 px):

    ================  =============  ====================
    mask radius       Kp reported    CV inside the mask
    ================  =============  ====================
    13 px (true)      **29.61**      0.016
    20 px             19.93          0.421
    30 px             9.46           0.807
    50 px             **4.41**       0.902
    ================  =============  ====================

    **A 7× collapse** — and the function reported *"Kp is pedestal-independent, validated"*
    the whole way down. The message was reassuring while the number was wrong.

    It is detectable **from the data alone**: a clean dense mask has a LOW coefficient of
    variation, because every pixel in it is dense phase. An over-inclusive mask mixes in
    dilute pixels and the CV rises — 0.016 to 0.807, a 50-fold separation, monotonic in the
    error.

    The confident "validated" message is also **suppressed** when the mask is suspect. A
    reassurance printed alongside a warning is worse than no reassurance: the user reads the
    one that agrees with them.
    """
    from pycat.toolbox import invitro_tools as it

    h = w = 200
    yy, xx = np.mgrid[0:h, 0:w]
    rng = np.random.default_rng(0)

    pedestal, dilute, dense = 500.0, 100.0, 3000.0
    centres = [(60, 60), (60, 140), (140, 60), (140, 140)]

    img = np.full((h, w), pedestal + dilute)
    for cy, cx in centres:
        r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        img += (dense - dilute) * 0.5 * (1 - np.tanh((r - 16) / 2.5))
    img = img + rng.normal(0, 5, (h, w))
    dark = pedestal + rng.normal(0, 5, (h, w))

    def _labels(radius):
        lab = np.zeros((h, w), np.int32)
        for i, (cy, cx) in enumerate(centres, start=1):
            lab[np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2) < radius] = i
        return lab

    warnings_seen = []
    real_warn = it.napari_show_warning
    real_info = it.napari_show_info
    it.napari_show_warning = lambda msg, *a, **k: warnings_seen.append(msg)
    it.napari_show_info = lambda msg, *a, **k: None
    try:
        good = it.partition_coefficient_local(img, _labels(13), sample_type="in_vitro",
                                              dark_reference=dark)
        n_warnings_good = sum("OVER-INCLUSIVE" in m for m in warnings_seen)

        warnings_seen.clear()
        bad = it.partition_coefficient_local(img, _labels(30), sample_type="in_vitro",
                                             dark_reference=dark)
        n_warnings_bad = sum("OVER-INCLUSIVE" in m for m in warnings_seen)
    finally:
        it.napari_show_warning = real_warn
        it.napari_show_info = real_info

    assert good["partition_coefficient"] == pytest.approx(30.0, rel=0.1)
    assert n_warnings_good == 0, (
        "the guard fired on a CORRECT mask — it must not cry wolf, or it will be ignored"
    )

    assert bad["partition_coefficient"] < 15.0, (
        "the premise of this test is that an over-inclusive mask collapses Kp "
        f"(it came out at {bad['partition_coefficient']:.2f} against a true 30)"
    )
    assert n_warnings_bad > 0, (
        f"Kp collapsed from 29.6 to {bad['partition_coefficient']:.2f} — a 3x error — because "
        f"the mask was 2.3x too large, and the user was told NOTHING. The mask IS the "
        f"measurement, and an over-inclusive one is detectable from the CV of the intensity "
        f"inside it (0.016 for a clean mask, 0.807 for this one)."
    )
