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


from pycat.utils.pixel_size import pixel_size_um_or_default
from pycat.utils.general_utils import debug_log
import pandas as pd
import napari
from napari.utils.notifications import (
    show_info    as napari_show_info,
    show_warning as napari_show_warning,
)
from PyQt5.QtWidgets import (
    QVBoxLayout, QWidget, QPushButton, QGroupBox, QFormLayout,
    QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QProgressBar,
    QTabWidget, QComboBox, QSizePolicy,
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
            result = self._task(progress_emit=self.progress.emit,
                                should_cancel=self.isInterruptionRequested, **self._kwargs)
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

    # ── The pixel-size gate: this panel reports AREAS and DISTANCES in microns ──
    #
    # ``microns_per_pixel_sq`` defaults to **1** when the metadata does not carry it — and
    # **1 um/px is a plausible value, not an obviously-wrong one.** So an area silently comes out
    # in PIXELS-squared, labelled as microns-squared, and nothing says so.
    #
    # ``utils/pixel_size.py`` puts it exactly: *"A NaN area is visibly wrong; a 1435x
    # overestimate is not."* Eight UIs already carry this gate; this one did not.
    try:
        from pycat.ui.field_status import add_pixel_size_gate
        add_pixel_size_gate(
            outer,
            lambda: ui_instance.central_manager.active_data_class.data_repository,
            central_manager=ui_instance.central_manager)
    except Exception as _exc:
        debug_log('advanced_analysis_ui: the pixel-size gate could not be added', _exc)

    # Fully-optional block: hidden by default behind an off-by-default checkbox.
    from PyQt5.QtWidgets import QCheckBox as _QCheckBox
    show_cb = _QCheckBox("Show advanced analysis (optional)")
    show_cb.setChecked(False)
    show_cb.setToolTip(
        "Morphological complexity, dynamic spatial phenotyping, and organizational "
        "metrics. Optional — enable only if you need these analyses.")
    outer.addWidget(show_cb)

    # Detect whether any loaded layer is a (T,H,W) time stack. The dynamic spatial
    # phenotyping tab only applies to time-series data, so it is hidden when the
    # input has no time channel.
    def _has_time_stack():
        try:
            for lyr in ui_instance.viewer.layers:
                data = getattr(lyr, 'data', None)
                if data is not None and getattr(data, 'ndim', 0) >= 3:
                    return True
        except Exception:
            pass
        return False

    tabs = QTabWidget()
    tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def _fit_tab_height(idx=None):
        # Cap the tab widget height to the current tab's content + tab bar.
        # QTabWidget normally reserves space for the tallest tab in ALL tabs;
        # this overrides that by calling setMaximumHeight() whenever the tab
        # changes, collapsing the empty space that would otherwise appear when
        # a shorter tab (Morphological, Organizational) is active.
        w = tabs.currentWidget()
        if w is None:
            return
        bar_h     = tabs.tabBar().sizeHint().height()
        content_h = w.sizeHint().height()
        tabs.setMaximumHeight(bar_h + content_h + 12)  # 12px margin

    tabs.currentChanged.connect(_fit_tab_height)

    # ── Tab 1: Morphological Complexity ─────────────────────────────────
    morph_widget = QWidget()
    morph_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    mf = QFormLayout(morph_widget)
    mf.setContentsMargins(4, 4, 4, 4)
    mf.setSpacing(5)

    punc_dd_m = ui_instance.create_layer_dropdown(napari.layers.Labels)
    cell_dd_m = ui_instance.create_layer_dropdown(napari.layers.Labels)
    mf.addRow("Condensate mask:", punc_dd_m)
    mf.addRow("Labeled cell mask:", cell_dd_m)

    cb_fd   = QCheckBox("Fractal dimension (box-counting)"); cb_fd.setChecked(True)
    cb_fd.setToolTip("Box-counting fractal dimension — how space-filling / rough each object's boundary is.")
    cb_fd.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    cb_lac  = QCheckBox("Lacunarity")
    cb_lac.setToolTip("Lacunarity — how gappy/heterogeneous the object texture is at multiple scales."); cb_lac.setChecked(True)
    cb_tort = QCheckBox("Tortuosity (fibrillar structures)")
    cb_tort.setToolTip("Tortuosity — path-length / end-to-end ratio; useful for fibrillar, not round, objects."); cb_tort.setChecked(False)
    cb_tort.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    cb_orient = QCheckBox("Orientation / anisotropy order parameter")
    cb_orient.setToolTip("Orientational order S — how aligned elongated objects are (0 random, 1 fully aligned)."); cb_orient.setChecked(True)
    cb_orient.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    for cb in (cb_fd, cb_lac, cb_tort, cb_orient):
        mf.addRow(cb)

    prog_m = QProgressBar(); prog_m.setVisible(False)
    run_m  = QPushButton("▶  Run Morphological Analysis")
    run_m.setToolTip("Compute the selected morphological-complexity metrics on the object mask.")
    run_m.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    stop_m = QPushButton("■  Stop"); stop_m.setVisible(False)
    mf.addRow(prog_m); mf.addRow(run_m); mf.addRow(stop_m)

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
        mpx = pixel_size_um_or_default(dr, context='advanced_analysis_ui')
        results = {}
        n_cells = len(np.unique(cmask)) - 1
        prog_m.setMaximum(max(n_cells, 1)); prog_m.setValue(0); prog_m.setVisible(True)
        run_m.setEnabled(False)

        def _task(progress_emit=None, should_cancel=None):
            def _cancelled():
                return bool(should_cancel and should_cancel())
            if cb_fd.isChecked():
                results['fractal_dimension'] = fractal_dimension_per_cell(pmask, cmask)
            if cb_lac.isChecked():
                # Whole-image lacunarity
                results['lacunarity'] = lacunarity(pmask > 0)
            if cb_tort.isChecked():
                import skimage as sk
                lp = sk.measure.label(pmask > 0)
                results['tortuosity'] = tortuosity_per_object(lp, mpx)
            if _cancelled():
                return results
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
        worker.progress.connect(lambda v, m: prog_m.setValue(v))
        stop_m.setVisible(True)
        try: stop_m.clicked.disconnect()
        except Exception: pass
        stop_m.clicked.connect(lambda: (worker.requestInterruption(), stop_m.setEnabled(False)))
        stop_m.setEnabled(True)

        def _done(res):
            prog_m.setVisible(False); run_m.setEnabled(True); stop_m.setVisible(False)
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
            prog_m.setVisible(False); run_m.setEnabled(True); stop_m.setVisible(False)
            napari_show_warning("Morphological analysis error — see terminal.")
            print(f"[PyCAT Morph] ERROR:\n{msg}")

        worker.finished.connect(_done); worker.error.connect(_err)
        worker.start()

    run_m.clicked.connect(_on_morph)
    tabs.addTab(morph_widget, "Morphological")

    # ── Tab 2: Dynamic Spatial Phenotyping ──────────────────────────────
    dyn_widget = QWidget()
    dyn_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    df = QFormLayout(dyn_widget)
    df.setContentsMargins(4, 4, 4, 4)
    df.setSpacing(5)

    stack_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    df.addRow("TS condensate mask stack (T,H,W):", stack_dd)

    from PyQt5.QtWidgets import QComboBox as _CB2
    linker_dd = _CB2()
    linker_dd.addItems([
        "Bayesian (Hungarian, recommended)",
        "Greedy NNL (fast, simple)",
        "TrackMate LAP (via pyimagej, requires Fiji)",
    ])
    try:
        from pycat.ui.ui_modules import guard_wheel
        guard_wheel(linker_dd)
    except Exception:
        pass
    df.addRow("Linking algorithm:", linker_dd)

    max_disp = QDoubleSpinBox(); max_disp.setRange(0.1, 20); max_disp.setValue(2.0)
    max_disp.setToolTip("Maximum distance (µm) an object may move between frames to be linked into a track.")
    max_gap  = QSpinBox();       max_gap.setRange(0, 5);    max_gap.setValue(2)
    max_gap.setToolTip("Frames an object may disappear for and still be re-linked to the same track.")
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
    vel_check.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    vel_check.setChecked(True)
    vel_check.setToolTip(
        "Maintain an exponentially-weighted velocity estimate per track.\n"
        "Improves linking for condensates undergoing directed motion.\n"
        "Bayesian only."
    )
    frame_dt = QDoubleSpinBox(); frame_dt.setRange(0.01, 3600); frame_dt.setValue(1.0)
    # The frame interval comes from the FILE, not from a spinbox default. See
    # pycat.utils.frame_interval — a 1.0 s default is a physical CLAIM, and it is
    # almost never true. The user's own value always wins.
    try:
        from pycat.utils.frame_interval import sync_spinbox_from_metadata
        sync_spinbox_from_metadata(
            frame_dt, ui_instance.central_manager.active_data_class.data_repository,
            context='advanced_analysis_ui')
    except Exception as _exc:
        debug_log('advanced_analysis_ui: could not sync the frame interval', _exc)
    frame_dt.setToolTip("Time between frames (seconds) — sets the physical time axis for dynamics.")
    prox_um  = QDoubleSpinBox(); prox_um.setRange(0.1, 10);  prox_um.setValue(1.0)
    prox_um.setToolTip("Centroid proximity (µm) for calling two objects a merge or fission event.")
    nb_rad   = QDoubleSpinBox(); nb_rad.setRange(0.5, 20);   nb_rad.setValue(3.0)
    nb_rad.setToolTip("Radius (µm) defining each object's neighbourhood for persistence analysis.")
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
    tm_merge_cb.setToolTip("Let the TrackMate LAP tracker merge two tracks into one.")
    tm_merge_cb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    tm_merge_cb.setChecked(True)
    tm_split_cb = QCheckBox("Allow track splitting (TrackMate LAP)")
    tm_split_cb.setToolTip("Let the TrackMate LAP tracker split one track into two.")
    tm_split_cb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    tm_split_cb.setChecked(True)
    tm_kalman_cb = QCheckBox("Use Kalman tracker instead of LAP")
    tm_kalman_cb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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

    cb_track = QCheckBox("Trajectory tracking + metrics")
    cb_track.setToolTip("Link objects into trajectories and compute per-track speed, displacement, and confinement."); cb_track.setChecked(True)
    cb_track.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    cb_mf    = QCheckBox("Merge / fission detection")
    cb_mf.setToolTip("Detect condensate merge and fission events between consecutive frames.");     cb_mf.setChecked(True)
    cb_mf.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    cb_life  = QCheckBox("Cluster lifetime analysis")
    cb_life.setToolTip("Measure how long each condensate persists before dissolving or merging.");     cb_life.setChecked(True)
    cb_life.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    cb_nb    = QCheckBox("Neighbourhood persistence")
    cb_nb.setToolTip("How stable each object's set of neighbours is over time.");     cb_nb.setChecked(True)
    cb_nb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    cb_grow  = QCheckBox("Growth / shrinkage kinetics")
    cb_grow.setToolTip("Per-object area growth/shrinkage rate over the movie.");   cb_grow.setChecked(True)
    cb_grow.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    for cb in (cb_track, cb_mf, cb_life, cb_nb, cb_grow):
        df.addRow(cb)

    prog_d = QProgressBar(); prog_d.setVisible(False)
    run_d  = QPushButton("▶  Run Dynamic Analysis")
    run_d.setToolTip("Run the selected trajectory and dynamic-phenotyping analyses.")
    run_d.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    stop_d = QPushButton("■  Stop"); stop_d.setVisible(False)
    df.addRow(prog_d); df.addRow(run_d); df.addRow(stop_d)

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
        mpx = pixel_size_um_or_default(dr, context='advanced_analysis_ui')
        # NOTE: this used to call `progress_emit(0, 5)` here. `progress_emit` is a
        # PARAMETER of the nested `_task` below, not a variable of this scope, so the
        # name did not exist yet and this line raised NameError — killing the handler
        # before the worker was even created. The Dynamic Spatial Analysis button
        # could never have run. There is nothing to emit TO at this point anyway (no
        # worker exists), so the progress bar is simply set directly.
        prog_d.setMaximum(5); prog_d.setValue(0); prog_d.setVisible(True)
        run_d.setEnabled(False)

        def _task(progress_emit=None, should_cancel=None):
            def _cancelled():
                return bool(should_cancel and should_cancel())
            res = {}
            props = extract_frame_properties(np.asarray(stack), mpx)
            tracks = None
            progress_emit and progress_emit(1, 5)
            if _cancelled():
                return res

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
                progress_emit and progress_emit(2, 5)

            if cb_mf.isChecked():
                res['merge_fission'] = detect_merge_fission(
                    np.asarray(stack), mpx, prox_um.value())
                progress_emit and progress_emit(3, 5)

            if cb_life.isChecked() and tracks is not None:
                res['lifetime_distribution'] = cluster_lifetime_analysis(tracks)
                progress_emit and progress_emit(3, 5)

            if cb_nb.isChecked() and tracks is not None:
                res['neighbourhood_persistence'] = neighbourhood_persistence(
                    props, tracks, nb_rad.value())
                progress_emit and progress_emit(4, 5)

            if cb_grow.isChecked() and tracks is not None:
                res['growth_kinetics'] = growth_shrinkage_kinetics(
                    tracks, frame_dt.value())
                progress_emit and progress_emit(5, 5)

            return res

        worker = _AdvancedAnalysisWorker(_task, {})
        ui_instance._dyn_worker = worker
        worker.progress.connect(lambda v, m: prog_d.setValue(v))
        stop_d.setVisible(True)
        try: stop_d.clicked.disconnect()
        except Exception: pass
        stop_d.clicked.connect(lambda: (worker.requestInterruption(), stop_d.setEnabled(False)))
        stop_d.setEnabled(True)

        def _done(res):
            prog_d.setVisible(False); run_d.setEnabled(True); stop_d.setVisible(False)
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
            prog_d.setVisible(False); run_d.setEnabled(True); stop_d.setVisible(False)
            napari_show_warning("Dynamic analysis error — see terminal.")
            print(f"[PyCAT Dynamic] ERROR:\n{msg}")

        worker.finished.connect(_done); worker.error.connect(_err)
        worker.start()

    run_d.clicked.connect(_on_dynamic)
    # Dynamic Spatial Phenotyping applies only to (T,H,W) time-series data.
    # The tab is added/removed dynamically by _sync_dynamic_tab() based on
    # whether a time stack is loaded (handled below), so it is NOT added here.

    # ── Tab 3: Organizational Metrics ───────────────────────────────────
    org_widget = QWidget()
    org_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    of = QFormLayout(org_widget)
    of.setContentsMargins(4, 4, 4, 4)
    of.setSpacing(5)

    punc_dd_o = ui_instance.create_layer_dropdown(napari.layers.Labels)
    cell_dd_o = ui_instance.create_layer_dropdown(napari.layers.Labels)
    of.addRow("Condensate mask:", punc_dd_o)
    of.addRow("Labeled cell mask:", cell_dd_o)

    eps_spin  = QDoubleSpinBox(); eps_spin.setRange(0.1, 20); eps_spin.setValue(2.0)
    eps_spin.setToolTip("DBSCAN neighbourhood radius (µm) for grouping condensates into clusters.")
    knn_spin  = QSpinBox();       knn_spin.setRange(1, 20);   knn_spin.setValue(3)
    knn_spin.setToolTip("Number of nearest neighbours used in the spacing / density metrics.")
    ebin_spin = QSpinBox();       ebin_spin.setRange(3, 30);  ebin_spin.setValue(10)
    ebin_spin.setToolTip("Number of bins for the spatial-entropy calculation.")
    of.addRow("DBSCAN eps (µm):", eps_spin)
    of.addRow("k-nearest neighbours:", knn_spin)
    of.addRow("Entropy grid bins:", ebin_spin)

    cb_ent  = QCheckBox("Spatial entropy")
    cb_ent.setToolTip("Shannon entropy of condensate positions — how ordered vs random the arrangement is.");               cb_ent.setChecked(True)
    cb_clsz = QCheckBox("Cluster size distribution")
    cb_clsz.setToolTip("Distribution of DBSCAN cluster sizes across the cell.");     cb_clsz.setChecked(True)
    cb_clsz.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    cb_spc  = QCheckBox("Inter-condensate spacing")
    cb_spc.setToolTip("Typical distance between neighbouring condensates.");      cb_spc.setChecked(True)
    cb_occ  = QCheckBox("Per-cell occupancy / fractional area")
    cb_occ.setToolTip("Fraction of the cell area occupied by condensates."); cb_occ.setChecked(True)
    cb_occ.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    cb_bnd  = QCheckBox("Distance to cell boundary")
    cb_bnd.setToolTip("How far condensates sit from the cell edge (centre vs periphery bias).");     cb_bnd.setChecked(True)
    cb_bnd.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    for cb in (cb_ent, cb_clsz, cb_spc, cb_occ, cb_bnd):
        of.addRow(cb)

    prog_o = QProgressBar(); prog_o.setVisible(False)
    run_o  = QPushButton("▶  Run Organizational Metrics")
    run_o.setToolTip("Run the selected spatial-organization metrics.")
    run_o.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    stop_o = QPushButton("■  Stop"); stop_o.setVisible(False)
    of.addRow(prog_o); of.addRow(run_o); of.addRow(stop_o)

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
        mpx = pixel_size_um_or_default(dr, context='advanced_analysis_ui')
        coords_df = get_puncta_centroids(pmask, cmask, mpx)
        cells = [c for c in coords_df['cell_label'].unique() if c != 0]
        n = len(cells)
        prog_o.setMaximum(max(n, 1)); prog_o.setValue(0); prog_o.setVisible(True)
        run_o.setEnabled(False)

        def _task(progress_emit=None, should_cancel=None):
            def _cancelled():
                return bool(should_cancel and should_cancel())
            res = {'occupancy': per_cell_occupancy(pmask, cmask, mpx) if cb_occ.isChecked() else None}
            ent_rows, clsz_rows, spc_rows, bnd_rows = [], [], [], []

            for i, cl in enumerate(cells):
                if _cancelled():
                    break
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

                progress_emit and progress_emit(i + 1, n)

            out = {}
            if ent_rows:  out['spatial_entropy']  = pd.DataFrame(ent_rows)
            if clsz_rows: out['cluster_sizes']     = pd.DataFrame(clsz_rows)
            if spc_rows:  out['spacing']           = pd.DataFrame(spc_rows)
            if res['occupancy'] is not None: out['occupancy'] = res['occupancy']
            if bnd_rows:  out['boundary_distances'] = pd.DataFrame(bnd_rows)
            return out

        worker = _AdvancedAnalysisWorker(_task, {})
        ui_instance._org_worker = worker
        worker.progress.connect(lambda v, m: prog_o.setValue(v))
        stop_o.setVisible(True)
        try: stop_o.clicked.disconnect()
        except Exception: pass
        stop_o.clicked.connect(lambda: (worker.requestInterruption(), stop_o.setEnabled(False)))
        stop_o.setEnabled(True)

        def _done(res):
            prog_o.setVisible(False); run_o.setEnabled(True); stop_o.setVisible(False)
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
            prog_o.setVisible(False); run_o.setEnabled(True); stop_o.setVisible(False)
            napari_show_warning("Organizational metrics error — see terminal.")
            print(f"[PyCAT Org] ERROR:\n{msg}")

        worker.finished.connect(_done); worker.error.connect(_err)
        worker.start()

    run_o.clicked.connect(_on_org)
    tabs.addTab(org_widget, "Organizational")

    outer.addWidget(tabs)
    # Hidden until the user ticks "Show advanced analysis".
    tabs.setVisible(False)

    def _sync_dynamic_tab():
        # Add/remove the Dynamic tab to match current time-stack presence, so it
        # appears if a (T,H,W) stack is loaded after this widget was built, and
        # stays hidden for plain 2D data.
        idx = tabs.indexOf(dyn_widget)
        if _has_time_stack():
            if idx == -1:
                # insert Dynamic between Morphological (0) and Organizational (last)
                tabs.insertTab(1, dyn_widget, "Dynamic")
        else:
            if idx != -1:
                tabs.removeTab(idx)

    def _toggle_tabs(checked):
        if checked:
            _sync_dynamic_tab()
        tabs.setVisible(bool(checked))
        if checked:
            _fit_tab_height()
    show_cb.toggled.connect(_toggle_tabs)

    # Keep the Dynamic tab in sync if layers change while the block is visible.
    try:
        ui_instance.viewer.layers.events.inserted.connect(
            lambda *_: (tabs.isVisible() and _sync_dynamic_tab()))
        ui_instance.viewer.layers.events.removed.connect(
            lambda *_: (tabs.isVisible() and _sync_dynamic_tab()))
    except Exception:
        pass

    widget = QWidget()
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    widget.setLayout(outer)

    # Initialise height cap after all tab content is constructed.
    # Must be deferred (via a zero-ms timer) so Qt has finished computing
    # sizeHints before we read them.
    from PyQt5.QtCore import QTimer
    QTimer.singleShot(0, _fit_tab_height)

    ui_instance._add_widget_to_layout_or_dock(
        widget, layout, separate_widget, "Advanced Analysis"
    )
