"""Image filters + enhancement - split out of image_processing_tools (1.6.251).

The 2D and pseudo-3D linear filters and enhancement operators: Gaussian smoothing, Gabor texture filtering,
difference-of-Gaussian blob enhancement, Laplacian-of-Gaussian filter/enhancement, edge-preserving bilateral
filtering, and the combined peak/edge enhancer - each with its viewer wrapper. Moved VERBATIM - no kernel,
sigma, or operation change; pinned by test_image_processing_filters_characterization (exact shape/dtype/sum/
min/max per operator). Build on the _base primitives (rescale/invert/CLAHE/pseudo3d/napari helpers).
"""
from __future__ import annotations

import math
import numpy as np
import skimage as sk
import scipy.ndimage as ndi
import SimpleITK as sitk
from pycat.utils.tag_registry import tags_layer
from pycat.utils.general_utils import dtype_conversion_func
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.toolbox.image_processing._base import apply_rescale_intensity, invert_image, _safe_equalize_adapthist, pseudo3d_tri_planar_filter, _add_image, _napari


@tags_layer('gaussian', role='preprocessed', inputs=('image',),
            summary='Gaussian smoothing')
def gaussian_smooth_2d(image: np.ndarray, sigma: float) -> np.ndarray:
    """Thin wrapper around ndi.gaussian_filter for use with pseudo3d_tri_planar_filter."""
    return ndi.gaussian_filter(np.asarray(image).astype(np.float32), sigma=sigma)


@tags_layer('gaussian_3d', role='preprocessed', requirements=('z_stack',),
            summary='Pseudo-3D (tri-planar) Gaussian smoothing')
def gaussian_smooth_3d_pseudo(volume: np.ndarray, sigma: float) -> np.ndarray:
    """Pseudo-3D (tri-planar) Gaussian smoothing of a (Z, H, W) volume."""
    return pseudo3d_tri_planar_filter(volume, gaussian_smooth_2d, sigma=sigma)


@tags_layer('gabor_3d', role='preprocessed', requirements=('z_stack',),
            summary='Pseudo-3D (tri-planar) Gabor texture filter')
def gabor_filter_3d_pseudo(volume: np.ndarray) -> np.ndarray:
    """
    Pseudo-3D (tri-planar) Gabor filtering of a (Z, H, W) volume.
    Reuses the exact same precomputed 2D Gabor kernel bank
    (gabor_filter_func / _GABOR_KERNELS) for every plane in every
    orientation pass.
    """
    return pseudo3d_tri_planar_filter(volume, gabor_filter_func)


@tags_layer('dog', role='preprocessed', inputs=('image',),
            summary='Difference-of-Gaussians blob enhancement', aliases=('difference_of_gaussians',))
def dog_blob_enhance_2d(image: np.ndarray, sigma_lo: float = 2.0, sigma_hi: float = 3.2) -> np.ndarray:
    """
    Difference-of-Gaussians blob enhancement (bright-blob convention,
    matching apply_laplace_of_gauss_enhancement's inverted-LoG sign).
    Thin wrapper for use with pseudo3d_tri_planar_filter.
    """
    img = np.asarray(image).astype(np.float32)
    lo = ndi.gaussian_filter(img, sigma=sigma_lo)
    hi = ndi.gaussian_filter(img, sigma=sigma_hi)
    enhanced = np.clip(lo - hi, 0, None)
    mx = enhanced.max()
    return (enhanced / mx if mx > 0 else enhanced).astype(np.float32)


@tags_layer('dog_3d', role='preprocessed', requirements=('z_stack',),
            summary='Pseudo-3D difference-of-Gaussians blob enhancement')
def dog_blob_enhance_3d_pseudo(volume: np.ndarray, sigma_lo: float = 2.0,
                               sigma_hi: float = 3.2) -> np.ndarray:
    """Pseudo-3D (tri-planar) DoG blob enhancement of a (Z, H, W) volume."""
    return pseudo3d_tri_planar_filter(
        volume, dog_blob_enhance_2d, sigma_lo=sigma_lo, sigma_hi=sigma_hi)


