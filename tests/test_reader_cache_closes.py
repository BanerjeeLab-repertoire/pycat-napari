"""The bounded reader cache must CLOSE readers it drops — on eviction (a 5th distinct file) and on
``clear_reader_cache()`` — so a cleared viewer doesn't leak file handles (on Windows an unclosed
reader keeps a HANDLE, blocking re-open/delete). BUT it must NOT close a reader a live layer still
owns: the cache and a layer's ImageSource can hold the same reader object (audit cleanup item 2).

Headless, no real files: fake readers with a ``.close()`` that records into a shared log, inserted
into the cache directly.
"""

import pytest

pytestmark = pytest.mark.core

import pycat.file_io.image_reader as ir
from pycat.file_io.image_source import ImageSource


class _FakeReader:
    def __init__(self, log, name):
        self._log = log
        self._name = name
        self.closed = False

    def close(self):
        self.closed = True
        self._log.append(self._name)


@pytest.fixture(autouse=True)
def _clean_cache():
    ir.clear_reader_cache()
    yield
    ir._READER_CACHE.clear()


def test_evicting_a_5th_reader_closes_the_oldest():
    log = []
    # Fill the cache to its limit; the readers are held ONLY by the cache (no external ref).
    for i in range(ir._READER_CACHE_LIMIT):
        ir._READER_CACHE[f"k{i}"] = _FakeReader(log, f"k{i}")
    # Insert one more the way open_image does — evict the oldest first.
    if len(ir._READER_CACHE) >= ir._READER_CACHE_LIMIT:
        ir._discard_reader(ir._READER_CACHE.pop(next(iter(ir._READER_CACHE))))
    ir._READER_CACHE["k_new"] = _FakeReader(log, "k_new")
    assert log == ["k0"]        # the oldest was closed, exactly once


def test_clear_reader_cache_closes_everything():
    log = []
    for i in range(3):
        ir._READER_CACHE[f"c{i}"] = _FakeReader(log, f"c{i}")
    ir.clear_reader_cache()
    assert sorted(log) == ["c0", "c1", "c2"]
    assert ir._READER_CACHE == {}


def test_a_reader_retained_by_a_live_layer_is_NOT_closed():
    log = []
    reader = _FakeReader(log, "retained")
    # A live layer's ImageSource owns it — retain() marks it so the cache leaves it open.
    src = ImageSource(file_path="x.tif")
    src.retain(reader)

    ir._READER_CACHE["retained_key"] = reader
    # Evict it — must NOT close, because the layer still needs it.
    ir._discard_reader(ir._READER_CACHE.pop("retained_key"))
    assert reader.closed is False and log == []

    # And clearing the cache must also leave a retained reader open.
    ir._READER_CACHE["retained_key"] = reader
    ir.clear_reader_cache()
    assert reader.closed is False and log == []


def test_retained_via_tuple_is_also_protected():
    # The generic loader retains (reader, dask_arr) tuples — retain() must still mark the reader.
    log = []
    reader = _FakeReader(log, "tuple_reader")
    src = ImageSource(file_path="x.czi")
    src.retain((reader, object()))     # tuple form, like the dask branches
    ir._READER_CACHE["k"] = reader
    ir._discard_reader(ir._READER_CACHE.pop("k"))
    assert reader.closed is False


def test_safe_close_tolerates_readers_without_close():
    # A reader lacking close()/__exit__ must not raise when discarded.
    class _NoClose:
        pass
    ir._discard_reader(_NoClose())   # no exception = pass
