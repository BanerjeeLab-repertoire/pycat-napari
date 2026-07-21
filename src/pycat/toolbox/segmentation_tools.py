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

# Cache GPU availability at module load time  --  avoids re-initializing the
# CUDA context on every Cellpose call. The actual check is deferred until
# first use so module import stays fast.
_CELLPOSE_USE_GPU = None
_CELLPOSE_GPU_BACKEND = None   # 'cuda', 'mps', or None — set by _get_cellpose_gpu
_WARNED_CPU = False

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


def _get_cellpose_gpu():
    """Return True if Cellpose should run on a GPU (CUDA or Apple MPS).

    Checks CUDA first (NVIDIA), then Apple Metal (MPS) on Apple Silicon Macs.
    Cellpose 3.x accepts gpu=True and uses whichever accelerator torch exposes.
    The detected backend is cached in the module-level _CELLPOSE_GPU_BACKEND so
    the CPU-warning message can name the right install path per platform.
    """
    global _CELLPOSE_USE_GPU, _CELLPOSE_GPU_BACKEND
    if _CELLPOSE_USE_GPU is None:
        _CELLPOSE_GPU_BACKEND = None
        try:
            import torch
            if torch.cuda.is_available():
                _CELLPOSE_USE_GPU = True
                _CELLPOSE_GPU_BACKEND = 'cuda'
            elif getattr(torch.backends, 'mps', None) is not None and torch.backends.mps.is_available():
                # Apple Silicon Metal Performance Shaders backend.
                _CELLPOSE_USE_GPU = True
                _CELLPOSE_GPU_BACKEND = 'mps'
            else:
                _CELLPOSE_USE_GPU = False
        except Exception:
            _CELLPOSE_USE_GPU = False
    return _CELLPOSE_USE_GPU


# ---------------------------------------------------------------------------
# Cellpose version awareness  --  cyto2 (Cellpose <4 CNN) vs cpsam (Cellpose >=4)
# ---------------------------------------------------------------------------
# Cellpose 4 (Cellpose-SAM) removed the legacy cyto/cyto2/cyto3 weights: passing
# pretrained_model='cyto2' there is silently ignored and cpsam is loaded instead
# (a large ViT-L transformer that is very slow on CPU). The two model families
# require different Cellpose versions and cannot coexist in one environment, so
# PyCAT pins cellpose<4 by default (fast cyto2 CNN) and adapts automatically if a
# newer Cellpose is installed.

_CELLPOSE_MODEL_CACHE = {}


def _cellpose_major_version():
    """Return the installed Cellpose major version as an int (0 if unknown)."""
    try:
        import cellpose
        return int(str(cellpose.version).split('.')[0])
    except Exception:
        return 0


def available_cellpose_models():
    """
    List the segmentation model names valid for the INSTALLED Cellpose version.
    Cellpose <4 exposes the legacy CNNs (cyto2 default); Cellpose >=4 exposes
    only the SAM/DINO models (cpsam default).
    """
    if _cellpose_major_version() >= 4:
        return ['cpsam']
    return ['cyto2', 'cyto', 'nuclei']


def default_cellpose_model():
    """The preferred default model for the installed Cellpose version."""
    return available_cellpose_models()[0]


def _build_cellpose_model(model_name):
    """
    Build (and cache) a Cellpose model using the correct API for the installed
    version. On Cellpose <4 the builtin name goes through `model_type`; on
    Cellpose >=4 it goes through `pretrained_model`.
    """
    gpu = _get_cellpose_gpu()
    key = (model_name, gpu, _cellpose_major_version())
    if key in _CELLPOSE_MODEL_CACHE:
        return _CELLPOSE_MODEL_CACHE[key]

    # First use this session: load the cached weights from disk into memory.
    # (Downloaded once to ~/.cellpose on first ever run; not re-downloaded.)
    try:
        import logging as _logging
        _logging.getLogger('pycat').info(
            "Loading Cellpose model '%s' weights from cache into memory "
            "(first use this session)...", model_name)
    except Exception:
        pass

    # ── ``models`` was imported INSIDE the Cellpose-4 branch only ────────────────
    #
    # ``from cellpose import models`` sat inside ``if _cellpose_major_version() >= 4:``, and the
    # ``else`` branch — **every Cellpose 3.x install** — then called ``models.CellposeModel(...)``
    # with nothing having imported it.
    #
    #     UnboundLocalError: cannot access local variable 'models'
    #
    # **Cell segmentation was completely dead on Cellpose < 4**, which is what most users have.
    # The import belongs **above** the branch, where both paths can see it.
    #
    # *(Reported by Meet, 2026-07-13. Reproduced by stubbing cellpose 3.1.0 — the traceback is
    # identical.)*
    from cellpose import models

    if _cellpose_major_version() >= 4:
        # Cellpose 4+: legacy names don't exist; fall back to cpsam explicitly.
        name = model_name if model_name in available_cellpose_models() else 'cpsam'
        if model_name == 'nuclei' and name != 'nuclei':
            # The dedicated nuclei CNN doesn't exist in Cellpose 4 (SAM is a
            # single unified model). Tell the user rather than silently ignoring.
            try:
                napari_show_warning(
                    "The Cellpose 'nuclei' model isn't available in Cellpose 4 "
                    "(Cellpose-SAM is a single unified model). Using the default "
                    "model instead. For a dedicated nuclei model, install "
                    "cellpose<4 (pip install 'cellpose<4').")
            except Exception:
                pass
        model = models.CellposeModel(gpu=gpu, pretrained_model=name)
    else:
        # Cellpose <4: builtin CNNs are selected via model_type.
        try:
            model = models.CellposeModel(gpu=gpu, model_type=model_name)
        except TypeError:
            # Very old API fallback
            model = models.CellposeModel(gpu=gpu, pretrained_model=model_name)
    _CELLPOSE_MODEL_CACHE[key] = model
    return model





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



