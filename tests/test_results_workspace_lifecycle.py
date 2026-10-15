"""**A results workspace leaves the bus when its dock goes, and a re-run replaces it** (Completion Spec F6).

Nothing in production called ``BrushableWorkspace.detach()``: the cellular and in-vitro workspaces were stored as
``(dock, ws)`` "to detach on close" with no close handler. A closed results dock kept its views subscribed, and
every re-run appended another pick handler to ``viewer.mouse_drag_callbacks`` that was never removed.
"""
from types import SimpleNamespace

import pandas as pd
import pytest

from pycat.utils.entity_ref import ENTITY_ID_COLUMN
from pycat.utils.selection_service import SelectionService

pytestmark = pytest.mark.integration


class _Window:
    def __init__(self):
        self.docks, self.removed = [], []

    def add_dock_widget(self, widget, name=None, area=None, tabify=False):
        dock = SimpleNamespace(widget=widget, name=name)
        self.docks.append(dock)
        return dock

    def remove_dock_widget(self, dock):
        self.removed.append(dock)


def _workspace(service, viewer):
    from pycat.ui.brushable_workspace import BrushableWorkspace
    df = pd.DataFrame({'label': [1, 2], 'area': [1.0, 2.0], ENTITY_ID_COLUMN: ['d/op/cell/-/1', 'd/op/cell/-/2']})
    ws = BrushableWorkspace(service)
    ws.add_table(df, 'cell.table')
    ws._install_viewer_pick(viewer)
    return ws


def test_a_rerun_detaches_the_previous_workspace_and_removes_its_dock(qtbot):
    from pycat.utils.dock_space import mount_results_workspace
    service = SelectionService(defer=lambda fn: fn())
    viewer = SimpleNamespace(mouse_drag_callbacks=[])
    window, holder = _Window(), SimpleNamespace()
    first = _workspace(service, viewer)
    qtbot.addWidget(first)
    mount_results_workspace(holder, '_dock', window, first, name='Results')
    second = _workspace(service, viewer)
    qtbot.addWidget(second)
    mount_results_workspace(holder, '_dock', window, second, name='Results')
    assert len(viewer.mouse_drag_callbacks) == 1, 'every re-run used to leave another pick handler behind'
    assert window.removed == [window.docks[0]]
    assert holder._dock == (window.docks[1], second)
    assert first._views == [] and 'cell.table' in service._subscribers


def test_destroying_the_results_widget_detaches_it(qtbot):
    from qtpy.QtCore import QCoreApplication, QEvent
    from pycat.utils.dock_space import add_results_dock
    service = SelectionService(defer=lambda fn: fn())
    viewer = SimpleNamespace(mouse_drag_callbacks=[])
    ws = _workspace(service, viewer)
    add_results_dock(_Window(), ws, name='Results', settings=SimpleNamespace(get=lambda *a, **k: None))
    assert 'cell.table' in service._subscribers and viewer.mouse_drag_callbacks
    ws.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert 'cell.table' not in service._subscribers
    assert viewer.mouse_drag_callbacks == []
