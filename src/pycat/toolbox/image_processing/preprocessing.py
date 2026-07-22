"""Preprocessing pipeline + shading corrections - split out of image_processing_tools (1.6.253).

pre_process_image is the composite pre-segmentation pipeline (optional soft foreground suppression -> WBNS
wavelet denoise -> CLAHE -> normalisation); apply_flatfield_correction / apply_background_subtraction are
the calibration-frame shading fixes. Moved VERBATIM - no step reordered, no parameter changed; pinned by
test_image_processing_preprocessing_characterization. Composes the background family (soft_foreground_
suppression, wbns_func) and the _base primitives.
"""
from __future__ import annotations

import math
import numpy as np
import skimage as sk
import scipy.ndimage as ndi
from pycat.utils.tag_registry import tags_layer
from pycat.utils.general_utils import dtype_conversion_func
from pycat.toolbox.image_processing._base import _safe_equalize_adapthist, _add_image, _napari
from pycat.toolbox.image_processing.background import soft_foreground_suppression, wbns_func


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
