"""Mask-morphology feature — extracted from label_and_mask_tools.py (label_mask_split Step 4).

Binary morphological operations (open/close/erode/dilate/fill, their structuring elements and the
edge-extension helper), the contour-area filter, and the watershed split of touching objects. Separated
from label measurement (masks/measurement.py) and the assessed-split decision path (masks/splitting.py).
``label_and_mask_tools`` re-exports every public name, so all callers are unchanged. Moved VERBATIM
(characterization-pinned in tests/test_mask_morphology_characterization.py; split_touching_objects by
tests/test_group_c_geometry.py).
"""
from __future__ import annotations

import numpy as np
import scipy.ndimage as ndi
import skimage as sk
import cv2

from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.tag_registry import tags_layer


def _napari():
    """Lazy napari import, for the viewer-facing helpers in this module."""
    import napari
    return napari


def generate_cross_structuring_element(radius):
    """
    Generates a cross-shaped structuring element with a specified radius for use in morphological 
    operations on binary images.

    Parameters
    ----------
    radius : int
        The radius of the cross. This value defines the reach of the arms of the cross from the center. 
        The overall size of the structuring element will be (2*radius + 1, 2*radius + 1), forming a 
        square array.

    Returns
    -------
    structuring_element : numpy.ndarray
        A 2D numpy array representing the structuring element. The array contains 1s along the arms of 
        the cross and 0s elsewhere.
    """

    size = 2 * radius + 1  # Calculate the size of the structuring element.
    structuring_element = np.zeros((size, size), dtype=int)  # Initialize a square array filled with 0's.
    center = radius  # The center of the structuring element.
    structuring_element[center, :] = 1  # Fill the central row with 1's.
    structuring_element[:, center] = 1  # Fill the central column with 1's.

    return structuring_element


@tags_layer('extend_edges', role='mask',
            summary='Extend a mask to the image border')
def extend_mask_to_edges(mask, size_to_extend=1):
    """
    Extend a segmentation mask outwards to the edges of an image, ensuring coverage up to the image borders. 
    This function is particularly useful for segmentation methods that might not reach the image borders, 
    leaving unsegmented spaces.

    This method copies the mask values from inside the border (specified by the extension size) to the actual 
    borders, effectively extending the mask.

    Parameters
    ----------
    mask : numpy.ndarray
        The segmentation mask array, which may be binary or labeled.
    size_to_extend : int, optional
        The number of pixels by which to extend the mask into the image borders. Defaults to 1.

    Returns
    -------
    mask : numpy.ndarray
        The extended mask, adjusted to cover up to the image borders.

    Notes
    -----
    If `size_to_extend` is less than or equal to 0, the function prints a warning and returns the 
    unmodified mask.
    """

    # ── It wrote into the CALLER's array, and returned the same object ───────────
    #
    # ``mask[0:size, :] = ...`` modifies the array it was **given**. Measured: a caller's mask goes
    # from **361 px to 400 px**, and ``result is mask`` is **True** — *there is no new array at
    # all.*
    #
    # **If that array is a napari layer, the user's mask on screen silently changes.** And a
    # workflow re-run starts from data that is no longer what the user segmented.
    #
    # It happens to be idempotent — running it twice gives the same answer — but that is **luck,
    # not design**: the second call simply finds the border already filled. *The aliasing is the
    # bug, and idempotence does not excuse it.*
    #
    # ``segmentation_tools`` passes ``refined_labels`` here — a **labels** array, not a boolean
    # mask — so the propagated border carries **label IDs**, not just True.
    mask = np.array(mask, copy=True)

    h, w = mask.shape # Get the height and width of the mask

    size_to_extend = int(size_to_extend) # Ensure the size to extend is an integer
    
    if size_to_extend <= 0:
        napari_show_warning("The size to extend must be a positive integer.")
        return mask
    else:
        # Extend the segmentation to the top and bottom borders.
        mask[0:size_to_extend, :] = mask[size_to_extend, None] # Use 'None' to maintain the second dimension
        mask[h-size_to_extend:h, :] = mask[h-size_to_extend-1, None]
        # Extend the segmentation to the left and right borders.
        mask[:, 0:size_to_extend] = mask[:, size_to_extend, None] # Use 'None' to keep the first dimension
        mask[:, w-size_to_extend:w] = mask[:, w-size_to_extend-1, None]

    return mask


