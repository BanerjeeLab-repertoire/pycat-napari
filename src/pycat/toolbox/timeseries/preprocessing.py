"""Time-series stack preprocessing science - split out of timeseries_condensate_tools (1.6.247).

upscale_stack_to_zarr upsamples a (T,H,W) stack frame-by-frame into a lazy zarr store (so Cellpose sees
objects at a workable pixel size); _cellpose_min_diameter_px is the target minimum diameter that upscaling
aims for. Pure science (no napari/Qt). Moved VERBATIM - no resampling change. Reads/writes via frame_access.
"""
from __future__ import annotations

import numpy as np
from pycat.toolbox.timeseries.frame_access import _read_source_frame, _ZarrStack, _compute_stack_global_range, _session_zarr_dir


def _cellpose_min_diameter_px():
    """Cellpose works best when objects are roughly >=~30 px across at the
    resolution it sees. Returns the target minimum cell diameter in px that
    upscaling should try to reach."""
    return 30.0


def upscale_stack_to_zarr(stack_like, factor, progress_cb=None):
    """Upscale a (T,H,W) stack frame-by-frame into a zarr store on disk and
    return a lazy _ZarrStack wrapper (reads frames on demand — snappy after
    processing, like the rest of the TS pipeline).

    Each frame is upscaled with order-1 (bilinear) interpolation. Frames are
    written to zarr as they complete so the full upscaled stack is never held
    in RAM at once.
    """
    import os as _os
    import zarr as _zarr
    from skimage.transform import rescale as _rescale

    f = max(1, int(factor))
    n_t = stack_like.shape[0]
    H, W = stack_like.shape[-2], stack_like.shape[-1]
    Hs, Ws = int(round(H * f)), int(round(W * f))

    out_dir = _os.path.join(_session_zarr_dir(), f"upscaled_{f}x_{_os.getpid()}_{id(stack_like)}")
    z_out = _zarr.open(out_dir, mode='w',
                       shape=(n_t, Hs, Ws), chunks=(1, Hs, Ws),
                       dtype=np.float32)
    # Global range so the upscaled stack keeps its true intensity trend.
    _g_range = _compute_stack_global_range(stack_like, n_t)
    for t in range(n_t):
        frame = _read_source_frame(stack_like, t, global_range=_g_range).astype(np.float32)
        if f == 1:
            up = frame
        else:
            up = _rescale(frame, f, order=1, anti_aliasing=True,
                          preserve_range=True).astype(np.float32)
        z_out[t] = up
        if progress_cb:
            progress_cb(t + 1, n_t)
    return _ZarrStack(_zarr.open(out_dir, mode='r'))
