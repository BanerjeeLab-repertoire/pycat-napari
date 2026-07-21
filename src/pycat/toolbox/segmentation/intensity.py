"""Absolute-intensity statistics + punctate-signal gate - split out of segmentation_tools (1.6.240).

compute_image_intensity_stats measures the image's ABSOLUTE background/noise floor ONCE before any
per-cell renormalisation; cell_has_punctate_signal is the two-floor (local + absolute) hypothesis test
that decides whether a cell has real puncta. This is the RESTORED subsystem whose loss caused spurious
puncta (1.5.526). Moved VERBATIM - no threshold, no renormalisation-order change.
"""
from __future__ import annotations

import math
import numpy as np
import skimage as sk
import scipy.ndimage as ndi
from pycat.utils.general_utils import check_contrast_func


# ── RESTORED: the ABSOLUTE-INTENSITY punctate gate ───────────────────────────────────────
#
# **Meet reported spurious puncta returning, and sent the file that worked.** Diffing it
# against the tree showed the tree had **regressed**: it had lost this entire subsystem — and
# Meet's file also already contained the Cellpose module-level import that 1.5.523 'fixed'.
# *A newer file was overwritten with an older copy at some point.*
#
# **The mechanism, verified:** ``sk.exposure.equalize_adapthist`` normalises **every cell to
# unit maximum**. So a cell containing only noise is amplified by ``1 / cell_max`` — measured
# at **500x** on a cell holding nothing but background — and **both cells come out of CLAHE
# with the same [0, 1] range.** The empty cell's noise now has structure, and it segments as
# puncta.
#
# These two functions are the fix, and they are a **hypothesis test, not a contrast
# heuristic**: a pixel counts as evidence only if it clears **both** a LOCAL floor and an
# ABSOLUTE one measured from the image background **before any per-cell renormalisation**.


def compute_image_intensity_stats(image, labeled_cells=None, smooth_sigma=1.0,
                                  min_bg_px=1000):
    """
    Measure the image's ABSOLUTE background level and noise floor, once, before
    any per-cell or per-crop renormalisation.

    Why this exists
    ---------------
    Every stage between the raw image and `fz_segmentation_and_binarization`
    rescales intensity relative to whatever it is currently looking at:

      * `cell_mask_stretching` runs `equalize_adapthist` PER CELL;
      * `segment_subcellular_objects` normalises each crop by that crop's own
        maximum (`_proc_norm = proc_crop / proc_crop.max()`);
      * `rb_gaussian_background_removal` again divides by `img.max()`, then
        rescales to [0.75, 1.0] and CLAHEs;
      * `fz_segmentation_and_binarization` CLAHEs a third time and OR-combines
        an Otsu "bright" mask.

    After all of that, a cell containing nothing but camera noise is
    indistinguishable from a cell full of condensates: its noise has been
    stretched to the full dynamic range. The `check_contrast_func` guard cannot
    help, because it is evaluated *after* those stretches.

    The only quantity that survives is absolute brightness, and it must be
    captured up front. This function does that; `cell_has_punctate_signal`
    consumes it.

    Parameters
    ----------
    image : numpy.ndarray
        The RAW intensity image the puncta will be measured on (the same array
        later passed to `segment_subcellular_objects` as `original_image`).
    labeled_cells : numpy.ndarray, optional
        Labelled cell mask. Background is taken as `labeled_cells == 0`. If not
        supplied, or if fewer than `min_bg_px` background pixels exist, the
        darkest quartile of the image is used instead.
    smooth_sigma : float, optional
        Gaussian sigma applied before measuring. Must match the value used by
        `cell_has_punctate_signal`, otherwise the two noise estimates are not
        comparable. Default 1.0 (= `min_spot_radius / 2` for the default
        `min_spot_radius=2`).
    min_bg_px : int, optional
        Minimum number of background pixels required to trust `labeled_cells`.

    Returns
    -------
    dict with keys ``bg_median``, ``bg_sigma``, ``smooth_sigma``.

    Notes
    -----
    `sk.util.img_as_float32` performs a DTYPE-RANGE conversion (uint16 -> /65535),
    not a per-image min/max rescale, so absolute intensities are preserved and
    stats measured here remain comparable to crops converted the same way.
    Sigma is a robust MAD estimate, so a background containing a few stray
    bright pixels does not inflate the noise floor.
    """
    img = sk.util.img_as_float32(image)
    sm = ndi.gaussian_filter(img, smooth_sigma) if smooth_sigma > 0 else img

    bg = None
    if labeled_cells is not None:
        candidate = sm[np.asarray(labeled_cells) == 0]
        if candidate.size >= min_bg_px:
            bg = candidate
    if bg is None:
        bg = sm[sm <= np.percentile(sm, 25)]

    med = float(np.median(bg))
    mad = float(np.median(np.abs(bg - med)))
    sigma = 1.4826 * mad
    if sigma <= 0:
        sigma = float(np.std(bg)) or 1e-6

    return {'bg_median': med, 'bg_sigma': float(sigma),
            'smooth_sigma': float(smooth_sigma)}


