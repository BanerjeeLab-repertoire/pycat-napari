"""``ToolboxFunctionsUI`` — the main toolbox panel (image ops, segmentation, analysis, filtering, labels),
split out of ui_modules.py (ui_decomposition, Part 1 final). It inherits ``BaseUIClass`` (from the base_ui
leaf module) plus the six widget mixins. ui_modules re-exports it, so
``from pycat.ui.ui_modules import ToolboxFunctionsUI`` still works.
"""
from __future__ import annotations

import napari
from PyQt5.QtWidgets import QDoubleSpinBox, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QRadioButton, QPushButton, QLineEdit, QWidget, QComboBox, QSlider, QScrollArea, QSizePolicy, QAction, QTabWidget, QToolButton, QFrame
from pycat.toolbox.image_processing_tools import run_pre_process_image, run_apply_rescale_intensity, run_invert_image, run_upscaling_func, run_rb_gaussian_background_removal, run_enhanced_rb_gaussian_bg_removal, run_wbns, run_wavelet_noise_subtraction, run_apply_bilateral_filter, run_clahe, run_peak_and_edge_enhancement, run_morphological_gaussian_filter, run_dpr, run_apply_laplace_of_gauss_filter
from pycat.ui.ui_diagnostics_mixin import _DiagnosticsWidgetsMixin
from pycat.ui.ui_filtering_mixin import _FilteringWidgetsMixin
from pycat.ui.ui_segmentation_mixin import _SegmentationWidgetsMixin
from pycat.ui.ui_analysis_mixin import _AnalysisWidgetsMixin
from pycat.ui.ui_labels_mixin import _LabelsMasksWidgetsMixin
from pycat.ui.ui_imageops_mixin import _ImageOpsWidgetsMixin
from pycat.toolbox.two_channel_coloc_tools import _add_run_two_channel_coloc
from pycat.toolbox.video_export_tools import _add_export_timeseries_video
from pycat.toolbox.ts_cellpose_tools import _add_run_ts_cellpose
from pycat.toolbox.spatial_metrology_ui import _add_spatial_metrology
from pycat.toolbox.spida_ui import _add_spida
from pycat.toolbox.nb_ui import _add_number_and_brightness
from pycat.toolbox.fibril_ui import _add_fibril_analysis
from pycat.toolbox.spatial_randomness_tools import _add_spatial_randomness
from pycat.toolbox.molecular_counting_tools import _add_molecular_counting
from pycat.toolbox.gaussian_localization_tools import _add_gaussian_localization
from pycat.toolbox.partition_enrichment_tools import _add_client_enrichment
from pycat.toolbox.intensity_profile_tools import _add_intensity_profile
from pycat.toolbox.morphological_complexity_tools import _add_morphological_complexity
from pycat.toolbox.advanced_analysis_ui import _add_advanced_analysis
from pycat.toolbox.data_qc_ui import _add_data_qc
from pycat.toolbox.contrast_cascade_ui import _add_contrast_cascade
from pycat.toolbox.condensate_physics_ui import _add_condensate_physics
from pycat.toolbox.data_viz_tools import PlottingWidget
from pycat.toolbox.spatial_acf_tools import _add_run_sacf_analysis
from pycat.toolbox.timeseries_condensate_tools import _add_run_timeseries_condensate_analysis, _add_lazy_preprocess_stack, _add_ts_upscale_stack
from pycat.ui.base_ui import BaseUIClass, _WheelScrollGuard, _wheel_guard, guard_wheel, _relax_min_widths, _apply_scroll_guard


