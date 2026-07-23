"""**Every shipped-but-hidden capability with a real opener has a discoverable card (navigator inc 4).**

The card-presence enumeration is the safety net the spec names explicitly: a future capability that forgets
to register a card is caught here. Also pins that no card is a placeholder (every card carries a real callable
entry and non-empty title/summary/category), that registration is idempotent-safe, and the mode-visibility.
"""
import pytest

from pycat.utils.app_mode import AppMode
from pycat.utils.feature_registry import FeatureRegistry
from pycat.utils.feature_cards import register_default_feature_cards, EXPECTED_CARD_KEYS

pytestmark = pytest.mark.core


class _FakeCM:
    """The entries close over the central_manager lazily; the registry never invokes them, so a bare stand-in
    is enough to build and register the cards headlessly."""


def _fresh_registry():
    reg = FeatureRegistry()
    register_default_feature_cards(_FakeCM(), reg=reg)
    return reg


def test_every_expected_capability_has_a_registered_card():
    reg = _fresh_registry()
    for key in EXPECTED_CARD_KEYS:
        assert key in reg, f"capability {key!r} lost its feature card — a surfacing regression"
    assert len(reg) == len(EXPECTED_CARD_KEYS), "an unexpected card was registered; update EXPECTED_CARD_KEYS"


def test_no_card_is_a_placeholder():
    """A card must open the REAL feature — 'a card that opens nothing is worse than none'."""
    reg = _fresh_registry()
    for card in reg.all():
        assert card.title and card.summary and card.category, f"{card.key} is missing display metadata"
        assert callable(card.entry), f"{card.key} has no real opener (entry is not callable)"


def test_registration_is_idempotent_safe():
    reg = _fresh_registry()
    n = len(reg)
    register_default_feature_cards(_FakeCM(), reg=reg)      # a second pass on the SAME registry must not raise
    assert len(reg) == n                                    # ...nor duplicate anything


def test_cards_group_into_sensible_categories():
    reg = _fresh_registry()
    assert {"Assess", "Correct", "Measure", "Report"} <= set(reg.categories())


def test_all_default_cards_are_discoverable_by_a_beginner():
    # every surfaced capability is meant to be found by a newcomer, so each defaults to BEGINNER visibility
    reg = _fresh_registry()
    assert len(reg.visible_in(AppMode.BEGINNER)) == len(EXPECTED_CARD_KEYS)