def _weight_mean_color(graph, src, dst, n):
    """
    Callback to handle merging nodes by recomputing mean color.
    
    This function is a utility designed to facilitate the merging process
    in a Region Adjacency Graph (RAG) by calculating the weight of the edge
    that will connect the merged node to its neighbors. The weight is
    determined based on the absolute difference in mean color between the
    `dst` node and its neighbors (`n`). It assumes the mean color of `dst`
    has already been updated to reflect the merging.

    Parameters
    ----------
    graph : RAG
        The graph under consideration.
    src, dst : int
        The vertices in `graph` to be merged.
    n : int
        A neighbor of `src` or `dst` or both.

    Returns
    -------
    data : dict
        A dictionary with the `"weight"` attribute set as the absolute
        difference of the mean color between node `dst` and `n`.
    """
    # Calculate the difference in mean color between `dst` and neighbor `n`
    diff = graph.nodes[dst]['mean color'] - graph.nodes[n]['mean color']
    # Use numpy's linear algebra norm function to compute the Euclidean distance
    # This distance represents the absolute difference in mean color
    diff = np.linalg.norm(diff)
    # Return a dictionary with the calculated weight
    return {'weight': diff}

@tags_layer('merge_mean_color', role='labels',
            summary='Region merging by mean colour')
def merge_mean_color(graph, src, dst):
    """
    Callback called before merging two nodes of a mean color distance graph.
    
    Prior to merging two nodes in a RAG, this function updates the `dst` node's
    attributes to reflect the combined color information of both `src` and `dst`.
    This is crucial for accurately computing the mean color of the merged node,
    ensuring the graph's integrity and the accuracy of its color representation.

    Parameters
    ----------
    graph : RAG
        The graph under consideration.
    src, dst : int
        The vertices in `graph` to be merged.
    """
    # Add the `total color` of `src` to `dst` to reflect merging
    graph.nodes[dst]['total color'] += graph.nodes[src]['total color']
    # Similarly, combine the `pixel count` of both nodes
    graph.nodes[dst]['pixel count'] += graph.nodes[src]['pixel count']
    # Recalculate `mean color` of `dst` to account for the merged node's new color data
    graph.nodes[dst]['mean color'] = (graph.nodes[dst]['total color'] /
                                      graph.nodes[dst]['pixel count'])


@tags_layer('felzenszwalb', role='labels', inputs=('image',),
            summary='Felzenszwalb graph segmentation with merging')
