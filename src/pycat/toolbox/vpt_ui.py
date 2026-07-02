"""
PyCAT Video Particle Tracking (VPT) UI
========================================
Self-contained microrheology pipeline: track fluorescent probe beads
diffusing inside an in-vitro condensate to extract viscosity.

Pipeline
--------
  Step 1 — Open multichannel image (via File menu)
  Step 2 — Segment host condensate (one channel) + erode interface
  Step 3 — Detect beads (second channel), keep only beads inside eroded host
  Step 4 — Link trajectories (TrackMate default; Bayesian / Greedy options)
  Step 5 — Drift-correct (ensemble COM) + MSD + diffusion fit + viscosity
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
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QGroupBox, QFormLayout,
    QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QProgressBar,
    QScrollArea, QSizePolicy, QRadioButton,
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt


class _VPTWorker(QThread):
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    def __init__(self, fn):
        super().__init__(); self._fn = fn
    def run(self):
        try:
            self.finished.emit(self._fn(self.progress.emit))
        except Exception:
            import traceback; self.error.emit(traceback.format_exc())


class VideoParticleTrackingUI:
    def __init__(self, viewer, central_manager):
        self.viewer          = viewer
        self.central_manager = central_manager

    # ── helpers ────────────────────────────────────────────────────────
    def _dr(self):
        return self.central_manager.active_data_class.data_repository

    def _mpx(self):
        return float(self._dr().get('microns_per_pixel_sq', 1.0)) ** 0.5

    def _record(self, step, params):
        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record(step, params)

    def create_layer_dropdown(self, layer_type, name_hint=''):
        return self.central_manager.toolbox_functions_ui.create_layer_dropdown(
            layer_type, name_hint=name_hint)

    def setup_ui(self):
        try:
            self.central_manager.workflow_checklist.activate('vpt')
            bp = getattr(self.central_manager, '_pycat_batch_processor', None)
            if bp:
                for step in bp.config.get('steps', []):
                    self.central_manager.workflow_checklist.on_step_recorded(step['step'])
        except Exception:
            pass

        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QLabel(
            "<b>Video Particle Tracking (Microrheology)</b><br>"
            "<span style='color:#888;font-size:9pt;'>"
            "Track fluorescent probe beads diffusing inside an in-vitro "
            "condensate to measure viscosity via the Stokes-Einstein relation."
            "</span>")
        header.setWordWrap(True)
        header.setStyleSheet("padding:6px; background:#2a2a2a; border-radius:4px;")
        layout.addWidget(header)

        self._add_host_segmentation(layout)
        self._add_bead_detection(layout)
        self._add_tracking(layout)
        self._add_microrheology(layout)

        main_w = QWidget(); main_w.setLayout(layout)
        main_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        from pycat.ui.ui_modules import _apply_scroll_guard
        _apply_scroll_guard(main_w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setWidget(main_w); scroll.setMinimumWidth(320)
        self.viewer.window.add_dock_widget(scroll, name="Video Particle Tracking")

    # ── Step 2: host condensate segmentation + erosion ─────────────────
    def _add_host_segmentation(self, layout):
        grp  = QGroupBox("Step 2 — Segment Host Condensate")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 4, 4, 4); form.setSpacing(5)

        note = QLabel(
            "<span style='color:#aaa;font-size:9pt;'>"
            "Select the channel showing the condensate (host) phase. The mask "
            "is eroded inward so beads near the interface — where fusion and "
            "surface flow corrupt bulk diffusion — are excluded.</span>")
        note.setWordWrap(True); form.addRow(note)

        self._host_dd = self.create_layer_dropdown(napari.layers.Image)
        self._host_dd.setToolTip("Fluorescence channel that labels the condensate host phase.")
        form.addRow("Host channel:", self._host_dd)

        self._seg_method = QSpinBox()  # placeholder swap below with combobox-like radios
        method_row = QHBoxLayout()
        self._rb_otsu     = QRadioButton("Otsu")
        self._rb_triangle = QRadioButton("Triangle")
        self._rb_li       = QRadioButton("Li")
        self._rb_otsu.setChecked(True)
        for rb in (self._rb_otsu, self._rb_triangle, self._rb_li):
            method_row.addWidget(rb)
        method_row.addStretch()
        mw = QWidget(); mw.setLayout(method_row)
        mw.setToolTip("Global threshold method for the host phase.")
        form.addRow("Threshold:", mw)

        self._erosion_spin = QSpinBox()
        self._erosion_spin.setRange(0, 100); self._erosion_spin.setValue(5)
        self._erosion_spin.setToolTip(
            "Erosion depth in pixels. Beads within this distance of the "
            "condensate edge are excluded. Use ~1-2× bead radius + margin.")
        form.addRow("Interface erosion (px):", self._erosion_spin)

        self._host_prog = QProgressBar(); self._host_prog.setVisible(False)
        btn = QPushButton("▶  Segment Host & Erode")
        btn.clicked.connect(self._on_segment_host)
        form.addRow(self._host_prog); form.addRow(btn)
        layout.addWidget(grp)

    def _seg_method_name(self):
        if self._rb_triangle.isChecked(): return 'triangle'
        if self._rb_li.isChecked():       return 'li'
        return 'otsu'

    def _on_segment_host(self):
        from pycat.toolbox.vpt_tools import segment_host_condensate, erode_host_mask
        name = self._host_dd.currentText()
        if name not in [l.name for l in self.viewer.layers]:
            napari_show_warning(f"Host channel layer '{name}' not found."); return
        img = np.asarray(self.viewer.layers[name].data)
        try:
            labeled = segment_host_condensate(img, method=self._seg_method_name())
            eroded  = erode_host_mask(labeled, erosion_px=self._erosion_spin.value())
        except Exception as e:
            napari_show_warning(f"Host segmentation failed: {e}"); return

        n_cond = int(eroded.max())
        if n_cond == 0:
            napari_show_warning(
                "No condensates remained after erosion. Reduce the erosion "
                "depth or check the host channel / threshold method."); return

        if "Eroded Host Mask" in self.viewer.layers:
            self.viewer.layers.remove("Eroded Host Mask")
        self.viewer.add_labels(eroded.astype(int), name="Eroded Host Mask")
        self._dr()['vpt_host_mask'] = eroded
        self._record('vpt_segment_host', {
            'host_channel': name, 'method': self._seg_method_name(),
            'erosion_px': self._erosion_spin.value()})
        napari_show_info(
            f"Host segmentation complete: {n_cond} condensate(s), "
            f"eroded {self._erosion_spin.value()}px inward.")

    # ── Step 3: bead detection ─────────────────────────────────────────
    def _add_bead_detection(self, layout):
        grp  = QGroupBox("Step 3 — Detect Beads")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 4, 4, 4); form.setSpacing(5)

        note = QLabel(
            "<span style='color:#aaa;font-size:9pt;'>"
            "Select the bead channel. Beads are found per frame by "
            "Laplacian-of-Gaussian blob detection; only beads inside the "
            "eroded host mask are kept.</span>")
        note.setWordWrap(True); form.addRow(note)

        self._bead_dd = self.create_layer_dropdown(napari.layers.Image)
        self._bead_dd.setToolTip("Fluorescence channel showing the probe beads.")
        form.addRow("Bead channel:", self._bead_dd)

        self._min_sigma = QDoubleSpinBox()
        self._min_sigma.setRange(0.5, 20); self._min_sigma.setValue(1.0)
        self._min_sigma.setSingleStep(0.5); self._min_sigma.setDecimals(1)
        self._min_sigma.setToolTip("Smallest bead scale (px). Bead radius ≈ √2·sigma.")
        form.addRow("Min sigma (px):", self._min_sigma)

        self._max_sigma = QDoubleSpinBox()
        self._max_sigma.setRange(0.5, 40); self._max_sigma.setValue(5.0)
        self._max_sigma.setSingleStep(0.5); self._max_sigma.setDecimals(1)
        self._max_sigma.setToolTip("Largest bead scale (px). Bead radius ≈ √2·sigma.")
        form.addRow("Max sigma (px):", self._max_sigma)

        self._bead_thresh = QDoubleSpinBox()
        self._bead_thresh.setRange(0.001, 1.0); self._bead_thresh.setValue(0.02)
        self._bead_thresh.setSingleStep(0.005); self._bead_thresh.setDecimals(3)
        self._bead_thresh.setToolTip("Detection sensitivity. Lower = detect more (dimmer) beads.")
        form.addRow("Threshold:", self._bead_thresh)

        self._fit_quality = QCheckBox("Gaussian quality fit + classify beads")
        self._fit_quality.setChecked(True)
        self._fit_quality.setToolTip(
            "Fit a 2D Gaussian to each bead to measure width/brightness and "
            "classify singlet / aggregate / out-of-plane. Aggregates are "
            "larger AND brighter; defocused beads are larger but dimmer.")
        form.addRow(self._fit_quality)

        self._exclude_agg = QCheckBox("Route aggregates to a secondary population")
        self._exclude_agg.setChecked(True)
        self._exclude_agg.setToolTip(
            "Keep aggregates OUT of the primary microrheology set (their "
            "size/mass would bias Stokes-Einstein viscosity) and instead track "
            "them as a separate population with its own aggregation readout. "
            "Uncheck to fold aggregates back into the primary tracks.")
        form.addRow(self._exclude_agg)

        self._recover_defocus = QCheckBox("Keep recoverable out-of-plane beads")
        self._recover_defocus.setChecked(True)
        self._recover_defocus.setToolTip(
            "Keep beads flagged as out-of-plane/defocused (larger, dimmer). "
            "Their centroid is still usable for tracking; uncheck to drop them.")
        form.addRow(self._recover_defocus)

        self._bead_prog = QProgressBar(); self._bead_prog.setVisible(False)
        btn = QPushButton("▶  Detect Beads")
        btn.clicked.connect(self._on_detect_beads)
        form.addRow(self._bead_prog); form.addRow(btn)
        layout.addWidget(grp)

    def _on_detect_beads(self):
        from pycat.toolbox.vpt_tools import detect_beads_stack
        name = self._bead_dd.currentText()
        if name not in [l.name for l in self.viewer.layers]:
            napari_show_warning(f"Bead channel layer '{name}' not found."); return
        stack = np.asarray(self.viewer.layers[name].data)
        host_mask = self._dr().get('vpt_host_mask')
        if host_mask is None:
            napari_show_warning(
                "No host mask found — run Step 2 first so beads near the "
                "condensate interface can be excluded."); return

        self._bead_prog.setVisible(True); self._bead_prog.setRange(0, 0)

        fit_q = self._fit_quality.isChecked()
        def _job(progress):
            # Keep ALL classes labelled at detection; routing (primary vs.
            # aggregate) happens at the tracking step so aggregates can be
            # followed as their own population.
            return detect_beads_stack(
                stack, host_mask=host_mask,
                min_sigma=self._min_sigma.value(),
                max_sigma=self._max_sigma.value(),
                threshold=self._bead_thresh.value(),
                microns_per_pixel=self._mpx(),
                fit_quality=fit_q,
                exclude_aggregates=False, recover_out_of_plane=True,
                progress_callback=progress)

        w = _VPTWorker(_job)
        def _done(det_df):
            self._bead_prog.setVisible(False)
            self._dr()['vpt_detections'] = det_df
            n = len(det_df)
            if n == 0:
                napari_show_warning(
                    "No beads detected inside the eroded host mask. Lower the "
                    "threshold, widen the sigma range, or reduce erosion depth.")
                return
            # Add a points layer for visual confirmation, coloured by class
            pts = det_df[['frame', 'y_um', 'x_um']].copy()
            pts['y_px'] = pts['y_um'] / self._mpx()
            pts['x_px'] = pts['x_um'] / self._mpx()
            coords = pts[['frame', 'y_px', 'x_px']].values
            if "Bead Detections" in self.viewer.layers:
                self.viewer.layers.remove("Bead Detections")
            if 'bead_class' in det_df.columns:
                cmap = {'singlet': '#00ff00', 'aggregate': '#ff3b30',
                        'out_of_plane': '#ffcc00', 'unfit': '#888888'}
                face = [cmap.get(c, '#00ff00') for c in det_df['bead_class']]
                self.viewer.add_points(
                    coords, name="Bead Detections", size=6,
                    face_color=face, border_color='white', opacity=0.7)
            else:
                self.viewer.add_points(
                    coords, name="Bead Detections", size=6,
                    face_color='#00ff00', border_color='white', opacity=0.7)
            rec = {'bead_channel': name, 'min_sigma': self._min_sigma.value(),
                   'max_sigma': self._max_sigma.value(),
                   'threshold': self._bead_thresh.value(),
                   'fit_quality': self._fit_quality.isChecked()}
            if 'bead_class' in det_df.columns:
                counts = det_df['bead_class'].value_counts().to_dict()
                rec['class_counts'] = counts
            self._record('vpt_detect_beads', rec)

            if 'bead_class' in det_df.columns:
                counts = det_df['bead_class'].value_counts().to_dict()
                # Show a per-class summary table
                try:
                    import pandas as pd
                    from pycat.ui.ui_utils import show_dataframes_dialog
                    summ = det_df.groupby('bead_class').agg(
                        n=('bead_class', 'size'),
                        median_sigma=('sigma_mean', 'median'),
                        median_intensity=('integrated_intensity', 'median'),
                        median_n_units=('n_units_est', 'median')).reset_index()
                    show_dataframes_dialog("Bead Quality Classes",
                                           [('Per-class summary', summ.round(3))])
                except Exception:
                    pass
                napari_show_info(
                    f"Detected {n} beads across {det_df['frame'].nunique()} "
                    f"frames. Classes: {counts} "
                    "(green=singlet, red=aggregate, yellow=out-of-plane).")
            else:
                napari_show_info(
                    f"Detected {n} bead positions across "
                    f"{det_df['frame'].nunique()} frames.")
        def _err(msg):
            self._bead_prog.setVisible(False)
            napari_show_warning("Bead detection failed — see terminal.")
            print(msg)
        w.finished.connect(_done); w.error.connect(_err)
        w.progress.connect(lambda i, n: (
            self._bead_prog.setRange(0, n), self._bead_prog.setValue(i)))
        self._bead_worker = w; w.start()

    # ── Step 4: trajectory linking ─────────────────────────────────────
    def _add_tracking(self, layout):
        grp  = QGroupBox("Step 4 — Link Trajectories")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 4, 4, 4); form.setSpacing(5)

        method_grp = QGroupBox("Linker")
        ml = QVBoxLayout(method_grp)
        ml.setContentsMargins(4, 4, 4, 4); ml.setSpacing(3)
        self._rb_trackmate = QRadioButton("TrackMate LAP  (recommended)")
        self._rb_bayesian  = QRadioButton("Bayesian / Hungarian")
        self._rb_greedy    = QRadioButton("Greedy nearest-neighbour")
        self._rb_trackmate.setChecked(True)
        self._rb_trackmate.setToolTip(
            "Real TrackMate LAP tracker via embedded Fiji. Requires "
            "pip install pycat-napari[trackmate] + a JDK. Falls back to "
            "Bayesian if unavailable.")
        self._rb_bayesian.setToolTip("PyCAT's native Hungarian/LAP linker with gap closing.")
        self._rb_greedy.setToolTip("Fast greedy nearest-neighbour linker.")
        for rb in (self._rb_trackmate, self._rb_bayesian, self._rb_greedy):
            ml.addWidget(rb)
        form.addRow(method_grp)

        self._max_link = QDoubleSpinBox()
        self._max_link.setRange(0.1, 50); self._max_link.setValue(2.0)
        self._max_link.setSingleStep(0.5); self._max_link.setDecimals(2)
        self._max_link.setToolTip("Maximum bead displacement between frames (µm).")
        form.addRow("Max linking dist (µm):", self._max_link)

        self._max_gap = QSpinBox()
        self._max_gap.setRange(0, 20); self._max_gap.setValue(2)
        self._max_gap.setToolTip("Max frames a bead can vanish and still be reconnected.")
        form.addRow("Max frame gap:", self._max_gap)

        self._track_prog = QProgressBar(); self._track_prog.setVisible(False)
        btn = QPushButton("▶  Link Trajectories")
        btn.clicked.connect(self._on_link)
        form.addRow(self._track_prog); form.addRow(btn)
        layout.addWidget(grp)

    def _linker_name(self):
        if self._rb_bayesian.isChecked(): return 'bayesian'
        if self._rb_greedy.isChecked():   return 'greedy'
        return 'trackmate'

    def _on_link(self):
        from pycat.toolbox.vpt_tools import (
            _link, drift_correct_com, split_bead_populations,
            aggregate_population_stats)
        det = self._dr().get('vpt_detections')
        if det is None or det.empty:
            napari_show_warning("No bead detections found — run Step 3 first."); return

        route_agg = (self._fit_quality.isChecked() and self._exclude_agg.isChecked()
                     and 'bead_class' in det.columns)
        self._track_prog.setVisible(True); self._track_prog.setRange(0, 0)

        def _job(progress):
            if route_agg:
                pops = split_bead_populations(
                    det, recover_out_of_plane=self._recover_defocus.isChecked())
                primary, aggregates = pops['primary'], pops['aggregate']
            else:
                primary, aggregates = det, det.iloc[0:0]
            ptracks = drift_correct_com(
                _link(primary, self._linker_name(), self._max_link.value(),
                      self._max_gap.value(), self._mpx()))
            atracks = None
            if route_agg and len(aggregates) >= 2:
                try:
                    atracks = _link(aggregates, self._linker_name(),
                                    self._max_link.value(), self._max_gap.value(),
                                    self._mpx())
                except Exception:
                    atracks = None
            total_by_frame = det.groupby('frame').size()
            astats = aggregate_population_stats(aggregates, total_by_frame=total_by_frame) \
                if route_agg else None
            return dict(primary=ptracks, aggregate_tracks=atracks,
                        aggregate_stats=astats, aggregates=aggregates)

        w = _VPTWorker(_job)
        def _done(res):
            self._track_prog.setVisible(False)
            tracks = res['primary']
            if tracks.empty:
                napari_show_warning("Linking produced no trajectories."); return
            tracks = tracks[tracks['track_id'] != -1] if 'track_id' in tracks else tracks
            self._dr()['vpt_tracks'] = tracks
            mpp = self._mpx()

            def _tracks_layer(tr, name, color=None):
                tl = tr[['track_id', 'frame']].copy()
                tl['y'] = tr['y_um_raw'] / mpp if 'y_um_raw' in tr else tr['y_um'] / mpp
                tl['x'] = tr['x_um_raw'] / mpp if 'x_um_raw' in tr else tr['x_um'] / mpp
                if name in self.viewer.layers:
                    self.viewer.layers.remove(name)
                self.viewer.add_tracks(tl[['track_id', 'frame', 'y', 'x']].values, name=name)

            _tracks_layer(tracks, "Bead Trajectories")

            # Secondary aggregate population
            atracks = res.get('aggregate_tracks')
            astats = res.get('aggregate_stats')
            n_agg_tracks = 0
            if atracks is not None and not atracks.empty and 'track_id' in atracks:
                atracks = atracks[atracks['track_id'] != -1]
                self._dr()['vpt_aggregate_tracks'] = atracks
                n_agg_tracks = int(atracks['track_id'].nunique())
                _tracks_layer(atracks, "Aggregate Trajectories")
            if astats is not None and not astats.empty:
                self._dr()['vpt_aggregate_stats'] = astats
                try:
                    from pycat.ui.ui_utils import show_dataframes_dialog
                    show_dataframes_dialog(
                        "Aggregate Population",
                        [('Per-frame aggregation', astats.round(3))])
                except Exception:
                    pass

            self._record('vpt_link_trajectories', {
                'linker': self._linker_name(),
                'max_linking_distance_um': self._max_link.value(),
                'max_frame_gap': self._max_gap.value(),
                'routed_aggregates': bool(route_agg),
                'n_aggregate_tracks': n_agg_tracks})
            msg = (f"Linked {tracks['track_id'].nunique()} primary trajectories "
                   f"(drift-corrected).")
            if route_agg:
                msg += f" Aggregate population: {n_agg_tracks} tracks."
            napari_show_info(msg)
        def _err(msg):
            self._track_prog.setVisible(False)
            napari_show_warning("Linking failed — see terminal."); print(msg)
        w.finished.connect(_done); w.error.connect(_err)
        self._track_worker = w; w.start()

    # ── Step 5: microrheology ──────────────────────────────────────────
    def _add_microrheology(self, layout):
        grp  = QGroupBox("Step 5 — Microrheology (MSD → Viscosity)")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 4, 4, 4); form.setSpacing(5)

        self._frame_dt = QDoubleSpinBox()
        self._frame_dt.setRange(0.0001, 3600); self._frame_dt.setValue(0.1)
        self._frame_dt.setDecimals(4); self._frame_dt.setSingleStep(0.01)
        self._frame_dt.setToolTip("Time between frames (seconds).")
        form.addRow("Frame interval (s):", self._frame_dt)

        self._bead_radius = QDoubleSpinBox()
        self._bead_radius.setRange(0.001, 5.0); self._bead_radius.setValue(0.1)
        self._bead_radius.setDecimals(3); self._bead_radius.setSingleStep(0.01)
        self._bead_radius.setToolTip("Probe bead radius (µm). 20nm–2µm typical → 0.01–1.0 µm radius.")
        form.addRow("Bead radius (µm):", self._bead_radius)

        self._temp_C = QDoubleSpinBox()
        self._temp_C.setRange(-20, 100); self._temp_C.setValue(24.0)
        self._temp_C.setDecimals(1); self._temp_C.setSingleStep(0.5)
        self._temp_C.setToolTip("Temperature (°C) for the Stokes-Einstein relation.")
        form.addRow("Temperature (°C):", self._temp_C)

        self._min_track = QSpinBox()
        self._min_track.setRange(2, 1000); self._min_track.setValue(5)
        self._min_track.setToolTip("Minimum track length (frames) to include in the MSD.")
        form.addRow("Min track length:", self._min_track)

        self._rheo_prog = QProgressBar(); self._rheo_prog.setVisible(False)
        btn = QPushButton("▶  Compute MSD & Viscosity")
        btn.clicked.connect(self._on_rheology)
        form.addRow(self._rheo_prog); form.addRow(btn)
        layout.addWidget(grp)

    def _on_rheology(self):
        from pycat.toolbox.condensate_physics_tools import (
            compute_msd, fit_anomalous_diffusion)
        from pycat.toolbox.vpt_tools import viscosity_from_diffusion
        tracks = self._dr().get('vpt_tracks')
        if tracks is None or tracks.empty:
            napari_show_warning("No trajectories found — run Step 4 first."); return

        try:
            msd_df = compute_msd(
                tracks, microns_per_pixel=1.0,
                frame_interval_s=self._frame_dt.value(),
                min_track_length=self._min_track.value())
            fit = fit_anomalous_diffusion(msd_df)
            eta = viscosity_from_diffusion(
                fit.get('D_um2_per_s', float('nan')),
                self._bead_radius.value(), self._temp_C.value())
        except Exception as e:
            napari_show_warning(f"Microrheology failed: {e}"); return

        self._dr()['vpt_msd_df'] = msd_df
        self._dr()['vpt_fit'] = fit
        self._dr()['vpt_eta_Pa_s'] = eta

        D = fit.get('D_um2_per_s', float('nan'))
        alpha = fit.get('alpha', float('nan'))
        r2 = fit.get('r_squared', float('nan'))
        motion = fit.get('motion_type', 'unknown')

        self._record('vpt_microrheology', {
            'frame_interval_s': self._frame_dt.value(),
            'bead_radius_um': self._bead_radius.value(),
            'temperature_C': self._temp_C.value(),
            'min_track_length': self._min_track.value(),
            'D_um2_per_s': D, 'alpha': alpha, 'eta_Pa_s': eta})

        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            summary = pd.DataFrame([{
                'D (µm²/s)': round(D, 5) if D == D else None,
                'alpha': round(alpha, 3) if alpha == alpha else None,
                'motion': motion,
                'R²': round(r2, 3) if r2 == r2 else None,
                'viscosity (Pa·s)': round(eta, 4) if eta == eta else None,
                'n_tracks': int(tracks['track_id'].nunique()),
            }])
            show_dataframes_dialog("VPT Microrheology Results",
                                   [('Summary', summary), ('MSD', msd_df)])
        except Exception:
            pass

        napari_show_info(
            f"Microrheology complete: D={D:.4g} µm²/s, α={alpha:.3g} ({motion}), "
            f"η={eta:.4g} Pa·s (n={tracks['track_id'].nunique()} tracks).")
