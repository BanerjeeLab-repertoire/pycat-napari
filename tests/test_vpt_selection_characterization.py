"""**Characterization tests for VPT's linked-selection dispatcher — written BEFORE it moves.**

`vpt_ui._select_track` is described in its own spec as *"the strongest brushing implementation in
PyCAT"*, and increment 3 promotes it into a shared `SelectionService` **"behaviour-preserving by
construction"**.

It has **zero test coverage.** Not "thin" — zero: nothing in `tests/` mentions `_select_track` or
`_sel_busy`. So "the promotion did not change VPT's behaviour" was an unverifiable claim, and the
one thing a refactor of a working, untested, production feature must not be is unverifiable.

These tests pin the behaviour that exists **today**, before the extraction, so that the same file —
unchanged — has to pass afterwards. They are deliberately about the *dispatcher's contract*, not its
internals:

* the initiating view is never called back (that is the loop);
* the other views are updated exactly once;
* a view that throws does not take the others down;
* re-entrant calls made *during* propagation are suppressed;
* the busy flag is released after the event queue drains, not before.

They do NOT assert the redundant first guard, which is dead code (it is the busy check plus an extra
condition, so it can never fire independently) — pinning a branch that cannot execute would just
make the refactor harder for no gain.
"""

# Standard library imports

# Third party imports
import pytest


pytestmark = pytest.mark.core


@pytest.fixture
def immediate_qtimer(monkeypatch):
    """Make `QTimer.singleShot(0, fn)` run `fn` now.

    `_select_track` releases its busy flag on a zero-delay timer, which needs a running Qt event
    loop to ever fire. Without one the flag would stay set forever and every later selection would
    be silently swallowed — so a test that did not do this would characterize a deadlock rather
    than the dispatcher.

    Only the `QTimer` *attribute* is replaced, not the module: `vpt_ui` imports plenty else from
    `PyQt5.QtCore`, and swapping the whole module out just breaks the import under test.
    """
    pytest.importorskip("PyQt5.QtCore")

    class _QTimer:
        @staticmethod
        def singleShot(_ms, fn):
            fn()

    monkeypatch.setattr('PyQt5.QtCore.QTimer', _QTimer)
    return _QTimer


def _Dispatcher():
    """**The real VPT class**, with only its three highlight views replaced by recorders.

    A subclass rather than a borrowed unbound method: `_select_track` is free to call anything else
    on `self`, and a stand-in that only carries the three helpers would break the moment it does —
    which is exactly what a refactor is likely to do, and exactly when these tests must still work.
    `__init__` is deliberately not called: the real one builds Qt widgets, and the dispatcher's
    state is all `getattr`-defensive anyway.

    The import is function-local so this module stays collectable without the GUI stack.
    """
    from pycat.toolbox.vpt_ui import VideoParticleTrackingUI

    class _D(VideoParticleTrackingUI):
        def __init__(self):
            self.calls = []
            self.raise_in = set()
            self.viewer = None
            self.central_manager = None

        def _reveal_track_in_viewer(self, tid):
            self.calls.append(('image', tid))
            if 'image' in self.raise_in:
                raise RuntimeError('the image view is broken')

        def _highlight_track_in_plot(self, tid):
            self.calls.append(('plot', tid))
            if 'plot' in self.raise_in:
                raise RuntimeError('the plot view is broken')

        def _highlight_track_in_table(self, tid):
            self.calls.append(('table', tid))
            if 'table' in self.raise_in:
                raise RuntimeError('the table view is broken')

    return _D()


def _select_track_of(obj):
    return obj._select_track


def _views(dispatcher):
    return [view for view, _tid in dispatcher.calls]


def test_the_INITIATING_view_is_never_called_back(immediate_qtimer):
    """**That is the loop.** A view that re-highlights from its own action fires its own emit and
    comes straight back."""
    for source in ('plot', 'image', 'table'):
        d = _Dispatcher()
        _select_track_of(d)(7, source=source)
        assert source not in _views(d), f"the '{source}' view was called back from its own action"


def test_the_OTHER_views_are_each_updated_EXACTLY_once(immediate_qtimer):
    d = _Dispatcher()
    _select_track_of(d)(7, source='table')

    assert sorted(_views(d)) == ['image', 'plot']
    assert all(tid == 7 for _v, tid in d.calls)


def test_a_view_that_THROWS_does_not_take_the_others_down(immediate_qtimer):
    """One dead view must not cost the user the other two."""
    d = _Dispatcher()
    d.raise_in = {'image'}
    _select_track_of(d)(3, source='table')

    assert 'plot' in _views(d), "a failure in the image view stopped the plot from updating"


def test_a_RE_ENTRANT_selection_during_propagation_is_SUPPRESSED(immediate_qtimer):
    """The highlight this selection causes in view B must not fire B's own emit back through the
    dispatcher. Without the busy flag a click oscillates."""
    d = _Dispatcher()
    select = _select_track_of(d)

    echoes = []

    def _echoing_plot(tid):
        d.calls.append(('plot', tid))
        echoes.append(tid)
        select(tid + 1, source='plot')      # the view echoes, mid-propagation

    d._highlight_track_in_plot = _echoing_plot
    select(1, source='table')

    assert echoes == [1], "the plot view was propagated to more than once"
    # The echo must not have started a second propagation.
    assert _views(d).count('image') == 1, "a re-entrant selection propagated a second time"


def test_the_busy_flag_is_RELEASED_after_propagation(immediate_qtimer):
    """...so the NEXT genuine selection is not swallowed. With a real Qt loop this happens once the
    queue drains; the release must actually happen."""
    d = _Dispatcher()
    select = _select_track_of(d)

    select(1, source='table')
    select(2, source='table')

    assert [tid for _v, tid in d.calls if _v == 'plot'] == [1, 2], (
        "the second selection was swallowed — the busy flag was never released")


def test_a_None_track_selects_NOTHING(immediate_qtimer):
    d = _Dispatcher()
    _select_track_of(d)(None, source='table')
    assert d.calls == []


def test_the_selected_track_is_RECORDED_on_the_dispatcher(immediate_qtimer):
    """Other VPT code reads `_selected_track_id`; the promotion must keep it populated."""
    d = _Dispatcher()
    _select_track_of(d)(42, source='plot')
    assert getattr(d, '_selected_track_id', None) == 42


def test_WITHOUT_Qt_the_release_falls_back_to_SYNCHRONOUS(monkeypatch):
    """Headless, the `QTimer` import fails and the flag is cleared inline. If it did not, the very
    first selection would wedge the dispatcher for the rest of the session.

    `from PyQt5.QtCore import QTimer` is an attribute lookup, so deleting the attribute is what
    makes the import raise — without disturbing everything else `vpt_ui` imports from that module.
    """
    pytest.importorskip("PyQt5.QtCore")
    monkeypatch.delattr('PyQt5.QtCore.QTimer')

    d = _Dispatcher()
    select = _select_track_of(d)
    select(1, source='table')
    select(2, source='table')

    assert [tid for _v, tid in d.calls if _v == 'plot'] == [1, 2], (
        "without Qt the busy flag was never released — every selection after the first is dead")