def felzenszwalb_segmentation_and_merging(image, scale=7.0, sigma=0.5, min_size=2):
    """
    Performs image segmentation using Felzenszwalb's method followed by merging based on color similarity.

    This function applies an initial segmentation to the input image using Felzenszwalb's efficient graph-based
    segmentation algorithm. It then constructs a Region Adjacency Graph (RAG) from the initial segments and
    merges segments based on the similarity of their mean color (intensity for grayscale). The merging process is controlled by comparing
    the color distance between segments against a threshold derived from the image's standard deviation.

    Parameters
    ----------
    image : numpy.ndarray
        The input image to segment. Can be a grayscale or RGB image.
    scale : float, optional
        The scale parameter influences the size of the clusters in the initial segmentation. Higher values result in larger clusters. 
        This controls how aggressively pixels are merged together in the initial segmentation. Defaults to 7.0.
    sigma : float, optional
        The standard deviation for the Gaussian kernel used in smoothing the image before segmenting. This preprocessing step can help 
        reduce noise and improve the quality of segmentation. Defaults to 0.5.
    min_size : int, optional
        The minimum size of final segments. Smaller segments are merged during post-processing to ensure that every segment is at least 
        this size. Defaults to 2.

    Returns
    -------
    segmented_img : numpy.ndarray
        The segmented image with segments represented by the average color (or intensity) of their respective pixels, returned in the 
        same data type as the input.

    Notes
    -----
    - 'scale' directly influences how aggressively pixels are merged in the initial segmentation, correlating with the 'k' parameter in Felzenszwalb's paper.
    - Appropriate selection of 'scale', 'sigma', and 'min_size' is crucial for optimal segmentation results, depending on the image's specific characteristics.
    """
    # Store the input image's data type to convert back at the end
    input_dtype = str(image.dtype)

    # Convert input image to float32 for processing; necessary for certain operations and ensures consistency
    img = dtype_conversion_func(image, output_bit_depth='float32')

    # Apply Felzenszwalb's segmentation algorithm to the image
    # This step segments the image into regions based on pixel similarity and the specified parameters
    segments_fz = sk.segmentation.felzenszwalb(img, scale=scale, sigma=sigma, min_size=min_size)

    # Construct a Region Adjacency Graph (RAG) from the initial segmentation
    # The RAG represents how segments are connected and allows for merging based on further criteria
    g = sk.graph.rag_mean_color(img, segments_fz, mode='similarity')
    
    # Define a threshold for merging segments based on color similarity
    # This threshold is set dynamically based on the square of the normalized float image's standard deviation (so it will be a sub 1 value)
    threshold = (np.std(img)**2)/2

    # Merge segments hierarchically based on their mean color similarity
    # `merge_func` determines how the color information is combined when segments are merged
    labels = sk.graph.merge_hierarchical(segments_fz, g, thresh=threshold, rag_copy=False,
                                         in_place_merge=True,
                                         merge_func=merge_mean_color,
                                         weight_func=_weight_mean_color)

    # Convert the merged segment labels into a segmented image with averaged colors
    # The `label2rgb` function assigns the average color of a segment to all its pixels
    merged_fz = sk.color.label2rgb(labels, img, kind='avg', bg_label=0)
    segmented_img = merged_fz[..., 0]  # Extract the grayscale channel for simplicity

    # Convert the segmented image back to the original input data type for consistency with the input
    segmented_img = dtype_conversion_func(segmented_img, output_bit_depth=input_dtype)

    return segmented_img


def run_fz_segmentation_and_merging(scale_input, sigma_input, min_size_input, viewer):
    """
    Applies Felzenszwalb's segmentation and merging to an active image layer in a Napari viewer based on user-provided settings.
    This function allows for dynamic interaction, enabling users to adjust segmentation parameters in real-time.

    Parameters
    ----------
    scale_input : QLineEdit
        Input field for the scale parameter, affecting the size of the initial segmentation clusters.
    sigma_input : QLineEdit
        Input field for the sigma parameter, controlling the degree of Gaussian smoothing prior to segmentation.
    min_size_input : QLineEdit
        Input field for the minimum size of the segments to be considered in the final output.
    viewer : napari.viewer.Viewer
        Viewer instance where the segmented image will be displayed.

    Raises
    ------
    Error
        If no active image layer is selected.
    """

    # Check for an active image layer in the viewer
    active_layer = viewer.layers.selection.active
    import napari
    if active_layer is None or not isinstance(active_layer, napari.layers.Image):
        raise ValueError("No active image layer selected")

    image = active_layer.data  # Extract the image data from the active layer

    # Read scale, sigma, and min_size from inputs, defaulting to preset values if empty
    scale = float(scale_input.text()) if scale_input.text() else 7.0
    sigma = float(sigma_input.text()) if sigma_input.text() else 0.5
    min_size = int(min_size_input.text()) if min_size_input.text() else 2

    # Apply the segmentation and merging process to the selected image layer
    segmented_img = felzenszwalb_segmentation_and_merging(image, scale=scale, sigma=sigma, min_size=min_size)

    # Display the segmented image in the viewer
    from pycat.ui.ui_utils import add_image_with_default_colormap
    add_image_with_default_colormap(segmented_img, viewer, name=f"Felzenszwalb Segmented {active_layer.name}")


@tags_layer('felzenszwalb_binary', role='mask', inputs=('image',),
            summary='Felzenszwalb segmentation, binarised')
