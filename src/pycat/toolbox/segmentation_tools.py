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


# ---------------------------------------------------------------------------
# Puncta-refinement diagnostic + fast-path flags + _refine_debug_enabled  ->  moved to puncta_refinement.py (1.6.242)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.puncta_refinement import (  # noqa: E402,F401
    _PYCAT_REFINE_DEBUG, _PYCAT_REFINE_FAST, _refine_debug_enabled)



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



# ---------------------------------------------------------------------------
# Puncta refinement filter (SNR/kurtosis/contrast gate) + ring-radii/bg helpers + fast/slow dispatch  ->  moved to puncta_refinement.py (1.6.242)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.puncta_refinement import (  # noqa: E402,F401
    _local_ring_radii, _ring_masks, _robust_bg, _snr_conditions, _report_refinement_drops, puncta_refinement_filtering_func, puncta_refinement_filtering_func_fast, puncta_refinement_func)



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
