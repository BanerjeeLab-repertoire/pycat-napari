"""VPT **bead detection** — the whole detection stack, split out of vpt_tools (1.6.238).

LoG blob detection (CPU + GPU), Airy/template PSF scoring, hot-pixel masking, ring-merge dedup, bead
classification, the detect_beads_stack orchestrator with its GPU/CPU-parallel backend chooser, and the two
linking-condition probes (assess_linking_conditions, estimate_linking_distance_um) that run detection to
estimate a linking distance. Moved VERBATIM - not a single detection or its order changed (the validated
detection path; downstream linking is order-sensitive). The tools module re-exports every public entry
point plus the two private helpers the parallel-equivalence test imports.
"""
from __future__ import annotations

from typing import Optional
import numpy as np
import pandas as pd
import skimage as sk
from pycat.utils.general_utils import debug_log
from pycat.utils.tag_registry import tags_layer


# ---------------------------------------------------------------------------
# 3. Bead detection
# ---------------------------------------------------------------------------

def detect_beads_frame(
    frame: np.ndarray,
    min_sigma: float = 1.0,
    max_sigma: float = 5.0,
    num_sigma: int = 5,
    threshold: float = 0.02,
    host_mask: Optional[np.ndarray] = None,
    fit_quality: bool = False,
    fit_window: int = 9,
    fast_fit: bool = False,
    use_gpu: bool = False,
    return_sigma: bool = False,
) -> np.ndarray:
    """
    Detect beads in a single frame via Laplacian-of-Gaussian blob detection.

    Parameters
    ----------
    frame : 2D array (single time point of the bead channel).
    min_sigma, max_sigma, num_sigma : LoG scale-space parameters.
        Bead radius ≈ sqrt(2)·sigma (px). Cover the expected bead size range.
    threshold : detection sensitivity. Lower = more (dimmer) beads detected.
    host_mask : optional 2D bool/int mask. Detections whose centre falls
        outside this mask are discarded (keeps beads inside the eroded host).

    fit_quality : if True, fit a 2D Gaussian + background to each detected
        bead and return per-bead quality metrics (sub-pixel centre, sigma,
        amplitude, integrated intensity, R²) instead of just coordinates.
    fit_window : xy window (px) for the per-bead Gaussian fit.

    Returns
    -------
    If fit_quality is False: coords : (N, 2) array of (y, x) centres (px).
    If fit_quality is True:  list of dicts, one per bead, with keys
        y, x (sub-pixel px), sigma_x, sigma_y, sigma_mean, amplitude,
        integrated_intensity, offset, r_squared. Falls back to the LoG
        centre with NaN metrics for beads whose fit fails.
    """
    img = np.asarray(frame).astype(np.float32)
    mn, mx = float(img.min()), float(img.max())
    if mx > mn:
        img = (img - mn) / (mx - mn)

    if use_gpu:
        blobs = blob_log_gpu(
            img, min_sigma=min_sigma, max_sigma=max_sigma,
            num_sigma=num_sigma, threshold=threshold)
    else:
        blobs = sk.feature.blob_log(
            img, min_sigma=min_sigma, max_sigma=max_sigma,
            num_sigma=num_sigma, threshold=threshold)

    if blobs.shape[0] == 0:
        if return_sigma:
            return np.empty((0, 2)), np.empty((0,))
        return np.empty((0, 2))

    coords = blobs[:, :2]  # (y, x)
    _sigmas = blobs[:, 2] if blobs.shape[1] >= 3 else None  # detected scale

    if host_mask is not None:
        hm = np.asarray(host_mask) > 0
        keep = []
        keep_sig = []
        for _i, (y, x) in enumerate(coords):
            yi, xi = int(round(y)), int(round(x))
            if 0 <= yi < hm.shape[0] and 0 <= xi < hm.shape[1] and hm[yi, xi]:
                keep.append((y, x))
                if _sigmas is not None:
                    keep_sig.append(_sigmas[_i])
        coords = np.array(keep) if keep else np.empty((0, 2))
        _sigmas = (np.array(keep_sig) if keep_sig else None) if _sigmas is not None else None

    if not fit_quality:
        if return_sigma:
            return coords, _sigmas
        return coords

    # Per-bead 2D Gaussian quality fit
    from pycat.toolbox.gaussian_localization_tools import fit_gaussian_2d_spot
    raw = np.asarray(frame).astype(np.float32)
    half = fit_window // 2
    beads = []
    for (y, x) in coords:
        yi, xi = int(round(y)), int(round(x))
        y0, y1 = yi - half, yi + half + 1
        x0, x1 = xi - half, xi + half + 1
        if y0 < 0 or x0 < 0 or y1 > raw.shape[0] or x1 > raw.shape[1]:
            beads.append(dict(y=float(y), x=float(x), sigma_x=np.nan,
                              sigma_y=np.nan, sigma_mean=np.nan,
                              amplitude=np.nan, integrated_intensity=np.nan,
                              offset=np.nan, r_squared=np.nan))
            continue
        patch = raw[y0:y1, x0:x1]
        fit = fit_gaussian_2d_spot(patch, fast=fast_fit)
        if fit.get('success'):
            sx, sy = fit['sigma_x'], fit['sigma_y']
            sigma_mean = 0.5 * (sx + sy)
            # Integrated intensity of a 2D Gaussian = 2*pi*A*sigma_x*sigma_y
            integ = 2.0 * np.pi * fit['amplitude'] * sx * sy
            beads.append(dict(
                y=y0 + fit['y0'], x=x0 + fit['x0'],
                sigma_x=sx, sigma_y=sy, sigma_mean=sigma_mean,
                amplitude=fit['amplitude'], integrated_intensity=integ,
                offset=fit['offset'], r_squared=fit['r_squared']))
        else:
            beads.append(dict(y=float(y), x=float(x), sigma_x=np.nan,
                              sigma_y=np.nan, sigma_mean=np.nan,
                              amplitude=np.nan, integrated_intensity=np.nan,
                              offset=np.nan, r_squared=np.nan))
    return beads


# ---------------------------------------------------------------------------
# 3d. Accelerated blob detection (GPU LoG scale-space, matches skimage blob_log)
# ---------------------------------------------------------------------------

def blob_log_gpu(image, min_sigma=1.0, max_sigma=5.0, num_sigma=5,
                 threshold=0.02, overlap=0.5):
    """GPU-accelerated Laplacian-of-Gaussian blob detection.

    Reproduces skimage.feature.blob_log: builds the scale-normalised LoG cube
    (-gaussian_laplace(img, s) * s**2 over num_sigma scales), finds 3D local
    maxima above threshold, converts the scale index to a sigma, and prunes
    overlapping blobs. The expensive part — the per-scale Gaussian convolutions
    — runs on the GPU (keeping the whole cube on-device to avoid per-scale
    transfer), which is where blob_log spends ~all its time. Results match the
    CPU path within floating-point tolerance.

    Falls back to skimage.blob_log on the CPU if CuPy/GPU is unavailable, so it
    is always safe to call.

    Returns an (N, 3) array of (y, x, sigma), same as skimage.blob_log.
    """
    from skimage import feature as skfeature
    try:
        from pycat.toolbox.gpu_utils import gpu_available
    except Exception:
        gpu_available = lambda: False

    if not gpu_available():
        return skfeature.blob_log(
            image, min_sigma=min_sigma, max_sigma=max_sigma,
            num_sigma=num_sigma, threshold=threshold, overlap=overlap)

    import cupy as cp
    import cupyx.scipy.ndimage as cpnd
    from skimage.feature.blob import _prune_blobs
    from skimage.feature import peak_local_max

    img = cp.asarray(image, dtype=cp.float32)
    scales = np.linspace(min_sigma, max_sigma, num_sigma)
    # scale-normalised LoG cube, built and kept on the GPU (the expensive part —
    # the per-scale Gaussian convolutions — is what runs on-device).
    cube_gpu = cp.empty((num_sigma,) + img.shape, dtype=cp.float32)
    for i, s in enumerate(scales):
        cube_gpu[i] = -cpnd.gaussian_laplace(img, float(s)) * (float(s) ** 2)

    # Move the finished cube to the CPU and finish with skimage's EXACT peak
    # finder (peak_local_max) and pruning, so results are bit-for-bit the same
    # as skimage.blob_log. A raw (cube == maximum_filter) comparison does NOT
    # match skimage — peak_local_max deduplicates plateau/tie maxima and handles
    # borders differently — so we defer to it rather than reimplement it. The
    # convolutions (the costly step) still ran on the GPU.
    cube = cp.asnumpy(cube_gpu)
    # blob_log stores the scale as the LAST axis for peak_local_max; skimage
    # transposes the (scale, y, x) cube to (y, x, scale). Match that.
    image_cube = np.moveaxis(cube, 0, -1)
    local_maxima = peak_local_max(
        image_cube, threshold_abs=threshold, threshold_rel=None,
        exclude_border=False, footprint=np.ones((3,) * image_cube.ndim))
    if local_maxima.size == 0:
        return np.empty((0, 3))
    lm = local_maxima.astype(np.float64)
    # columns: y, x, scale_index → replace scale index with sigma
    sigmas_of_peaks = scales[local_maxima[:, -1]]
    lm = np.hstack([lm[:, :-1], sigmas_of_peaks[:, np.newaxis]])
    try:
        pruned = _prune_blobs(lm, overlap, sigma_dim=1)
    except TypeError:
        pruned = _prune_blobs(lm, overlap)
    return pruned


