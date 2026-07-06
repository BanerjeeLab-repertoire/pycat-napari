"""
PyCAT In Vitro Fluorescence Condensate UI
===========================================
Self-contained pipeline for fluorescence images of in vitro LLPS droplet
assays — protein/RNA droplets on a coverslip without cells.

Pipeline
--------
  Step 1 — Open image (via File menu)
  Step 2 — Preprocess       : rolling ball BG, CLAHE (same as cellular)
  Step 3 — Segment droplets : segment_subcellular_objects on whole field
                              (no cell mask — whole image is the sample)
  Step 4 — Field summary    : volume fraction Φ, partition coefficient,
                              bulk concentration (C_sat proxy), number density
  Step 5 — Size distribution: lognormal / power-law fit
  Step 6 — Spatial metrology: NND, Ripley etc (reuse existing)
  Step 7 — Dynamics         : tracking, MSD, coarsening, fusion fitting
  Step 8 — Phase diagram    : C_sat estimation from dilution series
  Step 9 — Frame QC         : bleaching + focus (analyse_frame_quality)

Key differences from Cellular Condensate Analysis:
  - No cell segmentation step
  - No per-cell summary — whole-field statistics instead
  - Partition coefficient = droplet / bulk buffer (no cell background)
  - Phase diagram / C_sat tools available (unique to in vitro)
  - Fusion relaxation fitting is primary biophysics output
  - Sedimentation detection for time-series
"""
from __future__ import annotations
try:
    from pycat.ui.field_status import label_with_circle
except Exception:
    label_with_circle = lambda t, **k: t
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
    QScrollArea, QSizePolicy, QHBoxLayout, QTabWidget, QComboBox,
    QRadioButton, QButtonGroup, QStackedWidget,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal


class _IVFWorker(QThread):
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)
    def __init__(self, fn):
        super().__init__(); self._fn = fn
    def run(self):
        try:    self.finished.emit(self._fn())
        except Exception:
            import traceback; self.error.emit(traceback.format_exc())


class InVitroFluorUI:
    def __init__(self, viewer, central_manager):
        self.viewer          = viewer
        self.central_manager = central_manager

    def _dr(self):
        return self.central_manager.active_data_class.data_repository

    def _mpx(self):
        return float(self._dr().get('microns_per_pixel_sq', 1.0)) ** 0.5

    def _record(self, step, params):
        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp: bp.record(step, params)

    def create_layer_dropdown(self, layer_type):
        return self.central_manager.toolbox_functions_ui.create_layer_dropdown(
            layer_type)

    def _img(self, dd):
        arr = np.asarray(self.viewer.layers[dd.currentText()].data).astype(np.float32)
        mn, mx = arr.min(), arr.max()
        return (arr-mn)/(mx-mn+1e-8) if mx > mn else arr

    def setup_ui(self):
        try:
            self.central_manager.workflow_checklist.activate('invitro_fluor')
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
            "<b>In Vitro Fluorescence Condensate Analysis</b><br>"
            "<span style='color:#888;font-size:9pt;'>"
            "For protein/RNA LLPS droplets on coverslip — no cell segmentation needed.</span>"
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
                    "Load a fluorescence image via "
                    "<b>Open/Save File(s)</b>, or drag one onto the canvas."))
            # Pixel-size gate: shown only when metadata gave no scale; hides once set.
            self._pixel_gate_refresh = add_pixel_size_gate(
                layout,
                lambda: self.central_manager.active_data_class.data_repository,
                central_manager=self.central_manager)
        except Exception:
            pass

        _ivf_preprocessing(self, layout)
        _ivf_segmentation(self, layout)
        _ivf_field_summary(self, layout)
        _ivf_size_distribution(self, layout)
        _ivf_spatial(self, layout)
        _ivf_dynamics(self, layout)
        _ivf_phase_diagram(self, layout)
        _ivf_frame_qc(self, layout)

        # Steps 7 (Dynamics) and 9 (Frame Quality / bleaching) only apply to
        # time-series (2D+t) or 3D data — a single 2D droplet image has no
        # temporal dimension to coarsen or bleach. Show/hide them based on
        # whether any loaded Image layer is a stack (ndim >= 3), re-evaluated
        # whenever layers change.
        def _update_timeseries_steps(*_):
            try:
                has_stack = any(
                    getattr(l, 'data', None) is not None
                    and np.asarray(l.data).ndim >= 3
                    for l in self.viewer.layers
                    if isinstance(l, napari.layers.Image))
            except Exception:
                has_stack = True  # fail open — don't hide if uncertain
            for _attr in ('_ivf_dynamics_grp', '_ivf_qc_grp'):
                g = getattr(self, _attr, None)
                if g is not None:
                    g.setVisible(has_stack)
        self._ivf_update_ts_steps = _update_timeseries_steps
        try:
            self.viewer.layers.events.inserted.connect(_update_timeseries_steps)
            self.viewer.layers.events.removed.connect(_update_timeseries_steps)
        except Exception:
            pass
        _update_timeseries_steps()

        main_w = QWidget(); main_w.setLayout(layout)
        main_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        from pycat.ui.ui_modules import _apply_scroll_guard
        _apply_scroll_guard(main_w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); main_w.setMinimumWidth(0)
        try:
            from pycat.ui.ui_modules import _relax_min_widths, _apply_scroll_guard
            _relax_min_widths(main_w)      # let long buttons/labels shrink to dock width (fixes right-edge clipping)
            _apply_scroll_guard(main_w)    # scroll the panel, not the control under the cursor
        except Exception:
            pass
        scroll.setWidget(main_w)
        self.viewer.window.add_dock_widget(scroll, name="In Vitro Fluorescence Analysis")


