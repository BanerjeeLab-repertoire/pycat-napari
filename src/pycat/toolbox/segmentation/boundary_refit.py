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
        threshold = background + float(level) * (peak - background)
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
    level : float, optional
        Fraction of the way from an object's local background to its own peak.
        0.5 (default) is the half-maximum contour. Lower values trace further
        out into the object's skirt, higher values cut closer to its core.
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
