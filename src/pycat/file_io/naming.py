"""Pure filename / pixel-size / lazy-label helpers, moved out of file_io.py (decomposition, 1.6.146).

No Qt, no viewer, no heavy science import at module scope — extracting them makes this logic headlessly
testable, which is the payoff of the move. ``file_io.py`` RE-EXPORTS them, so every existing call site
and importer keeps working.

(``derive_layer_name`` and ``_clean_filename_token`` deliberately STAYED in file_io.py: two tests
AST-parse that file *by path* and pull them out by name, so moving them would fail tests the
decomposition is not allowed to edit.)
"""
from __future__ import annotations

from pycat.file_io.lazy_sources import (  # for _lazy_backing_label's isinstance checks
    _TiffPageStack, _TiffPageStackZYX, _TiffPageStackTZYX)


def _lazy_contrast_limits(lazy_layer, prefetched=None):
    """Compute (lo, hi) contrast limits from the FIRST plane of a lazy layer.

    Passing explicit contrast_limits to viewer.add_image stops napari from
    auto-estimating them by calling np.asarray() on the whole lazy array, which
    would trigger __array__ and load every frame from disk — the real cause of
    multi-second stalls on USB-HDD IMS stacks (e.g. when adding an ROI layer
    forces a layer-list/thumbnail refresh). ``prefetched`` lets callers reuse a
    first plane they already read. Returns (lo, hi) or None if unavailable.
    """
    try:
        import numpy as _np
        plane = prefetched if prefetched is not None else lazy_layer[0]
        plane = _np.asarray(plane)
        lo, hi = float(plane.min()), float(plane.max())
        return (lo, hi) if hi > lo else None
    except Exception:
        return None


def _tiff_pixel_size_um(file_path):
    """Read physical pixel size (µm/px) from baseline TIFF resolution tags.

    The structured reader's physical_pixel_sizes only reads OME-XML and ImageJ metadata; it
    does not fall back to the standard TIFF XResolution/YResolution/ResolutionUnit
    tags. Many microscope-exported TIFFs (and channel-split exports) store pixel
    size ONLY in those baseline tags, so the reader reports None and PyCAT wrongly
    falls back to 1.0 µm/px. This helper reads the tags directly.

    XResolution/YResolution are RATIONAL (numerator, denominator) = pixels per
    ResolutionUnit. ResolutionUnit: 2 = inch, 3 = centimeter (1 = none/unitless).

    Returns µm/px as a float, or None if no usable resolution metadata is present.
    """
    try:
        import tifffile
    except Exception:
        return None
    try:
        with tifffile.TiffFile(file_path) as t:
            page = t.pages[0]
            xres_tag = page.tags.get('XResolution')
            unit_tag = page.tags.get('ResolutionUnit')
            if xres_tag is None or xres_tag.value is None:
                return None
            val = xres_tag.value
            # Rational (num, den) -> pixels per unit
            if isinstance(val, (tuple, list)) and len(val) == 2 and val[1] != 0:
                pixels_per_unit = float(val[0]) / float(val[1])
            else:
                pixels_per_unit = float(val)
            if pixels_per_unit <= 0:
                return None
            # ResolutionUnit: 3 = cm, 2 = inch. Default to inch if absent (TIFF spec default).
            # NOTE: tifffile returns an enum; RESUNIT.NONE (value 1) is falsy, so test
            # `is not None` explicitly rather than truthiness (which would misread NONE).
            if unit_tag is not None and unit_tag.value is not None:
                unit = int(unit_tag.value)
            else:
                unit = 2
            if unit == 3:      # centimeters
                microns_per_unit = 10000.0        # 1 cm = 10 000 µm
            elif unit == 2:    # inches
                microns_per_unit = 25400.0        # 1 inch = 25 400 µm
            else:              # unit == 1 (none): tags are unitless, not a physical size
                return None
            microns_per_pixel = microns_per_unit / pixels_per_unit
            # Guard against absurd values (a bad tag shouldn't set a nonsense scale).
            if not (1e-4 < microns_per_pixel < 1e4):
                return None
            return microns_per_pixel
    except Exception:
        return None


def _ome_pixel_size_um(file_path):
    """Read physical pixel size (µm/px) from OME-XML PhysicalSizeX.

    For an OME-TIFF the OME-XML is the AUTHORITATIVE pixel-size source — the
    baseline TIFF XResolution/YResolution tags are often zeroed on OME exports
    (which makes the reader's own physical_pixel_sizes raise "division by zero"),
    while the OME-XML carries the real value. This reads it directly.

    Returns µm/px as a float, or None if not an OME file / no usable value.
    """
    try:
        import tifffile
        import re as _re
    except Exception:
        return None
    try:
        with tifffile.TiffFile(file_path) as t:
            ome = getattr(t, 'ome_metadata', None)
            if not ome:
                return None
            m = _re.search(r'PhysicalSizeX="([^"]+)"', ome)
            if not m:
                return None
            val = float(m.group(1))
            if val <= 0:
                return None
            # OME PhysicalSizeXUnit defaults to µm; honour an explicit unit if given.
            um = val
            um_match = _re.search(r'PhysicalSizeXUnit="([^"]+)"', ome)
            unit = (um_match.group(1).strip().lower() if um_match else '')
            if unit in ('nm', 'nanometer', 'nanometre'):
                um = val / 1000.0
            elif unit in ('mm', 'millimeter', 'millimetre'):
                um = val * 1000.0
            elif unit in ('cm', 'centimeter', 'centimetre'):
                um = val * 10000.0
            # µm (default) or 'µm'/'um'/'micron' → as-is
            if not (1e-4 < um < 1e4):
                return None
            return um
    except Exception:
        return None


def _lazy_backing_label(wrapper):
    """What is ACTUALLY behind this lazy layer, for the load message.

    Read off the wrapper rather than hardcoded per branch, because a hardcoded label is how these
    messages came to announce "(zarr-backed)" for months after the zarr transcode was deleted
    (cleanup item 3). A routing change now cannot leave the message lying: the label follows the
    object that was built.
    """
    if isinstance(wrapper, (_TiffPageStack, _TiffPageStackZYX, _TiffPageStackTZYX)):
        return "lazy, native TIFF pages"
    return "lazy, dask-backed"
