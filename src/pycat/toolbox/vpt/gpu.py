"""VPT GPU detection kernels — the pure cupy code, split out of detection.py (vpt_detection_subsplit).

`blob_log_gpu` (the cupy Laplacian-of-Gaussian blob detector, the GPU counterpart of skimage's blob_log)
and `_gpu_build_id` (a cupy build fingerprint used to memoise the GPU/CPU equivalence verdict). Moved
VERBATIM out of `detection.py`, which re-exports them and calls them on the core detection path. cupy is
imported lazily inside the functions so this module imports fine on a CPU-only box.

The equivalence GATE that consults these (`gpu_matches_cpu`, `_run_gpu_equivalence_check`, the session
memo) deliberately stays in `detection.py`: it is coupled to `detect_beads_frame` and the equivalence
guard tests monkeypatch it there.
"""
from __future__ import annotations

import numpy as np

def blob_log_gpu(image, min_sigma=1.0, max_sigma=5.0, num_sigma=5,
                 threshold=0.02, overlap=0.5):
    """GPU-accelerated Laplacian-of-Gaussian blob detection.

    Reproduces skimage.feature.blob_log: builds the scale-normalised LoG cube
    (-gaussian_laplace(img, s) * s**2 over num_sigma scales), finds 3D local
    maxima above threshold, converts the scale index to a sigma, and prunes
    overlapping blobs. The expensive part — the per-scale Gaussian convolutions
    — runs on the GPU (keeping the whole cube on-device to avoid per-scale
    transfer), which is where blob_log spends ~all its time. Results match the
    CPU path within floating-point tolerance.

    Falls back to skimage.blob_log on the CPU if CuPy/GPU is unavailable, so it
    is always safe to call.

    Returns an (N, 3) array of (y, x, sigma), same as skimage.blob_log.
    """
    from skimage import feature as skfeature
    try:
        from pycat.toolbox.gpu_utils import gpu_available
    except Exception:
        gpu_available = lambda: False

    if not gpu_available():
        return skfeature.blob_log(
            image, min_sigma=min_sigma, max_sigma=max_sigma,
            num_sigma=num_sigma, threshold=threshold, overlap=overlap)

    import cupy as cp
    import cupyx.scipy.ndimage as cpnd
    from skimage.feature.blob import _prune_blobs
    from skimage.feature import peak_local_max

    img = cp.asarray(image, dtype=cp.float32)
    scales = np.linspace(min_sigma, max_sigma, num_sigma)
    # scale-normalised LoG cube, built and kept on the GPU (the expensive part —
    # the per-scale Gaussian convolutions — is what runs on-device).
    cube_gpu = cp.empty((num_sigma,) + img.shape, dtype=cp.float32)
    for i, s in enumerate(scales):
        cube_gpu[i] = -cpnd.gaussian_laplace(img, float(s)) * (float(s) ** 2)

    # Move the finished cube to the CPU and finish with skimage's EXACT peak
    # finder (peak_local_max) and pruning, so results are bit-for-bit the same
    # as skimage.blob_log. A raw (cube == maximum_filter) comparison does NOT
    # match skimage — peak_local_max deduplicates plateau/tie maxima and handles
    # borders differently — so we defer to it rather than reimplement it. The
    # convolutions (the costly step) still ran on the GPU.
    cube = cp.asnumpy(cube_gpu)
    # blob_log stores the scale as the LAST axis for peak_local_max; skimage
    # transposes the (scale, y, x) cube to (y, x, scale). Match that.
    image_cube = np.moveaxis(cube, 0, -1)
    local_maxima = peak_local_max(
        image_cube, threshold_abs=threshold, threshold_rel=None,
        exclude_border=False, footprint=np.ones((3,) * image_cube.ndim))
    if local_maxima.size == 0:
        return np.empty((0, 3))
    lm = local_maxima.astype(np.float64)
    # columns: y, x, scale_index → replace scale index with sigma
    sigmas_of_peaks = scales[local_maxima[:, -1]]
    lm = np.hstack([lm[:, :-1], sigmas_of_peaks[:, np.newaxis]])
    try:
        pruned = _prune_blobs(lm, overlap, sigma_dim=1)
    except TypeError:
        pruned = _prune_blobs(lm, overlap)
    return pruned


def _gpu_build_id() -> str:
    """The cupy/driver build a verdict belongs to.

    Part of the cache key because a cupy or driver swap mid-session is the one
    thing that could legitimately change the answer. Cheap to read, and it means
    the cache can never outlive the build it was measured on.
    """
    try:
        import cupy
        return (f"{getattr(cupy, '__version__', '?')}/"
                f"{cupy.cuda.runtime.runtimeGetVersion()}")
    except Exception:  # broad-ok: optional_probe — optional-backend version probe — 'no-cupy' when CuPy is absent, not a scientific result
        return 'no-cupy'