# ─────────────────────────────────────────────────────────────────────────────

def _run_btn(form, label="▶  Run"):
    prog = QProgressBar(); prog.setVisible(False)
    btn  = QPushButton(label)
    form.addRow(prog); form.addRow(btn)
    return prog, btn


def _show(title, tables):
    from pycat.ui.ui_utils import show_dataframes_dialog
    show_dataframes_dialog(title, [(k, v.round(4) if hasattr(v,'round') else v)
                                   for k,v in tables])


def _ivf_preprocessing(ui, layout):
    grp = QGroupBox("Step 2 — Preprocess Fluorescence Image (optional)")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>Optional. In-vitro droplets on a "
        "clean field usually segment fine on the raw image — you can skip this "
        "step. Rolling-ball can hollow out large droplets; a gentle Gaussian blur "
        "or LoG edge-enhancement is usually a better choice if you preprocess.</span>"))
    img_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow(label_with_circle("Fluorescence image:", dropdown=img_dd), img_dd)

    method_dd = QComboBox()
    method_dd.addItems(["Gaussian blur (gentle denoise)",
                        "LoG edge enhancement",
                        "Rolling-ball background subtraction"])
    method_dd.setToolTip(
        "Gaussian = light smoothing, keeps droplet interiors solid.\n"
        "LoG = enhances droplet edges/blobs.\n"
        "Rolling-ball = legacy background subtraction (can hollow large droplets).")
    form.addRow("Method:", method_dd)

    # Gaussian sigma
    sigma_spin = QDoubleSpinBox(); sigma_spin.setRange(0.3, 20.0)
    sigma_spin.setSingleStep(0.5); sigma_spin.setValue(1.5); sigma_spin.setDecimals(2)
    sigma_spin.setToolTip("Gaussian/LoG sigma in pixels.")
    _sigma_row = form.rowCount()
    form.addRow("Sigma (px):", sigma_spin)

    # Rolling-ball radius (shown only for rolling-ball)
    ball_spin = QSpinBox(); ball_spin.setRange(2,200); ball_spin.setValue(15)
    ball_spin.setToolTip("Rolling ball radius (px). Smaller than cellular (15-30).")
    ball_lbl = QLabel("Rolling ball radius (px):")
    form.addRow(ball_lbl, ball_spin)

    def _on_method_change():
        is_ball = (method_dd.currentIndex() == 2)
        ball_spin.setVisible(is_ball); ball_lbl.setVisible(is_ball)
        sigma_spin.setVisible(not is_ball)
    method_dd.currentIndexChanged.connect(_on_method_change)
    _on_method_change()

    prog, run = _run_btn(form, "▶  Preprocess")

    def _on_run():
        try: img = ui._img(img_dd)
        except KeyError as e: napari_show_warning(str(e)); return
        idx = method_dd.currentIndex()
        sigma = sigma_spin.value(); ball = ball_spin.value()
        prog.setRange(0,0); prog.setVisible(True); run.setEnabled(False)

        def _task():
            arr = np.asarray(img).astype(np.float32)
            if idx == 0:   # Gaussian blur
                from pycat.toolbox.image_processing_tools import gaussian_smooth_2d
                return gaussian_smooth_2d(arr, sigma=sigma)
            if idx == 1:   # LoG enhancement
                from pycat.toolbox.image_processing_tools import apply_laplace_of_gauss_enhancement
                return np.asarray(apply_laplace_of_gauss_enhancement(arr, sigma=sigma)).astype(np.float32)
            # Rolling-ball (legacy)
            from pycat.toolbox.image_processing_tools import pre_process_image
            return np.asarray(pre_process_image(arr, ball_radius=ball, window_size=ball*2)).astype(np.float32)

        worker = _IVFWorker(_task)
        ui._ivf_pre_worker = worker
        def _done(proc):
            prog.setVisible(False); run.setEnabled(True)
            _mnames = ['gaussian', 'log', 'rolling-ball']
            ui.viewer.add_image(proc, name=f"IVF Preprocessed [{img_dd.currentText()}]",
                                 colormap='viridis')
            ui._dr()['ivf_preprocessed'] = proc
            ui._record('ivf_preprocess', {'image_layer': img_dd.currentText(),
                                          'method': _mnames[idx],
                                          'sigma': sigma, 'ball_radius': ball})
            napari_show_info(f"In vitro preprocessing done ({_mnames[idx]}).")
        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("Preprocessing error — see terminal."); print(f"[PyCAT IVF] {msg}")
        worker.finished.connect(_done); worker.error.connect(_err); worker.start()
    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _ivf_segmentation(ui, layout):
    grp  = QGroupBox("Step 3 — Segment Droplets")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)

    pre_dd  = ui.create_layer_dropdown(napari.layers.Image)
    raw_dd  = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow("Preprocessed image:", pre_dd)
    form.addRow("Raw fluorescence image:", raw_dd)

    # ── Method selector (radio buttons) ───────────────────────────────────
    # In-vitro droplets on a clean field segment well with a simple global
    # threshold, so Otsu is the default and the fiddly options are opt-in.
    method_box = QVBoxLayout()
    rb_otsu   = QRadioButton("Threshold (Otsu) — simplest, no parameters")
    rb_multi  = QRadioButton("Multi-level threshold (Multi-Otsu)")
    rb_sauv   = QRadioButton("Local threshold (Sauvola)")
    rb_rf     = QRadioButton("Random Forest (paint scribbles)")
    rb_adv    = QRadioButton("Advanced: spot detection (kurtosis / SNR)")
    rb_otsu.setChecked(True)
    bg = QButtonGroup(grp)
    for _rb in (rb_otsu, rb_multi, rb_sauv, rb_rf, rb_adv):
        bg.addButton(_rb); method_box.addWidget(_rb)
    _mw = QWidget(); _mw.setLayout(method_box)
    form.addRow("Segmentation method:", _mw)

    # ── Per-method parameter panels (only the active one is shown) ─────────
    stack = QStackedWidget()

    # Otsu: one OPTIONAL sensitivity nudge (default 1.0 = plain Otsu).
    otsu_w = QWidget(); otsu_f = QFormLayout(otsu_w)
    otsu_f.setContentsMargins(0,0,0,0)
    otsu_sens = QDoubleSpinBox(); otsu_sens.setRange(0.3, 3.0)
    otsu_sens.setSingleStep(0.05); otsu_sens.setValue(1.0); otsu_sens.setDecimals(2)
    otsu_sens.setToolTip("Multiplier on the Otsu threshold. 1.0 = standard Otsu. "
                         "<1 catches dimmer droplets; >1 is stricter.")
    otsu_f.addRow("Sensitivity (×Otsu):", otsu_sens)
    stack.addWidget(otsu_w)

    # Multi-Otsu: number of classes + which boundary to cut at.
    multi_w = QWidget(); multi_f = QFormLayout(multi_w)
    multi_f.setContentsMargins(0,0,0,0)
    multi_classes = QSpinBox(); multi_classes.setRange(2,5); multi_classes.setValue(3)
    multi_classes.setToolTip("Number of intensity classes to split the image into.")
    multi_level = QComboBox()
    multi_level.addItems(["Lower boundary (more inclusive)",
                          "Upper boundary (bright cores only)"])
    multi_level.setToolTip("Which class boundary becomes the foreground cutoff.")
    multi_f.addRow("Classes:", multi_classes)
    multi_f.addRow("Cut at:", multi_level)
    stack.addWidget(multi_w)

    # Sauvola: window + k (defaults chosen from real in-vitro data).
    sauv_w = QWidget(); sauv_f = QFormLayout(sauv_w)
    sauv_f.setContentsMargins(0,0,0,0)
    sauv_win = QSpinBox(); sauv_win.setRange(3,501); sauv_win.setSingleStep(2)
    sauv_win.setValue(35); sauv_win.setToolTip("Local window (px, forced odd).")
    sauv_k = QDoubleSpinBox(); sauv_k.setRange(-1.0,1.0); sauv_k.setSingleStep(0.05)
    sauv_k.setValue(0.0); sauv_k.setDecimals(3)
    sauv_k.setToolTip("Sauvola k: lower = more inclusive threshold.")
    sauv_f.addRow("Window size (px):", sauv_win)
    sauv_f.addRow("k:", sauv_k)
    stack.addWidget(sauv_w)

    # Random Forest: a Draw-Scribbles button that makes+arms the labels layer.
    rf_w = QWidget(); rf_f = QFormLayout(rf_w)
    rf_f.setContentsMargins(0,0,0,0)
    rf_scribble_btn = QPushButton("✏  Draw Scribbles")
    rf_scribble_btn.setToolTip(
        "Create/select a labels layer and switch to the paint tool. Paint "
        "label 1 over BACKGROUND and label 2 over DROPLETS, then "
        "press Segment Droplets.")
    rf_f.addRow(QLabel("Paint 1 = background, 2 = droplet:"))
    rf_f.addRow(rf_scribble_btn)
    stack.addWidget(rf_w)

    # Advanced spot detection: the original kurtosis/SNR/rolling-ball params.
    adv_w = QWidget(); adv_f = QFormLayout(adv_w)
    adv_f.setContentsMargins(0,0,0,0)
    min_r   = QDoubleSpinBox(); min_r.setRange(1,50);    min_r.setValue(2.0)
    kurt_sp = QDoubleSpinBox(); kurt_sp.setRange(-10,0); kurt_sp.setValue(-3.0)
    lsnr_sp = QDoubleSpinBox(); lsnr_sp.setRange(0,5);   lsnr_sp.setValue(0.8)
    adv_f.addRow("Min spot radius (px):", min_r)
    adv_f.addRow("Kurtosis threshold:", kurt_sp)
    adv_f.addRow("Local SNR threshold:", lsnr_sp)
    stack.addWidget(adv_w)

    form.addRow(stack)

    # Wire radio buttons to the stack, and show/hide the RF scribble panel.
    _rb_order = [rb_otsu, rb_multi, rb_sauv, rb_rf, rb_adv]
    def _on_method():
        for i, _rb in enumerate(_rb_order):
            if _rb.isChecked():
                stack.setCurrentIndex(i); break
    for _rb in _rb_order:
        _rb.toggled.connect(_on_method)
    _on_method()

    # ── Shared post-filters ───────────────────────────────────────────────
    min_area = QSpinBox(); min_area.setRange(0, 100000); min_area.setValue(6)
    min_area.setToolTip("Discard objects smaller than this many pixels² (removes "
                        "speckle). 0 = keep everything.")
    form.addRow("Min object size (px²):", min_area)

    round_cb = QCheckBox("Reject non-round objects (solidity < 0.85)")
    round_cb.setChecked(False)
    round_cb.setToolTip("In-vitro droplets are round; enable to drop irregular "
                        "objects (merged clumps, debris).")
    form.addRow("", round_cb)

    # RF scribble button behaviour (mirrors the contrast-cascade pattern).
    def _on_scribble():
        nm = "IVF RF Scribbles"
        names = [l.name for l in ui.viewer.layers]
        if nm not in names:
            iname = pre_dd.currentText()
            shape = (np.asarray(ui.viewer.layers[iname].data).shape[-2:]
                     if iname in names else (512, 512))
            lyr = ui.viewer.add_labels(np.zeros(shape, dtype=np.uint8), name=nm)
        else:
            lyr = ui.viewer.layers[nm]
        ui.viewer.layers.selection.active = lyr
        lyr.visible = True
        try:
            lyr.mode = 'paint'
            lyr.selected_label = 1
            lyr.brush_size = 8
        except Exception:
            pass
        napari_show_info("Paint 1 = background, 2 = droplet, then press "
                         "'Segment Droplets'.")
    rf_scribble_btn.clicked.connect(_on_scribble)

    prog, run = _run_btn(form, "▶  Segment Droplets")

    def _on_run():
        from pycat.toolbox.segmentation_tools import (
            segment_subcellular_objects, cell_mask_stretching)
        try:
            pre = ui._img(pre_dd)
            raw = ui._img(raw_dd)
        except KeyError as e:
            napari_show_warning(str(e)); return
        ball = int(ui._dr().get('ball_radius', 15))

        if rb_otsu.isChecked():      method = 'otsu'
        elif rb_multi.isChecked():   method = 'multiotsu'
        elif rb_sauv.isChecked():    method = 'sauvola'
        elif rb_rf.isChecked():      method = 'rf'
        else:                        method = 'spot'

        # Snapshot params on the GUI thread.
        p_sens   = otsu_sens.value()
        p_classes= multi_classes.value()
        p_upper  = (multi_level.currentIndex() == 1)
        p_win    = sauv_win.value(); p_k = sauv_k.value()
        p_minr   = min_r.value(); p_kurt = kurt_sp.value(); p_lsnr = lsnr_sp.value()
        p_minarea= min_area.value(); p_round = round_cb.isChecked()

        # RF needs its scribble layer up front (can't run in a worker w/o it).
        rf_scribbles = None
        if method == 'rf':
            nm = "IVF RF Scribbles"
            names = [l.name for l in ui.viewer.layers]
            if nm not in names or int(np.asarray(ui.viewer.layers[nm].data).max()) == 0:
                napari_show_warning("Draw scribbles first: click 'Draw Scribbles', "
                                    "paint 1 = background and 2 = droplet.")
                return
            rf_scribbles = np.asarray(ui.viewer.layers[nm].data)

        prog.setRange(0,0); prog.setVisible(True); run.setEnabled(False)

        def _task():
            import skimage as sk
            from skimage import filters, morphology, measure

            def _postfilter(binary):
                b = np.asarray(binary) > 0
                if p_minarea > 0:
                    b = morphology.remove_small_objects(b, int(p_minarea))
                lab = measure.label(b)
                if p_round:
                    keep = np.zeros_like(lab)
                    for pr in measure.regionprops(lab):
                        if pr.area >= 5 and pr.solidity >= 0.85:
                            keep[lab == pr.label] = pr.label
                    lab = measure.label(keep > 0)
                return lab.astype(np.int32), b

            if method == 'otsu':
                t = filters.threshold_otsu(pre) * p_sens
                return _postfilter(pre > t)

            if method == 'multiotsu':
                ts = filters.threshold_multiotsu(pre, classes=int(p_classes))
                cut = ts[-1] if p_upper else ts[0]
                return _postfilter(pre > cut)

            if method == 'sauvola':
                from pycat.toolbox.segmentation_tools import local_thresholding_func
                binary = local_thresholding_func(pre, window_size=int(p_win),
                                                 k_val=p_k, mode='Sauvola')
                return _postfilter(np.asarray(binary) > 0)

            if method == 'rf':
                from pycat.toolbox.segmentation_tools import train_and_apply_rf_classifier
                od = int(ui._dr().get('cell_diameter', 100))
                # train_and_apply_rf_classifier runs CLAHE (equalize_adapthist),
                # which requires float input in [-1, 1]. The raw fluorescence
                # image is in raw intensity units, so pass a [0,1]-normalized
                # copy or CLAHE raises "Images of type float must be between -1
                # and 1" — caught by the worker and surfacing as an EMPTY mask.
                _p = np.asarray(pre, dtype=np.float32)
                _lo, _hi = float(_p.min()), float(_p.max())
                _pn = (_p - _lo) / (_hi - _lo) if _hi > _lo else _p
                # Returns a LIST of refined masks, one per non-background class
                # (the lowest painted label is dropped as background inside).
                masks = train_and_apply_rf_classifier(_pn, rf_scribbles, od)
                if not masks:
                    return _postfilter(np.zeros(pre.shape, dtype=bool))
                # Foreground = union of all returned (non-background) class masks.
                fg = np.zeros(pre.shape, dtype=bool)
                for m in masks:
                    fg |= (np.asarray(m) > 0)
                return _postfilter(fg)

            # Advanced spot detection (original pipeline).
            H, W = pre.shape
            whole = np.ones((H, W), dtype=bool); whole[:2,:2] = False
            cms  = cell_mask_stretching(pre, whole.astype(int))
            refined, unrefined = segment_subcellular_objects(
                raw.copy(), cms.copy(), whole, 1, ball, cell_df=None,
                min_spot_radius=p_minr, kurtosis_threshold=p_kurt,
                local_snr_threshold=p_lsnr, global_snr_threshold=0.8)
            lab, _ = _postfilter(refined)
            return lab, unrefined

        worker = _IVFWorker(_task)
        ui._ivf_seg_worker = worker

        def _done(result):
            prog.setVisible(False); run.setEnabled(True)
            labeled, unrefined = result
            n = int(labeled.max())
            ui.viewer.add_labels(labeled, name=f"IVF Droplet Mask ({n} droplets)")
            ui._dr()['ivf_droplet_mask'] = labeled
            ui._record('ivf_segmentation', {
                'pre_layer': pre_dd.currentText(), 'raw_layer': raw_dd.currentText(),
                'method': method,
                'otsu_sensitivity': p_sens,
                'multiotsu_classes': p_classes, 'multiotsu_upper': p_upper,
                'sauvola_window': p_win, 'sauvola_k': p_k,
                'min_radius': p_minr, 'kurtosis': p_kurt, 'local_snr': p_lsnr,
                'min_area': p_minarea, 'reject_nonround': p_round,
            })
            napari_show_info(f"In vitro: {n} droplets segmented ({method}).")

        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("Segmentation error — see terminal.")
            print(f"[PyCAT IVF Seg] {msg}")

        worker.finished.connect(_done); worker.error.connect(_err); worker.start()
    run.clicked.connect(_on_run)
    layout.addWidget(grp)



