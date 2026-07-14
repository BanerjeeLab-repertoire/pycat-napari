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
from pycat.file_io.image_reader import open_image

if TYPE_CHECKING:
    from pycat.batch_processor import BatchProcessor


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_data(data_instance, key, default=None):
    """Safely retrieve a value from data_instance.data_repository."""
    return data_instance.data_repository.get(key, default)


def _derive_split_companion_path(primary_path: Path, primary_recorded_stem: str, companion_recorded_stem: str, companion_suffix: str = None) -> Path:
    """Map a recorded split-channel companion file onto the current batch sample.

    Example: recorded files `cell01_DAPI.tif` and `cell01_GFP.tif`; current
    primary is `cell17_DAPI.tif` -> companion becomes `cell17_GFP.tif`.
    """
    primary_stem = primary_path.stem
    # Longest common prefix between the recorded primary and companion.
    i = 0
    while i < min(len(primary_recorded_stem), len(companion_recorded_stem)) and primary_recorded_stem[i] == companion_recorded_stem[i]:
        i += 1
    common_prefix = primary_recorded_stem[:i]
    primary_token = primary_recorded_stem[i:]
    companion_token = companion_recorded_stem[i:]
    if primary_token and primary_stem.endswith(primary_token):
        new_stem = primary_stem[:-len(primary_token)] + companion_token
    elif common_prefix and primary_stem.startswith(common_prefix):
        new_stem = primary_stem[:len(common_prefix)] + companion_token
    else:
        # Last-resort fallback: same stem as current primary. This will only
        # work for extension-split pairs, but gives a clear FileNotFoundError if
        # not present.
        new_stem = primary_stem
    return primary_path.with_name(new_stem + (companion_suffix or primary_path.suffix))


def _source_path_for_recorded_channel(image_path: Path, channel_assignment, channel: int) -> Path:
    """Return the actual file that should provide a recorded channel."""
    if not channel_assignment:
        return image_path
    entry = next((e for e in channel_assignment if e.get('channel_num') == channel), None)
    if not entry:
        return image_path
    source_stem = entry.get('source_stem')
    source_suffix = entry.get('source_suffix') or image_path.suffix
    primary = channel_assignment[0]
    primary_stem = primary.get('source_stem')
    # Channels from the first recorded source are read from the current batch
    # file. Channels from later recorded sources are companion split files.
    if not source_stem or source_stem == primary_stem:
        return image_path
    companion = _derive_split_companion_path(image_path, primary_stem or image_path.stem, source_stem, source_suffix)
    if not companion.exists():
        raise FileNotFoundError(
            f"Split-channel companion file not found for {image_path.name}: expected {companion.name}. "
            f"Recorded companion stem was '{source_stem}'.")
    return companion


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
        img = open_image(str(image_path))
        # `get_image_data` LOADS THE WHOLE SCENE (documented, both libraries). `read_plane`
        # pulls exactly one YX plane through the lazy API — which matters in batch, where this
        # runs once per file per step.
        from pycat.file_io.image_reader import read_plane
        data = read_plane(img, path=str(image_path), scene=0, t=0, c=channel)
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


def _raw_counts(arr):
    """The image in RAW detector counts. For INTENSITY measurements.

    ``_normalize_to_float`` min-max normalises to [0, 1], which is required by several
    skimage functions and is correct for SEGMENTATION. It is **fatal for any intensity
    measurement**: it maps the image MINIMUM to zero, silently subtracting an uncontrolled
    floor — the darkest noise pixel in that particular field.

    Measured on the in-vitro partition coefficient, with a **true Kp of 30** throughout:

    ==========  ====================
    noise sd    reported "partition"
    ==========  ====================
    2           **323.5**
    5           130.0
    15          44.0
    30          **22.5**
    ==========  ====================

    A 14x swing driven entirely by the exposure. Optical density is worse still, because
    it is a LOG of a ratio: the strongest condensate — the one that SETS the image minimum
    — has its OD diverge.

    Intensity ratios need raw counts. See 1.5.424 / 1.5.425.
    """
    if arr is None:
        return None
    a = np.asarray(arr).astype(np.float64)
    return a


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


