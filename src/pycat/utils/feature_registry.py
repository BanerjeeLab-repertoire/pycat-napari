"""**The single answer to "what can PyCAT do, and where?"**

Many shipped capabilities have no UI home — biological QC, measurement stability, the ontology,
feature-provenance, analysis presets, scan QC, the figure spec, spectral unmixing. A user cannot discover
what they cannot see. This is the registry every capability registers a `FeatureCard` with, so a future
feature becomes discoverable the moment it registers one card — no navigator surgery.

It is a **catalogue, not a launcher UI**: it holds cards and answers which are visible in the current
:mod:`app_mode`. A beginner sees the beginner-visible set; an advanced user sees everything. The card's
`entry` is an opaque callable the eventual home dock invokes — the registry never calls it, so this module
stays Qt-free and `core`-testable.
"""
from __future__ import annotations

import dataclasses

from pycat.utils.app_mode import AppMode, current_mode


@dataclasses.dataclass(frozen=True)
class FeatureCard:
    """One discoverable capability. ``min_mode`` is the LOWEST mode that may see it — BEGINNER = everyone,
    ADVANCED = advanced users only. ``entry`` is an opaque callable the home dock launches (the registry
    never invokes it)."""
    key: str                               # unique id, e.g. 'spectral_unmixing'
    title: str                             # 'Spectral / Bleed-through Unmixing'
    summary: str                           # one line: what it does
    category: str                          # grouping, e.g. 'Correct' / 'Measure' / 'Segment'
    entry: object = None                   # callable to open it, or None
    docs_anchor: str | None = None         # a docs link/anchor
    min_mode: AppMode = AppMode.BEGINNER

    def visible_in(self, mode) -> bool:
        """Whether this card is shown in ``mode`` — visible once the mode is at least ``min_mode``."""
        return AppMode(mode).rank >= self.min_mode.rank


class FeatureRegistry:
    """A keyed catalogue of `FeatureCard`s. Registration is idempotent-safe: re-registering the same key
    raises, so two features cannot silently claim one id."""

    def __init__(self):
        self._cards: dict = {}

    def register(self, card: FeatureCard) -> FeatureCard:
        if not isinstance(card, FeatureCard):
            raise TypeError('register expects a FeatureCard')
        if card.key in self._cards:
            raise ValueError(f"a FeatureCard is already registered under key {card.key!r}")
        self._cards[card.key] = card
        return card

    def get(self, key):
        return self._cards.get(key)

    def __contains__(self, key):
        return key in self._cards

    def __len__(self):
        return len(self._cards)

    def all(self):
        """Every registered card, in registration order."""
        return list(self._cards.values())

    def categories(self):
        """The distinct categories present, in first-seen order."""
        seen = []
        for c in self._cards.values():
            if c.category not in seen:
                seen.append(c.category)
        return seen

    def visible_in(self, mode):
        """Every card visible in ``mode`` (registration order)."""
        return [c for c in self._cards.values() if c.visible_in(mode)]

    def visible_now(self, store=None):
        """Every card visible in the CURRENT app mode (see :func:`app_mode.current_mode`)."""
        return self.visible_in(current_mode(store))

    def by_category(self, mode=None):
        """``{category: [cards]}`` — all cards, or only those visible in ``mode`` when given. Categories in
        first-seen order; cards in registration order within each."""
        cards = self._cards.values() if mode is None else self.visible_in(mode)
        grouped: dict = {}
        for c in cards:
            grouped.setdefault(c.category, []).append(c)
        return grouped


_REGISTRY = None


def registry() -> FeatureRegistry:
    """The process-wide feature registry (created on first use)."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = FeatureRegistry()
    return _REGISTRY


def register_feature(card: FeatureCard) -> FeatureCard:
    """Register ``card`` in the process-wide registry (convenience for `registry().register`)."""
    return registry().register(card)
