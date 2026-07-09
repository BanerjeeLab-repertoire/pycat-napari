"""Tests for materialize_stack / as_full_array — the stack-materialisation path
that protects per-frame analysis (FRAP, viscosity, time-series) from the lazy
wrapper frame-collapse bug, where np.asarray(layer.data) silently returns only
frame 0 of a (T, H, W) stack.

These are golden-master style: they assert the materialiser reconstructs the
full stack (and preserves dtype for label masks) even when the wrapper's
__array__ is deliberately truncated to a single frame.
"""

import numpy as np

from pycat.file_io.file_io import materialize_stack


class _TruncatingWrapper:
    """Mimics a lazy stack whose __array__ returns ONLY frame 0 (the bug), but
    advertises a 3D shape and supports per-frame __getitem__ (the recovery path).
    """
    def __init__(self, arr):
        self._a = arr
        self.shape = arr.shape

    def __array__(self, dtype=None):
        f0 = self._a[0]
        return f0 if dtype is None else f0.astype(dtype)

    def __getitem__(self, t):
        return self._a[t]


def test_materialize_recovers_truncated_float_stack():
    data = (np.random.rand(5, 8, 8) * 100).astype(np.float32)
    w = _TruncatingWrapper(data)
    # The bug: raw asarray collapses to 2D.
    assert np.asarray(w).ndim == 2
    out = materialize_stack(w, dtype=np.float32)
    assert out.shape == (5, 8, 8)
    assert np.allclose(out, data)


def test_materialize_preserves_label_mask_dtype():
    masks = np.zeros((4, 8, 8), dtype=np.int32)
    masks[1, 2:5, 2:5] = 7
    masks[2] = 3
    w = _TruncatingWrapper(masks)
    out = materialize_stack(w, dtype=None)
    assert out.shape == (4, 8, 8)
    assert out.dtype == np.int32                       # labels not floated
    assert set(np.unique(out)) == set(np.unique(masks))


def test_materialize_2d_passthrough():
    img = np.random.rand(8, 8).astype(np.float32)
    out = materialize_stack(img)
    assert out.shape == (8, 8)


def test_materialize_plain_3d_array():
    data = np.random.rand(3, 6, 6).astype(np.float32)
    out = materialize_stack(data)
    assert out.shape == (3, 6, 6)
    assert np.allclose(out, data)
