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
            return dict(
                stem=stem,
                layer_type=ltype,
                display_name=f"{display} [{stem}]",
                path=path,
                is_3d=False,
                source='batch',
            )

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


def load_session(
    folder: Path,
    viewer,
    data_instance,
    stem_filter: Optional[str] = None,
    progress_callback=None,
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

    groups = scan_output_folder(folder)

    if stem_filter:
        groups = {s: v for s, v in groups.items()
                  if stem_filter.lower() in s.lower()}

    # Flatten to ordered list for progress tracking
    all_files = [info for files in groups.values() for info in files]
    n_total   = len(all_files)

    loaded_layers = []
    loaded_dfs    = {}
    skipped       = []

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

    return dict(
        loaded_layers=loaded_layers,
        loaded_dfs=loaded_dfs,
        skipped=skipped,
    )
