"""The extracted lazy IMS wrappers must produce byte-identical reads to the original
``FileIOClass._open_stack_ims`` inline classes they replaced (god-class decomposition piece #3).

Unlike the pure read-functions of pieces #1/#2, these are lazy WRAPPERS consumed during napari
layer construction, so the safety property is: ``wrapper[idx]`` / ``.shape`` / ``.dtype`` are
identical to a reimplemented-inline oracle across index forms and axis orders. Headless — a fake
``reader`` (mirroring ``tests/test_mask_reader_extraction.py``'s fake) avoids the real IMS lib.
"""

import numpy as np
import pytest

pytestmark = pytest.mark.core

from pycat.file_io.readers.ims_reader import (
    _ImsReaderTYX, _ImsReaderZYX, _ImsReaderTZYX,
    _ims_indices, _ims_frame_2d)


class _FakeIms:
    """Minimal stand-in for imaris_ims_file_reader.ims, indexed ``[t, c, z, :, :]``.

    Returns a deterministic uint16 (Y, X) plane whose values encode (t, c, z) PLUS a spatial
    gradient, so both frame selection and y/x sub-slicing are verifiable (not just shape). The
    real reader is always asked for full ``:, :`` by the wrappers, which then apply the y/x
    selector themselves — so this ignores the trailing slices exactly as the wrappers assume.
    """
    def __init__(self, T, C, Z, Y, X):
        self.shape = (T, C, Z, Y, X)
        self._Y, self._X = Y, X

    def __getitem__(self, key):
        t, c, z = int(key[0]), int(key[1]), int(key[2])
        grad = np.arange(self._Y * self._X, dtype=np.uint16).reshape(self._Y, self._X)
        return (grad + np.uint16((t * 37 + c * 7 + z * 3) % 200)).astype(np.uint16)


# ── Oracles: the wrappers' getitem algorithm, reimplemented inline in the test.
# They reuse the (trivial, separately-moved) _ims_indices / _ims_frame_2d helpers exactly as the
# original inline classes delegated to them — the risk under test is the index/stack/squeeze logic.

def _oracle_tyx(reader, c, idx):
    T = reader.shape[0]
    if isinstance(idx, tuple):
        t_sel = idx[0] if len(idx) > 0 else slice(None)
        yx_sel = idx[1:] if len(idx) > 1 else (slice(None), slice(None))
    else:
        t_sel, yx_sel = idx, (slice(None), slice(None))
    frames = [_ims_frame_2d(reader[int(t), c, 0, :, :])[yx_sel]
              for t in _ims_indices(t_sel, T)]
    if isinstance(t_sel, (int, np.integer)):
        return frames[0]
    return np.stack(frames, axis=0)


def _oracle_zyx(reader, c, t0, idx):
    Z = reader.shape[2]
    if isinstance(idx, tuple):
        z_sel = idx[0] if len(idx) > 0 else slice(None)
        yx_sel = idx[1:] if len(idx) > 1 else (slice(None), slice(None))
    else:
        z_sel, yx_sel = idx, (slice(None), slice(None))
    planes = [_ims_frame_2d(reader[t0, c, int(z), :, :])[yx_sel]
              for z in _ims_indices(z_sel, Z)]
    if isinstance(z_sel, (int, np.integer)):
        return planes[0]
    return np.stack(planes, axis=0)


def _oracle_tzyx(reader, c, idx):
    T, _, Z = reader.shape[0], reader.shape[1], reader.shape[2]
    if isinstance(idx, tuple):
        t_sel = idx[0] if len(idx) > 0 else slice(None)
        z_sel = idx[1] if len(idx) > 1 else slice(None)
        yx_sel = idx[2:] if len(idx) > 2 else (slice(None), slice(None))
    else:
        t_sel, z_sel, yx_sel = idx, slice(None), (slice(None), slice(None))
    t_indices = _ims_indices(t_sel, T)
    z_indices = _ims_indices(z_sel, Z)
    arr = np.stack([
        np.stack([_ims_frame_2d(reader[int(t), c, int(z), :, :])[yx_sel] for z in z_indices], axis=0)
        for t in t_indices
    ], axis=0)
    if isinstance(z_sel, (int, np.integer)):
        arr = arr[:, 0]
    if isinstance(t_sel, (int, np.integer)):
        arr = arr[0]
    return arr


_TYX_IDX = [0, 2, slice(1, 3), slice(None), [0, 2], (1, slice(0, 2), slice(1, 3)), (slice(None),)]
_ZYX_IDX = _TYX_IDX
_TZYX_IDX = [
    (0, 0), (1, 2), (0, slice(None)), (slice(1, 3), 1), (slice(None), slice(None)),
    (1, 1, slice(0, 2), slice(1, 3)), 0, slice(0, 2),
]


@pytest.mark.parametrize("idx", _TYX_IDX)
def test_tyx_matches_oracle(idx):
    reader = _FakeIms(T=4, C=3, Z=1, Y=5, X=6)
    w = _ImsReaderTYX(reader, c=1)
    assert w.shape == (4, 5, 6)
    assert w.dtype == np.dtype('float32')
    assert len(w) == 4
    assert np.array_equal(w[idx], _oracle_tyx(reader, 1, idx))


@pytest.mark.parametrize("idx", _ZYX_IDX)
def test_zyx_matches_oracle(idx):
    reader = _FakeIms(T=2, C=3, Z=4, Y=5, X=6)
    w = _ImsReaderZYX(reader, c=2, t=1)
    assert w.shape == (4, 5, 6)
    assert w.dtype == np.dtype('float32')
    assert len(w) == 4
    assert np.array_equal(w[idx], _oracle_zyx(reader, 2, 1, idx))


@pytest.mark.parametrize("idx", _TZYX_IDX)
def test_tzyx_matches_oracle(idx):
    reader = _FakeIms(T=3, C=2, Z=4, Y=5, X=6)
    w = _ImsReaderTZYX(reader, c=1)
    assert w.shape == (3, 4, 5, 6)
    assert w.dtype == np.dtype('float32')
    assert len(w) == 3
    assert np.array_equal(w[idx], _oracle_tzyx(reader, 1, idx))


def test_wrappers_refuse_implicit_full_read():
    """__array__ must refuse — the lazy-guard that stops napari materialising the whole stack."""
    reader = _FakeIms(T=3, C=2, Z=4, Y=5, X=6)
    for w in (_ImsReaderTYX(reader, c=0),
              _ImsReaderZYX(reader, c=0),
              _ImsReaderTZYX(reader, c=0)):
        with pytest.raises(Exception):
            np.asarray(w)


def test_default_frames_are_unit_float32():
    """A lazily-read frame is normalised from the SOURCE dtype into [0, 1] float32."""
    reader = _FakeIms(T=2, C=1, Z=1, Y=4, X=4)
    frame = _ImsReaderTYX(reader, c=0)[0]
    assert frame.dtype == np.float32
    assert 0.0 <= float(frame.min()) and float(frame.max()) <= 1.0
