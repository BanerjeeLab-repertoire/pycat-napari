"""Time-series condensate analysis - the scientific entry point, split out of timeseries_condensate_tools
(1.6.245).

run_timeseries_condensate_analysis drives per-frame condensate segmentation across a (T,H,W) stack with a
fixed cell mask and optional phase-correlation drift correction, aggregating per-cell metrics into a tidy
DataFrame + a condensate-mask stack. _ts_analyze_frame_worker is the embarrassingly-parallel per-frame
unit (dispatched serially or across a ProcessPoolExecutor); _condensate_metrics_per_cell / _phase_shift /
_apply_shift are its helpers. Moved VERBATIM - no numerics or threading semantics changed; pinned by
test_timeseries_analysis_characterization (exact DataFrame + mask on a fixed synthetic scene). Reads via
frame_access._materialize_stack_to_zarr and segments via segment_subcellular_objects. _init_worker_threads
(the pool thread-pinning initializer, shared with the preprocessing worker) moves here and is re-exported.
"""
from __future__ import annotations

import warnings
from typing import Optional
import numpy as np
import pandas as pd
import skimage as sk
from pycat.toolbox.segmentation_tools import segment_subcellular_objects
from pycat.toolbox.timeseries.frame_access import _materialize_stack_to_zarr


def _init_worker_threads():
    """ProcessPoolExecutor initializer: pin each worker to a SINGLE compute thread.

    A worker process inherits the parent's environment, and ``run_pycat`` sets
    ``OMP_NUM_THREADS=4`` for the main process. So 8 workers x 4 OMP threads = **32
    threads on an 8-core machine** -- a 4x oversubscription. Oversubscribed threads do not
    go faster; they thrash the cache and burn time context-switching. NumPy/BLAS, SciPy,
    OpenCV and scikit-image all spawn their own pools, and each worker is already a full
    process using one core, so the nested pools are pure overhead.

    Measured (in a 1-CPU sandbox, so the effect on a real 8-core box is LARGER, not
    smaller): pinning workers to one thread each was **2.1x faster** than letting them
    inherit OMP_NUM_THREADS=4.

    Deliberately applied ONLY in workers, via ``ProcessPoolExecutor(initializer=...)``.
    The main process keeps its threading -- interactive single-image operations there
    genuinely benefit from BLAS parallelism.
    """
    import os
    for _v in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
               'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        os.environ[_v] = '1'
    try:
        import cv2
        cv2.setNumThreads(0)          # 0 = disable OpenCV's internal pool
    except Exception:
        pass
    try:
        import torch
        torch.set_num_threads(1)
    except Exception:
        pass


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
        # Bounded SLIDING WINDOW rather than batch-and-drain. Draining a whole batch
        # before submitting the next adds a barrier: every worker waits on the batch's
        # slowest frame. Frame cost is far from uniform (a dense field costs many times
        # an empty one), so this idles workers in practice -- measured 1.7x slower than a
        # sliding window on a realistic mix, while holding twice as many tasks in flight.
        # The window also makes cancellation prompt: it is checked between completions,
        # not between batches, so Cancel stops within about one frame.
        max_pending = max(2, workers * 2)
        with ProcessPoolExecutor(max_workers=workers,
                                 initializer=_init_worker_threads) as executor:
            pending = {}
            nxt = 0
            while len(pending) < max_pending and nxt < len(tasks):
                fut = executor.submit(_ts_analyze_frame_worker, tasks[nxt])
                pending[fut] = tasks[nxt][0]
                nxt += 1
            while pending:
                if cancel_check is not None and cancel_check():
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise InterruptedError("Cancelled by user.")
                for future in as_completed(list(pending)):
                    del pending[future]
                    t_idx, frame_records, frame_mask = future.result()
                    records.extend(frame_records)
                    condensate_stack[t_idx] = frame_mask
                    done += 1
                    if progress_callback is not None:
                        progress_callback(done, n_frames)
                    cancelled = cancel_check is not None and cancel_check()
                    if not cancelled and nxt < len(tasks):
                        fut = executor.submit(_ts_analyze_frame_worker, tasks[nxt])
                        pending[fut] = tasks[nxt][0]
                        nxt += 1
                    break          # re-enter as_completed with the updated set

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
