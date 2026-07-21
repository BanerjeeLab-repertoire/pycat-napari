"""**What colour a workflow status-marker should show — the pure decision, no Qt.**

The status circle in front of each step is PyCAT's anti-black-box promise made visible: it must tell the
truth about where the user is. This module is the single decision function behind that circle, kept
Qt-free so it can be unit-tested headlessly (the Qt rendering lives in `ui/field_status.py`).

The one rule the tester's bug report turned on: **GREEN MEANS DONE — the step has actually run.** A step
that is merely *ready* to run (its inputs are satisfied) must NOT look the same as one that has run, or
the marker lies. So readiness gets its own distinct, non-green appearance: an OUTLINED (hollow) amber
ring, never a solid dot.

The colour convention, stated once so it is applied uniformly:

    RED     (filled)   required step, not yet run
    YELLOW  (filled)   optional step resting at its default
    READY   (outline)  inputs satisfied, NOT yet run — distinct from done, never solid green
    GREEN   (filled)   DONE — a required step has run
    BLUE    (filled)   DONE — an *optional* step has run (blue = "you did this optional thing")

Blue is deliberate and carries real information (an optional step you chose to run), so it is kept — but
its meaning is made explicit in the tooltip rather than left to guesswork.
"""
from __future__ import annotations


def resolve_marker(*, done: bool, optional: bool, ready: bool):
    """Return ``(colour_key, filled, tooltip)`` for a step marker.

    Precedence is **done → ready → resting**: a completed step is green/blue regardless of readiness; a
    not-yet-run step whose inputs are satisfied is the outlined READY look; otherwise it rests at
    red (required) or yellow (optional).

    ``filled`` is False ONLY for the READY state, so readiness renders as a hollow ring that can never be
    misread as the solid-green "done".
    """
    if done:
        if optional:
            return ('blue', True,
                    'Done — you ran this optional step. (Blue = an optional step you completed.)')
        return ('green', True, 'Done — this step has been run.')
    if ready:
        return ('ready', False, 'Ready to run — your inputs are set, but you have not run this step yet.')
    if optional:
        return ('yellow', True, 'Optional — a sensible default is in place. You can change it.')
    return ('red', True, 'Required — run this step to continue.')
