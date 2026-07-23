"""VPT UI — the combined results dock (one dockable widget: the 2×2 figure panel
on the left, the per-track table on the right, and a bucket pager to browse every
track a slice at a time).

Why a dock instead of the old pyplot windows + standalone table QDialog:

* **Brushing was fragile because the surfaces were disposable.** The MSD/table
  highlight receivers read a registry (``_msd_line_registry`` / ``_track_table_registry``)
  that was torn down whenever a pyplot window or the table dialog closed, so a
  table→plot selection silently no-op'd against a dead canvas. Embedding the figure
  canvas and the table in ONE persistent dock keeps those highlight targets alive.
* **All the data can be seen.** The MSD spaghetti and the centered-trajectory panels
  cap how many tracks they draw (past ~100 the spread stops changing). The pager lets
  the user step through literal buckets of tracks so nothing is hidden — every track
  is on a plot on some page.

A mixin so ``vpt_ui.py`` composes it. It reuses the ``_draw_*`` panel painters and
``_msd_overlay_hooks`` from ``analysis_plots`` (the same ones the pop-out path uses),
and the shared ``_select_track`` / ``_ensure_selection_views`` hub, so this adds a
presentation surface, not a second brushing implementation.
"""
from __future__ import annotations

import math

import numpy as np

# Below this the 2×2 grid of microrheology plots is not readable, so the dock cannot be dragged into an
# unusable state silently (floating the dock escapes the right-panel width entirely — the real fix for detail).
_RESULTS_CANVAS_MIN_W = 520
_RESULTS_CANVAS_MIN_H = 360


def _new_results_figure():
    """A 2×2 results figure that **reflows on every resize** via constrained layout, instead of being authored
    at a fixed 11×8.5 print size and laid out once at draw. That one-shot layout was the reported bug: the
    canvas stretched with the dock but the axes kept their old geometry ("won't stretch", overlapping labels).
    Constrained layout recomputes subplot geometry on each resize event, with no event plumbing. Returns
    ``(fig, axes)`` — the caller wraps it in a Qt canvas with an expanding size policy."""
    from matplotlib.figure import Figure
    fig = Figure(layout='constrained')      # NOT figsize=(11, 8.5): the canvas drives the size, not the print
    axes = fig.subplots(2, 2)
    return fig, axes


