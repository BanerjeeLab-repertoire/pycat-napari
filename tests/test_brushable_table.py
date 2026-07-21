"""**A results table that still means the right object after you sort it.**

This is what increments 1–4 were building toward, so it is the test that matters most.

Linked tables have been keyed on **row position**. VPT's is the honest example: `row_for_id =
{track_id: r}` where `r` is the **visual** row index, driving `table.selectRow(row_for_id[tid])`.
That map is correct exactly until the view is sorted — after which every highlight lands on whatever
row now sits at that position. *Nothing looks broken: a row is selected, it just isn't the one you
asked for.*

These tests are `QtCore` + `QtWidgets` only — no napari, no GL. `QApplication` runs fine offscreen;
it is `napari.Viewer` specifically that cannot (no GL context), which is why `test_ui_smoke.py`
errors here. A model/view test needs neither.
"""

# Third party imports
import pandas as pd
import pytest


pytestmark = pytest.mark.core


@pytest.fixture(scope='module')
def qapp():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return app


def _stamped(order=(3, 1, 2)):
    """A cells table whose row ORDER differs from its label order, so a sort really reorders."""
    from pycat.utils.entity_ref import stamp_entity_ids
    df = pd.DataFrame({'label': list(order), 'area': [float(v) * 10 for v in order]})
    return stamp_entity_ids(df, entity_type='cell', source_path='C:/data/a.tif',
                            operation_id='cell_analysis', frame=0)


def _service():
    from pycat.utils.selection_service import SelectionService
    return SelectionService(defer=lambda fn: fn(), debounce=lambda fn: fn())


def _table(qapp, df):
    from pycat.ui.brushable_table import make_brushable, sorted_table_view
    from pycat.ui.ui_utils import DataFrameModel
    model = DataFrameModel(df)
    view = sorted_table_view(df, model)
    service = _service()
    return make_brushable(view, df, service, 'results.cells', model=model), service


def _entity(df, label):
    from pycat.utils.entity_ref import ENTITY_ID_COLUMN
    return df.loc[df['label'] == label, ENTITY_ID_COLUMN].iloc[0]


def test_the_BrushableTable_satisfies_the_SelectionView_contract(qapp):
    """The real Qt adapter passes the SAME shared contract the reference fake and every other view
    pass (interaction-layer Gap 5): programmatic apply emits no command, a user row-select emits
    exactly one, an unknown entity is safe, and close() unsubscribes."""
    from tests.selection_view_contract import assert_selection_view_contract
    from pycat.utils.selection_service import SelectionState
    from pycat.ui.brushable_table import make_brushable, sorted_table_view
    from pycat.ui.ui_utils import DataFrameModel

    df = _stamped()
    service = _service()
    e1, e2, e3 = _entity(df, 1), _entity(df, 2), _entity(df, 3)

    def make_view():
        model = DataFrameModel(df)
        view = sorted_table_view(df, model)
        return make_brushable(view, df, service, 'results.cells', model=model)

    def do_user_select(table):
        # a USER selecting a row — drive the selection model directly (not apply_selection). Pick a
        # DIFFERENT row than the programmatic apply left selected, or selectionChanged never fires.
        src_row = table._rows[str(e2)]
        proxy_idx = table.proxy.mapFromSource(table.proxy.sourceModel().index(src_row, 0))
        table.view.selectRow(proxy_idx.row())

    assert_selection_view_contract(
        service, make_view, do_user_select, an_entity=e3,
        other_state=SelectionState(selected=frozenset({str(e1)}), primary=str(e1)))


def test_selecting_a_row_emits_the_OBJECTS_NAME_not_its_position(qapp):
    df = _stamped()
    table, service = _table(qapp, df)
    assert table is not None and table.is_linked

    heard = []
    service.subscribe('someone.else', lambda s: heard.append(s.entity_ids[0]))

    table.view.selectRow(0)          # the first row as displayed

    assert heard == [_entity(df, 3)], (
        f"the table emitted {heard}, not the name of the object in row 0")


def test_the_SELECTION_SURVIVES_A_SORT(qapp):
    """**The payoff.** Sort the table and row 0 is a different object. A position-keyed link
    highlights whatever moved there; an identity-keyed one follows the object."""
    df = _stamped(order=(3, 1, 2))
    table, service = _table(qapp, df)

    cell_3 = _entity(df, 3)

    # Select cell 3 — it is at view row 0 while unsorted.
    table.view.selectRow(0)
    assert table.selected_entity_id() == cell_3

    # Now sort by label ascending: 1, 2, 3 — cell 3 moves to the LAST row.
    table.view.sortByColumn(0, 0)     # Qt.AscendingOrder

    # A position-keyed table would now report the object at row 0 (cell 1).
    assert table.selected_entity_id() == cell_3, (
        "after sorting, the selection reports a DIFFERENT object — the link is keyed on row "
        "position, which is exactly the bug the entity id exists to fix")


