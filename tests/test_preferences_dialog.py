"""**Qt-smoke: the preferences dialog renders the registry and a click forwards to the owning module.**

Integration (needs a live QDialog + qtbot). Verifies the thin panel over the Qt-free registry: it builds one
radio group per preference with the current value pre-checked, and toggling a different option immediately
persists through `set_preference` into the injected store — visible via the owning module. The logic is tested
in `test_preferences.py`; this proves the widgets are wired to it.
"""
import pytest

from pycat.utils.user_settings import UserSettings
from pycat.utils import app_mode, dock_space


def _store(tmp_path):
    return UserSettings(path=tmp_path / 's.json')


@pytest.mark.integration
def test_the_dialog_builds_one_group_per_preference_with_the_current_value_checked(qtbot, tmp_path):
    from pycat.ui.preferences_dialog import build_preferences_dialog
    store = _store(tmp_path)
    dlg = build_preferences_dialog(store=store)
    assert dlg is not None
    qtbot.addWidget(dlg)
    # a control group for each registered preference, keyed by preference key
    assert set(dlg._pref_buttons) == {app_mode._KEY, dock_space.PREF_KEY}
    # the current value is the checked radio in each group (defaults: beginner, tabify)
    assert dlg._pref_buttons[app_mode._KEY]['beginner'].isChecked()
    assert dlg._pref_buttons[dock_space.PREF_KEY]['tabify'].isChecked()


@pytest.mark.integration
def test_toggling_a_radio_persists_the_change_through_the_owning_module(qtbot, tmp_path):
    from pycat.ui.preferences_dialog import build_preferences_dialog
    store = _store(tmp_path)
    dlg = build_preferences_dialog(store=store)
    qtbot.addWidget(dlg)

    # choose 'collapse' for the results dock — should forward through dock_space and persist
    dlg._pref_buttons[dock_space.PREF_KEY]['collapse'].setChecked(True)
    assert dock_space.reflow_mode(store) == 'collapse'

    # and switch the interface level to advanced
    dlg._pref_buttons[app_mode._KEY]['advanced'].setChecked(True)
    assert app_mode.is_advanced(store)


@pytest.mark.integration
def test_the_dialog_is_none_safe_semantics_hold_with_a_real_store(qtbot, tmp_path):
    # a second dialog opened after a change reflects the persisted value (no stale defaults)
    from pycat.ui.preferences_dialog import build_preferences_dialog
    store = _store(tmp_path)
    dock_space.set_reflow_mode(store, 'stack')
    dlg = build_preferences_dialog(store=store)
    qtbot.addWidget(dlg)
    assert dlg._pref_buttons[dock_space.PREF_KEY]['stack'].isChecked()
    assert not dlg._pref_buttons[dock_space.PREF_KEY]['tabify'].isChecked()


@pytest.mark.integration
def test_install_preferences_action_adds_a_menu_action_to_the_window(qtbot):
    from qtpy.QtWidgets import QMainWindow
    from pycat.ui.preferences_dialog import install_preferences_action

    qmw = QMainWindow()
    qtbot.addWidget(qmw)

    class _Win:
        _qt_window = qmw

    class _Viewer:
        window = _Win()

    action = install_preferences_action(_Viewer())
    assert action is not None
    assert action in qmw.menuBar().actions() and 'Preferences' in action.text()


@pytest.mark.integration
def test_install_preferences_action_is_headless_safe_without_a_qt_window():
    from pycat.ui.preferences_dialog import install_preferences_action

    class _Viewer:
        window = None                                  # no _qt_window → clean no-op, no crash

    assert install_preferences_action(_Viewer()) is None
