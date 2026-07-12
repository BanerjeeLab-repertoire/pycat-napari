#!/usr/bin/env python3
"""
Run the ``core`` test suite the way CI runs it — no conveniences.

Why this exists
---------------
Two guard bugs shipped in a row (1.5.428, 1.5.432) because the development check was **more
forgiving than the runner it stood in for**:

* ``sys.path.insert(0, 'src')`` in a dev check — which does not exist in CI, and hid the fact
  that PyCAT is a **src-layout** package that must be installed before ``import pycat`` works
  at all (1.5.409: *every* test failed, and the log said so plainly).
* ``exec``-ing test *bodies* with paths injected into the namespace — so a bare ``SRC``
  (the module defines ``_SRC``) was resolved by the harness and never looked up. **The
  undefined-name guard shipped with an undefined name** (1.5.432), and running it properly
  immediately found a *second* instance of the same bug.

The lesson, written down twice and applied once:

    A test environment that differs from the real one in a convenient way will hide exactly
    the bugs that matter.

So: **execute the module, call the test functions, inject nothing.** Block the GUI stack at the
meta-path so it raises a genuine ``ImportError`` rather than being quietly stubbed. Supply the
parametrize values, because the parametrized tests are the ones that exercise all 13 science
modules and both spatial statistics — skipping them is how five of eight tests went unchecked.

Usage::

    python tools/run_core_tests.py
"""

import ast
import importlib
import inspect
import pathlib
import re
import sys
import types


# ── The CI environment: compute deps present, GUI stack ABSENT ─────────────────────────
#
# Blocking at the meta-path makes the import raise ImportError, which is what a missing
# package actually does. Setting ``sys.modules[x] = None`` instead produces misleading
# ``AttributeError``s — it made four science modules look broken when they were fine.
class _Blocker:
    def __init__(self, names):
        self.names = set(names)

    def find_module(self, fullname, path=None):
        return self if fullname.split('.')[0] in self.names else None

    def load_module(self, fullname):
        raise ImportError(f"No module named '{fullname}' (not installed in the headless CI)")


def _setup_environment(repo_root):
    sys.meta_path.insert(0, _Blocker([
        'napari', 'PyQt5', 'PyQt6', 'qtpy', 'aicsimageio', 'cellpose', 'torch',
    ]))
    for name in ('pywt', 'SimpleITK', 'cv2', 'matplotlib'):
        try:
            importlib.import_module(name)
        except ImportError:
            stub = types.ModuleType(name)
            stub.wavedecn = stub.waverecn = lambda *a, **k: None
            sys.modules[name] = stub

    # `pip install --no-deps -e .` puts src/ on the path. Emulate exactly that, and nothing
    # more — the 1.5.409 bug was hidden by a dev check that did this without noticing that CI
    # would not.
    sys.path.insert(0, str(repo_root / 'src'))

    stub = types.ModuleType('pytest')
    stub.fail = lambda msg, **k: (_ for _ in ()).throw(AssertionError(msg))
    stub.importorskip = lambda name, **k: importlib.import_module(name)
    sys.modules['pytest'] = stub
    return stub


def _load(path, pytest_stub):
    """Execute the test module, capturing its parametrize values before stripping them."""
    source = path.read_text(encoding='utf-8')

    params = {}
    for m in re.finditer(
            r'@pytest\.mark\.parametrize\(\s*"(\w+)",\s*(\[[^\]]*\])\s*\)\s*\ndef (\w+)',
            source):
        try:
            params[m.group(3)] = (m.group(1), ast.literal_eval(m.group(2)))
        except (ValueError, SyntaxError):
            pass

    stripped = source.replace('import pytest\n', '')
    stripped = re.sub(r'@pytest\.mark\.parametrize\([^)]*\)\n', '', stripped)
    stripped = re.sub(r'@pytest\.mark\.\w+\n', '', stripped)

    ns = {'__file__': str(path.resolve()), 'pytest': pytest_stub}
    exec(compile(stripped, path.name, 'exec'), ns)
    return ns, params


def main():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    pytest_stub = _setup_environment(repo_root)

    core = [
        repo_root / 'tests' / 'test_no_undefined_names.py',
        repo_root / 'tests' / 'test_headless_science.py',
        repo_root / 'tests' / 'test_spatial_nulls.py',
    ]

    total = failed = 0
    for path in core:
        if not path.exists():
            print(f"  {path.name}: MISSING")
            failed += 1
            continue

        try:
            ns, params = _load(path, pytest_stub)
        except Exception as exc:                       # noqa: BLE001
            print(f"  {path.name}: MODULE FAILED TO LOAD: {type(exc).__name__}: {exc}")
            failed += 1
            continue

        print(f"\n  {path.name}")
        for name in sorted(k for k in ns if k.startswith('test_') and callable(ns[k])):
            fn = ns[name]
            sig = inspect.signature(fn)

            if not sig.parameters:
                total += 1
                try:
                    fn()
                    print(f"    {name:36} PASS")
                except Exception as exc:               # noqa: BLE001
                    failed += 1
                    print(f"    {name:36} FAIL: {type(exc).__name__}: {str(exc)[:60]}")
                continue

            # Parametrized. These are the tests that cover all 13 science modules and both
            # spatial statistics — skipping them left five of eight unchecked.
            if name in params:
                argname, values = params[name]
            else:
                argname = list(sig.parameters)[0]
                values = ns.get('SCIENTIFIC_MODULES', [])

            n_fail = 0
            first = ''
            for value in values:
                total += 1
                try:
                    fn(**{argname: value})
                except Exception as exc:               # noqa: BLE001
                    n_fail += 1
                    failed += 1
                    if not first:
                        first = f"{value}: {type(exc).__name__}: {str(exc)[:40]}"
            status = 'PASS' if n_fail == 0 else f'FAIL ({n_fail}/{len(values)})'
            print(f"    {name:36} {status}  {first}")

    print(f"\n  {total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
