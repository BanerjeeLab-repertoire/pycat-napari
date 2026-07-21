"""GUI-free lazy array sources (``_TiffPageStack``, ``_LazyArraySource``) — extracted from
``file_io.py`` so the TIFF lazy wrappers can be imported and perf-tested without dragging in
PyQt5. **Qt/napari-free by contract** (enforced by ``tests/test_lazy_sources_headless.py``).

``file_io.py`` imports the GUI stack at module scope (two ``QDialog`` subclasses live there), so
every wrapper defined beside them was reachable only by importing PyQt5 first. That made the
wrappers impossible to exercise headlessly — which is exactly what a performance harness or a CI
perf gate wants to do. The wrappers themselves never needed Qt: their bodies use ``tifffile``,
``numpy`` and two already-shared helpers. Only their address did.

The OME file-set helpers (``resolve_ome_file_set``, ``build_ome_page_map``) moved with them:
they are ``_TiffPageStack``'s multi-file machinery and have no other caller.

The only napari mentions below are in COMMENTS explaining the duck-typing this module is written
against. Keep it that way — the headless test is the contract.

Both names are re-exported from ``file_io.py`` so existing
``from pycat.file_io.file_io import _TiffPageStack`` callers keep working.
"""

# Standard library imports
#   `os`, `re` and `tifffile` are imported INSIDE the function bodies that use them, verbatim as
#   they were in `file_io.py`. The lazy `tifffile` import is what keeps this module cheap to
#   import — which is the point of the module.

# Third party imports
import numpy as np

# Local application imports
from pycat.file_io.stack_access import to_unit_float32
from pycat.file_io.readers.ims_reader import _suppress_ims_chunk_prints  # for _ZarrTYX (Qt-free)


def resolve_ome_file_set(primary_path):
    """Inspect a (possibly multi-file) OME-TIFF and report which companion files
    the metadata references and which are actually present on disk.

    Micro-Manager / OME-TIFF acquisitions are often split across sibling files
    (``..._MMStack_Pos0.ome.tif``, ``..._1.ome.tif``, …). The OME metadata in the
    FIRST file lists every file in the set. Two things can go wrong:

      * the companion files ARE present → we want to read frames from whichever
        file physically holds them (a true multi-file lazy view);
      * the companions are MISSING (a user copied one file out of the set without
        realising they were linked) → tifffile silently zero-fills the absent
        planes and prints a per-frame warning. Zero frames are misleading, so we
        prefer to use only the frames that physically exist and say so.

    Returns a dict:
        {
          'referenced': [filenames listed in OME metadata],
          'present':    [filenames that exist on disk, in order],
          'missing':    [filenames referenced but absent],
          'is_multifile': bool,   # more than one file referenced
          'complete':   bool,     # all referenced files present
        }
    The caller decides policy (warn + use present frames, build a cross-file
    view, etc.). Never raises — on any parsing problem it reports the primary
    file alone as a single-file set.
    """
    import os
    import re
    result = {'referenced': [], 'present': [], 'missing': [],
              'is_multifile': False, 'complete': True}
    try:
        import tifffile as _tf
        with _tf.TiffFile(primary_path) as _t:
            ome = _t.ome_metadata or ''
        # OME lists each file via <UUID FileName="...">; de-duplicate, keep order.
        names = []
        for fn in re.findall(r'FileName="([^"]+)"', ome):
            if fn not in names:
                names.append(fn)
        primary_name = os.path.basename(primary_path)
        if primary_name not in names:
            names.insert(0, primary_name)
        result['referenced'] = names
        result['is_multifile'] = len(names) > 1
        folder = os.path.dirname(os.path.abspath(primary_path))
        for fn in names:
            if os.path.exists(os.path.join(folder, fn)):
                result['present'].append(fn)
            else:
                result['missing'].append(fn)
        result['complete'] = (len(result['missing']) == 0)
    except Exception:
        # Any failure → treat as a plain single file (safe default).
        import os as _os
        result['referenced'] = [_os.path.basename(primary_path)]
        result['present'] = list(result['referenced'])
        result['missing'] = []
        result['is_multifile'] = False
        result['complete'] = True
    return result


