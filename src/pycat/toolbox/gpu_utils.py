"""
PyCAT GPU Acceleration Utilities
=================================
Provides GPU-accelerated drop-in replacements for the most expensive
image processing operations in PyCAT's pipeline, using CuPy (CUDA) with
automatic fallback to CPU (NumPy/SciPy) when a GPU is not available.

The module is designed so that no calling code needs to know whether GPU
or CPU is being used — every public function accepts and returns standard
NumPy arrays.

Accelerated operations
----------------------
- Morphological operations (white top-hat, erosion, dilation, disk element)
- Gaussian filtering
- Wavelet background/noise decomposition (WBNS)
- Rolling-ball background subtraction
- Laplacian of Gaussian

GPU availability is detected once at import time and cached in ``GPU_AVAILABLE``.
Set the environment variable ``PYCAT_FORCE_CPU=1`` to disable GPU even when
CuPy is installed (useful for testing or on shared machines).

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo

Date
----
    2025
"""

from __future__ import annotations

import os
import warnings
import numpy as np
import scipy.ndimage as ndi
from pywt import wavedecn, waverecn

# ---------------------------------------------------------------------------
# GPU detection
# ---------------------------------------------------------------------------

GPU_AVAILABLE: bool = False
cp = None           # cupy module reference, set below if available
cpnd = None         # cupyx.scipy.ndimage reference

# Suppress CuPy's CUDA path warning — CuPy works via the driver even without
# the full CUDA toolkit installed, so this warning is not actionable for most users.
import warnings as _warnings
_warnings.filterwarnings(
    "ignore",
    message="CUDA path could not be detected",
    category=UserWarning,
    module="cupy",
)


def _register_bundled_cuda_libs() -> None:
    """Make CuPy find the CUDA 11.x runtime DLLs that PyTorch bundles.

    ``cupy-cuda11x`` needs the CUDA 11.x runtime at first kernel launch
    (``cudart``, ``nvrtc`` + its ``nvrtc-builtins`` companion, ``cublas`` ...).
    On Windows machines without a standalone CUDA toolkit, the only consistent
    copy of those libraries is the one PyTorch — a hard PyCAT dependency, built
    against cu118 — ships in ``torch/lib``. CuPy cannot see that directory, so
    ``import cupy`` succeeds but the first real op dies with
    ``Could not find nvrtc64_112_0.dll`` (or nvrtc then fails to open its
    ``nvrtc-builtins`` companion, which it loads via ``PATH`` rather than the
    Python DLL-directory list).

    We locate ``torch/lib`` WITHOUT importing torch (``find_spec`` only — torch
    import is heavy and this module is re-imported in every pool worker), then
    add it to both ``os.add_dll_directory`` (for CuPy's own ``LoadLibraryEx``)
    and ``PATH`` (for nvrtc's internal load of ``nvrtc-builtins``). Best-effort
    and Windows-only: a no-op elsewhere, or if torch / its lib dir is absent
    (e.g. a CPU-only torch build, where GPU would be unavailable anyway).
    """
    if os.name != "nt":
        return
    try:
        import importlib.util
        spec = importlib.util.find_spec("torch")
        if spec is None or not spec.origin:
            return
        lib_dir = os.path.join(os.path.dirname(spec.origin), "lib")
        if not os.path.isdir(lib_dir):
            return
        os.environ["PATH"] = lib_dir + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(lib_dir)
    except Exception:
        # A DLL-path tweak must never break import of this module.
        pass


if os.environ.get("PYCAT_FORCE_CPU", "0") != "1":
    try:
        _register_bundled_cuda_libs()
        import cupy as _cp
        import cupyx.scipy.ndimage as _cpnd
        # Smoke-test that CUDA is *functional*, not merely importable: force an
        # elementwise kernel compile (nvrtc) and a reduction, so GPU_AVAILABLE
        # reflects true capability. A bare ``cp.zeros`` is only a memset and
        # would not surface a missing/broken nvrtc runtime.
        _test = _cp.zeros((4, 4), dtype=_cp.float32) + 1
        float(_test.sum())
        del _test
        cp   = _cp
        cpnd = _cpnd
        GPU_AVAILABLE = True
        # Only print in the main process — this module is imported fresh in
        # every ProcessPoolExecutor worker subprocess, so without this guard
        # the message prints once per worker (8x on an 8-core machine).
        import multiprocessing as _mp
        try:
            if _mp.current_process().name == 'MainProcess':
                print("[PyCAT GPU] CuPy detected — GPU acceleration enabled.")
        except Exception:
            pass
    except Exception as _e:
        import multiprocessing as _mp
        try:
            if _mp.current_process().name == 'MainProcess':
                print(f"[PyCAT GPU] CuPy not available ({_e}) — using CPU fallback.")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Transfer helpers
