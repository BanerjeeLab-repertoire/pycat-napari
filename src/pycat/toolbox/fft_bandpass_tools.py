"""
PyCAT FFT Bandpass & Manual Threshold Tools
=============================================
A Fourier-domain bandpass filter (annular frequency mask) for background
subtraction / feature isolation, plus a MATLAB-style manual binarization
(im2bw) that thresholds on an absolute intensity value rather than an
automatically-chosen level.

These are ports of the author's original tools (FFTmaskcreator.py,
FFT_bgsubtract.py, im2bw.py), corrected for several bugs in the originals:
  - the annular mask is now built with exact, symmetric disk placement that
    works for both even- and odd-sized images (the original had off-by-one
    placement and required XXX.5 radii on even images);
  - the 3D branch no longer returns after the first frame;
  - the FFT is applied per-2D-frame for a stack (the original fft2 on a 3D
    array transformed the wrong axes);
  - im2bw no longer mutates its input array in place.

Author
------
    Original tools: Gable Wadsworth, 2019
    PyCAT port: Banerjee Lab, SUNY Buffalo, 2026
"""

from __future__ import annotations

import numpy as np

from pycat.utils.tag_registry import tags_layer
import skimage as sk
from scipy import fftpack

# Via the notification shim: keeps the array functions importable with no GUI stack.
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning


# ---------------------------------------------------------------------------
# Annular frequency mask
# ---------------------------------------------------------------------------

def fft_annular_mask(shape: tuple, low_radius: float, high_radius: float) -> np.ndarray:
    """
    Build an annular (band) mask in the 2D Fourier domain.

    The mask is 1 in an annulus between `low_radius` and `high_radius`
    (in pixels, measured in frequency space from the zero-frequency centre)
    and 0 elsewhere. Applied to a centred FFT it keeps spatial frequencies
    between the two cutoffs — a bandpass that removes both the slowly-varying
    background (very low frequencies, below high_radius's complement) and the
    fine noise, depending on how the radii are chosen.

    Convention (matches the original tool): `high_radius` is the OUTER disk
    and `low_radius` the INNER disk, so the retained annulus is
    (disk(high_radius) − disk(low_radius)). Keeping mid-range frequencies
    suppresses the large-scale illumination background.

    Parameters
    ----------
    shape : (H, W) of the target image.
    low_radius : inner radius (px) — frequencies below this are removed.
    high_radius : outer radius (px) — frequencies above this are removed.

    Returns
    -------
    mask : (H, W) float array, fft-shifted so it multiplies a raw fftpack.fft2
        output directly (zero-frequency at the corner).
    """
    H, W = shape
    cy, cx = H / 2.0, W / 2.0
    y, x = np.ogrid[:H, :W]
    r = np.sqrt((y - cy) ** 2 + (x - cx) ** 2)

    lo = min(low_radius, high_radius)
    hi = max(low_radius, high_radius)
    annulus = ((r <= hi) & (r >= lo)).astype(float)

    # Shift so it aligns with an unshifted fftpack.fft2 output
    return fftpack.fftshift(annulus)


# ---------------------------------------------------------------------------
# Bandpass filter
# ---------------------------------------------------------------------------

@tags_layer('bandpass', role='preprocessed',
            summary='FFT bandpass filter')
def fft_bandpass(image: np.ndarray, low_cutoff: float, high_cutoff: float) -> np.ndarray:
    """
    Apply a Fourier-domain bandpass filter to a 2D image or a (T/Z, H, W) stack.

    For a stack, each 2D frame is filtered independently (the original applied
    a single fft2 to the whole 3D array, which is not a per-frame 2D filter).

    Parameters
    ----------
    image : 2D array or 3D stack (N, H, W).
    low_cutoff : inner frequency radius (px) — removes frequencies below this.
    high_cutoff : outer frequency radius (px) — removes frequencies above this.

    Returns
    -------
    filtered : real-valued array, same shape as `image`.
    """
    img = np.asarray(image, dtype=float)

    def _filter_2d(frame):
        mask = fft_annular_mask(frame.shape, low_cutoff, high_cutoff)
        f = fftpack.fft2(frame)
        return fftpack.ifft2(f * mask).real

    if img.ndim == 2:
        return _filter_2d(img)
    elif img.ndim == 3:
        out = np.empty_like(img)
        for i in range(img.shape[0]):
            out[i] = _filter_2d(img[i])
        return out
    else:
        raise ValueError(f"fft_bandpass expects 2D or 3D input, got ndim={img.ndim}")


# ---------------------------------------------------------------------------
# MATLAB-style manual threshold (im2bw)
# ---------------------------------------------------------------------------

def im2bw(image: np.ndarray, threshold: float) -> np.ndarray:
    """
    MATLAB-style manual binarization: pixels ≥ threshold → 1, else 0.

    Unlike skimage's automatic threshold functions (Otsu, Li, etc.), this
    thresholds on an absolute intensity value the user supplies — useful when
    you know the level you want and don't want an algorithm to pick it. The
    input array is not modified (the original mutated it in place).

    Parameters
    ----------
    image : input intensity image (any shape).
    threshold : absolute intensity cutoff.

    Returns
    -------
    binary : uint8 array (0/1), same shape as `image`.
    """
    arr = np.asarray(image)
    return (arr >= threshold).astype(np.uint8)

# ---------------------------------------------------------------------------
# napari run-wrappers (active-layer convention, matching run_clahe etc.)
# ---------------------------------------------------------------------------

def run_fft_bandpass(low_input, high_input, viewer):
    """
    Apply the FFT bandpass to the active image layer and add the result as a
    new layer. `low_input` / `high_input` are QLineEdit widgets holding the
    inner/outer frequency radii (px).
    """
    import napari
    active = viewer.layers.selection.active
    if active is None or not isinstance(active, napari.layers.Image):
        napari_show_warning("Select an active image layer first.")
        return
    try:
        low = float(low_input.text()) if low_input.text() else 3.0
        high = float(high_input.text()) if high_input.text() else 40.0
    except ValueError:
        napari_show_warning("Cutoffs must be numbers (pixels in frequency space).")
        return
    result = fft_bandpass(active.data, low, high)
    viewer.add_image(result, name=f"{active.name} FFT bandpass [{low:g}-{high:g}]")
    napari_show_info(f"FFT bandpass applied ({low:g}-{high:g} px).")


def run_im2bw(threshold_input, viewer):
    """
    Binarize the active image layer with a manual absolute threshold and add
    the result as a labels layer. `threshold_input` is a QLineEdit widget.
    """
    import napari
    active = viewer.layers.selection.active
    if active is None or not isinstance(active, napari.layers.Image):
        napari_show_warning("Select an active image layer first.")
        return
    txt = threshold_input.text().strip()
    if not txt:
        napari_show_warning("Enter a threshold value.")
        return
    try:
        thresh = float(txt)
    except ValueError:
        napari_show_warning("Threshold must be a number.")
        return
    binary = im2bw(active.data, thresh)
    viewer.add_labels(binary.astype(int), name=f"{active.name} im2bw (t={thresh:g})")
    napari_show_info(f"Binarized at threshold {thresh:g}: "
                     f"{int(binary.sum())} pixels above threshold.")