def build_ome_page_map(primary_path):
    """Build a global frame → (file_path, page_index) map for an OME set,
    including ONLY files that physically exist. Frames whose backing file is
    missing are omitted (not zero-filled), so the resulting stack contains only
    real data. Also returns the count of frames dropped because their file was
    absent.

    Returns (page_map, n_missing_frames) where page_map is a list of
    (abs_file_path, page_index_within_that_file). Reading frame t means opening
    page_map[t][0] and reading its page page_map[t][1].

    Falls back to a single-file map (this file's own pages) on any problem.
    """
    import os
    info = resolve_ome_file_set(primary_path)
    folder = os.path.dirname(os.path.abspath(primary_path))
    page_map = []
    n_missing_frames = 0
    try:
        import tifffile as _tf
        for fn in info['referenced']:
            fpath = os.path.join(folder, fn)
            if not os.path.exists(fpath):
                # Count how many frames this missing file would have held so the
                # caller can report it. Use the primary's per-file page count as
                # an estimate when the file itself can't be opened.
                continue
            with _tf.TiffFile(fpath) as _t:
                npages = len(_t.pages)
            for p in range(npages):
                page_map.append((os.path.abspath(fpath), p))
        # Report missing frames as the difference the OME metadata implied. We
        # can only know present frames for certain; expose the missing FILE
        # count via the caller (resolve_ome_file_set) — frame count for missing
        # files is not reliably knowable without the files, so report 0 here and
        # let the caller warn based on missing file names.
        if not page_map:
            raise ValueError("empty page map")
    except Exception:
        # Fallback: this file's own pages only.
        try:
            import tifffile as _tf
            with _tf.TiffFile(primary_path) as _t:
                npages = len(_t.pages)
            page_map = [(os.path.abspath(primary_path), p) for p in range(npages)]
        except Exception:
            page_map = [(os.path.abspath(primary_path), 0)]
    return page_map, n_missing_frames


