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
from pycat.utils.general_utils import debug_log
import pandas as pd
import skimage as sk
# ── napari and Qt are imported LAZILY ─────────────────────────────────────────
#
# Of the 23 top-level objects in this module, five use a GUI symbol: two QThread workers and
# three widget builders. The analysis — `estimate_temporal_correlation`,
# `upscale_stack_to_zarr` and the rest — uses none. A module-scope import blocked the
# headless import of all of it for the sake of the widgets.
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning

from pycat.toolbox.segmentation_tools import segment_subcellular_objects
from pycat.toolbox.ts_cache_manager import (
    get_cache_paths, cache_exists, write_meta, discard_cache, cache_size_mb
)


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


# ---------------------------------------------------------------------------
# Lazy zarr stack access: session dir, source-frame read, global range, materialize  ->  moved to frame_access.py (1.6.244)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.frame_access import (  # noqa: E402,F401
    _session_zarr_dir, _read_source_frame, _compute_stack_global_range, _get_zarr_dir_path, _materialize_stack_to_zarr)



# ---------------------------------------------------------------------------
# Temporal correlation estimate + regime/recommendation  ->  moved to correlation.py (1.6.244)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.correlation import (  # noqa: E402,F401
    estimate_temporal_correlation)



# ---------------------------------------------------------------------------
# _ZarrStack lazy napari-compatible wrapper  ->  moved to frame_access.py (1.6.244)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.frame_access import (  # noqa: E402,F401
    _ZarrStack)



# ---------------------------------------------------------------------------
# Parallel frame processing helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Parallel subprocess frame reader  ->  moved to execution.py (1.6.246)
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# _init_worker_threads (ProcessPoolExecutor thread-pinning initializer, shared)  ->  moved to analysis.py (1.6.245)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.analysis import (  # noqa: E402,F401
    _init_worker_threads)



# ---------------------------------------------------------------------------
# Parallel frame worker + the stack-process QThread-worker factory  ->  moved to execution.py (1.6.246)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.execution import (  # noqa: E402,F401
    _make__stackprocessworker)




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


def _build_ts_upscale_check_ui(ui_instance):
    """Build the upscale group box, its input rows (raw-stack dropdown, factor
    spinbox, advice label) and the 'check if upscaling is needed' button plus its
    handler. Returns (grp, form, stack_dropdown, factor_spin) so the caller can
    attach the run controls and worker below. Pure UI construction moved verbatim
    out of _add_ts_upscale_stack to keep that function under the complexity gate.
    """
    import napari
    from PyQt5.QtWidgets import QFormLayout, QGroupBox, QLabel, QPushButton, QSizePolicy, QSpinBox

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
    return grp, form, stack_dropdown, factor_spin


