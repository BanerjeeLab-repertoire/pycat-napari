"""
The pixel size, in one place, that says when it does not know.

The problem
-----------
``_mpx()`` was defined **ten times** across the codebase, in two forms, and **both silently
default to 1.0 µm/px**:

* eight copies (the UI widgets) via ``float(self._dr().get('microns_per_pixel_sq', 1.0)) ** 0.5``
* two copies (``intensity_profile_tools``, ``morphological_complexity_tools``) via
  ``except Exception: return 1.0``

The caller cannot distinguish *"the pixel size is 1.0 µm"* from *"the lookup failed"* — and 1.0
is a perfectly plausible pixel size, so nothing looks wrong. **It is not a harmless default.**
Every length and every area in the output is scaled by it:

=========================  =================  ====================  ==========
true µm/px                 true area (µm²)    with fallback 1.0     error
=========================  =================  ====================  ==========
0.0264 (Zeiss 63× oil)     0.348              500.0                 **1435×**
0.1 (typical 100×)         5.000              500.0                 100×
0.67 (the bead videos)     224.45             500.0                 2×
=========================  =================  ====================  ==========

On a Zeiss 63× that is a **1435× overestimate of every area**, and a 38× overestimate of every
length — reported as a number that looks entirely normal.

This is the same failure as ``estimate_psf_sigma`` returning 1.0 on any exception (1.5.437),
duplicated ten times over.

What this does instead
----------------------
``pixel_size_um`` returns ``NaN`` when the pixel size is genuinely unknown, and warns once. A
``NaN`` propagates: an area computed from it is ``NaN``, which is **visible**, rather than a
number that is wrong by three orders of magnitude and looks fine.

``pixel_size_um_or_default`` is available for the places that genuinely must proceed — but it
**warns**, so the assumption is on the record rather than silent.
"""

from __future__ import annotations

import numpy as np

from pycat.utils.notify import show_warning as napari_show_warning


# One warning per session per reason: a per-object warning would drown the log.
_WARNED = set()


def _warn_once(key, message):
    if key not in _WARNED:
        _WARNED.add(key)
        napari_show_warning(message)


def pixel_size_um(data_repository, context=''):
    """The pixel size in µm, or ``NaN`` if it is not known.

    Parameters
    ----------
    data_repository : the dict holding ``microns_per_pixel_sq`` (the SQUARED pixel size, i.e.
        µm² per pixel — the square root is taken here).
    context : optional string naming the caller, so the warning says what is affected.

    Returns
    -------
    float
        The pixel size in µm, or ``NaN``. **Callers must handle NaN** — that is the point. A
        ``NaN`` area is visibly wrong; a 1435× overestimate is not.
    """
    where = f" ({context})" if context else ""

    if not isinstance(data_repository, dict):
        _warn_once(
            f'no_repo{where}',
            f"Pixel size unknown{where}: no data repository was available. Lengths and areas "
            f"cannot be converted to microns and are returned as NaN. Set the pixel size on "
            f"load, or supply it explicitly.")
        return float('nan')

    raw = data_repository.get('microns_per_pixel_sq')

    if raw is None:
        _warn_once(
            f'missing{where}',
            f"Pixel size unknown{where}: `microns_per_pixel_sq` is not set, so lengths and "
            f"areas cannot be converted to microns and are returned as NaN.\n\n"
            f"This used to default SILENTLY to 1.0 µm/px. On a Zeiss 63× (0.0264 µm/px) that "
            f"is a **1435× overestimate of every area** — reported as a number that looks "
            f"entirely normal. NaN is the honest answer.\n\n"
            f"Set the pixel size when the image is loaded, or pass it explicitly.")
        return float('nan')

    try:
        value = float(raw)
    except (TypeError, ValueError):
        _warn_once(
            f'unparseable{where}',
            f"Pixel size unknown{where}: `microns_per_pixel_sq` is {raw!r}, which is not a "
            f"number. Lengths and areas are returned as NaN.")
        return float('nan')

    if not np.isfinite(value) or value <= 0:
        _warn_once(
            f'nonpositive{where}',
            f"Pixel size unknown{where}: `microns_per_pixel_sq` is {value}, which is not a "
            f"positive number. Lengths and areas are returned as NaN.")
        return float('nan')

    return float(value ** 0.5)


def pixel_size_um_or_default(data_repository, default=1.0, context=''):
    """The pixel size in µm, falling back to ``default`` — **and saying so.**

    For the places that genuinely must produce a number rather than a ``NaN``. The fallback is
    the same one that used to happen silently; the difference is that it is now **on the
    record**, so a result computed in pixel units is not mistaken for one in microns.
    """
    value = pixel_size_um(data_repository, context=context)
    if np.isfinite(value):
        return value

    where = f" ({context})" if context else ""
    _warn_once(
        f'defaulted{where}',
        f"Pixel size unknown{where} — proceeding with {default} µm/px. **The output is "
        f"therefore in PIXEL units, not microns**, unless the true pixel size happens to be "
        f"{default}. Do not report these lengths or areas as physical values.")
    return float(default)
