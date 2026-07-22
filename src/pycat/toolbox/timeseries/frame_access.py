"""Lazy zarr-backed frame access - split out of timeseries_condensate_tools (1.6.244).

The per-session zarr scratch dir, the source-frame reader that honours the lazy-read guard (never a
frame-0 collapse), the global intensity-range scan, and the _ZarrStack wrapper napari scrubs without dask
or recomputation. Moved VERBATIM - materialize/read semantics unchanged. Pure infrastructure with no
napari/Qt dependency; the analysis and worker code import from here.
"""
from __future__ import annotations

from typing import Optional
import numpy as np
import tempfile as _tempfile


def _session_zarr_dir():
    """Return a per-session temp dir for zarr stores (created once)."""
    if not hasattr(_session_zarr_dir, '_path'):
        _session_zarr_dir._path = _tempfile.mkdtemp(prefix='pycat_ts_')
    return _session_zarr_dir._path


def _read_source_frame(stack_data, t, global_range=None):
    """
    Read frame t from any stack type (numpy, zarr, _ZarrTYX, dask) and
    normalize to [0, 1] float32.

    skimage's equalize_adapthist (used in pre_process_image) requires float
    images in [-1, 1].  Raw IMS/TIFF stacks are uint16 (0–65535) or uint8
    (0–255).  Normalizing here ensures every consumer — preprocessing,
    background removal, and analysis — receives correctly scaled data,
    matching what dtype_conversion_func does in the standard 2D pipeline.

    global_range : (min, max) or None
        If given, normalise against this FIXED range (shared across every
        frame of the stack) instead of the frame's own min/max. This is
        essential for time-series intensity analysis: per-frame min/max
        normalisation makes a growing focus appear to plateau or decay,
        because the rising per-frame max (the denominator) shrinks the
        normalised value of a focus even as its raw intensity increases.
        Using one global range preserves the true intensity trend over time.
    """
    frame = stack_data[t]
    if hasattr(frame, 'compute'):
        frame = frame.compute()
    arr = np.asarray(frame).astype(np.float32)
    if global_range is not None:
        gmn, gmx = float(global_range[0]), float(global_range[1])
        if gmx > gmn:
            arr = (arr - gmn) / (gmx - gmn + 1e-8)
            arr = np.clip(arr, 0.0, 1.0)
        return arr
    # Fallback: per-frame normalisation (used only where a single frame is
    # read in isolation and cross-frame comparison isn't involved).
    mn, mx = arr.min(), arr.max()
    if mx > 1.0:
        arr = (arr - mn) / (mx - mn + 1e-8)
    return arr


def _compute_stack_global_range(stack_data, n_t):
    """Compute (min, max) across ALL frames of a stack, read one frame at a
    time so the whole stack is never held in RAM. Used to normalise a
    time-series against one fixed scale (preserving the intensity trend)."""
    gmn, gmx = np.inf, -np.inf
    for t in range(n_t):
        frame = stack_data[t]
        if hasattr(frame, 'compute'):
            frame = frame.compute()
        a = np.asarray(frame, dtype=np.float32)
        fmn, fmx = float(a.min()), float(a.max())
        if fmn < gmn:
            gmn = fmn
        if fmx > gmx:
            gmx = fmx
    if not np.isfinite(gmn) or not np.isfinite(gmx):
        return (0.0, 1.0)
    return (gmn, gmx)


def _get_zarr_dir_path(stack_like) -> Optional[str]:
    """
    Return the filesystem directory path backing a stack, if it is already
    a filesystem-backed zarr array (e.g. a _ZarrStack wrapper produced by
    lazy stack preprocessing).  Returns None if the stack is a plain
    in-memory array or otherwise not filesystem-zarr-backed.
    """
    import zarr as _zarr
    z = getattr(stack_like, '_z', None)  # _ZarrStack wrapper
    if z is None and hasattr(stack_like, 'store'):
        z = stack_like  # already a raw zarr Array
    if z is not None:
        # ── A CAPABILITY question, not a CLASS check ────────────────────────────
        #
        # This was ``isinstance(store, _zarr.storage.DirectoryStore)`` — and **zarr 3 renamed that
        # class to ``LocalStore``.** A class check breaks the day the class is renamed, which is
        # the day the BioIO migration needs it.
        #
        # Worse: zarr 3's ``LocalStore`` exposes its path as ``.root``, not ``.path``. So even
        # after fixing the name, a bare ``store.path`` would return ``None`` on zarr 3 —
        # **silently** — and PyCAT would copy a stack it did not need to copy.
        from pycat.file_io.zarr_compat import store_path
        return store_path(z)
    return None


def _materialize_stack_to_zarr(stack_like, n_frames: int, H: int, W: int,
                                prefix: str = 'ts_analysis_src') -> str:
    """
    Ensure `stack_like` is available as a filesystem zarr DirectoryStore and
    return its path, so that worker processes can open frames independently
    by path rather than requiring large arrays to be pickled through IPC.

    If the stack is already filesystem-zarr-backed (typical after the lazy
    stack preprocessing step), this returns the existing path immediately
    with no copying.  Otherwise every frame is read once and written to a
    new temporary zarr store — a one-time cost paid before parallel
    dispatch, and far cheaper than the per-frame analysis work that follows.
    """
    import zarr as _zarr
    import os

    existing = _get_zarr_dir_path(stack_like)
    if existing and os.path.isdir(existing):
        return existing

    tmp_dir  = _session_zarr_dir()
    tmp_path = os.path.join(tmp_dir, f'{prefix}_{id(stack_like)}')
    z_out = _zarr.open(tmp_path, mode='w',
                       shape=(n_frames, H, W), chunks=(1, H, W),
                       dtype=np.float32)
    # One global range across all frames so intensity trends over time are
    # preserved (per-frame min/max distorts a growing/decaying signal).
    g_range = _compute_stack_global_range(stack_like, n_frames)
    for t in range(n_frames):
        z_out[t] = _read_source_frame(stack_like, t, global_range=g_range)
    return tmp_path


class _ZarrStack:
    """
    Lightweight napari-compatible wrapper around a zarr Array.
    Presents shape/dtype/ndim and reads frames on demand without dask.
    Zarr reads only the chunk(s) needed for the current slider position.

    **The array protocol is refused, not faked.** ``__array__`` used to call
    ``np.asarray(self._z)`` — the whole zarr, off disk, from any thumbnail or contrast
    estimate — and ``transpose()`` used to return **frame 0** for whatever axes you asked
    for. Both were the bug that ``lazy_guard`` exists to stop; both were outside the guard's
    scope because the guard walked ``file_io/`` and this class lives in ``toolbox/``.
    See ``pycat.file_io.lazy_guard``.
    """
    def __init__(self, z):
        self._z    = z
        self.shape = z.shape
        self.dtype = z.dtype
        self.ndim  = z.ndim

    def __getitem__(self, idx):
        return np.asarray(self._z[idx])

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)

    def __len__(self):
        return self.shape[0]

    # `transpose()` is deliberately ABSENT. It used to return frame 0 broadcast to (1, Y, X)
    # for any requested axes. The three `_ImsReader*` wrappers have never had one and napari
    # loads them without complaint — absence is a path napari already handles, and it is the
    # only honest answer a lazy stack can give. A caller that genuinely needs a transposed
    # stack must ask for the read: `materialize_stack(layer).transpose(...)`.
