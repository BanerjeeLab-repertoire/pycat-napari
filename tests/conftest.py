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