_GABOR_KERNELS = [
    np.abs(sk.filters.gabor_kernel(frequency=1.0,
                                    theta=k / 4.0 * np.pi,
                                    bandwidth=1.0))
    for k in range(4)
]


@tags_layer('gabor', role='preprocessed', inputs=('image',),
            summary='Gabor texture filter')
def gabor_filter_func(image):
    """
    Applies a Gabor filter to an image to enhance texture and feature visibility at specific orientations. This function 
    utilizes a bank of Gabor filters at four distinct angles (0, 45, 90, and 135 degrees), which helps in capturing edge and 
    texture information effectively. The results from these orientations are summed to create a composite image that 
    emphasizes variations in pixel intensity related to the filter orientations, thereby enhancing the visibility of 
    features aligned with these angles.

    Parameters
    ----------
    image : numpy.ndarray
        A 2D array representing the input image. The image can be of any unsigned data type.

    Returns
    -------
    numpy.ndarray
        A 2D numpy array of the enhanced image. This output emphasizes the texture and edge features present in the 
        original image at the specified filter orientations. The output image is converted back to the original image 
        data type, ensuring compatibility with further processing or visualization steps.

    Notes
    -----
    The function processes the image using a float32 intermediate data type for filtering operations to ensure accuracy 
    while maintaining performance. The output is then rescaled to emphasize feature variations and converted back to the 
    original image data type.
    """

    input_dtype = str(image.dtype)  # Store the input image's data type for later conversion back
    img = dtype_conversion_func(image, 'float32')  # Convert the image to float32 for processing

    # Initialize a list to store the filtered images
    filtered_images = []
    # Gabor kernels are precomputed at module level (_GABOR_KERNELS).
    # The 4 convolutions are independent — run them in a thread pool.
    # ThreadPoolExecutor (not process) because ndi.convolve releases the GIL
    # for most of its execution, so threads genuinely run concurrently.
    from concurrent.futures import ThreadPoolExecutor as _TPE
    def _convolve_k(k):
        return ndi.convolve(img, k, mode='constant')
    with _TPE(max_workers=4) as _pool:
        filtered_images = list(_pool.map(_convolve_k, _GABOR_KERNELS))

    # Sum the results of the filtering to enhance edges and textures
    filtered_sum = np.sum(filtered_images, axis=0)
    # Rescale the sum of filtered images to adjust the intensity range
    rescaled_sum = apply_rescale_intensity(filtered_sum, out_min=0.75, out_max=1.0).astype(np.float32)
    # Multiply the original image by the rescaled sum to emphasize enhanced features
    enhanced_image = rescaled_sum * img

    # Convert the enhanced image back to its original data type
    enhanced_image = dtype_conversion_func(enhanced_image, output_bit_depth=input_dtype)

    return enhanced_image


@tags_layer('peak_edge', role='preprocessed',
            summary='Peak and edge enhancement')
