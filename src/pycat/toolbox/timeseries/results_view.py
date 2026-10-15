"""Linked results for the cellular time series (1.7.004): every plot, table and labels layer is one view on the
application ``SelectionService``, as in the other workflows -- plus the time axis.

`TraceView` is the time-axis piece the other workflows did not need: one line per object (a cell, or a
condensate track) of any measured column against time.

* Click a line -> that object is selected everywhere (tables, scatter plots, its outline in the viewer) AND the
  viewer jumps to the clicked time point, so the image under the curve is the one you clicked.
* Move the viewer's time slider -> a vertical cursor follows on every trace plot, so you read the curves at
  the frame you are looking at.
* A selection made anywhere else highlights that object's line here.
"""
from __future__ import annotations

import numpy as np

from pycat.utils.entity_ref import ENTITY_ID_COLUMN
from pycat.utils.general_utils import debug_log
from pycat.utils.selection_service import Selection, register_view

# The per-cell columns worth plotting against time, in menu order (label, column).
CELL_TRACE_METRICS = (
    ('Condensed fraction (mask)', 'condensed_fraction'),
    ('Condensed fraction (mask-free)', 'condensed_fraction_mask_free'),
    ('Partition coefficient', 'partition'),
    ('Dilute phase (net counts)', 'dilute_net'),
    ('Condensates per cell', 'n_condensates'),
    ('Mean cell intensity (net)', 'mean_net'),
    ('Signal retained (bleaching)', 'signal_retained'),
)
TRACK_TRACE_METRICS = (
    ('Excess signal', 'excess'),
    ('Partition coefficient', 'partition'),
    ('Area (µm²)', 'area_um2'),
    ('Mean intensity (net)', 'mean_net'),
    ('Local CNR', 'local_cnr'),
)


def droplet_trace_metrics(df):
    """The in-vitro droplet columns worth plotting against time: every measured channel's net signal and
    mean, then size and shape."""
    nets = [(f"{c[4:]} net signal", c) for c in df.columns if c.startswith('net_')]
    means = [(f"{c[5:]} mean", c) for c in df.columns if c.startswith('mean_') and c != 'mean_intensity']
    return tuple(nets + means + [('Area (µm²)', 'area_um2'), ('Eccentricity', 'eccentricity')])


_HIGHLIGHT = dict(color='#ff8c00', lw=2.4, zorder=6)


