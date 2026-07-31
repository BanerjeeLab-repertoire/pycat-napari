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


@pytest.mark.base
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


@pytest.mark.base
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


@pytest.mark.base
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

# ── N6-3: GLCM texture is computed over the OBJECT MASK, not the ROI bbox (verified + FIXED, 1.6.x) ──────
# calculate_glcm_features used to crop the ROI's BOUNDING BOX and run graycomatrix over it WITHOUT restricting to
# the mask, so background pixels and the object/background edge entered the co-occurrence matrix — a uniform
# object reported a large, spurious contrast. The fix (`_masked_graycomatrix`) counts only object–object pixel
# pairs. It is byte-identical to skimage when the mask is the whole bbox (guarded below), so only non-rectangular
# objects change — correctly. (The pre-existing `test_glcm_features_with_mask` never caught the bug because its
# mask is rectangular, so bbox == mask.)

def _uniform_disc(H=80, W=80, r=18, obj=0.5):
    """A UNIFORM-intensity disc (zero true texture) on a zero background — bbox != mask, so any contrast the
    function reports would come from the object/background edge the bbox includes, not the object."""
    yy, xx = np.ogrid[:H, :W]
    disc = (yy - H // 2) ** 2 + (xx - W // 2) ** 2 <= r * r
    img = np.zeros((H, W), dtype=float)
    img[disc] = obj
    return img, disc.astype(np.uint8)


@pytest.mark.base
def test_glcm_contrast_of_a_uniform_object_is_near_zero_now_masked():
    """N6-3 FIX (was the failing golden-master): a uniform object has no texture, so the mask-restricted GLCM
    gives ~0 contrast — the object/background edge no longer contaminates it."""
    img, mask = _uniform_disc()
    contrast = float(calculate_glcm_features(img, object_size=3, roi_mask=mask)["contrast"].iloc[0])
    assert contrast < 1.0, f"a uniform object must give ~0 contrast once masked; got {contrast:.3f}"


@pytest.mark.base
def test_masked_glcm_is_byte_identical_to_skimage_over_the_whole_bbox():
    """REGRESSION GUARD: the fix must NOT change the un-masked path. When the mask is the whole rectangle, the
    masked co-occurrence equals skimage's graycomatrix(symmetric, normed) exactly — so every existing GLCM number
    (no ROI, or a rectangular ROI) is preserved; only non-rectangular objects change."""
    import skimage as sk
    from pycat.toolbox.feature_analysis_tools import _masked_graycomatrix
    rng = np.random.default_rng(0)
    img8 = (rng.uniform(0, 1, (40, 40)) * 255).astype(np.uint8)
    dists = np.array([3, 4, 5])
    angles = np.array([0, np.pi/8, np.pi/4, 3*np.pi/8, np.pi/2, 5*np.pi/8, 3*np.pi/4, 7*np.pi/8])
    mine = _masked_graycomatrix(img8, np.ones((40, 40), bool), dists, angles)
    theirs = sk.feature.graycomatrix(img8, dists, angles, symmetric=True, normed=True)
    np.testing.assert_allclose(mine, theirs, atol=1e-12)
