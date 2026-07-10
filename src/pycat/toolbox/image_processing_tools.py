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
import skimage as sk
import napari
from napari.utils.notifications import show_warning as napari_show_warning
from napari.utils.notifications import show_info as napari_show_info
import scipy.ndimage as ndi
from scipy.interpolate import RectBivariateSpline
from pywt import wavedecn, waverecn 
import SimpleITK as sitk

# Local application imports
from pycat.utils.general_utils import dtype_conversion_func, get_default_intensity_range 
from pycat.ui.ui_utils import add_image_with_default_colormap


def _safe_equalize_adapthist(img, **kwargs):
    """CLAHE that is safe across skimage versions.

    ``skimage.exposure.equalize_adapthist`` requires float input in [0, 1].
    On skimage >= 0.26 an out-of-range float raises ValueError; on older
    versions it silently clips everything to the maximum, collapsing the image
    to a near-uniform field (the "yellow field / everything in one bin" bug).
    Background-subtracted and enhanced images here are in the ORIGINAL intensity
    scale (values far above 1), so we min-max normalise to [0, 1] before running
    CLAHE. A constant image is returned as zeros (nothing to equalise).
    """
    a = np.asarray(img, dtype=np.float32)
    lo = float(a.min()); hi = float(a.max())
    if hi <= lo:
        return np.zeros_like(a)
    a = (a - lo) / (hi - lo)
    return sk.exposure.equalize_adapthist(a, **kwargs)





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

def pseudo3d_tri_planar_filter(volume: np.ndarray, filter_2d_fn, **filter_kwargs) -> np.ndarray:
    """
    Apply a 2D linear filter to a (Z, H, W) volume in pseudo-3D mode:
    run the same filter along all three orthogonal slicing directions
    (XY, XZ, YZ) and average the three results.

    Parameters
    ----------
    volume : np.ndarray, shape (Z, H, W)
        Input volume, float32.
    filter_2d_fn : callable(2D array, **kwargs) -> 2D array
        A genuinely linear 2D filter, e.g. a Gaussian smoothing wrapper,
        the Gabor bank convolution, or a DoG blob-enhancement function.
        Must accept and return arrays of the same 2D shape.
    **filter_kwargs :
        Passed through to filter_2d_fn unchanged for every slice/plane.

    Returns
    -------
    np.ndarray, shape (Z, H, W), float32 — the averaged tri-planar result.

    Notes
    -----
    For a single 2D image (no real Z-stack), pass a volume with Z=1 and
    this degrades gracefully to the plain 2D filter response (the XZ and
    YZ passes become 1-pixel-thin "planes" that filter_2d_fn still handles,
    though the averaging benefit only manifests with a genuine multi-slice
    Z-stack — this function is intended for Z>1 volumes).

    IMPORTANT — index-space kernel, not physical-distance-space:
    The identical 2D kernel/sigma is applied to all three planes (XY, XZ,
    YZ) in *index* units (pixels along X/Y, slices along Z or frames along
    T) — it is NOT rescaled per axis to account for anisotropic physical
    spacing. Z-step in a Z-stack is very often several times larger than
    the XY pixel size, and a frame interval in a time series has no
    physical length at all. This is the standard simplification used in
    tri-planar filtering (matching the request this was built from: "3
    identical filters concatenated") — the technique is justified by
    genuine sample-to-sample *correlation* between adjacent planes, not by
    achieving isotropic smoothing in physical distance. Do not assume the
    XZ/YZ (or XT/YT) passes represent the same physical blur radius as the
    XY pass; verify the acquisition is in a genuinely correlated regime
    (see estimate_temporal_correlation for the time-series case) before
    relying on this for quantitative measurements.
    """
    volume = np.asarray(volume).astype(np.float32)
    Z, H, W = volume.shape

    # ── Pass 1: XY planes (standard per-Z-slice filtering) ────────────────
    xy_result = np.empty_like(volume)
    for z in range(Z):
        xy_result[z] = filter_2d_fn(volume[z], **filter_kwargs)

    if Z == 1:
        # No genuine Z extent — XZ/YZ passes would degenerate to filtering
        # single-row/column strips, which adds noise rather than signal.
        return xy_result

    # ── Pass 2: XZ planes — fix Y, filter each (Z, X) slice ───────────────
    xz_result = np.empty_like(volume)
    for y in range(H):
        plane = volume[:, y, :]                       # (Z, X)
        xz_result[:, y, :] = filter_2d_fn(plane, **filter_kwargs)

    # ── Pass 3: YZ planes — fix X, filter each (Z, Y) slice ───────────────
    yz_result = np.empty_like(volume)
    for x in range(W):
        plane = volume[:, :, x]                        # (Z, Y)
        yz_result[:, :, x] = filter_2d_fn(plane, **filter_kwargs)

    return (xy_result + xz_result + yz_result) / 3.0


