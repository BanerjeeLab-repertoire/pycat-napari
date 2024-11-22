"""
User-Interface (UI) Module for PyCAT 

This module contains the UI class for the toolbox functions, which provides a user interface for various toolbox functions within a 
Napari viewer. This class integrates with the central management system to facilitate image analysis operations, offering a variety 
of tools such as opening images, measuring lines, and running analyses like wavelet noise subtraction and correlation function 
analysis.

This is the main UI class which is used to setup individual functions, analysis methods, and the menu bar in the napari viewer
application. It provides a variety of methods for creating dropdown menus for layer selection, updating these dropdowns based on
viewer layer changes, handling button clicks, and managing dock widgets.

New analysis methods and individual functions can be created and added to this module following the existing structure, which includes 
methods for adding the functions to the toolbox and incorporating them into the viewer interface.

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
import napari 
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QRadioButton, QPushButton, 
    QLineEdit, QWidget, QComboBox, QSlider, QScrollArea, QSizePolicy, QAction)
from PyQt5.QtCore import Qt

# Local application imports
from pycat.toolbox.image_processing_tools import (
    run_pre_process_image, run_apply_rescale_intensity, run_invert_image, run_upscaling_func,
    run_rb_gaussian_background_removal, run_enhanced_rb_gaussian_bg_removal, run_wbns,
    run_wavelet_noise_subtraction, run_apply_bilateral_filter, run_clahe, run_peak_and_edge_enhancement,
    run_morphological_gaussian_filter, run_dpr, run_apply_laplace_of_gauss_filter)
from pycat.toolbox.segmentation_tools import (
    run_fz_segmentation_and_merging, run_cellpose_segmentation, run_train_and_apply_rf_classifier,
    run_local_thresholding, run_segment_subcellular_objects)
from pycat.toolbox.feature_analysis_tools import (
    run_cell_analysis_func, run_puncta_analysis_func)
from pycat.toolbox.pixel_wise_corr_analysis_tools import run_pwcca
from pycat.toolbox.obj_based_coloc_analysis_tools import run_manders_coloc, run_obca
from pycat.toolbox.correlation_func_analysis_tools import run_ccf_analysis, run_autocorrelation_analysis
from pycat.toolbox.label_and_mask_tools import (
    run_convert_labels_to_mask, run_measure_region_props, run_update_labels, run_label_binary_mask, 
    run_measure_binary_mask, run_binary_morph_operation) 
from pycat.toolbox.layer_tools import run_simple_multi_merge, run_advanced_two_layer_merge
from pycat.toolbox.data_viz_tools import PlottingWidget
from pycat.data.data_modules import BaseDataClass


class BaseUIClass:
    """
    A base UI class designed to provide utility functions for managing UI elements
    and interactions within a napari viewer instance. This class includes methods
    for creating dropdown menus for layer selection, updating these dropdowns based
    on viewer layer changes, handling button clicks, and managing dock widgets.

    Attributes
    ----------
    viewer : napari.Viewer
        The napari viewer instance with which the UI components will interact.
    """

    def __init__(self, viewer):
        """
        Initializes the BaseUIClass with a reference to the napari viewer instance.

        Parameters
        ----------
        viewer : napari.Viewer
            The napari viewer instance to interact with.
        """
        self.viewer = viewer

    def create_layer_dropdown(self, layer_type):
        """
        Creates a dropdown (QComboBox) widget that lists layers of a specific type.

        Parameters
        ----------
        layer_type : type
            The type of layer to list in the dropdown, e.g., napari.layers.Image or napari.layers.Labels.

        Returns
        -------
        dropdown : QComboBox
            The created dropdown widget populated with layers of the specified type.
        """
        dropdown = QComboBox()
        self.update_dropdown_items(dropdown, layer_type)

        # Update the dropdown items whenever layers are added or removed from the viewer
        self.viewer.layers.events.inserted.connect(lambda e: self.update_dropdown_items(dropdown, layer_type))
        self.viewer.layers.events.removed.connect(lambda e: self.update_dropdown_items(dropdown, layer_type))
        return dropdown

    def update_dropdown_items(self, dropdown, layer_type):
        """
        Updates the items in the dropdown based on the current layers in the viewer that match the specified type.
        Optionally ensures a 'None' option is available in the dropdown.

        Parameters
        ----------
        dropdown : QComboBox
            The dropdown widget to update.
        layer_type : type
            The type of layer to include in the dropdown.
        """
        # Check if 'None' option exists and store its state
        none_option_exists = dropdown.findText("None") != -1

        # Clear the dropdown and re-populate it
        dropdown.clear()
        for layer in self.viewer.layers:
            if isinstance(layer, layer_type):
                dropdown.addItem(layer.name)

        # Add 'None' option if it was present before
        if none_option_exists: #or dropdown.count() == 0:
            dropdown.insertItem(0, "None")
            dropdown.setCurrentIndex(0)

    def add_text_label(self, layout, text, font_size=10, bold=False):
        """
        Adds a text label above a dropdown widget in the given layout, with an option to make the text bold.

        Parameters
        ----------
        layout : QLayout
            The layout to which the label will be added.
        text : str
            The text of the label.
        font_size : int, optional
            The font size of the label text.
        bold : bool, optional
            If True, the label text will be bold.
        """
        label = QLabel(text)
        # Conditionally set font-weight based on the `bold` argument
        font_weight = "bold" if bold else "normal"
        label.setStyleSheet(f"font-size: {font_size}px; font-weight: {font_weight};")
        layout.addWidget(label)


    def on_general_button_clicked(self, processing_function, viewer=None, *args, **kwargs):
        """
        A general-purpose method to be connected to button click signals. It extracts selected layers
        from dropdowns, filters out non-layer arguments, and calls a specified processing function with
        these layers and any additional arguments.

        Parameters
        ----------
        processing_function : callable
            The function to call with the extracted layers and additional arguments.
        viewer : napari.Viewer, optional
            The napari viewer instance, if not already provided as part of the class.
        """
        # Extract layers if viewer is provided in the first argument position
        if viewer:
            layers = []
            for dropdown in args:
                if isinstance(dropdown, QComboBox):
                    # Check for 'None' selection in the dropdown
                    if dropdown.currentText() == "None":
                        layers.append(None)
                    else:
                        layers.append(viewer.layers[dropdown.currentText()])
        else:
            layers = []

        # Filter out the dropdowns, so we don't pass them to the processing function
        non_dropdown_args = [arg for arg in args if not isinstance(arg, QComboBox)]

        # Call the processing function with the extracted arguments
        processing_function(*layers, *non_dropdown_args, **kwargs)

    def clear_dock(self):
        """
        Removes all dock widgets from the viewer's window.
        """
        # Remove all widgest from the dock
        dock_widgets = list(self.viewer.window._dock_widgets.values())
        for dw in dock_widgets:
            self.viewer.window.remove_dock_widget(dw)

    def update_tool(self, event):
        """
        Updates the active tool based on the currently active layer. This could adjust brush sizes for label layers
        or switch modes for shape layers.

        Parameters
        ----------
        event : Event
            The event that triggered the tool update, not directly used.
        """
        active_layer = self.viewer.layers.selection.active
        if active_layer is None:
            return
        
        # Adjust the brush size for label layers and switch modes for shape layers
        if isinstance(active_layer, napari.layers.Labels):
            active_layer_size = active_layer.data.shape[0]
            active_layer.brush_size = active_layer_size//150
            active_layer.mode = 'paint'
            active_layer.selected_label = 1
        elif isinstance(active_layer, napari.layers.Shapes):
            active_layer.mode = 'add_line'

    def _add_widget_to_layout_or_dock(self, widget, layout, separate_widget, dock_name):
        """
        Adds a widget to the specified layout or creates a new dock widget for it, based on the provided parameters.

        Parameters
        ----------
        widget : QWidget
            The widget to add.
        layout : QLayout
            The layout to add the widget to if not creating a separate dock widget.
        separate_widget : bool
            If True, creates a separate dock widget for the widget.
        dock_name : str
            The name of the dock widget if creating a separate one.
        """
        if separate_widget==True:
            # Create a new layout for the separate widget
            dock_layout = QVBoxLayout()
            dock_layout.addWidget(widget)
            
            # Create a main widget to contain the input widget
            main_widget = QWidget()
            main_widget.setLayout(dock_layout)
            
            # Add the main widget to the viewer as a dock widget
            self.viewer.window.add_dock_widget(main_widget, name=dock_name)
        else:        
            # Add the widget to the existing layout in the dock                    
            layout.addWidget(widget)
            layout.setContentsMargins(1, 1, 1, 1)


class ToolboxFunctionsUI(BaseUIClass):
    """
    Provides a user interface for various toolbox functions within a Napari viewer.

    This class integrates with the central management system to facilitate image
    analysis operations, offering a variety of tools such as opening images, measuring
    lines, and running analyses like wavelet noise subtraction and cross-correlation
    function analysis.

    Parameters
    ----------
    viewer : napari.Viewer
        The Napari viewer instance to which the toolbox functions will be added.
    central_manager : CentralManager
        The central management system handling data and operations across tools.

    Attributes
    ----------
    central_manager : CentralManager
        Stores the central management system instance for accessing and managing data.
    """
    def __init__(self, viewer, central_manager):
        """Initialize the UI with a Napari viewer and a central management system."""
        super().__init__(viewer)
        self.central_manager = central_manager
        #self.central_manager.add_observer(self) # placeholder for possible future implementation of observer pattern


    def _add_open_2d_image(self, layout=None, separate_widget=False):
        """Add a widget to open 2D images, optionally in a separate dock."""
        open_file_layout = QVBoxLayout() # Create a vertical layout widget
        open_file_button = QPushButton("Open File") # Create a button widget
        open_file_button.clicked.connect(lambda: self.on_general_button_clicked( # Connect the button to the function
            self.central_manager.file_io.open_2d_image, None)) # function, viewer, *args
        open_file_layout.addWidget(open_file_button) # Add the button to the layout
        open_file_widget = QWidget() # Create a main widget to contain the input widget
        open_file_widget.setLayout(open_file_layout) # Set the layout for the widget
        self._add_widget_to_layout_or_dock(open_file_widget, layout, separate_widget, "Open File Dock") # Add widget to layout or dock


    def _add_save_and_clear(self, layout=None, separate_widget=False):
        """Add a widget for saving and clearing all data, optionally in a separate dock."""
        save_and_clear_layout = QVBoxLayout()
        save_and_clear_button = QPushButton("Save and Clear") # Create a button widget
        save_and_clear_button.clicked.connect(lambda: self.on_general_button_clicked(
            self.central_manager.file_io.save_and_clear_all, None, self.viewer))
        save_and_clear_layout.addWidget(save_and_clear_button) # Add the button to the layout
        save_and_clear_widget = QWidget()
        save_and_clear_widget.setLayout(save_and_clear_layout)
        self._add_widget_to_layout_or_dock(save_and_clear_widget, layout, separate_widget, "Save and Clear Dock")


    def _add_measure_line(self, layout=None, separate_widget=False):
        """Add a widget for measuring object diameters with drawn lines, optionally in a separate dock."""
        measure_layout = QVBoxLayout() # Create a vertical layout widget
        self.add_text_label(measure_layout, 'Measure Object Diameters', bold=True) # Add widget title label
        measure_button = QPushButton("Measure Line(s)") # Create a button widget
        measure_button.clicked.connect(lambda: self.on_general_button_clicked( # Connect the button to the function
            self.central_manager.active_data_class.calculate_length, None, self.viewer)) # function, viewer, *args
        measure_layout.addWidget(measure_button) # Add the button to the layout
        measure_widget = QWidget() # Create a main widget to contain the input widget
        measure_widget.setLayout(measure_layout) # Set the layout for the widget
        self._add_widget_to_layout_or_dock(measure_widget, layout, separate_widget, "Measure Line Dock") # Add widget to layout or dock
    

    #### Image Processing Functions ####


    def _add_pre_process(self, layout=None, separate_widget=False):
        """Add a widget for running the image pre-processing function, optionally in a separate dock."""
        pre_process_layout = QVBoxLayout()
        self.add_text_label(pre_process_layout, 'Image Pre-processing', bold=True) # Add a widget title label
        pre_process_button = QPushButton("Pre-process Image") # Create a button widget
        pre_process_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_pre_process_image, None, self.central_manager.active_data_class, self.viewer))
        pre_process_layout.addWidget(pre_process_button) # Add the button to the layout
        pre_process_widget = QWidget()
        pre_process_widget.setLayout(pre_process_layout)
        self._add_widget_to_layout_or_dock(pre_process_widget, layout, separate_widget, "Pre-process Image Dock")


    # Image Adjustment Functions 


    def _add_run_apply_rescale_intensity(self, layout=None, separate_widget=False):
        """Add a widget for rescaling image intensity values, optionally in a separate dock."""
        rescale_intensity_layout = QVBoxLayout()
        self.add_text_label(rescale_intensity_layout, 'Rescale Intensity', bold=True) # Add widget title label
        self.add_text_label(rescale_intensity_layout, 'Output Min') # Add a text label
        out_min_input = QLineEdit() # Create a text input
        rescale_intensity_layout.addWidget(out_min_input) # Add the text input to the layout
        self.add_text_label(rescale_intensity_layout, 'Output Max') # Add a text label
        out_max_input = QLineEdit() # Create a text input
        rescale_intensity_layout.addWidget(out_max_input) # Add the text input to the layout
        rescale_intensity_button = QPushButton("Rescale Intensity") # Create a button widget
        rescale_intensity_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_apply_rescale_intensity, None, out_min_input, out_max_input, self.viewer))
        rescale_intensity_layout.addWidget(rescale_intensity_button) # Add the button to the layout
        rescale_intensity_widget = QWidget()
        rescale_intensity_widget.setLayout(rescale_intensity_layout)
        self._add_widget_to_layout_or_dock(rescale_intensity_widget, layout, separate_widget, "Rescale Intensity Dock")


    def _add_run_invert_image(self, layout=None, separate_widget=False):
        """Add a widget for inverting image intensity values, optionally in a separate dock."""
        invert_image_layout = QVBoxLayout()
        self.add_text_label(invert_image_layout, 'Invert Image', bold=True) # Add widget title label
        invert_image_button = QPushButton("Invert Image") # Create a button widget
        invert_image_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_invert_image, None, self.viewer))
        invert_image_layout.addWidget(invert_image_button) # Add the button to the layout
        invert_image_widget = QWidget()
        invert_image_widget.setLayout(invert_image_layout)
        self._add_widget_to_layout_or_dock(invert_image_widget, layout, separate_widget, "Invert Image Dock")


    def _add_run_upscaling(self, layout=None, separate_widget=False):
        """Add a widget for upscaling images, optionally in a separate dock."""
        upscaling_layout = QVBoxLayout()
        self.add_text_label(upscaling_layout, 'Upscale Images', bold=True) # Add widget title label
        upscaling_checkbox = QCheckBox("Update Data Class") # Add a checkbox for updating the data class
        upscaling_checkbox.setChecked(True) # Set the checkbox to checked by default
        upscaling_layout.addWidget(upscaling_checkbox) # Add the checkbox to the layout
        upscaling_button = QPushButton("Run Upscaling") # Create a button widget
        upscaling_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_upscaling_func, None, upscaling_checkbox, self.central_manager.active_data_class, self.viewer))
        upscaling_layout.addWidget(upscaling_button) # Add the button to the layout
        upscaling_widget = QWidget()
        upscaling_widget.setLayout(upscaling_layout)
        self._add_widget_to_layout_or_dock(upscaling_widget, layout, separate_widget, "Upscaling Dock")


    # Background and Noise Correction Functions


    def _add_run_rb_gaussian_background_removal(self, layout=None, separate_widget=False):
        """Add a widget for rolling-ball and Gaussian background removal, optionally in a separate dock."""
        remove_background_layout = QVBoxLayout()
        self.add_text_label(remove_background_layout, 'RB-Gauss Background Removal', bold=True) # Add widget title label
        eq_int_checkbox = QCheckBox("Equalize Intensity") # Add a checkbox for equalizing intensity
        eq_int_checkbox.setChecked(False) # Set the checkbox to unchecked by default
        remove_background_layout.addWidget(eq_int_checkbox) # Add the checkbox to the layout   
        remove_background_button = QPushButton("Remove Background") # Create a button widget
        remove_background_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_rb_gaussian_background_removal, None, eq_int_checkbox, self.central_manager.active_data_class, self.viewer))
        remove_background_layout.addWidget(remove_background_button) # Add the button to the layout
        remove_background_widget = QWidget()
        remove_background_widget.setLayout(remove_background_layout)
        self._add_widget_to_layout_or_dock(remove_background_widget, layout, separate_widget, "Background Removal Dock")


    def _add_run_enhanced_rb_gaussian_bg_removal(self, layout=None, separate_widget=False):
        """Add a widget for rolling-ball and Gaussian background removal with edge enhancement, optionally in a separate dock."""
        remove_background_layout = QVBoxLayout()
        self.add_text_label(remove_background_layout, 'Enhanced RB-Gauss Background Removal', bold=True) # Add widget title label
        remove_background_button = QPushButton("Remove Background") # Create a button widget
        remove_background_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_enhanced_rb_gaussian_bg_removal, None, self.central_manager.active_data_class, self.viewer))
        remove_background_layout.addWidget(remove_background_button) # Add the button to the layout
        remove_background_widget = QWidget()
        remove_background_widget.setLayout(remove_background_layout)
        self._add_widget_to_layout_or_dock(remove_background_widget, layout, separate_widget, "Enhanced Background Removal Dock")


    def _add_run_wbns(self, layout=None, separate_widget=False):
        """Add a widget for wavelet background and noise subtraction, optionally in a separate dock."""
        WBNS_layout = QVBoxLayout() # Create a vertical layout widget
        self.add_text_label(WBNS_layout, 'Wavelet BG and Noise Subtraction', bold=True) # Add widget title label
        self.add_text_label(WBNS_layout, 'Noise Level') # Add a text label
        WBNS_noise_input = QLineEdit() # Create a text input
        WBNS_noise_input.setPlaceholderText('1') # Set a default text value
        WBNS_layout.addWidget(WBNS_noise_input) # Add the text input to the layout  
        self.add_text_label(WBNS_layout, 'PSF Size') # Add a text label
        WBNS_psf_input = QLineEdit() # Create a text input
        WBNS_psf_input.setPlaceholderText('3') # Set a default text value
        WBNS_layout.addWidget(WBNS_psf_input) # Add the text input to the layout
        WBNS_button = QPushButton("Run WBNS") # Create a button widget
        WBNS_button.clicked.connect(lambda: self.on_general_button_clicked( # Connect the button to the function
            run_wbns, None, WBNS_psf_input, WBNS_noise_input, self.viewer)) # function, viewer, *args
        WBNS_layout.addWidget(WBNS_button) # Add the button to the layout
        WBNS_widget = QWidget() # Create a main widget to contain the input widget
        WBNS_widget.setLayout(WBNS_layout) # Set the layout for the widget
        self._add_widget_to_layout_or_dock(WBNS_widget, layout, separate_widget, "WBNS Dock") # Add widget to layout or dock


    def _add_run_wavelet_noise_subtraction(self, layout=None, separate_widget=False):
        """Add a widget for wavelet noise subtraction, optionally in a separate dock."""
        wavelet_layout = QVBoxLayout() # Create a vertical layout widget
        self.add_text_label(wavelet_layout, 'Wavelet Noise Subtraction', bold=True)# Add widget title label
        self.add_text_label(wavelet_layout, 'Noise Level') # Add a text label
        wavelet_noise_input = QLineEdit() # Create a text input
        wavelet_noise_input.setPlaceholderText('1') # Set a default text value
        wavelet_layout.addWidget(wavelet_noise_input) # Add the text input to the layout
        self.add_text_label(wavelet_layout, 'PSF Size') # Add a text label
        wavelet_psf_input = QLineEdit() # Create a text input
        wavelet_psf_input.setPlaceholderText('3') # Set a default text value
        wavelet_layout.addWidget(wavelet_psf_input) # Add the text input to the layout
        wavelet_button = QPushButton("Run WNS") # Create a button widget
        wavelet_button.clicked.connect(lambda: self.on_general_button_clicked( # Connect the button to the function
            run_wavelet_noise_subtraction, None, wavelet_psf_input, wavelet_noise_input, self.viewer)) # function, viewer, *args
        wavelet_layout.addWidget(wavelet_button) # Add the button to the layout
        wavelet_widget = QWidget() # Create a main widget to contain the input widget
        wavelet_widget.setLayout(wavelet_layout) # Set the layout for the widget
        self._add_widget_to_layout_or_dock(wavelet_widget, layout, separate_widget, "WNS Dock") # Add widget to layout or dock


    def _add_run_apply_bilateral_filter(self, layout=None, separate_widget=False):
        """Add a widget for applying a bilateral filter, optionally in a separate dock."""
        bilateral_layout = QVBoxLayout()
        self.add_text_label(bilateral_layout, 'Bilateral Filter', bold=True) # Add widget title label
        self.add_text_label(bilateral_layout, 'Filter Size') # Add a text label
        filter_size_input = QLineEdit() # Create a text input
        bilateral_layout.addWidget(filter_size_input) # Add the text input to the layout
        bilateral_button = QPushButton("Apply Filter") # Create a button widget
        bilateral_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_apply_bilateral_filter, None, filter_size_input, self.viewer))
        bilateral_layout.addWidget(bilateral_button) # Add the button to the layout
        bilateral_widget = QWidget()
        bilateral_widget.setLayout(bilateral_layout)
        self._add_widget_to_layout_or_dock(bilateral_widget, layout, separate_widget, "Bilateral Filter Dock")



    # Image Enhancement and Filter Functions


    def _add_run_clahe(self, layout=None, separate_widget=False):
        """Add a widget for contrast-limited adaptive histogram equalization, optionally in a separate dock."""
        clahe_layout = QVBoxLayout()
        self.add_text_label(clahe_layout, 'Contrast-Limited Adapt. Hist. Equalization', bold=True) # Add widget title label
        self.add_text_label(clahe_layout, 'Clip Limit') # Add a text label
        clahe_clip_input = QLineEdit() # Create a text input
        clahe_clip_input.setPlaceholderText('0.0025') # Set a default text value
        clahe_layout.addWidget(clahe_clip_input) # Add the text input to the layout
        def_window_size = math.ceil(self.central_manager.active_data_class.data_repository['cell_diameter']//4) # Calculate the default window size
        self.add_text_label(clahe_layout, 'Window Size') # Add a text label    
        clahe_window_size_input = QLineEdit() # Create a text input
        clahe_window_size_input.setPlaceholderText(str(def_window_size)) # Set a default text value
        clahe_layout.addWidget(clahe_window_size_input) # Add the text input to the layout
        clahe_button = QPushButton("Run CLAHE") # Create a button widget
        clahe_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_clahe, None, clahe_clip_input, clahe_window_size_input,  self.viewer))
        clahe_layout.addWidget(clahe_button) # Add the button to the layout
        clahe_widget = QWidget()
        clahe_widget.setLayout(clahe_layout)
        self._add_widget_to_layout_or_dock(clahe_widget, layout, separate_widget, "CLAHE Dock")


    def _add_run_peak_and_edge_enhancement(self, layout=None, separate_widget=False):
        """Add a widget for peak and edge enhancement, optionally in a separate dock."""
        enhancement_layout = QVBoxLayout()
        self.add_text_label(enhancement_layout, 'Peak and Edge Enhancement', bold=True) # Add widget title label
        enhancement_button = QPushButton("Run Edge Enhancement") # Create a button widget
        enhancement_button.clicked.connect(lambda: self.on_general_button_clicked( 
            run_peak_and_edge_enhancement, None, self.central_manager.active_data_class, self.viewer))
        enhancement_layout.addWidget(enhancement_button) # Add the button to the layout
        enhancement_widget = QWidget() 
        enhancement_widget.setLayout(enhancement_layout)
        self._add_widget_to_layout_or_dock(enhancement_widget, layout, separate_widget, "Peak and Edge Enhancement Dock")


    def _add_run_morphological_gaussian_filter(self, layout=None, separate_widget=False):
        """Add a widget for morphological Gaussian filtering, optionally in a separate dock."""
        gauss_filter_layout = QVBoxLayout()
        self.add_text_label(gauss_filter_layout, 'Morphological Gaussian Filter', bold=True) # Add widget title label
        self.add_text_label(gauss_filter_layout, 'Filter Size') # Add a text label
        filter_size_input = QLineEdit() # Create a text input
        gauss_filter_layout.addWidget(filter_size_input) # Add the text input to the layout
        gauss_filter_button = QPushButton("Apply Filter") # Create a button widget
        gauss_filter_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_morphological_gaussian_filter, None, filter_size_input, self.viewer))
        gauss_filter_layout.addWidget(gauss_filter_button) # Add the button to the layout
        gauss_filter_widget = QWidget()
        gauss_filter_widget.setLayout(gauss_filter_layout)
        self._add_widget_to_layout_or_dock(gauss_filter_widget, layout, separate_widget, "Morphological Gaussian Dock")


    def _add_run_dpr(self, layout=None, separate_widget=False):
        """Add a widget for deblur by pixel reassignment, optionally in a separate dock."""
        DPR_layout = QVBoxLayout()
        self.add_text_label(DPR_layout, 'Deblur by Pixel Reassignment', bold=True)# Add widget title label
        self.add_text_label(DPR_layout, 'Gain Level') # Add a text label
        DPR_gain_input = QLineEdit() # Create a text input
        DPR_layout.addWidget(DPR_gain_input) # Add the text input to the layout
        self.add_text_label(DPR_layout, 'PSF Size') # Add a text label
        DPR_psf_input = QLineEdit() # Create a text input
        DPR_layout.addWidget(DPR_psf_input) # Add the text input to the layout
        DPR_button = QPushButton("Run DPR") # Create a button widget
        DPR_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_dpr, None, DPR_psf_input, DPR_gain_input, self.central_manager.active_data_class, self.viewer))
        DPR_layout.addWidget(DPR_button) # Add the button to the layout
        DPR_widget = QWidget() # Create a main widget to contain the input widget
        DPR_widget.setLayout(DPR_layout)
        self._add_widget_to_layout_or_dock(DPR_widget, layout, separate_widget, "DPR Dock")


    def _add_run_apply_laplace_of_gauss_filter(self, layout=None, separate_widget=False):
        """Add a widget for applying a Laplacian of Gaussian filter, optionally in a separate dock."""
        LoG_layout = QVBoxLayout()
        self.add_text_label(LoG_layout, 'Laplacian of Gaussian Filter', bold=True) # Add widget title label
        self.add_text_label(LoG_layout, 'Sigma Value') # Add a text label
        LoG_sigma_input = QLineEdit() # Create a text input
        LoG_layout.addWidget(LoG_sigma_input) # Add the text input to the layout
        LoG_button = QPushButton("Apply LoG Filter") # Create a button widget
        LoG_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_apply_laplace_of_gauss_filter, None, LoG_sigma_input, self.viewer))
        LoG_layout.addWidget(LoG_button) # Add the button to the layout
        LoG_widget = QWidget()
        LoG_widget.setLayout(LoG_layout)
        self._add_widget_to_layout_or_dock(LoG_widget, layout, separate_widget, "LoG Filter Dock")


    #### Image Segmentation Functions #### 


    def _add_run_fz_segmentation_and_merging(self, layout=None, separate_widget=False):
        """Add a widget for Felsenszwalb segmentation and region merging, optionally in a separate dock."""
        fz_layout = QVBoxLayout()
        self.add_text_label(fz_layout, 'Felsenszwalb Segmentation and Merging', bold=True) # Add a widget title label
        self.add_text_label(fz_layout, 'Scale') # Add a text label
        fz_scale_input = QLineEdit() # Create a text input
        fz_layout.addWidget(fz_scale_input) # Add the text input to the layout
        self.add_text_label(fz_layout, 'Sigma') # Add a text label
        fz_sigma_input = QLineEdit() # Create a text input
        fz_layout.addWidget(fz_sigma_input) # Add the text input to the layout
        self.add_text_label(fz_layout, 'Min Size') # Add a text label
        fz_min_size_input = QLineEdit() # Create a text input
        fz_layout.addWidget(fz_min_size_input) # Add the text input to the layout
        fz_button = QPushButton("Run Felsenszwalb Segmentation") # Create a button widget
        fz_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_fz_segmentation_and_merging, None, fz_scale_input, fz_sigma_input, fz_min_size_input, self.viewer))
        fz_layout.addWidget(fz_button) # Add the button to the layout
        fz_widget = QWidget()
        fz_widget.setLayout(fz_layout)
        self._add_widget_to_layout_or_dock(fz_widget, layout, separate_widget, "FZ Segmentation Dock")


    def _add_run_cellpose_segmentation(self, layout=None, separate_widget=False):
        """Add a widget for Cellpose segmentation, optionally in a separate dock."""
        cellpose_layout = QVBoxLayout()
        self.add_text_label(cellpose_layout, 'Cellpose Segmentation', bold=True) # Add a widget title label
        self.add_text_label(cellpose_layout, 'Select Image for Cellpose') # Add a text label
        cellpose_dropdown = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        cellpose_layout.addWidget(cellpose_dropdown) # Add the dropdown to the layout
        cellpose_button = QPushButton("Run Cellpose") # Create a button widget
        cellpose_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_cellpose_segmentation, self.viewer, cellpose_dropdown, self.central_manager.active_data_class, self.viewer))
        cellpose_layout.addWidget(cellpose_button) # Add the button to the layout
        cellpose_widget = QWidget()
        cellpose_widget.setLayout(cellpose_layout)
        self._add_widget_to_layout_or_dock(cellpose_widget, layout, separate_widget, "Cellpose Dock")


    def _add_run_train_and_apply_rf_classifier(self, layout=None, separate_widget=False):
        """Add a widget for training and applying a random forest pixel classifier, optionally in a separate dock."""
        rf_layout = QVBoxLayout()
        self.add_text_label(rf_layout, 'Random Forest Pixel Classifier', bold=True) # Add widget title label
        self.add_text_label(rf_layout, 'Select Annotations for Random Forest') # Add a text label
        rf_labels_dropdown = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        rf_layout.addWidget(rf_labels_dropdown) # Add the dropdown to the layout
        self.add_text_label(rf_layout, 'Select Image for Random Forest') # Add a text label
        rf_image_dropdown = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        rf_layout.addWidget(rf_image_dropdown) # Add the dropdown to the layout
        rf_button = QPushButton("Run Random Forest") # Create a button widget
        rf_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_train_and_apply_rf_classifier, self.viewer, rf_image_dropdown, rf_labels_dropdown, self.central_manager.active_data_class, self.viewer))
        rf_layout.addWidget(rf_button) # Add the button to the layout
        rf_widget = QWidget()
        rf_widget.setLayout(rf_layout)
        self._add_widget_to_layout_or_dock(rf_widget, layout, separate_widget, "Random Forest Dock")


    def _add_run_local_thresholding(self, layout=None, separate_widget=False):
        """Add a widget for applying local thresholding, optionally in a separate dock."""
        local_thresh_layout = QVBoxLayout()
        self.add_text_label(local_thresh_layout, 'Local Thresholding', bold=True) # Add widget title label
        self.add_text_label(local_thresh_layout, 'Select Thresholding Method') # Add a text label
        local_thresh_mode_dropdown = QComboBox() # Create a dropdown widget
        local_thresh_mode_dropdown.addItems(['Sauvola', 'Niblack', 'AND', 'OR']) # Add items to the dropdown
        local_thresh_layout.addWidget(local_thresh_mode_dropdown) # Add the dropdown to the layout

        # k_value slider
        k_label = QLabel("Threshold k value:") # Add a text label
        k_slider = QSlider(Qt.Horizontal) # Create a slider widget
        k_slider.setRange(0, 100)  # 100 steps from 0 to 100
        k_slider.setValue(50)  # default is 0
        k_slider.setSingleStep(1)  # Adjust for 0.01 steps
        k_label_value = QLabel("0.0") 
        def update_k_label(val):
            # Convert slider integer value to float
            float_val = (val / 50.0) - 1 # Convert slider value to float range from -1 to 1 in 0.01 steps
            k_label_value.setText(str(round(float_val, 2))) # Update the label number text
        k_slider.valueChanged.connect(update_k_label) # Connect the slider to the update function
        local_thresh_layout.addWidget(k_label) # Add the text label to the layout
        local_thresh_layout.addWidget(k_slider) # Add the slider to the layout
        local_thresh_layout.addWidget(k_label_value) # Add the label value to the layout

        # window_size slider
        def_window_size = math.ceil(self.central_manager.active_data_class.data_repository['ball_radius']) # Calculate the default window size  
        window_label = QLabel(f"Window Size:") # Add a text label
        window_slider = QSlider(Qt.Horizontal) # Create a slider widget
        window_slider.setRange(10, 250) # 100 steps from 10 to 250
        window_slider.setValue(def_window_size) # Set the default value
        window_label_value = QLabel(str(def_window_size)) # Set the default value
        window_slider.valueChanged.connect(lambda val: window_label_value.setText(str(val))) # Connect the slider to the update function
        local_thresh_layout.addWidget(window_label) # Add the text label to the layout
        local_thresh_layout.addWidget(window_slider) # Add the slider to the layout
        local_thresh_layout.addWidget(window_label_value) # Add the slider value to the layout

        # Button to apply thresholding
        local_thresh_button = QPushButton("Apply Thresholding") # Create a button widget
        local_thresh_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_local_thresholding, None, k_slider, window_slider, local_thresh_mode_dropdown.currentText(), self.viewer))
        local_thresh_layout.addWidget(local_thresh_button) # Add the button to the layout
        sauvola_widget = QWidget()
        sauvola_widget.setLayout(local_thresh_layout)
        self._add_widget_to_layout_or_dock(sauvola_widget, layout, separate_widget, "Local Thresholding Dock")


    def _add_run_segment_subcellular_objects(self, layout=None, separate_widget=False):
        """Add a widget for subcellular object segmentation, optionally in a separate dock."""
        process_cells_layout = QVBoxLayout()
        self.add_text_label(process_cells_layout, 'Subcellular Object Segmentation', bold=True) # Add widget title label
        self.add_text_label(process_cells_layout, 'Select Pre-Processed Image to Segment') # Add a text label
        process_cells_image1_dropdown = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        process_cells_layout.addWidget(process_cells_image1_dropdown) # Add the dropdown to the layout
        self.add_text_label(process_cells_layout, 'Select Fluorescence Image to Process') # Add a text label
        process_cells_image2_dropdown = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        process_cells_layout.addWidget(process_cells_image2_dropdown) # Add the dropdown to the layout
        process_cells_button = QPushButton("Run Condensate Segmentation") # Create a button widget
        process_cells_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_segment_subcellular_objects, self.viewer, process_cells_image1_dropdown, process_cells_image2_dropdown, self.central_manager.active_data_class, self.viewer))
        process_cells_layout.addWidget(process_cells_button) # Add the button to the layout
        process_cells_widget = QWidget()
        process_cells_widget.setLayout(process_cells_layout)
        self._add_widget_to_layout_or_dock(process_cells_widget, layout, separate_widget, "Condensate Segmentation Dock")


    #### Image Feature Analysis Functions ####


    def _add_run_cell_analysis_func(self, layout=None, separate_widget=False):
        """Add a widget for cell analysis, optionally in a separate dock."""
        cell_segmentation_layout = QVBoxLayout()
        self.add_text_label(cell_segmentation_layout, 'Cell/Nuclei Analysis', bold=True) # Add widget title label
        self.add_text_label(cell_segmentation_layout, 'Select Mask Layer for Cell Analysis') # Add a text label
        cell_segmentation_dropdown_labels = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        cell_segmentation_layout.addWidget(cell_segmentation_dropdown_labels) # Add the dropdown to the layout
        self.add_text_label(cell_segmentation_layout, 'Select Mask Layer to Omit') # Add a text label
        cell_segmentation_dropdown_omit = self.create_layer_dropdown(napari.layers.Labels)
        cell_segmentation_dropdown_omit.insertItem(0, "None")
        cell_segmentation_layout.addWidget(cell_segmentation_dropdown_omit)
        self.add_text_label(cell_segmentation_layout, 'Select Image for Cell Analysis') # Add a text label
        cell_segmentation_dropdown_images = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        cell_segmentation_layout.addWidget(cell_segmentation_dropdown_images) # Add the dropdown to the layout
        cell_analysis_button = QPushButton("Run Cell Analyzer") # Create a button widget
        cell_analysis_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_cell_analysis_func, self.viewer, cell_segmentation_dropdown_labels, cell_segmentation_dropdown_omit, cell_segmentation_dropdown_images, self.central_manager.active_data_class, self.viewer))
        cell_segmentation_layout.addWidget(cell_analysis_button) # Add the button to the layout
        cell_segmentation_widget = QWidget()
        cell_segmentation_widget.setLayout(cell_segmentation_layout)
        self._add_widget_to_layout_or_dock(cell_segmentation_widget, layout, separate_widget, "Cell Analysis Dock")


    def _add_run_puncta_analysis_func(self, layout=None, separate_widget=False):
        """Add a widget for puncta analysis, optionally in a separate dock."""
        measure_puncta_layout = QVBoxLayout()
        self.add_text_label(measure_puncta_layout, 'Condensate Analysis', bold=True) # Add widget title label
        self.add_text_label(measure_puncta_layout, 'Select Puncta Mask for Measurement') # Add a text label
        puncta_measure_dropdown_labels = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        measure_puncta_layout.addWidget(puncta_measure_dropdown_labels) # Add the dropdown to the layout
        self.add_text_label(measure_puncta_layout, 'Select Image for Puncta Measurement') # Add a text label
        puncta_measure_dropdown_images = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        measure_puncta_layout.addWidget(puncta_measure_dropdown_images) # Add the dropdown to the layout
        puncta_measure_button = QPushButton("Run Condensate Analyzer") # Create a button widget
        puncta_measure_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_puncta_analysis_func, self.viewer, puncta_measure_dropdown_labels, puncta_measure_dropdown_images, self.central_manager.active_data_class, self.viewer))
        measure_puncta_layout.addWidget(puncta_measure_button) # Add the button to the layout
        measure_puncta_widget = QWidget()
        measure_puncta_widget.setLayout(measure_puncta_layout)
        self._add_widget_to_layout_or_dock(measure_puncta_widget, layout, separate_widget, "Condensate Analysis Dock")


    #### Colocalization Analysis Functions ####


    # Pixel-Wise Correlation Functions 

    def _add_run_autocorrelation_analysis(self, layout=None, separate_widget=False):
        """Add a widget for autocorrelation analysis, optionally in a separate dock."""
        ACF_layout = QVBoxLayout() # Create a vertical layout widget
        self.add_text_label(ACF_layout, 'Auto-Correlation Function Analysis', bold=True) # Add widget title label
        self.add_text_label(ACF_layout, 'Select Image for Analysis') # Add a dropdown text label
        ACF_image_dropdown = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        ACF_layout.addWidget(ACF_image_dropdown) # Add the dropdown to the layout
        self.add_text_label(ACF_layout, 'Select ROI Mask') # Add a dropdown text label
        ACF_roi_dropdown = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        ACF_roi_dropdown.insertItem(0, "None") # Add a None option to the dropdown
        ACF_layout.addWidget(ACF_roi_dropdown) # Add the dropdown to the layout 

        self.add_text_label(ACF_layout, 'Set range to fit data (px)')  # Add a label for range inputs
        # Create the QHBoxLayout for the range inputs
        range_layout = QHBoxLayout()
        lower_limit_input = QLineEdit()  # Create QLineEdit for the lower limit
        range_layout.addWidget(lower_limit_input)  # Add the lower limit input to the layout
        self.add_text_label(range_layout, 'to')  # Add a text label
        upper_limit_input = QLineEdit()  # Create QLineEdit for the upper limit
        range_layout.addWidget(upper_limit_input)  # Add the upper limit input to the layout
        ACF_layout.addLayout(range_layout)  # Add the range inputs layout to the main vertical layout

        ACF_button = QPushButton("Calculate ACF") # Create a button widget
        ACF_button.clicked.connect(lambda: self.on_general_button_clicked( # Connect the button to the function
            run_autocorrelation_analysis, self.viewer, ACF_image_dropdown, ACF_roi_dropdown, lower_limit_input, upper_limit_input, self.central_manager.active_data_class))
        ACF_layout.addWidget(ACF_button) # Add the button to the layout
        ACF_widget = QWidget() # Create a main widget to contain the input widget
        ACF_widget.setLayout(ACF_layout) # Set the layout for the widget
        self._add_widget_to_layout_or_dock(ACF_widget, layout, separate_widget, "ACF Dock") # Add widget to layout or dock

        
    def _add_run_ccf_analysis(self, layout=None, separate_widget=False):
        """Add a widget for cross-correlation function analysis, optionally in a separate dock."""
        CCF_layout = QVBoxLayout() # Create a vertical layout widget
        self.add_text_label(CCF_layout, 'Cross-Correlation Function Analysis', bold=True) # Add widget title label
        self.add_text_label(CCF_layout, 'Select Image 1') # Add a dropdown text label
        CCF_image1_dropdown = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        CCF_layout.addWidget(CCF_image1_dropdown) # Add the dropdown to the layout
        self.add_text_label(CCF_layout, 'Select Image 2') # Add a dropdown text label
        CCF_image2_dropdown = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        CCF_layout.addWidget(CCF_image2_dropdown) # Add the dropdown to the layout
        self.add_text_label(CCF_layout, 'Select ROI Mask') # Add a dropdown text label
        CCF_roi_dropdown = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        CCF_roi_dropdown.insertItem(0, "None") # Add a None option to the dropdown
        CCF_layout.addWidget(CCF_roi_dropdown) # Add the dropdown to the layout
        CCF_button = QPushButton("Calculate CCF") # Create a button widget
        CCF_button.clicked.connect(lambda: self.on_general_button_clicked( # Connect the button to the function
            run_ccf_analysis, self.viewer, CCF_image1_dropdown, CCF_image2_dropdown, CCF_roi_dropdown, self.central_manager.active_data_class))
        CCF_layout.addWidget(CCF_button) # Add the button to the layout
        CCF_widget = QWidget() # Create a main widget to contain the input widget
        CCF_widget.setLayout(CCF_layout) # Set the layout for the widget
        self._add_widget_to_layout_or_dock(CCF_widget, layout, separate_widget, "CCF Dock")


    def _add_run_pwcca(self, layout=None, separate_widget=False):
        """Add a widget for pixel-wise correlation coefficient analysis, optionally in a separate dock."""
        PWCCA_layout = QVBoxLayout() # Create a vertical layout widget
        self.add_text_label(PWCCA_layout, 'Pixel-Wise Correlation Coefficient Analysis', bold=True) # Add widget title label
        self.add_text_label(PWCCA_layout, 'Select Image 1') # Add a dropdown text label
        PWCCA_image1_dropdown = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        PWCCA_layout.addWidget(PWCCA_image1_dropdown) # Add the dropdown to the layout
        self.add_text_label(PWCCA_layout, 'Select Image 2') # Add a dropdown text label
        PWCCA_image2_dropdown = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        PWCCA_layout.addWidget(PWCCA_image2_dropdown) # Add the dropdown to the layout
        PWCCA_roi_dropdown = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        self.add_text_label(PWCCA_layout, 'Select ROI Mask') # Add a dropdown text label
        PWCCA_roi_dropdown.insertItem(0, "None") # Add a None option to the dropdown
        PWCCA_layout.addWidget(PWCCA_roi_dropdown) # Add the dropdown to the layout
        PWCCA_button = QPushButton("Calculate PWCCA") # Create a button widget
        PWCCA_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_pwcca, self.viewer, PWCCA_image1_dropdown, PWCCA_image2_dropdown, PWCCA_roi_dropdown, self.central_manager.active_data_class, self.viewer))
        PWCCA_layout.addWidget(PWCCA_button) # Add the button to the layout
        PWCCA_widget = QWidget() # Create a main widget to contain the input widget
        PWCCA_widget.setLayout(PWCCA_layout) # Set the layout for the widget
        self._add_widget_to_layout_or_dock(PWCCA_widget, layout, separate_widget, "PWCCA Dock")


    # Object-Based Colocalization Functions
        

    def _add_run_obca(self, layout=None, separate_widget=False):
        """Add a widget for object-based colocalization analysis, optionally in a separate dock."""
        OBCA_layout = QVBoxLayout() # Create a vertical layout widget
        self.add_text_label(OBCA_layout, 'Object-Based Colocalization Analysis', bold=True) # Add widget title label
        self.add_text_label(OBCA_layout, 'Select Image 1') # Add a dropdown text label
        OBCA_mask1_dropdown = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        OBCA_layout.addWidget(OBCA_mask1_dropdown) # Add the dropdown to the layout
        self.add_text_label(OBCA_layout, 'Select Image 2') # Add a dropdown text label
        OBCA_mask2_dropdown = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        OBCA_layout.addWidget(OBCA_mask2_dropdown) # Add the dropdown to the layout
        self.add_text_label(OBCA_layout, 'Select ROI Mask') # Add a dropdown text label
        OBCA_roi_dropdown = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        OBCA_roi_dropdown.insertItem(0, "None") # Add a None option to the dropdown
        OBCA_layout.addWidget(OBCA_roi_dropdown) # Add the dropdown to the layout
        OBCA_button = QPushButton("Calculate OBCA") # Create a button widget
        OBCA_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_obca, self.viewer, OBCA_mask1_dropdown, OBCA_mask2_dropdown, OBCA_roi_dropdown, self.central_manager.active_data_class))
        OBCA_layout.addWidget(OBCA_button) # Add the button to the layout
        OBCA_widget = QWidget() # Create a main widget to contain the input widget
        OBCA_widget.setLayout(OBCA_layout) # Set the layout for the widget
        self._add_widget_to_layout_or_dock(OBCA_widget, layout, separate_widget, "OBCA Dock")


    def _add_run_manders_coloc(self, layout=None, separate_widget=False):
        """Add a widget for Mander's colocalization coefficient analysis, optionally in a separate dock."""
        manders_layout = QVBoxLayout()
        self.add_text_label(manders_layout, "Mander's Coloc Coefficient Analysis", bold=True) # Add widget title label
        self.add_text_label(manders_layout, 'Select Image 1') # Add a dropdown text label
        manders_image1_dropdown = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        manders_layout.addWidget(manders_image1_dropdown) # Add the dropdown to the layout
        self.add_text_label(manders_layout, 'Select Mask 2') # Add a dropdown text label
        manders_image2_dropdown = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        manders_layout.addWidget(manders_image2_dropdown) # Add the dropdown to the layout
        self.add_text_label(manders_layout, 'Select ROI Mask') # Add a dropdown text label
        manders_roi_dropdown = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        manders_roi_dropdown.insertItem(0, "None") # Add a None option to the dropdown
        manders_layout.addWidget(manders_roi_dropdown) # Add the dropdown to the layout
        manders_button = QPushButton("Calculate Mander's Coefficient") # Create a button widget
        manders_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_manders_coloc, self.viewer, manders_image1_dropdown, manders_image2_dropdown, manders_roi_dropdown, self.central_manager.active_data_class))
        manders_layout.addWidget(manders_button) # Add the button to the layout
        manders_widget = QWidget()
        manders_widget.setLayout(manders_layout)
        self._add_widget_to_layout_or_dock(manders_widget, layout, separate_widget, "Manders Coefficient Dock")
     

    #### Label and Mask Tools ####


    # Labeleled Mask Tools 

    def _add_run_convert_labels_to_mask(self, layout=None, separate_widget=False):
        """Add a widget for converting labels to binary masks, optionally in a separate dock."""
        convert_labels_layout = QVBoxLayout()
        self.add_text_label(convert_labels_layout, 'Convert Labels to Binary Mask', bold=True) # Add widget title label
        self.add_text_label(convert_labels_layout, 'Select Labels Layer to Convert') # Add a text label
        convert_labels_dropdown = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        convert_labels_layout.addWidget(convert_labels_dropdown) # Add the dropdown to the layout
        convert_labels_button = QPushButton("Convert Labels to Mask") # Create a button widget
        convert_labels_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_convert_labels_to_mask, self.viewer, convert_labels_dropdown, self.viewer))
        convert_labels_layout.addWidget(convert_labels_button) # Add the button to the layout
        convert_labels_widget = QWidget()
        convert_labels_widget.setLayout(convert_labels_layout)
        self._add_widget_to_layout_or_dock(convert_labels_widget, layout, separate_widget, "Labels to Mask Converter")

               
    def _add_run_measure_region_props(self, layout=None, separate_widget=False):
        """Add a widget for measuring region properties, optionally in a separate dock."""
        rp_layout = QVBoxLayout()
        self.add_text_label(rp_layout, 'Labeled Region Properties Measurement', bold=True) # Add widget title label
        self.add_text_label(rp_layout, 'Select Labeled Mask to Measure') # Add a text label
        rp_dropdown_layers = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        rp_layout.addWidget(rp_dropdown_layers) # Add the dropdown to the layout
        self.add_text_label(rp_layout, 'Select Intensity Image to Measure') # Add a text label
        rp_dropdown_image = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        rp_layout.addWidget(rp_dropdown_image) # Add the dropdown to the layout
        rp_button = QPushButton("Measure Region Properties") # Create a button widget
        rp_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_measure_region_props, self.viewer, rp_dropdown_layers, rp_dropdown_image, self.central_manager.active_data_class))
        rp_layout.addWidget(rp_button) # Add the button to the layout
        rp_widget = QWidget() 
        rp_widget.setLayout(rp_layout)
        self._add_widget_to_layout_or_dock(rp_widget, layout, separate_widget, "Region Properties Dock")


    def _add_run_update_labels(self, layout=None, separate_widget=False):
        """Add a widget for updating label values, optionally in a separate dock."""
        label_layout = QVBoxLayout()
        self.add_text_label(label_layout, 'Change Label Values', bold=True) # Add widget title label
        self.add_text_label(label_layout, 'New Label or Increment Amount') # Add a text label
        new_label_input = QLineEdit() # Add a text input for new label value
        label_layout.addWidget(new_label_input) # Add the text input to the layout
        # Radio buttons to select mode
        increment_mode = QRadioButton("Increment All Labels") # Add a radio button for increment mode
        specific_label_mode = QRadioButton("Change Specific Label") # Add a radio button for specific label mode
        increment_mode.setChecked(True) # Set the increment mode as the default
        label_layout.addWidget(increment_mode) # Add the radio button to the layout
        label_layout.addWidget(specific_label_mode) # Add the radio button to the layout
        # Button to apply changes
        update_button = QPushButton("Update Labels") # Create a button widget
        update_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_update_labels, None, new_label_input, increment_mode, self.viewer))
        label_layout.addWidget(update_button) # Add the button to the layout
        label_widget = QWidget()
        label_widget.setLayout(label_layout)
        self._add_widget_to_layout_or_dock(label_widget, layout, separate_widget, "Label Updater Dock")


    # Binary Mask Tools 


    def _add_run_label_binary_mask(self, layout=None, separate_widget=False):
        """Add a widget for labeling binary masks, optionally in a separate dock."""
        label_mask_layout = QVBoxLayout()
        self.add_text_label(label_mask_layout, 'Binary Mask Labeling', bold=True) # Add widget title label
        self.add_text_label(label_mask_layout, 'Select Binary Mask to Label') # Add a text label
        label_mask_dropdown = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        label_mask_layout.addWidget(label_mask_dropdown) # Add the dropdown to the layout
        label_mask_button = QPushButton("Label Binary Mask") # Create a button widget
        label_mask_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_label_binary_mask, self.viewer, label_mask_dropdown, self.viewer))
        label_mask_layout.addWidget(label_mask_button) # Add the button to the layout
        label_mask_widget = QWidget()
        label_mask_widget.setLayout(label_mask_layout)
        self._add_widget_to_layout_or_dock(label_mask_widget, layout, separate_widget, "Binary Mask Labeler")


    def _add_run_measure_binary_mask(self, layout=None, separate_widget=False):
        """Add a widget for measuring binary masks, optionally in a separate dock."""
        mbm_layout = QVBoxLayout()
        self.add_text_label(mbm_layout, 'Binary Mask Measurement', bold=True) # Add widget title label
        self.add_text_label(mbm_layout, 'Select Binary Mask to Measure') # Add a text label
        mbm_dropdown_labels = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        mbm_layout.addWidget(mbm_dropdown_labels) # Add the dropdown to the layout
        self.add_text_label(mbm_layout, 'Select Intensity Image to Measure') # Add a text label
        mbm_dropdown_images = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        mbm_layout.addWidget(mbm_dropdown_images) # Add the dropdown to the layout
        mbm_button = QPushButton("Measure Binary Mask") # Create a button widget
        mbm_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_measure_binary_mask, self.viewer, mbm_dropdown_labels, mbm_dropdown_images, self.central_manager.active_data_class))
        mbm_layout.addWidget(mbm_button) # Add the button to the layout
        mbm_widget = QWidget()
        mbm_widget.setLayout(mbm_layout)
        self._add_widget_to_layout_or_dock(mbm_widget, layout, separate_widget, "Binary Mask Measurement")


    def _add_run_binary_morph_operation(self, layout=None, separate_widget=False):
        """Add a widget for binary morphological operations, optionally in a separate dock."""
        bmo_layout = QVBoxLayout()
        self.add_text_label(bmo_layout, 'Binary Morphological Operations', bold=True) # Add widget title label
        self.add_text_label(bmo_layout, 'Select ROI Mask') # Add a text label
        bmo_roi_dropdown = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        bmo_roi_dropdown.insertItem(0, "None") # Add a None option to the dropdown
        bmo_layout.addWidget(bmo_roi_dropdown)

        # Add input widgets for morphological operation parameters
        self.add_text_label(bmo_layout, 'Number of Iterations')
        bmo_iter_input = QLineEdit()
        bmo_layout.addWidget(bmo_iter_input)
        self.add_text_label(bmo_layout, 'Structuring Element Size')
        bmo_elem_size_input = QLineEdit()
        bmo_layout.addWidget(bmo_elem_size_input)
        self.add_text_label(bmo_layout, 'Structuring Element Shape')
        bmo_elem_shape_dropdown = QComboBox()
        bmo_elem_shape_dropdown.addItems(['Disk', 'Diamond', 'Square', 'Star', 'Cross'])
        bmo_layout.addWidget(bmo_elem_shape_dropdown)   
        self.add_text_label(bmo_layout, 'Morphological Operation')
        bmo_mode_dropdown = QComboBox()
        bmo_mode_dropdown.addItems(['Erosion', 'Dilation', 'Opening', 'Closing', 'Fill Holes'])
        bmo_layout.addWidget(bmo_mode_dropdown)

        # Button to apply morphological operation
        bmo_button = QPushButton("Run Morphological Operation")
        bmo_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_binary_morph_operation, self.viewer, bmo_roi_dropdown, bmo_iter_input, bmo_elem_size_input, bmo_elem_shape_dropdown.currentText(), bmo_mode_dropdown.currentText(), self.viewer))
        bmo_layout.addWidget(bmo_button)
        bmo_widget = QWidget()
        bmo_widget.setLayout(bmo_layout)
        self._add_widget_to_layout_or_dock(bmo_widget, layout, separate_widget, "Binary Morphological Operation")


    #### Layer Operations ####


    def _add_run_simple_multi_merge(self, layout=None, separate_widget=False):
        """Add a widget for simple multi-layer merging, optionally in a separate dock."""
        simple_merge_layout = QVBoxLayout()
        self.add_text_label(simple_merge_layout, 'Simple Multi-Layer Merging', bold=True) # Add widget title label
        self.add_text_label(simple_merge_layout, 'Select Blending Mode') # Add a text label
        simple_merge_mode_dropdown = QComboBox() # Create a dropdown widget
        simple_merge_mode_dropdown.addItems(['Additive', 'Mean', 'Max', 'Min']) # Add items to the dropdown
        simple_merge_layout.addWidget(simple_merge_mode_dropdown) # Add the dropdown to the layout
        simple_merge_button = QPushButton("Merge Active Layers") # Create a button widget
        simple_merge_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_simple_multi_merge, None, simple_merge_mode_dropdown.currentText(), self.viewer))
        simple_merge_layout.addWidget(simple_merge_button) # Add the button to the layout
        simple_merge_widget = QWidget()
        simple_merge_widget.setLayout(simple_merge_layout)
        self._add_widget_to_layout_or_dock(simple_merge_widget, layout, separate_widget, "Simple Multi-Layer Merging")


    def _add_run_advanced_two_layer_merge(self, layout=None, separate_widget=False):
        """Add a widget for advanced two-layer merging, optionally in a separate dock."""
        advanced_merge_layout = QVBoxLayout()
        self.add_text_label(advanced_merge_layout, 'Advanced 2-Layer Merging', bold=True) # Add widget title label
        self.add_text_label(advanced_merge_layout, 'Select Base Layer for Merging') # Add a text label
        layer1_merge_dropdown = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        advanced_merge_layout.addWidget(layer1_merge_dropdown) # Add the dropdown to the layout
        self.add_text_label(advanced_merge_layout, 'Select Blend Layer for Merging') # Add a text label
        layer2_merge_dropdown = self.create_layer_dropdown(napari.layers.Image) # Create a dropdown widget
        advanced_merge_layout.addWidget(layer2_merge_dropdown) # Add the dropdown to the layout
        self.add_text_label(advanced_merge_layout, 'Select Blending Mode') # Add a text label
        advanced_merge_mode_dropdown = QComboBox() # Create a dropdown widget
        advanced_merge_mode_dropdown.addItems(['Subtractive', 'Screen_blending', 'Abs_difference', 'Alpha_blending', 'Blend'])
        advanced_merge_layout.addWidget(advanced_merge_mode_dropdown)
        
        # Alpha/Blend Slider
        slider_label = QLabel("Alpha/Blend Value:") # Add a text label
        alpha_blend_slider = QSlider(Qt.Horizontal) # Create a slider widget
        alpha_blend_slider.setRange(0, 10)  # 100 steps from 0 to 100
        alpha_blend_slider.setValue(5)  # default is 0.5
        alpha_blend_slider.setSingleStep(1)  # Adjust for 0.01 steps
        slider_label_value = QLabel("0.5") 
        def update_slider_label(val):
            # Convert slider integer value to float
            float_val = val * 0.1
            slider_label_value.setText(str(round(float_val, 2))) # Update the label number text
        alpha_blend_slider.valueChanged.connect(update_slider_label) # Connect the slider to the update function
        advanced_merge_layout.addWidget(slider_label) # Add the text label to the layout
        advanced_merge_layout.addWidget(alpha_blend_slider) # Add the slider to the layout
        advanced_merge_layout.addWidget(slider_label_value) # Add the slider value to the layout

        # Button to apply merging
        advanced_merge_button = QPushButton("Merge Layers")
        advanced_merge_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_advanced_two_layer_merge, self.viewer, layer1_merge_dropdown, layer2_merge_dropdown, advanced_merge_mode_dropdown.currentText(), alpha_blend_slider, self.viewer))
        advanced_merge_layout.addWidget(advanced_merge_button)
        # Create a main widget to contain the input widget
        advanced_merge_widget = QWidget()
        advanced_merge_widget.setLayout(advanced_merge_layout)
        self._add_widget_to_layout_or_dock(advanced_merge_widget, layout, separate_widget, "Advanced 2-Layer Merging")
 

    #### Data Visualization Functions ####
        

    def _add_plotting_widget(self, layout=None, separate_widget=False):
        """Add a widget for plotting data, optionally in a separate dock."""
        plot_widget = PlottingWidget(self.central_manager) # Create the plotting widget by instantiating its class
        self._add_widget_to_layout_or_dock(plot_widget, layout, separate_widget, "Plotting Widget")


