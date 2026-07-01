"""
PyCAT Time-Series Cellpose Segmentation
=========================================
Runs Cellpose at regular keyframe intervals across a (T, H, W) stack and
propagates masks to all frames using nearest-keyframe interpolation.

Rationale
---------
For live-cell condensate imaging, cells move slowly relative to condensate
dynamics.  Running Cellpose on every frame of a 600-frame stack takes
~10+ hours (even on GPU); running it every 20 frames and propagating the
nearest mask takes ~1/20th the time with negligible accuracy loss for the
cell-level analysis that follows.

Interpolation strategy: nearest-keyframe
-----------------------------------------
True spatial interpolation of integer label masks (e.g. morphing mask at
frame 0 toward mask at frame 20) introduces label bleeding at cell
boundaries, requires solving cell correspondence across frames, and adds
significant complexity for small biological benefit — cells simply do not
move enough over 20 frames for the boundary position to matter.

Nearest-keyframe is the correct choice:
  - Frame 0–9   → mask from frame 0 (keyframe)
  - Frame 10–29 → mask from frame 20 (next keyframe closer)
  - etc.

This is exactly what biologists do manually: segment a representative frame
and apply it to a temporal window.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo

Date
----
    2025
"""

from __future__ import annotations

import numpy as np
import napari
from napari.utils.notifications import (
    show_info  as napari_show_info,
    show_warning as napari_show_warning,
)
from PyQt5.QtWidgets import (
    QVBoxLayout, QWidget, QPushButton, QGroupBox,
    QFormLayout, QSpinBox, QProgressBar, QLabel, QCheckBox,
)
from PyQt5.QtCore import QThread, pyqtSignal


# ---------------------------------------------------------------------------
# Lazy mask-stack view
# ---------------------------------------------------------------------------

class _KeyframeMaskStack:
    """
    Lazy read-only view over nearest-keyframe-propagated Cellpose masks.

    Stores only the unique keyframe masks (typically ~n_t/interval of them)
    instead of materialising a full (T, H, W) array with the same mask
    duplicated across every frame in its temporal window. For a 600-frame
    stack at interval=20 (~30 unique masks) and 2048x2048 uint16 masks,
    this is roughly a 20x memory reduction (~5GB -> ~250MB).

    Exposes the same minimal array-protocol duck-typing that napari and
    downstream code already rely on for zarr-backed lazy stacks (see
    _ZarrStack in timeseries_condensate_tools.py): shape, dtype, ndim,
    __getitem__, __array__, __len__. Read-only — this data is never
    mutated after creation in the current pipeline.
    """
    def __init__(self, keyframe_masks: dict, keyframe_indices: list, n_t: int):
        self._keyframe_masks   = keyframe_masks
        self._keyframe_indices = sorted(keyframe_indices)
        self._n_t = n_t
        sample = next(iter(keyframe_masks.values()))
        self.shape = (n_t,) + sample.shape
        self.dtype = sample.dtype
        self.ndim  = 3

    def _nearest_keyframe(self, t: int) -> int:
        return min(self._keyframe_indices, key=lambda k: abs(k - t))

    def __getitem__(self, idx):
        if isinstance(idx, (int, np.integer)):
            t = int(idx)
            if t < 0:
                t += self._n_t
            return self._keyframe_masks[self._nearest_keyframe(t)]
        if isinstance(idx, slice):
            indices = range(*idx.indices(self._n_t))
            return np.stack([self[i] for i in indices], axis=0)
        idx_arr = np.asarray(idx)
        if idx_arr.ndim == 1:
            return np.stack([self[int(i)] for i in idx_arr], axis=0)
        raise IndexError(f"Unsupported index for _KeyframeMaskStack: {idx!r}")

    def __array__(self, dtype=None):
        arr = np.stack([self[t] for t in range(self._n_t)], axis=0)
        return arr if dtype is None else arr.astype(dtype)

    def __len__(self):
        return self._n_t


# ---------------------------------------------------------------------------
# Pure analysis functions
# ---------------------------------------------------------------------------

