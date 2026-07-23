"""**Qt-smoke: the 'Manage local cache…' File-menu action opens the same on-demand cache manager.**

Integration (needs a real QMenu + qtbot). The startup path only offers the cleanup non-blockingly, so a
dismissible prompt needs a way back — this verifies the installed File-menu action exists and, when triggered,
calls `open_cache_manager` (the same grouped dialog), and that the installer is headless-safe with no menu.
"""
import pytest


@pytest.mark.integration
def test_the_installer_adds_a_manage_cache_action_that_opens_the_manager(qtbot, monkeypatch):
    from pycat.file_io import local_cache
    from qtpy.QtWidgets import QMenu
    menu = QMenu("★ Open/Save File(s)")
    qtbot.addWidget(menu)

    action = local_cache.install_cache_menu_action(menu, parent=None)
    assert action is not None
    assert action in menu.actions() and 'Manage local cache' in action.text()

    opened = {'n': 0}
    monkeypatch.setattr(local_cache, 'open_cache_manager', lambda *a, **k: opened.__setitem__('n', opened['n'] + 1))
    action.trigger()
    assert opened['n'] == 1                                    # the menu action opens the on-demand manager


@pytest.mark.integration
def test_the_installer_is_headless_safe_with_no_menu():
    from pycat.file_io import local_cache
    assert local_cache.install_cache_menu_action(None) is None
