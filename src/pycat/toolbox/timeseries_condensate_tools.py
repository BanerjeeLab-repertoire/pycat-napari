"""
PyCAT Time-Series Condensate Analysis
======================================
Tracks total condensate area per cell across all frames of a (T, H, W)
image stack, using a fixed cell mask from a reference frame with optional
phase-correlation drift correction between frames.

Algorithm per frame
-------------------
1. Apply drift correction to the frame relative to the reference frame
   (optional, uses phase cross-correlation via skimage).
2. For each labeled cell in the fixed mask, run condensate segmentation
   (segment_subcellular_objects) using the bounding-box crop optimisation.
3. Compute per-cell metrics:
      total_condensate_area_px  — sum of refined puncta mask pixels in cell
      total_condensate_area_um2 — converted to µm²
      condensate_fraction       — condensate area / cell area
      n_condensates             — number of individual condensate objects
      mean_condensate_area_um2  — mean area per individual condensate
4. Aggregate all frames into a tidy DataFrame indexed by (frame, cell_label).

Integration
-----------
Added to CondensateAnalysisUI.setup_ui() as:
    self.central_manager.toolbox_functions_ui._add_run_timeseries_condensate_analysis(
        layout=self.condensate_layout)

And to MenuManager._add_analysis_methods_to_menu():
    'Time-Series Condensate Analysis': (
        self.central_manager.analysis_methods_ui._switch_to_condensate_analysis,
        {'base_data_repository': ...}
    )

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo

Date
----
    2025
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import skimage as sk
import napari
from napari.utils.notifications import (
    show_info as napari_show_info,
    show_warning as napari_show_warning,
)
from PyQt5.QtWidgets import (
    QSizePolicy,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QCheckBox, QSpinBox, QDoubleSpinBox, QGroupBox, QFormLayout,
    QProgressBar,
)
from PyQt5.QtCore import QThread, pyqtSignal

from pycat.toolbox.segmentation_tools import segment_subcellular_objects
from pycat.toolbox.ts_cache_manager import (
    get_cache_paths, cache_exists, write_meta, discard_cache, cache_size_mb
)
from pycat.ui.ui_utils import show_dataframes_dialog


# ---------------------------------------------------------------------------
# Lazy stack preprocessing
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Zarr-backed frame-by-frame processing
# ---------------------------------------------------------------------------
# Architecture: process every frame once in a background worker, writing
# each frame immediately to a zarr store on disk.  The zarr store is then
# handed to napari as a _ZarrStack wrapper — lazy reads, zero dask, no
# recomputation on slider scrub, no SSL crash.

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
        store = getattr(z, 'store', None)
        if isinstance(store, _zarr.storage.DirectoryStore):
            return store.path
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


def estimate_temporal_correlation(
    stack_data, n_sample_pairs: int = 20,
) -> dict:
    """
    Estimate frame-to-frame correlation in a time-series stack to check
    whether the acquisition is in an "oversampling regime" where
    pseudo-3D tri-planar temporal filtering (treating T like Z — see
    pseudo3d_tri_planar_filter) is justified.

    The same physical argument that makes tri-planar filtering valid for
    a Z-stack — genuine correlation between adjacent slices, typically
    from Nyquist-or-better axial sampling — only transfers to the time
    axis when frames are acquired fast enough relative to the sample's
    dynamics that adjacent frames are still highly similar. A slow
    time-lapse (minutes between frames, substantial condensate movement/
    fusion/fission between frames) does NOT have this property, and
    applying tri-planar-across-T filtering there would blur together
    frames that are only coincidentally adjacent in the file, not
    genuinely correlated — the opposite of what the technique is for.

    Parameters
    ----------
    stack_data : array-like, shape (T, H, W)
        Raw (unprocessed) time-series stack. Sampled, not read in full,
        for speed on large stacks.
    n_sample_pairs : int
        Number of consecutive-frame pairs to sample (evenly spaced across
        the stack) for the correlation estimate.

    Returns
    -------
    dict with keys:
        mean_correlation   : mean Pearson correlation between consecutive
                             sampled frame pairs (0-1, higher = more
                             oversampled / more redundant between frames)
        min_correlation, max_correlation
        regime             : 'oversampled' (mean_correlation > 0.9),
                             'moderate' (0.7-0.9),
                             'undersampled' (< 0.7)
        recommendation     : human-readable guidance string
    """
    n_t = stack_data.shape[0]
    if n_t < 2:
        return dict(mean_correlation=np.nan, regime='insufficient_data',
                    recommendation='Need at least 2 frames to estimate temporal correlation.')

    n_pairs = min(n_sample_pairs, n_t - 1)
    sample_indices = np.linspace(0, n_t - 2, n_pairs, dtype=int)

    correlations = []
    for t in sample_indices:
        f0 = np.asarray(_read_source_frame(stack_data, int(t)))
        f1 = np.asarray(_read_source_frame(stack_data, int(t) + 1))
        f0_flat = f0.ravel().astype(np.float64)
        f1_flat = f1.ravel().astype(np.float64)
        if f0_flat.std() < 1e-9 or f1_flat.std() < 1e-9:
            continue
        corr = float(np.corrcoef(f0_flat, f1_flat)[0, 1])
        correlations.append(corr)

    if not correlations:
        return dict(mean_correlation=np.nan, regime='insufficient_data',
                    recommendation='Could not compute correlation (flat/uniform frames sampled).')

    mean_corr = float(np.mean(correlations))
    min_corr  = float(np.min(correlations))
    max_corr  = float(np.max(correlations))

    if mean_corr > 0.9:
        regime = 'oversampled'
        rec = (f"Mean frame-to-frame correlation {mean_corr:.2f} — this acquisition "
               f"is temporally oversampled. Pseudo-3D tri-planar temporal filtering "
               f"is well-justified and should improve consistency without blurring "
               f"real dynamics.")
    elif mean_corr > 0.7:
        regime = 'moderate'
        rec = (f"Mean frame-to-frame correlation {mean_corr:.2f} — moderate temporal "
               f"correlation. Tri-planar temporal filtering may help but could "
               f"slightly soften fast dynamics; inspect results before relying on it "
               f"for quantitative analysis.")
    else:
        regime = 'undersampled'
        rec = (f"Mean frame-to-frame correlation {mean_corr:.2f} — frames change "
               f"substantially between timepoints. Tri-planar temporal filtering is "
               f"NOT recommended here: it would blend together frames that are only "
               f"coincidentally adjacent, not genuinely correlated, and could blur "
               f"or misrepresent real condensate dynamics.")

    return dict(mean_correlation=mean_corr, min_correlation=min_corr,
               max_correlation=max_corr, regime=regime, recommendation=rec)


class _ZarrStack:
    """
    Lightweight napari-compatible wrapper around a zarr Array.
    Presents shape/dtype/ndim and reads frames on demand without dask.
    Zarr reads only the chunk(s) needed for the current slider position.
    """
    def __init__(self, z):
        self._z    = z
        self.shape = z.shape
        self.dtype = z.dtype
        self.ndim  = z.ndim

    def __getitem__(self, idx):
        return np.asarray(self._z[idx])

    def __array__(self, dtype=None):
        arr = np.asarray(self._z)
        return arr if dtype is None else arr.astype(dtype)

    def __len__(self):
        return self.shape[0]

    def transpose(self, *axes):
        return np.asarray(self._z[0])[np.newaxis]


# ---------------------------------------------------------------------------
# Parallel frame processing helpers
# ---------------------------------------------------------------------------

def _worker_read_frame(t, src_desc):
    """Read frame t inside a worker subprocess, from whatever source the
    descriptor names. Top-level + picklable so ProcessPoolExecutor can use it.

    Supported descriptors:
      {'kind': 'zarr', 'path': <dir>}                    filesystem zarr store
      {'kind': 'tiff', 'path': <file>, 'nc': N, 'ci': C} multipage TIFF, page seek
    Returns a float32 2D array (NOT yet globally normalised — the caller applies
    the global range).
    """
    import numpy as np
    kind = src_desc.get('kind')
    if kind == 'tiff':
        import tifffile as _tf
        # Open per-call: TIFF handles are cheap to open and this keeps the
        # worker stateless (a persistent handle can't be pickled across the
        # pool). tifffile memory-maps, so repeated opens are not full reads.
        with _tf.TiffFile(src_desc['path']) as _tif:
            try:
                pages = _tif.series[0].pages
            except Exception:
                pages = _tif.pages
            nc = int(src_desc.get('nc', 1)) or 1
            ci = int(src_desc.get('ci', 0))
            page = pages[int(t) * nc + ci]
            return np.asarray(page.asarray()).astype(np.float32)
    # default: zarr
    import zarr as _zarr
    src = _zarr.open(src_desc['path'], mode='r')
    return np.asarray(src[t]).astype(np.float32)


def _process_frame_worker(args):
    """
    Top-level picklable function for ProcessPoolExecutor.
    Reads one pre-normalised frame from a filesystem zarr store,
    applies the named processing function, and returns (t, result).
    """
    import warnings
    import os
    # Suppress CuPy CUDA path warnings before any imports can trigger them.
    # Must use both filterwarnings AND env var because different Python
    # versions check them in different orders at process startup.
    warnings.filterwarnings("ignore", message="CUDA path could not be detected",
                            category=UserWarning)
    os.environ.setdefault("CUDA_PATH", "")  # prevents CuPy from searching

    t, src_desc, process_fn_name, process_fn_kwargs = args

    import numpy as np

    # src_desc describes how this subprocess should read frame t directly,
    # avoiding a pre-copy of the whole stack into a temp zarr on first run.
    #   {'kind': 'zarr',  'path': ...}                     — filesystem zarr
    #   {'kind': 'tiff',  'path': ..., 'nc':.., 'ci':..}   — multipage TIFF page seek
    # A global normalisation range is applied here (the copy used to do it).
    frame = _worker_read_frame(t, src_desc)
    _grange = process_fn_kwargs.get('_global_range')
    if _grange is not None:
        _gmn, _gmx = float(_grange[0]), float(_grange[1])
        if _gmx > _gmn:
            frame = np.clip((frame - _gmn) / (_gmx - _gmn + 1e-8), 0.0, 1.0)
    # else: source descriptor already yields [0,1] (pre-materialised zarr path)

    if process_fn_name == 'preprocess_and_bg_remove':
        # Combined single-pass mode: compute both stages from one read of
        # the source frame, avoiding a second full-stack disk round trip.
        # Fixes a bottleneck where preprocessing and background removal ran
        # as two entirely separate ProcessPoolExecutor passes — each frame
        # was read from disk, pickled to a worker, and written back twice.
        from pycat.toolbox.image_processing_tools import (
            pre_process_image, rb_gaussian_bg_removal_with_edge_enhancement)
        preproc_result = pre_process_image(frame,
                                           process_fn_kwargs['ball_radius'],
                                           process_fn_kwargs['window_size'],
                                           norm_max=process_fn_kwargs.get('norm_max'))
        preproc_result = np.asarray(preproc_result).astype(np.float32)
        # NOTE: no per-frame min/max renormalisation here. pre_process_image was
        # given the stack's global norm_max, so every frame is already on one
        # consistent scale. Re-normalising per frame (as this code used to) would
        # reintroduce the intensity-trend distortion — a brightening focus would
        # make later frames look dimmer because the per-frame max rises with it.
        bgrem_result = rb_gaussian_bg_removal_with_edge_enhancement(
            preproc_result, process_fn_kwargs['ball_radius'])
        return t, preproc_result, np.asarray(bgrem_result).astype(np.float32)

    if process_fn_name == 'preprocess':
        from pycat.toolbox.image_processing_tools import pre_process_image
        result = pre_process_image(frame,
                                   process_fn_kwargs['ball_radius'],
                                   process_fn_kwargs['window_size'],
                                   norm_max=process_fn_kwargs.get('norm_max'))
    elif process_fn_name == 'bg_remove':
        from pycat.toolbox.image_processing_tools import (
            rb_gaussian_bg_removal_with_edge_enhancement)
        result = rb_gaussian_bg_removal_with_edge_enhancement(
            frame, process_fn_kwargs['ball_radius'])
    else:
        result = frame

    return t, np.asarray(result).astype(np.float32)


class _StackProcessWorker(QThread):
    """
    Background worker that processes a (T, H, W) stack frame-by-frame and
    writes results to a zarr store on disk.

    Uses a ProcessPoolExecutor to run N_WORKERS frames in parallel — one
    worker per CPU core (capped at 8 to avoid RAM pressure on large stacks).
    Each worker is an independent process so the GIL is bypassed and all
    CPU cores are used.  Results are written to zarr in order as they
    complete so napari can display already-processed frames immediately.

    Speedup is roughly linear with core count up to memory bandwidth limits:
    a 4-core machine processes ~4× faster than the old serial approach.
    """
    progress    = pyqtSignal(int, int)   # (frames_done, total)
    finished    = pyqtSignal(str)        # zarr store path when all done
    finished2   = pyqtSignal(str)        # second zarr path (combined mode only)
    error       = pyqtSignal(str)

    def __init__(self, stack_data, zarr_path, process_fn_name, process_fn_kwargs,
                 n_t, H, W, n_workers=None, parent=None, zarr_path2=None,
                 pseudo3d_temporal=False):
        super().__init__(parent)
        self._stack_data        = stack_data
        self._path              = zarr_path
        self._path2             = zarr_path2   # combined mode: bg_remove output
        self._process_fn_name   = process_fn_name
        self._process_fn_kwargs = process_fn_kwargs
        self._n_t               = n_t
        self._H, self._W        = H, W
        self._pseudo3d_temporal = pseudo3d_temporal
        import os
        self._n_workers = n_workers or min(8, max(1, os.cpu_count() - 1))

    def _source_descriptor(self):
        """Return a picklable descriptor telling workers how to read frames
        directly, skipping the whole-stack pre-copy to zarr when the source is
        already a seekable file (a TIFF via _TiffPageStack, or a filesystem
        zarr such as IMS). Falls back to materialising a temp zarr for anything
        else (numpy/dask/non-seekable).

        Returns (descriptor_dict, needs_global_range_bool). When the source is a
        pre-materialised zarr the frames are already [0,1]-normalised, so the
        worker skips its own normalisation (needs_global_range=False).
        """
        import os
        import zarr as _zarr

        src = self._stack_data

        # 1) TIFF-backed lazy reader (_TiffPageStack) — read pages directly.
        #    Detect by duck-typing the attributes it exposes.
        _tiff_path = getattr(src, '_path', None)
        if (_tiff_path and isinstance(_tiff_path, str)
                and os.path.isfile(_tiff_path)
                and _tiff_path.lower().endswith(('.tif', '.tiff'))):
            desc = {'kind': 'tiff', 'path': _tiff_path,
                    'nc': int(getattr(src, '_nc', 1) or 1),
                    'ci': int(getattr(src, '_ci', 0))}
            return desc, True   # workers normalise with the global range

        # 2) Already a filesystem zarr directory (e.g. IMS-derived) — use as-is.
        src_zarr_path = None
        if isinstance(src, str) and os.path.isdir(src):
            src_zarr_path = src
        elif hasattr(src, 'store') and isinstance(
                getattr(src, 'store', None), _zarr.storage.DirectoryStore):
            src_zarr_path = src.store.path
        if src_zarr_path and os.path.isdir(src_zarr_path):
            # A raw filesystem zarr is NOT guaranteed [0,1]-normalised, so the
            # workers still apply the global range (cheap; preserves the trend).
            return {'kind': 'zarr', 'path': src_zarr_path}, True

        # 3) Fallback: materialise to a temp zarr (already [0,1]-normalised, so
        #    workers do NOT re-normalise).
        tmp_path = self._prepare_source_zarr()
        return {'kind': 'zarr', 'path': tmp_path}, False

    def _prepare_source_zarr(self):
        """
        Copy the source stack into a temporary zarr DirectoryStore so that
        worker processes can open it by filesystem path.

        IMS files use an HDF5-backed zarr store that is not a filesystem
        directory and cannot be re-opened by path in a subprocess.
        Any other non-filesystem source (numpy array, dask array) also
        needs to be materialised once before being handed to workers.

        Returns the path to a zarr DirectoryStore containing the
        normalised (float32, [0,1]) source frames.
        """
        import zarr as _zarr
        import os, tempfile

        src = self._stack_data
        src_zarr_path = None

        # Check if source is already a filesystem zarr DirectoryStore
        if isinstance(src, str) and os.path.isdir(src):
            src_zarr_path = src
        elif hasattr(src, 'store') and isinstance(getattr(src, 'store', None),
                                                    _zarr.storage.DirectoryStore):
            src_zarr_path = src.store.path

        if src_zarr_path and os.path.isdir(src_zarr_path):
            return src_zarr_path   # already a filesystem zarr — use directly

        # Need to materialise into a temp zarr directory
        tmp_dir = tempfile.mkdtemp(prefix='pycat_src_')
        tmp_path = os.path.join(tmp_dir, 'source')
        z_out = _zarr.open(tmp_path, mode='w',
                           shape=(self._n_t, self._H, self._W),
                           chunks=(1, self._H, self._W),
                           dtype=np.float32)
        # Normalise every frame against ONE global range so intensity trends
        # across time are preserved (per-frame min/max would make growing foci
        # appear to plateau/decay as the per-frame max rises).
        g_range = _compute_stack_global_range(src, self._n_t)
        for t in range(self._n_t):
            frame = _read_source_frame(src, t, global_range=g_range)
            z_out[t] = frame
            self.progress.emit(t + 1, self._n_t * 2)  # first half of progress
        return tmp_path

    def run(self):
        try:
            import zarr as _zarr
            from concurrent.futures import ProcessPoolExecutor, as_completed

            combined_mode = (self._process_fn_name == 'preprocess_and_bg_remove'
                             and self._path2 is not None)

            z = _zarr.open(
                self._path, mode='w',
                shape=(self._n_t, self._H, self._W),
                chunks=(1, self._H, self._W),
                dtype=np.float32,
            )
            z2 = None
            if combined_mode:
                z2 = _zarr.open(
                    self._path2, mode='w',
                    shape=(self._n_t, self._H, self._W),
                    chunks=(1, self._H, self._W),
                    dtype=np.float32,
                )

            # Get a source descriptor. For TIFF / filesystem-zarr sources this
            # skips the whole-stack pre-copy entirely (workers read frames
            # directly), which is the bulk of the first-run materialisation lag.
            src_desc, _needs_grange = self._source_descriptor()
            # src_path kept for the pseudo-3D pre-pass below, which still needs a
            # concrete zarr; only materialise for that when actually enabled.
            src_path = src_desc['path'] if src_desc.get('kind') == 'zarr' else None

            # ── Pseudo-3D temporal pre-pass ───────────────────────────────
            # Same technique as the Z-stack pipeline (see bg_removal_3d),
            # applied along T instead of Z: in an oversampling regime
            # (adjacent frames genuinely correlated — check via
            # estimate_temporal_correlation before enabling this), treating
            # the whole (T, H, W) stack like a pseudo-volume and running
            # tri-planar Gaussian pre-smoothing (XY, XT, YT planes averaged)
            # gives a temporally-consistent baseline before the existing
            # per-frame parallel pipeline runs — the per-frame dispatch
            # below is completely unmodified, it just reads from this
            # pre-smoothed source instead of the raw one.
            if self._pseudo3d_temporal:
                from pycat.toolbox.image_processing_tools import gaussian_smooth_3d_pseudo
                # The pseudo-3D pre-pass needs the whole stack as an array. If
                # we're on a direct-read path (no src_path), materialise the
                # source zarr now — this branch is opt-in and already reads the
                # whole stack, so the copy is not extra work here.
                if src_path is None:
                    src_path = self._prepare_source_zarr()
                    src_desc = {'kind': 'zarr', 'path': src_path}
                    _needs_grange = False
                raw_src = _zarr.open(src_path, mode='r')
                whole = np.asarray(raw_src).astype(np.float32)
                ball_radius = self._process_fn_kwargs.get('ball_radius', 15)
                smoothed = gaussian_smooth_3d_pseudo(
                    whole, sigma=max(1.0, ball_radius / 4))
                mn, mx = smoothed.min(), smoothed.max()
                if mx > mn:
                    smoothed = (smoothed - mn) / (mx - mn)
                smoothed_path = src_path + '_t3d_presmooth'
                z_smoothed = _zarr.open(
                    smoothed_path, mode='w', shape=smoothed.shape,
                    chunks=(1, self._H, self._W), dtype=np.float32)
                z_smoothed[:] = smoothed
                src_path = smoothed_path
                src_desc = {'kind': 'zarr', 'path': smoothed_path}
                _needs_grange = True  # smoothed store is [0,1] per-stack already

            # Global normalisation range. Workers normalise every frame by ONE
            # scale (a brightening focus must not make later frames look dimmer).
            # For a direct-read source (no pre-copy) compute it lazily here — one
            # cheap frame-at-a-time min/max pass, far cheaper than a full copy.
            _kwargs_with_norm = dict(self._process_fn_kwargs)
            if _needs_grange:
                if src_desc.get('kind') == 'zarr':
                    try:
                        _zs = _zarr.open(src_desc['path'], mode='r')
                        _g0 = float('inf'); _g1 = float('-inf')
                        for _t in range(self._n_t):
                            _a = np.asarray(_zs[_t])
                            _g0 = min(_g0, float(_a.min()))
                            _g1 = max(_g1, float(_a.max()))
                    except Exception:
                        _g0, _g1 = 0.0, 1.0
                else:  # tiff — read frames via the worker helper
                    _g0 = float('inf'); _g1 = float('-inf')
                    for _t in range(self._n_t):
                        _a = _worker_read_frame(_t, src_desc)
                        _g0 = min(_g0, float(_a.min()))
                        _g1 = max(_g1, float(_a.max()))
                if not np.isfinite(_g0) or not np.isfinite(_g1) or _g1 <= _g0:
                    _g0, _g1 = 0.0, 1.0
                _kwargs_with_norm['_global_range'] = (_g0, _g1)
                _kwargs_with_norm['norm_max'] = 1.0  # frames arrive in [0,1]
            else:
                # Pre-materialised zarr: frames already [0,1]; norm_max stays the
                # stack global max for pre_process_image's internal scaling.
                try:
                    _src_for_max = _zarr.open(src_desc['path'], mode='r')
                    _global_norm_max = float(np.asarray(_src_for_max[:]).max())
                except Exception:
                    _global_norm_max = 1.0
                _kwargs_with_norm['norm_max'] = _global_norm_max if _global_norm_max > 0 else 1.0

            args = [
                (t, src_desc, self._process_fn_name, _kwargs_with_norm)
                for t in range(self._n_t)
            ]

            done = 0
            offset = self._n_t   # progress offset after materialisation phase
            total_progress = self._n_t * (3 if self._pseudo3d_temporal else 2)
            batch_size = self._n_workers * 4

            def _dispatch(dispatch_args):
                """Run the parallel per-frame pass. Returns True on success.
                Raises to the caller if a worker fails."""
                nonlocal done
                with ProcessPoolExecutor(max_workers=self._n_workers) as executor:
                    for batch_start in range(0, self._n_t, batch_size):
                        batch = dispatch_args[batch_start:batch_start + batch_size]
                        futures = {executor.submit(_process_frame_worker, a): a[0]
                                   for a in batch}
                        for future in as_completed(futures):
                            result = future.result()
                            if combined_mode:
                                t_idx, preproc_res, bgrem_res = result
                                z[t_idx]  = preproc_res
                                z2[t_idx] = bgrem_res
                            else:
                                t_idx, res = result
                                z[t_idx] = res
                            done += 1
                            self.progress.emit(offset + done, total_progress)

            try:
                _dispatch(args)
            except Exception as _dispatch_err:
                # Safe fallback: if a direct-read source (TIFF) failed mid-run
                # (locked file, network hiccup, unexpected page layout),
                # materialise the source to a temp zarr and retry once. A zarr
                # source that failed is a real error and re-raises.
                if src_desc.get('kind') == 'tiff':
                    print("[PyCAT TimeSeries] Direct TIFF read failed mid-run "
                          f"({_dispatch_err}); falling back to zarr copy and "
                          "retrying.")
                    _fallback_path = self._prepare_source_zarr()
                    _fb_desc = {'kind': 'zarr', 'path': _fallback_path}
                    # Pre-materialised zarr is already [0,1]; drop the worker-side
                    # global-range normalisation and use norm_max instead.
                    _fb_kwargs = dict(_kwargs_with_norm)
                    _fb_kwargs.pop('_global_range', None)
                    try:
                        _fbmax = float(np.asarray(
                            _zarr.open(_fallback_path, mode='r')[:]).max())
                    except Exception:
                        _fbmax = 1.0
                    _fb_kwargs['norm_max'] = _fbmax if _fbmax > 0 else 1.0
                    done = 0
                    args = [(t, _fb_desc, self._process_fn_name, _fb_kwargs)
                            for t in range(self._n_t)]
                    _dispatch(args)
                else:
                    raise

            # ── Pseudo-3D temporal post-pass ──────────────────────────────
            # Tri-planar Gabor edge-enhancement on the whole output stack,
            # blended (averaged) with the per-frame result — same
            # "unmodified per-frame core, tri-planar pre/post passes
            # around it" pattern as the Z-stack pipeline. Only the primary
            # output path (self._path) gets this treatment; in combined
            # mode the bg_remove output (self._path2) already reflects the
            # temporally-smoothed source from the pre-pass and keeps its
            # own per-slice Gabor step from the composite 2D pipeline.
            if self._pseudo3d_temporal:
                from pycat.toolbox.image_processing_tools import gabor_filter_3d_pseudo
                out_whole = np.asarray(_zarr.open(self._path, mode='r')).astype(np.float32)
                gabor_whole = gabor_filter_3d_pseudo(out_whole)
                gmn, gmx = gabor_whole.min(), gabor_whole.max()
                if gmx > gmn:
                    gabor_whole = (gabor_whole - gmn) / (gmx - gmn)
                blended = (out_whole + gabor_whole) / 2.0
                z[:] = blended
                self.progress.emit(total_progress, total_progress)

            self.finished.emit(self._path)
            if combined_mode:
                self.finished2.emit(self._path2)

        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


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


def _add_ts_upscale_stack(ui_instance, layout=None, separate_widget=False):
    """Optional early upscale step for the time-series workflow.

    Upscales the raw stack frame-by-frame into a lazy zarr-backed stack, so the
    whole downstream pipeline (preprocess, Cellpose, condensate analysis) runs on
    the upscaled data — matching the 2D workflow order (upscale BEFORE
    preprocess). Optional and gated: if the data already meets Cellpose's
    resolution needs, upscaling is unnecessary and the check says so.
    """
    from PyQt5.QtWidgets import QGroupBox, QFormLayout, QPushButton, QSpinBox, QLabel

    grp = QGroupBox("Upscale Stack (optional)")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)

    stack_dropdown = ui_instance.create_layer_dropdown(napari.layers.Image)
    form.addRow("Raw stack layer:", stack_dropdown)

    factor_spin = QSpinBox(); factor_spin.setRange(1, 4); factor_spin.setValue(2)
    factor_spin.setToolTip("Integer upscale factor. 2 = double each dimension.")
    form.addRow("Upscale factor:", factor_spin)

    advice = QLabel("<span style='color:#888;font-size:9pt;'>Upscaling helps only "
                    "when objects are small relative to Cellpose's needs. Check "
                    "below before upscaling.</span>")
    advice.setWordWrap(True)
    form.addRow(advice)

    check_btn = QPushButton("Check if upscaling is needed")
    check_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(check_btn)

    def _on_check():
        name = stack_dropdown.currentText()
        try:
            layer = ui_instance.viewer.layers[name]
        except KeyError:
            napari_show_warning(f"Layer '{name}' not found."); return
        # Compare the current expected cell diameter (px) against Cellpose's
        # preferred minimum. If already comfortably above it, upscaling isn't
        # needed; otherwise recommend a factor to reach it.
        _dr = ui_instance.central_manager.active_data_class.data_repository
        cell_d = float(_dr.get('cell_diameter', 0) or 0)
        target = _cellpose_min_diameter_px()
        if cell_d <= 0:
            advice.setText("<span style='color:#f0a500;font-size:9pt;'>Set the "
                           "cell diameter first (measure a line) so I can tell "
                           "whether upscaling is needed.</span>")
            return
        if cell_d >= target:
            advice.setText(f"<span style='color:#5cb85c;font-size:9pt;'><b>Not "
                           f"needed</b> — cells are ~{cell_d:.0f} px, already "
                           f"above Cellpose's ~{target:.0f} px target. You can "
                           f"skip upscaling.</span>")
            factor_spin.setValue(1)
        else:
            rec = int(np.ceil(target / max(cell_d, 1e-6)))
            rec = max(2, min(4, rec))
            advice.setText(f"<span style='color:#f0a500;font-size:9pt;'><b>"
                           f"Recommended</b> — cells are ~{cell_d:.0f} px, below "
                           f"Cellpose's ~{target:.0f} px target. Try factor "
                           f"{rec}× (→ ~{cell_d*rec:.0f} px).</span>")
            factor_spin.setValue(rec)
    check_btn.clicked.connect(_on_check)

    run_btn = QPushButton("▶  Upscale Stack (lazy)")
    run_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    prog = QProgressBar(); prog.setVisible(False)
    form.addRow(run_btn); form.addRow(prog)

    class _UpWorker(QThread):
        progress = pyqtSignal(int, int)
        finished_ok = pyqtSignal(object, int)
        error = pyqtSignal(str)
        def __init__(self, stack, factor):
            super().__init__(); self._stack = stack; self._factor = factor
        def run(self):
            try:
                out = upscale_stack_to_zarr(
                    self._stack, self._factor,
                    progress_cb=lambda i, n: self.progress.emit(i, n))
                self.finished_ok.emit(out, self._factor)
            except Exception as e:
                import traceback; self.error.emit(traceback.format_exc())

    def _on_run():
        name = stack_dropdown.currentText()
        try:
            layer = ui_instance.viewer.layers[name]
        except KeyError:
            napari_show_warning(f"Layer '{name}' not found."); return
        data = layer.data
        if np.asarray(data).ndim != 3 and not hasattr(data, 'shape'):
            napari_show_warning("Upscaling needs a (T,H,W) stack."); return
        factor = factor_spin.value()
        if factor == 1:
            napari_show_info("Factor 1× — nothing to upscale."); return
        prog.setVisible(True); prog.setRange(0, 0); run_btn.setEnabled(False)

        worker = _UpWorker(data, factor)
        ui_instance._ts_upscale_worker = worker
        def _prog(i, n):
            prog.setRange(0, n); prog.setValue(i)
        def _done(out, f):
            prog.setVisible(False); run_btn.setEnabled(True)
            new_name = f"Upscaled {f}x [{name}]"
            dr = ui_instance.central_manager.active_data_class.data_repository
            _mpx_sq = float(dr.get('microns_per_pixel_sq', 0) or 0)
            _cl = float(np.sqrt(_mpx_sq)) if _mpx_sq > 0 else 0.0
            ui_instance.viewer.add_image(
                out, name=new_name,
                scale=(_cl / f, _cl / f) if _cl else None)
            # Downstream cell_diameter/ball_radius scale with the upscale factor.
            if dr.get('cell_diameter'):    dr['cell_diameter'] = float(dr['cell_diameter']) * f
            if dr.get('ball_radius'):      dr['ball_radius']   = float(dr['ball_radius']) * f
            ui_instance._record('ts_upscale_stack', {
                'stack_layer': name, 'factor': f})
            napari_show_info(f"Upscaled {f}× → '{new_name}' (lazy). "
                             f"Cell diameter / ball radius scaled ×{f}.")
        def _err(msg):
            prog.setVisible(False); run_btn.setEnabled(True)
            napari_show_warning("Upscale error — see terminal."); print(f"[PyCAT TS Upscale] {msg}")
        worker.progress.connect(_prog); worker.finished_ok.connect(_done)
        worker.error.connect(_err); worker.start()
    run_btn.clicked.connect(_on_run)

    target = layout if layout is not None else QVBoxLayout()
    target.addWidget(grp)
    return grp


def _add_lazy_preprocess_stack(ui_instance, layout=None, separate_widget=False):
    """
    Widget that builds lazy preprocessed and background-removed stacks from
    a raw image stack without processing any frames upfront.

    Designed for use in the Time-Series Condensate Analysis pipeline as a
    replacement for the standard Pre-process + Background Removal buttons,
    which process only the active (single) layer.

    The output dask layers are named consistently so the Time-Series
    Condensate Analysis widget can find them automatically.
    """
    from PyQt5.QtWidgets import QGroupBox, QFormLayout, QPushButton, QCheckBox

    grp = QGroupBox("Lazy Stack Pre-processing")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)

    stack_dropdown = ui_instance.create_layer_dropdown(napari.layers.Image)
    form.addRow("Raw stack layer:", stack_dropdown)

    preprocess_check = QCheckBox("Pre-process each frame")
    preprocess_check.setChecked(True)
    form.addRow("", preprocess_check)

    bg_check = QCheckBox("Background removal each frame")
    bg_check.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    bg_check.setChecked(True)
    form.addRow("", bg_check)

    # ── Pseudo-3D temporal filtering ────────────────────────────────────
    # Same tri-planar technique used for Z-stacks, applied along T. Only
    # justified when adjacent frames are genuinely correlated (a temporal
    # "oversampling" regime) — check first rather than assuming.
    corr_label = QLabel(
        "<span style='color:#888;font-size:9pt;'>"
        "Check frame-to-frame correlation before enabling temporal "
        "pseudo-3D filtering \u2014 it's only justified when adjacent "
        "frames are genuinely similar.</span>"
    )
    corr_label.setWordWrap(True)
    form.addRow(corr_label)

    check_corr_btn = QPushButton("Check Temporal Correlation")
    check_corr_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(check_corr_btn)

    pseudo3d_temporal_cb = QCheckBox("Pseudo-3D temporal filtering (tri-planar across T)")
    pseudo3d_temporal_cb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    pseudo3d_temporal_cb.setChecked(False)
    pseudo3d_temporal_cb.setToolTip(
        "Runs Gaussian pre-smoothing and Gabor edge-enhancement along XY, "
        "XT, and YT planes (averaged) instead of XY-only \u2014 the same "
        "technique used for Z-stack pseudo-3D filtering, applied to T. "
        "Only enable this if frame-to-frame correlation is high (check "
        "above first) \u2014 otherwise it will blur real dynamics between "
        "frames that aren't actually similar to each other. "
        "Adds a whole-stack pass before and after the per-frame pipeline."
    )
    form.addRow(pseudo3d_temporal_cb)

    def _on_check_correlation():
        from pycat.toolbox.timeseries_condensate_tools import estimate_temporal_correlation
        layer_name = stack_dropdown.currentText()
        try:
            layer = ui_instance.viewer.layers[layer_name]
        except KeyError:
            napari_show_warning(f"Layer '{layer_name}' not found.")
            return
        stack_data = layer.data
        if stack_data.ndim != 3:
            napari_show_warning("Correlation check requires a 3D (T,H,W) stack.")
            return

        result = estimate_temporal_correlation(stack_data)
        regime_colors = {
            'oversampled':  '#5cb85c',
            'moderate':     '#f0a500',
            'undersampled': '#d9534f',
        }
        color = regime_colors.get(result['regime'], '#aaa')
        corr_label.setText(
            f"<span style='color:{color};font-size:9pt;'>"
            f"<b>{result['regime'].upper()}</b> \u2014 "
            f"mean correlation {result.get('mean_correlation', float('nan')):.2f}. "
            f"{result['recommendation']}</span>"
        )
        napari_show_info(
            f"Temporal correlation: {result['regime']} "
            f"(mean r={result.get('mean_correlation', float('nan')):.2f})"
        )

    check_corr_btn.clicked.connect(_on_check_correlation)

    import os as _os
    _n_workers = min(8, max(1, _os.cpu_count() - 1))
    build_btn = QPushButton(f"▶  Process Stack  ({_n_workers} parallel workers)")
    build_btn.setToolTip(
        f"Processes all frames using {_n_workers} CPU cores in parallel,\n"
        "writing each frame to a zarr store on disk as it completes.\n"
        "Results are cached next to the source file and reloaded\n"
        "automatically on next open — no reprocessing needed."
    )
    # Worker references kept alive on ui_instance so GC doesn't kill them
    ui_instance._ts_workers = getattr(ui_instance, '_ts_workers', [])

    def _load_from_cache(cache_paths, layer_name, existing,
                         want_preproc, want_bgrem):
        """Reload zarr stores from cache into napari layers."""
        import zarr as _zarr
        if want_preproc and existing.get('preproc'):
            z = _zarr.open(str(cache_paths['preproc']), mode='r')
            wrapper = _ZarrStack(z)
            name = f"Pre-Processed {layer_name}"
            ui_instance.viewer.add_image(wrapper, name=name, colormap='green')
            ui_instance._ts_zarr_preproc = str(cache_paths['preproc'])
            napari_show_info(f"Loaded '{name}' from cache.")
        if want_bgrem and existing.get('bgrem'):
            z = _zarr.open(str(cache_paths['bgrem']), mode='r')
            wrapper = _ZarrStack(z)
            name = f"Enhanced Background Removed {layer_name}"
            ui_instance.viewer.add_image(wrapper, name=name, colormap='viridis')
            ui_instance._ts_zarr_bgrem = str(cache_paths['bgrem'])
            napari_show_info(f"Loaded '{name}' from cache.")

    def _on_discard_cache():
        source_file = getattr(ui_instance.central_manager.file_io,
                              '_ims_file_path', None) or                       getattr(ui_instance.central_manager.file_io,
                              'filePath', None)
        if not source_file:
            napari_show_warning("No source file known — nothing to discard.")
            return
        data_instance = ui_instance.central_manager.active_data_class
        ball_radius   = int(data_instance.data_repository.get('ball_radius', 50))
        window_size   = int(data_instance.data_repository.get('cell_diameter', 100)) // 2
        discard_cache(source_file, ball_radius, window_size)
        napari_show_info("Preprocessing cache discarded. Next run will reprocess.")

    discard_btn = QPushButton("🗑  Discard Cache")
    discard_btn.setToolTip(
        "Delete the cached preprocessed zarr stores for this file.\n"
        "Use this if you change processing parameters and want to\n"
        "force a full reprocess on the next run."
    )
    discard_btn.clicked.connect(_on_discard_cache)

    def _on_build():
        layer_name = stack_dropdown.currentText()
        try:
            layer = ui_instance.viewer.layers[layer_name]
        except KeyError:
            napari_show_warning(f"Layer '{layer_name}' not found.")
            return

        stack_data = layer.data
        if stack_data.ndim != 3:
            napari_show_warning("Lazy preprocessing requires a 3D (T, H, W) stack layer.")
            return

        data_instance = ui_instance.central_manager.active_data_class
        ball_radius   = int(data_instance.data_repository.get('ball_radius', 50))
        window_size   = int(data_instance.data_repository.get('cell_diameter', 100)) // 2

        H = stack_data.shape[1]
        max_radius = max(4, int(H * 0.05))
        ball_radius  = min(ball_radius, max_radius)
        window_size  = min(window_size, max_radius * 2)

        full_n_t = stack_data.shape[0]
        W        = stack_data.shape[2]

        # Respect user-defined frame range if set
        dr      = ui_instance.central_manager.active_data_class.data_repository
        t_start = int(dr.get('timeseries_frame_start', 0))
        t_end   = int(dr.get('timeseries_frame_end', full_n_t - 1))
        t_start = max(0, min(t_start, full_n_t - 1))
        t_end   = max(t_start, min(t_end, full_n_t - 1))
        n_t     = t_end - t_start + 1

        # Wrap the source so index 0..n_t-1 maps to t_start..t_end
        class _SlicedStack:
            def __init__(self, src, start, end):
                self._src   = src
                self._start = start
                n           = end - start + 1
                if hasattr(src, 'shape'):
                    self.shape = (n,) + src.shape[1:]
                else:
                    self.shape = (n, H, W)
                self.ndim = 3
            def __getitem__(self, idx):
                if isinstance(idx, (int, np.integer)):
                    return self._src[self._start + int(idx)]
                return self._src[self._start + idx]

        if t_start > 0 or t_end < full_n_t - 1:
            stack_data = _SlicedStack(stack_data, t_start, t_end)
            napari_show_info(
                f"Processing frame range {t_start}–{t_end} "
                f"({n_t} of {full_n_t} frames)."
            )

        # ── XY ROI crop ───────────────────────────────────────────────────
        roi_active = dr.get('timeseries_roi_active', False)
        y0 = int(dr.get('timeseries_roi_y0', 0))
        y1 = int(dr.get('timeseries_roi_y1', H))
        x0 = int(dr.get('timeseries_roi_x0', 0))
        x1 = int(dr.get('timeseries_roi_x1', W))

        # Clamp to actual frame dimensions
        y0, y1 = max(0, y0), min(H, y1)
        x0, x1 = max(0, x0), min(W, x1)

        if roi_active and (y0 > 0 or y1 < H or x0 > 0 or x1 < W):
            class _CroppedStack:
                """Wraps any stack and spatially crops every frame on read."""
                def __init__(self, src, _y0, _y1, _x0, _x1):
                    self._src = src
                    self._y0, self._y1 = _y0, _y1
                    self._x0, self._x1 = _x0, _x1
                    nh = _y1 - _y0
                    nw = _x1 - _x0
                    nt = src.shape[0] if hasattr(src, 'shape') else len(src)
                    self.shape = (nt, nh, nw)
                    self.ndim  = 3
                def __getitem__(self, idx):
                    frame = self._src[idx]
                    arr   = np.asarray(frame)
                    if arr.ndim == 2:
                        return arr[self._y0:self._y1, self._x0:self._x1]
                    return arr[:, self._y0:self._y1, self._x0:self._x1]

            stack_data = _CroppedStack(stack_data, y0, y1, x0, x1)
            H = y1 - y0
            W = x1 - x0
            napari_show_info(
                f"XY crop active: y[{y0}:{y1}] x[{x0}:{x1}] "
                f"→ {H}×{W}px per frame."
            )

        # Determine cache paths — use source file path if available
        source_file = getattr(ui_instance.central_manager.file_io,
                              '_ims_file_path', None) or                       getattr(ui_instance.central_manager.file_io,
                              'filePath', None)

        cache_paths = None
        if source_file:
            cache_paths = get_cache_paths(source_file, ball_radius, window_size)
            existing    = cache_exists(source_file, ball_radius, window_size)

            # If both caches exist, offer to reload instead of reprocessing
            if existing.get('preproc') or existing.get('bgrem'):
                sz = cache_size_mb(source_file)
                from PyQt5.QtWidgets import QMessageBox
                reply = QMessageBox.question(
                    None, "Preprocessing Cache Found",
                    f"A cached preprocessed stack was found for this file\n"
                    f"(ball_radius={ball_radius}, window_size={window_size})\n"
                    f"Cache size: {sz:.0f} MB\n\n"
                    f"Reload from cache?\n"
                    f"(Choose No to reprocess and overwrite the cache)",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    _load_from_cache(cache_paths, layer_name,
                                     existing, preprocess_check.isChecked(),
                                     bg_check.isChecked())
                    return

            # Use cache directory as zarr destination
            cache_paths['preproc'].parent.mkdir(parents=True, exist_ok=True)
            zarr_dir = str(cache_paths['preproc'].parent)
        else:
            zarr_dir = _session_zarr_dir()
            cache_paths = None

        build_btn.setEnabled(False)


        # Single shared progress bar and label for all workers —
        # avoids leaving multiple stuck bars on screen.
        prog_bar = QProgressBar()
        prog_bar.setMaximum(n_t * 2)
        prog_bar.setValue(0)
        prog_bar.setVisible(False)
        prog_label = QLabel("")
        prog_label.setWordWrap(True)
        prog_label.setVisible(False)
        build_btn.parent().layout().addWidget(prog_label)
        build_btn.parent().layout().addWidget(prog_bar)

        def _start_worker(source, fn_name, fn_kwargs, zarr_name, display_name,
                          colormap, on_done_cb=None):
            import zarr as _zarr
            import os
            zarr_path = os.path.join(zarr_dir, zarr_name)
            _zarr.open(zarr_path, mode='w',
                       shape=(n_t, H, W), chunks=(1, H, W), dtype=np.float32)
            z_arr   = _zarr.open(zarr_path, mode='r')
            wrapper = _ZarrStack(z_arr)
            ui_instance.viewer.add_image(wrapper, name=display_name,
                                         colormap=colormap)
            setattr(ui_instance, f'_ts_zarr_{zarr_name}', zarr_path)

            # Honor an applied Temporal Enhancement Optimizer choice: a
            # tri-planar / windowed winner maps onto the existing pseudo-3D
            # temporal path (temporally-coupled pre-smoothing). Per-frame /
            # pooled_stats winners leave the standard per-frame path in place.
            _te = {}
            try:
                _te = ui_instance.central_manager.active_data_class.data_repository.get(
                    'temporal_enhancement', {}) or {}
            except Exception:
                _te = {}
            _te_triplanar = _te.get('method') in ('triplanar', 'windowed_mean')
            _use_pseudo3d = pseudo3d_temporal_cb.isChecked() or _te_triplanar

            worker = _StackProcessWorker(
                source, zarr_path, fn_name, fn_kwargs, n_t, H, W,
                pseudo3d_temporal=_use_pseudo3d)
            ui_instance._ts_workers.append(worker)

            _n_stages = 3 if _use_pseudo3d else 2
            prog_bar.setValue(0)
            prog_bar.setMaximum(n_t * _n_stages)
            prog_bar.setVisible(True)
            prog_label.setText(f"Preparing frames…")
            prog_label.setVisible(True)

            def _on_progress(done, total):
                prog_bar.setValue(done)
                if done <= n_t:
                    prog_label.setText(
                        f"{display_name}: copying frame {done}/{n_t}…")
                elif done <= n_t * 2:
                    prog_label.setText(
                        f"{display_name}: processing frame {done - n_t}/{n_t}…")
                else:
                    prog_label.setText(
                        f"{display_name}: pseudo-3D temporal blend pass…")
                try:
                    ui_instance.viewer.layers[display_name].refresh()
                except Exception:
                    pass

            def _on_finished(path, _dn=display_name, _cb=on_done_cb):
                prog_bar.setValue(n_t * 2)
                napari_show_info(
                    f"'{_dn}' — all {n_t} frames processed.")
                if _cb:
                    # More work coming — keep bar visible, reset for next stage
                    prog_bar.setValue(0)
                    prog_label.setText("Starting next stage…")
                    _cb(path)
                else:
                    # Final step — hide bar
                    prog_bar.setVisible(False)
                    prog_label.setVisible(False)
                    build_btn.setEnabled(True)

            def _on_error(msg):
                prog_bar.setVisible(False)
                prog_label.setVisible(False)
                napari_show_warning("Processing error — see terminal.")
                print(f"[PyCAT TS Preprocess] ERROR:\n{msg}")
                build_btn.setEnabled(True)

            worker.progress.connect(_on_progress)
            worker.finished.connect(_on_finished)
            worker.error.connect(_on_error)
            worker.start()
            return worker, z_arr


        proc_source   = stack_data
        proc_zarr_ref = None

        def _start_bg(source):
            bg_name   = f"Enhanced Background Removed {layer_name}"
            zarr_name = (str(cache_paths['bgrem'].name)
                         if cache_paths else f"bgrem_{id(layer_name)}")

            def _after_bg(zarr_path):
                if cache_paths and source_file:
                    write_meta(source_file, ball_radius, window_size,
                               n_t, H, W)

            _start_worker(source, 'bg_remove',
                          {'ball_radius': ball_radius},
                          zarr_name, bg_name, 'viridis',
                          on_done_cb=_after_bg)

        if preprocess_check.isChecked() and bg_check.isChecked():
            # ── Combined single-pass mode ────────────────────────────────
            # Both stages are computed from one read of each source frame
            # inside a single ProcessPoolExecutor pass, instead of running
            # preprocessing to completion, writing it to disk, then reading
            # it all back for a second full background-removal pass. This
            # roughly halves wall-clock time and I/O for the default case
            # where both checkboxes are on.
            import zarr as _zarr, os as _os_

            preproc_name = f"Pre-Processed {layer_name}"
            bgrem_name   = f"Enhanced Background Removed {layer_name}"
            preproc_zarr_name = (str(cache_paths['preproc'].name)
                                 if cache_paths else f"preproc_{id(layer_name)}")
            bgrem_zarr_name   = (str(cache_paths['bgrem'].name)
                                 if cache_paths else f"bgrem_{id(layer_name)}")

            preproc_path = _os_.path.join(zarr_dir, preproc_zarr_name)
            bgrem_path   = _os_.path.join(zarr_dir, bgrem_zarr_name)

            _zarr.open(preproc_path, mode='w', shape=(n_t, H, W),
                      chunks=(1, H, W), dtype=np.float32)
            _zarr.open(bgrem_path, mode='w', shape=(n_t, H, W),
                      chunks=(1, H, W), dtype=np.float32)

            preproc_wrapper = _ZarrStack(_zarr.open(preproc_path, mode='r'))
            bgrem_wrapper   = _ZarrStack(_zarr.open(bgrem_path, mode='r'))
            ui_instance.viewer.add_image(preproc_wrapper, name=preproc_name,
                                         colormap='green')
            ui_instance.viewer.add_image(bgrem_wrapper, name=bgrem_name,
                                         colormap='viridis')
            ui_instance._ts_zarr_preproc = preproc_path
            ui_instance._ts_zarr_bgrem   = bgrem_path

            worker = _StackProcessWorker(
                stack_data, preproc_path, 'preprocess_and_bg_remove',
                {'ball_radius': ball_radius, 'window_size': window_size},
                n_t, H, W, zarr_path2=bgrem_path,
                pseudo3d_temporal=pseudo3d_temporal_cb.isChecked(),
            )
            ui_instance._ts_workers.append(worker)

            _n_stages = 3 if pseudo3d_temporal_cb.isChecked() else 2
            prog_bar.setValue(0); prog_bar.setMaximum(n_t * _n_stages)
            prog_bar.setVisible(True)
            prog_label.setText("Preparing frames…"); prog_label.setVisible(True)

            def _on_progress(done, total):
                prog_bar.setValue(done)
                if done <= n_t:
                    prog_label.setText(f"Copying frame {done}/{n_t}…")
                elif done <= n_t * 2:
                    prog_label.setText(
                        f"Preprocessing + BG removal: frame {done - n_t}/{n_t}…")
                else:
                    prog_label.setText("Pseudo-3D temporal blend pass…")
                for _name in (preproc_name, bgrem_name):
                    try:
                        ui_instance.viewer.layers[_name].refresh()
                    except Exception:
                        pass

            def _on_finished(_path):
                if cache_paths and source_file:
                    write_meta(source_file, ball_radius, window_size, n_t, H, W)
                prog_bar.setVisible(False)
                prog_label.setVisible(False)
                build_btn.setEnabled(True)
                napari_show_info(
                    f"'{preproc_name}' and '{bgrem_name}' — "
                    f"all {n_t} frames processed in a single combined pass."
                )

            def _on_error(msg):
                prog_bar.setVisible(False)
                prog_label.setVisible(False)
                napari_show_warning("Processing error — see terminal.")
                print(f"[PyCAT TS Preprocess] ERROR:\n{msg}")
                build_btn.setEnabled(True)

            worker.progress.connect(_on_progress)
            worker.finished.connect(_on_finished)   # fires once, after finished2
            worker.error.connect(_on_error)
            worker.start()

        elif preprocess_check.isChecked():
            br, ws = ball_radius, window_size
            proc_name  = f"Pre-Processed {layer_name}"
            zarr_name  = (str(cache_paths['preproc'].name)
                          if cache_paths else f"preproc_{id(layer_name)}")

            def _after_preproc(zarr_path):
                if cache_paths and source_file:
                    write_meta(source_file, ball_radius, window_size,
                               n_t, H, W)

            _start_worker(stack_data, 'preprocess',
                          {'ball_radius': br, 'window_size': ws},
                          zarr_name, proc_name, 'green',
                          on_done_cb=_after_preproc)

        elif bg_check.isChecked():
            _start_bg(stack_data)

        # Record for batch — note: slider interactions are NOT recorded
        ui_instance._record('lazy_preprocess_stack', {
            'stack_layer': layer_name,
            'ball_radius': ball_radius,
            'window_size': window_size,
            'preprocess': preprocess_check.isChecked(),
            'bg_removal': bg_check.isChecked(),
            'pseudo3d_temporal': pseudo3d_temporal_cb.isChecked(),
        })

    build_btn.clicked.connect(_on_build)
    form.addRow("", build_btn)
    form.addRow("", discard_btn)

    grp_widget = QWidget()
    from PyQt5.QtWidgets import QVBoxLayout as _QVB
    _l = _QVB(grp_widget)
    _l.addWidget(grp)
    ui_instance._add_widget_to_layout_or_dock(
        grp_widget, layout, separate_widget, "Lazy Stack Pre-processing"
    )


# ---------------------------------------------------------------------------
# Pure analysis functions
# ---------------------------------------------------------------------------

def _phase_shift(frame: np.ndarray, reference: np.ndarray) -> tuple[int, int]:
    """
    Estimate (row, col) drift of `frame` relative to `reference` using
    phase cross-correlation.  Returns integer pixel shift.
    """
    from skimage.registration import phase_cross_correlation
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        shift, _, _ = phase_cross_correlation(reference, frame, normalization=None)
    return int(round(shift[0])), int(round(shift[1]))


def _apply_shift(image: np.ndarray, dr: int, dc: int) -> np.ndarray:
    """
    Translate `image` by (dr, dc) pixels using numpy roll.
    Rolled-in pixels are zeroed to avoid wrap-around contamination.
    """
    shifted = np.roll(image, (dr, dc), axis=(0, 1))
    if dr > 0:
        shifted[:dr, :] = 0
    elif dr < 0:
        shifted[dr:, :] = 0
    if dc > 0:
        shifted[:, :dc] = 0
    elif dc < 0:
        shifted[:, dc:] = 0
    return shifted


def _condensate_metrics_per_cell(
    refined_puncta_mask: np.ndarray,
    cell_mask: np.ndarray,
    cell_label: int,
    microns_per_pixel_sq: float,
) -> dict:
    """
    Compute condensate area metrics for one cell at one frame.

    Parameters
    ----------
    refined_puncta_mask : np.ndarray bool  — full-frame refined puncta mask
    cell_mask : np.ndarray bool            — binary mask for this cell
    cell_label : int
    microns_per_pixel_sq : float

    Returns
    -------
    dict with keys: cell_label, total_condensate_area_px,
                    total_condensate_area_um2, cell_area_px,
                    condensate_fraction, n_condensates,
                    mean_condensate_area_um2
    """
    puncta_in_cell = refined_puncta_mask & cell_mask
    labeled_puncta = sk.measure.label(puncta_in_cell)
    n_condensates = int(labeled_puncta.max())
    total_area_px = int(puncta_in_cell.sum())
    cell_area_px = int(cell_mask.sum())
    total_area_um2 = total_area_px * microns_per_pixel_sq
    condensate_fraction = total_area_px / cell_area_px if cell_area_px > 0 else 0.0

    if n_condensates > 0:
        props = sk.measure.regionprops(labeled_puncta)
        mean_area_um2 = float(np.mean([p.area for p in props])) * microns_per_pixel_sq
    else:
        mean_area_um2 = 0.0

    return {
        'cell_label': cell_label,
        'total_condensate_area_px': total_area_px,
        'total_condensate_area_um2': round(total_area_um2, 4),
        'cell_area_px': cell_area_px,
        'condensate_fraction': round(condensate_fraction, 6),
        'n_condensates': n_condensates,
        'mean_condensate_area_um2': round(mean_area_um2, 4),
    }


def _ts_analyze_frame_worker(args):
    """
    Top-level picklable worker for parallel time-series condensate analysis.

    Reads one frame's raw and preprocessed data directly from filesystem
    zarr stores (avoiding IPC pickling of large arrays), performs drift
    correction, per-cell condensate segmentation, area/count metrics, and
    optional per-frame spatial metrology — all independent per frame given
    a fixed cell mask, making this an embarrassingly parallel workload.

    Also fixes a redundant double-labeling: the original serial
    implementation called sk.measure.label() once inside the per-cell
    metrics helper and again inside the spatial metrology block on the
    same array. Here it is computed once per cell and reused for both.

    Returns (t, list_of_metric_dicts, condensate_mask_uint8_for_frame).
    """
    import warnings, os
    warnings.filterwarnings("ignore", message="CUDA path could not be detected",
                            category=UserWarning)
    os.environ.setdefault("CUDA_PATH", "")

    (t, raw_zarr_path, proc_zarr_path, labeled_cell_mask, cell_labels,
     reference_raw, use_drift_correction, reference_frame,
     ball_radius, microns_per_pixel_sq,
     kurtosis_threshold, local_snr_threshold, global_snr_threshold,
     intensity_hwhm_scale, max_area_fraction, min_spot_radius,
     compute_spatial, per_frame_normalize) = args

    import numpy as np
    import zarr as _zarr
    import skimage as sk
    from pycat.toolbox.segmentation_tools import segment_subcellular_objects

    raw_src  = _zarr.open(raw_zarr_path, mode='r')
    proc_src = _zarr.open(proc_zarr_path, mode='r')
    frame_raw  = np.asarray(raw_src[t]).astype(np.float32)
    frame_proc = np.asarray(proc_src[t]).astype(np.float32)
    H, W = frame_raw.shape

    dr, dc = 0, 0
    if use_drift_correction and t != reference_frame:
        from skimage.registration import phase_cross_correlation
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            shift, _, _ = phase_cross_correlation(
                reference_raw, frame_raw, normalization=None)
        dr, dc = int(round(shift[0])), int(round(shift[1]))
        if dr != 0 or dc != 0:
            frame_raw  = _apply_shift(frame_raw,  dr, dc)
            frame_proc = _apply_shift(frame_proc, dr, dc)

    total_refined = np.zeros((H, W), dtype=bool)
    records = []
    _mpx = float(microns_per_pixel_sq ** 0.5)

    # Match the 2D fluorescence puncta path: it applies per-cell contrast
    # stretching (cell_mask_stretching) to the PREPROCESSED image before puncta
    # segmentation, and passes that stretched image (CMS_img) into
    # segment_subcellular_objects. The time-series path previously passed the
    # plain preprocessed frame, so puncta detection was weaker than 2D. Compute
    # the same stretched image once per frame (over the whole labeled mask),
    # then hand each cell its slice — identical to the 2D behaviour.
    from pycat.toolbox.segmentation_tools import cell_mask_stretching
    try:
        frame_cms = cell_mask_stretching(frame_proc, labeled_cell_mask)
    except Exception:
        frame_cms = frame_proc   # fall back to plain preprocessed frame

    for cell_label in cell_labels:
        cell_binary_mask = (labeled_cell_mask == cell_label)
        # A cell in the union label set may have zero pixels in this frame's
        # mask; skip it (nothing to segment, and avoids the empty-mask crash).
        if not cell_binary_mask.any():
            continue

        # Per-frame within-cell normalization for dissolution/dynamics experiments:
        # normalising to the CURRENT frame's own intensity range makes all
        # intensity-based conditions scale-invariant, so rising dilute-phase
        # background and falling condensate peaks don't progressively knock out
        # real condensates. Uses robust 1st–99th percentile to avoid clip
        # artefacts from outliers. When disabled (default / steady-state), the
        # raw frame is used directly, preserving the original behaviour.
        if per_frame_normalize:
            _cpx = frame_raw[cell_binary_mask]
            if _cpx.size > 0:
                _plo = float(np.percentile(_cpx, 1))
                _phi = float(np.percentile(_cpx, 99))
                _frame_raw_seg = (np.clip((frame_raw - _plo) / max(_phi - _plo, 1e-8),
                                          0.0, 1.0).astype(np.float32)
                                  if _phi > _plo else frame_raw)
            else:
                _frame_raw_seg = frame_raw
        else:
            _frame_raw_seg = frame_raw

        refined, _ = segment_subcellular_objects(
            _frame_raw_seg, frame_cms, cell_binary_mask, int(cell_label),
            ball_radius, cell_df=None,
            kurtosis_threshold=kurtosis_threshold,
            local_snr_threshold=local_snr_threshold,
            global_snr_threshold=global_snr_threshold,
            intensity_hwhm_scale=intensity_hwhm_scale,
            max_area_fraction=max_area_fraction,
            min_spot_radius=min_spot_radius,
        )
        total_refined |= refined

        # Single connected-components pass, reused for area/count metrics
        # AND spatial metrology (previously computed twice on the same array).
        labeled_puncta = sk.measure.label(refined)
        n_condensates  = int(labeled_puncta.max())
        total_area_px  = int(refined.sum())
        cell_area_px   = int(cell_binary_mask.sum())
        total_area_um2 = total_area_px * microns_per_pixel_sq
        condensate_fraction = (total_area_px / cell_area_px
                               if cell_area_px > 0 else 0.0)

        props = sk.measure.regionprops(labeled_puncta) if n_condensates > 0 else []
        mean_area_um2 = (float(np.mean([p.area for p in props])) * microns_per_pixel_sq
                         if props else 0.0)

        metrics = {
            'cell_label': int(cell_label),
            'total_condensate_area_px': total_area_px,
            'total_condensate_area_um2': round(total_area_um2, 4),
            'cell_area_px': cell_area_px,
            'condensate_fraction': round(condensate_fraction, 6),
            'n_condensates': n_condensates,
            'mean_condensate_area_um2': round(mean_area_um2, 4),
            'frame': t,
            'drift_row_px': dr,
            'drift_col_px': dc,
        }

        if compute_spatial:
            if len(props) >= 2:
                try:
                    _coords_arr = np.array([
                        [p.centroid[0] * _mpx, p.centroid[1] * _mpx]
                        for p in props
                    ])
                    from pycat.toolbox.spatial_metrology_tools import (
                        nearest_neighbour_distance, local_object_density,
                        convex_hull_metrics,
                    )
                    from pycat.toolbox.organizational_metrics_tools import (
                        inter_condensate_spacing,
                    )
                    _cell_area = cell_area_px * microns_per_pixel_sq
                    _nnd = nearest_neighbour_distance(_coords_arr)
                    _kde = local_object_density(_coords_arr)
                    _hull = convex_hull_metrics(_coords_arr, _cell_area)
                    _spc = inter_condensate_spacing(_coords_arr, k_neighbours=1)
                    metrics['nnd_mean_um']      = _nnd['mean_nnd']
                    metrics['nnd_cv']           = _nnd['cv_nnd']
                    metrics['kde_mean_density'] = _kde['mean_density']
                    metrics['hull_occupancy']   = _hull['occupancy_fraction']
                    metrics['hull_compactness'] = _hull['hull_compactness']
                    metrics['spacing_cv']       = _spc.attrs.get(
                        'coefficient_of_variation', np.nan)
                except Exception:
                    for k in ('nnd_mean_um', 'nnd_cv', 'kde_mean_density',
                              'hull_occupancy', 'hull_compactness', 'spacing_cv'):
                        metrics[k] = np.nan
            else:
                for k in ('nnd_mean_um', 'nnd_cv', 'kde_mean_density',
                          'hull_occupancy', 'hull_compactness', 'spacing_cv'):
                    metrics[k] = np.nan

        records.append(metrics)

    # Inverse-shift the condensate mask back to the original (unshifted)
    # coordinate system before returning. Drift correction shifts the analysis
    # frame INTO reference-frame space for segmentation; without this step the
    # mask is in the drift-corrected space and appears spatially offset when
    # napari overlays it on the original (unshifted) image layer.
    if dr != 0 or dc != 0:
        total_refined = np.asarray(
            _apply_shift(total_refined.astype(np.uint8), -dr, -dc)
        ).astype(bool)

    return t, records, total_refined.astype(np.uint8)


def run_timeseries_condensate_analysis(
    stack: np.ndarray,
    preprocessed_stack: np.ndarray,
    labeled_cell_mask: np.ndarray,
    ball_radius: float,
    microns_per_pixel_sq: float,
    reference_frame: int = 0,
    use_drift_correction: bool = True,
    kurtosis_threshold: float = -3.0,
    local_snr_threshold: float = 1.0,
    global_snr_threshold: float = 1.0,
    intensity_hwhm_scale: float = 1.17,
    max_area_fraction: float = 0.25,
    min_spot_radius: float = 2.0,
    progress_callback=None,
    compute_spatial: bool = True,
    use_parallel: bool = True,
    n_workers: Optional[int] = None,
    cancel_check=None,
    per_frame_normalize: bool = False,
) -> tuple[pd.DataFrame, np.ndarray]:
    """
    Core time-series condensate analysis — no viewer dependency.

    Parallelisation
    ----------------
    Per-frame condensate segmentation is embarrassingly parallel: each
    frame's analysis depends only on its own raw/preprocessed data and the
    (fixed) cell mask, never on other frames. This function dispatches
    frames across a ProcessPoolExecutor (default: all available cores,
    capped at 8) when use_parallel=True and there are enough frames to
    amortise pool startup cost (>=10 frames).

    Workers read their frame directly from filesystem zarr stores rather
    than receiving pickled arrays through IPC — the same pattern used by
    the lazy stack preprocessing stage — so large frame arrays are never
    serialised through the multiprocessing queue. If the input stacks are
    not already filesystem-zarr-backed (e.g. plain in-memory numpy arrays),
    they are materialised to a temporary zarr store once before dispatch.

    Falls back to a serial loop when use_parallel=False or n_frames < 10,
    where parallel dispatch overhead would exceed the benefit.

    Parameters
    ----------
    stack : np.ndarray, shape (T, H, W)
        Raw fluorescence image stack.
    preprocessed_stack : np.ndarray, shape (T, H, W) or (H, W)
        Pre-processed image stack.  If 2D, the same preprocessed image
        is used for every frame (useful when only the reference was processed).
    labeled_cell_mask : np.ndarray, shape (H, W) or (T, H, W)
        Integer-labeled cell mask. A (T, H, W) stack applies each frame's own
        mask (tracks moving cells); a 2D (H, W) mask is propagated to all frames.
    ball_radius : float
        Rolling-ball radius for background subtraction (from data_instance).
    microns_per_pixel_sq : float
        Physical pixel area in µm².
    reference_frame : int
        Frame index used as drift correction reference (default 0).
    use_drift_correction : bool
        Whether to apply phase-correlation drift correction.
    kurtosis_threshold, local_snr_threshold, global_snr_threshold,
    intensity_hwhm_scale, max_area_fraction, min_spot_radius :
        Refinement parameters passed through to segment_subcellular_objects.
    progress_callback : callable(frame_idx, total_frames) or None
        Called as each frame completes for progress reporting.  In the
        parallel path this is called as results arrive (out-of-order
        completion is normal), so frame_idx counts completed frames,
        not a specific frame number.
    compute_spatial : bool
        Whether to compute per-frame spatial metrics (NND, KDE, hull, spacing).
    use_parallel : bool
        Use ProcessPoolExecutor for frame-level parallelism (default True).
    n_workers : int or None
        Number of worker processes.  Defaults to min(8, cpu_count()-1).
    cancel_check : callable() -> bool or None
        If provided, checked periodically; when it returns True, remaining
        work is abandoned and an InterruptedError is raised (parallel path
        only — matches the cancellation contract used by the serial caller).

    Returns
    -------
    results_df : pd.DataFrame
        Tidy DataFrame with columns:
            frame, cell_label, total_condensate_area_px,
            total_condensate_area_um2, cell_area_px,
            condensate_fraction, n_condensates, mean_condensate_area_um2,
            drift_row_px, drift_col_px[, spatial columns if compute_spatial]
    condensate_stack : np.ndarray, shape (T, H, W), dtype uint8
        Stack of refined puncta masks for each frame (for napari display).
    """
    n_frames, H, W = stack.shape

    # Handle 2D preprocessed (single reference frame used for all)
    if preprocessed_stack.ndim == 2:
        preprocessed_stack = np.stack([preprocessed_stack] * n_frames, axis=0)

    # Normalise the cell mask to a per-frame (T, H, W) stack.
    #   - A (T, H, W) mask (e.g. keyframe-Cellpose output) is used as-is: each
    #     frame's own mask is applied to that frame, correctly tracking cells
    #     that move over time.
    #   - A 2D (H, W) mask is propagated to every frame (the caller is expected
    #     to have warned the user this assumes a temporally-stationary sample).
    _mask_arr = np.asarray(labeled_cell_mask)
    if _mask_arr.ndim == 2:
        mask_stack = np.broadcast_to(_mask_arr, (n_frames,) + _mask_arr.shape)
    elif _mask_arr.ndim == 3:
        if _mask_arr.shape[0] != n_frames:
            # Frame-count mismatch (e.g. mask stack from a different range):
            # fall back to the reference frame's mask propagated to all frames.
            _ref = _mask_arr[min(reference_frame, _mask_arr.shape[0] - 1)]
            mask_stack = np.broadcast_to(_ref, (n_frames,) + _ref.shape)
        else:
            mask_stack = _mask_arr
    else:
        raise ValueError(
            f"labeled_cell_mask must be 2D (H,W) or 3D (T,H,W); got shape "
            f"{_mask_arr.shape}")

    # Cell labels = union of all labels present across frames, so a cell that
    # only appears in some frames is still analysed where it exists.
    cell_labels = np.unique(mask_stack)
    cell_labels = cell_labels[cell_labels != 0]

    reference_raw = stack[reference_frame]
    if hasattr(reference_raw, 'compute'):
        reference_raw = reference_raw.compute()
    reference_raw = np.asarray(reference_raw).astype(np.float32)

    records = []
    condensate_stack = np.zeros((n_frames, H, W), dtype=np.uint8)

    should_parallelize = use_parallel and n_frames >= 10 and len(cell_labels) > 0

    if should_parallelize:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        import os as _os

        workers = n_workers or min(8, max(1, _os.cpu_count() - 1))

        # Materialise both stacks to filesystem zarr once — reuses existing
        # zarr paths if the stacks already came from lazy preprocessing.
        raw_zarr_path  = _materialize_stack_to_zarr(
            stack, n_frames, H, W, prefix='ts_analysis_raw')
        proc_zarr_path = _materialize_stack_to_zarr(
            preprocessed_stack, n_frames, H, W, prefix='ts_analysis_proc')

        tasks = [
            (t, raw_zarr_path, proc_zarr_path,
             np.asarray(mask_stack[t]), cell_labels,
             reference_raw, use_drift_correction, reference_frame,
             ball_radius, microns_per_pixel_sq,
             kurtosis_threshold, local_snr_threshold, global_snr_threshold,
             intensity_hwhm_scale, max_area_fraction, min_spot_radius,
             compute_spatial, per_frame_normalize)
            for t in range(n_frames)
        ]

        done = 0
        batch_size = workers * 4
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for batch_start in range(0, n_frames, batch_size):
                if cancel_check is not None and cancel_check():
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise InterruptedError("Cancelled by user.")

                batch = tasks[batch_start:batch_start + batch_size]
                futures = {executor.submit(_ts_analyze_frame_worker, task): task[0]
                           for task in batch}
                for future in as_completed(futures):
                    t_idx, frame_records, frame_mask = future.result()
                    records.extend(frame_records)
                    condensate_stack[t_idx] = frame_mask
                    done += 1
                    if progress_callback is not None:
                        progress_callback(done, n_frames)

    else:
        # ── Serial fallback ──────────────────────────────────────────────
        # Used for small stacks (<10 frames) or when explicitly requested.
        # Still benefits from the single-labeling fix applied in the
        # parallel worker by reusing the same per-frame logic inline.
        for t in range(n_frames):
            frame_raw  = stack[t]
            if hasattr(frame_raw, 'compute'):
                frame_raw = frame_raw.compute()
            frame_raw = np.asarray(frame_raw).astype(np.float32)

            frame_proc = preprocessed_stack[t]
            if hasattr(frame_proc, 'compute'):
                frame_proc = frame_proc.compute()
            frame_proc = np.asarray(frame_proc).astype(np.float32)

            dr, dc = 0, 0
            if use_drift_correction and t != reference_frame:
                dr, dc = _phase_shift(frame_raw, reference_raw)
                if dr != 0 or dc != 0:
                    frame_raw  = _apply_shift(frame_raw,  dr, dc)
                    frame_proc = _apply_shift(frame_proc, dr, dc)

            total_refined = np.zeros((H, W), dtype=bool)
            _mpx = float(microns_per_pixel_sq ** 0.5)

            # Per-frame cell mask (tracks moving cells when a (T,H,W) mask was
            # provided; identical every frame for a propagated 2D mask).
            _frame_mask = np.asarray(mask_stack[t])

            # Match the 2D puncta path: contrast-stretch the preprocessed frame
            # per cell before puncta segmentation (see the parallel worker for
            # the full rationale).
            from pycat.toolbox.segmentation_tools import cell_mask_stretching
            try:
                frame_cms = cell_mask_stretching(frame_proc, _frame_mask)
            except Exception:
                frame_cms = frame_proc

            for cell_label in cell_labels:
                cell_binary_mask = (_frame_mask == cell_label)
                # A cell in the union label set may have zero pixels in this
                # frame (e.g. it entered/left, or a (T,H,W) mask differs per
                # frame). Nothing to segment — skip it (also avoids the empty-
                # mask crop crash downstream).
                if not cell_binary_mask.any():
                    continue

                if per_frame_normalize:
                    _cpx = frame_raw[cell_binary_mask]
                    if _cpx.size > 0:
                        _plo = float(np.percentile(_cpx, 1))
                        _phi = float(np.percentile(_cpx, 99))
                        _frame_raw_seg = (np.clip((frame_raw - _plo) / max(_phi - _plo, 1e-8),
                                                  0.0, 1.0).astype(np.float32)
                                          if _phi > _plo else frame_raw)
                    else:
                        _frame_raw_seg = frame_raw
                else:
                    _frame_raw_seg = frame_raw

                refined, _ = segment_subcellular_objects(
                    _frame_raw_seg, frame_cms,
                    cell_binary_mask, int(cell_label),
                    ball_radius, cell_df=None,
                    kurtosis_threshold=kurtosis_threshold,
                    local_snr_threshold=local_snr_threshold,
                    global_snr_threshold=global_snr_threshold,
                    intensity_hwhm_scale=intensity_hwhm_scale,
                    max_area_fraction=max_area_fraction,
                    min_spot_radius=min_spot_radius,
                )
                total_refined |= refined

                # Single labeling pass, reused for metrics + spatial (fixes
                # the previous double sk.measure.label() call per iteration).
                labeled_puncta = sk.measure.label(refined)
                n_condensates  = int(labeled_puncta.max())
                total_area_px  = int(refined.sum())
                cell_area_px   = int(cell_binary_mask.sum())
                total_area_um2 = total_area_px * microns_per_pixel_sq
                condensate_fraction = (total_area_px / cell_area_px
                                       if cell_area_px > 0 else 0.0)

                props = (sk.measure.regionprops(labeled_puncta)
                         if n_condensates > 0 else [])
                mean_area_um2 = (float(np.mean([p.area for p in props]))
                                 * microns_per_pixel_sq if props else 0.0)

                metrics = {
                    'cell_label': int(cell_label),
                    'total_condensate_area_px': total_area_px,
                    'total_condensate_area_um2': round(total_area_um2, 4),
                    'cell_area_px': cell_area_px,
                    'condensate_fraction': round(condensate_fraction, 6),
                    'n_condensates': n_condensates,
                    'mean_condensate_area_um2': round(mean_area_um2, 4),
                    'frame': t,
                    'drift_row_px': dr,
                    'drift_col_px': dc,
                }

                if compute_spatial:
                    if len(props) >= 2:
                        try:
                            _coords_arr = np.array([
                                [p.centroid[0] * _mpx, p.centroid[1] * _mpx]
                                for p in props
                            ])
                            from pycat.toolbox.spatial_metrology_tools import (
                                nearest_neighbour_distance, local_object_density,
                                convex_hull_metrics,
                            )
                            from pycat.toolbox.organizational_metrics_tools import (
                                inter_condensate_spacing,
                            )
                            _cell_area = cell_area_px * microns_per_pixel_sq
                            _nnd  = nearest_neighbour_distance(_coords_arr)
                            _kde  = local_object_density(_coords_arr)
                            _hull = convex_hull_metrics(_coords_arr, _cell_area)
                            _spc  = inter_condensate_spacing(_coords_arr, k_neighbours=1)
                            metrics['nnd_mean_um']      = _nnd['mean_nnd']
                            metrics['nnd_cv']           = _nnd['cv_nnd']
                            metrics['kde_mean_density'] = _kde['mean_density']
                            metrics['hull_occupancy']   = _hull['occupancy_fraction']
                            metrics['hull_compactness'] = _hull['hull_compactness']
                            metrics['spacing_cv']       = _spc.attrs.get(
                                'coefficient_of_variation', np.nan)
                        except Exception:
                            for k in ('nnd_mean_um', 'nnd_cv', 'kde_mean_density',
                                      'hull_occupancy', 'hull_compactness', 'spacing_cv'):
                                metrics[k] = np.nan
                    else:
                        for k in ('nnd_mean_um', 'nnd_cv', 'kde_mean_density',
                                  'hull_occupancy', 'hull_compactness', 'spacing_cv'):
                            metrics[k] = np.nan

                records.append(metrics)

            # Inverse-shift mask back to original coordinates (same fix as
            # the parallel worker path — see comment there for rationale).
            if dr != 0 or dc != 0:
                total_refined = np.asarray(
                    _apply_shift(total_refined.astype(np.uint8), -dr, -dc)
                ).astype(bool)
            condensate_stack[t] = total_refined.astype(np.uint8)

            if progress_callback is not None:
                progress_callback(t + 1, n_frames)

    # Build tidy DataFrame with frame as first column
    results_df = pd.DataFrame(records)
    # Build column list dynamically so it works whether or not spatial
    # metrics were computed (they're absent when compute_spatial=False or
    # when a frame had fewer than 2 condensates).
    core_cols = ['frame', 'cell_label',
                 'total_condensate_area_px', 'total_condensate_area_um2',
                 'cell_area_px', 'condensate_fraction',
                 'n_condensates', 'mean_condensate_area_um2',
                 'drift_row_px', 'drift_col_px']
    spatial_cols = ['nnd_mean_um', 'nnd_cv', 'kde_mean_density',
                    'hull_occupancy', 'hull_compactness', 'spacing_cv']
    col_order = core_cols + [c for c in spatial_cols
                              if c in results_df.columns]
    results_df = results_df[col_order].sort_values(
        ['frame', 'cell_label']).reset_index(drop=True)

    return results_df, condensate_stack


# ---------------------------------------------------------------------------
# Background worker thread
# ---------------------------------------------------------------------------

class TimeSeriesWorker(QThread):
    progress  = pyqtSignal(int, int)   # frame, total
    finished  = pyqtSignal(object, object)  # results_df, condensate_stack
    error     = pyqtSignal(str)

    def __init__(self, kwargs: dict, parent=None):
        super().__init__(parent)
        self._kwargs = kwargs
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            def _cb(frame, total):
                if self._cancelled:
                    raise InterruptedError("Cancelled by user.")
                self.progress.emit(frame, total)

            results_df, cstack = run_timeseries_condensate_analysis(
                progress_callback=_cb,
                cancel_check=lambda: self._cancelled,
                **self._kwargs
            )
            self.finished.emit(results_df, cstack)
        except InterruptedError:
            self.finished.emit(pd.DataFrame(), np.array([]))
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


# ---------------------------------------------------------------------------
# UI widget
# ---------------------------------------------------------------------------

def _add_run_timeseries_condensate_analysis(
    ui_instance, layout=None, separate_widget=False
):
    """
    Build the Time-Series Condensate Analysis widget and add it to the
    Condensate Analysis pipeline dock.

    Call from CondensateAnalysisUI.setup_ui() as:
        self.central_manager.toolbox_functions_ui
            ._add_run_timeseries_condensate_analysis(
                layout=self.condensate_layout)
    """
    ts_layout = QVBoxLayout()
    ui_instance.add_text_label(ts_layout, 'Time-Series Condensate Analysis', bold=True)

    # ── Inputs ───────────────────────────────────────────────────────────
    ui_instance.add_text_label(ts_layout, 'Raw image stack (T, H, W):', font_size=9)
    stack_dropdown = ui_instance.create_layer_dropdown(napari.layers.Image)
    ts_layout.addWidget(stack_dropdown)

    ui_instance.add_text_label(ts_layout, 'Pre-processed image (2D or stack):', font_size=9)
    proc_dropdown = ui_instance.create_layer_dropdown(napari.layers.Image)
    ts_layout.addWidget(proc_dropdown)

    ui_instance.add_text_label(ts_layout, 'Labeled Cell Mask (from Cell Analyzer):', font_size=9)
    mask_dropdown = ui_instance.create_layer_dropdown(napari.layers.Labels)
    ts_layout.addWidget(mask_dropdown)

    # ── Options ───────────────────────────────────────────────────────────
    opts_group = QGroupBox("Options")
    opts_layout = QFormLayout(opts_group)
    opts_layout.setContentsMargins(9, 20, 9, 6)

    ref_spin = QSpinBox()
    ref_spin.setRange(0, 9999)
    # Pre-populate from Step 2 reference frame selector if available
    _preselected_ref = ui_instance.central_manager.active_data_class.data_repository.get(
        'timeseries_reference_frame', 0)
    ref_spin.setValue(int(_preselected_ref))
    ref_spin.setToolTip(
        "Frame index (0-based) used as drift correction reference. "
        "Auto-populated from the reference frame selected in Step 2.")
    opts_layout.addRow("Reference frame:", ref_spin)

    drift_checkbox = QCheckBox("Apply drift correction (phase cross-correlation)")
    drift_checkbox.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    drift_checkbox.setChecked(True)
    opts_layout.addRow("", drift_checkbox)

    spatial_checkbox = QCheckBox("Compute per-frame spatial metrics")
    spatial_checkbox.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    spatial_checkbox.setChecked(True)
    spatial_checkbox.setToolTip(
        "For each cell at each frame, compute:\n"
        "  • Mean nearest-neighbour distance (NND)\n"
        "  • NND coefficient of variation (clustering index)\n"
        "  • Mean local KDE density\n"
        "  • Convex hull occupancy fraction\n"
        "  • Convex hull compactness\n"
        "  • Inter-condensate spacing CV\n\n"
        "These appear as additional columns in the results DataFrame and\n"
        "can be plotted as time series to track spatial reorganization.\n"
        "Adds ~10–20% to analysis time; disable for quick previews."
    )
    opts_layout.addRow("", spatial_checkbox)

    # Extended spatial options — shown when spatial_checkbox is checked
    ripley_checkbox = QCheckBox("Also compute Ripley's L and PCF  [slower]")
    ripley_checkbox.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    ripley_checkbox.setChecked(False)
    ripley_checkbox.setEnabled(True)
    ripley_checkbox.setToolTip(
        "Compute Ripley's L(r) and Pair Correlation Function g(r) for each\n"
        "cell at each frame.  Results are stored in data_repository as\n"
        "timeseries_ripleys_l and timeseries_pcf DataFrames.\n\n"
        "These are multi-scale analyses (~30 r-values per cell per frame)\n"
        "and are substantially slower — recommended only for short stacks\n"
        "or when spatial scale information is specifically needed."
    )
    opts_layout.addRow("", ripley_checkbox)
    spatial_checkbox.stateChanged.connect(
        lambda s: ripley_checkbox.setEnabled(bool(s)))

    ts_layout.addWidget(opts_group)

    # ── Refinement parameters ────────────────────────────────────────────
    ref_group = QGroupBox("Refinement Parameters")
    ref_layout = QFormLayout(ref_group)
    ref_layout.setContentsMargins(9, 20, 9, 6)

    def _dspin(lo, hi, val, step, tip=""):
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi); sb.setValue(val)
        sb.setSingleStep(step); sb.setDecimals(2)
        if tip: sb.setToolTip(tip)
        return sb

    min_spot_spin  = _dspin(1, 20, 2, 0.5,
        "Minimum condensate radius in pixels.")
    kurtosis_spin  = _dspin(-10, 0, -3.0, 0.5,
        "Kurtosis threshold for each candidate condensate.\n"
        "More NEGATIVE = more permissive (keeps flatter-peaked spots).\n"
        "Decrease for dissolution experiments where condensate peaks\n"
        "become less sharp as they dissolve into the dilute phase.")
    lsnr_spin      = _dspin(0, 5, 1.0, 0.1,
        "Local SNR (peak / local background noise). LOWER = keep dimmer condensates.\n"
        "Decrease for dissolution experiments.")
    gsnr_spin      = _dspin(0, 5, 1.0, 0.1,
        "Global SNR (peak / cell-wide background noise). LOWER = more permissive.\n"
        "Decrease for dissolution experiments.")
    hwhm_spin      = _dspin(0, 5, 1.17, 0.1,
        "Intensity threshold: condensate mean must exceed local background mean\n"
        "by this many standard deviations. LOWER = accept dimmer condensates.\n"
        "Decrease for dissolution experiments.")
    maxarea_spin   = _dspin(0.01, 1.0, 0.25, 0.05,
        "Max condensate area as a fraction of cell area. Rejects objects that are\n"
        "implausibly large (likely segmentation artifacts or merged nuclei).")

    # Presets row — quick switching between experiment types
    from PyQt5.QtWidgets import QHBoxLayout as _QHL2, QPushButton as _QPB2
    preset_row = _QHL2()
    preset_lbl = QLabel("Preset:")
    preset_lbl.setWordWrap(True)
    preset_lbl.setStyleSheet("font-size:9pt; color:#aaa;")
    preset_row.addWidget(preset_lbl)

    def _apply_steady_state():
        kurtosis_spin.setValue(-3.0); lsnr_spin.setValue(1.0)
        gsnr_spin.setValue(1.0); hwhm_spin.setValue(1.17)
        per_frame_norm_cb.setChecked(False)

    def _apply_dissolution():
        kurtosis_spin.setValue(-5.0); lsnr_spin.setValue(0.5)
        gsnr_spin.setValue(0.5); hwhm_spin.setValue(0.7)
        per_frame_norm_cb.setChecked(True)

    ss_btn = _QPB2("Steady-state")
    ss_btn.setToolTip("Default thresholds for stable condensates")
    ss_btn.clicked.connect(_apply_steady_state)
    dis_btn = _QPB2("Dissolution / dynamics")
    dis_btn.setToolTip(
        "Relaxed thresholds for experiments where condensate signal\n"
        "decays over time (e.g. optodroplets dissolving after light-off,\n"
        "or condensates weakening under stress). Enables per-frame\n"
        "intensity normalisation and lowers SNR / kurtosis thresholds.")
    dis_btn.clicked.connect(_apply_dissolution)
    preset_row.addWidget(ss_btn)
    preset_row.addWidget(dis_btn)
    preset_row.addStretch()
    ref_layout.addRow(preset_row)

    per_frame_norm_cb = QCheckBox("Per-frame intensity normalisation")
    per_frame_norm_cb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    per_frame_norm_cb.setChecked(False)
    per_frame_norm_cb.setToolTip(
        "Normalise each frame's pixel intensities to [0, 1] within the\n"
        "cell mask before running refinement filtering. This makes the\n"
        "local and global intensity conditions scale-invariant — the\n"
        "thresholds compare relative contrast rather than absolute counts.\n\n"
        "Essential for dissolution / dynamics experiments where the dilute\n"
        "phase intensity rises over time: without this, rising background\n"
        "progressively triggers intensity conditions and removes real\n"
        "condensates from later frames.\n\n"
        "For steady-state experiments this can be left off.")
    ref_layout.addRow("", per_frame_norm_cb)

    ref_layout.addRow("Min spot radius (px):", min_spot_spin)
    ref_layout.addRow("Kurtosis threshold:", kurtosis_spin)
    ref_layout.addRow("Local SNR threshold:", lsnr_spin)
    ref_layout.addRow("Global SNR threshold:", gsnr_spin)
    ref_layout.addRow("Intensity scale (×SD):", hwhm_spin)
    ref_layout.addRow("Max area (fraction of cell):", maxarea_spin)

    ts_layout.addWidget(ref_group)

    # ── Progress & run ────────────────────────────────────────────────────
    progress_bar = QProgressBar()
    progress_bar.setVisible(False)
    ts_layout.addWidget(progress_bar)

    run_btn    = QPushButton("▶  Run Time-Series Analysis")
    run_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    cancel_btn = QPushButton("Cancel")
    cancel_btn.setVisible(False)

    btn_row = QHBoxLayout()
    btn_row.addWidget(run_btn)
    btn_row.addWidget(cancel_btn)
    ts_layout.addLayout(btn_row)

    _worker_ref  = [None]   # mutable container so closure can update it
    _run_ripley_ref = [False]  # mutable: set in _on_run, read in _on_finished
    # Same pattern, and for the same reason: `mask_name` is a LOCAL of _on_run, and
    # _on_finished is a SIBLING nested function, not an inner one -- siblings do not
    # share locals. Reading it directly from _on_finished raised NameError, which was
    # then swallowed by the `except Exception` around the Ripley block, so ticking
    # 'Ripley's L / PCF' silently produced NO Ripley or PCF results at all: no crash,
    # no warning, just missing output.
    _mask_name_ref = [None]    # mutable: set in _on_run, read in _on_finished

    def _on_run():
        # Validate inputs
        stack_name = stack_dropdown.currentText()
        proc_name  = proc_dropdown.currentText()
        mask_name  = mask_dropdown.currentText()
        _mask_name_ref[0] = mask_name   # hand it to _on_finished (see above)

        try:
            stack_layer = ui_instance.viewer.layers[stack_name]
            proc_layer  = ui_instance.viewer.layers[proc_name]
            mask_layer  = ui_instance.viewer.layers[mask_name]
        except KeyError as e:
            napari_show_warning(f"Time-Series: layer not found — {e}")
            return

        stack_data = stack_layer.data
        if stack_data.ndim == 2:
            napari_show_warning("Time-Series: selected image is 2D — need a (T,H,W) stack.")
            return
        if stack_data.ndim == 3:
            pass  # (T, H, W) — correct
        else:
            napari_show_warning(f"Time-Series: unexpected image shape {stack_data.shape}.")
            return

        proc_data   = proc_layer.data
        mask_data   = mask_layer.data

        # The cell mask may be either a 2D (H,W) mask or a (T,H,W) mask stack.
        # A (T,H,W) mask (e.g. from keyframe Cellpose) is preferred — it applies
        # each frame's own mask, correctly following cells that move over time.
        # A 2D mask is accepted but assumes the sample is stationary in time.
        if mask_data.ndim == 2:
            napari_show_warning(
                "Time-Series: 2D cell mask — assuming your sample is stationary "
                "in time (the same mask is applied to every frame). For moving "
                "cells, run keyframe cell segmentation to get a (T,H,W) mask.")
        elif mask_data.ndim == 3:
            if mask_data.shape[0] != stack_data.shape[0]:
                napari_show_warning(
                    f"Time-Series: mask stack has {mask_data.shape[0]} frames but "
                    f"the image has {stack_data.shape[0]}; the reference frame's "
                    "mask will be used for all frames.")
            # else: matching (T,H,W) — used per-frame downstream.
        else:
            napari_show_warning(
                f"Time-Series: cell mask must be 2D (H,W) or a (T,H,W) stack; "
                f"got shape {mask_data.shape}.")
            return

        data_instance = ui_instance.central_manager.active_data_class
        ball_radius   = float(data_instance.data_repository.get('ball_radius', 50))
        mpx_sq        = float(data_instance.data_repository.get('microns_per_pixel_sq', 1.0))

        # IMPORTANT: do NOT call .astype()/np.asarray() on stack_data or
        # proc_data here. If these are lazy zarr-backed _ZarrStack layers
        # (the normal case after lazy stack preprocessing), eagerly
        # converting dtype triggers __array__ and materialises the entire
        # (T, H, W) stack into RAM immediately, defeating both the lazy
        # loading AND the parallel analysis path's ability to detect and
        # reuse the existing on-disk zarr store (it would have to write
        # a redundant copy back to disk before parallel dispatch).
        # Per-frame dtype conversion happens lazily inside the analysis
        # function instead (both the parallel worker and serial fallback
        # already read each frame and cast it to float32 individually).
        kwargs = dict(
            stack=stack_data,
            preprocessed_stack=proc_data,
            labeled_cell_mask=mask_data,
            ball_radius=ball_radius,
            microns_per_pixel_sq=mpx_sq,
            reference_frame=int(ref_spin.value()),
            use_drift_correction=drift_checkbox.isChecked(),
            kurtosis_threshold=kurtosis_spin.value(),
            local_snr_threshold=lsnr_spin.value(),
            global_snr_threshold=gsnr_spin.value(),
            intensity_hwhm_scale=hwhm_spin.value(),
            max_area_fraction=maxarea_spin.value(),
            min_spot_radius=min_spot_spin.value(),
            compute_spatial=spatial_checkbox.isChecked(),
            per_frame_normalize=per_frame_norm_cb.isChecked(),
        )
        _run_ripley = ripley_checkbox.isChecked() and spatial_checkbox.isChecked()
        _run_ripley_ref[0] = _run_ripley

        n_frames = stack_data.shape[0]
        progress_bar.setMaximum(n_frames)
        progress_bar.setValue(0)
        progress_bar.setVisible(True)
        run_btn.setEnabled(False)
        cancel_btn.setVisible(True)

        worker = TimeSeriesWorker(kwargs)
        _worker_ref[0] = worker

        worker.progress.connect(lambda f, t: progress_bar.setValue(f))
        worker.finished.connect(lambda df, cs: _on_finished(df, cs, stack_name))
        worker.error.connect(_on_error)
        worker.start()

        # Record for batch
        ui_instance._record('timeseries_condensate_analysis', {
            'stack_layer': stack_name,
            'proc_layer': proc_name,
            'mask_layer': mask_name,
            'reference_frame': int(ref_spin.value()),
            'use_drift_correction': drift_checkbox.isChecked(),
            'kurtosis_threshold': kurtosis_spin.value(),
            'local_snr_threshold': lsnr_spin.value(),
            'global_snr_threshold': gsnr_spin.value(),
            'intensity_hwhm_scale': hwhm_spin.value(),
            'per_frame_normalize': per_frame_norm_cb.isChecked(),
            'max_area_fraction': maxarea_spin.value(),
            'min_spot_radius': min_spot_spin.value(),
            'compute_spatial': spatial_checkbox.isChecked(),
        })

    def _on_finished(results_df, condensate_stack, stack_name):
        progress_bar.setVisible(False)
        run_btn.setEnabled(True)
        cancel_btn.setVisible(False)

        if results_df.empty:
            napari_show_info("Time-Series analysis cancelled.")
            return

        # Store results
        data_instance = ui_instance.central_manager.active_data_class
        data_instance.data_repository['timeseries_condensate_df'] = results_df

        # Add condensate stack to viewer
        if condensate_stack.size > 0:
            ui_instance.viewer.add_labels(
                condensate_stack.astype(int),
                name=f"TimeSeries Condensate Masks"
            )

        # Build summary: condensate fraction vs frame per cell
        agg_dict = dict(
            n_cells=('cell_label', 'count'),
            mean_condensate_fraction=('condensate_fraction', 'mean'),
            std_condensate_fraction=('condensate_fraction', 'std'),
            mean_total_area_um2=('total_condensate_area_um2', 'mean'),
            total_n_condensates=('n_condensates', 'sum'),
        )
        # Add spatial summaries to frame-level aggregation if present
        spatial_cols_present = [c for c in
            ['nnd_mean_um','nnd_cv','kde_mean_density',
             'hull_occupancy','hull_compactness','spacing_cv']
            if c in results_df.columns]
        for sc in spatial_cols_present:
            agg_dict[f'mean_{sc}'] = (sc, 'mean')

        summary = results_df.groupby('frame').agg(
            **agg_dict).round(6).reset_index()

        tables = [
            ("Per-Cell Per-Frame Results", results_df.round(4)),
            ("Summary (per frame)", summary),
        ]

        # Optional post-run Ripley's L and PCF (per cell across all frames)
        _run_ripley = _run_ripley_ref[0]
        if _run_ripley:
            try:
                from pycat.toolbox.spatial_metrology_tools import (
                    ripleys_l, pair_correlation_function, get_puncta_centroids)
                import skimage as _sk
                mpx = float(data_instance.data_repository.get(
                    'microns_per_pixel_sq', 1.0) ** 0.5)
                _mask_name = _mask_name_ref[0]
                if not _mask_name:
                    raise RuntimeError(
                        'Ripley/PCF: the cell-mask layer name was not carried '
                        'over from the run.')
                cell_mask_layer = ui_instance.viewer.layers[_mask_name]
                cmask = cell_mask_layer.data

                ripley_rows, pcf_rows = [], []
                n_frames_rip = condensate_stack.shape[0]
                for t in range(n_frames_rip):
                    frame_mask = condensate_stack[t]
                    coords_df  = get_puncta_centroids(frame_mask, cmask, mpx)
                    for cl in coords_df['cell_label'].unique():
                        if cl == 0: continue
                        sub    = coords_df[coords_df['cell_label'] == cl]
                        coords = sub[['y_um','x_um']].values
                        cm     = (cmask == cl).astype(bool)
                        area   = float(cm.sum()) * (mpx**2)
                        if len(coords) >= 3:
                            rl = ripleys_l(coords, area)
                            rl['frame'] = t; rl['cell_label'] = cl
                            ripley_rows.append(rl)
                            pc = pair_correlation_function(coords, area)
                            pc['frame'] = t; pc['cell_label'] = cl
                            pcf_rows.append(pc)

                if ripley_rows:
                    import pandas as _pd
                    rdf = _pd.concat(ripley_rows, ignore_index=True)
                    pdf = _pd.concat(pcf_rows, ignore_index=True)
                    data_instance.data_repository['timeseries_ripleys_l'] = rdf
                    data_instance.data_repository['timeseries_pcf']        = pdf
                    tables += [
                        ("Ripley's L(r) — all frames", rdf.round(4)),
                        ("Pair Correlation g(r) — all frames", pdf.round(4)),
                    ]
            except Exception as _re:
                print(f"[PyCAT TS] Ripley/PCF computation failed: {_re}")

        show_dataframes_dialog("Time-Series Condensate Analysis", tables)

        napari_show_info(
            f"Time-Series analysis complete: {results_df['frame'].nunique()} frames, "
            f"{results_df['cell_label'].nunique()} cells."
            + (f" Spatial metrics: {', '.join(spatial_cols_present)}."
               if spatial_cols_present else "")
            + " Use Advanced Analysis → Dynamic tab to track condensates "
              "with Bayesian or greedy linking."
        )

        _plot_condensate_fraction(results_df)

    def _on_error(msg):
        progress_bar.setVisible(False)
        run_btn.setEnabled(True)
        cancel_btn.setVisible(False)
        napari_show_warning(f"Time-Series analysis error — see terminal for details.")
        print(f"[PyCAT TimeSeries] ERROR:\n{msg}")

    def _on_cancel():
        w = _worker_ref[0]
        if w and w.isRunning():
            w.cancel()
            cancel_btn.setVisible(False)
            napari_show_info("Cancellation requested — finishing current frame …")

    run_btn.clicked.connect(_on_run)
    cancel_btn.clicked.connect(_on_cancel)

    ts_widget = QWidget()
    ts_widget.setLayout(ts_layout)
    ui_instance._add_widget_to_layout_or_dock(
        ts_widget, layout, separate_widget, "Time-Series Condensate Analysis"
    )


# ---------------------------------------------------------------------------
# Plot helper
# ---------------------------------------------------------------------------

def _plot_condensate_fraction(results_df: pd.DataFrame):
    """
    Plot mean condensate fraction vs frame for each cell, with a mean ± SD
    across all cells overlay.
    """
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 4))
    frames = sorted(results_df['frame'].unique())

    for cell_label, cell_data in results_df.groupby('cell_label'):
        ax.plot(
            cell_data['frame'], cell_data['condensate_fraction'],
            alpha=0.5, linewidth=1, label=f"Cell {cell_label}"
        )

    # Mean ± SD across cells
    grouped = results_df.groupby('frame')['condensate_fraction']
    means = grouped.mean().reindex(frames).values
    stds  = grouped.std().reindex(frames).fillna(0).values

    ax.plot(frames, means, 'k-', linewidth=2, label='Mean')
    ax.fill_between(frames,
                    np.nan_to_num(means - stds),
                    np.nan_to_num(means + stds),
                    alpha=0.15, color='black')

    ax.set_xlabel('Frame', fontsize=12)
    ax.set_ylabel('Condensate fraction (area/cell area)', fontsize=12)
    ax.set_title('Condensate area fraction vs time', fontsize=13)
    ax.legend(fontsize=8, loc='upper right')
    ax.minorticks_on()
    plt.tight_layout()
    # Non-blocking show: napari's Qt event loop is already running, so a blocking
    # plt.show() triggers "QCoreApplication::exec: The event loop is already
    # running". block=False lets the figure display without starting a second loop.
    plt.show(block=False)
