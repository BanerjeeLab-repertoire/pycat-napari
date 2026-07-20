"""**Plot/view lifecycle — subscriptions must not accumulate across a long session.**

The audit found >20 matplotlib figures and a growing `SelectionService` subscriber list over a session:
each selection broadcast walks that list, so an unbounded list is the lag source. The service already
holds bound-method subscribers WEAKLY (a closed dock's method dies) and drops dead handles on broadcast;
this pins the self-defense the leak finding demands: a `subscriber_count()` that returns to baseline
across open/close cycles, dead subscribers pruned proactively, a broadcast that never calls a gone view,
and idempotent unsubscribe (close events fire twice).

The per-view `dispose()` (disconnect canvas callbacks + close the figure) is the UI half; this is the
service-level safety net that catches a missed one.
"""
import gc

import pytest

from pycat.utils.selection_service import SelectionService

pytestmark = pytest.mark.core


def _svc():
    return SelectionService(defer=lambda fn: fn(), debounce=lambda fn: fn())


def test_subscriber_count_returns_to_baseline_across_open_close_cycles():
    """The leak test: N open→dispose cycles must not grow the subscriber list."""
    svc = _svc()
    base = svc.subscriber_count()
    for i in range(50):
        svc.subscribe(f'plot_{i}', lambda st: None)     # a closure — held strongly, must be unsubscribed
        svc.unsubscribe(f'plot_{i}')                     # the view's dispose()
    assert svc.subscriber_count() == base, "subscriptions accumulated across open/close — a leak"


def test_a_closed_view_bound_method_is_pruned_from_the_count():
    """A Qt dock's bound method is held weakly; when the dock is gone, the count drops even without an
    explicit unsubscribe — the safety net for a missed dispose."""
    svc = _svc()

    class _View:
        def on_selection(self, state):
            pass

    v = _View()
    svc.subscribe('dock', v.on_selection)
    assert svc.subscriber_count() >= 1
    del v
    gc.collect()
    assert svc.subscriber_count(include_deferred=False) == 0, "a closed view's subscription leaked"


def test_broadcasting_after_a_view_is_gone_does_not_call_the_dead_subscriber():
    svc = _svc()
    calls = []

    class _View:
        def on_selection(self, state):
            calls.append(state)

    v = _View()
    svc.subscribe('dock', v.on_selection)
    svc.select_entity('a', source='other')             # live view is called
    assert len(calls) == 1
    del v
    gc.collect()
    svc.select_entity('b', source='other')             # gone view must NOT be called
    assert len(calls) == 1, "a broadcast called a garbage-collected subscriber"


def test_double_unsubscribe_is_idempotent():
    """Close events can fire more than once — a second dispose must not throw."""
    svc = _svc()
    svc.subscribe('v', lambda st: None)
    svc.unsubscribe('v')
    svc.unsubscribe('v')                                # no KeyError
    assert svc.subscriber_count() == 0


def test_the_deferred_channel_is_counted_and_pruned_too():
    svc = _svc()

    class _View:
        def on_reveal(self, state):
            pass

    v = _View()
    svc.subscribe_deferred('reveal', v.on_reveal)
    assert svc.subscriber_count(include_deferred=True) == 1
    assert svc.subscriber_count(include_deferred=False) == 0
    del v
    gc.collect()
    assert svc.subscriber_count(include_deferred=True) == 0
