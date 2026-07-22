"""The format-specific stack openers, moved out of file_io.py (decomposition, move 4, 1.6.146).

`_open_stack_ims`, `_open_stack_generic`, `_open_czi_streaming` are the format-specific pixel logic the
audit says must leave `file_io.py`. They are kept as a MIXIN rather than standalone functions on purpose:
each writes FileIOClass instance state (`_stack_lazy_refs`, `_current_scene`, `_current_stack_img_source`)
and calls sibling methods (`_finalise_stack_load`, `_add_lazy_stack_layer`, `_run_with_busy_progress`),
so a function form would take ~10+ params or pass `self` — the "worse seam" the spec warns against. The
mixin moves the ~600 lines out of `file_io.py` verbatim (move, don't rewrite), exactly as the vpt_ui
decomposition did. `FileIOClass` inherits it, so `self.<sibling>` resolves via the MRO unchanged.

The import block is copied from file_io.py so every module-level name the methods reference resolves.
"""
from __future__ import annotations

import os
import numpy as np
import skimage as sk
from pycat.file_io.image_reader import open_image, read_plane
from pycat.file_io.readers.mask_reader import read_2d_mask_channels
from pycat.file_io.readers.ims_reader import (
    _ImsReaderTYX, _ImsReaderZYX, _ImsReaderTZYX,
    _suppress_ims_chunk_prints, _ims_pixel_size_um)
from pycat.utils.channel_naming import (
    extract_channel_info,
    extract_channel_info_from_ims,
    suggest_colormap,
)
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QCheckBox, QRadioButton, QPushButton, QFileDialog, QLineEdit, QMessageBox
from PyQt5.QtGui import QFont
from pycat.file_io.dialogs import ChannelAssignmentDialog, LayerDataframeSelectionDialog  # moved from here, 1.6.146
from pycat.file_io.naming import (_lazy_contrast_limits, _tiff_pixel_size_um,  # moved out 1.6.146; re-exported
                                 _ome_pixel_size_um, _lazy_backing_label)  # noqa: F401
from pycat.utils.errors import StackLoadCancelled  # noqa: F401  (re-exported)
from pycat.ui.ui_utils import add_image_with_default_colormap
from pycat.utils.general_utils import dtype_conversion_func, debug_log
from pycat.utils.frame_interval import record_time_axis
from pycat.toolbox.image_processing_tools import apply_rescale_intensity
from pycat.file_io.stack_access import to_unit_float32
from pycat.file_io.multidim_io import _ZarrTZYX, _ZarrZYX
from pycat.file_io.lazy_sources import (      # noqa: F401  (re-exported for callers)
    _TiffPageStack,
    _TiffPageStackZYX,
    _TiffPageStackTZYX,
    _LazyArraySource,
    resolve_ome_file_set,
    build_ome_page_map,
)
from pycat.file_io.stack_access import (       # noqa: F401  (re-exported for callers)
    materialize_stack,
    iter_frames,
    layer_is_stack,
    extract_2d_plane,
    warn_if_assumed_axis,
)
from pycat.file_io.napari_adapter import EAGER_DIAMETER_LAYERS  # noqa: F401


def _stem_of(path):
    """The file stem for filename-aware channel naming ('Image1-GFP' for Image1-GFP.tif), or None."""
    return os.path.splitext(os.path.basename(path))[0] if path else None


