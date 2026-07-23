"""Batch replay handlers (io steps), moved from batch_step_registry.py (decomposition, 1.6.150).
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

    Split-file recordings (multiple SEPARATE 'open_image' steps -- e.g. two
    single-channel files opened as separate layers, rather than multi-
    selected in one dialog, so channel_assignment is empty on every step):
    the first call for a given batch sample loads the PRIMARY file
    (image_path, as normal, below). ``batch_processor._process_file`` stamps
    ``state['_primary_open_image_stem']`` before replay starts whenever more
    than one open_image step was recorded. Every open_image call AFTER the
    first one in that same replay must NOT reload image_path again (it
    would just reload the primary file a second time — the original bug
    here) — it must locate THIS step's own recorded file's companion for
    the CURRENT batch sample and stash it under its recorded layer name in
    state['channels_by_name'], leaving state['image']/['preprocessed']
    (the primary) untouched.
    """
    from pycat.data.data_modules import BaseDataClass

    primary_stem = state.get('_primary_open_image_stem')
    if primary_stem is not None:
        calls_so_far = state.get('_open_image_calls', 0)
        state['_open_image_calls'] = calls_so_far + 1
        if calls_so_far >= 1:
            recorded_stem = Path(params.get('file_path', '') or '').stem
            layer_name = params.get('_active_layer_at_record') or recorded_stem or 'companion'
            if not recorded_stem or recorded_stem == primary_stem:
                # Nothing to resolve: no recorded path, or same file as the
                # primary (e.g. a duplicate open_image step).
                return
            try:
                companion_path = _derive_split_companion_path(
                    image_path, primary_stem, recorded_stem)
                companion_image, _ = _load_image(companion_path, channel=0)
            except Exception as _e:
                print(f"[PyCAT Batch]   Companion file for layer "
                      f"'{layer_name}' could not be loaded ({_e}) — this "
                      f"layer will be unavailable to later steps.")
                return
            state.setdefault('channels_by_name', {})[layer_name] = companion_image
            print(f"[PyCAT Batch]   Loaded companion file {companion_path.name} "
                  f"as layer '{layer_name}'  shape={companion_image.shape}")
            return

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
        except Exception as _e:  # broad-ok: batch replay robustness — logged, this step is skipped, the batch continues
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


def replay_save_and_clear(state: dict, image_path: Path, params: dict, output_dir: Path):
    """No-op in headless mode — files are already saved by each step above."""
    print(f"[PyCAT Batch]   All outputs saved to {output_dir}")
    state.clear()


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
