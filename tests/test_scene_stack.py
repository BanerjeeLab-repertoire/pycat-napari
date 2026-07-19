"""**One position at a time — and never the wrong one.**

`_SceneStack` is the lazy (T, Y, X) wrapper for a single scene of a multi-position acquisition. These
tests pin the two properties that make position-switching safe:

1. it reads **one plane at a time from its pinned scene** and never materialises the position
   (`__array__` refuses — the same contract the AST guard `test_no_eager_reads` enforces structurally);
2. it asks the reader for **its own scene on every read**, so a shared, stateful reader can never serve
   a frame from another position — the headline hazard of switching.

Qt-free: the wrapper lives in `lazy_sources.py` (the headless module) and reads through an injected
plane reader, so no napari/BioIO is needed here.
"""

import numpy as np
import pytest

pytestmark = pytest.mark.core

from pycat.file_io.lazy_sources import _SceneStack


class _FakeReader:
    """Stands in for `image_reader.read_plane`: records every (scene, t, c, z) asked for, and returns a
    constant uint16 plane whose value ENCODES the scene, so a returned frame's provenance is checkable.
    """

    def __init__(self, scene_values, H=4, W=4):
        self.scene_values = scene_values      # {scene_name: uint16 value}
        self.H, self.W = H, W
        self.calls = []

    def __call__(self, image, *, scene, t, c, z):
        self.calls.append((scene, int(t), int(c), int(z)))
        return np.full((self.H, self.W), self.scene_values[scene], dtype=np.uint16)


def _stack(reader, scene, n_t=10, H=4, W=4, channel_idx=0):
    return _SceneStack(image=object(), scene=scene, n_t=n_t, H=H, W=W,
                       dtype=np.uint16, channel_idx=channel_idx, plane_reader=reader)


def test_the_wrapper_advertises_an_honest_shape_and_the_float32_contract():
    stack = _stack(_FakeReader({'P1': 100}), 'P1', n_t=7, H=8, W=6)
    assert stack.shape == (7, 8, 6)
    assert stack.ndim == 3
    assert stack.dtype == np.dtype('float32')
    assert len(stack) == 7
    assert stack.scene == 'P1'


def test_reading_one_frame_reads_exactly_one_plane_from_the_pinned_scene():
    reader = _FakeReader({'P1': 30000})
    stack = _stack(reader, 'P1', n_t=10)

    frame = stack[5]

    assert reader.calls == [('P1', 5, 0, 0)], "one scalar read must be exactly one plane read"
    assert frame.dtype == np.float32
    # uint16 30000 normalised to [0, 1] is 30000/65535 — the [0,1] contract, not raw counts.
    assert np.allclose(frame, 30000 / 65535.0)
    assert frame.shape == (4, 4)


def test_the_channel_index_is_carried_into_the_read():
    reader = _FakeReader({'P1': 1})
    stack = _stack(reader, 'P1', channel_idx=2)
    stack[3]
    assert reader.calls == [('P1', 3, 2, 0)]


def test_a_frame_ALWAYS_comes_from_THIS_wrappers_scene_even_with_a_shared_reader():
    """The safety property. Two wrappers over the SAME (stateful) reader, pinned to different scenes.
    Reading either always asks for — and returns — its own position, so a switch (a new wrapper) can
    never serve a stale frame from the previous one."""
    reader = _FakeReader({'P1': 10000, 'P2': 50000})
    p1 = _stack(reader, 'P1')
    p2 = _stack(reader, 'P2')

    f1 = p1[0]
    f2 = p2[0]          # "switching" position = reading the other wrapper
    f1_again = p1[9]

    assert np.allclose(f1, 10000 / 65535.0), "P1 frame is not P1's data"
    assert np.allclose(f2, 50000 / 65535.0), "P2 frame is not P2's data — a stale/wrong position"
    assert np.allclose(f1_again, 10000 / 65535.0), "P1 came back with another position's pixels"
    assert [c[0] for c in reader.calls] == ['P1', 'P2', 'P1'], (
        "each read must name its OWN scene, so a shared stateful reader is always re-pinned")


def test_a_slice_reads_each_frame_in_the_range_once():
    reader = _FakeReader({'P1': 1})
    stack = _stack(reader, 'P1', n_t=10)
    block = stack[2:5]
    assert block.shape == (3, 4, 4)
    assert [c[1] for c in reader.calls] == [2, 3, 4]


def test_np_asarray_on_the_wrapper_REFUSES():
    """An implicit full read would materialise the whole position — exactly what this exists to avoid.
    The AST guard also enforces this structurally; this is the behavioural proof."""
    stack = _stack(_FakeReader({'P1': 1}), 'P1')
    with pytest.raises(RuntimeError, match="implicit full-stack read"):
        np.asarray(stack)
