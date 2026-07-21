"""**A brushable plot cleans up after itself — figures, callbacks and refs do not accumulate.**

`plot_lifecycle` Parts A/B: `make_pickable` (the one integration every brushable plot uses) connected two
canvas callbacks, stored an overlay artist and a `LazyRefs` sequence on the figure, and had NO teardown —
so a long session piled up open figures, live `mpl_connect` handlers and ref caches (the audit's
">20 figures" finding). These pin the teardown: `dispose_pickable` disconnects the tracked cids, removes
the overlay, drops the refs and closes the figure; it is idempotent; it is wired to the figure's own
`close_event`; and N open→dispose cycles return the open-figure count to baseline.

Part C (the `SelectionService` subscriber-leak safety net) is pinned separately in
`test_selection_lifecycle.py`; the `make_pickable` path is outbound-only (plot → selection), so it holds
no service subscription to leak.
"""
import types

import matplotlib
matplotlib.use('Agg')          # headless: no Qt, no display — figures live only in memory
import matplotlib.pyplot as plt
import pytest

from pycat.utils.brushing import make_pickable, dispose_pickable, _teardown_pickable

pytestmark = pytest.mark.core


class _Ref:
    """A stand-in ObjectRef — make_pickable only indexes the sequence and hands an element to on_select."""
    def __init__(self, i): self.i = i


def _pickable_fig(on_select=None):
    fig, ax = plt.subplots()
    artist = ax.scatter([0, 1, 2], [0, 1, 2], picker=5)
    refs = [_Ref(0), _Ref(1), _Ref(2)]
    make_pickable(fig, artist, refs, on_select=on_select)
    return fig, ax, artist


def _fire_pick(fig, artist, index):
    """Dispatch a synthetic pick_event through the canvas's callback registry — the same path a real
    mouse pick takes, so a disconnected cid genuinely stops receiving it."""
    event = types.SimpleNamespace(
        artist=artist, ind=[index], canvas=fig.canvas,
        mouseevent=types.SimpleNamespace(key='', dblclick=False))
    fig.canvas.callbacks.process('pick_event', event)


def test_a_pick_reaches_the_handler_before_dispose():
    got = []
    fig, ax, artist = _pickable_fig(on_select=lambda ref: got.append(ref))
    try:
        _fire_pick(fig, artist, 1)
        assert len(got) == 1 and got[0].i == 1, "the pick handler should fire while the plot is live"
    finally:
        plt.close(fig)


def test_dispose_disconnects_the_canvas_callbacks():
    got = []
    fig, ax, artist = _pickable_fig(on_select=lambda ref: got.append(ref))
    _fire_pick(fig, artist, 0)
    assert len(got) == 1
    dispose_pickable(fig)                     # the view's dispose()
    _fire_pick(fig, artist, 0)                # a later event on the same (now torn-down) canvas
    assert len(got) == 1, "a disposed view's handler was still invoked — the callback was not disconnected"


def test_dispose_closes_the_figure_and_returns_to_baseline():
    base = set(plt.get_fignums())
    fig, ax, artist = _pickable_fig()
    assert set(plt.get_fignums()) != base       # the new figure is open
    dispose_pickable(fig)
    assert set(plt.get_fignums()) == base, "dispose did not close the figure — figures accumulate"


def test_dispose_removes_the_overlay_and_drops_the_refs():
    fig, ax, artist = _pickable_fig()
    _fire_pick(fig, artist, 2)                 # creates the one-point selection overlay
    state = fig._pycat_brush_state
    assert state.get('overlay') is not None
    overlay = state['overlay']
    dispose_pickable(fig, close_figure=False)
    assert state.get('overlay') is None, "the overlay artist was not released"
    assert overlay not in ax.collections, "the overlay artist is still on the axes after dispose"
    assert fig._pycat_object_refs is None, "the LazyRef sequence was not dropped"
    plt.close(fig)


