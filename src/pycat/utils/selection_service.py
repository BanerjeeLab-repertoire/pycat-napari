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
from contextlib import contextmanager
from typing import Protocol, runtime_checkable

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


@dataclasses.dataclass(frozen=True)
class SelectionState:
    """**The interaction state — hover, selection, and pins — as ONE immutable value.**

    A superset of the old single-object ``Selection``: ``selected`` can hold several entities
    (ctrl-click a comparison set), ``pinned`` survives a ``clear`` (Escape keeps pins), and
    ``hovered`` is independent of both. The service publishes the whole state per change, so a view
    sees hover and pins, not just a lone selection.

    It also **quacks like ``Selection``** — ``entity_ids`` / ``primary_id`` / ``source_view`` /
    ``is_empty`` — so every subscriber written against the old dispatch keeps working unchanged. That
    back-compat is mandatory: the dock, the VPT views and the plots all read those attributes.
    """

    selected: frozenset = frozenset()
    primary: str | None = None
    hovered: str | None = None
    pinned: frozenset = frozenset()
    generation: int = 0
    source_view: str = ''            # who caused THIS state — skipped when propagating (echo guard)

    # ── back-compat: the old Selection read interface ─────────────────────────────────────
    @property
    def entity_ids(self) -> tuple:
        """The selected ids, primary first then the rest sorted — a stable order for the tuple the
        old subscribers expect (a lone selection is just ``(id,)``)."""
        head = (self.primary,) if self.primary in self.selected else ()
        return head + tuple(sorted(e for e in self.selected if e != self.primary))

    @property
    def primary_id(self) -> str | None:
        return self.primary

    @property
    def mode(self) -> str:
        return 'selected'

    @property
    def is_empty(self) -> bool:
        return not self.selected


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
        self._state = SelectionState()
        self._busy = False
        self._generations = itertools.count(1)
        self._defer = defer if defer is not None else _qt_defer
        self._debounce = debounce if debounce is not None else _qt_debounce
        self._pending: SelectionState | None = None

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
    def selected(self):
        """The current selection as a back-compat object (reads like the old ``Selection``), or
        ``None`` when nothing is selected."""
        return self._state if self._state.selected else None

    @property
    def state(self) -> SelectionState:
        """The full interaction state — hover, selection, pins — that adapters read."""
        return self._state

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
        """Back-compat entry: dispatch a single ``Selection``. An empty selection is a no-op (as it
        always was). Folds the ``Selection`` into the richer state, keeping the current pins/hover,
        then publishes."""
        if selection is None or selection.is_empty:
            return False
        return self._publish(SelectionState(
            selected=frozenset(selection.entity_ids), primary=selection.primary_id,
            hovered=self._state.hovered, pinned=self._state.pinned,
            generation=selection.generation, source_view=selection.source_view))

    def _publish(self, state: SelectionState) -> bool:
        """Set the state and hand it to every subscriber **except** its ``source_view``.

        The **delayed release** is the load-bearing part (VPT's guard, which ``SelectionHub``
        dropped): the busy flag clears only after the Qt queue drains, because the propagation below
        makes programmatic changes (``selectRow``, ``dims.current_step``, ``camera.center``) whose
        signals fire *after* this returns. A synchronous ``finally`` would not cover them — they
        re-enter and cascade, the "jumps all over the place" loop. (The old ``select`` docstring's
        note that VPT's second "echo guard" was dead code still holds; only the busy guard matters.)
        """
        if self._busy:
            return False
        self._busy = True
        try:
            self._state = state
            for view_id, handle in list(self._subscribers.items()):
                if view_id == state.source_view:
                    continue          # a view never re-highlights from its own action
                callback = handle()
                if callback is None:
                    # The view was garbage collected — drop it rather than keep calling a ghost.
                    self._subscribers.pop(view_id, None)
                    continue
                try:
                    callback(state)
                except Exception as exc:
                    debug_log(f'selection: the "{view_id}" view failed to handle a selection', exc)
            # The expensive half is coalesced: only the LAST state of a burst is worth an image read.
            if self._deferred_subscribers:
                self._pending = state
                self._debounce(self._flush_deferred)
        finally:
            self._defer(self._release)          # the delayed release — see above
        return True

    # ── state commands (each produces a new state and publishes it) ───────────────────────
    def select_entity(self, entity_id, source=''):
        """Make ``entity_id`` the whole selection (pins survive)."""
        return self._publish(dataclasses.replace(
            self._state, selected=frozenset({entity_id}), primary=entity_id,
            generation=self.next_generation(), source_view=source))

    def toggle(self, entity_id, source=''):
        """Add/remove ``entity_id`` from the selection — ctrl-click to build a comparison set."""
        sel = set(self._state.selected) ^ {entity_id}
        primary = entity_id if entity_id in sel else next(iter(sel), None)
        return self._publish(dataclasses.replace(
            self._state, selected=frozenset(sel), primary=primary,
            generation=self.next_generation(), source_view=source))

    def hover(self, entity_id, source=''):
        """Set the hovered entity (independent of selection; ``None`` clears hover)."""
        return self._publish(dataclasses.replace(
            self._state, hovered=entity_id,
            generation=self.next_generation(), source_view=source))

    def pin(self, entity_id, source=''):
        """Pin an entity so it survives a ``clear_selection`` (explore another without losing it)."""
        return self._publish(dataclasses.replace(
            self._state, pinned=self._state.pinned | {entity_id},
            generation=self.next_generation(), source_view=source))

    def unpin(self, entity_id, source=''):
        return self._publish(dataclasses.replace(
            self._state, pinned=self._state.pinned - {entity_id},
            generation=self.next_generation(), source_view=source))

    def clear_selection(self, source=''):
        """Escape's semantics: clear selected + hovered, **keep pins**."""
        return self._publish(dataclasses.replace(
            self._state, selected=frozenset(), primary=None, hovered=None,
            generation=self.next_generation(), source_view=source))

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
        Pins that name the closed dataset are dropped too; pins on other datasets survive.
        """
        current = self._state
        if not current.selected or not dataset_id:
            return False
        prefix = f"{dataset_id}/"
        if any(str(e).startswith(prefix) for e in current.entity_ids):
            self._state = SelectionState(
                pinned=frozenset(p for p in current.pinned if not str(p).startswith(prefix)))
            return True
        return False

    def clear(self):
        """Full reset (no dispatch) — the lifecycle drop, e.g. on a data switch. For Escape's
        keep-the-pins clear that DOES notify views, use ``clear_selection``."""
        self._state = SelectionState()


# ═══════════════════════════════════════════════════════════════════════════════════════════
# The view-adapter contract — every linked view behaves the same, and can be tested the same
# ═══════════════════════════════════════════════════════════════════════════════════════════


@runtime_checkable
class SelectionView(Protocol):
    """**What a linked view must be**, so the dispatcher can drive any of them the same way.

    Subscribers used to be bare callbacks with no shared contract, so each view re-invented apply /
    suppress / cleanup and they drifted. A ``SelectionView`` is the small contract instead:

    * ``view_id`` — names the view, so the service can skip it when propagating its own action
      (echo-suppression);
    * ``apply_selection(state)`` — render a :class:`SelectionState`. This is a **programmatic** update,
      so it must NOT emit a command back (guard it with :class:`ProgrammaticGuard`);
    * ``close()`` — disconnect mpl cids / Qt signals and unsubscribe, so a closed view stops being
      driven and stops driving.

    A new adapter (e.g. the pyqtgraph plot backend) is built against THIS, and verified with
    :func:`assert_selection_view_contract` — not the old bare-callback API.
    """
    view_id: str

    def apply_selection(self, state: SelectionState) -> None: ...

    def close(self) -> None: ...


class ProgrammaticGuard:
    """A re-entrancy flag for the **primary** echo defence: while a view applies a selection
    *programmatically* (``selectRow``, moving a marker, restyling a curve), the change signals that
    fires must not be read as a fresh user action and emit a new command.

    ``with guard.applying(): <render>`` raises the flag; the view's OUTBOUND handler checks
    ``guard.is_applying`` and bails. This is the review's §5 rule; the service's ``source_view``
    suppression stays as the second line of defence. Re-entrant (depth-counted) so nested applies are
    safe.
    """

    def __init__(self):
        self._depth = 0

    @property
    def is_applying(self) -> bool:
        return self._depth > 0

    @contextmanager
    def applying(self):
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1


def register_view(service, view):
    """Subscribe a :class:`SelectionView` and immediately push the CURRENT state, so a view opened
    while something is already selected reflects it (a fresh plot should show the active selection,
    not a blank one). The initial apply is programmatic, so it emits no command. Returns ``view``."""
    service.subscribe(view.view_id, view.apply_selection)
    try:
        view.apply_selection(service.state)
    except Exception as exc:
        debug_log(f'selection: view "{getattr(view, "view_id", "?")}" failed its initial apply', exc)
    return view


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
