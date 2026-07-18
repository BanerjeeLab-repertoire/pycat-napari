"""BioFormats-backed reader for streaming CZI files that libCZI cannot decode.

── Why this exists ────────────────────────────────────────────────────────────────────────
Zeiss fast-streaming/timelapse CZI (many-subblock, e.g. a 15,766-frame movie) cannot be read by
**any** libCZI-based path (bioio-czi, pylibczirw, aicspylibczi): metadata reads fine but every pixel
read raises ``RuntimeError: The method or operation is not implemented``. Confocal and
widefield-single-subblock CZI read fine through libCZI — see ``docs/audits/czi_bakeoff_2026-07-15.md``.
The reference Zeiss decoder that CAN read the streaming layout is **BioFormats** (Java), shipped as the
opt-in ``[bioformats]`` extra.

── Why the DIRECT reader, not bioio's dask ────────────────────────────────────────────────
The bake-off found ``BioImage(...).get_image_dask_data(...).compute()`` takes **50–80 s/plane** with
the numpy-safe ``bioio-bioformats 1.3.2`` (a dask-wrapper artifact), while the **direct Java reader**
``loci.formats.ImageReader.openBytes(...)`` reads a plane in **~5 ms**. So this reads pixels straight
from the reader — exactly as ``tiff_planes.read_tiff_plane`` bypasses bioio's broken ``aszarr`` for
TIFF. numpy stays ``<2.1`` (BioFormats is Java; only scyjava/jpype are on the Python side).

── Two workarounds the bootstrap needs ────────────────────────────────────────────────────
* BioFormats' ``woolz`` transitive jar lives only in OME's artifactory, not Maven Central — register
  that repo before the JVM starts, or resolution fails.
* ``bioio-bioformats 1.3.2`` pins Java BioFormats **6.7.0**, which reports "does not support" this CZI;
  **8.1.1** reads it. We start the JVM ourselves with the 8.1.1 endpoint before bioio appends 6.7.0.

Qt/napari-free. All state is the loci reader + numpy.
"""

from __future__ import annotations

import numpy as np

from pycat.file_io.stack_access import to_unit_float32
from pycat.utils.general_utils import debug_log

# The OME artifactory (for the ``woolz`` transitive jar) and the Java BioFormats version that can
# actually read the streaming CZI (6.7.0, which bioio-bioformats 1.3.2 pins, cannot).
_OME_MAVEN = "https://artifacts.openmicroscopy.org/artifactory/maven"
_BIOFORMATS_ENDPOINT = "ome:formats-gpl:8.1.1"

_JVM_STARTED = False


def bioformats_available() -> bool:
    """True if the ``[bioformats]`` extra is installed (the JVM bridge, not yet started)."""
    import importlib.util
    return all(importlib.util.find_spec(m) is not None
               for m in ("scyjava", "jpype", "bioio_bioformats"))


class BioFormatsUnavailable(RuntimeError):
    """Raised when a streaming CZI needs BioFormats but the extra is not installed."""


def _ensure_jvm():
    """Start the JVM once, configured so BioFormats 8.1.1 resolves and can read streaming CZI.

    Idempotent. Must run BEFORE any ``bioio_bioformats`` call that would start the JVM with its own
    (older, insufficient) endpoint — once the JVM is up, a later ``start_jvm`` is a no-op, so ours
    wins.
    """
    global _JVM_STARTED
    if _JVM_STARTED:
        return
    if not bioformats_available():
        raise BioFormatsUnavailable(
            "Reading this streaming CZI needs the BioFormats extra.\n"
            "  pip install pycat-napari[bioformats]\n"
            "(libCZI cannot decode Zeiss fast-streaming/timelapse CZI; BioFormats can.)")
    import scyjava
    import jpype
    if not jpype.isJVMStarted():
        # woolz lives only in OME's artifactory; formats-gpl:8.1.1 is the version that reads the file.
        scyjava.config.add_repositories({'ome': _OME_MAVEN})
        if _BIOFORMATS_ENDPOINT not in scyjava.config.endpoints:
            scyjava.config.endpoints.append(_BIOFORMATS_ENDPOINT)
        scyjava.start_jvm()
    _JVM_STARTED = True


def _attach_thread():
    """BioFormats readers are used serially from whichever thread scrubs; JPype requires the calling
    thread to be attached to the JVM. The open may happen on a worker thread and reads on the main
    thread, so attach defensively (a no-op if already attached)."""
    import jpype
    try:
        # attachThreadToJVM is a no-op if the thread is already attached — cheaper and cleaner than
        # the (now-deprecated) isThreadAttachedToJVM guard.
        jpype.attachThreadToJVM()
    except Exception:
        pass


def _numpy_dtype(reader):
    """Map the reader's BioFormats pixel type + endianness to a numpy dtype string."""
    import jpype
    ft = jpype.JPackage("loci").formats.FormatTools
    pt = int(reader.getPixelType())
    base = {
        int(ft.INT8): 'i1', int(ft.UINT8): 'u1',
        int(ft.INT16): 'i2', int(ft.UINT16): 'u2',
        int(ft.INT32): 'i4', int(ft.UINT32): 'u4',
        int(ft.FLOAT): 'f4', int(ft.DOUBLE): 'f8',
    }.get(pt, 'u2')
    if base in ('i1', 'u1'):
        return np.dtype(base)          # single byte — endianness irrelevant
    endian = '<' if bool(reader.isLittleEndian()) else '>'
    return np.dtype(endian + base)


