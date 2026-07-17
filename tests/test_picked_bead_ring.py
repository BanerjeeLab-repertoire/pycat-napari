"""**The ring sat where the bead STARTED, not where the bead is.**

The marker was `add_points(path[:1])` — one point, `(1, 2)`: y and x, with no
frame coordinate. In a (T, Y, X) viewer that is a 2-D layer, so napari drew it on
EVERY frame at the bead's frame-0 position. Scrub forward, the bead moves off, the
ring stays behind. That is the "orange circle is offset from the bead" report.

**It was never padding.** `resolve_in_viewer`'s `pad_px=8` is mentioned only in its
own signature and never used in its body (verified by AST); `_centre_for` already
returns the exact bbox centre. The only real consumer of `pad_px` is
`resolve_offline`, for the CROP window, where padding is wanted. A missing axis
looks exactly like an offset, and `selection_overlay._centre_for` already guards
the same trap — "a 3-D+ viewer needs the leading coordinate or the rectangle floats
across every slice". VPT's own reveal never got that treatment.

So: one point per frame at `(frame, y, x)`. napari shows the one on the current
slice, so the ring sits ON the bead at every timepoint, by construction rather than
by luck.
"""

# Third party imports
import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.core


@pytest.fixture
def immediate_qtimer(monkeypatch):
    """Run timer callbacks synchronously, and record that one was armed."""
    pytest.importorskip("PyQt5.QtCore")
    armed = []

    class _QTimer:
        def __init__(self):
            self._fn = None
            self.started = None
            self.stopped = False
            armed.append(self)

        def timeout_connect(self, fn):
            self._fn = fn

        @property
        def timeout(self):
            outer = self

            class _Sig:
                def connect(self, fn):
                    outer._fn = fn
            return _Sig()

        def start(self, ms):
            self.started = ms

        def stop(self):
            self.stopped = True

        def fire(self):
            self._fn()

        @staticmethod
        def singleShot(_ms, fn):
            fn()

    monkeypatch.setattr('PyQt5.QtCore.QTimer', _QTimer)
    return armed


class _Layer:
    def __init__(self, data, **kw):
        self.name = kw.get('name')
        self.data = np.asarray(data)
        self.size = kw.get('size')
        self.opacity = kw.get('opacity')
        self.scale = kw.get('scale')
        self.kw = kw


class _Image:
    name = 'movie'
    scale = (1.0, 1.0)


class _Layers(list):
    def __init__(self, *items):
        super().__init__(items)
        self.selection = set()

    def __contains__(self, item):
        if isinstance(item, str):
            return any(getattr(l, 'name', None) == item for l in self)
        return any(l is item for l in self)

    def __getitem__(self, k):
        if isinstance(k, str):
            return next(l for l in self if getattr(l, 'name', None) == k)
        return list.__getitem__(self, k)

    def remove(self, name):
        for l in list(self):
            if getattr(l, 'name', None) == name or l is name:
                list.remove(self, l)


class _Dims:
    def __init__(self, ndim):
        self.ndim = ndim
        self.current_step = tuple([0] * ndim)


class _Camera:
    center = (0.0, 0.0)


class _Viewer:
    def __init__(self, ndim=3):
        self.layers = _Layers(_Image())
        self.dims = _Dims(ndim)
        self.camera = _Camera()

    def add_points(self, data, **kw):
        l = _Layer(data, **kw)
        self.layers.append(l)
        return l

    def add_shapes(self, data, **kw):
        l = _Layer(data, **kw)
        self.layers.append(l)
        return l


def _hub(viewer):
    from pycat.toolbox.vpt_ui import VideoParticleTrackingUI

    class _H(VideoParticleTrackingUI):
        def __init__(self):
            self.viewer = viewer
            self.central_manager = None

    return _H()


# A bead that MOVES — the whole point. Frame 4 at y=10, frame 6 at y=30.
_PATH = np.array([[10.0, 20.0], [20.0, 25.0], [30.0, 30.0]])
_FRAMES = np.array([4, 5, 6])


def test_the_ring_carries_a_FRAME_axis(immediate_qtimer):
    """`(frame, y, x)`, not `(y, x)`. Without the leading axis napari floats the
    ring across every slice, which is the bug."""
    v = _Viewer(ndim=3)
    _hub(v)._draw_picked_track(_PATH, _FRAMES, 1.0, 1.0, 1.0)

    ring = v.layers['Picked bead']
    assert ring.data.shape[1] == 3, (
        f'ring points are {ring.data.shape[1]}-D — a 2-D marker in a 3-D viewer '
        f'renders on every frame at the same place')


def test_there_is_one_ring_point_PER_FRAME_at_the_beads_position(immediate_qtimer):
    """Sitting on the bead is then true by construction, not by luck."""
    v = _Viewer(ndim=3)
    _hub(v)._draw_picked_track(_PATH, _FRAMES, 1.0, 1.0, 1.0)

    ring = v.layers['Picked bead']
    assert len(ring.data) == len(_FRAMES)
    assert np.array_equal(ring.data[:, 0], _FRAMES.astype(float))
    assert np.array_equal(ring.data[:, 1:], _PATH), (
        'the ring is not at the track coordinates')


