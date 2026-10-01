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


def robust_cell_background(values, n_sigma=3.0, iterations=3, min_keep_fraction=0.10):
    """A cell's OWN background level and noise, with its objects clipped off.

    ── Why a plain median/MAD is not this ──────────────────────────────────────────
    ``cell_has_punctate_signal`` used ``median(cell)`` and ``1.4826 * MAD(cell)``, on
    the stated assumption that *"puncta are a small area fraction and barely move the
    MAD"*. That assumption is true for puncta and **false for condensates**, and it
    fails in exactly the direction that hides them.

    The mechanism is a threshold, not a gradient. Both the median and the MAD hold up
    while the bright population is the MINORITY of the cell's pixels — the median
    stays in the background, and the 50th percentile of |deviation| still lands
    inside it. Once the objects pass about half the cell, both cross over together:
    the median moves INTO the bright population, and the MAD stops describing the
    noise and starts describing the distance BETWEEN the two populations. Puncta
    never get near that; large condensates in a small nucleus do.

    Measured on the constructed-truth large-object scenes
    (``benchmarks/condensate_scale.py``, 18 cells across 3 seeds): the six cells whose
    object footprint was 0.40 of the cell or more — which after the PSF and this
    function's own smoothing is more than half its pixels — reported a sigma of
    0.0080-0.0101 against a true background noise of ~0.0001, an overestimate of
    roughly 80x, and a background of 0.0145 against a true nucleoplasm of 0.0080. The
    gate's floor is ``background + 5 * sigma``, so it landed **above the condensates
    themselves**, and all six cells were declared to contain no punctate signal and
    skipped before any per-object check could run. No cell below 0.39 was affected.

    ── Sigma clipping, seeded from the lower half ─────────────────────────────────
    So estimate the background from the background: iteratively drop pixels more than
    ``n_sigma`` above the current estimate and re-measure, until what remains is the
    nucleoplasm.

    Two details are what make it work here rather than merely sound right:

    * **The seed comes from the LOWER HALF of the cell, not from the whole of it.**
      Seeded from the whole-cell median/MAD the iteration cannot start at all in the
      case it exists for: at 44% object area the first clip level
      (``median + 3 * MAD``) already sits above the condensates, so nothing is
      removed, the estimate never moves, and the cell is skipped exactly as before.
      Measured across the same 18 cells, the lower-half seed recovers the true
      background (0.00796-0.00798 against a true 0.00800) in every one; the
      whole-cell seed fails outright on the densest.
    * **Each iteration re-clips the ORIGINAL values**, not the previous iteration's
      survivors, so an over-aggressive early cut is recoverable rather than
      permanent.

    On a cell that is mostly background this is a no-op to four decimal places — the
    lower-half seed introduces no bias on clean Gaussian noise (measured:
    ``(0.00800, 0.000201)`` from all three of median, whole-cell-seeded clip and
    lower-half-seeded clip on the same 20 000 samples), and the gate's noise-only
    rejection fixture is bit-for-bit unaffected. That is the point: it corrects the
    case the old estimator got wrong without disturbing the case it got right.

    Clipping is ONE-SIDED — only bright pixels are removed. A dark nucleolus is part
    of the background as far as this is concerned, exactly as it was before.

    Returns ``(base, sigma)``.
    """
    values = np.asarray(values, dtype=np.float64).ravel()
    if values.size < 8:
        return (float(np.median(values)) if values.size else 0.0,
                float(np.std(values)) if values.size else 1e-6)

    lower = values[values <= np.median(values)]
    base = float(np.median(lower))
    sigma = 1.4826 * float(np.median(np.abs(lower - base)))
    minimum_keep = max(16, int(min_keep_fraction * values.size))
    for _ in range(int(iterations)):
        if sigma <= 0:
            break
        kept = values[values <= base + n_sigma * sigma]
        if kept.size < minimum_keep:
            break
        new_base = float(np.median(kept))
        new_sigma = 1.4826 * float(np.median(np.abs(kept - new_base)))
        if new_sigma <= 0:
            break
        converged = (abs(new_sigma - sigma) < 1e-3 * sigma
                     and abs(new_base - base) < 1e-3 * max(sigma, 1e-12))
        base, sigma = new_base, new_sigma
        if converged:
            break
    if sigma <= 0:
        sigma = float(np.std(values)) or 1e-6
    return base, sigma


