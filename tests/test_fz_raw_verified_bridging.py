"""**A large condensate splitting into 2 solid disc pieces (no hollow ring) still wasn't bridged.**

Traced to what the pre-decomposition main branch (1.6.457) did differently: its inline rim-
bridging accepted a merge on piece-count (<=8) and area-ratio (<=2x) ALONE, with no requirement
that the fill recover a real hole. That is why main's "Enhanced Background Removed" layer could
show a hollowed condensate while its "Total Refined/Puncta Mask" still came out solid -- the
mask-level bridge didn't care that there was no hole to fill. When `_bridge_fragmented_rims` was
extracted (1.6.376-era), a THIRD, now-MANDATORY condition (hole-fill-area) was added to fix a
different, real bug (several separate small puncta glued into a "hook", see
`test_fz_rim_bridging.py`) -- but making hole-recovery mandatory for every bridge broke the
solid-piece-split case main's simpler gate happened to handle.

Two prior fixes for the solid-piece case were tried and discarded:
- An absolute per-fragment area floor (`min_fragment_area`) -- discarded because real single
  puncta in production data span the same 60-180px range as "large" condensate fragments, so no
  area threshold can tell them apart.
- Unconditional threshold+close+fill_holes on the continuous image -- discarded because no
  single closing radius bridges a genuine hole without also gluing nearby small puncta (radius
  needed for the hole merges puncta at typical spacing).

The fix that holds up (this file): a FOURTH, alternative accept condition, gated on the RAW
(pre-enhancement) image. Two solid pieces are bridged only if the pixels the CLOSING itself
added between them are, in the raw image, at least half as bright as the pieces' own detected
footprint there. A genuine single condensate's bridge is ~as bright as its own rim in the raw
image (ratio ~1.0, measured); a bridge manufactured between separate real puncta is only as
bright as PSF-tail bleed (ratio ~0.17 mean) even though that can still clear an absolute
noise-floor test on its own -- which is why the check is RELATIVE to the rim's own brightness,
not an absolute threshold, and why a generous closing radius is safe with it (an over-eager
bridge is rejected regardless of how far the radius reaches). Opt-in via `raw_img`; None
reproduces prior behaviour exactly, so every existing hole-fill-path test is unaffected.
"""
from __future__ import annotations

import numpy as np
import pytest
import skimage as sk

from pycat.toolbox.segmentation.fz import _bridge_fragmented_rims

pytestmark = pytest.mark.base


def _two_solid_discs(h=200, w=200, cy=100, cx1=87, cx2=113, radius=10):
    """One condensate's mask, split into two SOLID discs (no hollow ring). Edge-to-edge gap is
    (cx2-cx1)-2*radius = 6px -- close enough for the PRODUCTION default rim_close_radius=5 to
    geometrically reach, matching how this is actually invoked in subcellular.py."""
    yy, xx = np.mgrid[0:h, 0:w]
    disc1 = (yy - cy) ** 2 + (xx - cx1) ** 2 < radius ** 2
    disc2 = (yy - cy) ** 2 + (xx - cx2) ** 2 < radius ** 2
    return disc1 | disc2


def _raw_one_continuous_blob(h=200, w=200, cy=100, cx1=87, cx2=113, radius=10):
    """Raw (pre-enhancement) image for ONE real condensate: continuous bright ellipse
    spanning both disc footprints and the gap -- what the physical signal really looks like
    before the enhancement artifact split it."""
    yy, xx = np.mgrid[0:h, 0:w]
    span = ((yy - cy) ** 2 / (radius + 2) ** 2 +
            (xx - (cx1 + cx2) / 2) ** 2 / (((cx2 - cx1) / 2 + radius + 2) ** 2)) < 1
    raw = np.full((h, w), 20.0, dtype=np.float32)
    raw[span] = 200.0
    return raw


def _raw_genuinely_separate(h=200, w=200, cy=100, cx1=87, cx2=113, radius=10):
    """Raw image for two genuinely SEPARATE puncta: bright only at each footprint, real dim
    background in the gap -- no continuity to find."""
    yy, xx = np.mgrid[0:h, 0:w]
    disc1 = (yy - cy) ** 2 + (xx - cx1) ** 2 < radius ** 2
    disc2 = (yy - cy) ** 2 + (xx - cx2) ** 2 < radius ** 2
    raw = np.full((h, w), 20.0, dtype=np.float32)
    raw[disc1 | disc2] = 200.0
    return raw