# ---------------------------------------------------------------------------
# 3c. Fast template-based bead scoring (empirical PSF + cross-correlation)
# ---------------------------------------------------------------------------

def bead_half_from_size(bead_size_nm, microns_per_pixel, n_rings=1, min_half=4, max_half=24):
    """Choose a template half-width (px) from the physical bead size so the
    patch is large enough to include the requested number of Airy rings.

    bead_size_nm : physical bead diameter in nanometres (user input).
    microns_per_pixel : loaded pixel size (µm/px); the linear scale, i.e.
        sqrt(microns_per_pixel_sq).
    n_rings : how many Airy rings the patch should span (1 by default; the 2nd
        ring is often only visible after frame averaging).

    The Airy disk radius (first dark ring) is roughly the bead radius scaled up
    by the optics, so we take the bead radius in px and pad it by n_rings worth
    of ring spacing (~the same radius again per ring), then clamp to a sane
    range. This is a heuristic starting size; detection/scoring still adapt.
    """
    try:
        mpp = float(microns_per_pixel) if microns_per_pixel and microns_per_pixel > 0 else None
    except Exception:
        mpp = None
    if not mpp:
        return min_half
    bead_um = float(bead_size_nm) / 1000.0
    bead_radius_px = (bead_um / mpp) / 2.0
    # central disk + n_rings, each ~one disk-radius wide, plus a small margin
    half = int(np.ceil(bead_radius_px * (1 + n_rings) + 2))
    return int(max(min_half, min(max_half, half)))


def build_airy_template(half, first_zero_px=None):
    """Build an analytic Airy-disk template (Bessel J1) of size (2*half+1)^2.

    The Airy intensity is I(r) = [2*J1(x)/x]^2 with x = 3.8317 * r / first_zero,
    where first_zero is the radius (px) of the first dark ring. Unlike a Gaussian
    template this reproduces the central disk AND the surrounding ring, so on
    data where beads show a resolved Airy pattern a single bead matches as ONE
    object (rather than blob_log firing separately on the ring).

    If first_zero_px is None it defaults to ~half (first dark ring near the patch
    edge, i.e. the patch spans about the first ring). Returns a zero-mean,
    unit-variance template for NCC scoring.
    """
    from scipy.special import j1
    if first_zero_px is None:
        first_zero_px = max(2.0, half * 0.8)
    y, x = np.ogrid[-half:half + 1, -half:half + 1]
    r = np.sqrt(y * y + x * x).astype(np.float64)
    xx = 3.8317 * r / float(first_zero_px)
    xx[xx == 0] = 1e-9
    airy = (2.0 * j1(xx) / xx) ** 2
    airy = airy.astype(np.float32)
    tmpl_z = (airy - airy.mean()) / (airy.std() + 1e-8)
    return tmpl_z


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


def dedup_detections(coords, frame, merge_radius_px, keep='brightest'):
    """Merge detections that fall within merge_radius_px of one another, keeping
    a single representative per cluster. blob_log can fire multiple times on one
    bead — at several scales on a broad bead, or on the Airy ring of a large
    bead — producing duplicate detections. This collapses each such cluster to
    one point (the brightest local intensity = the bead centre by default).

    coords : list/array of (y, x).
    frame  : the image, used to pick the brightest detection per cluster.
    merge_radius_px : detections closer than this are treated as the same bead.
    Returns the filtered list of (y, x).
    """
    if coords is None or len(coords) == 0 or merge_radius_px is None or merge_radius_px <= 0:
        return coords
    from scipy.spatial import cKDTree
    pts = np.asarray([(float(y), float(x)) for (y, x) in coords], dtype=float)
    raw = np.asarray(frame, dtype=np.float32)
    H, W = raw.shape

    def local_intensity(y, x, r=2):
        yi, xi = int(round(y)), int(round(x))
        y0, y1 = max(0, yi - r), min(H, yi + r + 1)
        x0, x1 = max(0, xi - r), min(W, xi + r + 1)
        if y1 <= y0 or x1 <= x0:
            return -np.inf
        return float(raw[y0:y1, x0:x1].mean())

    tree = cKDTree(pts)
    order = np.argsort([-local_intensity(y, x) for (y, x) in pts])  # brightest first
    used = np.zeros(len(pts), dtype=bool)
    kept = []
    for idx in order:
        if used[idx]:
            continue
        neighbours = tree.query_ball_point(pts[idx], r=float(merge_radius_px))
        kept.append(idx)                 # brightest in its neighbourhood
        for n in neighbours:
            used[n] = True
    kept.sort()
    return [tuple(pts[i]) for i in kept]


def build_bead_template(frame, coords, half=4, clean_percentile=60):
    """Build an empirical PSF template by averaging the cleanest bead patches.

    Instead of assuming a Gaussian, we measure the instrument's actual bead
    shape from the data: extract a patch around each detected bead, keep the
    cleanest (highest central-peak-over-edge) subset, normalise each to [0, 1],
    and average. The result is a zero-mean, unit-variance template used for fast
    normalised cross-correlation scoring.

    Returns (template_z, half) where template_z is a (2*half+1, 2*half+1) array,
    or (None, half) if too few beads to build a stable template.
    """
    raw = np.asarray(frame, dtype=np.float32)
    H, W = raw.shape
    patches = []
    for (y, x) in coords:
        yi, xi = int(round(y)), int(round(x))
        if yi - half < 0 or xi - half < 0 or yi + half + 1 > H or xi + half + 1 > W:
            continue
        patches.append(raw[yi - half:yi + half + 1, xi - half:xi + half + 1])
    if len(patches) < 10:
        return None, half
    patches = np.asarray(patches)
    mn = patches.min(axis=(1, 2), keepdims=True)
    mx = patches.max(axis=(1, 2), keepdims=True)
    norm = np.where(mx > mn, (patches - mn) / (mx - mn + 1e-8), 0.0)
    peakiness = norm[:, half, half] - norm[:, 0, :].mean(axis=1)
    keep = peakiness > np.percentile(peakiness, clean_percentile)
    if keep.sum() < 5:
        keep = np.ones(len(norm), dtype=bool)
    tmpl = norm[keep].mean(axis=0)
    tmpl_z = (tmpl - tmpl.mean()) / (tmpl.std() + 1e-8)
    return tmpl_z, half


def score_beads_template(frame, coords, template_z, half=4, subpixel=False):
    """Score each detected bead by fast features against an empirical template.

    For every bead, compute (all ~microseconds/bead):
      - ncc       : normalised cross-correlation to the template (shape match)
      - snr       : central peak over patch std (brightness/contrast)
      - symmetry  : radial symmetry (1 = symmetric; aggregates are lopsided)
    Optionally refine the centre to sub-pixel via an intensity centroid.

    Returns a list of per-bead dicts with keys: y, x, ncc, snr, symmetry,
    amplitude, integrated_intensity.
    """
    raw = np.asarray(frame, dtype=np.float32)
    H, W = raw.shape
    w = 2 * half + 1
    out = []
    for (y, x) in coords:
        yi, xi = int(round(y)), int(round(x))
        if yi - half < 0 or xi - half < 0 or yi + half + 1 > H or xi + half + 1 > W:
            out.append(dict(y=float(y), x=float(x), ncc=np.nan, snr=np.nan,
                            symmetry=np.nan,
                            amplitude=float(raw[min(yi, H - 1), min(xi, W - 1)]),
                            integrated_intensity=np.nan))
            continue
        p = raw[yi - half:yi + half + 1, xi - half:xi + half + 1]
        pmn, pmx = p.min(), p.max()
        pn = (p - pmn) / (pmx - pmn + 1e-8) if pmx > pmn else np.zeros_like(p)
        pz = (pn - pn.mean()) / (pn.std() + 1e-8)
        ncc = float((pz * template_z).sum() / (w * w)) if template_z is not None else np.nan
        snr = float(pn[half, half] / (pn.std() + 1e-8))
        q = np.array([pn[:half, :half].sum(), pn[:half, half + 1:].sum(),
                      pn[half + 1:, :half].sum(), pn[half + 1:, half + 1:].sum()])
        symmetry = float(1.0 - q.std() / (q.mean() + 1e-8))
        yy, xx = float(y), float(x)
        if subpixel:
            ww = np.clip(p - pmn, 0, None)
            s = ww.sum()
            if s > 0:
                gy, gx = np.mgrid[0:w, 0:w]
                yy = (yi - half) + float((ww * gy).sum() / s)
                xx = (xi - half) + float((ww * gx).sum() / s)
        out.append(dict(y=yy, x=xx, ncc=ncc, snr=snr, symmetry=symmetry,
                        amplitude=float(p[half, half]),
                        integrated_intensity=float(np.clip(p - pmn, 0, None).sum())))
    return out


