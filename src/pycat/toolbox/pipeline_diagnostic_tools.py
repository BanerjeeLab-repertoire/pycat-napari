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

    # Blob enhancement: LoG with sigma scaled to ball_radius (new pipeline)
    _log_sigma = max(1.0, ball_radius * 0.27)
    inv_log = -ndi.gaussian_laplace(img.astype(np.float64),
                                     sigma=_log_sigma).astype(np.float32)
    inv_log = np.clip(inv_log, 0, None)
    if inv_log.max() > 0:
        inv_log /= inv_log.max()
    steps.append((f"PP [2] LoG (σ={_log_sigma:.1f}, ball_r×0.27)", inv_log.copy()))

    log_enh = inv_log   # direct LoG — no top-hat multiplication
    steps.append(("PP [3] LoG normalised (WBNS input)", log_enh.copy()))

    # Step 3: WBNS
    wbns, _ = wbns_func(log_enh, 4, 1)
    steps.append(("PP [4] After WBNS", wbns.copy()))

    # Step 4: Erosion + dilation
    img2 = ndi.grey_erosion(wbns, footprint=sk.morphology.disk(1))
    img2 = ndi.grey_dilation(img2, footprint=sk.morphology.disk(1))
    steps.append(("PP [5] After erosion+dilation", img2.copy()))

    # Step 5: Gaussian smooth
    img2 = ndi.gaussian_filter(img2, 1)
    steps.append(("PP [6] After Gaussian smooth", img2.copy()))

    # Step 6: CLAHE
    img2 = _safe_clahe(img2, kernel_size=math.ceil(window_size), clip_limit=0.0025)
    steps.append(("PP [7] After CLAHE (final output)", img2.copy()))

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
    """Step-by-step layers showing what segment_subcellular_objects receives
    when fed the LoG-preprocessed image.

    As of 1.5.125, segment_subcellular_objects detects whether its input is
    already LoG/CLAHE preprocessed (median of non-zero pixels < 0.05) and
    bypasses rb_gaussian_bg_removal_with_edge_enhancement entirely, applying
    only a light CLAHE pass for Felzenszwalb. This diagnostic shows both
    paths so the bypass logic is visible.
    """
    from pycat.toolbox.image_processing_tools import (
        subtract_background, compute_rolling_ball_background,
        background_inpainting_func, _safe_equalize_adapthist)
    import math

    steps = []

    # What segment_subcellular_objects actually receives:
    # pre_process_image output = /max → LoG → WBNS → morph → Gauss → CLAHE
    img_raw = _to_float_raw(image)
    m = float(img_raw.max())
    img = img_raw / m if m > 0 else img_raw
    steps.append(("BGR [0] /max normalised (pp input)", img.copy()))

    # LoG enhancement (same as pre_process_image)
    _log_sigma = max(1.0, ball_radius * 0.27)
    log_img = -ndi.gaussian_laplace(img.astype(np.float64),
                                     sigma=_log_sigma).astype(np.float32)
    log_img = np.clip(log_img, 0, None)
    if log_img.max() > 0: log_img /= log_img.max()
    steps.append((f"BGR [1] LoG(σ={_log_sigma:.1f}) — pp output fed to seg", log_img.copy()))

    # Light CLAHE (what the bypass applies)
    _ks = max(8, math.ceil(ball_radius * 4))
    pp_clahe = _safe_equalize_adapthist(log_img, kernel_size=_ks,
                                         clip_limit=0.005).astype(np.float32)
    steps.append(("BGR [2] CLAHE only (bypass path — used by seg)", pp_clahe.copy()))

    # Old path for comparison: rb_gaussian_bg_removal on the LoG output
    rb_bg = compute_rolling_ball_background(log_img, ball_radius)
    rb_bg = ndi.gaussian_filter(rb_bg, sigma=ball_radius // 2)
    steps.append(("BGR [3] RB background on LoG output", rb_bg.copy()))

    rb_sub = subtract_background(log_img, rb_bg, bg_scaling_factor=0.75,
                                  equalize_intensity=False)
    steps.append(("BGR [4] After RB sub on LoG (DESTROYS signal)", rb_sub.copy()))

    gauss_bg = ndi.gaussian_filter(rb_sub, sigma=(ball_radius * 2))
    gauss_sub = subtract_background(rb_sub, gauss_bg, bg_scaling_factor=0.75,
                                     equalize_intensity=True,
                                     window_size=ball_radius * 4)
    steps.append(("BGR [5] After Gauss sub (old final — NaN SNR)", gauss_sub.copy()))

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