class _TiffPageStack:
    """Lazy (T, Y, X) wrapper that reads ONE frame at a time straight from a
    multipage TIFF via tifffile's page reader.

    This is the fast path for Micro-Manager / OME-TIFF time-series. The structured reader'sImage's
    dask reader consults the OME plane-map on every frame read, so scrubbing a
    large MMStack lags badly; a plain `TiffFile.pages[t].asarray()` is a direct
    seek+read of a single page (no dask graph, no OME-map walk, no copy of the
    whole stack), which matches the smooth per-frame behaviour of the native IMS
    zarr path. The file handle is kept open for the life of the wrapper.
    """
    def __init__(self, tiff_path, n_frames, H, W, dtype, channel_idx=0,
                 n_channels=1):
        import tifffile as _tf
        self._path   = tiff_path
        self._nc     = max(1, int(n_channels))
        self._ci     = int(channel_idx)

        # ── The SOURCE dtype was accepted and thrown away ────────────────────────────────
        #
        # ``dtype`` was a parameter, and the next line was ``self.dtype = np.dtype('float32')``.
        # **The source dtype was never stored** — so the wrapper could not have normalised even if
        # it had wanted to, and ``__getitem__`` did a bare ``arr.astype(np.float32)``: *a uint16
        # frame arrived as float32 holding **raw counts**, 0–65535.*
        #
        # **The 2-D loader does something else entirely.** It calls
        # ``dtype_conversion_func(data, 'float32')`` → ``skimage.img_as_float32``, which **divides
        # by the dtype max** and yields **[0, 1]**.
        #
        # ***Same pixels. Same file. Two loaders. A factor of 65535 apart.***
        #
        # And **[0, 1] is the real contract** — not a preference:
        #
        # * **17 toolbox functions declare it** in their signature docstrings, including
        #   ``partition_coefficient_field`` and ``fit_bimodal_intensity``;
        # * ``skimage.exposure.equalize_adapthist`` **raises** on anything else
        #   (*"Images of type float must be between -1 and 1"*), and preprocessing depends on it;
        # * ``img_as_uint`` — the save path's converter — **raises** on it too.
        #
        # Nothing has broken yet only because every current stack consumer happens to be immune: a
        # ratio (optical density: the 65535 cancels), a per-frame normalisation
        # (``analyse_frame_quality``), or a gradient. ***That is luck, not design.*** The next
        # function written against the documented contract will not be immune.
        #
        # So: keep the source dtype, and normalise by **its** max — exactly what ``img_as_float32``
        # does for the 2-D path.
        self._src_dtype = np.dtype(dtype) if dtype is not None else None

        self.dtype   = np.dtype('float32')
        self.ndim    = 3

        # Decide single-file (fast path) vs multi-file OME set. For a genuine
        # multi-file acquisition we build a page map spanning the files that are
        # actually PRESENT on disk; missing companions are dropped (not zeroed),
        # and the frame count is reduced to match real data.
        self._page_map = None          # list of (abs_path, page_idx) if multifile
        self._handles = {}             # abs_path -> open TiffFile (lazy)
        info = resolve_ome_file_set(tiff_path)
        if info.get('is_multifile') and not info.get('complete'):
            # Some companion files are missing — use only present frames.
            page_map, _ = build_ome_page_map(tiff_path)
            self._page_map = page_map
            self._present_info = info
            real_frames = len(page_map) // self._nc
            self.shape = (int(real_frames), int(H), int(W))
        elif info.get('is_multifile') and info.get('complete'):
            # All companions present — read across files via the page map.
            page_map, _ = build_ome_page_map(tiff_path)
            self._page_map = page_map
            self._present_info = info
            total_frames = len(page_map) // self._nc
            self.shape = (int(total_frames), int(H), int(W))
        else:
            # Single-file fast path (unchanged behaviour): keep one open handle
            # and index its series/pages directly.
            self._tif = _tf.TiffFile(tiff_path)
            try:
                self._pages = self._tif.series[0].pages
            except Exception:
                self._pages = self._tif.pages
            self.shape = (int(n_frames), int(H), int(W))

    def _page_index(self, t):
        # Interleaved channels are stored as consecutive pages per timepoint.
        return int(t) * self._nc + self._ci

    def _get_handle(self, path):
        """Lazily open (and cache) a TiffFile handle for a page-map file."""
        h = self._handles.get(path)
        if h is None:
            import tifffile as _tf
            h = _tf.TiffFile(path)
            self._handles[path] = h
        return h

    def _read_frame(self, t):
        if self._page_map is not None:
            # Multi-file: look up which physical file + page holds this frame.
            gi = self._page_index(t)
            if gi >= len(self._page_map):
                # Past the end of real data — return a black frame rather than
                # crashing (defensive; shape math should prevent this).
                return np.zeros(self.shape[1:], np.float32)
            path, page_idx = self._page_map[gi]
            handle = self._get_handle(path)
            arr = np.asarray(handle.pages[page_idx].asarray())
            # `[0, 1]`, not raw counts — the range the analysis stack is written for, and the range
            # the 2-D loader has always produced. See `to_unit_float32`.
            return to_unit_float32(arr, self._src_dtype or arr.dtype)
        # Single-file fast path.
        arr = np.asarray(self._pages[self._page_index(t)].asarray())
        return to_unit_float32(arr, self._src_dtype or arr.dtype)

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            t_idx, spatial = idx[0], idx[1:]
        else:
            t_idx, spatial = idx, ()

        # napari (and downstream code) may index the T axis with an int (one
        # frame — the scrubbing case), a slice (a range or the whole stack), or
        # a fancy index. Handle each; only the int case is the fast per-frame
        # read, but slices must not crash (the previous version did int(slice)).
        if isinstance(t_idx, slice):
            t_range = range(*t_idx.indices(self.shape[0]))
            frames = np.stack([self._read_frame(t) for t in t_range], axis=0) \
                if len(t_range) else np.empty((0,) + self.shape[1:], np.float32)
            if spatial:
                return frames[(slice(None),) + spatial]
            return frames
        if isinstance(t_idx, (list, tuple, np.ndarray)):
            frames = np.stack([self._read_frame(int(t)) for t in t_idx], axis=0)
            if spatial:
                return frames[(slice(None),) + spatial]
            return frames

        # Scalar index → single frame (the common, fast path).
        arr = self._read_frame(t_idx)
        if spatial:
            return arr[spatial]
        return arr

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)

    def as_full_array(self, dtype=np.float32, progress_callback=None):
        """Materialise the whole stack as a real (T, H, W) numpy array, read
        one frame at a time. Use this for analysis that needs every frame — it
        avoids the deliberately-truncated __array__ (which returns only frame 0
        to keep napari's incidental array requests cheap).

        dtype=None preserves the source frame dtype (e.g. integer label masks).
        progress_callback : optional callable(done, total) for a determinate
            "Materializing…" bar.
        """
        _f0 = self._read_frame(0)
        _dt = _f0.dtype if dtype is None else dtype
        out = np.empty(self.shape, dtype=_dt)
        out[0] = _f0.astype(_dt)
        n = self.shape[0]
        if progress_callback is not None:
            try: progress_callback(1, n)
            except Exception: pass
        for t in range(1, n):
            out[t] = self._read_frame(t).astype(_dt)
            if progress_callback is not None:
                try: progress_callback(t + 1, n)
                except Exception: pass
        return out

    def __len__(self):
        return self.shape[0]

    # ── `transpose()` was DELETED ────────────────────────────────────────────────────────
    #
    # It read::
    #
    #     def transpose(self, *axes):
    #         return self.__getitem__(0)[np.newaxis]
    #
    # **Whatever axes you asked for, you got frame 0**, shaped (1, Y, X) — and nothing about the
    # result looked wrong. It is precisely the bug `__array__` was fixed for in 1.6.3, wearing a
    # different name, and it **survived that fix because the guard only checked `__array__`.**
    #
    # *A guard that checks the bug it already found is not checking the bug.*
    #
    # **Absence is the honest implementation, and it is proven.** The three `_ImsReader*` wrappers
    # have never defined `transpose` — and one of them carries the 600-plane IMS file that scrubs
    # at 0.5% of scene. napari duck-types for the method; not having it is a path napari already
    # takes every time it touches an IMS layer.
    #
    # A caller that genuinely needs a transposed stack must **say so**, and pay for it:
    #
    #     materialize_stack(layer).transpose(...)

    def close(self):
        # Single-file mode keeps one handle in self._tif; multi-file mode keeps
        # a cache of per-file handles in self._handles. Close whichever exist.
        try:
            tif = getattr(self, '_tif', None)
            if tif is not None:
                tif.close()
        except Exception:
            pass
        for h in getattr(self, '_handles', {}).values():
            try:
                h.close()
            except Exception:
                pass


