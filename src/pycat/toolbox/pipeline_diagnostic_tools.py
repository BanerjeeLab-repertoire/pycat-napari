"""
Pipeline Diagnostic Tools — step-by-step layer outputs for both the current and
v1.0.0 preprocessing / enhanced background-removal pipelines, so you can see
exactly where the two diverge.
"""
import math
import numpy as np
import skimage as sk
import scipy.ndimage as ndi


# ---------------------------------------------------------------------------
# ── Helpers shared by both pipelines ────────────────────────────────────────
# ---------------------------------------------------------------------------

def _to_float_norm(image):
    """Convert to float32 and normalise to [0, 1] by the actual image max.
    This is the fix introduced in 1.5.116 — 1.0.0 did NOT normalise here."""
    from pycat.utils.general_utils import dtype_conversion_func
    img = dtype_conversion_func(image, 'float32')
    m = float(img.max())
    return (img / m) if m > 0 else img

def _to_float_raw(image):
    """Convert to float32 WITHOUT normalising (1.0.0 behaviour — divides uint16
    by 65535 via img_as_float32, leaving dim images in range ~[0, 0.05])."""
    from pycat.utils.general_utils import dtype_conversion_func
    return dtype_conversion_func(image, 'float32')

def _safe_clahe(img, **kw):
    from pycat.toolbox.image_processing_tools import _safe_equalize_adapthist
    return _safe_equalize_adapthist(img, **kw)


# ---------------------------------------------------------------------------
# ── CURRENT preprocessing — one output per step ────────────────────────────
# ---------------------------------------------------------------------------

def preprocess_steps_current(image, ball_radius, window_size):
    """Return an ordered list of (name, array) for every step of the CURRENT
    pre_process_image pipeline, using the current implementation exactly."""
    from pycat.toolbox.image_processing_tools import apply_rescale_intensity, wbns_func

    steps = []

    # Step 0: dtype → float32, then /max normalisation (1.5.116 fix)
    img = _to_float_raw(image)
    steps.append(("PP [0] raw float32 (/65535)", img.copy()))
    m = float(img.max())
    if m > 0:
        img = img / m
    steps.append(("PP [1] /max normalised [0,1]", img.copy()))

    # Step 1: White top-hat — SQUARE footprint (current)
    try:
        selem = sk.morphology.footprint_rectangle((ball_radius*2+1, ball_radius*2+1))
    except AttributeError:
        selem = sk.morphology.square(ball_radius*2+1)
    wth = ndi.white_tophat(img, footprint=selem)
    steps.append(("PP [2] White top-hat (square)", wth.copy()))

    rescaled_th = apply_rescale_intensity(wth, out_min=0.3, out_max=1.0)
    top_hat_enh = rescaled_th * img
    steps.append(("PP [3] Top-hat × image", top_hat_enh.copy()))

    # Step 2: DoG blob enhancement (fixed sigmas — CURRENT)
    d_lo = ndi.gaussian_filter(img, sigma=2.0)
    d_hi = ndi.gaussian_filter(img, sigma=3.2)
    inv_log = np.clip(d_lo - d_hi, 0, None).astype(np.float32)
    if inv_log.max() > 0:
        inv_log /= inv_log.max()
    steps.append(("PP [4] DoG (σ=2.0,3.2) normalised", inv_log.copy()))

    log_enh = inv_log * top_hat_enh
    steps.append(("PP [5] DoG × top-hat (WBNS input)", log_enh.copy()))

    # Step 3: WBNS
    wbns, _ = wbns_func(log_enh, 4, 1)
    steps.append(("PP [6] After WBNS", wbns.copy()))

    # Step 4: Erosion + dilation
    img2 = ndi.grey_erosion(wbns, footprint=sk.morphology.disk(1))
    img2 = ndi.grey_dilation(img2, footprint=sk.morphology.disk(1))
    steps.append(("PP [7] After erosion+dilation", img2.copy()))

    # Step 5: Gaussian smooth
    img2 = ndi.gaussian_filter(img2, 1)
    steps.append(("PP [8] After Gaussian smooth", img2.copy()))

    # Step 6: CLAHE
    img2 = _safe_clahe(img2, kernel_size=math.ceil(window_size), clip_limit=0.0025)
    steps.append(("PP [9] After CLAHE (final output)", img2.copy()))

    return steps


