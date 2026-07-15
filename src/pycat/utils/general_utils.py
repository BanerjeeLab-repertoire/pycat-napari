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

# Standard library
import os as _os
import traceback as _traceback


def _pycat_debug_enabled():
    """True when verbose PyCAT debugging is requested via the PYCAT_DEBUG env var
    (any value other than empty/0/false). Mirrors the PYCAT_REFINE_DEBUG /
    PYCAT_FORCE_CPU env-var convention used elsewhere in the codebase."""
    return _os.environ.get('PYCAT_DEBUG', '') not in ('', '0', 'false', 'False')


def report_guarantee_failure(context, exc=None):
    """**A scientific guarantee that failed to install must SAY SO. Unconditionally.**

    Call from an ``except`` block that would otherwise ``pass``, around the things that keep a
    number honest — the pixel-size gate, the T-vs-Z axis warning, the frame-interval sync.

    ── Why this is not ``debug_log`` ────────────────────────────────────────────────────

    ``debug_log`` prints **only when ``PYCAT_DEBUG=1``**. For an optional colormap or a tooltip that
    is exactly right — nobody needs to hear about it.

    **But the pixel-size gate is not optional.** It exists to catch an image with no physical scale,
    and it was installed like this, in **seven** separate panels::

        try:
            self._pixel_gate_refresh = add_pixel_size_gate(layout, ...)
        except Exception:
            pass

    ***If that throws, the gate never installs, `_pixel_gate_refresh` is never set, the reset hook
    finds `None` and does nothing — and the panel builds perfectly.*** The image then loads at
    1.0 µm/px, and **every length, area and diffusion coefficient is silently in pixels while the
    column header says microns.** Nothing is printed. Nothing looks wrong.

    *That is the pixel-size gate regression that cost a night to find. It was unfindable by
    construction.* The same shape silences ``warn_if_assumed_axis`` — the T-vs-Z prompt — in four
    more panels.

    ── What it does ─────────────────────────────────────────────────────────────────────

    Prints **unconditionally**, and raises a napari warning if a viewer is reachable — because a
    message only in the terminal is a message most users of a GUI never see.

    **It does not change control flow.** The caller still swallows: *a broken gate must not take the
    whole panel down with it.* The panel still builds; the failure is simply no longer invisible.

    *This does not make the gate work. It makes its absence impossible to miss.*
    """
    print(
        f"[PyCAT] A SCIENTIFIC CHECK FAILED TO INSTALL: {context}\n"
        f"        {type(exc).__name__ if exc is not None else 'error'}: {exc}\n"
        f"        This is not cosmetic. The check that would have caught a missing or wrong\n"
        f"        value is NOT RUNNING — so a number that looks fine may be silently wrong.\n"
        f"        Please report this, with the file you were opening."
    )

    if _pycat_debug_enabled():
        try:
            _traceback.print_exc()
        except Exception:
            pass

    try:
        from napari.utils.notifications import show_warning
        show_warning(f"A PyCAT check failed to install: {context}. "
                     f"Values that depend on it may be wrong — see the terminal.")
    except Exception:
        # No viewer (headless, tests). The print above is still unconditional, which is the point.
        pass


def debug_log(context, exc=None):
    """Surface an otherwise-silent swallowed exception.

    Call this inside an ``except Exception`` block that would normally just
    ``pass``, so that when PYCAT_DEBUG=1 the failure is printed (with traceback)
    instead of vanishing — turning invisible failures into diagnosable ones —
    while staying completely silent in normal use.

    Parameters
    ----------
    context : str
        Short description of what was being attempted, e.g.
        "file_io: reading physical pixel size".
    exc : Exception, optional
        The caught exception; if omitted the current traceback is used.

    Notes
    -----
    This does NOT change control flow — the caller still decides whether to
    ``pass``, ``continue``, ``return``, or fall back. It only makes the swallow
    observable. No-op unless PYCAT_DEBUG is set, so it is safe to sprinkle into
    hot paths.
    """
    if not _pycat_debug_enabled():
        return
    try:
        if exc is not None:
            print(f"[PyCAT DEBUG] {context}: {type(exc).__name__}: {exc}")
        else:
            print(f"[PyCAT DEBUG] {context}:")
        _traceback.print_exc()
    except Exception:
        # The logger itself must never raise into a caller's except block.
        pass


