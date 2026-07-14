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
import io
import os
import pathlib
import re
import shutil
import sys
import tempfile
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
    # **Tag** what `@pytest.fixture` decorates, so the runner can find it after exec.
    # The stub used to return the function unchanged, which made fixtures INVISIBLE — and a
    # test needing one was reported PASS without ever running.
    def _fixture(*args, **kwargs):
        def tag(fn):
            fn._pycat_fixture = True
            return fn
        # Bare `@pytest.fixture` (no parens) passes the function directly.
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return tag(args[0])
        return tag

    stub.fixture = _fixture
    stub.SkipTest = _SkipTest
    sys.modules['pytest'] = stub
    return stub


# ── The runner reported PASS for tests it never ran ──────────────────────────────────────
#
# A test taking a parameter but carrying **no ``parametrize`` decorator** — i.e. a test asking for
# a **fixture** — fell into the branch that builds its cases from ``SCIENTIFIC_MODULES``. For a
# file that does not define that name, ``ns.get(...)`` returned ``[]``, so ``combinations`` was
# **empty**, **the loop body never executed**, ``n_fail`` stayed ``0`` — and the runner printed::
#
#     test_a_CHANGED_file_gets_a_FRESH_reader   PASS
#
# ***A test whose body is `assert False` reported PASS.*** Verified with a canary.
#
# **23 tests were affected**, and they are not peripheral ones:
#
#   * ``test_reader_cache`` — **4 of 5**
#   * ``test_one_plane_reads_one_plane`` — **3 of 5** *(the perf guard the whole 1.6 arc turns on)*
#   * ``test_file_io`` — **5 of 5**, the entire file
#
# The guards protecting the BioIO migration have been reporting green **without executing a single
# line.** *This is the same failure as a metric that cannot catch its own bug, one level up: the
# thing that checks the checks was not being checked.*
#
# So: **inject the fixtures**, and where a fixture cannot be provided, **FAIL** — never pass.

class _MonkeyPatch:
    """The subset of ``pytest``'s monkeypatch the suite actually uses, with real undo."""

    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value=None, raising=True):
        # Support both `setattr(obj, 'name', value)` and `setattr('mod.attr', value)`.
        if isinstance(target, str):
            module_name, _, attribute = target.rpartition('.')
            target, name, value = importlib.import_module(module_name), attribute, name
        self._undo.append((target, name, getattr(target, name, _MISSING)))
        setattr(target, name, value)

    def setitem(self, mapping, key, value):
        self._undo.append((mapping, key, mapping.get(key, _MISSING), True))
        mapping[key] = value

    def delattr(self, target, name, raising=True):
        self._undo.append((target, name, getattr(target, name, _MISSING)))
        delattr(target, name)

    def setenv(self, name, value):
        self.setitem(os.environ, name, str(value))

    def undo(self):
        for entry in reversed(self._undo):
            if len(entry) == 4:
                mapping, key, old, _ = entry
                if old is _MISSING:
                    mapping.pop(key, None)
                else:
                    mapping[key] = old
            else:
                target, name, old = entry
                if old is _MISSING:
                    try:
                        delattr(target, name)
                    except AttributeError:
                        pass
                else:
                    setattr(target, name, old)
        self._undo.clear()


class _MISSING:
    pass


class _CaptureFixture:
    """``capsys``: capture stdout/stderr for the duration of the test."""

    def __init__(self):
        self._out, self._err = io.StringIO(), io.StringIO()
        self._saved = (sys.stdout, sys.stderr)
        sys.stdout, sys.stderr = self._out, self._err

    def readouterr(self):
        value = _Captured(self._out.getvalue(), self._err.getvalue())
        self._out.truncate(0); self._out.seek(0)
        self._err.truncate(0); self._err.seek(0)
        return value

    def close(self):
        sys.stdout, sys.stderr = self._saved


class _Captured:
    def __init__(self, out, err):
        self.out, self.err = out, err


