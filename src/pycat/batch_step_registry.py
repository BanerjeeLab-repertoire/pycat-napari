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


def _load_image(image_path: Path, channel: int = 0):
    """
    Load a single channel of an image file using AICSImage with a tifffile
    fallback for NumPy 2.0 compatibility.

    Parameters
    ----------
    image_path : Path
    channel : int
        Which channel to load (C index). Defaults to 0. Use _load_all_channels
        when the recorded channel_assignment needs to select a specific
        non-zero channel for a given image type (e.g. Segmentation vs
        Fluorescence Image came from different channels in the GUI session).
    """
    microns_per_pixel = 1.0

    try:
        from aicsimageio import AICSImage
        img = AICSImage(str(image_path))
        data = img.get_image_data("YX", S=0, T=0, C=channel)
        try:
            px_size = img.physical_pixel_sizes
            microns_per_pixel = float(px_size.Y) if px_size.Y else 1.0
        except Exception:
            pass
        return data, microns_per_pixel

    except AttributeError as e:
        if "newbyteorder" not in str(e):
            raise
        print(f"[PyCAT Batch] AICSImage newbyteorder error on {image_path.name} "
              f"— falling back to tifffile.")

    # Fallback: tifffile for .tif/.tiff, skimage for everything else
    suffix = image_path.suffix.lower()
    if suffix in ('.tif', '.tiff'):
        import tifffile
        data = tifffile.imread(str(image_path))
    else:
        from skimage import io
        data = io.imread(str(image_path))

    # If multi-channel, select the requested channel; otherwise squeeze to 2D
    if data.ndim == 3 and channel < data.shape[0]:
        data = data[channel]
    while data.ndim > 2:
        data = data[0]

    return data.astype('float32'), microns_per_pixel


def _resolve_channel_for_layer(channel_assignment, layer_name_substring: str, default: int = 0) -> int:
    """
    Look up which channel was assigned to a given layer name during the
    original GUI session, so batch replay uses the same channel for the
    same image type (e.g. "Segmentation Image" vs "Fluorescence Image").

    Works for any number of recorded channels (2, 3, 4+). If more than one
    channel name matches the substring (e.g. a 3+ fluorophore file where
    two layers both contain "Fluorescence"), the first match by channel
    index is used and a warning is printed — callers needing a *specific*
    additional channel beyond the primary seg/fluor pair should instead
    look it up directly from state['channels_by_name'] by its exact
    recorded layer name.

    Parameters
    ----------
    channel_assignment : list of dict or None
        The recorded 'channel_assignment' from the open_image step, each
        dict having 'channel_num' and 'layer_name' keys.
    layer_name_substring : str
        Substring to match against recorded layer_name (case-insensitive),
        e.g. "Segmentation" or "Fluorescence".
    default : int
        Channel index to use if no assignment was recorded or no match found.

    Returns
    -------
    int — the channel index to load.
    """
    if not channel_assignment:
        return default
    target = layer_name_substring.lower()
    matches = [entry for entry in channel_assignment
               if target in entry.get('layer_name', '').lower()]
    if not matches:
        return default
    if len(matches) > 1:
        names = [m.get('layer_name') for m in matches]
        print(f"[PyCAT Batch]   Note: multiple channels matched '{layer_name_substring}' "
              f"({names}) — using the first: '{matches[0].get('layer_name')}'. "
              f"For files with 3+ fluorophores, reference additional channels "
              f"directly via state['channels_by_name'][exact_layer_name].")
    return matches[0].get('channel_num', default)


def _save_array(arr: np.ndarray, path: Path):
    """Save a numpy array as a TIFF."""
    from skimage import io
    io.imsave(str(path), arr)


