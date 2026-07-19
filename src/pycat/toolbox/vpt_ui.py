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
try:
    from pycat.ui.field_status import label_with_circle
except Exception:
    label_with_circle = lambda t,**k: t
import numpy as np

from pycat.utils.pixel_size import pixel_size_um_or_default
import pandas as pd
import napari
from napari.utils.notifications import (
    show_info    as napari_show_info,
    show_warning as napari_show_warning,
)
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QGroupBox, QFormLayout,
    QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QProgressBar,
    QScrollArea, QSizePolicy, QRadioButton, QComboBox, QLineEdit,
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


from pycat.toolbox.vpt.panels import _VptPanelsMixin


from pycat.toolbox.vpt.napari_adapter import _VptNapariMixin


from pycat.toolbox.vpt.table_adapter import _VptTableMixin


from pycat.toolbox.vpt.msd_adapter import _VptMsdMixin


from pycat.toolbox.vpt.results_dock import _VptResultsDockMixin


class VideoParticleTrackingUI(_VptPanelsMixin, _VptMsdMixin, _VptTableMixin,
                              _VptNapariMixin, _VptResultsDockMixin):
    def __init__(self, viewer, central_manager):
        self.viewer          = viewer
        self.central_manager = central_manager

    # ── helpers ────────────────────────────────────────────────────────
    def _dr(self):
        return self.central_manager.active_data_class.data_repository

    # ── Linked-selection dispatcher (plot ↔ image ↔ table brushing) ──────────
    # One hub owns "the currently selected track". Each view calls _select_track
    # with its source tag; the hub updates the OTHER views. A re-entrancy guard
    # stops the highlight it triggers in view B from firing B's own emit and
    # looping back. The shared key is track_id, which already threads through the
    # Tracks layer, the per-track MSD curves, and the summary table.
    # ── The dispatcher now lives in `utils.selection_service` ────────────────────────────
    #
    # It used to live HERE, and it was the best brushing implementation in PyCAT — with its view
    # list hardcoded to 'plot' | 'image' | 'table', so nothing outside VPT could join it. Meanwhile
    # `brushing.SelectionHub` was written as a generic lift of this code and **dropped the delayed
    # release**, which is the guard that matters; it was never wired to a real Qt view, so nobody
    # found out.
    #
    # `SelectionService` is this logic, generalised: same busy guard, same source suppression, same
    # zero-delay release, same "one dead view must not take the others down". VPT's three views are
    # now ordinary subscribers, so a plot elsewhere in PyCAT joins the same dispatcher instead of
    # reimplementing it. `tests/test_vpt_selection_characterization.py` pins the behaviour this had
    # before the move and is unchanged by it.
    def _selection(self):
        """The shared `SelectionService`. Falls back to a private one if there is no CentralManager
        (the dispatcher is usable standalone, as the rest of this state already is)."""
        service = getattr(getattr(self, 'central_manager', None), 'selection', None)
        if service is None:
            service = getattr(self, '_local_selection', None)
            if service is None:
                from pycat.utils.selection_service import SelectionService
                service = self._local_selection = SelectionService()
        return service

    def _ensure_selection_views(self):
        """Register VPT's views as subscribers, once per service. The centered-
        trajectory panel is the fourth view (added with the results dock)."""
        service = self._selection()
        if getattr(self, '_sel_views_for', None) is not service:
            service.subscribe('vpt.image', self._on_selection_image)
            service.subscribe('vpt.plot', self._on_selection_plot)
            service.subscribe('vpt.table', self._on_selection_table)
            service.subscribe('vpt.centered', self._on_selection_centered)
            self._sel_views_for = service
        return service

    @staticmethod
    def _track_of(selection):
        """The track id inside a Selection. VPT's ids are `.../vpt/track/<tid>`."""
        try:
            return int(str(selection.entity_ids[0]).rsplit('/', 1)[-1])
        except (AttributeError, IndexError, TypeError, ValueError):
            return None




    def _track_entity_id(self, tid):
        """A track's stable id, in the increment-2 `EntityKey` shape.

        VPT keys on a raw `track_id`, which is only meaningful inside one dataset's one tracking
        run — the same trap `object_id` has. Naming it properly here means a VPT selection and a
        selection from any other plot are the same kind of thing to the service, and it costs one
        function call.
        """
        from pycat.utils.entity_ref import entity_id_column, source_path_of
        source = None
        try:
            source = source_path_of(self.central_manager.active_data_class)
        except Exception:
            source = None
        return entity_id_column(source, 'vpt', 'track', None, tid)







    def _mpx(self):
        return pixel_size_um_or_default(self._dr(), context='vpt_ui')

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
        layout.setContentsMargins(4, 20, 4, 4)

        header = QLabel(
            "<b>Video Particle Tracking (Microrheology)</b><br>"
            "<span style='color:#888;font-size:9pt;'>"
            "Track fluorescent probe beads diffusing inside an in-vitro "
            "condensate to measure viscosity via the Stokes-Einstein relation."
            "</span>")
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
                    "Open a multichannel image via <b>Open/Save File(s)</b>, "
                    "or drag one onto the canvas."))
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
            report_guarantee_failure("vpt_ui: pixel-size gate", _gate_exc)

        self._add_host_segmentation(layout)
        self._add_bead_detection(layout)
        self._add_tracking(layout)
        self._add_microrheology(layout)

        main_w = QWidget(); main_w.setLayout(layout)
        main_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        from pycat.ui.ui_modules import _apply_scroll_guard
        _apply_scroll_guard(main_w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_w.setMinimumWidth(0)
        scroll.setWidget(main_w)
        self.viewer.window.add_dock_widget(scroll, name="Video Particle Tracking")

    # ── Step 2: host condensate segmentation + erosion ─────────────────

    def _seg_method_name(self):
        if self._rb_triangle.isChecked(): return 'triangle'
        if self._rb_li.isChecked():       return 'li'
        return 'otsu'

    def _host_mode(self):
        """Return the selected host mode: 'host', 'nohost', or 'infer'."""
        if self._rb_mode_nohost.isChecked(): return 'nohost'
        if self._rb_mode_infer.isChecked():  return 'infer'
        return 'host'

    def _on_host_mode_changed(self, _checked=False):
        """Enable/disable controls and relabel the action button per host mode."""
        mode = self._host_mode()
        # Host-channel-only controls (channel dropdown + threshold method) matter
        # only in 'host' mode.
        for w in (self._host_dd, self._rb_otsu, self._rb_triangle, self._rb_li):
            try: w.setEnabled(mode == 'host')
            except Exception: pass
        # Erosion applies to BOTH host and infer modes: _infer_host_from_beads()
        # also erodes the inferred mask, so its value must be live in infer mode
        # (previously it was disabled/stale in infer mode).
        try:
            self._erosion_spin.setEnabled(mode in ('host', 'infer'))
        except Exception:
            pass
        # The physical size gate only applies to inferred hosts.
        try:
            self._min_cond_radius.setEnabled(mode == 'infer')
        except Exception:
            pass
        # Relabel the action button to fit the mode.
        try:
            if mode == 'host':
                self._seg_btn.setText("▶  Segment Host & Erode")
                self._seg_btn.setEnabled(True)
            elif mode == 'infer':
                self._seg_btn.setText("▶  Infer Host from Beads")
                self._seg_btn.setEnabled(True)
            else:  # nohost
                self._seg_btn.setText("(no host — full frame)")
                self._seg_btn.setEnabled(False)
        except Exception:
            pass

    def _on_segment_host(self):
        if self._host_mode() == 'infer':
            self._infer_host_from_beads()
            return
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

    def _infer_host_from_beads(self):
        """Mode C: infer an unlabelled host from where the beads are, then
        erode it and store it as the bead-inclusion mask."""
        from pycat.toolbox.vpt_tools import (
            detect_beads_stack, infer_host_from_beads, erode_host_mask)
        name = self._bead_dd.currentText()
        if name not in [l.name for l in self.viewer.layers]:
            napari_show_warning(
                f"Bead channel '{name}' not found — select the bead channel "
                "in Step 3 first."); return
        layer_data = self.viewer.layers[name].data
        shp = getattr(layer_data, 'shape', None)
        if shp is None or len(shp) < 2:
            napari_show_warning("Bead layer has an unexpected shape."); return
        H, W = int(shp[-2]), int(shp[-1])

        # The host condensate is (approximately) stationary, so we only need a
        # handful of frames to build a stable bead-density map — detecting on
        # every frame of a long movie is both unnecessary and slow enough to
        # freeze the UI (~1s per frame => minutes on a 1000-frame stack). Sample
        # up to N_KEYFRAMES evenly-spaced frames and stream just those (frames
        # are read one at a time; the full stack is never materialised).
        # Empirically this matches the all-frames host to within a few % IoU.
        N_KEYFRAMES = 8
        if len(shp) == 3 and shp[0] > N_KEYFRAMES:
            key_idx = np.unique(
                np.linspace(0, shp[0] - 1, N_KEYFRAMES).astype(int))
        else:
            key_idx = None  # all frames (short stack) or 2D

        # Detect beads across the KEY frames (no host filter — we're building
        # one), pool their centroids into a single (N, 2) array of (y, x) px.
        try:
            det = detect_beads_stack(
                layer_data, host_mask=None,
                min_sigma=self._min_sigma.value(),
                max_sigma=self._max_sigma.value(),
                threshold=self._bead_thresh.value(),
                microns_per_pixel=1.0, fit_quality=False,
                frame_indices=key_idx)
        except Exception as e:
            napari_show_warning(f"Bead detection for host inference failed: {e}")
            return
        if det.empty:
            napari_show_warning(
                "No beads detected — cannot infer a host. Lower the detection "
                "threshold or widen the sigma range in Step 3."); return

        coords = det[['y_um', 'x_um']].values  # mpp=1.0 above, so these are px
        mpp = self._mpx()
        try:
            labeled = infer_host_from_beads(
                coords, (H, W), microns_per_pixel=mpp,
                min_condensate_radius_um=self._min_cond_radius.value())
        except Exception as e:
            napari_show_warning(f"Host inference failed: {e}"); return

        n_cond = int(labeled.max())
        if n_cond == 0:
            napari_show_warning(
                "No condensate large enough was inferred. Lower the minimum "
                "condensate radius, or this data may have no bulk-diffusion "
                "region (consider 'No host (full frame)' mode)."); return

        eroded = erode_host_mask(labeled, erosion_px=self._erosion_spin.value())
        if int(eroded.max()) == 0:
            napari_show_warning(
                "Inferred condensates vanished after erosion. Reduce the "
                "interface erosion depth."); return

        if "Inferred Host Mask" in self.viewer.layers:
            self.viewer.layers.remove("Inferred Host Mask")
        self.viewer.add_labels(eroded.astype(int), name="Inferred Host Mask")
        self._dr()['vpt_host_mask'] = eroded
        self._record('vpt_infer_host', {
            'bead_channel': name,
            'min_condensate_radius_um': self._min_cond_radius.value(),
            'erosion_px': self._erosion_spin.value(),
            'n_condensates': int(eroded.max())})
        napari_show_info(
            f"Inferred host from beads: {int(eroded.max())} condensate(s) "
            f"large enough for bulk diffusion (≥{self._min_cond_radius.value():.1f}µm "
            "radius). Boundary is INFERRED from bead distribution, not imaged.")

    # ── Step 3: bead detection ─────────────────────────────────────────

    def _on_quality_mode_changed(self, _i=0):
        """Sub-pixel and template controls only apply to fast (template) mode."""
        is_fast = self._quality_mode.currentData() == 'fast'
        for w in (self._subpixel, self._template_per_frame):
            try: w.setEnabled(is_fast)
            except Exception: pass

    def _on_detect_beads(self):
        from pycat.toolbox.vpt_tools import detect_beads_stack
        name = self._bead_dd.currentText()
        if name not in [l.name for l in self.viewer.layers]:
            napari_show_warning(f"Bead channel layer '{name}' not found."); return
        # The bead channel is a time-series (T, H, W). detect_beads_stack now
        # STREAMS frames one at a time (via iter_frames), so we pass the lazy
        # layer data straight through — no need to materialise the whole stack
        # in memory (which for a long movie is large, and for a lazy
        # _TiffPageStack, np.asarray() would collapse to frame 0). This keeps
        # memory flat regardless of movie length.
        stack = self.viewer.layers[name].data
        host_mask = self._dr().get('vpt_host_mask')
        mode = self._host_mode()
        if mode == 'host' and host_mask is None:
            # Host-channel mode genuinely needs the mask from Step 2.
            napari_show_warning(
                "No host mask found — run Step 2 first so beads near the "
                "condensate interface can be excluded. (Or switch Host mode to "
                "'No host (full frame)' if this data has no condensate "
                "boundary, e.g. a beads-in-glycerol control.)"); return
        if mode == 'infer' and host_mask is None:
            # Infer mode is meant to FILTER beads by the host inferred from the
            # bead distribution — but that requires the inferred mask to exist.
            napari_show_warning(
                "No inferred host mask found — click 'Infer Host from Beads' "
                "first so the inferred boundary can filter beads. (Or switch to "
                "'No host (full frame)' to track every bead.)"); return
        if mode == 'nohost':
            # No-host / full-frame: track every bead across the whole field.
            # (The detection layer treats host_mask=None as "keep all".)
            host_mask = None
        # mode == 'host' or 'infer': keep the (segmented or inferred) host_mask
        # so beads outside/near the boundary are excluded as intended.

        # Determine frame count for a REAL (determinate) progress bar and a
        # runtime estimate, without materialising the stack.
        _shp = getattr(stack, 'shape', None)
        n_frames = int(_shp[0]) if (_shp is not None and len(_shp) == 3) else 1
        qmode = self._quality_mode.currentData()

        # Warn before a long run. Rough per-frame serial cost (seconds): fast
        # ~0.8, fast_fit ~3, precise ~10. Fast mode is accelerated (GPU if
        # present, else a CPU process pool), so divide the serial estimate by the
        # expected speedup — otherwise the estimate always reads the serial
        # worst case (e.g. ~13 min for 1000 frames) even when detection actually
        # runs several times faster.
        per_frame = {'fast': 0.8, 'fast_fit': 3.0, 'precise': 10.0}.get(qmode, 0.8)
        speedup = 1.0
        if qmode == 'fast':
            try:
                from pycat.toolbox.gpu_utils import gpu_available
                if gpu_available():
                    speedup = 6.0            # GPU LoG convolutions (approx)
                else:
                    import os as _os
                    speedup = max(1.0, min(8, (_os.cpu_count() or 2) - 1))
            except Exception:
                speedup = 1.0
        est_sec = per_frame * n_frames / speedup
        if est_sec > 120:
            from qtpy.QtWidgets import QMessageBox
            mins = est_sec / 60.0
            _accel = ("GPU" if speedup >= 6 else
                      (f"{int(speedup)} CPU workers" if speedup > 1 else "serial"))
            resp = QMessageBox.question(
                None, "Long detection run",
                f"Detecting beads in {n_frames} frames in "
                f"'{self._quality_mode.currentText().split(' —')[0]}' mode is "
                f"estimated to take about {mins:.0f} minute(s) ({_accel}).\n\n"
                "Tip: 'Fast (template match)' mode is much quicker. Proceed?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if resp != QMessageBox.Yes:
                return

        # Determinate progress bar (0..n_frames) so it visibly advances per
        # frame, rather than an indeterminate spinner stuck at 0%. Label the
        # phase so the wait is self-explanatory: the bar first covers reading/
        # decoding frames from disk (materialisation), then per-frame detection.
        # Without the label the bar appears to "run twice" — it fills during
        # detection after an unexplained pause while frames materialise.
        self._bead_prog.setVisible(True)
        self._bead_prog.setRange(0, max(1, n_frames))
        self._bead_prog.setValue(0)
        self._bead_prog.setTextVisible(True)
        self._bead_prog.setFormat("Preparing frames… %p%")
        self._bead_detect_started = False

        subpixel = self._subpixel.isChecked()
        template_mode = ('per_frame' if self._template_per_frame.isChecked()
                         else 'per_stack')
        bead_nm = self._bead_size_nm.value() or None
        template_type = self._template_type.currentData()
        # Merge radius (px) from the physical bead size, if de-dup is enabled.
        merge_radius = None
        if self._dedup_rings.isChecked() and bead_nm:
            try:
                from pycat.toolbox.vpt_tools import bead_half_from_size
                merge_radius = bead_half_from_size(bead_nm, self._mpx(), n_rings=1)
            except Exception:
                merge_radius = None
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
                quality_mode=qmode, subpixel=subpixel,
                template_mode=template_mode,
                bead_size_nm=bead_nm,
                template_type=template_type,
                merge_radius_px=merge_radius,
                strictness=self._strictness.value(),
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
            # Auto-estimate a physically-grounded max linking distance from the
            # bead MOTION (short-window time-projection: proj width vs single-
            # frame PSF width → per-frame displacement), and pre-fill the linker
            # field. Anti-black-box: the derived value is shown and remains
            # user-editable. This fixes the core linker failure where a too-tight
            # default clipped the beads' own jitter and shattered stable beads
            # into short tracks that can't support the MSD measurement window.
            try:
                from pycat.toolbox.vpt_tools import estimate_linking_distance_um
                _bname = self._bead_dd.currentText()
                _stack = self.viewer.layers[_bname].data if _bname in self.viewer.layers else None
                # Sample coords from the first detected frame for the estimate.
                _cbf = None
                if _stack is not None and 'frame' in det_df:
                    f0 = int(det_df['frame'].min())
                    sub = det_df[det_df['frame'] == f0]
                    _cbf = {f0: list(zip(sub['y_um'] / self._mpx(),
                                         sub['x_um'] / self._mpx()))}
                _kfac = (self._link_k.value()
                         if hasattr(self, '_link_k') else 2.5)
                if _stack is not None:
                    est = estimate_linking_distance_um(
                        _stack, coords_by_frame=_cbf,
                        microns_per_pixel=self._mpx(), k=_kfac)
                    d = est.get('linking_distance_um')
                    if d and np.isfinite(d) and d > 0:
                        self._max_link.blockSignals(True)
                        self._max_link.setValue(round(float(d), 3))
                        self._max_link.blockSignals(False)
                        self._dr()['vpt_link_distance_estimate'] = est
                        _mo = est.get('motion_sigma_um', float('nan')) * 1000
                        print(f"[PyCAT VPT] Auto-set max linking distance = "
                              f"{d:.3f} µm (per-frame motion ≈ {_mo:.0f} nm × "
                              f"k={_kfac}{', capped at bead footprint' if est.get('capped') else ''}). "
                              f"Editable in Step 4.")
                        # Assess frame-to-frame linking reliability for THIS movie
                        # (ratio of bead motion to nearest-neighbour spacing) and
                        # surface it as an info tag by the linker choice.
                        try:
                            from pycat.toolbox.vpt_tools import assess_linking_conditions
                            cond = assess_linking_conditions(
                                det_df, motion_sigma_um=est.get('motion_sigma_um'),
                                microns_per_pixel=self._mpx())
                            self._dr()['vpt_linking_conditions'] = cond
                            if hasattr(self, '_link_cond_lbl'):
                                _colour = {'safe': '#2e7d32', 'caution': '#f9a825',
                                           'risky': '#ef6c00', 'unsafe': '#c62828'}.get(
                                               cond['level'], '#666')
                                _tag = {'safe': 'SAFE', 'caution': 'CAUTION',
                                        'risky': 'RISKY', 'unsafe': 'UNSAFE'}.get(
                                            cond['level'], '')
                                self._link_cond_lbl.setText(
                                    f"<b style='color:{_colour}'>Linking conditions: "
                                    f"{_tag}</b><br><span style='color:#888'>"
                                    f"{cond['message']}</span>")
                        except Exception as _e2:
                            print(f"[PyCAT VPT] linking-conditions tag skipped: {_e2}")
            except Exception as _e:
                print(f"[PyCAT VPT] linking-distance auto-estimate skipped: {_e}")
            # Add a points layer for visual confirmation, coloured by class
            pts = det_df[['frame', 'y_um', 'x_um']].copy()
            pts['y_px'] = pts['y_um'] / self._mpx()
            pts['x_px'] = pts['x_um'] / self._mpx()
            coords = pts[['frame', 'y_px', 'x_px']].values
            if "Bead Detections" in self.viewer.layers:
                self.viewer.layers.remove("Bead Detections")
            if 'bead_class' in det_df.columns:
                cmap = {'singlet': '#00ff00', 'aggregate': '#ff3b30',
                        'out_of_plane': '#ffcc00', 'ambiguous': '#3b9dff',
                        'unfit': '#888888'}
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
                   'quality_mode': self._quality_mode.currentData(),
                   'subpixel': self._subpixel.isChecked(),
                   'template_mode': ('per_frame' if self._template_per_frame.isChecked() else 'per_stack')}
            if 'bead_class' in det_df.columns:
                counts = det_df['bead_class'].value_counts().to_dict()
                rec['class_counts'] = counts
            # Record the classification thresholds actually used (#11), so a
            # fast-mode run is reproducible and the regime is auditable.
            try:
                _thr = det_df.attrs.get('classify_thresholds')
                if _thr:
                    rec['classify_thresholds'] = _thr
            except Exception:
                pass
            self._record('vpt_detect_beads', rec)

            if 'bead_class' in det_df.columns:
                counts = det_df['bead_class'].value_counts().to_dict()
                # Show a per-class summary table
                try:
                    import pandas as pd
                    from pycat.ui.ui_utils import show_dataframes_dialog
                    # Fast template mode has no sigma_mean (no Gaussian fit);
                    # build the aggregation from whichever columns are present so
                    # the summary table still renders instead of silently failing.
                    _agg = {'n': ('bead_class', 'size')}
                    if 'sigma_mean' in det_df.columns:
                        _agg['median_sigma'] = ('sigma_mean', 'median')
                    if 'ncc' in det_df.columns:
                        _agg['median_ncc'] = ('ncc', 'median')
                    if 'integrated_intensity' in det_df.columns:
                        _agg['median_intensity'] = ('integrated_intensity', 'median')
                    if 'n_units_est' in det_df.columns:
                        _agg['median_n_units'] = ('n_units_est', 'median')
                    summ = det_df.groupby('bead_class').agg(**_agg).reset_index()
                    show_dataframes_dialog("Bead Quality Classes",
                                           [('Per-class summary', summ.round(3))])
                except Exception as _e:
                    from pycat.utils.general_utils import debug_log
                    debug_log("vpt_ui: bead-class summary table failed", _e)
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
        def _on_bead_progress(i, n):
            # First real per-frame tick means materialisation is done and
            # detection has begun — relabel so the phase is explicit.
            if not getattr(self, '_bead_detect_started', False) and i > 0:
                self._bead_detect_started = True
                self._bead_prog.setFormat("Detecting beads… %p%")
            self._bead_prog.setValue(i)
        w.finished.connect(_done); w.error.connect(_err)
        w.progress.connect(_on_bead_progress)
        self._bead_worker = w; w.start()

    # ── Step 4: trajectory linking ─────────────────────────────────────

    def _linker_name(self):
        if self._rb_bayesian.isChecked(): return 'bayesian'
        if self._rb_greedy.isChecked():   return 'greedy'
        return 'trackmate'

    def _on_link(self):
        from pycat.toolbox.vpt_tools import (
            _link, drift_correct_com, split_bead_populations,
            aggregate_population_stats, reclassify_by_temporal_stability,
            select_bead_population)
        det = self._dr().get('vpt_detections')
        if det is None or det.empty:
            napari_show_warning("No bead detections found — run Step 3 first."); return

        has_classes = 'bead_class' in det.columns
        pop_which = (self._pop_choice.currentData() if has_classes else 'singlet')
        # Determinate progress bar (0..n_frames) so it advances visibly per
        # frame instead of spinning indefinitely. The linker is sequential
        # across frames, so per-frame progress is the natural unit.
        _n_link_frames = int(det['frame'].nunique()) if 'frame' in det else 0
        self._track_prog.setVisible(True)
        self._track_prog.setRange(0, max(1, _n_link_frames))
        self._track_prog.setValue(0)

        def _job(progress):
            if has_classes:
                # The chosen population drives microrheology; aggregates are
                # ALWAYS tracked separately (never in the viscosity set).
                pops = split_bead_populations(det)
                primary = select_bead_population(det, pop_which)
                aggregates = pops['aggregate']
            else:
                primary, aggregates = det, det.iloc[0:0]
            _drift_mode = self._drift_mode.currentData() if hasattr(self, '_drift_mode') else 'com'
            ptracks = drift_correct_com(
                _link(primary, self._linker_name(), self._max_link.value(),
                      self._max_gap.value(), self._mpx(),
                      progress_callback=progress),
                mode=_drift_mode)
            # Temporal stability pass: a dim track that persists stably across
            # frames is a real (faint) bead, not an out-of-focus blink — promote
            # it back to singlet. Blinking dim tracks stay yellow. This is a
            # judgement call that affects the viscosity population, so it is an
            # explicit, recorded choice (#10): with it OFF, out-of-plane tracks
            # are never merged into the singlet set, giving a stricter,
            # singlet-only viscosity that excludes any defocused bead whose
            # axial fluctuations could masquerade as 2D motion.
            _promote = (self._promote_stable.isChecked()
                        if hasattr(self, '_promote_stable') else True)
            if _promote:
                ptracks = reclassify_by_temporal_stability(ptracks)
            atracks = None
            if len(aggregates) >= 2:
                try:
                    atracks = _link(aggregates, self._linker_name(),
                                    self._max_link.value(), self._max_gap.value(),
                                    self._mpx())
                except Exception:
                    atracks = None
            total_by_frame = det.groupby('frame').size()
            astats = (aggregate_population_stats(aggregates, total_by_frame=total_by_frame)
                      if len(aggregates) else None)
            return dict(primary=ptracks, aggregate_tracks=atracks,
                        aggregate_stats=astats, aggregates=aggregates)

        w = _VPTWorker(_job)
        def _done(res):
            self._track_prog.setVisible(False)
            tracks = res['primary']
            if tracks.empty:
                napari_show_warning("Linking produced no trajectories."); return
            tracks = tracks[tracks['track_id'] != -1] if 'track_id' in tracks else tracks
            # Guard against a degenerate link (almost every detection its own
            # single-frame "track"). Building a napari Tracks layer + histogram
            # from tens of thousands of length-1 tracks can hang/crash the GUI.
            # This should not happen after the gap off-by-one fix, but a bad
            # parameter combination could still produce it, so fail loudly
            # instead of freezing.
            try:
                _tl = tracks.groupby('track_id').size()
                _n_tracks = int(len(_tl))
                _frac_singleton = float((_tl <= 1).mean()) if _n_tracks else 0.0
                if _n_tracks > 2000 and _frac_singleton > 0.9:
                    napari_show_warning(
                        f"Linking looks degenerate: {_n_tracks} tracks and "
                        f"{_frac_singleton*100:.0f}% are single-frame — almost "
                        f"nothing linked. Check the linker settings (max linking "
                        f"distance may be too small, or the population empty). "
                        f"Not building the trajectory layer to avoid freezing.")
                    self._update_tracklen_hist(tracks)
                    return
            except Exception:
                pass
            self._dr()['vpt_tracks'] = tracks
            mpp = self._mpx()

            # Find the bead image layer to match its scale — a Tracks layer
            # added with no scale sits at raw pixel coordinates, so if the image
            # carries a (µm or upscaled) scale the tracks render at a different
            # world extent (the symptom: a full-width streak next to a tiny
            # image). Copy the image layer's spatial scale onto the tracks.
            _bead_name = self._bead_dd.currentText()
            _img_layer = None
            try:
                import napari.layers as _nl
                for _l in self.viewer.layers:
                    if isinstance(_l, _nl.Image):
                        if _bead_name and _l.name == _bead_name:
                            _img_layer = _l; break
                        if _img_layer is None:
                            _img_layer = _l   # fallback: first image layer
            except Exception:
                _img_layer = None

            def _tracks_layer(tr, name, color=None):
                tl = tr[['track_id', 'frame']].copy()
                tl['y'] = tr['y_um_raw'] / mpp if 'y_um_raw' in tr else tr['y_um'] / mpp
                tl['x'] = tr['x_um_raw'] / mpp if 'x_um_raw' in tr else tr['x_um'] / mpp
                if name in self.viewer.layers:
                    self.viewer.layers.remove(name)
                add_kwargs = {}
                # Match the image layer's spatial (y, x) scale so the tracks
                # overlay the image 1:1. Tracks data is (track_id, frame, y, x),
                # so the scale vector is (frame_scale, y_scale, x_scale).
                if _img_layer is not None:
                    try:
                        import numpy as _np
                        isc = _np.asarray(_img_layer.scale, float)
                        if isc.size >= 2:
                            yx = isc[-2:]
                            add_kwargs['scale'] = [1.0, float(yx[0]), float(yx[1])]
                    except Exception:
                        pass
                self.viewer.add_tracks(
                    tl[['track_id', 'frame', 'y', 'x']].values, name=name,
                    **add_kwargs)

            _tracks_layer(tracks, "Bead Trajectories")

            # Companion PICKABLE Points layer carrying per-point track identity.
            # napari Tracks layers expose no per-track click/pick API, so to make
            # the image->plot direction work (click a bead -> highlight its MSD
            # curve + its table row) we add a Points layer where every point knows
            # its track_id. Clicking a point resolves to that track_id and drives
            # the linked-selection dispatcher. One point per bead per frame so a
            # bead is clickable on whatever frame the user is viewing.
            try:
                self._add_pickable_bead_points(tracks, _img_layer, mpp)
            except Exception as _e:
                print(f"[PyCAT VPT] pickable bead layer failed: {_e}")

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
                'microrheology_population': pop_which,
                'n_aggregate_tracks': n_agg_tracks})
            msg = (f"Linked {tracks['track_id'].nunique()} primary trajectories "
                   f"(drift-corrected) from the {pop_which} population.")
            if n_agg_tracks:
                msg += f" Aggregate population: {n_agg_tracks} tracks."
            self._update_tracklen_hist(tracks)
            napari_show_info(msg)
        def _err(msg):
            self._track_prog.setVisible(False)
            napari_show_warning("Linking failed — see terminal."); print(msg)
        w.finished.connect(_done); w.error.connect(_err)
        w.progress.connect(lambda i, n: self._track_prog.setValue(i))
        self._track_worker = w; w.start()




    # How much of the image (in pixels) to frame around the bead when zooming to it on a click.
    _BEAD_ZOOM_WINDOW_PX = 80.0


    # Line widths for the two trajectory layers, in napari Tracks `tail_width` units (screen pixels, so
    # they DON'T balloon as you zoom to the bead — the earlier Shapes `edge_width` was in data units and
    # did). The base "Bead Trajectories" width the user found right; the picked track is 2× it, bold
    # enough to stand out while still thin enough to read the trajectory's detail.
    _BASE_TRACK_TAIL_WIDTH = 2.0
    _PICKED_TRACK_TAIL_WIDTH = 2.0 * _BASE_TRACK_TAIL_WIDTH

    # The pulsing ring was removed (1.6.104). It oscillated the Points layer's opacity/size on a
    # QTimer, but a per-frame ring is only present on the bead's own frame, so away from that frame
    # there was nothing to glow — the user saw the opacity slider churning with no visible effect, and
    # a continuous repaint for nothing. A static ring plus the zoom-to-bead navigation is clearer and
    # cheaper.


    # Solid-orange colormap name for the picked-track Tracks overlay. napari Tracks colour by a
    # feature via a REGISTERED colormap name (a Colormap object is rejected), so a flat two-stop orange
    # ramp is registered once; with one track_id the whole track maps to that single colour.
    _PICKED_COLORMAP = 'pycat_picked_orange'







    # ── Step 5: microrheology ──────────────────────────────────────────

    def _sync_frame_interval_from_metadata(self):
        """If the loaded file's metadata captured a frame interval, use it as the
        Step-5 default (unless the user has already changed the field). The
        interval is captured once at load into file_metadata, so every timing-
        dependent step reads it from one place instead of re-asking the user."""
        try:
            if getattr(self, '_frame_dt_touched', False):
                return  # user set it explicitly — never override
            md = self._dr().get('file_metadata') or {}
            fi = (md.get('common') or {}).get('frame_interval_s')
            if fi and fi > 0:
                # Set programmatically WITHOUT flipping the user-touched flag.
                self._frame_dt.blockSignals(True)
                self._frame_dt.setValue(float(fi))
                self._frame_dt.blockSignals(False)
        except Exception:
            pass

    def _on_rheology(self):
        from pycat.toolbox.condensate_physics_tools import (
            compute_msd, fit_anomalous_diffusion)
        from pycat.toolbox.vpt_tools import viscosity_from_diffusion
        # VPT treats frames as TIME (MSD, viscosity) — if the stack's axis was
        # assumed at load (undeclared multipage TIFF), warn once.
        try:
            from pycat.file_io.file_io import warn_if_assumed_axis
            warn_if_assumed_axis(self._dr(),
                                 "Video particle tracking (treats frames as time)")
        except Exception as _axis_exc:
            # NOT cosmetic: this is the T-vs-Z check. If this stack is really a Z-series,
            # 'time' is depth and the dynamics being reported are not dynamics at all.
            # It was swallowed in COMPLETE SILENCE.
            from pycat.utils.general_utils import report_guarantee_failure
            report_guarantee_failure('vpt_ui: warn_if_assumed_axis', _axis_exc)
        # Pull the frame interval from the file's captured metadata if available.
        self._sync_frame_interval_from_metadata()
        tracks = self._dr().get('vpt_tracks')
        if tracks is None or tracks.empty:
            napari_show_warning("No trajectories found — run Step 4 first."); return

        try:
            msd_df = compute_msd(
                tracks,
                frame_interval_s=self._frame_dt.value(),
                min_track_length=self._min_track.value())
            fit = fit_anomalous_diffusion(
                msd_df,
                frame_interval_s=self._frame_dt.value(),
                upper_lag_rule=(self._lag_rule.currentData()
                                if hasattr(self, '_lag_rule') else 'fraction'),
                upper_lag_fraction=(self._lag_fraction.value()
                                    if hasattr(self, '_lag_fraction') else 0.25),
                upper_lag_fixed_s=(self._lag_fixed_s.value()
                                   if hasattr(self, '_lag_fixed_s') else None),
                min_independent_pairs=(self._lag_minpairs.value()
                                       if hasattr(self, '_lag_minpairs') else 10),
                confine_to_defensible_bounds=(
                    self._lag_confine.isChecked()
                    if hasattr(self, '_lag_confine') else True))
            _fw = fit.get('fit_window_warning')
            if _fw:
                napari_show_warning(f"MSD fit window: {_fw}")
                print(f"[PyCAT VPT] MSD fit-window note: {_fw}")
            _win = fit.get('fit_window_s')
            if _win and all(v is not None for v in _win):
                print(f"[PyCAT VPT] MSD fit confined to defensible lag window "
                      f"{_win[0]:.3g}–{_win[1]:.3g}s "
                      f"({'confined' if (self._lag_confine.isChecked() if hasattr(self,'_lag_confine') else True) else 'full range (confine off)'}).")
            eta = viscosity_from_diffusion(
                fit.get('D_um2_per_s', float('nan')),
                self._bead_radius.value(), self._temp_C.value())
        except Exception as e:
            napari_show_warning(f"Microrheology failed: {e}"); return

        self._dr()['vpt_msd_df'] = msd_df
        self._dr()['vpt_fit'] = fit
        self._dr()['vpt_eta_Pa_s'] = eta

        # Build the viscosity as a MEASUREMENT that carries its provenance and its
        # assumptions, not just a float. eta is proportional to 1/R, so where the bead
        # radius came from is part of the result, not metadata about it.
        try:
            from pycat.toolbox.vpt_tools import viscosity_measurement
            _src = (self._radius_source.currentText()
                    if hasattr(self, '_radius_source') else 'manufacturer')
            _note = (self._radius_note.text().strip()
                     if hasattr(self, '_radius_note') else '')
            _vm = viscosity_measurement(
                D_um2_per_s=fit.get('D_um2_per_s', float('nan')),
                bead_radius_um=self._bead_radius.value(),
                temperature_C=self._temp_C.value(),
                radius_source=_src,
                # ── The interval on D, propagated to the viscosity ──────────────
                #
                # `fit_anomalous_diffusion` computes a 95% CI on D from the fit covariance
                # (1.5.447), and `viscosity_measurement` already knows how to propagate it —
                # but NOTHING WAS PASSING IT. The interval was computed, the consumer could
                # take it, and the two were never connected.
                #
                # Stokes-Einstein is eta = kT/(6*pi*R*D), so the interval propagates exactly
                # and INVERTS: a low D gives a HIGH viscosity. On the measured MSD intervals
                # the viscosity spans a factor of 1.7-1.9 between the ends — on the number
                # that goes into the paper.
                D_ci=(fit.get('identifiability', {})
                         .get('D_um2_per_s', {})
                         .get('ci', None)),
                alpha=fit.get('alpha', None),
                n_tracks=(int(tracks['track_id'].nunique())
                          if tracks is not None and len(tracks) else None),
            )
            if _note:
                _vm.notes.append(f'Bead radius: {_note}')
            self._dr()['vpt_viscosity_measurement'] = _vm
            print('[PyCAT VPT] ' + _vm.summary().replace(chr(10), chr(10) + '[PyCAT VPT] '))
        except Exception as _e:
            print(f'[PyCAT VPT] measurement record failed: {_e}')

        D = fit.get('D_um2_per_s', float('nan'))
        alpha = fit.get('alpha', float('nan'))
        r2 = fit.get('r_squared', float('nan'))
        motion = fit.get('motion_type', 'unknown')

        self._record('vpt_microrheology', {
            'frame_interval_s': self._frame_dt.value(),
            'bead_radius_um': self._bead_radius.value(),
            'bead_radius_source': (self._radius_source.currentText()
                                   if hasattr(self, '_radius_source') else 'manufacturer'),
            'bead_radius_note': (self._radius_note.text().strip()
                                 if hasattr(self, '_radius_note') else ''),
            'temperature_C': self._temp_C.value(),
            'min_track_length': self._min_track.value(),
            'drift_mode': (self._drift_mode.currentData()
                           if hasattr(self, '_drift_mode') else 'com'),
            'promote_stable_to_singlet': (self._promote_stable.isChecked()
                                          if hasattr(self, '_promote_stable') else True),
            'D_um2_per_s': D, 'alpha': alpha, 'eta_Pa_s': eta})

        # Graphs: consolidated 2×2 panel (MSD spaghetti, Evans G′/G″, centered
        # trajectories, van Hove displacement distribution) in one window by
        # default, with a button to pop them into separate windows. The MSD panel
        # keeps click-to-reveal brushing via the standalone plot when separate.
        try:
            from pycat.toolbox.condensate_physics_tools import (
                per_track_msd_curves, compute_moduli_evans,
                compute_moduli_evans_bootstrap)
            from pycat.toolbox.analysis_plots import (
                plot_msd_trajectories, plot_moduli, plot_vpt_panel)
            ptc = per_track_msd_curves(
                tracks, frame_interval_s=self._frame_dt.value(),
                min_track_length=self._min_track.value())
            _boot = (self._moduli_boot.isChecked()
                     if hasattr(self, '_moduli_boot') else False)
            if _boot:
                mod = compute_moduli_evans_bootstrap(
                    ptc, self._bead_radius.value(), self._temp_C.value(),
                    dimensions=2,
                    n_boot=(self._moduli_nboot.value()
                            if hasattr(self, '_moduli_nboot') else 200))
            else:
                mod = compute_moduli_evans(msd_df, self._bead_radius.value(),
                                           self._temp_C.value(), dimensions=2)
            self._dr()['vpt_moduli_df'] = mod
            if len(mod):
                print("[PyCAT VPT] G'/G'' (storage/loss moduli) use the Evans "
                      "et al. (2009) direct compliance->moduli conversion, "
                      "validated in-sandbox against analytic MSDs (exact on a "
                      "pure viscous fluid; ~1-2% on a Maxwell fluid across the "
                      "reliable band). The highest one or two frequencies "
                      "(shortest lags) are the least reliable and are dropped. "
                      + ("Bootstrap confidence bands (track resampling) are "
                         "shown; treat them as approximate. " if _boot else "")
                      + "Accuracy still depends on the input MSD: fragmented or "
                      "noisy trajectories degrade G'/G'' just as they degrade "
                      "the viscosity fit, so confirm the MSD is clean first.")
        except Exception as e:
            print(f"[PyCAT] VPT plots failed: {e}")

        # ── Present the results ────────────────────────────────────────────────
        # Combined dock by default (2×2 figure + per-track table + bucket pager in
        # one persistent widget, so brushing highlight targets stay alive); the
        # `_plots_consolidated` checkbox opts out to pop-out windows + the table
        # dialog. Its own try so a plotting failure above still shows the table;
        # `ptc`/`mod` read defensively in case they were not computed.
        try:
            from pycat.toolbox.analysis_plots import (
                plot_msd_trajectories, plot_vpt_panel)
            _ptc = ptc if 'ptc' in dir() else None
            _mod = mod if 'mod' in dir() else None
            _ptm = self._build_per_track_metrics(tracks, _ptc)
            self._dr()['vpt_per_track_metrics'] = _ptm

            _consolidated = True
            if hasattr(self, '_plots_consolidated'):
                _consolidated = self._plots_consolidated.isChecked()
            if _consolidated:
                self._show_vpt_results(_ptc, msd_df, fit, _mod, tracks, _ptm,
                                       self._frame_dt.value(), van_hove_lag=1)
            else:
                # Separate windows: the interactive MSD plot (with brushing) + the
                # standalone spread/moduli windows + the standalone table dialog.
                self._msd_line_registry = {}
                _render_mode = ('all' if (hasattr(self, '_plots_draw_all')
                                          and self._plots_draw_all.isChecked())
                                else 'auto')
                plot_msd_trajectories(
                    _ptc, msd_df, fit,
                    title="VPT MSD (per-track + ensemble)",
                    interactive=True,
                    on_pick_track=lambda tid: self._select_track(tid, source='plot'),
                    line_registry=self._msd_line_registry,
                    render_mode=_render_mode)
                plot_vpt_panel(_ptc, msd_df, fit, _mod, tracks_df=tracks,
                               frame_interval_s=self._frame_dt.value(),
                               van_hove_lag=1, consolidated=False,
                               interactive=True)
                self._show_per_track_table(_ptm)
        except Exception as e:
            print(f"[PyCAT VPT] results presentation failed: {e}")

        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            summary = pd.DataFrame([{
                'viscosity (Pa·s)': round(eta, 4) if eta == eta else None,
                'D (µm²/s)': round(D, 5) if D == D else None,
                'alpha': round(alpha, 3) if alpha == alpha else None,
                'motion': motion,
                'R²': round(r2, 3) if r2 == r2 else None,
                'localization err (nm)': (round(fit.get('localization_error_nm'), 1)
                                          if fit.get('localization_error_nm') ==
                                          fit.get('localization_error_nm') else None),
                'n_tracks': int(tracks['track_id'].nunique()),
            }])
            show_dataframes_dialog("VPT Microrheology Results",
                                   [('Summary', summary), ('MSD', msd_df)])
        except Exception:
            pass

        napari_show_info(
            f"Microrheology complete: η={eta:.4g} Pa·s "
            f"(D={D:.4g} µm²/s, α={alpha:.3g}, {motion}; "
            f"n={tracks['track_id'].nunique()} tracks).")
