"""
Image Processing Module for PyCAT

This module contains functions for image processing tasks, including image adjustments, enhancements, and filters. 
Most functions are decomposed into a function which actually performs the processing and a function which interacts
with the Napari viewer. This separation allows for easier testing and debugging of the processing functions. It also
allows future users to use the processing functions without the Napari viewer if needed, or to add the functions to 
Napari as plugins, providing flexibility and reusability.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Standard library imports
import math
import warnings

# Third party imports
import numpy as np

from pycat.utils.tag_registry import tags_layer
import skimage as sk
# GUI is imported LAZILY. This module's pure array operations (filters, background
# removal, upscaling, intensity rescaling) are imported by other SCIENTIFIC modules --
# feature_analysis_tools among them -- and a top-level `import napari` made every one
# of them, and their tests, un-importable without a display. The coupling is
# TRANSITIVE: one GUI import at the base of the graph blocks everything above it.
# Verified: 4 of 6 science test modules could not even be COLLECTED without napari/Qt.
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.notify import show_info as napari_show_info


# ---------------------------------------------------------------------------
# napari image-add helper  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    _add_image)



# ---------------------------------------------------------------------------
# lazy napari accessor  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    _napari)

import scipy.ndimage as ndi
from scipy.interpolate import RectBivariateSpline
import SimpleITK as sitk

# Local application imports
from pycat.utils.general_utils import dtype_conversion_func, get_default_intensity_range 
# ui_utils pulls in Qt -> imported at CALL time inside _add_image().


# ---------------------------------------------------------------------------
# version-safe CLAHE (equalize_adapthist)  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    _safe_equalize_adapthist)






# ---------------------------------------------------------------------------
# Pseudo-3D (tri-planar) linear filtering for Z-stack volumes
# ---------------------------------------------------------------------------
#
# For a genuinely 2D linear filter (Gaussian smoothing, Gabor convolution,
# LoG/DoG blob detection), applying it slice-by-slice down a Z-stack's XY
# planes only accounts for structure within each optical plane — Z-direction
# continuity is ignored entirely, which can leave abrupt slice-to-slice
# discontinuities in the filtered volume.
#
# Tri-planar pseudo-3D filtering runs the *same* 2D kernel three times —
# once slicing along XY (the standard per-plane pass), once along XZ, and
# once along YZ — then averages the three volumes (equivalently: sums the
# three contributions and scales by 1/3). Each pass is a cheap, well-tested
# 2D filter; averaging the three orthogonal responses gives a result that
# is sensitive to structure in all three spatial directions without the
# cost or complexity of a true N-D filter implementation. This is standard
# practice for approximating isotropic 3D response from 2D building blocks
# (e.g. tri-planar Hessian/Frangi approximations, tri-planar LBP texture).
#
# Only apply this to genuinely LINEAR filters (Gaussian, Gabor, LoG/DoG).
# Nonlinear operations — CLAHE, rolling-ball, bilateral filtering,
# watershed, morphological tophat — do not have the same orthogonal-
# averaging justification and should stay per-XY-slice only.

# ---------------------------------------------------------------------------
# pseudo-3D tri-planar filter wrapper  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    pseudo3d_tri_planar_filter)



# ---------------------------------------------------------------------------
# 2D/pseudo-3D Gaussian + Gabor + DoG blob-enhance filters  ->  moved to filters.py (1.6.251)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.filters import (  # noqa: E402,F401
    gaussian_smooth_2d, gaussian_smooth_3d_pseudo, gabor_filter_3d_pseudo, dog_blob_enhance_2d, dog_blob_enhance_3d_pseudo)



# Image adjustments #

# ---------------------------------------------------------------------------
# intensity rescaling (registered op)  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    apply_rescale_intensity)



def run_apply_rescale_intensity(out_min_input, out_max_input, viewer):
    """
    Applies intensity rescaling to the currently active image layer in a Napari viewer based on user-specified
    minimum and maximum output intensity values.

    This function interacts with a Napari viewer, allowing users to rescale the intensity of the selected image
    layer directly through UI elements for specifying the intensity range. The adjusted image is then displayed
    in the viewer.

    Parameters
    ----------
    out_min_input : UI Element
        A UI element for user input of the minimum output intensity value. Typically a text input field.
    out_max_input : UI Element
        A UI element for user input of the maximum output intensity value. Typically a text input field.
    viewer : napari.Viewer
        The Napari viewer instance where the image layers are managed and displayed.

    Raises
    ------
    Error
        If no active image layer is selected or if the active layer is not compatible as an image layer for processing.

    Notes
    -----
    It is assumed that `out_min_input` and `out_max_input` are components of a graphical user interface and can retrieve
    textual input from the user, which is then converted to floating point values for the rescaling function. 

    Error handling for digit inputs should be added. For example:

    .. code-block:: python

        # Ensure the input is valid and convert to an integer
        if new_label_input.text() == "" or not new_label_input.text().isdigit():
            print("Please enter a valid label value.")
            return
    """


    active_layer = viewer.layers.selection.active
    # Check if their is an active layer, and that it is a Napari image layer
    if active_layer is not None and isinstance(active_layer, _napari().layers.Image):
        image = active_layer.data
    else:
        napari_show_warning("No active image layer selected.")
        return

    
    # Use the user provided values if available, otherwise use the defaults
    out_min = float(out_min_input.text()) if out_min_input.text() else None
    out_max = float(out_max_input.text()) if out_max_input.text() else None
    
    # Apply the rescale intensity function to the image
    rescaled_image = apply_rescale_intensity(image, out_min, out_max)

    # Add the rescaled image to the viewer
    _add_image(rescaled_image, viewer, name=f"Intensity Rescaled {active_layer.name}", operation='rescale_intensity')


# ---------------------------------------------------------------------------
# image inversion (registered op)  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    invert_image)



def run_invert_image(viewer):
    """
    Inverts the intensity of the currently active image layer in the Napari viewer using PyQt GUI components.

    This function retrieves the currently selected image layer from the Napari viewer, inverts its intensity,
    and displays the result as a new layer in the viewer. This is particularly useful for enhancing visual 
    contrast or highlighting specific features in biophysical imaging data.

    Parameters
    ----------
    viewer : napari.Viewer
        The Napari viewer instance, used for displaying and processing the image layers. The viewer is expected
        to be part of a PyQt application, integrating seamlessly with other GUI components.

    Raises
    ------
    Error
        If no active image layer is selected, or if the selected layer is not suitable for processing (e.g., not an image layer).
    """

    active_layer = viewer.layers.selection.active
    
    # Check if their is an active layer, and that it is a Napari image layer
    if active_layer is not None and isinstance(active_layer, _napari().layers.Image):
        image = active_layer.data
    else:
        raise ValueError("No active image layer selected.")
    
    # Apply the invert image function to the rescaled LoG filtered image
    inverted_image = invert_image(image)

    # Add the inverted image to the viewer
    _add_image(inverted_image, viewer, name=f"Inverted {active_layer.name}", operation='rescale_intensity')


# ---------------------------------------------------------------------------
# bicubic upscaling (registered op)  ->  moved to _base.py (1.6.249)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing._base import (  # noqa: E402,F401
    upscale_image_interp)




def run_upscaling_func(data_checkbox, data_instance, viewer):
    """
    Applies the upscale_image_interp function to selected image layers within a viewer interface, potentially
    updating related data in the data_instance based on the upscaling process and user input. It iterates over 
    selected image layers, applies upscaling, and optionally updates related data in the data_instance. The result 
    is added back to the viewer as a new image layer.

    Parameters
    ----------
    viewer : napari.Viewer
        The viewer object that contains the image layers to be upscaled.
    data_instance : DataInstance
        An object containing data and parameters that may need updating based on the image upscaling (e.g., object
        sizes, resolution information).
    data_checkbox : Checkbox
        A user interface element indicating whether to update certain data in the data_instance based on the upscaling
        results.

    Notes
    -----
    The function ensures that upscaling does not apply to images already at or above a specific size threshold (2048x2048).
    It also corrects data types and ranges for upscaled images to ensure they are suitable for further processing or
    display within the viewer. This function interacts directly with the Napari viewer's GUI elements, facilitating
    a seamless user experience in adjusting image properties.
    """

    # Get the currently selected layers from the viewer
    # Snapshot the selection BEFORE the loop as a plain list filtered to Image
    # layers only. viewer.layers.selection is a live set — napari auto-selects
    # each newly added layer, which mutates the set mid-iteration and causes
    # every upscaled output to be upscaled again, producing duplicate layers.
    import napari.layers as _nl_up
    # Snapshot + de-duplicate. viewer.layers.selection is a live set that napari
    # mutates as new layers are added (auto-selecting them); we snapshot to a list
    # up front. We also de-duplicate by layer identity in case the same layer
    # appears more than once, and we skip any layer whose upscaled output already
    # exists — both of which otherwise produce two identical "Upscaled ..." layers
    # from a single source.
    _seen_ids = set()
    selected_layers = []
    for l in list(viewer.layers.selection):
        if not isinstance(l, _nl_up.Image):
            continue
        if id(l) in _seen_ids:
            continue
        _seen_ids.add(id(l))
        selected_layers.append(l)

    # Check if no image layers are selected and exit if true
    if not selected_layers:
        napari_show_warning("No image layers selected. Select one or more Image layers.")
        return

    # Determine whether the user has requested data updates based on the checkbox state
    update_data = data_checkbox.isChecked()

    # Loop through the snapshotted Image-layer list
    for layer in selected_layers:
        # Skip the iteration if the layer is None for some reason
        if layer is None:
            continue

        # Guard against producing a duplicate output. If an "Upscaled {name}"
        # layer already exists (e.g. the user clicked twice, or re-ran on a
        # selection that includes a prior output), skip rather than add a second
        # identical layer.
        _out_name = f"Upscaled {layer.name}"
        if _out_name in viewer.layers:
            napari_show_warning(
                f"'{_out_name}' already exists — skipping to avoid a duplicate.")
            continue

        # Copy the layer data to prevent modifying the original data directly
        image = layer.data.copy()
        # Retrieve the initial dimensions of the image
        num_row_initial, num_col_initial = image.shape

        # Decide not to upscale images already at or above a certain size threshold
        if num_row_initial >= 2048 or num_col_initial >= 2048:
            upscale_factor = 1  # Set the upscale factor to 1, effectively making no change
            update_data = False  # Prevent updates to data_instance for large images
            napari_show_warning("Max resolution is 2048 x 2048. Micron sizes have not been updated.")
            continue
        else:
            # v1.0.0 behaviour: 2× linear upscaling before preprocessing.
            upscale_factor = 2

        # Apply the upscaling function to the image
        upscaled_img = upscale_image_interp(image, num_row_initial, num_col_initial, upscale_factor=upscale_factor)

        # Prepare the upscaled image data for safe use in the viewer
        data = upscaled_img.copy()
        # Ensure integer data types are within a valid range, correcting them if necessary
        if np.issubdtype(data.dtype, np.integer):
            if np.min(data) < 0 or np.max(data) > 2**16 - 1:
                # Give 5% of the range as a buffer which is clipped rather than rescaled 
                if np.min(data) < (0 - (0.05 * 2**16)) or np.max(data) > (2**16 + (0.05 * 2**16)):
                    data = np.clip(data, 0, 2**16 - 1).astype(np.uint16)
                else:
                    data = apply_rescale_intensity(data, out_min=0, out_max=2**16 - 1).astype(np.uint16)
        # Ensure floating-point data types are within a valid range, correcting them if necessary
        elif np.issubdtype(data.dtype, np.floating):
            if np.min(data) < 0 or np.max(data) > 1.0: 
                # Give 5% of the range as a buffer which is clipped rather than rescaled 
                if np.min(data) < (0 - (0.05)) or np.max(data) > (1.0 + (0.05)):
                    data = np.clip(data, 0.0, 1.0).astype(np.float32)
                else:
                    data = apply_rescale_intensity(data, out_min=0.0, out_max=1.0).astype(np.float32)
        else: 
            data = apply_rescale_intensity(data, out_min=0.0, out_max=1.0).astype(np.float32)

        upscaled_img = data.copy()

        # Update relevant data in the data_instance for the first layer processed only if requested
        if update_data:
            # Calculate new dimensions and scale factors based on the upscaling
            scale_factor_width, scale_factor_height = upscaled_img.shape[0] / image.shape[0], upscaled_img.shape[1] / image.shape[1]
            square_scale_factor = scale_factor_width * scale_factor_height
            linear_scale_factor = np.sqrt(square_scale_factor)

            # Update object sizes, ball radius, and cell diameter in the data_instance based on scale factors
            data_instance.data_repository['object_size'] *= linear_scale_factor
            data_instance.data_repository['cell_diameter'] *= linear_scale_factor
            data_instance.data_repository['ball_radius'] *= linear_scale_factor

            # Update the microns per pixel squared resolution in the data_instance
            data_instance.data_repository['microns_per_pixel_sq'] /= square_scale_factor

            # After updating, ensure no further updates are made for subsequent layers
            update_data = False

        # Add the processed and potentially upscaled image back into the viewer
        # Align the upscaled layer physically with its source: it has
        # `upscale_factor`× more pixels over the SAME field of view, so its scale
        # must be the source scale divided by the actual upscale ratio. Without
        # this the upscaled layer (at scale 1) renders far larger than the source
        # (which may carry a µm scale), making the source look "embedded" in it.
        # Compute the physically-aligned scale for the upscaled layer. A failure
        # here must fall back to a plain add — but the add itself is done ONCE,
        # outside the try, so a later notification error cannot cause a second add
        # (the previous structure put the add and the show_info in the same try,
        # so a show_info failure fell into except and added the layer AGAIN,
        # producing two identical upscaled layers).
        try:
            _src_scale = [float(s) for s in layer.scale]
            _ry = upscaled_img.shape[0] / image.shape[0]
            _rx = upscaled_img.shape[1] / image.shape[1]
            _new_scale = list(_src_scale)
            if len(_new_scale) >= 2 and np.isfinite(_ry) and _ry > 0 and np.isfinite(_rx) and _rx > 0:
                _new_scale[-2] = _src_scale[-2] / _ry
                _new_scale[-1] = _src_scale[-1] / _rx
        except Exception:
            _src_scale = None
            _new_scale = None

        # Single add, exactly once per source layer.
        if _new_scale is not None:
            _add_image(upscaled_img, viewer,
                                            name=_out_name, scale=_new_scale)
        else:
            _add_image(upscaled_img, viewer, name=_out_name, operation='upscale')

        # Lineage: the upscaled layer is derived_from + supersedes the source, and
        # inherits its role/modality/channel. This is what makes autopopulation
        # prefer the upscaled version (head of lineage) automatically.
        try:
            from pycat.utils import layer_tags as _LT
            if len(viewer.layers):
                _LT.mark_derived(viewer.layers[-1], layer, via='upscale')
        except Exception:
            pass

        # Notification is best-effort and isolated: any failure here must not
        # affect the layer that was already added.
        try:
            _oh, _ow = image.shape[-2], image.shape[-1]
            _nh, _nw = upscaled_img.shape[-2], upscaled_img.shape[-1]
            _mpp = f"{_src_scale[-1]:.3f} µm/px " if _src_scale else ""
            napari_show_info(
                f"Upscaled \"{layer.name}\": {_ow}×{_oh} → {_nw}×{_nh} px "
                f"({upscale_factor}× linear, same {_mpp}world extent). "
                f"Both layers occupy the same field of view — zoom in on "
                f"\"{_out_name}\" to see the finer pixel grid. "
                f"The scale bar updates when you click a different layer.")
        except Exception:
            pass


# Enhancements and Filters # 


# ---------------------------------------------------------------------------
# Gabor + peak/edge + Laplacian-of-Gaussian + morphological-gaussian + CLAHE filters  ->  moved to filters.py (1.6.251)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.filters import (  # noqa: E402,F401
    gabor_filter_func, peak_and_edge_enhancement_func, run_peak_and_edge_enhancement, apply_laplace_of_gauss_filter, apply_laplace_of_gauss_enhancement, run_apply_laplace_of_gauss_filter, run_morphological_gaussian_filter, run_clahe)



# ---------------------------------------------------------------------------
# Deblurring by pixel reassignment (DPR) + run wrapper  ->  moved to deblur.py (1.6.250)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.deblur import (  # noqa: E402,F401
    deblur_by_pixel_reassignment, run_dpr)



# Background and Noise Correction # 

@tags_layer('inpaint', role='preprocessed',
            summary='Biharmonic inpainting of a masked region')
def background_inpainting_func(image, mask, ball_radius):
    """
    This function uses skimage biharmonic inpainting to 'extend' the masked region of an image to avoid edge effects and 
    artifacts from the rolling ball background subtraction method.
    
    This function first uses dilation and erosion of the mask to define the 'unknown' region for inpainting, then applies 
    a biharmonic inpainting algorithm to fill in the background.

    Parameters
    ----------
    image : numpy.ndarray
        The input image as a NumPy array.
    mask : numpy.ndarray
        A binary mask identifying the region of the image to be inpainted (background).
    ball_radius : int
        Radius used to adjust the size of the mask dilation and erosion, aiding in defining the inpainting region.

    Returns
    -------
    inpainted_img : numpy.ndarray
        The image with the background inpainted, returned as a NumPy array of the same type as the input image.
    """
    # Store the input image data type
    input_dtype = str(image.dtype)  
    # Convert input image to float32 for processing
    img = dtype_conversion_func(image, 'float32')
    
    # Erode the mask to ensure no background is erroneously left behind
    eroded_mask = ndi.binary_erosion(mask, sk.morphology.disk(3))
    
    # Dilate the mask to ensure the inpainting region extends beyond the rolling ball radius 
    dilated_mask  = mask.copy()
    for _ in range(int(4*ball_radius)):
        dilated_mask = ndi.binary_dilation(dilated_mask, sk.morphology.disk(1))
    
    # Identify the region for inpainting
    unknown_region = dilated_mask ^ eroded_mask

    # Perform inpainting on the identified region
    inpainted_img = sk.restoration.inpaint_biharmonic(img, unknown_region)

    # Convert the inpainted image back to the original data type
    inpainted_img = dtype_conversion_func(inpainted_img, input_dtype)

    return inpainted_img


@tags_layer('rolling_ball_bg', role='reference',
            summary='Rolling-ball background ESTIMATE (the background itself)')
def compute_rolling_ball_background(image, ball_radius):
    """
    Compute a background estimate via morphological opening (grey erosion
    followed by grey dilation with a disk of radius ``ball_radius``) -- GPU
    accelerated when a CUDA GPU is available, identical algorithm on CPU.

    IMPORTANT: both hardware paths now run the SAME algorithm. An earlier
    version routed CPU calls through skimage's exact ``rolling_ball`` while
    GPU calls used this morphological-opening approximation. Those are
    genuinely different algorithms (rolling_ball treats intensity as a literal
    extra spatial dimension coupled to the same radius used for the spatial
    footprint, so it is sensitive to the image's numeric range in a way plain
    erosion/dilation is not) and were confirmed to produce different
    segmentation outcomes on identical data depending on which hardware path
    ran. Using one algorithm on both paths removes that inconsistency.

    Returns the BACKGROUND image (not background-subtracted).
    """
    input_dtype = str(image.dtype)
    image_f32   = dtype_conversion_func(image, output_bit_depth='float32')

    bg = None
    try:
        from pycat.toolbox.gpu_utils import GPU_AVAILABLE, gpu_grey_erosion, gpu_grey_dilation
        if GPU_AVAILABLE:
            bg = gpu_grey_erosion(image_f32, radius=ball_radius)
            bg = gpu_grey_dilation(bg.astype(np.float32), radius=ball_radius)
    except Exception:
        bg = None  # Fall through to CPU

    if bg is None:
        # CPU path: identical morphological-opening algorithm, no GPU needed.
        selem = sk.morphology.disk(ball_radius)
        bg = ndi.grey_erosion(image_f32, footprint=selem)
        bg = ndi.grey_dilation(bg, footprint=selem)

    # NOTE: no smoothing here. rb_gaussian_background_removal (the caller)
    # already applies ndi.gaussian_filter(bg, sigma=ball_radius//2) right
    # after calling this function. Smoothing here too would blur the
    # background estimate twice with the same sigma before the caller's own
    # two-stage subtraction ever runs, spreading the estimate into real
    # signal and over-subtracting it.
    bg = ndi.grey_dilation(bg, footprint=sk.morphology.disk(1))
    bg = ndi.grey_erosion(bg, footprint=sk.morphology.disk(1))
    return dtype_conversion_func(bg.astype(np.float32), input_dtype)

@tags_layer('bg_subtract', role='preprocessed', inputs=('image',),
            summary='Background subtraction')
def subtract_background(image, background, bg_scaling_factor=0.75, equalize_intensity=False, window_size=None):
    """
    Subtracts the background from an image, optionally scaling the background intensity and applying local contrast enhancement.

    Parameters
    ----------
    image : numpy.ndarray
        The input image from which to subtract the background.
    background : numpy.ndarray
        The computed background to be subtracted.
    bg_scaling_factor : float, optional
        A scaling factor for the background intensity before subtraction, defaulting to 0.75.
    equalize_intensity : bool, optional
        Whether to apply adaptive histogram equalization to the result, defaulting to False.
    window_size : int, optional
        The window size for adaptive histogram equalization, required if equalize_intensity is True.

    Returns
    -------
    ouput_image : numpy.ndarray
        The image with the background subtracted and optional contrast enhancement, matching the original data type.
    """
    # Store the input image data type
    input_dtype = str(image.dtype)  
    # Convert input image to float32 for processing
    img = dtype_conversion_func(image, 'float32')

    # Subtract the scaled background from the image
    bg_subtracted_img = img - (bg_scaling_factor * background)
    bg_subtracted_img[bg_subtracted_img < 0] = 0  # Set negative values to zero

    # Use morphological smoothing on the bg subtracted image
    bg_subtracted_img = ndi.grey_erosion(bg_subtracted_img, footprint=sk.morphology.disk(1))
    bg_subtracted_img = ndi.grey_dilation(bg_subtracted_img, footprint=sk.morphology.disk(1))

    # Optionally apply adaptive histogram equalization
    if equalize_intensity:
        if window_size:
            k_size = math.ceil(window_size)
        else:
            k_size = None # Uses the skimage default window size of image.shape // 8
        bg_subtracted_img = _safe_equalize_adapthist(bg_subtracted_img, kernel_size=k_size, clip_limit=0.0025)

    # Convert back to the original data type
    ouput_image = dtype_conversion_func(bg_subtracted_img, input_dtype)

    return ouput_image


@tags_layer('rolling_ball', role='preprocessed', inputs=('image',),
            summary='Rolling-ball + Gaussian background removal')
def rb_gaussian_background_removal(image, ball_radius, equalize_intensity=False, roi_mask=None):
    """
    Removes background from an image using rolling ball and Gaussian blur techniques, aiming to enhance 
    contrast and detail by minimizing background noise. The rolling ball algorithm is used first to model 
    and subtract the background. Then, Gaussian blur is applied to smooth all image details into the background,
    which is then subtracted from the original image to remove the remaining background.

    Parameters
    ----------
    image : numpy.ndarray
        The input image array from which to remove background noise.
    ball_radius : int
        Determines the radius for the rolling ball filter and influences the smoothing level of the Gaussian blur.
    equalize_intensity : bool, optional
        Enables intensity histogram equalization for the output image for improved visualization. Defaults to False.
    roi_mask : numpy.ndarray, optional
        A binary mask indicating the region of interest within the image. Background removal is confined to this region.

    Returns
    -------
    bg_removed_image : numpy.ndarray
        The image with the background removed, maintaining the same dimensions and data type as the input.

    Note
    ----
    The function handles data type conversions internally for processing and reverts to the original data type
    for compatibility with downstream imaging tasks.
    """

    # Get the input dtype
    input_dtype = str(image.dtype)
    # Convert the input image to float32 for processing
    img = dtype_conversion_func(image, 'float32')
    # Normalise to [0, 1] by the actual image maximum BEFORE any processing.
    # img_as_float32 divides by 65535, so a dim uint16 image arrives at ~0.046
    # maximum instead of 1.0. The rolling-ball and Gaussian subtraction steps
    # are tuned for [0, 1] input; at 0.046 scale they over-suppress the signal.
    _rb_img_max = float(img.max())
    if _rb_img_max > 0:
        img = img / _rb_img_max

    # Apply the ROI mask if provided
    if roi_mask is not None:
        roi_mask = roi_mask.astype(bool) # Ensure the ROI mask is boolean
        img *= roi_mask # Apply the mask to the image
        # Inpaint the background aroud the mask to avoid edge artifcats from the rolling ball algorithm
        bg_img = background_inpainting_func(img, roi_mask, ball_radius)
    else:
        # If no mask is provided, use the entire image
        bg_img = img.copy()

    # Compute the background using the rolling ball algorithm
    rb_background = compute_rolling_ball_background(bg_img, ball_radius)
    # Apply a gaussian filter to smooth the edges of the translated ball
    rb_background = ndi.gaussian_filter(rb_background, sigma=ball_radius//2)

    # Subtract the rolling ball background from the original image
    rb_bg_subtracted_img = subtract_background(img, rb_background, bg_scaling_factor=0.75, equalize_intensity=False)
    
    # Apply a large gaussian filter to smooth all objects in the image into the background 
    gaussian_bg = ndi.gaussian_filter(rb_bg_subtracted_img, sigma=(ball_radius*2))
    # Subtract the Gaussian background for final background removal
    gaussian_bg_subtracted_img = subtract_background(rb_bg_subtracted_img, gaussian_bg, bg_scaling_factor=0.75, equalize_intensity=equalize_intensity, window_size=ball_radius*4)

    # Convert the final image back to the original data type
    bg_removed_image = dtype_conversion_func(gaussian_bg_subtracted_img, output_bit_depth=input_dtype)

    return bg_removed_image

def run_rb_gaussian_background_removal(eq_int_input, data_instance, viewer):
    """
    Executes the rb_gaussian_background_removal function on an active image layer within a napari viewer,
    enhancing the image by removing background noise using a combined rolling ball and Gaussian blur approach.

    Parameters
    ----------
    eq_int_input : QtWidgets.QCheckBox
        Input checkbox to specify whether intensity equalization should be applied to the processed image.
    data_instance : DataInstance
        An object encapsulating relevant data and parameters, including the ball radius for background removal.
    viewer : napari.Viewer
        The image viewer in which the processed image will be displayed.

    Raises
    ------
    Error
        If no active image layer is selected or if the layer is not compatible for processing.

    Note
    ----
    This function fetches parameters from the data_instance, applies background removal to the selected image, 
    and updates the viewer by adding the processed image as a new layer. The name of the new layer reflects the
    background removal process.
    """

    active_layer = viewer.layers.selection.active # Retrieve the active layer from the viewer
    ball_radius = math.ceil(data_instance.data_repository['ball_radius']) # Get the ball radius from the data instance
    equalize_intensity_input = eq_int_input.isChecked() # Check if equalize intensity is enabled

    # Check if there is an active layer, and that it is a Napari image layer
    if active_layer is not None and isinstance(active_layer, _napari().layers.Image):
        image = active_layer.data
    else:
        napari_show_warning("No active image layer selected.")

    # Perform the background removal on the image
    bg_removed_image = rb_gaussian_background_removal(image, ball_radius, equalize_intensity=equalize_intensity_input)

    # Add the processed image as a new layer in the viewer
    _add_image(bg_removed_image, viewer, name=f'RB-Gaussian Background Removed {active_layer.name}', operation='background_subtract')
    try:
        from pycat.utils import layer_tags as _LT
        if len(viewer.layers):
            _LT.mark_derived(viewer.layers[-1], active_layer, via='background_subtract')
    except Exception:
        pass


def rb_gaussian_bg_removal_with_edge_enhancement(image, ball_radius, roi_mask=None):
    """
    Applies background removal and edge enhancement to an image using a combination of processing techniques.
    The method involves rolling ball and Gaussian background subtraction followed by edge enhancement 
    through Gabor filtering and adaptive histogram equalization to improve feature visibility, particularly
    useful in microscopic image analysis.

    Parameters
    ----------
    image : numpy.ndarray
        The input image to be processed.
    ball_radius : int
        The radius of the rolling ball filter, used in the initial background removal step.
    roi_mask : numpy.ndarray, optional
        A binary mask defining the region of interest (ROI); processing is confined to this region if provided.

    Returns
    -------
    output_image : numpy.ndarray
        The enhanced image after background removal and edge enhancement, returned in the original data type.

    Note
    ----
    The sequence of image processing steps integrates background subtraction with texture and edge enhancement 
    to enhance microscopic images or similar detailed visual data.
    """
    
    input_dtype = str(image.dtype) # Store the input image's data type for later conversion back
    img = dtype_conversion_func(image, 'float32') # Convert the image data type to float32 for processing

    # Remove the background using rolling ball and Gaussian-based method
    bg_removed_image = rb_gaussian_background_removal(img, ball_radius, equalize_intensity=True, roi_mask=roi_mask)

    output_image = peak_and_edge_enhancement_func(bg_removed_image, ball_radius)

    # Convert the processed image back to its original data type
    output_image = dtype_conversion_func(output_image, output_bit_depth=input_dtype)

    return output_image

# Default foreground-suppression parameters. Tuned interactively on real GFP
# condensate data against hand-annotated ground truth (objects strongly visible
# in raw = keep; acceptable = keep, lightly attenuated; noise fluctuations =
# eliminate). These are the values applied by pre_process_image unless the
# preprocessing widget's "Adjust foreground suppression" checkbox overrides them.
FOREGROUND_SUPPRESSION_DEFAULTS = {
    'strength': 0.8,   # blend factor: 1.0 = full attenuation of low-realness px
    'log_p':    10.0,  # blob-shape (LoG) gate percentile
    'con_p':     4.0,  # local-contrast gate percentile
    'min_area':  3,    # objects smaller than this (px) knocked down as specks
    'border_grow': 2,  # dilate keep-region by this many px to protect borders
    'large_object_min_area': 700,  # contiguous bright blobs >= this area (px)
                                    # are treated as real regardless of local
                                    # peakiness -- see _realness_weight notes.
}


def _realness_weight(pp, ball_radius, log_p=10.0, con_p=4.0, min_area=3,
                     border_grow=2, large_object_min_area=700):
    """
    Composite per-pixel 'realness' weight in [0, 1] used by
    ``soft_foreground_suppression`` to decide what to keep vs. attenuate.

    Combines four cues so that real condensates (bright, spatially coherent,
    rising above their local surround, and large enough) score ~1, while noise
    fluctuations — which fail at least one cue — score ~0:

    - blob-shape    : normalised separable-LoG response at the feature scale
                      (σ = ball_radius × 0.27). Real puncta produce a strong,
                      coherent LoG peak; single-pixel noise does not.
    - local-contrast: value above a larger-σ surround estimate. Real puncta rise
                      clearly above their neighbourhood; diffuse noise does not.
    - intensity     : soft floor protecting genuinely bright pixels.
    - size          : small high-weight regions are knocked down.

    A final border-protection step grows the high-confidence keep region outward
    by ``border_grow`` pixels and lifts the weight back toward 1 there, so the
    dim falloff at object *borders* is not clipped (which would erode segmented
    condensates). Isolated noise, having no high-confidence core to grow around,
    is unaffected.

    Parameters
    ----------
    pp : numpy.ndarray
        Normalised (roughly [0, 1]) preprocessed image, float32.
    ball_radius : int
        Feature scale; sets the LoG / surround sigmas.
    log_p, con_p : float
        Lower percentile anchors for the blob-shape and local-contrast smoothstep
        gates. Higher = stricter (more aggressive noise removal).
    min_area : int
        Minimum object size in pixels; smaller high-weight regions are attenuated.
    border_grow : int
        Radius (px) by which the high-confidence keep region is dilated to protect
        object borders. 0 disables border protection (weights unchanged). Larger
        values recover thicker borders but also spare more surrounding pixels.
    large_object_min_area : int
        Contiguous bright regions at or above this area (px) are treated as
        real (weight forced to 1) regardless of the blob-shape/local-contrast
        gates above. Those gates are tuned to the puncta scale
        (sigma = ball_radius x 0.27) and, by construction, score near-zero
        across the flat interior of any condensate whose diameter
        substantially exceeds that scale -- large, coarsened/fused
        condensates were being progressively dimmed (and eventually dropped
        entirely by downstream thresholding) as a result. Size + brightness +
        contiguity alone is strong evidence of realness here: noise
        fluctuations are never simultaneously large, bright, AND contiguous,
        so this does not reopen the noise-suppression problem this function
        was built to solve. Deliberately NOT scaled with ball_radius (see the
        rim_close_radius lesson in segmentation_tools.py) -- tune this
        directly against your own large-condensate size range if needed.

    Returns
    -------
    numpy.ndarray
        float32 weight array in [0, 1], same shape as ``pp``.
    """
    def _norm01(a):
        a = a.astype(np.float32)
        mn, mx = float(a.min()), float(a.max())
        return (a - mn) / (mx - mn) if mx > mn else a * 0.0

    def _soft(x, plo, phi):
        z = x[x > 1e-4]
        if z.size < 10:
            return np.ones_like(x)
        lo = float(np.percentile(z, plo))
        hi = float(np.percentile(z, phi))
        if hi <= lo:
            hi = lo + 1e-6
        t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
        return t * t * (3.0 - 2.0 * t)

    sigma = max(1.0, ball_radius * 0.27)

    # blob-shape via separable LoG
    gg = ndi.gaussian_filter(pp, sigma)
    bl = np.zeros_like(gg)
    for ax in range(pp.ndim):
        bl += ndi.uniform_filter1d(gg, size=3, axis=ax, mode='reflect') * 2 - 2 * gg
    blob = _norm01(np.clip(-bl, 0, None))

    # local contrast vs a larger-σ surround
    surround = ndi.gaussian_filter(pp, sigma * 3.0)
    contrast = _norm01(np.clip(pp - surround, 0, None))

    # intensity floor
    inten = _norm01(pp)

    w = _soft(blob, log_p, 95.0) * _soft(contrast, con_p, 95.0) * _soft(inten, 30.0, 90.0)

    # size gate: suppress tiny high-weight specks
    mask = w > 0.2
    lbl, n = ndi.label(mask)
    if n > 0:
        sizes = ndi.sum(np.ones_like(lbl), lbl, range(1, n + 1))
        small_labels = np.where(sizes < min_area)[0] + 1
        if small_labels.size:
            small = np.isin(lbl, small_labels)
            w = np.where(small, w * 0.15, w)

    # Border protection: grow the high-confidence keep region (surviving cores,
    # after the size gate) outward and restore full weight within the grown band.
    # This stops the smoothstep falloff from eroding object borders during
    # segmentation, without sparing isolated noise (which has no core to grow).
    if border_grow and border_grow > 0:
        core = w > 0.5
        # remove specks that the size gate just demoted so they don't seed growth
        core &= (w > 0.2)
        if core.any():
            grown = ndi.binary_dilation(
                core, structure=ndi.generate_binary_structure(pp.ndim, 1),
                iterations=int(border_grow))
            # only lift the border band, and only where there is genuine signal
            # (avoid promoting pure-zero background pixels into the object).
            band = grown & ~core & (pp > 1e-4)
            w = np.where(band, np.maximum(w, 0.9), w)

    # Large-object rescue: independent of local peakiness, any sufficiently
    # large, contiguous, clearly-bright-above-background region is treated
    # as real. Uses a coarse global threshold (Otsu, falling back to a
    # percentile if Otsu can't be computed) purely to find "clearly bright"
    # pixels -- this is intentionally much coarser than the blob/contrast
    # gates and is only used to gate by CONNECTED-COMPONENT SIZE, not to
    # replace the fine-grained weight elsewhere.
    if large_object_min_area and large_object_min_area > 0:
        bright_px = pp[pp > 1e-4]
        if bright_px.size >= 10:
            try:
                coarse_thresh = sk.filters.threshold_otsu(bright_px)
            except Exception:
                coarse_thresh = float(np.percentile(bright_px, 70))
            coarse_bright = pp > coarse_thresh
            lbl2, n2 = ndi.label(coarse_bright)
            if n2 > 0:
                sizes2 = ndi.sum(np.ones_like(lbl2), lbl2, range(1, n2 + 1))
                big_labels = np.where(sizes2 >= large_object_min_area)[0] + 1
                if big_labels.size:
                    big = np.isin(lbl2, big_labels)
                    w = np.where(big, 1.0, w)
    return w.astype(np.float32)


@tags_layer('fg_suppress', role='preprocessed',
            summary='Soft attenuation of bright foreground')
def soft_foreground_suppression(image, ball_radius, strength=None,
                                log_p=None, con_p=None, min_area=None,
                                border_grow=None, large_object_min_area=None):
    """
    Refine a preprocessed condensate image by attenuating noise-like foreground
    (diffuse texture and single-pixel fluctuations) while preserving the
    nucleoplasm baseline and leaving real condensate puncta intact.

    This is a NON-destructive alternative to full rolling-ball / Gaussian
    background subtraction. A full background subtraction on a preprocessed
    condensate image (``/max -> separable LoG -> WBNS -> morph -> Gaussian ->
    CLAHE``) collapses the IQR noise floor to zero: it removes the nucleoplasm
    baseline that condensates sit on top of, which destroys downstream SNR and
    segmentation. Instead of subtracting an estimated background, this function
    computes a composite per-pixel 'realness' weight (see ``_realness_weight``)
    that is ~1 at real puncta and ~0 at noise fluctuations, then blends it in by
    ``strength`` so the baseline is preserved rather than zeroed.

    Parameters
    ----------
    image : numpy.ndarray
        The input image, expected to be a preprocessed condensate image. Any
        dtype; the result is returned in the input dtype.
    ball_radius : int
        Feature scale, used to set the LoG / surround sigmas in the realness
        weight so it varies over structure-sized regions rather than per-pixel.
    strength : float, optional
        Blend factor in [0, 1]. 0.0 is a no-op; 1.0 applies the full realness
        weight. Defaults to ``FOREGROUND_SUPPRESSION_DEFAULTS['strength']``.
    log_p, con_p : float, optional
        Blob-shape and local-contrast gate percentiles. Higher = stricter noise
        removal. Default to the tuned values in ``FOREGROUND_SUPPRESSION_DEFAULTS``.
    min_area : int, optional
        Minimum object size (px); smaller specks are knocked down. Defaults to
        ``FOREGROUND_SUPPRESSION_DEFAULTS['min_area']``.
    border_grow : int, optional
        Radius (px) by which the keep-region is dilated to protect object borders
        from erosion during segmentation. 0 disables. Defaults to
        ``FOREGROUND_SUPPRESSION_DEFAULTS['border_grow']``.
    large_object_min_area : int, optional
        Area (px) above which a contiguous bright region is treated as real
        regardless of local peakiness, rescuing large condensates that the
        puncta-scale gates would otherwise dim or erase. Defaults to
        ``FOREGROUND_SUPPRESSION_DEFAULTS['large_object_min_area']``.

    Returns
    -------
    output_image : numpy.ndarray
        The refined image in the input dtype. The nucleoplasm baseline (non-zero
        IQR of in-tissue pixels) is preserved; noise fluctuations are suppressed;
        real puncta are retained.
    """
    # Resolve defaults (None -> tuned default) so callers can override any subset.
    if strength is None:
        strength = FOREGROUND_SUPPRESSION_DEFAULTS['strength']
    if log_p is None:
        log_p = FOREGROUND_SUPPRESSION_DEFAULTS['log_p']
    if con_p is None:
        con_p = FOREGROUND_SUPPRESSION_DEFAULTS['con_p']
    if min_area is None:
        min_area = FOREGROUND_SUPPRESSION_DEFAULTS['min_area']
    if border_grow is None:
        border_grow = FOREGROUND_SUPPRESSION_DEFAULTS['border_grow']
    if large_object_min_area is None:
        large_object_min_area = FOREGROUND_SUPPRESSION_DEFAULTS['large_object_min_area']

    input_dtype = str(image.dtype)
    img = dtype_conversion_func(image, output_bit_depth='float32')

    _max = float(img.max())
    if _max <= 0:
        # Empty / all-zero image: nothing to do.
        return dtype_conversion_func(img, output_bit_depth=input_dtype)
    norm = img / _max

    # Composite realness weight, then blend by strength so the baseline survives:
    #   strength=0 -> weight_eff=1 everywhere (no change)
    #   strength=1 -> weight_eff=weight (full attenuation of low-realness px)
    weight = _realness_weight(norm, ball_radius, log_p=log_p,
                              con_p=con_p, min_area=min_area,
                              border_grow=border_grow,
                              large_object_min_area=large_object_min_area)
    weight_eff = (1.0 - strength) + strength * weight
    refined = norm * weight_eff

    # Restore original intensity scale before dtype conversion.
    refined = (refined * _max).astype(np.float32)

    output_image = dtype_conversion_func(refined, output_bit_depth=input_dtype)
    return output_image


def run_enhanced_rb_gaussian_bg_removal(data_instance, viewer):
    """
    Refine the active image layer for condensate detection and display the result
    as a new layer in the napari viewer.

    Historically this ran a full rolling-ball + Gaussian background subtraction with
    edge enhancement. On a preprocessed condensate image that step is destructive:
    it subtracts the nucleoplasm baseline and collapses the IQR noise floor to zero,
    leaving only the brightest peaks and erasing the diffuse signal that dim
    candidate condensates sit in. This runner now detects whether the active layer
    is already preprocessed and, in that case, applies a soft foreground-suppression
    refinement instead — dim candidates are attenuated (dimmed but still visible)
    while the baseline is preserved and bright peaks are left intact. A genuinely raw
    image (not yet preprocessed) still receives the original enhancement path.

    Parameters
    ----------
    viewer : napari.Viewer
        The napari viewer object to which the processed image will be added.
    data_instance : DataInstance
        Encapsulates relevant data and parameters for the session, including the necessary ball radius for processing.

    Raises
    ------
    Error
        If no active image layer is selected, preventing process execution.

    Note
    ----
    The output layer keeps the ``Enhanced Background Removed [name]`` naming so
    downstream widgets and batch steps that reference it continue to work.
    """

    # Retrieve the active layer and the ball radius from the data instance
    active_layer = viewer.layers.selection.active
    ball_radius = math.ceil(data_instance.data_repository['ball_radius'])

    # Validate the active layer
    if active_layer is None or not isinstance(active_layer, _napari().layers.Image):
        raise ValueError("No active image layer selected or the selected layer is not an image layer.")

    # Process the active image layer
    image = active_layer.data

    # Detect whether the input is already preprocessed. A preprocessed condensate
    # image (/max -> LoG -> WBNS -> morph -> Gauss -> CLAHE) has a sparse, peaked
    # intensity distribution: the median of its non-zero pixels after /max
    # normalisation is small (< 0.05). This mirrors the bypass heuristic in
    # segment_subcellular_objects so both paths agree on what "already enhanced"
    # means.
    _norm = dtype_conversion_func(image, output_bit_depth='float32')
    _pmax = float(_norm.max())
    if _pmax > 0:
        _norm = _norm / _pmax
    _nz = _norm[_norm > 0.001]
    _already_enhanced = (_nz.size > 10 and float(np.median(_nz)) < 0.05)

    if _already_enhanced:
        # Non-destructive refinement: attenuate noise-like foreground, keep the
        # nucleoplasm baseline and real puncta. This replaces the old subtractive
        # chain that collapsed the noise floor to zero. Uses the same session
        # suppression params as pre_process_image so behaviour is consistent.
        # (As of 1.5.128 pre_process_image already applies suppression, so on a
        # freshly-preprocessed layer this button is largely redundant; running it
        # with the same params is near-idempotent rather than double-destructive.)
        sp = data_instance.data_repository.get('foreground_suppression_params', None) or {}
        enhanced_image = soft_foreground_suppression(
            image, ball_radius,
            strength=sp.get('strength'), log_p=sp.get('log_p'),
            con_p=sp.get('con_p'), min_area=sp.get('min_area'),
            border_grow=sp.get('border_grow'))
    else:
        # Genuinely raw input: retain the original enhancement behaviour.
        enhanced_image = rb_gaussian_bg_removal_with_edge_enhancement(image, ball_radius)

    # Add the processed image as a new layer with an indicative name
    _add_image(enhanced_image, viewer, name=f'Enhanced Background Removed {active_layer.name}')
    try:
        from pycat.utils import layer_tags as _LT
        if len(viewer.layers):
            _LT.mark_derived(viewer.layers[-1], active_layer, via='background_subtract')
    except Exception:
        pass


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

    # Suppress VisibleDeprecationWarning from NumPy, potentially triggered by pywavelets.
    # This is a temporary measure due to an unresolved issue in the pywavelets package
    # that does not affect the functionality of our code but produces unwanted warning messages.
    warnings.filterwarnings("ignore", message=".*Creating an ndarray from ragged nested sequences.*") # pywavelets must have some stupid bug that I cant figure out so Im just going to suppress the annoying warning here
    
    # Background and noise extraction
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
    WBNS_img, _ = wbns_func(image, psf_fwhm, noise_lvl)

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
    _, wavelet_noise_corrected = wbns_func(image, psf_fwhm, noise_lvl)

    # Display the processed image
    _add_image(wavelet_noise_corrected, viewer, name=f"Wavelet Noise Corrected {active_layer.name}")

# ---------------------------------------------------------------------------
# Edge-preserving bilateral filter  ->  moved to filters.py (1.6.251)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.filters import (  # noqa: E402,F401
    apply_bilateral_filter, run_apply_bilateral_filter)



@tags_layer('preprocess', role='preprocessed',
            summary='The standard preprocessing cascade')
def pre_process_image(image, ball_radius, window_size,
                      suppress_foreground=True, suppression_params=None,
                      norm_max=None):
    """
    Enhances features in an image through a comprehensive pre-processing pipeline that includes noise reduction,
    feature enhancement, and contrast improvement. This function is tailored for images where maintaining 
    feature integrity and detail is crucial, such as in microscopic imaging.

    Parameters
    ----------
    image : numpy.ndarray
        The input image array to be processed.
    ball_radius : int
        The radius used for the disk element in the White Top Hat filter and other morphological operations.
    window_size : int
        The window size used for CLAHE, influencing how contrast is adapted locally in the image.
    suppress_foreground : bool, optional
        If True (default), a final foreground-suppression step attenuates noise-like
        pixels (diffuse texture and single-pixel fluctuations) while preserving the
        nucleoplasm baseline and real puncta. This restores usable preprocessing
        output for condensate detection; without it, the raw CLAHE output leaves
        the diffuse noise tier at full strength. Set False to get the pre-1.5.128
        output (CLAHE result with no suppression).
    suppression_params : dict, optional
        Overrides for the suppression parameters (`strength`, `log_p`, `con_p`,
        `min_area`). Any key omitted falls back to
        ``FOREGROUND_SUPPRESSION_DEFAULTS``. Ignored if ``suppress_foreground`` is
        False.

    Returns
    -------
    output_image : numpy.ndarray
        The pre-processed image, converted back to its original data type, with enhanced features and reduced noise.

    Notes
    -----
    The pre-processing pipeline includes the following steps:
    - Converting image data type to float32 for processing.
    - Applying a White Top Hat filter to highlight bright elements smaller than the footprint.
    - Enhancing the image using a Laplacian of Gaussian filter.
    - Removing background and noise using a custom wavelet based noise and backkgroud removal function.
    - Performing erosion and dilation for noise reduction.
    - Applying Gaussian filter for smoothing.
    - Enhancing contrast using CLAHE (Contrast Limited Adaptive Histogram Equalization).
    """

    # ── CPU path ─────────────────────────────────────────────────────────
    input_dtype = str(image.dtype)  # Store original image data type for conversion back after processing
    img = dtype_conversion_func(image, output_bit_depth='float32') # Convert image data type to float32 for processing
    # Normalise to [0, 1] by the actual image maximum BEFORE any processing.
    # sk.util.img_as_float32 divides by 65535, so a uint16 image that only
    # uses values up to ~3000 (a typical dim condensate image) arrives as
    # float32 with a maximum of ~0.046. Every subsequent multiplicative step
    # (white-top-hat rescale, DoG, WBNS wavelet thresholding) is tuned for
    # [0, 1] but receives [0, 0.046], causing near-total signal suppression.
    _pp_max = float(img.max())
    # For a time-series, every frame must be normalised by the SAME scale or a
    # brightening focus makes later frames appear dimmer (the per-frame max, the
    # denominator, rises with the signal). Callers processing a stack pass a
    # fixed norm_max (the stack's global max); 2D callers leave it None and get
    # the original per-frame behaviour unchanged.
    if norm_max is not None and float(norm_max) > 0:
        _pp_max = float(norm_max)
    if _pp_max > 0:
        img = img / _pp_max

    # Blob enhancement via separable LoG (Laplacian of Gaussian) with sigma
    # scaled to ball_radius.
    #
    # Quantitative SNR analysis on real condensate data (GFP channel):
    #   raw /max:              within-nucleus SNR =     8
    #   LoG(σ=ball_radius×0.27): within-nucleus SNR = 2917  (×360 gain)
    #
    # Speed optimisations validated against the float64 gaussian_laplace
    # reference on 2048×2048 images at ball_radius=15 and ball_radius=50:
    #
    #   gaussian_laplace f64 (old)   1.00×  corr=1.000  SNR=430  ← reference
    #   gaussian_laplace f32         1.15×  corr=1.000  SNR=430  ← safe
    #   separable LoG f32 (this)     1.54×  corr=0.9999 SNR=429  ← adopted
    #   DoG fixed σ=2.0,3.2 (old)   1.37×  corr=0.904  SNR=224  ← DO NOT USE
    #   DoG scaled (0.15,0.25)       1.43×  corr=0.948  SNR=268  ← DO NOT USE
    #
    # Separable LoG: Gaussian(σ) then discrete Laplacian in each axis.
    # 1.54× faster than gaussian_laplace, corr=0.9999 on real data, SNR
    # within 0.1% of reference at both ball_radius=15 and ball_radius=50.
    # All arithmetic in float32 — precision is sufficient for this application.
    #
    # Rule: sigma = ball_radius × 0.27 (matches v1.0.0 LoG(σ=3) at br≈11px).
    _log_sigma = max(1.0, ball_radius * 0.27)
    _g = ndi.gaussian_filter(img.astype(np.float32), sigma=_log_sigma)
    _lap = np.zeros_like(_g)
    for _ax in range(img.ndim):
        _lap += ndi.uniform_filter1d(_g, size=3, axis=_ax, mode='reflect') * 2 - 2 * _g
    inverted_LoG_img = np.clip(-_lap, 0, None).astype(np.float32)
    if inverted_LoG_img.max() > 0:
        inverted_LoG_img /= inverted_LoG_img.max()
    LoG_enhanced_img = inverted_LoG_img  # direct LoG, no multiplicative suppression

    # Parameters for background and noise removal
    psf_res = 4  # Point Spread Function resolution
    noise_lvl = 1  # Noise level
    # Remove background and noise using WBNS function
    WBNS_img, _ = wbns_func(LoG_enhanced_img, psf_res, noise_lvl)

    # Noise reduction through morphological operations
    img = WBNS_img.copy()
    selem = sk.morphology.disk(1)  # Structuring element for erosion and dilation
    img = ndi.grey_erosion(img, footprint=selem)
    img = ndi.grey_dilation(img, footprint=selem)

    # Apply Gaussian filter for image smoothing
    img = ndi.gaussian_filter(img, 1)

    # Apply CLAHE for contrast enhancement. The tile is scaled to the user's
    # window_size (v1.0.0 behavior) rather than a fixed 64-px tile: a fixed
    # tile smaller than the chosen window is more aggressive (more local
    # equalization) and ignores the window_size control, over-enhancing
    # background texture and suppressing low-contrast puncta.
    clip_limit = 0.0025
    k_size = math.ceil(window_size)
    img = _safe_equalize_adapthist(img, kernel_size=k_size,
                                          clip_limit=clip_limit)

    # Foreground suppression (1.5.128): attenuate noise-like foreground while
    # preserving the nucleoplasm baseline and real puncta. Applied here in the
    # core so every consumer (button, batch replay, subcellular segmentation)
    # receives the corrected output. Operates in the current float32 [0,1]-ish
    # space; the function normalises internally, so scale is preserved.
    if suppress_foreground:
        sp = suppression_params or {}
        img = soft_foreground_suppression(
            img, ball_radius,
            strength=sp.get('strength'),
            log_p=sp.get('log_p'),
            con_p=sp.get('con_p'),
            min_area=sp.get('min_area'),
            border_grow=sp.get('border_grow'),
        )

    # Convert the processed image back to its original data type
    output_image = dtype_conversion_func(img, output_bit_depth=input_dtype)

    return output_image


def run_pre_process_image(data_instance, viewer):
    """
    Run the pre-processing function on an image selected in a viewer interface. This function handles the selection 
    of an active image layer, retrieves necessary parameters from a data instance, applies the pre-processing, and 
    then adds the processed image back to the viewer.

    Parameters
    ----------
    viewer : napari.Viewer
        The Napari viewer instance where the image layers are managed.
    data_instance : object
        An object containing the data repository with parameters such as ball radius and window size for the pre-processing.

    Raises
    ------
    Error
        If no active image layer is selected.

    Notes
    -----
    Retrieves necessary parameters from the data_instance, applies a comprehensive pre-processing pipeline to the selected
    image, and displays the enhanced image as a new layer in the viewer. This allows users to immediately observe and analyze the
    effects of the pre-processing on the original image.
    """

    # Check for an active image layer in the viewer
    active_layer = viewer.layers.selection.active
    if active_layer is None or not isinstance(active_layer, _napari().layers.Image):
        raise ValueError("No active image layer selected")
    
    # Retrieve the image and parameters for pre-processing from the data instance
    image = active_layer.data
    ball_radius = int(data_instance.data_repository['ball_radius'])
    window_size = int(data_instance.data_repository['cell_diameter']) // 2

    # Cap ball_radius relative to image size to prevent MemoryError on large/upscaled images.
    # disk(r) creates a (2r+1)^2 footprint; scipy needs ~8x that in RAM for white_tophat.
    # Limit to 5% of the smallest image dimension as a safe upper bound.
    max_radius = max(4, int(min(image.shape[-2:]) * 0.05))
    if ball_radius > max_radius:
        print(f"[PyCAT] ball_radius {ball_radius} capped to {max_radius} for image shape {image.shape}")
        ball_radius = max_radius
    window_size = min(window_size, max_radius * 2)

    # Foreground-suppression settings. Defaults are always applied; the
    # preprocessing widget's "Adjust foreground suppression" checkbox may store
    # overrides in the data repository under 'foreground_suppression_params'.
    # A stored value of None/absent -> use FOREGROUND_SUPPRESSION_DEFAULTS.
    suppression_params = data_instance.data_repository.get(
        'foreground_suppression_params', None)
    suppress_foreground = data_instance.data_repository.get(
        'suppress_foreground', True)

    # Apply pre-processing to the selected image
    pre_processed_image = pre_process_image(
        image, ball_radius, window_size,
        suppress_foreground=suppress_foreground,
        suppression_params=suppression_params)

    # Add the pre-processed image to the viewer with a default colormap
    _add_image(pre_processed_image, viewer, name=f"Pre-Processed {active_layer.name}",
               operation='preprocess')


# ---------------------------------------------------------------------------
# Calibration-frame background correction
# ---------------------------------------------------------------------------
# Empirical correction using a separately-acquired reference (free dye / flat
# field, or a clear no-condensate frame). The reference is specific to a
# microscope + settings + sample combination, so it is loaded once and applied
# to matching data rather than derived per-dataset.

@tags_layer('flatfield', role='preprocessed',
            summary='Flat-field (illumination) correction')
def apply_flatfield_correction(image, flat, dark=None):
    """
    Flat-field (illumination) correction for a free-dye / flat reference.

    Removes MULTIPLICATIVE non-uniformity (vignetting, uneven excitation):

        corrected = (image - dark) / (flat - dark) * mean(flat - dark)

    The ``* mean(...)`` term restores the original intensity level so the result
    stays in a comparable range. ``dark`` (a camera dark/offset frame) is
    optional. Works on a single 2D image or a (T/Z, H, W) stack — the 2D
    reference broadcasts across frames.
    """
    img = np.asarray(image, dtype=np.float32)
    flt = np.asarray(flat, dtype=np.float32)
    if dark is not None:
        drk = np.asarray(dark, dtype=np.float32)
        num = img - drk
        den = flt - drk
    else:
        num = img
        den = flt
    den_mean = float(np.mean(den))
    if den_mean == 0:
        den_mean = 1.0
    # Guard against divide-by-zero in dark pixels of the reference.
    den_safe = np.where(np.abs(den) < 1e-6, den_mean, den)
    corrected = num / den_safe * den_mean
    return corrected.astype(np.float32)


@tags_layer('bg_subtract_clear', role='preprocessed',
            summary='Additive background subtraction from a clear frame')
def apply_background_subtraction(image, background):
    """
    Additive background subtraction for a clear-frame (no-condensate) reference.

        corrected = clip(image - background, 0, None)

    Use when the background is additive (stray light, autofluorescence floor,
    fixed-pattern offset). The 2D reference broadcasts across a (T/Z, H, W) stack.
    """
    img = np.asarray(image, dtype=np.float32)
    bg = np.asarray(background, dtype=np.float32)
    corrected = np.clip(img - bg, 0, None)
    return corrected.astype(np.float32)


# ---------------------------------------------------------------------------
# Automatic object-size estimation (top-hat/Otsu + brightfield variant) + validity gate  ->  moved to size_estimation.py (1.6.248)
# ---------------------------------------------------------------------------
from pycat.toolbox.image_processing.size_estimation import (  # noqa: E402,F401
    AUTO_OBJECT_SIZE_VALID_WORKFLOWS, auto_object_size_valid, estimate_object_size_px, estimate_object_size_px_brightfield)