def _resolve_image_layer(state: dict, layer_name, fallback=None):
    """
    Resolve a RECORDED napari layer name to the actual array in ``state``.

    The GUI records which layer each step operated on (e.g.
    ``"Upscaled Fluorescence Image"`` or
    ``"Enhanced Background Removed Pre-Processed Upscaled Segmentation Image"``).
    Replay must honour that recorded name instead of assuming a fixed
    channel/stage, otherwise a step can silently run on the wrong channel
    (e.g. Cellpose running on the foreground-suppressed segmentation channel
    instead of the fluorescence channel, finding 0 cells).

    Resolution uses two independent facts encoded in the layer name:

      1. WHICH CHANNEL  — "Segmentation" vs "Fluorescence" vs a named extra
         channel from ``state['channels_by_name']`` (3+ fluorophore files).
      2. WHICH STAGE    — raw (upscaled) vs preprocessed / background-removed.
         The processed segmentation array lives in ``state['preprocessed']``
         (background_removal overwrites it with the enhanced bg-removed image),
         and the processed fluorescence array in
         ``state['preprocessed_fluorescence']``.

    Parameters
    ----------
    state : dict          per-file replay state.
    layer_name : str|None the recorded layer name to resolve.
    fallback : ndarray    array to return if ``layer_name`` is missing/'None'
                          or cannot be matched.

    Returns
    -------
    numpy.ndarray
    """
    if not layer_name or str(layer_name).strip().lower() == 'none':
        return fallback

    name = str(layer_name).lower()

    # --- which processing stage? (most-processed keyword wins) -------------
    is_processed = ('background removed' in name
                    or 'bg removed' in name
                    or 'pre-processed' in name
                    or 'preprocessed' in name)

    # --- which channel? ----------------------------------------------------
    if 'fluorescence' in name:
        if is_processed:
            return state.get('preprocessed_fluorescence',
                             state.get('fluorescence_image', state['image']))
        return state.get('fluorescence_image', state['image'])

    if 'segmentation' in name:
        if is_processed:
            return state.get('preprocessed', state['image'])
        return state['image']

    # --- a named extra channel (files with 3+ fluorophores) ---------------
    channels = state.get('channels_by_name', {}) or {}
    if layer_name in channels:                       # exact recorded name
        return channels[layer_name]
    for key, arr in channels.items():                # loose base-name match
        base = key.lower()
        if base and (base in name or name in base):
            return arr

    return fallback


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

    seg_path = _source_path_for_recorded_channel(image_path, channel_assignment, seg_channel)
    image, microns_per_pixel = _load_image(seg_path, channel=seg_channel if seg_path == image_path else 0)

    data_instance = BaseDataClass()
    data_instance.data_repository['microns_per_pixel'] = microns_per_pixel
    data_instance.data_repository['microns_per_pixel_sq'] = microns_per_pixel ** 2
    data_instance.data_repository['cell_diameter'] = params.get('cell_diameter', 100)

    # ball_radius: use the recorded value if present; otherwise, when the batch
    # enabled automatic object-size estimation (valid fluorescence workflow, no
    # explicit ball_radius), estimate it per image from the fluorescence signal
    # (top-hat + Otsu → median object diameter). Falls back to 50 if estimation
    # finds no objects. The user was told at batch start that this is applying.
    _ball_radius = params.get('ball_radius', None)
    if _ball_radius is None and state.get('_auto_ball_radius'):
        try:
            from pycat.toolbox.image_processing_tools import estimate_object_size_px
            _est = estimate_object_size_px(image)   # seg image = fluorescence here
            if _est.get('ball_radius'):
                _ball_radius = _est['ball_radius']
                print(f"[PyCAT Batch]   Auto ball_radius = {_ball_radius} "
                      f"(object_size {_est['object_size_px']:.1f}px from "
                      f"{_est['n_objects']} objects) for {image_path.name}.")
        except Exception as _e:
            print(f"[PyCAT Batch]   Auto ball_radius estimation failed "
                  f"({_e}); using default.")
    if _ball_radius is None:
        _ball_radius = 50
    data_instance.data_repository['ball_radius'] = _ball_radius

    state['image'] = image
    state['preprocessed'] = image.copy()
    state['data_instance'] = data_instance

    # Load the fluorescence channel separately if it differs from the
    # segmentation channel — used later by condensate segmentation/analysis
    if fluor_channel != seg_channel:
        fluor_path = _source_path_for_recorded_channel(image_path, channel_assignment, fluor_channel)
        fluor_image, _ = _load_image(fluor_path, channel=fluor_channel if fluor_path == image_path else 0)
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
                extra_path = _source_path_for_recorded_channel(image_path, channel_assignment, ch_num)
                extra_image, _ = _load_image(extra_path, channel=ch_num if extra_path == image_path else 0)
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
    Run pre_process_image on ONLY the layer the GUI recorded as active
    (params['active_layer']), mirroring the interactive tool, which acts on
    the single active layer. The non-active channel is left unprocessed (its
    "preprocessed" slot passes through the raw array), so a config that only
    preprocessed the segmentation channel does not also silently preprocess
    the fluorescence channel.
    """
    from pycat.toolbox.image_processing_tools import pre_process_image

    data_instance = state['data_instance']
    # Prefer recorded params; fall back to data_instance for legacy configs
    ball_radius = int(params.get('ball_radius',
                      _get_data(data_instance, 'ball_radius', 50)))
    window_size = int(params.get('window_size',
                      _get_data(data_instance, 'cell_diameter', 100) // 2))

    # Foreground suppression: replay exactly what was recorded. Legacy configs
    # (no keys) default to suppression ON with tuned defaults, matching the
    # interactive default behaviour.
    suppress_foreground = bool(params.get('suppress_foreground', True))
    suppression_params = params.get('foreground_suppression_params', None)

    # Which layer was active when preprocessing was clicked?
    active_name = str(params.get('active_layer')
                      or params.get('active_image_layer') or '').lower()
    on_fluor = 'fluorescence' in active_name  # default (incl. "segmentation") -> seg

    def _proc(arr):
        # ── BATCH MUST PASS RAW COUNTS. It was pre-normalising, and that is the bug. ──
        #
        # **Gable's report: batch segments the same image differently from the recording.**
        #
        # ``pre_process_image`` **normalises internally** — ``img = img / img.max()``. It expects
        # **raw counts**, and it divides. Batch was calling ``_normalize_to_float`` first, which
        # does ``(x - min) / (max - min)`` — **it subtracts the pedestal too.** The subsequent
        # ``/max`` inside ``pre_process_image`` is then a **no-op**, so the two callers hand the
        # rolling ball genuinely different images::
        #
        #     INTERACTIVE   img / max            ->  range **[0.425, 1.0]**
        #     BATCH         (img-min)/(max-min)  ->  range **[0.000, 1.0]**
        #
        # **And the rolling ball is NOT scale-invariant.** ``skimage.restoration.rolling_ball``
        # rolls a ball in **(x, y, INTENSITY)**, and its ``radius`` applies to **all three axes**.
        # Change the intensity range and the same radius fits the background differently.
        #
        # Measured — the mean of the background-subtracted image, same input, same radius:
        #
        #     interactive       **0.0205**
        #     batch (before)    **0.0493**   <- **2.4x more background removed**
        #     batch (fixed)     **0.0205**   <- bit-for-bit identical to the recording
        #
        # ``_raw_counts`` in this very file already documents that ``_normalize_to_float`` is
        # *"fatal for any intensity measurement"* — a **14x** swing in partition coefficient — and
        # says it *"is correct for SEGMENTATION"*. **That last part is wrong**, and this is why:
        # the rolling ball's radius has an intensity component, so pedestal subtraction changes
        # the segmentation too.
        return np.asarray(pre_process_image(
            _raw_counts(arr), ball_radius, window_size,
            suppress_foreground=suppress_foreground,
            suppression_params=suppression_params)).astype(np.float32)

    if on_fluor:
        fluor = state.get('fluorescence_image', state['image'])
        state['preprocessed_fluorescence'] = _proc(fluor)
        # Segmentation channel was NOT the active layer -> leave it unprocessed.
        state.setdefault('preprocessed', np.asarray(state['image']).copy())
        _save_array(state['preprocessed_fluorescence'],
                    output_dir / f"{image_path.stem}_preprocessed.tiff")
        print(f"[PyCAT Batch]   Preprocessing done (active layer: fluorescence).")
    else:
        state['preprocessed'] = _proc(state['image'])
        # Fluorescence channel was NOT the active layer -> pass through raw so
        # any later reference to a processed-fluor layer returns the raw image
        # rather than an unintentionally preprocessed one.
        fluor = state.get('fluorescence_image')
        state['preprocessed_fluorescence'] = (
            np.asarray(fluor).copy() if fluor is not None else state['preprocessed'])
        _save_array(state['preprocessed'],
                    output_dir / f"{image_path.stem}_preprocessed.tiff")
        print(f"[PyCAT Batch]   Preprocessing done (active layer: segmentation).")


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
                min_spot_radius=params.get('min_spot_radius', 2),
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


def replay_calibration_correction(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay calibration-frame correction: reload the reference and apply it."""
    import os
    import numpy as np
    from pycat.toolbox.image_processing_tools import (
        apply_flatfield_correction, apply_background_subtraction)
    calib = params.get('calibration_path', '')
    if not calib or not os.path.exists(calib):
        print('[PyCAT Batch]   Calibration correction skipped (reference file not found).')
        return
    import tifffile
    ref = np.squeeze(np.asarray(tifffile.imread(calib), dtype=np.float32))
    if ref.ndim == 3:
        ref = np.median(ref, axis=0)
    # RAW counts: the rolling ball's radius has an INTENSITY component (see _proc, above), and
    # `_normalize_to_float` subtracts the pedestal. The GUI passes the raw layer.
    img = _raw_counts(state.get('preprocessed', state['image']))
    if img.shape[-2:] != ref.shape[-2:]:
        print(f'[PyCAT Batch]   Calibration correction skipped (shape {ref.shape} != image {img.shape[-2:]}).')
        return
    if params.get('method') == 'flatfield':
        corrected = apply_flatfield_correction(img, ref)
    else:
        corrected = apply_background_subtraction(img, ref)
    state['preprocessed'] = corrected
    state['image'] = corrected
    _save_array(corrected.astype(np.float32),
                output_dir / f"{image_path.stem}_calibrated.tiff")
    print(f"[PyCAT Batch]   Calibration correction ({params.get('method')}) applied.")


