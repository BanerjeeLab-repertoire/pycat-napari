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
    r = _run('from pycat.batch.console import make_console_output_safe\nmake_console_output_safe()\n')
    assert r.returncode == 0, r.stderr.decode(errors='replace')
    assert b'still running' in r.stdout and b'\\u2192' in r.stdout


def test_batch_output_survives_having_no_console(monkeypatch):
    from pycat.batch.console import make_console_output_safe
    monkeypatch.setattr(sys, 'stdout', None)
    make_console_output_safe()
    print('\u2192 no console, no crash')
    assert sys.stdout is not None


def test_every_batch_run_starts_with_the_guard():
    # Read from the source, not imported: batch_processor is the Qt worker, and this lane has no Qt.
    import ast
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat' / 'batch_processor.py'
    worker = next(n for n in ast.parse(path.read_text(encoding='utf-8')).body
                  if isinstance(n, ast.ClassDef) and n.name == 'BatchWorker')
    run = next(n for n in worker.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
    body = [n for n in run.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    assert ast.unparse(body[0]) == 'make_console_output_safe()'
