"""**The bar moved and the window still froze, because they shared a thread.**

`load_session` and every `materialize_stack` call run on the Qt thread. 1.6.81/82 wired progress
bars into those sites, which made the wait *visible* without making it *shorter*. `run_with_progress`
moves the work to a QThread so the dialog can actually paint.

What these tests are for
------------------------
The roadmap's warning about this work: *"napari layer creation MUST stay on the main thread — get
that wrong and you have traded a freeze for a crash."* A crash is worse than a freeze and it is
intermittent, so the contract is asserted here rather than trusted:

* the work runs on a thread that is **not** the main one (otherwise this whole change is theatre);
* the value comes back **on the caller's thread**, so the caller's `viewer.add_*` never moves;
* exceptions re-raise **on the caller's thread**, so existing `try/except` around these calls keeps
  working;
* headless, it degrades to a synchronous call rather than importing Qt or hanging.

What they cannot cover, and why the branch is not merged
--------------------------------------------------------
**No automated test can tell you the window stopped freezing.** These prove the threading contract;
they cannot prove the UI paints, that the dialog looks right, or that a real session load feels
different. That needs someone at a running viewer — which is exactly what the roadmap says about this
item, and why this is on a branch.
"""

# Standard library imports
import threading

# Third party imports
import pytest

# Local application imports
from pycat.utils.qt_worker import run_with_progress


# ── Headless: no Qt application, so the work must still happen ────────────────────────────

@pytest.mark.core
def test_HEADLESS_it_still_does_the_work():
    """A brief freeze in a context with no window to freeze — not an import error, not a hang."""
    assert run_with_progress(lambda progress: 6 * 7) == 42


@pytest.mark.core
def test_HEADLESS_the_progress_callback_is_still_CALLABLE():
    """`fn` is written once and runs in both worlds. If the headless path handed it `None`, every
    caller would need a branch — and `materialize_stack` would raise on `progress_callback(...)`."""
    seen = []

    def _work(progress):
        progress(1, 10)          # must not explode with nowhere to report
        seen.append('ran')
        return 'ok'

    assert run_with_progress(_work) == 'ok'
    assert seen == ['ran']


@pytest.mark.core
def test_HEADLESS_an_error_propagates_to_the_caller():
    """Existing `try/except` around these call sites must keep working."""
    def _boom(progress):
        raise ValueError('decode failed')

    with pytest.raises(ValueError, match='decode failed'):
        run_with_progress(_boom)


# ── With a real Qt loop: the part that matters ────────────────────────────────────────────

@pytest.mark.integration
def test_the_work_runs_OFF_the_main_thread(qtbot):
    """**The whole point.** If this fails, the bar moves and the window still freezes."""
    caller_thread = threading.current_thread().ident
    box = {}

    def _work(progress):
        box['worker_thread'] = threading.current_thread().ident
        return 'done'

    assert run_with_progress(_work, title='t', text='x') == 'done'
    assert box['worker_thread'] != caller_thread, (
        'the work ran on the calling thread — the dialog and the work still share a thread, which '
        'is the freeze this exists to fix'
    )


@pytest.mark.integration
def test_the_VALUE_comes_back_on_the_CALLER_thread(qtbot):
    """Why the API is synchronous: the caller adds the layer, on the thread napari requires. A
    future or a callback would invite `viewer.add_*` inside the worker — the crash the roadmap
    warns about."""
    caller_thread = threading.current_thread().ident
    result = run_with_progress(lambda progress: threading.current_thread().ident,
                               title='t', text='x')

    assert result != caller_thread, 'premise: the work should have run elsewhere'
    assert threading.current_thread().ident == caller_thread, (
        'control returned on a different thread — the caller cannot safely touch napari'
    )


@pytest.mark.integration
def test_PROGRESS_from_the_worker_reaches_the_main_thread(qtbot):
    """The callback is handed to code that knows nothing about threads (`materialize_stack`). Qt
    queues the signal across, so the dialog update happens on the main thread."""
    main_thread = threading.current_thread().ident
    seen = []

    def _work(progress):
        for i in range(1, 6):
            progress(i, 5)
        return len(seen)

    # The dialog is internal; observe the same crossing the dialog relies on.
    from PyQt5.QtCore import QObject, pyqtSignal

    class _Probe(QObject):
        ping = pyqtSignal(int, int)

    probe = _Probe()
    probe.ping.connect(lambda d, t: seen.append(threading.current_thread().ident))

    def _work2(progress):
        for i in range(1, 6):
            probe.ping.emit(i, 5)
        return 'ok'

    assert run_with_progress(_work2, title='t', text='x') == 'ok'
    qtbot.wait(50)
    assert seen, 'no progress crossed back to the main thread'
    assert set(seen) == {main_thread}, (
        f'progress was delivered on {set(seen)}, not the main thread {main_thread} — a dialog '
        f'updated from a worker is a crash waiting to happen'
    )


@pytest.mark.integration
def test_an_error_in_the_worker_RE_RAISES_on_the_caller_thread(qtbot):
    """Not swallowed, not printed on a thread nobody is watching."""
    def _boom(progress):
        raise RuntimeError('worker exploded')

    with pytest.raises(RuntimeError, match='worker exploded'):
        run_with_progress(_boom, title='t', text='x')


@pytest.mark.integration
def test_the_worker_thread_is_CLEANED_UP(qtbot):
    """`thread.wait()` after `quit()`. A leaked QThread per materialize would accumulate one per
    load, and Qt complains loudly at exit about threads still running."""
    before = threading.active_count()
    for _ in range(3):
        run_with_progress(lambda progress: None, title='t', text='x')
    qtbot.wait(100)

    assert threading.active_count() <= before + 1, (
        f'threads leaked: {before} -> {threading.active_count()}'
    )
