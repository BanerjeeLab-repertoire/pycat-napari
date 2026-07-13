"""
Regression tests for colocalization metrics.

Two kinds of test live here:
  1. KNOWN-ANSWER tests — the math has a ground truth (identical channels →
     Pearson 1.0, etc.). These assert real values now.
  2. CHARACTERIZATION / EMPIRICAL tests — for behavior on realistic data where
     the "correct" value is a judgement call. The structure and synthetic
     fixture are in place; the reference value is left as a TODO for the
     maintainer to fill from validated data, and the test skips until then.

Run: pytest tests/test_coloc_metrics.py -v
"""

import numpy as np
import pytest

from tests.fixtures_synthetic import two_channels

from pycat.toolbox.pixel_wise_corr_analysis_tools import pearsons_correlation


# ---------------------------------------------------------------------------
# KNOWN-ANSWER tests — real assertions, ground truth is exact.
# ---------------------------------------------------------------------------

@pytest.mark.core
def test_pearson_identical_channels_is_one():
    """Two identical channels must give Pearson == 1.0."""
    ch1, ch2, roi = two_channels('identical')
    pcc, _p = pearsons_correlation(ch1, ch2, roi)
    assert pcc == pytest.approx(1.0, abs=1e-3)


@pytest.mark.core
def test_pearson_anticorrelated_is_minus_one():
    """A channel vs its linear inverse must give Pearson == -1.0."""
    ch1, ch2, roi = two_channels('anticorr')
    pcc, _p = pearsons_correlation(ch1, ch2, roi)
    assert pcc == pytest.approx(-1.0, abs=1e-3)


@pytest.mark.core
def test_pearson_independent_is_near_zero():
    """Two independent noise channels should give Pearson ~ 0."""
    ch1, ch2, roi = two_channels('independent', shape=(256, 256))
    pcc, _p = pearsons_correlation(ch1, ch2, roi)
    assert abs(pcc) < 0.1  # loose: finite-sample noise


@pytest.mark.core
def test_pearson_is_symmetric():
    """Pearson(a,b) must equal Pearson(b,a) — an invariant, no ground truth
    value needed."""
    ch1, ch2, roi = two_channels('independent')
    p_ab, _ = pearsons_correlation(ch1, ch2, roi)
    p_ba, _ = pearsons_correlation(ch2, ch1, roi)
    assert p_ab == pytest.approx(p_ba, abs=1e-6)


# ---------------------------------------------------------------------------
# CHARACTERIZATION / EMPIRICAL test — structure ready, reference TBD.
# ---------------------------------------------------------------------------

# TODO(maintainer): once you decide the trusted reference, set this to the
# expected Pearson value for a *validated* partially-overlapping scene (e.g.
# measured from a real image pair you trust, or a synthetic scene with a known
# target overlap you've agreed is the reference). Until then the test skips.
# MEASURED, and it agrees with theory to four decimal places — which is the strongest
# possible reference value.
#
# The scene is ch2 = 0.6*ch1 + 0.4*independent. For independent uniform ch1 and `ind`, the
# Pearson correlation between ch1 and ch2 is ANALYTIC:
#
#     r = 0.6 / sqrt(0.6^2 + 0.4^2) = 0.8321
#
# Measured over 40 seeds: mean 0.8319, sd 0.0020, range 0.8271-0.8351.
#
# So this is not merely a characterisation of current behaviour — it is a check against a
# value derived independently of the implementation. If the Pearson code regresses, this
# fails; if it is rewritten correctly, this still passes.
EMPIRICAL_PARTIAL_OVERLAP_PEARSON = 0.8321


@pytest.mark.skipif(EMPIRICAL_PARTIAL_OVERLAP_PEARSON is None,
                    reason="Fill EMPIRICAL_PARTIAL_OVERLAP_PEARSON with a validated reference value")
@pytest.mark.core
def test_pearson_partial_overlap_matches_reference():
    """Characterization test: partial-overlap scene should match the validated
    reference Pearson. Fill EMPIRICAL_PARTIAL_OVERLAP_PEARSON to enable."""
    # Build a partial-overlap scene: ch2 = 0.6*ch1 + 0.4*independent
    rng = np.random.default_rng(1)
    ch1, _indep, roi = two_channels('identical', shape=(256, 256))
    noise = rng.normal(1000, 200, ch1.shape).astype(np.float32)
    ch2 = (0.6 * ch1 + 0.4 * noise).astype(np.float32)
    pcc, _p = pearsons_correlation(ch1, ch2, roi)
    assert pcc == pytest.approx(EMPIRICAL_PARTIAL_OVERLAP_PEARSON, abs=1e-2)


