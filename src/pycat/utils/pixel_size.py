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


# ── Is this pixel size PHYSICALLY POSSIBLE? ──────────────────────────────────────────────
#
# **A file can carry a scale that is a lie**, and PyCAT was believing it.
#
# Gable exported a substack from ImageJ. The resulting TIFF carries::
#
#     XResolution    = 2147054150 / 4999   ->  429,496.7 pixels per unit
#     ResolutionUnit = 1                   ->  "no absolute unit"
#     ImageJ unit    = micron
#
# That works out to **2.3 picometres per pixel** — *four hundred times smaller than a hydrogen
# atom.* And ``2147054150`` is a hair under **2³¹ = 2147483648**: a **signed-integer overflow** in
# ImageJ's Substack export.
#
# **The pixel-size gate did not fire, and it was right not to** — it asked *"is there a number?"*,
# and there was one. It was not ``None`` and not the ``1.0`` sentinel, so PyCAT concluded the file
# carried a real scale and hid the prompt.
#
# ***It was doing what it was told. The file was lying.***
#
# ── The bounds come from OPTICS, not from taste ──────────────────────────────────────────
#
# **Lower — 0.001 µm/px (1 nm).** Abbe puts the best real resolution at
# ``λ/(2·NA) = 400/(2×1.49) ≈ 134 nm``, and Nyquist wants ~2–3 samples across that, so the smallest
# *sensible* pixel is ~40–65 nm. Super-resolution reconstructions go finer — an aggressive SMLM
# render might be 5 nm/px. **1 nm is a 1000× margin below even that.**
#
# **Upper — 1000 µm/px (1 mm).** A 4× objective is ~1.6 µm/px; a slide scanner ~20 µm/px; a
# photograph of a gel might be 100 µm/px. **1 mm per pixel is not a micrograph.**
#
# ***Both bounds are deliberately loose. A bound this wide can only catch garbage, never real
# data.*** Every instrument in the lab passes — the 63×/1.4 confocal at 0.0264, the 100× bead data
# at 0.067, the spinning disk at 0.108 — and the corrupt substack fails by a factor of 400.

_PLAUSIBLE_MIN_UM_PER_PX = 1e-3      # 1 nm — below the finest SMLM render, by 1000x
_PLAUSIBLE_MAX_UM_PER_PX = 1e3       # 1 mm — above any micrograph


def is_physically_plausible(pixel_size_um_value):
    """**Could a microscope have produced this pixel size?**

    ``False`` for a scale no optical instrument can generate. See the bounds above — they are set
    from Abbe and Nyquist, and they are **deliberately loose**: they exist to catch a **corrupt
    metadata tag**, not to second-guess a real acquisition.
    """
    try:
        value = float(pixel_size_um_value)
    except (TypeError, ValueError):
        return False

    if not (value == value) or value <= 0:      # NaN or non-positive
        return False

    return _PLAUSIBLE_MIN_UM_PER_PX <= value <= _PLAUSIBLE_MAX_UM_PER_PX


def implausible_reason(pixel_size_um_value):
    """**Why** it is implausible — in the terms a microscopist thinks in. ``None`` if it is fine."""
    if is_physically_plausible(pixel_size_um_value):
        return None

    try:
        value = float(pixel_size_um_value)
    except (TypeError, ValueError):
        return "the pixel size in the file is not a number"

    if not (value == value):
        return "the file carries no pixel size"

    if value <= 0:
        return f"the file claims a pixel size of {value} µm — a pixel cannot be zero or negative"

    if value < _PLAUSIBLE_MIN_UM_PER_PX:
        nanometres = value * 1000.0
        return (f"the file claims **{nanometres:.4g} nm per pixel**. No microscope can resolve "
                f"that — the diffraction limit is ~130 nm, and even a super-resolution "
                f"reconstruction rarely goes below 5 nm. **The resolution tag in this file is "
                f"corrupt.** *(ImageJ's Substack export is a known cause: it can write an "
                f"overflowed 32-bit resolution numerator.)*")

    kilometres_per_pixel = value / 1e9
    return (f"the file claims **{value:.4g} µm per pixel**"
            + (f" ({kilometres_per_pixel:.3g} km!)" if value > 1e9 else "")
            + ". That is not a micrograph — **the resolution tag in this file is corrupt.**")


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

    # ── EXACTLY 1 is the value the loader writes when it does NOT KNOW ──────────
    #
    # ``file_io`` falls back to ``microns_per_pixel_sq = 1`` when the metadata carries no
    # resolution — and it prints *"Resolution data incomplete, using default value of 1
    # (um/px)^2"* when it does so.
    #
    # **So a value of exactly 1 is a SENTINEL, not a measurement.** This function was returning it
    # as a legitimate pixel size, with **no warning** — which is the whole failure it exists to
    # prevent: *1 µm/px is a plausible value, not an obviously-wrong one.*
    #
    # ``field_status``'s pixel-size gate already knows this — ``abs(val - 1.0) > 1e-9`` is its
    # test for a REAL scale. **The accessor did not.**
    #
    # A microscope whose pixel really IS 1.000 µm is possible, and such a user confirms it through
    # the gate, which sets ``pixel_size_confirmed``. **That flag is the one thing that
    # distinguishes "the user told us it is 1" from "nobody told us anything".**
    if (abs(value - 1.0) < 1e-9
            and isinstance(data_repository, dict)
            and not data_repository.get('pixel_size_confirmed')):
        _warn_once(
            f'sentinel{where}',
            f"Pixel size unknown{where}: `microns_per_pixel_sq` is exactly 1, which is the "
            f"**fallback the loader writes when the metadata carries no resolution** — not a "
            f"measurement. Lengths and areas are returned as NaN.\n\n"
            f"If your pixel really is 1.000 um, confirm it in the pixel-size panel and it will "
            f"be accepted.")
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


