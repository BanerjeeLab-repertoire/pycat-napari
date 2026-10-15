"""**Batch steps carry a typed BatchStepResult status (exception_context_classification Part 3 / typed-result-models).**

`BatchWorker._process_file` now records each replay step's outcome as a typed `BatchStepResult` — `'ok'`,
`'skipped'` (unregistered), or `'error'` (with a typed `PyCATError`, and the remaining steps skipped) — and
returns the list. The file loop uses that to mark a file whose step failed as a visible partial (⚠), not a
misleading clean ✓. These pin the typed-result contract at the step level (headlessly, via the unbound method).
"""
import numpy as np
import pytest
from pathlib import Path
from types import SimpleNamespace

from pycat.batch_processor import BatchWorker
from pycat.utils.result_models import BatchStepResult
from pycat.utils.errors import PyCATError

pytestmark = pytest.mark.base      # batch_processor imports Qt at module scope → the fuller lane


def _worker(steps, registry):
    return SimpleNamespace(config={"steps": steps}, step_registry=registry, _auto_ball_radius=False)


def test_all_steps_ok_returns_all_ok_results(tmp_path):
    ran = []
    reg = {"a": lambda s, p, pa, o: ran.append("a"),
           "b": lambda s, p, pa, o: ran.append("b")}
    out = BatchWorker._process_file(_worker([{"step": "a"}, {"step": "b"}], reg), Path("x.tif"), tmp_path)
    assert [(nm, r.status) for nm, r in out] == [("a", "ok"), ("b", "ok")]
    assert all(isinstance(r, BatchStepResult) for _, r in out)
    assert ran == ["a", "b"]


def test_a_failing_step_is_error_typed_and_halts_the_rest(tmp_path):
    ran = []

    def _boom(state, image_path, params, output_dir):
        raise ValueError("boom in the middle")

    reg = {"a": lambda s, p, pa, o: ran.append("a"),
           "boom": _boom,
           "c": lambda s, p, pa, o: ran.append("c")}
    out = BatchWorker._process_file(
        _worker([{"step": "a"}, {"step": "boom"}, {"step": "c"}], reg), Path("x.tif"), tmp_path)

    assert [(nm, r.status) for nm, r in out] == [("a", "ok"), ("boom", "error")]
    assert "c" not in ran                                  # the step after the failure is not attempted
    err = dict(out)["boom"].error
    assert isinstance(err, PyCATError) and "boom in the middle" in str(err)   # typed, message preserved


def test_an_unregistered_step_is_recorded_skipped(tmp_path):
    reg = {"a": lambda s, p, pa, o: None}
    out = BatchWorker._process_file(
        _worker([{"step": "a"}, {"step": "does_not_exist"}], reg), Path("x.tif"), tmp_path)
    assert [(nm, r.status) for nm, r in out] == [("a", "ok"), ("does_not_exist", "skipped")]


def test_batch_step_result_invariant_error_iff_pycat_error():
    # the envelope this adoption relies on: status 'error' iff a typed error is attached
    assert BatchStepResult(status="ok").error is None
    with pytest.raises(Exception):
        BatchStepResult(status="error")                   # error status with no error → refused
    with pytest.raises(Exception):
        BatchStepResult(status="ok", error=PyCATError("x"))   # error attached but claims success → refused


# ── AnalysisResult adoption: an analysis step carries its measurement table as a typed result ──────

class _DI:
    def __init__(self):
        self.data_repository = {}

    def set_data(self, k, v):
        self.data_repository[k] = v

    def get_data(self, k, d=None):
        return self.data_repository.get(k, d)


def test_an_analysis_step_carries_a_typed_AnalysisResult_output(tmp_path):
    import pandas as pd
    from pycat.utils.result_models import AnalysisResult
    df = pd.DataFrame({'label': [1, 2], 'area': [10.0, 20.0]})

    def cell_analysis(state, ip, pa, o):
        state['data_instance'] = _DI()
        state['data_instance'].set_data('cell_df', df)      # the step writes its table the way the real one does

    out = BatchWorker._process_file(
        _worker([{"step": "cell_analysis"}], {"cell_analysis": cell_analysis}), Path("x.tif"), tmp_path)
    name, res = out[0]
    assert res.status == 'ok' and len(res.outputs) == 1
    ar = res.outputs[0]
    assert isinstance(ar, AnalysisResult)
    assert ar.operation_id == 'cell_analysis' and ar.entity_type == 'cell'
    assert ar.measurements is df                            # the actual table — nothing re-derived or copied


