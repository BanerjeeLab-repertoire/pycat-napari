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

# NOTE: not a file-level `pytestmark = core` — the two Qt-smoke tests below are `integration` (they request
# `qtbot`), and marks are ADDITIVE, so a blanket `core` would leave them core-selected and trip the headless
# guard (test_ci_dependencies). Each headless test is marked `core` individually instead.


class _FakeQt:
    """Records ``resizeDocks`` calls so the collapse path is testable without a real Qt window."""
    def __init__(self):
        self.resizes = []

    def resizeDocks(self, docks, sizes, orientation):
        self.resizes.append((docks, sizes))


class _FakeWindow:
    """A stand-in for napari's ``viewer.window`` — records every ``add_dock_widget`` call. Optionally carries a
    ``_qt_window`` so the collapse (``resizeDocks``) path can be exercised without importing Qt."""
    def __init__(self, *, raise_on_tabify=False, with_qt=False):
        self.calls = []
        self._raise_on_tabify = raise_on_tabify
        self._qt_window = _FakeQt() if with_qt else None

    def add_dock_widget(self, widget, *, name, area, tabify=False):
        if tabify and self._raise_on_tabify:
            raise TypeError("this napari has no tabify kwarg")
        self.calls.append({'widget': widget, 'name': name, 'area': area, 'tabify': tabify})
        return ('dock', name, tabify)


def _store(tmp_path):
    return UserSettings(path=tmp_path / 's.json')


# ── policy ────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.core
def test_the_default_favours_visibility_tabify_and_is_opt_out(tmp_path):
    store = _store(tmp_path)
    assert reflow_mode(store) == 'tabify' == DEFAULT_MODE      # unset → tabify (results visible by default)
    assert reflow_mode(None) == 'tabify'                       # no store at all → still the default


@pytest.mark.core
def test_the_mode_persists_and_an_unknown_stored_value_falls_back(tmp_path):
    store = _store(tmp_path)
    set_reflow_mode(store, 'stack')
    assert store.get(PREF_KEY) == 'stack' and reflow_mode(store) == 'stack'
    # a garbage value on disk must not stick — read-time falls back to the default
    store.set(PREF_KEY, 'nonsense')
    assert reflow_mode(store) == DEFAULT_MODE


@pytest.mark.core
def test_set_reflow_mode_rejects_an_unknown_mode(tmp_path):
    with pytest.raises(ValueError):
        set_reflow_mode(_store(tmp_path), 'floating')         # not a valid mode → loud, not silent
    assert set(VALID_MODES) == {'tabify', 'collapse', 'stack'}


@pytest.mark.core
def test_plan_is_backward_compatible_safe_and_idempotent():
    assert plan_results_mount(mode='stack', has_results_widget=True) == 'stack'      # opt-out → today
    assert plan_results_mount(mode='tabify', has_results_widget=True) == 'tabify'    # default reflow
    assert plan_results_mount(mode='collapse', has_results_widget=True) == 'collapse'  # stacked-with-room
    assert plan_results_mount(mode='tabify', has_results_widget=False) == 'stack'    # nothing to mount
    assert plan_results_mount(mode='collapse', has_results_widget=True,
                              has_method_panel=False) == 'stack'                     # nothing to reflow against
    assert plan_results_mount(mode='tabify', has_results_widget=True,
                              already_reflowed=True) == 'stack'                      # idempotent
    assert plan_results_mount(mode='collapse', has_results_widget=True,
                              already_reflowed=True) == 'stack'                      # idempotent (collapse too)


# ── mount helper (Qt-free: fake window) ─────────────────────────────────────────────────────────────

@pytest.mark.core
def test_headless_no_window_is_a_clean_noop():
    assert add_results_dock(None, object(), name='VPT Results') is None


@pytest.mark.core
def test_default_mount_tabifies_the_results_dock(tmp_path):
    win = _FakeWindow()
    add_results_dock(win, 'W', name='VPT Results', settings=_store(tmp_path))
    assert len(win.calls) == 1
    assert win.calls[0]['tabify'] is True and win.calls[0]['area'] == 'right'


@pytest.mark.core
def test_stack_preference_reproduces_todays_behaviour(tmp_path):
    store = _store(tmp_path)
    set_reflow_mode(store, 'stack')
    win = _FakeWindow()
    add_results_dock(win, 'W', name='Batch Results', settings=store)
    assert win.calls[0]['tabify'] is False                    # exactly the old stacked mount


@pytest.mark.core
def test_an_already_reflowed_area_does_not_re_tabify(tmp_path):
    win = _FakeWindow()
    add_results_dock(win, 'W', name='2nd Results', settings=_store(tmp_path), already_reflowed=True)
    assert win.calls[0]['tabify'] is False                    # second results dock does not re-reflow


@pytest.mark.core
def test_a_napari_without_the_tabify_kwarg_falls_back_and_never_loses_the_dock(tmp_path):
    win = _FakeWindow(raise_on_tabify=True)
    dock = add_results_dock(win, 'W', name='VPT Results', settings=_store(tmp_path))
    # the tabify attempt raised; the helper retried a plain mount so the results still appear
    assert dock == ('dock', 'VPT Results', False)
    assert win.calls[-1]['tabify'] is False


