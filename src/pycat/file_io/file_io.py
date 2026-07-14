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
import sys
import warnings
import contextlib
import io


@contextlib.contextmanager
def _suppress_ims_chunk_prints():
    """
    The imaris_ims_file_reader package prints a 'GET : <key>' debug line
    plus chunk slice/shape info to stdout on every single zarr chunk read.
    Since our lazy IMS loading reads chunks on-demand as napari displays
    frames, this floods the terminal with dozens of lines per frame.
    This context manager redirects stdout to a null sink for the duration
    of any IMS read operation, since the package offers no verbosity flag.
    """
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = old_stdout

# Third party imports
import numpy as np


def _ims_indices(selector, size):
    """Return concrete indices for an int/slice/list selector against an IMS axis."""
    if isinstance(selector, slice):
        return list(range(*selector.indices(size)))
    if selector is Ellipsis or selector is None:
        return list(range(size))
    if isinstance(selector, (list, tuple, np.ndarray)):
        return [int(i) for i in selector]
    return [int(selector)]


def _ims_frame_2d(raw):
    """Normalize imaris_ims_file_reader output to exactly (Y, X).

    With squeeze_output=False, direct IMS reads may retain singleton T/C/Z axes
    even when indexed with integers. Napari expects a 2-D plane after slicing a
    (T, Y, X) layer, so leaving those singleton axes in place causes
    ValueError: axes don't match array during napari transpose.
    """
    arr = np.asarray(raw).astype(np.float32, copy=False)
    arr = np.squeeze(arr)
    if arr.ndim != 2:
        raise ValueError(f"Expected IMS plane to reduce to 2-D (Y, X), got shape {arr.shape}")
    return arr


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


def _ims_pixel_size_um(reader, width_px):
    """Read physical pixel size (um/px) from an IMS file's spatial extents.

    Imaris .ims files store the physical bounding box as DataSetInfo/Image
    attributes ExtMin0/ExtMax0 (X), ExtMin1/ExtMax1 (Y), ExtMin2/ExtMax2 (Z),
    each as a FIXED-LENGTH ASCII CHAR ARRAY (e.g. b'-42107.8'). Pixel size is
    (ExtMax0 - ExtMin0) / width. The values can be negative (stage coordinates),
    which is why a naive parse can fail -- we decode the char array to a string
    and float() it explicitly.

    Prefers reading the h5py handle directly (reader.hf) because the reader's
    own accessor name and behaviour vary across imaris_ims_file_reader versions
    and it silently mishandles some char-array attributes.

    Returns um/px as a float, or None if the extents can't be read.
    """
    def _to_float(raw):
        if raw is None:
            return None
        try:
            if hasattr(raw, 'tobytes'):
                raw = raw.tobytes()
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode('ascii', errors='ignore')
            s = str(raw).strip().strip('\x00').strip()
            return float(s) if s else None
        except Exception:
            return None

    ext_min = ext_max = None

    hf = getattr(reader, 'hf', None)
    if hf is not None:
        try:
            img_attrs = hf['DataSetInfo']['Image'].attrs
            ext_min = _to_float(img_attrs.get('ExtMin0'))
            ext_max = _to_float(img_attrs.get('ExtMax0'))
        except Exception:
            ext_min = ext_max = None

    if ext_min is None or ext_max is None:
        for _meth in ('read_numerical_dataset_attr', 'read_attribute'):
            fn = getattr(reader, _meth, None)
            if fn is None:
                continue
            try:
                ext_max = _to_float(fn('ExtMax0'))
                ext_min = _to_float(fn('ExtMin0'))
                if ext_min is not None and ext_max is not None:
                    break
            except Exception:
                continue

    if ext_min is None or ext_max is None:
        return None
    extent = abs(ext_max - ext_min)
    if extent <= 0 or width_px <= 0:
        return None
    microns_per_pixel = extent / float(width_px)
    if not (1e-4 < microns_per_pixel < 1e4):
        return None
    return microns_per_pixel


