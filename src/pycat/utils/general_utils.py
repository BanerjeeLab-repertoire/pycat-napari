"""
General Utilities Module for PyCAT

This module contains utility functions for image processing and data manipulation tasks in the PyCAT application.
The functions include image data type conversion, cropping images to bounding boxes defined by masks, checking image
contrast, and creating overlay images with red masks. These utilities are designed to facilitate common image processing
operations and enhance the user experience in the application.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import numpy as np
import skimage as sk


def dtype_conversion_func(image, output_bit_depth='uint16'):
    """
    Converts the data type of an image to a specified bit depth using skimage's utility functions. This conversion
    facilitates the optimization of images for various image processing tasks by ensuring compatibility with algorithm 
    requirements and enhancing performance.

    Parameters
    ----------
    image : numpy.ndarray
        The input image to be converted, which can be of any data type supported by skimage.
    output_bit_depth : str, optional
        The desired output bit depth as a string. Valid options include 'uint8', 'uint16', 'int16', 'float32', and 
        'float64', defaulting to 'uint16'.

    Returns
    -------
    converted_image : numpy.ndarray
        The image converted to the specified bit depth.

    Raises
    ------
    ValueError
        If an unsupported output bit depth is specified.

    Notes
    -----
    This function uses a mapping of bit depth strings to corresponding skimage functions to perform the conversion.
    """

    # Mapping each supported output bit depth to the corresponding skimage function
    bit_depth_func_map = {
        'uint8': sk.util.img_as_ubyte,
        'uint16': sk.util.img_as_uint,
        'int16': sk.util.img_as_int,
        'float32': sk.util.img_as_float32,
        'float64': sk.util.img_as_float64
    }

    # Retrieve the conversion function from the map based on the desired output bit depth
    conversion_func = bit_depth_func_map.get(output_bit_depth)

    # If the specified output bit depth is not supported, raise an error
    if conversion_func is None:
        raise ValueError(f"Unsupported output_bit_depth '{output_bit_depth}'. Supported values are 'uint8', 'uint16', 'int16', 'float32', 'float64'.")

    # Apply the selected conversion function to the input image
    converted_image = conversion_func(image)

    return converted_image



def crop_bounding_box(image, roi_mask):
    """
    Crops an image and its corresponding region of interest (ROI) mask to the bounding box defined by the ROI mask,
    then applies the cropped mask to the cropped image to generate a masked image.

    Parameters
    ----------
    image : numpy.ndarray
        The input image to be cropped, which can be a 2D (grayscale) or 3D (color) numpy array.
    roi_mask : numpy.ndarray
        A binary mask indicating the region of interest within the image. The mask should be of the same height 
        and width as the image and contain non-zero values (typically 1) in the region of interest and 0 elsewhere.

    Returns
    -------
    masked_img : numpy.ndarray
        The cropped image with the ROI mask applied, setting pixels outside the ROI to zero.
    cropped_mask : numpy.ndarray
        The cropped ROI mask.
    cropped_img : numpy.ndarray
        The cropped image without the mask applied.

    Notes
    -----
    This function identifies non-zero values in the ROI mask to determine the bounding box for cropping.
    The cropped image and mask are then used to create a masked image where only the region of interest is visible.
    """
    
    # Identify the rows and columns that contain non-zero values in the ROI mask
    rows, cols = np.where(roi_mask)
    
    # Determine the minimum and maximum row and column indices to define the bounding box
    min_row, max_row = rows.min(), rows.max()
    min_col, max_col = cols.min(), cols.max()

    # Crop the image and the mask to the bounding box
    cropped_img = image[min_row:max_row+1, min_col:max_col+1]
    cropped_mask = roi_mask[min_row:max_row+1, min_col:max_col+1]

    # Apply the cropped mask to the cropped image, setting pixels outside the ROI to 0
    masked_img = np.where(cropped_mask, cropped_img, 0)

    return masked_img, cropped_mask, cropped_img



def get_default_intensity_range(dtype_str):
    """
    Retrieves the default intensity range for a specified image data type. This range is important for tasks such
    as image normalization and processing, where specific data types may impact computations.

    Parameters
    ----------
    dtype_str : str
        A string representing the data type for which the intensity range is sought. Supported data types include 
        'uint8', 'uint16', 'int16', 'float32', and 'float64'.

    Returns
    -------
    tuple
        A tuple containing two numbers that represent the minimum and maximum intensity values for the given data type.

    Raises
    ------
    ValueError
        If the provided data type string is not supported, raises a ValueError indicating the unsupported data type.

    Notes
    -----
    This function serves as a helper for intensity rescaling functions to determine the appropriate ranges based on 
    the input data type.
    """

    # Define a dictionary mapping data type strings to their intensity ranges.
    type_ranges = {
        'uint8': (0, 255),        # Standard range for 8-bit unsigned integers
        'uint16': (0, 65535),     # Standard range for 16-bit unsigned integers
        'int16': (-32768, 32767), # Range for 16-bit signed integers
        'float32': (0.0, 1.0),    # Common range for normalized floating-point data
        'float64': (0.0, 1.0)     # Common range for normalized floating-point data
    }

    # Attempt to fetch and return the intensity range from the dictionary based on the provided dtype string.
    if dtype_str in type_ranges:
        return type_ranges[dtype_str]
    else:
        # Raise an error if the data type is not recognized
        raise ValueError(f"Data type {dtype_str} not supported")


def check_contrast_func(image):
    """
    Check if the input image has sufficient contrast, specifically after conversion to 16-bit depth.

    This function converts the input image to uint16 format using `dtype_conversion_func`. It calculates
    the minimum and maximum pixel values to assess contrast. If the min and max values differ by 2 or less,
    it indicates a lack of sufficient contrast, typically implying the image or cell is blank, and is
    useful for deciding whether to exclude such images from further processing.

    Parameters
    ----------
    image : numpy.ndarray
        The input image array.

    Returns
    -------
    bool
        False if there is sufficient contrast in the image; True if there is insufficient contrast.

    Notes
    -----
    The return value is True for error conditions (no contrast), which might seem counterintuitive but follows
    a specific pattern where checking functions return True to indicate the presence of the condition they check for.
    """
    # Convert the input image to uint16, if there is no contrast at that resolution, likely nothing in the image
    image = dtype_conversion_func(image, output_bit_depth='uint16')
    # Calculate the minimum and maximum pixel values in the image
    min_val, max_val = np.min(image), np.max(image)
    contrast_range = max_val - min_val
    # Ensure there's variation in the image to avoid division by zero
    if contrast_range <= 2:
        # Indicate an error condition due to lack of variation in the image
        #print("The image has no contrast.")
        return True

    return False


def create_overlay_image(green_channel, overlay_mask, alpha=0.65):
    """
    Create an image showing the green channel of an input image next to the same image with a red overlay on specified areas.

    This function normalizes the green channel data to 8-bit, creates an RGB representation of it, and overlays
    a red mask on specified areas defined by the `overlay_mask`. The resulting images are shown side by side
    for comparison purposes.

    Parameters
    ----------
    green_channel : numpy.ndarray
        A 2D array representing the green channel of an image.
    overlay_mask : numpy.ndarray
        A 2D boolean or binary array where true/non-zero values indicate areas to apply a red overlay.
    alpha : float, optional
        Transparency level for the red overlay, ranging from 0 (transparent) to 1 (opaque). Default is 0.65.

    Returns
    -------
    side_by_side_image : numpy.ndarray
        An array containing the original RGB image and the modified image with a red overlay side by side.

    Notes
    -----
    Both `green_channel` and `overlay_mask` must have the same dimensions. This function assumes that the input green channel
    values are normalized (i.e., within a 0 to 1 range) or will normalize them internally for the purpose of image processing.
    """

    # Normalize and convert the green channel image to an 8-bit format.
    green_channel_8 = (green_channel / np.max(green_channel) * 255).astype('uint8')
    
    # Create an RGB image by stacking the green channel between two arrays of zeros (for R and B channels).
    rgb_image = np.stack((np.zeros_like(green_channel_8), green_channel_8, np.zeros_like(green_channel_8)), axis=-1)
    
    # Prepare a red overlay by creating an RGB array filled with zeros and setting the red channel to maximum.
    red_overlay = np.zeros_like(rgb_image)
    red_overlay[..., 0] = 255  # Red channel set to maximum intensity
    
    # Apply the overlay mask to the red overlay, making the overlay transparent where the mask is 0.
    masked_red_overlay = np.where((overlay_mask > 0)[..., np.newaxis], red_overlay, 0)
    
    # Combine the original RGB image with the masked red overlay, adjusting by the alpha for transparency.
    combined_image = rgb_image + (masked_red_overlay * alpha)
    combined_image = combined_image.astype(np.uint8)  # Ensure the result is in 8-bit format
    
    # Concatenate the original and combined images side by side for comparison.
    side_by_side_image = np.hstack((rgb_image, combined_image))
    
    return side_by_side_image