def peak_and_edge_enhancement_func(image, ball_radius):
  """
  Enhances the edges and peaks of features within an image through a sequence of image processing operations.
  This includes Gaussian background division, application of a Gabor filter, morphological operations, and adaptive
  histogram equalization to improve contrast.

  Parameters
  ----------
  image : numpy.ndarray
      The input image to be enhanced, which can be of any unsigned integer data type.
  ball_radius : int
      Determines the size of the Gaussian filter used for initial smoothing, indirectly affecting the scale of
      features targeted for enhancement.

  Returns
  -------
  output_image : numpy.ndarray
      The enhanced image, showing improved visibility of edges and peaks. The output retains the same data type as the input.

  Notes
  -----
  The sequence starts with Gaussian background division to highlight edges by suppressing steady background features,
  followed by a Gabor filter for edge and texture enhancement. Morphological dilation and erosion emphasize structures,
  and adaptive histogram equalization adjusts contrast. The process is designed for small to medium-sized features,
  making it suitable for applications like microscopy or detailed texture analysis.
  """

  input_dtype = str(image.dtype) # Store the input image's data type for later conversion back
  img = dtype_conversion_func(image, 'float32') # Convert the image data type to float32 for processing

  # Apply a large gaussian filter to smooth all objects in the image into the background 
  gaussian_bg = ndi.gaussian_filter(img, sigma=(ball_radius * 2))

  # Perform gaussian background division for edge illumination enhancement
  bg_division = img / (gaussian_bg + 0.00001)

  # Rescale intensity of the background-divided image
  bg_division_rescaled = apply_rescale_intensity(bg_division, out_min=0.75, out_max=1.0)
  # Apply the rescaled background-divided image as an attenuation mask
  img *= bg_division_rescaled

  # Enhance edges and peaks using a Gabor filter
  gabor_img = gabor_filter_func(img)

  # Create a structural element for morphological operations
  selem = sk.morphology.disk(1) 
  # Apply morphological dilation to enhance bright structures
  gabor_img = ndi.grey_dilation(gabor_img, footprint=selem)
  # Apply morphological erosion to refine the structures
  gabor_img = ndi.grey_erosion(gabor_img, footprint=selem)

  # Smooth the enhanced image with a small Gaussian filter
  gabor_img = ndi.gaussian_filter(gabor_img, 0.5)

  # CLAHE tile scaled to the rolling-ball radius (v1.0.0 behavior). A fixed
  # 64-px tile is MORE aggressive for large ball radii (smaller tile => more
  # local equalization), which over-enhances background and suppresses
  # low-contrast puncta; scaling the tile to ball_radius*4 keeps it gentle.
  k_size = math.ceil(ball_radius * 4)
  output_image = _safe_equalize_adapthist(gabor_img, kernel_size=k_size,
                                                 clip_limit=0.0025)

  # Convert the output image back to the original input data type for consistency
  output_image = dtype_conversion_func(output_image, output_bit_depth=input_dtype)

  return output_image

def run_peak_and_edge_enhancement(data_instance, viewer):
  """
  Applies peak and edge enhancement techniques to the currently active image layer in a Napari viewer. The enhancement
  process includes Gabor filtering, morphological operations, Gaussian smoothing, and adaptive histogram equalization.

  Parameters
  ----------
  viewer : napari.Viewer
      The viewer containing the image layer to be enhanced.

  Raises
  ------
  Error
      If no active image layer is selected, preventing the function from proceeding.

  Notes
  -----
  The function retrieves the currently active image layer, applies the `peak_and_edge_enhancement_func`, and adds the
  enhanced image back as a new layer to the viewer.
  """

  ball_radius = math.ceil(data_instance.data_repository['ball_radius'])

  # Retrieve the currently active image layer from the viewer
  active_layer = viewer.layers.selection.active

  # Validate that an active layer is selected
  if active_layer is None or not isinstance(active_layer, _napari().layers.Image):
      # Raise an error if no layer is currently active
      raise ValueError("No active image layer selected")

  # Retrieve the image data from the active layer
  image = active_layer.data

  # Apply the peak and edge enhancement function to the input image
  enhanced_image = peak_and_edge_enhancement_func(image, ball_radius)

  # Add the enhanced image as a new layer to the viewer with a descriptive name
  _add_image(enhanced_image, viewer, name=f"Peak & Edge Enhanced {active_layer.name}", operation='log_enhance')


@tags_layer('log', role='preprocessed', inputs=('image',),
            summary='Laplacian-of-Gaussian filter', aliases=('laplacian_of_gaussian',))
