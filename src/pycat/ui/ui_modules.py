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

from pycat.utils.general_utils import debug_log
from napari.utils.notifications import show_warning as napari_show_warning
from PyQt5.QtWidgets import (
    QDoubleSpinBox,
    QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QRadioButton, QPushButton, 
    QLineEdit, QWidget, QComboBox, QSlider, QScrollArea, QSizePolicy, QAction,
    QTabWidget, QToolButton, QFrame)
from PyQt5.QtCore import Qt, QObject

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
from pycat.ui.ui_diagnostics_mixin import _DiagnosticsWidgetsMixin
from pycat.ui.ui_filtering_mixin import _FilteringWidgetsMixin
from pycat.ui.ui_segmentation_mixin import _SegmentationWidgetsMixin
from pycat.ui.ui_analysis_mixin import _AnalysisWidgetsMixin
from pycat.ui.ui_labels_mixin import _LabelsMasksWidgetsMixin
from pycat.ui.ui_imageops_mixin import _ImageOpsWidgetsMixin
from pycat.toolbox.pixel_wise_corr_analysis_tools import run_pwcca
from pycat.toolbox.obj_based_coloc_analysis_tools import run_manders_coloc, run_obca
from pycat.toolbox.two_channel_coloc_tools import _add_run_two_channel_coloc
from pycat.toolbox.video_export_tools import _add_export_timeseries_video
from pycat.toolbox.ts_cellpose_tools import _add_run_ts_cellpose
from pycat.toolbox.spatial_metrology_ui import _add_spatial_metrology
from pycat.toolbox.spida_ui import _add_spida
from pycat.toolbox.nb_ui import _add_number_and_brightness
from pycat.toolbox.fibril_ui import _add_fibril_analysis
from pycat.toolbox.spatial_randomness_tools import _add_spatial_randomness
from pycat.toolbox.fft_bandpass_tools import run_fft_bandpass, run_im2bw
from pycat.toolbox.brightfield_tools import run_best_slice
from pycat.toolbox.molecular_counting_tools import _add_molecular_counting
from pycat.toolbox.gaussian_localization_tools import _add_gaussian_localization
from pycat.toolbox.partition_enrichment_tools import _add_client_enrichment
from pycat.toolbox.intensity_profile_tools import _add_intensity_profile
from pycat.toolbox.morphological_complexity_tools import _add_morphological_complexity
from pycat.toolbox.advanced_analysis_ui import _add_advanced_analysis
from pycat.toolbox.data_qc_ui import _add_data_qc
from pycat.toolbox.contrast_cascade_ui import _add_contrast_cascade
from pycat.toolbox.condensate_physics_ui import _add_condensate_physics
from pycat.toolbox.brightfield_ui import BrightfieldCondensateUI
from pycat.toolbox.invitro_fluor_ui import InVitroFluorUI
from pycat.toolbox.timeseries_invitro_fluor_ui import TimeSeriesInVitroFluorUI
from pycat.toolbox.vpt_ui import VideoParticleTrackingUI
from pycat.toolbox.frap_ui import FRAPUI
from pycat.toolbox.fusion_ui import DropletFusionUI
from pycat.toolbox.temperature_ui import TemperatureDependentUI
from pycat.toolbox.fd_curve_ui import FDCurveUI
from pycat.toolbox.invitro_bf_ui import InVitroBFUI
from pycat.toolbox.zstack_segmentation_ui import ZStackSegmentationUI
from pycat.toolbox.correlation_func_analysis_tools import run_ccf_analysis, run_autocorrelation_analysis
from pycat.toolbox.label_and_mask_tools import (
    run_convert_labels_to_mask, run_measure_region_props, run_update_labels, run_label_binary_mask, 
    run_measure_binary_mask, run_binary_morph_operation,
    run_expand_labels, run_mask_logic_merge)
from pycat.toolbox.layer_tools import run_simple_multi_merge, run_advanced_two_layer_merge
from pycat.toolbox.data_viz_tools import PlottingWidget
from pycat.data.data_modules import BaseDataClass
from pycat.toolbox.spatial_acf_tools import _add_run_sacf_analysis
from pycat.toolbox.timeseries_condensate_tools import _add_run_timeseries_condensate_analysis, _add_lazy_preprocess_stack, _add_ts_upscale_stack



# BaseUIClass + the scroll-guard helpers moved to base_ui.py (ui_decomposition); re-exported so the
# classes below (which inherit BaseUIClass) resolve it and external `from ui_modules import guard_wheel`
# (and BaseUIClass, etc.) keep working. base_ui is a leaf module -> no import cycle.
from pycat.ui.base_ui import (  # noqa: F401
    BaseUIClass, _WheelScrollGuard, _wheel_guard, guard_wheel, _relax_min_widths, _apply_scroll_guard)

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
        from PyQt5.QtWidgets import QComboBox, QFileDialog, QLabel
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


# ── Session restore: which method to reopen, and how to rebuild its view ─────────────────────
#
# `active_method` in the manifest is the UI class name that was open when the session was saved. On
# load, `_on_load` maps it to the `_switch_to_*` method that reopens it. A session saved before
# `active_method` was recorded has none, so the method is inferred from a signature dataframe instead.

