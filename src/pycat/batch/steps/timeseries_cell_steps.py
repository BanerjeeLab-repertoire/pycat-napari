"""Batch replay of the cellular time series (1.7.004): cells once on the time average, then every frame
segmented as the 2D workflow segments one image (`timeseries.cell_pipeline.analyze_cell_timeseries`).

Per movie it writes, into ``output_dir`` (the per-image folder):

* ``<stem>_ts_cells_df.csv``             one row per cell -- kinetics, expression, bbox (brushable)
* ``<stem>_ts_condensate_tracks_df.csv`` one row per condensate track (brushable)
* ``<stem>_ts_cell_frames.csv``          one row per cell per frame -- the traces
* ``<stem>_ts_condensates.csv``          one row per condensate per frame
* ``<stem>_ts_summary.csv``              the movie's field summary (one row)
* ``<stem>_measured_image.tiff`` (the time average), ``<stem>_labeled_cells.tiff``,
  ``<stem>_condensate_tracks.tiff`` (zlib; value = track_id + 1)

so the linked batch results (`utils.batch_brushing`) plot every movie's cells together and open the right
movie's image at a clicked cell.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from pycat.batch.steps._common import _get_data, _save_array
from pycat.kernel import OperationService


def _stack(state):
    for key in ('fluorescence_image', 'image'):
        arr = state.get(key)
        if arr is not None and getattr(arr, 'ndim', 0) == 3:
            return arr
    return None


def _frame_interval(state, params):
    """The file's own frame interval when its metadata has one (a batch mixes movies), else the recorded."""
    try:
        from pycat.utils.frame_interval import frame_interval_s
        value = frame_interval_s(state['data_instance'].data_repository, context='batch cellular time series')
        if value == value and value > 0:
            return float(value)
    except Exception:                                        # broad-ok: optional_probe — fall back to the recording
        pass
    return float(params.get('frame_interval_s', 1.0) or 1.0)


def replay_ts_cells_time_average(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Cellpose on the movie's time average at the recorded cell diameter (the GUI's 'Segment cells on the
    time average')."""
    from pycat.toolbox.timeseries.cell_pipeline import time_average
    stack = _stack(state)
    if stack is None:
        print(f'[PyCAT Batch]   Cells on the time average skipped for {image_path.name}: not a (T, H, W) stack.')
        return
    avg = time_average(stack)
    lo, hi = np.percentile(avg, (0.5, 99.9))
    norm = np.clip((avg - lo) / max(hi - lo, 1e-6), 0, 1).astype(np.float32)
    diameter = float(params.get('cell_diameter') or _get_data(state['data_instance'], 'cell_diameter', 100))
    cells = np.asarray(OperationService.produce('cellpose', {'image': norm},
                                                {'object_diameter': int(round(diameter))})).astype(np.int32)
    state['ts_time_average'] = avg
    state['labeled_cells'] = cells
    state['no_cells'] = int(cells.max()) == 0
    print(f'[PyCAT Batch]   {int(cells.max())} cells on the time average of {image_path.name}.')


def replay_cell_timeseries(state: dict, image_path: Path, params: dict, output_dir: Path):
    """The cellular time-series analysis of one movie, with the recorded settings."""
    from pycat.toolbox.timeseries.cell_pipeline import analyze_cell_timeseries
    from pycat.utils.pixel_size import pixel_size_um_or_default
    stack = _stack(state)
    cells = state.get('ts_cell_mask_stack')
    cells = cells if cells is not None and getattr(cells, 'shape', (0,))[0] == getattr(stack, 'shape', (1,))[0] \
        else state.get('labeled_cells')
    if stack is None or cells is None or state.get('no_cells'):
        print(f'[PyCAT Batch]   Cellular time series skipped for {image_path.name}: '
              f'{"not a (T, H, W) stack" if stack is None else "no cells"}.')
        return
    di = state['data_instance']
    # Frame range and a Step 2 rectangle were already applied to the stack by set_frame_range; only the
    # automatic cells' region is re-derived per movie.
    roi = 'cells' if params.get('roi') == 'cells' else None
    result = analyze_cell_timeseries(
        stack, cells, int(params.get('ball_radius') or _get_data(di, 'ball_radius', 5)),
        int(params.get('window_size') or int(_get_data(di, 'cell_diameter', 100)) // 2),
        frame_interval_s=_frame_interval(state, params),
        microns_per_pixel=pixel_size_um_or_default(di.data_repository, context='batch cellular time series'),
        frame_step=int(params.get('frame_step', 1) or 1), roi=roi,
        contrast_floor=params.get('contrast_floor'), contrast_floor_on=bool(params.get('contrast_floor_on', True)),
        min_track_frames=int(params.get('min_track_frames', 1) or 1), n_workers=params.get('n_workers'),
        suppress_foreground=params.get('suppress_foreground', True),
        suppression_params=params.get('suppression_params'))
    write_outputs(result, cells, state.get('ts_time_average'), stack, image_path, output_dir,
                  source=di.data_repository.get('file_path') or str(image_path))
    s = result.summary
    print(f"[PyCAT Batch]   Cellular time series: {s.get('n_responded', 0)}/{s.get('n_cells', 0)} cells responded, "
          f"median half-time {s.get('t_half_s_median', float('nan')):.0f} s.")


def write_outputs(result, cells, average, stack, image_path, output_dir, source=None):
    """The per-movie files listed in the module docstring."""
    import pandas as pd
    import tifffile
    from pycat.toolbox.timeseries.cell_pipeline import result_tables, time_average
    stem = Path(image_path).stem
    out = Path(output_dir)
    tables = result_tables(result, cells, source or str(image_path))
    tables['cells'].to_csv(out / f'{stem}_ts_cells_df.csv', index=False)
    tables['condensate_tracks'].to_csv(out / f'{stem}_ts_condensate_tracks_df.csv', index=False)
    tables['cell_frames'].to_csv(out / f'{stem}_ts_cell_frames.csv', index=False)
    tables['condensates'].to_csv(out / f'{stem}_ts_condensates.csv', index=False)
    pd.DataFrame([result.summary]).to_csv(out / f'{stem}_ts_summary.csv', index=False)
    avg = average if average is not None else time_average(stack, result.frames)
    _save_array(np.asarray(avg, dtype=np.float32), out / f'{stem}_measured_image.tiff')
    cells2d = np.asarray(cells) if np.ndim(cells) == 2 else np.asarray(cells).max(axis=0)
    _save_array(cells2d.astype(np.uint32), out / f'{stem}_labeled_cells.tiff')
    tifffile.imwrite(out / f'{stem}_condensate_tracks.tiff', result.condensate_labels, compression='zlib')