def test_dispose_is_idempotent():
    fig, ax, artist = _pickable_fig()
    dispose_pickable(fig)
    dispose_pickable(fig)                      # a close signal can fire twice — must not throw
    _teardown_pickable(fig)                    # and directly, on an already-torn-down figure
    assert fig._pycat_object_refs is None


def test_close_event_is_wired_to_teardown():
    """Part B: teardown is wired to the figure's OWN close_event, so a closed window cleans up even
    without an explicit dispose call. A GUI backend dispatches `close_event` through the canvas callback
    registry when the user closes the window; we dispatch it the same way (Agg has no window to close),
    which is the faithful simulation of that close."""
    fig, ax, artist = _pickable_fig()
    assert 'close_event' in fig.canvas.callbacks.callbacks, "no close_event handler was wired"
    assert fig._pycat_object_refs is not None
    fig.canvas.callbacks.process('close_event', types.SimpleNamespace(canvas=fig.canvas))
    assert fig._pycat_object_refs is None, "the close_event handler did not run teardown"
    plt.close(fig)


def test_open_close_cycles_do_not_grow_the_open_figure_count():
    """The leak test the audit's finding demands, in figure terms: N open→dispose cycles return the
    open-figure count to baseline (it does not grow with N)."""
    base = len(plt.get_fignums())
    for _ in range(30):
        fig, ax, artist = _pickable_fig()
        _fire_pick(fig, artist, 1)             # exercise the overlay path too
        dispose_pickable(fig)
    assert len(plt.get_fignums()) == base, "open figures accumulated across open/close cycles — a leak"


def test_dispose_is_safe_on_a_never_pickable_figure():
    """A figure make_pickable never touched has no brush state — teardown must be a clean no-op."""
    fig = plt.figure()
    try:
        _teardown_pickable(fig)                # no _pycat_brush_* attributes — must not raise
        dispose_pickable(fig, close_figure=False)
    finally:
        plt.close(fig)


# ── the histogram-cohort brushing (wired into Feature Explorer) leaks a CLOSURE subscription ───────
# `apply_selection` there is a closure, held STRONGLY by the service — the weak-method net (Part C) does
# NOT catch it. Its `dispose` is the explicit unsubscribe + cid disconnect that keeps the subscriber list
# from growing as the user switches columns in the dock.
def _service():
    from pycat.utils.selection_service import SelectionService
    return SelectionService(defer=lambda fn: fn(), debounce=lambda fn: fn())


def _attach_histogram(svc, view_id='histogram'):
    import numpy as np
    from pycat.utils.cohort_targets import attach_histogram_brushing
    fig, ax = plt.subplots()
    vals = np.array([0.1, 0.5, 0.9, 1.4, 1.8])
    eids = np.array(['d/1', 'd/2', 'd/3', 'd/4', 'd/5'])
    counts, edges, bars = ax.hist(vals, bins=4)
    handles = attach_histogram_brushing(fig, ax, vals, eids, bin_edges=edges,
                                        selection_service=svc, view_id=view_id, bars=bars)
    return fig, handles


def test_histogram_brushing_dispose_unsubscribes_the_closure():
    svc = _service()
    base = svc.subscriber_count()
    fig, handles = _attach_histogram(svc)
    assert svc.subscriber_count() == base + 1, "the histogram view should be subscribed while live"
    handles['dispose']()
    assert svc.subscriber_count() == base, "the closure subscription leaked — dispose did not unsubscribe"
    handles['dispose']()                       # idempotent — a second close must not throw
    plt.close(fig)


def test_switching_columns_does_not_accumulate_histogram_subscriptions():
    """The dock reuses one figure and re-attaches per column; dispose-before-reattach keeps the count
    flat instead of leaving a cid + subscription behind on every switch."""
    svc = _service()
    base = svc.subscriber_count()
    for _ in range(20):                        # 20 column switches on the same view_id
        fig, handles = _attach_histogram(svc, view_id='feature_explorer_histogram')
        handles['dispose']()
        plt.close(fig)
    assert svc.subscriber_count() == base, "histogram subscriptions accumulated across column switches"
