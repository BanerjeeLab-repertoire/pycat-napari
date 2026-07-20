> **✅ STATUS — Part C (service self-defense) DONE, shipped in 1.6.187.** The verified state was already
> better than the premise: `SelectionService` holds bound-method subscribers WEAKLY (`_as_callable` →
> `WeakMethod`, so a closed dock's method dies) and drops dead handles on broadcast. Added `subscriber_count()`
> (live count, dead handles pruned) + `_prune_dead()` (proactive sweep), and the leak test
> (`tests/test_selection_lifecycle.py`): 50 open→dispose cycles return to baseline, a GC'd view is never
> broadcast to, idempotent unsubscribe, deferred channel counted too. **Follow-on** (the UI half, Parts A/B):
> a per-view `dispose()` that also `mpl_disconnect`s canvas callbacks and `plt.close`s the figure, wired to
> dock/dialog close signals — the weak-method design already prevents the subscription leak; this remains for
> the figure/callback accumulation.

# Claude Code spec — Plot/view lifecycle cleanup

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. The audit found
*">20 matplotlib figures remained open"* during the test run and identified accumulating figures,
callbacks, `SelectionService` subscriptions, and overlay artists as a slow-session risk. This spec
gives every plot/dock an explicit teardown so long sessions do not degrade.

## The problem (verified)
`SelectionService` has `subscribe`/`unsubscribe` (`selection_service.py:185/208`) — so the mechanism to
detach exists. But a grep for teardown paths (`plt.close`, `mpl_disconnect`, `unsubscribe` calls) finds
only ~2 sites. So subscriptions and figures are created far more often than they are cleaned up. Each
plot window accumulates:
- a matplotlib figure (the >20-open warning);
- canvas event callbacks (`mpl_connect` handlers);
- a `SelectionService` subscription (never unsubscribed);
- `LazyRef` sequences and overlay artists.

Individually each selection is efficient (the audit confirms the overlay-artist optimisation works),
but the *accumulation* across a long session is the lag source — subscriptions pile up and every
selection broadcast walks a growing subscriber list.

## Design — one teardown contract, called on close
### Part A — a disposable protocol for views
```python
class SelectionView(Protocol):        # the existing brushing view contract
    def dispose(self) -> None: ...
```
Every plot window, dock, and brushable table implements `dispose()`, which:
1. `mpl_disconnect`s every canvas callback it registered;
2. `SelectionService.unsubscribe(view_id)`;
3. drops its `LazyRef` cache and overlay artists;
4. `plt.close(fig)` for its matplotlib figure.

Track what was registered so teardown is exact — store connection ids and the `view_id` on the view,
rather than trying to guess them at close time.

### Part B — wire dispose to the actual close events
- napari dock widgets: connect `dispose` to the dock's close/destroyed signal.
- matplotlib figure windows: connect to the figure's `close_event`.
- The comparative-figure dialog and linked-selection dock: `dispose` on dialog close.

The point the audit stresses: a close must **disconnect events, unsubscribe, drop references, AND close
the figure** — partial teardown (closing the figure but leaving the subscription) is the leak.

### Part C — SelectionService self-defense
Even with disciplined views, make the service robust:
- **Weak references or liveness checks:** when broadcasting, a subscriber whose view is gone should be
  dropped, not called. This prevents a forgotten `dispose` from leaking forever and from calling into a
  half-torn-down widget (a crash risk).
- A `subscriber_count()` accessor so a test can assert subscriptions do not grow across
  open/close cycles.

## Tests (`core` where possible; Qt-smoke where not)
- **The leak test:** open and dispose a plot view N times; assert `SelectionService.subscriber_count()`
  returns to baseline (does not grow with N). This is the test the audit's finding demands.
- `dispose` disconnects canvas callbacks (a disposed view's handler is not invoked on a later event).
- `dispose` closes the matplotlib figure (figure count returns to baseline).
- Broadcasting after a view is gone does not call the dead subscriber (weakref/liveness).
- Double-`dispose` is safe (idempotent) — close events can fire more than once.
- A disposed view's `LazyRef` cache and overlay artists are released.

## Steps
1. Add `dispose()` to the `SelectionView` contract; implement on plot windows, brushable table, docks.
2. Track registered connection ids + `view_id` per view for exact teardown.
3. Wire `dispose` to napari dock close, matplotlib `close_event`, and dialog close.
4. `SelectionService`: liveness-checked broadcast + `subscriber_count()`.
5. Tests above, especially the open/close-N-times leak test.
6. Full `pytest -m core` green (the >20-figure warning should be gone from the suite).
7. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (explicit plot/view teardown;
   subscriptions and figures no longer accumulate).

## Definition of done
- Every plot/dock/table implements `dispose()` that disconnects events, unsubscribes, drops refs, and
  closes its figure.
- `dispose` is wired to the real close/destroy signals.
- `SelectionService` drops dead subscribers on broadcast and exposes `subscriber_count()`.
- Repeated open/close does not grow subscriber count or open-figure count (the leak test).
- The test suite no longer emits the >20-open-figures warning.
- Full `pytest -m core` green.

## Cautions
- **Partial teardown is the bug.** Closing the figure but leaving the subscription (or vice versa) is
  exactly the leak; `dispose` must do all four steps.
- Make `dispose` idempotent — close signals can fire twice, and a second call must not throw.
- Liveness-checked broadcast is the safety net for a missed `dispose`, not a replacement for it — do
  both.
- Do not change selection *behaviour* — this is lifecycle only; a disposed-and-reopened view must brush
  exactly as before.
- Track connection ids explicitly; guessing what to disconnect at close time is fragile.