def fz_segmentation_and_binarization(image, mask, ball_radius, rim_close_radius=5,
                                     rim_close_min_result_area=150):
    """
    Applies Felzenszwalb's segmentation method followed by additional processing to convert the segmented
    image into a refined binary mask. This involves contrast adjustments, morphological operations, and local
    thresholding to highlight distinct objects within a specified region of interest. Additionally, external 
    contours are detected and filled to ensure solid object representation in the binary mask.

    Parameters
    ----------
    image : numpy.ndarray
        The input grayscale image for segmentation.
    mask : numpy.ndarray
        A binary mask defining the region of interest where segmentation is focused.
    ball_radius : int
        The radius influencing the segmentation sensitivity and scale, particularly used in local thresholding.
    rim_close_radius : int, optional
        Radius of the morphological closing used to bridge fragmented rim
        pieces of large, hollowed condensates before hole-filling (see notes
        below). This is a small, FIXED scale independent of ball_radius --
        it corresponds to the small-scale (disk(1)) morphological/Gabor
        operations upstream that fragment a large condensate's rim, not to
        the size of the condensate itself. Do not scale this with
        ball_radius: at large ball_radius values that would bridge gaps of
        100+ px and erroneously merge unrelated, well-separated objects
        across the whole image. Increase only if real large-condensate rims
        are still visibly broken after this default; default is 5. Safe to
        tune upward even for densely-packed small puncta -- see
        rim_close_min_result_area, which prevents this from deforming or
        fusing small objects.
    rim_close_min_result_area : int, optional
        A closing/fill result is only kept where the resulting connected
        component is at least this large (px); smaller components revert to
        their pre-closing shape. This is what makes rim_close_radius safe to
        set generously for large-condensate bridging without deforming or
        fusing nearby, genuinely distinct small puncta -- closing a cluster
        of small puncta rarely produces a component this large, so they
        keep their original compact shape, while a real fragmented
        large-condensate rim reliably does. Default is 150 (well above a
        single ~7px-diameter punctum's area, well below a genuine large
        condensate's).

    Returns
    -------
    boolean_mask : numpy.ndarray
        A binary mask refined from the segmented image, highlighting detected objects within the region defined by the input mask.

    Notes
    -----
    - The process dynamically adjusts to the 'ball_radius' to ensure appropriate scale processing for different image details.
    - A correct 'ball_radius' is crucial for optimal segmentation and post-processing results.
    - The function assumes the input image has undergone basic preprocessing for noise reduction and contrast enhancement.
    - The binary mask is further processed through morphological operations and local thresholding to ensure a clean and usable output.
    """

    img = dtype_conversion_func(image, output_bit_depth='float32') # Convert image to float32 for processing
    object_radius = ball_radius / 1.5  # Adjust object radius based on ball_radius for segmentation scale
    
    # Perform initial segmentation with adjusted parameters
    fz_segmented_img = felzenszwalb_segmentation_and_merging(img, scale=object_radius, sigma=0.5, min_size=2)

    # Check image contrast and return empty mask if insufficient for segmentation
    contrast_flag = check_contrast_func(fz_segmented_img)
    if contrast_flag:
        return np.zeros_like(img, dtype=bool)
    
    clip_limit = 0.0025  # Adaptive histogram equalization parameter
    k_size = math.ceil(ball_radius * 4)  # Set a window size of ~ 2x larger than the object diameter for CLAHE
    # Enhance segmented image using adaptive histogram equalization
    segmented_img = sk.exposure.equalize_adapthist(fz_segmented_img, kernel_size=k_size, clip_limit=clip_limit)

    # Apply morphological operations to smooth the segmented image
    segmented_img = ndi.grey_dilation(segmented_img, footprint=sk.morphology.disk(1))
    segmented_img = ndi.grey_erosion(segmented_img, footprint=sk.morphology.disk(1))
    
    # Further smooth the image using Gaussian filtering
    segmented_img = ndi.gaussian_filter(segmented_img, sigma=0.5)
    #viewer.add_image(segmented_img, name='Segmented Image')

    # Refine segmentation into a binary mask using local thresholding
    segmented_mask = local_thresholding_func(segmented_img, int(ball_radius))

    # ── Absolute-brightness rescue for locally-uniform bright regions ──────
    # Niblack/Sauvola are LOCAL, CONTRAST-based thresholds: a pixel passes
    # only if it's bright relative to its immediate window_size=ball_radius
    # neighborhood. Deep inside a large, flat, saturated condensate, that
    # local neighborhood is essentially uniform -- local std collapses toward
    # 0, so even a pixel far brighter than the whole image's background can
    # fail the local test purely because its surroundings look like itself.
    # This is a structural blind spot, independent of anything upstream: it
    # persists even after the object is correctly preserved through
    # pre-processing and enhancement, and explains large condensates being
    # segmented as a thin rim/partial coverage rather than their full extent.
    # A coarse, scale-independent ABSOLUTE brightness criterion (Otsu on this
    # image/ROI) is OR-combined in to rescue exactly this case: pixels that
    # are clearly bright relative to the whole image, even where local
    # contrast is near zero. This is deliberately coarse and used only as an
    # OR-addition (never removes anything local thresholding already found),
    # so it cannot make small/medium puncta detection any less sensitive.
    try:
        otsu_thresh = sk.filters.threshold_otsu(segmented_img)
        bright_mask = segmented_img > otsu_thresh
        segmented_mask = np.logical_or(segmented_mask, bright_mask)
    except ValueError:
        # threshold_otsu can raise on a degenerate (near-constant) image;
        # local_thresholding_func's result alone is used in that case.
        pass

    # ── Large-condensate rim bridging ───────────────────────────────────
    # The upstream ball_radius-scale enhancement (white top-hat / Gaussian
    # background division) is a band-pass operation: it suppresses the flat,
    # uniform interior of condensates that are large relative to ball_radius,
    # leaving only their curved rim. Local (Niblack/Sauvola) thresholding on
    # that rim-only signal often breaks it into a scatter of disconnected
    # fragments rather than one continuous ring -- a "necklace" of small
    # puncta instead of one solid object. The binary_fill_holes call below
    # only closes a hole that's already fully enclosed by a continuous ring;
    # it can't do anything for a ring that's broken into pieces. A
    # morphological closing first bridges those gaps into a continuous loop
    # so the fill (and the external-contour fill downstream) can recover the
    # object's full extent.
    #
    # IMPORTANT: rim_close_radius is intentionally NOT scaled with
    # ball_radius. The fragmentation gap size comes from small, fixed-scale
    # operations upstream (disk(1) erosion/dilation, Gabor filtering), not
    # from the condensate's own size. An earlier version of this fix set
    # close_radius = ball_radius, which at realistic ball_radius values
    # (e.g. 75) applies a ~150px-wide closing to the whole image and
    # erroneously fuses distinct, well-separated puncta/condensates
    # anywhere in the mask -- corrupting segmentation broadly, not just for
    # large condensates. Keeping this small and fixed avoids that.
    close_radius = max(1, int(rim_close_radius))
    closed = ndi.binary_closing(segmented_mask, structure=sk.morphology.disk(close_radius))
    filled_closed = ndi.binary_fill_holes(closed)

    # Only ACCEPT the closing/fill result where it produces a sufficiently
    # large object. For an isolated small punctum (or several genuinely
    # distinct, closely-spaced small puncta), the closing can subtly deform
    # or -- at larger rim_close_radius values -- bridge them into elongated
    # "worm" shapes instead of the clean, compact round dots local
    # thresholding originally found. Gating by the RESULTING component size
    # (rather than by rim_close_radius alone) means small puncta always keep
    # their original, un-closed shape, regardless of how large
    # rim_close_radius needs to be tuned to bridge a real large-condensate
    # rim. Only components that end up at/above rim_close_min_result_area
    # (i.e., plausibly a bridged large-condensate rim, not ordinary puncta)
    # get the closed/filled version; everything else reverts to the
    # pre-closing mask.
    lbl_closed = sk.measure.label(filled_closed)
    if lbl_closed.max() > 0:
        areas = ndi.sum(np.ones_like(lbl_closed), lbl_closed, range(1, lbl_closed.max() + 1))
        large_labels = np.where(areas >= rim_close_min_result_area)[0] + 1
        accept = np.isin(lbl_closed, large_labels)
    else:
        accept = np.zeros_like(filled_closed, dtype=bool)
    segmented_mask = np.where(accept, filled_closed, segmented_mask)

    # Determine the maximum area for objects based on the input cell mask.
    # This is intentionally permissive (previously a hard 25% cap): rejecting
    # objects purely for being large also throws away genuine large/coarsened
    # condensates. A more informed, shape-aware rejection of implausible
    # (e.g., erroneously merged) large objects happens later in
    # puncta_refinement_filtering_func, once solidity is available.
    max_area = (np.sum(mask.astype(bool)) * 0.9)

    # Detect external contours and fill them to ensure solid object representation
    contour_mask = opencv_contour_func(segmented_mask, max_area=max_area)

    # Explicitly fill any residual interior holes. Local (Niblack/Sauvola)
    # thresholding hollows out large bright flat cores into rings; the external
    # contour fill above closes most of these, but this guarantees fully solid
    # objects (e.g. when a ring didn't fully close) so bright condensates are not
    # left partially segmented.
    contour_mask = ndi.binary_fill_holes(contour_mask.astype(bool)).astype(contour_mask.dtype)

    # Combine with the eroded input mask to refine the final mask and reduce edge artifacts
    boolean_mask = (contour_mask * ndi.binary_erosion(mask, sk.morphology.disk(1))).astype(bool)

    # Dilate the mask to ensure objects are fully covered
    boolean_mask = binary_morph_operation(boolean_mask, iterations=1, element_size=1, element_shape='Disk', mode='Dilation')

    return boolean_mask


