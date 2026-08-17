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


def _finalize_ball_radius(state: dict, image_path: Path) -> None:
    """Resolve the deferred ball_radius auto-estimate once the TRUE fluorescence
    channel is known, and write it to ``data_instance.data_repository``.

    ``replay_open_image`` cannot estimate ball_radius from the fluorescence
    signal until ``state['fluorescence_image']`` actually holds it. For a
    single/multi-channel file that resolves immediately; for a split-file
    recording (separate 'open_image' steps, no channel_assignment —
    see ``_replay_split_file_companion``), the real fluorescence channel only
    arrives on a LATER companion call, so ``replay_open_image`` defers by
    setting ``state['_ball_radius_pending']`` and this is called once the
    companion (or the failure/no-op fallback) settles ``fluorescence_image``.

    Estimating from the wrong channel is not a hypothetical: on a DAPI (seg) +
    GFP (fluorescence) split recording, estimating on the still-placeholder
    ``fluorescence_image`` (== the DAPI array, before the companion loads)
    measured ~3px nuclear puncta -> ball_radius=1, when the real GFP
    condensates the user hand-measured were ~4px. Reported by Meet Raval.
    """
    if not state.pop('_ball_radius_pending', False):
        return
    data_instance = state.get('data_instance')
    if data_instance is None:
        return
    recorded = state.pop('_ball_radius_recorded', None)
    ball_radius = recorded
    fluor_image = state.get('fluorescence_image')
    fluor_source = state.get('_fluorescence_source_name', '?')
    if state.get('_auto_ball_radius') and fluor_image is not None:
        try:
            from pycat.toolbox.image_processing_tools import estimate_object_size_px
            _est = estimate_object_size_px(fluor_image)
            _estimated = _est.get('ball_radius')
            if _estimated:
                if recorded is not None and recorded == _estimated:
                    print(f"[PyCAT Batch]   ball_radius = {_estimated} "
                          f"(recorded value agrees with the estimate; measured on "
                          f"'{fluor_source}') for {image_path.name}.")
                else:
                    ball_radius = _estimated
                    print(f"[PyCAT Batch]   Auto ball_radius = {ball_radius} "
                          f"(object_size {_est['object_size_px']:.1f}px from "
                          f"{_est['n_objects']} objects, measured on '{fluor_source}') "
                          f"for {image_path.name}"
                          + (f" — overriding recorded {recorded}."
                             if recorded is not None else "."))
        except Exception as _e:  # broad-ok: optional_probe — auto ball_radius estimation is best-effort; on failure the recorded/default value is used
            print(f"[PyCAT Batch]   Auto ball_radius estimation failed "
                  f"({_e}); using recorded/default.")
    if ball_radius is None:
        ball_radius = 50
    data_instance.data_repository['ball_radius'] = ball_radius


