"""Preprocessing pipeline + shading corrections - split out of image_processing_tools (1.6.253).

pre_process_image is the composite pre-segmentation pipeline: normalise -> White Top-Hat -> fixed-sigma LoG
mask -> WBNS wavelet denoise -> morph -> CLAHE -> optional soft foreground suppression; apply_flatfield_
correction / apply_background_subtraction are the calibration-frame shading fixes. Originally moved VERBATIM
(pinned by test_image_processing_preprocessing_characterization); the blob-enhancement block was later
restored to v1.0.0's White Top-Hat + fixed-sigma(3) LoG-mask recipe (Meet Raval: measurably better large-
condensate preservation than the separable-LoG-direct-image approach it replaced) -- see
pre_process_image's inline comment for the full rationale and the characterization pins updated
alongside. Composes the background family (soft_foreground_suppression, wbns_func), the filters family
(apply_laplace_of_gauss_enhancement), and the _base primitives.
"""
from __future__ import annotations

import math
import numpy as np
import skimage as sk
import scipy.ndimage as ndi
from pycat.utils.tag_registry import tags_layer
from pycat.utils.general_utils import dtype_conversion_func
from pycat.toolbox.image_processing._base import _safe_equalize_adapthist, _add_image, _napari, apply_rescale_intensity
from pycat.toolbox.image_processing.background import soft_foreground_suppression, wbns_func
from pycat.toolbox.image_processing.filters import apply_laplace_of_gauss_enhancement


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

    # Blob enhancement: v1.0.0's White Top-Hat -> fixed-sigma LoG-mask recipe,
    # restored VERBATIM (Meet Raval: v1.0.0's Step 1 preserves large condensates
    # noticeably better than the separable-LoG-direct-image approach this
    # replaced -- see preprocessing.py's git history / the 1.0.0 vs current
    # comparison discussion for the measured difference). Three pieces, in the
    # v1.0.0 order:
    #
    #   1. White Top-Hat: isolate bright elements smaller than disk(ball_radius),
    #      rescale to [0.3, 1.0], multiply into img -- so a pixel is attenuated
    #      to AT MOST 70% of itself, never fully removed, which is exactly what
    #      keeps a large condensate's flat interior from being erased even
    #      though the top-hat response there is near zero.
    #   2. LoG at a FIXED sigma=3 (not scaled to ball_radius, unlike the
    #      separable-LoG replacement this restores) -- `apply_laplace_of_gauss_
    #      enhancement` (filters.py) returns the inverted LoG response as a
    #      [~0.9, 1.0]-ish attenuation MASK, not a direct image.
    #   3. That mask multiplies `top_hat_enhanced_img` (not the raw `img`) to
    #      produce LoG_enhanced_img -- WBNS below sees top-hat AND LoG AND the
    #      (normalised) original image all multiplied together, exactly as
    #      v1.0.0's WBNS input was, not the LoG response alone.
    #
    # `_tophat_radius` guards against a real, confirmed failure: ndi.white_tophat
    # with a large disk footprint has the identical catastrophic scaling as the
    # binary morphology MemoryError already found and fixed this session in
    # _bridge_fragmented_rims (fz.py) -- measured directly: disk(90) on an
    # 800x800 array did not return in 40s. GUI (run_pre_process_image) and
    # batch-condensate-replay (preprocessing_steps.py) already cap ball_radius
    # to 5% of the image's smaller dimension before it reaches here, so this is
    # a no-op there; it closes the same gap for callers that don't pre-cap
    # (in-vitro batch/GUI, time-series) without altering ball_radius for
    # anything else in this function (soft_foreground_suppression's own LoG
    # sigma below still scales off the UNCAPPED ball_radius, as before).
    _tophat_radius = min(int(ball_radius), max(4, int(min(img.shape[-2:]) * 0.05)))
    white_top_hat_img = ndi.white_tophat(img, footprint=sk.morphology.disk(_tophat_radius))
    rescaled_top_hat = apply_rescale_intensity(white_top_hat_img, out_min=0.3, out_max=1.0)
    top_hat_enhanced_img = rescaled_top_hat * img

    _, inverted_LoG_img = apply_laplace_of_gauss_enhancement(img, sigma=3)
    LoG_enhanced_img = inverted_LoG_img * top_hat_enhanced_img

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
    _pre_layer = _add_image(pre_processed_image, viewer, name=f"Pre-Processed {active_layer.name}",
                            operation='preprocess')
    # Record lineage: the pre-processed image is derived FROM active_layer via pre_process_image, so a
    # later segmentation step querying head-of-lineage finds the right source image.
    try:
        from pycat.utils.tag_registry import tag_from_operation
        tag_from_operation(_pre_layer, pre_process_image, source_layer=active_layer)
    except Exception:  # broad-ok: optional_probe — lineage metadata is auxiliary; never block the result
        pass


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
