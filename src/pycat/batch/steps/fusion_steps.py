"""Batch replay of the droplet-fusion panel (fusion_ui): build the fusion signal, then fit its relaxation, through
the same operations the panel runs.

Force mode reads the C-Trap .h5 being processed (the panel's Step 1 loads it and records nothing, so batch loads
each file itself); image mode reads the file's movie. The fit window is re-suggested on each file's own signal when
the session used the suggested one, and reused only when it was set by hand.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from pycat.kernel import OperationService


def _data_instance(state, image_path):
    """The file's data instance. A force-mode recording has no open step (the panel loads the .h5 itself), so the
    first fusion step makes one, with the file path stamped as the open step would."""
    di = state.get('data_instance')
    if di is None:
        from pycat.data.data_modules import BaseDataClass
        di = BaseDataClass()
        di.data_repository['file_path'] = str(image_path)
        state['data_instance'] = di
    return di


def replay_fusion_build_signal(state: dict, image_path: Path, params: dict, output_dir: Path):
    """``fusion_ui._on_build_signal``: a force channel of the .h5, or the aspect ratio of the droplets in the movie."""
    import pandas as pd
    mode = params.get('mode')
    di = _data_instance(state, image_path)
    details = None
    if mode == 'force':
        from pycat.file_io.frap_io import load_lumicks_fusion
        data = load_lumicks_fusion(str(image_path))
        sr = params.get('sample_rate_hz') or data.get('sample_rate_hz')
        time, sig = OperationService.produce('fusion.force_signal', {'force_traces': data['forces']},
                                             {'which': params.get('channel'), 'sample_rate_hz': sr})
    elif mode == 'image':
        from pycat.batch.steps._common import _session_time_stack
        stack = _session_time_stack(state, image_path, int(params.get('channel_index', 0) or 0))
        if stack is None:
            print(f"[PyCAT Batch]   Fusion signal skipped for {image_path.name}: not a (T, H, W) movie.")
            return
        time, sig, details = OperationService.produce('fusion.aspect_ratio_signal', {'stack': stack}, {
            'threshold_method': params.get('threshold_method', 'auto'),
            'frame_interval_s': float(params.get('frame_interval_s', 1.0)), 'return_details': True})
    else:
        print(f"[PyCAT Batch]   Fusion signal skipped for {image_path.name}: this recording predates the signal "
              "source being recorded (2026-10-19) -- re-record the session to replay it.")
        return
    state.update(fusion_time=time, fusion_signal=sig, fusion_image_details=details)
    di.data_repository.update(fusion_time=time, fusion_signal=sig, fusion_image_details=details)
    pd.DataFrame({'time_s': time, 'signal': sig}).to_csv(output_dir / f"{image_path.stem}_fusion_signal.csv",
                                                         index=False)
    print(f"[PyCAT Batch]   Fusion signal ({mode}): {len(sig)} samples.")


def replay_fusion_fit(state: dict, image_path: Path, params: dict, output_dir: Path):
    """``fusion_ui._on_fit``: S(t) = a·exp(−t/τ) + b·t + d over the window, with the droplet radii in image mode."""
    import pandas as pd
    time, sig = state.get('fusion_time'), state.get('fusion_signal')
    if time is None or sig is None:
        print(f"[PyCAT Batch]   Fusion fit skipped for {image_path.name}: no signal (the build step did not run).")
        return
    source = params.get('window_source', 'manual')
    if source == 'suggested':
        win = OperationService.produce('fusion.fit_window', {'time': time, 'signal': sig})
        t_start, t_end = ((win['t_start'], win['t_end']) if np.isfinite(win['t_start'])
                          else (float(np.nanmin(time)), float(np.nanmax(time))))
        if params.get('window_decimals') is not None:       # as the panel's spin boxes round it
            t_start, t_end = round(t_start, int(params['window_decimals'])), round(t_end, int(params['window_decimals']))
    elif source == 'whole_trace':
        t_start = t_end = None
    else:
        t_start, t_end = params.get('t_start'), params.get('t_end')
    if t_start is not None and t_end is not None and t_end <= t_start:
        t_start = t_end = None
    fit = OperationService.produce('fusion.fit_relaxation', {'time': time, 'signal': sig},
                                   {'t_start': t_start, 't_end': t_end})
    row = {k: fit.get(k) for k in ('tau_s', 'a', 'b', 'd', 'r_squared', 't_start', 't_end')}
    row['window_source'] = source
    details = state.get('fusion_image_details')
    di = _data_instance(state, image_path)
    if details and fit.get('tau_s') == fit.get('tau_s'):
        from pycat.utils.pixel_size import pixel_size_um_or_default
        mpx = pixel_size_um_or_default(di.data_repository, context='fusion_ui')
        r = OperationService.produce('fusion.droplet_radii', {
            'details': details, 'onset_index': int(np.searchsorted(np.asarray(time), fit['t_start'])),
            'microns_per_pixel': float(mpx)})
        row.update(R1_um=r['R1_um'], R2_um=r['R2_um'], R_fused_um=r['R_fused_um'])
        if r['R_mean_um'] == r['R_mean_um'] and r['R_mean_um'] > 0:
            row['tau_over_R_s_per_um'] = fit['tau_s'] / r['R_mean_um']
    di.set_data('fusion_fit', fit)
    pd.DataFrame([row]).to_csv(output_dir / f"{image_path.stem}_fusion_fit.csv", index=False)
    if len(fit.get('fit_time', ())):
        pd.DataFrame({'time_s': fit['fit_time'], 'fit': fit['fit_curve']}).to_csv(
            output_dir / f"{image_path.stem}_fusion_fit_curve.csv", index=False)
    print(f"[PyCAT Batch]   Fusion fit: tau = {fit.get('tau_s')} s, R2 = {fit.get('r_squared')}.")
