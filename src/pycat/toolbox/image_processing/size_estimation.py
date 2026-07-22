"""Automatic object-size estimation - split out of image_processing_tools (1.6.248).

estimate_object_size_px is the headless/batch top-hat + Otsu estimator (median equivalent diameter ->
ball_radius) that feeds downstream segmentation; auto_object_size_valid + AUTO_OBJECT_SIZE_VALID_WORKFLOWS
gate WHICH workflows it is valid for; estimate_object_size_px_brightfield is the experimental edge-based
variant. Moved VERBATIM - no threshold or measurement change; pinned BEFORE the move by
test_image_processing_size_characterization (exact object_size_px / ball_radius / n_objects on a fixed
synthetic scene). Self-contained science, no napari/Qt.
"""
from __future__ import annotations

import numpy as np
import skimage as sk
import scipy.ndimage as ndi


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
