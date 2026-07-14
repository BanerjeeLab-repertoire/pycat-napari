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


def _page_and_slice(axes, shape, page_ndim, *, t, c, z):
    """**Which page, and which plane inside it.** ``(page_index, inner_key)``, or ``None``.

    ── A "page" is not a "plane", and that is what both previous versions missed ─────────

    The original code did ``pages[t * n_c + c]``. The first attempt at fixing it computed the index
    from ``series.axes`` — **and was still wrong**, because it fixed the arithmetic and left the
    wrong container.

    For a 3×2 **TCYX** file, tifffile reports ``len(series.pages) == 3`` and
    ``series.pages[0].shape == (2, 4, 4)``. ***One page holds BOTH channels.*** So:

    * ``pages[t * n_c + c]`` walks off the end — half the requests **decline**, and a decline falls
      back to BioIO, *which for TIFF is the broken ``aszarr()`` path*;
    * the requests that land return **the wrong timepoint**, with a real image on screen and
      nothing about it looking broken.

    The page list is indexed by the axes that are **not inside a page**; the axes that *are* inside
    it must be sliced out of the array the page returns.

    ``page_ndim`` is ``len(page.shape)`` — how many trailing axes the page already contains. The
    leading ``len(axes) - page_ndim`` axes select the page; the rest index within it.

    **Why it survived so long:** for a single-channel time series — Micro-Manager bead stacks, the
    only TIFF PyCAT is routinely pointed at — a page *is* a frame, the arithmetic collapses to
    ``page = t``, and the old rule was **correct by coincidence.** *A rule that is right on the only
    data anyone tested is indistinguishable from a rule that is right.*
    """
    if not axes or not shape or len(axes) != len(shape):
        return None
    if page_ndim is None or page_ndim > len(axes):
        return None

    n_leading = len(axes) - int(page_ndim)
    coordinate = {'T': int(t), 'C': int(c), 'Z': int(z)}

    # ── If the file has NO AXIS for the stack, this map cannot answer ───────────────────
    #
    # An undeclared 3-frame TIFF is read by tifffile as **``SYX``** — three *samples* (an RGB
    # image), not three timepoints. tifffile is not wrong; the file genuinely does not say.
    #
    # But ``S`` is part of the image, so this map passes it through **whole** — and a request for
    # ``t=0`` came back as the entire ``(3, 8, 8)`` stack, while ``t=99`` on a 3-frame file came
    # back as *an array*. **The old hardcoded code declined.**
    #
    # *``test_an_OUT_OF_RANGE_page_DECLINES`` caught it. The guard was right and the fix was wrong.*
    #
    # ***The test is whether the AXIS EXISTS, not whether the index is non-zero.*** A file with no
    # T and no Z is not a stack this map can index — **including at t=0**, where the bug is quietest:
    # it returns pixels, they are real, and they are the wrong shape for a plane.
    #
    # Returning ``None`` is **not a refusal** — it hands the file to the legacy geometry, which is
    # the right handler for it: PyCAT has already asked the user whether the stack axis is T or Z.
    if 'T' not in axes and 'Z' not in axes and 'C' not in axes:
        return None

    coordinate = {'T': int(t), 'C': int(c), 'Z': int(z)}
    for axis_name, requested in coordinate.items():
        if requested != 0 and axis_name not in axes:
            return None

    # Axes that select the PAGE.
    page_index = 0
    for axis, size in zip(axes[:n_leading], shape[:n_leading]):
        position = coordinate.get(axis)
        if position is None:
            # An axis this reader does not model — Q (unknown), I (generic), M (mosaic). **Decline
            # rather than guess which plane the caller meant.** A wrong page is worse than a slow
            # one.
            return None
        if not (0 <= position < size):
            return None
        page_index = page_index * size + position

    # Axes INSIDE the page. Y, X and S (samples/RGB) are the image itself and pass through whole.
    inner = []
    for axis, size in zip(axes[n_leading:], shape[n_leading:]):
        if axis in 'YXS':
            inner.append(slice(None))
            continue
        position = coordinate.get(axis)
        if position is None or not (0 <= position < size):
            return None
        inner.append(position)

    return page_index, tuple(inner)


