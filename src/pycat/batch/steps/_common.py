"""Shared helpers for the batch replay handlers, moved out of batch_step_registry.py (decomposition,
1.6.150). ONE copy, imported by every family module — duplicating them is the failure mode to avoid."""
from __future__ import annotations

from __future__ import annotations
import traceback
from pathlib import Path
from typing import TYPE_CHECKING
import numpy as np
from pycat.file_io.image_reader import open_image


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
        except Exception:  # broad-ok: batch replay best-effort probe → fallback; a per-step failure must not abort the whole batch
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
