"""Pure lazy-wrapper builders for the generic stack loader's per-branch construction.

Extracted VERBATIM from ``FileIOClass._open_stack_generic``'s scene×channel loop (god-class
decomposition #5b). Qt/napari-free: each builder decides how to wrap ONE channel lazily and returns
``(wrapper, retain_refs, warnings)`` — it does NOT call ``add_image`` (napari wiring stays in the
controller, which does the contrast-pin + ``add_image`` + retention via ``_add_lazy_stack_layer``).

* ``wrapper``     : the lazy (T,Y,X)/(Z,Y,X)/(T,Z,Y,X) source napari will read one plane at a time.
* ``retain_refs`` : objects whose lifetime must be pinned to the layer (readers + dask arrays), for
                    the controller to retain into the layer-scoped ImageSource. Every lazy branch
                    (including T-Z, fixed in audit cleanup item 1) returns the handles its wrapper's
                    on-demand reads depend on.
* ``warnings``    : user-facing strings (e.g. a multi-file OME-TIFF with missing companions) for the
                    controller to surface — kept out of here so the module stays Qt-free.

``_TiffPageStack`` and ``_LazyArraySource`` live in ``file_io.py`` (used across it and imported by
tests), so they are **injected** to keep this module free of an import cycle. The zarr-3.2-shim
workaround (bioio's TIFF dask path is broken on zarr 3.2 → tifffile-page wrapper, else a clear error)
is load-bearing and moved unchanged.
"""

from __future__ import annotations

import os

import numpy as np

from pycat.file_io.stack_access import to_unit_float32
from pycat.utils.general_utils import debug_log


def build_tifffile_fallback_wrapper(arr, *, lazy_array_source_cls):
    """Structured reader failed → the head handed us either an eager (T,H,W) ndarray or a lazy
    ``_TiffPageStack``; wrap it for napari.

    Normalise an eager array to [0, 1] via the canonical helper (audit cleanup item 5) so its
    intensity matches every other loader path, instead of the old raw ``astype(float32)`` that left
    0–65535 counts. A ``_TiffPageStack`` already yields [0, 1] frames (and has no ``.astype``), so it
    is wrapped as-is."""
    if isinstance(arr, np.ndarray):
        arr_ch = to_unit_float32(arr, arr.dtype)
    else:
        arr_ch = arr   # already a self-normalising lazy wrapper (e.g. _TiffPageStack → [0,1] frames)
    wrapper = lazy_array_source_cls(arr_ch)
    return wrapper, [arr_ch], []


