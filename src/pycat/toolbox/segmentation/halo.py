"""Optical halo fragments: rings and arcs of light around a condensate, segmented as objects of their own.

A condensate's optics can put real photons in a ring around it — an Airy side lobe, a defocus halo, a
refractive-index interface effect. Segmentation then finds that ring, or, more often, pieces of it,
as extra "condensates": thin arcs at a near-constant distance outside a brighter object. Each arc
inflates the count; folding them into the parent would inflate its area and dilute its intensity.
So a halo fragment is REMOVED, the parent's mask is left exactly as it was, and the fragment is
reported (``ring_rejected``) rather than silently dropped.

An object ``O`` is a halo fragment of a brighter neighbour ``B`` when ALL four hold:

1. **It follows B's contour** — the distance from O's centre line (skeleton) to B's edge is nearly
   constant (low coefficient of variation). This is what works the same for a full ring and a short
   arc, and what a separate neighbouring condensate fails.
2. **It is thin** — narrow relative to B's radius, and long along B's edge relative to its own width.
   A condensate is filled; a halo fragment is a sliver.
3. **It is dim relative to B** — O's peak above background is at most a fraction of B's.
4. **Its standoff fits a halo** — it sits within about one parent radius of B's edge.

Each test alone has false positives (two adjacent condensates can satisfy 1 by chance; a small
punctum satisfies 2 and 3); the conjunction is what makes it safe.

**The thresholds are physical and geometric defaults, not measured ones.** None of the reference
data (the 2D cellular large/small/irregular fields) contains an optical ring
(``docs/audits/ring_rejection_phaseA_2026-10-06.md``), so there was nothing to calibrate against.
They are therefore set leniently on brightness and standoff — an ideal Airy first lobe is ~1.7% of
the peak, interface and defocus halos are brighter, and the 50% ceiling admits all of them — and
the protection against removing a real condensate rests on the SHAPE tests (thin, elongated, at a
constant standoff), which a filled object fails whatever its brightness.
"""
from __future__ import annotations

import numpy as np
import scipy.ndimage as ndi
from skimage.morphology import skeletonize

HALO_MAX_PEAK_FRACTION = 0.5      # O's peak above background <= this x B's
HALO_MAX_WIDTH_FRACTION = 0.5     # O's width <= this x B's equivalent radius
HALO_MIN_ELONGATION = 2.0         # O's length along B's edge >= this x its width
HALO_MAX_STANDOFF_CV = 0.3        # CV of skeleton-to-B-edge distance
HALO_MAX_STANDOFF_FRACTION = 1.0  # median standoff <= this x B's radius (+2 px)


def _object_stats(labels, raw):
    idx = np.arange(int(labels.max()) + 1)
    area = np.bincount(labels.ravel(), minlength=idx.size)
    peak = ndi.maximum(raw, labels, index=idx)
    return area, np.asarray(peak, dtype=float)


def _follows_edge(o, b):
    """(cv, median standoff) of O's skeleton distance to B's edge, within one window."""
    edge_b = b & ~ndi.binary_erosion(b)
    d = ndi.distance_transform_edt(~edge_b)
    skel = skeletonize(o)
    if not skel.any():
        skel = o
    v = d[skel]
    return float(v.std() / max(v.mean(), 1e-9)), float(np.median(v))


def find_halo_fragments(labels, raw, background=None, max_peak_fraction=HALO_MAX_PEAK_FRACTION,
                        max_width_fraction=HALO_MAX_WIDTH_FRACTION, min_elongation=HALO_MIN_ELONGATION,
                        max_standoff_cv=HALO_MAX_STANDOFF_CV,
                        max_standoff_fraction=HALO_MAX_STANDOFF_FRACTION):
    """Labels in ``labels`` that are halo fragments of a brighter neighbour, as {label: parent label}.

    ``raw`` is the image intensities are read from (the raw, pre-enhancement image); ``background``
    is its local background level (default: the 20th percentile of ``raw`` outside the objects).
    Nothing is changed; see `reject_halo_fragments` for the removal.
    """
    labels = np.asarray(labels)
    raw = np.asarray(raw, dtype=float)
    if labels.max() < 2:
        return {}
    if background is None:
        outside = raw[labels == 0]
        background = float(np.percentile(outside, 20)) if outside.size else 0.0
    area, peak = _object_stats(labels, raw)
    radius = np.sqrt(area / np.pi)
    slices = ndi.find_objects(labels)
    found = {}
    for o_lab, sl in enumerate(slices, start=1):
        if sl is None:
            continue
        pad = int(np.ceil(2 * radius.max())) + 4
        win = tuple(slice(max(0, s.start - pad), min(n, s.stop + pad)) for s, n in zip(sl, labels.shape))
        sub = labels[win]
        o = sub == o_lab
        width = 2.0 * float(ndi.distance_transform_edt(o).max())
        if width <= 0 or area[o_lab] / width < min_elongation * width:          # 2: not elongated
            continue
        d_o = ndi.distance_transform_edt(~o)
        for b_lab in np.unique(sub[(sub > 0) & (sub != o_lab)]):
            r_b = radius[b_lab]
            if peak[b_lab] <= peak[o_lab] or width > max_width_fraction * r_b:   # 2: not thin vs B
                continue
            b = sub == b_lab
            if float(d_o[b].min()) > max_standoff_fraction * r_b + 2:           # 4: too far to test
                continue
            if peak[o_lab] - background > max_peak_fraction * (peak[b_lab] - background):
                continue                                                         # 3: too bright
            cv, standoff = _follows_edge(o, b)
            if cv <= max_standoff_cv and standoff <= max_standoff_fraction * r_b + 2:   # 1 and 4
                found[o_lab] = int(b_lab)
                break
    return found


def reject_halo_fragments(mask, raw, background=None, **thresholds):
    """Split a segmented mask into (kept, rejected) boolean masks. The kept objects are exactly as
    they were — a parent's boundary is never touched — and the rejected ones are only removed."""
    mask = np.asarray(mask, dtype=bool)
    labels, _ = ndi.label(mask, structure=np.ones((3, 3), dtype=bool))   # a thin diagonal arc is one object
    found = find_halo_fragments(labels, raw, background=background, **thresholds)
    rejected = np.isin(labels, list(found)) if found else np.zeros(mask.shape, dtype=bool)
    return mask & ~rejected, rejected
