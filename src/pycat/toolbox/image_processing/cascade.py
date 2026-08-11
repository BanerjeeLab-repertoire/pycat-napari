"""Generic scale-aware large/small cascade orchestrator (1.6.461).

Shared by pre_process_image (preprocessing.py, Step 1) and the enhanced
background-removal step (background.py: rb_gaussian_bg_removal_with_edge_
enhancement / soft_foreground_suppression, Step 2) so BOTH stages of the
"Pre-process Image" button apply the same large/small split. Fixing a large
condensate's collapsed ("necklace") interior in Step 1 and then re-running
Step 2 with a single ball_radius on top would just reintroduce the same
collapsed-interior problem one step later (Step 2's own LoG/rolling-ball
machinery is exactly as scale-sensitive as Step 1's) -- so both stages need to
run the SAME router + cascade, not just the first one.

See ``pre_process_image``'s ``cascade_large_small`` docstring (preprocessing.py)
for the full algorithm description; this module holds the mechanism, callers
supply the single-scale processing function to run at each pass.

Three correctness fixes are baked into this module and the router it depends
on (``estimate_bimodal_object_sizes``, size_estimation.py) after being found
on real data -- do not regress any while touching this file:

  1. A real large condensate is often internally TEXTURED (nucleation puncta,
     partial phase separation), not one uniformly bright disk. A single global
     threshold on it fragments into several/many separate bright cores that
     are each individually too small to pass any size gate -- confirmed: a
     14-bead ring thresholds into 14 separate ~96px pieces, ALL rejected, so
     the "large object" is invisibly dropped and its fragments re-appear as
     spurious small objects in the small pass. Fixed in ``detect_large_objects``
     by a morphological closing at the large object's own scale BEFORE the
     size filter.
  2. The router's large-scale top-hat probe is prohibitively slow at full
     resolution (disk(60) on a 2048x2048 image alone measured ~65s; disk(100)
     did not finish in 5 minutes) and MUST run on a downsampled copy. That
     downsample must be plain strided decimation, not a block-mean/anti-
     aliased resize -- a block-mean BLURS nearby pixels together and was
     confirmed to merge a dense field of 1225 separate small puncta into one
     apparent large blob (a false positive for "large"). Fixed in
     ``estimate_bimodal_object_sizes``.
  3. r_large/r_small come back from the router UNCAPPED -- unlike the single-
     scale ``ball_radius`` path, which ``run_pre_process_image`` explicitly
     caps against the image's own size before ever calling ``pre_process_image``
     ("to prevent a MemoryError from an oversized rolling-ball structuring
     element"). ``detect_large_objects``'s closing, ``erase_and_backfill``'s
     dilation margins, and ``feather_composite``'s feather all size a disk
     footprint directly off r_large, so an uncapped value can reproduce that
     exact cost class the existing cap exists to prevent. Fixed in
     ``run_scale_aware_cascade`` via ``_cap_radius_for_image`` -- the ONE place
     both Step 1 and Step 2, GUI and batch, funnel through.
"""
from __future__ import annotations

import math
import numpy as np
import skimage as sk
import scipy.ndimage as ndi
from pycat.utils.general_utils import dtype_conversion_func

# Sentinel distinguishing "caller didn't pass a split at all" (do this
# module's own estimate_bimodal_object_sizes call, the original behaviour)
# from "caller explicitly passed a split" (even None, meaning "Step 1 already
# decided this image isn't bimodal -- don't re-decide, just match it"). A
# plain `None` default can't make that distinction on its own.
_NOT_PROVIDED = object()


