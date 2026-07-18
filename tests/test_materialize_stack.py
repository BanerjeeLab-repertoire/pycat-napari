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


class _RefusingWrapper:
    """Mimics the IMS readers (`_ImsReaderTYX` …): a lazy 3D stack whose ``__array__`` REFUSES an
    implicit full read (``lazy_guard.refuse_implicit_full_read``) rather than truncating, but which
    is indexable per frame. This is the shape that crashed QC on a 600-frame .ims: `materialize_stack`
    used to call ``np.asarray(wrapper)`` on the way to its rebuild, which the guard turned into a
    ``RuntimeError`` — so the sanctioned full-read path could not read the one wrapper family that
    most needs it.
    """
    def __init__(self, arr):
        self._a = arr
        self.shape = arr.shape

    def __array__(self, dtype=None):
        raise RuntimeError("An implicit full-stack read was attempted (guard).")

    def __getitem__(self, t):
        return self._a[t]


def test_materialize_reads_a_wrapper_that_REFUSES_asarray():
    """The regression: a guard-refusing 3D wrapper must materialise via per-frame indexing, not raise.
    (QC on a large .ims hit exactly this.)"""
    data = (np.random.rand(6, 8, 8) * 100).astype(np.float32)
    w = _RefusingWrapper(data)

    import pytest
    with pytest.raises(RuntimeError):
        np.asarray(w)                                   # the guard fires on a whole-wrapper read

    out = materialize_stack(w, dtype=np.float32)        # but the sanctioned path reads it frame-by-frame
    assert out.shape == (6, 8, 8)
    assert np.allclose(out, data)


def test_materialize_refusing_wrapper_preserves_label_dtype():
    masks = np.zeros((4, 8, 8), dtype=np.int32)
    masks[1, 2:5, 2:5] = 7
    out = materialize_stack(_RefusingWrapper(masks), dtype=None)
    assert out.dtype == np.int32 and out.shape == (4, 8, 8)
    assert set(np.unique(out)) == set(np.unique(masks))


def test_materialize_reports_progress_frame_by_frame():
    """The per-frame path drives the progress callback, so the off-thread dialog advances."""
    seen = []
    materialize_stack(_RefusingWrapper(np.zeros((5, 4, 4), np.float32)),
                      progress_callback=lambda done, total: seen.append((done, total)))
    assert seen[-1] == (5, 5) and len(seen) == 5
