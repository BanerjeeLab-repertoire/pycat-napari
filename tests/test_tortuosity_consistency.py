"""**`tortuosity_per_object` measures the main-axis geodesic, not the MST sum + raster endpoints** (1.6.376 S3).

The old code summed EVERY edge of the skeleton's minimum spanning tree — so a branched (Y/T) fibril folded
its side-branch length into the "path", inflating tortuosity — and took the end-to-end distance between the
raster-order (row-major) first/last skeleton pixels, which are not the geodesic endpoints. The fix computes the
geodesic diameter: the shortest path between the two farthest skeleton endpoints (degree-1 nodes), over the
straight-line distance between those same endpoints. These pin the three properties that fixes: a straight rod
is ≈ 1, an equal-armed right-angle bend is ≈ √2, and a Y-shape is NOT inflated by its stub branch.
"""
import numpy as np
import pytest

from pycat.toolbox.morphological_complexity_tools import tortuosity_per_object

pytestmark = pytest.mark.base      # scikit-image / scipy / pandas stack


def _rod(length=50):
    m = np.zeros((length + 10, 12), dtype=int)
    m[5:5 + length, 5:7] = 1
    return m


def _L(arm=30):
    """A right-angle bend of two equal arms: path ≈ 2·arm, end-to-end ≈ arm·√2 → tortuosity ≈ √2."""
    m = np.zeros((arm + 12, arm + 12), dtype=int)
    m[5:5 + arm, 5:7] = 1                       # vertical arm
    m[5 + arm - 2:5 + arm, 5:5 + arm] = 1       # horizontal arm
    return m


def _Y(main=40, stub=15):
    """A main axis with a diagonal stub off its middle — the stub must NOT enter the tortuosity."""
    m = np.zeros((main + 15, 60), dtype=int)
    m[5:5 + main, 29:31] = 1
    for k in range(stub):
        m[5 + main // 2 - k, 30 + k:32 + k] = 1
    return m


def _tort(mask):
    return float(tortuosity_per_object(mask)['tortuosity'].iloc[0])


def test_a_straight_rod_is_tortuosity_one():
    assert abs(_tort(_rod()) - 1.0) < 0.05


def test_a_right_angle_bend_is_root_two():
    # path = two equal arms, end-to-end = the hypotenuse → √2 (skeletonisation rounds the corner slightly)
    assert abs(_tort(_L(arm=30)) - np.sqrt(2)) < 0.1


def test_a_Y_shape_is_not_inflated_by_its_stub_branch():
    # the OLD MST-sum folded the stub length into the path, pushing tortuosity well past this; the geodesic
    # main-axis path ignores the stub.
    assert _tort(_Y(main=40, stub=15)) < 1.3


def test_the_reported_path_length_is_the_main_axis_not_main_plus_branch():
    # For the Y, the path length must be ~ the main axis (~main px), not main + stub (the MST sum).
    row = tortuosity_per_object(_Y(main=40, stub=15), microns_per_pixel=1.0).iloc[0]
    assert row['path_length_um'] < 40 + 6                       # ~ main axis, not main + the 15-px stub
    assert row['end_to_end_um'] > 30                             # geodesic endpoints span the main axis
