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
    QScrollArea, QSizePolicy, QHBoxLayout, QTabWidget,
)
from PyQt5.QtCore import QThread, pyqtSignal


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

        layout = QVBoxLayout(); layout.setSpacing(4)
        header = QLabel(
            "<b>In Vitro Fluorescence Condensate Analysis</b><br>"
            "<span style='color:#888;font-size:9pt;'>"
            "For protein/RNA LLPS droplets on coverslip — no cell segmentation needed.</span>"
        )
        header.setWordWrap(True)
        header.setStyleSheet("padding:6px; background:#2a2a2a; border-radius:4px;")
        layout.addWidget(header)

        _ivf_preprocessing(self, layout)
        _ivf_segmentation(self, layout)
        _ivf_field_summary(self, layout)
        _ivf_size_distribution(self, layout)
        _ivf_spatial(self, layout)
        _ivf_dynamics(self, layout)
        _ivf_phase_diagram(self, layout)
        _ivf_frame_qc(self, layout)

        layout.addStretch()
        main_w = QWidget(); main_w.setLayout(layout)
        main_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(main_w)
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
    grp = QGroupBox("Step 2 — Preprocess Fluorescence Image")
    form = QFormLayout(grp)
    img_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow("Fluorescence image:", img_dd)
    ball_spin = QSpinBox(); ball_spin.setRange(2,200); ball_spin.setValue(15)
    ball_spin.setToolTip(
        "Rolling ball radius for background subtraction (pixels).\n"
        "For in vitro data, background is very uniform buffer — a\n"
        "smaller radius than cellular (15-30px vs 50px) is typically better."
    )
    form.addRow("Rolling ball radius (px):", ball_spin)
    prog, run = _run_btn(form, "▶  Preprocess")

    def _on_run():
        from pycat.toolbox.image_processing_tools import pre_process_image
        try: img = ui._img(img_dd)
        except KeyError as e: napari_show_warning(str(e)); return
        ball = ball_spin.value()
        prog.setRange(0,0); prog.setVisible(True); run.setEnabled(False)
        def _task():
            proc = pre_process_image(img, ball_radius=ball, window_size=ball*2)
            return np.asarray(proc).astype(np.float32)
        worker = _IVFWorker(_task)
        ui._ivf_pre_worker = worker
        def _done(proc):
            prog.setVisible(False); run.setEnabled(True)
            ui.viewer.add_image(proc, name=f"IVF Preprocessed [{img_dd.currentText()}]",
                                 colormap='viridis')
            ui._dr()['ivf_preprocessed'] = proc
            ui._record('ivf_preprocess', {'image_layer': img_dd.currentText(),
                                           'ball_radius': ball})
            napari_show_info("In vitro preprocessing done.")
        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("Preprocessing error — see terminal."); print(f"[PyCAT IVF] {msg}")
        worker.finished.connect(_done); worker.error.connect(_err); worker.start()
    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _ivf_segmentation(ui, layout):
    grp  = QGroupBox("Step 3 — Segment Droplets (whole field, no cell mask)")
    form = QFormLayout(grp)
    pre_dd  = ui.create_layer_dropdown(napari.layers.Image)
    raw_dd  = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow("Preprocessed image:", pre_dd)
    form.addRow("Raw fluorescence image:", raw_dd)

    min_r   = QDoubleSpinBox(); min_r.setRange(1,50);   min_r.setValue(2.0)
    kurt_sp = QDoubleSpinBox(); kurt_sp.setRange(-10,0); kurt_sp.setValue(-3.0)
    lsnr_sp = QDoubleSpinBox(); lsnr_sp.setRange(0,5);  lsnr_sp.setValue(0.8)
    form.addRow("Min spot radius (px):", min_r)
    form.addRow("Kurtosis threshold:", kurt_sp)
    form.addRow("Local SNR threshold:", lsnr_sp)

    prog, run = _run_btn(form, "▶  Segment Droplets")

    def _on_run():
        from pycat.toolbox.segmentation_tools import (
            segment_subcellular_objects, cell_mask_stretching)
        try:
            pre = ui._img(pre_dd)
            raw = ui._img(raw_dd)
        except KeyError as e: napari_show_warning(str(e)); return
        ball = int(ui._dr().get('ball_radius', 15))
        prog.setRange(0,0); prog.setVisible(True); run.setEnabled(False)

        def _task():
            # No cell mask — use whole image as single "cell"
            H, W = pre.shape
            whole = np.ones((H, W), dtype=bool)
            whole[:2,:2] = False  # leave corner for background label
            cms  = cell_mask_stretching(pre, whole.astype(int))
            # Label the whole-field mask as cell 1
            cell_mask = np.ones((H, W), dtype=int)
            cell_mask[:2,:2] = 0
            refined, unrefined = segment_subcellular_objects(
                raw.copy(), cms.copy(), whole, 1, ball, cell_df=None,
                min_spot_radius=min_r.value(),
                kurtosis_threshold=kurt_sp.value(),
                local_snr_threshold=lsnr_sp.value(),
                global_snr_threshold=0.8,
            )
            import skimage as sk
            labeled = sk.measure.label(refined)
            return labeled.astype(np.int32), unrefined

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
                'min_radius': min_r.value(), 'kurtosis': kurt_sp.value(),
            })
            napari_show_info(f"In vitro: {n} droplets segmented.")

        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("Segmentation error — see terminal."); print(f"[PyCAT IVF Seg] {msg}")

        worker.finished.connect(_done); worker.error.connect(_err); worker.start()
    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _ivf_field_summary(ui, layout):
    grp  = QGroupBox("Step 4 — Field Summary & Partition Coefficient")
    form = QFormLayout(grp)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Volume fraction Φ, partition coefficient, bulk C_sat proxy, number density.</span>"
    ))
    img_dd  = ui.create_layer_dropdown(napari.layers.Image)
    mask_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow("Fluorescence image:", img_dd)
    form.addRow("Droplet mask:", mask_dd)
    run = QPushButton("▶  Compute Field Summary")
    form.addRow(run)

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
    grp  = QGroupBox("Step 5 — Size Distribution (lognormal / power-law)")
    form = QFormLayout(grp)
    mask_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow("Droplet mask:", mask_dd)
    bins_sp = QSpinBox(); bins_sp.setRange(5,100); bins_sp.setValue(30)
    form.addRow("Histogram bins:", bins_sp)
    run = QPushButton("▶  Fit Size Distribution")
    form.addRow(run)

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
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "NND, Ripley's L, PCF, Voronoi — identical to cellular analysis.</span>"
    ))
    mask_dd = ui.create_layer_dropdown(napari.layers.Labels)
    form.addRow("Droplet mask:", mask_dd)
    run = QPushButton("▶  Run Spatial Metrology")
    form.addRow(run)

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
            napari_show_info("Spatial metrology complete.")
        def _err(msg):
            napari_show_warning("Spatial error — see terminal."); print(f"[PyCAT IVF Sp] {msg}")
        worker.finished.connect(_done); worker.error.connect(_err); worker.start()
    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _ivf_dynamics(ui, layout):
    grp  = QGroupBox("Step 7 — Dynamics & Coarsening (time-series)")
    form = QFormLayout(grp)

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

        def _err(msg):
            prog.setVisible(False); run.setEnabled(True)
            napari_show_warning("Dynamics error — see terminal."); print(f"[PyCAT IVF Dyn] {msg}")

        worker.finished.connect(_done); worker.error.connect(_err); worker.start()
    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _ivf_phase_diagram(ui, layout):
    grp  = QGroupBox("Step 8 — Phase Diagram / C_sat (dilution series)")
    form = QFormLayout(grp)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Enter total protein concentrations and measured volume fractions\n"
        "from a dilution series to estimate C_sat via the lever rule.\n"
        "Separate values with commas.</span>"
    ))
    from PyQt5.QtWidgets import QLineEdit
    conc_edit = QLineEdit(); conc_edit.setPlaceholderText("e.g. 1, 2, 5, 10, 20  (µM)")
    phi_edit  = QLineEdit(); conc_edit.setPlaceholderText("e.g. 0, 0, 0.05, 0.12, 0.21")
    form.addRow("Concentrations (µM):", conc_edit)
    form.addRow("Volume fractions (Φ):", phi_edit)
    run = QPushButton("▶  Estimate C_sat")
    form.addRow(run)

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
    form = QFormLayout(grp)
    stack_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow("Fluorescence stack (T,H,W):", stack_dd)
    dt_sp  = QDoubleSpinBox(); dt_sp.setRange(0.01,3600); dt_sp.setValue(1.0)
    thr_sp = QDoubleSpinBox(); thr_sp.setRange(0.01,0.9);  thr_sp.setValue(0.3)
    form.addRow("Frame interval (s):", dt_sp)
    form.addRow("Blur threshold fraction:", thr_sp)
    apply_cb = QCheckBox("Apply bleaching correction (adds corrected layer)")
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
