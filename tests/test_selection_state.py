"""**Selection state — hover/selected/pinned are independent; clear keeps pins; one command, one generation.**

The interaction-layer Gap 1 semantics, pure and immutable: select/toggle/hover/pin/clear each return a NEW
state one generation later and never mutate the old one; ctrl-click toggles membership; clear empties
selected+hovered but preserves pinned; hover never disturbs selected.
"""
import pytest

from pycat.utils.selection_state import SelectionState

pytestmark = pytest.mark.core


def test_select_replaces_the_set_and_sets_primary():
    s = SelectionState().select('a')
    assert s.selected == frozenset({'a'}) and s.primary == 'a' and s.generation == 1
    s2 = s.select('b')
    assert s2.selected == frozenset({'b'}) and s2.primary == 'b'


def test_toggle_adds_and_removes_and_updates_primary():
    s = SelectionState().toggle('a').toggle('b')          # ctrl-click builds a set
    assert s.selected == frozenset({'a', 'b'}) and s.primary == 'b'
    s = s.toggle('b')                                     # remove the primary
    assert s.selected == frozenset({'a'}) and s.primary == 'a'
    s = s.toggle('a')                                     # empty it
    assert s.selected == frozenset() and s.primary is None


def test_clear_empties_selected_and_hovered_but_KEEPS_pinned():
    s = SelectionState().select('a').hover('h').pin('p')
    c = s.clear()
    assert c.selected == frozenset() and c.primary is None and c.hovered is None
    assert c.pinned == frozenset({'p'}), "pins must survive a clear (Escape keeps pins)"


def test_hover_is_independent_of_selection():
    s = SelectionState().select('a').hover('b')
    assert s.selected == frozenset({'a'}) and s.primary == 'a' and s.hovered == 'b'
    assert s.hover(None).hovered is None and s.hover(None).selected == frozenset({'a'})


def test_pin_and_unpin():
    s = SelectionState().pin('x').pin('y')
    assert s.pinned == frozenset({'x', 'y'})
    assert s.unpin('x').pinned == frozenset({'y'})


def test_every_command_increments_generation_by_exactly_one():
    s = SelectionState()
    for cmd in (lambda x: x.select('a'), lambda x: x.toggle('b'), lambda x: x.hover('c'),
                lambda x: x.pin('d'), lambda x: x.unpin('d'), lambda x: x.clear()):
        before = s.generation
        s = cmd(s)
        assert s.generation == before + 1


def test_the_state_is_immutable_commands_return_a_new_value():
    s = SelectionState().select('a')
    s.select('b')                                        # the return is ignored — s must be unchanged
    assert s.selected == frozenset({'a'})
    with pytest.raises(Exception):
        s.primary = 'z'                                  # frozen


def test_displayed_is_the_union_of_selected_and_pinned():
    s = SelectionState().select('a').pin('p')
    assert s.displayed == frozenset({'a', 'p'})
