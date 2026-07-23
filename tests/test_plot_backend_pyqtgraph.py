"""**The pyqtgraph 'explore' backend: a native-Qt scatter that brushes through the SelectionService.**

The fourth plot backend (matplotlib/seaborn/plotly are the others). It is OPTIONAL — PyCAT imports and
runs without it — and it is built against the `SelectionView` contract (Gap 5), not the old
bare-callback API: the same adapter contract the table and the reference view pass.

Availability + row-order are checkable headlessly; anything that builds a real `PlotWidget` needs a
QApplication (the `qapp` fixture) and is skipped when pyqtgraph is absent.
"""

import numpy as np
import pandas as pd
import pytest

# NOTE: not a file-level `pytestmark` — availability/row-order checks are headless (core); anything that
# builds a real pyqtgraph PlotWidget needs the pytest-qt `qapp` fixture (integration). Marked per test so
# the `core` lane stays headless (test_core_lane_is_headless enforces it).

from pycat.utils.plot_backend_pyqtgraph import pyqtgraph_available

_needs_pg = pytest.mark.skipif(not pyqtgraph_available(),
                               reason="pyqtgraph not installed (pip install pycat-napari[pyqtgraph])")


class _FakePoint:
    def __init__(self, i):
        self._i = i

    def index(self):
        return self._i


# ── availability + seam wiring (headless) ─────────────────────────────────────

@pytest.mark.base
def test_availability_is_a_bool():
    assert isinstance(pyqtgraph_available(), bool)


@pytest.mark.base
def test_pyqtgraph_is_a_registered_backend():
    from pycat.utils.plot_backends import BACKENDS, available_backends
    assert 'pyqtgraph' in BACKENDS
    assert 'pyqtgraph' in available_backends()          # reported either way, with a reason if absent


@pytest.mark.base
def test_scatter_reports_clearly_when_pyqtgraph_is_absent(monkeypatch):
    """The backend seam must degrade with a message, not crash, if the extra is missing."""
    import pycat.utils.plot_backend_pyqtgraph as pg
    monkeypatch.setattr(pg, 'pyqtgraph_available', lambda: False)
    from pycat.utils.plot_backends import scatter
    fig, artist, ok, msg = scatter(pd.DataFrame({'x': [1.0], 'y': [1.0]}), 'x', 'y',
                                   backend='pyqtgraph')
    assert ok is False and artist is None and 'pyqtgraph' in msg


@pytest.mark.base
def test_pd_unique_preserves_first_seen_order():
    from pycat.utils.plot_backend_pyqtgraph import pd_unique
    assert pd_unique(['b', 'a', 'b', 'c', 'a']) == ['b', 'a', 'c']


# ── the scatter maps 1:1 to rows (Qt) ─────────────────────────────────────────

@pytest.mark.integration
@_needs_pg
def test_the_scatter_maps_1_to_1_to_the_rows_in_order(qapp):
    from pycat.utils.plot_backend_pyqtgraph import pyqtgraph_scatter
    df = pd.DataFrame({'x': [1.0, 2.0, 3.0, 4.0], 'y': [4.0, 3.0, 2.0, 1.0]})
    widget, scatter, ok, msg = pyqtgraph_scatter(df, 'x', 'y')
    assert ok is True and scatter is not None, msg
    xs, ys = scatter.getData()
    assert np.allclose(xs, df['x']) and np.allclose(ys, df['y']), "points are not in row order"


@pytest.mark.integration
@_needs_pg
def test_hue_keeps_ONE_scatter_item_in_row_order(qapp):
    from pycat.utils.plot_backend_pyqtgraph import pyqtgraph_scatter
    df = pd.DataFrame({'x': [1.0, 2.0, 3.0], 'y': [1.0, 2.0, 3.0], 'grp': ['a', 'b', 'a']})
    widget, scatter, ok, msg = pyqtgraph_scatter(df, 'x', 'y', hue='grp')
    assert ok is True and scatter is not None
    xs, _ = scatter.getData()
    assert np.allclose(xs, df['x']), "hue split the data or reordered it — a click would mismap"


# ── the SelectionView contract (Qt) ───────────────────────────────────────────

@pytest.mark.integration
@_needs_pg
def test_the_pyqtgraph_view_passes_the_SelectionView_contract(qapp):
    from tests.selection_view_contract import assert_selection_view_contract
    from pycat.utils.selection_service import SelectionService, SelectionState
    from pycat.utils.plot_backend_pyqtgraph import make_pyqtgraph_pickable, pyqtgraph_scatter

    df = pd.DataFrame({'x': [1.0, 2.0, 3.0, 4.0], 'y': [1.0, 2.0, 3.0, 4.0]})
    ids = ['e0', 'e1', 'e2', 'e3']
    service = SelectionService(defer=lambda fn: fn())

    def make_view():
        widget, scatter, ok, _msg = pyqtgraph_scatter(df, 'x', 'y')
        return make_pyqtgraph_pickable(widget, scatter, ids, service=service,
                                       entity_id_of=lambda r: r)

    def do_user_select(view):
        view._on_clicked(view.scatter, [_FakePoint(1)])     # a user clicks point 1 (entity e1)

    assert_selection_view_contract(
        service, make_view, do_user_select, an_entity='e2',
        other_state=SelectionState(selected=frozenset({'e0'}), primary='e0'))


@pytest.mark.integration
@_needs_pg
def test_an_inbound_selection_HIGHLIGHTS_the_matching_point_on_the_overlay(qapp):
    from pycat.utils.selection_service import SelectionService, SelectionState
    from pycat.utils.plot_backend_pyqtgraph import make_pyqtgraph_pickable, pyqtgraph_scatter

    df = pd.DataFrame({'x': [1.0, 2.0, 3.0], 'y': [1.0, 2.0, 3.0]})
    service = SelectionService(defer=lambda fn: fn())
    widget, scatter, ok, _ = pyqtgraph_scatter(df, 'x', 'y')
    view = make_pyqtgraph_pickable(widget, scatter, ['a', 'b', 'c'], service=service,
                                   entity_id_of=lambda r: r)

    # a selection from ANOTHER view highlights point 'b' (index 1) on the overlay, and emits nothing.
    view.apply_selection(SelectionState(selected=frozenset({'b'}), primary='b'))
    ox, oy = view.overlay.getData()
    assert list(ox) == [2.0] and list(oy) == [2.0], "the overlay did not mark the selected point"
