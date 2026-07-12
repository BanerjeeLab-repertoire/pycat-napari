"""
Segmentation scale advisor — does this method need upscaling, and by how much?

Why this exists
---------------
Upscaling is routinely applied as though it were a general image-improvement step.
It is not. **Upscaling adds no information** (see
``pycat.toolbox.partial_volume_tools``): it is a deterministic interpolation of the
pixels you already had, it cannot resolve anything the optics did not capture, and
measuring intensities on it corrupts your statistics.

It has exactly **one** legitimate purpose: to fix a **scale mismatch between your
objects and your algorithm.**

That means the question "should I upscale?" has no universal answer — it depends
entirely on which segmentation method you are about to run:

* **Cellpose** is a CNN with a *learned* scale prior. Its convolutional features were
  trained on objects of a particular size (~30 px across), so an 8-px cell is not
  merely small — it is outside the range the network's features can read. Upscaling
  re-presents identical information at a scale the model was trained on. This is a
  property of the **algorithm**, not of the data.

* **Otsu and other threshold methods have no spatial scale at all.** They operate on
  the intensity *histogram*. Upscaling cannot help them, and measurably **hurts**:
  interpolation inserts intermediate-intensity pixels at every boundary, blurring the
  bimodality that Otsu depends on. (Measured on synthetic discs with known ground
  truth: Dice fell from 0.876 to 0.759 at a 2-px object radius, and from 0.994 to
  0.930 at 4 px, going from 1x to 4x.)

* **Blob/LoG detection is scale-adaptive by parameter** — you tell it the sigma range.
  If your objects are small, the correct fix is to *set a smaller sigma*, not to
  inflate the image.

* **Random-forest / pixel classifiers** depend on the scales of their feature filters.
  Upscaling shifts every object relative to a fixed filter bank, which usually makes
  things worse, not better.

So the advice is method-specific, and for most methods it is "don't."
"""

from __future__ import annotations

import numpy as np


# Object diameter (px) each method wants to see, and whether upscaling can help it.
#
#   target_px : the object diameter the method performs best at (None = scale-free)
#   scale_prior : True if the method has a FIXED internal scale it cannot adapt to.
#                 Only these can be helped by upscaling.
#   note : what to do instead, when upscaling is not the answer.
METHOD_SCALE_REQUIREMENTS = {
    'cellpose': dict(
        target_px=30.0,
        scale_prior=True,
        note="Cellpose's features were trained on objects roughly 30 px across. "
             "Objects much smaller than that are outside the range the network can "
             "read, and upscaling genuinely helps.",
    ),
    'stardist': dict(
        target_px=25.0,
        scale_prior=True,
        note="StarDist also carries a learned scale prior, though it is somewhat "
             "more tolerant than Cellpose.",
    ),
    'otsu': dict(
        target_px=None,
        scale_prior=False,
        note="Otsu thresholds the intensity HISTOGRAM and has no spatial scale. "
             "Upscaling cannot help it and measurably hurts — interpolation adds "
             "intermediate-intensity pixels at every boundary, blurring the "
             "bimodality Otsu relies on. If segmentation is poor, the problem is "
             "contrast or background, not resolution.",
    ),
    'local_threshold': dict(
        target_px=None,
        scale_prior=False,
        note="Local thresholding adapts via its window size. If objects are small, "
             "shrink the window — do not inflate the image.",
    ),
    'blob_log': dict(
        target_px=None,
        scale_prior=False,
        note="Blob/LoG detection is scale-adaptive by parameter: you specify the "
             "sigma range. For small objects, set a SMALLER min/max sigma. "
             "Upscaling just moves the same information to different coordinates.",
    ),
    'random_forest': dict(
        target_px=None,
        scale_prior=False,
        note="A pixel classifier depends on the scales of its feature filters. "
             "Upscaling shifts objects relative to a fixed filter bank and usually "
             "degrades the result. Retrain with representative annotations instead.",
    ),
    'watershed': dict(
        target_px=None,
        scale_prior=False,
        note="Watershed is driven by the gradient/distance transform, not by an "
             "absolute scale. Upscaling adds interpolated gradients, which can "
             "create spurious basins.",
    ),
}


def _equiv_diameter(prop):
    """Equivalent diameter, tolerant of the skimage rename
    (equivalent_diameter -> equivalent_diameter_area in 0.26)."""
    for attr in ('equivalent_diameter_area', 'equivalent_diameter'):
        try:
            v = getattr(prop, attr)
            if v is not None:
                return float(v)
        except Exception:
            continue
    return float('nan')