def _lazy_indices(selector, size):
    """Return concrete indices for an int/slice/list selector against an axis.

    A deliberate twin of ``ims_reader._ims_indices``. The two are **not** shared on purpose: the
    IMS path is validated and shipping, and importing it here would drag
    ``imaris_ims_file_reader`` into a module whose entire contract is being cheap and Qt-free to
    import (it is not installed in the headless `core` CI job). ``test_ztz_readers_agree.py`` is
    what keeps the twins honest — it drives both families through the same index set and demands
    identical results, so a divergence is a test failure rather than a silent inconsistency.
    """
    if isinstance(selector, slice):
        return list(range(*selector.indices(size)))
    if selector is Ellipsis or selector is None:
        return list(range(size))
    if isinstance(selector, (list, tuple, np.ndarray)):
        return [int(i) for i in selector]
    return [int(selector)]


def _tiff_plane_2d(raw, src_dtype):
    """Normalize a raw TIFF page read to exactly (Y, X) in ``[0, 1]``.

    The TIFF twin of ``ims_reader._ims_frame_2d``, and it exists for the same two reasons:

    * **``[0, 1]`` from the SOURCE dtype, not raw counts.** `read_tiff_plane` and
      `page.asarray()` both hand back raw counts; a bare ``astype(float32)`` here is the 1.6.x
      intensity bug (*same pixels, same file, two loaders, a factor of 65535 apart*).
    * **A page is not always a plane.** An RGB/sample page comes back with a leading `S` axis, and
      napari raises *"axes don't match array"* on transpose if a singleton survives. Squeeze, then
      **assert** — a wrong-shaped plane must not reach the viewer quietly.
    """
    arr = to_unit_float32(raw, src_dtype if src_dtype is not None
                          else getattr(raw, 'dtype', None))
    arr = np.squeeze(arr)
    if arr.ndim != 2:
        raise ValueError(f"Expected TIFF plane to reduce to 2-D (Y, X), got shape {arr.shape}")
    return arr


