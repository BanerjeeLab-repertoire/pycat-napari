# Claude Code spec — Brushing increment 3: promote VPT's dispatcher into a `SelectionService`

## ✅ STATUS — DONE, shipped in 1.6.75 (executed against the 1.6.74 tree)
`pytest -m core`: **661 passed, 2 skipped** (was 640). Definition of done met: `SelectionService`
exists on `CentralManager` with echo suppression, re-entrancy guard, weak-ref subscriptions and
dataset invalidation; VPT emits through it and its three views are subscribers; a generic plot shares
the same dispatcher. VPT's behaviour is **proven** preserved, not asserted — see below.

**The biggest problem with this spec was not a wrong line number: the thing it asks you to refactor
"behaviour-preservingly" had ZERO tests.** Nothing in `tests/` mentioned `_select_track` or
`_sel_busy`. "Because the guard/suppression code is the SAME code (moved, not rewritten), VPT's
three-way link must behave identically" is an argument, not evidence, and the one thing a refactor of
a working, untested, production feature must not be is unverifiable. So
`tests/test_vpt_selection_characterization.py` was written **first**, against the un-refactored code,
and is **unchanged** by the extraction — that file passing before and after is the actual proof.

**Three premises that did not survive the tree:**
1. **VPT's "echo guard" is DEAD CODE.** `if self._selected_track_id == tid and self._sel_busy:
   return` sits directly above `if self._sel_busy: return` — the first is the second plus an extra
   condition, so it can never fire independently. Its docstring claims it *"kills the common echo"*;
   that is not what the code does. **Lifting it "VERBATIM" would have carried a dead branch and a
   false claim into a new module.** Dropped — behaviour-preserving by construction, since a branch
   that cannot fire cannot be missed. (Implementing the docstring's intent would be a real behaviour
   change — re-clicking the selected object would become a no-op — i.e. a product decision, so it
   was not made here.)
2. **`SelectionHub` is not merely "unused with perf bugs" — it had lost the guard that matters.** Its
   `select()` ends in `finally: self._busy = False`, the synchronous release VPT's docstring
   explicitly documents as insufficient. It would have oscillated the first time a real Qt view was
   wired to it, and its one test passes anyway (the `source` skip alone carries that case). The spec
   ignores it; leaving it would have left a trap with a green test. It is now a shim over the
   service, so there is **one** dispatcher rather than three.
3. **"Keys are the increment-2 EntityKey-derived ids" needed a bridge that did not exist.** VPT keys
   on a raw `track_id`, and tracks are not one of the three tables increment 2 stamps — there was no
   `track_id → EntityKey` route. VPT now names its tracks via `entity_ref` (`…/vpt/track/<id>`).

**Also:** it is `QTimer.singleShot(0, …)` — the release is *posted behind* the queue, not a drain;
`_sel_busy` had two other readers (Qt handlers that early-out before doing work) which now read
`service.is_busy`; and the spec's "(callback list, weak refs)" describes an idiom that does not
exist — `_data_switch_callbacks` is a plain list of **strong** refs with a bare `except: pass`. Weak
refs were implemented as asked, with the necessary care: `WeakMethod` for bound methods, **strong for
lambdas/closures**, because a `weakref.ref` to a lambda dies the instant `subscribe` returns and the
registry then silently never fires. The closed-dock case the weakness exists for *is* the
bound-method case.

**Left for increment 5, deliberately:** no production plot passes a service yet —
`plot_focus_diagnostic` and friends have no `central_manager` in scope, and threading it through is
the adapter work increment 5 owns. The seam (`SelectionHub`/`make_pickable`'s `hub=`) is wired and
tested; only the call sites are pending.

**Date:** 2026-07-16 · **Target tree:** verified against 1.6.70. **PREREQUISITE: increments 1 and 2
landed** (identity seed + `EntityRef` foundation). Re-validate line numbers when you start. The core
move: extract VPT's PROVEN selection machinery into one application-level service — **promote, don't
replace**. Touches `vpt_ui.py`, `central_manager.py`, a new service module; not `file_io.py`.

## Why promote rather than build new
An audit proposed a new central `SelectionService` and migrating VPT onto it. Inverted here, because
the tree says the OPPOSITE of the audit's assumption: **VPT's dispatcher is the mature, efficient one;
the generic `SelectionHub` is the one that's unused in production and has the perf bugs.** Verified —
`vpt_ui.py:65 _select_track` already has:
- re-entrancy / echo guard (`_sel_busy`, `vpt_ui.py:85–118`),
- source-view suppression (`source=` arg so a view doesn't echo its own selection),
- delayed guard release (drains the Qt event queue before clearing `_sel_busy`),
- direct `track_id → row` lookup + a blit highlight path (redraw only changed artists).
This is the strongest brushing implementation in PyCAT. So EXTRACT it into the service; don't reinvent.

CentralManager already has the idiom to slot into: `central_manager.py:71 self._data_switch_callbacks`
+ `register_data_switch_callback`. Mirror that (callback list, weak refs) — do NOT build a new event
bus framework.

## Part A — `SelectionService` (new module, owned by CentralManager)
```python
@dataclass(frozen=True)
class Selection:
    entity_ids: tuple[str, ...]      # EntityKey-derived stable ids (increment 2)
    primary_id: str | None
    mode: str                        # "hover" | "selected" | "pinned"
    source_view: str                 # who emitted it — for suppression
    generation: int                  # monotonic; stale callbacks ignored

