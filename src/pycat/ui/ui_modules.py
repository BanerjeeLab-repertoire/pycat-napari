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

# ToolboxFunctionsUI moved to toolbox_functions_ui.py (ui_decomposition); re-exported so
# `from pycat.ui.ui_modules import ToolboxFunctionsUI` keeps working. No cycle (it imports base_ui, a leaf).
from pycat.ui.toolbox_functions_ui import ToolboxFunctionsUI  # noqa: F401


# ── Session restore: which method to reopen, and how to rebuild its view ─────────────────────
#
# `active_method` in the manifest is the UI class name that was open when the session was saved. On
# load, `_on_load` maps it to the `_switch_to_*` method that reopens it. A session saved before
# `active_method` was recorded has none, so the method is inferred from a signature dataframe instead.

# A restored dataframe that identifies the method, for sessions predating `active_method`.


# The AnalysisMethodsUI hierarchy moved to analysis_methods_ui.py (ui_decomposition); re-exported so
# `from pycat.ui.ui_modules import AnalysisMethodsUI` (etc.) keeps working. That module imports BaseUIClass
# from base_ui (leaf) -> no cycle.
from pycat.ui.analysis_methods_ui import (  # noqa: F401
    AnalysisMethodsUI, CondensateAnalysisUI, TimeSeriesCondensateUI, ObjectColocAnalysisUI,
    PixelColocAnalysisUI, ColocalizationAnalysisUI, CollapsibleSection, GeneralAnalysisUI, FibrilAnalysisUI)




# MenuManager + its file-drop filter moved to menu_manager (1.6.149); the session-switch maps moved on
# with the session-loader feature to ui/session_loader.py (ui_decomposition Part 2). ALL re-exported.
from pycat.ui.menu_manager import (  # noqa: E402,F401
    MenuManager, _FileDropFilter)
from pycat.ui.session_loader import (  # noqa: E402,F401
    _SESSION_METHOD_SWITCH, _SESSION_METHOD_BY_DATA)
