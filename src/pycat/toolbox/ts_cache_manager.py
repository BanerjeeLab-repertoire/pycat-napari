"""
PyCAT Time-Series Zarr Cache Manager
======================================
Manages persistent zarr caches for time-series preprocessing.

Instead of random temp directories that are lost when PyCAT closes, caches
are stored in a deterministic location derived from the source file path and
processing parameters.  This means:

  - Re-opening the same .ims file with the same ball_radius/window_size
    immediately offers to reload the existing preprocessed layers rather
    than reprocessing 600 frames again.
  - Caches survive PyCAT restarts — the first session processes once and
    every subsequent session loads instantly.
  - Users can explicitly discard the cache (to force reprocessing if
    parameters change) via a "Discard Cache" button.

Cache layout
------------
Caches live in:  <source_file_dir>/.pycat_cache/<source_stem>/
Each preprocessing configuration gets its own subdirectory named by a
short hash of the parameters:

  .pycat_cache/
    post_1_0.5/
      preproc_br3_ws33/          ← preprocessed zarr store
      bgrem_br3_ws33/            ← bg-removed zarr store
      meta.json                  ← parameters + timestamps

This is co-located with the source file so caches travel with the data
(e.g. on a shared network drive or external disk) and are easy to find
and manage manually.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo

Date
----
    2025
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Cache path helpers
# ---------------------------------------------------------------------------

def _cache_root(source_file: str) -> Path:
    """Return the .pycat_cache directory next to the source file."""
    src = Path(source_file)
    return src.parent / ".pycat_cache" / src.stem


def _param_key(ball_radius: int, window_size: int) -> str:
    """Short deterministic string for a set of preprocessing parameters."""
    return f"br{ball_radius}_ws{window_size}"


def get_cache_paths(source_file: str, ball_radius: int,
                    window_size: int) -> dict[str, Path]:
    """
    Return the expected zarr paths for preprocessed and bg-removed stacks
    for a given source file and parameter set.

    Returns a dict with keys 'preproc', 'bgrem', 'meta', 'root'.
    The paths may or may not exist yet.
    """
    root   = _cache_root(source_file)
    key    = _param_key(ball_radius, window_size)
    return {
        'root':    root,
        'preproc': root / f"preproc_{key}",
        'bgrem':   root / f"bgrem_{key}",
        'meta':    root / "meta.json",
    }


def cache_exists(source_file: str, ball_radius: int,
                 window_size: int) -> dict[str, bool]:
    """
    Check which cache stores exist for the given parameters.

    Returns dict with keys 'preproc', 'bgrem' mapped to bool.
    """
    paths = get_cache_paths(source_file, ball_radius, window_size)
    return {
        'preproc': paths['preproc'].exists() and any(paths['preproc'].iterdir()),
        'bgrem':   paths['bgrem'].exists()   and any(paths['bgrem'].iterdir()),
    }


def write_meta(source_file: str, ball_radius: int, window_size: int,
               n_frames: int, H: int, W: int):
    """Write/update the cache metadata JSON."""
    paths = get_cache_paths(source_file, ball_radius, window_size)
    paths['root'].mkdir(parents=True, exist_ok=True)

    meta = {}
    if paths['meta'].exists():
        try:
            meta = json.loads(paths['meta'].read_text())
        except Exception:
            meta = {}

    key = _param_key(ball_radius, window_size)
    meta[key] = {
        'source_file':  str(source_file),
        'ball_radius':  ball_radius,
        'window_size':  window_size,
        'n_frames':     n_frames,
        'H':            H,
        'W':            W,
        'created':      datetime.now().isoformat(timespec='seconds'),
    }
    paths['meta'].write_text(json.dumps(meta, indent=2))


def discard_cache(source_file: str, ball_radius: Optional[int] = None,
                  window_size: Optional[int] = None):
    """
    Delete cached zarr stores.  If ball_radius/window_size are given,
    only that parameter set is discarded; otherwise the entire cache
    directory for this source file is removed.
    """
    root = _cache_root(source_file)
    if ball_radius is not None and window_size is not None:
        key = _param_key(ball_radius, window_size)
        for prefix in ('preproc', 'bgrem'):
            p = root / f"{prefix}_{key}"
            if p.exists():
                shutil.rmtree(str(p))
        # Remove the parameter entry from meta
        paths = get_cache_paths(source_file, ball_radius, window_size)
        if paths['meta'].exists():
            try:
                meta = json.loads(paths['meta'].read_text())
                meta.pop(key, None)
                paths['meta'].write_text(json.dumps(meta, indent=2))
            except Exception:  # broad-ok: write — best-effort cache-metadata prune during invalidation; the cache dir itself is already removed, so a stale meta entry is harmless
                pass
    else:
        if root.exists():
            shutil.rmtree(str(root))


def cache_size_mb(source_file: str) -> float:
    """Return total disk usage of the cache in MB."""
    root = _cache_root(source_file)
    if not root.exists():
        return 0.0
    total = sum(
        f.stat().st_size
        for f in root.rglob('*')
        if f.is_file()
    )
    return total / 1e6
