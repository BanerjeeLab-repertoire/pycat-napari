"""The BioFormats CZI path: a lazy (T, Y, X) stack that reads one plane at a time.

Two layers of coverage:

* **Unit (headless, no JVM):** the ``_CziChannelStack`` lazy wrapper over a fake reader — shape,
  dtype, single-plane vs slice indexing, and that ``__array__`` REFUSES a full-stack read (the
  lazy-guard that stops napari materialising 15,766 frames for a thumbnail).
* **Integration (skip unless the extra AND a real streaming CZI are present):** the streaming file
  libCZI cannot decode opens through BioFormats, dims match, and planes read non-zero. Guarded so CI
  without the JVM / without the file skips cleanly.

Point ``PYCAT_CZI_STREAMING_FILE`` at a streaming CZI to run the integration test (it also tries the
known local path).
"""

import os

import numpy as np
import pytest

pytestmark = pytest.mark.core

from pycat.file_io.readers import czi_bioformats as cb


# ── Unit: the lazy wrapper, no JVM ──────────────────────────────────────────────────────────

class _FakeReader:
    """Stand-in for CziBioFormatsReader: the wrapper only needs n_t/H/W + _read_plane(t, c, z)."""
    def __init__(self, n_t, H, W):
        self.n_t, self.H, self.W = n_t, H, W

    def _read_plane(self, t, c, z):
        # deterministic [0,1] float32 plane encoding (t, c) + a spatial gradient
        grad = np.linspace(0, 1, self.H * self.W, dtype=np.float32).reshape(self.H, self.W)
        return (grad * 0.5 + (int(t) % 10) * 0.01 + int(c) * 0.001).astype(np.float32)


def test_channel_stack_shape_dtype_and_len():
    st = cb._CziChannelStack(_FakeReader(15766, 500, 500), channel_idx=0)
    assert st.shape == (15766, 500, 500)
    assert st.dtype == np.dtype('float32')
    assert st.ndim == 3
    assert len(st) == 15766


def test_channel_stack_single_plane_vs_slice():
    r = _FakeReader(12, 8, 6)
    st = cb._CziChannelStack(r, channel_idx=1)
    one = st[5]
    assert one.shape == (8, 6) and one.dtype == np.float32
    assert np.array_equal(one, r._read_plane(5, 1, 0))
    # a y/x sub-slice on a single plane
    assert st[5, 0:3, 1:4].shape == (3, 3)
    # a T slice stacks planes
    sl = st[2:5]
    assert sl.shape == (3, 8, 6)
    assert np.array_equal(sl[0], r._read_plane(2, 1, 0))


def test_channel_stack_refuses_implicit_full_read():
    st = cb._CziChannelStack(_FakeReader(15766, 500, 500), channel_idx=0)
    with pytest.raises(Exception):
        np.asarray(st)   # __array__ must refuse — else napari pulls all 15,766 frames


def test_bioformats_available_is_bool():
    assert isinstance(cb.bioformats_available(), bool)


# ── probe_libczi: routes AND hands back the libCZI image so the big open isn't paid twice ────

def test_probe_returns_the_image_even_when_the_PIXEL_read_fails(monkeypatch):
    """A streaming CZI: libCZI opens (metadata) but its pixel read raises. The image must still come
    back so the streaming loader reuses it — the multi-second subblock parse is paid ONCE, not again
    in `_open_czi_streaming`."""
    import pycat.file_io.image_reader as ir

    class _Img:
        def get_image_dask_data(self, *a, **k):
            raise RuntimeError("The method or operation is not implemented.")

    img = _Img()
    monkeypatch.setattr(ir, 'open_image', lambda p: img)
    can_read, out = cb.probe_libczi("streaming.czi")
    assert can_read is False and out is img


def test_probe_is_TRUE_and_returns_the_image_when_the_read_succeeds(monkeypatch):
    import pycat.file_io.image_reader as ir

    class _DD:
        def compute(self):
            return np.zeros((2, 2), np.uint16)

    class _Img:
        def get_image_dask_data(self, *a, **k):
            return _DD()

    img = _Img()
    monkeypatch.setattr(ir, 'open_image', lambda p: img)
    assert cb.probe_libczi("normal.czi") == (True, img)


def test_probe_image_is_NONE_only_when_the_OPEN_itself_fails(monkeypatch):
    import pycat.file_io.image_reader as ir

    def _boom(p):
        raise IOError("cannot open")

    monkeypatch.setattr(ir, 'open_image', _boom)
    assert cb.probe_libczi("broken.czi") == (False, None)


def test_libczi_can_read_is_just_the_bool_of_probe(monkeypatch):
    monkeypatch.setattr(cb, 'probe_libczi', lambda p: (True, object()))
    assert cb.libczi_can_read("x.czi") is True
    monkeypatch.setattr(cb, 'probe_libczi', lambda p: (False, None))
    assert cb.libczi_can_read("x.czi") is False


# ── Integration: a real streaming CZI through BioFormats ────────────────────────────────────

_CZI = os.environ.get("PYCAT_CZI_STREAMING_FILE") or (
    r"C:\Users\Gable\Desktop\A pycat test data"
    r"\Movie 5 - CAG31 100uM - 50mM Mg 25mM Na 10mM tris tphase40-004.czi")

needs_streaming_czi = pytest.mark.skipif(
    not cb.bioformats_available() or not os.path.exists(_CZI),
    reason="set PYCAT_CZI_STREAMING_FILE and `pip install pycat-napari[bioformats]` to run")


@pytest.mark.integration
@needs_streaming_czi
def test_streaming_czi_opens_and_reads_through_bioformats():
    # libCZI must NOT be able to read this file's pixels (that is why we route to BioFormats).
    assert cb.libczi_can_read(_CZI) is False

    reader = cb.CziBioFormatsReader(_CZI)
    try:
        assert reader.H == 500 and reader.W == 500
        assert reader.n_t >= 15000            # ~15,766-frame streaming movie
        st = reader.channel_stack(0)
        assert st.shape == (reader.n_t, 500, 500)
        for t in (0, 1, 100, reader.n_t - 1):
            plane = st[t]
            assert plane.shape == (500, 500) and plane.dtype == np.float32
            assert 0.0 <= float(plane.min()) and float(plane.max()) <= 1.0
            assert float(plane.mean()) > 0.0     # genuine pixels, not all-zero
    finally:
        reader.close()