def apply_laplace_of_gauss_filter(image, sigma=3):
    """
    Applies a Laplacian of Gaussian (LoG) filter to an input image for edge detection. This method combines 
    Gaussian smoothing with a Laplacian filter to reduce noise before detecting edges, enhancing feature definition 
    and image quality.

    Parameters
    ----------
    image : numpy.ndarray
        The input image to be processed.
    sigma : float
        Standard deviation of the Gaussian kernel, which determines the level of blurring and influences edge detection sensitivity.

    Returns
    -------
    gauss_laplace_image : numpy.ndarray
        The image processed with the LoG filter, highlighting edges and returning it in the original data type.
    """

    input_dtype = str(image.dtype)  # Store the input image data type
    img = dtype_conversion_func(image, 'float32')  # Convert the image to float32 for processing

    # Apply the LoG filter to the image
    gauss_laplace_image = ndi.gaussian_laplace(img, sigma=sigma) 

    # Convert the image back to the original data type
    gauss_laplace_image = dtype_conversion_func(gauss_laplace_image, input_dtype)  
    
    return gauss_laplace_image

@tags_layer('log_enhance', role='preprocessed',
            summary='Laplacian-of-Gaussian enhancement (LoG added back)')
def apply_laplace_of_gauss_enhancement(image, sigma=3):
    """
    Enhances an image using a Laplacian of Gaussian (LoG) filter followed by intensity rescaling and inversion to highlight edges.
    The process involves edge detection, shifting image intensity to ensure all values are positive, rescaling the intensity to a
    specified range, inverting the intensity to emphasize edges, and optionally multiplying with the original image for attenuation.

    Parameters
    ----------
    image : numpy.ndarray
        The input image to be enhanced.
    sigma : float
        The standard deviation of the Gaussian kernel used in the LoG filter.

    Returns
    -------
    enhanced_img : numpy.ndarray
        The enhanced image, which is the input image attenuated by the processed LoG image for edge enhancement.
    inverted_img : numpy.ndarray
        The inverted LoG image, useful for visualization and analysis, can be applied as an attenuation mask to the original image.
    """

    input_dtype = str(image.dtype)  # Store the input image data type
    img = dtype_conversion_func(image, 'float32')  # Convert the image to float32 for processing

    # Apply LoG filter to detect edges and smooth the image
    LoG_img = apply_laplace_of_gauss_filter(img, sigma=sigma)
    
    # Shift the image to ensure all values are positive
    shifted_image = LoG_img + np.abs(np.min(LoG_img))

    # Rescale the intensity to a narrow range to prepare for inversion
    rescaled_img = apply_rescale_intensity(shifted_image, out_min=0.0, out_max=0.1)
    
    # Invert the image to emphasize low-intensity edges
    inverted_img = invert_image(rescaled_img)
    
    # Apply the inverted LoG as an attenuation mask, this slighty enhances the contrast of edges
    enhanced_img = inverted_img * img

    # Convert the image back to the original data type
    enhanced_img = dtype_conversion_func(enhanced_img, input_dtype)
    
    return enhanced_img, inverted_img


def run_apply_laplace_of_gauss_filter(sigma_input, viewer):
    """
    Applies the Laplacian of Gaussian (LoG) filter to the currently active image layer in a Napari viewer, 
    using a user-specified sigma value from UI input. This enhances the image by highlighting edges through LoG filtering.

    Parameters
    ----------
    sigma_input : UI Element
        A UI element that allows the user to input the sigma value for the LoG filter.
    viewer : napari.Viewer
        The Napari viewer instance where the image layer is displayed and processed.

    Raises
    ------
    Error
        If no active image layer is selected in the viewer, prevent the application of the filter.
    """

    active_layer = viewer.layers.selection.active

    # Check if their is an active layer, and that it is a Napari image layer
    if active_layer is not None and isinstance(active_layer, _napari().layers.Image):
        image = active_layer.data
    else:
        raise ValueError("No active image layer selected.")
    
    sigma = float(sigma_input.text()) if sigma_input.text() else 3

    # Apply the LoG filter to the input image
    LoG_image = apply_laplace_of_gauss_filter(image, sigma)

    # Add the LoG filtered image to the viewer
    _add_image(LoG_image, viewer, name=f"LoG of {active_layer.name}", operation='log_enhance')