def _legacy_geometry(handle, pages, *, t, c, z, n_channels, n_z):
    """**The file declares no axes. Fall back to the caller's stated geometry.**

    Returns ``(pages, n_pages, page_index, inner_key)``.

    This is the plain, undeclared multipage TIFF — the one PyCAT has *already* had to ask the user
    about ("is the stack axis T or Z?"), because the file itself does not say.

    Two traps live here, and both returned **real pixels of the right dtype** while being wrong:

    * **tifffile reads a plain 3-frame stack as ``SYX``** — three *samples*, i.e. an RGB image, on
      **one page**. So ``series.pages`` has a single entry holding ``(3, 8, 8)``, and ``page = t``
      declines for every ``t >= 1``. *The old code did not hit this only because it indexed
      ``handle.pages`` (3 entries) rather than ``series.pages`` (1) — switching container changed
      the geometry underneath the arithmetic.* So: use the **flat page list** when there is one,
      and index *within* the page when the whole stack sits on it.

    * **A plain 2-D image is not a stack.** One page, ``(Y, X)``, frame 0 — *the whole file is the
      plane.* Slicing ``plane[(0,)]`` returns **row 0 of the image**.
      ``test_a_plane_is_BIT_IDENTICAL_to_a_full_read`` caught exactly that.
    """
    channels = max(1, int(n_channels))
    slices = max(1, int(n_z))
    frame = ((int(t) * slices) + int(z)) * channels + int(c)

    flat = handle.pages
    if len(flat) > 1:
        return flat, len(flat), frame, ()

    if frame == 0 and len(pages[0].shape) <= 2:
        # A plain 2-D image. The file IS the plane.
        return pages, len(pages), 0, ()

    # One physical page holding the whole stack: the frame lives inside it.
    return pages, len(pages), 0, (frame,)


def read_tiff_plane(path, *, t=0, c=0, z=0, n_channels=1, n_z=1, dtype=None):
    """**One page, one seek, no zarr.** ``None`` if this reader will not risk it.

    Returns the plane as a numpy array, or ``None`` when the page mapping cannot be established
    with confidence — in which case the caller should fall back to BioIO.

    ***A wrong page is worse than a slow one.*** It would show the wrong channel or the wrong
    timepoint, and **nothing about the image would look broken.**

    ``n_channels`` / ``n_z`` are accepted for backward compatibility and used **only** when the file
    declares no axes at all (the plain, undeclared multipage TIFF). When it does declare them —
    which is nearly always — **the file's own axis order wins.** See ``_page_and_slice``.
    """
    import numpy as np
    import tifffile

    if not is_tiff(path) or not os.path.exists(str(path)):
        return None

    try:
        with tifffile.TiffFile(str(path)) as handle:
            # ── DECLINING ON A MULTI-FILE OME SET WAS A DEAD END ──────────────────
            #
            # The first version returned ``None`` here, on the reasoning that *"the caller falls
            # back to BioIO."*
            #
            # ***But for TIFF, BioIO is exactly what is broken.*** ``bioio-tifffile`` reads pixels
            # through ``tif.aszarr()``, and that is incompatible with zarr 3.2. **The decline handed
            # the file to a path that cannot work** — and Gable's 1200-frame MMStack came back as::
            #
            #     read_plane failed: ValueError: zarr 3.2.1 < 3 is not supported
            #
            # *A fallback that does not exist is not a fallback. It is a dead end with a comment
            # explaining why it is safe.*
            #
            # **tifffile resolves the multi-file set itself.** ``series`` walks the OME-XML, finds
            # the companion files, and exposes **one page list spanning them** — and it handles
            # *absent* companions too (it zero-fills and says so), which is exactly what
            # ``_TiffPageStack`` was written to do by hand.
            #
            # So: use the series' page list, and multi-file comes for free.
            try:
                series = handle.series[0] if handle.series else None
            except Exception:
                series = None

            pages = series.pages if series is not None else handle.pages
            n_pages = len(pages)
            if n_pages == 0:
                return None

            # ── Ask the FILE for the page order. Do not assume one. ─────────────
            located = None
            if series is not None:
                try:
                    page_ndim = len(pages[0].shape)
                except Exception:
                    page_ndim = None
                located = _page_and_slice(getattr(series, 'axes', None),
                                          getattr(series, 'shape', None),
                                          page_ndim, t=t, c=c, z=z)

            if located is not None:
                index, inner = located
            else:
                pages, n_pages, index, inner = _legacy_geometry(
                    handle, pages, t=t, c=c, z=z, n_channels=n_channels, n_z=n_z)

            if index >= n_pages:
                # The computed page does not exist. Either the geometry is wrong for this file, or
                # the caller asked for a frame that is not there. **Decline** — do not return page 0
                # and pretend.
                return None

            plane = np.asarray(pages[index].asarray())

            # Slice the requested plane out of the page. For a single-channel time series `inner`
            # is empty and this is a no-op — the overwhelmingly common case pays nothing.
            if inner:
                try:
                    plane = plane[inner]
                except (IndexError, TypeError):
                    # The frame is not in this page. **Decline** — returning page 0, or the whole
                    # stack, would put the wrong pixels on screen with nothing to indicate it.
                    return None

    except Exception:
        # tifffile could not read it. That is not a failure worth escalating — BioIO may well
        # manage, and it is the caller's fallback.
        return None

    if dtype is not None:
        plane = plane.astype(dtype, copy=False)

    return plane
