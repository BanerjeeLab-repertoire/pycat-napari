"""WBNS wavelet background + noise separation -- split out of background.py (OperationService adoption).

``wavelet_bg_and_noise_calculation`` / ``wbns_func`` and their viewer wrappers (``run_wbns``,
``run_wavelet_noise_subtraction``). Moved VERBATIM -- no level, PSF or noise-level change.
"""
from __future__ import annotations

import warnings

import numpy as np
import scipy.ndimage as ndi

from pycat.toolbox.image_processing._base import _add_image, _napari
from pycat.utils.general_utils import dtype_conversion_func
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.tag_registry import tags_layer
from pycat.kernel import OperationService


def wavelet_bg_and_noise_calculation(image, num_levels, noise_lvl):
    """
    Decomposes an image using a wavelet transform and selectively modifies the coefficients to isolate 
    and remove noise and background before reconstructing the image. This method allows for precise 
    background and noise estimation and removal.

    Parameters
    ----------
    image : numpy.ndarray
        Input image array for processing.
    num_levels : int
        Number of decomposition levels for background estimation in the wavelet transform.
    noise_lvl : int
        Levels considered for noise estimation and removal in the wavelet decomposition.

    Returns
    -------
    Background : numpy.ndarray
        The estimated background after wavelet decomposition and reconstruction.
    Noise : numpy.ndarray
        The noise component extracted from the wavelet decomposition.
    BG_unfiltered : numpy.ndarray
        The raw background before filtering and Gaussian smoothing.

    Authors
    -------
    - Manuel Hüpfel, Institute of Applied Physics, KIT, Karlsruhe, Germany
    - Improved documentation: Christian Neureuter, University at Buffalo
    """
    # PyWavelets is imported HERE, not at module scope: 8 toolbox modules transitively import
    # image_processing_tools, and this wavelet path is the only code that needs pywt — a function-scope
    # import keeps a minimal segmentation/coloc/time-series import from dragging in PyWavelets (ci_hygiene
    # Fix 3, 1.6.222). Behaviour-identical; only WHEN pywt is imported changes.
    from pywt import wavedecn, waverecn

    # Wavelet decomposition
    coeffs = wavedecn(image, 'db1', level=None) # 'db1' denotes Daubechies wavelet with one vanishing moment
    coeffs2 = coeffs.copy()
    
    # Zeroing coefficients at specified levels to remove background
    for BGlvl in range(1, num_levels):
        coeffs[-BGlvl] = {k: np.zeros_like(v) for k, v in coeffs[-BGlvl].items()}
    
    # Wavelet reconstruction to obtain the background
    Background = waverecn(coeffs, 'db1')
    
    BG_unfiltered = Background.copy()
    Background = ndi.gaussian_filter(Background, sigma=2**num_levels) # Smooth the background with a gaussian filter w/ sigma=2^(#lvls) 
    
    # Modify coefficients for noise estimation and removal
    coeffs2[0] = np.ones_like(coeffs2[0]) # Set approximation coefficients to 1 (constant)
    for lvl in range(1, len(coeffs2) - noise_lvl):
        coeffs2[lvl] = {k: np.zeros_like(v) for k, v in coeffs2[lvl].items()} # Keep the first detail lvl only
    
    # Wavelet reconstruction to obtain the noise component
    Noise =  waverecn(coeffs2, 'db1')

    return Background, Noise, BG_unfiltered


@tags_layer('wbns', role='preprocessed', inputs=('image',),
            summary='Wavelet-based background and noise subtraction')