def run_morphological_gaussian_filter(filter_size_input, viewer):
    """
    Applies morphological operations and Gaussian smoothing to the active image layer in the Napari viewer,
    enhancing structural features and reducing noise. The process involves morphological dilation and erosion
    followed by Gaussian smoothing, with the results added as a new layer to the viewer.

    Parameters
    ----------
    filter_size_input : text input
        A user interface element that allows the user to input the filter size, influencing the extent of the
        morphological operations and Gaussian smoothing.
    viewer : Viewer
        An image viewer object that contains image layers, such as in a Napari viewer.

    Raises
    ------
    Error
        If no active image layer is selected in the viewer.

    Notes
    -----
    The filter size from the user input determines the size of the disk-shaped structural element used for
    morphological dilation and erosion, directly impacting the degree of feature enhancement and noise reduction.
    """

    # Retrieve the currently active image layer from the viewer
    active_layer = viewer.layers.selection.active
    
    # Validate that an active layer is selected
    if active_layer is None or not isinstance(active_layer, _napari().layers.Image):
        # Raise an error if no layer is currently active
        raise ValueError("No active image layer selected")
    
    input_dtype = str(active_layer.data.dtype)  # Store the input data type for conversion back at the end
    # Retrieve the image data from the active layer
    image = active_layer.data
    # Convert the image to float32 for processing
    img = dtype_conversion_func(image, output_bit_depth='float32')
    
    # Determine the filter size from the user input or default to 2
    filter_size = int(filter_size_input.text()) if filter_size_input.text() else 2
    
    # Create a disk-shaped structural element based on the filter size
    selem = sk.morphology.disk(filter_size)
    
    # Apply morphological dilation to emphasize bright structures in the image
    img = ndi.grey_dilation(img, footprint=selem)
    
    # Apply morphological erosion to refine bright structures
    img = ndi.grey_erosion(img, footprint=selem)

    # Apply Gaussian smoothing to reduce noise, with the filter size influencing the smoothing extent
    img = ndi.gaussian_filter(img, filter_size)

    # Convert the processed image back to the original data type
    image = dtype_conversion_func(img, output_bit_depth=input_dtype)

    # Add the processed image as a new layer to the viewer with a descriptive name
    _add_image(image, viewer, name=f"Filtered {active_layer.name}", operation='gaussian_blur')


def run_clahe(clip_input, k_size_input, viewer):
    """
    Applies Contrast Limited Adaptive Histogram Equalization (CLAHE) to the currently active image layer in the Napari
    viewer. This technique enhances the contrast of the image by dividing it into small blocks and applying histogram
    equalization to each block independently, limiting the amplification of noise common in standard methods.

    Parameters
    ----------
    clip_input : UI Element (Text Input)
        A UI element that allows the user to input the clip limit value for CLAHE.
    k_size_input : UI Element (Text Input)
        A UI element that allows the user to input the kernel size for CLAHE.
    viewer : napari.Viewer
        The Napari viewer instance where the image layer is displayed and processed.

    Raises
    ------
    Error
        If no active image layer is selected in the viewer.

    Notes
    -----
    The function processes the image by converting it to float32 for enhanced precision during CLAHE and then converts
    it back to its original data type. The clip limit and kernel size are adjustable, allowing for fine-tuning of the
    contrast enhancement based on specific image requirements.
    """
    
    # Retrieve the currently active image layer from the viewer
    active_layer = viewer.layers.selection.active
    
    # Validate that an active layer is selected
    if active_layer is None or not isinstance(active_layer, _napari().layers.Image):
        # Raise an error if no layer is currently active
        raise ValueError("No active image layer selected")
    
    input_dtype = str(active_layer.data.dtype)  # Store the input data type for conversion back at the end
    # Retrieve the image data from the active layer
    image = active_layer.data
    # Convert the image to float32 for processing
    img = dtype_conversion_func(image, output_bit_depth='float32')
    
    # Retrieve clip limit and kernel size from the UI input, falling back to defaults if necessary
    clip_val = float(clip_input.text()) if clip_input.text() else 0.0025
    k_size = int(k_size_input.text()) if k_size_input.text() else None

    # The number of bins is set dynamically based on the image height
    no_bins = image.shape[0]

    # Apply CLAHE to the image
    CLAHE_img = _safe_equalize_adapthist(img, kernel_size=k_size, clip_limit=clip_val, nbins=no_bins)

    # Convert the processed image back to its original data type
    CLAHE_img = dtype_conversion_func(CLAHE_img, output_bit_depth=input_dtype)

    # Add the CLAHE-enhanced image as a new layer to the Napari viewer
    _add_image(CLAHE_img, viewer, name=f"CLAHE Contrast EQed {active_layer.name}", operation='clahe')


