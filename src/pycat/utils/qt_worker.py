"""**Run slow work off the Qt thread without restructuring the caller.**

Two freezes in PyCAT have the same cause: ``load_session`` and every ``materialize_stack`` call in
every widget run on the Qt thread, so the window stops painting and Windows paints
"Python is not responding" over it. 1.6.81/82 wired progress bars into those sites, which made the
wait **visible** without making it **shorter** — the bar advances and the window is still frozen,
because the bar and the work share a thread.

This is the other half. The work moves to a ``QThread``; the dialog stays on the Qt thread and keeps
painting.

── The trap this is shaped around ───────────────────────────────────────────────────────

**napari layer creation MUST stay on the main thread.** Touch ``viewer.add_*`` from a worker and you
have traded a freeze for a crash — a worse bug, and an intermittent one.

So this deliberately does NOT take "a function that loads a layer". It takes a function that
*computes and returns a value*, runs that off-thread, and hands the value back **on the caller's
thread**. The caller's ``viewer.add_*`` never moves. That is why the API is synchronous: a callback
or a future would invite people to do the adding inside it.

``materialize_stack`` fits exactly — it is pure decode (numpy/dask, no napari, no Qt) and already
takes ``progress_callback(done, total)``. So a site becomes::

    arr = materialize_stack(layer.data, progress_callback=reporter)      # freezes

    arr = run_with_progress(                                             # does not
        lambda progress: materialize_stack(layer.data, progress_callback=progress),
        title='Loading', text='Decoding frames…', parent=parent)
    viewer.add_image(arr)        # unchanged, still on the main thread

── Why a nested event loop, and what it costs ───────────────────────────────────────────

``dlg.exec_()`` spins a nested event loop until the worker finishes. That is what keeps the window
painting, and it is the same mechanism the BioFormats open already uses (``file_io.py``). The cost is
that the loop also delivers *other* events: without care the user could start a second operation on
top of the first. The dialog is therefore **window-modal**, which is what blocks that.

Headless (no Qt, no display) the work still has to happen: the function is called synchronously with
a no-op progress callback. A brief freeze in a context with no window to freeze, rather than an
import error or a hang.
"""

from __future__ import annotations


def _noop_progress(done, total):
    """Progress with nowhere to go. Headless callers still need the callable."""


def run_with_progress(fn, *, title='Working', text='Working…', parent=None,
                      cancellable=False):
    """Run ``fn(progress)`` on a worker thread; return its value on the CALLER's thread.

    Parameters
    ----------
    fn : callable taking one argument — ``progress(done, total)`` — and returning a value.
        **It must not touch napari or Qt.** It runs on a worker thread; layer creation there is a
        crash, not a freeze. Compute, return, and let the caller add the layer.
    title, text : what the dialog says.
    parent : a QWidget to parent the dialog to (usually ``viewer.window._qt_window``). ``None`` is
        allowed; the dialog is then unparented, which is worse-looking but not broken.
    cancellable : reserved. Cancelling means teaching `fn` to stop, and none of the current callers
        can — a Cancel button that does nothing is worse than none, so this stays False until a
        caller can honour it.

    Returns
    -------
    Whatever ``fn`` returned. Exceptions raised inside ``fn`` are re-raised **here**, on the
    caller's thread, so ``try/except`` around the call still works exactly as it did before.
    """
    try:
        from PyQt5.QtCore import QThread, QObject, pyqtSignal, Qt
        from PyQt5.QtWidgets import QProgressDialog
    except Exception:
        # No Qt: do the work, report nothing. There is no window to keep painting.
        return fn(_noop_progress)

    from PyQt5.QtWidgets import QApplication
    if QApplication.instance() is None:
        # Qt is importable but there is no application (headless test, script). A QProgressDialog
        # without a QApplication is undefined behaviour; a synchronous call is not.
        return fn(_noop_progress)

    from PyQt5.QtCore import QEventLoop, pyqtSlot

    box = {}

    class _Worker(QObject):
        finished = pyqtSignal()
        progressed = pyqtSignal(int, int)

        def run(self):
            try:
                box['value'] = fn(lambda done, total: self.progressed.emit(int(done), int(total)))
            except BaseException as exc:        # noqa: BLE001 — reported to the caller's thread
                box['error'] = exc
            finally:
                self.finished.emit()

    # ── The receiver is a QObject, and that is not decoration ─────────────────
    #
    # Connecting a signal to a PLAIN FUNCTION gives it no thread affinity, so Qt runs it
    # **on the emitting thread** — here, the worker. The slots below touch a QWidget, and
    # touching a widget off the main thread is the crash this whole design exists to
    # avoid. A QObject constructed *here* lives on the main thread, so `AutoConnection`
    # resolves to `QueuedConnection` and the slots run where the dialog lives.
    #
    # It also removes a race. `dlg.exec_()` used to be closed by `dlg.reset()` from the
    # finish handler — but with fast work the worker finishes BEFORE `exec_()` is entered,
    # so `reset()` ran first and `exec_()` then blocked forever with nothing left to close
    # it. (The BioFormats caller in `file_io.py` has the same shape and never sees it: its
    # work is a ~33 s Java call, so the worker cannot win that race. A small stack can.)
    # A queued slot cannot be delivered until the loop is spinning, so "finished before we
    # waited" resolves to "delivered as soon as we wait" instead of a hang.
    class _Bridge(QObject):
        @pyqtSlot(int, int)
        def on_progress(self, done, total):
            try:
                if total > 0:
                    dlg.setMaximum(int(total))
                    dlg.setValue(min(int(done), int(total)))
                else:
                    dlg.setMaximum(0)          # unknown length -> busy bar
            except Exception:
                pass

        @pyqtSlot()
        def on_finished(self):
            thread.quit()
            dlg.hide()
            loop.quit()

    thread = QThread()
    worker = _Worker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    # `cancellable` is False, so the cancel button is None — see the parameter's note.
    dlg = QProgressDialog(text, None, 0, 100, parent)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.WindowModal)      # blocks a second operation starting underneath
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setValue(0)

    loop = QEventLoop()
    bridge = _Bridge()                         # main thread -> queued delivery
    worker.progressed.connect(bridge.on_progress)
    worker.finished.connect(bridge.on_finished)

    thread.start()
    dlg.show()
    loop.exec_()                               # nested loop: the window keeps painting
    thread.wait()
    dlg.close()

    if 'error' in box:
        raise box['error']
    return box.get('value')
