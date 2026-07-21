"""**Beginner vs advanced — one shared answer, owned by nobody.**

PyCAT wants to greet a new user with a guided surface and give an expert the full menu-first workbench, and
to let either switch at will. That needs a single mode any UI can consult without one widget owning it. This
is that mode: a thin, general accessor over the persisted user-settings (`app.mode`) — **first run is
BEGINNER**, every later run is the user's last choice, and a change notifies subscribers so open UIs
re-render live.

It carries no feature knowledge and mounts no widget; it only answers "which mode are we in" and "tell me
when it changes." The store is injectable so it is testable without touching the real config file.
"""
from __future__ import annotations

import enum

from pycat.utils.user_settings import settings

_KEY = 'app.mode'


class AppMode(str, enum.Enum):
    BEGINNER = 'beginner'      # guided surface; only beginner-visible capabilities shown
    ADVANCED = 'advanced'      # full menu-first workbench; everything visible

    @property
    def rank(self) -> int:
        return 0 if self is AppMode.BEGINNER else 1


def _store(store):
    return store if store is not None else settings()


def current_mode(store=None) -> AppMode:
    """The active mode — the user's stored choice, or **BEGINNER on first run**. An unrecognized stored
    value degrades to BEGINNER rather than raising."""
    value = _store(store).get_str(_KEY, AppMode.BEGINNER.value)
    try:
        return AppMode(value)
    except ValueError:
        return AppMode.BEGINNER


def set_mode(mode, store=None) -> AppMode:
    """Persist the mode (accepts an `AppMode` or its value). Subscribers fire on an actual change."""
    m = AppMode(mode)
    _store(store).set(_KEY, m.value)
    return m


def is_beginner(store=None) -> bool:
    return current_mode(store) is AppMode.BEGINNER


def is_advanced(store=None) -> bool:
    return current_mode(store) is AppMode.ADVANCED


def toggle_mode(store=None) -> AppMode:
    """Flip BEGINNER↔ADVANCED and persist; returns the new mode."""
    return set_mode(AppMode.ADVANCED if is_beginner(store) else AppMode.BEGINNER, store)


def on_mode_change(callback, store=None):
    """Call ``callback(AppMode)`` whenever the mode changes. Returns an unsubscribe callable."""
    def _wrap(value):
        try:
            callback(AppMode(value))
        except ValueError:
            callback(AppMode.BEGINNER)
    return _store(store).subscribe(_KEY, _wrap)
