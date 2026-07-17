"""**The data layer must be importable with no GUI stack — the guard did not watch it.**

`test_headless_science.py` enforces the headless-import contract, but only over `src/pycat/toolbox/`.
`BaseDataClass` lives in `pycat.data.data_modules`, which is not a toolbox module — so nothing
stopped it importing `napari.utils.notifications` at module scope, and nothing did until
`test_set_data_new_key` (a `core` test of pure dict logic) hit `ModuleNotFoundError: No module named
'napari'` **in CI**, on a headless runner.

The data layer is exactly where this matters: storing a value in a dict must not need a viewer. It now
uses the `pycat.utils.notify` shim (forwards to napari when present, prints otherwise — the same
pattern the physics modules use), and this pins that so the direct import cannot creep back.

Why a test and not just the fix
-------------------------------
The fix is one line's worth of imports. Without a guard, the next person adding a `napari_show_*`
call reaches for the obvious `from napari...` and CI goes red again three commits later, for a reason
that looks unrelated to what they changed. The contract belongs in a test next to the module it
governs.
"""

# Standard library imports
import ast
import pathlib

import pytest

pytestmark = pytest.mark.core

_FORBIDDEN_ROOTS = {"napari", "PyQt5", "PyQt6", "qtpy"}

# The data layer — the modules a `core` test may legitimately reach without a GUI. Kept explicit
# rather than globbed: a new file under `pycat/data/` should be a deliberate addition here, with a
# moment's thought about whether it, too, must stay headless.
_DATA_MODULES = ["data_modules"]

_DATA_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "data"


def _module_level_gui_imports(path):
    """GUI imports made at MODULE scope. Function-body imports are fine — they run only when a
    viewer already exists. Mirrors the check in `test_headless_science`."""
    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    bad = []
    for node in tree.body:                       # module scope only, deliberately
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] in _FORBIDDEN_ROOTS]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in _FORBIDDEN_ROOTS:
                bad.append(node.module)
    return bad


@pytest.mark.parametrize("mod", _DATA_MODULES)
def test_no_module_scope_GUI_import_in_the_data_layer(mod):
    """The static half: catches a re-added `from napari...` at import time, with a clear message,
    before it reaches a headless CI run as an opaque ModuleNotFoundError mid-test."""
    path = _DATA_DIR / f"{mod}.py"
    assert path.exists(), f"{path} moved — update _DATA_MODULES"

    bad = _module_level_gui_imports(path)
    assert not bad, (
        f"pycat.data.{mod} imports {bad} at module scope. The data layer must import headlessly — "
        f"use `pycat.utils.notify` (show_info / show_warning), which forwards to napari when a UI is "
        f"present and prints otherwise. A core test that reaches this module would die in CI with "
        f"'No module named napari'."
    )


def test_BaseDataClass_constructs_with_napari_BLOCKED():
    """The dynamic half: proves the module actually imports and works when napari is genuinely
    absent — the exact CI condition, reproduced. A static scan can miss a transitive import; this
    cannot."""
    import builtins
    import sys

    real_import = builtins.__import__

    def _no_napari(name, *args, **kwargs):
        if name == "napari" or name.startswith("napari."):
            raise ModuleNotFoundError("No module named 'napari'")
        return real_import(name, *args, **kwargs)

    saved = {k: v for k, v in sys.modules.items() if k == "napari" or k.startswith("napari")}
    for k in saved:
        del sys.modules[k]
    # Force a fresh import of the module under test so the blocked-napari state is exercised.
    sys.modules.pop("pycat.data.data_modules", None)

    builtins.__import__ = _no_napari
    try:
        import pycat.data.data_modules as dm
        obj = dm.BaseDataClass()
        obj.set_data("a_brand_new_key", 123)          # the exact path CI exercised
        assert obj.get_data("a_brand_new_key") == 123
    finally:
        builtins.__import__ = real_import
        sys.modules.update(saved)
