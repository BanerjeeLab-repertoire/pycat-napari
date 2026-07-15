"""
registry.py
===========

The module registry. Modules register their :class:`ModuleContract` here; the
interface then *queries* the registry instead of hardcoding menus.

This directly implements PDF2/PDF3's proposal: "the UI doesn't need a giant web
of if/else logic. It can simply ask 'what scientific question are you trying to
answer?' and search the registered modules for those that claim to answer it."

Adding a new method (PDF3's Persistent Homology example) is a single
``registry.register(contract)`` call — no GUI edits.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from .capabilities import Capability
from .contracts import ModuleContract


class ModuleRegistry:
    def __init__(self):
        self._by_name: Dict[str, ModuleContract] = {}

    # -- registration ------------------------------------------------------ #
    def register(self, contract: ModuleContract) -> ModuleContract:
        if contract.name in self._by_name:
            raise ValueError(f"module {contract.name!r} already registered")
        self._by_name[contract.name] = contract
        return contract

    def get(self, name: str) -> ModuleContract:
        return self._by_name[name]

    def all(self) -> List[ModuleContract]:
        return list(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    # -- queries the interface actually uses ------------------------------- #
    def answering(self, question: str) -> List[ModuleContract]:
        """Modules that claim to answer a scientific question."""
        return [m for m in self._by_name.values() if m.can_answer(question)]

    def measuring(self, observable: str) -> List[ModuleContract]:
        """Modules that deliver a given scientific observable. This is the
        primary entry point for the planner: an intent lists observables."""
        return [m for m in self._by_name.values() if m.measures(observable)]

    def providers_of(self, required: Capability) -> List[ModuleContract]:
        """Every module whose output can satisfy ``required``. Ordered
        deterministically by (preference desc, name asc) so planning is
        reproducible (stress-test: determinism requires a fixed tie-break)."""
        hits = [m for m in self._by_name.values()
                if m.provides_capability(required) is not None]
        hits.sort(key=lambda m: (-m.preference, m.name))
        return hits

    def observables_index(self) -> Dict[str, List[str]]:
        idx: Dict[str, List[str]] = defaultdict(list)
        for m in self._by_name.values():
            for o in m.observables:
                idx[o].append(m.name)
        return dict(idx)
