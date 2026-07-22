"""Deblurring by pixel reassignment (DPR) - split out of image_processing_tools (1.6.250).

deblur_by_pixel_reassignment sharpens by re-locating each pixel toward the local intensity gradient on an
upsampled grid (the classic DPR); run_dpr is the viewer wrapper. Moved VERBATIM - no numerics changed;
pinned by test_image_processing_deblur_characterization (exact two-array output on a fixed scene). Imports
upscale_image_interp + _add_image from the _base primitives.
"""
from __future__ import annotations

import math

import numpy as np
import scipy.ndimage as ndi
from pycat.utils.tag_registry import tags_layer
from pycat.toolbox.image_processing._base import upscale_image_interp, _add_image


@tags_layer('dpr', role='preprocessed',
            summary='Deblurring by pixel reassignment', aliases=('pixel_reassignment',))
def deblur_by_pixel_reassignment(I_in, PSF, gain, window_radius):
    """
    Performs Deblurring by Pixel Reassignment, adapted from MATLAB code, enhancing microscopy image quality by reducing
    blurriness and improving visualization of microscopic entities [dpr_1]_.

    Parameters
    ----------
    I_in : numpy.ndarray
        The input image array to be processed.
    PSF : float
        Point Spread Function width, quantifying blurriness in terms of pixels.
    gain : float
        Gain factor to adjust the intensity of pixel displacement.
    window_radius : int
        Radius in pixels for the local minimum filter, enhancing local contrast.

    Returns
    -------
    single_frame_I_out : numpy.ndarray
        The DPR processed deblurred image.
    single_frame_I_magnified : numpy.ndarray
        The magnified input image.

    Notes
    -----
    The function adapts MATLAB code to Python, implementing modifications to suit Pythonic nuances and the specific data.
    Modifications include scaling down the gain to reduce artifacts introduced by integer gain values in Python,
    which differ from MATLAB's handling.

    References
    ----------
    .. [dpr_1] MATLAB code source: [DPR Project](https://github.com/biomicroscopy/DPR-Project)
        - Related paper: "Advanced Photonics, Vol. 5, Issue 6, 066004 (October 2023)", available at https://doi.org/10.1117/1.AP.5.6.066004
    """

    # Initial setup: calculate the upscale factor and prepare the Sobel filters for edge detection
    num_row_initial, num_col_initial = I_in.shape
    upscale_factor = 5 / PSF  # Upscaled image has 5 pixels per PSF (1/e beam radius) 
    sobelY = np.array([[1, 0, -1], [2, 0, -2], [1, 0, -1]])  # Vertical edge detection
    sobelX = np.array([[1, 2, 1], [0, 0, 0], [-1, -2, -1]])  # Horizontal edge detection

    # Preprocessing: Subtract local minimum for local contrast enhancement
    single_frame_I_in = I_in - I_in.min()
    local_minimum = np.zeros_like(I_in)
    single_frame_I_in_localmin = np.zeros_like(I_in)

    # Calculate local minimum for each pixel
    for u in range(num_row_initial):
        for v in range(num_col_initial):
            sub_window = single_frame_I_in[max(0, u - window_radius):min(num_row_initial, u + window_radius + 1),
                                           max(0, v - window_radius):min(num_col_initial, v + window_radius + 1)]
            local_minimum[u, v] = sub_window.min()
            single_frame_I_in_localmin[u, v] = single_frame_I_in[u, v] - local_minimum[u, v]

    # Upscale images using interpolation to enhance details
    single_frame_localmin_magnified = upscale_image_interp(single_frame_I_in_localmin, num_row_initial, num_col_initial, upscale_factor=upscale_factor, pad=True)
    single_frame_I_magnified = upscale_image_interp(single_frame_I_in, num_row_initial, num_col_initial, upscale_factor=upscale_factor, pad=True)

    # Normalize and calculate gradients for displacement calculation
    I_normalized = single_frame_localmin_magnified / (ndi.gaussian_filter(single_frame_localmin_magnified, 10) + 0.000001)
    gradient_x = ndi.convolve(I_normalized, sobelX, mode='nearest')
    gradient_y = ndi.convolve(I_normalized, sobelY, mode='nearest')
    gradient_x /= (I_normalized + 0.000001)
    gradient_y /= (I_normalized + 0.000001)

    # Apply gain and limit the displacements
    gain_value = 0.1 * gain
    displacement_x = gain_value * gradient_x
    displacement_y = gain_value * gradient_y
    displacement_x[np.abs(displacement_x) > 10] = 0  # Limit displacements to prevent artifacts
    displacement_y[np.abs(displacement_y) > 10] = 0

    # Weighted pixel displacement for image reconstruction
    single_frame_I_out = np.zeros_like(single_frame_I_magnified)
    num_row, num_col = single_frame_I_magnified.shape

    for nx in range(10, num_row - 10):
        for ny in range(10, num_col - 10):
            # Process displacement and calculate weights for reconstruction
            disp_x = displacement_x[nx, ny]
            disp_y = displacement_y[nx, ny]

            # Calculating the integer parts of the displacements
            int_disp_x = int(np.fix(disp_x))
            int_disp_y = int(np.fix(disp_y))

            # Weights based on the fractional part of the displacement
            weighted1 = (1 - abs(disp_x - int_disp_x)) * (1 - abs(disp_y - int_disp_y))
            weighted2 = (1 - abs(disp_x - int_disp_x)) * abs(disp_y - int_disp_y)
            weighted3 = abs(disp_x - int_disp_x) * (1 - abs(disp_y - int_disp_y))
            weighted4 = abs(disp_x - int_disp_x) * abs(disp_y - int_disp_y)

            # Calculating the coordinates for pixel reassignment
            coordinate1 = (int_disp_x, int_disp_y)
            coordinate2 = (int_disp_x, int(int_disp_y + np.sign(disp_y)))
            coordinate3 = (int(int_disp_x + np.sign(disp_x)), int_disp_y)
            coordinate4 = (int(int_disp_x + np.sign(disp_x)), int(int_disp_y + np.sign(disp_y)))

            # Assigning the weighted pixel values to reconstruct the image
            # To shift I-local_min use 'single_frame_localmin_magnified', to shift raw image use 'single_frame_I_magnified'
            single_frame_I_out[nx + coordinate1[0], ny + coordinate1[1]] += weighted1 * single_frame_I_magnified[nx, ny]
            single_frame_I_out[nx + coordinate2[0], ny + coordinate2[1]] += weighted2 * single_frame_I_magnified[nx, ny]
            single_frame_I_out[nx + coordinate3[0], ny + coordinate3[1]] += weighted3 * single_frame_I_magnified[nx, ny]
            single_frame_I_out[nx + coordinate4[0], ny + coordinate4[1]] += weighted4 * single_frame_I_magnified[nx, ny]

    # Crop the borders added by displacement
    single_frame_I_out = single_frame_I_out[10:-10, 10:-10]
    single_frame_I_magnified = single_frame_I_magnified[10:-10, 10:-10]

    return single_frame_I_out, single_frame_I_magnified

