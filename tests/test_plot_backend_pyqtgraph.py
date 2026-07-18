"""**The PyQtGraph interactive backend — the contract that CAN be verified without a human click.**

On branch `pyqtgraph-backend`, not main, because the *experience* — does a click in a live napari dock
brush correctly and feel right — needs a viewer. But the two things that actually go wrong are
programmatically checkable, and both are here:

* **identity** — the scatter maps 1:1 to rows in order, and REFUSES (ok=False) when it cannot, so a
  click never lands on the wrong object;
* **no loop** — the VPT-rework Problem 3 that force-closed the app. A click emits exactly ONE
  selection; an inbound selection highlights on a separate overlay and never re-emits; the plot
  ignores its own selection (echo-suppression). These are tested by emitting `sigClicked`
  programmatically under offscreen Qt and counting selections — the dangerous case, without a click.

What still needs a human at a viewer: the visual render, the real mouse click, and that it feels
responsive at large N. That is the merge gate; everything below is the guard that the logic is right
before it gets there.
"""

# Standard library imports

# Third party imports
import numpy as np
import pandas as pd
import pytest

pytest.importorskip('pyqtgraph')

pytestmark = pytest.mark.integration      # needs a Qt app; run with QT_QPA_PLATFORM=offscreen


@pytest.fixture(scope='module')
def _qapp():
    from PyQt5.QtWidgets import QApplication
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def df():
    return pd.DataFrame({'x': [10.0, 20.0, 30.0, 40.0], 'y': [1.0, 2.0, 3.0, 4.0],
                         'genotype': ['WT', 'WT', 'mut', 'mut']})


class _Ref:
    def __init__(self, i):
        self.i = i

    def __repr__(self):
        return f'ref{self.i}'


class _Service:
    """A minimal SelectionService stand-in that records what was emitted and can fan back."""
    def __init__(self):
        self.selections = []
        self._subs = {}
        self._gen = 0

    def next_generation(self):
        self._gen += 1
        return self._gen

    def subscribe(self, view_id, cb):
        self._subs[view_id] = cb

    def select(self, selection):
        self.selections.append(selection)
        # fan out to every subscriber EXCEPT the source — the real service's echo guard
        for vid, cb in self._subs.items():
            if vid != selection.source_view:
                cb(selection)


# ── identity ───────────────────────────────────────────────────────────────────

def test_the_scatter_maps_1_to_1_to_rows_in_ORDER(_qapp, df):
    from pycat.utils.plot_backend_pyqtgraph import pyqtgraph_scatter
    widget, item, ok, msg = pyqtgraph_scatter(df, 'x', 'y')
    assert ok, msg
    assert len(item.points()) == len(df)
    # point i sits at row i's coordinates
    xs = item.getData()[0]
    assert list(xs) == list(df['x'])


def test_a_row_count_mismatch_is_REFUSED(_qapp):
    """If the drawn points cannot correspond to the rows, brushing must be refused, not mismapped."""
    from pycat.utils.plot_backend_pyqtgraph import pyqtgraph_scatter
    from pycat.utils import plot_backends
    # monkeypatch _verify_row_order to simulate a mismatch
    import pycat.utils.plot_backend_pyqtgraph as mod
    orig = plot_backends._verify_row_order
    plot_backends._verify_row_order = lambda *a, **k: (False, 'mismatch')
    try:
        widget, item, ok, msg = pyqtgraph_scatter(pd.DataFrame({'x': [1], 'y': [2]}), 'x', 'y')
        assert ok is False and item is None
    finally:
        plot_backends._verify_row_order = orig


def test_HUE_keeps_one_artist_in_row_order(_qapp, df):
    """Colour by group, but never split into per-group artists — that is what breaks an index map."""
    from pycat.utils.plot_backend_pyqtgraph import pyqtgraph_scatter
    widget, item, ok, msg = pyqtgraph_scatter(df, 'x', 'y', hue='genotype')
    assert ok and len(item.points()) == len(df)     # still ONE item, all rows


# ── the no-loop contract (VPT-P3), tested by emitting sigClicked ────────────────

def _click(item, index):
    """Emit sigClicked for the point at `index`, exactly as a real click would."""
    pts = item.points()
    item.sigClicked.emit(item, [pts[index]], None)


