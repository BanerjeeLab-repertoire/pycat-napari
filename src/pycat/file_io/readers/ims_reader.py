"""Pure lazy IMS readers — extracted from ``FileIOClass._open_stack_ims`` (god-class
decomposition #3, see docs/audits/fileio_godclass_roadmap_2026-07-15.md). Qt/napari-free.

The three ``_ImsReader*`` classes are lazy adapters consumed DURING napari layer
construction in the controller (not a separable read-then-construct flow); they read one
plane at a time through ``_ims_frame_2d`` and refuse implicit full-stack materialization via
the lazy-guard. Moved VERBATIM — the bodies carry subtle correctness (source-dtype
normalization through ``to_unit_float32`` + the accidental-full-read guard) and must not be
"cleaned up". ``_suppress_ims_chunk_prints``, ``_ims_indices`` and ``_ims_pixel_size_um`` are
also called directly by the controller, which imports them back from here.
"""

from __future__ import annotations

import contextlib
import io
import sys

import numpy as np

from pycat.file_io.stack_access import to_unit_float32


@contextlib.contextmanager
def _suppress_ims_chunk_prints():
    """
    The imaris_ims_file_reader package prints a 'GET : <key>' debug line
    plus chunk slice/shape info to stdout on every single zarr chunk read.
    Since our lazy IMS loading reads chunks on-demand as napari displays
    frames, this floods the terminal with dozens of lines per frame.
    This context manager redirects stdout to a null sink for the duration
    of any IMS read operation, since the package offers no verbosity flag.
    """
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = old_stdout


def _ims_indices(selector, size):
    """Return concrete indices for an int/slice/list selector against an IMS axis."""
    if isinstance(selector, slice):
        return list(range(*selector.indices(size)))
    if selector is Ellipsis or selector is None:
        return list(range(size))
    if isinstance(selector, (list, tuple, np.ndarray)):
        return [int(i) for i in selector]
    return [int(selector)]


def _ims_frame_2d(raw):
    """Normalize imaris_ims_file_reader output to exactly (Y, X).

    With squeeze_output=False, direct IMS reads may retain singleton T/C/Z axes
    even when indexed with integers. Napari expects a 2-D plane after slicing a
    (T, Y, X) layer, so leaving those singleton axes in place causes
    ValueError: axes don't match array during napari transpose.
    """
    # `[0, 1]` from the SOURCE dtype, not raw counts — the same contract the 2-D loader has always
    # honoured. **This cast lives in a HELPER, so a scan of the wrapper classes missed it entirely**
    # while `_ImsReaderTYX`/`ZYX`/`TZYX` all read through it. See `stack_access.to_unit_float32`.
    arr = to_unit_float32(raw, getattr(raw, 'dtype', None))
    arr = np.squeeze(arr)
    if arr.ndim != 2:
        raise ValueError(f"Expected IMS plane to reduce to 2-D (Y, X), got shape {arr.shape}")
    return arr


def _ims_pixel_size_um(reader, width_px):
    """Read physical pixel size (um/px) from an IMS file's spatial extents.

    Imaris .ims files store the physical bounding box as DataSetInfo/Image
    attributes ExtMin0/ExtMax0 (X), ExtMin1/ExtMax1 (Y), ExtMin2/ExtMax2 (Z),
    each as a FIXED-LENGTH ASCII CHAR ARRAY (e.g. b'-42107.8'). Pixel size is
    (ExtMax0 - ExtMin0) / width. The values can be negative (stage coordinates),
    which is why a naive parse can fail -- we decode the char array to a string
    and float() it explicitly.

    Prefers reading the h5py handle directly (reader.hf) because the reader's
    own accessor name and behaviour vary across imaris_ims_file_reader versions
    and it silently mishandles some char-array attributes.

    Returns um/px as a float, or None if the extents can't be read.
    """
    def _to_float(raw):
        if raw is None:
            return None
        try:
            if hasattr(raw, 'tobytes'):
                raw = raw.tobytes()
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode('ascii', errors='ignore')
            s = str(raw).strip().strip('\x00').strip()
            return float(s) if s else None
        except Exception:
            return None

    ext_min = ext_max = None

    hf = getattr(reader, 'hf', None)
    if hf is not None:
        try:
            img_attrs = hf['DataSetInfo']['Image'].attrs
            ext_min = _to_float(img_attrs.get('ExtMin0'))
            ext_max = _to_float(img_attrs.get('ExtMax0'))
        except Exception:
            ext_min = ext_max = None

    if ext_min is None or ext_max is None:
        for _meth in ('read_numerical_dataset_attr', 'read_attribute'):
            fn = getattr(reader, _meth, None)
            if fn is None:
                continue
            try:
                ext_max = _to_float(fn('ExtMax0'))
                ext_min = _to_float(fn('ExtMin0'))
                if ext_min is not None and ext_max is not None:
                    break
            except Exception:
                continue

    if ext_min is None or ext_max is None:
        return None
    extent = abs(ext_max - ext_min)
    if extent <= 0 or width_px <= 0:
        return None
    microns_per_pixel = extent / float(width_px)
    if not (1e-4 < microns_per_pixel < 1e4):
        return None
    return microns_per_pixel


