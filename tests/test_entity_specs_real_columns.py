"""**Identity on the columns the producers actually write** (Completion Spec F2/F3).

* F3: ``'cell_label'`` is a known PARENT spelling, so a table whose LABEL column is ``cell_label`` (time series,
  z-stack cells, BF per-cell) had that same column auto-detected as its parent -- every id carried the label
  twice. An explicit label column now wins.
* F2: the time-series, z-stack, brightfield and fibril producers declare specs on their real columns, and
  ``object_colocalization`` names ``'Mask 1 Labels'`` (``'label'`` matched nothing).
"""
import pandas as pd
import pytest

from pycat.utils.entity_ref import ENTITY_ID_COLUMN, entity_spec_for, finalize_entity_table, stamp_entity_ids

pytestmark = pytest.mark.base


def test_an_explicit_cell_label_LABEL_is_not_also_taken_as_its_parent():
    df = pd.DataFrame({'cell_label': [4, 4], 'frame': [0, 1]})
    stamp_entity_ids(df, entity_type='cell', source_path=None, operation_id='op', label_column='cell_label',
                     frame_column='frame')
    assert list(df[ENTITY_ID_COLUMN]) == ['unknown/op/cell/0/4', 'unknown/op/cell/1/4']


def test_a_condensate_table_still_takes_cell_label_as_its_parent():
    df = pd.DataFrame({'condensate_label': [1, 1], 'cell_label': [2, 3]})
    stamp_entity_ids(df, entity_type='condensate', operation_id='op', label_column='condensate_label')
    assert list(df[ENTITY_ID_COLUMN]) == ['unknown/op/condensate/-/2/1', 'unknown/op/condensate/-/3/1']


@pytest.mark.parametrize('op, label', [
    ('timeseries_condensate_analysis', 'cell_label'), ('zstack_cell_metrics', 'cell_label'),
    ('zstack_condensate_metrics', 'condensate_label'), ('bf_condensate_metrics', 'condensate_label'),
    ('bf_per_cell_summary', 'cell_label'), ('ivbf_droplet_metrics', 'condensate_label'),
    ('fibril_morphology', 'label'), ('object_colocalization', 'Mask 1 Labels'),
    ('two_channel_condensate_coloc', 'cell_label')])
def test_each_declared_spec_names_the_real_label_column(op, label):
    assert entity_spec_for(op).label_column == label


def test_a_time_series_table_is_stamped_per_cell_per_frame():
    df = pd.DataFrame({'cell_label': [1, 2, 1], 'frame': [0, 0, 1], 'condensate_fraction': [0.1, 0.2, 0.3]})
    out = finalize_entity_table(df, 'timeseries_condensate_analysis')
    assert out[ENTITY_ID_COLUMN].is_unique
    assert out[ENTITY_ID_COLUMN].iloc[2].endswith('/timeseries_condensate_analysis/cell/1/1')
