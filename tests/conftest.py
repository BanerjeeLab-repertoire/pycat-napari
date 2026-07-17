"""
Pytest configuration.

Why this file exists
--------------------
``pytest -m core`` does **not** stop pytest from *importing* every module under
``testpaths``. Markers are applied **after** collection, and collection means importing.
So a test module whose *module-scope* imports need napari, PyQt or aicsimageio raises
``ImportError`` during collection and aborts the whole run — no matter what the marker
selects.

That is exactly what broke the ``core`` workflow. Five test modules import the GUI /
file-IO stack::

    test_central_manager    -> napari
    test_data_management    -> pycat.data.data_modules  -> napari
    test_file_io            -> pycat.file_io.file_io    -> aicsimageio
    test_materialize_stack  -> pycat.file_io.file_io    -> aicsimageio
    test_run_pycat          -> pycat.run_pycat          -> napari

The headless job deliberately does not install any of that. 28 tests were *selected* and
none of them ever ran, because collection died first.

So: a test module that cannot be imported **because the GUI/IO stack is intentionally
absent** is skipped rather than treated as an error. This grows by itself — a new GUI test
does not need anyone to remember to add it to an ``--ignore`` list.

Deliberately conservative. A module is only skipped when a package from
``_OPTIONAL_STACK`` is *genuinely not installed* **and** that module imports it (directly,
or through a PyCAT module known to require it). Everything else is collected normally, so
a real import bug is still a hard failure — and ``tests/test_headless_science.py``, which
asserts the 13 scientific modules DO import, is never skipped by this hook, because it
imports nothing heavy at module scope.
"""

import ast
import importlib.util
import os
import pathlib

import pytest

# Packages the headless (`core`) CI deliberately does not install. A test module needing
# any of these at import time is a GUI / file-IO test, not a core scientific one.
_OPTIONAL_STACK = ("napari", "PyQt5", "qtpy", "aicsimageio", "cellpose", "torch")

# PyCAT modules that pull the GUI/IO stack in at import time. A test importing one of these
# transitively needs the stack, even though it never names it.
_GUI_BOUND_PYCAT = ("pycat.data", "pycat.file_io", "pycat.run_pycat", "pycat.ui")


def _absent_packages():
    return {m for m in _OPTIONAL_STACK if importlib.util.find_spec(m) is None}


def pytest_ignore_collect(collection_path, config):
    """Skip test modules that cannot import because the GUI/IO stack is absent."""
    absent = _absent_packages()
    if not absent:
        return False                       # full environment: collect everything

    if collection_path.suffix != ".py" or not collection_path.name.startswith("test_"):
        return False

    try:
        tree = ast.parse(collection_path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError):
        return False

    for node in tree.body:                 # module scope only — that is what breaks collection
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            if name.split(".")[0] in absent:
                return True
            if any(name.startswith(p) for p in _GUI_BOUND_PYCAT):
                return True
    return False


# ── The suite must test the WORKING TREE, not an installed copy ───────────────────────────
#
# A non-editable install makes `pycat` resolve to site-packages, so a bare `pytest` tests
# whatever was last installed — **and passes**. That is the worst possible failure mode: a
# green suite that never executed the code under review. It has already happened here (the
# 2026-07-16 audit recorded it against the `pycat-160` env; the same trap was live in this
# repo's own `pycat`/`pycat-dev` envs).
#
# It cannot be fixed by fixing an env, because the next machine gets it again. So the suite
# refuses to run rather than lie about what it tested.
#
# CI is safe: `.github/workflows/core.yml` installs with `pip install --no-deps -e .`, so
# `pycat` resolves inside the checkout and this passes.
#
# The escape hatch exists for one real case — deliberately testing a built wheel before a
# release — and is deliberately awkward to reach by accident.

def _pycat_import_location():
    import importlib.util
    spec = importlib.util.find_spec("pycat")
    if spec is None or not spec.origin:
        return None
    return pathlib.Path(spec.origin).resolve()


def pytest_configure(config):
    if os.environ.get("PYCAT_ALLOW_INSTALLED"):
        return

    found = _pycat_import_location()
    repo_src = (pathlib.Path(__file__).resolve().parent.parent / "src").resolve()

    if found is None:
        raise pytest.UsageError(
            "`pycat` is not importable at all. Install the working tree with:\n"
            "    pip install --no-deps -e .\n"
            "(or run with PYTHONPATH=src)"
        )

    if repo_src not in found.parents:
        raise pytest.UsageError(
            f"These tests would run against an INSTALLED copy of pycat, not this working tree.\n"
            f"\n"
            f"  importing pycat from : {found}\n"
            f"  this working tree is : {repo_src}\n"
            f"\n"
            f"A bare `pytest` against a non-editable install tests whatever was last installed "
            f"and PASSES — a green suite that never ran your changes. Fix it with:\n"
            f"\n"
            f"    pip install --no-deps -e .\n"
            f"\n"
            f"(`--no-deps` is deliberate — see the note in core.yml.) Or run with PYTHONPATH=src.\n"
            f"To test an installed build on purpose (e.g. checking a wheel before release), set "
            f"PYCAT_ALLOW_INSTALLED=1."
        )