class _TiffPageGeometry:
    """**Open the file ONCE; ask it for its page order ONCE.** Then every plane is a seek.

    ── Why this is not just ``read_tiff_plane`` ─────────────────────────────────────────────

    ``tiff_planes.read_tiff_plane`` is the right *arithmetic* and the wrong *host* for a scrubbing
    wrapper: it does ``with tifffile.TiffFile(path) as handle:`` on **every call** and rebuilds
    ``handle.series[0]`` — which re-walks the OME-XML — before reading a single page. napari asks
    for a plane on **every slider tick**, so that cost lands per tick. Measured on a 60-plane
    OME-TIFF z-stack: **3.61 ms/plane reopening vs 0.17 ms/plane with a cached handle — 21x**, and
    the gap widens with the size of the OME-XML, because the series rebuild is what dominates.
    Backing the Z/TZ wrappers with it would make them slower than the BioIO path they replace,
    which is the opposite of why ``_TiffPageStack`` exists.

    So this holds the handle for the life of the wrapper (exactly the contract ``_TiffPageStack``
    states) and reuses the *page-index arithmetic* — which is the part that carries the hard-won
    knowledge:

    * ``_page_and_slice`` is the primary map, and it is **not** a fixed formula. It is a
      mixed-radix fold over the axis order **the file itself declares**, so a ``ZTYX`` or ``CTZYX``
      file indexes correctly. Its docstring is an autopsy of two earlier versions that hardcoded an
      order and put the wrong pixels on screen.
    * ``_legacy_geometry`` is the fallback for a file that declares **no** axes — the plain
      multipage TIFF PyCAT has already had to ask the user about. Only there does the classic
      ``frame = ((t * n_z) + z) * channels + c`` apply.

    Reimplementing either would re-open bugs the comments in ``tiff_planes.py`` were written over.
    """

    def __init__(self, tiff_path):
        import tifffile as _tf
        self._tif = _tf.TiffFile(tiff_path)
        try:
            series = self._tif.series[0] if self._tif.series else None
        except Exception:
            series = None
        self._series = series
        # The series' page list spans a multi-file OME set; tifffile resolves the companions
        # itself. See the reasoning in `tiff_planes.read_tiff_plane`.
        self._pages = series.pages if series is not None else self._tif.pages
        self._axes = getattr(series, 'axes', None) if series is not None else None
        self._shape = getattr(series, 'shape', None) if series is not None else None
        try:
            self._page_ndim = len(self._pages[0].shape)
        except Exception:
            self._page_ndim = None

    @property
    def n_pages(self):
        return len(self._pages)

    def read(self, *, t, c, z, n_channels, n_z):
        """The raw page (or the plane sliced out of it). Raises IndexError rather than guessing."""
        from pycat.file_io.tiff_planes import _legacy_geometry, _page_and_slice

        located = None
        if self._series is not None:
            located = _page_and_slice(self._axes, self._shape, self._page_ndim, t=t, c=c, z=z)

        if located is not None:
            index, inner = located
            pages, n_pages = self._pages, len(self._pages)
        else:
            pages, n_pages, index, inner = _legacy_geometry(
                self._tif, self._pages, t=t, c=c, z=z, n_channels=n_channels, n_z=n_z)

        if index >= n_pages:
            # **Do not return page 0 and pretend.** A wrong plane is worse than a loud failure —
            # it looks entirely correct on screen.
            raise IndexError(
                f"TIFF page {index} does not exist (file has {n_pages}); "
                f"asked for t={t}, z={z}, c={c}")

        plane = np.asarray(pages[index].asarray())
        if inner:
            plane = plane[inner]
        return plane

    def close(self):
        try:
            self._tif.close()
        except Exception:
            pass


