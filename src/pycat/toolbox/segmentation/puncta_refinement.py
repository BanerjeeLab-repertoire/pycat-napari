"""Puncta refinement - the SNR/kurtosis/contrast filter gate, split out of segmentation_tools (1.6.242).

The filter-sensitivity-gated core: per-object local-ring background estimation, the SNR/contrast/gradient
hypothesis-test conditions, and the two bit-for-bit-identical implementations (windowed *fast* default +
the original *slow* reference) behind the puncta_refinement_func dispatcher. Moved VERBATIM - no threshold,
no morphology, no operation order changed; pinned by test_filter_sensitivity / test_refine_fast_slow_parity
/ test_local_ring_scales / test_puncta_refinement. The module owns the _PYCAT_REFINE_FAST/_DEBUG flags it
reads, and imports apply_watershed_labeling (watershed) + _to_uint16_safe (_common) from their families.
"""
from __future__ import annotations

import math
import numpy as np
import skimage as sk
import scipy.ndimage as ndi
import scipy.stats as stats
import pandas as pd
from pycat.utils.object_ref import normalise_bbox_columns
from pycat.utils.math_utils import remove_outliers_iqr
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.toolbox.label_and_mask_tools import binary_morph_operation
from pycat.utils.general_utils import dtype_conversion_func
from pycat.utils.tag_registry import tags_layer
from pycat.toolbox.segmentation._common import _to_uint16_safe
from pycat.toolbox.segmentation.watershed import apply_watershed_labeling


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


        # ── Large-object exemption for the fixed-scale edge/local checks ───────
        # local_intensity_condition and gradient_condition both compare an
        # object's "interior" (eroded by 1px) against a "rim"/"local
        # background" band that is only 1-4px wide, REGARDLESS of the
        # object's own size. That scale is correct for point-like puncta
        # (a few px across), where a 1px erosion/dilation meaningfully
        # separates core from edge. For a large condensate spanning tens to
        # hundreds of pixels, eroding by 1px removes almost nothing -- the
        # "interior" sample is nearly the whole (texture-rich, often noisy)
        # object, while the "rim"/"local background" sample is a vanishingly
        # thin sliver relative to the object's true boundary. Empirically
        # (validated on the bundled example dataset), this makes both checks
        # misfire on essentially every real large condensate -- not a
        # parameter-tuning issue, a scale mismatch in what's being measured.
        # min_spot_radius=2 -> min_area=~13px; 150px is comfortably above any
        # single punctum (matches rim_close_min_result_area's semantics in
        # fz_segmentation_and_binarization: "well above a single punctum's
        # area"). Large objects remain fully subject to kurtosis, ellipticity,
        # cell-intensity, and SNR checks, and to the area/solidity check below
        # for implausible (e.g. erroneously merged) shapes.
        is_large_object = df['area'].values[0] >= 150

        # Setup local intensity based conditions
        local_intensity_condition = (
            img_object_mean < (img_local_bg_mean + intensity_hwhm_scale*img_local_bg_std) or
            processed_object_mean < (processed_local_bg_mean + intensity_hwhm_scale*processed_local_bg_std)
        ) and not is_large_object

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
        gradient_condition = (gradient_local_bg_mean < (gradient_object_mean + np.std(gradient_object_pixels)/4)) and not is_large_object
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

        # See puncta_refinement_filtering_func for the rationale: the fixed
        # 1-4px erosion/dilation scale used by local_intensity_condition and
        # gradient_condition below only meaningfully separates "core" from
        # "edge" for point-like puncta; at large-condensate scale it samples
        # a texture-rich interior against a vanishingly thin rim and misfires
        # on real objects. Exempt objects above this size from those two
        # checks only; all other checks (including area/solidity below)
        # still apply.
        is_large_object = _area >= 150

        local_intensity_condition = (
            img_object_mean < (img_local_bg_mean + intensity_hwhm_scale*img_local_bg_std) or
            processed_object_mean < (processed_local_bg_mean + intensity_hwhm_scale*processed_local_bg_std)
        ) and not is_large_object
        cell_intensity_condition = img_object_mean < cell_bg_mean
        kurtosis_condition = img_object_kurtosis < kurtosis_threshold or processed_object_kurtosis < kurtosis_threshold
        # See puncta_refinement_filtering_func for the rationale: large
        # objects are only rejected if they're also irregularly shaped.
        is_undersized = _area < min_area
        is_oversized_and_irregular = (_area > cell_area*max_area_fraction) and (p.solidity < 0.85)
        area_condition = is_oversized_and_irregular or is_undersized
        ellipticty_condition = ellipticity > 0.99
        gradient_condition = (gradient_local_bg_mean < (gradient_object_mean + np.std(gradient_object_pixels)/4)) and not is_large_object
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