@tags_layer('binary_open', role='mask',
            summary='Binary morphological opening')
def custom_binary_opening(binary_mask, structure=None, iterations=1, mask=None):
    """
    Performs a binary opening on a binary image, which is an erosion followed by a dilation. This operation 
    is used to remove small objects from the foreground of an image, typically small noise components.

    Parameters
    ----------
    binary_mask : numpy.ndarray
        The binary image to process.
    structure : numpy.ndarray, optional
        The structuring element used for erosion and dilation. If not provided, a default element is used.
    iterations : int, optional
        The number of times the erosion and dilation are applied.
    mask : numpy.ndarray, optional
        A mask defining where the operation should be applied; if provided, operations are confined to this area.

    Returns
    -------
    binary_mask : numpy.ndarray
        The binary image after applying the opening operation.
    """
    for _ in range(iterations):
        binary_mask = ndi.binary_erosion(binary_mask, structure=structure, mask=mask)
        binary_mask = ndi.binary_dilation(binary_mask, structure=structure, mask=mask)

    return binary_mask


@tags_layer('binary_close', role='mask',
            summary='Binary morphological closing')
def custom_binary_closing(binary_mask, structure=None, iterations=1, mask=None):
    """
    Performs a binary closing on a binary image, which is a dilation followed by an erosion. This operation 
    is useful for closing small holes within the foreground objects in an image, enhancing connectivity 
    and coverage.

    Parameters
    ----------
    binary_mask : numpy.ndarray
        The binary image to process.
    structure : numpy.ndarray, optional
        The structuring element used for dilation and erosion. If not provided, a default element is used.
    iterations : int, optional
        The number of times the dilation and erosion are applied.
    mask : numpy.ndarray, optional
        A mask defining where the operation should be applied; if provided, operations are confined to this area.

    Returns
    -------
    binary_mask : numpy.ndarray
        The binary image after applying the closing operation.
    """
    for _ in range(iterations):
        binary_mask = ndi.binary_dilation(binary_mask, structure=structure, mask=mask)
        binary_mask = ndi.binary_erosion(binary_mask, structure=structure, mask=mask)

    return binary_mask


@tags_layer('binary_morph', role='mask',
            summary='Binary morphological operation (open/close/erode/dilate)')
def binary_morph_operation(binary_mask_input, iterations=1, element_size=3, element_shape='Disk', mode='Opening', roi_mask=None):
    """
    Performs specified binary morphological operations using various structuring elements on a binary image. This 
    function provides flexibility in image processing applications to manipulate image structures based on the 
    selected morphological technique.

    Parameters
    ----------
    binary_mask_input : numpy.ndarray
        The binary image on which to perform the operation.
    iterations : int, optional
        The number of times the operation is applied; more iterations intensify the effect.
    element_size : int, optional
        Determines the size of the structuring element used in the operation.
    element_shape : str, optional
        The shape of the structuring element, such as 'Disk', 'Square', 'Diamond', 'Star', or 'Cross'.
    mode : str, optional
        The type of morphological operation to perform, including 'Opening', 'Closing', 'Dilation', 'Erosion', or 'Fill Holes'.
    roi_mask : numpy.ndarray, optional
        A mask that defines the region of interest within the binary image where the operation should be applied.

    Returns
    -------
    binary_mask : numpy.ndarray
        The binary image processed by the specified morphological operation.

    Notes
    -----
    The function includes an automatic extension of the mask to the edges of the image to prevent artifacts from 
    operations near the image borders.
    """
    # Define dictionaries mapping operation modes and structuring element shapes to their corresponding functions and constructors.
    mode_dict = {
        'Opening': custom_binary_opening,
        'Closing': custom_binary_closing,
        'Dilation': ndi.binary_dilation,
        'Erosion': ndi.binary_erosion,
        'Fill Holes': ndi.binary_fill_holes
    }

    footprint_dict = {
        'Diamond': sk.morphology.diamond,
        'Disk': sk.morphology.disk,
        'Square': sk.morphology.square,
        'Star': sk.morphology.star,
        'Cross': generate_cross_structuring_element
    }

    # Retrieve the function and structuring element based on user inputs.
    mode_func = mode_dict.get(mode)
    struct_elem = footprint_dict.get(element_shape)

    # Ensure the image is boolean.
    binary_mask = binary_mask_input.astype(bool)

    # Apply the selected operation with the specified structuring element.
    if mode == 'Fill Holes':
        binary_mask = mode_func(binary_mask)
    else:
        binary_mask = mode_func(binary_mask, structure=struct_elem(element_size), iterations=iterations, mask=roi_mask)        
        # Extend the mask to the edges of the image to maintain object integrity at the borders.
        binary_mask = extend_mask_to_edges(binary_mask, 2)

    return binary_mask


