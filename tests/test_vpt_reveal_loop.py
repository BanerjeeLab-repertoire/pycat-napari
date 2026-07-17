"""**A plot click must not loop, and must not yank the view.**

`_reveal_track_in_viewer` moved the camera and stepped the frame on every pick.
Moving the camera fires napari's `draw_event`, which re-runs the MSD plot's
blit-capture, which can re-enter the pick → reveal again: a continuous jump until
force-close. `_select_track` was already guarded, but that guard stops selection
*echo* — it never saw the camera-move → draw → re-selection re-entrancy, because
that path does not go back through the dispatcher.

Two independent fixes, so two independent groups of tests:

* the navigation is **opt-in** (`central_manager.follow_selection`, OFF by
  default) — which removes the loop entirely for the default case, because there
  is no camera move to fire the `draw_event`;
* the reveal is **re-entrant-guarded** — which removes it for the users who turn
  follow ON, a supported state that must not force-close their session.

The second is not made redundant by the first. That is the whole point of having
both, and it is why the guard is tested with follow ON.
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


def _tracks():
    import pandas as pd
    return pd.DataFrame({
        'track_id': [7, 7, 7, 9],
        'frame': [4, 5, 6, 0],
        'y_um': [10.0, 11.0, 12.0, 1.0],
        'x_um': [20.0, 21.0, 22.0, 2.0],
    })


def _Hub(viewer, follow=False):
    """**The real VPT class**, with only its data accessors stubbed.

    A subclass, not a borrowed unbound method: `_reveal_track_in_viewer` is free
    to call anything else on `self`. `__init__` is deliberately not called — the
    real one builds Qt widgets, and the reveal's state is `getattr`-defensive.
    """
    from pycat.toolbox.vpt_ui import VideoParticleTrackingUI

    class _CM:
        follow_selection = follow

    class _H(VideoParticleTrackingUI):
        def __init__(self):
            self.viewer = viewer
            self.central_manager = _CM()
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


# ── follow OFF: the default, and the reason the loop cannot start ─────────────

def test_with_follow_OFF_a_pick_does_not_move_the_camera(immediate_qtimer):
    """You clicked a track to ask what it is. The view you were reading stays."""
    viewer = _Viewer()
    hub = _Hub(viewer, follow=False)

    hub._reveal_track_in_viewer(7)

    assert viewer.camera.center == (0.0, 0.0), 'the pick yanked the camera'


def test_with_follow_OFF_a_pick_does_not_jump_the_frame(immediate_qtimer):
    """The frame and the camera are the same question, so one flag gates both."""
    viewer = _Viewer()
    hub = _Hub(viewer, follow=False)

    hub._reveal_track_in_viewer(7)

    assert viewer.dims.current_step == (0, 0, 0), 'the pick jumped the timepoint'


def test_with_follow_OFF_the_track_is_still_MARKED(immediate_qtimer):
    """Not-navigating is not not-answering. The overlay is what makes staying put
    safe: the track is shown where it sits."""
    viewer = _Viewer()
    hub = _Hub(viewer, follow=False)

    hub._reveal_track_in_viewer(7)

    assert 'Picked track' in viewer.added, (
        'follow-off suppressed the highlight too — the click now does nothing')


def test_a_MISSING_manager_reads_as_do_not_yank(immediate_qtimer):
    """Plenty of callers have no manager. A missing preference must read as
    "leave my view alone", not crash and not navigate."""
    viewer = _Viewer()
    hub = _Hub(viewer, follow=False)
    hub.central_manager = None

    hub._reveal_track_in_viewer(7)

    assert viewer.camera.center == (0.0, 0.0)


def test_VPT_reads_the_SAME_preference_the_generic_brushing_path_reads(immediate_qtimer):
    """Not a second flag. `follow_selection` already existed and already meant
    this; VPT's plot just had its own pick route and never honoured it."""
    viewer = _Viewer()
    hub = _Hub(viewer, follow=False)
    hub.central_manager.follow_selection = True

    hub._reveal_track_in_viewer(7)

    assert viewer.camera.center != (0.0, 0.0), (
        'the shared follow_selection preference did not re-enable navigation')


# ── follow ON: supported, and it must not loop ────────────────────────────────

def test_with_follow_ON_a_pick_navigates_to_the_tracks_first_frame(immediate_qtimer):
    """Opt-in, not gone. Some users want it; the loop was the bug, not the feature."""
    viewer = _Viewer()
    hub = _Hub(viewer, follow=True)

    hub._reveal_track_in_viewer(7)

    assert viewer.dims.current_step[0] == 4, 'follow-on did not step to frame 4'
    assert viewer.camera.center == (10.0, 20.0), 'follow-on did not centre the bead'


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
    hub = _Hub(viewer, follow=True)

    hub._reveal_track_in_viewer(7)

    assert hub.reveals == 1, (
        f'the reveal body ran {hub.reveals} times for one pick — the camera move '
        f'came back round as another reveal. This is the loop that runs until '
        f'force-close.')


def test_the_guard_RELEASES_so_the_next_click_still_works(immediate_qtimer):
    """A guard that never releases is not a fix, it is a dead plot: every later
    pick would be silently swallowed."""
    viewer = _Viewer()
    hub = _Hub(viewer, follow=True)

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
    hub = _Hub(viewer, follow=True)
    hub._highlight_track_in_plot = lambda tid: None
    hub._highlight_track_in_table = lambda tid: None
    hub._reveal_track_in_viewer = (
        lambda tid, _o=hub._reveal_track_in_viewer: (selections.append(tid), _o(tid))[1])

    hub._select_track(7, source='plot')

    assert len(selections) <= 1, (
        f'one click produced {len(selections)} reveals — the selection is unbounded')
