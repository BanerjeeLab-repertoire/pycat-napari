"""**Typed result envelopes — a result crossing a boundary is a TYPE, validated at construction.**

Pins the contract: the models are frozen; an impossible result cannot be built (unknown status, a failed
step with no error, a stringly-typed error, a measurements bare-dict); and `to_dict`/`from_dict` bridge to
the dict form existing code speaks, round-tripping the scalar fields and the measurements table.
"""
import json

import pandas as pd
import pytest

from pycat.utils.result_models import AnalysisResult, BatchStepResult, STATUSES
from pycat.utils.errors import PyCATError, ScientificAssumptionError

pytestmark = pytest.mark.core


def test_analysis_result_is_frozen_and_normalizes_sequences():
    r = AnalysisResult(operation_id='op1', entity_type='cell',
                       source_layer_ids=['L0', 'L1'], artifacts=['out.csv'])
    assert r.source_layer_ids == ('L0', 'L1') and r.artifacts == ('out.csv',)   # coerced to tuples
    with pytest.raises(Exception):
        r.operation_id = 'x'                                                     # frozen


def test_analysis_result_refuses_empty_ids_and_a_bare_dict_for_measurements():
    with pytest.raises(ScientificAssumptionError, match="operation_id"):
        AnalysisResult(operation_id='', entity_type='cell')
    with pytest.raises(ScientificAssumptionError, match="entity_type"):
        AnalysisResult(operation_id='op', entity_type='  ')
    with pytest.raises(ScientificAssumptionError, match="DataFrame"):
        AnalysisResult(operation_id='op', entity_type='cell', measurements={'area': [1, 2]})


def test_analysis_result_round_trips_through_the_dict_boundary():
    df = pd.DataFrame({'area': [10.0, 20.0], 'intensity': [1.0, 2.0]})
    r = AnalysisResult(operation_id='partition', entity_type='punctum',
                       source_layer_ids=('L0',), measurements=df,
                       provenance={'area': {'feature': 'area', 'software': {'pycat': '1.0'}}},
                       calibration={'slope': 2.0})
    d = r.to_dict()
    assert json.dumps(d)                                       # JSON-serializable at the boundary
    back = AnalysisResult.from_dict(d)
    assert back.operation_id == r.operation_id and back.entity_type == r.entity_type
    assert back.source_layer_ids == ('L0',)
    pd.testing.assert_frame_equal(back.measurements, df)
    assert back.provenance == {'area': {'feature': 'area', 'software': {'pycat': '1.0'}}}


def test_analysis_result_serializes_composed_dataclasses_to_plain_dicts():
    from pycat.utils.feature_provenance import compose_provenance
    prov = compose_provenance('area', parameters={'units': 'µm²'})
    r = AnalysisResult(operation_id='op', entity_type='cell', provenance={'area': prov})
    d = r.to_dict()
    assert d['provenance']['area']['feature'] == 'area'       # FeatureProvenance → plain dict
    assert json.dumps(d)                                      # still JSON-serializable


def test_batch_step_status_must_be_known_and_error_must_be_typed():
    assert set(STATUSES) == {'ok', 'warning', 'error', 'skipped'}
    with pytest.raises(ScientificAssumptionError, match="status"):
        BatchStepResult(status='done')                        # unknown status
    with pytest.raises(ScientificAssumptionError, match="PyCATError"):
        BatchStepResult(status='error', error='it broke')     # stringly-typed error refused


def test_batch_step_status_and_error_must_AGREE():
    with pytest.raises(ScientificAssumptionError, match="agree"):
        BatchStepResult(status='error')                       # error status, no error attached
    with pytest.raises(ScientificAssumptionError, match="agree"):
        BatchStepResult(status='ok', error=PyCATError('x'))   # error attached, non-error status
    ok = BatchStepResult(status='ok', outputs=['a.csv'], warnings=['w'])
    assert ok.outputs == ('a.csv',) and ok.warnings == ('w',) and ok.error is None
    bad = BatchStepResult(status='error', error=PyCATError('the reader failed'))
    assert bad.error is not None


def test_batch_step_round_trips_through_the_dict_boundary():
    r = BatchStepResult(status='error', warnings=['slow'], error=PyCATError('boom'))
    d = r.to_dict()
    assert json.dumps(d) and d['error']['message'] == 'boom' and d['error']['type'] == 'PyCATError'
    back = BatchStepResult.from_dict(d)
    assert back.status == 'error' and isinstance(back.error, PyCATError) and str(back.error) == 'boom'
    assert back.warnings == ('slow',)
