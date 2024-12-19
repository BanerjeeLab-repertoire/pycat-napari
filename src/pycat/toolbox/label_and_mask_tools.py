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
import pandas as pd
import scipy.ndimage as ndi
import skimage as sk
import cv2
import napari
from napari.utils.notifications import show_warning as napari_show_warning
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QCheckBox, QLineEdit, QPushButton, QScrollArea, QWidget

# Local application imports
from pycat.ui.ui_utils import show_dataframes_dialog, refresh_viewer_with_new_data




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
        if isinstance(active_layer, napari.layers.Labels):
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
    if active_layer is None or not isinstance(active_layer, napari.layers.Labels):
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
    show_dataframes_dialog(window_title, tables_info)



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


def measure_region_props(labeled_mask, image, selected_props):
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
    measurement_df = measurement_df.rename(columns=custom_names)

    return measurement_df

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
    measurement_df = measure_region_props(labeled_mask, image, selected_props)
    data_instance.data_repository['generic_df'] = pd.concat([data_instance.data_repository['generic_df'], measurement_df], ignore_index=True)

    # Show the measurement results in a popup table
    tables_info = [("Region Properties", data_instance.data_repository['generic_df'])]
    window_title = "Analysis Results"
    show_dataframes_dialog(window_title, tables_info)


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
        # Draw each contour on the mask if its area is greater than or equal to the specified minimum area.
        # Contours are filled in (thickness=-1) with white (value=255).
        contour_area = cv2.contourArea(contour)
        if contour_area >= min_area and contour_area <= max_area:
            #cv2.drawContours(mask_with_border, [contour], contourIdx=0, intensity=1, thickness=-1)
            cv2.drawContours(contour_mask, [contour], 0, 1, -1)  # Draw filled-in contour on mask  


    # Remove the padding from the mask to match the size of the original input image.
    output_mask = contour_mask[border_size:-border_size, border_size:-border_size]

    return output_mask



def split_touching_objects(binary_mask, sigma=3.5):
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
    
    # Find the edges where the watershed and binary mask agree, so as to not introduce new erroneous edges
    common_edges_mask = np.logical_not(np.logical_xor(watershed_edges != 0, binary_mask_edges != 0)) * binary_mask

    # Run morphological opening to separate the split objects (watershed lines are only 1 pixel which may not fully separate objects)
    refined_split_mask = binary_morph_operation(common_edges_mask, iterations=7, element_size=1, element_shape='Disk', mode='Opening')

    return refined_split_mask