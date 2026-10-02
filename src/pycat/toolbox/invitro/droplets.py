"""In-vitro droplets: one object per droplet, each edge placed by that droplet's own intensity.

Why this exists
---------------
A whole-field threshold (Otsu, multi-Otsu) gives every droplet the same cut. A bright droplet's
out-of-focus glow sits above that cut, so it reaches the next droplet and the two come back as one
object; a dim droplet sits close to it, so its outline is ragged. On the Abhradeep RG5 fields
(10 images, 100% and 80% laser) half of the multi-Otsu (3 classes) objects of 30 px or more are
non-convex (solidity < 0.9) and 92 hold two droplets with a clear intensity dip between them.

This follows the large-condensate approach of the 2D cellular workflow: find each object first,
then decide its boundary from that object's own peak and local background, and never let two
objects touch.

1. **Detect** droplets with a Laplacian-of-Gaussian blob detector; a centre that falls inside a
   larger droplet is dropped.
2. **Partition** the foreground (smoothed image above background + 3 noise SDs) between the
   droplets by a slightly compact watershed on intensity. Plain intensity flooding lets a bright
   droplet spill across the flat top of a dim droplet pressed against it (no dip to stop it), and
   the dim droplet vanishes; a small cost per pixel of distance keeps that top with its own seed.
3. **Merge flanks.** A region with no intensity peak of its own (its brightest pixel lies on the
   border with a brighter neighbour, within the noise or the neighbour's texture) is the side of
   that neighbour, so it joins it — unless it stands above the neighbour's own fall-off (a dim
   droplet pressed against a bright one) or joining would add a lobe (a contact narrower than the
   region is thick: a budding droplet). Either way it stays its own object.
4. **Boundary** of each droplet at half its height above the local background ring, its height
   read away from any neighbour's skirt; strips under 5 px wide are opened off.
5. **Second pass** for faint droplets that no detection reached: an isolated intensity peak whose
   half-height contour closes on itself (does not climb into a neighbouring droplet's glow).
6. Objects are kept apart by a one-pixel gap; thin streaks (scan-line artefacts) are dropped.

Measured on those fields against multi-Otsu (3 classes), with no annotation: objects 1208 → 1415,
non-convex objects 550 → 15, objects holding two intensity peaks with a dip between them 92 → 3,
strong intensity maxima left uncovered 143 → 14, droplets matched between the 100% and 80% laser
images of the same field 432 → 547 (80/100 area ratio 1.00 → 1.02). About 4 s per 1024 x 1024 field.
"""
from __future__ import annotations

import numpy as np
import scipy.ndimage as ndi
from skimage.feature import blob_log
from skimage.measure import regionprops
from skimage.morphology import h_maxima
from skimage.segmentation import watershed

from pycat.toolbox.segmentation.boundary_refit import keep_objects_apart
from pycat.toolbox.segmentation.intensity import robust_cell_background

SMOOTH_SIGMA = 1.5          # px; the image the boundaries and peaks are read from
SEED_THRESHOLD = 0.04       # blob_log response on the [0, 1]-scaled image
SEED_SUPPRESS = 1.25        # a centre within 1.25 radii of a larger droplet's centre is that droplet
FOREGROUND_K = 3.0          # foreground: smoothed image above background + k noise SDs
BOUNDARY_LEVEL = 0.5        # edge at half the droplet's height above its local background
RING_PX = 6                 # local background ring width
FAINT_K = 6.0               # second pass: peak height above background, in noise SDs
MIN_MINOR_AXIS = 4.0        # px; thinner objects are noise or streaks
MAX_ELONGATION = 0.35       # minor/major below this (not cut by the image edge) = scan-line streak
LOBE_RATIO = 1.0            # a flank's contact must be at least as wide as the flank is thick
WATERSHED_COMPACTNESS = 0.001   # intensity (of the [0, 1] range) per px of distance from the seed
NEIGHBOUR_SKIRT_PX = 4      # px; how far a neighbour's blur reaches into a region
OWN_BODY_FRACTION = 0.25   # a region this far above its neighbour's fall-off (of its own height) is a droplet

_DISK2 = np.hypot(*np.mgrid[-2:3, -2:3]) <= 2


def _scaled(image):
    a = np.asarray(image, dtype=np.float64)
    lo, hi = float(a.min()), float(np.percentile(a, 99.99))
    return np.clip((a - lo) / (hi - lo), 0, None) if hi > lo else np.zeros_like(a)


