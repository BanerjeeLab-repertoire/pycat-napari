"""Folding a coarse-scale detection pass into the primary one, object by object.

The multiscale detection added each coarser pass with a plain boolean OR. That has two failure modes:

* **a split** — one condensate whose primary pass found two sub-peaks keeps both pieces, now joined to
  or overlapping the coarse view of the same object, and later watershed/`keep_objects_apart` makes the
  seam visible;
* **a merge** — two nearby small condensates that the coarse scale blurs into one blob are OR-ed with
  their two resolved primary detections into one oversized object (a size and count error).

The rule, per coarse detection ``L`` and the primary detections ``P`` it overlaps:

=====  ============================  ==========================================================
|P|    action                        why
=====  ============================  ==========================================================
0      keep ``L``                    a large object the primary band-pass could not see (the
                                     reason the coarse pass exists)
1      ``L`` replaces that detection the same object, better resolved at its own scale
>= 2   discard ``L``, keep ``P``     the fine scale resolved them; the coarse one would fuse them
       -- unless ``P`` are one       ... but when the raw image shows NO dip between the pieces
       object: ``L`` replaces them   inside ``L`` (< `SAME_OBJECT_MAX_DIP`), they are sub-peaks of
                                     one condensate the primary band-pass split: L replaces them
=====  ============================  ==========================================================

The last row is what lets one rule serve both of the spec's cases, which are geometrically identical (one
coarse blob over two primary pieces): two puncta the coarse scale fuses have a dip between them; two
sub-peaks of one condensate do not. Without ``raw`` the table applies as written.

Object count can only stay flat or fall by duplicate views; nothing is dropped for being dim.
"""
from __future__ import annotations

import numpy as np
import scipy.ndimage as ndi

_EIGHT = np.ones((3, 3), dtype=bool)
SAME_OBJECT_MAX_DIP = 0.2     # saddle within 20% of the dimmer piece's height above background = one object


def _pieces_are_one_object(raw, region, pieces, max_dip=SAME_OBJECT_MAX_DIP):
    """Do the primary ``pieces`` (label image, same window) join inside ``region`` without a dip?

    The saddle is the highest raw level at which every piece is still connected to the others within
    ``region``; the dip is how far it falls below the dimmest piece's peak, as a fraction of that peak's
    height above the background around ``region``."""
    ids = np.unique(pieces[pieces > 0])
    ring = ndi.binary_dilation(region, iterations=3) & ~region
    bg = float(np.percentile(raw[ring], 20)) if ring.any() else float(raw[region].min())
    low_peak = min(float(raw[pieces == i].max()) for i in ids)
    lo, hi = float(raw[region].min()), low_peak
    for _ in range(12):                                # bisection on the saddle level
        t = 0.5 * (lo + hi)
        lab, _ = ndi.label(region & (raw >= t), structure=_EIGHT)
        joined = len(set(np.unique(lab[pieces > 0])) - {0}) == 1
        lo, hi = (t, hi) if joined else (lo, t)
    height = low_peak - bg
    return height > 0 and (low_peak - lo) / height < max_dip


def reconcile_scales(primary, coarse, raw=None):
    """``primary`` with one coarse-scale detection mask folded in by the rule above (boolean masks).
    ``raw`` (the raw image) enables the same-object test for a coarse object over several pieces."""
    primary = np.asarray(primary, dtype=bool)
    coarse = np.asarray(coarse, dtype=bool)
    if not coarse.any():
        return primary.copy()
    p_lab, _ = ndi.label(primary, structure=_EIGHT)
    c_lab, _ = ndi.label(coarse, structure=_EIGHT)
    out = primary.copy()
    for c, sl in enumerate(ndi.find_objects(c_lab), start=1):
        if sl is None:
            continue
        piece = c_lab[sl] == c
        hits = np.unique(p_lab[sl][piece])
        hits = hits[hits != 0]
        if hits.size >= 2:
            region = piece | np.isin(p_lab[sl], hits)
            if raw is None or not _pieces_are_one_object(
                    np.asarray(raw, dtype=float)[sl], region, np.where(np.isin(p_lab[sl], hits), p_lab[sl], 0)):
                continue                               # the fine scale resolved them: keep those
        if hits.size:
            out[np.isin(p_lab, hits)] = False          # same object: the coarse view replaces it
        out[sl] |= piece
    return out
