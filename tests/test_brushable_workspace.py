"""**The reusable brushable workspace core: plots left, tables right, one selection service.**

Phase 1 of the brushable-results-workspace spec. `BrushablePlot` is a scatter promoted to the
`SelectionView` contract (so it composes and is contract-verified); `BrushableWorkspace` stacks plots on the
left and brushable tables on the right, all keyed on `_pycat_entity_id` through the one `SelectionService`.
These pin: the plot passes the shared view contract; a plot click selects the nearest object everywhere; a
plot and a table over the same data brush together; and two entity *types* (cell vs condensate) are
independent tiers on one service.
"""
import matplotlib
matplotlib.use('Agg')
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

import pandas as pd
import pytest

from pycat.utils.entity_ref import ENTITY_ID_COLUMN
from pycat.utils.selection_service import SelectionService, SelectionState
from pycat.ui.brushable_workspace import BrushablePlot
from tests.selection_view_contract import assert_selection_view_contract

pytestmark = pytest.mark.core


def _service():
    return SelectionService(defer=lambda fn: fn())


def _cell_df():
    return pd.DataFrame({
        'intensity_total': [10.0, 20.0, 30.0, 40.0],
        'puncta_intensity_total': [1.0, 4.0, 9.0, 16.0],
        'cell_xor_puncta_int_total': [9.0, 16.0, 21.0, 24.0],
        ENTITY_ID_COLUMN: ['ds/op/cell/0/1', 'ds/op/cell/0/2', 'ds/op/cell/0/3', 'ds/op/cell/0/4'],
    })


def _plot_over(df, service, view_id='ws.plot', x='intensity_total', y='puncta_intensity_total'):
    fig = Figure()
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    return BrushablePlot(ax, df, x, y, service, view_id)


def _click_point(view, i):
    """Simulate a user clicking the i-th object point: transform its data coords to display and emit."""
    view.figure.canvas.draw()
    _eid, x, y = view._points[i]
    px, py = view.ax.transData.transform((x, y))
    return view.emit_nearest(px, py, radius_px=60.0)


# ── the contract ────────────────────────────────────────────────────────────────────────────────
def test_the_plot_satisfies_the_selection_view_contract():
    service = _service()
    df = _cell_df()

    def make_view():
        return _plot_over(df, service)

    def do_user_select(view):
        _click_point(view, 0)

    assert_selection_view_contract(
        service, make_view, do_user_select,
        an_entity=df[ENTITY_ID_COLUMN].iloc[0],
        other_state=SelectionState(selected=frozenset({'x/y/z/9/9'}), primary='x/y/z/9/9'))


def test_a_plot_click_selects_the_nearest_object_everywhere():
    service = _service()
    seen = []
    service.subscribe('probe', lambda st: seen.append(st.primary_id))
    view = _plot_over(_cell_df(), service)

    eid = _click_point(view, 2)                          # click the 3rd cell
    assert eid == 'ds/op/cell/0/3'
    assert service.state.primary_id == 'ds/op/cell/0/3'
    assert seen[-1] == 'ds/op/cell/0/3'                  # the other view heard it
    view.close()


def test_an_inbound_selection_rings_the_point_without_emitting():
    service = _service()
    commands = []
    service.subscribe('probe', lambda st: commands.append(st.source_view))
    view = _plot_over(_cell_df(), service)
    before = len(commands)

    view.apply_selection(SelectionState(selected=frozenset({'ds/op/cell/0/2'}), primary='ds/op/cell/0/2'))
    assert view._ring is not None                        # the point is ringed
    assert len(commands) == before                       # ...but no command was emitted (programmatic)
    view.close()


# ── the workspace assembly (needs a QApplication) ─────────────────────────────────────────────────
def test_a_plot_and_a_table_over_the_same_data_brush_together(qtbot):
    from pycat.ui.brushable_workspace import BrushableWorkspace
    service = _service()
    df = _cell_df()

    ws = BrushableWorkspace(service)
    qtbot.addWidget(ws)
    plot = ws.add_plot(df, 'intensity_total', 'puncta_intensity_total', 'cell.plot', title='Csat')
    table = ws.add_table(df, 'cell.table', title='Cells')
    assert table is not None and table.is_linked

    # click a plot point -> the matching table row is selected
    _click_point(plot, 1)
    assert table.selected_entity_id() == 'ds/op/cell/0/2'

    # a selection from elsewhere rings the plot point too
    service.select_entity('ds/op/cell/0/4', source='__external__')
    assert plot._ring is not None
    ws.detach()


