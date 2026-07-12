"""
UI-notification shim — lets scientific modules report to the user *when a UI exists*,
without being importable only when a UI exists.

The problem
-----------
Several analysis modules imported napari at module scope purely to emit user-facing
notifications::

    from napari.utils.notifications import show_info, show_warning   # top of file

That single line makes the whole module — including its physics — un-importable
without a GUI stack. The consequences are not cosmetic:

* **The science cannot be tested headlessly.** A viscosity calculation, an MSD fit, or
  a colocalization coefficient should be verifiable in a plain ``pytest`` run on CI,
  with no display, no Qt, and no napari. Coupling them to a GUI import makes
  automated scientific validation impossible, which is exactly backwards: the physics
  is the part that most needs regression testing.
* **It blocks reuse.** The same functions cannot be called from a script, a notebook,
  or a batch pipeline without dragging in a windowing toolkit.

The fix
-------
Import from here instead::

    from pycat.utils.notify import show_info, show_warning

When napari is present these forward to it unchanged, so behaviour in the application
is identical. When it is absent (a test run, a notebook, a headless batch job) they
degrade to printing, and the module imports and runs.

Note that ``print`` is a deliberate fallback rather than a silent no-op: a warning a
scientist should see must not vanish just because the code is running in a script.
"""

from __future__ import annotations

import sys


def _has_napari() -> bool:
    """True if napari is importable AND a viewer stack is plausibly usable.

    Deliberately cheap and tolerant: any failure means "no UI", which is the safe
    assumption (we fall back to printing, which always works).
    """
    try:
        import napari  # noqa: F401
        return True
    except Exception:
        return False


_HAS_NAPARI = _has_napari()

if _HAS_NAPARI:
    try:
        from napari.utils.notifications import (
            show_info as _ni,
            show_warning as _nw,
        )
    except Exception:                     # napari present but notifications aren't
        _ni = _nw = None
else:
    _ni = _nw = None


def show_info(message):
    """Report an informational message to the user.

    Forwards to napari when a UI is present; prints otherwise so the message is not
    lost in a headless run.
    """
    if _ni is not None:
        try:
            _ni(message)
            return
        except Exception:
            pass
    print(f"[PyCAT] {message}")


def show_warning(message):
    """Report a warning to the user.

    Forwards to napari when a UI is present; prints to stderr otherwise. A warning
    that a scientist needs to see must not disappear because the code is running
    outside the GUI.
    """
    if _nw is not None:
        try:
            _nw(message)
            return
        except Exception:
            pass
    print(f"[PyCAT WARNING] {message}", file=sys.stderr)


def ui_available() -> bool:
    """True when a napari UI is available. Use this to guard code that genuinely
    needs a viewer (adding layers, opening dialogs) rather than merely reporting."""
    return _HAS_NAPARI
