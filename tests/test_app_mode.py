"""**Beginner/advanced app mode — persisted, first-run BEGINNER, change-notifying.**

Uses an injected `UserSettings` (a temp file) so the real config is never touched.
"""
import pytest

from pycat.utils.user_settings import UserSettings
from pycat.utils.app_mode import (
    AppMode, current_mode, set_mode, is_beginner, is_advanced, toggle_mode, on_mode_change)

pytestmark = pytest.mark.core


def _store(tmp_path):
    return UserSettings(path=tmp_path / 'settings.json')


def test_first_run_is_beginner(tmp_path):
    s = _store(tmp_path)
    assert current_mode(s) is AppMode.BEGINNER and is_beginner(s) and not is_advanced(s)


def test_the_mode_persists_across_a_fresh_instance(tmp_path):
    s = _store(tmp_path)
    set_mode(AppMode.ADVANCED, s)
    assert is_advanced(s)
    again = UserSettings(path=tmp_path / 'settings.json')          # next session
    assert current_mode(again) is AppMode.ADVANCED


def test_toggle_flips_and_persists(tmp_path):
    s = _store(tmp_path)
    assert toggle_mode(s) is AppMode.ADVANCED
    assert toggle_mode(s) is AppMode.BEGINNER


def test_an_unrecognized_stored_value_degrades_to_beginner(tmp_path):
    s = _store(tmp_path)
    s.set('app.mode', 'wizard')                                    # not a valid AppMode
    assert current_mode(s) is AppMode.BEGINNER


def test_a_mode_change_notifies_subscribers(tmp_path):
    s = _store(tmp_path)
    seen = []
    unsub = on_mode_change(seen.append, s)
    set_mode(AppMode.ADVANCED, s)
    set_mode(AppMode.ADVANCED, s)                                  # no change → no fire
    set_mode(AppMode.BEGINNER, s)
    assert seen == [AppMode.ADVANCED, AppMode.BEGINNER]
    unsub()
    set_mode(AppMode.ADVANCED, s)
    assert seen == [AppMode.ADVANCED, AppMode.BEGINNER]            # unsubscribed → silent