def run_binary_morph_operation(roi_mask_layer, iter_input, elem_size_input, elem_shape_dropdown, mode_dropdown, viewer):
    """
    Facilitates the interactive execution of binary morphological operations within the Napari viewer, 
    allowing users to adjust parameters through the UI and apply changes dynamically to the image data.

    Parameters
    ----------
    roi_mask_layer : napari.layers.Labels
        The Napari Labels layer that serves as a mask defining the region of interest where the operation is applied.
    iter_input : int
        The number of iterations for the morphological operation.
    elem_size_input : int
        The size parameter for the structuring element used in the operation.
    elem_shape_dropdown : str
        The shape of the structuring element; options include 'disk', 'square', 'diamond', 'star', 'cross'.
    mode_dropdown : str
        The type of morphological operation to perform; options include 'opening', 'closing', 'dilation', 'erosion', 'fill holes'.
    viewer : napari.Viewer
        The Napari viewer instance used for visualizing the changes.

    Raises
    ------
    ValueError
        If the active layer is not a labels layer, or if the binary mask and ROI mask have different shapes.

    Notes
    -----
    This function dynamically updates the viewer based on user input, providing real-time visual feedback. It checks for
    the type of the active layer and raises an error if the layer is not suitable for the operation.
    """

    # Get the currently selected layer in the viewer.
    active_layer = viewer.layers.selection.active  
    if active_layer is not None:
        if isinstance(active_layer, _napari().layers.Labels):
            binary_mask = active_layer.data.copy()
        else:
            raise ValueError('The active layer must be a labels layer.')
    else:
        napari_show_warning("No active layer selected.")
        return 
    
    # Store the data type of the input mask for later use.
    input_dtype = binary_mask.dtype
    
    # Check if the mask is labeled (contains more than binary values).
    labeled_mask_flag = np.max(binary_mask) > 1  
    if labeled_mask_flag:
        binary_mask = binary_mask > 0  # Convert labeled mask to binary mask.

    binary_mask = binary_mask.astype(bool)  # Ensure mask is boolean.
    roi_mask = roi_mask_layer.data.astype(bool) if roi_mask_layer is not None else None  # Get ROI mask if provided.

    # Get textbox input values 
    iter_val = int(iter_input.text()) if iter_input.text() else 1
    elem_size_val = int(elem_size_input.text()) if elem_size_input.text() else 3

    if roi_mask is not None and roi_mask.shape != binary_mask.shape:
        raise ValueError('The binary mask and ROI mask must have the same shape.')

    # Perform the binary morphological operation
    processed_mask = binary_morph_operation(binary_mask, iterations=iter_val, element_size=elem_size_val, element_shape=elem_shape_dropdown, mode=mode_dropdown, roi_mask=roi_mask)

    if labeled_mask_flag:
        processed_mask = sk.measure.label(processed_mask)

    # Convert the processed mask back to the original data type.
    processed_mask = processed_mask.astype(input_dtype)

    # Refresh the viewer
    from pycat.ui.ui_utils import refresh_viewer_with_new_data
    refresh_viewer_with_new_data(viewer, active_layer, new_data=processed_mask.copy())


@tags_layer('contour_filter', role='mask',
            summary='Contour-based area filtering')
