"""
PyCAT Batch ROI Detection
==========================
Automatic region-of-interest detection for batch headless processing.

In the GUI, users can draw a rectangle to restrict spatial processing to
a specific cell or region.  In batch mode this is replaced by two automatic
strategies:

Strategy 1 — Cellpose-derived bounding boxes
    After Cellpose runs, each labeled cell gets its own tight bounding box.
    Preprocessing, condensate segmentation, and analysis all operate inside
    that box.  Results are stitched back to full-image coordinates.
    Use when: you have a cell mask (DAPI or clear cell segmentation).

Strategy 2 — Multi-Otsu foreground detection
    Three-class multi-Otsu thresholding separates background / cytoplasm /
    nucleus (or background / cell / bright-regions in single-channel images).
    The bounding box of all foreground pixels (above the first threshold)
    is used as the global processing region.
    Use when: single-channel images with no DAPI, or to restrict to the
    tissue/well area when imaging a sub-region of a large field.

Both strategies produce the same output: a dict of
    {cell_label: (y0, y1, x0, x1)}  bounding boxes in full-image coordinates,
which is stored in state['cell_bboxes'] and consumed by replay_condensate_segmentation
to process each cell independently in its own crop.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2025
"""

from __future__ import annotations
import numpy as np
from typing import Optional


# ---------------------------------------------------------------------------
# Strategy 1: Bounding boxes from a cell mask
# ---------------------------------------------------------------------------

def cell_bboxes_from_mask(
    labeled_cells: np.ndarray,
    padding_px: int = 8,
) -> dict[int, tuple[int, int, int, int]]:
    """
    Return per-cell bounding boxes from a labeled cell mask.

    Parameters
    ----------
    labeled_cells : (H, W) integer-labelled mask (0 = background)
    padding_px : pixels to pad each side of the bounding box

    Returns
    -------
    dict mapping cell_label → (y0, y1, x0, x1) in full-image coordinates
    """
    import skimage as sk
    H, W = labeled_cells.shape
    bboxes = {}
    for prop in sk.measure.regionprops(labeled_cells):
        r0, c0, r1, c1 = prop.bbox   # skimage bbox: (min_row, min_col, max_row, max_col)
        y0 = max(0, r0 - padding_px)
        y1 = min(H, r1 + padding_px)
        x0 = max(0, c0 - padding_px)
        x1 = min(W, c1 + padding_px)
        bboxes[prop.label] = (y0, y1, x0, x1)
    return bboxes


# ---------------------------------------------------------------------------
# Strategy 2: Multi-Otsu foreground detection
# ---------------------------------------------------------------------------

def multi_otsu_foreground_bbox(
    image: np.ndarray,
    n_classes: int = 3,
    padding_px: int = 16,
    min_foreground_fraction: float = 0.01,
) -> Optional[tuple[int, int, int, int]]:
    """
    Find the bounding box of foreground (non-background) pixels using
    multi-Otsu thresholding.

    For condensate imaging:
      n_classes=3 → thresholds separate background / cytoplasm / condensates
      The foreground mask is all pixels above the *first* (lowest) threshold,
      i.e. the union of cytoplasm + condensate regions.

    For single-channel images with no DAPI the three classes map to:
      Class 0 — dark background (slide/coverslip)
      Class 1 — cell body / cytoplasm
      Class 2 — bright puncta / condensates

    Parameters
    ----------
    image : (H, W) float32 in [0, 1]
    n_classes : number of Otsu classes (2 = simple threshold, 3 = background/
                cytoplasm/nucleus, 4 = if a 4th class is needed)
    padding_px : pixels to pad around the detected foreground
    min_foreground_fraction : if fewer than this fraction of pixels are
                              foreground, return None (image is too empty)

    Returns
    -------
    (y0, y1, x0, x1) bounding box, or None if no foreground detected
    """
    import skimage as sk
    from scipy import ndimage

    img = np.asarray(image).astype(np.float32)
    H, W = img.shape

    # Multi-Otsu thresholds
    try:
        thresholds = sk.filters.threshold_multiotsu(img, classes=n_classes)
    except Exception:
        # Fallback to simple Otsu if multi fails (e.g. not enough distinct
        # intensity levels in a very uniform image)
        thresholds = [sk.filters.threshold_otsu(img)]

    # Foreground = above the *first* threshold (lowest threshold = background boundary)
    foreground = img >= thresholds[0]

    # Remove small noise specks — require connected regions larger than 0.1%
    # of the image area to be counted as foreground
    min_size = max(4, int(H * W * 0.001))
    foreground = sk.morphology.remove_small_objects(foreground, min_size=min_size)

    # Fill holes within cells so the bounding box captures the full cell interior
    foreground = ndimage.binary_fill_holes(foreground)

    frac = foreground.sum() / (H * W)
    if frac < min_foreground_fraction:
        return None

    rows, cols = np.where(foreground)
    y0 = max(0, int(rows.min()) - padding_px)
    y1 = min(H, int(rows.max()) + padding_px + 1)
    x0 = max(0, int(cols.min()) - padding_px)
    x1 = min(W, int(cols.max()) + padding_px + 1)

    return (y0, y1, x0, x1)


def multi_otsu_cell_mask(
    image: np.ndarray,
    n_classes: int = 3,
) -> np.ndarray:
    """
    Generate a binary cell mask via multi-Otsu thresholding.
    Returns a (H, W) boolean array: True = cell body or brighter.

    Used as a fallback cell mask when Cellpose has not run yet, so
    condensate segmentation has some spatial context.
    """
    import skimage as sk
    from scipy import ndimage

    img = np.asarray(image).astype(np.float32)
    try:
        thresholds = sk.filters.threshold_multiotsu(img, classes=n_classes)
    except Exception:
        thresholds = [sk.filters.threshold_otsu(img)]

    mask = img >= thresholds[0]
    mask = sk.morphology.remove_small_objects(mask, min_size=64)
    mask = ndimage.binary_fill_holes(mask)

    # Label connected components as individual "cells" for downstream steps
    labeled = sk.measure.label(mask)
    return labeled


# ---------------------------------------------------------------------------
# Per-cell crop-process-stitch helpers
# ---------------------------------------------------------------------------

def crop_to_bbox(array: np.ndarray, bbox: tuple) -> np.ndarray:
    """Crop a 2D array to (y0, y1, x0, x1)."""
    y0, y1, x0, x1 = bbox
    return array[y0:y1, x0:x1]


def stitch_into(
    full_array: np.ndarray,
    cropped: np.ndarray,
    bbox: tuple,
    combine: str = 'or',
) -> np.ndarray:
    """
    Write a cropped result back into the full-image array.

    Parameters
    ----------
    full_array : (H, W) destination array
    cropped : (h, w) result from processing the bbox region
    bbox : (y0, y1, x0, x1)
    combine : 'or' (boolean union), 'add' (sum), 'replace' (overwrite)
    """
    y0, y1, x0, x1 = bbox
    if combine == 'or':
        full_array[y0:y1, x0:x1] |= cropped.astype(full_array.dtype)
    elif combine == 'add':
        full_array[y0:y1, x0:x1] += cropped.astype(full_array.dtype)
    else:
        full_array[y0:y1, x0:x1] = cropped.astype(full_array.dtype)
    return full_array
