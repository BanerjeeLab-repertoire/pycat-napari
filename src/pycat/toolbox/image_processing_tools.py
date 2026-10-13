"""
Image Processing Module for PyCAT

This module contains functions for image processing tasks, including image adjustments, enhancements, and filters. 
Most functions are decomposed into a function which actually performs the processing and a function which interacts
with the Napari viewer. This separation allows for easier testing and debugging of the processing functions. It also
allows future users to use the processing functions without the Napari viewer if needed, or to add the functions to 
Napari as plugins, providing flexibility and reusability.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Standard library imports
import math
import warnings

# Third party imports
import numpy as np

from pycat.utils.tag_registry import tags_layer
import skimage as sk
# GUI is imported LAZILY. This module's pure array operations (filters, background
# removal, upscaling, intensity rescaling) are imported by other SCIENTIFIC modules --
# feature_analysis_tools among them -- and a top-level `import napari` made every one
# of them, and their tests, un-importable without a display. The coupling is
# TRANSITIVE: one GUI import at the base of the graph blocks everything above it.
# Verified: 4 of 6 science test modules could not even be COLLECTED without napari/Qt.
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.notify import show_info as napari_show_info


# ---------------------------------------------------------------------------
# napari image-add helper  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    _add_image)



# ---------------------------------------------------------------------------
# lazy napari accessor  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    _napari)

import scipy.ndimage as ndi
from scipy.interpolate import RectBivariateSpline
import SimpleITK as sitk

# Local application imports
from pycat.utils.general_utils import dtype_conversion_func, get_default_intensity_range 
# ui_utils pulls in Qt -> imported at CALL time inside _add_image().


# ---------------------------------------------------------------------------
# version-safe CLAHE (equalize_adapthist)  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    _safe_equalize_adapthist)






# ---------------------------------------------------------------------------
# Pseudo-3D (tri-planar) linear filtering for Z-stack volumes
# ---------------------------------------------------------------------------
#
# For a genuinely 2D linear filter (Gaussian smoothing, Gabor convolution,
# LoG/DoG blob detection), applying it slice-by-slice down a Z-stack's XY
# planes only accounts for structure within each optical plane — Z-direction
# continuity is ignored entirely, which can leave abrupt slice-to-slice
# discontinuities in the filtered volume.
#
# Tri-planar pseudo-3D filtering runs the *same* 2D kernel three times —
# once slicing along XY (the standard per-plane pass), once along XZ, and
# once along YZ — then averages the three volumes (equivalently: sums the
# three contributions and scales by 1/3). Each pass is a cheap, well-tested
# 2D filter; averaging the three orthogonal responses gives a result that
# is sensitive to structure in all three spatial directions without the
# cost or complexity of a true N-D filter implementation. This is standard
# practice for approximating isotropic 3D response from 2D building blocks
# (e.g. tri-planar Hessian/Frangi approximations, tri-planar LBP texture).
#
# Only apply this to genuinely LINEAR filters (Gaussian, Gabor, LoG/DoG).
# Nonlinear operations — CLAHE, rolling-ball, bilateral filtering,
# watershed, morphological tophat — do not have the same orthogonal-
# averaging justification and should stay per-XY-slice only.

# ---------------------------------------------------------------------------
# pseudo-3D tri-planar filter wrapper  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    pseudo3d_tri_planar_filter)



# ---------------------------------------------------------------------------
# 2D/pseudo-3D Gaussian + Gabor + DoG blob-enhance filters  ->  moved to filters.py (1.6.251)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.filters import (  # noqa: E402,F401
    gaussian_smooth_2d, gaussian_smooth_3d_pseudo, gabor_filter_3d_pseudo, dog_blob_enhance_2d, dog_blob_enhance_3d_pseudo)



# Image adjustments #

# ---------------------------------------------------------------------------
# intensity rescaling (registered op)  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    apply_rescale_intensity)



# ---------------------------------------------------------------------------
# Interactive intensity-rescale wrapper  ->  moved to upscaling.py (1.6.253)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.upscaling import (  # noqa: E402,F401
    run_apply_rescale_intensity)



# ---------------------------------------------------------------------------
# image inversion (registered op)  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    invert_image)



# ---------------------------------------------------------------------------
# Interactive image-inversion wrapper  ->  moved to upscaling.py (1.6.253)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.upscaling import (  # noqa: E402,F401
    run_invert_image)



# ---------------------------------------------------------------------------
# bicubic upscaling (registered op)  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    upscale_image_interp)




# ---------------------------------------------------------------------------
# Interactive bicubic-upscaling workflow  ->  moved to upscaling.py (1.6.253)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.upscaling import (  # noqa: E402,F401
    run_upscaling_func)



# Enhancements and Filters # 


# ---------------------------------------------------------------------------
# Gabor + peak/edge + Laplacian-of-Gaussian + morphological-gaussian + CLAHE filters  ->  moved to filters.py (1.6.251)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.filters import (  # noqa: E402,F401
    gabor_filter_func, peak_and_edge_enhancement_func, run_peak_and_edge_enhancement, apply_laplace_of_gauss_filter, apply_laplace_of_gauss_enhancement, run_apply_laplace_of_gauss_filter, run_morphological_gaussian_filter, run_clahe)



# ---------------------------------------------------------------------------
# Deblurring by pixel reassignment (DPR) + run wrapper  ->  moved to deblur.py (1.6.250)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.deblur import (  # noqa: E402,F401
    deblur_by_pixel_reassignment, run_dpr)



# Background and Noise Correction # 

# ---------------------------------------------------------------------------
# Background + noise removal (rolling-ball/Gaussian, WBNS wavelet, soft foreground suppression) + wrappers  ->  moved to background.py (1.6.252)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.background import (  # noqa: E402,F401
    background_inpainting_func, compute_rolling_ball_background, subtract_background, rb_gaussian_background_removal, run_rb_gaussian_background_removal, rb_gaussian_bg_removal_with_edge_enhancement, _realness_weight, soft_foreground_suppression, run_enhanced_rb_gaussian_bg_removal, wavelet_bg_and_noise_calculation, wbns_func, run_wbns, run_wavelet_noise_subtraction, FOREGROUND_SUPPRESSION_DEFAULTS)


# ---------------------------------------------------------------------------
# Edge-preserving bilateral filter  ->  moved to filters.py (1.6.251)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.filters import (  # noqa: E402,F401
    apply_bilateral_filter, run_apply_bilateral_filter)



# ---------------------------------------------------------------------------
# Composite preprocessing pipeline + run wrapper  ->  moved to preprocessing.py (1.6.253)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.preprocessing import (  # noqa: E402,F401
    pre_process_image, run_pre_process_image)



# ---------------------------------------------------------------------------
# Calibration-frame flatfield + background-subtraction corrections  ->  moved to preprocessing.py (1.6.253)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.preprocessing import (  # noqa: E402,F401
    apply_flatfield_correction, apply_background_subtraction)



# ---------------------------------------------------------------------------
# Automatic object-size estimation (top-hat/Otsu + brightfield variant) + validity gate  ->  moved to size_estimation.py (1.6.248)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.size_estimation import (  # noqa: E402,F401
    AUTO_OBJECT_SIZE_VALID_WORKFLOWS, auto_object_size_valid, estimate_object_size_px, estimate_object_size_px_brightfield)

