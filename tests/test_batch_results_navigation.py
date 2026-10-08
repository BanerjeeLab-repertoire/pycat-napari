"""**Batch results are linked to the images:** a selected cell or condensate from any image opens THAT image
in the viewer, centred and outlined; a click on an object in the image selects it back; ids are unique
across images; and each condensate carries its own and its cell's total intensity for plotting."""
import numpy as np
import pandas as pd
import pytest

from pycat.utils.entity_ref import ENTITY_ID_COLUMN
from pycat.utils.selection_service import Selection, SelectionService
from pycat.utils.batch_brushing import (SOURCE_PATH_COLUMN, assemble_batch_object_tables,
                                        counted_condensates, with_cell_context)

# base (headless) except the widget test, which is integration (qtbot) like test_batch_brushing's.


def _batch(tmp_path):
    """Two images' batch outputs: each one cell (label 1) holding two condensates (global labels 1, 2)."""
    import tifffile
    out, srcs = tmp_path / 'out', []
    for i, stem in enumerate(['imgA', 'imgB']):
        d = out / stem
        d.mkdir(parents=True)
        srcs.append(tmp_path / f'{stem}.tif')
        img = np.full((40, 40), 10.0, np.float32)
        cells = np.zeros((40, 40), np.uint16)
        cells[5:35, 5:35] = 1
        cond = np.zeros((40, 40), np.uint32)
        cond[10:14, 10:14] = 1
        cond[20:26, 22:28] = 2
        img[cond > 0] = 100 + i
        for name, a in (('measured_image', img), ('labeled_cells', cells), ('condensate_labels', cond)):
            tifffile.imwrite(str(d / f'{stem}_{name}.tiff'), a)
        pd.DataFrame({'label': [1], 'intensity_total': [5000.0 + i], 'number_of_puncta': [2],
                      'bbox_y0': [5], 'bbox_x0': [5], 'bbox_y1': [35], 'bbox_x1': [35],
                      ENTITY_ID_COLUMN: [f'ds{i}/cell_analysis/cell/-/1']}).to_csv(d / f'{stem}_cell_df.csv', index=False)
        pd.DataFrame({'label': [1, 2, -1], 'cell label': [1, 1, 1], 'global_punctum_label': [1, 2, 0],
                      'area': [16, 36, 4], 'intensity_mean': [100.0 + i, 100.0 + i, 12.0],
                      'bbox_y0': [10, 20, 30], 'bbox_x0': [10, 22, 30], 'bbox_y1': [14, 26, 32], 'bbox_x1': [14, 28, 32],
                      'below_contrast_floor': [False, False, True],
                      ENTITY_ID_COLUMN: [f'ds{i}/puncta_analysis/punctum/-/1/{k}' for k in (1, 2, -1)]}
                     ).to_csv(d / f'{stem}_puncta_df.csv', index=False)
    return out, srcs


class _Layer:
    def __init__(self, data, name):
        self.data, self.name, self.visible = np.asarray(data), name, True

    def get_value(self, position, world=True):
        y, x = (int(round(v)) for v in position[-2:])
        return self.data[y, x]


class _Viewer:
    """The slice of a napari viewer the navigator uses (no OpenGL needed)."""
    def __init__(self):
        self.layers = {}
        self.camera = type('C', (), {'center': (0, 0), 'zoom': 1.0})()
        self.mouse_drag_callbacks = []

    def add_image(self, data, name, **kw):
        self.layers[name] = _Layer(data, name)
        return self.layers[name]

    add_labels = add_image

    def add_shapes(self, data, name, **kw):
        self.layers[name] = _Layer(np.asarray(data), name)
        return self.layers[name]


def _navigator(tmp_path):
    from pycat.ui.batch_navigator import BatchImageNavigator
    out, srcs = _batch(tmp_path)
    tables = assemble_batch_object_tables(out, srcs)
    service = SelectionService(defer=lambda fn: fn(), debounce=lambda fn: fn())   # no event loop in tests
    viewer = _Viewer()
    nav = BatchImageNavigator(viewer, out, {'cell': tables['cell'],
                                            'condensate': with_cell_context(tables['puncta'], tables['cell'])},
                              service, 'batch.image')
    return nav, viewer, service, tables