class SelectionService:
    def select(self, selection: Selection): ...          # dispatch to subscribers != source_view
    def subscribe(self, view_id, callback): ...          # weak-ref
    def unsubscribe(self, view_id): ...
    def invalidate_dataset(self, dataset_id): ...         # drop selections when a dataset closes
```
Lift the guard/suppression/coalescing logic out of `_select_track` VERBATIM into the service (the
`_sel_busy` re-entrancy guard, the source suppression, the delayed release). Own it on CentralManager
next to `_data_switch_callbacks`. Selections are keyed by the increment-2 stable `entity_ids`, so a
selection survives sort/filter/reload.

## Part B — refactor VPT to CALL the service (behaviour preserved by construction)
`_select_track` becomes a thin adapter: it builds a `Selection(entity_ids=(track_key,),
source_view="vpt", ...)` and calls `SelectionService.select(...)`; VPT's own table/plot/points
highlight becomes a SUBSCRIBER callback. Because the guard/suppression code is the SAME code (moved,
not rewritten), VPT's three-way link (MSD curve ↔ table ↔ bead) must behave identically. Verify: the
existing VPT brushing still works — same echo-free, same blitting.

## Part C — one generic subscriber as proof
Wire ONE non-VPT plot (e.g. a partition scatter that now carries entity ids from increment 2) as a
subscriber, so selecting a point emits through the SAME service and could highlight elsewhere. This
proves the service generalizes beyond VPT without yet building all the adapters (that's increment 5).

## Steps
1. New `utils/selection_service.py` (or `navigator/`—put it where CentralManager can own it):
   `Selection` + `SelectionService`, guard/suppression logic lifted from `_select_track`.
2. Instantiate it on CentralManager; expose `central_manager.selection` + register/invalidate hooks
   mirroring `_data_switch_callbacks`.
3. Refactor `_select_track` to emit via the service; VPT's highlights become subscribers.
4. Wire one generic plot subscriber as the generalization proof.
5. Tests (`core`, no Qt where possible): `test_selection_service.py` — echo suppression (a source
   view doesn't receive its own selection), re-entrancy guard (no infinite loop when two views echo),
   generation/staleness (an old callback is ignored), dataset invalidation drops selections. Plus:
   VPT's existing brushing tests stay green (the promotion is behaviour-preserving).
6. Full `pytest -m core` green (esp. any VPT brushing test + complexity budget).
7. Ship: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (increment 3: VPT
   dispatcher promoted to a shared SelectionService; VPT behaviour preserved).

## Definition of done
- `SelectionService` exists on CentralManager with echo suppression, re-entrancy guard, coalescing,
  weak-ref subscriptions, dataset invalidation — the logic LIFTED from VPT, not rewritten.
- VPT's `_select_track` now emits through the service; its three-way link behaves identically.
- One generic plot subscribes and selects through the same service.
- Full `pytest -m core` green.

## Cautions
- **Promote, don't replace.** The guard/suppression/blit logic must be MOVED verbatim — a rewritten
  guard that's subtly different is the failure mode. Diff VPT's behaviour before/after.
- Use the `_data_switch_callbacks` idiom (callback list, weak refs); do NOT introduce a new event-bus
  dependency.
- Keys are the increment-2 `EntityKey`-derived ids — do NOT reintroduce row-position or raw
  `track_id`-only selection (that's what sort/filter breaks).
- Don't build the table adapter / linked-selection dock (increment 5) or the scaling fixes
  (increment 4) here — just the service + VPT promotion + one proof subscriber.