def gaussian_smooth_2d(image: np.ndarray, sigma: float) -> np.ndarray:
    """Thin wrapper around ndi.gaussian_filter for use with pseudo3d_tri_planar_filter."""
    return ndi.gaussian_filter(np.asarray(image).astype(np.float32), sigma=sigma)


def gaussian_smooth_3d_pseudo(volume: np.ndarray, sigma: float) -> np.ndarray:
    """Pseudo-3D (tri-planar) Gaussian smoothing of a (Z, H, W) volume."""
    return pseudo3d_tri_planar_filter(volume, gaussian_smooth_2d, sigma=sigma)


def gabor_filter_3d_pseudo(volume: np.ndarray) -> np.ndarray:
    """
    Pseudo-3D (tri-planar) Gabor filtering of a (Z, H, W) volume.
    Reuses the exact same precomputed 2D Gabor kernel bank
    (gabor_filter_func / _GABOR_KERNELS) for every plane in every
    orientation pass.
    """
    return pseudo3d_tri_planar_filter(volume, gabor_filter_func)


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


def dog_blob_enhance_3d_pseudo(volume: np.ndarray, sigma_lo: float = 2.0,
                               sigma_hi: float = 3.2) -> np.ndarray:
    """Pseudo-3D (tri-planar) DoG blob enhancement of a (Z, H, W) volume."""
    return pseudo3d_tri_planar_filter(
        volume, dog_blob_enhance_2d, sigma_lo=sigma_lo, sigma_hi=sigma_hi)


# Image adjustments #

def apply_rescale_intensity(image, out_min=None, out_max=None):
    """
    Rescales the intensity of an image to a specified range, adjusting its pixel values accordingly.

    This function modifies the intensity values of the image so that the output image's pixel intensities
    are scaled between `out_min` and `out_max`. This is useful for enhancing image contrast or normalizing
    image data.

    Parameters
    ----------
    image : numpy.ndarray
        The input image whose intensities are to be rescaled.

    out_min : float, optional
        The minimum intensity value for the output image. If not provided, defaults to the minimum value
        supported by the input image's data type.

    out_max : float, optional
        The maximum intensity value for the output image. If not provided, defaults to the maximum value
        supported by the input image's data type.

    Returns
    -------
    rescaled_image : numpy.ndarray
        The image with rescaled intensity values.

    Notes
    -----
    It is crucial not to set `out_min` and `out_max` to the same value to avoid a division by zero error.
    Also, ensure that the output range does not exceed the input image's data type range; for example,
    scaling an 8-bit image to values outside 0-255 will lead to clipping and potential data loss.
    """


    input_dtype = str(image.dtype)  # Store the input image data type

    # Get the default intensity range for the input image data type
    default_min, default_max = get_default_intensity_range(input_dtype)

    # Ensure the output range is within the input dtype range
    out_min = int(default_min) if out_min is None or (out_min < default_min) else out_min
    out_max = int(default_max) if out_max is None or (out_max > default_max) else out_max

    # Ensure the output range does not have the same minimum and maximum values
    if out_min == out_max:
        napari_show_warning("The output range cannot have the same minimum and maximum values.")

    # Rescale the intensity of the image to the specified range
    out_range = (out_min, out_max)
    rescaled_image = sk.exposure.rescale_intensity(image, out_range=out_range).astype(image.dtype)

    # Ensure the image is the same dtype as the input
    rescaled_image = dtype_conversion_func(rescaled_image, input_dtype)  

    return rescaled_image


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
    if active_layer is not None and isinstance(active_layer, napari.layers.Image):
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
    add_image_with_default_colormap(rescaled_image, viewer, name=f"Intensity Rescaled {active_layer.name}")


