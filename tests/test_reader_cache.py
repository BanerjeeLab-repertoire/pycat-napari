"""
**A single drag-and-drop constructed the reader three to four times.**

    _add_image_or_mask_single   -> open_image()    "is this an image or a mask?"
    _open_image_auto_single     -> open_image()    "is this 2D or a stack?"
      -> _open_stack_generic    -> open_image()
         OR open_2d_image       -> open_image() x3  (probe, fallback check, reload)

**Reader construction is not free.** Depending on the plugin it parses OME-XML, walks the TIFF
series, reads the **CZI subblock directory**, and enumerates scenes — **every time.** For a large
CZI that is the same expensive directory walk, four times over, *before anything is displayed.*

The cache lives in the seam, so **all seven call sites benefit and none had to change.**

Two things it must get right
----------------------------
**A stale reader is worse than a slow one.** It holds an open handle to a file that may have
changed on disk, and would serve the *old* pixels while the user looks at a *new* file — a quiet
wrongness of exactly the kind this project keeps finding. So the key is **path + size + mtime**,
not path alone.

**``kwargs`` bypass the cache.** A caller passing options wants a reader built *their* way. Handing
them a differently-configured one from the cache would be the same quiet wrongness, wearing a
different hat.
"""

import os
import sys
import tempfile
import time
import types

import pytest


def _with_counting_reader():
    """A fake reader that counts how many times it is CONSTRUCTED."""
    calls = {'n': 0}

    module = types.ModuleType('aicsimageio')
    module.__version__ = '4.14.0'

    class _Reader:
        def __init__(self, path, **kwargs):
            calls['n'] += 1
            self.path = path

    module.AICSImage = _Reader
    return module, calls


@pytest.fixture
def counting_reader(monkeypatch):
    module, calls = _with_counting_reader()
    monkeypatch.setitem(sys.modules, 'aicsimageio', module)
    monkeypatch.setenv('PYCAT_IMAGE_READER', 'aicsimageio')

    reader = pytest.importorskip("pycat.file_io.image_reader")
    monkeypatch.setattr(reader, '_BACKEND', 'aicsimageio')
    reader.clear_reader_cache()

    yield reader, calls

    reader.clear_reader_cache()


@pytest.mark.core
def test_the_same_file_is_opened_ONCE_not_four_times(counting_reader):
    """**The whole point.** Four calls, one construction."""
    reader, calls = counting_reader

    handle, path = tempfile.mkstemp(suffix='.tif')
    os.write(handle, b'x' * 100)
    os.close(handle)

    try:
        for _ in range(4):
            reader.open_image(path)

        assert calls['n'] == 1, (
            f"the reader was constructed {calls['n']} times for four opens of the same file. "
            f"A drag-and-drop does this — and for a CZI each construction walks the subblock "
            f"directory."
        )
    finally:
        os.unlink(path)


@pytest.mark.core
def test_a_CHANGED_file_gets_a_FRESH_reader(counting_reader):
    """***A stale reader is worse than a slow one*** — it would serve the OLD pixels.

    The key is **path + size + mtime**, not path alone.
    """
    reader, calls = counting_reader

    handle, path = tempfile.mkstemp(suffix='.tif')
    os.write(handle, b'x' * 100)
    os.close(handle)

    try:
        reader.open_image(path)
        calls['n'] = 0

        time.sleep(0.02)                      # ensure the mtime actually moves
        with open(path, 'wb') as changed:
            changed.write(b'y' * 200)         # different size AND mtime

        reader.open_image(path)

        assert calls['n'] == 1, (
            "the file changed on disk and the CACHED reader was reused. It would serve the old "
            "pixels while the user looks at the new file."
        )
    finally:
        os.unlink(path)


@pytest.mark.core
def test_KWARGS_bypass_the_cache(counting_reader):
    """A caller passing options wants a reader built **their** way.

    Handing them a differently-configured one from the cache is quiet wrongness.
    """
    reader, calls = counting_reader

    handle, path = tempfile.mkstemp(suffix='.tif')
    os.write(handle, b'x' * 100)
    os.close(handle)

    try:
        reader.open_image(path, some_option=True)
        reader.open_image(path, some_option=True)

        assert calls['n'] == 2, (
            "a call with kwargs was served from the cache. The cached reader may have been built "
            "with different options entirely."
        )
    finally:
        os.unlink(path)


@pytest.mark.core
def test_the_cache_is_BOUNDED():
    """**It holds open file handles.** An unbounded session cache would pin every file the user
    has ever opened."""
    reader = pytest.importorskip("pycat.file_io.image_reader")

    assert reader._READER_CACHE_LIMIT <= 8, (
        f"the reader cache holds {reader._READER_CACHE_LIMIT} readers. This is a "
        f"'same file, several times, within one load' cache — not a session cache."
    )


class _StatefulReader:
    """A reader with a **mutable current scene** — as BioIO's is."""

    def __init__(self, path, **kwargs):
        self.path = path
        self.scenes = ['Image:0', 'Image:1', 'Image:2']
        self.current_scene = 'Image:0'

    def set_scene(self, scene):
        self.current_scene = (self.scenes[scene] if isinstance(scene, int) else scene)


@pytest.mark.core
def test_a_CACHED_reader_is_REWOUND_before_it_is_handed_out(monkeypatch):
    """***The cache introduced a correctness bug, and this is it.***

    **A cached reader is SHARED, and ``set_scene()`` mutates it.** Two call sites hold the *same
    object* — so a site that moves to scene 2 leaves the next caller's reader **parked on scene
    2.**

    That caller reads **the wrong field of view**, and ***nothing about the image looks broken.***
    On a multi-position CZI that is a silently wrong analysis.

    *This is exactly the class of quiet wrongness this project keeps finding — and I introduced it
    in 1.6.6, while fixing something else.*
    """
    module = types.ModuleType('aicsimageio')
    module.__version__ = '4.14.0'
    module.AICSImage = _StatefulReader

    monkeypatch.setitem(sys.modules, 'aicsimageio', module)
    monkeypatch.setenv('PYCAT_IMAGE_READER', 'aicsimageio')

    reader = pytest.importorskip("pycat.file_io.image_reader")
    monkeypatch.setattr(reader, '_BACKEND', 'aicsimageio')
    reader.clear_reader_cache()

    handle, path = tempfile.mkstemp(suffix='.tif')
    os.write(handle, b'x' * 100)
    os.close(handle)

    try:
        first = reader.open_image(path)
        first.set_scene(2)
        assert first.current_scene == 'Image:2'

        # A different call site opens "the same file" and has every right to expect a reader in a
        # known state.
        second = reader.open_image(path)

        assert second.current_scene == 'Image:0', (
            f"a cached reader was handed out parked on {second.current_scene!r}. The next caller "
            f"reads the WRONG SCENE, and nothing about the image looks broken."
        )
    finally:
        reader.clear_reader_cache()
        os.unlink(path)