# ── Z is NOT the same number as XY, and assuming it is corrupts every volume ─────────────
#
# **Microscopy is anisotropic and confocal Z-stacks are the extreme case.** A typical acquisition::
#
#     X = 0.108 µm      Y = 0.108 µm      Z = 0.300 µm
#
# The Z step is nearly **three times** the lateral pixel — because the axial PSF is that much
# worse, and nobody oversamples a dimension they cannot resolve.
#
# ``zstack_segmentation_tools`` computes::
#
#     voxel_volume_um3 = (microns_per_pixel ** 2) * z_step_um
#
# and ``z_step_um`` was a **function default of 1.0** that **nothing ever passed**. So on the
# acquisition above, every 3-D volume PyCAT reported was out by::
#
#     assumed 1.0 / true 0.300  =  3.33x
#
# ***A 3.3× error in every condensate volume, every volume fraction, and every 3-D density —
# reported as a number that looks entirely normal.*** And it feeds the marching-cubes ``spacing=``
# and the 3-D centroid coordinates too, so the surface areas and the distances are wrong in the
# same breath.
#
# **The true value was already in the repository.** ``metadata_extract`` reads
# ``physical_pixel_sizes.Z`` from the OME metadata and stores it as ``z_step_um`` — where it was
# **displayed in the metadata panel and read by nothing.**
#
# *This is the same disease as the 1.0 µm/px sentinel: the honest number exists, nobody consults
# it, and the fallback is a plausible-looking lie.*


def z_step_um(data_repository, context=''):
    """The Z step in µm, or ``NaN`` if it is not known.

    **Do not substitute the XY pixel size.** They are different numbers on essentially every
    confocal — a 0.108 µm lateral pixel commonly pairs with a 0.300 µm Z step — and a volume
    computed from the wrong one is wrong by their ratio, silently.

    Returns ``NaN`` rather than guessing, for the same reason ``pixel_size_um`` does: a ``NaN``
    volume is visibly wrong, and a 3.3× overestimate is not.
    """
    where = f" ({context})" if context else ""

    if not isinstance(data_repository, dict):
        _warn_once(
            f'z_no_repo{where}',
            f"Z step unknown{where}: no data repository was available. 3-D volumes, surface "
            f"areas and axial distances cannot be converted to microns and are returned as NaN.")
        return float('nan')

    # Written by `metadata_extract` from the OME `physical_pixel_sizes.Z`.
    raw = ((data_repository.get('file_metadata') or {}).get('common') or {}).get('z_step_um')

    # A value set directly on the repository (by a UI field, or a batch step) wins over the file:
    # the user correcting the metadata is the whole point of being able to.
    if data_repository.get('z_step_um') is not None:
        raw = data_repository.get('z_step_um')

    if raw is None:
        _warn_once(
            f'z_missing{where}',
            f"Z step unknown{where}: the file carries no axial spacing, so 3-D volumes and "
            f"surface areas cannot be computed in microns and are returned as NaN.\n\n"
            f"This used to default SILENTLY to 1.0 µm. On a typical confocal Z step of 0.3 µm "
            f"that is a **3.3x overestimate of every 3-D volume** — reported as a number that "
            f"looks entirely normal.\n\n"
            f"Set the Z step in the metadata panel, or pass it explicitly.")
        return float('nan')

    try:
        value = float(raw)
    except (TypeError, ValueError):
        _warn_once(
            f'z_unparseable{where}',
            f"Z step unknown{where}: `z_step_um` is {raw!r}, which is not a number. 3-D "
            f"volumes are returned as NaN.")
        return float('nan')

    if not np.isfinite(value) or value <= 0:
        _warn_once(
            f'z_nonpositive{where}',
            f"Z step unknown{where}: `z_step_um` is {value}, which is not a positive number. "
            f"3-D volumes are returned as NaN.")
        return float('nan')

    # The same plausibility bounds as the lateral pixel: a Z step is a physical distance produced
    # by the same instrument, and the bounds are loose enough that any real one passes.
    if not is_physically_plausible(value):
        _warn_once(
            f'z_implausible{where}',
            f"Z step unknown{where}: the file claims {value:g} µm per slice, which no microscope "
            f"can have produced. 3-D volumes are returned as NaN. "
            f"{implausible_reason(value)}")
        return float('nan')

    return value


def z_step_um_or_default(data_repository, default=1.0, context=''):
    """The Z step in µm, falling back to ``default`` — **and saying so.**

    For the places that must produce a number. The fallback is the same one that used to happen
    silently; the difference is that the assumption is now **on the record**, so a volume computed
    with an assumed isotropic voxel is not mistaken for a measured one.
    """
    value = z_step_um(data_repository, context=context)
    if np.isfinite(value):
        return value

    where = f" ({context})" if context else ""
    _warn_once(
        f'z_defaulted{where}',
        f"Z step unknown{where} — proceeding with {default} µm per slice. **The voxel is "
        f"therefore assumed ISOTROPIC**, which it almost never is: a typical confocal pairs a "
        f"~0.1 µm lateral pixel with a ~0.3 µm Z step. Do not report these volumes or surface "
        f"areas as physical values.")
    return float(default)
