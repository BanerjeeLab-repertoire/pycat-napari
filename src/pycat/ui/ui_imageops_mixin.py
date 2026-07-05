"""
Basic image-operation widgets mixin for ToolboxFunctionsUI.

Holds the pure image-transform widget builders (rescale intensity, invert,
upscaling, rolling-ball + Gaussian background removal) — the self-contained
"take an image, apply an operation" tools, grouped with the other image
processing/filtering widgets rather than the __init__-coupled base I/O (open,
save, measure line, pre-process, calibration), which stay in the main class.
Split out of ui_modules.ToolboxFunctionsUI; methods moved verbatim and inherited
via the mixin, so behaviour is unchanged.
"""

import napari
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QPushButton, QLabel, QVBoxLayout, QHBoxLayout, QLineEdit, QWidget,
    QComboBox, QSlider, QScrollArea, QSizePolicy, QCheckBox)

from pycat.toolbox.image_processing_tools import (
    run_apply_rescale_intensity, run_invert_image, run_upscaling_func,
    run_rb_gaussian_background_removal)


class _ImageOpsWidgetsMixin:
    """Basic image-transform widget builders for ToolboxFunctionsUI (mixin)."""

    def _add_run_apply_rescale_intensity(self, layout=None, separate_widget=False):
        """Add a widget for rescaling image intensity values, optionally in a separate dock."""
        rescale_intensity_layout = QVBoxLayout()
        self.add_text_label(rescale_intensity_layout, 'Rescale Intensity', bold=True) # Add widget title label
        self.add_text_label(rescale_intensity_layout, 'Output Min') # Add a text label
        out_min_input = QLineEdit() # Create a text input
        out_min_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        rescale_intensity_layout.addWidget(out_min_input) # Add the text input to the layout
        self.add_text_label(rescale_intensity_layout, 'Output Max') # Add a text label
        out_max_input = QLineEdit() # Create a text input
        out_max_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        rescale_intensity_layout.addWidget(out_max_input) # Add the text input to the layout
        rescale_intensity_button = QPushButton("Rescale Intensity") # Create a button widget
        rescale_intensity_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        def _on_upscaling():
            # run_upscaling_func operates on viewer.layers.selection (the
            # highlighted set), not just the single active layer — record
            # all selected layer names so replay knows what was upscaled.
            selected_names = [l.name for l in self.viewer.layers.selection
                              if l is not None]
            self.on_general_button_clicked(
                run_upscaling_func, None, upscaling_checkbox,
                self.central_manager.active_data_class, self.viewer)
            self._record('upscaling', {
                'update_data_class': upscaling_checkbox.isChecked(),
                'selected_layers': selected_names,
            })
        upscaling_button.clicked.connect(_on_upscaling)
        try:
            from pycat.ui.field_status import button_with_circle
            upscaling_layout.addWidget(button_with_circle(upscaling_button, optional=True))  # yellow
        except Exception:
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
        remove_background_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        remove_background_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_rb_gaussian_background_removal, None, eq_int_checkbox, self.central_manager.active_data_class, self.viewer))
        remove_background_layout.addWidget(remove_background_button) # Add the button to the layout
        remove_background_widget = QWidget()
        remove_background_widget.setLayout(remove_background_layout)
        self._add_widget_to_layout_or_dock(remove_background_widget, layout, separate_widget, "Background Removal Dock")