class TraceView:
    """One line per object of ``metric`` against ``time_s`` (a ``SelectionView``). See the module docstring.

    ``df`` is long format (one row per object per frame) carrying ``ENTITY_ID_COLUMN``, ``frame`` and
    ``time_s``; ``group_col`` names the object; ``color_col`` (optional, one value per object) colours the lines
    -- expression for cells, so the transfection heterogeneity is visible in the plot itself.
    """

    def __init__(self, df, service, view_id, *, group_col, metrics, viewer=None, color_col=None,
                 color_label='', max_lines=400):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
        from qtpy.QtWidgets import QComboBox, QVBoxLayout, QWidget

        self.df = df
        self.service = service
        self.view_id = str(view_id)
        self.group_col = group_col
        self.viewer = viewer
        self.color_col = color_col
        self.color_label = color_label
        self.max_lines = int(max_lines)
        self._lines = {}              # eid -> Line2D
        self._points = []             # (eid, x, y, frame)
        self._highlight = []
        self._cursor = None
        self._colorbar = None
        self._dims_cb = None

        self.widget = QWidget()
        lay = QVBoxLayout(self.widget)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self._metrics = [(lbl, col) for lbl, col in metrics if col in df.columns]
        for lbl, _ in self._metrics:
            self.combo.addItem(lbl)
        lay.addWidget(self.combo)
        self.figure = Figure(figsize=(4.4, 3.0), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(260)
        lay.addWidget(self.canvas)
        self.ax = self.figure.add_subplot(111)

        self.combo.currentIndexChanged.connect(lambda _i: self.redraw())
        self._cid = self.canvas.mpl_connect('button_press_event', self._on_click)
        self.redraw()
        try:
            register_view(service, self)
        except Exception as exc:                          # broad-ok: optional_probe — a plot that can't subscribe still draws
            debug_log('results_view: could not register the trace view', exc)
        self._connect_time_cursor()

    # ── drawing ────────────────────────────────────────────────────────────────────────────────
    @property
    def metric(self):
        i = self.combo.currentIndex()
        return self._metrics[i][1] if 0 <= i < len(self._metrics) else None

    def _groups(self):
        groups = list(self.df.groupby(self.group_col))
        if len(groups) > self.max_lines:              # keep the longest-lived objects (the readable ones)
            groups = sorted(groups, key=lambda g: -len(g[1]))[:self.max_lines]
        return groups

    def redraw(self):
        from matplotlib import cm, colors
        self.ax.clear()
        self._lines, self._points, self._highlight, self._cursor = {}, [], [], None
        metric = self.metric
        if metric is None or ENTITY_ID_COLUMN not in self.df:
            self.canvas.draw_idle()
            return
        groups = self._groups()
        cvals = None
        if self.color_col and self.color_col in self.df:
            per = self.df.groupby(self.group_col)[self.color_col].first()
            norm = colors.Normalize(float(np.nanmin(per)), float(np.nanmax(per)))
            cvals = (per, norm, cm.viridis)
        for key, g in groups:
            g = g.sort_values('frame')
            eid = str(g[ENTITY_ID_COLUMN].iloc[0])
            x, y = g['time_s'].to_numpy(float), g[metric].to_numpy(float)
            color = cvals[2](cvals[1](cvals[0].loc[key])) if cvals is not None else '0.45'
            (line,) = self.ax.plot(x, y, color=color, lw=1.0, alpha=0.85 if len(groups) < 60 else 0.35)
            self._lines[eid] = line
            self._points += [(eid, xi, yi, int(f)) for xi, yi, f in zip(x, y, g['frame']) if np.isfinite(yi)]
        self.ax.set_xlabel('time (s)')
        self.ax.set_ylabel(self.combo.currentText())
        if cvals is not None and self._colorbar is None:
            sm = cm.ScalarMappable(norm=cvals[1], cmap=cvals[2])
            try:
                self._colorbar = self.figure.colorbar(sm, ax=self.ax, pad=0.01)
                self._colorbar.set_label(self.color_label or self.color_col, fontsize=8)
            except Exception as exc:                     # broad-ok: ui_cleanup — the colour bar is cosmetic
                debug_log('results_view: no colour bar', exc)
        self._draw_cursor()
        self.apply_selection(getattr(self.service, 'state', None))

    # ── outbound: click -> select the object and step the viewer to that time ──────────────────
    def _on_click(self, event):
        if event.inaxes is not self.ax or event.x is None or self.service.is_busy or not self._points:
            return
        trans = self.ax.transData
        best = None
        for eid, x, y, frame in self._points:
            px, py = trans.transform((x, y))
            d = (px - event.x) ** 2 + (py - event.y) ** 2
            if best is None or d < best[0]:
                best = (d, eid, frame)
        if best is None or best[0] > 14.0 ** 2:
            return
        _, eid, frame = best
        self._highlight_ids({eid})
        self.goto_frame(frame)
        self.service.select(Selection(entity_ids=(eid,), primary_id=eid, mode='selected',
                                      source_view=self.view_id, generation=self.service.next_generation()))

    def goto_frame(self, frame):
        """Step the viewer's time axis to ``frame`` (the stack's own frame index)."""
        if self.viewer is None:
            return
        try:
            step = list(self.viewer.dims.current_step)
            if step:
                step[0] = int(frame)
                self.viewer.dims.current_step = tuple(step)
        except Exception as exc:                         # broad-ok: ui_cleanup — navigation is best-effort
            debug_log('results_view: could not step the viewer', exc)

    # ── inbound: highlight the selected object's line ──────────────────────────────────────────
    def apply_selection(self, state):
        self._highlight_ids({str(e) for e in (getattr(state, 'entity_ids', ()) or ())})

    def _highlight_ids(self, eids):
        for art in self._highlight:
            try:
                art.remove()
            except Exception:                            # broad-ok: ui_cleanup — already removed
                pass
        self._highlight = []
        for eid in eids:
            line = self._lines.get(eid)
            if line is not None:
                (h,) = self.ax.plot(line.get_xdata(), line.get_ydata(), **_HIGHLIGHT)
                self._highlight.append(h)
        self.canvas.draw_idle()

    # ── the time cursor ────────────────────────────────────────────────────────────────────────
    def _frame_time(self, frame):
        d = self.df[self.df['frame'] == frame]
        if len(d):
            return float(d['time_s'].iloc[0])
        f, t = self.df['frame'].to_numpy(float), self.df['time_s'].to_numpy(float)
        if len(f) > 1 and np.ptp(f) > 0:
            return float(np.interp(frame, np.unique(f), np.unique(t)))
        return None

    def _draw_cursor(self):
        if self.viewer is None:
            return
        try:
            frame = int(self.viewer.dims.current_step[0])
        except Exception:                                # broad-ok: optional_probe — a 2D viewer has no time axis
            return
        t = self._frame_time(frame)
        if t is None:
            return
        if self._cursor is None:
            self._cursor = self.ax.axvline(t, color='0.2', lw=0.9, ls='--', zorder=7)
        else:
            self._cursor.set_xdata([t, t])

    def _connect_time_cursor(self):
        if self.viewer is None:
            return

        def _on_step(_event=None):
            self._draw_cursor()
            self.canvas.draw_idle()
        try:
            self.viewer.dims.events.current_step.connect(_on_step)
            self._dims_cb = _on_step
        except Exception as exc:                         # broad-ok: optional_probe — no live viewer headless
            debug_log('results_view: no time cursor', exc)

    def close(self):
        try:
            self.service.unsubscribe(self.view_id, self.apply_selection)
        except Exception as exc:                         # broad-ok: ui_cleanup — teardown is best-effort
            debug_log('results_view: trace unsubscribe failed', exc)
        if self._dims_cb is not None and self.viewer is not None:
            try:
                self.viewer.dims.events.current_step.disconnect(self._dims_cb)
            except Exception:                            # broad-ok: ui_cleanup — already disconnected
                pass
            self._dims_cb = None
        try:
            self.canvas.mpl_disconnect(self._cid)
        except Exception:                                # broad-ok: ui_cleanup — stale cid
            pass


def build_workspace(tables, service, *, viewer=None, cell_layer=None, track_layer=None, source_path=None,
                    prefix='cellts'):
    """The linked results panel for one movie's ``result_tables``. Returns the `BrushableWorkspace`."""
    from pycat.ui.brushable_workspace import BrushableWorkspace

    ws = BrushableWorkspace(service)
    cells, frames = tables['cells'], tables['cell_frames']
    tracks, conds = tables.get('condensate_tracks'), tables.get('condensates')
    frames = frames.merge(cells[['cell', 'expression']], on='cell', how='left', suffixes=('', '_c')) \
        if 'expression' not in frames else frames
    tv = TraceView(frames, service, f'{prefix}:cell_traces', group_col='cell', metrics=CELL_TRACE_METRICS,
                   viewer=viewer, color_col='expression', color_label='expression (net counts)')
    ws.add_view_widget(tv, tv.widget, title='Per-cell traces — click a line: select the cell, jump to that time')
    if conds is not None and len(conds):
        tt = TraceView(conds, service, f'{prefix}:track_traces', group_col='track_id',
                       metrics=TRACK_TRACE_METRICS, viewer=viewer, max_lines=300)
        ws.add_view_widget(tt, tt.widget, title='Condensate tracks — one line per condensate')
    ws.add_plot(cells, 'expression', 't_half_s', f'{prefix}:thalf_vs_expr',
                title='Half-time vs expression (one point per cell)')
    ws.add_plot(cells, 'expression', 'initial_condensed_fraction', f'{prefix}:frac_vs_expr',
                title='Initial condensed fraction vs expression')
    plot_tables = {'cells': cells, 'cell_frames': frames}
    if tracks is not None and len(tracks):
        plot_tables['condensate_tracks'] = tracks
    ws.add_plot_builder(plot_tables, f'{prefix}:builder')
    ws.add_table(cells, f'{prefix}:cells_table', title='Cells')
    if tracks is not None and len(tracks):
        ws.add_table(tracks, f'{prefix}:tracks_table', title='Condensate tracks')
    if viewer is not None and cell_layer is not None:
        ws.add_image_tier(viewer, cell_layer, cells, f'{prefix}:cell_tier', label_col='cell',
                          source_path=source_path)
    if viewer is not None and track_layer is not None and tracks is not None and len(tracks):
        ws.add_image_tier(viewer, track_layer, tracks, f'{prefix}:track_tier', label_col='track_label',
                          source_path=source_path, reveal='overlay')
    return ws


def build_invitro_workspace(tables, service, *, viewer=None, track_layer=None, source_path=None, prefix='tsivf'):
    """The linked results panel of the in-vitro time-series kinetics (`invitro.timeseries_kinetics.
    brush_ready_kinetics` tables): droplet traces (click: select the droplet, jump to that time), kinetics
    against size, the droplet table, and the tracked-droplet labels as a click target in the viewer."""
    from pycat.ui.brushable_workspace import BrushableWorkspace
    ws = BrushableWorkspace(service)
    droplets, frames = tables['droplets'], tables['droplet_frames']
    tv = TraceView(frames, service, f'{prefix}:traces', group_col='track_id', metrics=droplet_trace_metrics(frames),
                   viewer=viewer, max_lines=400)
    ws.add_view_widget(tv, tv.widget, title='Droplet traces — click a line: select the droplet, jump to that time')
    for x, y, title in (('mean_area_um2', 't_half_s', 'Half-time vs size'),
                        ('mean_area_um2', 'amplitude', 'Amplitude vs size')):
        if {x, y} <= set(droplets.columns):
            ws.add_plot(droplets, x, y, f'{prefix}:{y}', title=title)
    ws.add_plot_builder({'droplets': droplets}, f'{prefix}:builder')
    ws.add_table(droplets, f'{prefix}:table', title='Droplets with kinetics')
    if viewer is not None and track_layer is not None:
        ws.add_image_tier(viewer, track_layer, droplets, f'{prefix}:tier', label_col='droplet_label',
                          source_path=source_path, reveal='overlay')
    return ws