def _add_ts_upscale_stack(ui_instance, layout=None, separate_widget=False):
    """Optional early upscale step for the time-series workflow.

    Upscales the raw stack frame-by-frame into a lazy zarr-backed stack, so the
    whole downstream pipeline (preprocess, Cellpose, condensate analysis) runs on
    the upscaled data — matching the 2D workflow order (upscale BEFORE
    preprocess). Optional and gated: if the data already meets Cellpose's
    resolution needs, upscaling is unnecessary and the check says so.
    """
    # QThread/pyqtSignal are for the NESTED _UpWorker class defined below in this
    # function — nesting already makes it lazy; it just needs the names in scope.
    from PyQt5.QtCore import QThread, pyqtSignal
    # GUI imported here, not at module scope — the analysis in this module needs none.
    from PyQt5.QtWidgets import QFormLayout, QGroupBox, QLabel, QProgressBar, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout
    import napari
    from PyQt5.QtWidgets import QGroupBox, QFormLayout, QPushButton, QSpinBox, QLabel

    grp, form, stack_dropdown, factor_spin = _build_ts_upscale_check_ui(ui_instance)

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
    # GUI imported here, not at module scope — the analysis in this module needs none.
    from PyQt5.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QLabel, QProgressBar, QPushButton, QSizePolicy, QWidget
    import napari
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
        # The IMS source path used to live on file_io._ims_file_path, but that attribute was removed
        # when IMS reader retention moved to the layer-scoped ImageSource — it is no longer set, so
        # the source file is simply the last-opened path on file_io.filePath (set for every loader).
        source_file = getattr(ui_instance.central_manager.file_io, 'filePath', None)
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

        # Determine cache paths — use source file path if available. (file_io._ims_file_path was
        # removed with the IMS→ImageSource retention migration; filePath is the source path now.)
        source_file = getattr(ui_instance.central_manager.file_io, 'filePath', None)

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

            worker = _make__stackprocessworker()(
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

            worker = _make__stackprocessworker()(
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
# Time-series condensate analysis: run_timeseries_condensate_analysis + per-frame worker + drift/metrics helpers  ->  moved to analysis.py (1.6.245)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.analysis import (  # noqa: E402,F401
    _phase_shift, _apply_shift, _condensate_metrics_per_cell, _ts_analyze_frame_worker, run_timeseries_condensate_analysis)



# ---------------------------------------------------------------------------
# Background worker thread
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Time-series analysis QThread-worker factory  ->  moved to execution.py (1.6.246)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.execution import (  # noqa: E402,F401
    _make_timeseriesworker)




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
    # GUI imported here, not at module scope — the analysis in this module needs none.
    from PyQt5.QtWidgets import QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget
    import napari
    from pycat.ui.ui_utils import show_dataframes_dialog
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

        worker = _make_timeseriesworker()(kwargs)
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

    def _append_ripley_pcf_tables(results_df, condensate_stack, data_instance, tables):
        """Post-run Ripley's L / PCF per cell across all frames, appended to the
        results-dialog `tables` list in place. Closure over ui_instance,
        _run_ripley_ref and _mask_name_ref; moved verbatim out of _on_finished to
        keep that function under the complexity gate."""
        _run_ripley = _run_ripley_ref[0]
        if _run_ripley:
            try:
                from pycat.toolbox.spatial_metrology_tools import (
                    ripleys_l, pair_correlation_function, get_puncta_centroids,
                    spatial_null_envelope)
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

                            # ── Against a COMPARTMENT-CONSTRAINED null, not CSR ──────
                            #
                            # L(r) = 0 is the complete-spatial-randomness expectation, and
                            # CSR assumes an object could land ANYWHERE in `area`. It
                            # cannot: these condensates are confined to THIS cell, which is
                            # irregular and usually non-convex — and the confinement itself
                            # produces an apparent signal. Measured (1.5.397) on objects
                            # placed uniformly at random inside a real non-convex cell,
                            # where the truth is no structure at all:
                            #
                            #     r=8  -> L = -0.82   "~random"
                            #     r=29 -> L = -4.95   "strong regularity"
                            #
                            # and at a realistic pixel size the same random objects gave
                            # L = +6.18, i.e. "strong clustering". **The artefact points in
                            # either direction depending on the scale.**
                            #
                            # spatial_null_envelope randomises the points WITHIN THIS CELL,
                            # so whatever the confinement does to L(r) is in the null too
                            # and cancels. Validated: 0/20 false positives on random-in-cell
                            # data (which the CSR line called "regular"), 20/20 detection of
                            # genuine clustering.
                            try:
                                coords_px = sub[['y_px', 'x_px']].values
                                _env_df, _env = spatial_null_envelope(
                                    coords_px, cm, microns_per_pixel=mpx,
                                    n_simulations=99)
                                rl['null_p_value'] = _env.get('p_value', np.nan)
                                rl['null_significant'] = bool(
                                    _env.get('significant', False))
                            except Exception as _e:
                                debug_log("TS Ripley: null envelope failed", _e)
                                rl['null_p_value'] = np.nan
                                rl['null_significant'] = False
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
        _append_ripley_pcf_tables(results_df, condensate_stack, data_instance, tables)

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
