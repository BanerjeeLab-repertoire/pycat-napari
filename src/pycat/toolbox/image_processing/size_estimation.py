"""Automatic object-size estimation - split out of image_processing_tools (1.6.248).

estimate_object_size_px is the headless/batch top-hat + Otsu estimator (median equivalent diameter ->
ball_radius) that feeds downstream segmentation; auto_object_size_valid + AUTO_OBJECT_SIZE_VALID_WORKFLOWS
gate WHICH workflows it is valid for; estimate_object_size_px_brightfield is the experimental edge-based
variant. Moved VERBATIM - no threshold or measurement change; pinned BEFORE the move by
test_image_processing_size_characterization (exact object_size_px / ball_radius / n_objects on a fixed
synthetic scene). Self-contained science, no napari/Qt.
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
      2. Otsu threshold on the top-hat response → foreground objects.
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
    ball_radius = max(1, math.ceil(1.5 * (object_size / 2.0)))
    result = {'object_size_px': object_size,
              'ball_radius': ball_radius,
              'n_objects': int(diams.size)}
    if return_diagnostics:
        result['diagnostics'] = {'tophat': tophat, 'fg': fg, 'diameters': diams}
    return result


def estimate_bimodal_object_sizes(image, workflow=None, min_area_px=4,
                                  min_objects_per_cluster=3,
                                  return_diagnostics=False):
    """Large/small size ROUTER for the preprocessing cascade
    (``pre_process_image``'s ``cascade_large_small=True`` path).

    ONE call to ``estimate_object_size_px``, at native resolution with its
    default (small) ``tophat_radius`` -- not a second, separately-scaled
    probe. This replaces an earlier two-pass design (a second call at a
    deliberately large ``tophat_radius``, on a DOWNSAMPLED copy of the image
    so the big structuring element stayed affordable) that was retired after
    real-world testing: dense puncta populations, under that pass's
    decimation, produced a smooth CONTINUUM of apparent object sizes from
    progressive merging (nearby puncta blending together at coarse
    resolution) rather than two discrete populations -- confirmed on real
    data reporting r_large=90 from a raw diameter histogram that climbed
    smoothly from 7.9 to 198.4px with no gap anywhere to split at, and
    reproduced synthetically with a dense puncta population confined to
    nucleus-sized clusters.

    Why a single native-resolution pass can work at all: white top-hat's
    blind spot is objects LARGER than its structuring element (the opening
    reconstructs them almost exactly, so their top-hat response is near
    zero -- see ``estimate_object_size_px``), not "small" as an absolute
    label. The default ``tophat_radius`` is ``clip(min(image_dims)//50, 3,
    25)`` -- for a realistically large image that caps at 25, which stays
    visible to objects up to roughly 50px diameter. Real condensate images
    where the "large" population isn't dramatically bigger than the small
    one (confirmed on real data: small_scale=25px) can have BOTH populations
    within reach of one call, with no downsampling and therefore no
    decimation-driven merging at all.

    NOT a bimodality test. Earlier versions tried to first PROVE the image
    genuinely has two separate populations (Otsu-vs-bootstrap-null
    significance testing, then a GMM-based version of the same idea) before
    deriving r_small/r_large from the resulting split. Both were retired:
    every statistically rigorous version, when pointed at a real image with
    a visually-obvious handful of larger condensates among many smaller
    ones, found that tail statistically indistinguishable from the natural
    upper tail of one continuous (right-skewed / lognormal-shaped)
    population -- correctly, by the standards of that test, but useless in
    practice, since it meant a real second population went undetected and
    the necklace-hollowing failure this whole cascade feature exists to
    prevent went unfixed.

    This version doesn't try to decide whether the image IS bimodal at all.
    It sorts the diameters, and unconditionally takes the MEAN of the lower
    half as the "small" scale. If the image only has one real population,
    this stays close to the population's overall mean (a mild, low-risk
    scale); if there's a genuine wide spread or a real second population, it
    tracks the small end specifically. This deliberately always produces a
    two-scale cascade (given enough objects) rather than gating on
    statistical significance -- the trade-off, made explicitly here rather
    than left implicit, is recall over precision: no image is treated as a
    single clean population anymore, in exchange for never missing a real
    one.

    r_large is derived differently: the MEAN diameter of only the objects
    ABOVE the 90th percentile of the full diameter distribution (the same
    ball_radius formula is then applied to that mean, exactly as for
    r_small -- see ``estimate_object_size_px``). Earlier versions derived
    r_large from the mean (then mean+0.5*std) of the upper HALF of the
    sorted diameters, but the upper half is mostly mid-sized objects --
    averaging over it dilutes r_large toward the population's overall scale
    rather than the genuinely large tail, and a ball_radius sized to that
    diluted average still undersizes the largest condensates: top-hat with a
    too-small structuring element hollows a large condensate into a
    "necklace" (bright rim, dim or empty core) instead of a filled disk --
    observed on real data at the mean-of-upper-half r_large, and still
    observed at mean-of-upper-half+0.5*std. Restricting the mean to the top
    decile targets the actual large-object subpopulation directly instead of
    trying to nudge a blended average toward it.

    Returns ``None`` when there are too few objects to trust a split (either
    half has fewer than ``min_objects_per_cluster`` objects), or the
    resulting r_large does not exceed r_small (a degenerate case, e.g. every
    detected object is nearly identical in size) -- the caller should fall
    back to the existing single-pass preprocessing unchanged.

    Returns ``{'r_large', 'r_small', 'n_large', 'n_small'}`` otherwise: the
    standard ball_radius formula (see ``estimate_object_size_px``) applied
    to r_small's lower-half mean diameter and r_large's above-90th-percentile
    mean diameter.

    Parameters
    ----------
    image : 2D array (a single fluorescence frame/channel).
    workflow : optional workflow id for the validity guard (see
        ``auto_object_size_valid``).
    min_area_px : passed through to ``estimate_object_size_px``.
    min_objects_per_cluster : the lower half (r_small) and the above-90th-
        percentile tail (r_large) must each have at least this many objects,
        or the split is not trusted.
    return_diagnostics : if True, also return the full diameter array (for
        a diagnostic figure / audit log).

    Returns
    -------
    dict or None. See above.
    """
    if workflow is not None and not auto_object_size_valid(workflow):
        raise ValueError(
            f"Automatic object-size estimation is not valid for workflow "
            f"'{workflow}'. Valid: {sorted(AUTO_OBJECT_SIZE_VALID_WORKFLOWS)}.")

    est = estimate_object_size_px(image, min_area_px=min_area_px, return_diagnostics=True)
    diams = (est.get('diagnostics') or {}).get('diameters')
    if diams is None or diams.size < 2 * min_objects_per_cluster:
        return None

    d = np.sort(diams)
    mid = d.size // 2
    lower = d[:mid]
    if lower.size < min_objects_per_cluster:
        return None

    # r_large comes from only the objects ABOVE the 90th percentile of the
    # full diameter distribution, not the upper half -- the upper half is
    # mostly mid-sized objects, and averaging over it (even with a +std
    # bias, tried and still insufficient) undersizes the genuinely large
    # tail, leaving big condensates "necklaced" (hollow rim, no filled core)
    # by a too-small top-hat structuring element. Restricting to the top
    # decile targets that tail directly.
    p90 = np.percentile(d, 90)
    upper = d[d > p90]
    if upper.size < min_objects_per_cluster:
        return None

    small_diam = float(np.mean(lower))
    large_diam = float(np.mean(upper))
    r_small = max(1, math.ceil(1.5 * (small_diam / 2.0)))
    r_large = max(1, math.ceil(1.5 * (large_diam / 2.0)))
    if r_large <= r_small:
        return None

    result = {'r_large': int(r_large), 'r_small': int(r_small),
              'n_large': int(upper.size), 'n_small': int(lower.size)}
    if return_diagnostics:
        result['diagnostics'] = {'diameters': diams}
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
