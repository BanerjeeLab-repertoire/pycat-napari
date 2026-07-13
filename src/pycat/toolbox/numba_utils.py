"""
PyCAT Numba JIT Acceleration Utilities
=======================================
Provides JIT-compiled versions of the most expensive pure-arithmetic
operations in PyCAT's image processing pipeline using Numba.

Numba compiles these functions to native machine code on the first call
(per session), then runs at near-C speed on every subsequent call.
Compilation is cached to disk so the second session onwards is instant.

Targeted operations
-------------------
The PyWavelets wavedecn/waverecn calls and scipy morphological operations
(white_tophat, gaussian_filter) are already written in C and cannot be
accelerated further by Numba.  The functions here target the pure-NumPy
arithmetic between those calls, which is where Python overhead accumulates:

    - wbns_post_process        : bg subtraction, noise thresholding, clipping
    - rescale_intensity_numba  : min-max rescaling
    - invert_image_numba       : intensity inversion
    - clip_to_zero_numba       : positivity constraint (clip negatives)
    - top_hat_enhance_numba    : elementwise multiply of tophat × image

All functions accept and return float32 NumPy arrays.

Graceful fallback
-----------------
If Numba is not installed, every function falls back to a plain NumPy
implementation transparently.  No calling code needs to change.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo

Date
----
    2025
"""

from __future__ import annotations
import numpy as np

# ---------------------------------------------------------------------------
# Numba availability check
# ---------------------------------------------------------------------------