# ---------------------------------------------------------------------------
# ── v1.0.0 preprocessing — one output per step ─────────────────────────────
# ---------------------------------------------------------------------------

def preprocess_steps_v100(image, ball_radius, window_size):
    """Return an ordered list of (name, array) for every step of the v1.0.0
    pre_process_image pipeline.  Uses the ORIGINAL:
      • No /max normalisation (raw /65535 float32)
      • disk(ball_radius) structuring element (not square)
      • apply_laplace_of_gauss_enhancement with sigma=3 (not DoG)
    """
    from pycat.toolbox.image_processing_tools import (
        apply_rescale_intensity, wbns_func,
        apply_laplace_of_gauss_enhancement)

    steps = []

    # Step 0: dtype → float32, NO normalisation (1.0.0 behaviour)
    img = _to_float_raw(image)
    steps.append(("v100-PP [0] raw float32 (/65535)", img.copy()))

    # Step 1: White top-hat — DISK footprint (1.0.0)
    wth = ndi.white_tophat(img, footprint=sk.morphology.disk(ball_radius))
    steps.append(("v100-PP [1] White top-hat (disk)", wth.copy()))

    rescaled_th = apply_rescale_intensity(wth, out_min=0.3, out_max=1.0)
    top_hat_enh = rescaled_th * img
    steps.append(("v100-PP [2] Top-hat × image", top_hat_enh.copy()))

    # Step 2: LoG blob enhancement (sigma=3, radius-independent — 1.0.0)
    _, inv_log = apply_laplace_of_gauss_enhancement(img, sigma=3)
    steps.append(("v100-PP [3] LoG inverted (σ=3)", inv_log.copy()))

    log_enh = inv_log * top_hat_enh
    steps.append(("v100-PP [4] LoG × top-hat (WBNS input)", log_enh.copy()))

    # Step 3: WBNS (identical)
    wbns, _ = wbns_func(log_enh, 4, 1)
    steps.append(("v100-PP [5] After WBNS", wbns.copy()))

    # Step 4: Erosion + dilation (identical)
    img2 = ndi.grey_erosion(wbns, footprint=sk.morphology.disk(1))
    img2 = ndi.grey_dilation(img2, footprint=sk.morphology.disk(1))
    steps.append(("v100-PP [6] After erosion+dilation", img2.copy()))

    # Step 5: Gaussian smooth (identical)
    img2 = ndi.gaussian_filter(img2, 1)
    steps.append(("v100-PP [7] After Gaussian smooth", img2.copy()))

    # Step 6: CLAHE (same params — but input scale differs!)
    img2 = _safe_clahe(img2, kernel_size=math.ceil(window_size), clip_limit=0.0025)
    steps.append(("v100-PP [8] After CLAHE (final output)", img2.copy()))

    return steps


# ---------------------------------------------------------------------------
# ── CURRENT enhanced BG removal — one output per step ──────────────────────
# ---------------------------------------------------------------------------

