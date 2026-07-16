"""The extracted metadata head (`read_stack_structure`) must return the same reader/dims/pixel-size
decision the inline `_open_stack_generic` head produced (god-class decomposition #5a).

Headless, with fakes — no napari, no Qt, no real files. Covers the structured-reader path (dims +
scenes + pixel size, incl. the TIFF-tag pixel-size fallback) and the tifffile-page fallback path
(structured reader raises → lazy page wrapper + frame count).
"""

import numpy as np
import pytest

pytestmark = pytest.mark.core

from pycat.file_io.readers.stack_metadata import read_stack_structure, StackStructure


class _FakePx:
    def __init__(self, Y):
        self.Y = Y


class _FakeImage:
    def __init__(self, scenes, py):
        self.scenes = list(scenes)
        self.current_scene = self.scenes[0] if self.scenes else None
        self.physical_pixel_sizes = _FakePx(py)


def test_structured_reader_returns_handle_scenes_and_pixel_size():
    img = _FakeImage(scenes=['S0', 'S1', 'S2'], py=0.065)
    s = read_stack_structure(
        'f.czi', '.czi',
        tiff_page_stack_cls=None,
        tiff_pixel_size_um=lambda p: pytest.fail("should not read TIFF tags when reader has px"),
        open_image=lambda p: img)
    assert isinstance(s, StackStructure)
    assert s.reader_has_structure is True
    assert s.image is img
    assert s.scenes == ['S0', 'S1', 'S2']
    assert abs(s.microns_per_pixel - 0.065) < 1e-12
    assert s.fallback_array is None


def test_pixel_size_falls_back_to_tiff_tags_when_reader_is_silent():
    # Reader reports no pixel size (Y is None) -> microns stays 1.0 -> read the TIFF tags.
    img = _FakeImage(scenes=[], py=None)
    s = read_stack_structure(
        'f.tif', '.tif',
        tiff_page_stack_cls=None,
        tiff_pixel_size_um=lambda p: 0.108,
        open_image=lambda p: img)
    assert s.reader_has_structure is True
    assert s.scenes == []
    assert abs(s.microns_per_pixel - 0.108) < 1e-12


def test_reader_pixel_size_wins_over_tags():
    img = _FakeImage(scenes=[], py=0.05)
    called = {'tags': False}

    def _tags(p):
        called['tags'] = True
        return 0.108

    s = read_stack_structure('f.tif', '.tif', tiff_page_stack_cls=None,
                             tiff_pixel_size_um=_tags, open_image=lambda p: img)
    assert abs(s.microns_per_pixel - 0.05) < 1e-12
    assert called['tags'] is False   # a real reader pixel size short-circuits the tag read


class _FakeStack:
    """Stand-in for _TiffPageStack: read_stack_structure only reads .ndim / .shape off it."""
    def __init__(self, path, n, H, W, dtype, channel_idx=0, n_channels=1):
        self.ndim = 3
        self.shape = (n, H, W)
        self.args = (path, n, H, W, dtype, channel_idx, n_channels)


def test_fallback_to_lazy_tifffile_pages_when_reader_raises(monkeypatch):
    import tifffile

    class _Page:
        shape = (64, 48)
        dtype = np.dtype('uint16')

    class _FakeTiff:
        def __enter__(self):
            self.pages = [_Page(), _Page(), _Page()]   # 3 pages
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(tifffile, 'TiffFile', lambda p: _FakeTiff())

    def _boom(p):
        raise RuntimeError("no reader for this file")

    s = read_stack_structure(
        'f.tif', '.tif',
        tiff_page_stack_cls=_FakeStack,
        tiff_pixel_size_um=lambda p: 0.1,
        open_image=_boom)

    assert s.reader_has_structure is False
    assert s.image is None and s.scenes == []
    assert isinstance(s.fallback_array, _FakeStack)
    assert s.fallback_array.shape == (3, 64, 48)
    assert s.n_frames == 3 and s.H == 64 and s.W == 48
    assert abs(s.microns_per_pixel - 0.1) < 1e-12
    # the lazy page wrapper was built with the page count + first-page geometry
    assert s.fallback_array.args[1:4] == (3, 64, 48)
