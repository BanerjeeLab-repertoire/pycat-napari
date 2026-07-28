"""Batch replay handler for VPT (video particle tracking) microrheology.

The headless chain: a bead movie → detect beads per frame → link into trajectories → ensemble MSD →
anomalous-diffusion fit for the diffusion coefficient → Stokes–Einstein viscosity. It replaces the
`vpt_microrheology` skip-stub (spec N2b-1, the 1.6.415 next-steps audit). The whole chain is pure
numpy/scipy/skimage — no torch — so it runs headlessly; the route test proves it recovers a seeded viscosity.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
# NOTE: pandas is imported INSIDE the handler, not at module scope. `batch_step_registry` imports this module,
# and a `core` test that imports the registry must still work in the minimal (numpy-only) lane — a module-scope
# pandas import here breaks that (test_ci_dependencies::test_a_core_test_needs_only_the_minimal_lane_install).


def replay_vpt_microrheology(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Recover the medium viscosity from a bead movie via the VPT chain. Bead radius and temperature come from
    the recorded ``params``; the frame interval and pixel size from the file metadata.

    **SCALE GATE (non-negotiable).** A viscosity in pixel units is meaningless. If the pixel size is the loader's
    1.0 placeholder (not a real, confirmed / metadata value) or the frame interval is unknown, the handler
    REFUSES to emit a number — it writes a verdict row and stashes the verdict (mirroring
    ``replay_client_enrichment``'s calibration-validity stash), never a pixel-unit viscosity.
    """
    from pycat.toolbox.vpt.detection import detect_beads_stack
    from pycat.toolbox.dynamic_spatial_tools import link_trajectories
    from pycat.toolbox.condensate_physics_tools import compute_msd, fit_anomalous_diffusion
    from pycat.toolbox.vpt_tools import viscosity_from_diffusion
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
    if not np.isfinite(dt_s):
        _refuse("no frame interval in the file metadata — MSD lags cannot be put in seconds, so a viscosity "
                "cannot be computed")
        return

    stack = np.asarray(state.get('image'))          # the WHOLE (T, H, W) movie — detect_beads_stack streams it
    if stack.ndim != 3 or stack.shape[0] < 2:
        _refuse("VPT needs a time stack (T, H, W) with at least 2 frames")
        return

    bead_radius_um = float(params.get('bead_radius_um', 0.5))
    temperature_C = float(params.get('temperature_C', 24.0))
    min_track_length = int(params.get('min_track_length', 10))

    beads = detect_beads_stack(stack, microns_per_pixel=mpx, threshold=float(params.get('threshold', 0.02)))
    tracks = link_trajectories(beads, max_displacement_um=float(params.get('max_displacement_um', 2.0)))
    msd = compute_msd(tracks, frame_interval_s=dt_s, min_track_length=min_track_length)
    fit = fit_anomalous_diffusion(msd, frame_interval_s=dt_s)
    eta = viscosity_from_diffusion(fit['D_um2_per_s'], bead_radius_um, temperature_C)

    di = state['data_instance']
    di.set_data('vpt_tracks', tracks)
    di.set_data('vpt_eta_Pa_s', eta)
    state['_vpt_scale_validity'] = {'viscosity_Pa_s': eta, 'scale_valid': True, 'reason': ''}
    n_tracks = int(tracks['track_id'].nunique()) if 'track_id' in getattr(tracks, 'columns', ()) else 0
    row = {'viscosity_Pa_s': eta, 'D_um2_per_s': fit.get('D_um2_per_s'), 'alpha': fit.get('alpha'),
           'motion_type': fit.get('motion_type'), 'r_squared': fit.get('r_squared'), 'n_tracks': n_tracks,
           'bead_radius_um': bead_radius_um, 'temperature_C': temperature_C, 'frame_interval_s': dt_s,
           'microns_per_pixel': mpx, 'scale_valid': True}
    pd.DataFrame([row]).to_csv(output_dir / f"{image_path.stem}_vpt_microrheology.csv", index=False)
    print(f"[PyCAT Batch]   VPT microrheology: eta = {eta:.4g} Pa.s "
          f"(D = {fit.get('D_um2_per_s'):.4g} um2/s, {n_tracks} tracks).")
