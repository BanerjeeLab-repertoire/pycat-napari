"""
File I/O Test Module for PyCAT

This module contains test cases for the FileIOClass, which handles file input/output operations
in the PyCAT application. The tests verify proper initialization, data loading into the viewer,
file format determination, and save operations. It ensures reliable handling of different data
types including images, masks, and labels.

The test suite uses pytest fixtures to provide mock objects for the napari viewer and central
manager, allowing isolated testing of file I/O functionality without requiring actual GUI
components or file system operations.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import pytest
import numpy as np
from unittest.mock import Mock, patch
from skimage import io
import tempfile
import os

# Local application imports
from pycat.file_io.file_io import FileIOClass
from pycat.data.data_modules import BaseDataClass


@pytest.fixture
def mock_viewer():
    """
    Provide a mock napari viewer.

    This fixture creates a mock viewer object that can be used to verify
    viewer-related operations without requiring an actual napari instance.

    Returns
    -------
    Mock
        A mock object simulating a napari viewer.
    """
    return Mock()


@pytest.fixture
def mock_central_manager():
    """
    Provide a mock central manager with BaseDataClass.

    This fixture creates a mock central manager with an initialized BaseDataClass,
    simulating the basic functionality needed for file I/O operations.

    Returns
    -------
    Mock
        A mock object simulating the central manager with an active data class.
    """
    manager = Mock()
    manager.active_data_class = BaseDataClass()
    return manager


@pytest.fixture
def file_io(mock_viewer, mock_central_manager):
    """
    Provide a FileIOClass instance with mocked dependencies.

    This fixture creates a FileIOClass instance using mock objects for the viewer
    and central manager, allowing isolated testing of file I/O functionality.

    Parameters
    ----------
    mock_viewer : Mock
        The mock viewer instance.
    mock_central_manager : Mock
        The mock central manager instance.

    Returns
    -------
    FileIOClass
        An initialized FileIOClass instance with mock dependencies.
    """
    return FileIOClass(mock_viewer, mock_central_manager)


def test_basic_initialization(file_io):
    """
    Test that FileIOClass initializes correctly.

    This test verifies that a new FileIOClass instance is properly initialized
    with all required attributes set to their default values.

    Parameters
    ----------
    file_io : FileIOClass
        The FileIOClass instance provided by the file_io fixture.
    """
    assert file_io.viewer is not None
    assert file_io.central_manager is not None
    assert file_io.filePath == ""
    assert file_io.base_file_name == ""


def test_load_into_viewer_image(file_io):
    """
    Test loading image data into viewer.

    This test verifies that image data is correctly loaded into the viewer
    using the appropriate viewer method for image data.

    Parameters
    ----------
    file_io : FileIOClass
        The FileIOClass instance provided by the file_io fixture.

    Notes
    -----
    Tests float32 image data loading using add_image method.
    """
    # Test with float32 data
    test_data = np.random.rand(100, 100).astype(np.float32)
    file_io.load_into_viewer(test_data, "test_image", is_mask=False)
    file_io.viewer.add_image.assert_called_once()


def test_load_into_viewer_mask(file_io):
    """
    Test loading mask data into viewer.

    This test verifies that mask data is correctly loaded into the viewer
    using the appropriate viewer method for label data.

    Parameters
    ----------
    file_io : FileIOClass
        The FileIOClass instance provided by the file_io fixture.

    Notes
    -----
    Tests int32 mask data loading using add_labels method.
    """
    # Test with int data
    test_data = np.random.randint(0, 5, size=(100, 100), dtype=np.int32)
    file_io.load_into_viewer(test_data, "test_mask", is_mask=True)
    file_io.viewer.add_labels.assert_called_once()


def test_determine_file_format(file_io):
    """
    Test file format determination.

    This test verifies that the correct file format is determined based on
    the type of data being processed (labels, images, RGB images).

    Parameters
    ----------
    file_io : FileIOClass
        The FileIOClass instance provided by the file_io fixture.

    Notes
    -----
    Tests format determination for:
    - Label data (uint16)
    - Grayscale image data (float32)
    - RGB image data (float32)
    """
    # Test label data
    label_data = np.zeros((100, 100), dtype=np.uint16)
    ext, _ = file_io.determine_file_format_and_process_data("Labels", label_data)
    assert ext == ".png"
    
    # Test image data
    image_data = np.zeros((100, 100), dtype=np.float32)
    ext, _ = file_io.determine_file_format_and_process_data("Image", image_data)
    assert ext == ".tiff"
    
    # Test RGB image data
    rgb_data = np.zeros((100, 100, 3), dtype=np.float32)
    ext, _ = file_io.determine_file_format_and_process_data("Image", rgb_data)
    assert ext == ".png"


def test_save_operations(file_io, tmp_path):
    """
    Test save operations and data processing.

    This test verifies that different types of data are correctly processed
    and saved with appropriate file formats and data type conversions.

    Parameters
    ----------
    file_io : FileIOClass
        The FileIOClass instance provided by the file_io fixture.
    tmp_path : Path
        Pytest fixture providing a temporary directory path.

    Notes
    -----
    Tests saving operations for:
    - Label data (converted to uint16)
    - Grayscale image data (converted to uint16)
    - RGB image data (converted to uint8)
    """
    # Create test data
    image_data = np.random.rand(100, 100).astype(np.float32)
    mask_data = np.random.randint(0, 5, (100, 100), dtype=np.int32)
    rgb_data = np.random.rand(100, 100, 3).astype(np.float32)
    
    # Test different layer types and their processing
    test_cases = [
        ("Labels", mask_data, ".png"),
        ("Image", image_data, ".tiff"),
        ("Image", rgb_data, ".png")
    ]
    
    for layer_type, data, expected_ext in test_cases:
        # Get format and processed data
        ext, processed_data = file_io.determine_file_format_and_process_data(layer_type, data)
        
        # Check extension
        assert ext == expected_ext
        
        # Verify data type conversions
        if layer_type == "Labels":
            assert processed_data.dtype == np.uint16
        elif layer_type == "Image":
            if data.ndim == 3:  # RGB
                assert processed_data.dtype == np.uint8
            else:  # Grayscale
                assert processed_data.dtype == np.uint16
        
        # Test actual saving
        test_path = os.path.join(tmp_path, f"test{ext}")
        io.imsave(test_path, processed_data)
        assert os.path.exists(test_path)