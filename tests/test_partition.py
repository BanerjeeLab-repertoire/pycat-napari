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


@pytest.mark.base
def test_partition_known_ratio_no_background():
    """K_true = dense/dilute with background=0 must be recovered exactly."""
    k_true = 5.0
    img, dense, cell = partition_scene(k_true=k_true, dilute_val=100.0)
    res = client_enrichment(img, dense, cell_mask=cell, background=0.0)
    assert res['enrichment'] == pytest.approx(k_true, rel=1e-3)


@pytest.mark.base
def test_partition_unity_when_uniform():
    """A uniform image (dense == dilute intensity) must give K == 1.0."""
    img, dense, cell = partition_scene(k_true=1.0, dense_val=100.0, dilute_val=100.0)
    res = client_enrichment(img, dense, cell_mask=cell, background=0.0)
    assert res['enrichment'] == pytest.approx(1.0, rel=1e-3)


@pytest.mark.base
def test_partition_background_subtraction_effect():
    """Invariant / sanity: subtracting a positive camera offset increases the
    apparent K (moves the ratio away from 1), per K=(dense-bg)/(dilute-bg)."""
    img, dense, cell = partition_scene(k_true=3.0, dilute_val=100.0)  # dense=300
    k_no_bg = client_enrichment(img, dense, cell_mask=cell, background=0.0)['enrichment']
    k_with_bg = client_enrichment(img, dense, cell_mask=cell, background=50.0)['enrichment']
    # (300-50)/(100-50) = 5.0  > 3.0
    assert k_with_bg > k_no_bg
    assert k_with_bg == pytest.approx((300 - 50) / (100 - 50), rel=1e-3)


@pytest.mark.base
def test_partition_non_negative():
    """Invariant: enrichment of a real positive-intensity scene is non-negative."""
    img, dense, cell = partition_scene(k_true=2.0)
    res = client_enrichment(img, dense, cell_mask=cell, background=0.0)
    assert res['enrichment'] >= 0.0


@pytest.mark.base
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

    # partition_coefficient_local moved to invitro/partition.py (1.6.214); patch the warning at its new
    # home (invitro_tools re-exports the function). Assertions and values are unchanged — the OVER-INCLUSIVE
    # warning still fires; only the monkeypatch target follows the moved symbol.
    from pycat.toolbox.invitro import partition as _part
    warnings_seen = []
    real_warn = _part.napari_show_warning
    real_info = _part.napari_show_info
    _part.napari_show_warning = lambda msg, *a, **k: warnings_seen.append(msg)
    _part.napari_show_info = lambda msg, *a, **k: None
    try:
        good = it.partition_coefficient_local(img, _labels(13), sample_type="in_vitro",
                                              dark_reference=dark)
        n_warnings_good = sum("OVER-INCLUSIVE" in m for m in warnings_seen)

        warnings_seen.clear()
        bad = it.partition_coefficient_local(img, _labels(30), sample_type="in_vitro",
                                             dark_reference=dark)
        n_warnings_bad = sum("OVER-INCLUSIVE" in m for m in warnings_seen)
    finally:
        _part.napari_show_warning = real_warn
        _part.napari_show_info = real_info

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


# ── N6-2: partition_coefficient_field does NOT distort Kp at low dilute intensity ────────────────────────
# `partition_coefficient_field` estimates the dilute phase from a low background percentile, with two floors: it
# falls back to the background MEAN when that percentile is degenerate (<=0 or a tiny fraction of the mean), and a
# final `bulk_div > 1e-6` guard. The audit asked whether those floors distort Kp at low dilute intensity. They do
# NOT for a well-posed (uniform) dilute phase — Kp is recovered exactly across the whole low range; the floors
# only engage on a degenerate right-skewed background, where they trade a ~1e8 explosion for a bounded bias.

def _ivf_field(bulk, dense, H=192, W=192, r=10, skew_zeros=0.0, seed=0):
    """A synthetic in-vitro field: 16 dense droplets on a uniform dilute background of intensity `bulk`.
    `skew_zeros` forces a fraction of background pixels to 0 (a degenerate, right-skewed dark background)."""
    rng = np.random.default_rng(seed)
    img = np.full((H, W), float(bulk))
    if skew_zeros:
        img[rng.random((H, W)) < skew_zeros] = 0.0
    lbl = np.zeros((H, W), int)
    yy, xx = np.ogrid[:H, :W]
    k = 1
    for gy in range(4):
        for gx in range(4):
            cy, cx = 28 + gy * 45, 28 + gx * 45
            disc = (yy - cy) ** 2 + (xx - cx) ** 2 <= r * r
            img[disc] = float(dense)
            lbl[disc] = k
            k += 1
    return img, lbl


@pytest.mark.base
@pytest.mark.parametrize("bulk,true_kp", [(0.3, 2.0), (0.1, 6.0), (0.03, 20.0),
                                          (0.01, 60.0), (0.003, 200.0), (0.001, 600.0)])
def test_partition_field_recovers_Kp_across_low_dilute_intensities(bulk, true_kp):
    """GOLDEN-MASTER (closes N6-2): for a uniform dilute phase, Kp = dense/bulk is recovered EXACTLY even as the
    dilute intensity drops three orders of magnitude and Kp climbs to 600 — the percentile==mean==bulk, so neither
    floor engages. This is the regression guard proving no low-intensity distortion."""
    from pycat.toolbox.invitro.partition import partition_coefficient_field
    img, lbl = _ivf_field(bulk=bulk, dense=0.6)
    res = partition_coefficient_field(img, lbl)
    assert res['partition_coeff'] == pytest.approx(true_kp, rel=1e-2)
    assert res['c_sat_proxy'] == pytest.approx(bulk, rel=1e-2)     # the dilute estimate is the real bulk


@pytest.mark.base
def test_partition_field_mean_fallback_is_bounded_not_an_explosion():
    """CHARACTERISATION: on a degenerate right-skewed background (30% near-zero pixels) the 10th-percentile
    collapses to ~0 and the documented mean-fallback engages. It biases Kp (the mean sits below the true dilute),
    but the point of the floor is that the result stays BOUNDED — not the ~1e8 a divide-by-~0 would give. This
    pins the deliberate trade so a future change does not silently reintroduce the explosion."""
    from pycat.toolbox.invitro.partition import partition_coefficient_field
    img, lbl = _ivf_field(bulk=0.1, dense=0.6, skew_zeros=0.3)
    res = partition_coefficient_field(img, lbl)
    kp = res['partition_coeff']
    assert np.isfinite(kp) and 0 < kp < 100        # bounded — NOT the 1e8 the raw percentile-0 would produce
    assert res['c_sat_proxy'] > 0                  # the floor kept the denominator off zero
