"""**One physical click is ONE selection — via button_press, not per-line pick_event.**

An audit of the first fix found the debounce intrinsically fragile: it assumed every `pick_event` from
one mouse press arrives before a zero-delay timer fires, which is not a safe contract, so a click could
still resolve several tracks. The mechanism was replaced: the MSD lines are non-pickable, and one
canvas-level `button_press_event` handler picks the nearest curve. matplotlib fires exactly one
button-press per click, so there is nothing to batch — the cascade is gone by construction.

A first cut *refused* to choose where curves overlapped, but real MSD curves overlap essentially
everywhere, so that meant nothing ever got selected (reported from the viewer). The dense-data model is
the opposite and is what these test: a near click ALWAYS selects the nearest, and repeated clicks at
the same spot CYCLE through the overlapping stack. Plus the audit's structural cases: one press → one
track; empty space → none; outside axes → none; lone-track re-click → no-op; nearest by SEGMENT not
vertex; and the real matplotlib event path the audit said was never reproduced.
"""

# Third party imports
import numpy as np
import pytest

# Local application imports
from pycat.toolbox import analysis_plots as ap

pytestmark = pytest.mark.core


class _Line:
    """A stand-in Line2D with an identity data→pixel transform, so display coords ARE data coords."""
    def __init__(self, xs, ys):
        self._x = np.asarray(xs, float)
        self._y = np.asarray(ys, float)

    def get_data(self):
        return self._x, self._y

    def get_transform(self):
        class _T:
            @staticmethod
            def transform(arr):
                return np.asarray(arr, float)
        return _T()


class _Canvas:
    def __init__(self):
        self.handlers = {}

    def mpl_connect(self, name, fn):
        self.handlers.setdefault(name, []).append(fn)
        return len(self.handlers[name])

    def click(self, ax, x, y, button=1):
        ev = type('E', (), {'inaxes': ax, 'x': x, 'y': y, 'button': button})()
        for fn in self.handlers.get('button_press_event', []):
            fn(ev)


class _Fig:
    def __init__(self):
        self.canvas = _Canvas()


def _wire(line_to_tid, ax, *, radius_px=8.0):
    fig = _Fig()
    state = {'prev': None}
    got = []

    def _apply(ln, tid):
        state['prev'] = ln
        got.append(tid)

    notes = []
    ap._connect_nearest_curve_click(fig, ax, line_to_tid, state, _apply,
                                    radius_px=radius_px, notify=notes.append)
    return fig, state, got, notes


# ── segment distance ────────────────────────────────────────────────────────────

def test_distance_is_to_the_SEGMENT_not_the_nearest_vertex():
    """A click on the drawn edge BETWEEN two sampled points is distance ~0 to that curve — vertex
    distance would report the gap to the nearest endpoint and could pick a neighbour."""
    line = _Line([0, 100], [0, 0])          # a long horizontal segment, endpoints far apart
    d_seg = ap._segment_distance_px(line, 50, 0)      # mid-segment, on the line
    assert d_seg == pytest.approx(0.0)
    # the nearest VERTEX is 50px away; segment distance must be far smaller
    assert d_seg < 1.0


def test_distance_handles_a_single_point_line():
    assert ap._segment_distance_px(_Line([5], [5]), 8, 9) == pytest.approx(np.hypot(3, 4))


# ── one click, one selection ─────────────────────────────────────────────────────

def test_ONE_click_in_a_dense_overlap_selects_EXACTLY_ONE():
    """The bug: near the origin many curves converge. One button_press must yield exactly one
    selection — not a cascade (the original bug), and not zero (the over-eager refusal)."""
    ax = object()
    # 20 curves all passing through (10,10), fanning out
    lines = {_Line([10, 10 + i], [10, 10 + 2 * i]): i for i in range(20)}
    fig, state, got, notes = _wire(lines, ax)
    fig.canvas.click(ax, 10, 10)
    assert len(got) == 1, f'one click selected {len(got)} tracks (want exactly 1)'


def test_the_NEAREST_curve_wins():
    ax = object()
    near = _Line([5, 6], [5, 6])
    far = _Line([80, 81], [80, 81])
    fig, state, got, notes = _wire({near: 1, far: 2}, ax)
    fig.canvas.click(ax, 5, 5)
    assert got == [1]


def test_a_click_in_EMPTY_space_selects_nothing():
    ax = object()
    fig, state, got, notes = _wire({_Line([0, 1], [0, 1]): 1}, ax, radius_px=8.0)
    fig.canvas.click(ax, 500, 500)          # far from any curve
    assert got == []


def test_a_click_OUTSIDE_the_axes_is_ignored():
    ax = object()
    other_ax = object()
    fig, state, got, notes = _wire({_Line([0, 0], [0, 0]): 1}, ax)
    fig.canvas.click(other_ax, 0, 0)        # inaxes is not our ax
    assert got == []


def test_re_clicking_the_SELECTED_track_emits_nothing_new():
    ax = object()
    a = _Line([0, 1], [0, 1])
    fig, state, got, notes = _wire({a: 7, _Line([50, 51], [50, 51]): 8}, ax)
    fig.canvas.click(ax, 0, 0)
    fig.canvas.click(ax, 0, 0)              # same spot, same track
    assert got == [7]                       # not [7, 7]


def test_a_non_left_button_is_ignored():
    ax = object()
    fig, state, got, notes = _wire({_Line([0, 0], [0, 0]): 1}, ax)
    fig.canvas.click(ax, 0, 0, button=3)    # right-click
    assert got == []


# ── cycling through overlapping candidates (the dense-data model) ────────────────

