"""
Feature Analysis Test Module for PyCAT

This module contains test cases for the feature analysis tools in PyCAT, specifically focusing
on the Gray Level Co-occurrence Matrix (GLCM) feature calculations. This feature was chosen for
its limited reliance on numerous imports, making the test more self-contained. This functionality
serves as a basic test for the feature analysis tools and module. The tests verify proper
functionality of texture analysis operations under various conditions, including basic feature
extraction, masked region analysis, and handling of invalid inputs.

The test suite validates the calculation of important texture features such as contrast,
dissimilarity, homogeneity, ASM, energy, and correlation. It ensures reliable feature
extraction both with and without region of interest (ROI) masks.

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
import pandas as pd

# Local application imports
from pycat.toolbox.feature_analysis_tools import calculate_glcm_features


def test_glcm_features_basic():
    """
    Test basic GLCM feature calculation.

    This test verifies the fundamental functionality of GLCM feature calculation
    using a simple test image with a repeating pattern. It checks both the
    correct calculation of features and their value ranges.

    Notes
    -----
    The test validates:
    - Proper DataFrame output format
    - Presence of all expected feature columns
    - Feature values within expected ranges (e.g., ASM and energy between 0 and 1)
    """
    # Create a simple test image with a repeating pattern
    test_image = np.array([
        [0, 0, 1, 1],
        [0, 0, 1, 1],
        [2, 2, 3, 3],
        [2, 2, 3, 3]
    ], dtype=np.uint8)
    
    # Calculate GLCM features
    features_df = calculate_glcm_features(test_image, object_size=1)
    
    # Check that we got a DataFrame with the expected columns
    expected_columns = ['contrast', 'dissimilarity', 'homogeneity', 
                       'ASM', 'energy', 'correlation']
    assert isinstance(features_df, pd.DataFrame)
    assert all(col in features_df.columns for col in expected_columns)
    
    # Check that values are within expected ranges
    assert 0 <= features_df['ASM'].iloc[0] <= 1  # ASM is always between 0 and 1
    assert 0 <= features_df['energy'].iloc[0] <= 1  # Energy is always between 0 and 1


def test_glcm_features_with_mask():
    """
    Test GLCM feature calculation with ROI mask.

    This test verifies that GLCM features are correctly calculated when using
    a region of interest mask. It ensures that the analysis properly considers
    the masked region and produces different results compared to unmasked analysis.

    Notes
    -----
    The test validates:
    - GLCM calculation with a specific region of interest
    - Different results when using mask vs. no mask
    - Proper handling of masked regions
    """
    # Create a test image and mask
    test_image = np.ones((10, 10), dtype=np.uint8)
    test_image[2:8, 2:8] = 2  # Create a pattern in the center
    
    # Create a mask focusing on the center region
    mask = np.zeros_like(test_image, dtype=bool)
    mask[2:8, 2:8] = True  # Only analyze the center region
    
    # Calculate features with and without mask
    features_with_mask = calculate_glcm_features(test_image, object_size=1, roi_mask=mask)
    features_no_mask = calculate_glcm_features(test_image, object_size=1)
    
    # Results should be different with mask
    assert not features_with_mask.equals(features_no_mask)


def test_glcm_features_invalid_input():
    """
    Test GLCM feature calculation with invalid inputs.

    This test verifies that the GLCM feature calculation function properly handles
    invalid inputs by raising appropriate exceptions. It tests both empty images
    and invalid object sizes.

    Notes
    -----
    The test validates proper error handling for:
    - Empty image arrays
    - Negative object sizes
    """
    # Test with empty image
    with pytest.raises(Exception):
        calculate_glcm_features(np.array([]), object_size=1)
    
    # Test with negative object size
    with pytest.raises(Exception):
        calculate_glcm_features(np.ones((5, 5)), object_size=-1)