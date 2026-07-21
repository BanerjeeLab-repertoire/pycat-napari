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

import os
import threading
import time as _time
from collections import OrderedDict

import numpy as np

from pycat.file_io.stack_access import to_unit_float32
from pycat.utils.general_utils import debug_log

# Set PYCAT_CZI_TRACE=1 to print, per ~24 foreground plane reads, the cache hit-rate and the actual
# read latency the viewer experiences — the ground truth for whether the cache/prefetch is helping.
_TRACE = bool(os.environ.get("PYCAT_CZI_TRACE"))

# The OME artifactory (for the ``woolz`` transitive jar) and the Java BioFormats version that can
# actually read the streaming CZI (6.7.0, which bioio-bioformats 1.3.2 pins, cannot).
_OME_MAVEN = "https://artifacts.openmicroscopy.org/artifactory/maven"
_BIOFORMATS_ENDPOINT = "ome:formats-gpl:8.1.1"

_JVM_STARTED = False
_EXIT_HOOK_INSTALLED = False


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
        # ── Headless, so the process can EXIT ──────────────────────────────────────────────
        #
        # Reading a CZI can make BioFormats touch Java AWT (colour models / thumbnails), which spawns
        # a NON-daemon AWT event thread. That thread keeps the JVM — and the whole Python process —
        # alive at shutdown: PyCAT's window closes but the terminal never returns (only after a CZI
        # was opened). A plain script exits fine because it never triggers AWT the way the Qt app
        # does. `-Djava.awt.headless=true` stops the AWT thread ever starting; BioFormats reads pixels
        # and metadata without it.
        try:
            scyjava.config.enable_headless_mode()
        except Exception as _e:
            debug_log("czi_bioformats: could not enable JVM headless mode", _e)
        # woolz lives only in OME's artifactory; formats-gpl:8.1.1 is the version that reads the file.
        scyjava.config.add_repositories({'ome': _OME_MAVEN})
        if _BIOFORMATS_ENDPOINT not in scyjava.config.endpoints:
            scyjava.config.endpoints.append(_BIOFORMATS_ENDPOINT)
        scyjava.start_jvm()
    _JVM_STARTED = True
    _install_forced_exit_on_quit()


