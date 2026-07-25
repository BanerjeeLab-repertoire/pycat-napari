"""VPT detection **artifact rejection** — hot-pixel masking and ring-merge de-duplication.

`build_hot_pixel_mask` (per-camera hot-pixel map) and `dedup_detections_ring_merge` (merge double-counted detections in a ring) are rejection/merge steps around the core detection path. Moved VERBATIM out of `detection.py`, which re-exports them and calls them internally.
"""
from __future__ import annotations

import numpy as np

def build_hot_pixel_mask(bead_stack, cv_max=0.12, tstd_max=8.0,
                         local_excess_min=8.0, median_size=5,
                         max_frames=None):
    """Identify fixed-location sensor hot/dead pixels from a stack's TEMPORAL
    statistics (detection_variant='hot_pixel_reject').

    Physics. A sensor hot/dead pixel is a FIXED (r, c) whose value is set by the
    detector, not the scene, so across the movie it is essentially CONSTANT in
    time — high (or anomalous) temporal MEAN but very LOW temporal VARIANCE. A
    real bead location has HIGH temporal variance because the bead moves through /
    jitters (it comes and goes). Verified on Gable's fluorescence VPT data
    (2026-07): hot pixels have temporal std ~3-4 (cv≈0.05) while bead locations
    have temporal std ~40-50 (cv≈0.3-0.5) — a wide, clean gap. This temporal
    signature is SCENE-INDEPENDENT, so unlike a per-frame spike test it (a)
    catches hot pixels sitting DOWN NEAR THE NOISE FLOOR (this camera does this in
    some modes), and (b) will not falsely reject a genuinely stable bead, which
    still jitters in time. It is computed once over the stack, not per frame.

    A pixel is flagged hot when ALL hold:
      * it stands out from its local neighbourhood in temporal MEAN
        (local-median-subtracted excess ≥ ``local_excess_min``), AND
      * it is temporally FLAT — coefficient of variation
        (temporal_std / temporal_mean) ≤ ``cv_max`` OR absolute temporal
        std ≤ ``tstd_max``.

    Parameters
    ----------
    bead_stack : (T, H, W) stack (lazy or array). Streamed via iter_frames so the
        whole movie is never held in memory.
    cv_max : max coefficient of variation for a flat (hot) pixel.
    tstd_max : max absolute temporal std for a flat (hot) pixel (catches
        near-noise-floor hot pixels whose mean is low so cv is less reliable).
    local_excess_min : min temporal-mean excess over the local median background
        to be considered anomalous at all (avoids flagging ordinary background).
    median_size : neighbourhood size for the local background estimate.
    max_frames : cap frames used for the statistics (None = all). A few hundred
        frames are plenty to estimate the temporal signature.

    Returns
    -------
    (H, W) boolean mask, True at hot/dead sensor pixels.

    STATUS (2026-07): mechanism validated CORRECT and SAFE on Gable's fluorescence
    VPT data — the temporal signature cleanly separates hot pixels (temporal std
    ~3-4) from beads (temporal std ~40-50), and wired as detection_variant=
    'hot_pixel_reject' it drops hot pixels via a harsher NCC gate WITHOUT rejecting
    real beads (every confirmed bead survived, including one adjacent to a hot
    pixel). HOWEVER on that specific data it is nearly a no-op (~18 hot pixels
    found but blob_log barely fires on them, so ~1 detection removed) — the beads
    are clean and detection is already good there. It earns its place on data where
    a camera/mode DOES turn hot/dead pixels into recurring false detections (e.g.
    the brightfield near-noise-floor hot pixels this camera can produce). Kept and
    wired, low-risk (baseline untouched); expect little effect on clean
    fluorescence bead movies.
    """
    from pycat.file_io.stack_access import iter_frames
    # Streaming mean/variance (Welford) so we never hold the whole stack.
    mean = None
    M2 = None
    n = 0
    for t, frame in iter_frames(bead_stack):
        f = np.asarray(frame, dtype=np.float64)
        f = np.squeeze(f)
        if f.ndim != 2:
            continue
        if mean is None:
            mean = np.zeros_like(f)
            M2 = np.zeros_like(f)
        n += 1
        delta = f - mean
        mean += delta / n
        M2 += delta * (f - mean)
        if max_frames is not None and n >= int(max_frames):
            break
    if mean is None or n < 5:
        # Not enough frames to estimate — flag nothing.
        return np.zeros((1, 1), dtype=bool) if mean is None else \
            np.zeros_like(mean, dtype=bool)
    tvar = M2 / max(n - 1, 1)
    tstd = np.sqrt(np.maximum(tvar, 0.0))
    tmean = mean

    from scipy.ndimage import median_filter
    local_bg = median_filter(tmean, size=int(median_size))
    excess = tmean - local_bg
    cv = tstd / np.maximum(tmean, 1.0)

    anomalous = excess >= float(local_excess_min)
    flat = (cv <= float(cv_max)) | (tstd <= float(tstd_max))
    hot = anomalous & flat
    return hot