def build_timeseries_wrapper(file_path, ext, image, channel_idx, n_t, n_c, H, W, *,
                             tiff_page_stack_cls, lazy_array_source_cls):
    """Pure time series (T, Y, X).

    For TIFF/OME-TIFF read frames straight from the multipage TIFF via ``_TiffPageStack`` (a direct
    per-page seek, far faster to scrub than the structured reader's dask path, and it sidesteps
    tifffile's zarr-3.2-broken ``aszarr``). CZI has no tifffile path, so it keeps the dask wrapper.
    """
    warnings = []
    # For a TIFF the dask array is never built (``tif.aszarr()`` is broken on zarr 3.2); it is only
    # needed as the non-tifffile fallback below.
    dask_arr = None
    if ext not in ('.tif', '.tiff'):
        dask_arr = image.get_image_dask_data('TYX', C=channel_idx)

    wrapper = None
    if ext in ('.tif', '.tiff'):
        try:
            # tifffile reports the dtype directly — no zarr store, no dask.
            import tifffile as _tf_probe
            with _tf_probe.TiffFile(file_path) as _probe:
                _tiff_dtype = _probe.pages[0].dtype

            wrapper = tiff_page_stack_cls(
                file_path, n_t, H, W, _tiff_dtype,
                channel_idx=channel_idx, n_channels=n_c)
            # Multi-file OME set with missing companions: warn and proceed with present frames.
            _pinfo = getattr(wrapper, '_present_info', None)
            if _pinfo and _pinfo.get('missing'):
                warnings.append(
                    f"This OME-TIFF references {len(_pinfo['referenced'])} linked files but "
                    f"{len(_pinfo['missing'])} are missing "
                    f"({', '.join(_pinfo['missing'][:3])}"
                    f"{'…' if len(_pinfo['missing']) > 3 else ''}). "
                    f"Loading only the {wrapper.shape[0]} frames that are present. If you meant to "
                    f"analyse the full set, keep the linked .ome.tif files together.")
            # Single-file fast path: the page count must be consistent with (frames × channels).
            _pages_attr = getattr(wrapper, '_pages', None)
            if _pages_attr is not None:
                _npages = len(_pages_attr)
                if n_c > 1 and _npages < n_t * n_c:
                    wrapper.close()
                    wrapper = None
        except Exception as _te:
            debug_log("file_io: tifffile page reader failed, "
                      "using the structured reader's dask wrapper", _te)
            wrapper = None

    if wrapper is None:
        # The tifffile page reader declined or failed. Fall back to BioIO's dask array — built NOW,
        # because for a TIFF it was deliberately not built above. This can itself fail on a TIFF, and
        # if it does the file genuinely cannot be read lazily — say so plainly rather than let
        # tifffile's misleading "zarr < 3" message reach the user.
        if dask_arr is None:
            try:
                dask_arr = image.get_image_dask_data('TYX', C=channel_idx)
            except Exception as _dask_exc:
                raise RuntimeError(
                    f"Could not read {os.path.basename(file_path)} lazily.\n\n"
                    f"The tifffile page reader declined, and BioIO's dask path "
                    f"failed too: {_dask_exc}\n\n"
                    f"If this says 'zarr < 3 is not supported', that message is "
                    f"misleading — it is tifffile's zarr store failing to import "
                    f"from a newer zarr, not a version that is too old."
                ) from _dask_exc

        wrapper = lazy_array_source_cls(dask_arr)
        retain_refs = [(image, dask_arr)]
    else:
        retain_refs = [wrapper]   # keep the tifffile handle open
    return wrapper, retain_refs, warnings


def _dask_stack_wrapper(axes, kind, file_path, ext, image, channel_idx, *, lazy_array_source_cls):
    """Shared body for the (Z,Y,X) and (T,Z,Y,X) branches: build the structured reader's dask array
    for ``axes``, translating tifffile's misleading zarr-3.2 error into a clear one for TIFF."""
    try:
        dask_arr = image.get_image_dask_data(axes, C=channel_idx)
    except Exception as _dask_exc:
        if 'zarr' in str(_dask_exc).lower() and ext in ('.tif', '.tiff'):
            raise RuntimeError(
                f"Cannot read {os.path.basename(file_path)} lazily.\n\n"
                f"BioIO reads TIFF pixels through tifffile's zarr store, and that "
                f"store is incompatible with the installed zarr "
                f"({_dask_exc}).\n\n"
                f"**That message is misleading** — it is not that zarr is too old. "
                f"tifffile's zarr module fails to import a symbol that a newer "
                f"zarr renamed, and tifffile reports it as a version problem.\n\n"
                f"2-D time series are read natively and are unaffected. This is a "
                f"TIFF {kind}, which still depends on that path."
            ) from _dask_exc
        raise
    return lazy_array_source_cls(dask_arr), dask_arr


def build_zstack_wrapper(file_path, ext, image, channel_idx, *, lazy_array_source_cls):
    """Pure z-stack (Z, Y, X)."""
    wrapper, dask_arr = _dask_stack_wrapper(
        'ZYX', "Z stack", file_path, ext, image, channel_idx,
        lazy_array_source_cls=lazy_array_source_cls)
    return wrapper, [(image, dask_arr)], []


def build_tzstack_wrapper(file_path, ext, image, channel_idx, *, lazy_array_source_cls):
    """Nested time-series-with-z-stack (T, Z, Y, X) — the dask array is already lazy, so napari reads
    one plane per T/Z slider move and the window opens immediately (the old code transcoded the whole
    channel to a temp zarr first).

    Retains ``(image, dask_arr)`` like the z-stack branch: the dask array is lazy, so the READER must
    stay alive for those on-demand reads. The pre-migration branch retained nothing here — a latent
    orphaned-reader bug, fixed as part of the ImageSource migration (audit cleanup item 1)."""
    wrapper, dask_arr = _dask_stack_wrapper(
        'TZYX', "T+Z stack", file_path, ext, image, channel_idx,
        lazy_array_source_cls=lazy_array_source_cls)
    return wrapper, [(image, dask_arr)], []
