"""
Analysis widgets mixin for ToolboxFunctionsUI.

Holds the feature/correlation/colocalization analysis widget builders (cell
analysis, puncta analysis, spatial autocorrelation, cross-correlation function,
pixel-wise correlation, object-based colocalization, Manders coefficient). Split
out of ui_modules.ToolboxFunctionsUI to keep that file navigable; methods are
moved verbatim and inherited via the mixin, so behaviour is unchanged. They rely
on attributes/methods from BaseUIClass/ToolboxFunctionsUI at runtime (self.viewer,
self.central_manager, self.on_general_button_clicked,
self._add_widget_to_layout_or_dock, etc.).
"""

import math

import napari
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QPushButton, QLabel, QVBoxLayout, QHBoxLayout, QLineEdit, QWidget,
    QComboBox, QSlider, QScrollArea, QSizePolicy)

from pycat.toolbox.feature_analysis_tools import (
    run_cell_analysis_func, run_puncta_analysis_func)
from pycat.toolbox.correlation_func_analysis_tools import (
    run_ccf_analysis, run_autocorrelation_analysis)
from pycat.toolbox.pixel_wise_corr_analysis_tools import run_pwcca
from pycat.toolbox.obj_based_coloc_analysis_tools import run_manders_coloc, run_obca


class _AnalysisWidgetsMixin:
    """Feature/correlation/coloc analysis widget builders for ToolboxFunctionsUI (mixin)."""

    def _add_run_cell_analysis_func(self, layout=None, separate_widget=False):
        """Add a widget for cell analysis, optionally in a separate dock."""
        cell_segmentation_layout = QVBoxLayout()
        self.add_text_label(cell_segmentation_layout, 'Cell/Nuclei Analysis', bold=True) # Add widget title label
        cell_segmentation_dropdown_labels = self._layer_row(cell_segmentation_layout, 'Select Mask Layer for Cell Analysis:', napari.layers.Labels, name_hint='Labeled Cell Mask')
        cell_segmentation_dropdown_omit = self._layer_row(cell_segmentation_layout, 'Select Mask Layer to Omit:', napari.layers.Labels, optional=True)
        cell_segmentation_dropdown_omit.insertItem(0, "None")
        cell_segmentation_dropdown_images = self._layer_row(cell_segmentation_layout, 'Select Image for Cell Analysis:', napari.layers.Image, name_hint='Upscaled Fluorescence')
        cell_analysis_button = QPushButton("Run Cell Analyzer") # Create a button widget
        cell_analysis_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        def _on_cell_analysis():
            self.on_general_button_clicked(
                run_cell_analysis_func, self.viewer,
                cell_segmentation_dropdown_labels, cell_segmentation_dropdown_omit,
                cell_segmentation_dropdown_images,
                self.central_manager.active_data_class, self.viewer)
            self._record('cell_analysis', {
                'labels_layer': cell_segmentation_dropdown_labels.currentText(),
                'omit_layer': cell_segmentation_dropdown_omit.currentText(),
                'image_layer': cell_segmentation_dropdown_images.currentText(),
            })
        cell_analysis_button.clicked.connect(_on_cell_analysis)
        try:
            from pycat.ui.field_status import button_with_circle
            cell_segmentation_layout.addWidget(button_with_circle(
                cell_analysis_button,
                watch_dropdowns=[cell_segmentation_dropdown_labels,
                                 cell_segmentation_dropdown_images]))
        except Exception:
            cell_segmentation_layout.addWidget(cell_analysis_button) # Add the button to the layout
        cell_segmentation_widget = QWidget()
        cell_segmentation_widget.setLayout(cell_segmentation_layout)
        self._add_widget_to_layout_or_dock(cell_segmentation_widget, layout, separate_widget, "Cell Analysis Dock")


    def _add_run_puncta_analysis_func(self, layout=None, separate_widget=False):
        """Add a widget for puncta analysis, optionally in a separate dock."""
        measure_puncta_layout = QVBoxLayout()
        self.add_text_label(measure_puncta_layout, 'Condensate Analysis', bold=True) # Add widget title label
        # Required dropdowns get a red status square (via _layer_row) that turns
        # green once a real layer is selected.
        puncta_measure_dropdown_labels = self._layer_row(
            measure_puncta_layout, 'Select Puncta Mask for Measurement:',
            napari.layers.Labels, name_hint='Refined Puncta')
        puncta_measure_dropdown_images = self._layer_row(
            measure_puncta_layout, 'Select Image for Puncta Measurement:',
            napari.layers.Image, name_hint='Upscaled Fluorescence')
        puncta_measure_button = QPushButton("Run Condensate Analyzer") # Create a button widget
        puncta_measure_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        def _on_puncta_analysis():
            self.on_general_button_clicked(
                run_puncta_analysis_func, self.viewer,
                puncta_measure_dropdown_labels, puncta_measure_dropdown_images,
                self.central_manager.active_data_class, self.viewer)
            self._record('condensate_analysis', {
                'labels_layer': puncta_measure_dropdown_labels.currentText(),
                'image_layer': puncta_measure_dropdown_images.currentText(),
            })
        puncta_measure_button.clicked.connect(_on_puncta_analysis)
        try:
            from pycat.ui.field_status import button_with_circle
            measure_puncta_layout.addWidget(button_with_circle(
                puncta_measure_button,
                watch_dropdowns=[puncta_measure_dropdown_labels,
                                 puncta_measure_dropdown_images]))
        except Exception:
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
        lower_limit_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        range_layout.addWidget(lower_limit_input)  # Add the lower limit input to the layout
        self.add_text_label(range_layout, 'to')  # Add a text label
        upper_limit_input = QLineEdit()  # Create QLineEdit for the upper limit
        upper_limit_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        range_layout.addWidget(upper_limit_input)  # Add the upper limit input to the layout
        ACF_layout.addLayout(range_layout)  # Add the range inputs layout to the main vertical layout

        def _on_acf():
            self.on_general_button_clicked(
                run_autocorrelation_analysis, self.viewer, ACF_image_dropdown,
                ACF_roi_dropdown, lower_limit_input, upper_limit_input,
                self.central_manager.active_data_class)
            self._record('sacf_analysis', {
                'image_layer': ACF_image_dropdown.currentText(),
                'roi_layer': ACF_roi_dropdown.currentText(),
                'lower_limit': lower_limit_input.text(),
                'upper_limit': upper_limit_input.text(),
            })
        ACF_button = QPushButton("Calculate ACF")
        ACF_button.clicked.connect(_on_acf)
        ACF_layout.addWidget(ACF_button)
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
        manders_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        manders_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_manders_coloc, self.viewer, manders_image1_dropdown, manders_image2_dropdown, manders_roi_dropdown, self.central_manager.active_data_class))
        manders_layout.addWidget(manders_button) # Add the button to the layout
        manders_widget = QWidget()
        manders_widget.setLayout(manders_layout)
        self._add_widget_to_layout_or_dock(manders_widget, layout, separate_widget, "Manders Coefficient Dock")
     