@tags_layer('bilateral', role='preprocessed', inputs=('image',),
            summary='Edge-preserving bilateral filter')
def apply_bilateral_filter(image, radius):
    """
    Applies a bilateral filter to an image to reduce noise while preserving edges. The filter considers both
    spatial proximity and intensity similarity between pixels, which makes it particularly effective for 
    denoising while maintaining important structural details in images.

    Parameters
    ----------
    image : numpy.ndarray
        The input image array to be processed.
    radius : int
        The radius of the filter, determining the size of the spatial neighborhood for smoothing.

    Returns
    -------
    filtered_image : numpy.ndarray
        The image with noise reduced using the bilateral filter, returned in the original data type.

    Notes
    -----
    The function uses the SimpleITK library for the bilateral filter application, ensuring high performance
    and quality of noise reduction. Images are temporarily converted to float32 for processing to maintain precision.
    """

    input_dtype = str(image.dtype)  # Store the input data type for conversion back at the end
    img = dtype_conversion_func(image, output_bit_depth='float32') # Convert the image to float32 for processing

    # Convert the image to SimpleITK format
    img_sitk = sitk.GetImageFromArray(img)
    # Apply the bilateral filter to the image
    filtered_img_sitk = sitk.Bilateral(img_sitk, radius)
    # Convert the filtered image back to a NumPy array
    img = sitk.GetArrayFromImage(filtered_img_sitk)

    # Deprecated skimage bilateral filter (for some reason the skimage version adds some sort of shift to the image)
    # Apply the bilateral filter to the image
    #filtered_image = sk.restoration.denoise_bilateral(image, win_size=2*radius, multichannel=False)

    # Convert the filtered image back to the original data type
    filtered_image = dtype_conversion_func(img, output_bit_depth=input_dtype)
    
    return filtered_image

def run_apply_bilateral_filter(radius_input, viewer):
    """
    Applies a bilateral filter to an active image layer in a Napari viewer to reduce noise while preserving 
    important details. The filter radius is retrieved from the user's input.

    Parameters
    ----------
    radius_input : QLineEdit
        The input field where users specify the filter radius.
    viewer : napari.Viewer
        The viewer instance where the processed image will be displayed.

    Raises
    ------
    Error
        If no active image layer is selected.

    Notes
    ----
    The function retrieves the radius from the input, applies the bilateral filter, and displays the result as a 
    new layer in the viewer, facilitating immediate visual feedback.
    """

    active_layer = viewer.layers.selection.active
    # Check if their is an active layer, and that it is a Napari image layer
    if active_layer is not None and isinstance(active_layer, _napari().layers.Image):
        image = active_layer.data
    else:
        napari_show_warning("No active image layer selected.")
        return

    # Get the radius value from the input field
    radius = float(radius_input.text()) if radius_input.text() else 2

    # Apply the bilateral filter to the image
    filtered_image = apply_bilateral_filter(image, radius)

    # Add the filtered image as a new layer to the viewer
    _add_image(filtered_image, viewer, name=f"Bilateral Filtered {active_layer.name}")
