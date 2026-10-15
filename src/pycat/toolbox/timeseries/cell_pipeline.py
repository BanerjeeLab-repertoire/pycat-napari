"""Cellular time series, segmented frame by frame exactly as the 2D workflow segments one image (1.7.004).

`analyze_cell_timeseries` is the whole analysis with no viewer -- the GUI panel and the batch step both call
it. Per frame (in parallel, raw counts never rescaled):

1. ``pre_process_image`` on the raw frame, scaled by the movie's maximum (one scale for every frame, the
   2D path's ``img / img.max()`` otherwise), then the enhanced background removal -- the 2D workflow's
   "Enhanced Background Removed" layer;
2. `segment_condensates_in_cells` with the 2D settings, then the 2D contrast floor (local CNR);
3. `cell_kinetics.measure_frame`: every cell's and every condensate's intensities on raw counts.

Then across frames: condensates are linked into tracks (persistence = frames per track), every cell gets an
``expression`` and a ``signal_retained`` (bleaching), and model-free kinetics are fitted per cell on the
mask-based and the mask-free condensed fraction.

Cells are segmented ONCE (the caller passes one 2D label image -- `time_average` gives the image to segment
them on); on the dissolution movie they move a few px in 10 min. A (T, H, W) label stack (keyframe Cellpose,
for cells that move) is accepted too: frame t is measured in its own masks.

Speed ("potato") options, all off by default because each one trades a little fidelity for time:
``roi='cells'`` processes only the bounding box of the cells plus a margin, ``frame_step`` analyses every
n-th frame, ``n_workers`` bounds the processes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import skimage as sk

from pycat.toolbox.timeseries import cell_kinetics as ck


@dataclass
class CellTimeSeriesResult:
    per_cell: pd.DataFrame                  # one row per cell per frame
    per_condensate: pd.DataFrame            # one row per condensate per frame, with track_id / track_frames
    kinetics: pd.DataFrame                  # one row per cell: kinetics of the mask-based condensed fraction
    kinetics_mask_free: pd.DataFrame        # ... and of the mask-free condensed fraction
    summary: dict
    condensate_labels: np.ndarray           # (T, h, w) over ``roi``, value = track_id + 1 (0 = none)
    fusions: pd.DataFrame = field(default_factory=pd.DataFrame)
    frames: list = field(default_factory=list)
    roi: Optional[tuple] = None             # (y0, y1, x0, x1) processed, in full-frame px
    params: dict = field(default_factory=dict)


def time_average(stack, frames=None):
    """The mean of the frames, read one at a time -- the image to segment the cells on. Every cell is
    visible in it whether its signal is condensed or diffuse at any given moment."""
    frames = range(len(stack)) if frames is None else frames
    acc = None
    for n, t in enumerate(frames, start=1):
        f = np.asarray(stack[t], dtype=np.float64)
        acc = f if acc is None else acc + f
    return (acc / n).astype(np.float32)


def capped_sizes(shape, ball_radius, window_size):
    """The GUI's per-image caps (`run_pre_process_image`): ball radius at 5 % of the smaller side (>= 4 px),
    window at twice that."""
    max_radius = max(4, int(min(shape[-2:]) * 0.05))
    return min(int(ball_radius), max_radius), min(int(window_size), max_radius * 2)


def segment_frame(raw, cell_labels, ball_radius, window_size, norm_max=None, settings=None,
                  contrast_floor=None, contrast_floor_on=True, suppress_foreground=True, suppression_params=None):
    """One frame, segmented as the 2D workflow segments one image. Returns (labels, cnr): the condensate
    labels above the contrast floor (below-floor objects zeroed, ids NOT renumbered) and every object's
    local CNR indexed by label."""
    from pycat.toolbox.image_processing_tools import pre_process_image, rb_gaussian_bg_removal_with_edge_enhancement
    from pycat.toolbox.segmentation.contrast_floor import CONTRAST_FLOOR_DEFAULT, object_local_cnr, split_by_floor
    from pycat.toolbox.segmentation.subcellular import segment_condensates_in_cells

    raw = np.asarray(raw, dtype=np.float32)
    br, ws = capped_sizes(raw.shape, ball_radius, window_size)
    pp = np.asarray(pre_process_image(raw, br, ws, suppress_foreground=suppress_foreground,
                                      suppression_params=suppression_params, norm_max=norm_max), np.float32)
    enhanced = np.asarray(rb_gaussian_bg_removal_with_edge_enhancement(pp, br), np.float32)
    seg = segment_condensates_in_cells(raw, enhanced, cell_labels, br, **(settings or {}))
    labels = sk.measure.label(seg['refined']).astype(np.int32)
    cnr = object_local_cnr(labels, raw, cell_labels)
    if contrast_floor_on:
        labels, _ = split_by_floor(labels, cnr, CONTRAST_FLOOR_DEFAULT if contrast_floor is None else contrast_floor)
    return labels.astype(np.int32), cnr


def _frame_job(args):
    """Worker: segment and measure one frame. Top-level so it pickles."""
    import io
    import contextlib
    t, raw, cells, kw, mpp = args
    with contextlib.redirect_stdout(io.StringIO()):          # the per-cell segmentation prints per cell
        labels, cnr = segment_frame(raw, cells, **kw)
        cell_rows, cond_rows = ck.measure_frame(raw, cells, labels, frame=t, microns_per_pixel=mpp)
    for r in cond_rows:
        r['local_cnr'] = float(cnr[r['label']]) if r['label'] < len(cnr) else np.nan
    return t, labels, cell_rows, cond_rows


def _resolve_roi(roi, cells2d, margin, shape):
    """(y0, y1, x0, x1) to process: None -> whole frame; 'cells' -> the cells' bounding box + ``margin``."""
    H, W = shape
    if roi is None:
        return (0, H, 0, W)
    if isinstance(roi, str) and roi == 'cells':
        rows = np.flatnonzero((cells2d > 0).any(axis=1))
        cols = np.flatnonzero((cells2d > 0).any(axis=0))
        if not len(rows):
            return (0, H, 0, W)
        return (max(0, rows[0] - margin), min(H, rows[-1] + 1 + margin),
                max(0, cols[0] - margin), min(W, cols[-1] + 1 + margin))
    y0, y1, x0, x1 = (int(v) for v in roi)
    return (max(0, y0), min(H, y1), max(0, x0), min(W, x1))


