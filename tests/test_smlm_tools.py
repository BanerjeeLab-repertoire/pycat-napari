"""**SMLM localization loading — units are the whole risk, so the tests are about units and blinks.**

The spatial statistics already exist and are tested; this is the loader that feeds them. These pin the
traps that make it correct: ThunderSTORM nm columns load to µm, bare (unitless) columns REQUIRE a
pixel_size_um rather than being guessed, the SAME pattern in nm vs µm gives identical downstream stats
(the scale is normalized), clustered vs random is distinguished through the loader path, the temporal
merge reduces the blink over-count and the warning fires when unmerged, and the median precision floor is
reported.
"""
import numpy as np
import pandas as pd
import pytest

from pycat.toolbox.smlm_tools import (LocalizationSet, load_localization_table,
                                      temporal_merge, analyze_localizations)

pytestmark = pytest.mark.base


def _write(tmp_path, df, name='locs.csv'):
    p = tmp_path / name
    df.to_csv(p, index=False)
    return str(p)


def test_thunderstorm_nm_columns_load_to_um(tmp_path):
    df = pd.DataFrame({'x [nm]': [1000.0, 2000.0, 3000.0], 'y [nm]': [0.0, 500.0, 1000.0],
                       'frame': [1, 2, 3], 'uncertainty [nm]': [12.0, 15.0, 11.0]})
    loc = load_localization_table(_write(tmp_path, df))
    assert loc.source_units == 'nm' and loc.n == 3
    assert np.allclose(loc.x_um, [1.0, 2.0, 3.0])          # nm → µm
    assert loc.median_precision_nm() == pytest.approx(12.0)


def test_bare_columns_REQUIRE_a_pixel_size_never_guessed(tmp_path):
    df = pd.DataFrame({'x': [10.0, 20.0], 'y': [10.0, 20.0]})
    with pytest.raises(ValueError, match='ambiguous|pixel_size'):
        load_localization_table(_write(tmp_path, df))          # no unit, no pixel size → refuse
    loc = load_localization_table(_write(tmp_path, df), pixel_size_um=0.1)
    assert np.allclose(loc.x_um, [1.0, 2.0]) and loc.source_units == 'px'


def test_the_SAME_pattern_in_nm_and_um_gives_identical_stats(tmp_path):
    rng = np.random.default_rng(0)
    xy_um = rng.uniform(0, 10, (120, 2))
    nm = pd.DataFrame({'x [nm]': xy_um[:, 0] * 1000, 'y [nm]': xy_um[:, 1] * 1000})
    um = pd.DataFrame({'x [um]': xy_um[:, 0], 'y [um]': xy_um[:, 1]})
    a = analyze_localizations(load_localization_table(_write(tmp_path, nm, 'a.csv')), cell_area_um2=100.0)
    b = analyze_localizations(load_localization_table(_write(tmp_path, um, 'b.csv')), cell_area_um2=100.0)
    # Ripley's L is scale-sensitive; after normalization the two must agree exactly.
    assert np.allclose(a['ripley_l']['L_r'].to_numpy(), b['ripley_l']['L_r'].to_numpy(), equal_nan=True)


def test_clustered_vs_random_is_distinguished_through_the_loader(tmp_path):
    rng = np.random.default_rng(1)
    random_xy = rng.uniform(0, 10, (200, 2))
    centers = rng.uniform(1, 9, (5, 2))
    clustered_xy = np.vstack([c + rng.normal(0, 0.2, (40, 2)) for c in centers])
    ra = analyze_localizations(load_localization_table(_write(
        tmp_path, pd.DataFrame({'x [um]': random_xy[:, 0], 'y [um]': random_xy[:, 1]}), 'r.csv')),
        cell_area_um2=100.0)
    ca = analyze_localizations(load_localization_table(_write(
        tmp_path, pd.DataFrame({'x [um]': clustered_xy[:, 0], 'y [um]': clustered_xy[:, 1]}), 'c.csv')),
        cell_area_um2=100.0)
    # Clustering shows as a larger max Ripley L over the random case.
    assert ca['ripley_l']['L_r_minus_r'].max() > ra['ripley_l']['L_r_minus_r'].max()


def test_temporal_merge_reduces_the_blink_overcount_and_warns_when_unmerged(tmp_path):
    # One molecule at (5,5) blinking across 4 consecutive frames + 3 distinct molecules.
    blinks = pd.DataFrame({
        'x [um]': [5.0, 5.01, 4.99, 5.0, 1.0, 2.0, 8.0],
        'y [um]': [5.0, 5.0, 5.01, 4.99, 1.0, 8.0, 2.0],
        'frame': [1, 2, 3, 4, 1, 1, 1]})
    loc = load_localization_table(_write(tmp_path, blinks))
    merged = temporal_merge(loc, radius_um=0.05, gap_frames=1)
    assert merged.n < loc.n and merged.n == 4              # the 4 blinks collapse to 1 → 4 molecules
    unmerged_result = analyze_localizations(loc)
    assert 'warning' in unmerged_result and 'OVER-COUNT' in unmerged_result['warning']
    assert 'warning' not in analyze_localizations(merged, merged=True)


def test_median_precision_is_reported_as_the_resolution_floor(tmp_path):
    df = pd.DataFrame({'x [nm]': [0.0, 100.0], 'y [nm]': [0.0, 100.0],
                       'uncertainty [nm]': [20.0, 30.0]})
    result = analyze_localizations(load_localization_table(_write(tmp_path, df)))
    assert result['median_localization_precision_nm'] == pytest.approx(25.0)


def test_a_table_with_no_xy_columns_raises(tmp_path):
    df = pd.DataFrame({'foo': [1, 2], 'bar': [3, 4]})
    with pytest.raises(ValueError, match='x/y'):
        load_localization_table(_write(tmp_path, df))