def test_the_ring_is_at_the_TRUE_position_with_no_pad(immediate_qtimer):
    """The spec blamed `pad_px=8`. There is no padding here and there never was —
    the ring lands exactly on the track's own coordinates."""
    v = _Viewer(ndim=3)
    _hub(v)._draw_picked_track(_PATH, _FRAMES, 1.0, 1.0, 1.0)

    ring = v.layers['Picked bead']
    for (fy, fx), row in zip(_PATH, ring.data[:, 1:]):
        assert (fy, fx) == tuple(row), 'the ring is offset from the bead'


def test_pad_px_is_DEAD_in_resolve_in_viewer():
    """Pinned because a spec asserted the opposite as 'verified'. If someone later
    wires `pad_px` into the viewer path, the offset comes back and this fails."""
    import ast, inspect
    from pycat.utils import object_ref

    src = inspect.getsource(object_ref)
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == 'resolve_in_viewer':
            body = ast.get_source_segment(src, node)
            lines = [l for l in body.splitlines() if 'pad_px' in l]
            assert len(lines) == 1 and lines[0].lstrip().startswith('def '), (
                f'pad_px is used in resolve_in_viewer now: {lines}')
            return
    pytest.fail('resolve_in_viewer not found')


def test_the_TIME_axis_is_not_scaled_in_microns(immediate_qtimer):
    """Scale is (1, um/px, um/px). Scaling frames by the pixel size would put the
    ring on the wrong frame — a subtler version of the same bug."""
    v = _Viewer(ndim=3)
    _hub(v)._draw_picked_track(_PATH, _FRAMES, 0.1, 0.1, 0.1)

    assert v.layers['Picked bead'].scale == [1.0, 0.1, 0.1]


def test_a_2D_viewer_still_gets_a_ring(immediate_qtimer):
    """No time axis, nothing to index — fall back rather than crash."""
    v = _Viewer(ndim=2)
    _hub(v)._draw_picked_track(_PATH, _FRAMES, 1.0, 1.0, 1.0)

    ring = v.layers['Picked bead']
    assert ring.data.shape[1] == 2
    assert ring.scale == [1.0, 1.0]


def test_the_ring_is_HOLLOW_so_it_does_not_hide_the_bead(immediate_qtimer):
    v = _Viewer(ndim=3)
    _hub(v)._draw_picked_track(_PATH, _FRAMES, 1.0, 1.0, 1.0)

    assert v.layers['Picked bead'].kw.get('face_color') == 'transparent'


def test_the_overlay_is_NEVER_accumulated(immediate_qtimer):
    """Three picks must leave one ring, not three. The old resolver grew the layer
    list on every click."""
    v = _Viewer(ndim=3)
    hub = _hub(v)
    for _ in range(3):
        hub._draw_picked_track(_PATH, _FRAMES, 1.0, 1.0, 1.0)

    rings = [l for l in v.layers if getattr(l, 'name', None) == 'Picked bead']
    assert len(rings) == 1, f'{len(rings)} ring layers after 3 picks'


# ── the pulse ────────────────────────────────────────────────────────────────

def test_the_pulse_changes_SIZE_and_OPACITY_only(immediate_qtimer):
    """Display state, never the data. An overlay that edits pixels is not an overlay."""
    v = _Viewer(ndim=3)
    _hub(v)._draw_picked_track(_PATH, _FRAMES, 1.0, 1.0, 1.0)
    ring = v.layers['Picked bead']
    before = ring.data.copy()

    timer = immediate_qtimer[-1]
    seen = set()
    for _ in range(6):
        timer.fire()
        seen.add((ring.size, ring.opacity))

    assert len(seen) > 1, 'the ring never changed — it is not pulsing'
    assert np.array_equal(ring.data, before), 'the pulse moved the data'


def test_the_pulse_touches_ONLY_the_overlay_layer(immediate_qtimer):
    """Never the image/labels layer — the caution the spec is explicit about."""
    v = _Viewer(ndim=3)
    img = v.layers['movie']
    _hub(v)._draw_picked_track(_PATH, _FRAMES, 1.0, 1.0, 1.0)

    before = (img.scale, getattr(img, 'size', None), getattr(img, 'opacity', None))
    for _ in range(6):
        immediate_qtimer[-1].fire()

    assert (img.scale, getattr(img, 'size', None),
            getattr(img, 'opacity', None)) == before, 'the pulse touched the image layer'


def test_only_ONE_pulse_timer_survives_repeated_picks(immediate_qtimer):
    """A timer per click would accumulate, and each would pin a dead layer alive."""
    v = _Viewer(ndim=3)
    hub = _hub(v)
    for _ in range(3):
        hub._draw_picked_track(_PATH, _FRAMES, 1.0, 1.0, 1.0)

    running = [t for t in immediate_qtimer if t.started and not t.stopped]
    assert len(running) == 1, f'{len(running)} pulse timers still running'


def test_the_pulse_STOPS_when_the_ring_is_deleted(immediate_qtimer):
    """The user can always delete an overlay. A timer writing to a removed layer
    would raise on every tick, forever."""
    v = _Viewer(ndim=3)
    _hub(v)._draw_picked_track(_PATH, _FRAMES, 1.0, 1.0, 1.0)
    timer = immediate_qtimer[-1]

    v.layers.remove('Picked bead')
    timer.fire()

    assert timer.stopped, 'the pulse kept running after its layer was deleted'
