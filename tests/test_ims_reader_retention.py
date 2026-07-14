r"""
Multi-position IMS reader-retention guard.

WHY THIS EXISTS
---------------
`_open_stack_ims` keeps every per-position IMS reader alive via `self._ims_zarr_refs`. For a
multi-position file each sibling `pos_reader` is a *separately opened* HDF5 file, and that list is
the ONLY thing owning it (the primary reader has `self._ims_reader` as a backstop; siblings have
nothing else). This was proven load-bearing on 2026-07-14: dropping the reference and forcing GC
made a frame read raise `OSError: Can't synchronously read data` because the HDF5 handle closed.
See docs/audits/ims_zarr_refs_resolved_2026-07-14.md.

WHAT THIS GUARDS
----------------
The upcoming `ImageSource` refactor moves reader ownership out of `FileIOClass`. If it re-orphans
the sibling readers, scrubbing a non-primary position crashes *intermittently* (GC-timing
dependent) — the worst possible bug shape. This test pins the CURRENT (passing) behaviour so any
refactor that breaks retention fails loudly here instead of in a user's session.

It captures the lazy wrappers handed to `viewer.add_image` (what napari would hold), drops the
`FileIOClass` and every other reference, forces `gc.collect()`, and reads a frame from a wrapper.
It must succeed.

RUNNING
-------
Requires a real multi-position .ims file. Point PYCAT_IMS_MULTIPOS_FILE at one, or the test skips:

    set PYCAT_IMS_MULTIPOS_FILE=C:\path\to\a_multiposition.ims   (Windows)
    export PYCAT_IMS_MULTIPOS_FILE=/path/to/a_multiposition.ims  (POSIX)
    pytest tests/test_ims_reader_retention.py -v

Marked `integration` (needs Qt/napari + the IMS reader). Not a headless core test.
"""

import gc
import os

import numpy as np
import pytest


IMS_ENV = "PYCAT_IMS_MULTIPOS_FILE"
_ims_path = os.environ.get(IMS_ENV)

pytestmark = pytest.mark.integration


needs_ims = pytest.mark.skipif(
    not _ims_path or not os.path.exists(_ims_path),
    reason=f"set {IMS_ENV} to a real multi-position .ims file to run the retention guard",
)


@needs_ims
def test_multiposition_ims_readers_survive_gc_when_only_wrappers_held():
    """After load, holding ONLY the lazy wrappers (as napari does) must keep every IMS
    file open through a GC — including sibling-position readers owned only by _ims_zarr_refs."""
    from unittest.mock import Mock
    from pycat.file_io.file_io import FileIOClass
    from pycat.data.data_modules import BaseDataClass

    # A mock viewer that captures every lazy object handed to add_image — these are the
    # wrappers napari would retain as layer.data.
    captured_wrappers = []

    viewer = Mock()

    def _capture_add_image(data, *args, **kwargs):
        captured_wrappers.append(data)
        return Mock()  # a stand-in layer

    viewer.add_image.side_effect = _capture_add_image

    cm = Mock()
    cm.active_data_class = BaseDataClass()

    fio = FileIOClass(viewer, cm)

    # load_into_viewer also delivers frames for some branches; capture those wrappers too.
    _orig_load = fio.load_into_viewer

    def _capturing_load(data, *a, **k):
        captured_wrappers.append(data)
        # do not actually touch napari internals; the retention question is about `data`
        return None

    fio.load_into_viewer = _capturing_load

    # Run the REAL loader.
    fio._open_stack_ims(_ims_path)

    assert captured_wrappers, (
        "no lazy wrappers were captured — the loader delivered no layers, so this test cannot "
        "exercise retention. Check the file is genuinely multi-position and loads."
    )

    # Snapshot how many sibling readers the current code retained, for the failure message.
    n_refs = len(getattr(fio, "_ims_zarr_refs", []) or [])

    # THE GUARD: simulate a world where FileIOClass is gone and ONLY the layers survive.
    # Keep the wrappers; drop everything else; force collection.
    wrappers = list(captured_wrappers)
    del fio
    del captured_wrappers
    gc.collect()

    # Every wrapper must still read frame 0. A closed sibling reader raises OSError here.
    failures = []
    for i, w in enumerate(wrappers):
        try:
            frame = np.asarray(w[0])
            assert frame is not None and getattr(frame, "size", 0) > 0
        except Exception as e:  # noqa: BLE001 - we want to report ANY failure with context
            failures.append(f"wrapper[{i}] ({type(w).__name__}): {type(e).__name__}: {e}")

    assert not failures, (
        "IMS reader retention BROKE after GC — a wrapper's underlying file closed when only the "
        f"layers were held. This is the multi-position sibling-reader orphaning bug.\n"
        f"  sibling readers retained by _ims_zarr_refs at load: {n_refs}\n"
        f"  failing reads:\n    " + "\n    ".join(failures)
    )


