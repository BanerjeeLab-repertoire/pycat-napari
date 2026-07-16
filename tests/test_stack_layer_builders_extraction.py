"""The extracted per-branch lazy-wrapper builders must produce the same wrapper + retention the
inline ``_open_stack_generic`` branches did (god-class decomposition #5b).

Headless, with fakes — the builders are Qt/napari-free and return ``(wrapper, retain_refs, warnings)``
without touching the viewer. Covers the tifffile-fallback, time-series (CZI dask + TIFF page paths),
z-stack and T-Z branches, plus the zarr-3.2 error translation.
"""

import numpy as np
import pytest

pytestmark = pytest.mark.core

from pycat.file_io.readers.stack_layer_builders import (
    build_tifffile_fallback_wrapper, build_timeseries_wrapper,
    build_zstack_wrapper, build_tzstack_wrapper)


class _FakeDask:
    def __init__(self, tag):
        self.tag = tag


class _FakeLazySource:
    """Stand-in for _LazyArraySource: records exactly what it was handed to wrap."""
    def __init__(self, data):
        self.data = data


class _FakeImage:
    def __init__(self):
        self.calls = []

    def get_image_dask_data(self, axes, C=None):
        self.calls.append((axes, C))
        return _FakeDask((axes, C))


def test_tifffile_fallback_wraps_float32_and_retains_it():
    arr = np.ones((5, 4, 3), dtype=np.uint16)
    w, refs, warns = build_tifffile_fallback_wrapper(arr, lazy_array_source_cls=_FakeLazySource)
    assert isinstance(w, _FakeLazySource)
    assert w.data.dtype == np.float32 and w.data.shape == (5, 4, 3)
    assert refs == [w.data]          # the float32 array is retained
    assert warns == []


def test_timeseries_czi_dask_path():
    img = _FakeImage()
    w, refs, warns = build_timeseries_wrapper(
        'f.czi', '.czi', img, channel_idx=2, n_t=10, n_c=3, H=64, W=48,
        tiff_page_stack_cls=None, lazy_array_source_cls=_FakeLazySource)
    assert isinstance(w, _FakeLazySource)
    assert w.data.tag == ('TYX', 2)
    assert refs == [(img, w.data)]   # reader + dask retained together
    assert warns == []


def test_timeseries_tiff_uses_page_reader_and_retains_wrapper(monkeypatch):
    import tifffile

    class _Page:
        dtype = np.dtype('uint16')

    class _FakeTiff:
        def __enter__(self):
            self.pages = [_Page()]
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(tifffile, 'TiffFile', lambda p: _FakeTiff())

    class _FakeStack:
        def __init__(self, path, n, H, W, dtype, channel_idx=0, n_channels=1):
            self.shape = (n, H, W)
            self._present_info = None
            self._pages = list(range(n * n_channels))
            self.ctor = (n, H, W, channel_idx, n_channels)

        def close(self):
            pass

    img = _FakeImage()
    w, refs, warns = build_timeseries_wrapper(
        'f.tif', '.tif', img, channel_idx=0, n_t=3, n_c=1, H=64, W=48,
        tiff_page_stack_cls=_FakeStack, lazy_array_source_cls=_FakeLazySource)
    assert isinstance(w, _FakeStack)          # tifffile fast path, not the dask wrapper
    assert w.ctor == (3, 64, 48, 0, 1)
    assert refs == [w]                        # the open tifffile wrapper is retained
    assert img.calls == []                    # the reader's dask path was NOT touched for a TIFF


def test_zstack_czi_retains_reader_and_dask():
    img = _FakeImage()
    w, refs, warns = build_zstack_wrapper('f.czi', '.czi', img, 1,
                                          lazy_array_source_cls=_FakeLazySource)
    assert w.data.tag == ('ZYX', 1)
    assert refs == [(img, w.data)]


def test_tzstack_czi_retains_reader_and_dask():
    img = _FakeImage()
    w, refs, warns = build_tzstack_wrapper('f.czi', '.czi', img, 0,
                                           lazy_array_source_cls=_FakeLazySource)
    assert w.data.tag == ('TZYX', 0)
    # T-Z retains the reader like the z-stack branch (item-1 fix; the lazy dask needs the reader
    # kept alive, which the pre-migration branch failed to do).
    assert refs == [(img, w.data)]


@pytest.mark.parametrize("builder,ext", [
    (build_zstack_wrapper, '.tif'),
    (build_tzstack_wrapper, '.tif'),
])
def test_zarr32_error_is_translated_for_tiff(builder, ext):
    class _RaiseImage:
        def get_image_dask_data(self, axes, C=None):
            raise ValueError("zarr 3.2.1 < 3 is not supported")

    with pytest.raises(RuntimeError, match="misleading"):
        builder('f.tif', ext, _RaiseImage(), 0, lazy_array_source_cls=_FakeLazySource)