def run_keyframe_cellpose(
    stack: np.ndarray,
    cell_diameter: float,
    keyframe_interval: int,
    progress_callback=None,
) -> tuple[np.ndarray, list[int]]:
    """
    Run Cellpose on keyframes and return a (T, H, W) label stack with
    nearest-keyframe interpolation for non-keyframes.

    Parameters
    ----------
    stack : np.ndarray, shape (T, H, W)
        Pre-processed image stack (float32, values should be in [0, 1]).
    cell_diameter : float
        Expected cell diameter in pixels (passed to Cellpose).
    keyframe_interval : int
        Run Cellpose every this many frames.  E.g. 20 means frames
        0, 20, 40, … get Cellpose; all others get the nearest keyframe mask.
    progress_callback : callable(done, total) or None

    Returns
    -------
    mask_stack : np.ndarray, shape (T, H, W), dtype uint16
        Per-frame labeled cell masks.
    keyframe_indices : list of int
        Which frames were actually segmented by Cellpose.
    """
    from pycat.toolbox.segmentation_tools import cellpose_segmentation

    n_t, H, W = stack.shape

    # Determine keyframe indices
    keyframe_indices = list(range(0, n_t, keyframe_interval))
    if (n_t - 1) not in keyframe_indices:
        keyframe_indices.append(n_t - 1)  # always include the last frame

    # Run Cellpose on each keyframe
    keyframe_masks = {}
    for i, t in enumerate(keyframe_indices):
        frame = stack[t].astype(np.float32)
        mask  = cellpose_segmentation(frame, cell_diameter)
        keyframe_masks[t] = mask.astype(np.uint16)
        if progress_callback:
            progress_callback(i + 1, len(keyframe_indices))

    # Propagate: each frame gets the mask of its nearest keyframe.
    # Lazy view instead of full (T, H, W) materialisation — see
    # _KeyframeMaskStack docstring for the memory-saving rationale.
    mask_stack = _KeyframeMaskStack(keyframe_masks, keyframe_indices, n_t)

    return mask_stack, keyframe_indices


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _KeyframeCellposeWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object, object)   # (mask_stack, keyframe_indices)
    error    = pyqtSignal(str)

    def __init__(self, stack, cell_diameter, interval, parent=None):
        super().__init__(parent)
        self._stack    = stack
        self._diameter = cell_diameter
        self._interval = interval

    def run(self):
        try:
            def _cb(done, total):
                self.progress.emit(done, total)
            mask_stack, kf = run_keyframe_cellpose(
                self._stack, self._diameter, self._interval, _cb
            )
            self.finished.emit(mask_stack, kf)
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


# ---------------------------------------------------------------------------
# UI widget
# ---------------------------------------------------------------------------

