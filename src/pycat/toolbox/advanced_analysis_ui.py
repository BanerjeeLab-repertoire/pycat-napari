"""
PyCAT Advanced Analysis UI
============================
Tabbed widget for Morphological Complexity, Dynamic Spatial Phenotyping,
and Organizational Metrics analyses.

Sits in the pipeline after condensate analysis (static) or after time-series
condensate analysis (dynamic).
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
    QTabWidget, QComboBox,
)
from PyQt5.QtCore import QThread, pyqtSignal


class _AdvancedAnalysisWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, task, kwargs):
        super().__init__()
        self._task   = task
        self._kwargs = kwargs

    def run(self):
        try:
            result = self._task(**self._kwargs)
            self.finished.emit(result)
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


def _add_advanced_analysis(ui_instance, layout=None, separate_widget=False):
    """
    Tabbed widget with three sections:
      Tab 1 — Morphological Complexity
      Tab 2 — Dynamic Spatial Phenotyping (time-series only)
      Tab 3 — Organizational Metrics
    """
    outer = QVBoxLayout()
    ui_instance.add_text_label(outer, 'Advanced Condensate Analysis', bold=True)
    tabs = QTabWidget()

    # ── Tab 1: Morphological Complexity ─────────────────────────────────
    morph_widget = QWidget()
    mf = QFormLayout(morph_widget)

    punc_dd_m = ui_instance.create_layer_dropdown(napari.layers.Labels)
    cell_dd_m = ui_instance.create_layer_dropdown(napari.layers.Labels)
    mf.addRow("Condensate mask:", punc_dd_m)
    mf.addRow("Labeled cell mask:", cell_dd_m)

    cb_fd   = QCheckBox("Fractal dimension (box-counting)"); cb_fd.setChecked(True)
    cb_lac  = QCheckBox("Lacunarity"); cb_lac.setChecked(True)
    cb_tort = QCheckBox("Tortuosity (fibrillar structures)"); cb_tort.setChecked(False)
    cb_orient = QCheckBox("Orientation / anisotropy order parameter"); cb_orient.setChecked(True)
    for cb in (cb_fd, cb_lac, cb_tort, cb_orient):
        mf.addRow(cb)

    prog_m = QProgressBar(); prog_m.setVisible(False)
    run_m  = QPushButton("▶  Run Morphological Analysis")
    mf.addRow(prog_m); mf.addRow(run_m)

    def _on_morph():
        from pycat.toolbox.morphological_complexity_tools import (
            fractal_dimension_per_cell, lacunarity,
            tortuosity_per_object, orientation_order_parameter,
        )
        try:
            pmask = ui_instance.viewer.layers[punc_dd_m.currentText()].data
            cmask = ui_instance.viewer.layers[cell_dd_m.currentText()].data
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return

        dr  = ui_instance.central_manager.active_data_class.data_repository
        mpx = float(dr.get('microns_per_pixel_sq', 1.0))**0.5
        results = {}
        n_cells = len(np.unique(cmask)) - 1
        prog_m.setMaximum(max(n_cells, 1)); prog_m.setValue(0); prog_m.setVisible(True)
        run_m.setEnabled(False)

        def _task():
            if cb_fd.isChecked():
                results['fractal_dimension'] = fractal_dimension_per_cell(pmask, cmask)
            if cb_lac.isChecked():
                # Whole-image lacunarity
                results['lacunarity'] = lacunarity(pmask > 0)
            if cb_tort.isChecked():
                import skimage as sk
                lp = sk.measure.label(pmask > 0)
                results['tortuosity'] = tortuosity_per_object(lp, mpx)
            if cb_orient.isChecked():
                import skimage as sk
                lp = sk.measure.label(pmask > 0)
                r  = orientation_order_parameter(lp)
                results['orientation_summary'] = pd.DataFrame([{
                    'S_order_parameter':  r['S'],
                    'circular_variance':  r['circular_variance'],
                    'preferred_angle_deg':r['preferred_angle_deg'],
                    'mean_eccentricity':  r['mean_eccentricity'],
                    'mean_anisotropy':    r['mean_anisotropy'],
                }])
                results['orientation_per_object'] = r['per_object_df']
            return results

        worker = _AdvancedAnalysisWorker(_task, {})
        ui_instance._morph_worker = worker

        def _done(res):
            prog_m.setVisible(False); run_m.setEnabled(True)
            for k, v in res.items():
                dr[f'morph_{k}'] = v
            from pycat.ui.ui_utils import show_dataframes_dialog
            tables = [(k.replace('_',' ').title(), v.round(4) if hasattr(v,'round') else v)
                      for k, v in res.items()]
            show_dataframes_dialog("Morphological Complexity", tables)
            ui_instance._record('morphological_complexity',
                                {'analyses': [k for k, c in
                                  [('fd', cb_fd), ('lac', cb_lac),
                                   ('tort', cb_tort), ('orient', cb_orient)]
                                  if c.isChecked()]})
            napari_show_info("Morphological complexity analysis complete.")

        def _err(msg):
            prog_m.setVisible(False); run_m.setEnabled(True)
            napari_show_warning("Morphological analysis error — see terminal.")
            print(f"[PyCAT Morph] ERROR:\n{msg}")

        worker.finished.connect(_done); worker.error.connect(_err)
        worker.start()

    run_m.clicked.connect(_on_morph)
    tabs.addTab(morph_widget, "Morphological")

    # ── Tab 2: Dynamic Spatial Phenotyping ──────────────────────────────
    dyn_widget = QWidget()
    df = QFormLayout(dyn_widget)

    stack_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    df.addRow("TS condensate mask stack (T,H,W):", stack_dd)

    from PyQt5.QtWidgets import QComboBox as _CB2
    linker_dd = _CB2()
    linker_dd.addItems([
        "Bayesian (Hungarian, recommended)",
        "Greedy NNL (fast, simple)",
        "TrackMate LAP (via pyimagej, requires Fiji)",
    ])
    linker_dd.wheelEvent = lambda e: (
        linker_dd.__class__.wheelEvent(linker_dd, e)
        if linker_dd.view().isVisible() else e.ignore()
    )
    df.addRow("Linking algorithm:", linker_dd)

    max_disp = QDoubleSpinBox(); max_disp.setRange(0.1, 20); max_disp.setValue(2.0)
    max_gap  = QSpinBox();       max_gap.setRange(0, 5);    max_gap.setValue(2)
    sigma_spin = QDoubleSpinBox(); sigma_spin.setRange(0.1, 10); sigma_spin.setValue(0.5)
    sigma_spin.setToolTip(
        "Expected displacement per frame (µm) — Gaussian std for Bayesian linker.\n"
        "Set to typical condensate diffusion step size.\n"
        "Ignored for greedy NNL."
    )
    area_w_spin = QDoubleSpinBox(); area_w_spin.setRange(0, 2); area_w_spin.setValue(0.3)
    area_w_spin.setToolTip(
        "Weight of area consistency in linking cost (Bayesian only).\n"
        "0 = ignore area, 1 = equal weight to distance.\n"
        "Higher values discourage linking condensates of very different sizes."
    )
    vel_check = QCheckBox("Velocity-assisted linking (EWM predicted position)")
    vel_check.setChecked(True)
    vel_check.setToolTip(
        "Maintain an exponentially-weighted velocity estimate per track.\n"
        "Improves linking for condensates undergoing directed motion.\n"
        "Bayesian only."
    )
    frame_dt = QDoubleSpinBox(); frame_dt.setRange(0.01, 3600); frame_dt.setValue(1.0)
    prox_um  = QDoubleSpinBox(); prox_um.setRange(0.1, 10);  prox_um.setValue(1.0)
    nb_rad   = QDoubleSpinBox(); nb_rad.setRange(0.5, 20);   nb_rad.setValue(3.0)
    df.addRow("Max displacement (µm):", max_disp)
    df.addRow("Max gap frames:", max_gap)
    df.addRow("Motion sigma (µm/frame, Bayesian):", sigma_spin)
    df.addRow("Area weight (Bayesian):", area_w_spin)
    df.addRow("", vel_check)
    df.addRow("Frame interval (s):", frame_dt)
    df.addRow("Merge/fission proximity (µm):", prox_um)
    df.addRow("Neighbourhood radius (µm):", nb_rad)

    # TrackMate-specific settings
    tm_gap_dist = QDoubleSpinBox(); tm_gap_dist.setRange(0.1, 50); tm_gap_dist.setValue(3.0)
    tm_gap_dist.setToolTip("TrackMate gap-closing max distance (µm).")
    tm_merge_cb = QCheckBox("Allow track merging (TrackMate LAP)")
    tm_merge_cb.setChecked(True)
    tm_split_cb = QCheckBox("Allow track splitting (TrackMate LAP)")
    tm_split_cb.setChecked(True)
    tm_kalman_cb = QCheckBox("Use Kalman tracker instead of LAP")
    tm_kalman_cb.setChecked(False)
    tm_kalman_cb.setToolTip(
        "TrackMate's Kalman tracker propagates position+velocity state "
        "with real uncertainty — better for persistent directed motion, "
        "but does not model merge/split events (LAP does)."
    )
    tm_note = QLabel(
        "<span style='color:#888;font-size:9pt;'>"
        "Runs real TrackMate (Jaqaman LAP tracker) via an embedded Fiji "
        "JVM. First use downloads Fiji (~minutes, one-time, needs network). "
        "Requires: pip install pycat-napari[trackmate] + a Java runtime "
        "(JDK 11+, not installed by pip \u2014 use conda/OS package manager)."
        "</span>"
    )
    tm_note.setWordWrap(True)
    df.addRow(tm_note)
    df.addRow("Gap-closing max distance (µm):", tm_gap_dist)
    df.addRow(tm_merge_cb)
    df.addRow(tm_split_cb)
    df.addRow(tm_kalman_cb)

    # Show/hide linker-specific params
    def _on_linker_changed():
        idx = linker_dd.currentIndex()
        is_bayes = idx == 0
        is_trackmate = idx == 2
        sigma_spin.setEnabled(is_bayes)
        area_w_spin.setEnabled(is_bayes)
        vel_check.setEnabled(is_bayes)
        tm_gap_dist.setEnabled(is_trackmate)
        tm_merge_cb.setEnabled(is_trackmate)
        tm_split_cb.setEnabled(is_trackmate)
        tm_kalman_cb.setEnabled(is_trackmate)
        tm_note.setVisible(is_trackmate)
    linker_dd.currentIndexChanged.connect(_on_linker_changed)
    _on_linker_changed()

    cb_track = QCheckBox("Trajectory tracking + metrics"); cb_track.setChecked(True)
    cb_mf    = QCheckBox("Merge / fission detection");     cb_mf.setChecked(True)
    cb_life  = QCheckBox("Cluster lifetime analysis");     cb_life.setChecked(True)
    cb_nb    = QCheckBox("Neighbourhood persistence");     cb_nb.setChecked(True)
    cb_grow  = QCheckBox("Growth / shrinkage kinetics");   cb_grow.setChecked(True)
    for cb in (cb_track, cb_mf, cb_life, cb_nb, cb_grow):
        df.addRow(cb)

    prog_d = QProgressBar(); prog_d.setVisible(False)
    run_d  = QPushButton("▶  Run Dynamic Analysis")
    df.addRow(prog_d); df.addRow(run_d)

    def _on_dynamic():
        from pycat.toolbox.dynamic_spatial_tools import (
            extract_frame_properties, link_trajectories,
            trajectory_metrics, detect_merge_fission,
            cluster_lifetime_analysis, neighbourhood_persistence,
            growth_shrinkage_kinetics,
        )
        try:
            stack = ui_instance.viewer.layers[stack_dd.currentText()].data
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return

        if stack.ndim != 3:
            napari_show_warning("Dynamic analysis needs a 3D (T,H,W) mask stack."); return

        dr  = ui_instance.central_manager.active_data_class.data_repository
        mpx = float(dr.get('microns_per_pixel_sq', 1.0))**0.5
        prog_d.setMaximum(5); prog_d.setValue(0); prog_d.setVisible(True)
        run_d.setEnabled(False)

        def _task():
            res = {}
            props = extract_frame_properties(np.asarray(stack), mpx)
            tracks = None
            prog_d.setValue(1)

            if cb_track.isChecked() or cb_life.isChecked() or cb_nb.isChecked() or cb_grow.isChecked():
                linker_idx = linker_dd.currentIndex()
                if linker_idx == 0:
                    from pycat.toolbox.dynamic_spatial_tools import link_trajectories_bayesian
                    tracks = link_trajectories_bayesian(
                        props,
                        max_displacement_um=max_disp.value(),
                        max_gap_frames=max_gap.value(),
                        sigma_um=sigma_spin.value(),
                        area_weight=area_w_spin.value(),
                        use_velocity=vel_check.isChecked(),
                    )
                elif linker_idx == 2:
                    from pycat.toolbox.trackmate_bridge import (
                        trackmate_bridge_available, run_trackmate_lap_tracking)
                    if not trackmate_bridge_available():
                        raise ImportError(
                            "TrackMate bridge requires pyimagej. Install with:\n"
                            "  pip install pycat-napari[trackmate]\n"
                            "  (or directly: pip install pyimagej)\n"
                            "and ensure a Java runtime (JDK 11+) is on PATH "
                            "(pip does not install Java \u2014 use conda "
                            "install openjdk=11 or your OS package manager), "
                            "then retry.")
                    tm_result = run_trackmate_lap_tracking(
                        props,
                        max_linking_distance_um=max_disp.value(),
                        max_gap_closing_distance_um=tm_gap_dist.value(),
                        max_frame_gap=max_gap.value(),
                        allow_merging=tm_merge_cb.isChecked(),
                        allow_splitting=tm_split_cb.isChecked(),
                        use_kalman=tm_kalman_cb.isChecked(),
                    )
                    # Drop any spots TrackMate left unlinked (track_id == -1)
                    # so downstream trajectory functions never see an
                    # ambiguous "-1" pseudo-track.
                    tracks = tm_result[tm_result['track_id'] != -1].reset_index(drop=True)
                else:
                    tracks = link_trajectories(props, max_disp.value(), max_gap.value())
                if cb_track.isChecked():
                    res['trajectories']    = tracks
                    res['trajectory_metrics'] = trajectory_metrics(tracks)
                prog_d.setValue(2)

            if cb_mf.isChecked():
                res['merge_fission'] = detect_merge_fission(
                    np.asarray(stack), mpx, prox_um.value())
                prog_d.setValue(3)

            if cb_life.isChecked() and tracks is not None:
                res['lifetime_distribution'] = cluster_lifetime_analysis(tracks)
                prog_d.setValue(3)

            if cb_nb.isChecked() and tracks is not None:
                res['neighbourhood_persistence'] = neighbourhood_persistence(
                    props, tracks, nb_rad.value())
                prog_d.setValue(4)

            if cb_grow.isChecked() and tracks is not None:
                res['growth_kinetics'] = growth_shrinkage_kinetics(
                    tracks, frame_dt.value())
                prog_d.setValue(5)

            return res

        worker = _AdvancedAnalysisWorker(_task, {})
        ui_instance._dyn_worker = worker

        def _done(res):
            prog_d.setVisible(False); run_d.setEnabled(True)
            for k, v in res.items():
                dr[f'dyn_{k}'] = v
            from pycat.ui.ui_utils import show_dataframes_dialog
            tables = [(k.replace('_',' ').title(), v.round(4) if hasattr(v,'round') else v)
                      for k, v in res.items() if isinstance(v, pd.DataFrame)]
            if tables:
                show_dataframes_dialog("Dynamic Spatial Phenotyping", tables)
            _linker_names = {0: 'bayesian', 1: 'greedy', 2: 'trackmate'}
            ui_instance._record('dynamic_spatial', {
                'stack':      stack_dd.currentText(),
                'linker':     _linker_names.get(linker_dd.currentIndex(), 'bayesian'),
                'sigma_um':   sigma_spin.value(),
                'area_weight':area_w_spin.value(),
                'max_disp':   max_disp.value(),
                'max_gap':    max_gap.value(),
                'tm_gap_closing_distance': tm_gap_dist.value(),
                'tm_allow_merging':  tm_merge_cb.isChecked(),
                'tm_allow_splitting': tm_split_cb.isChecked(),
                'tm_use_kalman':     tm_kalman_cb.isChecked(),
            })
            napari_show_info("Dynamic spatial phenotyping complete.")

        def _err(msg):
            prog_d.setVisible(False); run_d.setEnabled(True)
            napari_show_warning("Dynamic analysis error — see terminal.")
            print(f"[PyCAT Dynamic] ERROR:\n{msg}")

        worker.finished.connect(_done); worker.error.connect(_err)
        worker.start()

    run_d.clicked.connect(_on_dynamic)
    tabs.addTab(dyn_widget, "Dynamic")

    # ── Tab 3: Organizational Metrics ───────────────────────────────────
    org_widget = QWidget()
    of = QFormLayout(org_widget)

    punc_dd_o = ui_instance.create_layer_dropdown(napari.layers.Labels)
    cell_dd_o = ui_instance.create_layer_dropdown(napari.layers.Labels)
    of.addRow("Condensate mask:", punc_dd_o)
    of.addRow("Labeled cell mask:", cell_dd_o)

    eps_spin  = QDoubleSpinBox(); eps_spin.setRange(0.1, 20); eps_spin.setValue(2.0)
    knn_spin  = QSpinBox();       knn_spin.setRange(1, 20);   knn_spin.setValue(3)
    ebin_spin = QSpinBox();       ebin_spin.setRange(3, 30);  ebin_spin.setValue(10)
    of.addRow("DBSCAN eps (µm):", eps_spin)
    of.addRow("k-nearest neighbours:", knn_spin)
    of.addRow("Entropy grid bins:", ebin_spin)

    cb_ent  = QCheckBox("Spatial entropy");               cb_ent.setChecked(True)
    cb_clsz = QCheckBox("Cluster size distribution");     cb_clsz.setChecked(True)
    cb_spc  = QCheckBox("Inter-condensate spacing");      cb_spc.setChecked(True)
    cb_occ  = QCheckBox("Per-cell occupancy / fractional area"); cb_occ.setChecked(True)
    cb_bnd  = QCheckBox("Distance to cell boundary");     cb_bnd.setChecked(True)
    for cb in (cb_ent, cb_clsz, cb_spc, cb_occ, cb_bnd):
        of.addRow(cb)

    prog_o = QProgressBar(); prog_o.setVisible(False)
    run_o  = QPushButton("▶  Run Organizational Metrics")
    of.addRow(prog_o); of.addRow(run_o)

    def _on_org():
        from pycat.toolbox.organizational_metrics_tools import (
            spatial_entropy, cluster_size_distribution,
            inter_condensate_spacing, per_cell_occupancy,
            distance_to_boundary,
        )
        from pycat.toolbox.spatial_metrology_tools import get_puncta_centroids
        try:
            pmask = ui_instance.viewer.layers[punc_dd_o.currentText()].data
            cmask = ui_instance.viewer.layers[cell_dd_o.currentText()].data
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}"); return

        dr  = ui_instance.central_manager.active_data_class.data_repository
        mpx = float(dr.get('microns_per_pixel_sq', 1.0))**0.5
        coords_df = get_puncta_centroids(pmask, cmask, mpx)
        cells = [c for c in coords_df['cell_label'].unique() if c != 0]
        n = len(cells)
        prog_o.setMaximum(max(n, 1)); prog_o.setValue(0); prog_o.setVisible(True)
        run_o.setEnabled(False)

        def _task():
            res = {'occupancy': per_cell_occupancy(pmask, cmask, mpx) if cb_occ.isChecked() else None}
            ent_rows, clsz_rows, spc_rows, bnd_rows = [], [], [], []

            for i, cl in enumerate(cells):
                sub = coords_df[coords_df['cell_label'] == cl]
                coords = sub[['y_um', 'x_um']].values
                cm = (cmask == cl).astype(bool)

                if cb_ent.isChecked():
                    e = spatial_entropy(coords, cm, ebin_spin.value(), mpx)
                    ent_rows.append({'cell_label': cl, **e})

                if cb_clsz.isChecked():
                    cs = cluster_size_distribution(coords, eps_spin.value())
                    clsz_rows.append({'cell_label': cl,
                                      'n_clusters':        cs.attrs.get('n_clusters', 0),
                                      'n_noise':           cs.attrs.get('n_noise', 0),
                                      'mean_cluster_size': cs.attrs.get('mean_cluster_size', np.nan),
                                      'fraction_clustered':cs.attrs.get('fraction_clustered', np.nan)})

                if cb_spc.isChecked():
                    sp = inter_condensate_spacing(coords, knn_spin.value())
                    spc_rows.append({'cell_label': cl,
                                     'mean_spacing_um':  sp.attrs.get('mean_spacing_um', np.nan),
                                     'median_spacing_um':sp.attrs.get('median_spacing_um', np.nan),
                                     'spacing_cv':       sp.attrs.get('coefficient_of_variation', np.nan)})

                if cb_bnd.isChecked():
                    bd = distance_to_boundary(coords, cm, mpx)
                    bnd_rows.append({'cell_label': cl,
                                     'mean_dist_to_boundary_um':   bd['mean_dist_um'],
                                     'median_dist_to_boundary_um': bd['median_dist_um'],
                                     'max_inscribed_radius_um':    bd['max_inscribed_radius_um']})

                prog_o.setValue(i + 1)

            out = {}
            if ent_rows:  out['spatial_entropy']  = pd.DataFrame(ent_rows)
            if clsz_rows: out['cluster_sizes']     = pd.DataFrame(clsz_rows)
            if spc_rows:  out['spacing']           = pd.DataFrame(spc_rows)
            if res['occupancy'] is not None: out['occupancy'] = res['occupancy']
            if bnd_rows:  out['boundary_distances'] = pd.DataFrame(bnd_rows)
            return out

        worker = _AdvancedAnalysisWorker(_task, {})
        ui_instance._org_worker = worker

        def _done(res):
            prog_o.setVisible(False); run_o.setEnabled(True)
            for k, v in res.items():
                dr[f'org_{k}'] = v
            from pycat.ui.ui_utils import show_dataframes_dialog
            tables = [(k.replace('_',' ').title(), v.round(4))
                      for k, v in res.items()]
            show_dataframes_dialog("Organizational Metrics", tables)
            ui_instance._record('organizational_metrics',
                                {'puncta': punc_dd_o.currentText()})
            napari_show_info("Organizational metrics complete.")

        def _err(msg):
            prog_o.setVisible(False); run_o.setEnabled(True)
            napari_show_warning("Organizational metrics error — see terminal.")
            print(f"[PyCAT Org] ERROR:\n{msg}")

        worker.finished.connect(_done); worker.error.connect(_err)
        worker.start()

    run_o.clicked.connect(_on_org)
    tabs.addTab(org_widget, "Organizational")

    outer.addWidget(tabs)
    widget = QWidget()
    widget.setLayout(outer)
    ui_instance._add_widget_to_layout_or_dock(
        widget, layout, separate_widget, "Advanced Analysis"
    )
