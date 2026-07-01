"""
PyCAT Z-Stack (3D) Condensate Segmentation UI
================================================
Dock widget for 3D condensate analysis on Z-stack acquisitions, built
directly on the same per-plane 2D algorithms as the standard pipeline
(see zstack_segmentation_tools.py for the full rationale).

Pipeline
--------
  Step 1 — Open a (Z, H, W) or (T, Z, H, W) stack via the standard
           multi-dimensional loader (File menu). For (T, Z, H, W) data,
           select a single T-slice here first (napari's T slider).
  Step 2 — 3D background removal (per-slice, assembled into a volume)
  Step 3 — 3D cell segmentation (per-slice Cellpose + Z-stitching by IoU)
  Step 4 — 3D condensate segmentation (per-slice 2D pipeline + 3D Z-link)
  Step 5 — 3D metrics (volume, sphericity, ellipsoid axes, intensity)
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import napari
from napari.utils.notifications import (
    show_info    as napari_show_info,
    show_warning as napari_show_warning,
)
from PyQt5.QtWidgets import (
    QVBoxLayout, QWidget, QPushButton, QGroupBox, QFormLayout,
    QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QProgressBar,
    QScrollArea, QSizePolicy,
)
from PyQt5.QtCore import QThread, pyqtSignal


class _ZWorker(QThread):
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)
    def __init__(self, fn):
        super().__init__(); self._fn = fn
    def run(self):
        try:    self.finished.emit(self._fn())
        except Exception:
            import traceback; self.error.emit(traceback.format_exc())


class ZStackSegmentationUI:
    """
    3D condensate analysis dock. Instantiated by the analysis mode
    switcher; call setup_ui() to build the dock.
    """
    def __init__(self, viewer, central_manager):
        self.viewer = viewer
        self.central_manager = central_manager

    def _dr(self):  return self.central_manager.active_data_class.data_repository
    def _mpx(self): return float(self._dr().get('microns_per_pixel_sq', 1.0)) ** 0.5
    def _record(self, step, params):
        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp: bp.record(step, params)
    def create_layer_dropdown(self, lt):
        return self.central_manager.toolbox_functions_ui.create_layer_dropdown(lt)
    def _vol(self, dd):
        arr = np.asarray(self.viewer.layers[dd.currentText()].data).astype(np.float32)
        mn, mx = arr.min(), arr.max()
        return (arr-mn)/(mx-mn+1e-8) if mx > mn else arr

    def setup_ui(self):
        try:
            self.central_manager.workflow_checklist.activate('zstack')
            bp = getattr(self.central_manager, '_pycat_batch_processor', None)
            if bp:
                for step in bp.config.get('steps', []):
                    self.central_manager.workflow_checklist.on_step_recorded(
                        step['step'])
        except Exception:
            pass

        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(4, 4, 4, 4)
        header = QLabel(
            "<b>Z-Stack (3D) Condensate Segmentation</b><br>"
            "<span style='color:#888;font-size:9pt;'>"
            "Open a (Z,H,W) or (T,Z,H,W) stack via File \u2192 Open Image Stack.<br>"
            "For nested T-Z data, use napari's T slider to pick a single "
            "timepoint\u2014this dock analyses one 3D volume at a time.</span>"
        )
        header.setWordWrap(True)
        header.setStyleSheet("padding:6px; background:#2a2a2a; border-radius:4px;")
        layout.addWidget(header)

        _add_zstack_bg_removal(self, layout)
        _add_zstack_cell_seg(self, layout)
        _add_zstack_condensate_seg(self, layout)
        _add_zstack_metrics(self, layout)

        main_w = QWidget(); main_w.setLayout(layout)
        main_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        from pycat.ui.ui_modules import _apply_scroll_guard
        _apply_scroll_guard(main_w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(main_w)
        self.viewer.window.add_dock_widget(scroll, name="Z-Stack (3D) Condensate Analysis")


def _run_btn(form, label="\u25b6  Run"):
    prog = QProgressBar(); prog.setVisible(False)
    btn  = QPushButton(label)
    form.addRow(prog); form.addRow(btn)
    return prog, btn


def _show(title, tables):
    from pycat.ui.ui_utils import show_dataframes_dialog
    show_dataframes_dialog(title, [(k, v.round(4) if hasattr(v, 'round') else v)
                                   for k, v in tables])


def _add_zstack_bg_removal(ui, layout):
    grp  = QGroupBox("Step 2 \u2014 3D Background Removal")
    form = QFormLayout(grp)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Applies the standard 2D rolling-ball removal to each Z-slice "
        "independently.</span>"
    ))

    vol_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow("Raw Z-stack (Z,H,W):", vol_dd)

    ball_sp = QSpinBox(); ball_sp.setRange(2, 200); ball_sp.setValue(15)
    form.addRow("Ball radius (px):", ball_sp)

    pseudo3d_cb = QCheckBox("Pseudo-3D tri-planar linear filtering")
    pseudo3d_cb.setChecked(True)
    pseudo3d_cb.setToolTip(
        "Runs the Gaussian and Gabor linear filtering steps along all "
        "three orthogonal planes (XY, XZ, YZ) and averages the results, "
        "instead of XY-only. Produces more Z-consistent, less "
        "slice-artifacted results at ~3x the linear-step cost. "
        "Rolling ball, CLAHE, and morphology steps always run per-XY-slice "
        "regardless of this setting."
    )
    form.addRow(pseudo3d_cb)

    prog, run = _run_btn(form, "\u25b6  Remove Background (3D)")

    def _on_run():
        from pycat.toolbox.zstack_segmentation_tools import bg_removal_3d
        try:
            vol = ui._vol(vol_dd)
        except KeyError as e:
            napari_show_warning(str(e)); return
        if vol.ndim != 3:
            napari_show_warning("Needs a 3D (Z,H,W) stack."); return

        n_stages = 3 if pseudo3d_cb.isChecked() else 1
        prog.setRange(0, vol.shape[0] * n_stages); prog.setValue(0); prog.setVisible(True)
        run.setEnabled(False)

        def _task():
            return bg_removal_3d(
                vol, ball_sp.value(),
                progress_callback=lambda done, total: prog.setValue(done),
                pseudo3d_linear=pseudo3d_cb.isChecked())
        worker = _ZWorker(_task)
        ui._zstack_bg_worker = worker

        def _done(bg_removed):
            prog.setVisible(False); run.setEnabled(True)
            name = f"BG-Removed 3D [{vol_dd.currentText()}]"
            ui.viewer.add_image(bg_removed, name=name, colormap='viridis')
            ui._dr()['zstack_bg_removed'] = bg_removed
            ui._dr()['zstack_source']     = vol
            # Store the ball_radius the user actually chose here so Step 4
            # (3D condensate segmentation) reads the SAME value instead of
            # silently falling back to its own hardcoded default — without
            # this, the two steps could use inconsistent ball_radius values
            # with no indication to the user that they'd diverged.
            ui._dr()['ball_radius'] = ball_sp.value()
            ui._record('zstack_bg_removal', {
                'image_layer': vol_dd.currentText(), 'ball_radius': ball_sp.value(),
                'pseudo3d_linear': pseudo3d_cb.isChecked()})
            napari_show_info(f"3D background removal done \u2014 '{name}' ready.")

        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("3D BG removal error \u2014 see terminal.")
            print(f"[PyCAT ZStack BG] {msg}")

        worker.finished.connect(_done); worker.error.connect(_err); worker.start()

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _add_zstack_cell_seg(ui, layout):
    grp  = QGroupBox("Step 3 \u2014 3D Cell Segmentation (optional)")
    form = QFormLayout(grp)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Runs 2D Cellpose per Z-slice, then stitches labels into 3D "
        "cells via IoU overlap between consecutive slices.</span>"
    ))

    vol_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow("Volume for cell segmentation:", vol_dd)

    diam_sp = QSpinBox(); diam_sp.setRange(10, 500); diam_sp.setValue(100)
    form.addRow("Cell diameter (px):", diam_sp)

    iou_sp = QDoubleSpinBox(); iou_sp.setRange(0.05, 0.95); iou_sp.setValue(0.3)
    iou_sp.setToolTip(
        "Minimum IoU overlap between consecutive Z-slices to link labels "
        "as the same 3D cell. Lower = more permissive (cell drifts more "
        "between slices); higher = stricter."
    )
    form.addRow("Min Z-link IoU:", iou_sp)

    prog, run = _run_btn(form, "\u25b6  Segment Cells (3D)")

    def _on_run():
        from pycat.toolbox.zstack_segmentation_tools import cellpose_segmentation_3d
        try:
            vol = ui._vol(vol_dd)
        except KeyError as e:
            napari_show_warning(str(e)); return
        if vol.ndim != 3:
            napari_show_warning("Needs a 3D (Z,H,W) stack."); return

        prog.setRange(0, vol.shape[0]); prog.setValue(0); prog.setVisible(True)
        run.setEnabled(False)

        def _task():
            return cellpose_segmentation_3d(
                vol, diam_sp.value(), min_iou=iou_sp.value(),
                progress_callback=lambda z, n: prog.setValue(z))
        worker = _ZWorker(_task)
        ui._zstack_cell_worker = worker

        def _done(labeled_3d):
            prog.setVisible(False); run.setEnabled(True)
            n = int(labeled_3d.max())
            name = f"3D Cell Mask ({n} cells)"
            ui.viewer.add_labels(labeled_3d, name=name)
            ui._dr()['zstack_cell_mask'] = labeled_3d
            ui._dr()['cell_diameter']    = diam_sp.value()
            ui._record('zstack_cell_segmentation', {
                'image_layer': vol_dd.currentText(),
                'cell_diameter': diam_sp.value(), 'min_iou': iou_sp.value()})
            napari_show_info(f"3D cell segmentation: {n} cells across "
                             f"{labeled_3d.shape[0]} Z-slices.")

        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("3D cell segmentation error \u2014 see terminal.")
            print(f"[PyCAT ZStack Cell] {msg}")

        worker.finished.connect(_done); worker.error.connect(_err); worker.start()

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _add_zstack_condensate_seg(ui, layout):
    grp  = QGroupBox("Step 4 \u2014 3D Condensate Segmentation")
    form = QFormLayout(grp)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Runs the exact 2D condensate segmentation per Z-slice, then "
        "merges overlapping detections across Z into 3D objects.</span>"
    ))

    raw_dd  = ui.create_layer_dropdown(napari.layers.Image)
    proc_dd = ui.create_layer_dropdown(napari.layers.Image)
    cell_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow("Raw volume:", raw_dd)
    form.addRow("BG-removed volume:", proc_dd)
    form.addRow("3D cell mask (or 2D, broadcast):", cell_dd)

    # Ball radius is set in Step 2; shown here (editable) rather than
    # silently inherited, so it's never a hidden value the user can't see.
    ball_sp2 = QSpinBox(); ball_sp2.setRange(2, 200)
    ball_sp2.setValue(int(ui._dr().get('ball_radius', 15)))
    ball_sp2.setToolTip(
        "Defaults to the ball radius set in Step 2. Change here if you "
        "want condensate segmentation to use a different value than "
        "background removal used."
    )
    form.addRow("Ball radius (px):", ball_sp2)

    min_r = QDoubleSpinBox(); min_r.setRange(1, 50); min_r.setValue(2.0)
    kurt  = QDoubleSpinBox(); kurt.setRange(-10, 0); kurt.setValue(-3.0)
    conn_sp = QSpinBox(); conn_sp.setRange(1, 3); conn_sp.setValue(1)
    conn_sp.setToolTip(
        "3D connectivity for merging detections across Z:\n"
        "1 = face-connected (6-neighbour, conservative, recommended)\n"
        "2 = edge-connected (18-neighbour)\n"
        "3 = corner-connected (26-neighbour, most permissive)"
    )
    form.addRow("Min spot radius (px):", min_r)
    form.addRow("Kurtosis threshold:", kurt)
    form.addRow("Z-link connectivity:", conn_sp)

    prog, run = _run_btn(form, "\u25b6  Segment Condensates (3D)")

    def _on_run():
        from pycat.toolbox.zstack_segmentation_tools import segment_subcellular_objects_3d
        try:
            raw   = ui._vol(raw_dd)
            proc  = ui._vol(proc_dd)
            cells = np.asarray(ui.viewer.layers[cell_dd.currentText()].data)
        except KeyError as e:
            napari_show_warning(str(e)); return
        if raw.ndim != 3:
            napari_show_warning("Needs 3D (Z,H,W) volumes."); return

        # Broadcast a 2D cell mask across Z if needed
        if cells.ndim == 2:
            cells = np.broadcast_to(cells, raw.shape)

        ball = ball_sp2.value()
        prog.setRange(0, raw.shape[0]); prog.setValue(0); prog.setVisible(True)
        run.setEnabled(False)

        def _task():
            cell_labels = np.unique(cells)
            cell_labels = cell_labels[cell_labels != 0]
            total_refined = np.zeros(raw.shape, dtype=bool)
            for cl in cell_labels:
                cmask3d = (cells == cl)
                refined, _ = segment_subcellular_objects_3d(
                    raw, proc, cmask3d, int(cl), ball,
                    kurtosis_threshold=kurt.value(),
                    min_spot_radius=min_r.value(),
                    connectivity=conn_sp.value(),
                    progress_callback=lambda z, n: prog.setValue(z),
                )
                total_refined |= refined
            labeled_3d = sk_label(total_refined, conn_sp.value())
            return labeled_3d

        worker = _ZWorker(_task)
        ui._zstack_cond_worker = worker

        def _done(labeled_3d):
            prog.setVisible(False); run.setEnabled(True)
            n = int(labeled_3d.max())
            name = f"3D Condensate Mask ({n} objects)"
            ui.viewer.add_labels(labeled_3d, name=name)
            ui._dr()['zstack_condensate_mask'] = labeled_3d
            ui._record('zstack_condensate_segmentation', {
                'raw_layer': raw_dd.currentText(), 'proc_layer': proc_dd.currentText(),
                'cell_layer': cell_dd.currentText(), 'ball_radius': ball_sp2.value(),
                'min_spot_radius': min_r.value(), 'kurtosis': kurt.value(),
                'connectivity': conn_sp.value(),
            })
            napari_show_info(f"3D condensate segmentation: {n} objects detected.")

        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("3D condensate segmentation error \u2014 see terminal.")
            print(f"[PyCAT ZStack Cond] {msg}")

        worker.finished.connect(_done); worker.error.connect(_err); worker.start()

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def sk_label(mask, connectivity):
    import skimage as sk
    return sk.measure.label(mask, connectivity=connectivity).astype(np.int32)


def _add_zstack_metrics(ui, layout):
    grp  = QGroupBox("Step 5 \u2014 3D Metrics")
    form = QFormLayout(grp)

    mask_dd = ui.create_layer_dropdown(napari.layers.Labels)
    int_dd  = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow("3D condensate mask:", mask_dd)
    form.addRow("Intensity volume:", int_dd)

    z_step_sp = QDoubleSpinBox(); z_step_sp.setRange(0.01, 50); z_step_sp.setValue(1.0)
    z_step_sp.setToolTip(
        "Z-step size in \u00b5m from the acquisition metadata. Voxels are "
        "almost always anisotropic (Z-step \u2260 XY pixel size) \u2014 check "
        "your acquisition settings rather than assuming isotropy."
    )
    form.addRow("Z-step (\u00b5m):", z_step_sp)

    run = QPushButton("\u25b6  Compute 3D Metrics")
    form.addRow(run)

    def _on_run():
        from pycat.toolbox.zstack_segmentation_tools import condensate_metrics_3d
        try:
            mask = np.asarray(ui.viewer.layers[mask_dd.currentText()].data)
            intensity = ui._vol(int_dd)
        except KeyError as e:
            napari_show_warning(str(e)); return

        df = condensate_metrics_3d(mask, intensity, ui._mpx(), z_step_sp.value())
        ui._dr()['zstack_condensate_df'] = df
        _show("3D Condensate Metrics", [("Per-condensate 3D metrics", df)])
        if len(df):
            napari_show_info(
                f"3D metrics: {len(df)} condensates, "
                f"mean volume={df['volume_um3'].mean():.3f}\u00b5m\u00b3, "
                f"mean sphericity={df['sphericity'].mean():.2f}"
            )
        else:
            napari_show_warning("No condensates found in the mask.")

    run.clicked.connect(_on_run)
    layout.addWidget(grp)
