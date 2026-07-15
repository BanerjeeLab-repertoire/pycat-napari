"""PyCAT session save/load — the *whole working state* as ONE unit.

Motivation
----------
"Save & Clear" used to be a per-layer/per-dataframe EXPORT tool: it listed every
layer and every DataFrame as checkboxes and made the user curate what to keep.
That is the wrong model for "return to where I was":

  * The user should not have to know which artifacts constitute a session — PyCAT
    already knows (the derived layers + the analysis dataframes).
  * The SOURCE IMAGE should never be copied — it is already on disk (and it is the
    biggest file). A session only needs a *reference* to it.
  * Everything should land in ONE session folder, not scattered loose among the
    user's data files.
  * Loading a session should restore the source image AND the derived state,
    including VPT tracks — which the old suffix-only loader could not do.

Design
------
A session is a folder containing:

  * ``pycat_session.json`` — the MANIFEST. Records the source image path (a
    reference, not a copy), key acquisition state (pixel size, frame interval),
    and the mapping of each saved derived layer / dataframe to its file.
  * the derived layers (Labels/Image/Tracks) and analysis dataframes, written
    with the existing per-type writer.

Load reads the manifest, opens the source image from its recorded path, then
restores the derived layers and dataframes. The manifest is the source of truth;
the suffix-based scan remains only as a fallback for older folders.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd


MANIFEST_NAME = "pycat_session.json"
MANIFEST_VERSION = 1


# ---------------------------------------------------------------------------
# What belongs in a session (the smart default — no user curation needed)
# ---------------------------------------------------------------------------

def _is_source_image_layer(layer, source_stem):
    """The originally-loaded image — identified by name matching the source stem
    or by a provenance tag marking it as a loaded (not derived) layer. It is
    NEVER saved; the manifest references the file on disk instead."""
    try:
        if type(layer).__name__ != 'Image':
            return False
        nm = str(getattr(layer, 'name', '')).lower()
        if source_stem and source_stem.lower() in nm:
            # a loaded source layer keeps the file stem in its name; a derived
            # image (e.g. "Pre-Processed …") does not START with it plainly
            derived_markers = ('pre-processed', 'enhanced', 'background',
                               'upscaled', 'overlay', 'picked')
            if not any(m in nm for m in derived_markers):
                return True
        try:
            from pycat.utils.layer_tags import get_tags
            tags = get_tags(layer) or {}
            if str(tags.get('origin', '')).lower() in ('loaded', 'source', 'file'):
                return True
        except Exception:
            pass
    except Exception:
        pass
    return False


def _is_reconstructable(layer):
    """Pure interpolations of another layer (upscaled images) carry no new
    information and are excluded from the smart default."""
    try:
        if type(layer).__name__ == 'Labels':
            return False
        nm = str(getattr(layer, 'name', '')).lower()
        if 'upscal' in nm:
            return True
        try:
            from pycat.utils.layer_tags import get_tags
            op = str((get_tags(layer) or {}).get('operation', '')).lower()
            if 'upscal' in op:
                return True
        except Exception:
            pass
    except Exception:
        pass
    return False


def default_session_selection(layers, dataframe_names, source_stem):
    """The artifacts a session needs, WITHOUT user curation.

    Returns (layer_names, dataframe_names): every derived layer (masks, tracks,
    processed images) except the source image and pure-interpolation upscales,
    plus every analysis dataframe. This is the smart default; the caller may
    expand to the full ticklist on request.
    """
    keep_layers = []
    for l in layers:
        try:
            if _is_source_image_layer(l, source_stem):
                continue      # referenced, never copied
            if _is_reconstructable(l):
                continue      # pure interpolation
            keep_layers.append(l.name)
        except Exception:
            continue
    keep_dfs = list(dataframe_names)   # all analysis dataframes by default
    return keep_layers, keep_dfs


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def default_session_dir(parent_dir, source_stem):
    """A consolidated per-session subfolder next to the data."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe = (source_stem or "session").replace(os.sep, "_")
    return Path(parent_dir) / f"session_{safe}_{ts}"


def write_manifest(session_dir, source_path, data_repository,
                   layer_entries, dataframe_entries, extra=None):
    """Write pycat_session.json describing how to restore the session.

    layer_entries    : list of {name, layer_type, file, is_3d}
    dataframe_entries: list of {key, file}
    """
    session_dir = Path(session_dir)
    dr = data_repository or {}
    manifest = {
        'manifest_version': MANIFEST_VERSION,
        'created': time.strftime("%Y-%m-%dT%H:%M:%S"),
        # the source image is REFERENCED, not copied
        'source_image': {
            'path': str(source_path) if source_path else None,
            'exists': bool(source_path and os.path.exists(str(source_path))),
        },
        'acquisition': {
            'microns_per_pixel_sq': dr.get('microns_per_pixel_sq'),
            'pixel_size_from_metadata': dr.get('pixel_size_from_metadata'),
            'pixel_size_confirmed': dr.get('pixel_size_confirmed'),
            'frame_interval_s': (
                (dr.get('file_metadata') or {}).get('common', {}) or {}
            ).get('frame_interval_s'),
        },
        'layers': layer_entries,
        'dataframes': dataframe_entries,
    }
    if extra:
        manifest.update(extra)
    session_dir.mkdir(parents=True, exist_ok=True)
    with open(session_dir / MANIFEST_NAME, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    return session_dir / MANIFEST_NAME


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def read_manifest(folder):
    """Return the parsed manifest dict if folder contains one, else None."""
    p = Path(folder) / MANIFEST_NAME
    if not p.exists():
        return None
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def restore_dataframes_from_manifest(manifest, folder, data_repository):
    """Load each dataframe file recorded in the manifest back into the repo.
    Returns the dict of restored {key: DataFrame}."""
    folder = Path(folder)
    out = {}
    for entry in (manifest.get('dataframes') or []):
        key = entry.get('key')
        fname = entry.get('file')
        if not key or not fname:
            continue
        fpath = folder / fname
        if not fpath.exists():
            continue
        try:
            df = pd.read_csv(fpath)
            data_repository[key] = df
            out[key] = df
        except Exception:
            continue
    return out