def _select(service, eid):
    service.select(Selection(entity_ids=(eid,), primary_id=eid, mode='selected', source_view='test',
                             generation=service.next_generation()))


@pytest.mark.base
def test_a_selected_condensate_opens_its_own_image_centred_and_outlined(tmp_path):
    from pycat.ui.batch_navigator import SELECTION_LAYER
    nav, viewer, service, _ = _navigator(tmp_path)
    _select(service, 'ds1/puncta_analysis/punctum/-/1/2')
    assert viewer.layers['Image [imgB]'].visible and nav.current_stem == 'imgB'
    rect = viewer.layers[SELECTION_LAYER].data[0]
    assert rect[:, 0].min() == 18 and rect[:, 1].max() == 30          # bbox (20,22)-(26,28) +/- 2 px
    assert tuple(viewer.camera.center) == (23.0, 25.0)


@pytest.mark.base
def test_selecting_another_image_shows_it_and_hides_the_first(tmp_path):
    nav, viewer, service, _ = _navigator(tmp_path)
    _select(service, 'ds1/cell_analysis/cell/-/1')
    _select(service, 'ds0/cell_analysis/cell/-/1')
    assert viewer.layers['Image [imgA]'].visible and not viewer.layers['Image [imgB]'].visible


@pytest.mark.base
def test_a_click_on_an_object_in_the_image_selects_it(tmp_path):
    nav, viewer, service, _ = _navigator(tmp_path)
    nav.show_image('imgA')
    assert nav.pick_at((22.0, 24.0)) == 'ds0/puncta_analysis/punctum/-/1/2'      # condensate wins
    assert nav.pick_at((30.0, 8.0)) == 'ds0/cell_analysis/cell/-/1'               # else the cell
    assert nav.pick_at((1.0, 1.0)) is None                                         # background


@pytest.mark.base
def test_each_condensate_carries_its_own_and_its_cells_total_intensity(tmp_path):
    out, srcs = _batch(tmp_path)
    t = assemble_batch_object_tables(out, srcs)
    p = with_cell_context(t['puncta'], t['cell'])
    a2 = p[(p[SOURCE_PATH_COLUMN] == str(srcs[0])) & (p['global_punctum_label'] == 2)].iloc[0]
    assert a2['condensate_intensity_total'] == 36 * 100.0 and a2['cell_intensity_total'] == 5000.0
    b1 = p[(p[SOURCE_PATH_COLUMN] == str(srcs[1])) & (p['global_punctum_label'] == 1)].iloc[0]
    assert b1['cell_intensity_total'] == 5001.0                    # joined on the image, not just the label
    assert len(counted_condensates(p)) == 4                         # the below-floor rows are not plotted


@pytest.mark.base
def test_batch_stamps_each_image_path_so_ids_are_unique_per_image(tmp_path):
    import tifffile
    from pycat.batch.steps.io_steps import replay_open_image
    paths = []
    for stem in ('a', 'b'):
        p = tmp_path / f'{stem}.tif'
        tifffile.imwrite(str(p), np.zeros((16, 16), np.uint16))
        paths.append(p)
    states = []
    for p in paths:
        st = {}
        replay_open_image(st, p, {'file_path': str(p)}, tmp_path)
        states.append(st['data_instance'].data_repository['file_path'])
    assert states == [str(paths[0]), str(paths[1])]


@pytest.mark.integration
def test_the_plot_builder_redraws_on_any_two_columns(qtbot):
    from pycat.ui.brushable_workspace import PlotBuilder
    df = pd.DataFrame({'a': [1.0, 2.0], 'b': [3.0, 4.0], 'c': [5, 6], ENTITY_ID_COLUMN: ['e1', 'e2']})
    pb = PlotBuilder({'T': df}, SelectionService(), 'builder')
    assert pb.numeric_columns('T') == ['a', 'b', 'c']
    pb.set_axes('T', 'c', 'a')
    assert (pb.plot.x_col, pb.plot.y_col) == ('c', 'a') and len(pb.plot._points) == 2
    pb.close()


