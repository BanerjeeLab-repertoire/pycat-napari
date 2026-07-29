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

# ── N6-3: GLCM texture is computed over the ROI bbox, not the object mask (verify-then-fix) ──────────────
# calculate_glcm_features crops the ROI's BOUNDING BOX (`crop_bounding_box(...)[0]`) and runs graycomatrix over it
# WITHOUT restricting to the mask, so background pixels AND the object/background edge enter the co-occurrence
# matrix. For a non-rectangular object a UNIFORM interior (true texture = 0) then reports a large contrast that is
# entirely edge/background. (`calculate_image_entropy` restricts via `cropped_mask`; LBP restricts its histogram
# but still computes codes over the bbox, so its boundary ring is contaminated too — GLCM is the clear case, and
# the pre-existing `test_glcm_features_with_mask` misses it because its mask is rectangular, so bbox == mask.)

def _uniform_disc(H=80, W=80, r=18, obj=0.5):
    """A UNIFORM-intensity disc (zero true texture) on a zero background — bbox != mask, so any GLCM contrast the
    function reports comes from the object/background edge the bbox includes, not the object."""
    yy, xx = np.ogrid[:H, :W]
    disc = (yy - H // 2) ** 2 + (xx - W // 2) ** 2 <= r * r
    img = np.zeros((H, W), dtype=float)
    img[disc] = obj
    return img, disc.astype(np.uint8)


@pytest.mark.base
def test_glcm_contrast_is_contaminated_by_the_bbox_background():
    """CHARACTERISATION: a uniform object has zero true texture, yet calculate_glcm_features runs graycomatrix
    over the ROI bounding box (background + edge included) and reports a large contrast. Pins the contamination
    (and that even a zero-noise background contaminates — the object/background step alone does it)."""
    img, mask = _uniform_disc()
    contrast = float(calculate_glcm_features(img, object_size=3, roi_mask=mask)["contrast"].iloc[0])
    assert contrast > 100.0, f"a uniform object should give ~0 contrast; got {contrast:.1f} from the bbox edge"


@pytest.mark.base
@pytest.mark.xfail(reason="N6-3 fix spec: calculate_glcm_features runs graycomatrix over the ROI bounding box, not "
                          "the object mask, so a uniform object reports large contrast from the edge/background. "
                          "The fix restricts the co-occurrence to both-in-mask pixel pairs (matching skimage in "
                          "the all-True-mask limit). Remove this xfail when GLCM is mask-restricted.", strict=True)
def test_glcm_contrast_of_a_uniform_object_is_near_zero_once_masked():
    """FAILING GOLDEN-MASTER: a uniform object has no texture, so a mask-restricted GLCM must give ~0 contrast.
    This is the acceptance test the fix must satisfy — it flips from xfail to pass once GLCM stops averaging in
    the bbox background/edge."""
    img, mask = _uniform_disc()
    contrast = float(calculate_glcm_features(img, object_size=3, roi_mask=mask)["contrast"].iloc[0])
    assert contrast < 1.0
