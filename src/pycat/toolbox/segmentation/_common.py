"""Shared low-level segmentation helpers - split out of segmentation_tools (1.6.240).

_to_uint16_safe rescales an arbitrary-dtype image into a safe uint16 for the OpenCV/skimage paths that
require it. Moved VERBATIM; imported by the watershed and puncta-refinement families.
"""
from __future__ import annotations

import numpy as np
import skimage as sk


def _to_uint16_safe(image):
    """Convert any image to uint16 without clipping float values outside [-1, 1].

    ``sk.util.img_as_uint`` requires float input in [-1, 1] and raises or clips
    outside that range. Background-removed and CLAHE-processed images are often
    float32 with values well outside that range. This helper normalises the input
    to [0, 1] first so the uint16 conversion is always valid, preserving relative
    intensity differences (which is all the refinement filter needs for kurtosis,
    std-dev, and SNR checks).
    """
    arr = np.asarray(image, dtype=np.float32)
    mn, mx = float(arr.min()), float(arr.max())
    if mx - mn < 1e-12:
        return np.zeros_like(arr, dtype=np.uint16)
    normed = (arr - mn) / (mx - mn)
    return sk.util.img_as_uint(normed)