def _tiff_pixel_size_um(file_path):
    """Read physical pixel size (µm/px) from baseline TIFF resolution tags.

    AICSImage's physical_pixel_sizes only reads OME-XML and ImageJ metadata; it
    does not fall back to the standard TIFF XResolution/YResolution/ResolutionUnit
    tags. Many microscope-exported TIFFs (and channel-split exports) store pixel
    size ONLY in those baseline tags, so AICSImage reports None and PyCAT wrongly
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

import skimage as sk
# ── aicsimageio is GONE. Every reader construction goes through the seam. ────
#
# This import was already DEAD — `open_image()` replaced every use of it in 1.5.529, and an
# AST walk confirms `AICSImage` is referenced nowhere in this file's code.
from pycat.file_io.image_reader import open_image, read_plane
from pycat.utils.channel_naming import (
    extract_channel_info_from_aicsimage,
    extract_channel_info_from_ims,
    suggest_colormap,
)
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QCheckBox, QRadioButton, QPushButton, QFileDialog, QLineEdit, QMessageBox
from PyQt5.QtGui import QFont
from napari.utils.notifications import show_warning as napari_show_warning

# Local application imports
from pycat.ui.ui_utils import add_image_with_default_colormap
from pycat.utils.general_utils import dtype_conversion_func, debug_log
from pycat.toolbox.image_processing_tools import apply_rescale_intensity
from pycat.file_io.multidim_io import _ZarrTZYX, _ZarrZYX



class LayerDataframeSelectionDialog(QDialog):
    """
    A dialog that allows users to select from a list of layers and dataframe names for operations
    such as saving or processing. Users can also choose a clearing option to specify whether all
    data should be cleared or only the data that has been saved.

    Parameters
    ----------
    layers : list
        A list of layer objects. Each layer object is expected to have a 'name' attribute.
    dataframe_names : list of str
        A list of names representing the dataframes available for selection.

    Attributes
    ----------
    selected_layers : list
        A list of names of the layers that the user has selected.
    selected_dataframes : list of str
        A list of names of the dataframes that the user has selected.
    
    Methods
    -------
    get_selections(self):
        Returns the selections of layers and dataframes, along with the clearing option.
    """
    def __init__(self, layers, dataframe_names):
        """
        Initializes the dialog with the provided layers and dataframe names, setting up
        the UI components including checkboxes for each layer and dataframe, and radio buttons
        for clearing options.
        """
        super().__init__() # Initialize the parent class
        
        self.layers = layers
        self.dataframe_names = dataframe_names  # Expecting list of dataframe names
        self.selected_layers = []
        self.selected_dataframes = []
        
        layout = QVBoxLayout()

        # List all available layers with checkboxes, annotated with their
        # estimated on-disk size and whether they are RECONSTRUCTABLE.
        #
        # Two kinds of waste motivated this:
        #  * An UPSCALED IMAGE carries no information its source didn't — it is a
        #    pure interpolation, so saving a 4x upscale costs 16x the pixels for
        #    nothing (measured: 484 kB -> 7.7 MB). It is reconstructable from the
        #    source + the factor, so it is flagged and unticked by default.
        #  * A MASK SEGMENTED AT HIGH RESOLUTION is NOT redundant — its sub-pixel
        #    boundaries are real information that downscaling destroys (it can
        #    merge touching objects). Those are never flagged as disposable.
        layout.addWidget(QLabel("Select Layers to Save:"))
        _hint = QLabel(
            "<span style='color:#aaa;font-size:9pt;'>Sizes are the compressed "
            "estimate. Layers marked <span style='color:#e8a33d;'>reconstructable"
            "</span> are pure interpolations of another layer (an upscale carries "
            "no new information) — they're unticked by default. Masks segmented at "
            "high resolution are <i>not</i> reconstructable and are never flagged."
            "</span>")
        _hint.setWordWrap(True)
        layout.addWidget(_hint)

        self.layer_checkboxes = {}

        def _est_size_mb(layer):
            """Rough compressed-size estimate: masks/labels compress ~100x, images
            ~1.5x. Cheap heuristic — no need to actually compress to inform a
            checkbox."""
            try:
                d = layer.data
                n = int(np.prod(d.shape))
                # right-sized bytes-per-pixel (matches what the saver now writes)
                is_label = (type(layer).__name__ == 'Labels')
                if is_label:
                    try:
                        mx = int(np.asarray(d[0] if d.ndim == 3 else d).max())
                    except Exception:
                        mx = 65535
                    bpp = 1 if mx <= 255 else 2
                    ratio = 100.0           # masks compress enormously
                else:
                    bpp = 2
                    ratio = 1.5             # real image data barely compresses
                return (n * bpp / ratio) / (1024 * 1024)
            except Exception:
                return 0.0

        def _is_reconstructable(layer):
            """True only for layers that are pure interpolations of another layer
            (upscaled IMAGES). Deliberately conservative: never flags a mask, since
            a mask segmented at high res holds real sub-pixel information."""
            try:
                if type(layer).__name__ == 'Labels':
                    return False            # never call a segmentation disposable
                name = str(layer.name).lower()
                if 'upscal' in name:
                    return True
                # Tag-based: provenance says it was derived by an upscale step.
                try:
                    from pycat.utils.layer_tags import get_tags
                    tags = get_tags(layer) or {}
                    op = str(tags.get('operation', '')).lower()
                    if 'upscal' in op:
                        return True
                except Exception:
                    pass
                return False
            except Exception:
                return False

        default_checked_layers = [
            "Labeled Cell Mask",
            "Cell Labeled Puncta Mask",
            "Overlay Image",
            "Pre-Processed Fluorescence Image",
        ]

        _total_all = 0.0
        for layer in self.layers:
            mb = _est_size_mb(layer)
            _total_all += mb
            recon = _is_reconstructable(layer)
            label = f"{layer.name}   ({mb:.1f} MB)"
            if recon:
                label += "   — reconstructable (upscale of another layer)"
            checkbox = QCheckBox(label)
            if recon:
                checkbox.setStyleSheet("color: #e8a33d;")
                checkbox.setToolTip(
                    "This layer is an upscaled copy of another layer. Upscaling "
                    "adds no information — it can be recreated from the source "
                    "layer and the scale factor, so saving it wastes space "
                    "(a 4x upscale is 16x the pixels). Unticked by default.")
            self.layer_checkboxes[layer.name] = checkbox
            layout.addWidget(checkbox)

            # Default on for the usual results; never default-on a reconstructable.
            if layer.name in default_checked_layers and not recon:
                checkbox.setChecked(True)


        # List all available Python dataframe names with checkboxes
        layout.addWidget(QLabel("Select Dataframes to Save:"))
        self.df_checkboxes = {}
        # Create checkboxes for each dataframe name
        for df_name in self.dataframe_names:
            checkbox = QCheckBox(df_name)
            self.df_checkboxes[df_name] = checkbox
            layout.addWidget(checkbox)


            # List of default checked dataframe names
            default_checked_dfs = [
                "cell_df", 
                "puncta_df"
            ]

            # Set the default state of some checkboxes
            if df_name in default_checked_dfs:
                checkbox.setChecked(True)

        # Radio buttons for Clearing option
        self.clear_all_radio = QRadioButton("Clear All")
        self.clear_saved_radio = QRadioButton("Clear Only Saved")
        self.clear_all_radio.setChecked(True)  # Default to clear all 
        layout.addWidget(self.clear_all_radio)
        layout.addWidget(self.clear_saved_radio)
        
        # Ok and Cancel buttons
        self.ok_btn = QPushButton("OK")
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        layout.addWidget(self.ok_btn)
        layout.addWidget(self.cancel_btn)

        # Clear WITHOUT saving — discards everything after an explicit confirm.
        self.clear_without_saving = False
        self.discard_btn = QPushButton("☠  Clear Without Saving")
        self.discard_btn.setToolTip(
            "Discard all layers and data without saving anything.")
        self.discard_btn.setStyleSheet(
            "QPushButton { color: #b00020; font-weight: bold; }")
        def _on_discard():
            confirm = QMessageBox.warning(
                self, "Clear without saving?",
                "This will permanently clear ALL layers and data and save "
                "NOTHING.\n\nAll unsaved data will be lost. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if confirm == QMessageBox.Yes:
                self.clear_without_saving = True
                self.accept()
        self.discard_btn.clicked.connect(_on_discard)
        layout.addWidget(self.discard_btn)

        self.setLayout(layout)

    def get_selections(self):
        """
        Gathers and returns the user's selections, including the selected layers, selected
        dataframes, and the selected clearing option.

        Returns
        -------
        tuple
            A tuple containing two lists (selected layers and selected dataframes) and a boolean
            indicating the clearing option (True for clearing all, False for clearing only saved).
        """
        # Update which layers/dataframes are selected
        self.selected_layers = [layer for layer, checkbox in self.layer_checkboxes.items() if checkbox.isChecked()]
        self.selected_dataframes = [df for df, checkbox in self.df_checkboxes.items() if checkbox.isChecked()]
        
        clear_all = self.clear_all_radio.isChecked()
        
        return self.selected_layers, self.selected_dataframes, clear_all


class ChannelAssignmentDialog(QDialog):
    """
    A dialog for assigning names to image channels, providing a user-friendly interface for 
    specifying custom names for each channel based on the file path or default naming conventions. 
    It supports differentiating between mask channels and other image types.

    Parameters
    ----------
    channels : list of tuples
        A list where each tuple contains channel data, the file path of the channel, and potentially
        other metadata. The channel data and file path are used in the UI.
    is_mask : bool, optional
        A flag indicating whether the channels being named are mask channels. This affects the
        default naming convention. Default is False.
    parent : QWidget, optional
        The parent widget of the dialog. Default is None.

    Attributes
    ----------
    channel_name_inputs : list of QLineEdit
        A list of QLineEdit widgets that allow the user to enter custom names for each channel.

    Methods
    -------
    initUI(self):
        Initializes the user interface components of the dialog, including labels and text input
        fields for channel names, and the OK button to accept the naming.
    """
    def __init__(self, channels, is_mask=False, parent=None, channel_info=None):
        """
        Initializes the dialog with the provided channels, setting up the UI for channel naming.

        Parameters
        ----------
        channel_info : list of dict, optional
            Per-channel metadata-derived info from identify_channel(), used
            to pre-populate default names (e.g. "DAPI", "EGFP") instead of
            generic "Segmentation Image"/"Fluorescence Image" placeholders
            when channel identity could be determined from file metadata.
        """
        super().__init__(parent)
        self.channels = channels
        self.is_mask = is_mask
        self.channel_info = channel_info or []
        self.initUI()

    def initUI(self):
        """
        Sets up the layout and UI elements of the dialog, including labels indicating the channel
        number and file name, and text input fields pre-populated with default names that the user
        can customize. An OK button is provided for confirming the naming.
        """
        layout = QVBoxLayout()
        self.channel_name_inputs = [] # Create a list to store the textbox name inputs

        # Add labels and input fields for each channel
        for channel_num, (channel_data, file_path, _) in enumerate(self.channels):
            label = QLabel(f"Channel {channel_num + 1} ({os.path.basename(file_path)}):")
            input_field = QLineEdit()

            # Set the default name — prefer metadata-derived channel identity
            # (e.g. "DAPI", "EGFP") when available; fall back to the original
            # position-based convention otherwise so existing workflows that
            # rely on "Segmentation Image"/"Fluorescence Image" still work.
            info = self.channel_info[channel_num] if channel_num < len(self.channel_info) else None
            if not self.is_mask:
                if info is not None and info.get('source') != 'position':
                    # Metadata gave us a real identity — use it, but keep the
                    # familiar suffix for the first two channels so downstream
                    # dropdowns that default to these names still find them.
                    if channel_num == 0:
                        default_name = f"Segmentation Image ({info['label']})"
                    elif channel_num == 1:
                        default_name = f"Fluorescence Image ({info['label']})"
                    else:
                        default_name = f"{info['layer_name']} {os.path.basename(file_path)}"
                elif channel_num == 0:
                    default_name = "Segmentation Image"
                elif channel_num == 1:
                    default_name = "Fluorescence Image"
                else:
                    default_name = f"{os.path.basename(file_path)} Ch {channel_num+1}"
            else:
                default_name = f"{os.path.basename(file_path)} Ch {channel_num+1} Mask"

            # Set the default name in the input field
            input_field.setText(default_name)
            self.channel_name_inputs.append(input_field)

            # Add the label and input field to the layout
            layout.addWidget(label)
            layout.addWidget(input_field)

        # Add the OK button to confirm the channel names
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        layout.addWidget(ok_button)

        # Set the layout for the dialog
        self.setLayout(layout)
        self.setWindowTitle("Channel Name Assignment")


# Main FileIOClass for handling file input/output operations





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
        arr = np.asarray(raw).astype(np.float32)
        if arr.ndim == 2:
            return arr[spatial]
        return arr[(slice(None),) + spatial]

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)

    def __len__(self):
        return self.shape[0]

    def transpose(self, *axes):
        return np.asarray(self.__getitem__(0))[np.newaxis]


class _ImsReaderTYX:
    """Lazy (T, Y, X) IMS view backed directly by imaris_ims_file_reader.ims."""
    def __init__(self, reader, c, suppress_ctx=None):
        self._reader = reader
        self._c = c
        self._ctx = suppress_ctx or _suppress_ims_chunk_prints
        T, _, _, Y, X = reader.shape
        self.shape = (T, Y, X)
        self.dtype = np.dtype('float32')
        self.ndim = 3

    def _read_frame(self, t):
        with self._ctx():
            raw = self._reader[int(t), self._c, 0, :, :]
        return _ims_frame_2d(raw)

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            t_sel = idx[0] if len(idx) > 0 else slice(None)
            yx_sel = idx[1:] if len(idx) > 1 else (slice(None), slice(None))
        else:
            t_sel = idx
            yx_sel = (slice(None), slice(None))
        t_indices = _ims_indices(t_sel, self.shape[0])
        frames = [self._read_frame(t)[yx_sel] for t in t_indices]
        if isinstance(t_sel, (int, np.integer)):
            return frames[0]
        return np.stack(frames, axis=0)

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)

    def __len__(self):
        return self.shape[0]


class _ImsReaderZYX:
    """Lazy (Z, Y, X) IMS view backed directly by imaris_ims_file_reader.ims."""
    def __init__(self, reader, c, t=0, suppress_ctx=None):
        self._reader = reader
        self._c = c
        self._t = t
        self._ctx = suppress_ctx or _suppress_ims_chunk_prints
        _, _, Z, Y, X = reader.shape
        self.shape = (Z, Y, X)
        self.dtype = np.dtype('float32')
        self.ndim = 3

    def _read_plane(self, z):
        with self._ctx():
            raw = self._reader[self._t, self._c, int(z), :, :]
        return _ims_frame_2d(raw)

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            z_sel = idx[0] if len(idx) > 0 else slice(None)
            yx_sel = idx[1:] if len(idx) > 1 else (slice(None), slice(None))
        else:
            z_sel = idx
            yx_sel = (slice(None), slice(None))
        z_indices = _ims_indices(z_sel, self.shape[0])
        planes = [self._read_plane(z)[yx_sel] for z in z_indices]
        if isinstance(z_sel, (int, np.integer)):
            return planes[0]
        return np.stack(planes, axis=0)

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)

    def __len__(self):
        return self.shape[0]


class _ImsReaderTZYX:
    """Lazy (T, Z, Y, X) IMS view backed directly by imaris_ims_file_reader.ims."""
    def __init__(self, reader, c, suppress_ctx=None):
        self._reader = reader
        self._c = c
        self._ctx = suppress_ctx or _suppress_ims_chunk_prints
        T, _, Z, Y, X = reader.shape
        self.shape = (T, Z, Y, X)
        self.dtype = np.dtype('float32')
        self.ndim = 4

    def _read_plane(self, t, z):
        with self._ctx():
            raw = self._reader[int(t), self._c, int(z), :, :]
        return _ims_frame_2d(raw)

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            t_sel = idx[0] if len(idx) > 0 else slice(None)
            z_sel = idx[1] if len(idx) > 1 else slice(None)
            yx_sel = idx[2:] if len(idx) > 2 else (slice(None), slice(None))
        else:
            t_sel, z_sel, yx_sel = idx, slice(None), (slice(None), slice(None))
        t_indices = _ims_indices(t_sel, self.shape[0])
        z_indices = _ims_indices(z_sel, self.shape[1])
        arr = np.stack([
            np.stack([self._read_plane(t, z)[yx_sel] for z in z_indices], axis=0)
            for t in t_indices
        ], axis=0)
        # Squeeze out scalar-selected axes in reverse order (Z first, then T)
        # so that arr[0, 0] returns (Y, X), arr[0, :] returns (Z, Y, X), etc.
        if isinstance(z_sel, (int, np.integer)):
            arr = arr[:, 0]   # (T, 1, Y, X) -> (T, Y, X) -- squeeze Z
        if isinstance(t_sel, (int, np.integer)):
            arr = arr[0]      # (T, ...) -> squeeze T (now leading axis)
        return arr

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)

    def __len__(self):
        return self.shape[0]


def resolve_ome_file_set(primary_path):
    """Inspect a (possibly multi-file) OME-TIFF and report which companion files
    the metadata references and which are actually present on disk.

    Micro-Manager / OME-TIFF acquisitions are often split across sibling files
    (``..._MMStack_Pos0.ome.tif``, ``..._1.ome.tif``, …). The OME metadata in the
    FIRST file lists every file in the set. Two things can go wrong:

      * the companion files ARE present → we want to read frames from whichever
        file physically holds them (a true multi-file lazy view);
      * the companions are MISSING (a user copied one file out of the set without
        realising they were linked) → tifffile silently zero-fills the absent
        planes and prints a per-frame warning. Zero frames are misleading, so we
        prefer to use only the frames that physically exist and say so.

    Returns a dict:
        {
          'referenced': [filenames listed in OME metadata],
          'present':    [filenames that exist on disk, in order],
          'missing':    [filenames referenced but absent],
          'is_multifile': bool,   # more than one file referenced
          'complete':   bool,     # all referenced files present
        }
    The caller decides policy (warn + use present frames, build a cross-file
    view, etc.). Never raises — on any parsing problem it reports the primary
    file alone as a single-file set.
    """
    import os
    import re
    result = {'referenced': [], 'present': [], 'missing': [],
              'is_multifile': False, 'complete': True}
    try:
        import tifffile as _tf
        with _tf.TiffFile(primary_path) as _t:
            ome = _t.ome_metadata or ''
        # OME lists each file via <UUID FileName="...">; de-duplicate, keep order.
        names = []
        for fn in re.findall(r'FileName="([^"]+)"', ome):
            if fn not in names:
                names.append(fn)
        primary_name = os.path.basename(primary_path)
        if primary_name not in names:
            names.insert(0, primary_name)
        result['referenced'] = names
        result['is_multifile'] = len(names) > 1
        folder = os.path.dirname(os.path.abspath(primary_path))
        for fn in names:
            if os.path.exists(os.path.join(folder, fn)):
                result['present'].append(fn)
            else:
                result['missing'].append(fn)
        result['complete'] = (len(result['missing']) == 0)
    except Exception:
        # Any failure → treat as a plain single file (safe default).
        import os as _os
        result['referenced'] = [_os.path.basename(primary_path)]
        result['present'] = list(result['referenced'])
        result['missing'] = []
        result['is_multifile'] = False
        result['complete'] = True
    return result


def build_ome_page_map(primary_path):
    """Build a global frame → (file_path, page_index) map for an OME set,
    including ONLY files that physically exist. Frames whose backing file is
    missing are omitted (not zero-filled), so the resulting stack contains only
    real data. Also returns the count of frames dropped because their file was
    absent.

    Returns (page_map, n_missing_frames) where page_map is a list of
    (abs_file_path, page_index_within_that_file). Reading frame t means opening
    page_map[t][0] and reading its page page_map[t][1].

    Falls back to a single-file map (this file's own pages) on any problem.
    """
    import os
    info = resolve_ome_file_set(primary_path)
    folder = os.path.dirname(os.path.abspath(primary_path))
    page_map = []
    n_missing_frames = 0
    try:
        import tifffile as _tf
        for fn in info['referenced']:
            fpath = os.path.join(folder, fn)
            if not os.path.exists(fpath):
                # Count how many frames this missing file would have held so the
                # caller can report it. Use the primary's per-file page count as
                # an estimate when the file itself can't be opened.
                continue
            with _tf.TiffFile(fpath) as _t:
                npages = len(_t.pages)
            for p in range(npages):
                page_map.append((os.path.abspath(fpath), p))
        # Report missing frames as the difference the OME metadata implied. We
        # can only know present frames for certain; expose the missing FILE
        # count via the caller (resolve_ome_file_set) — frame count for missing
        # files is not reliably knowable without the files, so report 0 here and
        # let the caller warn based on missing file names.
        if not page_map:
            raise ValueError("empty page map")
    except Exception:
        # Fallback: this file's own pages only.
        try:
            import tifffile as _tf
            with _tf.TiffFile(primary_path) as _t:
                npages = len(_t.pages)
            page_map = [(os.path.abspath(primary_path), p) for p in range(npages)]
        except Exception:
            page_map = [(os.path.abspath(primary_path), 0)]
    return page_map, n_missing_frames


class _TiffPageStack:
    """Lazy (T, Y, X) wrapper that reads ONE frame at a time straight from a
    multipage TIFF via tifffile's page reader.

    This is the fast path for Micro-Manager / OME-TIFF time-series. AICSImage's
    dask reader consults the OME plane-map on every frame read, so scrubbing a
    large MMStack lags badly; a plain `TiffFile.pages[t].asarray()` is a direct
    seek+read of a single page (no dask graph, no OME-map walk, no copy of the
    whole stack), which matches the smooth per-frame behaviour of the native IMS
    zarr path. The file handle is kept open for the life of the wrapper.
    """
    def __init__(self, tiff_path, n_frames, H, W, dtype, channel_idx=0,
                 n_channels=1):
        import tifffile as _tf
        self._path   = tiff_path
        self._nc     = max(1, int(n_channels))
        self._ci     = int(channel_idx)
        self.dtype   = np.dtype('float32')
        self.ndim    = 3

        # Decide single-file (fast path) vs multi-file OME set. For a genuine
        # multi-file acquisition we build a page map spanning the files that are
        # actually PRESENT on disk; missing companions are dropped (not zeroed),
        # and the frame count is reduced to match real data.
        self._page_map = None          # list of (abs_path, page_idx) if multifile
        self._handles = {}             # abs_path -> open TiffFile (lazy)
        info = resolve_ome_file_set(tiff_path)
        if info.get('is_multifile') and not info.get('complete'):
            # Some companion files are missing — use only present frames.
            page_map, _ = build_ome_page_map(tiff_path)
            self._page_map = page_map
            self._present_info = info
            real_frames = len(page_map) // self._nc
            self.shape = (int(real_frames), int(H), int(W))
        elif info.get('is_multifile') and info.get('complete'):
            # All companions present — read across files via the page map.
            page_map, _ = build_ome_page_map(tiff_path)
            self._page_map = page_map
            self._present_info = info
            total_frames = len(page_map) // self._nc
            self.shape = (int(total_frames), int(H), int(W))
        else:
            # Single-file fast path (unchanged behaviour): keep one open handle
            # and index its series/pages directly.
            self._tif = _tf.TiffFile(tiff_path)
            try:
                self._pages = self._tif.series[0].pages
            except Exception:
                self._pages = self._tif.pages
            self.shape = (int(n_frames), int(H), int(W))

    def _page_index(self, t):
        # Interleaved channels are stored as consecutive pages per timepoint.
        return int(t) * self._nc + self._ci

    def _get_handle(self, path):
        """Lazily open (and cache) a TiffFile handle for a page-map file."""
        h = self._handles.get(path)
        if h is None:
            import tifffile as _tf
            h = _tf.TiffFile(path)
            self._handles[path] = h
        return h

    def _read_frame(self, t):
        if self._page_map is not None:
            # Multi-file: look up which physical file + page holds this frame.
            gi = self._page_index(t)
            if gi >= len(self._page_map):
                # Past the end of real data — return a black frame rather than
                # crashing (defensive; shape math should prevent this).
                return np.zeros(self.shape[1:], np.float32)
            path, page_idx = self._page_map[gi]
            handle = self._get_handle(path)
            arr = np.asarray(handle.pages[page_idx].asarray())
            return arr.astype(np.float32)
        # Single-file fast path.
        arr = np.asarray(self._pages[self._page_index(t)].asarray())
        return arr.astype(np.float32)

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            t_idx, spatial = idx[0], idx[1:]
        else:
            t_idx, spatial = idx, ()

        # napari (and downstream code) may index the T axis with an int (one
        # frame — the scrubbing case), a slice (a range or the whole stack), or
        # a fancy index. Handle each; only the int case is the fast per-frame
        # read, but slices must not crash (the previous version did int(slice)).
        if isinstance(t_idx, slice):
            t_range = range(*t_idx.indices(self.shape[0]))
            frames = np.stack([self._read_frame(t) for t in t_range], axis=0) \
                if len(t_range) else np.empty((0,) + self.shape[1:], np.float32)
            if spatial:
                return frames[(slice(None),) + spatial]
            return frames
        if isinstance(t_idx, (list, tuple, np.ndarray)):
            frames = np.stack([self._read_frame(int(t)) for t in t_idx], axis=0)
            if spatial:
                return frames[(slice(None),) + spatial]
            return frames

        # Scalar index → single frame (the common, fast path).
        arr = self._read_frame(t_idx)
        if spatial:
            return arr[spatial]
        return arr

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)

    def as_full_array(self, dtype=np.float32, progress_callback=None):
        """Materialise the whole stack as a real (T, H, W) numpy array, read
        one frame at a time. Use this for analysis that needs every frame — it
        avoids the deliberately-truncated __array__ (which returns only frame 0
        to keep napari's incidental array requests cheap).

        dtype=None preserves the source frame dtype (e.g. integer label masks).
        progress_callback : optional callable(done, total) for a determinate
            "Materializing…" bar.
        """
        _f0 = self._read_frame(0)
        _dt = _f0.dtype if dtype is None else dtype
        out = np.empty(self.shape, dtype=_dt)
        out[0] = _f0.astype(_dt)
        n = self.shape[0]
        if progress_callback is not None:
            try: progress_callback(1, n)
            except Exception: pass
        for t in range(1, n):
            out[t] = self._read_frame(t).astype(_dt)
            if progress_callback is not None:
                try: progress_callback(t + 1, n)
                except Exception: pass
        return out

    def __len__(self):
        return self.shape[0]

    def transpose(self, *axes):
        return self.__getitem__(0)[np.newaxis]

    def close(self):
        # Single-file mode keeps one handle in self._tif; multi-file mode keeps
        # a cache of per-file handles in self._handles. Close whichever exist.
        try:
            tif = getattr(self, '_tif', None)
            if tif is not None:
                tif.close()
        except Exception:
            pass
        for h in getattr(self, '_handles', {}).values():
            try:
                h.close()
            except Exception:
                pass


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










class _LazyArraySource:
    """**A napari-facing view over ANY lazy source — dask, zarr, or numpy.**

    ── The wrapper it replaces was named after the wrong thing ──────────────────

    ``_ZarrTZYX_generic`` is not zarr-specific. It receives **zarr arrays, numpy arrays, and BioIO
    dask arrays** — and the name told a reader it could rely on zarr semantics it does not have.

    More importantly, the TZYX branch used to **transcode the entire file into a temporary zarr**
    before showing anything, *purely so it would have a zarr to wrap.* **The dask array was already
    lazy.** The copy bought nothing and cost the whole file.

    This wraps whatever it is given:

    * ``__getitem__`` computes **only the requested slice** — one plane per slider move
    * ``__array__`` **refuses**, because an implicit full read is never what the caller meant

    *(A zarr cache remains the right thing for repeated random access. But it belongs in the
    background, behind an explicit action — not on the critical path to first display.)*
    """

    def __init__(self, source):
        self._source = source
        self.shape = tuple(int(v) for v in source.shape)
        self.dtype = np.dtype(getattr(source, 'dtype', np.float32))
        self.ndim = len(self.shape)

    def __getitem__(self, index):
        value = self._source[index]
        # dask computes on demand; zarr and numpy are already here. Ask, do not assume — a reader
        # plugin is free to return either.
        if hasattr(value, 'compute'):
            value = value.compute()
        return np.asarray(value).astype(np.float32)

    def __len__(self):
        return self.shape[0]

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)


class _ZarrTYX_generic:
    """
    Lightweight napari-compatible wrapper around a plain zarr Array
    for TIFF/CZI stacks (no IMS chunk-print suppression needed).
    """
    def __init__(self, z):
        self._z    = z
        self.shape = z.shape
        self.dtype = np.dtype('float32')
        self.ndim  = 3

    def __getitem__(self, idx):
        if isinstance(idx, tuple):
            t_idx, spatial = idx[0], idx[1:]
        else:
            t_idx, spatial = idx, (slice(None), slice(None))
        arr = np.asarray(self._z[t_idx]).astype(np.float32)
        if arr.ndim == 2:
            return arr[spatial]
        return arr[(slice(None),) + spatial]

    def __array__(self, dtype=None):
        """**Refuse.** See `pycat.file_io.lazy_guard` — this has cost three bugs."""
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)

    def __len__(self):
        return self.shape[0]

    def transpose(self, *axes):
        return np.asarray(self._z[0]).astype(np.float32)[np.newaxis]


# When True, the 'Object Diameter' / 'Cell Diameter' annotation layers are created
# eagerly at every file load (legacy behaviour). When False (default), they are
# created ON DEMAND by the measure widget the first time the user measures, so a
# session that never measures diameters isn't cluttered with them. Flip to True to
# revert if the on-demand path ever misbehaves (e.g. the native Home button).
EAGER_DIAMETER_LAYERS = False




class FileIOClass:
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
        """Give every unit-scale layer the same physical extent as the primary
        scaled image layer, so all layers overlay and the µm scale bar stays
        consistent. Layers that already carry a meaningful (non-unit) scale — the
        reference itself, or an explicitly-scaled upscaled layer — are left alone.
        Image/Labels layers are aligned by field of view (handles upscaled masks);
        Shapes/Points overlays inherit the reference's per-pixel scale."""
        import numpy as np
        try:
            import napari.layers as _nl
        except Exception:
            return
        try:
            ref = None
            for l in self.viewer.layers:
                if isinstance(l, _nl.Image):
                    rs = np.asarray(l.scale, float)
                    if (rs.size >= 2 and np.all(np.isfinite(rs))
                            and np.any(np.abs(rs[-2:] - 1.0) > 1e-9)):
                        ref = l
                        break
            if ref is None:
                return
            ref_scale = np.asarray(ref.scale, float)
            ref_shape = np.asarray(getattr(ref, 'data').shape, float)
            if ref_shape.size < 2:
                return
            ref_fov = ref_shape[-2:] * ref_scale[-2:]
            for l in self.viewer.layers:
                if l is ref:
                    continue
                try:
                    sc = np.asarray(l.scale, float)
                    if sc.size >= 2 and np.any(np.abs(sc[-2:] - 1.0) > 1e-9):
                        continue   # already scaled — don't override
                    if isinstance(l, (_nl.Shapes, _nl.Points)):
                        new_yx = ref_scale[-2:]     # pixel-coordinate overlay
                    elif (isinstance(l, _nl.Image) and getattr(l, 'rgb', False)):
                        # RGB overlays (e.g. the side-by-side "Overlay Image",
                        # which is (H, 2W, 3)) are built at the SAME per-pixel
                        # resolution as the reference — they just have more pixels
                        # (two panels wide). Fit them to the reference field of
                        # view would compress the extra width into one image's
                        # worth of world units (the "overlay looks squished in X"
                        # symptom). Instead give them the reference's per-pixel
                        # scale so each overlay pixel matches a reference pixel.
                        new_yx = ref_scale[-2:]
                    else:
                        shp = np.asarray(getattr(l, 'data').shape, float)
                        if shp.size < 2:
                            continue
                        spatial_shape = shp[-2:]
                        new_yx = ref_fov / spatial_shape
                    if not (np.all(np.isfinite(new_yx)) and np.all(new_yx > 0)):
                        continue
                    new_scale = list(np.asarray(l.scale, float))
                    new_scale[-2] = float(new_yx[0]); new_scale[-1] = float(new_yx[1])
                    l.scale = new_scale
                except Exception:
                    continue
        except Exception:
            pass

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

            # Open the image using AICSImage.
            # We detect NumPy 2.0 newbyteorder errors lazily — only reading
            # the minimal metadata needed (xarray_dask_data uses dask so no
            # full read happens).  Avoid calling image.dims or image.data
            # eagerly as these trigger full image reads on large files.
            _use_fallback = False
            try:
                image = open_image(file_path)
                # Access only the dask-backed metadata — does not read pixel data
                _ = image.xarray_dask_data.dims
            except AttributeError as _e:
                if "newbyteorder" not in str(_e):
                    raise
                _use_fallback = True
                print(f"[PyCAT] NumPy 2.0 tifffile fallback for {os.path.basename(file_path)}")
            except Exception:
                # Any other error on metadata access — try normal path anyway
                image = open_image(file_path)

            if _use_fallback:
                # skimage also uses tifffile internally so hits the same NumPy 2.0
                # bug. PIL/Pillow has its own independent TIFF reader that avoids
                # tifffile entirely and works correctly with NumPy 2.0.
                try:
                    from PIL import Image as _PILImage
                    import numpy as _np
                    _pil_img = _PILImage.open(file_path)
                    # PIL loads one frame at a time; iterate frames for stacks
                    _frames = []
                    try:
                        while True:
                            _frames.append(_np.array(_pil_img).astype('float32'))
                            _pil_img.seek(_pil_img.tell() + 1)
                    except EOFError:
                        pass
                    if len(_frames) == 1:
                        all_channels.append((_frames[0], file_path, 0))
                    else:
                        for _ci, _frame in enumerate(_frames):
                            all_channels.append((_frame, file_path, _ci))
                    from napari.utils.notifications import show_warning as _warn
                    _warn(
                        f"{os.path.basename(file_path)} loaded via PIL fallback (NumPy 2.0 / tifffile conflict). "
                        "Run 'python fix_tifffile.py' to permanently fix this."
                    )
                except Exception as _pil_e:
                    from napari.utils.notifications import show_warning as _warn
                    _warn(
                        f"Could not load {os.path.basename(file_path)}: NumPy 2.0 is incompatible with "
                        "the installed tifffile version. Run 'python fix_tifffile.py' to fix this permanently, "
                        "or downgrade NumPy: pip install 'numpy<2.0'"
                    )
                    print(f"[PyCAT] PIL fallback also failed: {_pil_e}")
                continue  # skip the AICSImage path below

            image = open_image(file_path)
            self.central_manager.active_data_class.update_metadata(image)
            # Also store the normalised metadata record for the metadata widget
            # and results export.
            try:
                from pycat.file_io.metadata_extract import extract_metadata
                _md = extract_metadata(file_path, image=image)
                self.central_manager.active_data_class.data_repository['file_metadata'] = _md
            except Exception as _mde:
                debug_log("file_io: metadata extraction failed", _mde)
            
            # Get the number of pages and channels in the image
            num_pages = getattr(image.dims, 'S', 1)
            num_channels = getattr(image.dims, 'C', 1)

            # Check if the image has channels or pages
            if not hasattr(image.dims, 'S') and not hasattr(image.dims, 'C'):
                raise ValueError("Image does not have any channels or pages. Check file format.")

            # If there are multiple pages, iterate over pages and channels
            if num_pages > 1: 
                k = 0
                for page_num in range(num_pages):
                    for channel_num in range(num_channels):
                        k += 1
                        channel_data = read_plane(image, path=file_path, scene=page_num, c=channel_num, t=0)
                        all_channels.append((channel_data, file_path, k))
            # If only one page, iterate over channels
            else: 
                for channel_num in range(num_channels):
                    channel_data = read_plane(image, path=file_path, c=channel_num, t=0)
                    all_channels.append((channel_data, file_path, channel_num))

            # Identify channel identity from OME/Bio-Formats metadata
            # (fluorophore name, emission wavelength, or position fallback)
            for ch_num in range(num_channels):
                try:
                    self._last_channel_info = getattr(self, '_last_channel_info', [])
                    self._last_channel_info.append(
                        extract_channel_info_from_aicsimage(image, ch_num)
                    )
                except Exception:
                    pass

        # Check if there are multiple channels to assign names
        if len(all_channels) > 1:
            self.assign_channels_in_dialog(
                all_channels,
                channel_info=getattr(self, '_last_channel_info', None)
            )
        # If only one channel, default to 'Fluorescence Image'
        else:
            fluorescence_image = all_channels[0][0]
            self.load_into_viewer(fluorescence_image, name="Fluorescence Image")

        # Add layers for measuring object and cell diameters to the viewer based on the image size
        self._add_diameter_annotation_layers()

        # Update the data instance with default sizes for object and cell diameters
        self.central_manager.active_data_class.data_repository['object_size'] = channel_data.shape[0] // 20
        self.central_manager.active_data_class.data_repository['cell_diameter'] = channel_data.shape[0] // 8

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
        """Populate tags on a freshly-loaded layer from what the load path already
        knows — dimensionality, scale calibration, role, provenance, and (when
        available) modality/channel. Also re-applies any tags saved inside the
        file (PyCAT-saved TIFFs embed their tag store), with saved user overrides
        taking precedence over freshly-inferred tags.

        This is the single load-time tagging entry point; call it once per layer
        after it is added to the viewer. No new detection is performed — it
        captures inferences the loaders already made into the structured tag store
        so autopopulation can query typed facts instead of matching names.
        """
        if layer is None:
            return
        try:
            from pycat.utils import layer_tags as _LT
        except Exception:
            return

        # 1. Inferred tags from load context.
        try:
            if role:
                _LT.tag_layer(layer, 'role', role, source='inferred')

            # Dimensionality from the axis sizes the loader parsed.
            if n_p and n_p > 1:
                dim = 'multi-position'
            elif n_t and n_t > 1:
                dim = '2d+t'
            elif n_z and n_z > 1:
                dim = 'z-stack'
            else:
                dim = '2d'
            _LT.tag_layer(layer, 'dimensionality', dim, source='inferred')

            # Scale calibration: a real pixel size is essentially never exactly
            # 1.0 µm/px, so 1.0 means "no metadata / uncalibrated". Viscosity and
            # any physical measurement depend on this, so it is a first-class tag.
            if microns_per_pixel is not None:
                calibrated = abs(float(microns_per_pixel) - 1.0) > 1e-9
                _LT.tag_layer(layer, 'scale',
                              'calibrated' if calibrated else 'uncalibrated',
                              source=('from_metadata' if calibrated else 'inferred'))

            _LT.tag_layer(layer, 'provenance', provenance, source='inferred')

            if modality:
                _LT.tag_layer(layer, 'modality', modality, source='inferred')
            if channel:
                _LT.tag_layer(layer, 'channel', channel, source='from_metadata')
        except Exception as _e:
            debug_log("file_io: load-time tagging failed", _e)

        # 2. Re-apply any tags saved inside the file (overrides win). Applied
        #    AFTER inference so a saved user_set tag locks over a fresh inference.
        try:
            if file_path:
                saved = self._read_pycat_tags(file_path)
                if saved:
                    self._apply_saved_tags_to_layer(layer, saved)
        except Exception as _e:
            debug_log("file_io: reapplying saved tags failed", _e)

    def _file_has_imaging_metadata_safe(self, file_path):
        """Best-effort check for whether a file carries real imaging-structure
        metadata (pixel size, channels, dimensional axes). Used ONLY to choose the
        wording of the image-vs-mask prompt, so it must never raise — any failure
        returns True (softer 'looks like X, confirm' wording) rather than crashing
        the load. (Replaces an earlier call to a method that was never defined,
        which crashed every menu-Add / drop of a non-signifier file.)"""
        try:
            ext = os.path.splitext(file_path)[1].lower()
            # Formats that inherently carry structured imaging metadata.
            if ext in ('.ims', '.czi'):
                return True
            if ext in ('.tif', '.tiff'):
                try:
                    import tifffile
                    with tifffile.TiffFile(file_path) as tf:
                        # OME-XML, ImageJ metadata, or a resolution tag all count
                        # as real imaging metadata.
                        if getattr(tf, 'is_ome', False) or getattr(tf, 'is_imagej', False):
                            return True
                        p0 = tf.pages[0]
                        for tag in ('XResolution', 'YResolution', 'ImageDescription'):
                            try:
                                if tag in p0.tags:
                                    return True
                            except Exception:
                                pass
                    return False
                except Exception:
                    return True  # can't tell -> assume metadata (softer prompt)
            # PNG/JPG typically carry no imaging metadata.
            if ext in ('.png', '.jpg', '.jpeg'):
                return False
            return True
        except Exception:
            return True

    def _read_pycat_signifier(self, file_path):
        """Read PyCAT's saved-file signifier from a TIFF's ImageDescription, if
        present. Returns 'image' / 'mask' / None. Lets a file PyCAT itself saved
        be re-loaded with its type known exactly, without guessing."""
        try:
            import tifffile, json as _json
            ext = os.path.splitext(file_path)[1].lower()
            if ext not in ('.tif', '.tiff'):
                return None
            with tifffile.TiffFile(file_path) as tf:
                desc = None
                try:
                    desc = tf.pages[0].tags['ImageDescription'].value
                except Exception:
                    desc = getattr(tf, 'imagej_metadata', None)
                if not desc:
                    return None
                if isinstance(desc, bytes):
                    desc = desc.decode('utf-8', 'ignore')
                # The description may be OME-XML or our JSON; only parse JSON.
                desc = desc.strip()
                if not desc.startswith('{'):
                    return None
                tag = _json.loads(desc)
                if isinstance(tag, dict) and tag.get('pycat'):
                    k = tag.get('kind')
                    if k in ('image', 'mask'):
                        return k
        except Exception:
            pass
        return None

    def _read_pycat_tags(self, file_path):
        """Read PyCAT's embedded tag store ({'tags':[...],'edges':[...]}) from a
        saved TIFF's ImageDescription, if present. Returns the dict or None. This
        is how layer tags (role/modality/lineage/etc.) survive save→reload —
        they ride in the same JSON blob as the image/mask signifier."""
        try:
            import tifffile, json as _json
            ext = os.path.splitext(file_path)[1].lower()
            if ext not in ('.tif', '.tiff'):
                return None
            with tifffile.TiffFile(file_path) as tf:
                try:
                    desc = tf.pages[0].tags['ImageDescription'].value
                except Exception:
                    desc = getattr(tf, 'imagej_metadata', None)
                if not desc:
                    return None
                if isinstance(desc, bytes):
                    desc = desc.decode('utf-8', 'ignore')
                desc = desc.strip()
                if not desc.startswith('{'):
                    return None
                tag = _json.loads(desc)
                if isinstance(tag, dict) and tag.get('pycat'):
                    ts = tag.get('pycat_tags')
                    if isinstance(ts, dict):
                        return ts
        except Exception:
            pass
        return None

    def _apply_saved_tags_to_layer(self, layer, tag_store):
        """Re-apply a saved tag store to a freshly-loaded layer via the tag
        engine, preserving each tag's original source/confidence. Edges are
        restored as-is (their targets are tag-ids that resolve once all layers
        of a session are loaded)."""
        if not tag_store or layer is None:
            return
        try:
            from pycat.utils import layer_tags as _LT
            for t in tag_store.get('tags', []):
                try:
                    _LT.tag_layer(layer, t.get('key'), t.get('value'),
                                  source=t.get('source', 'inferred'),
                                  confidence=t.get('confidence'),
                                  overwrite=True)
                except Exception:
                    pass
            # Restore edges directly into the canonical store.
            md = getattr(layer, 'metadata', None)
            if isinstance(md, dict):
                store = md.setdefault('pycat_tags', {'tags': [], 'edges': []})
                store.setdefault('edges', [])
                for e in tag_store.get('edges', []):
                    if e not in store['edges']:
                        store['edges'].append(e)
        except Exception as _e:
            debug_log("file_io: applying saved tags failed", _e)
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
        # LABEL rather than merely whether AICSImage can read some dims, which is
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
        """Return (n_pages, is_undeclared) for a TIFF: n_pages is the page count of
        the first series; is_undeclared is True when the file carries no ImageJ/OME
        axis metadata AND the series' leading axis is unlabelled ('Q'), i.e. a
        plain multipage TIFF whose stack axis type is unknown. Safe: any failure
        returns (1, False) so the caller falls back to the normal 2D path."""
        try:
            import tifffile
            with tifffile.TiffFile(file_path) as t:
                if t.is_imagej or t.is_ome:
                    return (len(t.pages), False)  # metadata present → not our case
                series = t.series[0]
                axes = getattr(series, 'axes', '') or ''
                n_pages = len(t.pages)
                # Undeclared when the leading (non-YX) axis is 'Q' (unknown) or the
                # shape has >1 in a leading position with no T/Z label.
                lead = axes[:-2] if len(axes) >= 2 else axes
                undeclared = n_pages > 1 and (('T' not in lead) and ('Z' not in lead))
                return (n_pages, undeclared)
        except Exception:
            return (1, False)

    def _ask_multipage_axis(self, file_path, n_pages):
        """Prompt for how to interpret an undeclared multipage TIFF: time-series
        (T), z-stack (Z), or genuinely separate 2D images. Returns 'T', 'Z',
        'separate', or None (dialog unavailable). A 'remember this choice'
        checkbox skips the prompt for later undeclared TIFFs this session."""
        # Honour a remembered choice from earlier this session.
        remembered = getattr(self, '_multipage_axis_choice', None)
        if remembered is not None:
            return remembered
        try:
            from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QLabel,
                                         QRadioButton, QCheckBox, QPushButton,
                                         QButtonGroup)
        except Exception:
            return None
        import os as _os
        dlg = QDialog()
        dlg.setWindowTitle("Unlabelled multipage TIFF")
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(
            f"'{_os.path.basename(file_path)}' has {n_pages} pages but no axis "
            "metadata (the stack axis type is unknown).\n\nHow should PyCAT load "
            "it? (Time-series and z-stack load the same way — the label only "
            "affects axis-dependent analysis steps, which will warn if the axis "
            "was assumed.)"))
        grp = QButtonGroup(dlg)
        rb_t = QRadioButton("Time-series (T) — a movie / recovery / tracking stack")
        rb_z = QRadioButton("Z-stack (Z) — an axial slice series")
        rb_s = QRadioButton("Separate 2D images — unrelated planes, load individually")
        rb_t.setChecked(True)
        for rb in (rb_t, rb_z, rb_s):
            grp.addButton(rb); v.addWidget(rb)
        remember = QCheckBox("Remember my choice for other unlabelled TIFFs this session")
        v.addWidget(remember)
        ok = QPushButton("Load"); ok.clicked.connect(dlg.accept)
        v.addWidget(ok)
        if dlg.exec_() != QDialog.Accepted:
            return None
        choice = 'T' if rb_t.isChecked() else ('Z' if rb_z.isChecked() else 'separate')
        if remember.isChecked():
            self._multipage_axis_choice = choice
        return choice

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
        """Ask whether to copy a slow-storage file to fast local temp storage
        before loading. Returns 'yes'|'no'|'always'|'never' (or 'no' if the dialog
        can't be shown)."""
        try:
            from PyQt5.QtWidgets import QMessageBox, QCheckBox
        except Exception:
            return 'no'
        import os as _os
        try:
            size_mb = (verdict.size_bytes or 0) / (1024 * 1024)
        except Exception:
            size_mb = 0
        where = {'network': 'a network location', 'removable': 'a removable drive',
                 'cloud_placeholder': 'cloud storage (will download)'}.get(
                     getattr(verdict, 'location', ''), 'slow storage')
        box = QMessageBox()
        box.setWindowTitle("Copy to local storage first?")
        box.setIcon(QMessageBox.Question)
        box.setText(
            f"'{_os.path.basename(file_path)}' ({size_mb:.0f} MB) is on {where}, "
            "which loads slowly. Copy it to fast local temp storage first (with a "
            "progress bar), then load from the copy?")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.Yes)
        always = QCheckBox("Always do this for slow files this session")
        box.setCheckBox(always)
        res = box.exec_()
        if res == QMessageBox.Yes:
            return 'always' if always.isChecked() else 'yes'
        return 'never' if always.isChecked() else 'no'

    def _copy_to_local_with_progress(self, file_path, verdict):
        """Copy a (slow-storage) file to a local temp dir in chunks, showing a Qt
        progress bar (the copy IS the slow I/O, so this doubles as the slow-load
        progress indicator). Returns the local path, or None on failure/cancel."""
        import os as _os
        import tempfile
        try:
            from PyQt5.QtWidgets import QProgressDialog
            from PyQt5.QtCore import Qt
        except Exception:
            QProgressDialog = None
        try:
            total = _os.path.getsize(file_path)
        except Exception:
            total = getattr(verdict, 'size_bytes', 0) or 0
        dst_dir = _os.path.join(tempfile.gettempdir(), 'pycat_local_cache')
        try:
            _os.makedirs(dst_dir, exist_ok=True)
        except Exception:
            return None
        # Opportunistic cleanup: remove cached copies older than ~24h so the
        # cache doesn't grow unbounded across sessions (the OS clears the temp
        # dir eventually, but this keeps it tidy between reboots).
        try:
            import time as _time
            now = _time.time()
            for _f in _os.listdir(dst_dir):
                _p = _os.path.join(dst_dir, _f)
                try:
                    if now - _os.path.getmtime(_p) > 86400:
                        _os.remove(_p)
                except Exception:
                    pass
        except Exception:
            pass
        dst = _os.path.join(dst_dir, _os.path.basename(file_path))
        # If a fresh local copy already exists (same size), reuse it.
        try:
            if _os.path.exists(dst) and total and _os.path.getsize(dst) == total:
                print(f"[PyCAT storage] reusing local copy: {dst}")
                return dst
        except Exception:
            pass

        dlg = None
        if QProgressDialog is not None:
            try:
                dlg = QProgressDialog(
                    f"Copying {_os.path.basename(file_path)} to local storage…",
                    "Cancel", 0, 100)
                dlg.setWindowTitle("Copying to local storage")
                dlg.setWindowModality(Qt.WindowModal)
                dlg.setMinimumDuration(0)
                dlg.setValue(0)
            except Exception:
                dlg = None

        CHUNK = 8 * 1024 * 1024  # 8 MB chunks
        copied = 0
        try:
            with open(file_path, 'rb') as fsrc, open(dst, 'wb') as fdst:
                while True:
                    buf = fsrc.read(CHUNK)
                    if not buf:
                        break
                    fdst.write(buf)
                    copied += len(buf)
                    if dlg is not None and total:
                        pct = int(copied * 100 / total)
                        dlg.setValue(min(pct, 100))
                        from PyQt5.QtWidgets import QApplication
                        QApplication.processEvents()
                        if dlg.wasCanceled():
                            fdst.close()
                            try:
                                _os.remove(dst)
                            except Exception:
                                pass
                            print("[PyCAT storage] copy cancelled by user")
                            return None
            if dlg is not None:
                dlg.setValue(100)
            print(f"[PyCAT storage] copied to local cache: {dst} "
                  f"({copied/(1024*1024):.0f} MB)")
            # Track for optional cleanup at session end.
            if not hasattr(self, '_local_cache_files'):
                self._local_cache_files = []
            self._local_cache_files.append(dst)
            return dst
        except Exception as e:
            print(f"[PyCAT storage] copy-to-local failed: {e}")
            try:
                if _os.path.exists(dst):
                    _os.remove(dst)
            except Exception:
                pass
            return None

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
        .czi          Zeiss CZI — opened via AICSImage; frames loaded one at a
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
        except Exception as _e:
            debug_log("file_io: IMS pixel-size read failed, using 1.0 µm/px", _e)

        # Extract and store the full normalised metadata record (IMS metadata
        # was previously discarded entirely — update_metadata is only called on
        # the AICSImage path).
        try:
            from pycat.file_io.metadata_extract import extract_metadata
            md = extract_metadata(file_path, reader=reader, width_px=W)
            self.central_manager.active_data_class.data_repository['file_metadata'] = md
        except Exception as _e:
            debug_log("file_io: IMS metadata extraction failed", _e)

        channels_to_load = list(range(n_c))
        self._ims_reader    = reader
        self._ims_channels  = channels_to_load
        self._ims_n_frames  = n_t
        self._ims_n_z       = n_z
        self._ims_file_path = file_path
        channel_data = None
        self._ims_zarr_refs = []

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

            for channel_idx in channels_to_load:
                with _suppress_ims_chunk_prints():
                    _ch_info = extract_channel_info_from_ims(pos_reader, channel_idx)
                _ch_label    = _ch_info['layer_name']
                _ch_colormap = suggest_colormap(_ch_info['bucket'])
                debug_log(f"file_io: IMS channel {channel_idx} -> "
                          f"name='{_ch_info.get('raw_name')}' label='{_ch_label}' "
                          f"bucket='{_ch_info.get('bucket')}'")

                if n_t == 1 and n_z == 1:
                    # Single 2D frame — no lazy wrapper needed
                    with _suppress_ims_chunk_prints():
                        frame = pos_reader[0, channel_idx, 0, :, :].astype(np.float32)
                    self.load_into_viewer(
                        frame, name=f"{self.base_file_name} {_ch_label}{pos_suffix}")
                    channel_data = frame

                elif n_z == 1:
                    # Pure time series (T, Y, X) — direct reader path, bypasses
                    # the zarr-store adapter that can raise KeyError on valid chunks.
                    layer_name = f"{self.base_file_name} {_ch_label} Stack{pos_suffix}"
                    lazy_tyx = _ImsReaderTYX(pos_reader, channel_idx,
                                             suppress_ctx=_suppress_ims_chunk_prints)
                    self._ims_zarr_refs.append((pos_reader, None, lazy_tyx))
                    if channel_idx == 0 and pos_path == file_path:
                        # Probe-read the first frame to populate channel_data (used
                        # only for default object/cell diameter estimates). Wrapped
                        # defensively: Box Drive, file locks, or partial HDF5 syncs
                        # can raise OSError/KeyError at this point; if so we fall back
                        # to a dummy array of the correct spatial size so the layer
                        # still loads — the user gets a warning with the likely cause.
                        try:
                            channel_data = lazy_tyx[0]
                        except (KeyError, OSError, Exception) as _probe_err:
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
                        self._ims_reader_array = pos_reader
                        self._ims_lazy_tyx   = lazy_tyx
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
                    self.viewer.add_image(lazy_tyx, **_add_kwargs)
                    napari_show_info(
                        f"Lazy-loaded IMS {_ch_label}{pos_suffix}: {n_t} frames "
                        f"{H}\u00d7{W}px (frames read on demand)"
                    )

                elif n_t == 1:
                    # Pure z-stack (Z, Y, X), no time dimension — lazy, on demand.
                    layer_name = f"{self.base_file_name} {_ch_label} Z-Stack{pos_suffix}"
                    lazy_zyx = _ImsReaderZYX(pos_reader, channel_idx, t=0,
                                             suppress_ctx=_suppress_ims_chunk_prints)
                    self._ims_zarr_refs.append((pos_reader, None, lazy_zyx))
                    if channel_idx == 0 and pos_path == file_path:
                        try:
                            channel_data = lazy_zyx[0]
                        except (KeyError, OSError, Exception) as _probe_err:
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
                    self.viewer.add_image(lazy_zyx, **_add_kwargs)
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
                    self._ims_zarr_refs.append((pos_reader, None, lazy_tzyx))
                    if channel_idx == 0 and pos_path == file_path:
                        channel_data = lazy_tzyx[0, 0]
                        self._ims_reader_array = pos_reader
                        self._ims_lazy_tzyx  = lazy_tzyx
                    # First (t=0, z=0) plane for contrast — reuse the prefetched
                    # one for channel 0, else read a single plane.
                    try:
                        _plane0 = (channel_data if (channel_idx == 0 and pos_path == file_path)
                                   else lazy_tzyx[0, 0])
                    except Exception:
                        _plane0 = None
                    _clim = _lazy_contrast_limits(lazy_tzyx, prefetched=_plane0)
                    _add_kwargs = dict(name=layer_name, colormap=_ch_colormap)
                    if _clim is not None:
                        _add_kwargs['contrast_limits'] = _clim
                    self.viewer.add_image(lazy_tzyx, **_add_kwargs)
                    napari_show_info(
                        f"Lazy-loaded IMS T-Z stack {_ch_label}{pos_suffix}: "
                        f"{n_t} timepoints \u00d7 {n_z} z-slices, "
                        f"{H}\u00d7{W}px (nothing pre-loaded — scrub T/Z sliders "
                        f"to read on demand)"
                    )

        self._finalise_stack_load(H, W, microns_per_pixel, channels_to_load,
                                  n_t, n_z, file_path, source='ims')


    # ── Generic back-end (TIFF, CZI, …) ────────────────────────────────────

    def _open_stack_generic(self, file_path: str, ext: str):
        """
        Generic stack loader for TIFF, OME-TIFF, and CZI files via AICSImage.

        Reads the full T, C, Z dimensions from file metadata (OME-XML,
        ImageJ hyperstack description, or format-native equivalent) rather
        than forcing a choice between T and Z when both are present —
        nested time-series-with-z-stack acquisitions are loaded as genuine
        lazy 4D (T, Z, Y, X) per-channel arrays, matching the IMS loader.

        Multi-position acquisitions (OME-XML scenes / Bio-Formats series)
        are detected via AICSImage's `.scenes` and offered through the
        same position-selection dialog used for IMS sibling files.
        """
        import tempfile, zarr as _zarr

        from napari.utils.notifications import show_info as napari_show_info
        from napari.utils.notifications import show_warning as napari_show_warning
        from pycat.file_io.multidim_io import (
            show_position_selection_dialog, _ZarrTZYX_generic)

        microns_per_pixel = 1.0
        n_c = 1

        # ── Read metadata ────────────────────────────────────────────────
        try:
            # `open_image` is the seam: it routes to aicsimageio or bioio, and raises
            # ImageReaderUnavailable with the exact `pip install` line when neither is
            # present. The hand-rolled ImportError that used to live here said less.
            image = open_image(file_path)
            use_aicsimage = True

            # ── Multi-position (scene) detection ───────────────────────
            scenes = list(getattr(image, 'scenes', []) or [])
            scenes_to_load = [image.current_scene] if scenes else [None]
            if len(scenes) > 1:
                scene_dicts = [{'position_index': i, 'filename': s}
                               for i, s in enumerate(scenes)]
                selected_idx = show_position_selection_dialog(
                    scene_dicts,
                    title=f"Multi-Position Acquisition Detected ({len(scenes)} scenes)",
                )
                if selected_idx:
                    scenes_to_load = [scenes[i] for i in selected_idx]
                    napari_show_info(
                        f"Opening {len(scenes_to_load)} of {len(scenes)} "
                        f"detected scene(s)."
                    )
                else:
                    scenes_to_load = [image.current_scene]

            try:
                px = image.physical_pixel_sizes
                microns_per_pixel = float(px.Y) if px.Y else 1.0
            except Exception as _e:
                debug_log("file_io: reading physical pixel size (falling back to "
                          "1.0 µm/px — micron measurements may be wrong)", _e)
                pass

            # Fallback: AICSImage's physical_pixel_sizes only reads OME-XML and
            # ImageJ metadata, not the baseline TIFF resolution tags. If it came
            # back empty (== 1.0), try reading XResolution/ResolutionUnit directly.
            if abs(microns_per_pixel - 1.0) < 1e-9:
                _tag_px = _tiff_pixel_size_um(file_path)
                if _tag_px is not None:
                    microns_per_pixel = _tag_px
                    debug_log(f"file_io: pixel size {_tag_px:.6f} µm/px recovered "
                              "from TIFF resolution tags (AICSImage missed it)")

            self.central_manager.active_data_class.update_metadata(image)
            # Also store the normalised metadata record for the metadata widget
            # and results export.
            try:
                from pycat.file_io.metadata_extract import extract_metadata
                _md = extract_metadata(file_path, image=image)
                self.central_manager.active_data_class.data_repository['file_metadata'] = _md
            except Exception as _mde:
                debug_log("file_io: metadata extraction failed", _mde)

        except Exception as _e:
            debug_log("file_io: AICSImage load failed, falling back to direct "
                      "tifffile read (scene/T/Z metadata unavailable)", _e)
            use_aicsimage = False
            scenes_to_load = [None]
            # ── A METADATA defect must not trigger a full EAGER read ────────────
            #
            # The ``except Exception`` above catches **everything** — including a failure to parse
            # something entirely optional: a channel name, a physical pixel size, a scene entry, a
            # plugin property. Any of those used to drop PyCAT into ``tifffile.imread(file_path)``,
            # which **reads the whole file into memory.**
            #
            # ***A cosmetic metadata problem should not cost a gigabyte.***
            #
            # ``_TiffPageStack`` does per-page seeks — the same lazy contract as the primary path.
            # If it cannot be built either, THEN the file is genuinely unreadable and the eager
            # read is the honest last resort.
            import tifffile
            arr = None

            # ── The 1.6.4 version of this was BROKEN and never ran ────────────────
            #
            # It called ``_TiffPageStack(file_path)`` — **one argument, where five are required.**
            # That raised ``TypeError``, was caught by the ``except`` below, and **fell straight
            # through to the eager read anyway.** *Item 8 was reported fixed and was not.*
            #
            # The shape comes from tifffile directly, which is the whole point: **BioIO's metadata
            # is what failed**, and tifffile can still read the pages.
            try:
                with tifffile.TiffFile(file_path) as _probe:
                    _pages = _probe.pages
                    _n_pages = len(_pages)
                    if _n_pages > 0:
                        _first = _pages[0]
                        _H, _W = int(_first.shape[-2]), int(_first.shape[-1])
                        arr = _TiffPageStack(file_path, _n_pages, _H, _W, _first.dtype,
                                             channel_idx=0, n_channels=1)
                        debug_log("file_io: BioIO metadata unavailable — reading pages LAZILY "
                                  "via tifffile, not a full eager read", _e)
            except Exception as _lazy_exc:
                debug_log("file_io: the lazy TIFF page reader failed too", _lazy_exc)
                arr = None

            if arr is None:
                # Genuinely unreadable lazily. **A full read is the honest last resort** — and it
                # is now reached only when the lazy path has actually been tried and failed, rather
                # than because the call to it was malformed.
                debug_log("file_io: falling back to a FULL eager read of %s" % file_path)
                arr = tifffile.imread(file_path)
            while arr.ndim > 3 and arr.shape[0] == 1:
                arr = arr[0]
            if arr.ndim == 2:
                arr = arr[np.newaxis]
            n_frames = arr.shape[0]
            H, W = arr.shape[1], arr.shape[2]
            n_c = 1
            n_t, n_z = n_frames, 1
            # Recover pixel size from baseline resolution tags in this branch too.
            _tag_px = _tiff_pixel_size_um(file_path)
            if _tag_px is not None:
                microns_per_pixel = _tag_px
                debug_log(f"file_io: pixel size {_tag_px:.6f} µm/px recovered "
                          "from TIFF resolution tags (direct tifffile branch)")

        zarr_dir = tempfile.mkdtemp(prefix='pycat_stack_')
        self._stack_zarr_paths = []
        # Keep lazy sources (AICSImage readers + dask arrays) alive for as long
        # as the layers exist, so on-demand frame reads keep working without an
        # eager copy to disk.
        if not hasattr(self, '_stack_lazy_refs'):
            self._stack_lazy_refs = []
        channels_to_load = list(range(n_c)) if not use_aicsimage else None
        H = W = n_t = n_z = None

        for scene in scenes_to_load:
            scene_suffix = ''
            if use_aicsimage and len(scenes_to_load) > 1:
                image.set_scene(scene)
                scene_suffix = f" [{scene}]"

            if use_aicsimage:
                n_t = getattr(image.dims, 'T', 1)
                n_c = getattr(image.dims, 'C', 1)
                n_z = getattr(image.dims, 'Z', 1)
                H   = getattr(image.dims, 'Y', None)
                W   = getattr(image.dims, 'X', None)
                channels_to_load = list(range(n_c))

            for channel_idx in channels_to_load:
                if use_aicsimage:
                    _ch_info = extract_channel_info_from_aicsimage(image, channel_idx)
                else:
                    _ch_info = {'layer_name': f'C{channel_idx}',
                                 'bucket': 'unknown', 'label': f'C{channel_idx}',
                                 'source': 'position'}

                _ch_label    = _ch_info['layer_name']
                _ch_colormap = suggest_colormap(_ch_info['bucket'])

                import os as _os

                if not use_aicsimage:
                    # tifffile fallback — single (T,H,W), no Z/scene metadata
                    arr_ch = arr.astype(np.float32)
                    layer_name = f"{self.base_file_name} {_ch_label} Stack{scene_suffix}"
                    # Data is already in memory — wrap it directly (no disk copy).
                    wrapper = _ZarrTYX_generic(arr_ch)
                    self._stack_lazy_refs.append(arr_ch)
                    # ── Pin the contrast limits, or napari reads EVERY frame ────────
                    #
                    # Without explicit limits, napari auto-estimates contrast **and builds the
                    # thumbnail** by calling ``np.asarray()`` on the layer — which hits
                    # ``__array__`` and, on a lazy wrapper, **loads the entire acquisition off
                    # disk one frame at a time.**
                    #
                    # PyCAT's own source has documented this for months, on the IMS path::
                    #
                    #     "On a USB-HDD IMS stack that is the real cause of the multi-second
                    #      stalls (e.g. when adding an ROI layer forces a layer-list refresh)."
                    #
                    # **The IMS branches pin them. These three did not.** One frame is cheap; the
                    # user can still adjust the contrast afterwards.
                    #
                    # (In 1.6.4 ``__array__`` RAISES rather than materialising — so a layer that
                    # reaches this path without limits now fails loudly instead of hanging. That is
                    # the right trade, but pinning the limits is what stops it happening at all.)
                    _add_kwargs = {'name': layer_name, 'colormap': _ch_colormap}
                    _clim = _lazy_contrast_limits(wrapper)
                    if _clim is not None:
                        _add_kwargs['contrast_limits'] = _clim
                    _stack_layer = self.viewer.add_image(wrapper, **_add_kwargs)
                    # Show the current frame, not a projection — a stray
                    # 'mean' projection mode averages the whole time-series
                    # into a flat/black display.
                    try:
                        _stack_layer.projection_mode = 'none'
                    except Exception:
                        pass
                    napari_show_info(
                        f"Loaded {_ch_label}: {n_frames} frames "
                        f"{H}\u00d7{W}px → '{layer_name}'"
                    )
                    continue

                if n_t == 1 and n_z == 1:
                    frame = read_plane(image, path=file_path, c=channel_idx, t=0, z=0, dtype=np.float32)
                    self.load_into_viewer(
                        frame,
                        name=f"{self.base_file_name} {_ch_label}{scene_suffix}")

                elif n_z == 1:
                    # Pure time series (T, Y, X). AICSImage's dask array is lazy
                    # but SLOW to scrub: indexing it per slider-move re-executes
                    # the dask/reader graph for that frame, re-decoding through
                    # the TIFF/CZI backend every time — so scrubbing a modest
                    # TIFF lags badly even though an IMS (native zarr random
                    # access) scrolls smoothly. Materialize once, frame-by-frame,
                    # into an on-disk zarr store (memory-bounded — never holds
                    # the whole stack in RAM) so subsequent frame reads are fast
                    # zarr random access, matching the IMS path.
                    layer_name = f"{self.base_file_name} {_ch_label} Stack{scene_suffix}"

                    # ── For a TIFF the dask array is never built ────────────────────
                    #
                    # ``bioio-tifffile`` builds its dask array via ``tif.aszarr()``, and
                    # **tifffile's zarr store is broken on zarr 3.2**::
                    #
                    #     ImportError: cannot import name 'RegularChunkGrid'
                    #     -> re-raised as: ValueError: zarr 3.2.1 < 3 is not supported
                    #
                    # *(That message is a lie — 3.2.1 is not less than 3. tifffile blames the
                    # version for any ImportError out of its zarr-3 module.)*
                    #
                    # **And this line crashed before ``_TiffPageStack`` was ever reached** — the
                    # dask array was built first, and then used only for its ``dtype``. For a TIFF
                    # it is not needed at all: ``_TiffPageStack`` seeks pages directly, and
                    # tifffile reports the dtype without touching zarr.
                    dask_arr = None
                    if ext not in ('.tif', '.tiff'):
                        dask_arr = image.get_image_dask_data('TYX', C=channel_idx)
                    # Lazy by design: pull exactly one frame per slider move —
                    # no eager copy. For TIFF/OME-TIFF (incl. Micro-Manager
                    # MMStack) read frames straight from the multipage TIFF via
                    # tifffile, which is a direct per-page seek and far faster to
                    # scrub than AICSImage's dask reader (which walks the OME
                    # plane-map on every frame). CZI has no tifffile path, so it
                    # keeps the dask wrapper.
                    wrapper = None
                    if ext in ('.tif', '.tiff'):
                        try:
                            # tifffile reports the dtype directly — no zarr store, no dask.
                            import tifffile as _tf_probe
                            with _tf_probe.TiffFile(file_path) as _probe:
                                _tiff_dtype = _probe.pages[0].dtype

                            wrapper = _TiffPageStack(
                                file_path, n_t, H, W, _tiff_dtype,
                                channel_idx=channel_idx, n_channels=n_c)
                            # If this is a multi-file OME set with missing
                            # companions, tell the user we're using only the
                            # frames that physically exist (least-friction: warn
                            # and proceed rather than block).
                            _pinfo = getattr(wrapper, '_present_info', None)
                            if _pinfo and _pinfo.get('missing'):
                                from napari.utils.notifications import show_warning as _sw
                                _sw(f"This OME-TIFF references "
                                    f"{len(_pinfo['referenced'])} linked files but "
                                    f"{len(_pinfo['missing'])} are missing "
                                    f"({', '.join(_pinfo['missing'][:3])}"
                                    f"{'…' if len(_pinfo['missing'])>3 else ''}). "
                                    f"Loading only the {wrapper.shape[0]} frames that "
                                    f"are present. If you meant to analyse the full "
                                    f"set, keep the linked .ome.tif files together.")
                            # Sanity check: for the single-file fast path the page
                            # count must be consistent with (frames x channels).
                            # (Multi-file mode sizes itself from the page map, so
                            # skip this check there.)
                            _pages_attr = getattr(wrapper, '_pages', None)
                            if _pages_attr is not None:
                                _npages = len(_pages_attr)
                                if n_c > 1 and _npages < n_t * n_c:
                                    wrapper.close()
                                    wrapper = None
                        except Exception as _te:
                            debug_log("file_io: tifffile page reader failed, "
                                      "using AICSImage dask wrapper", _te)
                            wrapper = None
                    if wrapper is None:
                        # The tifffile page reader declined or failed. Fall back to BioIO's dask
                        # array — building it NOW, because for a TIFF it was deliberately not built
                        # above (``tif.aszarr()`` is broken on zarr 3.2).
                        #
                        # **This can itself fail on a TIFF**, and if it does the file genuinely
                        # cannot be read lazily — which is worth saying plainly rather than
                        # crashing in tifffile's zarr store with a message that blames the wrong
                        # thing.
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

                        wrapper = _ZarrTYX_generic(dask_arr)
                        self._stack_lazy_refs.append((image, dask_arr))
                    else:
                        self._stack_lazy_refs.append(wrapper)  # keep handle open
                    # Pin contrast_limits from the first frame. Without this,
                    # napari auto-estimates the display range by calling
                    # np.asarray() on the whole wrapper (__array__), which
                    # materialises EVERY frame on each slider move — the real
                    # cause of TIFF/CZI scrubbing lag (the IMS path already does
                    # this; the generic path did not).
                    _add_kw = dict(name=layer_name, colormap=_ch_colormap)
                    _clim = _lazy_contrast_limits(wrapper)
                    if _clim is not None:
                        _add_kw['contrast_limits'] = _clim
                    _stack_layer = self.viewer.add_image(wrapper, **_add_kw)
                    # Show the current frame, not a projection — a stray
                    # 'mean' projection mode averages the whole time-series
                    # into a flat/black display.
                    try:
                        _stack_layer.projection_mode = 'none'
                    except Exception:
                        pass
                    napari_show_info(
                        f"Loaded {_ch_label}{scene_suffix}: {n_t} frames "
                        f"{H}\u00d7{W}px → '{layer_name}' (lazy)"
                    )

                elif n_t == 1:
                    # Pure z-stack (Z, Y, X)
                    layer_name = f"{self.base_file_name} {_ch_label} Z-Stack{scene_suffix}"

                    # ── BioIO's dask path is broken for TIFF on zarr 3.2 ────────────
                    #
                    # ``bioio-tifffile`` builds its dask array via ``tif.aszarr()``, and tifffile's
                    # zarr store fails to import from zarr 3.2 — then **blames the version**::
                    #
                    #     ValueError: zarr 3.2.1 < 3 is not supported
                    #
                    # *3.2.1 is not less than 3.* The real error is ``cannot import name
                    # 'RegularChunkGrid'``, one frame up, where nobody looks.
                    #
                    # ``_TiffPageStack`` handles the **TYX** case natively (direct page seeks, no
                    # zarr). It does **not** handle Z or T+Z, so those still go through BioIO — and
                    # if that fails, **say what actually happened** rather than let tifffile's
                    # misleading message reach the user.
                    try:
                        dask_arr = image.get_image_dask_data('ZYX', C=channel_idx)
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
                                f"TIFF Z stack, which still depends on that path."
                            ) from _dask_exc
                        raise
                    wrapper = _ZarrTYX_generic(dask_arr)
                    self._stack_lazy_refs.append((image, dask_arr))
                    # ── Pin the contrast limits, or napari reads EVERY frame ────────
                    #
                    # Without explicit limits, napari auto-estimates contrast **and builds the
                    # thumbnail** by calling ``np.asarray()`` on the layer — which hits
                    # ``__array__`` and, on a lazy wrapper, **loads the entire acquisition off
                    # disk one frame at a time.**
                    #
                    # PyCAT's own source has documented this for months, on the IMS path::
                    #
                    #     "On a USB-HDD IMS stack that is the real cause of the multi-second
                    #      stalls (e.g. when adding an ROI layer forces a layer-list refresh)."
                    #
                    # **The IMS branches pin them. These three did not.** One frame is cheap; the
                    # user can still adjust the contrast afterwards.
                    #
                    # (In 1.6.4 ``__array__`` RAISES rather than materialising — so a layer that
                    # reaches this path without limits now fails loudly instead of hanging. That is
                    # the right trade, but pinning the limits is what stops it happening at all.)
                    _add_kwargs = {'name': layer_name, 'colormap': _ch_colormap}
                    _clim = _lazy_contrast_limits(wrapper)
                    if _clim is not None:
                        _add_kwargs['contrast_limits'] = _clim
                    _stack_layer = self.viewer.add_image(wrapper, **_add_kwargs)
                    # Show the current frame, not a projection — a stray
                    # 'mean' projection mode averages the whole time-series
                    # into a flat/black display.
                    try:
                        _stack_layer.projection_mode = 'none'
                    except Exception:
                        pass
                    napari_show_info(
                        f"Loaded {_ch_label}{scene_suffix} z-stack: {n_z} slices "
                        f"{H}\u00d7{W}px → '{layer_name}' (zarr-backed)"
                    )

                else:
                    # Nested time-series-with-z-stack (T, Z, Y, X) — the
                    # scenario this fix targets. Previously this branch
                    # picked EITHER T or Z as "the" stack dimension and
                    # silently discarded the other entirely. Now both are
                    # preserved as a genuine lazy 4D array; napari adds a
                    # T slider and a Z slider automatically for 4D layers.
                    layer_name = f"{self.base_file_name} {_ch_label} T-Z Stack{scene_suffix}"
                    zarr_path = _os.path.join(
                        zarr_dir, f'ch{channel_idx}_tz{scene_suffix or "0"}')
                    # ── This TRANSCODED THE WHOLE FILE before showing anything ─────
                    #
                    # It was::
                    #
                    #     z = _zarr.open(zarr_path, mode='w', ...)
                    #     for t in range(n_t):
                    #         for zi in range(n_z):
                    #             z[t, zi] = np.asarray(dask_arr[t, zi])
                    #
                    # **Every (t, z) plane, decoded and written to a temporary zarr, on the
                    # synchronous path, before the first pixel reaches the screen.** On a 4-D
                    # acquisition that is the entire selected channel — and the user is looking at
                    # a frozen window while it happens.
                    #
                    # *It was not accidentally eager. It was a deliberate full-file copy, and the
                    # note beside it said "nothing pre-loaded beyond this write pass" — which is
                    # true, and which was the whole problem.*
                    #
                    # **The dask array is ALREADY lazy.** Wrapping it directly gives napari a
                    # 4-D source that reads one plane per slider move, and the window opens
                    # immediately.
                    #
                    # *(A zarr cache is still the right thing for repeated random access — but it
                    # belongs in the background, behind an "optimize for browsing" action, not on
                    # the critical path to first display.)*

                    # ── BioIO's dask path is broken for TIFF on zarr 3.2 ────────────
                    #
                    # ``bioio-tifffile`` builds its dask array via ``tif.aszarr()``, and tifffile's
                    # zarr store fails to import from zarr 3.2 — then **blames the version**::
                    #
                    #     ValueError: zarr 3.2.1 < 3 is not supported
                    #
                    # *3.2.1 is not less than 3.* The real error is ``cannot import name
                    # 'RegularChunkGrid'``, one frame up, where nobody looks.
                    #
                    # ``_TiffPageStack`` handles the **TYX** case natively (direct page seeks, no
                    # zarr). It does **not** handle Z or T+Z, so those still go through BioIO — and
                    # if that fails, **say what actually happened** rather than let tifffile's
                    # misleading message reach the user.
                    try:
                        dask_arr = image.get_image_dask_data('TZYX', C=channel_idx)
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
                                f"TIFF T+Z stack, which still depends on that path."
                            ) from _dask_exc
                        raise
                    wrapper = _LazyArraySource(dask_arr)
                    # ── Pin the contrast limits, or napari reads EVERY frame ────────
                    #
                    # Without explicit limits, napari auto-estimates contrast **and builds the
                    # thumbnail** by calling ``np.asarray()`` on the layer — which hits
                    # ``__array__`` and, on a lazy wrapper, **loads the entire acquisition off
                    # disk one frame at a time.**
                    #
                    # PyCAT's own source has documented this for months, on the IMS path::
                    #
                    #     "On a USB-HDD IMS stack that is the real cause of the multi-second
                    #      stalls (e.g. when adding an ROI layer forces a layer-list refresh)."
                    #
                    # **The IMS branches pin them. These three did not.** One frame is cheap; the
                    # user can still adjust the contrast afterwards.
                    #
                    # (In 1.6.4 ``__array__`` RAISES rather than materialising — so a layer that
                    # reaches this path without limits now fails loudly instead of hanging. That is
                    # the right trade, but pinning the limits is what stops it happening at all.)
                    _add_kwargs = {'name': layer_name, 'colormap': _ch_colormap}
                    _clim = _lazy_contrast_limits(wrapper)
                    if _clim is not None:
                        _add_kwargs['contrast_limits'] = _clim
                    _stack_layer = self.viewer.add_image(wrapper, **_add_kwargs)
                    # Show the current frame, not a projection — a stray
                    # 'mean' projection mode averages the whole time-series
                    # into a flat/black display.
                    try:
                        _stack_layer.projection_mode = 'none'
                    except Exception:
                        pass
                    napari_show_info(
                        f"Loaded {_ch_label}{scene_suffix} T-Z stack: "
                        f"{n_t} timepoints \u00d7 {n_z} z-slices, "
                        f"{H}\u00d7{W}px → '{layer_name}' (zarr-backed, "
                        f"nothing pre-loaded beyond this write pass)"
                    )

        self._finalise_stack_load(H, W, microns_per_pixel,
                                  list(range(n_c)),
                                  n_t if use_aicsimage else n_frames,
                                  n_z if use_aicsimage else 1,
                                  file_path, source='generic')


    # ── Shared post-load logic ───────────────────────────────────────────────

    def _fit_view_to_layer(self, layer=None, margin=0.9, attempt=0):
        """Fit the napari camera to an image layer, mirroring the (working)
        Home button exactly.

        The Home button reads ``layer.extent.world`` — the transform-aware extent
        napari actually renders with — and it fits correctly. An earlier version
        of this auto-fit recomputed ``shape × scale`` by hand, which can disagree
        with the real extent right after load: the µm/px scale was just assigned
        and napari's transform/extent cache may not have caught up at the moment
        the deferred fit fires, so the image opened tiny even though pressing Home
        afterwards fit it fine. Using ``extent.world`` here makes auto-fit behave
        identically to Home. Retries with growing delays until the canvas has a
        real size (it can be 0 while the dock is still laying out after load).
        """
        try:
            import numpy as np
            import napari.layers as _nl

            if layer is None:
                imgs = [l for l in self.viewer.layers if isinstance(l, _nl.Image)]
                if not imgs:
                    return
                layer = imgs[-1]

            cw = ch = None
            import warnings as _warnings
            with _warnings.catch_warnings():
                _warnings.simplefilter('ignore', FutureWarning)
                for accessor in ('_qt_viewer', 'qt_viewer'):
                    try:
                        sz = getattr(self.viewer.window, accessor).canvas.size
                        cw, ch = float(sz[0]), float(sz[1])
                        break
                    except Exception:
                        continue

            # Canvas not laid out yet → retry shortly (up to ~6 attempts).
            if (not cw or not ch or cw <= 1 or ch <= 1) and attempt < 6:
                from PyQt5.QtCore import QTimer as _QT
                _QT.singleShot(120 * (attempt + 1),
                               lambda: self._fit_view_to_layer(layer, margin, attempt + 1))
                return

            # Transform-aware world extent — same source of truth as Home.
            ext = np.asarray(layer.extent.world)     # (2, ndim): [mins, maxs]
            mins, maxs = ext[0], ext[1]
            nd = self.viewer.dims.ndisplay
            dims = list(self.viewer.dims.displayed)[-nd:]
            sizes = [float(maxs[d] - mins[d]) for d in dims]

            center = (mins + maxs) / 2.0
            self.viewer.camera.center = tuple(float(center[d]) for d in dims)

            if nd == 2 and cw and ch and all(s > 0 for s in sizes):
                # displayed dims are [y, x]; canvas is (width=x, height=y).
                _z = min(ch / sizes[0], cw / sizes[1]) * margin
                _z_before = float(self.viewer.camera.zoom)
                self.viewer.camera.zoom = _z
                if os.environ.get('PYCAT_DEBUG'):
                    print(f"[PyCAT fit] layer='{layer.name}' extent_world_size(yx)={sizes} "
                          f"canvas(w,h)=({cw},{ch}) zoom {_z_before:.4f} -> {_z:.4f} "
                          f"(attempt {attempt})")
                    from PyQt5.QtCore import QTimer as _QTd
                    _QTd.singleShot(600, lambda: print(
                        f"[PyCAT fit] zoom 600ms later = {float(self.viewer.camera.zoom):.4f} "
                        f"(if changed, something reset it)"))
            else:
                self.viewer.reset_view()
        except Exception as e:
            try:
                self.viewer.reset_view()
            except Exception:
                pass
            if os.environ.get('PYCAT_DEBUG'):
                print(f"[PyCAT] fit view skipped: {e}")

    def _finalise_stack_load(self, H, W, microns_per_pixel, channels_to_load,
                              n_t, n_z, file_path, source='generic'):
        """Update data repository and record batch step after any stack load."""
        dr = self.central_manager.active_data_class.data_repository
        dr['object_size']       = H // 20
        dr['cell_diameter']     = H // 8
        dr['microns_per_pixel_sq'] = microns_per_pixel ** 2
        # Provenance for the Set-Scale overwrite warning: a real microscope
        # pixel size is essentially never exactly 1.0 µm/px, so treat 1.0 as the
        # "no metadata" fallback and anything else as metadata-derived.
        dr['pixel_size_from_metadata'] = (abs(float(microns_per_pixel) - 1.0) > 1e-9)

        # The pixel size has just been set from this file's metadata (or fallen
        # back to 1.0). A plain load does not switch the data class, so notify
        # any registered gates (e.g. the pixel-size gate) to re-evaluate now,
        # otherwise the gate would keep its pre-load state and never appear.
        try:
            self.central_manager.notify_data_changed()
        except Exception:
            pass
        self._prompt_pixel_size_if_needed()

        self._add_diameter_annotation_layers()

        # Label the non-spatial slider axes so they read "T"/"Z" instead of the
        # default "0"/"1". napari shows one slider per axis beyond the displayed
        # two (Y, X); giving them names makes multi-dimensional browsing legible.
        try:
            ndim = 2
            if n_t and n_t > 1:
                ndim += 1
            if n_z and n_z > 1:
                ndim += 1
            if ndim > 2:
                # Axis order for the loaded stacks is (T, Z, Y, X) with whichever
                # of T/Z are present; build labels to match.
                labels = []
                if n_t and n_t > 1:
                    labels.append('T')
                if n_z and n_z > 1:
                    labels.append('Z')
                labels += ['Y', 'X']
                if len(labels) == self.viewer.dims.ndim:
                    self.viewer.dims.axis_labels = labels
        except Exception:
            pass

        # Open on the FIRST frame/slice, not napari's default centre. Most image
        # viewers open a stack on index 0; napari initialises each slider to the
        # middle of its axis, so a freshly-loaded time series or z-stack would
        # otherwise start mid-movie. Set every non-displayed (slider) axis to 0.
        # The last two axes are the displayed Y,X plane and are left untouched.
        try:
            if self.viewer.dims.ndim > 2:
                step = list(self.viewer.dims.current_step)
                for ax in range(self.viewer.dims.ndim - 2):
                    step[ax] = 0
                self.viewer.dims.current_step = tuple(step)
        except Exception:
            pass

        # Auto scale bar for the freshly-loaded stack.
        self._enable_auto_scale_bar()

        # ── Tag the freshly-loaded stack layers ──────────────────────────────
        # Populate the structured tag store from the load context (role, the
        # dimensionality just parsed, scale calibration, provenance) so downstream
        # autopopulation can query typed facts rather than matching names. Tag
        # only Image layers that are not yet tagged (i.e. the ones just added);
        # channel identity is left to metadata-driven naming already applied.
        try:
            import napari as _np_napari
            from pycat.utils import layer_tags as _LT
            for _lyr in self.viewer.layers:
                if _lyr.__class__.__name__ != 'Image':
                    continue
                if _LT.get_tag(_lyr, 'role') is not None:
                    continue  # already tagged (not freshly added)
                self._tag_loaded_layer(
                    _lyr, role='image', n_t=n_t, n_z=n_z,
                    microns_per_pixel=microns_per_pixel, file_path=file_path,
                    channel=getattr(_lyr, 'name', None), provenance='raw')
        except Exception as _e:
            debug_log("file_io: stack layer tagging failed", _e)

        # Fit the canvas to the newly-loaded image. Deferred long enough that the
        # scale bar has been applied and all layer-insert scale-alignment events
        # have flushed — otherwise the fit reads a stale extent and the image
        # opens tiny (whereas pressing Home later, once settled, fits correctly).
        try:
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(400, lambda: self._fit_view_to_layer())
        except Exception:
            self._fit_view_to_layer()

        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record('open_stack', {
                'file_path': file_path,
                'source': source,
                'channels': channels_to_load,
                'n_timepoints': n_t,
                'n_z': n_z,
            })

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

            # Open the mask using AICSImage package
            mask = open_image(file_path)

            # Get the number of pages and channels in the mask
            num_pages = getattr(mask.dims, 'S', 1)
            num_channels = getattr(mask.dims, 'C', 1)

            # Check if the image has channels or pages
            if not hasattr(mask.dims, 'S') and not hasattr(mask.dims, 'C'):
                raise ValueError("Image does not have any channels or pages. Check file format.")

            # If there are multiple pages, iterate over pages and channels
            if num_pages > 1:
                k = 0
                for page_num in range(num_pages):
                    for channel_num in range(num_channels):
                        k += 1
                        channel_data = read_plane(mask, path=file_path, scene=page_num, c=channel_num, t=0)
                        all_channels.append((channel_data, file_path, k))
            # If only one page, iterate over channels
            else: 
                for channel_num in range(num_channels):
                    channel_data = read_plane(mask, path=file_path, c=channel_num, t=0)
                    all_channels.append((channel_data, file_path, channel_num))

        # Check if there are multiple channels to assign names
        if len(all_channels) > 1:
            self.assign_channels_in_dialog(all_channels, is_mask=True)
        # If only one channel, default to 'Mask Layer'
        else:
            mask_image = all_channels[0][0]
            self.load_into_viewer(mask_image, name="Mask Layer", is_mask=True)

        
    def assign_channels_in_dialog(self, all_channels, is_mask=False, channel_info=None):
        """
        Displays a dialog for the user to assign names to each channel of an opened image or mask. This method aids in 
        organizing and identifying channels, especially when dealing with multichannel data.

        Parameters
        ----------
        all_channels : list
            A list of tuples, each containing channel data, the file path of the image or mask, and the channel number.
        is_mask : bool, optional
            Indicates whether the channels belong to a mask or an image, default is False (image).

        Notes
        -----
        This method facilitates better data management within the Napari viewer by allowing users to assign meaningful 
        names to various channels, enhancing the interpretability of multichannel datasets.
        """
        dialog = ChannelAssignmentDialog(all_channels, is_mask=is_mask, channel_info=channel_info)
        result = dialog.exec_()

        if result == QDialog.Accepted:
            # Get the names assigned by the user
            channel_names = [input_field.text() for input_field in dialog.channel_name_inputs]
        elif result == QDialog.Rejected:
            return # If the user cancels the dialog do nothing

        # Record the final channel_num -> layer_name assignment so batch
        # replay can recreate the exact same image-type-to-channel mapping.
        # Stored on self so open_image()'s bp.record call can include it.
        self._last_channel_assignment = []

        # Load each channel into the viewer with the assigned name
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
    

    def _add_diameter_annotation_layers(self):
        """Add the 'Object Diameter'/'Cell Diameter' line-annotation layers,
        seeded with one invisible near-zero-length line so the (otherwise empty)
        Shapes layers report a FINITE extent. An empty Shapes layer reports a NaN
        extent in this napari build, which makes reset_view (the Home button)
        compute a NaN camera zoom and crash the scale-bar overlay. The seed is
        ignored by calculate_length, which measures the last non-degenerate line.

        As of the drawing-layer rework these layers are created ON DEMAND by the
        measure widget (via pycat.toolbox.drawing_layers.add_drawing_layer) instead
        of eagerly at every file load, so a session that never measures diameters
        isn't cluttered with two annotation layers. The module flag
        EAGER_DIAMETER_LAYERS restores the old eager behaviour if needed (a one-line
        revert): when False (default) this method is a no-op at load time; the
        measure widget creates the seeded, tagged layers when first used.

        NOTE on the Home-button crash: the NaN-extent crash only occurs when an
        EMPTY Shapes layer is present. With eager creation off, no diameter layer
        exists until the user makes one (and the factory seeds it), so the interim
        is safe — the absence of a layer cannot NaN the extent.
        """
        if not EAGER_DIAMETER_LAYERS:
            return
        import numpy as _np
        for _nm, _ec, _ew in (('Object Diameter', 'red', 2),
                              ('Cell Diameter', 'white', 5)):
            if _nm in [l.name for l in self.viewer.layers]:
                continue
            lyr = self.viewer.add_shapes(name=_nm, shape_type='line',
                                         edge_color=_ec, edge_width=_ew)
            try:
                lyr.add(_np.array([[0.0, 0.0], [0.0, 1e-4]]),
                        shape_type='line', edge_width=0.0)
                lyr.current_edge_width = _ew
            except Exception:
                pass

    def _enable_auto_scale_bar(self, image_layer=None):
        """
        Enable napari's scale bar for a freshly-loaded image.

        - Real metadata pixel size  → µm bar: sets ``layer.scale`` (a display-only
          transform that never touches ``layer.data`` or any calculation).
        - No metadata               → pixel bar (scale left at 1).

        NEVER sets ``layer.units`` — that is the confirmed cause of the black
        canvas on lazy 3D stacks. The unit label comes from ``scale_bar.unit``.
        """
        try:
            import napari.layers as _nl
            dr = self.central_manager.active_data_class.data_repository
            from_meta = bool(dr.get('pixel_size_from_metadata', False))
            mpx_sq = dr.get('microns_per_pixel_sq', 1)
            if image_layer is None:
                imgs = [l for l in self.viewer.layers if isinstance(l, _nl.Image)]
                if not imgs:
                    return
                image_layer = imgs[-1]
            sb = self.viewer.scale_bar
            sb.visible = True
            # Show a µm bar whenever a real pixel size is known — from metadata
            # OR entered by the user (e.g. via the pixel-size gate). Only fall
            # back to pixels when no valid scale exists. A non-finite or non-
            # positive scale would make the world extent degenerate and, on
            # reset_view (the Home button), drive the camera zoom to NaN, which
            # crashes napari's scale-bar overlay — so we validate strictly.
            import numpy as _np
            try:
                mpx_sq = float(mpx_sq)
            except (TypeError, ValueError):
                mpx_sq = 1.0
            px = _np.sqrt(mpx_sq) if (_np.isfinite(mpx_sq) and mpx_sq > 0) else 0.0
            if _np.isfinite(px) and px > 0 and abs(px - 1.0) > 1e-9:
                sc = [float(s) for s in image_layer.scale]
                if all(_np.isfinite(s) and s > 0 for s in sc[:-2]) or len(sc) <= 2:
                    sc[-1] = px; sc[-2] = px
                    image_layer.scale = sc
                label = 'um'
            else:
                label = 'px'
            try:
                import warnings as _w
                with _w.catch_warnings():
                    _w.simplefilter('ignore', FutureWarning)
                    sb.unit = label
            except Exception:
                pass
            # Now that the reference image carries a µm scale, bring any layers
            # that were added earlier (e.g. the diameter overlays) into alignment.
            if label == 'um':
                self._align_layer_scales()
        except Exception as e:
            print(f"[PyCAT] auto scale bar skipped: {e}")

    def _update_scale_bar_for_active_layer(self):
        """Update the napari scale bar to reflect the physical pixel size of
        whichever Image layer is currently active (top of the selection).

        This fires on viewer.layers.selection.events.changed so switching to
        an upscaled layer (scale = source_scale / 2) shows the correct bar.

        Scale bar logic:
          • layer.scale[-1] is the physical size of one pixel in µm.
          • The bar length in world units is unchanged — what changes is the
            label. If the upscaled layer has scale 0.085 µm/px and the bar
            spans 588 pixels, it correctly shows ~50 µm, the same FOV as the
            original 294-px image at 0.17 µm/px. So the bar length is right;
            we just need to make sure the unit is 'um' when any valid µm scale
            is set on the active layer.
        """
        try:
            import napari.layers as _nl
            import numpy as _np
            import warnings as _w
            # Find the topmost selected Image layer
            sel = [l for l in self.viewer.layers.selection
                   if isinstance(l, _nl.Image)]
            if not sel:
                return
            # napari puts the most-recently-selected layer last in the set
            active = sel[-1]
            sc = [float(v) for v in active.scale]
            if not sc:
                return
            px = sc[-1]   # µm per pixel on the active layer
            sb = self.viewer.scale_bar
            if _np.isfinite(px) and px > 0 and abs(px - 1.0) > 1e-9:
                # Valid µm scale — show µm bar
                with _w.catch_warnings():
                    _w.simplefilter('ignore', FutureWarning)
                    sb.unit = 'um'
            else:
                # Unit or pixel scale — show px bar
                with _w.catch_warnings():
                    _w.simplefilter('ignore', FutureWarning)
                    sb.unit = 'px'
        except Exception:
            pass

    def load_into_viewer(self, data, name, is_mask=False):
        """
        Loads the given data into the Napari viewer, distinguishing between image and mask data, and applies appropriate 
        visual representations.

        Parameters
        ----------
        data : array-like
            The image or mask data to be loaded into the viewer.
        name : str
            The name to assign to the layer in the viewer.
        is_mask : bool, optional
            A flag indicating whether the data is a mask, defaults to False.

        Notes
        -----
        This method ensures that mask data is loaded as label layers and image data as image layers. It handles data type 
        conversions and scaling to optimize visualization within the Napari environment.
        """
        if is_mask:
            # If it's a mask, skip conversion to float and ensure it's int type
            if np.issubdtype(data.dtype, np.integer):
                data = data.astype(int) if not np.issubdtype(data.dtype, int) else data
            # Add the mask to the viewer
            self.viewer.add_labels(data, name=name)
            # Tag: this is a mask (role/provenance), 2D dimensionality.
            try:
                if len(self.viewer.layers):
                    _mpp = None
                    try:
                        _mps = self.central_manager.active_data_class.data_repository.get('microns_per_pixel_sq')
                        _mpp = (float(_mps) ** 0.5) if _mps else None
                    except Exception:
                        _mpp = None
                    self._tag_loaded_layer(
                        self.viewer.layers[-1], role='mask', n_t=1, n_z=1,
                        microns_per_pixel=_mpp, provenance='segmentation')
            except Exception as _e:
                debug_log("file_io: 2D mask tagging failed", _e)
        else:
            # Handle as before for images
            if np.issubdtype(data.dtype, np.integer):
                if np.issubdtype(data.dtype, np.signedinteger):
                    data = data.astype(np.uint16)
            elif np.issubdtype(data.dtype, np.floating):
                if np.max(data) > 1 or np.min(data) < 0:             
                    # For floating-point types, ensure values are between 0-1 and convert to float32
                    data = apply_rescale_intensity(data, out_min=0.0, out_max=1.0).astype(np.float32)
                else: 
                    data = data.astype(np.float32)
            data = dtype_conversion_func(data, 'float32')  # Ensure image data is correct float32 dtype
            # Add the image to the viewer
            add_image_with_default_colormap(data, self.viewer, name=name)
            # Stash the current file's metadata on the layer so a later
            # multi-image comparison can diff acquisition settings per-layer even
            # though data_repository['file_metadata'] is overwritten on each load.
            try:
                _md = self.central_manager.active_data_class.data_repository.get('file_metadata')
                if _md is not None and len(self.viewer.layers):
                    self.viewer.layers[-1].metadata['pycat_file_metadata'] = _md
            except Exception:
                pass
            # Tag: this is a 2D image (role/dimensionality/scale/provenance);
            # channel identity from the layer name (metadata-driven naming already
            # applied it upstream).
            try:
                if len(self.viewer.layers):
                    _mpp = None
                    try:
                        _mps = self.central_manager.active_data_class.data_repository.get('microns_per_pixel_sq')
                        _mpp = (float(_mps) ** 0.5) if _mps else None
                    except Exception:
                        _mpp = None
                    self._tag_loaded_layer(
                        self.viewer.layers[-1], role='image', n_t=1, n_z=1,
                        microns_per_pixel=_mpp,
                        channel=getattr(self.viewer.layers[-1], 'name', None),
                        provenance='raw')
            except Exception as _e:
                debug_log("file_io: 2D image tagging failed", _e)
            # Auto scale bar for the freshly-loaded 2D image.
            self._enable_auto_scale_bar()



    def _prompt_pixel_size_if_needed(self):
        """After a load, show the modal pixel-size dialog if the freshly-loaded
        image has no valid physical scale. Separate from the in-dock gate; both
        read/write the same data_repository scale so they stay consistent."""
        try:
            from pycat.ui.field_status import prompt_pixel_size_on_load
            prompt_pixel_size_on_load(
                lambda: self.central_manager.active_data_class.data_repository,
                central_manager=self.central_manager)
        except Exception:
            pass

    def _auto_clear_before_load(self):
        """Reset to the workflow start state before loading a new dataset.

        Returns True if it is safe to proceed with the load, False if the user
        declined to discard existing work.

        If no image layers are present, there is nothing to clear and we proceed
        immediately. If layers exist, we treat that as potentially-unsaved work
        and ask for confirmation (mirroring the Clear button's safety prompt)
        before wiping — so a new load never silently discards analysis. On
        confirmation we reuse _clear_everything, the same full reset the Clear
        button uses (layers, data repository, dataframes, workflow checklist,
        and batch recording), so the new dataset starts from a clean state.
        """
        try:
            has_layers = len(self.viewer.layers) > 0
        except Exception:
            has_layers = False
        if not has_layers:
            return True  # nothing to clear

        # There is existing work — confirm before discarding it.
        try:
            from qtpy.QtWidgets import QMessageBox
            resp = QMessageBox.question(
                None, "Load new image?",
                "Loading a new image will clear the current layers and reset the "
                "workflow.\n\nAny unsaved analysis will be lost. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if resp != QMessageBox.Yes:
                return False
        except Exception:
            # If the dialog can't be shown, err on the side of NOT destroying
            # work silently — proceed only if there were no layers (handled
            # above). Here layers exist, so bail out safely.
            return False

        try:
            self._clear_everything(self.viewer)
        except Exception:
            # If the reset fails, still allow the load to proceed (napari will
            # add the new layers alongside; not ideal but not destructive).
            pass
        return True

    def _clear_everything(self, viewer):
        """
        Reset the napari space to the workflow start state: remove all layers,
        reset the data repository/dataframes, and reset the workflow checklist
        progress bar. Saves nothing. Shared by Save & Clear's discard option and
        the top-bar Clear button.
        """
        # Drop the cached readers. They hold open file handles, and after a Clear the user is
        # done with those files. *(The cache exists because one drag-and-drop used to construct
        # the reader three to four times — see `image_reader._READER_CACHE`.)*
        try:
            from pycat.file_io.image_reader import clear_reader_cache
            clear_reader_cache()
        except Exception:
            pass

        self.viewer = viewer
        try:
            df_names = list(self.central_manager.active_data_class.get_dataframes().keys())
        except Exception:
            df_names = []
        _persist = getattr(self.central_manager, 'persist_measurements', False)
        _dr = self.central_manager.active_data_class.data_repository
        _saved = {}
        if _persist:
            _saved = {k: _dr.get(k) for k in
                      ('ball_radius', 'object_size', 'cell_diameter')
                      if _dr.get(k) is not None}
        viewer.layers.select_all()
        viewer.layers.remove_selected()
        self.central_manager.active_data_class.reset_values(
            clear_all=True, df_names_to_reset=df_names)
        # Dismiss any lingering napari notifications from the previous session.
        try:
            from napari.utils.notifications import notification_manager
            notification_manager.records.clear()
        except Exception:
            pass
        if _persist and _saved:
            _dr2 = self.central_manager.active_data_class.data_repository
            for k, v in _saved.items():
                try:
                    _dr2[k] = v
                except Exception:
                    pass
        # Reset the workflow checklist so the next dataset starts from step 1.
        try:
            wc = getattr(self.central_manager, 'workflow_checklist', None)
            if wc is not None:
                wc.reset()
        except Exception:
            pass

        # Reset the batch recording so the recorded-steps list starts empty for
        # the next dataset. The plain Clear button previously left the recording
        # intact (only Save & Clear reset it via terminate_recording); both paths
        # now reset it here. clear_recording() also flips the record toggle back
        # to OFF and resyncs the toolbar button.
        try:
            bp = getattr(self.central_manager, '_pycat_batch_processor', None)
            if bp is not None:
                bp.clear_recording()
        except Exception:
            pass

        # Reset the "Measure Line(s)" status circle back to red on clear, UNLESS
        # the user asked to remember measurements across clears (then the
        # measurement — and its done state — carries over).
        try:
            if not _persist:
                tb = getattr(self.central_manager, 'toolbox_functions_ui', None)
                mls = getattr(tb, '_measure_line_status', None)
                if mls is not None and hasattr(mls, 'reset'):
                    mls.reset()
        except Exception:
            pass

        # Reset the optional "Run Upscaling" status circle on clear (its upscaled
        # output layers are removed, so the step is no longer "done").
        try:
            tb = getattr(self.central_manager, 'toolbox_functions_ui', None)
            ups = getattr(tb, '_upscaling_status', None)
            if ups is not None and hasattr(ups, 'reset'):
                ups.reset()
        except Exception:
            pass

        # Re-show the pixel-size gate for the next dataset. Clearing wipes the
        # scale from the data repository, but the gate only re-evaluates on its
        # own triggers — call its reset so it reappears (honoring the persist /
        # "keep for session" checkbox, which retains the remembered value).
        try:
            pxr = getattr(self.central_manager, '_pixel_gate_refresh', None)
            if pxr is not None and hasattr(pxr, '_reset_gate'):
                pxr._reset_gate()
        except Exception:
            pass

    def clear_all_without_saving(self, viewer, confirm=True):
        """
        Clear all layers and data without saving, resetting the workspace to the
        beginning-of-workflow (startup) state. If `confirm` is True, asks for
        explicit confirmation first and warns that all unsaved data will be lost.
        """
        if confirm:
            reply = QMessageBox.warning(
                None, "Clear everything without saving?",
                "This resets the workspace to the start of a workflow.\n\n"
                "All layers and analysis data will be permanently cleared and "
                "NOTHING will be saved. All unsaved data will be lost.\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        self._clear_everything(viewer)
        print("[PyCAT] Workspace cleared without saving.")

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

        # Get the names of all layers in the viewer
        layer_names = [layer.name for layer in self.viewer.layers]

        # Suppress specific skimage warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            
            # Save only the selected layers based on their names
            for layer_name in selected_layers:
                if layer_name in layer_names:
                    layer = self.viewer.layers[layer_name]
                    layer_data = layer.data
                    layer_type = type(layer).__name__
                    safe_name  = layer_name.replace(' ', '_').lower()
                    # Pull the layer's tag store so tags persist through the save.
                    _tag_store = None
                    try:
                        _md = getattr(layer, 'metadata', None)
                        if isinstance(_md, dict):
                            _tag_store = _md.get('pycat_tags')
                    except Exception:
                        _tag_store = None
                    self._save_layer(layer_data, layer_type,
                                     save_name, safe_name, tag_store=_tag_store)
            
            # Save only the selected dataframes
            dataframes_to_save = self.central_manager.active_data_class.get_dataframes()
            clear_dfs_list = []
            for df_name, df_value in dataframes_to_save.items():
                clear_dfs_list.append(df_name)
                if df_name in selected_dataframes:
                    df_value.to_csv(save_name + f'_{df_name}.csv', index=True)

            # Export the file's normalised acquisition metadata alongside the
            # results, for provenance/reproducibility. Written once per save.
            try:
                _md = self.central_manager.active_data_class.data_repository.get('file_metadata')
                if _md:
                    import json as _json
                    with open(save_name + '_metadata.json', 'w', encoding='utf-8') as _mf:
                        _json.dump(_md, _mf, indent=2, default=str)
            except Exception as _mde:
                debug_log("file_io: metadata JSON export failed", _mde)

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
        """
        Save a layer to disk, handling zarr-backed lazy stacks, regular
        numpy arrays, and label/shape layers.

        For 3D stacks (T, H, W) — whether backed by zarr, numpy, or any
        other lazy array — frames are written one at a time as a multi-page
        TIFF so the full stack is never held in RAM simultaneously.  This
        is essential for 600-frame 2048×2048 stacks that would otherwise
        require ~5 GB of RAM just for the save operation.

        tag_store : optional dict — the layer's pycat_tags store
        ({'tags':[...], 'edges':[...]}). Embedded in the TIFF description JSON
        alongside the image/mask signifier so tags survive save→reload.

        Naming convention
        -----------------
        2D image          → <save_name>_<layer>.tiff
        3D image stack    → <save_name>_<layer>_stack.tiff   (multi-page)
        Labels (2D)       → <save_name>_<layer>.png
        Labels (3D stack) → <save_name>_<layer>_masks.tiff  (multi-page)
        """
        import tifffile
        import json as _json

        # PyCAT signifier: a small JSON tag embedded in the TIFF ImageDescription
        # so a saved file can be re-loaded with its type known exactly (image vs
        # label mask) instead of guessing from pixel statistics. Read back by
        # add_image_or_mask / _read_pycat_signifier on load. The layer's tag store
        # (role/modality/lineage/etc.) rides in the same blob so tags persist.
        def _pycat_tag(kind):
            try:
                from pycat import __version__ as _ver
            except Exception:
                _ver = 'unknown'
            blob = {'pycat': True, 'kind': kind, 'pycat_version': _ver}
            if tag_store:
                try:
                    blob['pycat_tags'] = {
                        'tags': list(tag_store.get('tags', [])),
                        'edges': list(tag_store.get('edges', [])),
                    }
                except Exception:
                    pass
            return _json.dumps(blob, default=str)

        is_lazy = hasattr(data, '_z') or hasattr(data, 'store')  # _ZarrStack or zarr.Array

        # Materialise only what we need
        def _frame(t):
            f = data[t]
            return np.asarray(f).astype(np.float32) if layer_type == 'Image' else np.asarray(f)

        def _minimal_label_dtype(arr):
            """Smallest lossless integer dtype for a label/mask array.

            Everything used to be force-cast to uint16, which wastes headroom: a
            BINARY mask needs 1 bit and a 40-cell label mask needs 6. Compression
            hides most of that (the high byte is all zeros), but not all of it —
            measured ~1.3x on masks — and it is free to just not waste it.
            """
            a = np.asarray(arr)
            try:
                mx = int(a.max()) if a.size else 0
            except Exception:
                return np.uint16
            if mx <= 1:
                return np.uint8       # binary mask (TIFF has no 1-bit path here)
            if mx <= 255:
                return np.uint8       # up to 255 labels
            if mx <= 65535:
                return np.uint16
            return np.uint32          # >65k objects: don't silently wrap!

        def _to_label_array(arr):
            return np.asarray(arr).astype(_minimal_label_dtype(arr))

        def _to_uint16(arr):
            """Convert an IMAGE for saving without inventing precision.

            The previous version rescaled anything with max<=1.0 by 65535 and
            min-max stretched anything above 65535 — i.e. it CHANGED THE PIXEL
            VALUES, fabricating 16-bit precision for 8-bit data and silently
            renormalising floats. That is a correctness problem, not just a size
            one. Now: integer data is preserved as-is in the smallest lossless
            integer type, and float data is only converted when it is safe to do
            so (float images that are genuinely outside integer range keep their
            float dtype, handled by the caller).
            """
            a = np.asarray(arr)
            if np.issubdtype(a.dtype, np.integer):
                mx = int(a.max()) if a.size else 0
                if mx <= 255:
                    return a.astype(np.uint8)      # don't upcast 8-bit sources
                if mx <= 65535:
                    return a.astype(np.uint16)
                return a.astype(np.uint32)
            # Floating point: preserve values; only narrow when lossless.
            af = a.astype(np.float32)
            finite = af[np.isfinite(af)] if af.size else af
            if finite.size and float(np.min(finite)) >= 0:
                mx = float(np.max(finite))
                # Integral-valued floats (e.g. a mask or a counted image) can be
                # stored exactly as ints.
                if np.all(finite == np.rint(finite)):
                    if mx <= 255:
                        return af.astype(np.uint8)
                    if mx <= 65535:
                        return af.astype(np.uint16)
            # Genuine continuous float data — keep float32 rather than quantising
            # it (quantising is lossy and the old code did it silently).
            return af

        if layer_type in ('Labels',):
            if hasattr(data, 'shape') and len(data.shape) == 3:
                # 3D label stack (e.g. TS Cell Masks) → compressed multi-page TIFF.
                #
                # Masks are the bulk of a PyCAT project's disk usage and they
                # compress enormously (a 1024² uint16 label mask: 2.1 MB raw →
                # 13 kB zlib, ~160×, lossless, for ~7 ms). The stack MUST be
                # written in one imwrite call with the axis DECLARED:
                #   * per-frame writes with compression lose the series structure
                #     (imread collapses the stack to a single plane);
                #   * a whole-stack write without `axes` metadata produces an
                #     UNDECLARED 'Q' axis — the very case that makes PyCAT prompt
                #     "is this T or Z?" when reopening its own file (see 1.5.351).
                out_path = f"{save_name}_{safe_name}_masks.tiff"
                _axes = 'TYX'
                try:
                    _dr = self.central_manager.active_data_class.data_repository
                    _lbl = str(_dr.get('stack_axis_label') or 'T').upper()
                    _axes = 'ZYX' if _lbl.startswith('Z') else 'TYX'
                except Exception:
                    pass
                _n, _h, _w = (int(data.shape[0]), int(data.shape[1]),
                              int(data.shape[2]))
                # Right-size the label dtype from the GLOBAL max across frames —
                # deciding from frame 0 alone would silently WRAP labels if a later
                # frame has more objects (e.g. 300 cells in frame 40 vs 200 in
                # frame 0). One streaming pass over max() is cheap next to the I/O.
                _gmax = 0
                for t in range(_n):
                    try:
                        _gmax = max(_gmax, int(np.asarray(data[t]).max()))
                    except Exception:
                        _gmax = 65535
                        break
                _dt = (np.uint8 if _gmax <= 255
                       else np.uint16 if _gmax <= 65535 else np.uint32)

                def _mask_frames():
                    for t in range(_n):
                        yield np.asarray(data[t]).astype(_dt)

                tifffile.imwrite(
                    out_path, _mask_frames(),
                    shape=(_n, _h, _w), dtype=_dt,
                    compression='zlib',
                    metadata={'axes': _axes},
                    description=_pycat_tag('mask'),
                    bigtiff=True)
                print(f"[PyCAT] Saved 3D label stack → {out_path} "
                      f"(compressed, axes={_axes}, {np.dtype(_dt).name}, "
                      f"max label {_gmax})")
            else:
                # 2D label mask → PNG (already compressed), right-sized: a binary
                # mask or a <256-object label image is uint8, not uint16.
                arr = _to_label_array(data)
                out_path = f"{save_name}_{safe_name}.png"
                sk.io.imsave(out_path, arr)

        elif layer_type == 'Shapes':
            arr = dtype_conversion_func(np.asarray(data), 'uint16')
            sk.io.imsave(f"{save_name}_{safe_name}.png", arr)

        elif layer_type == 'Image':
            ndim = data.shape[0] if hasattr(data, 'shape') else len(data)
            # Check if this is a (T, H, W) stack
            shape = data.shape if hasattr(data, 'shape') else None

            if shape is not None and len(shape) == 3 and not (
                shape[2] in (3, 4) and shape[0] < 10
            ):
                # 3D grayscale stack — compressed multi-page TIFF with the axis
                # DECLARED (see the label-stack note above: per-frame compressed
                # writes lose the series, and an undeclared axis reopens as 'Q').
                # Images compress far less than masks (typically 1.3–2×, since
                # they carry real noise), but it is free correctness and still a
                # saving; the mask paths are where the big win is.
                n_t = shape[0]
                out_path = f"{save_name}_{safe_name}_stack.tiff"
                print(f"[PyCAT] Saving {n_t}-frame stack to {out_path} …")
                _axes = 'TYX'
                try:
                    _dr = self.central_manager.active_data_class.data_repository
                    _lbl = str(_dr.get('stack_axis_label') or 'T').upper()
                    _axes = 'ZYX' if _lbl.startswith('Z') else 'TYX'
                except Exception:
                    pass
                # Stream the frames: imwrite accepts a generator with shape=/dtype=,
                # so we get compression + a declared axis WITHOUT materialising the
                # whole movie in RAM (the original per-frame writer streamed too).
                # The dtype is decided from the FIRST frame (right-sized, never
                # upcast) and used for the whole stack.
                _h, _w = int(shape[1]), int(shape[2])
                _probe = _to_uint16(_frame(0))
                _dt = _probe.dtype

                def _frames():
                    yield _probe
                    for t in range(1, n_t):
                        yield _to_uint16(_frame(t)).astype(_dt, copy=False)

                tifffile.imwrite(
                    out_path, _frames(),
                    shape=(n_t, _h, _w), dtype=_dt,
                    compression='zlib',
                    metadata={'axes': _axes},
                    description=_pycat_tag('image'),
                    bigtiff=True)
                print(f"[PyCAT] Saved stack → {out_path} "
                      f"(compressed, axes={_axes}, {_dt})")
            else:
                # 2D image or RGB
                arr = np.asarray(data)
                if arr.ndim == 2:
                    out_path = f"{save_name}_{safe_name}.tiff"
                    tifffile.imwrite(out_path, _to_uint16(arr),
                                     compression='zlib',
                                     description=_pycat_tag('image'))
                else:
                    out_path = f"{save_name}_{safe_name}.png"
                    sk.io.imsave(out_path, dtype_conversion_func(arr, 'uint8'))
        else:
            # Unknown — save raw (compressed: .npz costs nothing vs .npy)
            np.savez_compressed(f"{save_name}_{safe_name}.npz",
                                data=np.asarray(data))

    def determine_file_format_and_process_data(self, layer_type, data):
        """Legacy helper kept for compatibility; new code uses _save_layer."""
        if layer_type in ['Labels', 'Shapes']:
            return ".png", dtype_conversion_func(data, 'uint16')
        elif layer_type == 'Image':
            if data.ndim == 3:
                return ".png", dtype_conversion_func(data, 'uint8')
            else:
                return ".tiff", dtype_conversion_func(data, 'uint16')
        else:
            return ".dat", data
        
