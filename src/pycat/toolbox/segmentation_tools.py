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
import skimage as sk
import cv2
import scipy.ndimage as ndi
import scipy.stats as stats
import pandas as pd
from cellpose import models
from sklearn.ensemble import RandomForestClassifier
import napari
from napari.utils.notifications import show_info as napari_show_info
from napari.utils.notifications import show_warning as napari_show_warning

# Local application imports
from pycat.toolbox.label_and_mask_tools import binary_morph_operation, opencv_contour_func, extend_mask_to_edges
from pycat.ui.ui_utils import refresh_viewer_with_new_data, add_image_with_default_colormap
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


def _to_uint16_safe(image):
    """Convert any image to uint16 without clipping float values outside [-1, 1].

    ``sk.util.img_as_uint`` requires float input in [-1, 1] and raises or clips
    outside that range. Background-removed and CLAHE-processed images are often
    float32 with values well outside that range. This helper normalises the input
    to [0, 1] first so the uint16 conversion is always valid, preserving relative
    intensity differences (which is all the refinement filter needs for kurtosis,
    std-dev, and SNR checks).
    """
    arr = np.asarray(image, dtype=np.float32)
    mn, mx = float(arr.min()), float(arr.max())
    if mx - mn < 1e-12:
        return np.zeros_like(arr, dtype=np.uint16)
    normed = (arr - mn) / (mx - mn)
    return sk.util.img_as_uint(normed)

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





def local_thresholding_func(image, window_size, k_val=-0.5, mode='AND'):
    """
    Applies local thresholding on the input image based on the specified method and parameters.
    Local thresholding is applied using either the Niblack or Sauvola method, or a combination thereof,
    to produce a binary mask that highlights regions of interest in the image based on local pixel value variations.

    Parameters
    ----------
    image : numpy.ndarray
        The input grayscale image to undergo thresholding.
    window_size : int
        Size of the window for local threshold calculations. Adjusted to the nearest odd number if even.
    k_val : float, optional
        The parameter influencing the threshold computation for both Niblack and Sauvola methods. Defaults to -0.5.
    mode : str, optional
        Specifies the thresholding method or the combination of binary masks. Valid options are 'Niblack', 'Sauvola',
        'AND' (intersection of Niblack and Sauvola masks), and 'OR' (union of Niblack and Sauvola masks). Defaults to 'AND'.

    Returns
    -------
    thresh_mask : numpy.ndarray
        Binary mask from the applied thresholds, indicating areas of interest (1) against the background (0).

    Raises
    ------
    ValueError
        If the mode provided is not supported.

    Example
    -------
    Applying combined Niblack and Sauvola thresholding with a window size of 15 and a k-value of -0.5:
    >>> image = np.random.rand(100, 100)
    >>> mask = local_thresholding_func(image, 15, -0.5, 'AND')
    >>> mask.shape
    (100, 100)
    """
    # Ensure window size is odd to meet the thresholding function requirements
    window_size = int(window_size)
    if window_size % 2 == 0:
        window_size += 1  # Adjust to the next odd number if even

    # Compute thresholds and binary masks using Niblack and/or Sauvola methods as required by the mode
    if mode in ['AND', 'OR', 'Niblack']:
        # Calculate Niblack threshold and apply to image
        thresh_niblack = sk.filters.threshold_niblack(image, window_size=window_size, k=k_val)
        binary_niblack = image > thresh_niblack  # Create binary mask
    if mode in ['AND', 'OR', 'Sauvola']:
        # Calculate Sauvola threshold and apply to image
        thresh_sauvola = sk.filters.threshold_sauvola(image, window_size=window_size, k=k_val)
        binary_sauvola = image > thresh_sauvola  # Create binary mask

    # Combine or select the masks based on the mode specified by the user
    if mode == 'AND':
        # Logical AND combines the masks, keeping only overlapping true regions
        thresh_mask = np.logical_and(binary_niblack, binary_sauvola)
    elif mode == 'OR':
        # Logical OR combines the masks, including any true regions from either
        thresh_mask = np.logical_or(binary_niblack, binary_sauvola)
    elif mode == 'Niblack':
        thresh_mask = binary_niblack  # Use Niblack mask directly
    elif mode == 'Sauvola':
        thresh_mask = binary_sauvola  # Use Sauvola mask directly
    else:
        # Handle unsupported modes by raising an error
        raise ValueError("Invalid mode. Supported modes are 'Niblack', 'Sauvola', 'AND', and 'OR'.")

    # Optional: Apply morphological operations based on mode to refine the mask further
    if mode in ['AND', 'OR']:
        # The type of morphological operation could depend on the combined method
        operation_mode = 'Opening' if mode == 'AND' else 'Closing'
        thresh_mask = binary_morph_operation(thresh_mask, iterations=3, element_size=1, element_shape='Disk', mode=operation_mode)

    return thresh_mask