def test_a_non_analysis_step_carries_no_outputs(tmp_path):
    out = BatchWorker._process_file(
        _worker([{"step": "a"}], {"a": lambda s, p, pa, o: None}), Path("x.tif"), tmp_path)
    assert out[0][1].status == 'ok' and out[0][1].outputs == ()


def test_an_analysis_step_that_wrote_no_table_carries_no_output(tmp_path):
    # cell_analysis is in the map, but if it produced nothing (no cell_df) there is no result to wrap — ()
    out = BatchWorker._process_file(
        _worker([{"step": "cell_analysis"}], {"cell_analysis": lambda s, p, pa, o: None}),
        Path("x.tif"), tmp_path)
    assert out[0][1].status == 'ok' and out[0][1].outputs == ()


# ── Completion Spec E1: a step batch cannot replay is LOUD, not a print() that reads like it ran ─────────────

def test_every_not_replayed_registry_entry_raises_StepNotImplemented_with_its_reason(tmp_path):
    from pycat.batch_step_registry import _STEP_MAP
    from pycat.utils.errors import StepNotImplemented
    stubs = {k: f for k, f in _STEP_MAP.items() if getattr(f, 'not_replayed', False)}
    assert stubs, 'expected the not-replayed entries to be marked'
    for key, fn in stubs.items():
        with pytest.raises(StepNotImplemented) as info:
            fn({}, Path('x.tif'), {}, tmp_path)
        assert info.value.step == key and info.value.reason


def test_no_registry_entry_is_a_bare_lambda():
    """A ``lambda ...: print(...)`` entry is the silent stub E1 removed; a new one must use ``_not_replayed``."""
    from pycat.batch_step_registry import _STEP_MAP
    lambdas = [k for k, f in _STEP_MAP.items() if getattr(f, '__name__', '') == '<lambda>']
    assert not lambdas, f'bare-lambda registry entries (use _not_replayed(step, reason)): {lambdas}'


def test_a_not_replayed_step_is_skipped_and_the_rest_still_run(tmp_path):
    from pycat.batch_step_registry import _not_replayed
    ran = []
    reg = {"a": lambda s, p, pa, o: ran.append("a"),
           "stub": _not_replayed("stub", "interactive only"),
           "c": lambda s, p, pa, o: ran.append("c")}
    out = BatchWorker._process_file(
        _worker([{"step": "a"}, {"step": "stub"}, {"step": "c"}], reg), Path("x.tif"), tmp_path)
    assert [(nm, r.status) for nm, r in out] == [("a", "ok"), ("stub", "skipped"), ("c", "ok")]
    assert dict(out)["stub"].warnings == ("interactive only",)
    assert ran == ["a", "c"]


def test_the_run_report_lists_every_step_that_was_not_replayed():
    from pycat.batch_processor import not_replayed_report, record_not_replayed
    collected = {}
    for name in ('one.tif', 'two.tif'):
        record_not_replayed(collected, name, [("a", BatchStepResult(status="ok")),
                                              ("fd_load", BatchStepResult(status="skipped", warnings=("why",))),
                                              ("gone", BatchStepResult(status="skipped",
                                                                       warnings=("not registered",)))])
    report = not_replayed_report(collected, 2)
    assert "2 recorded step(s) were NOT replayed" in report
    assert "fd_load (2/2 image(s)): why" in report and "gone (2/2 image(s)): not registered" in report
    assert not_replayed_report({}, 2) == ''


def test_a_real_replay_is_never_shadowed_by_a_not_replayed_declaration():
    """``_STEP_MAP`` ends with ``**NOT_REPLAYED``: a key in both would silently run the stub, not the replay."""
    import ast
    import inspect
    import pycat.batch_step_registry as reg
    from pycat.batch.steps.not_replayed import NOT_REPLAYED
    tree = ast.parse(inspect.getsource(reg))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                and any(getattr(t, 'id', None) == '_STEP_MAP' for t in n.targets))
    explicit = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    assert not explicit & set(NOT_REPLAYED), explicit & set(NOT_REPLAYED)