def _normalize_to_float(arr: np.ndarray) -> np.ndarray:
    """
    Normalize an image to [0, 1] float32.
    skimage functions (equalize_adapthist, etc.) require float images in
    [-1, 1].  Raw images from file are uint16/uint8 with values up to 65535.
    """
    arr = np.asarray(arr).astype(np.float32)
    mn, mx = arr.min(), arr.max()
    if mx > 1.0 or mn < 0.0:
        arr = (arr - mn) / (mx - mn + 1e-8)
    return arr


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
    """
    Load the image and populate the per-file state dict.

    If the original GUI session recorded a channel_assignment (from a
    multi-channel file where the user assigned names to each channel via
    the channel naming dialog), this is used to resolve which channel
    index backs the "Segmentation Image" and "Fluorescence Image" roles,
    and — for files with 3+ fluorophores — every recorded channel is also
    loaded individually and made available in
    state['channels_by_name'][layer_name] for use by any future replay
    step that needs a specific additional channel (e.g. a second
    condensate marker or a colocalization channel).

    This generalizes to any number of channels — 2, 3, 4, or more — since
    the lookup is driven entirely by the recorded layer names rather than
    a fixed seg/fluor pair.
    """
    from pycat.data.data_modules import BaseDataClass

    channel_assignment = params.get('channel_assignment')

    seg_channel = _resolve_channel_for_layer(channel_assignment, 'Segmentation', default=0)
    fluor_channel = _resolve_channel_for_layer(channel_assignment, 'Fluorescence', default=0)

    image, microns_per_pixel = _load_image(image_path, channel=seg_channel)

    data_instance = BaseDataClass()
    data_instance.data_repository['microns_per_pixel'] = microns_per_pixel
    data_instance.data_repository['microns_per_pixel_sq'] = microns_per_pixel ** 2
    data_instance.data_repository['cell_diameter'] = params.get('cell_diameter', 100)
    data_instance.data_repository['ball_radius'] = params.get('ball_radius', 50)

    state['image'] = image
    state['preprocessed'] = image.copy()
    state['data_instance'] = data_instance

    # Load the fluorescence channel separately if it differs from the
    # segmentation channel — used later by condensate segmentation/analysis
    if fluor_channel != seg_channel:
        fluor_image, _ = _load_image(image_path, channel=fluor_channel)
        state['fluorescence_image'] = fluor_image
    else:
        state['fluorescence_image'] = image

    # Load every recorded channel (covers 3+ fluorophore files) and store
    # by its assigned layer name so any channel can be referenced later,
    # not just the two primary seg/fluor roles.
    state['channels_by_name'] = {}
    loaded_channel_cache = {seg_channel: image}
    if fluor_channel != seg_channel:
        loaded_channel_cache[fluor_channel] = state['fluorescence_image']

    if channel_assignment:
        for entry in channel_assignment:
            ch_num = entry.get('channel_num')
            layer_name = entry.get('layer_name')
            if ch_num is None or layer_name is None:
                continue
            if ch_num not in loaded_channel_cache:
                extra_image, _ = _load_image(image_path, channel=ch_num)
                loaded_channel_cache[ch_num] = extra_image
            state['channels_by_name'][layer_name] = loaded_channel_cache[ch_num]

        n_channels = len(channel_assignment)
        print(f"[PyCAT Batch]   Loaded {image_path.name}  shape={image.shape}  "
              f"({n_channels} channel(s); seg_channel={seg_channel}, "
              f"fluor_channel={fluor_channel} from recorded assignment)")
        if n_channels > 2:
            extra_names = [e['layer_name'] for e in channel_assignment
                           if e.get('channel_num') not in (seg_channel, fluor_channel)]
            if extra_names:
                print(f"[PyCAT Batch]   Additional channels available in "
                      f"state['channels_by_name']: {extra_names}")
    else:
        print(f"[PyCAT Batch]   Loaded {image_path.name}  shape={image.shape}")


