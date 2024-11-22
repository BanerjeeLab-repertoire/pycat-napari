"""
Central Manager Module for PyCAT

This module defines the CentralManager class, which acts as the central coordinating class for PyCAT.
The CentralManager integrates various components such as file input/output, data management, and user 
interface elements. It initializes and manages interactions between different parts of the application,
including UI components for basic functions, analysis methods, and a menu manager, facilitating a cohesive
user experience. 

It could also be used to relay changes in the program if an observer pattern is implemented in the future.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Local application imports
from pycat.file_io.file_io import FileIOClass
from pycat.data.data_modules import BaseDataClass
from pycat.ui.ui_modules import ToolboxFunctionsUI, AnalysisMethodsUI, MenuManager


class CentralManager:
    """
    Acts as the central coordinating class for PyCAT, integrating various components
    such as file input/output, data management, and user interface elements within a napari viewer context.
    
    The CentralManager initializes and manages interactions between different parts of the application,
    including UI components for basic functions, analysis methods, and a menu manager, facilitating a
    cohesive user experience.

    Attributes
    ----------
    viewer : napari.Viewer
        The napari viewer instance used for visualizing images and annotations.
    file_io : FileIOClass
        An instance of FileIOClass responsible for handling file input and output operations.
    active_data_class : BaseDataClass
        The current data class instance that holds and manages the application's data.
    toolbox_functions_ui : ToolboxFunctionsUI
        UI component for basic application functionalities.
    analysis_methods_ui : AnalysisMethodsUI
        UI component for executing specific analysis methods.
    menu_manager : MenuManager
        Manages the application's menu system within the napari viewer.
    """

    def __init__(self, viewer):
        """
        Initializes the CentralManager with a napari viewer instance and sets up the application's
        file IO, data management, and UI components.

        Parameters
        ----------
        viewer : napari.Viewer
            The napari viewer instance to be used by the application.
        """
        self.viewer = viewer
                
        # Set up the default data class for managing application data
        self.active_data_class = BaseDataClass()
        #print("CentralManager initial data class id:", id(self.active_data_class))

        # Initialize the component responsible for file input/output operations
        self.file_io = FileIOClass(self.viewer, self)
        
        # Initialize UI components to provide functionality and interactivity
        self.toolbox_functions_ui = ToolboxFunctionsUI(self.viewer, self)
        self.analysis_methods_ui = AnalysisMethodsUI(self.viewer, self)
        self.menu_manager = MenuManager(self.viewer, self)

        # Connect viewer layer selection changes to update the UI tools appropriately
        self.viewer.layers.selection.events.changed.connect(self.toolbox_functions_ui.update_tool)

    def set_active_data_class(self, data_class):
        """
        Sets the active data class instance, allowing for dynamic changes in data management strategies
        or structures during the application's runtime.

        Parameters
        ----------
        data_class : BaseDataClass or derived class instance
            An instance of BaseDataClass or a subclass thereof to be used as the new active data class.
        """
        #print("CentralManager setting data class id:", id(data_class))
        if isinstance(data_class, BaseDataClass):
            self.active_data_class = data_class
