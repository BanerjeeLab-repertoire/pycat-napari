"""The AnalysisMethodsUI hierarchy — the condensate/coloc/general/timeseries analysis panels,
split out of ui_modules.py (ui_decomposition).

The whole hierarchy (AnalysisMethodsUI + every subclass, including TimeSeriesCondensateUI, plus the
CollapsibleSection helper) lives here TOGETHER rather than in a separate timeseries module:
TimeSeriesCondensateUI inherits AnalysisMethodsUI AND the family references TimeSeriesCondensateUI,
so one module keeps that mutual dependency cycle-free. ui_modules re-exports every class here.
"""
from __future__ import annotations

import napari
from PyQt5.QtWidgets import QDoubleSpinBox, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QRadioButton, QPushButton, QLineEdit, QWidget, QComboBox, QSlider, QScrollArea, QSizePolicy, QAction, QTabWidget, QToolButton, QFrame
from PyQt5.QtCore import Qt, QObject
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
from pycat.data.data_modules import BaseDataClass
from pycat.ui.base_ui import BaseUIClass, _relax_min_widths, _apply_scroll_guard


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
        #
        # Optional standalone alternative, requested by Meet Raval: the merged button above always
        # runs enhanced background removal CHAINED AFTER `pre_process_image`'s soft-foreground-
        # suppression step (suppress_foreground=True), on the resulting "Pre-Processed" layer. This
        # reuses the same underlying widget (`_add_run_enhanced_rb_gaussian_bg_removal` /
        # `rb_gaussian_bg_removal_with_edge_enhancement` — rolling-ball + Gaussian background removal,
        # then Gabor peak/edge enhancement) but run directly against whichever layer is active, so it
        # can be pointed at the raw/upscaled image instead and never passes through suppression at
        # all. No `_stage_step` call: this doesn't replace or renumber Steps 4-5, it's an extra,
        # optional entry point for when foreground suppression is suspected of attenuating real
        # diffuse signal (see the puncta-refinement diffuse-object investigation).
        self.central_manager.toolbox_functions_ui._add_run_enhanced_rb_gaussian_bg_removal(layout=self.condensate_layout)
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
                                      QLabel)
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
