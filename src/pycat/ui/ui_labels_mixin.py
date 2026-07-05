"""
Labels / masks / merge widgets mixin for ToolboxFunctionsUI.

Holds the label- and mask-tool widget builders (convert labels↔mask, measure
region properties, update labels, label/measure binary mask, binary morphology,
simple multi-merge, advanced two-layer merge). Split out of
ui_modules.ToolboxFunctionsUI to keep that file navigable; methods are moved
verbatim and inherited via the mixin, so behaviour is unchanged. They rely on
attributes/methods from BaseUIClass/ToolboxFunctionsUI at runtime (self.viewer,
self.central_manager, self.on_general_button_clicked,
self._add_widget_to_layout_or_dock, etc.). `guard_wheel` (defined in ui_modules)
is imported deferred inside the one method that uses it, to avoid an import cycle.
"""

import math

import napari
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QPushButton, QLabel, QVBoxLayout, QHBoxLayout, QLineEdit, QWidget,
    QComboBox, QSlider, QScrollArea, QSizePolicy, QRadioButton)

from pycat.toolbox.label_and_mask_tools import (
    run_convert_labels_to_mask, run_measure_region_props, run_update_labels,
    run_label_binary_mask, run_measure_binary_mask, run_binary_morph_operation)
from pycat.toolbox.layer_tools import (
    run_simple_multi_merge, run_advanced_two_layer_merge)


class _LabelsMasksWidgetsMixin:
    """Label / mask / merge widget builders for ToolboxFunctionsUI (mixin)."""

    def _add_run_convert_labels_to_mask(self, layout=None, separate_widget=False):
        """Add a widget for converting labels to binary masks, optionally in a separate dock."""
        convert_labels_layout = QVBoxLayout()
        self.add_text_label(convert_labels_layout, 'Convert Labels to Binary Mask', bold=True) # Add widget title label
        self.add_text_label(convert_labels_layout, 'Select Labels Layer to Convert') # Add a text label
        convert_labels_dropdown = self.create_layer_dropdown(napari.layers.Labels) # Create a dropdown widget
        convert_labels_layout.addWidget(convert_labels_dropdown) # Add the dropdown to the layout
        convert_labels_button = QPushButton("Convert Labels to Mask") # Create a button widget
        convert_labels_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        def _on_rp():
            self.on_general_button_clicked(
                run_measure_region_props, self.viewer, rp_dropdown_layers,
                rp_dropdown_image, self.central_manager.active_data_class)
            self._record('measure_region_props', {
                'mask_layer': rp_dropdown_layers.currentText(),
                'image_layer': rp_dropdown_image.currentText(),
            })
        rp_button = QPushButton("Measure Region Properties")
        rp_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        rp_button.clicked.connect(_on_rp)
        rp_layout.addWidget(rp_button)
        rp_widget = QWidget() 
        rp_widget.setLayout(rp_layout)
        self._add_widget_to_layout_or_dock(rp_widget, layout, separate_widget, "Region Properties Dock")


    def _add_run_update_labels(self, layout=None, separate_widget=False):
        """Add a widget for updating label values, optionally in a separate dock."""
        label_layout = QVBoxLayout()
        self.add_text_label(label_layout, 'Change Label Values', bold=True) # Add widget title label
        self.add_text_label(label_layout, 'New Label or Increment Amount') # Add a text label
        new_label_input = QLineEdit() # Add a text input for new label value
        new_label_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        def _on_label_mask():
            self.on_general_button_clicked(
                run_label_binary_mask, self.viewer, label_mask_dropdown, self.viewer)
            self._record('label_binary_mask', {
                'mask_layer': label_mask_dropdown.currentText(),
            })
        label_mask_button = QPushButton("Label Binary Mask")
        label_mask_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        label_mask_button.clicked.connect(_on_label_mask)
        label_mask_layout.addWidget(label_mask_button)
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
        mbm_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        bmo_iter_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        bmo_layout.addWidget(bmo_iter_input)
        self.add_text_label(bmo_layout, 'Structuring Element Size')
        bmo_elem_size_input = QLineEdit()
        bmo_elem_size_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        bmo_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        simple_merge_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        slider_label.setWordWrap(True)

        slider_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        alpha_blend_slider = QSlider(Qt.Horizontal) # Create a slider widget
        from pycat.ui.ui_modules import guard_wheel  # deferred (avoids import cycle)
        guard_wheel(alpha_blend_slider)
        alpha_blend_slider.setRange(0, 10)  # 100 steps from 0 to 100
        alpha_blend_slider.setValue(5)  # default is 0.5
        alpha_blend_slider.setSingleStep(1)  # Adjust for 0.01 steps
        slider_label_value = QLabel("0.5") 
        slider_label_value.setWordWrap(True)
 
        slider_label_value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
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
        

