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


# ── condensate wetting/coalescence physics moved to its rightful home (label_mask_split) ──────────
# `neck_geometry` and `fit_elastocapillary_length` are fusion/wetting measurements, not masking; they now
# live in condensate_physics/wetting.py and are re-exported here so every existing caller is unchanged.
from pycat.toolbox.masks.morphology import (  # noqa: F401,E402  (re-export shim — label_mask_split Step 4)
    generate_cross_structuring_element, extend_mask_to_edges, custom_binary_opening,
    custom_binary_closing, binary_morph_operation, run_binary_morph_operation, opencv_contour_func,
    split_touching_objects)

from pycat.toolbox.masks.measurement import (  # noqa: F401,E402  (re-export shim — label_mask_split Step 3)
    MeasurementDialog, measure_region_props, run_measure_binary_mask, run_measure_region_props)

from pycat.toolbox.masks.splitting import (  # noqa: F401,E402  (re-export shim — label_mask_split Step 5)
    assess_and_split_touching)

from pycat.toolbox.condensate_physics.wetting import (  # noqa: F401,E402  (re-export shim)
    neck_geometry, fit_elastocapillary_length)
