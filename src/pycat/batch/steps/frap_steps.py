"""Batch replay of the FRAP panel (frap_ui): the ROIs the user drew, then the recovery analysis, through the same
operations the panel runs.

A recording carries the ROI GEOMETRY (shape vertices, or circle centres and radius) since 2026-10-19, so batch
measures the same pixels on every file -- right for a fixed bleach position (a stage-registered acquisition series),
and stated in the log. A recording made before then has no geometry, and the step says so instead of guessing.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np

from pycat.kernel import OperationService


def _recovery_stack(state, image_path, params):
    stack = state.get('frap_stack')
    if stack is not None:
        return stack
    from pycat.batch.steps._common import _session_time_stack
    stack = _session_time_stack(state, image_path, int(params.get('channel', 0) or 0))
    state['frap_stack'] = stack
    return stack


def replay_frap_define_roi(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Rebuild the recorded bleach / reference ROIs as masks on this file's frame (``frap_ui._build_masks``)."""
    from pycat.toolbox.frap_tools import circular_mask, masks_from_shapes, masks_from_shapes_multi
    stack = _recovery_stack(state, image_path, params)
    if stack is None:
        print(f"[PyCAT Batch]   FRAP ROI skipped for {image_path.name}: not a (T, H, W) recovery series.")
        return
    shape = stack.shape[1:]
    mode = params.get('roi_mode')
    if 'shapes' in params:
        layer = SimpleNamespace(data=[np.asarray(s['vertices'], float) for s in params['shapes']],
                                shape_type=[s['type'] for s in params['shapes']])
        if mode == 'draw_multi':
            state['frap_masks'] = ('multi',) + tuple(masks_from_shapes_multi(
                layer, shape, n_reference=int(params.get('n_reference', 1))))
        else:
            state['frap_masks'] = ('single',) + tuple(masks_from_shapes(layer, shape))
    elif 'bleach_center' in params:
        r = float(params['radius'])
        state['frap_masks'] = ('single', circular_mask(shape, tuple(params['bleach_center']), r),
                               circular_mask(shape, tuple(params['reference_center']), r))
    else:
        print(f"[PyCAT Batch]   FRAP ROI skipped for {image_path.name}: this recording predates ROI geometry "
              "being recorded (2026-10-19) -- re-record the session to replay it.")
        return
    print(f"[PyCAT Batch]   FRAP ROIs: the recorded {mode} geometry, at the same pixels on {image_path.name}.")


def replay_frap_analysis(state: dict, image_path: Path, params: dict, output_dir: Path):
    """The recovery analysis with the recorded normalisation, model and timing (``frap_ui._on_analyze``)."""
    import pandas as pd
    masks = state.get('frap_masks')
    stack = state.get('frap_stack')
    if masks is None or stack is None:
        print(f"[PyCAT Batch]   FRAP analysis skipped for {image_path.name}: no ROIs (the define-ROI step did "
              "not run).")
        return
    kind, bleach, ref = masks
    photofade = bool(params.get('photofading', True))
    if photofade and ref is None:
        print(f"[PyCAT Batch]   FRAP analysis skipped for {image_path.name}: photofading correction was on but "
              "the recording has no reference ROI.")
        return
    common = {'reference_mask': ref if photofade else None,
              'frame_interval_s': float(params.get('frame_interval_s', 1.0)),
              'time_lag_s': float(params.get('time_lag_s', 0.0)),
              'n_spline_points': int(params.get('n_spline_points', 30)),
              'normalization': params.get('normalization', 'taylor')}
    if kind == 'multi':
        multi = OperationService.produce('frap.analysis_multi', {'recovery_stack': stack, 'bleach_masks': bleach},
                                         common)
        state['data_instance'].set_data('frap_multi_result', multi)
        multi['summary_df'].to_csv(output_dir / f"{image_path.stem}_frap_multi_summary.csv", index=False)
        for i, res in enumerate(multi.get('per_roi') or []):
            if isinstance(res, dict) and isinstance(res.get('results_df'), pd.DataFrame):
                res['results_df'].to_csv(output_dir / f"{image_path.stem}_frap_curve_roi{i + 1}.csv", index=False)
        print(f"[PyCAT Batch]   FRAP: {len(bleach)} bleach ROIs analysed.")
        return
    prebleach = None
    if params.get('prebleach_stack') and params['prebleach_stack'] == params.get('recovery_stack'):
        prebleach = stack                     # the session took I_pre from the recovery stack itself
    elif params.get('prebleach_stack'):
        print(f"[PyCAT Batch]   FRAP analysis skipped for {image_path.name}: the session used a separate "
              f"pre-bleach stack ('{params['prebleach_stack']}'), which batch cannot locate -- without it the "
              "normalisation would differ from the panel's.")
        return
    fit_model = params.get('fit_model', 'empirical')
    roi_dims_um, bleach_radius_um = OperationService.produce(
        'frap.fit_geometry', {'bleach_mask': bleach, 'fit_model': fit_model},
        {'pixel_um': float(params.get('pixel_um', 0.1)), 'entered_radius_um': float(params.get('entered_radius_um',
                                                                                                    1.0))})
    result = OperationService.produce('frap.analysis', {'recovery_stack': stack, 'bleach_mask': bleach}, {
        **common, 'prebleach_stack': prebleach, 'fit_model': fit_model, 'roi_dims_um': roi_dims_um,
        'fit_koff': bool(params.get('fit_koff', True)), 'bleach_radius_um': bleach_radius_um})
    state['data_instance'].set_data('frap_result', result)
    result['results_df'].to_csv(output_dir / f"{image_path.stem}_frap_curve.csv", index=False)
    fit = result['fit']
    row = {'tau_half_s': fit.get('half_time_s'), 'mobile_fraction': fit.get('mobile_fraction'),
           'immobile_fraction': fit.get('immobile_fraction'), 'r_squared': fit.get('r_squared'),
           'intensity_0': result.get('intensity_0'), 'prebleach': result.get('prebleach'),
           'normalization': common['normalization'], 'fit_model': fit_model}
    for key in ('rd_fit', 'circ_fit'):
        if result.get(key):
            row.update({f'{key}_{k}': v for k, v in result[key].items() if np.isscalar(v)})
    pd.DataFrame([row]).to_csv(output_dir / f"{image_path.stem}_frap_summary.csv", index=False)
    print(f"[PyCAT Batch]   FRAP: tau_half = {fit.get('half_time_s')}, mobile = {fit.get('mobile_fraction')}.")