# A restored dataframe that identifies the method, for sessions predating `active_method`.


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

    def _switch_to_invitro_fluor_analysis(self, *args, **kwargs):
        """Switch to the in vitro fluorescence condensate analysis pipeline."""
        self._switch_analysis(BaseDataClass, InVitroFluorUI, *args, **kwargs)

    def _switch_to_ts_invitro_fluor_analysis(self, *args, **kwargs):
        """Switch to the time-series (2D+t) in vitro fluorescence pipeline."""
        self._switch_analysis(BaseDataClass, TimeSeriesInVitroFluorUI, *args, **kwargs)

    def _switch_to_vpt_analysis(self, *args, **kwargs):
        """Switch to the Video Particle Tracking (microrheology) pipeline."""
        self._switch_analysis(BaseDataClass, VideoParticleTrackingUI, *args, **kwargs)

    def _switch_to_frap_analysis(self, *args, **kwargs):
        """Switch to the FRAP analysis pipeline."""
        self._switch_analysis(BaseDataClass, FRAPUI, *args, **kwargs)

    def _switch_to_fusion_analysis(self, *args, **kwargs):
        """Switch to the Droplet Fusion (C-Trap) pipeline."""
        self._switch_analysis(BaseDataClass, DropletFusionUI, *args, **kwargs)

    def _switch_to_temperature_analysis(self, *args, **kwargs):
        """Switch to the Temperature-Dependent Microscopy pipeline."""
        self._switch_analysis(BaseDataClass, TemperatureDependentUI, *args, **kwargs)

    def _switch_to_fd_curve_analysis(self, *args, **kwargs):
        """Switch to the Force-Distance Curve (DNA tethering) pipeline."""
        self._switch_analysis(BaseDataClass, FDCurveUI, *args, **kwargs)

    def _switch_to_invitro_bf_analysis(self, *args, **kwargs):
        """Switch to the in vitro brightfield condensate analysis pipeline."""
        self._switch_analysis(BaseDataClass, InVitroBFUI, *args, **kwargs)

    def _switch_to_zstack_analysis(self, *args, **kwargs):
        """Switch to the Z-stack (3D) condensate segmentation pipeline."""
        self._switch_analysis(BaseDataClass, ZStackSegmentationUI, *args, **kwargs)

    def _switch_to_brightfield_analysis(self, *args, **kwargs):
        """Switch to the brightfield condensate analysis pipeline."""
        self._switch_analysis(BaseDataClass, BrightfieldCondensateUI, *args, **kwargs)

    def _switch_to_timeseries_analysis(self, *args, **kwargs):
        """Switches the analysis interface to time-series condensate analysis."""
        self._switch_analysis(BaseDataClass, TimeSeriesCondensateUI, *args, **kwargs)

    def _switch_to_coloc_analysis(self, *args, **kwargs):
        """Switch to the unified (tabbed) colocalization analysis pipeline."""
        self._switch_analysis(BaseDataClass, ColocalizationAnalysisUI, *args, **kwargs)

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
        """Back-compat: defaults to the in-vitro fibril pipeline."""
        self._switch_to_fibril_analysis_vitro(*args, **kwargs)

    def _switch_to_fibril_analysis_cellulo(self, *args, **kwargs):
        """Switch to fibril analysis tuned for fibrils IN CELLS (cellular context:
        membranes/cells present, so cell segmentation + per-cell context apply)."""
        kwargs.pop('fibril_context', None)
        self.central_manager._fibril_context = 'cellulo'
        self._switch_analysis(BaseDataClass, FibrilAnalysisUI, *args, **kwargs)

    def _switch_to_fibril_analysis_vitro(self, *args, **kwargs):
        """Switch to fibril analysis tuned for IN-VITRO fibrils (purified/
        reconstituted: no cells, whole-field fibril morphometry)."""
        kwargs.pop('fibril_context', None)
        self.central_manager._fibril_context = 'vitro'
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
        # Activate the workflow checklist for this pipeline
        try:
            self.central_manager.workflow_checklist.activate('condensate')
            # Replay any steps already recorded before the pipeline was opened
            bp = getattr(self.central_manager, '_pycat_batch_processor', None)
            if bp:
                for step in bp.config.get('steps', []):
                    self.central_manager.workflow_checklist.on_step_recorded(
                        step['step'])
        except Exception:
            pass

        # Add analysis and processing steps to the layout, each staged with its
        # checklist step number (see CONDENSATE_PIPELINE). _stage_step prepends
        # "Step N — " to the next shared builder's title.
        self._add_workflow_header(self.condensate_layout, include_pixel_gate=True)
        self._stage_step("Step 2 — ")
        self.central_manager.toolbox_functions_ui._add_measure_line(layout=self.condensate_layout)
        self._stage_step("Step 3 — ")
        self.central_manager.toolbox_functions_ui._add_run_upscaling(layout=self.condensate_layout)
        # Pre-process produces both the pre-processed and background-removed
        # layers in one click (merged 1.5.136), covering checklist steps 4 & 5.
        self._stage_step("Steps 4–5 — ")
        self.central_manager.toolbox_functions_ui._add_pre_process(layout=self.condensate_layout)
        # (Enhanced BG removal is now produced by the Pre-process Image button — merged in 1.5.136)
        self._stage_step("Step 6 — ")
        self.central_manager.toolbox_functions_ui._add_run_cellpose_segmentation(layout=self.condensate_layout)
        self._stage_step("Step 7 — ")
        self.central_manager.toolbox_functions_ui._add_run_cell_analysis_func(layout=self.condensate_layout)
        self._stage_step("Step 8 — ")
        self.central_manager.toolbox_functions_ui._add_run_segment_subcellular_objects(layout=self.condensate_layout)
        self._stage_step("Step 9 — ")
        self.central_manager.toolbox_functions_ui._add_run_puncta_analysis_func(layout=self.condensate_layout)

        # ── Spatial Metrology ───────────────────────────────────────────────
        self._stage_step("Step 10 — ")
        self.central_manager.toolbox_functions_ui._add_spatial_metrology(
            layout=self.condensate_layout)

        # ── Advanced Analysis (Morphological / Dynamic / Organizational) ──
        # Advanced Analysis bundles checklist steps 11–13 (Morphological
        # Complexity, Dynamic Spatial Phenotyping, Organizational Metrics) into
        # one tabbed, optional block.
        self._stage_step("Steps 11–13 — ")
        self.central_manager.toolbox_functions_ui._add_advanced_analysis(
            layout=self.condensate_layout)

        # ── Condensate Biophysics (MSD, Csat, kinetics, QC) ─────────────
        self.central_manager.toolbox_functions_ui._add_condensate_physics(
            layout=self.condensate_layout)

        self._stage_step("Step 14 — ")
        self.central_manager.toolbox_functions_ui._add_save_and_clear(layout=self.condensate_layout)
        # ... Add other components in the order you want ...

        # Create a main widget and assign the vertical layout to it
        main_widget = QWidget()
        main_widget.setLayout(self.condensate_layout)

        # Create a scroll area to enable scrolling for the UI components
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)  # Make the scroll area resizable
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_widget.setMinimumWidth(0)
        try:
            _relax_min_widths(main_widget)
        except Exception:
            pass
        scroll_area.setWidget(main_widget)  # Set the main widget as the scroll area's content

        # Add the scroll area to the viewer as a dockable widget for condensate analysis
        self.viewer.window.add_dock_widget(scroll_area, name="Object Analysis Dock")

        # Set the size policy to make the widget and scroll area expand with the window
        main_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Align the layout to the top of the widget to ensure orderly arrangement
        self.condensate_layout.setAlignment(Qt.AlignTop)
        _apply_scroll_guard(main_widget)