def normalize01(arr, out_dtype=np.float32):
    """Min-max normalise an array to [0, 1], safely.

    Returns (arr - min) / (max - min) as ``out_dtype``. On a flat/constant array
    (max == min) this returns all zeros instead of dividing by zero — the common
    case that a bare ``(x - mn) / (mx - mn)`` turns into NaN/inf (e.g. an all-
    background frame in a time series, or a fully-saturated region). This is the
    single canonical normalisation used across PyCAT so the divide-by-zero guard
    and behaviour are consistent everywhere.

    Parameters
    ----------
    arr : array-like
        Input image / stack.
    out_dtype : numpy dtype
        Output dtype (default float32). Pass None to keep float64.

    Returns
    -------
    numpy.ndarray in [0, 1].
    """
    a = np.asarray(arr, dtype=np.float64)
    mn = float(a.min()) if a.size else 0.0
    mx = float(a.max()) if a.size else 0.0
    if mx > mn:
        out = (a - mn) / (mx - mn)
    else:
        out = np.zeros_like(a)
    return out if out_dtype is None else out.astype(out_dtype)


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
    # Check contrast on the actual pixel values, not via uint16 conversion.
    # img_as_uint assumes float input is in [-1, 1] and clips anything outside
    # that range — background-removed float32 images with values e.g. [0, 1500]
    # get collapsed to a flat array, triggering a false "no contrast" result.
    # Instead: compare min/max on the raw array; if the image is integer convert
    # to float first so the comparison is in a consistent scale.
    arr = np.asarray(image, dtype=np.float64)
    if arr.size == 0:
        return True
    min_val = float(np.min(arr))
    max_val = float(np.max(arr))
    contrast_range = max_val - min_val
    # Relative threshold: flag as no-contrast if range < 0.1% of max magnitude,
    # which is equivalent to the old uint16 "<=2" rule but works across dtypes
    # and float ranges (preserving the original intent without the clipping bug).
    magnitude = max(abs(max_val), abs(min_val), 1e-12)
    if contrast_range / magnitude < 0.001:
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


def remove_small_objects_compat(binary, min_area, connectivity=1):
    """Version-compatible ``skimage.morphology.remove_small_objects``.

    scikit-image 0.26 DEPRECATED ``min_size`` in favour of ``max_size`` — and the
    two are NOT a simple rename: the old ``min_size=N`` removed objects *smaller
    than* N (size < N), while the new ``max_size=N`` removes objects *smaller than
    OR EQUAL TO* N (size <= N). To preserve the original "remove anything below
    ``min_area``" behaviour on new skimage, pass ``max_size = min_area - 1``.

    This is the single correct place for that logic — several call sites had
    drifted into a mix of positional ``min_size``, keyword ``min_size`` and even
    ``max_size=min_area`` (which silently changed the threshold by one and the
    comparison to ``<=``). Route them all here.

    Parameters
    ----------
    binary : ndarray
        Boolean mask or label image.
    min_area : int
        Objects with area STRICTLY LESS THAN this are removed (the historical
        ``min_size`` semantics). ``<= 0`` is a no-op.
    connectivity : int
        Passed through to ``remove_small_objects``.
    """
    if int(min_area) <= 0:
        return binary
    try:
        # New API (skimage >= 0.26): removes size <= max_size, so max_size =
        # min_area - 1 reproduces the old "size < min_area" removal exactly.
        return sk.morphology.remove_small_objects(
            binary, max_size=int(min_area) - 1, connectivity=connectivity)
    except TypeError:
        # Old API (skimage < 0.26): only min_size exists.
        return sk.morphology.remove_small_objects(
            binary, min_size=int(min_area), connectivity=connectivity)
