"""VPT UI — track-table methods (per-track table build, row<->entity, table selection callback), extracted from vpt_ui.py (behaviour-preserving move).

A mixin so ``vpt_ui.py`` composes it instead of implementing it. Bodies are UNCHANGED; they use
``self`` (resolved by the composed class) and the imports below (copied verbatim from vpt_ui).
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


class _VptTableMixin:
    """VPT track-table methods. Mixed into ``VideoParticleTrackingUI``."""

    def _on_selection_table(self, selection):
        tid = self._track_of(selection)
        if tid is None:
            return
        try:
            self._highlight_track_in_table(tid)
        except Exception as e:
            print(f"[PyCAT VPT] link→table failed: {e}")

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
