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
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QCheckBox, QSpinBox, QDoubleSpinBox, QGroupBox, QFormLayout,
    QProgressBar,
)
from PyQt5.QtCore import QThread, pyqtSignal

from pycat.toolbox.segmentation_tools import segment_subcellular_objects
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


def _read_source_frame(stack_data, t):
    """
    Read frame t from any stack type (numpy, zarr, _ZarrTYX, dask) and
    normalize to [0, 1] float32.

    skimage's equalize_adapthist (used in pre_process_image) requires float
    images in [-1, 1].  Raw IMS/TIFF stacks are uint16 (0–65535) or uint8
    (0–255).  Normalizing here ensures every consumer — preprocessing,
    background removal, and analysis — receives correctly scaled data,
    matching what dtype_conversion_func does in the standard 2D pipeline.
    """
    frame = stack_data[t]
    if hasattr(frame, 'compute'):
        frame = frame.compute()
    arr = np.asarray(frame).astype(np.float32)
    # Normalize to [0, 1] if outside that range
    mn, mx = arr.min(), arr.max()
    if mx > 1.0:
        arr = (arr - mn) / (mx - mn + 1e-8)
    return arr


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

def _process_frame_worker(args):
    """
    Top-level picklable function for ProcessPoolExecutor.
    Reads one pre-normalised frame from a filesystem zarr store,
    applies the named processing function, and returns (t, result).
    """
    import warnings
    # Suppress CuPy/CUDA warnings that fire on every worker process spawn
    warnings.filterwarnings("ignore", message="CUDA path could not be detected")
    warnings.filterwarnings("ignore", category=UserWarning, module="cupy")

    t, src_zarr_path, process_fn_name, process_fn_kwargs = args

    import numpy as np
    import zarr as _zarr

    src = _zarr.open(src_zarr_path, mode='r')
    frame = np.asarray(src[t]).astype(np.float32)
    # Source was already normalised to [0,1] by _prepare_source_zarr

    if process_fn_name == 'preprocess':
        from pycat.toolbox.image_processing_tools import pre_process_image
        result = pre_process_image(frame,
                                   process_fn_kwargs['ball_radius'],
                                   process_fn_kwargs['window_size'])
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
    error       = pyqtSignal(str)

    def __init__(self, stack_data, zarr_path, process_fn_name, process_fn_kwargs,
                 n_t, H, W, n_workers=None, parent=None):
        super().__init__(parent)
        self._stack_data        = stack_data
        self._path              = zarr_path
        self._process_fn_name   = process_fn_name
        self._process_fn_kwargs = process_fn_kwargs
        self._n_t               = n_t
        self._H, self._W        = H, W
        import os
        self._n_workers = n_workers or min(8, max(1, os.cpu_count() - 1))

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
        for t in range(self._n_t):
            frame = _read_source_frame(src, t)  # already normalises to [0,1]
            z_out[t] = frame
            self.progress.emit(t + 1, self._n_t * 2)  # first half of progress
        return tmp_path

    def run(self):
        try:
            import zarr as _zarr
            from concurrent.futures import ProcessPoolExecutor, as_completed

            z = _zarr.open(
                self._path, mode='w',
                shape=(self._n_t, self._H, self._W),
                chunks=(1, self._H, self._W),
                dtype=np.float32,
            )

            # Ensure source is a filesystem zarr that subprocesses can open
            src_path = self._prepare_source_zarr()

            args = [
                (t, src_path, self._process_fn_name, self._process_fn_kwargs)
                for t in range(self._n_t)
            ]

            done = 0
            offset = self._n_t   # progress offset after materialisation phase
            batch_size = self._n_workers * 4

            with ProcessPoolExecutor(max_workers=self._n_workers) as executor:
                for batch_start in range(0, self._n_t, batch_size):
                    batch = args[batch_start:batch_start + batch_size]
                    futures = {executor.submit(_process_frame_worker, a): a[0]
                               for a in batch}
                    for future in as_completed(futures):
                        t_idx, result = future.result()
                        z[t_idx] = result
                        done += 1
                        self.progress.emit(offset + done, self._n_t * 2)

            self.finished.emit(self._path)

        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


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

    stack_dropdown = ui_instance.create_layer_dropdown(napari.layers.Image)
    form.addRow("Raw stack layer:", stack_dropdown)

    preprocess_check = QCheckBox("Pre-process each frame")
    preprocess_check.setChecked(True)
    form.addRow("", preprocess_check)

    bg_check = QCheckBox("Background removal each frame")
    bg_check.setChecked(True)
    form.addRow("", bg_check)

    import os as _os
    _n_workers = min(8, max(1, _os.cpu_count() - 1))
    build_btn = QPushButton(f"▶  Process Stack  ({_n_workers} parallel workers)")
    build_btn.setToolTip(
        f"Processes all frames using {_n_workers} CPU cores in parallel,\n"
        "writing each frame to a zarr store on disk as it completes.\n"
        "Napari displays already-processed frames immediately while\n"
        "later frames are still being computed."
    )

    # Worker references kept alive on ui_instance so GC doesn't kill them
    ui_instance._ts_workers = getattr(ui_instance, '_ts_workers', [])

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

        n_t = stack_data.shape[0]
        W   = stack_data.shape[2]
        zarr_dir = _session_zarr_dir()

        build_btn.setEnabled(False)

        def _start_worker(source, fn_name, fn_kwargs, zarr_name, display_name,
                          colormap, on_done_cb=None):
            import zarr as _zarr
            import os
            zarr_path = os.path.join(zarr_dir, zarr_name)
            # Pre-create the zarr store so napari can open it immediately
            _zarr.open(zarr_path, mode='w',
                       shape=(n_t, H, W), chunks=(1, H, W), dtype=np.float32)
            z_arr  = _zarr.open(zarr_path, mode='r')
            wrapper = _ZarrStack(z_arr)
            ui_instance.viewer.add_image(wrapper, name=display_name,
                                         colormap=colormap)

            # Track this zarr ref so the time-series analysis can find it
            setattr(ui_instance, f'_ts_zarr_{zarr_name}', zarr_path)

            worker = _StackProcessWorker(source, zarr_path, fn_name, fn_kwargs,
                                         n_t, H, W)
            ui_instance._ts_workers.append(worker)

            prog = QProgressBar()
            prog.setMaximum(n_t * 2)  # phase 1: materialise source; phase 2: process
            prog.setValue(0)
            build_btn.parent().layout().addWidget(prog)

            def _on_progress(done, total):
                prog.setValue(done)
                # Refresh the napari layer so newly written frames appear
                try:
                    ui_instance.viewer.layers[display_name].refresh()
                except Exception:
                    pass

            def _on_finished(path):
                prog.setValue(n_t)
                napari_show_info(f"'{display_name}' — all {n_t} frames processed.")
                if on_done_cb:
                    on_done_cb(path)
                build_btn.setEnabled(True)

            def _on_error(msg):
                napari_show_warning(f"Processing error — see terminal.")
                print(f"[PyCAT TS Preprocess] ERROR:\n{msg}")
                build_btn.setEnabled(True)

            worker.progress.connect(_on_progress)
            worker.finished.connect(_on_finished)
            worker.error.connect(_on_error)
            worker.start()
            return worker, z_arr



        proc_source   = stack_data
        proc_zarr_ref = None

        if preprocess_check.isChecked():
            br, ws = ball_radius, window_size
            proc_name  = f"Pre-Processed {layer_name}"
            zarr_name  = f"preproc_{id(layer_name)}"

            def _after_preproc(zarr_path):
                nonlocal proc_source
                import zarr as _zarr
                proc_source = _zarr.open(zarr_path, mode='r')
                if bg_check.isChecked():
                    _start_bg(proc_source)

            _start_worker(stack_data, 'preprocess',
                          {'ball_radius': br, 'window_size': ws},
                          zarr_name, proc_name, 'green',
                          on_done_cb=_after_preproc)
        else:
            proc_source = stack_data

        def _start_bg(source):
            bg_name   = f"Enhanced Background Removed {layer_name}"
            zarr_name = f"bgrem_{id(layer_name)}"
            _start_worker(source, 'bg_remove',
                          {'ball_radius': ball_radius},
                          zarr_name, bg_name, 'viridis')

        if bg_check.isChecked() and not preprocess_check.isChecked():
            _start_bg(stack_data)

        # Record for batch — note: slider interactions are NOT recorded
        ui_instance._record('lazy_preprocess_stack', {
            'stack_layer': layer_name,
            'ball_radius': ball_radius,
            'window_size': window_size,
            'preprocess': preprocess_check.isChecked(),
            'bg_removal': bg_check.isChecked(),
        })

    build_btn.clicked.connect(_on_build)
    form.addRow("", build_btn)

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
) -> tuple[pd.DataFrame, np.ndarray]:
    """
    Core time-series condensate analysis — no viewer dependency.

    Parameters
    ----------
    stack : np.ndarray, shape (T, H, W)
        Raw fluorescence image stack.
    preprocessed_stack : np.ndarray, shape (T, H, W) or (H, W)
        Pre-processed image stack.  If 2D, the same preprocessed image
        is used for every frame (useful when only the reference was processed).
    labeled_cell_mask : np.ndarray, shape (H, W)
        Integer-labeled cell mask from the reference frame.
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
        Called after each frame completes for progress reporting.

    Returns
    -------
    results_df : pd.DataFrame
        Tidy DataFrame with columns:
            frame, cell_label, total_condensate_area_px,
            total_condensate_area_um2, cell_area_px,
            condensate_fraction, n_condensates, mean_condensate_area_um2,
            drift_row_px, drift_col_px
    condensate_stack : np.ndarray, shape (T, H, W), dtype uint8
        Stack of refined puncta masks for each frame (for napari display).
    """
    n_frames, H, W = stack.shape

    # Handle 2D preprocessed (single reference frame used for all)
    if preprocessed_stack.ndim == 2:
        preprocessed_stack = np.stack([preprocessed_stack] * n_frames, axis=0)

    cell_labels = np.unique(labeled_cell_mask)
    cell_labels = cell_labels[cell_labels != 0]

    reference_raw = stack[reference_frame].astype(np.float32)

    records = []
    condensate_stack = np.zeros((n_frames, H, W), dtype=np.uint8)

    for t in range(n_frames):
        napari_show_info(f"[TimeSeries] Frame {t + 1}/{n_frames} …")
        print(f"[PyCAT TimeSeries] Frame {t + 1}/{n_frames}")

        frame_raw  = stack[t].astype(np.float32)
        frame_proc = preprocessed_stack[t].astype(np.float32)

        # Drift correction
        dr, dc = 0, 0
        if use_drift_correction and t != reference_frame:
            dr, dc = _phase_shift(frame_raw, reference_raw)
            if dr != 0 or dc != 0:
                frame_raw  = _apply_shift(frame_raw,  dr, dc)
                frame_proc = _apply_shift(frame_proc, dr, dc)
                print(f"[PyCAT TimeSeries]   Drift corrected: row={dr}px col={dc}px")

        # Per-cell condensate segmentation
        total_refined = np.zeros((H, W), dtype=bool)

        for cell_label in cell_labels:
            cell_binary_mask = (labeled_cell_mask == cell_label).astype(bool)

            refined, _ = segment_subcellular_objects(
                frame_raw, frame_proc,
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

            metrics = _condensate_metrics_per_cell(
                refined, cell_binary_mask, int(cell_label), microns_per_pixel_sq
            )
            metrics['frame'] = t
            metrics['drift_row_px'] = dr
            metrics['drift_col_px'] = dc
            records.append(metrics)

        condensate_stack[t] = total_refined.astype(np.uint8)

        if progress_callback is not None:
            progress_callback(t + 1, n_frames)

    # Build tidy DataFrame with frame as first column
    results_df = pd.DataFrame(records)
    col_order = ['frame', 'cell_label',
                 'total_condensate_area_px', 'total_condensate_area_um2',
                 'cell_area_px', 'condensate_fraction',
                 'n_condensates', 'mean_condensate_area_um2',
                 'drift_row_px', 'drift_col_px']
    results_df = results_df[col_order]

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
                progress_callback=_cb, **self._kwargs
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
    drift_checkbox.setChecked(True)
    opts_layout.addRow("", drift_checkbox)

    ts_layout.addWidget(opts_group)

    # ── Refinement parameters (same as condensate segmentation widget) ────
    ref_group = QGroupBox("Refinement Parameters")
    ref_layout = QFormLayout(ref_group)

    def _dspin(lo, hi, val, step, tip=""):
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi); sb.setValue(val)
        sb.setSingleStep(step); sb.setDecimals(2)
        if tip: sb.setToolTip(tip)
        return sb

    min_spot_spin  = _dspin(1, 20, 2, 0.5,  "Minimum puncta radius in pixels.")
    kurtosis_spin  = _dspin(-10, 0, -3.0, 0.5, "Kurtosis threshold. More negative = more permissive.")
    lsnr_spin      = _dspin(0, 5, 1.0, 0.1,  "Local SNR threshold. Lower = keep dimmer puncta.")
    gsnr_spin      = _dspin(0, 5, 1.0, 0.1,  "Global SNR threshold.")
    hwhm_spin      = _dspin(0, 5, 1.17, 0.1, "Intensity scale (multiples of local BG SD).")
    maxarea_spin   = _dspin(0.01, 1.0, 0.25, 0.05, "Max puncta area as fraction of cell area.")

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
    cancel_btn = QPushButton("Cancel")
    cancel_btn.setVisible(False)

    btn_row = QHBoxLayout()
    btn_row.addWidget(run_btn)
    btn_row.addWidget(cancel_btn)
    ts_layout.addLayout(btn_row)

    _worker_ref = [None]   # mutable container so closure can update it

    def _on_run():
        # Validate inputs
        stack_name = stack_dropdown.currentText()
        proc_name  = proc_dropdown.currentText()
        mask_name  = mask_dropdown.currentText()

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

        if mask_data.ndim != 2:
            napari_show_warning("Time-Series: Labels layer must be 2D.")
            return

        data_instance = ui_instance.central_manager.active_data_class
        ball_radius   = float(data_instance.data_repository.get('ball_radius', 50))
        mpx_sq        = float(data_instance.data_repository.get('microns_per_pixel_sq', 1.0))

        kwargs = dict(
            stack=stack_data.astype(np.float32),
            preprocessed_stack=proc_data.astype(np.float32),
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
        )

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
            'max_area_fraction': maxarea_spin.value(),
            'min_spot_radius': min_spot_spin.value(),
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
        summary = results_df.groupby('frame').agg(
            n_cells=('cell_label', 'count'),
            mean_condensate_fraction=('condensate_fraction', 'mean'),
            std_condensate_fraction=('condensate_fraction', 'std'),
            mean_total_area_um2=('total_condensate_area_um2', 'mean'),
            total_n_condensates=('n_condensates', 'sum'),
        ).round(6).reset_index()

        show_dataframes_dialog("Time-Series Condensate Analysis", [
            ("Per-Cell Per-Frame Results", results_df.round(4)),
            ("Summary (per frame)", summary),
        ])

        napari_show_info(
            f"Time-Series analysis complete: {results_df['frame'].nunique()} frames, "
            f"{results_df['cell_label'].nunique()} cells."
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
    plt.show()