@pytest.mark.core
def test_collapse_mode_mounts_stacked_then_grows_the_results_dock(tmp_path):
    store = _store(tmp_path)
    set_reflow_mode(store, 'collapse')
    win = _FakeWindow(with_qt=True)
    dock = add_results_dock(win, 'W', name='VPT Results', settings=store)
    # collapse mounts stacked (NOT tabified) ...
    assert win.calls[0]['tabify'] is False
    # ... then grows the results dock via resizeDocks so the tall method panel shrinks to give it room
    assert len(win._qt_window.resizes) == 1
    resized_docks, sizes = win._qt_window.resizes[0]
    assert resized_docks == [dock] and sizes[0] > 0


@pytest.mark.core
def test_collapse_fires_the_resize_even_when_qtpy_is_absent(tmp_path, monkeypatch):
    # The minimal `core` CI lane has NO qtpy. `_apply_collapse` must still call resizeDocks (Qt.Vertical is
    # just the int 2) rather than silently no-op because `from qtpy.QtCore import Qt` failed — that swallowed
    # ImportError only surfaced as a failing test in the minimal lane (the regression this pins).
    import sys
    monkeypatch.setitem(sys.modules, 'qtpy', None)
    monkeypatch.setitem(sys.modules, 'qtpy.QtCore', None)
    store = _store(tmp_path)
    set_reflow_mode(store, 'collapse')
    win = _FakeWindow(with_qt=True)
    dock = add_results_dock(win, 'W', name='VPT Results', settings=store)
    assert len(win._qt_window.resizes) == 1                        # resize fired despite no qtpy
    resized_docks, sizes = win._qt_window.resizes[0]
    assert resized_docks == [dock] and sizes[0] > 0


@pytest.mark.core
def test_collapse_mode_is_a_clean_noop_without_a_qt_window(tmp_path):
    # no _qt_window (e.g. a stripped/headless window object) → stacked mount, resize simply skipped, no crash
    store = _store(tmp_path)
    set_reflow_mode(store, 'collapse')
    win = _FakeWindow(with_qt=False)
    dock = add_results_dock(win, 'W', name='VPT Results', settings=store)
    assert dock == ('dock', 'VPT Results', False) and win.calls[0]['tabify'] is False


# ── Qt-smoke: the reflow drives the REAL Qt dock primitives our design relies on ────────────────────
# (integration — a live QMainWindow with real QDockWidgets; the `core` lane stays headless.)

class _RecordingMainWindow:
    """A REAL napari-shaped ``viewer.window``: ``add_dock_widget`` backed by an actual ``QMainWindow`` with
    real ``QDockWidget``s, so tabify/resizeDocks exercise true Qt behaviour. Records ``resizeDocks`` calls."""
    def __init__(self, qtbot):
        from qtpy.QtWidgets import QMainWindow
        self.resizes = []
        _outer = self

        class _MW(QMainWindow):
            def resizeDocks(self, docks, sizes, orientation):
                _outer.resizes.append((list(docks), list(sizes)))
                return super().resizeDocks(docks, sizes, orientation)

        self._qt_window = _MW()
        qtbot.addWidget(self._qt_window)
        self._docks = []

    def add_dock_widget(self, widget, *, name, area, tabify=False):
        from qtpy.QtWidgets import QDockWidget
        from qtpy.QtCore import Qt
        d = QDockWidget(name)
        d.setWidget(widget)
        self._qt_window.addDockWidget(Qt.RightDockWidgetArea, d)
        if tabify and self._docks:
            self._qt_window.tabifyDockWidget(self._docks[0], d)
        self._docks.append(d)
        return d


@pytest.mark.integration
def test_qt_smoke_tabify_really_tabs_the_results_dock_and_preserves_method_state(qtbot, tmp_path):
    from qtpy.QtWidgets import QLabel
    win = _RecordingMainWindow(qtbot)
    method = QLabel("method params")                       # stands in for the (tall) method panel + its state
    win.add_dock_widget(method, name="Method", area='right')
    method_dock = win._docks[0]

    results = QLabel("plots + linked table")
    dock = add_results_dock(win, results, name="VPT Results", settings=_store(tmp_path))  # default = tabify

    # the results dock is REALLY tabified with the method dock (Qt, not our fake)
    assert dock in win._qt_window.tabifiedDockWidgets(method_dock)
    # and the method widget was never reparented/rebuilt — its state is intact (the constraint that matters)
    assert method_dock.widget() is method and method.text() == "method params"


@pytest.mark.integration
def test_qt_smoke_collapse_grows_the_results_dock_via_real_resizeDocks(qtbot, tmp_path):
    from qtpy.QtWidgets import QLabel
    store = _store(tmp_path)
    set_reflow_mode(store, 'collapse')
    win = _RecordingMainWindow(qtbot)
    method = QLabel("method params")
    win.add_dock_widget(method, name="Method", area='right')
    method_dock = win._docks[0]

    results = QLabel("plots")
    dock = add_results_dock(win, results, name="VPT Results", settings=store)

    # collapse does NOT tab — the docks stay stacked, and resizeDocks was invoked to grow the results dock
    assert dock not in win._qt_window.tabifiedDockWidgets(method_dock)
    assert win.resizes and win.resizes[-1][0] == [dock]
    # the method panel is untouched (grown-not-closed, state preserved)
    assert method_dock.widget() is method and method.text() == "method params"
