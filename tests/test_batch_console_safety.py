"""A batch run must never die on a log line (UnicodeEncodeError on a cp1252 pipe; no console at all)."""
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.base

_SCRIPT = (
    "import sys\n"
    "{setup}"
    "print('[PyCAT Batch] top-hat + Otsu \\u2192 median object size, \\u03c3 = 2, \\u00b5m')\n"
    "print('still running')\n"
)


def _run(setup):
    env = dict(os.environ, PYTHONIOENCODING='cp1252')        # what a Windows pipe / log file gets
    return subprocess.run([sys.executable, '-c', _SCRIPT.format(setup=setup)], env=env,
                          capture_output=True, timeout=120)


def test_the_failure_is_real_without_the_guard():
    r = _run('')
    assert r.returncode != 0 and b'UnicodeEncodeError' in r.stderr


def test_batch_output_survives_a_cp1252_pipe():
    r = _run('from pycat.batch_processor import make_console_output_safe\nmake_console_output_safe()\n')
    assert r.returncode == 0, r.stderr.decode(errors='replace')
    assert b'still running' in r.stdout and b'\\u2192' in r.stdout


def test_batch_output_survives_having_no_console(monkeypatch):
    from pycat.batch_processor import make_console_output_safe
    monkeypatch.setattr(sys, 'stdout', None)
    make_console_output_safe()
    print('\u2192 no console, no crash')
    assert sys.stdout is not None


def test_every_batch_run_starts_with_the_guard():
    import inspect
    from pycat.batch_processor import BatchWorker
    assert 'make_console_output_safe()' in inspect.getsource(BatchWorker.run).split('\n', 3)[1]