def dedup_detections_ring_merge(coords, frame, sigmas=None,
                                k_sigma=2.5, ring_dim_ratio=0.6,
                                base_radius_px=None):
    """Ring-merge deduplication (detection_variant='ring_merge').

    ⚠ STATUS: BUILT BUT NOT YET VALIDATED — NEEDS DATA WITH RESOLVED AIRY RINGS.
    ---------------------------------------------------------------------------
    A/B comparison against baseline on Gable's 2026-07 bead data (100x/~1.2 NA,
    0.67 µm/px, 200 nm beads) showed this variant is a near no-op there: the
    beads are well-separated (median nearest-neighbour ~17.5 px, only ~4% within
    5 px) and blob_log already returns ~one detection per bead, so there are
    essentially no ring fragments to merge (it changed ~2 of ~2000 detections).
    On THAT data the real detection-quality lever is hot-pixel rejection, not
    ring-merge. This function is kept because the logic is sound and there is
    almost certainly a use case — data with genuinely RESOLVED Airy rings that
    fire as separate blobs (denser sampling, lower NA relative to bead size, or a
    lower detection threshold that picks up ring shoulders). It is deliberately
    NOT exposed in the VPT widget; wire it in and validate against such a dataset
    (center+ring must collapse to ONE bead, two bright peaks must stay TWO)
    before trusting/surfacing it. Reach it programmatically via
    detect_beads_stack(..., detection_variant='ring_merge').

    Improves on ``dedup_detections`` for large, non-diffraction-limited Airy-disk
    beads, where blob_log fires on both the bright CENTRE and the dim Airy RING /
    multi-scale shoulders of a single bead. Two corrections over the baseline:

    1. **Self-scaling merge radius.** The merge radius is ``k_sigma × sigma`` of
       the detected blob (not a fixed pixel count), so it tracks the imaged
       footprint and stays correct under low NA / undersampling / astigmatism.
       At 0.67 µm/px a 200 nm bead is sub-pixel, so keying off physical µm is
       wrong — the detected blob sigma is the robust length scale.

    2. **Merge only the DIM companion into the BRIGHT centre; keep two bright
       peaks as two beads.** A ring fragment is always the DIM companion of a
       bright peak (never itself bright+compact). So a neighbour is merged into a
       kept centre only if it is DIM relative to that centre
       (``neighbour_intensity ≤ ring_dim_ratio × centre_intensity``). If a nearby
       detection is comparably BRIGHT, it is a second real bead and is kept —
       trajectory linking resolves two genuinely-separate beads far better than
       detection can, and collapsing them (as the baseline does) destroys a real
       track. This is the key behavioural difference from ``dedup_detections``.

    Parameters
    ----------
    coords : list/array of (y, x).
    frame  : the image, used for local intensity of each detection.
    sigmas : per-detection blob sigma (from blob_log column 3). If None, falls
        back to ``base_radius_px`` (behaves like a fixed-radius dedup that still
        respects the bright-vs-dim rule).
    k_sigma : merge radius = k_sigma × sigma (default 2.5).
    ring_dim_ratio : a neighbour is a mergeable ring fragment only if its local
        intensity ≤ this fraction of the centre's (default 0.6). Higher = merges
        more aggressively; lower = keeps more separate detections.
    base_radius_px : fallback merge radius when sigmas is None.

    Returns
    -------
    Filtered list of (y, x) — bright bead centres, with dim ring fragments folded
    in and genuinely-separate bright beads preserved.
    """
    if coords is None or len(coords) == 0:
        return coords
    from scipy.spatial import cKDTree
    pts = np.asarray([(float(y), float(x)) for (y, x) in coords], dtype=float)
    raw = np.asarray(frame, dtype=np.float32)
    raw = np.squeeze(raw)
    if raw.ndim != 2:
        return coords
    H, W = raw.shape

    def local_intensity(y, x, r=2):
        yi, xi = int(round(y)), int(round(x))
        y0, y1 = max(0, yi - r), min(H, yi + r + 1)
        x0, x1 = max(0, xi - r), min(W, xi + r + 1)
        if y1 <= y0 or x1 <= x0:
            return -np.inf
        return float(raw[y0:y1, x0:x1].max())

    inten = np.array([local_intensity(y, x) for (y, x) in pts])
    # Per-detection merge radius (sigma-scaled, or fixed fallback).
    if sigmas is not None and len(sigmas) == len(pts):
        radii = np.maximum(1.0, float(k_sigma) * np.asarray(sigmas, dtype=float))
    elif base_radius_px:
        radii = np.full(len(pts), float(base_radius_px))
    else:
        # No sigma and no fallback → nothing principled to merge on; keep all.
        return [tuple(p) for p in pts]

    tree = cKDTree(pts)
    order = np.argsort(-inten)          # brightest first
    used = np.zeros(len(pts), dtype=bool)
    kept = []
    for idx in order:
        if used[idx]:
            continue
        kept.append(idx)
        centre_I = inten[idx]
        # Query within this centre's radius; fold in only DIM neighbours.
        neighbours = tree.query_ball_point(pts[idx], r=float(radii[idx]))
        for n in neighbours:
            if n == idx or used[n]:
                continue
            # Merge only if the neighbour is a DIM ring fragment of this centre.
            # A comparably-bright neighbour is a second real bead → leave it for
            # its own turn in the brightness-ordered loop (kept separately).
            if inten[n] <= ring_dim_ratio * centre_I:
                used[n] = True
        used[idx] = True
    kept.sort()
    return [tuple(pts[i]) for i in kept]
