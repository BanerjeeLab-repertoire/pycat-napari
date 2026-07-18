"""
PyCAT In Vitro Brightfield Condensate UI
==========================================
Pipeline for brightfield images of in vitro LLPS droplet assays.

Pipeline
--------
  Step 2 — Preprocess    : flat-field, BG subtract, halo correction, CLAHE
  Step 3 — Segment       : dark-blob segmentation (no cell mask)
  Step 4 — OD metrics    : optical density, CNR, field summary
  Step 5 — Size & shape  : size distribution fit, contact angle
  Step 6 — Spatial       : NND, Ripley, etc
  Step 7 — Dynamics      : coarsening, sedimentation, tracking, MSD, fusions
  Step 8 — Focus QC      : Brenner/Tenengrad focus quality

Compared to In Vitro Fluorescence:
  - Uses OD not fluorescence intensity
  - Contact angle measurement available (BF-specific)
  - No bleaching QC (no fluorophore)
  - Sedimentation more visible in BF (contrast increases as droplets settle)
"""
from __future__ import annotations
import numpy as np


from pycat.utils.general_utils import debug_log
from pycat.utils.pixel_size import pixel_size_um_or_default
import pandas as pd
import napari
from napari.utils.notifications import (
    show_info    as napari_show_info,
    show_warning as napari_show_warning,
)
from PyQt5.QtWidgets import (
    QVBoxLayout, QWidget, QPushButton, QGroupBox, QFormLayout,
    QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QProgressBar,
    QScrollArea, QSizePolicy, QComboBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
try:
    from pycat.ui.field_status import label_with_circle
except Exception:
    label_with_circle = lambda t,**k: t


class _IVBFWorker(QThread):
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)
    def __init__(self, fn):
        super().__init__(); self._fn = fn
    def run(self):
        try:    self.finished.emit(self._fn())
        except Exception:
            import traceback; self.error.emit(traceback.format_exc())


class InVitroBFUI:
    def __init__(self, viewer, central_manager):
        self.viewer = viewer; self.central_manager = central_manager

    def _dr(self):  return self.central_manager.active_data_class.data_repository
    def _mpx(self): return pixel_size_um_or_default(self._dr(), context='invitro_bf_ui')
    def _record(self, step, params):
        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp: bp.record(step, params)
    def create_layer_dropdown(self, lt):
        return self.central_manager.toolbox_functions_ui.create_layer_dropdown(lt)
    def _img(self, dd):
        arr = np.asarray(self.viewer.layers[dd.currentText()].data).astype(np.float32)
        mn, mx = arr.min(), arr.max()
        return (arr-mn)/(mx-mn+1e-8) if mx > mn else arr

    def setup_ui(self):
        try:
            self.central_manager.workflow_checklist.activate('invitro_bf')
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
            "<b>In Vitro Brightfield Condensate Analysis</b><br>"
            "<span style='color:#888;font-size:9pt;'>"
            "Dark droplets on bright buffer background — no cell segmentation.</span>"
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
                    "Load a brightfield image via "
                    "<b>Open/Save File(s)</b>, or drag one onto the canvas."))
            self._pixel_gate_refresh = add_pixel_size_gate(
                layout,
                lambda: self.central_manager.active_data_class.data_repository,
                central_manager=self.central_manager)
        except Exception as _gate_exc:
            # **The pixel-size gate is not optional.** It is the check that catches an image
            # with no physical scale — and it was installed inside `except Exception: pass`,
            # in SEVEN panels. If it threw, `_pixel_gate_refresh` was never set, the reset
            # hook found `None` and did nothing, and **the panel built perfectly.** The image
            # then loaded at 1.0 µm/px and *every length, area and diffusion coefficient was
            # silently in pixels while the column header said microns.*
            #
            # *That is the pixel-size gate regression that cost a night to find. It was
            # unfindable by construction.* See `utils.general_utils.guarantee`.
            from pycat.utils.general_utils import report_guarantee_failure
            report_guarantee_failure("invitro_bf_ui: pixel-size gate", _gate_exc)

        _ivbf_preprocessing(self, layout)
        _ivbf_segmentation(self, layout)
        _ivbf_od_field(self, layout)
        _ivbf_size_contact(self, layout)
        _ivbf_spatial(self, layout)
        _ivbf_dynamics(self, layout)
        _ivbf_focus_qc(self, layout)

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
        self.viewer.window.add_dock_widget(scroll, name="In Vitro Brightfield Analysis")


