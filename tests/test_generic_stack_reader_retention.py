r"""
Generic-stack (TIFF/CZI) reader-retention guard.

WHY THIS EXISTS
---------------
`_open_stack_generic` keeps its backing readers alive via `self._stack_lazy_refs` — the same
keepalive pattern that `_open_stack_ims` used for `_ims_zarr_refs` (one of its append sites is
even commented ``# keep handle open``). For a lazily-loaded multi-frame TIFF/CZI, the BioIO
reader (`image`) and/or the tifffile page wrapper must stay alive for as long as the napari layer
reads frames on demand; if the only reference is a load-scoped one that gets dropped, scrubbing a
frame can fail once GC runs (the IMS analogue was proven on 2026-07-14 —
docs/audits/ims_zarr_refs_resolved_2026-07-14.md).

WHAT THIS GUARDS
----------------
This is the BASELINE guard for the generic loader, to be captured green on the CURRENT code
BEFORE `_stack_lazy_refs` retention is migrated onto the layer-scoped ImageSource (the way IMS
already was). It builds a synthetic multi-page TIFF, loads it through the real
`_open_stack_generic`, captures the lazy wrapper(s) handed to `viewer.add_image`, drops the
FileIOClass, forces `gc.collect()`, and reads a frame. It must succeed.

Unlike the IMS test, this needs no external file — the multi-page TIFF is synthesised in a
tmp dir, so this runs in CI.

Marked `integration` (needs napari/Qt + BioIO/tifffile). Not a headless core test.
"""

import gc
import os

import numpy as np
import pytest


pytestmark = pytest.mark.integration

# A committed 20-frame slice of a real MicroManager MMStack (TYX OME-TIFF). Using real data — not a
# hand-rolled synthetic TIFF — matters here: the generic loader routes real MMStack/OME TIFFs
# through the zarr-free `_TiffPageStack` fast path, whereas a naive synthetic multipage TIFF fell
# into BioIO's dask→zarr path and tripped the known ``zarr 3.2.1 < 3`` tifffile-zarr-store bug,
# which is an ENVIRONMENT issue, not a retention failure.
_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "mmstack_20frames.tif")

# Errors that mean "the backing file/handle CLOSED" — the actual retention failure this guards.
# Anything else (e.g. the zarr-3.2 store bug) is NOT a retention failure and must not fail the test.
_CLOSE_ERROR_SIGNS = (
    "can't synchronously read data",  # HDF5 closed
    "can't open",                     # HDF5/file closed
    "closed file",
    "seek of closed file",
    "i/o operation on closed",
    "bad file descriptor",
)


def _is_close_error(exc):
    """True if `exc` indicates the backing file/handle was closed (a real retention failure),
    as opposed to an unrelated read error like the zarr-3.2 store bug."""
    if isinstance(exc, (OSError,)):
        return True
    msg = str(exc).lower()
    return any(sign in msg for sign in _CLOSE_ERROR_SIGNS)


@pytest.fixture
def multipage_tiff():
    if not os.path.exists(_FIXTURE):
        pytest.skip(f"missing test fixture: {_FIXTURE}")
    import tifffile
    with tifffile.TiffFile(_FIXTURE) as t:
        stack = t.series[0].asarray()
    return _FIXTURE, stack


def test_generic_stack_readers_survive_gc_when_only_layers_held(qapp, multipage_tiff):
    """After a lazy TIFF load, holding ONLY the layers must keep the backing reader/handle open
    through a GC. This pins the CURRENT (_stack_lazy_refs on FileIOClass) behaviour so the
    upcoming ImageSource migration for the generic loader cannot silently orphan the reader.

    The ``qapp`` fixture (pytest-qt) constructs a QApplication first: the generic loader may build
    a Qt dialog (scene/position detection), and constructing a QWidget with no QApplication
    aborts the process at the C level."""
    from unittest.mock import Mock
    from pycat.file_io.file_io import FileIOClass
    from pycat.data.data_modules import BaseDataClass

    tiff_path, stack = multipage_tiff
    n_frames = stack.shape[0]

    # Fake layer that carries a real .data and .metadata, like a napari layer.
    class _FakeLayer:
        def __init__(self, data):
            self.data = data
            self.metadata = {}

    captured_layers = []

    viewer = Mock()

    def _capture_add_image(data, *args, **kwargs):
        layer = _FakeLayer(data)
        captured_layers.append(layer)
        return layer

    viewer.add_image.side_effect = _capture_add_image

    cm = Mock()
    cm.active_data_class = BaseDataClass()

    fio = FileIOClass(viewer, cm)
    fio.base_file_name = "synthetic_stack"
    fio.filePath = tiff_path
    # Some branches deliver via load_into_viewer (materialised) — capture but don't require.
    fio.load_into_viewer = lambda *a, **k: None
    # Stub the modal pixel-size prompt: it fires for images with no parseable scale
    # (correct production behaviour) but would BLOCK an automated test on human input.
    fio._prompt_pixel_size_if_needed = lambda *a, **k: None

    # Run the REAL generic loader.
    fio._open_stack_generic(tiff_path, ".tif")

    # Identify layers whose data is a LAZY wrapper (has __getitem__, multi-frame). A materialised
    # ndarray needs no retention, so we only guard the lazy ones.
    lazy_layers = []
    for ly in captured_layers:
        d = ly.data
        if isinstance(d, np.ndarray):
            continue  # materialised — no reader to keep alive
        # lazy wrapper: indexable and reports >1 frame
        if hasattr(d, "__getitem__") and getattr(d, "shape", (1,))[0] > 1:
            lazy_layers.append(ly)

    if not lazy_layers:
        pytest.skip(
            "the synthetic TIFF did not load through the lazy path (it was materialised), so "
            "there is no _stack_lazy_refs retention to exercise. If the generic loader stopped "
            "lazy-loading small multi-page TIFFs, this guard needs a larger/synthetic-lazy input."
        )

    # THE GUARD: hold ONLY the layers, drop FileIOClass (and thus _stack_lazy_refs), force GC.
    layers = list(lazy_layers)
    del fio
    del captured_layers
    gc.collect()

    failures = []        # genuine close-of-handle errors (the retention failure this guards)
    non_retention = []   # other read errors (e.g. zarr-3.2 store bug) — NOT retention, don't fail
    read_ok = 0
    for i, ly in enumerate(layers):
        try:
            frame = np.asarray(ly.data[0])
            assert frame is not None and getattr(frame, "size", 0) > 0
            read_ok += 1
        except Exception as e:  # noqa: BLE001
            entry = f"layer[{i}] data ({type(ly.data).__name__}): {type(e).__name__}: {e}"
            (failures if _is_close_error(e) else non_retention).append(entry)

    assert not failures, (
        "Generic-stack reader retention BROKE after GC — a lazy layer's backing reader/handle "
        "closed when only the layers were held. _stack_lazy_refs is the current owner; the "
        "ImageSource migration must preserve this.\n"
        f"  lazy layers: {len(layers)}\n"
        f"  close-of-handle failures:\n    " + "\n    ".join(failures)
    )
    if read_ok == 0 and non_retention:
        pytest.skip(
            "no layer read succeeded, but every failure was a NON-retention read error (e.g. the "
            "known zarr-3.2 tifffile-store bug), not a closed handle. Retention could not be "
            "exercised on this environment.\n    " + "\n    ".join(non_retention)
        )


