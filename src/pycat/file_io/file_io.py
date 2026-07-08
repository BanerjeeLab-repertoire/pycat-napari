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
from aicsimageio import AICSImage
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

        # List all available layers with checkboxes
        layout.addWidget(QLabel("Select Layers to Save:"))
        self.layer_checkboxes = {}
        # Create checkboxes for each layer
        for layer in self.layers:
            checkbox = QCheckBox(layer.name)
            self.layer_checkboxes[layer.name] = checkbox  # Use dictionary assignment instead of append
            layout.addWidget(checkbox)

            # List of default checked layer names
            default_checked_layers = [
                "Labeled Cell Mask", 
                "Cell Labeled Puncta Mask", 
                "Overlay Image", 
                "Pre-Processed Fluorescence Image"
            ]

            # Set the default state of some checkboxes
            if layer.name in default_checked_layers:
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
        with self._ctx():
            arr = np.stack(
                [np.asarray(self._z[t, self._c, 0]).astype(np.float32)
                 for t in range(self.shape[0])], axis=0)
        return arr if dtype is None else arr.astype(dtype)

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
        arr = np.stack([self._read_frame(t) for t in range(self.shape[0])], axis=0)
        return arr if dtype is None else arr.astype(dtype)

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
        arr = np.stack([self._read_plane(z) for z in range(self.shape[0])], axis=0)
        return arr if dtype is None else arr.astype(dtype)

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
        arr = self[:, :, :, :]
        return arr if dtype is None else arr.astype(dtype)

    def __len__(self):
        return self.shape[0]


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
        self._tif    = _tf.TiffFile(tiff_path)
        # Prefer the SERIES page sequence: for a Micro-Manager MMStack split
        # across sibling files (_1.ome.tif, _2.ome.tif, …) the OME series spans
        # the whole set, whereas TiffFile.pages only sees this one file. Fall
        # back to this file's pages if the series is unavailable.
        try:
            self._pages = self._tif.series[0].pages
        except Exception:
            self._pages = self._tif.pages
        self._nc     = max(1, int(n_channels))
        self._ci     = int(channel_idx)
        self.shape   = (int(n_frames), int(H), int(W))
        self.dtype   = np.dtype('float32')
        self.ndim    = 3

    def _page_index(self, t):
        # Interleaved channels are stored as consecutive pages per timepoint.
        return int(t) * self._nc + self._ci

    def _read_frame(self, t):
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
        # Deliberately read only the FIRST frame if napari asks for an array
        # (e.g. thumbnail) — never materialise the whole stack. Analysis code
        # that needs every frame must call as_full_array() (or index frames),
        # NOT np.asarray(), which would otherwise silently get a 2D single
        # frame and mis-detect the stack as non-3D.
        arr = np.asarray(self._pages[self._page_index(0)].asarray()).astype(np.float32)
        return arr if dtype is None else arr.astype(dtype)

    def as_full_array(self, dtype=np.float32):
        """Materialise the whole stack as a real (T, H, W) numpy array, read
        one frame at a time. Use this for analysis that needs every frame — it
        avoids the deliberately-truncated __array__ (which returns only frame 0
        to keep napari's incidental array requests cheap)."""
        out = np.empty(self.shape, dtype=dtype)
        for t in range(self.shape[0]):
            out[t] = self._read_frame(t).astype(dtype)
        return out

    def __len__(self):
        return self.shape[0]

    def transpose(self, *axes):
        return self.__getitem__(0)[np.newaxis]

    def close(self):
        try:
            self._tif.close()
        except Exception:
            pass


def materialize_stack(stack_like, dtype=np.float32):
    """Return a real (T, H, W) numpy array from any stack-like layer data.

    Handles PyCAT's lazy wrappers (_TiffPageStack, _ZarrTYX_generic, IMS
    readers) whose __array__ is deliberately truncated to one frame for napari's
    sake, plus plain numpy/dask arrays. Analysis code that needs every frame
    should call THIS, not np.asarray(layer.data) — the latter can silently
    return a single 2D frame from a lazy wrapper and make a (T,H,W) stack look
    2D (breaking shape checks and per-frame analysis).
    """
    # Lazy wrappers expose as_full_array() or are safely indexable by frame.
    if hasattr(stack_like, 'as_full_array'):
        return stack_like.as_full_array(dtype=dtype)
    # dask
    if hasattr(stack_like, 'compute'):
        return np.asarray(stack_like.compute()).astype(dtype)
    arr = np.asarray(stack_like)
    # If __array__ truncated a 3D wrapper to 2D but it advertises a 3D shape,
    # rebuild by indexing frames.
    shp = getattr(stack_like, 'shape', None)
    if arr.ndim == 2 and shp is not None and len(shp) == 3:
        out = np.empty(shp, dtype=dtype)
        for t in range(shp[0]):
            out[t] = np.asarray(stack_like[t]).astype(dtype)
        return out
    return arr.astype(dtype)


