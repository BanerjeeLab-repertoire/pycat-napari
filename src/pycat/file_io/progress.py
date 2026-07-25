"""Modal busy/progress plumbing for the file loader (file_io_decomposition).

`_run_with_busy_progress` runs a blocking call OFF the Qt thread behind a modal busy dialog — the one-time
CZI/IMS frame-index parse uses it so the window stays responsive. Extracted VERBATIM from `file_io` into a
mixin; `FileIOClass` inherits it, so every `self._run_with_busy_progress(...)` call site (in the stack
openers) is unchanged. All Qt imports are function-local, exactly as before, so this module imports headless.
"""
from __future__ import annotations

from pycat.utils.errors import StackLoadCancelled


class _ProgressMixin:
    def _run_with_busy_progress(self, fn, title, text, cancellable=True):
        """Run blocking ``fn()`` OFF the Qt thread behind a modal busy dialog; return its result (or
        re-raise). Raises :class:`StackLoadCancelled` on "Give up". Headless → plain sync call.

        Two things the naive version got wrong (both seen opening a streaming CZI): the dialog must
        CLOSE when the work finishes — the finish handler is a main-thread ``QObject`` slot ending a
        ``QEventLoop``, not a worker-thread plain function whose ``dlg.reset()`` never returns the
        modal loop; and "Give up" must FREE the UI — the JVM call can't be interrupted, so cancel
        detaches (drops the orphan's result) rather than ``thread.wait()`` blocking (the X-out hang).
        """
        try:
            from PyQt5.QtCore import (QThread, QObject, pyqtSignal, pyqtSlot, Qt,
                                      QTimer, QEventLoop)
            from PyQt5.QtWidgets import QProgressDialog
        except Exception:
            return fn()

        box = {}

        class _Worker(QObject):
            finished = pyqtSignal()

            def run(self):
                try:
                    box['value'] = fn()
                except BaseException as e:   # reported back to the caller's thread
                    box['error'] = e
                finally:
                    self.finished.emit()

        thread = QThread()
        worker = _Worker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        parent = None
        try:
            _win = getattr(self.viewer, 'window', None)
            parent = getattr(_win, '_qt_window', None)
        except Exception:
            parent = None

        # (min, max) = (0, 0) → indeterminate/busy bar. A "Give up" button lets the user abandon a
        # long parse; label None removes it.
        dlg = QProgressDialog(text, "Give up" if cancellable else None, 0, 0, parent)
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)

        loop = QEventLoop()
        _secs = [0]
        _state = {'cancelled': False}

        # Elapsed-seconds counter (main thread): the work is opaque (no percentage), so a counting-up
        # "…Ns" is what tells the user it is working, not hung.
        def _tick():
            _secs[0] += 1
            try:
                dlg.setLabelText(f"{text}\n\n… {_secs[0]}s elapsed")
            except Exception:
                pass
        _timer = QTimer()
        _timer.setInterval(1000)
        _timer.timeout.connect(_tick)

        class _Bridge(QObject):
            @pyqtSlot()
            def on_finished(self):          # runs on the MAIN thread (queued) — closes the dialog
                _state['done'] = True
                _timer.stop()
                thread.quit()
                if loop.isRunning():
                    loop.quit()
        bridge = _Bridge()
        worker.finished.connect(bridge.on_finished)

        def _on_cancel():
            # `QProgressDialog.close()` (below, on NORMAL completion) also emits `canceled` — ignore
            # that, or every successful open would report itself cancelled. Only a real Give-up click,
            # before the work finishes, counts.
            if _state.get('done'):
                return
            _state['cancelled'] = True
            _timer.stop()
            if loop.isRunning():
                loop.quit()
        if cancellable:
            dlg.canceled.connect(_on_cancel)

        _timer.start()
        thread.start()
        dlg.show()
        loop.exec_()                 # nested loop; the window keeps painting until quit()
        dlg.close()

        if _state['cancelled']:
            # Detach: keep the thread + its main-thread bridge alive (a QThread GC'd mid-run crashes)
            # until the blocking call returns. bridge.on_finished then quits the thread; thread.finished
            # drops the references. The result is discarded, the UI is free NOW.
            # The orphan list is a class attribute on the concrete loader (FileIOClass); reach it via the
            # instance's class so this stays a mixin (importing FileIOClass here would be circular).
            orphans = getattr(type(self), '_orphan_load_threads', None)
            if orphans is None:
                orphans = type(self)._orphan_load_threads = []
            entry = (thread, worker, bridge)
            orphans.append(entry)
            thread.finished.connect(lambda e=entry: e in orphans and orphans.remove(e))
            raise StackLoadCancelled()

        thread.wait()
        if 'error' in box:
            raise box['error']
        return box.get('value')