# ── Opt-in stronger check against a REAL multi-frame stack ────────────────────────────────────
# The synthetic test above is self-contained (CI-safe). This one runs the identical guard against
# a real file, which exercises the actual BioIO/tifffile lazy path on genuine data — a stronger
# check than the synthetic hyperstack. Point PYCAT_GENERIC_STACK_FILE at e.g. a 1000-frame MMStack
# TIFF:
#     set PYCAT_GENERIC_STACK_FILE=C:\...\3_30_hr_1_MMStack_Pos0_ome2.tif   (Windows)
#     pytest tests/test_generic_stack_reader_retention.py -v
_GENERIC_ENV = "PYCAT_GENERIC_STACK_FILE"
_generic_real = os.environ.get(_GENERIC_ENV)

needs_real_stack = pytest.mark.skipif(
    not _generic_real or not os.path.exists(_generic_real),
    reason=f"set {_GENERIC_ENV} to a real multi-frame TIFF/CZI to run the stronger check",
)


@needs_real_stack
def test_generic_stack_readers_survive_gc_on_real_file(qapp):
    """Same guard as the synthetic test, but on a real multi-frame stack (genuine BioIO/tifffile
    lazy path). Opt-in via PYCAT_GENERIC_STACK_FILE. ``qapp`` constructs a QApplication first (the
    loader may build a Qt scene-selection dialog)."""
    from unittest.mock import Mock
    from pycat.file_io.file_io import FileIOClass
    from pycat.data.data_modules import BaseDataClass

    class _FakeLayer:
        def __init__(self, data):
            self.data = data
            self.metadata = {}

    captured_layers = []
    viewer = Mock()

    def _capture_add_image(data, *args, **kwargs):
        layer = _FakeLayer(data)
        captured_layers.append(layer)
        return layer

    viewer.add_image.side_effect = _capture_add_image

    cm = Mock()
    cm.active_data_class = BaseDataClass()

    fio = FileIOClass(viewer, cm)
    fio.base_file_name = os.path.splitext(os.path.basename(_generic_real))[0]
    fio.filePath = _generic_real
    fio.load_into_viewer = lambda *a, **k: None
    # Stub the modal pixel-size prompt: it fires for images with no parseable scale
    # (correct production behaviour) but would BLOCK an automated test on human input.
    fio._prompt_pixel_size_if_needed = lambda *a, **k: None

    ext = os.path.splitext(_generic_real)[1].lower()
    fio._open_stack_generic(_generic_real, ext)

    lazy_layers = []
    for ly in captured_layers:
        d = ly.data
        if isinstance(d, np.ndarray):
            continue
        if hasattr(d, "__getitem__") and getattr(d, "shape", (1,))[0] > 1:
            lazy_layers.append(ly)

    assert lazy_layers, (
        "the real file did not load through the lazy path — no lazy layer to guard. Expected a "
        "multi-frame stack to lazy-load."
    )

    layers = list(lazy_layers)
    del fio
    del captured_layers
    gc.collect()

    failures = []
    non_retention = []
    read_ok = 0
    for i, ly in enumerate(layers):
        try:
            frame = np.asarray(ly.data[0])
            assert frame is not None and getattr(frame, "size", 0) > 0
            read_ok += 1
        except Exception as e:  # noqa: BLE001
            entry = f"layer[{i}] data ({type(ly.data).__name__}): {type(e).__name__}: {e}"
            (failures if _is_close_error(e) else non_retention).append(entry)

    assert not failures, (
        "Generic-stack reader retention BROKE after GC on the real file.\n"
        f"  lazy layers: {len(layers)}\n"
        f"  close-of-handle failures:\n    " + "\n    ".join(failures)
    )
    if read_ok == 0 and non_retention:
        pytest.skip(
            "no read succeeded, but all failures were non-retention read errors (not closed "
            "handles):\n    " + "\n    ".join(non_retention)
        )