def test_two_solid_fragments_ARE_bridged_at_the_PRODUCTION_default_radius_when_raw_confirms_one_object():
    discs = _two_solid_discs()
    raw = _raw_one_continuous_blob()
    n_before = sk.measure.label(discs).max()
    assert n_before == 2, "test setup check: the two discs must start separate"

    result = _bridge_fragmented_rims(discs, raw_img=raw)  # rim_close_radius defaults to 5

    n_after = sk.measure.label(result).max()
    assert n_after == 1, (
        f"two solid disc fragments confirmed as one continuous object in the raw image must "
        f"be reconnected at the DEFAULT rim_close_radius -- got {n_after} components"
    )


def test_two_solid_fragments_stay_separate_when_raw_shows_a_real_gap():
    discs = _two_solid_discs()
    raw = _raw_genuinely_separate()

    result = _bridge_fragmented_rims(discs, raw_img=raw)

    assert np.array_equal(result, discs), (
        "two genuinely separate puncta (dim gap in the raw image too) were bridged -- the "
        "raw-verification path must reject this"
    )


def test_raw_img_None_reproduces_prior_behavior_exactly():
    """The opt-in contract: omitting raw_img must be byte-identical to today's hole-fill-only
    behaviour -- this is what makes the new path safe to add without re-verifying every
    existing caller."""
    discs = _two_solid_discs()
    result_no_raw = _bridge_fragmented_rims(discs)
    assert np.array_equal(result_no_raw, discs), (
        "without raw_img, a solid-piece split (no hole) must remain unbridged exactly as before"
    )


def test_the_original_five_puncta_hook_bug_stays_rejected_even_with_raw_img():
    """Regression guard: the bug _bridge_fragmented_rims was originally built for (5 separate
    small puncta glued into a 'hook') must still be rejected when raw_img is supplied too --
    the new path must not accidentally reopen it."""
    centers = [(80, 80), (80, 94), (94, 80), (94, 94), (87, 87)]
    yy, xx = np.mgrid[0:200, 0:200]
    cluster = np.zeros((200, 200), dtype=bool)
    raw = np.full((200, 200), 20.0, dtype=np.float32)
    for (cy, cx) in centers:
        m = (yy - cy) ** 2 + (xx - cx) ** 2 < 4 ** 2
        cluster |= m
        raw[m] = 200.0
    n_before = sk.measure.label(cluster).max()
    assert n_before == len(centers), "test setup check: the puncta must start separate"

    result = _bridge_fragmented_rims(cluster, raw_img=raw)

    assert np.array_equal(result, cluster), (
        "5 separate small puncta were bridged even with raw_img supplied -- the original "
        "reported bug must stay fixed"
    )


def test_tightly_clustered_puncta_stay_separate_even_with_a_generous_closing_radius():
    """The specific case that broke an earlier (discarded) unconditional close+fill attempt:
    a generous closing radius alone would merge these, but raw-verification must still keep
    them apart."""
    centers = [(80, 80), (80, 96), (96, 80), (96, 96), (88, 88)]
    yy, xx = np.mgrid[0:200, 0:200]
    cluster = np.zeros((200, 200), dtype=bool)
    raw = np.full((200, 200), 20.0, dtype=np.float32)
    for (cy, cx) in centers:
        m = (yy - cy) ** 2 + (xx - cx) ** 2 < 4 ** 2
        cluster |= m
        raw[m] = 200.0

    result = _bridge_fragmented_rims(cluster, rim_close_radius=20, raw_img=raw)

    assert sk.measure.label(result).max() == 5, (
        "tightly clustered puncta must stay separate even at a generous closing radius, "
        "when raw evidence shows no real continuity between them"
    )


def test_sparse_necklace_around_a_nucleus_does_not_fill_the_interior():
    """The nucleus-ring false positive discovered while testing a discarded close+fill
    approach: few (<=8) puncta arranged in a ring must not have the enclosed nucleus interior
    treated as a recovered hole, at any closing radius."""
    yy, xx = np.mgrid[0:200, 0:200]
    ring_r, n_puncta, punct_r = 20, 6, 6
    necklace = np.zeros((200, 200), dtype=bool)
    raw = np.full((200, 200), 20.0, dtype=np.float32)
    for i in range(n_puncta):
        ang = 2 * np.pi * i / n_puncta
        cy, cx = 100 + ring_r * np.sin(ang), 100 + ring_r * np.cos(ang)
        m = (yy - cy) ** 2 + (xx - cx) ** 2 < punct_r ** 2
        necklace |= m
        raw[m] = 200.0
    interior = np.sqrt((yy - 100) ** 2 + (xx - 100) ** 2) < 12

    for close_radius in (10, 15, 20):
        result = _bridge_fragmented_rims(necklace, rim_close_radius=close_radius, raw_img=raw)
        filled = int((result & interior & ~necklace).sum())
        assert filled == 0, (
            f"close_radius={close_radius}: {filled} nucleus-interior pixels were wrongly "
            f"treated as a recovered hole"
        )
