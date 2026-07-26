"""Mask-measurement feature — extracted from label_and_mask_tools.py (label_mask_split Step 3).

The region-property / binary-mask measurement functions and their property-picker dialog live here,
separated from the masking morphology. ``label_and_mask_tools`` re-exports every public name, so all
callers are unchanged. Moved VERBATIM (characterization-pinned in tests/test_mask_measurement_characterization.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import skimage as sk

from pycat.utils.entity_ref import attach_layer_id, finalize_entity_table, source_path_of
from pycat.utils.notify import show_warning as napari_show_warning


# PyQt is needed only by MeasurementDialog (a GUI dialog). Import it defensively so
# that a headless run — a test, a notebook, a batch job — can still import this
# module for its array operations. If Qt is genuinely absent, the dialog class
# becomes a stub that raises only if someone actually tries to open it.
try:
    from PyQt5.QtWidgets import (
        QDialog, QVBoxLayout, QFormLayout, QCheckBox, QLineEdit, QPushButton,
        QScrollArea, QWidget, QSizePolicy)
    _QT_AVAILABLE = True
except Exception:                                    # pragma: no cover - headless
    _QT_AVAILABLE = False

    class _NoQt:
        """Placeholder base: importing this module without Qt is fine; *using* the
        GUI dialog without Qt is not, and says so clearly."""
        def __init__(self, *a, **k):
            raise RuntimeError(
                "MeasurementDialog requires PyQt5, which is not available in this "
                "environment. The array operations in this module work headlessly; "
                "the GUI dialog does not.")

    QDialog = QWidget = _NoQt                        # type: ignore
    QVBoxLayout = QFormLayout = QCheckBox = _NoQt    # type: ignore
    QLineEdit = QPushButton = QScrollArea = _NoQt    # type: ignore
    QSizePolicy = _NoQt                              # type: ignore


class MeasurementDialog(QDialog):
    """
    A dialog window that allows users to select which properties to measure from regions within an image.
    It presents a list of common properties with checkboxes and textboxes for custom naming of measurements.
    Additional properties can be accessed via a 'Show More' button, which expands the dialog to show a scrollable area.

    Parameters
    ----------
    props_list : list
        A list of property names that can be measured.
    parent : QWidget, optional
        The parent widget of this dialog. Default is None.

    Attributes
    ----------
    checkboxes : list
        A list of QCheckBox widgets for selecting properties.
    textboxes : list
        A list of QLineEdit widgets for entering custom names for the selected properties.

    Methods
    -------
    toggle_scroll_area(self):
        Show or hide the scrollable area containing additional properties.
    select_all(self):
        Selects all property checkboxes.
    deselect_all(self):
        Deselects all property checkboxes.
    get_selected_props(self):
        Returns a list of tuples containing the selected properties and their custom names.
    """
    def __init__(self, props_list, parent=None):
        super().__init__(parent)
        # Setup dialog properties and UI elements
        self.setWindowTitle('Select Measurements')
        self.checkboxes = []
        self.textboxes = []

        # Main layout
        self.top_level_layout = QVBoxLayout(self)

        # Layout for common properties
        self.common_layout = QFormLayout()
        common_props = ['area', 'axis_major_length', 'axis_minor_length', 'bbox', 'centroid', 
                'eccentricity', 'intensity_max', 'intensity_mean', 'intensity_min', 'label']
        
        for prop in common_props:
            checkbox = QCheckBox(prop)
            textbox = QLineEdit()
            textbox.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            textbox.setPlaceholderText(prop)
            self.common_layout.addRow(checkbox, textbox)
            self.checkboxes.append(checkbox)
            self.textboxes.append(textbox)

        # Add common properties layout to the main layout
        self.top_level_layout.addLayout(self.common_layout)

        # Show more button
        self.show_more_button = QPushButton('Show More', self)
        self.show_more_button.clicked.connect(self.toggle_scroll_area)
        self.top_level_layout.addWidget(self.show_more_button)

        # Scrollable area for the rest of the properties
        self.scroll_area = QScrollArea(self)
        self.scroll_content = QWidget(self.scroll_area)
        self.scroll_layout = QFormLayout(self.scroll_content)
        
        for prop in props_list:
            if prop not in common_props:
                checkbox = QCheckBox(prop)
                textbox = QLineEdit()
                textbox.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                textbox.setPlaceholderText(prop)
                self.scroll_layout.addRow(checkbox, textbox)
                self.checkboxes.append(checkbox)
                self.textboxes.append(textbox)

        # Add the scrollable list of all region props to the main layout        
        self.scroll_content.setLayout(self.scroll_layout)
        self.scroll_area.setWidget(self.scroll_content)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setVisible(False)  # Initially hidden
        self.scroll_area.setFixedSize(400, 300)  # Adjust width and height to your preferred size

        self.top_level_layout.addWidget(self.scroll_area)

        # Select All and Deselect All buttons
        self.select_all_button = QPushButton('Select All', self)
        self.select_all_button.clicked.connect(self.select_all)
        self.deselect_all_button = QPushButton('Deselect All', self)
        self.deselect_all_button.clicked.connect(self.deselect_all)
        # Add the buttons to the main layout
        selection_layout = QFormLayout()
        selection_layout.addRow(self.select_all_button, self.deselect_all_button)
        self.top_level_layout.addLayout(selection_layout)

        
        # OK and Cancel buttons
        self.ok_button = QPushButton('OK', self)
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton('Cancel', self)
        self.cancel_button.clicked.connect(self.reject)
        
        # Add the buttons to the main layout
        button_layout = QFormLayout()
        button_layout.addRow(self.ok_button, self.cancel_button)
        self.top_level_layout.addLayout(button_layout)

        self.setLayout(self.top_level_layout)

    def toggle_scroll_area(self):
        """Show or hide the scrollable area."""
        visible = self.scroll_area.isVisible()
        self.scroll_area.setVisible(not visible)
        if not visible:
            self.show_more_button.setText('Show Less')
        else:
            self.show_more_button.setText('Show More')

    def select_all(self):
        """Selects all checkboxes."""
        for checkbox in self.checkboxes:
            checkbox.setChecked(True)
    
    def deselect_all(self):
        """Deselects all checkboxes."""
        for checkbox in self.checkboxes:
            checkbox.setChecked(False)

    def get_selected_props(self):
        """
        Returns a list of tuples for each selected property. Each tuple contains the property name
        and the custom label from the textbox, if provided; otherwise, it defaults to the property name.
        """
        return [(checkbox.text(), textbox.text() or checkbox.text())
                for checkbox, textbox in zip(self.checkboxes, self.textboxes) if checkbox.isChecked()]


def measure_region_props(labeled_mask, image, selected_props, *, data_instance=None):
    """
    Measures specified properties of labeled regions within an image. It maps the selected properties
    to their corresponding measurements for each region and returns these measurements as a DataFrame.

    Parameters
    ----------
    labeled_mask : numpy.ndarray
        A labeled mask of the image, where each unique label corresponds to a different region.
    image : numpy.ndarray
        The original image corresponding to the labeled mask.
    selected_props : list of tuples
        Each tuple contains the name of a property to measure and its custom name (if provided by the user).
    data_instance : optional
        The active data class, used only to record which file the objects came from so a row can be
        turned back into an image. Optional and defaulted: without it the table still measures
        exactly the same numbers, it is just matched by row position rather than by identity.

    Returns
    -------
    measurement_df : pandas.DataFrame
        A pandas DataFrame containing the measurements for the specified properties of each labeled region.
    """

    # Get the properties to measure and their custom names
    properties_to_measure = [prop[0] for prop in selected_props]
    custom_names = {prop[0]: prop[1] for prop in selected_props if prop[1]}

    # Convert measurements to DataFrame and rename columns based on user input
    measurement_df = pd.DataFrame(sk.measure.regionprops_table(labeled_mask, intensity_image=image, properties=properties_to_measure))

    # ── Named BEFORE the rename, and only if there is a label to name it by ────────────────
    #
    # This table is not like the cell/puncta ones: **the user picks the properties** from a
    # checkbox dialog and may rename the columns. So `label` is not guaranteed to be measured at
    # all, and if it is, it may not still be called `label` a line later.
    #
    # Stamping here — before the rename — is the only point where the column has its known name.
    # If the user did not select `label`, `stamp_entity_ids` leaves the table untouched and it
    # stays brushable by row position, flagged rather than silently trusted. That is the honest
    # outcome: without a label there is genuinely nothing stable to name an object by.
    measurement_df = finalize_entity_table(
        measurement_df, 'measure_region_props',
        source_path=source_path_of(data_instance))

    measurement_df = measurement_df.rename(columns=custom_names)

    return measurement_df


def run_measure_binary_mask(mask_layer, image_layer, data_instance):
    """
    Measures various intensity and area-based properties of regions defined by a binary mask within a corresponding image, 
    then appends the results to a Pandas DataFrame stored within a data instance object. This allows for further analysis 
    or reporting.

    Parameters
    ----------
    mask_layer : napari.layers.Labels
        The layer containing the binary mask which indicates regions of interest. This mask should be a boolean array.
    image_layer : napari.layers.Image
        The layer containing the image from which properties are to be measured. Must have the same dimensions as the mask layer.
    data_instance : object
        An object containing a Pandas DataFrame (data_instance.binary_mask_stats_df) to append the results. 
        This object should also contain a 'microns_per_pixel_sq' attribute within data_instance.data_repository for 
        micron area calculations.

    Returns
    -------
    None
        Modifies the DataFrame within `data_instance.binary_mask_stats_df` directly by appending new measurements. 
        If no such DataFrame exists, it creates a new one.

    Raises 
    ------
    ValueError  
        If the mask and image layers have different dimensions.     

    Notes
    -----
    - The function checks that the mask and image have the same dimensions.
    - It calculates the mean, median, standard deviation, minimum, maximum, and total intensity; relative intensity; 
      area; micron area; and relative area.
    - Results are rounded to four decimal places and either appended to an existing DataFrame or used to create a new DataFrame.
    - A dialog is shown with the updated DataFrame upon completion, if applicable.
    """

    mask = mask_layer.data.astype(bool)  # Ensure the mask is boolean
    image = image_layer.data

    if mask.shape != image.shape:
        raise ValueError("Mask and image must have the same dimensions.")

    # Get the properties of the labeled mask using numpy
    properties = {
        'Intensity_Mean': np.mean(image[mask]),
        'Intensity_Median': np.median(image[mask]),
        'Intensity_StdDev': np.std(image[mask]),
        'Intensity_Min': np.min(image[mask]),
        'Intensity_Max': np.max(image[mask]),
        'Intensity_Total': np.sum(image[mask]),
        'Relative Intensity': np.sum(image[mask]) / np.sum(image),
        'Area': np.sum(mask),
        'Micron Area': np.sum(mask) * data_instance.data_repository['microns_per_pixel_sq'],
        'Relative Area': np.sum(mask) / mask.size
    }

    # Convert the properties to a Pandas DataFrame with a single row
    #properties_df = pd.DataFrame(properties, index=[0]).round(4)

    # Create a DataFrame for the properties and append it to the existing DataFrame
    properties_df = pd.DataFrame([properties]).round(4)
    if 'binary_mask_stats_df' in data_instance.data_repository:
        data_instance.data_repository['binary_mask_stats_df'] = pd.concat(
            [data_instance.data_repository['binary_mask_stats_df'], properties_df], ignore_index=True
        )
    else:
        data_instance.data_repository['binary_mask_stats_df'] = properties_df

    tables_info = [("Mask Statistics", data_instance.data_repository['binary_mask_stats_df'])]
    window_title = "Analysis Results"
    from pycat.ui.ui_utils import show_dataframes_dialog
    show_dataframes_dialog(window_title, tables_info)


def run_measure_region_props(mask_layer, image_layer, data_instance):
    """
    Coordinates the measurement of region properties within an image. It handles the preparation of
    the labeled mask and the image, user selection of properties through a dialog, and the storage
    of measurement results in a data repository.

    Parameters
    ----------
    mask_layer : napari.layers.Labels
        The mask layer containing labeled regions for measurement.
    image_layer : napari.layers.Image
        The image layer corresponding to the mask layer.
    data_instance : object
        An instance containing a data repository where measurement results are stored.

    Raises
    ------
    ValueError
        If the mask and image layers have different shapes.

    Notes
    -----
    This function integrates with napari UI elements and custom dialogs to provide a user-friendly
    interface for selecting and measuring region properties. It ensures that the mask and image
    have the same shape and that there are at least two labels in the mask before proceeding with
    measurements.
    """
    # Get the mask and image data
    labeled_mask = mask_layer.data
    image = image_layer.data

    # Check if the mask and image have the same shape
    if labeled_mask.shape != image.shape:
        raise ValueError("The mask and image must have the same shape.")
    
    # Check if there are more than 2 labels in the mask
    if len(np.unique(labeled_mask)) < 3:
        napari_show_warning(
            "Warning: Region Properties operates on a labeled mask. "
            "Use 'Measure Binary Mask' for binary masks.\n"
            "Ignore warning if you meant to do this"
        )


    # Create and show the dialog
    all_props = ['area', 'area_bbox', 'area_convex', 'area_filled', 'axis_major_length', 'axis_minor_length', 'bbox', 'centroid', 
                    'centroid_local', 'centroid_weighted', 'centroid_weighted_local', 'coords_scaled', 'coords', 'eccentricity', 
                    'equivalent_diameter_area', 'euler_number', 'extent', 'feret_diameter_max', 'image', 'image_convex', 'image_filled', 
                    'image_intensity', 'inertia_tensor', 'inertia_tensor_eigvals', 'intensity_max', 'intensity_mean', 'intensity_min', 'label', 
                    'moments', 'moments_central', 'moments_hu', 'moments_normalized', 'moments_weighted', 'moments_weighted_central', 
                    'moments_weighted_hu', 'moments_weighted_normalized', 'num_pixels', 'orientation', 'perimeter', 'perimeter_crofton', 'slice', 'solidity']
    dialog = MeasurementDialog(all_props)
    result = dialog.exec_()

    # Get the selected properties from the dialog
    if result == QDialog.Accepted:
        selected_props = dialog.get_selected_props()
    elif result == QDialog.Rejected:
        return  # Do nothing if user cancels the dialog

    # Measure the selected properties and store the results in the data repository
    measurement_df = measure_region_props(labeled_mask, image, selected_props,
                                          data_instance=data_instance)
    # The objects in this table ARE the labels of `mask_layer`, so that is the layer a click should
    # resolve to — not merely the first mask that happens to be open. (Unlike the cell/puncta
    # tables, the layer already exists here: it is the input, not a freshly-created output.)
    measurement_df = attach_layer_id(measurement_df, mask_layer)
    data_instance.data_repository['generic_df'] = pd.concat([data_instance.data_repository['generic_df'], measurement_df], ignore_index=True)

    # Show the measurement results in a popup table
    tables_info = [("Region Properties", data_instance.data_repository['generic_df'])]
    window_title = "Analysis Results"
    from pycat.ui.ui_utils import show_dataframes_dialog
    show_dataframes_dialog(window_title, tables_info)
