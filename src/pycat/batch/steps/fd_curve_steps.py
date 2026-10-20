"""Batch replay of the force-distance panel (fd_curve_ui): load the recorded channels of each C-Trap .h5, unfold the
trace into stretch/relax half-cycles, detect the rips -- through the same operations the panel runs.

The panel's Step 1 picks the file in a dialog; batch processes the file it is given, with the recorded force and
distance channels (auto-selected the same way when the recording has none, or names one this file lacks).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from pycat.kernel import OperationService


def _data_instance(state, image_path):
    di = state.get('data_instance')
    if di is None:
        from pycat.data.data_modules import BaseDataClass
        di = BaseDataClass()
        di.data_repository['file_path'] = str(image_path)
        state['data_instance'] = di
    return di


def replay_fd_load(state: dict, image_path: Path, params: dict, output_dir: Path):
    """``fd_curve_ui._on_load`` / ``_on_rechannel``: the paired force and distance traces of the .h5."""
    from pycat.file_io.frap_io import load_lumicks_fd
    data = load_lumicks_fd(str(image_path), force_channel=params.get('force_channel'),
                           distance_channel=params.get('distance_channel'))
    state.update(fd_force=data['force'], fd_distance=data['distance'], fd_time=data['time_s'])
    _data_instance(state, image_path).data_repository.update(
        fd_path=str(image_path), fd_force=data['force'], fd_distance=data['distance'], fd_time=data['time_s'])
    print(f"[PyCAT Batch]   FD trace: {len(data['force'])} samples, F='{data['force_channel']}', "
          f"d='{data['distance_channel']}'.")


def replay_fd_segment(state: dict, image_path: Path, params: dict, output_dir: Path):
    """``fd_curve_ui._on_segment``: optional Savitzky-Golay smoothing, then the half-cycle split."""
    force, dist = state.get('fd_force'), state.get('fd_distance')
    if force is None or dist is None:
        print(f"[PyCAT Batch]   FD segmentation skipped for {image_path.name}: no trace (the load step did not run).")
        return
    f = np.asarray(force, dtype=float)
    if params.get('smoothed'):
        f = OperationService.produce('fd_curve.smooth_force', {'force': f},
                                     {'window_length': int(params.get('smooth_window', 11)), 'polyorder': 3})
    seg = OperationService.produce('fd_curve.segment_cycles', {'force': f, 'distance': dist}, {
        'min_segment': int(params.get('min_segment', 20)),
        'smooth_window': int(params.get('distance_smooth_window', 11))})
    state.update(fd_segments=seg, fd_force_used=f)
    _data_instance(state, image_path).data_repository.update(fd_segments=seg, fd_force_used=f)
    summ = OperationService.produce('fd_curve.summarise_cycles', {'seg_result': seg})
    summ.to_csv(output_dir / f"{image_path.stem}_fd_cycles.csv", index=False)
    print(f"[PyCAT Batch]   FD: {len(seg['stretches'])} stretches, {len(seg['relaxes'])} relaxes.")


def replay_fd_rips(state: dict, image_path: Path, params: dict, output_dir: Path):
    """``fd_curve_ui._on_rips``: a WLC fit per stretch, rips where the force drops by more than the threshold."""
    seg = state.get('fd_segments')
    if seg is None:
        print(f"[PyCAT Batch]   FD rips skipped for {image_path.name}: no half-cycles (segmentation did not run).")
        return
    rips = OperationService.produce('fd_curve.rips', {'seg_result': seg}, {
        'which': 'stretches', 'min_force_drop_pN': float(params.get('min_force_drop_pN', 3.0)),
        'kuhn_length_nm': float(params.get('kuhn_length_nm', 1.5)),
        'stretch_modulus_pN': float(params.get('stretch_modulus_pN', 800.0)),
        'rise_nm_per_nt': float(params.get('rise_nm_per_nt', 0.59))})
    _data_instance(state, image_path).set_data('fd_rips', rips)
    rips.to_csv(output_dir / f"{image_path.stem}_fd_rips.csv", index=False)
    print(f"[PyCAT Batch]   FD: {len(rips)} rips.")
