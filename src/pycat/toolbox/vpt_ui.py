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


class VideoParticleTrackingUI:
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
        """Register VPT's three views as subscribers, once per service."""
        service = self._selection()
        if getattr(self, '_sel_views_for', None) is not service:
            service.subscribe('vpt.image', self._on_selection_image)
            service.subscribe('vpt.plot', self._on_selection_plot)
            service.subscribe('vpt.table', self._on_selection_table)
            self._sel_views_for = service
        return service

    @staticmethod
    def _track_of(selection):
        """The track id inside a Selection. VPT's ids are `.../vpt/track/<tid>`."""
        try:
            return int(str(selection.entity_ids[0]).rsplit('/', 1)[-1])
        except (AttributeError, IndexError, TypeError, ValueError):
            return None

    def _on_selection_image(self, selection):
        tid = self._track_of(selection)
        if tid is None:
            return
        try:
            self._reveal_track_in_viewer(tid)
        except Exception as e:
            print(f"[PyCAT VPT] link→image failed: {e}")

    def _on_selection_plot(self, selection):
        tid = self._track_of(selection)
        if tid is None:
            return
        try:
            self._highlight_track_in_plot(tid)
        except Exception as e:
            print(f"[PyCAT VPT] link→plot failed: {e}")

    def _on_selection_table(self, selection):
        tid = self._track_of(selection)
        if tid is None:
            return
        try:
            self._highlight_track_in_table(tid)
        except Exception as e:
            print(f"[PyCAT VPT] link→table failed: {e}")

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

    def _select_track(self, track_id, source=None):
        """Select a track everywhere. `source` is the view that initiated it
        ('plot'|'image'|'table'); it is skipped when propagating, so a view never re-highlights
        from its own action.

        A thin adapter now: the guard, the suppression and the delayed release all live in
        `SelectionService`. See the note above the helpers for why they moved.
        """
        if track_id is None:
            return
        tid = int(track_id)
        service = self._ensure_selection_views()

        from pycat.utils.selection_service import Selection
        selection = Selection(
            entity_ids=(self._track_entity_id(tid),),
            primary_id=self._track_entity_id(tid),
            mode='selected',
            source_view=f'vpt.{source}',
            generation=service.next_generation(),
        )
        # Recorded before propagating: the other views (and `_reveal_track_in_viewer`) read it, and
        # they read it DURING the propagation this triggers.
        previous = getattr(self, '_selected_track_id', None)
        self._selected_track_id = tid
        if not service.select(selection):
            self._selected_track_id = previous      # suppressed — nothing was propagated

    def _highlight_track_in_plot(self, track_id):
        """Emphasise a track's MSD curve in the live plot (if one is open and its
        line map was registered). No-op if the plot isn't showing.

        Uses the plot's blit path (redraw only the changed lines) rather than a
        full-figure redraw, so image/table -> plot highlighting is as fast as a
        direct plot click."""
        reg = getattr(self, '_msd_line_registry', None)
        if not reg:
            return
        lines = reg.get('lines'); canvas = reg.get('canvas')
        state = reg.setdefault('state', {'prev': None})
        blit = reg.get('blit_highlight')
        if not lines:
            return
        prev = state.get('prev')
        ln = lines.get(int(track_id))
        if ln is prev:
            return   # already highlighted — nothing to redraw
        if prev is not None and prev in lines.values():
            try:
                prev.set_color('#4c72b0'); prev.set_alpha(0.18); prev.set_linewidth(0.8)
                prev.set_zorder(1)
            except Exception:
                pass
        if ln is not None:
            try:
                ln.set_color('#ff8c00'); ln.set_alpha(1.0); ln.set_linewidth(2.2)
                ln.set_zorder(5)
                state['prev'] = ln
                if blit is not None:
                    blit(ln, prev)          # fast: only the two changed lines
                elif canvas is not None:
                    canvas.draw_idle()      # fallback
            except Exception:
                pass

    def _highlight_track_in_table(self, track_id):
        """Select the track's row in the summary table (if the linked table is
        open and registered)."""
        reg = getattr(self, '_track_table_registry', None)
        if not reg:
            return
        table = reg.get('table'); id_col = reg.get('id_col', 0)
        row_for_id = reg.get('row_for_id')
        if table is None or row_for_id is None:
            return
        r = row_for_id.get(int(track_id))
        if r is None:
            return
        try:
            table.blockSignals(True)
            table.selectRow(r)
            table.scrollToItem(table.item(r, id_col))
        except Exception:
            pass
        finally:
            try:
                table.blockSignals(False)
            except Exception:
                pass

    def _build_per_track_metrics(self, tracks, ptc):
        """Per-track summary rows: track_id, n_frames, duration, and a per-track
        D/α from a quick power-law fit of that track's own MSD curve. Returns a
        DataFrame (one row per track) for the linked per-track table."""
        import numpy as _np
        import pandas as _pd
        rows = []
        dt = self._frame_dt.value()
        # group per-track MSD once
        by_tid = {tid: g.sort_values('lag_s')
                  for tid, g in ptc.groupby('track_id')} if ptc is not None else {}
        for tid, g in tracks.groupby('track_id'):
            if tid < 0:
                continue
            n = int(len(g))
            dur = n * dt
            D = _np.nan; alpha = _np.nan
            mg = by_tid.get(tid)
            if mg is not None and len(mg) >= 3:
                lag = mg['lag_s'].values.astype(float)
                msd = mg['msd_um2'].values.astype(float)
                ok = (lag > 0) & (msd > 0)
                if ok.sum() >= 3:
                    # log-log linear fit: slope = alpha, intercept -> D (msd=4Dτ^α)
                    p = _np.polyfit(_np.log(lag[ok]), _np.log(msd[ok]), 1)
                    alpha = float(p[0])
                    D = float(_np.exp(p[1]) / 4.0)
            rows.append({'track_id': int(tid), 'n_frames': n,
                         'duration_s': round(dur, 3),
                         'D_um2_per_s': (round(D, 5) if D == D else None),
                         'alpha': (round(alpha, 3) if alpha == alpha else None)})
        return _pd.DataFrame(rows).sort_values('track_id').reset_index(drop=True)

    def _show_per_track_table(self, per_track_metrics):
        """Non-modal per-track results table. Clicking a row selects that track
        everywhere (row → plot curve + image bead) via the dispatcher, and the
        dispatcher can select a row here when the selection comes from elsewhere.
        Registers _track_table_registry {table, row_for_id, id_col}."""
        try:
            from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QTableWidget,
                                         QTableWidgetItem, QLabel)
            from PyQt5.QtCore import Qt
        except Exception:
            return
        if per_track_metrics is None or per_track_metrics.empty:
            return
        dlg = QDialog(self.viewer.window._qt_window)
        dlg.setWindowTitle("VPT — per-track results (click a row to highlight)")
        dlg.setMinimumSize(460, 420)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("Click a track row to reveal it in the image and "
                           "highlight its MSD curve."))
        cols = list(per_track_metrics.columns)
        table = QTableWidget(len(per_track_metrics), len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        id_col = cols.index('track_id') if 'track_id' in cols else 0
        row_for_id = {}
        for r in range(len(per_track_metrics)):
            for c, col in enumerate(cols):
                val = per_track_metrics.iloc[r][col]
                table.setItem(r, c, QTableWidgetItem('' if val is None else str(val)))
            try:
                row_for_id[int(per_track_metrics.iloc[r]['track_id'])] = r
            except Exception:
                pass
        table.resizeColumnsToContents()
        v.addWidget(table)

        # Register for dispatcher-driven highlighting.
        self._track_table_registry = {'table': table, 'row_for_id': row_for_id,
                                      'id_col': id_col}

        # Row-click → select that track everywhere. blockSignals during
        # dispatcher-driven selectRow (in _highlight_track_in_table) prevents the
        # loop; here we also guard via the dispatcher's busy state.
        def _on_row(*_):
            if self._selection().is_busy:
                return
            items = table.selectedItems()
            if not items:
                return
            r = items[0].row()
            try:
                tid = int(table.item(r, id_col).text())
            except Exception:
                return
            self._select_track(tid, source='table')
        table.itemSelectionChanged.connect(_on_row)

        # Clean up the registry when the table closes so the dispatcher stops
        # trying to drive a dead widget.
        def _closeEvent(ev):
            try:
                if getattr(self, '_track_table_registry', None) \
                        and self._track_table_registry.get('table') is table:
                    self._track_table_registry = None
            except Exception:
                pass
            ev.accept()
        dlg.closeEvent = _closeEvent

        dlg.setModal(False)
        dlg.show()
        self._per_track_dialog = dlg  # keep a ref so it isn't GC'd

    def _add_pickable_bead_points(self, tracks, img_layer, mpp):
        """Add/refresh a Points layer with one point per bead per frame, each
        carrying its track_id, so clicking a bead selects that track everywhere
        (image->plot/table brushing). Matches the image layer's scale so points
        overlay the beads. The click handler resolves the nearest point to its
        track_id and drives the linked-selection dispatcher."""
        import numpy as _np
        if tracks is None or 'track_id' not in tracks or tracks.empty:
            return
        tr = tracks[tracks['track_id'] >= 0]
        if tr.empty:
            return
        ycol = 'y_um_raw' if 'y_um_raw' in tr else 'y_um'
        xcol = 'x_um_raw' if 'x_um_raw' in tr else 'x_um'
        frames = tr['frame'].values.astype(float)
        ys = (tr[ycol].values / mpp).astype(float)
        xs = (tr[xcol].values / mpp).astype(float)
        tids = tr['track_id'].values.astype(int)
        pts = _np.column_stack([frames, ys, xs])   # (T, Y, X)

        name = "Bead Picker"
        if name in self.viewer.layers:
            try:
                self.viewer.layers.remove(name)
            except Exception:
                pass

        add_kwargs = {'name': name, 'size': 8, 'opacity': 0.35,
                      'face_color': 'transparent', 'border_color': 'yellow',
                      'properties': {'track_id': tids}}
        # Match the image layer's spatial (y, x) scale, like the Tracks layer.
        if img_layer is not None:
            try:
                isc = _np.asarray(img_layer.scale, float)
                if isc.size >= 2:
                    yx = isc[-2:]
                    add_kwargs['scale'] = [1.0, float(yx[0]), float(yx[1])]
            except Exception:
                pass
        try:
            layer = self.viewer.add_points(pts, **add_kwargs)
        except Exception:
            # Older napari uses edge_color rather than border_color.
            add_kwargs.pop('border_color', None)
            add_kwargs['edge_color'] = 'yellow'
            layer = self.viewer.add_points(pts, **add_kwargs)

        self._bead_picker_tids = tids

        # Click -> select that track everywhere. Uses the layer's own
        # mouse_drag_callbacks so it only fires when the picker layer is active;
        # get_value returns the index of the point under the cursor.
        def _on_click(layer, event):
            if self._selection().is_busy:
                return
            try:
                idx = layer.get_value(
                    event.position, view_direction=event.view_direction,
                    dims_displayed=event.dims_displayed)
            except Exception:
                try:
                    idx = layer.get_value(event.position)
                except Exception:
                    idx = None
            if idx is None:
                return
            try:
                tid = int(self._bead_picker_tids[int(idx)])
            except Exception:
                return
            self._select_track(tid, source='image')

        try:
            layer.mouse_drag_callbacks.append(_on_click)
        except Exception:
            pass
        self._bead_picker_layer = layer

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
    def _add_host_segmentation(self, layout):
        grp  = QGroupBox("Step 2 — Segment Host Condensate")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        note = QLabel(
            "<span style='color:#aaa;font-size:9pt;'>"
            "Select the channel showing the condensate (host) phase. The mask "
            "is eroded inward so beads near the interface — where fusion and "
            "surface flow corrupt bulk diffusion — are excluded.</span>")
        note.setWordWrap(True); form.addRow(note)

        # Host mode selector. Not all data has a companion host channel:
        #   • Host channel   — segment a separate condensate channel (default).
        #   • No host        — no condensate boundary at all (e.g. beads-in-
        #                      glycerol viscosity controls); track every bead
        #                      across the full frame.
        #   • Infer from beads — (experimental / not yet enabled) synthesize a
        #                      host region from the bead distribution when the
        #                      condensate is real but unlabelled.
        self._rb_mode_host   = QRadioButton("Host channel")
        self._rb_mode_nohost = QRadioButton("No host (full frame)")
        self._rb_mode_infer  = QRadioButton("Infer from beads")
        self._rb_mode_host.setChecked(True)
        self._rb_mode_infer.setToolTip(
            "Infer an unlabelled host boundary from the bead distribution "
            "(density + watershed + a physical size gate). Detect beads first "
            "(Step 3), then run 'Infer Host from Beads' here. Only condensates "
            "large enough for boundary-free bulk diffusion are kept.")
        self._rb_mode_nohost.setToolTip(
            "No condensate boundary — track all beads across the whole field. "
            "Use for bulk-medium controls (e.g. beads diffusing in glycerol).")
        mode_row = QHBoxLayout()
        for rb in (self._rb_mode_host, self._rb_mode_nohost, self._rb_mode_infer):
            mode_row.addWidget(rb)
        mode_row.addStretch()
        mode_w = QWidget(); mode_w.setLayout(mode_row)
        form.addRow("Host mode:", mode_w)
        for rb in (self._rb_mode_host, self._rb_mode_nohost, self._rb_mode_infer):
            rb.toggled.connect(self._on_host_mode_changed)

        # Physics gate for inferred hosts: minimum condensate radius for a bead
        # to sample bulk diffusion without feeling the interface. Only used in
        # 'Infer from beads' mode.
        from qtpy.QtWidgets import QDoubleSpinBox
        self._min_cond_radius = QDoubleSpinBox()
        self._min_cond_radius.setRange(0.5, 100.0)
        self._min_cond_radius.setValue(5.0)
        self._min_cond_radius.setSingleStep(0.5)
        self._min_cond_radius.setSuffix(" µm")
        self._min_cond_radius.setToolTip(
            "Minimum condensate radius (µm) to keep. Beads in condensates "
            "smaller than this feel boundary/interface effects and don't report "
            "bulk viscosity, so small condensates are discarded. Edge-clipped "
            "condensates are judged by their projected (circle-fit) radius.")
        self._min_cond_radius_row = self._min_cond_radius  # keep a handle
        form.addRow("Min condensate radius:", self._min_cond_radius)

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
        self._seg_btn = QPushButton("▶  Segment Host & Erode")
        self._seg_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._seg_btn.clicked.connect(self._on_segment_host)
        form.addRow(self._host_prog); from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(self._seg_btn))
        layout.addWidget(grp)
        self._on_host_mode_changed()  # set initial enabled/label state

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
    def _add_bead_detection(self, layout):
        grp  = QGroupBox("Step 3 — Detect Beads")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

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

        from qtpy.QtWidgets import QComboBox
        self._quality_mode = QComboBox()
        self._quality_mode.addItem("Fast (template match) — recommended", "fast")
        self._quality_mode.addItem("Fast fit (bounded Gaussian, quick)", "fast_fit")
        self._quality_mode.addItem("Precise fit (full Gaussian, slow)", "precise")
        self._quality_mode.setCurrentIndex(0)
        self._quality_mode.setToolTip(
            "How bead quality/classification is measured:\n"
            "• Fast — empirical-PSF template + cross-correlation. Seconds/minutes "
            "for a long movie. Best default for throughput.\n"
            "• Fast fit — a real Gaussian fit with a tight iteration cap.\n"
            "• Precise fit — full Gaussian fit; highest precision, slowest "
            "(can take many minutes on a long movie).")
        form.addRow("Detection mode:", self._quality_mode)
        self._quality_mode.currentIndexChanged.connect(self._on_quality_mode_changed)

        self._subpixel = QCheckBox("Sub-pixel centres")
        self._subpixel.setChecked(True)
        self._subpixel.setToolTip(
            "Refine each bead centre to sub-pixel precision with a cheap "
            "intensity centroid (fast mode). Off = integer blob centres.")
        form.addRow(self._subpixel)

        self._template_per_frame = QCheckBox("Rebuild PSF template per frame (drift/SMLM)")
        self._template_per_frame.setChecked(False)
        self._template_per_frame.setToolTip(
            "Fast mode builds one empirical PSF template per stack by default "
            "(fastest; correct when the PSF is stable). Enable to rebuild the "
            "template every frame — adapts to focus drift, useful for SMLM-like "
            "data, slightly slower.")
        form.addRow(self._template_per_frame)

        # Physical bead size (nm). Sets the template patch size (converted to px
        # via the pixel size) and the ring de-duplication merge radius.
        self._bead_size_nm = QDoubleSpinBox()
        self._bead_size_nm.setRange(0.0, 100000.0); self._bead_size_nm.setValue(200.0)
        self._bead_size_nm.setSingleStep(50.0); self._bead_size_nm.setDecimals(0)
        self._bead_size_nm.setSuffix(" nm")
        self._bead_size_nm.setToolTip(
            "Physical bead diameter in nanometres. Converted to pixels using the "
            "loaded pixel size to size the detection template and the ring-merge "
            "radius. 0 = do not use a physical size (fall back to sigma-based).")
        form.addRow("Bead size:", self._bead_size_nm)

        self._template_type = QComboBox()
        self._template_type.addItem("Empirical PSF (from data) — recommended", "empirical")
        self._template_type.addItem("Airy model (Bessel J₁, ringed beads)", "airy")
        self._template_type.setCurrentIndex(0)
        self._template_type.setToolTip(
            "Template used for fast-mode matching:\n"
            "• Empirical — measured from the cleanest beads in your data "
            "(best default; adapts to your actual PSF).\n"
            "• Airy model — an analytic diffraction pattern (central disk + "
            "ring). Use when large beads show a visible Airy ring, so one bead "
            "matches as a single object instead of the ring being detected "
            "separately.")
        form.addRow("Template:", self._template_type)

        self._dedup_rings = QCheckBox("Merge duplicate ring/multi-scale detections")
        self._dedup_rings.setChecked(True)
        self._dedup_rings.setToolTip(
            "A single large bead can trigger several detections (at multiple "
            "scales, or on its Airy ring). Merge detections that fall within "
            "about one bead radius, keeping the brightest (the bead centre). "
            "Uses the bead size above to set the merge radius.")
        form.addRow(self._dedup_rings)

        # ── Advanced: classification strictness (viscosity-dependent) ────────
        # Dim detections are routed to the out-of-plane (yellow) bin. How
        # aggressively depends on the sample: in a viscous sample (~3 Pa·s and
        # above, the default) beads move slowly and a dim spot is almost always
        # a bead drifting out of focus, so a firm dim gate is correct. In a
        # low-viscosity sample (approaching water) beads cross the focal plane
        # quickly and the same gate would wrongly bin real beads — so this is an
        # exposed control, hidden by default to keep the common case simple.
        self._strictness_row = QWidget()
        _sr = QHBoxLayout(self._strictness_row)
        _sr.setContentsMargins(0, 0, 0, 0)
        self._strictness = QDoubleSpinBox()
        self._strictness.setRange(0.2, 3.0); self._strictness.setValue(1.0)
        self._strictness.setSingleStep(0.1); self._strictness.setDecimals(1)
        self._strictness.setToolTip(
            "Classification strictness for the dim / out-of-plane (yellow) bin.\n"
            "1.0 (default) is tuned for viscous samples (~3 Pa·s and above),\n"
            "where beads move slowly and dim spots are usually out of focus.\n"
            "Lower it (toward 0.2) for less viscous / faster samples so fewer\n"
            "real beads are pushed to yellow; raise it for an even stricter\n"
            "dim gate. Stable dim tracks are promoted back to singlet after\n"
            "linking regardless of this value.")
        _sr.addWidget(QLabel("Strictness (viscosity):"))
        _sr.addWidget(self._strictness)
        self._strictness_row.setVisible(False)   # hidden until 'Advanced' toggled

        self._show_advanced = QCheckBox("Show advanced detection options")
        self._show_advanced.setChecked(False)
        self._show_advanced.toggled.connect(self._strictness_row.setVisible)
        form.addRow(self._show_advanced)
        form.addRow(self._strictness_row)


        # ── Which bead population drives microrheology ───────────────────────
        # The three classes are never silently mixed. Green (singlets) is the
        # correct default for Stokes-Einstein viscosity. Yellow (out-of-plane)
        # can be measured on its own to check whether it agrees, then optionally
        # combined with green. Red (aggregates) is always a separate readout —
        # its size would bias viscosity — never in the viscosity population.
        self._pop_choice = QComboBox()
        self._pop_choice.addItem("Green (singlets) — recommended", "singlet")
        self._pop_choice.addItem("Yellow (out-of-plane) only", "out_of_plane")
        self._pop_choice.addItem("Green + Yellow (combine)", "singlet+out_of_plane")
        self._pop_choice.setCurrentIndex(0)
        self._pop_choice.setToolTip(
            "Which detected population to link and measure for viscosity.\n"
            "• Green (singlets): clean, in-focus single beads — the correct\n"
            "  default; Stokes-Einstein assumes a known single-bead size.\n"
            "• Yellow (out-of-plane) only: measure the dim/defocused population\n"
            "  on its own, to check whether it gives a consistent viscosity\n"
            "  before trusting or combining it.\n"
            "• Green + Yellow: combine both, once you've confirmed yellow agrees.\n"
            "Aggregates (red) are always tracked separately and never included\n"
            "in the viscosity population.")
        form.addRow("Microrheology population:", self._pop_choice)

        self._bead_prog = QProgressBar(); self._bead_prog.setVisible(False)
        btn = QPushButton("▶  Detect Beads")
        btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn.clicked.connect(self._on_detect_beads)
        form.addRow(self._bead_prog); from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(btn))
        layout.addWidget(grp)

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
    def _add_tracking(self, layout):
        grp  = QGroupBox("Step 4 — Link Trajectories")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        method_grp = QGroupBox("Linker")
        ml = QVBoxLayout(method_grp)
        ml.setContentsMargins(4, 20, 4, 4); ml.setSpacing(3)
        self._rb_trackmate = QRadioButton("TrackMate LAP  (recommended)")
        self._rb_trackmate.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._rb_bayesian  = QRadioButton("Bayesian / Hungarian")
        self._rb_greedy    = QRadioButton("Greedy nearest-neighbour")
        self._rb_trackmate.setChecked(True)
        self._rb_trackmate.setToolTip(
            "Real TrackMate LAP tracker via embedded Fiji. Requires "
            "pip install pycat-napari[trackmate] + a JDK. Falls back to "
            "Bayesian if unavailable.\n\n"
            "VALIDATED: TrackMate-through-PyCAT recovers viscosity within ~10% "
            "of the reference workflow. This is the recommended linker for "
            "quantitative viscosity/microrheology.")
        self._rb_bayesian.setToolTip(
            "PyCAT's native Bayesian/Hungarian linker with gap closing.\n\n"
            "Since 1.5.335 (gap off-by-one fix + auto-estimated linking distance) "
            "this produces clean full-length tracks and reproduces the reference "
            "viscosity on validated data when the bead motion is small relative to "
            "the inter-bead spacing. It does frame-to-frame assignment, so its "
            "reliability depends on that ratio \u2014 see the linking-conditions tag "
            "below, which is computed from your data. For fast/dense beads (high "
            "ratio), prefer TrackMate LAP (global optimisation).")
        self._rb_greedy.setToolTip(
            "Fast greedy nearest-neighbour linker.\n\n"
            "Since 1.5.335 (gap off-by-one fix + auto-estimated linking distance) "
            "it produces clean tracks when bead motion is small relative to "
            "inter-bead spacing. It commits to the nearest match each frame with no "
            "global optimisation, so it is the most sensitive of the three to "
            "identity ambiguity when beads move far or sit close together \u2014 see "
            "the linking-conditions tag below (computed from your data). For "
            "fast/dense beads, prefer TrackMate LAP.")
        for rb in (self._rb_trackmate, self._rb_bayesian, self._rb_greedy):
            ml.addWidget(rb)

        # Live linking-conditions tag: the frame-to-frame linkers (greedy,
        # Bayesian) are reliable only when a bead's per-frame displacement is
        # small relative to the nearest-neighbour spacing. The ratio
        # R = motion / NN_spacing is the governing quantity (displacement alone
        # is not — a fast bead is trivially linkable if neighbours are far). This
        # label is filled after Detect Beads from the measured motion and
        # spacing, so the warning is specific to the user's movie, not generic.
        self._link_cond_lbl = QLabel("")
        self._link_cond_lbl.setWordWrap(True)
        self._link_cond_lbl.setStyleSheet("QLabel { font-size: 11px; }")
        self._link_cond_lbl.setToolTip(
            "Ratio R = per-frame bead displacement / nearest-neighbour spacing, "
            "measured from your detections (no tracking needed). Frame-to-frame "
            "nearest-neighbour linking (greedy, Bayesian) is reliable when a bead's "
            "step is small versus the distance to its neighbours:\n"
            "  R < 0.10  SAFE   — NN linking reliable\n"
            "  0.10–0.25 CAUTION — mostly fine, occasional identity swaps\n"
            "  0.25–0.50 RISKY  — identity ambiguous; prefer TrackMate LAP (global)\n"
            "  R > 0.50  UNSAFE — frame-to-frame linking unreliable; use TrackMate "
            "LAP or acquire at a faster frame rate to shrink the displacement.\n\n"
            "This does not block any linker — it reports the conditions so you can "
            "choose. TrackMate's global optimisation tolerates higher R than the "
            "greedy/Bayesian frame-to-frame linkers.")
        ml.addWidget(self._link_cond_lbl)
        form.addRow(method_grp)

        self._max_link = QDoubleSpinBox()
        self._max_link.setRange(0.01, 50)
        # Default max linking distance ≈ 2× the bead diameter (a bead should not
        # move more than about its own size between frames in a viscous medium;
        # linking farther invites mis-links). Derived from the Step-3 bead size
        # when available, else a small sensible fallback.
        try:
            _bead_um = (self._bead_size_nm.value() / 1000.0
                        if hasattr(self, '_bead_size_nm') else 0.2)
        except Exception:
            _bead_um = 0.2
        self._max_link.setValue(max(0.05, round(2.0 * _bead_um, 3)))
        self._max_link.setSingleStep(0.05); self._max_link.setDecimals(3)
        self._max_link.setToolTip(
            "Maximum bead displacement between frames (µm). Auto-filled after "
            "Detect Beads from the measured per-frame bead MOTION (a short-window "
            "time-projection width vs the single-frame PSF width gives the "
            "displacement scale), times the margin factor k below. A too-small "
            "value clips the beads' own jitter and shatters stable beads into "
            "short tracks; this estimate is set from the data. Editable — override "
            "if needed.")
        form.addRow("Max linking dist (µm):", self._max_link)

        # Advanced: margin factor k on the auto-estimated linking distance.
        self._link_k = QDoubleSpinBox()
        self._link_k.setRange(1.0, 6.0); self._link_k.setValue(2.5)
        self._link_k.setSingleStep(0.5); self._link_k.setDecimals(1)
        self._link_k.setToolTip(
            "Margin factor applied to the measured per-frame bead motion when "
            "auto-setting the max linking distance (distance ≈ k × motion σ). "
            "Higher k = more tolerant linking (bridges bigger jumps, risks "
            "mis-links in dense fields); lower k = stricter. 2.5 is a good "
            "default. Takes effect on the next Detect Beads run.")
        self._link_k_row = QWidget()
        _lk = QFormLayout(self._link_k_row)
        _lk.setContentsMargins(0, 0, 0, 0); _lk.setSpacing(5)
        _lk.addRow("Linking-distance margin k:", self._link_k)
        self._link_k_row.setVisible(False)
        self._show_link_adv = QCheckBox("Show advanced linking options")
        self._show_link_adv.setChecked(False)
        self._show_link_adv.toggled.connect(self._link_k_row.setVisible)
        form.addRow(self._show_link_adv)
        form.addRow(self._link_k_row)

        # ── The default was 0, and the reasoning was backwards ──────────────────
        #
        # The old tooltip said bridging a gap is DANGEROUS — that a bead which vanishes and
        # reappears is "more likely a broken trajectory that should be pruned". **Ground truth
        # says the opposite** (tests/test_linkers.py, 1.5.477: the first test the linkers have
        # ever had).
        #
        # With objects whose true identity is known, a detector that misses 10 % of frames turns
        # **20 objects into 92 tracks at gap=0**, with only **49 %** of detections keeping their
        # identity:
        #
        #     dropout   gap   purity     tracks (true = 20)
        #     10 %      0     **49 %**   **92**
        #     10 %      1     87 %       32
        #     10 %      **3** **99 %**   **21**
        #     20 %      0     **29 %**   **147**
        #     20 %      **3** **99 %**   **21**
        #
        # And it is SAFE: **zero mixed tracks at any gap** on separated objects. Bridging repairs
        # a break; it does not invent a link. (A mixed track WOULD be dangerous — it injects a
        # spurious jump into the MSD and deflates the viscosity — which is presumably the fear
        # the old default was built on. The fear is real; the setting was aimed at the wrong
        # end.)
        #
        # **On Gable's real bead data it moves the answer.** Against the 8.325 Pa·s reference:
        # gap=0 gives 10.14, gap=1 gives 7.97, and **gap=2-3 gives 8.54-8.57 (2.6 %)** with
        # alpha = 0.97 — closer to the Brownian 1.0 than gap=1's 0.93.
        #
        # Default 2: enough to bridge the ~15 % dropout measured on real bead data, without
        # reaching so far that a genuinely lost bead is stitched to a new one.
        self._max_gap = QSpinBox()
        self._max_gap.setRange(0, 20); self._max_gap.setValue(2)
        self._max_gap.setToolTip(
            "Max frames a bead can vanish and still be reconnected.\n\n"
            "**Default 2.** Detection drops beads — around 15% of frames on real data — and a "
            "linker that cannot bridge that SHATTERS the tracks. Measured against known "
            "identities: a 10% dropout turns 20 objects into 92 tracks at gap=0, with only 49% "
            "of detections keeping their identity. At gap=3 it is 21 tracks and 99%.\n\n"
            "Bridging is safe on separated objects: zero mixed tracks at any gap. It repairs a "
            "break rather than inventing a link.\n\n"
            "On the reference bead data (true viscosity 8.325 Pa\u00b7s): gap=0 gives 10.1, "
            "gap=1 gives 8.0, gap=2-3 gives 8.5. Raise it if your detection is poor; lower it "
            "only if beads are close enough to be confused with each other.")
        form.addRow("Max frame gap:", self._max_gap)

        self._track_prog = QProgressBar(); self._track_prog.setVisible(False)
        btn = QPushButton("▶  Link Trajectories")
        btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn.clicked.connect(self._on_link)
        form.addRow(self._track_prog); from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(btn))

        layout.addWidget(grp)

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

    def _rebuild_track_layers(self, tracks, name="Bead Trajectories"):
        """(Re)build the napari Tracks layer + the pickable Points layer from a
        tracks DataFrame. Shared by the linker and by session load, so a loaded
        session gets exactly the same brushable layers a fresh link produces.

        tracks needs at least: track_id, frame, and y_um/x_um (or y_um_raw/
        x_um_raw). Scale is matched to the bead image layer so the tracks overlay
        the image 1:1."""
        import numpy as _np
        if tracks is None or 'track_id' not in tracks or tracks.empty:
            return
        mpp = self._mpx()
        _bead_name = self._bead_dd.currentText() if hasattr(self, '_bead_dd') else ''
        _img_layer = None
        try:
            import napari.layers as _nl
            for _l in self.viewer.layers:
                if isinstance(_l, _nl.Image):
                    if _bead_name and _l.name == _bead_name:
                        _img_layer = _l; break
                    if _img_layer is None:
                        _img_layer = _l
        except Exception:
            _img_layer = None

        tl = tracks[['track_id', 'frame']].copy()
        tl['y'] = tracks['y_um_raw'] / mpp if 'y_um_raw' in tracks else tracks['y_um'] / mpp
        tl['x'] = tracks['x_um_raw'] / mpp if 'x_um_raw' in tracks else tracks['x_um'] / mpp
        if name in self.viewer.layers:
            self.viewer.layers.remove(name)
        add_kwargs = {}
        if _img_layer is not None:
            try:
                isc = _np.asarray(_img_layer.scale, float)
                if isc.size >= 2:
                    yx = isc[-2:]
                    add_kwargs['scale'] = [1.0, float(yx[0]), float(yx[1])]
            except Exception:
                pass
        self.viewer.add_tracks(
            tl[['track_id', 'frame', 'y', 'x']].values, name=name, **add_kwargs)
        try:
            self._add_pickable_bead_points(tracks, _img_layer, mpp)
        except Exception as _e:
            print(f"[PyCAT VPT] pickable bead layer failed: {_e}")

    def _update_tracklen_hist(self, tracks):
        """Draw the track-length (frames-per-track) histogram in a popped-out
        dock widget. A healthy link has many long tracks; a fragmentation-prone
        linker piles up very short ones. Called after each link."""
        try:
            if tracks is None or 'track_id' not in tracks or tracks.empty:
                return
            import numpy as _np
            lengths = tracks.groupby('track_id').size().values
            n_frames = int(tracks['frame'].nunique()) if 'frame' in tracks else 0
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
            fig = Figure(figsize=(4.2, 2.6), tight_layout=True)
            ax = fig.add_subplot(111)
            Lmax = int(lengths.max()) if len(lengths) else 1
            nbins = int(_np.clip(Lmax, 5, 40))
            ax.hist(lengths, bins=nbins, color='#4c72b0',
                    edgecolor='#2b4a72', linewidth=0.4)
            med = float(_np.median(lengths)) if len(lengths) else 0.0
            ax.axvline(med, color='#c44e52', ls='--', lw=1,
                       label=f"median {med:.0f}")
            if n_frames > 0:
                frac_long = float(_np.mean(lengths >= 0.5 * n_frames))
                ax.set_title(
                    f"{len(lengths)} tracks · {frac_long*100:.0f}% span ≥½ movie",
                    fontsize=9)
            else:
                ax.set_title(f"{len(lengths)} tracks", fontsize=9)
            ax.set_xlabel("track length (frames)", fontsize=9)
            ax.set_ylabel("count", fontsize=9)
            ax.tick_params(labelsize=8)
            ax.legend(fontsize=8, frameon=False)
            canvas = FigureCanvasQTAgg(fig)
            # Reuse a single dock so repeated links replace it instead of stacking.
            try:
                if getattr(self, '_tracklen_dock', None) is not None:
                    self.viewer.window.remove_dock_widget(self._tracklen_dock)
            except Exception:
                pass
            self._tracklen_dock = self.viewer.window.add_dock_widget(
                canvas, name="Track lengths", area='right')
        except Exception as _e:
            print(f"[PyCAT VPT] track-length histogram skipped: {_e}")

    def _follow_enabled(self):
        """Should a plot click take the user to the bead? **No, by default.**

        VPT reads the SAME session preference the generic brushing path reads
        (`pycat.utils.brushing._follow_enabled` → `central_manager.follow_selection`)
        rather than owning a second flag. Two flags meaning "follow the selection"
        is how the next person gets one of them wrong.
        """
        from pycat.utils.brushing import _follow_enabled as _follow
        return _follow(getattr(self, 'central_manager', None))

    def _world_scale_yx(self, mpp):
        """The (y, x) scale the image layer is ACTUALLY drawn at.

        Read once and used for both the highlight and the camera, so they stay
        consistent even when the layer scale differs from `_mpx()` — e.g. the
        pixel-size gate never fired and the image sits at scale 1.0. Placing the
        overlay in the same world frame the image uses is what guarantees it lands
        on the bead. This is the exact µm-vs-px confusion that bit before: pin it
        to one source of truth, the image layer's own scale.
        """
        import numpy as _np
        for layer in self.viewer.layers:
            if layer.__class__.__name__ == 'Image':
                scale = _np.asarray(layer.scale, float)
                if scale.size >= 2:
                    return float(scale[-2]), float(scale[-1])
                break
        return mpp, mpp

    def _navigate_to_bead(self, f0, y0, x0, sc_y, sc_x):
        """Take the user to the bead — **only if they asked to be taken there.**

        The frame step and the camera answer the same question ("take me there"),
        so one preference gates both. With follow OFF (the default) this is a
        no-op and the pick merely marks the track in place: the view stays where
        the user put it, and — because there is no camera move — there is no
        `draw_event` to loop back through the pick.
        """
        if not self._follow_enabled():
            return
        # Step to the track's first frame (T axis is axis 0 for a movie). A bead on
        # frame 40 is not visible from frame 0, so going there means the frame too.
        try:
            if self.viewer.dims.ndim > 2:
                step = list(self.viewer.dims.current_step)
                step[0] = f0
                self.viewer.dims.current_step = tuple(step)
        except Exception:
            pass
        # Centre the camera on the bead, in the image's world frame.
        try:
            self.viewer.camera.center = (y0 * sc_y, x0 * sc_x)
        except Exception:
            pass

    def _draw_picked_track(self, path, mpp, sc_y, sc_x):
        """Trace the picked track as a connected LINE (a Shapes 'path').

        Not a column of filled circles: those sit on top of and obscure the track
        already plotted in the Bead Trajectories layer. A small ring marks where it
        starts. One Shapes + one Points layer, reused — never accumulated.
        """
        hl_line = "Picked track"
        hl_start = "Picked track start"
        try:
            for name in (hl_line, hl_start):
                if name in self.viewer.layers:
                    self.viewer.layers.remove(name)
            if path.shape[0] >= 2:
                # Bright, thin and slightly transparent, ON TOP of the trajectory —
                # a highlight that does not hide what it highlights. edge_width is
                # in data (pixel) units.
                kw = dict(name=hl_line, shape_type='path',
                          edge_color='#ff8c00', face_color='transparent',
                          edge_width=max(1.0, 0.25 / mpp),
                          opacity=0.85, scale=[sc_y, sc_x])
                try:
                    self.viewer.add_shapes([path], **kw)
                except Exception:
                    # Older napari: edge_width may need to be a scalar list.
                    kw['edge_width'] = float(max(1.0, 0.25 / mpp))
                    self.viewer.add_shapes([path], **kw)
            kw = dict(name=hl_start, size=max(6, 0.5 / mpp),
                      face_color='transparent', border_color='#ff8c00',
                      opacity=0.95, scale=[sc_y, sc_x])
            try:
                self.viewer.add_points(path[:1], **kw)
            except Exception:
                kw.pop('border_color', None); kw['edge_color'] = '#ff8c00'
                self.viewer.add_points(path[:1], **kw)
        except Exception:
            pass

    def _announce_picked_track(self, track_id, g, f0):
        """One line about the picked track — what it is, without going to it."""
        import numpy as _np
        try:
            n = int(len(g))
            step_nm = float(_np.median(_np.sqrt(
                _np.sum(_np.diff(g[['y_um', 'x_um']].values, axis=0) ** 2,
                        axis=1)))) * 1000 if n > 1 else 0.0
            napari_show_info(
                f"Track {int(track_id)}: {n} frames, starts frame {f0}, "
                f"median step {step_nm:.0f} nm — highlighted in viewer.")
        except Exception:
            pass

    def _reveal_track_in_viewer(self, track_id):
        """Plot -> data brushing: clicking a track in the MSD plot marks that exact
        bead in the napari viewer — traces its trajectory, and surfaces its row.

        napari's Tracks layer has no per-track selection API, so instead of
        recolouring one track inside the layer we add a short-lived Shapes path
        highlight at the bead's position, which is the clear visual cue.

        ── Marking it and going to it are two different acts ──────────────────

        This used to step the frame and move the camera on EVERY pick. Two
        problems, and only one of them was cosmetic:

        1. It was abrupt navigation — you click a track to ask what it is, and the
           view you were reading leaves. The generic brushing path settled this
           already (`follow_selection`, OFF by default); VPT's plot has its own
           pick route and never got the memo, so it kept yanking the view.
        2. It LOOPED. Moving the camera/frame fires napari's `draw_event`, which
           re-runs the MSD plot's blit-capture, which can re-enter the pick and
           reveal again — a continuous jump until force-close.

        So: the navigation is gated on the preference (1), and the whole reveal is
        re-entrant-guarded (2). The guard is not redundant with the preference —
        `follow_selection = True` is a supported state, and it must not loop for
        the users who turn it on.
        """
        # ── Re-entrancy: a camera move must not become another reveal ─────────
        #
        # Set before the work, released on the next event-loop tick AFTER it — the
        # same delayed pattern `SelectionService` uses, and for the same reason.
        # The release has to outlive both this call and the `draw_event` our own
        # camera move fires, which is why it is deferred rather than cleared here.
        if getattr(self, '_revealing', False):
            return
        self._revealing = True
        try:
            import numpy as _np
            tracks = self._dr().get('vpt_tracks')
            if tracks is None or 'track_id' not in tracks:
                return
            g = tracks[tracks['track_id'] == track_id]
            if g.empty:
                return
            g = g.sort_values('frame')
            mpp = self._mpx()
            # Track positions in PIXEL (layer-native) coordinates. tracks store
            # y_um/x_um, so divide by the pixel size to get pixel indices.
            ys = (g['y_um'].values / mpp)
            xs = (g['x_um'].values / mpp)
            f0 = int(g['frame'].iloc[0])
            y0 = float(ys[0]); x0 = float(xs[0])

            sc_y, sc_x = self._world_scale_yx(mpp)

            # Select the Tracks layer if present (so the picked track is in focus).
            if "Bead Trajectories" in self.viewer.layers:
                try:
                    self.viewer.layers.selection = {
                        self.viewer.layers["Bead Trajectories"]}
                except Exception:
                    pass

            self._navigate_to_bead(f0, y0, x0, sc_y, sc_x)
            self._draw_picked_track(_np.column_stack([ys, xs]), mpp, sc_y, sc_x)
            self._announce_picked_track(track_id, g, f0)
        except Exception as _e:
            print(f"[PyCAT VPT] reveal track failed: {_e}")
        finally:
            # In a `finally` so a broken reveal cannot wedge the plot: a guard that
            # never releases is not a fix, it is a dead plot — every later pick
            # would be silently swallowed.
            from pycat.utils.selection_service import _qt_defer
            _qt_defer(lambda: setattr(self, '_revealing', False))

    # ── Step 5: microrheology ──────────────────────────────────────────
    def _add_microrheology(self, layout):
        grp  = QGroupBox("Step 5 — Microrheology (MSD → Viscosity)")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        self._frame_dt = QDoubleSpinBox()
        self._frame_dt.setRange(0.0001, 3600); self._frame_dt.setValue(0.1)
        self._frame_dt.setDecimals(4); self._frame_dt.setSingleStep(0.01)
        self._frame_dt.setToolTip(
            "Time between frames (seconds). Auto-filled from the file's metadata "
            "when available (OME TimeIncrement / per-plane DeltaT / MicroManager "
            "Interval_ms); edit to override.")
        self._frame_dt_touched = False
        self._frame_dt.valueChanged.connect(
            lambda _v: setattr(self, '_frame_dt_touched', True))
        form.addRow("Frame interval (s):", self._frame_dt)

        self._bead_radius = QDoubleSpinBox()
        self._bead_radius.setRange(0.001, 5.0); self._bead_radius.setValue(0.1)
        self._bead_radius.setDecimals(3); self._bead_radius.setSingleStep(0.01)
        self._bead_radius.setToolTip(
            "Probe bead radius (µm). 20nm–2µm typical → 0.01–1.0 µm radius.\n\n"
            "Viscosity is INVERSELY proportional to this: η = kT/(6πRD). A radius "
            "that is 30% wrong makes the viscosity 30% wrong.")
        form.addRow("Bead radius (µm):", self._bead_radius)

        # Provenance of the radius. Recorded because η ∝ 1/R, so the radius is not a
        # nuisance parameter — it propagates linearly into the answer. A dropdown, not
        # a free-text field: it is structured (so it can be batched, queried and
        # exported) and it is one click rather than something to retype every run.
        self._radius_source = QComboBox()
        self._radius_source.addItems(["manufacturer", "calibrated", "metadata",
                                      "assumed"])
        self._radius_source.setToolTip(
            "Where this radius came from. Recorded with the result.\n\n"
            "• manufacturer — the bead specification / datasheet (the usual case).\n"
            "• calibrated — measured against a standard on this instrument.\n"
            "• metadata — read from the acquisition file.\n"
            "• assumed — a default or a guess; the result inherits that uncertainty.\n\n"
            "Note: do NOT derive the radius from the imaged blob. The blob is the bead "
            "CONVOLVED WITH THE PSF — for a 200 nm bead at ~1.2 NA the PSF is comparable "
            "to the bead itself, so the apparent size is dominated by the optics. You "
            "would be measuring the microscope, and the viscosity would come out too "
            "LOW. Comparing the apparent size to the spec as a SANITY CHECK is good "
            "practice (it catches a wrong vial or aggregates); using it as the input "
            "is not.\n\n"
            "Example of a fully-specified radius: 0.100 µm, manufacturer spec, ±5%.")
        form.addRow("Radius source:", self._radius_source)

        self._radius_note = QLineEdit()
        self._radius_note.setPlaceholderText("optional — e.g. cat# / lot, or ±5%")
        self._radius_note.setToolTip(
            "Optional free text stored with the result: a catalogue number, a lot, a "
            "tolerance. Leave blank if the source dropdown says enough.")
        form.addRow("Radius note:", self._radius_note)

        self._temp_C = QDoubleSpinBox()
        self._temp_C.setRange(-20, 100); self._temp_C.setValue(24.0)
        self._temp_C.setDecimals(1); self._temp_C.setSingleStep(0.5)
        self._temp_C.setToolTip("Temperature (°C) for the Stokes-Einstein relation.")
        form.addRow("Temperature (°C):", self._temp_C)

        self._min_track = QSpinBox()
        self._min_track.setRange(2, 1000); self._min_track.setValue(5)
        self._min_track.setToolTip("Minimum track length (frames) to include in the MSD.")
        form.addRow("Min track length:", self._min_track)

        # Drift-correction mode (#9). COM subtraction is standard for
        # microrheology but removes REAL collective motion (internal flow,
        # sedimentation, bulk translation) along with stage drift — so the choice
        # is explicit and recorded, not always-on.
        self._drift_mode = QComboBox()
        self._drift_mode.addItem("Ensemble COM (standard)", "com")
        self._drift_mode.addItem("Immobile-reference (flow-safe)", "immobile_reference")
        self._drift_mode.addItem("None (keep collective motion)", "none")
        self._drift_mode.setToolTip(
            "How to remove drift before the MSD:\n"
            "• Ensemble COM — subtract the mean displacement of all beads "
            "(classic microrheology). Removes stage drift AND any real bulk flow.\n"
            "• Immobile-reference — estimate drift from only the most stationary "
            "tracks, so genuinely flowing/diffusing beads don't bias the "
            "correction. Safer when real motion is present.\n"
            "• None — no correction; use when collective flow IS the signal "
            "(e.g. internal-flow studies).")
        form.addRow("Drift correction:", self._drift_mode)

        # Out-of-plane handling (#10). A defocused bead's axial fluctuations can
        # masquerade as in-plane motion and bias viscosity. Recovered out-of-plane
        # (yellow) beads are already excluded from viscosity unless the population
        # selector includes them — but the temporal-stability pass promotes stable
        # dim tracks back to singlet, which can fold a persistent defocused bead
        # into the viscosity set. This makes that promotion explicit.
        self._promote_stable = QCheckBox("Promote stable dim tracks to singlet")
        self._promote_stable.setChecked(True)
        self._promote_stable.setToolTip(
            "When on, a dim (out-of-plane) track that persists stably across "
            "frames is treated as a real faint bead and included in the singlet "
            "viscosity population. Turn OFF for a stricter singlet-only viscosity "
            "that never merges defocused beads — safer if axial fluctuations of "
            "out-of-focus beads might bias the measurement.")
        form.addRow(self._promote_stable)

        # ── Advanced: viscoelastic moduli (G'/G'') options ───────────────────
        # Hidden by default (mirrors the 'Show advanced detection options'
        # pattern above). The G'/G'' point estimate always uses the Evans (2009)
        # conversion; these controls add optional bootstrap confidence bands.
        self._moduli_boot = QCheckBox("Bootstrap G′/G″ confidence intervals")
        self._moduli_boot.setChecked(False)
        self._moduli_boot.setToolTip(
            "Estimate uncertainty on the storage/loss moduli by resampling whole "
            "tracks with replacement and re-computing G′/G″ for each resample; "
            "the plot then shows shaded confidence bands. This is the honest "
            "response to noisy data — it shows which parts of the spectrum are "
            "trustworthy. Bands are approximate (empirical coverage runs a little "
            "below nominal). Adds compute time proportional to the resample count.")
        self._moduli_nboot = QSpinBox()
        self._moduli_nboot.setRange(20, 2000); self._moduli_nboot.setValue(200)
        self._moduli_nboot.setSingleStep(50)
        self._moduli_nboot.setToolTip(
            "Number of bootstrap resamples for the G′/G″ confidence bands. More "
            "resamples = smoother, more stable bands but longer compute. 200 is a "
            "reasonable default; raise for a final figure.")
        self._moduli_boot_row = QWidget()
        _mb = QFormLayout(self._moduli_boot_row)
        _mb.setContentsMargins(0, 0, 0, 0); _mb.setSpacing(5)
        _mb.addRow(self._moduli_boot)
        _mb.addRow("Bootstrap resamples:", self._moduli_nboot)

        # ── Lag-window fit gate (MSD → D/α fit range) ────────────────────────
        # The reliable MSD lag window is bounded by hardware: high-frequency
        # cutoff = frame interval, low-frequency cutoff = acquisition duration.
        # Fitting outside it gives a wrong D/α. These pick the upper-lag rule and
        # whether to clip the fit to the defensible band (warn, not block).
        self._lag_rule = QComboBox()
        self._lag_rule.addItem("Fraction of track length", "fraction")
        self._lag_rule.addItem("Fixed frequency window", "fixed")
        self._lag_rule.addItem("Minimum independent pairs", "min_pairs")
        self._lag_rule.setToolTip(
            "How to set the upper-lag (low-frequency) cutoff of the MSD fit:\n"
            "• Fraction of track length — fit lags up to a fraction of the record "
            "length; longer lags have too few independent samples. Standard, "
            "conservative.\n"
            "• Fixed frequency window — restrict to a hardware-defensible band "
            "(set the upper lag in seconds). Matches routine lab practice.\n"
            "• Minimum independent pairs — keep a lag only while enough independent "
            "trajectories span it. Adapts to how many/how long your tracks are.")
        self._lag_fraction = QDoubleSpinBox()
        self._lag_fraction.setRange(0.02, 1.0); self._lag_fraction.setValue(0.25)
        self._lag_fraction.setDecimals(2); self._lag_fraction.setSingleStep(0.05)
        self._lag_fraction.setToolTip(
            "Upper lag = this fraction × longest track duration (Fraction rule).")
        self._lag_fixed_s = QDoubleSpinBox()
        self._lag_fixed_s.setRange(0.001, 1e6); self._lag_fixed_s.setValue(10.0)
        self._lag_fixed_s.setDecimals(3)
        self._lag_fixed_s.setToolTip(
            "Upper lag in seconds (Fixed-window rule). E.g. 10 s for a "
            "0.1–10 s defensible band at 0.1 s/frame.")
        self._lag_minpairs = QSpinBox()
        self._lag_minpairs.setRange(2, 1000); self._lag_minpairs.setValue(10)
        self._lag_minpairs.setToolTip(
            "Keep lags spanned by at least this many independent tracks "
            "(Minimum-independent-pairs rule).")
        self._lag_confine = QCheckBox("Confine fit to scientifically defensible bounds")
        self._lag_confine.setChecked(True)
        self._lag_confine.setToolTip(
            "When ON (default), the MSD fit is clipped to the hardware-defensible "
            "lag window (frame interval → the upper-lag rule above). When OFF, the "
            "full available lag range is fit at your own risk. Either way, PyCAT "
            "WARNS (never blocks) if the acquisition can't cover the window.")
        _mb.addRow("Upper-lag rule:", self._lag_rule)
        _mb.addRow("  fraction:", self._lag_fraction)
        _mb.addRow("  fixed upper lag (s):", self._lag_fixed_s)
        _mb.addRow("  min independent pairs:", self._lag_minpairs)
        _mb.addRow(self._lag_confine)
        self._moduli_boot_row.setVisible(False)   # hidden until toggled

        self._show_moduli_adv = QCheckBox("Show advanced fit / moduli options")
        self._show_moduli_adv.setChecked(False)
        self._show_moduli_adv.toggled.connect(self._moduli_boot_row.setVisible)
        form.addRow(self._show_moduli_adv)
        form.addRow(self._moduli_boot_row)

        # Plot layout preference: one consolidated 2×2 window (default) vs separate
        # pop-out windows. There is also a live toggle button on the consolidated
        # window, so this only sets the initial layout.
        self._plots_consolidated = QCheckBox("Show all plots in one window (2×2)")
        self._plots_consolidated.setChecked(True)
        self._plots_consolidated.setToolTip(
            "ON (default): MSD, Evans G′/G″, centered trajectories, and the van "
            "Hove displacement distribution appear together in one 2×2 figure "
            "(with a button to pop them into separate windows). OFF: each plot "
            "opens in its own resizable window, and the MSD plot supports "
            "click-a-track-to-reveal-it-in-the-viewer.")
        form.addRow(self._plots_consolidated)

        # Fidelity-based render opt-out. By default the MSD spaghetti plot draws
        # the smallest representative sample that reproduces the full percentile
        # band (~95% fidelity) — fast and faithful, since extra lines just
        # overplot. This checkbox forces drawing EVERY track for anyone who wants
        # the literal full spaghetti (streams in progressively so it stays live).
        self._plots_draw_all = QCheckBox("Draw every track (slower; default shows a representative sample)")
        self._plots_draw_all.setChecked(False)
        self._plots_draw_all.setToolTip(
            "OFF (default): the MSD plot draws the smallest random sample of "
            "tracks that reproduces the full 10–90% spread to ~95% (labelled with "
            "the measured fidelity). The ensemble mean and fit always use ALL "
            "tracks regardless — this only affects how many faint lines are drawn. "
            "ON: draw every track (streamed in progressively).")
        form.addRow(self._plots_draw_all)

        self._rheo_prog = QProgressBar(); self._rheo_prog.setVisible(False)
        btn = QPushButton("▶  Compute MSD & Viscosity")
        btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn.clicked.connect(self._on_rheology)
        form.addRow(self._rheo_prog); from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(btn))
        layout.addWidget(grp)

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
            # Consolidated by default; the checkbox lets the user prefer separate
            # pop-out windows (each resizable, and the MSD one gets click-to-reveal
            # brushing via the standalone interactive plot).
            _consolidated = True
            if hasattr(self, '_plots_consolidated'):
                _consolidated = self._plots_consolidated.isChecked()
            if _consolidated:
                # Register the consolidated MSD panel's lines so table/image
                # selections can highlight a curve here too, and route its picks
                # through the dispatcher (consolidated-mode brushing).
                self._msd_line_registry = {}
                plot_vpt_panel(ptc, msd_df, fit, mod, tracks_df=tracks,
                               frame_interval_s=self._frame_dt.value(),
                               van_hove_lag=1, consolidated=True,
                               interactive=True,
                               line_registry=self._msd_line_registry,
                               on_pick_track=lambda tid: self._select_track(tid, source='plot'))
            else:
                # Separate windows: use the interactive MSD plot (with brushing)
                # + the standalone spread/moduli windows. Route picks through the
                # linked-selection dispatcher and register the line map so
                # image/table selections can highlight the curve too.
                self._msd_line_registry = {}
                _render_mode = ('all' if (hasattr(self, '_plots_draw_all')
                                          and self._plots_draw_all.isChecked())
                                else 'auto')
                plot_msd_trajectories(
                    ptc, msd_df, fit,
                    title="VPT MSD (per-track + ensemble)",
                    interactive=True,
                    on_pick_track=lambda tid: self._select_track(tid, source='plot'),
                    line_registry=self._msd_line_registry,
                    render_mode=_render_mode)
                plot_vpt_panel(ptc, msd_df, fit, mod, tracks_df=tracks,
                               frame_interval_s=self._frame_dt.value(),
                               van_hove_lag=1, consolidated=False,
                               interactive=True)
        except Exception as e:
            print(f"[PyCAT] VPT plots failed: {e}")

        # Per-track results table — one row per track, click a row to highlight
        # that track everywhere (row -> MSD curve + image bead) via the linked
        # selection dispatcher. Non-modal so it stays open alongside the plots.
        # Shown BEFORE the modal summary dialog below (which blocks until closed).
        try:
            _ptc_for_tbl = ptc if 'ptc' in dir() else None
            _ptm = self._build_per_track_metrics(tracks, _ptc_for_tbl)
            self._dr()['vpt_per_track_metrics'] = _ptm
            self._show_per_track_table(_ptm)
        except Exception as e:
            print(f"[PyCAT VPT] per-track table failed: {e}")

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
