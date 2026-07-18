"""**A plot click goes to the bead, and must not loop.**

Clicking a track in the MSD plot takes the user to that bead — steps to its
frame, centres, and zooms in. That navigation is what the user asked for, and it
is on by default now (1.6.104). The reason it was ever gated off was a loop:
moving the camera fires napari's `draw_event`, which re-runs the MSD plot's
blit-capture, which can re-enter the pick → reveal again, a continuous jump until
force-close. `_select_track` was already guarded, but that guard stops selection
*echo* — it never saw the camera-move → draw → re-selection re-entrancy, because
that path does not go back through the dispatcher.

So navigation is safe now for a different reason than "it doesn't happen": one
`button_press` per click (1.6.100) plus the `_revealing` re-entrancy guard. Two
groups of tests below: the pick **navigates** to the bead, and the reveal is
**re-entrant-guarded** so navigating can never loop.
"""

# Standard library imports

# Third party imports
import pytest

pytestmark = pytest.mark.core


@pytest.fixture
def immediate_qtimer(monkeypatch):
    """`QTimer.singleShot(0, fn)` runs `fn` now.

    The reveal releases its re-entrancy flag on a zero-delay timer, which needs a
    running Qt event loop to ever fire. Without one the flag would stay set and
    every later pick would be silently swallowed — a test that skipped this would
    characterize a deadlock rather than the guard.
    """
    pytest.importorskip("PyQt5.QtCore")

    class _QTimer:
        @staticmethod
        def singleShot(_ms, fn):
            fn()

    monkeypatch.setattr('PyQt5.QtCore.QTimer', _QTimer)
    return _QTimer


class _Image:
    """Stands in for a napari Image layer — the reveal reads its scale as the one
    source of truth for the world frame."""
    name = 'movie'

    def __init__(self):
        self.scale = (1.0, 1.0)


class _Layers(list):
    def __init__(self, *items):
        super().__init__(items)
        self.selection = set()

    def __contains__(self, name):
        return any(getattr(l, 'name', None) == name for l in self)

    def __getitem__(self, key):
        if isinstance(key, str):
            return next(l for l in self if getattr(l, 'name', None) == key)
        return list.__getitem__(self, key)

    def remove(self, name):
        for l in list(self):
            if getattr(l, 'name', None) == name:
                list.remove(self, l)


class _Notifies:
    """A fake that runs `_on_write` when `_WATCHED` is assigned.

    Via `__setattr__` rather than a property/setter pair, which reads more
    naturally but trips the repo's duplicate-definition rule (two defs of one
    name in one scope) — and that rule is worth more than the sugar.
    """
    _WATCHED = ''

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name == self._WATCHED and getattr(self, '_on_write', None):
            self._on_write()


class _Dims(_Notifies):
    """Writing `current_step` is one of the two things that fires a real
    `draw_event` — the frame jump."""
    _WATCHED = 'current_step'

    def __init__(self, on_write=None):
        self._on_write = None            # not armed until the state is built
        self.ndim = 3
        self.current_step = (0, 0, 0)
        self._on_write = on_write


class _Camera(_Notifies):
    """And writing `center` is the other one."""
    _WATCHED = 'center'

    def __init__(self, on_write=None):
        self._on_write = None
        self.center = (0.0, 0.0)
        self._on_write = on_write


class _Viewer:
    def __init__(self, on_write=None):
        self.layers = _Layers(_Image())
        self.dims = _Dims(on_write)
        self.camera = _Camera(on_write)
        self.added = []

    def add_shapes(self, data, **kw):
        layer = type('_S', (), {'name': kw.get('name'), 'data': data})()
        self.layers.append(layer)
        self.added.append(kw.get('name'))
        return layer

    def add_points(self, data, **kw):
        layer = type('_P', (), {'name': kw.get('name'), 'data': data})()
        self.layers.append(layer)
        self.added.append(kw.get('name'))
        return layer

    def add_tracks(self, data, **kw):
        layer = type('_T', (), {'name': kw.get('name'), 'data': data})()
        self.layers.append(layer)
        self.added.append(kw.get('name'))
        return layer


def _tracks():
    import pandas as pd
    return pd.DataFrame({
        'track_id': [7, 7, 7, 9],
        'frame': [4, 5, 6, 0],
        'y_um': [10.0, 11.0, 12.0, 1.0],
        'x_um': [20.0, 21.0, 22.0, 2.0],
    })


