"""**A results table that still means the right object after you sort it.**

── The bug this closes ─────────────────────────────────────────────────────────────────────

Linked tables have been keyed on **row position**. VPT's is the honest example: it builds
``row_for_id = {track_id: r}`` where ``r`` is the **visual row index**, and drives the highlight with
``table.selectRow(row_for_id[tid])``. That map is correct exactly until the view is sorted — after
which every highlight lands on whatever row happens to sit at that position. *Nothing looks broken:
a row is selected, it just isn't the one you asked for.*

This is what increments 1–4 were building toward. The table now keys on the **entity id** — the
stable name stamped at the table-building chokepoint (increment 2) — and sorting is done by a
``QSortFilterProxyModel`` that reorders the *view* while the identity stays on the *source row*. So
the answer to "which object is this row?" no longer depends on how the user chose to look at it.

── What it plugs into ──────────────────────────────────────────────────────────────────────

Selection goes out to, and comes back from, the one ``SelectionService`` (increment 3) — the same
dispatcher VPT's three-way link and every other plot use. There is no second selection path.

A table **without** entity ids (a legacy producer, or `measure_region_props` where the user did not
select `label`) still works exactly as it does today, by position — and says so, via increment 2's
``linkability_of``. Degraded and labelled beats silently wrong.
"""

from __future__ import annotations

from PyQt5.QtCore import QSortFilterProxyModel, Qt
from PyQt5.QtWidgets import QAbstractItemView, QTableView

from pycat.utils.entity_ref import ENTITY_ID_COLUMN, has_entity_ids, linkability_of
from pycat.utils.general_utils import debug_log
from pycat.utils.selection_service import Selection


def entity_row_map(df):
    """``{entity_id: source row}``. **Source** rows — they do not move when the view sorts.

    This is the difference from `row_for_id`: a *visual* row index is a fact about the current sort
    order, and storing one is storing the thing that is about to change.
    """
    try:
        if not has_entity_ids(df):
            return {}
        return {str(value): row for row, value in enumerate(df[ENTITY_ID_COLUMN])}
    except Exception as exc:
        debug_log('brushable_table: could not map entity ids to rows', exc)
        return {}


class BrushableTable:
    """Wires a ``QTableView`` to the shared ``SelectionService``, keyed by entity id.

    Held by the caller (a dialog), which keeps it alive; ``detach()`` when the dialog closes so the
    dispatcher stops driving a dead widget — the same lifecycle discipline VPT's table registry uses.
    """

    def __init__(self, table_view, df, service, view_id, *, model=None):
        self.view = table_view
        self.df = df
        self.service = service
        self.view_id = str(view_id)
        self._rows = entity_row_map(df)
        self._ids = {row: eid for eid, row in self._rows.items()}
        self._applying = False

        self.proxy = QSortFilterProxyModel(table_view)
        if model is not None:
            self.proxy.setSourceModel(model)
        elif table_view.model() is not None:
            self.proxy.setSourceModel(table_view.model())
        table_view.setModel(self.proxy)
        table_view.setSortingEnabled(True)
        table_view.setSelectionBehavior(QAbstractItemView.SelectRows)

        if self.is_linked:
            self._connect()

    # ── state ─────────────────────────────────────────────────────────────────────────────
    @property
    def is_linked(self) -> bool:
        return bool(self._rows)

    @property
    def linkability(self) -> str:
        """The string to show the user. A table matched by position must SAY so."""
        return linkability_of(self.df)

    # ── outbound: the user clicked a row ──────────────────────────────────────────────────
    def _connect(self):
        try:
            self.view.selectionModel().selectionChanged.connect(self._on_selection_changed)
            self.service.subscribe(self.view_id, self._on_service_selection)
        except Exception as exc:
            debug_log('brushable_table: could not wire the table to the service', exc)

    def _on_selection_changed(self, *_args):
        if self._applying or self.service.is_busy:
            return
        eid = self.selected_entity_id()
        if eid is None:
            return
        self.service.select(Selection(
            entity_ids=(eid,), primary_id=eid, mode='selected',
            source_view=self.view_id, generation=self.service.next_generation()))

    def selected_entity_id(self):
        """The entity id of the selected row — resolved through the proxy, so it is the row the
        user actually clicked and not the one that used to be there."""
        try:
            indexes = self.view.selectionModel().selectedRows()
            if not indexes:
                return None
            source_row = self.proxy.mapToSource(indexes[0]).row()
            return self._ids.get(source_row)
        except Exception as exc:
            debug_log('brushable_table: could not read the selected row', exc)
            return None

    # ── inbound: something else selected ──────────────────────────────────────────────────
    def _on_service_selection(self, selection):
        row = None
        for eid in selection.entity_ids:
            row = self._rows.get(str(eid))
            if row is not None:
                break
        if row is None:
            return          # this table does not hold that object — say nothing, do nothing

        try:
            proxy_index = self.proxy.mapFromSource(self.proxy.sourceModel().index(row, 0))
            if not proxy_index.isValid():
                return      # filtered out of view
            # `_applying` on top of the service's own guard: `selectRow` emits synchronously, and
            # this handler runs *inside* the service's propagation.
            self._applying = True
            try:
                self.view.selectRow(proxy_index.row())
            finally:
                self._applying = False
        except Exception as exc:
            debug_log('brushable_table: could not highlight the selected row', exc)

    def reveal(self):
        """Scroll to the selection. **Only on an explicit reveal** — scrolling on every hover is
        the "abrupt navigation" complaint."""
        try:
            indexes = self.view.selectionModel().selectedRows()
            if indexes:
                self.view.scrollTo(indexes[0], QAbstractItemView.PositionAtCenter)
        except Exception as exc:
            debug_log('brushable_table: could not scroll to the selection', exc)

    def detach(self):
        """Stop the dispatcher driving this table (its dialog closed)."""
        try:
            self.service.unsubscribe(self.view_id)
        except Exception as exc:
            debug_log('brushable_table: could not detach', exc)


def make_brushable(table_view, df, service, view_id, *, model=None):
    """Wire ``table_view`` to ``service``. Returns the `BrushableTable`, or None if it cannot be.

    Never raises: a results table is the user's data, and a brushing failure must not cost them the
    numbers.
    """
    if service is None or table_view is None:
        return None
    try:
        return BrushableTable(table_view, df, service, view_id, model=model)
    except Exception as exc:
        debug_log('brushable_table: could not make the table brushable', exc)
        return None


def sorted_table_view(df, model):
    """A ``QTableView`` over ``model`` with sorting enabled — the plain, unlinked case."""
    view = QTableView()
    view.setModel(model)
    view.setSortingEnabled(True)
    view.setSelectionBehavior(QAbstractItemView.SelectRows)
    view.setEditTriggers(QAbstractItemView.NoEditTriggers)
    view.sortByColumn(-1, Qt.AscendingOrder)
    return view
