"""**A native-Qt interactive scatter that brushes through the SelectionService.**

The fourth plot backend. matplotlib stays the export/publication backend; PyQtGraph is the *explore*
backend — native Qt (napari is Qt), so a click is a Qt signal in the same event loop, with no
Qt→WebEngine→JS bridge and low latency at large N.

It plugs into seams that are already proven and in use: the row-order guarantee (`_verify_row_order`),
the `SelectionService`, and the overlay-artist highlight. It builds **no** second identity or selection
path — that is the parallel-registries tax the audit warned about.

── The two lessons this backend is built around ─────────────────────────────────────────────

1. **Identity is backend-independent, so it is verified, not assumed.** A fast backend that mismaps a
   click to the wrong object is worse than a slow correct one. So the scatter runs the same
   `_verify_row_order` check every other backend runs, and **refuses** (returns ``ok=False``) rather
   than wire a click that could land on the wrong row.

2. **A click must not loop** (the VPT-rework Problem 3 that force-closed the app). The brushing is a
   proper :class:`~pycat.utils.selection_service.SelectionView` (Gap 5): ``apply_selection`` renders an
   inbound selection on a *separate* overlay item under a :class:`ProgrammaticGuard`, so it never
   re-emits; a click emits one command carrying ``source_view`` (the service skips the emitter); and
   camera-follow is opt-in. Same contract every other view passes.

PyQtGraph is an **optional** dependency: imported lazily inside these functions, and `plot_backends`
only offers ``'pyqtgraph'`` when it is installed. PyCAT imports and runs headlessly without it — the
non-negotiable headless-import contract.
"""

from __future__ import annotations

import numpy as np

from pycat.utils.general_utils import debug_log
from pycat.utils.selection_service import ProgrammaticGuard, Selection, register_view


_SOURCE_VIEW = 'pyqtgraph.plot'
_BASE_BRUSH = (76, 114, 176, 180)         # the default point colour (matches the matplotlib blue)
_HILITE = (255, 140, 0)                    # the selection orange the rest of the arc uses


def pyqtgraph_available() -> bool:
    """True when the optional dependency is importable. `plot_backends` gates the backend on this."""
    try:
        import pyqtgraph  # noqa: F401
        return True
    except Exception:
        return False


def pd_unique(series):
    seen, out = set(), []
    for v in series:
        if v not in seen:
            seen.add(v); out.append(v)
    return out


def _hue_brushes(df, hue):
    """One brush per point, coloured by group — still one artist, still row order."""
    import pyqtgraph as pg
    from pycat.utils.figure_publication import PUBLICATION_PALETTE

    cats = list(pd_unique(df[hue]))
    lut = {c: pg.mkBrush(PUBLICATION_PALETTE[i % len(PUBLICATION_PALETTE)])
           for i, c in enumerate(cats)}
    return [lut[v] for v in df[hue]]


def pyqtgraph_scatter(df, x_col, y_col, *, hue=None, title=None):
    """A ``ScatterPlotItem`` whose points map **1:1 to ``df`` rows in order**, in a ``PlotWidget``.

    Returns ``(widget, scatter_item, ok, message)`` — the same contract the matplotlib/seaborn
    ``scatter`` returns, so a caller treats identity the same whatever the backend. ``ok`` is False (and
    ``scatter_item`` None) when the points cannot be trusted to map to rows.

    ``hue`` colours points per group but keeps **one** scatter item in row order — the split-into-many-
    artists trap that breaks seaborn's index map cannot happen here, because we never split.
    """
    import pyqtgraph as pg
    from pycat.utils.plot_backends import _verify_row_order

    x = np.asarray(df[x_col], dtype=float)
    y = np.asarray(df[y_col], dtype=float)

    widget = pg.PlotWidget(title=title)
    widget.setLabel('bottom', str(x_col))
    widget.setLabel('left', str(y_col))

    brushes = _hue_brushes(df, hue) if hue is not None and hue in df.columns else _BASE_BRUSH
    scatter = pg.ScatterPlotItem(x=x, y=y, data=np.arange(len(df)), brush=brushes,
                                 pen=None, size=10)
    widget.addItem(scatter)

    drawn = np.column_stack([x, y])
    ok, message = _verify_row_order(drawn, df, x_col, y_col)
    return widget, (scatter if ok else None), ok, message


def _follow(central_manager) -> bool:
    """Camera-follow is opt-in and OFF by default — the VPT-P3 lesson, one authority for it."""
    from pycat.utils.brushing import _follow_enabled
    return _follow_enabled(central_manager)


