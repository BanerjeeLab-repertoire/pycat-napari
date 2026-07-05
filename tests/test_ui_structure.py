"""
Static structural safety-net tests for the UI layer.

These exist to protect UI *refactoring* (e.g. splitting the large
``ui_modules.py`` into mixins). They parse the source with ``ast`` and need
NO Qt / napari / live display, so they run anywhere and are fast.

What they catch — the exact failure mode behind "a change broke a menu/widget":
a workflow or menu registration references a widget-builder method by name
(``toolbox_functions_ui._add_something``), but a refactor moved/renamed/lost that
method, so the reference silently breaks until someone clicks that item at
runtime. These tests turn that into a test-time failure.

Run: pytest tests/test_ui_structure.py -v
"""

import ast
import os
import re

import pytest

_UI_MODULES = os.path.join(
    os.path.dirname(__file__), "..", "src", "pycat", "ui", "ui_modules.py")


def _read_ui_source():
    with open(_UI_MODULES, "r", encoding="utf-8") as f:
        return f.read()


def _defined_add_methods(src):
    """All widget-builder methods that EXIST, by either binding style:
      1. `def _add_x(self, ...)` inside a class
      2. `self._add_x = lambda **kw: _add_x(self, **kw)` runtime binding
    Returns a set of method names.
    """
    tree = ast.parse(src)
    defined = set()

    # Style 1: def _add_x
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_add_"):
            defined.add(node.name)

    # Style 2: self._add_x = lambda ...  (runtime attribute binding)
    for m in re.finditer(r"self\.(_add_[A-Za-z_0-9]+)\s*=", src):
        defined.add(m.group(1))

    return defined


def _referenced_add_methods(src):
    """All widget-builder methods REFERENCED via a UI accessor:
      - toolbox_functions_ui._add_x
      - self._add_x (call/registration)
    Returns a set of method names.
    """
    refs = set()
    for m in re.finditer(r"toolbox_functions_ui\.(_add_[A-Za-z_0-9]+)", src):
        refs.add(m.group(1))
    return refs


# Methods that are legitimately defined in OTHER ui modules (brightfield_ui,
# frap_ui, advanced_analysis_ui, *_tools.py, etc.) and delegated to. These are
# not defined in ui_modules.py, so exclude them from the "must be defined here"
# check. If a refactor is supposed to keep one of these in ui_modules, remove it
# from this allowlist.
_EXTERNAL_ADD_METHODS = {
    # Populated lazily below by scanning the other ui/tool modules.
}


def _external_add_methods():
    """Scan sibling ui/toolbox modules for `def _add_*` so references to methods
    that live outside ui_modules.py aren't flagged as missing."""
    base = os.path.join(os.path.dirname(__file__), "..", "src", "pycat")
    found = set()
    for root, _dirs, files in os.walk(base):
        for fn in files:
            if not fn.endswith(".py") or fn == "ui_modules.py":
                continue
            try:
                with open(os.path.join(root, fn), "r", encoding="utf-8") as f:
                    s = f.read()
            except Exception:
                continue
            for m in re.finditer(r"def (_add_[A-Za-z_0-9]+)\s*\(", s):
                found.add(m.group(1))
    return found


def test_ui_module_parses():
    """ui_modules.py must be syntactically valid (a refactor that breaks the
    parse fails here immediately, before any import/Qt is attempted)."""
    ast.parse(_read_ui_source())


def test_every_menu_referenced_add_method_exists():
    """Every `toolbox_functions_ui._add_X` referenced anywhere in ui_modules.py
    must resolve to a method defined either in ui_modules.py (def or lambda-bound)
    or in a sibling ui/tool module. Catches menu/workflow registrations left
    dangling by a refactor."""
    src = _read_ui_source()
    defined_here = _defined_add_methods(src)
    defined_elsewhere = _external_add_methods()
    referenced = _referenced_add_methods(src)

    missing = sorted(
        name for name in referenced
        if name not in defined_here and name not in defined_elsewhere)
    assert not missing, (
        "These _add_* methods are referenced in ui_modules.py but not defined "
        "anywhere (a refactor likely moved/renamed/lost them): " + ", ".join(missing))


def test_analysis_workflow_layouts_present():
    """Each analysis UI class must still build its workflow layout attribute
    (condensate_layout, timeseries..., etc.). A refactor that drops one would
    break that whole workflow silently."""
    src = _read_ui_source()
    for layout_attr in ("condensate_layout", "object_coloc_layout",
                        "general_layout", "fibril_layout"):
        assert f"self.{layout_attr}" in src, (
            f"Workflow layout '{layout_attr}' no longer assigned — a workflow "
            f"UI may have been broken by a refactor.")


def test_core_ui_classes_present():
    """The core UI class hierarchy must remain intact. A mixin refactor should
    preserve these public class names (or this test is the reminder to update
    the contract deliberately)."""
    tree = ast.parse(_read_ui_source())
    classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    for required in ("BaseUIClass", "ToolboxFunctionsUI", "AnalysisMethodsUI",
                     "CondensateAnalysisUI", "MenuManager"):
        assert required in classes, (
            f"Class '{required}' missing from ui_modules.py — if this move was "
            f"intentional, update this test to reflect the new structure.")
