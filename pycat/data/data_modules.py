"""
Data Module for PyCAT

This module contains classes and functions for managing and processing data within a biological
image analysis context, using napari. The primary components include BaseDataClass, which provides
basic functionalities for data handling and management, and AnalysisDataClass, which extends
BaseDataClass to cater specifically to puncta and cell data analysis.

This module is designed to be integrated with napari viewers to facilitate real-time data manipulation
and analysis, enhancing the workflow in biological research settings.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Standard library imports
import math
import copy

# Third party imports
import pandas as pd
import numpy as np
from scipy.spatial.distance import euclidean
from napari.utils.notifications import show_info as napari_show_info
from napari.utils.notifications import show_warning as napari_show_warning



class BaseDataClass:
    """
    A base class for managing data related to image analysis or similar scientific data processing applications. 
    It encapsulates operations for storing, retrieving, updating, and resetting data, especially focusing 
    on handling pandas DataFrames for analysis results alongside other types of metadata and parameters.

    Attributes
    ----------
    data_repository : dict
        A dictionary acting as a central repository for all data managed by instances of this class. 
        This includes pandas DataFrames for storing analysis results, numeric parameters for analysis, 
        and metadata.

    Methods
    -------
    set_data(self, key, data):
        Stores or updates data in the data repository under the specified key.

    get_data(self, key):
        Retrieves data stored in the data repository under the specified key.

    append_to_df(self, key, data):
        Appends a new row of data to a DataFrame stored under the specified key in the data repository.

    update_df(self, key, index, column, value):
        Updates a specific value in a DataFrame stored under the specified key in the data repository.

    add_column_to_df(self, key, column_name, default_value=None):
        Adds a new column to a DataFrame stored under the specified key in the data repository, initializing 
        all rows in this column to a default value.

    calculate_length(self, viewer):
        Calculates and updates the size parameters for objects and cells based on annotations made in 
        the napari viewer.

    get_dataframes(self):
        Retrieves all pandas DataFrames stored in the data repository.

    get_all_variables(self):
        Returns a list of all keys currently stored in the data repository.

    reset_values(self, df_names_to_reset=None, clear_all=False):
        Resets specified DataFrames or all data within the class to their default initialization values.
    """

    def __init__(self, base_data_repository=None):
        """
        Initializes the BaseDataClass with a default data repository containing empty pandas DataFrames
        for storing analysis results, default analysis parameters, and an empty metadata dictionary. It 
        can be with initialized with optional existing repository data. 
        """
        if base_data_repository:
            # Create a deep copy of the provided repository
            self.data_repository = copy.deepcopy(base_data_repository)
        else:
            self._initialize_repository()
        
    def _initialize_repository(self):
        """
        Initialize or reset the data repository with default values.
        Includes all necessary dataframes and parameters for analysis.
        """
        self.data_repository = {
            # Original BaseDataClass attributes
            'region_props_df': pd.DataFrame(),
            'generic_df': pd.DataFrame(),
            'object_size': 50,
            'cell_diameter': 100,
            'ball_radius': 75,
            'microns_per_pixel_sq': 1,
            'metadata': {},
            # Former AnalysisDataClass attributes
            'cell_df': pd.DataFrame(),
            'puncta_df': pd.DataFrame()
        }

    def set_data(self, key, data):
        """
        Stores or updates the specified data under the given key within the data repository. 
        This method is flexible and can be used to store various types of data, 
        from numeric values to complex objects.

        Parameters
        ----------
        key : str
            A unique identifier for the data being stored.
        data : object
            The data to be stored in the data repository under the specified key.

        Notes
        -----
        This method can be used to store a wide range of data types, including pandas DataFrames,
        numpy arrays, dictionaries, and other objects. The key should be a string that uniquely
        identifies the data being stored.
        """
        # Validation to ensure that the key exists and the data is of the correct type
        if self.data_repository[key].__class__ != data.__class__:
            napari_show_warning(f"Data type mismatch for key {key}.")
        # if self.data_repository[key] doesnt exist yet, create it
        elif key not in self.data_repository:
            self.data_repository[key] = data
        else:
            self.data_repository[key] = copy.deepcopy(data)

        #self._notify(f"Data {key} in data class has been set!")

    def get_data(self, key, default_value=None):
        """
        Retrieves the data stored under the specified key from the data repository. If the key does 
        not exist, `None` is returned. An optional default value can be provided to return when the key
        is not found.

        Parameters
        ----------
        key : str
            The unique identifier for the data to be retrieved.
        default_value : object, optional
            The default value to return if the key does not exist in the data repository. 
            The default is `None`.

        Returns
        -------
        object
            The data stored in the data repository under the specified key, or the default value if the key
            does not exist. If no default value is provided, `None` is returned when the key is not found.
        """
        return self.data_repository.get(key, default_value)

    def append_to_df(self, key, data: dict):
        """
        Appends a new row of data to the DataFrame identified by the given key. The new row of data 
        should be provided as a dictionary where keys correspond to column names.

        Parameters
        ----------
        key : str
            The key identifying the DataFrame to which the new row of data should be appended.
        data : dict
            A dictionary where keys represent column names and values represent the data to be appended.
        """

        # Check if the key exists and is a DataFrame
        if key in self.data_repository and isinstance(self.data_repository[key], pd.DataFrame):
            df = self.data_repository[key]
            new_row = pd.DataFrame([data])
            self.data_repository[key] = pd.concat([df, new_row], ignore_index=True)
            #self._notify(f"Data {key} in data class has been appended!")
        else:
            napari_show_warning(f"Key {key} does not exist or is not a DataFrame")

    def update_df(self, key, index, column, value):
        """
        Updates the value at a specified index and column in the DataFrame identified by the given key.

        Parameters
        ----------
        key : str
            The key identifying the DataFrame to be updated.
        index : int
            The index of the row to be updated.
        column : str
            The column name where the value should be updated.
        value : object
            The new value to be set at the specified index and column.
        """
        if key in self.data_repository and isinstance(self.data_repository[key], pd.DataFrame):
            self.data_repository[key].at[index, column] = value
            #self._notify(f"Data {key} in data class has been updated!")
        else:
            napari_show_warning(f"Key {key} does not exist or is not a DataFrame")

    def add_column_to_df(self, key, column_name, default_value=None):
        """
        Adds a new column to the DataFrame identified by the given key, initializing it with the 
        specified default value for all rows.

        Parameters
        ----------
        key : str
            The key identifying the DataFrame to which the new column should be added.
        column_name : str
            The name of the new column to be added.
        default_value : object, optional
            The default value to be assigned to all rows in the new column. If not provided, the 
            default value is `None`.
        """
        if key in self.data_repository and isinstance(self.data_repository[key], pd.DataFrame):
            self.data_repository[key][column_name] = default_value
            #self._notify(f"Column {column_name} added to {key} in data class!")
        else:
            napari_show_warning(f"Key {key} does not exist or is not a DataFrame")


    # Placeholders for possible future implmentation of observer notifications in the data class
    #def _notify(self, message):
    #    if self._data_manager:
    #        self._data_manager.notify_observers(message)

    def get_dataframes(self):
        """
        Retrieves and returns a dictionary of all pandas DataFrames currently stored in the data repository.
        """
        dataframes = {} # Create an empty dictionary to store DataFrames

        for attr_name, attr_value in self.data_repository.items(): # Iterate over all items in the data repository
            if isinstance(attr_value, pd.DataFrame): # Check if the attribute is a DataFrame
                dataframes[attr_name] = attr_value
                
        return dataframes
    
    def get_all_variables(self):
        """
        Returns a list of all keys representing the data currently stored in the data repository.
        """
        return list(self.data_repository.keys())
    

    def reset_values(self, df_names_to_reset=None, clear_all=False):
        """
        Resets specific DataFrames to empty DataFrames or clears all data within the class back to 
        default initialization values. This method can target specific DataFrames for resetting, 
        or reinitialize the class data repository entirely based on the parameters provided.

        Parameters
        ----------
        df_names_to_reset : list of str, optional
            A list of keys (string) identifying which DataFrames within the data repository should be 
            reset to their default empty state. This parameter is ignored if `clear_all` is True.

        clear_all : bool, optional
            A flag indicating whether to reset all data within the class to their default values. If True, 
            it overrides `df_names_to_reset` and reinitializes the entire data repository to default 
            values specified in the class constructor.

        Note
        ----
        This method selectively resets data based on provided parameters, allowing for flexible data 
        management within the class instance. 
        """

        # Reinitialize the entire data repository to default values if clear_all is True
        if clear_all:
            self.data_repository = None  # Force break any existing references
            self._initialize_repository() # Calls the class constructor to simply reinitialize to reset all values
        elif df_names_to_reset:
            # Loop through the list of DataFrame names provided for resetting
            for df_name in df_names_to_reset:
                if df_name in self.data_repository and isinstance(self.data_repository[df_name], pd.DataFrame):
                    # Reset the specified DataFrame to an empty DataFrame
                    self.data_repository[df_name] = pd.DataFrame()
                # Optionally, handle resetting other types of data based on key
                elif df_name in self.data_repository:
                    # Resetting non-DataFrame data to None or a default value
                    self.data_repository[df_name] = None
                else:
                    # Raise an error if a provided key does not exist in the data repository
                    napari_show_warning(f"Key '{df_name}' does not exist in the data repository.")

    def update_metadata(self, image):
        """
        Update metadata while preserving other state.

        Parameters
        ----------
        image : AICSImage
            Image object containing metadata to extract
        """
        #print("Update_metadata called on instance:", id(self))
        try:
            # Check if the image has multiple scenes (e.g., z-stack)
            num_scenes = len(image.scenes) 
            if num_scenes > 1:
                # Optionally, handle multiple scenes. Here, we'll use the first scene.
                scene = image.get_scene(0)
                physical_pixel_sizes = scene.physical_pixel_sizes
                metadata = scene.metadata
            else:
                physical_pixel_sizes = image.physical_pixel_sizes
                metadata = image.metadata

            # Safely extract X and Y resolutions
            x_resolution = getattr(physical_pixel_sizes, 'X', None)
            y_resolution = getattr(physical_pixel_sizes, 'Y', None)

            if x_resolution is not None and y_resolution is not None:
                self.data_repository['microns_per_pixel_sq'] = x_resolution * y_resolution
            else:
                napari_show_warning(
                    "Resolution data incomplete, using default value of 1 (um/px)^2."
                )
                self.data_repository['microns_per_pixel_sq'] = 1

            # Safely assign metadata
            if metadata:
                self.data_repository['metadata'] = metadata
            else:
                napari_show_warning(
                    "Metadata is empty or unavailable, using empty dictionary."
                )
                self.data_repository['metadata'] = {}

        except AttributeError as e:
            napari_show_warning(
                f"Attribute error encountered: {e}. Using default values."
            )
            self.data_repository['microns_per_pixel_sq'] = 1
            self.data_repository['metadata'] = {}
        except Exception as e:
            napari_show_warning(
                f"Unexpected error while updating metadata: {e}. Using default values."
            )
            self.data_repository['microns_per_pixel_sq'] = 1
            self.data_repository['metadata'] = {}


    def calculate_length(self, viewer):
        """
        Utilizes annotations made in a napari viewer to calculate and update size parameters for objects 
        and cells, such as diameters and radii. This method assumes specific naming conventions for 
        annotation layers within the viewer.

        Parameters
        ----------
        viewer : napari.Viewer
            A napari viewer instance containing annotations for calculating object and cell sizes.

        Notes
        -----
        This method is designed to work with specific annotation layers named 'Cell Diameter' and
        'Object Diameter' in the viewer, which are assumed to contain line annotations representing
        the diameters of cells and objects, respectively. The calculated sizes are stored in the
        data repository under the keys 'cell_diameter', 'object_size', and 'ball_radius'.
        """
        # Get the shapes layers
        cell_size_layer = viewer.layers['Cell Diameter'] if 'Cell Diameter' in viewer.layers else None
        object_size_layer = viewer.layers['Object Diameter'] if 'Object Diameter' in viewer.layers else None
        # Get the coordinates of the last shape drawn
        if cell_size_layer is not None:
            cell_coords = cell_size_layer.data[-1] if cell_size_layer.data else None
        else:
            cell_coords = None
        if object_size_layer is not None:
            object_coords = object_size_layer.data[-1] if object_size_layer.data else None
        else:
            object_coords = None

        # Calculate the distance using the Euclidean distance formula
        if cell_coords is not None:
            self.data_repository['cell_diameter'] = euclidean(cell_coords[0], cell_coords[1])
        else: 
            napari_show_warning(f"No cell diameter found, using default value. Please draw a line to measure the cell diameter.")
        if object_coords is not None:
            self.data_repository['object_size'] = euclidean(object_coords[0], object_coords[1])
            object_radius = self.data_repository['object_size'] / 2
            self.data_repository['ball_radius'] = math.ceil(1.5*object_radius)
        else: 
            napari_show_warning("No object diameter found, using default value. Please draw a line to measure the object diameter.")
        
        # Calculate the microns per pixel
        microns_per_pixel_sq = self.data_repository['microns_per_pixel_sq']
        microns_per_pixel = np.sqrt(microns_per_pixel_sq)
        round(microns_per_pixel, 2)
        #key = 'ball_radius'
        #self._notify(f"Data {key} in data class has been set!")
        # Print the length of the line
        napari_show_info("The diameter of the cell is: "
              f"{round(self.data_repository['cell_diameter']*microns_per_pixel, 2)}um ({round(self.data_repository['cell_diameter'], 2)}px)," 
              "the diameter of the object is: "
              f"{round(self.data_repository['object_size']*microns_per_pixel, 2)}um ({round(self.data_repository['object_size'], 2)}px)"
              )
        #napari_show_info("This is a message for Napari users!")