# ── `parallel=True` is what segfaults on Apple Silicon ──────────────────────────────────────
#
# Reported on an M2 (2026-07-13). The fault handler landed on line 192 — **the first call into a
# ``@njit(parallel=True)`` kernel** — with an OpenMP banner printed immediately before it:
#
#     OMP: Info #276: omp_set_nested routine deprecated...
#     Fatal Python error: Segmentation fault
#
# Two things could produce that, and **the traceback cannot distinguish them**:
#
# 1. **A native-init race.** The warm-up ran on a worker thread while Qt initialised on the main
#    thread, so Numba's OpenMP runtime and Qt were coming up **concurrently**. ``run_pycat``
#    already documents exactly this race for torch, and already defers torch on Darwin for it —
#    **and was letting Numba do it anyway.** That is fixed there.
#
# 2. **The parallel backend itself.** ``parallel=True`` + ``cache=True`` on arm64 has to load a
#    threading layer (OpenMP/TBB) and a cached object file, and that combination is fragile on
#    macOS ARM.
#
# **Fixing only (1) would be a guess**, and if (2) is the real cause the crash simply moves from
# launch to first use — which is *worse*, because it would then happen mid-analysis.
#
# **WHAT THE DIAGNOSTIC ACTUALLY SHOWED (2026-07-13) — and it is NOT what it claimed** ─────────
#
# The first version of ``numba_arm64_diag.py`` ran each test with ``python -c "<code>"``. But
# ``@njit(cache=True)`` **cannot cache code that came from a string** — there is no file to write
# the cache beside — so numba raised::
#
#     RuntimeError: cannot cache function 'f': no locator available for file '<string>'
#
# **Both** the cached test and the parallel test hit that, because both used ``cache=True``.
# Neither ever reached the parallel backend. The script saw "parallel crashed" and announced that
# the backend was broken — **it had proved nothing of the sort.**
#
# And the failures were **clean Python exceptions, not segfaults**. The launch crash was
# ``Fatal Python error: Segmentation fault``. *Those are not the same failure.*
#
# **What Meet's run DOES establish:**
#   * plain ``@njit`` works on the M2 (test 1 passed)
#   * **the OpenMP threading layer loads fine on its own** — test 4 launched it, printed ``omp``,
#     and did not crash
#   * numpy 1.26.4 / numba 0.65.1 / llvmlite 0.47.0, macOS 26.5 arm64
#
# **That last point is weak evidence for the RACE, not the backend**: OpenMP comes up cleanly when
# it comes up *alone*. The crash needed Qt initialising beside it.
#
# **So the honest position is:** deferring the warm-up (1) is almost certainly the real fix — it
# removes a concurrency this very file's neighbour already identifies as fatal on arm64. Disabling
# the parallel backend was justified by a diagnostic **that did not test it**, and may be an
# unnecessary loss of speed.
#
# ── **IT IS TORCH, NOT Qt.** The race I spent three diagnostics assuming never existed. ──
#
# ``reproduce_arm64_crash.py`` on Meet's M2 (2026-07-13). Each case in its own subprocess, each
# recreating the ORIGINAL concurrency — numba compiling on a **worker thread**:
#
#     A  numba on a worker, nothing else            **OK**
#     B  numba on a worker + **Qt** on the main     **OK**        <- **Qt IS INNOCENT**
#     C  numba on a worker + **torch** first        **SEGFAULT**  <- the cause
#     D  numba on a worker + Qt + torch             **SEGFAULT**
#     E  numba on the MAIN thread, after Qt         **OK**
#
# **torch ships its own libomp.** Two OpenMP runtimes in one arm64 process is a classic way to
# die — and the ``OMP: Info #276`` banner in the original crash was **torch's.** Numba was the
# bystander that happened to be running when it blew up.
#
# **The Qt race was a hypothesis I formed from a coincidence, and I ran with it for three
# diagnostics. It was wrong.**
#
# ── AND THIS CHANGES WHY 1.5.503 WORKS ───────────────────────────────────────────────────
#
# ``run_pycat`` calls ``_prewarm_cellpose_model()`` at line ~294 — **which imports torch** — and
# starts the Numba warm-up thread at line ~495. **That is case C, exactly.**
#
# 1.5.503 does two things on Darwin: it **defers the warm-up** and it **disables parallel Numba**.
# I claimed the first was the fix. **The matrix says it is the second.**
#
# Deferring the warm-up moves the compile from launch to **first use** — and if torch+numba
# segfaults on the main thread too, that is *worse*, not better: the crash would land
# **mid-analysis** instead of at startup.
#
# **The one thing that genuinely protects Meet is `parallel=False`**, because the crash is inside
# a parallel kernel.
#
# ── THE MISSING CELL, and it decides the real fix ────────────────────────────────────────
#
# E vs C changed **two** variables (torch present/absent AND worker/main thread), so E surviving
# does not prove the main thread is safe. **``reproduce_arm64_crash.py`` (2nd run) isolates it:**
#
#     F  **torch + numba on the MAIN thread**
#
#     F segfaults -> the thread is irrelevant. It is libomp vs libomp, and the fix must stop two
#                    OpenMP runtimes loading — ``KMP_DUPLICATE_LIB_OK=TRUE`` (case G) or
#                    ``NUMBA_THREADING_LAYER=workqueue`` (case H, the cleaner one: numba avoids
#                    OpenMP entirely and KEEPS its parallelism).
#     F survives  -> the worker thread IS the trigger in torch's presence, and parallel Numba can
#                    be re-enabled provided the warm-up stays on the main thread.
#
# **Until F is run, parallel stays OFF on Darwin** — and now for a reason that is measured rather
# than assumed.
#
# So the parallel backend is **off by default on Darwin**. The kernels still JIT (single-threaded
# Numba is fine), and every one of them has a NumPy fallback besides. On a 64x64 warm-up image
# the parallelism buys nothing anyway; on a real image it is a speed-up, not a capability — **and
# it is not worth a segfault.**
#
# ``PYCAT_NUMBA_PARALLEL=1`` forces it back on, for anyone who wants to test whether a newer
# numba/llvmlite has fixed it. **That is the experiment worth running**, and it should not require
# editing the source.
import os as _os
import sys as _sys