def _classify_fast_template_refs(df, strictness, variant):
    """Reference statistics for fast-template classification, computed over the REAL beads only (so
    ring/hot/noise detections do not skew them): the NCC realness floor, the singlet intensity, the
    aggregate mass/amplitude gates (p99.3 mass just below the top cluster so a true aggregate stays
    stable frame-to-frame; p50 amplitude so it must also be bright), and the dim/out-of-focus cutoffs
    scaled by ``strictness``. Returned as a dict the per-bead pass reads."""
    ncc = df['ncc'].to_numpy(dtype=float)
    amp = df['amplitude'].to_numpy(dtype=float)
    ii = df['integrated_intensity'].to_numpy(dtype=float)
    snr = (df['snr'].to_numpy(dtype=float) if 'snr' in df
           else np.full(len(df), np.nan))

    # Real-vs-garbage: absolute NCC floor. The template is built FROM the real beads, so genuine beads
    # match it well; rings/hot/noise do not. 0.55 (not 0.50) reduces frame-to-frame flicker of dim
    # detections whose NCC hovers at the floor.
    NCC_FLOOR = 0.55
    is_real = np.isfinite(ncc) & (ncc >= NCC_FLOOR)

    # Hot-pixel reject variant: a HARSHER acceptance test on suspect pixels, not a flat veto — a real
    # bead drifting over a hot/dead pixel must still be accepted if it brings genuine template evidence.
    if variant == 'hot_pixel_reject' and 'on_hot_pixel' in df.columns:
        HOT_NCC_FLOOR = 0.75
        on_hot = df['on_hot_pixel'].fillna(False).to_numpy(dtype=bool)
        harsh_ok = ~on_hot | (np.isfinite(ncc) & (ncc >= HOT_NCC_FLOOR))
        is_real = is_real & harsh_ok

    rii = ii[is_real & np.isfinite(ii)]
    ramp = amp[is_real & np.isfinite(amp)]
    rsnr = snr[is_real & np.isfinite(snr)] if 'snr' in df else np.array([])
    if len(rii) >= 10:
        singlet_int = float(np.median(rii[rii <= np.median(rii)]))
        mass_hi = float(np.percentile(rii, 99.3))
        amp_hi = float(np.percentile(ramp, 50))     # must also be bright
    else:
        singlet_int = float(np.median(rii)) if len(rii) else np.nan
        mass_hi = np.inf; amp_hi = np.inf

    # Dim / out-of-focus threshold: a low-amplitude percentile scaled by strictness (default 1.0 → 25th
    # pct, tuned for viscous samples where most beads stay in focus). A low-SNR detection is dim-like too.
    s = float(strictness) if strictness and strictness > 0 else 1.0
    dim_pct = None
    if len(ramp) >= 10:
        dim_pct = float(np.clip(25.0 * s, 2.0, 60.0))
        amp_dim = float(np.percentile(ramp, dim_pct))
    else:
        amp_dim = -np.inf
    if len(rsnr) >= 10:
        snr_pct = float(np.clip(15.0 * s, 2.0, 50.0))
        snr_dim = float(np.percentile(rsnr, snr_pct))
    else:
        snr_dim = -np.inf

    # High-NCC guard against out_of_plane flicker: a bright, well-matched bead near the moving dim line
    # must never be demoted to yellow purely on a wobbling per-frame SNR percentile.
    return dict(ncc=ncc, amp=amp, ii=ii, snr=snr, has_snr=('snr' in df),
                is_real=is_real, singlet_int=singlet_int, mass_hi=mass_hi, amp_hi=amp_hi,
                amp_dim=amp_dim, snr_dim=snr_dim, ncc_floor=NCC_FLOOR,
                ncc_singlet_guard=0.80, dim_pct=dim_pct)


def _classify_fast_template(df, strictness, variant):
    """Fast-mode (template-scorer) classification into four tiers, for large Airy-disk beads where a real
    single bead is BRIGHT and high-mass:

      rejected  : poor template match (NCC below the floor) — Airy-ring fragments, hot pixels, noise;
                  DROPPED entirely (never become points).
      aggregate : BRIGHT and COMPACT and HIGH-MASS (top mass tail AND high amplitude) — requiring BOTH
                  is what separates a true aggregate from an out-of-focus blob (high-mass but dim).
      ambiguous : high-mass but dim/diffuse (out of focus) — too uncertain to call; flagged honestly.
      singlet   : every other well-matched real bead (the large majority).
    """
    R = _classify_fast_template_refs(df, strictness, variant)
    ii, amp, snr, ncc = R['ii'], R['amp'], R['snr'], R['ncc']
    is_real, singlet_int, has_snr = R['is_real'], R['singlet_int'], R['has_snr']
    mass_hi, amp_hi, amp_dim, snr_dim = R['mass_hi'], R['amp_hi'], R['amp_dim'], R['snr_dim']
    NCC_SINGLET_GUARD = R['ncc_singlet_guard']

    n_units, classes = [], []
    for k in range(len(df)):
        if not is_real[k]:
            n_units.append(np.nan); classes.append('rejected'); continue
        I, A = ii[k], amp[k]
        S = snr[k] if has_snr else np.nan
        C = ncc[k]
        nu = I / singlet_int if (singlet_int and singlet_int > 0) else np.nan
        n_units.append(nu)
        high_mass = np.isfinite(I) and I >= mass_hi
        bright = np.isfinite(A) and A >= amp_hi
        # Require the AMPLITUDE to actually be low — a low per-frame SNR alone must NOT demote a bead
        # whose amplitude is fine (that was the flicker source); SNR is only a secondary confirmation.
        amp_low = np.isfinite(A) and A <= amp_dim
        snr_low = np.isfinite(S) and S <= snr_dim
        is_dim = amp_low or (snr_low and amp_low)
        well_matched = np.isfinite(C) and C >= NCC_SINGLET_GUARD   # immune to the dim gate (anti-flicker)
        if high_mass and bright:
            classes.append('aggregate')
        elif is_dim and not high_mass and not well_matched:
            classes.append('out_of_plane')
        elif high_mass and not bright:
            classes.append('ambiguous')
        else:
            classes.append('singlet')
    df['n_units_est'] = n_units
    df['bead_class'] = classes
    # DROP rejected detections entirely — a marked point should be a real bead.
    df = df[df['bead_class'] != 'rejected'].reset_index(drop=True)
    df['singlet'] = df['bead_class'] == 'singlet'
    # Record the thresholds actually used, so results are reproducible and the regime is auditable.
    df.attrs['classify_thresholds'] = {
        'mode': 'fast_template',
        'ncc_floor': float(R['ncc_floor']),
        'ncc_singlet_guard': float(NCC_SINGLET_GUARD),
        'aggregate_mass_percentile': 99.3,
        'aggregate_amp_percentile': 50.0,
        'aggregate_mass_hi': float(mass_hi),
        'aggregate_amp_hi': float(amp_hi),
        'dim_amp_percentile': float(R['dim_pct']) if R['dim_pct'] is not None else None,
        'strictness': float(strictness),
    }
    return df


def _classify_gaussian_fit(df, sigma_outlier_factor, aggregate_intensity_factor):
    """Gaussian-fit-mode classification (fast_fit / precise / legacy), reached when a Gaussian fit
    produced ``sigma_mean`` + ``r_squared``. Focus is judged by the fitted SIGMA, which is
    SNR-independent — NOT by R² (R² measures how well the model explains the VARIANCE, so at low SNR it
    collapses even for a perfectly in-focus bead: a true sigma-1.0 bead scores R² 0.24 at SNR 3 and 0.99
    at SNR 53, so an R² gate flagged DIM in-focus beads as out-of-plane. ``defocus_r2_max`` is retained
    in the caller's signature for back-compat and is no longer used)."""
    # Restrict the singlet reference stats to beads with finite fit metrics (else NaN/failed fits
    # pollute the reference medians).
    valid = (
        np.isfinite(df['integrated_intensity']) &
        np.isfinite(df['sigma_mean']) &
        np.isfinite(df['r_squared'])
    )

    # Robust singlet reference = lower-half median (biases the reference toward singlets; aggregates are
    # the bright minority).
    ref = df.loc[valid, 'integrated_intensity']
    if len(ref) >= 4:
        singlet_int = float(np.median(ref[ref <= ref.median()]))
    elif len(ref) > 0:
        singlet_int = float(ref.median())
    else:
        singlet_int = np.nan
    sig = df.loc[valid, 'sigma_mean']
    singlet_sigma = float(np.median(sig[sig <= sig.median()])) if len(sig) >= 4 else \
        (float(sig.median()) if len(sig) > 0 else np.nan)

    amp = df.loc[valid, 'amplitude']
    singlet_amp = float(np.median(amp[amp <= amp.median()])) if len(amp) >= 4 else \
        (float(amp.median()) if len(amp) > 0 else np.nan)

    n_units, classes = [], []
    for _, r in df.iterrows():
        I = r['integrated_intensity']; s = r['sigma_mean']
        r2 = r['r_squared']; A = r['amplitude']
        if not np.isfinite(I) or not np.isfinite(r2):
            n_units.append(np.nan); classes.append('unfit'); continue
        nu = I / singlet_int if (singlet_int and singlet_int > 0) else np.nan
        n_units.append(nu)
        oversized = (np.isfinite(s) and np.isfinite(singlet_sigma)
                     and s > sigma_outlier_factor * singlet_sigma)
        brighter = np.isfinite(nu) and nu >= aggregate_intensity_factor
        # Defocus signature: enlarged spot whose PEAK amplitude is depressed relative to a singlet
        # (photons spread over a wider area), i.e. NOT a true aggregate. Sigma (not R²) is the focus test.
        dim_peak = (np.isfinite(A) and np.isfinite(singlet_amp)
                    and singlet_amp > 0 and A < 0.7 * singlet_amp)
        if brighter and not dim_peak:
            classes.append('aggregate')
        elif oversized and dim_peak:
            classes.append('out_of_plane')
        else:
            classes.append('singlet')
    df['n_units_est'] = n_units
    df['bead_class'] = classes
    df['singlet'] = df['bead_class'] == 'singlet'
    return df