def detect_large_objects(large_pass_img, r_large, min_area_factor=0.4):
    """Threshold a LARGE-pass output to find filled large-object regions.

    A real large condensate is often internally TEXTURED (nucleation puncta,
    partial phase separation) rather than one uniformly bright disk -- a
    single global Otsu threshold on it does not come back as one filled blob,
    it fragments into several/many separate bright cores with dim gaps
    between them (confirmed: a synthetic 14-bead ring at the correct r_large
    thresholds into 14 separate ~96px pieces, each individually below any
    sane large-object area floor, so a plain threshold + fill_holes finds NO
    large object at all -- the fragments are individually too small to ever
    pass a size gate, no matter where that gate is set). This is the same
    "arcs instead of one ring" failure ``_bridge_fragmented_rims`` (fz.py) was
    built to fix at the segmentation stage, showing up one stage earlier here;
    a morphological closing at the large object's OWN scale bridges the
    fragments back into one blob before ``binary_fill_holes`` recovers the
    enclosed interior. Unlike ``_bridge_fragmented_rims``, no fragment-count
    cap is applied here -- there is no cell-level ball_radius to calibrate
    one against, a bridged ring can legitimately be built from dozens of
    beads, and the ``min_area_factor`` gate below (a large object's real
    footprint, not a fragment count) is what stops a handful of nearby small
    puncta from being accidentally accepted as "large": their closed+filled
    area essentially never reaches a real large object's footprint.

    ``min_area_factor`` rejects components smaller than
    ``min_area_factor * pi * object_radius**2`` -- residual texture picked up
    at the large scale that isn't actually a large object. ``object_radius``
    is recovered from ``r_large`` (a ball_radius, i.e.
    ``ceil(1.5 * object_radius)`` -- see ``estimate_object_size_px``), not
    ``r_large`` itself, or this cutoff is ~2.25x too large and rejects real
    large objects.
    """
    arr = dtype_conversion_func(large_pass_img, 'float32')
    mx = float(arr.max())
    if mx <= 0:
        return np.zeros(arr.shape, dtype=bool)
    norm = arr / mx
    nz = norm[norm > 1e-4]
    if nz.size < 10:
        return np.zeros(arr.shape, dtype=bool)
    try:
        thr = sk.filters.threshold_otsu(nz)
    except Exception:
        thr = float(np.percentile(nz, 70))
    mask = norm > thr
    close_radius = max(3, int(round(r_large)))
    mask = ndi.binary_closing(mask, sk.morphology.disk(close_radius))
    mask = ndi.binary_fill_holes(mask)
    lbl, n = ndi.label(mask)
    if n == 0:
        return mask
    object_radius = r_large / 1.5
    min_area = min_area_factor * math.pi * (object_radius ** 2)
    sizes = ndi.sum(np.ones_like(lbl), lbl, range(1, n + 1))
    keep = np.where(sizes >= min_area)[0] + 1
    return np.isin(lbl, keep)


def detect_objects_generic(pass_img):
    """Threshold a pass output into a foreground mask. Used only to build the
    safety-net dedup in ``run_scale_aware_cascade`` -- NOT a substitute for
    the real per-cell FZ segmentation downstream, which still runs on
    whatever image the cascade returns."""
    arr = dtype_conversion_func(pass_img, 'float32')
    mx = float(arr.max())
    if mx <= 0:
        return np.zeros(arr.shape, dtype=bool)
    norm = arr / mx
    nz = norm[norm > 1e-4]
    if nz.size < 10:
        return np.zeros(arr.shape, dtype=bool)
    try:
        thr = sk.filters.threshold_otsu(nz)
    except Exception:
        thr = float(np.percentile(nz, 80))
    return norm > thr


