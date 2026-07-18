"""``FileIOClass._run_with_busy_progress`` — the worker dialog behind the CZI/BioFormats open.

It runs a blocking ``fn()`` on a ``QThread`` behind a modal busy dialog and returns the value on the
caller's thread. Two bugs this pins, both found from the viewer opening a streaming CZI:

* **It must CLOSE when the work finishes** (not hang open until the user X's it). The finish handler
  is a main-thread ``QObject`` slot, so ``dlg.close()`` on the main thread actually ends the modal
  loop.
* **It must NOT report itself cancelled on success.** ``QProgressDialog.close()`` emits ``canceled``;
  if the cancel handler acts on that, every successful open aborts as "cancelled" (it did). The
  handler ignores the close-triggered signal once the work is done.

These need a real Qt event loop (``qtbot``), so they are integration-marked and skip in a headless
run without a display.
"""

import threading

import pytest


@pytest.mark.integration
def test_it_returns_the_VALUE_and_does_not_self_cancel(qtbot):
    """The regression: a fast, successful call must return its value — not raise StackLoadCancelled
    because closing the dialog emitted `canceled`."""
    from pycat.file_io.file_io import FileIOClass

    class _Fake:
        viewer = None                       # parent lookup is guarded; None is fine

    result = FileIOClass._run_with_busy_progress(_Fake(), lambda: 6 * 7, "t", "x")
    assert result == 42


@pytest.mark.integration
def test_the_work_runs_OFF_the_caller_thread(qtbot):
    """If it ran inline, the dialog could never paint — the freeze this exists to fix."""
    from pycat.file_io.file_io import FileIOClass

    class _Fake:
        viewer = None

    caller = threading.current_thread().ident
    box = {}
    FileIOClass._run_with_busy_progress(
        _Fake(), lambda: box.setdefault('tid', threading.current_thread().ident), "t", "x")
    assert box['tid'] != caller


@pytest.mark.integration
def test_an_error_in_the_work_RE_RAISES_on_the_caller(qtbot):
    """A failed open must surface as its own exception, not vanish or hang."""
    from pycat.file_io.file_io import FileIOClass

    class _Fake:
        viewer = None

    def _boom():
        raise ValueError("open failed")

    with pytest.raises(ValueError, match="open failed"):
        FileIOClass._run_with_busy_progress(_Fake(), _boom, "t", "x")


@pytest.mark.integration
def test_non_cancellable_still_returns(qtbot):
    """With no cancel button there is no `canceled` to mis-handle, but the close path must still end
    the loop and return."""
    from pycat.file_io.file_io import FileIOClass

    class _Fake:
        viewer = None

    assert FileIOClass._run_with_busy_progress(
        _Fake(), lambda: "ok", "t", "x", cancellable=False) == "ok"
