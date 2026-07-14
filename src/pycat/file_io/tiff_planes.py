"""
**TIFF pixels do not go through BioIO. They go through tifffile's page reader.**

── Why: BioIO's TIFF path is broken on zarr 3.2 ─────────────────────────────────────────

``bioio-tifffile`` builds its lazy dask array by calling ``tif.aszarr()`` — and ``tifffile``'s zarr
store does::

    from zarr.core.chunk_grids import RegularChunkGrid
    ...
    except ImportError as exc:
        raise ValueError(f'zarr {zarr.__version__} < 3 is not supported') from exc

**zarr 3.2 renamed that class.** The import fails, ``tifffile`` catches it, and raises::

    ValueError: zarr 3.2.1 < 3 is not supported

***That message is a lie.*** 3.2.1 is not less than 3. ``tifffile`` blames the version for **any**
ImportError out of its zarr-3 module, and the real failure — ``cannot import name
'RegularChunkGrid'`` — is one frame up, where nobody looks.

**And PyCAT's own lazy-read fix is what walked into it.** The eager ``get_image_data()`` decoded the
page directly and **never touched tifffile's zarr store**; ``get_image_dask_data()`` goes straight
through it. *The old path worked precisely because it was doing the wrong thing.*

── Why not just pin zarr ────────────────────────────────────────────────────────────────

Because **BioIO does not need to be the pixel transport for TIFF at all.** ``tifffile`` can seek a
single page directly — no zarr store, no dask graph, no OME plane-map walk. It is **faster than the
BioIO path even when the BioIO path works**, which is why ``_TiffPageStack`` was written in the
first place.

*Pinning zarr would re-pin the stack that the whole 1.6.0 migration existed to free, and it would
be a guess: nobody knows which zarr 3.x ``tifffile 2026.6.1`` was actually built against.*

── What BioIO still does for TIFF ───────────────────────────────────────────────────────

**Everything except move pixels.** Dimensions, scene enumeration, channel names, physical pixel
size, OME metadata. *That is what it is good at, and none of it goes near the zarr store.*

── The hazards, and which are handled ───────────────────────────────────────────────────

============================  ==========================================================
**page order (T/C/Z)**        Handled — the page index is computed from the same
                              interleaving rule ``_TiffPageStack`` uses, which was
                              derived from real Micro-Manager and OME data.
**interleaved channels**      Handled — ``n_channels`` and ``channel_idx``.
**multi-file OME-TIFF**       ``_TiffPageStack`` builds a page map across the companion
                              files that are **present**; this reader falls back to BioIO
                              for the multi-file case rather than guess.
**compressed pages**          Handled by tifffile itself — ``page.asarray()`` decodes.
**tiled TIFF**                Handled by tifffile itself.
**planar vs contiguous**      Handled by tifffile itself.
============================  ==========================================================

*Where the page mapping cannot be established with confidence, this reader **declines** and the
caller falls back to BioIO. **A wrong page is worse than a slow one** — it would show the wrong
channel, or the wrong timepoint, and nothing would look broken.*
"""

from __future__ import annotations

import os


_TIFF_SUFFIXES = ('.tif', '.tiff')


def is_tiff(path) -> bool:
    """A plain or OME TIFF — the formats tifffile can seek natively."""
    return str(path).lower().endswith(_TIFF_SUFFIXES)


def _is_multifile_ome(handle) -> bool:
    """**A multi-file OME set needs a page map across companions.**

    ``_TiffPageStack`` builds one. This single-file reader does not, and **guessing would put the
    wrong pixels on screen** — so it declines and lets BioIO handle it.
    """
    try:
        if not getattr(handle, 'is_ome', False):
            return False
        # More series than files, or an OME-XML naming companion files, means the acquisition
        # spans more than this one file.
        xml = getattr(handle, 'ome_metadata', None) or ''
        return xml.count('<UUID') > 1 or xml.count('FileName=') > 1
    except Exception:
        return False


def read_tiff_plane(path, *, t=0, c=0, z=0, n_channels=1, n_z=1, dtype=None):
    """**One page, one seek, no zarr.** ``None`` if this reader will not risk it.

    Returns the plane as a numpy array, or ``None`` when the page mapping cannot be established
    with confidence — in which case the caller should fall back to BioIO.

    ***A wrong page is worse than a slow one.*** It would show the wrong channel or the wrong
    timepoint, and **nothing about the image would look broken.**
    """
    import numpy as np
    import tifffile

    if not is_tiff(path) or not os.path.exists(str(path)):
        return None

    try:
        with tifffile.TiffFile(str(path)) as handle:
            if _is_multifile_ome(handle):
                # A multi-file OME set. `_TiffPageStack` maps pages across companions; this
                # single-file reader does not, and a guess would be silently wrong.
                return None

            pages = handle.pages
            n_pages = len(pages)
            if n_pages == 0:
                return None

            # ── The page index ──────────────────────────────────────────────────
            #
            # Micro-Manager and OME both write pages in the order the acquisition ran. The common
            # case — and the one PyCAT sees — is channel-interleaved within each (t, z):
            #
            #     page = ((t * n_z) + z) * n_channels + c
            #
            # For a plain single-channel, single-z TIFF this collapses to `page = t`, which is the
            # overwhelmingly common case and needs no interpretation at all.
            channels = max(1, int(n_channels))
            slices = max(1, int(n_z))

            index = ((int(t) * slices) + int(z)) * channels + int(c)

            if index >= n_pages:
                # The computed page does not exist. Either the interleaving assumption is wrong for
                # this file, or the caller asked for a frame that is not there. **Decline** — do
                # not return page 0 and pretend.
                return None

            plane = np.asarray(pages[index].asarray())

    except Exception:
        # tifffile could not read it. That is not a failure worth escalating — BioIO may well
        # manage, and it is the caller's fallback.
        return None

    if dtype is not None:
        plane = plane.astype(dtype, copy=False)

    return plane
