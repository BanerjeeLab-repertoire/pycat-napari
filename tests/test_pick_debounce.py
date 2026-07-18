"""**One click is ONE selection — even where the MSD curves converge.**

Reported from a real viewer: clicking the VPT MSD plot "loops through many points on one click", and
after closing the window the terminal kept spitting out tracks it was still iterating. That was NOT
the 1.6.83 re-entrancy loop — the plot never re-entered itself. matplotlib fires a *separate*
`pick_event` for every line whose pick-radius (`set_picker(5)`) contains the click, and MSD curves all
fan out from near the origin, so one click there hits dozens of lines → dozens of genuine selections
queued.

`_debounce_picks` collapses the many picks from one click into ONE, on the line closest to the click.
These test that collapse directly: N picks sharing a click → one `apply_to_best`, on the nearest line.
The re-entrancy guard's tests still pass (they cover a different failure); this covers the one the
viewer actually hit.
"""

# Third party imports
import numpy as np
import pytest

# Local application imports
from pycat.toolbox import analysis_plots as ap

pytestmark = pytest.mark.core


class _Artist:
    """A stand-in Line2D: fixed pixel coordinates so distance-to-click is deterministic."""
    def __init__(self, px, py):
        self._px = np.asarray(px, float)
        self._py = np.asarray(py, float)

    def get_data(self):
        return self._px, self._py

    def get_transform(self):
        class _T:
            @staticmethod
            def transform(arr):
                return np.asarray(arr, float)      # identity: data coords ARE pixels here
        return _T()


class _Mouse:
    def __init__(self, x, y):
        self.x, self.y = x, y


class _Pick:
    def __init__(self, artist, mouse):
        self.artist = artist
        self.mouseevent = mouse


def _sync(monkeypatch):
    """Make `_defer_once` run immediately so a test can drive the resolve without a Qt loop —
    then feed all of a click's picks BEFORE the resolve, to model the real batched case."""
    calls = []
    monkeypatch.setattr(ap, '_defer_once', lambda fn: calls.append(fn))
    return calls


def test_MANY_picks_from_one_click_resolve_to_ONE_selection(monkeypatch):
    """The bug, as a unit test: a click near the convergence zone picks many lines; exactly one
    selection results."""
    deferred = _sync(monkeypatch)
    near = _Artist([10, 11], [10, 11])           # closest to the click at (10,10)
    far = _Artist([100, 101], [100, 101])
    l2t = {near: 1, far: 2}
    selected = []
    handler = ap._debounce_picks(l2t, lambda artist, tid: selected.append(tid))

    click = _Mouse(10, 10)
    handler(_Pick(far, click))                   # both lines picked by ONE click
    handler(_Pick(near, click))
    handler(_Pick(far, click))                   # matplotlib order is arbitrary
    assert len(deferred) == 1, 'more than one resolve scheduled for a single click'

    deferred[0]()                                # the event-loop tick fires
    assert selected == [1], f'expected one selection on the closest track, got {selected}'


def test_the_CLOSEST_line_wins(monkeypatch):
    deferred = _sync(monkeypatch)
    a = _Artist([0, 0], [0, 0])
    b = _Artist([5, 5], [5, 5])
    c = _Artist([50, 50], [50, 50])
    l2t = {a: 10, b: 20, c: 30}
    got = []
    handler = ap._debounce_picks(l2t, lambda artist, tid: got.append(tid))

    click = _Mouse(6, 6)                          # nearest to b
    for art in (a, c, b):
        handler(_Pick(art, click))
    deferred[0]()
    assert got == [20]


def test_a_SECOND_click_is_a_fresh_batch(monkeypatch):
    deferred = _sync(monkeypatch)
    a = _Artist([0, 0], [0, 0])
    b = _Artist([100, 100], [100, 100])
    l2t = {a: 1, b: 2}
    got = []
    handler = ap._debounce_picks(l2t, lambda artist, tid: got.append(tid))

    handler(_Pick(a, _Mouse(0, 0)))              # click 1 -> a
    deferred[-1]()
    handler(_Pick(b, _Mouse(100, 100)))          # click 2 -> b (new batch scheduled)
    deferred[-1]()
    assert got == [1, 2]
    assert len(deferred) == 2                     # one resolve per click, not per pick


def test_a_pick_on_an_UNKNOWN_artist_is_ignored(monkeypatch):
    deferred = _sync(monkeypatch)
    known = _Artist([0, 0], [0, 0])
    handler = ap._debounce_picks({known: 1}, lambda a, t: None)
    handler(_Pick(_Artist([1], [1]), _Mouse(0, 0)))     # not in the map
    assert deferred == [], 'an unmapped artist opened a batch'


def test_pixel_distance_uses_the_TRANSFORM_not_raw_data():
    """A log-log MSD plot warps data→pixels; the distance must be measured in pixels, which is why it
    goes through the artist's transform."""
    class _LogArtist(_Artist):
        def get_transform(self):
            class _T:
                @staticmethod
                def transform(arr):
                    return np.asarray(arr, float) * 10.0     # a non-identity transform
            return _T()

    art = _LogArtist([1, 2], [1, 2])
    d = ap._pick_pixel_distance(art, _Mouse(10, 10))         # data (1,1)->pixel (10,10): distance 0
    assert d == pytest.approx(0.0)