class CziBioFormatsReader:
    """Owns one BioFormats ``ImageReader`` for a CZI, and hands out per-channel lazy (T, Y, X) stacks.

    Its lifetime must outlive the layers (lazy plane reads go back to it), so the loader retains it
    via the ``ImageSource`` pattern, exactly like the IMS readers.
    """

    def __init__(self, path):
        _ensure_jvm()
        _attach_thread()
        import jpype
        loci = jpype.JPackage("loci")
        self.path = str(path)
        self._reader = loci.formats.ImageReader()
        self._reader.setId(self.path)
        self.n_t = int(self._reader.getSizeT())
        self.n_c = int(self._reader.getSizeC())
        self.n_z = int(self._reader.getSizeZ())
        self.H = int(self._reader.getSizeY())
        self.W = int(self._reader.getSizeX())
        self.src_dtype = _numpy_dtype(self._reader)

    def _read_plane(self, t, c, z):
        """One (Y, X) plane, normalised to [0, 1] float32 — the range the analysis stack expects
        (same contract as ``to_unit_float32`` on every other loader)."""
        _attach_thread()
        idx = int(self._reader.getIndex(int(z), int(c), int(t)))
        buf = self._reader.openBytes(idx)
        arr = np.frombuffer(bytes(buf), dtype=self.src_dtype).reshape(self.H, self.W)
        return to_unit_float32(arr, self.src_dtype)

    def channel_stack(self, channel_idx):
        """A lazy (T, Y, X) view of one channel (z is fixed at 0 — the streaming files are 2-D+T)."""
        return _CziChannelStack(self, int(channel_idx))

    def close(self):
        try:
            if self._reader is not None:
                self._reader.close()
        except Exception as e:
            debug_log("czi_bioformats: reader close failed", e)
        finally:
            self._reader = None


class _CziChannelStack:
    """Lazy (T, Y, X) wrapper over one channel of a ``CziBioFormatsReader``.

    Mirrors ``_TiffPageStack`` / ``_ImsReaderTYX``: shape (T, Y, X), float32 dtype, one plane read per
    slider move (``openBytes`` ≈ 5 ms), and ``__array__`` REFUSES an implicit full-stack read (the
    lazy-guard that stops napari materialising 15,766 frames for a thumbnail)."""

    def __init__(self, reader: CziBioFormatsReader, channel_idx: int):
        self._reader = reader
        self._c = int(channel_idx)
        self.shape = (reader.n_t, reader.H, reader.W)
        self.dtype = np.dtype('float32')
        self.ndim = 3

    def _frame(self, t):
        return self._reader._read_plane(int(t), self._c, 0)

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            t_sel = idx[0] if len(idx) > 0 else slice(None)
            yx = idx[1:] if len(idx) > 1 else (slice(None), slice(None))
        else:
            t_sel, yx = idx, (slice(None), slice(None))
        if isinstance(t_sel, (int, np.integer)):
            return self._frame(t_sel)[yx]
        t_range = range(*t_sel.indices(self.shape[0])) if isinstance(t_sel, slice) \
            else [int(t) for t in t_sel]
        frames = ([self._frame(t)[yx] for t in t_range] if t_range
                  else np.empty((0,) + self.shape[1:], np.float32))
        return np.stack(frames, axis=0) if len(frames) else frames

    def __array__(self, dtype=None):
        """**Refuse.** See ``pycat.file_io.lazy_guard`` — a bare ``np.asarray(layer)`` here would
        pull all 15,766 frames off disk for a thumbnail."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)

    def __len__(self):
        return self.shape[0]


def probe_libczi(path):
    """Open the CZI via libCZI and test whether it can actually READ pixels.

    Returns ``(can_read, image)``. Metadata always reads; only the pixel read distinguishes a normal
    CZI (libCZI is fine, and far cheaper — no JVM) from a streaming one (libCZI raises "not
    implemented"). `can_read` is True iff libCZI reads plane 0.

    **The libCZI image is returned regardless of `can_read`** — its metadata (dims, pixel size,
    channels) is valid even for a streaming file whose pixel reads fail, and the streaming loader
    reuses it so the multi-second libCZI open of a big movie (parsing every subblock offset) is paid
    ONCE, not once here and again in ``_open_czi_streaming``. `image` is None only if the open itself
    failed.
    """
    from pycat.file_io.image_reader import open_image
    image = None
    try:
        image = open_image(path)
        _ = np.asarray(image.get_image_dask_data("YX", T=0, C=0, Z=0).compute())
        return True, image
    except Exception as e:
        debug_log("czi_bioformats: libCZI cannot read pixels (will try BioFormats)", e)
        return False, image


def libczi_can_read(path) -> bool:
    """Back-compat bool wrapper over :func:`probe_libczi` (drops the reused image)."""
    return probe_libczi(path)[0]