def run_local_thresholding(k_slider, window_slider, mode_dropdown, viewer):
    """
    Applies local thresholding to an active image layer in a Napari viewer based on user inputs from sliders and a dropdown menu.
    The process uses either Niblack, Sauvola, or a combination of these methods to highlight areas of interest in the image.

    Parameters
    ----------
    k_slider : QSlider
        A slider widget to set the k-value for thresholding, adjusting the sensitivity of the method.
    window_slider : QSlider
        A slider widget to set the window size for local threshold calculations.
    mode_dropdown : QComboBox
        A dropdown to select the thresholding mode: 'Niblack', 'Sauvola', 'AND', or 'OR'.
    viewer : napari.viewer.Viewer
        The viewer instance where the processed image will be displayed.

    Raises
    ------
    Error
        If no active image layer is selected.

    Notes
    -----
    This function retrieves settings from the sliders and dropdown, applies the thresholding to the selected image,
    and updates the viewer by either adding a new layer or updating an existing one with the processed image.
    """

    # Convert slider value to k-value and retrieve window size
    k_value = (k_slider.value() * 0.01) - 0.5  # Adjust slider value to k-value range
    window_size = window_slider.value()  # Directly use slider value for window size

    # Identify the currently active layer
    active_layer = viewer.layers.selection.active
    current_active_layer_name = active_layer.name  # Store name for later use

    # Verify active layer is a Napari image layer
    if active_layer is not None and isinstance(active_layer, napari.layers.Image):
        image = active_layer.data  # Extract image data for processing
    else:
        # If no valid image layer is active, raise an error
        napari_show_warning("No active image layer selected.")
        return

    # Apply local thresholding to the image and convert result to integer for display
    thresh_mask = local_thresholding_func(image, window_size, k_val=k_value, mode=mode_dropdown).astype(int)

    # Update or add the processed layer to the viewer
    layer_name = f'Locally Thresholded {current_active_layer_name}'  # Name for the new or updated layer
    existing_layer = next((layer for layer in viewer.layers if layer.name == layer_name), None)
    if existing_layer:
        # Update existing layer with the thresholded mask
        refresh_viewer_with_new_data(viewer, existing_layer, thresh_mask)  # Refresh the viewer to display changes
    else:
        viewer.add_labels(thresh_mask, name=layer_name)  # Add new layer

    # Reset the active layer to update it in the viewer
    viewer.layers.selection.active = viewer.layers[current_active_layer_name]