# ── THE FIX: numba must not load an OpenMP runtime on macOS ─────────────────────────────────
#
# **Established on Meet's M2 (2026-07-13), each case in its own subprocess:**
#
#     C  torch + numba on a **worker**                    **SEGFAULT**
#     F  torch + numba on the **MAIN thread**             **SEGFAULT**  <- the thread is irrelevant
#     G  torch + numba + **KMP_DUPLICATE_LIB_OK=TRUE**    **SEGFAULT**  <- the flag does NOTHING
#     H  torch + numba + **NUMBA_THREADING_LAYER=workqueue**   **OK**   <- **the fix**
#
# **It is torch's libomp against numba's libomp. Full stop.** Qt is innocent; the thread is
# irrelevant; and ``KMP_DUPLICATE_LIB_OK`` — **which ``run_pycat`` already sets** — does not help,
# exactly as Intel's own documentation warns (*"may cause crashes or silently produce incorrect
# results"*).
#
# **``workqueue`` is numba's own pure-Python thread pool. It loads NO libomp at all**, so the
# collision cannot happen — and it **keeps the parallelism.** It is slower than OpenMP and has no
# nested parallelism, but PyCAT's kernels are flat per-pixel loops, so neither costs anything.
#
# ``NUMBA_THREADING_LAYER`` is read at **numba's import time**, so this must run before
# ``import numba`` below. This is the only place early enough.
#
# **This is what made the deferred warm-up look like a fix**: it was never the concurrency. The
# only thing that protected macOS users was ``parallel=False``, and with workqueue we no longer
# need even that.
if _sys.platform == 'darwin':
    _os.environ.setdefault('NUMBA_THREADING_LAYER', 'workqueue')

# **And if someone forces OpenMP back on, the crash comes back.** Say so rather than letting them
# discover it as a segfault — a user who sets this deserves to know what they are choosing.
_FORCED_OMP = (_sys.platform == 'darwin'
               and _os.environ.get('NUMBA_THREADING_LAYER', '').lower() in ('omp', 'tbb'))
if _FORCED_OMP:
    print("[PyCAT Numba] WARNING: NUMBA_THREADING_LAYER is set to "
          f"'{_os.environ['NUMBA_THREADING_LAYER']}' on macOS. **PyTorch already loads its own "
          "OpenMP runtime, and a second one segfaults this process** (verified on Apple Silicon). "
          "Unset it, or use 'workqueue', unless you know what you are doing.")

# With numba no longer loading libomp, the collision is gone — so parallel can be ON everywhere.
# **It was disabled on Darwin for a cause that has now been correctly identified and removed.**
_PARALLEL_DEFAULT = True

NUMBA_PARALLEL: bool = _os.environ.get(
    'PYCAT_NUMBA_PARALLEL', '1' if _PARALLEL_DEFAULT else '0') in ('1', 'true', 'True')

NUMBA_AVAILABLE: bool = False
try:
    import numba
    from numba import njit, prange
    NUMBA_AVAILABLE = True
    if NUMBA_PARALLEL:
        print("[PyCAT Numba] Numba detected — JIT acceleration enabled.")
    else:
        print("[PyCAT Numba] Numba detected — JIT enabled, parallel backend OFF "
              "(it segfaults on Apple Silicon; set PYCAT_NUMBA_PARALLEL=1 to re-enable).")