def _Hub(viewer):
    """**The real VPT class**, with only its data accessors stubbed.

    A subclass, not a borrowed unbound method: `_reveal_track_in_viewer` is free
    to call anything else on `self`. `__init__` is deliberately not called — the
    real one builds Qt widgets, and the reveal's state is `getattr`-defensive.
    """
    from pycat.toolbox.vpt_ui import VideoParticleTrackingUI

    class _H(VideoParticleTrackingUI):
        def __init__(self):
            self.viewer = viewer
            self.central_manager = None
            self.reveals = 0

        def _dr(self):
            # Counts BODY EXECUTIONS, not calls. A call the guard turns away is
            # the guard working — counting those would assert the opposite of
            # what these tests mean.
            self.reveals += 1
            return {'vpt_tracks': _tracks()}

        def _mpx(self):
            return 1.0

    return _H()


# ── a pick NAVIGATES to the bead (default now — the user asked for it) ────────
#
# Navigation was gated off while the plot-click loop existed. With one button_press per click and the
# re-entrancy guard, the camera move is safe, and going to the bead is what the user wants: a plot
# click steps to the bead's frame and centres on it.

def test_a_pick_STEPS_to_the_beads_frame(immediate_qtimer):
    viewer = _Viewer()
    hub = _Hub(viewer)
    hub._reveal_track_in_viewer(7)
    assert viewer.dims.current_step[0] == 4, 'the pick did not step to the bead frame'


def test_a_pick_CENTRES_the_camera_on_the_bead(immediate_qtimer):
    viewer = _Viewer()
    hub = _Hub(viewer)
    hub._reveal_track_in_viewer(7)
    assert viewer.camera.center == (10.0, 20.0), 'the pick did not centre the bead'


def test_a_pick_MARKS_the_track(immediate_qtimer):
    """Navigating and marking both happen — the track is shown where it sits AND the view goes to it."""
    viewer = _Viewer()
    hub = _Hub(viewer)
    hub._reveal_track_in_viewer(7)
    assert 'Picked track' in viewer.added


def test_a_MISSING_manager_does_not_crash_the_reveal(immediate_qtimer):
    """Navigation no longer depends on a manager preference, but a missing manager must still not
    crash the reveal."""
    viewer = _Viewer()
    hub = _Hub(viewer)
    hub.central_manager = None
    hub._reveal_track_in_viewer(7)          # must not raise
    assert viewer.camera.center == (10.0, 20.0)


# ── the reveal must not loop ──────────────────────────────────────────────────


def test_the_camera_move_does_not_RE_ENTER_the_reveal(immediate_qtimer):
    """**The force-close bug.**

    The camera write fires a `draw_event`, which re-runs the plot's blit-capture,
    which comes back through the pick. Simulated here by re-entering the reveal
    from the camera/dims setter — which is exactly what napari does, just with
    more layers in between.
    """
    viewer = _Viewer()
    hub = None

    def _draw_event():
        # napari's draw_event → the plot's blit capture → the pick → back here.
        if hub is not None:
            hub._reveal_track_in_viewer(7)

    viewer.dims._on_write = _draw_event
    viewer.camera._on_write = _draw_event
    hub = _Hub(viewer)

    hub._reveal_track_in_viewer(7)

    assert hub.reveals == 1, (
        f'the reveal body ran {hub.reveals} times for one pick — the camera move '
        f'came back round as another reveal. This is the loop that runs until '
        f'force-close.')


def test_the_guard_RELEASES_so_the_next_click_still_works(immediate_qtimer):
    """A guard that never releases is not a fix, it is a dead plot: every later
    pick would be silently swallowed."""
    viewer = _Viewer()
    hub = _Hub(viewer)

    hub._reveal_track_in_viewer(7)
    viewer.camera.center = (0.0, 0.0)       # prove the SECOND click does the work
    hub._reveal_track_in_viewer(7)

    assert hub.reveals == 2, 'the second pick was swallowed — the guard never released'
    assert viewer.camera.center == (10.0, 20.0)


def test_one_selection_per_click_not_unbounded(immediate_qtimer):
    """The dispatcher-level statement of the same property: a pick that triggers a
    reveal that fires a draw_event must still be ONE selection."""
    viewer = _Viewer()
    hub = None
    selections = []

    def _draw_event():
        if hub is not None:
            hub._select_track(7, source='plot')

    viewer.dims._on_write = _draw_event
    viewer.camera._on_write = _draw_event
    hub = _Hub(viewer)
    hub._highlight_track_in_plot = lambda tid: None
    hub._highlight_track_in_table = lambda tid: None
    hub._reveal_track_in_viewer = (
        lambda tid, _o=hub._reveal_track_in_viewer: (selections.append(tid), _o(tid))[1])

    hub._select_track(7, source='plot')

    assert len(selections) <= 1, (
        f'one click produced {len(selections)} reveals — the selection is unbounded')