def apply_watershed_labeling(original_image, binary_mask, sigma=1.5):
    """
    Apply watershed segmentation to an image for labeling different segments. The segmentation
    is based on a binary mask that defines the regions of interest. The function first converts
    the original image to a suitable dtype, applies Gaussian filtering to smooth the image,
    calculates the distance transform of the binary mask, and then performs the watershed
    segmentation on the smoothed distance map. Finally, it refines the segmentation by a binary
    morphological operation and labels the segments.

    Parameters
    ----------
    original_image : numpy.ndarray
        The original image to be segmented. It can be of any dimensional shape.
    binary_mask : numpy.ndarray
        A binary mask defining the regions of interest in the `original_image`. It must have
        the same shape as `original_image`.
    sigma : float, optional
        The sigma value for the Gaussian filter applied to the distance transform. This
        controls the amount of smoothing. Default is 1.5.

    Returns
    -------
    labeled_segments : numpy.ndarray
        An array of the same shape as `original_image` and `binary_mask`, containing labels
        for different segments identified by the watershed algorithm.

    Notes
    -----
    The watershed algorithm is sensitive to the number of local maxima in the distance
    transform, which are used as markers. The sigma parameter can be adjusted to control
    the smoothing applied to the distance transform, thus influencing the segmentation
    result. This function utilizes a disk-shaped structuring element for the final morphological
    operation to refine the segmentation. The size and shape of this element can be adjusted
    for different applications.

    Examples
    --------
    >>> original_image = np.array([...])  # Some image data
    >>> binary_mask = np.array([...])    # A binary mask for the image
    >>> labeled_segments = apply_watershed_labeling(original_image, binary_mask, sigma=1.5)
    """
    
    # Convert the original image to 16-bit unsigned integers for processing.
    # Use _to_uint16_safe to handle float images outside [-1,1] correctly.
    image = _to_uint16_safe(original_image)
    
    # Ensure the binary mask is a boolean array
    binary_mask = np.asarray(binary_mask).astype(bool)

    # Compute the distance transform of the binary mask
    distance = ndi.distance_transform_edt(binary_mask)
    # Apply Gaussian filter to the distance transform with user-defined sigma
    blurred_distance = ndi.gaussian_filter(distance, sigma=sigma)
    
    # Identify local maxima in the blurred distance map as markers for watershed
    max_coords = sk.feature.peak_local_max(blurred_distance, labels=binary_mask)
    local_maxima = np.zeros_like(image, dtype=bool)
    local_maxima[tuple(max_coords.T)] = True
    
    # Label the local maxima
    markers = sk.measure.label(local_maxima)
    # Apply the watershed algorithm using the negative blurred distance map and markers
    labels = sk.segmentation.watershed(-blurred_distance, markers, mask=binary_mask, watershed_line=True)

    # Create a mask where labels are assigned (segmented regions)
    agreement_mask = labels > 0

    # Refine the segmentation with a binary morphological operation
    agreement_mask = binary_morph_operation(agreement_mask, iterations=3, element_size=1, element_shape='Disk', mode='Opening')

    # Label the refined segments
    labeled_segments = sk.measure.label(agreement_mask)

    return labeled_segments


