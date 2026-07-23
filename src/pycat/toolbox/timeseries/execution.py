"""Time-series worker plumbing - the QThread/ProcessPool workers, split out of timeseries_condensate_tools
(1.6.246).

The background worker LIFECYCLE, relocated BEHAVIOUR-PRESERVING (no threading semantics changed): the
parallel per-frame read/process helpers (_worker_read_frame, _process_frame_worker) run in ProcessPool
subprocesses; _make__stackprocessworker and _make_timeseriesworker are the lazy QThread-worker factories
(built on first use so Qt is not needed to IMPORT this module - PyQt5/napari stay function-scoped). The two
factory result caches move with their factories. Moved VERBATIM. Reads frames via frame_access and runs the
analysis via analysis.run_timeseries_condensate_analysis; the tools module re-exports the two factories,
which the staying UI builders call.
"""
from __future__ import annotations

import warnings
import numpy as np
import pandas as pd
from pycat.toolbox.timeseries.frame_access import _read_source_frame, _compute_stack_global_range
from pycat.toolbox.timeseries.analysis import _init_worker_threads, run_timeseries_condensate_analysis


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


def _make__stackprocessworker():
    """Build the QThread worker ON FIRST USE, so Qt is not needed to import this module.

    `class _StackProcessWorker(QThread)` resolves `QThread` at CLASS-DEFINITION time, which runs at
    import — so the Qt import cannot simply move into a method; the base class needs it.
    Cached after the first call.
    """
    global __STACKPROCESSWORKER_CLS
    if __STACKPROCESSWORKER_CLS is not None:
        return __STACKPROCESSWORKER_CLS

    from PyQt5.QtCore import QThread, pyqtSignal

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
            # Cancellation: the dispatch loop checks this between completions, so Cancel
            # stops within one frame instead of waiting for the whole pass to finish.
            self._cancelled = False

        def cancel(self):
            """Request cancellation. Frames already in flight finish; no new ones start."""
            self._cancelled = True

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
            else:
                # Same capability question as above — see `zarr_compat.store_path`. `DirectoryStore`
                # is `LocalStore` in zarr 3, and its path lives on `.root`, not `.path`.
                from pycat.file_io.zarr_compat import store_path
                src_zarr_path = store_path(src)
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
            else:
                # Same capability question — see `zarr_compat.store_path`. `DirectoryStore` is
                # `LocalStore` in zarr 3, and its path lives on `.root`, not `.path`.
                from pycat.file_io.zarr_compat import store_path
                src_zarr_path = store_path(src)

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
                    # Streaming reduction: read one frame at a time and keep a running
                    # max. The previous line was:
                    #
                    #     float(np.asarray(_src_for_max[:]).max())
                    #
                    # `store[:]` on a zarr array pulls the ENTIRE stack into RAM -- for a
                    # 1.5 GB movie that is 1.5 GB allocated to obtain ONE SCALAR, which
                    # defeats the whole point of the zarr backing. Worse, when that
                    # allocation failed the `except` silently substituted norm_max = 1.0,
                    # i.e. a WRONG normalisation rather than a slow one -- so the failure
                    # mode of running out of memory was a quietly mis-scaled movie.
                    _global_norm_max = 1.0
                    try:
                        _src_for_max = _zarr.open(src_desc['path'], mode='r')
                        _gm = -np.inf
                        _nt = int(_src_for_max.shape[0])
                        for _t in range(_nt):
                            _fr = np.asarray(_src_for_max[_t])
                            if _fr.size:
                                _fm = float(np.nanmax(_fr))
                                if _fm > _gm:
                                    _gm = _fm
                        if np.isfinite(_gm):
                            _global_norm_max = float(_gm)
                    except Exception as _e:
                        # Do not swallow this silently: a fallback of 1.0 is a DIFFERENT
                        # normalisation, not a missing one.
                        print(f'[PyCAT TS] global-max scan failed ({_e}); falling back to '
                              f'norm_max=1.0 -- normalisation may be wrong.')
                        _global_norm_max = 1.0
                    _kwargs_with_norm['norm_max'] = _global_norm_max if _global_norm_max > 0 else 1.0

                args = [
                    (t, src_desc, self._process_fn_name, _kwargs_with_norm)
                    for t in range(self._n_t)
                ]

                done = 0
                offset = self._n_t   # progress offset after materialisation phase
                total_progress = self._n_t * (3 if self._pseudo3d_temporal else 2)
                def _dispatch(dispatch_args):
                    """Run the parallel per-frame pass with a BOUNDED SLIDING WINDOW.

                    The previous version submitted a batch of `n_workers * 4` frames, drained
                    it completely, then submitted the next. That bounds memory (good) but adds
                    a barrier: the whole batch waits on its SLOWEST frame while every other
                    worker sits idle. Frame cost is not uniform -- a dense field or a big cell
                    takes far longer than an empty one -- so the barrier bites in practice.
                    Measured on a realistic mix (12% of frames 15x slower): batch-and-drain
                    81 ms vs sliding window 48 ms, a 1.7x speedup, with HALF the tasks in
                    flight.

                    A sliding window keeps `max_pending` tasks outstanding and submits a new
                    one the moment any completes, so no worker ever idles waiting for a
                    straggler -- and memory stays bounded by `max_pending`, not by the frame
                    count.

                    It also makes cancellation responsive: the loop checks `_cancelled`
                    between completions, so Cancel stops within roughly one frame instead of
                    waiting for hundreds of queued tasks to drain.
                    """
                    nonlocal done
                    max_pending = max(2, self._n_workers * 2)
                    with ProcessPoolExecutor(max_workers=self._n_workers,
                                             initializer=_init_worker_threads) as executor:
                        pending = {}
                        nxt = 0
                        # prime the window
                        while len(pending) < max_pending and nxt < len(dispatch_args):
                            fut = executor.submit(_process_frame_worker, dispatch_args[nxt])
                            pending[fut] = dispatch_args[nxt][0]
                            nxt += 1
                        while pending:
                            for future in as_completed(list(pending)):
                                del pending[future]
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
                                # Refill immediately so no worker idles -- unless cancelled,
                                # in which case we simply stop feeding the pool.
                                if not self._cancelled and nxt < len(dispatch_args):
                                    fut = executor.submit(_process_frame_worker,
                                                          dispatch_args[nxt])
                                    pending[fut] = dispatch_args[nxt][0]
                                    nxt += 1
                                break          # re-enter as_completed with the updated set
                            if self._cancelled and not pending:
                                print('[PyCAT TS] cancelled by user.')
                                return

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

    __STACKPROCESSWORKER_CLS = _StackProcessWorker
    return _StackProcessWorker


__STACKPROCESSWORKER_CLS = None


def _make_timeseriesworker():
    """Build the QThread worker ON FIRST USE, so Qt is not needed to import this module.

    `class TimeSeriesWorker(QThread)` resolves `QThread` at CLASS-DEFINITION time, which runs at
    import — so the Qt import cannot simply move into a method; the base class needs it.
    Cached after the first call.
    """
    global _TIMESERIESWORKER_CLS
    if _TIMESERIESWORKER_CLS is not None:
        return _TIMESERIESWORKER_CLS

    from PyQt5.QtCore import QThread, pyqtSignal

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

    _TIMESERIESWORKER_CLS = TimeSeriesWorker
    return TimeSeriesWorker


_TIMESERIESWORKER_CLS = None
