"""**PyCAT talks to Qt only through qtpy (1.7.0, Qt6 migration spec Phase 1).**

Every Qt import used to name PyQt5 directly (338 import lines in 103 files), which tied PyCAT to one binding —
a GPL one, under a BSD-licensed package — and to Qt5. They now all go through ``qtpy``, which resolves to
whichever binding is installed, so the binding is a one-line dependency choice. The signal names were the
only real rename: ``pyqtSignal`` / ``pyqtSlot`` are PyQt-only and qtpy does not provide them under PySide, so
they are ``Signal`` / ``Slot`` from ``qtpy.QtCore``.

This is a ratchet in the style of ``test_complexity_budget``, but the ceiling is ZERO: nothing was
grandfathered, so a single direct binding import or ``pyqt*`` name is a regression.
"""
import pathlib
import re

import pytest

pytestmark = pytest.mark.core

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_BINDINGS = r"(?:PyQt5|PyQt6|PySide2|PySide6)"
_DIRECT_IMPORT = re.compile(rf"^[ \t]*(?:from[ \t]+{_BINDINGS}\b|import[ \t]+{_BINDINGS}\b)", re.M)
_PYQT_ONLY_NAMES = re.compile(r"\bpyqt(?:Signal|Slot|Property|BoundSignal)\b")
# PyQt accepts a TUPLE of types in findChildren/findChild; PySide6 raises TypeError. Under PySide6 this one call
# stopped 14 of the 19 analysis methods from opening. Use `base_ui.find_children_of_types`.
_TUPLE_FIND = re.compile(r"\.findChild(?:ren)?\(\s*\(")


def _python_files():
    return sorted(list((_ROOT / "src").rglob("*.py")) + list((_ROOT / "tests").rglob("*.py")))


def _hits(pattern):
    out = []
    for p in _python_files():
        if p.name == pathlib.Path(__file__).name:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in pattern.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            out.append(f"{p.relative_to(_ROOT)}:{line}: {m.group(0).strip()}")
    return out


def test_no_module_imports_a_qt_binding_directly():
    hits = _hits(_DIRECT_IMPORT)
    assert not hits, (
        "these import a Qt binding directly instead of through qtpy:\n    " + "\n    ".join(hits) +
        "\n\nUse `from qtpy.QtWidgets import ...` (QtCore / QtGui likewise). qtpy resolves to the installed "
        "binding; a direct import ties PyCAT back to one binding.")


def test_no_findChildren_with_a_tuple_of_types():
    hits = [h for h in _hits(_TUPLE_FIND) if not h.startswith("tests")]
    assert not hits, (
        "findChildren((A, B)) is PyQt-only — PySide6 raises TypeError:\n    " + "\n    ".join(hits) +
        "\n\nUse pycat.ui.base_ui.find_children_of_types(widget, (A, B)).")


def test_no_module_uses_the_pyqt_only_signal_names():
    hits = _hits(_PYQT_ONLY_NAMES)
    assert not hits, (
        "these use PyQt-only names that do not exist under PySide:\n    " + "\n    ".join(hits) +
        "\n\nUse `Signal` / `Slot` / `Property` from qtpy.QtCore.")
