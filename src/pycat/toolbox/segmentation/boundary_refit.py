"""**Where the edge of an object is, decided by the object — not by the band-pass.**

Why this exists
---------------
Today an object's reported SIZE is wherever a band-pass tuned to ``ball_radius``
happens to cross a Niblack/Sauvola threshold whose window is also ``ball_radius``.
So the size is a property of a parameter rather than of the object, and it drifts
in opposite directions at different scales. Measured against constructed ground
truth (``benchmarks/condensate_scale.py``), the same pipeline returns:

    elongated objects, major axis 3-20 px   median predicted / true area  1.63
    round objects,     radius   11-17 px    mean coverage of the true object  0.81-0.83

— over-covering the elongated ones by 63% while leaving a fifth of each large
round one outside the mask. No single threshold fixes that, because the error is
not a threshold error: the two populations are being measured by different,
scale-dependent rulers. (With this step: 1.03 and 0.92-1.00 respectively.)

This module gives them one ruler. Each object keeps the identity the existing
detection gave it; only its EDGE is re-placed, at the point where the RAW signal
falls to a fixed fraction of the way from that object's own local background up
to its own peak. At the default ``level=0.5`` that is the **half-maximum
contour** — the convention ``tests/fixtures_synthetic.py`` already uses to define
ground truth (``core = blob > amplitude * 0.5``), the convention used to size a
fluorescent object generally, and close to where a human annotator traces a
condensate.

Two properties follow, and both are the point:

* the size no longer depends on ``ball_radius``, so it is comparable between a
  4 px punctum and a 20 px condensate in the same field; and
* it is measured on the PRE-enhancement image, so no amount of band-pass,
  CLAHE or top-hat upstream can move it.

What this does NOT do
---------------------
It does not decide whether a detection is real. Every object handed to it has
already passed the punctate gate and the full SNR/kurtosis/contrast refinement;
this step only answers "given that this object is real, where does it end?".
An object whose local contrast is too poor to place an edge on is returned
unchanged rather than guessed at.
"""
from __future__ import annotations

import numpy as np
import scipy.ndimage as ndi
import skimage as sk


# Refuse a re-fit that inflates an object beyond this factor. A flood that runs
# this far has left the object and entered its neighbourhood — the honest
# response is to keep the original detection, not to report the neighbourhood.
DEFAULT_MAX_GROWTH = 6.0

# ...and refuse one that collapses it below this fraction of the detection. The
# re-fit is allowed to pull a boundary IN — that is half its value, because the
# same settings that leave a fifth of a large round condensate outside its mask
# over-cover an elongated object by 63% (measured; see the module docstring) —
# but a contour that vanishes means the level could not be placed, not that the
# object is not there.
DEFAULT_MIN_SHRINK = 0.20

# The object's own "peak" is a high percentile rather than the maximum: one hot
# pixel should not set the level for the whole object.
DEFAULT_PEAK_PERCENTILE = 98.0

# ...and its local background is a LOW percentile of the surrounding ring rather
# than the median — see `_local_background` for why that specific asymmetry is
# what makes an under-covering detection recoverable at all.
BACKGROUND_PERCENTILE = 25.0

# How many times to re-place the background ring around the improved boundary.
# Two is enough: the first pass fixes a badly wrong boundary, the second measures
# a background that is actually outside the object. See `_fit_one_object`.
DEFAULT_PASSES = 2


