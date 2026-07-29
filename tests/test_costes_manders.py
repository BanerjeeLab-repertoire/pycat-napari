"""
**Costes M1/M2 never looked at the other channel — they were not colocalization coefficients.**

The Manders thresholded coefficients cross-reference the channels: M1 is the fraction of channel-1
intensity in pixels where channel *2* is above its threshold, and M2 the mirror. The old dispatch
computed ``sum(image1[image1 > thresh1]) / sum(image1)`` -- self-referential -- which is ~1 for any
threshold at the noise floor regardless of whether the channels colocalize at all. Two completely
disjoint channels reported M1 ~= M2 ~= 1 (false colocalization).

The fix (shipped with the corrected Costes threshold search, A4) computes the true cross-referenced,
ROI-masked coefficients. These tests drive the real dispatch (`pixel_wise_correlation_analysis`).
"""
import warnings

import numpy as np
import pytest


def _m1m2(image1, image2, roi_mask=None):
    mod = pytest.importorskip("pycat.toolbox.coloc.analysis")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        table1, _, _ = mod.pixel_wise_correlation_analysis(
            image1, image2, roi_mask, ["Costes Automatic Thresholded M1 & M2"], True, None)
    d = dict(zip(table1["Method"], table1["Coefficient"]))
    return (d["Costes Automatic Thresholded M1 (intensity, auto-threshold)"],
            d["Costes Automatic Thresholded M2 (intensity, auto-threshold)"])


@pytest.mark.base
def test_fully_colocalized_channels_give_M_near_one():
    """Identical support (a bright block on zero background) -> M1, M2 ~= 1."""
    rng = np.random.default_rng(3)
    img = np.zeros((200, 200))
    img[60:140, 60:140] = rng.uniform(1000, 60000, (80, 80))
    m1, m2 = _m1m2(img, img.copy())
    assert 0.9 <= m1 <= 1.0 and 0.9 <= m2 <= 1.0, (m1, m2)


@pytest.mark.base
def test_disjoint_channels_do_not_report_colocalization():
    """Disjoint bright regions -> NOT the old false ~1.

    For non-positively-correlated channels the Costes threshold is honestly undefined (nan); either
    way the coefficient must not be ~1, which is the bug the old self-referential formula produced."""
    rng = np.random.default_rng(3)
    size = 200
    base = rng.uniform(0, 3000, (size, size))
    r = base + rng.normal(0, 100, (size, size))
    g = base + rng.normal(0, 100, (size, size))
    r[30:60, 30:60] = 45000            # bright only in channel 1
    g[140:170, 140:170] = 45000        # bright only in channel 2, disjoint location
    m1, m2 = _m1m2(r, g)
    for v in (m1, m2):
        assert np.isnan(v) or v < 0.3, (m1, m2)      # old code returned ~1 here


@pytest.mark.base
def test_partial_overlap_gives_a_graded_coefficient_in_zero_one():
    """Overlap only in a sub-region -> M1, M2 strictly between 0 and 1 (a real cross-referenced value)."""
    rng = np.random.default_rng(7)
    size = 200
    r = rng.uniform(0, 2000, (size, size))
    g = rng.uniform(0, 2000, (size, size))
    common = rng.uniform(20000, 50000, (size, size))
    A = np.zeros((size, size), bool); A[40:100, 40:160] = True
    C = np.zeros((size, size), bool); C[100:160, 40:160] = True
    overlap = np.zeros((size, size), bool); overlap[80:120, 60:140] = True
    r[A] = common[A]; r[overlap] = common[overlap]
    g[C] = common[C]; g[overlap] = common[overlap]
    m1, m2 = _m1m2(r, g)
    assert 0.02 < m1 < 0.95 and 0.02 < m2 < 0.95, (m1, m2)