except ImportError:
    print("[PyCAT Numba] Numba not installed — using NumPy fallback.")
    # Provide a no-op decorator so the rest of the file is syntax-valid
    def njit(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator if args and callable(args[0]) else decorator
    prange = range


# ---------------------------------------------------------------------------
# JIT-compiled arithmetic kernels
# ---------------------------------------------------------------------------

@njit(cache=True, parallel=NUMBA_PARALLEL, fastmath=True)
def _rescale_intensity_kernel(image: np.ndarray,
                               img_min: float,
                               img_max: float,
                               out_min: float,
                               out_max: float) -> np.ndarray:
    """Rescale image pixel values to [out_min, out_max].
    Min/max passed in explicitly to avoid parallel reduction race condition.
    """
    result = np.empty_like(image)
    h, w = image.shape
    rng = img_max - img_min
    out_rng = out_max - out_min

    if rng == 0.0:
        for i in prange(h):
            for j in range(w):
                result[i, j] = out_min
    else:
        scale = out_rng / rng
        for i in prange(h):
            for j in range(w):
                result[i, j] = (image[i, j] - img_min) * scale + out_min

    return result


@njit(cache=True, parallel=NUMBA_PARALLEL, fastmath=True)
def _clip_to_zero_kernel(image: np.ndarray) -> np.ndarray:
    """Clip all negative values to zero (positivity constraint)."""
    result = np.empty_like(image)
    h, w = image.shape
    for i in prange(h):
        for j in range(w):
            v = image[i, j]
            result[i, j] = v if v > 0.0 else 0.0
    return result


@njit(cache=True, parallel=NUMBA_PARALLEL, fastmath=True)
def _invert_kernel(image: np.ndarray, img_max: float) -> np.ndarray:
    """Invert image: result = max_val - image.
    Max passed in explicitly to avoid serial loop bottleneck.
    """
    result = np.empty_like(image)
    h, w = image.shape
    for i in prange(h):
        for j in range(w):
            result[i, j] = img_max - image[i, j]
    return result


@njit(cache=True, parallel=NUMBA_PARALLEL, fastmath=True)
def _elementwise_multiply_kernel(a: np.ndarray,
                                  b: np.ndarray) -> np.ndarray:
    """Elementwise multiply two same-shape float32 arrays."""
    result = np.empty_like(a)
    h, w = a.shape
    for i in prange(h):
        for j in range(w):
            result[i, j] = a[i, j] * b[i, j]
    return result


@njit(cache=True, parallel=NUMBA_PARALLEL, fastmath=True)
def _wbns_post_process_kernel(img: np.ndarray,
                               background: np.ndarray,
                               noise_smooth: np.ndarray,
                               bg_scale: float,
                               noise_scale_bg: float,
                               noise_scale_img: float,
                               noise_threshold: float) -> tuple:
    """
    Combined WBNS post-processing kernel — all arithmetic in one JIT pass:
      1. Noise thresholding
      2. Background subtraction + positivity
      3. Noise subtraction from background-corrected image + positivity
      4. Noise subtraction from original image + positivity

    Returns (bg_noise_corrected, noise_corrected).
    """
    h, w = img.shape
    bg_noise_corrected = np.empty_like(img)
    noise_corrected    = np.empty_like(img)

    for i in prange(h):
        for j in range(w):
            ns = noise_smooth[i, j]
            # Clip noise smooth to threshold
            if ns > noise_threshold:
                ns = noise_threshold

            bg_sub = img[i, j] - bg_scale * background[i, j]
            if bg_sub < 0.0:
                bg_sub = 0.0

            bgn = bg_sub - noise_scale_bg * ns
            if bgn < 0.0:
                bgn = 0.0
            bg_noise_corrected[i, j] = bgn

            nc = img[i, j] - noise_scale_img * ns
            if nc < 0.0:
                nc = 0.0
            noise_corrected[i, j] = nc

    return bg_noise_corrected, noise_corrected


# ---------------------------------------------------------------------------
# Public API — same interface as the original functions
# ---------------------------------------------------------------------------

def rescale_intensity_fast(image: np.ndarray,
                            out_min: float = 0.0,
                            out_max: float = 1.0) -> np.ndarray:
    """
    Numba-accelerated min-max intensity rescaling.
    Drop-in replacement for apply_rescale_intensity(image, out_min, out_max).
    Min/max computed in NumPy (safe, fast) then passed to parallel Numba kernel.
    """
    img = np.ascontiguousarray(image, dtype=np.float32)
    img_min = float(img.min())
    img_max = float(img.max())
    if NUMBA_AVAILABLE:
        return _rescale_intensity_kernel(img, img_min, img_max, float(out_min), float(out_max))
    # NumPy fallback
    if img_max == img_min:
        return np.full_like(img, out_min)
    return (img - img_min) / (img_max - img_min) * (out_max - out_min) + out_min


def clip_to_zero_fast(image: np.ndarray) -> np.ndarray:
    """
    Numba-accelerated positivity clipping.
    Drop-in replacement for image[image < 0] = 0.
    """
    img = np.ascontiguousarray(image, dtype=np.float32)
    if NUMBA_AVAILABLE:
        return _clip_to_zero_kernel(img)
    return np.clip(img, 0.0, None)


def invert_fast(image: np.ndarray) -> np.ndarray:
    """
    Numba-accelerated image inversion (max - image).
    Drop-in replacement for invert_image().
    Max computed in NumPy (safe) then passed to parallel Numba kernel.
    """
    img = np.ascontiguousarray(image, dtype=np.float32)
    img_max = float(img.max())
    if NUMBA_AVAILABLE:
        return _invert_kernel(img, img_max)
    return img_max - img


def multiply_fast(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Numba-accelerated elementwise multiply.
    Drop-in replacement for a * b.
    """
    a = np.ascontiguousarray(a, dtype=np.float32)
    b = np.ascontiguousarray(b, dtype=np.float32)
    if NUMBA_AVAILABLE:
        return _elementwise_multiply_kernel(a, b)
    return a * b


def wbns_post_process_fast(img: np.ndarray,
                            background: np.ndarray,
                            noise_smooth: np.ndarray,
                            bg_scale: float = 0.65,
                            noise_scale_bg: float = 0.2,
                            noise_scale_img: float = 0.7,
                            noise_threshold: float = None) -> tuple:
    """
    Numba-accelerated WBNS post-processing.

    Replaces the final arithmetic block of wbns_func — background
    subtraction, noise thresholding, and dual noise correction — in a
    single parallel kernel pass instead of 6 separate NumPy operations.

    Parameters
    ----------
    img : np.ndarray float32
    background : np.ndarray float32  (Gaussian-smoothed wavelet background)
    noise_smooth : np.ndarray float32 (Gaussian-smoothed noise component)
    bg_scale : float   coefficient for background subtraction (default 0.65)
    noise_scale_bg : float  noise coefficient for bg-corrected image (0.2)
    noise_scale_img : float noise coefficient for original image (0.7)
    noise_threshold : float  2-sigma threshold; computed from noise_smooth if None

    Returns
    -------
    bg_noise_corrected, noise_corrected : np.ndarray, np.ndarray
    """
    img    = np.ascontiguousarray(img,         dtype=np.float32)
    bg     = np.ascontiguousarray(background,  dtype=np.float32)
    ns     = np.ascontiguousarray(noise_smooth, dtype=np.float32)

    if noise_threshold is None:
        noise_threshold = float(ns.mean() + 2.0 * ns.std())

    if NUMBA_AVAILABLE:
        return _wbns_post_process_kernel(
            img, bg, ns,
            float(bg_scale), float(noise_scale_bg), float(noise_scale_img),
            float(noise_threshold)
        )

    # NumPy fallback
    ns_clipped = np.clip(ns, 0.0, noise_threshold)
    bg_sub = np.clip(img - bg_scale * bg, 0.0, None)
    bg_noise_corrected = np.clip(bg_sub - noise_scale_bg * ns_clipped, 0.0, None)
    noise_corrected    = np.clip(img    - noise_scale_img * ns_clipped, 0.0, None)
    return bg_noise_corrected, noise_corrected


# ---------------------------------------------------------------------------
# Warm-up function — call once at startup to trigger JIT compilation
# in the background so the first real image is fast
# ---------------------------------------------------------------------------

def warmup_numba():
    """
    Pre-compile all Numba kernels using a tiny dummy image.
    Call this once at startup (e.g. from run_pycat_func) so the user
    never experiences the compilation delay during actual analysis.
    """
    if not NUMBA_AVAILABLE:
        return
    dummy = np.random.rand(64, 64).astype(np.float32)
    dummy2 = np.random.rand(64, 64).astype(np.float32)
    rescale_intensity_fast(dummy, 0.0, 1.0)
    clip_to_zero_fast(dummy)
    invert_fast(dummy)
    multiply_fast(dummy, dummy2)
    wbns_post_process_fast(dummy, dummy2, dummy2)
    print("[PyCAT Numba] JIT kernels compiled and cached.")