def _build_fixtures(parameters, namespace=None):
    """Provide the fixtures this test asked for. ``None`` if any of them cannot be provided.

    **Returning ``None`` must make the caller FAIL, not pass.** The whole reason this function
    exists is that a test which could not be run was being counted as one that had.

    Resolves, in order:

    1. the **built-ins** the suite uses — ``tmp_path``, ``monkeypatch``, ``capsys``;
    2. **custom fixtures defined in the test module** — ``@pytest.fixture def counting_reader():``.
       These are the ones that actually guard the 1.6 work (``a_fifty_frame_tiff``,
       ``counting_reader``, ``slow_storage``), and a runner that could not build them was reporting
       their tests **green without executing them.**

    A custom fixture may itself take ``tmp_path`` or another fixture; those are resolved
    recursively. Generator fixtures (``yield``) get their teardown run.
    """
    provided, teardown = {}, []
    namespace = namespace or {}

    def _fail():
        for undo in reversed(teardown):
            undo()
        return None, None

    for parameter in parameters:
        if parameter in ('tmp_path', 'tmpdir'):
            path = pathlib.Path(tempfile.mkdtemp())
            provided[parameter] = path
            teardown.append(lambda p=path: shutil.rmtree(p, ignore_errors=True))

        elif parameter == 'monkeypatch':
            patcher = _MonkeyPatch()
            provided[parameter] = patcher
            teardown.append(patcher.undo)

        elif parameter == 'capsys':
            capture = _CaptureFixture()
            provided[parameter] = capture
            teardown.append(capture.close)

        elif callable(namespace.get(parameter)) and getattr(
                namespace[parameter], '_pycat_fixture', False):
            # A `@pytest.fixture` defined in this test module. Looked up **in the namespace
            # itself** — not in an `id()`-keyed global, which made the resolver depend on hidden
            # state and untestable in isolation.
            factory = namespace[parameter]
            inner_names = [p for p in inspect.signature(factory).parameters]
            inner, inner_teardown = _build_fixtures(inner_names, namespace) if inner_names \
                else ({}, [])
            if inner is None:
                return _fail()
            teardown.extend(inner_teardown)

            try:
                value = factory(**inner)
            except Exception:                          # noqa: BLE001
                return _fail()

            if inspect.isgenerator(value):
                generator = value
                value = next(generator)
                teardown.append(lambda g=generator: next(g, None))

            provided[parameter] = value

        else:
            return _fail()

    return provided, teardown



def _parametrize_cases(function_node, source, namespace):
    """**Every case pytest would generate from this function's parametrize decorators.**

    ── The regex that stood here MISSED 40 % of them ────────────────────────────

    It matched a **single** parameter name and a **single-line** list, and nothing else. It caught
    32 decorators and **missed 20**, including:

    * multi-parameter forms — ``parametrize("scene,expected", [...])``
    * multi-line value lists
    * **computed** value lists — ``parametrize("mod", SCIENTIFIC_MODULES)``, which is *every
      scientific module*, and it was **never run**

    CI collected **433** items. This runner reported **354**. ***I was shipping against a different
    test suite than the one that gates the build*** — which is the exact failure this file's own
    docstring was written to prevent, arrived at from the other direction.

    An AST walk handles every form, because it **reads** the decorator instead of guessing its
    shape.
    """
    cases = []

    for decorator in function_node.decorator_list:
        segment = ast.get_source_segment(source, decorator) or ''
        if 'parametrize' not in segment:
            continue
        if not isinstance(decorator, ast.Call) or len(decorator.args) < 2:
            continue

        # The names: "a", or "a,b", or ["a", "b"].
        try:
            names_node = decorator.args[0]
            if isinstance(names_node, ast.Constant):
                names = [n.strip() for n in str(names_node.value).split(',')]
            else:
                names = [str(ast.literal_eval(element)) for element in names_node.elts]
        except Exception:
            continue

        # The values: a literal list, OR a name/call resolved from the module namespace.
        values_node = decorator.args[1]
        try:
            values = ast.literal_eval(values_node)
        except Exception:
            # `parametrize("mod", SCIENTIFIC_MODULES)` — computed values. Resolve them from the
            # module that has already been executed. **This is the form that hid every scientific
            # module from this runner.**
            try:
                values = eval(compile(ast.Expression(values_node), '<param>', 'eval'), namespace)
            except Exception:
                continue

        try:
            cases.append((names, list(values)))
        except Exception:
            continue

    return cases


def plan_test(parameters, is_parametrized, namespace):
    """**How should this test be run — and CAN it be?** ``'parametrize' | 'fixtures' | None``.

    ``None`` means *it cannot be run*, and the caller must report **FAIL**.

    ── This decision is where the runner was lying ──────────────────────────────────────

    The dispatch was: no parameters → run it; parameters → treat them as ``parametrize`` cases. But
    **a test asking for a FIXTURE has parameters and no ``parametrize`` decorator**, so it fell into
    the parametrize branch — which built its cases from ``SCIENTIFIC_MODULES``, a name most test
    files do not define. ``ns.get(...)`` returned ``[]``, so ``combinations`` was **empty**, the
    loop body never executed, ``n_fail`` stayed ``0``, and the runner printed::

        test_a_CHANGED_file_gets_a_FRESH_reader   PASS

    ***A test whose entire body was `assert False` reported PASS.***

    **23 tests were in this state** — including **4 of 5** reader-cache guards, **3 of 5** one-plane
    perf guards, and the whole of ``test_file_io``. The guards protecting the BioIO migration were
    green **without executing a single line.**

    *This is a metric that could not catch its own bug, one level up.*
    """
    if not parameters:
        return 'run'

    if is_parametrized:
        return 'parametrize'

    # Asking for fixtures. Can they be built?
    fixtures, teardown = _build_fixtures(list(parameters), namespace)
    if fixtures is not None:
        for undo in reversed(teardown):
            undo()
        return 'fixtures'

    # Legacy: a bare `mod` parameter driven by a module-level SCIENTIFIC_MODULES list.
    if namespace.get('SCIENTIFIC_MODULES'):
        return 'parametrize'

    # **Cannot be run. That is a FAILURE, not a pass.** Saying PASS here is what hid 23 tests.
    return None


