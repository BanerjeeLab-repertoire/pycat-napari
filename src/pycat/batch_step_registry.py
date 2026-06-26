"""
pycat/batch_step_registry.py
============================
Headless replay functions for PyCAT batch processing.

Each replay function calls the underlying pure-numpy computation functions
directly, completely bypassing the napari viewer and all Qt dialogs.
Results (masks and DataFrames) are saved straight to disk.

The pipeline order for Condensate Analysis is:
  open_image → preprocessing → background_removal → cellpose_segmentation
  → cell_analysis → condensate_segmentation → condensate_analysis → save_and_clear
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from pycat.batch_processor import BatchProcessor


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_data(data_instance, key, default=None):
    """Safely retrieve a value from data_instance.data_repository."""
    return data_instance.data_repository.get(key, default)


def _load_image(image_path: Path):
    """Load an image file using AICSImage (same as PyCAT's open_2d_image)."""
    from aicsimageio import AICSImage
    img = AICSImage(str(image_path))
    # Get first scene, timepoint, and channel as a 2D YX array
    data = img.get_image_data("YX", S=0, T=0, C=0)
    # Store physical pixel size in microns if available
    try:
        px_size = img.physical_pixel_sizes
        microns_per_pixel = float(px_size.Y) if px_size.Y else 1.0
    except Exception:
        microns_per_pixel = 1.0
    return data, microns_per_pixel


def _save_array(arr: np.ndarray, path: Path):
    """Save a numpy array as a TIFF."""
    from skimage import io
    io.imsave(str(path), arr)


# ---------------------------------------------------------------------------
# Replay functions
# Each signature: fn(state, image_path, params, output_dir) -> None
#
# `state` is a plain dict shared across all steps for one file, holding:
#   state['image']           – raw image array
#   state['preprocessed']    – preprocessed image array
#   state['data_instance']   – BaseDataClass instance for this file
#   state['labeled_cells']   – labeled cell mask array (from cell_analysis)
#   state['puncta_mask']     – refined puncta mask array (from condensate_seg)
# ---------------------------------------------------------------------------

def replay_open_image(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Load the image and populate the per-file state dict."""
    from pycat.data.data_modules import BaseDataClass

    image, microns_per_pixel = _load_image(image_path)

    data_instance = BaseDataClass()
    data_instance.data_repository['microns_per_pixel'] = microns_per_pixel
    data_instance.data_repository['microns_per_pixel_sq'] = microns_per_pixel ** 2

    # Estimate cell/object diameters from recorded params, or use defaults
    data_instance.data_repository['cell_diameter'] = params.get('cell_diameter', 100)
    data_instance.data_repository['ball_radius'] = params.get('ball_radius', 50)

    state['image'] = image
    state['preprocessed'] = image.copy()  # will be updated by preprocessing steps
    state['data_instance'] = data_instance

    print(f"[PyCAT Batch]   Loaded {image_path.name}  shape={image.shape}")


def replay_preprocessing(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Run pre_process_image on the raw image."""
    from pycat.toolbox.image_processing_tools import pre_process_image

    image = state['image']
    data_instance = state['data_instance']
    ball_radius = _get_data(data_instance, 'ball_radius', 50)
    window_size = _get_data(data_instance, 'cell_diameter', 100) // 2

    preprocessed = pre_process_image(image, ball_radius, window_size)
    state['preprocessed'] = preprocessed

    _save_array(preprocessed, output_dir / f"{image_path.stem}_preprocessed.tiff")
    print(f"[PyCAT Batch]   Preprocessing done.")


def replay_background_removal(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Run rolling-ball + gaussian background removal on the preprocessed image."""
    from pycat.toolbox.image_processing_tools import rb_gaussian_background_removal

    image = state['preprocessed']
    data_instance = state['data_instance']
    ball_radius = _get_data(data_instance, 'ball_radius', 50)

    # rb_gaussian_background_removal is the pure function; signature varies by version
    try:
        bg_removed = rb_gaussian_background_removal(image, ball_radius)
    except TypeError:
        # Fall back to calling with just the image if signature differs
        from pycat.toolbox.image_processing_tools import apply_rescale_intensity
        bg_removed = apply_rescale_intensity(image)

    state['preprocessed'] = bg_removed
    _save_array(bg_removed, output_dir / f"{image_path.stem}_bg_removed.tiff")
    print(f"[PyCAT Batch]   Background removal done.")


def replay_cellpose_segmentation(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Run Cellpose on the raw image to get a labeled cell mask."""
    from pycat.toolbox.segmentation_tools import cellpose_segmentation

    image = state['image']
    data_instance = state['data_instance']
    object_diameter = _get_data(data_instance, 'cell_diameter', 100)

    cell_masks = cellpose_segmentation(image, object_diameter)
    state['cellpose_mask'] = cell_masks

    _save_array(cell_masks.astype(np.uint16),
                output_dir / f"{image_path.stem}_cellpose_mask.tiff")
    print(f"[PyCAT Batch]   Cellpose done: {len(np.unique(cell_masks))-1} cells found.")


def replay_cell_analysis(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Run cell_analysis_func on the Cellpose mask to get labeled cells + cell_df."""
    from pycat.toolbox.feature_analysis_tools import cell_analysis_func

    image = state['image']
    data_instance = state['data_instance']
    cell_masks = state.get('cellpose_mask')

    if cell_masks is None:
        raise RuntimeError("cell_analysis requires cellpose_segmentation to run first.")

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
    from pycat.toolbox.spatial_acf_tools import sacf_per_cell_per_slice
    import numpy as np

    image = state['image']
    labeled_cells = state.get('labeled_cells')
    data_instance = state['data_instance']

    if labeled_cells is None:
        print("[PyCAT Batch] SACF: no labeled cell mask in state — skipping.")
        return

    stack = image[np.newaxis, ...] if image.ndim == 2 else image
    microns_per_pixel = np.sqrt(
        data_instance.data_repository.get('microns_per_pixel_sq', 1.0)
    )

    results_df = sacf_per_cell_per_slice(
        stack=stack,
        labeled_cell_mask=labeled_cells,
        microns_per_pixel=microns_per_pixel,
    )
    results_df.to_csv(output_dir / f"{image_path.stem}_sacf_results.csv", index=False)
    data_instance.data_repository['sacf_results_df'] = results_df
    print(f"[PyCAT Batch]   SACF done: {len(results_df)} rows.")

def replay_condensate_segmentation(state: dict, image_path: Path, params: dict, output_dir: Path):
    """
    Run segment_subcellular_objects cell-by-cell (the inner loop from
    run_segment_subcellular_objects, without any viewer calls).
    """
    from pycat.toolbox.segmentation_tools import (
        segment_subcellular_objects, cell_mask_stretching
    )
    import pandas as pd

    original_image = state['image']
    pre_processed_image = state['preprocessed']
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

    for label in unique_labels:
        cell_mask_holder = (labeled_cells == label).astype(bool)
        refined, unrefined = segment_subcellular_objects(
            original_image.copy(), CMS_img.copy(),
            cell_mask_holder, label, ball_radius, cell_df
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


def replay_condensate_analysis(state: dict, image_path: Path, params: dict, output_dir: Path):
    """
    Run puncta_analysis_func directly (inner logic of run_puncta_analysis_func,
    no viewer or Qt dialog calls).
    """
    from pycat.toolbox.feature_analysis_tools import puncta_analysis_func

    image = state['image']
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


def replay_save_and_clear(state: dict, image_path: Path, params: dict, output_dir: Path):
    """No-op in headless mode — files are already saved by each step above."""
    print(f"[PyCAT Batch]   All outputs saved to {output_dir}")
    state.clear()


# ---------------------------------------------------------------------------
# Step map
# ---------------------------------------------------------------------------

_STEP_MAP = {
    'open_image':               replay_open_image,
    'preprocessing':            replay_preprocessing,
    'background_removal':       replay_background_removal,
    'cellpose_segmentation':    replay_cellpose_segmentation,
    'cell_analysis':            replay_cell_analysis,
    'condensate_segmentation':  replay_condensate_segmentation,
    'condensate_analysis':      replay_condensate_analysis,
    'sacf_analysis':            replay_sacf_analysis,
    'save_and_clear':           replay_save_and_clear,
}


def register_all_steps(bp: "BatchProcessor"):
    for name, fn in _STEP_MAP.items():
        bp.register_step(name, fn)
    print(f"[PyCAT Batch] Registered {len(_STEP_MAP)} headless replay steps.")
