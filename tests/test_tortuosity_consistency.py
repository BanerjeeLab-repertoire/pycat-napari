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
from pycat.toolbox.fibril_tools import fibril_morphometry

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


def _arc(R=18):
    """An open circular arc — an UNBRANCHED but genuinely curved skeleton (not piecewise-linear), so the
    cross-check below tests more than two straight segments meeting at a corner."""
    H = W = 2 * R + 14
    cy = cx = R + 7
    m = np.zeros((H, W), dtype=int)
    yy, xx = np.ogrid[:H, :W]
    r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    ang = np.arctan2(yy - cy, xx - cx)
    m[(np.abs(r - R) < 1.0) & (ang > -1.0)] = 1
    return m


def _tort(mask):
    return float(tortuosity_per_object(mask)['tortuosity'].iloc[0])


def _fb_main_tort(mask):
    """The tortuosity of `fibril_morphometry`'s longest segment (its NetworkX per-edge path implementation)."""
    segment_rows, _nodes, _summary = fibril_morphometry(mask)
    main = max(segment_rows, key=lambda r: r['length_px'])
    return float(main['tortuosity'])


# ── N3: the two tortuosity implementations must not silently drift ───────────────────────────────────────
# `tortuosity_per_object` (scipy-sparse adjacency + `shortest_path`) and `fibril_morphometry` (a NetworkX graph
# with per-edge traced paths) compute tortuosity on two DIFFERENT skeleton representations. The S3 fix made them
# agree numerically, but nothing structural keeps them agreeing. Rather than force one onto the other's graph
# (which would cost `fibril_morphometry` its curvature/persistence-length information), this cross-check is the
# anti-drift guarantee: on an unbranched skeleton both measure the same end-to-end geodesic and must agree to
# floating point; on a branched one they legitimately measure different things, and that boundary is pinned too.

@pytest.mark.parametrize("mask,name", [(_rod(), "rod"), (_L(arm=30), "L-bend"), (_arc(), "arc")])
def test_the_two_tortuosity_impls_agree_on_an_unbranched_skeleton(mask, name):
    # one path, no junction → the whole-object geodesic (MC) IS the single segment (FB). They share the geometry,
    # so they agree to ~1e-15; a tolerance far tighter than the science needs, because its job is to catch drift.
    assert abs(_tort(mask) - _fb_main_tort(mask)) < 1e-9, f"tortuosity impls drifted on the {name}"


def test_the_impls_legitimately_diverge_on_a_branched_skeleton():
    # On a Y the two measure DIFFERENT quantities BY DESIGN: MC reports the whole-object geodesic diameter (the
    # path across the two farthest arms), FB reports its longest single segment. They must NOT be "unified" into
    # agreement — this pins that the divergence is real, so a future reader does not mistake it for a bug.
    y = _Y(main=40, stub=15)
    assert abs(_tort(y) - _fb_main_tort(y)) > 0.02


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
