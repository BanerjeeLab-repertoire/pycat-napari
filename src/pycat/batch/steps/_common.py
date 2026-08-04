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

    Returns the plane as a dtype-max-normalised [0, 1] float32 — the SAME
    convention the interactive 2-D loader uses (``to_unit_float32`` /
    ``dtype_conversion_func(data, 'float32')``, see ``stack_access.py``:
    *"[0, 1] is the contract"*, declared by 17 toolbox functions). Previously
    this returned RAW counts (0-65535 for a uint16 source) while the GUI's
    layer held the same pixels divided by 65535 -- same file, same pixels, a
    factor of 65535 apart. Every self-normalising consumer (SNR, GLCM texture,
    ratios) was immune and never showed it, but ``cell_analysis_func`` /
    ``puncta_analysis_func`` report intensity directly with no internal
    normalisation, so batch's cell_df/puncta_df intensity columns came out
    ~65535x the GUI's for the exact same image. Reported by Meet Raval, with
    matching CSVs from both paths (ratio measured at 65,344-65,924x across
    intensity_mean/intensity_total on identical cells/puncta).

    Normalising HERE, once, at the single chokepoint every batch step's image
    data flows through, is what makes this fix hold everywhere rather than
    needing a scale-aware patch at every consumer (the previous, narrower
    attempt at exactly that — matching one function's absolute-threshold logic
    onto raw-counts data — corrupted the image instead, see replay_upscaling's
    history). `_raw_counts`/`_normalize_to_float` (this module) are unaffected
    in spirit: a dtype-max division is a FIXED, frame-independent, purely
    multiplicative rescale (unlike `_normalize_to_float`'s per-frame min-max,
    which shifts the pedestal and IS what the raw-counts fixes were protecting
    against) -- so it does not reintroduce the exposure-dependent partition/
    intensity-ratio bugs those fixes exist for.

    SIGNED integer sources (some camera/acquisition software writes non-negative
    counts into a SIGNED 16-bit TIFF) are cast to uint16 before normalising --
    mirroring `load_into_viewer`'s identical correction for the interactive
    path (`if signedinteger: data = data.astype(np.uint16)`). Without it,
    `to_unit_float32` divides by the SIGNED range's max (32767) instead of
    65535 -- a clean, exact 2x -- for exactly this reason: a signed-vs-unsigned
    dtype disagreement between readers (tifffile reports one TIFF's dtype as
    int16; PIL, the GUI's own NumPy-2.0 fallback reader, can report the SAME
    file differently) is a real, confirmed way for the two paths to disagree
    without either being "wrong" about the file's nominal dtype tag.

    Parameters
    ----------
    image_path : Path
    channel : int
        Which channel to load (C index). Defaults to 0. Use _load_all_channels
        when the recorded channel_assignment needs to select a specific
        non-zero channel for a given image type (e.g. Segmentation vs
        Fluorescence Image came from different channels in the GUI session).
    """
    from pycat.file_io.stack_access import to_unit_float32

    def _to_unit_float32_gui_convention(arr):
        arr = np.asarray(arr)
        if np.issubdtype(arr.dtype, np.signedinteger):
            arr = arr.astype(np.uint16)
        return to_unit_float32(arr, arr.dtype)

    microns_per_pixel = 1.0

    try:
        img = open_image(str(image_path))
        # `get_image_data` LOADS THE WHOLE SCENE (documented, both libraries). `read_plane`
        # pulls exactly one YX plane through the lazy API — which matters in batch, where this
        # runs once per file per step.
        from pycat.file_io.image_reader import read_plane
        data = read_plane(img, path=str(image_path), scene=0, t=0, c=channel)
        data = _to_unit_float32_gui_convention(data)
        try:
            px_size = img.physical_pixel_sizes
            microns_per_pixel = float(px_size.Y) if px_size.Y else 1.0
        except Exception:  # broad-ok: optional_probe — pixel-size metadata probe → fallback (1.0 micron/px)
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

    return _to_unit_float32_gui_convention(data), microns_per_pixel


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


def _active_layer_channel_role(state: dict, active_name: str):
    """**Was the recorded active layer the primary (segmentation) channel, or a
    secondary (fluorescence / companion) one — and if secondary, which
    ``channels_by_name`` key is it?**

    ``preprocessing``/``background_removal`` used to decide this with a single
    keyword test — ``'fluorescence' in active_name`` — which only works when the
    GUI's generic placeholder names ("Segmentation Image"/"Fluorescence Image")
    were actually used. A split-file recording (two separate ``open_image``
    steps, e.g. an ``*_DAPI.tif`` + ``*_GFP.tif`` pair) or a channel-assignment
    dialog with the sample's own identity as the layer name (PyCAT's
    ``derive_layer_name``, e.g. "In_Cell"/"In_Cell [1]") never contains that
    keyword, so the check silently returned ``False`` for every step that
    genuinely ran on the companion channel — and preprocessing/background
    removal ran on the DAPI/segmentation image every time, no matter what the
    GUI recorded.

    The second, more general signal: the recorded name matches (by substring,
    the same test ``_resolve_image_layer`` already uses for named channels) a
    ``channels_by_name`` entry that is NOT the primary segmentation channel.
    That covers exactly the split-file / sample-identity-named case, without
    touching the keyword path multi-channel configs already rely on.

    "Is this the primary channel" is checked by NAME first
    (``state['_primary_channel_name']``, set once at open_image time by both
    the split-file and channel-assignment loaders) rather than array identity,
    because ``replay_upscaling`` only re-upscales a ``channels_by_name`` entry
    that is NOT (at upscale time) identical to the pre-upscale
    ``state['image']`` — so a channel-assignment recording's own segmentation-
    channel entry is deliberately left pointing at the stale pre-upscale array
    forever after, and an identity check against the CURRENT ``state['image']``
    would then wrongly call it "not primary" once upscaling has run. The
    per-entry identity check in the loop below is a second, redundant guard for
    the split-file case, where the primary is never added to
    ``channels_by_name`` at all.

    A NAIVE substring test is not enough, though: napari disambiguates a
    duplicate layer NAME with a ``" [N]"`` suffix ("In_Cell" and "In_Cell
    [1]"), so the primary's own recorded name is itself a substring of the
    companion's ("in_cell" is inside "upscaled in_cell [1]") — the companion
    would otherwise be misrouted to the primary. The LONGEST matching known
    name wins, so a companion's longer, more specific name always beats the
    shorter primary name it happens to share a prefix with.

    Returns ``(is_fluor, channel_key)`` — ``channel_key`` is ``None`` unless the
    match came from a named channel (so the caller can write processed output
    to ``channels_processed_by_name[channel_key]``, keeping later lookups of
    that recorded name in sync — mirroring what ``replay_upscaling`` already
    does for the raw channel on upscale).
    """
    if 'fluorescence' in active_name:
        return True, None
    primary = state.get('image')
    primary_name = str(state.get('_primary_channel_name') or '').lower()
    best_len, best_key = -1, None
    if primary_name and primary_name in active_name:
        best_len = len(primary_name)                  # best_key stays None: "it's the primary"
    for key, arr in (state.get('channels_by_name') or {}).items():
        if arr is primary:
            continue                      # this name IS the segmentation channel
        lk = key.lower()
        if lk and lk in active_name and len(lk) > best_len:
            best_len, best_key = len(lk), key
    return (best_key is not None), best_key


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
    ``"Upscaled Fluorescence Image"`` or a sample-identity name from a
    split-file recording, ``"Enhanced Background Removed Pre-Processed
    Upscaled In_Cell [1]"``). Replay must honour that recorded name instead of
    assuming a fixed channel/stage, otherwise a step can silently run on the
    wrong channel (e.g. Cellpose running on the wrong channel, or getting the
    ENHANCED image where the recording asked for the RAW one).

    Resolution uses two independent facts encoded in the layer name:

      1. WHICH CHANNEL  — the primary/segmentation channel (the "Segmentation"
         keyword, OR ``state['_primary_channel_name']`` — the recorded name a
         split-file/sample-identity recording actually uses, e.g. "In_Cell");
         "Fluorescence"; or a named extra channel from
         ``state['channels_by_name']`` (split-file companions, 3+ fluorophore
         files).
      2. WHICH STAGE    — raw (upscaled) vs preprocessed / background-removed.
         The primary/fluorescence roles each have a dedicated raw + processed
         pair of state slots (``image``/``preprocessed``,
         ``fluorescence_image``/``preprocessed_fluorescence``). A named extra
         channel gets the SAME two-slot treatment via
         ``channels_by_name`` (raw) / ``channels_processed_by_name``
         (preprocessed, then overwritten by background-removed) — a single
         shared slot per name cannot tell "raw" and "processed" apart, and a
         step recorded BEFORE preprocessing (e.g. cell_analysis reading the
         raw upscaled channel) would otherwise silently receive whatever a
         LATER-run preprocessing step left behind.

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
    if 'segmentation' in name:
        if is_processed:
            return state.get('preprocessed', state['image'])
        return state['image']

    if 'fluorescence' in name:
        if is_processed:
            return state.get('preprocessed_fluorescence',
                             state.get('fluorescence_image', state['image']))
        return state.get('fluorescence_image', state['image'])

    # --- name-based resolution (split-file / sample-identity-named configs) ---
    #
    # Napari disambiguates a duplicate layer NAME with a " [N]" suffix ("In_Cell" and
    # "In_Cell [1]"), so the primary's own recorded name can be a SUBSTRING of a
    # companion's ("in_cell" is inside "upscaled in_cell [1]") — a plain substring
    # test would misroute the companion to the primary. The LONGEST matching known
    # name wins: a companion's longer, more specific name always beats the shorter
    # primary name it happens to share a prefix with.
    primary_name = str(state.get('_primary_channel_name') or '').lower()
    channels_raw = state.get('channels_by_name') or {}
    best_len, best_key = -1, None                 # best_key stays None while the primary leads
    if primary_name and (primary_name in name or name in primary_name):
        best_len = len(primary_name)
    for key in channels_raw:
        lk = key.lower()
        if lk and (lk in name or name in lk) and len(lk) > best_len:
            best_len, best_key = len(lk), key

    if best_len >= 0 and best_key is None:
        if is_processed:
            return state.get('preprocessed', state['image'])
        return state['image']

    if best_key is not None:
        if is_processed:
            proc = (state.get('channels_processed_by_name') or {}).get(best_key)
            if proc is not None:
                return proc
        return channels_raw.get(best_key, fallback)

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


# The batch analysis steps that produce a per-object measurement table, and the (entity_type, state / data-
# instance key) of that table — so each such step's outcome can carry a typed AnalysisResult (typed-result-
# models), not just a status. Keyed by the _STEP_MAP step name.
_ANALYSIS_STEP_TABLES = {
    'cell_analysis':          ('cell', 'cell_df'),
    'ivf_droplet_analysis':   ('condensate', 'ivf_droplet_df'),
    'bf_condensate_analysis': ('condensate', 'bf_condensate_df'),
}


def analysis_results_for_step(step_name, state):
    """The typed ``AnalysisResult`` output(s) of a batch analysis ``step_name``, or ``()``.

    When a step produced a known per-object measurement table, wrap it in an ``AnalysisResult`` — validated
    identity (a non-empty operation_id / entity_type, a real DataFrame) — so the step's outcome carries the
    STRUCTURED result, not just a status. Additive: it reads the table the step already wrote (from the
    ``data_instance`` or ``state``), changes nothing, and never raises into the run."""
    spec = _ANALYSIS_STEP_TABLES.get(step_name)
    if spec is None:
        return ()
    entity_type, key = spec
    di = state.get('data_instance')
    table = di.get_data(key) if di is not None and hasattr(di, 'get_data') else None
    if table is None:
        table = state.get(key)
    if table is None or not hasattr(table, 'columns'):
        return ()
    try:
        from pycat.utils.result_models import AnalysisResult
        return (AnalysisResult(operation_id=step_name, entity_type=entity_type, measurements=table),)
    except Exception:  # broad-ok: optional_probe -- AnalysisResult is result metadata; a bad table must not fail the step
        return ()
