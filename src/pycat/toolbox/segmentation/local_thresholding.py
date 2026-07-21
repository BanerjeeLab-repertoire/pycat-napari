"""Local (windowed) thresholding - split out of segmentation_tools (1.6.240).

local_thresholding_func applies Niblack/Sauvola-style windowed thresholding (registered op); the run_
wrapper is the viewer-facing entry point (napari imported lazily). Moved VERBATIM - no threshold change.
"""
from __future__ import annotations

import numpy as np
import skimage as sk
from pycat.utils.tag_registry import tags_layer
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.toolbox.label_and_mask_tools import binary_morph_operation


@tags_layer('local_threshold', role='mask', inputs=('image',),
            summary='Local (adaptive) thresholding')
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
    import napari
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
        from pycat.ui.ui_utils import refresh_viewer_with_new_data
        refresh_viewer_with_new_data(viewer, existing_layer, thresh_mask)  # Refresh the viewer to display changes
    else:
        viewer.add_labels(thresh_mask, name=layer_name)  # Add new layer

    # Reset the active layer to update it in the viewer
    viewer.layers.selection.active = viewer.layers[current_active_layer_name]
