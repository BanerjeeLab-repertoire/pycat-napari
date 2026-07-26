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