def _load(path, pytest_stub):
    """Execute the test module, and collect every parametrize case pytest would generate."""
    source = path.read_text(encoding='utf-8')

    stripped = source.replace('import pytest\n', '')
    stripped = re.sub(r'@pytest\.mark\.parametrize\((?:[^()]|\([^()]*\))*\)\s*\n', '', stripped)
    stripped = re.sub(r'@pytest\.mark\.\w+\s*\n', '', stripped)

    namespace = {'__file__': str(path.resolve()), 'pytest': pytest_stub}
    exec(compile(stripped, path.name, 'exec'), namespace)

    # The module has now run, so its module-level values (SCIENTIFIC_MODULES, _linkers(), ...) are
    # available — which is what makes the COMPUTED parametrize forms resolvable at all.
    params = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith('test_'):
            continue
        cases = _parametrize_cases(node, source, namespace)
        if cases:
            params[node.name] = cases

    return namespace, params


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

            # **The dispatch decision, in one place, so it can be tested.** See `plan_test` — this
            # is where the runner was reporting PASS for tests it never ran.
            plan = plan_test(list(sig.parameters), name in params, ns)

            if plan is None:
                # **A test that cannot be run is a FAILURE, not a pass.**
                total += 1
                failed += 1
                unmet = ', '.join(sig.parameters)
                print(f"    {name:36} FAIL: cannot inject fixture(s): {unmet}")
                continue

            if plan == 'run':
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
            # ── Every case pytest would generate, including the ones the old regex missed ──
            #
            # `params[name]` is now a LIST of (names, values) — one entry per parametrize
            # decorator. pytest takes the CROSS PRODUCT when decorators are stacked, and it
            # supports MULTI-NAME forms: parametrize("scene,expected", [...]).
            #
            # The regex this replaced handled neither, and missed 20 of 52 decorators — including
            # `parametrize("mod", SCIENTIFIC_MODULES)`, which is EVERY scientific module.
            import itertools

            if name in params:
                decorators = params[name]
                # Cross-product of every decorator, as pytest does.
                combinations = []
                for bundle in itertools.product(*[
                        [(names, value) for value in values] for names, values in decorators]):
                    kwargs = {}
                    for names, value in bundle:
                        if len(names) == 1:
                            kwargs[names[0]] = value
                        else:
                            for parameter, item in zip(names, value):
                                kwargs[parameter] = item
                    combinations.append(kwargs)
            elif plan == 'fixtures':
                # ── Asking for FIXTURES, and `plan_test` says they can be built ──
                #
                # This used to be the parametrize branch, which built cases from
                # ``SCIENTIFIC_MODULES`` — an **empty list** for most files. The loop never ran,
                # ``n_fail`` stayed 0, and the runner printed **PASS for a test it never executed.**
                fixtures, teardown = _build_fixtures(list(sig.parameters), ns)
                total += 1
                try:
                    fn(**fixtures)
                    print(f"    {name:36} PASS")
                except pytest_stub.SkipTest:
                    total -= 1
                    print(f"    {name:36} skip")
                except Exception as exc:               # noqa: BLE001
                    failed += 1
                    print(f"    {name:36} FAIL: {type(exc).__name__}: {str(exc)[:60]}")
                finally:
                    for undo in reversed(teardown):
                        undo()
                continue

            else:
                # Legacy: a bare `mod` parameter driven by module-level SCIENTIFIC_MODULES.
                only = list(sig.parameters)[0]
                combinations = [{only: value}
                                for value in ns.get('SCIENTIFIC_MODULES', [])]

            n_fail = 0
            first = ''
            for kwargs in combinations:
                total += 1
                # A parametrized test may ALSO ask for a fixture (`(self, axes, sizes, tmp_path)`).
                # Build them per-case so each case gets a clean tmp dir, as pytest does.
                extra = [p for p in sig.parameters if p not in kwargs]
                fixtures, teardown = _build_fixtures(extra, ns) if extra else ({}, [])

                if fixtures is None:
                    n_fail += 1
                    failed += 1
                    if not first:
                        first = f"cannot inject fixture(s): {', '.join(extra)}"
                    continue

                try:
                    fn(**kwargs, **fixtures)
                except Exception as exc:               # noqa: BLE001
                    n_fail += 1
                    failed += 1
                    if not first:
                        first = f"{kwargs}: {type(exc).__name__}: {str(exc)[:40]}"
                finally:
                    for undo in reversed(teardown):
                        undo()
            status = 'PASS' if n_fail == 0 else f'FAIL ({n_fail}/{len(combinations)})'
            print(f"    {name:36} {status}  {first}")

    print(f"\n  {total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
