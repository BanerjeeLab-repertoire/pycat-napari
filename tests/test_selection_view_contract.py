"""**The view-adapter contract, and a reference adapter that satisfies it (interaction-layer Gap 5).**

`SelectionView` is the small contract every linked view meets — `view_id`, `apply_selection(state)`,
`close()` — so the dispatcher drives them all the same. `ProgrammaticGuard` is the primary echo
defence (a view rendering a selection must not emit a new command), and `register_view` pushes the
current state so a newly-opened view reflects the active selection. These pin the mechanism and prove
a reference adapter passes the shared `assert_selection_view_contract`.
"""

import pytest

from pycat.utils.selection_service import (
    ProgrammaticGuard, SelectionService, SelectionState, SelectionView, register_view)
from tests.selection_view_contract import assert_selection_view_contract

pytestmark = pytest.mark.core


def _service():
    return SelectionService(defer=lambda fn: fn())


class _FakeView:
    """A minimal SelectionView: a stand-in widget whose 'selection changed' signal fires synchronously
    whenever its state is set — exactly the shape (selectRow / marker-move / curve-restyle) the guard
    exists for."""
    view_id = 'fake.plot'

    def __init__(self, service):
        self.service = service
        self.guard = ProgrammaticGuard()
        self.rendered = ()

    def apply_selection(self, state):
        with self.guard.applying():                 # programmatic — must not emit a command
            self.rendered = tuple(sorted(state.selected))
            self._widget_changed()

    def _widget_changed(self):
        # the widget's outbound signal: emit a command UNLESS this is a programmatic apply / busy.
        if self.guard.is_applying or self.service.is_busy:
            return
        if self.rendered:
            self.service.select_entity(self.rendered[0], source=self.view_id)

    def user_click(self, entity):                   # simulate a real user selecting a row/curve
        self.rendered = (entity,)
        self._widget_changed()

    def close(self):
        self.service.unsubscribe(self.view_id)


# ── the mechanism ────────────────────────────────────────────────────────────

def test_ProgrammaticGuard_is_reentrant():
    g = ProgrammaticGuard()
    assert not g.is_applying
    with g.applying():
        assert g.is_applying
        with g.applying():                          # nested apply
            assert g.is_applying
        assert g.is_applying, "the inner apply cleared the flag the outer one still needs"
    assert not g.is_applying


def test_register_view_pushes_the_CURRENT_state_so_a_new_view_reflects_it():
    """A plot opened while a track is already selected should show that selection, not a blank one."""
    s = _service()
    s.select_entity('C:/a.tif/op/cell/0/1', source='table')     # something already selected
    v = register_view(s, _FakeView(s))
    assert v.rendered == ('C:/a.tif/op/cell/0/1',), "the view did not receive the current state on open"
    assert 'fake.plot' in s._subscribers


def test_registering_does_not_bounce_a_command_back():
    """The initial apply is programmatic — opening a view must not itself emit a selection."""
    s = _service()
    heard = []
    s.subscribe('other', lambda st: heard.append(st.source_view))
    s.select_entity('e1', source='table')
    heard.clear()
    register_view(s, _FakeView(s))
    assert heard == [], "registering a view emitted a command"


# ── the shared contract, on the reference adapter ────────────────────────────

def test_a_fake_view_isinstance_of_the_protocol():
    assert isinstance(_FakeView(_service()), SelectionView)


def test_the_reference_adapter_passes_the_contract():
    s = _service()
    assert_selection_view_contract(
        s,
        make_view=lambda: register_view(s, _FakeView(s)),
        do_user_select=lambda v: v.user_click('C:/a.tif/op/cell/0/9'),
        an_entity='C:/a.tif/op/cell/0/2',
        other_state=SelectionState(selected=frozenset({'x'}), primary='x'))