def invert_image(image):
    """
    Inverts the intensity of an image, mapping dark regions to light and vice versa, suitable for different data types.

    This function applies an inversion transformation where each pixel's intensity is subtracted from the maximum 
    possible value for its data type, effectively reversing its brightness. This operation is tailored for different
    data types to maintain the integrity of the image's contrast and appearance.

    Parameters
    ----------
    image : numpy.ndarray
        The input image to be processed. The image can be of any data type that is supported by the function.

    Returns
    -------
    inverted_image : numpy.ndarray
        The intensity-inverted image, matching the input image's data type and dimensions.

    Notes
    -----
    The inversion logic varies by data type:
    - Unsigned integers: Subtract pixel values from their maximum possible value.
    - Signed integers: Subtract pixel values from -1, flipping their sign.
    - Floats (0 to 1 range): Subtract pixel values from 1.

    This function assumes the input image's pixel values are appropriately scaled for their data type.
    """

    inverted_image = sk.util.invert(image)
    return inverted_image


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
    if active_layer is not None and isinstance(active_layer, napari.layers.Image):
        image = active_layer.data
    else:
        raise ValueError("No active image layer selected.")
    
    # Apply the invert image function to the rescaled LoG filtered image
    inverted_image = invert_image(image)

    # Add the inverted image to the viewer
    add_image_with_default_colormap(inverted_image, viewer, name=f"Inverted {active_layer.name}")


def upscale_image_interp(image, num_row_initial, num_col_initial, upscale_factor=2, pad=False):
    """
    Upscales an image using bicubic interpolation to enhance its resolution. This function increases the density
    of image pixels based on the given upscale factor, applying bicubic spline interpolation to estimate the pixel
    values at new grid points.

    Parameters
    ----------
    image : numpy.ndarray
        The input image array to be upscaled.
    num_row_initial : int
        The number of rows in the input image.
    num_col_initial : int
        The number of columns in the input image.
    upscale_factor : int, optional
        The factor by which the image resolution is to be increased, doubling the dimensions by default.
    pad : bool, optional
        If True, retains a constant border padding of 10 pixels to mitigate edge artifacts. Default is False,
        which removes the padding from the final output.

    Returns
    -------
    magnified_image : numpy.ndarray
        The upscaled image with increased resolution. If padding is not removed, the returned image includes a
        10-pixel border around the edges.

    Note
    ----
    The function pads the upscaled image with a constant border of 10 pixels to mitigate edge artifacts, unless
    specified otherwise by the `pad` parameter. This is important for applications where edge continuity is critical.
    """

    # Output grid size.
    n_row_out = int(np.round(upscale_factor * num_row_initial))
    n_col_out = int(np.round(upscale_factor * num_col_initial))

    # Separable 2-D Akima interpolation (columns, then rows). Akima is a local,
    # shape-preserving interpolant: unlike a bicubic spline it does NOT overshoot
    # at sharp intensity edges, so it produces no ringing halos or negative
    # values around bright puncta (the bicubic path could dip hundreds of counts
    # below background). Falls back to the bicubic spline if Akima is unavailable.
    try:
        from scipy.interpolate import Akima1DInterpolator
        col0 = np.arange(num_col_initial, dtype=float)
        row0 = np.arange(num_row_initial, dtype=float)
        col1 = np.linspace(0, num_col_initial - 1, n_col_out)
        row1 = np.linspace(0, num_row_initial - 1, n_row_out)
        tmp = Akima1DInterpolator(col0, np.asarray(image, dtype=float),
                                  axis=1)(col1)          # (H, n_col_out)
        magnified_image = Akima1DInterpolator(row0, tmp, axis=0)(row1)  # (n_row_out, n_col_out)
    except Exception:
        x0 = np.linspace(-0.5, 0.5, num_col_initial)
        y0 = np.linspace(-0.5, 0.5, num_row_initial)
        x = np.linspace(-0.5, 0.5, n_col_out)
        y = np.linspace(-0.5, 0.5, n_row_out)
        interp_func = RectBivariateSpline(y0, x0, image)
        magnified_image = interp_func(y, x)

    # Clean up by setting negative values to zero and padding the image
    magnified_image = np.asarray(magnified_image)
    magnified_image[magnified_image < 0] = 0
    magnified_image = np.pad(magnified_image, 10, mode='constant')

    return magnified_image[10:-10, 10:-10]  if not pad else magnified_image # Remove the padding if not requested



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
            add_image_with_default_colormap(upscaled_img, viewer,
                                            name=_out_name, scale=_new_scale)
        else:
            add_image_with_default_colormap(upscaled_img, viewer, name=_out_name)

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


