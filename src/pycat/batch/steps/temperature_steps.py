"""Batch replay of the temperature-ramp panel (temperature_ui): sync every frame to the temperature log, then the
entropy turbidity curve and its heating/cooling transitions -- through the same operations the panel runs.

Each TIFF's per-frame times come from its own MicroManager metadata; its temperature CSV is found the way the
panel's own folder batch finds it -- by the TIFF's date, in the recorded temperature folder -- or is the recorded
CSV when the session pointed at one file.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from pycat.kernel import OperationService


def _temperature_stack(state, image_path):
    stack = state.get('temp_stack')
    if stack is None:
        from pycat.batch.steps._common import _session_time_stack
        stack = _session_time_stack(state, image_path, 0)
        state['temp_stack'] = stack
    return stack


def replay_temperature_sync(state: dict, image_path: Path, params: dict, output_dir: Path):
    """``temperature_ui._on_sync``: the temperature at each frame's received time."""
    from pycat.toolbox.temperature_tools import elapsed_to_seconds, locate_temperature_csv, read_micromanager_times
    csv = None
    root = params.get('temperature_root')
    if params.get('csv_path') and os.path.isfile(params['csv_path']):      # the session named one CSV file
        csv = params['csv_path']
    elif root and os.path.isdir(root):
        csv = locate_temperature_csv(str(image_path), root)
    if not csv:
        print(f"[PyCAT Batch]   Temperature sync skipped for {image_path.name}: no temperature CSV for this "
              f"file's date in '{root}'.")
        return
    times = read_micromanager_times(str(image_path))
    temps = OperationService.produce(
        'temperature.sync_temperatures', {'received_times': times['received'], 'csv_path': csv},
        {'temp_column': params.get('temp_column') or 'AI0 (°C)', 'csv_header': int(params.get('csv_header', 6))})
    state['temp_temperatures'] = temps
    state['temp_elapsed_s'] = elapsed_to_seconds(times['elapsed_ms'])
    di = state.get('data_instance')
    if di is not None:
        di.data_repository.update(temp_temperatures=temps, temp_elapsed_s=state['temp_elapsed_s'])
    print(f"[PyCAT Batch]   Temperature sync: {int(np.isfinite(temps).sum())}/{times['n_frames']} frames matched "
          f"({os.path.basename(csv)}).")


def replay_temperature_turbidity(state: dict, image_path: Path, params: dict, output_dir: Path):
    """``temperature_ui._on_turbidity``: the entropy turbidity curve and the cloud / clear points."""
    import pandas as pd
    temps = state.get('temp_temperatures')
    if temps is None:
        print(f"[PyCAT Batch]   Turbidity skipped for {image_path.name}: no temperatures (the sync step did not run).")
        return
    stack = _temperature_stack(state, image_path)
    if stack is None:
        print(f"[PyCAT Batch]   Turbidity skipped for {image_path.name}: not a (T, H, W) stack.")
        return
    focus = bool(params.get('correct_focal_drift', False))
    df = OperationService.produce('temperature.turbidity', {'stack': stack, 'temperatures': temps}, {
        'subtract_first_frame': bool(params.get('subtract_first', False)), 'correct_focal_drift': focus,
        'bins': int(params.get('bins', 256)), 'reference_frame_index': int(params.get('reference_frame_index', 0))})
    sig_col = 'entropy_corrected' if focus else 'entropy'
    method = params.get('method', 'baseline')
    trans = OperationService.produce('temperature.detect_transitions', {'turbidity_df': df, 'signal_column': sig_col},
                                     {'method': method, 'frac': float(params.get('onset_frac', 0.12))})
    curve = df.drop(columns=['focus_score'], errors='ignore')
    curve.to_csv(output_dir / f"{image_path.stem}_temperature_turbidity.csv", index=False)
    pd.DataFrame([{'T_cloud_C': trans['T_phase_C'], 'T_clear_C': trans['T_clear_C'],
                   'hysteresis_C': trans['hysteresis_C'], 'cloud_branch': trans.get('cloud_branch'),
                   'clear_branch': trans.get('clear_branch'), 'method': method, 'signal': sig_col}]).to_csv(
        output_dir / f"{image_path.stem}_temperature_transitions.csv", index=False)
    di = state.get('data_instance')
    if di is not None:
        di.data_repository.update(temp_turbidity_df=curve, temp_transitions=trans)
    print(f"[PyCAT Batch]   Turbidity: T_cloud = {trans['T_phase_C']}, T_clear = {trans['T_clear_C']} °C.")
