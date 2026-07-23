"""**Feature Explorer — one card per measurement, AGGREGATED from existing sources, never recomputed.**

`build_feature_card` is the Explorer's whole deliverable: it pulls a measurement's definition, reliability
grade, stability verdict, correlated columns, provenance, and value distribution from the modules that
already computed them, and degrades each field to `None` when its source did not run. These pin exactly
that: a full card when everything is present, a partial card when the ontology has no entry, per-source
degradation with nothing fabricated, a distribution that matches an independent histogram, a correlated
-with list that matches the redundancy report, and the no-recompute / no-mutation contract.
"""
import numpy as np
import pandas as pd
import pytest

from pycat.utils.feature_explorer import build_feature_card, FeatureCard

pytestmark = pytest.mark.base


def _table(n=200, seed=0):
    rng = np.random.default_rng(seed)
    size = rng.uniform(10, 100, n)
    return pd.DataFrame({'area': size, 'convex_area': size + rng.normal(0, 0.1, n),
                         'mobile_fraction': rng.uniform(0, 1, n)})


class _Rel:
    grade = 'moderate'
    reasons = ('calibration not assessed', 'focus warn')


class _Stab:
    verdict = 'sensitive'


def test_a_card_with_all_sources_present_populates_every_field():
    from pycat.toolbox.feature_redundancy import analyze_redundancy
    table = _table()
    red = analyze_redundancy(table, method='pearson', threshold=0.95)
    ctx = {'reliability': {'area': _Rel()}, 'stability': {'area': _Stab()},
           'redundancy': red, 'provenance': {'area': 'obj → area (regionprops)'}}
    card = build_feature_card(table, 'area', context=ctx)

    assert isinstance(card, FeatureCard) and card.key == 'area'
    assert card.reliability == 'moderate' and 'calibration not assessed' in card.reliability_reasons
    assert card.stability == 'sensitive'
    assert 'convex_area' in card.correlated_with          # grouped with area in the redundancy report
    assert card.provenance_summary == 'obj → area (regionprops)'
    assert card.distribution is not None and card.distribution['n'] == 200


def test_a_measurement_in_the_ontology_carries_its_definition():
    """'mobile_fraction' is an ontology-defined measurement — its definition/units come through."""
    card = build_feature_card(_table(), 'mobile_fraction')
    assert card.definition is not None and card.units is not None


def test_a_measurement_with_NO_ontology_entry_still_gets_a_card():
    """Partial cards are correct: no ontology entry → definition None, but the value distribution still
    fills. A column is never hidden just because its metadata is sparse."""
    card = build_feature_card(_table(), 'convex_area')     # not an ontology key
    assert card.definition is None and card.equation is None and card.units is None
    assert card.distribution is not None                    # the value part still works


def test_fields_DEGRADE_to_none_per_missing_source_nothing_fabricated():
    card = build_feature_card(_table(), 'area', context={})   # no sources ran
    assert card.reliability is None and card.reliability_reasons == ()
    assert card.stability is None and card.correlated_with == ()
    assert card.provenance_summary is None
    assert card.distribution is not None                     # only the distribution needs no source


def test_the_distribution_binning_matches_an_independent_histogram():
    table = _table()
    card = build_feature_card(table, 'area', bins=15)
    counts, edges = np.histogram(table['area'].to_numpy(), bins=15)
    assert np.array_equal(card.distribution['counts'], counts)
    assert np.allclose(card.distribution['edges'], edges)


def test_correlated_with_matches_the_redundancy_report():
    from pycat.toolbox.feature_redundancy import analyze_redundancy
    table = _table()
    red = analyze_redundancy(table, method='pearson', threshold=0.95)
    card = build_feature_card(table, 'area', context={'redundancy': red})
    # the group containing 'area', minus 'area' itself
    group = next(g for g in red.groups if 'area' in g)
    assert set(card.correlated_with) == set(group) - {'area'}


def test_card_assembly_does_not_mutate_the_table():
    table = _table()
    before = table.copy(deep=True)
    build_feature_card(table, 'area', context={'reliability': {'area': _Rel()}})
    pd.testing.assert_frame_equal(table, before)


def test_an_absent_column_yields_a_card_with_no_distribution():
    card = build_feature_card(_table(), 'does_not_exist')
    assert card.key == 'does_not_exist' and card.distribution is None


def test_the_dock_WIRES_the_assembler_and_the_cohort_histogram():
    """AST: the dock (Qt-bound, not importable headless) must build its content via `build_feature_card`
    and wire the mini-histogram through `attach_histogram_brushing` (the 1.6.170 cohort emitter) — a dock
    that recomputed, or whose histogram did not emit selections, would miss the point."""
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat' / 'ui'
           / 'feature_explorer_dock.py').read_text(encoding='utf-8')
    calls = {getattr(c.func, 'id', None) or getattr(c.func, 'attr', None)
             for c in ast.walk(ast.parse(src)) if isinstance(c, ast.Call)}
    assert 'build_feature_card' in calls, "the dock does not use the tested assembler"
    assert 'attach_histogram_brushing' in calls, "the mini-histogram does not emit cohort selections"
