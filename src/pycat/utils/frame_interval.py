"""
**The frame interval is a physical claim, and 51 functions default it to 1.0 second.**

The problem, and it is the pixel-size problem one axis over
-----------------------------------------------------------
``frame_interval_s=1.0`` is not an absence of information. **It is a statement that the microscope
acquired one frame per second** — and it is silently wrong on almost every real acquisition.

This has already cost real time. VPT's viscosity read **~0.094 Pa·s against an expected ~7**, and
one of the two root causes was exactly this: *the frame interval defaulted to 0.1 s when the real
MicroManager metadata said 0.5 s/frame.* **A 5× error in the time axis is a 5× error in every
diffusion coefficient**, and nothing about the output looks wrong.

``metadata_extract`` already captures the true interval at load
(``_extract_frame_interval_s`` → ``data_repository['file_metadata']['common']['frame_interval_s']``),
and **VPT already reads it.** Three UIs do. **Seven do not** — they take a spinbox default and
report the answer as physics.

What this module does
---------------------
It lifts VPT's pattern out, and adds the part VPT does not have: **it says so when it is
guessing.**

The rule VPT gets right, and which is preserved here: **a value the user set is never
overridden.** A metadata sync that stomps a deliberate choice is worse than no sync at all — the
user changed it *because* they knew something the file did not.
"""

from __future__ import annotations

from pycat.utils.general_utils import debug_log


_WARNED = set()


def _warn_once(key, message):
    if key in _WARNED:
        return
    _WARNED.add(key)
    try:
        from napari.utils.notifications import show_warning
        show_warning(message)
    except Exception as exc:
        debug_log('frame_interval: could not show the warning', exc)
        print(f"[PyCAT] {message}")


def frame_interval_s(data_repository, context=''):
    """The frame interval from the file's metadata, or **NaN** if it is not there.

    **NaN, not 1.0.** A one-second interval is a *claim*, and a failure to read the metadata is not
    evidence for it. *A NaN diffusion coefficient is visibly wrong; a 5× overestimate is not.*
    """
    where = f" ({context})" if context else ""

    if not isinstance(data_repository, dict):
        _warn_once(f'nodict{where}', (
            f"Frame interval unknown{where}: there is no metadata to read it from. "
            f"Time-dependent results are returned as NaN."))
        return float('nan')

    metadata = data_repository.get('file_metadata') or {}
    common = metadata.get('common') or {}
    raw = common.get('frame_interval_s')

    if raw is None:
        _warn_once(f'missing{where}', (
            f"Frame interval unknown{where} — this file's metadata does not carry one.\n\n"
            f"**Every time-dependent result depends on it.** A diffusion coefficient, an MSD "
            f"exponent, a recovery half-time and a coarsening rate all scale with it directly: "
            f"if the true interval is 0.5 s and the assumed one is 1.0 s, **every one of them is "
            f"out by a factor of two.**\n\n"
            f"Set it in the panel, and it will be used."))
        return float('nan')

    try:
        value = float(raw)
    except (TypeError, ValueError):
        _warn_once(f'unparseable{where}', (
            f"Frame interval unknown{where}: the metadata holds {raw!r}, which is not a number."))
        return float('nan')

    if not (value > 0) or value != value:
        _warn_once(f'nonpositive{where}', (
            f"Frame interval unknown{where}: the metadata holds {value}, which is not a positive "
            f"number of seconds."))
        return float('nan')

    return value


def sync_spinbox_from_metadata(spinbox, data_repository, *, context='',
                               touched_flag=None, owner=None):
    """**Fill a frame-interval spinbox from the file, and NEVER stomp the user's choice.**

    This is VPT's pattern, lifted out — with the part it was missing: it **says so** when there is
    nothing to sync from, instead of an ``except: pass``.

    ``touched_flag`` / ``owner`` : the attribute on ``owner`` that records *"the user edited this
    field"*. **A metadata sync that overrides a deliberate choice is worse than no sync**, because
    the user changed it *because* they knew something the file did not.

    Returns True if the spinbox was set from the file.
    """
    if owner is not None and touched_flag and getattr(owner, touched_flag, False):
        return False        # the user set it. Their value wins, always.

    value = frame_interval_s(data_repository, context=context)

    if value != value:      # NaN — the warning has already been shown
        return False

    try:
        spinbox.blockSignals(True)      # set it WITHOUT flipping the user-touched flag
        spinbox.setValue(float(value))
        spinbox.blockSignals(False)
        return True
    except Exception as exc:
        debug_log('frame_interval: could not set the spinbox', exc)
        return False
