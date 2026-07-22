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



def run_apply_rescale_intensity(out_min_input, out_max_input, viewer):
    """
    Applies intensity rescaling to the currently active image layer in a Napari viewer based on user-specified
    minimum and maximum output intensity values.

    This function interacts with a Napari viewer, allowing users to rescale the intensity of the selected image
    layer directly through UI elements for specifying the intensity range. The adjusted image is then displayed
    in the viewer.

    Parameters
    ----------
    out_min_input : UI Element
        A UI element for user input of the minimum output intensity value. Typically a text input field.
    out_max_input : UI Element
        A UI element for user input of the maximum output intensity value. Typically a text input field.
    viewer : napari.Viewer
        The Napari viewer instance where the image layers are managed and displayed.

    Raises
    ------
    Error
        If no active image layer is selected or if the active layer is not compatible as an image layer for processing.

    Notes
    -----
    It is assumed that `out_min_input` and `out_max_input` are components of a graphical user interface and can retrieve
    textual input from the user, which is then converted to floating point values for the rescaling function. 

    Error handling for digit inputs should be added. For example:

    .. code-block:: python

        # Ensure the input is valid and convert to an integer
        if new_label_input.text() == "" or not new_label_input.text().isdigit():
            print("Please enter a valid label value.")
            return
    """


    active_layer = viewer.layers.selection.active
    # Check if their is an active layer, and that it is a Napari image layer
    if active_layer is not None and isinstance(active_layer, _napari().layers.Image):
        image = active_layer.data
    else:
        napari_show_warning("No active image layer selected.")
        return

    
    # Use the user provided values if available, otherwise use the defaults
    out_min = float(out_min_input.text()) if out_min_input.text() else None
    out_max = float(out_max_input.text()) if out_max_input.text() else None
    
    # Apply the rescale intensity function to the image
    rescaled_image = apply_rescale_intensity(image, out_min, out_max)

    # Add the rescaled image to the viewer
    _add_image(rescaled_image, viewer, name=f"Intensity Rescaled {active_layer.name}", operation='rescale_intensity')


# ---------------------------------------------------------------------------
# image inversion (registered op)  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    invert_image)



def run_invert_image(viewer):
    """
    Inverts the intensity of the currently active image layer in the Napari viewer using PyQt GUI components.

    This function retrieves the currently selected image layer from the Napari viewer, inverts its intensity,
    and displays the result as a new layer in the viewer. This is particularly useful for enhancing visual 
    contrast or highlighting specific features in biophysical imaging data.

    Parameters
    ----------
    viewer : napari.Viewer
        The Napari viewer instance, used for displaying and processing the image layers. The viewer is expected
        to be part of a PyQt application, integrating seamlessly with other GUI components.

    Raises
    ------
    Error
        If no active image layer is selected, or if the selected layer is not suitable for processing (e.g., not an image layer).
    """

    active_layer = viewer.layers.selection.active
    
    # Check if their is an active layer, and that it is a Napari image layer
    if active_layer is not None and isinstance(active_layer, _napari().layers.Image):
        image = active_layer.data
    else:
        raise ValueError("No active image layer selected.")
    
    # Apply the invert image function to the rescaled LoG filtered image
    inverted_image = invert_image(image)

    # Add the inverted image to the viewer
    _add_image(inverted_image, viewer, name=f"Inverted {active_layer.name}", operation='rescale_intensity')


# ---------------------------------------------------------------------------
# bicubic upscaling (registered op)  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    upscale_image_interp)




