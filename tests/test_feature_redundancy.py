"""**Feature redundancy — find near-duplicate columns by clustering, report them, never drop them.**

PyCAT emits wide feature tables where several columns track the same underlying quantity (size, intensity).
`analyze_redundancy` groups the mutually-redundant ones by correlation-distance clustering and picks one
representative per group by a stated rule; `minimal_feature_set` is the opt-in minimal set. These pin the
properties that make it honest: transitive clustering (not order-dependent pairwise dropping), a chosen
(not arbitrary) representative, Spearman catching square-law redundancy Pearson misses, constant/NaN
handling with stated reasons, the cry-wolf test (independent columns → no groups), and — the cardinal
contract — the input table is never mutated.
"""
import numpy as np
import pandas as pd
import pytest

from pycat.toolbox.feature_redundancy import (analyze_redundancy, minimal_feature_set,
                                              RedundancyReport)

pytestmark = pytest.mark.core


def _rng(seed=0):
    return np.random.default_rng(seed)


def test_a_known_duplicate_is_grouped_and_one_is_kept():
    rng = _rng(0)
    area = rng.uniform(10, 100, 200)
    table = pd.DataFrame({'area': area, 'double_area': 2 * area,
                          'unrelated': rng.uniform(0, 1, 200)})
    rep = analyze_redundancy(table, method='pearson', threshold=0.95)

    assert len(rep.groups) == 1
    assert rep.groups[0] == frozenset({'area', 'double_area'})
    # exactly one of the pair is dropped, and 'unrelated' is never grouped
    assert set(rep.dropped) < {'area', 'double_area'} and len(rep.dropped) == 1
    assert 'unrelated' not in rep.dropped


def test_independent_columns_produce_NO_groups_the_cry_wolf_test():
    rng = _rng(1)
    table = pd.DataFrame({f'f{i}': rng.normal(0, 1, 300) for i in range(6)})
    rep = analyze_redundancy(table, threshold=0.95)
    assert rep.groups == [] and rep.dropped == ()
    # a clean table's minimal set is the whole table
    assert set(minimal_feature_set(rep)) == set(table.columns)


def test_clustering_is_TRANSITIVE_not_pairwise():
    """A~B and B~C must pull all three into one group even if A~C is just under threshold — an
    order-dependent pairwise drop would not."""
    rng = _rng(2)
    a = rng.uniform(0, 1, 400)
    b = a + rng.normal(0, 0.02, 400)      # A~B very high
    c = b + rng.normal(0, 0.02, 400)      # B~C very high; A~C slightly lower (noise compounds)
    table = pd.DataFrame({'A': a, 'B': b, 'C': c})
    rep = analyze_redundancy(table, method='pearson', threshold=0.97)
    assert len(rep.groups) == 1 and rep.groups[0] == frozenset({'A', 'B', 'C'})
    assert len(minimal_feature_set(rep)) == 1        # one representative for the whole group


def test_spearman_catches_a_square_law_that_pearson_at_the_same_threshold_misses():
    r = _rng(3).uniform(1, 10, 300)
    table = pd.DataFrame({'radius': r, 'area': np.pi * r ** 2})   # monotonic, non-linear
    pear = analyze_redundancy(table, method='pearson', threshold=0.99)
    spear = analyze_redundancy(table, method='spearman', threshold=0.99)
    assert spear.groups == [frozenset({'radius', 'area'})], "Spearman must see the monotonic redundancy"
    assert pear.groups == [], "Pearson at 0.99 understates the square-law relationship"


def test_representative_prefers_an_ONTOLOGY_defined_column():
    """Within a redundant group, an ontology-defined measurement is kept over a derived one, with the
    reason recorded — so the minimal set is reproducible, not an arbitrary pick."""
    rng = _rng(4)
    base = rng.uniform(0.1, 0.9, 200)
    # 'mobile_fraction' is in the measurement ontology; 'mobile_frac_copy' is a derived near-duplicate.
    table = pd.DataFrame({'mobile_frac_copy': base + rng.normal(0, 0.001, 200),
                          'mobile_fraction': base})
    rep = analyze_redundancy(table, method='pearson', threshold=0.95)
    assert len(rep.groups) == 1
    assert 'mobile_fraction' in rep.representatives
    assert 'ontology' in rep.representatives['mobile_fraction']
    assert rep.dropped == ('mobile_frac_copy',)


def test_a_constant_column_is_EXCLUDED_with_a_stated_reason():
    rng = _rng(5)
    a = rng.uniform(0, 1, 100)
    table = pd.DataFrame({'a': a, 'b': a * 3, 'flat': np.full(100, 7.0)})
    rep = analyze_redundancy(table, method='pearson', threshold=0.95)
    assert 'flat' in rep.excluded and 'constant' in rep.excluded['flat']
    assert 'flat' not in rep.correlation.columns          # not correlated at all
    assert rep.groups == [frozenset({'a', 'b'})]


def test_minimal_feature_set_is_one_per_group_plus_ungrouped():
    rng = _rng(6)
    size = rng.uniform(10, 100, 200)
    table = pd.DataFrame({'area': size, 'convex_area': size + rng.normal(0, 0.1, 200),
                          'intensity': rng.uniform(0, 255, 200)})
    rep = analyze_redundancy(table, method='pearson', threshold=0.95)
    keep = minimal_feature_set(rep)
    assert 'intensity' in keep                            # ungrouped, never dropped
    assert len([c for c in keep if c in ('area', 'convex_area')]) == 1   # one of the size pair


def test_the_analysis_NEVER_mutates_the_input_table():
    rng = _rng(7)
    a = rng.uniform(0, 1, 150)
    table = pd.DataFrame({'a': a, 'b': 2 * a, 'c': rng.uniform(0, 1, 150)})
    before = table.copy(deep=True)
    analyze_redundancy(table, method='pearson', threshold=0.9)
    pd.testing.assert_frame_equal(table, before)          # report, never mutate


def test_fewer_than_two_analysable_columns_returns_an_empty_report():
    table = pd.DataFrame({'only': _rng(8).uniform(0, 1, 50), 'flat': np.zeros(50)})
    rep = analyze_redundancy(table)
    assert isinstance(rep, RedundancyReport)
    assert rep.groups == [] and 'flat' in rep.excluded