def measure_object_diameter_px(image, mask=None, method='auto'):
    """Estimate a representative object diameter (px) from an image.

    Used to answer "how big are my objects, in the units the algorithm cares about?"
    If a mask is supplied, the diameter is measured directly from it (reliable). If
    not, a top-hat + Otsu estimate is used, which assumes discrete bright objects on
    a thresholdable background — valid for fluorescence, NOT for brightfield.

    Returns the median equivalent diameter in pixels, or NaN if it cannot be
    estimated.
    """
    import skimage as sk
    from scipy import ndimage as ndi

    if mask is not None:
        m = np.asarray(mask)
        lab = m if m.max() > 1 else sk.measure.label(m > 0)
        props = sk.measure.regionprops(lab.astype(int))
        d = [_equiv_diameter(p) for p in props if p.area >= 4]
        d = [x for x in d if np.isfinite(x)]
        return float(np.median(d)) if d else float('nan')

    img = np.asarray(image, dtype=np.float32)
    if img.ndim == 3:
        img = img[img.shape[0] // 2]
    try:
        rng = float(img.max() - img.min())
        if rng <= 0:
            return float('nan')
        norm = (img - img.min()) / rng
        # Top-hat isolates small bright objects from a slowly-varying background.
        th = sk.morphology.white_tophat(norm, sk.morphology.disk(15))
        t = sk.filters.threshold_otsu(th)
        fg = th > t
        fg = ndi.binary_opening(fg)
        lab = sk.measure.label(fg)
        props = sk.measure.regionprops(lab)
        d = [_equiv_diameter(p) for p in props if p.area >= 4]
        d = [x for x in d if np.isfinite(x)]
        return float(np.median(d)) if d else float('nan')
    except Exception:
        return float('nan')


def advise_upscaling(object_diameter_px, method='cellpose', max_factor=8):
    """Should this method be given an upscaled image, and by what factor?

    Parameters
    ----------
    object_diameter_px : the size of YOUR objects, in native pixels.
    method : one of METHOD_SCALE_REQUIREMENTS (cellpose, otsu, blob_log, ...).
    max_factor : cap on the recommended factor (upscaling is expensive: a 4x upscale
        is 16x the pixels).

    Returns
    -------
    dict with:
      needed : bool                   — does upscaling help this method here?
      factor : int                    — recommended factor (1 = don't upscale)
      reason : str                    — a plain-English explanation
      level  : 'ok'|'suggest'|'warn'  — for UI colouring
      target_px : the diameter this method wants (None if scale-free)
    """
    key = str(method).lower().strip()
    req = METHOD_SCALE_REQUIREMENTS.get(key)
    if req is None:
        return dict(needed=False, factor=1, level='ok', target_px=None,
                    reason=f"No scale requirement is recorded for '{method}'. "
                           "Upscaling is not recommended by default — it adds no "
                           "information and should only be used to fix a known "
                           "scale mismatch.")

    # Methods with no scale prior: upscaling is never the right answer.
    if not req['scale_prior']:
        return dict(needed=False, factor=1, level='ok', target_px=None,
                    reason=f"**Upscaling is not needed and is likely harmful.** "
                           f"{req['note']}")

    d = float(object_diameter_px) if object_diameter_px else 0.0
    target = float(req['target_px'])

    if not np.isfinite(d) or d <= 0:
        return dict(needed=False, factor=1, level='warn', target_px=target,
                    reason=f"Object size is unknown, so I cannot tell whether "
                           f"upscaling is needed. Measure an object (draw a line) "
                           f"or set the diameter, then ask again. "
                           f"({method} performs best around {target:.0f} px.)")

    if d >= target:
        return dict(needed=False, factor=1, level='ok', target_px=target,
                    reason=f"**Not needed.** Your objects are ~{d:.0f} px across, "
                           f"already at or above {method}'s ~{target:.0f} px target. "
                           f"Upscaling would only cost time and memory (and must "
                           f"never be used for intensity measurement).")

    # Scale mismatch: recommend the factor that reaches the target.
    raw = target / d
    factor = int(min(max(2, int(np.ceil(raw))), int(max_factor)))
    reached = d * factor
    level = 'suggest'
    extra = ""
    if raw > max_factor:
        level = 'warn'
        extra = (f" Even at {max_factor}x your objects would only reach "
                 f"~{d*max_factor:.0f} px, still short of {target:.0f} px — they may "
                 f"simply be too small for {method} on this data. Consider a method "
                 f"suited to small objects (blob/LoG detection with a small sigma, or "
                 f"thresholding) rather than forcing a CNN to see them.")
    return dict(needed=True, factor=factor, level=level, target_px=target,
                reason=f"**Upscale {factor}x.** Your objects are ~{d:.0f} px across, "
                       f"below {method}'s ~{target:.0f} px target; at {factor}x they "
                       f"would be ~{reached:.0f} px. {req['note']}{extra}"
                       f"\n\nRemember: upscale to SEGMENT, then measure intensities "
                       f"on the ORIGINAL image (Partial-Volume Measurement).")
