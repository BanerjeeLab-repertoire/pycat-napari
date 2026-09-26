"""**Which object scales are actually in this image** — measured, not assumed.

Why this exists
---------------
Every scale-setting number in the condensate path descends from ``ball_radius``,
and ``ball_radius`` descends from **one line the user drew across one object**
(``DataClass.update_sizes``: ``ball_radius = ceil(1.5 * object_radius)``). That
single number then sets the rolling-ball radius, the Gaussian-division sigma
(``2 * ball_radius``), the white-top-hat footprint, the CLAHE tile
(``4 * ball_radius``) and the Niblack/Sauvola window — i.e. **the whole chain is
one band-pass, centred on one hand-measured scale.**

That is fine when every object in the field is that size. It fails, and fails
silently, when they are not. Measured on synthetic fields with constructed
ground truth (``benchmarks/condensate_scale.py``), a field mixing 3-8 px and
11-20 px objects, segmented at the ``ball_radius`` a user would derive from the
dominant small population:

    true radius   detected    mean coverage
      2-5 px         95 %         0.94
      5-8 px         99 %         0.95
      8-11 px         0 %         0.06
     11-14 px        18 %         0.06
     14-17 px        12 %         0.08
     17-21 px         0 %         0.00

The large objects are not mis-sized. **They are not detected at all** — the
band-pass suppresses a flat interior that is large relative to its scale, and
what survives is a rim too thin and too broken to recover. Nothing in the
pipeline notices; the user sees a plausible count of small puncta and no warning.

The same fields, segmented at a larger ``ball_radius``, score IoU 0.83 against
0.63 — so the parameter alone is worth ~20 IoU points, and the better value is
*larger* than the one a careful user measuring a typical object would draw.
That asymmetry is the design rule this module encodes: **a too-small
``ball_radius`` deletes large objects outright, while a too-large one only
blunts small ones.**

What this module does
---------------------
`object_scale_spectrum` measures the area-weighted distribution of object radii
directly from the image, by scale-space maxima of the normalised
Laplacian-of-Gaussian. `recommend_working_scales` turns that into the
``ball_radius`` values to actually segment at, and — this is the part that
matters to a user — **says so out loud** when the image disagrees with the line
they drew, instead of quietly returning a small count.

This module only MEASURES and RECOMMENDS. It does not segment, and it does not
change any threshold on its own.
"""
from __future__ import annotations

import math

import numpy as np
import scipy.ndimage as ndi
import skimage as sk


# A second, larger pass is worth its cost only when the large population is real.
# Both conditions must hold, and both are measured, not assumed:
#   * the upper end of the size distribution is at least this many times the
#     DRAWN radius (below ~1.8x a single band-pass still covers both), and
#   * the object area at or above that multiple of the drawn radius is at least
#     this fraction (below it, one or two large objects are not worth doubling
#     runtime).
MULTISCALE_RADIUS_RATIO = 1.8
MULTISCALE_MIN_AREA_FRACTION = 0.05

# A pixel counts as belonging to an object only if it is this many robust sigmas
# above the cell's OWN background. Without a floor, nucleoplasm texture is
# measured and the spectrum reports the noise rather than the objects.
SPECTRUM_Z_FLOOR = 3.0

# Below this many object pixels there is no population to describe, only a few
# specks, and any percentile of them is noise.
SPECTRUM_MIN_FOREGROUND_PX = 20

# An "object" larger than this fraction of the CELL's own equivalent radius is
# not a condensate — it is the cell. Capping here is what stops the measurement
# from reporting the nucleus itself as the dominant object scale.
SPECTRUM_MAX_CELL_FRACTION = 0.45

# Upper bound on the number of openings the granulometry performs. Cost is one
# opening per radius, and the radius ceiling scales with the cell, so without this
# a whole-frame ROI would run hundreds of steps to resolve a part of the
# distribution that is empty anyway.
SPECTRUM_MAX_STEPS = 40


