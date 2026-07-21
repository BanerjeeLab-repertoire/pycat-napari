"""**Feature registry — the single catalogue of "what can PyCAT do, and where," filtered by app mode.**

Pins: a card registers and is retrievable; a duplicate key is refused (two features cannot claim one id); a
beginner sees only beginner-visible cards while an advanced user sees everything; and cards group by
category.
"""
import pytest

from pycat.utils.app_mode import AppMode
from pycat.utils.feature_registry import FeatureCard, FeatureRegistry

pytestmark = pytest.mark.core


def _card(key, *, category='Measure', min_mode=AppMode.BEGINNER, entry=None):
    return FeatureCard(key=key, title=key.title(), summary=f'does {key}', category=category,
                       min_mode=min_mode, entry=entry)


def test_a_card_registers_and_is_retrievable():
    r = FeatureRegistry()
    called = []
    r.register(_card('unmix', category='Correct', entry=lambda: called.append(1)))
    assert 'unmix' in r and len(r) == 1
    card = r.get('unmix')
    assert card.title == 'Unmix' and card.category == 'Correct'
    card.entry()                                          # the registry never calls entry; a consumer does
    assert called == [1]


def test_a_duplicate_key_is_refused():
    r = FeatureRegistry()
    r.register(_card('x'))
    with pytest.raises(ValueError, match='already registered'):
        r.register(_card('x'))


def test_a_beginner_sees_only_beginner_cards_advanced_sees_all():
    r = FeatureRegistry()
    r.register(_card('basic', min_mode=AppMode.BEGINNER))
    r.register(_card('expert', min_mode=AppMode.ADVANCED))
    assert {c.key for c in r.visible_in(AppMode.BEGINNER)} == {'basic'}
    assert {c.key for c in r.visible_in(AppMode.ADVANCED)} == {'basic', 'expert'}


def test_cards_group_by_category_in_first_seen_order():
    r = FeatureRegistry()
    r.register(_card('a', category='Correct'))
    r.register(_card('b', category='Measure'))
    r.register(_card('c', category='Correct'))
    assert r.categories() == ['Correct', 'Measure']
    grouped = r.by_category()
    assert [c.key for c in grouped['Correct']] == ['a', 'c']
    assert [c.key for c in grouped['Measure']] == ['b']


def test_by_category_can_filter_to_a_mode():
    r = FeatureRegistry()
    r.register(_card('a', category='Correct', min_mode=AppMode.BEGINNER))
    r.register(_card('b', category='Correct', min_mode=AppMode.ADVANCED))
    assert [c.key for c in r.by_category(AppMode.BEGINNER)['Correct']] == ['a']
    assert [c.key for c in r.by_category(AppMode.ADVANCED)['Correct']] == ['a', 'b']


def test_visible_in_is_the_card_predicate_too():
    assert _card('x', min_mode=AppMode.ADVANCED).visible_in(AppMode.BEGINNER) is False
    assert _card('x', min_mode=AppMode.ADVANCED).visible_in(AppMode.ADVANCED) is True
    assert _card('x', min_mode=AppMode.BEGINNER).visible_in(AppMode.BEGINNER) is True
