"""Batch replay handlers (analysis steps), moved from batch_step_registry.py (decomposition, 1.6.150).
Handlers unchanged; each has signature (state, image_path, params, output_dir). The _STEP_MAP dispatch
table stays in batch_step_registry.py and imports these."""
from __future__ import annotations

from __future__ import annotations
import traceback
from pathlib import Path
from typing import TYPE_CHECKING
import numpy as np
from pycat.file_io.image_reader import open_image
from pycat.batch.steps._common import (
    _get_data, _derive_split_companion_path, _source_path_for_recorded_channel, _load_image, _resolve_channel_for_layer, _save_array, _raw_counts, _normalize_to_float, _resolve_image_layer, _ivf_droplet_mask_and_image)


def replay_condensate_analysis(state: dict, image_path: Path, params: dict, output_dir: Path):
    """
    Run puncta_analysis_func directly (inner logic of run_puncta_analysis_func,
    no viewer or Qt dialog calls).
    """
    from pycat.toolbox.feature_analysis_tools import puncta_analysis_func

    if state.get('no_cells'):
        print(f"[PyCAT Batch]   Condensate analysis skipped for "
              f"{image_path.name}: no cells were segmented upstream.")
        return
    # A MISSING puncta_mask is different from an empty one: it means condensate segmentation did not run.
    # Report that as an error (below) rather than silently producing nothing — the segmentation must run first.

    # Measure puncta intensity on the layer the GUI recorded (image_layer),
    # e.g. "Upscaled Fluorescence Image".
    image = _resolve_image_layer(
        state, params.get('image_layer'),
        fallback=state.get('fluorescence_image', state['image']))
    data_instance = state['data_instance']
    puncta_mask = state.get('puncta_mask')
    labeled_cells = state.get('labeled_cells')

    if puncta_mask is None:
        raise RuntimeError("condensate_analysis requires condensate_segmentation to run first.")

    if labeled_cells is None:
        labeled_cells = np.ones_like(image).astype(int)
        labeled_cells[0:2, 0:2] = 0

    cell_labeled_puncta = puncta_analysis_func(
        puncta_mask, image, labeled_cells, data_instance
    )

    # Retrieve the DataFrames written into data_instance by puncta_analysis_func
    cell_df = data_instance.data_repository.get('cell_df')
    puncta_df = data_instance.data_repository.get('puncta_df')

    _save_array(cell_labeled_puncta.astype(np.uint16),
                output_dir / f"{image_path.stem}_cell_labeled_puncta.tiff")

    if cell_df is not None:
        cell_df.to_csv(output_dir / f"{image_path.stem}_cell_df.csv", index=False)
    if puncta_df is not None:
        puncta_df.to_csv(output_dir / f"{image_path.stem}_puncta_df.csv", index=False)

    print("[PyCAT Batch]   Condensate analysis done.")