def opencv_contour_func(input_mask, min_area=1, max_area=1024**2, border_size=3): 
    """
    Extracts and draws contours from a binary input mask based on specified area thresholds. This function converts
    the input mask to uint8, pads it to detect contours at the edges, and then filters the detected contours by
    area before drawing them onto a new mask.

    Parameters
    ----------
    input_mask : numpy.ndarray
        A binary mask where the contours are to be detected and drawn. The mask should be in a format compatible
        with OpenCV (usually a binary image).
    min_area : int, optional
        The minimum area threshold for a contour to be considered valid. Contours with an area less than this
        value are ignored. Defaults to 1.
    max_area : int, optional
        The maximum area threshold for a contour to be considered valid. Contours with an area greater than this
        value are ignored. Defaults to 1024^2, accommodating very large contours.
    border_size : int, optional
        The size of the border added around the input mask to ensure contours at the edges are detected. Defaults
        to 3.

    Returns
    -------
    output_mask : numpy.ndarray
        A mask of the same shape as `input_mask`, with valid contours filled in. The type of the mask is uint8,
        suitable for further processing or visualization with OpenCV.

    Notes
    -----
    The function initially pads the input mask with a black border to facilitate the detection of contours that
    reach the edges of the image. It then utilizes `cv2.findContours` to detect contours and `cv2.drawContours` to
    draw them based on the specified area thresholds. The padding is removed from the final output, ensuring the
    output mask matches the size of the original input mask.
    """
    
    # Convert the input mask to boolean and then to uint8 for compatibility with OpenCV functions.
    input_mask = input_mask.astype(bool)
    mask = input_mask.astype(np.uint8)

    # Pad the input mask with a black border to ensure contour detection at the edges.
    mask_with_border = np.pad(mask, pad_width=((border_size, border_size), (border_size, border_size)), mode='constant', constant_values=0)
    
    # Initialize a mask to draw contours on, with the same shape as the padded mask.
    contour_mask = np.zeros_like(mask_with_border, dtype=np.uint8)

    # Find contours in the padded image using cv2.findContours with parameters to retrieve external contours
    contours, _ = cv2.findContours(mask_with_border, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    for contour in contours:
        # Measure area by FILLED PIXEL COUNT, not cv2.contourArea (enclosed polygon
        # area). Local (Niblack/Sauvola) thresholding hollows out large bright flat
        # cores into rings; cv2.contourArea then reports the whole enclosed disc,
        # over-estimating the object's true size and wrongly tripping max_area —
        # dropping or partially filling genuine bright condensates. Rasterising the
        # filled contour and counting pixels makes the area gate consistent with how
        # area is measured elsewhere (pixel sums), and pairs with the solid fill
        # (thickness=-1) so hollow cores become complete objects.
        single = np.zeros_like(contour_mask, dtype=np.uint8)
        cv2.drawContours(single, [contour], 0, 1, -1)  # filled rasterisation
        contour_area = int(single.sum())               # true filled pixel area
        if contour_area >= min_area and contour_area <= max_area:
            # Composite this object's filled pixels into the output mask.
            contour_mask |= single


    # Remove the padding from the mask to match the size of the original input image.
    output_mask = contour_mask[border_size:-border_size, border_size:-border_size]

    return output_mask


@tags_layer('split_watershed', role='labels',
            summary='Watershed split of touching objects')
def split_touching_objects(binary_mask, sigma=3.5, return_mask=False):
    """
    Splits touching objects in a binary image using a watershed algorithm. The function applies
    morphological closing to connect close objects, followed by a distance transform and Gaussian
    filtering. Peak local maxima are identified in the filtered distance transform as markers for
    the watershed algorithm, which segments the image into individual objects. This method is
    useful for separating connected objects such as cell nuclei in binary images.

    Parameters
    ----------
    binary_mask : numpy.ndarray
        A binary image where the objects to be split are marked as True (or 1) and the background
        as False (or 0).
    sigma : float, optional
        The standard deviation for Gaussian filter applied to the distance transform of the binary
        image. A higher value results in more smoothing, which can be useful for separating objects
        that are very close to each other. Default is 3.5.

    Returns
    -------
    refined_split_mask : numpy.ndarray
        A binary image where the originally connected objects have been split based on the
        watershed segmentation results.

    Notes
    -----
    This function is adapted from an original implementation by Robert Haase [split_objects_1]_. The 3D processing
    capabilities have been removed, as they were deemed unnecessary at the time of writing. Simple
    morphological opening and closing operations were introduced to refine the mask. For potential
    re-addition of 3D functionality, referring to the original source code is advised. Other changes
    include syntactical and style improvements and enhanced documentation.The function is similar to the ImageJ watershed 
    algorithm, and it is suitable for images where nuclei or other objects are not overly dense [split_objects_2]_. For 
    denser object configurations, considering alternatives such as Stardist or Cellpose, may be beneficial [split_objects_3]_, [split_objects_4]_.

    References
    ----------
    .. [split_objects_1] Original python code: https://github.com/haesleinhuepf/napari-segment-blobs-and-things-with-membranes/blob/main/napari_segment_blobs_and_things_with_membranes/__init__.py
           BSD-3 License open source. Copyright (c) 2021, Robert Haase. All rights reserved.
    .. [split_objects_2] ImageJ Watershed Algorithm: https://imagej.nih.gov/ij/docs/menus/process.html#watershed
    .. [split_objects_3] Stardist Plugin for Napari: https://www.napari-hub.org/plugins/stardist-napari
    .. [split_objects_4] Cellpose Plugin for Napari: https://www.napari-hub.org/plugins/cellpose-napari
    """
    
    binary_mask = np.asarray(binary_mask).astype(bool)

    # Apply morphological closing to connect close objects
    binary_mask = binary_morph_operation(binary_mask, iterations=7, element_size=1, element_shape='Cross', mode='Closing')

    # Calculate the distance transform and apply Gaussian filtering
    distance = ndi.distance_transform_edt(binary_mask)
    blurred_distance = sk.filters.gaussian(distance, sigma=sigma)
    
    # Find peak local maxima as markers for watershed
    fp = np.ones((3,) * binary_mask.ndim)
    coords = sk.feature.peak_local_max(blurred_distance, footprint=fp, labels=binary_mask)
    mask = np.zeros(distance.shape, dtype=bool)
    mask[tuple(coords.T)] = True
    markers = sk.measure.label(mask)
    
    # Perform watershed segmentation
    labels = sk.segmentation.watershed(-blurred_distance, markers, mask=binary_mask)

    # Edge detection and final morphological operation to refine the segmentation
    if len(binary_mask.shape) == 2:
        watershed_edges = sk.filters.sobel(labels)
        binary_mask_edges = sk.filters.sobel(binary_mask)
    else:
        # Placeholder for potential future 3D support
        napari_show_warning("3D not supported yet")
        return
    
    # ── The watershed computed the split, and the function THREW IT AWAY ────────
    #
    # ``labels`` above IS the answer: it separates two touching discs correctly at every real
    # overlap, and correctly DECLINES to split when they have merged into one blob. Verified
    # against known geometry:
    #
    #     overlap    components in    watershed labels
    #     0 px       2                **2**
    #     4 px       1                **2**
    #     8 px       1                **2**
    #     14 px      1                1      (genuinely one object now)
    #     20 px      1                1
    #
    # The function then **discarded ``labels``** and rebuilt a BOOLEAN mask by subtracting Sobel
    # edges. **A boolean mask cannot express a split.** The two halves stay 8-connected through
    # the corner of the one-pixel cut, so ``label()`` on the output still returns ONE object —
    # measured, at every overlap, including the case where the two discs merely TOUCH and were
    # already two separate components on the way in. **It merged them.**
    #
    # **Touching condensates were always counted as one**, and every count, size distribution and
    # per-object measurement downstream inherited that.
    #
    # The labels are returned. ``return_mask=True`` restores the old boolean output for any
    # caller that needs it — but note that output is the thing that could not represent a split
    # in the first place.
    if return_mask:
        # Find the edges where the watershed and binary mask agree, so as to not introduce new
        # erroneous edges.
        common_edges_mask = np.logical_not(
            np.logical_xor(watershed_edges != 0, binary_mask_edges != 0)) * binary_mask
        return binary_morph_operation(common_edges_mask, iterations=7, element_size=1,
                                      element_shape='Disk', mode='Opening')

    return labels
