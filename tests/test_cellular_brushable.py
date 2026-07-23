"""**Cellular fluorescence: two interleaved brushing tiers (cell + condensate) over one image.**

Phase 3 of the brushable-results-workspace spec. Two things: (1) `puncta_analysis_func` now paints a
GLOBALLY-unique per-punctum labels array (the cell-labeled mask cannot key a click to a punctum) and stamps
a `global_punctum_label` column mapping each row to it; (2) `mount_cellular_workspace` builds the two-tier
panel — Csat + dilute plots and cell/condensate tables, with the cell-labels and per-punctum layers as two
independent image tiers.
"""
import types

import numpy as np
import pandas as pd
import pytest

from pycat.utils.entity_ref import ENTITY_ID_COLUMN, finalize_entity_table
from pycat.utils.selection_service import SelectionService
from pycat.toolbox.feature_analysis_tools import puncta_analysis_func, mount_cellular_workspace

pytestmark = pytest.mark.core


class _FakeDI:
    def __init__(self, cell_df):
        self.data_repository = {'microns_per_pixel_sq': 0.01, 'microns_per_pixel': 0.1,
                                'cell_df': cell_df, 'file_path': 'cellular_test.tif'}

    def get_data(self, key, default=None):
        return self.data_repository.get(key, default)

    def set_data(self, key, value):
        self.data_repository[key] = value


def test_puncta_analysis_paints_a_globally_unique_perpunctum_layer():
    labeled_cells = np.zeros((40, 40), dtype=np.int32)
    labeled_cells[2:18, 2:18] = 1
    labeled_cells[22:38, 22:38] = 2
    puncta = np.zeros((40, 40), dtype=bool)
    puncta[4:7, 4:7] = True         # cell 1 punctum A
    puncta[10:13, 10:13] = True     # cell 1 punctum B
    puncta[26:30, 26:30] = True     # cell 2 punctum
    image = np.full((40, 40), 100.0)
    image[puncta] = 500.0

    di = _FakeDI(pd.DataFrame({'label': [1, 2]}))
    puncta_analysis_func(puncta, image, labeled_cells, di)

    glob = di.data_repository['puncta_labels_global']
    pdf = di.data_repository['puncta_df']

    assert set(np.unique(glob).tolist()) == {0, 1, 2, 3}          # 3 distinct puncta + background
    assert sorted(pdf['global_punctum_label']) == [1, 2, 3]        # one global label per punctum...
    assert pdf['global_punctum_label'].nunique() == 3
    # ...while the per-cell 'label' restarts in each cell (why the global one is needed)
    assert sorted(pdf.loc[pdf['cell label'] == 1, 'label']) == [1, 2]
    assert sorted(pdf.loc[pdf['cell label'] == 2, 'label']) == [1]
    # the layer's labels ARE the global punctum labels (a click on a pixel maps to the right row)
    for g in pdf['global_punctum_label']:
        assert int(g) in np.unique(glob)
    assert ENTITY_ID_COLUMN in pdf.columns and pdf[ENTITY_ID_COLUMN].nunique() == 3


# ── the two-tier mount ──────────────────────────────────────────────────────────────────────────────
def _fake_layer():
    return types.SimpleNamespace(name='layer', metadata={'pycat_layer_id': 'LID'},
                                 mouse_drag_callbacks=[], get_value=lambda position, world=True: 0)


def _cell_df():
    df = pd.DataFrame({
        'label': [1, 2],
        'intensity_total': [10.0, 20.0], 'puncta_intensity_total': [1.0, 4.0],
        'cell_xor_puncta_int_total': [9.0, 16.0],
        'bbox_y0': [2, 22], 'bbox_x0': [2, 22], 'bbox_y1': [18, 38], 'bbox_x1': [18, 38]})
    return finalize_entity_table(df, 'cell_analysis', source_path='cellular_test.tif')


def _puncta_df():
    df = pd.DataFrame({
        'label': [1, 2, 1], 'cell label': [1, 1, 2], 'global_punctum_label': [1, 2, 3],
        'circularity': [0.8, 0.9, 0.7], 'micron area': [0.1, 0.2, 0.15],
        'bbox_y0': [4, 10, 26], 'bbox_x0': [4, 10, 26], 'bbox_y1': [7, 13, 30], 'bbox_x1': [7, 13, 30]})
    return finalize_entity_table(df, 'puncta_analysis', source_path='cellular_test.tif')


def _fake_viewer(with_layers=True):
    layers = ({'Labeled Cell Mask': _fake_layer(), 'Condensate Labels': _fake_layer()}
              if with_layers else {})
    return types.SimpleNamespace(
        layers=layers, mouse_drag_callbacks=[],
        window=types.SimpleNamespace(add_dock_widget=lambda w, name=None, area=None: object()))


def _fake_cm(repo):
    return types.SimpleNamespace(
        selection=SelectionService(defer=lambda fn: fn()),
        active_data_class=types.SimpleNamespace(data_repository=repo))


def test_session_load_remounts_the_cellular_panel(qtbot):
    """A reloaded cellular session (cell table + cell-labels layer restored) re-opens the brushable panel,
    so it brushes exactly like a fresh analysis — no persisted panel needed."""
    from pycat.file_io.session_loader import _remount_brushable_panel
    cm = _fake_cm({'cell_df': _cell_df(), 'puncta_df': _puncta_df(), 'file_path': 't.tif'})
    assert _remount_brushable_panel(_fake_viewer(), cm) is True
    assert 'cell.table' in cm.selection._subscribers


def test_a_session_without_a_cell_table_does_not_remount(qtbot):
    from pycat.file_io.session_loader import _remount_brushable_panel
    assert _remount_brushable_panel(_fake_viewer(with_layers=False), _fake_cm({})) is False


def test_mount_cellular_workspace_wires_both_tiers(qtbot):
    service = SelectionService(defer=lambda fn: fn())
    cell_df, puncta_df = _cell_df(), _puncta_df()

    viewer = types.SimpleNamespace(
        layers={'Labeled Cell Mask': _fake_layer(), 'Condensate Labels': _fake_layer()},
        window=types.SimpleNamespace(add_dock_widget=lambda w, name=None, area=None: object()))
    cm = types.SimpleNamespace(
        selection=service,
        active_data_class=types.SimpleNamespace(
            data_repository={'cell_df': cell_df, 'puncta_df': puncta_df, 'file_path': 'cellular_test.tif'}))

    ws = mount_cellular_workspace(viewer, cm)
    assert ws is not None
    for vid in ('cell.plot.csat', 'cell.plot.dilute', 'cell.table', 'condensate.table',
                'cell.image', 'condensate.image'):
        assert vid in service._subscribers, f"{vid} was not wired"

    # a CELL selection lights the cell table but not the condensate table (independent tiers)
    cell_eid = cell_df[ENTITY_ID_COLUMN].iloc[0]
    service.select_entity(cell_eid, source='__external__')
    cell_table = next(v for v in ws._views if getattr(v, 'view_id', '') == 'cell.table')
    cond_table = next(v for v in ws._views if getattr(v, 'view_id', '') == 'condensate.table')
    assert cell_table.selected_entity_id() == cell_eid
    assert cell_eid not in cond_table._rows                    # the condensate tier holds no cell id

    # a CONDENSATE selection lights the condensate table
    cond_eid = puncta_df[ENTITY_ID_COLUMN].iloc[2]
    service.select_entity(cond_eid, source='__external__')
    assert cond_table.selected_entity_id() == cond_eid
    ws.detach()
