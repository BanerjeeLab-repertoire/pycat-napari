"""The extracted 2-D mask reader must produce byte-identical channel tuples to the original
``FileIOClass.open_2d_mask`` inline loop it replaced (god-class decomposition piece #1).

This is the safety property for the whole decomposition: an extraction that changes behaviour is a
bug, not a refactor. The test reimplements the ORIGINAL loop and asserts the new free function
matches it across page/channel shapes — headlessly, with a fake reader (no napari / Qt).
"""

import numpy as np
import pytest

pytestmark = pytest.mark.core


class _FakeDims:
    def __init__(self, S=1, C=1):
        self.S = S
        self.C = C


class _FakeMask:
    def __init__(self, S, C):
        self.dims = _FakeDims(S, C)


def _install_fakes(monkeypatch, S, C):
    """Point the reader module's open_image/read_plane at deterministic fakes."""
    import pycat.file_io.readers.mask_reader as mr

    def open_image(path):
        return _FakeMask(S, C)

    def read_plane(mask, path=None, scene=None, t=0, c=0, z=0, dtype=None):
        # content encodes the coordinate so identity is verifiable, not just shape
        return np.array([[scene if scene is not None else -1, c, t]])

    monkeypatch.setattr(mr, "open_image", open_image)
    monkeypatch.setattr(mr, "read_plane", read_plane)
    return open_image, read_plane


def _original_loop(file_path, open_image, read_plane):
    """Verbatim reimplementation of the pre-extraction inline loop (the oracle)."""
    mask = open_image(file_path)
    num_pages = getattr(mask.dims, 'S', 1)
    num_channels = getattr(mask.dims, 'C', 1)
    if not hasattr(mask.dims, 'S') and not hasattr(mask.dims, 'C'):
        raise ValueError("Image does not have any channels or pages. Check file format.")
    out = []
    if num_pages > 1:
        k = 0
        for page_num in range(num_pages):
            for channel_num in range(num_channels):
                k += 1
                out.append((read_plane(mask, path=file_path, scene=page_num, c=channel_num, t=0),
                            file_path, k))
    else:
        for channel_num in range(num_channels):
            out.append((read_plane(mask, path=file_path, c=channel_num, t=0),
                        file_path, channel_num))
    return out


@pytest.mark.parametrize("S,C", [(1, 1), (1, 3), (2, 1), (3, 2), (4, 4)])
def test_extracted_mask_reader_matches_original(monkeypatch, S, C):
    from pycat.file_io.readers.mask_reader import read_2d_mask_channels
    oi, rp = _install_fakes(monkeypatch, S, C)
    new = read_2d_mask_channels("f.tif")
    old = _original_loop("f.tif", oi, rp)
    assert len(new) == len(old)
    for n, o in zip(new, old):
        assert n[1] == o[1] and n[2] == o[2]        # file_path + key identical
        assert np.array_equal(n[0], o[0])           # channel data identical
