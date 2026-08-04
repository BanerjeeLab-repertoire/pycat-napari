"""Batch replay handlers (preprocessing steps), moved from batch_step_registry.py (decomposition, 1.6.150).
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
    _get_data, _derive_split_companion_path, _source_path_for_recorded_channel, _load_image, _resolve_channel_for_layer, _save_array, _raw_counts, _normalize_to_float, _resolve_image_layer, _ivf_droplet_mask_and_image, _active_layer_channel_role)


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
    # ball_radius: when this batch has per-image auto-estimation active, the
    # LIVE data_instance value must win over the recorded params snapshot — it
    # was captured once, on whichever image was active during the original GUI
    # recording, and replaying it unchanged for every file would silently
    # discard the per-image estimate replay_open_image/_finalize_ball_radius
    # (and its subsequent replay_upscaling doubling) just computed for THIS
    # file — the exact reason a recorded ball_radius isn't allowed to win over
    # the estimate anywhere else in the batch pipeline (see
    # BatchWorker._auto_ball_radius_active, replay_measure_line). Outside
    # auto-estimation, prefer the recorded params as before (legacy configs
    # fall back to data_instance).
    if state.get('_auto_ball_radius'):
        ball_radius = int(_get_data(data_instance, 'ball_radius', 50))
    else:
        ball_radius = int(params.get('ball_radius',
                          _get_data(data_instance, 'ball_radius', 50)))
    window_size = int(params.get('window_size',
                      _get_data(data_instance, 'cell_diameter', 100) // 2))

    # Foreground suppression: replay exactly what was recorded. Legacy configs
    # (no keys) default to suppression ON with tuned defaults, matching the
    # interactive default behaviour.
    suppress_foreground = bool(params.get('suppress_foreground', True))
    suppression_params = params.get('foreground_suppression_params', None)

    # Which layer was active when preprocessing was clicked? Keyword match for generic-
    # named configs, or a channels_by_name match for split-file / sample-identity-named
    # ones (e.g. "Upscaled In_Cell [1]") — see _active_layer_channel_role.
    active_name = str(params.get('active_layer')
                      or params.get('active_image_layer') or '').lower()
    on_fluor, _fluor_key = _active_layer_channel_role(state, active_name)

    def _proc(arr):
        # run_pre_process_image (the interactive path) caps ball_radius/window_size
        # to 5% of the image's own smaller dimension (min 4px) to avoid a
        # MemoryError from an oversized rolling-ball structuring element -- but
        # that cap is a LOCAL variable there, never written back to
        # data_repository, so the recorded ball_radius/window_size above are the
        # PRE-cap values. Replaying with the uncapped values silently applies a
        # larger ball_radius/window_size than the GUI actually used whenever the
        # cap would have triggered (small images, or after upscaling pushes
        # ball_radius close to/above the threshold) -- a different LoG sigma,
        # WBNS scale and CLAHE tile size, and the exact MemoryError risk the cap
        # exists to prevent. The cap must be recomputed per-image (not reused
        # from the recording) since a batch image's shape can differ from what
        # was used interactively.
        _max_radius = max(4, int(min(np.asarray(arr).shape[-2:]) * 0.05))
        _br = min(ball_radius, _max_radius)
        _ws = min(window_size, _max_radius * 2)
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
            _raw_counts(arr), _br, _ws,
            suppress_foreground=suppress_foreground,
            suppression_params=suppression_params)).astype(np.float32)

    if on_fluor:
        fluor = state.get('fluorescence_image', state['image'])
        state['preprocessed_fluorescence'] = _proc(fluor)
        # Named channel (resolved via channels_by_name, not the generic 'fluorescence'
        # keyword): record the PROCESSED result in its own dict, separate from the raw
        # one channels_by_name holds. A later step recorded on the RAW name (e.g.
        # cell_analysis's "Upscaled In_Cell [1]", no processing keyword) still needs the
        # untouched raw array — overwriting channels_by_name in place would break that.
        if _fluor_key is not None:
            state.setdefault('channels_processed_by_name', {})[_fluor_key] = (
                state['preprocessed_fluorescence'])
        # Segmentation channel was NOT the active layer -> leave it unprocessed.
        state.setdefault('preprocessed', np.asarray(state['image']).copy())
        _save_array(state['preprocessed_fluorescence'],
                    output_dir / f"{image_path.stem}_preprocessed.tiff")
        print("[PyCAT Batch]   Preprocessing done (active layer: fluorescence).")
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
        print("[PyCAT Batch]   Preprocessing done (active layer: segmentation).")


def replay_upscaling(state: dict, image_path: Path, params: dict, output_dir: Path):
    """
    Apply the same bicubic-interpolation upscaling used by run_upscaling_func
    in the GUI, doubling resolution (capped at 2048x2048). Updates both the
    raw image and preprocessed image in state, and the relevant data_instance
    fields, mirroring what the GUI does for every selected layer.

    Does NOT reproduce run_upscaling_func's dtype/range "correction" (clip or
    rescale toward [0, 1] / [0, 65535]) -- that logic is calibrated for the
    GUI's load-time convention (the interactive 2-D loader divides every pixel
    by the dtype max, so `run_upscaling_func` sees e.g. 4000 counts as ~0.06 and
    its `> 1.0` check never fires on a real image). Batch's `_load_image` keeps
    RAW counts (needed elsewhere -- see `_proc`'s "BATCH MUST PASS RAW COUNTS"
    comment below), so at THIS scale the identical `> 1.0` check would always
    fire and hard-clip every pixel above 1 count to 1.0, destroying the image.
    Applying GUI code to data at a different absolute scale doesn't reproduce
    GUI behaviour, it breaks batch -- confirmed: Cellpose count and cell_analysis
    both broke the one time this was tried. Since the GUI's correction is a
    no-op for any non-saturated real image, a plain floor-at-zero already
    matches the GUI's real-world numbers.
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
        # run_upscaling_func also scales object_size (image_processing/upscaling.py) --
        # replay previously only synced cell_diameter/ball_radius/microns_per_pixel_sq,
        # leaving object_size stale at its pre-upscale value for every batch run.
        data_instance.data_repository['object_size'] = (
            _get_data(data_instance, 'object_size', 50) * upscale_factor
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

    # Update channels_by_name (raw) and channels_processed_by_name (if a named
    # channel's preprocessing/background-removal already ran BEFORE upscaling —
    # an unusual order, but one the recorded config is free to produce) too.
    for channels_key in ('channels_by_name', 'channels_processed_by_name'):
        for name, arr in state.get(channels_key, {}).items():
            if arr is not None and arr is not image:
                cr, cc = arr.shape[-2], arr.shape[-1]
                if cr < 2048 and cc < 2048:
                    arr_up = upscale_image_interp(arr, cr, cc, upscale_factor=upscale_factor)
                    state[channels_key][name] = np.clip(arr_up, 0, None).astype(np.float32)

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
    from pycat.file_io.stack_access import to_unit_float32
    ref_raw = np.squeeze(np.asarray(tifffile.imread(calib)))
    # Normalise to the SAME [0, 1] dtype-max convention _load_image now applies to
    # `img` below -- a raw-counts reference against a [0, 1] image would scale-
    # mismatch the flatfield division / background subtraction (this file's own
    # values would come out ~65535x off from what they should correct). Signed-
    # integer sources are cast to uint16 first, same as _load_image -- see its
    # docstring for why (a confirmed exact-2x reader-disagreement case).
    if np.issubdtype(ref_raw.dtype, np.signedinteger):
        ref_raw = ref_raw.astype(np.uint16)
    ref = to_unit_float32(ref_raw, ref_raw.dtype)
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


