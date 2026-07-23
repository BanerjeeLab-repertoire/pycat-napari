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
from pycat.ui.workflow_checklist import WorkflowChecklistManager
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

        # Listeners fired when the active data class is switched (e.g. pixel-size
        # gates that must re-evaluate whether the new data has a scale).
        self._data_switch_callbacks = []

        # ── The linked-selection dispatcher, application-wide ────────────────────────────
        #
        # One object is selected; every view that cares hears about it. This is `vpt_ui`'s proven
        # three-way dispatcher, generalised — it lived inside VPT with its view list hardcoded to
        # 'plot'|'image'|'table', so nothing else in PyCAT could join it.
        #
        # It sits beside `_data_switch_callbacks` because it is the same shape of thing: a place
        # views register to be told something changed. It holds its subscribers WEAKLY, though — a
        # plot dock that is closed must not be kept alive by having once wanted to hear.
        from pycat.utils.selection_service import SelectionService
        self.selection = SelectionService()

        # ── "Follow selection in viewer" — OFF, deliberately ─────────────────────────────
        #
        # When on, clicking a point in a plot moves the camera and jumps to that object's frame.
        # That is what brushing used to do **unconditionally**, and it is the "abrupt navigation"
        # complaint: you click a point to find out what it is, and the view you were reading leaves.
        #
        # Off is the honest default because the overlay already answers the question — the object is
        # outlined where it sits, so you can see *which* one it is without being taken there. Going
        # to it is a separate intention, and it has its own gestures: a double-click, or Reveal.
        #
        # Session-level, like `persist_measurements` — PyCAT has no preference persistence, and
        # inventing one for a checkbox would be its own piece of work.
        self.follow_selection = False

        # Session-level flag: if True, ball_radius / object_size / cell_diameter
        # are preserved across Save & Clear so the user doesn't need to re-measure
        # when processing a second image from the same experiment. Controlled by the
        # "Remember measurements across clears" checkbox in the Measure Line widget.
        self.persist_measurements = False

        # Initialize the component responsible for file input/output operations
        self.file_io = FileIOClass(self.viewer, self)
        
        # Initialize UI components to provide functionality and interactivity
        self.toolbox_functions_ui = ToolboxFunctionsUI(self.viewer, self)
        self.analysis_methods_ui = AnalysisMethodsUI(self.viewer, self)
        self.menu_manager = MenuManager(self.viewer, self)
        # These entry points are installed here rather than in the menu god-file (pinned at its line ceiling)
        # so each action lives beside the code it opens: the '⚙ Preferences' panel, and 'Manage local cache…'
        # in the File menu (the on-demand cache manager, now that startup only offers it non-blockingly).
        from pycat.ui.preferences_dialog import install_preferences_action
        self._preferences_action = install_preferences_action(self.viewer)
        from pycat.file_io.local_cache import install_cache_menu_action
        self._cache_menu_action = install_cache_menu_action(
            getattr(self.menu_manager, 'file_menu', None),
            getattr(getattr(self.viewer, 'window', None), '_qt_window', None))
        # Navigator: guided-analysis dock (question flow -> quality-gated, editable plan). Entry point here,
        # not the line-capped menu god-file. on_run is left None until the plan-execution bridge lands.
        from pycat.ui.navigator_dock import install_navigator_action
        self._navigator_action = install_navigator_action(self.viewer)

        # Connect viewer layer selection changes to update the UI tools appropriately
        self.viewer.layers.selection.events.changed.connect(self.toolbox_functions_ui.update_tool)

        # Workflow checklist — activated when user switches to an analysis mode
        self.workflow_checklist = WorkflowChecklistManager(self.viewer)

    def register_data_switch_callback(self, cb):
        """Register a zero-arg callable fired whenever the active data class is
        switched. Used by pixel-size gates to re-check the new data's scale."""
        lst = getattr(self, '_data_switch_callbacks', None)
        if lst is None:
            lst = self._data_switch_callbacks = []
        if cb not in lst:
            lst.append(cb)

    def notify_data_changed(self):
        """Fire the registered data-switch callbacks WITHOUT changing the active
        data class. A plain image load does not switch the data class, so gates
        (e.g. the pixel-size gate) would otherwise never re-evaluate after a
        file is opened. The file loader calls this once the new image and its
        metadata (including pixel size) are in the data repository, so the gate
        re-checks the freshly-loaded scale and shows/hides accordingly."""
        for cb in list(getattr(self, '_data_switch_callbacks', [])):
            try:
                cb()
            except Exception:
                pass

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
            # Notify any registered listeners (e.g. pixel-size gates) that the
            # active data changed, so they can re-evaluate visibility/scale.
            for cb in list(getattr(self, '_data_switch_callbacks', [])):
                try:
                    cb()
                except Exception:
                    pass
