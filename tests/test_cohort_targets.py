"""**Histogram bins and aggregate rows now select honestly — the two deferred cohort emitters.**

The cohort target (`Cohort` + `select_cohort`) and the comparative box/violin emitter shipped in 1.6.151;
the histogram-bin and aggregate-row emitters were the clean follow-ons deferred with it. This pins them:

- **Histogram bin → cohort** whose membership matches the bin's TRUE range (computed independently and
  asserted equal — the test that the grouping is *correct*, not merely non-empty), carrying the range as
  its definition.
- **Aggregate row → cohort** equal to the contributing set, with the count stated ("summarizes N
  objects") — never one arbitrary member.

Both emit through the real `SelectionService`, so the additive contract carries: `selected` is filled with
the members (a cohort-unaware overlay highlights them for free), and a cohort is a SELECTION, never a
FILTER — the emitters touch no DataFrame.
"""
import numpy as np
import pytest

from pycat.utils.selection_service import SelectionService, Cohort
from pycat.utils.cohort_targets import (bin_cohort, aggregate_cohort, cohort_dock_label,
                                        select_aggregate_row, attach_histogram_brushing)

pytestmark = pytest.mark.core


def _service():
    return SelectionService(defer=lambda fn: fn(), debounce=lambda fn: fn())


# A population with known values so a bin's true membership is checkable by hand.
_VALUES = [1.0, 2.0, 5.0, 5.5, 9.0, 9.9, 10.0]
_EIDS = ['e0', 'e1', 'e2', 'e3', 'e4', 'e5', 'e6']
_EDGES = [0.0, 3.0, 6.0, 10.0]     # bins: [0,3) [3,6) [6,10]


# ── histogram bin membership is CORRECT ────────────────────────────────────────────────────────

def test_bin_cohort_membership_matches_the_range_exactly():
    # bin 0 = [0, 3): values 1.0, 2.0 → e0, e1
    c0 = bin_cohort(_VALUES, _EIDS, 0, _EDGES)
    assert c0.members == frozenset({'e0', 'e1'})
    # bin 1 = [3, 6): 5.0, 5.5 → e2, e3
    c1 = bin_cohort(_VALUES, _EIDS, 1, _EDGES)
    assert c1.members == frozenset({'e2', 'e3'})


def test_the_LAST_bin_is_CLOSED_so_the_max_lands_in_it():
    """matplotlib's last bin is [edge[-2], edge[-1]] — the maximum value must be included, not dropped."""
    c2 = bin_cohort(_VALUES, _EIDS, 2, _EDGES)          # [6, 10] closed
    assert c2.members == frozenset({'e4', 'e5', 'e6'})  # 9.0, 9.9, AND 10.0 (the max)


def test_bin_membership_matches_an_INDEPENDENT_recompute_over_every_bin():
    """The grouping is correct across all bins: union is the whole population, bins are disjoint."""
    v = np.asarray(_VALUES)
    all_members = set()
    for i in range(len(_EDGES) - 1):
        c = bin_cohort(_VALUES, _EIDS, i, _EDGES)
        lo, hi = _EDGES[i], _EDGES[i + 1]
        last = i == len(_EDGES) - 2
        expect = {_EIDS[j] for j in range(len(v))
                  if ((lo <= v[j] <= hi) if last else (lo <= v[j] < hi))}
        assert c.members == frozenset(expect), f"bin {i} membership wrong"
        assert all_members.isdisjoint(c.members), "bins must be disjoint"
        all_members |= c.members
    assert all_members == set(_EIDS), "every object lands in exactly one bin"


def test_bin_cohort_definition_carries_the_range_and_units():
    c = bin_cohort(_VALUES, _EIDS, 1, _EDGES, measurement='area', units='µm²')
    assert c.kind == 'bin'
    assert 'area' in c.definition and 'µm²' in c.definition
    assert '[3, 6)' in c.definition, "a non-last bin is half-open — the ')' must show that"


def test_bin_cohort_out_of_range_index_raises():
    with pytest.raises(IndexError):
        bin_cohort(_VALUES, _EIDS, 9, _EDGES)


# ── aggregate row → the contributing set, with the count ───────────────────────────────────────

def test_aggregate_cohort_is_the_contributing_set_with_the_count():
    c = aggregate_cohort(['a', 'b', 'c', 'a'])          # dedup via frozenset
    assert c.members == frozenset({'a', 'b', 'c'}) and c.kind == 'aggregate'
    assert c.definition == 'summarizes 3 objects'       # never one arbitrary member


def test_aggregate_cohort_keeps_a_caller_label_and_appends_the_count():
    c = aggregate_cohort(['a', 'b'], definition='WT mean area')
    assert c.definition == 'WT mean area · 2 objects'


def test_cohort_dock_label_states_count_then_why():
    c = aggregate_cohort(['a', 'b', 'c'])
    assert cohort_dock_label(c) == '3 objects · summarizes 3 objects'
    assert cohort_dock_label(None) == '' and cohort_dock_label(Cohort()) == ''


# ── the emitters ride the real service (additive + selection≠filter) ───────────────────────────

def test_select_aggregate_row_fills_selected_for_graceful_degradation():
    svc = _service()
    seen = []
    svc.subscribe('overlay', lambda st: seen.append(set(st.selected)))   # a cohort-UNAWARE view
    select_aggregate_row(svc, ['a', 'b', 'c'], view_id='table')
    assert seen[-1] == {'a', 'b', 'c'}, "members must ride in `selected` so the overlay highlights them"
    assert svc._state.cohort is not None and svc._state.cohort.kind == 'aggregate'


def test_histogram_brushing_emits_the_right_bin_on_a_click_x():
    """The GUI-free path the matplotlib handler calls: emit_bin(xdata) selects the bin's cohort."""
    import matplotlib
    matplotlib.use('Agg', force=False)
    import matplotlib.pyplot as plt

    svc = _service()
    got = []
    svc.subscribe('dock', lambda st: got.append(st.cohort))
    fig, ax = plt.subplots()
    counts, edges, bars = ax.hist(_VALUES, bins=_EDGES)
    handle = attach_histogram_brushing(fig, ax, _VALUES, _EIDS, bin_edges=edges,
                                       selection_service=svc, view_id='hist', measurement='area')
    coh = handle['emit_bin'](5.2)                       # x=5.2 falls in bin [3, 6)
    assert coh.members == frozenset({'e2', 'e3'})
    assert got and got[-1].members == frozenset({'e2', 'e3'})
    plt.close(fig)


def test_a_cohort_emit_does_not_mutate_the_source_data():
    """Selection ≠ filter: emitting a cohort must not touch the values/ids it was built from."""
    values = list(_VALUES)
    eids = list(_EIDS)
    svc = _service()
    select_aggregate_row(svc, eids)
    bin_cohort(values, eids, 0, _EDGES)
    assert values == _VALUES and eids == _EIDS, "the emitter mutated its inputs — a cohort must not filter"
