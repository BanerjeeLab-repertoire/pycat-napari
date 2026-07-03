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
    QDoubleSpinBox,
    QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QRadioButton, QPushButton, 
    QLineEdit, QWidget, QComboBox, QSlider, QScrollArea, QSizePolicy, QAction)
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
from pycat.toolbox.pixel_wise_corr_analysis_tools import run_pwcca
from pycat.toolbox.obj_based_coloc_analysis_tools import run_manders_coloc, run_obca
from pycat.toolbox.two_channel_coloc_tools import _add_run_two_channel_coloc
from pycat.toolbox.video_export_tools import _add_export_timeseries_video
from pycat.toolbox.ts_cellpose_tools import _add_run_ts_cellpose
from pycat.toolbox.spatial_metrology_ui import _add_spatial_metrology
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
    run_measure_binary_mask, run_binary_morph_operation) 
from pycat.toolbox.layer_tools import run_simple_multi_merge, run_advanced_two_layer_merge
from pycat.toolbox.data_viz_tools import PlottingWidget
from pycat.data.data_modules import BaseDataClass
from pycat.toolbox.spatial_acf_tools import _add_run_sacf_analysis
from pycat.toolbox.timeseries_condensate_tools import _add_run_timeseries_condensate_analysis, _add_lazy_preprocess_stack


class _WheelScrollGuard(QObject):
    """
    Event filter that stops the mouse wheel from changing spin box / slider /
    combo values unless the control has keyboard focus, and forwards the wheel
    event to the enclosing QScrollArea so the panel scrolls instead.

    This replaces the older instance-attribute `wheelEvent` patch, which does
    not work in PyQt5: Qt dispatches the C++ virtual `wheelEvent`, which never
    looks up a Python instance attribute, so the guard was silently bypassed
    (the control changed value AND swallowed the scroll).
    """
    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if event.type() == QEvent.Wheel and not obj.hasFocus():
            from PyQt5.QtWidgets import QScrollArea, QApplication
            p = obj.parentWidget()
            while p is not None:
                if isinstance(p, QScrollArea):
                    QApplication.sendEvent(p.viewport(), event)
                    break
                p = p.parentWidget()
            return True   # consume: the control must not change its value
        return False


# Module-level singleton filter, held by this reference so it is never GC'd.
_GLOBAL_WHEEL_GUARD = None


def _wheel_guard():
    global _GLOBAL_WHEEL_GUARD
    if _GLOBAL_WHEEL_GUARD is None:
        _GLOBAL_WHEEL_GUARD = _WheelScrollGuard()
    return _GLOBAL_WHEEL_GUARD


def guard_wheel(control):
    """
    Install the wheel-scroll guard on a SINGLE control (spin box / slider /
    combo). Safe to call at widget-creation time, before the control is placed
    inside a QScrollArea — the enclosing scroll area is located at event time.
    """
    from PyQt5.QtCore import Qt
    if control is None or getattr(control, '_pycat_scroll_guard', False):
        return
    control.setFocusPolicy(Qt.StrongFocus)
    control.installEventFilter(_wheel_guard())
    control._pycat_scroll_guard = True


class _FileDropFilter(QObject):
    """
    Application-level event filter that routes files dropped anywhere on the
    napari window into PyCAT's own openers (channel assignment + data-repository
    registration), instead of napari's default reader which bypasses the PyCAT
    pipeline. Also accepts the drag-enter so the drop actually fires.

    Text/number input widgets are left alone so path drops into fields still work.
    """
    def __init__(self, file_io):
        super().__init__()
        self._file_io = file_io

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        from PyQt5.QtWidgets import QLineEdit, QTextEdit, QAbstractSpinBox
        et = event.type()
        if et not in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            return False
        if isinstance(obj, (QLineEdit, QTextEdit, QAbstractSpinBox)):
            return False   # let input fields handle their own drops
        md = event.mimeData() if hasattr(event, 'mimeData') else None
        if md is None or not md.hasUrls():
            return False
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        paths = [p for p in paths if p]
        if not paths:
            return False
        if et in (QEvent.DragEnter, QEvent.DragMove):
            event.acceptProposedAction()
            return True
        # Drop
        event.acceptProposedAction()
        self._route(paths)
        return True

    def _route(self, paths):
        import os
        ims    = [p for p in paths if os.path.splitext(p)[1].lower() == '.ims']
        others = [p for p in paths if os.path.splitext(p)[1].lower() != '.ims']
        try:
            if others:
                # 2D / multichannel opener (channel-assignment dialog).
                self._file_io.open_2d_image(file_paths=others)
            for p in ims:
                # IMS must go through the lazy stack loader.
                self._file_io.open_stack(file_path=p)
        except Exception as e:
            try:
                from napari.utils.notifications import show_warning
                show_warning(f"PyCAT could not open dropped file(s): {e}")
            except Exception:
                print(f"[PyCAT] Drop-open error: {e}")