# ── In-vitro droplets: the same linking, from the in-vitro batch ─────────────────────────────────────────

def _droplet_scene():
    mask = np.zeros((40, 40), np.int32)
    mask[5:11, 5:11] = 1
    mask[20:30, 18:28] = 2
    img = np.where(mask > 0, 200.0, 20.0).astype(np.float32)
    part = pd.DataFrame({'droplet_label': [1, 2], 'I_dense': [200.0, 210.0], 'partition_coefficient': [10.0, 10.5]})
    return mask, img, part


@pytest.mark.base
def test_the_droplet_table_is_brush_ready_the_same_way_for_panel_and_batch():
    from pycat.toolbox.invitro.partition import brush_ready_droplet_table
    mask, _img, part = _droplet_scene()
    before = part.copy()
    t = brush_ready_droplet_table(part, mask, 0.1, source_path='C:/x/image 1.tif')
    pd.testing.assert_frame_equal(part, before)                      # the caller's table is not touched
    assert list(t['bbox_y0']) == [5, 20] and list(t['bbox_x1']) == [11, 28]
    assert t['area_um2'].round(4).tolist() == [0.36, 1.0]
    assert t[ENTITY_ID_COLUMN].nunique() == 2 and list(t['label']) == [1, 2]


@pytest.mark.base
def test_the_in_vitro_batch_writes_what_the_results_dock_links(tmp_path):
    from pycat.batch.steps.invitro_steps import _write_brushable_droplets
    from pycat.data.data_modules import BaseDataClass
    from pycat.utils.consolidated_table import records_from_output_dir
    mask, img, part = _droplet_scene()
    di = BaseDataClass()
    di.data_repository['file_path'] = str(tmp_path / 'image 1.tif')
    _write_brushable_droplets({'data_instance': di}, tmp_path / 'image 1.tif', tmp_path, part, mask, img, 0.1)
    for name in ('droplet_df.csv', 'measured_image.tiff', 'droplet_labels.tiff'):
        assert (tmp_path / f'image 1_{name}').is_file()
    kinds = [k for k, _ in records_from_output_dir(tmp_path, 'image 1')]
    assert kinds == ['droplet']                                      # reaches the consolidated table too


@pytest.mark.base
def test_a_selected_droplet_opens_its_image_and_a_click_selects_it_back(tmp_path):
    import tifffile
    from pycat.toolbox.invitro.partition import brush_ready_droplet_table
    from pycat.ui.batch_navigator import BatchImageNavigator, SELECTION_LAYER
    mask, img, part = _droplet_scene()
    rows = []
    for stem in ('image 1', 'image 2'):
        d = tmp_path / stem
        d.mkdir()
        tifffile.imwrite(str(d / f'{stem}_measured_image.tiff'), img)
        tifffile.imwrite(str(d / f'{stem}_droplet_labels.tiff'), mask.astype(np.uint32))
        t = brush_ready_droplet_table(part, mask, 0.1, source_path=str(tmp_path / f'{stem}.tif'))
        t[SOURCE_PATH_COLUMN] = str(tmp_path / f'{stem}.tif')
        rows.append(t)
    droplets = pd.concat(rows, ignore_index=True)
    service = SelectionService(defer=lambda fn: fn(), debounce=lambda fn: fn())
    viewer = _Viewer()
    nav = BatchImageNavigator(viewer, tmp_path, {'droplet': droplets}, service, 'batch.image')
    eid = droplets[ENTITY_ID_COLUMN].iloc[3]                         # image 2, droplet 2
    _select(service, eid)
    assert nav.current_stem == 'image 2' and viewer.layers['Droplets [image 2]'].visible
    rect = viewer.layers[SELECTION_LAYER].data[0]
    assert rect[:, 0].min() == 18 and rect[:, 1].max() == 30
    assert nav.pick_at((7.0, 7.0)) == droplets[ENTITY_ID_COLUMN].iloc[2]   # image 2, droplet 1
