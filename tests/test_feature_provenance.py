"""**Per-feature provenance — a reported number can name the exact chain that produced it.**

The load-bearing test is discrimination: in a workflow with two independent branches, a feature from branch
A must NOT list branch B's steps — a provenance record that says "everything" has no information content.
Also pinned: fields are composed from existing sources (never fabricated), an underivable field is `None`
with a reason, software/acquisition are captured automatically, and the sidecar JSON round-trips keyed by
column name.
"""
import numpy as np
import pytest

from pycat.utils.feature_provenance import (
    FeatureProvenance, acquisition_from_metadata, compose_provenance, describe_provenance,
    provenance_sidecar_dict, read_provenance_sidecar, software_versions, trace_step_indices,
    write_provenance_sidecar)

pytestmark = pytest.mark.core


# ── THE discrimination test: independent branches do not claim each other's steps ───────────────
def test_a_feature_from_one_branch_does_not_list_the_other_branch():
    """A two-branch workflow: raw → (branch A: preprocess→segment_cells) and (branch B: segment_fibrils).
    A feature measured on the cell mask (branch A) must trace steps 0,1,2 — NOT the fibril step 3."""
    lineage = {
        'raw': [],
        'ppA': ['raw'],          # step 1: preprocess (branch A)
        'cells': ['ppA'],        # step 2: cell segmentation (branch A)
        'fibrils': ['raw'],      # step 3: fibril segmentation (branch B, independent)
    }
    layer_step = {'raw': 0, 'ppA': 1, 'cells': 2, 'fibrils': 3}

    steps_a, reason_a = trace_step_indices('cells', lineage, layer_step)
    assert steps_a == (0, 1, 2) and reason_a == ''
    assert 3 not in steps_a, "the cell feature must not claim the independent fibril step"

    steps_b, _ = trace_step_indices('fibrils', lineage, layer_step)
    assert steps_b == (0, 3), "the fibril feature traces only raw→fibrils, not the cell branch"


def test_an_unrecorded_lineage_is_None_with_a_reason_not_all_steps():
    """"All steps" is indistinguishable from no record — so an unknown layer yields None + a reason."""
    steps, reason = trace_step_indices('unknown_layer', {'raw': []}, {'raw': 0})
    assert steps is None and 'cannot be attributed' in reason


# ── Compose from existing sources; never fabricate ──────────────────────────────────────────────
def test_compose_derives_the_fields_and_leaves_underivable_ones_absent():
    prov = compose_provenance(
        'partition_coefficient', operation_id='client_enrichment',
        input_layers=('layer-abc', 'layer-def'), step_indices=(0, 1, 2),
        parameters={'background': 0.0},
        metadata={'pixel_size_um': 0.1, 'exposure_s': 0.05, 'camera_name': 'ignored'})
    assert prov.feature == 'partition_coefficient'
    assert prov.operation_id == 'client_enrichment'
    assert prov.input_layers == ('layer-abc', 'layer-def')
    assert prov.step_indices == (0, 1, 2)
    assert prov.parameters == {'background': 0.0}
    assert prov.acquisition == {'pixel_size_um': 0.1, 'exposure_s': 0.05}   # only known fields, no camera_name

    # An underivable operation/steps stays absent, not guessed.
    bare = compose_provenance('area')
    assert bare.operation_id is None and bare.step_indices is None and bare.input_layers == ()


def test_software_and_acquisition_are_captured_automatically():
    sw = software_versions()
    assert 'pycat' in sw and 'numpy' in sw          # captured from the environment, not asked for
    assert acquisition_from_metadata({'pixel_size_um': 0.1, 'frame_interval_s': 0.5}) == {
        'pixel_size_um': 0.1, 'frame_interval_s': 0.5}
    assert acquisition_from_metadata({}) == {}       # nothing present → nothing recorded


# ── The sidecar JSON round-trips, keyed by column name ──────────────────────────────────────────
def test_the_sidecar_round_trips_keyed_by_column(tmp_path):
    prov_by_col = {
        'partition_coefficient': compose_provenance(
            'partition_coefficient', operation_id='client_enrichment', step_indices=(0, 1),
            input_layers=('lyr-1',), parameters={'background': 0.0},
            metadata={'pixel_size_um': 0.1}),
        'area': compose_provenance('area'),          # underivable steps → None in the JSON
    }
    table_path = tmp_path / 'results.csv'
    table_path.write_text('a,b\n1,2\n', encoding='utf-8')
    sidecar = write_provenance_sidecar(table_path, prov_by_col)
    assert sidecar.name == 'results_provenance.json'

    restored = read_provenance_sidecar(sidecar)
    assert set(restored) == {'partition_coefficient', 'area'}
    assert restored['partition_coefficient']['operation_id'] == 'client_enrichment'
    assert restored['partition_coefficient']['step_indices'] == [0, 1]
    assert restored['area']['step_indices'] is None      # absent, not "all steps"


def test_the_where_did_this_come_from_query_reads_the_chain():
    prov = compose_provenance('partition_coefficient', operation_id='client_enrichment',
                              step_indices=(0, 1, 2), parameters={'background': 0.0},
                              metadata={'pixel_size_um': 0.1})
    text = describe_provenance(prov)
    assert 'client_enrichment' in text and '[0, 1, 2]' in text and 'partition_coefficient' in text

    unknown = compose_provenance('x', step_indices=None, step_reason='lineage incomplete')
    assert 'unknown' in describe_provenance(unknown)


def test_capturing_provenance_does_not_touch_computed_values():
    """Provenance is composed from copies of its inputs; the caller's data is untouched (a frozen record,
    and dict inputs are copied)."""
    params = {'background': 0.0}
    prov = compose_provenance('k', parameters=params)
    prov.parameters['background'] = 999             # mutating the record's copy…
    assert params == {'background': 0.0}            # …does not touch the caller's dict
    assert isinstance(prov, FeatureProvenance)


def test_provenance_sidecar_dict_is_json_shaped():
    d = provenance_sidecar_dict({'k': compose_provenance('k', operation_id='op', step_indices=(1,))})
    assert d['k']['input_layers'] == [] and d['k']['step_indices'] == [1]
