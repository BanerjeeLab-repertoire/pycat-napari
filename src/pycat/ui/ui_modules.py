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
    QLineEdit, QWidget, QComboBox, QSlider, QScrollArea, QSizePolicy, QAction,
    QTabWidget)
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


def _relax_min_widths(widget):
    """
    Recursively relax minimum widths so a dock's content can shrink to the dock
    width instead of being clipped when the horizontal scrollbar is disabled.

    Buttons, combo boxes, line edits and labels with long text otherwise report a
    wide minimum-size hint, forcing the row wider than the dock and pushing
    controls off the right edge. Setting a small minimum width and allowing labels
    to elide/wrap lets the layout compress gracefully. Call once on the root widget
    of any dock that lives inside a horizontal-scroll-disabled QScrollArea.
    """
    from PyQt5.QtWidgets import (QPushButton, QComboBox as _QCB, QLineEdit,
                                  QLabel as _QLbl)
    for w in widget.findChildren((QPushButton, _QCB, QLineEdit)):
        try:
            w.setMinimumWidth(0)
            # Preferred (not Ignored): respects the size hint when there is room,
            # but allows shrinking below it when the dock is narrow, rather than
            # forcing the row wider than the dock and clipping.
            sp = w.sizePolicy()
            sp.setHorizontalPolicy(QSizePolicy.Preferred)
            w.setSizePolicy(sp)
        except Exception:
            pass
    for lbl in widget.findChildren(_QLbl):
        try:
            lbl.setMinimumWidth(0)
            lbl.setWordWrap(True)  # wrap long labels instead of forcing width
        except Exception:
            pass


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

    def _hint_matches(self, name_hint, layer_name):
        """Return True if layer_name is the intended target of name_hint.

        Plain substring matching is wrong: a hint of 'Upscaled Fluorescence'
        substring-matches derived layers like 'Pre-Processed Upscaled
        Fluorescence Image' or 'Enhanced Background Removed Upscaled Fluorescence
        Image', causing a derived layer to auto-populate a dropdown that wants the
        plain upscaled image. We reject a match when the layer name carries an
        EXTRA leading modifier prefix that the hint does not — i.e. the layer is a
        more-derived version than the dropdown asked for. A hint that itself names
        the modifier still matches (e.g. hint 'Pre-Processed' matches the
        pre-processed layer).
        """
        if not name_hint:
            return False
        hl = name_hint.lower().strip()
        nl = layer_name.lower().strip()
        if hl not in nl:
            return False
        # Leading modifier prefixes that mark a DERIVED layer. Any of these at the
        # START of a layer name means the layer is a processed derivative; if the
        # hint doesn't itself mention the modifier, that layer is not what the
        # dropdown asked for. Longest-first so multi-word prefixes match.
        _modifiers = (
            'enhanced background removed',
            'background removed',
            'pre-processed',
            'preprocessed',
        )
        for mod in _modifiers:
            # If the layer name STARTS with this modifier but the hint neither
            # starts with nor contains it, the layer is a derived version the
            # dropdown didn't ask for — reject.
            if nl.startswith(mod) and mod not in hl:
                return False
        return True

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
            # Performance: only rebuild if the inserted layer is of this
            # dropdown's type. Adding one Shapes ROI layer would otherwise
            # trigger a full rebuild of EVERY dropdown in the dock (dozens on the
            # time-series/condensate docks), each looping all layers — the cause
            # of the multi-second lag when clicking "Add ROI Drawing Layer" on a
            # large lazy IMS stack.
            try:
                inserted_layer = getattr(event, 'value', None)
                if inserted_layer is not None and not isinstance(inserted_layer, layer_type):
                    return
            except Exception:
                pass
            try:
                self.update_dropdown_items(dropdown, layer_type)
            except RuntimeError:
                return  # dropdown deleted
            # Auto-select: if a name_hint was given and the new layer matches,
            # switch to it so the user doesn't have to manually find it.
            if name_hint:
                try:
                    new_name = event.value.name if hasattr(event, 'value') else ''
                    if not new_name or not self._hint_matches(name_hint, new_name):
                        # fallback: most-recently added layer that truly matches
                        new_name = ''
                        for layer in reversed(self.viewer.layers):
                            if isinstance(layer, layer_type) and self._hint_matches(name_hint, layer.name):
                                new_name = layer.name
                                break
                    if new_name and self._hint_matches(name_hint, new_name):
                        idx = dropdown.findText(new_name)
                        if idx != -1:
                            dropdown.setCurrentIndex(idx)
                except RuntimeError:
                    return
                except Exception:
                    pass

        self.viewer.layers.events.inserted.connect(_on_inserted)
        _removed_handler = lambda e: self.update_dropdown_items(dropdown, layer_type)
        self.viewer.layers.events.removed.connect(_removed_handler)

        # Disconnect both viewer-level handlers when the dropdown is destroyed,
        # so a later insert/remove doesn't fire a callback that touches a deleted
        # QComboBox (RuntimeError: wrapped C/C++ object has been deleted).
        def _disconnect(*_):
            for _sig, _h in ((self.viewer.layers.events.inserted, _on_inserted),
                             (self.viewer.layers.events.removed, _removed_handler)):
                try:
                    _sig.disconnect(_h)
                except Exception:
                    pass
        try:
            dropdown.destroyed.connect(_disconnect)
        except Exception:
            pass
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
        # Remember what was selected before rebuilding. If the dropdown's C++
        # object has already been deleted (its parent workflow was torn down but
        # a viewer-level layer signal still references it), bail out silently.
        try:
            previous_selection = dropdown.currentText()
        except RuntimeError:
            return

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

    def _consume_step_label(self):
        """Return the staged 'Step N — ' prefix (and clear it), or '' if none.
        For builders that render their title via QGroupBox(...) or a bare button
        rather than add_text_label(bold=True); they can prepend the returned
        string to their title so _stage_step works uniformly across mechanisms."""
        pending = getattr(self, '_pending_step_label', None)
        if pending:
            self._pending_step_label = None
            return pending
        return ''

    def _stage_step(self, step_label):
        """Stage a 'Step N — ' prefix to be prepended to the next shared widget
        builder's bold title. Set on the toolbox_functions_ui instance, since that
        is the object whose _add_* builders render the titles. No-op if the
        toolbox UI isn't available yet."""
        try:
            tfu = self.central_manager.toolbox_functions_ui
            tfu._pending_step_label = step_label
        except Exception:
            pass

    def add_text_label(self, layout, text, font_size=10, bold=False):
        """
        Adds a text label above a dropdown widget in the given layout, with an option to make the text bold.

        If a step label has been staged via ``self._pending_step_label`` (set by a
        workflow just before calling a shared widget builder), it is prepended to
        the FIRST bold label rendered and then cleared — this is how the built-in
        workflows enumerate shared widgets ("Step 4 — Pre-process image") without
        hardcoding a number into the reusable builder, since the same builder
        appears at different step numbers in different pipelines.

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
        stepped = False
        if bold:
            pending = getattr(self, '_pending_step_label', None)
            if pending:
                # Render the "Step N — " prefix in a stronger emphasis than the
                # title, and use rich text so the two weights show. The stepped
                # section titles also get a larger font so they read as primary
                # section headers (matching the Step 1 block), not sub-labels.
                prefix = pending.strip()
                # normalise trailing dash/spacing for consistent rendering
                title = text
                text = (f"<span style='font-weight:800;'>{prefix}</span> "
                        f"<span style='font-weight:600;'>{title}</span>")
                stepped = True
                self._pending_step_label = None
        label = QLabel(text)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        if stepped:
            label.setTextFormat(Qt.RichText)
            # 14px to match the QGroupBox::title size (Step 1's block), so all
            # section headers read at the same scale.
            label.setStyleSheet("font-size: 14px; margin-top: 4px;")
        else:
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
            # Prevent opening a second instance of the same toolbox widget. If a
            # dock with this name is already open, warn (OK) and don't add another.
            # Uses the same public/fallback access pattern as clear_dock().
            container = getattr(self.viewer.window, 'dock_widgets', None)
            if container is None:
                container = getattr(self.viewer.window, '_dock_widgets', {})
            already_open = False
            # napari's dock_widgets is a dict keyed by dock name — check keys first.
            try:
                if dock_name in container:
                    already_open = True
            except Exception:
                pass
            if not already_open:
                try:
                    _dws = list(container.values())
                except AttributeError:
                    _dws = list(container)
                except Exception:
                    _dws = []
                for dw in _dws:
                    try:
                        name_attr = getattr(dw, 'name', None)
                        title = ''
                        if hasattr(dw, 'windowTitle'):
                            try:
                                title = dw.windowTitle()
                            except Exception:
                                title = ''
                        if name_attr == dock_name or title == dock_name:
                            already_open = True
                            break
                    except Exception:
                        continue
            if already_open:
                try:
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.information(
                        None, "Already open",
                        f"\u201c{dock_name}\u201d is already open.\n\n"
                        "Close the existing one first if you want a fresh copy.",
                        QMessageBox.Ok)
                except Exception:
                    pass
                return
            # Create a new layout for the separate widget
            dock_layout = QVBoxLayout()
            dock_layout.addWidget(widget)
            
            # Create a main widget to contain the input widget
            main_widget = QWidget()
            main_widget.setLayout(dock_layout)
            # Allow the dock content to shrink to the dock width instead of forcing
            # a minimum width that gets clipped (horizontal scroll is disabled).
            # Matches the main analysis docks, which all set this.
            main_widget.setMinimumWidth(0)

            # Guard all spin boxes / sliders / combos in this widget against
            # accidental wheel-scroll value changes (covers every toolbox tool
            # that goes through this common docking path).
            try:
                _apply_scroll_guard(main_widget)
            except Exception:
                pass
            try:
                _relax_min_widths(main_widget)
            except Exception:
                pass

            # Add the main widget to the viewer as a dock widget, wrapped in a
            # scroll area whose horizontal scrollbar is disabled so content fits
            # the dock width (vertical scroll only) — consistent with the pipeline
            # docks and the separate workflow modules.
            try:
                _sa = QScrollArea()
                _sa.setWidgetResizable(True)
                _sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                _sa.setWidget(main_widget)
                self.viewer.window.add_dock_widget(_sa, name=dock_name)
            except Exception:
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

    def _layer_row(self, layout, label_text, layer_type, name_hint='',
                   optional=False):
        """Add a status-circle + label + layer-dropdown row to *layout*, matching
        the field-status UEX from the temperature workflow. Returns the dropdown.
        Circle is red (required) or yellow (optional) until a real layer is
        selected, then turns green. 'None' placeholders stay red/yellow."""
        from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget
        try:
            from pycat.ui.field_status import StatusCircle
        except Exception:
            dd = self.create_layer_dropdown(layer_type, name_hint)
            self.add_text_label(layout, label_text)
            layout.addWidget(dd)
            return dd
        circle = StatusCircle()
        init_color = 'yellow' if optional else 'red'
        init_tip = ('Optional — a default will be used.' if optional
                    else 'Required — select a layer to continue.')
        circle._set(init_color, init_tip)
        # Label on its own row (no marker), then the marker sits inline to the
        # LEFT of the dropdown it applies to.
        self.add_text_label(layout, label_text)
        dd = self.create_layer_dropdown(layer_type, name_hint)
        row_w = QWidget()
        row_h = QHBoxLayout(row_w)
        row_h.setContentsMargins(0, 0, 0, 0); row_h.setSpacing(4)
        row_h.addWidget(circle)
        row_h.addWidget(dd, 1)
        layout.addWidget(row_w)
        # Track whether the user has DELIBERATELY picked an item. QComboBox.activated
        # fires only on user interaction (not on programmatic setCurrentIndex or the
        # implicit index-0 default), so we use it to distinguish a real choice from
        # the dropdown merely defaulting to the first layer.
        _user_picked = [False]
        def _mark_user_picked(*_):
            _user_picked[0] = True
        try:
            dd.activated.connect(_mark_user_picked)
        except Exception:
            pass

        def _update_circle(*_):
            # The layers.events.inserted signal (connected below) outlives this
            # widget: after a workflow is torn down and its dropdown deleted, a
            # later layer insertion would still fire this callback with a stale
            # `dd`, raising "wrapped C/C++ object of type QComboBox has been
            # deleted". Guard every access so a stale call is a harmless no-op.
            try:
                txt = (dd.currentText() or '').strip()
            except RuntimeError:
                return  # dd was deleted; nothing to update
            txt_l = txt.lower()
            is_placeholder = (not txt_l or txt_l.startswith(
                ('select', 'none', '--', '—', 'no ', 'choose')))
            try:
                if is_placeholder:
                    # Nothing chosen → back to the initial required/optional state.
                    circle._set(init_color, init_tip)
                    return
                # A real layer is selected. Distinguish:
                #   GREEN  — the selection matches the name hint (the auto-filled /
                #            suggested layer), or a required field with no hint is
                #            now satisfied.
                #   BLUE   — the user deliberately picked a non-suggested layer, OR
                #            an OPTIONAL field with no hint was set to a real value
                #            (i.e. changed away from its 'None'/default).
                if name_hint:
                    matches_hint = self._hint_matches(name_hint, txt)
                    if matches_hint:
                        circle._set('green', 'Done — using the suggested layer.')
                    elif _user_picked[0]:
                        circle._set('blue', 'Changed — you picked a different '
                                            'layer than the suggested one.')
                    else:
                        circle._set(init_color, init_tip)
                else:
                    # No hint: a required field is simply satisfied (green); an
                    # optional field with a real value has been changed from its
                    # default (blue).
                    if optional:
                        circle._set('blue', 'Changed — you set this optional layer.')
                    else:
                        circle._set('green', 'Done — layer selected.')
            except RuntimeError:
                return
        dd.currentIndexChanged.connect(_update_circle)
        _update_circle()

        # Also re-evaluate when a new layer lands (auto-selection via name_hint
        # may not fire currentIndexChanged if the index doesn't change). This
        # connects to the viewer-level inserted signal, which outlives the
        # dropdown — so we disconnect it when the dropdown is destroyed to avoid
        # leaking stale callbacks (see the guard in _update_circle above).
        def _on_inserted_with_circle_refresh(event):
            # Only react to layers of this row's type (see perf note in
            # create_layer_dropdown._on_inserted).
            try:
                inserted_layer = getattr(event, 'value', None)
                if inserted_layer is not None and not isinstance(inserted_layer, layer_type):
                    return
            except Exception:
                pass
            try:
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(0, _update_circle)
            except Exception:
                _update_circle()

        self.viewer.layers.events.inserted.connect(_on_inserted_with_circle_refresh)

        def _disconnect_on_destroy(*_):
            try:
                self.viewer.layers.events.inserted.disconnect(
                    _on_inserted_with_circle_refresh)
            except Exception:
                pass
        try:
            dd.destroyed.connect(_disconnect_on_destroy)
        except Exception:
            pass
        return dd

    def _add_workflow_header(self, layout, include_pixel_gate=False,
                             instruction_html=None):
        """Add the Step 1 file-I/O status block to a workflow layout.
        The 'Image loaded' indicator turns green once a file is open.
        Pass include_pixel_gate=True only for imaging pipelines that need a
        physical pixel size (condensate, time-series, general, fibril analysis).
        Non-imaging workflows (FD-curve, Droplet Fusion, Force-Distance) omit it."""
        try:
            from pycat.ui.field_status import (
                FieldRegistry, add_step1_file_io, add_pixel_size_gate)
            reg = FieldRegistry()
            self._field_registry = reg
            add_step1_file_io(self.viewer, layout, reg,
                              instruction_html=instruction_html)
            if include_pixel_gate:
                def _on_px(v):
                    try:
                        reg.refresh()
                        self.central_manager.file_io._enable_auto_scale_bar()
                    except Exception:
                        pass
                _px_refresh = add_pixel_size_gate(
                    layout,
                    lambda: self.central_manager.active_data_class.data_repository,
                    on_set=_on_px, central_manager=self.central_manager)
                # Store the gate refresh (which carries a ._reset_gate) so Clear
                # can re-show the gate for the next dataset.
                try:
                    self.central_manager._pixel_gate_refresh = _px_refresh
                except Exception:
                    pass
                # The pixel-size gate only re-evaluated on field edit / data
                # switch, so its status marker went stale when an image loaded
                # (metadata scale detected) or the canvas was cleared. Wire its
                # refresh to layer insert/remove so it updates in lock-step with
                # the "Image loaded" marker.
                if callable(_px_refresh):
                    try:
                        self.viewer.layers.events.inserted.connect(
                            lambda e: _px_refresh())
                        self.viewer.layers.events.removed.connect(
                            lambda e: _px_refresh())
                    except Exception:
                        pass
        except Exception:
            pass

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
        from PyQt5.QtWidgets import QCheckBox
        measure_layout = QVBoxLayout() # Create a vertical layout widget
        self.add_text_label(measure_layout, 'Measure Object Diameters', bold=True) # Add widget title label
        measure_button = QPushButton("Measure Line(s)") # Create a button widget
        def _arm_line_drawing():
            """Activate a diameter Shapes layer in add_line mode so the user can
            draw. Clicking an image layer's eyeball (napari default) steals the
            active layer, silently disabling line drawing even though the Shapes
            layer still looks selected; this re-arms it deterministically."""
            try:
                import numpy as _np
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
        def _on_measure_line():
            self.on_general_button_clicked(
                self.central_manager.active_data_class.calculate_length, None, self.viewer)
            self._record('measure_line', {
                'object_size': self.central_manager.active_data_class.data_repository.get('object_size'),
                'cell_diameter': self.central_manager.active_data_class.data_repository.get('cell_diameter'),
                'ball_radius': self.central_manager.active_data_class.data_repository.get('ball_radius'),
            })
        measure_button.clicked.connect(_on_measure_line)

        # "Measure Line(s)" is the only button here — drawing happens directly on
        # the diameter Shapes layer (armed by _arm_line_drawing when the widget is
        # shown / the layer is selected). The circle is required (red) and turns
        # green once Measure Line(s) has been run.
        try:
            from pycat.ui.field_status import button_with_circle
            _measure_wrapped = button_with_circle(measure_button)  # red → green on run
            self._measure_line_status = _measure_wrapped
            measure_layout.addWidget(_measure_wrapped)
        except Exception:
            measure_layout.addWidget(measure_button)

        # Arm line drawing automatically so the diameter Shapes layer is ready to
        # draw on as soon as this step is shown — no separate "Draw" button needed.
        # Deferred so the layer exists and the dock has finished building.
        try:
            from PyQt5.QtCore import QTimer as _QTarm
            _QTarm.singleShot(0, _arm_line_drawing)
        except Exception:
            try:
                _arm_line_drawing()
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
        from PyQt5.QtWidgets import QCheckBox, QSlider, QLabel as _QLabel, QWidget as _QWidget, QFormLayout
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
            # Only record suppression params when the user has overridden defaults,
            # so unmodified configs stay clean and forward-compatible.
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





    #### Image Segmentation Functions #### 




    #### Image Feature Analysis Functions ####



    #### Label and Mask Tools ####


    # Labeleled Mask Tools 

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
        self._add_workflow_header(self.general_layout, include_pixel_gate=True)
        self.central_manager.toolbox_functions_ui._add_measure_line(layout=self.general_layout)
        self.central_manager.toolbox_functions_ui._add_run_upscaling(layout=self.general_layout)
        self.central_manager.toolbox_functions_ui._add_pre_process(layout=self.general_layout)
        # (Enhanced BG removal is now produced by the Pre-process Image button — merged in 1.5.136)
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
        main_widget.setMinimumWidth(0)
        try:
            _relax_min_widths(main_widget)
        except Exception:
            pass
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

    def _hide_napari_native_menus(self, hidden_default=True):
        """Collapse napari's own top-level menus (File / View / Plugins / Window /
        Help / Layers) behind a single toggle, hidden by default.

        The PyCAT workflow never needs napari's native menus, and several test
        users lost their session by loading data through napari's File -> Open
        (which bypasses PyCAT's channel-assignment / metadata pipeline and crashes
        the workflow). So on startup only PyCAT's own controls are visible, with
        napari's menus tucked away -- but NOT removed: a leftmost toggle reveals /
        hides them on demand, because some of napari's layer operations are
        genuinely useful. napari's Open* actions stay disabled regardless, so even
        when the menus are revealed, data still loads through PyCAT.

        Fully defensive: identifies napari-native menus by title, never touches
        PyCAT's own menus, and never raises if napari changes its menu layout.
        """
        try:
            menubar = self.viewer.window._qt_window.menuBar()
        except Exception:
            return

        def _norm(t):
            return (t or '').replace('&', '').strip().lower()

        # PyCAT's own top-level titles -- never collapse these.
        pycat_titles = {
            _norm('\u25c6 PyCAT \u25b8'), _norm('Analysis Methods'), _norm('Toolbox'),
            _norm('\u2605 Open/Save File(s)'), _norm('\u2620 Clear'), _norm('\u2302 Home'),
            _norm('\u24d8 Metadata'), _norm('\u2630 Recorded Steps'),
            _norm('\u2630 napari'), _norm('\u2630 napari \u25be'),
        }
        # napari-native top-level menus to collapse.
        napari_titles = {'file', 'view', 'plugins', 'window', 'help', 'layers',
                         'acquisition'}

        # Collect the napari-native menu actions currently on the bar.
        self._napari_menu_actions = []
        try:
            for action in menubar.actions():
                menu = action.menu()
                if menu is None:
                    continue
                title = _norm(action.text() or menu.title())
                if title in pycat_titles:
                    continue
                if title in napari_titles:
                    self._napari_menu_actions.append(action)
        except Exception:
            self._napari_menu_actions = []

        # Make PyCAT's Open/Save the first PyCAT menu (workflow entry point).
        self._reorder_pycat_menu_bar()

        # Build the leftmost toggle that shows / hides the napari menus.
        try:
            from PyQt5.QtGui import QFont as _QFont
            self._napari_toggle_action = QAction('\u2630 napari',
                                                 self.viewer.window._qt_window)
            self._napari_toggle_action.setToolTip(
                'Show / hide napari\u2019s own menus (File, View, Layers, Window, '
                'Help). Hidden by default \u2014 the PyCAT workflow doesn\u2019t need '
                'them, but napari\u2019s layer operations are available here if you '
                'want them. (napari\u2019s Open stays disabled; load data via '
                '\u2605 Open/Save File(s).)')
            _tf = _QFont()
            _tf.setPointSize(max(1, _tf.pointSize() - 1))
            self._napari_toggle_action.setFont(_tf)
            self._napari_menus_visible = not hidden_default
            self._napari_toggle_action.triggered.connect(self._toggle_napari_menus)
            # Insert the toggle as the LEFTMOST item so that, with napari's menus
            # hidden, the bar reads: [napari] PyCAT | Open/Save | Analysis | ...
            _all = menubar.actions()
            if _all:
                menubar.insertAction(_all[0], self._napari_toggle_action)
            else:
                menubar.addAction(self._napari_toggle_action)
        except Exception:
            self._napari_toggle_action = None

        # Apply the default visibility (hidden).
        self._set_napari_menus_visible(not hidden_default)

        # Belt-and-suspenders: napari's Open* actions stay disabled even when the
        # menus are revealed, so data always loads through PyCAT.
        try:
            self._disable_napari_open_actions()
        except Exception:
            pass

    def _set_napari_menus_visible(self, visible):
        """Show or hide the collected napari-native menu actions and update the
        toggle label to reflect state."""
        for action in getattr(self, '_napari_menu_actions', []):
            try:
                action.setVisible(visible)
            except Exception:
                pass
        self._napari_menus_visible = visible
        tog = getattr(self, '_napari_toggle_action', None)
        if tog is not None:
            try:
                tog.setText('\u2630 napari \u25be' if visible else '\u2630 napari')
            except Exception:
                pass

    def _toggle_napari_menus(self, *_):
        """Flip napari-native menu visibility (the un-hide control)."""
        self._set_napari_menus_visible(
            not getattr(self, '_napari_menus_visible', False))

    def _reorder_pycat_menu_bar(self):
        """Move PyCAT's ★ Open/Save File(s) ahead of Analysis Methods / Toolbox,
        so loading data (the workflow entry point) is the first PyCAT menu.
        Defensive: no-op if the expected actions aren't present."""
        try:
            menubar = self.viewer.window._qt_window.menuBar()
            file_action = self.file_menu.menuAction()
            anchor = self.analysis_methods_menu.menuAction()
            if file_action is not None and anchor is not None:
                menubar.removeAction(file_action)
                menubar.insertAction(anchor, file_action)
        except Exception:
            pass

    def _disable_napari_open_actions(self):
        """Hard-disable every napari action that loads data, so a file can never
        enter the viewer through napari's own reader (which bypasses PyCAT's
        channel-assignment / data-repository pipeline and breaks downstream
        analysis). Loading must always go through PyCAT's ★ Open/Save File(s).

        Matching is primarily by the action's stable ``objectName`` (napari 0.7
        gives every action one, e.g. ``napari.window.file.open_files_dialog``),
        which is far more robust than display text (accelerators, '...' suffixes,
        version renames). A small text fallback covers older napari.

        napari builds some menus lazily (actions only exist once the menu is
        shown), so this is ALSO wired to each file-menu's ``aboutToShow`` to
        re-disable every time the menu opens — a one-shot startup pass alone
        misses lazily-created actions and anything napari re-enables.
        """
        try:
            window = self.viewer.window._qt_window
        except Exception:
            return

        # Stable objectName prefixes / exact ids for data-LOADING actions.
        # Anything whose objectName starts with one of these, OR is a sample
        # loader (napari.<sample> under the Open Sample menu), is disabled.
        _load_object_prefixes = (
            'napari.window.file.open_files_dialog',
            'napari.window.file.open_files_as_stack_dialog',
            'napari.window.file.open_folder_dialog',
            'napari.window.file._open_files_with_plugin',
            'napari.window.file._open_files_as_stack_with_plugin',
            'napari.window.file._open_folder_with_plugin',
            'napari.window.file._image_from_clipboard',
        )
        # Text fallback for older napari that may lack objectNames.
        _load_texts = {'open', 'open file...', 'open files...', 'open file(s)...',
                       'open folder...', 'open sample', 'open files as stack...',
                       'new image from clipboard'}

        _tip = ('Loading through napari is disabled \u2014 use PyCAT\u2019s '
                '\u2605 Open/Save File(s) menu so data enters PyCAT\u2019s '
                'pipeline (channel assignment + registration). napari\u2019s own '
                'reader would bypass this and break analysis.')

        def _is_load_action(act):
            on = act.objectName() or ''
            if any(on.startswith(p) for p in _load_object_prefixes):
                return True
            # Open Sample entries: objectName is 'napari.<sample>' and they live
            # under the Open Sample menu; disable all sample loaders.
            if on.startswith('napari.') and self._obj_is_sample_loader(on):
                return True
            txt = (act.text() or '').replace('&', '').strip().lower()
            return txt in _load_texts

        def _disable_in_menu(menu, depth=0):
            """Recursively disable+hide load actions within a QMenu tree. Walking
            the menu tree (rather than window.findChildren) is essential on napari
            0.7.1, where menu actions are provided by the app-model and may not be
            children of the QMainWindow — so findChildren misses them, but the
            menu that renders them always contains them."""
            if menu is None or depth > 4:
                return
            try:
                for act in menu.actions():
                    sub = act.menu()
                    if sub is not None:
                        _disable_in_menu(sub, depth + 1)
                        # Hide the submenu CONTAINER itself if, after processing,
                        # it has no usable content left: either every real action
                        # is now hidden (e.g. "Open with Plugin" — all its entries
                        # are load actions we hid) or it holds only napari's
                        # disabled "empty_dummy" placeholders ("IO Utilities",
                        # "Acquire"). Leaves genuinely-useful submenus alone.
                        try:
                            subacts = [a for a in sub.actions()
                                       if not a.isSeparator()]
                            def _dead(a):
                                on = a.objectName() or ''
                                return ((not a.isVisible()) or (not a.isEnabled())
                                        or on.endswith('empty_dummy'))
                            if subacts and all(_dead(a) for a in subacts):
                                act.setVisible(False)
                        except Exception:
                            pass
                        continue
                    try:
                        if _is_load_action(act):
                            act.setEnabled(False)
                            act.setToolTip(_tip)
                            # Hiding removes it from the menu entirely — a hidden
                            # action can't be triggered even if napari re-enables
                            # it, and makes the lockdown visually obvious.
                            act.setVisible(False)
                    except Exception:
                        continue
            except Exception:
                pass

        def _sweep():
            # Primary: walk the menu-bar tree (reaches app-model actions).
            try:
                menubar = window.menuBar()
                for action in menubar.actions():
                    _disable_in_menu(action.menu())
            except Exception:
                pass
            # Secondary: also sweep any QActions parented under the window
            # (older napari where actions ARE window children).
            try:
                from PyQt5.QtGui import QAction as _QA
            except Exception:
                from PyQt5.QtWidgets import QAction as _QA
            try:
                for act in window.findChildren(_QA):
                    try:
                        if _is_load_action(act):
                            act.setEnabled(False)
                            act.setToolTip(_tip)
                            act.setVisible(False)
                    except Exception:
                        continue
            except Exception:
                pass

        # Initial sweep.
        _sweep()

        # Re-sweep whenever any top-level menu (or its submenus) is about to show
        # — covers lazily built/re-created actions. napari 0.7.1 may REBUILD menu
        # actions each time the menu opens, so a one-shot disable of the original
        # QAction objects is undone; re-running at aboutToShow catches the fresh
        # actions right before they're displayed. Connect once per menu.
        if not getattr(self, '_napari_load_guard_wired', False):
            try:
                menubar = window.menuBar()
                for action in menubar.actions():
                    menu = action.menu()
                    if menu is not None:
                        menu.aboutToShow.connect(_sweep)
                        for sub in menu.actions():
                            smenu = sub.menu()
                            if smenu is not None:
                                smenu.aboutToShow.connect(_sweep)
                self._napari_load_guard_wired = True
            except Exception:
                pass

    def _obj_is_sample_loader(self, object_name):
        """True for napari 'Open Sample' loader actions. These have objectNames
        like 'napari.astronaut' / 'napari.cells3d' (a sample id) rather than the
        'napari.window.*' / 'napari.viewer.*' / 'napari.layer.*' namespaces used
        by UI/toggle actions. Heuristic: 'napari.<single_token>' with no further
        dotted namespace, and not one of the known non-loader singletons."""
        parts = object_name.split('.')
        if len(parts) != 2 or parts[0] != 'napari':
            return False
        # Known non-loader 'napari.<x>' actions to leave alone (none currently,
        # but guard against false positives on UI singletons).
        _not_loaders = {'napari.new_layer'}
        return object_name not in _not_loaders


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

            # Zoom to fit: need the canvas size in pixels. Prefer the private
            # `_qt_viewer` attribute — the public `window.qt_viewer` property is
            # deprecated (napari ≤0.8) and emits a FutureWarning on access, so we
            # try the private one first and only fall back with the warning muted.
            cw = ch = None
            import warnings as _warnings
            with _warnings.catch_warnings():
                _warnings.simplefilter('ignore', FutureWarning)
                for accessor in ('_qt_viewer', 'qt_viewer'):
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
        # ── PyCAT section marker ─────────────────────────────────────────────
        # PyCAT's menus are appended to napari's own menu bar (File/View/Plugins/
        # Window/Help). Without a visual break, users can't tell where napari ends
        # and PyCAT begins. Insert a bold, non-clickable marker as an obvious
        # divider so everything to its right reads as "PyCAT".
        from PyQt5.QtGui import QFont as _QFont, QColor as _QColor
        _menubar = self.viewer.window._qt_window.menuBar()
        self._pycat_marker_action = QAction('◆ PyCAT ▸', self.viewer.window._qt_window)
        self._pycat_marker_action.setEnabled(False)   # non-clickable divider
        _mfont = _QFont()
        _mfont.setBold(True)
        _mfont.setPointSize(_mfont.pointSize() + 1)
        self._pycat_marker_action.setFont(_mfont)
        # Make the disabled action's text render in an accent colour rather than
        # the greyed-out default, so it reads as a heading not a dead menu.
        try:
            _menubar.setStyleSheet(
                _menubar.styleSheet() +
                "\nQMenuBar::item:disabled { color: #2d7dd2; font-weight: bold; }")
        except Exception:
            pass
        _menubar.addAction(self._pycat_marker_action)

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

        # Metadata viewer: shows the curated acquisition metadata for the loaded
        # file, with a toggle to reveal the full raw metadata dump.
        self.metadata_action = QAction('\u24d8 Metadata', self.viewer.window._qt_window)
        self.metadata_action.setToolTip(
            'View acquisition metadata (pixel size, objective, wavelengths, '
            'dimensions, date) for the loaded file.')
        self.metadata_action.triggered.connect(self._show_metadata_dialog)
        self.viewer.window._qt_window.menuBar().addAction(self.metadata_action)

        # Recorded-steps viewer: shows the batch workflow recorded so far, with
        # each step expandable to reveal the layers/parameters it used.
        self.recorded_steps_action = QAction('\u2630 Recorded Steps',
                                             self.viewer.window._qt_window)
        self.recorded_steps_action.setToolTip(
            'View the batch workflow recorded so far — each step and the '
            'layers/parameters it used.')
        self.recorded_steps_action.triggered.connect(self._show_recorded_steps_dialog)
        self.viewer.window._qt_window.menuBar().addAction(self.recorded_steps_action)

        # Route files dropped onto the napari window through PyCAT's openers
        # (napari's default drop bypasses PyCAT's channel-assignment pipeline).
        try:
            from PyQt5.QtWidgets import QApplication
            self._pycat_drop_filter = _FileDropFilter(self.central_manager.file_io)
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(self._pycat_drop_filter)
            self.viewer.window._qt_window.setAcceptDrops(True)
            # An app-level filter usually sees events first, but a file dropped
            # directly on the napari CANVAS is handled by napari's QtViewer. The
            # "no-drop" cursor over the canvas means the canvas widget has
            # acceptDrops=False, so Qt never even generates DragEnter/Drop events
            # there for our filter to catch. Fix: force acceptDrops=True on the
            # QtViewer + its canvas widget, and install our event filter on each
            # so it intercepts and routes the drop. (PyQt does not honour
            # instance-level dropEvent reassignment — Qt calls the C++ virtual —
            # so an installed event filter is the correct mechanism, and it only
            # works once acceptDrops is enabled on the target widget.)
            _qtv = None
            for _acc in ('_qt_viewer', 'qt_viewer'):
                try:
                    _qtv = getattr(self.viewer.window, _acc, None)
                    if _qtv is not None:
                        break
                except Exception:
                    continue

            def _enable_drops(widget):
                if widget is None:
                    return
                try:
                    if hasattr(widget, 'setAcceptDrops'):
                        widget.setAcceptDrops(True)
                    if hasattr(widget, 'installEventFilter'):
                        widget.installEventFilter(self._pycat_drop_filter)
                except Exception:
                    pass

            if _qtv is not None:
                _enable_drops(_qtv)
                for _wattr in ('canvas', '_canvas'):
                    try:
                        _w = getattr(_qtv, _wattr, None)
                        _qw = getattr(_w, 'native', _w)
                        _enable_drops(_qw)
                        # vispy's native widget may itself wrap a viewport/child
                        # that receives the events; enable on its children too.
                        if _qw is not None and hasattr(_qw, 'children'):
                            try:
                                for _child in _qw.children():
                                    if hasattr(_child, 'setAcceptDrops'):
                                        _enable_drops(_child)
                            except Exception:
                                pass
                    except Exception:
                        continue
        except Exception as _e:
            print(f"[PyCAT] Could not install file-drop handler: {_e}")

        # LAYER-INSERTION BACKSTOP for drag-and-drop onto the canvas.
        # On napari 0.7.1 the canvas refuses the drag before any event filter can
        # catch it (the "no-drop" cursor), so the filter approach above cannot
        # intercept a canvas drop. This backstop takes the opposite tack: let
        # napari's own reader load the file (producing a layer), then detect that
        # layer as FOREIGN (napari sets layer.source.path on reader-loaded layers;
        # PyCAT's programmatic add_image leaves it None), remove the raw napari
        # layer(s), and re-open the SAME path through PyCAT's context-aware opener
        # so it enters the channel-assignment / metadata pipeline. This catches a
        # load no matter how it was triggered (canvas drop, or any path we can't
        # otherwise block), without depending on reaching napari's canvas widget.
        try:
            self._pycat_reroute_guard = False

            def _on_foreign_layer_inserted(event):
                # Re-entrancy guard: PyCAT's own opener inserts layers too.
                if getattr(self, '_pycat_reroute_guard', False):
                    return
                try:
                    layer = event.value
                except Exception:
                    layer = getattr(event, 'source', None)
                if layer is None:
                    return
                # Foreign = has a reader source path PyCAT didn't set.
                src_path = None
                try:
                    src = getattr(layer, 'source', None)
                    src_path = getattr(src, 'path', None) if src is not None else None
                except Exception:
                    src_path = None
                if not src_path:
                    return  # programmatic PyCAT layer — leave it alone
                # Defer the reroute: several layers can be inserted from one drop
                # (multi-channel), and we must not mutate viewers inside the
                # inserted callback. Collect the path and process once via a timer.
                try:
                    if not hasattr(self, '_pending_foreign_paths'):
                        self._pending_foreign_paths = []
                    if src_path not in self._pending_foreign_paths:
                        self._pending_foreign_paths.append(src_path)
                    from PyQt5.QtCore import QTimer
                    QTimer.singleShot(0, self._process_foreign_layers)
                except Exception:
                    pass

            self._on_foreign_layer_inserted = _on_foreign_layer_inserted
            self.viewer.layers.events.inserted.connect(_on_foreign_layer_inserted)
        except Exception as _e:
            print(f"[PyCAT] Could not install layer-insertion backstop: {_e}")

        # Hide napari's native File menu (and disable its Open* actions) so users
        # can't accidentally load data through napari's reader, which routes
        # around PyCAT's channel-assignment / metadata pipeline and crashes the
        # downstream workflow. Data must load via PyCAT's ★ Open/Save File(s).
        self._hide_napari_native_menus()

    def _process_foreign_layers(self):
        """Remove napari-reader-loaded (foreign) layers and re-open their source
        files through PyCAT's opener. Runs deferred (QTimer) so it doesn't mutate
        the layer list from inside the inserted-event callback. Handles the
        multi-layer case (one dropped multi-channel file → several foreign
        layers sharing a path)."""
        paths = getattr(self, '_pending_foreign_paths', [])
        self._pending_foreign_paths = []
        if not paths:
            return
        # Collect and remove every foreign layer whose source path is in our set.
        try:
            to_remove = []
            for layer in list(self.viewer.layers):
                try:
                    src = getattr(layer, 'source', None)
                    sp = getattr(src, 'path', None) if src is not None else None
                except Exception:
                    sp = None
                if sp and sp in paths:
                    to_remove.append(layer)
            for layer in to_remove:
                try:
                    self.viewer.layers.remove(layer)
                except Exception:
                    pass
        except Exception:
            pass
        # Re-open each unique path through PyCAT's context-aware opener, guarding
        # against the backstop re-triggering on PyCAT's own inserts.
        import os as _os
        self._pycat_reroute_guard = True
        try:
            from napari.utils.notifications import show_info as _info
            for i, p in enumerate(paths):
                try:
                    # First dropped file replaces the session (normal open);
                    # additional files add without clearing (comparison).
                    self.central_manager.file_io.open_image_auto(
                        file_path=p, clear_first=(i == 0))
                except Exception as _e:
                    print(f"[PyCAT] Could not re-open dropped file '{p}': {_e}")
            try:
                _info("Loaded dropped file(s) through PyCAT.")
            except Exception:
                pass
        finally:
            self._pycat_reroute_guard = False

    def _show_metadata_dialog(self):
        """Show acquisition metadata for the loaded file.

        Displays the curated 'common' fields by default, with a checkbox that
        reveals the full raw metadata dump. Also offers a JSON export button.
        """
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                      QLabel, QPushButton, QCheckBox,
                                      QTableWidget, QTableWidgetItem, QHeaderView,
                                      QFileDialog)
        from napari.utils.notifications import (show_info as _info,
                                                show_warning as _warn)
        dr = self.central_manager.active_data_class.data_repository
        md = dr.get('file_metadata')
        if not md or not isinstance(md, dict):
            _warn("No metadata available — open an image first.")
            return

        common = md.get('common', {}) or {}
        raw = md.get('raw', {}) or {}

        dialog = QDialog(self.viewer.window._qt_window)
        dialog.setWindowTitle("File Metadata")
        dialog.resize(560, 620)
        layout = QVBoxLayout(dialog)

        fname = common.get('file_name') or 'Unknown file'
        header = QLabel(f"<b>{fname}</b>")
        layout.addWidget(header)

        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(['Field', 'Value'])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(table)

        # Curated-field display order and labels.
        _labels = [
            ('file_type', 'File type'),
            ('dimensions', 'Dimensions (T,C,Z,Y,X)'),
            ('pixel_size_um', 'Pixel size (µm/px)'),
            ('pixel_size_source', 'Pixel size source'),
            ('bit_depth', 'Bit depth'),
            ('n_channels', 'Channels'),
            ('n_timepoints', 'Timepoints'),
            ('n_z', 'Z slices'),
            ('objective', 'Objective'),
            ('numerical_aperture', 'Numerical aperture'),
            ('modality', 'Modality'),
            ('excitation_nm', 'Excitation (nm)'),
            ('emission_nm', 'Emission (nm)'),
            ('acquisition_date', 'Acquisition date'),
            ('software', 'Software'),
            ('camera_name', 'Camera'),
            ('exposure_s', 'Exposure (s)'),
            ('frame_interval_s', 'Frame interval (s)'),
            ('frame_interval_source', 'Frame interval source'),
            ('z_step_um', 'Z step (µm)'),
        ]

        def _fmt(v):
            if v is None:
                return '—'
            if isinstance(v, dict):
                return ', '.join(f"{k.upper()}={v.get(k)}" for k in ('t', 'c', 'z', 'y', 'x')
                                 if v.get(k) is not None)
            if isinstance(v, float):
                return f"{v:.6g}"
            return str(v)

        def _fmt_interval(c):
            """Frame interval with IQR appended when measured per-frame."""
            fi = c.get('frame_interval_s')
            if fi is None:
                return '—'
            txt = f"{float(fi):.6g}"
            iqr = c.get('frame_interval_iqr_s')
            if iqr is not None:
                txt += f"  (IQR {float(iqr):.4g})"
            return txt

        def _populate(show_raw):
            rows = []
            for key, lbl in _labels:
                if key == 'frame_interval_s':
                    rows.append((lbl, _fmt_interval(common)))
                else:
                    rows.append((lbl, _fmt(common.get(key))))
            if show_raw:
                # Full per-frame timing (the measured deltas) live in the
                # expanded view so the curated panel stays compact.
                deltas = common.get('frame_deltas_s')
                if deltas:
                    rows.append(('— frame timing (measured) —', ''))
                    rows.append(('n frames', _fmt(common.get('n_frames'))))
                    rows.append(('acquisition start', _fmt(common.get('acquisition_start_time'))))
                    rows.append(('frame deltas (s)',
                                 ', '.join(f"{float(d):.5g}" for d in deltas)))
                if raw:
                    rows.append(('— raw metadata —', ''))
                    for k in sorted(raw.keys()):
                        rows.append((k, _fmt(raw.get(k))))
            table.setRowCount(len(rows))
            for i, (k, v) in enumerate(rows):
                table.setItem(i, 0, QTableWidgetItem(str(k)))
                table.setItem(i, 1, QTableWidgetItem(str(v)))

        _populate(False)

        controls = QHBoxLayout()
        raw_check = QCheckBox("Show all raw metadata")
        raw_check.toggled.connect(_populate)
        controls.addWidget(raw_check)
        controls.addStretch(1)

        export_btn = QPushButton("Export JSON…")

        def _export():
            import json
            path, _ = QFileDialog.getSaveFileName(
                dialog, "Export metadata as JSON",
                (common.get('file_name') or 'metadata') + '_metadata.json',
                "JSON Files (*.json)")
            if path:
                try:
                    with open(path, 'w', encoding='utf-8') as f:
                        json.dump(md, f, indent=2, default=str)
                    _info(f"Metadata exported to {path}")
                except Exception as e:
                    _warn(f"Export failed: {e}")

        export_btn.clicked.connect(_export)
        controls.addWidget(export_btn)

        compare_btn = QPushButton("Compare loaded images…")
        compare_btn.setToolTip(
            "Diff acquisition settings across the currently visible images and "
            "flag differences (exposure, laser, objective, filters, etc.) that "
            "can make a quantitative comparison untrustworthy.")
        compare_btn.clicked.connect(lambda: self._show_metadata_comparison())
        controls.addWidget(compare_btn)
        layout.addLayout(controls)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)

        dialog.exec_()

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

        status_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
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

    def _show_recorded_steps_dialog(self):
        """Show the batch workflow recorded so far.

        Top-level rows are the recorded steps (number, name, timestamp). Each
        step expands to reveal the layers/parameters it captured, so the user
        can review exactly what will be replayed.
        """
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                      QPushButton, QTreeWidget, QTreeWidgetItem,
                                      QHeaderView)
        from napari.utils.notifications import show_info as _info

        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        steps = (bp.config.get('steps', []) if bp and getattr(bp, 'config', None)
                 else [])
        rec_on = bool(getattr(bp, 'recording_enabled', False)) if bp else False

        dialog = QDialog(self.viewer.window._qt_window)
        dialog.setWindowTitle("Recorded Batch Steps")
        dialog.resize(620, 640)
        layout = QVBoxLayout(dialog)

        status = ("<span style='color:#5cb85c;'>● Recording ON</span>" if rec_on
                  else "<span style='color:#aaa;'>○ Recording off</span>")
        header = QLabel(f"<b>{len(steps)} step(s) recorded</b> &nbsp; {status}")
        layout.addWidget(header)

        if not steps:
            layout.addWidget(QLabel(
                "<span style='color:#aaa;'>No steps recorded yet. Turn on "
                "recording in the Batch dialog, then run your workflow.</span>"))

        tree = QTreeWidget()
        tree.setColumnCount(2)
        tree.setHeaderLabels(['Step', 'Value'])
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(tree)

        # Parameter keys that are internal debugging snapshots — shown last and
        # de-emphasised rather than as primary parameters.
        _debug_keys = {'_active_layer_at_record', '_all_layers_at_record'}

        def _fmt(v):
            if v is None:
                return '—'
            if isinstance(v, (list, tuple)):
                return ', '.join(str(x) for x in v) if v else '(none)'
            if isinstance(v, float):
                return f"{v:.4g}"
            return str(v)

        for i, step in enumerate(steps, 1):
            name = step.get('step', '?')
            ts = step.get('timestamp', '')
            params = step.get('params', {}) or {}
            top = QTreeWidgetItem([f"{i}.  {name}", ts])
            tree.addTopLevelItem(top)
            # Primary params first, debug snapshots last.
            primary = [(k, v) for k, v in params.items() if k not in _debug_keys]
            debug   = [(k, v) for k, v in params.items() if k in _debug_keys]
            for k, v in primary:
                top.addChild(QTreeWidgetItem([str(k), _fmt(v)]))
            for k, v in debug:
                child = QTreeWidgetItem([f"{k}  (snapshot)", _fmt(v)])
                top.addChild(child)
        tree.expandToDepth(0)  # show steps collapsed; user expands to see params

        btn_row = QHBoxLayout()
        expand_btn = QPushButton("Expand all")
        expand_btn.clicked.connect(tree.expandAll)
        collapse_btn = QPushButton("Collapse all")
        collapse_btn.clicked.connect(lambda: tree.collapseAll())
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(expand_btn)
        btn_row.addWidget(collapse_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dialog.exec_()

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
                'Open Image (auto-detect 2D / stack)': (self.central_manager.file_io.open_image_auto, {}),
                'Add Image / Mask (keep current)': (self.central_manager.file_io.add_image_or_mask, {}),
                'Load Previous Session Results': (self._open_session_loader, {}),
                'Save and Clear': (self.central_manager.file_io.save_and_clear_all, {'viewer': self.viewer})
            }
            self._add_actions_to_menu(file_io_methods_dict, self.file_menu)

    def _open_image_add(self, *args, **kwargs):
        """Open an image WITHOUT clearing the current session — adds its layers
        alongside the existing ones (for side-by-side comparison or loading a
        missing channel of a split-file image). Uses the context-aware router."""
        self.central_manager.file_io.open_image_auto(clear_first=False)

    def _toggle_grid_view(self, *args, **kwargs):
        """Toggle a PyCAT-managed side-by-side grid for comparing images.

        napari's raw grid mode tiles EVERY layer — including PyCAT's annotation
        Shapes layers (Cell/Object Diameter) and any drawing layers, which then
        get their own empty tiles instead of overlaying the images. It also grids
        by layer count regardless of the visibility eyeball. This managed version:
          - tiles only IMAGE layers (annotations/shapes/points stay overlaid,
            hidden behind the scenes while comparing — they can't be tiled
            meaningfully since an annotation belongs to one image),
          - respects the visibility eyeball: hidden image layers are dropped from
            the grid and it reflows,
          - recomputes automatically when layer visibility changes while grid is
            on, and restores the normal overlaid view when toggled off.
        """
        try:
            self._pycat_grid_on = not getattr(self, '_pycat_grid_on', False)
        except Exception:
            self._pycat_grid_on = True

        from napari.utils.notifications import show_info as _info
        if self._pycat_grid_on:
            # Snapshot the CANONICAL order of tileable layers at the moment grid
            # is turned on. Every reflow arranges visible layers against THIS
            # fixed anchor (not the transient list order), so toggling visibility
            # — including "show/hide all" — can never shuffle the grid: a layer
            # always returns to the same relative slot. Layers added later append
            # to the anchor in arrival order.
            self._grid_canonical_order = [
                l for l in self.viewer.layers
                if isinstance(l, (napari.layers.Image, napari.layers.Labels))]
            self._apply_managed_grid()
            # Recompute the grid whenever any layer's visibility toggles.
            if not getattr(self, '_grid_vis_wired', False):
                try:
                    for lyr in self.viewer.layers:
                        try:
                            lyr.events.visible.connect(self._on_grid_layer_vis_changed)
                        except Exception:
                            pass
                    # New layers added while grid is on should also be watched.
                    self.viewer.layers.events.inserted.connect(
                        self._on_grid_layers_changed)
                    self.viewer.layers.events.removed.connect(
                        self._on_grid_layers_changed)
                    self._grid_vis_wired = True
                except Exception:
                    pass
            # If any non-image (annotation / drawing) layers were pulled out to
            # keep them from claiming empty grid tiles, tell the user they're
            # temporarily set aside and will come back when grid is turned off —
            # so a drawing layer vanishing from the list isn't alarming.
            n_removed = len(getattr(self, '_grid_removed_nonimage', []))
            if n_removed:
                _info(f"Side-by-side grid view ON. {n_removed} annotation/"
                      f"drawing layer(s) temporarily set aside (with their "
                      f"contents) and will return when you toggle grid off.")
            else:
                _info("Side-by-side grid view ON (image layers only).")
            # Surface an acquisition-metadata comparison so the user knows
            # whether the images being compared were acquired under the same
            # settings (different exposure / laser / objective / filters make a
            # quantitative comparison untrustworthy — independent of the grid).
            try:
                self._maybe_warn_metadata_diff()
            except Exception:
                pass
        else:
            try:
                self.viewer.grid.enabled = False
            except Exception:
                pass
            # Re-insert the non-image layers removed for grid mode.
            n_restored = len(getattr(self, '_grid_removed_nonimage', []))
            self._restore_grid_removed_layers()
            # Clear the canonical order anchor so a fresh snapshot is taken next
            # time grid is enabled.
            self._grid_canonical_order = []
            if n_restored:
                _info(f"Side-by-side grid view OFF. {n_restored} annotation/"
                      f"drawing layer(s) restored.")
            else:
                _info("Side-by-side grid view OFF.")

    def _gather_compared_metadata(self):
        """Collect per-layer acquisition metadata for the currently VISIBLE image
        layers (the ones being compared in grid mode). Returns (names, metas).
        Reads the metadata stashed on each layer at load time."""
        names, metas = [], []
        try:
            for lyr in self.viewer.layers:
                if isinstance(lyr, napari.layers.Image) and bool(getattr(lyr, 'visible', True)):
                    md = None
                    try:
                        full = lyr.metadata.get('pycat_file_metadata')
                        if isinstance(full, dict):
                            md = full.get('common', full)
                    except Exception:
                        md = None
                    names.append(lyr.name)
                    metas.append(md or {})
        except Exception:
            pass
        return names, metas

    def _maybe_warn_metadata_diff(self):
        """When grid comparison starts with 2+ images, run the acquisition-
        metadata diff and, if critical settings differ, pop the comparison table
        so the user knows the comparison may be untrustworthy. If everything
        matches (or metadata is absent), stay quiet."""
        names, metas = self._gather_compared_metadata()
        if len(names) < 2:
            return
        # Only show automatically when there's something worth warning about.
        try:
            from pycat.file_io.metadata_extract import compare_acquisition_metadata
            result = compare_acquisition_metadata(metas, names=names)
        except Exception:
            return
        if result['n_critical_diff'] > 0:
            self._show_metadata_comparison(result)

    def _show_metadata_comparison(self, result=None):
        """Show a table diffing acquisition metadata across the compared images,
        highlighting settings that differ. Can be called standalone; if no
        result is passed it gathers the current visible-image metadata."""
        from qtpy.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTableWidget,
                                    QTableWidgetItem, QPushButton)
        from qtpy.QtGui import QColor
        if result is None:
            names, metas = self._gather_compared_metadata()
            if len(names) < 2:
                from napari.utils.notifications import show_info as _info
                _info("Load/show at least two images to compare their metadata.")
                return
            from pycat.file_io.metadata_extract import compare_acquisition_metadata
            result = compare_acquisition_metadata(metas, names=names)

        names = result['names']
        rows = result['rows']
        dlg = QDialog()
        dlg.setWindowTitle("Acquisition Metadata Comparison")
        lay = QVBoxLayout(dlg)

        verdict = QLabel(result['summary'])
        verdict.setWordWrap(True)
        if result['n_critical_diff'] > 0:
            verdict.setStyleSheet("color:#c0392b; font-weight:bold;")
        elif result['any_diff']:
            verdict.setStyleSheet("color:#b8860b;")
        else:
            verdict.setStyleSheet("color:#2e7d32;")
        lay.addWidget(verdict)

        table = QTableWidget(len(rows), len(names) + 1)
        table.setHorizontalHeaderLabels(['Setting'] + list(names))
        for r, row in enumerate(rows):
            lbl = QTableWidgetItem(row['label']
                                   + ('  \u26a0' if row['differs'] and
                                      row['severity'] == 'critical' else ''))
            table.setItem(r, 0, lbl)
            for c, val in enumerate(row['values']):
                item = QTableWidgetItem('—' if val is None else str(val))
                if row['differs']:
                    # Highlight differing rows: red for critical, amber for info.
                    item.setBackground(QColor('#f9d6d5') if row['severity'] ==
                                       'critical' else QColor('#fdf1cf'))
                table.setItem(r, c + 1, item)
        table.resizeColumnsToContents()
        lay.addWidget(table)

        note = QLabel("Rows highlighted red are acquisition settings that can "
                      "make a quantitative comparison untrustworthy; amber rows "
                      "differ but are less critical. '—' means the value wasn't "
                      "recorded in that file's metadata.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#888; font-size:9pt;")
        lay.addWidget(note)

        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        lay.addWidget(close)
        dlg.resize(600, 400)
        dlg.exec_()

    def _annotation_layers(self):
        """Layers that are pure annotation/drawing (Shapes/Points) — these are
        removed from the grid (they can't tile meaningfully). Mask (Labels)
        layers are NOT included here: they overlay their image and are controlled
        by their own visibility eyeball."""
        out = []
        try:
            for lyr in self.viewer.layers:
                if isinstance(lyr, (napari.layers.Shapes, napari.layers.Points)):
                    out.append(lyr)
        except Exception:
            pass
        return out

    def _grid_tileable_visible(self):
        """Visible layers that should occupy grid cells: Image and Labels (mask)
        layers that are currently visible, in layer order."""
        out = []
        try:
            for lyr in self.viewer.layers:
                if isinstance(lyr, (napari.layers.Image, napari.layers.Labels)):
                    if bool(getattr(lyr, 'visible', True)):
                        out.append(lyr)
        except Exception:
            pass
        return out

    def _apply_managed_grid(self):
        """Enable napari grid, reflowed to only the VISIBLE tileable layers.

        The diagnostic on napari 0.7.1 established two facts that drive this:
          (1) napari's grid tiles by TOTAL layer count and ignores visibility, so
              hidden layers otherwise leave empty black tiles (grid does NOT
              reflow on its own, and shape=(-1,-1) auto-recomputes to the full
              count) — but
          (2) setting grid.shape EXPLICITLY to fit the visible count DOES reflow
              the canvas, and napari fills cells by LAYER INDEX.
        So: remove pure annotation/drawing layers; arrange the visible tileable
        layers (images + visible masks) into the front cells ORDERED BY A
        CANONICAL ANCHOR snapshotted when grid was enabled — so visibility
        toggles (including show/hide-all) reflow the grid without ever shuffling
        which layer sits where — and set grid.shape to fit the visible count.
        Hidden tileable layers sort after the visible ones; masks overlay their
        image via z-order and are governed by their own eyeball.

        Idempotent and re-entrancy-safe.
        """
        import math
        if getattr(self, '_grid_applying', False):
            return
        self._grid_applying = True
        try:
            g = self.viewer.grid
            # 1. Remove pure annotation/drawing layers (recorded for restore).
            if not hasattr(self, '_grid_removed_nonimage'):
                self._grid_removed_nonimage = []
            for idx in range(len(self.viewer.layers) - 1, -1, -1):
                lyr = self.viewer.layers[idx]
                if isinstance(lyr, (napari.layers.Shapes, napari.layers.Points)):
                    if not any(l is lyr for _, l in self._grid_removed_nonimage):
                        self._grid_removed_nonimage.append((idx, lyr))
                    try:
                        self.viewer.layers.remove(lyr)
                    except Exception:
                        pass
            # 2. Count visible tileable layers and set an explicit grid shape.
            vis = self._grid_tileable_visible()
            n = len(vis)
            if n <= 1:
                g.enabled = False
                return
            # 3. Arrange visible tileable layers into the front cells, ordered by
            #    the CANONICAL anchor captured at grid-on (not by transient list
            #    order) so visibility toggles never shuffle the grid. Any layer
            #    not in the anchor (added after grid-on) is appended in arrival
            #    order. Hidden tileable layers go after the visible ones.
            anchor = getattr(self, '_grid_canonical_order', None) or []

            def _anchor_key(layer):
                try:
                    return anchor.index(layer)
                except ValueError:
                    return len(anchor) + list(self.viewer.layers).index(layer)

            vis_sorted = sorted(vis, key=_anchor_key)
            hidden_tileable = [
                l for l in self.viewer.layers
                if isinstance(l, (napari.layers.Image, napari.layers.Labels))
                and l not in vis]
            hidden_sorted = sorted(hidden_tileable, key=_anchor_key)
            target = vis_sorted + hidden_sorted + [
                l for l in self.viewer.layers
                if l not in vis_sorted and l not in hidden_sorted]
            try:
                for dst, lyr in enumerate(target):
                    src = list(self.viewer.layers).index(lyr)
                    if src != dst:
                        self.viewer.layers.move(src, dst)
            except Exception:
                pass
            cols = int(math.ceil(math.sqrt(n)))
            rows = int(math.ceil(n / cols))
            g.enabled = True
            try:
                g.stride = 1
                g.shape = (rows, cols)   # EXPLICIT shape → reflows (proven)
            except Exception:
                pass
        except Exception as _e:
            print(f"[PyCAT] managed grid failed: {_e}")
        finally:
            self._grid_applying = False

    def _restore_grid_removed_layers(self):
        """Re-insert the annotation/drawing layers removed for grid mode, at their
        original positions (best-effort), preserving their data."""
        removed = getattr(self, '_grid_removed_nonimage', [])
        for idx, lyr in sorted(removed, key=lambda t: t[0]):
            try:
                if lyr not in list(self.viewer.layers):
                    insert_at = min(idx, len(self.viewer.layers))
                    self.viewer.layers.insert(insert_at, lyr)
            except Exception:
                try:
                    self.viewer.layers.append(lyr)
                except Exception:
                    pass
        self._grid_removed_nonimage = []

    def _on_grid_layer_vis_changed(self, *args):
        if getattr(self, '_pycat_grid_on', False):
            self._apply_managed_grid()

    def _on_grid_layers_changed(self, *args):
        if getattr(self, '_pycat_grid_on', False):
            # Wire visibility watcher on any new layer, then recompute.
            try:
                for lyr in self.viewer.layers:
                    try:
                        lyr.events.visible.connect(self._on_grid_layer_vis_changed)
                    except Exception:
                        pass
            except Exception:
                pass
            self._apply_managed_grid()

    # Add specific analysis methods as actions to the analysis methods menu.
    def _add_analysis_methods_to_menu(self):
        """
        Add specific analysis methods as actions to the analysis methods menu. 
        """
        # Imaging/morphometric pipelines — agnostic to whether the system has a
        # membrane (cellular or in vitro), hence "Condensate & Cell Analysis".
        condensate_cell_analysis_submenu = self.analysis_methods_menu.addMenu('Cell and Object Analyses')
        condensate_cell_analysis_dict = {
            'Cellular Object Analysis (Fluorescence)': (self.central_manager.analysis_methods_ui._switch_to_condensate_analysis, {'base_data_repository': self.central_manager.active_data_class.data_repository}),
            'In Vitro Object Analysis (Fluorescence)': (self.central_manager.analysis_methods_ui._switch_to_invitro_fluor_analysis, {}),
            'In Vitro Object Analysis (Brightfield)': (self.central_manager.analysis_methods_ui._switch_to_invitro_bf_analysis, {}),
            'Time Series Cellular Object Analysis': (self.central_manager.analysis_methods_ui._switch_to_timeseries_analysis, {'base_data_repository': self.central_manager.active_data_class.data_repository}),
            'Time Series In Vitro Object Analysis (Fluorescence)': (self.central_manager.analysis_methods_ui._switch_to_ts_invitro_fluor_analysis, {}),
            'Z-Stack (3D) Object Analysis': (self.central_manager.analysis_methods_ui._switch_to_zstack_analysis, {}),
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
            'Colocalization Analysis (Pixel-wise + Object-based)': (self.central_manager.analysis_methods_ui._switch_to_coloc_analysis, {'base_data_repository': self.central_manager.active_data_class.data_repository}),
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
            'Pre-Process Image': (self.central_manager.toolbox_functions_ui._add_pre_process, {'separate_widget': True}),
            'Reference / Background Subtraction': (self.central_manager.toolbox_functions_ui._add_run_reference_subtraction, {'separate_widget': True}),
            'Pipeline Step Diagnostics': (self.central_manager.toolbox_functions_ui._add_pipeline_diagnostics, {'separate_widget': True}),
            'Pipeline SNR Analysis': (self.central_manager.toolbox_functions_ui._add_pipeline_snr_analysis, {'separate_widget': True}),
            'Foreground Suppression Tuner': (self.central_manager.toolbox_functions_ui._add_foreground_suppression_tuner, {'separate_widget': True}),
            'Temporal Enhancement Optimizer': (self.central_manager.toolbox_functions_ui._add_temporal_enhancement_optimizer, {'separate_widget': True}),
            'Segmentation Benchmark': (self.central_manager.toolbox_functions_ui._add_segmentation_benchmark, {'separate_widget': True}),
            'Segmentation Speed Comparison': (self.central_manager.toolbox_functions_ui._add_segmentation_speed_comparison, {'separate_widget': True}),
            'Chromatin Topology Map': (self.central_manager.toolbox_functions_ui._add_chromatin_topology, {'separate_widget': True}),
            'Nucleolus / Void Estimator': (self.central_manager.toolbox_functions_ui._add_nucleolus_void_estimator, {'separate_widget': True}),
            'Display Diagnostics': (self.central_manager.toolbox_functions_ui._add_display_diagnostics, {'separate_widget': True}),
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
            'Expand Labels': (self.central_manager.toolbox_functions_ui._add_run_expand_labels, {'separate_widget': True}),
            'Measure Region Properties': (self.central_manager.toolbox_functions_ui._add_run_measure_region_props, {'separate_widget': True})
        }
        self._add_actions_to_menu(label_tools_actions, label_tools_sub_submenu)

        # Create a sub-menu for layer operations    
        layer_operations_submenu = self.toolbox_menu.addMenu('Layer Operations')
        layer_operations_actions = {
            'Simple Multi-Layer Merge': (self.central_manager.toolbox_functions_ui._add_run_simple_multi_merge, {'separate_widget': True}),
            'Advanced 2-Layer Merge': (self.central_manager.toolbox_functions_ui._add_run_advanced_two_layer_merge, {'separate_widget': True}),
            'Mask Operations (AND/OR/XOR)': (self.central_manager.toolbox_functions_ui._add_run_mask_logic_merge, {'separate_widget': True})
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

        # ── Cell and Object Analyses ───────────────────────────────────────────
        condensate_analysis_submenu = self.toolbox_menu.addMenu('Cell and Object Analyses')
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
            'Fibril Analysis (beads, morphometry, graph)': (self.central_manager.toolbox_functions_ui._add_fibril_analysis, {'separate_widget': True}),
        }
        self._add_actions_to_menu(spatial_metrology_actions, spatial_metrology_submenu)

        # ── Advanced Analysis ──────────────────────────────────────────────────
        advanced_analysis_submenu = self.toolbox_menu.addMenu('Advanced Analysis')
        advanced_analysis_actions = {
            'Dynamic Spatial Phenotyping / Tracking': (self.central_manager.toolbox_functions_ui._add_advanced_analysis, {'separate_widget': True}),
            'Condensate Biophysics (MSD, C_sat, Kinetics…)': (self.central_manager.toolbox_functions_ui._add_condensate_physics, {'separate_widget': True}),
        }
        self._add_actions_to_menu(advanced_analysis_actions, advanced_analysis_submenu)

        # ── Molecular Counting (quantitative density / stoichiometry) ───────────
        molecular_counting_submenu = advanced_analysis_submenu.addMenu('Molecular Counting')
        molecular_counting_actions = {
            'Photobleaching Step Counting': (self.central_manager.toolbox_functions_ui._add_molecular_counting, {'separate_widget': True}),
            'SpIDA (density & oligomeric state)': (self.central_manager.toolbox_functions_ui._add_spida, {'separate_widget': True}),
            'Number & Brightness (camera / time-series)': (self.central_manager.toolbox_functions_ui._add_number_and_brightness, {'separate_widget': True}),
        }
        self._add_actions_to_menu(molecular_counting_actions, molecular_counting_submenu)

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
