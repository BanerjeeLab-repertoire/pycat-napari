"""**Time-series results are linked like every other workflow's, plus the time axis.**

* A trace plot (one line per cell / droplet) is a view on the one ``SelectionService``: clicking a line selects
  that object everywhere and steps the viewer to the clicked frame; a selection made elsewhere highlights the
  object's line; the viewer's time slider drives a cursor on the plot.
* The linked panels build for the cellular and the in-vitro time series, and a trace click reaches the table.
* The in-vitro time series replays in batch (it recorded its steps from the start, and batch skipped them all)
  and writes the brushable tables the batch results read back.
"""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from pycat.utils.entity_ref import ENTITY_ID_COLUMN
from pycat.utils.selection_service import SelectionService


def _service():
    return SelectionService(defer=lambda fn: fn())


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def disconnect(self, fn):
        self.slots.remove(fn)

    def emit(self):
        for fn in list(self.slots):
            fn(None)


class _Dims:
    """napari's ``viewer.dims`` as far as a trace plot uses it: ``current_step``, and an event when it changes."""

    def __init__(self):
        object.__setattr__(self, 'events', SimpleNamespace(current_step=_Signal()))
        object.__setattr__(self, 'current_step', (0, 0, 0))

    def __setattr__(self, name, value):
        object.__setattr__(self, name, tuple(value) if name == 'current_step' else value)
        if name == 'current_step':
            self.events.current_step.emit()


def _traces():
    rows = []
    for cell, (expr, k) in {1: (10.0, 0.2), 2: (3.0, 0.5)}.items():
        for f in range(10):
            rows.append({'cell': cell, 'frame': f, 'time_s': 10.0 * f, 'expression': expr,
                         'condensed_fraction': 0.3 * np.exp(-k * f), ENTITY_ID_COLUMN: f'm/op/cell/-/{cell}'})
    return pd.DataFrame(rows)


def _click(view, eid, frame):
    view.canvas.draw()
    x, y = next((x, y) for e, x, y, f in view._points if e == eid and f == frame)
    px, py = view.ax.transData.transform((x, y))
    view._on_click(SimpleNamespace(inaxes=view.ax, x=px, y=py))


@pytest.mark.integration
def test_a_trace_click_selects_the_object_and_steps_the_viewer(qtbot):
    from pycat.toolbox.timeseries.results_view import CELL_TRACE_METRICS, TraceView
    service = _service()
    viewer = SimpleNamespace(dims=_Dims())
    tv = TraceView(_traces(), service, 'tv', group_col='cell', metrics=CELL_TRACE_METRICS, viewer=viewer,
                   color_col='expression')
    qtbot.addWidget(tv.widget)
    _click(tv, 'm/op/cell/-/2', 6)
    assert service.state.primary_id == 'm/op/cell/-/2'
    assert viewer.dims.current_step[0] == 6
    assert tv._highlight, 'the clicked line is highlighted'
    tv.close()


@pytest.mark.integration
def test_the_time_cursor_follows_the_viewer_and_inbound_selection_highlights(qtbot):
    from pycat.toolbox.timeseries.results_view import CELL_TRACE_METRICS, TraceView
    service = _service()
    viewer = SimpleNamespace(dims=_Dims())
    tv = TraceView(_traces(), service, 'tv', group_col='cell', metrics=CELL_TRACE_METRICS, viewer=viewer)
    qtbot.addWidget(tv.widget)
    viewer.dims.current_step = (4, 0, 0)
    assert list(tv._cursor.get_xdata()) == [40.0, 40.0]
    service.select_entity('m/op/cell/-/1', source='__external__')
    assert len(tv._highlight) == 1
    tv.close()
    assert not viewer.dims.events.current_step.slots, 'close() disconnects the cursor'


@pytest.fixture(scope='module')
def cell_result():
    from tests.test_cell_timeseries import _dissolving_movie
    from pycat.toolbox.timeseries.cell_pipeline import analyze_cell_timeseries
    stack, cells = _dissolving_movie(T=8)
    return analyze_cell_timeseries(stack, cells, 3, 40, frame_interval_s=10.0, n_workers=1), cells