def opencv_watershed_func(binary_mask, original_image=None, dist_thresh=0.5, sigma=3.5, dilation_size=2, dilation_iterations=3):
    """
    Applies the Watershed algorithm to segment objects from a binary mask of an image. This function refines the binary
    mask using morphological operations, applies a distance transform, and uses the Watershed algorithm to delineate
    separate objects. Optionally, the algorithm can utilize the original image for improved segmentation accuracy.

    Parameters
    ----------
    binary_mask : numpy.ndarray
        A binary mask where the contours are to be detected and drawn. The mask should be in a format compatible
        with OpenCV (usually a binary image).
    original_image : numpy.ndarray, optional
        The original intensity image which, if provided, should match the dimensions of `binary_mask`.
    dist_thresh : float, optional
        Threshold for the distance transform, specified as a fraction of its maximum value. Defaults to 0.5.
    sigma : float, optional
        The standard deviation for Gaussian filtering, used to smooth the distance transform and the original
        image if provided. Defaults to 3.5.
    dilation_size : int, optional
        The size of the structuring element used for dilation, which helps define sure background areas.
        Defaults to 2.
    dilation_iterations : int, optional
        The number of iterations for dilation, used to enhance the background determination. Defaults to 3.

    Returns
    -------
    watershed_contours : numpy.ndarray
        A binary mask indicating the boundary contours of segmented objects, with the same dimensions as the input
        `binary_mask`.

    Raises 
    ------
    ValueError
        If the dimensions of the original image and binary mask do not match.

    Notes
    -----
    The function performs an initial morphological opening to clean up small noise in the mask, followed by dilation
    to determine sure background areas. It applies a Gaussian blur to smooth the distance transform and optionally
    the original image, uses a threshold to identify sure foreground areas, and subtracts the foreground from the
    background to define regions of uncertainty. The Watershed algorithm is then applied using either the original
    image or the refined mask, depending on the input provided. The resulting segmented boundaries are returned as
    a binary mask.
    """

    # Ensure binary_mask is boolean
    binary_mask = binary_mask.astype(bool)

    # Apply morphological opening to clean up small noise in the mask
    mask = binary_morph_operation(binary_mask, iterations=3, element_size=2, element_shape='Disk', mode='Opening')
    
    # Dilation to find sure background area
    sure_bg = binary_morph_operation(mask, iterations=dilation_iterations, element_size=dilation_size, element_shape='Disk', mode='Dilation')

    # Convert mask to uint8 for distance transform
    mask = mask.astype(np.uint8) * 255
    mask_copy = mask.copy()

    # Compute the distance transform
    dist_transform = cv2.distanceTransform(mask, cv2.DIST_L2, 5)

    # Apply Gaussian blur to the distance transform
    #dist_transform = cv2.GaussianBlur(dist_transform, (5,5), sigma, sigma)
    dist_transform = ndi.gaussian_filter(dist_transform, sigma=sigma)

    # Thresholding the distance transform to find sure foreground area
    ret, sure_fg = cv2.threshold(dist_transform, dist_thresh * dist_transform.max(), 255, 0)
    sure_fg = np.uint8(sure_fg)

    # Ensure the sure background matches the sure foreground format
    sure_bg = np.uint8(sure_bg) * 255

    # Find unknown region by subtracting foreground from background
    unknown_region = cv2.subtract(sure_bg, sure_fg)

    # Mark connected components in the foreground
    ret, markers = cv2.connectedComponents(sure_fg)
    markers += 1  # Increment all labels so background is not 0, but 1
    markers[unknown_region == 255] = 0  # Mark unknown regions with zero

    # Process the original image if it is provided
    if original_image is not None:
        if original_image.shape[:2] != mask.shape[:2]:
            raise ValueError("The original image and mask must have the same dimensions.")

        # Apply Gaussian filtering and normalize the original image
        image = ndi.gaussian_filter(original_image, sigma=sigma)
        image = (image - np.min(image)) / (np.max(image) - np.min(image)) * 255
        image = image.astype(np.uint8)
        image_bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

        # Apply watershed using the processed original image
        watershed_markers = cv2.watershed(image_bgr, markers)
    else:
        # Apply watershed using the binary mask
        watershed_markers = cv2.watershed(cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR), markers)

    # Extract contours from the watershed markers
    contours_list = []
    for label in np.unique(watershed_markers)[2:]:  # Skip the background and border
        target = np.where(watershed_markers == label, 255, 0).astype(np.uint8)
        contours, _ = cv2.findContours(target, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours_list.append(contours[0])

    # Draw contours on the mask copy
    watershed_contours = mask_copy.copy()
    watershed_contours = cv2.drawContours(watershed_contours, contours_list, -1, 0, thickness=2)

    # Return the final contours as a binary mask
    return watershed_contours.astype(bool)


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
    add_image_with_default_colormap(segmented_img, viewer, name=f"Felzenszwalb Segmented {active_layer.name}")


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
    labeled_mask = sk.morphology.remove_small_objects(labeled_mask, min_size=min_area)
    binary_mask = labeled_mask > 0 
    # Use the binary mask to remove the small objects from the refined labels
    refined_labels *= binary_mask

    # Extend mask to the edges and refine each label's mask
    refined_labels = extend_mask_to_edges(refined_labels, 3)
    refined_masks = refine_labels_with_contours(refined_labels, min_area)

    return refined_masks

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
    properties = ('label', 'area', 'intensity_mean', 'axis_major_length', 'axis_minor_length', 'solidity')
    puncta_region_props_df = pd.DataFrame(sk.measure.regionprops_table(labeled_puncta_mask, intensity_image=original_img, properties=properties))
    cell_area = np.sum(cell_mask)
    
    # Analyze each object individually
    for label in np.unique(labeled_puncta_mask)[1:]:
        # Create a binary mask for each object
        puncta_mask_holder = labeled_puncta_mask == label
        # Erode the mask for the gradient image
        eroded_puncta_holder = ndi.binary_erosion(puncta_mask_holder, sk.morphology.disk(1))
        # Dilate the mask (encompases more of the full spot fluorescence to aviod its tails in the local bg)
        dilated_puncta_holder = ndi.binary_dilation(puncta_mask_holder, sk.morphology.disk(1))
        # Dilate the mask by 3 pixels for local bg aroud the object
        dilated_local_mask = puncta_mask_holder.copy()
        for _ in range(3):
            dilated_local_mask = ndi.binary_dilation(dilated_local_mask, sk.morphology.disk(1)) # diamond element gives smaller dilations
        # The local bg is simply the dilated mask minus the puncta mask
        local_bg_mask = dilated_local_mask ^ dilated_puncta_holder

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
        # Setup 'local' SNR condition
        local_snr_condition = (img_dilated_object_mean/(img_local_bg_std+np.finfo(np.float32).eps)) <= local_snr_threshold
        # Setup 'global' SNR condition
        global_snr_condition = (img_dilated_object_mean/(cell_bg_std+np.finfo(np.float32).eps)) <= global_snr_threshold

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
            # Diagnostic: log why each object was dropped. Enabled by setting the
            # module-level _PYCAT_REFINE_DEBUG flag (or env PYCAT_REFINE_DEBUG=1).
            # Reports object area and the condition(s) that fired, so a dropped
            # bright condensate can be traced to the exact failing check rather
            # than guessed at.
            if _refine_debug_enabled():
                _a = int(df['area'].values[0])
                print(f"[PyCAT refine] dropped label {int(label)} "
                      f"(area={_a}px, obj_mean={img_object_mean:.0f}): "
                      f"{', '.join(_reasons)}")


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
    _disk1 = sk.morphology.disk(1)
    H, W = labeled_puncta_mask.shape

    for p in props:
        label = p.label
        # Object bounding box, padded by 4 px to contain the 3-step dilation ring
        # (3 dilations + the extra dilated_puncta_holder) with margin to spare.
        r0, c0, r1, c1 = p.bbox  # (min_row, min_col, max_row, max_col)
        pad = 4
        rr0 = max(0, r0 - pad); rr1 = min(H, r1 + pad)
        cc0 = max(0, c0 - pad); cc1 = min(W, c1 + pad)

        sub_label = labeled_puncta_mask[rr0:rr1, cc0:cc1]
        puncta_mask_holder = (sub_label == label)

        eroded_puncta_holder = ndi.binary_erosion(puncta_mask_holder, _disk1)
        dilated_puncta_holder = ndi.binary_dilation(puncta_mask_holder, _disk1)
        dilated_local_mask = puncta_mask_holder.copy()
        for _ in range(3):
            dilated_local_mask = ndi.binary_dilation(dilated_local_mask, _disk1)
        local_bg_mask = dilated_local_mask ^ dilated_puncta_holder

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
        local_snr_condition = (img_dilated_object_mean/(img_local_bg_std+np.finfo(np.float32).eps)) <= local_snr_threshold
        global_snr_condition = (img_dilated_object_mean/(cell_bg_std+np.finfo(np.float32).eps)) <= global_snr_threshold

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
            if _refine_debug_enabled():
                print(f"[PyCAT refine-fast] dropped label {int(label)} "
                      f"(area={int(_area)}px, obj_mean={img_object_mean:.0f}): "
                      f"{', '.join(_reasons)}")

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


def cell_mask_stretching(image, cell_masks):
    """
    Enhances the contrast within specific areas of an image defined by cell masks, followed by smoothing operations.

    The function dilates the cell masks to include surrounding areas, then applies CLAHE (Contrast Limited Adaptive 
    Histogram Equalization) to these regions for contrast enhancement. The areas are then slightly eroded to fit
    the original mask dimensions. After processing all masks, the background is zeroed out, and the image is smoothed 
    using grey-scale morphological operations to avoid blurring.

    Parameters
    ----------
    image : numpy.ndarray
        The input image to perform contrast stretching on. Must be a 2D array.
    cell_masks : numpy.ndarray
        A labeled mask image where each cell is represented by a unique integer label, and the background is 0.

    Returns
    -------
    output_image : numpy.ndarray
        The image after applying contrast enhancement and smoothing operations, in the original data type.

    Notes
    -----
    This function assumes the presence of at least one cell label in `cell_masks` (i.e., not all values are 0). 
    It enhances only the areas defined by the masks, leaving the background unaffected except for smoothing. The 
    CLAHE parameters are dynamically adjusted based on the object's estimated radius from its area.
    """

    input_dtype = str(image.dtype)
    img = dtype_conversion_func(image, 'float32') # Convert image to float32 for processing

    # Copy the input image to apply contrast stretching
    img_contrast_stretched = img.copy()
    
    # Initialize a total cell mask with the same shape as the input image but with boolean type
    total_cell_mask = np.zeros_like(cell_masks, dtype=bool)


    for label in np.unique(cell_masks)[1:]:  # Exclude the background label (0).
        # Create a mask for the current cell.
        cell_mask = (cell_masks == label).astype(bool)
        # Check the contrast of the cell
        contrast_flag = check_contrast_func(img * cell_mask)
        if contrast_flag:
            # Update the contrast-stretched image within the mask
            img_contrast_stretched[cell_mask] = 0
            # Update the total cell mask.
            total_cell_mask |= cell_mask
            continue

        # Dilate the mask slightly to include a bit of the surrounding area.
        dilated_mask = ndi.binary_dilation(cell_mask, sk.morphology.disk(3))
        # Create a masked version of the input image using the dilated mask.
        masked_cell = img * dilated_mask

        # Calculate parameters for CLAHE based on the object's size.
        mask_area = np.sum(cell_mask) # Total area of the cell
        object_rad = np.sqrt(mask_area / np.pi) # Estimated radius of the cell
        k_size = math.ceil(object_rad / 4)  # Kernel size for CLAHE.
        clip_lim = 0.0025  # Clip limit for CLAHE.
        # Apply CLAHE to the masked cell.
        stretched_cell = sk.exposure.equalize_adapthist(masked_cell, kernel_size=k_size, clip_limit=clip_lim)

        # Erode the dilated mask to reduce artifacts at the edges.
        eroded_mask = ndi.binary_erosion(dilated_mask, sk.morphology.disk(3)).astype(bool)
    
        # Update the contrast-stretched image within the eroded mask
        img_contrast_stretched[eroded_mask] = stretched_cell[eroded_mask]
                
        # Update the total cell mask.
        total_cell_mask |= eroded_mask

    # Set the background (areas not covered by any cell mask) to 0.
    img_contrast_stretched[~total_cell_mask] = 0
    
    # Apply grey dilation and erosion to smooth the image data without blurring.
    structuring_element = sk.morphology.disk(1)
    output_image = ndi.grey_dilation(img_contrast_stretched, footprint=structuring_element)
    output_image = ndi.grey_erosion(output_image, footprint=structuring_element)

    # Convert the output image back to the original data type.
    output_image = dtype_conversion_func(output_image, output_bit_depth=input_dtype)
    
    return output_image


def segment_subcellular_objects(original_image, pre_processed_image, cell_mask, cell_label, ball_radius, cell_df=None,
                                kurtosis_threshold=-3.0, local_snr_threshold=1.0, global_snr_threshold=1.0,
                                intensity_hwhm_scale=1.17, max_area_fraction=0.25, min_spot_radius=2,
                                crop_to_cell=True, refine_fast=None):
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
        refined_puncta_mask_crop = puncta_refinement_func(orig_crop, proc_crop, puncta_mask_crop, mask_crop, min_spot_radius=2, fast=refine_fast)

        # Paste cropped results back into full-size output arrays
        puncta_mask = np.zeros_like(cell_mask)
        refined_puncta_mask = np.zeros_like(cell_mask)
        puncta_mask[r0p:r1p, c0p:c1p] = puncta_mask_crop
        refined_puncta_mask[r0p:r1p, c0p:c1p] = refined_puncta_mask_crop

    return refined_puncta_mask, puncta_mask

def run_segment_subcellular_objects(pre_processed_image_layer, original_image_layer, data_instance, viewer,
                                    kurtosis_threshold=-3.0, local_snr_threshold=1.0, global_snr_threshold=1.0,
                                    intensity_hwhm_scale=1.17, max_area_fraction=0.25, min_spot_radius=2):
    """
    Orchestrates the segmentation and refinement of subcellular objects across all cells
    in an image. It utilizes the napari viewer for visualization and operates on pre-processed
    and original images to detect and refine objects such as puncta within cell masks.

    Parameters
    ----------
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
            max_area_fraction=max_area_fraction, min_spot_radius=min_spot_radius)

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