"""Watershed labeling - split out of segmentation_tools (1.6.240).

apply_watershed_labeling (skimage distance-transform watershed) and opencv_watershed_func (the OpenCV
marker-based splitter) - both registered ops that split touching objects into labels. Moved VERBATIM -
no seed, marker, or morphology change. Imports the shared _to_uint16_safe from _common.
"""
from __future__ import annotations

import numpy as np
import skimage as sk
import cv2
import scipy.ndimage as ndi
from pycat.utils.tag_registry import tags_layer
from pycat.toolbox.label_and_mask_tools import binary_morph_operation
from pycat.toolbox.segmentation._common import _to_uint16_safe


@tags_layer('watershed', role='labels',
            summary='Watershed labelling from a distance transform')
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


@tags_layer('watershed_cv', role='labels',
            summary='OpenCV marker-based watershed')
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
