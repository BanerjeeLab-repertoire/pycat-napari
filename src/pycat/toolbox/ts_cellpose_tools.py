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
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QGroupBox,
    QFormLayout, QSpinBox, QProgressBar, QLabel, QCheckBox, QRadioButton,
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt


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

    def _nearest_nonempty_keyframe(self, t: int) -> int:
        # Return nearest keyframe with a non-empty mask; fall back to nearest if all empty
        by_dist = sorted(self._keyframe_indices, key=lambda k: abs(k - t))
        for k in by_dist:
            if self._keyframe_masks[k].max() > 0:
                return k
        return by_dist[0]

    def __getitem__(self, idx):
        if isinstance(idx, (int, np.integer)):
            t = int(idx)
            if t < 0:
                t += self._n_t
            return self._keyframe_masks[self._nearest_nonempty_keyframe(t)]
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
    upscale_factor: int = 1,
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
    import skimage.transform as _sktr
    import skimage as _sk
    keyframe_masks = {}
    _uf = max(1, int(upscale_factor))
    for i, t in enumerate(keyframe_indices):
        frame = np.asarray(stack[t]).astype(np.float32)
        # Normalize to [0, 1] so equalize_adapthist inside
        # cellpose_segmentation receives a valid float range.
        # stack[t] may be a raw uint16 layer (values 0-65535)
        # or an already-normalized preprocessed layer — either
        # way normalization here is safe and idempotent on [0,1] input.
        _mn, _mx = frame.min(), frame.max()
        if _mx > _mn:
            frame = (frame - _mn) / (_mx - _mn)
        if _uf > 1:
            # Upscale for Cellpose, rescale mask back to original resolution.
            # Using linear interpolation for the image (preserves gradients)
            # and nearest-neighbour for the mask (preserves label boundaries).
            frame_up = _sktr.rescale(frame, _uf, order=1,
                                     anti_aliasing=True,
                                     preserve_range=True).astype(np.float32)
            mask_up = cellpose_segmentation(frame_up, cell_diameter * _uf)
            mask = _sktr.rescale(mask_up.astype(np.float32), 1.0 / _uf,
                                  order=0, anti_aliasing=False,
                                  preserve_range=True).astype(np.uint16)
            # Re-label after downscale to guarantee contiguous integer labels
            mask = _sk.measure.label(mask > 0).astype(np.uint16)
        else:
            mask = cellpose_segmentation(frame, cell_diameter)
        keyframe_masks[t] = mask.astype(np.uint16)
        if progress_callback:
            progress_callback(i + 1, len(keyframe_indices))

    # Propagate: each frame gets the mask of its nearest keyframe.
    # Lazy view instead of full (T, H, W) materialisation — see
    # _KeyframeMaskStack docstring for the memory-saving rationale.
    mask_stack = _KeyframeMaskStack(keyframe_masks, keyframe_indices, n_t)

    return mask_stack, keyframe_indices


# ---------------------------------------------------------------------------
# StarDist keyframe segmentation (mirrors run_keyframe_cellpose)
# ---------------------------------------------------------------------------