def object_scale_spectrum(image, cell_mask, z_floor=SPECTRUM_Z_FLOOR,
                          smooth_sigma=1.0,
                          min_foreground_px=SPECTRUM_MIN_FOREGROUND_PX,
                          max_cell_fraction=SPECTRUM_MAX_CELL_FRACTION):
    """The area-weighted distribution of object radii inside ``cell_mask``.

    Method: a **granulometry**. Threshold to the pixels genuinely above the cell's
    own background, then measure how much of that area survives a morphological
    opening by a disc of radius ``r``, for increasing ``r``. The area that
    disappears between ``r`` and ``r+1`` is, by construction, the area belonging
    to structures about that thick — which is the size distribution, in the one
    form that does not need the objects to be separated first.

    That last property is why an opening is used rather than the more obvious
    "label the objects and measure each one". Condensates touch. Labelling merges
    a pair of 5 px puncta into one 10 px object and reports a size that is in the
    image nowhere; an opening by disc(6) removes both, because neither is 12 px
    thick anywhere, regardless of whether they are one connected component.

    And why not a scale-space (Laplacian-of-Gaussian) probe, the usual answer to
    "what sizes are here": measured on a real cell it reports **the cell**. A
    nucleus is an excellent blob and is far larger than anything inside it, so it
    wins at every large scale, and suppressing that requires already knowing the
    answer. The opening never sees the cell at all — the nucleoplasm is background
    as far as the foreground threshold is concerned.

    Parameters
    ----------
    image : numpy.ndarray
        The RAW image, before any enhancement. Measuring the spectrum on an
        already band-passed image would report the band-pass, not the objects.
    cell_mask : numpy.ndarray
        Where to look, and what "background" means.

    Returns
    -------
    dict or None
        ``None`` when there is nothing to measure — an empty mask, or no pixel
        clearing the background floor, which is what a genuinely empty cell looks
        like. Otherwise:

        ``r_dominant``          area-weighted MEDIAN radius: the size most of the
                                object area actually is
        ``r_p90``               area-weighted 90th percentile: the upper end
        ``r_max``               the largest radius any area survived
        ``radii``, ``area``     the distribution itself, so a caller can ask what
                                fraction of the object area sits above ANY radius
                                (`area_fraction_above`) rather than only above the
                                one this function happened to pick
        ``foreground_px``       how much object area was measured at all
    """
    img = sk.util.img_as_float32(np.asarray(image))
    cell = np.asarray(cell_mask, dtype=bool)
    if not cell.any():
        return None

    values = img[cell]
    base = float(np.median(values))
    mad = 1.4826 * float(np.median(np.abs(values - base)))
    sigma = mad if mad > 0 else (float(np.std(values)) or 1e-9)
    smoothed = ndi.gaussian_filter(img, smooth_sigma)
    foreground = cell & (smoothed > base + z_floor * sigma)
    total_area = int(foreground.sum())
    if total_area < min_foreground_px:
        return None

    # An object cannot plausibly be a large fraction of its own cell; anything
    # that big is the cell, a segmentation error, or a saturated field, and
    # letting it into the distribution would set every downstream scale wrong.
    cell_radius = math.sqrt(float(cell.sum()) / math.pi)
    radius_ceiling = max(2.0, max_cell_fraction * cell_radius)

    # Opening by disc(r) computed from two distance transforms rather than a
    # structuring-element convolution: the erosion is {distance-into-foreground
    # >= r} and the dilation back out is {distance-to-that-core <= r}. Cost is
    # independent of r, which matters because r runs to tens of pixels — the same
    # reason `fz._bridge_fragmented_rims` uses `isotropic_closing`.
    inside = ndi.distance_transform_edt(foreground)
    # One opening per radius, so the number of radii is the cost. Unit steps while
    # they are affordable, coarser above that: the ceiling scales with the cell, and
    # on a whole-frame "cell" (no cell mask supplied) it would otherwise run to
    # hundreds of steps to describe a distribution that is already flat up there.
    n_steps = min(int(math.floor(radius_ceiling)), SPECTRUM_MAX_STEPS)
    radii = np.linspace(1.0, math.floor(radius_ceiling), max(2, n_steps))
    surviving = []
    for r in radii:
        core = inside >= r
        if not core.any():
            surviving.append(0.0)
            continue
        opened = (ndi.distance_transform_edt(~core) <= r) & foreground
        surviving.append(float(opened.sum()))
    surviving = np.asarray(surviving, dtype=float)
    if surviving[0] <= 0:
        return None

    # Area belonging to each size class, then the cumulative distribution over it.
    per_class = np.diff(np.concatenate([surviving, [0.0]])) * -1.0
    per_class = np.clip(per_class, 0.0, None)
    if per_class.sum() <= 0:
        return None
    cdf = np.cumsum(per_class) / per_class.sum()
    return dict(
        r_dominant=float(np.interp(0.50, cdf, radii)),
        r_p90=float(np.interp(0.90, cdf, radii)),
        r_max=float(radii[surviving > 0].max()) if (surviving > 0).any() else 1.0,
        radii=radii,
        area=per_class,
        foreground_px=total_area,
    )


