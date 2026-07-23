"""Interactive image-adjustment + upscaling UI - split out of image_processing_tools (1.6.253).

The viewer-facing wrappers for the _base intensity/geometry primitives: run_apply_rescale_intensity,
run_invert_image, and run_upscaling_func (the interactive bicubic-upscaling workflow, with its resolution
and cellpose-min-diameter guidance). Moved VERBATIM; napari stays function-scoped so the module imports
headless. The numeric cores (apply_rescale_intensity / invert_image / upscale_image_interp) live in _base
and are pinned there.
"""
from __future__ import annotations

import numpy as np
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.toolbox.image_processing._base import _add_image, _napari, apply_rescale_intensity, upscale_image_interp, invert_image


def run_apply_rescale_intensity(out_min_input, out_max_input, viewer):
    """
    Applies intensity rescaling to the currently active image layer in a Napari viewer based on user-specified
    minimum and maximum output intensity values.

    This function interacts with a Napari viewer, allowing users to rescale the intensity of the selected image
    layer directly through UI elements for specifying the intensity range. The adjusted image is then displayed
    in the viewer.

    Parameters
    ----------
    out_min_input : UI Element
        A UI element for user input of the minimum output intensity value. Typically a text input field.
    out_max_input : UI Element
        A UI element for user input of the maximum output intensity value. Typically a text input field.
    viewer : napari.Viewer
        The Napari viewer instance where the image layers are managed and displayed.

    Raises
    ------
    Error
        If no active image layer is selected or if the active layer is not compatible as an image layer for processing.

    Notes
    -----
    It is assumed that `out_min_input` and `out_max_input` are components of a graphical user interface and can retrieve
    textual input from the user, which is then converted to floating point values for the rescaling function. 

    Error handling for digit inputs should be added. For example:

    .. code-block:: python

        # Ensure the input is valid and convert to an integer
        if new_label_input.text() == "" or not new_label_input.text().isdigit():
            print("Please enter a valid label value.")
            return
    """


    active_layer = viewer.layers.selection.active
    # Check if their is an active layer, and that it is a Napari image layer
    if active_layer is not None and isinstance(active_layer, _napari().layers.Image):
        image = active_layer.data
    else:
        napari_show_warning("No active image layer selected.")
        return

    
    # Use the user provided values if available, otherwise use the defaults
    out_min = float(out_min_input.text()) if out_min_input.text() else None
    out_max = float(out_max_input.text()) if out_max_input.text() else None
    
    # Apply the rescale intensity function to the image
    rescaled_image = apply_rescale_intensity(image, out_min, out_max)

    # Add the rescaled image to the viewer
    _add_image(rescaled_image, viewer, name=f"Intensity Rescaled {active_layer.name}", operation='rescale_intensity')


def run_invert_image(viewer):
    """
    Inverts the intensity of the currently active image layer in the Napari viewer using PyQt GUI components.

    This function retrieves the currently selected image layer from the Napari viewer, inverts its intensity,
    and displays the result as a new layer in the viewer. This is particularly useful for enhancing visual 
    contrast or highlighting specific features in biophysical imaging data.

    Parameters
    ----------
    viewer : napari.Viewer
        The Napari viewer instance, used for displaying and processing the image layers. The viewer is expected
        to be part of a PyQt application, integrating seamlessly with other GUI components.

    Raises
    ------
    Error
        If no active image layer is selected, or if the selected layer is not suitable for processing (e.g., not an image layer).
    """

    active_layer = viewer.layers.selection.active
    
    # Check if their is an active layer, and that it is a Napari image layer
    if active_layer is not None and isinstance(active_layer, _napari().layers.Image):
        image = active_layer.data
    else:
        raise ValueError("No active image layer selected.")
    
    # Apply the invert image function to the rescaled LoG filtered image
    inverted_image = invert_image(image)

    # Add the inverted image to the viewer
    _add_image(inverted_image, viewer, name=f"Inverted {active_layer.name}", operation='rescale_intensity')