class AnalysisMethodsUI(BaseUIClass):
    """
    A user interface (UI) class designed to manage and switch between different analysis
    methodologies within a Napari Viewer environment. It facilitates the dynamic 
    instantiation of data processing classes and their associated UIs based on the user's 
    selection, supporting a flexible and modular approach to data analysis.

    Attributes
    ----------
    viewer : napari.Viewer
        The graphical viewer instance that the UI class interacts with. This viewer is
        used to display and manage the visual elements of the analysis interfaces.
    central_manager : CentralManager Class
        An instance of a central management class that coordinates the active data and
        analysis state across different components of the application.

    Methods
    -------
    _switch_analysis(data_class, ui_class, *data_class_args, **data_class_kwargs):
        Dynamically switches the analysis method by instantiating the given data processing
        class and its associated UI class, effectively updating the analysis interface.
    _switch_to_condensate_analysis(*args, **kwargs):
        Switches the analysis interface to condensate analysis, a specific type of analysis
        method.
    _switch_to_object_coloc_analysis(*args, **kwargs):
        Switches the analysis interface to object colocalization analysis.
    _switch_to_pixel_coloc_analysis(*args, **kwargs):
        Switches the analysis interface to pixel colocalization analysis.
    _switch_to_general_analysis(*args, **kwargs):
        Switches the analysis interface to a general analysis mode.
    _switch_to_fibril_analysis(*args, **kwargs):
        Switches the analysis interface to fibril analysis, focusing on fibril structures.
    """
    def __init__(self, viewer, central_manager):
        """
        Initializes the AnalysisMethodsUI class with a viewer and central manager.

        Parameters
        ----------
        viewer : napari.Viewer
            The graphical viewer instance to be used by the UI class.
        central_manager : CentralManagerType
            The central management instance responsible for managing data and analysis state.
        """
        super().__init__(viewer)
        self.central_manager = central_manager

        
    def _switch_analysis(self, data_class, ui_class, *data_class_args, **data_class_kwargs):
        """
        Switches the current analysis method by initializing the specified data processing
        class and its corresponding UI class.

        Parameters
        ----------
        data_class : type
            The class of the data processing module to be initialized.
        ui_class : type
            The class of the UI module associated with the data processing module.
        *data_class_args :
            Variable length argument list for initializing `data_class`.
        **data_class_kwargs :
            Arbitrary keyword arguments for initializing `data_class`.
        """
        # Clear current dock to prepare for the new analysis UI
        self.clear_dock()

        # Create new BaseDataClass instance with existing repository
        new_data_class = BaseDataClass(
            base_data_repository=self.central_manager.active_data_class.data_repository
        )

        # Initialize the data/project class with provided arguments and keyword arguments
        #self.central_manager.set_active_data_class(data_class(*data_class_args, **data_class_kwargs))
        self.central_manager.set_active_data_class(new_data_class)
        # Instantiate the analysis UI class and set up its UI components
        self.current_analysis_ui = ui_class(self.viewer, self.central_manager)
        self.current_analysis_ui.setup_ui()

    # Each of the following methods provides a convenient way to switch
    # to a specific type of analysis, encapsulating the instantiation of
    # both the data processing class and its associated UI class.

    def _switch_to_condensate_analysis(self, *args, **kwargs):
        """
        Switches the analysis interface to condensate analysis.

        Parameters
        ----------
        *args :
            Arguments to pass to the `AnalysisDataClass`.
        **kwargs :
            Keyword arguments to pass to the `AnalysisDataClass`.
        """
        self._switch_analysis(BaseDataClass, CondensateAnalysisUI, *args, **kwargs)

    def _switch_to_object_coloc_analysis(self, *args, **kwargs):
        """
        Switches the analysis interface to object colocalization analysis.

        Parameters
        ----------
        *args :
            Arguments to pass to the `AnalysisDataClass`.
        **kwargs :
            Keyword arguments to pass to the `AnalysisDataClass`.
        """
        self._switch_analysis(BaseDataClass, ObjectColocAnalysisUI, *args, **kwargs)

    def _switch_to_pixel_coloc_analysis(self, *args, **kwargs):
        """
        Switches the analysis interface to pixel colocalization analysis.

        Parameters
        ----------
        *args :
            Arguments to pass to the `AnalysisDataClass`.
        **kwargs :
            Keyword arguments to pass to the `AnalysisDataClass`.
        """
        self._switch_analysis(BaseDataClass, PixelColocAnalysisUI, *args, **kwargs)

    def _switch_to_general_analysis(self, *args, **kwargs):
        """
        Switches the analysis interface to a general analysis mode.

        Parameters
        ----------
        *args :
            Arguments to pass to the `AnalysisDataClass`.
        **kwargs :
            Keyword arguments to pass to the `AnalysisDataClass`.
        """
        self._switch_analysis(BaseDataClass, GeneralAnalysisUI, *args, **kwargs)

    def _switch_to_fibril_analysis(self, *args, **kwargs):
        """
        Switches the analysis interface to fibril analysis, focusing on the study of fibril structures.

        Parameters
        ----------
        *args :
            Arguments to pass to the `AnalysisDataClass`.
        **kwargs :
            Keyword arguments to pass to the `AnalysisDataClass`.
        """
        self._switch_analysis(BaseDataClass, FibrilAnalysisUI, *args, **kwargs)