def _run_btn(form, label="▶  Run"):
    prog = QProgressBar(); prog.setVisible(False)
    btn  = QPushButton(label)
    form.addRow(prog); form.addRow(btn)
    return prog, btn


def _show(title, tables):
    from pycat.ui.ui_utils import show_dataframes_dialog
    show_dataframes_dialog(title, [(k, v.round(4) if hasattr(v,'round') else v)
                                   for k,v in tables])


def _ivbf_preprocessing(ui, layout):
    grp  = QGroupBox("Step 2 — Preprocess Brightfield Image")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)

    img_dd = ui.create_layer_dropdown(napari.layers.Image)
    ref_cb = QCheckBox("Use flat-field reference")
    ref_dd = ui.create_layer_dropdown(napari.layers.Image)
    ref_dd.setEnabled(False)
    ref_cb.stateChanged.connect(lambda s: ref_dd.setEnabled(bool(s)))
    form.addRow("Brightfield image:", img_dd)
    form.addRow(ref_cb); form.addRow("  Reference:", ref_dd)

    bg_sp   = QSpinBox(); bg_sp.setRange(10,300); bg_sp.setValue(60)
    bg_sp.setToolTip("Background kernel size. For in vitro the field is large and\n"
                     "illumination very uniform — 60-100px works well.")
    halo_sp = QDoubleSpinBox(); halo_sp.setRange(0,0.8); halo_sp.setValue(0.3)
    form.addRow("BG kernel (px):", bg_sp)
    form.addRow("Halo suppression:", halo_sp)

    prog, run = _run_btn(form, "▶  Preprocess")

    def _on_run():
        from pycat.toolbox.brightfield_tools import preprocess_brightfield
        try: img = ui._img(img_dd)
        except KeyError as e: napari_show_warning(str(e)); return
        ref = ui._img(ref_dd) if ref_cb.isChecked() else None
        prog.setRange(0,0); prog.setVisible(True); run.setEnabled(False)
        def _task():
            return preprocess_brightfield(img, bg_kernel=bg_sp.value(),
                                           halo_weight=halo_sp.value(),
                                           background_image=ref)
        worker = _IVBFWorker(_task)
        ui._ivbf_pre_worker = worker
        def _done(res):
            prog.setVisible(False); run.setEnabled(True)
            base = img_dd.currentText()
            ui.viewer.add_image(res['bg_subtracted'],
                                 name=f"IVBF BG-Subtracted [{base}]", colormap='gray_r')
            ui.viewer.add_image(res['enhanced'],
                                 name=f"IVBF Enhanced [{base}]", colormap='viridis')
            ui._dr()['ivbf_enhanced']     = res['enhanced']
            ui._dr()['ivbf_bg_subtracted']= res['bg_subtracted']
            ui._dr()['ivbf_source']       = img
            ui._record('ivbf_preprocess', {'image_layer': img_dd.currentText(),
                                            'bg_kernel': bg_sp.value()})
            napari_show_info("In vitro BF preprocessing done.")
        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("Preprocessing error."); print(f"[PyCAT IVBF] {msg}")
        worker.finished.connect(_done); worker.error.connect(_err); worker.start()
    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _ivbf_segmentation(ui, layout):
    grp  = QGroupBox("Step 3 — Segment Droplets")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    enh_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow(label_with_circle("Enhanced BF image:", dropdown=enh_dd), enh_dd)

    method_dd = QComboBox()
    method_dd.addItems(["Texture (edges/rings — local adaptive)",
                        "Blob detection (DoG — consistent per-droplet)",
                        "Invert + reconcile (both focus polarities)",
                        "Intensity threshold (bright blobs)"])
    method_dd.setToolTip(
        "Texture: local-adaptive threshold on local intensity variation — good "
        "for dense small droplets and rings; adaptive so regions of the same "
        "texture segment consistently.\n"
        "Blob (DoG): detects individual droplet-scale blobs — most consistent "
        "per-droplet output, cannot fuse a region into one giant blob.\n"
        "Invert + reconcile: detects bright- AND dark-centred droplets (runs on "
        "the image and its inversion, unions both, drops oversized objects) — "
        "catches condensates on either side of focus.\n"
        "Intensity: thresholds a preprocessed bright-blob image (legacy).")
    form.addRow("Method:", method_dd)

    min_d = QDoubleSpinBox(); min_d.setRange(1,100); min_d.setValue(4.0)
    max_d = QDoubleSpinBox(); max_d.setRange(5,1000); max_d.setValue(200.0)
    circ  = QDoubleSpinBox(); circ.setRange(0.1,1.0);  circ.setValue(0.5)
    max_d.setToolTip("In vitro droplets can be larger than cellular condensates.")
    form.addRow("Min diameter (px):", min_d)
    form.addRow("Max diameter (px):", max_d)
    form.addRow("Min circularity:", circ)

    tex_win = QSpinBox(); tex_win.setRange(3,51); tex_win.setSingleStep(2); tex_win.setValue(9)
    tex_win.setToolTip("Local-std window (px) for the texture method. ~ droplet edge width.")
    tex_win_lbl = QLabel("Texture window (px):")
    form.addRow(tex_win_lbl, tex_win)
    split_cb = QCheckBox("Split touching droplets (watershed)")
    split_cb.setChecked(True)
    form.addRow("", split_cb)

    def _on_method():
        _mi = method_dd.currentIndex()
        is_tex = (_mi == 0)              # texture window only for texture
        is_split = (_mi in (0, 1, 2))   # texture + dog + invert_reconcile split
        tex_win.setVisible(is_tex); tex_win_lbl.setVisible(is_tex)
        split_cb.setVisible(is_split)
    method_dd.currentIndexChanged.connect(_on_method)
    _on_method()

    prog, run = _run_btn(form, "▶  Segment Droplets")

    def _on_run():
        from pycat.toolbox.brightfield_tools import segment_bf_condensates
        try: enh = ui._img(enh_dd)
        except KeyError as e: napari_show_warning(str(e)); return
        _mi = method_dd.currentIndex()
        _method = ('texture' if _mi == 0 else 'dog' if _mi == 1
                   else 'invert_reconcile' if _mi == 2 else 'intensity')
        _tw = tex_win.value(); _split = split_cb.isChecked()
        _mind = min_d.value(); _maxd = max_d.value(); _circ = circ.value()
        prog.setRange(0,0); prog.setVisible(True); run.setEnabled(False)
        def _task():
            return segment_bf_condensates(enh, min_diameter_px=_mind,
                                           max_diameter_px=_maxd,
                                           min_circularity=_circ,
                                           method=_method,
                                           texture_window=_tw,
                                           split_touching=_split)
        worker = _IVBFWorker(_task)
        ui._ivbf_seg_worker = worker
        def _done(labeled):
            prog.setVisible(False); run.setEnabled(True)
            n = int(labeled.max())
            ui.viewer.add_labels(labeled, name=f"IVBF Droplet Mask ({n} droplets)")
            ui._dr()['ivbf_droplet_mask'] = labeled
            ui._record('ivbf_segmentation', {'enhanced_layer': enh_dd.currentText(),
                                              'method': _method,
                                              'texture_window': _tw, 'split': _split,
                                              'min_d': _mind, 'max_d': _maxd})
            napari_show_info(f"In vitro BF: {n} droplets segmented ({_method}).")
        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("Segmentation error."); print(f"[PyCAT IVBF Seg] {msg}")
        worker.finished.connect(_done); worker.error.connect(_err); worker.start()
    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _ivbf_od_field(ui, layout):
    grp  = QGroupBox("Step 4 — Optical Density & Field Summary")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    raw_dd  = ui.create_layer_dropdown(napari.layers.Image)
    mask_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow("Raw BF image:", raw_dd)
    form.addRow("Droplet mask:", mask_dd)
    run = QPushButton("▶  Compute OD & Field Summary")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run))

    def _on_run():
        from pycat.toolbox.brightfield_tools import bf_condensate_metrics
        from pycat.toolbox.invitro_tools import field_summary
        try:
            # ── RAW counts, not ui._img() ────────────────────────────────────────
            #
            # `ui._img()` min-max normalises to [0, 1]. Fine for segmentation; **fatal for
            # optical density**, which `bf_condensate_metrics` computes as
            # OD = -log10(I / I0). Normalisation maps the image MINIMUM to zero — and in
            # brightfield the darkest pixel IS the strongest condensate, so its OD diverges:
            #
            #     condensate            true OD    OD on normalised data
            #     moderate (50 % abs)   0.301      0.310
            #     very strong (98 %)    1.699      **12.000**
            #
            # A log of a ratio cannot be computed on data whose zero point has been moved.
            # The measurement becomes self-referential: every field's OD is scaled by its
            # own most-absorbing object.
            raw  = np.asarray(ui.viewer.layers[raw_dd.currentText()].data,
                              dtype=np.float64)
            mask = np.asarray(ui.viewer.layers[mask_dd.currentText()].data)
        except KeyError as e: napari_show_warning(str(e)); return
        mpx = ui._mpx()
        # For in vitro BF, pass mask as both droplet and "cell" (whole field)
        # `1 - raw` was an inverted-intensity proxy that ONLY made sense on [0, 1] data.
        # On raw counts it is meaningless (it would be negative). Compute the real optical
        # density instead: OD = -log10(I / I0), with I0 the transmitted background.
        #
        # This is not a cosmetic change. OD is what relates a brightfield image to
        # concentration (Beer-Lambert); `1 - I` is a linear proxy that is only monotonic
        # with OD, not proportional to it.
        _i0 = float(np.percentile(raw[mask == 0], 90)) if (mask == 0).any() else float(raw.max())
        od_proxy = -np.log10(np.clip(raw, 1e-6, None) / max(_i0, 1e-6))
        summ = field_summary(mask, od_proxy, mpx)
        per_drop = bf_condensate_metrics(raw, mask, None, mpx)
        ui._dr()['ivbf_field_summary'] = summ
        ui._dr()['ivbf_droplet_df']    = per_drop
        summ_df = pd.DataFrame([summ])
        _show("IVBF Field Summary", [
            ("Field statistics", summ_df),
            ("Per-droplet OD metrics", per_drop),
        ])
        napari_show_info(
            f"area fraction={summ['projected_area_fraction']:.3f} (2D projection, "
            f"not a volume fraction), n={summ['n_droplets']}, "
            f"mean R={summ['mean_radius_um']:.2f}µm, "
            f"mean apparent OD={per_drop['mean_od'].mean():.3f} "
            f"(scattering/phase, not calibrated absorbance)"
        )
    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _ivbf_size_contact(ui, layout):
    grp  = QGroupBox("Step 5 — Size Distribution & Contact Angle")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)

    mask_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow(label_with_circle("Droplet mask:", dropdown=mask_dd), mask_dd)

    # Contact angle selector
    single_lbl_sp = QSpinBox(); single_lbl_sp.setRange(0, 9999); single_lbl_sp.setValue(0)
    single_lbl_sp.setToolTip("Label ID for contact angle measurement (0 = largest droplet).")
    form.addRow("Label for contact angle (0=largest):", single_lbl_sp)

    bins_sp = QSpinBox(); bins_sp.setRange(5,100); bins_sp.setValue(30)
    form.addRow("Size histogram bins:", bins_sp)

    run = QPushButton("▶  Fit Size Distribution & Contact Angle")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    from pycat.ui.field_status import button_with_circle as _bwc
    form.addRow(_bwc(run))

    def _on_run():
        from pycat.toolbox.invitro_tools import (
            fit_size_distribution, estimate_contact_angle)
        import skimage as sk
        try:
            mask = np.asarray(ui.viewer.layers[mask_dd.currentText()].data)
        except KeyError as e: napari_show_warning(str(e)); return

        mpx = ui._mpx()
        props = sk.measure.regionprops(mask.astype(np.int32))
        if not props:
            napari_show_warning("No droplets in mask."); return

        radii = np.array([np.sqrt(p.area * mpx**2 / np.pi) for p in props])
        size_res = fit_size_distribution(radii, bins_sp.value())
        ui._dr()['ivbf_size_dist'] = size_res
        size_df = pd.DataFrame([{k:v for k,v in size_res.items() if not hasattr(v,'__len__')}])

        # Contact angle for selected or largest droplet
        lbl_id = single_lbl_sp.value()
        if lbl_id == 0:
            lbl_id = max(props, key=lambda p: p.area).label
        raw = ui._dr().get('ivbf_source')
        tables = [("Size distribution fit", size_df)]
        if raw is not None:
            ca_res = estimate_contact_angle(raw, mask, droplet_label=lbl_id)
            if ca_res.get('fit_success'):
                ca_df = pd.DataFrame([{k:v for k,v in ca_res.items()
                                       if not hasattr(v,'__len__')}])
                tables.append(("Contact angle", ca_df))
                napari_show_info(
                    f"Size: {size_res.get('preferred_model','?')}, "
                    f"PDI={size_res.get('polydispersity_index',np.nan):.3f}. "
                    f"Contact angle: {ca_res.get('contact_angle_deg',np.nan):.1f}°"
                )
            else:
                napari_show_warning("Contact angle fit failed — check droplet boundary.")
        else:
            napari_show_info(f"Size: {size_res.get('preferred_model','?')}, "
                              f"PDI={size_res.get('polydispersity_index',np.nan):.3f}")

        _show("IVBF Size & Contact Angle", tables)
    run.clicked.connect(_on_run)
    import skimage as sk
    layout.addWidget(grp)