# ── The second route through the gate: a clearly transfected cell with clearly resolved puncta ──
#
# The local floor above is `base + 5 * sigma_cell`, and `sigma_cell` is the cell's own intensity
# spread. In a bright cell packed with dim irregular puncta that spread IS the puncta and their
# texture, not noise: measured on the annotated Irregular fields, sigma_cell was ~5x the pixel noise,
# the floor rose above the puncta, and 7 of 15 annotated cells (46% of the traced objects) were
# skipped whole. Lowering n_sigma is the wrong fix: the cells it newly admits are untransfected
# nuclei, whose small noise makes any flicker significant.
#
# So a cell also passes when it is clearly TRANSFECTED (its baseline sits far above the image
# background) and its peak stands far above its PIXEL noise (the high-frequency residual, which
# texture and puncta do not inflate). A dark cell can never take this route. Over all 241 cells of
# the 27 annotated fields: 59/59 annotated cells pass (52 before), 6 unannotated -- all transfected,
# none dark -- newly pass, and the result is the same for any baseline 5-20 and peak 8-10.
TRANSFECTED_MIN_BG_SIGMA = 10.0   # cell baseline above the image background, in background sigmas
TRANSFECTED_MIN_NOISE_Z = 10.0    # cell peak above its baseline, in pixel-noise sigmas


def _transfected_punctate_evidence(img, smoothed, cell_mask, base, image_stats, info):
    """True when the cell is clearly transfected and its peak is far above its pixel noise.
    Needs `image_stats` (the absolute background); without it this route is closed."""
    if image_stats is None or not cell_mask.any():
        return False
    residual = (img - ndi.gaussian_filter(img, 3.0))[cell_mask]
    noise = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
    if noise <= 0:
        return False
    base_over_bg = (base - image_stats['bg_median']) / max(float(image_stats['bg_sigma']), 1e-12)
    z_noise = (float(np.percentile(smoothed[cell_mask], 99.9)) - base) / noise
    passed = base_over_bg >= TRANSFECTED_MIN_BG_SIGMA and z_noise >= TRANSFECTED_MIN_NOISE_Z
    info.update({'base_over_bg': float(base_over_bg), 'z_noise': float(z_noise),
                 'transfected_route': bool(passed)})
    return passed


def cell_has_punctate_signal(original_crop, cell_mask, image_stats=None,
                             n_sigma=5.0, abs_n_sigma=3.0, min_spot_radius=2,
                             min_area_px=None, smooth_sigma=None):
    """
    Decide whether a cell contains anything punctate, using ABSOLUTE intensity.

    This is a hypothesis test, not a contrast heuristic. A pixel counts as
    evidence only if it clears BOTH:

      1. a LOCAL floor  -- `base + n_sigma * sigma` from `robust_cell_background`,
         which is the cell's own nucleoplasm level and fluctuation with its objects
         sigma-clipped off. For pure Gaussian noise the 99.9th percentile sits near
         +3.1 sigma, so a 5-sigma floor is essentially never crossed by noise alone.
         The clipping is what makes that still true when the objects are
         CONDENSATES rather than puncta: a plain whole-cell MAD is measuring the gap
         between nucleoplasm and condensates once they occupy ~40% of the cell, and
         put this floor above the objects it exists to detect.

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

    # The cell's own background, with its objects clipped off. A plain median/MAD
    # here measures the gap between nucleoplasm and condensates rather than the
    # noise as soon as the objects stop being a small area fraction, and the floor
    # below then sits above the objects it is meant to find — see
    # `robust_cell_background` for the measurement.
    base, sigma_cell = robust_cell_background(vals)
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
    transfected = _transfected_punctate_evidence(img, sm, cell_mask, base, image_stats, info)
    return largest >= min_area_px or transfected, info
