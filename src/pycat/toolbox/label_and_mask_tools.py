"""
Labeled Mask and Binary Mask Module for PyCAT

This module contains functions for processing labeled masks and binary masks, including operations such as
morphological transformations, labeling connected components, and measuring properties of regions. It also
provides functions for splitting touching objects in binary images and extending segmentation masks to the
image borders.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import numpy as np



from pycat.utils.entity_ref import attach_layer_id, finalize_entity_table, source_path_of
from pycat.utils.object_ref import bbox_columns_from_regionprops
from pycat.utils.tag_registry import tags_layer
from pycat.utils.general_utils import debug_log
import pandas as pd
import scipy.ndimage as ndi
import skimage as sk
import cv2

# GUI stack is imported LAZILY (inside the functions that need it) rather than at
# module scope. This module contains pure array operations — binary_morph_operation,
# opencv_contour_func — that other scientific modules import, and a top-level
# `import napari` / `from PyQt5 ...` made those functions, and everything that
# depends on them, un-importable without a display. That prevented the measurement
# chain from being tested headlessly, which is backwards: the numerical code is the
# part that most needs automated regression testing.
#
# The viewer-facing functions below still use napari.layers for isinstance checks;
# they simply import it at call time, when a viewer demonstrably exists.
from pycat.utils.notify import show_warning as napari_show_warning


def _napari():
    """Lazy napari import, for the viewer-facing helpers in this module."""
    import napari
    return napari



# Local application imports
# pycat.ui.ui_utils pulls in the Qt stack, so it is imported at CALL time inside the
# viewer-facing functions below — keeping this module's array operations headless.




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



def run_update_labels(new_label_input, increment_mode, viewer):
    """
    Updates label values in the active label layer of a viewer based on user input. The operation performed 
    depends on the operation mode selected: either incrementing all label values by a specified value or 
    changing a specific label to a new value. The viewer is refreshed to display the updated labels.

    Parameters
    ----------
    viewer : napari.Viewer
        The viewer object that contains the label layer to be updated.
    new_label_input : UI component (e.g., a text input field)
        An input widget or field that provides the new label value or the increment value. Expected to 
        be convertible to an integer.
    increment_mode : bool
        A boolean value or a widget (e.g., a checkbox) indicating the operation mode. If True, all label 
        values in the layer are incremented by the value from `new_label_input`. If False, the specified 
        label is changed to the new value provided.

    Notes
    -----
    - Assumes `new_label_input.text()` returns a string convertible to an integer.
    - Validates the active layer as a labels layer before performing updates.
    - If changing a specific label to a new value, ensures the new value does not duplicate existing label values,
      alerting the user for manual intervention (such as undo) if duplication occurs.
    """

    # Get the active layer from the viewer
    active_layer = viewer.layers.selection.active

    # Ensure there is an active labels layer
    if active_layer is None or not isinstance(active_layer, _napari().layers.Labels):
        napari_show_warning("No active labels layer selected.")
        return
    # Ensure the input is valid and convert to an integer
    if new_label_input.text() == "": # or not new_label_input.text().isdigit():
        napari_show_warning("Please enter a valid label value.")
        return
    
    # Handle label value incrementing for all labels
    if increment_mode.isChecked(): 
        increment_value = int(new_label_input.text())
        active_layer.data += increment_value
    else:
        # Handle changing a specific label to a new value
        picked_label = active_layer.selected_label
        new_label_value = int(new_label_input.text())
        # Check if the new label value is already in use 
        if new_label_value in active_layer.data:
            napari_show_warning(f"Warning: Label {new_label_value} was already in use.")

        active_layer.data[active_layer.data == picked_label] = new_label_value
        
    # Manually refresh the viewer to update the changes
    from pycat.ui.ui_utils import refresh_viewer_with_new_data
    refresh_viewer_with_new_data(viewer, active_layer)


def run_convert_labels_to_mask(labels_layer, viewer):
    """
    Converts a labeled image layer to a binary mask and displays the resulting mask in the viewer. 
    Each unique integer label in the labeled image is treated as a distinct object, and all objects 
    are represented collectively in a single binary mask, where pixels of objects are set to 1, 
    and the background remains 0.

    Parameters
    ----------
    labels_layer : napari.layers.Labels
        The layer containing the labeled image to be converted. Each distinct label represents a different object.
    viewer : napari.Viewer
        The viewer object where the resulting binary mask will be added and displayed.

    Notes
    -----
    - The function creates a binary mask where all non-zero labels are set to 1, effectively differentiating 
      objects from the background without distinguishing between individual objects.
    - The new mask layer is named using the original labels layer's name for easy identification.
    """
    
    # Extract the labeled image data from the layer
    labels = labels_layer.data

    # Convert the labeled image to a binary mask
    mask = (labels > 0).astype(int)

    # Add the binary mask as a new layer to the viewer
    viewer.add_labels(mask, name=f"Mask from {labels_layer.name}")


def run_label_binary_mask(mask_layer, viewer):
    """
    Labels connected components in a binary mask and displays the result in the viewer as a new layer. 
    This process involves assigning a unique label to each connected group of '1's in the binary mask, 
    facilitating the identification and analysis of individual components.

    Parameters
    ----------
    mask_layer : napari.layers.Labels
        The layer containing the binary mask. This mask should only contain values of 0 (background) and 1 (foreground).
    viewer : napari.Viewer
        The viewer object in which the resulting labeled mask will be displayed.

    Notes
    -----
    - The function first checks to ensure that the input mask contains only 0 and 1 values. If any other values are present,
      it issues a warning and exits without performing the labeling.
    - The labeled mask is then added to the viewer under a new layer named 'Labeled <original_layer_name>', 
      making it easy to distinguish from the original binary mask.
    """

    # Extract the binary mask data from the layer
    mask = mask_layer.data

    # Ensure the input is a binary mask (0 and 1 values)
    if not np.all(np.logical_or(mask == 0, mask == 1)):
        napari_show_warning("Input mask must be a binary mask with values of 0 and 1.")
        return

    # Label connected components in the binary mask
    labeled_mask = sk.measure.label(mask).astype(int)

    # Add the labeled mask as a new layer to the viewer
    viewer.add_labels(labeled_mask, name=f"Labeled {mask_layer.name}")











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

def run_expand_labels(labels_layer, distance, viewer):
    """
    Grow labeled regions outward by a fixed distance without merging touching
    labels, using ``skimage.segmentation.expand_labels``. Each label is dilated
    up to ``distance`` pixels into the background; expansion stops at the midpoint
    between two labels so distinct objects never merge.
    """
    labels = np.asarray(labels_layer.data)
    try:
        dist = float(distance)
    except (TypeError, ValueError):
        napari_show_warning("Expand labels: distance must be a number.")
        return
    if dist <= 0:
        napari_show_warning("Expand labels: distance must be greater than 0.")
        return
    expanded = sk.segmentation.expand_labels(labels, distance=dist).astype(int)
    viewer.add_labels(expanded, name=f"Expanded {labels_layer.name}")


def run_mask_logic_merge(mask_layer1, mask_layer2, mode, viewer):
    """
    Combine two binary masks with a boolean set operation (AND / OR / XOR).
    AND keeps overlap, OR keeps the union, XOR keeps the symmetric difference.
    Inputs are binarized (!=0) before the operation; shapes must match.
    """
    m1 = np.asarray(mask_layer1.data)
    m2 = np.asarray(mask_layer2.data)
    if m1.shape != m2.shape:
        napari_show_warning(
            f"Mask logic merge: shapes differ ({m1.shape} vs {m2.shape}) — "
            "masks must match.")
        return
    b1 = m1 != 0
    b2 = m2 != 0
    key = str(mode).strip().upper()
    ops = {'AND': np.logical_and, 'OR': np.logical_or, 'XOR': np.logical_xor}
    if key not in ops:
        napari_show_warning(
            f"Mask logic merge: unknown mode '{mode}' (use AND, OR, or XOR).")
        return
    merged = ops[key](b1, b2).astype(int)
    viewer.add_labels(
        merged, name=f"{key} ({mask_layer1.name} · {mask_layer2.name})")

@tags_layer('split_assessed', role='labels',
            summary='Morphology-aware split: two droplets vs arrested fusion vs chain', target='condensate')
def assess_and_split_touching(binary_mask, intensity_image=None, sigma=2.0,
                              neck_threshold=0.6, min_peak_distance=6,
                              chain_min_units=4, microns_per_pixel=1.0):
    """**Should these masks be split? The morphology answers, and it is a physical answer.**

    ``split_touching_objects`` runs a watershed and cuts. **It does not ask whether it should.**
    That is the wrong question to leave to a threshold, because the same connected mask can be
    four physically different things, and only one of them is two droplets:

    * **Two droplets in contact** — round, with a **deep neck** between them. They have not fused;
      splitting them is correct and *not* splitting them merges two measurements into one.
    * **Arrested fusion** — two droplets caught **part-way** through coalescence. The neck is
      **shallow**, because the interface has already begun to relax. **This is ONE object, and the
      arrest IS the finding**: a material that fuses slowly is a material with a high viscosity or
      a solidified interface. Splitting it destroys the very observation.
    * **Beads on a string / a fractal aggregate** — **many** small units stuck together. Cutting it
      into *two* is meaningless; the object is not a droplet pair at all.
    * **A single irregular droplet** — nothing to split.

    The evidence
    ------------
    **The neck ratio** — the depth of the saddle between two distance-transform peaks, as a
    fraction of the peaks themselves. It is the degree to which the two bodies have merged, and it
    moves smoothly and monotonically with the physics:

        overlap      neck_ratio    what it is
        0.00         **0.128**     barely touching  -> SPLIT
        0.10         0.433         still necked     -> SPLIT
        0.20         0.639         relaxing         -> arrested
        0.50         0.914         mostly fused     -> arrested
        0.80         1.000         one body         -> single

    A neck shallower than ~0.6 of the droplet radius means **the interface has already relaxed**
    — surface tension has done its work, and what is left is one body with a memory of two.

    Measured on the four morphologies (all ONE connected mask):

        morphology            solidity   n_peaks   neck_ratio
        single droplet        0.979      1         1.000
        **two touching**      0.906      **2**     **0.364**
        **arrested fusion**   0.979      **2**     **0.965**
        beads on a string     0.930      **6**     0.788
        fractal aggregate     0.891      1         1.000

    **The neck ratio separates "two touching" from "arrested fusion" cleanly (0.36 vs 0.97) —
    and nothing else does.** Solidity does not (0.906 vs 0.979 overlaps with a single droplet);
    eccentricity does not; the peak count does not (both are 2).

    **The intensity is a second, independent witness.** A real neck between two droplets sits in a
    thinner part of the object, so it is **dimmer** — less material in the light path. An arrested
    neck is filled with material and is **not** dimmer. Where an intensity image is given, this is
    reported as ``neck_intensity_ratio`` and it is used to override a marginal geometric call.

    References
    ----------
    The arrest physics — **interfacial driving force against internal elasticity** — is
    established, and this module implements the observable side of it:

    * **Pawar, Caggioni, Ergun, Hartel & Spicer**, "Arrested coalescence in Pickering emulsions",
      *Soft Matter* **7**, 7710-7716 (2011). DOI: 10.1039/c1sm05457k

      *"their complete fusion into a single spherical drop can sometimes be arrested in an
      intermediate shape **if a rheological resistance offsets the Laplace pressure driving
      force**."*

      Their **eqn (6)** gives the pressure imbalance at the neck as
      ``dP = 2*gamma/R_droplet - (gamma/R1 - gamma/R2)``, with R1 the cross-sectional radius and
      R2 the neck radius — **the two principal radii of a saddle, of opposite sign.** That is
      exactly the object measured here, and their two published doublets **both imply the same
      interfacial tension (0.0529 N/m)** when their equation is recomputed from their own
      geometry — see ``test_the_neck_laplace_pressure_reproduces_PAWAR_2011``.

    * **Pawar, Caggioni, Hartel & Spicer**, "Arrested coalescence of viscoelastic droplets with
      internal microstructure", *Faraday Discuss.* **158**, 341-350 (2012).
      DOI: 10.1039/c2fd20029e

      *"the interfacial energy is continuously reduced while the elastic energy is increased by
      compression of the internal structure and, **when the two processes balance one another,
      coalescence is arrested**."*

    * **Dahiya, Caggioni, Spicer et al.**, arrested coalescence of polydisperse doublets,
      *Phil. Trans. R. Soc. A* (2016), PMC4920281 — the three-regime structure this function
      reports: *"If surface energy dominates, the drops will completely coalesce. If elastic
      energy dominates, the droplets are unable to even initiate coalescence. **Arrest occurs when
      coalescence can begin but not complete.**"*

    Full validation, including the parameter ranges for biomolecular condensates, is in
    ``docs/validation/neck_geometry_and_elastocapillarity.md``.

    Returns
    -------
    dict with ``labels`` (the split, or the original object unsplit), and per-object records
    carrying the verdict, the evidence, and **why**.
    """
    import skimage as sk
    from scipy import ndimage as ndi

    mask = np.asarray(binary_mask) > 0
    intensity = None if intensity_image is None else np.asarray(intensity_image, float)

    labelled = sk.measure.label(mask)
    output = np.zeros_like(labelled)
    records = []
    next_label = 1

    for prop in sk.measure.regionprops(labelled):
        sub = (labelled[prop.slice] == prop.label)

        distance = ndi.distance_transform_edt(sub)
        smoothed = sk.filters.gaussian(distance, sigma=sigma)

        peaks = sk.feature.peak_local_max(
            smoothed, min_distance=int(min_peak_distance), labels=sub)

        record = dict(
            label=int(prop.label),
            # ── KEEP THE BBOX. It is what makes this row brushable. ─────────────
            #
            # regionprops hands it over free, and PyCAT was discarding it at 24 of its 25 call
            # sites. **A row without a bbox cannot be turned back into an image** — and in BATCH
            # that is the ONLY route back to the object, because the layer is gone.
            **bbox_columns_from_regionprops(prop),
            area_um2=float(prop.area) * microns_per_pixel ** 2,
            solidity=float(prop.solidity),
            n_peaks=int(len(peaks)),
            neck_ratio=np.nan,
            neck_intensity_ratio=np.nan,
            verdict='single',
            split=False,
            reason='',
        )

        # ── Not enough peaks: nothing to split ──────────────────────────────────
        if len(peaks) < 2:
            record['neck_ratio'] = 1.0
            record['reason'] = ('One distance-transform maximum: this is a single body, however '
                                'irregular its outline. A ramified or fractal aggregate lands '
                                'here — it has no neck because it has no two centres.')
            output[prop.slice][sub] = next_label
            next_label += 1
            records.append(record)
            continue

        # ── Many peaks: a CHAIN or an aggregate, not a droplet pair ─────────────
        if len(peaks) >= int(chain_min_units):
            record['verdict'] = 'chain_or_aggregate'
            record['reason'] = (
                f'{len(peaks)} sub-units. **This is not a droplet pair** — it is a chain '
                f'(beads-on-a-string) or a ramified aggregate. Cutting it in TWO would be '
                f'arbitrary: the object is not two things, it is many things stuck together, '
                f'and that is itself the observation. Left intact.')
            output[prop.slice][sub] = next_label
            next_label += 1
            records.append(record)
            continue

        # ── Two (or three) peaks: measure the NECK ──────────────────────────────
        depths = sorted((float(smoothed[tuple(q)]) for q in peaks), reverse=True)[:2]

        markers = np.zeros(sub.shape, int)
        for i, q in enumerate(peaks[:2], start=1):
            markers[tuple(q)] = i

        basins = sk.segmentation.watershed(-smoothed, sk.measure.label(markers > 0), mask=sub)
        boundary = sk.segmentation.find_boundaries(basins, mode='thick') & sub

        saddle = float(smoothed[boundary].max()) if boundary.any() else 0.0
        neck = saddle / max(min(depths), 1e-9)
        record['neck_ratio'] = float(neck)

        # ── The intensity is an INDEPENDENT witness ─────────────────────────────
        #
        # A real neck between two droplets is a thinner part of the object, so LESS material sits
        # in the light path and it is DIMMER. An arrested neck is filled, and is not.
        if intensity is not None and boundary.any():
            patch = intensity[prop.slice]
            neck_intensity = float(np.median(patch[boundary]))
            body_intensity = float(np.median(patch[sub & ~boundary]))
            if body_intensity > 1e-9:
                record['neck_intensity_ratio'] = neck_intensity / body_intensity

        deep_neck = neck < float(neck_threshold)

        # ── The intensity is REPORTED but does NOT override the geometry ────────
        #
        # A real neck sits in a thinner part of the object, so less material is in the light path
        # and it should be dimmer. **Tested, and it does not discriminate**: the neck intensity
        # came out at 0.42-0.46 of the body median for a genuine neck AND for an arrested one
        # alike, because the body median is dominated by the bright droplet centres and every
        # neck is dim compared with those.
        #
        # **The geometry is decisive on its own** (0.50 against 0.77 on the same pair), so the
        # intensity is reported for the user to inspect and is NOT used to override the call.
        # A witness that does not discriminate must not be given a vote.
        #
        # (A discriminating intensity test would compare the neck against the LOCAL body
        # thickness at the same distance from the centres — i.e. against what the intensity
        # WOULD be if the neck were filled. That is a real piece of work, and it is not done
        # here.)
        _intensity_ratio = record['neck_intensity_ratio']

        if deep_neck:
            record['verdict'] = 'two_droplets'
            record['split'] = True
            if not record['reason']:
                record['reason'] = (
                    f'Neck ratio {neck:.2f} — **a deep neck**. The two bodies are in contact but '
                    f'have NOT fused: surface tension has not relaxed the interface between '
                    f'them. They are two droplets, and measuring them as one would merge two '
                    f'independent objects.')
            output[prop.slice][basins == 1] = next_label
            output[prop.slice][basins == 2] = next_label + 1
            next_label += 2
        else:
            record['verdict'] = 'arrested_fusion'
            record['reason'] = (
                f'Neck ratio {neck:.2f} — **a shallow neck**. The interface between the two '
                f'centres has already relaxed: surface tension has done its work and what '
                f'remains is ONE body with a memory of two. **This is arrested fusion, and the '
                f'arrest is the finding** — a droplet pair that stalls part-way through '
                f'coalescence is reporting a high viscosity or a solidified interface. '
                f'Splitting it would destroy exactly that observation. Left intact.')
            output[prop.slice][sub] = next_label
            next_label += 1

        records.append(record)

    return dict(labels=output, objects=records)

# ── condensate wetting/coalescence physics moved to its rightful home (label_mask_split) ──────────
# `neck_geometry` and `fit_elastocapillary_length` are fusion/wetting measurements, not masking; they now
# live in condensate_physics/wetting.py and are re-exported here so every existing caller is unchanged.
from pycat.toolbox.masks.measurement import (  # noqa: F401,E402  (re-export shim — label_mask_split Step 3)
    MeasurementDialog, measure_region_props, run_measure_binary_mask, run_measure_region_props)

from pycat.toolbox.condensate_physics.wetting import (  # noqa: F401,E402  (re-export shim)
    neck_geometry, fit_elastocapillary_length)