# ---------------------------------------------------------------------------

def gpu_available() -> bool:
    """Return True if CuPy is installed and a CUDA GPU is functional."""
    return GPU_AVAILABLE


def to_gpu(arr: np.ndarray):
    """Send a NumPy array to the GPU. Returns unchanged if GPU unavailable."""
    if GPU_AVAILABLE:
        return cp.asarray(arr, dtype=cp.float32)
    return arr.astype(np.float32)


def to_cpu(arr) -> np.ndarray:
    """Return a GPU array to CPU as float32 NumPy. Pass-through on CPU."""
    if GPU_AVAILABLE and isinstance(arr, cp.ndarray):
        return cp.asnumpy(arr).astype(np.float32)
    return np.asarray(arr, dtype=np.float32)


# ---------------------------------------------------------------------------
# Accelerated morphological operations
# ---------------------------------------------------------------------------

def gpu_disk(radius: int):
    """
    Create a disk structuring element on GPU (or CPU).
    Equivalent to skimage.morphology.disk(radius).
    """
    import skimage.morphology as skm
    disk_cpu = skm.disk(radius).astype(np.float32)
    if GPU_AVAILABLE:
        return cp.asarray(disk_cpu)
    return disk_cpu


def gpu_white_tophat(image: np.ndarray, radius: int) -> np.ndarray:
    """
    GPU-accelerated white top-hat filter.
    Falls back to scipy.ndimage on CPU.
    """
    if GPU_AVAILABLE:
        img_gpu  = to_gpu(image)
        disk     = gpu_disk(radius)
        result   = cpnd.white_tophat(img_gpu, footprint=disk)
        return to_cpu(result)
    else:
        import skimage.morphology as skm
        return ndi.white_tophat(image.astype(np.float32),
                                footprint=skm.disk(radius))


def gpu_grey_erosion(image: np.ndarray, radius: int = 1) -> np.ndarray:
    """GPU-accelerated grey erosion with disk footprint."""
    if GPU_AVAILABLE:
        img_gpu = to_gpu(image)
        disk    = gpu_disk(radius)
        result  = cpnd.grey_erosion(img_gpu, footprint=disk)
        return to_cpu(result)
    else:
        import skimage.morphology as skm
        return ndi.grey_erosion(image.astype(np.float32),
                                footprint=skm.disk(radius))


def gpu_grey_dilation(image: np.ndarray, radius: int = 1) -> np.ndarray:
    """GPU-accelerated grey dilation with disk footprint."""
    if GPU_AVAILABLE:
        img_gpu = to_gpu(image)
        disk    = gpu_disk(radius)
        result  = cpnd.grey_dilation(img_gpu, footprint=disk)
        return to_cpu(result)
    else:
        import skimage.morphology as skm
        return ndi.grey_dilation(image.astype(np.float32),
                                 footprint=skm.disk(radius))


def gpu_gaussian_filter(image: np.ndarray, sigma: float) -> np.ndarray:
    """GPU-accelerated Gaussian filter."""
    if GPU_AVAILABLE:
        img_gpu = to_gpu(image)
        result  = cpnd.gaussian_filter(img_gpu, sigma=sigma)
        return to_cpu(result)
    else:
        return ndi.gaussian_filter(image.astype(np.float32), sigma=sigma)


def gpu_laplace_of_gaussian(image: np.ndarray, sigma: float = 3.0) -> np.ndarray:
    """
    GPU-accelerated Laplacian of Gaussian filter.
    Returns the LoG-filtered image as a float32 NumPy array.
    """
    if GPU_AVAILABLE:
        img_gpu    = to_gpu(image)
        smoothed   = cpnd.gaussian_filter(img_gpu, sigma=sigma)
        log_result = cpnd.laplace(smoothed)
        return to_cpu(log_result)
    else:
        smoothed   = ndi.gaussian_filter(image.astype(np.float32), sigma=sigma)
        return ndi.laplace(smoothed)