def classify_beads(beads_df: pd.DataFrame,
                   aggregate_intensity_factor: float = 1.6,
                   defocus_r2_max: float = 0.85,
                   sigma_outlier_factor: float = 1.5,
                   strictness: float = 1.0,
                   variant: str = 'baseline') -> pd.DataFrame:
    """
    Classify fitted beads into singlet / aggregate / out-of-plane using the
    2D-Gaussian quality metrics.

    DETECTION-VARIANT STAGING (``variant``): 'baseline' is the 1.5.329-validated
    classifier and is the default — it is never changed, so the validated
    ~8.325-through-TrackMate path stays selectable and a revert is a one-arg
    change. New variants are opt-in and additive, each implemented as its own
    branch so they can be A/B-compared against baseline on the same detections
    without touching the baseline code path. See ``_classify_variant_*`` helpers.

    The discriminating physics:
      - A singlet has a characteristic PSF width (sigma) and integrated
        intensity — the population modes.
      - An AGGREGATE is larger AND brighter: its integrated intensity is a
        (roughly discrete) multiple of the singlet level, because it is
        several beads' worth of signal. Width also grows.
      - An OUT-OF-PLANE / defocused bead is larger but DIMMER per unit area:
        defocus spreads the same photons over a wider spot, lowering the peak
        amplitude and degrading the Gaussian fit (lower R²). Integrated
        intensity stays near the singlet level even though sigma is inflated.

    So the key separation is: large sigma + high integrated intensity →
    aggregate; large sigma + near-singlet integrated intensity + poor fit →
    defocused (recoverable).

    Parameters
    ----------
    beads_df : DataFrame with sigma_mean, integrated_intensity, r_squared.
    aggregate_intensity_factor : integrated-intensity multiple of the singlet
        median above which a bead is called an aggregate (default 1.6× ≈
        partway to a dimer, catching dimers and larger).
    defocus_r2_max : **DEPRECATED and no longer used.** R² measures SNR, not focus: a
        perfectly in-focus bead scores R² = 0.24 at SNR 3 and 0.99 at SNR 53, so this
        threshold flagged DIM IN-FOCUS beads as out-of-plane and kept bright ones. Focus
        is judged by the fitted SIGMA, which is SNR-independent. Retained in the signature
        for backward compatibility only.

        (historical) fit-R² below which an oversized, non-brighter bead was
        called out-of-plane rather than an aggregate.
    sigma_outlier_factor : sigma multiple of the singlet median above which a
        bead is considered "oversized".

    Returns
    -------
    beads_df with added columns:
        n_units_est   : integrated_intensity / singlet median (≈ #beads)
        bead_class    : 'singlet' | 'aggregate' | 'out_of_plane' | 'unfit'
        singlet       : bool convenience flag (bead_class == 'singlet')
    """
    df = beads_df.copy()
    if df.empty:
        for c in ('n_units_est', 'bead_class', 'singlet'):
            df[c] = [] if c != 'singlet' else []
        return df

    # Fast-template mode: the template scorer produced ncc/snr/symmetry but no Gaussian r_squared.
    if 'r_squared' not in df.columns and 'ncc' in df.columns:
        return _classify_fast_template(df, strictness, variant)

    # Gaussian-fit mode (fast_fit / precise / legacy): sigma_mean + r_squared are present.
    return _classify_gaussian_fit(df, sigma_outlier_factor, aggregate_intensity_factor)


def _bead_source_descriptor(bead_stack):
    """Build a small picklable descriptor that lets a worker subprocess re-open
    the bead stack and read a single frame, WITHOUT pickling the (unpicklable,
    file-handle-backed) lazy stack wrapper itself.

    Returns a dict describing how to read a frame, or None if the stack is not a
    file-backed lazy reader we know how to re-open in a subprocess (in which case
    the caller falls back to serial/in-process detection).

    For a multi-file OME set the wrapper carries a page map (global frame →
    (file, page)); we pass that map to the workers so they read exactly the same
    frames the serial path does, across the linked files, without re-resolving
    the OME series per frame (which is both slow and the source of the repeated
    "companion missing" warning).
    """
    path = getattr(bead_stack, '_path', None)
    if not path:
        return None
    page_map = getattr(bead_stack, '_page_map', None)
    if page_map is not None:
        # Multi-file: hand the workers the explicit (file, page) map.
        return {
            'kind': 'pagemap',
            'page_map': [(str(p), int(i)) for (p, i) in page_map],
            'nc': int(getattr(bead_stack, '_nc', 1) or 1),
            'ci': int(getattr(bead_stack, '_ci', 0)),
        }
    return {
        'kind': 'tiff',
        'path': str(path),
        'nc': int(getattr(bead_stack, '_nc', 1) or 1),
        'ci': int(getattr(bead_stack, '_ci', 0)),
    }


def _read_frame_from_descriptor(t, src_desc):
    """Read frame t in a worker subprocess from a source descriptor. Top-level +
    picklable. Mirrors the time-series reader so both share the same approach.

    tifffile logs an OME-series warning ("... failed to read ... Missing data are
    zeroed") when a multi-file OME set references a companion file that is not
    present. The serial reader hits this once (it opens the file a single time);
    a worker re-opens the file per frame, so without suppression the warning is
    printed once PER FRAME — thousands of lines for a long movie. We silence
    tifffile's logger for the duration of the read; the frame we want lives in
    this file's own pages regardless of the companion.
    """
    import numpy as np
    import logging
    kind = src_desc.get('kind')
    if kind == 'pagemap':
        # Multi-file OME set: read from the explicit (file, page) map so workers
        # match the serial reader exactly, across linked files, no per-frame OME
        # resolution (and thus no repeated companion-missing warning).
        import tifffile as _tf
        page_map = src_desc['page_map']
        nc = int(src_desc.get('nc', 1)) or 1
        ci = int(src_desc.get('ci', 0))
        gi = int(t) * nc + ci
        path, page_idx = page_map[gi]
        _tflog = logging.getLogger('tifffile')
        _prev = _tflog.level
        _tflog.setLevel(logging.ERROR)
        try:
            with _tf.TiffFile(path) as _tif:
                return np.asarray(_tif.pages[page_idx].asarray()).astype(np.float32)
        finally:
            _tflog.setLevel(_prev)
    if kind == 'tiff':
        import tifffile as _tf
        _tflog = logging.getLogger('tifffile')
        _prev = _tflog.level
        _tflog.setLevel(logging.ERROR)  # hide the per-file OME warning
        try:
            with _tf.TiffFile(src_desc['path']) as _tif:
                # Match the serial reader (_TiffPageStack) EXACTLY so parallel and
                # serial read the same frame: prefer the OME series (which spans
                # a multi-file set) and fall back to this file's own pages. The
                # only difference from serial is that we silence tifffile's
                # per-file OME warning, which would otherwise print once per frame
                # because each worker re-opens the file.
                try:
                    pages = _tif.series[0].pages
                except Exception:
                    pages = _tif.pages
                nc = int(src_desc.get('nc', 1)) or 1
                ci = int(src_desc.get('ci', 0))
                page = pages[int(t) * nc + ci]
                return np.asarray(page.asarray()).astype(np.float32)
        finally:
            _tflog.setLevel(_prev)
    raise ValueError(f"unsupported source descriptor kind: {kind!r}")


def _detect_frame_worker(args):
    """Top-level picklable worker for ProcessPoolExecutor.

    Reads one frame (from a source descriptor OR a directly-passed array),
    runs blob-detection (+ optional de-dup), and returns (t, coords) where
    coords is a plain list of (y, x) floats — small and cheap to pickle back.

    Only the EXPENSIVE, embarrassingly-parallel part (per-frame blob detection)
    runs here. Template building, scoring and classification stay in the parent
    process where the shared template lives. This keeps the worker stateless and
    the returned payload tiny.
    """
    (t, frame_or_desc, is_desc, det_kwargs, merge_radius_px) = args
    import numpy as np
    if is_desc:
        frame = _read_frame_from_descriptor(t, frame_or_desc)
    else:
        frame = np.asarray(frame_or_desc, dtype=np.float32)
    coords = detect_beads_frame(frame, **det_kwargs)
    if merge_radius_px:
        coords = dedup_detections(coords, frame, merge_radius_px)
    # Return plain python floats so the payload is trivially picklable.
    return int(t), [(float(y), float(x)) for (y, x) in coords]


