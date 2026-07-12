"""
Napari Layer Operations Module for PyCAT

This module contains functions for merging multiple layers in the Napari viewer. It supports simple merging of multiple
layers using different modes like 'Additive', 'Mean', 'Max', and 'Min'. It also provides advanced merging of two layers
with modes like 'Subtractive', 'Screen blending', 'Alpha blending', 'Absolute difference', and a weighted 'Blend' mode. 
The merged result is normalized to prevent data clipping and is displayed in the viewer. The functions ensure that the
selected layers are compatible in terms of shape and datatype before proceeding with the merge operation.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import numpy as np
from pycat.utils.notify import show_warning as napari_show_warning

# Local application imports
# `pycat.ui.ui_utils` imports napari, so importing it at module scope blocks the
# headless import of this module's array functions. `add_image_with_default_colormap` is imported lazily,
# inside the function that uses it.
from pycat.utils.general_utils import dtype_conversion_func



def run_simple_multi_merge(mode, viewer):
    """
    Merges selected layers in the viewer based on the specified mode and adds the result as a new layer to the viewer.

    The function supports different merging modes like 'Additive', 'Mean', 'Max', and 'Min'. It verifies that all 
    selected layers are of the same shape and datatype before proceeding with the merge. The merged result is 
    normalized to prevent data clipping and is then displayed in the viewer.

    Parameters
    ----------
    mode : str
        The merging mode to apply. Accepted values are 'Additive', 'Mean', 'Max', and 'Min'.
    viewer : napari.Viewer
        The viewer object containing the layers to be merged.

    Raises
    ------
    ValueError
        If the selected layers do not have the same shape and datatype.

    Notes
    -----
    This function requires that at least two layers are selected in the viewer. It ensures uniformity in layer data 
    characteristics and normalizes the merged output to maintain visual consistency across varying data scales.
    """
    selected_layer_names = [layer.name for layer in viewer.layers.selection]
    # Collect layers that are selected for merging
    layers = [layer for layer in viewer.layers if layer.name in selected_layer_names]

    # Validation: Ensure there are selected layers for merging
    if not layers or len(layers) < 2:
        napari_show_warning("Please select at least two layers for merging.")
        return

    # Validation: Check if layers have the same shape and datatype
    shapes = [layer.data.shape for layer in layers]
    dtypes = [layer.data.dtype for layer in layers]
    if not all(shape == shapes[0] for shape in shapes) or len(set(dtypes)) != 1:
        raise ValueError("All selected layers should have the same shape and datatype for merging.")
    
    input_dtype = str(dtypes[0]) # Store the input data type for conversion back at the end

    # Define merging functions for supported modes
    merge_functions = {
        'Additive': lambda layer_list: np.sum(layer_list, axis=0),
        'Mean': lambda layer_list: np.mean(layer_list, axis=0),
        'Max': lambda layer_list: np.max(layer_list, axis=0),
        'Min': lambda layer_list: np.min(layer_list, axis=0)
    }

    # Perform the merge operation
    #normalized_layer_list = [(layer.data - np.min(layer.data)) / (np.max(layer.data) - np.min(layer.data)) for layer in layers]
    #normalized_layer_list = np.stack(normalized_layer_list, axis=0)
    #merged_data = merge_functions[mode](normalized_layer_list)
    layer_list = [layer.data for layer in layers]
    layer_list = np.stack(layer_list, axis=0).astype(np.float32)
    merged_data = merge_functions[mode](layer_list)

    # NOTE (fixes "Mean and Additive look identical" bug): the previous code
    # used per-result min-max normalisation, which cancelled the ÷N factor
    # between Mean and Additive, making them byte-identical. Now we clip to the
    # input dtype's valid range and scale by that fixed maximum so each mode
    # keeps its own scale (Additive can saturate; Mean/Max/Min stay distinct).
    if np.issubdtype(input_dtype, np.integer):
        _max = float(np.iinfo(input_dtype).max)
    else:
        _max = float(np.nanmax(merged_data)) or 1.0
    clipped = np.clip(merged_data, 0.0, _max)
    normalized_data = dtype_conversion_func(clipped / _max, output_bit_depth=input_dtype)

    # Add the merged image to the viewer with a default colormap
    from pycat.ui.ui_utils import add_image_with_default_colormap
    add_image_with_default_colormap(normalized_data, viewer, name=f"{mode} Merged Image")


def run_advanced_two_layer_merge(input_layer1, input_layer2, mode, slider, viewer):
    """
    Merges two image layers using a specified mode influenced by an adjustable slider parameter, displaying the result in the viewer.

    Supports various merging modes, including 'Subtractive', 'Screen blending', 'Alpha blending', 'Absolute difference',
    and a weighted 'Blend' based on the slider value. It ensures that both input layers are compatible in terms of shape
    and datatype before proceeding with the merge. The result is normalized and converted back to the original datatype
    for visualization.

    Parameters
    ----------
    input_layer1 : napari.layers.Image
        The first input layer (image data) for merging.
    input_layer2 : napari.layers.Image
        The second input layer (image data) for merging.
    mode : str
        The merging mode to be applied. Supported modes include 'Subtractive', 'Screen_blending', 'Alpha_blending',
        'Abs_difference', and 'Blend'.
    slider : object
        A GUI element or similar object that provides a scalar value influencing the merge operation.
    viewer : napari.Viewer
        The viewer object where the merged result will be displayed.

    Raises
    ------
    ValueError
        If the input layers do not have the same shape and datatype.

    Notes
    -----
    Ensures both input layers are of the same shape and datatype. Uses `dtype_conversion_func` for accurate datatype
    conversions and `add_image_with_default_colormap` to add the resulting image to the viewer with default settings.
    """

    layer1 = input_layer1.data.astype(float)
    layer2 = input_layer2.data.astype(float)
    slider_value = slider.value() * 0.1

    # Validate layer compatibility
    if layer1.shape != layer2.shape or layer1.dtype != layer2.dtype:
        raise ValueError("Both layers should have the same shape and datatype for merging.")
    
    input_dtype = str(input_layer1.data.dtype) # Store the input data type for conversion back at the end

    # Define merge functions
    merge_functions = {
        'Subtractive': lambda l1, l2: l1 - l2,
        'Screen_blending': lambda l1, l2: 1 - (1 - l1) * (1 - l2),
        'Alpha_blending': lambda l1, l2: l1 * slider_value + l2 * (1 - slider_value),
        'Abs_difference': lambda l1, l2: np.abs(l1 - l2),
        'Blend': lambda l1, l2: np.average([l1, l2], axis=0, weights=[slider_value, 1 - slider_value])
    }

    # Perform merge operation
    merged_data = merge_functions[mode](layer1, layer2)

    # Enforce non-negative values
    merged_data[merged_data < 0] = 0

    # Normalize merged data and convert back to original datatype
    normalized_data = (merged_data - np.min(merged_data)) / (np.max(merged_data) - np.min(merged_data))
    normalized_data = dtype_conversion_func(normalized_data, output_bit_depth=input_dtype)

    # Add the merged image to the viewer
    from pycat.ui.ui_utils import add_image_with_default_colormap
    add_image_with_default_colormap(normalized_data, viewer, name=f"{mode} Merged Image")