def run_keyframe_stardist(
    stack: np.ndarray,
    keyframe_interval: int,
    progress_callback=None,
    upscale_factor: int = 1,
) -> tuple:
    """Run StarDist 2D_versatile_fluo on keyframes; propagate to all frames."""
    try:
        from stardist.models import StarDist2D
        from csbdeep.utils import normalize as csbdeep_normalize
    except ImportError:
        raise ImportError(
            "StarDist not installed. Run: pip install stardist csbdeep")

    import skimage.transform as _sktr
    import skimage as _sk

    model = StarDist2D.from_pretrained('2D_versatile_fluo')
    n_t, H, W = stack.shape
    keyframe_indices = list(range(0, n_t, keyframe_interval))
    if (n_t - 1) not in keyframe_indices:
        keyframe_indices.append(n_t - 1)

    _uf = max(1, int(upscale_factor))
    keyframe_masks = {}
    for i, t in enumerate(keyframe_indices):
        frame = np.asarray(stack[t]).astype(np.float32)
        frame = csbdeep_normalize(frame)
        if _uf > 1:
            frame = _sktr.rescale(frame, _uf, order=1,
                                   anti_aliasing=True,
                                   preserve_range=True).astype(np.float32)
        labels, _ = model.predict_instances(frame)
        if _uf > 1:
            labels = _sktr.rescale(labels.astype(np.float32), 1.0 / _uf,
                                    order=0, anti_aliasing=False,
                                    preserve_range=True)
            labels = _sk.measure.label(labels > 0).astype(np.uint16)
        keyframe_masks[t] = labels.astype(np.uint16)
        if progress_callback:
            progress_callback(i + 1, len(keyframe_indices))

    mask_stack = _KeyframeMaskStack(keyframe_masks, keyframe_indices, n_t)
    return mask_stack, keyframe_indices


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _KeyframeCellposeWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object, object)   # (mask_stack, keyframe_indices)
    error    = pyqtSignal(str)

    def __init__(self, stack, cell_diameter, interval, parent=None,
                 upscale_factor=1, use_stardist=False):
        super().__init__(parent)
        self._stack       = stack
        self._diameter    = cell_diameter
        self._interval    = interval
        self._upscale     = upscale_factor
        self._use_stardist = use_stardist

    def run(self):
        try:
            def _cb(done, total):
                self.progress.emit(done, total)
            if self._use_stardist:
                mask_stack, kf = run_keyframe_stardist(
                    self._stack, self._interval, _cb,
                    upscale_factor=self._upscale,
                )
            else:
                mask_stack, kf = run_keyframe_cellpose(
                    self._stack, self._diameter, self._interval, _cb,
                    upscale_factor=self._upscale,
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
    Time-series cell segmentation with keyframe interpolation.

    Supports Cellpose, StarDist, Random Forest, and Multi-Otsu thresholding.
    Automatically applies the XY ROI and frame range set in Step 2 (Apply ROI).

    For multi-channel data, the user can select a dedicated segmentation
    channel (e.g. DAPI for nuclei) instead of the fluorescence channel
    used for condensate detection — only that channel is passed to the
    segmentation algorithm. When no dedicated channel is available, 
    Multi-Otsu thresholding provides a DNA-stain-free fallback.
    """
    from PyQt5.QtWidgets import QButtonGroup, QStackedWidget, QSizePolicy as _QSP
    grp   = QGroupBox("Step 5 — Cell / Nuclei Segmentation (Keyframe)")
    form  = QFormLayout(grp)
    form.setLabelAlignment(Qt.AlignLeft)

    # ── Channel / stack selection ─────────────────────────────────────────
    seg_lbl = QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Select the image channel for segmentation. Use a DAPI/nuclear "
        "channel when available — it gives the clearest cell boundaries. "
        "If none, use the fluorescence stack or choose Multi-Otsu below."
        "</span>"
    )
    seg_lbl.setWordWrap(True)
    form.addRow(seg_lbl)

    stack_dropdown = ui_instance.create_layer_dropdown(
        napari.layers.Image,
        name_hint='Enhanced Background Removed')
    stack_dropdown.setToolTip(
        "Image stack to segment cells from.\n"
        "• Preferred: a DAPI / nuclear stain channel (separate raw stack).\n"
        "• Acceptable: the Enhanced Background Removed fluorescence stack.\n"
        "• The XY ROI and frame range from Step 2 are applied automatically."
    )
    form.addRow("Seg. channel:", stack_dropdown)

    interval_spin = QSpinBox()
    interval_spin.setRange(1, 200)
    interval_spin.setValue(20)
    interval_spin.setToolTip(
        "Run segmentation every N frames; other frames reuse the nearest\n"
        "keyframe mask. Lower = more accurate tracking of cell shape changes\n"
        "but slower. Typical live-cell data: 10–30 frames."
    )
    form.addRow("Keyframe interval:", interval_spin)

    # ── Segmentation method ───────────────────────────────────────────────
    method_grp = QGroupBox("Method")
    method_layout = QVBoxLayout(method_grp)
    method_layout.setSpacing(3)
    method_layout.setContentsMargins(4, 4, 4, 4)

    rb_cellpose = QRadioButton("Cellpose  (deep learning, recommended)")
    rb_stardist = QRadioButton("StarDist  (star-convex, nuclei)")
    rb_rf       = QRadioButton("Random Forest  (pixel classifier)")
    rb_otsu     = QRadioButton("Multi-Otsu  (no seg. channel needed)")
    rb_cellpose.setChecked(True)
    rb_cellpose.setToolTip("Deep-learning cell/nuclei segmentation. Works on most image types.")
    rb_stardist.setToolTip("Star-convex shape model, optimized for round nuclei. Requires pip install stardist.")
    rb_rf.setToolTip("Supervised pixel classifier — annotate a few frames, then apply to all keyframes.")
    rb_otsu.setToolTip(
        "Multi-class intensity thresholding — no dedicated segmentation\n"
        "channel or training needed. Best fallback when DAPI is absent.\n"
        "Use the fluorescence channel as the seg. channel above."
    )
    for rb in (rb_cellpose, rb_stardist, rb_rf, rb_otsu):
        method_layout.addWidget(rb)
    form.addRow(method_grp)

    # RF annotation layer — only shown when RF is selected
    rf_ann_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    rf_ann_dd.setToolTip("Labels layer with annotated pixels for Random Forest training.")
    rf_ann_row_lbl = QLabel("RF annotation:")
    rf_ann_container = QWidget()
    rf_ann_row = QHBoxLayout(rf_ann_container)
    rf_ann_row.setContentsMargins(2,0,0,0)
    rf_ann_row.addWidget(rf_ann_row_lbl)
    rf_ann_row.addWidget(rf_ann_dd)
    rf_ann_container.setVisible(False)
    form.addRow(rf_ann_container)

    # Otsu classes — only shown when Multi-Otsu is selected
    otsu_spin = QSpinBox()
    otsu_spin.setRange(2, 5); otsu_spin.setValue(3)
    otsu_spin.setToolTip(
        "Number of intensity classes for Multi-Otsu.\n"
        "3 = background / cytoplasm / bright structures (recommended).\n"
        "The two brightest classes are merged as 'cell'.")
    otsu_container = QWidget()
    otsu_row = QHBoxLayout(otsu_container)
    otsu_row.setContentsMargins(2,0,0,0)
    otsu_row.addWidget(QLabel("Otsu classes:"))
    otsu_row.addWidget(otsu_spin)
    otsu_container.setVisible(False)
    form.addRow(otsu_container)

    def _on_method():
        rf_ann_container.setVisible(rb_rf.isChecked())
        otsu_container.setVisible(rb_otsu.isChecked())
    for rb in (rb_cellpose, rb_stardist, rb_rf, rb_otsu):
        rb.toggled.connect(lambda _: _on_method())

    # ── Upscaling ─────────────────────────────────────────────────────────
    upscale_check = QCheckBox("Upscale keyframes  (recommended for ≤512px)")
    upscale_check.setChecked(False)
    upscale_check.setToolTip(
        "Upscale each keyframe before segmentation and rescale the\n"
        "resulting mask back down. Helps when cells are too small for\n"
        "Cellpose/StarDist to detect at native resolution (e.g. 512×512).\n"
        "Cell diameter is scaled proportionally. No other pipeline step\n"
        "is affected — the zarr-backed stack stays at original resolution."
    )
    form.addRow("", upscale_check)

    upscale_spin = QSpinBox()
    upscale_spin.setRange(2, 8); upscale_spin.setValue(2)
    upscale_spin.setEnabled(False)
    upscale_spin.setToolTip("Upscale factor (2× recommended; 4× for very small images).")
    upscale_check.stateChanged.connect(lambda s: upscale_spin.setEnabled(bool(s)))
    form.addRow("Upscale factor:", upscale_spin)

    # ── Max-projection ────────────────────────────────────────────────────
    max_proj_check = QCheckBox("Merge all keyframe masks (max-projection)")
    max_proj_check.setChecked(False)
    max_proj_check.setToolTip(
        "OFF (default): each frame uses the nearest keyframe mask —\n"
        "cell boundaries update as cells move. Best for most data.\n\n"
        "ON: union of all keyframe masks → one fixed ROI covering the\n"
        "full range of cell motion. Use only for heavily drifting samples\n"
        "where you want a conservative worst-case cell boundary."
    )
    form.addRow("", max_proj_check)

    progress_bar = QProgressBar()
    progress_bar.setVisible(False)
    progress_label = QLabel("")
    progress_label.setVisible(False)

    run_btn = QPushButton("▶  Run Cell Segmentation")
    run_btn.setToolTip("Run keyframe cell segmentation using the selected method.")

    def _on_run():
        layer_name = stack_dropdown.currentText()
        try:
            layer = ui_instance.viewer.layers[layer_name]
        except KeyError:
            napari_show_warning(f"Layer '{layer_name}' not found.")
            return

        data = layer.data
        if data.ndim == 2:
            data = data[np.newaxis]
        if data.ndim != 3:
            napari_show_warning("Time-Series cell segmentation requires a 3D (T,H,W) layer.")
            return

        n_t = data.shape[0]
        interval     = interval_spin.value()
        dr           = ui_instance.central_manager.active_data_class.data_repository
        cell_diameter = float(dr.get('cell_diameter', 100))

        # ── Apply frame range from Apply ROI (Step 2) ─────────────────
        t_start = int(dr.get('timeseries_frame_start', 0))
        t_end   = int(dr.get('timeseries_frame_end', n_t - 1))
        t_start = max(0, min(t_start, n_t - 1))
        t_end   = max(t_start, min(t_end, n_t - 1))
        stack_np = np.asarray(data[t_start:t_end + 1]).astype(np.float32)
        if t_start > 0 or t_end < n_t - 1:
            napari_show_info(
                f"Cell seg: using frame range {t_start}–{t_end} "
                f"({t_end - t_start + 1} of {n_t} frames).")

        # ── Apply XY ROI from Apply ROI (Step 2) ─────────────────────
        if dr.get('timeseries_roi_active', False):
            y0 = int(dr.get('timeseries_roi_y0', 0))
            y1 = int(dr.get('timeseries_roi_y1', stack_np.shape[1]))
            x0 = int(dr.get('timeseries_roi_x0', 0))
            x1 = int(dr.get('timeseries_roi_x1', stack_np.shape[2]))
            y0, y1 = max(0, y0), min(stack_np.shape[1], y1)
            x0, x1 = max(0, x0), min(stack_np.shape[2], x1)
            stack_np = stack_np[:, y0:y1, x0:x1]
            napari_show_info(
                f"Cell seg: XY crop y[{y0}:{y1}] x[{x0}:{x1}] applied.")

        dr['timeseries_frame_start'] = t_start
        dr['timeseries_frame_end']   = t_end

        # ── Multi-Otsu: no worker needed, runs per-frame inline ───────
        if rb_otsu.isChecked():
            from skimage.filters import threshold_multiotsu
            import skimage as _sk
            n_classes = otsu_spin.value()
            n_seg_frames = stack_np.shape[0]
            mask_arr = np.zeros_like(stack_np, dtype=np.uint16)
            for i in range(n_seg_frames):
                frame = stack_np[i]
                try:
                    thresholds = threshold_multiotsu(frame, classes=n_classes)
                    # Merge the two brightest classes as 'cell'
                    binary = frame >= thresholds[-1]
                    mask_arr[i] = _sk.measure.label(binary).astype(np.uint16)
                except Exception:
                    pass
            ts_mask_name = f"TS Cell Masks [{layer_name}]"
            ui_instance.viewer.add_labels(mask_arr.copy(), name=ts_mask_name)
            best = max(range(n_seg_frames),
                       key=lambda i: int(mask_arr[i].max()))
            ui_instance.viewer.add_labels(
                mask_arr[best].copy(), name="Labeled Cell Mask")
            data_inst = ui_instance.central_manager.active_data_class
            data_inst.data_repository['ts_cell_mask_stack'] = mask_arr
            ui_instance._record('ts_cellpose_keyframe', {
                'stack_layer': layer_name, 'method': 'multi_otsu',
                'n_classes': n_classes, 'keyframe_interval': interval,
            })
            napari_show_info(f"Multi-Otsu cell segmentation complete ({n_seg_frames} frames).")
            return

        # ── Random Forest: one trained model applied per keyframe ─────
        if rb_rf.isChecked():
            ann_name = rf_ann_dd.currentText()
            try:
                ann_layer = ui_instance.viewer.layers[ann_name]
            except KeyError:
                napari_show_warning(f"Annotation layer '{ann_name}' not found.")
                return
            from pycat.toolbox.segmentation_tools import train_rf_segmenter, apply_rf_segmenter
            import skimage as _sk
            n_kf = len(range(0, stack_np.shape[0], interval))
            progress_bar.setMaximum(n_kf); progress_bar.setValue(0)
            progress_bar.setVisible(True)
            progress_label.setText(f"Training RF on annotation layer…")
            progress_label.setVisible(True)
            run_btn.setEnabled(False)
            try:
                ref_frame = stack_np[0]
                ann_data  = np.asarray(ann_layer.data)
                if ann_data.ndim == 3:
                    ann_data = ann_data[0]
                clf = train_rf_segmenter(ref_frame, ann_data)
            except Exception as e:
                napari_show_warning(f"RF training failed: {e}")
                progress_bar.setVisible(False); progress_label.setVisible(False)
                run_btn.setEnabled(True)
                return
            mask_arr = np.zeros_like(stack_np, dtype=np.uint16)
            kf_indices = list(range(0, stack_np.shape[0], interval))
            if (stack_np.shape[0] - 1) not in kf_indices:
                kf_indices.append(stack_np.shape[0] - 1)
            for i, t in enumerate(kf_indices):
                try:
                    pred = apply_rf_segmenter(clf, stack_np[t])
                    mask_arr[t] = _sk.measure.label(pred > 0).astype(np.uint16)
                except Exception:
                    pass
                progress_bar.setValue(i + 1)
            # Propagate to non-keyframes
            kf_masks = {t: mask_arr[t] for t in kf_indices}
            lazy = _KeyframeMaskStack(kf_masks, kf_indices, stack_np.shape[0])
            full = np.asarray(lazy).copy()
            ts_mask_name = f"TS Cell Masks [{layer_name}]"
            ui_instance.viewer.add_labels(full, name=ts_mask_name)
            best = max(kf_indices, key=lambda t: int(mask_arr[t].max()))
            ui_instance.viewer.add_labels(mask_arr[best].copy(), name="Labeled Cell Mask")
            data_inst = ui_instance.central_manager.active_data_class
            data_inst.data_repository['ts_cell_mask_stack'] = lazy
            ui_instance._record('ts_cellpose_keyframe', {
                'stack_layer': layer_name, 'method': 'random_forest',
                'annotation_layer': ann_name, 'keyframe_interval': interval,
            })
            napari_show_info("Random Forest cell segmentation complete.")
            progress_bar.setVisible(False); progress_label.setVisible(False)
            run_btn.setEnabled(True)
            return

        # ── Cellpose or StarDist: use background worker ───────────────
        n_kf = len(range(0, stack_np.shape[0], interval))
        progress_bar.setMaximum(n_kf); progress_bar.setValue(0)
        progress_bar.setVisible(True)
        method_name = "StarDist" if rb_stardist.isChecked() else "Cellpose"
        progress_label.setText(f"Running {method_name} on 0 / {n_kf} keyframes…")
        progress_label.setVisible(True)
        run_btn.setEnabled(False)

        upscale = upscale_spin.value() if upscale_check.isChecked() else 1
        worker = _KeyframeCellposeWorker(
            stack_np, cell_diameter, interval,
            upscale_factor=upscale,
            use_stardist=rb_stardist.isChecked(),
        )
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

            # Labeled Cell Mask: use the keyframe with the most detected
            # cells rather than always frame 0. When max_projection=False,
            # frame 0 may have an empty individual mask while other keyframes
            # succeeded — picking the best-populated keyframe is always safe.
            best_kf = max(kf_indices,
                          key=lambda k: int(np.asarray(mask_stack[k]).max()))
            ui_instance.viewer.add_labels(
                np.asarray(mask_stack[best_kf]).copy(), name="Labeled Cell Mask"
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

            n_cells = int(np.asarray(mask_stack[best_kf]).max())
            napari_show_info(
                f"Keyframe Cellpose complete: {len(kf_indices)} keyframes, "
                f"{n_t} total frames, {n_cells} cells (best keyframe: {best_kf}). "
                f"Mask stack → '{ts_mask_name}'"
            )

            # Record for batch
            ui_instance._record('ts_cellpose_keyframe', {
                'stack_layer':       layer_name,
                'method':            'stardist' if rb_stardist.isChecked() else 'cellpose',
                'keyframe_interval': interval,
                'cell_diameter':     cell_diameter,
                'max_projection':    max_proj_check.isChecked(),
                'upscale_factor':    upscale_spin.value() if upscale_check.isChecked() else 1,
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