def _add_run_ts_cellpose(ui_instance, layout=None, separate_widget=False):
    """
    Widget for time-series Cellpose segmentation with keyframe interpolation.
    Replaces the standard Cellpose widget in the Time-Series pipeline.

    The user selects:
    - Which pre-processed stack layer to segment (typically the
      Pre-Processed reference frame layer, or a full preprocessed stack)
    - Keyframe interval (e.g. every 20 frames)
    - Whether to also produce a maximum-projection mask (useful if cells
      move slightly between keyframes)

    Output: a (T, H, W) Labels stack named "TS Cell Masks" added to the
    viewer, plus the standard 2D "Labeled Cell Mask" from frame 0 for
    downstream cell analysis compatibility.
    """
    grp   = QGroupBox("Time-Series Cellpose (Keyframe)")
    form  = QFormLayout(grp)

    stack_dropdown = ui_instance.create_layer_dropdown(napari.layers.Image)
    form.addRow("Pre-processed stack:", stack_dropdown)

    interval_spin = QSpinBox()
    interval_spin.setRange(1, 200)
    interval_spin.setValue(20)
    interval_spin.setToolTip(
        "Run Cellpose every N frames.  All other frames reuse the\n"
        "nearest keyframe mask.  Lower = more accurate but slower.\n"
        "Typical live-cell data: 10–30 frames."
    )
    form.addRow("Keyframe interval (frames):", interval_spin)

    max_proj_check = QCheckBox("Use max-projection across keyframes")
    max_proj_check.setChecked(False)
    max_proj_check.setToolTip(
        "If cells shift between keyframes, taking the union (max-projection)\n"
        "of all keyframe masks produces a conservative cell ROI that covers\n"
        "the full range of motion.  Useful for drift-prone acquisitions."
    )
    form.addRow("", max_proj_check)

    progress_bar = QProgressBar()
    progress_bar.setVisible(False)
    progress_label = QLabel("")
    progress_label.setVisible(False)

    run_btn = QPushButton("▶  Run Keyframe Cellpose")

    def _on_run():
        layer_name = stack_dropdown.currentText()
        try:
            layer = ui_instance.viewer.layers[layer_name]
        except KeyError:
            napari_show_warning(f"Layer '{layer_name}' not found.")
            return

        data = layer.data
        # If the layer is a 2D reference frame, treat it as a single-frame stack
        if data.ndim == 2:
            data = data[np.newaxis]

        if data.ndim != 3:
            napari_show_warning("Time-Series Cellpose requires a 3D (T, H, W) layer.")
            return

        n_t = data.shape[0]
        interval  = interval_spin.value()
        n_kf = len(range(0, n_t, interval))
        cell_diameter = float(
            ui_instance.central_manager.active_data_class
            .data_repository.get('cell_diameter', 100)
        )

        progress_bar.setMaximum(n_kf)
        progress_bar.setValue(0)
        progress_bar.setVisible(True)
        progress_label.setText(f"Running Cellpose on 0 / {n_kf} keyframes…")
        progress_label.setVisible(True)
        run_btn.setEnabled(False)

        # Materialise stack to numpy if lazy
        # Respect frame range set by reference frame selector
        dr      = ui_instance.central_manager.active_data_class.data_repository
        t_start = int(dr.get('timeseries_frame_start', 0))
        t_end   = int(dr.get('timeseries_frame_end', data.shape[0] - 1))
        t_start = max(0, min(t_start, data.shape[0] - 1))
        t_end   = max(t_start, min(t_end, data.shape[0] - 1))

        if t_start > 0 or t_end < data.shape[0] - 1:
            napari_show_info(
                f"Keyframe Cellpose: using frame range {t_start}–{t_end} "
                f"({t_end - t_start + 1} of {data.shape[0]} frames)."
            )
            stack_np = np.asarray(data[t_start:t_end + 1]).astype(np.float32)
        else:
            stack_np = np.asarray(data).astype(np.float32)

        # Apply XY ROI crop if set
        roi_active = dr.get('timeseries_roi_active', False)
        if roi_active:
            y0 = int(dr.get('timeseries_roi_y0', 0))
            y1 = int(dr.get('timeseries_roi_y1', stack_np.shape[1]))
            x0 = int(dr.get('timeseries_roi_x0', 0))
            x1 = int(dr.get('timeseries_roi_x1', stack_np.shape[2]))
            y0, y1 = max(0, y0), min(stack_np.shape[1], y1)
            x0, x1 = max(0, x0), min(stack_np.shape[2], x1)
            stack_np = stack_np[:, y0:y1, x0:x1]
            napari_show_info(
                f"Keyframe Cellpose: XY crop y[{y0}:{y1}] x[{x0}:{x1}] applied."
            )

        # Store range on data repository for condensate analysis step
        dr['timeseries_frame_start'] = t_start
        dr['timeseries_frame_end']   = t_end

        worker = _KeyframeCellposeWorker(stack_np, cell_diameter, interval)
        ui_instance._ts_cellpose_worker = worker

        def _on_progress(done, total):
            progress_bar.setValue(done)
            progress_label.setText(f"Cellpose: {done} / {total} keyframes done…")

        def _on_finished(mask_stack, kf_indices):
            progress_bar.setVisible(False)
            progress_label.setVisible(False)
            run_btn.setEnabled(True)

            if max_proj_check.isChecked():
                # Union of all keyframe masks — conservative cell ROI.
                # np.broadcast_to WITHOUT .copy() creates a read-only
                # stride-tricked view: the same 2D union array is presented
                # as (T,H,W) without allocating n_t separate copies in
                # memory. Safe here since this data is only read/displayed,
                # never mutated after creation.
                union = np.zeros(mask_stack.shape[1:], dtype=np.uint16)
                for t in kf_indices:
                    union = np.where(mask_stack[t] > 0, mask_stack[t], union)
                mask_stack = np.broadcast_to(union, mask_stack.shape)

            # Add (T, H, W) label stack to viewer as a genuinely writable
            # array — napari Labels layers support paint/edit tools, so a
            # read-only lazy view or broadcast_to view (used above purely
            # to save memory during storage/return) must be materialised
            # into a real, independent array here rather than handed to
            # add_labels() directly. This is a one-time cost paid only if
            # the user displays the layer, not held throughout the session.
            display_stack = np.asarray(mask_stack).copy()

            ts_mask_name = f"TS Cell Masks [{layer_name}]"
            ui_instance.viewer.add_labels(
                display_stack, name=ts_mask_name
            )

            # Also add frame-0 mask as a standard 2D Labels layer
            # so Cell Analyzer and downstream tools work unchanged
            ui_instance.viewer.add_labels(
                np.asarray(mask_stack[0]).copy(), name="Labeled Cell Mask"
            )

            # Store the LAZY (or broadcast-view) version in the data
            # repository, not display_stack — this data is only ever read,
            # never mutated, downstream (confirmed: batch_step_registry.py
            # stores it into per-file state without further writes), so
            # keeping it lazy here avoids holding a full duplicated-frame
            # array in memory for the rest of the session.
            data_inst = ui_instance.central_manager.active_data_class
            data_inst.data_repository['ts_cell_mask_stack'] = mask_stack
            data_inst.data_repository['ts_cellpose_keyframes'] = kf_indices

            n_cells = int(mask_stack[0].max())
            napari_show_info(
                f"Keyframe Cellpose complete: {len(kf_indices)} keyframes, "
                f"{n_t} total frames, {n_cells} cells in frame 0. "
                f"Mask stack → '{ts_mask_name}'"
            )

            # Record for batch
            ui_instance._record('ts_cellpose_keyframe', {
                'stack_layer':        layer_name,
                'keyframe_interval':  interval,
                'cell_diameter':      cell_diameter,
                'max_projection':     max_proj_check.isChecked(),
            })

        def _on_error(msg):
            progress_bar.setVisible(False)
            progress_label.setVisible(False)
            run_btn.setEnabled(True)
            napari_show_warning("Keyframe Cellpose error — see terminal.")
            print(f"[PyCAT TS Cellpose] ERROR:\n{msg}")

        worker.progress.connect(_on_progress)
        worker.finished.connect(_on_finished)
        worker.error.connect(_on_error)
        worker.start()

    run_btn.clicked.connect(_on_run)
    form.addRow("", progress_bar)
    form.addRow("", progress_label)
    form.addRow("", run_btn)

    widget = QWidget()
    layout_ = QVBoxLayout(widget)
    layout_.addWidget(grp)
    ui_instance._add_widget_to_layout_or_dock(
        widget, layout, separate_widget, "Time-Series Cellpose"
    )