def replay_preprocessing(state: dict, image_path: Path, params: dict, output_dir: Path):
    """
    Run pre_process_image on the segmentation channel (state['image']).
    Also preprocesses the fluorescence channel separately if it differs,
    storing it in state['preprocessed_fluorescence'] for downstream use.
    """
    from pycat.toolbox.image_processing_tools import pre_process_image

    data_instance = state['data_instance']
    ball_radius = _get_data(data_instance, 'ball_radius', 50)
    window_size = _get_data(data_instance, 'cell_diameter', 100) // 2

    # Preprocess segmentation channel (used for Cellpose)
    seg_image = _normalize_to_float(state['image'])
    preprocessed = pre_process_image(seg_image, ball_radius, window_size)
    state['preprocessed'] = np.asarray(preprocessed).astype(np.float32)

    _save_array(state['preprocessed'],
                output_dir / f"{image_path.stem}_preprocessed.tiff")

    # Preprocess fluorescence channel separately if different from seg
    fluor = state.get('fluorescence_image')
    if fluor is not None and not np.array_equal(fluor, state['image']):
        fluor_norm = _normalize_to_float(fluor)
        fluor_proc = pre_process_image(fluor_norm, ball_radius, window_size)
        state['preprocessed_fluorescence'] = np.asarray(fluor_proc).astype(np.float32)
        _save_array(state['preprocessed_fluorescence'],
                    output_dir / f"{image_path.stem}_preprocessed_fluor.tiff")
    else:
        state['preprocessed_fluorescence'] = state['preprocessed']

    print(f"[PyCAT Batch]   Preprocessing done.")


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

    # Use the preprocessed fluorescence channel for condensate segmentation.
    # This is the bg-removed enhanced image of the fluorescence channel,
    # not the raw image or the segmentation channel preprocessed image.
    original_image = state.get('fluorescence_image', state['image'])
    pre_processed_image = state.get('preprocessed_fluorescence',
                                     state['preprocessed'])
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
            cell_mask_holder, label, ball_radius, cell_df,
            kurtosis_threshold=params.get('kurtosis_threshold', -3.0),
            local_snr_threshold=params.get('local_snr_threshold', 1.0),
            global_snr_threshold=params.get('global_snr_threshold', 1.0),
            intensity_hwhm_scale=params.get('intensity_hwhm_scale', 1.17),
            max_area_fraction=params.get('max_area_fraction', 0.25),
            min_spot_radius=params.get('min_spot_radius', 2),
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

    # Use the fluorescence channel image for puncta intensity measurement
    image = state.get('fluorescence_image', state['image'])
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

def replay_upscaling(state: dict, image_path: Path, params: dict, output_dir: Path):
    """
    Apply the same bicubic-interpolation upscaling used by run_upscaling_func
    in the GUI, doubling resolution (capped at 2048x2048). Updates both the
    raw image and preprocessed image in state, and the relevant data_instance
    fields, mirroring what the GUI does for every selected layer.
    """
    from pycat.toolbox.image_processing_tools import upscale_image_interp

    data_instance = state['data_instance']
    image = state['image']
    num_row, num_col = image.shape[-2], image.shape[-1]

    if num_row >= 2048 or num_col >= 2048:
        print(f"[PyCAT Batch]   Upscaling skipped — already at/above 2048px "
              f"({image.shape}).")
        return

    upscale_factor = 2
    upscaled = upscale_image_interp(image, num_row, num_col, upscale_factor=upscale_factor)
    upscaled = np.clip(upscaled, 0, None).astype(np.float32)

    state['image'] = upscaled
    state['preprocessed'] = upscaled.copy()

    if params.get('update_data_class', True):
        data_instance.data_repository['cell_diameter'] = (
            _get_data(data_instance, 'cell_diameter', 100) * upscale_factor
        )
        data_instance.data_repository['ball_radius'] = (
            _get_data(data_instance, 'ball_radius', 50) * upscale_factor
        )
        data_instance.data_repository['microns_per_pixel_sq'] = (
            _get_data(data_instance, 'microns_per_pixel_sq', 1.0) / (upscale_factor ** 2)
        )

    # Also upscale the fluorescence channel if it was separately loaded
    # (multi-channel files where seg and fluor are different channels).
    # In the GUI the user selects both layers before clicking upscale.
    fluor = state.get('fluorescence_image')
    if fluor is not None and fluor is not image:
        fr, fc = fluor.shape[-2], fluor.shape[-1]
        if fr < 2048 and fc < 2048:
            fluor_up = upscale_image_interp(fluor, fr, fc, upscale_factor=upscale_factor)
            state['fluorescence_image'] = np.clip(fluor_up, 0, None).astype(np.float32)

    # Update channels_by_name too if populated
    for name, arr in state.get('channels_by_name', {}).items():
        if arr is not None and arr is not image:
            cr, cc = arr.shape[-2], arr.shape[-1]
            if cr < 2048 and cc < 2048:
                arr_up = upscale_image_interp(arr, cr, cc, upscale_factor=upscale_factor)
                state['channels_by_name'][name] = np.clip(arr_up, 0, None).astype(np.float32)

    _save_array(upscaled, output_dir / f"{image_path.stem}_upscaled.tiff")
    selected = params.get('selected_layers', params.get('active_layer', '?'))
    print(f"[PyCAT Batch]   Upscaling done: {image.shape} -> {upscaled.shape}  "
          f"(layers: {selected})")


