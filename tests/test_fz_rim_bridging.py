"""**Large-condensate rim bridging was gluing together separate, nearby small puncta.**

Reported by Meet Raval: "Total Puncta Mask" showed several clearly separate spots (visibly
distinct in the "Enhanced Background Removed" image) joined into one connected blob — a thin
curved "hook" shape, not a filled disc.

``_bridge_fragmented_rims`` (``fz_segmentation_and_binarization``'s rim-reconnection step)
exists to undo a real artifact: local thresholding hollows a large, flat condensate core into a
thin ring, and upstream fixed-scale operations often fragment that ring into disconnected arcs.
A morphological closing + hole-fill reconnects it. The accept/reject gate that decides whether a
closed/filled region is a genuine reconnected ring (keep it) or an accidental merge of separate
objects (revert it) used to check only two things: how many pre-existing pieces it joined
(<= 8) and how much area the bridging added (<= 2x). Neither reliably tells a fragmented RING
apart from several separate puncta close enough for the closing radius to touch — both can
easily satisfy both checks.

The fix adds a third, required condition: the fill must have actually recovered a SUBSTANTIAL
enclosed hole. A genuine fragmented ring has a hollow interior — recovering it is the entire
point of this step. Several separate puncta bridged by thin closing connectors enclose nothing;
``binary_fill_holes`` finds no real hole to fill there. This can only make the gate STRICTER: it
never accepts a region the old two-condition gate rejected, only rejects one with no real hole
to justify the merge — so large-condensate rim recovery is preserved.
"""
from __future__ import annotations

import numpy as np
import pytest
import skimage as sk

from pycat.toolbox.segmentation.fz import _bridge_fragmented_rims

pytestmark = pytest.mark.base


def _fragmented_ring(h=200, w=200, cy=100, cx=100, r_out=40, r_in=25, n_gaps=5, gap_deg=6):
    """One large condensate's rim, broken into n_gaps disconnected arcs — a genuine hollow
    interior once the arcs are reconnected."""
    yy, xx = np.mgrid[0:h, 0:w]
    dist2 = (yy - cy) ** 2 + (xx - cx) ** 2
    ring = (dist2 < r_out ** 2) & (dist2 > r_in ** 2)
    angles = np.degrees(np.arctan2(yy - cy, xx - cx)) % 360
    gaps = np.zeros_like(ring)
    step = 360 / n_gaps
    for i in range(n_gaps):
        g0 = i * step
        gaps |= (angles >= g0) & (angles < g0 + gap_deg)
    return ring & ~gaps


def _separate_puncta_cluster(h=200, w=200, centers=None, radius=4):
    """Several genuinely separate small puncta, close enough that the rim-bridging closing
    radius touches them, but with NO enclosed hole between them."""
    if centers is None:
        centers = [(80, 80), (80, 94), (94, 80), (94, 94), (87, 87)]
    yy, xx = np.mgrid[0:h, 0:w]
    mask = np.zeros((h, w), dtype=bool)
    for (cy, cx) in centers:
        mask |= ((yy - cy) ** 2 + (xx - cx) ** 2) < radius ** 2
    return mask, centers


def test_a_fragmented_condensate_rim_is_still_bridged_into_one_object():
    """The whole point of this function: a large condensate's ring, broken into arcs by
    upstream processing, must be reconnected into one solid object. This must keep working —
    the fix must never make large-condensate recovery worse."""
    ring = _fragmented_ring()
    n_pieces_before = sk.measure.label(ring).max()
    assert n_pieces_before > 1, "test setup check: the ring must start fragmented"

    bridged = _bridge_fragmented_rims(ring)

    n_pieces_after = sk.measure.label(bridged).max()
    assert n_pieces_after == 1, (
        f"a fragmented condensate rim ({n_pieces_before} arcs, a real hollow interior) must "
        f"be reconnected into ONE object — got {n_pieces_after}. This is a regression in "
        f"large-condensate recovery, which must never be degraded by the separate-puncta fix."
    )
    assert not np.array_equal(bridged, ring), (
        "the fragmented ring was returned unchanged — the bridging never applied"
    )


def test_separate_small_puncta_are_NOT_glued_together():
    """The reported bug: several genuinely separate puncta, close enough for the closing
    radius to touch, must NOT be merged into one blob — there is no hollow interior here to
    justify treating this as a reconnected ring."""
    cluster, centers = _separate_puncta_cluster()
    n_pieces_before = sk.measure.label(cluster).max()
    assert n_pieces_before == len(centers), "test setup check: the puncta must start separate"

    result = _bridge_fragmented_rims(cluster)

    assert np.array_equal(result, cluster), (
        "separate small puncta were bridged into one connected blob — this is exactly the "
        "reported bug: puncta that are clearly distinct in the enhanced image end up as one "
        "region in the puncta mask, with no real enclosed hole to justify the merge."
    )


def test_the_old_two_condition_gate_alone_would_have_accepted_the_separate_puncta():
    """Sanity check that this scenario actually exercises the fix: the piece-count (<=8) and
    area-ratio (<=2x) checks alone — the OLD gate — must be satisfied by the separate-puncta
    cluster, or this test suite doesn't reproduce the bug it claims to guard against."""
    import scipy.ndimage as ndi

    cluster, _ = _separate_puncta_cluster()
    closed = ndi.binary_closing(cluster, structure=sk.morphology.disk(5))
    filled = ndi.binary_fill_holes(closed)
    pre_labeled = sk.measure.label(cluster)
    post_labeled = sk.measure.label(filled)
    assert post_labeled.max() == 1, "test setup check: closing must merge the cluster into one blob"

    region = post_labeled == 1
    contributing = np.unique(pre_labeled[region])
    contributing = contributing[contributing != 0]
    pre_area = int(np.isin(pre_labeled, contributing).sum())
    region_area = int(region.sum())

    old_gate_would_accept = contributing.size <= 8 and region_area <= 2.0 * pre_area
    assert old_gate_would_accept, (
        "the old piece-count/area-ratio gate would already reject this scene — it does not "
        "reproduce the reported bug; tune the cluster spacing/radius"
    )

    hole_fill_area = int((filled & ~closed)[region].sum())
    assert hole_fill_area < max(20, 0.10 * pre_area), (
        "this cluster recovered a substantial enclosed hole, which means it is NOT a clean "
        "reproduction of the 'separate puncta, no hollow interior' case this test targets"
    )
