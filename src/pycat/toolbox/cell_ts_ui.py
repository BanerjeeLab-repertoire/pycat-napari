"""The cellular time-series panel (1.7.004): segment the cells once, segment every frame as 2D does, measure
on raw counts, open the linked results. The science is `timeseries.cell_pipeline`; this file is only the GUI.

Speed options live under one "Performance options" checkbox, collapsed by default: they exist to make a long
movie finish on a modest computer, and each trades a little fidelity for time (see `analyze_cell_timeseries`).
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from pycat.utils.general_utils import debug_log
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.kernel import OperationService

_WORKER_CLS = None
STEP_NAME = 'cell_timeseries'


def _worker_class():
    """The QThread that runs `analyze_cell_timeseries` (built on first use: Qt is not needed to import)."""
    global _WORKER_CLS
    if _WORKER_CLS is not None:
        return _WORKER_CLS
    from qtpy.QtCore import QThread, Signal

    class _CellTimeSeriesWorker(QThread):
        progress = Signal(int, int)
        finished_ok = Signal(object)
        failed = Signal(str)

        def __init__(self, kwargs):
            super().__init__()
            self.kwargs = kwargs
            self._cancel = False

        def cancel(self):
            self._cancel = True

        def run(self):
            import traceback
            kw = dict(self.kwargs)
            inputs = {role: kw.pop(role) for role in ('stack', 'cell_labels')}
            try:
                res = OperationService.produce('cell_timeseries.analyze', inputs, {
                    **kw, 'progress': lambda d, n: self.progress.emit(int(d), int(n)),
                    'cancel': lambda: self._cancel})
                self.finished_ok.emit(res)
            except InterruptedError:
                self.failed.emit('Cancelled.')
            except Exception:                                # broad-ok: batch_step — reported to the GUI thread
                self.failed.emit(traceback.format_exc())

    _WORKER_CLS = _CellTimeSeriesWorker
    return _WORKER_CLS


def _dr(ui):
    return ui.central_manager.active_data_class.data_repository


def _layer(ui, name):
    try:
        return ui.viewer.layers[name]
    except (KeyError, ValueError):
        return None


def _build_widgets(ui, lay):
    """The panel's widgets; returns their handles."""
    from qtpy.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QProgressBar,
                                QPushButton, QSpinBox, QWidget)
    import napari
    import os
    ns = SimpleNamespace()
    form = QFormLayout()
    ns.stack_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow('Raw stack (T, H, W):', ns.stack_dd)
    ns.cells_btn = QPushButton('Segment cells on the time average')
    ns.cells_btn.setToolTip('Cellpose once, on the mean of the frames (every cell is visible there, condensed '
                            'or not), at the cell diameter from Measure Line. Edit the labels before running '
                            'if a cell is wrong.')
    form.addRow(ns.cells_btn)
    ns.mask_dd = ui.create_layer_dropdown(napari.layers.Labels, name_hint='Labeled Cell Mask')
    form.addRow('Cell mask:', ns.mask_dd)
    ns.interval = QDoubleSpinBox()
    ns.interval.setRange(0.001, 1e5)
    ns.interval.setDecimals(3)
    ns.interval.setValue(1.0)
    ns.interval.setSuffix(' s')
    try:
        from pycat.utils.frame_interval import sync_spinbox_from_metadata
        sync_spinbox_from_metadata(ns.interval, _dr(ui), context='cellular time series')
    except Exception as exc:                                 # broad-ok: optional_probe — keep the default, but say so
        from pycat.utils.general_utils import report_guarantee_failure
        report_guarantee_failure('cell_ts_ui: frame interval from metadata', exc)
    form.addRow('Frame interval:', ns.interval)
    ns.floor_cb = QCheckBox('Contrast floor (local CNR)')
    ns.floor_cb.setChecked(True)
    ns.floor = QDoubleSpinBox()
    ns.floor.setRange(0.0, 20.0)
    ns.floor.setSingleStep(0.25)
    ns.floor.setValue(1.75)
    row = QHBoxLayout()
    row.addWidget(ns.floor_cb)
    row.addWidget(ns.floor)
    form.addRow(row)
    ns.min_track = QSpinBox()
    ns.min_track.setRange(1, 999)
    ns.min_track.setValue(1)
    ns.min_track.setToolTip('Drop condensates seen in fewer than this many frames (1 = keep all). Every '
                            "condensate's track length is recorded either way (track_frames).")
    form.addRow('Min frames per condensate:', ns.min_track)
    lay.addLayout(form)

    ns.perf_cb = QCheckBox('Performance options (for slower computers)')
    lay.addWidget(ns.perf_cb)
    ns.perf_box = QWidget()
    pf = QFormLayout(ns.perf_box)
    ns.workers = QSpinBox()
    ns.workers.setRange(1, max(1, os.cpu_count() or 1))
    ns.workers.setValue(min(8, max(1, (os.cpu_count() or 2) - 1)))
    pf.addRow('Parallel workers:', ns.workers)
    ns.roi_cb = QCheckBox("Process only the cells' region")
    ns.roi_cb.setToolTip('Skip the empty field around the cells (the bounding box of all cells plus a margin).')
    pf.addRow(ns.roi_cb)
    ns.step = QSpinBox()
    ns.step.setRange(1, 1000)
    ns.step.setValue(1)
    pf.addRow('Analyse every n-th frame:', ns.step)
    pf.addRow(QLabel("<span style='color:#888;font-size:9pt;'>Frame range and a rectangle crop are set in "
                     "Step 2; upscaling, the cached preprocessed stack and keyframe Cellpose (moving cells) "
                     "are below.</span>"))
    ns.perf_box.setVisible(False)
    ns.perf_cb.toggled.connect(ns.perf_box.setVisible)
    lay.addWidget(ns.perf_box)

    ns.progress = QProgressBar()
    ns.progress.setVisible(False)
    lay.addWidget(ns.progress)
    btns = QHBoxLayout()
    ns.run_btn = QPushButton('▶  Segment and measure every frame')
    ns.cancel_btn = QPushButton('Cancel')
    ns.cancel_btn.setVisible(False)
    btns.addWidget(ns.run_btn)
    btns.addWidget(ns.cancel_btn)
    lay.addLayout(btns)
    ns.worker = None
    ns.workspace = None
    return ns


