"""**Give a results dock room when it mounts beside a tall method panel.**

Reported from the GUI: a brushable results dock is appended *below* the method widget in the same right-hand
dock area, and Qt splits the height between them — so on a method whose parameter panel is very tall (VPT),
the results (the plots + linked table that are the whole payoff of brushing) get almost no space and are
effectively invisible.

The fix reflows the dock **area** rather than touching the method widget. `add_results_dock` mounts a results
dock with napari's ``tabify=True`` by default, so the results dock becomes a TAB alongside the method panel:
it gets the full panel height, the parameters are one tab-click away, and — critically — the method widget is
never reparented, rebuilt, or cleared, so its entered values and field-status markers survive untouched (the
main risk the spec flags). It is reversible (drag the tab out), obvious (tabs are visible), and general (every
method that mounts through this helper inherits it). A user can opt out to today's stacking via the
``ui.results_dock_reflow`` = ``'stack'`` preference.

Default is tabify: it satisfies every constraint — room, reversibility, visible affordance, and guaranteed
state preservation — with no widget surgery. For users who prefer the stacked mental model, ``'collapse'``
grows the results dock via ``QMainWindow.resizeDocks`` (a native primitive, not hand-rolled reparenting) so
the tall method panel shrinks to give room while staying open and reversible by dragging the splitter.
``'stack'`` opts out to today's even height-split.

Qt-free: the napari ``window`` is duck-typed (only ``add_dock_widget`` is called), so the whole helper is
core-tested with a fake window and no Qt import; it no-ops cleanly when there is no window (headless)."""
from __future__ import annotations

#: The user preference: which way a results dock reflows the right-hand dock area when it mounts.
PREF_KEY = 'ui.results_dock_reflow'
#: Default favours VISIBILITY — the reported problem is that results are invisible — but it is opt-out.
DEFAULT_MODE = 'tabify'
#: 'tabify' → results dock tabs with the method panel (full height); 'collapse' → keep the stacked mental
#: model but grow the results dock so the tall method panel shrinks to give it room; 'stack' → today's
#: even height-split behaviour (opt-out).
VALID_MODES = ('tabify', 'collapse', 'stack')


def reflow_mode(settings) -> str:
    """The user's results-dock reflow mode, falling back to :data:`DEFAULT_MODE` when unset, unknown, or the
    settings store is missing/broken (a bad preference must never break a mount)."""
    if settings is None:
        return DEFAULT_MODE
    try:
        val = settings.get(PREF_KEY, DEFAULT_MODE)
    except Exception:      # broad-ok: optional_probe — a broken settings store falls back to the default
        return DEFAULT_MODE
    return val if val in VALID_MODES else DEFAULT_MODE


def set_reflow_mode(settings, mode):
    """Persist the reflow ``mode``. Raises ``ValueError`` on an unknown mode so a typo fails loudly here rather
    than silently reverting to the default at read time."""
    if mode not in VALID_MODES:
        raise ValueError(f"unknown reflow mode {mode!r}; expected one of {VALID_MODES}")
    settings.set(PREF_KEY, mode)


def plan_results_mount(*, mode, has_results_widget, has_method_panel=True, already_reflowed=False) -> str:
    """Pure decision for how to mount a results dock: ``'tabify'``, ``'collapse'``, or ``'stack'``.
    Backward-compatible (mode ``'stack'`` → ``'stack'``, exactly today's behaviour); safe (no widget to mount,
    or no method panel to reflow against → ``'stack'``); idempotent (an already-reflowed area → ``'stack'`` so
    a second results dock does not re-reflow an area that is already tabbed or collapsed)."""
    if not has_results_widget:
        return 'stack'
    if mode == 'stack':
        return 'stack'
    if not has_method_panel:
        return 'stack'          # nothing to tab onto / collapse beside — a plain mount is the correct outcome
    if already_reflowed:
        return 'stack'
    return mode                 # 'tabify' or 'collapse'


def _apply_collapse(window, dock):
    """Grow the results ``dock`` so the taller method panel collapses to give it room, using
    ``QMainWindow.resizeDocks`` — a **native Qt primitive**, not hand-rolled widget reparenting, so the method
    widget's state is untouched and the change is reversible by dragging the splitter. Guarded and
    headless-safe: no ``_qt_window`` (or any Qt hiccup) → a clean no-op that just leaves today's stacking.
    Never raises."""
    qt = getattr(window, '_qt_window', None)
    if qt is None or dock is None or not hasattr(qt, 'resizeDocks'):
        return
    try:
        from qtpy.QtCore import Qt as _Qt
        # A very large target height maxes the results dock; Qt shrinks the siblings (the tall method panel)
        # to their minimum. This gives the results the freed vertical space without closing the method panel.
        qt.resizeDocks([dock], [1_000_000], _Qt.Vertical)
    except Exception:      # broad-ok: ui_cleanup — collapse is cosmetic; a failure just leaves the stacking
        pass


def add_results_dock(window, widget, *, name, settings=None, area='right', already_reflowed=False):
    """Mount ``widget`` as a results dock that actually gets room. Tabifies it with the existing right-hand
    docks (so it takes the full panel height, the parameters one tab-click away) unless the user opted out to
    stacking. **Headless-safe**: returns ``None`` when ``window`` is falsy. **Never loses the dock**: if the
    tabified mount raises for any reason (an older napari without the ``tabify`` kwarg, or a Qt hiccup), it
    falls back to a plain stacked mount so the results always appear.

    ``window`` is napari's ``viewer.window`` (duck-typed — only ``add_dock_widget`` is used). ``settings``
    defaults to the process-wide user settings; pass an explicit store in tests. Returns the created dock."""
    if not window:
        return None
    if settings is None:
        try:
            from pycat.utils.user_settings import settings as _settings
            settings = _settings()
        except Exception:      # broad-ok: optional_probe — no settings available → default (tabify) mode
            settings = None

    action = plan_results_mount(mode=reflow_mode(settings), has_results_widget=widget is not None,
                                already_reflowed=already_reflowed)
    if action == 'tabify':
        try:
            return window.add_dock_widget(widget, name=name, area=area, tabify=True)
        except Exception:      # broad-ok: ui_cleanup — reflow is cosmetic; fall back so results never vanish
            pass
    dock = window.add_dock_widget(widget, name=name, area=area)     # 'collapse' and 'stack' both mount stacked
    if action == 'collapse':
        _apply_collapse(window, dock)                               # then grow the results dock to shrink method
    return dock
