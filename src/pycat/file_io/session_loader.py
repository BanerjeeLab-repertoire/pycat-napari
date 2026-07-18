"""
PyCAT Session Loader
=====================
Scans an output folder for previously saved PyCAT analysis outputs and
reloads them into the napari viewer as the correct layer types with
meaningful names, restoring the working state of a previous session.

Supports both batch-replay outputs ({stem}_{suffix}.tiff/.csv) and
GUI Save & Clear outputs ({stem}_{layer_name}.tiff/.png/.csv).

File classification
-------------------
The suffix after the image stem determines the layer type and name:

Batch outputs
  *_preprocessed.tiff          → Image  "Pre-Processed {stem}"
  *_preprocessed_fluor.tiff    → Image  "Pre-Processed Fluorescence {stem}"
  *_bg_removed.tiff            → Image  "Enhanced Background Removed {stem}"
  *_bg_removed_fluor.tiff      → Image  "Enhanced Background Removed Fluorescence {stem}"
  *_upscaled.tiff              → Image  "Upscaled {stem}"
  *_cell_mask.tiff             → Labels "Cell Mask {stem}"
  *_labeled_cells.tiff         → Labels "Labeled Cell Mask {stem}"
  *_total_puncta_mask.tiff     → Labels "Puncta Mask {stem}"
  *_total_refined_puncta_mask.tiff → Labels "Refined Puncta Mask {stem}"
  *_cell_labeled_puncta.tiff   → Labels "Cell-Labeled Puncta {stem}"
  *_ts_cell_mask.tiff          → Labels "TS Cell Mask {stem}"
  *_cell_df.csv                → DataFrame (stored in data_repository)
  *_puncta_df.csv              → DataFrame (stored in data_repository)
  *_sacf_results.csv           → DataFrame (stored in data_repository)

GUI Save & Clear outputs (layer name embedded in filename)
  *_labeled_cell_mask.png      → Labels
  *_cellpose_segmentation_*.png → Labels
  *_refined_puncta_mask.png    → Labels
  *_*_stack.tiff               → Image (3D)
  all other *.tiff             → Image (2D)
  all other *.png              → Labels (default for PNG)

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2025
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------

# (suffix_pattern, layer_type, display_name_template)
# layer_type: 'image', 'labels', 'dataframe'
# display_name_template: {stem} = source file stem, {suffix} = matched suffix
_BATCH_RULES: list[tuple[str, str, str]] = [
    # Images (order matters — more specific first)
    ('_preprocessed_fluor',          'image',  'Pre-Processed Fluorescence'),
    ('_preprocessed',                'image',  'Pre-Processed'),
    ('_bg_removed_fluor',            'image',  'Enhanced BG Removed Fluorescence'),
    ('_bg_removed',                  'image',  'Enhanced BG Removed'),
    ('_upscaled',                    'image',  'Upscaled'),
    # Labels
    ('_total_refined_puncta_mask',   'labels', 'Refined Puncta Mask'),
    ('_total_puncta_mask',           'labels', 'Puncta Mask'),
    ('_cell_labeled_puncta',         'labels', 'Cell-Labeled Puncta'),
    ('_labeled_cells',               'labels', 'Labeled Cell Mask'),
    ('_cell_mask',                   'labels', 'Cell Mask'),
    ('_ts_cell_mask',                'labels', 'TS Cell Mask'),
    # DataFrames
    ('_puncta_df',                   'dataframe', 'puncta_df'),
    ('_cell_df',                     'dataframe', 'cell_df'),
    ('_sacf_results',                'dataframe', 'sacf_results_df'),
    ('_timeseries_condensate_df',    'dataframe', 'timeseries_condensate_df'),
    # VPT (video particle tracking) dataframes. vpt_tracks is the source of truth
    # for a VPT session — when it loads, the caller rebuilds the trajectory layers
    # (see _open_session_loader). List the more specific suffixes first so e.g.
    # `_vpt_aggregate_tracks` is not shadowed by `_vpt_tracks`.
    ('_vpt_aggregate_tracks',        'dataframe', 'vpt_aggregate_tracks'),
    ('_vpt_aggregate_stats',         'dataframe', 'vpt_aggregate_stats'),
    ('_vpt_moduli_df',               'dataframe', 'vpt_moduli_df'),
    ('_vpt_msd_df',                  'dataframe', 'vpt_msd_df'),
    ('_vpt_detections',              'dataframe', 'vpt_detections'),
    ('_vpt_tracks',                  'dataframe', 'vpt_tracks'),
]

# Patterns for GUI Save & Clear outputs
# (regex on safe_layer_name part, layer_type)
_GUI_LABEL_PATTERNS = [
    r'labeled.cell.mask',
    r'cellpose.segmentation',
    r'refined.puncta.mask',
    r'puncta.mask',
    r'cell.labeled.puncta',
    r'ts.cell.mask',
    r'labeled.cells',
    r'cell.mask',
    r'stardist.segmentation',
    r'timeseries.condensate.masks',
    r'masks$',           # anything ending in _masks
]


def classify_file(path: Path) -> Optional[dict]:
    """
    Classify a single file as a PyCAT output.

    Returns a dict with keys:
        stem        : source image stem (everything before the suffix)
        layer_type  : 'image' | 'labels' | 'dataframe'
        display_name: suggested napari layer name
        path        : Path object
        is_3d       : bool (True for *_stack.tiff and *_masks.tiff)
        source      : 'batch' | 'gui'

    Returns None if the file is not recognised as a PyCAT output.
    """
    name  = path.stem    # filename without extension
    ext   = path.suffix.lower()

    if ext not in ('.tiff', '.tif', '.png', '.csv'):
        return None

    # ── Batch outputs ────────────────────────────────────────────────────
    for suffix, ltype, display in _BATCH_RULES:
        if name.endswith(suffix):
            stem = name[:-len(suffix)]
            entry = dict(
                stem=stem,
                layer_type=ltype,
                display_name=f"{display} [{stem}]",
                path=path,
                is_3d=False,
                source='batch',
            )
            # For dataframes, the rule's `display` IS the repository key (e.g.
            # 'vpt_tracks') — carry it as df_key so the loader stores it under the
            # right key (and the VPT rebuild hook, which looks for 'vpt_tracks',
            # fires for loose files too).
            if ltype == 'dataframe':
                entry['df_key'] = display
            return entry

    # ── GUI Save & Clear outputs ─────────────────────────────────────────
    if ext in ('.tiff', '.tif'):
        is_3d = name.endswith('_stack') or name.endswith('_masks')
        # Try to find the layer name part after the last underscore group
        # GUI format: {base_file_name}_{safe_layer_name}[_stack|_masks]
        safe_name = name
        if is_3d:
            safe_name = re.sub(r'_(stack|masks)$', '', safe_name)

        # Check if this looks like a labels layer by name pattern
        ltype = 'image'
        for pattern in _GUI_LABEL_PATTERNS:
            if re.search(pattern, safe_name, re.IGNORECASE):
                ltype = 'labels'
                break

        # Derive display name: convert underscores back to spaces, title-case
        display = safe_name.replace('_', ' ').title()
        return dict(
            stem=safe_name,
            layer_type=ltype,
            display_name=display + (' (3D)' if is_3d else ''),
            path=path,
            is_3d=is_3d,
            source='gui',
        )

    if ext == '.png':
        ltype = 'labels'
        for pattern in _GUI_LABEL_PATTERNS:
            if re.search(pattern, name, re.IGNORECASE):
                ltype = 'labels'
                break
        display = name.replace('_', ' ').title()
        return dict(
            stem=name, layer_type=ltype, display_name=display,
            path=path, is_3d=False, source='gui',
        )

    if ext == '.csv':
        # Try to infer DataFrame type from name
        df_key = 'analysis_df'
        for suffix, _, key in _BATCH_RULES:
            if suffix.startswith('_') and name.endswith(suffix[1:]):
                df_key = key
                break
        display = name.replace('_', ' ').title()
        return dict(
            stem=name, layer_type='dataframe', display_name=display,
            path=path, is_3d=False, source='gui', df_key=df_key,
        )

    return None


def scan_output_folder(folder: Path) -> dict[str, list[dict]]:
    """
    Scan a folder for PyCAT outputs and group them by source image stem.

    Returns
    -------
    dict mapping stem → list of classified file dicts, sorted by layer_type
    (images first, then labels, then dataframes) so napari layers are added
    in a logical order.
    """
    groups: dict[str, list[dict]] = {}
    for path in sorted(folder.iterdir()):
        if path.is_dir():
            continue
        info = classify_file(path)
        if info is None:
            continue
        stem = info['stem']
        if stem not in groups:
            groups[stem] = []
        groups[stem].append(info)

    # Sort within each group: images → labels → dataframes
    type_order = {'image': 0, 'labels': 1, 'dataframe': 2}
    for stem in groups:
        groups[stem].sort(key=lambda x: type_order.get(x['layer_type'], 3))

    return groups


def _os_exists(p):
    try:
        import os
        return bool(p) and os.path.exists(str(p))
    except Exception:
        return False


def _load_source_image_into_viewer(src_path, viewer, data_instance, file_io=None):
    """Load the session's source image through PyCAT's OWN (LAZY) loader.

    ── Why this must be lazy ─────────────────────────────────────────────────

    A session's source is often a long time-series — the one reported was
    (1000, 1080, 1440) float32 = **5.79 GiB**. Reading it whole with
    ``tifffile.imread`` (the old fallback) raises ``MemoryError`` and the session
    loads **zero layers**. PyCAT already opens such stacks lazily via
    ``open_image_auto`` (frame-by-frame ``_TiffPageStack``); the trouble was that
    this function looked for ``file_io`` on ``data_instance.central_manager``,
    which the loaded ``BaseDataClass`` does not carry — so it silently fell to the
    eager read and OOM'd. ``file_io`` is now passed in explicitly by the caller.

    Fallbacks are lazy too: a memory-mapped read (no full allocation) before, only
    as a last resort, the eager read that could exhaust RAM.
    """
    # 1) The real path: PyCAT's own lazy opener, with scale + metadata.
    if file_io is None:
        cm = getattr(data_instance, 'central_manager', None)
        file_io = getattr(cm, 'file_io', None) if cm is not None else None
    try:
        if file_io is not None and hasattr(file_io, 'open_image_auto'):
            file_io.open_image_auto(file_path=src_path, clear_first=False)
            return True
    except Exception as e:
        print(f"[PyCAT Session] source image via file_io failed, falling back: {e}")

    import os
    import tifffile
    # 2) Memory-mapped: napari reads frames on demand; no 5.79 GiB allocation.
    try:
        arr = tifffile.memmap(str(src_path))
        viewer.add_image(arr, name=os.path.basename(str(src_path)))
        return True
    except Exception as e:
        print(f"[PyCAT Session] memmap fallback failed ({e}); trying an eager read")
    # 3) Last resort — may OOM on a large stack, but it is the honest final attempt.
    try:
        arr = tifffile.imread(str(src_path))
        viewer.add_image(arr, name=os.path.basename(str(src_path)))
        return True
    except Exception as e:
        print(f"[PyCAT Session] source image could not be loaded: {e}")
        return False


def load_session(
    folder: Path,
    viewer,
    data_instance,
    stem_filter: Optional[str] = None,
    progress_callback=None,
    stems=None,
    central_manager=None,
) -> dict:
    """
    Load all recognised PyCAT outputs from folder into the napari viewer.

    Parameters
    ----------
    folder : Path
        Output directory to scan.
    viewer : napari.Viewer
        Target viewer.
    data_instance : BaseDataClass
        Active data instance for storing DataFrames.
    stem_filter : str or None
        If given, only load files whose stem contains this string.
        Useful for loading a single image's outputs from a batch folder.
    progress_callback : callable(done, total) or None

    Returns
    -------
    dict with keys:
        loaded_layers  : list of layer names added
        loaded_dfs     : dict of {df_key: DataFrame}
        skipped        : list of (path, reason) for files that failed
    """
    import tifffile
    import skimage as sk

    loaded_layers = []
    loaded_dfs    = {}
    skipped       = []

    # ── Manifest-first: a session folder carries pycat_session.json describing
    # how to restore the whole working state — including the SOURCE IMAGE (which
    # is referenced by path, not copied) and VPT tracks (which the suffix scan
    # alone cannot rebuild). If a manifest is present, use it; the suffix scan
    # then still runs to pick up any derived layer files in the folder.
    _manifest = None
    try:
        from pycat.file_io import session_manifest as _sm
        _manifest = _sm.read_manifest(folder)
    except Exception:
        _manifest = None

    if _manifest is not None:
        # 1) Load the source image from its recorded path (a reference, not a copy).
        try:
            src = (_manifest.get('source_image') or {}).get('path')
            if src and _os_exists(src):
                # Prefer the caller's file_io (the loaded BaseDataClass has no central_manager, so
                # the lazy opener would otherwise be unreachable and the source would OOM).
                _fio = getattr(central_manager, 'file_io', None)
                _load_source_image_into_viewer(src, viewer, data_instance, file_io=_fio)
                loaded_layers.append(f"[source] {src}")
                print(f"[PyCAT Session] Loaded source image: {src}")
            elif src:
                skipped.append((src, "source image not found at recorded path"))
                print(f"[PyCAT Session] Source image missing: {src}")
        except Exception as e:
            skipped.append((str((_manifest.get('source_image') or {}).get('path')), str(e)))

        # 2) Restore acquisition state (pixel size, frame interval) so downstream
        #    analysis is calibrated exactly as it was.
        try:
            if data_instance is not None:
                acq = _manifest.get('acquisition') or {}
                dr = data_instance.data_repository
                if acq.get('microns_per_pixel_sq') is not None:
                    dr['microns_per_pixel_sq'] = acq['microns_per_pixel_sq']
                    dr['pixel_size_from_metadata'] = acq.get('pixel_size_from_metadata', False)
                    dr['pixel_size_confirmed'] = acq.get('pixel_size_confirmed', True)
        except Exception:
            pass

        # 3) Restore dataframes recorded in the manifest (incl. vpt_tracks).
        try:
            from pycat.file_io import session_manifest as _sm
            if data_instance is not None:
                restored = _sm.restore_dataframes_from_manifest(
                    _manifest, folder, data_instance.data_repository)
                loaded_dfs.update(restored)
                for k in restored:
                    print(f"[PyCAT Session] Restored dataframe '{k}'")
        except Exception as e:
            skipped.append((str(folder), f"manifest dataframes: {e}"))

    groups = scan_output_folder(folder)

    # ── The user's selection is honoured ──────────────────────────────────────────────────
    #
    # It was not. The dialog computed the selected stems, used them to size the progress bar, and
    # then called `load_session(folder, ...)` with **no filter at all** — so selecting two images out
    # of eight loaded all eight. The progress bar carried the tell: its maximum was the SELECTED
    # count while the load reported over the WHOLE folder.
    #
    # `stem_filter` was a single SUBSTRING, which cannot express "these three of eight" even if the
    # dialog had passed it. `stems` is the set the dialog actually has.
    if stems is not None:
        wanted = {str(s) for s in stems}
        groups = {s: v for s, v in groups.items() if s in wanted}
    elif stem_filter:
        groups = {s: v for s, v in groups.items()
                  if stem_filter.lower() in s.lower()}

    # Flatten to ordered list for progress tracking
    all_files = [info for files in groups.values() for info in files]
    n_total   = len(all_files)

    for i, info in enumerate(all_files):
        path  = info['path']
        ltype = info['layer_type']
        name  = info['display_name']
        is_3d = info.get('is_3d', False)

        try:
            if ltype == 'dataframe':
                df = pd.read_csv(str(path))
                df_key = info.get('df_key', path.stem)
                loaded_dfs[df_key] = df
                if data_instance is not None:
                    data_instance.data_repository[df_key] = df
                print(f"[PyCAT Session]   Loaded DataFrame '{df_key}': "
                      f"{len(df)} rows × {len(df.columns)} cols")

            elif ltype == 'image':
                arr = tifffile.imread(str(path)).astype(np.float32)
                # Normalise to [0, 1] for display
                mn, mx = arr.min(), arr.max()
                if mx > mn:
                    arr = (arr - mn) / (mx - mn)
                viewer.add_image(arr, name=name, colormap='viridis')
                loaded_layers.append(name)
                print(f"[PyCAT Session]   Loaded Image '{name}': {arr.shape}")

            elif ltype == 'labels':
                ext = path.suffix.lower()
                if ext == '.png':
                    arr = sk.io.imread(str(path))
                    if arr.ndim == 3:
                        # RGB/RGBA mask — take first channel or convert to int
                        arr = arr[..., 0]
                    arr = arr.astype(np.int32)
                else:
                    arr = tifffile.imread(str(path)).astype(np.int32)
                viewer.add_labels(arr, name=name)
                loaded_layers.append(name)
                print(f"[PyCAT Session]   Loaded Labels '{name}': {arr.shape}")

        except Exception as e:
            skipped.append((path, str(e)))
            print(f"[PyCAT Session]   Skipped {path.name}: {e}")

        if progress_callback:
            progress_callback(i + 1, n_total)

    # The analysis method the session was in (if the manifest recorded it), so the caller can reopen
    # it and rebuild its view. Absent for sessions saved before this was recorded — the caller infers
    # it from the restored data instead.
    active_method = (_manifest or {}).get('active_method')

    return dict(
        loaded_layers=loaded_layers,
        loaded_dfs=loaded_dfs,
        skipped=skipped,
        active_method=active_method,
    )