def run_upscaling_func(data_checkbox, data_instance, viewer):
    """
    Applies the upscale_image_interp function to selected image layers within a viewer interface, potentially
    updating related data in the data_instance based on the upscaling process and user input. It iterates over 
    selected image layers, applies upscaling, and optionally updates related data in the data_instance. The result 
    is added back to the viewer as a new image layer.

    Parameters
    ----------
    viewer : napari.Viewer
        The viewer object that contains the image layers to be upscaled.
    data_instance : DataInstance
        An object containing data and parameters that may need updating based on the image upscaling (e.g., object
        sizes, resolution information).
    data_checkbox : Checkbox
        A user interface element indicating whether to update certain data in the data_instance based on the upscaling
        results.

    Notes
    -----
    The function ensures that upscaling does not apply to images already at or above a specific size threshold (2048x2048).
    It also corrects data types and ranges for upscaled images to ensure they are suitable for further processing or
    display within the viewer. This function interacts directly with the Napari viewer's GUI elements, facilitating
    a seamless user experience in adjusting image properties.
    """

    # Get the currently selected layers from the viewer
    # Snapshot the selection BEFORE the loop as a plain list filtered to Image
    # layers only. viewer.layers.selection is a live set — napari auto-selects
    # each newly added layer, which mutates the set mid-iteration and causes
    # every upscaled output to be upscaled again, producing duplicate layers.
    import napari.layers as _nl_up
    # Snapshot + de-duplicate. viewer.layers.selection is a live set that napari
    # mutates as new layers are added (auto-selecting them); we snapshot to a list
    # up front. We also de-duplicate by layer identity in case the same layer
    # appears more than once, and we skip any layer whose upscaled output already
    # exists — both of which otherwise produce two identical "Upscaled ..." layers
    # from a single source.
    _seen_ids = set()
    selected_layers = []
    for l in list(viewer.layers.selection):
        if not isinstance(l, _nl_up.Image):
            continue
        if id(l) in _seen_ids:
            continue
        _seen_ids.add(id(l))
        selected_layers.append(l)

    # Check if no image layers are selected and exit if true
    if not selected_layers:
        napari_show_warning("No image layers selected. Select one or more Image layers.")
        return

    # Determine whether the user has requested data updates based on the checkbox state
    update_data = data_checkbox.isChecked()

    # Loop through the snapshotted Image-layer list
    for layer in selected_layers:
        # Skip the iteration if the layer is None for some reason
        if layer is None:
            continue

        # Guard against producing a duplicate output. If an "Upscaled {name}"
        # layer already exists (e.g. the user clicked twice, or re-ran on a
        # selection that includes a prior output), skip rather than add a second
        # identical layer.
        _out_name = f"Upscaled {layer.name}"
        if _out_name in viewer.layers:
            napari_show_warning(
                f"'{_out_name}' already exists — skipping to avoid a duplicate.")
            continue

        # Copy the layer data to prevent modifying the original data directly
        image = layer.data.copy()
        # Retrieve the initial dimensions of the image
        num_row_initial, num_col_initial = image.shape

        # Decide not to upscale images already at or above a certain size threshold
        if num_row_initial >= 2048 or num_col_initial >= 2048:
            upscale_factor = 1  # Set the upscale factor to 1, effectively making no change
            update_data = False  # Prevent updates to data_instance for large images
            napari_show_warning("Max resolution is 2048 x 2048. Micron sizes have not been updated.")
            continue
        else:
            # v1.0.0 behaviour: 2× linear upscaling before preprocessing.
            upscale_factor = 2

        # Apply the upscaling function to the image
        upscaled_img = upscale_image_interp(image, num_row_initial, num_col_initial, upscale_factor=upscale_factor)

        # Prepare the upscaled image data for safe use in the viewer
        data = upscaled_img.copy()
        # Ensure integer data types are within a valid range, correcting them if necessary
        if np.issubdtype(data.dtype, np.integer):
            if np.min(data) < 0 or np.max(data) > 2**16 - 1:
                # Give 5% of the range as a buffer which is clipped rather than rescaled 
                if np.min(data) < (0 - (0.05 * 2**16)) or np.max(data) > (2**16 + (0.05 * 2**16)):
                    data = np.clip(data, 0, 2**16 - 1).astype(np.uint16)
                else:
                    data = apply_rescale_intensity(data, out_min=0, out_max=2**16 - 1).astype(np.uint16)
        # Ensure floating-point data types are within a valid range, correcting them if necessary
        elif np.issubdtype(data.dtype, np.floating):
            if np.min(data) < 0 or np.max(data) > 1.0: 
                # Give 5% of the range as a buffer which is clipped rather than rescaled 
                if np.min(data) < (0 - (0.05)) or np.max(data) > (1.0 + (0.05)):
                    data = np.clip(data, 0.0, 1.0).astype(np.float32)
                else:
                    data = apply_rescale_intensity(data, out_min=0.0, out_max=1.0).astype(np.float32)
        else: 
            data = apply_rescale_intensity(data, out_min=0.0, out_max=1.0).astype(np.float32)

        upscaled_img = data.copy()

        # Update relevant data in the data_instance for the first layer processed only if requested
        if update_data:
            # Calculate new dimensions and scale factors based on the upscaling
            scale_factor_width, scale_factor_height = upscaled_img.shape[0] / image.shape[0], upscaled_img.shape[1] / image.shape[1]
            square_scale_factor = scale_factor_width * scale_factor_height
            linear_scale_factor = np.sqrt(square_scale_factor)

            # Update object sizes, ball radius, and cell diameter in the data_instance based on scale factors
            data_instance.data_repository['object_size'] *= linear_scale_factor
            data_instance.data_repository['cell_diameter'] *= linear_scale_factor
            data_instance.data_repository['ball_radius'] *= linear_scale_factor

            # Update the microns per pixel squared resolution in the data_instance
            data_instance.data_repository['microns_per_pixel_sq'] /= square_scale_factor

            # After updating, ensure no further updates are made for subsequent layers
            update_data = False

        # Add the processed and potentially upscaled image back into the viewer
        # Align the upscaled layer physically with its source: it has
        # `upscale_factor`× more pixels over the SAME field of view, so its scale
        # must be the source scale divided by the actual upscale ratio. Without
        # this the upscaled layer (at scale 1) renders far larger than the source
        # (which may carry a µm scale), making the source look "embedded" in it.
        # Compute the physically-aligned scale for the upscaled layer. A failure
        # here must fall back to a plain add — but the add itself is done ONCE,
        # outside the try, so a later notification error cannot cause a second add
        # (the previous structure put the add and the show_info in the same try,
        # so a show_info failure fell into except and added the layer AGAIN,
        # producing two identical upscaled layers).
        try:
            _src_scale = [float(s) for s in layer.scale]
            _ry = upscaled_img.shape[0] / image.shape[0]
            _rx = upscaled_img.shape[1] / image.shape[1]
            _new_scale = list(_src_scale)
            if len(_new_scale) >= 2 and np.isfinite(_ry) and _ry > 0 and np.isfinite(_rx) and _rx > 0:
                _new_scale[-2] = _src_scale[-2] / _ry
                _new_scale[-1] = _src_scale[-1] / _rx
        except Exception:
            _src_scale = None
            _new_scale = None

        # Single add, exactly once per source layer.
        if _new_scale is not None:
            _add_image(upscaled_img, viewer,
                                            name=_out_name, scale=_new_scale)
        else:
            _add_image(upscaled_img, viewer, name=_out_name, operation='upscale')

        # Lineage: the upscaled layer is derived_from + supersedes the source, and
        # inherits its role/modality/channel. This is what makes autopopulation
        # prefer the upscaled version (head of lineage) automatically.
        try:
            from pycat.utils import layer_tags as _LT
            if len(viewer.layers):
                _LT.mark_derived(viewer.layers[-1], layer, via='upscale')
        except Exception:
            pass

        # Notification is best-effort and isolated: any failure here must not
        # affect the layer that was already added.
        try:
            _oh, _ow = image.shape[-2], image.shape[-1]
            _nh, _nw = upscaled_img.shape[-2], upscaled_img.shape[-1]
            _mpp = f"{_src_scale[-1]:.3f} µm/px " if _src_scale else ""
            napari_show_info(
                f"Upscaled \"{layer.name}\": {_ow}×{_oh} → {_nw}×{_nh} px "
                f"({upscale_factor}× linear, same {_mpp}world extent). "
                f"Both layers occupy the same field of view — zoom in on "
                f"\"{_out_name}\" to see the finer pixel grid. "
                f"The scale bar updates when you click a different layer.")
        except Exception:
            pass


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



