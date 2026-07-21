"""**Status-marker colour logic — GREEN MEANS DONE, and 'ready' can never be misread as done.**

The tester's report turned on one confusion: a step that was merely *ready* to run looked identical to
one that had *run* (both solid green). These pin the fixed decision (`utils.marker_logic.resolve_marker`,
Qt-free so it runs in headless core): readiness is a distinct OUTLINED state, green/blue mean the step
actually ran, and a not-done marker is NEVER solid green.
"""
import pytest

from pycat.utils.marker_logic import resolve_marker

pytestmark = pytest.mark.core


def test_done_required_is_solid_green():
    color, filled, tip = resolve_marker(done=True, optional=False, ready=False)
    assert color == 'green' and filled is True
    assert 'done' in tip.lower()


def test_done_optional_is_solid_blue_with_explicit_meaning():
    color, filled, tip = resolve_marker(done=True, optional=True, ready=False)
    assert color == 'blue' and filled is True
    # Fix 2: blue's meaning is made explicit, not left to guesswork.
    assert 'optional' in tip.lower() and 'done' in tip.lower()


def test_ready_is_a_DISTINCT_outlined_state_not_green():
    color, filled, tip = resolve_marker(done=False, optional=False, ready=True)
    assert color == 'ready', "readiness must not reuse the 'green' key"
    assert color != 'green'
    assert filled is False, "ready must render OUTLINED so it can't be misread as solid-green done"
    assert 'not' in tip.lower() and 'run' in tip.lower()   # tooltip says it has NOT run yet


def test_resting_required_is_red_and_optional_is_yellow():
    assert resolve_marker(done=False, optional=False, ready=False)[0] == 'red'
    assert resolve_marker(done=False, optional=True, ready=False)[0] == 'yellow'


def test_done_beats_ready_precedence():
    # A completed step is green/blue regardless of readiness — done wins.
    assert resolve_marker(done=True, optional=False, ready=True)[0] == 'green'
    assert resolve_marker(done=True, optional=True, ready=True)[0] == 'blue'


def test_the_core_invariant_a_not_done_marker_is_NEVER_solid_green():
    """Green (solid) is reserved for DONE. No combination of not-done inputs may produce solid green —
    this is the whole bug the tester found."""
    for optional in (False, True):
        for ready in (False, True):
            color, filled, _ = resolve_marker(done=False, optional=optional, ready=ready)
            assert not (color == 'green' and filled), (
                f"not-done produced solid green (optional={optional}, ready={ready})")


def test_only_ready_is_outlined():
    """`filled=False` is the ready state's signature and nothing else's — the ring is what makes ready
    visually unmistakable from every solid (done/resting) state."""
    for done in (False, True):
        for optional in (False, True):
            for ready in (False, True):
                color, filled, _ = resolve_marker(done=done, optional=optional, ready=ready)
                assert (filled is False) == (color == 'ready'), (
                    f"only 'ready' may be outlined (done={done}, optional={optional}, ready={ready})")
