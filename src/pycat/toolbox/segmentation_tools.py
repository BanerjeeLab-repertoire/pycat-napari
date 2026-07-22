# -*- coding: utf-8 -*-
"""
Image Segmentation and Analysis Module for PyCAT 

This module provides functions for image segmentation and analysis, including local thresholding, watershed segmentation,
felzenszwalb segmentation, cellpose segmentation, random forest pixel classification, and more. These functions are designed 
to process grayscale images and binary masks, segment objects of interest, and extract relevant features for further analysis. 
Segmentation and post-segmentation filtering and processing functions are contained within to ensure accurate and reliable
segmentation results.

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
import numpy as np


from pycat.utils.object_ref import normalise_bbox_columns
from pycat.utils.tag_registry import tags_layer
import skimage as sk
from pycat.utils.general_utils import remove_small_objects_compat as _remove_small_objects_compat
import cv2
import scipy.ndimage as ndi
import scipy.stats as stats
import pandas as pd
# cellpose pulls in torch; imported lazily inside the cellpose functions that need it.
from sklearn.ensemble import RandomForestClassifier
# ── napari and Qt are imported LAZILY, inside the viewer-facing functions ─────
#
# This module holds 16 PURE analysis functions — the puncta refinement filter, local
# thresholding, the SNR/contrast gates, watershed splitting — and a handful of `run_*`
# functions that take a viewer. Importing napari at module scope blocked the headless
# import of ALL of them, so none could be tested in CI. The puncta filter in particular
# (whose SNR gate was found dead in 1.5.416) had never been exercised by a test.
#
# `napari` is used only for `isinstance(layer, napari.layers.Image)` inside the `run_*`
# functions; it is imported there.
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning

# Local application imports
from pycat.toolbox.label_and_mask_tools import binary_morph_operation, opencv_contour_func, extend_mask_to_edges
# `pycat.ui.ui_utils` imports napari — imported lazily inside the `run_*` functions.
from pycat.utils.general_utils import dtype_conversion_func, check_contrast_func
from pycat.utils.math_utils import remove_outliers_iqr
from pycat.toolbox.image_processing_tools import apply_rescale_intensity, rb_gaussian_bg_removal_with_edge_enhancement

# ---------------------------------------------------------------------------
# Cellpose GPU-availability caches  ->  moved to cellpose.py (1.6.241)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Puncta-refinement diagnostic + fast-path flags + _refine_debug_enabled  ->  moved to puncta_refinement.py (1.6.242)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.puncta_refinement import (  # noqa: E402,F401
    _PYCAT_REFINE_DEBUG, _PYCAT_REFINE_FAST, _refine_debug_enabled)



# ---------------------------------------------------------------------------
# Shared _to_uint16_safe dtype helper  ->  moved to _common.py (1.6.240)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation._common import (  # noqa: E402,F401
    _to_uint16_safe)


# ---------------------------------------------------------------------------
# Cellpose GPU/version helpers + model builder + model cache  ->  moved to cellpose.py (1.6.241)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.cellpose import (  # noqa: E402,F401
    available_cellpose_models, default_cellpose_model, _cellpose_major_version, _build_cellpose_model, _CELLPOSE_MODEL_CACHE)






# ---------------------------------------------------------------------------
# Local (windowed) thresholding + run_ wrapper  ->  moved to local_thresholding.py (1.6.240)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.local_thresholding import (  # noqa: E402,F401
    local_thresholding_func, run_local_thresholding)



# ---------------------------------------------------------------------------
# Watershed labeling (skimage + OpenCV marker splitters)  ->  moved to watershed.py (1.6.240)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.watershed import (  # noqa: E402,F401
    apply_watershed_labeling, opencv_watershed_func)



# ---------------------------------------------------------------------------
# Felzenszwalb segmentation + RAG merge + binarization  ->  moved to fz.py (1.6.241)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.fz import (  # noqa: E402,F401
    merge_mean_color, felzenszwalb_segmentation_and_merging, run_fz_segmentation_and_merging, fz_segmentation_and_binarization)



# ---------------------------------------------------------------------------
# Cellpose segmentation + random-forest classifier + contour refine  ->  moved to cellpose.py (1.6.241)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.cellpose import (  # noqa: E402,F401
    cellpose_segmentation, run_cellpose_segmentation, train_and_apply_rf_classifier, refine_labels_with_contours, run_train_and_apply_rf_classifier)



# ---------------------------------------------------------------------------
# Puncta refinement filter (SNR/kurtosis/contrast gate) + ring-radii/bg helpers + fast/slow dispatch  ->  moved to puncta_refinement.py (1.6.242)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.puncta_refinement import (  # noqa: E402,F401
    _local_ring_radii, _ring_masks, _robust_bg, _snr_conditions, _report_refinement_drops, puncta_refinement_filtering_func, puncta_refinement_filtering_func_fast, puncta_refinement_func)



# ---------------------------------------------------------------------------
# Absolute-intensity stats + punctate-signal gate (the RESTORED subsystem)  ->  moved to intensity.py (1.6.240)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.intensity import (  # noqa: E402,F401
    compute_image_intensity_stats, cell_has_punctate_signal)



# ---------------------------------------------------------------------------
# Cell-mask stretching morphology  ->  moved to morphology.py (1.6.240)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.morphology import (  # noqa: E402,F401
    cell_mask_stretching)

# ---------------------------------------------------------------------------
# Subcellular-object segmentation orchestrator + run_ + speed comparison  ->  moved to subcellular.py (1.6.243)
# ---------------------------------------------------------------------------
from pycat.toolbox.segmentation.subcellular import (  # noqa: E402,F401
    segment_subcellular_objects, run_segment_subcellular_objects, _segment_core, compare_segmentation_speed)