def test_the_viewer_level_dispatcher_picks_the_finest_tier_regardless_of_active_layer(qtbot):
    """One viewer-level handler tries the tiers finest-first, so a click on a condensate brushes the
    condensate and a click on bare cell brushes the cell — without switching the active napari layer."""
    import types
    from pycat.ui.brushable_workspace import BrushableWorkspace
    service = _service()

    cells = pd.DataFrame({'clabel': [1, 2], ENTITY_ID_COLUMN: ['ds/op/cell/0/1', 'ds/op/cell/0/2']})
    conds = pd.DataFrame({'plabel': [7], ENTITY_ID_COLUMN: ['ds/op/punctum/0/1/7']})
    viewer = types.SimpleNamespace(mouse_drag_callbacks=[], layers={})
    cell_layer = types.SimpleNamespace(metadata={'pycat_layer_id': 'Lc'}, mouse_drag_callbacks=[],
                                       get_value=lambda position, world=True: 1)      # always over cell 1
    cond_state = {'v': 7}
    cond_layer = types.SimpleNamespace(metadata={'pycat_layer_id': 'Lp'}, mouse_drag_callbacks=[],
                                       get_value=lambda position, world=True: cond_state['v'])

    ws = BrushableWorkspace(service)
    qtbot.addWidget(ws)
    ws.add_image_tier(viewer, cell_layer, cells, 'cell.image', label_col='clabel')      # coarse (added first)
    ws.add_image_tier(viewer, cond_layer, conds, 'cond.image', label_col='plabel')      # fine (added last)
    assert ws._viewer_cb is not None and ws._viewer_cb in viewer.mouse_drag_callbacks
    assert cell_layer._cb is None if hasattr(cell_layer, '_cb') else True                # no per-layer callback

    event = types.SimpleNamespace(position=(1, 1))
    ws._viewer_cb(viewer, event)                                # a condensate is here → condensate wins
    assert service.state.primary_id == 'ds/op/punctum/0/1/7'

    cond_state['v'] = 0                                          # no condensate here now → the cell wins
    ws._viewer_cb(viewer, event)
    assert service.state.primary_id == 'ds/op/cell/0/1'

    ws.detach()
    assert ws._viewer_cb not in viewer.mouse_drag_callbacks     # handler removed on teardown


def test_two_entity_tiers_are_independent_on_one_service(qtbot):
    """A cell selection lights the cell views; a condensate selection lights the condensate table — one
    does not fire the other's views (they are different entity keys)."""
    from pycat.ui.brushable_workspace import BrushableWorkspace
    service = _service()
    cells = _cell_df()
    condensates = pd.DataFrame({
        'area_um2': [0.5, 1.5],
        'intensity': [100.0, 200.0],
        ENTITY_ID_COLUMN: ['ds/op/punctum/0/1/1', 'ds/op/punctum/0/1/2'],
    })

    ws = BrushableWorkspace(service)
    qtbot.addWidget(ws)
    cell_plot = ws.add_plot(cells, 'intensity_total', 'puncta_intensity_total', 'cell.plot')
    cell_table = ws.add_table(cells, 'cell.table')
    cond_table = ws.add_table(condensates, 'cond.table')

    # select a CELL -> the cell table has it; the condensate table holds nothing matching
    _click_point(cell_plot, 0)
    assert cell_table.selected_entity_id() == 'ds/op/cell/0/1'
    assert cond_table.apply_selection.__self__ is cond_table   # sanity: it's a real view
    # the condensate table's row map has no cell id, so nothing was highlighted there
    assert 'ds/op/cell/0/1' not in cond_table._rows

    # select a CONDENSATE -> the condensate table has it
    service.select_entity('ds/op/punctum/0/1/2', source='__external__')
    assert cond_table.selected_entity_id() == 'ds/op/punctum/0/1/2'
    ws.detach()