def wbns_func(img, psf_px_resolution, noise_lvl):
    """
    Wrapper function for wavelet-based background and noise subtraction (WBNS), adapted from [wbns_1]_. 
    It adjusts image dimensions for compatibility, performs background and noise subtraction using 
    wavelet transforms, and restores original dimensions, improving image clarity.

    Parameters
    ----------
    img : numpy.ndarray
        Input image array to be processed.
    psf_px_resolution : int
        Resolution of the image in pixels, based on the point spread function (PSF) size, used to calculate 
        decomposition levels for wavelet processing.
    noise_lvl : int
        Number of levels to consider for noise estimation and removal during wavelet processing.

    Returns
    -------
    bg_noise_output_image : numpy.ndarray
        The image with background and noise subtracted, maintaining the original data type.
    noise_output_image : numpy.ndarray
        The image with noise subtracted, but not backgroud correction, maintains the original data type.
    tuple
        Returns a tuple of numpy.ndarrays: the background and noise corrected image, and the noise-only corrected image.

    Notes
    -----
    In adapting this code, the paralell processing was removed as was the 3D z-stack processing. It would be simpe enough to refer
    to their original source code, should the functionality be desired to be re-added. The noise level also had a gaussian blur added 
    to it, similar to the reconstrcuted background, to further reduce artifacts, as leaving it as is, caused some artifacts in the
    final image. The background and noise images were scaled by 0.75 and 0.25 respectively, as leaving them as is, was a bit aggressive.
    
    References
    ----------
    .. [wbns_1]  Original Python code: [HuepfelM WBNS](https://github.com/NienhausLabKIT/HuepfelM/blob/master/WBNS/python_script/WBNS.py)
        - Related paper: Biomed. Opt. Express 12, 969-980 (2021), [DOI](https://doi.org/10.1364/BOE.413181)
    """   

    # Determine the number of decomposition levels based on image resolution
    num_levels = np.uint16(np.ceil(np.log2(psf_px_resolution)))
    input_dtype = str(img.dtype) # store the input dtype for conversion back at the end
    # These calculations utilize decimals and are therefore better run on floats, float32 saves time and memory
    img = dtype_conversion_func(img, output_bit_depth='float32') 
    shape = np.shape(img)

    # Padding to make image dimensions even
    pad_1, pad_2 = False, False
    if shape[0] % 2 != 0:
        img = np.pad(img, ((0,1), (0, 0)), 'edge')
        pad_1 = True
    if shape[1] % 2 != 0:
        img = np.pad(img, ((0,0), (0, 1)), 'edge')
        pad_2 = True

    # Suppress NumPy's ragged-array VisibleDeprecationWarning from pywavelets -- SCOPED (1.7.005): this was a bare
    # filterwarnings() here, a permanent process-wide filter installed on first call (Completion Spec D "Watch").
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*Creating an ndarray from ragged nested sequences.*")
        Background, Noise, BG_unfiltered = wavelet_bg_and_noise_calculation(img, num_levels, noise_lvl)

    # Convert arrays to float32 for processing
    Noise = np.asarray(Noise, dtype='float32')
    Background = np.asarray(Background, dtype='float32')
    BG_unfiltered = np.asarray(BG_unfiltered, dtype='float32')

    # Undo padding
    if pad_1:
        img = img[:-1,:]
        Noise = Noise[:-1,:]
        Background = Background[:-1,:]
        BG_unfiltered = BG_unfiltered[:-1,:]
    if pad_2:
        img = img[:,:-1]
        Noise = Noise[:,:-1]
        Background = Background[:,:-1]
        BG_unfiltered = BG_unfiltered[:,:-1]

    # Background subtraction and positivity constraint
    bg_subtracted = img - (0.65*Background)
    bg_subtracted[bg_subtracted < 0] = 0

    # Noise correction and thresholding
    Noise[Noise < 0] = 0
    noise_threshold = np.mean(Noise) + 2*np.std(Noise) # 2 sigma threshold reduces artifacts
    Noise[Noise > noise_threshold] = noise_threshold 
    Noise_smooth = ndi.gaussian_filter(Noise, sigma=(num_levels)) # Gaussian filter smooths the noise to further reduce artifacts

    # Noise subtraction and positivity constraint from the original image
    noise_corrected = img - (0.7*Noise_smooth)
    noise_corrected[noise_corrected < 0] = 0

    # Noise subtraction and positivity constraint from the background corrected image
    bg_noise_corrected = bg_subtracted - (0.2*Noise_smooth)
    bg_noise_corrected[bg_noise_corrected < 0] = 0

    # Convert the output image back to the original data type
    bg_noise_output_image = dtype_conversion_func(bg_noise_corrected, output_bit_depth=input_dtype)
    noise_output_image = dtype_conversion_func(noise_corrected, output_bit_depth=input_dtype)

    return bg_noise_output_image, noise_output_image


def run_wbns(psf_input, noise_level_input, viewer):
    """
    Executes the WBNS process on an active image layer within a napari viewer, enhancing the image by removing 
    background noise and correcting noise artifacts based on user-input PSF and noise level settings.

    Parameters
    ----------
    psf_input : QLineEdit
        Input field for the PSF value, determining the decomposition level for wavelet processing.
    noise_level_input : QLineEdit
        Input field for the noise level, specifying how many wavelet decomposition levels are used for noise reduction.
    viewer : napari.viewer.Viewer
        Viewer instance where the processed images are displayed.

    Raises
    ------
    Error
        If no active image layer is selected.

    Notes
    -----
    The function retrieves settings from user inputs, applies the WBNS algorithm to the selected image, 
    and updates the viewer with the processed image, adding it as a new layer with a descriptive name.
    """

    active_layer = viewer.layers.selection.active
    # Check if their is an active layer, and that it is a Napari image layer
    if active_layer is not None and isinstance(active_layer, _napari().layers.Image):
        image = active_layer.data
    else:
        raise ValueError("No active image layer selected.")

    psf_fwhm = int(psf_input.text()) if psf_input.text() else 3
    noise_lvl = int(noise_level_input.text()) if noise_level_input.text() else 1

    # Process the image with the WBNS function
    WBNS_img, _ = OperationService.produce('wbns', {'img': image},
        {'psf_px_resolution': psf_fwhm, 'noise_lvl': noise_lvl})

    # Display the processed image
    _add_image(WBNS_img, viewer, name=f"BG and Noise Corrected {active_layer.name}")


def run_wavelet_noise_subtraction(psf_input, noise_level_input, viewer):
    """
    Applies the wavelet noise subtraction from the WBNS process to an active image layer selected in a viewer.
    Reads PSF and noise level values from input fields, processes the selected image using the WBNS algorithm, 
    and updates the viewer with the corrected image.

    Parameters
    ----------
    psf_input : QLineEdit
        Input field for specifying the point spread function (PSF) value, which determines the wavelet decomposition levels for noise processing.
    noise_level_input : QLineEdit
        Input field for setting the noise level, indicating the intensity of noise reduction to apply.
    viewer : napari.Viewer
        Viewer instance where the processed images are displayed.

    Raises
    ------
    Error
        If no active image layer is selected.
    """
    
    active_layer = viewer.layers.selection.active
    # Check if their is an active layer, and that it is a Napari image layer
    if active_layer is not None and isinstance(active_layer, _napari().layers.Image):
        image = active_layer.data
    else:
        napari_show_warning("No active image layer selected.")
        return

    psf_fwhm = int(psf_input.text()) if psf_input.text() else 3
    noise_lvl = int(noise_level_input.text()) if noise_level_input.text() else 1

    # Process the image with the WBNS function
    _, wavelet_noise_corrected = OperationService.produce('wbns', {'img': image},
        {'psf_px_resolution': psf_fwhm, 'noise_lvl': noise_lvl})

    # Display the processed image
    _add_image(wavelet_noise_corrected, viewer, name=f"Wavelet Noise Corrected {active_layer.name}")
