"""
PyCAT Multi-Dimensional Acquisition I/O
==========================================
Handles complex microscopy acquisitions with combinations of:
  - Multiple channels (laser lines), up to ~5 typical
  - Time series (T)
  - Z-stacks (Z), potentially nested within time series
  - Multiple XY stage positions / fields of view (P)

Format-specific dimension conventions
---------------------------------------
IMS (Imaris HDF5)
    A single .ims file stores exactly one (T, C, Z, Y, X) dataset — Imaris
    has no native multi-position dimension. Multi-position acquisitions
    ("File Series") are saved by the acquisition/conversion software as
    SEPARATE .ims files, one per position, typically sharing a common
    filename stem and differing only by a position index/suffix (e.g.
    "expt_Position1.ims", "expt_Position2.ims" or "expt_p01.ims", "..p02.ims").
    This module detects such sibling files by filename pattern so they can
    be browsed/opened together as one logical multi-position acquisition.

    Within a single .ims file, T and Z can BOTH be >1 (nested time series
    with a z-stack per position/timepoint) — this must be preserved as a
    genuine lazy 4D (T, Z, Y, X) per-channel array, not collapsed to one
    or the other.

OME-TIFF / CZI / other formats read via AICSImage
    Multi-position is natively supported within a single file as separate
    "scenes" (OME-XML SizeS / Bio-Formats series concept). AICSImage
    exposes this directly via `.scenes` (list of scene ids) and
    `.set_scene(id)`. T, C, Z are read from `.dims` and can all be >1
    simultaneously — again must be preserved as a genuine 4D per-channel
    array rather than forcing a T-xor-Z choice.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2026
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Lazy N-D array wrappers
# ---------------------------------------------------------------------------

class _ZarrTZYX:
    """
    Lazy (T, Z, Y, X) view over an IMS zarr array's full (T, C, Z, Y, X)
    dataset, fixed to one channel. Presents a genuine 4D array to napari
    (which natively adds both a T and a Z slider for 4D layers), so nested
    time-series-with-z-stack acquisitions are browsable without collapsing
    either dimension or forcing an upfront single-timepoint choice.

    Frames are read on demand — only the (Y, X) plane(s) actually requested
    are pulled from the HDF5-backed zarr store.
    """
    def __init__(self, z, c, suppress_ctx=None):
        self._z   = z
        self._c   = c
        self._ctx = suppress_ctx or (lambda: _NullCtx())
        T, _, Z, Y, X = z.shape
        self.shape = (T, Z, Y, X)
        self.dtype = np.dtype('float32')
        self.ndim  = 4

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            t_idx = idx[0] if len(idx) > 0 else slice(None)
            z_idx = idx[1] if len(idx) > 1 else slice(None)
            spatial = idx[2:]
        else:
            t_idx, z_idx, spatial = idx, slice(None), ()
        with self._ctx():
            raw = self._z[t_idx, self._c, z_idx]
        arr = np.asarray(raw).astype(np.float32)
        if spatial:
            arr = arr[(Ellipsis,) + spatial] if arr.ndim > 2 else arr[spatial]
        return arr

    def __array__(self, dtype=None):
        with self._ctx():
            arr = np.stack([
                np.stack([np.asarray(self._z[t, self._c, z]).astype(np.float32)
                          for z in range(self.shape[1])], axis=0)
                for t in range(self.shape[0])
            ], axis=0)
        return arr if dtype is None else arr.astype(dtype)

    def __len__(self):
        return self.shape[0]


class _ZarrZYX:
    """
    Lazy (Z, Y, X) view for a pure z-stack (no time dimension, or a single
    fixed timepoint) from an IMS zarr array, one channel.
    """
    def __init__(self, z, c, t=0, suppress_ctx=None):
        self._z   = z
        self._c   = c
        self._t   = t
        self._ctx = suppress_ctx or (lambda: _NullCtx())
        _, _, Z, Y, X = z.shape
        self.shape = (Z, Y, X)
        self.dtype = np.dtype('float32')
        self.ndim  = 3

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            z_idx, spatial = idx[0], idx[1:]
        else:
            z_idx, spatial = idx, ()
        with self._ctx():
            raw = self._z[self._t, self._c, z_idx]
        arr = np.asarray(raw).astype(np.float32)
        if spatial:
            arr = arr[spatial]
        return arr

    def __array__(self, dtype=None):
        with self._ctx():
            arr = np.stack(
                [np.asarray(self._z[self._t, self._c, z]).astype(np.float32)
                 for z in range(self.shape[0])], axis=0)
        return arr if dtype is None else arr.astype(dtype)

    def __len__(self):
        return self.shape[0]


class _ZarrTZYX_generic:
    """
    Lazy (T, Z, Y, X) view over a plain zarr DirectoryStore array (used for
    TIFF/CZI/other AICSImage-backed formats). Companion to _ZarrTZYX (which
    is IMS-specific and indexes a fixed channel out of a 5D HDF5 zarr
    array) — this one wraps an already-per-channel 4D zarr array written
    directly with shape (T, Z, Y, X).
    """
    def __init__(self, z):
        self._z    = z
        self.shape = z.shape
        self.dtype = np.dtype('float32')
        self.ndim  = 4

    def __getitem__(self, idx):
        arr = np.asarray(self._z[idx]).astype(np.float32)
        return arr

    def __array__(self, dtype=None):
        arr = np.asarray(self._z).astype(np.float32)
        return arr if dtype is None else arr.astype(dtype)

    def __len__(self):
        return self.shape[0]


class _NullCtx:
    def __enter__(self): return self
    def __exit__(self, *a): return False


# ---------------------------------------------------------------------------
# IMS multi-position (sibling file) detection
# ---------------------------------------------------------------------------

# Common position/field-of-view naming patterns used by acquisition and
# conversion software (Imaris File Converter, µManager, NIS-Elements, ZEN).
_POSITION_PATTERNS = [
    re.compile(r'(.+?)[_\-]?Position[_\-]?(\d+)(\.ims)$', re.IGNORECASE),
    re.compile(r'(.+?)[_\-]?[Pp](\d+)(\.ims)$'),
    re.compile(r'(.+?)[_\-]?XY(\d+)(\.ims)$', re.IGNORECASE),
    re.compile(r'(.+?)[_\-]?FOV[_\-]?(\d+)(\.ims)$', re.IGNORECASE),
    re.compile(r'(.+?)[_\-]?[Ss](\d+)(\.ims)$'),
    re.compile(r'(.+?)_(\d{2,4})(\.ims)$'),   # trailing numeric index
]


def find_sibling_position_files(file_path: str) -> list[dict]:
    """
    Scan the directory containing `file_path` for other .ims files that
    appear to be sibling positions of the same multi-position acquisition
    — i.e. files sharing the same stem with only a position index differing.

    Parameters
    ----------
    file_path : str
        Path to the .ims file the user opened.

    Returns
    -------
    List of dicts, one per detected position (including the opened file
    itself), sorted by position index:
        {'path': str, 'position_index': int, 'filename': str,
         'is_opened_file': bool}
    `is_opened_file` marks the entry matching `file_path` exactly — this
    is the one that should default to checked in any selection dialog,
    regardless of where it falls after sorting by position index. Without
    this flag, a user opening e.g. Position_3.ims out of [1,2,3,4] would
    see the dialog default-check Position_1 instead of the file they
    actually asked to open.
    Empty list if no pattern match or no siblings found (single-position file).
    """
    path = Path(file_path)
    directory = path.parent
    filename  = path.name
    resolved_opened = str(path.resolve())

    matched_pattern = None
    stem = None
    for pattern in _POSITION_PATTERNS:
        m = pattern.match(filename)
        if m:
            matched_pattern = pattern
            stem = m.group(1)
            break

    if matched_pattern is None:
        return []

    siblings = []
    try:
        candidates = list(directory.glob('*.ims'))
    except Exception:
        return []

    for candidate in candidates:
        m = matched_pattern.match(candidate.name)
        if m and m.group(1) == stem:
            siblings.append({
                'path': str(candidate),
                'position_index': int(m.group(2)),
                'filename': candidate.name,
                'is_opened_file': str(candidate.resolve()) == resolved_opened,
            })

    if len(siblings) < 2:
        return []   # not actually a multi-position set

    siblings.sort(key=lambda s: s['position_index'])
    return siblings


# ---------------------------------------------------------------------------
# Position/scene selection dialog
# ---------------------------------------------------------------------------

def show_position_selection_dialog(positions: list[dict], title: str,
                                    label_key: str = 'filename') -> list[int]:
    """
    Show a checklist dialog for selecting which position(s)/scene(s) to open.

    Parameters
    ----------
    positions : list of dict
        Each dict must have a display label under `label_key`.
    title : str
    label_key : str
        Which key in each position dict to use as the checkbox label.

    Returns
    -------
    List of indices (into `positions`) that the user selected.
    Empty list if the dialog was cancelled.
    """
    from PyQt5.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QCheckBox, QPushButton,
        QLabel, QScrollArea, QWidget, QDialogButtonBox,
    )

    dlg = QDialog()
    dlg.setWindowTitle(title)
    dlg.setMinimumWidth(420)
    layout = QVBoxLayout(dlg)

    layout.addWidget(QLabel(
        f"Found {len(positions)} positions in this acquisition.\n"
        f"Select which to open (large multi-position sets can be slow —\n"
        f"consider opening only the positions you need):"
    ))

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    inner = QWidget()
    inner_layout = QVBoxLayout(inner)

    # Default-check the file the user actually opened, not just index 0 —
    # after sorting by position index, the opened file's position in the
    # list is arbitrary (e.g. opening Position_3.ims out of [1,2,3,4]
    # would otherwise default-check Position_1 instead).
    any_flagged = any(pos.get('is_opened_file') for pos in positions)

    checkboxes = []
    for i, pos in enumerate(positions):
        cb = QCheckBox(f"Position {pos.get('position_index', i)}: "
                       f"{pos.get(label_key, str(pos))}")
        if any_flagged:
            cb.setChecked(bool(pos.get('is_opened_file', False)))
        else:
            # Scenes (no is_opened_file concept) — default to the first entry.
            cb.setChecked(i == 0)
        checkboxes.append(cb)
        inner_layout.addWidget(cb)

    inner_layout.addStretch()
    scroll.setWidget(inner)
    layout.addWidget(scroll)

    btn_row = QHBoxLayout()
    all_btn  = QPushButton("Select All")
    none_btn = QPushButton("Select None")
    all_btn.clicked.connect(lambda: [cb.setChecked(True) for cb in checkboxes])
    none_btn.clicked.connect(lambda: [cb.setChecked(False) for cb in checkboxes])
    btn_row.addWidget(all_btn)
    btn_row.addWidget(none_btn)
    layout.addLayout(btn_row)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)

    if dlg.exec_() == QDialog.Accepted:
        return [i for i, cb in enumerate(checkboxes) if cb.isChecked()]
    return []