def _local_background(sub, seed, cell, others, ring_px,
                      percentile=BACKGROUND_PERCENTILE):
    """This object's local background: a LOW percentile of a wide surrounding ring,
    with every OTHER object removed.

    Two decisions here, and both come from the failure this whole module exists to
    fix — a detection that is much SMALLER than its object.

    *Why a low percentile and not the median.* When the detection under-covers badly, the
    ring around it still lands inside the object's own bright body. A median of that
    ring reads nearly as bright as the object itself, the half-max level computed
    from it lands above the object's own plateau, and the re-fit refuses to grow —
    it silently confirms the too-small mask it was supposed to correct. A low
    percentile of the same ring finds the darkest quarter of the neighbourhood,
    which is background whenever any of the ring has reached it. For an object that
    is already well covered the ring is all background anyway, and the difference
    between its 25th percentile and its median is a fraction of the noise sigma —
    far too small to move a level set by (peak - background).

    *Why other objects are excluded.* A neighbour inside the ring raises the
    background, which raises the level, which shrinks this object — the same
    contamination ``puncta_refinement._ring_masks`` excludes for the same reason.
    """
    # Distance transform, NOT `binary_dilation(seed, disk(ring_px))`. The ring
    # scales with the object, so `ring_px` reaches tens of pixels on a large
    # condensate, and scipy's non-separable binary dilation scales catastrophically
    # with footprint size — measured elsewhere in this codebase at 13.6s for r=70
    # and a MemoryError by r=110 (see `fz._bridge_fragmented_rims`). A distance
    # transform costs the same at every radius.
    grown = ndi.distance_transform_edt(~seed) <= ring_px
    ring = grown & ~seed & cell & ~others
    if int(ring.sum()) < 8:
        ring = grown & ~seed & cell
    if int(ring.sum()) < 4:
        return None
    return float(np.percentile(sub[ring], percentile))


def _resolve_level(level, current):
    """The level for this pass: a constant, or a function of the CURRENT boundary's
    equivalent radius, in px of this image (called per object, per pass)."""
    if callable(level):
        return float(level(float(np.sqrt(max(int(current.sum()), 1) / np.pi))))
    return float(level)


def _fit_one_object(sub, seed, sub_cell, sub_basin, others, radius, level,
                    percentile, peak_percentile, passes):
    """The level contour for ONE object, refined over `passes` iterations.

    The iteration exists for the same reason as the low percentile above: the
    background ring is placed relative to the CURRENT boundary, so a badly
    under-covering detection puts it inside the object. One re-fit later the
    boundary is close to right, so the second ring is genuinely outside and the
    level it yields is the one that matters. Converges immediately when the first
    boundary was already good, which is the common case.
    """
    current = seed
    fitted = None
    for _ in range(int(passes)):
        ring_px = max(3, int(round(2.0 * radius)))
        background = _local_background(sub, current, sub_cell, others, ring_px,
                                       percentile)
        peak = float(np.percentile(sub[seed], peak_percentile))
        if background is None or peak <= background:
            return fitted
        threshold = background + _resolve_level(level, current) * (peak - background)
        # NOT unioned with the seed: the contour has to be free to come inside it,
        # or the over-covered half of the problem can never be corrected. The
        # growth/shrink bounds in the caller are what keep that safe.
        candidate = (sub >= threshold) & sub_cell & sub_basin
        components, _ = ndi.label(candidate)
        keep = np.unique(components[seed & candidate])
        keep = keep[keep != 0]
        if keep.size == 0:
            return fitted
        fitted = ndi.binary_fill_holes(np.isin(components, keep))
        if int((fitted ^ current).sum()) == 0:
            break
        current = fitted
    return fitted


