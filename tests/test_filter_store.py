"""**FilterStore — the analytical population, provably separate from selection.**

`FilterStore` holds which entities are in the active analysed set, on its own channel. The load-bearing
property is ISOLATION: selection and filtering must not mutate each other — that is the whole reason the
store exists. These pin it in both directions, plus the population API (None = everything), the four-tier
rendering resolver, the honest filtered-result note (a filtered mean must announce itself), and the
no-implicit-filtering contract (no selection-handler path calls `set_filter`).
"""
import numpy as np
import pandas as pd
import pytest

from pycat.utils.filter_store import (Filter, FilterStore, resolve_render_tier,
                                      filtered_result_note, filter_table)

pytestmark = pytest.mark.base


def _service():
    from pycat.utils.selection_service import SelectionService
    return SelectionService(defer=lambda fn: fn(), debounce=lambda fn: fn())


# ── population API ──────────────────────────────────────────────────────────────────────────────

def test_no_filter_means_everything_and_clearing_restores_it():
    fs = FilterStore()
    assert fs.population() is None and fs.is_active() is False
    fs.set_filter(Filter(predicate='area > 12', members=frozenset({'a', 'b'}), source='ctrl'))
    assert fs.population() == frozenset({'a', 'b'}) and fs.is_active() is True
    fs.clear()
    assert fs.population() is None and fs.is_active() is False   # None = all, not an empty population


def test_an_inactive_filter_is_not_applied():
    fs = FilterStore()
    fs.set_filter(Filter(predicate='x', members=frozenset({'a'}), active=False))
    assert fs.population() is None and fs.is_active() is False


def test_the_filter_carries_its_predicate():
    fs = FilterStore()
    fs.set_filter(Filter(predicate='area > 12 µm²', members=frozenset({'a', 'b', 'c'})))
    assert fs.current().predicate == 'area > 12 µm²' and fs.current().n == 3


def test_a_filter_change_notifies_subscribers_on_its_OWN_channel():
    fs = FilterStore()
    seen = []
    fs.subscribe('view', lambda f: seen.append(f))
    fs.set_filter(Filter(predicate='p', members=frozenset({'a'})))
    fs.clear()
    assert len(seen) == 2 and seen[0].predicate == 'p' and seen[1] is None


# ── the isolation invariant — the spec's reason to exist, both directions ─────────────────────────

def test_a_FILTER_change_leaves_SELECTION_untouched():
    svc = _service()
    fs = FilterStore()
    svc.select_entity('obj1')
    before = svc._state
    fs.set_filter(Filter(predicate='area > 12', members=frozenset({'obj1', 'obj2'})))
    assert svc._state is before, "setting a filter mutated the selection state"
    assert set(svc._state.selected) == {'obj1'}


def test_a_SELECTION_change_leaves_the_FILTER_untouched():
    svc = _service()
    fs = FilterStore()
    fs.set_filter(Filter(predicate='area > 12', members=frozenset({'obj1', 'obj2'})))
    pop_before = fs.population()
    svc.select_entity('obj3')          # a plot click == attention, NOT a population change
    assert fs.population() == pop_before, "a selection change mutated the filter population"
    assert fs.current().predicate == 'area > 12'


# ── four-tier rendering (emphasis order) ─────────────────────────────────────────────────────────

def test_the_four_tiers_resolve_in_emphasis_order():
    fs = FilterStore()
    fs.set_filter(Filter(predicate='p', members=frozenset({'in', 'sel', 'pin'})))

    class _Sel:
        selected = frozenset({'sel'})
        pinned = frozenset({'pin'})

    sel = _Sel()
    assert resolve_render_tier('pin', selection=sel, filter_store=fs) == 'pinned'
    assert resolve_render_tier('sel', selection=sel, filter_store=fs) == 'selected'
    assert resolve_render_tier('in', selection=sel, filter_store=fs) == 'filtered_in'
    assert resolve_render_tier('out', selection=sel, filter_store=fs) == 'excluded'   # not in population


def test_with_no_filter_nothing_is_excluded():
    fs = FilterStore()
    assert resolve_render_tier('anything', selection=None, filter_store=fs) == 'filtered_in'


# ── honest filtered results ──────────────────────────────────────────────────────────────────────

def test_a_filtered_result_RECORDS_the_predicate_and_counts():
    fs = FilterStore()
    fs.set_filter(Filter(predicate='area > 12', members=frozenset({'1', '2'})))
    note = filtered_result_note(fs, n_total=10)
    assert note == dict(filtered=True, predicate='area > 12', n_population=2, n_total=10)
    # an unfiltered result carries NO such note (never mistaken for filtered)
    assert filtered_result_note(FilterStore(), n_total=10) is None


def test_filter_table_restricts_to_the_population_and_notes_it():
    table = pd.DataFrame({'entity_id': ['1', '2', '3'], 'area': [10.0, 20.0, 30.0]})
    fs = FilterStore()
    fs.set_filter(Filter(predicate='area >= 20', members=frozenset({'2', '3'})))
    out, note = filter_table(table, fs, id_col='entity_id')
    assert list(out['entity_id']) == ['2', '3'] and note['predicate'] == 'area >= 20'
    # no filter → unchanged table, no note (never silently filters)
    out2, note2 = filter_table(table, FilterStore(), id_col='entity_id')
    assert len(out2) == 3 and note2 is None


def test_filter_table_does_not_mutate_the_input():
    table = pd.DataFrame({'entity_id': ['1', '2'], 'v': [1.0, 2.0]})
    before = table.copy(deep=True)
    fs = FilterStore(); fs.set_filter(Filter(predicate='p', members=frozenset({'1'})))
    filter_table(table, fs, id_col='entity_id')
    pd.testing.assert_frame_equal(table, before)


# ── no implicit filtering ────────────────────────────────────────────────────────────────────────

def test_no_selection_handler_path_applies_a_filter():
    """Grep-level contract: the selection machinery must never call `set_filter` — a brush/click is
    attention, not a population change."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat'
    for name in ('utils/selection_service.py', 'utils/selection_overlay.py',
                 'utils/comparative_figures.py', 'utils/cohort_targets.py'):
        src = (root / name).read_text(encoding='utf-8', errors='ignore')
        assert 'set_filter' not in src, f"{name} telegraphs to the FilterStore — selection must not filter"
