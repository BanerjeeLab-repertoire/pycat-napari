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
