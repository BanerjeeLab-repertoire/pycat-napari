"""Automatic object-size estimation - split out of image_processing_tools (1.6.248).

estimate_object_size_px is the headless/batch top-hat + multi-Otsu estimator (median equivalent diameter ->
ball_radius) that feeds downstream segmentation; auto_object_size_valid + AUTO_OBJECT_SIZE_VALID_WORKFLOWS
gate WHICH workflows it is valid for; estimate_object_size_px_brightfield is the experimental edge-based
variant. Originally moved VERBATIM (pinned BEFORE the move by test_image_processing_size_characterization);
the threshold step was later switched from plain 2-class Otsu to 3-class multi-Otsu (keep-brightest-class)
after Meet Raval confirmed on real low-contrast nuclear-condensate data that plain Otsu fuses dim background
texture with real puncta into oversized blobs where multi-Otsu correctly isolates the individual objects --
see estimate_object_size_px's WHY MULTI-OTSU docstring section. Every existing characterization pin (the
clean 7-disk scene and its brightfield variant) still passes UNCHANGED under multi-Otsu -- the two methods
agree exactly wherever there's no ambiguous middle-brightness class to split off, i.e. every scene this
suite covers so far. Self-contained science, no napari/Qt.
"""
from __future__ import annotations

import math

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
      2. Multi-Otsu (3-class) threshold on the top-hat response, KEEPING ONLY
         THE BRIGHTEST CLASS → foreground objects.
      3. Label; keep objects >= min_area_px.
      4. object_size = median equivalent diameter over kept objects.
      5. ball_radius = ceil(1.5 * (object_size / 2)) (native px), clamped >= 1
         — the SAME formula the interactive GUI cellular-analysis pipeline uses
         when the user hand-measures the object diameter with the Measure Line
         tool (``BaseDataClass.calculate_sizes``, data_modules.py:462-463:
         ``object_radius = object_size / 2; ball_radius =
         math.ceil(1.5 * object_radius)``), so the batch auto-estimate lands on
         the same ball_radius a human would get measuring the same object by
         hand. (An earlier version halved object_size with no 1.5x factor —
         object_size=6.86px -> ball_radius=3 — which disagreed with the GUI
         formula's ball_radius=6 for the same measurement.)

    VALIDITY: this is only meaningful where discrete bright objects sit on a
    thresholdable background (fluorescence). If ``workflow`` is supplied and is
    not in AUTO_OBJECT_SIZE_VALID_WORKFLOWS, this raises ValueError — the caller
    must not apply it to brightfield / time-series / z-stack data.

    WHY MULTI-OTSU, NOT PLAIN 2-CLASS OTSU: on a high-SNR image the top-hat
    response is genuinely bimodal — background collapses to ~0, real puncta
    pop out as sharp peaks — and plain 2-class Otsu finds that valley cleanly.
    On a low-contrast image with small features, the SAME top-hat response
    instead has a widespread band of dim mid-brightness texture between
    background and the real puncta, with NO clean valley. 2-class Otsu still
    forces a single global cut (it minimizes intra-class variance regardless
    of whether a valley exists), and lands well below the real-punctum floor —
    admitting most of that texture, which is spatially contiguous and so
    fuses with real objects into a handful of giant blobs instead of the
    individual puncta the top-hat clearly resolved (reported by Meet Raval on
    real nuclear-condensate data: the resulting mask was large amoeba-shaped
    multi-object blobs, not discrete puncta). This corrupted more than the
    visual mask -- a fused blob's equivalent diameter is a huge outlier, so it
    skewed object_size_px's median directly. 3-class Otsu, keeping only the brightest class, discards that
    middle "ambiguous texture" class instead of forcing one hard 2-class
    boundary through it -- confirmed by Meet on real low-contrast data to
    recover the individual puncta the top-hat already resolved, where 2-class
    Otsu produced fused blobs. On the clean/high-SNR case the two methods
    agree exactly (the top class IS the whole foreground population when
    there's no middle texture class to split off), so this is not a
    trade-off against the already-working case. Falls back to plain 2-class
    Otsu only if there aren't enough distinct top-hat values to support 3
    classes (a robustness net for a degenerate/near-empty top-hat response,
    not a user-facing choice).

    # TODO(optimize-on-real-data): the top-hat radius and min_area cutoff are
    # still first-pass defaults. Validate/tune against a real cellular- and
    # in-vitro-fluorescence batch (see Meet's STEP 2 diagnostic).

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

    # Normalise to [0, 1] for a stable threshold.
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
        positive = tophat[tophat > 0]
        if positive.size < 10:
            raise ValueError('too few positive top-hat pixels for multi-Otsu')
        # 3 classes: background / ambiguous texture / real objects -- keep
        # only the brightest class, discarding the middle one instead of
        # forcing a single 2-class cut through it (see WHY MULTI-OTSU above).
        thr = sk.filters.threshold_multiotsu(positive, classes=3)[-1]
    except Exception:  # broad-ok: robustness_net -- too few/uniform values for 3 classes
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
    ball_radius = max(1, math.ceil(1.5 * (object_size / 2.0)))
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