class _TiffPageStackZYX:
    """Lazy (Z, Y, X) TIFF view — the native z-stack path, no zarr.

    The contract is ``_ImsReaderZYX``'s, deliberately and exactly: same shape/ndim/dtype, same
    ``__getitem__`` squeeze, same refusing ``__array__``, same ``__len__``. Downstream code
    (segmentation, 3-D volume, measurement, brushing) must never have to know whether a z-stack
    came from IMS or TIFF, so a TIFF-only shape that behaves *almost* the same is the failure this
    class is written to avoid. ``tests/test_ztz_readers_agree.py`` enforces it.

    Like ``_ImsReaderZYX``, this pins a single timepoint (``t=0``) — a pure z-stack has one.
    """

    def __init__(self, tiff_path, n_z, H, W, dtype, channel_idx=0, n_channels=1, t=0):
        self._path = tiff_path
        self._nc = max(1, int(n_channels))
        self._ci = int(channel_idx)
        self._nz = max(1, int(n_z))
        self._t = int(t)
        # The SOURCE dtype, kept apart from the dtype we HAND OUT. Conflating them is what let a
        # wrapper advertise uint16 and return float32 raw counts.
        self._src_dtype = np.dtype(dtype) if dtype is not None else None
        self._geom = _TiffPageGeometry(tiff_path)

        self.shape = (int(self._nz), int(H), int(W))
        self.dtype = np.dtype('float32')
        self.ndim = 3

    def _read_plane(self, z):
        raw = self._geom.read(t=self._t, c=self._ci, z=int(z),
                              n_channels=self._nc, n_z=self._nz)
        return _tiff_plane_2d(raw, self._src_dtype)

    def __getitem__(self, idx):
        # The squeeze semantics are `_ImsReaderZYX.__getitem__`'s, copied rather than re-derived.
        if isinstance(idx, tuple):
            z_sel = idx[0] if len(idx) > 0 else slice(None)
            yx_sel = idx[1:] if len(idx) > 1 else (slice(None), slice(None))
        else:
            z_sel = idx
            yx_sel = (slice(None), slice(None))
        z_indices = _lazy_indices(z_sel, self.shape[0])
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

    def close(self):
        self._geom.close()


class _TiffPageStackTZYX:
    """Lazy (T, Z, Y, X) TIFF view — the native T+Z path, no zarr.

    ``_ImsReaderTZYX``'s contract, exactly. See `_TiffPageStackZYX` for why that matters.
    """

    def __init__(self, tiff_path, n_t, n_z, H, W, dtype, channel_idx=0, n_channels=1):
        self._path = tiff_path
        self._nc = max(1, int(n_channels))
        self._ci = int(channel_idx)
        self._nz = max(1, int(n_z))
        self._nt = max(1, int(n_t))
        self._src_dtype = np.dtype(dtype) if dtype is not None else None
        self._geom = _TiffPageGeometry(tiff_path)

        self.shape = (int(self._nt), int(self._nz), int(H), int(W))
        self.dtype = np.dtype('float32')
        self.ndim = 4

    def _read_plane(self, t, z):
        raw = self._geom.read(t=int(t), c=self._ci, z=int(z),
                              n_channels=self._nc, n_z=self._nz)
        return _tiff_plane_2d(raw, self._src_dtype)

    def __getitem__(self, idx):
        # Copied from `_ImsReaderTZYX.__getitem__` — including the reverse-order squeeze and the
        # reason for it. A subtly different squeeze here is precisely the inconsistency the
        # cross-reader agreement test exists to catch.
        if isinstance(idx, tuple):
            t_sel = idx[0] if len(idx) > 0 else slice(None)
            z_sel = idx[1] if len(idx) > 1 else slice(None)
            yx_sel = idx[2:] if len(idx) > 2 else (slice(None), slice(None))
        else:
            t_sel, z_sel, yx_sel = idx, slice(None), (slice(None), slice(None))
        t_indices = _lazy_indices(t_sel, self.shape[0])
        z_indices = _lazy_indices(z_sel, self.shape[1])
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

    def close(self):
        self._geom.close()


# `as_full_array` is deliberately ABSENT from both Z/TZ wrappers — `_ImsReaderZYX` /
# `_ImsReaderTZYX` do not define it either, and matching them is the point. `materialize_stack`
# therefore refuses on a Z/TZ layer (its `np.asarray` hits the guard) exactly as it already does
# for IMS. Adding it here and not there would be a new inconsistency in a module written to remove
# one; if a caller ever genuinely needs a whole volume, it should be added to BOTH families, with a
# test that they agree.


