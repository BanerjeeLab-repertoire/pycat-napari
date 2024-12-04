"""
General Utilities Test Module for PyCAT

This module contains test cases for the general utility functions in PyCAT, specifically
focusing on data type conversion functionality. This feature was chosen for its limited 
reliance on numerous imports, making the test more self-contained. This functionality
serves as a basic test for the PyCAT utility function and utils modules. The tests verify 
proper handling of different numeric data types, value ranges, and error conditions when 
converting betweenvarious numpy data types.

The test suite ensures reliable data type conversions which are fundamental to many
image processing operations throughout the application, particularly in maintaining
proper data representation and precision across different processing steps.

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
from pycat.utils.general_utils import dtype_conversion_func


def test_dtype_conversion_basic():
    """
    Test basic dtype conversion functionality.

    This test verifies that basic data type conversions work correctly for common
    scenarios, specifically testing conversions to uint16 and float32 from uint8.

    Notes
    -----
    The test validates:
    - Successful conversion to uint16
    - Successful conversion to float32
    - Correct output data types
    """
    # Create a simple test array
    test_array = np.array([[0, 127, 255]], dtype=np.uint8)
    
    # Test conversion to different dtypes
    uint16_result = dtype_conversion_func(test_array, 'uint16')
    float32_result = dtype_conversion_func(test_array, 'float32')
    
    # Check output dtypes
    assert uint16_result.dtype == np.uint16
    assert float32_result.dtype == np.float32


def test_dtype_conversion_ranges():
    """
    Test that conversion maintains appropriate value ranges.

    This test ensures that value ranges are properly maintained during conversion,
    particularly when converting between normalized float values and integer types.

    Notes
    -----
    The test validates:
    - Correct conversion from normalized float32 to uint8
    - Proper scaling of values to maintain relative magnitudes
    - Correct minimum and maximum values in output
    """
    # Create a normalized float array
    test_array = np.array([[0.0, 0.5, 1.0]], dtype=np.float32)
    
    # Convert to uint8 and check range
    uint8_result = dtype_conversion_func(test_array, 'uint8')
    assert uint8_result.min() == 0
    assert uint8_result.max() == 255


def test_dtype_conversion_invalid():
    """
    Test that invalid dtype raises ValueError.

    This test verifies that the function properly handles invalid input by raising
    appropriate exceptions when an unsupported data type is requested.

    Notes
    -----
    The test validates:
    - Proper error handling for invalid dtype strings
    - Raising of ValueError for unsupported data types
    """
    # Create a simple test array
    test_array = np.array([[0, 127, 255]], dtype=np.uint8)
    
    # Check that an invalid dtype raises a ValueError
    with pytest.raises(ValueError):
        dtype_conversion_func(test_array, 'invalid_dtype') 
