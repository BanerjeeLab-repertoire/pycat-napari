"""**One object is selected. Every view that cares hears about it — exactly once.**

── Why this is a PROMOTION, not a new design ───────────────────────────────────────────────

PyCAT already had two implementations of this idea, and the good one was the one nobody could
reuse:

* ``vpt_ui._select_track`` — the **mature** dispatcher. Three-way linked selection (MSD curve ↔
  table ↔ bead) that works, in production, on real data. But its view list is *hardcoded* to
  ``'plot' | 'image' | 'table'``, so nothing outside VPT can join it.
* ``brushing.SelectionHub`` — the **generic** one, written as a lift of the above. Its own docstring
  says so. It is keyed on an ``ObjectRef`` rather than a ``track_id``, which is the right
  generalisation — **and it has never been used in production.**

The lift dropped the two guards that matter, and that is not a coincidence: they are invisible until
a real Qt view echoes. ``vpt_ui``'s docstring spells out what the hub is missing:

    *"Several of those emit Qt/napari signals ASYNCHRONOUSLY — they fire after this method has
    already returned — so a synchronous busy-flag that resets in `finally` does NOT cover them, and
    the queued signals re-enter here and cascade (the 'jumps all over the place' loop)."*

``SelectionHub.select`` ends in ``finally: self._busy = False``. **That is precisely the bug VPT
documents having fixed** — a hub that looks correct, passes a synchronous test, and oscillates the
moment a real Qt view is wired to it. It survived only because it was never wired to one.

So this module is VPT's dispatcher, generalised: the subscriber registry from the hub, and the
**echo guard** and **delayed release** from VPT. ``SelectionHub`` becomes a shim over it, so there
is one implementation rather than three.

── What is selected ────────────────────────────────────────────────────────────────────────

Selections are keyed on the increment-2 stable ``entity_ids`` (``EntityKey``-derived), never on row
position and never on a bare ``track_id``. That is what lets a selection survive a sort, a filter, or
a reload — the failure the whole brushing plan exists to remove.
"""

from __future__ import annotations

import dataclasses
import itertools
import weakref

from pycat.utils.general_utils import debug_log


@dataclasses.dataclass(frozen=True)
class Selection:
    """**What is selected, who said so, and when.**

    ``source_view`` is not decoration: it is what stops the highlight this selection causes in view
    B from firing B's own emit and coming straight back.
    """

    entity_ids: tuple[str, ...] = ()
    primary_id: str | None = None
    mode: str = 'selected'           # "hover" | "selected" | "pinned"
    source_view: str = ''            # who emitted it — skipped when propagating
    generation: int = 0              # monotonic; a stale callback can tell it lost the race

    @property
    def is_empty(self) -> bool:
        return not self.entity_ids


def _as_callable(callback):
    """A handle to ``callback``: **weak if it has an owner, strong if it does not.**

    ── Both halves of this are bugs I had to be shown ─────────────────────────────────────

    A plain ``weakref.ref`` to a **bound method** is dead immediately: the bound method object is
    created fresh on each attribute access, so nothing else holds it. Every weak-callback registry
    gets written that way once, and the symptom is that subscribers silently never fire.
    ``WeakMethod`` is the fix — it holds the *instance* weakly and rebuilds the bound method.

    But a **lambda or closure is weak-referenceable and equally unowned**, so taking a weak ref to
    one dies just as fast, and *that* trap is invisible because it looks like it worked. So they are
    held strongly: a callback with no owner cannot outlive one, and a subscriber that never fires is
    worse than a subscriber that lingers.

    The point of weakness here is the **closed dock** — a Qt view whose method was subscribed and
    which the user then closed. That case is exactly the bound-method case, and it is covered.
    A caller holding a closure is responsible for ``unsubscribe``.
    """
    if hasattr(callback, '__self__') and hasattr(callback, '__func__'):
        try:
            return weakref.WeakMethod(callback)
        except TypeError:
            pass
    return lambda: callback