def test_a_track_is_ALWAYS_selected_when_near_even_if_ambiguous():
    """Real MSD curves overlap everywhere; refusing to choose means never selecting. A near click
    always selects one, and hints that more are stacked here."""
    ax = object()
    a = _Line([10, 11], [10, 11])
    b = _Line([10, 11], [10, 11])           # coincident — maximally ambiguous
    fig, state, got, notes = _wire({a: 1, b: 2}, ax)
    fig.canvas.click(ax, 10, 10)
    assert len(got) == 1                     # one selected, not zero
    assert notes and 'cycle' in notes[0].lower()   # told they can cycle


def test_REPEATED_clicks_at_the_same_spot_CYCLE_through_the_stack():
    """The requested UX: click again to move to the next overlapping track, wrapping around."""
    ax = object()
    a = _Line([10, 11], [10, 11])
    b = _Line([10, 11], [10, 11])
    c = _Line([10, 11], [10, 11])
    fig, state, got, notes = _wire({a: 1, b: 2, c: 3}, ax)
    for _ in range(5):                       # 3 candidates -> 1,2,3,1,2
        fig.canvas.click(ax, 10, 10)
    assert got == [1, 2, 3, 1, 2], f'cycling did not walk the stack: {got}'


def test_a_click_at_a_NEW_spot_starts_a_fresh_stack():
    ax = object()
    a = _Line([0, 1], [0, 1])
    b = _Line([0, 1], [0, 1])
    far = _Line([100, 101], [100, 101])
    fig, state, got, notes = _wire({a: 1, b: 2, far: 9}, ax)
    fig.canvas.click(ax, 0, 0)               # stack at origin -> 1
    fig.canvas.click(ax, 0, 0)               # cycle -> 2
    fig.canvas.click(ax, 100, 100)           # NEW spot -> 9 (fresh)
    assert got == [1, 2, 9]


def test_re_clicking_a_LONE_track_is_a_no_op():
    """Only one curve near the click: re-clicking the same spot changes nothing (no re-fire)."""
    ax = object()
    a = _Line([0, 1], [0, 1])
    fig, state, got, notes = _wire({a: 1, _Line([80, 81], [80, 81]): 2}, ax)
    fig.canvas.click(ax, 0, 0)
    fig.canvas.click(ax, 0, 0)
    assert got == [1]


# ── the REAL matplotlib event path (the audit's missing coverage) ────────────────
#
# The fake-canvas tests above check the handler logic; these drive an actual matplotlib figure with
# an actual `button_press_event` through the canvas callback machinery — the exact path the audit
# said was never reproduced. Agg, so no Qt/viewer needed.

def _real_fig_with_curves(n=30):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    line_to_tid = {}
    for tid in range(n):
        x = np.linspace(0.1, 10, 20)
        y = (0.5 + 0.05 * tid) * x ** (0.8 + 0.01 * tid)   # converge near origin, fan out
        (ln,) = ax.plot(x, y, alpha=0.2)
        line_to_tid[ln] = tid
    fig.canvas.draw()
    return fig, ax, line_to_tid


def _fire(fig, ax, data_xy, button=1):
    from matplotlib.backend_bases import MouseEvent
    px, py = ax.transData.transform(data_xy)
    ev = MouseEvent('button_press_event', fig.canvas, px, py, button=button)
    fig.canvas.callbacks.process('button_press_event', ev)


def test_REAL_event_one_press_yields_at_most_one_selection():
    fig, ax, l2t = _real_fig_with_curves(30)
    state = {'prev': None}
    got = []
    ap._connect_nearest_curve_click(
        fig, ax, l2t, state,
        lambda ln, tid: (state.__setitem__('prev', ln), got.append(tid))[-1],
        notify=lambda m: None)

    _fire(fig, ax, (9.5, 3.0))            # a clear click far along one curve
    assert len(got) <= 1
    import matplotlib.pyplot as plt
    plt.close(fig)


def test_REAL_event_convergence_click_does_not_cycle():
    """The reported bug, through the real event machinery: a click in the convergence zone must not
    fire a cascade of selections. (It may select one or, if too ambiguous, none — never many.)"""
    fig, ax, l2t = _real_fig_with_curves(30)
    state = {'prev': None}
    got = []
    ap._connect_nearest_curve_click(
        fig, ax, l2t, state,
        lambda ln, tid: (state.__setitem__('prev', ln), got.append(tid))[-1],
        notify=lambda m: None)

    _fire(fig, ax, (0.15, 0.06))          # right where the curves bunch
    assert len(got) <= 1, f'convergence click cycled through {len(got)} tracks'
    import matplotlib.pyplot as plt
    plt.close(fig)


def test_REAL_event_repeated_convergence_clicks_CYCLE_one_at_a_time():
    """The requested UX, through the real event machinery: clicking the dense region repeatedly walks
    the stack of overlapping tracks — one selection per click, all distinct — instead of refusing."""
    fig, ax, l2t = _real_fig_with_curves(30)
    state = {'prev': None}
    got = []
    ap._connect_nearest_curve_click(
        fig, ax, l2t, state,
        lambda ln, tid: (state.__setitem__('prev', ln), got.append(tid))[-1],
        notify=lambda m: None)

    for _ in range(4):
        _fire(fig, ax, (0.15, 0.06))         # same spot, repeatedly
    assert len(got) == 4, 'a click failed to select (the over-eager refusal is back)'
    assert len(set(got)) == 4, f'clicks did not cycle to distinct tracks: {got}'
    import matplotlib.pyplot as plt
    plt.close(fig)
