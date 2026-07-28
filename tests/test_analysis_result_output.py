"""**Batch analysis steps carry a typed AnalysisResult — the helper, tested HEADLESSLY.**

`batch/steps/_common.analysis_results_for_step` wraps the measurement table a batch analysis step produced into a
typed `AnalysisResult` (typed-result-models adoption), so a step's outcome carries the structured result, not just
a status. This pins that mapping headlessly: the Qt-bound `BatchWorker` integration (which drives it) lives in
`test_batch_step_results.py`, a GUI-lane module the headless gate ignores — so the load-bearing LOGIC is pinned
here, importing only the non-GUI helper (never `pycat.batch_processor`).
"""
import pandas as pd
import pytest

from pycat.batch.steps._common import analysis_results_for_step
from pycat.utils.result_models import AnalysisResult

pytestmark = pytest.mark.base


class _DI:
    def __init__(self, repo=None):
        self.data_repository = dict(repo or {})

    def get_data(self, k, d=None):
        return self.data_repository.get(k, d)


def test_a_mapped_analysis_step_wraps_its_table_as_a_typed_result():
    df = pd.DataFrame({'label': [1, 2], 'area': [10.0, 20.0]})
    out = analysis_results_for_step('cell_analysis', {'data_instance': _DI({'cell_df': df})})
    assert len(out) == 1 and isinstance(out[0], AnalysisResult)
    assert out[0].operation_id == 'cell_analysis' and out[0].entity_type == 'cell'
    assert out[0].measurements is df                      # the actual table — nothing re-derived or copied


def test_droplet_and_condensate_steps_map_to_condensate_and_read_from_state():
    df = pd.DataFrame({'label': [1], 'area': [5.0]})
    for step, key in (('ivf_droplet_analysis', 'ivf_droplet_df'),
                      ('bf_condensate_analysis', 'bf_condensate_df')):
        out = analysis_results_for_step(step, {key: df})   # no data_instance → read the table from state
        assert len(out) == 1 and out[0].entity_type == 'condensate' and out[0].operation_id == step
        assert out[0].measurements is df


def test_an_unmapped_step_or_a_missing_table_yields_no_result():
    df = pd.DataFrame({'a': [1]})
    assert analysis_results_for_step('rolling_ball', {'data_instance': _DI({'cell_df': df})}) == ()   # unmapped
    assert analysis_results_for_step('cell_analysis', {}) == ()                                        # no table
    assert analysis_results_for_step('cell_analysis', {'data_instance': _DI({'cell_df': None})}) == ()
    # a non-frame value under the key is not a measurements table
    assert analysis_results_for_step('cell_analysis', {'cell_df': [1, 2, 3]}) == ()
