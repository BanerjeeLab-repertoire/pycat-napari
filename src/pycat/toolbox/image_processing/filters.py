"""Image filters + enhancement - split out of image_processing_tools (1.6.251).

The 2D and pseudo-3D linear filters and enhancement operators: Gaussian smoothing, Gabor texture filtering,
difference-of-Gaussian blob enhancement, Laplacian-of-Gaussian filter/enhancement, edge-preserving bilateral
filtering, and the combined peak/edge enhancer. Their viewer wrappers moved on to filter_panels.py. Moved VERBATIM - no kernel,
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
from pycat.toolbox.image_processing._base import apply_rescale_intensity, invert_image, _safe_equalize_adapthist, pseudo3d_tri_planar_filter


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


def morphological_gaussian_filter(image, filter_size=2):
    """Grey dilation then erosion with a disk of ``filter_size``, then a Gaussian of sigma ``filter_size`` --
    the computation behind the Morphological Gaussian button, callable headlessly (batch replay)."""
    input_dtype = str(image.dtype)  # Store the input data type for conversion back at the end
    # Convert the image to float32 for processing
    img = dtype_conversion_func(image, output_bit_depth='float32')
    # Create a disk-shaped structural element based on the filter size
    selem = sk.morphology.disk(int(filter_size))
    # Apply morphological dilation to emphasize bright structures, then erosion to refine them
    img = ndi.grey_dilation(img, footprint=selem)
    img = ndi.grey_erosion(img, footprint=selem)
    # Apply Gaussian smoothing to reduce noise, with the filter size influencing the smoothing extent
    img = ndi.gaussian_filter(img, filter_size)
    # Convert the processed image back to the original data type
    return dtype_conversion_func(img, output_bit_depth=input_dtype)


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


# Moved to filter_panels.py; re-exported so existing imports from filters keep working.
from pycat.toolbox.image_processing.filter_panels import (  # noqa: E402,F401
    run_peak_and_edge_enhancement, run_apply_laplace_of_gauss_filter, run_morphological_gaussian_filter,
    run_clahe, run_apply_bilateral_filter)
