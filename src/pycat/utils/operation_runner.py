"""**One runner every widget uses to run slow work off the Qt thread — so nobody re-derives it.**

Progress parts 1–2 made waits *visible*; several analyses still run *on* the Qt thread, and the
off-thread work that exists (session load, stack decode, scene switch) was written per-site. The audit's
point: *"otherwise every widget will continue implementing this independently."* This standardizes it
once, on top of the existing `qt_worker` (a second threading mechanism would be the exact duplication it
removes):

- **Worker policy** — the compute runs on `qt_worker.run_with_progress`'s worker thread (or synchronously
  when there is no Qt app, e.g. headless/tests). The worker must NOT touch napari/Qt.
- **Main-thread marshalling** — `run_with_progress` returns the value on the CALLER's (main) thread, so
  `on_result` fires there. Layer creation and widget updates belong in `on_result`, never in the worker.
- **Stale-result suppression** — a generation counter: a slow result cannot overwrite a newer request.
  The same hazard the selection deferred lane and the scene switcher already solve; solved here once.
- **Cancellation** — a cooperative token, checked at the boundaries the progress callback fires.
- **Error transport** — a failure reaches `on_error` as the exception object (a typed `pycat.utils.errors`
  one if the compute raised it), so the UI can state the cause instead of hitting a raw traceback.
"""

from __future__ import annotations

import inspect
import threading


class CancellationToken:
    """A cooperative cancel signal. The runner checks it where progress fires; set it with `cancel()`."""

    def __init__(self):
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class _Cancelled(Exception):
    """Internal: raised out of the progress boundary to unwind a cancelled operation. Never surfaces."""


class OperationRunner:
    """Runs a compute off the Qt thread with progress, cancellation, stale-suppression, and typed error
    transport — the one place that policy lives. Hold one per widget (or share one)."""

    def __init__(self):
        self._generation = 0
        self._lock = threading.Lock()

    def next_generation(self) -> int:
        """Claim a fresh generation. A result is delivered only if the runner's generation has not moved
        past the one its request claimed — so issuing a newer request discards the older's result."""
        with self._lock:
            self._generation += 1
            return self._generation

    @property
    def generation(self) -> int:
        return self._generation

    def execute(self, fn, *args, progress=None, on_result=None, on_error=None,
                cancellation=None, generation=None, parent=None,
                title='Working', text='Working…', **kwargs):
        """Run ``fn(*args, **kwargs)`` off the Qt thread. Returns the result (or ``None`` if cancelled,
        stale, or routed to ``on_error``).

        - ``progress`` — an existing ``progress_callback(done, total)``; forwarded to ``fn`` unchanged if
          ``fn`` accepts one, and also drives the dialog bar.
        - ``on_result(result)`` — called on the MAIN thread when the result is current.
        - ``on_error(exc)`` — called with the exception if ``fn`` raised (typed if it raised a typed one).
        - ``cancellation`` — a `CancellationToken`; cancelling stops at the next progress boundary.
        - ``generation`` — claim explicitly, else the runner claims one; a stale result is discarded.
        """
        from pycat.utils.qt_worker import run_with_progress

        gen = generation if generation is not None else self.next_generation()
        _user_progress = progress or (lambda _done, _total: None)
        _accepts_progress = 'progress_callback' in inspect.signature(fn).parameters

        def _work(worker_progress):
            def _progress(done, total):
                if cancellation is not None and cancellation.cancelled:
                    raise _Cancelled()
                _user_progress(done, total)      # the existing (done, total) contract, unchanged
                worker_progress(done, total)     # drive the modal dialog's bar
            if _accepts_progress:
                return fn(*args, progress_callback=_progress, **kwargs)
            # fn has no progress hook: still honour cancellation before it starts.
            if cancellation is not None and cancellation.cancelled:
                raise _Cancelled()
            return fn(*args, **kwargs)

        try:
            result = run_with_progress(_work, title=title, text=text, parent=parent)
        except _Cancelled:
            return None                          # cancelled — no result, no error
        except Exception as exc:                 # broad-ok: the runner's job IS to transport ANY failure to on_error (typed if fn raised one)
            if on_error is not None:
                on_error(exc)
                return None
            raise

        if gen != self._generation:
            return None                          # a newer request superseded this one — discard silently
        if on_result is not None:
            on_result(result)                    # MAIN thread: run_with_progress has returned to the caller
        return result