class PyQtGraphScatterView:
    """A :class:`SelectionView` over a pyqtgraph ``ScatterPlotItem``.

    A click emits one ``Selection`` (``source_view`` = ``view_id``); an inbound selection highlights the
    matching points on a **separate overlay** item (O(1), not a recolour of N). The ``ProgrammaticGuard``
    is the primary echo defence — rendering a selection never emits — and the service's ``source_view``
    suppression is the second line. Same contract the table, the dock and the MSD plot pass.
    """

    def __init__(self, widget, scatter_item, ids, service, *, view_id=_SOURCE_VIEW,
                 refs=None, on_select=None, viewer=None, central_manager=None):
        import pyqtgraph as pg
        self.view_id = str(view_id)
        self.widget = widget
        self.scatter = scatter_item
        self.ids = list(ids)
        self.refs = list(refs) if refs is not None else None
        self.service = service
        self.on_select = on_select
        self.viewer = viewer
        self.central_manager = central_manager
        self.guard = ProgrammaticGuard()

        self.overlay = pg.ScatterPlotItem(x=[], y=[], brush=pg.mkBrush(_HILITE),
                                          pen=pg.mkPen('w', width=1), size=16)
        self.overlay.setZValue(10)
        widget.addItem(self.overlay)
        self._base_xy = scatter_item.getData()
        scatter_item.sigClicked.connect(self._on_clicked)

    def _highlight(self, indices):
        xs = [self._base_xy[0][i] for i in indices if 0 <= i < len(self.ids)]
        ys = [self._base_xy[1][i] for i in indices if 0 <= i < len(self.ids)]
        self.overlay.setData(x=xs, y=ys)

    # ── inbound (SelectionView.apply_selection): render, never emit ───────────────────────
    def apply_selection(self, state):
        with self.guard.applying():
            wanted = set(state.entity_ids or ())
            self._highlight([i for i, e in enumerate(self.ids) if e in wanted])

    # ── outbound: the user clicked a point ────────────────────────────────────────────────
    def _on_clicked(self, _item, points, _event=None):
        if self.guard.is_applying or self.service.is_busy or not points:
            return
        try:
            index = int(points[0].index())
        except Exception as exc:
            debug_log('pyqtgraph brushing: could not read the clicked index', exc)
            return
        if not (0 <= index < len(self.ids)):
            return

        self._highlight([index])          # mark in place — no camera move, so no draw->re-enter
        if self.on_select is not None and self.refs is not None:
            try:
                self.on_select(self.refs[index])
            except Exception as exc:
                debug_log('pyqtgraph brushing: on_select failed', exc)
        # Opt-in navigation only — a plain click must not yank the view or re-enter selection.
        if self.viewer is not None and self.refs is not None and _follow(self.central_manager):
            try:
                from pycat.utils.object_ref import resolve_in_viewer
                resolve_in_viewer(self.refs[index], self.viewer, centre=True)
            except Exception as exc:
                debug_log('pyqtgraph brushing: reveal failed', exc)

        self.service.select(Selection(entity_ids=(self.ids[index],), primary_id=self.ids[index],
                                      mode='selected', source_view=self.view_id,
                                      generation=self.service.next_generation()))

    def close(self):
        try:
            self.scatter.sigClicked.disconnect(self._on_clicked)
        except Exception:
            pass
        try:
            self.service.unsubscribe(self.view_id)
        except Exception:
            pass


def make_pyqtgraph_pickable(widget, scatter_item, refs, *, service=None, entity_id_of=None,
                            view_id=_SOURCE_VIEW, on_select=None, viewer=None, central_manager=None):
    """Wire a pyqtgraph scatter into linked brushing as a :class:`PyQtGraphScatterView`, registered
    (subscribed + current state pushed). ``refs`` is one object per point, in row order;
    ``entity_id_of(ref) -> str`` is its stable entity id (default ``str(ref)``). Returns the view, or
    None if it cannot be wired."""
    if scatter_item is None or not refs or service is None:
        return None
    entity_id_of = entity_id_of or (lambda r: str(r))
    ids = [entity_id_of(r) for r in refs]
    view = PyQtGraphScatterView(widget, scatter_item, ids, service, view_id=view_id,
                                refs=refs, on_select=on_select, viewer=viewer,
                                central_manager=central_manager)
    register_view(service, view)          # subscribe + push current state (opening reflects selection)
    return view
