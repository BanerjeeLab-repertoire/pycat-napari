"""**VPT's per-track table survives a sort, and VPT uses the one key** (Completion Spec F4).

The table was a ``QTableWidget`` driven through ``row_for_id = {track_id: visual row}``: sort it and every
highlight lands on whatever track now sits at that row, silently. And VPT emitted
``{raw path}/vpt/track/-/{tid}`` while the declared ``vpt_tracks`` spec produces
``{dataset uuid}/vpt_tracks/bead/-/{tid}`` -- three of four segments differed, and a raw-path prefix is never
cleared by ``invalidate_dataset``. The table is now a ``BrushableTable`` stamped through the spec.
"""
from types import SimpleNamespace

import pandas as pd
import pytest

pytestmark = pytest.mark.integration


def _hub(service, source):
    from pycat.toolbox.vpt_ui import VideoParticleTrackingUI

    class _Hub(VideoParticleTrackingUI):
        def __init__(self):
            self.viewer = None
            self.central_manager = SimpleNamespace(
                selection=service, active_data_class=SimpleNamespace(data_repository={'file_path': source}))
            self.revealed = []

        def _reveal_track_in_viewer(self, tid):
            self.revealed.append(tid)

        def _highlight_track_in_plot(self, tid):
            pass

    return _Hub()


def _metrics():
    return pd.DataFrame({'track_id': [3, 7, 11, 20], 'D_um2_s': [0.4, 0.1, 0.9, 0.2]})


def test_vpt_emits_the_declared_spec_id_over_the_dataset_uuid(tmp_path):
    from pycat.utils.entity_ref import dataset_id_for
    from pycat.utils.selection_service import SelectionService
    src = tmp_path / 'beads.tif'
    src.write_bytes(b'x')
    hub = _hub(SelectionService(defer=lambda fn: fn()), str(src))
    assert hub._track_entity_id(7) == f'{dataset_id_for(str(src))}/vpt_tracks/bead/-/7'


def test_a_sorted_track_table_still_highlights_the_right_track(qtbot, tmp_path):
    from qtpy.QtCore import Qt
    from pycat.utils.selection_service import SelectionService
    src = tmp_path / 'beads.tif'
    src.write_bytes(b'x')
    service = SelectionService(defer=lambda fn: fn())
    hub = _hub(service, str(src))
    view, brushable = hub._vpt_track_table(_metrics(), 'vpt.track_table')
    qtbot.addWidget(view)
    assert brushable is not None and brushable.is_linked

    view.sortByColumn(1, Qt.DescendingOrder)              # by D: 11, 3, 20, 7 -- every track moves
    hub._select_track(7, source='plot')
    assert brushable.selected_entity_id() == hub._track_entity_id(7)
    row = view.selectionModel().selectedRows()[0].row()
    shown = view.model().index(row, 0).data()
    assert str(shown) == '7', f'the highlighted row shows track {shown}, not 7'


def test_a_row_click_reaches_the_other_vpt_views(qtbot, tmp_path):
    from qtpy.QtCore import Qt
    from pycat.utils.selection_service import SelectionService
    src = tmp_path / 'beads.tif'
    src.write_bytes(b'x')
    service = SelectionService(defer=lambda fn: fn())
    hub = _hub(service, str(src))
    hub._ensure_selection_views()
    view, brushable = hub._vpt_track_table(_metrics(), 'vpt.track_table')
    qtbot.addWidget(view)
    view.sortByColumn(1, Qt.DescendingOrder)
    view.selectRow(0)                                     # after the sort, row 0 is track 11
    assert hub.revealed[-1] == 11 and hub._selected_track_id == 11

