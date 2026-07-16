"""Pure metadata-read + reader-selection head for the generic stack loader.

Extracted VERBATIM from ``FileIOClass._open_stack_generic`` (god-class decomposition #5a, see
docs/audits/fileio_godclass_roadmap_2026-07-15.md). No napari, no Qt, no ``self``.

``read_stack_structure`` answers the one question the head asked: *did the structured reader
(bioio/AICSImage) parse this file's dimensions/scenes/pixel size, or must we fall back to reading
raw tifffile pages?* It returns a ``StackStructure`` with the reader handle + scenes + pixel size on
the structured path, or a lazy tifffile-page wrapper (or eager array) + frame count on the fallback
path. The controller keeps the two things that are NOT pure and stayed exactly where they were:

* the Qt **scene-selection dialog** (run on ``scenes`` when there is more than one), and
* the **data-repository side effects** — ``update_metadata(image)`` and the ``file_metadata`` export.

Relocating those two out of the fallback-triggering ``try`` is behaviour-preserving: ``update_metadata``
catches all its own exceptions (never propagates), and ``show_position_selection_dialog`` returns a
selection rather than raising — so neither ever drove the tifffile fallback in practice.

``_TiffPageStack`` and ``_tiff_pixel_size_um`` live in ``file_io.py`` (heavily used there and imported
by tests), so they are **injected** rather than imported here — that keeps this module free of an
import cycle back into its former host.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from pycat.file_io.image_reader import open_image as _default_open_image
from pycat.utils.general_utils import debug_log


@dataclass
class StackStructure:
    """What the metadata head produced, for the controller to build layers from.

    On the **structured** path: ``reader_has_structure=True``, ``image`` is the reader handle, and
    ``scenes`` / ``microns_per_pixel`` are populated; per-scene dims (n_t/n_c/n_z/H/W) are read from
    ``image.dims`` in the controller's loop, exactly as before.

    On the **fallback** path: ``reader_has_structure=False``, ``fallback_array`` is a lazy
    ``_TiffPageStack`` (or an eager ndarray last resort) shaped (T, H, W), with ``n_frames``/``H``/``W``
    describing it. (The controller nulls H/W/n_t/n_z right after the head just as the original did —
    they are re-derived per scene — so those fields are informational.)
    """
    reader_has_structure: bool
    image: Any = None
    scenes: list = field(default_factory=list)
    microns_per_pixel: float = 1.0
    fallback_array: Any = None
    n_frames: int = 0
    H: Optional[int] = None
    W: Optional[int] = None


def read_stack_structure(file_path, ext, *, tiff_page_stack_cls, tiff_pixel_size_um,
                         open_image=_default_open_image) -> StackStructure:
    """Open ``file_path`` for dims/scenes/pixel size, or fall back to a lazy tifffile-page read.

    Parameters
    ----------
    file_path, ext : str
        The path and its lowercased extension (``ext`` is accepted for parity with the loader seam
        and future format-specific routing; the current logic keys off the reader, not the suffix).
    tiff_page_stack_cls : type
        ``file_io._TiffPageStack`` — injected to avoid an import cycle. Built as
        ``cls(path, n_pages, H, W, dtype, channel_idx=0, n_channels=1)`` on the fallback path.
    tiff_pixel_size_um : callable
        ``file_io._tiff_pixel_size_um`` — reads µm/px from baseline TIFF resolution tags.
    open_image : callable
        The reader seam (defaults to ``image_reader.open_image``); overridable for tests.
    """
    microns_per_pixel = 1.0

    # ── Read metadata ────────────────────────────────────────────────
    try:
        # `open_image` is the seam: it routes to aicsimageio or bioio, and raises
        # ImageReaderUnavailable with the exact `pip install` line when neither is present.
        image = open_image(file_path)
        scenes = list(getattr(image, 'scenes', []) or [])

        try:
            px = image.physical_pixel_sizes
            microns_per_pixel = float(px.Y) if px.Y else 1.0
        except Exception as _e:
            debug_log("file_io: reading physical pixel size (falling back to "
                      "1.0 µm/px — micron measurements may be wrong)", _e)

        # Fallback: the reader's physical_pixel_sizes only reads OME-XML and ImageJ metadata, not
        # the baseline TIFF resolution tags. If it came back empty (== 1.0), read them directly.
        if abs(microns_per_pixel - 1.0) < 1e-9:
            _tag_px = tiff_pixel_size_um(file_path)
            if _tag_px is not None:
                microns_per_pixel = _tag_px
                debug_log(f"file_io: pixel size {_tag_px:.6f} µm/px recovered "
                          "from TIFF resolution tags (the reader missed it)")

        return StackStructure(reader_has_structure=True, image=image, scenes=scenes,
                              microns_per_pixel=microns_per_pixel)

    except Exception as _e:
        debug_log("file_io: the structured reader failed, falling back to direct "
                  "tifffile read (scene/T/Z metadata unavailable)", _e)
        # ── A METADATA defect must not trigger a full EAGER read ────────────
        #
        # The ``except`` above catches everything — including a failure to parse something optional
        # (a channel name, a pixel size, a scene entry). Any of those used to drop PyCAT into
        # ``tifffile.imread(file_path)``, which reads the whole file into memory. A cosmetic metadata
        # problem should not cost a gigabyte: ``_TiffPageStack`` does per-page seeks (the same lazy
        # contract as the primary path). Only if it cannot be built either is the eager read reached.
        import tifffile
        arr = None
        try:
            with tifffile.TiffFile(file_path) as _probe:
                _pages = _probe.pages
                _n_pages = len(_pages)
                if _n_pages > 0:
                    _first = _pages[0]
                    _H, _W = int(_first.shape[-2]), int(_first.shape[-1])
                    arr = tiff_page_stack_cls(file_path, _n_pages, _H, _W, _first.dtype,
                                              channel_idx=0, n_channels=1)
                    debug_log("file_io: BioIO metadata unavailable — reading pages LAZILY "
                              "via tifffile, not a full eager read", _e)
        except Exception as _lazy_exc:
            debug_log("file_io: the lazy TIFF page reader failed too", _lazy_exc)
            arr = None

        if arr is None:
            # Genuinely unreadable lazily — a full read is the honest last resort, reached only after
            # the lazy path has actually been tried and failed.
            debug_log("file_io: falling back to a FULL eager read of %s" % file_path)
            arr = tifffile.imread(file_path)
        while arr.ndim > 3 and arr.shape[0] == 1:
            arr = arr[0]
        if arr.ndim == 2:
            arr = arr[np.newaxis]
        n_frames = arr.shape[0]
        H, W = arr.shape[1], arr.shape[2]
        # Recover pixel size from baseline resolution tags in this branch too.
        _tag_px = tiff_pixel_size_um(file_path)
        if _tag_px is not None:
            microns_per_pixel = _tag_px
            debug_log(f"file_io: pixel size {_tag_px:.6f} µm/px recovered "
                      "from TIFF resolution tags (direct tifffile branch)")

        return StackStructure(reader_has_structure=False, image=None, scenes=[],
                              microns_per_pixel=microns_per_pixel, fallback_array=arr,
                              n_frames=n_frames, H=H, W=W)
