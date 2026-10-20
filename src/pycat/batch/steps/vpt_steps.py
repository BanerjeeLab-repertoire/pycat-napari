"""Batch replay handler for VPT (video particle tracking) microrheology.

The headless chain: a bead movie → detect beads per frame → link into trajectories → ensemble MSD →
anomalous-diffusion fit for the diffusion coefficient → Stokes–Einstein viscosity. It replaces the
`vpt_microrheology` skip-stub (spec N2b-1, the 1.6.415 next-steps audit). The whole chain is pure
numpy/scipy/skimage — no torch — so it runs headlessly; the route test proves it recovers a seeded viscosity.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from pycat.kernel import OperationService
# NOTE: pandas is imported INSIDE the handler, not at module scope. `batch_step_registry` imports this module,
# and a `core` test that imports the registry must still work in the minimal (numpy-only) lane — a module-scope
# pandas import here breaks that (test_ci_dependencies::test_a_core_test_needs_only_the_minimal_lane_install).


def _bead_stack(state, image_path):
    """The whole (T, H, W) bead movie in raw counts, as the panel's layer holds it: the opened stack, or -- when
    the session opened it with ``open_image`` (one plane) -- the file's every frame."""
    from pycat.batch.steps._common import _session_time_stack
    return _session_time_stack(state, image_path, 0)


def _vpt_mpx(state):
    from pycat.utils.pixel_size import pixel_size_um_or_default
    return pixel_size_um_or_default(state['data_instance'].data_repository, context='vpt_ui')


