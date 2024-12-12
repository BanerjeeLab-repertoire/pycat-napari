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
    
    # Convert the original image to 16-bit unsigned integers for processing
    image = dtype_conversion_func(original_image, output_bit_depth='uint16')
    
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


def fz_segmentation_and_binarization(image, mask, ball_radius):
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

    # Determine the maximum area for objects based on the input cell mask
    max_area = (np.sum(mask.astype(bool)) / 4)  # Set maximum area based on the input mask

    # Detect external contours and fill them to ensure solid object representation
    contour_mask = opencv_contour_func(segmented_mask, max_area=max_area)

    # Combine with the eroded input mask to refine the final mask and reduce edge artifacts
    boolean_mask = (contour_mask * ndi.binary_erosion(mask, sk.morphology.disk(1))).astype(bool)

    # Dilate the mask to ensure objects are fully covered
    boolean_mask = binary_morph_operation(boolean_mask, iterations=1, element_size=1, element_shape='Disk', mode='Dilation')

    return boolean_mask


def cellpose_segmentation(image, object_diameter):
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
    
    # Initialize Cellpose with the preferred model for cell and nucleus segmentation.
    model = models.Cellpose(model_type='cyto2') 
    #model = models.Cellpose(model_type='nuclei') # Placeholders for future users to specify model type
    #model = models.Cellpose(gpu=True, model_type='nuclei') # This just shows how to use GPU acceleration

    # Preprocess the image to improve segmentation quality.
    img = dtype_conversion_func(image, 'float32') # Convert image to float32 for processing
    img = sk.exposure.equalize_adapthist(img, kernel_size=object_diameter//2, clip_limit=0.0025)
    img = sk.restoration.denoise_wavelet(img)
    img = apply_rescale_intensity(img, out_min=0.0, out_max=1.0)

    image_preprocessed = dtype_conversion_func(img, 'uint16') # Convert the image to uint16 for Cellpose
    # Apply Cellpose model to segment cells/nuclei.
    masks, flows, styles, diams = model.eval(image_preprocessed, diameter=object_diameter, channels=[0,0], resample=True)

    # Post-process segmentation masks to improve results.
    mask = masks > 0 # Ensure the mask is binary
    # Split objects that are erroneously connected. deprecated method replaced by cv2 binary watershed
    #split_mask = split_touching_objects(mask, sigma=object_diameter//4) 
    split_mask = opencv_watershed_func(mask)
    mask = binary_morph_operation(split_mask, iterations=7, element_size=3, element_shape='Disk', mode='Opening') 
    mask = extend_mask_to_edges(mask, 3) # Extend the mask to eliminate the empty border cellpose leaves

    return mask

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
    
    # Perform cell segmentation using Cellpose.
    cell_masks = cellpose_segmentation(image, object_diameter)
    
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


def puncta_refinement_filtering_func(original_img, processed_img, puncta_mask, cell_mask, labeled_puncta_mask, min_spot_radius):
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
    # Convert images to 16-bit for consistent intensity analysis
    original_image_16 = dtype_conversion_func(original_img, output_bit_depth='uint16')
    processed_image_16 = dtype_conversion_func(processed_img, output_bit_depth='uint16')
    
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
    properties = ('label', 'area', 'intensity_mean', 'axis_major_length', 'axis_minor_length')
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
        hwhm = np.sqrt(2*np.log(2)) # HWHM is approx 1.17 sigma
        local_intensity_condition = (
            img_object_mean < (img_local_bg_mean + hwhm*img_local_bg_std) or # could use dilated object mean too
            processed_object_mean < (processed_local_bg_mean + hwhm*processed_local_bg_std)
        )

        # Setup global intensity based conditions
        cell_intensity_condition = img_object_mean < cell_bg_mean
        # Setup kurtosis based conditions
        kurtosis_condition = img_object_kurtosis < -3.0 or processed_object_kurtosis < -3.0
        # Setup area based conditions
        min_area = math.ceil(np.pi * min_spot_radius**2)
        area_condition = df['area'].values[0] > cell_area/4 or df['area'].values[0] < min_area
        # Setup ellipticity based condition
        ellipticty_condition = ellipticity > 0.99
        # Setup gradient based condition
        gradient_condition = gradient_local_bg_mean < (gradient_object_mean + np.std(gradient_object_pixels)/4)
        # Setup 'local' SNR condition
        local_snr_condition = (img_dilated_object_mean/(img_local_bg_std+np.finfo(np.float32).eps)) <= 1.0 
        # Setup 'global' SNR condition
        global_snr_condition = (img_dilated_object_mean/(cell_bg_std+np.finfo(np.float32).eps)) <= 1.0

        # If any of the conditions are met, remove the object from the mask
        if (local_intensity_condition or cell_intensity_condition 
            or kurtosis_condition or area_condition or ellipticty_condition 
            or gradient_condition or local_snr_condition or global_snr_condition):
            # remove the puncta from the mask
            refined_puncta_mask[labeled_puncta_mask == label] = 0


    return refined_puncta_mask

def puncta_refinement_func(original_image, processed_image, puncta_mask, cell_mask, min_spot_radius=2):
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

    # Label the puncta within the mask for individual analysis
    labeled_puncta_mask = sk.measure.label(puncta_mask)
    # First round of puncta refinement using filtering criteria
    refined_puncta_mask = puncta_refinement_filtering_func(original_img, processed_img, puncta_mask, cell_mask, labeled_puncta_mask, min_spot_radius)
    # Apply watershed segmentation to separate closely positioned puncta
    watershed_puncta_mask = apply_watershed_labeling(original_img, refined_puncta_mask, sigma=min_spot_radius/2)
    # Deprecated method involving cv2 watershed 
    #watershed_puncta_mask = opencv_watershed_func(refined_puncta_mask, dist_thresh=0.5, sigma=min_spot_radius, dilation_size=1, dilation_iterations=3)
    #watershed_puncta_mask = sk.measure.label(watershed_puncta_mask)
    # Second round of refinement after watershed segmentation
    refined_puncta_mask = puncta_refinement_filtering_func(original_img, processed_img, refined_puncta_mask, cell_mask, watershed_puncta_mask, min_spot_radius)
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


def segment_subcellular_objects(original_image, pre_processed_image, cell_mask, cell_label, ball_radius, cell_df=None):
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
    cell_mask = cell_mask.astype(bool) # Ensure mask is boolean

    # Initialize a flag indicating whether to perform background removal
    perform_bg_removal = True
    # Check if conditions are met to potentially skip background removal
    if cell_df is not None and not cell_df.empty: # Is no df, the user has not run cell analyzer
        cell_kurt = cell_df.loc[cell_df['label'] == cell_label, 'img_kurtosis'].values
        cell_gaussian_snr = cell_df.loc[cell_df['label'] == cell_label, 'gaussian_snr_estimate'].values
        # Check if kurtosis is NaN or Gaussian SNR is below 1.0
        if np.isnan(cell_kurt[0]) or cell_gaussian_snr[0] < 1.0: # These values indicate nothing present in the cell
            perform_bg_removal = False # So we can skip this and save processing time

    # Perform background removal
    if perform_bg_removal:
        bg_removed_img = rb_gaussian_bg_removal_with_edge_enhancement(pre_processed_img, ball_radius, cell_mask)
    else:
        bg_removed_img = np.zeros_like(original_img)

    # Check for contrast after bg removal
    contrast_flag = check_contrast_func(bg_removed_img)
    if contrast_flag:
        napari_show_info(f"Cell {cell_label} has low contrast, likely has no puncta...")
        # If there is no contrast, return empty masks and skip the segmentation for faster processing
        puncta_mask = np.zeros_like(cell_mask)
        refined_puncta_mask = np.zeros_like(cell_mask)
    else:
        # Call segmentation_func with the appropriate arguments
        puncta_mask = fz_segmentation_and_binarization(bg_removed_img, cell_mask, ball_radius)
        # Refine the puncta mask using custom filtering criteria, primarily based on local intensity aroudn the 'object'  
        refined_puncta_mask = puncta_refinement_func(original_img, pre_processed_img, puncta_mask, cell_mask, min_spot_radius=2)

    return refined_puncta_mask, puncta_mask

def run_segment_subcellular_objects(pre_processed_image_layer, original_image_layer, data_instance, viewer):
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
        The napari viewer instance used for adding the segmentation results as new layers.

    Raises
    ------
    Warning
        If the 'Labeled Cell Mask' layer is not present in the viewer, a warning is issued as the function
        is intended to be used after running the Cell Analyzer. If the layer is absent, the function will
        run on the entire image, which may lead to unintended behavior.

    Notes
    -----
    The function iterates over all cells, segments subcellular objects, and refines the segmentation.
    It relies on cell-specific metrics if available. The final segmentation masks for all cells are
    combined and added to the napari viewer as new layers for visualization and further analysis.
    """
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
        refined_puncta_mask, puncta_mask = segment_subcellular_objects(original_img, contrast_stretched_img, cell_mask_holder, label, ball_radius, cell_df)    

        # Add the segmented mask to the total mask
        total_puncta_mask += puncta_mask 
        total_refined_puncta_mask += refined_puncta_mask


    viewer.add_labels(total_puncta_mask.astype(int), name=f"Total Puncta Mask")
    viewer.add_labels(total_refined_puncta_mask.astype(int), name=f"Total Refined Puncta Mask")