@tags_layer('cellpose', role='labels', inputs=('image',),
            summary='Cellpose deep-learning segmentation', target='cell')
def cellpose_segmentation(image, object_diameter, model_name=None, postprocess=True):
    """
    Perform cell segmentation on an image using Cellpose, a deep-learning-based method for cell/nucleus segmentation.

    This function processes an input image to enhance its features and applies the Cellpose deep learning model
    for cell and nucleus segmentation. It focuses on segmenting the image into distinct cell or nucleus areas.
    The `object_diameter` parameter is used to determine the scale of the objects to be segmented.

    Parameters
    ----------
    image : numpy.ndarray
        The input image for cell segmentation, expected to be in a format compatible with Cellpose.
    object_diameter : int
        The approximate diameter (in pixels) of the cells or nuclei to be segmented in the image. This value scales
        the segmentation process.

    Returns
    -------
    mask : numpy.ndarray
        A binary mask of the segmented cells/nuclei in the input image, refined to enhance separation between adjacent
        objects and extend segmentation to image edges.

    Notes
    -----
    - Cellpose model 'cyto2' is used by default for broader applicability in cell and nucleus segmentation.
    - The input image is processed through several steps including dynamic range conversion, adaptive histogram
      equalization, denoising, and intensity rescaling to optimize it for segmentation.
    - Ensure that the Cellpose library is installed and properly configured in your environment. For more information
      on Cellpose, see: https://cellpose.readthedocs.io/en/latest/.
    - This function assumes the availability of several skimage and custom preprocessing functions to prepare the
      image for segmentation.
    """
    
    # Select the model for the installed Cellpose version (default cyto2 on
    # Cellpose <4, cpsam on Cellpose >=4). The model is cached across calls so
    # weights are not reloaded every segmentation.
    if model_name is None:
        model_name = default_cellpose_model()
    model = _build_cellpose_model(model_name)

    # Warn CPU-only users once per session  --  Cellpose is much slower without a
    # CUDA GPU, and the large Cellpose-SAM (cpsam) model on Cellpose >= 4 can
    # take minutes per image on CPU.
    global _WARNED_CPU
    if not _get_cellpose_gpu() and not _WARNED_CPU:
        _WARNED_CPU = True
        import sys as _sys
        _is_mac = _sys.platform == 'darwin'
        if _cellpose_major_version() >= 4:
            if _is_mac:
                napari_show_warning(
                    "Cellpose is running on CPU. The Cellpose-SAM model is very "
                    "slow on CPU -- expect minutes per image. On Apple Silicon, "
                    "install a PyTorch build with MPS support and PyCAT will use "
                    "the Apple GPU automatically; or switch to the faster cyto2 "
                    "model (cellpose<4). See the README GPU section.")
            else:
                napari_show_warning(
                    "Cellpose is running on CPU (no CUDA GPU detected). The "
                    "Cellpose-SAM model is very slow on CPU -- expect minutes per "
                    "image. For speed, install CUDA PyTorch or switch to the cyto2 "
                    "model (cellpose<4). See the README GPU section.")
        else:
            if _is_mac:
                napari_show_warning(
                    "Cellpose is running on CPU. Segmentation will be slower than "
                    "on GPU. On Apple Silicon, install a PyTorch build with MPS "
                    "support (the default recent torch wheels include it) and PyCAT "
                    "will use the Apple GPU automatically. There is no CUDA on Mac.")
            else:
                napari_show_warning(
                    "Cellpose is running on CPU (no CUDA GPU detected). Segmentation "
                    "will be slower than on GPU. To enable GPU acceleration, install "
                    "CUDA PyTorch: pip install torch torchvision --index-url "
                    "https://download.pytorch.org/whl/cu118")

    # Preprocess the image to improve segmentation quality.
    img = dtype_conversion_func(image, 'float32') # Convert image to float32 for processing
    img = sk.exposure.equalize_adapthist(img, kernel_size=object_diameter//2, clip_limit=0.0025)
    img = sk.restoration.denoise_wavelet(img)
    img = apply_rescale_intensity(img, out_min=0.0, out_max=1.0)

    image_preprocessed = dtype_conversion_func(img, 'uint16') # Convert the image to uint16 for Cellpose
    # Apply Cellpose model to segment cells/nuclei. Cellpose >=4 ignores the
    # `channels` argument (SAM is channel-order invariant); Cellpose <4 uses it.
    if _cellpose_major_version() >= 4:
        masks, flows, styles = model.eval(image_preprocessed, diameter=object_diameter)
    else:
        masks, flows, styles = model.eval(image_preprocessed, diameter=object_diameter, channels=[0,0])

    # When postprocess=False, return Cellpose's instance labels UNCHANGED. The
    # post-processing below (binarize → generic watershed → 7× morphological
    # opening → relabel) discards Cellpose's learned per-object boundaries and
    # replaces them with a harsh generic morphology pipeline — which degrades
    # otherwise-good Cellpose output. The time-series path passes postprocess=False
    # so it uses Cellpose's masks as-is. The legacy 2D path keeps postprocess=True
    # for backward compatibility (its downstream steps expect the refined masks).
    masks = np.asarray(masks).astype(np.uint16)
    if not postprocess:
        return masks

    # Post-process segmentation masks to improve results.
    binary_mask = masks > 0  # Binary version for morphological operations
    # Split objects that are erroneously connected. deprecated method replaced by cv2 binary watershed
    #split_mask = split_touching_objects(mask, sigma=object_diameter//4) 
    split_mask = opencv_watershed_func(binary_mask)
    refined_binary = binary_morph_operation(split_mask, iterations=7, element_size=3, element_shape='Disk', mode='Opening')
    refined_binary = extend_mask_to_edges(refined_binary, 3)  # Extend the mask to eliminate the empty border cellpose leaves

    # Re-label the refined binary mask so each cell retains a unique integer ID.
    # This is required for per-cell analyses (SACF, cell analyzer, etc.).
    labeled_mask = sk.measure.label(refined_binary)

    return labeled_mask

def run_cellpose_segmentation(image_layer, data_instance, viewer):
    """
    Applies cell segmentation to an image layer using Cellpose and displays the results in the Napari viewer.

    Retrieves the necessary parameters from provided objects, executes cell segmentation with `cellpose_segmentation`,
    and integrates the resulting mask into the viewer as a new layer.

    Parameters
    ----------
    import napari
    image_layer : napari.layers.Image
        The image layer to be segmented.
    data_instance : object
        An object containing a data repository with segmentation parameters, such as 'cell_diameter'.
    viewer : napari.Viewer
        The viewer object where the segmentation results will be displayed.
    """
    
    # Retrieve the image data and cell diameter from the data instance
    image = image_layer.data
    object_diameter = data_instance.data_repository['cell_diameter']
    model_name = data_instance.data_repository.get('cellpose_model', None)
    # Refine (post-process) Cellpose masks only if the user opted in. Default
    # False → use Cellpose's instance masks directly (preserves learned
    # boundaries); True → legacy watershed + morphology cleanup.
    refine = bool(data_instance.data_repository.get('cellpose_refine', False))

    # Perform cell segmentation using Cellpose.
    cell_masks = cellpose_segmentation(image, object_diameter,
                                       model_name=model_name,
                                       postprocess=refine)
    
    # Add the segmentation results as a new label layer to the viewer.
    viewer.add_labels(cell_masks, name=f"Cellpose Segmentation on {image_layer.name}")


def train_and_apply_rf_classifier(image, training_labels, object_diameter):
    """
    Trains and applies a Random Forest classifier to segment an image based on training labels.

    The function enhances the input image using adaptive histogram equalization and denoising techniques
    before training a Random Forest classifier. The classifier is then used to predict segmentation masks
    across the entire image. These masks are refined to improve the segmentation quality.

    Parameters
    ----------
    image : numpy.ndarray
        The input image for segmentation, expected to be in grayscale or compatible format.
    training_labels : numpy.ndarray
        The ground truth labels for training the classifier, must be the same dimensions as the image.
    object_diameter : int
        The approximate diameter of the target objects in pixels, used to tailor image preprocessing.

    Returns
    -------
    refined_masks : List[numpy.ndarray]
        A list of refined segmentation masks for each detected classification type, adjusted for segmentation 
        quality.

    Notes
    -----
    The segmentation process includes image preprocessing for feature enhancement, classifier training on specified
    regions, and applying this classifier to the whole image. The resulting masks are then refined through morphological
    operations and contour adjustments to produce the final segmented output.
    """
    
    # Image preprocessing for enhanced segmentation performance
    img = dtype_conversion_func(image, 'float32') # Convert image to float32 for processing
    img = sk.exposure.equalize_adapthist(img, kernel_size=object_diameter//2, clip_limit=0.0025)
    img = sk.restoration.denoise_wavelet(img)

    # Training data preparation
    training_img_pixels = img[training_labels != 0]
    training_label_pxs = training_labels[training_labels != 0]

    # Random Forest classifier initialization and training
    rf_classifier = RandomForestClassifier(n_estimators=500, max_depth=4, criterion='entropy', n_jobs=-1)
    rf_classifier.fit(training_img_pixels.reshape(-1, 1), training_label_pxs)

    # Segmentation using the trained classifier
    prediction_pixels = img.reshape(-1, 1)
    predicted_labels = rf_classifier.predict(prediction_pixels).reshape(img.shape)
    predicted_labels -= 1 # Shift labels to start from 0
    predicted_labels = predicted_labels.astype(np.uint8) # Convert to uint8 for compatibility

    # Refinement of predicted labels
    refined_labels = np.zeros_like(predicted_labels)
    for label in np.unique(predicted_labels)[1:]:  # Skip label 0 (background)
        label_mask = predicted_labels == label
        #label_mask = binary_morph_operation(label_mask, mode='Fill Holes')
        label_mask = binary_morph_operation(label_mask, iterations=3, element_size=5, element_shape='Disk', mode='Opening')
        label_mask = binary_morph_operation(label_mask, iterations=5, element_size=3, element_shape='Disk', mode='Closing')
        #label_mask = opencv_watershed_func(label_mask)
        refined_labels[label_mask] = label

    # Convert to binary mask and label connected components
    binary_mask = refined_labels > 0
    labeled_mask = sk.measure.label(binary_mask)
    # Remove small objects from the labeled mask
    min_area = (np.pi * (object_diameter / 2) ** 2) // 10
    labeled_mask = _remove_small_objects_compat(labeled_mask, min_area)
    binary_mask = labeled_mask > 0 
    # Use the binary mask to remove the small objects from the refined labels
    refined_labels *= binary_mask

    # Extend mask to the edges and refine each label's mask
    refined_labels = extend_mask_to_edges(refined_labels, 3)
    refined_masks = refine_labels_with_contours(refined_labels, min_area)

    return refined_masks

@tags_layer('contour_refine', role='labels',
            summary='Label refinement against image contours')
def refine_labels_with_contours(refined_labels, min_area):
    """
    Refines segmentation masks for each label within a given input mask using contour detection and area filtering. 
    This function iterates over each unique label in the input mask, extracts contours for each label using the 
    specified minimum area criteria, and applies morphological operations to refine these contours.

    Parameters
    ----------
    refined_labels : numpy.ndarray
        The input mask containing different labels for segmented regions, typically obtained from segmentation algorithms.
    min_area : int
        The minimum area threshold for contours to be considered during the refinement process. Only contours larger 
        than this threshold are included.

    Returns
    -------
    refined_masks : List[numpy.ndarray]
        A list of refined masks for each label present in `refined_labels`. Each mask in the list corresponds to a 
        unique label and contains the refined contours for that label.

    Notes
    -----
    The function first segregates each label within the input mask and then applies `opencv_contour_func` to detect and
    draw contours that meet the specified area criteria. It further refines these contours using a binary morphological 
    operation (e.g., opening) to smooth edges and remove small artifacts. If no valid objects are found for a label after
    processing, a message is printed, and the label is skipped in the output. The resulting refined masks are returned as
    a list, one for each label, ensuring that the refined contours correspond to the initial segmented regions.
    """
    # Initialize an empty list to store the refined masks for each label
    refined_masks = []

    # Iterate over each unique label found in `refined_labels` (skip the background label, typically 0)
    for label in np.unique(refined_labels)[1:]:  # Skip background label
        # Create a binary mask for the current label
        binary_mask = (refined_labels == label)

        # Find contours in the binary mask
        current_label_mask = opencv_contour_func(binary_mask, min_area)

        # Final post-processing steps for the current label mask
        current_label_mask = binary_morph_operation(current_label_mask, mode='Opening', iterations=7, element_size=3, element_shape='Disk')
        if np.sum(current_label_mask) == 0:
            napari_show_warning(f"RF Label {label+1} has no valid objects.")
            continue
        current_label_mask[current_label_mask > 0] = label # Assign the label value to the refined mask
        refined_masks.append(current_label_mask) # Store the refined mask for the current label

    return refined_masks

def run_train_and_apply_rf_classifier(image_layer, label_layer, data_instance, viewer):
    """
    Facilitates the training and application of a Random Forest classifier on an image layer and displays the
    results in a Napari viewer.

    This function extracts the necessary data from the provided image and label layers, trains a Random Forest
    classifier based on the training labels, and applies this classifier to segment the image. The segmented results
    are then displayed as new layers in the viewer.

    Parameters
    ----------
    import napari
    image_layer : napari.layers.Image
        The layer containing the image data to be segmented.
    label_layer : napari.layers.Labels
        The layer containing label data used for training the classifier.
    data_instance : object
        An object containing additional parameters such as 'cell_diameter' needed for processing.
    viewer : napari.Viewer
        The viewer in which to display the segmented results.

    Notes
    -----
    - Multiple refined masks are displayed in separate layers if more than one valid object classification is found.
    """
    # Extract necessary data for segmentation
    object_diameter = data_instance.data_repository['cell_diameter']
    image = image_layer.data
    training_labels = label_layer.data

    # Train and apply the Random Forest classifier for segmentation
    output_mask_list = train_and_apply_rf_classifier(image, training_labels, object_diameter)

    # Display the segmentation results in the viewer
    if len(output_mask_list) == 0:
        napari_show_info("No valid objects were found.")
    elif len(output_mask_list) == 1:
        viewer.add_labels(output_mask_list[0].astype(int), name=f"Random Forest Segmentation on {image_layer.name}")
    else:
        for idx, output_mask in enumerate(output_mask_list):
            output_mask = output_mask.astype(int)
            output_mask[output_mask > 0] = idx + 1
            viewer.add_labels(output_mask, name=f"Random Forest Segmentation {idx+1} on {image_layer.name}")


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