def replay_measure_line(state: dict, image_path: Path, params: dict, output_dir: Path):
    """
    Apply the object/cell measurements the user made with the Measure Line
    tool in the GUI.

    These values (cell_diameter, ball_radius, object_size) are captured at the
    moment Measure Line was clicked and are what every downstream step in the
    GUI used from then on. They intentionally OVERRIDE the placeholder values
    recorded at open_image time. This step runs *before* upscaling, so the
    values written here are the pre-upscale measurements; replay_upscaling then
    doubles cell_diameter and ball_radius exactly as the GUI does.

    (Previously this was a no-op, which left the stale open_image ball_radius in
    place — after upscaling that produced an enormous rolling-ball structuring
    element and a MemoryError in condensate segmentation, and gave Cellpose the
    wrong cell diameter.)

    EXCEPTION: ``ball_radius`` is skipped when this batch has per-image
    auto-estimation active (``state['_auto_ball_radius']``) — Measure Line's
    recorded value is a SINGLE measurement from whichever image the user
    happened to be looking at during the original recording session, applied
    identically to every file in the batch; blindly reapplying it here would
    silently discard the per-image estimate that ``replay_open_image`` /
    ``_finalize_ball_radius`` just computed for THIS file, for the exact reason
    a recorded ``open_image.ball_radius`` isn't allowed to either (see
    ``BatchWorker._auto_ball_radius_active``). ``cell_diameter``/``object_size``
    have no competing per-image estimate, so they still apply unconditionally.
    """
    data_instance = state['data_instance']
    applied = []
    skipped_ball_radius = False
    for key in ('cell_diameter', 'ball_radius', 'object_size'):
        val = params.get(key)
        if val is None:
            continue
        if key == 'ball_radius' and state.get('_auto_ball_radius'):
            skipped_ball_radius = True
            continue
        data_instance.data_repository[key] = val
        applied.append(f"{key}={val}")

    if applied:
        print(f"[PyCAT Batch]   Measure Line applied recorded measurements: "
              f"{', '.join(applied)}"
              + ("  (ball_radius left at the per-image auto-estimate)."
                 if skipped_ball_radius else "."))
    elif skipped_ball_radius:
        print("[PyCAT Batch]   Measure Line: ball_radius left at the per-image "
              "auto-estimate; no other recorded measurements to apply.")
    else:
        print("[PyCAT Batch]   Measure Line: no recorded measurements to apply "
              "(using open_image values).")