def _ivf_field_summary(ui, layout):
    grp  = QGroupBox("Step 4 — Field Summary & Partition Coefficient")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Area fraction Φ (2D coverage — see note), partition coefficient, bulk "
        "C_sat proxy, number density.<br>"
        "<b>Note:</b> Φ here is the fraction of the imaged <i>plane</i> covered by "
        "droplets, not a true 3D volume fraction. In a flow cell, droplets settle "
        "into the bottom few µm of a ~200 µm channel, so this single-plane Φ over- "
        "or under-represents the bulk volume fraction depending on focal depth. "
        "Treat it as a 2D coverage metric.</span>"))
    img_dd  = ui.create_layer_dropdown(napari.layers.Image)
    mask_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow("Fluorescence image:", img_dd)
    form.addRow("Droplet mask:", mask_dd)
    run = QPushButton("▶  Compute Field Summary")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run))

    def _on_run():
        from pycat.toolbox.invitro_tools import field_summary, partition_coefficient_field
        try:
            img  = ui._img(img_dd)
            mask = np.asarray(ui.viewer.layers[mask_dd.currentText()].data)
        except KeyError as e: napari_show_warning(str(e)); return

        mpx = ui._mpx()
        summ = field_summary(mask, img, mpx)
        part = partition_coefficient_field(img, mask)
        ui._dr()['ivf_field_summary']   = summ
        ui._dr()['ivf_partition_coeff'] = part
        ui._record('ivf_field_summary', {
            'image_layer': img_dd.currentText(), 'mask_layer': mask_dd.currentText()})

        summ_df = pd.DataFrame([summ])
        part_df = part['per_droplet_df']
        part_df['area_um2'] = [
            p.area * mpx**2 for p in sk.measure.regionprops(mask.astype(np.int32))
        ] if len(part_df) > 0 else []
        _show("IVF Field Summary", [
            ("Field statistics", summ_df),
            ("Per-droplet partition", part_df),
        ])
        napari_show_info(
            f"Φ={summ['volume_fraction']:.3f}, "
            f"n={summ['n_droplets']}, "
            f"mean R={summ['mean_radius_um']:.2f}µm, "
            f"partition={summ['partition_coefficient']:.1f}×"
        )
    run.clicked.connect(_on_run)
    import skimage as sk
    layout.addWidget(grp)


