"""
Image Processing Test Module for PyCAT

This module contains test cases for the image processing tools in PyCAT, specifically
focusing on the intensity rescaling functionality. This feature was chosen for its 
limited reliance on numerous imports, making the test more self-contained. This 
functionality serves as a basic test for the image processing tools and module. The 
tests verify proper handling of intensity transformations, ensuring that images are 
correctly rescaled to specified intensity ranges while preserving relative intensity 
relationships.

The test suite ensures reliable intensity rescaling, which is crucial for consistent
image analysis and visualization across different datasets and processing steps.

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

# Local application imports
from pycat.toolbox.image_processing_tools import apply_rescale_intensity


def test_rescale_intensity_basic():
    """
    Test basic intensity rescaling functionality.

    This test verifies that the intensity rescaling function correctly transforms
    an input array to the full range of the output data type, preserving relative
    intensity differences.

    Notes
    -----
    The test validates:
    - Correct output data type
    - Proper scaling to full range of uint8 (0 to 255)
    - Preservation of relative intensity scaling
    """
    # Create a test array with known values
    test_array = np.array([0, 50, 100, 150, 200], dtype=np.uint8)
    
    # Test rescaling to [0, 1] range
    result = apply_rescale_intensity(test_array)
    
    # Check that output matches input dtype and is properly scaled
    assert result.dtype == test_array.dtype
    assert result.min() == 0
    assert result.max() == 255  # For uint8, max value is 255
    
    # Check that relative scaling is preserved
    expected_values = np.array([0, 63, 127, 191, 255], dtype=np.uint8)
    assert np.allclose(result, expected_values)


def test_rescale_intensity_custom_range():
    """
    Test rescaling to custom output range.

    This test verifies that the intensity rescaling function can correctly
    transform an input array to a specified custom range, maintaining the
    relative intensity differences.

    Notes
    -----
    The test validates:
    - Correct output data type
    - Proper scaling to specified custom range (0 to 100)
    """
    # Create a test array with known values
    test_array = np.array([0, 5, 10], dtype=np.uint8)
    
    # Test rescaling to custom range
    result = apply_rescale_intensity(test_array, out_min=0, out_max=100)
    
    # Check that output matches input dtype and is properly scaled
    assert result.dtype == test_array.dtype
    assert np.isclose(result.min(), 0)
    assert np.isclose(result.max(), 100)


def test_rescale_intensity_preserve_zeros():
    """
    Test that zero values remain zero after rescaling.

    This test ensures that zero values in the input array are preserved
    as zero in the output array after intensity rescaling, which is important
    for maintaining background or masked regions.

    Notes
    -----
    The test validates:
    - Preservation of zero values in the output
    """
    # Create a test array with known values
    test_array = np.array([0, 0, 10, 20], dtype=np.uint8)

    # Test rescaling
    result = apply_rescale_intensity(test_array)
    
    # Check that zeros are preserved
    assert np.all(result[test_array == 0] == 0) 