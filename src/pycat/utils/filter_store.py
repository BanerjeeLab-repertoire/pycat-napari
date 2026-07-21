"""**FilterStore — the active analytical population, kept SEPARATE from selection.**

Two questions that were tangled together:

    Selection: which entities am I examining?                        (transient attention)
    Filter:    which entities are in the active analytical population? (the analysed set)

`SelectionState` answers the first (selected / hovered / pinned / cohort). This answers the second, on its
**own channel**. The separation is the entire point, and it is enforced, not merely conventional:

1. **Selection never mutates the filter, and filtering never mutates the selection.** Clicking a plot
   point changes `SelectionState`; it must NOT touch the `FilterStore`. (Pinned as two tests, both
   directions — the spec's reason to exist.)
2. **A filter is explicit and NAMED.** It carries its `predicate` string so a view can always say
   *"showing 214 of 1,032 objects: area > 12 µm²"*. An anonymous filtered set is a black box.
3. **Filtering is opt-in and reversible.** Applying changes the analysed population; clearing restores
   everything (``population()`` → ``None`` = no filter = all objects). No brush/click may apply a filter
   implicitly — only an explicit filter action.
4. **The filter IS the population the analysis sees.** When active, downstream aggregation operates on
   ``population()`` and the result RECORDS that a filter was applied (predicate + count), so a filtered
   result is never mistaken for an unfiltered one.

Relationship to comparative phenotyping: a "condition" is arguably a filter, but the two are **not merged
here** — `FilterStore` is the general mechanism; comparative phenotyping keeps its own grouping. A future
increment may unify them deliberately (not by accident).
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Filter:
    """A named, explicit restriction of the analysed population. ``predicate`` is human-readable
    ("area > 12 µm²"), ``members`` the resolved entity ids, ``source`` the control/action that created it,
    ``active`` whether it is currently applied."""
    predicate: str
    members: frozenset = frozenset()
    source: str = ''
    active: bool = True

    @property
    def n(self) -> int:
        return len(self.members)


class FilterStore:
    """Holds the active analytical population as explicit, named, reversible state — on its own change
    channel, entirely separate from the `SelectionService`. Nothing here reads or writes selection state."""

    def __init__(self):
        self._filter: Filter | None = None
        self._subscribers: dict = {}

    # ── change channel (analogous to SelectionService, but its OWN) ──────────────────────────────
    def subscribe(self, sub_id, callback):
        self._subscribers[str(sub_id)] = callback

    def unsubscribe(self, sub_id):
        self._subscribers.pop(str(sub_id), None)

    def _publish(self):
        for cb in list(self._subscribers.values()):
            try:
                cb(self._filter)
            except Exception:      # broad-ok: one bad filter subscriber must not break the others
                pass

    # ── the population API ───────────────────────────────────────────────────────────────────────
    def set_filter(self, f: Filter) -> None:
        """Apply a filter (explicitly — only a filter action calls this, never a plot interaction)."""
        self._filter = f
        self._publish()

    def clear(self) -> None:
        """Restore the full population."""
        self._filter = None
        self._publish()

    def current(self) -> "Filter | None":
        return self._filter

    def population(self):
        """The active population as a ``frozenset`` of entity ids, or ``None`` — None means NO filter,
        i.e. everything (never confuse 'no filter' with 'an empty population')."""
        if self._filter is None or not self._filter.active:
            return None
        return frozenset(self._filter.members)

    def is_active(self) -> bool:
        return self._filter is not None and self._filter.active


# ── four-tier rendering (emphasis order) + honest filtered results ───────────────────────────────

def resolve_render_tier(entity_id, *, selection=None, filter_store=None) -> str:
    """One of ``'pinned' | 'selected' | 'excluded' | 'filtered_in'`` for an entity, in EMPHASIS order:
    pinned (highest) beats selected beats the population tiers. ``excluded`` = not in the active
    population; ``filtered_in`` = in it (or no filter) but not selected — the low-emphasis baseline. A
    view that does not understand filtering simply ignores this and renders everything as before."""
    eid = str(entity_id)
    pinned = {str(x) for x in (getattr(selection, 'pinned', ()) or ())}
    selected = {str(x) for x in (getattr(selection, 'selected', ()) or ())}
    if eid in pinned:
        return 'pinned'
    if eid in selected:
        return 'selected'
    pop = filter_store.population() if filter_store is not None else None
    if pop is not None and eid not in pop:
        return 'excluded'
    return 'filtered_in'


def filtered_result_note(filter_store, n_total):
    """The record a filtered result must carry so it is never mistaken for an unfiltered one: the
    predicate and the population/total counts. ``None`` when no filter is active (an unfiltered result
    carries no such note)."""
    f = filter_store.current() if filter_store is not None else None
    if f is None or not f.active:
        return None
    return dict(filtered=True, predicate=f.predicate,
                n_population=len(f.members), n_total=int(n_total))


def filter_table(table, filter_store, *, id_col):
    """Restrict ``table`` to the active population by entity id, returning ``(table, note)``. When no
    filter is active, returns the table UNCHANGED and ``note=None`` — it never silently filters, and the
    note announces the restriction when one applies. The input table is not mutated."""
    pop = filter_store.population() if filter_store is not None else None
    if pop is None or id_col not in getattr(table, 'columns', ()):
        return table, None
    mask = table[id_col].astype(str).isin(pop)
    return table[mask].reset_index(drop=True), filtered_result_note(filter_store, len(table))