def _ivf_size_distribution(ui, layout):
    grp  = QGroupBox("Step 5 — Size Distribution")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    mask_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow(label_with_circle("Droplet mask:", dropdown=mask_dd), mask_dd)
    bins_sp = QSpinBox(); bins_sp.setRange(5,100); bins_sp.setValue(30)
    form.addRow("Histogram bins:", bins_sp)
    run = QPushButton("▶  Fit Size Distribution")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run))

    def _on_run():
        from pycat.toolbox.invitro_tools import fit_size_distribution
        try:
            mask = np.asarray(ui.viewer.layers[mask_dd.currentText()].data)
        except KeyError as e: napari_show_warning(str(e)); return
        mpx  = ui._mpx()
        props = sk.measure.regionprops(mask.astype(np.int32))
        radii = np.array([np.sqrt(p.area * mpx**2 / np.pi) for p in props])
        if len(radii) < 5:
            napari_show_warning("Need at least 5 droplets for size distribution fit."); return
        res = fit_size_distribution(radii, n_bins=bins_sp.value())
        ui._dr()['ivf_size_dist'] = res
        ui._record('ivf_size_distribution', {
            'mask_layer': mask_dd.currentText(), 'n_bins': bins_sp.value()})
        res_df = pd.DataFrame([{k: v for k,v in res.items() if not hasattr(v,'__len__')}])
        _show("Size Distribution", [("Fit parameters", res_df)])
        napari_show_info(
            f"Size distribution: {res.get('preferred_model','?')} preferred, "
            f"PDI={res.get('polydispersity_index',np.nan):.3f}"
        )
    run.clicked.connect(_on_run)
    import skimage as sk
    layout.addWidget(grp)


