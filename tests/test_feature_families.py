"""**Feature families: the organizing schema over the measurement layer.**

Measurements are grouped into families (Geometry, Intensity, Material-state, Spatial, …) so the Feature
Explorer, redundancy analysis, and "export only family X" have a structure to hang on. These pin the
contract: the ontology is authoritative (source `'ontology'`), the substring map is a labelled guess
(source `'inferred'`), a genuinely ambiguous column stays `None`/Ungrouped, grouping preserves canonical
order, and NOTHING is silently dropped by grouping. The addition is purely additive — `family` defaults
to `None`, so nothing that already reads the ontology breaks.
"""
import pytest

from pycat.utils.measurement_ontology import (
    MEASUREMENTS, MeasurementDef, FeatureFamily,
)
from pycat.utils.feature_families import (
    classify_column, family_for_column, group_columns_by_family, CANONICAL_ORDER,
)

pytestmark = pytest.mark.core


def test_family_is_additive_default_None_does_not_break_the_ontology():
    """A MeasurementDef built with no family is valid and defaults to None — existing consumers unaffected."""
    d = MeasurementDef(key='x', display_name='X', definition='d', equation='e', units='u')
    assert d.family is None
    # describe()/units_for() still work unchanged on the real registry.
    from pycat.utils.measurement_ontology import describe, units_for
    assert describe('area').family is FeatureFamily.GEOMETRY
    assert units_for('area') == 'µm²'


def test_every_ontology_family_is_returned_via_family_for_column_marked_ontology():
    populated = [k for k, m in MEASUREMENTS.items() if m.family is not None]
    assert populated, "expected the ontology to carry family assignments"
    for key in populated:
        a = classify_column(key)
        assert a.family is MEASUREMENTS[key].family, f"{key}: family_for_column disagrees with the ontology"
        assert a.source == 'ontology', f"{key}: a defined family must be marked source='ontology'"


def test_substring_fallback_classifies_obvious_non_ontology_columns_as_inferred():
    # These are NOT ontology keys, so they exercise the substring map — and must be marked 'inferred'.
    cases = {
        'convex_area': FeatureFamily.GEOMETRY,
        'nucleus_perimeter': FeatureFamily.GEOMETRY,
        'max_intensity': FeatureFamily.INTENSITY,
        'pearson_r_ch1_ch2': FeatureFamily.COLOCALIZATION,
        'apparent_viscosity_pas': FeatureFamily.MATERIAL,
        'ripley_l_at_500nm': FeatureFamily.SPATIAL,
        'partition_ratio_raw': FeatureFamily.PARTITION,
    }
    for name, expected in cases.items():
        assert name not in MEASUREMENTS, f"{name} unexpectedly became an ontology key — pick another"
        a = classify_column(name)
        assert a.family is expected, f"{name} → {a.family}, expected {expected}"
        assert a.source == 'inferred', f"{name}: a substring guess must be marked source='inferred'"


def test_genuinely_ambiguous_columns_stay_None_ungrouped():
    for name in ('value', 'score', 'result', 'label', 'foo_bar', 'timestamp'):
        a = classify_column(name)
        assert a.family is None, f"{name} should be ambiguous → None, got {a.family}"
        assert a.source is None
    assert family_for_column('') is None
    assert family_for_column(None) is None


def test_group_preserves_canonical_order_and_puts_ungrouped_last():
    # Deliberately shuffled across families, plus one unclassifiable ('mystery_metric').
    cols = ['max_intensity', 'convex_area', 'pearson_r', 'mystery_metric', 'nucleus_area', 'total_intensity']
    grouped = group_columns_by_family(cols)
    keys = list(grouped.keys())
    # Named families appear in enum order; None (Ungrouped) is last.
    named = [k for k in keys if k is not None]
    assert named == sorted(named, key=CANONICAL_ORDER.index), "families must be in canonical order"
    assert keys[-1] is None, "the Ungrouped bucket must sort last"
    # Within a bucket, input order is preserved.
    assert grouped[FeatureFamily.INTENSITY] == ['max_intensity', 'total_intensity']
    assert grouped[FeatureFamily.GEOMETRY] == ['convex_area', 'nucleus_area']
    assert grouped[None] == ['mystery_metric']


def test_grouping_drops_nothing_union_equals_input():
    cols = ['area', 'intensity_mean', 'pearson', 'viscosity', 'ripley_l_max', 'nn_median',
            'delta_g_transfer', 'mystery_a', 'mystery_b', 'convex_area', 'aspect_ratio']
    grouped = group_columns_by_family(cols)
    flat = [c for bucket in grouped.values() for c in bucket]
    assert sorted(flat) == sorted(cols), "grouping dropped or duplicated a column"
    assert len(flat) == len(cols)


def test_family_serializes_as_its_plain_string_value():
    # str-enum: a family round-trips through JSON/CSV/manifests as its value with no custom encoder.
    assert FeatureFamily.GEOMETRY == 'geometry'
    assert f"{FeatureFamily.MATERIAL.value}" == 'material_state'
    import json
    assert json.dumps({'family': FeatureFamily.SPATIAL}) == '{"family": "spatial"}'