_GABOR_KERNELS = [
    np.abs(sk.filters.gabor_kernel(frequency=1.0,
                                    theta=k / 4.0 * np.pi,
                                    bandwidth=1.0))
    for k in range(4)
]


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
  if active_layer is None or not isinstance(active_layer, napari.layers.Image):
      # Raise an error if no layer is currently active
      raise ValueError("No active image layer selected")

  # Retrieve the image data from the active layer
  image = active_layer.data

  # Apply the peak and edge enhancement function to the input image
  enhanced_image = peak_and_edge_enhancement_func(image, ball_radius)

  # Add the enhanced image as a new layer to the viewer with a descriptive name
  add_image_with_default_colormap(enhanced_image, viewer, name=f"Peak & Edge Enhanced {active_layer.name}")


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
    if active_layer is not None and isinstance(active_layer, napari.layers.Image):
        image = active_layer.data
    else:
        raise ValueError("No active image layer selected.")
    
    sigma = float(sigma_input.text()) if sigma_input.text() else 3

    # Apply the LoG filter to the input image
    LoG_image = apply_laplace_of_gauss_filter(image, sigma)

    # Add the LoG filtered image to the viewer
    add_image_with_default_colormap(LoG_image, viewer, name=f"LoG of {active_layer.name}")


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
    if active_layer is None or not isinstance(active_layer, napari.layers.Image):
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
    add_image_with_default_colormap(image, viewer, name=f"Filtered {active_layer.name}")


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
    if active_layer is None or not isinstance(active_layer, napari.layers.Image):
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
    add_image_with_default_colormap(CLAHE_img, viewer, name=f"CLAHE Contrast EQed {active_layer.name}")


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
    add_image_with_default_colormap(DPR_img, viewer, name=f"DPR Corrected {active_layer.name}")


# Background and Noise Correction # 

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
    if active_layer is not None and isinstance(active_layer, napari.layers.Image):
        image = active_layer.data
    else:
        napari_show_warning("No active image layer selected.")

    # Perform the background removal on the image
    bg_removed_image = rb_gaussian_background_removal(image, ball_radius, equalize_intensity=equalize_intensity_input)

    # Add the processed image as a new layer in the viewer
    add_image_with_default_colormap(bg_removed_image, viewer, name=f'RB-Gaussian Background Removed {active_layer.name}')
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
    if active_layer is None or not isinstance(active_layer, napari.layers.Image):
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
    add_image_with_default_colormap(enhanced_image, viewer, name=f'Enhanced Background Removed {active_layer.name}')
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
    if active_layer is not None and isinstance(active_layer, napari.layers.Image):
        image = active_layer.data
    else:
        raise ValueError("No active image layer selected.")

    psf_fwhm = int(psf_input.text()) if psf_input.text() else 3
    noise_lvl = int(noise_level_input.text()) if noise_level_input.text() else 1

    # Process the image with the WBNS function
    WBNS_img, _ = wbns_func(image, psf_fwhm, noise_lvl)

    # Display the processed image
    add_image_with_default_colormap(WBNS_img, viewer, name=f"BG and Noise Corrected {active_layer.name}")


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
    if active_layer is not None and isinstance(active_layer, napari.layers.Image):
        image = active_layer.data
    else:
        napari_show_warning("No active image layer selected.")
        return

    psf_fwhm = int(psf_input.text()) if psf_input.text() else 3
    noise_lvl = int(noise_level_input.text()) if noise_level_input.text() else 1

    # Process the image with the WBNS function
    _, wavelet_noise_corrected = wbns_func(image, psf_fwhm, noise_lvl)

    # Display the processed image
    add_image_with_default_colormap(wavelet_noise_corrected, viewer, name=f"Wavelet Noise Corrected {active_layer.name}")

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
    if active_layer is not None and isinstance(active_layer, napari.layers.Image):
        image = active_layer.data
    else:
        napari_show_warning("No active image layer selected.")
        return

    # Get the radius value from the input field
    radius = float(radius_input.text()) if radius_input.text() else 2

    # Apply the bilateral filter to the image
    filtered_image = apply_bilateral_filter(image, radius)

    # Add the filtered image as a new layer to the viewer
    add_image_with_default_colormap(filtered_image, viewer, name=f"Bilateral Filtered {active_layer.name}")


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
    if active_layer is None or not isinstance(active_layer, napari.layers.Image):
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
    add_image_with_default_colormap(pre_processed_image, viewer, name=f"Pre-Processed {active_layer.name}")


