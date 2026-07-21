"""**One process-wide, corruption-safe home for cross-session user preferences.** *(General mechanism —
zero feature knowledge.)*

PyCAT has nowhere to remember a choice between sessions: no ``QSettings``, no user-config file, no
first-run flag. So the navigator's beginner/advanced mode, an acquisition pixel-size profile, the preferred
plot backend, and a dismissed QC warning all have nowhere to live. This is that home — a namespaced
key/value store persisted to ONE atomic JSON file in the OS user-config dir. It knows nothing about any of
those features; each registers its own defaults and reads/writes its own keys.

Two properties it must never violate, because it runs at startup:

* **Corruption never crashes the app.** A hand-edited or truncated settings file falls back to the
  registered defaults (the bad file is set aside, not deleted), so a broken JSON can never stop PyCAT
  opening.
* **A write is atomic.** Values are written to a temp file and ``os.replace``-d into place, so a crash or a
  full disk mid-write leaves the PREVIOUS good file intact — never a half-written one.

Keys are namespaced by convention with dots (``'navigator.mode'``, ``'plot.backend'``); they are independent
strings, so two namespaces cannot collide. Typed accessors (:meth:`get_bool` / :meth:`get_int` /
:meth:`get_float`) coerce and fall back to the default rather than raising on a malformed stored value.
"""
from __future__ import annotations

import json
import os


def _default_store_path() -> str:
    """``<user-config>/settings.json`` — ``platformdirs`` where available, else ``~/.pycat``."""
    try:
        import platformdirs
        base = platformdirs.user_config_dir('pycat')
    except Exception:      # broad-ok: no platformdirs / odd platform → a home-dir fallback, never crash
        base = os.path.join(os.path.expanduser('~'), '.pycat')
    return os.path.join(base, 'settings.json')


def _coerce_bool(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ('1', 'true', 'yes', 'on'):
            return True
        if v in ('0', 'false', 'no', 'off', ''):
            return False
    return bool(default)


class UserSettings:
    """A namespaced, persisted key/value store. Construct with an explicit ``path`` in tests; use
    :func:`settings` for the process-wide instance."""

    def __init__(self, path=None, *, autosave=True):
        self._path = str(path) if path is not None else _default_store_path()
        self._autosave = bool(autosave)
        self._defaults: dict = {}
        self._values: dict = {}
        self._subscribers: dict = {}
        self._load()

    # ── registration + reads ─────────────────────────────────────────────────
    def register_default(self, key, value):
        """Declare the value a key takes when nothing is stored. Idempotent; a stored value always wins."""
        self._defaults[str(key)] = value

    def get(self, key, default=None):
        """The stored value, else the registered default, else ``default``."""
        key = str(key)
        if key in self._values:
            return self._values[key]
        if key in self._defaults:
            return self._defaults[key]
        return default

    def get_bool(self, key, default=False) -> bool:
        return _coerce_bool(self.get(key, default), default)

    def get_int(self, key, default=0) -> int:
        try:
            return int(self.get(key, default))
        except (TypeError, ValueError):
            return int(default)

    def get_float(self, key, default=0.0) -> float:
        try:
            return float(self.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def get_str(self, key, default='') -> str:
        v = self.get(key, default)
        return default if v is None else str(v)

    def has(self, key) -> bool:
        """True if a value is STORED for ``key`` (a registered default alone is not 'stored')."""
        return str(key) in self._values

    def keys(self):
        """Every key with a stored value or a registered default."""
        return sorted(set(self._values) | set(self._defaults))

    # ── writes + subscriptions ───────────────────────────────────────────────
    def set(self, key, value):
        """Store ``value`` under ``key`` (persisted immediately when ``autosave``). Subscribers for that key
        fire only when the effective value actually CHANGES. If the atomic save fails (e.g. an
        unserializable value), the in-memory change is rolled back so the instance stays consistent with the
        file on disk, and the error propagates."""
        key = str(key)
        old = self.get(key)
        had = key in self._values
        prev = self._values.get(key)
        self._values[key] = value
        if self._autosave:
            try:
                self._save()
            except Exception:
                if had:
                    self._values[key] = prev
                else:
                    self._values.pop(key, None)
                raise
        if value != old:
            for cb in list(self._subscribers.get(key, ())):
                try:
                    cb(value)
                except Exception:      # broad-ok: a bad subscriber must not corrupt the store or block others
                    pass

    def reset(self, key):
        """Drop a stored value so the key falls back to its registered default. Fires subscribers on change."""
        key = str(key)
        if key in self._values:
            old = self._values.pop(key)
            if self._autosave:
                self._save()
            new = self.get(key)
            if new != old:
                for cb in list(self._subscribers.get(key, ())):
                    try:
                        cb(new)
                    except Exception:      # broad-ok: a bad subscriber must not block reset
                        pass

    def subscribe(self, key, callback):
        """Call ``callback(new_value)`` whenever ``key``'s effective value changes. Returns an unsubscribe
        callable."""
        key = str(key)
        self._subscribers.setdefault(key, []).append(callback)

        def _unsubscribe():
            try:
                self._subscribers.get(key, []).remove(callback)
            except ValueError:
                pass
        return _unsubscribe

    # ── persistence ──────────────────────────────────────────────────────────
    def _load(self):
        try:
            if os.path.isfile(self._path):
                with open(self._path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError('settings file is not a JSON object')
                self._values = data
        except Exception:      # broad-ok: a corrupt/unreadable settings file must NEVER crash startup
            self._values = {}
            self._quarantine_corrupt()

    def _quarantine_corrupt(self):
        """Move a corrupt settings file aside (``.corrupt``) rather than deleting it — the user's hand-edits
        may be recoverable, and silently losing them is its own failure."""
        try:
            if os.path.isfile(self._path):
                os.replace(self._path, self._path + '.corrupt')
        except Exception:      # broad-ok: best-effort quarantine; failing it still leaves us on defaults
            pass

    def _save(self):
        """Atomic write: full temp file, then ``os.replace`` into place. A failure mid-write leaves the
        previous good file untouched (the temp is discarded)."""
        d = os.path.dirname(self._path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = self._path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self._values, f, indent=1, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path)


_INSTANCE = None


def settings() -> UserSettings:
    """The process-wide `UserSettings` (created on first use, at the default store path)."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = UserSettings()
    return _INSTANCE