@tags_layer('preprocess', role='preprocessed',
            summary='The standard preprocessing cascade')
def pre_process_image(image, ball_radius, window_size,
                      suppress_foreground=True, suppression_params=None,
                      norm_max=None):
    """
    Enhances features in an image through a comprehensive pre-processing pipeline that includes noise reduction,
    feature enhancement, and contrast improvement. This function is tailored for images where maintaining 
    feature integrity and detail is crucial, such as in microscopic imaging.

    Parameters
    ----------
    image : numpy.ndarray
        The input image array to be processed.
    ball_radius : int
        The radius used for the disk element in the White Top Hat filter and other morphological operations.
    window_size : int
        The window size used for CLAHE, influencing how contrast is adapted locally in the image.
    suppress_foreground : bool, optional
        If True (default), a final foreground-suppression step attenuates noise-like
        pixels (diffuse texture and single-pixel fluctuations) while preserving the
        nucleoplasm baseline and real puncta. This restores usable preprocessing
        output for condensate detection; without it, the raw CLAHE output leaves
        the diffuse noise tier at full strength. Set False to get the pre-1.5.128
        output (CLAHE result with no suppression).
    suppression_params : dict, optional
        Overrides for the suppression parameters (`strength`, `log_p`, `con_p`,
        `min_area`). Any key omitted falls back to
        ``FOREGROUND_SUPPRESSION_DEFAULTS``. Ignored if ``suppress_foreground`` is
        False.

    Returns
    -------
    output_image : numpy.ndarray
        The pre-processed image, converted back to its original data type, with enhanced features and reduced noise.

    Notes
    -----
    The pre-processing pipeline includes the following steps:
    - Converting image data type to float32 for processing.
    - Applying a White Top Hat filter to highlight bright elements smaller than the footprint.
    - Enhancing the image using a Laplacian of Gaussian filter.
    - Removing background and noise using a custom wavelet based noise and backkgroud removal function.
    - Performing erosion and dilation for noise reduction.
    - Applying Gaussian filter for smoothing.
    - Enhancing contrast using CLAHE (Contrast Limited Adaptive Histogram Equalization).
    """

    # ── CPU path ─────────────────────────────────────────────────────────
    input_dtype = str(image.dtype)  # Store original image data type for conversion back after processing
    img = dtype_conversion_func(image, output_bit_depth='float32') # Convert image data type to float32 for processing
    # Normalise to [0, 1] by the actual image maximum BEFORE any processing.
    # sk.util.img_as_float32 divides by 65535, so a uint16 image that only
    # uses values up to ~3000 (a typical dim condensate image) arrives as
    # float32 with a maximum of ~0.046. Every subsequent multiplicative step
    # (white-top-hat rescale, DoG, WBNS wavelet thresholding) is tuned for
    # [0, 1] but receives [0, 0.046], causing near-total signal suppression.
    _pp_max = float(img.max())
    # For a time-series, every frame must be normalised by the SAME scale or a
    # brightening focus makes later frames appear dimmer (the per-frame max, the
    # denominator, rises with the signal). Callers processing a stack pass a
    # fixed norm_max (the stack's global max); 2D callers leave it None and get
    # the original per-frame behaviour unchanged.
    if norm_max is not None and float(norm_max) > 0:
        _pp_max = float(norm_max)
    if _pp_max > 0:
        img = img / _pp_max

    # Blob enhancement via separable LoG (Laplacian of Gaussian) with sigma
    # scaled to ball_radius.
    #
    # Quantitative SNR analysis on real condensate data (GFP channel):
    #   raw /max:              within-nucleus SNR =     8
    #   LoG(σ=ball_radius×0.27): within-nucleus SNR = 2917  (×360 gain)
    #
    # Speed optimisations validated against the float64 gaussian_laplace
    # reference on 2048×2048 images at ball_radius=15 and ball_radius=50:
    #
    #   gaussian_laplace f64 (old)   1.00×  corr=1.000  SNR=430  ← reference
    #   gaussian_laplace f32         1.15×  corr=1.000  SNR=430  ← safe
    #   separable LoG f32 (this)     1.54×  corr=0.9999 SNR=429  ← adopted
    #   DoG fixed σ=2.0,3.2 (old)   1.37×  corr=0.904  SNR=224  ← DO NOT USE
    #   DoG scaled (0.15,0.25)       1.43×  corr=0.948  SNR=268  ← DO NOT USE
    #
    # Separable LoG: Gaussian(σ) then discrete Laplacian in each axis.
    # 1.54× faster than gaussian_laplace, corr=0.9999 on real data, SNR
    # within 0.1% of reference at both ball_radius=15 and ball_radius=50.
    # All arithmetic in float32 — precision is sufficient for this application.
    #
    # Rule: sigma = ball_radius × 0.27 (matches v1.0.0 LoG(σ=3) at br≈11px).
    _log_sigma = max(1.0, ball_radius * 0.27)
    _g = ndi.gaussian_filter(img.astype(np.float32), sigma=_log_sigma)
    _lap = np.zeros_like(_g)
    for _ax in range(img.ndim):
        _lap += ndi.uniform_filter1d(_g, size=3, axis=_ax, mode='reflect') * 2 - 2 * _g
    inverted_LoG_img = np.clip(-_lap, 0, None).astype(np.float32)
    if inverted_LoG_img.max() > 0:
        inverted_LoG_img /= inverted_LoG_img.max()
    LoG_enhanced_img = inverted_LoG_img  # direct LoG, no multiplicative suppression

    # Parameters for background and noise removal
    psf_res = 4  # Point Spread Function resolution
    noise_lvl = 1  # Noise level
    # Remove background and noise using WBNS function
    WBNS_img, _ = wbns_func(LoG_enhanced_img, psf_res, noise_lvl)

    # Noise reduction through morphological operations
    img = WBNS_img.copy()
    selem = sk.morphology.disk(1)  # Structuring element for erosion and dilation
    img = ndi.grey_erosion(img, footprint=selem)
    img = ndi.grey_dilation(img, footprint=selem)

    # Apply Gaussian filter for image smoothing
    img = ndi.gaussian_filter(img, 1)

    # Apply CLAHE for contrast enhancement. The tile is scaled to the user's
    # window_size (v1.0.0 behavior) rather than a fixed 64-px tile: a fixed
    # tile smaller than the chosen window is more aggressive (more local
    # equalization) and ignores the window_size control, over-enhancing
    # background texture and suppressing low-contrast puncta.
    clip_limit = 0.0025
    k_size = math.ceil(window_size)
    img = _safe_equalize_adapthist(img, kernel_size=k_size,
                                          clip_limit=clip_limit)

    # Foreground suppression (1.5.128): attenuate noise-like foreground while
    # preserving the nucleoplasm baseline and real puncta. Applied here in the
    # core so every consumer (button, batch replay, subcellular segmentation)
    # receives the corrected output. Operates in the current float32 [0,1]-ish
    # space; the function normalises internally, so scale is preserved.
    if suppress_foreground:
        sp = suppression_params or {}
        img = soft_foreground_suppression(
            img, ball_radius,
            strength=sp.get('strength'),
            log_p=sp.get('log_p'),
            con_p=sp.get('con_p'),
            min_area=sp.get('min_area'),
            border_grow=sp.get('border_grow'),
        )

    # Convert the processed image back to its original data type
    output_image = dtype_conversion_func(img, output_bit_depth=input_dtype)

    return output_image