class TimeSeriesCondensateUI(AnalysisMethodsUI):
    """
    Dedicated pipeline dock for time-series condensate analysis.

    Workflow order:
      1. Open Image Stack   — loads (T,H,W) via Open/Save File(s) > Open Image Stack (T/Z / IMS)
      2. Select Reference Frame — user picks which frame to use for segmentation
      3. Pre-process Image  — runs on the reference frame
      4. Enhanced BG Removal
      5. Cellpose Segmentation — on the reference frame
      6. Cell Analyzer — produces Labeled Cell Mask
      7. Time-Series Condensate Analysis — propagates segmentation across all frames
      8. Save and Clear
    """

    def __init__(self, viewer, central_manager):
        super().__init__(viewer, central_manager)
        self.ts_layout = QVBoxLayout()
        self.ts_layout.setSpacing(8)
        self.ts_layout.setContentsMargins(6, 6, 6, 6)

        # Activate the workflow checklist for this pipeline
        try:
            self.central_manager.workflow_checklist.activate('timeseries')
            # Replay any steps already recorded before the pipeline was opened
            bp = getattr(self.central_manager, '_pycat_batch_processor', None)
            if bp:
                for step in bp.config.get('steps', []):
                    self.central_manager.workflow_checklist.on_step_recorded(
                        step['step'])
        except Exception:
            pass


    def setup_ui(self):
        tfu = self.central_manager.toolbox_functions_ui

        # ── Step 1: load (hybrid — status marker + stack-load instruction) ──
        # Single Step 1 block: the red/green "image loaded" marker on top, and
        # the time-series-specific load instruction below it. Previously there
        # were two competing Step 1s (a standalone instruction label AND the
        # workflow header's file-I/O block); merged into one here.
        self._add_workflow_header(
            self.ts_layout, include_pixel_gate=True,
            instruction_html=(
                "Load your time-series via "
                "<i>★ Open/Save File(s) → Open Image Stack (T/Z / IMS)</i>"))

        # ── Step 2: Reference frame selector ─────────────────────────────
        self._add_reference_frame_selector(self.ts_layout)

        # ── Steps 3-4: measurement lines, upscale, lazy stack preprocessing ─
        # Order matches the 2D cellular workflow: measure → upscale → preprocess.
        # Upscaling is optional and produces a lazy zarr-backed stack, so
        # downstream preprocess/Cellpose/analysis all run on the upscaled data.
        tfu._add_measure_line(layout=self.ts_layout)
        tfu._add_ts_upscale_stack(layout=self.ts_layout)
        tfu._add_lazy_preprocess_stack(layout=self.ts_layout)

        # ── Steps 5-6: Cellpose and cell analysis ─────────────────────────
        # Keyframe Cellpose: runs Cellpose every N frames and propagates
        # the nearest mask to all other frames — much faster than running
        # on every frame while remaining accurate for slow-moving cells.
        # Cell Analyzer still runs on the frame-0 mask as normal.
        tfu._add_run_ts_cellpose(layout=self.ts_layout)
        tfu._add_run_cell_analysis_func(layout=self.ts_layout)

        # ── Step 7: Time-Series Condensate Analysis ────────────────────────
        tfu._add_run_timeseries_condensate_analysis(layout=self.ts_layout)

        # ── Step 8: Advanced Analysis (dynamic spatial / morphological) ─────
        tfu._add_advanced_analysis(layout=self.ts_layout)

        # ── Step 8b: Condensate Biophysics (MSD, Csat, kinetics) ───────────
        tfu._add_condensate_physics(layout=self.ts_layout)

        # ── Step 9: Export video  [optional] ─────────────────────────────────
        tfu._add_export_timeseries_video(layout=self.ts_layout)

        # ── Step 10: Save & Clear ─────────────────────────────────────────────
        tfu._add_save_and_clear(layout=self.ts_layout)

        main_widget = QWidget()
        main_widget.setLayout(self.ts_layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_widget.setMinimumWidth(0)
        try:
            _relax_min_widths(main_widget)
        except Exception:
            pass
        scroll_area.setWidget(main_widget)

        self.viewer.window.add_dock_widget(
            scroll_area, name="Time-Series Condensate Analysis Dock"
        )

        # Minimum vertical policy: inner widget is only as tall as its content.
        # Without this, Qt stretches main_widget to fill the entire dock and
        # distributes the extra space among sections — creating large gaps.
        # Scroll appears automatically if the dock is shorter than the content.
        main_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.ts_layout.setAlignment(Qt.AlignTop)
        # Prevent scroll wheel from adjusting spinboxes/sliders/dropdowns
        # when the user is scrolling through the dock panel.
        _apply_scroll_guard(main_widget)

    def _add_reference_frame_selector(self, layout):
        """
        Widget for selecting a reference frame and optionally restricting all
        subsequent analysis to a contiguous sub-range of the stack.

        The frame range is stored in the data repository and respected by:
          - Lazy stack preprocessing (only processes the selected range)
          - Keyframe Cellpose (keyframes only within the range)
          - Time-Series Condensate Analysis (iterates only over the range)
          - Save & Clear (saves only the range if a sub-range is active)

        This lets users analyse a specific phase of an experiment
        (e.g. frames 100–400 after stimulus addition) without modifying
        the source file or loading the whole stack into memory.
        """
        from PyQt5.QtWidgets import (QGroupBox, QFormLayout, QSpinBox,
                                      QPushButton, QLabel)
        import numpy as np

        group = QGroupBox("Step 2 — Reference Frame & Analysis Range")
        form  = QFormLayout(group)
        form.setContentsMargins(9, 20, 9, 6)

        stack_dropdown = self.central_manager.toolbox_functions_ui.create_layer_dropdown(
            napari.layers.Image
        )
        form.addRow("Stack layer:", stack_dropdown)

        # Reference frame
        frame_spin = QSpinBox()
        frame_spin.setRange(0, 9999)
        frame_spin.setValue(0)
        frame_spin.setToolTip(
            "Frame index (0-based) to use for pre-processing and Cellpose. "
            "This frame's cell mask is propagated to all analysed frames."
        )
        form.addRow("Reference frame:", frame_spin)

        # Frame range
        range_check = QCheckBox("Restrict to frame range")
        range_check.setChecked(False)
        range_check.setToolTip(
            "When checked, all subsequent steps (preprocessing, Cellpose, "
            "condensate analysis) operate only on frames in the selected range. "
            "Useful for analysing a specific phase of the experiment."
        )
        form.addRow("", range_check)

        start_spin = QSpinBox()
        start_spin.setRange(0, 9999)
        start_spin.setValue(0)
        start_spin.setEnabled(False)
        start_spin.setToolTip("First frame of the analysis range (inclusive, 0-based).")
        form.addRow("Start frame:", start_spin)

        end_spin = QSpinBox()
        end_spin.setRange(0, 9999)
        end_spin.setValue(599)
        end_spin.setEnabled(False)
        end_spin.setToolTip("Last frame of the analysis range (inclusive, 0-based).")
        form.addRow("End frame:", end_spin)

        range_info = QLabel("")
        range_info.setWordWrap(True)

        range_info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        range_info.setStyleSheet("color: #aaa; font-size: 9pt;")
        form.addRow("", range_info)

        def _update_range_controls():
            enabled = range_check.isChecked()
            start_spin.setEnabled(enabled)
            end_spin.setEnabled(enabled)
            if enabled:
                n = end_spin.value() - start_spin.value() + 1
                range_info.setText(f"{n} frames selected")
            else:
                range_info.setText("")

        def _update_count():
            if range_check.isChecked():
                n = end_spin.value() - start_spin.value() + 1
                range_info.setText(f"{n} frames selected")

        def _on_user_range_edit():
            # A manual edit to start/end (not a programmatic refresh) means the
            # user has deliberately chosen a range — protect it from being reset
            # by later downstream layer insertions.
            if not _range_updating[0]:
                _range_locked[0] = True
            _update_count()

        def _on_range_check_toggled():
            if range_check.isChecked():
                _range_locked[0] = True   # checking the box is a deliberate choice
            _update_range_controls()

        range_check.stateChanged.connect(lambda _: _on_range_check_toggled())
        start_spin.valueChanged.connect(lambda _: _on_user_range_edit())
        end_spin.valueChanged.connect(lambda _: _on_user_range_edit())

        # Auto-populate range from stack when dropdown changes.
        # _range_locked becomes True once the user clicks Apply ROI — after
        # that, inserting new layers (which triggers currentIndexChanged via
        # the name_hint auto-select machinery) must NOT reset the spinboxes
        # back to full range, because the user has deliberately set a range.
        _range_locked = [False]
        # True only while _on_stack_changed is programmatically updating the
        # spinboxes, so those updates are not mistaken for a user edit.
        _range_updating = [False]

        def _on_stack_changed():
            if _range_locked[0]:
                # User has already applied a range — only update the maximums
                # (so the spinboxes don't go out of bounds on a different stack)
                # but preserve the current start/end values.
                name = stack_dropdown.currentText()
                try:
                    layer = self.viewer.layers[name]
                    n_t = layer.data.shape[0] if layer.data.ndim == 3 else 1
                    end_spin.setMaximum(n_t - 1)
                    start_spin.setMaximum(n_t - 1)
                    frame_spin.setMaximum(n_t - 1)
                except Exception:
                    pass
                return
            name = stack_dropdown.currentText()
            try:
                layer = self.viewer.layers[name]
                n_t = layer.data.shape[0] if layer.data.ndim == 3 else 1
                _range_updating[0] = True
                end_spin.setValue(max(0, n_t - 1))
                end_spin.setMaximum(n_t - 1)
                start_spin.setMaximum(n_t - 1)
                frame_spin.setMaximum(n_t - 1)
                _range_updating[0] = False
            except Exception:
                _range_updating[0] = False
        stack_dropdown.currentIndexChanged.connect(_on_stack_changed)

        # ── XY ROI crop controls ─────────────────────────────────────────
        from PyQt5.QtWidgets import QComboBox as _QCB, QGroupBox as _QGB

        roi_grp = _QGB("XY Region of Interest")
        roi_grp.setFlat(True)
        roi_grp_layout = QVBoxLayout(roi_grp)
        roi_grp_layout.setContentsMargins(4, 20, 4, 4)

        # ── GUI interactive mode ──────────────────────────────────────────
        roi_check = QCheckBox("Restrict to drawn rectangle (interactive)")
        roi_check.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        roi_check.setChecked(False)
        roi_check.setToolTip(
            "Draw a Rectangle shape on the stack layer, then check this box.\n"
            "All preprocessing steps in this session will be cropped to that region.\n"
            "In batch replay, the automatic strategy below is used instead."
        )
        roi_grp_layout.addWidget(roi_check)

        # ── Add ROI layer button ──────────────────────────────────────────
        # Creates a ready-to-draw Shapes layer rather than requiring the user
        # to add one manually from the napari layer panel. Clicking activates
        # Rectangle mode immediately so the user can draw straight away.
        def _add_roi_shapes_layer():
            roi_name = "Draw XY ROI Here"
            # Adding a Shapes layer to a viewer showing a large lazy IMS stack
            # makes napari recompute the combined world extent, which can take a
            # noticeable moment. Show a wait cursor and defer the heavy work by
            # one event-loop tick so the button click feels instant instead of
            # freezing mid-press.
            from PyQt5.QtCore import QTimer, Qt as _Qt
            from PyQt5.QtWidgets import QApplication as _QApp

            def _do_add():
                try:
                    _QApp.setOverrideCursor(_Qt.WaitCursor)
                    if roi_name not in [l.name for l in self.viewer.layers]:
                        roi_layer = self.viewer.add_shapes(
                            name=roi_name,
                            shape_type='rectangle',
                            face_color='transparent',
                            edge_color='#f0a500',
                            edge_width=3,
                        )
                    else:
                        roi_layer = self.viewer.layers[roi_name]
                    try:
                        roi_layer.visible = True
                    except Exception:
                        pass
                    self.viewer.layers.selection.active = roi_layer
                    self.viewer.layers.selection.active.mode = 'add_rectangle'
                    self.central_manager.toolbox_functions_ui.update_dropdown_items(
                        roi_shapes_dd, napari.layers.Shapes)
                    idx = roi_shapes_dd.findText(roi_name)
                    if idx != -1:
                        roi_shapes_dd.setCurrentIndex(idx)
                finally:
                    _QApp.restoreOverrideCursor()

            QTimer.singleShot(0, _do_add)

        add_roi_btn = QPushButton("＋  Add ROI Drawing Layer")
        add_roi_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        add_roi_btn.setToolTip(
            "Creates a Shapes layer pre-configured for rectangle drawing "
            "and activates Rectangle mode — just click and drag to define "
            "the XY region to crop all subsequent processing to."
        )
        add_roi_btn.clicked.connect(_add_roi_shapes_layer)
        roi_grp_layout.addWidget(add_roi_btn)

        roi_shapes_dd = self.central_manager.toolbox_functions_ui.create_layer_dropdown(
            napari.layers.Shapes, name_hint='ROI')
        roi_shapes_dd.setEnabled(False)
        roi_shapes_dd.setToolTip("Shapes layer containing the Rectangle to crop to.")
        roi_grp_layout.addWidget(roi_shapes_dd)

        # Batch auto-crop note (read-only, strategy is always 'auto' for
        # interactive use — the batch replay uses Cellpose bbox or Multi-Otsu,
        # but those algorithms require a Cellpose mask that doesn't exist yet
        # at this step in the interactive pipeline, so we keep this simple).
        batch_roi_check = QCheckBox("Enable auto-crop in batch replay")
        batch_roi_check.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        batch_roi_check.setChecked(True)
        batch_roi_check.setToolTip(
            "In headless batch replay, PyCAT will automatically compute a\n"
            "tight cell bounding-box crop so condensate segmentation runs\n"
            "in each cell's region only — much faster for sparse fields.\n\n"
            "The batch strategy (Cellpose bbox or Multi-Otsu) is chosen\n"
            "automatically at replay time based on what masks are available.\n"
            "No action needed here for interactive analysis.")
        roi_grp_layout.addWidget(batch_roi_check)

        # Keep these as hidden variables so the record call below still works
        strategy_dd    = None   # used only in batch record, not shown in UI
        otsu_classes_spin = None

        roi_info = QLabel("")
        roi_info.setWordWrap(True)

        roi_info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        roi_info.setStyleSheet("color: #aaa; font-size: 9pt;")
        roi_grp_layout.addWidget(roi_info)

        form.addRow(roi_grp)

        def _on_roi_toggle():
            enabled = roi_check.isChecked()
            roi_shapes_dd.setEnabled(enabled)
            if not enabled:
                roi_info.setText("")

        roi_check.stateChanged.connect(lambda _: _on_roi_toggle())

        def _get_roi_bbox():
            """
            Extract (y0, y1, x0, x1) crop box from the first Rectangle shape
            in the selected Shapes layer.  Returns None if no valid rectangle found.
            """
            try:
                shapes_layer = self.viewer.layers[roi_shapes_dd.currentText()]
            except KeyError:
                return None
            if not shapes_layer.data:
                return None
            # napari shapes data: list of (N,2) arrays in (y,x) order
            for shape_data in shapes_layer.data:
                pts = np.asarray(shape_data)
                if pts.ndim == 2 and pts.shape[1] == 2:
                    y0 = int(np.floor(pts[:,0].min()))
                    y1 = int(np.ceil(pts[:,0].max()))
                    x0 = int(np.floor(pts[:,1].min()))
                    x1 = int(np.ceil(pts[:,1].max()))
                    return (y0, y1, x0, x1)
            return None

        extract_btn = QPushButton("Apply ROI / Range & Extract Reference Frame")
        extract_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        extract_btn.setToolTip(
            "Extracts the reference frame (cropped if ROI is set) as a 2D layer\n"
            "and stores the frame range and XY crop so all downstream steps\n"
            "operate on the same spatial and temporal region."
        )

        def _on_extract():
            layer_name = stack_dropdown.currentText()
            try:
                layer = self.viewer.layers[layer_name]
            except KeyError:
                from napari.utils.notifications import show_warning as w
                w(f"Layer '{layer_name}' not found.")
                return

            data = layer.data
            if data.ndim < 3:
                from napari.utils.notifications import show_warning as w
                w("Selected layer is already 2D — no extraction needed.")
                return

            n_t = data.shape[0]
            H   = data.shape[1]
            W   = data.shape[2]
            frame_idx = min(int(frame_spin.value()), n_t - 1)

            # ── Temporal range ───────────────────────────────────────────
            if range_check.isChecked():
                t_start = max(0, min(int(start_spin.value()), n_t - 1))
                t_end   = max(t_start, min(int(end_spin.value()), n_t - 1))
            else:
                t_start, t_end = 0, n_t - 1

            # ── XY crop ─────────────────────────────────────────────────
            y0, y1, x0, x1 = 0, H, 0, W   # defaults: full frame
            if roi_check.isChecked():
                bbox = _get_roi_bbox()
                if bbox is None:
                    from napari.utils.notifications import show_warning as w
                    w("No valid Rectangle shape found in the selected Shapes layer.")
                    return
                y0_raw, y1_raw, x0_raw, x1_raw = bbox
                # Clamp to image bounds
                y0 = max(0, y0_raw);  y1 = min(H, y1_raw)
                x0 = max(0, x0_raw);  x1 = min(W, x1_raw)
                if y1 <= y0 or x1 <= x0:
                    from napari.utils.notifications import show_warning as w
                    w(f"ROI bounding box is degenerate: y=[{y0},{y1}] x=[{x0},{x1}]")
                    return
                roi_info.setText(f"Crop: y[{y0}:{y1}] x[{x0}:{x1}]  "
                                  f"({y1-y0}×{x1-x0} px)")

            # ── Store everything in data repository ──────────────────────
            dr = self.central_manager.active_data_class.data_repository
            dr['timeseries_reference_frame'] = frame_idx
            dr['timeseries_frame_start']     = t_start
            dr['timeseries_frame_end']        = t_end
            dr['timeseries_n_frames']         = t_end - t_start + 1
            _range_locked[0] = True   # prevent new layer insertions from resetting the range
            dr['timeseries_roi_y0']           = y0
            dr['timeseries_roi_y1']           = y1
            dr['timeseries_roi_x0']           = x0
            dr['timeseries_roi_x1']           = x1
            dr['timeseries_roi_active']       = roi_check.isChecked()

            # ── Extract and crop the reference frame ─────────────────────
            ref_frame = np.asarray(data[frame_idx]).astype(np.float32)
            if roi_check.isChecked():
                ref_frame = ref_frame[y0:y1, x0:x1]

            ref_name  = f"{layer_name} [frame {frame_idx}]"
            if roi_check.isChecked():
                ref_name += f" [ROI {y1-y0}×{x1-x0}]"

            saved_step = tuple(self.viewer.dims.current_step)
            self.viewer.add_image(ref_frame, name=ref_name)
            try:
                self.viewer.dims.current_step = saved_step
            except Exception:
                pass

            from napari.utils.notifications import show_info as napari_show_info
            range_str = (f"frames {t_start}–{t_end} ({t_end-t_start+1} frames)"
                         if range_check.isChecked() else f"all {n_t} frames")
            roi_str   = (f", ROI y[{y0}:{y1}] x[{x0}:{x1}]"
                         if roi_check.isChecked() else "")
            napari_show_info(
                f"Reference frame {frame_idx} extracted as '{ref_name}'. "
                f"Analysis range: {range_str}{roi_str}."
            )

            # Determine batch auto-crop strategy from UI
            strategy_text = 'auto'   # strategy decided at batch replay time

            # Record for batch — includes both the GUI rectangle crop (for
            # replay_set_frame_range) and the batch auto-crop config
            self.central_manager.toolbox_functions_ui._record(
                'set_frame_range', {
                    'stack_layer':     layer_name,
                    'reference_frame': frame_idx,
                    'frame_start':     t_start,
                    'frame_end':       t_end,
                    'roi_y0': y0, 'roi_y1': y1,
                    'roi_x0': x0, 'roi_x1': x1,
                    'roi_active': roi_check.isChecked(),
                })

            # Record the auto-crop step separately so it appears in the
            # batch config and can be replayed in headless mode
            if batch_roi_check.isChecked():
                self.central_manager.toolbox_functions_ui._record(
                    'auto_crop_roi', {
                        'strategy':       strategy_text,
                        'n_otsu_classes': 3,   # default; set at batch replay time
                        'padding_px':     8,
                    })

        form.addRow("", extract_btn)
        extract_btn.clicked.connect(_on_extract)
        layout.addWidget(group)


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
        # Activate the workflow checklist for this pipeline
        try:
            self.central_manager.workflow_checklist.activate('coloc')
        except Exception:
            pass
        self._add_workflow_header(self.object_coloc_layout, include_pixel_gate=True)
        self.central_manager.toolbox_functions_ui._add_measure_line(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_upscaling(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_pre_process(layout=self.object_coloc_layout)
        # (Enhanced BG removal is now produced by the Pre-process Image button — merged in 1.5.136)
        self.central_manager.toolbox_functions_ui._add_run_cellpose_segmentation(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_cell_analysis_func(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_segment_subcellular_objects(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_puncta_analysis_func(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_two_channel_coloc(layout=self.object_coloc_layout)
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
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_widget.setMinimumWidth(0)
        try:
            _relax_min_widths(main_widget)
        except Exception:
            pass
        scroll_area.setWidget(main_widget)  # Assign the main widget as the scroll area's content

        # Integrate the scroll area into the viewer as a dockable widget
        self.viewer.window.add_dock_widget(scroll_area, name="Object Based Colocalization Analysis Dock")

        # Configure size policies to ensure UI components and scroll area expand appropriately
        main_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Align UI components to the top of the layout for a tidy presentation
        self.object_coloc_layout.setAlignment(Qt.AlignTop)
        _apply_scroll_guard(main_widget)


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
        self._add_workflow_header(self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_measure_line(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_clahe(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_wbns(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_rb_gaussian_background_removal(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_apply_rescale_intensity(layout=self.pixel_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_cellpose_segmentation(layout=self.pixel_coloc_layout)
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
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_widget.setMinimumWidth(0)
        try:
            _relax_min_widths(main_widget)
        except Exception:
            pass
        scroll_area.setWidget(main_widget)  # Assign the main widget as the scroll area's content

        # Integrate the scroll area into the viewer as a dockable widget
        self.viewer.window.add_dock_widget(scroll_area, name="Pixel-Wise Corr-Coeff Analysis Dock")

        # Configure size policies to ensure UI components and scroll area expand appropriately
        main_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Align UI components to the top of the layout for a tidy presentation
        self.pixel_coloc_layout.setAlignment(Qt.AlignTop)



class ColocalizationAnalysisUI(AnalysisMethodsUI):
    """Unified colocalization analysis with PIXEL-WISE and OBJECT-BASED tabs.

    Replaces the two separate coloc pipelines (ObjectColocAnalysisUI and
    PixelColocAnalysisUI) with a single tabbed widget, matching the tabbed
    multi-method pattern used elsewhere in PyCAT. Shared preprocessing /
    segmentation lives above the tabs (both approaches need channels and, for
    object-based, masks); the coloc-specific runners live in their respective
    tabs.

    Layer hand-off: because the runner dropdowns read live viewer layers, any
    layers produced by an upstream method (2D/3D cell or in-vitro analysis) are
    already available here. `_suggest_layers()` additionally makes a best-effort
    guess at sensible defaults from common upstream layer names, so a
    cell/in-vitro → colocalization workflow lands with the right dropdowns
    pre-filled (the user can always re-curate).
    """

    def __init__(self, viewer, central_manager):
        super().__init__(viewer, central_manager)
        self.coloc_layout = QVBoxLayout()

    # -- layer hand-off: guess sensible defaults from upstream method outputs --
    # Substrings (lowercased) that commonly name processed intensity images and
    # segmentation masks produced by the cell / in-vitro / z-stack pipelines.
    _IMAGE_HINTS = ('upscaled', 'preprocessed', 'pre-process', 'fluorescence',
                    'processed', 'channel', 'intensity')
    _MASK_HINTS = ('labeled cell', 'cell mask', 'condensate', 'puncta',
                   'droplet', 'labeled', 'mask', 'segmentation')

    def _suggest_layers(self):
        """Return best-effort (image_names, mask_names) ordered by likely
        relevance, for pre-filling coloc dropdowns from upstream outputs."""
        import napari as _napari
        imgs, masks = [], []
        try:
            for l in self.viewer.layers:
                nm = l.name
                low = nm.lower()
                if isinstance(l, _napari.layers.Image):
                    score = sum(h in low for h in self._IMAGE_HINTS)
                    imgs.append((score, nm))
                elif isinstance(l, _napari.layers.Labels):
                    score = sum(h in low for h in self._MASK_HINTS)
                    masks.append((score, nm))
        except Exception:
            pass
        imgs.sort(key=lambda t: -t[0])
        masks.sort(key=lambda t: -t[0])
        return [n for _, n in imgs], [n for _, n in masks]

    def setup_ui(self):
        try:
            self.central_manager.workflow_checklist.activate('coloc')
        except Exception:
            pass

        tf = self.central_manager.toolbox_functions_ui

        # Shared header + measure line (both tabs need a scale and a loaded image).
        self._add_workflow_header(self.coloc_layout, include_pixel_gate=True)
        tf._add_measure_line(layout=self.coloc_layout)

        note = QLabel(
            "<span style='color:#888;font-size:9pt;'>"
            "Colocalization operates on layers already in the viewer — including "
            "processed images and masks produced by other analysis methods. Run a "
            "cell / in-vitro analysis first, then the dropdowns below will list "
            "those outputs.</span>")
        note.setWordWrap(True)
        self.coloc_layout.addWidget(note)

        # ── Tabs ──────────────────────────────────────────────────────────────
        tabs = QTabWidget()

        # Pixel-wise tab: intensity-correlation preprocessing + PWCCA + CCF.
        pix_w = QWidget(); pix_l = QVBoxLayout(pix_w)
        tf._add_run_clahe(layout=pix_l)
        tf._add_run_wbns(layout=pix_l)
        tf._add_run_rb_gaussian_background_removal(layout=pix_l)
        tf._add_run_apply_rescale_intensity(layout=pix_l)
        tf._add_run_pwcca(layout=pix_l)
        tf._add_run_ccf_analysis(layout=pix_l)
        pix_l.setAlignment(Qt.AlignTop)
        tabs.addTab(pix_w, "Pixel-wise Correlation")

        # Object-based tab: segmentation + object coloc metrics.
        obj_w = QWidget(); obj_l = QVBoxLayout(obj_w)
        tf._add_run_upscaling(layout=obj_l)
        tf._add_pre_process(layout=obj_l)
        tf._add_run_cellpose_segmentation(layout=obj_l)
        tf._add_run_cell_analysis_func(layout=obj_l)
        tf._add_run_segment_subcellular_objects(layout=obj_l)
        tf._add_run_puncta_analysis_func(layout=obj_l)
        tf._add_run_two_channel_coloc(layout=obj_l)
        tf._add_run_obca(layout=obj_l)
        tf._add_run_manders_coloc(layout=obj_l)
        obj_l.setAlignment(Qt.AlignTop)
        tabs.addTab(obj_w, "Object-based Colocalization")

        self.coloc_layout.addWidget(tabs)
        tf._add_save_and_clear(layout=self.coloc_layout)

        main_widget = QWidget()
        main_widget.setLayout(self.coloc_layout)

        # Best-effort layer hand-off: pre-select likely layers now that the
        # runners (and their dropdowns) are built and parented under main_widget.
        try:
            self._apply_layer_suggestions(root_widget=main_widget)
        except Exception:
            pass

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_widget.setMinimumWidth(0)
        try:
            _relax_min_widths(main_widget)
        except Exception:
            pass
        scroll_area.setWidget(main_widget)
        self.viewer.window.add_dock_widget(
            scroll_area, name="Colocalization Analysis Dock")
        main_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.coloc_layout.setAlignment(Qt.AlignTop)
        _apply_scroll_guard(main_widget)

    def _apply_layer_suggestions(self, root_widget=None):
        """Pre-select likely image/mask layers in coloc dropdowns (best effort).

        Walks ALL QComboBox descendants of the dock (via Qt findChildren, so it
        reaches dropdowns nested inside the tab pages) and, for any dropdown that
        contains a suggested layer name, sets it to the highest-scoring
        suggestion present. Convenience only — the user re-curates freely.
        """
        img_names, mask_names = self._suggest_layers()
        if not img_names and not mask_names:
            return
        from PyQt5.QtWidgets import QComboBox
        if root_widget is None:
            return  # applied after the main widget exists (see setup_ui)
        ordered = img_names + mask_names
        for combo in root_widget.findChildren(QComboBox):
            try:
                items = [combo.itemText(j) for j in range(combo.count())]
                for cand in ordered:
                    if cand in items:
                        combo.setCurrentText(cand)
                        break
            except Exception:
                continue


class CollapsibleSection(QWidget):
    """A titled, collapsible container. Clicking the header toggles a content area
    whose inner layout (``content_layout``) tools can populate via the usual
    ``_add_*(layout=...)`` methods. Used to group the many toolbox tools in the
    Exploratory Analysis dock into coherent, expandable sections that start
    collapsed so the panel isn't overwhelming.
    """
    def __init__(self, title, expanded=False, parent=None):
        super().__init__(parent)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        self._toggle = QToolButton()
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self._toggle.setStyleSheet(
            "QToolButton { border: none; font-weight: bold; padding: 6px 4px; "
            "text-align: left; background: #2b2b2b; }"
            "QToolButton:hover { background: #353535; }")
        self._toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._toggle.toggled.connect(self._on_toggled)
        self._outer.addWidget(self._toggle)

        # Content area
        self._content = QFrame()
        self._content.setFrameShape(QFrame.NoFrame)
        self.content_layout = QVBoxLayout(self._content)
        self.content_layout.setContentsMargins(8, 4, 4, 8)
        self.content_layout.setSpacing(4)
        self._content.setVisible(expanded)
        self._outer.addWidget(self._content)

    def _on_toggled(self, checked):
        self._toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self._content.setVisible(checked)

    def set_expanded(self, expanded):
        self._toggle.setChecked(expanded)


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
        Build the Exploratory Analysis dock: a "workbench" giving access to the
        full toolbox, grouped into collapsible sections that mirror the Toolbox
        menu structure. Most sections start collapsed so the panel isn't
        overwhelming; a couple of common starting points (Setup, Segmentation,
        Save) start expanded. Tools that only make sense inside a dedicated,
        stateful pipeline (whole cellular/in-vitro/time-series/z-stack pipelines,
        the biophysics single-tether methods) are intentionally NOT duplicated
        here — this dock is for mixing individual tools, not re-hosting pipelines.
        """
        tf = self.central_manager.toolbox_functions_ui
        L = self.general_layout

        # Always-visible header (file IO + pixel gate + measure).
        self._add_workflow_header(L, include_pixel_gate=True)

        def section(title, expanded=False):
            sec = CollapsibleSection(title, expanded=expanded)
            L.addWidget(sec)
            return sec.content_layout

        def add(fn, layout):
            """Add one tool to a section, but never let a single tool's
            construction error tear down the whole dock — log it and continue so
            the rest of the workbench still builds."""
            try:
                fn(layout=layout)
            except Exception as e:
                import traceback
                name = getattr(fn, '__name__', str(fn))
                print(f"[PyCAT Exploratory] tool '{name}' failed to load: {e}")
                traceback.print_exc()
                try:
                    warn = QLabel(f"\u26a0 {name} unavailable (see terminal)")
                    warn.setStyleSheet("color:#c66; font-size:9pt;")
                    warn.setWordWrap(True)
                    layout.addWidget(warn)
                except Exception:
                    pass

        # ── Setup (expanded) ────────────────────────────────────────────────
        s = section("Setup & Measure", expanded=True)
        add(tf._add_measure_line, s)
        add(tf._add_run_upscaling, s)
        add(tf._add_pre_process, s)
        # ── Image Processing (collapsed) ──
        s = section("Image Processing")
        add(tf._add_run_spectral_unmixing, s)
        add(tf._add_run_reference_subtraction, s)
        add(tf._add_run_apply_rescale_intensity, s)
        add(tf._add_run_invert_image, s)
        add(tf._add_run_rb_gaussian_background_removal, s)
        add(tf._add_run_enhanced_rb_gaussian_bg_removal, s)
        add(tf._add_run_calibration_correction, s)
        add(tf._add_run_wbns, s)
        add(tf._add_run_wavelet_noise_subtraction, s)
        add(tf._add_run_apply_bilateral_filter, s)
        add(tf._add_run_clahe, s)
        add(tf._add_run_peak_and_edge_enhancement, s)
        add(tf._add_run_morphological_gaussian_filter, s)
        add(tf._add_run_apply_laplace_of_gauss_filter, s)
        add(tf._add_run_dpr, s)
        add(tf._add_run_fft_bandpass, s)
        # Stack / time-series variants (operate on a whole (T,H,W) stack).
        add(tf._add_ts_upscale_stack, s)
        add(tf._add_lazy_preprocess_stack, s)
        # General techniques promoted out of single-method pipelines.
        add(tf._add_image_registration, s)
        add(tf._add_bleach_correction, s)
        add(tf._add_detrend_stack, s)

        # ── Segmentation (expanded — common starting point) ─────────────────
        s = section("Segmentation", expanded=True)
        add(tf._add_run_train_and_apply_rf_classifier, s)
        add(tf._add_run_local_thresholding, s)
        add(tf._add_run_im2bw, s)
        add(tf._add_run_cellpose_segmentation, s)
        add(tf._add_run_ts_cellpose, s)
        add(tf._add_run_fz_segmentation_and_merging, s)
        add(tf._add_gaussian_localization, s)
        add(tf._add_contrast_cascade, s)

        # ── Labels & Masks (collapsed) ──────────────────────────────────────
        s = section("Labels & Masks")
        add(tf._add_run_binary_morph_operation, s)
        add(tf._add_run_measure_binary_mask, s)
        add(tf._add_run_label_binary_mask, s)
        add(tf._add_run_update_labels, s)
        add(tf._add_run_convert_labels_to_mask, s)
        add(tf._add_run_expand_labels, s)
        add(tf._add_run_measure_region_props, s)

        # ── Layer Operations (collapsed) ────────────────────────────────────
        s = section("Layer Operations")
        add(tf._add_run_simple_multi_merge, s)
        add(tf._add_run_advanced_two_layer_merge, s)
        add(tf._add_run_mask_logic_merge, s)

        # ── Cell & Object Analyzers (collapsed) ─────────────────────────────
        s = section("Cell & Object Analyzers")
        add(tf._add_run_cell_analysis_func, s)
        add(tf._add_run_segment_subcellular_objects, s)
        add(tf._add_run_puncta_analysis_func, s)
        add(tf._add_partial_volume_measure, s)

        # ── Colocalization / Correlation (collapsed) ────────────────────────
        s = section("Colocalization / Correlation")
        add(tf._add_run_autocorrelation_analysis, s)
        add(tf._add_client_enrichment, s)
        add(tf._add_run_pwcca, s)
        add(tf._add_run_ccf_analysis, s)
        add(tf._add_run_obca, s)
        add(tf._add_run_manders_coloc, s)
        add(tf._add_run_two_channel_coloc, s)

        # ── Spatial Metrology (collapsed) ───────────────────────────────────
        s = section("Spatial Metrology")
        add(tf._add_run_sacf_analysis, s)
        add(tf._add_spatial_metrology, s)
        add(tf._add_spatial_randomness, s)
        add(tf._add_intensity_profile, s)
        add(tf._add_morphological_complexity, s)
        add(tf._add_fibril_analysis, s)

        # ── Advanced Analysis (collapsed) ───────────────────────────────────
        s = section("Advanced Analysis")
        add(tf._add_advanced_analysis, s)
        add(tf._add_condensate_physics, s)
        add(tf._add_molecular_counting, s)
        add(tf._add_spida, s)
        add(tf._add_number_and_brightness, s)

        # ── Structure Estimators (collapsed) ────────────────────────────────
        s = section("Structure Estimators")
        add(tf._add_chromatin_topology, s)
        add(tf._add_nucleolus_void_estimator, s)

        # ── Diagnostics & QC (collapsed) ────────────────────────────────────
        s = section("Diagnostics & QC")
        add(tf._add_pipeline_diagnostics, s)
        add(tf._add_pipeline_snr_analysis, s)
        add(tf._add_foreground_suppression_tuner, s)
        add(tf._add_temporal_enhancement_optimizer, s)
        add(tf._add_segmentation_benchmark, s)
        add(tf._add_segmentation_speed_comparison, s)
        add(tf._add_display_diagnostics, s)
        add(tf._add_data_qc, s)
        add(tf._add_frame_quality_qc, s)
        add(tf._add_motion_scale_estimator, s)
        add(tf._add_plotting_widget, s)
        add(tf._add_export_timeseries_video, s)

        # ── Save (expanded) ─────────────────────────────────────────────────
        s = section("Save & Clear", expanded=True)
        add(tf._add_save_and_clear, s)

        # Create a main widget to contain everything
        main_widget = QWidget()
        main_widget.setLayout(self.general_layout)

        # Create a scroll area and set the main widget as its central widget
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_widget.setMinimumWidth(0)
        try:
            _relax_min_widths(main_widget)
        except Exception:
            pass
        scroll_area.setWidget(main_widget)

        # Add the scroll area to the viewer as a dock widget
        self.viewer.window.add_dock_widget(scroll_area, name="Exploratory Analysis Dock")

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

        Pipeline order:
          1-9.  Preprocessing, enhancement, and segmentation (unchanged from baseline)
          10.   Label connected components — converts the final binary mask into
                individually-labeled fibril objects, required for the per-object
                spatial metrology steps that follow.
          11.   Measure binary mask — whole-image intensity/area summary (baseline).
          12.   Morphological Complexity — fractal dimension, lacunarity, and
                tortuosity (path length vs. end-to-end distance) are the standard
                quantitative descriptors for fibrillar/filamentous structures;
                orientation order parameter quantifies fibril bundle alignment
                (nematic order), relevant for amyloid, collagen, cytoskeletal,
                or DNA fibril studies.
          13.   Organizational Metrics — spatial entropy, DBSCAN cluster sizing,
                inter-fibril spacing, and network occupancy characterise how
                fibrils are distributed and bundled across the field.
          14.   Save & Clear.
        """
        # Activate the workflow checklist for this pipeline
        try:
            self.central_manager.workflow_checklist.activate('fibril')
            bp = getattr(self.central_manager, '_pycat_batch_processor', None)
            if bp:
                for step in bp.config.get('steps', []):
                    self.central_manager.workflow_checklist.on_step_recorded(
                        step['step'])
        except Exception:
            pass

        # In-cellulo vs in-vitro: cellular fibrils sit inside cells, so we add cell
        # segmentation to give per-cell context; in-vitro fibrils are analysed over
        # the whole field. Default to in-vitro (back-compat) if unset.
        context = getattr(self.central_manager, '_fibril_context', 'vitro')
        is_cellulo = (context == 'cellulo')

        # Setup the specific UI components for fibril analysis
        header = ("Cellular Fibril Analysis" if is_cellulo
                  else "In Vitro Fibril Analysis")
        self._add_workflow_header(self.fibril_layout, include_pixel_gate=True)
        self.central_manager.toolbox_functions_ui._add_measure_line(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_upscaling(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_apply_bilateral_filter(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_pre_process(layout=self.fibril_layout)
        # (Enhanced BG removal is now produced by the Pre-process Image button — merged in 1.5.136)
        self.central_manager.toolbox_functions_ui._add_run_peak_and_edge_enhancement(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_morphological_gaussian_filter(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_train_and_apply_rf_classifier(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_local_thresholding(layout=self.fibril_layout)
        # In cells: segment cells so fibrils can be attributed to a cell (per-cell
        # context). In vitro: skip — fibrils are analysed across the whole field.
        if is_cellulo:
            self.central_manager.toolbox_functions_ui._add_run_cell_analysis_func(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_label_binary_mask(layout=self.fibril_layout)
        self.central_manager.toolbox_functions_ui._add_run_measure_binary_mask(layout=self.fibril_layout)

        # ── Spatial metrology additions ─────────────────────────────────
        # Morphological Complexity and Organizational Metrics tabs from the
        # Advanced Analysis dock apply directly to labeled fibril masks —
        # tortuosity and orientation order in particular were designed with
        # fibrillar structures in mind (see morphological_complexity_tools.py).
        self.central_manager.toolbox_functions_ui._add_advanced_analysis(layout=self.fibril_layout)

        self.central_manager.toolbox_functions_ui._add_save_and_clear(layout=self.fibril_layout)
        # ... Add other components in the order you want ...

        # Create a main widget to contain everything
        main_widget = QWidget()
        main_widget.setLayout(self.fibril_layout)

        # Create a scroll area and set the main widget as its central widget
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_widget.setMinimumWidth(0)
        try:
            _relax_min_widths(main_widget)
        except Exception:
            pass
        scroll_area.setWidget(main_widget)

        # Add the scroll area to the viewer as a dock widget
        self.viewer.window.add_dock_widget(
            scroll_area,
            name=("Cellular Fibril Analysis Dock" if is_cellulo
                  else "In Vitro Fibril Analysis Dock"))

        # Set the size policy of the main widget and scroll area
        main_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Align UI components to the top of the layout for a neat presentation
        self.fibril_layout.setAlignment(Qt.AlignTop)




# MenuManager + its session maps and file-drop filter moved to menu_manager (1.6.149); ALL re-exported.
from pycat.ui.menu_manager import (  # noqa: E402,F401
    MenuManager, _SESSION_METHOD_SWITCH, _SESSION_METHOD_BY_DATA, _FileDropFilter)
