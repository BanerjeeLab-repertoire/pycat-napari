"""**One handle on every open method widget's field registry — so a workspace CLEAR resets them all.**

Each toolbox UI builder creates its own ``FieldRegistry`` (``ui/field_status.py``) to drive the status
circles and per-field reset. There was no way for the CLEAR path to find them all — each registry was an
island only its builder saw — so clearing the workspace left the previous workflow's spin boxes, dropdowns
and status circles populated, not the "~fresh open" state the user expects (`session_clear_reset` Bug 2).

This is the missing central handle: every ``FieldRegistry`` registers here on construction, and
``_clear_everything`` calls ``reset_all()`` to return every open method widget to its defaults.

**Qt-free on purpose.** It holds registries WEAKLY and calls their ``.reset_all()``, nothing more — so the
clear plumbing (and its tests) never drag in PyQt5, and a closed widget's registry drops out on its own
(no leak, no resetting a dead widget). Which fields reset is `FieldRegistry.reset_all`'s decision (OPTIONAL
and EXPERT return to defaults; a REQUIRED value the user supplied, with no default, is left alone).
"""
from __future__ import annotations

import weakref

from pycat.utils.general_utils import debug_log


class FieldRegistryHub:
    """A weak collection of the live ``FieldRegistry`` instances, resettable as one on a workspace clear."""

    def __init__(self):
        self._registries = []                 # list[weakref.ref]

    def register(self, registry):
        """Register a field registry (idempotent). Held weakly — a closed widget's registry drops out on
        its own, so the hub never keeps a dead widget alive or resets one."""
        self._prune()
        if not any(r() is registry for r in self._registries):
            self._registries.append(weakref.ref(registry))
        return registry

    def reset_all(self) -> int:
        """Reset every LIVE registry to its defaults (the "~fresh open" state) and drop dead ones. One
        registry's reset failing does NOT stop the others. Returns how many registries were reset."""
        reset = 0
        for ref in list(self._registries):
            reg = ref()
            if reg is None:
                continue
            try:
                reg.reset_all()
                reset += 1
            except Exception as exc:  # broad-ok: one widget's reset must not block clearing the rest of the workspace
                debug_log('FieldRegistryHub: a registry reset failed', exc)
        self._prune()
        return reset

    def _prune(self):
        self._registries = [r for r in self._registries if r() is not None]

    def __len__(self) -> int:
        self._prune()
        return len(self._registries)


_ACTIVE = FieldRegistryHub()


def active_field_registries() -> FieldRegistryHub:
    """The process-wide hub that the CLEAR path resets and every ``FieldRegistry`` registers with."""
    return _ACTIVE


def register_field_registry(registry):
    """Register ``registry`` with the active hub — called by ``FieldRegistry.__init__``, so coverage is
    automatic: a new method widget's fields reset on clear without the builder wiring anything."""
    return _ACTIVE.register(registry)