def erase_and_backfill(image, mask, r_large):
    """Remove the large objects (``mask``) from ``image`` BEFORE the small
    pass runs, so their leftover rim/halo doesn't get re-detected as a small
    object and so their huge dynamic range doesn't skew the small pass's own
    normalisation/CLAHE/Otsu steps.

    - Dilates ``mask`` beyond the object so the rim/halo is erased too.
    - Backfills with the LOCAL background: a biharmonic-inpainted base (smooth
      local estimate, not a global constant) plus Gaussian noise matched to the
      LOCAL background variance sampled from an annulus just outside each
      erased region (per connected component, since illumination isn't flat) --
      not literal salt-and-pepper impulse noise, which would trip the next LoG
      pass the same way a hard edge would.
    - Feathers the fill boundary with a distance-based alpha ramp so there is
      no hard intensity step at the erase boundary (a hard edge is a LoG
      magnet -- exactly the artifact this step exists to avoid creating).
    """
    arr = dtype_conversion_func(image, 'float32')
    if not mask.any():
        return arr

    margin = max(3, int(math.ceil(r_large * 0.75)))
    feather = max(2, int(math.ceil(r_large * 0.5)))
    dilated = ndi.binary_dilation(mask, sk.morphology.disk(margin))

    filled = sk.restoration.inpaint_biharmonic(arr, dilated).astype(np.float32)

    annulus = ndi.binary_dilation(dilated, sk.morphology.disk(feather)) & ~dilated
    rng = np.random.default_rng(0)
    noisy = filled.copy()
    lbl, n = ndi.label(dilated)
    for i in range(1, n + 1):
        comp = lbl == i
        ys, xs = np.where(ndi.binary_dilation(comp, sk.morphology.disk(feather)))
        if ys.size == 0:
            continue
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        local_annulus = annulus[y0:y1, x0:x1]
        local_vals = arr[y0:y1, x0:x1][local_annulus]
        if local_vals.size >= 10:
            local_std = float(local_vals.std())
            if local_std > 0:
                noisy[comp] = filled[comp] + rng.normal(0, local_std, size=int(comp.sum())).astype(np.float32)
    noisy = np.clip(noisy, 0, None)

    # Feather: alpha=1 through the whole erased (dilated) region, ramping to 0
    # over `feather` px beyond it -- no hard edge at the erase boundary.
    dist_out = ndi.distance_transform_edt(~dilated)
    alpha = np.clip(1.0 - dist_out / feather, 0.0, 1.0)
    alpha = np.where(dilated, 1.0, alpha).astype(np.float32)
    return (alpha * noisy + (1.0 - alpha) * arr).astype(np.float32)


def dedup_iom(large_mask, small_mask, iom_threshold=0.3):
    """Safety-net de-duplication between the large-pass and small-pass masks.

    Uses INTERSECTION-OVER-MINIMUM (intersection / smaller object's area), not
    plain IoU: a small remnant sitting inside a big object has near-zero IoU
    against it but high intersection-over-minimum, which is exactly the
    duplicate this needs to catch. On a conflict the large object wins (the
    erase step already removed the original signal before the small pass ran,
    so a surviving small-mask component that highly overlaps a large object is
    a leftover artifact, not a genuine second object).

    Returns (deduped_small_mask, dropped_regions) where ``dropped_regions`` is
    the boolean mask of small-mask pixels that were dropped, for logging and so
    the caller can also erase them from the small-pass IMAGE before compositing.
    """
    lbl_l, n_l = ndi.label(large_mask)
    lbl_s, n_s = ndi.label(small_mask)
    if n_l == 0 or n_s == 0:
        return small_mask, np.zeros(small_mask.shape, dtype=bool)

    keep_small = np.ones(n_s + 1, dtype=bool)
    keep_small[0] = False
    for i in range(1, n_l + 1):
        comp_l = lbl_l == i
        area_l = int(comp_l.sum())
        overlapping = np.unique(lbl_s[comp_l])
        overlapping = overlapping[overlapping != 0]
        for j in overlapping:
            j = int(j)
            if not keep_small[j]:
                continue
            comp_s = lbl_s == j
            area_s = int(comp_s.sum())
            inter = int(np.logical_and(comp_l, comp_s).sum())
            iom = inter / max(1, min(area_l, area_s))
            if iom >= iom_threshold:
                keep_small[j] = False

    deduped = np.isin(lbl_s, np.where(keep_small)[0])
    dropped_regions = small_mask & ~deduped
    return deduped, dropped_regions


