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

def test_pearson_identical_channels_is_one():
    """Two identical channels must give Pearson == 1.0."""
    ch1, ch2, roi = two_channels('identical')
    pcc, _p = pearsons_correlation(ch1, ch2, roi)
    assert pcc == pytest.approx(1.0, abs=1e-3)


def test_pearson_anticorrelated_is_minus_one():
    """A channel vs its linear inverse must give Pearson == -1.0."""
    ch1, ch2, roi = two_channels('anticorr')
    pcc, _p = pearsons_correlation(ch1, ch2, roi)
    assert pcc == pytest.approx(-1.0, abs=1e-3)


def test_pearson_independent_is_near_zero():
    """Two independent noise channels should give Pearson ~ 0."""
    ch1, ch2, roi = two_channels('independent', shape=(256, 256))
    pcc, _p = pearsons_correlation(ch1, ch2, roi)
    assert abs(pcc) < 0.1  # loose: finite-sample noise


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
EMPIRICAL_PARTIAL_OVERLAP_PEARSON = None


@pytest.mark.skipif(EMPIRICAL_PARTIAL_OVERLAP_PEARSON is None,
                    reason="Fill EMPIRICAL_PARTIAL_OVERLAP_PEARSON with a validated reference value")
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