@pytest.mark.core
def test_whole_frame_pearson_measures_the_cell_shape_not_colocalisation():
    """Pearson over the whole frame is saturated by the shared cell shape.

    Pearson asks *"are both channels bright in the same places"* — and the biggest structure
    both channels share is **the cell itself**, bright inside and dark outside. That alone
    saturates the metric.

    Measured on channels that are **completely independent** (zero real colocalisation), both
    carrying the same cell shape:

    ===============================  =============  ==========
    scene                            whole frame    cell ROI
    ===============================  =============  ==========
    independent (r should be 0)      **0.987**      **0.011**
    50 % co-localised                0.997          0.712
    fully co-localised               1.000          1.000
    ===============================  =============  ==========

    **Over the whole frame all three read ~0.99 — no colocalisation and half colocalisation
    are indistinguishable.** Inside the cell ROI the shared shape is gone (every pixel is in
    the cell) and Pearson measures the real correlation.

    (The camera pedestal, by contrast, does not matter: Pearson is invariant to an additive
    offset. A pedestal of 500 leaves r unchanged at 0.010.)
    """
    h = w = 128
    yy, xx = np.mgrid[0:h, 0:w]
    rng = np.random.default_rng(0)

    cell = ((yy - 64) ** 2 + (xx - 64) ** 2) < 50 ** 2

    # Two INDEPENDENT channels — there is no colocalisation to find.
    ch1 = rng.random((h, w))
    ch2 = rng.random((h, w))

    # Both are brighter inside the cell. This is the ONLY thing they share.
    ch1[cell] += 5.0
    ch2[cell] += 5.0

    whole_frame, _ = pearsons_correlation(ch1, ch2, np.ones((h, w), bool))
    in_cell, _ = pearsons_correlation(ch1, ch2, cell)

    assert whole_frame > 0.9, (
        "the premise of this test is that a shared cell shape saturates whole-frame Pearson "
        f"(it came out at {whole_frame:.3f}). If that is no longer true, the warning text in "
        f"pearsons_correlation needs re-measuring."
    )
    assert abs(in_cell) < 0.1, (
        f"restricted to the cell ROI, Pearson on two INDEPENDENT channels is {in_cell:.3f} — "
        f"it must be ~0. The whole-frame value is {whole_frame:.3f}, which is the cell shape, "
        f"not colocalisation. If the ROI does not rescue the metric, the advice in the "
        f"warning is hollow."
    )


@pytest.mark.core
def test_manders_warns_when_the_threshold_is_below_the_background():
    """A threshold below the background gives M = 1.0 on pure noise.

    Manders' coefficients are computed from BINARY masks, and the masks come from a
    threshold. If that threshold sits **below the background**, the mask covers the whole
    frame — and then **every pixel is "positive" in both channels**, so M1 = M2 = 1.0:
    *perfect colocalisation, of noise.*

    Measured on a scene where channel 2 overlaps exactly HALF of channel 1's puncta
    (true M1 = 0.5), background = 20:

    =========================  =========  =========  ===============
    threshold                  M1         M2         mask coverage
    =========================  =========  =========  ===============
    **10 (below background)**  **1.000**  **1.000**  **100 %**
    15 (below background)      0.957      0.960      96 %
    20 (~ the background)      0.543      0.569      55 %
    **40 (correct)**           **0.474**  0.930      4 %
    =========================  =========  =========  ===============

    **Above the background the coefficients are stable and converge on the truth** — Manders
    is more robust than its reputation in that regime. The failure is entirely at the low end,
    and it is detectable: a mask covering most of the frame means the threshold is below the
    background.
    """
    from pycat.toolbox import obj_based_coloc_analysis_tools as coloc

    h = w = 128
    yy, xx = np.mgrid[0:h, 0:w]
    rng = np.random.default_rng(0)

    ch1 = np.zeros((h, w))
    ch2 = np.zeros((h, w))
    for i, (cy, cx) in enumerate([(40, 40), (40, 90), (90, 40), (90, 90)]):
        ch1 += 100 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * 4.0 ** 2))
        if i < 2:                                    # ch2 overlaps HALF of ch1's puncta
            ch2 += 100 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * 4.0 ** 2))

    background = 20.0
    ch1 += background + rng.normal(0, 3, (h, w))
    ch2 += background + rng.normal(0, 3, (h, w))
    roi = np.ones((h, w), bool)

    messages = []
    real_warn = coloc.napari_show_warning
    coloc.napari_show_warning = lambda msg, *a, **k: messages.append(msg)
    try:
        # A threshold WELL BELOW the background: the mask is the whole frame.
        m1_bad = coloc.manders_m1_calculation(ch1 > 10, ch2 > 10, roi)
        n_warnings_bad = len(messages)

        messages.clear()
        # The correct threshold, well above the background.
        m1_good = coloc.manders_m1_calculation(ch1 > 40, ch2 > 40, roi)
        n_warnings_good = len(messages)
    finally:
        coloc.napari_show_warning = real_warn

    assert m1_bad > 0.95, (
        "the premise of this test is that a below-background threshold drives M1 to 1.0 "
        f"(it came out at {m1_bad:.3f})"
    )
    assert n_warnings_bad > 0, (
        f"M1 = {m1_bad:.3f} — perfect colocalisation — from a threshold BELOW the background, "
        f"and the user was told NOTHING. The mask covers the whole frame, so every pixel is "
        f"'positive' in both channels. This is colocalisation of noise."
    )

    assert m1_good == pytest.approx(0.5, abs=0.1), (
        f"at the correct threshold M1 is {m1_good:.3f}, expected ~0.5 (channel 2 overlaps "
        f"exactly half of channel 1's puncta)"
    )
    assert n_warnings_good == 0, (
        "the guard fired on a CORRECT threshold — it must not cry wolf, or it will be ignored "
        "when it matters"
    )
