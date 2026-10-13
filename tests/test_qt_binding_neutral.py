"""**Behaviour that differs between Qt bindings, pinned on whichever binding is installed (1.7.0).**

The import swap to qtpy was mechanical; these are the runtime differences the PySide6 smoke pass found.
Run the file under each binding (``QT_API=pyqt5`` / ``QT_API=pyside6``); every test must pass on both.
"""
import pytest

pytestmark = pytest.mark.base


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("qtpy.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_find_children_of_types_finds_every_type_once_in_order(qapp):
    """PySide6 rejects ``findChildren((A, B))``; the helper queries per type and de-duplicates."""
    from qtpy.QtWidgets import QWidget, QVBoxLayout, QPushButton, QComboBox, QLineEdit, QLabel
    from pycat.ui.base_ui import find_children_of_types
    root = QWidget(); lay = QVBoxLayout(root)
    widgets = [QPushButton("a"), QComboBox(), QLineEdit(), QLabel("x"), QPushButton("b")]
    for w in widgets:
        lay.addWidget(w)
    found = find_children_of_types(root, (QPushButton, QComboBox, QLineEdit, QPushButton))
    assert sorted(map(id, found)) == sorted(id(w) for w in widgets if not isinstance(w, QLabel))
    assert len(found) == len(set(map(id, found))), "a child matched twice (repeated type) must appear once"


def test_the_dock_helpers_run_on_a_real_widget_tree(qapp):
    """Both helpers every analysis dock goes through — the ones that raised under PySide6."""
    from qtpy.QtWidgets import QWidget, QVBoxLayout, QPushButton, QComboBox, QSpinBox, QSlider
    from pycat.ui.base_ui import _apply_scroll_guard, _relax_min_widths
    root = QWidget(); lay = QVBoxLayout(root)
    for w in (QPushButton("go"), QComboBox(), QSpinBox(), QSlider()):
        lay.addWidget(w)
    _relax_min_widths(root)
    _apply_scroll_guard(root)


def test_a_stateChanged_int_is_compared_as_a_check_state(qapp):
    """``stateChanged`` delivers an int; under PySide6 ``2 == Qt.Checked`` is False (CheckState is a plain Enum).
    ``Qt.CheckState(state) == Qt.Checked`` is right on both bindings — the local-cache folder cascade uses it."""
    from qtpy.QtCore import Qt
    from qtpy.QtWidgets import QCheckBox
    cb, got = QCheckBox(), []
    cb.stateChanged.connect(lambda s: got.append(Qt.CheckState(s) == Qt.Checked))
    cb.setChecked(True); cb.setChecked(False)
    assert got == [True, False]


def test_a_file_dropped_on_the_window_reaches_pycats_router(qapp, tmp_path):
    """The drop filter (menu_manager._FileDropFilter) on synthetic drag-enter + drop events: the file path
    reaches PyCAT's router, and a drop into a text field is left to the field."""
    from qtpy.QtCore import QMimeData, QUrl, QPoint, QPointF, Qt
    from qtpy.QtGui import QDragEnterEvent, QDropEvent
    from qtpy.QtWidgets import QWidget, QLineEdit
    from pycat.ui.menu_manager import _FileDropFilter
    f = tmp_path / "stack.tif"; f.write_bytes(b"x")
    filt = _FileDropFilter(file_io=None)
    routed = []
    filt._route = routed.extend
    md = QMimeData(); md.setUrls([QUrl.fromLocalFile(str(f))])
    target, field = QWidget(), QLineEdit()
    enter = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    assert filt.eventFilter(target, enter) and enter.isAccepted()
    drop = QDropEvent(QPointF(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    assert filt.eventFilter(target, drop)
    assert [p.replace("\\", "/").lower() for p in routed] == [str(f).replace("\\", "/").lower()]
    assert not filt.eventFilter(field, QDropEvent(QPointF(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))


def test_the_webengine_probe_agrees_with_reality():
    """The probe used to ask about qtpy's wrapper module, which always exists — so it said True everywhere."""
    pytest.importorskip("qtpy")
    import importlib
    from pycat.utils.plot_backend_selection import qtwebengine_available
    from qtpy import API_NAME
    try:
        importlib.import_module(f"{API_NAME}.QtWebEngineWidgets")
        real = True
    except ImportError:
        real = False
    assert qtwebengine_available() is real
