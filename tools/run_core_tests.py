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
import builtins
import contextlib
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


def _ci_installed_packages(repo_root):
    """(import_name, pip_name) for every package the CI workflow installs.

    Read from the workflow, not hard-coded — a hand-maintained copy of a derivable fact
    drifts, which is the lesson of 1.5.444/445.
    """
    workflow = (repo_root / '.github' / 'workflows' / 'core.yml')
    if not workflow.exists():
        return []

    commands = " ".join(
        line.split('#', 1)[0]
        for line in workflow.read_text(encoding='utf-8', errors='ignore').splitlines()
        if 'pip install' in line.split('#', 1)[0]
    )

    # pip name -> the name you actually import
    known = {
        'numpy': 'numpy', 'scipy': 'scipy', 'pandas': 'pandas',
        'matplotlib': 'matplotlib', 'scikit-image': 'skimage',
        'opencv-python-headless': 'cv2', 'pywavelets': 'pywt',
        'simpleitk': 'SimpleITK', 'scikit-learn': 'sklearn',
        'networkx': 'networkx', 'largestinteriorrectangle': 'largestinteriorrectangle',
    }
    return [(imp, pip) for pip, imp in known.items() if pip in commands]


def _setup_environment(repo_root):
    sys.meta_path.insert(0, _Blocker([
        'napari', 'PyQt5', 'PyQt6', 'qtpy', 'aicsimageio', 'cellpose', 'torch',
    ]))
    # ── NEVER STUB A COMPUTE DEPENDENCY ────────────────────────────────────────
    #
    # This block used to fabricate `pywt`, `SimpleITK`, `cv2` and `matplotlib` when they were
    # missing locally. **That is the exact mechanism that hid `sklearn` (1.5.444) and
    # `largestinteriorrectangle` (1.5.442)**: the module imported fine here against a fake
    # package, and went red in CI where the real one was absent.
    #
    # The docstring of this very file says "no conveniences", and it was doing this. A
    # missing compute dependency is a FINDING — either the package belongs in the CI install
    # list, or the import belongs inside a function. It is never something to paper over.
    #
    # The GUI stack is different: it is blocked at the meta-path ON PURPOSE, because the
    # whole point of the headless job is to prove the science imports without it.
    _required = _ci_installed_packages(repo_root)
    _missing = []
    for _import_name, _pip_name in _required:
        try:
            importlib.import_module(_import_name)
        except ImportError:
            _missing.append(f"{_import_name}  (pip install {_pip_name})")

    if _missing:
        # ── If we MUST stub, say so loudly. Do not report a clean run. ─────────
        #
        # Some sandboxes have no network and cannot install these. Stubbing is then the only
        # way to run at all — but a stubbed run is NOT a faithful CI run, and reporting
        # "115/115 passed" from one is how `sklearn` and `largestinteriorrectangle` reached
        # a red build.
        #
        # So: stub if we have to, and print a banner that the result is degraded, listing
        # exactly which packages are fake. The exit code is still meaningful for the tests
        # that DID run; what is not meaningful is the claim that the import surface is clean.
        print("=" * 78)
        print("  WARNING: THIS IS NOT A FAITHFUL CI RUN")
        print("=" * 78)
        print("  These packages are installed by CI but are ABSENT here, and are being")
        print("  STUBBED. Any import error they would have caused is invisible in this run:")
        for _m in _missing:
            print(f"    {_m}")
        print()
        print("  Stubbing is exactly what hid the sklearn (1.5.444) and")
        print("  largestinteriorrectangle (1.5.442) failures: the module imported fine")
        print("  locally against a fake package, and went red in CI.")
        print()
        print("  Install them if you can. If you cannot (no network), then a GREEN result")
        print("  here does NOT mean the import surface is clean — only `pytest -m core` in")
        print("  CI can tell you that.")
        print("=" * 78)
        print()

        for _import_name, _pip_name in _required:
            if any(_import_name in _m for _m in _missing):
                _stub = types.ModuleType(_import_name)
                _stub.wavedecn = _stub.waverecn = lambda *a, **k: None
                sys.modules[_import_name] = _stub

    # `pip install --no-deps -e .` puts src/ on the path. Emulate exactly that, and nothing
    # more — the 1.5.409 bug was hidden by a dev check that did this without noticing that CI
    # would not.
    sys.path.insert(0, str(repo_root / 'src'))
    # The science tests import `tests.fixtures_synthetic`, so the repo root must be on the
    # path too — pytest does this via rootdir, and forgetting it made every science module
    # "fail to load" with ModuleNotFoundError.
    sys.path.insert(0, str(repo_root))

    # The stub must be FAITHFUL. A first version lacked `raises` and `approx`, and duly
    # reported four failures that were entirely its own — a runner that invents failures is
    # worse than no runner, because it trains you to ignore it.
    stub = types.ModuleType('pytest')

    @contextlib.contextmanager
    def _raises(expected, **kwargs):
        try:
            yield
        except expected:
            return
        raise AssertionError(f"DID NOT RAISE {expected}")

    class _Approx:
        def __init__(self, value, rel=None, abs=None):
            self.value, self.rel, self.abs = value, rel, abs

        def __eq__(self, other):
            if self.abs is not None:
                return builtins.abs(other - self.value) <= self.abs
            rel = self.rel if self.rel is not None else 1e-6
            return builtins.abs(other - self.value) <= rel * max(builtins.abs(self.value), 1e-12)

        def __repr__(self):
            return f"approx({self.value})"

    class _SkipTest(Exception):
        pass

    class _MarkStub:
        def __getattr__(self, _name):
            return lambda *a, **k: (lambda fn: fn)

    stub.raises = _raises
    stub.approx = lambda value, **kwargs: _Approx(value, **kwargs)
    stub.fail = lambda msg, **k: (_ for _ in ()).throw(AssertionError(msg))
    stub.skip = lambda *a, **k: (_ for _ in ()).throw(_SkipTest())
    stub.importorskip = lambda name, **k: importlib.import_module(name)
    stub.mark = _MarkStub()
    stub.fixture = lambda *a, **k: (lambda fn: fn)
    stub.SkipTest = _SkipTest
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

    # Every file carrying @pytest.mark.core. The science tests were added in 1.5.434 —
    # they had never been marked, so `pytest -m core` never ran them, and two golden-master
    # reference values had been sitting unfilled (= permanently skipped) since they were
    # written.
    core = sorted(
        p for p in (repo_root / 'tests').glob('test_*.py')
        if 'pytest.mark.core' in p.read_text(encoding='utf-8', errors='ignore')
    )

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
                except pytest_stub.SkipTest:
                    total -= 1
                    print(f"    {name:36} skip")
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