class _StackOpenersMixin:
    """Format-specific stack openers. Mixed into ``FileIOClass``; bodies unchanged."""

    def _open_stack_ims(self, file_path: str):
        """IMS loader — zarr-native lazy reading, unchanged from open_ims_file."""
        try:
            # Importing hdf5plugin registers bundled HDF5 compression filters.
            # Some IMS files read metadata without it but fail on pixel data.
            import hdf5plugin  # noqa: F401
            from imaris_ims_file_reader.ims import ims as ImsReader
            import zarr
        except ImportError as _ie:
            from napari.utils.notifications import show_warning as napari_show_warning
            napari_show_warning(
                f"Missing dependency: {_ie}\n"
                "Install with:  pip install imaris-ims-file-reader hdf5plugin zarr"
            )
            return

        from napari.utils.notifications import show_info as napari_show_info

        reader = ImsReader(file_path, squeeze_output=False)
        n_t    = reader.TimePoints
        n_c    = reader.Channels
        shape  = reader.shape          # (T, C, Z, Y, X)
        n_z    = shape[2]
        H, W   = shape[3], shape[4]
        dtype  = reader.dtype

        print(f"[PyCAT IMS] {self.base_file_name}: "
              f"T={n_t} C={n_c} Z={n_z} Y={H} X={W}  dtype={dtype}")

        microns_per_pixel = 1.0
        try:
            microns_per_pixel = _ims_pixel_size_um(reader, W) or 1.0
        except Exception as _e:  # broad-ok: format/metadata read logged via debug_log; open continues with a fallback
            debug_log("file_io: IMS pixel-size read failed, using 1.0 µm/px", _e)

        # Extract and store the full normalised metadata record (IMS metadata
        # was previously discarded entirely — update_metadata is only called on
        # the structured-reader path).
        try:
            from pycat.file_io.metadata_extract import extract_metadata
            md = extract_metadata(file_path, reader=reader, width_px=W)
            self.central_manager.active_data_class.data_repository['file_metadata'] = md
        except Exception as _e:  # broad-ok: format/metadata read logged via debug_log; open continues with a fallback
            debug_log("file_io: IMS metadata extraction failed", _e)

        channels_to_load = list(range(n_c))
        # NOTE: `_ims_file_path` is kept because timeseries_condensate_tools reads it via getattr
        # to locate the on-disk source. That cross-file reach-in is a separate, clearly-scoped
        # migration (→ read ImageSource.file_path from the layer instead); it is intentionally NOT
        # bundled into this retention change. The other legacy `_ims_*` attributes were removed in
        # this release: reader retention is now owned solely by ImageSource (below), proven by
        # tests/test_ims_reader_retention.py. See docs/audits/ims_zarr_refs_resolved_2026-07-14.md.
        self._ims_file_path = file_path
        channel_data = None

        # ── ImageSource: explicit reader ownership, lifetime-tied to the layers ──────────
        # The SOLE owner of the IMS readers now. It is attached to each lazy layer's metadata
        # below, so the readers it holds live exactly as long as the layers do — not as long as
        # FileIOClass. This replaces the old _ims_reader (primary) + _ims_zarr_refs (siblings)
        # retention, which kept readers alive only incidentally by living on the session-scoped
        # FileIOClass instance.
        from pycat.file_io.image_source import ImageSource
        _img_source = ImageSource(file_path=file_path)
        _img_source.retain(reader)

        # ── Multi-position detection ─────────────────────────────────────
        # A single IMS file never contains multiple stage positions —
        # Imaris ("File Series") multi-position acquisitions are always
        # saved as separate sibling .ims files. Detect them by filename
        # pattern and offer to open the ones the user wants alongside
        # this one, rather than silently only ever showing this position.
        from pycat.file_io.multidim_io import (
            find_sibling_position_files, show_position_selection_dialog)

        sibling_positions = find_sibling_position_files(file_path)
        positions_to_open = [file_path]
        if sibling_positions:
            selected_idx = show_position_selection_dialog(
                sibling_positions,
                title=f"Multi-Position Acquisition Detected ({len(sibling_positions)} positions)",
            )
            if selected_idx:
                positions_to_open = [sibling_positions[i]['path']
                                     for i in selected_idx]
                napari_show_info(
                    f"Opening {len(positions_to_open)} of "
                    f"{len(sibling_positions)} detected position(s)."
                )
            # else: user cancelled the multi-position dialog — fall back
            # to opening only the originally-selected file.

        for pos_path in positions_to_open:
            pos_suffix = ''
            if len(positions_to_open) > 1:
                # Tag layer names with the position so multiple positions
                # opened together remain distinguishable in the layer list.
                for sp in sibling_positions:
                    if sp['path'] == pos_path:
                        pos_suffix = f" [Pos {sp['position_index']}]"
                        break

            if pos_path == file_path:
                pos_reader = reader
            else:
                pos_reader = ImsReader(pos_path, squeeze_output=False)
            # Pin this position's reader lifetime to the ImageSource. retain() dedups by
            # identity, so the primary reader (already retained above) is not held twice.
            _img_source.retain(pos_reader)

            for channel_idx in channels_to_load:
                with _suppress_ims_chunk_prints():
                    _ch_info = extract_channel_info_from_ims(
                        pos_reader, channel_idx,
                        file_stem=_stem_of(pos_path))
                _ch_label    = _ch_info['layer_name']
                _ch_colormap = suggest_colormap(_ch_info['bucket'])
                debug_log(f"file_io: IMS channel {channel_idx} -> "
                          f"name='{_ch_info.get('raw_name')}' label='{_ch_label}' "
                          f"bucket='{_ch_info.get('bucket')}'")

                if n_t == 1 and n_z == 1:
                    # Single 2D frame — no lazy wrapper needed. Normalise to [0, 1] via the canonical
                    # helper (audit cleanup item 5), NOT a raw astype: load_into_viewer's img_as_float32
                    # does not rescale a FLOAT input, so a bare .astype(float32) here leaked raw counts
                    # into analysis while a multi-frame IMS (via _ImsReaderTYX → to_unit_float32) is [0,1].
                    with _suppress_ims_chunk_prints():
                        _raw = pos_reader[0, channel_idx, 0, :, :]
                    frame = to_unit_float32(_raw, getattr(_raw, 'dtype', None))
                    self.load_into_viewer(
                        frame, name=f"{self.base_file_name} {_ch_label}{pos_suffix}")
                    channel_data = frame

                elif n_z == 1:
                    # Pure time series (T, Y, X) — direct reader path, bypasses
                    # the zarr-store adapter that can raise KeyError on valid chunks.
                    layer_name = f"{self.base_file_name} {_ch_label} Stack{pos_suffix}"
                    lazy_tyx = _ImsReaderTYX(pos_reader, channel_idx,
                                             suppress_ctx=_suppress_ims_chunk_prints)
                    if channel_idx == 0 and pos_path == file_path:
                        # Probe-read the first frame to populate channel_data (used
                        # only for default object/cell diameter estimates). Wrapped
                        # defensively: Box Drive, file locks, or partial HDF5 syncs
                        # can raise OSError/KeyError at this point; if so we fall back
                        # to a dummy array of the correct spatial size so the layer
                        # still loads — the user gets a warning with the likely cause.
                        try:
                            channel_data = lazy_tyx[0]
                        except (KeyError, OSError, Exception) as _probe_err:  # broad-ok: format-open failure surfaced to the user; the open aborts gracefully
                            from napari.utils.notifications import show_warning as _sw
                            _sw(
                                f"IMS: could not pre-read the first frame of "
                                f"'{self.base_file_name}' ({_probe_err}). "
                                "The layer will still be added lazily. "
                                "If the file lives on Box Drive or a network share, "
                                "ensure it is fully downloaded locally (right-click → "
                                "'Make Available Offline' in Box Drive) before opening. "
                                "Also check that Imaris is not holding the file open."
                            )
                            channel_data = np.zeros((H, W), dtype=np.float32)
                    # Compute contrast limits from the FIRST frame only and pass
                    # them explicitly. Without this, napari auto-estimates contrast
                    # (and builds the thumbnail) by calling np.asarray() on the
                    # layer — which for a lazy (T,Y,X) wrapper triggers __array__
                    # and loads EVERY frame from disk. On a USB-HDD IMS stack that
                    # is the real cause of the multi-second stalls (e.g. when adding
                    # an ROI layer forces a layer-list refresh). One frame is already
                    # cheap to read; the user can still adjust contrast afterwards.
                    _prefetched = channel_data if (channel_idx == 0 and pos_path == file_path) else None
                    _clim = _lazy_contrast_limits(lazy_tyx, prefetched=_prefetched)
                    _add_kwargs = dict(name=layer_name, colormap=_ch_colormap)
                    if _clim is not None:
                        _add_kwargs['contrast_limits'] = _clim
                    _layer = self.viewer.add_image(lazy_tyx, **_add_kwargs)
                    try:
                        _layer.metadata['pycat_image_source'] = _img_source
                    except Exception as _e:  # broad-ok: format/metadata read logged via debug_log; open continues with a fallback
                        debug_log("file_io: could not attach ImageSource to TYX layer", _e)
                    napari_show_info(
                        f"Lazy-loaded IMS {_ch_label}{pos_suffix}: {n_t} frames "
                        f"{H}\u00d7{W}px (frames read on demand)"
                    )

                elif n_t == 1:
                    # Pure z-stack (Z, Y, X), no time dimension — lazy, on demand.
                    layer_name = f"{self.base_file_name} {_ch_label} Z-Stack{pos_suffix}"
                    lazy_zyx = _ImsReaderZYX(pos_reader, channel_idx, t=0,
                                             suppress_ctx=_suppress_ims_chunk_prints)
                    if channel_idx == 0 and pos_path == file_path:
                        try:
                            channel_data = lazy_zyx[0]
                        except (KeyError, OSError, Exception) as _probe_err:  # broad-ok: format-open failure surfaced to the user; the open aborts gracefully
                            from napari.utils.notifications import show_warning as _sw
                            _sw(
                                f"IMS: could not pre-read the first z-slice of "
                                f"'{self.base_file_name}' ({_probe_err}). "
                                "The layer will still be added lazily. "
                                "If the file is on Box Drive or a network share, "
                                "ensure it is fully downloaded locally before opening."
                            )
                            channel_data = np.zeros((H, W), dtype=np.float32)
                    _prefetched = channel_data if (channel_idx == 0 and pos_path == file_path) else None
                    _clim = _lazy_contrast_limits(lazy_zyx, prefetched=_prefetched)
                    _add_kwargs = dict(name=layer_name, colormap=_ch_colormap)
                    if _clim is not None:
                        _add_kwargs['contrast_limits'] = _clim
                    _layer = self.viewer.add_image(lazy_zyx, **_add_kwargs)
                    try:
                        _layer.metadata['pycat_image_source'] = _img_source
                    except Exception as _e:  # broad-ok: format/metadata read logged via debug_log; open continues with a fallback
                        debug_log("file_io: could not attach ImageSource to ZYX layer", _e)
                    napari_show_info(
                        f"Lazy-loaded IMS z-stack {_ch_label}{pos_suffix}: "
                        f"{n_z} slices {H}\u00d7{W}px (slices read on demand)"
                    )

                else:
                    # Nested time-series-with-z-stack (T, Z, Y, X) — the
                    # scenario this fix targets. Previously this branch
                    # forced a single-timepoint choice and DISCARDED every
                    # other timepoint's z-data entirely. Now a genuine
                    # lazy 4D array is handed to napari, which natively
                    # adds both a T slider and a Z slider — no data lost,
                    # nothing materialised until the user scrubs to it.
                    layer_name = f"{self.base_file_name} {_ch_label} T-Z Stack{pos_suffix}"
                    lazy_tzyx = _ImsReaderTZYX(pos_reader, channel_idx,
                                               suppress_ctx=_suppress_ims_chunk_prints)
                    if channel_idx == 0 and pos_path == file_path:
                        channel_data = lazy_tzyx[0, 0]
                    # First (t=0, z=0) plane for contrast — reuse the prefetched
                    # one for channel 0, else read a single plane.
                    try:
                        _plane0 = (channel_data if (channel_idx == 0 and pos_path == file_path)
                                   else lazy_tzyx[0, 0])
                    except Exception:  # broad-ok: best-effort probe → fallback value; a read failure must not crash the open
                        _plane0 = None
                    _clim = _lazy_contrast_limits(lazy_tzyx, prefetched=_plane0)
                    _add_kwargs = dict(name=layer_name, colormap=_ch_colormap)
                    if _clim is not None:
                        _add_kwargs['contrast_limits'] = _clim
                    _layer = self.viewer.add_image(lazy_tzyx, **_add_kwargs)
                    try:
                        _layer.metadata['pycat_image_source'] = _img_source
                    except Exception as _e:  # broad-ok: format/metadata read logged via debug_log; open continues with a fallback
                        debug_log("file_io: could not attach ImageSource to TZYX layer", _e)
                    napari_show_info(
                        f"Lazy-loaded IMS T-Z stack {_ch_label}{pos_suffix}: "
                        f"{n_t} timepoints \u00d7 {n_z} z-slices, "
                        f"{H}\u00d7{W}px (nothing pre-loaded — scrub T/Z sliders "
                        f"to read on demand)"
                    )

        self._finalise_stack_load(H, W, microns_per_pixel, channels_to_load,
                                  n_t, n_z, file_path, source='ims')


    # ── Generic back-end (TIFF, CZI, …) ────────────────────────────────────

    # Above this size, run the libCZI open-probe on a worker thread (a streaming movie's subblock
    # parse is multi-second). Small confocal/widefield CZIs are a few MB and parse instantly, so they
    # probe inline — no worker dialog to flash. Streaming movies are GBs, so the gap is enormous; the
    # threshold only has to sit between them.

    def _open_stack_generic(self, file_path: str, ext: str):
        """
        Generic stack loader for TIFF, OME-TIFF, and CZI files via the reader seamImage.

        Reads the full T, C, Z dimensions from file metadata (OME-XML,
        ImageJ hyperstack description, or format-native equivalent) rather
        than forcing a choice between T and Z when both are present —
        nested time-series-with-z-stack acquisitions are loaded as genuine
        lazy 4D (T, Z, Y, X) per-channel arrays, matching the IMS loader.

        Multi-position acquisitions (OME-XML scenes / Bio-Formats series)
        are detected via the reader's `.scenes` and offered through the
        same position-selection dialog used for IMS sibling files.
        """
        # ── Zeiss fast-streaming CZI: libCZI cannot decode it ──────────────────
        #
        # Confocal and widefield-single-subblock CZI read fine (and fast, no JVM) through libCZI, so
        # only DIVERT to BioFormats when a pixel read actually fails — the streaming/many-subblock
        # layout (e.g. a 15,766-frame movie) raises "not implemented" on every plane. The BioFormats
        # path is opt-in (`pip install pycat-napari[bioformats]`). See
        # docs/audits/czi_bakeoff_2026-07-15.md.
        #
        # The probe OPENS libCZI (parsing every subblock offset — ~11 s for a 15,766-frame movie), so
        # for a big file run it OFF the Qt thread behind the busy dialog, else that parse freezes the
        # window before the BioFormats indexing even starts. A small CZI parses in milliseconds, so it
        # stays inline (a worker dialog would only flash). The probe returns the libCZI image, which
        # the streaming loader reuses — the big open is paid ONCE, not twice.
        if ext == '.czi':
            import os as _os
            from napari.utils.notifications import show_info as _czi_show_info
            from pycat.file_io.readers import czi_bioformats as _czibf
            _probe = (lambda: _czibf.probe_libczi(file_path))
            try:
                if _os.path.getsize(file_path) > self._CZI_OFFTHREAD_BYTES:
                    _can_read, _czi_image = self._run_with_busy_progress(
                        _probe, "Reading CZI",
                        "Indexing this CZI's frames…\n\nLarge Zeiss files parse every frame offset "
                        "first; the window stays responsive.")
                else:
                    _can_read, _czi_image = _probe()
            except StackLoadCancelled:
                _czi_show_info("CZI open cancelled.")
                return
            if not _can_read:
                self._open_czi_streaming(file_path, image=_czi_image)
                return

        from napari.utils.notifications import show_info as napari_show_info
        from napari.utils.notifications import show_warning as napari_show_warning
        from pycat.file_io.multidim_io import (
            show_position_selection_dialog, _ZarrTZYX_generic)
        from pycat.file_io.readers.stack_layer_builders import (
            build_tifffile_fallback_wrapper, build_timeseries_wrapper,
            build_zstack_wrapper, build_tzstack_wrapper)

        n_c = 1

        # ── Read metadata + select reader (extracted: readers/stack_metadata.py, decomposition #5a) ──
        #
        # The pure read (structured reader → dims/scenes/pixel size, else a lazy tifffile-page
        # fallback) now lives in read_stack_structure. `_TiffPageStack` / `_tiff_pixel_size_um` are
        # injected because they live here and are used elsewhere. The Qt scene dialog and the
        # data-repository side effects (update_metadata / file_metadata) STAY in the controller —
        # they are not pure, and relocating them out of the fallback-triggering try is behaviour-
        # preserving (update_metadata never propagates; the dialog returns a selection, not a raise).
        from pycat.file_io.readers.stack_metadata import read_stack_structure
        _struct = read_stack_structure(
            file_path, ext,
            tiff_page_stack_cls=_TiffPageStack,
            tiff_pixel_size_um=_tiff_pixel_size_um,
            ome_pixel_size_um=_ome_pixel_size_um)
        reader_has_structure = _struct.reader_has_structure
        microns_per_pixel = _struct.microns_per_pixel

        if reader_has_structure:
            image = _struct.image

            # ── Multi-position (scene) detection — load ONE position at a time ──────────
            #
            # Materialising several scenes at once is the load-everything memory profile the
            # streaming work removed everywhere else. So a multi-position file now loads exactly ONE
            # scene, lazily; the scene switcher changes position in place afterwards (no reopen). The
            # existing dialog still asks which position — we honour the first choice and default to
            # scene 0 — and the memory footgun (several scenes overlaid) is gone rather than kept
            # beside the new default.
            scenes = _struct.scenes
            scenes_to_load = [image.current_scene] if scenes else [None]
            if len(scenes) > 1:
                scene_dicts = [{'position_index': i, 'filename': s}
                               for i, s in enumerate(scenes)]
                selected_idx = show_position_selection_dialog(
                    scene_dicts,
                    title=f"Multi-Position Acquisition Detected ({len(scenes)} scenes)",
                )
                chosen = selected_idx[0] if selected_idx else 0
                image.set_scene(scenes[chosen])          # pin the reader to the chosen position
                scenes_to_load = [scenes[chosen]]
                napari_show_info(
                    f"Opening position {chosen + 1} of {len(scenes)} ('{scenes[chosen]}'). "
                    f"Use the scene switcher to change position.")

            self.central_manager.active_data_class.update_metadata(image)
            # Also store the normalised metadata record for the metadata widget and results export.
            try:
                from pycat.file_io.metadata_extract import extract_metadata
                _md = extract_metadata(file_path, image=image)
                self.central_manager.active_data_class.data_repository['file_metadata'] = _md
            except Exception as _mde:  # broad-ok: format/metadata read logged via debug_log; open continues with a fallback
                debug_log("file_io: metadata extraction failed", _mde)
        else:
            scenes_to_load = [None]
            arr = _struct.fallback_array
            n_frames = _struct.n_frames
            H, W = _struct.H, _struct.W
            n_c = 1
            n_t, n_z = n_frames, 1

        # (No temp zarr store: the old synchronous full-file zarr transcode is gone — every branch
        # now hands napari an already-lazy wrapper. The `pycat_stack_*` mkdtemp and `_stack_zarr_paths`
        # it fed were obsolete scaffolding — audit cleanup item 3.)
        # Retention is owned by a layer-scoped ImageSource, exactly like the IMS loader — it keeps
        # the backing reader/dask handles alive for the LAYER's lifetime, so on-demand frame reads
        # keep working after this method returns, with no controller-scoped list to leak or forget
        # (the old self._stack_lazy_refs is gone — audit cleanup item 1). `_add_lazy_stack_layer`
        # retains into it and attaches it to each lazy layer's metadata['pycat_image_source'].
        from pycat.file_io.image_source import ImageSource
        self._current_stack_img_source = ImageSource(file_path=file_path)
        channels_to_load = list(range(n_c)) if not reader_has_structure else None
        H = W = n_t = n_z = None

        self._current_scene = None          # tagged onto lazy layers; only set for multi-scene files
        for scene in scenes_to_load:
            scene_suffix = ''
            # A real scene name only for a genuine multi-position file (`len(scenes) > 1`); a
            # single-scene file keeps scene=None here, so it is tagged and named exactly as before.
            if reader_has_structure and scene is not None and len(scenes) > 1:
                image.set_scene(scene)                   # re-pin (idempotent) so dims/reads are this scene
                scene_suffix = f" [{scene}]"
                self._current_scene = scene

            if reader_has_structure:
                n_t = getattr(image.dims, 'T', 1)
                n_c = getattr(image.dims, 'C', 1)
                n_z = getattr(image.dims, 'Z', 1)
                H   = getattr(image.dims, 'Y', None)
                W   = getattr(image.dims, 'X', None)
                channels_to_load = list(range(n_c))

            _stem = _stem_of(file_path)
            for channel_idx in channels_to_load:
                if reader_has_structure:
                    _ch_info = extract_channel_info(image, channel_idx, file_stem=_stem)
                else:
                    _ch_info = {'layer_name': f'C{channel_idx}',
                                 'bucket': 'unknown', 'label': f'C{channel_idx}',
                                 'source': 'position'}

                _ch_label    = _ch_info['layer_name']
                _ch_colormap = suggest_colormap(_ch_info['bucket'])

                if not reader_has_structure:
                    # tifffile fallback — single (T,H,W), no Z/scene metadata
                    layer_name = f"{self.base_file_name} {_ch_label} Stack{scene_suffix}"
                    _wrapper, _refs, _warns = build_tifffile_fallback_wrapper(
                        arr, lazy_array_source_cls=_LazyArraySource)
                    self._add_lazy_stack_layer(
                        _wrapper, layer_name, _ch_colormap, _refs, _warns,
                        f"Loaded {_ch_label}: {n_frames} frames "
                        f"{H}×{W}px → '{layer_name}'")
                    continue

                if n_t == 1 and n_z == 1:
                    # Normalise to [0, 1] via the canonical DTYPE-MAX helper — NOT a raw float cast
                    # (`dtype=np.float32`), which would reach load_into_viewer as raw counts and hit
                    # its (former) min-max branch: a per-frame contrast stretch that corrupts
                    # partition ratios and false-trips saturation ceilings. Reads the native (integer)
                    # dtype, then divides by the dtype max — matching every other loader and the IMS
                    # single-frame path. See tests/test_loaders_agree_on_scale.py.
                    _raw = read_plane(image, path=file_path, c=channel_idx, t=0, z=0)
                    frame = to_unit_float32(_raw, getattr(_raw, 'dtype', None))
                    self.load_into_viewer(
                        frame,
                        name=f"{self.base_file_name} {_ch_label}{scene_suffix}")

                elif n_z == 1:
                    # Pure time series (T, Y, X): the tifffile-page fast path (or the reader's dask).
                    layer_name = f"{self.base_file_name} {_ch_label} Stack{scene_suffix}"
                    _wrapper, _refs, _warns = build_timeseries_wrapper(
                        file_path, ext, image, channel_idx, n_t, n_c, H, W,
                        tiff_page_stack_cls=_TiffPageStack,
                        lazy_array_source_cls=_LazyArraySource)
                    self._add_lazy_stack_layer(
                        _wrapper, layer_name, _ch_colormap, _refs, _warns,
                        f"Loaded {_ch_label}{scene_suffix}: {n_t} frames "
                        f"{H}×{W}px → '{layer_name}' (lazy)")

                elif n_t == 1:
                    # Pure z-stack (Z, Y, X). TIFF reads natively via `_TiffPageStackZYX` — before
                    # 1.6.71 this branch was dask-only and a z-stack TIFF did NOT load (BioIO's
                    # TIFF path dies on zarr 3.2).
                    layer_name = f"{self.base_file_name} {_ch_label} Z-Stack{scene_suffix}"
                    _wrapper, _refs, _warns = build_zstack_wrapper(
                        file_path, ext, image, channel_idx, n_z, n_c, H, W,
                        tiff_zstack_cls=_TiffPageStackZYX,
                        lazy_array_source_cls=_LazyArraySource)
                    self._add_lazy_stack_layer(
                        _wrapper, layer_name, _ch_colormap, _refs, _warns,
                        f"Loaded {_ch_label}{scene_suffix} z-stack: {n_z} slices "
                        f"{H}×{W}px → '{layer_name}' ({_lazy_backing_label(_wrapper)})")

                else:
                    # Nested time-series-with-z-stack (T, Z, Y, X) — a genuine lazy 4D array; napari
                    # adds a T slider AND a Z slider automatically. One plane per slider move, so the
                    # window opens immediately (the old code transcoded the whole channel first).
                    layer_name = f"{self.base_file_name} {_ch_label} T-Z Stack{scene_suffix}"
                    _wrapper, _refs, _warns = build_tzstack_wrapper(
                        file_path, ext, image, channel_idx, n_t, n_z, n_c, H, W,
                        tiff_tzstack_cls=_TiffPageStackTZYX,
                        lazy_array_source_cls=_LazyArraySource)
                    self._add_lazy_stack_layer(
                        _wrapper, layer_name, _ch_colormap, _refs, _warns,
                        f"Loaded {_ch_label}{scene_suffix} T-Z stack: "
                        f"{n_t} timepoints × {n_z} z-slices, "
                        f"{H}×{W}px → '{layer_name}' ({_lazy_backing_label(_wrapper)})")

        self._current_scene = None          # don't let a scene leak onto a later, unrelated load

        self._finalise_stack_load(H, W, microns_per_pixel,
                                  list(range(n_c)),
                                  n_t if reader_has_structure else n_frames,
                                  n_z if reader_has_structure else 1,
                                  file_path, source='generic')

    def _open_czi_streaming(self, file_path: str, image=None):
        """Load a Zeiss streaming CZI (which libCZI cannot decode) via BioFormats.

        Pixels come from the direct BioFormats reader (``openBytes``, ~5 ms/plane — bioio's dask path
        is ~1000× slower); dims, pixel size and channel identity come from libCZI's metadata (which
        reads fine — only its PIXEL reads fail). The ~33 s one-time reader open (parsing the frame
        index) runs on a worker thread so the Qt UI stays responsive. The reader's lifetime is pinned
        to the layers via ``ImageSource``, exactly like the IMS path. See
        docs/audits/czi_bakeoff_2026-07-15.md.

        ``image`` : the libCZI metadata image from the routing probe, reused so a big movie's
        multi-second libCZI open is not paid a second time. Opened here only if not supplied.
        """
        from napari.utils.notifications import show_info as napari_show_info
        from napari.utils.notifications import show_warning as napari_show_warning
        from pycat.file_io.readers import czi_bioformats as _czibf
        from pycat.file_io.image_source import ImageSource
        from pycat.file_io.czi_seam import warn_seam_qc

        if not _czibf.bioformats_available():
            napari_show_warning(
                "This is a Zeiss fast-streaming CZI, which the built-in reader (libCZI) cannot "
                "decode. Install the BioFormats extra to open it:\n"
                "    pip install pycat-napari[bioformats]\n"
                "Alternatively, export it to OME-TIFF from ZEN.")
            return

        # Metadata via libCZI — it opens the file fine; only the pixel reads fail. Reuse the probe's
        # image when given (the big open is not paid twice); open here only as a fallback.
        microns_per_pixel = 1.0
        try:
            if image is None:
                image = open_image(file_path)
            try:
                px = image.physical_pixel_sizes
                microns_per_pixel = float(px.Y) if px.Y else 1.0
            except Exception as _pe:  # broad-ok: format/metadata read logged via debug_log; open continues with a fallback
                debug_log("file_io: CZI physical pixel size unavailable", _pe)
            self.central_manager.active_data_class.update_metadata(image)
            try:
                from pycat.file_io.metadata_extract import extract_metadata
                _md = extract_metadata(file_path, image=image)
                self.central_manager.active_data_class.data_repository['file_metadata'] = _md
            except Exception as _mde:  # broad-ok: format/metadata read logged via debug_log; open continues with a fallback
                debug_log("file_io: CZI metadata extraction failed", _mde)
        except Exception as _e:  # broad-ok: format/metadata read logged via debug_log; open continues with a fallback
            debug_log("file_io: CZI metadata via libCZI failed (using BioFormats dims only)", _e)

        # Open the BioFormats reader OFF the main thread (setId parses every frame offset) so the
        # event loop keeps painting instead of a dead spinner. Surface the frame count (known from
        # libCZI, opened above) so the user sees the SCALE of the one-time parse — and it ticks an
        # elapsed-seconds counter, because the parse is opaque (no percentage available).
        _n_frames = None
        try:
            _n_frames = int(getattr(image.dims, 'T', 0) or 0) if image is not None else None
        except Exception:  # broad-ok: best-effort probe → fallback value; a read failure must not crash the open
            _n_frames = None
        _frames_txt = f"{_n_frames:,} frames" if _n_frames else "all frames"
        napari_show_info(f"Indexing CZI via BioFormats — one-time parse of {_frames_txt}; then it scrubs.")
        try:
            reader = self._run_with_busy_progress(
                lambda: _czibf.CziBioFormatsReader(file_path),
                "Opening CZI",
                f"Indexing {_frames_txt} via BioFormats…\n\nOne-time frame-index parse (can take a few "
                f"minutes for a large file). The window stays responsive; frames then scrub on demand.")
        except StackLoadCancelled:
            napari_show_info("CZI open cancelled.")
            return
        except Exception as _e:  # broad-ok: format-open failure surfaced to the user; the open aborts gracefully
            napari_show_warning(f"BioFormats could not open this CZI:\n{_e}")
            debug_log("file_io: BioFormats CZI open failed", _e)
            return

        n_t, n_c, H, W = reader.n_t, reader.n_c, reader.H, reader.W

        # Pin the reader's lifetime to the layers (lazy plane reads go back to it), same as IMS.
        _img_source = ImageSource(file_path=file_path)
        _img_source.retain(reader)

        for channel_idx in range(n_c):
            try:
                _ch_info = extract_channel_info(image, channel_idx, file_stem=_stem_of(file_path)) if image is not None else None
            except Exception:  # broad-ok: best-effort probe → fallback value; a read failure must not crash the open
                _ch_info = None
            if not _ch_info:
                _ch_info = {'layer_name': f'C{channel_idx}', 'bucket': 'unknown'}
            _ch_label = _ch_info.get('layer_name', f'C{channel_idx}')
            _ch_colormap = suggest_colormap(_ch_info.get('bucket', 'unknown'))

            lazy = reader.channel_stack(channel_idx)
            layer_name = f"{self.base_file_name} {_ch_label} Stack"
            # One frame for contrast; without explicit limits napari calls np.asarray() on the whole
            # lazy stack (→ __array__, which REFUSES) to auto-estimate — see the IMS path.
            try:
                _plane0 = lazy[0]
            except Exception as _pe:  # broad-ok: format/metadata read logged via debug_log; open continues with a fallback
                debug_log("file_io: CZI first-frame prefetch failed", _pe)
                _plane0 = None
            _clim = _lazy_contrast_limits(lazy, prefetched=_plane0)
            _add = dict(name=layer_name, colormap=_ch_colormap)
            if _clim is not None:
                _add['contrast_limits'] = _clim
            _layer = self.viewer.add_image(lazy, **_add)
            try:
                _layer.projection_mode = 'none'   # show the current frame, not a mean projection
            except Exception:  # broad-ok: best-effort probe → fallback value; a read failure must not crash the open
                pass
            try:
                _layer.metadata['pycat_image_source'] = _img_source
            except Exception as _e:  # broad-ok: format/metadata read logged via debug_log; open continues with a fallback
                debug_log("file_io: could not attach ImageSource to CZI layer", _e)
            napari_show_info(
                f"Lazy-loaded CZI {_ch_label}: {n_t} frames {H}×{W}px via BioFormats "
                f"(frames read on demand)")

            # Optional, non-blocking mosaic-seam QC, once per file (wire_orphans B2 — czi_seam).
            if channel_idx == 0:
                warn_seam_qc(lazy, napari_show_warning)

        self._finalise_stack_load(H, W, microns_per_pixel, list(range(n_c)),
                                  n_t, 1, file_path, source='generic')