def _frame_range(ui, n_t):
    dr = _dr(ui)
    t0 = int(dr.get('timeseries_frame_start', 0) or 0)
    t1 = int(dr.get('timeseries_frame_end', n_t - 1) if dr.get('timeseries_frame_end') is not None else n_t - 1)
    return list(range(max(0, t0), min(n_t - 1, t1) + 1))


def _step2_roi(ui):
    dr = _dr(ui)
    if not dr.get('timeseries_roi_active'):
        return None
    return tuple(int(dr[k]) for k in ('timeseries_roi_y0', 'timeseries_roi_y1', 'timeseries_roi_x0',
                                      'timeseries_roi_x1'))


def _on_segment_cells(ui, ns):
    """Cellpose on the time average, at the Measure Line cell diameter."""
    from pycat.utils.qt_worker import run_with_progress
    layer = _layer(ui, ns.stack_dd.currentText())
    if layer is None or np.ndim(layer.data) != 3:
        napari_show_warning('Cellular time series: choose a (T, H, W) stack first.')
        return
    dr = _dr(ui)
    diameter = float(dr.get('cell_diameter', 0) or 0)
    if diameter <= 0:
        napari_show_warning('Cellular time series: run Measure Line (draw a line across a cell) first.')
        return
    frames = _frame_range(ui, layer.data.shape[0])

    def _work(progress):
        avg = OperationService.produce('cell_timeseries.time_average', {'stack': layer.data, 'frames': frames})
        lo, hi = np.percentile(avg, (0.5, 99.9))
        norm = np.clip((avg - lo) / max(hi - lo, 1e-6), 0, 1).astype(np.float32)
        return avg, OperationService.produce(
            'cellpose', {'image': norm},
            {'object_diameter': int(round(diameter))})

    try:
        avg, cells = run_with_progress(_work, title='Cells', text='Time average + Cellpose…',
                                       parent=getattr(ui.viewer.window, '_qt_window', None))
    except Exception as exc:                                 # broad-ok: batch_step — report, keep the panel usable
        napari_show_warning(f'Cellular time series: cell segmentation failed — {exc}')
        return
    ui.viewer.add_image(avg, name=f'{layer.name} [time average]', colormap='gray', visible=False)
    if 'Labeled Cell Mask' in ui.viewer.layers:
        ui.viewer.layers['Labeled Cell Mask'].data = cells
    else:
        ui.viewer.add_labels(cells, name='Labeled Cell Mask')
    ns.mask_dd.setCurrentText('Labeled Cell Mask')
    napari_show_info(f'{int(cells.max())} cells on the time average. Edit the labels if needed, then run.')
    ui._record('ts_cells_time_average', {'stack_layer': layer.name, 'cell_diameter': diameter,
                                         'frames': [frames[0], frames[-1]]})


