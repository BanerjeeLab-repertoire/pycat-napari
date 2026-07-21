"""Cell-mask morphology - split out of segmentation_tools (1.6.240).

cell_mask_stretching (registered op) dilates/stretches cell masks toward local intensity structure so
subcellular objects near the membrane are not clipped. Moved VERBATIM - no structuring element or
threshold change.
"""
from __future__ import annotations

import math
import numpy as np
import skimage as sk
import scipy.ndimage as ndi
from pycat.utils.tag_registry import tags_layer
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.general_utils import dtype_conversion_func, check_contrast_func


@tags_layer('mask_stretch', role='mask',
            summary='Cell-mask dilation to the true boundary', target='cell')
def cell_mask_stretching(image, cell_masks, min_relative_max=0.02,
                         preserve_scale=False):
    """
    Enhances the contrast within specific areas of an image defined by cell masks, followed by smoothing operations.

    The function dilates the cell masks to include surrounding areas, then applies CLAHE (Contrast Limited Adaptive 
    Histogram Equalization) to these regions for contrast enhancement. The areas are then slightly eroded to fit
    the original mask dimensions. After processing all masks, the background is zeroed out, and the image is smoothed 
    using grey-scale morphological operations to avoid blurring.

    Parameters
    ----------
    image : numpy.ndarray
        The input image to perform contrast stretching on. Must be a 2D array.
    cell_masks : numpy.ndarray
        A labeled mask image where each cell is represented by a unique integer label, and the background is 0.
    min_relative_max : float, optional
        A cell whose maximum is below ``min_relative_max * image_max`` contains
        nothing comparable to the brightest object anywhere in the image, and is
        zeroed instead of being amplified. Default 0.02 (a 50x gain ceiling).

        WHY THIS EXISTS. ``sk.exposure.equalize_adapthist`` calls
        ``rescale_intensity`` on BOTH its input and its output::

            image = rescale_intensity(image, out_range=(0, NR_OF_GRAY - 1))
            ...
            return rescale_intensity(image)

        Because this function hands it ONE CELL AT A TIME (``img * dilated_mask``,
        whose min is 0 and whose max is that cell's max), every cell is divided by
        its own maximum. The gain is exactly ``1 / cell_max``. A cell holding a
        bright condensate (max 0.94) is amplified 1.1x; a cell holding only
        nucleoplasm noise (max 3.2e-4) is amplified **3150x**, turning its shot
        noise into speckle that segments as puncta. Nothing downstream can undo
        that: the speckle is real structure by then.

        The pre-existing ``check_contrast_func`` guard cannot catch this. It is a
        RELATIVE range test -- ``(max - min) / max < 0.001`` -- which for a
        noise-only cell evaluates to ~1.0. It fires only when a cell is exactly
        constant.

        Set to 0 to restore the unguarded behaviour.
    preserve_scale : bool, optional
        If True, map the CLAHE output back onto the cell's ORIGINAL intensity
        range, so the per-cell equalisation reshapes the histogram without
        applying any gain. This removes the amplification entirely rather than
        merely bounding it, but changes the values seen by every downstream
        stage, so it is off by default.

    Returns
    -------
    output_image : numpy.ndarray
        The image after applying contrast enhancement and smoothing operations, in the original data type.

    Notes
    -----
    This function assumes the presence of at least one cell label in `cell_masks` (i.e., not all values are 0). 
    It enhances only the areas defined by the masks, leaving the background unaffected except for smoothing. The 
    CLAHE parameters are dynamically adjusted based on the object's estimated radius from its area.
    """

    input_dtype = str(image.dtype)
    img = dtype_conversion_func(image, 'float32') # Convert image to float32 for processing

    # Copy the input image to apply contrast stretching
    img_contrast_stretched = img.copy()
    
    # Initialize a total cell mask with the same shape as the input image but with boolean type
    total_cell_mask = np.zeros_like(cell_masks, dtype=bool)

    # The one absolute intensity reference available here: the brightest pixel in
    # the whole image. Per-cell CLAHE destroys it (see `min_relative_max`), so it
    # has to be captured before the loop.
    img_max = float(img.max())

    for label in np.unique(cell_masks)[1:]:  # Exclude the background label (0).
        # Create a mask for the current cell.
        cell_mask = (cell_masks == label).astype(bool)
        # Check the contrast of the cell
        contrast_flag = check_contrast_func(img * cell_mask)

        # Absolute-brightness guard. equalize_adapthist normalises every cell to
        # unit maximum, so a cell containing only noise is amplified by
        # 1/cell_max -- often 1000x or more -- and its noise becomes speckle that
        # segments as puncta. A cell with no pixel within `min_relative_max` of
        # the image maximum holds nothing worth enhancing.
        cell_max = float(img[cell_mask].max()) if cell_mask.any() else 0.0
        below_floor = bool(min_relative_max > 0 and img_max > 0
                           and cell_max < min_relative_max * img_max)
        if below_floor and not contrast_flag:
            gain = (1.0 / cell_max) if cell_max > 0 else float('inf')
            napari_show_info(
                f"Cell {label}: max {cell_max:.3g} is {cell_max / img_max:.1e} of the "
                f"image max ({img_max:.3g}); per-cell CLAHE would amplify it "
                f"{gain:.0f}x. Treating as empty (no condensates).")

        if contrast_flag or below_floor:
            # Update the contrast-stretched image within the mask
            img_contrast_stretched[cell_mask] = 0
            # Update the total cell mask.
            total_cell_mask |= cell_mask
            continue

        # Dilate the mask slightly to include a bit of the surrounding area.
        dilated_mask = ndi.binary_dilation(cell_mask, sk.morphology.disk(3))
        # Create a masked version of the input image using the dilated mask.
        masked_cell = img * dilated_mask

        # Calculate parameters for CLAHE based on the object's size.
        mask_area = np.sum(cell_mask) # Total area of the cell
        object_rad = np.sqrt(mask_area / np.pi) # Estimated radius of the cell
        k_size = math.ceil(object_rad / 4)  # Kernel size for CLAHE.
        clip_lim = 0.0025  # Clip limit for CLAHE.
        # Apply CLAHE to the masked cell.
        stretched_cell = sk.exposure.equalize_adapthist(masked_cell, kernel_size=k_size, clip_limit=clip_lim)

        # equalize_adapthist always returns [0, 1] (it rescale_intensity's its own
        # output). Optionally map it back onto the cell's original range so the
        # histogram is reshaped without any gain being applied.
        if preserve_scale:
            _cmin = float(img[cell_mask].min())
            _span = cell_max - _cmin
            if _span > 0:
                stretched_cell = (stretched_cell * _span + _cmin).astype(np.float32)

        # Erode the dilated mask to reduce artifacts at the edges.
        eroded_mask = ndi.binary_erosion(dilated_mask, sk.morphology.disk(3)).astype(bool)
    
        # Update the contrast-stretched image within the eroded mask
        img_contrast_stretched[eroded_mask] = stretched_cell[eroded_mask]
                
        # Update the total cell mask.
        total_cell_mask |= eroded_mask

    # Set the background (areas not covered by any cell mask) to 0.
    img_contrast_stretched[~total_cell_mask] = 0
    
    # Apply grey dilation and erosion to smooth the image data without blurring.
    structuring_element = sk.morphology.disk(1)
    output_image = ndi.grey_dilation(img_contrast_stretched, footprint=structuring_element)
    output_image = ndi.grey_erosion(output_image, footprint=structuring_element)

    # Convert the output image back to the original data type.
    output_image = dtype_conversion_func(output_image, output_bit_depth=input_dtype)
    
    return output_image