def _ivbf_spatial(ui, layout):
    grp  = QGroupBox("Step 6 — Spatial Metrology")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
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
        H, W = mask.shape[:2]
        field_lbl = np.ones((H, W), dtype=np.int32); field_lbl[:2, :2] = 0
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

        worker = _IVBFWorker(_task)
        ui._ivbf_sp_worker = worker
        def _done(res):
            if not res:
                napari_show_warning("Need at least 2 droplets for spatial metrics."); return
            _show("IVBF Spatial", list(_results_to_dataframes(res).items()))
            napari_show_info("Spatial metrology done.")
        def _err(msg):
            napari_show_warning("Spatial error."); print(f"[PyCAT IVBF Sp] {msg}")
        worker.finished.connect(_done); worker.error.connect(_err); worker.start()
    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _ivbf_dynamics(ui, layout):
    grp  = QGroupBox("Step 7 — Dynamics & Coarsening (time-series)")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    stack_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow(label_with_circle("Droplet mask stack (T,H,W):", dropdown=stack_dd), stack_dd)
    dt_sp   = QDoubleSpinBox(); dt_sp.setRange(0.01,3600); dt_sp.setValue(1.0)
    # The frame interval comes from the FILE, not from a spinbox default. See
    # pycat.utils.frame_interval — a 1.0 s default is a physical CLAIM, and it is
    # almost never true. The user's own value always wins.
    try:
        from pycat.utils.frame_interval import sync_spinbox_from_metadata
        sync_spinbox_from_metadata(
            dt_sp, ui.central_manager.active_data_class.data_repository,
            context='invitro_bf_ui')
    except Exception as _exc:
        # NOT cosmetic: this installs the frame interval. Every dynamics result scales with it directly: assume 1.0 s when the
                    # truth is 0.5 s and D, alpha, t-half and the coarsening rate are ALL out by 2x.
        # `debug_log` prints ONLY under PYCAT_DEBUG=1 -- so in normal use this failed
        # in COMPLETE SILENCE. See utils.general_utils.report_guarantee_failure.
        from pycat.utils.general_utils import report_guarantee_failure
        report_guarantee_failure('invitro_bf_ui: sync_spinbox_from_metadata', _exc)
    disp_sp = QDoubleSpinBox(); disp_sp.setRange(0.1,100); disp_sp.setValue(10.0)
    form.addRow("Frame interval (s):", dt_sp)
    form.addRow("Max displacement (µm):", disp_sp)
    cb_coarse = QCheckBox("Coarsening kinetics"); cb_coarse.setChecked(True)
    cb_sed    = QCheckBox("Sedimentation detection"); cb_sed.setChecked(True)
    cb_msd    = QCheckBox("MSD / diffusion");        cb_msd.setChecked(True)
    cb_fuse   = QCheckBox("Fusion relaxation");      cb_fuse.setChecked(True)
    form.addRow(cb_coarse); form.addRow(cb_sed); form.addRow(cb_msd); form.addRow(cb_fuse)
    prog, run = _run_btn(form, "▶  Run Dynamics")

    def _on_run():
        from pycat.toolbox.dynamic_spatial_tools import (
            extract_frame_properties, link_trajectories_bayesian)
        from pycat.toolbox.condensate_physics_tools import (
            compute_msd, fit_anomalous_diffusion, msd_per_track, fit_coarsening)
        from pycat.toolbox.invitro_tools import (
            coarsening_statistics, detect_sedimentation, detect_and_fit_fusions)
        try:
            from pycat.utils.qt_worker import materialize_off_thread
            stack = materialize_off_thread(ui.viewer.layers[stack_dd.currentText()].data,
                                           viewer=ui.viewer, dtype=None)
        except KeyError as e: napari_show_warning(str(e)); return
        if stack.ndim != 3:
            napari_show_warning("Needs a 3D (T,H,W) mask stack."); return

        mpx = ui._mpx(); dt = dt_sp.value()
        do = dict(c=cb_coarse.isChecked(), s=cb_sed.isChecked(),
                  m=cb_msd.isChecked(), f=cb_fuse.isChecked())
        prog.setRange(0,0); prog.setVisible(True); run.setEnabled(False)

        def _task():
            props  = extract_frame_properties(stack, mpx)
            tracks = link_trajectories_bayesian(props, max_displacement_um=disp_sp.value())
            res = {'tracks': tracks}
            cs = coarsening_statistics(stack, mpx, dt)
            res['coarsening_stats'] = cs
            if do['c']:
                r = cs['mean_radius_um'].values; t = cs['time_s'].values
                res['coarsening_fit'] = fit_coarsening(t, r)
            if do['s']:
                res['sedimentation'] = detect_sedimentation(cs)
            if do['m']:
                try:
                    from pycat.file_io.stack_access import warn_if_assumed_axis
                    warn_if_assumed_axis(ui._dr(), 'Condensate MSD / coarsening (treats frames as time)')
                except Exception as _exc:
                    # NOT cosmetic: this installs the T-vs-Z check. If this stack is really a Z-series, 'time' is depth and the dynamics
                    # being reported are not dynamics at all.
                    # `debug_log` prints ONLY under PYCAT_DEBUG=1 -- so in normal use this failed
                    # in COMPLETE SILENCE. See utils.general_utils.report_guarantee_failure.
                    from pycat.utils.general_utils import report_guarantee_failure
                    report_guarantee_failure('invitro_bf_ui: warn_if_assumed_axis', _exc)
                msd_df = compute_msd(tracks, frame_interval_s=dt)
                res['msd']    = msd_df
                res['msd_fit']= fit_anomalous_diffusion(msd_df)
                res['msd_pt'] = msd_per_track(tracks, dt)
            if do['f']:
                res['fusions'] = detect_and_fit_fusions(stack, tracks, None, mpx, dt)
            return res

        worker = _IVBFWorker(_task)
        ui._ivbf_dyn_worker = worker
        def _done(res):
            prog.setVisible(False); run.setEnabled(True)
            dr = ui._dr(); dr['ivbf_trajectories'] = res['tracks']
            tables = [("Coarsening per frame", res['coarsening_stats'])]
            if 'coarsening_fit' in res:
                co = res['coarsening_fit']
                tables.append(("Coarsening fit",
                                pd.DataFrame([{k:v for k,v in co.items() if not hasattr(v,'__len__')}])))
                napari_show_info(f"Coarsening: {co.get('preferred_mechanism','?')}")
            if 'sedimentation' in res:
                sed = res['sedimentation']
                sed_df = pd.DataFrame([{k:v for k,v in sed.items() if k!='recommendation'}])
                tables.append(("Sedimentation", sed_df))
                if sed.get('sedimentation_detected'):
                    napari_show_warning(f"Sedimentation detected: {sed.get('recommendation','')}")
            if 'msd' in res:
                fit = res['msd_fit']
                fit_df = pd.DataFrame([{k:v for k,v in fit.items() if not hasattr(v,'__len__')}])
                tables += [("MSD", res['msd']), ("Diffusion", fit_df), ("Per-track", res['msd_pt'])]
            if 'fusions' in res and not res['fusions'].empty:
                dr['ivbf_fusions'] = res['fusions']
                tables.append(("Fusion relaxation", res['fusions']))
            _show("IVBF Dynamics", tables)
        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("Dynamics error."); print(f"[PyCAT IVBF Dyn] {msg}")
        worker.finished.connect(_done); worker.error.connect(_err); worker.start()
    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _ivbf_focus_qc(ui, layout):
    grp  = QGroupBox("Step 8 — Focus Quality (time-series)")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "BF focus QC using Brenner/Tenengrad/normalised variance.\n"
        "No bleaching correction — no fluorophore to bleach.</span>"
    ))
    stack_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow(label_with_circle("BF stack (T,H,W):", dropdown=stack_dd), stack_dd)
    dt_sp  = QDoubleSpinBox(); dt_sp.setRange(0.01,3600); dt_sp.setValue(1.0)
    thr_sp = QDoubleSpinBox(); thr_sp.setRange(0.1,0.9);  thr_sp.setValue(0.4)
    form.addRow("Frame interval (s):", dt_sp)
    form.addRow("Defocus threshold:", thr_sp)
    run = QPushButton("▶  Assess Focus")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    # Its own bar. `_ivbf_dynamics` next door has a `prog` from `_run_btn`; that one is NOT in scope
    # here, and wiring to it raises NameError on the first click (test_no_undefined_names caught
    # exactly that). A sibling's bar is not this section's bar.
    prog = QProgressBar(); prog.setVisible(False)
    form.addRow(prog)
    form.addRow(run)

    def _on_run():
        from pycat.toolbox.brightfield_tools import bf_analyse_frame_quality
        try:
            from pycat.utils.qt_worker import materialize_off_thread
            stack = materialize_off_thread(ui.viewer.layers[stack_dd.currentText()].data,
                                           viewer=ui.viewer, dtype=np.float32)
        except KeyError as e: napari_show_warning(str(e)); return
        if stack.ndim != 3:
            napari_show_warning("Needs a 3D (T,H,W) stack."); return
        mn, mx = stack.min(), stack.max()
        if mx > mn: stack = (stack-mn)/(mx-mn)
        run.setEnabled(False)
        def _task():
            return bf_analyse_frame_quality(stack, dt_sp.value(), thr_sp.value())
        worker = _IVBFWorker(_task)
        ui._ivbf_qc_worker = worker
        def _done(res):
            run.setEnabled(True)
            df = res['per_frame_df']; summ = res['summary']
            ui._dr()['ivbf_frame_qc'] = df
            summ_df = pd.DataFrame([{k:v for k,v in summ.items() if k!='recommendation'}])
            _show("IVBF Focus QC", [("Summary", summ_df), ("Per-frame", df)])
            n = summ['n_defocused_frames']
            if n > 0:
                napari_show_warning(f"IVBF Focus: {n} defocused frame(s). {summ['recommendation']}")
            else:
                napari_show_info(f"IVBF Focus: {summ['dominant_cause']}. {summ['recommendation']}")
        def _err(msg):
            run.setEnabled(True); napari_show_warning("Focus QC error."); print(f"[PyCAT IVBF QC] {msg}")
        worker.finished.connect(_done); worker.error.connect(_err); worker.start()
    run.clicked.connect(_on_run)
    layout.addWidget(grp)
