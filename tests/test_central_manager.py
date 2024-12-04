"""
Central Manager Test Module for PyCAT

This module contains test cases for the CentralManager class, which serves as the core orchestrator
for the PyCAT application. The tests verify the proper initialization and functionality of the
CentralManager, ensuring it correctly manages interactions between various components including the
viewer, file I/O, data classes, and UI elements.

The test suite uses pytest fixtures to provide consistent test environments and validates both the
successful initialization of required components and proper handling of data class management. These
tests are crucial for maintaining the reliability of PyCAT's central coordination system.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import pytest
import napari

# Local application imports
from pycat.central_manager import CentralManager
from pycat.data.data_modules import BaseDataClass


@pytest.fixture
def viewer():
    """
    Provide a napari viewer instance for testing.

    This fixture creates a headless napari viewer instance that can be used
    consistently across tests without launching a GUI.

    Returns
    -------
    napari.Viewer
        A napari viewer instance with GUI disabled for testing purposes.
    """
    return napari.Viewer(show=False)


@pytest.fixture
def central_manager(viewer):
    """
    Provide a CentralManager instance for testing.

    This fixture creates a CentralManager instance with a test viewer,
    setting up the complete environment needed for testing central
    management functionality.

    Parameters
    ----------
    viewer : napari.Viewer
        The napari viewer instance provided by the viewer fixture.

    Returns
    -------
    CentralManager
        A fully initialized CentralManager instance ready for testing.
    """
    return CentralManager(viewer)


def test_central_manager_initialization(central_manager):
    """
    Test that CentralManager initializes with all required components.

    This test verifies that all essential components of the CentralManager
    are properly initialized and accessible. It checks for the presence of
    the viewer, file I/O system, active data class, UI components, and
    menu manager.

    Parameters
    ----------
    central_manager : CentralManager
        The CentralManager instance provided by the central_manager fixture.

    Notes
    -----
    The test ensures that:
    - All main components are initialized and not None
    - The active_data_class is of the correct type (BaseDataClass)
    """
    # Check that all main components are initialized
    assert central_manager.viewer is not None
    assert central_manager.file_io is not None
    assert central_manager.active_data_class is not None
    assert central_manager.toolbox_functions_ui is not None
    assert central_manager.analysis_methods_ui is not None
    assert central_manager.menu_manager is not None
    
    # Check that active_data_class is of correct type
    assert isinstance(central_manager.active_data_class, BaseDataClass)


def test_set_active_data_class(central_manager):
    """
    Test setting a new active data class.

    This test verifies the functionality of changing the active data class
    in the CentralManager. It tests both valid and invalid inputs to ensure
    proper handling of data class assignments.

    Parameters
    ----------
    central_manager : CentralManager
        The CentralManager instance provided by the central_manager fixture.

    Notes
    -----
    The test validates:
    - Successful assignment of a valid new data class
    - Proper handling of invalid input (maintains previous valid data class)
    """
    # Store reference to original data class for comparison
    original_data_class = central_manager.active_data_class
    
    # Test with valid input - create and set new data class
    new_data_class = BaseDataClass()
    central_manager.set_active_data_class(new_data_class)
    assert central_manager.active_data_class is new_data_class
    
    # Test with invalid input - should maintain previous valid data class
    central_manager.set_active_data_class("not a data class")
    assert central_manager.active_data_class is new_data_class