def feather_composite(small_pass_img, large_pass_img, large_mask, r_large):
    """Union the two passes into the single image this function returns: the
    small pass everywhere, with the large pass's filled objects feathered back
    in over ``large_mask`` so downstream consumers see large condensates as
    solid regions rather than the necklace a single-scale pass would have
    produced."""
    small_f = dtype_conversion_func(small_pass_img, 'float32')
    large_f = dtype_conversion_func(large_pass_img, 'float32')
    if not large_mask.any():
        return small_f
    feather = max(2, int(math.ceil(r_large * 0.5)))
    dist_out = ndi.distance_transform_edt(~large_mask)
    alpha = np.clip(1.0 - dist_out / feather, 0.0, 1.0)
    alpha = np.where(large_mask, 1.0, alpha).astype(np.float32)
    return (alpha * large_f + (1.0 - alpha) * small_f).astype(np.float32)


def tighten_noise_gates(log_p, con_p, min_area, strength,
                        log_p_boost=25.0, con_p_boost=15.0, min_area_scale=2.5,
                        strength_floor=0.97):
    """Stricter foreground-suppression noise gates for a cascade's SMALL pass.

    The small pass's LoG kernel scales with r_small (``sigma = 0.27 *
    r_small``): a smaller r_small is correctly more sensitive to genuinely
    tiny real puncta, but that same fine-grained sensitivity also resolves
    pixel-level noise and faint background texture that a coarser, single
    "compromise" ball_radius (what a non-bimodal image, or the pre-cascade
    single-scale pipeline, would have used) smooths right over -- confirmed
    directly on a plain noisy background: measurable bright speckle appeared
    at ball_radius=5 that did NOT appear at ball_radius=12 on the identical
    background. ``soft_foreground_suppression``'s ``log_p``/``con_p``
    percentile gates (and its ``min_area`` floor) are tuned against that
    coarser "normal" scale, so the same cutoff admits more of that noise at
    the small pass's finer scale.

    Four levers, all for that ONE pass only -- the large pass and the
    not-bimodal single-pass path (see ``run_scale_aware_cascade``) are
    untouched and must keep today's exact sensitivity:

    - Higher ``log_p``/``con_p`` percentiles reject more of the blob-shape/
      contrast response as "not real".
    - A larger ``min_area`` knocks down more single/few-pixel specks.
    - ``strength`` is floored near 1.0 (default 0.97). This is the SAFEST of
      the four: ``weight_eff = (1 - strength) + strength * weight``, so a
      pixel the gates score as real (weight~1, e.g. a genuine punctum, which
      cleanly passes blob-shape AND contrast AND intensity) keeps
      weight_eff~1 regardless of strength -- only pixels ALREADY scored as
      low-realness are affected. At the tuned default ``strength=0.8``, a
      pixel scored as pure noise (weight=0) is still shown at 20% of its
      brightness, which CLAHE's earlier contrast stretch can leave faintly
      but visibly non-zero; flooring strength removes that visible floor
      instead of just shifting which pixels get classified as noise.

    Returns
    -------
    (log_p, con_p, min_area, strength) : the tightened values, clamped to
    sane maxima (log_p <= 40, con_p <= 20) so a pathological input can't
    suppress everything.
    """
    return (min(40.0, log_p + log_p_boost),
            min(20.0, con_p + con_p_boost),
            max(1, int(round(min_area * min_area_scale))),
            max(strength, strength_floor))


def _cap_radius_for_image(radius, image_shape, max_fraction=0.15, floor=4):
    """Cap a ball_radius against the image's own size.

    Mirrors the protection ``run_pre_process_image`` already applies to its
    single ``ball_radius`` argument before calling ``pre_process_image``
    (there: 5% of the smaller dimension, specifically "to prevent a
    MemoryError from an oversized rolling-ball structuring element") --
    but the cascade's ``r_large``/``r_small`` are derived INTERNALLY by the
    router and never pass through that existing cap.
    ``detect_large_objects``'s closing, ``erase_and_backfill``'s dilation
    margins, and ``feather_composite``'s feather all size a disk structuring
    element directly off ``r_large``, so an uncapped value can reproduce the
    exact multi-minute / memory-blowup cost class already measured and fixed
    for the router's own top-hat probe (see size_estimation.py) -- e.g. a
    pathological probe-scale artifact, or simply a genuinely huge condensate
    on a modest-sized image. A larger fraction (15% vs the single-pass path's
    5%) is used here since large condensates are, by construction, the reason
    this cascade exists, and a real large object legitimately can be a bigger
    fraction of the frame than a "typical" ball_radius would be.
    """
    max_radius = max(floor, int(min(image_shape[-2:]) * max_fraction))
    return min(int(radius), max_radius)