# ---------------------------------------------------------------------------
# Calibration-frame background correction
# ---------------------------------------------------------------------------
# Empirical correction using a separately-acquired reference (free dye / flat
# field, or a clear no-condensate frame). The reference is specific to a
# microscope + settings + sample combination, so it is loaded once and applied
# to matching data rather than derived per-dataset.

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
# Automatic object-size → ball_radius estimation (for headless / batch use)
# ---------------------------------------------------------------------------

# Workflows for which intensity-threshold-based object-size estimation is VALID.
# The estimator assumes discrete high-intensity objects on a thresholdable
# background — true for fluorescence puncta/condensates/droplets, NOT for
# brightfield (edge/phase contrast, no intensity hierarchy), time-series (object
# size drifts as objects grow/coarsen so a single median is wrong), or z-stacks
# (a 2D-projection diameter is not the 3D object size).
AUTO_OBJECT_SIZE_VALID_WORKFLOWS = frozenset({
    'condensate',       # 2D cellular fluorescence
    'invitro_fluor',    # 2D in-vitro fluorescence
})


def auto_object_size_valid(workflow: str) -> bool:
    """Whether automatic (top-hat + Otsu) object-size estimation is valid for a
    given workflow identity. See AUTO_OBJECT_SIZE_VALID_WORKFLOWS."""
    return str(workflow) in AUTO_OBJECT_SIZE_VALID_WORKFLOWS