class ToolboxFunctionsUI(BaseUIClass, _DiagnosticsWidgetsMixin, _FilteringWidgetsMixin, _SegmentationWidgetsMixin, _AnalysisWidgetsMixin, _LabelsMasksWidgetsMixin, _ImageOpsWidgetsMixin):
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
        self._add_run_sacf_analysis = lambda **kw: _add_run_sacf_analysis(self, **kw)
        self._add_run_timeseries_condensate_analysis = lambda **kw: _add_run_timeseries_condensate_analysis(self, **kw)
        self._add_lazy_preprocess_stack = lambda **kw: _add_lazy_preprocess_stack(self, **kw)
        self._add_ts_upscale_stack = lambda **kw: _add_ts_upscale_stack(self, **kw)
        self._add_run_two_channel_coloc = lambda **kw: _add_run_two_channel_coloc(self, **kw)
        self._add_export_timeseries_video = lambda **kw: _add_export_timeseries_video(self, **kw)
        self._add_run_ts_cellpose = lambda **kw: _add_run_ts_cellpose(self, **kw)
        self._add_spatial_metrology = lambda **kw: _add_spatial_metrology(self, **kw)
        self._add_spida = lambda **kw: _add_spida(self, **kw)
        self._add_number_and_brightness = lambda **kw: _add_number_and_brightness(self, **kw)
        self._add_fibril_analysis = lambda **kw: _add_fibril_analysis(self, **kw)
        self._add_spatial_randomness = lambda **kw: _add_spatial_randomness(self, **kw)
        self._add_molecular_counting = lambda **kw: _add_molecular_counting(self, **kw)
        self._add_gaussian_localization = lambda **kw: _add_gaussian_localization(self, **kw)
        self._add_client_enrichment = lambda **kw: _add_client_enrichment(self, **kw)
        self._add_intensity_profile = lambda **kw: _add_intensity_profile(self, **kw)
        self._add_morphological_complexity = lambda **kw: _add_morphological_complexity(self, **kw)
        self._add_advanced_analysis = lambda **kw: _add_advanced_analysis(self, **kw)
        self._add_data_qc = lambda **kw: _add_data_qc(self, **kw)
        self._add_contrast_cascade = lambda **kw: _add_contrast_cascade(self, **kw)
        self._add_condensate_physics = lambda **kw: _add_condensate_physics(self, **kw)
        # General-purpose techniques promoted out of method-specific pipelines
        # (registration was fibril-only, focus/entropy QC was temperature-only,
        # bleach correction was condensate-physics-only, detrending was N&B-only)
        # — see pycat/toolbox/general_image_tools.py.
        from pycat.toolbox.general_image_tools import (
            _add_image_registration, _add_frame_quality_qc,
            _add_bleach_correction, _add_detrend_stack,
            _add_motion_scale_estimator, _add_partial_volume_measure)
        self._add_image_registration = lambda **kw: _add_image_registration(self, **kw)
        self._add_frame_quality_qc = lambda **kw: _add_frame_quality_qc(self, **kw)
        self._add_bleach_correction = lambda **kw: _add_bleach_correction(self, **kw)
        self._add_detrend_stack = lambda **kw: _add_detrend_stack(self, **kw)
        self._add_motion_scale_estimator = lambda **kw: _add_motion_scale_estimator(self, **kw)
        self._add_partial_volume_measure = lambda **kw: _add_partial_volume_measure(self, **kw)
        # New pipeline UI entry points exposed as standalone toolbox tools.
        # These use the same (ui_instance, layout=None, separate_widget=False)
        # calling convention as _add_spatial_metrology so they slot directly
        # into the toolbox menu with {'separate_widget': True}.
        from pycat.toolbox.brightfield_ui import (
            _add_bf_preprocessing, _add_bf_condensate_segmentation,
            _add_bf_od_metrics, _add_bf_per_cell_summary,
            _add_bf_spatial, _add_bf_dynamics, _add_bf_texture, _add_bf_frame_qc)
        from pycat.toolbox.zstack_segmentation_ui import (
            _add_zstack_bg_removal, _add_zstack_cell_seg,
            _add_zstack_condensate_seg, _add_zstack_metrics)

        def _make_dock_wrapper(fn, dock_name):
            def _wrapper(layout=None, separate_widget=False):
                from PyQt5.QtWidgets import QVBoxLayout as _VBL, QWidget as _QW, QScrollArea as _QSA
                from PyQt5.QtCore import Qt as _Qt
                inner_layout = _VBL()
                fn(self, inner_layout)
                w = _QW(); w.setLayout(inner_layout)
                if separate_widget:
                    sa = _QSA(); sa.setWidgetResizable(True); sa.setHorizontalScrollBarPolicy(_Qt.ScrollBarAlwaysOff); sa.setWidget(w)
                    self.viewer.window.add_dock_widget(sa, name=dock_name, area='right')
                elif layout is not None:
                    layout.addLayout(inner_layout)
            return _wrapper

        self._add_bf_preprocessing           = _make_dock_wrapper(_add_bf_preprocessing,           'BF Preprocessing')
        self._add_bf_condensate_segmentation = _make_dock_wrapper(_add_bf_condensate_segmentation, 'BF Condensate Segmentation')
        self._add_bf_od_metrics              = _make_dock_wrapper(_add_bf_od_metrics,              'BF Optical Density Metrics')
        self._add_bf_per_cell_summary        = _make_dock_wrapper(_add_bf_per_cell_summary,        'BF Per-Cell Summary')
        self._add_bf_spatial                 = _make_dock_wrapper(_add_bf_spatial,                 'BF Spatial Metrology')
        self._add_bf_dynamics                = _make_dock_wrapper(_add_bf_dynamics,                'BF Dynamics')
        self._add_bf_texture                 = _make_dock_wrapper(_add_bf_texture,                 'BF Texture')
        self._add_bf_frame_qc                = _make_dock_wrapper(_add_bf_frame_qc,                'BF Frame Quality')
        self._add_zstack_bg_removal          = _make_dock_wrapper(_add_zstack_bg_removal,          '3D Background Removal')
        self._add_zstack_cell_seg            = _make_dock_wrapper(_add_zstack_cell_seg,            '3D Cell Segmentation')
        self._add_zstack_condensate_seg      = _make_dock_wrapper(_add_zstack_condensate_seg,      '3D Condensate Segmentation')
        self._add_zstack_metrics             = _make_dock_wrapper(_add_zstack_metrics,             '3D Condensate Metrics')

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
        # Title via add_text_label so a staged "Step N — " prefix (e.g. Step 14)
        # is applied and styled like the other enumerated step headers.
        self.add_text_label(save_and_clear_layout, "Save & Clear", bold=True)
        save_and_clear_button = QPushButton("Save and Clear") # Create a button widget
        def _on_save_and_clear():
            self.on_general_button_clicked(
                self.central_manager.file_io.save_and_clear_all, None, self.viewer)
            # save_and_clear_all records the step internally after dialogs
            # close, capturing the actual layer and dataframe selections made.
        save_and_clear_button.clicked.connect(_on_save_and_clear)
        try:
            from pycat.ui.field_status import button_with_circle
            save_and_clear_layout.addWidget(button_with_circle(save_and_clear_button))  # red
        except Exception:
            save_and_clear_layout.addWidget(save_and_clear_button) # Add the button to the layout
        save_and_clear_widget = QWidget()
        save_and_clear_widget.setLayout(save_and_clear_layout)
        self._add_widget_to_layout_or_dock(save_and_clear_widget, layout, separate_widget, "Save and Clear Dock")


    def _add_measure_line(self, layout=None, separate_widget=False):
        """Add a widget for measuring object diameters with drawn lines, optionally in a separate dock."""
        measure_layout = QVBoxLayout() # Create a vertical layout widget
        self.add_text_label(measure_layout, 'Measure Object Diameters', bold=True) # Add widget title label
        # Single cycling button: Draw Lines → Measure Lines → Clear Lines → …
        # The label is state-driven (reads actual layer/line/measurement state) so
        # it's always honest. Starts as "Draw Lines" (layers are created on demand,
        # not at file load).
        measure_button = QPushButton("Draw Lines")
        def _arm_line_drawing():
            """Activate a diameter Shapes layer in add_line mode so the user can
            draw. Creates the 'Object Diameter' / 'Cell Diameter' layers on demand
            (via the shared tagged drawing-layer factory) if they don't exist yet —
            they are no longer created eagerly at every file load. Clicking an
            image layer's eyeball (napari default) steals the active layer,
            silently disabling line drawing even though the Shapes layer still
            looks selected; this re-arms it deterministically."""
            try:
                import numpy as _np
                # Create-if-missing via the factory (seeds against the NaN-extent
                # Home-button crash and tags role=annotation + purpose).
                try:
                    from pycat.toolbox.drawing_layers import add_drawing_layer
                    for _nm, _purpose in (('Object Diameter', 'object_diameter'),
                                          ('Cell Diameter', 'cell_diameter')):
                        if _nm not in self.viewer.layers:
                            add_drawing_layer(self.viewer, kind='line',
                                              purpose=_purpose, name=_nm,
                                              activate=False)
                except Exception as _ce:
                    import os as _os
                    if _os.environ.get('PYCAT_DEBUG'):
                        print(f"[PyCAT] diameter layer create failed: {_ce}")
                target = None
                for _nm in ('Object Diameter', 'Cell Diameter'):
                    if _nm in self.viewer.layers:
                        lyr = self.viewer.layers[_nm]
                        # Count real (non-seed) lines: the seed is a ~0-length
                        # line at the origin used to keep the extent finite.
                        n_real = 0
                        for d in getattr(lyr, 'data', []) or []:
                            try:
                                if _np.ptp(_np.asarray(d), axis=0).max() > 1e-2:
                                    n_real += 1
                            except Exception:
                                pass
                        # Prefer the first layer that has no real lines yet.
                        if target is None or n_real == 0:
                            target = lyr
                            if n_real == 0:
                                break
                if target is not None:
                    # A Shapes layer that is hidden cannot be drawn on — napari
                    # silently ignores the drawing tool. Make it visible (and
                    # ensure a usable opacity) before activating add_line mode.
                    try:
                        target.visible = True
                        if getattr(target, 'opacity', 1.0) < 0.05:
                            target.opacity = 0.7
                    except Exception:
                        pass
                    self.viewer.layers.selection.active = target
                    target.mode = 'add_line'
            except Exception as _e:
                import os as _os
                if _os.environ.get('PYCAT_DEBUG'):
                    print(f"[PyCAT] arm line drawing failed: {_e}")
        def _diameter_layers():
            """Return (object_layer, cell_layer) or (None, None) for missing."""
            o = self.viewer.layers['Object Diameter'] if 'Object Diameter' in self.viewer.layers else None
            c = self.viewer.layers['Cell Diameter'] if 'Cell Diameter' in self.viewer.layers else None
            return o, c

        def _count_real_lines(layer):
            """Number of non-seed lines on a Shapes layer (seed is ~0-length)."""
            import numpy as _np
            n = 0
            for d in getattr(layer, 'data', []) or []:
                try:
                    if _np.ptp(_np.asarray(d), axis=0).max() > 1e-2:
                        n += 1
                except Exception:
                    pass
            return n

        def _measure_state():
            """Derive the button state from ACTUAL layer/line/measurement state so
            the label is always honest, even if the user drew/deleted/switched
            methods in between. Returns one of:
              'draw'    — no diameter layers exist yet
              'measure' — layers exist (drawn or empty); next action is measure
              'clear'   — a measurement has been taken; next action is clear
            """
            o, c = _diameter_layers()
            if o is None and c is None:
                return 'draw'
            dr = self.central_manager.active_data_class.data_repository
            # "measured" = calculate_length has populated a real value this cycle.
            if dr.get('_diameter_measured'):
                return 'clear'
            return 'measure'

        def _relabel():
            st = _measure_state()
            label = {'draw': 'Draw Lines', 'measure': 'Measure Lines',
                     'clear': 'Clear Lines'}[st]
            try:
                measure_button.setText(label)
            except Exception:
                pass

        def _do_draw():
            """Create the diameter layers (seeded + tagged) and arm drawing."""
            _arm_line_drawing()  # create-if-missing + arm (defined above)

        def _do_measure():
            self.on_general_button_clicked(
                self.central_manager.active_data_class.calculate_length, None, self.viewer)
            # Mark measured so the button advances to Clear.
            try:
                o, c = _diameter_layers()
                any_real = ((o is not None and _count_real_lines(o) > 0) or
                            (c is not None and _count_real_lines(c) > 0))
                self.central_manager.active_data_class.data_repository['_diameter_measured'] = bool(any_real)
            except Exception:
                pass
            self._record('measure_line', {
                'object_size': self.central_manager.active_data_class.data_repository.get('object_size'),
                'cell_diameter': self.central_manager.active_data_class.data_repository.get('cell_diameter'),
                'ball_radius': self.central_manager.active_data_class.data_repository.get('ball_radius'),
            })

        def _do_clear():
            """Delete drawn lines from both layers, reset measured values, re-seed
            for a finite extent, and re-arm drawing for a smooth draw→measure→
            clear→draw loop. Layers are NOT removed (they persist across methods)."""
            import numpy as _np
            o, c = _diameter_layers()
            for lyr in (o, c):
                if lyr is None:
                    continue
                try:
                    lyr.data = []  # remove all shapes
                    # Re-seed one invisible near-zero line so the empty layer keeps
                    # a finite extent (guards the Home-button NaN crash).
                    lyr.add(_np.array([[0.0, 0.0], [0.0, 1e-4]]),
                            shape_type='line', edge_width=0.0)
                except Exception:
                    pass
            # Reset measured values to defaults unless the user chose to persist.
            try:
                dr = self.central_manager.active_data_class.data_repository
                dr['_diameter_measured'] = False
                if not getattr(self.central_manager, 'persist_measurements', False):
                    for k in ('object_size', 'cell_diameter', 'ball_radius'):
                        dr.pop(k, None)
            except Exception:
                pass
            # Revert the red/green status circle to its initial (unmeasured) state.
            try:
                w = getattr(self, '_measure_line_status', None)
                if w is not None and hasattr(w, 'reset'):
                    w.reset()
            except Exception:
                pass
            _arm_line_drawing()  # re-arm so the user can draw again immediately

        def _has_clearable():
            """True if there are real drawn lines or measured values to lose."""
            o, c = _diameter_layers()
            if (o is not None and _count_real_lines(o) > 0) or \
               (c is not None and _count_real_lines(c) > 0):
                return True
            dr = self.central_manager.active_data_class.data_repository
            return any(dr.get(k) is not None
                       for k in ('object_size', 'cell_diameter', 'ball_radius'))

        def _confirm_clear():
            """Ask before clearing, but only when there's something to lose.
            Returns True to proceed. Defaults to proceeding if the dialog can't be
            shown (matches the button's stated action)."""
            if not _has_clearable():
                return True
            try:
                from PyQt5.QtWidgets import QMessageBox
                box = QMessageBox()
                box.setWindowTitle("Clear measurements?")
                box.setIcon(QMessageBox.Question)
                box.setText("Clear the drawn diameter line(s) and reset the "
                            "measured object size, cell diameter, and ball radius?")
                box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
                box.setDefaultButton(QMessageBox.Cancel)
                return box.exec_() == QMessageBox.Ok
            except Exception:
                return True

        def _on_measure_line():
            """Single cycling button: Draw → Measure → Clear → (Measure) …,
            with the label always reflecting actual state."""
            st = _measure_state()
            if st == 'draw':
                _do_draw()
            elif st == 'measure':
                _do_measure()
                _w = getattr(self, '_measure_line_status', None)  # green = a real measure, not the Draw click (Fix 1)
                if _w is not None and self.central_manager.active_data_class.data_repository.get('_diameter_measured'):
                    _w.mark_done()
            else:  # clear
                if not _confirm_clear():
                    return  # cancelled — leave state (and label) as "Clear Lines"
                _do_clear()
            _relabel()

        measure_button.clicked.connect(_on_measure_line)

        # Only button here; circle greens only after a real Measure — complete_on_click=False (cycling) (Fix 1).
        try:
            from pycat.ui.field_status import button_with_circle
            _measure_wrapped = button_with_circle(measure_button, complete_on_click=False)
            self._measure_line_status = _measure_wrapped
            measure_layout.addWidget(_measure_wrapped)
        except Exception:
            measure_layout.addWidget(measure_button)

        # On show, set the button label from the CURRENT state instead of
        # auto-creating the layers — if the user already drew/measured on a
        # previous visit (the layers persist across methods), the label reflects
        # that; otherwise it reads "Draw Lines". Deferred so the dock has finished
        # building and any persisted layers are present.
        try:
            from PyQt5.QtCore import QTimer as _QTarm
            _QTarm.singleShot(0, _relabel)
        except Exception:
            try:
                _relabel()
            except Exception:
                pass

        # Persist checkbox — same pattern as "Keep this pixel size for the session".
        # Off by default: Clear returns to true blank state. When ticked, ball_radius,
        # object_size, and cell_diameter are preserved across Save & Clear so the user
        # doesn't need to re-measure when running a second image of the same experiment.
        persist_cb = QCheckBox("Remember measurements across clears")
        persist_cb.setChecked(
            getattr(self.central_manager, 'persist_measurements', False))
        persist_cb.setToolTip(
            "When on, the measured object size, cell diameter, and ball radius are "
            "preserved after Save & Clear, so you don't need to re-measure when "
            "loading a second image from the same experiment.\n"
            "Leave off to return to a completely blank state after each clear.")
        def _on_persist_toggled(checked):
            self.central_manager.persist_measurements = bool(checked)
        persist_cb.toggled.connect(_on_persist_toggled)
        measure_layout.addWidget(persist_cb)

        measure_widget = QWidget() # Create a main widget to contain the input widget
        measure_widget.setLayout(measure_layout) # Set the layout for the widget
        self._add_widget_to_layout_or_dock(measure_widget, layout, separate_widget, "Measure Line Dock") # Add widget to layout or dock
    

    #### Image Processing Functions ####


    def _add_pre_process(self, layout=None, separate_widget=False):
        """Add a widget for running image pre-processing, optionally in a separate dock.

        As of 1.5.136 this single "Pre-process Image" button produces BOTH the
        "Pre-Processed [name]" layer and the "Enhanced Background Removed [name]"
        layer in one click (previously two separate buttons). Preprocessing always
        applies foreground suppression using the tuned defaults; the unchecked
        "Adjust foreground suppression" checkbox reveals five editable sliders
        (strength, log_p, con_p, min_area, border_grow) that override the defaults.
        Both the 'preprocessing' and 'background_removal' batch steps are recorded
        so replay reproduces both layers.
        """
        from PyQt5.QtWidgets import QLabel as _QLabel, QWidget as _QWidget, QFormLayout
        from PyQt5.QtCore import Qt
        from pycat.toolbox.image_processing_tools import FOREGROUND_SUPPRESSION_DEFAULTS

        pre_process_layout = QVBoxLayout()
        self.add_text_label(pre_process_layout, 'Image Pre-processing', bold=True) # Add a widget title label
        pre_process_button = QPushButton("Pre-process Image") # Create a button widget
        pre_process_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        # ── Foreground-suppression controls (collapsed by default) ───────────
        adjust_cb = QCheckBox("Adjust foreground suppression")
        adjust_cb.setChecked(False)

        # Container holding the four sliders; hidden until the box is checked.
        params_container = _QWidget()
        params_form = QFormLayout(params_container)
        params_form.setContentsMargins(8, 4, 4, 4)

        d = FOREGROUND_SUPPRESSION_DEFAULTS

        # (slider, scale) — sliders are int-only, so float params are scaled.
        def _mk_slider(minv, maxv, init, scale):
            s = QSlider(Qt.Horizontal)
            s.setMinimum(int(minv * scale)); s.setMaximum(int(maxv * scale))
            s.setValue(int(init * scale))
            return s

        strength_sl = _mk_slider(0.0, 1.0, d['strength'], 100)  # 0.00–1.00
        logp_sl     = _mk_slider(0.0, 95.0, d['log_p'], 1)       # 0–95
        conp_sl     = _mk_slider(0.0, 95.0, d['con_p'], 1)       # 0–95
        minarea_sl  = _mk_slider(1, 30, d['min_area'], 1)        # 1–30 px
        border_sl   = _mk_slider(0, 10, d['border_grow'], 1)     # 0–10 px

        strength_lbl = _QLabel(f"{d['strength']:.2f}")
        logp_lbl     = _QLabel(f"{int(d['log_p'])}")
        conp_lbl     = _QLabel(f"{int(d['con_p'])}")
        minarea_lbl  = _QLabel(f"{int(d['min_area'])}")
        border_lbl   = _QLabel(f"{int(d['border_grow'])}")

        def _row(text, slider, label):
            row = _QWidget(); rl = QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0)
            rl.addWidget(slider); rl.addWidget(label)
            params_form.addRow(text, row)

        _row("strength", strength_sl, strength_lbl)
        _row("log_p (blob)", logp_sl, logp_lbl)
        _row("con_p (contrast)", conp_sl, conp_lbl)
        _row("min_area (px)", minarea_sl, minarea_lbl)
        _row("border_grow (px)", border_sl, border_lbl)
        params_container.setVisible(False)

        def _store_params():
            """Push current slider values into the data repository."""
            dr = self.central_manager.active_data_class.data_repository
            if adjust_cb.isChecked():
                params = {
                    'strength': strength_sl.value() / 100.0,
                    'log_p':    float(logp_sl.value()),
                    'con_p':    float(conp_sl.value()),
                    'min_area': int(minarea_sl.value()),
                    'border_grow': int(border_sl.value()),
                }
                dr['foreground_suppression_params'] = params
            else:
                # Unchecked -> use defaults (clear any override).
                dr['foreground_suppression_params'] = None
            dr['suppress_foreground'] = True

        def _on_slider():
            strength_lbl.setText(f"{strength_sl.value()/100.0:.2f}")
            logp_lbl.setText(f"{logp_sl.value()}")
            conp_lbl.setText(f"{conp_sl.value()}")
            minarea_lbl.setText(f"{minarea_sl.value()}")
            border_lbl.setText(f"{border_sl.value()}")
            _store_params()

        for _s in (strength_sl, logp_sl, conp_sl, minarea_sl, border_sl):
            _s.valueChanged.connect(_on_slider)

        def _on_toggle(checked):
            params_container.setVisible(bool(checked))
            _store_params()

        adjust_cb.toggled.connect(_on_toggle)

        def _on_preprocess():
            # Capture the active layer BEFORE running — the operation adds a
            # new output layer to the viewer which napari may then select
            # as active, making post-hoc capture unreliable.
            _store_params()  # ensure repo reflects current slider state
            active = self.viewer.layers.selection.active
            active_name = active.name if active is not None else ''

            # Step 1: pre-processing → adds "Pre-Processed {name}" (suppression baked in).
            self.on_general_button_clicked(
                run_pre_process_image, None, self.central_manager.active_data_class, self.viewer)
            dr = self.central_manager.active_data_class.data_repository
            rec = {
                'active_layer': active_name,
                'ball_radius':  int(dr.get('ball_radius', 50)),
                'window_size':  int(dr.get('cell_diameter', 100)) // 2,
                'suppress_foreground': bool(dr.get('suppress_foreground', True)),
            }
            # Record suppression params only when the user overrode defaults (keeps clean configs clean).
            sp = dr.get('foreground_suppression_params', None)
            if sp:
                rec['foreground_suppression_params'] = dict(sp)
            self._record('preprocessing', rec)

            # Step 2: enhanced background removal on the just-created Pre-Processed
            # layer → adds "Enhanced Background Removed Pre-Processed {name}".
            # run_pre_process_image selects its new layer as active, so the BG
            # removal (which operates on the active layer) targets it directly.
            pp_name = f"Pre-Processed {active_name}" if active_name else None
            try:
                if pp_name and pp_name in self.viewer.layers:
                    self.viewer.layers.selection.active = self.viewer.layers[pp_name]
                self.on_general_button_clicked(
                    run_enhanced_rb_gaussian_bg_removal, None,
                    self.central_manager.active_data_class, self.viewer)
                self._record('background_removal', {
                    'active_layer': pp_name or active_name,
                    'ball_radius': int(dr.get('ball_radius', 50)),
                    **({'foreground_suppression_params': dict(sp)} if sp else {}),  # replay needs the recorded override, else defaults
                })
            except Exception as e:
                from napari.utils.notifications import show_warning
                show_warning(f"Background removal step failed: {e}")

        pre_process_button.clicked.connect(_on_preprocess)
        try:
            from pycat.ui.field_status import button_with_circle
            pre_process_layout.addWidget(button_with_circle(pre_process_button))  # red (mandatory)
        except Exception:
            pre_process_layout.addWidget(pre_process_button) # Add the button to the layout
        pre_process_layout.addWidget(adjust_cb)
        pre_process_layout.addWidget(params_container)
        pre_process_widget = QWidget()
        pre_process_widget.setLayout(pre_process_layout)
        self._add_widget_to_layout_or_dock(pre_process_widget, layout, separate_widget, "Pre-process Image Dock")


    # Image Adjustment Functions 



    def _add_run_calibration_correction(self, layout=None, separate_widget=False):
        """
        Calibration-frame background correction. Load a free-dye / flat-field OR
        clear-frame reference once (it persists across images for the session),
        pick the correction type, and apply it to the active image layer.
        """
        from PyQt5.QtWidgets import QFileDialog
        import os as _os
        from napari.utils.notifications import show_warning as _warn, show_info as _info

        lay = QVBoxLayout()
        self.add_text_label(lay, 'Calibration Background Correction', bold=True)
        info = QLabel(
            "Load a flat-field (free-dye) or clear-frame reference, then apply it "
            "to correct data. The calibration is specific to a microscope/settings/"
            "sample and persists across images until you load a new one.")
        info.setWordWrap(True); lay.addWidget(info)

        method_dd = QComboBox()
        method_dd.addItems(["Flat-field division (free-dye / illumination)",
                            "Background subtraction (clear-frame)"])
        method_dd.setToolTip(
            "Flat-field: removes multiplicative non-uniformity (vignetting).\n"
            "Subtraction: removes an additive background floor.")
        lay.addWidget(QLabel("Correction method:")); lay.addWidget(method_dd)

        status = QLabel("No calibration loaded.")
        status.setWordWrap(True)

        status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        load_btn = QPushButton("Load Calibration Reference…")
        load_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        def _on_load():
            p, _ = QFileDialog.getOpenFileName(
                None, "Load calibration reference (flat / clear frame)",
                "", "Images (*.tif *.tiff *.png *.czi);;All Files (*)")
            if not p:
                return
            try:
                import numpy as _np
                arr = None
                try:
                    # ── `get_image_data` LOADS THE WHOLE SCENE ──────────────────────
                    #
                    # Both libraries document it in the same words. This read a ZYX volume with the
                    # eager API, which on a 4-D file pulls the entire scene into memory.
                    from pycat.file_io.image_reader import open_image
                    _img = open_image(p)
                    _lazy = _img.get_image_dask_data("ZYX", C=0, T=0)
                    arr = _np.asarray(
                        _lazy.compute() if hasattr(_lazy, 'compute') else _lazy
                    ).astype(_np.float32)
                    arr = _np.squeeze(arr)
                except Exception:
                    import tifffile
                    arr = tifffile.imread(p).astype(_np.float32)
                arr = _np.squeeze(_np.asarray(arr, dtype=_np.float32))
                if arr.ndim == 3:
                    # Robust flat/clear reference from a stack: median across frames.
                    arr = _np.median(arr, axis=0)
                if arr.ndim != 2:
                    _warn(f"Calibration must be a 2D image (got shape {arr.shape})."); return
                self._calibration_ref = arr
                self._calibration_path = p
                status.setText(f"Loaded: {_os.path.basename(p)}  ({arr.shape[0]}\u00d7{arr.shape[1]})")
                _info("Calibration reference loaded.")
            except Exception as e:
                _warn(f"Could not load calibration: {e}")
        load_btn.clicked.connect(_on_load)
        lay.addWidget(load_btn); lay.addWidget(status)

        apply_btn = QPushButton("Apply to Active Layer")
        apply_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        def _on_apply():
            import numpy as _np
            ref = getattr(self, '_calibration_ref', None)
            if ref is None:
                _warn("Load a calibration reference first."); return
            active = self.viewer.layers.selection.active
            if active is None or not isinstance(active, napari.layers.Image):
                _warn("Select an image layer to correct."); return
            from pycat.file_io.stack_access import materialize_stack
            img = materialize_stack(active.data, dtype=_np.float32)   # full stack; a lazy wrapper gives only frame 0
            if img.shape[-2:] != ref.shape[-2:]:
                _warn(f"Calibration shape {ref.shape} doesn't match image "
                      f"{tuple(img.shape[-2:])} — use a reference from the same acquisition.")
                return
            from pycat.toolbox.image_processing_tools import apply_flatfield_correction, apply_background_subtraction
            if method_dd.currentIndex() == 0:
                corrected = apply_flatfield_correction(img, ref); suffix = "flatfield-corrected"; mkey = "flatfield"
            else:
                corrected = apply_background_subtraction(img, ref); suffix = "bg-subtracted"; mkey = "subtraction"
            self.viewer.add_image(corrected, name=f"{active.name} ({suffix})")
            try:
                self._record('calibration_correction', {
                    'method': mkey,
                    'calibration': _os.path.basename(getattr(self, '_calibration_path', '')),
                    'calibration_path': getattr(self, '_calibration_path', ''),
                    'layer': active.name})
            except Exception:
                pass
            _info(f"Applied {suffix} using the loaded calibration.")
        apply_btn.clicked.connect(_on_apply)
        lay.addWidget(apply_btn)

        w = QWidget(); w.setLayout(lay)
        self._add_widget_to_layout_or_dock(w, layout, separate_widget, "Calibration Correction Dock")





    #### Image Segmentation Functions #### 




    #### Image Feature Analysis Functions ####



    #### Label and Mask Tools ####


    # Labeleled Mask Tools 

    def _add_plotting_widget(self, layout=None, separate_widget=False):
        """Add a widget for plotting data, optionally in a separate dock."""
        plot_widget = PlottingWidget(self.central_manager) # Create the plotting widget by instantiating its class
        self._add_widget_to_layout_or_dock(plot_widget, layout, separate_widget, "Plotting Widget")
