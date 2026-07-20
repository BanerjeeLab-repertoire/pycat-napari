"""Batch replay handlers (brightfield steps), moved from batch_step_registry.py (decomposition, 1.6.150).
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


def replay_bf_preprocess(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay brightfield preprocessing (flat-field, BG subtract, halo, CLAHE)."""
    from pycat.toolbox.brightfield_tools import preprocess_brightfield

    # RAW counts: this feeds `pre_process_image`, whose rolling ball is NOT scale-invariant.
    # `_normalize_to_float` subtracts the pedestal; the GUI does not. See _proc, above.
    image = _raw_counts(state.get('preprocessed', state['image']))
    result = preprocess_brightfield(
        image,
        bg_kernel=params.get('bg_kernel', 50),
        halo_weight=params.get('halo_weight', 0.35),
    )
    state['bf_enhanced']      = result['enhanced']
    state['bf_bg_subtracted'] = result['bg_subtracted']

    _save_array(result['enhanced'].astype(np.float32),
                output_dir / f"{image_path.stem}_bf_enhanced.tiff")
    print(f"[PyCAT Batch]   BF preprocessing done.")


def replay_bf_condensate_segmentation(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay brightfield dark-blob condensate segmentation."""
    from pycat.toolbox.brightfield_tools import segment_bf_condensates

    enhanced = state.get('bf_enhanced')
    if enhanced is None:
        print("[PyCAT Batch] BF condensate segmentation: no enhanced image — skipping.")
        return

    labeled = segment_bf_condensates(
        enhanced,
        min_diameter_px=params.get('min_diameter_px', 3.0),
        max_diameter_px=params.get('max_diameter_px', 50.0),
        min_circularity=params.get('min_circularity', 0.5),
    )
    state['bf_condensate_mask'] = labeled
    state['cellpose_mask']      = labeled   # alias for downstream compatibility
    state['labeled_cells']      = labeled

    _save_array(labeled.astype(np.uint16),
                output_dir / f"{image_path.stem}_bf_condensate_mask.tiff")
    print(f"[PyCAT Batch]   BF condensate segmentation: {int(labeled.max())} spots.")


def replay_bf_cell_segmentation(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay brightfield Cellpose cell segmentation."""
    # RAW counts: this feeds `pre_process_image`, whose rolling ball is NOT scale-invariant.
    # `_normalize_to_float` subtracts the pedestal; the GUI does not. See _proc, above.
    image = _raw_counts(state.get('preprocessed', state['image']))
    diameter = params.get('cell_diameter', 80)

    try:
        from cellpose import models
        try:
            model = models.CellposeModel(pretrained_model='brightfield')
        except Exception:  # broad-ok: batch replay best-effort probe → fallback; a per-step failure must not abort the whole batch
            model = models.CellposeModel(pretrained_model='cyto2')
        masks, _, _ = model.eval(image, diameter=diameter, channels=[0, 0])
        state['bf_cell_mask'] = masks.astype(np.int32)
        _save_array(masks.astype(np.uint16),
                    output_dir / f"{image_path.stem}_bf_cell_mask.tiff")
        print(f"[PyCAT Batch]   BF cell segmentation: {int(masks.max())} cells.")
    except Exception as e:  # broad-ok: batch replay robustness — logged, this step is skipped, the batch continues
        print(f"[PyCAT Batch]   BF cell segmentation failed: {e} — skipping.")


def replay_ivbf_preprocess(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay in vitro brightfield preprocessing."""
    from pycat.toolbox.brightfield_tools import preprocess_brightfield

    # RAW counts: this feeds `pre_process_image`, whose rolling ball is NOT scale-invariant.
    # `_normalize_to_float` subtracts the pedestal; the GUI does not. See _proc, above.
    image = _raw_counts(state.get('preprocessed', state['image']))
    result = preprocess_brightfield(
        image,
        bg_kernel=params.get('bg_kernel', 60),
        halo_weight=params.get('halo_weight', 0.3),
    )
    state['ivbf_enhanced']      = result['enhanced']
    state['ivbf_bg_subtracted'] = result['bg_subtracted']
    state['ivbf_source']        = image

    _save_array(result['enhanced'].astype(np.float32),
                output_dir / f"{image_path.stem}_ivbf_enhanced.tiff")
    print(f"[PyCAT Batch]   In vitro BF preprocessing done.")


def replay_ivbf_segmentation(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay in vitro brightfield droplet segmentation."""
    from pycat.toolbox.brightfield_tools import segment_bf_condensates

    enhanced = state.get('ivbf_enhanced')
    if enhanced is None:
        print("[PyCAT Batch] IVBF segmentation: no enhanced image — skipping.")
        return

    labeled = segment_bf_condensates(
        enhanced,
        min_diameter_px=params.get('min_d', 4.0),
        max_diameter_px=params.get('max_d', 200.0),
        min_circularity=params.get('min_circularity', 0.5),
    )
    state['ivbf_droplet_mask'] = labeled
    state['cellpose_mask']     = labeled
    state['labeled_cells']     = labeled

    _save_array(labeled.astype(np.uint16),
                output_dir / f"{image_path.stem}_ivbf_droplet_mask.tiff")
    print(f"[PyCAT Batch]   In vitro BF segmentation: {int(labeled.max())} droplets.")
