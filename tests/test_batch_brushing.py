"""**A finished batch produces brushable plots+tables whose points pull up the originating image.**

Phase 4 of the brushable-results-workspace spec. `assemble_batch_object_tables` reads a batch's per-image
`<stem>_cell_df.csv` (which keep the bbox + entity id) and tags each row with its source image, so a row's
`ObjectRef` is resolvable OFFLINE (source path + bbox). `mount_batch_workspace` puts those tables into the
workspace with a `BatchCropView` that opens the source file and slices the crop on selection — no session,
no re-segmentation.
"""
import types

import numpy as np
import pandas as pd
import pytest

from pycat.utils.entity_ref import ENTITY_ID_COLUMN
from pycat.utils.selection_service import SelectionService
from pycat.utils.batch_brushing import (SOURCE_PATH_COLUMN, assemble_batch_object_tables,
                                        mount_batch_workspace)

pytestmark = pytest.mark.core

_BBOX = (5, 5, 11, 11)          # y0, x0, y1, x1 of the bright square in each synthetic image


def _make_batch(tmp_path):
    import tifffile
    out = tmp_path / 'batch_out'
    src_dir = tmp_path / 'inputs'
    out.mkdir()
    src_dir.mkdir()
    source_paths = []
    for i, stem in enumerate(['imgA', 'imgB']):
        img = np.zeros((32, 32), dtype=np.uint16)
        y0, x0, y1, x1 = _BBOX
        img[y0:y1, x0:x1] = 1000 + i * 100                 # a bright square at a known bbox
        src = src_dir / f'{stem}.tif'
        tifffile.imwrite(str(src), img)
        source_paths.append(src)

        cell = pd.DataFrame({
            'label': [1], 'intensity_total': [10.0 + i], 'puncta_intensity_total': [1.0 + i],
            'cell_xor_puncta_int_total': [9.0],
            'bbox_y0': [y0], 'bbox_x0': [x0], 'bbox_y1': [y1], 'bbox_x1': [x1],
            ENTITY_ID_COLUMN: [f'ds{i}/op/cell/0/1'], '_pycat_layer_id': ['L']})
        (out / stem).mkdir()
        cell.to_csv(out / stem / f'{stem}_cell_df.csv', index=False)
    return out, source_paths


def test_assemble_tags_each_row_with_its_source_image(tmp_path):
    out, srcs = _make_batch(tmp_path)
    tables = assemble_batch_object_tables(out, srcs)
    cell = tables['cell']
    assert len(cell) == 2                                   # one object per image
    assert set(cell[SOURCE_PATH_COLUMN]) == {str(srcs[0]), str(srcs[1])}
    assert cell[ENTITY_ID_COLUMN].nunique() == 2


def test_a_batch_row_resolves_to_its_image_offline(tmp_path):
    out, srcs = _make_batch(tmp_path)
    cell = assemble_batch_object_tables(out, srcs)['cell']
    from pycat.utils.object_ref import ObjectRef
    from pycat.utils.brushing import crop_for_ref

    row = cell.iloc[0]
    ref = ObjectRef.from_row(row, source_path=row[SOURCE_PATH_COLUMN])
    assert ref.is_resolvable_offline()                     # source path + bbox present

    crop, _message = crop_for_ref(ref, viewer=None)        # no session, no viewer — opens the file
    assert crop is not None
    assert np.asarray(crop).max() >= 1000                  # the crop contains the object's bright square


def test_mount_batch_workspace_pulls_up_the_image_on_selection(tmp_path, qtbot):
    out, srcs = _make_batch(tmp_path)
    service = SelectionService(defer=lambda fn: fn())
    cm = types.SimpleNamespace(selection=service)

    ws = mount_batch_workspace(out, srcs, cm)
    assert ws is not None
    qtbot.addWidget(ws)

    cell = assemble_batch_object_tables(out, srcs)['cell']
    eid = cell[ENTITY_ID_COLUMN].iloc[1]                    # select the object from the SECOND image
    service.select_entity(eid, source='__external__')

    crop_view = next(v for v in ws._views if getattr(v, 'view_id', '') == 'batch.cell.crop')
    assert crop_view.last_crop is not None                  # its image was pulled offline
    assert np.asarray(crop_view.last_crop).max() >= 1100     # image B's square is 1100
    ws.detach()
