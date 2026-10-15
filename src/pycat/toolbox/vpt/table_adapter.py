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
from qtpy.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QGroupBox, QFormLayout,
    QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QProgressBar,
    QScrollArea, QSizePolicy, QRadioButton, QComboBox, QLineEdit,
)
from qtpy.QtCore import QThread, Signal, Qt


class _VptTableMixin:
    """VPT track-table methods. Mixed into ``VideoParticleTrackingUI``."""

    def _on_selection_table(self, selection):
        tid = self._track_of(selection)
        if tid is None:
            return
        # A row click in the brushable table selects through the service directly (its own view id), so record
        # the track here too -- paging re-highlights from `_selected_track_id`.
        self._selected_track_id = tid
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
        else:
            # Part 3: bring the plots to the selected track's bucket if it is off the current page, so the
            # selection lands among its neighbours (page-to-selection). An on-page pick does not move the view.
            page_to = getattr(self, '_vpt_page_to_selected_track', None)
            if callable(page_to):
                try:
                    page_to(tid)
                except Exception:                   # broad-ok: ui_cleanup — paging-to-selection is a convenience, never gating
                    pass

    def _highlight_track_in_table(self, track_id):
        """Nothing to drive by hand any more: the per-track table is a ``BrushableTable`` subscribed to the
        service itself, keyed on ``_pycat_entity_id`` through a sort proxy. The old ``row_for_id`` map stored the
        VISUAL row, so a sorted table highlighted the wrong track, silently (Completion Spec F4). Kept as the
        dispatcher's table hook."""
        return None

    def _vpt_source(self):
        try:
            from pycat.utils.entity_ref import source_path_of
            return source_path_of(self.central_manager.active_data_class)
        except Exception:                            # broad-ok: optional_probe — no dataset → 'unknown' dataset id
            return None

    def _vpt_track_table(self, per_track_metrics, view_id):
        """The per-track results as a sort-safe ``BrushableTable`` on VPT's selection service: the table is
        stamped through the declared ``vpt_tracks`` spec, so a row's id equals ``_track_entity_id(tid)`` and a
        selection from any view lands on the right row however the table is sorted. Returns
        ``(QTableView, BrushableTable or None)``; registers ``_track_table_registry`` for teardown."""
        from pycat.ui.brushable_table import make_brushable
        from pycat.ui.ui_utils import create_table_view
        from pycat.utils.entity_ref import finalize_entity_table, without_identity
        df = finalize_entity_table(per_track_metrics.copy(), 'vpt_tracks', source_path=self._vpt_source())
        view = create_table_view(without_identity(df))
        brushable = make_brushable(view, df, self._ensure_selection_views(), view_id)
        old = getattr(self, '_track_table_registry', None) or {}
        if old.get('brushable') is not None:
            old['brushable'].close()
        self._track_table_registry = {'table': view, 'brushable': brushable}
        return view, brushable

    def _show_per_track_table(self, per_track_metrics):
        """Non-modal per-track results table. Clicking a row selects that track everywhere (row → plot curve +
        image bead) through the selection service, and a selection from elsewhere selects its row here —
        keyed on identity, so sorting the table never misroutes a highlight (Completion Spec F4)."""
        try:
            from qtpy.QtWidgets import QDialog, QVBoxLayout, QLabel
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
        table, brushable = self._vpt_track_table(per_track_metrics, 'vpt.track_table_dialog')
        v.addWidget(table)

        # Detach from the service when the window closes, so the dispatcher stops driving a dead widget.
        def _closeEvent(ev):
            try:
                if brushable is not None:
                    brushable.close()
                reg = getattr(self, '_track_table_registry', None)
                if reg and reg.get('table') is table:
                    self._track_table_registry = None
            except Exception:                        # broad-ok: ui_cleanup — teardown is best-effort
                pass
            ev.accept()
        dlg.closeEvent = _closeEvent

        dlg.setModal(False)
        dlg.show()
        self._per_track_dialog = dlg  # keep a ref so it isn't GC'd
