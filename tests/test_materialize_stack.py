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


class _CountingWrapper:
    """A lazy 3-D stack that records which frames were actually read — to prove `max_frames` reads
    ONLY the sampled frames (a large movie must not decode in full to be sampled)."""
    def __init__(self, arr):
        self._a = arr
        self.shape = arr.shape
        self.read = []

    def __array__(self, dtype=None):
        raise RuntimeError("full-read refused")

    def __getitem__(self, t):
        self.read.append(int(t))
        return self._a[t]


def test_materialize_max_frames_subsamples_and_reads_ONLY_those_frames():
    """A 600-frame movie capped at 64: returns 64 evenly-spaced frames and reads exactly those off
    disk — not the whole stack."""
    data = np.arange(600 * 3 * 3).reshape(600, 3, 3).astype(np.float32)
    w = _CountingWrapper(data)

    out = materialize_stack(w, max_frames=64)

    assert out.shape[0] <= 64 and out.shape[1:] == (3, 3)
    assert sorted(w.read) == sorted(set(w.read))            # each sampled frame read once
    assert len(w.read) == out.shape[0]                      # ONLY the sampled frames were read
    assert 0 in w.read and 599 in w.read                    # endpoints included
    # the returned frames really are those frames, in order
    assert np.allclose(out[0], data[0]) and np.allclose(out[-1], data[599])


def test_materialize_max_frames_is_a_noop_when_stack_is_small():
    """Fewer frames than the cap: read them all, no subsampling."""
    data = np.random.rand(10, 4, 4).astype(np.float32)
    out = materialize_stack(_TruncatingWrapper(data), max_frames=64)
    assert out.shape == (10, 4, 4)
    assert np.allclose(out, data)


def test_to_float_is_float32_not_float64():
    """QC's per-frame cast must not DOUBLE a large stack's memory (the float64 OOM)."""
    from pycat.toolbox.data_qc_tools import _to_float
    a = np.zeros((2, 2), dtype=np.float32)
    assert _to_float(a).dtype == np.float32
    assert _to_float(a) is a                                # float32 input -> no copy at all