class _VptResultsDockMixin:
    """VPT combined-results dock + bucket pager + centered-trajectory selection.
    Mixed into ``VideoParticleTrackingUI``."""

    #: Default tracks-per-bucket. ~100 is where the MSD band converges, so it is a
    #: sensible slice size; the spinbox lets the user change it live.
    _VPT_BUCKET_DEFAULT = 100

    # ── Centered-trajectory view (the fourth SelectionView) ──────────────────────
    def _on_selection_centered(self, selection):
        tid = self._track_of(selection)
        if tid is None:
            return
        try:
            self._highlight_track_in_centered(tid)
        except Exception as e:                       # broad-ok: one dead view must not take the others down
            print(f"[PyCAT VPT] link→centered failed: {e}")

    def _highlight_track_in_centered(self, track_id):
        """Emphasise a track's centered path (if the results dock is open and its
        line map was registered). Promotes a track that was not drawn on the current
        page/sample so a selection from any view still lands. No-op if the dock is closed."""
        reg = getattr(self, '_centered_registry', None)
        if not reg:
            return
        lines = reg.get('lines'); canvas = reg.get('canvas')
        if lines is None:
            return
        state = reg.setdefault('state', {'prev': None})
        tid = int(track_id)
        prev = state.get('prev')
        if prev == tid:
            return
        demote = reg.get('demote'); promote = reg.get('promote')
        if prev is not None and demote is not None:
            demote(prev)
        ln = lines.get(tid)
        if ln is None and promote is not None:
            ln = promote(tid)
        if ln is not None:
            from pycat.toolbox.analysis_plots import _CENTERED_HL
            try:
                ln.set(**_CENTERED_HL)
            except Exception:                        # broad-ok: a restyle failure must not wedge selection
                pass
        state['prev'] = tid
        try:
            if canvas is not None:
                canvas.draw_idle()
        except Exception:                            # broad-ok: draw is best-effort
            pass

    # ── The per-track table (embedded, not a standalone dialog) ──────────────────
    def _vpt_build_table(self, per_track_metrics):
        """Build the per-track ``QTableWidget`` for the dock's right pane and register
        ``_track_table_registry`` so a selection from any view highlights the right row.
        Row-click selects that track everywhere. Returns the QTableWidget (or None)."""
        from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem
        if per_track_metrics is None or getattr(per_track_metrics, 'empty', True):
            self._track_table_registry = None
            return None
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
            except Exception:                        # broad-ok: a non-int id just isn't row-mapped
                pass
        table.resizeColumnsToContents()
        self._track_table_registry = {'table': table, 'row_for_id': row_for_id,
                                      'id_col': id_col}

        def _on_row(*_):
            if self._selection().is_busy:
                return
            items = table.selectedItems()
            if not items:
                return
            try:
                # Cell text can be float-formatted ("48.0"); int("48.0") throws, so via float.
                tid = int(float(table.item(items[0].row(), id_col).text()))
            except Exception:                        # broad-ok: unparsable id cell → no selection
                return
            self._select_track(tid, source='table')
        table.itemSelectionChanged.connect(_on_row)
        return table

    # ── The dock itself ──────────────────────────────────────────────────────────
    def _show_vpt_results(self, ptc, msd_df, fit, mod, tracks, per_track_metrics,
                          frame_dt, van_hove_lag=1, *, restore_page=0, restore_bucket=None):
        """Open (or replace) the combined VPT results dock: figure left, table right,
        with a bucket pager over the tracks. Everything the pop-out path drew, in one
        persistent widget so the brushing highlight targets stay alive.

        ``restore_page`` / ``restore_bucket`` land a REOPENED dock (Part 2's "Show results") back where the
        user left it, rather than resetting to page 0 — they are passed only on a reopen, not a fresh run."""
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
        from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
                                     QLabel, QPushButton, QSpinBox, QSizePolicy)
        from PyQt5.QtCore import Qt

        all_tids = []
        try:
            if tracks is not None and 'track_id' in tracks:
                all_tids = sorted(int(t) for t in tracks['track_id'].unique() if t >= 0)
        except Exception:                            # broad-ok: no usable track ids → empty pager
            all_tids = []

        # ── left: pager row + the 2×2 figure canvas ──
        # A constrained-layout figure (reflows on resize) in a canvas that EXPANDS with the dock and has a
        # minimum size below which the 2×2 grid is unreadable — the fix for "squashed plots that won't stretch".
        fig, axes = _new_results_figure()
        canvas = FigureCanvasQTAgg(fig)
        canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        canvas.setMinimumSize(_RESULTS_CANVAS_MIN_W, _RESULTS_CANVAS_MIN_H)

        prev_btn = QPushButton("◀ Prev")
        next_btn = QPushButton("Next ▶")
        page_lbl = QLabel(""); page_lbl.setAlignment(Qt.AlignCenter)
        size_spin = QSpinBox()
        size_spin.setRange(10, 5000); size_spin.setSingleStep(10)
        size_spin.setValue(int(restore_bucket) if restore_bucket else self._VPT_BUCKET_DEFAULT)
        size_spin.setPrefix("bucket "); size_spin.setSuffix(" tracks")
        pager = QHBoxLayout()
        pager.addWidget(prev_btn); pager.addWidget(page_lbl, 1)
        pager.addWidget(next_btn); pager.addWidget(size_spin)

        left = QWidget(); lv = QVBoxLayout(left)
        lv.setContentsMargins(2, 2, 2, 2)
        lv.addLayout(pager); lv.addWidget(canvas, 1)

        # ── right: title + per-track table ──
        table = self._vpt_build_table(per_track_metrics)
        right = QWidget(); rv = QVBoxLayout(right)
        rv.setContentsMargins(2, 2, 2, 2)
        rv.addWidget(QLabel("Per-track metrics — click a row to reveal that track "
                            "in the image and highlight its curves."))
        if table is not None:
            rv.addWidget(table, 1)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left); splitter.addWidget(right)
        splitter.setStretchFactor(0, 3); splitter.setStretchFactor(1, 2)

        # Reuse a single dock so repeated computes replace it instead of stacking.
        try:
            if getattr(self, '_vpt_results_dock', None) is not None:
                self.viewer.window.remove_dock_widget(self._vpt_results_dock)
        except Exception:                            # broad-ok: stale dock ref → just add a fresh one
            pass
        from pycat.utils.dock_space import add_results_dock
        self._vpt_results_dock = add_results_dock(
            self.viewer.window, splitter, name="VPT Results")
        # Floating the dock escapes the right-panel width entirely — the real answer for detailed inspection.
        try:
            from PyQt5.QtWidgets import QDockWidget
            _d = self._vpt_results_dock
            _d.setFeatures(_d.features() | QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetMovable)
            _d.setToolTip("Drag wider to reflow the plots — or float this dock (double-click its title bar) "
                          "for a full-size view.")
        except Exception:                            # broad-ok: ui_cleanup — floatable is a convenience, never gating
            pass

        self._vpt_results = {
            'ptc': ptc, 'msd_df': msd_df, 'fit': fit, 'mod': mod, 'tracks': tracks,
            'frame_dt': frame_dt, 'van_hove_lag': van_hove_lag,
            'fig': fig, 'axes': axes, 'canvas': canvas, 'all_tids': all_tids,
            'bucket_size': int(size_spin.value()), 'page': 0,
            'label': page_lbl, 'prev_btn': prev_btn, 'next_btn': next_btn,
            'size_spin': size_spin,
        }
        # Restore the page a reopen asked for (clamped to the current bucket count).
        self._vpt_results['page'] = min(max(0, int(restore_page or 0)),
                                        self._vpt_nbuckets(self._vpt_results))

        prev_btn.clicked.connect(lambda: self._vpt_page_step(-1))
        next_btn.clicked.connect(lambda: self._vpt_page_step(+1))
        size_spin.valueChanged.connect(self._vpt_set_bucket_size)

        self._ensure_selection_views()               # image/plot/table/centered all subscribed
        self._vpt_render_page()

        # Part 2: retain the payload so a CLOSED dock can be reopened from "Show results" with NO recompute.
        # The rebuild reads the CURRENT page/bucket off _vpt_results (which survives a close), so a reopen
        # lands where the user left it. The rebuild reuses this same method, which removes the stale dock
        # first — so reopening is idempotent and never stacks duplicates.
        try:
            from pycat.utils.results_store import retain_results
            _payload = (ptc, msd_df, fit, mod, tracks, per_track_metrics, frame_dt, van_hove_lag)

            def _rebuild():
                _st = getattr(self, '_vpt_results', None) or {}
                self._show_vpt_results(*_payload, restore_page=_st.get('page', 0),
                                       restore_bucket=_st.get('bucket_size'))
            retain_results('vpt', _rebuild, label='VPT microrheology')
        except Exception:                            # broad-ok: results retention is a convenience, never gating
            pass

    def _vpt_nbuckets(self, st):
        n = len(st['all_tids']); size = max(1, int(st['bucket_size']))
        return max(1, math.ceil(n / size)) if n else 0

    def _vpt_page_step(self, delta):
        st = getattr(self, '_vpt_results', None)
        if not st:
            return
        st['page'] = min(self._vpt_nbuckets(st), max(0, st['page'] + delta))
        self._vpt_render_page()

    def _vpt_set_bucket_size(self, v):
        st = getattr(self, '_vpt_results', None)
        if not st:
            return
        st['bucket_size'] = int(v)
        st['page'] = min(st['page'], self._vpt_nbuckets(st))
        self._vpt_render_page()

    def _vpt_drawn_tids(self):
        """The track ids actually DRAWN on the current page. For a bucket page (k ≥ 1) it is exactly the
        bucket slice of ``all_tids``; for the ensemble (page 0) the panels draw a representative SAMPLE, whose
        ids the centered/MSD registries record under ``'coords'``. Used to tell an on-page selection (already
        visible → don't move) from an off-page one (navigate)."""
        st = getattr(self, '_vpt_results', None)
        if not st:
            return set()
        if st['page'] != 0:
            size = max(1, int(st['bucket_size']))
            a = (st['page'] - 1) * size
            return set(int(t) for t in st['all_tids'][a:a + size])
        drawn = set()
        for attr in ('_centered_registry', '_msd_line_registry'):
            reg = getattr(self, attr, None) or {}
            drawn |= set(int(k) for k in (reg.get('coords') or {}).keys())
        return drawn

    def _vpt_page_to_selected_track(self, track_id):
        """Part 3: when a selection arrives for a track that is NOT on the current page, move the pager to the
        bucket that contains it and re-render, so the picked track lands AMONG ITS NEIGHBOURS instead of being
        promoted alone onto some other bucket's page (which leaves the pager label naming a range that excludes
        it). An on-page selection — including a track visible in the page-0 ensemble — does not move the view.
        The bucket index is recomputed from the CURRENT bucket size every time, never cached, so a bucket-size
        change followed by a selection still lands on the right page."""
        st = getattr(self, '_vpt_results', None)
        if not st:
            return                                   # no results dock → clean no-op (existing contract)
        try:
            tid = int(track_id)
        except (TypeError, ValueError):
            return
        all_tids = st['all_tids']
        if tid not in all_tids:
            return                                   # unknown / not a paged track → nothing to page to
        if tid in self._vpt_drawn_tids():
            return                                   # already visible on the current page → don't yank the view
        size = max(1, int(st['bucket_size']))
        st['page'] = all_tids.index(tid) // size + 1  # 1-based; page 0 is the ensemble, buckets follow
        self._vpt_render_page()                       # re-render; the existing re-highlight path then fires

    def _vpt_update_pager_label(self):
        st = self._vpt_results
        n = len(st['all_tids']); size = max(1, int(st['bucket_size'])); page = st['page']
        nb = self._vpt_nbuckets(st)
        if page == 0:
            txt = f"Ensemble — representative sample of {n} tracks"
        else:
            a = (page - 1) * size; b = min(a + size, n)
            txt = f"Tracks {a + 1}–{b} of {n}   (bucket {page} / {nb})"
        st['label'].setText(txt)
        st['prev_btn'].setEnabled(page > 0)
        st['next_btn'].setEnabled(page < nb)

    def _vpt_render_page(self):
        """Draw the current page (0 = representative ensemble; k = the k-th bucket of
        tracks) into the persistent figure, rebuild the brushing registries against the
        fresh artists, and re-apply any live selection so paging keeps the highlight."""
        from pycat.toolbox.analysis_plots import (
            _draw_msd_into, _draw_moduli_into, _draw_centered_tracks,
            _draw_van_hove, _msd_overlay_hooks)
        st = getattr(self, '_vpt_results', None)
        if not st:
            return
        fig = st['fig']; axes = st['axes']

        # Disconnect the PREVIOUS render's matplotlib click handlers before drawing
        # again — the axes are reused (ax.clear), so a stale handler stays live on the
        # canvas and would fire on the next click too, promoting another track. That
        # accumulation is what lit up multiple MSD curves after paging.
        _cv = st.get('canvas')
        for _attr in ('_msd_line_registry', '_centered_registry'):
            _old = getattr(self, _attr, None)
            if _cv is not None and _old and _old.get('click_cid') is not None:
                try:
                    _cv.mpl_disconnect(_old['click_cid'])
                except Exception:                    # broad-ok: disconnecting a dead cid is harmless
                    pass

        for ax in np.asarray(axes).ravel():
            ax.clear()

        page = st['page']; size = max(1, int(st['bucket_size'])); all_tids = st['all_tids']
        if page == 0:
            only = None
        else:
            a = (page - 1) * size
            only = all_tids[a:a + size]

        # Fresh registries each render — the artists are new after ax.clear().
        self._msd_line_registry = {}
        self._centered_registry = {}
        _draw_msd_into(axes[0, 0], st['ptc'], st['msd_df'], st['fit'],
                       line_registry=self._msd_line_registry, only_tids=only)
        _draw_moduli_into(axes[0, 1], st['mod'])
        _draw_centered_tracks(axes[1, 0], st['tracks'], only_tids=only,
                              registry=self._centered_registry,
                              on_pick_track=lambda tid: self._select_track(tid, source='centered'))
        _draw_van_hove(axes[1, 1], st['tracks'], st['van_hove_lag'], st['frame_dt'])
        fig.suptitle("VPT microrheology", fontweight='bold', fontsize=12)
        # No tight_layout here: the figure uses constrained layout (see _new_results_figure), which reflows on
        # every resize AND accounts for the suptitle. A one-shot tight_layout at draw was exactly the bug — it
        # never re-ran when the user dragged the dock wider, so the axes stayed squashed.

        # MSD plot→other brushing (emit) + promote/demote for other→plot (receive),
        # the SAME overlay hooks the pop-out path wires.
        self._msd_line_registry['canvas'] = fig.canvas
        try:
            _msd_overlay_hooks(axes[0, 0], fig, st['ptc'],
                               self._msd_line_registry.get('lines', {}),
                               self._msd_line_registry.get('coords', {}),
                               lambda tid: self._select_track(tid, source='plot'),
                               self._msd_line_registry)
        except Exception as e:                       # broad-ok: brushing wiring is best-effort
            print(f"[PyCAT VPT] MSD brushing wiring failed: {e}")

        try:
            st['canvas'].draw_idle()
        except Exception:                            # broad-ok: draw is best-effort
            pass
        self._vpt_update_pager_label()

        # Paging rebuilds the artists, so a track selected before the page turn lost
        # its highlight — re-apply it on the new page's curves.
        sel = getattr(self, '_selected_track_id', None)
        if sel is not None:
            try:
                self._highlight_track_in_plot(sel)
            except Exception:                        # broad-ok: re-highlight is best-effort
                pass
            try:
                self._highlight_track_in_centered(sel)
            except Exception:                        # broad-ok: re-highlight is best-effort
                pass