@pytest.mark.integration
def test_the_cellular_linked_panel_builds_and_a_trace_click_reaches_the_table(qtbot, cell_result):
    from pycat.toolbox.timeseries.cell_pipeline import result_tables
    from pycat.toolbox.timeseries.results_view import build_workspace
    result, cells = cell_result
    service = _service()
    tables = result_tables(result, cells, 'movie.tif')
    ws = build_workspace(tables, service)
    qtbot.addWidget(ws)
    trace = next(v for v in ws._views if v.view_id == 'cellts:cell_traces')
    table = next(v for v in ws._views if getattr(v, 'view_id', '') == 'cellts:cells_table')
    eid = tables['cells'].loc[tables['cells']['cell'] == 1, ENTITY_ID_COLUMN].iloc[0]
    _click(trace, eid, 3)
    assert table.selected_entity_id() == eid
    ws.detach()


def _droplet_movie(T=14, seed=0):
    rng = np.random.default_rng(seed)
    H = W = 120
    yy, xx = np.mgrid[:H, :W]
    st = np.zeros((T, H, W), np.float32)
    for t in range(T):
        img = 100 + rng.normal(0, 3, (H, W))
        for i, (y, x) in enumerate([(30, 30), (80, 40), (60, 90)]):
            img += (200 + (15 + 10 * i) * t) * ((yy - y) ** 2 + (xx - x) ** 2 < 64)
        st[t] = img
    return st


@pytest.mark.base
def test_the_invitro_time_series_replays_in_batch_and_reads_back_linked(tmp_path):
    from pycat.batch_step_registry import _STEP_MAP
    from pycat.utils.batch_brushing import assemble_batch_object_tables, per_image_csvs
    stack = _droplet_movie()
    src = tmp_path / 'drops.tif'
    out = tmp_path / 'out' / 'drops'
    out.mkdir(parents=True)
    state = {'image': stack, 'data_instance': SimpleNamespace(data_repository={'microns_per_pixel_sq': 0.01,
                                                                               'file_path': str(src)})}
    steps = [('tsivf_segment_stack', {'method': 'Multi-Otsu', 'multiotsu_classes': 3, 'min_area_px': 20}),
             ('tsivf_link_condensates', {'measure_layers': ['drops C0-488 Stack'], 'min_track_length': 3}),
             ('tsivf_object_trajectories', {'frame_interval_s': 10.0}),
             ('tsivf_field_trajectories', {'frame_interval_s': 10.0}),
             ('tsivf_kinetics', {'channel': 'C0-488', 'min_frames': 5, 'smooth_frames': 3, 'frame_interval_s': 10.0})]
    for name, params in steps:
        _STEP_MAP[name](state, src, params, out)
    for suffix in ('tsivf_linked.csv', 'tsivf_object_summary.csv', 'tsivf_field_trajectories.csv',
                   'tsivf_kinetics_df.csv', 'tsivf_droplet_frames.csv', 'tsivf_heterogeneity.csv',
                   'tsivf_kinetics_summary.csv', 'measured_image.tiff', 'droplet_labels.tiff'):
        assert (out / f'drops_{suffix}').is_file(), suffix
    droplets = assemble_batch_object_tables(tmp_path / 'out', [src])['tsivf_kinetics']
    # (Multi-Otsu keeps the brightest class, so the dimmest of the three droplets drops out of most frames)
    assert len(droplets) >= 2 and droplets['responded'].all() and set(droplets['direction']) == {'rise'}
    frames = per_image_csvs(tmp_path / 'out', [src], 'tsivf_droplet_frames')
    assert set(frames[ENTITY_ID_COLUMN]) == set(droplets[ENTITY_ID_COLUMN])


@pytest.mark.integration
def test_the_invitro_linked_panel_builds(qtbot):
    from pycat.toolbox.invitro.timeseries_kinetics import (brush_ready_kinetics, droplet_kinetics,
                                                            measure_droplets_multichannel)
    from pycat.toolbox.timeseries.results_view import build_invitro_workspace
    from pycat.toolbox.timeseries_invitro_tools import (link_condensates, make_frame_segmenter,
                                                        segment_stack_per_frame)
    st = _droplet_movie()
    labels = segment_stack_per_frame(st, make_frame_segmenter('Multi-Otsu', 3, 20))
    props, _ = measure_droplets_multichannel(labels, {'C0-488': st}, 0.1)
    linked, _ = link_condensates(props)
    kin = droplet_kinetics(linked, 'C0-488', 10.0, min_frames=5)
    tables = brush_ready_kinetics(linked, kin, 'C0-488', 10.0, 0.1, 'drops.tif')
    service = _service()
    ws = build_invitro_workspace(tables, service)
    qtbot.addWidget(ws)
    trace = next(v for v in ws._views if v.view_id == 'tsivf:traces')
    eid = tables['droplets'][ENTITY_ID_COLUMN].iloc[0]
    _click(trace, eid, 5)
    assert service.state.primary_id == eid
    ws.detach()