def run_scale_aware_cascade(image, ball_radius, single_pass_fn, log_label='cascade',
                            precomputed_split=_NOT_PROVIDED):
    """ROUTER + two-pass cascade, generic over the single-scale processing
    function to run at each pass.

    Parameters
    ----------
    image : numpy.ndarray
        The input image (any dtype).
    ball_radius : int
        The ball_radius the CALLER would have used on the single-pass path --
        only used to log the not-bimodal fallback; the cascade path derives
        its own r_large/r_small from the image.
    single_pass_fn : callable(image, ball_radius, small_pass=False) -> numpy.ndarray
        Runs ONE scale's worth of single-pass processing (e.g.
        ``pre_process_image``'s core, or ``soft_foreground_suppression``'s)
        and returns an image. Called at r_large and r_small when bimodal, or
        once at ``ball_radius`` (with ``small_pass`` omitted -- the not-bimodal
        path must stay byte-identical to ``cascade_large_small=False``) when
        not. ``small_pass=True`` is passed on every call actually using
        r_small, so a suppression-aware implementation can tighten its own
        noise gates for that pass specifically (see ``tighten_noise_gates``);
        implementations without such a gate can just ignore the kwarg.
    log_label : str
        Prefix for the ``[PyCAT] {log_label}: ...`` audit-log lines so batch
        output can tell which stage (preprocess / background removal) ran the
        cascade for a given image.
    precomputed_split : dict or None, optional
        Bypasses this function's own ``estimate_bimodal_object_sizes(image)``
        call and uses this result instead -- a dict (the router's normal
        return shape) to force a specific split, or ``None`` to force the
        not-bimodal single-pass path. Left at the ``_NOT_PROVIDED`` sentinel
        (the default) this behaves exactly as before: estimate from ``image``
        directly.

        Exists because Step 1 (``pre_process_image``) and Step 2
        (``soft_foreground_suppression``) used to each independently call
        ``estimate_bimodal_object_sizes`` on their OWN input -- but Step 2's
        input is Step 1's OUTPUT, which has already been through LoG blob
        enhancement. LoG sharpens diffuse intensity peaks into much tighter
        blob responses, so top-hat+Otsu run on that transformed image
        systematically measures SMALLER apparent object sizes than the same
        detector finds on the original raw data -- confirmed on real data:
        Step 1 computed r_small=6 from the raw image, Step 2's independent
        re-estimate on Step 1's own output came back r_small=3, roughly
        HALF, which is small enough (LoG sigma~0.8px) to noticeably degrade
        the small pass's own noise suppression on top of the sensitivity
        ``tighten_noise_gates`` already exists to compensate for. Both
        callers now compute the split ONCE, from the original raw image, and
        Step 2 reuses Step 1's result via this parameter instead of
        re-deriving a different (and worse) one from already-processed data.

    Returns
    -------
    numpy.ndarray in the ORIGINAL ``image``'s dtype.
    """
    input_dtype = str(np.asarray(image).dtype)
    if precomputed_split is not _NOT_PROVIDED:
        bimodal = precomputed_split
    else:
        from pycat.toolbox.image_processing.size_estimation import estimate_bimodal_object_sizes
        try:
            bimodal = estimate_bimodal_object_sizes(image)
        except Exception as e:  # broad-ok: optional_probe -- router failure falls back to the safe single-pass path
            print(f"[PyCAT] {log_label}: bimodality check failed ({e}) -- using single-pass.")
            bimodal = None

    if bimodal is None:
        print(f"[PyCAT] {log_label}: object-size distribution is not bimodal "
              f"-- single-pass (ball_radius={ball_radius}).")
        return single_pass_fn(image, ball_radius)

    r_large, r_small = bimodal['r_large'], bimodal['r_small']
    _shape = np.asarray(image).shape
    _capped_large = _cap_radius_for_image(r_large, _shape)
    _capped_small = _cap_radius_for_image(r_small, _shape)
    if _capped_large != r_large or _capped_small != r_small:
        print(f"[PyCAT] {log_label}: capped radii for image shape {_shape} "
              f"(r_large {r_large}->{_capped_large}, r_small {r_small}->{_capped_small}) "
              "to bound the cascade's own morphology cost.")
    r_large, r_small = _capped_large, _capped_small
    if r_large <= r_small:
        print(f"[PyCAT] {log_label}: capping collapsed the large/small split for this "
              f"image size -- falling back to single-pass (ball_radius={ball_radius}).")
        return single_pass_fn(image, ball_radius)
    print(f"[PyCAT] {log_label}: bimodal population detected -- cascade "
          f"(r_large={r_large} [n={bimodal['n_large']}], "
          f"r_small={r_small} [n={bimodal['n_small']}]).")

    img = dtype_conversion_func(image, 'float32')

    # a. Large pass: process + detect at the large scale.
    large_pass_img = single_pass_fn(img, r_large)
    large_mask = detect_large_objects(large_pass_img, r_large)
    if not large_mask.any():
        print(f"[PyCAT] {log_label}: no large objects survived detection on the "
              "large pass -- falling back to a single small-scale pass.")
        result = single_pass_fn(img, r_small, small_pass=True)
        return dtype_conversion_func(result, output_bit_depth=input_dtype)

    # b. Erase the large objects (+ halo) from the ORIGINAL image and backfill
    # locally before the small pass ever sees it.
    erased_img = erase_and_backfill(img, large_mask, r_large)

    # c. Small pass on the modified (large-object-free) image. ``small_pass=True``
    # tells a suppression-aware single_pass_fn to tighten its own noise gates --
    # see ``tighten_noise_gates`` docstring: the small pass's LoG kernel scales
    # with r_small, so a smaller r_small is correctly more sensitive to genuinely
    # tiny real puncta, but that same fine-grained sensitivity also resolves
    # pixel-level noise and faint background texture a coarser, single
    # compromise ball_radius would have smoothed over -- confirmed directly: a
    # plain noisy background produced measurable bright speckle at
    # ball_radius=5 that the SAME background did not produce at ball_radius=12.
    # single_pass_fn implementations that have no such noise gate (e.g. the
    # destructive rolling-ball + edge-enhancement path) just ignore the flag.
    small_pass_img = single_pass_fn(erased_img, r_small, small_pass=True)

    # 3. MERGE: de-duplicate via intersection-over-minimum (safety net -- the
    # erase step above should already prevent most conflicts), drop any
    # surviving small-pass duplicate from the small-pass IMAGE too so it
    # doesn't leak into the composite, then union the two passes.
    small_mask = detect_objects_generic(small_pass_img)
    _, dropped_regions = dedup_iom(large_mask, small_mask)
    n_dropped = int(ndi.label(dropped_regions)[1])
    if dropped_regions.any():
        small_pass_img = sk.restoration.inpaint_biharmonic(
            dtype_conversion_func(small_pass_img, 'float32'),
            ndi.binary_dilation(dropped_regions, sk.morphology.disk(2))
        ).astype(np.float32)

    composite = feather_composite(small_pass_img, large_pass_img, large_mask, r_large)
    n_large = int(ndi.label(large_mask)[1])
    print(f"[PyCAT] {log_label}: merged output (large objects kept={n_large}, "
          f"small-pass duplicates dropped={n_dropped}).")

    return dtype_conversion_func(composite, output_bit_depth=input_dtype)
