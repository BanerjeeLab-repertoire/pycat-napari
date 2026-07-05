"""
Segmentation widgets mixin for ToolboxFunctionsUI.

Holds the segmentation widget builders (Felzenszwalb segmentation + merging,
Cellpose, StarDist, random-forest classifier, local thresholding, and subcellular
condensate segmentation) plus the `_run_stardist_segmentation` helper. Split out
of ui_modules.ToolboxFunctionsUI to keep that file navigable; methods are moved
verbatim and inherited via the mixin, so behaviour is unchanged. They rely on
attributes/methods from BaseUIClass/ToolboxFunctionsUI at runtime (self.viewer,
self.central_manager, self.on_general_button_clicked,
self._add_widget_to_layout_or_dock, etc.).
"""

import math

import napari
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QPushButton, QLabel, QVBoxLayout, QHBoxLayout, QLineEdit, QWidget,
    QComboBox, QSlider, QScrollArea, QSizePolicy)

from pycat.toolbox.segmentation_tools import (
    run_fz_segmentation_and_merging, run_cellpose_segmentation,
    run_train_and_apply_rf_classifier, run_local_thresholding,
    run_segment_subcellular_objects)


class _SegmentationWidgetsMixin:
    """Segmentation widget builders for ToolboxFunctionsUI (mixin)."""

    def _add_run_fz_segmentation_and_merging(self, layout=None, separate_widget=False):
        """Add a widget for Felsenszwalb segmentation and region merging, optionally in a separate dock."""
        fz_layout = QVBoxLayout()
        self.add_text_label(fz_layout, 'Felsenszwalb Segmentation and Merging', bold=True) # Add a widget title label
        self.add_text_label(fz_layout, 'Scale') # Add a text label
        fz_scale_input = QLineEdit() # Create a text input
        fz_scale_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        fz_layout.addWidget(fz_scale_input) # Add the text input to the layout
        self.add_text_label(fz_layout, 'Sigma') # Add a text label
        fz_sigma_input = QLineEdit() # Create a text input
        fz_sigma_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        fz_layout.addWidget(fz_sigma_input) # Add the text input to the layout
        self.add_text_label(fz_layout, 'Min Size') # Add a text label
        fz_min_size_input = QLineEdit() # Create a text input
        fz_min_size_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        fz_layout.addWidget(fz_min_size_input) # Add the text input to the layout
        fz_button = QPushButton("Run Felsenszwalb Segmentation") # Create a button widget
        fz_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        fz_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_fz_segmentation_and_merging, None, fz_scale_input, fz_sigma_input, fz_min_size_input, self.viewer))
        fz_layout.addWidget(fz_button) # Add the button to the layout
        fz_widget = QWidget()
        fz_widget.setLayout(fz_layout)
        self._add_widget_to_layout_or_dock(fz_widget, layout, separate_widget, "FZ Segmentation Dock")


    def _add_run_cellpose_segmentation(self, layout=None, separate_widget=False):
        """
        Unified cell segmentation widget with a method selector.
        Defaults to Cellpose; offers StarDist and Random Forest as alternatives.
        """
        from PyQt5.QtWidgets import QButtonGroup, QRadioButton, QStackedWidget, QGroupBox

        seg_layout = QVBoxLayout()
        self.add_text_label(seg_layout, 'Cell Segmentation', bold=True)

        # ── Method selector (radio buttons) ─────────────────────────────
        method_group = QGroupBox("Segmentation method")
        method_row   = QVBoxLayout(method_group)
        method_row.setContentsMargins(9, 20, 9, 6)
        rb_cellpose  = QRadioButton("Cellpose  (deep learning, recommended)")
        rb_cellpose.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        rb_stardist  = QRadioButton("StarDist  (star-convex, nucleus-optimised)")
        rb_stardist.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        rb_rf        = QRadioButton("Random Forest  (pixel classifier, manual annotation)")
        rb_rf.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        rb_cellpose.setChecked(True)
        for rb in (rb_cellpose, rb_stardist, rb_rf):
            method_row.addWidget(rb)
        seg_layout.addWidget(method_group)

        # ── Cellpose model selector (version-aware) ──────────────────────
        from pycat.toolbox.segmentation_tools import (
            available_cellpose_models, default_cellpose_model)
        cp_model_group = QWidget()
        cp_model_row = QVBoxLayout(cp_model_group)
        cp_model_row.setContentsMargins(2, 0, 0, 0)
        self.add_text_label(cp_model_row, 'Cellpose model:')
        cp_model_dropdown = QComboBox()
        try:
            _models = available_cellpose_models()
        except Exception:
            _models = ['cyto2']
        cp_model_dropdown.addItems(_models)
        _default = default_cellpose_model() if _models else 'cyto2'
        _idx = cp_model_dropdown.findText(_default)
        if _idx >= 0:
            cp_model_dropdown.setCurrentIndex(_idx)
        cp_model_dropdown.setToolTip(
            "cyto2 = fast Cellpose <4 CNN (default). cpsam = Cellpose-SAM "
            "(Cellpose >=4 only; much slower on CPU). Only models supported by "
            "your installed Cellpose version are shown.")
        cp_model_row.addWidget(cp_model_dropdown)
        seg_layout.addWidget(cp_model_group)

        # ── Shared image dropdown ────────────────────────────────────────
        image_dropdown = self._layer_row(seg_layout, 'Select image layer:', napari.layers.Image, name_hint='Upscaled Segmentation')

        # ── RF-only extras (annotation layer) — shown/hidden by selection ──
        rf_extra = QWidget()
        rf_row   = QVBoxLayout(rf_extra)
        rf_row.setContentsMargins(2, 0, 0, 0)
        self.add_text_label(rf_row, 'Select annotation layer (for RF training):')
        rf_labels_dropdown = self.create_layer_dropdown(napari.layers.Labels)
        rf_row.addWidget(rf_labels_dropdown)
        rf_extra.setVisible(False)
        seg_layout.addWidget(rf_extra)

        def _on_method_changed():
            rf_extra.setVisible(rb_rf.isChecked())
            cp_model_group.setVisible(rb_cellpose.isChecked())

        rb_cellpose.toggled.connect(_on_method_changed)
        rb_stardist.toggled.connect(_on_method_changed)
        rb_rf.toggled.connect(_on_method_changed)
        _on_method_changed()

        # ── Run button ───────────────────────────────────────────────────
        run_btn = QPushButton("Run Segmentation")

        def _on_run():
            layer_name = image_dropdown.currentText()
            dr = self.central_manager.active_data_class.data_repository

            if rb_cellpose.isChecked():
                dr['cellpose_model'] = cp_model_dropdown.currentText()
                self.on_general_button_clicked(
                    run_cellpose_segmentation, self.viewer, image_dropdown,
                    self.central_manager.active_data_class, self.viewer)
                self._record('cellpose_segmentation', {
                    'method': 'cellpose',
                    'cellpose_model': cp_model_dropdown.currentText(),
                    'image_layer': layer_name,
                    'cell_diameter': dr.get('cell_diameter', 100),
                    'ball_radius':   dr.get('ball_radius', 50),
                })

            elif rb_stardist.isChecked():
                self._run_stardist_segmentation(layer_name)
                self._record('cellpose_segmentation', {
                    'method': 'stardist',
                    'image_layer': layer_name,
                    'cell_diameter': dr.get('cell_diameter', 100),
                })

            else:  # Random Forest
                self.on_general_button_clicked(
                    run_train_and_apply_rf_classifier, self.viewer,
                    image_dropdown, rf_labels_dropdown,
                    self.central_manager.active_data_class, self.viewer)
                self._record('cellpose_segmentation', {
                    'method': 'random_forest',
                    'image_layer': layer_name,
                    'annotation_layer': rf_labels_dropdown.currentText(),
                })

        run_btn.clicked.connect(_on_run)
        try:
            from pycat.ui.field_status import button_with_circle
            seg_layout.addWidget(button_with_circle(run_btn, watch_dropdowns=[image_dropdown]))
        except Exception:
            seg_layout.addWidget(run_btn)

        seg_widget = QWidget()
        seg_widget.setLayout(seg_layout)
        self._add_widget_to_layout_or_dock(seg_widget, layout, separate_widget,
                                            "Cell Segmentation")

    def _run_stardist_segmentation(self, layer_name: str):
        """Run StarDist 2D segmentation on the named layer."""
        try:
            from stardist.models import StarDist2D
            from csbdeep.utils import normalize as csbdeep_normalize
        except ImportError:
            from napari.utils.notifications import show_warning as w
            w("StarDist not installed. Run: pip install stardist")
            return

        import numpy as np
        try:
            image = self.viewer.layers[layer_name].data
        except KeyError:
            from napari.utils.notifications import show_warning as w
            w(f"Layer '{layer_name}' not found.")
            return

        from napari.utils.notifications import show_info as napari_show_info
        napari_show_info("Running StarDist — please wait…")

        img = np.asarray(image).astype(np.float32)
        img = csbdeep_normalize(img, 1, 99.8)

        model = StarDist2D.from_pretrained('2D_versatile_fluo')
        labels, _ = model.predict_instances(img)
        labels = labels.astype(np.uint16)

        dr = self.central_manager.active_data_class.data_repository
        cell_diameter = float(dr.get('cell_diameter', 100))
        layer_out = f"StarDist Segmentation on {layer_name}"
        self.viewer.add_labels(labels, name=layer_out)

        # Also create Labeled Cell Mask for downstream compatibility
        self.viewer.add_labels(labels.copy(), name="Labeled Cell Mask")

        dr['stardist_labels'] = labels
        napari_show_info(
            f"StarDist complete: {labels.max()} cells detected → '{layer_out}'")

    def _add_run_train_and_apply_rf_classifier(self, layout=None, separate_widget=False):
        """Kept for backward compatibility — RF is now in the unified segmentation widget."""
        self._add_run_cellpose_segmentation(layout=layout,
                                            separate_widget=separate_widget)


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
        k_label.setWordWrap(True)

        k_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        k_slider = QSlider(Qt.Horizontal) # Create a slider widget
        from pycat.ui.ui_modules import guard_wheel  # deferred (avoids import cycle)
        guard_wheel(k_slider)
        k_slider.setRange(0, 100)  # 100 steps from 0 to 100
        k_slider.setValue(50)  # default is 0
        k_slider.setSingleStep(1)  # Adjust for 0.01 steps
        k_label_value = QLabel("0.0") 
        k_label_value.setWordWrap(True)
 
        k_label_value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
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
        window_label.setWordWrap(True)

        window_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        window_slider = QSlider(Qt.Horizontal) # Create a slider widget
        from pycat.ui.ui_modules import guard_wheel  # deferred (avoids import cycle)
        guard_wheel(window_slider)
        window_slider.setRange(10, 250) # 100 steps from 10 to 250
        window_slider.setValue(def_window_size) # Set the default value
        window_label_value = QLabel(str(def_window_size)) # Set the default value
        window_label_value.setWordWrap(True)

        window_label_value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        window_slider.valueChanged.connect(lambda val: window_label_value.setText(str(val))) # Connect the slider to the update function
        local_thresh_layout.addWidget(window_label) # Add the text label to the layout
        local_thresh_layout.addWidget(window_slider) # Add the slider to the layout
        local_thresh_layout.addWidget(window_label_value) # Add the slider value to the layout

        # Button to apply thresholding
        def _on_local_thresh():
            self.on_general_button_clicked(
                run_local_thresholding, None, k_slider, window_slider,
                local_thresh_mode_dropdown.currentText(), self.viewer)
            self._record('local_thresholding', {
                'method': local_thresh_mode_dropdown.currentText(),
                'k_value': round((k_slider.value() / 50.0) - 1, 2),
                'window_size': window_slider.value(),
            })
        local_thresh_button = QPushButton("Apply Thresholding")
        local_thresh_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        local_thresh_button.clicked.connect(_on_local_thresh)
        local_thresh_layout.addWidget(local_thresh_button)
        sauvola_widget = QWidget()
        sauvola_widget.setLayout(local_thresh_layout)
        self._add_widget_to_layout_or_dock(sauvola_widget, layout, separate_widget, "Local Thresholding Dock")


    def _add_run_segment_subcellular_objects(self, layout=None, separate_widget=False):
        """Add a widget for subcellular object segmentation, optionally in a separate dock."""
        from PyQt5.QtWidgets import QDoubleSpinBox, QSpinBox, QFormLayout, QGroupBox
        process_cells_layout = QVBoxLayout()
        self.add_text_label(process_cells_layout, 'Subcellular Object Segmentation', bold=True)
        process_cells_image1_dropdown = self._layer_row(process_cells_layout, 'Select Pre-Processed Image to Segment:', napari.layers.Image, name_hint='Enhanced Background Removed')
        process_cells_image2_dropdown = self._layer_row(process_cells_layout, 'Select Fluorescence Image to Process:', napari.layers.Image, name_hint='Upscaled Fluorescence')

        # ── Refinement parameters ──────────────────────────────────────────
        params_group = QGroupBox("Refinement Parameters")
        params_layout = QFormLayout()
        params_layout.setContentsMargins(9, 20, 9, 6)
        params_group.setLayout(params_layout)

        def _make_spinbox(min_val, max_val, default, step, decimals=2):
            sb = QDoubleSpinBox()
            sb.setRange(min_val, max_val)
            sb.setValue(default)
            sb.setSingleStep(step)
            sb.setDecimals(decimals)
            return sb

        # Min spot radius — minimum puncta size in pixels
        min_spot_spin = QDoubleSpinBox()
        min_spot_spin.setRange(1, 20)
        min_spot_spin.setValue(2)
        min_spot_spin.setSingleStep(0.5)
        min_spot_spin.setDecimals(1)
        min_spot_spin.setToolTip("Minimum puncta radius in pixels. Increase to exclude small noise specks.")
        params_layout.addRow("Min spot radius (px):", min_spot_spin)

        # Kurtosis threshold — how peaked the intensity distribution must be
        kurtosis_spin = _make_spinbox(-10.0, 0.0, -3.0, 0.5)
        kurtosis_spin.setToolTip("Kurtosis threshold. More negative = keep flatter distributions (more permissive). Default -3.0. Try -5.0 to -8.0 if too many puncta are rejected.")
        params_layout.addRow("Kurtosis threshold:", kurtosis_spin)

        # Local SNR threshold
        local_snr_spin = _make_spinbox(0.0, 5.0, 1.0, 0.1)
        local_snr_spin.setToolTip("Local SNR threshold. Lower = keep dimmer puncta. Default 1.0. Try 0.5 if puncta in bright regions are being lost.")
        params_layout.addRow("Local SNR threshold:", local_snr_spin)

        # Global SNR threshold
        global_snr_spin = _make_spinbox(0.0, 5.0, 1.0, 0.1)
        global_snr_spin.setToolTip("Global SNR threshold relative to whole-cell background. Default 1.0. Lower to retain puncta in high-background cells.")
        params_layout.addRow("Global SNR threshold:", global_snr_spin)

        # Intensity scale (HWHM multiplier)
        hwhm_spin = _make_spinbox(0.0, 5.0, 1.17, 0.1)
        hwhm_spin.setToolTip("Intensity threshold scale (multiples of local background SD). Default 1.17 (HWHM). Lower = keep puncta closer to background intensity.")
        params_layout.addRow("Intensity scale (×SD):", hwhm_spin)

        # Max area fraction
        max_area_spin = _make_spinbox(0.01, 1.0, 0.25, 0.05)
        max_area_spin.setToolTip("Maximum puncta area as a fraction of cell area. Default 0.25. Increase if large condensates are being excluded.")
        params_layout.addRow("Max area (fraction of cell):", max_area_spin)

        # Refinement parameters are hidden behind an off-by-default reveal
        # checkbox (advanced tuning; sensible defaults are used otherwise).
        from PyQt5.QtWidgets import QCheckBox as _QCheckBox
        _refine_cb = _QCheckBox("Show refinement parameters")
        _refine_cb.setChecked(False)
        _refine_cb.setToolTip("Advanced tuning for condensate segmentation. "
                              "Defaults work for most data.")
        process_cells_layout.addWidget(_refine_cb)
        params_group.setVisible(False)
        _refine_cb.toggled.connect(params_group.setVisible)
        process_cells_layout.addWidget(params_group)

        # ── Run button ────────────────────────────────────────────────────
        process_cells_button = QPushButton("Run Condensate Segmentation")
        process_cells_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        def _on_condensate_seg():
            import functools
            # Partially apply the refinement params to run_segment_subcellular_objects
            fn = functools.partial(
                run_segment_subcellular_objects,
                kurtosis_threshold=kurtosis_spin.value(),
                local_snr_threshold=local_snr_spin.value(),
                global_snr_threshold=global_snr_spin.value(),
                intensity_hwhm_scale=hwhm_spin.value(),
                max_area_fraction=max_area_spin.value(),
                min_spot_radius=min_spot_spin.value(),
            )
            fn.__name__ = 'run_segment_subcellular_objects'
            self.on_general_button_clicked(
                fn, self.viewer,
                process_cells_image1_dropdown, process_cells_image2_dropdown,
                self.central_manager.active_data_class, self.viewer)
            self._record('condensate_segmentation', {
                'seg_image_layer': process_cells_image1_dropdown.currentText(),
                'measure_image_layer': process_cells_image2_dropdown.currentText(),
                'kurtosis_threshold': kurtosis_spin.value(),
                'local_snr_threshold': local_snr_spin.value(),
                'global_snr_threshold': global_snr_spin.value(),
                'intensity_hwhm_scale': hwhm_spin.value(),
                'max_area_fraction': max_area_spin.value(),
                'min_spot_radius': min_spot_spin.value(),
            })
        process_cells_button.clicked.connect(_on_condensate_seg)
        try:
            from pycat.ui.field_status import button_with_circle
            process_cells_layout.addWidget(button_with_circle(
                process_cells_button,
                watch_dropdowns=[process_cells_image1_dropdown,
                                 process_cells_image2_dropdown]))
        except Exception:
            process_cells_layout.addWidget(process_cells_button)
        process_cells_widget = QWidget()
        process_cells_widget.setLayout(process_cells_layout)
        self._add_widget_to_layout_or_dock(process_cells_widget, layout, separate_widget, "Condensate Segmentation Dock")
