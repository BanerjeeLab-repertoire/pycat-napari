"""
File Input/Output Handling Module for PyCAT

This module provides functionalities for opening, processing, and saving image and mask data
in a biological image analysis setting using napari. It includes the FileIOClass, which is
designed to facilitate the interaction between the file system and the napari viewer, managing
everything from opening files to saving processed results.

The module is structured to support a variety of file formats and ensures that data is handled
efficiently, maintaining compatibility with different types of image data used in biological
research. AICS ImageIO is used for reading image data and metadata since it provides a python 
native package comparable to the Java-based Bio-Formats library.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Standard library imports
import os


# Third party imports
import numpy as np








import skimage as sk
# ── aicsimageio is GONE. Every reader construction goes through the seam. ────
#
# This import was already DEAD — `open_image()` replaced every use of it in 1.5.529, and an
# AST walk confirms `AICSImage` is referenced nowhere in this file's code.
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
from pycat.file_io.stack_openers import _StackOpenersMixin  # format openers moved out, 1.6.146
from pycat.file_io.naming import (_lazy_contrast_limits, _tiff_pixel_size_um,  # moved out 1.6.146; re-exported
                                 _ome_pixel_size_um, _lazy_backing_label)  # noqa: F401
# StackLoadCancelled moved to the typed-signal module (utils/errors.py) in 1.6.146; re-exported here so
# `raise`/`except StackLoadCancelled` below, and any caller importing it from file_io, keep working.
from pycat.utils.errors import StackLoadCancelled  # noqa: F401  (re-exported)
# NOTE: `napari.utils.notifications.show_warning` is NOT imported at module scope — it was,
# as `napari_show_warning`, but it was DEAD (every one of its call sites re-imports it locally:
# lines ~1887/1903/2143). Removing the module-scope copy trims one of this module's GUI import
# routes. It is NOT enough to make file_io.py headless-importable on its own — the PyQt5 import
# above (needed by the two QDialog subclasses defined below) and the module-scope `ui_utils`
# import (which itself pulls napari + PyQt5) remain. Full headless import of this module is the
# FileIOClass decomposition (external audit 2026-07-14 #9 / handoff §3.2), not a one-line fix.
# The reusable stack helpers are already Qt-free in stack_access.py; import them from THERE,
# not through this module.

# Local application imports
from pycat.ui.ui_utils import add_image_with_default_colormap
from pycat.utils.general_utils import dtype_conversion_func, debug_log
from pycat.utils.frame_interval import record_time_axis
from pycat.toolbox.image_processing_tools import apply_rescale_intensity
from pycat.file_io.stack_access import to_unit_float32
from pycat.file_io.multidim_io import _ZarrTZYX, _ZarrZYX


def _clean_filename_token(stem):
    """Reduce a raw acquisition filename to a short, meaningful layer token.

    Microscope filenames range from useless ('Image 3-OME TIFF-Export-01.ome') to
    information-rich-but-wrong-scope ('polyA 3 mgpmL - 1000 mM LiCl - 50mM HEPES
    pH 7p5_3_MMStack_Pos0.ome'). The layer name wants the sample IDENTITY, not the
    full acquisition string — the rich fields (concentrations, buffer, pH) belong
    in the provenance JSON, and the full filename goes in the layer tooltip.

    Cleaning:
      * strip the OME/MicroManager tail: '.ome', '_MMStack_Pos<N>', trailing '_<N>'
        run indices MicroManager appends (a user rarely opens Pos0 and Pos1 at once);
      * strip a generic export prefix like 'Image 3-OME TIFF-Export-01' → nothing
        useful, so fall through to a positional name;
      * take the leading sample token before the first concentration/parameter
        block (the part before ' - ' or a run of numbers+units), so
        'polyA 3 mgpmL - 1000 mM LiCl ...' → 'polyA'.

    Returns a cleaned token, or None if nothing meaningful survives.
    """
    import re as _re
    if not stem:
        return None
    s = str(stem).strip()

    # Drop a trailing '.ome' (case-insensitive) if it survived the extension split.
    s = _re.sub(r'\.ome$', '', s, flags=_re.IGNORECASE)
    # Strip MicroManager's _MMStack_PosN (and any trailing _N run index).
    s = _re.sub(r'_MMStack_Pos\d+.*$', '', s, flags=_re.IGNORECASE)
    s = _re.sub(r'_MMStack.*$', '', s, flags=_re.IGNORECASE)

    # Generic export names carry no sample identity → treat as empty.
    if _re.match(r'^\s*image[\s_-]*\d*[\s_-]*ome', s, flags=_re.IGNORECASE) or \
       _re.match(r'^\s*(export|snap|img|image|untitled)[\s_\-]*\d*\s*$', s, flags=_re.IGNORECASE):
        return None

    # Take the sample token before the first ' - ' parameter block (concentrations,
    # salts, buffers), which belong in provenance, not the layer name.
    head = _re.split(r'\s*-\s*', s)[0].strip()
    # If the head still starts with a clear sample word followed by a number+unit
    # (e.g. 'polyA 3 mgpmL'), keep only the leading word(s) before the first
    # numeric-with-unit token.
    m = _re.match(r'^([A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z][A-Za-z0-9]*)*?)\s+\d', head)
    if m:
        head = m.group(1).strip()

    # Trim any trailing run index the user didn't intend ('sample_3' → 'sample').
    head = _re.sub(r'[_\s]+\d+$', '', head).strip()
    # Collapse whitespace/underscores to a single separator.
    head = _re.sub(r'[\s_]+', '_', head).strip('_')

    return head or None


def derive_layer_name(base_file_name, file_path=None, channel_infos=None,
                      is_mask=False):
    """Build a meaningful layer name from channel IDENTITY and a cleaned filename.

    Precedence (highest first):
      1. Channel identity — a fluorophore/modality label from metadata OR from
         pixel-measured modality (fluorescence/brightfield/DIC/phase). This is what
         the channel actually IS, and it takes precedence over the filename.
      2. A cleaned filename token (sample identity, with MicroManager/OME cruft and
         acquisition parameters stripped — those go to the provenance JSON).
      3. The generic role word as a last resort.

    A single-channel result reads like 'polyA-Brightfield' (sample + modality). The
    full original filename is attached to the layer as a tooltip by the caller.
    """
    import os as _os
    raw_stem = base_file_name or (
        _os.path.splitext(_os.path.basename(file_path))[0] if file_path else None)
    stem = _clean_filename_token(raw_stem)

    # A confident channel label: from metadata NAME/WAVELENGTH, or from the
    # pixel-measured modality. A positional guess ('C0-Blue') is NOT identity.
    label = None
    infos = channel_infos or []
    if infos:
        ci = infos[0] if isinstance(infos, (list, tuple)) else infos
        try:
            if ci.get('source') in ('name', 'wavelength', 'pixels') and ci.get('label'):
                label = ci['label']
        except AttributeError:
            pass

    suffix = ' Mask' if is_mask else ''
    if stem and label:
        # sample + identity, e.g. 'polyA-Brightfield' — unless the stem already
        # names the modality/fluorophore.
        if label.lower() not in stem.lower():
            return f"{stem}-{label}{suffix}"
        return f"{stem}{suffix}"
    if stem:
        return f"{stem}{suffix}"
    if label:
        return f"{label}{suffix}"
    return ("Mask Layer" if is_mask else "Fluorescence Image")


class _ZarrTYX:
    """
    Thin wrapper presenting an IMS zarr array's z_full[:, c, 0, :, :] as a
    (T, Y, X) array that satisfies napari's requirements without dask.
    Suppresses the per-chunk debug prints from imaris_ims_file_reader.
    """
    def __init__(self, z, c, suppress_ctx=None):
        self._z   = z
        self._c   = c
        self._ctx = suppress_ctx or _suppress_ims_chunk_prints
        T, _, _, Y, X = z.shape
        self.shape = (T, Y, X)
        self.dtype = np.dtype('float32')
        self.ndim  = 3

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            t_idx, spatial = idx[0], idx[1:]
        else:
            t_idx, spatial = idx, (slice(None), slice(None))
        with self._ctx():
            raw = self._z[t_idx, self._c, 0]
        # `[0, 1]` from the SOURCE dtype (`self._z.dtype`) — not raw counts. See `to_unit_float32`.
        arr = to_unit_float32(raw, getattr(self._z, 'dtype', None))
        if arr.ndim == 2:
            return arr[spatial]
        return arr[(slice(None),) + spatial]

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)

    def __len__(self):
        return self.shape[0]

    # `transpose()` is deliberately ABSENT — it used to return frame 0 as (1, Y, X) for any
    # requested axes. See `_TiffPageStack` for the full reasoning.


# ── The lazy TIFF wrappers moved to `lazy_sources.py` ───────────────────────────────────────
#
# ``_TiffPageStack``, ``_LazyArraySource`` and the OME file-set helpers they depend on
# (``resolve_ome_file_set`` / ``build_ome_page_map``) were defined HERE — beside two ``QDialog``
# subclasses, in a module that imports PyQt5 at module scope. **So reaching a TIFF lazy wrapper
# dragged in the whole GUI stack**, and the wrappers could not be exercised headlessly — which is
# exactly what a perf harness or a CI perf gate wants to do. Their bodies never needed Qt; only
# their address did.
#
# ``lazy_sources.py`` is Qt/napari-free by contract (``tests/test_lazy_sources_headless.py``
# enforces it). This file re-exports the names so the existing
# ``from pycat.file_io.file_io import _TiffPageStack`` callers keep working unchanged — the same
# courtesy the ``stack_access`` re-export below already provides.
from pycat.file_io.lazy_sources import (      # noqa: F401  (re-exported for callers)
    _TiffPageStack,
    _TiffPageStackZYX,
    _TiffPageStackTZYX,
    _LazyArraySource,
    resolve_ome_file_set,
    build_ome_page_map,
)




# ── ONE implementation of the stack helpers, not two ────────────────────────────────────────
#
# ``materialize_stack``, ``iter_frames``, ``layer_is_stack``, ``extract_2d_plane`` and
# ``warn_if_assumed_axis`` were defined **in this file AND in stack_access.py** — byte-identical
# copies.
#
# **That is the dangerous state**: they agree today, so nothing catches the day they do not. And
# these are not any five functions — they are **the functions that fix the lazy-stack bug**, the
# one that has silently collapsed a movie to frame 0 four separate times. Fixing one copy and
# missing the other is exactly how that bug survives.
#
# ``stack_access.py`` is the purpose-built module (its docstring names the bug), so it owns the
# implementation. This file re-exports, so all 25 existing ``from pycat.file_io.file_io import
# materialize_stack`` call sites keep working unchanged.
from pycat.file_io.stack_access import (       # noqa: F401  (re-exported for callers)
    materialize_stack,
    iter_frames,
    layer_is_stack,
    extract_2d_plane,
    warn_if_assumed_axis,
)










# When True, the 'Object Diameter' / 'Cell Diameter' annotation layers are created
# eagerly at every file load (legacy behaviour). When False (default), they are
# created ON DEMAND by the measure widget the first time the user measures, so a
# session that never measures diameters isn't cluttered with them. Flip to True to
# revert if the on-demand path ever misbehaves (e.g. the native Home button).
# Moved to `napari_adapter`, with the layers it toggles. Re-exported so the name still
# resolves here — it was documented in this file as the one-line revert.
from pycat.file_io.napari_adapter import EAGER_DIAMETER_LAYERS  # noqa: F401




class FileIOClass(_StackOpenersMixin):
    """
    A class for handling file input/output operations related to image analysis, including
    opening images and masks, assigning channels to opened images, and saving analysis results.

    Attributes
    ----------
    viewer : napari.Viewer
        The napari viewer instance for displaying images and annotations.
    analysis_data : object
        An instance that stores analysis results and metadata.
    filePath : str
        Path of the last opened file.
    base_file_name : str
        Base name of the last opened file, used for naming saved files.

    Methods
    -------
    open_2d_image(self):
        Opens one or more 2D images for analysis, handles channel assignment and loading into the viewer.
    open_2d_mask(self):
        Opens one or more 2D masks associated with images, for segmentation or analysis purposes.
    assign_channels_in_dialog(self, all_channels, is_mask=False):
        Displays a dialog for assigning names to the channels of the opened image or mask.
    load_into_viewer(self, data, name, is_mask=False):
        Loads image or mask data into the napari viewer with appropriate settings.
    save_and_clear_all(self, viewer):
        Saves selected layers and dataframes to files and optionally clears them from the viewer and analysis data.
    determine_file_format_and_process_data(self, layer_type, data):
        Determines the appropriate file format for saving and processes the data accordingly.
    """
    def __init__(self, viewer, central_manager):
        """
        Initializes the FileIOClass with a reference to a napari viewer instance.
        """
        self.viewer = viewer
        self.analysis_data = None
        self.central_manager = central_manager
        self.filePath = ""
        self.base_file_name = ""
        # Keep every layer physically aligned: when a layer is added anywhere in
        # the app, give it the same field of view as the primary µm-scaled image
        # so masks / processed images / overlays never render at the wrong size.
        try:
            self.viewer.layers.events.inserted.connect(
                lambda e: self._align_layer_scales())
        except Exception:
            pass
        # Update the scale bar whenever the active layer selection changes.
        # This ensures that switching to an upscaled layer (whose scale is
        # source_scale / 2) shows the correct physical bar length, rather than
        # remaining frozen at the value set when the original was loaded.
        try:
            self.viewer.layers.selection.events.changed.connect(
                lambda e: self._update_scale_bar_for_active_layer())
        except Exception:
            pass

    def _align_layer_scales(self):
        from pycat.file_io.napari_adapter import _align_layer_scales
        return _align_layer_scales(self.viewer, self.central_manager)

    def open_2d_image(self, file_paths=None, clear_first=True):
        """
        Opens a dialog for selecting and opening 2D image files. Supports multiple file formats and handles multichannel 
        images by assigning channels through a dialog. The method updates the Napari viewer with the opened images and 
        integrates image metadata into the provided data instance for subsequent analysis.

        Parameters
        ----------
        file_paths : list[str] or None
            Paths to open; None opens a file dialog.
        clear_first : bool, default True
            If True, reset to the workflow start state before loading (the normal
            single-dataset behaviour). If False, ADD the loaded layers to the
            current session without clearing — used to load a missing channel of a
            split-file image, or to place a second image alongside the first for
            side-by-side comparison. Metadata/data-repository updates still apply
            to the active data class, so analyses continue to target it.

        Notes
        -----
        This method can handle different image formats including TIFF, CZI, and PNG. It automatically assigns channels 
        to multichannel images and prompts the user to confirm or adjust the assignments. Metadata and resolution 
        information are extracted and stored, which can be crucial for accurate image analysis tasks.
        """
        #print("FileIO data_instance id:", id(self.central_manager.active_data_class))
        # A QAction.triggered signal passes a `checked` bool to its slot; ignore
        # anything that isn't an actual list/tuple of paths so the menu still
        # opens the file dialog (only the drop handler passes real paths).
        if not isinstance(file_paths, (list, tuple)):
            file_paths = None
        if file_paths is None:
            options = QFileDialog.Options()
            file_paths, _ = QFileDialog.getOpenFileNames(None, "Open File(s)", "", "Image Files (*.tiff *.tif *.czi *.png);;All Files (*)", options=options)

        # Check if any files were selected
        if not file_paths: 
            return

        # Auto-clear existing layers before loading a new dataset. Loading a new
        # image while a previous one is still present causes confusing display
        # behaviour — e.g. a 300-frame image loaded over a 1000-frame one looks
        # like it failed to load when scrubbed past frame 300, because the frame
        # slider still spans the old stack and only the old layer has data there.
        # Reset to the workflow start state first, so the new dataset loads clean.
        # If there is existing work, confirm before discarding it (matching the
        # Clear button's safety prompt) so unsaved analysis isn't lost silently.
        # clear_first=False skips this (add-without-clearing).
        if clear_first and not self._auto_clear_before_load():
            return  # user declined to discard existing work

        self._last_channel_info = []  # reset per file-open to avoid accumulation
        self._last_channel_assignment = []  # reset per file-open

        all_channels = [] # Create a list to store all channels for multichannel images

        for file_path in file_paths:
            # Setting the filePath variable and base file name
            self.filePath = file_path  
            self.base_file_name = os.path.splitext(os.path.basename(file_path))[0]
            # Also stash on the data class so downstream analysis (e.g. the puncta
            # overlay PNG export) can resolve the original source folder/name.
            try:
                _dc = self.central_manager.active_data_class
                _dc.data_repository['file_path'] = file_path
                _dc.data_repository['base_file_name'] = self.base_file_name
            except Exception:
                pass

            # Read the image's channels through the extracted reader (god-class
            # decomposition #2). The reader returns the channel tuples + per-channel
            # identity + the reader object; the metadata-repository updates, the
            # user-facing fallback warning, and napari construction stay here.
            from pycat.file_io.readers.image_reader_2d import read_2d_image_channels
            _channels, _channel_info, image, _used_pil = read_2d_image_channels(file_path)

            if _used_pil:
                # PIL NumPy-2.0 fallback path: reader already produced the channel
                # tuples; emit the same user-facing warning and skip the structured
                # metadata path (no reader object available).
                if _channels:
                    all_channels.extend(_channels)
                    from napari.utils.notifications import show_warning as _warn
                    _warn(
                        f"{os.path.basename(file_path)} loaded via PIL fallback (NumPy 2.0 / tifffile conflict). "
                        "Run 'python fix_tifffile.py' to permanently fix this."
                    )
                else:
                    from napari.utils.notifications import show_warning as _warn
                    _warn(
                        f"Could not load {os.path.basename(file_path)}: NumPy 2.0 is incompatible with "
                        "the installed tifffile version. Run 'python fix_tifffile.py' to fix this permanently, "
                        "or downgrade NumPy: pip install 'numpy<2.0'"
                    )
                continue  # skip the structured-reader path below

            self.central_manager.active_data_class.update_metadata(image)

            # Pixel-size recovery. The structured reader's physical_pixel_sizes
            # can miss or choke on a file's real scale — an OME-TIFF whose baseline
            # XResolution is zeroed (0/1) makes the reader raise "division by zero"
            # and fall back to 1.0, even though the OME-XML carries the true value.
            # If update_metadata landed on the 1.0 sentinel, recover it: OME-XML
            # first (authoritative for OME-TIFF), then baseline TIFF tags.
            try:
                _dr = self.central_manager.active_data_class.data_repository
                _cur = _dr.get('microns_per_pixel_sq', 1)
                if abs(float(_cur) - 1.0) < 1e-9:
                    _rec = _ome_pixel_size_um(file_path)
                    _src = 'OME-XML'
                    if _rec is None:
                        _rec = _tiff_pixel_size_um(file_path); _src = 'TIFF tags'
                    if _rec is not None:
                        _dr['microns_per_pixel_sq'] = _rec * _rec
                        _dr['pixel_size_from_metadata'] = True
                        debug_log(f"file_io: pixel size {_rec:.6f} µm/px recovered "
                                  f"from {_src} (reader missed it)")
            except Exception as _pxe:
                debug_log("file_io: 2D pixel-size recovery failed", _pxe)

            # A 2-D image has ONE frame. Recorded OUTSIDE the metadata `try` below: if extraction
            # fails, the PREVIOUS file's frame count would otherwise still be sitting in the
            # repository, and a stale time axis is worse than an absent one.
            record_time_axis(
                self.central_manager.active_data_class.data_repository, 1)

            # Also store the normalised metadata record for the metadata widget
            # and results export.
            try:
                from pycat.file_io.metadata_extract import extract_metadata
                _md = extract_metadata(file_path, image=image)
                self.central_manager.active_data_class.data_repository['file_metadata'] = _md
            except Exception as _mde:
                debug_log("file_io: metadata extraction failed", _mde)

            all_channels.extend(_channels)

            # Store the per-channel identity the reader extracted.
            self._last_channel_info = getattr(self, '_last_channel_info', [])
            self._last_channel_info.extend(_channel_info)

        # Check if there are multiple channels to assign names
        if len(all_channels) > 1:
            self.assign_channels_in_dialog(
                all_channels,
                channel_info=getattr(self, '_last_channel_info', None)
            )
        # If only one channel, name it from the file (filename token / stem)
        # rather than a generic 'Fluorescence Image', so e.g. '..._DAPI.tif'
        # loads as 'DAPI' and two separate DAPI/GFP files are distinguishable.
        else:
            fluorescence_image = all_channels[0][0]
            _name = derive_layer_name(
                getattr(self, 'base_file_name', None), file_path,
                getattr(self, '_last_channel_info', None))
            self.load_into_viewer(fluorescence_image, name=_name)

        # Attach the FULL original filename to every layer we just loaded, as a
        # tooltip/metadata. The layer NAME is the short cleaned identity
        # (e.g. 'polyA-Brightfield'); the full acquisition filename lives here so
        # it stays discoverable (the rich concentration/buffer/pH fields go to the
        # provenance JSON, not the visible name).
        try:
            self._attach_source_filename_tooltip(file_paths)
        except Exception as _te:
            debug_log("file_io: source-filename tooltip attach failed", _te)

        # Add layers for measuring object and cell diameters to the viewer based on the image size
        self._add_diameter_annotation_layers()

        # Update the data instance with default sizes for object and cell
        # diameters. The original code used the last `channel_data` left by the
        # per-file read loop; that is the last channel across all loaded files,
        # i.e. all_channels[-1][0]. Preserve that exactly.
        _last_channel = all_channels[-1][0]
        self.central_manager.active_data_class.data_repository['object_size'] = _last_channel.shape[0] // 20
        self.central_manager.active_data_class.data_repository['cell_diameter'] = _last_channel.shape[0] // 8

        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record('open_image', {
                'file_path': self.filePath,
                'source_files': list(file_paths),
                'cell_diameter': self.central_manager.active_data_class.data_repository.get('cell_diameter', 100),
                'ball_radius': self.central_manager.active_data_class.data_repository.get('ball_radius', 50),
                'channel_assignment': getattr(self, '_last_channel_assignment', None),
            })

        # Fit the canvas to the freshly-loaded 2-D image. This path (open_2d_image
        # → load_into_viewer) previously never called the fit — only the stack
        # path (_finalise_stack_load) did — so 2-D TIFFs opened tiny and pressing
        # Home was the only way to fill the canvas. Deferred so the scale bar and
        # the diameter-annotation layer inserts (which fire scale-alignment) have
        # settled before the fit reads layer.extent.world.
        try:
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(400, lambda: self._fit_view_to_layer())
        except Exception:
            self._fit_view_to_layer()

        # Notify registered gates (e.g. the pixel-size gate) to re-evaluate now
        # that this 2-D image's metadata/scale is in the data repository. A plain
        # load does not switch the data class, so without this the gate would
        # keep its pre-load state and never appear.
        try:
            self.central_manager.notify_data_changed()
        except Exception:
            pass
        self._prompt_pixel_size_if_needed()



    def _tag_loaded_layer(self, layer, role=None, n_t=1, n_z=1, n_p=1,
                          microns_per_pixel=None, file_path=None,
                          modality=None, channel=None, provenance='raw'):
        from pycat.file_io.tagging import _tag_loaded_layer
        return _tag_loaded_layer(self.central_manager, layer, role, n_t, n_z, n_p,
                                 microns_per_pixel, file_path, modality, channel,
                                 provenance)

    def _file_has_imaging_metadata_safe(self, file_path):
        from pycat.file_io.routing import _file_has_imaging_metadata_safe
        return _file_has_imaging_metadata_safe(file_path)

    def _read_pycat_signifier(self, file_path):
        from pycat.file_io.routing import _read_pycat_signifier
        return _read_pycat_signifier(file_path)

    def _read_pycat_tags(self, file_path):
        from pycat.file_io.routing import _read_pycat_tags
        return _read_pycat_tags(file_path)

    def _apply_saved_tags_to_layer(self, layer, tag_store):
        from pycat.file_io.writers import _apply_saved_tags_to_layer
        return _apply_saved_tags_to_layer(layer, tag_store)
        # NOTE: an orphaned block used to sit here — the body of a
        # `_has_structured_metadata` method (docstring and all) that had been
        # accidentally merged into this one. It referenced `file_path`, which is
        # not a parameter of this method, so it raised NameError on EVERY tagged
        # layer load and swallowed it in its own `except Exception: return False`
        # — silent, and it also made this method return False instead of None.
        #
        # It was NOT restored, because the job it described ("decide whether we
        # must ask the user what they loaded") is already done, and done better,
        # by `_tiff_multipage_undeclared` (1.5.351): that checks the actual axis
        # LABEL rather than merely whether the reader can read some dims, which is
        # the distinction that matters (a plain multipage TIFF has dims but no
        # declared T/Z axis). Reinstating the weaker check would add a redundant
        # code path that nothing calls.

    def add_image_or_mask(self, file_path=None):
        """Add a file to the CURRENT session without clearing, routing it to the
        right layer type: Image layer for images, Labels layer for masks (so a
        previously-generated mask can be brought in for colocalization / analysis
        without re-running segmentation). Unifies the old "Open 2D Mask(s)".

        Type is resolved in priority order:
          1. PyCAT SIGNIFIER — if PyCAT saved this file, its embedded tag says
             image-vs-mask exactly (no guessing, no prompt).
          2. Otherwise, if the file has NO imaging-structure metadata AND no
             signifier, ASK the user what they loaded (image or mask).
          3. Otherwise fall back to a pixel-statistics guess (integer + few /
             consecutive label IDs → mask), offered as the default in a prompt.

        Multiple files may be selected in the dialog; each is routed independently
        (a selection can mix images and masks, so each gets its own type check /
        prompt). All are added to the current session without clearing.
        """
        if not isinstance(file_path, str):
            file_path = None
        if file_path is None:
            options = QFileDialog.Options()
            file_paths, _ = QFileDialog.getOpenFileNames(
                None, "Add Image(s) / Mask(s) (keep current)", "",
                "Image / Mask Files (*.ims *.tif *.tiff *.czi *.png *.jpg);;All Files (*)",
                options=options)
            if not file_paths:
                return
            for _fp in file_paths:
                self._add_image_or_mask_single(_fp)
            return

        self._add_image_or_mask_single(file_path)

    def _add_image_or_mask_single(self, file_path, clear_first=False):
        """Route a SINGLE file to an Image or Labels layer, classifying
        image-vs-mask (signifier → pixel-stats → prompt). clear_first controls
        whether the session is cleared first: the menu "Add" path passes False
        (keep current); the drop path passes True for the first file so a drop
        starts a fresh session like Open does.
        (Extracted so add_image_or_mask can loop over a multi-file selection.)"""
        if not file_path:
            return

        # Probe storage once here (this router runs for menu-Add and for drops);
        # delegated open_image_auto calls below pass _skip_storage_probe so the
        # few-MB probe read happens only once. May redirect to a fast local copy.
        file_path = self._warn_if_slow_storage(file_path) or file_path

        # 1. PyCAT signifier — authoritative, no prompt.
        sig = self._read_pycat_signifier(file_path)
        if sig == 'mask':
            self.open_2d_mask(file_paths=[file_path], clear_first=clear_first)
            return
        if sig == 'image':
            self._open_image_auto_single(file_path, clear_first=clear_first,
                                         _skip_storage_probe=True)
            return

        # 2/3. No signifier — classify by pixel stats for a default, and decide
        # whether we MUST ask (no imaging metadata at all).
        looks_like_mask = False
        try:
            import numpy as _np
            img = open_image(file_path)
            plane = read_plane(img, path=file_path, c=0, t=0, z=0)
            is_int = _np.issubdtype(plane.dtype, _np.integer)
            uniq = _np.unique(plane)
            n_unique = int(uniq.size)
            # A label mask has integer values that are (a) few and (b) look like
            # label IDs: contiguous from 0 (0,1,2,...,N) or binary. A grayscale
            # image — even integer-typed — has values scattered across its range,
            # so uniq won't be a contiguous 0..N run. Requiring the contiguous
            # pattern (not just "few values") avoids mis-tagging low-contrast
            # images as masks.
            if is_int and n_unique <= 256 and n_unique >= 1:
                mn = int(uniq.min()); mx = int(uniq.max())
                contiguous_from_zero = (mn == 0 and mx == n_unique - 1)
                binary = (n_unique <= 2 and mn == 0)
                if contiguous_from_zero or binary:
                    looks_like_mask = True
        except Exception as _e:
            debug_log("file_io: add_image_or_mask classification failed", _e)

        has_meta = self._file_has_imaging_metadata_safe(file_path)

        # Ask the user. When there's no imaging metadata AND no signifier we have
        # nothing to go on, so the prompt is essential; otherwise it's a
        # confirmation with the detected type pre-selected.
        as_mask = looks_like_mask
        try:
            from qtpy.QtWidgets import QMessageBox
            box = QMessageBox()
            box.setWindowTitle("Add as image or mask?")
            if not has_meta:
                lead = (f"'{os.path.basename(file_path)}' has no imaging-structure "
                        "metadata and no PyCAT signifier, so PyCAT can't tell what "
                        "it is. Please choose:")
            else:
                guess = ("looks like a LABEL MASK" if looks_like_mask
                         else "looks like an IMAGE")
                lead = f"'{os.path.basename(file_path)}' {guess}. Load as:"
            box.setText(lead + "\n\nMask → Labels layer (colocalization / analysis).\n"
                               "Image → Image layer.")
            mask_btn = box.addButton("Mask (Labels)", QMessageBox.AcceptRole)
            img_btn = box.addButton("Image", QMessageBox.RejectRole)
            box.setDefaultButton(mask_btn if looks_like_mask else img_btn)
            box.exec_()
            as_mask = (box.clickedButton() is mask_btn)
        except Exception:
            pass

        if as_mask:
            self.open_2d_mask(file_paths=[file_path], clear_first=clear_first)
        else:
            self._open_image_auto_single(file_path, clear_first=clear_first,
                                         _skip_storage_probe=True)

    def open_image_auto(self, file_path=None, clear_first=True):
        """Context-aware opener: inspect a file's dimensional structure
        (X, Y, Z, C, T, P) and route it to the right loader automatically, so
        the user doesn't have to know whether their file is "2D" or a "stack".

        Routing rule:
          - Any real Z or T axis (size > 1), or multi-position (P > 1) → open_stack
            (lazy; napari gives a slider per non-spatial axis; channels become
            separate overlaid layers; multi-position is handled by the scene
            switcher).
          - Otherwise (a single XY plane, optionally multi-channel XYC) →
            open_2d_image (channel-assignment pipeline).

        Every file is parsed for structure BEFORE loading so the decision is made
        on the real axes, not the file extension. If structure can't be read, we
        fall back to the 2D opener (which itself handles multi-channel).

        clear_first is forwarded so this can also add-without-clearing.

        Multiple files may be selected in the dialog: the first is loaded honouring
        clear_first, and each subsequent file is ADDED (clear_first=False) so the
        selection loads together instead of replacing one another.
        """
        # If no explicit path was given, open the dialog in MULTI-select mode.
        if not isinstance(file_path, str):
            file_path = None
        if file_path is None:
            options = QFileDialog.Options()
            file_paths, _ = QFileDialog.getOpenFileNames(
                None, "Open Image(s)",
                "",
                "Image Files (*.ims *.tif *.tiff *.czi *.png);;All Files (*)",
                options=options)
            if not file_paths:
                return
            for _i, _fp in enumerate(file_paths):
                # First file respects clear_first; the rest add without clearing.
                self._open_image_auto_single(
                    _fp, clear_first=(clear_first if _i == 0 else False))
            return

        # Explicit single path (programmatic call).
        self._open_image_auto_single(file_path, clear_first=clear_first)

    def _open_image_auto_single(self, file_path, clear_first=True,
                                _skip_storage_probe=False):
        """Route a SINGLE file to the correct loader by inspecting its structure.
        (Extracted so open_image_auto can loop over a multi-file selection.)"""
        if not file_path:
            return

        # Warn if this file is on slow storage / a cloud placeholder before the
        # potentially long load begins, and optionally copy it local (with a
        # progress bar) — returning the path to actually load from. Skipped when a
        # caller (e.g. _add_image_or_mask_single) has already probed/redirected.
        if not _skip_storage_probe:
            file_path = self._warn_if_slow_storage(file_path) or file_path

        ext = os.path.splitext(file_path)[1].lower()
        # IMS is always a stack format (T/C/Z), route directly.
        if ext == '.ims':
            self.open_stack(file_path=file_path, clear_first=clear_first)
            return

        n_t = n_z = n_c = n_p = 1
        parsed = False
        try:
            # ── Inspect ONCE, and CARRY the answer ──────────────────────────────
            #
            # This function reads ``.dims`` and ``.scenes`` to decide 2-D versus stack — and then
            # **used to throw all of it away.** ``open_stack`` and ``open_2d_image`` then opened
            # the file and worked it out again, each with its own subtly different rule.
            #
            # The 1.6.6 reader cache made the *re-opening* free. **It did not make the
            # re-inspection free** — on a CZI, ``.dims`` walks the subblock directory. *The cache
            # hid the design flaw rather than fixing it.*
            #
            # The structure is now stored on ``self`` and read by the loader that runs next. **One
            # inspection, one answer** — and nothing downstream can disagree with it, which would
            # be its own kind of bug and a very hard one to see.
            from pycat.file_io.image_structure import inspect_image

            img = open_image(file_path)
            _structure = inspect_image(img, file_path)
            self._pending_structure = _structure

            n_t = _structure.n_t
            n_z = _structure.n_z
            n_c = _structure.n_c
            n_p = _structure.n_scenes
            parsed = _structure.parsed
            print(f"[PyCAT open-auto] {os.path.basename(file_path)}: "
                  f"P={n_p} T={n_t} C={n_c} Z={n_z} → "
                  f"{'stack' if _structure.is_stack else '2D'}")
        except Exception as _e:
            debug_log("file_io: open_image_auto structure parse failed; "
                      "falling back to 2D loader", _e)

        # Multi-position (P>1) or any real Z/T axis → stack loader.
        if parsed and (n_t > 1 or n_z > 1 or n_p > 1):
            self.open_stack(file_path=file_path, clear_first=clear_first)
            return

        # Undeclared multipage TIFF: a TIFF whose metadata declares no T/Z/P axis
        # but which nonetheless has multiple pages (a plain writer / non-ImageJ
        # "save as TIFF" leaves the stack axis unlabelled — tifffile calls it 'Q').
        # Most microscopy platforms (Andor, Zeiss, Leica, saved-from-.h5) can emit
        # such split/stacked TIFFs, so this is common, not a FRAP quirk. The old
        # behaviour mis-routed these to the 2D loader → "loaded as individual
        # images". Now: detect the case and ASK whether it's a time-series,
        # z-stack, or genuinely separate images (with a remember-choice option).
        if ext in ('.tif', '.tiff'):
            n_pages, undeclared = self._tiff_multipage_undeclared(file_path)
            if undeclared and n_pages > 1:
                choice = self._ask_multipage_axis(file_path, n_pages)
                if choice in ('T', 'Z'):
                    # Both T and Z are 3D and load the same way; the label is
                    # recorded so downstream steps can warn if an unknown/assumed
                    # axis is used in an axis-dependent operation.
                    self.central_manager.active_data_class.data_repository['stack_axis_label'] = choice
                    self.central_manager.active_data_class.data_repository['stack_axis_assumed'] = True
                    self.open_stack(file_path=file_path, clear_first=clear_first)
                    return
                elif choice == 'separate':
                    self.open_2d_image(file_paths=[file_path],
                                       clear_first=clear_first)
                    return
                # choice is None (dialog failed) → fall through to 2D as before.

        self.open_2d_image(file_paths=[file_path], clear_first=clear_first)

    def _tiff_multipage_undeclared(self, file_path):
        from pycat.file_io.routing import _tiff_multipage_undeclared
        return _tiff_multipage_undeclared(file_path)

    def _ask_multipage_axis(self, file_path, n_pages):
        from pycat.file_io.dialogs import _ask_multipage_axis
        return _ask_multipage_axis(file_path, n_pages)

    def _warn_if_slow_storage(self, file_path):
        """Probe where a file lives and, if it is on slow storage (network share,
        slow external drive) or a cloud online-only placeholder, warn the user
        that loading may take a while — and OFFER to copy it to fast local storage
        first (with a progress bar). Returns the path to load from: the original
        path, or a local copy if the user accepted the copy. Callers should load
        from the returned path.

        The warning is shown ONLY when the storage is genuinely slow. Fast storage
        stays silent and the original path is returned unchanged.
        """
        try:
            from pycat.file_io.storage_probe import probe_path
        except Exception:
            return file_path
        try:
            verdict = probe_path(file_path)
        except Exception:
            return file_path
        if verdict is None or not verdict.message:
            return file_path
        if not (verdict.slow or verdict.needs_download):
            return file_path

        # Persistent-ish notice: napari warning + terminal line so it is visible
        # in the notification area and the log while the load runs.
        try:
            from napari.utils.notifications import show_warning
            show_warning("PyCAT: " + verdict.message)
        except Exception:
            pass
        print(f"[PyCAT storage] {verdict!r} :: {verdict.message}")

        # Offer to copy to fast local storage first. Skipped if the user chose
        # "always/never" earlier this session.
        pref = getattr(self, '_copy_to_local_pref', None)  # None | 'always' | 'never'
        if pref == 'never':
            return file_path
        if pref != 'always':
            decision = self._ask_copy_to_local(file_path, verdict)
            if decision in ('never', 'no'):
                if decision == 'never':
                    self._copy_to_local_pref = 'never'
                return file_path
            if decision == 'always':
                self._copy_to_local_pref = 'always'
            # decision in ('yes','always') → proceed to copy
        local = self._copy_to_local_with_progress(file_path, verdict)
        return local or file_path

    def _ask_copy_to_local(self, file_path, verdict):
        from pycat.file_io.dialogs import _ask_copy_to_local
        return _ask_copy_to_local(file_path, verdict)

    def _copy_to_local_with_progress(self, file_path, verdict):
        from pycat.file_io.dialogs import _copy_to_local_with_progress
        return _copy_to_local_with_progress(file_path, verdict)

    def open_stack(self, file_path=None, clear_first=True):
        """
        Open any supported multi-frame image file as a lazy (T, Y, X) or
        (Z, Y, X) stack — one layer per channel — without loading the full
        array into memory.

        Parameters
        ----------
        file_path : str or None
            Path to open; None opens a file dialog.
        clear_first : bool, default True
            If True, reset to the workflow start state before loading (normal
            single-dataset behaviour). If False, ADD the loaded layers to the
            current session without clearing (side-by-side comparison / loading
            an additional channel). See open_2d_image for the rationale.

        Supported formats
        -----------------
        .ims          Andor/Bitplane Imaris — opened via imaris-ims-file-reader
                      as a zarr store; truly zero-copy lazy reads per chunk.
        .tif/.tiff    Multi-frame TIFF — opened via tifffile into a numpy
                      memmap, then wrapped in the same _ZarrTYX interface so
                      napari reads one frame at a time from the memory-mapped
                      file rather than holding the whole stack in RAM.
        .czi          Zeiss CZI — opened via the reader seam; frames loaded one at a
                      time into a temporary zarr store on disk (same pattern as
                      the preprocessing pipeline).

        All formats
        -----------
        - Channel metadata (fluorophore name, emission wavelength) is extracted
          from file metadata where available and used to name layers and assign
          colormaps.  Falls back to position-based defaults (DAPI/green/red/…).
        - Physical pixel size is read from metadata and stored in
          data_repository['microns_per_pixel_sq'] where available.
        - Each channel becomes its own named napari layer.
        - The time/Z slider is preserved after loading.
        """
        # A QAction.triggered signal passes a `checked` bool to its slot; ignore
        # anything that isn't a real path string so the menu still opens the
        # dialog (only the drop handler passes a real path).
        if not isinstance(file_path, str):
            file_path = None
        if file_path is None:
            options = QFileDialog.Options()
            file_path, _ = QFileDialog.getOpenFileName(
                None, "Open Image Stack",
                "",
                "Image Stacks (*.ims *.tif *.tiff *.czi);;All Files (*)",
                options=options,
            )
        if not file_path:
            return

        # Reset to the workflow start state before loading a new stack (same as
        # the 2-D loader). Prevents the confusing overlap where a new stack loads
        # over an existing one with a different frame count. Confirms first if
        # there is existing work. clear_first=False skips this (add-without-clear).
        if clear_first and not self._auto_clear_before_load():
            return  # user declined to discard existing work

        self.filePath      = file_path
        self.base_file_name = os.path.splitext(os.path.basename(file_path))[0]
        ext = os.path.splitext(file_path)[1].lower()

        from napari.utils.notifications import show_info as napari_show_info

        try:
            if ext == '.ims':
                self._open_stack_ims(file_path)
            else:
                self._open_stack_generic(file_path, ext)
        except Exception as e:
            import traceback
            from napari.utils.notifications import show_warning as napari_show_warning
            napari_show_warning(f"Failed to open stack: {e}")
            print(f"[PyCAT Stack] Error:\n{traceback.format_exc()}")


    # ── IMS back-end ────────────────────────────────────────────────────────

    _CZI_OFFTHREAD_BYTES = 256 * 1024 * 1024


    def _add_lazy_stack_layer(self, wrapper, layer_name, colormap, retain_refs, warnings, info_msg):
        """Shared tail for the generic loader's lazy branches (decomposition #5c).

        Every lazy branch (tifffile-fallback, time series, z-stack, T-Z) built a wrapper and then did
        the SAME six things. They live here now, once:

        1. pin the branch's retained refs (readers + dask arrays) to the layer-scoped ImageSource so
           on-demand reads keep working for the layer's life;
        2. surface any builder warnings (e.g. a multi-file OME-TIFF with missing companions);
        3. **PIN CONTRAST from the first frame** — without explicit limits napari auto-estimates by
           calling ``np.asarray()`` on the whole lazy wrapper (``__array__``), which on a lazy source
           either loads every frame off disk or (post-1.6.4) raises. One frame is cheap;
        4. ``add_image``;
        5. force per-frame display (``projection_mode='none'``), not a mean projection that averages
           the time-series to a flat/black image;
        6. announce the load.
        """
        from napari.utils.notifications import show_info as _si
        from napari.utils.notifications import show_warning as _sw
        # Pin the branch's reader/dask handles to the layer-scoped ImageSource — the SOLE owner of
        # retention now (self._stack_lazy_refs is gone). retain() dedups by identity.
        _src = self._current_stack_img_source
        for _r in (retain_refs or []):
            _src.retain(_r)
        for _w in (warnings or []):
            _sw(_w)
        _add_kwargs = {'name': layer_name, 'colormap': colormap}
        _clim = _lazy_contrast_limits(wrapper)
        if _clim is not None:
            _add_kwargs['contrast_limits'] = _clim
        _layer = self.viewer.add_image(wrapper, **_add_kwargs)
        try:
            _layer.projection_mode = 'none'
        except Exception:
            pass
        # Lifetime = layer lifetime: attach the ImageSource so the reader survives GC of the
        # controller (the retention guard asserts this on every lazy generic layer).
        try:
            _layer.metadata['pycat_image_source'] = _src
        except Exception as _e:
            debug_log("file_io: could not attach ImageSource to generic stack layer", _e)
        # Record WHICH position this layer holds (multi-scene files only; None is a no-op), so results
        # and exports carry the scene and the switcher can identify and re-tag it.
        try:
            from pycat.file_io.scenes import tag_scene_layer
            tag_scene_layer(_layer, getattr(self, '_current_scene', None))
        except Exception as _se:
            debug_log("file_io: could not tag the layer with its scene", _se)
        if info_msg:
            _si(info_msg)
        return _layer


    def _run_with_busy_progress(self, fn, title, text, cancellable=True):
        """Run blocking ``fn()`` OFF the Qt thread behind a modal busy dialog; return its result (or
        re-raise). Raises :class:`StackLoadCancelled` on "Give up". Headless → plain sync call.

        Two things the naive version got wrong (both seen opening a streaming CZI): the dialog must
        CLOSE when the work finishes — the finish handler is a main-thread ``QObject`` slot ending a
        ``QEventLoop``, not a worker-thread plain function whose ``dlg.reset()`` never returns the
        modal loop; and "Give up" must FREE the UI — the JVM call can't be interrupted, so cancel
        detaches (drops the orphan's result) rather than ``thread.wait()`` blocking (the X-out hang).
        """
        try:
            from PyQt5.QtCore import (QThread, QObject, pyqtSignal, pyqtSlot, Qt,
                                      QTimer, QEventLoop)
            from PyQt5.QtWidgets import QProgressDialog
        except Exception:
            return fn()

        box = {}

        class _Worker(QObject):
            finished = pyqtSignal()

            def run(self):
                try:
                    box['value'] = fn()
                except BaseException as e:   # reported back to the caller's thread
                    box['error'] = e
                finally:
                    self.finished.emit()

        thread = QThread()
        worker = _Worker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        parent = None
        try:
            _win = getattr(self.viewer, 'window', None)
            parent = getattr(_win, '_qt_window', None)
        except Exception:
            parent = None

        # (min, max) = (0, 0) → indeterminate/busy bar. A "Give up" button lets the user abandon a
        # long parse; label None removes it.
        dlg = QProgressDialog(text, "Give up" if cancellable else None, 0, 0, parent)
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)

        loop = QEventLoop()
        _secs = [0]
        _state = {'cancelled': False}

        # Elapsed-seconds counter (main thread): the work is opaque (no percentage), so a counting-up
        # "…Ns" is what tells the user it is working, not hung.
        def _tick():
            _secs[0] += 1
            try:
                dlg.setLabelText(f"{text}\n\n… {_secs[0]}s elapsed")
            except Exception:
                pass
        _timer = QTimer()
        _timer.setInterval(1000)
        _timer.timeout.connect(_tick)

        class _Bridge(QObject):
            @pyqtSlot()
            def on_finished(self):          # runs on the MAIN thread (queued) — closes the dialog
                _state['done'] = True
                _timer.stop()
                thread.quit()
                if loop.isRunning():
                    loop.quit()
        bridge = _Bridge()
        worker.finished.connect(bridge.on_finished)

        def _on_cancel():
            # `QProgressDialog.close()` (below, on NORMAL completion) also emits `canceled` — ignore
            # that, or every successful open would report itself cancelled. Only a real Give-up click,
            # before the work finishes, counts.
            if _state.get('done'):
                return
            _state['cancelled'] = True
            _timer.stop()
            if loop.isRunning():
                loop.quit()
        if cancellable:
            dlg.canceled.connect(_on_cancel)

        _timer.start()
        thread.start()
        dlg.show()
        loop.exec_()                 # nested loop; the window keeps painting until quit()
        dlg.close()

        if _state['cancelled']:
            # Detach: keep the thread + its main-thread bridge alive (a QThread GC'd mid-run crashes)
            # until the blocking call returns. bridge.on_finished then quits the thread; thread.finished
            # drops the references. The result is discarded, the UI is free NOW.
            orphans = getattr(FileIOClass, '_orphan_load_threads', None)
            if orphans is None:
                orphans = FileIOClass._orphan_load_threads = []
            entry = (thread, worker, bridge)
            orphans.append(entry)
            thread.finished.connect(lambda e=entry: e in orphans and orphans.remove(e))
            raise StackLoadCancelled()

        thread.wait()
        if 'error' in box:
            raise box['error']
        return box.get('value')

    # ── Shared post-load logic ───────────────────────────────────────────────

    def _fit_view_to_layer(self, layer=None, margin=0.9, attempt=0):
        from pycat.file_io.napari_adapter import _fit_view_to_layer
        return _fit_view_to_layer(self.viewer, self.central_manager, layer, margin, attempt)

    def _finalise_stack_load(self, H, W, microns_per_pixel, channels_to_load,
                              n_t, n_z, file_path, source='generic'):
        from pycat.file_io.stack_load import _finalise_stack_load
        return _finalise_stack_load(self.viewer, self.central_manager, H, W,
                                    microns_per_pixel, channels_to_load, n_t, n_z,
                                    file_path, source)

    def open_2d_mask(self, file_paths=None, clear_first=False):
        """
        Opens a dialog for selecting and opening mask files. This method is similar to `open_2d_image` but is specifically 
        tailored for mask files, supporting operations such as assigning channels to masks if the mask file contains 
        multiple channels. Masks load as napari Labels layers (via load_into_viewer(is_mask=True)).

        Parameters
        ----------
        file_paths : list[str] or None
            Paths to open; None opens a file dialog.
        clear_first : bool, default False
            Masks default to ADD-without-clearing (their purpose is to bring a
            previously-generated mask into a session that already holds the image,
            e.g. for colocalization without re-analysis). Pass True to reset first.

        Notes
        -----
        The method supports a variety of file formats for masks, including TIFF, PNG, and JPG. It handles multichannel 
        masks by offering a dialog to assign specific channel roles, aiding in precise segmentation tasks.
        """
        if not isinstance(file_paths, (list, tuple)):
            file_paths = None
        if clear_first and not self._auto_clear_before_load():
            return
        if file_paths is None:
            options = QFileDialog.Options()
            file_paths, _ = QFileDialog.getOpenFileNames(None, "Open File(s)", "", "Mask Files (*.tiff *.tif *.png *.jpg);;All Files (*)", options=options)

        # Check if any files were selected
        if not file_paths:
            return

        all_channels = [] # Create a list to store all channels for multichannel masks

        for file_path in file_paths:
            # Setting the filePath variable and base file name
            self.filePath = file_path  
            self.base_file_name = os.path.splitext(os.path.basename(file_path))[0]
            # Also stash on the data class so downstream analysis (e.g. the puncta
            # overlay PNG export) can resolve the original source folder/name.
            try:
                _dc = self.central_manager.active_data_class
                _dc.data_repository['file_path'] = file_path
                _dc.data_repository['base_file_name'] = self.base_file_name
            except Exception:
                pass 

            # Read the mask's channels through the extracted pure reader (god-class
            # decomposition piece #1 — see readers/mask_reader.py). Same tuples, same order.
            all_channels.extend(read_2d_mask_channels(file_path))

        # Check if there are multiple channels to assign names
        if len(all_channels) > 1:
            self.assign_channels_in_dialog(all_channels, is_mask=True)
        # If only one channel, name the mask from the file rather than a bare
        # 'Mask Layer', so a mask keeps the identity of the file it came from.
        else:
            mask_image = all_channels[0][0]
            _mask_name = derive_layer_name(
                getattr(self, 'base_file_name', None), file_path, is_mask=True)
            self.load_into_viewer(mask_image, name=_mask_name, is_mask=True)

        
    def _channels_all_confident(self, channel_info):
        """True when every channel has a confident identity (metadata name /
        wavelength, or a pixel-measured modality) — i.e. no channel is a bare
        positional guess. Used to skip the naming dialog when it would only be
        confirming names PyCAT is already sure of."""
        if not channel_info:
            return False
        try:
            for ci in channel_info:
                if not ci or ci.get('source') not in ('name', 'wavelength', 'pixels'):
                    return False
            return True
        except Exception:
            return False

    def assign_channels_in_dialog(self, all_channels, is_mask=False, channel_info=None):
        """
        Assign names to each channel of an opened image or mask.

        When every channel already has a CONFIDENT identity (a fluorophore/emission
        label from metadata, or a modality measured from the pixels), the naming
        dialog is SKIPPED and those names are applied directly — the dialog would
        only be asking the user to confirm names PyCAT is already sure of. The
        dialog still appears when at least one channel is ambiguous (a bare
        positional guess), so the user can disambiguate.

        Parameters
        ----------
        all_channels : list
            Tuples of (channel data, file path, channel number).
        is_mask : bool, optional
            Whether the channels belong to a mask (default False).
        channel_info : list, optional
            Per-channel identity dicts from identify_channel (carries 'source').
        """
        # Confidence gate: skip the dialog when nothing is ambiguous (images only;
        # masks keep the dialog since they have no measurable modality identity).
        _auto = (not is_mask) and self._channels_all_confident(channel_info)

        if _auto:
            # Derive each channel's name from its confident identity — no dialog.
            channel_names = []
            for i, (channel_data, file_path, channel_num) in enumerate(all_channels):
                info = channel_info[channel_num] if channel_info and channel_num < len(channel_info) else None
                channel_names.append(
                    derive_layer_name(
                        getattr(self, 'base_file_name', None), file_path,
                        channel_infos=[info] if info else None, is_mask=is_mask))
            _designated_condensate = None
        else:
            dialog = ChannelAssignmentDialog(all_channels, is_mask=is_mask, channel_info=channel_info)
            result = dialog.exec_()

            if result == QDialog.Accepted:
                # Get the names assigned by the user
                channel_names = [input_field.text() for input_field in dialog.channel_name_inputs]
            elif result == QDialog.Rejected:
                return # If the user cancels the dialog do nothing

            # Read the opt-in condensate-channel designation (if the dialog offered it) and
            # PERSIST it for this acquisition layout, so future same-layout files recall it.
            _designated_condensate = None
            try:
                dd = getattr(dialog, '_condensate_dd', None)
                if dd is not None:
                    chosen = dd.currentData()
                    if isinstance(chosen, int) and chosen >= 0:
                        _designated_condensate = chosen
                        from pycat.utils.channel_designations import remember_designation
                        remember_designation(channel_info, chosen)
            except Exception:
                pass

        # Record the final channel_num -> layer_name assignment so batch
        # replay can recreate the exact same image-type-to-channel mapping.
        # Stored on self so open_image()'s bp.record call can include it.
        self._last_channel_assignment = []

        # Load each channel into the viewer with the assigned name
        # Recall any persisted "which channel is the condensate" designation for THIS
        # acquisition layout (opt-in memory; None when nothing is remembered — we never guess).
        # A designation the user made in THIS dialog wins over the recalled one.
        try:
            from pycat.utils.channel_designations import recall_designation
            _condensate_idx = recall_designation(channel_info) if (channel_info and not is_mask) else None
        except Exception:
            _condensate_idx = None
        if _designated_condensate is not None:
            _condensate_idx = _designated_condensate

        for i, (channel_data, file_path, channel_num) in enumerate(all_channels):
            name = channel_names[i]
            if not name:  # Use default naming if input is empty
                if not is_mask:
                    if channel_num == 0:
                        name = "Fluorescence Image"
                    elif channel_num == 1:
                        name = "Segmentation Image"
                    else:
                        name = f"{os.path.basename(file_path)}_ch_{channel_num}"
                else:
                    name = f"Mask Layer {channel_num}"

            # Capture detected identity (if any) alongside the final name
            info = channel_info[channel_num] if channel_info and channel_num < len(channel_info) else None
            self._last_channel_assignment.append({
                'channel_num': channel_num,
                'layer_name': name,
                'source_path': file_path,
                'source_stem': os.path.splitext(os.path.basename(file_path))[0],
                'source_suffix': os.path.splitext(file_path)[1].lower(),
                'detected_label': info.get('label') if info else None,
                'detected_source': info.get('source') if info else None,
            })

            self.load_into_viewer(channel_data, name=name, is_mask=is_mask)

            # Tag the channel's IDENTITY on the layer so downstream selection can query tags
            # instead of relying on load order (which is what made DAPI and the condensate
            # channel indistinguishable when both were named "Fluorescence Image"). This is the
            # keystone of the tag migration for the fluorescence pipeline.
            if not is_mask:
                try:
                    self._tag_channel_identity(info, channel_num,
                                               is_condensate=(_condensate_idx == channel_num))
                except Exception:
                    pass

    def _tag_channel_identity(self, info, channel_num, is_condensate=False):
        """Attach channel-identity tags to the just-loaded layer (the last-added image layer).

        Tags written:
          * ``channel``          -- the detected fluorophore/label (DAPI, EGFP, Ch0, ...)
          * ``spectral_bucket``  -- blue/green/red/far_red/unknown, the honest DAPI-vs-GFP discriminator
          * ``target=condensate`` -- ONLY when a persisted designation says this channel index is the
            condensate one (opt-in memory). Never inferred otherwise.

        Identity tags use source='metadata' when the info came from real metadata, else 'inferred'.
        The condensate designation is source='user_set' (it originated from an explicit user choice),
        so it LOCKS the key and won't be clobbered by later inference.
        """
        try:
            from pycat.utils.layer_tags import tag_layer
        except Exception:
            return
        # The channel just loaded is the most-recently-added image layer.
        layer = None
        try:
            for lyr in reversed(list(self.viewer.layers)):
                if lyr.__class__.__name__ == 'Image':
                    layer = lyr
                    break
        except Exception:
            layer = None
        if layer is None:
            return

        label = (info or {}).get('label')
        bucket = (info or {}).get('bucket')
        src = 'metadata' if (info or {}).get('source') not in (None, 'position') else 'inferred'
        if label:
            tag_layer(layer, 'channel', str(label), source=src)
        if bucket:
            tag_layer(layer, 'spectral_bucket', str(bucket), source=src)
        if is_condensate:
            tag_layer(layer, 'target', 'condensate', source='user_set', overwrite=True)
    

    def _add_diameter_annotation_layers(self):
        from pycat.file_io.napari_adapter import _add_diameter_annotation_layers
        return _add_diameter_annotation_layers(self.viewer)

    def _enable_auto_scale_bar(self, image_layer=None):
        from pycat.file_io.napari_adapter import _enable_auto_scale_bar
        return _enable_auto_scale_bar(self.viewer, self.central_manager, image_layer)

    def _update_scale_bar_for_active_layer(self):
        from pycat.file_io.napari_adapter import _update_scale_bar_for_active_layer
        return _update_scale_bar_for_active_layer(self.viewer, self.central_manager)

    def load_into_viewer(self, data, name, is_mask=False):
        from pycat.file_io.viewer_load import load_into_viewer
        return load_into_viewer(self.viewer, self.central_manager, data, name, is_mask)

    def _attach_source_filename_tooltip(self, file_paths):
        """Stamp the full original filename onto layers loaded from this open, so
        the rich acquisition name (which the short layer name deliberately drops)
        stays discoverable. Stored in layer.metadata['source_filename'] and, where
        the napari build supports it, as a layer tooltip. Only stamps layers that
        don't already carry a source_filename (so re-opens don't clobber)."""
        import os as _os
        names = [_os.path.basename(p) for p in (file_paths or []) if p]
        full = names[-1] if names else None
        if not full:
            return
        try:
            import napari.layers as _nl
        except Exception:
            _nl = None
        for _l in list(self.viewer.layers):
            try:
                if _nl is not None and not isinstance(_l, (_nl.Image, _nl.Labels)):
                    continue
                md = getattr(_l, 'metadata', None)
                if not isinstance(md, dict):
                    continue
                if md.get('source_filename'):
                    continue
                md['source_filename'] = full
                # napari layers expose no universal tooltip, but many builds
                # honour a 'help' string; set it best-effort so hovering shows it.
                try:
                    _l.help = full
                except Exception:
                    pass
            except Exception:
                continue



    def _prompt_pixel_size_if_needed(self):
        from pycat.file_io.tagging import _prompt_pixel_size_if_needed
        return _prompt_pixel_size_if_needed(self.central_manager)

    def _auto_clear_before_load(self):
        from pycat.file_io.session import _auto_clear_before_load
        return _auto_clear_before_load(self.viewer, self.central_manager)

    def _clear_everything(self, viewer):
        from pycat.file_io.session import _clear_everything
        return _clear_everything(viewer, self.central_manager)

    def clear_all_without_saving(self, viewer, confirm=True):
        from pycat.file_io.session import clear_all_without_saving
        return clear_all_without_saving(viewer, self.central_manager, confirm)

    def save_and_clear_all(self, viewer):
        """
        Provides options for saving selected layers and dataframes based on user input from a dialog, with additional 
        options for naming files and deciding whether to clear saved data from both the viewer and the repository.

        Parameters
        ----------
        viewer : object
            The Napari viewer object containing the layers and data to be managed.

        Notes
        -----
        This method presents a dialog to the user for selecting which layers and dataframes to save and whether to clear 
        these items from the viewer and repository after saving. It supports flexible file naming and formats, ensuring 
        data is preserved in a user-specified manner.
        """
        self.viewer = viewer
        # Get layer names and dataframe names from the viewer and analysis data abd present them to the user
        dataframe_names = self.central_manager.active_data_class.get_dataframes().keys()
        dialog = LayerDataframeSelectionDialog(self.viewer.layers, dataframe_names)
        result = dialog.exec_()

        # If the user chose "Clear Without Saving", discard everything now.
        if result == QDialog.Accepted and getattr(dialog, 'clear_without_saving', False):
            self._clear_everything(viewer)
            print("[PyCAT] Cleared all layers and data without saving.")
            return

        # If user clicks OK, proceed with saving and clearing
        if result == QDialog.Accepted:
            selected_layers, selected_dataframes, clear_all = dialog.get_selections()
        # If user cancels the dialog, return without saving or clearing
        elif result == QDialog.Rejected:
            return

        # Present a file dialog for saving the selected layers and dataframes, get the save path and base name
        options = QFileDialog.Options()
        default_file_name = os.path.join(os.path.dirname(self.filePath), self.base_file_name + "_placeholder_name")
        save_file_path, _ = QFileDialog.getSaveFileName(None, "Save Files", default_file_name, "All Files (*)", options=options)

        # If the user cancels the save dialog, return without saving or clearing
        if not save_file_path:
            return
        
        # Check if the user has changed the base file name
        user_provided_base_name = os.path.splitext(os.path.basename(save_file_path))[0]
        default_base_name = os.path.splitext(os.path.basename(default_file_name))[0]

        if user_provided_base_name != default_base_name:
            #save_name = os.path.dirname(save_file_path) + os.sep + user_provided_base_name
            save_name = os.path.join(os.path.dirname(save_file_path), user_provided_base_name)
        else:
            #save_name = os.path.dirname(save_file_path) + os.sep + self.base_file_name
            save_name = os.path.join(os.path.dirname(save_file_path), self.base_file_name)

        # Record the save selections now that we have the full picture
        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record('save_and_clear', {
                'save_path': save_name,
                'saved_layers': list(selected_layers),
                'saved_dataframes': list(selected_dataframes),
                'clear_all': clear_all,
            })

        # ── Consolidate into ONE session folder (not scattered loose files) ──
        #
        # Files used to be written with a flat `save_name` prefix straight into
        # the chosen directory, so a session's artifacts scattered among the
        # user's data files. Instead, gather them into a dedicated session folder
        # and record a manifest, so the top-level "Load Session" can restore the
        # whole working state (source image referenced by path, derived layers +
        # dataframes reloaded). The user's chosen name/location is honoured as the
        # PARENT; the session folder is created inside it.
        from pycat.file_io import session_manifest as _sm
        _parent_dir = os.path.dirname(save_name)
        _stem = os.path.basename(save_name)
        try:
            _session_dir = _sm.default_session_dir(_parent_dir, self.base_file_name or _stem)
            _session_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            _session_dir = None
        # Inside the session folder, keep the stem-based naming the loader expects.
        _base_in_session = (str(_session_dir / (self.base_file_name or _stem))
                            if _session_dir is not None else save_name)
        save_name = _base_in_session

        # Get the names of all layers in the viewer (needed by the clear logic
        # below, whichever branch runs).
        layer_names = [layer.name for layer in self.viewer.layers]

        # Do the actual file writes in the pure, Qt-free writer. It takes the
        # already-decided inputs (which layers/dataframes, the final in-session
        # save_name, the created session dir) and writes the layer files, the
        # dataframe CSVs, the metadata JSON, and the session manifest.
        _dataframes = self.central_manager.active_data_class.get_dataframes()
        clear_dfs_list = list(_dataframes.keys())
        _file_metadata = self.central_manager.active_data_class.data_repository.get('file_metadata')
        from pycat.file_io.writers import write_session_outputs
        write_session_outputs(
            self.central_manager,
            {layer.name: layer for layer in self.viewer.layers},
            selected_layers,
            selected_dataframes,
            _dataframes,
            _file_metadata,
            save_name,
            _session_dir,
            getattr(self, 'filePath', None),
            self.base_file_name or _stem)

        # Clear all layers and dataframes from the viewer and data instance.
        # If "Remember measurements across clears" is on, preserve the measured
        # sizes so the user doesn't need to re-measure for a second image.
        if clear_all:
            _persist = getattr(self.central_manager, 'persist_measurements', False)
            _dr = self.central_manager.active_data_class.data_repository
            _saved = {}
            if _persist:
                _saved = {k: _dr.get(k) for k in
                          ('ball_radius', 'object_size', 'cell_diameter')
                          if _dr.get(k) is not None}
            self.viewer.layers.select_all()
            self.viewer.layers.remove_selected()
            self.central_manager.active_data_class.reset_values(
                clear_all=True, df_names_to_reset=clear_dfs_list)
            if _persist and _saved:
                _dr2 = self.central_manager.active_data_class.data_repository
                for k, v in _saved.items():
                    try:
                        _dr2[k] = v
                    except Exception:
                        pass
        # Clear only the saved layers and dataframes
        else:
            for layer_name in selected_layers:
                if layer_name in layer_names:
                    self.viewer.layers.remove(layer_name)
            self.central_manager.active_data_class.reset_values(df_names_to_reset=selected_dataframes)

        # Save/Clear is a hard boundary between datasets. Reset the workflow UI
        # and the in-memory batch recorder so subsequent operations start a new
        # process instead of being appended to the previous saved dataset.
        try:
            wc = getattr(self.central_manager, 'workflow_checklist', None)
            if wc is not None:
                wc.reset()
        except Exception:
            pass
        try:
            bp = getattr(self.central_manager, '_pycat_batch_processor', None)
            if bp is not None:
                # Save/Clear ends this dataset's recording. If there are unsaved
                # recorded steps, offer to export the batch config first (unless
                # the user silenced the prompt for this session), so the workflow
                # isn't lost when the recorder resets.
                if (bp.has_unsaved_steps()
                        and not getattr(bp, '_export_prompt_silenced', False)):
                    box = QMessageBox(self.viewer.window._qt_window
                                      if hasattr(self.viewer.window, '_qt_window') else None)
                    box.setIcon(QMessageBox.Question)
                    box.setWindowTitle("Export batch config?")
                    box.setText(
                        "This batch workflow recording hasn't been saved.\n\n"
                        "Save-and-Clear ends the current recording. Export the "
                        "batch config now so you can replay this workflow later?")
                    box.setStandardButtons(QMessageBox.Save | QMessageBox.Discard)
                    box.setDefaultButton(QMessageBox.Save)
                    _dont_ask = QCheckBox("Don't ask again this session")
                    box.setCheckBox(_dont_ask)
                    choice = box.exec_()
                    if _dont_ask.isChecked():
                        bp._export_prompt_silenced = True
                    if choice == QMessageBox.Save:
                        from pathlib import Path as _Path
                        path, _ = QFileDialog.getSaveFileName(
                            None, "Save Batch Config", "", "JSON (*.json)")
                        if path:
                            bp.save_config(_Path(path))
                bp.terminate_recording()
        except Exception:
            pass

    def _save_layer(self, data, layer_type: str, save_name: str, safe_name: str,
                    tag_store=None):
        from pycat.file_io.writers import _save_layer
        return _save_layer(self.central_manager, data, layer_type, save_name, safe_name,
                           tag_store)

    def determine_file_format_and_process_data(self, layer_type, data):
        from pycat.file_io.viewer_load import determine_file_format_and_process_data
        return determine_file_format_and_process_data(self.viewer, self.central_manager,
                                                      layer_type, data)
        
