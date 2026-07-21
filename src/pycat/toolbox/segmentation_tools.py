# -*- coding: utf-8 -*-
"""
Image Segmentation and Analysis Module for PyCAT 

This module provides functions for image segmentation and analysis, including local thresholding, watershed segmentation,
felzenszwalb segmentation, cellpose segmentation, random forest pixel classification, and more. These functions are designed 
to process grayscale images and binary masks, segment objects of interest, and extract relevant features for further analysis. 
Segmentation and post-segmentation filtering and processing functions are contained within to ensure accurate and reliable
segmentation results.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Standard library imports
import math 

# Third party imports
import numpy as np


from pycat.utils.object_ref import normalise_bbox_columns
from pycat.utils.tag_registry import tags_layer
import skimage as sk
from pycat.utils.general_utils import remove_small_objects_compat as _remove_small_objects_compat
import cv2
import scipy.ndimage as ndi
import scipy.stats as stats
import pandas as pd
# cellpose pulls in torch; imported lazily inside the cellpose functions that need it.
from sklearn.ensemble import RandomForestClassifier
# ── napari and Qt are imported LAZILY, inside the viewer-facing functions ─────
#
# This module holds 16 PURE analysis functions — the puncta refinement filter, local
# thresholding, the SNR/contrast gates, watershed splitting — and a handful of `run_*`
# functions that take a viewer. Importing napari at module scope blocked the headless
# import of ALL of them, so none could be tested in CI. The puncta filter in particular
# (whose SNR gate was found dead in 1.5.416) had never been exercised by a test.
#
# `napari` is used only for `isinstance(layer, napari.layers.Image)` inside the `run_*`
# functions; it is imported there.
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning

# Local application imports
from pycat.toolbox.label_and_mask_tools import binary_morph_operation, opencv_contour_func, extend_mask_to_edges
# `pycat.ui.ui_utils` imports napari — imported lazily inside the `run_*` functions.
from pycat.utils.general_utils import dtype_conversion_func, check_contrast_func
from pycat.utils.math_utils import remove_outliers_iqr
from pycat.toolbox.image_processing_tools import apply_rescale_intensity, rb_gaussian_bg_removal_with_edge_enhancement

# ---------------------------------------------------------------------------
# Cellpose GPU-availability caches  ->  moved to cellpose.py (1.6.241)
# ---------------------------------------------------------------------------


# Diagnostic flag for puncta-refinement rejection logging. Set to True here, or
# export PYCAT_REFINE_DEBUG=1, to print why each object is dropped in refinement.
_PYCAT_REFINE_DEBUG = False

# Use the windowed (fast) refinement filter by default. Bit-for-bit identical to
# the original; ~13× faster on the per-object loop. Set False to force the
# original implementation (used by the Segmentation Speed Comparison widget for
# A/B timing and equivalence checks).
_PYCAT_REFINE_FAST = True

def _refine_debug_enabled():
    if _PYCAT_REFINE_DEBUG:
        return True
    import os
    return os.environ.get('PYCAT_REFINE_DEBUG', '') not in ('', '0', 'false', 'False')


# ---------------------------------------------------------------------------
# Shared _to_uint16_safe dtype helper  ->  moved to _common.py (1.6.240)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation._common import (  # noqa: E402,F401
    _to_uint16_safe)


# ---------------------------------------------------------------------------
# Cellpose GPU/version helpers + model builder + model cache  ->  moved to cellpose.py (1.6.241)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.cellpose import (  # noqa: E402,F401
    available_cellpose_models, default_cellpose_model, _cellpose_major_version, _build_cellpose_model, _CELLPOSE_MODEL_CACHE)






# ---------------------------------------------------------------------------
# Local (windowed) thresholding + run_ wrapper  ->  moved to local_thresholding.py (1.6.240)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.local_thresholding import (  # noqa: E402,F401
    local_thresholding_func, run_local_thresholding)



# ---------------------------------------------------------------------------
# Watershed labeling (skimage + OpenCV marker splitters)  ->  moved to watershed.py (1.6.240)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.watershed import (  # noqa: E402,F401
    apply_watershed_labeling, opencv_watershed_func)



# ---------------------------------------------------------------------------
# Felzenszwalb segmentation + RAG merge + binarization  ->  moved to fz.py (1.6.241)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.fz import (  # noqa: E402,F401
    merge_mean_color, felzenszwalb_segmentation_and_merging, run_fz_segmentation_and_merging, fz_segmentation_and_binarization)



# ---------------------------------------------------------------------------
# Cellpose segmentation + random-forest classifier + contour refine  ->  moved to cellpose.py (1.6.241)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.cellpose import (  # noqa: E402,F401
    cellpose_segmentation, run_cellpose_segmentation, train_and_apply_rf_classifier, refine_labels_with_contours, run_train_and_apply_rf_classifier)



# ── The two refinement filters SHARE this, because they diverged once already ──
#
# `puncta_refinement_filtering_func_fast` is documented as bit-for-bit identical to
# `puncta_refinement_filtering_func`, and `tests/test_segmentation_refine.py`
# asserts it. It was not true. The 1.5.416 CNR fix was applied to the slow filter
# and **never to the fast one** — which is the DEFAULT (`_PYCAT_REFINE_FAST = True`):
#
#     slow:  local_cnr = (dilated_mean - loc_med) / loc_sd     <- the fix
#     fast:  dilated_mean / (img_local_bg_std + eps)           <- the dead bare ratio
#
# So the gate the slow path's own comment calls dead — "these two conditions have
# never rejected anything" — was still live for every user, and the ground-truth
# calibration justifying its threshold described code nobody ran. The equivalence
# test passed because `local_intensity_condition` decides first in its fixture and
# masks the difference (measured: puncta amplitudes 8->25 flip together at 11).
#
# What the fast path was keeping, measured against ground truth
# (`synthetic_puncta_image`, 3 amplitudes x 3 seeds):
#
#     amp=30   bare kept 3-11 real + 22-30 SPURIOUS  ->  CNR removed 0 real, 14-19 spurious
#     amp=60   bare kept  6-8 real + 16-19 SPURIOUS  ->  CNR removed 0 real, 12-14 spurious
#     amp=120  bare kept 18-27 real + 12-21 SPURIOUS ->  CNR removed 0 real, 10-18 spurious
#
# Zero real puncta lost in any run; 128 spurious detections removed. (The amp=8
# worry from the calibration — a real punctum at CNR 0.8, below the 1.0 cut — never
# arises: at amp=8 the intensity and kurtosis checks have already removed it, so the
# SNR gate is never consulted.)
#
# Copying the fix into the second implementation would leave two copies to drift
# again. These helpers ARE the fix: one definition, both callers.


def _local_ring_radii(area_px, cell_area_px):
    """How far to erode, and where to put the local-background ring, FOR THIS OBJECT.

    ── The rim was a fixed 1-4px no matter how big the object was ────────────

    `local_intensity_condition` and `gradient_condition` compare an object's
    interior (eroded 1px) against a band 1-4px outside it — regardless of the
    object's own size. That geometry is right for a point-like punctum a few px
    across. It is wrong for a condensate spanning tens to hundreds of px:

      * eroding 1px off a 30px-wide object removes almost nothing, so the
        "interior" sample is essentially the whole object, boundary included;
      * a 1-4px ring hugging a large object's edge sits INSIDE that object's own
        halo — the PSF tail and the real concentration gradient at its boundary
        both scale with the object — so the "background" is contaminated upward,
        the contrast is underestimated, and the checks misfire on real objects.

    Both are the same bug: a fixed-scale probe measuring a variable-scale object.
    The earlier attempt exempted large objects from the checks; this fixes what the
    checks measure instead, which means nothing needs exempting and the same rule
    applies at every size.

    Radii scale with the object's equivalent radius `r_eq = sqrt(area/pi)`:

        erode = gap = 0.5 * r_eq        band = 1.0 * r_eq

    **For a typical punctum this reproduces today's geometry exactly.**
    `min_spot_radius=2` gives `min_area ~13px` -> `r_eq ~2px` -> erode 1, gap 1,
    band 2: a ring from +1 to +3, which is what the fixed code did. So puncta are
    unaffected and only the objects the fixed scale could not describe change.

    The ceiling comes from the physics, not from taste. In cellulo the cell bounds
    the condensate, and all but extreme condensates are at most ~25% of the cell
    DIAMETER — so 25% of the cell's equivalent radius is the largest standoff that
    can be justified, and past it the ring would start sampling other cells or
    leave the cell entirely. In vitro there is no cell and objects can exceed one,
    but `cell_area_px` is then the field, so the cap scales with that instead. The
    floor of 3px keeps a tiny/degenerate cell from collapsing the ring to nothing.

    Returns `(erode_r, gap_r, band_r)` in pixels, each >= 1.
    """
    area = max(1.0, float(area_px))
    r_eq = float(np.sqrt(area / np.pi))
    cell_r = float(np.sqrt(max(1.0, float(cell_area_px)) / np.pi))
    cap = max(3, int(round(0.25 * cell_r)))

    erode_r = int(min(max(1, int(round(0.5 * r_eq))), cap))
    gap_r = int(min(max(1, int(round(0.5 * r_eq))), cap))
    band_r = int(min(max(2, int(round(1.0 * r_eq))), cap))
    return erode_r, gap_r, band_r


def _ring_masks(puncta_mask_holder, erode_r, gap_r, band_r):
    """Interior, dilated-object and local-background masks at the given radii.

    Shared so the two filters cannot drift — the same reason `_snr_conditions` is
    shared. `binary_dilation` with `disk(r)` is one call rather than r iterations of
    `disk(1)`, which also removes the old 3x loop.
    """
    interior = ndi.binary_erosion(puncta_mask_holder, sk.morphology.disk(erode_r))
    dilated = ndi.binary_dilation(puncta_mask_holder, sk.morphology.disk(gap_r))
    outer = ndi.binary_dilation(dilated, sk.morphology.disk(band_r))
    local_bg = outer ^ dilated
    return interior, dilated, local_bg


def _robust_bg(vals):
    """Background level and spread as (median, MAD-sigma). **Robust on purpose.**

    A plain mean/std is contaminated by NEIGHBOURING PUNCTA in the local ring:
    measured, a bright (amp=60) punctum with 3 neighbours had its ring_std inflated
    from 5 to 18, collapsing its CNR from 6.7 to 1.7. The metric was reporting
    crowding, not contrast, and a threshold calibrated against it would have deleted
    real puncta.
    """
    v = np.asarray(vals, dtype=float).ravel()
    if v.size < 4:
        return (float(np.mean(v)) if v.size else 0.0,
                float(np.std(v)) if v.size else 1.0)
    med = float(np.median(v))
    mad = 1.4826 * float(np.median(np.abs(v - med)))
    return med, (mad if mad > 0 else float(np.std(v)) or 1.0)


def _snr_conditions(img_dilated_object_mean, img_local_bg_pixels, cell_bg_iqr,
                    local_snr_threshold, global_snr_threshold):
    """The two SNR rejections, as CONTRAST to noise rather than a bare ratio.

    This used to be `object_mean / bg_std` — NO background subtraction. The camera
    pedestal is in the numerator and not the denominator, so the "SNR" scaled with
    the pedestal:

        pedestal 500  -> reported "SNR" 115
        pedestal 2000 -> reported "SNR" 416

    The gate rejects when SNR <= threshold, and the threshold is 1.0 — so it
    rejected only when `object_mean <= bg_std`. On any camera with a positive
    background that NEVER happens: a "punctum" of pure noise with zero contrast has
    object_mean 120 against bg_std 5, and is kept.

    The correct quantity is contrast above background in units of background NOISE,
    which is pedestal-invariant:

        CNR = (object_mean - background) / background_noise

    Calibrated against ground truth (12 fields, 8 puncta each):

        SPURIOUS (pure noise)  median CNR 0.0, 95th pct 0.4
        REAL punctum amp=8     median 0.8   (at the detection limit)
        REAL punctum amp=15    median 1.6
        REAL punctum amp=30    median 3.2
        REAL punctum amp=120   median 12.7

    Spurious detections top out at 0.4, so a threshold of 1.0 separates noise from
    real puncta. `cell_bg_iqr` is the cell background with outliers already removed.
    """
    eps = np.finfo(np.float32).eps
    loc_med, loc_sd = _robust_bg(img_local_bg_pixels)
    cell_med, cell_sd = _robust_bg(cell_bg_iqr)
    local_cnr = (img_dilated_object_mean - loc_med) / (loc_sd + eps)
    global_cnr = (img_dilated_object_mean - cell_med) / (cell_sd + eps)
    return (local_cnr <= local_snr_threshold, global_cnr <= global_snr_threshold)


def _report_refinement_drops(dropped, reason_counts, n_in):
    """**Tell the user what the filter did.** Shared by both implementations.

    A filter that removes objects and says nothing is indistinguishable from a
    segmentation that never found them. The counts are named so a suspicious
    rejection (e.g. everything dropped on `area`, meaning min_spot_radius is wrong
    for this pixel size) is visible immediately rather than after a day of
    confusion.

    The fast path — the DEFAULT — carried none of this: `napari_show_info` appeared
    twice in the slow filter and zero times in the fast one. So the always-on
    summary, added precisely so that puncta could not vanish silently, was itself
    silent for every user who had not set an env var.
    """
    n_dropped = len(dropped)
    if not n_dropped:
        return
    detail = ", ".join(f"{k} ({v})" for k, v in
                       sorted(reason_counts.items(), key=lambda kv: -kv[1]))
    msg = (f"Puncta refinement: {n_dropped} of {n_in} detections rejected. "
           f"Reasons: {detail}. "
           f"(A detection can fail several conditions, so these sum to more than the "
           f"number dropped.)")
    if n_in and n_dropped == n_in:
        napari_show_warning(
            msg + " **EVERY detection was rejected.** That usually means a threshold is "
            "wrong for this data rather than that the puncta are all spurious — "
            "check min_spot_radius against the pixel size, and the SNR thresholds "
            "against the image contrast.")
    elif n_in and n_dropped >= 0.8 * n_in:
        # >= not >: 4 of 5 is exactly 80%, and a user who loses four fifths of their
        # detections should hear about it.
        napari_show_warning(
            msg + " At least 80% were rejected — worth checking the thresholds before "
            "trusting the count.")
    else:
        napari_show_info(msg)


@tags_layer('puncta_filter', role='labels',
            summary='Puncta filtering by size/shape/intensity', target='punctum')
def puncta_refinement_filtering_func(original_img, processed_img, puncta_mask, cell_mask, labeled_puncta_mask, min_spot_radius,
                                     kurtosis_threshold=-3.0, local_snr_threshold=1.0, global_snr_threshold=1.0,
                                     intensity_hwhm_scale=1.17, max_area_fraction=0.25):
    """
    Refines a segmentation mask by filtering based on intensity, size, shape, and local background
    conditions. It aims to ensure that detected objects are valid and significant relative to
    the cell and the background, employing multiple criteria including intensity thresholds,
    kurtosis, ellipticity, area conditions, and signal-to-noise ratio (SNR).

    Parameters
    ----------
    original_img : numpy.ndarray
        The original image, before any processing.
    processed_img : numpy.ndarray
        The processed image, potentially after enhancing objects or other preprocessing steps.
    puncta_mask : numpy.ndarray
        A binary mask where the objects are identified, before refinement.
    cell_mask : numpy.ndarray
        A binary mask of the cell(s), used to define the cell background and exclude non-cell areas.
    labeled_puncta_mask : numpy.ndarray
        A labeled mask of the objects, where each punctum is assigned a unique label.
    min_spot_radius : float
        The minimum radius of objects, used in various calculations and filtering criteria.

    Returns
    -------
    refined_puncta_mask : numpy.ndarray
        The refined binary mask of objects after applying the filtering criteria.

    Notes
    -----
    This function applies a series of criteria to refine detected objects, including:
    - Local and global intensity thresholds to remove false objects.
    - Kurtosis to filter out objects with flat pixel intensity distributions, which are likely false positives.
    - Area conditions to exclude objects that are too large or too small.
    - Ellipticity to remove objects that are too long and narrow.
    - Gradient and SNR conditions to ensure objects stand out from their background and are not indistinguishable from noise.
    """
    # Convert images to uint16 for consistent intensity analysis.
    # Use _to_uint16_safe which normalises float images to [0,1] first;
    # dtype_conversion_func/img_as_uint clips float outside [-1,1] to extremes,
    # collapsing background-removed images to a flat array and causing false
    # std_dev<2 / kurtosis failures on all objects.
    original_image_16  = _to_uint16_safe(original_img)
    processed_image_16 = _to_uint16_safe(processed_img)
    
    refined_puncta_mask = puncta_mask.copy()

    # Calculate the Gaussian Gradient Magnitude (DoG) 
    DoG_img = ndi.gaussian_gradient_magnitude(original_img, sigma=min_spot_radius)

    # Exclude puncta from the cell mask to analyze background
    cell_xor_puncta_mask = cell_mask ^ puncta_mask
    cell_bg = original_img[cell_xor_puncta_mask]
    
    # Refine background analysis by removing outliers for accurate mean and std dev calculation
    cell_bg_iqr = remove_outliers_iqr(cell_bg)
    cell_bg_mean = np.mean(cell_bg_iqr)
    cell_bg_std = np.std(cell_bg_iqr)

    # Measure properties of each object in the labeled mask

    # ── 'bbox' is what makes a results row BRUSHABLE ────────────────────────────
    #
    # regionprops hands it over free, and PyCAT threw it away at every site. **A table without a
    # bbox is a table whose rows cannot be turned back into an image** — which is the difference
    # between a plot you can click and a plot you can only look at.
    #
    # It matters most in BATCH: a point in a plot built over a hundred files points at an object
    # in an image that is NOT LOADED. With the bbox, that object's region is read straight out of
    # the file. Without it, the only way back is to re-run the whole analysis.
    #
    # skimage expands 'bbox' into bbox-0..bbox-3; _normalise_bbox_columns renames them to the
    # bbox_y0/x0/y1/x1 that ObjectRef.from_row expects.
    properties = ('label', 'area', 'intensity_mean', 'axis_major_length', 'axis_minor_length', 'solidity', 'bbox')
    puncta_region_props_df = pd.DataFrame(sk.measure.regionprops_table(labeled_puncta_mask, intensity_image=original_img, properties=properties))
    puncta_region_props_df = normalise_bbox_columns(puncta_region_props_df)    # bbox-0..3 -> bbox_y0..x1, so a row can be brushed
    cell_area = np.sum(cell_mask)
    
    # Analyze each object individually
    # Accumulated for the summary below: what was rejected, and why.
    _dropped = []
    _reason_counts = {}

    for label in np.unique(labeled_puncta_mask)[1:]:
        # Create a binary mask for each object
        puncta_mask_holder = labeled_puncta_mask == label
        # Interior, dilated object, and the local-background ring — at radii SCALED
        # TO THIS OBJECT. A fixed 1-4px probe describes a punctum and misdescribes a
        # condensate; see `_local_ring_radii`. For a punctum the radii come out 1/1/2,
        # which is exactly the fixed geometry this replaces.
        _erode_r, _gap_r, _band_r = _local_ring_radii(
            int(puncta_mask_holder.sum()), cell_area)
        eroded_puncta_holder, dilated_puncta_holder, local_bg_mask = _ring_masks(
            puncta_mask_holder, _erode_r, _gap_r, _band_r)
        dilated_local_mask = local_bg_mask | dilated_puncta_holder

        # Collect pixel values from various masks and images for analysis
        # Get the pixels for each mask from the original image
        img_object_pixels = original_img[puncta_mask_holder]
        img_dilated_object_pixels = original_img[dilated_puncta_holder]
        img_local_bg_pixels = original_img[local_bg_mask]
        # Get the pixels for each mask from the processed image
        processed_object_pixels = processed_img[puncta_mask_holder]
        #processed_dilated_object_pixels = processed_img[dilated_puncta_holder]
        processed_local_bg_pixels = processed_img[local_bg_mask]
        # Get the pixels for each mask from the DoG gradient image
        gradient_object_pixels = DoG_img[eroded_puncta_holder]
        gradient_local_bg_pixels = DoG_img[dilated_puncta_holder ^ eroded_puncta_holder]
        # Get the local pixels from the 16 bit versions of the images 
        img_local_pixels = original_image_16[dilated_local_mask]
        processed_local_pixels = processed_image_16[dilated_local_mask]

        # Calculate the local standard deviation as a quick check of variation in pixel intensity
        img_local_std_dev = np.std(img_local_pixels)
        processed_local_std_dev = np.std(processed_local_pixels)
        if img_local_std_dev < 2 or processed_local_std_dev < 2:
            refined_puncta_mask[labeled_puncta_mask == label] = 0
            continue

        # Calculate the kurtosis of the pixel distributions in the mask
        img_object_kurtosis = stats.kurtosis(img_local_pixels)
        processed_object_kurtosis = stats.kurtosis(processed_local_pixels)
        # Calculate the mean and std dev of the pixel distributions in the masks
        img_object_mean = np.mean(img_object_pixels)
        processed_object_mean = np.mean(processed_object_pixels)
        gradient_object_mean = np.mean(gradient_object_pixels)
        img_dilated_object_mean = np.mean(img_dilated_object_pixels)
        img_local_bg_mean = np.mean(img_local_bg_pixels)
        processed_local_bg_mean = np.mean(processed_local_bg_pixels)
        gradient_local_bg_mean = np.mean(gradient_local_bg_pixels)
        img_local_bg_std = np.std(img_local_bg_pixels)
        processed_local_bg_std = np.std(processed_local_bg_pixels)

        # Calculate ellipticity and area from the region props dataframe
        df = puncta_region_props_df[puncta_region_props_df['label']==label]
        ellipticity = 1 - (df['axis_minor_length'].values[0]/df['axis_major_length'].values[0])


        # Setup local intensity based conditions
        local_intensity_condition = (
            img_object_mean < (img_local_bg_mean + intensity_hwhm_scale*img_local_bg_std) or
            processed_object_mean < (processed_local_bg_mean + intensity_hwhm_scale*processed_local_bg_std)
        )

        # Setup global intensity based conditions
        cell_intensity_condition = img_object_mean < cell_bg_mean
        # Setup kurtosis based conditions
        kurtosis_condition = img_object_kurtosis < kurtosis_threshold or processed_object_kurtosis < kurtosis_threshold
        # Setup area based conditions.
        # Large objects are only rejected here if they are ALSO irregularly
        # shaped (low solidity). A genuine large/coarsened condensate is a
        # single compact blob and has high solidity (area / convex_area),
        # typically ~0.9+. Low solidity indicates a concave, dumbbell-like,
        # or branching shape -- the signature of an erroneous merge of
        # several distinct puncta rather than one real large object -- and
        # is a more reliable artifact indicator than size alone.
        min_area = math.ceil(np.pi * min_spot_radius**2)
        solidity = df['solidity'].values[0]
        is_undersized = df['area'].values[0] < min_area
        is_oversized_and_irregular = (df['area'].values[0] > cell_area*max_area_fraction) and (solidity < 0.85)
        area_condition = is_oversized_and_irregular or is_undersized
        # Setup ellipticity based condition
        ellipticty_condition = ellipticity > 0.99
        # Setup gradient based condition
        gradient_condition = gradient_local_bg_mean < (gradient_object_mean + np.std(gradient_object_pixels)/4)
        # The two SNR rejections. CONTRAST to noise, not a bare ratio -- see
        # `_snr_conditions`, which is SHARED with the fast filter. It used to be
        # inlined here and only here, which is exactly how the fast path (the
        # default) kept the dead pedestal-scaled formula this replaced.
        local_snr_condition, global_snr_condition = _snr_conditions(
            img_dilated_object_mean, img_local_bg_pixels, cell_bg_iqr,
            local_snr_threshold, global_snr_threshold)

        # If any of the conditions are met, remove the object from the mask
        _reasons = []
        if local_intensity_condition: _reasons.append('local_intensity')
        if cell_intensity_condition:  _reasons.append('cell_intensity')
        if kurtosis_condition:        _reasons.append('kurtosis')
        if area_condition:            _reasons.append('area')
        if ellipticty_condition:      _reasons.append('ellipticity')
        if gradient_condition:        _reasons.append('gradient')
        if local_snr_condition:       _reasons.append('local_snr')
        if global_snr_condition:      _reasons.append('global_snr')
        if _reasons:
            # remove the puncta from the mask
            refined_puncta_mask[labeled_puncta_mask == label] = 0

            # ── ALWAYS record WHY, not only when a debug flag is set ────────────
            #
            # These eight conditions decide which detections survive into every downstream
            # count. The reasons were computed for each dropped object and then DISCARDED
            # unless `PYCAT_REFINE_DEBUG=1` was set — and even then they were `print`ed to a
            # console a napari user never sees.
            #
            # So a user whose puncta silently vanish had no way to find out why. That is the
            # same class of problem as the dead SNR gate itself (1.5.416): the pipeline was
            # making a consequential decision and not telling anyone.
            #
            # The per-object detail is kept for the debug flag; the SUMMARY is always
            # accumulated and always surfaced.
            _dropped.append({
                'label': int(label),
                'area_px': int(df['area'].values[0]),
                'object_mean': float(img_object_mean),
                'reasons': list(_reasons),
            })
            for _r in _reasons:
                _reason_counts[_r] = _reason_counts.get(_r, 0) + 1

            if _refine_debug_enabled():
                _a = int(df['area'].values[0])
                print(f"[PyCAT refine] dropped label {int(label)} "
                      f"(area={_a}px, obj_mean={img_object_mean:.0f}): "
                      f"{', '.join(_reasons)}")

    # Tell the user what the filter did -- see `_report_refinement_drops`, which is
    # SHARED with the fast filter. This summary used to live only here, so the
    # default path reported nothing at all.
    _report_refinement_drops(_dropped, _reason_counts,
                             int(len(np.unique(labeled_puncta_mask)) - 1))

    return refined_puncta_mask


def puncta_refinement_filtering_func_fast(original_img, processed_img, puncta_mask, cell_mask, labeled_puncta_mask, min_spot_radius,
                                          kurtosis_threshold=-3.0, local_snr_threshold=1.0, global_snr_threshold=1.0,
                                          intensity_hwhm_scale=1.17, max_area_fraction=0.25):
    """
    Windowed (fast) equivalent of ``puncta_refinement_filtering_func``.

    Bit-for-bit identical results, but each object's morphology and pixel-population
    statistics are computed inside that object's own padded bounding-box sub-window
    instead of on the full array. The original ran `binary_erosion`/`binary_dilation`
    (~5 calls) on the whole crop *per object*; those are local operations (a 4-px
    dilation cannot reach beyond the object bbox + 4 px), so restricting them to a
    small patch changes nothing mathematically while removing the dominant cost.

    All global quantities (DoG image, cell background mean/std, regionprops table)
    are computed exactly as in the original. Only the per-object inner work is
    windowed. See ``puncta_refinement_filtering_func`` for the meaning of each
    condition.
    """
    original_image_16 = _to_uint16_safe(original_img)
    processed_image_16 = _to_uint16_safe(processed_img)

    refined_puncta_mask = puncta_mask.copy()

    # Global DoG gradient image (same as original).
    DoG_img = ndi.gaussian_gradient_magnitude(original_img, sigma=min_spot_radius)

    # Global cell background (same as original).
    cell_xor_puncta_mask = cell_mask ^ puncta_mask
    cell_bg = original_img[cell_xor_puncta_mask]
    cell_bg_iqr = remove_outliers_iqr(cell_bg)
    cell_bg_mean = np.mean(cell_bg_iqr)
    cell_bg_std = np.std(cell_bg_iqr)

    # Batched regionprops with slices so we get each object's bounding box once.
    props = sk.measure.regionprops(labeled_puncta_mask, intensity_image=original_img)
    cell_area = np.sum(cell_mask)
    min_area = math.ceil(np.pi * min_spot_radius**2)
    H, W = labeled_puncta_mask.shape
    # Accumulated for the summary below: what was rejected, and why. Same contract
    # as the slow filter, via the same reporter.
    _dropped = []
    _reason_counts = {}

    for p in props:
        label = p.label
        # Ring radii SCALED TO THIS OBJECT — see `_local_ring_radii`. The same call
        # the slow filter makes, so the two cannot describe an object differently.
        _erode_r, _gap_r, _band_r = _local_ring_radii(int(p.area), cell_area)

        # Object bounding box, padded to contain the ring. The pad used to be a
        # fixed 4px, which was exactly enough for the fixed 3-step dilation — now
        # that the ring scales with the object, a fixed pad would CLIP it, and the
        # windowed filter would silently sample a different background from the
        # full-array one. +1 for the erosion/rounding margin.
        r0, c0, r1, c1 = p.bbox  # (min_row, min_col, max_row, max_col)
        pad = _gap_r + _band_r + 1
        rr0 = max(0, r0 - pad); rr1 = min(H, r1 + pad)
        cc0 = max(0, c0 - pad); cc1 = min(W, c1 + pad)

        sub_label = labeled_puncta_mask[rr0:rr1, cc0:cc1]
        puncta_mask_holder = (sub_label == label)

        eroded_puncta_holder, dilated_puncta_holder, local_bg_mask = _ring_masks(
            puncta_mask_holder, _erode_r, _gap_r, _band_r)
        dilated_local_mask = local_bg_mask | dilated_puncta_holder

        sub_orig = original_img[rr0:rr1, cc0:cc1]
        sub_proc = processed_img[rr0:rr1, cc0:cc1]
        sub_dog = DoG_img[rr0:rr1, cc0:cc1]
        sub_orig16 = original_image_16[rr0:rr1, cc0:cc1]
        sub_proc16 = processed_image_16[rr0:rr1, cc0:cc1]

        img_object_pixels = sub_orig[puncta_mask_holder]
        img_dilated_object_pixels = sub_orig[dilated_puncta_holder]
        img_local_bg_pixels = sub_orig[local_bg_mask]
        processed_object_pixels = sub_proc[puncta_mask_holder]
        processed_local_bg_pixels = sub_proc[local_bg_mask]
        gradient_object_pixels = sub_dog[eroded_puncta_holder]
        gradient_local_bg_pixels = sub_dog[dilated_puncta_holder ^ eroded_puncta_holder]
        img_local_pixels = sub_orig16[dilated_local_mask]
        processed_local_pixels = sub_proc16[dilated_local_mask]

        img_local_std_dev = np.std(img_local_pixels)
        processed_local_std_dev = np.std(processed_local_pixels)
        if img_local_std_dev < 2 or processed_local_std_dev < 2:
            refined_puncta_mask[labeled_puncta_mask == label] = 0
            continue

        img_object_kurtosis = stats.kurtosis(img_local_pixels)
        processed_object_kurtosis = stats.kurtosis(processed_local_pixels)
        img_object_mean = np.mean(img_object_pixels)
        processed_object_mean = np.mean(processed_object_pixels)
        gradient_object_mean = np.mean(gradient_object_pixels)
        img_dilated_object_mean = np.mean(img_dilated_object_pixels)
        img_local_bg_mean = np.mean(img_local_bg_pixels)
        processed_local_bg_mean = np.mean(processed_local_bg_pixels)
        gradient_local_bg_mean = np.mean(gradient_local_bg_pixels)
        img_local_bg_std = np.std(img_local_bg_pixels)
        processed_local_bg_std = np.std(processed_local_bg_pixels)

        # axis lengths from regionprops (identical to the DF the original used)
        _maj = p.axis_major_length
        _min = p.axis_minor_length
        ellipticity = 1 - (_min / _maj) if _maj > 0 else 0.0
        _area = p.area

        local_intensity_condition = (
            img_object_mean < (img_local_bg_mean + intensity_hwhm_scale*img_local_bg_std) or
            processed_object_mean < (processed_local_bg_mean + intensity_hwhm_scale*processed_local_bg_std)
        )
        cell_intensity_condition = img_object_mean < cell_bg_mean
        kurtosis_condition = img_object_kurtosis < kurtosis_threshold or processed_object_kurtosis < kurtosis_threshold
        # See puncta_refinement_filtering_func for the rationale: large
        # objects are only rejected if they're also irregularly shaped.
        is_undersized = _area < min_area
        is_oversized_and_irregular = (_area > cell_area*max_area_fraction) and (p.solidity < 0.85)
        area_condition = is_oversized_and_irregular or is_undersized
        ellipticty_condition = ellipticity > 0.99
        gradient_condition = gradient_local_bg_mean < (gradient_object_mean + np.std(gradient_object_pixels)/4)
        # The SAME call the slow filter makes. This was the divergence: the 1.5.416
        # CNR fix went into the slow path and never into this one -- the DEFAULT --
        # so the dead pedestal-scaled ratio kept every noise blob that survived the
        # other checks. Ground truth: 0 real puncta lost, 128 spurious removed.
        local_snr_condition, global_snr_condition = _snr_conditions(
            img_dilated_object_mean, img_local_bg_pixels, cell_bg_iqr,
            local_snr_threshold, global_snr_threshold)

        _reasons = []
        if local_intensity_condition: _reasons.append('local_intensity')
        if cell_intensity_condition:  _reasons.append('cell_intensity')
        if kurtosis_condition:        _reasons.append('kurtosis')
        if area_condition:            _reasons.append('area')
        if ellipticty_condition:      _reasons.append('ellipticity')
        if gradient_condition:        _reasons.append('gradient')
        if local_snr_condition:       _reasons.append('local_snr')
        if global_snr_condition:      _reasons.append('global_snr')
        if _reasons:
            refined_puncta_mask[labeled_puncta_mask == label] = 0
            # Accumulate the SUMMARY, not just the debug print. This path had only
            # the print — behind an env var, to a console a napari user never sees —
            # so the "always record why" guarantee the slow filter carries was
            # missing from the one that actually runs.
            _dropped.append({
                'label': int(label),
                'area_px': int(_area),
                'object_mean': float(img_object_mean),
                'reasons': list(_reasons),
            })
            for _r in _reasons:
                _reason_counts[_r] = _reason_counts.get(_r, 0) + 1
            if _refine_debug_enabled():
                print(f"[PyCAT refine-fast] dropped label {int(label)} "
                      f"(area={int(_area)}px, obj_mean={img_object_mean:.0f}): "
                      f"{', '.join(_reasons)}")

    _report_refinement_drops(_dropped, _reason_counts,
                             int(len(np.unique(labeled_puncta_mask)) - 1))
    return refined_puncta_mask

def puncta_refinement_func(original_image, processed_image, puncta_mask, cell_mask, min_spot_radius=2,
                           kurtosis_threshold=-3.0, local_snr_threshold=1.0, global_snr_threshold=1.0,
                           intensity_hwhm_scale=1.17, max_area_fraction=0.25, fast=None):
    """
    Refines a puncta mask through a series of image processing steps, including smoothing,
    morphological operations, refinement filtering, and watershed segmentation. This
    function is designed to improve the accuracy of puncta detection and segmentation by
    reducing noise and separating closely positioned objects.

    Parameters
    ----------
    original_image : numpy.ndarray
        The original microscopy image, before any processing. This image is used to guide
        the refinement process and to apply the watershed segmentation.
    processed_image : numpy.ndarray
        The processed image, which has potentially undergone preprocessing steps to enhance
        puncta or otherwise prepare the image for segmentation.
    puncta_mask : numpy.ndarray
        A binary mask where puncta have been initially identified. This mask is subject to
        refinement through this function.
    cell_mask : numpy.ndarray
        A binary mask of the cell(s) used to define areas of interest and exclude regions
        outside of cells.
    min_spot_radius : float, optional
        The minimum radius of puncta, which influences several processing steps including
        smoothing and watershed segmentation. Default is 2.

    Returns
    -------
    refined_mask : numpy.ndarray
        The refined binary mask of puncta after applying all processing and refinement steps.

    Notes
    -----
    The refinement process includes:
    - Converting images to a suitable data type and smoothing based on the minimum spot size.
    - Applying binary opening to the initial puncta mask to remove single-pixel noise.
    - Labeling the puncta mask for individual puncta identification.
    - Refining the labeled puncta mask through custom filtering criteria (primarily based on 
    the local intensity distribution aroud the object).
    - Separating closely positioned objects using watershed segmentation.
    - Further refining the segmentation to ensure accurate and distinct object detection, providing 
    an iterative refinement approach.
    - Final morphological opening to clean up the segmentation result.
    """
    # Convert image data types for processing
    original_img = dtype_conversion_func(original_image, 'float32')
    processed_img = dtype_conversion_func(processed_image, 'float32')
    puncta_mask = puncta_mask.astype(bool)
    cell_mask = cell_mask.astype(bool)

    # Smooth the images using a Gaussian filter based on the minimum spot size
    original_img = ndi.gaussian_filter(original_img, sigma=1.5)   
    processed_img = ndi.gaussian_filter(processed_img, sigma=min_spot_radius)

    # Refine initial puncta mask to remove noise
    puncta_mask  = binary_morph_operation(puncta_mask, iterations=2, element_size=1, element_shape='Cross', mode='Opening')

    # Select the refinement filter implementation. `fast` (windowed per-object
    # morphology) is bit-for-bit identical to the original but much faster; it is
    # the default unless explicitly disabled or overridden by the module flag
    # _PYCAT_REFINE_FAST (set to False to force the original for A/B comparison).
    if fast is None:
        fast = _PYCAT_REFINE_FAST
    _filter_fn = puncta_refinement_filtering_func_fast if fast else puncta_refinement_filtering_func

    # Label the puncta within the mask for individual analysis
    labeled_puncta_mask = sk.measure.label(puncta_mask)
    # First round of puncta refinement using filtering criteria
    refined_puncta_mask = _filter_fn(
        original_img, processed_img, puncta_mask, cell_mask, labeled_puncta_mask, min_spot_radius,
        kurtosis_threshold=kurtosis_threshold, local_snr_threshold=local_snr_threshold,
        global_snr_threshold=global_snr_threshold, intensity_hwhm_scale=intensity_hwhm_scale,
        max_area_fraction=max_area_fraction)
    # Apply watershed segmentation to separate closely positioned puncta
    watershed_puncta_mask = apply_watershed_labeling(original_img, refined_puncta_mask, sigma=min_spot_radius/2)
    # Deprecated method involving cv2 watershed 
    #watershed_puncta_mask = opencv_watershed_func(refined_puncta_mask, dist_thresh=0.5, sigma=min_spot_radius, dilation_size=1, dilation_iterations=3)
    #watershed_puncta_mask = sk.measure.label(watershed_puncta_mask)
    # Second round of refinement after watershed segmentation
    refined_puncta_mask = _filter_fn(
        original_img, processed_img, refined_puncta_mask, cell_mask, watershed_puncta_mask, min_spot_radius,
        kurtosis_threshold=kurtosis_threshold, local_snr_threshold=local_snr_threshold,
        global_snr_threshold=global_snr_threshold, intensity_hwhm_scale=intensity_hwhm_scale,
        max_area_fraction=max_area_fraction)
    # Final morphological opening to clean up the segmentation
    refined_mask = binary_morph_operation(refined_puncta_mask, iterations=1, element_size=1, element_shape='Disk', mode='Opening')

    return refined_mask


# ---------------------------------------------------------------------------
# Absolute-intensity stats + punctate-signal gate (the RESTORED subsystem)  ->  moved to intensity.py (1.6.240)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.intensity import (  # noqa: E402,F401
    compute_image_intensity_stats, cell_has_punctate_signal)



# ---------------------------------------------------------------------------
# Cell-mask stretching morphology  ->  moved to morphology.py (1.6.240)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.morphology import (  # noqa: E402,F401
    cell_mask_stretching)

@tags_layer('subcellular_segment', role='labels', inputs=('image',),
            summary='Subcellular object segmentation within cells')
def segment_subcellular_objects(original_image, pre_processed_image, cell_mask, cell_label, ball_radius, cell_df=None,
                                kurtosis_threshold=-3.0, local_snr_threshold=1.0, global_snr_threshold=1.0,
                                intensity_hwhm_scale=1.17, max_area_fraction=0.25, min_spot_radius=2,
                                crop_to_cell=True, refine_fast=None,
                                image_stats=None, punctate_gate=True,
                                punctate_gate_sigma=5.0, punctate_gate_abs_sigma=3.0):
    """
    Segments and refines subcellular objects within a specified cell mask from microscopy images.
    The function uses pre-processed images and cell-specific metrics to remove background, enhance
    edges, and segment objects like puncta. It then refines the segmentation based on image quality
    metrics such as kurtosis and signal-to-noise ratio (SNR).

    Parameters
    ----------
    original_image : numpy.ndarray
        The original microscopy image before any processing.
    pre_processed_image : numpy.ndarray
        The image after pre-processing steps, ready for segmentation.
    cell_mask : numpy.ndarray
        A binary mask representing a single cell within which objects are to be segmented.
    cell_label : int or float
        The label identifying the current cell within `cell_df` or used for reporting.
    ball_radius : float
        The radius used in background removal and edge enhancement algorithms.
    cell_df : pandas.DataFrame, optional
        A DataFrame containing cell-specific metrics such as kurtosis and SNR. Default is None.

    Returns
    -------
    refined_puncta_mask : numpy.ndarray
        The refined binary mask of segmented subcellular objects.
    puncta_mask : numpy.ndarray
        The initial binary mask of segmented subcellular objects before refinement.

    Notes
    -----
    This function applies background removal and edge enhancement before segmenting objects.
    It assesses the quality of segmentation using contrast checks and refines the segmentation
    through a separate refinement function to ensure accurate object detection.
    """
    # Convert images to float32 for consistent processing
    original_img = dtype_conversion_func(original_image, 'float32')
    pre_processed_img = dtype_conversion_func(pre_processed_image, 'float32')
    cell_mask = cell_mask.astype(bool)  # Ensure mask is boolean

    # ── Processing region ────────────────────────────────────────────────
    # crop_to_cell=True (default) processes only the cell's bounding box plus a
    # context margin, instead of the whole frame masked to one cell. This is the
    # main speedup for multi-cell images: with N cells each occupying a fraction
    # of the frame, whole-frame-per-cell processing is ~N× redundant.
    #
    # The context-dependent operations  --  Gaussian background removal
    # (σ≈2·ball_radius, ~3σ support) and CLAHE (kernel≈4·ball_radius)  --  need
    # enough surrounding pixels to match whole-image results near the cell edge.
    # A margin of 6·ball_radius makes the cropped result numerically IDENTICAL to
    # whole-image inside the cell (verified on real data: max diff 0.0, corr
    # 1.000 at pad=6·br, vs measurable edge error at pad=1·br). This is why the
    # earlier 1·ball_radius crop was distrusted and left off by default; the
    # larger margin removes that concern while retaining most of the speedup.
    if crop_to_cell:
        rows = np.any(cell_mask, axis=1)
        cols = np.any(cell_mask, axis=0)
        row_idx = np.where(rows)[0]
        col_idx = np.where(cols)[0]
        if row_idx.size == 0 or col_idx.size == 0:
            # The cell mask is empty in this frame (e.g. a cell present in the
            # union label set but absent from this time-point). There is nothing
            # to segment — return empty results instead of crashing on the
            # np.where(...)[[0, -1]] index.
            _empty = np.zeros(cell_mask.shape, dtype=bool)
            return _empty, _empty
        r0, r1 = row_idx[[0, -1]]
        c0, c1 = col_idx[[0, -1]]
        pad = int(math.ceil(6 * ball_radius))
        r0p = max(0, r0 - pad);  r1p = min(cell_mask.shape[0], r1 + pad + 1)
        c0p = max(0, c0 - pad);  c1p = min(cell_mask.shape[1], c1 + pad + 1)
    else:
        r0p, r1p = 0, cell_mask.shape[0]
        c0p, c1p = 0, cell_mask.shape[1]

    orig_crop  = original_img[r0p:r1p, c0p:c1p]
    proc_crop  = pre_processed_img[r0p:r1p, c0p:c1p]
    mask_crop  = cell_mask[r0p:r1p, c0p:c1p]

    # ── The ABSOLUTE-INTENSITY gate. It must run BEFORE the contrast steps. ─────
    #
    # ``check_contrast_func`` **cannot catch this**: it inspects the image AFTER the
    # contrast-maximising steps (per-cell CLAHE, background removal), so it essentially
    # **never fires**. This gate runs BEFORE them, on the raw intensity image, and is
    # **the only place in the chain where absolute brightness is still available.**
    #
    # Restored after Meet reported spurious puncta returning and sent the file that
    # worked. The tree had regressed and lost it.
    if punctate_gate:
        has_signal, gate_info = cell_has_punctate_signal(
            orig_crop, mask_crop, image_stats=image_stats,
            n_sigma=punctate_gate_sigma, abs_n_sigma=punctate_gate_abs_sigma,
            min_spot_radius=min_spot_radius)
        if not has_signal:
            napari_show_info(
                f"Cell {cell_label}: no punctate signal above the absolute "
                f"intensity floor (largest blob {gate_info['largest_blob_px']}px "
                f"< {gate_info['min_area_px']}px required; peak z="
                f"{gate_info['z_local']:.1f} sigma). Skipping -- no puncta.")
            _empty = np.zeros(cell_mask.shape, dtype=bool)
            return _empty, _empty

    # Initialize a flag indicating whether to perform background removal
    perform_bg_removal = True
    # Check if conditions are met to potentially skip background removal
    if cell_df is not None and not cell_df.empty:
        cell_kurt = cell_df.loc[cell_df['label'] == cell_label, 'img_kurtosis'].values
        cell_gaussian_snr = cell_df.loc[cell_df['label'] == cell_label, 'gaussian_snr_estimate'].values
        if np.isnan(cell_kurt[0]) or cell_gaussian_snr[0] < 1.0:
            perform_bg_removal = False

    # Perform background removal on the cropped ROI.
    # If the pre-processed image already has a sparse, peaked intensity
    # distribution (median of non-zero pixels < 0.05 after normalisation),
    # the input has already been LoG/DoG enhanced and CLAHE normalised  -- 
    # running rb_gaussian_bg_removal on top would subtract the nucleoplasm
    # baseline and collapse the noise floor to zero (NaN SNR). In that case
    # we use the preprocessed image directly, as it is already optimal for
    # Felzenszwalb segmentation.
    if perform_bg_removal:
        _proc_norm = proc_crop.astype(np.float32)
        _pmax = float(_proc_norm.max())
        if _pmax > 0:
            _proc_norm = _proc_norm / _pmax
        _nz = _proc_norm[(_proc_norm > 0.001) & mask_crop]
        _already_enhanced = (len(_nz) > 10 and float(np.median(_nz)) < 0.05)
        if _already_enhanced:
            # Input is LoG/CLAHE preprocessed  --  background is already near-zero.
            # Apply a light CLAHE pass only to ensure consistent dynamic range
            # for Felzenszwalb, without any subtractive background removal.
            from pycat.toolbox.image_processing_tools import _safe_equalize_adapthist
            import math as _math
            _ks = max(8, _math.ceil(ball_radius * 4))
            bg_removed_crop = _safe_equalize_adapthist(
                _proc_norm, kernel_size=_ks, clip_limit=0.005).astype(np.float32)
        else:
            bg_removed_crop = rb_gaussian_bg_removal_with_edge_enhancement(
                proc_crop, ball_radius, mask_crop)
    else:
        bg_removed_crop = np.zeros_like(orig_crop)

    # Check for contrast after bg removal
    contrast_flag = check_contrast_func(bg_removed_crop)
    if contrast_flag:
        napari_show_info(f"Cell {cell_label} has low contrast, likely has no puncta...")
        puncta_mask = np.zeros_like(cell_mask)
        refined_puncta_mask = np.zeros_like(cell_mask)
    else:
        # Segment and refine on the cropped ROI
        puncta_mask_crop = fz_segmentation_and_binarization(bg_removed_crop, mask_crop, ball_radius)
        # ── Pass the thresholds ON. They used to stop here. ────────────────────
        #
        # This call took `min_spot_radius` and `fast` and DROPPED the other five —
        # kurtosis_threshold, local_snr_threshold, global_snr_threshold,
        # intensity_hwhm_scale, max_area_fraction. They are accepted by this
        # function, threaded down from `run_segment_subcellular_objects`, and were
        # then silently discarded here; `puncta_refinement_func` fell back to its
        # own defaults.
        #
        # Those defaults are IDENTICAL to this function's (-3.0, 1.0, 1.0, 1.17,
        # 0.25), which is exactly why nobody saw it: at defaults the bug is
        # invisible. It only bites when a user CHANGES a threshold — at which point
        # their control silently does nothing and the refinement keeps using -3.0.
        #
        # Same class as the dead SNR gate (1.5.416): a consequential decision made
        # without telling anyone. A UI control that does not reach the code it names
        # is worse than no control, because it looks like it worked.
        refined_puncta_mask_crop = puncta_refinement_func(
            orig_crop, proc_crop, puncta_mask_crop, mask_crop,
            min_spot_radius=min_spot_radius,
            kurtosis_threshold=kurtosis_threshold,
            local_snr_threshold=local_snr_threshold,
            global_snr_threshold=global_snr_threshold,
            intensity_hwhm_scale=intensity_hwhm_scale,
            max_area_fraction=max_area_fraction,
            fast=refine_fast)

        # Paste cropped results back into full-size output arrays
        puncta_mask = np.zeros_like(cell_mask)
        refined_puncta_mask = np.zeros_like(cell_mask)
        puncta_mask[r0p:r1p, c0p:c1p] = puncta_mask_crop
        refined_puncta_mask[r0p:r1p, c0p:c1p] = refined_puncta_mask_crop

    return refined_puncta_mask, puncta_mask

def run_segment_subcellular_objects(pre_processed_image_layer, original_image_layer, data_instance, viewer,
                                    kurtosis_threshold=-3.0, local_snr_threshold=1.0, global_snr_threshold=1.0,
                                    intensity_hwhm_scale=1.17, max_area_fraction=0.25, min_spot_radius=2,
                                    punctate_gate=True, punctate_gate_sigma=5.0,
                                    punctate_gate_abs_sigma=3.0):
    """
    Orchestrates the segmentation and refinement of subcellular objects across all cells
    in an image. It utilizes the napari viewer for visualization and operates on pre-processed
    and original images to detect and refine objects such as puncta within cell masks.

    Parameters
    ----------
    import napari
    pre_processed_image_layer : napari.layers.Image
        The pre-processed image layer, ready for segmentation.
    original_image_layer : napari.layers.Image
        The original image layer before any processing.
    data_instance : object
        An instance containing a data repository with necessary parameters such as ball_radius.
    viewer : napari.Viewer
    """
    # Guard: if ball_radius is still at the hardcoded default (75) it almost
    # certainly means the user has not run Step 2 (Measure Line) since the last
    # Save & Clear. A default ball_radius of 75 is correct only for objects
    # ~100 px in diameter; for typical condensates (~5-20 px) it makes the CLAHE
    # kernel and local threshold window so large relative to the objects that
    # segmentation returns 0 objects. Warn and abort so the user knows what to do
    # rather than getting a confusing "0 objects after refinement" message.
    _br = data_instance.data_repository.get('ball_radius', 75)
    _os = data_instance.data_repository.get('object_size', 50)
    _DEFAULT_BR = 75
    _DEFAULT_OS = 50
    if abs(_br - _DEFAULT_BR) < 1 and abs(_os - _DEFAULT_OS) < 1:
        napari_show_warning(
            "Condensate segmentation: ball_radius is still at the default value "
            f"({_DEFAULT_BR} px), which means Step 2  --  Measure Line has not been run "
            "since the last Save & Clear.\n\n"
            "Please draw lines over a condensate and a cell in the viewer and click "
            "'Measure Line(s)' (Step 2) before segmenting. Using the default "
            f"ball_radius={_DEFAULT_BR} on small condensates will produce 0 objects."
        )
        return

    # Retrieve the data from the image layers and data instance
    original_image = original_image_layer.data
    pre_processed_image = pre_processed_image_layer.data
    ball_radius = data_instance.data_repository['ball_radius']

    # Labeled Cell Mask is created by the cell analyzer, if it is not in the viewer the function
    # will run on the entire image, however this is not the desired behavior hence we warn the user
    if 'Labeled Cell Mask' in viewer.layers:
        cell_masks = viewer.layers['Labeled Cell Mask'].data # Get the labeled cell mask
        cell_df = data_instance.get_data('cell_df', pd.DataFrame()) # Get the cell_df if it is available
        CMS_img = cell_mask_stretching(pre_processed_image, cell_masks) # Apply per cell contrast enhancement 
    else:
        cell_masks = np.ones_like(original_image).astype(int) # Create a dummy cell mask to run on the entire image
        cell_masks[0:2, 0:2] = 0 # Ensure there are 2 labels for the cell mask
        cell_df = pd.DataFrame() # Create cell_df since we assume it is not available because it is created by the cell analyzer too
        CMS_img = pre_processed_image.copy() # We cannot do per cell contrast enhancement without the cell masks
        napari_show_warning("Warning: This function is intended to be used after running Cell Analyzer.\n"
              "Ignore this warning if you intend on segmenting the entire image.\n"
              "Note that this may cause unintended behavior."
              )


    # Get the number of cells in cell_masks
    unique_labels = np.unique(cell_masks)
    unique_labels = unique_labels[1:]

    # ── Measure the ABSOLUTE background ONCE, before any per-cell renormalisation ──
    #
    # This is what lets ``segment_subcellular_objects`` tell **"this cell is empty"** apart
    # from **"this cell's noise has been stretched to look like signal"** — a distinction that
    # is destroyed the moment per-cell CLAHE runs, because it normalises every cell to unit
    # maximum.
    image_stats = None
    if punctate_gate:
        image_stats = compute_image_intensity_stats(
            original_image, cell_masks,
            smooth_sigma=max(0.5, min_spot_radius / 2.0))

    # Initialize total masks to store the combined results
    total_puncta_mask = np.zeros_like(cell_masks, dtype=bool)
    total_refined_puncta_mask = np.zeros_like(cell_masks, dtype=bool)

    # Iterate over all cell labels, segment, and refine puncta within each cell
    for label in unique_labels:

        contrast_stretched_img = CMS_img.copy()
        original_img = original_image.copy()

        napari_show_info(f"Processing cell... {label} of {len(unique_labels)}")

        # Create a binary mask for the current cell
        cell_mask_holder = np.zeros_like(cell_masks)
        cell_mask_holder[cell_masks==label] = 1
        cell_mask_holder = cell_mask_holder.astype(bool)

        # Segment and refine puncta within the cell
        refined_puncta_mask, puncta_mask = segment_subcellular_objects(
            original_img, contrast_stretched_img, cell_mask_holder, label, ball_radius, cell_df,
            kurtosis_threshold=kurtosis_threshold, local_snr_threshold=local_snr_threshold,
            global_snr_threshold=global_snr_threshold, intensity_hwhm_scale=intensity_hwhm_scale,
            max_area_fraction=max_area_fraction, min_spot_radius=min_spot_radius,
                image_stats=image_stats, punctate_gate=punctate_gate,
                punctate_gate_sigma=punctate_gate_sigma,
                punctate_gate_abs_sigma=punctate_gate_abs_sigma)

        # Add the segmented mask to the total mask
        total_puncta_mask += puncta_mask 
        total_refined_puncta_mask += refined_puncta_mask


    # Count DISTINCT objects via connected components, not the boolean max.
    # total_refined_puncta_mask is a boolean OR-accumulation across cells, so its
    # .max() is always 1 when any object exists  --  it reports "at least one pixel
    # set", not the object count. Label the mask to count and to give each object
    # a unique id for downstream analysis.
    labeled_total_puncta = sk.measure.label(total_puncta_mask.astype(bool))
    labeled_total_refined = sk.measure.label(total_refined_puncta_mask.astype(bool))
    n_condensates = int(labeled_total_refined.max())
    if n_condensates == 0:
        napari_show_warning(
            "Condensate segmentation found 0 objects after refinement filtering.\n"
            "Possible causes:\n"
            "  • The image has not been preprocessed  --  run Pre-process and Background Removal first.\n"
            "  • Thresholds are too strict for this image  --  try lowering Kurtosis threshold (e.g. -5),\n"
            "    Local/Global SNR thresholds (e.g. 0.5), or the Intensity scale (e.g. 0.8).\n"
            "  • The wrong image layer is selected in the dropdown  --  check both dropdowns point to\n"
            "    the correct pre-processed and raw layers.\n"
            "No mask layers were added to avoid cluttering the viewer with empty results."
        )
        return
    viewer.add_labels(labeled_total_puncta, name=f"Total Puncta Mask")
    viewer.add_labels(labeled_total_refined, name=f"Total Refined Puncta Mask")
    napari_show_info(
        f"Condensate segmentation complete: {n_condensates} objects found.")


def _segment_core(pre_processed_image, original_image, cell_masks, cell_df, ball_radius,
                  kurtosis_threshold=-3.0, local_snr_threshold=1.0, global_snr_threshold=1.0,
                  intensity_hwhm_scale=1.17, max_area_fraction=0.25, min_spot_radius=2,
                  fast=True):
    """
    Viewer-free core of the per-cell condensate segmentation loop. Returns
    (labeled_total_puncta, labeled_total_refined). Used both by the interactive
    runner and by the speed-comparison helper so the two never drift.
    """
    CMS_img = cell_mask_stretching(pre_processed_image, cell_masks)
    unique_labels = np.unique(cell_masks)[1:]
    total_puncta_mask = np.zeros_like(cell_masks, dtype=bool)
    total_refined_puncta_mask = np.zeros_like(cell_masks, dtype=bool)
    for label in unique_labels:
        contrast_stretched_img = CMS_img.copy()
        original_img = original_image.copy()
        cell_mask_holder = np.zeros_like(cell_masks)
        cell_mask_holder[cell_masks == label] = 1
        cell_mask_holder = cell_mask_holder.astype(bool)
        refined_puncta_mask, puncta_mask = segment_subcellular_objects(
            original_img, contrast_stretched_img, cell_mask_holder, label, ball_radius, cell_df,
            kurtosis_threshold=kurtosis_threshold, local_snr_threshold=local_snr_threshold,
            global_snr_threshold=global_snr_threshold, intensity_hwhm_scale=intensity_hwhm_scale,
            max_area_fraction=max_area_fraction, min_spot_radius=min_spot_radius,
            refine_fast=fast)
        total_puncta_mask += puncta_mask
        total_refined_puncta_mask += refined_puncta_mask
    return (sk.measure.label(total_puncta_mask.astype(bool)),
            sk.measure.label(total_refined_puncta_mask.astype(bool)))


def compare_segmentation_speed(pre_processed_image_layer, original_image_layer, data_instance, viewer,
                               kurtosis_threshold=-3.0, local_snr_threshold=1.0, global_snr_threshold=1.0,
                               intensity_hwhm_scale=1.17, max_area_fraction=0.25, min_spot_radius=2):
    """
    Run condensate segmentation twice  --  once with the original refinement filter,
    once with the windowed (fast) filter  --  timing each and checking the refined
    masks are identical. Adds the fast-path result layers and reports timings and
    equivalence via a napari notification and stdout. For the Segmentation Speed
    Comparison widget.
    """
    import time as _time
    original_image = original_image_layer.data
    pre_processed_image = pre_processed_image_layer.data
    ball_radius = data_instance.data_repository['ball_radius']

    if 'Labeled Cell Mask' in viewer.layers:
        cell_masks = viewer.layers['Labeled Cell Mask'].data
        cell_df = data_instance.get_data('cell_df', pd.DataFrame())
    else:
        cell_masks = np.ones_like(original_image).astype(int)
        cell_masks[0:2, 0:2] = 0
        cell_df = pd.DataFrame()
        napari_show_warning("No 'Labeled Cell Mask'  --  comparison will run on the whole image.")

    kw = dict(kurtosis_threshold=kurtosis_threshold, local_snr_threshold=local_snr_threshold,
              global_snr_threshold=global_snr_threshold, intensity_hwhm_scale=intensity_hwhm_scale,
              max_area_fraction=max_area_fraction, min_spot_radius=min_spot_radius)

    # Original (slow) path
    _t = _time.perf_counter()
    _, slow_refined = _segment_core(pre_processed_image, original_image, cell_masks, cell_df,
                                    ball_radius, fast=False, **kw)
    t_slow = _time.perf_counter() - _t

    # Windowed (fast) path
    _t = _time.perf_counter()
    fast_puncta, fast_refined = _segment_core(pre_processed_image, original_image, cell_masks, cell_df,
                                              ball_radius, fast=True, **kw)
    t_fast = _time.perf_counter() - _t

    # Equivalence: compare as binary foreground (labels differ in numbering only).
    identical = np.array_equal(slow_refined > 0, fast_refined > 0)
    n_slow = int(slow_refined.max()); n_fast = int(fast_refined.max())
    speedup = (t_slow / t_fast) if t_fast > 0 else float('nan')

    # Show the fast result (what production uses).
    viewer.add_labels(fast_puncta, name="Total Puncta Mask (fast)")
    viewer.add_labels(fast_refined, name="Total Refined Puncta Mask (fast)")
    if not identical:
        # Surface the difference so it can be inspected.
        diff = ((slow_refined > 0) ^ (fast_refined > 0)).astype(np.uint8)
        viewer.add_labels(diff, name="Fast vs Slow DIFF")

    msg = (f"Segmentation speed comparison:\n"
           f"  original: {t_slow:.2f} s  ({n_slow} objects)\n"
           f"  fast:     {t_fast:.2f} s  ({n_fast} objects)\n"
           f"  speedup:  {speedup:.1f}×\n"
           f"  masks identical: {identical}")
    print("[PyCAT] " + msg.replace("\n", "\n[PyCAT] "))
    napari_show_info(msg)
    return {'t_slow': t_slow, 't_fast': t_fast, 'speedup': speedup,
            'identical': identical, 'n_slow': n_slow, 'n_fast': n_fast}
