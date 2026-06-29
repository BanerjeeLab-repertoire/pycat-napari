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
import warnings

# Third party imports
import numpy as np
import skimage as sk
from aicsimageio import AICSImage
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QCheckBox, QRadioButton, QPushButton, QFileDialog, QLineEdit
from napari.utils.notifications import show_warning as napari_show_warning

# Local application imports
from pycat.ui.ui_utils import add_image_with_default_colormap
from pycat.utils.general_utils import dtype_conversion_func
from pycat.toolbox.image_processing_tools import apply_rescale_intensity



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
    def __init__(self, channels, is_mask=False, parent=None):
        """
        Initializes the dialog with the provided channels, setting up the UI for channel naming.
        """
        super().__init__(parent)
        self.channels = channels
        self.is_mask = is_mask
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

            # Set the default name based on the file path or a generic naming convention
            if not self.is_mask:
                if channel_num == 0:
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

    def open_2d_image(self):
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
        options = QFileDialog.Options()
        file_paths, _ = QFileDialog.getOpenFileNames(None, "Open File(s)", "", "Image Files (*.tiff *.tif *.czi *.png);;All Files (*)", options=options)

        # Check if any files were selected
        if not file_paths: 
            return

        all_channels = [] # Create a list to store all channels for multichannel images

        for file_path in file_paths:
            # Setting the filePath variable and base file name
            self.filePath = file_path  
            self.base_file_name = os.path.splitext(os.path.basename(file_path))[0]

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

        # Check if there are multiple channels to assign names
        if len(all_channels) > 1:
            self.assign_channels_in_dialog(all_channels)
        # If only one channel, default to 'Fluorescence Image'
        else:
            fluorescence_image = all_channels[0][0]
            self.load_into_viewer(fluorescence_image, name="Fluorescence Image")

        # Add layers for measuring object and cell diameters to the viewer based on the image size
        self.viewer.add_shapes(name='Object Diameter', shape_type='line', edge_color='red', edge_width=2)
        self.viewer.add_shapes(name='Cell Diameter', shape_type='line', edge_color='white', edge_width=5)

        # Update the data instance with default sizes for object and cell diameters
        self.central_manager.active_data_class.data_repository['object_size'] = channel_data.shape[0] // 20
        self.central_manager.active_data_class.data_repository['cell_diameter'] = channel_data.shape[0] // 8

        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record('open_image', {
                'file_path': self.filePath,
                'cell_diameter': self.central_manager.active_data_class.data_repository.get('cell_diameter', 100),
                'ball_radius': self.central_manager.active_data_class.data_repository.get('ball_radius', 50),
            })



    def open_image_stack(self):
        """
        Opens a single multi-frame TIFF file as a (T, H, W) or (T, C, H, W)
        image stack and adds it to the napari viewer as a 3D image layer.

        Intended for time-series or z-stack data used in the Time-Series
        Condensate Analysis tool.  Unlike open_2d_image, this method preserves
        the time/z dimension and adds the full stack as a single napari layer.

        Metadata (pixel size) and diameter defaults are still populated in
        the active data instance so downstream tools work correctly.
        """
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(
            None, "Open Image Stack (T/Z)",
            "",
            "Image Files (*.tiff *.tif *.czi);;All Files (*)",
            options=options,
        )
        if not file_path:
            return

        self.filePath = file_path
        self.base_file_name = os.path.splitext(os.path.basename(file_path))[0]

        # Try AICSImage first; fall back to tifffile on NumPy 2.0 environments
        _use_fallback = False
        try:
            image = AICSImage(file_path)
            _ = image.dims
        except AttributeError as _e:
            if "newbyteorder" not in str(_e):
                raise
            _use_fallback = True
            print(f"[PyCAT] NumPy 2.0 tifffile fallback for {os.path.basename(file_path)}")

        if _use_fallback:
            try:
                from PIL import Image as _PILImage
                import numpy as _np
                _pil = _PILImage.open(file_path)
                _frames = []
                try:
                    while True:
                        _frames.append(_np.array(_pil).astype('float32'))
                        _pil.seek(_pil.tell() + 1)
                except EOFError:
                    pass
                stack = _np.stack(_frames, axis=0)  # (T, H, W)
                from napari.utils.notifications import show_warning as _warn
                _warn(
                    f"{os.path.basename(file_path)} loaded via PIL fallback. "
                    "Run 'python fix_tifffile.py' to permanently fix NumPy 2.0 compatibility."
                )
            except Exception as _pil_e:
                from napari.utils.notifications import show_warning as _warn
                _warn(
                    f"Could not load {os.path.basename(file_path)}: "
                    "NumPy 2.0 / tifffile conflict. Run 'python fix_tifffile.py' to fix."
                )
                print(f"[PyCAT] PIL stack fallback failed: {_pil_e}")
                return
        else:
            self.central_manager.active_data_class.update_metadata(image)

            # Get time and channel dimensions
            n_t = getattr(image.dims, 'T', 1)
            n_c = getattr(image.dims, 'C', 1)
            n_z = getattr(image.dims, 'Z', 1)

            # Determine which dimension is the "stack" axis
            # Priority: T > Z (treat Z as stack if no time dimension)
            stack_dim = 'T' if n_t > 1 else 'Z'
            n_frames = n_t if n_t > 1 else n_z

            if n_frames == 1:
                from napari.utils.notifications import show_warning as napari_show_warning
                napari_show_warning(
                    "This file appears to be a single frame — use "
                    "'Open 2D Image(s)' instead for 2D images."
                )

            if n_c > 1:
                # Multi-channel stack: ask user which channel to load
                from PyQt5.QtWidgets import QInputDialog
                channel_names = [f"Channel {i}" for i in range(n_c)]
                choice, ok = QInputDialog.getItem(
                    None, "Select Channel",
                    f"This file has {n_c} channels. Select one to load as stack:",
                    channel_names, 0, False
                )
                if not ok:
                    return
                channel_idx = channel_names.index(choice)
                frames = []
                for t in range(n_frames):
                    kwargs = {'C': channel_idx, 'T': t} if stack_dim == 'T' else {'C': channel_idx, 'Z': t, 'T': 0}
                    frames.append(image.get_image_data("YX", **kwargs))
                stack = np.stack(frames, axis=0).astype('float32')
            else:
                # Single channel — load all frames
                frames = []
                for t in range(n_frames):
                    kwargs = {'C': 0, 'T': t} if stack_dim == 'T' else {'C': 0, 'Z': t, 'T': 0}
                    frames.append(image.get_image_data("YX", **kwargs))
                stack = np.stack(frames, axis=0).astype('float32')

        layer_name = f"{self.base_file_name} Stack"
        self.viewer.add_image(stack, name=layer_name)

        # Update data instance with size defaults from the frame shape
        frame_h = stack.shape[1]
        self.central_manager.active_data_class.data_repository['object_size'] = frame_h // 20
        self.central_manager.active_data_class.data_repository['cell_diameter'] = frame_h // 8

        from napari.utils.notifications import show_info as napari_show_info
        napari_show_info(
            f"Loaded stack: {stack.shape[0]} frames, "
            f"{stack.shape[1]}×{stack.shape[2]} px  →  layer '{layer_name}'"
        )

        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record('open_image_stack', {
                'file_path': self.filePath,
                'n_frames': int(stack.shape[0]),
                'cell_diameter': self.central_manager.active_data_class.data_repository.get('cell_diameter', 100),
                'ball_radius': self.central_manager.active_data_class.data_repository.get('ball_radius', 50),
            })


    def open_ims_file(self):
        """
        Opens an Andor/Bitplane Imaris .ims file lazily using dask — only the
        frames napari actually displays are read from disk, keeping RAM usage
        minimal regardless of file size.

        The .ims format is HDF5-based with dimensions (T, C, Z, Y, X).
        For 2D time-series data (Z=1) this produces a lazy (T, H, W) dask
        array per channel, suitable for the Time-Series Condensate Analysis
        pipeline.  Frames are only read into memory when requested
        (e.g. when the time slider is moved or a frame is extracted).

        Requires:  pip install imaris-ims-file-reader hdf5plugin dask
        """
        try:
            from imaris_ims_file_reader.ims import ims as ImsReader
        except ImportError as _ie:
            from napari.utils.notifications import show_warning as napari_show_warning
            napari_show_warning(
                f"Missing dependency: {_ie}\n"
                "Install with:  pip install imaris-ims-file-reader hdf5plugin dask"
            )
            return

        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(
            None, "Open Andor/Imaris File",
            "",
            "Imaris Files (*.ims);;All Files (*)",
            options=options,
        )
        if not file_path:
            return

        self.filePath = file_path
        self.base_file_name = os.path.splitext(os.path.basename(file_path))[0]

        try:
            # Open with squeeze_output=False — always (T, C, Z, Y, X)
            reader = ImsReader(file_path, squeeze_output=False)
            n_t    = reader.TimePoints
            n_c    = reader.Channels
            shape  = reader.shape          # (T, C, Z, Y, X)
            n_z    = shape[2]
            H, W   = shape[3], shape[4]
            dtype  = reader.dtype

            print(f"[PyCAT IMS] {self.base_file_name}: "
                  f"T={n_t} C={n_c} Z={n_z} Y={H} X={W}  dtype={dtype}")

            # Physical pixel size
            microns_per_pixel = 1.0
            try:
                ext_x = (reader.read_numerical_dataset_attr('ExtMax0') -
                         reader.read_numerical_dataset_attr('ExtMin0'))
                microns_per_pixel = float(ext_x) / float(W)
            except Exception:
                pass

            # Channel selection
            if n_c > 1:
                from PyQt5.QtWidgets import QInputDialog
                channel_names = [f"Channel {i}" for i in range(n_c)]
                choice, ok = QInputDialog.getItem(
                    None, "Select Channel",
                    f"This file has {n_c} channels. Select one to load:",
                    channel_names, 0, False
                )
                if not ok:
                    return
                channel_idx = channel_names.index(choice)
            else:
                channel_idx = 0

            # ── Build lazy dask array — no data is read yet ──────────────
            # Each delayed task reads exactly one (H, W) frame on demand.
            frame_dtype = np.float32

            # Use a custom lazy array class that reads frames on demand
            # without dask — avoids the distributed/SSL crash on Windows.
            class _ImsLazyArray:
                """
                Numpy-compatible lazy array for IMS stacks that avoids dask.
                Napari probes shape/dtype, calls __getitem__ per frame, and
                also calls transpose() and __array__. All are handled here
                so napari gets real numpy arrays at every step.
                """
                def __init__(self, fp, n, c, z_idx, H, W):
                    self._fp = fp
                    self._n  = n
                    self._c  = c
                    self._z  = z_idx
                    self.shape = (n, H, W)
                    self.dtype = np.dtype('float32')
                    self.ndim  = 3

                def _read_frame(self, t):
                    r = ImsReader(self._fp, squeeze_output=False)
                    return r[int(t), self._c, self._z, :, :].astype(np.float32)

                def __len__(self):
                    return self._n

                def __getitem__(self, idx):
                    # Materialise to numpy immediately so napari can
                    # call transpose and any other numpy ops freely.
                    if isinstance(idx, tuple):
                        t_idx = idx[0]
                        spatial = idx[1:]
                    else:
                        t_idx = idx
                        spatial = (slice(None), slice(None))
                    if isinstance(t_idx, (int, np.integer)):
                        return self._read_frame(t_idx)[spatial]
                    # Slice of frames — materialise the requested range
                    t_range = range(*t_idx.indices(self._n))
                    frames = [self._read_frame(t) for t in t_range]
                    arr = np.stack(frames, axis=0)
                    return arr[(slice(None),) + spatial]

                def __array__(self, dtype=None):
                    frames = [self._read_frame(t) for t in range(self._n)]
                    arr = np.stack(frames, axis=0)
                    return arr if dtype is None else arr.astype(dtype)

                def transpose(self, *axes):
                    # Napari calls data.transpose(order) during slice setup.
                    # Materialise the full array and transpose it.
                    return np.asarray(self).transpose(*axes)

            if n_z == 1:
                layer_name = f"{self.base_file_name} [C{channel_idx}] Stack"
                if n_t == 1:
                    # Single frame — load eagerly as 2D
                    frame = reader[0, channel_idx, 0, :, :].astype(np.float32)
                    self.load_into_viewer(frame,
                                         name=f"{self.base_file_name} [C{channel_idx}]")
                    channel_data = frame
                else:
                    lazy_data = _ImsLazyArray(file_path, n_t, channel_idx, 0, H, W)
                    self.viewer.add_image(lazy_data, name=layer_name)
                    self._ims_lazy_ref = lazy_data
                    channel_data = reader[0, channel_idx, 0, :, :].astype(np.float32)
                    from napari.utils.notifications import show_info as napari_show_info
                    napari_show_info(
                        f"Lazy-loaded IMS time-series: {n_t} frames, "
                        f"{H}×{W} px → '{layer_name}' (frames read on demand)"
                    )
            else:
                from PyQt5.QtWidgets import QInputDialog
                t_choice, ok = QInputDialog.getInt(
                    None, "Select Timepoint",
                    f"This file has {n_t} timepoints and {n_z} Z slices.\n"
                    "Enter timepoint index to load (0-based):",
                    0, 0, n_t - 1, 1
                )
                if not ok:
                    t_choice = 0
                lazy_data = _ImsLazyArray(file_path, n_z, channel_idx, t_choice, H, W)
                layer_name = f"{self.base_file_name} [C{channel_idx}] T{t_choice}"
                self.viewer.add_image(lazy_data, name=layer_name)
                self._ims_lazy_ref = lazy_data
                channel_data = reader[t_choice, channel_idx, 0, :, :].astype(np.float32)
                from napari.utils.notifications import show_info as napari_show_info
                napari_show_info(
                    f"Lazy-loaded IMS z-stack: {n_z} slices, T={t_choice} "
                    f"→ '{layer_name}' (slices read on demand)"
                )

            # Update data instance
            self.central_manager.active_data_class.data_repository['object_size'] = H // 20
            self.central_manager.active_data_class.data_repository['cell_diameter'] = H // 8
            self.central_manager.active_data_class.data_repository['microns_per_pixel_sq'] = (
                microns_per_pixel ** 2
            )

            self.viewer.add_shapes(name='Object Diameter', shape_type='line',
                                   edge_color='red', edge_width=2)
            self.viewer.add_shapes(name='Cell Diameter', shape_type='line',
                                   edge_color='white', edge_width=5)

            bp = getattr(self.central_manager, '_pycat_batch_processor', None)
            if bp:
                bp.record('open_ims_file', {
                    'file_path': file_path,
                    'channel': channel_idx,
                    'n_timepoints': n_t,
                    'n_z': n_z,
                })

        except Exception as e:
            import traceback
            from napari.utils.notifications import show_warning as napari_show_warning
            napari_show_warning(f"Failed to load IMS file: {e}")
            print(f"[PyCAT IMS] Error:\n{traceback.format_exc()}")
            print(f"[PyCAT IMS] Error:\n{traceback.format_exc()}")

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

        
    def assign_channels_in_dialog(self, all_channels, is_mask=False):
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
        dialog = ChannelAssignmentDialog(all_channels, is_mask=is_mask)
        result = dialog.exec_()

        if result == QDialog.Accepted:
            # Get the names assigned by the user
            channel_names = [input_field.text() for input_field in dialog.channel_name_inputs]
        elif result == QDialog.Rejected:
            return # If the user cancels the dialog do nothing
        
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
            self.load_into_viewer(channel_data, name=name, is_mask=is_mask)
    

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
                    layer_data = self.viewer.layers[layer_name].data
                    layer_type = type(self.viewer.layers[layer_name]).__name__  # Gets the type of layer, like 'Labels' or 'Image'
                    file_extension, processed_data = self.determine_file_format_and_process_data(layer_type, layer_data)
                    sk.io.imsave(f"{save_name}_{layer_name.replace(' ', '_').lower()}{file_extension}", processed_data)
            
            # Save only the selected dataframes
            dataframes_to_save = self.central_manager.active_data_class.get_dataframes()
            clear_dfs_list = []
            for df_name, df_value in dataframes_to_save.items():
                clear_dfs_list.append(df_name)
                if df_name in selected_dataframes:
                    df_value.to_csv(save_name + f'_{df_name}.csv', index=True)

        # Clear all layers and dataframes from the viewer and data instance
        if clear_all:
            self.viewer.layers.select_all()
            self.viewer.layers.remove_selected()
            self.central_manager.active_data_class.reset_values(clear_all=True, df_names_to_reset=clear_dfs_list)
        # Clear only the saved layers and dataframes
        else:
            for layer_name in selected_layers:
                if layer_name in layer_names:
                    self.viewer.layers.remove(layer_name)
            self.central_manager.active_data_class.reset_values(df_names_to_reset=selected_dataframes)

    def determine_file_format_and_process_data(self, layer_type, data):
        """
        Determines the appropriate file format based on the layer type and processes the data to ensure compatibility 
        with the selected format. Supports image and label layer types.

        Parameters
        ----------
        layer_type : str
            The type of the layer, such as 'Image', 'Labels', or 'Shapes'.
        data : array-like
            The data associated with the layer to be processed and saved.

        Returns
        -------
        tuple
            A tuple containing the file extension as a string and the processed data ready for saving.

        Notes
        -----
        This method supports various formats, choosing PNG for labels and shapes for their lower resolution requirements 
        and TIFF or PNG for images depending on their dimensional properties. This ensures that data is saved in the most 
        appropriate format to maintain quality and usability.
        """
        if layer_type in ['Labels', 'Shapes']:  # Label layers are 16-bit int in Napari so we convert to uint16 and save as PNG
            return ".png", dtype_conversion_func(data, 'uint16')  
        elif layer_type == 'Image':
            if data.ndim == 3:  # RGB images are usually overlays and therefore dont need very high resolution
                return ".png", dtype_conversion_func(data, 'uint8')
            else:  # Regular 2D images are saved as 16 bit TIFF
                return ".tiff", dtype_conversion_func(data, 'uint16')
        else:  # Defaults to saving as raw data file if the layer type is not recognized, can be changed 
            return ".dat", data 
        