def assess_linking_conditions(detections, motion_sigma_um=None,
                              bead_stack=None, microns_per_pixel=1.0):
    """Assess whether frame-to-frame nearest-neighbour linking (greedy, Bayesian)
    is reliable for this data, via the ambiguity ratio R = per-frame bead
    displacement / nearest-neighbour spacing.

    Rationale. Frame-to-frame NN linking assigns each bead to its closest match in
    the next frame; it succeeds when a bead's own next position is unambiguously
    closer to it than any *other* bead's position. The governing quantity is
    therefore displacement RELATIVE TO SPACING, not displacement alone — a bead
    moving 1 µm/frame is trivially linkable if neighbours are 50 µm away and
    hopeless if they are 0.5 µm away. Thresholds:

        R < 0.10   SAFE    — step ≪ spacing; NN linking reliable.
        0.10-0.25  CAUTION — mostly reliable; occasional close-approach swaps.
        0.25-0.50  RISKY   — identity ambiguous; global (TrackMate LAP) wins.
        R > 0.50   UNSAFE  — bead routinely closer to a neighbour than itself;
                             frame-to-frame identity fundamentally ambiguous —
                             use TrackMate LAP or a faster frame rate (which
                             shrinks displacement and lowers R).

    Both inputs are available WITHOUT tracking: the per-frame displacement is the
    projection-based ``motion_sigma`` (see estimate_linking_distance_um), and the
    nearest-neighbour spacing is a single-frame kd-tree query over detections.

    Parameters
    ----------
    detections : DataFrame with 'frame','y_um','x_um'.
    motion_sigma_um : per-frame displacement (µm). If None and bead_stack given,
        it is estimated via estimate_linking_distance_um.
    bead_stack : optional stack, used only to estimate motion if not supplied.
    microns_per_pixel : pixel size (for the motion estimate if needed).

    Returns
    -------
    dict: ratio, motion_um, nn_spacing_um, level ('safe'/'caution'/'risky'/
        'unsafe'), message.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    if motion_sigma_um is None:
        if bead_stack is None:
            return dict(ratio=float('nan'), motion_um=float('nan'),
                        nn_spacing_um=float('nan'), level='unknown',
                        message="linking conditions unknown (no motion estimate)")
        est = estimate_linking_distance_um(
            bead_stack, microns_per_pixel=microns_per_pixel)
        motion_sigma_um = est.get('motion_sigma_um', float('nan'))

    # Nearest-neighbour spacing: median over frames of the median NN distance.
    nns = []
    for _f, g in detections.groupby('frame'):
        pts = g[['y_um', 'x_um']].values
        if len(pts) < 2:
            continue
        tree = cKDTree(pts)
        dd, _ = tree.query(pts, k=2)  # self + nearest neighbour
        nns.append(np.median(dd[:, 1]))
    nn_um = float(np.median(nns)) if nns else float('nan')

    if not (np.isfinite(motion_sigma_um) and np.isfinite(nn_um) and nn_um > 0):
        return dict(ratio=float('nan'), motion_um=motion_sigma_um,
                    nn_spacing_um=nn_um, level='unknown',
                    message="linking conditions unknown")

    R = motion_sigma_um / nn_um
    if R < 0.10:
        level = 'safe'
        note = "nearest-neighbour linking (greedy/Bayesian) reliable"
    elif R < 0.25:
        level = 'caution'
        note = "mostly reliable; occasional identity swaps possible"
    elif R < 0.50:
        level = 'risky'
        note = "bead identity ambiguous — prefer TrackMate LAP (global linking)"
    else:
        level = 'unsafe'
        note = ("frame-to-frame linking unreliable — use TrackMate LAP or a "
                "faster frame rate")
    msg = (f"R = {R:.2f} ({motion_sigma_um*1000:.0f} nm step / "
           f"{nn_um*1000:.0f} nm spacing): {note}")
    return dict(ratio=R, motion_um=motion_sigma_um, nn_spacing_um=nn_um,
                level=level, message=msg)


def estimate_linking_distance_um(bead_stack, coords_by_frame=None,
                                 microns_per_pixel=1.0, k=2.5,
                                 window=8, n_beads=40, half=7,
                                 min_distance_um=0.05):
    """Estimate a physically-grounded max linking distance (µm) WITHOUT linking
    any tracks, via a short-window time-projection of the bead motion.

    Idea (Gable's). A short-window MAX-projection of the stack smears each bead
    into a blob whose width = its single-frame PSF width broadened by how far the
    bead MOVED over that window. The motion contribution is recovered by
    subtracting the single-frame PSF width in quadrature:

        motion_sigma = sqrt( sigma_projected^2 - sigma_singleframe^2 )

    That ``motion_sigma`` is the per-frame displacement scale (a short window ≈ a
    few frames of motion), which is exactly the quantity a frame-to-frame linker
    must bridge. The linking distance is ``k × motion_sigma`` (k gives margin for
    the jitter tail), computed robustly over many beads. It is CAPPED at the bead
    footprint (a few × the PSF sigma) so it can never exceed one bead's own size
    and start grabbing neighbours in a dense field.

    Why this beats a fixed default or a PSF-width rule: these 200 nm beads image
    as a ~2 px PSF but move only ~0.5 px/frame, so motion ≪ bead size — a
    PSF-width distance (2-3 µm) would be far too generous, while the motion scale
    (~0.3-0.5 µm here) is what actually needs bridging. It is also
    viscosity-adaptive: slow (viscous) beads → tight distance, fast beads →
    looser, with no user guessing and no provisional linking pass.

    Parameters
    ----------
    bead_stack : (T, H, W) stack (lazy or array).
    coords_by_frame : optional {frame_index: [(y_px, x_px), ...]} of detections
        to sample bead locations from. If None, a quick blob_log on the first
        frame is used.
    microns_per_pixel : pixel size.
    k : margin factor on the per-frame motion sigma (default 2.5).
    window : number of frames for the short projection (default 8).
    n_beads : max beads to sample for the robust estimate.
    half : half-window (px) of the patch fit around each bead.
    min_distance_um : floor so the estimate is never absurdly small.

    Returns
    -------
    dict: linking_distance_um, motion_sigma_um, psf_sigma_um, capped (bool),
        n_beads_used — the derived distance plus the quantities behind it
        (anti-black-box: the caller can show what was measured and why).
    """
    import numpy as np
    from scipy.optimize import curve_fit
    from pycat.file_io.stack_access import materialize_stack

    def _fit_sigma(patch, h):

        """Fit a 2-D Gaussian to one bead and return its width.


        **The covariance is discarded here, and that is correct.**


        Elsewhere in PyCAT ``popt, _ = curve_fit(...)`` was a real bug: the SACF and CCF fits threw

        away the one number that says whether the Gaussian describes the data at all, and reported

        a **119.8 px correlation length for pure noise** (1.5.520).


        **This is not that.** There, ONE fit IS the answer. Here it is one of forty: the caller takes

        ``np.median(psf_sigmas)`` across every bead, and **the median tolerates up to 50 % garbage by

        construction.** Verified: with 40 % of the fits replaced by uniform noise, the median still

        recovers **2.12** against a true **2.00**.


        *A per-fit quality gate would add cost and no protection.*

        """
        p = np.asarray(patch, dtype=float)
        p = p - p.min()
        if p.max() <= 0:
            return np.nan
        yy, xx = np.mgrid[0:p.shape[0], 0:p.shape[1]]

        def g(c, A, x0, y0, s, o):
            x, y = c
            return (A * np.exp(-((x - x0) ** 2 + (y - y0) ** 2) / (2 * s ** 2)) + o).ravel()
        try:
            popt, _ = curve_fit(g, (xx, yy), p.ravel(),
                                p0=[p.max(), h, h, 1.5, 0.0], maxfev=4000)
            return abs(float(popt[3]))
        except Exception:  # broad-ok: returns NaN on Gaussian-fit failure — an honest missing width, not a fabricated value
            return np.nan

    # Materialise only the projection window (small), not the whole movie.
    try:
        arr = np.asarray(materialize_stack(bead_stack))
    except Exception:
        arr = np.asarray(bead_stack)
    if arr.ndim == 2:
        arr = arr[None]
    T, H, W = arr.shape
    win = int(min(max(2, window), T))

    # Sample bead centres.
    centres = []
    if coords_by_frame:
        f0 = sorted(coords_by_frame.keys())[0]
        centres = [(int(round(y)), int(round(x)))
                   for (y, x) in coords_by_frame[f0]]
    if not centres:
        c0 = detect_beads_frame(arr[0].astype(np.float32))
        centres = [(int(round(y)), int(round(x))) for (y, x) in c0]
    if not centres:
        return dict(linking_distance_um=float('nan'), motion_sigma_um=float('nan'),
                    psf_sigma_um=float('nan'), capped=False, n_beads_used=0)

    rng = np.random.default_rng(0)
    if len(centres) > n_beads:
        idx = rng.choice(len(centres), n_beads, replace=False)
        centres = [centres[i] for i in idx]

    proj_win = arr[:win].max(axis=0)
    psf_sigmas, motion_sigmas = [], []
    for (yi, xi) in centres:
        if yi - half < 0 or xi - half < 0 or yi + half + 1 > H or xi + half + 1 > W:
            continue
        s1 = _fit_sigma(arr[0][yi - half:yi + half + 1, xi - half:xi + half + 1], half)
        sp = _fit_sigma(proj_win[yi - half:yi + half + 1, xi - half:xi + half + 1], half)
        if not (np.isfinite(s1) and np.isfinite(sp)):
            continue
        psf_sigmas.append(s1)
        motion_sigmas.append(np.sqrt(max(sp ** 2 - s1 ** 2, 0.0)))
    if not motion_sigmas:
        return dict(linking_distance_um=float('nan'), motion_sigma_um=float('nan'),
                    psf_sigma_um=float('nan'), capped=False, n_beads_used=0)

    motion_sigma_px = float(np.median(motion_sigmas))
    psf_sigma_px = float(np.median(psf_sigmas))
    dist_px = float(k) * motion_sigma_px
    # Cap at the bead footprint (never link farther than ~the bead's own size).
    cap_px = 3.0 * psf_sigma_px
    capped = dist_px > cap_px
    dist_px = min(dist_px, cap_px)
    dist_um = max(dist_px * microns_per_pixel, float(min_distance_um))
    return dict(
        linking_distance_um=dist_um,
        motion_sigma_um=motion_sigma_px * microns_per_pixel,
        psf_sigma_um=psf_sigma_px * microns_per_pixel,
        capped=bool(capped),
        n_beads_used=len(motion_sigmas))


# ── GPU/CPU equivalence: verified ONCE per session, never once per call ──────
#
# Whether the GPU blob detector agrees with skimage is a property of the
# **machine** (driver + cupy build) and the **LoG params**. It is not a property
# of the data: the same machine running the same params cannot agree on one
# stack and disagree on the next. So the verdict is memoised on exactly those
# invariants and nothing else — deliberately NOT on the stack.
#
# It used to run on every `detect_beads_stack` call. That is four call sites
# (including the live preview, which re-runs on every param change), each paying
# a full CPU-detect + GPU-detect + compare of frame 0 before the real work
# started — enough to erase a marginal GPU win and make GPU feel slower than
# CPU-parallel.
#
# The CHECK is preserved, not removed: a cache miss still runs it in full, and a
# mismatching GPU is still never trusted. Only the repetition is gone.
_GPU_EQUIV_CACHE: dict = {}


def _gpu_build_id() -> str:
    """The cupy/driver build a verdict belongs to.

    Part of the cache key because a cupy or driver swap mid-session is the one
    thing that could legitimately change the answer. Cheap to read, and it means
    the cache can never outlive the build it was measured on.
    """
    try:
        import cupy
        return (f"{getattr(cupy, '__version__', '?')}/"
                f"{cupy.cuda.runtime.runtimeGetVersion()}")
    except Exception:  # broad-ok: optional-backend version probe — 'no-cupy' when CuPy is absent, not a scientific result
        return 'no-cupy'


def _run_gpu_equivalence_check(frame, *, min_sigma, max_sigma, num_sigma,
                              threshold, host_mask=None) -> bool:
    """Detect one frame on BOTH backends and report whether they agree.

    The expensive half of the guard, kept as its own function so the memo above
    is the only thing deciding how often it runs (and so a test can spy on it).
    """
    cpu = detect_beads_frame(
        frame, min_sigma=min_sigma, max_sigma=max_sigma, num_sigma=num_sigma,
        threshold=threshold, host_mask=host_mask, use_gpu=False)
    gpu = detect_beads_frame(
        frame, min_sigma=min_sigma, max_sigma=max_sigma, num_sigma=num_sigma,
        threshold=threshold, host_mask=host_mask, use_gpu=True)

    def _key(cs):
        return sorted((round(float(y), 3), round(float(x), 3)) for (y, x) in cs)

    return _key(cpu) == _key(gpu)


def gpu_matches_cpu(frame_getter, *, min_sigma, max_sigma, num_sigma, threshold,
                    host_mask=None) -> bool:
    """Can the GPU detector be trusted for these params on this machine?

    Memoised per process. `frame_getter` is a callable so that a cache HIT never
    even reads frame 0 — on the hot path (a preview re-running on every spinbox
    tick) the whole guard collapses to one dict lookup.

    Any failure reads as "do not trust the GPU": a guard that cannot prove
    equivalence has not proven it.
    """
    key = (_gpu_build_id(), float(min_sigma), float(max_sigma),
           int(num_sigma), float(threshold))
    if key in _GPU_EQUIV_CACHE:
        return _GPU_EQUIV_CACHE[key]
    try:
        verdict = _run_gpu_equivalence_check(
            frame_getter(), min_sigma=min_sigma, max_sigma=max_sigma,
            num_sigma=num_sigma, threshold=threshold, host_mask=host_mask)
    except Exception as exc:
        debug_log('GPU equivalence guard failed; not trusting the GPU', exc)
        verdict = False
    _GPU_EQUIV_CACHE[key] = verdict
    return verdict


# ── Which detection tier? Cost all three; pick the cheapest. ─────────────────
#
# The rule used to be a FIXED preference order — "GPU > CPU-parallel > serial" —
# implemented by making the pool unreachable whenever a GPU existed:
#
#     if quality_mode == 'fast' and not gpu_on and ...:     # the pool never competed
#
# Two things were wrong with that, and both are measured on this tree (GTX 1080,
# 7 CPU workers, constant bead density, per frame):
#
#     xy      serial       GPU      CPU-pool(7w)      T=1000 total
#     512    136.9 ms    49.5 ms   46.2 ms + 5.0 s    GPU  50 s | pool  51 s
#     1024   528.1 ms   249.0 ms  166.8 ms + 5.4 s    GPU 250 s | pool 172 s
#     2048  2817.2 ms  1123.1 ms 1068.4 ms + 6.8 s    GPU 1124 s | pool 1075 s
#
# 1. The GPU is only ~2-3x one CPU core here — not enough to beat SEVEN of them.
#    On a real 2048x2048x1000 stack the fixed order picked the slower tier, which
#    is exactly the "GPU felt slower than CPU-parallel" report from the workflow.
# 2. The pool was gated on `n_frames > 1`, which is not a threshold: a 20-frame
#    stack got a 7-worker pool and took 5043 ms instead of 451 ms (an ~11x LOSS),
#    because a spawn costs ~4.9 s and that stack is 0.27 s of work.
#
# There is no GPU contention to fear from letting the pool compete: the workers
# detect on the CPU (`detect_beads_frame`'s use_gpu defaults to False), so the two
# tiers use different hardware and are genuinely independent.
#
# So: measure what a frame costs on this data, model each tier's total, take the
# minimum. Nothing here is a fixed preference.

# The pool parallelises DETECTION only — template building, scoring and
# classification stay in the parent process (see `_detect_frame_worker`). That
# serial tail is why 7 workers return ~3x and not ~7x. Amdahl with p = 0.78
# reproduces the measured 2.64-3.17x across 512/1024/2048.
_POOL_PARALLEL_FRACTION = 0.78

_FRAME_COST_CACHE: dict = {}


def _pool_spawn_cost_s() -> float:
    """Roughly what standing up a worker pool costs, in seconds.

    Platform-derived rather than measured, because measuring it means paying it —
    and the whole question is whether to pay it at all.

    The split that matters is the start method, not the OS: `fork` clones a warm
    interpreter and is nearly free, while `spawn` (Windows, and macOS since 3.8)
    starts every worker from scratch and re-imports numpy/skimage/pandas in each
    one. 4.0 s is a deliberately conservative read of the 4.9-6.8 s measured here:
    under-spawning costs a little speed, over-spawning costs seconds.
    """
    try:
        import multiprocessing
        method = multiprocessing.get_start_method(allow_none=True)
        if method is None:
            method = multiprocessing.get_start_method()
    except Exception:
        method = 'spawn'
    return 0.05 if method == 'fork' else 4.0


def _pool_speedup(workers) -> float:
    """What `workers` workers actually deliver — Amdahl, not the worker count."""
    if not workers or workers < 2:
        return 1.0
    p = _POOL_PARALLEL_FRACTION
    return 1.0 / ((1.0 - p) + p / float(workers))


def _frame_costs_s(frame, *, gpu_ok, min_sigma, max_sigma, num_sigma, threshold,
                   host_mask=None):
    """`(serial_s, gpu_s|None)` for ONE frame. Probed once per (build, params, shape).

    Probed rather than assumed, because per-frame cost spans 14 ms (a 171x201 crop)
    to 2.8 s (a 2048x2048 field) and the tier that wins moves with it — no fixed
    frame-count threshold is right at both ends.

    Keyed on the frame SHAPE as well as the params and build, because shape is what
    the cost depends on. Contrast `gpu_matches_cpu`, whose verdict is a property of
    the machine and is deliberately NOT keyed on the data: same cache discipline,
    different invariants, because they are answering different questions.

    A probe that raises returns `(0.0, None)` — "cost unknown", which the selector
    reads as a reason to stay on the tier that needs no justification.
    """
    import time
    key = (_gpu_build_id(), float(min_sigma), float(max_sigma), int(num_sigma),
           float(threshold), tuple(getattr(frame, 'shape', ()) or ()), bool(gpu_ok))
    if key in _FRAME_COST_CACHE:
        return _FRAME_COST_CACHE[key]

    def _time(use_gpu):
        t0 = time.perf_counter()
        detect_beads_frame(frame, min_sigma=min_sigma, max_sigma=max_sigma,
                           num_sigma=num_sigma, threshold=threshold,
                           host_mask=host_mask, use_gpu=use_gpu)
        return time.perf_counter() - t0

    try:
        costs = (_time(False), _time(True) if gpu_ok else None)
    except Exception as exc:
        debug_log('tier probe: frame 0 would not detect; costs unknown', exc)
        costs = (0.0, None)
    _FRAME_COST_CACHE[key] = costs
    return costs


def _choose_detection_tier(*, n_frames, t_ser, t_gpu, workers, gpu_ok, pool_ok) -> str:
    """The cheapest tier for THIS stack: `'gpu'` | `'pool'` | `'serial'`.

        serial : t_ser * T
        gpu    : t_gpu * T
        pool   : spawn + (t_ser * T) / speedup(workers)

    With the cost unknown (`t_ser` 0), fall back to the old preference rather than
    guess — a wrong guess here costs minutes on a long stack.
    """
    if not n_frames or n_frames < 1 or not t_ser or t_ser <= 0:
        return 'gpu' if gpu_ok else 'serial'

    options = [('serial', t_ser * n_frames)]
    if gpu_ok and t_gpu and t_gpu > 0:
        options.append(('gpu', t_gpu * n_frames))
    if pool_ok and workers and workers >= 2 and n_frames > 1:
        options.append(('pool', _pool_spawn_cost_s()
                        + (t_ser * n_frames) / _pool_speedup(workers)))
    return min(options, key=lambda kv: kv[1])[0]


def _bead_first_frame(bead_stack, frame_indices):
    """The first frame to be processed — used by the backend-choice cost/equivalence probes without
    materialising the stack."""
    from pycat.file_io.stack_access import iter_frames as _itf
    return next(iter(_itf(bead_stack, indices=frame_indices)))[1]


def _choose_detection_backend(bead_stack, frame_indices, n_frames, *, quality_mode, use_gpu, parallel,
                              n_workers, host_mask, min_sigma, max_sigma, num_sigma, threshold, variant):
    """Pick the detection execution tier for THIS stack and return ``(tier, gpu_on, src_desc, max_workers)``.

    No fixed preference order — each candidate (GPU / CPU-process-pool / serial) is COSTED and the cheapest
    wins (see `_choose_detection_tier`). The GPU equivalence guard runs whenever the GPU is a candidate,
    even if the pool ends up winning — "never trust a mismatching GPU" is a correctness rule, memoised so
    it is free. The pool is a candidate only for the fast path on multi-frame stacks whose variant does not
    need per-detection sigma / a stack-level mask. **Path OUTCOME is behaviour: the equivalence guards pin
    that GPU/pool/serial produce identical blobs.**"""
    # Is the GPU a CANDIDATE?
    gpu_ok = False
    if quality_mode == 'fast' and use_gpu in ('auto', 'gpu', True, 'true'):
        try:
            from pycat.toolbox.gpu_utils import gpu_available
            gpu_ok = bool(gpu_available())
        except Exception:
            gpu_ok = False
        if gpu_ok:
            gpu_ok = gpu_matches_cpu(
                lambda: _bead_first_frame(bead_stack, frame_indices),
                min_sigma=min_sigma, max_sigma=max_sigma,
                num_sigma=num_sigma, threshold=threshold, host_mask=host_mask)

    # Is the POOL a candidate? Ring-merge needs per-detection sigma (not carried by the worker) and
    # hot-pixel reject filters against a stack-level mask, so both stay serial.
    src_desc = _bead_source_descriptor(bead_stack) if quality_mode == 'fast' else None
    try:
        import os as _os
        max_workers = n_workers or max(1, min(8, (_os.cpu_count() or 2) - 1))
    except Exception:
        max_workers = 1
    pool_ok = (quality_mode == 'fast'
               and variant not in ('ring_merge', 'hot_pixel_reject')
               and parallel in ('auto', 'cpu', 'process')
               and src_desc is not None and max_workers >= 2
               and bool(n_frames) and n_frames > 1)

    # An explicit request is the caller telling us which tier they want; only 'auto' must justify itself.
    if use_gpu in ('gpu', True, 'true') and gpu_ok:
        tier = 'gpu'
    elif parallel in ('cpu', 'process') and pool_ok:
        tier = 'pool'
    elif not gpu_ok and not pool_ok:
        tier = 'serial'
    else:
        _t_ser, _t_gpu = _frame_costs_s(
            _bead_first_frame(bead_stack, frame_indices), gpu_ok=gpu_ok, min_sigma=min_sigma,
            max_sigma=max_sigma, num_sigma=num_sigma, threshold=threshold, host_mask=host_mask)
        tier = _choose_detection_tier(
            n_frames=n_frames, t_ser=_t_ser, t_gpu=_t_gpu, workers=max_workers,
            gpu_ok=gpu_ok, pool_ok=pool_ok)
        debug_log(
            f'VPT tier: {tier} for {n_frames} x {tuple(getattr(bead_stack, "shape", ()))[-2:]} '
            f'(serial {_t_ser*1000:.0f} ms/frame'
            + (f', GPU {_t_gpu*1000:.0f} ms/frame' if _t_gpu else '')
            + (f', pool ~{_t_ser/_pool_speedup(max_workers)*1000:.0f} ms/frame + '
               f'{_pool_spawn_cost_s():.1f} s spawn' if pool_ok else '') + ')', None)
    return tier, (tier == 'gpu'), src_desc, max_workers


def _pool_predetect(src_desc, frame_indices, n_frames, max_workers, *, min_sigma, max_sigma, num_sigma,
                    threshold, host_mask, merge_radius_px, progress_callback):
    """Detect coordinates for every frame on a process pool, or ``None`` on any failure (→ serial fallback).
    Reports progress DURING detection (the expensive phase), mapped to the first 70% of the bar so the
    subsequent cheap scoring loop continues from there rather than restarting at 0."""
    try:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        det_kwargs = dict(min_sigma=min_sigma, max_sigma=max_sigma,
                          num_sigma=num_sigma, threshold=threshold, host_mask=host_mask)
        idxs = (list(frame_indices) if frame_indices is not None else list(range(n_frames)))
        tasks = [(t, src_desc, True, det_kwargs, merge_radius_px) for t in idxs]
        precomputed_coords = {}
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            _n_par = len(tasks)
            futures = [ex.submit(_detect_frame_worker, task) for task in tasks]
            _done_par = 0
            for fut in as_completed(futures):
                t, coords = fut.result()
                precomputed_coords[t] = coords
                _done_par += 1
                if progress_callback is not None and _n_par:
                    progress_callback(int(_done_par / _n_par * 0.70 * max(1, n_frames)),
                                      max(1, n_frames))
        return precomputed_coords
    except Exception:
        return None      # pickling / worker crash / non-picklable host_mask → serial detection


def _fast_frame_rows(frame, t, coords, template_z, *, template_mode, template_type, half, subpixel,
                     microns_per_pixel, nominal_area, hot_mask):
    """The fast-path scoring for one frame: (re)build the PSF template as needed, score the coords, and
    build the per-bead rows. Returns ``(rows, template_z)`` — template_z threads across frames so the
    per_stack template is built once."""
    if template_z is None or template_mode == 'per_frame':
        if template_type == 'airy':
            template_z = build_airy_template(half)
        else:
            tz, _h = build_bead_template(frame, coords, half=half)
            if tz is not None:
                template_z = tz
    scored = score_beads_template(frame, coords, template_z, half=half, subpixel=subpixel)
    rows = []
    for i, b in enumerate(scored):
        _row = {
            'frame': t, 'object_id': i,
            'y_um': float(b['y']) * microns_per_pixel,
            'x_um': float(b['x']) * microns_per_pixel,
            'area_um2': nominal_area,
            'ncc': b['ncc'], 'snr': b['snr'], 'symmetry': b['symmetry'],
            'amplitude': b['amplitude'],
            'integrated_intensity': b['integrated_intensity']}
        # Flag a detection on a fixed sensor hot pixel so the classifier applies a HARSHER acceptance
        # test there (not a flat reject — a real bead can drift over a hot/dead pixel).
        if hot_mask is not None:
            yi = int(round(b['y'])); xi = int(round(b['x']))
            if 0 <= yi < hot_mask.shape[0] and 0 <= xi < hot_mask.shape[1]:
                _row['on_hot_pixel'] = bool(hot_mask[yi, xi])
        rows.append(_row)
    return rows, template_z


def _precise_frame_rows(beads, t, *, microns_per_pixel, nominal_area):
    """The Gaussian-fit-path rows for one frame — area from the fitted sigmas when finite, else nominal."""
    rows = []
    for i, b in enumerate(beads):
        if np.isfinite(b.get('sigma_x', np.nan)) and np.isfinite(b.get('sigma_y', np.nan)):
            area = float(np.pi * b['sigma_x'] * b['sigma_y'] * microns_per_pixel ** 2)
        else:
            area = nominal_area
        rows.append({
            'frame': t, 'object_id': i,
            'y_um': float(b['y']) * microns_per_pixel,
            'x_um': float(b['x']) * microns_per_pixel,
            'area_um2': area,
            'sigma_x': b['sigma_x'], 'sigma_y': b['sigma_y'],
            'sigma_mean': b['sigma_mean'], 'amplitude': b['amplitude'],
            'integrated_intensity': b['integrated_intensity'],
            'r_squared': b['r_squared']})
    return rows


def _assemble_detections(rows, *, quality_mode, strictness, variant, exclude_aggregates,
                         recover_out_of_plane):
    """Build the detection DataFrame from the per-frame rows, classify the beads, and apply the optional
    class filters. 'baseline' is the 1.5.329-validated classifier (recovers ~8.325 through TrackMate); the
    variant is recorded on the frame for auditability. An empty run returns the correct empty schema."""
    if not rows:
        cols = ['frame', 'object_id', 'y_um', 'x_um', 'area_um2']
        if quality_mode == 'fast':
            cols += ['ncc', 'snr', 'symmetry', 'amplitude',
                     'integrated_intensity', 'n_units_est', 'bead_class', 'singlet']
        else:
            cols += ['sigma_x', 'sigma_y', 'sigma_mean', 'amplitude',
                     'integrated_intensity', 'r_squared', 'n_units_est', 'bead_class', 'singlet']
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rows)
    df = classify_beads(df, strictness=strictness, variant=variant)
    df.attrs['detection_variant'] = variant
    if exclude_aggregates:
        df = df[df['bead_class'] != 'aggregate'].reset_index(drop=True)
    if not recover_out_of_plane:
        df = df[df['bead_class'] != 'out_of_plane'].reset_index(drop=True)
    return df


def _bead_hot_mask(bead_stack, variant, progress_callback):
    """The fixed-sensor hot-pixel mask for the ``hot_pixel_reject`` variant, built ONCE from the stack's
    temporal statistics (scene-independent), or ``None``. A build failure degrades to no mask, never a
    crash — the reject is an opt-in robustness variant, not the detection itself."""
    if variant != 'hot_pixel_reject':
        return None
    try:
        hot_mask = build_hot_pixel_mask(bead_stack)
        _n_hot = int(hot_mask.sum()) if hot_mask is not None else 0
        if progress_callback is None:
            print(f"[PyCAT VPT] hot_pixel_reject: flagged {_n_hot} fixed "
                  f"sensor pixels from temporal statistics.")
        return hot_mask
    except Exception as _e:
        print(f"[PyCAT VPT] hot-pixel mask failed ({_e}); proceeding without.")
        return None


def _detect_all_frames(bead_stack, frame_indices, precomputed_coords, template_z, *, quality_mode,
                       variant, gpu_on, template_mode, template_type, half, subpixel, microns_per_pixel,
                       nominal_area, hot_mask, min_sigma, max_sigma, num_sigma, threshold, host_mask,
                       merge_radius_px, fit_window, progress_callback, n_frames):
    """Stream the stack one frame at a time and build the per-bead rows for the whole movie. The fast
    path detects coords (using the pool's precomputed set when present), (re)builds the PSF template
    threaded across frames, and scores; the precise path runs a Gaussian fit. Progress is reported per
    frame — mapped to the 70→100% tail when the pool pre-detected (which filled the first 70%)."""
    from pycat.file_io.stack_access import iter_frames
    rows = []
    done = 0
    for t, frame in iter_frames(bead_stack, indices=frame_indices):
        if quality_mode == 'fast':
            if precomputed_coords is not None and t in precomputed_coords:
                coords = precomputed_coords[t]
            elif variant == 'ring_merge':
                coords, _sig = detect_beads_frame(
                    frame, min_sigma=min_sigma, max_sigma=max_sigma,
                    num_sigma=num_sigma, threshold=threshold,
                    host_mask=host_mask, use_gpu=gpu_on, return_sigma=True)
                coords = dedup_detections_ring_merge(
                    coords, frame, sigmas=_sig, base_radius_px=merge_radius_px)
            else:
                coords = detect_beads_frame(
                    frame, min_sigma=min_sigma, max_sigma=max_sigma,
                    num_sigma=num_sigma, threshold=threshold,
                    host_mask=host_mask, use_gpu=gpu_on)
                if merge_radius_px:
                    coords = dedup_detections(coords, frame, merge_radius_px)
            _frows, template_z = _fast_frame_rows(
                frame, t, coords, template_z, template_mode=template_mode,
                template_type=template_type, half=half, subpixel=subpixel,
                microns_per_pixel=microns_per_pixel, nominal_area=nominal_area, hot_mask=hot_mask)
            rows.extend(_frows)
        else:
            beads = detect_beads_frame(
                frame, min_sigma=min_sigma, max_sigma=max_sigma,
                num_sigma=num_sigma, threshold=threshold, host_mask=host_mask,
                fit_quality=True, fit_window=fit_window,
                fast_fit=(quality_mode == 'fast_fit'))
            rows.extend(_precise_frame_rows(beads, t, microns_per_pixel=microns_per_pixel,
                                            nominal_area=nominal_area))
        done += 1
        if progress_callback is not None:
            if precomputed_coords is not None:
                _val = int(0.70 * n_frames + (done / max(1, n_frames)) * 0.30 * n_frames)
                progress_callback(min(_val, n_frames), n_frames)
            else:
                progress_callback(done, n_frames)
    return rows


@tags_layer('bead_detect', role='overlay', inputs=('image',),
            summary='Bead detection across a stack (blob LoG)', target='bead')
def detect_beads_stack(
    bead_stack: np.ndarray,
    host_mask: Optional[np.ndarray] = None,
    min_sigma: float = 1.0,
    max_sigma: float = 5.0,
    num_sigma: int = 5,
    threshold: float = 0.02,
    microns_per_pixel: float = 1.0,
    fit_quality: bool = False,
    exclude_aggregates: bool = False,
    recover_out_of_plane: bool = True,
    fit_window: int = 9,
    progress_callback=None,
    frame_indices=None,
    quality_mode: str = 'fast',
    template_mode: str = 'per_stack',
    subpixel: bool = True,
    bead_size_nm: Optional[float] = None,
    template_type: str = 'empirical',
    merge_radius_px: Optional[float] = None,
    refine_with_airy: bool = False,
    parallel: str = 'auto',
    n_workers: Optional[int] = None,
    strictness: float = 1.0,
    use_gpu: str = 'auto',
    detection_variant: str = 'baseline',
) -> pd.DataFrame:
    """
    Detect beads across all frames of a (T, H, W) stack.

    Frames are read and processed ONE AT A TIME (streamed via iter_frames), so
    a long movie is never fully held in memory. Pass a lazy stack wrapper (e.g.
    a napari layer's .data) directly — do not pre-materialise it.

    Quality modes (speed vs. precision trade-off):
      'fast'     — empirical-PSF template + cross-correlation scoring. No
                   per-bead nonlinear fit; ~microseconds/bead. Default. Gives
                   classification (singlet/aggregate/out-of-plane) and, with
                   subpixel=True, a cheap centroid centre.
      'fast_fit' — bounded Gaussian fit with a tight iteration cap (fast but
                   still a real fit; good centres + sigmas at moderate cost).
      'precise'  — full Gaussian fit (highest precision, slowest). Use when
                   sub-pixel localisation precision genuinely matters.

    template_mode ('fast' only): 'per_stack' builds one PSF template from the
    first processed frame (fastest; correct when the PSF is stable). 'per_frame'
    rebuilds the template each frame (adapts to focus drift; useful for SMLM-
    like data). subpixel toggles cheap centroid refinement in 'fast' mode.

    The legacy fit_quality=True is honoured as an alias for quality_mode
    ='precise' (backwards compatibility).

    frame_indices : optional iterable of frame indices to process (e.g. a
        keyframe subset for host inference). The 'frame' column uses ORIGINAL
        indices so subsetting stays traceable.

    Returns
    -------
    props_df : DataFrame with columns frame, object_id, y_um, x_um, area_um2
        (+ quality columns depending on mode). Schema is compatible with the
        trajectory linkers and classify_beads.
    """
    from pycat.file_io.stack_access import iter_frames

    # Back-compat: fit_quality=True means the caller wants a real fit.
    if fit_quality and quality_mode == 'fast':
        quality_mode = 'precise'

    # Determine the frame count for progress reporting without materialising.
    shp = getattr(bead_stack, 'shape', None)
    if shp is not None and len(shp) == 3:
        n_frames = len(list(frame_indices)) if frame_indices is not None else shp[0]
    else:
        n_frames = 1

    rows = []
    nominal_area = float(np.pi * (max_sigma * np.sqrt(2) * microns_per_pixel) ** 2)
    half = max(2, fit_window // 2)
    # If a physical bead size is given, size the template patch from it (so it
    # can span the Airy ring). Overrides the fit_window-derived half.
    if bead_size_nm:
        try:
            half = bead_half_from_size(bead_size_nm, microns_per_pixel, n_rings=1)
        except Exception:
            pass
    template_z = None  # built lazily on first frame in 'fast' + per_stack mode

    _variant = (detection_variant or 'baseline').lower()

    # Choose the execution tier (GPU / process-pool / serial), costed for this stack; the equivalence
    # guards pin that all three produce identical blobs.
    _tier, gpu_on, _src_desc, _max_workers = _choose_detection_backend(
        bead_stack, frame_indices, n_frames, quality_mode=quality_mode, use_gpu=use_gpu,
        parallel=parallel, n_workers=n_workers, host_mask=host_mask, min_sigma=min_sigma,
        max_sigma=max_sigma, num_sigma=num_sigma, threshold=threshold, variant=_variant)

    _hot_mask = _bead_hot_mask(bead_stack, _variant, progress_callback)

    precomputed_coords = None
    if _tier == 'pool':
        precomputed_coords = _pool_predetect(
            _src_desc, frame_indices, n_frames, _max_workers, min_sigma=min_sigma,
            max_sigma=max_sigma, num_sigma=num_sigma, threshold=threshold, host_mask=host_mask,
            merge_radius_px=merge_radius_px, progress_callback=progress_callback)

    rows = _detect_all_frames(
        bead_stack, frame_indices, precomputed_coords, template_z, quality_mode=quality_mode,
        variant=_variant, gpu_on=gpu_on, template_mode=template_mode, template_type=template_type,
        half=half, subpixel=subpixel, microns_per_pixel=microns_per_pixel, nominal_area=nominal_area,
        hot_mask=_hot_mask, min_sigma=min_sigma, max_sigma=max_sigma, num_sigma=num_sigma,
        threshold=threshold, host_mask=host_mask, merge_radius_px=merge_radius_px,
        fit_window=fit_window, progress_callback=progress_callback, n_frames=n_frames)

    return _assemble_detections(
        rows, quality_mode=quality_mode, strictness=strictness, variant=_variant,
        exclude_aggregates=exclude_aggregates, recover_out_of_plane=recover_out_of_plane)
