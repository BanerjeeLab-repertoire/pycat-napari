"""**One physical click is ONE selection — via button_press, not per-line pick_event.**

An audit of the first fix found the debounce intrinsically fragile: it assumed every `pick_event` from
one mouse press arrives before a zero-delay timer fires, which is not a safe contract, so a click could
still resolve several tracks. The mechanism was replaced: the MSD lines are non-pickable, and one
canvas-level `button_press_event` handler selects the single nearest curve. matplotlib fires exactly
one button-press per click, so there is nothing to batch — the failure mode is gone by construction.

These tests drive the REAL handler (`button_press_event`) directly, one call per click, and cover the
cases the audit named: one press → one track; a dense-overlap click → at most one; empty space → none;
already-selected → none; nearest by SEGMENT not vertex; and the ambiguity refusal.
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


def _wire(line_to_tid, ax, *, radius_px=8.0, ambiguity_px=3.0):
    fig = _Fig()
    state = {'prev': None}
    got = []

    def _apply(ln, tid):
        state['prev'] = ln
        got.append(tid)

    notes = []
    ap._connect_nearest_curve_click(fig, ax, line_to_tid, state, _apply,
                                    radius_px=radius_px, ambiguity_px=ambiguity_px,
                                    notify=notes.append)
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

def test_ONE_click_in_a_dense_overlap_selects_at_most_ONE():
    """The bug: near the origin many curves converge. One button_press must yield one selection."""
    ax = object()
    # 20 curves all passing through (10,10), fanning out
    lines = {_Line([10, 10 + i], [10, 10 + 2 * i]): i for i in range(20)}
    fig, state, got, notes = _wire(lines, ax, ambiguity_px=0.0)   # disable ambiguity for this check
    fig.canvas.click(ax, 10, 10)
    assert len(got) <= 1, f'one click selected {len(got)} tracks'


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


# ── the ambiguity refusal ────────────────────────────────────────────────────────

def test_it_REFUSES_to_guess_when_two_curves_are_indistinguishable():
    """Where the two nearest are within the ambiguity margin, do not pick an arbitrary track — ask
    for a click at a less crowded spot."""
    ax = object()
    a = _Line([10, 11], [10, 11])
    b = _Line([10, 11], [10, 11])           # coincident with a at the click point
    fig, state, got, notes = _wire({a: 1, b: 2}, ax, ambiguity_px=3.0)
    fig.canvas.click(ax, 10, 10)
    assert got == []
    assert notes and 'overlap' in notes[0].lower()


def test_a_clearly_nearest_curve_is_NOT_blocked_by_ambiguity():
    ax = object()
    a = _Line([0, 1], [0, 1])               # right at the click
    b = _Line([40, 41], [40, 41])           # far — unambiguous
    fig, state, got, notes = _wire({a: 1, b: 2}, ax, ambiguity_px=3.0)
    fig.canvas.click(ax, 0, 0)
    assert got == [1] and notes == []


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