def replay_cell_analysis(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Run cell_analysis_func on the Cellpose mask to get labeled cells + cell_df."""
    from pycat.toolbox.feature_analysis_tools import cell_analysis_func

    # Measure cell features on the layer the GUI recorded (params['image_layer']),
    # e.g. "Upscaled Segmentation Image", rather than assuming the fluorescence
    # channel.
    image = _resolve_image_layer(
        state, params.get('image_layer'),
        fallback=state.get('preprocessed_fluorescence',
                           state.get('fluorescence_image', state['image'])))
    data_instance = state['data_instance']
    cell_masks = state.get('cellpose_mask')

    if cell_masks is None:
        raise RuntimeError("cell_analysis requires cellpose_segmentation to run first.")

    # If segmentation produced no cells, skip gracefully instead of crashing
    # deep inside pandas ("No objects to concatenate").
    if int(np.asarray(cell_masks).max()) == 0:
        print(f"[PyCAT Batch]   Cell analysis skipped for {image_path.name}: "
              f"0 cells were segmented. Check that cellpose_segmentation ran on "
              f"the intended channel (recorded image_layer="
              f"{params.get('image_layer')!r}) and that the cell diameter is set "
              f"correctly.")
        state['labeled_cells'] = None
        state['no_cells'] = True
        return

    # An omit-mask layer is created and hand-painted interactively (the user
    # marks structures like nucleoli to exclude) -- there is no file or
    # recorded geometry to reconstruct it from headlessly, so it can never be
    # applied in batch mode. Warn loudly rather than silently including cells
    # the interactive run would have excluded.
    omit_layer = params.get('omit_layer')
    if omit_layer and str(omit_layer).strip().lower() not in ('', 'none'):
        print(f"[PyCAT Batch]   Cell analysis: recorded omit mask "
              f"'{omit_layer}' was hand-painted interactively and cannot be "
              f"reconstructed in batch mode -- proceeding WITHOUT it. Cells "
              f"the interactive session excluded may be included here.")

    labeled_cell_masks, cell_df = cell_analysis_func(
        image, cell_masks, omission_mask=None, data_instance=data_instance
    )

    # Store in state and data_instance (condensate steps depend on both)
    state['labeled_cells'] = labeled_cell_masks
    data_instance.data_repository['cell_df'] = cell_df
    data_instance.set_data('cell_df', cell_df)

    _save_array(labeled_cell_masks.astype(np.uint16),
                output_dir / f"{image_path.stem}_labeled_cells.tiff")
    cell_df.to_csv(output_dir / f"{image_path.stem}_cell_df.csv", index=False)
    print(f"[PyCAT Batch]   Cell analysis done: {len(cell_df)} cells.")


def replay_sacf_analysis(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay Spatial ACF analysis, dispatching on the recorded mode exactly
    like run_sacf_analysis (spatial_acf_tools.py) does.

    Previously this imported `sacf_per_cell_per_slice`, a function that does
    not exist anywhere in the codebase -- every batch run of this step raised
    an ImportError, regardless of what was recorded. It also never read
    `params['mode']` at all, so even a fixed import would have silently run
    LIR-style logic for 'drawn_rectangle'/'whole_image' recordings.
    """
    from pycat.toolbox.spatial_acf_tools import (
        sacf_lir_mode, sacf_whole_image_mode, MODE_LIR, MODE_RECT, MODE_WHOLE)
    import numpy as np

    data_instance = state['data_instance']
    mode = params.get('mode', MODE_LIR)

    image = _resolve_image_layer(state, params.get('image_layer'), fallback=state.get('image'))
    if image is None:
        print("[PyCAT Batch]   SACF skipped: recorded image_layer not found in state.")
        return
    stack = image[np.newaxis, ...] if image.ndim == 2 else image
    microns_per_pixel = np.sqrt(
        data_instance.data_repository.get('microns_per_pixel_sq', 1.0)
    )

    if mode == MODE_LIR:
        labeled_cells = state.get('labeled_cells')
        if labeled_cells is None:
            print("[PyCAT Batch]   SACF (LIR mode) skipped: no labeled cell mask in state.")
            return
        results_df = sacf_lir_mode(stack, labeled_cells, microns_per_pixel)
    elif mode == MODE_WHOLE:
        results_df = sacf_whole_image_mode(stack, microns_per_pixel)
    elif mode == MODE_RECT:
        # A drawn-rectangle Shapes layer is created interactively (the user
        # draws rectangles on the canvas) -- there is no file or recorded
        # geometry to reconstruct it from headlessly. Recorded for
        # provenance only; say so explicitly rather than silently skipping
        # or guessing a substitute ROI.
        print("[PyCAT Batch]   SACF (drawn-rectangle mode) skipped in headless "
              "mode: the ROI rectangles were drawn interactively and cannot "
              "be reconstructed from the recorded config.")
        return
    else:
        print(f"[PyCAT Batch]   SACF: unknown recorded mode {mode!r} — skipping.")
        return

    results_df.to_csv(output_dir / f"{image_path.stem}_sacf_results.csv", index=False)
    data_instance.data_repository['sacf_results_df'] = results_df
    print(f"[PyCAT Batch]   SACF ({mode}) done: {len(results_df)} rows.")


def replay_condensate_segmentation(state: dict, image_path: Path, params: dict, output_dir: Path):
    """
    Run segment_subcellular_objects cell-by-cell (the inner loop from
    run_segment_subcellular_objects, without any viewer calls).
    """
    from pycat.toolbox.segmentation_tools import (
        segment_subcellular_objects, cell_mask_stretching
    )
    from pycat.toolbox.segmentation.intensity import compute_image_intensity_stats
    import pandas as pd

    if state.get('no_cells'):
        print(f"[PyCAT Batch]   Condensate segmentation skipped for "
              f"{image_path.name}: no cells were segmented upstream.")
        return

    # Resolve the layers the GUI actually recorded for this step:
    #   seg_image_layer     → thresholding source (usually the bg-removed layer)
    #   measure_image_layer → intensity image the puncta are measured on
    # Honour whichever channel/stage each name encodes instead of assuming the
    # fluorescence channel.
    pre_processed_image = _resolve_image_layer(
        state, params.get('seg_image_layer'),
        fallback=state.get('preprocessed_fluorescence',
                           state.get('preprocessed', state['image'])))
    original_image = _resolve_image_layer(
        state, params.get('measure_image_layer'),
        fallback=state.get('fluorescence_image', state['image']))
    data_instance = state['data_instance']
    ball_radius = _get_data(data_instance, 'ball_radius', 50)

    labeled_cells = state.get('labeled_cells')
    if labeled_cells is not None:
        cell_df = data_instance.get_data('cell_df', pd.DataFrame())
        CMS_img = cell_mask_stretching(pre_processed_image, labeled_cells)
    else:
        # No cell masks — run on whole image
        labeled_cells = np.ones_like(original_image).astype(int)
        labeled_cells[0:2, 0:2] = 0
        cell_df = pd.DataFrame()
        CMS_img = pre_processed_image.copy()

    unique_labels = np.unique(labeled_cells)[1:]  # skip background 0
    total_puncta_mask = np.zeros_like(labeled_cells, dtype=bool)
    total_refined_puncta_mask = np.zeros_like(labeled_cells, dtype=bool)

    # ── Absolute-intensity punctate gate ────────────────────────────────
    # run_segment_subcellular_objects (the interactive path) computes this
    # ONCE globally, before any per-cell/per-crop renormalisation, and passes
    # it into every per-cell call -- it's what lets segment_subcellular_objects
    # tell "this cell is genuinely empty" apart from "this cell's noise got
    # stretched to look like signal" by CLAHE. Replay previously never
    # computed or passed it, so image_stats defaulted to None and the
    # phantom-cell gate was silently disabled for every batch run --
    # systematically more permissive on empty/noisy cells than the
    # interactive session, independent of any single recorded parameter.
    # compute_image_intensity_stats returns position-independent scalars
    # (bg_median/bg_sigma/smooth_sigma), so the same globally-computed dict
    # is valid for both the bbox-crop and full-image branches below.
    min_spot_radius = params.get('min_spot_radius', 2)
    image_stats = compute_image_intensity_stats(
        original_image, labeled_cells,
        smooth_sigma=max(0.5, min_spot_radius / 2.0))

    # Per-cell bounding boxes from auto_crop_roi step (if it ran).
    # Processing each cell in its own tight crop avoids operating on the
    # full 2048×2048 image for every cell — substantial speedup for images
    # with sparse cells surrounded by large background regions.
    cell_bboxes = state.get('cell_bboxes')   # {label: (y0,y1,x0,x1)} or None

    for label in unique_labels:
        cell_mask_holder = (labeled_cells == label).astype(bool)

        if cell_bboxes and label in cell_bboxes:
            # Crop both images and mask to the cell bounding box
            y0, y1, x0, x1 = cell_bboxes[label]
            orig_crop  = original_image[y0:y1, x0:x1].copy()
            proc_crop  = CMS_img[y0:y1, x0:x1].copy()
            mask_crop  = cell_mask_holder[y0:y1, x0:x1]

            refined_crop, unrefined_crop = segment_subcellular_objects(
                orig_crop, proc_crop, mask_crop, label, ball_radius, cell_df,
                kurtosis_threshold=params.get('kurtosis_threshold', -3.0),
                local_snr_threshold=params.get('local_snr_threshold', 1.0),
                global_snr_threshold=params.get('global_snr_threshold', 1.0),
                intensity_hwhm_scale=params.get('intensity_hwhm_scale', 1.17),
                max_area_fraction=params.get('max_area_fraction', 0.25),
                min_spot_radius=min_spot_radius,
                image_stats=image_stats,
                punctate_gate=params.get('punctate_gate', True),
                punctate_gate_sigma=params.get('punctate_gate_sigma', 5.0),
                punctate_gate_abs_sigma=params.get('punctate_gate_abs_sigma', 3.0),
            )
            # Stitch results back into full-image mask
            total_puncta_mask[y0:y1, x0:x1]         |= unrefined_crop
            total_refined_puncta_mask[y0:y1, x0:x1] |= refined_crop
        else:
            # No bounding box — process full image (original behaviour)
            refined, unrefined = segment_subcellular_objects(
                original_image.copy(), CMS_img.copy(),
                cell_mask_holder, label, ball_radius, cell_df,
                kurtosis_threshold=params.get('kurtosis_threshold', -3.0),
                local_snr_threshold=params.get('local_snr_threshold', 1.0),
                global_snr_threshold=params.get('global_snr_threshold', 1.0),
                intensity_hwhm_scale=params.get('intensity_hwhm_scale', 1.17),
                max_area_fraction=params.get('max_area_fraction', 0.25),
                min_spot_radius=min_spot_radius,
                image_stats=image_stats,
                punctate_gate=params.get('punctate_gate', True),
                punctate_gate_sigma=params.get('punctate_gate_sigma', 5.0),
                punctate_gate_abs_sigma=params.get('punctate_gate_abs_sigma', 3.0),
            )
            total_puncta_mask |= unrefined
            total_refined_puncta_mask |= refined

    state['puncta_mask'] = total_refined_puncta_mask
    state['puncta_mask_unrefined'] = total_puncta_mask

    _save_array(total_puncta_mask.astype(np.uint8),
                output_dir / f"{image_path.stem}_total_puncta_mask.tiff")
    _save_array(total_refined_puncta_mask.astype(np.uint8),
                output_dir / f"{image_path.stem}_total_refined_puncta_mask.tiff")
    print("[PyCAT Batch]   Condensate segmentation done.")


def replay_pixel_coloc(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Pixel-wise colocalization — Pearson r + Manders overlap / k1 / k2 between two channels, restricted to the
    segmentation ROI the planner chained upstream. NOT whole-frame: whole-frame Pearson measures the cell shape
    both channels share (r≈0.99 even for independent channels), not colocalisation — the ROI (the union of the
    segmented objects, excluding background) is the fix. The raw threshold-free measures from `coloc/metrics`."""
    from pycat.toolbox.coloc.metrics import (
        pearsons_correlation, manders_overlap, manders_k1_calculation, manders_k2_calculation)
    import pandas as pd
    roi_src = state.get('puncta_mask')
    if roi_src is None:
        roi_src = state.get('labeled_cells')
    if roi_src is None:
        roi_src = state.get('cellpose_mask')
    if roi_src is None:
        print(f"[PyCAT Batch]   Colocalization skipped for {image_path.name}: no segmentation ROI in state.")
        return
    roi = np.asarray(roi_src) > 0
    ch1 = np.asarray(_resolve_image_layer(state, params.get('image_layer1'),
                                          fallback=state.get('preprocessed', state['image'])), dtype=np.float64)
    named = state.get('channels_by_name', {}) or {}
    ch2 = _resolve_image_layer(state, params.get('image_layer2'), fallback=next(iter(named.values()), None))
    if ch2 is None:
        print(f"[PyCAT Batch]   Colocalization skipped for {image_path.name}: no second channel in state.")
        return
    ch2 = np.asarray(ch2, dtype=np.float64)
    pcc, pval = pearsons_correlation(ch1, ch2, roi)
    moc, _ = manders_overlap(ch1, ch2, roi)
    k1, _ = manders_k1_calculation(ch1, ch2, roi)
    k2, _ = manders_k2_calculation(ch1, ch2, roi)
    df = pd.DataFrame([{'pearson_r': pcc, 'pearson_p': pval, 'manders_overlap': moc,
                        'manders_k1': k1, 'manders_k2': k2}])
    state['coloc_df'] = df
    state['data_instance'].set_data('coloc_df', df)
    df.to_csv(output_dir / f"{image_path.stem}_colocalization.csv", index=False)
    print(f"[PyCAT Batch]   Colocalization (within ROI): r={pcc}, MOC={moc}.")


def replay_spatial_metrology(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Per-cell spatial organisation of the segmented objects — Ripley's L / nearest-neighbour / radial density
    (`run_all_spatial_metrics`) on the puncta centroids WITHIN each cell. Replaces the `spatial_metrology`
    skip-stub (spec N2b-2); the cellular analogue of `replay_ivf_spatial_metrology`, which treats the whole field
    as one 'cell'."""
    from pycat.toolbox.spatial_metrology_tools import get_puncta_centroids, run_all_spatial_metrics
    import pandas as pd

    puncta = state.get('puncta_mask')
    cells = state.get('labeled_cells')
    if puncta is None or cells is None:
        print(f"[PyCAT Batch]   Spatial metrology skipped for {image_path.name}: "
              f"needs a puncta mask + labelled cells.")
        return
    puncta = np.asarray(puncta)
    cells = np.asarray(cells)
    mpx = state['data_instance'].data_repository.get('microns_per_pixel_sq', 1.0) ** 0.5

    def _flatten(prefix, obj, out):
        if isinstance(obj, dict):
            for k, v in obj.items():
                _flatten(f"{prefix}_{k}" if prefix else str(k), v, out)
        elif np.isscalar(obj):
            out[prefix] = obj

    coords_df = get_puncta_centroids(puncta, cells, mpx)
    rows = []
    for cl in [c for c in coords_df['cell_label'].unique() if c != 0]:
        sub = coords_df[coords_df['cell_label'] == cl]
        coords = sub[['y_um', 'x_um']].values
        if len(coords) < 2:                       # Ripley/NN need at least two points in the cell
            continue
        res = run_all_spatial_metrics(coords, (cells == cl), mpx)
        row = {'cell_label': int(cl)}
        _flatten('', res, row)
        rows.append(row)
    if not rows:
        print(f"[PyCAT Batch]   Spatial metrology skipped for {image_path.name}: no cell had >= 2 objects.")
        return
    df = pd.DataFrame(rows)
    state['spatial_metrology_df'] = df
    state['data_instance'].set_data('spatial_metrology_df', df)
    df.to_csv(output_dir / f"{image_path.stem}_spatial_metrology.csv", index=False)
    print(f"[PyCAT Batch]   Spatial metrology: {len(rows)} cell(s) analysed.")


def replay_dynamic_spatial(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Trajectory linking (motion) + merge/fission detection (fusion) over a segmented (T, H, W) time-series
    object-label stack (spec N2b-3). Self-contained like the VPT terminal: `extract_frame_properties` ->
    `link_trajectories` and `detect_merge_fission`, both straight from the label stack. Replaces the
    `dynamic_spatial` skip-stub. Refuses (a clear skip, no numbers) when no 3-D segmented stack is in state — it
    never fabricates a per-frame segmentation. Both the motion op (`dynamic_spatial.link_trajectories`) and the
    fusion op (`dynamic_spatial.detect_merge_fission`) resolve here, so the `_dynamic_spatial_done` guard keeps a
    plan that contains both from tracking twice."""
    from pycat.toolbox.dynamic_spatial_tools import (
        extract_frame_properties, link_trajectories, detect_merge_fission)
    import pandas as pd

    if state.get('_dynamic_spatial_done'):
        return                                    # already linked+detected this stack (both ops in one plan)

    stack = None
    for key in ('puncta_mask', 'labeled_cells', 'cellpose_mask'):
        cand = state.get(key)
        if cand is not None and np.asarray(cand).ndim == 3:
            stack = np.asarray(cand)
            break
    if stack is None:
        print(f"[PyCAT Batch]   Dynamic spatial skipped for {image_path.name}: "
              f"needs a segmented (T, H, W) time-series mask stack upstream.")
        return

    mpx = state['data_instance'].data_repository.get('microns_per_pixel_sq', 1.0) ** 0.5
    max_disp = float(params.get('max_displacement_um', 2.0))
    max_gap = int(params.get('max_gap_frames', 1))
    proximity = float(params.get('proximity_um', 1.0))

    props = extract_frame_properties(stack, mpx)
    tracks = link_trajectories(props, max_displacement_um=max_disp, max_gap_frames=max_gap)
    events = detect_merge_fission(stack, mpx, proximity_um=proximity)

    di = state['data_instance']
    state['dynamic_spatial_tracks_df'] = tracks
    state['dynamic_spatial_events_df'] = events
    di.set_data('dynamic_spatial_tracks_df', tracks)
    di.set_data('dynamic_spatial_events_df', events)
    tracks.to_csv(output_dir / f"{image_path.stem}_dynamic_spatial_tracks.csv", index=False)
    events.to_csv(output_dir / f"{image_path.stem}_dynamic_spatial_events.csv", index=False)
    state['_dynamic_spatial_done'] = True
    n_tracks = int(tracks['track_id'].nunique()) if 'track_id' in tracks.columns else len(tracks)
    print(f"[PyCAT Batch]   Dynamic spatial: {n_tracks} track(s), {len(events)} merge/fission event(s).")


def replay_msd_analysis(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Ensemble MSD -> anomalous-diffusion fit over the condensate trajectories `dynamic_spatial` linked
    (spec N2b-4). This is the "build the stack-level handler" branch of the msd_analysis decision: `compute_msd`
    needs a whole-stack trajectory table, and the batch loop now has one — `replay_dynamic_spatial` writes
    `state['dynamic_spatial_tracks_df']` (track_id / frame / y_um / x_um, exactly compute_msd's contract). Same
    scale discipline as VPT microrheology: a diffusion coefficient is a physical rate (um^2/s), so the handler
    REFUSES (a validity flag, no number) when the pixel size is a 1.0 placeholder or the frame interval is
    missing — it never emits a pixel^2/frame "D". Replaces the msd_analysis skip-stub."""
    from pycat.toolbox.condensate_physics_tools import compute_msd, fit_anomalous_diffusion
    from pycat.utils.pixel_size import has_real_pixel_size
    from pycat.utils.frame_interval import frame_interval_s
    import pandas as pd

    if state.get('_msd_done'):
        return                                        # compute_msd + fit ops both resolve here; run once per plan

    tracks = state.get('dynamic_spatial_tracks_df')
    if tracks is None or len(tracks) == 0:
        print(f"[PyCAT Batch]   MSD analysis skipped for {image_path.name}: "
              f"needs linked trajectories (run dynamic_spatial first).")
        return

    repo = state['data_instance'].data_repository
    dt_s = frame_interval_s(repo, context='msd_analysis')
    if not has_real_pixel_size(repo) or not np.isfinite(dt_s):
        state['_msd_scale_validity'] = {'scale_valid': False}
        print(f"[PyCAT Batch]   MSD analysis refused for {image_path.name}: a diffusion coefficient needs a "
              f"calibrated pixel size AND a frame interval (um^2/s) — refusing a pixel^2/frame value.")
        return

    min_track_length = int(params.get('min_track_length', 200))
    max_lag = params.get('max_lag')
    msd = compute_msd(tracks, frame_interval_s=dt_s, min_track_length=min_track_length,
                      max_lag=(int(max_lag) if max_lag is not None else None))
    if msd is None or len(msd) == 0:
        print(f"[PyCAT Batch]   MSD analysis: no track met the {min_track_length}-frame minimum length.")
        return
    fit = fit_anomalous_diffusion(msd, frame_interval_s=dt_s)

    di = state['data_instance']
    state['msd_df'] = msd
    state['msd_fit'] = fit
    state['_msd_scale_validity'] = {'scale_valid': True}
    state['_msd_done'] = True
    di.set_data('msd_df', msd)
    di.set_data('msd_D_um2_per_s', fit.get('D_um2_per_s'))
    msd.to_csv(output_dir / f"{image_path.stem}_msd.csv", index=False)
    # the fit dict carries nested/array diagnostics — write only the scalar summary fields to the CSV.
    scalar_fit = {k: fit.get(k) for k in
                  ('D_um2_per_s', 'alpha', 'motion_type', 'r_squared', 'localization_error_nm', 'log_log_slope')}
    pd.DataFrame([scalar_fit]).to_csv(output_dir / f"{image_path.stem}_msd_fit.csv", index=False)
    print(f"[PyCAT Batch]   MSD analysis: D={fit.get('D_um2_per_s')} um^2/s, alpha={fit.get('alpha')} "
          f"({fit.get('motion_type')}).")
