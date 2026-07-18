"""**A session's source image loads LAZILY — a 5.79 GiB stack must not OOM the load.**

Reported from the viewer: loading a session reported "0 layers", and the terminal showed
``Unable to allocate 5.79 GiB for an array with shape (1000, 1080, 1440) float32``. The source is a
long time-series, and `_load_source_image_into_viewer` was reading it whole with `tifffile.imread`
because its lazy path (`open_image_auto`) was unreachable — it looked for `file_io` on
`data_instance.central_manager`, which the loaded `BaseDataClass` does not carry. `file_io` is now
passed in by the caller, so the frame-by-frame lazy opener is used.

These pin the wiring headlessly: given a `file_io`, the lazy opener is called and the eager read is
NOT; without one, it degrades to a memory-mapped read before ever attempting a full allocation. What
still needs a viewer: that the lazy layer actually displays and scrubs.
"""

# Standard library imports

# Third party imports
import pytest

from pycat.file_io import session_loader as sl

pytestmark = pytest.mark.core


class _FileIO:
    def __init__(self):
        self.opened = []

    def open_image_auto(self, file_path=None, clear_first=True):
        self.opened.append(file_path)


class _Viewer:
    def __init__(self):
        self.added = []

    def add_image(self, arr, **kw):
        self.added.append(kw.get('name'))


def test_the_LAZY_opener_is_used_when_file_io_is_given(monkeypatch):
    """The real path: `open_image_auto` (frame-by-frame) is called, and `tifffile.imread` — the
    eager read that OOM'd — is NEVER reached."""
    import tifffile
    monkeypatch.setattr(tifffile, 'imread',
                        lambda *a, **k: pytest.fail('eager tifffile.imread was called'))

    fio = _FileIO()
    ok = sl._load_source_image_into_viewer('/some/stack.ome.tif', _Viewer(), None, file_io=fio)
    assert ok is True
    assert fio.opened == ['/some/stack.ome.tif']       # the lazy opener took it


def test_without_file_io_it_MEMMAPS_before_ever_reading_eagerly(monkeypatch):
    """The fallback is lazy too: a memory-mapped read (no full allocation) is tried before the eager
    read that could exhaust RAM."""
    import tifffile
    calls = []
    monkeypatch.setattr(tifffile, 'memmap',
                        lambda p: calls.append('memmap') or __import__('numpy').zeros((2, 2)))
    monkeypatch.setattr(tifffile, 'imread',
                        lambda *a, **k: pytest.fail('imread was reached before memmap succeeded'))

    v = _Viewer()
    ok = sl._load_source_image_into_viewer('/s.tif', v, None, file_io=None)
    assert ok is True and calls == ['memmap']
    assert v.added == ['s.tif']


def test_the_eager_read_is_only_the_LAST_resort(monkeypatch):
    """If both the lazy opener and memmap are unavailable, the eager read is the honest final attempt
    — reached only after the others fail, not before them."""
    import tifffile
    order = []
    monkeypatch.setattr(tifffile, 'memmap',
                        lambda p: order.append('memmap') or (_ for _ in ()).throw(ValueError('no')))
    monkeypatch.setattr(tifffile, 'imread',
                        lambda *a, **k: order.append('imread') or __import__('numpy').zeros((2, 2)))

    ok = sl._load_source_image_into_viewer('/s.tif', _Viewer(), None, file_io=None)
    assert ok is True
    assert order == ['memmap', 'imread']               # memmap first, imread only after it failed


def test_load_session_passes_the_managers_file_io(monkeypatch):
    """The wiring the caller relies on: `central_manager.file_io` reaches the source-image loader."""
    import inspect
    sig = inspect.signature(sl.load_session)
    assert 'central_manager' in sig.parameters, (
        'load_session no longer accepts central_manager — the lazy source-image path is unreachable '
        'again and a large stack will OOM the load'
    )