def estimate_object_size_px(image, workflow=None, min_area_px=4,
                            tophat_radius=None, return_diagnostics=False):
    """Estimate a representative object diameter (px) and ball_radius from a
    fluorescence image, without a human in the loop (for batch processing).

    Pipeline (Meet Raval's validated approach):
      1. White top-hat to isolate small bright objects from background.
      2. Otsu threshold on the top-hat response → foreground objects.
      3. Label; keep objects >= min_area_px.
      4. object_size = median equivalent diameter over kept objects.
      5. ball_radius = round(object_size / 2) (native px), clamped >= 1.

    VALIDITY: this is only meaningful where discrete bright objects sit on a
    thresholdable background (fluorescence). If ``workflow`` is supplied and is
    not in AUTO_OBJECT_SIZE_VALID_WORKFLOWS, this raises ValueError — the caller
    must not apply it to brightfield / time-series / z-stack data.

    # TODO(optimize-on-real-data): the top-hat radius, Otsu vs multi-Otsu choice,
    # and min_area cutoff are first-pass defaults. Validate/tune against a real
    # cellular- and in-vitro-fluorescence batch (see Meet's STEP 2 diagnostic).

    Parameters
    ----------
    image : 2D array (a single fluorescence frame/channel).
    workflow : optional workflow id for the validity guard.
    min_area_px : ignore objects smaller than this (noise).
    tophat_radius : white-top-hat disk radius (px). Default: ~ min(H,W)//50,
        clamped to [3, 25] — big enough to pass typical puncta, small enough to
        suppress cell-scale background.
    return_diagnostics : if True, also return a dict with the object-diameter
        array and intermediate masks (for a diagnostic figure).

    Returns
    -------
    dict with keys: object_size_px, ball_radius, n_objects, (and 'diagnostics'
    if requested). Returns object_size_px=None / ball_radius=None if no objects
    are found (caller should fall back to its default).
    """
    if workflow is not None and not auto_object_size_valid(workflow):
        raise ValueError(
            f"Automatic object-size estimation is not valid for workflow "
            f"'{workflow}'. Valid: {sorted(AUTO_OBJECT_SIZE_VALID_WORKFLOWS)}.")

    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim != 2:
        # Reduce to 2D defensively (take max projection over leading axes).
        arr = np.max(arr, axis=tuple(range(arr.ndim - 2)))

    # Normalise to [0, 1] for a stable Otsu.
    mn, mx = float(arr.min()), float(arr.max())
    norm = (arr - mn) / (mx - mn) if mx > mn else np.zeros_like(arr)

    if tophat_radius is None:
        tophat_radius = int(np.clip(min(norm.shape) // 50, 3, 25))
    footprint = sk.morphology.disk(int(max(1, tophat_radius)))
    tophat = sk.morphology.white_tophat(norm, footprint)

    result = {'object_size_px': None, 'ball_radius': None, 'n_objects': 0}
    if tophat.max() <= tophat.min():
        return (result if not return_diagnostics
                else {**result, 'diagnostics': {'tophat': tophat}})

    try:
        thr = sk.filters.threshold_otsu(tophat[tophat > 0])
    except Exception:
        thr = sk.filters.threshold_otsu(tophat)
    fg = tophat > thr

    labels = sk.measure.label(fg)
    props = sk.measure.regionprops(labels)

    def _equiv_diam(p):
        # skimage renamed equivalent_diameter → equivalent_diameter_area (0.26+).
        d = getattr(p, 'equivalent_diameter_area', None)
        return d if d is not None else p.equivalent_diameter

    diams = np.array([_equiv_diam(p) for p in props
                      if p.area >= min_area_px], dtype=float)
    if diams.size == 0:
        return (result if not return_diagnostics
                else {**result, 'diagnostics': {'tophat': tophat, 'fg': fg}})

    object_size = float(np.median(diams))
    ball_radius = max(1, int(round(object_size / 2.0)))
    result = {'object_size_px': object_size,
              'ball_radius': ball_radius,
              'n_objects': int(diams.size)}
    if return_diagnostics:
        result['diagnostics'] = {'tophat': tophat, 'fg': fg, 'diameters': diams}
    return result


def estimate_object_size_px_brightfield(image, min_area_px=4,
                                        return_diagnostics=False):
    """EXPERIMENTAL edge/texture-based object-size estimator for BRIGHTFIELD.

    Brightfield contrast is edge/phase, not intensity, so the fluorescence
    top-hat + Otsu estimator (`estimate_object_size_px`) is NOT valid on it.
    This variant instead segments via local gradient magnitude (Sobel) + Otsu
    on the edge-energy image, then measures object diameters the same way.

    ⚠️ NOT VALIDATED. This is a first-pass approach that must be checked against
    real brightfield data before use in an automated pipeline — brightfield
    regimes vary widely (dense/sparse, in/out of focus, ring-like). It is
    intentionally NOT wired into the batch auto-estimation path; enable only
    after validation.
    # TODO(validate-on-real-data): confirm on representative brightfield batches
    # (sparse+large droplets, dense small, out-of-focus/ring) before trusting.

    Returns the same dict shape as estimate_object_size_px.
    """
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim != 2:
        arr = np.max(arr, axis=tuple(range(arr.ndim - 2)))
    mn, mx = float(arr.min()), float(arr.max())
    norm = (arr - mn) / (mx - mn) if mx > mn else np.zeros_like(arr)

    edges = sk.filters.sobel(norm)
    result = {'object_size_px': None, 'ball_radius': None, 'n_objects': 0}
    if edges.max() <= edges.min():
        return result
    thr = sk.filters.threshold_otsu(edges)
    fg = edges > thr
    # Close edge rings into filled objects.
    fg = ndi.binary_fill_holes(sk.morphology.binary_closing(
        fg, sk.morphology.disk(2)))
    labels = sk.measure.label(fg)

    def _equiv_diam(p):
        d = getattr(p, 'equivalent_diameter_area', None)
        return d if d is not None else p.equivalent_diameter
    diams = np.array([_equiv_diam(p) for p in sk.measure.regionprops(labels)
                      if p.area >= min_area_px], dtype=float)
    if diams.size == 0:
        return result
    object_size = float(np.median(diams))
    result = {'object_size_px': object_size,
              'ball_radius': max(1, int(round(object_size / 2.0))),
              'n_objects': int(diams.size)}
    if return_diagnostics:
        result['diagnostics'] = {'edges': edges, 'fg': fg, 'diameters': diams}
    return result