def run_dpr(psf_input, gain_input, data_instance, viewer):
    """
    Executes the Deblurring by Pixel Reassignment (DPR) process on a selected layer within a napari viewer, using user-defined
    PSF and gain settings to improve image clarity and detail.

    Parameters
    ----------
    psf_input : QLineEdit
        Widget for inputting the PSF value.
    gain_input : QLineEdit
        Widget for inputting the gain value.
    data_instance : object
        Contains data and metadata, such as cell diameter.
    viewer : napari.Viewer
        Viewer instance where images are displayed.

    Raises
    ------
    Error
        If no active image layer is selected in the viewer.
    """
    
    # Ensure an active layer is selected
    active_layer = viewer.layers.selection.active
    if active_layer is None:
        raise ValueError("No active layer selected")
    
    # Read PSF and gain from inputs, defaulting to preset values if empty
    psf_0 = int(psf_input.text()) if psf_input.text() else 3
    gain_0 = int(gain_input.text()) if gain_input.text() else 2
    window_size = math.ceil(data_instance.data_repository['cell_diameter'] / 2)
    
    # Apply DPR processing on the selected image layer
    DPR_img, _ = deblur_by_pixel_reassignment(active_layer.data, psf_0, gain_0, window_size)

    # Display the processed image in the viewer
    _add_image(DPR_img, viewer, name=f"DPR Corrected {active_layer.name}", operation='preprocess')