def bg_removal_steps_current(image, ball_radius, roi_mask=None):
    """Step-by-step layers for the CURRENT rb_gaussian_bg_removal_with_edge_enhancement."""
    from pycat.toolbox.image_processing_tools import (
        subtract_background, compute_rolling_ball_background,
        background_inpainting_func, peak_and_edge_enhancement_func)

    steps = []

    img = _to_float_raw(image)
    steps.append(("BGR [0] raw float32 (/65535)", img.copy()))

    # 1.5.116 /max normalisation
    m = float(img.max())
    if m > 0:
        img = img / m
    steps.append(("BGR [1] /max normalised [0,1]", img.copy()))

    # ROI mask
    if roi_mask is not None:
        roi_mask = roi_mask.astype(bool)
        img *= roi_mask
        bg_img = background_inpainting_func(img, roi_mask, ball_radius)
    else:
        bg_img = img.copy()
    steps.append(("BGR [2] After mask/inpaint", bg_img.copy()))

    # Rolling ball
    rb_bg = compute_rolling_ball_background(bg_img, ball_radius)
    rb_bg = ndi.gaussian_filter(rb_bg, sigma=ball_radius // 2)
    steps.append(("BGR [3] Rolling-ball background", rb_bg.copy()))

    rb_sub = subtract_background(img, rb_bg, bg_scaling_factor=0.75,
                                  equalize_intensity=False)
    steps.append(("BGR [4] After RB subtraction", rb_sub.copy()))

    # Gaussian BG
    gauss_bg = ndi.gaussian_filter(rb_sub, sigma=(ball_radius * 2))
    steps.append(("BGR [5] Gaussian background", gauss_bg.copy()))

    gauss_sub = subtract_background(rb_sub, gauss_bg, bg_scaling_factor=0.75,
                                     equalize_intensity=True,
                                     window_size=ball_radius * 4)
    steps.append(("BGR [6] After Gaussian subtraction (rb_gauss out)", gauss_sub.copy()))

    # Peak and edge enhancement
    enh = peak_and_edge_enhancement_func(gauss_sub, ball_radius)
    steps.append(("BGR [7] After peak+edge enhancement (final output)", enh.copy()))

    return steps


# ---------------------------------------------------------------------------
# ── v1.0.0 enhanced BG removal — one output per step ───────────────────────
# ---------------------------------------------------------------------------

def bg_removal_steps_v100(image, ball_radius, roi_mask=None):
    """Step-by-step layers for the v1.0.0 rb_gaussian_bg_removal_with_edge_enhancement.
    1.0.0 did NOT normalise by /max — it passes the raw /65535 float straight in."""
    from pycat.toolbox.image_processing_tools import (
        subtract_background, compute_rolling_ball_background,
        background_inpainting_func, peak_and_edge_enhancement_func)

    steps = []

    # 1.0.0: raw /65535 float32, NO /max normalisation
    img = _to_float_raw(image)
    steps.append(("v100-BGR [0] raw float32 (/65535, no /max)", img.copy()))

    if roi_mask is not None:
        roi_mask = roi_mask.astype(bool)
        img *= roi_mask
        bg_img = background_inpainting_func(img, roi_mask, ball_radius)
    else:
        bg_img = img.copy()
    steps.append(("v100-BGR [1] After mask/inpaint", bg_img.copy()))

    rb_bg = compute_rolling_ball_background(bg_img, ball_radius)
    rb_bg = ndi.gaussian_filter(rb_bg, sigma=ball_radius // 2)
    steps.append(("v100-BGR [2] Rolling-ball background", rb_bg.copy()))

    rb_sub = subtract_background(img, rb_bg, bg_scaling_factor=0.75,
                                  equalize_intensity=False)
    steps.append(("v100-BGR [3] After RB subtraction", rb_sub.copy()))

    gauss_bg = ndi.gaussian_filter(rb_sub, sigma=(ball_radius * 2))
    steps.append(("v100-BGR [4] Gaussian background", gauss_bg.copy()))

    gauss_sub = subtract_background(rb_sub, gauss_bg, bg_scaling_factor=0.75,
                                     equalize_intensity=True,
                                     window_size=ball_radius * 4)
    steps.append(("v100-BGR [5] After Gaussian subtraction (rb_gauss out)", gauss_sub.copy()))

    enh = peak_and_edge_enhancement_func(gauss_sub, ball_radius)
    steps.append(("v100-BGR [6] After peak+edge enhancement (final output)", enh.copy()))

    return steps