@needs_ims
def test_multiposition_ims_readers_survive_gc_via_layer_imagesource():
    """The ImageSource path: readers must survive GC when ONLY the layers are held (not the
    FileIOClass), because each layer's metadata['pycat_image_source'] owns its readers.

    This is the NEW-design counterpart to the test above. The test above proves the LEGACY
    retention (_ims_zarr_refs on FileIOClass). This one proves the layer-scoped ImageSource
    retention that will replace it. Both must pass while the two paths run in parallel; once the
    legacy path is removed, this one is the guard.
    """
    from unittest.mock import Mock
    from pycat.file_io.file_io import FileIOClass
    from pycat.file_io.image_source import ImageSource
    from pycat.data.data_modules import BaseDataClass

    # A fake layer that carries a REAL metadata dict AND holds its data, like a napari layer.
    class _FakeLayer:
        def __init__(self, data):
            self.data = data
            self.metadata = {}

    captured_layers = []

    viewer = Mock()

    def _capture_add_image(data, *args, **kwargs):
        layer = _FakeLayer(data)
        captured_layers.append(layer)
        return layer  # loader stashes ImageSource into layer.metadata

    viewer.add_image.side_effect = _capture_add_image

    cm = Mock()
    cm.active_data_class = BaseDataClass()

    fio = FileIOClass(viewer, cm)
    # load_into_viewer path (single-2D-frame) materialises; no reader retention needed there.
    fio.load_into_viewer = lambda *a, **k: None

    fio._open_stack_ims(_ims_path)

    lazy_layers = [ly for ly in captured_layers
                   if ly.metadata.get('pycat_image_source') is not None]
    assert lazy_layers, (
        "no layer carried a pycat_image_source in metadata — either the file produced only "
        "materialised 2D frames, or the ImageSource wire-in did not attach. For a multi-position "
        "lazy stack this must be non-empty."
    )

    # Confirm the ImageSource actually retained readers (primary + siblings).
    src = lazy_layers[0].metadata['pycat_image_source']
    assert isinstance(src, ImageSource) and len(src) >= 1, (
        f"ImageSource retained {len(src) if isinstance(src, ImageSource) else 'N/A'} readers; "
        "expected at least the primary reader."
    )

    # THE GUARD: hold ONLY the layers (their metadata transitively holds the ImageSource, which
    # holds the readers). Drop the FileIOClass and everything else, force GC, read a frame.
    layers = list(lazy_layers)
    del fio
    del captured_layers
    gc.collect()

    failures = []
    for i, ly in enumerate(layers):
        try:
            frame = np.asarray(ly.data[0])
            assert frame is not None and getattr(frame, "size", 0) > 0
        except Exception as e:  # noqa: BLE001
            failures.append(f"layer[{i}] data ({type(ly.data).__name__}): {type(e).__name__}: {e}")

    assert not failures, (
        "ImageSource retention BROKE after GC — a lazy layer's file closed when only the layers "
        "were held. The layer.metadata['pycat_image_source'] path is not keeping readers alive.\n"
        f"  readers in ImageSource: {len(src)}\n"
        f"  failing reads:\n    " + "\n    ".join(failures)
    )