# ---------------------------------------------------------------------------
# Accelerated WBNS wavelet decomposition
# ---------------------------------------------------------------------------

def gpu_wavelet_bg_and_noise(image: np.ndarray,
                              num_levels: int,
                              noise_lvl: int):
    """
    GPU-accelerated wavelet background and noise decomposition.

    The wavelet decomposition itself (pywt.wavedecn / waverecn) runs on CPU
    since PyWavelets has no GPU backend.  What we accelerate is the
    post-decomposition Gaussian smoothing, which on large images is a
    significant fraction of WBNS runtime.

    For true GPU wavelet transforms, CuSignal (part of RAPIDS) provides
    GPU wavelets, but requires additional RAPIDS installation.  This
    implementation is therefore a partial GPU acceleration that handles
    the most numerically intensive smoothing steps on GPU.

    Parameters
    ----------
    image : np.ndarray, float32
    num_levels : int
    noise_lvl : int

    Returns
    -------
    Background, Noise, BG_unfiltered : np.ndarray, np.ndarray, np.ndarray
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        coeffs  = wavedecn(image, 'db1', level=None)
        coeffs2 = coeffs.copy()

        for BGlvl in range(1, num_levels):
            coeffs[-BGlvl] = {k: np.zeros_like(v)
                              for k, v in coeffs[-BGlvl].items()}
        Background_raw = waverecn(coeffs, 'db1')
        BG_unfiltered  = Background_raw.copy()

        # GPU-accelerated Gaussian smoothing of background
        Background = gpu_gaussian_filter(
            Background_raw.astype(np.float32), sigma=2 ** num_levels
        )

        coeffs2[0] = np.ones_like(coeffs2[0])
        for lvl in range(1, len(coeffs2) - noise_lvl):
            coeffs2[lvl] = {k: np.zeros_like(v)
                            for k, v in coeffs2[lvl].items()}
        Noise = waverecn(coeffs2, 'db1')

    return (Background.astype(np.float32),
            Noise.astype(np.float32),
            BG_unfiltered.astype(np.float32))


# ---------------------------------------------------------------------------
# Full accelerated pre_process_image pipeline
# ---------------------------------------------------------------------------

def gpu_pre_process_image(image: np.ndarray,
                           ball_radius: int,
                           window_size: int) -> np.ndarray:
    """
    GPU-accelerated version of PyCAT's ``pre_process_image`` pipeline.

    Replaces every scipy.ndimage and skimage morphological call with
    GPU equivalents where available.  Falls back to CPU automatically.

    Steps (unchanged from original):
        1. White top-hat filter
        2. Laplacian of Gaussian enhancement
        3. WBNS (wavelet background/noise subtraction) — partial GPU
        4. Grey erosion + dilation
        5. Gaussian smoothing
        6. CLAHE

    Parameters
    ----------
    image : np.ndarray
    ball_radius : int
    window_size : int

    Returns
    -------
    np.ndarray  same dtype as input
    """
    import math
    import skimage as sk

    input_dtype = str(image.dtype)
    img = image.astype(np.float32)

    # ── Step 1: White top-hat ─────────────────────────────────────────────
    wth = gpu_white_tophat(img, ball_radius)
    # rescale top-hat to [0.3, 1.0]
    wth_min, wth_max = wth.min(), wth.max()
    if wth_max > wth_min:
        wth_rescaled = 0.3 + 0.7 * (wth - wth_min) / (wth_max - wth_min)
    else:
        wth_rescaled = np.full_like(wth, 0.3)
    top_hat_enhanced = wth_rescaled * img

    # ── Step 2: Laplacian of Gaussian ────────────────────────────────────
    log_filtered = gpu_laplace_of_gaussian(img, sigma=3.0)
    # shift positive, rescale to [0, 0.1], invert → attenuation mask
    shifted = log_filtered + np.abs(log_filtered.min())
    s_min, s_max = shifted.min(), shifted.max()
    if s_max > s_min:
        rescaled_log = 0.1 * (shifted - s_min) / (s_max - s_min)
    else:
        rescaled_log = np.zeros_like(shifted)
    inverted_log = rescaled_log.max() - rescaled_log   # invert
    log_enhanced = inverted_log * top_hat_enhanced

    # ── Step 3: WBNS ──────────────────────────────────────────────────────
    psf_res   = 4
    noise_lvl = 1
    num_levels = int(np.ceil(np.log2(psf_res)))

    # Pad to even dimensions for wavelet
    h, w = log_enhanced.shape
    pad_h = h % 2 != 0
    pad_w = w % 2 != 0
    padded = log_enhanced
    if pad_h:
        padded = np.pad(padded, ((0, 1), (0, 0)), 'edge')
    if pad_w:
        padded = np.pad(padded, ((0, 0), (0, 1)), 'edge')

    Background, Noise, _ = gpu_wavelet_bg_and_noise(padded, num_levels, noise_lvl)

    # Trim padding
    if pad_h:
        padded     = padded[:-1, :]
        Background = Background[:-1, :]
        Noise      = Noise[:-1, :]
    if pad_w:
        padded     = padded[:, :-1]
        Background = Background[:, :-1]
        Noise      = Noise[:, :-1]

    # Background subtraction
    bg_subtracted = padded - 0.65 * Background
    bg_subtracted = np.clip(bg_subtracted, 0, None)

    # Noise thresholding and smoothing
    Noise = np.clip(Noise, 0, None)
    noise_thresh = Noise.mean() + 2 * Noise.std()
    Noise = np.clip(Noise, 0, noise_thresh)
    Noise_smooth = gpu_gaussian_filter(Noise, sigma=num_levels)

    wbns_img = bg_subtracted - 0.2 * Noise_smooth
    wbns_img = np.clip(wbns_img, 0, None)

    # ── Step 4: Erosion + dilation ────────────────────────────────────────
    img_proc = gpu_grey_erosion(wbns_img, radius=1)
    img_proc = gpu_grey_dilation(img_proc, radius=1)

    # ── Step 5: Gaussian smoothing ────────────────────────────────────────
    img_proc = gpu_gaussian_filter(img_proc, sigma=1.0)

    # ── Step 6: CLAHE ─────────────────────────────────────────────────────
    # CLAHE has no GPU implementation in skimage or CuPy — runs on CPU
    # but input is now float32 which is faster than the original path
    k_size     = math.ceil(window_size)
    clip_limit = 0.0025
    img_proc   = sk.exposure.equalize_adapthist(
        img_proc, kernel_size=k_size, clip_limit=clip_limit
    )

    # ── Restore dtype ──────────────────────────────────────────────────────
    from pycat.utils.general_utils import dtype_conversion_func
    return dtype_conversion_func(img_proc.astype(np.float32), input_dtype)


# ---------------------------------------------------------------------------
# Accelerated rolling-ball background subtraction
# ---------------------------------------------------------------------------

def gpu_rolling_ball_background(image: np.ndarray,
                                 ball_radius: int) -> np.ndarray:
    """
    GPU-accelerated rolling-ball background estimation via morphological
    opening (erosion then dilation with a disk of `ball_radius`).

    This approximates the rolling-ball algorithm and is significantly faster
    than SimpleITK's exact implementation on large images.

    Returns the background-subtracted image.
    """
    img_f32 = image.astype(np.float32)
    # Background ≈ morphological opening with large disk
    background = gpu_grey_erosion(img_f32, radius=ball_radius)
    background = gpu_grey_dilation(background, radius=ball_radius)
    subtracted = img_f32 - background
    return np.clip(subtracted, 0, None)


# ---------------------------------------------------------------------------
# Convenience: print GPU memory usage (useful for benchmarking)
# ---------------------------------------------------------------------------

def gpu_memory_info() -> str:
    """Return a string with current GPU memory usage, or 'GPU not available'."""
    if not GPU_AVAILABLE:
        return "GPU not available"
    mem = cp.cuda.runtime.memGetInfo()
    free_mb  = mem[0] / 1024 ** 2
    total_mb = mem[1] / 1024 ** 2
    used_mb  = total_mb - free_mb
    return f"GPU memory: {used_mb:.0f} MB used / {total_mb:.0f} MB total"