class _ImsReaderTYX:
    """Lazy (T, Y, X) IMS view backed directly by imaris_ims_file_reader.ims."""
    def __init__(self, reader, c, suppress_ctx=None):
        self._reader = reader
        self._c = c
        self._ctx = suppress_ctx or _suppress_ims_chunk_prints
        T, _, _, Y, X = reader.shape
        self.shape = (T, Y, X)
        self.dtype = np.dtype('float32')
        self.ndim = 3

    def _read_frame(self, t):
        with self._ctx():
            raw = self._reader[int(t), self._c, 0, :, :]
        return _ims_frame_2d(raw)

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            t_sel = idx[0] if len(idx) > 0 else slice(None)
            yx_sel = idx[1:] if len(idx) > 1 else (slice(None), slice(None))
        else:
            t_sel = idx
            yx_sel = (slice(None), slice(None))
        t_indices = _ims_indices(t_sel, self.shape[0])
        frames = [self._read_frame(t)[yx_sel] for t in t_indices]
        if isinstance(t_sel, (int, np.integer)):
            return frames[0]
        return np.stack(frames, axis=0)

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)

    def __len__(self):
        return self.shape[0]


class _ImsReaderZYX:
    """Lazy (Z, Y, X) IMS view backed directly by imaris_ims_file_reader.ims."""
    def __init__(self, reader, c, t=0, suppress_ctx=None):
        self._reader = reader
        self._c = c
        self._t = t
        self._ctx = suppress_ctx or _suppress_ims_chunk_prints
        _, _, Z, Y, X = reader.shape
        self.shape = (Z, Y, X)
        self.dtype = np.dtype('float32')
        self.ndim = 3

    def _read_plane(self, z):
        with self._ctx():
            raw = self._reader[self._t, self._c, int(z), :, :]
        return _ims_frame_2d(raw)

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            z_sel = idx[0] if len(idx) > 0 else slice(None)
            yx_sel = idx[1:] if len(idx) > 1 else (slice(None), slice(None))
        else:
            z_sel = idx
            yx_sel = (slice(None), slice(None))
        z_indices = _ims_indices(z_sel, self.shape[0])
        planes = [self._read_plane(z)[yx_sel] for z in z_indices]
        if isinstance(z_sel, (int, np.integer)):
            return planes[0]
        return np.stack(planes, axis=0)

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)

    def __len__(self):
        return self.shape[0]


class _ImsReaderTZYX:
    """Lazy (T, Z, Y, X) IMS view backed directly by imaris_ims_file_reader.ims."""
    def __init__(self, reader, c, suppress_ctx=None):
        self._reader = reader
        self._c = c
        self._ctx = suppress_ctx or _suppress_ims_chunk_prints
        T, _, Z, Y, X = reader.shape
        self.shape = (T, Z, Y, X)
        self.dtype = np.dtype('float32')
        self.ndim = 4

    def _read_plane(self, t, z):
        with self._ctx():
            raw = self._reader[int(t), self._c, int(z), :, :]
        return _ims_frame_2d(raw)

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            t_sel = idx[0] if len(idx) > 0 else slice(None)
            z_sel = idx[1] if len(idx) > 1 else slice(None)
            yx_sel = idx[2:] if len(idx) > 2 else (slice(None), slice(None))
        else:
            t_sel, z_sel, yx_sel = idx, slice(None), (slice(None), slice(None))
        t_indices = _ims_indices(t_sel, self.shape[0])
        z_indices = _ims_indices(z_sel, self.shape[1])
        arr = np.stack([
            np.stack([self._read_plane(t, z)[yx_sel] for z in z_indices], axis=0)
            for t in t_indices
        ], axis=0)
        # Squeeze out scalar-selected axes in reverse order (Z first, then T)
        # so that arr[0, 0] returns (Y, X), arr[0, :] returns (Z, Y, X), etc.
        if isinstance(z_sel, (int, np.integer)):
            arr = arr[:, 0]   # (T, 1, Y, X) -> (T, Y, X) -- squeeze Z
        if isinstance(t_sel, (int, np.integer)):
            arr = arr[0]      # (T, ...) -> squeeze T (now leading axis)
        return arr

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)

    def __len__(self):
        return self.shape[0]