def _run_kwargs(ui, ns, stack, cells):
    dr = _dr(ui)
    br = float(dr.get('ball_radius', 0) or 0)
    if br <= 0 or abs(br - 75) < 1:
        napari_show_warning('Cellular time series: run Measure Line (a condensate and a cell) first — '
                            'the condensate size sets the segmentation scale.')
        return None
    roi = _step2_roi(ui)
    if roi is None and ns.perf_cb.isChecked() and ns.roi_cb.isChecked():
        roi = 'cells'
    from pycat.utils.pixel_size import pixel_size_um_or_default
    mpp = pixel_size_um_or_default(dr, context='cellular time series')
    return dict(stack=stack, cell_labels=cells, ball_radius=int(br),
                window_size=max(1, int(float(dr.get('cell_diameter', 2 * br * 10) or 1)) // 2),
                frame_interval_s=float(ns.interval.value()), microns_per_pixel=mpp,
                frames=_frame_range(ui, len(stack)),
                frame_step=int(ns.step.value()) if ns.perf_cb.isChecked() else 1,
                roi=roi, contrast_floor=float(ns.floor.value()), contrast_floor_on=ns.floor_cb.isChecked(),
                min_track_frames=int(ns.min_track.value()),
                n_workers=int(ns.workers.value()) if ns.perf_cb.isChecked() else None,
                suppress_foreground=dr.get('suppress_foreground', True),
                suppression_params=dr.get('foreground_suppression_params'))


def _on_run(ui, ns):
    stack_layer, mask_layer = _layer(ui, ns.stack_dd.currentText()), _layer(ui, ns.mask_dd.currentText())
    if stack_layer is None or np.ndim(stack_layer.data) != 3:
        napari_show_warning('Cellular time series: choose a (T, H, W) raw stack.')
        return
    if mask_layer is None:
        napari_show_warning('Cellular time series: segment the cells first (or choose a cell mask).')
        return
    # a (T, H, W) keyframe mask may be a lazy wrapper whose __array__ is frame 0 only: materialise every frame
    from pycat.utils.qt_worker import materialize_off_thread
    cells = (materialize_off_thread(mask_layer.data, viewer=ui.viewer, title='Cell masks', dtype=np.int32)
             if np.ndim(mask_layer.data) == 3
             else np.array(mask_layer.data[...]))            # a 2D mask: one frame is all there is
    if cells.shape[-2:] != tuple(stack_layer.data.shape[-2:]):
        napari_show_warning(f'Cellular time series: the cell mask is {cells.shape[-2:]}, the stack '
                            f'{tuple(stack_layer.data.shape[-2:])}.')
        return
    kw = _run_kwargs(ui, ns, stack_layer.data, cells)
    if kw is None:
        return
    ns.progress.setRange(0, len(kw['frames'][::kw['frame_step']]))
    ns.progress.setValue(0)
    ns.progress.setVisible(True)
    ns.run_btn.setEnabled(False)
    ns.cancel_btn.setVisible(True)
    worker = _worker_class()(kw)
    ns.worker = worker

    def _done(_msg=None):
        ns.progress.setVisible(False)
        ns.run_btn.setEnabled(True)
        ns.cancel_btn.setVisible(False)

    def _failed(msg):
        _done()
        napari_show_warning('Cellular time series: ' + ('cancelled.' if msg == 'Cancelled.' else
                                                         'failed — see the terminal.'))
        if msg != 'Cancelled.':
            print(f'[PyCAT cellular time series] ERROR:\n{msg}')

    def _ok(result):
        _done()
        _show_results(ui, ns, result, stack_layer, mask_layer)

    worker.progress.connect(lambda d, n: ns.progress.setValue(d))
    worker.finished_ok.connect(_ok)
    worker.failed.connect(_failed)
    worker.start()
    ui._record(STEP_NAME, {k: v for k, v in kw.items() if k not in ('stack', 'cell_labels')}
               | {'stack_layer': stack_layer.name, 'mask_layer': mask_layer.name})


def _show_results(ui, ns, result, stack_layer, mask_layer):
    """Layers, tables in the repository, and the linked results dock."""
    from pycat.toolbox.timeseries.cell_pipeline import result_tables
    from pycat.toolbox.timeseries.results_view import build_workspace
    y0, _y1, x0, _x1 = result.roi
    step = int(result.params.get('frame_step', 1) or 1)
    name = 'Condensate tracks'
    if name in ui.viewer.layers:
        ui.viewer.layers.remove(name)
    track_layer = ui.viewer.add_labels(result.condensate_labels, name=name,
                                       translate=(result.frames[0], y0, x0), scale=(step, 1, 1))
    source = getattr(stack_layer, 'source', None)
    source_path = str(getattr(source, 'path', '') or '') or None
    tables = result_tables(result, mask_layer.data, source_path)
    dr = _dr(ui)
    for key, df in tables.items():
        dr[f'ts_{key}_df'] = df
    dr['ts_cell_summary'] = dict(result.summary)
    s = result.summary
    napari_show_info(
        f"{s.get('n_responded', 0)}/{s.get('n_cells', 0)} cells responded ({s.get('direction', '')}); "
        f"median half-time {s.get('t_half_s_median', float('nan')):.0f} s; "
        f"{s.get('n_condensate_tracks', 0)} condensate tracks.")
    service = getattr(ui.central_manager, 'selection', None)
    if service is None:
        return
    if ns.workspace is not None:
        try:
            ns.workspace.detach()
        except Exception as exc:                             # broad-ok: ui_cleanup — the old dock is being replaced
            debug_log('cell_ts_ui: could not detach the previous results', exc)
    ns.workspace = build_workspace(tables, service, viewer=ui.viewer, cell_layer=mask_layer,
                                   track_layer=track_layer, source_path=source_path)
    from pycat.utils.dock_space import add_results_dock
    add_results_dock(ui.viewer.window, ns.workspace, name='Cellular time series — results')


def add_cell_timeseries_panel(ui, layout=None, separate_widget=False):
    """Build the panel and add it to ``layout`` (or its own dock)."""
    from qtpy.QtWidgets import QVBoxLayout, QWidget
    lay = QVBoxLayout()
    ui.add_text_label(lay, 'Cellular time series — segment every frame, measure on raw counts', bold=True)
    ns = _build_widgets(ui, lay)
    ns.cells_btn.clicked.connect(lambda: _on_segment_cells(ui, ns))
    ns.run_btn.clicked.connect(lambda: _on_run(ui, ns))
    ns.cancel_btn.clicked.connect(lambda: ns.worker is not None and ns.worker.cancel())
    w = QWidget()
    w.setLayout(lay)
    ui._add_widget_to_layout_or_dock(w, layout, separate_widget, 'Cellular time series')
    ui._cell_ts_panel = ns
    return ns