class CondensateAnalysisUI(AnalysisMethodsUI):
    """
    A specialized user interface class for condensate analysis within a larger analytical
    framework. Inherits from AnalysisMethodsUI to utilize the base functionalities and to
    add specific components relevant to condensate analysis.

    This class sets up a custom layout for the analysis of condensates, incorporating a
    series of predefined analysis and processing steps. It dynamically constructs the
    UI components based on the requirements of condensate analysis, facilitating an
    efficient workflow for users.

    Attributes
    ----------
    viewer : napari.Viewer
        The graphical viewer instance used for display and interaction purposes.
    central_manager : CentralManagerType
        A central management instance responsible for managing data and analysis state,
        facilitating the interaction between different components of the application.
    condensate_layout : QVBoxLayout
        The layout manager for arranging UI components vertically. It is used to organize
        the specific UI components required for condensate analysis.

    Methods
    -------
    setup_ui():
        Initializes and arranges the UI components specific to condensate analysis into
        the application's interface, ensuring a user-friendly environment for conducting
        analyses.
    """

    def __init__(self, viewer, central_manager):
        """
        Initializes the CondensateAnalysisUI class with a viewer and central manager,
        setting up the initial layout for further UI component addition.

        Parameters
        ----------
        viewer : napari.Viewer
            The graphical viewer instance to be used for UI display and interaction.
        central_manager : CentralManagerType
            The central management instance for coordinating data and analysis flow.
        """
        super().__init__(viewer, central_manager)
        # Initialize a vertical layout to hold UI components for condensate analysis
        self.condensate_layout = QVBoxLayout()

    def setup_ui(self):
        """
        Sets up the specific UI components necessary for conducting condensate analysis.
        This includes initializing and arranging various analysis and processing steps
        in the user interface.
        """
        # Add analysis and processing steps to the layout
        # Each method call adds a specific UI component for condensate analysis
        self.central_manager.toolbox_functions_ui._add_measure_line(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_run_upscaling(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_pre_process(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_run_enhanced_rb_gaussian_bg_removal(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_run_cellpose_segmentation(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_run_train_and_apply_rf_classifier(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_run_cell_analysis_func(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_run_segment_subcellular_objects(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_run_puncta_analysis_func(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_save_and_clear(layout=self.condensate_layout)
        # ... Add other components in the order you want ...

        # Create a main widget and assign the vertical layout to it
        main_widget = QWidget()
        main_widget.setLayout(self.condensate_layout)

        # Create a scroll area to enable scrolling for the UI components
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)  # Make the scroll area resizable
        scroll_area.setWidget(main_widget)  # Set the main widget as the scroll area's content

        # Add the scroll area to the viewer as a dockable widget for condensate analysis
        self.viewer.window.add_dock_widget(scroll_area, name="Condensate Analysis Dock")

        # Set the size policy to make the widget and scroll area expand with the window
        main_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Align the layout to the top of the widget to ensure orderly arrangement
        self.condensate_layout.setAlignment(Qt.AlignTop)


class ObjectColocAnalysisUI(AnalysisMethodsUI):
    """
    A specialized user interface (UI) class for object-based colocalization analysis
    within a larger analytical framework. Inherits from AnalysisMethodsUI to leverage
    foundational functionalities while introducing specific components necessary for
    comprehensive object-based colocalization analysis.

    This class facilitates the assembly of UI components tailored to the analysis
    requirements of object colocalization, enabling researchers to perform detailed
    analyses with an emphasis on spatial relationships between different objects within
    an image.

    Attributes
    ----------
    viewer : napari.Viewer
        The graphical viewer instance utilized for displaying and interacting with
        the analysis tools and results.
    central_manager : CentralManager Class
        The central management instance that oversees the flow of data and analysis
        across various components, ensuring a cohesive operational experience.
    object_coloc_layout : QVBoxLayout
        A vertical layout manager to sequentially arrange UI components for object
        colocalization analysis, ensuring an organized presentation within the UI.

    Methods
    -------
    setup_ui():
        Initializes and organizes the specific UI components for object-based
        colocalization analysis, constructing an intuitive and efficient workspace
        for users to conduct their analysis.
    """

    def __init__(self, viewer, central_manager):
        """
        Initializes the ObjectColocAnalysisUI with essential components such as the viewer
        and central manager, and prepares the vertical layout for subsequent UI component
        additions.

        Parameters
        ----------
        viewer : napari.Viewer
            The graphical viewer used for visual interaction within the analysis UI.
        central_manager : CentralManagerType
            A central manager that facilitates coordination between different analysis
            and data management components.
        """
        super().__init__(viewer, central_manager)
        # Set up a QVBoxLayout to manage the arrangement of UI components
        self.object_coloc_layout = QVBoxLayout()

    def setup_ui(self):
        """
        Sets up the UI components specifically required for object-based colocalization
        analysis, detailing the process flow and enabling comprehensive analysis features
        through a structured UI layout.
        """
        # Sequentially add UI components for object colocalization analysis
        # Each method enriches the UI with functional capabilities tailored to the analysis needs
        self.central_manager.toolbox_functions_ui._add_measure_line(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_upscaling(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_pre_process(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_enhanced_rb_gaussian_bg_removal(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_cellpose_segmentation(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_train_and_apply_rf_classifier(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_cell_analysis_func(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_segment_subcellular_objects(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_puncta_analysis_func(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_obca(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_manders_coloc(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_save_and_clear(layout=self.object_coloc_layout)
        # ... Add other components in the order you want ...

        # Create the main widget to house all UI components
        main_widget = QWidget()
        main_widget.setLayout(self.object_coloc_layout)

        # Set up a scrollable area to accommodate varying numbers of UI components
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(main_widget)  # Assign the main widget as the scroll area's content

        # Integrate the scroll area into the viewer as a dockable widget
        self.viewer.window.add_dock_widget(scroll_area, name="Object Based Colocalization Analysis Dock")

        # Configure size policies to ensure UI components and scroll area expand appropriately
        main_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Align UI components to the top of the layout for a tidy presentation
        self.object_coloc_layout.setAlignment(Qt.AlignTop)


class PixelColocAnalysisUI(AnalysisMethodsUI):
    """
    A user interface (UI) class tailored for pixel-wise colocalization analysis. Inherits
    from AnalysisMethodsUI to provide a specialized framework that integrates pixel-based
    analysis tools into a cohesive graphical interface. This class focuses on facilitating
    the exploration of spatial correlations at the pixel level between different channels
    or markers within an image.

    Attributes
    ----------
    viewer : napari.Viewer
        The graphical viewer for displaying and interacting with images and analysis results.
    central_manager : CentralManagerType
        Manages the flow of data and analysis operations, ensuring seamless integration of
        various analysis components.
    pixel_coloc_layout : QVBoxLayout
        Organizes UI components vertically, tailored for pixel colocalization analysis workflows.

    Methods
    -------
    setup_ui():
        Sets up the UI for pixel-wise colocalization analysis, incorporating various image
        processing and analysis functions designed for detailed spatial correlation studies.
    """
    def __init__(self, viewer, central_manager):
        """
        Initializes the PixelColocAnalysisUI with essential components such as the viewer
        and central manager, and prepares the vertical layout for subsequent UI component
        additions.

        Parameters
        ----------
        viewer : napari.Viewer
            The graphical viewer used for visual interaction within the analysis UI.
        central_manager : CentralManagerType
            A central manager that facilitates coordination between different analysis
            and data management components.
        """
        super().__init__(viewer, central_manager)
        # Initialize a vertical layout to hold UI components for condensate analysis
        self.pixel_coloc_layout = QVBoxLayout()


    def setup_ui(self):
        """
        Sets up the UI components specifically required for pixel-wise correlation coefficient
        analysis, detailing the process flow and enabling comprehensive analysis features through 
        a structured UI layout.
        """
        # Setup the specific UI components for pixel wise correlation analysis
        self.central_manager.toolbox_functions_ui._add_measure_line(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_clahe(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_wbns(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_rb_gaussian_background_removal(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_apply_rescale_intensity(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_cellpose_segmentation(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_train_and_apply_rf_classifier(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_cell_analysis_func(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_pwcca(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_ccf_analysis(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_save_and_clear(layout=self.pixel_coloc_layout)
        # ... Add other components in the order you want ...

        # Create the main widget to house all UI components
        main_widget = QWidget()
        main_widget.setLayout(self.pixel_coloc_layout)

        # Set up a scrollable area to accommodate varying numbers of UI components
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(main_widget)  # Assign the main widget as the scroll area's content

        # Integrate the scroll area into the viewer as a dockable widget
        self.viewer.window.add_dock_widget(scroll_area, name="Pixel-Wise Corr-Coeff Analysis Dock")

        # Configure size policies to ensure UI components and scroll area expand appropriately
        main_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Align UI components to the top of the layout for a tidy presentation
        self.pixel_coloc_layout.setAlignment(Qt.AlignTop)



class GeneralAnalysisUI(AnalysisMethodsUI):
    """
    A user interface (UI) class designed for general analysis purposes within a broader
    analytical software framework. Inherits from AnalysisMethodsUI, providing a versatile
    and adaptable UI that supports a wide range of image processing and analysis operations.
    This class is ideal for users seeking a generalized analysis toolset that can be applied
    to various types of data.

    Attributes
    ----------
    viewer : napari.Viewer
        A graphical viewer for visual interaction with analysis tools and data.
    central_manager : CentralManagerType
        Coordinates the overall analysis workflow and data management across the application.
    general_layout : QVBoxLayout
        Manages the arrangement of UI components for a flexible and comprehensive general
        analysis workflow.

    Methods
    -------
    setup_ui():
        Initializes and arranges UI components for general analysis, offering a broad
        spectrum of image processing and analysis functionalities to suit diverse research needs.
    """
    def __init__(self, viewer, central_manager):
        """
        Initializes the GeneralAnalysisUI class with a viewer and central manager, setting up
        the initial layout for further UI component addition.

        Parameters
        ----------
        viewer : napari.Viewer
            The graphical viewer instance to be used for UI display and interaction.
        central_manager : CentralManagerType
            The central management instance for coordinating data and analysis state.
        """
        super().__init__(viewer, central_manager)
        # Initialize a vertical layout to hold UI components for general analysis
        self.general_layout = QVBoxLayout()


    def setup_ui(self):
        """
        Sets up the UI components specifically required for general analysis, detailing the
        process flow and enabling comprehensive analysis features through a structured UI layout.
        """
        # Setup the specific UI components for a general analysis
        self.central_manager.toolbox_functions_ui._add_measure_line(layout=self.general_layout)
        self.central_manager.toolbox_functions_ui._add_run_upscaling(layout=self.general_layout)
        self.central_manager.toolbox_functions_ui._add_pre_process(layout=self.general_layout)
        self.central_manager.toolbox_functions_ui._add_run_enhanced_rb_gaussian_bg_removal(layout=self.general_layout)
        self.central_manager.toolbox_functions_ui._add_run_train_and_apply_rf_classifier(layout=self.general_layout)
        self.central_manager.toolbox_functions_ui._add_run_local_thresholding(layout=self.general_layout)   
        self.central_manager.toolbox_functions_ui._add_run_label_binary_mask(layout=self.general_layout)  
        self.central_manager.toolbox_functions_ui._add_run_measure_region_props(layout=self.general_layout) 
        self.central_manager.toolbox_functions_ui._add_run_autocorrelation_analysis(layout=self.general_layout)  
        self.central_manager.toolbox_functions_ui._add_save_and_clear(layout=self.general_layout)
        # ... Add other components in the order you want ...

        # Create a main widget to contain everything
        main_widget = QWidget()
        main_widget.setLayout(self.general_layout)

        # Create a scroll area and set the main widget as its central widget
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(main_widget)

        # Add the scroll area to the viewer as a dock widget
        self.viewer.window.add_dock_widget(scroll_area, name="General Analysis Dock")

        # Configure size policies to ensure UI components and scroll area expand appropriately
        main_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Align UI components to the top of the layout for a tidy presentation
        self.general_layout.setAlignment(Qt.AlignTop)


class FibrilAnalysisUI(AnalysisMethodsUI):
    """
    Specializes in the analysis of fibrillar structures within images, extending the
    AnalysisMethodsUI class to provide specific functionalities for fibril identification,
    characterization, and quantification. This UI class is tailored for researchers focused
    on studying fibrous proteins, DNA, or other fibrillar components, offering dedicated
    tools for enhanced visualization and analysis of fibril morphology.

    Attributes
    ----------
    viewer : napari.Viewer
        Serves as the interface for visual data exploration and analysis interaction.
    central_manager : CentralManagerType
        Ensures integrated and efficient management of data and analysis workflows
        specific to fibril analysis.
    fibril_layout : QVBoxLayout
        Arranges UI components that facilitate fibril analysis operations, promoting
        an organized and intuitive user experience.

    Methods
    -------
    setup_ui():
        Constructs the UI for fibril analysis, incorporating specialized image processing
        and analysis techniques aimed at extracting and analyzing fibrillar features within
        complex biological or material science images.
    """
    def __init__(self, viewer, central_manager):
        """
        Initializes the FibrilAnalysisUI class with a viewer and central manager, setting up
        the initial layout for further UI component addition.

        Parameters
        ----------
        viewer : napari.Viewer
            The graphical viewer instance to be used for UI display and interaction.
        central_manager : CentralManagerType
            The central management instance for coordinating data and analysis state.
        """
        super().__init__(viewer, central_manager)
        # Initialize a vertical layout to hold UI components for fibril analysis
        self.fibril_layout = QVBoxLayout()


    def setup_ui(self):
        """
        Sets up the UI components specifically required for fibril analysis, detailing the
        process flow and enabling comprehensive analysis features through a structured UI layout.
        """
        # Setup the specific UI components for fibril analysis
        self.central_manager.toolbox_functions_ui._add_measure_line(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_upscaling(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_apply_bilateral_filter(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_pre_process(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_enhanced_rb_gaussian_bg_removal(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_peak_and_edge_enhancement(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_morphological_gaussian_filter(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_train_and_apply_rf_classifier(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_local_thresholding(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_measure_binary_mask(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_save_and_clear(layout=self.fibril_layout)
        # ... Add other components in the order you want ...

        # Create a main widget to contain everything
        main_widget = QWidget()
        main_widget.setLayout(self.fibril_layout)

        # Create a scroll area and set the main widget as its central widget
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(main_widget)

        # Add the scroll area to the viewer as a dock widget
        self.viewer.window.add_dock_widget(scroll_area, name="Fibril Analysis Dock")

        # Set the size policy of the main widget and scroll area
        main_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Align UI components to the top of the layout for a neat presentation
        self.fibril_layout.setAlignment(Qt.AlignTop)



class MenuManager:
    """
    Manages the setup and addition of menu items to a napari viewer instance. This class
    integrates a variety of analysis, file I/O, and toolbox functions into the viewer's
    menu bar, allowing for easy access to different functionalities within the application.

    Attributes
    ----------
    viewer : napari.Viewer
        The napari Viewer instance to which the menus will be added.
    central_manager : CentralManager
        An instance of a custom class managing central functionalities, including
        file I/O operations, analysis methods, and toolbox functions.

    Methods
    -------
    _setup_menu_bar():
        Sets up the main menu bar with specific menu items and their associated actions.
    make_lambda(action_method, kwargs):
        Creates a lambda function for triggering actions with arguments.
    _add_actions_to_menu(actions_dict, menu):
        Adds actions to a given menu based on a dictionary of action names and methods.
    _add_file_io_methods_to_menu():
        Adds file I/O methods as menu items under the file menu.
    _add_analysis_methods_to_menu():
        Adds analysis methods as menu items under the analysis methods menu.
    _add_toolbox_to_menu():
        Adds toolbox functions as menu items under the toolbox menu.
    """

    def __init__(self, viewer, central_manager):
        """
        Initializes the MenuManager with a viewer and a central_manager instance,
        and sets up the menu bar.

        Parameters
        ----------
        viewer : Viewer
            The napari Viewer instance to which the menus will be added.
        central_manager : CentralManager
            An instance managing central functionalities, like file I/O and analysis methods.
        """

        self.viewer = viewer
        self.central_manager = central_manager
        self._setup_menu_bar()

    def _setup_menu_bar(self):
        """
        Set up the main menu bar with specific menu items and their associated actions.
        This method initializes and configures menus for analysis methods, toolbox functions,
        and file I/O operations, populating them with the relevant actions.
        """

        # Setup and populate the "Analysis Methods" menu
        self.analysis_methods_menu = self.viewer.window._qt_window.menuBar().addMenu('Analysis Methods')
        self._add_analysis_methods_to_menu()

        # Setup and populate the "Toolbox" menu with various tools and utilities
        self.toolbox_menu = self.viewer.window._qt_window.menuBar().addMenu('Toolbox')
        self._add_toolbox_to_menu()

        # Setup and populate the "Open File(s)" menu with file I/O actions
        self.file_menu = self.viewer.window._qt_window.menuBar().addMenu('Open/Save File(s)')
        self._add_file_io_methods_to_menu()

    def make_lambda(self, action_method, kwargs):
        """
        Creates a lambda function for triggering actions with arguments. This allows
        for the dynamic execution of methods with specific parameters directly from
        menu action triggers.

        Parameters
        ----------
        action_method : callable
            The method to be executed when the action is triggered.
        kwargs : dict
            A dictionary of keyword arguments to be passed to the action method.

        Returns
        -------
        function
            A lambda function that calls `action_method` with `kwargs` when triggered.
        """
        return lambda: action_method(**kwargs)

    def _add_actions_to_menu(self, actions_dict, menu):
        """
        Add actions to a given menu based on the provided dictionary of action names
        and methods. This allows for a dynamic and flexible addition of actions to menus,
        facilitating customization and extension.

        Parameters
        ----------
        actions_dict : dict
            A dictionary where keys are action names (str) and values are tuples.
            Each tuple contains the method to connect to the action and an optional
            dictionary of arguments for that method.
        menu : QMenu
            The menu to which the actions will be added.
        """

        for action_name, (action_method, kwargs) in actions_dict.items():
            action = QAction(action_name, self.viewer.window._qt_window)
            if kwargs:
                # Connect the action to a lambda function for methods requiring arguments
                action.triggered.connect(self.make_lambda(action_method, kwargs))
            else:
                # Connect the action directly to the method if no arguments are needed
                action.triggered.connect(action_method)
            menu.addAction(action)

    # The following methods implement specific functionality additions to their respective menus.
    # These methods organize the addition of various analysis, file I/O, and toolbox
    # actions into structured menus and submenus, providing a user-friendly interface for
    # accessing different functionalities within the napari application.

    # Each method utilizes _add_actions_to_menu to dynamically add actions based on a predefined
    # dictionary of action names and associated methods. These dictionaries define the structure
    # and content of the menus, facilitating easy modifications and extensions to the menu system.
            
    # Add specific file I/O methods as actions to the file I/O menu.
    def _add_file_io_methods_to_menu(self):
            """
            Add specific file I/O methods as actions to the file I/O menu.
            """
            file_io_methods_dict = {
                'Open 2D Image(s)': (self.central_manager.file_io.open_2d_image, {}),
                'Open 2D Mask(s)': (self.central_manager.file_io.open_2d_mask, {}),
                'Save and Clear': (self.central_manager.file_io.save_and_clear_all, {'viewer': self.viewer})
            }
            self._add_actions_to_menu(file_io_methods_dict, self.file_menu)

    # Add specific analysis methods as actions to the analysis methods menu.
    def _add_analysis_methods_to_menu(self):
        """
        Add specific analysis methods as actions to the analysis methods menu. 
        """
        condensate_analysis_dict = {
            'Condensate Analysis': (self.central_manager.analysis_methods_ui._switch_to_condensate_analysis, {'base_data_repository': self.central_manager.active_data_class.data_repository})
        }
        self._add_actions_to_menu(condensate_analysis_dict, self.analysis_methods_menu)

        coloc_analysis_submenu = self.analysis_methods_menu.addMenu('Colocalization Analysis')
        coloc_analysis_actions = {
            'Object Based Colocalization Analysis': (self.central_manager.analysis_methods_ui._switch_to_object_coloc_analysis, {'base_data_repository': self.central_manager.active_data_class.data_repository}),
            'Pixel Based Correlation Analysis': (self.central_manager.analysis_methods_ui._switch_to_pixel_coloc_analysis, {'base_data_repository': self.central_manager.active_data_class.data_repository})
        }
        self._add_actions_to_menu(coloc_analysis_actions, coloc_analysis_submenu)

        analysis_methods_dict = {
            'General Analysis': (self.central_manager.analysis_methods_ui._switch_to_general_analysis, {'base_data_repository': self.central_manager.active_data_class.data_repository}),
            'Fibril Analysis': (self.central_manager.analysis_methods_ui._switch_to_fibril_analysis, {'base_data_repository': self.central_manager.active_data_class.data_repository})
        }
        self._add_actions_to_menu(analysis_methods_dict, self.analysis_methods_menu)

    # Add specific toolbox functions as actions to the toolbox menu.
    def _add_toolbox_to_menu(self):
        """
        Add indiviudal toolbox functions as actions to the toolbox functions menu. They are organized into sub-menus based on their functionality.
        """
        # Add functions to the main toolbox menu
        toolbox_actions = {
            'Measure Object Diameters': (self.central_manager.toolbox_functions_ui._add_measure_line, {'separate_widget': True})
        }
        self._add_actions_to_menu(toolbox_actions, self.toolbox_menu)

        # Create sub-menu for image processing functions
        image_processing_submenu = self.toolbox_menu.addMenu('Image Processing')
        image_processing_actions = {
            'Pre-Process Image': (self.central_manager.toolbox_functions_ui._add_pre_process, {'separate_widget': True})
        }
        self._add_actions_to_menu(image_processing_actions, image_processing_submenu)

        # Create sub-sub-menu for image adjustment functions
        image_adjustments_sub_submenu = image_processing_submenu.addMenu('Image Adjustments')
        image_adjustment_actions = {
            'Rescale Intensity': (self.central_manager.toolbox_functions_ui._add_run_apply_rescale_intensity, {'separate_widget': True}),
            'Invert Image': (self.central_manager.toolbox_functions_ui._add_run_invert_image, {'separate_widget': True}),
            'Upscale Image': (self.central_manager.toolbox_functions_ui._add_run_upscaling, {'separate_widget': True})
        }
        self._add_actions_to_menu(image_adjustment_actions, image_adjustments_sub_submenu)

        # Create sub-sub-menu for background and noise correction functions
        background_noise_correction_submenu = image_processing_submenu.addMenu('Background and Noise Correction')
        background_noise_correction_actions = {
            'Rolling-Ball Gaussian Background Removal': (self.central_manager.toolbox_functions_ui._add_run_rb_gaussian_background_removal, {'separate_widget': True}),
            'Background Removal w/ Edge Enhancement': (self.central_manager.toolbox_functions_ui._add_run_enhanced_rb_gaussian_bg_removal, {'separate_widget': True}),
            'Wavelet BG and Noise Subtraction': (self.central_manager.toolbox_functions_ui._add_run_wbns, {'separate_widget': True}),
            'Wavelet Noise Reduction': (self.central_manager.toolbox_functions_ui._add_run_wavelet_noise_subtraction, {'separate_widget': True}), 
            'Bilateral Noise Reduction': (self.central_manager.toolbox_functions_ui._add_run_apply_bilateral_filter, {'separate_widget': True}),
        }
        self._add_actions_to_menu(background_noise_correction_actions, background_noise_correction_submenu)

        # Create sub-sub-menu for image enhancement and filter functions
        enhancements_and_filters_submenu = image_processing_submenu.addMenu('Enhancements and Filters')
        enhancements_and_filters_actions = {
            'CLAHE': (self.central_manager.toolbox_functions_ui._add_run_clahe, {'separate_widget': True}),
            'Peak and Edge Enhancement': (self.central_manager.toolbox_functions_ui._add_run_peak_and_edge_enhancement, {'separate_widget': True}),
            'Morphological Gaussian Filter': (self.central_manager.toolbox_functions_ui._add_run_morphological_gaussian_filter, {'separate_widget': True}),
            'LoG Filter': (self.central_manager.toolbox_functions_ui._add_run_apply_laplace_of_gauss_filter, {'separate_widget': True}),            
            'Deblur by Pixel Reassignment': (self.central_manager.toolbox_functions_ui._add_run_dpr, {'separate_widget': True})
        }
        self._add_actions_to_menu(enhancements_and_filters_actions, enhancements_and_filters_submenu)

        # Create a sub-menu for segmentation functions
        image_segmentation_submenu = self.toolbox_menu.addMenu('Image Segmentation')
        image_segmentation_actions = {
            'Local Thresholding': (self.central_manager.toolbox_functions_ui._add_run_local_thresholding, {'separate_widget': True}),
            'Cellpose Segmentation': (self.central_manager.toolbox_functions_ui._add_run_cellpose_segmentation, {'separate_widget': True}),
            'Random Forest Classifier': (self.central_manager.toolbox_functions_ui._add_run_train_and_apply_rf_classifier, {'separate_widget': True}),
            'Felzenszwalb Segmentation and Region Merging': (self.central_manager.toolbox_functions_ui._add_run_fz_segmentation_and_merging, {'separate_widget': True})
        }
        self._add_actions_to_menu(image_segmentation_actions, image_segmentation_submenu)

        # Create a sub-menu for Label and Mask Tools
        label_and_mask_tools_submenu = self.toolbox_menu.addMenu('Label and Mask Tools')

        # Create a sub-sub-menu for binary mask tools
        mask_tools_sub_submenu = label_and_mask_tools_submenu.addMenu('Binary Mask Tools')
        mask_tools_actions = {
            'Binary Morphological Operations': (self.central_manager.toolbox_functions_ui._add_run_binary_morph_operation, {'separate_widget': True}),
            'Measure Binary Mask': (self.central_manager.toolbox_functions_ui._add_run_measure_binary_mask, {'separate_widget': True}),
            'Label Binary Mask': (self.central_manager.toolbox_functions_ui._add_run_label_binary_mask, {'separate_widget': True})
        }
        self._add_actions_to_menu(mask_tools_actions, mask_tools_sub_submenu)
        
        # Create a sub-sub-menu for labeled mask tools
        label_tools_sub_submenu = label_and_mask_tools_submenu.addMenu('Labeled Mask Tools')   
        label_tools_actions = {
            'Label Updater': (self.central_manager.toolbox_functions_ui._add_run_update_labels, {'separate_widget': True}),
            'Convert Labels to Mask': (self.central_manager.toolbox_functions_ui._add_run_convert_labels_to_mask, {'separate_widget': True}),
            'Measure Region Properties': (self.central_manager.toolbox_functions_ui._add_run_measure_region_props, {'separate_widget': True})
        }
        self._add_actions_to_menu(label_tools_actions, label_tools_sub_submenu)

        # Create a sub-menu for layer operations    
        layer_operations_submenu = self.toolbox_menu.addMenu('Layer Operations')
        layer_operations_actions = {
            'Simple Multi-Layer Merge': (self.central_manager.toolbox_functions_ui._add_run_simple_multi_merge, {'separate_widget': True}),
            'Advanced 2-Layer Merge': (self.central_manager.toolbox_functions_ui._add_run_advanced_two_layer_merge, {'separate_widget': True})
        }
        self._add_actions_to_menu(layer_operations_actions, layer_operations_submenu)

        # Create a sub-menu for colocalization tools
        colocalization_tools_submenu = self.toolbox_menu.addMenu('Colocalization/Correlation')
        autocorrelation_actions = {
            'Auto-Correlation Function Analysis': (self.central_manager.toolbox_functions_ui._add_run_autocorrelation_analysis, {'separate_widget': True})
        }
        self._add_actions_to_menu(autocorrelation_actions, colocalization_tools_submenu)

        # Create a sub-sub-menu for pixel wise correlation analysis tools
        pixel_coloc_tools_sub_submenu = colocalization_tools_submenu.addMenu('Pixel-Wise Correlation Analysis')
        pixel_coloc_tools_actions = {
            'Pixel-Wise Correlation Coefficient Analysis': (self.central_manager.toolbox_functions_ui._add_run_pwcca, {'separate_widget': True}),
            'Cross-Correlation Function Analysis': (self.central_manager.toolbox_functions_ui._add_run_ccf_analysis, {'separate_widget': True})
        }
        self._add_actions_to_menu(pixel_coloc_tools_actions, pixel_coloc_tools_sub_submenu)

        # Create a sub-sub-menu for object based colocalization analysis tools
        obj_coloc_tools_sub_submenu = colocalization_tools_submenu.addMenu('Object-Based Colocalization Analysis')
        obj_coloc_tools_actions = {
            'Object Based Colocalization Analysis': (self.central_manager.toolbox_functions_ui._add_run_obca, {'separate_widget': True}),
            'Manders Colocalization Coefficient': (self.central_manager.toolbox_functions_ui._add_run_manders_coloc, {'separate_widget': True})
        }
        self._add_actions_to_menu(obj_coloc_tools_actions, obj_coloc_tools_sub_submenu)

        # Create a sub-menu for data visulaization tools
        data_visualization_submenu = self.toolbox_menu.addMenu('Data Visualization')
        data_visualization_actions = {
            'Plotting Widget': (self.central_manager.toolbox_functions_ui._add_plotting_widget, {'separate_widget': True})
        }
        self._add_actions_to_menu(data_visualization_actions, data_visualization_submenu)
