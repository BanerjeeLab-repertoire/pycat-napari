"""
PyCAT Condensate Physics UI
============================
Tabbed widget for:
  Tab 1 — Diffusion & MSD
  Tab 2 — Intensity Decomposition (Csat / bimodal)
  Tab 3 — Kinetics (fusion relaxation, coarsening)
  Tab 4 — Quality Control (bleaching correction, focus detection)
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
    QSizePolicy,
    QVBoxLayout, QWidget, QPushButton, QGroupBox, QFormLayout,
    QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QProgressBar,
    QTabWidget, QComboBox,
)
from PyQt5.QtCore import QThread, pyqtSignal


class _PhysicsWorker(QThread):
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)
    def __init__(self, fn, kwargs):
        super().__init__()
        self._fn, self._kw = fn, kwargs
    def run(self):
        try:
            self.finished.emit(self._fn(**self._kw))
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())



def _fit_tab_height(tabs):
    """Cap tab widget to current tab content height, eliminating empty space."""
    from PyQt5.QtCore import QTimer as _QT
    def _do():
        w = tabs.currentWidget()
        if w:
            tabs.setMaximumHeight(tabs.tabBar().sizeHint().height() + w.sizeHint().height() + 12)
    tabs.currentChanged.connect(lambda _: _do())
    _QT.singleShot(0, _do)

def _add_condensate_physics(ui_instance, layout=None, separate_widget=False):
    outer = QVBoxLayout()
    outer.setSpacing(6)
    outer.setContentsMargins(2, 2, 2, 2)
    # Top-level section header sized to match the enumerated step titles (14px).
    # Not a numbered checklist step, so no "Step N —" prefix — just the name.
    from PyQt5.QtWidgets import QLabel as _QLabel
    _cp_hdr = _QLabel("<span style='font-weight:700;'>Condensate Biophysics</span>")
    from PyQt5.QtCore import Qt as _Qt
    _cp_hdr.setTextFormat(_Qt.RichText)
    _cp_hdr.setStyleSheet("font-size: 14px; margin-top: 4px;")
    _cp_hdr.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
    outer.addWidget(_cp_hdr)
    # Optional block: reveal checkbox (off by default) shows/hides the analyses.
    from PyQt5.QtWidgets import QCheckBox as _CPCheckBox
    _cp_reveal = _CPCheckBox("Show condensate biophysics (optional)")
    _cp_reveal.setChecked(False)
    _cp_reveal.setToolTip("Optional MSD/diffusion, intensity decomposition, "
                          "kinetics, and QC analyses. Enable to configure and run.")
    outer.addWidget(_cp_reveal)
    tabs = QTabWidget()
    tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    # Most biophysics tabs (MSD, Kinetics, QC/Bleach, Survival) need a (T,H,W)
    # time stack; only Intensity/Csat is static. Time tabs are added/removed
    # dynamically based on whether a time stack is loaded.
    def _has_time_stack():
        try:
            for lyr in ui_instance.viewer.layers:
                data = getattr(lyr, 'data', None)
                if data is not None and getattr(data, 'ndim', 0) >= 3:
                    return True
        except Exception:
            pass
        return False
    _fit_tab_height(tabs)

    # ── Tab 1: MSD / Diffusion ───────────────────────────────────────────
    msd_w = QWidget()
    msd_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    mf = QFormLayout(msd_w)
    mf.setContentsMargins(4, 4, 4, 4)
    mf.setSpacing(5)

    stack_dd_msd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    mf.addRow("Condensate mask stack (T,H,W):", stack_dd_msd)

    dt_spin  = QDoubleSpinBox(); dt_spin.setRange(0.01, 3600); dt_spin.setValue(1.0)
    lag_spin = QSpinBox();       lag_spin.setRange(2, 500);    lag_spin.setValue(0)
    lag_spin.setToolTip("Max lag frames (0 = auto: n_frames/4)")
    min_len  = QSpinBox();       min_len.setRange(3, 50);      min_len.setValue(5)
    min_len.setToolTip("Minimum trajectory length (frames). Shorter tracks are excluded from the MSD.")
    mf.addRow("Frame interval (s):", dt_spin)
    mf.addRow("Max lag frames (0=auto):", lag_spin)
    mf.addRow("Min track length:", min_len)

    per_track_cb = QCheckBox("Also fit per-track diffusion")
    per_track_cb.setToolTip("Also fit D and α for each individual track (slower; adds a per-track table).")
    per_track_cb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    per_track_cb.setChecked(True)
    mf.addRow(per_track_cb)

    prog_msd = QProgressBar(); prog_msd.setVisible(False)
    run_msd  = QPushButton("▶  Run MSD Analysis")
    run_msd.setToolTip("Compute the ensemble MSD, fit anomalous diffusion (D, α), and plot the per-track and mean MSD curves.")
    run_msd.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    mf.addRow(prog_msd)
    from pycat.ui.field_status import button_with_circle as _bwc
    mf.addRow(_bwc(run_msd, optional=True))

    def _on_msd():
        from pycat.toolbox.condensate_physics_tools import (
            compute_msd, fit_anomalous_diffusion, msd_per_track)
        from pycat.toolbox.dynamic_spatial_tools import (
            extract_frame_properties, link_trajectories_bayesian)
        try:
            stack = ui_instance.viewer.layers[stack_dd_msd.currentText()].data
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return
        if stack.ndim != 3:
            napari_show_warning("MSD needs a 3D (T,H,W) label stack."); return
        dr  = ui_instance.central_manager.active_data_class.data_repository
        mpx = float(dr.get('microns_per_pixel_sq', 1.0))**0.5
        prog_msd.setVisible(True); prog_msd.setRange(0,0); run_msd.setEnabled(False)

        def _task():
            props  = extract_frame_properties(np.asarray(stack), mpx)
            tracks = link_trajectories_bayesian(props,
                         max_displacement_um=float(dr.get('object_size', 3))*mpx*2)
            max_l  = lag_spin.value() or None
            msd_df = compute_msd(tracks, max_lag=max_l,
                                  frame_interval_s=dt_spin.value(),
                                  min_track_length=min_len.value())
            fit    = fit_anomalous_diffusion(msd_df)
            res    = {'msd': msd_df, 'fit': fit}
            from pycat.toolbox.condensate_physics_tools import per_track_msd_curves
            res['track_curves'] = per_track_msd_curves(
                tracks, frame_interval_s=dt_spin.value(),
                min_track_length=min_len.value())
            if per_track_cb.isChecked():
                res['per_track'] = msd_per_track(tracks, dt_spin.value(), min_len.value())
            return res

        worker = _PhysicsWorker(_task, {})
        ui_instance._msd_worker = worker

        def _done(res):
            prog_msd.setVisible(False); run_msd.setEnabled(True)
            dr['msd_results'] = res['msd']
            fit = res['fit']
            from pycat.ui.ui_utils import show_dataframes_dialog
            tables = [("MSD vs lag", res['msd'].round(4))]
            fit_df = pd.DataFrame([{k: v for k, v in fit.items()
                                    if not hasattr(v, '__len__')}])
            tables.append(("Anomalous diffusion fit", fit_df.round(4)))
            if 'per_track' in res:
                tables.append(("Per-track D and α", res['per_track'].round(4)))
                dr['msd_per_track'] = res['per_track']
            # Graph: MSD spaghetti (per-track faint + ensemble mean + fit).
            try:
                from pycat.toolbox.analysis_plots import plot_msd_trajectories
                plot_msd_trajectories(res.get('track_curves'), res['msd'], fit,
                                      title="Condensate MSD", interactive=True)
            except Exception as e:
                print(f"[PyCAT] MSD plot failed: {e}")
            show_dataframes_dialog("MSD Analysis", tables)
            napari_show_info(
                f"MSD: D={fit.get('D_um2_per_s', np.nan):.4f} µm²/s, "
                f"α={fit.get('alpha', np.nan):.3f} ({fit.get('motion_type','?')})"
            )
            ui_instance._record('msd_analysis', {'stack': stack_dd_msd.currentText(),
                                                   'dt': dt_spin.value()})
        def _err(msg):
            prog_msd.setVisible(False); run_msd.setEnabled(True)
            napari_show_warning("MSD error — see terminal.")
            print(f"[PyCAT MSD] {msg}")
        worker.finished.connect(_done); worker.error.connect(_err); worker.start()

    run_msd.clicked.connect(_on_msd)
    # MSD, Kinetics, QC/Bleach, Survival are time-dependent — added by
    # _sync_time_tabs() below only when a (T,H,W) stack is present.

    # ── Tab 2: Intensity decomposition ───────────────────────────────────
    hist_w = QWidget(); hf = QFormLayout(hist_w)

    img_dd   = ui_instance.create_layer_dropdown(napari.layers.Image)
    cell_dd  = ui_instance.create_layer_dropdown(napari.layers.Labels)
    hf.addRow("Fluorescence image:", img_dd)
    hf.addRow("Labeled cell mask:", cell_dd)

    bins_spin = QSpinBox(); bins_spin.setRange(32, 512); bins_spin.setValue(256)
    bins_spin.setToolTip("Histogram bins for the two-population (dilute vs dense) intensity fit.")
    hf.addRow("Histogram bins:", bins_spin)

    prog_hist = QProgressBar(); prog_hist.setVisible(False)
    run_hist  = QPushButton("▶  Fit Bimodal Intensity")
    run_hist.setToolTip("Fit a two-Gaussian model per cell to separate dilute- and dense-phase intensities.")
    run_hist.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    hf.addRow(prog_hist)
    from pycat.ui.field_status import button_with_circle as _bwc
    hf.addRow(_bwc(run_hist, optional=True))

    def _on_hist():
        from pycat.toolbox.condensate_physics_tools import intensity_decomposition_per_cell
        try:
            image = ui_instance.viewer.layers[img_dd.currentText()].data
            cells = ui_instance.viewer.layers[cell_dd.currentText()].data
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return
        dr  = ui_instance.central_manager.active_data_class.data_repository
        mpx = float(dr.get('microns_per_pixel_sq', 1.0))**0.5
        prog_hist.setVisible(True); prog_hist.setRange(0,0); run_hist.setEnabled(False)

        def _task():
            img_f = np.asarray(image).astype(np.float32)
            mn, mx = img_f.min(), img_f.max()
            if mx > mn: img_f = (img_f - mn)/(mx - mn)
            return intensity_decomposition_per_cell(img_f, np.asarray(cells), mpx)

        worker = _PhysicsWorker(_task, {})
        ui_instance._hist_worker = worker
        def _done(df):
            prog_hist.setVisible(False); run_hist.setEnabled(True)
            dr['intensity_decomposition'] = df
            from pycat.ui.ui_utils import show_dataframes_dialog
            show_dataframes_dialog("Intensity Decomposition", [("Per-cell bimodal fit", df.round(4))])
            napari_show_info(f"Bimodal fit complete: {len(df)} cells.")
        def _err(msg):
            prog_hist.setVisible(False); run_hist.setEnabled(True)
            napari_show_warning("Histogram fit error — see terminal.")
            print(f"[PyCAT Hist] {msg}")
        worker.finished.connect(_done); worker.error.connect(_err); worker.start()

    run_hist.clicked.connect(_on_hist)
    tabs.addTab(hist_w, "Intensity / Csat")

    # ── Tab 3: Kinetics ──────────────────────────────────────────────────
    kin_w = QWidget(); kf = QFormLayout(kin_w)
    _wl = QLabel("Coarsening kinetics — uses timeseries_condensate_df from TS Analysis."); _wl.setWordWrap(True)
    kf.addRow(_wl)
    dt_kin = QDoubleSpinBox(); dt_kin.setRange(0.01, 3600); dt_kin.setValue(1.0)
    kf.addRow("Frame interval (s):", dt_kin)
    run_coarse = QPushButton("▶  Fit Coarsening Kinetics")
    run_coarse.setToolTip("Fit mean radius vs time to t^⅓ (Ostwald) and t^½ (coalescence); reports the preferred mechanism and confidence.")
    run_coarse.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    from pycat.ui.field_status import button_with_circle as _bwc
    kf.addRow(_bwc(run_coarse, optional=True))

    # Fusion relaxation controls
    fus_stack_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    kf.addRow("Fusion mask stack (T,H,W):", fus_stack_dd)
    fus_R = QDoubleSpinBox(); fus_R.setRange(0.0, 100.0); fus_R.setDecimals(3)
    fus_R.setValue(0.0); fus_R.setSuffix(" µm")
    fus_R.setToolTip("Characteristic droplet length R for η/γ = τ/R. "
                     "0 = use the merged droplet's equivalent radius automatically.")
    kf.addRow("Fusion length R:", fus_R)
    run_fusion = QPushButton("▶  Fit Fusion Relaxation (from merge events)")
    run_fusion.setToolTip("Detect droplet merges, follow the merged droplet's aspect-ratio relaxation, and fit τ (and η/γ if R is set).")
    run_fusion.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    kf.addRow(_bwc(run_fusion, optional=True))

    def _on_coarsen():
        from pycat.toolbox.condensate_physics_tools import fit_coarsening
        dr = ui_instance.central_manager.active_data_class.data_repository
        ts_df = dr.get('timeseries_condensate_df')
        if ts_df is None or ts_df.empty:
            napari_show_warning("Run Time-Series Condensate Analysis first."); return
        # Mean radius per frame (from mean_condensate_area_um2)
        summary = ts_df.groupby('frame')['mean_condensate_area_um2'].mean()
        time_s  = summary.index.values * dt_kin.value()
        r_mean  = np.sqrt(summary.values / np.pi)
        res = fit_coarsening(time_s, r_mean)
        from pycat.ui.ui_utils import show_dataframes_dialog
        df = pd.DataFrame([{k: v for k, v in res.items() if not hasattr(v,'__len__') or isinstance(v, str)}])
        show_dataframes_dialog("Coarsening Kinetics", [("Coarsening fit", df.round(4))])
        try:
            from pycat.toolbox.analysis_plots import plot_coarsening
            plot_coarsening(time_s, r_mean, res, interactive=True)
        except Exception as e:
            print(f"[PyCAT] coarsening plot failed: {e}")
        mech = res.get('preferred_mechanism', '?')
        conf = res.get('mechanism_confidence', '')
        cav = res.get('mechanism_caveat', '')
        msg = f"Preferred mechanism: {mech} (confidence: {conf})."
        if cav:
            msg += f" {cav}"
        (napari_show_warning if conf == 'low' else napari_show_info)(msg)

    def _on_fusion():
        from pycat.toolbox.condensate_physics_tools import (
            extract_fusion_relaxation, fit_aspect_ratio_relaxation)
        dr = ui_instance.central_manager.active_data_class.data_repository
        name = fus_stack_dd.currentText()
        if name not in [l.name for l in ui_instance.viewer.layers]:
            napari_show_warning("Select a labelled condensate mask stack first."); return
        stack = np.asarray(ui_instance.viewer.layers[name].data)
        if stack.ndim != 3:
            napari_show_warning("Fusion needs a 3D (T,H,W) mask stack."); return
        mpx = float(dr.get('microns_per_pixel_sq', 1.0)) ** 0.5
        events = extract_fusion_relaxation(
            stack, microns_per_pixel=mpx, frame_interval_s=dt_kin.value())
        if not events:
            napari_show_warning("No usable merge events found (need a merge that "
                                "persists several frames)."); return
        # Fit the event with the largest initial aspect ratio (clearest fusion).
        ev = max(events, key=lambda e: float(np.max(e['aspect_ratio'])))
        R = fus_R.value() if fus_R.value() > 0 else ev['R_um']
        fit = fit_aspect_ratio_relaxation(
            ev['time_s'], ev['aspect_ratio'], characteristic_length_um=R)
        dr['fusion_relaxation_fit'] = fit
        try:
            from pycat.toolbox.analysis_plots import plot_fusion_relaxation
            plot_fusion_relaxation(ev['time_s'], ev['aspect_ratio'], fit,
                                   interactive=True)
        except Exception as e:
            print(f"[PyCAT] fusion plot failed: {e}")
        if fit.get('fit_success'):
            eg = fit.get('eta_over_gamma_s_per_um', np.nan)
            napari_show_info(
                f"Fusion: τ={fit['tau_s']:.3g}s, R={R:.2g}µm, "
                f"η/γ={eg:.3g} s/µm (R²={fit['r_squared']:.2f}); "
                f"{len(events)} merge event(s) found.")
        else:
            napari_show_warning("Fusion fit did not converge — the merge may be "
                                "too short or noisy.")

    run_coarse.clicked.connect(_on_coarsen)
    run_fusion.clicked.connect(_on_fusion)
    # (time-dependent — added by _sync_time_tabs when a time stack is present)

    # ── Tab 4: Quality Control ───────────────────────────────────────────
    # Uses analyse_frame_quality() which computes 4 metrics per frame:
    #   mean_intensity  → bleaching signal (exponential decay)
    #   laplacian_variance → sharpness (focal drift → decreases)
    #   image_entropy      → information content (focal drift → decreases)
    #   gradient_energy    → edge strength (focal drift → decreases)
    # Entropy is particularly useful because it is robust to shot noise
    # in dim frames that can artificially inflate Laplacian variance.

    qc_w = QWidget(); qf = QFormLayout(qc_w)

    stack_dd_qc = ui_instance.create_layer_dropdown(napari.layers.Image)
    qf.addRow("Image stack (T,H,W):", stack_dd_qc)

    dt_qc  = QDoubleSpinBox(); dt_qc.setRange(0.01, 3600); dt_qc.setValue(1.0)
    thr_qc = QDoubleSpinBox(); thr_qc.setRange(0.01, 0.9);  thr_qc.setValue(0.3)
    thr_qc.setToolTip(
        "Frames with Laplacian variance AND entropy below this fraction\n"
        "of their median are flagged as blurry/out-of-focus.\n"
        "Both metrics must agree to reduce false positives from\n"
        "genuinely dim/sparse frames."
    )
    bleach_r2_spin = QDoubleSpinBox()
    bleach_r2_spin.setRange(0.1, 1.0); bleach_r2_spin.setValue(0.70)
    bleach_r2_spin.setToolTip(
        "Minimum R² of exponential fit to mean intensity for\n"
        "bleaching to be declared.  Lower = more permissive."
    )
    drift_slope_spin = QDoubleSpinBox()
    drift_slope_spin.setRange(-1.0, 0.0); drift_slope_spin.setValue(-0.05)
    drift_slope_spin.setDecimals(3)
    drift_slope_spin.setToolTip(
        "Normalised linear slope threshold for focal drift.\n"
        "If entropy or Laplacian variance decline by more than\n"
        "this fraction of their range over the movie, drift is flagged.\n"
        "E.g. -0.05 = 5% decline triggers drift flag."
    )
    qf.addRow("Frame interval (s):", dt_qc)
    qf.addRow("Blurry threshold fraction:", thr_qc)
    qf.addRow("Bleach min R²:", bleach_r2_spin)
    qf.addRow("Drift slope threshold:", drift_slope_spin)

    apply_cb = QCheckBox("Apply bleaching correction (adds corrected layer)")
    apply_cb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    apply_cb.setChecked(False)
    apply_cb.setToolTip(
        "When bleaching is detected, multiply each frame by the\n"
        "fitted correction factor I(0)/I(t) and add the result\n"
        "as a new layer.  Does not correct focal drift."
    )
    qf.addRow(apply_cb)

    prog_qc = QProgressBar(); prog_qc.setVisible(False)
    prog_qc.setFormat("Computing frame metrics… %p%")
    status_qc = QLabel("")
    status_qc.setWordWrap(True)
    status_qc.setStyleSheet("color: #aaa; font-size: 9pt;")
    run_qc  = QPushButton("▶  Run QC Analysis")
    run_qc.setToolTip("Compute per-frame quality metrics: mean intensity (bleaching), sharpness (focus), and drift.")
    run_qc.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    qf.addRow(prog_qc); qf.addRow(status_qc)
    from pycat.ui.field_status import button_with_circle as _bwc
    qf.addRow(_bwc(run_qc, optional=True))

    def _on_qc():
        from pycat.toolbox.condensate_physics_tools import (
            analyse_frame_quality, apply_bleach_correction)
        try:
            layer = ui_instance.viewer.layers[stack_dd_qc.currentText()]
            stack = np.asarray(layer.data).astype(np.float32)
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return
        if stack.ndim != 3:
            napari_show_warning("QC needs a 3D (T,H,W) image stack."); return

        prog_qc.setMaximum(stack.shape[0])
        prog_qc.setValue(0); prog_qc.setVisible(True)
        status_qc.setText("Analysing frames…"); run_qc.setEnabled(False)

        def _task():
            result = analyse_frame_quality(
                stack,
                frame_interval_s=dt_qc.value(),
                threshold_fraction=thr_qc.value(),
                bleach_r2_min=bleach_r2_spin.value(),
                drift_slope_threshold=drift_slope_spin.value(),
            )
            if apply_cb.isChecked():
                result['corrected_stack'] = apply_bleach_correction(
                    stack, result['bleach_correction_factors'])
            return result

        worker = _PhysicsWorker(_task, {})
        ui_instance._qc_worker = worker

        def _done(result):
            prog_qc.setVisible(False); run_qc.setEnabled(True)
            dr  = ui_instance.central_manager.active_data_class.data_repository
            df  = result['per_frame_df']
            summ = result['summary']
            dr['frame_quality_df']   = df
            dr['frame_quality_summary'] = summ

            # Summary row as DataFrame for display
            summ_df = pd.DataFrame([{
                k: v for k, v in summ.items() if k != 'recommendation'
            }]).round(4)

            from pycat.ui.ui_utils import show_dataframes_dialog
            tables = [
                ("QC Summary", summ_df),
                ("Per-frame metrics", df.round(4)),
            ]
            show_dataframes_dialog("Frame Quality Analysis", tables)

            # Add corrected stack if computed
            if 'corrected_stack' in result:
                ui_instance.viewer.add_image(
                    result['corrected_stack'],
                    name=f"Bleach-Corrected {layer.name}",
                    colormap='viridis')

            # Status message with cause and recommendation
            cause = summ['dominant_cause']
            cause_colors = {
                'clean':          '#5cb85c',   # green
                'bleaching_only': '#f0a500',   # amber
                'drift_only':     '#d9534f',   # red
                'both':           '#d9534f',
                'undetermined':   '#aaa',
            }
            _qc_color = cause_colors.get(cause, '#aaa')
            status_qc.setText(
                f"<span style='color:{_qc_color}'>"
                f"Diagnosis: {cause.replace('_',' ').upper()}</span>"
            )

            # Napari notification
            n_blur = summ['n_blurry_frames']
            if cause == 'clean':
                napari_show_info("Frame QC: clean — no bleaching or focal drift detected.")
            else:
                napari_show_warning(
                    f"Frame QC: {cause.replace('_',' ')}. "
                    f"{n_blur} blurry frame(s). {summ['recommendation']}"
                )

        def _err(msg):
            prog_qc.setVisible(False); run_qc.setEnabled(True)
            napari_show_warning("QC error — see terminal.")
            print(f"[PyCAT QC] {msg}")

        worker.finished.connect(_done); worker.error.connect(_err); worker.start()

    run_qc.clicked.connect(_on_qc)
    # (time-dependent — added by _sync_time_tabs when a time stack is present)

    # ── Survival analysis ─────────────────────────────────────────────────
    surv_w = QWidget(); sf = QFormLayout(surv_w)
    _wl = QLabel("Kaplan-Meier lifetime survival — uses dynamic tracking results."); _wl.setWordWrap(True)
    sf.addRow(_wl)
    total_frames_spin = QSpinBox(); total_frames_spin.setRange(1,9999); total_frames_spin.setValue(600)
    total_frames_spin.setToolTip("Total frames in the movie — condensates still present at the end are right-censored in the survival fit.")
    sf.addRow("Total frames in movie:", total_frames_spin)
    run_surv = QPushButton("▶  Kaplan-Meier Survival")
    run_surv.setToolTip("Kaplan–Meier survival curve of condensate lifetimes (from dynamic tracking), with right-censoring.")
    run_surv.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    from pycat.ui.field_status import button_with_circle as _bwc
    sf.addRow(_bwc(run_surv, optional=True))

    def _on_surv():
        from pycat.toolbox.condensate_physics_tools import kaplan_meier_lifetimes
        dr = ui_instance.central_manager.active_data_class.data_repository
        tracks = dr.get('dyn_trajectories')
        if tracks is None:
            napari_show_warning("Run Dynamic Spatial Analysis (tracking) first."); return
        km = kaplan_meier_lifetimes(tracks, total_frames_spin.value())
        dr['km_survival'] = km
        from pycat.ui.ui_utils import show_dataframes_dialog
        try:
            from pycat.toolbox.analysis_plots import plot_km_survival
            plot_km_survival(km, interactive=True)
        except Exception as e:
            print(f"[PyCAT] KM plot failed: {e}")
        show_dataframes_dialog("Kaplan-Meier Survival", [("KM curve", km.round(4))])
        napari_show_info(
            f"KM: median lifetime = {km.attrs.get('median_lifetime_frames','?')} frames, "
            f"mean = {km.attrs.get('mean_lifetime_frames',np.nan):.1f} frames")

    run_surv.clicked.connect(_on_surv)
    # (time-dependent — added by _sync_time_tabs when a time stack is present)

    def _sync_time_tabs():
        # Add/remove the time-dependent tabs to match time-stack presence.
        has_t = _has_time_stack()
        specs = [(msd_w, "MSD / Diffusion"), (kin_w, "Kinetics"),
                 (qc_w, "QC / Bleach"), (surv_w, "Survival")]
        for w, name in specs:
            idx = tabs.indexOf(w)
            if has_t and idx == -1:
                tabs.addTab(w, name)
            elif not has_t and idx != -1:
                tabs.removeTab(idx)
    _sync_time_tabs()
    try:
        ui_instance.viewer.layers.events.inserted.connect(lambda *_: _sync_time_tabs())
        ui_instance.viewer.layers.events.removed.connect(lambda *_: _sync_time_tabs())
    except Exception:
        pass

    outer.addWidget(tabs)
    tabs.setVisible(False)
    _cp_reveal.toggled.connect(tabs.setVisible)
    widget = QWidget()
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    widget.setLayout(outer)
    ui_instance._add_widget_to_layout_or_dock(
        widget, layout, separate_widget, "Condensate Biophysics"
    )