def replay_background_removal(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay enhanced RB-Gaussian background removal on the preprocessed image."""
    from pycat.toolbox.image_processing_tools import rb_gaussian_bg_removal_with_edge_enhancement
    import math

    preprocessed = state.get('preprocessed')
    if preprocessed is None:
        print("[PyCAT Batch] background_removal: no preprocessed image in state — skipping.")
        return

    data_instance = state['data_instance']
    ball_radius = math.ceil(_get_data(data_instance, 'ball_radius', 50))

    # Background remove segmentation channel
    bg_removed = rb_gaussian_bg_removal_with_edge_enhancement(
        _normalize_to_float(preprocessed), ball_radius
    )
    state['preprocessed'] = bg_removed.astype(np.float32)
    _save_array(state['preprocessed'],
                output_dir / f"{image_path.stem}_bg_removed.tiff")

    # Background remove fluorescence channel if separate
    fluor_proc = state.get('preprocessed_fluorescence')
    if fluor_proc is not None and fluor_proc is not preprocessed:
        bg_fluor = rb_gaussian_bg_removal_with_edge_enhancement(
            _normalize_to_float(fluor_proc), ball_radius
        )
        state['preprocessed_fluorescence'] = bg_fluor.astype(np.float32)
        _save_array(state['preprocessed_fluorescence'],
                    output_dir / f"{image_path.stem}_bg_removed_fluor.tiff")
    else:
        state['preprocessed_fluorescence'] = state['preprocessed']

    print(f"[PyCAT Batch]   Background removal done.")


def replay_measure_line(state: dict, image_path: Path, params: dict, output_dir: Path):
    """
    Measure Line records object/cell diameter measurements made interactively
    via drawn lines in the GUI — there is no equivalent automatic action for
    headless batch mode since it requires the user to draw on the image.
    The cell_diameter/object_size/ball_radius values from the recorded
    params (captured at the moment Measure Line was clicked) are already
    applied to data_instance by replay_open_image, so this step is a no-op
    that exists only to keep the recorded step sequence complete and to
    surface the values that were in effect at that point in the GUI session.
    """
    print(f"[PyCAT Batch]   Measure Line skipped in headless mode "
          f"(values already set: cell_diameter={params.get('cell_diameter')}, "
          f"ball_radius={params.get('ball_radius')}).")


def replay_open_stack(state: dict, image_path: Path, params: dict, output_dir: Path):
    """
    Replay a unified open_stack step (covers former open_image_stack and
    open_ims_file).  For batch headless replay we load the segmentation and
    fluorescence channels using the same channel_assignment logic as
    replay_open_image, then store them in state identically so all downstream
    steps (cellpose, condensate segmentation, etc.) work unchanged.
    """
    # Delegate to the existing open_image replay — it already handles
    # multi-channel files via channel_assignment and state['channels_by_name'].
    # The only difference is the step name; the params schema is the same.
    replay_open_image(state, image_path, params, output_dir)


_STEP_MAP = {
    'open_image':               replay_open_image,
    'open_stack':               replay_open_stack,    # unified IMS + TIFF stack
    'open_ims_file':            replay_open_stack,    # legacy key — keep for old configs
    'open_image_stack':         replay_open_stack,    # legacy key — keep for old configs
    'measure_line':              replay_measure_line,
    'upscaling':                replay_upscaling,
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
