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

    if state.get('no_cells') or state.get('puncta_mask') is None:
        print(f"[PyCAT Batch]   Condensate analysis skipped for "
              f"{image_path.name}: no cells/puncta available upstream.")
        return

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

    print(f"[PyCAT Batch]   Condensate analysis done.")


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
    """
    data_instance = state['data_instance']
    applied = []
    for key in ('cell_diameter', 'ball_radius', 'object_size'):
        val = params.get(key)
        if val is not None:
            data_instance.data_repository[key] = val
            applied.append(f"{key}={val}")

    if applied:
        print(f"[PyCAT Batch]   Measure Line applied recorded measurements: "
              f"{', '.join(applied)}.")
    else:
        print(f"[PyCAT Batch]   Measure Line: no recorded measurements to apply "
              f"(using open_image values).")


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
            )
            total_puncta_mask |= unrefined
            total_refined_puncta_mask |= refined

    state['puncta_mask'] = total_refined_puncta_mask
    state['puncta_mask_unrefined'] = total_puncta_mask

    _save_array(total_puncta_mask.astype(np.uint8),
                output_dir / f"{image_path.stem}_total_puncta_mask.tiff")
    _save_array(total_refined_puncta_mask.astype(np.uint8),
                output_dir / f"{image_path.stem}_total_refined_puncta_mask.tiff")
    print(f"[PyCAT Batch]   Condensate segmentation done.")