def _apply_scroll_guard(widget):
    """
    Recursively install a wheel-scroll guard on all interactive controls
    (QComboBox, QAbstractSpinBox, QAbstractSlider) in a widget tree so that,
    inside a QScrollArea dock, hovering over a spin box / slider / combo while
    scrolling scrolls the panel instead of silently adjusting the control.

    Call once on the root widget of any dock that lives inside a QScrollArea.
    """
    from PyQt5.QtWidgets import (QAbstractSpinBox, QAbstractSlider,
                                  QComboBox as _QCB)
    controls = list(widget.findChildren((_QCB, QAbstractSpinBox, QAbstractSlider)))
    if isinstance(widget, (_QCB, QAbstractSpinBox, QAbstractSlider)):
        controls.insert(0, widget)
    for w in controls:
        guard_wheel(w)


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

    def create_layer_dropdown(self, layer_type, name_hint: str = ''):
        """
        Creates a dropdown (QComboBox) widget that lists layers of a specific type.

        Parameters
        ----------
        layer_type : type
            The type of layer to list in the dropdown, e.g., napari.layers.Image
            or napari.layers.Labels.
        name_hint : str, optional
            A substring to match against layer names when auto-selecting after a
            new layer is inserted. When a new layer whose name contains name_hint
            is added to the viewer, this dropdown will automatically jump to it.
            This implements the "auto-populate the appropriate layer as it is
            generated" UX pattern for sequential pipelines: pass e.g.
            name_hint='BG-Removed' for a background-removal output dropdown,
            name_hint='Labeled Cell Mask' for a cell-mask dropdown, etc.
            If name_hint is empty (the default), no auto-selection occurs on
            insert — the dropdown stays on whatever the user last chose.

        Returns
        -------
        dropdown : QComboBox
            The created dropdown widget populated with layers of the specified type.
        """
        dropdown = QComboBox()
        # Don't let long layer names balloon the dropdown (and the whole form)
        # past the dock width — size to a small minimum and let it shrink so the
        # right side of rows (spinbox controls, buttons) stays visible.
        from PyQt5.QtWidgets import QSizePolicy as _QSP
        dropdown.setSizePolicy(_QSP.Ignored, _QSP.Fixed)
        try:
            dropdown.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            dropdown.setMinimumContentsLength(8)
        except Exception:
            pass
        # Prevent scroll wheel from accidentally changing the selection while
        # the user scrolls through the dock panel (event-filter based; the
        # older instance-attribute wheelEvent patch never fired under PyQt5).
        guard_wheel(dropdown)

        self.update_dropdown_items(dropdown, layer_type)

        def _on_inserted(event):
            self.update_dropdown_items(dropdown, layer_type)
            # Auto-select: if a name_hint was given and the new layer matches,
            # switch to it so the user doesn't have to manually find it.
            if name_hint:
                try:
                    new_name = event.value.name if hasattr(event, 'value') else ''
                    if not new_name:
                        # fallback: check the most-recently added matching layer
                        for layer in reversed(self.viewer.layers):
                            if isinstance(layer, layer_type) and name_hint.lower() in layer.name.lower():
                                new_name = layer.name
                                break
                    if new_name and name_hint.lower() in new_name.lower():
                        idx = dropdown.findText(new_name)
                        if idx != -1:
                            dropdown.setCurrentIndex(idx)
                except Exception:
                    pass

        self.viewer.layers.events.inserted.connect(_on_inserted)
        self.viewer.layers.events.removed.connect(
            lambda e: self.update_dropdown_items(dropdown, layer_type))
        return dropdown

    def update_dropdown_items(self, dropdown, layer_type):
        """
        Updates the items in the dropdown based on the current layers in the viewer that match the specified type.
        Optionally ensures a 'None' option is available in the dropdown.

        Preserves the user's current selection across rebuilds when possible —
        previously the dropdown silently reset to index 0 (the first/oldest
        layer) every time any layer was added or removed anywhere in the
        viewer, discarding the user's actual choice without any visual
        indication. This caused batch config recordings to capture stale
        defaults like "Segmentation Image" instead of the intended
        "Upscaled Segmentation Image" the user had selected.

        Parameters
        ----------
        dropdown : QComboBox
            The dropdown widget to update.
        layer_type : type
            The type of layer to include in the dropdown.
        """
        # Remember what was selected before rebuilding
        previous_selection = dropdown.currentText()

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

        # Restore the previous selection if it still exists among the
        # current layers; only fall back to the default (index 0) if the
        # previously selected layer was actually removed from the viewer.
        restored_index = dropdown.findText(previous_selection)
        if restored_index != -1:
            dropdown.setCurrentIndex(restored_index)

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
        label.setWordWrap(True)
        label.setWordWrap(True)
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
                    name = dropdown.currentText()
                    if name == "None" or not name:
                        layers.append(None)
                    elif name not in [l.name for l in viewer.layers]:
                        from napari.utils.notifications import show_warning as _warn
                        _warn(
                            f"Layer '{name}' not found in viewer. "                            f"The dropdown may be pointing to a layer that was "                            f"removed or renamed. Re-run the previous step or "                            f"select the correct layer from the dropdown.")
                        return
                    else:
                        layers.append(viewer.layers[name])
        else:
            layers = []

        # Filter out the dropdowns, so we don't pass them to the processing function
        non_dropdown_args = [arg for arg in args if not isinstance(arg, QComboBox)]

        # Call the processing function and time it for performance metrics
        import time
        import pandas as pd
        from pycat.data.data_modules import BaseDataClass
        t0 = time.perf_counter()
        try:
            processing_function(*layers, *non_dropdown_args, **kwargs)
        except Exception as _e:
            from napari.utils.notifications import show_warning as _warn
            import traceback as _tb
            _warn(f"Step failed: {type(_e).__name__}: {_e}\n"                  f"See terminal for details.")
            _tb.print_exc()
            return
        elapsed = time.perf_counter() - t0

        # Store timing in data_instance if one is present in the args
        data_instance = next(
            (a for a in non_dropdown_args if isinstance(a, BaseDataClass)), None
        )
        if data_instance is not None:
            step_name = getattr(processing_function, '__name__', str(processing_function))
            image_shape = str(layers[0].data.shape) if layers else ''
            new_row = pd.DataFrame([{
                'step': step_name,
                'elapsed_s': round(elapsed, 4),
                'image_shape': image_shape,
            }])
            if 'timing_df' not in data_instance.data_repository:
                data_instance.data_repository['timing_df'] = new_row
            else:
                data_instance.data_repository['timing_df'] = pd.concat(
                    [data_instance.data_repository['timing_df'], new_row],
                    ignore_index=True
                )
            print(f"[PyCAT Timing] {step_name}: {elapsed:.3f}s")

    def clear_dock(self):
        """
        Removes all dock widgets from the viewer's window.
        """
        # Remove all widgets from the dock. napari 0.7 renamed the private
        # `_dock_widgets` to the public `dock_widgets`; prefer the public API
        # and fall back to the old attribute for older napari versions.
        container = getattr(self.viewer.window, 'dock_widgets', None)
        if container is None:
            container = getattr(self.viewer.window, '_dock_widgets', {})
        try:
            dock_widgets = list(container.values())
        except AttributeError:
            dock_widgets = list(container)
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
            # Base brush size on the SPATIAL extent (last two dims), not shape[0]
            # — for a 3D (T/Z, H, W) mask shape[0] is the frame count, which for
            # a short stack gives 0 and makes napari divide-by-zero (NaN) on the
            # first paint click. Floor at 1 so the brush is always valid.
            spatial = active_layer.data.shape[-2:]
            active_layer.brush_size = max(1, max(spatial) // 150)
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

            # Guard all spin boxes / sliders / combos in this widget against
            # accidental wheel-scroll value changes (covers every toolbox tool
            # that goes through this common docking path).
            try:
                _apply_scroll_guard(main_widget)
            except Exception:
                pass

            # Add the main widget to the viewer as a dock widget
            self.viewer.window.add_dock_widget(main_widget, name=dock_name)
        else:        
            # Add the widget to the existing layout in the dock                    
            layout.addWidget(widget)
            layout.setContentsMargins(1, 1, 1, 1)
            try:
                _apply_scroll_guard(widget)
            except Exception:
                pass


    def _record(self, step_name, params):
        """Record a pipeline step to the BatchProcessor if one is attached."""
        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record(step_name, params)

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
        self._add_run_sacf_analysis = lambda **kw: _add_run_sacf_analysis(self, **kw)
        self._add_run_timeseries_condensate_analysis = lambda **kw: _add_run_timeseries_condensate_analysis(self, **kw)
        self._add_lazy_preprocess_stack = lambda **kw: _add_lazy_preprocess_stack(self, **kw)
        self._add_run_two_channel_coloc = lambda **kw: _add_run_two_channel_coloc(self, **kw)
        self._add_export_timeseries_video = lambda **kw: _add_export_timeseries_video(self, **kw)
        self._add_run_ts_cellpose = lambda **kw: _add_run_ts_cellpose(self, **kw)
        self._add_spatial_metrology = lambda **kw: _add_spatial_metrology(self, **kw)
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
        save_and_clear_button = QPushButton("Save and Clear") # Create a button widget
        def _on_save_and_clear():
            self.on_general_button_clicked(
                self.central_manager.file_io.save_and_clear_all, None, self.viewer)
            # save_and_clear_all records the step internally after dialogs
            # close, capturing the actual layer and dataframe selections made.
        save_and_clear_button.clicked.connect(_on_save_and_clear)
        save_and_clear_layout.addWidget(save_and_clear_button) # Add the button to the layout
        save_and_clear_widget = QWidget()
        save_and_clear_widget.setLayout(save_and_clear_layout)
        self._add_widget_to_layout_or_dock(save_and_clear_widget, layout, separate_widget, "Save and Clear Dock")


    def _add_measure_line(self, layout=None, separate_widget=False):
        """Add a widget for measuring object diameters with drawn lines, optionally in a separate dock."""
        measure_layout = QVBoxLayout() # Create a vertical layout widget
        self.add_text_label(measure_layout, 'Measure Object Diameters', bold=True) # Add widget title label
        measure_button = QPushButton("Measure Line(s)") # Create a button widget
        def _on_measure_line():
            self.on_general_button_clicked(
                self.central_manager.active_data_class.calculate_length, None, self.viewer)
            self._record('measure_line', {
                'object_size': self.central_manager.active_data_class.data_repository.get('object_size'),
                'cell_diameter': self.central_manager.active_data_class.data_repository.get('cell_diameter'),
                'ball_radius': self.central_manager.active_data_class.data_repository.get('ball_radius'),
            })
        measure_button.clicked.connect(_on_measure_line)
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
        pre_process_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        def _on_preprocess():
            # Capture the active layer BEFORE running — the operation adds a
            # new output layer to the viewer which napari may then select
            # as active, making post-hoc capture unreliable.
            active = self.viewer.layers.selection.active
            active_name = active.name if active is not None else ''
            self.on_general_button_clicked(
                run_pre_process_image, None, self.central_manager.active_data_class, self.viewer)
            dr = self.central_manager.active_data_class.data_repository
            self._record('preprocessing', {
                'active_layer': active_name,
                'ball_radius':  int(dr.get('ball_radius', 50)),
                'window_size':  int(dr.get('cell_diameter', 100)) // 2,
            })
        pre_process_button.clicked.connect(_on_preprocess)
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
                    from aicsimageio import AICSImage
                    arr = _np.asarray(AICSImage(p).get_image_data("ZYX", C=0, T=0)).astype(_np.float32)
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
            img = _np.asarray(active.data, dtype=_np.float32)
            if img.shape[-2:] != ref.shape[-2:]:
                _warn(f"Calibration shape {ref.shape} doesn't match image "
                      f"{tuple(img.shape[-2:])} — use a reference from the same acquisition.")
                return
            from pycat.toolbox.image_processing_tools import (
                apply_flatfield_correction, apply_background_subtraction)
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

    def _add_run_enhanced_rb_gaussian_bg_removal(self, layout=None, separate_widget=False):
        """Add a widget for rolling-ball and Gaussian background removal with edge enhancement, optionally in a separate dock."""
        remove_background_layout = QVBoxLayout()
        self.add_text_label(remove_background_layout, 'Enhanced RB-Gauss Background Removal', bold=True) # Add widget title label
        remove_background_button = QPushButton("Remove Background") # Create a button widget
        remove_background_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        def _on_enhanced_bg_removal():
            # Capture BEFORE running — previously this read the active layer
            # AFTER the operation completed, by which point napari had often
            # auto-selected the newly created output layer instead of the
            # input layer that was actually processed.
            active = self.viewer.layers.selection.active
            active_name = active.name if active is not None else ''
            self.on_general_button_clicked(
                run_enhanced_rb_gaussian_bg_removal, None,
                self.central_manager.active_data_class, self.viewer)
            dr = self.central_manager.active_data_class.data_repository
            self._record('background_removal', {
                'active_layer': active_name,
                'ball_radius':  int(dr.get('ball_radius', 50)),
            })
        remove_background_button.clicked.connect(_on_enhanced_bg_removal)
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
        WBNS_noise_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        WBNS_noise_input.setPlaceholderText('1') # Set a default text value
        WBNS_layout.addWidget(WBNS_noise_input) # Add the text input to the layout  
        self.add_text_label(WBNS_layout, 'PSF Size') # Add a text label
        WBNS_psf_input = QLineEdit() # Create a text input
        WBNS_psf_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        wavelet_noise_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        wavelet_noise_input.setPlaceholderText('1') # Set a default text value
        wavelet_layout.addWidget(wavelet_noise_input) # Add the text input to the layout
        self.add_text_label(wavelet_layout, 'PSF Size') # Add a text label
        wavelet_psf_input = QLineEdit() # Create a text input
        wavelet_psf_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        filter_size_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        clahe_clip_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        clahe_clip_input.setPlaceholderText('0.0025') # Set a default text value
        clahe_layout.addWidget(clahe_clip_input) # Add the text input to the layout
        def_window_size = math.ceil(self.central_manager.active_data_class.data_repository['cell_diameter']//4) # Calculate the default window size
        self.add_text_label(clahe_layout, 'Window Size') # Add a text label    
        clahe_window_size_input = QLineEdit() # Create a text input
        clahe_window_size_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        clahe_window_size_input.setPlaceholderText(str(def_window_size)) # Set a default text value
        clahe_layout.addWidget(clahe_window_size_input) # Add the text input to the layout
        clahe_button = QPushButton("Run CLAHE") # Create a button widget
        clahe_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_clahe, None, clahe_clip_input, clahe_window_size_input,  self.viewer))
        clahe_layout.addWidget(clahe_button) # Add the button to the layout
        clahe_widget = QWidget()
        clahe_widget.setLayout(clahe_layout)
        self._add_widget_to_layout_or_dock(clahe_widget, layout, separate_widget, "CLAHE Dock")


    def _add_run_fft_bandpass(self, layout=None, separate_widget=False):
        """FFT bandpass filter — annular frequency mask for background/feature isolation."""
        fft_layout = QVBoxLayout()
        self.add_text_label(fft_layout, 'FFT Bandpass Filter', bold=True)
        self.add_text_label(fft_layout,
            'Annular frequency mask: keeps spatial frequencies between the '
            'inner and outer radii. Removes low-frequency background and '
            'high-frequency noise. Works on a 2D image or a whole stack.')
        self.add_text_label(fft_layout, 'Inner radius (low cutoff, px)')
        fft_low_input = QLineEdit(); fft_low_input.setPlaceholderText('3')
        fft_low_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        fft_layout.addWidget(fft_low_input)
        self.add_text_label(fft_layout, 'Outer radius (high cutoff, px)')
        fft_high_input = QLineEdit(); fft_high_input.setPlaceholderText('40')
        fft_high_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        fft_layout.addWidget(fft_high_input)
        fft_button = QPushButton("Run FFT Bandpass")
        fft_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_fft_bandpass, None, fft_low_input, fft_high_input, self.viewer))
        fft_layout.addWidget(fft_button)
        fft_widget = QWidget(); fft_widget.setLayout(fft_layout)
        self._add_widget_to_layout_or_dock(fft_widget, layout, separate_widget, "FFT Bandpass Dock")

    def _add_run_im2bw(self, layout=None, separate_widget=False):
        """MATLAB-style manual threshold binarization (absolute intensity cutoff)."""
        bw_layout = QVBoxLayout()
        self.add_text_label(bw_layout, 'Manual Threshold (im2bw)', bold=True)
        self.add_text_label(bw_layout,
            'Binarize on an absolute intensity value you supply (pixels ≥ '
            'threshold → 1). Unlike Otsu/Li, the level is not auto-chosen.')
        self.add_text_label(bw_layout, 'Threshold value')
        bw_input = QLineEdit(); bw_input.setPlaceholderText('e.g. 0.5 or 128')
        bw_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        bw_layout.addWidget(bw_input)
        bw_button = QPushButton("Binarize")
        bw_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_im2bw, None, bw_input, self.viewer))
        bw_layout.addWidget(bw_button)
        bw_widget = QWidget(); bw_widget.setLayout(bw_layout)
        self._add_widget_to_layout_or_dock(bw_widget, layout, separate_widget, "Manual Threshold Dock")

    def _add_run_best_slice(self, layout=None, separate_widget=False):
        """Extract the best (most-informative/sharpest) slice of a Z/T-stack."""
        bs_layout = QVBoxLayout()
        self.add_text_label(bs_layout, 'Best Slice Selector', bold=True)
        self.add_text_label(bs_layout,
            'Reduce a Z- or T-stack to a single representative 2D plane — the '
            'most informative slice (max std) or the sharpest (Brenner / '
            'Tenengrad). Useful before 2D segmentation of a nuclear/DAPI stack.')
        self.add_text_label(bs_layout, 'Selection metric')
        bs_method = QComboBox(); bs_method.addItems(['std', 'brenner', 'tenengrad'])
        bs_method.setToolTip(
            "std: maximum intensity spread (max-information plane).\n"
            "brenner: sharpest focus (Brenner gradient).\n"
            "tenengrad: sharpest edges (Sobel gradient magnitude).")
        bs_layout.addWidget(bs_method)
        bs_button = QPushButton("Extract Best Slice")
        bs_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        bs_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_best_slice, None, bs_method, self.viewer))
        bs_layout.addWidget(bs_button)
        bs_widget = QWidget(); bs_widget.setLayout(bs_layout)
        self._add_widget_to_layout_or_dock(bs_widget, layout, separate_widget, "Best Slice Dock")

    def _add_run_peak_and_edge_enhancement(self, layout=None, separate_widget=False):
        """Add a widget for peak and edge enhancement, optionally in a separate dock."""
        enhancement_layout = QVBoxLayout()
        self.add_text_label(enhancement_layout, 'Peak and Edge Enhancement', bold=True) # Add widget title label
        enhancement_button = QPushButton("Run Edge Enhancement") # Create a button widget
        enhancement_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        filter_size_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        DPR_gain_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        DPR_layout.addWidget(DPR_gain_input) # Add the text input to the layout
        self.add_text_label(DPR_layout, 'PSF Size') # Add a text label
        DPR_psf_input = QLineEdit() # Create a text input
        DPR_psf_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        LoG_sigma_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        self.add_text_label(seg_layout, 'Select image layer:')
        image_dropdown = self.create_layer_dropdown(napari.layers.Image)
        seg_layout.addWidget(image_dropdown)

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
        k_slider = QSlider(Qt.Horizontal) # Create a slider widget
        guard_wheel(k_slider)
        k_slider.setRange(0, 100)  # 100 steps from 0 to 100
        k_slider.setValue(50)  # default is 0
        k_slider.setSingleStep(1)  # Adjust for 0.01 steps
        k_label_value = QLabel("0.0") 
        k_label_value.setWordWrap(True)
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
        window_slider = QSlider(Qt.Horizontal) # Create a slider widget
        guard_wheel(window_slider)
        window_slider.setRange(10, 250) # 100 steps from 10 to 250
        window_slider.setValue(def_window_size) # Set the default value
        window_label_value = QLabel(str(def_window_size)) # Set the default value
        window_label_value.setWordWrap(True)
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
        self.add_text_label(process_cells_layout, 'Select Pre-Processed Image to Segment')
        process_cells_image1_dropdown = self.create_layer_dropdown(napari.layers.Image, name_hint='Pre-Processed')
        process_cells_layout.addWidget(process_cells_image1_dropdown)
        self.add_text_label(process_cells_layout, 'Select Fluorescence Image to Process')
        process_cells_image2_dropdown = self.create_layer_dropdown(napari.layers.Image)
        process_cells_layout.addWidget(process_cells_image2_dropdown)

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
        process_cells_layout.addWidget(process_cells_button)
        process_cells_widget = QWidget()
        process_cells_widget.setLayout(process_cells_layout)
        self._add_widget_to_layout_or_dock(process_cells_widget, layout, separate_widget, "Condensate Segmentation Dock")


    #### Image Feature Analysis Functions ####


    def _add_run_cell_analysis_func(self, layout=None, separate_widget=False):
        """Add a widget for cell analysis, optionally in a separate dock."""
        cell_segmentation_layout = QVBoxLayout()
        self.add_text_label(cell_segmentation_layout, 'Cell/Nuclei Analysis', bold=True) # Add widget title label
        self.add_text_label(cell_segmentation_layout, 'Select Mask Layer for Cell Analysis') # Add a text label
        cell_segmentation_dropdown_labels = self.create_layer_dropdown(napari.layers.Labels, name_hint='Labeled Cell Mask')
        cell_segmentation_layout.addWidget(cell_segmentation_dropdown_labels) # Add the dropdown to the layout
        self.add_text_label(cell_segmentation_layout, 'Select Mask Layer to Omit') # Add a text label
        cell_segmentation_dropdown_omit = self.create_layer_dropdown(napari.layers.Labels)
        cell_segmentation_dropdown_omit.insertItem(0, "None")
        cell_segmentation_layout.addWidget(cell_segmentation_dropdown_omit)
        self.add_text_label(cell_segmentation_layout, 'Select Image for Cell Analysis') # Add a text label
        cell_segmentation_dropdown_images = self.create_layer_dropdown(napari.layers.Image)
        cell_segmentation_layout.addWidget(cell_segmentation_dropdown_images)
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
        cell_segmentation_layout.addWidget(cell_analysis_button) # Add the button to the layout
        cell_segmentation_widget = QWidget()
        cell_segmentation_widget.setLayout(cell_segmentation_layout)
        self._add_widget_to_layout_or_dock(cell_segmentation_widget, layout, separate_widget, "Cell Analysis Dock")


    def _add_run_puncta_analysis_func(self, layout=None, separate_widget=False):
        """Add a widget for puncta analysis, optionally in a separate dock."""
        measure_puncta_layout = QVBoxLayout()
        self.add_text_label(measure_puncta_layout, 'Condensate Analysis', bold=True) # Add widget title label
        self.add_text_label(measure_puncta_layout, 'Select Puncta Mask for Measurement') # Add a text label
        puncta_measure_dropdown_labels = self.create_layer_dropdown(napari.layers.Labels, name_hint='Refined Puncta')
        measure_puncta_layout.addWidget(puncta_measure_dropdown_labels)
        self.add_text_label(measure_puncta_layout, 'Select Image for Puncta Measurement')
        puncta_measure_dropdown_images = self.create_layer_dropdown(napari.layers.Image)
        measure_puncta_layout.addWidget(puncta_measure_dropdown_images) # Add the dropdown to the layout
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
        alpha_blend_slider = QSlider(Qt.Horizontal) # Create a slider widget
        guard_wheel(alpha_blend_slider)
        alpha_blend_slider.setRange(0, 10)  # 100 steps from 0 to 100
        alpha_blend_slider.setValue(5)  # default is 0.5
        alpha_blend_slider.setSingleStep(1)  # Adjust for 0.01 steps
        slider_label_value = QLabel("0.5") 
        slider_label_value.setWordWrap(True)
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

    def _switch_to_invitro_fluor_analysis(self, *args, **kwargs):
        """Switch to the in vitro fluorescence condensate analysis pipeline."""
        self._switch_analysis(BaseDataClass, InVitroFluorUI, *args, **kwargs)

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

        # Add analysis and processing steps to the layout
        # Each method call adds a specific UI component for condensate analysis
        self.central_manager.toolbox_functions_ui._add_measure_line(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_run_upscaling(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_pre_process(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_run_enhanced_rb_gaussian_bg_removal(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_run_cellpose_segmentation(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_run_cell_analysis_func(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_run_segment_subcellular_objects(layout=self.condensate_layout)
        self.central_manager.toolbox_functions_ui._add_run_puncta_analysis_func(layout=self.condensate_layout)

        # ── Spatial Metrology ───────────────────────────────────────────────
        self.central_manager.toolbox_functions_ui._add_spatial_metrology(
            layout=self.condensate_layout)

        # ── Advanced Analysis (Morphological / Dynamic / Organizational) ──
        self.central_manager.toolbox_functions_ui._add_advanced_analysis(
            layout=self.condensate_layout)

        # ── Condensate Biophysics (MSD, Csat, kinetics, QC) ─────────────
        self.central_manager.toolbox_functions_ui._add_condensate_physics(
            layout=self.condensate_layout)

        self.central_manager.toolbox_functions_ui._add_save_and_clear(layout=self.condensate_layout)
        # ... Add other components in the order you want ...

        # Create a main widget and assign the vertical layout to it
        main_widget = QWidget()
        main_widget.setLayout(self.condensate_layout)

        # Create a scroll area to enable scrolling for the UI components
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)  # Make the scroll area resizable
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setWidget(main_widget)  # Set the main widget as the scroll area's content

        # Add the scroll area to the viewer as a dockable widget for condensate analysis
        self.viewer.window.add_dock_widget(scroll_area, name="Condensate Analysis Dock")

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

        # ── Step 1: instruction label ─────────────────────────────────────
        from PyQt5.QtWidgets import QLabel, QFrame
        instr = QLabel(
            "<b>Step 1:</b> Load your time-series via<br>"
            "<i>★ Open/Save File(s) → Open Image Stack (T/Z / IMS)</i>"
        )
        instr.setWordWrap(True)
        instr.setStyleSheet("padding: 6px; background: #2a2a2a; border-radius: 4px;")
        self.ts_layout.addWidget(instr)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        self.ts_layout.addWidget(sep)

        # ── Step 2: Reference frame selector ─────────────────────────────
        self._add_reference_frame_selector(self.ts_layout)

        # ── Steps 3-4: measurement lines and lazy stack preprocessing ──────
        # Lazy preprocessing builds dask-backed layers so frames are
        # processed one at a time on demand rather than all upfront.
        tfu._add_measure_line(layout=self.ts_layout)
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
                                      QPushButton, QCheckBox, QLabel)
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
        roi_grp_layout.setContentsMargins(4, 8, 4, 4)

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
            # Reuse existing layer if already present
            if roi_name not in [l.name for l in self.viewer.layers]:
                import napari.layers as _nl
                roi_layer = self.viewer.add_shapes(
                    name=roi_name,
                    shape_type='rectangle',
                    face_color='transparent',
                    edge_color='#f0a500',
                    edge_width=3,
                )
            else:
                roi_layer = self.viewer.layers[roi_name]
            self.viewer.layers.selection.active = roi_layer
            self.viewer.layers.selection.active.mode = 'add_rectangle'
            # Update the shapes dropdown to reflect the new layer
            self.central_manager.toolbox_functions_ui.update_dropdown_items(
                roi_shapes_dd, napari.layers.Shapes)
            idx = roi_shapes_dd.findText(roi_name)
            if idx != -1:
                roi_shapes_dd.setCurrentIndex(idx)

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
        self.central_manager.toolbox_functions_ui._add_measure_line(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_upscaling(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_pre_process(layout=self.object_coloc_layout)
        self.central_manager.toolbox_functions_ui._add_run_enhanced_rb_gaussian_bg_removal(layout=self.object_coloc_layout)
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
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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

    def _home_fit_view(self):
        """
        Fit the camera to the selected Image / Labels / Shapes (ROI) layer.
        For an arbitrary Points/line selection (or nothing selected), show a
        brief notice and do nothing.
        """
        import numpy as np
        from napari.utils.notifications import show_info as _info
        layer = self.viewer.layers.selection.active
        if layer is None:
            _info("Select an image or ROI layer, then press Home.")
            return
        fittable = isinstance(
            layer, (napari.layers.Image, napari.layers.Labels, napari.layers.Shapes))
        if not fittable:
            _info(f"'{layer.name}' isn't an image or ROI — nothing to fit to.")
            return
        try:
            ext = np.asarray(layer.extent.world)      # (2, ndim): [mins, maxs]
            mins, maxs = ext[0], ext[1]
            nd = self.viewer.dims.ndisplay
            dims = list(self.viewer.dims.displayed)[-nd:]
            center = (mins + maxs) / 2.0
            self.viewer.camera.center = tuple(float(center[d]) for d in dims)

            # Zoom to fit: need the canvas size in pixels.
            cw = ch = None
            for accessor in ('qt_viewer', '_qt_viewer'):
                try:
                    sz = getattr(self.viewer.window, accessor).canvas.size
                    cw, ch = float(sz[0]), float(sz[1])
                    break
                except Exception:
                    continue
            sizes = [float(maxs[d] - mins[d]) for d in dims]
            if nd == 2 and cw and ch and all(s > 0 for s in sizes):
                # displayed dims are [y, x]; canvas is (width=x, height=y)
                zoom = min(ch / sizes[0], cw / sizes[1]) * 0.9   # 10% margin
                self.viewer.camera.zoom = zoom
            else:
                # Couldn't compute a fit zoom — at least re-center via reset.
                self.viewer.reset_view()
        except Exception:
            try:
                self.viewer.reset_view()
            except Exception:
                pass

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
        self.file_menu = self.viewer.window._qt_window.menuBar().addMenu('★ Open/Save File(s)')
        self._add_file_io_methods_to_menu()

        # Clear button directly on the menu bar, next to Open/Save, with a
        # hazard icon. Resets the workspace to the workflow start WITHOUT saving.
        self.clear_action = QAction('\u2620 Clear', self.viewer.window._qt_window)
        self.clear_action.setToolTip(
            'Clear all layers and data WITHOUT saving — resets to the start of a workflow.')
        self.clear_action.triggered.connect(
            lambda: self.central_manager.file_io.clear_all_without_saving(self.viewer, confirm=True))
        self.viewer.window._qt_window.menuBar().addAction(self.clear_action)

        # Home / fill-view button: refit the camera to the selected layer if it
        # is an Image or ROI (Shapes/Labels); does nothing for an arbitrary
        # line/points selection. Useful when the image scrolls out of view.
        self.home_action = QAction('\u2302 Home', self.viewer.window._qt_window)
        self.home_action.setToolTip(
            'Fit the view to the selected image or ROI layer.')
        self.home_action.triggered.connect(self._home_fit_view)
        self.viewer.window._qt_window.menuBar().addAction(self.home_action)

        # Route files dropped onto the napari window through PyCAT's openers
        # (napari's default drop bypasses PyCAT's channel-assignment pipeline).
        try:
            from PyQt5.QtWidgets import QApplication
            self._pycat_drop_filter = _FileDropFilter(self.central_manager.file_io)
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(self._pycat_drop_filter)
            self.viewer.window._qt_window.setAcceptDrops(True)
        except Exception as _e:
            print(f"[PyCAT] Could not install file-drop handler: {_e}")

    def _open_session_loader(self):
        """Open a folder browser to select a PyCAT output directory and reload."""
        from PyQt5.QtWidgets import (QFileDialog, QDialog, QVBoxLayout,
                                      QListWidget, QPushButton, QLabel,
                                      QHBoxLayout, QCheckBox, QProgressBar,
                                      QAbstractItemView)
        from pathlib import Path
        from napari.utils.notifications import (
            show_info as napari_show_info,
            show_warning as napari_show_warning,
        )
        from pycat.file_io.session_loader import scan_output_folder, load_session

        folder = QFileDialog.getExistingDirectory(
            None, "Select PyCAT Output Folder", "",
            QFileDialog.ShowDirsOnly
        )
        if not folder:
            return
        folder = Path(folder)

        groups = scan_output_folder(folder)
        if not groups:
            napari_show_warning(
                f"No recognised PyCAT outputs found in {folder.name}.\n"
                "Expected files like *_preprocessed.tiff, *_cell_df.csv, etc."
            )
            return

        dlg = QDialog()
        dlg.setWindowTitle(f"Load Session — {folder.name}")
        dlg.setMinimumWidth(520)
        dlg.setMinimumHeight(480)
        vl = QVBoxLayout(dlg)

        n_files = sum(len(v) for v in groups.values())
        vl.addWidget(QLabel(
            f"Found {n_files} PyCAT output file(s) from "
            f"{len(groups)} image stem(s) in:\n{folder}"
        ))

        group_list = QListWidget()
        group_list.setSelectionMode(QAbstractItemView.MultiSelection)
        for stem, files in sorted(groups.items()):
            n_img = sum(1 for f in files if f["layer_type"] == "image")
            n_lbl = sum(1 for f in files if f["layer_type"] == "labels")
            n_df  = sum(1 for f in files if f["layer_type"] == "dataframe")
            group_list.addItem(
                f"{stem}  —  {n_img} image(s), {n_lbl} label(s), {n_df} table(s)"
            )
        group_list.selectAll()
        vl.addWidget(group_list)

        prog_bar    = QProgressBar(); prog_bar.setVisible(False)
        status_lbl  = QLabel("")
        status_lbl.setWordWrap(True)
        vl.addWidget(prog_bar)
        vl.addWidget(status_lbl)

        btn_row    = QHBoxLayout()
        load_btn   = QPushButton("Load Selected")
        cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(load_btn); btn_row.addWidget(cancel_btn)
        vl.addLayout(btn_row)

        cancel_btn.clicked.connect(dlg.reject)

        def _on_load():
            selected_stems = {
                item.text().split("  —  ")[0].strip()
                for item in group_list.selectedItems()
            }
            if not selected_stems:
                napari_show_warning("No images selected.")
                return

            all_files = [
                info for stem, files in groups.items()
                if stem in selected_stems
                for info in files
            ]
            n = len(all_files)
            prog_bar.setMaximum(n); prog_bar.setValue(0)
            prog_bar.setVisible(True); load_btn.setEnabled(False)

            data_instance = self.central_manager.active_data_class

            def _prog(done, total):
                prog_bar.setValue(done)
                status_lbl.setText(f"Loading {done}/{total}…")

            result = load_session(
                folder, self.central_manager.viewer,
                data_instance, progress_callback=_prog,
            )

            prog_bar.setVisible(False); load_btn.setEnabled(True)
            n_layers = len(result["loaded_layers"])
            n_dfs    = len(result["loaded_dfs"])
            n_skip   = len(result["skipped"])
            status_lbl.setText(
                f"Loaded {n_layers} layer(s), {n_dfs} table(s)"
                + (f", {n_skip} skipped." if n_skip else ".")
            )
            napari_show_info(
                f"Session reloaded: {n_layers} layers, {n_dfs} DataFrames"
                + (f" ({n_skip} skipped — see terminal)." if n_skip else ".")
            )
            for p, reason in result["skipped"]:
                print(f"[PyCAT Session] Skipped {p.name}: {reason}")

        load_btn.clicked.connect(_on_load)
        dlg.exec_()

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
                'Open Image Stack (T/Z / IMS)': (self.central_manager.file_io.open_stack, {}),
                'Load Previous Session Results': (self._open_session_loader, {}),
                # IMS files are now handled by the unified Open Stack menu item above
                'Open 2D Mask(s)': (self.central_manager.file_io.open_2d_mask, {}),
                'Save and Clear': (self.central_manager.file_io.save_and_clear_all, {'viewer': self.viewer})
            }
            self._add_actions_to_menu(file_io_methods_dict, self.file_menu)

    # Add specific analysis methods as actions to the analysis methods menu.
    def _add_analysis_methods_to_menu(self):
        """
        Add specific analysis methods as actions to the analysis methods menu. 
        """
        # Imaging/morphometric pipelines — agnostic to whether the system has a
        # membrane (cellular or in vitro), hence "Condensate & Cell Analysis".
        condensate_cell_analysis_submenu = self.analysis_methods_menu.addMenu('Condensate & Cell Analysis')
        condensate_cell_analysis_dict = {
            'Cellular Condensate Analysis (Fluorescence)': (self.central_manager.analysis_methods_ui._switch_to_condensate_analysis, {'base_data_repository': self.central_manager.active_data_class.data_repository}),
            'In Vitro Condensate Analysis (Fluorescence)': (self.central_manager.analysis_methods_ui._switch_to_invitro_fluor_analysis, {}),
            'In Vitro Condensate Analysis (Brightfield)': (self.central_manager.analysis_methods_ui._switch_to_invitro_bf_analysis, {}),
            'Time-Series Condensate Analysis': (self.central_manager.analysis_methods_ui._switch_to_timeseries_analysis, {'base_data_repository': self.central_manager.active_data_class.data_repository}),
            'Z-Stack (3D) Condensate Analysis': (self.central_manager.analysis_methods_ui._switch_to_zstack_analysis, {}),
        }
        self._add_actions_to_menu(condensate_cell_analysis_dict, condensate_cell_analysis_submenu)

        # Biophysics pipelines — dynamics, material properties, and single-tether
        # force measurements.
        biophysics_submenu = self.analysis_methods_menu.addMenu('Biophysics')
        biophysics_dict = {
            'Video Particle Tracking (Microrheology)': (self.central_manager.analysis_methods_ui._switch_to_vpt_analysis, {}),
            'FRAP (Fluorescence Recovery)': (self.central_manager.analysis_methods_ui._switch_to_frap_analysis, {}),
            'Droplet Fusion (C-Trap)': (self.central_manager.analysis_methods_ui._switch_to_fusion_analysis, {}),
            'Temperature-Dependent Microscopy': (self.central_manager.analysis_methods_ui._switch_to_temperature_analysis, {}),
            'Force-Distance Curve (DNA Tethering)': (self.central_manager.analysis_methods_ui._switch_to_fd_curve_analysis, {}),
        }
        self._add_actions_to_menu(biophysics_dict, biophysics_submenu)

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
            'Calibration Correction (flat-field / clear-frame)': (self.central_manager.toolbox_functions_ui._add_run_calibration_correction, {'separate_widget': True}),
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
            'Deblur by Pixel Reassignment': (self.central_manager.toolbox_functions_ui._add_run_dpr, {'separate_widget': True}),
            'FFT Bandpass Filter': (self.central_manager.toolbox_functions_ui._add_run_fft_bandpass, {'separate_widget': True}),
        }
        self._add_actions_to_menu(enhancements_and_filters_actions, enhancements_and_filters_submenu)

        # Create a sub-menu for segmentation functions
        image_segmentation_submenu = self.toolbox_menu.addMenu('Image Segmentation')
        image_segmentation_actions = {
            'Local Thresholding': (self.central_manager.toolbox_functions_ui._add_run_local_thresholding, {'separate_widget': True}),
            'Manual Threshold (im2bw)': (self.central_manager.toolbox_functions_ui._add_run_im2bw, {'separate_widget': True}),
            'Cellpose Segmentation': (self.central_manager.toolbox_functions_ui._add_run_cellpose_segmentation, {'separate_widget': True}),
            'Felzenszwalb Segmentation and Region Merging': (self.central_manager.toolbox_functions_ui._add_run_fz_segmentation_and_merging, {'separate_widget': True}),
            'Gaussian Spot Localization': (self.central_manager.toolbox_functions_ui._add_gaussian_localization, {'separate_widget': True}),
            'Contrast Cascade (bright body + dim fibers)': (self.central_manager.toolbox_functions_ui._add_contrast_cascade, {'separate_widget': True})
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
            'Auto-Correlation Function Analysis': (self.central_manager.toolbox_functions_ui._add_run_autocorrelation_analysis, {'separate_widget': True}),
            'Client Partition / Enrichment': (self.central_manager.toolbox_functions_ui._add_client_enrichment, {'separate_widget': True})
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

        # ── Condensate & Cell Analysis ─────────────────────────────────────────
        condensate_analysis_submenu = self.toolbox_menu.addMenu('Condensate & Cell Analysis')
        condensate_analysis_actions = {
            'Cell Analyzer': (self.central_manager.toolbox_functions_ui._add_run_cell_analysis_func, {'separate_widget': True}),
            'Condensate Segmentation': (self.central_manager.toolbox_functions_ui._add_run_segment_subcellular_objects, {'separate_widget': True}),
            'Condensate Analyzer': (self.central_manager.toolbox_functions_ui._add_run_puncta_analysis_func, {'separate_widget': True}),
        }
        self._add_actions_to_menu(condensate_analysis_actions, condensate_analysis_submenu)

        # ── Spatial Metrology ──────────────────────────────────────────────────
        spatial_metrology_submenu = self.toolbox_menu.addMenu('Spatial Metrology')
        spatial_metrology_actions = {
            'Per-Cell Spatial ACF Analysis': (self.central_manager.toolbox_functions_ui._add_run_sacf_analysis, {'separate_widget': True}),
            'Spatial Metrology (NND, Ripley, Voronoi…)': (self.central_manager.toolbox_functions_ui._add_spatial_metrology, {'separate_widget': True}),
            'Spatial Randomness (noise vs. clustering)': (self.central_manager.toolbox_functions_ui._add_spatial_randomness, {'separate_widget': True}),
            'Intensity Profiles (line / radial)': (self.central_manager.toolbox_functions_ui._add_intensity_profile, {'separate_widget': True}),
            'Morphological Complexity (fractal, lacunarity…)': (self.central_manager.toolbox_functions_ui._add_morphological_complexity, {'separate_widget': True}),
        }
        self._add_actions_to_menu(spatial_metrology_actions, spatial_metrology_submenu)

        # ── Advanced Analysis ──────────────────────────────────────────────────
        advanced_analysis_submenu = self.toolbox_menu.addMenu('Advanced Analysis')
        advanced_analysis_actions = {
            'Dynamic Spatial Phenotyping / Tracking': (self.central_manager.toolbox_functions_ui._add_advanced_analysis, {'separate_widget': True}),
            'Condensate Biophysics (MSD, C_sat, Kinetics…)': (self.central_manager.toolbox_functions_ui._add_condensate_physics, {'separate_widget': True}),
            'Molecular Counting (Photobleaching)': (self.central_manager.toolbox_functions_ui._add_molecular_counting, {'separate_widget': True}),
        }
        self._add_actions_to_menu(advanced_analysis_actions, advanced_analysis_submenu)

        # ── Brightfield Tools ──────────────────────────────────────────────────
        brightfield_submenu = self.toolbox_menu.addMenu('Brightfield Tools')
        brightfield_actions = {
            'BF Preprocessing (flat-field, halo, CLAHE)': (self.central_manager.toolbox_functions_ui._add_bf_preprocessing, {'separate_widget': True}),
            'BF Condensate Segmentation': (self.central_manager.toolbox_functions_ui._add_bf_condensate_segmentation, {'separate_widget': True}),
            'BF Optical Density Metrics': (self.central_manager.toolbox_functions_ui._add_bf_od_metrics, {'separate_widget': True}),
            'BF Per-Cell Summary': (self.central_manager.toolbox_functions_ui._add_bf_per_cell_summary, {'separate_widget': True}),
            'BF Spatial Metrology': (self.central_manager.toolbox_functions_ui._add_bf_spatial, {'separate_widget': True}),
            'BF Dynamics': (self.central_manager.toolbox_functions_ui._add_bf_dynamics, {'separate_widget': True}),
            'BF Texture Analysis': (self.central_manager.toolbox_functions_ui._add_bf_texture, {'separate_widget': True}),
            'BF Frame Quality': (self.central_manager.toolbox_functions_ui._add_bf_frame_qc, {'separate_widget': True}),
        }
        self._add_actions_to_menu(brightfield_actions, brightfield_submenu)

        # ── Z-Stack (3D) Tools ─────────────────────────────────────────────────
        zstack_submenu = self.toolbox_menu.addMenu('Z-Stack (3D) Tools')
        zstack_actions = {
            '3D Background Removal': (self.central_manager.toolbox_functions_ui._add_zstack_bg_removal, {'separate_widget': True}),
            '3D Cell Segmentation': (self.central_manager.toolbox_functions_ui._add_zstack_cell_seg, {'separate_widget': True}),
            '3D Condensate Segmentation': (self.central_manager.toolbox_functions_ui._add_zstack_condensate_seg, {'separate_widget': True}),
            '3D Condensate Metrics': (self.central_manager.toolbox_functions_ui._add_zstack_metrics, {'separate_widget': True}),
            'Best Slice Selector': (self.central_manager.toolbox_functions_ui._add_run_best_slice, {'separate_widget': True}),
        }
        self._add_actions_to_menu(zstack_actions, zstack_submenu)

        # ── Data Visualization ─────────────────────────────────────────────────
        data_visualization_submenu = self.toolbox_menu.addMenu('Data Visualization')
        data_visualization_actions = {
            'Plotting Widget': (self.central_manager.toolbox_functions_ui._add_plotting_widget, {'separate_widget': True}),
            'Data Quality Control': (self.central_manager.toolbox_functions_ui._add_data_qc, {'separate_widget': True})
        }
        self._add_actions_to_menu(data_visualization_actions, data_visualization_submenu)
