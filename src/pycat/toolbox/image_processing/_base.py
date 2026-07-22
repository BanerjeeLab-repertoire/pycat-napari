"""Shared image-processing primitives - split out of image_processing_tools (1.6.249).

The low-level building blocks every algorithm family reuses: intensity rescaling (apply_rescale_intensity),
inversion (invert_image), CLAHE-safe adaptive equalisation (_safe_equalize_adapthist), bicubic upscaling
(upscale_image_interp), the pseudo-3D tri-planar filter wrapper (pseudo3d_tri_planar_filter), and the two
lazy-napari display helpers (_add_image, _napari). Moved VERBATIM - no numerics changed; the registered ops
(apply_rescale_intensity / invert_image / upscale_image_interp) are pinned by
test_image_processing_base_characterization. napari stays function-scoped so this module imports headless.
"""
from __future__ import annotations

import numpy as np
import skimage as sk
from pycat.utils.tag_registry import tags_layer
from scipy.interpolate import RectBivariateSpline
from pycat.utils.general_utils import dtype_conversion_func, get_default_intensity_range
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.notify import show_info as napari_show_info


def _add_image(image, viewer, operation=None, **kw):
    """Lazy wrapper for the viewer helper, plus the intensity-semantics tag.

    **This used to call ITSELF** — ``return _add_image(image, viewer, **kw)`` — which is
    infinite recursion, and every one of the 19 call sites in this module would have blown
    the stack. It was meant to lazily import ``add_image_with_default_colormap`` (importing
    it at module scope would pull in Qt and block the headless import of this module's array
    functions).

    ``operation`` records what produced this layer, so that measurements can refuse an input
    whose intensity semantics have been destroyed. See ``pycat.utils.intensity_semantics``:
    a white top-hat removes the background, a Laplacian-of-Gaussian is signed and centred on
    zero (giving a NEGATIVE partition coefficient), and CLAHE measures the contrast-
    enhancement algorithm rather than the sample. The information is in the provenance, not
    the pixels — so it is recorded here, where the operation happens.
    """
    from pycat.ui.ui_utils import add_image_with_default_colormap
    layer = add_image_with_default_colormap(image, viewer, **kw)
    if operation is not None and layer is not None:
        try:
            from pycat.utils.intensity_semantics import mark_intensity_semantics
            mark_intensity_semantics(layer, operation)
        except Exception:
            pass          # tagging must never break the operation itself
    return layer


def _napari():
    """Lazy napari import, for the viewer-facing helpers in this module."""
    import napari
    return napari


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


@tags_layer('rescale', role='preprocessed',
            summary='Intensity rescaling to a target range')
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


@tags_layer('invert', role='preprocessed', inputs=('image',),
            summary='Intensity inversion (dark <-> bright)')
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


@tags_layer('upscale', role='preprocessed',
            summary='Bicubic upscaling')
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