def _ivf_spatial(ui, layout):
    grp  = QGroupBox("Step 6 — Spatial Metrology")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "NND, Ripley's L, PCF, Voronoi — identical to cellular analysis.</span>"
    ))
    mask_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow(label_with_circle("Droplet mask:", dropdown=mask_dd), mask_dd)
    run = QPushButton("▶  Run Spatial Metrology")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run, optional=True))

    def _on_run():
        from pycat.toolbox.spatial_metrology_tools import (
            get_puncta_centroids, run_all_spatial_metrics)
        from pycat.toolbox.spatial_metrology_ui import _results_to_dataframes
        try:
            mask = np.asarray(ui.viewer.layers[mask_dd.currentText()].data)
        except KeyError as e: napari_show_warning(str(e)); return
        # For in vitro, treat the whole field as a single "cell" so the
        # existing per-cell spatial metrics apply to the whole droplet field.
        H, W = mask.shape[:2]
        field_lbl = np.ones((H, W), dtype=np.int32)
        field_lbl[:2, :2] = 0
        mpx = ui._mpx()

        def _task():
            coords_df = get_puncta_centroids(mask, field_lbl, mpx)
            if coords_df.empty:
                return {}
            results = {}
            for cell_lbl in [c for c in coords_df['cell_label'].unique() if c != 0]:
                sub    = coords_df[coords_df['cell_label'] == cell_lbl]
                coords = sub[['y_um', 'x_um']].values
                if len(coords) < 2:
                    continue
                cmask  = (field_lbl == cell_lbl)
                results[cell_lbl] = run_all_spatial_metrics(coords, cmask, mpx)
            return results

        worker = _IVFWorker(_task)
        ui._ivf_sp_worker = worker
        def _done(res):
            if not res:
                napari_show_warning("Need at least 2 droplets for spatial metrics."); return
            dfs = _results_to_dataframes(res)
            _show("IVF Spatial Metrology", list(dfs.items()))
            ui._record('ivf_spatial_metrology', {'mask_layer': mask_dd.currentText()})
            napari_show_info("Spatial metrology complete.")
        def _err(msg):
            napari_show_warning("Spatial error — see terminal."); print(f"[PyCAT IVF Sp] {msg}")
        worker.finished.connect(_done); worker.error.connect(_err); worker.start()
    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _ivf_dynamics(ui, layout):
    grp  = QGroupBox("Step 7 — Dynamics & Coarsening (time-series)")
    ui._ivf_dynamics_grp = grp
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)

    stack_dd = ui.create_layer_dropdown(napari.layers.Labels)
    img_dd   = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow("Droplet mask stack (T,H,W):", stack_dd)
    form.addRow("Fluorescence stack (optional):", img_dd)

    dt_sp   = QDoubleSpinBox(); dt_sp.setRange(0.01,3600); dt_sp.setValue(1.0)
    disp_sp = QDoubleSpinBox(); disp_sp.setRange(0.1,50);  disp_sp.setValue(5.0)
    disp_sp.setToolTip("Max displacement between frames (µm).\n"
                       "In vitro droplets can move more than cellular condensates.")
    form.addRow("Frame interval (s):", dt_sp)
    form.addRow("Max displacement (µm):", disp_sp)

    cb_msd    = QCheckBox("MSD / diffusion");        cb_msd.setChecked(True)
    cb_coarse = QCheckBox("Coarsening kinetics");    cb_coarse.setChecked(True)
    cb_sed    = QCheckBox("Sedimentation detection"); cb_sed.setChecked(True)
    cb_fuse   = QCheckBox("Auto-fit fusion events"); cb_fuse.setChecked(True)
    cb_km     = QCheckBox("Kaplan-Meier survival");  cb_km.setChecked(True)
    form.addRow(cb_msd); form.addRow(cb_coarse); form.addRow(cb_sed)
    form.addRow(cb_fuse); form.addRow(cb_km)

    prog, run = _run_btn(form, "▶  Run Dynamics")

    def _on_run():
        from pycat.toolbox.dynamic_spatial_tools import (
            extract_frame_properties, link_trajectories_bayesian,
            trajectory_metrics, detect_merge_fission)
        from pycat.toolbox.condensate_physics_tools import (
            compute_msd, fit_anomalous_diffusion, msd_per_track,
            fit_coarsening, kaplan_meier_lifetimes)
        from pycat.toolbox.invitro_tools import (
            coarsening_statistics, detect_sedimentation,
            detect_and_fit_fusions)

        try:
            stack = np.asarray(ui.viewer.layers[stack_dd.currentText()].data)
        except KeyError as e: napari_show_warning(str(e)); return
        if stack.ndim != 3:
            napari_show_warning("Dynamics needs a 3D (T,H,W) label stack."); return

        try:
            img_stack = np.asarray(ui.viewer.layers[img_dd.currentText()].data).astype(np.float32)
        except Exception:
            img_stack = None

        mpx = ui._mpx(); dt = dt_sp.value()
        do = dict(msd=cb_msd.isChecked(), coarse=cb_coarse.isChecked(),
                  sed=cb_sed.isChecked(), fuse=cb_fuse.isChecked(),
                  km=cb_km.isChecked())
        prog.setRange(0,0); prog.setVisible(True); run.setEnabled(False)

        def _task():
            props  = extract_frame_properties(stack, mpx)
            tracks = link_trajectories_bayesian(
                props, max_displacement_um=disp_sp.value())
            res = {'tracks': tracks, 'props': props}

            if do['coarse'] or do['sed']:
                cs = coarsening_statistics(stack, mpx, dt)
                res['coarsening_stats'] = cs
                if do['coarse']:
                    r   = cs['mean_radius_um'].values
                    t   = cs['time_s'].values
                    res['coarsening_fit'] = fit_coarsening(t, r)
                if do['sed']:
                    res['sedimentation'] = detect_sedimentation(cs)

            if do['msd']:
                msd_df = compute_msd(tracks, frame_interval_s=dt)
                res['msd']    = msd_df
                res['msd_fit']= fit_anomalous_diffusion(msd_df)
                res['msd_pt'] = msd_per_track(tracks, dt)

            if do['fuse']:
                res['fusions'] = detect_and_fit_fusions(
                    stack, tracks, img_stack, mpx, dt)

            if do['km']:
                res['km'] = kaplan_meier_lifetimes(tracks, stack.shape[0])

            return res

        worker = _IVFWorker(_task)
        ui._ivf_dyn_worker = worker

        def _done(res):
            prog.setVisible(False); run.setEnabled(True)
            dr = ui._dr()
            dr['ivf_trajectories'] = res['tracks']
            tables = []

            if 'coarsening_stats' in res:
                dr['ivf_coarsening_stats'] = res['coarsening_stats']
                tables.append(("Coarsening per frame", res['coarsening_stats']))
                if 'coarsening_fit' in res:
                    co = res['coarsening_fit']
                    co_df = pd.DataFrame([{k:v for k,v in co.items() if not hasattr(v,'__len__')}])
                    tables.append(("Coarsening fit", co_df))
                    napari_show_info(f"Coarsening: {co.get('preferred_mechanism','?')}")
                if 'sedimentation' in res:
                    sed = res['sedimentation']
                    sed_df = pd.DataFrame([{k:v for k,v in sed.items() if k!='recommendation'}])
                    tables.append(("Sedimentation analysis", sed_df))
                    if sed.get('sedimentation_detected'):
                        napari_show_warning(f"Sedimentation: {sed.get('recommendation','')}")

            if 'msd' in res:
                dr['ivf_msd'] = res['msd']
                fit = res['msd_fit']
                fit_df = pd.DataFrame([{k:v for k,v in fit.items() if not hasattr(v,'__len__')}])
                tables += [("MSD", res['msd']), ("Diffusion fit", fit_df),
                            ("Per-track D,α", res['msd_pt'])]
                napari_show_info(
                    f"MSD: D={fit.get('D_um2_per_s',np.nan):.4f} µm²/s "
                    f"α={fit.get('alpha',np.nan):.3f} ({fit.get('motion_type','?')})")

            if 'fusions' in res and not res['fusions'].empty:
                dr['ivf_fusions'] = res['fusions']
                tables.append(("Fusion relaxation", res['fusions']))
                n_ok = res['fusions']['fit_success'].sum()
                napari_show_info(f"Fusion events: {len(res['fusions'])} detected, "
                                  f"{n_ok} fitted successfully.")

            if 'km' in res:
                dr['ivf_km'] = res['km']
                tables.append(("KM survival", res['km']))

            _show("IVF Dynamics", tables)
            ui._record('ivf_dynamics', {
                'mask_stack': stack_dd.currentText(),
                'frame_interval_s': dt_sp.value(),
                'max_displacement_um': disp_sp.value()})

        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("Dynamics error — see terminal."); print(f"[PyCAT IVF Dyn] {msg}")

        worker.finished.connect(_done); worker.error.connect(_err); worker.start()
    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _ivf_phase_diagram(ui, layout):
    grp  = QGroupBox("Step 8 — Phase Diagram / C_sat")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Enter total protein concentrations and measured volume fractions\n"
        "from a dilution series to estimate C_sat via the lever rule.\n"
        "Separate values with commas.</span>"
    ))
    from PyQt5.QtWidgets import QLineEdit
    conc_edit = QLineEdit(); conc_edit.setPlaceholderText("e.g. 1, 2, 5, 10, 20  (µM)")
    conc_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    phi_edit  = QLineEdit(); conc_edit.setPlaceholderText("e.g. 0, 0, 0.05, 0.12, 0.21")
    phi_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow("Concentrations (µM):", conc_edit)
    form.addRow("Volume fractions (Φ):", phi_edit)
    run = QPushButton("▶  Estimate C_sat")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run, optional=True))

    def _on_run():
        from pycat.toolbox.invitro_tools import estimate_csat_lever_rule
        try:
            concs = np.array([float(x.strip()) for x in conc_edit.text().split(',')])
            phis  = np.array([float(x.strip()) for x in phi_edit.text().split(',')])
        except ValueError:
            napari_show_warning("Could not parse concentrations/fractions — check format."); return
        if len(concs) != len(phis):
            napari_show_warning("Number of concentrations and volume fractions must match."); return

        res = estimate_csat_lever_rule(concs, phis)
        if res.get('fit_success'):
            res_df = pd.DataFrame([{k:v for k,v in res.items() if not hasattr(v,'__len__')}])
            _show("C_sat Estimation", [("Lever rule fit", res_df)])
            ui._record('ivf_phase_diagram', {
                'concentrations': conc_edit.text(),
                'volume_fractions': phi_edit.text()})
            napari_show_info(
                f"C_sat ≈ {res['C_sat']:.2f} µM  "
                f"(R²={res['r_squared']:.3f})"
            )
        else:
            napari_show_warning("Lever rule fit failed — ensure data spans below and above phase boundary.")
    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _ivf_frame_qc(ui, layout):
    grp  = QGroupBox("Step 9 — Frame Quality (bleaching + focus)")
    ui._ivf_qc_grp = grp
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    stack_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow(label_with_circle("Fluorescence stack (T,H,W):", dropdown=stack_dd), stack_dd)
    dt_sp  = QDoubleSpinBox(); dt_sp.setRange(0.01,3600); dt_sp.setValue(1.0)
    thr_sp = QDoubleSpinBox(); thr_sp.setRange(0.01,0.9);  thr_sp.setValue(0.3)
    form.addRow("Frame interval (s):", dt_sp)
    form.addRow("Blur threshold fraction:", thr_sp)
    apply_cb = QCheckBox("Apply bleaching correction (adds corrected layer)")
    apply_cb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    apply_cb.setChecked(False)
    form.addRow(apply_cb)
    prog, run = _run_btn(form, "▶  Run Frame QC")

    def _on_run():
        from pycat.toolbox.condensate_physics_tools import (
            analyse_frame_quality, apply_bleach_correction)
        try:
            layer = ui.viewer.layers[stack_dd.currentText()]
            stack = np.asarray(layer.data).astype(np.float32)
        except KeyError as e: napari_show_warning(str(e)); return
        if stack.ndim != 3:
            napari_show_warning("QC needs a 3D (T,H,W) stack."); return
        mn, mx = stack.min(), stack.max()
        if mx > mn: stack = (stack-mn)/(mx-mn)
        prog.setRange(0,0); prog.setVisible(True); run.setEnabled(False)

        do_apply = apply_cb.isChecked()
        def _task():
            res = analyse_frame_quality(stack, dt_sp.value(), thr_sp.value())
            if do_apply and res['bleach_fit'].get('fit_success'):
                res['corrected'] = apply_bleach_correction(
                    stack, res['bleach_correction_factors'])
            return res

        worker = _IVFWorker(_task)
        ui._ivf_qc_worker = worker
        def _done(res):
            prog.setVisible(False); run.setEnabled(True)
            df = res['per_frame_df']; summ = res['summary']
            ui._dr()['ivf_frame_qc'] = df
            cause = summ['dominant_cause']
            summ_df = pd.DataFrame([{k:v for k,v in summ.items() if k!='recommendation'}])
            _show("IVF Frame QC", [("Summary", summ_df), ("Per-frame", df)])
            ui._record('ivf_frame_qc', {
                'stack_layer': stack_dd.currentText(),
                'frame_interval_s': dt_sp.value(),
                'blur_threshold': thr_sp.value()})
            if 'corrected' in res:
                ui.viewer.add_image(res['corrected'],
                                     name=f"Bleach-Corrected [{layer.name}]",
                                     colormap='viridis')
            napari_show_info(f"Frame QC: {cause}. {summ.get('recommendation','')}")
        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("QC error."); print(f"[PyCAT IVF QC] {msg}")
        worker.finished.connect(_done); worker.error.connect(_err); worker.start()
    run.clicked.connect(_on_run)
    layout.addWidget(grp)