def replay_vpt_segment_host(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Step 2 (host channel): threshold the recorded host layer, erode the interface (``vpt_ui._on_segment_host``)."""
    from pycat.batch.steps._common import _resolve_image_layer
    img = _resolve_image_layer(state, params.get('host_channel'), fallback=None)
    if img is None:
        print(f"[PyCAT Batch]   VPT host segmentation skipped for {image_path.name}: the recorded host layer "
              f"'{params.get('host_channel')}' is not in this file.")
        return
    labeled = OperationService.produce('host_segment', {'host_image': np.asarray(img)},
                                       {'method': params.get('method', 'otsu')})
    eroded = OperationService.produce('host_erode', {'labeled_mask': labeled},
                                      {'erosion_px': params.get('erosion_px', 5)})
    state['vpt_host_mask'] = eroded
    state['data_instance'].data_repository['vpt_host_mask'] = eroded
    print(f"[PyCAT Batch]   VPT host: {int(eroded.max())} condensate(s) after erosion.")


def replay_vpt_infer_host(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Step 2 (infer): the host inferred from where the beads are, on 8 key frames (``_infer_host_from_beads``)."""
    from pycat.toolbox.vpt_tools import infer_host_from_beads
    stack = _bead_stack(state, image_path)
    if stack is None:
        print(f"[PyCAT Batch]   VPT host inference skipped for {image_path.name}: not a time stack.")
        return
    n_key = 8
    key_idx = (np.unique(np.linspace(0, stack.shape[0] - 1, n_key).astype(int)) if stack.shape[0] > n_key
               else None)
    det = OperationService.produce('bead_detect', {'bead_stack': stack, 'host_mask': None}, {
        'min_sigma': params.get('min_sigma', 1.0), 'max_sigma': params.get('max_sigma', 5.0),
        'threshold': params.get('threshold', 0.02), 'microns_per_pixel': 1.0, 'fit_quality': False,
        'frame_indices': key_idx})
    if det.empty:
        print(f"[PyCAT Batch]   VPT host inference skipped for {image_path.name}: no beads detected.")
        return
    labeled = infer_host_from_beads(det[['y_um', 'x_um']].values, stack.shape[-2:],
                                    microns_per_pixel=_vpt_mpx(state),
                                    min_condensate_radius_um=params.get('min_condensate_radius_um', 5.0))
    eroded = OperationService.produce('host_erode', {'labeled_mask': labeled},
                                      {'erosion_px': params.get('erosion_px', 5)})
    state['vpt_host_mask'] = eroded
    state['data_instance'].data_repository['vpt_host_mask'] = eroded
    print(f"[PyCAT Batch]   VPT inferred host: {int(eroded.max())} condensate(s).")


def replay_vpt_detect_beads(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Step 3: detect beads in every frame, with the recorded knobs, inside the host mask unless the recorded mode
    is full-frame (``vpt_ui._on_detect_beads``)."""
    stack = _bead_stack(state, image_path)
    if stack is None:
        print(f"[PyCAT Batch]   VPT bead detection skipped for {image_path.name}: not a time stack.")
        return
    mode = params.get('host_mode', 'nohost' if state.get('vpt_host_mask') is None else 'host')
    host_mask = None if mode == 'nohost' else state.get('vpt_host_mask')
    if mode != 'nohost' and host_mask is None:
        print(f"[PyCAT Batch]   VPT bead detection skipped for {image_path.name}: host mode '{mode}' needs the "
              "host mask from step 2, which did not run.")
        return
    det = OperationService.produce('bead_detect', {'bead_stack': stack, 'host_mask': host_mask}, {
        'min_sigma': params.get('min_sigma', 1.0), 'max_sigma': params.get('max_sigma', 5.0),
        'threshold': params.get('threshold', 0.02), 'microns_per_pixel': _vpt_mpx(state),
        'quality_mode': params.get('quality_mode', 'fast'), 'subpixel': params.get('subpixel', True),
        'template_mode': params.get('template_mode', 'per_stack'), 'bead_size_nm': params.get('bead_size_nm'),
        'template_type': params.get('template_type', 'empirical'), 'merge_radius_px': params.get('merge_radius_px'),
        'strictness': params.get('strictness', 1.0), 'exclude_aggregates': False, 'recover_out_of_plane': True})
    state['vpt_detections'] = det
    state['data_instance'].data_repository['vpt_detections'] = det
    det.to_csv(output_dir / f"{image_path.stem}_vpt_detections.csv", index=False)
    n_frames = det['frame'].nunique() if len(det) else 0
    print(f"[PyCAT Batch]   VPT beads: {len(det)} detections in {n_frames} frames.")


def replay_vpt_link_trajectories(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Step 4: link the recorded population into trajectories, drift beside the centroids, aggregates apart
    (``vpt_ui._on_link`` -> ``vpt.link_beads``)."""
    det = state.get('vpt_detections')
    if det is None or det.empty:
        print(f"[PyCAT Batch]   VPT linking skipped for {image_path.name}: no bead detections.")
        return
    population = params.get('microrheology_population', 'singlet') if 'bead_class' in det.columns else 'singlet'
    res = OperationService.produce('vpt.link_beads', {'detections': det}, {
        'linker': params.get('linker', 'trackmate'),
        'max_linking_distance_um': params.get('max_linking_distance_um', 1.0),
        'max_frame_gap': params.get('max_frame_gap', 2), 'microns_per_pixel': _vpt_mpx(state),
        'population': population, 'promote_stable_to_singlet': params.get('promote_stable_to_singlet', True)})
    tracks = res['primary']
    tracks = tracks[tracks['track_id'] != -1] if 'track_id' in tracks else tracks
    di = state['data_instance']
    state['vpt_tracks'] = tracks
    di.set_data('vpt_tracks', tracks)
    tracks.to_csv(output_dir / f"{image_path.stem}_vpt_tracks.csv", index=False)
    atracks = res.get('aggregate_tracks')
    if atracks is not None and not atracks.empty and 'track_id' in atracks:
        atracks = atracks[atracks['track_id'] != -1]
        di.set_data('vpt_aggregate_tracks', atracks)
        atracks.to_csv(output_dir / f"{image_path.stem}_vpt_aggregate_tracks.csv", index=False)
    n = int(tracks['track_id'].nunique()) if 'track_id' in tracks else 0
    print(f"[PyCAT Batch]   VPT linking: {n} trajectories from the {population} population.")


def replay_vpt_microrheology(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Recover the medium viscosity from a bead movie via the VPT chain. Bead radius and temperature come from
    the recorded ``params``; the frame interval and pixel size from the file metadata.

    **SCALE GATE (non-negotiable).** A viscosity in pixel units is meaningless. If the pixel size is the loader's
    1.0 placeholder (not a real, confirmed / metadata value) or the frame interval is unknown, the handler
    REFUSES to emit a number — it writes a verdict row and stashes the verdict (mirroring
    ``replay_client_enrichment``'s calibration-validity stash), never a pixel-unit viscosity.
    """
    from pycat.utils.pixel_size import pixel_size_um, has_real_pixel_size
    from pycat.utils.frame_interval import frame_interval_s
    import pandas as pd

    repo = state['data_instance'].data_repository

    def _refuse(reason):
        verdict = {'viscosity_Pa_s': float('nan'), 'scale_valid': False, 'reason': reason}
        state['_vpt_scale_validity'] = verdict
        pd.DataFrame([verdict]).to_csv(output_dir / f"{image_path.stem}_vpt_microrheology.csv", index=False)
        print(f"[PyCAT Batch]   VPT microrheology REFUSED for {image_path.name}: {reason}")

    mpx = pixel_size_um(repo, context='vpt_microrheology')
    if not has_real_pixel_size(repo) or not np.isfinite(mpx):
        _refuse("no real pixel size (microns_per_pixel is the 1.0 loader placeholder) — a viscosity in pixel "
                "units is meaningless; set the pixel size and re-run")
        return
    dt_s = frame_interval_s(repo, context='vpt_microrheology')
    if not np.isfinite(dt_s) and params.get('frame_interval_s'):
        # The panel's Step 5 interval: synced from the metadata when the file has one, otherwise the value the user
        # entered -- and the recording carries it. Used only when THIS file's metadata has none.
        dt_s = float(params['frame_interval_s'])
        print(f"[PyCAT Batch]   VPT: no frame interval in {image_path.name}'s metadata -- using the recorded "
              f"{dt_s:g} s the panel ran with.")
    if not np.isfinite(dt_s):
        _refuse("no frame interval in the file metadata (and none recorded) — MSD lags cannot be put in "
                "seconds, so a viscosity cannot be computed")
        return

    bead_radius_um = float(params.get('bead_radius_um', 0.5))
    temperature_C = float(params.get('temperature_C', 24.0))
    min_track_length = int(params.get('min_track_length', 10))

    linked = state.get('vpt_tracks')
    if linked is not None:
        # The recorded chain ran (steps 2-4): the tracks it linked, as the panel's step 5 uses them -- drift
        # subtracted for the MSD only when the recording applied it; the stored tracks keep the centroids.
        tracks = linked
        drift = OperationService.produce('vpt.drift', {'tracks_df': tracks, 'microns_per_pixel': mpx})
        msd_tracks = (OperationService.produce('vpt.drift_corrected', {'tracks_df': tracks})
                      if params.get('drift_mode') == 'measured' else tracks)
    else:
        # A recording with only this step: the self-contained chain from the raw bead movie.
        stack = np.asarray(state.get('image'))      # the WHOLE (T, H, W) movie — detect_beads_stack streams it
        if stack.ndim != 3 or stack.shape[0] < 2:
            _refuse("VPT needs a time stack (T, H, W) with at least 2 frames")
            return
        beads = OperationService.produce('bead_detect', {'bead_stack': stack},
            {'microns_per_pixel': mpx, 'threshold': float(params.get('threshold', 0.02))})
        tracks = OperationService.produce('dynamic_spatial.link_trajectories', {'props_df': beads},
            {'max_displacement_um': float(params.get('max_displacement_um', 2.0))})
        # Same rule as the panel: drift is measured from the beads and subtracted for the MSD only when it is
        # real (and not when the recording says the user switched it off). The stored tracks keep the centroids.
        tracks, msd_tracks, drift = OperationService.produce(
            'vpt.measured_drift', {'tracks_df': tracks, 'microns_per_pixel': mpx})
        if params.get('drift_mode') == 'none':
            msd_tracks = tracks
    msd = OperationService.execute('condensate_physics.compute_msd', {'tracks': msd_tracks}, {
        'frame_interval_s': dt_s, 'min_track_length': min_track_length}).measurements
    fit = OperationService.produce('condensate_physics.fit_anomalous_diffusion', {'msd_df': msd}, {
        'frame_interval_s': dt_s, 'upper_lag_rule': params.get('upper_lag_rule', 'fraction'),
        'upper_lag_fraction': params.get('upper_lag_fraction', 0.25),
        'upper_lag_fixed_s': params.get('upper_lag_fixed_s'),
        'min_independent_pairs': params.get('min_independent_pairs', 10),
        'confine_to_defensible_bounds': params.get('confine_to_defensible_bounds', True)})
    eta = OperationService.produce('viscosity', {'D_um2_per_s': fit['D_um2_per_s']},
        {'bead_radius_um': bead_radius_um, 'temperature_C': temperature_C})

    di = state['data_instance']
    di.set_data('vpt_tracks', tracks)
    di.set_data('vpt_eta_Pa_s', eta)
    state['_vpt_scale_validity'] = {'viscosity_Pa_s': eta, 'scale_valid': True, 'reason': ''}
    n_tracks = int(tracks['track_id'].nunique()) if 'track_id' in getattr(tracks, 'columns', ()) else 0
    row = {'viscosity_Pa_s': eta, 'D_um2_per_s': fit.get('D_um2_per_s'), 'alpha': fit.get('alpha'),
           'motion_type': fit.get('motion_type'), 'r_squared': fit.get('r_squared'), 'n_tracks': n_tracks,
           'bead_radius_um': bead_radius_um, 'temperature_C': temperature_C, 'frame_interval_s': dt_s,
           'microns_per_pixel': mpx, 'scale_valid': True, 'drift_real': drift['real'],
           'drift_peak_px': drift['peak_px'], 'drift_applied': msd_tracks is not tracks}
    pd.DataFrame([row]).to_csv(output_dir / f"{image_path.stem}_vpt_microrheology.csv", index=False)
    print(f"[PyCAT Batch]   VPT microrheology: eta = {eta:.4g} Pa.s "
          f"(D = {fit.get('D_um2_per_s'):.4g} um2/s, {n_tracks} tracks).")