def iter_frames(stack_like, dtype=np.float32, indices=None):
    """Yield frames of a (T, H, W) stack ONE AT A TIME, without ever holding
    the whole stack in memory.

    This is the streaming counterpart to materialize_stack(): use it for
    per-frame analysis (e.g. bead detection) that only needs one frame at a
    time, so a long movie never has to be fully materialised. Handles PyCAT's
    lazy wrappers (_TiffPageStack, _ZarrTYX_generic, IMS readers) by indexing
    them frame-by-frame, dask arrays by computing one frame at a time, and plain
    numpy arrays by iterating rows of the T axis. A 2D input yields a single
    frame.

    Parameters
    ----------
    stack_like : array-like or lazy wrapper with .shape and __getitem__.
    dtype : frames are returned as this dtype.
    indices : optional iterable of frame indices to yield (e.g. a keyframe
        subset). If None, all frames are yielded in order.

    Yields
    ------
    (t, frame) : the frame index and the 2D frame as a numpy array.
    """
    shp = getattr(stack_like, 'shape', None)
    # 2D single frame
    if shp is not None and len(shp) == 2:
        yield 0, np.asarray(stack_like).astype(dtype)
        return

    if shp is not None and len(shp) == 3:
        n = shp[0]
        idxs = range(n) if indices is None else list(indices)
        is_dask = hasattr(stack_like, 'compute') and not hasattr(stack_like, 'as_full_array')
        for t in idxs:
            if t < 0 or t >= n:
                continue
            frame = stack_like[t]
            if is_dask and hasattr(frame, 'compute'):
                frame = frame.compute()
            yield int(t), np.asarray(frame).astype(dtype)
        return

    # Fallback: unknown shape — materialise once and iterate (last resort).
    arr = materialize_stack(stack_like, dtype=dtype)
    if arr.ndim == 2:
        arr = arr[np.newaxis, ...]
    idxs = range(arr.shape[0]) if indices is None else list(indices)
    for t in idxs:
        if 0 <= t < arr.shape[0]:
            yield int(t), arr[t]


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
        arr = np.asarray(self._z).astype(np.float32)
        return arr if dtype is None else arr.astype(dtype)

    def __len__(self):
        return self.shape[0]

    def transpose(self, *axes):
        return np.asarray(self._z[0]).astype(np.float32)[np.newaxis]


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

    def open_2d_image(self, file_paths=None):
        """
        Opens a dialog for selecting and opening 2D image files. Supports multiple file formats and handles multichannel 
        images by assigning channels through a dialog. The method updates the Napari viewer with the opened images and 
        integrates image metadata into the provided data instance for subsequent analysis.

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
        if not self._auto_clear_before_load():
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
                image = AICSImage(file_path)
                # Access only the dask-backed metadata — does not read pixel data
                _ = image.xarray_dask_data.dims
            except AttributeError as _e:
                if "newbyteorder" not in str(_e):
                    raise
                _use_fallback = True
                print(f"[PyCAT] NumPy 2.0 tifffile fallback for {os.path.basename(file_path)}")
            except Exception:
                # Any other error on metadata access — try normal path anyway
                image = AICSImage(file_path)

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

            image = AICSImage(file_path)
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
                        channel_data = image.get_image_data("YX", C=channel_num, S=page_num, T=0)
                        all_channels.append((channel_data, file_path, k))
            # If only one page, iterate over channels
            else: 
                for channel_num in range(num_channels):
                    channel_data = image.get_image_data("YX", C=channel_num, T=0)
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



    def open_stack(self, file_path=None):
        """
        Open any supported multi-frame image file as a lazy (T, Y, X) or
        (Z, Y, X) stack — one layer per channel — without loading the full
        array into memory.

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
            try:
                from aicsimageio import AICSImage as _AICSImage
                AICSImage = _AICSImage
            except ImportError:
                raise RuntimeError(
                    "aicsimageio is required to open TIFF/CZI stacks. "
                    "Install with: pip install aicsimageio"
                )
            image = AICSImage(file_path)
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
            # Fallback: tifffile direct read (no scene/T/Z metadata available)
            import tifffile
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
                    _stack_layer = self.viewer.add_image(wrapper, name=layer_name,
                                          colormap=_ch_colormap)
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
                    frame = image.get_image_data(
                        'YX', C=channel_idx, T=0, Z=0).astype(np.float32)
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
                            wrapper = _TiffPageStack(
                                file_path, n_t, H, W, dask_arr.dtype,
                                channel_idx=channel_idx, n_channels=n_c)
                            # Sanity check: the page count must be consistent
                            # with (frames x channels). If a multi-channel MM
                            # file uses a page order we don't model, fall back to
                            # the AICSImage reader rather than show wrong frames.
                            _npages = len(wrapper._pages)
                            if n_c > 1 and _npages < n_t * n_c:
                                wrapper.close()
                                wrapper = None
                        except Exception as _te:
                            debug_log("file_io: tifffile page reader failed, "
                                      "using AICSImage dask wrapper", _te)
                            wrapper = None
                    if wrapper is None:
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
                    dask_arr = image.get_image_dask_data('ZYX', C=channel_idx)
                    wrapper = _ZarrTYX_generic(dask_arr)
                    self._stack_lazy_refs.append((image, dask_arr))
                    _stack_layer = self.viewer.add_image(wrapper, name=layer_name,
                                          colormap=_ch_colormap)
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
                    dask_arr = image.get_image_dask_data('TZYX', C=channel_idx)
                    z = _zarr.open(zarr_path, mode='w', shape=dask_arr.shape,
                                   chunks=(1, 1, H, W), dtype=np.float32)
                    for t in range(n_t):
                        for zi in range(n_z):
                            z[t, zi] = np.asarray(dask_arr[t, zi]).astype(np.float32)
                    self._stack_zarr_paths.append(zarr_path)
                    wrapper = _ZarrTZYX_generic(_zarr.open(zarr_path, mode='r'))
                    _stack_layer = self.viewer.add_image(wrapper, name=layer_name,
                                          colormap=_ch_colormap)
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

        self._add_diameter_annotation_layers()

        # Auto scale bar for the freshly-loaded stack.
        self._enable_auto_scale_bar()

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

    def open_2d_mask(self):
        """
        Opens a dialog for selecting and opening mask files. This method is similar to `open_2d_image` but is specifically 
        tailored for mask files, supporting operations such as assigning channels to masks if the mask file contains 
        multiple channels.

        Notes
        -----
        The method supports a variety of file formats for masks, including TIFF, PNG, and JPG. It handles multichannel 
        masks by offering a dialog to assign specific channel roles, aiding in precise segmentation tasks.
        """
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
            mask = AICSImage(file_path)

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
                        channel_data = mask.get_image_data("YX", C=channel_num, S=page_num, T=0)
                        all_channels.append((channel_data, file_path, k))
            # If only one page, iterate over channels
            else: 
                for channel_num in range(num_channels):
                    channel_data = mask.get_image_data("YX", C=channel_num, T=0)
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
        ignored by calculate_length, which measures the last non-degenerate line."""
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
            # Auto scale bar for the freshly-loaded 2D image.
            self._enable_auto_scale_bar()



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
                    self._save_layer(layer_data, layer_type,
                                     save_name, safe_name)
            
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

    def _save_layer(self, data, layer_type: str, save_name: str, safe_name: str):
        """
        Save a layer to disk, handling zarr-backed lazy stacks, regular
        numpy arrays, and label/shape layers.

        For 3D stacks (T, H, W) — whether backed by zarr, numpy, or any
        other lazy array — frames are written one at a time as a multi-page
        TIFF so the full stack is never held in RAM simultaneously.  This
        is essential for 600-frame 2048×2048 stacks that would otherwise
        require ~5 GB of RAM just for the save operation.

        Naming convention
        -----------------
        2D image          → <save_name>_<layer>.tiff
        3D image stack    → <save_name>_<layer>_stack.tiff   (multi-page)
        Labels (2D)       → <save_name>_<layer>.png
        Labels (3D stack) → <save_name>_<layer>_masks.tiff  (multi-page)
        """
        import tifffile

        is_lazy = hasattr(data, '_z') or hasattr(data, 'store')  # _ZarrStack or zarr.Array

        # Materialise only what we need
        def _frame(t):
            f = data[t]
            return np.asarray(f).astype(np.float32) if layer_type == 'Image' else np.asarray(f)

        def _to_uint16(arr):
            arr = np.asarray(arr).astype(np.float32)
            mn, mx = arr.min(), arr.max()
            if mx <= 1.0:
                arr = arr * 65535
            elif mx > 65535:
                arr = (arr - mn) / (mx - mn + 1e-8) * 65535
            return arr.astype(np.uint16)

        if layer_type in ('Labels',):
            if hasattr(data, 'shape') and len(data.shape) == 3:
                # 3D label stack (e.g. TS Cell Masks) → multi-page TIFF
                out_path = f"{save_name}_{safe_name}_masks.tiff"
                with tifffile.TiffWriter(out_path, bigtiff=True) as tw:
                    for t in range(data.shape[0]):
                        tw.write(np.asarray(data[t]).astype(np.uint16),
                                 contiguous=True)
                print(f"[PyCAT] Saved 3D label stack → {out_path}")
            else:
                arr = np.asarray(data).astype(np.uint16)
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
                # 3D grayscale stack — save as multi-page TIFF, one frame at a time
                n_t = shape[0]
                out_path = f"{save_name}_{safe_name}_stack.tiff"
                print(f"[PyCAT] Saving {n_t}-frame stack to {out_path} …")
                with tifffile.TiffWriter(out_path, bigtiff=True) as tw:
                    for t in range(n_t):
                        tw.write(_to_uint16(_frame(t)), contiguous=True)
                print(f"[PyCAT] Saved stack → {out_path}")
            else:
                # 2D image or RGB
                arr = np.asarray(data)
                if arr.ndim == 2:
                    out_path = f"{save_name}_{safe_name}.tiff"
                    tifffile.imwrite(out_path, _to_uint16(arr))
                else:
                    out_path = f"{save_name}_{safe_name}.png"
                    sk.io.imsave(out_path, dtype_conversion_func(arr, 'uint8'))
        else:
            # Unknown — save raw
            np.save(f"{save_name}_{safe_name}.npy", np.asarray(data))

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
        