def area_fraction_above(spectrum, radius):
    """What fraction of the measured object area is in structures at least this thick.

    The question the multi-scale decision actually needs, and it has to be asked
    about a SPECIFIC radius — the one the user's `ball_radius` corresponds to —
    rather than about the distribution's own centre. Asking it relative to the
    dominant size gets the answer backwards in the very case that matters: in a
    field of a few large condensates and many small puncta, the large objects
    carry most of the AREA, so they ARE the dominant size, and a
    "how much area is far above the dominant size?" test then reports ~0 and
    concludes the field is single-scale.
    """
    if spectrum is None:
        return 0.0
    total = float(np.sum(spectrum['area']))
    if total <= 0:
        return 0.0
    return float(np.sum(spectrum['area'][spectrum['radii'] >= float(radius)]) / total)


def _ball_radius_for(object_radius_px):
    """The ``ball_radius`` the rest of the pipeline expects for objects of this
    radius — the same ``ceil(1.5 * r)`` relation ``DataClass.update_sizes``
    applies to the hand-drawn line, so a recommended scale means the same thing
    everywhere as a measured one."""
    return max(2, int(math.ceil(1.5 * float(object_radius_px))))


def recommend_working_scales(spectrum, measured_object_radius,
                             allow_multiscale=True,
                             radius_ratio=MULTISCALE_RADIUS_RATIO,
                             min_area_fraction=MULTISCALE_MIN_AREA_FRACTION):
    """The ``ball_radius`` value(s) to segment at, and WHY — in plain English.

    **The user's own scale is never overridden.** ``ball_radii[0]`` is always the
    one derived from the line they drew, so this can only ADD objects, never
    remove one, and a result stays reproducible from the annotation. That is a
    deliberate constraint: an automatic scale that silently replaced a measured
    one would make two runs of the same image on the same annotation disagree.

    A SECOND, larger scale is added only when both measured conditions hold:

    * the upper end of the size distribution is at least ``radius_ratio`` times
      the drawn scale — below about 1.8x a single band-pass still covers both
      populations, so a second pass would only cost runtime; and
    * the object area at or above that multiple of the DRAWN scale is at least
      ``min_area_fraction`` — one unusually large object is not a population.

    The second condition is measured against the user's own scale, not against the
    distribution's centre, because those give opposite answers in the case that
    matters most — see `area_fraction_above`.

    Returns
    -------
    dict
        ``ball_radii``   ball_radius values, the caller's first, length 1 or 2
        ``multiscale``   bool — is a second pass being recommended?
        ``reason``       a sentence naming the numbers behind the decision
        ``disagreement`` bool — does the image disagree with the drawn line
                         enough that the user should be told?
    """
    measured_r = float(measured_object_radius)
    primary = _ball_radius_for(measured_r)

    if spectrum is None:
        return dict(ball_radii=[primary], multiscale=False, disagreement=False,
                    reason="No object scale could be measured (nothing in this cell "
                           "rises above its own background), so the drawn line was "
                           "used unchanged.")

    r_dominant = spectrum['r_dominant']
    r_p90 = spectrum['r_p90']
    large_fraction = area_fraction_above(spectrum, radius_ratio * measured_r)
    ball_radii = [primary]
    multiscale = bool(allow_multiscale
                      and r_p90 >= radius_ratio * measured_r
                      and large_fraction >= min_area_fraction)
    if multiscale:
        ball_radii.append(_ball_radius_for(r_p90))

    disagreement = bool(r_dominant >= 1.5 * measured_r)
    reason = (f"object scale measured from the image — typical radius "
              f"{r_dominant:.1f} px, upper end {r_p90:.1f} px, with "
              f"{100 * large_fraction:.0f}% of the object area in objects at least "
              f"{radius_ratio:.1f}x the drawn scale; the drawn line said "
              f"{measured_r:.1f} px. ")
    if multiscale:
        reason += (f"Large objects are present alongside small ones, so a SECOND "
                   f"pass runs at ball_radius {ball_radii[1]} and its objects are "
                   f"added to the usual pass at {ball_radii[0]} — a single "
                   f"band-pass at {ball_radii[0]} does not merely mis-size objects "
                   f"this much larger than itself, it misses them entirely.")
    else:
        reason += "The field is single-scale, so one pass is used."
    if disagreement:
        reason += (" NOTE: the objects in this cell are substantially larger than "
                   "the line drawn across one of them — re-check the Object "
                   "Diameter annotation if the result looks wrong.")
    return dict(ball_radii=ball_radii, multiscale=multiscale,
                disagreement=disagreement, reason=reason)