def run_pre_process_image(data_instance, viewer):
    """
    Run the pre-processing function on an image selected in a viewer interface. This function handles the selection 
    of an active image layer, retrieves necessary parameters from a data instance, applies the pre-processing, and 
    then adds the processed image back to the viewer.

    Parameters
    ----------
    viewer : napari.Viewer
        The Napari viewer instance where the image layers are managed.
    data_instance : object
        An object containing the data repository with parameters such as ball radius and window size for the pre-processing.

    Raises
    ------
    Error
        If no active image layer is selected.

    Notes
    -----
    Retrieves necessary parameters from the data_instance, applies a comprehensive pre-processing pipeline to the selected
    image, and displays the enhanced image as a new layer in the viewer. This allows users to immediately observe and analyze the
    effects of the pre-processing on the original image.
    """

    # Check for an active image layer in the viewer
    active_layer = viewer.layers.selection.active
    if active_layer is None or not isinstance(active_layer, _napari().layers.Image):
        raise ValueError("No active image layer selected")
    
    # Retrieve the image and parameters for pre-processing from the data instance
    image = active_layer.data
    ball_radius = int(data_instance.data_repository['ball_radius'])
    window_size = int(data_instance.data_repository['cell_diameter']) // 2

    # Cap ball_radius relative to image size to prevent MemoryError on large/upscaled images.
    # disk(r) creates a (2r+1)^2 footprint; scipy needs ~8x that in RAM for white_tophat.
    # Limit to 5% of the smallest image dimension as a safe upper bound.
    max_radius = max(4, int(min(image.shape[-2:]) * 0.05))
    if ball_radius > max_radius:
        print(f"[PyCAT] ball_radius {ball_radius} capped to {max_radius} for image shape {image.shape}")
        ball_radius = max_radius
    window_size = min(window_size, max_radius * 2)

    # Foreground-suppression settings. Defaults are always applied; the
    # preprocessing widget's "Adjust foreground suppression" checkbox may store
    # overrides in the data repository under 'foreground_suppression_params'.
    # A stored value of None/absent -> use FOREGROUND_SUPPRESSION_DEFAULTS.
    suppression_params = data_instance.data_repository.get(
        'foreground_suppression_params', None)
    suppress_foreground = data_instance.data_repository.get(
        'suppress_foreground', True)

    # Apply pre-processing to the selected image
    pre_processed_image = pre_process_image(
        image, ball_radius, window_size,
        suppress_foreground=suppress_foreground,
        suppression_params=suppression_params)

    # Add the pre-processed image to the viewer with a default colormap
    _add_image(pre_processed_image, viewer, name=f"Pre-Processed {active_layer.name}",
               operation='preprocess')


