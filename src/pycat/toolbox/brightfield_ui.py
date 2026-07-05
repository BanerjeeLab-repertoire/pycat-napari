"""
PyCAT Brightfield Condensate UI
=================================
Complete self-contained pipeline dock for brightfield condensate analysis.

Pipeline
--------
  Step 1 — Open image (via File menu — standard loader)
  Step 2 — Preprocess       : flat-field, BG subtract, halo correction, CLAHE
  Step 3 — Cell segmentation: Cellpose on BF image (phase/DIC model) — optional
  Step 4 — Segment spots    : dark-blob segmentation on enhanced image
  Step 5 — OD metrics       : optical density + per-condensate morphology
  Step 6 — Per-cell summary : n_condensates, coverage, mean OD, mean CNR
  Step 7 — Spatial metrics  : NND, Ripley's L, PCF (reuses spatial_metrology_tools)
  Step 8 — Tracking/dynamics: trajectory linking, MSD, coarsening (reuse existing)
  Step 9 — Texture          : OD entropy, kurtosis, skewness
  Step 10 — Frame QC        : focus/drift detection for time-series (BF-specific)
  Step 11 — Save & clear    : standard save

Key design principle:
    Steps 1-6 are BF-specific.
    Steps 7-11 directly call the existing fluorescence-toolkit functions —
    they operate on (masks, centroids, DataFrames), not on image intensity,
    so they work identically for brightfield data.
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
    QVBoxLayout, QWidget, QPushButton, QGroupBox,
    QFormLayout, QCheckBox, QSpinBox, QDoubleSpinBox, QLabel,
    QProgressBar, QScrollArea, QSizePolicy,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
try:
    from pycat.ui.field_status import label_with_circle
except Exception:
    label_with_circle = lambda t,**k: t


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _BFWorker(QThread):
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, fn, kwargs=None):
        super().__init__()
        self._fn = fn
        self._kw = kwargs or {}

    def run(self):
        try:
            self.finished.emit(self._fn(**self._kw))
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


# ---------------------------------------------------------------------------
# Main pipeline class
# ---------------------------------------------------------------------------

class BrightfieldCondensateUI:
    """
    Brightfield condensate analysis dock.
    Instantiated by the analysis mode switcher; call setup_ui() to build the dock.
    """
    def __init__(self, viewer, central_manager):
        self.viewer          = viewer
        self.central_manager = central_manager

    # ── helpers ──────────────────────────────────────────────────────────

    def _record(self, step, params):
        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record(step, params)

    def create_layer_dropdown(self, layer_type):
        return self.central_manager.toolbox_functions_ui.create_layer_dropdown(
            layer_type)

    def _add_label(self, layout, text, bold=False):
        lbl = QLabel(f"<b>{text}</b>" if bold else text)
        lbl.setWordWrap(True)

        lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        layout.addWidget(lbl)

    def _get_image(self, dropdown):
        arr = np.asarray(
            self.viewer.layers[dropdown.currentText()].data
        ).astype(np.float32)
        mn, mx = arr.min(), arr.max()
        return (arr - mn) / (mx - mn + 1e-8) if mx > mn else arr

    def _dr(self):
        return self.central_manager.active_data_class.data_repository

    def _mpx(self):
        return float(self._dr().get('microns_per_pixel_sq', 1.0)) ** 0.5

    # ── dock construction ────────────────────────────────────────────────

    def setup_ui(self):
        # Activate the workflow checklist for this pipeline
        try:
            self.central_manager.workflow_checklist.activate('cellular_bf')
            bp = getattr(self.central_manager, '_pycat_batch_processor', None)
            if bp:
                for step in bp.config.get('steps', []):
                    self.central_manager.workflow_checklist.on_step_recorded(
                        step['step'])
        except Exception:
            pass

        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(4, 20, 4, 4)

        header = QLabel(
            "<b>Brightfield Condensate Analysis</b><br>"
            "<span style='color:#888;font-size:9pt;'>"
            "Open your brightfield image via File → Open Image first.</span>"
        )
        header.setWordWrap(True)

        header.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        header.setStyleSheet("padding:6px; background:#2a2a2a; border-radius:4px;")
        layout.addWidget(header)

        # ── Step 1: load (status marker + load instruction) ────────────────
        try:
            from pycat.ui.field_status import add_step1_file_io, add_pixel_size_gate
            add_step1_file_io(
                self.viewer, layout,
                instruction_html=(
                    "Open an image via <b>Open/Save File(s)</b>, or drag one "
                    "onto the canvas."))
            self._pixel_gate_refresh = add_pixel_size_gate(
                layout,
                lambda: self.central_manager.active_data_class.data_repository,
                central_manager=self.central_manager)
        except Exception:
            pass

        _add_bf_preprocessing(self, layout)
        _add_bf_cell_segmentation(self, layout)
        _add_bf_condensate_segmentation(self, layout)
        _add_bf_od_metrics(self, layout)
        _add_bf_per_cell_summary(self, layout)
        _add_bf_spatial(self, layout)
        _add_bf_dynamics(self, layout)
        _add_bf_texture(self, layout)
        _add_bf_frame_qc(self, layout)

        main_w = QWidget()
        main_w.setLayout(layout)
        main_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        from pycat.ui.ui_modules import _apply_scroll_guard
        _apply_scroll_guard(main_w)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_w.setMinimumWidth(0)
        scroll.setWidget(main_w)

        self.viewer.window.add_dock_widget(
            scroll, name="Brightfield Condensate Analysis"
        )


# ---------------------------------------------------------------------------
# Step 2 — Preprocessing
# ---------------------------------------------------------------------------

def _add_bf_preprocessing(ui, layout):
    grp  = QGroupBox("Step 2 — Preprocess Brightfield Image")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)

    img_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow(label_with_circle("Brightfield image:", dropdown=img_dd), img_dd)

    ref_cb = QCheckBox("Use flat-field reference layer")
    ref_cb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    ref_cb.setChecked(False)
    ref_dd = ui.create_layer_dropdown(napari.layers.Image)
    ref_dd.setEnabled(False)
    ref_cb.stateChanged.connect(lambda s: ref_dd.setEnabled(bool(s)))
    form.addRow(ref_cb)
    form.addRow("  Flat-field reference:", ref_dd)

    bg_spin   = QSpinBox()
    bg_spin.setRange(10, 300); bg_spin.setValue(50)
    bg_spin.setToolTip(
        "Background kernel size in pixels.\n"
        "Must be >> condensate diameter (≥3-5× largest expected spot).\n"
        "Larger values preserve more background variation."
    )
    halo_spin = QDoubleSpinBox()
    halo_spin.setRange(0, 0.8); halo_spin.setValue(0.35); halo_spin.setSingleStep(0.05)
    halo_spin.setToolTip(
        "Halo suppression strength.\n"
        "Brightfield condensates have a bright ring artefact caused by\n"
        "light diffraction at the spot boundary. This subtracts a smoothed\n"
        "copy of the signal to remove the halo.\n"
        "0 = no suppression, 0.35 = moderate (recommended), 0.6 = aggressive."
    )
    form.addRow("BG kernel (px):", bg_spin)
    form.addRow("Halo suppression:", halo_spin)

    prog = QProgressBar(); prog.setVisible(False)
    run  = QPushButton("▶  Preprocess")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(prog); from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run))

    def _on_run():
        from pycat.toolbox.brightfield_tools import preprocess_brightfield
        try:
            layer = ui.viewer.layers[img_dd.currentText()]
            img   = ui._get_image(img_dd)
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return

        ref = None
        if ref_cb.isChecked():
            try:
                ref = ui._get_image(ref_dd)
            except Exception:
                napari_show_warning("Could not load flat-field reference — proceeding without.")

        prog.setRange(0, 0); prog.setVisible(True); run.setEnabled(False)

        def _task():
            return preprocess_brightfield(img, bg_kernel=bg_spin.value(),
                                           halo_weight=halo_spin.value(),
                                           background_image=ref)
        worker = _BFWorker(_task)
        ui._bf_preproc_worker = worker

        def _done(res):
            prog.setVisible(False); run.setEnabled(True)
            base = layer.name
            ui.viewer.add_image(res['bg_subtracted'],
                                 name=f"BF BG-Subtracted [{base}]",
                                 colormap='gray_r')
            ui.viewer.add_image(res['enhanced'],
                                 name=f"BF Enhanced [{base}]",
                                 colormap='viridis')
            dr = ui._dr()
            dr['bf_enhanced']      = res['enhanced']
            dr['bf_bg_subtracted'] = res['bg_subtracted']
            dr['bf_source_image']  = img
            ui._record('bf_preprocess', {
                'image_layer': img_dd.currentText(),
                'bg_kernel':   bg_spin.value(),
                'halo_weight': halo_spin.value(),
            })
            napari_show_info("BF preprocessing done — 'BF Enhanced' layer ready for segmentation.")

        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("BF preprocessing error — see terminal.")
            print(f"[PyCAT BF Preprocess] {msg}")

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.start()

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


# ---------------------------------------------------------------------------
# Step 3 — Cell segmentation (optional)
# ---------------------------------------------------------------------------

def _add_bf_cell_segmentation(ui, layout):
    grp  = QGroupBox("Step 3 — Cell Segmentation (optional)")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)

    note = QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Segment cell bodies to restrict condensate analysis per-cell.\n"
        "Cellpose works on brightfield using the 'phase3' or 'brightfield' model.\n"
        "Skip if working with the whole image field.</span>"
    )
    note.setWordWrap(True)

    note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
    form.addRow(note)

    img_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow(label_with_circle("BF image for segmentation:", dropdown=img_dd), img_dd)

    diam_spin = QSpinBox(); diam_spin.setRange(10, 500); diam_spin.setValue(80)
    diam_spin.setToolTip("Approximate cell diameter in pixels.")
    form.addRow("Cell diameter (px):", diam_spin)

    prog = QProgressBar(); prog.setVisible(False)
    run  = QPushButton("▶  Segment Cells (Cellpose BF)")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(prog); from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run, optional=True))

    def _on_run():
        try:
            img = ui._get_image(img_dd)
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return
        prog.setRange(0, 0); prog.setVisible(True); run.setEnabled(False)

        def _task():
            from cellpose import models
            # phase3 model is trained on phase contrast / brightfield
            try:
                model = models.CellposeModel(pretrained_model='brightfield')
            except Exception:
                # Fall back to cyto2 if brightfield model not available
                model = models.CellposeModel(pretrained_model='cyto2')
            masks, _, _ = model.eval(img, diameter=diam_spin.value(),
                                      channels=[0, 0])
            return masks.astype(np.int32)

        worker = _BFWorker(_task)
        ui._bf_cell_worker = worker

        def _done(masks):
            prog.setVisible(False); run.setEnabled(True)
            n = int(masks.max())
            ui.viewer.add_labels(masks, name=f"BF Cell Mask ({n} cells)")
            ui._dr()['bf_cell_mask'] = masks
            ui._dr()['cell_diameter'] = diam_spin.value()
            ui._record('bf_cell_segmentation', {
                'image_layer': img_dd.currentText(),
                'cell_diameter': diam_spin.value(),
            })
            napari_show_info(f"BF cell segmentation: {n} cells found.")

        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("BF cell segmentation error — see terminal.")
            print(f"[PyCAT BF CellSeg] {msg}")

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.start()

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


# ---------------------------------------------------------------------------
# Step 4 — Condensate segmentation
# ---------------------------------------------------------------------------

def _add_bf_condensate_segmentation(ui, layout):
    grp  = QGroupBox("Step 4 — Segment Condensate Spots")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)

    enh_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow(label_with_circle("Enhanced BF image:", dropdown=enh_dd), enh_dd)

    min_d = QDoubleSpinBox(); min_d.setRange(1, 50);  min_d.setValue(3.0)
    max_d = QDoubleSpinBox(); max_d.setRange(5, 500); max_d.setValue(50.0)
    circ  = QDoubleSpinBox(); circ.setRange(0.1, 1.0); circ.setValue(0.5)
    min_d.setToolTip("Minimum condensate diameter in pixels.")
    max_d.setToolTip("Maximum condensate diameter in pixels.")
    circ.setToolTip(
        "Minimum circularity (4πA/P²). 1.0 = perfect circle.\n"
        "0.5 accepts moderately elongated objects.\n"
        "Increase to reject non-circular debris."
    )
    form.addRow("Min diameter (px):", min_d)
    form.addRow("Max diameter (px):", max_d)
    form.addRow("Min circularity:", circ)

    prog = QProgressBar(); prog.setVisible(False)
    run  = QPushButton("▶  Segment Condensates")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(prog); from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run))

    def _on_run():
        from pycat.toolbox.brightfield_tools import segment_bf_condensates
        try:
            enh = ui._get_image(enh_dd)
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return
        prog.setRange(0, 0); prog.setVisible(True); run.setEnabled(False)

        def _task():
            return segment_bf_condensates(enh,
                                           min_diameter_px=min_d.value(),
                                           max_diameter_px=max_d.value(),
                                           min_circularity=circ.value())
        worker = _BFWorker(_task)
        ui._bf_seg_worker = worker

        def _done(labeled):
            prog.setVisible(False); run.setEnabled(True)
            n = int(labeled.max())
            ui.viewer.add_labels(labeled, name=f"BF Condensate Mask ({n} spots)")
            ui._dr()['bf_condensate_mask'] = labeled
            ui._record('bf_condensate_segmentation', {
                'enhanced_layer':  enh_dd.currentText(),
                'min_diameter_px': min_d.value(),
                'max_diameter_px': max_d.value(),
                'min_circularity': circ.value(),
            })
            napari_show_info(f"BF condensate segmentation: {n} spots detected.")

        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("BF segmentation error — see terminal.")
            print(f"[PyCAT BF Seg] {msg}")

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.start()

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


# ---------------------------------------------------------------------------
# Step 5 — Optical density and per-condensate metrics
# ---------------------------------------------------------------------------

def _add_bf_od_metrics(ui, layout):
    grp  = QGroupBox("Step 5 — OD & Per-Condensate Metrics")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)

    note = QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "OD = −log₁₀(I/I_background). Proportional to condensate\n"
        "concentration × optical path length. BF equivalent of intensity.</span>"
    )
    note.setWordWrap(True); form.addRow(note)

    raw_dd  = ui.create_layer_dropdown(napari.layers.Image)
    mask_dd = ui.create_layer_dropdown(napari.layers.Labels)
    cell_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow("Raw BF image:", raw_dd)
    form.addRow("Condensate mask:", mask_dd)
    form.addRow("Cell mask (optional):", cell_dd)

    prog = QProgressBar(); prog.setVisible(False)
    run  = QPushButton("▶  Compute OD Metrics")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(prog); from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run))

    def _on_run():
        from pycat.toolbox.brightfield_tools import bf_condensate_metrics
        try:
            raw  = ui._get_image(raw_dd)
            mask = np.asarray(ui.viewer.layers[mask_dd.currentText()].data)
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return

        try:
            cells = np.asarray(ui.viewer.layers[cell_dd.currentText()].data)
        except Exception:
            cells = None

        prog.setRange(0, 0); prog.setVisible(True); run.setEnabled(False)

        def _task():
            return bf_condensate_metrics(raw, mask, cells, ui._mpx())

        worker = _BFWorker(_task)
        ui._bf_od_worker = worker

        def _done(df):
            prog.setVisible(False); run.setEnabled(True)
            ui._dr()['bf_condensate_df'] = df
            from pycat.ui.ui_utils import show_dataframes_dialog
            show_dataframes_dialog("BF Condensate Metrics",
                                    [("Per-condensate OD metrics", df.round(4))])
            napari_show_info(
                f"OD metrics: {len(df)} condensates — "
                f"mean OD={df['mean_od'].mean():.3f}, "
                f"mean CNR={df['cnr'].mean():.1f}, "
                f"mean partition={df['od_partition_coeff'].mean():.2f}"
            )

        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("OD metrics error — see terminal.")
            print(f"[PyCAT BF OD] {msg}")

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.start()

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


# ---------------------------------------------------------------------------
# Step 6 — Per-cell summary
# ---------------------------------------------------------------------------

def _add_bf_per_cell_summary(ui, layout):
    grp  = QGroupBox("Step 6 — Per-Cell Summary")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Aggregates per-condensate metrics to per-cell statistics.\n"
        "Requires Step 5 and a cell mask.</span>"
    ))

    cell_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow(label_with_circle("Cell mask:", dropdown=cell_dd), cell_dd)
    run = QPushButton("▶  Summarise Per Cell")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run))

    def _on_run():
        from pycat.toolbox.brightfield_tools import bf_per_cell_summary
        cond_df = ui._dr().get('bf_condensate_df')
        if cond_df is None or cond_df.empty:
            napari_show_warning("Run Step 5 (OD Metrics) first."); return
        try:
            cells = np.asarray(ui.viewer.layers[cell_dd.currentText()].data)
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return

        df = bf_per_cell_summary(cond_df, cells, ui._mpx())
        ui._dr()['bf_per_cell_df'] = df
        from pycat.ui.ui_utils import show_dataframes_dialog
        show_dataframes_dialog("BF Per-Cell Summary", [("Per-cell metrics", df.round(4))])
        napari_show_info(
            f"Per-cell summary: {len(df)} cells — "
            f"mean condensates/cell={df['n_condensates'].mean():.1f}, "
            f"mean coverage={df['condensate_coverage_fraction'].mean():.3f}"
        )

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


# ---------------------------------------------------------------------------
# Step 7 — Spatial metrology (reuses existing toolkit)
# ---------------------------------------------------------------------------

def _add_bf_spatial(ui, layout):
    grp  = QGroupBox("Step 7 — Spatial Metrology")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "NND, Ripley's L, PCF, Voronoi, convex hull, etc.\n"
        "Operates on condensate centroids — identical to fluorescence.</span>"
    ))

    mask_dd = ui.create_layer_dropdown(napari.layers.Labels)
    cell_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow("Condensate mask:", mask_dd)
    form.addRow("Cell mask:", cell_dd)

    run = QPushButton("▶  Run Spatial Metrology")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run, optional=True))

    def _on_run():
        from pycat.toolbox.spatial_metrology_tools import (
            get_puncta_centroids, run_all_spatial_metrics)
        from pycat.toolbox.spatial_metrology_ui import _results_to_dataframes
        try:
            mask  = np.asarray(ui.viewer.layers[mask_dd.currentText()].data)
            cells = np.asarray(ui.viewer.layers[cell_dd.currentText()].data)
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return

        mpx = ui._mpx()

        def _task():
            # Extract per-cell centroids, then run all metrics for each cell —
            # run_all_spatial_metrics takes a single cell's (N,2) coords array
            # and boolean mask, not a labeled mask directly.
            coords_df = get_puncta_centroids(mask, cells, mpx)
            if coords_df.empty:
                return {}
            results = {}
            for cell_lbl in [c for c in coords_df['cell_label'].unique() if c != 0]:
                sub    = coords_df[coords_df['cell_label'] == cell_lbl]
                coords = sub[['y_um', 'x_um']].values
                if len(coords) < 2:
                    continue
                cmask  = (cells == cell_lbl)
                results[cell_lbl] = run_all_spatial_metrics(coords, cmask, mpx)
            return results

        worker = _BFWorker(_task)
        ui._bf_spatial_worker = worker

        def _done(results):
            if not results:
                napari_show_warning("No condensates with ≥2 objects per cell — "
                                     "spatial metrics need at least 2 points per group.")
                return
            dfs = _results_to_dataframes(results)
            from pycat.ui.ui_utils import show_dataframes_dialog
            show_dataframes_dialog("BF Spatial Metrology",
                                    [(k, v.round(4)) for k, v in dfs.items()])
            napari_show_info("BF spatial metrology complete.")

        def _err(msg):
            napari_show_warning("Spatial metrology error — see terminal.")
            print(f"[PyCAT BF Spatial] {msg}")

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.start()

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


# ---------------------------------------------------------------------------
# Step 8 — Tracking & dynamics (reuses existing toolkit)
# ---------------------------------------------------------------------------

def _add_bf_dynamics(ui, layout):
    grp  = QGroupBox("Step 8 — Tracking & Dynamics (time-series)")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Trajectory linking, MSD, coarsening, merge/fission.\n"
        "Operates on centroids from a (T,H,W) condensate mask stack.</span>"
    ))

    stack_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow(label_with_circle("Condensate mask stack (T,H,W):", dropdown=stack_dd), stack_dd)

    dt_spin   = QDoubleSpinBox(); dt_spin.setRange(0.01, 3600); dt_spin.setValue(1.0)
    disp_spin = QDoubleSpinBox(); disp_spin.setRange(0.1, 20);  disp_spin.setValue(2.0)
    form.addRow("Frame interval (s):", dt_spin)
    form.addRow("Max displacement (µm):", disp_spin)

    cb_msd     = QCheckBox("MSD / anomalous diffusion"); cb_msd.setChecked(True)
    cb_msd.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    cb_coarse  = QCheckBox("Coarsening kinetics R(t)");   cb_coarse.setChecked(True)
    cb_km      = QCheckBox("Kaplan-Meier lifetime");      cb_km.setChecked(True)
    cb_mf      = QCheckBox("Merge / fission detection");  cb_mf.setChecked(True)
    cb_mf.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(cb_msd); form.addRow(cb_coarse); form.addRow(cb_km); form.addRow(cb_mf)

    prog = QProgressBar(); prog.setVisible(False)
    run  = QPushButton("▶  Run Dynamics")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(prog); from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run, optional=True))

    def _on_run():
        from pycat.toolbox.dynamic_spatial_tools import (
            extract_frame_properties, link_trajectories_bayesian,
            trajectory_metrics, detect_merge_fission, cluster_lifetime_analysis,
        )
        from pycat.toolbox.condensate_physics_tools import (
            compute_msd, fit_anomalous_diffusion, msd_per_track,
            fit_coarsening, kaplan_meier_lifetimes,
        )
        try:
            stack = np.asarray(ui.viewer.layers[stack_dd.currentText()].data)
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return
        if stack.ndim != 3:
            napari_show_warning("Dynamics needs a 3D (T,H,W) label stack."); return

        n_frames = stack.shape[0]
        mpx = ui._mpx()
        dt  = dt_spin.value()
        prog.setRange(0, 0); prog.setVisible(True); run.setEnabled(False)

        do_msd, do_coarse, do_km, do_mf = (
            cb_msd.isChecked(), cb_coarse.isChecked(),
            cb_km.isChecked(), cb_mf.isChecked())

        def _task():
            props  = extract_frame_properties(stack, mpx)
            tracks = link_trajectories_bayesian(
                props, max_displacement_um=disp_spin.value())
            res = {'tracks': tracks, 'props': props}

            if do_msd:
                msd_df = compute_msd(tracks, frame_interval_s=dt)
                res['msd']     = msd_df
                res['msd_fit'] = fit_anomalous_diffusion(msd_df)
                res['msd_pt']  = msd_per_track(tracks, dt)

            if do_coarse:
                # Mean radius vs time from condensate areas
                area_per_frame = props.groupby('frame')['area_um2'].mean()
                r_mean = np.sqrt(area_per_frame.values / np.pi)
                t_arr  = area_per_frame.index.values * dt
                res['coarsening'] = fit_coarsening(t_arr, r_mean)

            if do_km:
                res['km'] = kaplan_meier_lifetimes(tracks, n_frames)

            if do_mf:
                res['merge_fission'] = detect_merge_fission(stack, mpx)

            return res

        worker = _BFWorker(_task)
        ui._bf_dyn_worker = worker

        def _done(res):
            prog.setVisible(False); run.setEnabled(True)
            dr = ui._dr()
            dr['bf_trajectories'] = res['tracks']
            tables = [("Trajectories", res['tracks'].round(4))]

            if 'msd' in res:
                dr['bf_msd'] = res['msd']
                fit = res['msd_fit']
                fit_df = pd.DataFrame([{k: v for k, v in fit.items()
                                        if not hasattr(v, '__len__')}])
                tables += [("MSD vs lag", res['msd'].round(4)),
                            ("Anomalous diffusion fit", fit_df.round(4)),
                            ("Per-track D and α", res['msd_pt'].round(4))]
                napari_show_info(
                    f"BF MSD: D={fit.get('D_um2_per_s',np.nan):.4f} µm²/s, "
                    f"α={fit.get('alpha',np.nan):.3f} ({fit.get('motion_type','?')})"
                )

            if 'coarsening' in res:
                co = res['coarsening']
                co_df = pd.DataFrame([{k: v for k, v in co.items()
                                       if not hasattr(v, '__len__')}])
                tables.append(("Coarsening kinetics", co_df.round(4)))
                napari_show_info(f"Coarsening: {co.get('preferred_mechanism','?')}")

            if 'km' in res:
                dr['bf_km'] = res['km']
                tables.append(("KM survival", res['km'].round(4)))

            if 'merge_fission' in res and not res['merge_fission'].empty:
                tables.append(("Merge/fission events", res['merge_fission'].round(4)))

            from pycat.ui.ui_utils import show_dataframes_dialog
            show_dataframes_dialog("BF Dynamics", tables)

        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("BF dynamics error — see terminal.")
            print(f"[PyCAT BF Dynamics] {msg}")

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.start()

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


# ---------------------------------------------------------------------------
# Step 9 — Texture on OD image
# ---------------------------------------------------------------------------

def _add_bf_texture(ui, layout):
    grp  = QGroupBox("Step 9 — OD Texture Features")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Entropy, kurtosis, skewness of the optical density distribution.\n"
        "Measures internal heterogeneity of the condensate phase.</span>"
    ))

    od_dd   = ui.create_layer_dropdown(napari.layers.Image)
    mask_dd = ui.create_layer_dropdown(napari.layers.Labels)
    cell_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow("OD image (or BF Enhanced):", od_dd)
    form.addRow("Condensate mask:", mask_dd)
    form.addRow("Cell mask (optional):", cell_dd)

    run = QPushButton("▶  Compute OD Texture")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run, optional=True))

    def _on_run():
        from pycat.toolbox.brightfield_tools import (
            bf_texture_features, compute_optical_density)
        try:
            raw_img = ui._get_image(od_dd)
            mask    = np.asarray(ui.viewer.layers[mask_dd.currentText()].data)
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return
        try:
            cells = np.asarray(ui.viewer.layers[cell_dd.currentText()].data)
        except Exception:
            cells = None

        # Compute OD from the selected image
        od = compute_optical_density(raw_img)

        df = bf_texture_features(od, mask, cells)
        ui._dr()['bf_texture_df'] = df
        from pycat.ui.ui_utils import show_dataframes_dialog
        show_dataframes_dialog("BF OD Texture",
                                [("OD texture features", df.round(4))])
        napari_show_info(f"OD texture: {len(df)} regions analysed.")

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


# ---------------------------------------------------------------------------
# Step 10 — Frame quality (BF-specific: no bleaching)
# ---------------------------------------------------------------------------

def _add_bf_frame_qc(ui, layout):
    grp  = QGroupBox("Step 10 — Frame Quality (time-series)")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Focus and drift assessment for BF stacks.\n"
        "Uses Brenner gradient, Tenengrad, and normalised variance.\n"
        "No bleaching correction (no fluorophore).</span>"
    ))

    stack_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow(label_with_circle("BF stack (T,H,W):", dropdown=stack_dd), stack_dd)

    dt_spin  = QDoubleSpinBox(); dt_spin.setRange(0.01, 3600); dt_spin.setValue(1.0)
    thr_spin = QDoubleSpinBox(); thr_spin.setRange(0.1, 0.9);  thr_spin.setValue(0.4)
    thr_spin.setToolTip(
        "Frames with a focus_score below this fraction of the median\n"
        "are flagged as defocused. 0.4 is a good starting point."
    )
    form.addRow("Frame interval (s):", dt_spin)
    form.addRow("Defocus threshold:", thr_spin)

    prog = QProgressBar(); prog.setVisible(False)
    run  = QPushButton("▶  Assess Frame Quality")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(prog); from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run, optional=True))

    def _on_run():
        from pycat.toolbox.brightfield_tools import bf_analyse_frame_quality
        try:
            stack = np.asarray(
                ui.viewer.layers[stack_dd.currentText()].data
            ).astype(np.float32)
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return
        if stack.ndim != 3:
            napari_show_warning("Frame QC needs a 3D (T,H,W) stack."); return

        prog.setRange(0, 0); prog.setVisible(True); run.setEnabled(False)

        def _task():
            # Normalise stack
            mn, mx = stack.min(), stack.max()
            s = (stack - mn) / (mx - mn + 1e-8) if mx > mn else stack
            return bf_analyse_frame_quality(s, dt_spin.value(), thr_spin.value())

        worker = _BFWorker(_task)
        ui._bf_qc_worker = worker

        def _done(result):
            prog.setVisible(False); run.setEnabled(True)
            df   = result['per_frame_df']
            summ = result['summary']
            ui._dr()['bf_frame_quality'] = df
            n_def = summ['n_defocused_frames']
            from pycat.ui.ui_utils import show_dataframes_dialog
            summ_df = pd.DataFrame([{k: v for k, v in summ.items()
                                     if k != 'recommendation'}]).round(4)
            show_dataframes_dialog("BF Frame Quality",
                                    [("Summary", summ_df),
                                     ("Per-frame metrics", df.round(4))])
            if n_def > 0:
                bad = df[df['is_defocused']]['frame'].tolist()
                napari_show_warning(
                    f"BF QC: {n_def} defocused frame(s): {bad}. "
                    f"{summ['recommendation']}"
                )
            else:
                napari_show_info(f"BF Frame QC: {summ['dominant_cause']}. "
                                  f"{summ['recommendation']}")

        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("Frame QC error — see terminal.")
            print(f"[PyCAT BF QC] {msg}")

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.start()

    run.clicked.connect(_on_run)
    layout.addWidget(grp)