def _replay_split_file_companion(state: dict, image_path: Path, params: dict) -> bool:
    """Handle split-file recordings (multiple SEPARATE 'open_image' steps -- e.g. two
    single-channel files opened as separate layers, so channel_assignment is empty on
    every step). ``batch_processor`` stamps ``state['_primary_open_image_stem']`` when
    more than one open_image step was recorded; the first call loads the primary as
    normal, and every call AFTER it must locate THIS step's own companion file for the
    current batch sample and stash it under its recorded layer name, leaving the primary
    state['image']/['preprocessed'] untouched (reloading image_path again was the bug).

    Returns True if this call was a companion (caller should return early), False to fall
    through and load the primary file below."""
    primary_stem = state.get('_primary_open_image_stem')
    if primary_stem is None:
        return False
    calls_so_far = state.get('_open_image_calls', 0)
    state['_open_image_calls'] = calls_so_far + 1
    if calls_so_far < 1:
        # The primary's OWN recorded name — a split-file recording never has a
        # "Segmentation Image" keyword to match on, so _resolve_image_layer /
        # _active_layer_channel_role need this to recognise a step recorded
        # against e.g. "Upscaled In_Cell" as the primary channel.
        state['_primary_channel_name'] = params.get('_active_layer_at_record')
        return False                       # first call: load the primary below
    recorded_stem = Path(params.get('file_path', '') or '').stem
    layer_name = params.get('_active_layer_at_record') or recorded_stem or 'companion'
    if not recorded_stem or recorded_stem == primary_stem:
        _finalize_ball_radius(state, image_path)
        return True                        # no recorded path, or same file as the primary
    try:
        companion_path = _derive_split_companion_path(image_path, primary_stem, recorded_stem)
        companion_image, _ = _load_image(companion_path, channel=0)
    except Exception as _e:  # broad-ok: batch_step — companion load failure is logged and the layer left unavailable to later steps, not silently swallowed
        print(f"[PyCAT Batch]   Companion file for layer '{layer_name}' could not be "
              f"loaded ({_e}) — this layer will be unavailable to later steps.")
        _finalize_ball_radius(state, image_path)
        return True
    state.setdefault('channels_by_name', {})[layer_name] = companion_image
    # ── This companion is also the FLUORESCENCE role, if no other channel claimed it ──
    #
    # A split-file recording never sets a channel_assignment, so `replay_open_image`'s
    # normal seg/fluor split (lines below) never ran — `state['fluorescence_image']` is
    # still just an ALIAS of the primary image object from the first `open_image` call.
    # Left that way, every later step that reads `state['fluorescence_image']` (the
    # preprocessing/background-removal fallback, and `_resolve_image_layer`'s own
    # fallback for an unmatched name) silently gets the PRIMARY/segmentation channel
    # instead of this companion — the recorded pipeline's condensate-marker channel
    # never actually reaches any step that resolves it that way. Only claim the role
    # once (the first companion establishes it); a 3rd+ companion is a named extra
    # channel, not a second "the" fluorescence channel.
    if state.get('fluorescence_image') is state.get('image'):
        state['fluorescence_image'] = companion_image
        state['_fluorescence_source_name'] = companion_path.name
    print(f"[PyCAT Batch]   Loaded companion file {companion_path.name} "
          f"as layer '{layer_name}'  shape={companion_image.shape}")
    _finalize_ball_radius(state, image_path)
    return True


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

    Split-file recordings (separate 'open_image' steps) are handled up front
    by _replay_split_file_companion; see its docstring.
    """
    from pycat.data.data_modules import BaseDataClass

    if _replay_split_file_companion(state, image_path, params):
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
    # object_size: falls back to the SAME image-size-based formula the GUI uses
    # as its own default at load time (file_io.py: `_last_channel.shape[0] //
    # 20`) rather than BaseDataClass's flat placeholder of 50 -- a config
    # recorded before this key existed (or a session where the user never
    # touched Measure Line, which is not gated and can be skipped) must still
    # land on the GUI's actual default, not an arbitrary constant that can be
    # off by 2x+ for images far from ~1000px. Superseded by replay_measure_line
    # if that step was recorded, exactly like the GUI's own Measure Line click.
    data_instance.data_repository['object_size'] = params.get(
        'object_size', image.shape[-2] // 20)
    # Placeholder until _finalize_ball_radius resolves the real value below —
    # covers the (should-never-happen) case where something reads ball_radius
    # before finalize runs.
    data_instance.data_repository['ball_radius'] = params.get('ball_radius', 50)

    state['image'] = image
    state['preprocessed'] = image.copy()
    state['data_instance'] = data_instance

    # Load the fluorescence channel separately if it differs from the
    # segmentation channel — used later by condensate segmentation/analysis
    if fluor_channel != seg_channel:
        fluor_path = _source_path_for_recorded_channel(image_path, channel_assignment, fluor_channel)
        fluor_image, _ = _load_image(fluor_path, channel=fluor_channel if fluor_path == image_path else 0)
        state['fluorescence_image'] = fluor_image
        state['_fluorescence_source_name'] = f"{fluor_path.name} (channel {fluor_channel})"
    else:
        state['fluorescence_image'] = image
        state['_fluorescence_source_name'] = f"{seg_path.name} (channel {seg_channel}, same as segmentation)"

    # ball_radius: when the batch enabled automatic object-size estimation (valid
    # fluorescence workflow — see BatchWorker._auto_ball_radius_active), estimate
    # it from the FLUORESCENCE signal (top-hat + Otsu → median object diameter),
    # not the segmentation channel — they can be entirely different stains (e.g.
    # DAPI segmentation + GFP condensate marker). For a split-file recording (no
    # channel_assignment; see _replay_split_file_companion) the true fluorescence
    # channel is still just this call's placeholder (== the segmentation image)
    # until the companion file loads on a LATER open_image step, so estimating
    # now would measure the wrong channel's objects — defer to
    # _finalize_ball_radius, which _replay_split_file_companion calls once the
    # companion (or its failure/no-op fallback) settles fluorescence_image.
    state['_ball_radius_recorded'] = params.get('ball_radius', None)
    state['_ball_radius_pending'] = True
    if state.get('_primary_open_image_stem') is None:
        _finalize_ball_radius(state, image_path)

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
            # The primary channel's OWN recorded name — see _replay_split_file_companion's
            # matching comment; _resolve_image_layer / _active_layer_channel_role check
            # this by name rather than array identity (upscaling leaves this entry's
            # identity stale).
            if ch_num == seg_channel:
                state['_primary_channel_name'] = layer_name

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