# ---------------------------------------------------------------------------
# Calibration-frame background correction
# ---------------------------------------------------------------------------
# Empirical correction using a separately-acquired reference (free dye / flat
# field, or a clear no-condensate frame). The reference is specific to a
# microscope + settings + sample combination, so it is loaded once and applied
# to matching data rather than derived per-dataset.

@tags_layer('flatfield', role='preprocessed',
            summary='Flat-field (illumination) correction')
def apply_flatfield_correction(image, flat, dark=None):
    """
    Flat-field (illumination) correction for a free-dye / flat reference.

    Removes MULTIPLICATIVE non-uniformity (vignetting, uneven excitation):

        corrected = (image - dark) / (flat - dark) * mean(flat - dark)

    The ``* mean(...)`` term restores the original intensity level so the result
    stays in a comparable range. ``dark`` (a camera dark/offset frame) is
    optional. Works on a single 2D image or a (T/Z, H, W) stack — the 2D
    reference broadcasts across frames.
    """
    img = np.asarray(image, dtype=np.float32)
    flt = np.asarray(flat, dtype=np.float32)
    if dark is not None:
        drk = np.asarray(dark, dtype=np.float32)
        num = img - drk
        den = flt - drk
    else:
        num = img
        den = flt
    den_mean = float(np.mean(den))
    if den_mean == 0:
        den_mean = 1.0
    # Guard against divide-by-zero in dark pixels of the reference.
    den_safe = np.where(np.abs(den) < 1e-6, den_mean, den)
    corrected = num / den_safe * den_mean
    return corrected.astype(np.float32)


@tags_layer('bg_subtract_clear', role='preprocessed',
            summary='Additive background subtraction from a clear frame')
def apply_background_subtraction(image, background):
    """
    Additive background subtraction for a clear-frame (no-condensate) reference.

        corrected = clip(image - background, 0, None)

    Use when the background is additive (stray light, autofluorescence floor,
    fixed-pattern offset). The 2D reference broadcasts across a (T/Z, H, W) stack.
    """
    img = np.asarray(image, dtype=np.float32)
    bg = np.asarray(background, dtype=np.float32)
    corrected = np.clip(img - bg, 0, None)
    return corrected.astype(np.float32)


# ---------------------------------------------------------------------------
# Automatic object-size estimation (top-hat/Otsu + brightfield variant) + validity gate  ->  moved to size_estimation.py (1.6.248)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.size_estimation import (  # noqa: E402,F401
    AUTO_OBJECT_SIZE_VALID_WORKFLOWS, auto_object_size_valid, estimate_object_size_px, estimate_object_size_px_brightfield)

