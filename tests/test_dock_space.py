"""**Results docks get room: they tabify with the tall method panel instead of being starved below it.**

A brushable results dock appended below a tall method widget in the same dock area gets almost no height. The
fix reflows the dock AREA (napari `tabify=True`) rather than touching the method widget, so the results dock
takes the full panel height with the parameters one tab-click away — and the method widget is never
reparented, so its state survives. This pins the policy (default tabify, opt-out to stacking, idempotent,
backward-compatible) and the mount helper (headless no-op, tabify kwarg passed, never-lose-the-dock fallback).
The napari window is a fake here — the whole helper is Qt-free by construction.
"""
import pytest

from pycat.utils.user_settings import UserSettings
from pycat.utils.dock_space import (
    reflow_mode, set_reflow_mode, plan_results_mount, add_results_dock,
    PREF_KEY, DEFAULT_MODE, VALID_MODES)

pytestmark = pytest.mark.core


class _FakeWindow:
    """A stand-in for napari's ``viewer.window`` — records every ``add_dock_widget`` call."""
    def __init__(self, *, raise_on_tabify=False):
        self.calls = []
        self._raise_on_tabify = raise_on_tabify

    def add_dock_widget(self, widget, *, name, area, tabify=False):
        if tabify and self._raise_on_tabify:
            raise TypeError("this napari has no tabify kwarg")
        self.calls.append({'widget': widget, 'name': name, 'area': area, 'tabify': tabify})
        return ('dock', name, tabify)


def _store(tmp_path):
    return UserSettings(path=tmp_path / 's.json')


# ── policy ────────────────────────────────────────────────────────────────────────────────────────

def test_the_default_favours_visibility_tabify_and_is_opt_out(tmp_path):
    store = _store(tmp_path)
    assert reflow_mode(store) == 'tabify' == DEFAULT_MODE      # unset → tabify (results visible by default)
    assert reflow_mode(None) == 'tabify'                       # no store at all → still the default


def test_the_mode_persists_and_an_unknown_stored_value_falls_back(tmp_path):
    store = _store(tmp_path)
    set_reflow_mode(store, 'stack')
    assert store.get(PREF_KEY) == 'stack' and reflow_mode(store) == 'stack'
    # a garbage value on disk must not stick — read-time falls back to the default
    store.set(PREF_KEY, 'nonsense')
    assert reflow_mode(store) == DEFAULT_MODE


def test_set_reflow_mode_rejects_an_unknown_mode(tmp_path):
    with pytest.raises(ValueError):
        set_reflow_mode(_store(tmp_path), 'collapse')         # not yet a valid mode → loud, not silent
    assert set(VALID_MODES) == {'tabify', 'stack'}


def test_plan_is_backward_compatible_safe_and_idempotent():
    assert plan_results_mount(mode='stack', has_results_widget=True) == 'stack'      # opt-out → today
    assert plan_results_mount(mode='tabify', has_results_widget=True) == 'tabify'    # default reflow
    assert plan_results_mount(mode='tabify', has_results_widget=False) == 'stack'    # nothing to mount
    assert plan_results_mount(mode='tabify', has_results_widget=True,
                              has_method_panel=False) == 'stack'                     # nothing to tab onto
    assert plan_results_mount(mode='tabify', has_results_widget=True,
                              already_reflowed=True) == 'stack'                      # idempotent


# ── mount helper (Qt-free: fake window) ─────────────────────────────────────────────────────────────

def test_headless_no_window_is_a_clean_noop():
    assert add_results_dock(None, object(), name='VPT Results') is None


def test_default_mount_tabifies_the_results_dock(tmp_path):
    win = _FakeWindow()
    add_results_dock(win, 'W', name='VPT Results', settings=_store(tmp_path))
    assert len(win.calls) == 1
    assert win.calls[0]['tabify'] is True and win.calls[0]['area'] == 'right'


def test_stack_preference_reproduces_todays_behaviour(tmp_path):
    store = _store(tmp_path)
    set_reflow_mode(store, 'stack')
    win = _FakeWindow()
    add_results_dock(win, 'W', name='Batch Results', settings=store)
    assert win.calls[0]['tabify'] is False                    # exactly the old stacked mount


def test_an_already_reflowed_area_does_not_re_tabify(tmp_path):
    win = _FakeWindow()
    add_results_dock(win, 'W', name='2nd Results', settings=_store(tmp_path), already_reflowed=True)
    assert win.calls[0]['tabify'] is False                    # second results dock does not re-reflow


def test_a_napari_without_the_tabify_kwarg_falls_back_and_never_loses_the_dock(tmp_path):
    win = _FakeWindow(raise_on_tabify=True)
    dock = add_results_dock(win, 'W', name='VPT Results', settings=_store(tmp_path))
    # the tabify attempt raised; the helper retried a plain mount so the results still appear
    assert dock == ('dock', 'VPT Results', False)
    assert win.calls[-1]['tabify'] is False