# Measured peak memory of one frame job is ~53x the float32 frame (0.89 GB for 2048 x 2048), plus ~0.25 GB of
# imports per worker process.
_WORKER_BYTES_PER_PX = 53 * 4
_WORKER_BASE_BYTES = 0.25e9


def default_workers(frame_shape, cpu_count=None, available_bytes=None):
    """How many frame workers this machine can run: one per spare core, at most 8, and no more than fit in
    60 % of the memory available now (a 2048 x 2048 movie on 8 workers wants ~9 GB on top of the viewer)."""
    import os
    cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 2)
    n = min(8, max(1, cpus - 1))
    if available_bytes is None:
        try:
            import psutil
            available_bytes = psutil.virtual_memory().available
        except Exception:                                   # broad-ok: optional_probe — no psutil: cores only
            return n
    per = _WORKER_BASE_BYTES + _WORKER_BYTES_PER_PX * int(np.prod(frame_shape[-2:]))
    return max(1, min(n, int(0.6 * available_bytes // per)))


def _run_frames(stack, cells, frames, window, kw, mpp, n_workers, progress, cancel):
    """Segment + measure every frame in ``frames`` (parallel when it pays). Yields (t, labels, cells, conds)."""
    y0, y1, x0, x1 = window
    cells_at = (lambda t: cells[t][y0:y1, x0:x1]) if cells.ndim == 3 else (lambda t: cells[y0:y1, x0:x1])
    jobs = ((t, np.asarray(stack[t])[y0:y1, x0:x1].astype(np.float32), cells_at(t), kw, mpp) for t in frames)
    n = len(frames)
    workers = int(n_workers) if n_workers else default_workers((y1 - y0, x1 - x0))
    if workers <= 1 or n < 3:
        for i, job in enumerate(jobs, start=1):
            if cancel is not None and cancel():
                raise InterruptedError('Cancelled by user.')
            yield _frame_job(job)
            if progress is not None:
                progress(i, n)
        return
    from concurrent.futures import ProcessPoolExecutor, FIRST_COMPLETED, wait
    from pycat.toolbox.timeseries.analysis import _init_worker_threads
    done = 0
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker_threads) as ex:
        pending = set()
        for job in jobs:                                     # bounded window: at most 2 frames per worker in flight
            pending.add(ex.submit(_frame_job, job))
            while len(pending) >= 2 * workers:
                finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                for f in finished:
                    done += 1
                    yield f.result()
                    if progress is not None:
                        progress(done, n)
                if cancel is not None and cancel():
                    ex.shutdown(wait=False, cancel_futures=True)
                    raise InterruptedError('Cancelled by user.')
        for f in pending:
            done += 1
            yield f.result()
            if progress is not None:
                progress(done, n)


def _relabel_by_track(labels_stack, per_condensate, frames):
    """Each frame's labels repainted with track_id + 1, so one condensate keeps one value through time."""
    n_tracks = int(per_condensate['track_id'].max()) + 2 if per_condensate is not None and len(per_condensate)         and 'track_id' in per_condensate else 1
    out = np.zeros(labels_stack.shape, np.uint16 if n_tracks < 65535 else np.int32)
    if per_condensate is None or not len(per_condensate) or 'track_id' not in per_condensate:
        return out
    for i, t in enumerate(frames):
        d = per_condensate[per_condensate['frame'] == t]
        if not len(d):
            continue
        lut = np.zeros(int(labels_stack[i].max()) + 1, out.dtype)
        lut[d['label'].to_numpy(int)] = d['track_id'].to_numpy(int) + 1
        out[i] = lut[labels_stack[i]]
    return out


def analyze_cell_timeseries(stack, cell_labels, ball_radius, window_size, *, frame_interval_s=1.0,
                            microns_per_pixel=1.0, frames=None, frame_step=1, roi=None, settings=None,
                            contrast_floor=None, contrast_floor_on=True, suppress_foreground=True,
                            suppression_params=None, max_gap_frames=1, min_track_frames=1, n_workers=None,
                            progress=None, cancel=None) -> CellTimeSeriesResult:
    """The cellular time-series analysis of one movie. See the module docstring.

    ``stack`` is (T, H, W) raw counts, anything indexable by frame (numpy, dask, zarr, a lazy reader).
    ``cell_labels`` is (H, W), or (T, H, W) for per-frame masks. ``frames`` restricts the analysis to those
    frame indices (default all), ``frame_step`` keeps every n-th of them. ``settings`` overrides
    `CELLULAR_2D_SETTINGS` keys. ``progress(done, total)`` and ``cancel() -> bool`` are optional.
    """
    T = len(stack)
    frames = list(range(T) if frames is None else frames)[::max(1, int(frame_step))]
    cells = np.asarray(cell_labels)
    cells2d = cells if cells.ndim == 2 else cells.max(axis=0)
    H, W = cells2d.shape
    window = _resolve_roi(roi, cells2d, 6 * int(ball_radius) + int(window_size), (H, W))
    y0, y1, x0, x1 = window

    # ONE intensity scale for the whole movie (the brightest pixel in the processed window over the frames
    # analysed), so a frame's preprocessing does not depend on how bright that frame happens to be.
    norm_max = max(float(np.asarray(stack[t])[y0:y1, x0:x1].max()) for t in frames)
    kw = dict(ball_radius=ball_radius, window_size=window_size, norm_max=norm_max, settings=settings,
              contrast_floor=contrast_floor, contrast_floor_on=contrast_floor_on,
              suppress_foreground=suppress_foreground, suppression_params=suppression_params)

    labels_stack = np.zeros((len(frames), y1 - y0, x1 - x0), np.uint16)   # per-frame ids (thousands at most)
    index = {t: i for i, t in enumerate(frames)}
    cell_rows, cond_rows = [], []
    for t, labels, crow, conds in _run_frames(stack, cells, frames, window, kw, microns_per_pixel,
                                              n_workers, progress, cancel):
        labels_stack[index[t]] = labels.astype(np.uint16)
        cell_rows += crow
        cond_rows += conds

    per_cell = ck.add_bleach_and_expression(pd.DataFrame(cell_rows))
    per_cell = per_cell.assign(time_s=per_cell['frame'] * float(frame_interval_s))
    per_cond = pd.DataFrame(cond_rows)
    if len(per_cond):
        # positions back in full-frame coordinates (the window's origin)
        for c in ('y_px', 'bbox_y0', 'bbox_y1'):
            per_cond[c] += y0
        for c in ('x_px', 'bbox_x0', 'bbox_x1'):
            per_cond[c] += x0
        per_cond['y_um'] = per_cond['y_px'] * microns_per_pixel
        per_cond['x_um'] = per_cond['x_px'] * microns_per_pixel
        per_cond = per_cond.sort_values(['frame', 'label']).reset_index(drop=True)
    linked, fusions = ck.link_condensate_tracks(per_cond, max_gap_frames=max_gap_frames,
                                                min_track_frames=min_track_frames)
    if linked is not None and len(linked):
        linked = linked.assign(time_s=linked['frame'] * float(frame_interval_s))

    # Kept over the processed window only (a full 60 x 2048 x 2048 movie is 500 MB even as uint16); the
    # caller places it with ``roi`` as the offset.
    track_labels = _relabel_by_track(labels_stack, linked, frames)
    del labels_stack

    kin = ck.cell_kinetics(per_cell, 'condensed_fraction', frame_interval_s)
    kin_free = ck.cell_kinetics(per_cell, 'condensed_fraction_mask_free', frame_interval_s)
    summary = ck.field_summary(kin)
    summary.update({f'mask_free_{k}': v for k, v in ck.field_summary(kin_free).items()})
    summary.update(n_frames=len(frames), n_condensate_tracks=int(linked['track_id'].nunique()) if len(linked) else 0)
    params = dict(ball_radius=ball_radius, window_size=window_size, frame_interval_s=frame_interval_s,
                  microns_per_pixel=microns_per_pixel, frame_step=frame_step, roi=window,
                  contrast_floor=contrast_floor, contrast_floor_on=contrast_floor_on,
                  max_gap_frames=max_gap_frames, min_track_frames=min_track_frames, norm_max=norm_max)
    return CellTimeSeriesResult(per_cell=per_cell, per_condensate=linked if linked is not None else per_cond,
                                kinetics=kin, kinetics_mask_free=kin_free, summary=summary,
                                condensate_labels=track_labels, fusions=fusions, frames=frames, roi=window, params=params)


def _cell_boxes(cell_labels):
    cells = np.asarray(cell_labels)
    cells = cells if cells.ndim == 2 else cells.max(axis=0)
    rows = []
    for p in sk.measure.regionprops(cells):
        y0, x0, y1, x1 = p.bbox
        rows.append({'cell': int(p.label), 'bbox_y0': y0, 'bbox_x0': x0, 'bbox_y1': y1, 'bbox_x1': x1,
                     'cell_area_um2': np.nan, 'touches_border': bool(y0 == 0 or x0 == 0 or y1 == cells.shape[0]
                                                                   or x1 == cells.shape[1])})
    return pd.DataFrame(rows)


def _track_table(per_condensate):
    """One row per condensate track: where and when it lived, how big and how enriched it got."""
    if per_condensate is None or not len(per_condensate) or 'track_id' not in per_condensate:
        return pd.DataFrame()
    g = per_condensate.sort_values('frame').groupby('track_id')
    first = g.first()
    out = pd.DataFrame({
        'track_id': first.index.astype(int), 'track_label': first.index.astype(int) + 1,
        'cell': first['cell'].astype(int).to_numpy(),
        'first_frame': first['frame'].astype(int).to_numpy(), 'last_frame': g['frame'].max().to_numpy(),
        'track_frames': g['frame'].nunique().to_numpy(),
        'mean_area_um2': g['area_um2'].mean().to_numpy(), 'max_area_um2': g['area_um2'].max().to_numpy(),
        'max_partition': g['partition'].max().to_numpy(), 'max_excess': g['excess'].max().to_numpy(),
        'mean_local_cnr': g['local_cnr'].mean().to_numpy() if 'local_cnr' in per_condensate else np.nan,
        'frame': first['frame'].astype(int).to_numpy(),
        'bbox_y0': first['bbox_y0'].to_numpy(), 'bbox_x0': first['bbox_x0'].to_numpy(),
        'bbox_y1': first['bbox_y1'].to_numpy(), 'bbox_x1': first['bbox_x1'].to_numpy()})
    return out.reset_index(drop=True)


def result_tables(result: CellTimeSeriesResult, cell_labels, source_path=None) -> dict:
    """The result as brushable tables, every row named (`entity_ref.stamp_entity_ids`): ``cells`` (one row per
    cell: kinetics of both measures, expression, bbox), ``cell_frames`` (one row per cell per frame -- the
    traces), ``condensate_tracks`` (one row per track) and ``condensates`` (one row per condensate per frame).
    A cell's rows in ``cells`` and ``cell_frames`` share ONE entity id, and so do a track's rows in
    ``condensate_tracks`` and ``condensates``: selecting a cell anywhere selects its whole trace."""
    from pycat.utils.entity_ref import stamp_entity_ids
    kin = result.kinetics.copy()
    free = result.kinetics_mask_free.drop(columns=['metric', 'expression'], errors='ignore').add_prefix('mf_')
    cells = kin.drop(columns=['metric'], errors='ignore').merge(
        free.rename(columns={'mf_cell': 'cell'}), on='cell', how='left')
    first = result.per_cell.sort_values('frame').groupby('cell').first()
    cells['initial_condensed_fraction'] = cells['cell'].map(first['condensed_fraction'])
    cells['initial_partition'] = cells['cell'].map(first['partition'])
    cells['initial_n_condensates'] = cells['cell'].map(first['n_condensates'])
    cells = cells.merge(_cell_boxes(cell_labels), on='cell', how='left')
    um = float(result.params.get('microns_per_pixel', 1.0) or 1.0)
    area = result.per_cell.groupby('cell')['cell_area_px'].first()
    cells['cell_area_um2'] = cells['cell'].map(area) * um * um
    cells['frame'] = 0
    cells['label'] = cells['cell']          # the cell-label value (what a batch image navigator indexes by)
    tracks = _track_table(result.per_condensate)
    conds = result.per_condensate.copy() if result.per_condensate is not None else pd.DataFrame()
    if len(conds):
        conds['track_label'] = conds['track_id'] + 1
    frames = result.per_cell.copy()
    stamp = dict(source_path=source_path, operation_id='cell_timeseries')
    for df, kind, col in ((cells, 'cell', 'cell'), (frames, 'cell', 'cell'),
                          (tracks, 'condensate_track', 'track_label'), (conds, 'condensate_track', 'track_label')):
        if len(df):
            stamp_entity_ids(df, entity_type=kind, label_column=col, parent_column='_none_', frame=None, **stamp)
    return {'cells': cells, 'cell_frames': frames, 'condensate_tracks': tracks, 'condensates': conds}