def _droplet_seeds(img, threshold):
    blobs = blob_log(ndi.gaussian_filter(img, 1.0), min_sigma=2, max_sigma=30, num_sigma=15,
                     threshold=threshold, overlap=0.3)
    kept = []
    for y, x, s in blobs[np.argsort(-blobs[:, 2])] if len(blobs) else []:
        if not any(np.hypot(y - ky, x - kx) < SEED_SUPPRESS * ks * np.sqrt(2) for ky, kx, ks in kept):
            kept.append((y, x, s))
    markers = np.zeros(img.shape, np.int32)
    for i, (y, x, s) in enumerate(kept, start=1):
        r = max(1.0, 0.35 * s * np.sqrt(2))
        y0, x0 = max(0, int(y - r)), max(0, int(x - r))
        sl = markers[y0:int(y + r) + 1, x0:int(x + r) + 1]
        yy, xx = np.ogrid[y0:y0 + sl.shape[0], x0:x0 + sl.shape[1]]
        sl[((yy - y) ** 2 + (xx - x) ** 2 <= r * r) & (sl == 0)] = i
    return markers


def _view(mask_slices, shape, pad):
    return tuple(slice(max(0, s.start - pad), min(d, s.stop + pad)) for s, d in zip(mask_slices, shape))


def _half_height(region, sm, others, base, level, ring=RING_PX):
    """The largest connected part of ``region`` above ``level`` of the way from the local background
    (25th percentile of a ring outside it, other objects excluded) to its peak (98th percentile, read
    away from any neighbour so a bright neighbour's skirt is not taken for this droplet's top)."""
    away = region & (ndi.distance_transform_edt(~(others & ~region)) > NEIGHBOUR_SKIRT_PX)
    peak = float(np.percentile(sm[away if away.sum() >= 20 else region], 98))
    dist = ndi.distance_transform_edt(~region)
    around = (dist <= ring) & ~region & ~others
    if around.sum() < 8:
        around = (dist <= ring) & ~region
    bg = float(np.percentile(sm[around], 25)) if around.sum() >= 8 else base
    obj = region & (sm >= bg + level * (peak - bg))
    opened = ndi.binary_opening(obj, structure=_DISK2)     # drop strips of a neighbour's skirt
    obj = opened if opened.any() else obj
    lab, n = ndi.label(obj)
    if n == 0:
        return None
    return ndi.binary_fill_holes(lab == np.bincount(lab[obj]).argmax())


def _has_own_body(r, sm, a, b, base, level):
    """Is ``a`` brighter than ``b``'s own fall-off predicts? ``b``'s profile is read against the
    distance from its half-height edge, on every side but ``a``'s. A flank or a glow follows that
    profile; a dim droplet pressed against a bright one sits well above it."""
    b_obj = _half_height((r == b) | (r == a), sm, r > 0, base, level)    # with a, so a notch is not 'outside'
    if b_obj is None:
        return False
    d = np.rint(ndi.distance_transform_edt(~b_obj) - ndi.distance_transform_edt(b_obj)).astype(int)
    ref = ((r == b) | (r == 0)) & ~(r == a)
    lo = d.min()
    idx = (d - lo).ravel()
    n = np.bincount(idx[ref.ravel()], minlength=idx.max() + 1)
    if n.sum() == 0:
        return False
    sums = np.bincount(idx[ref.ravel()], weights=sm.ravel()[ref.ravel()], minlength=idx.max() + 1)
    have = n > 0
    prof = np.interp(np.arange(len(n)), np.flatnonzero(have), sums[have] / n[have])
    in_a = (r == a)
    excess = sm[in_a] - prof[d[in_a] - lo]
    spread = float(np.std(sm[r == b] - prof[d[r == b] - lo]))
    peak_a = float(sm[in_a].max())
    return float(np.percentile(excess, 75)) > max(3 * spread, OWN_BODY_FRACTION * (peak_a - base))


def _is_flank(regions, sm, a, b, base, level):
    """Joining ``a`` to ``b`` must not add a lobe: in the joined half-height object, ``a``'s part must
    touch ``b``'s along at least its own thickness (a rim flank does; a budding droplet has a neck)."""
    both = (regions == a) | (regions == b)
    ys, xs = np.nonzero(both)
    v = (slice(max(0, ys.min() - 2 * RING_PX), ys.max() + 2 * RING_PX + 1),
         slice(max(0, xs.min() - 2 * RING_PX), xs.max() + 2 * RING_PX + 1))
    r = regions[v]
    if _has_own_body(r, sm[v], a, b, base, level):
        return False
    joined = _half_height(both[v], sm[v], r > 0, base, level)
    if joined is None:
        return True
    part_a, part_b = joined & (r == a), joined & (r == b)
    if not part_a.any():
        return True
    thick = 2 * ndi.distance_transform_edt(np.pad(part_a, 1))[1:-1, 1:-1].max()
    return (part_a & ndi.binary_dilation(part_b)).sum() >= LOBE_RATIO * thick


