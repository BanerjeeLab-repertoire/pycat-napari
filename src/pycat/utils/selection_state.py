"""**Selection as a STATE — hover, multi-select, and pinning, independent and immutable.**

Today a selection is one object with a string ``mode``, so there is no way to build a comparison set by
ctrl-click, to pin a track while exploring another, or to "clear the selection but keep the pins." This
models selection as an immutable `SelectionState` carrying four independent facets — the ``selected`` set,
its ``primary``, an independent ``hovered``, and a ``pinned`` set — plus the ``generation`` counter that
already drives dispatch. Every command returns a NEW state (one generation later), so it is trivially
publishable as one object and free of the in-place-mutation bugs a live selection is prone to.

Pure and Qt-free: this is the state model. Wiring it into ``selection_service`` behind the existing
subscriber contract (back-compat preserved) is the integration step; this pins the semantics first.
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class SelectionState:
    """The whole selection, as one immutable value. Entity ids are ``EntityKey.as_column_value()`` strings.

    ``selected`` — the active set; ``primary`` — the one within it a single-object view highlights;
    ``hovered`` — a transient pointer highlight, INDEPENDENT of ``selected``; ``pinned`` — a set that
    survives ``clear``; ``generation`` — increments once per change (reuse the dispatch counter)."""
    selected: frozenset = frozenset()
    primary: "str | None" = None
    hovered: "str | None" = None
    pinned: frozenset = frozenset()
    generation: int = 0

    def _next(self, **changes) -> "SelectionState":
        return dataclasses.replace(self, generation=self.generation + 1, **changes)

    def select(self, entity) -> "SelectionState":
        """Replace the selection with just ``entity`` (it becomes primary). Hover and pins are untouched."""
        return self._next(selected=frozenset({entity}), primary=entity)

    def toggle(self, entity) -> "SelectionState":
        """Ctrl-click: add ``entity`` to the selection, or remove it if already there. When the removed one
        was primary, primary falls back to another selected member (or None if the set empties)."""
        if entity in self.selected:
            remaining = self.selected - {entity}
            primary = self.primary if self.primary in remaining else next(iter(remaining), None)
            return self._next(selected=remaining, primary=primary)
        return self._next(selected=self.selected | {entity}, primary=entity)

    def hover(self, entity) -> "SelectionState":
        """Set (or clear, with ``None``) the transient hover — never disturbs ``selected``."""
        return self._next(hovered=entity)

    def pin(self, entity) -> "SelectionState":
        return self._next(pinned=self.pinned | {entity})

    def unpin(self, entity) -> "SelectionState":
        return self._next(pinned=self.pinned - {entity})

    def clear(self) -> "SelectionState":
        """Escape: empty ``selected`` and ``hovered``, but KEEP ``pinned`` — pins survive a clear."""
        return self._next(selected=frozenset(), primary=None, hovered=None)

    @property
    def displayed(self) -> frozenset:
        """Everything a view must show: the union of selected and pinned (the set an inbound selection must
        be able to render even if it is outside a representative sample — see interaction_layer Gap 3)."""
        return self.selected | self.pinned