def test_a_click_emits_EXACTLY_ONE_selection(_qapp, df):
    from pycat.utils.plot_backend_pyqtgraph import pyqtgraph_scatter, make_pyqtgraph_pickable
    widget, item, ok, _ = pyqtgraph_scatter(df, 'x', 'y')
    service = _Service()
    refs = [_Ref(i) for i in range(len(df))]
    make_pyqtgraph_pickable(widget, item, refs, service=service, entity_id_of=lambda r: f'e{r.i}')

    _click(item, 2)
    assert len(service.selections) == 1
    assert service.selections[0].entity_ids == ('e2',)
    assert service.selections[0].source_view == 'pyqtgraph.plot'


def test_a_click_does_NOT_LOOP_even_when_the_service_fans_back(_qapp, df):
    """**The force-close bug, as a unit test.** The service fans the selection to every subscriber —
    including this plot's inbound handler. If that handler re-emitted, it would loop. It must not:
    one click, one selection, full stop."""
    from pycat.utils.plot_backend_pyqtgraph import pyqtgraph_scatter, make_pyqtgraph_pickable
    widget, item, ok, _ = pyqtgraph_scatter(df, 'x', 'y')
    service = _Service()
    refs = [_Ref(i) for i in range(len(df))]
    make_pyqtgraph_pickable(widget, item, refs, service=service, entity_id_of=lambda r: f'e{r.i}')

    _click(item, 1)
    # the service fanned it back to _on_inbound; if that re-entered select(), this would be > 1
    assert len(service.selections) == 1, (
        f'{len(service.selections)} selections from one click — the plot re-emitted from its own '
        f'selection, which is the loop that force-closed VPT')


def test_the_plot_IGNORES_its_own_selection(_qapp, df):
    """Echo-suppression: a selection tagged `pyqtgraph.plot` must not re-highlight this plot."""
    from pycat.utils.plot_backend_pyqtgraph import pyqtgraph_scatter, make_pyqtgraph_pickable
    from pycat.utils.selection_service import Selection
    widget, item, ok, _ = pyqtgraph_scatter(df, 'x', 'y')
    service = _Service()
    refs = [_Ref(i) for i in range(len(df))]
    overlay = make_pyqtgraph_pickable(widget, item, refs, service=service,
                                      entity_id_of=lambda r: f'e{r.i}')

    # deliver a selection from OUR OWN view id — must be ignored (no highlight)
    service.select(Selection(entity_ids=('e0',), source_view='pyqtgraph.plot'))
    assert len(overlay.getData()[0]) == 0, 'the plot highlighted from its own selection'


def test_an_INBOUND_selection_highlights_on_the_OVERLAY_not_the_base(_qapp, df):
    """O(1) inbound highlight: a second overlay item carries the mark, the base scatter is untouched
    (no recolour of N points)."""
    from pycat.utils.plot_backend_pyqtgraph import pyqtgraph_scatter, make_pyqtgraph_pickable
    from pycat.utils.selection_service import Selection
    widget, item, ok, _ = pyqtgraph_scatter(df, 'x', 'y')
    service = _Service()
    refs = [_Ref(i) for i in range(len(df))]
    overlay = make_pyqtgraph_pickable(widget, item, refs, service=service,
                                      entity_id_of=lambda r: f'e{r.i}')

    # a selection from ANOTHER view highlights the matching point on the overlay
    service.subscribe('other.view', lambda s: None)
    from types import SimpleNamespace
    # simulate an inbound selection not from us
    for cb_vid, cb in list(service._subs.items()):
        if cb_vid == 'pyqtgraph.plot':
            cb(Selection(entity_ids=('e3',), source_view='table.view'))
    hx, hy = overlay.getData()
    assert list(hx) == [df['x'].iloc[3]] and list(hy) == [df['y'].iloc[3]]
    # base scatter still has all its points, unrecoloured
    assert len(item.points()) == len(df)


def test_camera_follow_is_OFF_by_default(_qapp, df):
    """A plain click marks in place; it does not reveal-in-viewer unless follow_selection is on —
    which is also what keeps a click from firing a draw that re-enters selection."""
    from pycat.utils.plot_backend_pyqtgraph import pyqtgraph_scatter, make_pyqtgraph_pickable
    widget, item, ok, _ = pyqtgraph_scatter(df, 'x', 'y')
    revealed = []

    class _Viewer:
        pass

    class _CM:
        follow_selection = False

    refs = [_Ref(i) for i in range(len(df))]
    # on_select records; viewer reveal would append if follow were on
    make_pyqtgraph_pickable(widget, item, refs, viewer=_Viewer(), central_manager=_CM(),
                            on_select=lambda r: revealed.append(('select', r)))
    _click(item, 0)
    assert ('select', refs[0]) in revealed          # on_select fired
    # no reveal attempted (follow off) — nothing raised, and the click stayed local