class _LazyArraySource:
    """**A napari-facing view over ANY lazy source — dask, zarr, or numpy.**

    ── The wrapper it replaces was named after the wrong thing ──────────────────

    ``_ZarrTZYX_generic`` is not zarr-specific. It receives **zarr arrays, numpy arrays, and BioIO
    dask arrays** — and the name told a reader it could rely on zarr semantics it does not have.

    More importantly, the TZYX branch used to **transcode the entire file into a temporary zarr**
    before showing anything, *purely so it would have a zarr to wrap.* **The dask array was already
    lazy.** The copy bought nothing and cost the whole file.

    This wraps whatever it is given:

    * ``__getitem__`` computes **only the requested slice** — one plane per slider move
    * ``__array__`` **refuses**, because an implicit full read is never what the caller meant

    *(A zarr cache remains the right thing for repeated random access. But it belongs in the
    background, behind an explicit action — not on the critical path to first display.)*
    """

    def __init__(self, source):
        self._source = source
        self.shape = tuple(int(v) for v in source.shape)
        # The SOURCE's dtype — what to divide by. Kept apart from `self.dtype`, which is what the
        # wrapper HANDS OUT (float32, by the [0, 1] contract). Conflating them is what let this
        # class advertise uint16 while returning float32 raw counts.
        self._src_dtype = np.dtype(getattr(source, 'dtype', np.float32))
        self.dtype = np.dtype('float32')
        self.ndim = len(self.shape)

    def __getitem__(self, index):
        value = self._source[index]
        # dask computes on demand; zarr and numpy are already here. Ask, do not assume — a reader
        # plugin is free to return either.
        if hasattr(value, 'compute'):
            value = value.compute()
        # `[0, 1]` from the SOURCE dtype, not raw counts. This wrapper already kept the source
        # dtype in `self.dtype` — it just never USED it, so it advertised uint16 and handed back
        # float32 raw counts. See `stack_access.to_unit_float32`.
        return to_unit_float32(value, self._src_dtype)

    def __len__(self):
        return self.shape[0]

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)


# ── `_ZarrTYX_generic` was DELETED in 1.6.9 ──────────────────────────────────────────────
#
# **It was named after the wrong thing.** It is not zarr-specific — it received **zarr arrays,
# numpy arrays, and BioIO dask arrays** — and the name told every reader it could rely on zarr
# semantics it does not have.
#
# Worse, the TZYX branch **transcoded the entire file into a temporary zarr** before showing
# anything, *purely so it would have a zarr to wrap.* **The dask array was already lazy.** The copy
# bought nothing and cost the whole file. *(Removed in 1.6.4.)*
#
# ``_LazyArraySource`` wraps whatever it is given, and was verified to behave **identically** on
# every indexing pattern napari uses on a (T, Y, X) layer — ``stack[t]``, ``stack[t, :, :]``,
# ``stack[t0:t1]``.