def test_an_INBOUND_selection_finds_the_right_row_after_a_sort(qapp):
    """The other direction: a plot selects an object, and the table must highlight that object's
    row wherever the current sort has put it."""
    df = _stamped(order=(3, 1, 2))
    table, service = _table(qapp, df)
    cell_3 = _entity(df, 3)

    table.view.sortByColumn(0, 0)     # ascending by label -> 1, 2, 3

    from pycat.utils.selection_service import Selection
    service.select(Selection(entity_ids=(cell_3,), primary_id=cell_3,
                             source_view='a.plot', generation=1))

    assert table.selected_entity_id() == cell_3, (
        "the table highlighted the wrong row for an inbound selection after a sort")
    # ...and it is genuinely the LAST view row now, not row 0.
    selected = table.view.selectionModel().selectedRows()[0].row()
    assert selected == 2, f"expected cell 3 at view row 2 after an ascending sort, got {selected}"


def test_the_table_does_not_ECHO_its_own_selection_back(qapp):
    """It emits on click and highlights on inbound — the two must not chase each other."""
    df = _stamped()
    table, service = _table(qapp, df)

    emitted = []
    service.subscribe('watcher', lambda s: emitted.append(s.entity_ids[0]))

    table.view.selectRow(0)
    table.view.selectRow(1)

    assert len(emitted) == 2, f"a click emitted {len(emitted)} selections — it is echoing"


def test_a_table_WITHOUT_entity_ids_still_works_and_SAYS_it_is_by_position(qapp):
    """A legacy producer, or `measure_region_props` where the user did not tick `label`. Degraded
    and labelled beats silently wrong."""
    from pycat.utils.entity_ref import LINKED_BY_POSITION

    df = pd.DataFrame({'area': [1.0, 2.0, 3.0]})       # no identity
    table, _service = _table(qapp, df)

    assert table is not None, "an unlinkable table must still be a table"
    assert not table.is_linked
    assert table.linkability == LINKED_BY_POSITION
    assert table.selected_entity_id() is None

    table.view.selectRow(0)                             # must not raise


def test_the_row_map_is_keyed_on_SOURCE_rows_which_a_sort_does_not_move(qapp):
    """The difference from `row_for_id`: a VISUAL row index is a fact about the current sort order,
    so storing one is storing the thing that is about to change."""
    from pycat.ui.brushable_table import entity_row_map

    df = _stamped(order=(3, 1, 2))
    mapping = entity_row_map(df)

    assert mapping[_entity(df, 3)] == 0      # cell 3 is source row 0, whatever the view shows
    assert mapping[_entity(df, 1)] == 1
    assert len(mapping) == 3

    assert entity_row_map(pd.DataFrame({'area': [1.0]})) == {}


def test_detaching_stops_the_dispatcher_driving_a_dead_table(qapp):
    """Its dialog closed. The same lifecycle discipline VPT's table registry uses."""
    df = _stamped()
    table, service = _table(qapp, df)
    cell_1 = _entity(df, 1)

    table.detach()

    from pycat.utils.selection_service import Selection
    service.select(Selection(entity_ids=(cell_1,), primary_id=cell_1,
                             source_view='a.plot', generation=1))
    assert table.view.selectionModel().selectedRows() == [], (
        "a detached table was still being driven")


def test_the_identity_columns_are_NOT_shown_to_the_user(qapp):
    """**A regression shipped in 1.6.74.** The `_pycat_*` columns were introduced with a comment
    calling them hidden and nothing that hid them: `DataFrameModel` renders every column a df has,
    so every results dialog listed `_pycat_entity_id` for two versions.

    A doc comment is not a mechanism.
    """
    from pycat.ui.ui_utils import DataFrameModel

    df = _stamped()
    model = DataFrameModel(df)
    shown = [model.headerData(c, 1, 0) for c in range(model.columnCount())]

    assert shown == ['label', 'area'], f"identity columns leaked into the results table: {shown}"
    # ...while the DataFrame itself keeps them, because the machinery needs them.
    assert '_pycat_entity_id' in df.columns


def test_the_identity_columns_are_NOT_exported_to_CSV(tmp_path):
    """Same regression, worse blast radius: a scientific results CSV landing in someone's
    spreadsheet with a `_pycat_entity_id` column. Nothing reads them back — session restore goes
    through the manifest — so exporting them buys nothing."""
    from pycat.utils.entity_ref import without_identity

    df = _stamped()
    out = tmp_path / "results.csv"
    without_identity(df).to_csv(out, index=True)

    header = out.read_text(encoding='utf-8').splitlines()[0]
    assert '_pycat' not in header, f"identity columns were exported: {header}"
    assert 'label' in header and 'area' in header


def test_a_plot_axis_is_never_offered_an_identity_column():
    """The other half of the same regression: `_pycat_entity_id` is a name, not a number — plotting
    it against area is meaningless, and offering it invites the question of what it measures."""
    from pycat.utils.entity_ref import visible_columns

    df = _stamped()
    assert visible_columns(df) == ['label', 'area']
