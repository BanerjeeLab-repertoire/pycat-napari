"""**Ratiometric analysis — the traps ARE the science, so the tests are about the traps.**

A ratio image is trivial to compute and easy to get wrong. These pin the handling that makes it correct:
background-subtracted-first recovery of a known ratio, the pedestal test (an un-subtracted offset bends
the ratio; subtracting it recovers the truth — the whole reason background-first is mandatory), low-
denominator pixels becoming NaN (not spikes) with the excluded fraction reported, mean-of-ratio vs
ratio-of-means differing on a heterogeneous object and agreeing on a uniform one, and bleed-through
biasing the ratio toward 1 until a coefficient corrects it.
"""
import numpy as np
import pandas as pd
import pytest

from pycat.toolbox.ratiometric_tools import ratio_image, object_ratios, RatioResult

pytestmark = pytest.mark.base


def test_a_known_ratio_is_recovered_after_background_subtraction():
    rng = np.random.default_rng(0)
    d = rng.uniform(100, 200, (64, 64))
    n = 3.0 * d                                    # true ratio 3.0, no offsets
    res = ratio_image(n, d)
    assert isinstance(res, RatioResult)
    assert np.nanmean(res.ratio) == pytest.approx(3.0, rel=1e-9)
    assert res.fraction_thresholded == 0.0


def test_the_PEDESTAL_test_unsubtracted_offset_bends_the_ratio():
    """The whole point of background-first: a pedestal on the numerator bends the ratio; subtracting it
    recovers the truth."""
    d = np.full((32, 32), 100.0)
    n = 3.0 * d                                    # true ratio 3.0
    n_ped = n + 500.0                              # a 500-count pedestal on the numerator

    bent = ratio_image(n_ped, d)                   # NOT subtracting it
    assert np.nanmean(bent.ratio) == pytest.approx((300 + 500) / 100.0, rel=1e-9)   # 8.0, badly wrong
    fixed = ratio_image(n_ped, d, background_num=500.0)   # subtract it
    assert np.nanmean(fixed.ratio) == pytest.approx(3.0, rel=1e-9)                    # truth recovered


def test_low_denominator_pixels_become_NaN_and_the_fraction_is_reported():
    n = np.full((10, 10), 5.0)
    d = np.full((10, 10), 5.0)
    d[:3, :] = 0.05                                # 30 of 100 pixels have a near-zero denominator
    res = ratio_image(n, d, threshold=1.0)
    assert np.isnan(res.ratio[:3, :]).all(), "near-zero-denominator pixels must be NaN, not spikes"
    assert not np.isnan(res.ratio[3:, :]).any()
    assert res.fraction_thresholded == pytest.approx(0.30, rel=1e-9)


def test_a_nonpositive_denominator_is_never_divided_even_with_no_threshold():
    n = np.full((4, 4), 1.0)
    d = np.array([[2.0, 0.0, -1.0, 2.0]] * 4)
    res = ratio_image(n, d)                        # default threshold: D must be > 0
    assert np.isnan(res.ratio[:, 1]).all() and np.isnan(res.ratio[:, 2]).all()
    assert not np.isnan(res.ratio[:, 0]).any()


def test_mean_of_ratio_and_ratio_of_means_AGREE_on_a_uniform_object():
    labels = np.ones((16, 16), int)
    d = np.full((16, 16), 50.0)
    n = 2.0 * d                                    # uniform ratio 2.0 everywhere
    df = object_ratios(labels, n, d)
    row = df.iloc[0]
    assert row['ratio_of_means'] == pytest.approx(2.0, rel=1e-9)
    assert row['mean_of_ratio'] == pytest.approx(2.0, rel=1e-9)


def test_mean_of_ratio_and_ratio_of_means_DIFFER_on_a_heterogeneous_object():
    """Half the object has a big denominator, half a small one — mean-of-ratio (equal per-pixel weight)
    and ratio-of-means (aggregate) then answer different questions and diverge. Both are reported."""
    labels = np.ones((10, 10), int)
    d = np.full((10, 10), 100.0); d[:5, :] = 1.0   # heterogeneous denominator
    n = np.full((10, 10), 10.0)                    # constant numerator
    df = object_ratios(labels, n, d)
    row = df.iloc[0]
    # ratio_of_means = 1000 / (500*1 + 500*100)/... aggregate; mean_of_ratio averages 10/1 and 10/100
    assert row['mean_of_ratio'] != pytest.approx(row['ratio_of_means'], rel=1e-3)
    assert set(['ratio_of_means', 'mean_of_ratio', 'fraction_thresholded']).issubset(df.columns)


def test_uncorrected_bleedthrough_biases_toward_1_and_a_coefficient_corrects_it():
    labels = np.ones((20, 20), int)
    true_d = np.full((20, 20), 20.0)
    n = np.full((20, 20), 60.0)                    # true ratio 3.0
    measured_d = true_d + 0.5 * n                  # 50% of the numerator bleeds into the denominator

    uncorrected = object_ratios(labels, n, measured_d).iloc[0]['ratio_of_means']
    corrected = object_ratios(labels, n, measured_d, bleedthrough_coeff=0.5).iloc[0]['ratio_of_means']
    assert uncorrected < 3.0 and uncorrected > 1.0, "uncorrected bleed-through must bias toward 1"
    assert corrected == pytest.approx(3.0, rel=1e-9), "the coefficient must recover the true ratio"
    assert bool(object_ratios(labels, n, measured_d).iloc[0]['bleedthrough_corrected']) is False


def test_ratio_is_registered_in_the_measurement_ontology():
    from pycat.utils.measurement_ontology import describe
    d = describe('ratio')
    assert d is not None and d.units == 'dimensionless'
    assert any('BACKGROUND FIRST' in c for c in d.caveats)
    assert any('BLEED-THROUGH' in c or 'bleed-through' in c.lower() for c in d.caveats)


def test_object_ratios_records_the_background_used():
    labels = np.ones((8, 8), int)
    df = object_ratios(labels, np.full((8, 8), 30.0), np.full((8, 8), 10.0),
                       background_num=5.0, background_den=2.0)
    assert df.iloc[0]['background_num'] == 5.0 and df.iloc[0]['background_den'] == 2.0