def run_upscaling_func(data_checkbox, data_instance, viewer):
    """
    Applies the upscale_image_interp function to selected image layers within a viewer interface, potentially
    updating related data in the data_instance based on the upscaling process and user input. It iterates over 
    selected image layers, applies upscaling, and optionally updates related data in the data_instance. The result 
    is added back to the viewer as a new image layer.

    Parameters
    ----------
    viewer : napari.Viewer
        The viewer object that contains the image layers to be upscaled.
    data_instance : DataInstance
        An object containing data and parameters that may need updating based on the image upscaling (e.g., object
        sizes, resolution information).
    data_checkbox : Checkbox
        A user interface element indicating whether to update certain data in the data_instance based on the upscaling
        results.

    Notes
    -----
    The function ensures that upscaling does not apply to images already at or above a specific size threshold (2048x2048).
    It also corrects data types and ranges for upscaled images to ensure they are suitable for further processing or
    display within the viewer. This function interacts directly with the Napari viewer's GUI elements, facilitating
    a seamless user experience in adjusting image properties.
    """

    # Get the currently selected layers from the viewer
    # Snapshot the selection BEFORE the loop as a plain list filtered to Image
    # layers only. viewer.layers.selection is a live set — napari auto-selects
    # each newly added layer, which mutates the set mid-iteration and causes
    # every upscaled output to be upscaled again, producing duplicate layers.
    import napari.layers as _nl_up
    # Snapshot + de-duplicate. viewer.layers.selection is a live set that napari
    # mutates as new layers are added (auto-selecting them); we snapshot to a list
    # up front. We also de-duplicate by layer identity in case the same layer
    # appears more than once, and we skip any layer whose upscaled output already
    # exists — both of which otherwise produce two identical "Upscaled ..." layers
    # from a single source.
    _seen_ids = set()
    selected_layers = []
    for l in list(viewer.layers.selection):
        if not isinstance(l, _nl_up.Image):
            continue
        if id(l) in _seen_ids:
            continue
        _seen_ids.add(id(l))
        selected_layers.append(l)

    # Check if no image layers are selected and exit if true
    if not selected_layers:
        napari_show_warning("No image layers selected. Select one or more Image layers.")
        return

    # Determine whether the user has requested data updates based on the checkbox state
    update_data = data_checkbox.isChecked()

    # Loop through the snapshotted Image-layer list
    for layer in selected_layers:
        # Skip the iteration if the layer is None for some reason
        if layer is None:
            continue

        # Guard against producing a duplicate output. If an "Upscaled {name}"
        # layer already exists (e.g. the user clicked twice, or re-ran on a
        # selection that includes a prior output), skip rather than add a second
        # identical layer.
        _out_name = f"Upscaled {layer.name}"
        if _out_name in viewer.layers:
            napari_show_warning(
                f"'{_out_name}' already exists — skipping to avoid a duplicate.")
            continue

        # Copy the layer data to prevent modifying the original data directly
        image = layer.data.copy()
        # Retrieve the initial dimensions of the image
        num_row_initial, num_col_initial = image.shape

        # Decide not to upscale images already at or above a certain size threshold
        if num_row_initial >= 2048 or num_col_initial >= 2048:
            upscale_factor = 1  # Set the upscale factor to 1, effectively making no change
            update_data = False  # Prevent updates to data_instance for large images
            napari_show_warning("Max resolution is 2048 x 2048. Micron sizes have not been updated.")
            continue
        else:
            # v1.0.0 behaviour: 2× linear upscaling before preprocessing.
            upscale_factor = 2

        # Apply the upscaling function to the image
        upscaled_img = upscale_image_interp(image, num_row_initial, num_col_initial, upscale_factor=upscale_factor)

        # Prepare the upscaled image data for safe use in the viewer
        data = upscaled_img.copy()
        # Ensure integer data types are within a valid range, correcting them if necessary
        if np.issubdtype(data.dtype, np.integer):
            if np.min(data) < 0 or np.max(data) > 2**16 - 1:
                # Give 5% of the range as a buffer which is clipped rather than rescaled 
                if np.min(data) < (0 - (0.05 * 2**16)) or np.max(data) > (2**16 + (0.05 * 2**16)):
                    data = np.clip(data, 0, 2**16 - 1).astype(np.uint16)
                else:
                    data = apply_rescale_intensity(data, out_min=0, out_max=2**16 - 1).astype(np.uint16)
        # Ensure floating-point data types are within a valid range, correcting them if necessary
        elif np.issubdtype(data.dtype, np.floating):
            if np.min(data) < 0 or np.max(data) > 1.0: 
                # Give 5% of the range as a buffer which is clipped rather than rescaled 
                if np.min(data) < (0 - (0.05)) or np.max(data) > (1.0 + (0.05)):
                    data = np.clip(data, 0.0, 1.0).astype(np.float32)
                else:
                    data = apply_rescale_intensity(data, out_min=0.0, out_max=1.0).astype(np.float32)
        else: 
            data = apply_rescale_intensity(data, out_min=0.0, out_max=1.0).astype(np.float32)

        upscaled_img = data.copy()

        # Update relevant data in the data_instance for the first layer processed only if requested
        if update_data:
            # Calculate new dimensions and scale factors based on the upscaling
            scale_factor_width, scale_factor_height = upscaled_img.shape[0] / image.shape[0], upscaled_img.shape[1] / image.shape[1]
            square_scale_factor = scale_factor_width * scale_factor_height
            linear_scale_factor = np.sqrt(square_scale_factor)

            # Update object sizes, ball radius, and cell diameter in the data_instance based on scale factors
            data_instance.data_repository['object_size'] *= linear_scale_factor
            data_instance.data_repository['cell_diameter'] *= linear_scale_factor
            data_instance.data_repository['ball_radius'] *= linear_scale_factor

            # Update the microns per pixel squared resolution in the data_instance
            data_instance.data_repository['microns_per_pixel_sq'] /= square_scale_factor

            # After updating, ensure no further updates are made for subsequent layers
            update_data = False

        # Add the processed and potentially upscaled image back into the viewer
        # Align the upscaled layer physically with its source: it has
        # `upscale_factor`× more pixels over the SAME field of view, so its scale
        # must be the source scale divided by the actual upscale ratio. Without
        # this the upscaled layer (at scale 1) renders far larger than the source
        # (which may carry a µm scale), making the source look "embedded" in it.
        # Compute the physically-aligned scale for the upscaled layer. A failure
        # here must fall back to a plain add — but the add itself is done ONCE,
        # outside the try, so a later notification error cannot cause a second add
        # (the previous structure put the add and the show_info in the same try,
        # so a show_info failure fell into except and added the layer AGAIN,
        # producing two identical upscaled layers).
        try:
            _src_scale = [float(s) for s in layer.scale]
            _ry = upscaled_img.shape[0] / image.shape[0]
            _rx = upscaled_img.shape[1] / image.shape[1]
            _new_scale = list(_src_scale)
            if len(_new_scale) >= 2 and np.isfinite(_ry) and _ry > 0 and np.isfinite(_rx) and _rx > 0:
                _new_scale[-2] = _src_scale[-2] / _ry
                _new_scale[-1] = _src_scale[-1] / _rx
        except Exception:
            _src_scale = None
            _new_scale = None

        # Single add, exactly once per source layer.
        if _new_scale is not None:
            _add_image(upscaled_img, viewer,
                                            name=_out_name, scale=_new_scale)
        else:
            _add_image(upscaled_img, viewer, name=_out_name, operation='upscale')

        # Lineage: the upscaled layer is derived_from + supersedes the source, and
        # inherits its role/modality/channel. This is what makes autopopulation
        # prefer the upscaled version (head of lineage) automatically.
        try:
            from pycat.utils import layer_tags as _LT
            if len(viewer.layers):
                _LT.mark_derived(viewer.layers[-1], layer, via='upscale')
        except Exception:
            pass

        # Notification is best-effort and isolated: any failure here must not
        # affect the layer that was already added.
        try:
            _oh, _ow = image.shape[-2], image.shape[-1]
            _nh, _nw = upscaled_img.shape[-2], upscaled_img.shape[-1]
            _mpp = f"{_src_scale[-1]:.3f} µm/px " if _src_scale else ""
            napari_show_info(
                f"Upscaled \"{layer.name}\": {_ow}×{_oh} → {_nw}×{_nh} px "
                f"({upscale_factor}× linear, same {_mpp}world extent). "
                f"Both layers occupy the same field of view — zoom in on "
                f"\"{_out_name}\" to see the finer pixel grid. "
                f"The scale bar updates when you click a different layer.")
        except Exception:
            pass