def refit_object_boundaries(raw_image, object_mask, cell_mask, level=0.5,
                            smooth_sigma=1.0,
                            max_growth=DEFAULT_MAX_GROWTH,
                            min_shrink=DEFAULT_MIN_SHRINK,
                            peak_percentile=DEFAULT_PEAK_PERCENTILE,
                            background_percentile=BACKGROUND_PERCENTILE,
                            passes=DEFAULT_PASSES):
    """Re-place every object's boundary at its own ``level`` contour in ``raw_image``.

    Parameters
    ----------
    raw_image : numpy.ndarray
        The PRE-enhancement image. Passing the enhanced one would put the
        band-pass back into the size, which is exactly what this removes.
    object_mask : numpy.ndarray
        Binary detections to re-fit. Object identity is preserved: this never
        creates an object and never deletes one.
    cell_mask : numpy.ndarray
        No object grows outside its cell.
    level : float or callable, optional
        Fraction of the way from an object's local background to its own peak.
        0.5 (default) is the half-maximum contour. Lower values trace further
        out into the object's skirt, higher values cut closer to its core. A
        callable ``level(radius_px) -> float`` makes it size-dependent: it is
        called per object and per pass with the equivalent radius of the current
        boundary, in px of ``raw_image``.
    max_growth : float, optional
        Reject (and keep the original detection for) any object the re-fit would
        inflate by more than this factor.
    min_shrink : float, optional
        Likewise for collapse: an object whose contour comes back smaller than
        this fraction of the detection keeps the detection instead. Boundaries
        ARE allowed to move inward — the same settings over-cover elongated
        objects while under-covering large round ones — but a contour that
        nearly vanishes means the level could not be placed.
    background_percentile : float, optional
        Percentile of the surrounding ring taken as this object's local
        background. Low on purpose — see `_local_background`.
    passes : int, optional
        How many times to re-place the background ring around the improved
        boundary — see `_fit_one_object`.

    Returns
    -------
    numpy.ndarray
        Boolean mask, same shape as the input.
    """
    img = ndi.gaussian_filter(np.asarray(raw_image, dtype=np.float32), smooth_sigma)
    seeds = np.asarray(object_mask, dtype=bool)
    cell = np.asarray(cell_mask, dtype=bool)
    if not seeds.any():
        return seeds.copy()

    labels, _n = ndi.label(seeds)
    # Watershed basins over the whole field, seeded by the detections themselves.
    # This is what keeps two neighbouring objects apart when both flood outward:
    # each is confined to its own basin, so a re-fit can grow an object to its
    # true edge without ever merging it into the one next door.
    basins = sk.segmentation.watershed(-img, labels, mask=cell)

    out = np.zeros(seeds.shape, dtype=bool)
    for index, window in enumerate(ndi.find_objects(labels), start=1):
        if window is None:
            continue
        area = int((labels[window] == index).sum())
        radius = np.sqrt(area / np.pi)
        pad = int(np.ceil(max(4.0, 2.5 * radius)))
        y0 = max(0, window[0].start - pad)
        y1 = min(img.shape[0], window[0].stop + pad)
        x0 = max(0, window[1].start - pad)
        x1 = min(img.shape[1], window[1].stop + pad)
        view = (slice(y0, y1), slice(x0, x1))

        seed = labels[view] == index
        sub = img[view]
        sub_cell = cell[view]
        sub_basin = basins[view] == index
        others = seeds[view] & ~seed

        fitted = _fit_one_object(sub, seed, sub_cell, sub_basin, others, radius,
                                 level, background_percentile, peak_percentile,
                                 passes)
        if fitted is None:
            out[view] |= seed
            continue
        fitted_area = int(fitted.sum())
        if fitted_area > max_growth * area or fitted_area < min_shrink * area:
            fitted = seed
        out[view] |= fitted
    return out


# ── Regional boundaries ─────────────────────────────────────────────────────────
#
# Half-maximum is the right edge for a punctum near the PSF, and the wrong one for a
# condensate several times larger: its seed under-covers it by about half, the ring the
# level is measured from lands in its own skirt, and the result is drawn at ~0.55 of the
# area three annotators traced (large-puncta fields, 1.6.460). A simple per-cell
# threshold gets those right — which is why a hand-tuned CellProfiler pipeline beat PyCAT
# there — and badly wrong for sparse puncta, where the same threshold splits nucleoplasm
# from background and over-draws 9-15x.
#
# So each detection is offered BOTH boundaries and keeps the regional one only when it
# behaves like a condensate: compact, and not far past its own half-max contour. Nothing
# here was fitted to human masks: the four constants below were chosen by field-held-out
# cross-validation of pooled pixel IoU over all 27 annotated fields, with object sizes
# from `estimate_object_size_px` (docs/audits/region_selection_phases2-3_2026-09-29.md).

# Top-hat radius, in units of ball_radius: flattens nucleoplasm, keeps objects up to it.
REGIONAL_TOPHAT_SCALE = 2.0
# The regional foreground is the per-cell Otsu threshold of the flattened image x this.
REGIONAL_OTSU_FACTOR = 0.8
# Keep the regional boundary only if it is at most this many times the half-max one...
REGIONAL_MAX_GROWTH = 2.5
# ...and at least this solid (area / convex area). A region that leaks along textured
# nucleoplasm or an irregular aggregate is not compact, and keeps its half-max edge.
REGIONAL_MIN_SOLIDITY = 0.9
# Smoothing for the regional pass: one ORIGINAL pixel at the GUI's x2 working grid.
REGIONAL_SMOOTH_SIGMA = 2.0
# Provenance codes written by `refit_regional_boundaries(source_out=...)`.
BOUNDARY_REGIONAL = 1
BOUNDARY_LEVEL = 2


