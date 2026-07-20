"""Batch replay handlers (segmentation steps), moved from batch_step_registry.py (decomposition, 1.6.150).
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


def replay_cellpose_segmentation(state: dict, image_path: Path, params: dict, output_dir: Path):
    """
    Run cell segmentation using the method recorded from the GUI session.
    Supports 'cellpose' (default), 'stardist', and 'random_forest'.
    Random Forest requires interactive annotation and falls back to Cellpose
    in headless mode with a warning.
    """
    method          = params.get('method', 'cellpose')
    # Run on the layer the GUI recorded for this step (params['image_layer']),
    # e.g. "Upscaled Fluorescence Image". Falling back to the segmentation
    # channel (as older replay did) runs Cellpose on the foreground-suppressed
    # channel and typically finds 0 cells, which then crashes cell_analysis.
    resolved = _resolve_image_layer(
        state, params.get('image_layer'),
        fallback=state.get('fluorescence_image', state.get('preprocessed', state['image'])))
    image           = _normalize_to_float(resolved)
    data_instance   = state['data_instance']
    object_diameter = _get_data(data_instance, 'cell_diameter', 100)

    cell_masks = None

    if method == 'stardist':
        try:
            from stardist.models import StarDist2D
            from csbdeep.utils import normalize as csbdeep_normalize
            img = csbdeep_normalize(image, 1, 99.8)
            model = StarDist2D.from_pretrained('2D_versatile_fluo')
            cell_masks, _ = model.predict_instances(img)
            cell_masks = cell_masks.astype(np.uint16)
            print(f"[PyCAT Batch]   StarDist done: {cell_masks.max()} cells found.")
        except ImportError:
            print("[PyCAT Batch]   StarDist not installed — falling back to Cellpose.")
            method = 'cellpose'

    if method == 'random_forest':
        print("[PyCAT Batch]   Random Forest requires interactive annotation — "
              "falling back to Cellpose for headless replay.")
        method = 'cellpose'

    if method == 'cellpose':
        from pycat.toolbox.segmentation_tools import cellpose_segmentation
        _refine = bool(params.get('cellpose_refine', False))
        _model = params.get('cellpose_model', None)
        cell_masks = cellpose_segmentation(image, object_diameter,
                                           model_name=_model,
                                           postprocess=_refine)
        cell_masks = np.asarray(cell_masks).astype(np.uint16)
        print(f"[PyCAT Batch]   Cellpose done: {cell_masks.max()} cells found.")

    state['cellpose_mask'] = cell_masks
    _save_array(cell_masks, output_dir / f"{image_path.stem}_cell_mask.tiff")


def replay_ts_cellpose_keyframe(state: dict, image_path: Path, params: dict, output_dir: Path):
    """
    Run keyframe Cellpose on the preprocessed stack and propagate masks.
    Uses the same interval and diameter recorded from the GUI session.
    """
    from pycat.toolbox.ts_cellpose_tools import run_keyframe_cellpose

    preprocessed = state.get('preprocessed')
    if preprocessed is None:
        raise RuntimeError("ts_cellpose_keyframe requires preprocessing to run first.")

    # Stack may be 2D (single reference frame) — promote to (1, H, W)
    if preprocessed.ndim == 2:
        preprocessed = preprocessed[np.newaxis]

    interval      = params.get('keyframe_interval', 20)
    cell_diameter = params.get('cell_diameter',
                               _get_data(state['data_instance'], 'cell_diameter', 100))

    mask_stack, kf_indices = run_keyframe_cellpose(
        preprocessed.astype(np.float32), cell_diameter, interval
    )

    state['labeled_cells']          = mask_stack[0]   # frame-0 mask for cell analysis
    state['ts_cell_mask_stack']     = mask_stack
    state['ts_cellpose_keyframes']  = kf_indices

    _save_array(mask_stack[0].astype(np.uint16),
                output_dir / f"{image_path.stem}_ts_cell_mask.tiff")
    print(f"[PyCAT Batch]   TS Cellpose done: {len(kf_indices)} keyframes, "
          f"{mask_stack.shape[0]} frames total.")