class _SceneStack:
    """**Lazy (T, Y, X) wrapper for ONE scene of a multi-scene acquisition.**

    A multi-position CZI/IMS/OME-TIFF holds several *scenes* (positions/wells). The loader used to
    materialise **every selected scene at once** — the exact load-everything memory profile the
    streaming work removed elsewhere. This wrapper holds the reader and a **pinned scene**, and reads
    each plane **from that scene, on demand**.

    ── Why the scene is re-pinned on every read ────────────────────────────────────────────────
    A structured reader (BioIO ``BioImage``) is **stateful**: ``set_scene`` mutates which position it
    reads, and the reader is shared (the reader cache). So a plane read for this wrapper's scene must
    set the scene *immediately before* the read, or a plane from another position could be served — a
    **silently wrong image**, the headline hazard of position switching. ``read_plane(scene=…)`` does
    exactly that (set-scene then read one plane) under the reader's lock, so this wrapper passes its
    pinned scene on *every* frame read rather than trusting the reader's current state.

    ── Why there is no cross-scene cache to go stale ───────────────────────────────────────────
    Switching position builds a **fresh** wrapper for the new scene (the switcher replaces the layer's
    ``data``). Nothing is cached across scenes here, so a stale previous-position plane cannot exist by
    construction — stronger than clearing a shared cache and hoping the clear is never missed.

    ── The contract (the same duck type every wrapper here satisfies) ─────────────────────────
    ``.shape`` / ``.dtype`` (float32) / ``.ndim``; ``__getitem__`` returns ``[0, 1]`` float32 reading
    exactly the indexed frame(s); ``__array__`` **refuses** (``test_no_eager_reads``). It reads through
    the **structured** ``read_plane`` path (no ``path=`` — the TIFF fast path indexes by page and would
    ignore the scene, a stale-scene trap), so it works for any reader BioIO exposes ``set_scene`` on.
    """

    def __init__(self, image, scene, n_t, H, W, dtype, channel_idx=0, z=0, plane_reader=None):
        self._image = image
        self._scene = scene                       # the scene NAME (matches image.scenes + the layer tag)
        self._ci = int(channel_idx)
        self._z = int(z)
        self._src_dtype = np.dtype(dtype) if dtype is not None else None
        self.dtype = np.dtype('float32')
        self.ndim = 3
        self.shape = (int(n_t), int(H), int(W))
        # Injected for tests; defaults to the real scene-pinning single-plane reader.
        self._plane_reader = plane_reader

    @property
    def scene(self):
        """The scene (position) this wrapper is pinned to — the value the layer is tagged with."""
        return self._scene

    def _read(self, t):
        reader = self._plane_reader
        if reader is None:
            from pycat.file_io.image_reader import read_plane
            reader = read_plane
        # No `path=`: force the structured, scene-respecting path. `read_plane` sets the scene then
        # reads ONE (Y, X) plane; it returns RAW values, so normalise to the [0, 1] contract here,
        # exactly as the sibling wrappers do.
        arr = np.asarray(reader(self._image, scene=self._scene, t=int(t), c=self._ci, z=self._z))
        return to_unit_float32(arr, self._src_dtype or arr.dtype)

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            t_idx, spatial = idx[0], idx[1:]
        else:
            t_idx, spatial = idx, ()

        if isinstance(t_idx, slice):
            t_range = range(*t_idx.indices(self.shape[0]))
            frames = (np.stack([self._read(t) for t in t_range], axis=0) if len(t_range)
                      else np.empty((0,) + self.shape[1:], np.float32))
            return frames[(slice(None),) + spatial] if spatial else frames
        if isinstance(t_idx, (list, tuple, np.ndarray)):
            frames = np.stack([self._read(int(t)) for t in t_idx], axis=0)
            return frames[(slice(None),) + spatial] if spatial else frames

        arr = self._read(t_idx)                   # scalar index → one frame (the fast, common path)
        return arr[spatial] if spatial else arr

    def __len__(self):
        return self.shape[0]

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — an implicit full read is never meant, and for a
        multi-scene file it would materialise a whole position."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)


# ── _ZarrTYX: IMS zarr (T,C,1,Y,X) presented as (T,Y,X) — moved from file_io.py, 1.6.146 ──

class _ZarrTYX:
    """
    Thin wrapper presenting an IMS zarr array's z_full[:, c, 0, :, :] as a
    (T, Y, X) array that satisfies napari's requirements without dask.
    Suppresses the per-chunk debug prints from imaris_ims_file_reader.
    """
    def __init__(self, z, c, suppress_ctx=None):
        self._z   = z
        self._c   = c
        self._ctx = suppress_ctx or _suppress_ims_chunk_prints
        T, _, _, Y, X = z.shape
        self.shape = (T, Y, X)
        self.dtype = np.dtype('float32')
        self.ndim  = 3

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            t_idx, spatial = idx[0], idx[1:]
        else:
            t_idx, spatial = idx, (slice(None), slice(None))
        with self._ctx():
            raw = self._z[t_idx, self._c, 0]
        # `[0, 1]` from the SOURCE dtype (`self._z.dtype`) — not raw counts. See `to_unit_float32`.
        arr = to_unit_float32(raw, getattr(self._z, 'dtype', None))
        if arr.ndim == 2:
            return arr[spatial]
        return arr[(slice(None),) + spatial]

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)

    def __len__(self):
        return self.shape[0]

    # `transpose()` is deliberately ABSENT — it used to return frame 0 as (1, Y, X) for any
    # requested axes. See `_TiffPageStack` for the full reasoning.
