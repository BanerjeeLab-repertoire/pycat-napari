"""**The radial-localization profile pairs each bin's count with the RIGHT area** (1.6.376 audit, S1).

`radial_localization_profile` used two radial coordinates of OPPOSITE orientation: the condensate count ran
0 = centre → 1 = edge, but the ring AREA was binned on a distance-to-edge field (0 = edge → 1 = centre). So a
bin counted the points near the centre yet measured the outer-annulus area — all-central condensates were
paired with the ~9× larger edge area, understating central density and inverting the profile. This is the
golden-master test that was missing: it asserts the count-and-area PAIRING the old code inverted.
"""
import numpy as np
import pytest

from pycat.toolbox.spatial_metrology_tools import radial_localization_profile

pytestmark = pytest.mark.base      # pandas/scipy stack


def _disk(n=201, radius=90):
    c = n // 2
    yy, xx = np.mgrid[0:n, 0:n]
    return ((xx - c) ** 2 + (yy - c) ** 2) <= radius ** 2, c


def test_central_points_are_paired_with_the_small_central_area():
    mask, c = _disk()
    coords = np.column_stack([np.full(50, c), np.full(50, c)]).astype(float)   # 50 points AT the centre
    df = radial_localization_profile(coords, mask, n_bins=5, microns_per_pixel=1.0)

    assert int(df.iloc[0]['count']) == 50                       # the centre bin holds the points
    assert df.iloc[0]['area_um2'] < df.iloc[-1]['area_um2']     # central-disk area < outer-annulus area
    # density = central points over the SMALL central area, not the large outer annulus (the inversion)
    assert df.iloc[0]['density_per_um2'] > df.iloc[-1]['density_per_um2']
    assert int(df['count'].sum()) == 50                          # every point is binned exactly once


def test_edge_points_land_in_the_outer_bin():
    mask, c = _disk()
    edge = np.column_stack([np.full(50, c), np.full(50, c + 80)]).astype(float)  # points in the outer shell
    df = radial_localization_profile(edge, mask, n_bins=5, microns_per_pixel=1.0)
    assert int(df.iloc[-1]['count']) > int(df.iloc[0]['count'])  # they bin near the EDGE, not the centre


def test_area_increases_monotonically_from_centre_to_edge_on_a_disk():
    # On a disk, the annulus area grows with radius — a direct check that the area field is centre-referenced.
    mask, _c = _disk()
    df = radial_localization_profile(np.zeros((0, 2)), mask, n_bins=5, microns_per_pixel=1.0)
    assert df.empty                                             # no points → empty (unchanged contract)

    df2 = radial_localization_profile(
        np.column_stack([np.full(1, _c), np.full(1, _c)]).astype(float), mask, n_bins=5, microns_per_pixel=1.0)
    areas = df2['area_um2'].to_numpy()
    assert np.all(np.diff(areas) > 0)                           # strictly increasing centre → edge


def test_microns_per_pixel_scales_area_but_not_the_binning():
    mask, c = _disk()
    coords = np.column_stack([np.full(30, c), np.full(30, c)]).astype(float)
    df1 = radial_localization_profile(coords, mask, n_bins=4, microns_per_pixel=1.0)
    # coords are µm: at 0.5 µm/px the SAME physical centre point is at pixel c/0.5 — but the test keeps the
    # centroid at c, so use coords already in µm consistent with mpx (centre stays centre under scaling).
    coords_um = np.column_stack([np.full(30, c * 0.5), np.full(30, c * 0.5)]).astype(float)
    df2 = radial_localization_profile(coords_um, mask, n_bins=4, microns_per_pixel=0.5)
    assert int(df1.iloc[0]['count']) == 30 and int(df2.iloc[0]['count']) == 30   # centre bin in both
    # area scales with mpx² (0.5² = 0.25×), the count/binning does not
    np.testing.assert_allclose(df2.iloc[0]['area_um2'], df1.iloc[0]['area_um2'] * 0.25, rtol=1e-9)
