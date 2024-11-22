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

            # Open the image using AICSImage package
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
        
