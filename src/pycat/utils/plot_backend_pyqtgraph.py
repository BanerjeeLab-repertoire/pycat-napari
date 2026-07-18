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

2. **A click must not loop** (the VPT-rework Problem 3 that force-closed the app). A reveal that
   re-enters selection is the exact failure. So: the emitted selection carries ``source_view`` and the
   service skips the emitter (echo-suppression); an inbound highlight is drawn on a *separate* overlay
   item and never re-emits; and camera-follow is opt-in (`central_manager.follow_selection`, off by
   default), so a plain click marks in place and does not yank or re-enter.

PyQtGraph is an **optional** dependency: imported lazily inside these functions, and
`plot_backends` only offers ``'pyqtgraph'`` when it is installed. PyCAT imports and runs headlessly
without it — the non-negotiable headless-import contract.
"""

from __future__ import annotations

import numpy as np

from pycat.utils.general_utils import debug_log


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


def _hue_brushes(df, hue):
    """One brush per point, coloured by group — still one artist, still row order."""
    import pyqtgraph as pg
    from pycat.utils.figure_publication import PUBLICATION_PALETTE

    cats = list(pd_unique(df[hue]))
    lut = {c: pg.mkBrush(PUBLICATION_PALETTE[i % len(PUBLICATION_PALETTE)])
           for i, c in enumerate(cats)}
    return [lut[v] for v in df[hue]]


def pd_unique(series):
    seen, out = set(), []
    for v in series:
        if v not in seen:
            seen.add(v); out.append(v)
    return out


def make_pyqtgraph_pickable(widget, scatter_item, refs, *, service=None, entity_id_of=None,
                            on_select=None, viewer=None, central_manager=None):
    """Wire a pyqtgraph scatter into linked brushing — one selection per click, no loop.

    ``refs`` is one object per point, in row order. ``entity_id_of(ref) -> str`` maps a ref to its
    stable entity id (the key the ``SelectionService`` uses); default ``str(ref)``. A click emits one
    ``Selection`` with ``source_view='pyqtgraph.plot'``; an inbound selection highlights the matching
    points on a **separate overlay item** (O(1), not a recolour of N). Returns the overlay item.
    """
    if scatter_item is None or not refs:
        return None
    import pyqtgraph as pg
    from pycat.utils.selection_service import Selection

    entity_id_of = entity_id_of or (lambda r: str(r))
    ids = [entity_id_of(r) for r in refs]

    # The inbound-highlight overlay: a second, initially-empty scatter drawn on top. Highlighting
    # sets its points; it NEVER emits, so it cannot start a loop.
    overlay = pg.ScatterPlotItem(x=[], y=[], brush=pg.mkBrush(_HILITE), pen=pg.mkPen('w', width=1),
                                 size=16)
    overlay.setZValue(10)
    widget.addItem(overlay)
    base_xy = scatter_item.getData()
    state = {'suppress': False}

    def _highlight_indices(indices):
        xs = [base_xy[0][i] for i in indices if 0 <= i < len(refs)]
        ys = [base_xy[1][i] for i in indices if 0 <= i < len(refs)]
        overlay.setData(x=xs, y=ys)

    def _on_clicked(_item, points, _event=None):
        if not points:
            return
        try:
            index = int(points[0].index())
        except Exception as exc:
            debug_log('pyqtgraph brushing: could not read the clicked index', exc)
            return
        if not (0 <= index < len(refs)):
            return
        ref = refs[index]

        _highlight_indices([index])          # mark in place — no camera move, so no draw->re-enter

        if on_select is not None:
            try:
                on_select(ref)
            except Exception as exc:
                debug_log('pyqtgraph brushing: on_select failed', exc)

        # Opt-in navigation only — a plain click must not yank the view or re-enter selection.
        if viewer is not None and _follow(central_manager):
            try:
                from pycat.utils.object_ref import resolve_in_viewer
                resolve_in_viewer(ref, viewer, centre=True)
            except Exception as exc:
                debug_log('pyqtgraph brushing: reveal failed', exc)

        if service is not None:
            # `source_view` is what the service skips when propagating — the echo guard that stops a
            # view re-highlighting from its own click. The `suppress` flag is the belt to that
            # braces: even if a subscriber fans back, this plot ignores its own selection.
            state['suppress'] = True
            try:
                service.select(Selection(entity_ids=(ids[index],), primary_id=ids[index],
                                         mode='selected', source_view=_SOURCE_VIEW,
                                         generation=service.next_generation()))
            finally:
                state['suppress'] = False

    def _on_inbound(selection):
        """A selection from ANOTHER view: highlight the matching points. Never emits."""
        if state['suppress'] or getattr(selection, 'source_view', '') == _SOURCE_VIEW:
            return                            # our own selection — do not echo
        wanted = set(getattr(selection, 'entity_ids', ()) or ())
        _highlight_indices([i for i, e in enumerate(ids) if e in wanted])

    scatter_item.sigClicked.connect(_on_clicked)
    if service is not None:
        service.subscribe(_SOURCE_VIEW, _on_inbound)
    return overlay


def _follow(central_manager) -> bool:
    """Camera-follow is opt-in and OFF by default — the VPT-P3 lesson, one authority for it."""
    from pycat.utils.brushing import _follow_enabled
    return _follow_enabled(central_manager)
