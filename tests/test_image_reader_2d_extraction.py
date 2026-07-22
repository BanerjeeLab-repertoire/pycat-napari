"""The extracted 2-D image reader must produce byte-identical channel tuples to the original
``FileIOClass.open_2d_image`` inline loop it replaced (god-class decomposition piece #2).

Same safety property as the mask-reader test: an extraction that changes behaviour is a bug, not a
refactor. Reimplements the ORIGINAL loop as the oracle and asserts the new free function matches it
across page/channel shapes — headlessly, with fakes (no napari / Qt).
"""

import numpy as np
import pytest

pytestmark = pytest.mark.core


class _FakeDims:
    def __init__(self, S=1, C=1):
        self.S = S
        self.C = C


class _FakeImage:
    def __init__(self, S, C):
        self.dims = _FakeDims(S, C)
        # a dask-metadata stand-in so the newbyteorder probe path is a no-op
        class _X:
            dims = ('Y', 'X')
        self.xarray_dask_data = _X()


def _install_fakes(monkeypatch, S, C):
    """Point the reader module's open_image/read_plane/extract_channel_info at deterministic fakes."""
    import pycat.file_io.readers.image_reader_2d as ir

    def open_image(path):
        return _FakeImage(S, C)

    def read_plane(image, path=None, scene=None, t=0, c=0, z=0, dtype=None):
        # encode the coordinate so identity is verifiable, not just shape
        return np.array([[scene if scene is not None else -1, c, t]])

    def extract_channel_info(image, ch_num, pixel_frame=None, file_stem=None):
        # Must track the real extract_channel_info signature as it grows, or the reader's call raises
        # TypeError which its `except: pass` swallows, leaving channel_info empty (which is exactly what
        # these assertions catch). It grew `pixel_frame` (classify modality from pixels when metadata is
        # silent) and then `file_stem` (prefer the user's filename over a generic pixel/position guess).
        return {'channel': ch_num, 'name': f'ch{ch_num}'}

    monkeypatch.setattr(ir, "open_image", open_image)
    monkeypatch.setattr(ir, "read_plane", read_plane)
    # extract_channel_info is imported inside the function from channel_naming
    import pycat.utils.channel_naming as cn
    monkeypatch.setattr(cn, "extract_channel_info", extract_channel_info)
    return open_image, read_plane, extract_channel_info


def _original_loop(file_path, open_image, read_plane):
    """Verbatim reimplementation of the pre-extraction inline channel loop (the oracle)."""
    image = open_image(file_path)
    num_pages = getattr(image.dims, 'S', 1)
    num_channels = getattr(image.dims, 'C', 1)
    if not hasattr(image.dims, 'S') and not hasattr(image.dims, 'C'):
        raise ValueError("Image does not have any channels or pages. Check file format.")
    out = []
    if num_pages > 1:
        k = 0
        for page_num in range(num_pages):
            for channel_num in range(num_channels):
                k += 1
                out.append((read_plane(image, path=file_path, scene=page_num, c=channel_num, t=0),
                            file_path, k))
    else:
        for channel_num in range(num_channels):
            out.append((read_plane(image, path=file_path, c=channel_num, t=0),
                        file_path, channel_num))
    return out


@pytest.mark.parametrize("S,C", [(1, 1), (1, 3), (2, 1), (3, 2), (4, 4)])
def test_extracted_image_reader_matches_original(monkeypatch, S, C):
    from pycat.file_io.readers.image_reader_2d import read_2d_image_channels
    oi, rp, _ = _install_fakes(monkeypatch, S, C)
    channels, channel_info, image, used_pil = read_2d_image_channels("f.tif")
    old = _original_loop("f.tif", oi, rp)

    assert used_pil is False
    assert image is not None
    assert len(channels) == len(old)
    for n, o in zip(channels, old):
        assert n[1] == o[1] and n[2] == o[2]        # file_path + key identical
        assert np.array_equal(n[0], o[0])           # channel data identical

    # channel_info has one entry per channel, in channel order
    assert len(channel_info) == C
    assert [ci['channel'] for ci in channel_info] == list(range(C))