def cell_has_punctate_signal(original_crop, cell_mask, image_stats=None,
                             n_sigma=5.0, abs_n_sigma=3.0, min_spot_radius=2,
                             min_area_px=None, smooth_sigma=None):
    """
    Decide whether a cell contains anything punctate, using ABSOLUTE intensity.

    This is a hypothesis test, not a contrast heuristic. A pixel counts as
    evidence only if it clears BOTH:

      1. a LOCAL floor  -- `median(cell) + n_sigma * MAD_sigma(cell)`. For pure
         Gaussian noise the 99.9th percentile sits near +3.1 sigma, so a 5-sigma
         floor is essentially never crossed by noise alone. `MAD_sigma` is taken
         over the whole cell, so it reflects the nucleoplasm's own fluctuation
         (puncta are a small area fraction and barely move the MAD).

      2. an ABSOLUTE floor -- `bg_median + abs_n_sigma * bg_sigma` from
         `compute_image_intensity_stats`. This is what a dim, out-of-focus cell
         cannot fake: its noise may be locally stretched, but it never gets
         brighter than the image's own background noise floor.

    Evidence is then required to be *shaped like a punctum*: at least one
    CONNECTED component of `min_area_px` pixels must clear the threshold. Noise
    crosses 5 sigma at isolated pixels, never in 12-pixel blobs, which is what
    makes this robust to the pixel correlation introduced by 2x bicubic
    upscaling.

    Deliberately NOT triggered by broad out-of-focus haze: haze raises the cell
    baseline (`median`) along with everything else, so it never produces a
    bright tail. Only genuinely punctate structure does.

    Parameters
    ----------
    original_crop : numpy.ndarray
        Raw intensity image (or a crop of it). Must be on the same absolute
        scale as the image `image_stats` was computed from.
    cell_mask : numpy.ndarray
        Boolean mask of this cell, same shape as `original_crop`.
    image_stats : dict, optional
        Output of `compute_image_intensity_stats`. If omitted, only the local
        criterion applies (still useful, but the absolute floor is the part that
        catches phantom cells, so supplying this is strongly recommended).
    n_sigma : float, optional
        Local threshold in robust sigmas above the cell median. Default 5.0.
    abs_n_sigma : float, optional
        Absolute threshold in sigmas above the image background. Default 3.0.
    min_spot_radius : int, optional
        Used to derive `min_area_px` (= pi * r^2) and the smoothing sigma.
    min_area_px : int, optional
        Override the connected-component area requirement.
    smooth_sigma : float, optional
        Defaults to `image_stats['smooth_sigma']` if given, else
        `max(0.5, min_spot_radius / 2)`.

    Returns
    -------
    has_signal : bool
    info : dict
        Diagnostics: ``z_local`` (peak-to-baseline in robust sigmas),
        ``largest_blob_px``, ``min_area_px``, ``binding`` ('local' or
        'absolute'), ``base``, ``sigma_cell``, ``threshold``.
    """
    if smooth_sigma is None:
        smooth_sigma = (image_stats['smooth_sigma'] if image_stats is not None
                        else max(0.5, min_spot_radius / 2.0))
    if min_area_px is None:
        min_area_px = max(4, int(round(math.pi * float(min_spot_radius) ** 2)))

    img = sk.util.img_as_float32(original_crop)
    sm = ndi.gaussian_filter(img, smooth_sigma) if smooth_sigma > 0 else img

    cell_mask = np.asarray(cell_mask, dtype=bool)
    vals = sm[cell_mask]
    info = {'z_local': 0.0, 'largest_blob_px': 0, 'min_area_px': min_area_px,
            'binding': 'local', 'base': 0.0, 'sigma_cell': 0.0, 'threshold': 0.0}
    if vals.size < 10:
        return False, info

    base = float(np.median(vals))
    mad = float(np.median(np.abs(vals - base)))
    sigma_cell = 1.4826 * mad
    if sigma_cell <= 0:
        sigma_cell = float(np.std(vals)) or 1e-6
    if image_stats is not None:
        # A cell's own fluctuation can never sit below the image noise floor.
        # Without this, a flat or saturated region drives sigma_cell -> 0 and
        # `base + n_sigma * sigma_cell` degenerates into "anything above the
        # median", which would pass every cell.
        sigma_cell = max(sigma_cell, image_stats['bg_sigma'])

    thr_local = base + n_sigma * sigma_cell
    thr_abs = -np.inf
    if image_stats is not None:
        thr_abs = image_stats['bg_median'] + abs_n_sigma * image_stats['bg_sigma']
    threshold = max(thr_local, thr_abs)

    candidate = (sm > threshold) & cell_mask
    labelled, n_found = ndi.label(candidate)
    largest = 0
    if n_found:
        largest = int(np.bincount(labelled.ravel())[1:].max())

    peak = float(np.percentile(vals, 99.9))
    info.update({'z_local': (peak - base) / sigma_cell,
                 'largest_blob_px': largest,
                 'binding': 'absolute' if thr_abs > thr_local else 'local',
                 'base': base, 'sigma_cell': sigma_cell,
                 'threshold': float(threshold)})
    return largest >= min_area_px, info