def _install_forced_exit_on_quit():
    """Guarantee the process TERMINATES when PyCAT closes after a CZI opened the JVM.

    Even headless, the BioFormats JVM leaves non-daemon Java threads that survive shutdown inside the
    napari/Qt process: ``napari.run()`` returns when the window closes, but Python then hangs joining
    those threads — the terminal never comes back (a plain script exits fine, so it only bites in the
    GUI). An ``atexit`` handler runs on the main thread right BEFORE that join, so ``os._exit(0)`` there
    terminates past the hang; it is the reliable path (Qt's ``aboutToQuit`` is a best-effort earlier
    trigger, and it may not fire — this is installed from the CZI-open WORKER thread). The only atexit
    it pre-empts is the welcome-logo temp-file cleanup — harmless. Only ever armed in a CZI session.
    """
    global _EXIT_HOOK_INSTALLED
    if _EXIT_HOOK_INSTALLED:
        return
    _EXIT_HOOK_INSTALLED = True

    def _force_exit():
        import os, sys
        try:
            print("[PyCAT CZI] BioFormats JVM was open — forcing a clean process exit so the "
                  "terminal returns.", flush=True)
            sys.stdout.flush(); sys.stderr.flush()
        except Exception:
            pass
        os._exit(0)

    import atexit
    atexit.register(_force_exit)                 # the guarantee: runs before Python joins JVM threads
    try:
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(_force_exit)  # best-effort earlier trigger
    except Exception:
        pass


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

    ── LRU cache + read-ahead so scrubbing is smooth ──────────────────────────────────────────
    A plane is ~5 ms to ``openBytes``, which is visible as an intermittent stall when scrubbing a long
    movie frame by frame. So planes are cached (a byte-budgeted LRU), and a single background thread
    reads AHEAD of the frame last accessed — a forward scrub then lands on already-decoded planes.
    Every read (foreground and prefetch) is serialised on one lock, because a loci ``ImageReader`` is
    NOT safe for concurrent ``openBytes``; the lock is held per plane (~5 ms), so a foreground miss
    never waits long behind the prefetcher.
    """

    _CACHE_BYTES = 256 * 1024 * 1024     # LRU budget: 256 planes at 500², 16 at 2048² — bounded either way
    _PREFETCH_AHEAD = 8                  # frames to read ahead of the current one on a forward scrub

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
        self._init_cache()

    def _init_cache(self):
        """Set up the LRU cache + prefetch thread. Separate from ``__init__`` so the cache/prefetch
        logic is testable without a JVM (construct the reader, set n_t/H/W/src_dtype, call this)."""
        # `_read_lock` serialises openBytes; `_cache_lock` guards the LRU dict.
        self._read_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._cache = OrderedDict()                          # (t, c) -> float32 (Y, X) plane
        self._cache_max = max(4, int(self._CACHE_BYTES / (self.H * self.W * 4)))
        self._closed = False
        self._target_cv = threading.Condition()
        # Prefetch coordination. The FOREGROUND read publishes its request (target + a monotonic
        # generation) BEFORE it reads, and raises `_fg_pending` while it reads, so the prefetcher can
        # (a) never obstruct the frame the user is waiting on and (b) abandon obsolete read-ahead the
        # instant the user moves. `_last_t` tracks direction so read-ahead follows the scrub.
        self._target = None                                  # ((t, c), generation, offsets) or None
        self._gen = 0
        self._fg_pending = False
        self._last_t = None
        self._tr_lat, self._tr_hits = [], 0                  # PYCAT_CZI_TRACE accumulators
        self._tr_lockwait, self._tr_openbytes = [], []       # per-stage (all reads, fg + prefetch)
        self._prefetch = threading.Thread(target=self._prefetch_loop, daemon=True)
        self._prefetch.start()

    # ── cache ────────────────────────────────────────────────────────────────
    def _cache_get(self, key):
        with self._cache_lock:
            plane = self._cache.get(key)
            if plane is not None:
                self._cache.move_to_end(key)
            return plane

    def _cache_put(self, key, plane):
        with self._cache_lock:
            self._cache[key] = plane
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)              # evict least-recently-used

    def _read_plane_raw(self, t, c, z):
        """The actual JVM read, serialised. One (Y, X) plane, normalised to [0, 1] float32 (same
        contract as ``to_unit_float32`` on every other loader)."""
        _lw0 = _time.perf_counter() if _TRACE else 0.0
        with self._read_lock:
            if _TRACE:
                self._tr_lockwait.append((_time.perf_counter() - _lw0) * 1000.0)
            if self._closed or self._reader is None:
                raise RuntimeError("CZI reader is closed")
            _attach_thread()
            idx = int(self._reader.getIndex(int(z), int(c), int(t)))
            _ob0 = _time.perf_counter() if _TRACE else 0.0
            buf = self._reader.openBytes(idx)
            if _TRACE:
                self._tr_openbytes.append((_time.perf_counter() - _ob0) * 1000.0)
            # Guard the buffer LAYOUT, not just the reshape: a reshape only fails on a gross size
            # mismatch, but a wrong series / RGB / interleaved / tiled plane can be the wrong size in a
            # way that still reshapes to a shifted image. Fail loudly with the reader's layout instead.
            expected = self.H * self.W * self.src_dtype.itemsize
            if len(buf) != expected:
                raise RuntimeError(
                    f"BioFormats plane size mismatch: got {len(buf)} bytes, expected {expected} for "
                    f"{self.W}x{self.H} {self.src_dtype} — series={int(self._reader.getSeries())}, "
                    f"rgb={bool(self._reader.isRGB())}, interleaved={bool(self._reader.isInterleaved())}")
            arr = np.frombuffer(bytes(buf), dtype=self.src_dtype).reshape(self.H, self.W)
            return to_unit_float32(arr, self.src_dtype)

    _PREFETCH_JUMP = 16          # a step larger than this is a JUMP, not a scrub — don't speculate far

    def _prefetch_offsets(self, t):
        """Which neighbours to read ahead, from the scrub DIRECTION (the audit's key point — a
        forward-only prefetcher is useless for a backward or back-and-forth scrub)."""
        prev, self._last_t = self._last_t, t
        n = self._PREFETCH_AHEAD
        if prev is None or prev == t:
            return tuple(o for d in range(1, n // 2 + 2) for o in (d, -d))     # symmetric neighbourhood
        delta = t - prev
        if abs(delta) > self._PREFETCH_JUMP:
            return (1, -1, 2, -2)                                              # a jump — stay shallow
        step = 1 if delta > 0 else -1
        # steady scrub: read AHEAD in the travel direction, plus two behind to survive a reversal
        return tuple([step * d for d in range(1, n + 1)] + [-step, -step * 2])

    def _read_plane(self, t, c, z):
        """Cached (Y, X) plane read. Publishes the request to the prefetcher BEFORE reading and holds
        `_fg_pending` while reading, so the background thread never obstructs the frame being waited on
        and abandons obsolete read-ahead the moment the user moves."""
        _t0 = _time.perf_counter() if _TRACE else 0.0
        key = (int(t), int(c))
        with self._target_cv:
            self._gen += 1
            self._fg_pending = True
            self._target = (key, self._gen, self._prefetch_offsets(key[0]))
            self._target_cv.notify()
        try:
            plane = self._cache_get(key)
            hit = plane is not None
            if plane is None:
                plane = self._read_plane_raw(t, c, z)
                self._cache_put(key, plane)
            return plane
        finally:
            with self._target_cv:
                self._fg_pending = False
                self._target_cv.notify()
            if _TRACE:
                self._trace_read(hit, (_time.perf_counter() - _t0) * 1000.0)

    def _trace_read(self, hit, ms):
        self._tr_lat.append(ms)
        self._tr_hits += int(hit)
        if len(self._tr_lat) >= 24:
            lat = sorted(self._tr_lat)
            n = len(lat)
            lw = max(self._tr_lockwait) if self._tr_lockwait else 0.0
            ob = max(self._tr_openbytes) if self._tr_openbytes else 0.0
            print(f"[PyCAT CZI trace] {n} reads  hits {self._tr_hits}/{n} "
                  f"({100*self._tr_hits/n:.0f}%)  total ms: median {lat[n//2]:.1f} "
                  f"p90 {lat[int(n*0.9)]:.1f} max {lat[-1]:.1f}  |  worst lock-wait {lw:.0f} "
                  f"worst openBytes {ob:.0f}  cache {len(self._cache)}", flush=True)
            self._tr_lat, self._tr_hits = [], 0
            self._tr_lockwait, self._tr_openbytes = [], []

    @staticmethod
    def _detach_jvm():
        """Detach THIS thread from the JVM. A JNI thread that attached (via ``openBytes``) and never
        detaches blocks ``DestroyJavaVM`` — so an idle or finished prefetcher would HANG the whole
        process at exit. Idempotent / harmless if never attached."""
        try:
            import jpype
            if jpype.isJVMStarted():
                jpype.detachThreadFromJVM()
        except Exception:
            pass

    def _prefetch_loop(self):
        """Read AHEAD of the last-accessed frame into the cache; bail the moment the user moves, so no
        effort is spent on frames they have already scrubbed past.

        The thread DETACHES from the JVM whenever it goes idle (after each read-ahead pass), so it is
        never holding a JVM attachment while blocked on the condition — otherwise the process would
        hang at exit if the reader was never closed (a still-attached thread blocks JVM shutdown even
        as a Python daemon)."""
        try:
            while not self._closed:
                with self._target_cv:
                    # Wait for a target AND for the foreground read to FINISH — claiming it mid-read
                    # would only make us bail on `_fg_pending` and lose it (starvation).
                    while (self._target is None or self._fg_pending) and not self._closed:
                        self._target_cv.wait()
                    if self._closed:
                        return
                    (t, c), gen, offsets = self._target
                    self._target = None                      # one read-ahead pass per access
                for off in offsets:
                    with self._target_cv:
                        # Yield to the foreground: never start a read while the UI is waiting on one,
                        # and drop this whole pass the moment a newer request arrives (`gen` bumped).
                        if self._closed or self._fg_pending or gen != self._gen:
                            break
                    nt = t + off
                    if not (0 <= nt < self.n_t) or self._cache_get((nt, c)) is not None:
                        continue
                    try:
                        self._cache_put((nt, c), self._read_plane_raw(nt, c, 0))
                    except Exception as e:
                        debug_log("czi_bioformats: prefetch read failed", e)
                        break
                self._detach_jvm()                           # idle again — hold no JVM attachment
        finally:
            self._detach_jvm()

    def channel_stack(self, channel_idx):
        """A lazy (T, Y, X) view of one channel (z is fixed at 0 — the streaming files are 2-D+T)."""
        return _CziChannelStack(self, int(channel_idx))

    def close(self):
        self._closed = True
        with self._target_cv:
            self._target_cv.notify_all()                     # wake the prefetcher so it can exit
        try:
            with self._read_lock:
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