def keep_objects_apart(labels):
    """Remove the pixels where two DIFFERENT objects touch.

    Callers accumulate objects into one boolean mask and relabel it, so any two objects
    that touch come back as one. Measured on the large-puncta fields, touching regional
    boundaries fused 11% of annotated condensates that way (CellProfiler: 28%). A one-pixel
    gap makes relabelling unable to merge objects this step kept distinct.
    """
    labels = np.asarray(labels)
    high = ndi.maximum_filter(labels, size=3)
    low = ndi.minimum_filter(np.where(labels == 0, np.iinfo(labels.dtype).max, labels), size=3)
    touching = (labels > 0) & ((high > labels) | (low < labels))
    return np.where(touching, 0, labels)


def refit_regional_boundaries(raw_image, object_mask, cell_mask, ball_radius, level=0.5,
                              tophat_scale=REGIONAL_TOPHAT_SCALE,
                              otsu_factor=REGIONAL_OTSU_FACTOR,
                              max_growth=REGIONAL_MAX_GROWTH,
                              min_solidity=REGIONAL_MIN_SOLIDITY,
                              smooth_sigma=REGIONAL_SMOOTH_SIGMA, source_out=None):
    """Per object, the regional boundary if it is condensate-like, else the ``level`` contour.

    Each detection is grown by watershed into the cell's foreground — the Otsu threshold
    (x ``otsu_factor``) of the image after a white top-hat at ``tophat_scale * ball_radius``
    — and keeps that region only if it is at least ``min_solidity`` solid and at most
    ``max_growth`` times its own `refit_object_boundaries` contour. Otherwise it keeps
    that contour. Identity is preserved: one object out per detection, never created or
    deleted, and objects are kept apart so they cannot fuse downstream.

    Parameters
    ----------
    raw_image, object_mask, cell_mask : numpy.ndarray
        As for `refit_object_boundaries`; ``cell_mask`` is ONE cell.
    ball_radius : float
        The working scale (px of ``raw_image``) the rest of the pipeline uses.
    level : float or callable, optional
        The fallback contour level, passed to `refit_object_boundaries`.
    source_out : numpy.ndarray, optional
        Same shape as the input; receives, per output pixel, which boundary its object kept:
        ``BOUNDARY_REGIONAL`` or ``BOUNDARY_LEVEL``. This is the per-object provenance that
        lets a user see why one object is drawn wider than its neighbour.

    Returns
    -------
    numpy.ndarray
        Boolean mask, same shape as the input.
    """
    seeds = np.asarray(object_mask, dtype=bool)
    cell = np.asarray(cell_mask, dtype=bool)
    fallback = refit_object_boundaries(raw_image, seeds, cell, level=level)
    if not seeds.any() or int(cell.sum()) < 50:
        if source_out is not None:
            source_out[fallback] = BOUNDARY_LEVEL
        return fallback
    img = ndi.gaussian_filter(np.asarray(raw_image, dtype=np.float32), smooth_sigma)
    radius = max(2, int(round(tophat_scale * float(ball_radius))))
    flat = sk.morphology.white_tophat(img, sk.morphology.disk(radius))
    foreground = (flat > sk.filters.threshold_otsu(flat[cell]) * otsu_factor) & cell
    markers, _n = ndi.label(seeds)
    markers = np.where(cell, markers, 0)
    regions = sk.segmentation.watershed(-flat, markers, mask=foreground | (markers > 0))
    contours, _m = ndi.label(fallback)
    out = np.zeros(seeds.shape, dtype=np.int32)
    took_regional = []
    for index, window in enumerate(ndi.find_objects(markers), start=1):
        if window is None:
            continue
        region = regions == index
        own = np.unique(contours[(markers == index) & fallback])
        own = np.isin(contours, own[own != 0]) if (own != 0).any() else markers == index
        compact = sk.measure.regionprops(region.astype(np.uint8))[0].solidity >= min_solidity
        regional = compact and region.sum() <= max_growth * max(int(own.sum()), 1)
        took_regional.append(index) if regional else None
        out[(region if regional else own) & (out == 0)] = index
    out = keep_objects_apart(out)
    if source_out is not None:
        source_out[out > 0] = np.where(np.isin(out[out > 0], took_regional),
                                       BOUNDARY_REGIONAL, BOUNDARY_LEVEL)
    return out > 0
