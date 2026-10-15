"""Batch replay of the in-vitro fluorescence time series (1.7.004). The panel recorded these steps from the
start, but nothing replayed them: a batch skipped every one ("not registered").

Each step calls the function the panel calls, with the recorded settings, and keeps its result in ``state``
under the panel's own repository key. Files per movie, in the per-image folder:

* ``<stem>_tsivf_linked.csv`` (every droplet in every frame, measured in every channel, with ``track_id``)
  and ``<stem>_tsivf_fusions.csv``;
* ``<stem>_tsivf_object_summary.csv`` and ``<stem>_tsivf_field_trajectories.csv``;
* ``<stem>_tsivf_kinetics_df.csv`` (one row per droplet: kinetics, bbox -- brushable),
  ``<stem>_tsivf_droplet_frames.csv`` (the traces), ``<stem>_tsivf_heterogeneity.csv``,
  ``<stem>_tsivf_kinetics_summary.csv``;
* ``<stem>_measured_image.tiff`` (the time average) and ``<stem>_droplet_labels.tiff`` (each track's
  footprint over the movie, value = track_id + 1) -- what the batch image navigator opens.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from pycat.batch.steps._common import _get_data, _resolve_image_layer, _save_array


def _stack(state, layer_name=None):
    arr = _resolve_image_layer(state, layer_name, fallback=None) if layer_name else None
    for cand in (arr, state.get('fluorescence_image'), state.get('image')):
        if cand is not None and getattr(cand, 'ndim', 0) == 3:
            return cand
    return None


def _mpx(state):
    from pycat.utils.pixel_size import pixel_size_um_or_default
    return pixel_size_um_or_default(state['data_instance'].data_repository, context='batch in-vitro time series')


def _dt(state, params):
    try:
        from pycat.utils.frame_interval import frame_interval_s
        v = frame_interval_s(state['data_instance'].data_repository, context='batch in-vitro time series')
        if v == v and v > 0:
            return float(v)
    except Exception:                                        # broad-ok: optional_probe — fall back to the recording
        pass
    return float(params.get('frame_interval_s', 1.0) or 1.0)


def replay_tsivf_segment_stack(state: dict, image_path: Path, params: dict, output_dir: Path):
    from pycat.toolbox.timeseries_invitro_tools import make_frame_segmenter, segment_stack_per_frame
    stack = _stack(state, params.get('image_layer'))
    if stack is None:
        print(f'[PyCAT Batch]   TSIVF segmentation skipped for {image_path.name}: not a (T, H, W) stack.')
        return
    seg = make_frame_segmenter(params.get('method', 'Multi-Otsu'), params.get('multiotsu_classes', 3),
                               params.get('min_area_px', 20))
    kf = int(params.get('keyframe_every', 1) or 1)
    state['tsivf_label_stack'] = segment_stack_per_frame(stack, seg, keyframe_every=kf, n_workers=1)
    state['tsivf_stack'] = stack
    print(f'[PyCAT Batch]   TSIVF: segmented {len(stack)} frames.')


def _channels(state, params, shape):
    """{name: stack} for the recorded measured layers that exist for this movie (same shape as the labels)."""
    from pycat.toolbox.invitro.timeseries_kinetics import short_channel_name
    names = params.get('measure_layers') or []
    names = [names] if isinstance(names, str) else list(names)
    picked = {}
    for name in names:
        arr = _resolve_image_layer(state, name, fallback=None)
        if arr is not None and tuple(np.shape(arr)) == tuple(shape):
            picked[short_channel_name(name)] = arr
    if not picked and state.get('tsivf_stack') is not None:
        picked['signal'] = state['tsivf_stack']
    return picked


def replay_tsivf_link_condensates(state: dict, image_path: Path, params: dict, output_dir: Path):
    from pycat.toolbox.invitro.timeseries_kinetics import measure_droplets_multichannel
    from pycat.toolbox.timeseries_invitro_tools import link_condensates
    labels = state.get('tsivf_label_stack')
    if labels is None:
        print(f'[PyCAT Batch]   TSIVF linking skipped for {image_path.name}: no segmentation.')
        return
    channels = _channels(state, params, labels.shape)
    props, dilute = measure_droplets_multichannel(labels, channels, _mpx(state))
    linked, fusions = link_condensates(props, search_radius_scale=params.get('search_radius_scale', 2.0),
                                       max_gap_frames=params.get('max_frame_gap', 2),
                                       area_weight=params.get('area_weight', 0.6),
                                       detect_fusion=params.get('detect_fusion', True))
    minlen = int(params.get('min_track_length', 3) or 1)
    if minlen > 1 and 'track_id' in linked.columns:
        linked = linked[linked.groupby('track_id')['frame'].transform('size') >= minlen].reset_index(drop=True)
    state.update(tsivf_linked=linked, tsivf_fusions=fusions, tsivf_measured_channels=list(channels))
    stem = image_path.stem
    linked.to_csv(output_dir / f'{stem}_tsivf_linked.csv', index=False)
    fusions.to_csv(output_dir / f'{stem}_tsivf_fusions.csv', index=False)
    print(f"[PyCAT Batch]   TSIVF: {linked['track_id'].nunique() if len(linked) else 0} tracks, "
          f"{len(fusions)} fusions.")


def replay_tsivf_object_trajectories(state: dict, image_path: Path, params: dict, output_dir: Path):
    from pycat.toolbox.timeseries_invitro_tools import build_object_records, object_records_to_df
    linked = state.get('tsivf_linked')
    if linked is None or 'track_id' not in linked:
        return
    records = build_object_records(linked, frame_interval_s=_dt(state, params), fusion_df=state.get('tsivf_fusions'))
    object_records_to_df(records).to_csv(output_dir / f'{image_path.stem}_tsivf_object_summary.csv', index=False)


def replay_tsivf_field_trajectories(state: dict, image_path: Path, params: dict, output_dir: Path):
    from pycat.toolbox.timeseries_invitro_tools import field_trajectories
    labels = state.get('tsivf_label_stack')
    intensity = _stack(state, params.get('intensity_layer'))
    if labels is None or intensity is None:
        return
    field_trajectories(labels, intensity, microns_per_pixel=_mpx(state), frame_interval_s=_dt(state, params)) \
        .to_csv(output_dir / f'{image_path.stem}_tsivf_field_trajectories.csv', index=False)


def replay_tsivf_kinetics(state: dict, image_path: Path, params: dict, output_dir: Path):
    import pandas as pd
    from pycat.toolbox.invitro.timeseries_kinetics import (brush_ready_kinetics, droplet_kinetics,
                                                            heterogeneity_over_time, kinetics_heterogeneity)
    from pycat.toolbox.timeseries.cell_pipeline import time_average
    linked = state.get('tsivf_linked')
    measured = state.get('tsivf_measured_channels') or []
    if linked is None or not measured:
        print(f'[PyCAT Batch]   TSIVF kinetics skipped for {image_path.name}: run measure + link first.')
        return
    ch = params.get('channel') if params.get('channel') in measured else measured[0]
    dt = _dt(state, params)
    kin = droplet_kinetics(linked, ch, dt, smooth_frames=int(params.get('smooth_frames', 5)),
                           min_frames=int(params.get('min_frames', 10)))
    stem = image_path.stem
    source = state['data_instance'].data_repository.get('file_path') or str(image_path)
    tables = brush_ready_kinetics(linked, kin, ch, dt, _mpx(state), source)
    tables['droplets'].to_csv(output_dir / f'{stem}_tsivf_kinetics_df.csv', index=False)
    tables['droplet_frames'].to_csv(output_dir / f'{stem}_tsivf_droplet_frames.csv', index=False)
    heterogeneity_over_time(linked, kin, ch).to_csv(output_dir / f'{stem}_tsivf_heterogeneity.csv', index=False)
    pd.DataFrame([{'channel': ch, **kinetics_heterogeneity(kin)}]).to_csv(
        output_dir / f'{stem}_tsivf_kinetics_summary.csv', index=False)
    labels = state.get('tsivf_label_stack')
    stack = state.get('tsivf_stack')
    if labels is not None and stack is not None:
        from pycat.toolbox.timeseries_invitro_tools import relabel_stack_by_track
        tracked = relabel_stack_by_track(labels, linked)
        footprint = np.asarray(tracked).max(axis=0).astype(np.uint32)       # track_id + 1 (0 = none)
        _save_array(time_average(stack), output_dir / f'{stem}_measured_image.tiff')
        _save_array(footprint, output_dir / f'{stem}_droplet_labels.tiff')
    print(f'[PyCAT Batch]   TSIVF kinetics ({ch}): {int(kin["responded"].sum()) if len(kin) else 0} of '
          f'{len(kin)} droplets responded.')
