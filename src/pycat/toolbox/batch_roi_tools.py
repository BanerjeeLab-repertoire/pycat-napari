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

from pycat.utils.tag_registry import tags_layer
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


@tags_layer('multi_otsu', role='mask',
            summary='Multi-Otsu cell mask (valid on fluorescence, NOT brightfield)',
            target='cell')
def multi_otsu_cell_mask(
    image: np.ndarray,
    n_classes: int = 3,
    cell_diameter: int = 100,
) -> np.ndarray:
    """
    Generate a labeled cell mask via multi-Otsu thresholding + distance
    transform watershed.

    Intended as a fallback cell segmentation for fluorescence channels
    (GFP, RFP, etc.) when no dedicated nuclear stain (DAPI) or Cellpose
    result is available.  Works because fluorophores are weakly persistent
    in the cytoplasm / nucleus, creating a meaningful intensity hierarchy:
      class 0 (below t[0]): outside cell — background
      class 1 (t[0]–t[1]):  cytoplasm / nucleoplasm
      class 2 (above t[1]): bright condensates / puncta

    The lowest threshold t[0] captures the full cell body (both cytoplasm
    and nucleus), which is the appropriate boundary for cell segmentation.
    Individual cells are separated by watershed on the distance transform,
    seeded from local maxima spaced by half the expected cell diameter —
    matching the output format of Cellpose (integer-labeled regions).

    Parameters
    ----------
    image : np.ndarray
        2-D fluorescence image (any dtype).
    n_classes : int
        Number of intensity classes for multi-Otsu (default 3).
    cell_diameter : int
        Expected cell diameter in pixels, used to set the minimum object
        size filter and the watershed seed spacing (default 100 px).

    Returns
    -------
    labeled : np.ndarray (uint16, H×W)
        Integer-labeled cell mask (0 = background, 1…N = cells).
    """
    import skimage as sk
    from scipy import ndimage as _ndi
    from skimage.feature import peak_local_max

    img = np.asarray(image).astype(np.float32)

    # Pre-smooth to suppress condensate puncta before thresholding — we want
    # the cell body boundary, not individual bright spots driving the histogram
    sigma = max(1.0, cell_diameter * 0.1)
    img_smooth = _ndi.gaussian_filter(img, sigma=sigma)

    try:
        thresholds = sk.filters.threshold_multiotsu(img_smooth, classes=n_classes)
    except Exception:
        thresholds = [sk.filters.threshold_otsu(img_smooth)]

    # Use lowest threshold: captures entire cell body (cytoplasm + nucleus)
    mask = img_smooth >= thresholds[0]

    # Remove objects smaller than a quarter of one cell area
    min_size = max(16, int((cell_diameter / 2) ** 2))
    mask = sk.morphology.remove_small_objects(mask, max_size=min_size)
    mask = _ndi.binary_fill_holes(mask)

    if not mask.any():
        return np.zeros(image.shape[:2], dtype=np.uint16)

    # Distance transform → watershed to separate touching cells
    dist = _ndi.distance_transform_edt(mask)

    # Seed spacing = half the cell diameter; at least 10px
    min_seed_dist = max(10, cell_diameter // 2)
    coords = peak_local_max(dist, min_distance=min_seed_dist, labels=mask)

    if len(coords) == 0:
        # Fallback: one seed at the global distance maximum
        coords = np.array([np.unravel_index(dist.argmax(), dist.shape)])

    seeds = np.zeros_like(mask, dtype=bool)
    seeds[tuple(coords.T)] = True
    seed_labels = sk.measure.label(seeds)
    labeled = sk.segmentation.watershed(-dist, seed_labels, mask=mask)

    return labeled.astype(np.uint16)


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
