"""**The one operation runner: marshalling, stale-suppression, cancellation, typed error transport.**

`run_with_progress` runs the compute synchronously when there is no Qt event loop, so the runner's
policy — forward the progress contract, deliver the result to the caller, discard a superseded result,
stop on cancel, transport a typed error to `on_error` — is all exercisable headlessly.
"""

import threading

import pytest

pytestmark = pytest.mark.core

from pycat.utils.operation_runner import OperationRunner, CancellationToken


def test_the_result_is_delivered_to_the_CALLERS_thread():
    """`on_result` is the main-thread marshalling point — it fires on the thread that called `execute`,
    where napari/Qt updates are legal. (Headless: worker and caller are the same thread; the contract is
    still that `on_result` runs on the caller's.)"""
    runner = OperationRunner()
    caller_thread = threading.get_ident()
    seen = {}

    def fn():
        return 42

    runner.execute(fn, on_result=lambda r: seen.update(value=r, thread=threading.get_ident()))
    assert seen['value'] == 42
    assert seen['thread'] == caller_thread, "on_result must run on the caller's (main) thread"


def test_progress_forwards_the_done_total_contract_unchanged():
    runner = OperationRunner()
    seen = []

    def fn(progress_callback=None):
        progress_callback(2, 5)
        return 'ok'

    runner.execute(fn, progress=lambda d, t: seen.append((d, t)), on_result=lambda r: None)
    assert seen == [(2, 5)], "the existing (done, total) callback must be forwarded verbatim"


def test_a_STALE_result_is_DISCARDED_a_newer_request_wins():
    """The stale-suppression hazard: a slow result must not overwrite a newer request. Here `fn` bumps
    the generation mid-flight (a newer request arriving), so its own result is dropped."""
    runner = OperationRunner()
    gen = runner.next_generation()
    delivered = []

    def fn():
        runner.next_generation()          # a NEWER request supersedes this one while it runs
        return 'stale'

    out = runner.execute(fn, generation=gen, on_result=lambda r: delivered.append(r))
    assert out is None and delivered == [], "a superseded result must be discarded, not delivered"


def test_CANCELLATION_stops_the_work_at_a_progress_boundary():
    runner = OperationRunner()
    token = CancellationToken()
    reached = []

    def fn(progress_callback=None):
        progress_callback(1, 3)           # first boundary — not cancelled yet
        token.cancel()
        progress_callback(2, 3)           # this boundary sees the cancel and unwinds
        reached.append('past cancel')     # must never run
        return 'done'

    out = runner.execute(fn, cancellation=token, on_result=lambda r: reached.append(r))
    assert out is None
    assert 'past cancel' not in reached and 'done' not in reached, "cancellation did not stop the work"


def test_a_TYPED_error_reaches_on_error_with_its_cause():
    """A failure is transported to `on_error` as the exception object — a typed `pycat.utils.errors` one
    if the compute raised it — so the UI states the cause instead of a raw traceback."""
    from pycat.utils.errors import ScientificAssumptionError
    runner = OperationRunner()
    errors = []

    def fn():
        raise ScientificAssumptionError("only 2 replicates — the inferential unit is the replicate")

    out = runner.execute(fn, on_error=lambda e: errors.append(e))
    assert out is None
    assert len(errors) == 1 and isinstance(errors[0], ScientificAssumptionError)
    assert 'replicate' in str(errors[0]), "the transported error must carry its stated cause"


def test_without_on_error_the_exception_PROPAGATES():
    """No `on_error` handler ⇒ the failure is not swallowed; it raises on the caller's thread, so a
    `try/except` around `execute` still works."""
    runner = OperationRunner()

    def fn():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        runner.execute(fn)