def _merge_flanks(regions, sm, texture, noise, base, level, rounds=4):
    regions = regions.copy()
    for _ in range(rounds):
        changed = False
        peaks = ndi.maximum(sm, regions, index=np.arange(regions.max() + 1))
        for idx, sl in enumerate(ndi.find_objects(regions), start=1):
            if sl is None:
                continue
            v = _view(sl, regions.shape, 1)
            r, s = regions[v], sm[v]
            a = r == idx
            local = max(2 * noise, 2 * float(texture[v][a].std()))
            rim = (a & ~ndi.binary_erosion(a)).sum()
            best, target = -np.inf, 0
            for nb in set(np.unique(r[ndi.binary_dilation(a) & ~a])) - {0, idx}:
                if peaks[nb] <= peaks[idx]:
                    continue
                contact = a & ndi.binary_dilation(r == nb)
                edge = s[contact].max()
                # no peak of its own: within the noise, or within the texture along a long contact
                flank = edge >= peaks[idx] - 2 * noise or (
                    edge >= peaks[idx] - local and contact.sum() >= 0.3 * rim)
                if flank and edge > best and _is_flank(regions, sm, idx, nb, base, level):
                    best, target = edge, nb
            if target:
                regions[regions == idx] = target
                peaks[target] = max(peaks[target], peaks[idx])
                changed = True
        if not changed:
            break
    return regions


def _round_enough(mask, min_area):
    if mask.sum() < min_area:
        return False
    return regionprops(mask.astype(np.uint8))[0].axis_minor_length >= MIN_MINOR_AXIS


def segment_droplets_by_peak(image, *, min_area=12, level=BOUNDARY_LEVEL, seed_threshold=SEED_THRESHOLD,
                             second_pass=True, compactness=WATERSHED_COMPACTNESS):
    """Label in-vitro droplets one per object, each bounded at half its own height (see module doc).

    ``image`` is the raw fluorescence field (any scale; it is rescaled internally). Returns an int32
    label image with every pair of droplets separated by at least one background pixel.
    """
    img = _scaled(image)
    sm = ndi.gaussian_filter(img, SMOOTH_SIGMA)
    base, noise = robust_cell_background(sm.ravel())
    fg = sm > base + FOREGROUND_K * noise
    markers = _droplet_seeds(img, seed_threshold)
    regions = watershed(-sm, markers, mask=fg | (markers > 0), compactness=compactness)
    regions = _merge_flanks(regions, sm, sm - ndi.gaussian_filter(img, 4.0), noise, base, level)

    out = np.zeros(img.shape, np.int32)
    for idx, sl in enumerate(ndi.find_objects(regions), start=1):
        if sl is None:
            continue
        v = _view(sl, img.shape, 2 * RING_PX)
        keep = _half_height(regions[v] == idx, sm[v], regions[v] > 0, base, level)
        if keep is not None and _round_enough(keep, min_area):
            o = out[v]
            o[keep & (o == 0)] = idx

    if second_pass:
        _add_faint_droplets(out, img, base, noise, level, min_area)

    out = keep_objects_apart(out)
    lab, _ = ndi.label(out > 0)
    H, W = lab.shape
    for p in regionprops(lab):
        y0, x0, y1, x1 = p.bbox
        cut = y0 == 0 or x0 == 0 or y1 == H or x1 == W
        streak = not cut and p.axis_minor_length < MAX_ELONGATION * p.axis_major_length
        if p.area < min_area or streak:
            lab[p.slice][p.image] = 0
    lab, _ = ndi.label(lab > 0)
    return lab.astype(np.int32)


def _add_faint_droplets(out, img, base, noise, level, min_area, window=30):
    """Isolated peaks no detection reached, kept only if their half-height contour closes on itself
    away from every existing droplet (glow between bright droplets climbs into them instead)."""
    sm2 = ndi.gaussian_filter(img, 2.0)
    blocked = ndi.binary_dilation(out > 0, iterations=3)
    cand = h_maxima(sm2, FAINT_K * noise) & ~blocked & (sm2 > base + FAINT_K * noise)
    nxt = int(out.max()) + 1
    for y, x in np.argwhere(cand):
        if out[y, x] or blocked[y, x]:
            continue
        v = (slice(max(0, y - window), y + window + 1), slice(max(0, x - window), x + window + 1))
        sub, blk = sm2[v], blocked[v]
        if not (~blk).any():
            continue
        bg = float(np.percentile(sub[~blk], 25))
        peak = float(sm2[y, x])
        if peak - bg < FAINT_K * noise:
            continue
        comp, _ = ndi.label(sub >= bg + level * (peak - bg))
        obj = comp == comp[y - v[0].start, x - v[1].start]
        edge = np.zeros_like(obj)
        edge[[0, -1], :] = True
        edge[:, [0, -1]] = True
        if (obj & (blk | edge)).any():
            continue
        obj = ndi.binary_fill_holes(obj)
        if _round_enough(obj, min_area) and regionprops(obj.astype(np.uint8))[0].solidity >= 0.9:
            o = out[v]
            o[obj & (o == 0)] = nxt
            nxt += 1
            blocked[v] |= ndi.binary_dilation(obj, iterations=3)
