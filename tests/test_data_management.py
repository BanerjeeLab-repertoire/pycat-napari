"""
Data Management Test Module for PyCAT

This module contains test cases for the BaseDataClass, which serves as the foundation for
data management in the PyCAT application. The tests verify proper initialization, data storage,
DataFrame operations, and reset functionality of the BaseDataClass. These tests ensure reliable
data handling and manipulation throughout the application.

The test suite uses pytest fixtures to provide consistent test environments and validates
both the successful initialization of data structures and proper handling of various data
operations, including DataFrame manipulations and value resets.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import pytest
import pandas as pd
import numpy as np

# Local application imports
from pycat.data.data_modules import BaseDataClass


@pytest.fixture
def base_data():
    """
    Provide a fresh BaseDataClass instance for testing.

    This fixture creates a new BaseDataClass instance for each test,
    ensuring a clean state for testing data management operations.

    Returns
    -------
    BaseDataClass
        A newly initialized BaseDataClass instance.
    """
    return BaseDataClass()


def test_initialization():
    """
    Test that BaseDataClass initializes with correct default values.

    This test verifies that a new BaseDataClass instance is properly initialized
    with all expected data repository keys and default values. It checks both the
    presence of required keys and the correctness of default numeric values.

    Notes
    -----
    The test ensures:
    - All expected keys exist in the data repository
    - Default numeric values are correctly set
    """
    data = BaseDataClass()
    
    # Check that all expected keys exist
    expected_keys = [
        'region_props_df', 'generic_df', 'object_size', 'cell_diameter',
        'ball_radius', 'microns_per_pixel_sq', 'metadata', 'cell_df', 'puncta_df'
    ]
    assert all(key in data.data_repository for key in expected_keys)
    
    # Check default numeric values
    assert data.get_data('object_size') == 50
    assert data.get_data('cell_diameter') == 100
    assert data.get_data('ball_radius') == 75
    assert data.get_data('microns_per_pixel_sq') == 1


def test_dataframe_operations(base_data):
    """
    Test basic DataFrame operations.

    This test verifies the functionality of various DataFrame operations including
    adding data, appending columns, and updating values. It ensures that the
    BaseDataClass correctly handles DataFrame manipulations.

    Parameters
    ----------
    base_data : BaseDataClass
        The BaseDataClass instance provided by the base_data fixture.

    Notes
    -----
    The test validates:
    - Adding new data to a DataFrame
    - Adding new columns to existing DataFrames
    - Updating specific values in DataFrames
    """
    # Test adding data to DataFrame
    test_data = {'col1': 1, 'col2': 'test'}
    base_data.append_to_df('generic_df', test_data)
    
    # Verify the data was correctly added
    df = base_data.get_data('generic_df')
    assert len(df) == 1
    assert df.iloc[0]['col1'] == 1
    assert df.iloc[0]['col2'] == 'test'
    
    # Test adding a new column
    base_data.add_column_to_df('generic_df', 'new_col', 'default')
    assert 'new_col' in base_data.get_data('generic_df').columns
    
    # Test updating a value
    base_data.update_df('generic_df', 0, 'col1', 999)
    assert base_data.get_data('generic_df').iloc[0]['col1'] == 999


def test_reset_functionality(base_data):
    """
    Test reset operations.

    This test verifies the functionality of both partial and full reset operations
    in the BaseDataClass. It ensures that the reset operations correctly clear
    specified DataFrames while maintaining other values as appropriate.

    Parameters
    ----------
    base_data : BaseDataClass
        The BaseDataClass instance provided by the base_data fixture.

    Notes
    -----
    The test validates:
    - Partial reset of specific DataFrames
    - Full reset of all values
    - Preservation of non-reset values during partial reset
    """
    # Add some test data
    test_data = {'col1': 1, 'col2': 'test'}
    base_data.append_to_df('generic_df', test_data)
    
    # Test partial reset
    base_data.reset_values(df_names_to_reset=['generic_df'])
    assert len(base_data.get_data('generic_df')) == 0
    assert base_data.get_data('object_size') == 50  # Other values should remain unchanged
    
    # Test full reset
    base_data.reset_values(clear_all=True)
    assert all(isinstance(df, pd.DataFrame) and df.empty 
              for df in base_data.get_dataframes().values())