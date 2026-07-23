"""**The preferences registry: one data-driven list a settings UI renders, changes forwarded to the owners.**

Pins the model behind the preferences panel: `list_preferences` enumerates every user-adjustable preference
(interface level, results-dock placement) with its options and current value, resolved from an injected store;
`set_preference` forwards a change to the owning module (`app_mode` / `dock_space`), which validates and
persists it — so a round-trip through the registry is visible via that module AND via the next `list`. Unknown
keys and out-of-range values fail loudly rather than silently no-op. No Qt here — the registry is pure data.
"""
import pytest

from pycat.utils.user_settings import UserSettings
from pycat.utils import app_mode, dock_space
from pycat.utils.preferences import (
    list_preferences, set_preference, PreferenceView, PreferenceOption)

pytestmark = pytest.mark.core


def _store(tmp_path):
    return UserSettings(path=tmp_path / 's.json')


def test_the_registry_lists_the_known_preferences_with_options_and_current_values(tmp_path):
    store = _store(tmp_path)
    views = list_preferences(store)
    by_key = {v.key: v for v in views}
    # both known preferences are present, as PreferenceViews with discrete options
    assert app_mode._KEY in by_key and dock_space.PREF_KEY in by_key
    for v in views:
        assert isinstance(v, PreferenceView) and v.label and v.description
        assert v.options and all(isinstance(o, PreferenceOption) for o in v.options)
        assert v.current in {o.value for o in v.options}      # current is always one of the offered options
    # defaults surface: first run is beginner; the dock reflow default is tabify
    assert by_key[app_mode._KEY].current == 'beginner'
    assert by_key[dock_space.PREF_KEY].current == 'tabify'


def test_setting_a_preference_forwards_to_the_owner_and_shows_up_next_time(tmp_path):
    store = _store(tmp_path)
    set_preference(dock_space.PREF_KEY, 'collapse', store)
    # forwarded to the owning module (the source of truth) ...
    assert dock_space.reflow_mode(store) == 'collapse'
    # ... and reflected in the next registry read
    nxt = {v.key: v.current for v in list_preferences(store)}
    assert nxt[dock_space.PREF_KEY] == 'collapse'


def test_setting_the_interface_level_goes_through_app_mode(tmp_path):
    store = _store(tmp_path)
    set_preference(app_mode._KEY, 'advanced', store)
    assert app_mode.is_advanced(store)
    assert {v.key: v.current for v in list_preferences(store)}[app_mode._KEY] == 'advanced'


def test_an_unknown_preference_key_raises(tmp_path):
    with pytest.raises(KeyError):
        set_preference('ui.nonexistent', 'x', _store(tmp_path))


def test_a_value_outside_the_options_raises_and_does_not_persist(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        set_preference(dock_space.PREF_KEY, 'floating', store)
    assert dock_space.reflow_mode(store) == 'tabify'          # unchanged — the bad set did not slip through


def test_options_are_stable_and_cover_each_owning_modules_valid_set(tmp_path):
    by_key = {v.key: v for v in list_preferences(_store(tmp_path))}
    # the reflow preference offers exactly dock_space's valid modes
    assert {o.value for o in by_key[dock_space.PREF_KEY].options} == set(dock_space.VALID_MODES)
    # the interface level offers exactly the AppMode values
    assert {o.value for o in by_key[app_mode._KEY].options} == {m.value for m in app_mode.AppMode}