def replay_background_removal(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay enhanced RB-Gaussian background removal on the preprocessed image.

    Matches the interactive `run_enhanced_rb_gaussian_bg_removal`: if the input is
    already preprocessed (sparse, peaked distribution), it applies the
    non-destructive `soft_foreground_suppression` using the session's suppression
    params rather than the destructive rolling-ball subtraction, so batch and GUI
    produce the same 'Enhanced Background Removed' result.
    """
    from pycat.toolbox.image_processing_tools import (
        rb_gaussian_bg_removal_with_edge_enhancement, soft_foreground_suppression)
    import math

    preprocessed = state.get('preprocessed')
    if preprocessed is None:
        print("[PyCAT Batch] background_removal: no preprocessed image in state — skipping.")
        return

    data_instance = state['data_instance']
    ball_radius = math.ceil(int(params.get('ball_radius',
                                _get_data(data_instance, 'ball_radius', 50))))
    sp = params.get('foreground_suppression_params', None) or {}

    # Which layer was active when background removal was clicked?
    active_name = str(params.get('active_layer')
                      or params.get('active_image_layer') or '').lower()
    on_fluor = 'fluorescence' in active_name  # default (incl. "segmentation") -> seg

    def _enhance(img):
        # ── The "already enhanced" HEURISTIC IS SCALE-DEPENDENT, and batch changed the scale ──
        #
        # ``_already_enhanced = median(nonzero) < 0.05`` — and ``_normalize_to_float`` maps the
        # image minimum to **zero**, which **moves the median.**
        #
        # Measured, on a **high-contrast image — a bright spot on a dim background, i.e. exactly a
        # condensate image**:
        #
        #     path            median          verdict              processing applied
        #     INTERACTIVE     **403 counts**  not enhanced         **full rolling-ball removal**
        #     BATCH           **0.030**       **"already enhanced"**  **soft suppression only**
        #
        # ***The two paths take DIFFERENT BRANCHES and apply COMPLETELY DIFFERENT PROCESSING.***
        # This is not a scale shift in one number — it is a different algorithm.
        #
        # The GUI hands ``active_layer.data`` — **raw counts** — to the same heuristic. Batch must
        # do the same, or the branch it takes is decided by a normalisation the GUI never applied.
        img = _raw_counts(img)
        # Detect already-preprocessed input (same heuristic as the GUI runner).
        n = img.astype(np.float32)
        m = float(n.max())
        if m > 0:
            n = n / m
        nz = n[n > 0.001]
        already = (nz.size > 10 and float(np.median(nz)) < 0.05)
        if already:
            return soft_foreground_suppression(
                img, ball_radius,
                strength=sp.get('strength'), log_p=sp.get('log_p'),
                con_p=sp.get('con_p'), min_area=sp.get('min_area'),
                border_grow=sp.get('border_grow')).astype(np.float32)
        return rb_gaussian_bg_removal_with_edge_enhancement(img, ball_radius).astype(np.float32)

    if on_fluor:
        fluor_proc = state.get('preprocessed_fluorescence',
                               state.get('fluorescence_image', state['image']))
        state['preprocessed_fluorescence'] = _enhance(fluor_proc)
        _save_array(state['preprocessed_fluorescence'],
                    output_dir / f"{image_path.stem}_bg_removed.tiff")
        print(f"[PyCAT Batch]   Background removal done (active layer: fluorescence).")
    else:
        state['preprocessed'] = _enhance(preprocessed)
        _save_array(state['preprocessed'],
                    output_dir / f"{image_path.stem}_bg_removed.tiff")
        print(f"[PyCAT Batch]   Background removal done (active layer: segmentation).")


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


def replay_set_frame_range(state: dict, image_path: Path, params: dict, output_dir: Path):
    """
    Restore the frame range and optional XY ROI crop recorded from the GUI session.
    Slices all loaded image arrays to the correct temporal and spatial region
    so all downstream replay steps see the same data as the GUI session did.
    """
    t_start = params.get('frame_start', 0)
    t_end   = params.get('frame_end', 9999)
    ref     = params.get('reference_frame', 0)
    roi_active = params.get('roi_active', False)
    y0 = params.get('roi_y0', 0)
    y1 = params.get('roi_y1', None)   # None = full extent
    x0 = params.get('roi_x0', 0)
    x1 = params.get('roi_x1', None)

    if state.get('data_instance'):
        dr = state['data_instance'].data_repository
        dr['timeseries_frame_start']     = t_start
        dr['timeseries_frame_end']       = t_end
        dr['timeseries_reference_frame'] = ref
        dr['timeseries_n_frames']        = t_end - t_start + 1
        dr['timeseries_roi_active']      = roi_active
        dr['timeseries_roi_y0']          = y0
        dr['timeseries_roi_y1']          = y1
        dr['timeseries_roi_x0']          = x0
        dr['timeseries_roi_x1']          = x1

    # Apply temporal slice then spatial crop to all image arrays in state
    for key in ('image', 'preprocessed', 'fluorescence_image'):
        arr = state.get(key)
        if arr is None or not hasattr(arr, 'ndim'):
            continue
        # Temporal slice
        if arr.ndim == 3:
            t_end_clamped = min(t_end, arr.shape[0] - 1)
            arr = arr[t_start:t_end_clamped + 1]
        # Spatial crop
        if roi_active:
            _y1 = y1 if y1 is not None else arr.shape[-2]
            _x1 = x1 if x1 is not None else arr.shape[-1]
            if arr.ndim == 3:
                arr = arr[:, y0:_y1, x0:_x1]
            elif arr.ndim == 2:
                arr = arr[y0:_y1, x0:_x1]
        state[key] = arr

    roi_str = (f", ROI y[{y0}:{y1}] x[{x0}:{x1}]" if roi_active else "")
    print(f"[PyCAT Batch]   Frame range: {t_start}\u2013{t_end} "
          f"({t_end - t_start + 1} frames, reference={ref}){roi_str}")


def replay_auto_crop_roi(state: dict, image_path: Path, params: dict, output_dir: Path):
    """
    Automatically detect per-cell bounding boxes for efficient batch processing.

    In the GUI, users draw a rectangle to restrict spatial processing.
    In batch mode this step computes equivalent bounding boxes automatically
    using one of two strategies:

    Strategy A — 'cellpose' (default when a cell mask is available):
        Uses bounding boxes from the labeled cell mask already in state.
        Each cell is cropped individually for condensate segmentation.
        Requires cellpose_segmentation to have run first.

    Strategy B — 'multi_otsu':
        Three-class multi-Otsu thresholding finds the foreground (non-
        background) region automatically. The bounding box of all foreground
        pixels is used as a single global crop, and a binary cell mask is
        generated from the thresholding result.
        Use for single-channel images without a cell segmentation step,
        or to restrict processing to a tissue sub-region.

    Both strategies store bboxes in state['cell_bboxes'] as:
        {cell_label: (y0, y1, x0, x1)}

    replay_condensate_segmentation reads this dict to process each cell
    in its own tight crop rather than operating on the full image.
    """
    from pycat.toolbox.batch_roi_tools import (
        cell_bboxes_from_mask, multi_otsu_foreground_bbox,
        multi_otsu_cell_mask,
    )

    strategy = params.get('strategy', 'auto')
    padding  = int(params.get('padding_px', 8))

    # Resolve strategy: 'auto' = cellpose if mask exists, else multi_otsu
    labeled_cells = state.get('labeled_cells') or state.get('cellpose_mask')
    if strategy == 'auto':
        strategy = 'cellpose' if (labeled_cells is not None and
                                   labeled_cells.max() > 0) else 'multi_otsu'

    if strategy == 'cellpose' and labeled_cells is not None and labeled_cells.max() > 0:
        bboxes = cell_bboxes_from_mask(labeled_cells, padding_px=padding)
        state['cell_bboxes'] = bboxes
        print(f"[PyCAT Batch]   Auto-crop (Cellpose): {len(bboxes)} cell bounding boxes computed.")
        for lbl, (y0, y1, x0, x1) in list(bboxes.items())[:3]:
            print(f"    Cell {lbl}: y[{y0}:{y1}] x[{x0}:{x1}]  "
                  f"({y1-y0}×{x1-x0}px)")
        if len(bboxes) > 3:
            print(f"    ... and {len(bboxes)-3} more")

    else:
        # Multi-Otsu strategy
        # Multi-Otsu is SCALE-INVARIANT (it thresholds on the histogram's shape), so the
        # normalisation is harmless here — unlike the rolling-ball steps. Left as-is.
        image = _normalize_to_float(state.get('preprocessed', state['image']))
        n_classes = int(params.get('n_otsu_classes', 3))
        bbox = multi_otsu_foreground_bbox(image, n_classes=n_classes,
                                           padding_px=padding)
        if bbox is None:
            print("[PyCAT Batch]   Auto-crop (multi-Otsu): no foreground detected — "
                  "using full image.")
            state['cell_bboxes'] = None
            return

        y0, y1, x0, x1 = bbox
        print(f"[PyCAT Batch]   Auto-crop (multi-Otsu): foreground bbox "
              f"y[{y0}:{y1}] x[{x0}:{x1}]  ({y1-y0}×{x1-x0}px)")

        # Generate a pseudo-cell mask from multi-Otsu for downstream steps
        # that expect a labeled_cells array (condensate_segmentation, analysis)
        if state.get('labeled_cells') is None:
            _cdiam = params.get('cell_diameter', 100)
            otsu_mask = multi_otsu_cell_mask(image, n_classes=n_classes,
                                              cell_diameter=int(_cdiam))
            state['cellpose_mask'] = otsu_mask
            state['labeled_cells'] = otsu_mask
            print(f"[PyCAT Batch]   Multi-Otsu cell mask: "
                  f"{otsu_mask.max()} regions found.")

        state['cell_bboxes'] = {lbl: bbox
                                  for lbl in np.unique(state['labeled_cells'])
                                  if lbl > 0}




# ---------------------------------------------------------------------------
# Brightfield / In-vitro replay functions
# ---------------------------------------------------------------------------

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
        except Exception:
            model = models.CellposeModel(pretrained_model='cyto2')
        masks, _, _ = model.eval(image, diameter=diameter, channels=[0, 0])
        state['bf_cell_mask'] = masks.astype(np.int32)
        _save_array(masks.astype(np.uint16),
                    output_dir / f"{image_path.stem}_bf_cell_mask.tiff")
        print(f"[PyCAT Batch]   BF cell segmentation: {int(masks.max())} cells.")
    except Exception as e:
        print(f"[PyCAT Batch]   BF cell segmentation failed: {e} — skipping.")


def replay_ivf_preprocess(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay in vitro fluorescence preprocessing (no cell mask)."""
    from pycat.toolbox.image_processing_tools import pre_process_image

    # RAW counts: this feeds `pre_process_image`, whose rolling ball is NOT scale-invariant.
    # `_normalize_to_float` subtracts the pedestal; the GUI does not. See _proc, above.
    image = _raw_counts(state.get('preprocessed', state['image']))
    ball  = params.get('ball_radius', 15)
    proc  = pre_process_image(image, ball_radius=ball, window_size=ball * 2)
    state['preprocessed'] = np.asarray(proc).astype(np.float32)

    _save_array(state['preprocessed'],
                output_dir / f"{image_path.stem}_ivf_preprocessed.tiff")
    print(f"[PyCAT Batch]   In vitro fluorescence preprocessing done.")


def _ivf_droplet_mask_and_image(state):
    """Fetch the in vitro droplet mask and a 2D fluorescence image from state."""
    mask = state.get('ivf_droplet_mask')
    if mask is None:
        mask = state.get('labeled_cells')
    # ── The ORIGINAL image, NOT `preprocessed` ─────────────────────────────────
    #
    # `state['preprocessed']` is the output of `pre_process_image`: a white top-hat, a LoG
    # enhancement and WBNS wavelet denoising. That chain is built for SEGMENTATION, and it
    # is designed to destroy exactly the thing an intensity measurement needs.
    #
    # Measured on a droplet field with a TRUE Kp of 30:
    #
    #     image                    I_dense   I_dilute   ratio
    #     RAW counts                3500.1      600.0    5.83
    #     after white-tophat        2914.4       14.6  199.27   <- background REMOVED
    #     after tophat + LoG          48.6       -4.1  -11.96   <- NEGATIVE
    #
    # The white top-hat removes the background — which is its purpose — so the dilute phase
    # goes to ~0 and the ratio explodes. The LoG is a SIGNED operator centred on zero, so
    # the dilute-phase mean goes NEGATIVE, and **a ratio of two numbers straddling zero is
    # not a physical quantity at all.** A negative partition coefficient.
    #
    # This is a deeper version of the normalisation bug (1.5.424-426): normalisation moved
    # the zero point, but the preprocessing chain removes the background entirely and then
    # takes a signed derivative of it. Intensity measurements need the ORIGINAL image.
    img = state.get('image')
    if img is None:
        img = state.get('preprocessed')      # nothing else available; better than failing
    img = _raw_counts(img) if img is not None else None
    if img is not None and img.ndim == 3:
        img = img[0]
    return mask, img


def replay_ivf_field_summary(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay in vitro field summary + partition coefficient from the droplet mask."""
    from pycat.toolbox.invitro_tools import field_summary, partition_coefficient_field
    import pandas as pd
    mask, img = _ivf_droplet_mask_and_image(state)
    if mask is None or img is None:
        print('[PyCAT Batch]   IVF field summary skipped (no droplet mask/image in state).')
        return
    mpx = state['data_instance'].data_repository.get('microns_per_pixel_sq', 1.0) ** 0.5
    summ = field_summary(mask, img, mpx)
    part = partition_coefficient_field(img, mask)
    pd.DataFrame([summ]).to_csv(
        output_dir / f"{image_path.stem}_ivf_field_summary.csv", index=False)
    if isinstance(part.get('per_droplet_df'), pd.DataFrame):
        part['per_droplet_df'].to_csv(
            output_dir / f"{image_path.stem}_ivf_partition.csv", index=False)
    print(f"[PyCAT Batch]   IVF field summary: Phi={summ.get('volume_fraction', float('nan')):.3f}, "
          f"n={summ.get('n_droplets', 0)}.")


def replay_ivf_size_distribution(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay in vitro droplet size-distribution fit from the droplet mask."""
    from pycat.toolbox.invitro_tools import fit_size_distribution
    import pandas as pd, numpy as np
    import skimage as sk
    mask, _ = _ivf_droplet_mask_and_image(state)
    if mask is None:
        print('[PyCAT Batch]   IVF size distribution skipped (no droplet mask in state).')
        return
    mpx = state['data_instance'].data_repository.get('microns_per_pixel_sq', 1.0) ** 0.5
    props = sk.measure.regionprops(mask.astype(np.int32))
    radii = np.array([np.sqrt(p.area * mpx**2 / np.pi) for p in props])
    if len(radii) < 5:
        print(f'[PyCAT Batch]   IVF size distribution skipped ({len(radii)} droplets < 5).')
        return
    res = fit_size_distribution(radii, n_bins=int(params.get('n_bins', 30)))
    row = {k: v for k, v in res.items() if not hasattr(v, '__len__')}
    pd.DataFrame([row]).to_csv(
        output_dir / f"{image_path.stem}_ivf_size_distribution.csv", index=False)
    print(f"[PyCAT Batch]   IVF size distribution: {res.get('preferred_model', '?')} preferred.")


def replay_ivf_spatial_metrology(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay in vitro spatial metrology (whole field as one 'cell') from the droplet mask."""
    from pycat.toolbox.spatial_metrology_tools import get_puncta_centroids, run_all_spatial_metrics
    import numpy as np
    import pandas as pd

    def _flatten_scalars(prefix, obj, out):
        # Recursively collect scalar (non-array) values into flat columns.
        if isinstance(obj, dict):
            for k, v in obj.items():
                _flatten_scalars(f"{prefix}_{k}" if prefix else str(k), v, out)
        elif np.isscalar(obj):
            out[prefix] = obj

    mask, _ = _ivf_droplet_mask_and_image(state)
    if mask is None:
        print('[PyCAT Batch]   IVF spatial metrology skipped (no droplet mask in state).')
        return
    mpx = state['data_instance'].data_repository.get('microns_per_pixel_sq', 1.0) ** 0.5
    H, W = mask.shape[:2]
    field_lbl = np.ones((H, W), dtype=np.int32); field_lbl[:2, :2] = 0
    coords_df = get_puncta_centroids(mask, field_lbl, mpx)
    rows = []
    for cl in [c for c in coords_df['cell_label'].unique() if c != 0]:
        sub = coords_df[coords_df['cell_label'] == cl]
        coords = sub[['y_um', 'x_um']].values
        if len(coords) < 2:
            continue
        res = run_all_spatial_metrics(coords, (field_lbl == cl), mpx)
        row = {'field_label': int(cl)}
        _flatten_scalars('', res, row)
        rows.append(row)
    if not rows:
        print('[PyCAT Batch]   IVF spatial metrology skipped (<2 droplets).')
        return
    pd.DataFrame(rows).to_csv(
        output_dir / f"{image_path.stem}_ivf_spatial_metrology.csv", index=False)
    print(f"[PyCAT Batch]   IVF spatial metrology: {len(rows)} field(s) analysed.")


def replay_ivf_segmentation(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay in vitro fluorescence droplet segmentation (whole field, no cell mask)."""
    from pycat.toolbox.segmentation_tools import (
        segment_subcellular_objects, cell_mask_stretching)
    import pandas as pd

    pre = state.get('preprocessed', state['image'])
    raw = _normalize_to_float(state['image'])
    ball = state['data_instance'].data_repository.get('ball_radius', 15)

    H, W = pre.shape
    whole = np.ones((H, W), dtype=bool)
    whole[:2, :2] = False
    cms = cell_mask_stretching(pre, whole.astype(int))

    refined, unrefined = segment_subcellular_objects(
        raw.copy(), cms.copy(), whole, 1, ball, cell_df=None,
        min_spot_radius=params.get('min_radius', 2.0),
        kurtosis_threshold=params.get('kurtosis', -3.0),
        local_snr_threshold=params.get('local_snr_threshold', 0.8),
        global_snr_threshold=0.8,
    )
    import skimage as sk
    labeled = sk.measure.label(refined).astype(np.int32)
    state['ivf_droplet_mask'] = labeled
    state['cellpose_mask']    = labeled
    state['labeled_cells']    = labeled

    _save_array(labeled.astype(np.uint16),
                output_dir / f"{image_path.stem}_ivf_droplet_mask.tiff")
    print(f"[PyCAT Batch]   In vitro fluorescence segmentation: {int(labeled.max())} droplets.")


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


_STEP_MAP = {
    'open_image':               replay_open_image,
    'open_stack':               replay_open_stack,    # unified IMS + TIFF stack
    'set_frame_range':          replay_set_frame_range,
    'auto_crop_roi':            replay_auto_crop_roi,
    'lazy_preprocess_stack':    lambda s,p,pa,o: print('[PyCAT Batch]   TS preprocessing skipped in headless mode (zarr cache required).'),
    'open_ims_file':            replay_open_stack,    # legacy key — keep for old configs
    'open_image_stack':         replay_open_stack,    # legacy key — keep for old configs
    'measure_line':              replay_measure_line,
    'upscaling':                replay_upscaling,
    'preprocessing':            replay_preprocessing,
    'background_removal':       replay_background_removal,
    'calibration_correction':   replay_calibration_correction,
    'cellpose_segmentation':    replay_cellpose_segmentation,
    'cell_analysis':            replay_cell_analysis,
    'condensate_segmentation':  replay_condensate_segmentation,
    'condensate_analysis':      replay_condensate_analysis,
    'sacf_analysis':            replay_sacf_analysis,
    'ts_cellpose_keyframe':      replay_ts_cellpose_keyframe,
    'spatial_metrology':        lambda s,p,pa,o: print('[PyCAT Batch]   Spatial metrology skipped in headless mode.'),
    'dynamic_spatial':        lambda s,p,pa,o: print('[PyCAT Batch]   dynamic_spatial skipped.'),
    'organizational_metrics':        lambda s,p,pa,o: print('[PyCAT Batch]   organizational_metrics skipped.'),
    'export_timeseries_video':            lambda s,p,pa,o: print('[PyCAT Batch]   Video export skipped in headless mode.'),
    'timeseries_condensate_analysis':     lambda s,p,pa,o: print('[PyCAT Batch]   TS condensate analysis skipped in headless mode.'),
    'two_channel_condensate_coloc':       lambda s,p,pa,o: print('[PyCAT Batch]   Two-channel colocalization skipped in headless mode.'),
    'bf_preprocess':              replay_bf_preprocess,
    'bf_cell_segmentation':       replay_bf_cell_segmentation,
    'bf_condensate_segmentation': replay_bf_condensate_segmentation,
    'ivf_preprocess':             replay_ivf_preprocess,
    'ivf_segmentation':           replay_ivf_segmentation,
    'ivf_field_summary':          replay_ivf_field_summary,
    'ivf_size_distribution':      replay_ivf_size_distribution,
    'ivf_spatial_metrology':      replay_ivf_spatial_metrology,
    'ivf_dynamics':               lambda s,p,pa,o: print(
        '[PyCAT Batch]   IVF dynamics skipped (time-series; not a per-image batch step).'),
    'ivf_phase_diagram':          lambda s,p,pa,o: print(
        '[PyCAT Batch]   IVF phase diagram skipped (dilution series; manual multi-condition input).'),
    'ivf_frame_qc':               lambda s,p,pa,o: print(
        '[PyCAT Batch]   IVF frame QC skipped (time-series; not a per-image batch step).'),
    'msd_analysis':               lambda s,p,pa,o: print(
        '[PyCAT Batch]   MSD / condensate biophysics skipped (time-series; not a per-image batch step).'),
    'ivbf_preprocess':            replay_ivbf_preprocess,
    'ivbf_segmentation':          replay_ivbf_segmentation,
    'zstack_bg_removal':              lambda s,p,pa,o: print(
        '[PyCAT Batch]   Z-stack background removal skipped in headless mode '
        '(batch file loading only extracts single 2D planes — 3D volume batch '
        'processing is not yet supported; run this step interactively in the '
        'Z-Stack Condensate Analysis dock).'),
    'zstack_cell_segmentation':       lambda s,p,pa,o: print(
        '[PyCAT Batch]   Z-stack cell segmentation skipped in headless mode '
        '(requires a 3D volume — not available via batch file loading).'),
    'zstack_condensate_segmentation': lambda s,p,pa,o: print(
        '[PyCAT Batch]   Z-stack condensate segmentation skipped in headless mode '
        '(requires a 3D volume — not available via batch file loading).'),
    # Interactive general-analysis steps — recorded for provenance but not
    # replayed headlessly (they are exploratory tools that require interactive
    # layer/ROI selection rather than a fixed automated sequence).
    'local_thresholding':       lambda s,p,pa,o: print(
        '[PyCAT Batch]   local_thresholding skipped in headless mode '
        '(interactive general-analysis step).'),
    'label_binary_mask':        lambda s,p,pa,o: print(
        '[PyCAT Batch]   label_binary_mask skipped in headless mode '
        '(interactive general-analysis step).'),
    'measure_region_props':     lambda s,p,pa,o: print(
        '[PyCAT Batch]   measure_region_props skipped in headless mode '
        '(interactive general-analysis step).'),
    # Video Particle Tracking — interactive microrheology; recorded for
    # provenance but not headlessly replayed (requires interactive channel
    # selection and produces terminal microrheology output, not stored masks).
    'vpt_segment_host':         lambda s,p,pa,o: print(
        '[PyCAT Batch]   VPT host segmentation skipped in headless mode '
        '(interactive multichannel step).'),
    'vpt_infer_host':           lambda s,p,pa,o: print(
        '[PyCAT Batch]   VPT infer-host-from-beads skipped in headless mode '
        '(interactive step).'),
    'vpt_detect_beads':         lambda s,p,pa,o: print(
        '[PyCAT Batch]   VPT bead detection skipped in headless mode.'),
    'vpt_link_trajectories':    lambda s,p,pa,o: print(
        '[PyCAT Batch]   VPT trajectory linking skipped in headless mode.'),
    'vpt_microrheology':        lambda s,p,pa,o: print(
        '[PyCAT Batch]   VPT microrheology skipped in headless mode '
        '(terminal reporting step).'),
    # FRAP — interactive ROI selection; recorded for provenance, not replayed.
    'frap_define_roi':          lambda s,p,pa,o: print(
        '[PyCAT Batch]   FRAP ROI definition skipped in headless mode.'),
    'frap_analysis':            lambda s,p,pa,o: print(
        '[PyCAT Batch]   FRAP analysis skipped in headless mode '
        '(interactive ROI + terminal reporting step).'),
    # Droplet Fusion — interactive fit-window selection; recorded, not replayed.
    'fusion_build_signal':      lambda s,p,pa,o: print(
        '[PyCAT Batch]   Fusion signal build skipped in headless mode.'),
    'fusion_fit':               lambda s,p,pa,o: print(
        '[PyCAT Batch]   Fusion fit skipped in headless mode '
        '(interactive fit-window + terminal reporting step).'),
    'spatial_randomness':       lambda s,p,pa,o: print(
        '[PyCAT Batch]   spatial_randomness skipped in headless mode '
        '(interactive image/ROI selection step).'),
    # Temperature-dependent condensate — interactive sync/annotation; recorded.
    'temperature_sync':         lambda s,p,pa,o: print(
        '[PyCAT Batch]   Temperature sync skipped in headless mode '
        '(interactive TIFF/CSV selection).'),
    'temperature_turbidity':    lambda s,p,pa,o: print(
        '[PyCAT Batch]   Temperature turbidity skipped in headless mode.'),
    'temperature_export_video': lambda s,p,pa,o: print(
        '[PyCAT Batch]   Temperature annotated export skipped in headless mode.'),
    # Force-Distance curve — interactive .h5 load + channel/plot selection.
    'fd_load':                  lambda s,p,pa,o: print(
        '[PyCAT Batch]   FD load skipped in headless mode (interactive .h5 selection).'),
    'fd_segment':               lambda s,p,pa,o: print(
        '[PyCAT Batch]   FD segment skipped in headless mode.'),
    'fd_plot':                  lambda s,p,pa,o: print(
        '[PyCAT Batch]   FD plot skipped in headless mode.'),
    'fd_export':                lambda s,p,pa,o: print(
        '[PyCAT Batch]   FD export skipped in headless mode.'),
    'fd_rips':                  lambda s,p,pa,o: print(
        '[PyCAT Batch]   FD rip detection skipped in headless mode.'),
    'molecular_counting':       lambda s,p,pa,o: print(
        '[PyCAT Batch]   Molecular counting skipped in headless mode '
        '(interactive stack/mask selection).'),
    'gaussian_localization':    lambda s,p,pa,o: print(
        '[PyCAT Batch]   Gaussian localization skipped in headless mode '
        '(interactive image/points selection).'),
    'client_enrichment':        lambda s,p,pa,o: print(
        '[PyCAT Batch]   Client enrichment skipped in headless mode '
        '(interactive layer selection).'),
    'intensity_profile':        lambda s,p,pa,o: print(
        '[PyCAT Batch]   Intensity profile skipped in headless mode '
        '(interactive shapes/points selection).'),
    'morphological_complexity': lambda s,p,pa,o: print(
        '[PyCAT Batch]   Morphological complexity skipped in headless mode '
        '(interactive mask selection).'),
    'save_and_clear':           replay_save_and_clear,
}


def register_all_steps(bp: "BatchProcessor"):
    # A duplicate key in _STEP_MAP is silently swallowed by Python (the later
    # entry wins), which makes it a latent trap: someone implements a real
    # replay handler, a stale skip-stub further down the dict overrides it, and
    # they debug a handler that never runs. `morphological_complexity` was
    # registered twice for exactly this reason. The dict literal cannot report
    # it, so check the SOURCE for repeated keys at import time and say so.
    _warn_on_duplicate_step_keys()
    for name, fn in _STEP_MAP.items():
        bp.register_step(name, fn)
    print(f"[PyCAT Batch] Registered {len(_STEP_MAP)} headless replay steps.")


def _warn_on_duplicate_step_keys():
    """Report any step name written more than once in the _STEP_MAP literal."""
    try:
        import ast, inspect, collections
        tree = ast.parse(inspect.getsource(inspect.getmodule(register_all_steps)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "_STEP_MAP"
                       for t in node.targets):
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            keys = [k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            dups = [k for k, n in collections.Counter(keys).items() if n > 1]
            if dups:
                print("[PyCAT Batch] WARNING: duplicate replay step name(s) in "
                      f"_STEP_MAP: {dups}. The LAST definition wins and the "
                      "earlier one is silently discarded.")
    except Exception:
        pass
