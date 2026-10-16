"""Console safety for batch runs -- no Qt, so headless runs and tests can use it."""
import io
import os
import sys


def make_console_output_safe():
    """A batch run must never die on a log line.

    Batch reports progress with ``print``. On Windows, when PyCAT's output goes to a pipe or a file
    (a launcher, an IDE run window, ``> log.txt``), Python encodes it with the locale code page
    (cp1252), which has no arrow or Greek letters -- and one ``print`` of "->" written as an arrow
    raised UnicodeEncodeError and ended the whole run before the first image (seen 2026-10-08). With
    no console at all (``pythonw``), ``sys.stdout`` is None and every ``print`` fails.

    So for the run: characters the stream cannot encode are written as escapes instead of raising,
    and a missing stream becomes a null one. Nothing printed is lost from a UTF-8 console."""
    for name in ('stdout', 'stderr'):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
            continue
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors='backslashreplace')
        except (ValueError, OSError, io.UnsupportedOperation, AttributeError):
            pass    # a stream that cannot be reconfigured (already closed / not a text wrapper) is left as is
