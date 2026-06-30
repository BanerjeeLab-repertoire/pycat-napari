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

    # Propagate: each frame gets the mask of its nearest keyframe
    mask_stack = np.zeros((n_t, H, W), dtype=np.uint16)
    for t in range(n_t):
        nearest = min(keyframe_indices, key=lambda k: abs(k - t))
        mask_stack[t] = keyframe_masks[nearest]

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
        stack_np = np.asarray(data).astype(np.float32)

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
                # Union of all keyframe masks — conservative cell ROI
                union = np.zeros(mask_stack.shape[1:], dtype=np.uint16)
                for t in kf_indices:
                    union = np.where(mask_stack[t] > 0, mask_stack[t], union)
                mask_stack = np.broadcast_to(union, mask_stack.shape).copy()

            # Add (T, H, W) label stack to viewer
            ts_mask_name = f"TS Cell Masks [{layer_name}]"
            ui_instance.viewer.add_labels(
                mask_stack, name=ts_mask_name
            )

            # Also add frame-0 mask as a standard 2D Labels layer
            # so Cell Analyzer and downstream tools work unchanged
            ui_instance.viewer.add_labels(
                mask_stack[0], name="Labeled Cell Mask"
            )

            # Store in data instance
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