class SelectionService:
    """**The dispatcher, owned by CentralManager** — one selection, many views.

    Mirrors the ``_data_switch_callbacks`` idiom already on CentralManager (a callback list, fired
    in a copy, each in its own ``try``, a failing view never taking the others down). It holds
    subscribers **weakly**, unlike that list: a plot dock that is closed must not be kept alive by
    the fact that it once wanted to hear about selections.
    """

    def __init__(self, defer=None, debounce=None):
        self._subscribers: dict[str, object] = {}
        self._deferred_subscribers: dict[str, object] = {}
        self._selected: Selection | None = None
        self._busy = False
        self._generations = itertools.count(1)
        self._defer = defer if defer is not None else _qt_defer
        self._debounce = debounce if debounce is not None else _qt_debounce
        self._pending: Selection | None = None

    # ── subscription ──────────────────────────────────────────────────────────────────────
    def subscribe(self, view_id, callback):
        """``callback(selection)`` whenever something *else* selects. Re-subscribing replaces."""
        self._subscribers[str(view_id)] = _as_callable(callback)
        return self

    def subscribe_deferred(self, view_id, callback):
        """``callback(selection)`` for the **expensive** half of a selection — the one that reads
        pixels (a crop, an offline resolve, a reveal).

        ── Why two kinds of subscriber ────────────────────────────────────────────────────

        Dragging across a scatter, or holding an arrow key down a table, emits a burst of
        selections. The **cheap** feedback (moving a marker, selecting a row) must land on every
        one of them or the UI feels dead — it costs microseconds. The **expensive** one must not:
        resolving the image for every intermediate point means reading a file per hover, and the
        only one the user will ever look at is the last.

        So these fire on a trailing debounce, for the most recent selection only. Everything before
        it is superseded before it was worth doing.
        """
        self._deferred_subscribers[str(view_id)] = _as_callable(callback)
        return self

    def unsubscribe(self, view_id):
        self._subscribers.pop(str(view_id), None)
        self._deferred_subscribers.pop(str(view_id), None)
        return self

    @property
    def selected(self) -> Selection | None:
        return self._selected

    @property
    def is_busy(self) -> bool:
        """**Mid-propagation.** A view may check this to bail out *before* doing work.

        `select()` suppresses re-entrant calls itself, so this is an optimisation, not the guard —
        but it is the one VPT's Qt handlers already relied on (they read `_sel_busy` before even
        reading the table row). Exposing it keeps those early-outs alive instead of leaving them
        reading a flag nothing sets any more.
        """
        return self._busy

    def next_generation(self) -> int:
        return next(self._generations)

    # ── dispatch ──────────────────────────────────────────────────────────────────────────
    def select(self, selection: Selection):
        """Dispatch to every subscriber **except** ``selection.source_view``.

        ── One guard, not two: VPT's "echo guard" is dead code ────────────────────────────

        ``vpt_ui._select_track`` opens with what reads as two guards::

            if self._selected_track_id == tid and self._sel_busy:   # (1) "the echo guard"
                return
            if self._sel_busy:                                      # (2) the busy guard
                return

        **(1) can never fire a return that (2) would not** — it is (2) plus an extra condition, so
        it is strictly subsumed. Its docstring claims *"if the requested track is already the
        selected one, do nothing (kills the common echo)"*, which would be a real second guard —
        but that is not what the code does, because it also requires ``_sel_busy``.

        Lifting it "verbatim" would carry a dead branch into a new module and, worse, carry the
        claim with it. Dropping it is **behaviour-preserving by construction** (a branch that
        cannot fire cannot be missed). Implementing what the docstring *says* would be a real
        behaviour change — re-clicking the selected object would become a no-op — and that is a
        product decision, not a refactor, so it is not made here.

        What IS load-bearing is the **delayed release**: the busy flag clears only after the Qt
        event queue drains, because the propagation below makes programmatic changes
        (``selectRow``, ``dims.current_step``, ``camera.center``) whose signals fire *after* this
        returns. A synchronous ``finally`` does not cover them — they re-enter and cascade. That is
        the guard ``SelectionHub`` dropped, and why it oscillates the moment a real Qt view is
        wired to it.
        """
        if selection is None or selection.is_empty:
            return False

        if self._busy:
            return False

        self._busy = True
        try:
            self._selected = selection
            for view_id, handle in list(self._subscribers.items()):
                if view_id == selection.source_view:
                    continue          # a view never re-highlights from its own action
                callback = handle()
                if callback is None:
                    # The view was garbage collected — drop it rather than keep calling a ghost.
                    self._subscribers.pop(view_id, None)
                    continue
                try:
                    callback(selection)
                except Exception as exc:
                    debug_log(f'selection: the "{view_id}" view failed to handle a selection', exc)
            # The expensive half is coalesced: only the LAST selection of a burst is worth an
            # image read, and the user only ever sees that one.
            if self._deferred_subscribers:
                self._pending = selection
                self._debounce(self._flush_deferred)
        finally:
            # (2) the delayed release. See the docstring — this is the difference between the
            # working dispatcher and the one that oscillates.
            self._defer(self._release)
        return True

    def _flush_deferred(self):
        """Run the expensive subscribers for the most recent selection, and drop the rest."""
        selection, self._pending = self._pending, None
        if selection is None:
            return
        for view_id, handle in list(self._deferred_subscribers.items()):
            if view_id == selection.source_view:
                continue
            callback = handle()
            if callback is None:
                self._deferred_subscribers.pop(view_id, None)
                continue
            try:
                callback(selection)
            except Exception as exc:
                debug_log(f'selection: the "{view_id}" view failed to resolve a selection', exc)

    def _release(self):
        self._busy = False

    # ── lifecycle ─────────────────────────────────────────────────────────────────────────
    def invalidate_dataset(self, dataset_id):
        """Drop the selection when the dataset it names goes away.

        A selection outliving its data is a click that resolves to whatever now sits at that id —
        the same class of wrongness as row-position matching. The ids are ``EntityKey`` column
        values, which begin with the dataset id (see `entity_ref.EntityKey.as_column_value`).
        """
        current = self._selected
        if current is None or not dataset_id:
            return False
        prefix = f"{dataset_id}/"
        if any(str(e).startswith(prefix) for e in current.entity_ids):
            self._selected = None
            return True
        return False

    def clear(self):
        self._selected = None


def _qt_defer(fn):
    """Run ``fn`` once the Qt event queue has drained; synchronously if there is no Qt.

    The fallback matters for headless use (and for tests): without a running event loop a
    ``singleShot`` would never fire and the service would stay busy forever, silently swallowing
    every subsequent selection.
    """
    try:
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, fn)
    except Exception:
        fn()


_DEBOUNCE_MS = 30      # a hover burst coalesces; a deliberate click still feels immediate


def _qt_debounce(fn):
    """Run ``fn`` after a short trailing delay, restarting the clock on each call.

    30 ms is below the threshold where a click feels laggy and comfortably above the interval a
    drag or a held arrow key emits at, so a burst collapses to one image read.

    Without Qt this runs inline: there is no event loop to trail behind, and a debounce that never
    fires is a feature that never happens (the same trap as the deferred release).
    """
    try:
        from PyQt5.QtCore import QTimer
    except Exception:
        fn()
        return

    timer = getattr(_qt_debounce, '_timer', None)
    if timer is None:
        timer = QTimer()
        timer.setSingleShot(True)
        _qt_debounce._timer = timer
    try:
        timer.timeout.disconnect()
    except Exception:
        pass
    timer.timeout.connect(fn)
    timer.start(_DEBOUNCE_MS)
