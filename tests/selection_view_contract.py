"""**Reusable adapter-contract checks** — the highest-value part of interaction-layer Gap 5.

Every linked view (the MSD plot, the VPT table, the napari overlay, the dock, and a NEW plot backend
like pyqtgraph) must pass `assert_selection_view_contract`. An adapter's own test imports this and
passes a factory + a user-action simulator; the checks are the same for all, so views cannot drift.

Not a test module itself (no ``test_`` prefix) — a shared helper.
"""

import dataclasses


def assert_selection_view_contract(service, make_view, do_user_select, an_entity, other_state):
    """Assert the four cross-view invariants.

    service        : a SelectionService (with a synchronous ``defer`` in tests).
    make_view()    : build AND register a SelectionView on ``service`` (via ``register_view``).
    do_user_select(view) : simulate a USER selecting in that view — must emit exactly one command.
    an_entity      : an entity id string the view can be selected to.
    other_state    : a SelectionState to apply programmatically (its entity need not exist in the view).
    """
    from pycat.utils.selection_service import SelectionView

    commands = []
    service.subscribe('__contract_probe__', lambda st: commands.append(st.source_view))

    view = make_view()
    assert isinstance(view, SelectionView), "adapter does not satisfy the SelectionView protocol"
    assert view.view_id and view.view_id != '__contract_probe__'
    assert view.view_id in service._subscribers, "register_view did not subscribe the view"

    # (1) A PROGRAMMATIC apply must NOT emit a command — tested with the service IDLE, so it is the
    #     view's programmatic-update guard doing the work, not the service's busy flag.
    before = len(commands)
    view.apply_selection(other_state)
    assert len(commands) == before, (
        "apply_selection emitted a command — the programmatic-update guard is missing")

    # (2) A USER action emits EXACTLY ONE command, attributed to this view.
    before = len(commands)
    do_user_select(view)
    mine = [s for s in commands[before:] if s == view.view_id]
    assert len(mine) == 1, f"one user action emitted {len(mine)} commands (expected 1)"

    # (3) An UNKNOWN entity applies safely — no crash, no spurious command.
    before = len(commands)
    unknown = dataclasses.replace(other_state, selected=frozenset({'no/such/entity/0/0'}),
                                  primary='no/such/entity/0/0')
    view.apply_selection(unknown)
    assert len(commands) == before, "applying an unknown entity emitted a command"

    # (4) close() unsubscribes; a later external selection neither reaches nor crashes the closed view.
    view.close()
    assert view.view_id not in service._subscribers, "close() did not unsubscribe the view"
    service.select_entity(an_entity, source='__external__')     # must not raise
