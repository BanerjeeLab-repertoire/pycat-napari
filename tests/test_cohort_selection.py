"""**Cohort selection — a GROUP as a typed target, carrying the definition that makes it honest.**

`SelectionState` gained a `cohort` field (a `Cohort` with members + a human-readable definition + a
kind). A histogram bin, a box/violin condition, or an aggregate row selects a *set* of entities; a bare
id-set loses *why* they are grouped, so the cohort carries that.

Additive by construction: `select_cohort` also fills `selected` with the members, so a cohort-UNAWARE
view (reading only `selected`) degrades gracefully and still highlights them, while a cohort-aware view
reads `cohort` for the definition and count. And a cohort is a SELECTION, never a FILTER — it must not
mutate the data or the analysed population.
"""
import pandas as pd
import pytest

from pycat.utils.selection_service import SelectionService, Cohort

pytestmark = pytest.mark.base


def _service():
    # `defer=lambda fn: fn()` collapses the delayed busy-release (Qt-queue in production) to synchronous,
    # so successive commands are not swallowed by the busy guard in a headless test.
    return SelectionService(defer=lambda fn: fn(), debounce=lambda fn: fn())


def test_cohort_round_trip_carries_members_definition_and_kind():
    svc = _service()
    seen = []
    svc.subscribe('aware', lambda st: seen.append(st.cohort))
    c = Cohort(members=frozenset({'a', 'b', 'c'}), definition='area in [12,18) um2', kind='bin')
    svc.select_cohort(c, source='hist')
    assert seen and seen[-1].members == frozenset({'a', 'b', 'c'})
    assert seen[-1].definition == 'area in [12,18) um2' and seen[-1].kind == 'bin' and seen[-1].n == 3


def test_a_cohort_unaware_view_degrades_gracefully():
    """A view that reads only `selected` (never touched cohorts) must still highlight every member — the
    contract that lets a cohort selection ship without breaking existing adapters."""
    svc = _service()
    highlighted = []
    svc.subscribe('legacy', lambda st: highlighted.append(set(st.selected)))
    svc.select_cohort(Cohort(members=frozenset({'a', 'b', 'c'}), definition='g=WT'), source='box')
    assert highlighted[-1] == {'a', 'b', 'c'}          # members via `selected`, no cohort awareness needed


def test_emitting_view_does_not_receive_its_own_cohort():
    svc = _service()
    got = []
    svc.subscribe('box', lambda st: got.append(st))
    svc.subscribe('other', lambda st: None)
    svc.select_cohort(Cohort(members=frozenset({'x'}), definition='g=WT'), source='box')
    assert got == []                                    # echo-suppressed by source_view, like every command


def test_clear_drops_cohort_and_selected_and_hovered_but_keeps_pins():
    svc = _service()
    svc.pin('p1')
    svc.select_cohort(Cohort(members=frozenset({'a', 'b'}), definition='g=WT'), source='box')
    svc.clear_selection()
    st = svc._state
    assert st.cohort is None and not st.selected and st.hovered is None
    assert 'p1' in st.pinned                            # pins survive, matching Escape semantics


def test_a_single_selection_clears_the_cohort():
    svc = _service()
    svc.select_cohort(Cohort(members=frozenset({'a', 'b'}), definition='g=WT'), source='box')
    svc.select_entity('z')
    assert svc._state.cohort is None and set(svc._state.selected) == {'z'}


def test_cohort_selection_never_mutates_the_data():
    """Selection ≠ filtering. Selecting a cohort must leave the underlying DataFrame — the analysed
    population — untouched. Emit a cohort built FROM a frame and assert the frame is byte-identical."""
    df = pd.DataFrame({'entity_id': ['a', 'b', 'c'], 'area': [10.0, 15.0, 16.0]})
    before = df.copy(deep=True)
    svc = _service()
    in_bin = df.loc[df['area'] >= 15.0, 'entity_id']
    svc.select_cohort(Cohort(members=frozenset(in_bin), definition='area >= 15', kind='bin'), source='hist')
    pd.testing.assert_frame_equal(df, before)           # the frame is untouched — a selection, not a filter
