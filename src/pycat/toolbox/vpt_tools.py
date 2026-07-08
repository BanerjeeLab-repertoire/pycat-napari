"""
PyCAT Video Particle Tracking (VPT) Tools
==========================================
Microrheology by tracking fluorescent probe beads (20 nm - 2 µm) diffusing
inside an in-vitro biomolecular condensate (host phase).

Pipeline
--------
1. Segment the host condensate system (one fluorescence channel).
2. Erode the condensate mask inward to exclude beads near the condensate
   interface — interface dynamics (fusion, flow, surface tension gradients)
   corrupt the assumption of pure thermal diffusion in the bulk.
3. Detect beads (a second fluorescence channel, typically green but any color)
   frame-by-frame via Laplacian-of-Gaussian blob detection, keeping only
   beads inside the eroded host mask.
4. Link bead detections into trajectories (TrackMate LAP by default, or one
   of PyCAT's native linkers).
5. Drift-correct via ensemble center-of-mass subtraction (removes stage drift
   and bulk condensate translation/flow).
6. Compute per-track and ensemble MSD, fit MSD(τ) = 4Dτ^α, and derive
   viscosity via the Stokes-Einstein relation η = kT / (6πRD).

This mirrors the established manual workflow (load TrackMate XML → COM drift
correction → per-track MSD → ensemble fit → Stokes-Einstein) but runs
end-to-end from raw multichannel image data within PyCAT.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2026
"""

from __future__ import annotations

import warnings
from typing import Optional

import numpy as np
import pandas as pd

import skimage as sk
import scipy.ndimage as ndi

from napari.utils.notifications import show_info as napari_show_info
from napari.utils.notifications import show_warning as napari_show_warning


# Boltzmann constant (J/K)
_K_BOLTZMANN = 1.38064852e-23


# ---------------------------------------------------------------------------
# 1-2. Host condensate segmentation + interface erosion
# ---------------------------------------------------------------------------

def segment_host_condensate(
    host_image: np.ndarray,
    method: str = 'otsu',
    min_area_px: int = 50,
    fill_holes: bool = True,
) -> np.ndarray:
    """
    Segment the host condensate phase from a single fluorescence channel.

    Parameters
    ----------
    host_image : 2D array (single frame) or 3D array (T, H, W).
        For a time series the maximum-intensity projection across time is
        used to define a single stable host mask, on the assumption the
        condensate is roughly stationary (drift is corrected separately at
        the trajectory stage). If the condensate moves substantially, pass
        a single representative frame instead.
    method : 'otsu' | 'triangle' | 'li'
        Global threshold method from skimage.filters.
    min_area_px : remove connected components smaller than this.
    fill_holes : fill interior holes in the condensate mask.

    Returns
    -------
    labeled_mask : 2D int array — connected components of the host phase,
        one integer label per distinct condensate.
    """
    img = np.asarray(host_image)
    if img.ndim == 3:
        # Max-projection across time gives a stable host footprint
        proj = img.max(axis=0)
    else:
        proj = img
    proj = proj.astype(np.float32)

    # Normalise to [0,1] for stable thresholding
    mn, mx = float(proj.min()), float(proj.max())
    if mx > mn:
        proj = (proj - mn) / (mx - mn)

    method = method.lower()
    if method == 'triangle':
        thresh = sk.filters.threshold_triangle(proj)
    elif method == 'li':
        thresh = sk.filters.threshold_li(proj)
    else:
        thresh = sk.filters.threshold_otsu(proj)

    binary = proj > thresh
    if fill_holes:
        binary = ndi.binary_fill_holes(binary)

    labeled = sk.measure.label(binary)
    if min_area_px > 0:
        labeled = sk.morphology.remove_small_objects(labeled, min_size=min_area_px)
        labeled = sk.measure.label(labeled > 0)  # relabel contiguous

    return labeled.astype(np.int32)


def erode_host_mask(
    labeled_mask: np.ndarray,
    erosion_px: int = 5,
) -> np.ndarray:
    """
    Erode each labeled condensate inward to exclude a border region.

    Beads within ~erosion_px of the condensate edge are excluded from
    tracking because interface dynamics (fusion, surface flow, capillary
    fluctuations) violate the bulk-diffusion assumption underlying the
    MSD → viscosity conversion.

    Erosion is done per-label so that touching/nearby condensates don't
    merge or bleed into one another. Labels that erode away entirely
    (smaller than the erosion diameter) are dropped, with a warning.

    Parameters
    ----------
    labeled_mask : 2D int array from segment_host_condensate().
    erosion_px : erosion depth in pixels (radius of the disk structuring
        element). Choose ~1-2× the bead radius plus a safety margin.

    Returns
    -------
    eroded_labeled : 2D int array — same labels, each shrunk inward.
    """
    if erosion_px <= 0:
        return labeled_mask.copy()

    struct = sk.morphology.disk(erosion_px)
    out = np.zeros_like(labeled_mask)
    dropped = 0
    for lbl in np.unique(labeled_mask):
        if lbl == 0:
            continue
        single = (labeled_mask == lbl)
        eroded = ndi.binary_erosion(single, structure=struct)
        if eroded.sum() == 0:
            dropped += 1
            continue
        out[eroded] = lbl

    if dropped > 0:
        napari_show_warning(
            f"{dropped} condensate(s) were smaller than the erosion depth "
            f"({erosion_px}px) and were dropped entirely. Reduce the erosion "
            f"depth to keep small condensates.")

    return out


# ---------------------------------------------------------------------------
# 2b. Infer an unlabelled host from the bead distribution (Mode C)
# ---------------------------------------------------------------------------

def infer_host_from_beads(
    bead_coords: np.ndarray,
    image_shape: tuple,
    microns_per_pixel: float = 1.0,
    min_condensate_radius_um: float = 5.0,
    density_percentile: float = 60.0,
    bin_px: int = 8,
    smooth_sigma: float = 3.0,
    min_beads_per_region: int = 8,
) -> np.ndarray:
    """
    Infer a host-condensate mask from the spatial distribution of detected
    beads, for data where the condensate is real but unlabelled (no companion
    host channel).

    This is a combined density + geometry + physics method:

    1. **Density backbone.** Bead centroids are binned into a coarse density
       map and smoothed, turning discrete detections into a continuous
       concentration field. A percentile threshold on this field picks out
       regions of elevated bead concentration; a distance-transform watershed
       then separates condensates that touch (so neighbouring condensates are
       not merged into one region).
    2. **Geometry validation.** Each region is checked for internal bead
       content — a genuine condensate is densely populated with beads, not a
       sparse threshold artefact.
    3. **Physics gate.** Only condensates large enough for bulk (boundary-free)
       diffusion are kept: beads in a condensate that is too shallow feel the
       interface and do not report bulk viscosity. Regions clipped by the frame
       edge have their *true* radius estimated by fitting a circle to their
       non-border boundary (the visible interface arc), so a large condensate
       that is only partly in frame is still retained.

    The result is an INFERRED boundary — it follows the bead distribution, not
    a directly imaged condensate edge — and should be reported as such.

    Parameters
    ----------
    bead_coords : (N, 2) array of bead (y, x) pixel centroids, pooled across
        frames (the host is treated as stationary; drift is corrected later).
    image_shape : (H, W) of the source frames.
    microns_per_pixel : pixel size, for the physical size gate.
    min_condensate_radius_um : minimum condensate radius for a bead to sample
        bulk diffusion without boundary effects. Regions smaller than this
        (after edge-projection) are discarded.
    density_percentile : percentile of the non-zero density field used as the
        inclusion threshold. Lower keeps more/larger regions but risks merging.
    bin_px : density-map bin size in source pixels (coarsening + smoothing).
    smooth_sigma : Gaussian smoothing sigma on the density map (in bins).
    min_beads_per_region : reject regions with fewer beads than this.

    Returns
    -------
    labeled_mask : 2D int array at the FULL image resolution — one integer
        label per retained (large-enough) condensate, 0 = background.
        Empty (all-zero) if no region passes the physics gate.
    """
    H, W = int(image_shape[0]), int(image_shape[1])
    coords = np.asarray(bead_coords, dtype=float)
    if coords.ndim != 2 or coords.shape[0] < min_beads_per_region:
        return np.zeros((H, W), dtype=np.int32)

    # --- Stage 1: bead-density map + threshold + watershed separation --------
    dh, dw = max(H // bin_px, 1), max(W // bin_px, 1)
    yi = np.clip((coords[:, 0] / bin_px).astype(int), 0, dh - 1)
    xi = np.clip((coords[:, 1] / bin_px).astype(int), 0, dw - 1)
    density = np.zeros((dh, dw), dtype=np.float32)
    np.add.at(density, (yi, xi), 1.0)
    density = ndi.gaussian_filter(density, sigma=smooth_sigma)

    nz = density[density > 0]
    if nz.size == 0:
        return np.zeros((H, W), dtype=np.int32)
    thr = float(np.percentile(nz, density_percentile))
    mask = density > thr
    mask = sk.morphology.remove_small_objects(mask, 5)
    mask = ndi.binary_fill_holes(mask)
    if not mask.any():
        return np.zeros((H, W), dtype=np.int32)

    dist = ndi.distance_transform_edt(mask)
    peaks = sk.feature.peak_local_max(dist, min_distance=10, labels=mask)
    seeds = np.zeros(mask.shape, dtype=int)
    for i, (py, px) in enumerate(peaks):
        seeds[py, px] = i + 1
    if seeds.max() == 0:
        seeds, _ = ndi.label(mask)
    ws = sk.segmentation.watershed(-dist, seeds, mask=mask)

    # --- Stage 3: physics gate (with rigorous edge-clip projection) ----------
    um_per_dbin = bin_px * microns_per_pixel
    coords_d = coords / bin_px
    yid = np.clip(coords_d[:, 0].astype(int), 0, dh - 1)
    xid = np.clip(coords_d[:, 1].astype(int), 0, dw - 1)

    keep_labels = []
    for lbl in range(1, int(ws.max()) + 1):
        reg = (ws == lbl)
        area = int(reg.sum())
        if area < 3:
            continue
        n_beads = int(reg[yid, xid].sum())
        if n_beads < min_beads_per_region:
            continue

        touches_edge = (reg[0, :].any() or reg[-1, :].any()
                        or reg[:, 0].any() or reg[:, -1].any())
        if touches_edge:
            radius_dbin = _fit_clipped_radius(reg)
        else:
            radius_dbin = np.sqrt(area / np.pi)
        radius_um = radius_dbin * um_per_dbin
        if radius_um >= min_condensate_radius_um:
            keep_labels.append(lbl)

    if not keep_labels:
        return np.zeros((H, W), dtype=np.int32)

    # Relabel kept regions 1..K and upsample to full resolution
    small = np.zeros(ws.shape, dtype=np.int32)
    for new_id, lbl in enumerate(keep_labels, start=1):
        small[ws == lbl] = new_id
    full = np.repeat(np.repeat(small, bin_px, axis=0), bin_px, axis=1)
    # Pad/crop to exact (H, W)
    out = np.zeros((H, W), dtype=np.int32)
    fh, fw = full.shape
    out[:min(H, fh), :min(W, fw)] = full[:min(H, fh), :min(W, fw)]
    return out


def _fit_clipped_radius(reg: np.ndarray) -> float:
    """Estimate the true radius of an edge-clipped region by fitting a circle
    to its non-border boundary (the real interface arc, excluding the straight
    frame-edge cut). Falls back to the equivalent-area radius on a poor fit."""
    H, W = reg.shape
    perim = reg & ~ndi.binary_erosion(reg)
    ys, xs = np.where(perim)
    keep = (ys > 0) & (ys < H - 1) & (xs > 0) & (xs < W - 1)
    ys, xs = ys[keep], xs[keep]
    eqrad = np.sqrt(reg.sum() / np.pi)
    if len(ys) < 8:
        return eqrad
    # Algebraic (Kåsa) circle fit: x^2 + y^2 + D x + E y + F = 0
    A = np.c_[xs, ys, np.ones(len(xs))]
    b = -(xs.astype(float) ** 2 + ys.astype(float) ** 2)
    try:
        D, E, F = np.linalg.lstsq(A, b, rcond=None)[0]
        cx, cy = -D / 2.0, -E / 2.0
        r = np.sqrt(max(cx ** 2 + cy ** 2 - F, 0.0))
        if not np.isfinite(r) or r <= 0 or r > max(H, W):
            return eqrad
        # Never return smaller than what we already see
        return max(r, eqrad)
    except Exception:
        return eqrad


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
        return np.empty((0, 2))

    coords = blobs[:, :2]  # (y, x)

    if host_mask is not None:
        hm = np.asarray(host_mask) > 0
        keep = []
        for (y, x) in coords:
            yi, xi = int(round(y)), int(round(x))
            if 0 <= yi < hm.shape[0] and 0 <= xi < hm.shape[1] and hm[yi, xi]:
                keep.append((y, x))
        coords = np.array(keep) if keep else np.empty((0, 2))

    if not fit_quality:
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


def classify_beads(beads_df: pd.DataFrame,
                   aggregate_intensity_factor: float = 1.6,
                   defocus_r2_max: float = 0.85,
                   sigma_outlier_factor: float = 1.5,
                   strictness: float = 1.0) -> pd.DataFrame:
    """
    Classify fitted beads into singlet / aggregate / out-of-plane using the
    2D-Gaussian quality metrics.

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
    defocus_r2_max : fit-R² below which an oversized, non-brighter bead is
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

    # Fast-mode classification: when the fast template scorer was used, we have
    # ncc / snr / symmetry / integrated_intensity but no Gaussian r_squared.
    # Classify from those instead: a singlet matches the template well (high
    # ncc), is symmetric, and has near-singlet integrated intensity; an
    # aggregate is much brighter; a poor template match (low ncc/symmetry) that
    # isn't brighter is treated as out-of-plane/unfit.
    if 'r_squared' not in df.columns and 'ncc' in df.columns:
        # Fast-mode classification, calibrated for large (non-diffraction-limited)
        # Airy-disk beads where a real single bead is BRIGHT and high-mass.
        #
        # Four tiers:
        #   rejected     : poor template match (ncc below a floor) — these are
        #                  Airy-ring fragments, hot pixels, and noise. DROPPED
        #                  entirely (never become points), not just labelled.
        #   aggregate    : a real bead that is BRIGHT and COMPACT and HIGH-MASS
        #                  (top mass tail AND high amplitude). Requiring BOTH is
        #                  what separates a true aggregate from an out-of-focus
        #                  blob (which is high-mass but DIM/diffuse).
        #   ambiguous    : high-mass but dim/diffuse (out of focus) — too
        #                  uncertain to call singlet or aggregate; flagged so the
        #                  software is honest about not knowing.
        #   singlet      : every other well-matched real bead (the large majority).
        ncc = df['ncc'].to_numpy(dtype=float)
        amp = df['amplitude'].to_numpy(dtype=float)
        ii = df['integrated_intensity'].to_numpy(dtype=float)
        snr = (df['snr'].to_numpy(dtype=float) if 'snr' in df
               else np.full(len(df), np.nan))

        # Real-vs-garbage: absolute NCC floor. The template is built FROM the
        # real beads, so genuine beads match it well; rings/hot/noise do not.
        # 0.55 (rather than 0.50) reduces frame-to-frame flicker: dim detections
        # whose NCC hovers right at the floor were dropping in and out between
        # frames as their score wobbled; a slightly firmer floor keeps that
        # borderline-noise population consistently rejected.
        NCC_FLOOR = 0.55
        is_real = np.isfinite(ncc) & (ncc >= NCC_FLOOR)

        # References computed over REAL beads only (so garbage doesn't skew them).
        rii = ii[is_real & np.isfinite(ii)]
        ramp = amp[is_real & np.isfinite(amp)]
        rsnr = snr[is_real & np.isfinite(snr)] if 'snr' in df else np.array([])
        if len(rii) >= 10:
            singlet_int = float(np.median(rii[rii <= np.median(rii)]))
            # Aggregate mass gate at p99.3 (not p99.5): p99.5 landed INSIDE the
            # top mass cluster, so a genuine aggregate whose mass fluctuates a
            # few percent frame-to-frame kept crossing it and flickered
            # red/green. p99.3 sits just BELOW that cluster, so the handful of
            # true aggregates stay solidly above it every frame (stable class).
            mass_hi = float(np.percentile(rii, 99.3))
            amp_hi = float(np.percentile(ramp, 50))     # must also be bright
        else:
            singlet_int = float(np.median(rii)) if len(rii) else np.nan
            mass_hi = np.inf; amp_hi = np.inf

        # Dim / out-of-focus (YELLOW) threshold. Dim detections — low amplitude
        # relative to the population — are most likely beads drifting out of the
        # focal plane; they belong in the out_of_plane (yellow) bin, not called
        # singlets. The cutoff is a low-amplitude percentile scaled by
        # `strictness`: strictness=1.0 (default, tuned for viscous samples
        # ~3 Pa·s and above, where beads move slowly) uses the 25th percentile;
        # higher strictness pushes more borderline-dim detections to yellow,
        # lower strictness (opt-in for less viscous / faster samples) keeps more
        # as singlets. In a viscous sample most beads stay in focus, so the dim
        # tail is genuinely out-of-plane; in a fast/low-viscosity sample beads
        # cross the plane quickly and a stricter dim gate would wrongly bin real
        # beads, hence the exposed control.
        s = float(strictness) if strictness and strictness > 0 else 1.0
        if len(ramp) >= 10:
            dim_pct = float(np.clip(25.0 * s, 2.0, 60.0))
            amp_dim = float(np.percentile(ramp, dim_pct))
        else:
            amp_dim = -np.inf
        # A low-SNR detection is also out-of-focus-like (weak, diffuse peak).
        if len(rsnr) >= 10:
            snr_pct = float(np.clip(15.0 * s, 2.0, 50.0))
            snr_dim = float(np.percentile(rsnr, snr_pct))
        else:
            snr_dim = -np.inf

        n_units, classes = [], []
        for k in range(len(df)):
            if not is_real[k]:
                n_units.append(np.nan); classes.append('rejected'); continue
            I, A = ii[k], amp[k]
            S = snr[k] if 'snr' in df else np.nan
            nu = I / singlet_int if (singlet_int and singlet_int > 0) else np.nan
            n_units.append(nu)
            high_mass = np.isfinite(I) and I >= mass_hi
            bright = np.isfinite(A) and A >= amp_hi
            # Dim / out-of-focus: low amplitude AND not part of the bright
            # aggregate tail. These go YELLOW (out_of_plane). Blinking of this
            # population across frames is expected and acceptable — a bead that
            # is genuinely in focus and stable will instead read as a singlet;
            # the temporal-stability pass (after linking) promotes stable dim
            # tracks back to singlet.
            is_dim = (np.isfinite(A) and A <= amp_dim) or \
                     (np.isfinite(S) and S <= snr_dim)
            if high_mass and bright:
                classes.append('aggregate')          # bright + compact + heavy
            elif is_dim and not high_mass:
                classes.append('out_of_plane')        # YELLOW: dim / out of focus
            elif high_mass and not bright:
                classes.append('ambiguous')           # heavy but dim/diffuse (OOF)
            else:
                classes.append('singlet')             # the large majority
        df['n_units_est'] = n_units
        df['bead_class'] = classes
        # DROP rejected detections entirely — a marked point should be a real
        # bead (rings/hot pixels/noise never become points).
        df = df[df['bead_class'] != 'rejected'].reset_index(drop=True)
        df['singlet'] = df['bead_class'] == 'singlet'
        return df


    # Robust singlet reference = median of reasonably-fit beads. Use the lower
    # half of the intensity distribution to bias the reference toward singlets
    # (aggregates are the bright minority).
    ref = df.loc[valid, 'integrated_intensity']
    if len(ref) >= 4:
        singlet_int = float(np.median(ref[ref <= ref.median()]))
    elif len(ref) > 0:
        singlet_int = float(ref.median())
    else:
        singlet_int = np.nan
    sig = df.loc[valid, 'sigma_mean']
    singlet_sigma = float(np.median(sig[sig <= sig.median()])) if len(sig) >= 4 else         (float(sig.median()) if len(sig) > 0 else np.nan)

    # Reference peak amplitude of a singlet (lower-half median, like intensity)
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
        # Defocus signature: enlarged spot whose PEAK amplitude is depressed
        # relative to a singlet (photons spread over a wider area), i.e. NOT a
        # true aggregate. Amplitude test is primary; poor R² reinforces it.
        dim_peak = (np.isfinite(A) and np.isfinite(singlet_amp)
                    and singlet_amp > 0 and A < 0.7 * singlet_amp)
        if brighter and not dim_peak:
            classes.append('aggregate')
        elif oversized and (dim_peak or r2 < defocus_r2_max):
            classes.append('out_of_plane')
        else:
            classes.append('singlet')
    df['n_units_est'] = n_units
    df['bead_class'] = classes
    df['singlet'] = df['bead_class'] == 'singlet'
    return df


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
    from pycat.file_io.file_io import iter_frames

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

    # ── Optional CPU-parallel pre-detection (fast mode only) ─────────────────
    # The expensive, embarrassingly-parallel part is per-frame blob detection.
    # When enabled and the stack is file-backed (so workers can re-open it),
    # detect coords for all frames across a process pool up front, then do the
    # cheap scoring/classification serially below (where the shared template
    # lives). Falls back cleanly to serial if anything is unavailable.
    # ── Tier selection: GPU > CPU-parallel > serial ─────────────────────────
    # GPU (LoG convolutions on-device) is the biggest single-machine win and is
    # done IN-PROCESS: we do not also spin up a process pool, because the pool
    # workers would contend for the one GPU. So GPU takes priority; only when no
    # GPU is present do we consider the CPU process-pool path.
    gpu_on = False
    if quality_mode == 'fast' and use_gpu in ('auto', 'gpu', True, 'true'):
        try:
            from pycat.toolbox.gpu_utils import gpu_available
            gpu_on = bool(gpu_available())
        except Exception:
            gpu_on = False
        if gpu_on:
            # Equivalence guard: verify the GPU blob detector matches the CPU
            # (skimage) detector on the FIRST frame before trusting it for the
            # whole stack. If they disagree (a driver/cupy quirk), fall back to
            # CPU so results are never silently wrong. Runs once, cheap.
            try:
                from pycat.file_io.file_io import iter_frames as _itf
                _t0, _f0 = next(iter(_itf(bead_stack, indices=frame_indices)))
                _cpu = detect_beads_frame(
                    _f0, min_sigma=min_sigma, max_sigma=max_sigma,
                    num_sigma=num_sigma, threshold=threshold,
                    host_mask=host_mask, use_gpu=False)
                _gpu = detect_beads_frame(
                    _f0, min_sigma=min_sigma, max_sigma=max_sigma,
                    num_sigma=num_sigma, threshold=threshold,
                    host_mask=host_mask, use_gpu=True)
                # Compare as sorted rounded coordinate sets.
                def _key(cs):
                    return sorted((round(float(y), 3), round(float(x), 3))
                                  for (y, x) in cs)
                if _key(_cpu) != _key(_gpu):
                    gpu_on = False   # disagreement → do not trust GPU
            except Exception:
                gpu_on = False

    precomputed_coords = None
    if quality_mode == 'fast' and not gpu_on and parallel in ('auto', 'cpu', 'process'):
        src_desc = _bead_source_descriptor(bead_stack)
        try:
            import os as _os
            max_workers = n_workers or max(1, min(8, (_os.cpu_count() or 2) - 1))
        except Exception:
            max_workers = 1
        # Only worth it with a real descriptor and enough frames + workers.
        if src_desc is not None and max_workers > 1 and n_frames and n_frames > 1:
            try:
                from concurrent.futures import ProcessPoolExecutor
                det_kwargs = dict(min_sigma=min_sigma, max_sigma=max_sigma,
                                  num_sigma=num_sigma, threshold=threshold,
                                  host_mask=host_mask)
                idxs = (list(frame_indices) if frame_indices is not None
                        else list(range(n_frames)))
                tasks = [(t, src_desc, True, det_kwargs, merge_radius_px)
                         for t in idxs]
                precomputed_coords = {}
                with ProcessPoolExecutor(max_workers=max_workers) as ex:
                    for t, coords in ex.map(_detect_frame_worker, tasks):
                        precomputed_coords[t] = coords
            except Exception:
                # Any failure (pickling, worker crash, host_mask not picklable)
                # → fall back to serial detection below.
                precomputed_coords = None

    done = 0
    for t, frame in iter_frames(bead_stack, indices=frame_indices):
        if quality_mode == 'fast':
            if precomputed_coords is not None and t in precomputed_coords:
                # Coords already found in parallel; frame still needed for
                # template building + scoring (both cheap).
                coords = precomputed_coords[t]
            else:
                coords = detect_beads_frame(
                    frame, min_sigma=min_sigma, max_sigma=max_sigma,
                    num_sigma=num_sigma, threshold=threshold, host_mask=host_mask,
                    use_gpu=gpu_on)
                # De-duplicate multi-scale / ring detections on a single bead, if
                # a merge radius is set (from the physical bead size upstream).
                if merge_radius_px:
                    coords = dedup_detections(coords, frame, merge_radius_px)
            if template_z is None or template_mode == 'per_frame':
                if template_type == 'airy':
                    # Analytic Airy (Bessel J1) template — matches beads that
                    # show a resolved ring, so a bead is one object not several.
                    template_z = build_airy_template(half)
                else:
                    tz, _h = build_bead_template(frame, coords, half=half)
                    if tz is not None:
                        template_z = tz
            scored = score_beads_template(frame, coords, template_z,
                                          half=half, subpixel=subpixel)
            for i, b in enumerate(scored):
                rows.append({
                    'frame': t, 'object_id': i,
                    'y_um': float(b['y']) * microns_per_pixel,
                    'x_um': float(b['x']) * microns_per_pixel,
                    'area_um2': nominal_area,
                    'ncc': b['ncc'], 'snr': b['snr'], 'symmetry': b['symmetry'],
                    'amplitude': b['amplitude'],
                    'integrated_intensity': b['integrated_intensity']})
        else:
            beads = detect_beads_frame(
                frame, min_sigma=min_sigma, max_sigma=max_sigma,
                num_sigma=num_sigma, threshold=threshold, host_mask=host_mask,
                fit_quality=True, fit_window=fit_window,
                fast_fit=(quality_mode == 'fast_fit'))
            for i, b in enumerate(beads):
                if np.isfinite(b.get('sigma_x', np.nan)) and np.isfinite(b.get('sigma_y', np.nan)):
                    area = float(np.pi * b['sigma_x'] * b['sigma_y']
                                 * microns_per_pixel ** 2)
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
        done += 1
        if progress_callback is not None:
            progress_callback(done, n_frames)

    if not rows:
        cols = ['frame', 'object_id', 'y_um', 'x_um', 'area_um2']
        if quality_mode == 'fast':
            cols += ['ncc', 'snr', 'symmetry', 'amplitude',
                     'integrated_intensity', 'n_units_est', 'bead_class', 'singlet']
        else:
            cols += ['sigma_x', 'sigma_y', 'sigma_mean', 'amplitude',
                     'integrated_intensity', 'r_squared', 'n_units_est',
                     'bead_class', 'singlet']
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rows)
    df = classify_beads(df, strictness=strictness)

    if exclude_aggregates:
        df = df[df['bead_class'] != 'aggregate'].reset_index(drop=True)
    if not recover_out_of_plane:
        df = df[df['bead_class'] != 'out_of_plane'].reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 4b. Bead population routing (primary probes vs. aggregate secondary set)
# ---------------------------------------------------------------------------

def split_bead_populations(detections_df: pd.DataFrame,
                           recover_out_of_plane: bool = True) -> dict:
    """
    Split classified bead detections into a primary probe population and a
    secondary aggregate population.

    primary   = singlets (+ out-of-plane if recover_out_of_plane) — used for
                microrheology, since Stokes-Einstein assumes a known single
                bead size.
    aggregate = beads classified as aggregates — tracked separately so
                aggregation can be used as its own readout (count, size, and
                mobility over time) rather than discarded.

    Returns
    -------
    dict with 'primary' and 'aggregate' DataFrames. If the input lacks a
    'bead_class' column (quality fit not run), everything is 'primary'.
    """
    df = detections_df
    if df is None or df.empty or 'bead_class' not in df.columns:
        return dict(primary=df if df is not None else pd.DataFrame(),
                    aggregate=pd.DataFrame())
    primary_classes = ['singlet', 'unfit']
    if recover_out_of_plane:
        primary_classes.append('out_of_plane')
    primary = df[df['bead_class'].isin(primary_classes)].reset_index(drop=True)
    # Aggregates AND ambiguous (dim/diffuse, possibly-aggregate) are kept out of
    # the primary microrheology set — an aggregate's size biases Stokes-Einstein
    # viscosity, and an ambiguous bead can't be confirmed as a clean singlet.
    # They are still returned (as the secondary population) rather than dropped.
    secondary = df[df['bead_class'].isin(['aggregate', 'ambiguous'])].reset_index(drop=True)
    return dict(primary=primary, aggregate=secondary)


def aggregate_population_stats(aggregate_df: pd.DataFrame,
                              total_by_frame: Optional[pd.Series] = None) -> pd.DataFrame:
    """
    Per-frame aggregation readout from the aggregate population.

    Parameters
    ----------
    aggregate_df : detections classified as aggregates (with n_units_est,
        integrated_intensity, sigma_mean).
    total_by_frame : optional Series indexed by frame giving the TOTAL number
        of beads (all classes) per frame, so an aggregated fraction can be
        reported.

    Returns
    -------
    DataFrame indexed by frame:
        n_aggregates       : count of aggregate detections
        total_aggregated_units : summed n_units_est (total beads' worth of
                                 signal tied up in aggregates)
        median_aggregate_units : typical aggregate size (in bead-units)
        median_sigma           : typical aggregate width (px)
        aggregated_fraction    : n_aggregates / total beads (if total given)
    """
    if aggregate_df is None or aggregate_df.empty:
        return pd.DataFrame(columns=[
            'frame', 'n_aggregates', 'total_aggregated_units',
            'median_aggregate_units', 'median_sigma', 'aggregated_fraction'])
    g = aggregate_df.groupby('frame')
    cols = {'n_aggregates': g.size()}
    # n_units_est and sigma_mean only exist in FIT detection mode; fast
    # (template) mode does not fit a Gaussian, so guard each column and fill
    # NaN when it is absent rather than raising a KeyError.
    if 'n_units_est' in aggregate_df.columns:
        cols['total_aggregated_units'] = g['n_units_est'].sum(min_count=1)
        cols['median_aggregate_units'] = g['n_units_est'].median()
    else:
        cols['total_aggregated_units'] = np.nan
        cols['median_aggregate_units'] = np.nan
    if 'sigma_mean' in aggregate_df.columns:
        cols['median_sigma'] = g['sigma_mean'].median()
    else:
        cols['median_sigma'] = np.nan
    out = pd.DataFrame(cols)
    if total_by_frame is not None:
        out['aggregated_fraction'] = (out['n_aggregates']
                                      / total_by_frame.reindex(out.index)).astype(float)
    else:
        out['aggregated_fraction'] = np.nan
    return out.reset_index()


# ---------------------------------------------------------------------------
# 5. Ensemble center-of-mass drift correction
# ---------------------------------------------------------------------------

def reclassify_by_temporal_stability(tracks_df, min_stable_len=5,
                                     max_gap_frac=0.25):
    """Promote STABLE dim tracks back to singlet after linking.

    Per-frame classification sends dim detections to out_of_plane (yellow),
    because a dim spot is usually a bead drifting out of the focal plane. But a
    dim spot that is actually a real in-focus bead will appear in (almost) every
    frame of its track — it is stable, not blinking. Once tracks exist we can
    tell the two apart:

      * a dim track that is LONG and has FEW gaps  → a real (if faint) bead →
        promote every detection in it to 'singlet';
      * a dim track that is SHORT or GAPPY (blinks in and out) → genuinely
        out-of-focus → leave as 'out_of_plane' (yellow).

    This is the temporal counterpart to the per-frame classifier: instantaneous
    features cannot distinguish a faint-but-real bead from an out-of-focus one,
    but persistence across frames can. Aggregates and normal singlets are left
    untouched.

    Parameters
    ----------
    tracks_df : linked detections with columns track_id, frame, bead_class.
    min_stable_len : minimum number of frames a dim track must span to be
        considered a stable (real) bead.
    max_gap_frac : maximum fraction of missing frames (gaps) within the track's
        span for it to count as stable rather than blinking.

    Returns the DataFrame with 'bead_class' updated in place (copy returned).
    """
    if tracks_df is None or tracks_df.empty or 'bead_class' not in tracks_df:
        return tracks_df
    if 'track_id' not in tracks_df or 'frame' not in tracks_df:
        return tracks_df
    df = tracks_df.copy()
    for tid, grp in df.groupby('track_id'):
        if tid == -1:
            continue
        classes = grp['bead_class']
        # Only consider tracks that are predominantly dim (out_of_plane).
        dim_frac = float((classes == 'out_of_plane').mean())
        if dim_frac < 0.5:
            continue
        frames = grp['frame'].to_numpy()
        n_present = len(frames)
        span = (frames.max() - frames.min() + 1) if n_present else 0
        gap_frac = 1.0 - (n_present / span) if span > 0 else 1.0
        if n_present >= min_stable_len and gap_frac <= max_gap_frac:
            # Stable, persistent dim track → a real bead. Promote its dim
            # detections to singlet (leave any aggregate frames alone).
            promote = grp.index[grp['bead_class'] == 'out_of_plane']
            df.loc[promote, 'bead_class'] = 'singlet'
    # keep the convenience flag consistent
    if 'singlet' in df.columns:
        df['singlet'] = df['bead_class'] == 'singlet'
    return df


def drift_correct_com(tracks_df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove global drift/flow by subtracting the ensemble center-of-mass
    displacement at each frame — the approach used in the manual workflow.

    For each frame transition the mean displacement of all beads present in
    both frames is accumulated into a running COM trajectory, which is then
    subtracted from every bead position. This removes stage drift and bulk
    condensate translation while preserving each bead's relative thermal
    motion.

    Parameters
    ----------
    tracks_df : DataFrame with columns track_id, frame, y_um, x_um.

    Returns
    -------
    corrected : same DataFrame with y_um, x_um drift-corrected, plus
        original values preserved in y_um_raw, x_um_raw.
    """
    if tracks_df.empty:
        return tracks_df

    df = tracks_df.sort_values(['track_id', 'frame']).copy()
    df['y_um_raw'] = df['y_um']
    df['x_um_raw'] = df['x_um']

    frames = np.sort(df['frame'].unique())
    # Per-frame mean displacement of beads present in consecutive frames
    com_dx = {frames[0]: 0.0}
    com_dy = {frames[0]: 0.0}
    cum_dx, cum_dy = 0.0, 0.0

    for f_prev, f_cur in zip(frames[:-1], frames[1:]):
        prev = df[df['frame'] == f_prev].set_index('track_id')
        cur  = df[df['frame'] == f_cur].set_index('track_id')
        common = prev.index.intersection(cur.index)
        if len(common) > 0:
            dx = (cur.loc[common, 'x_um'] - prev.loc[common, 'x_um']).mean()
            dy = (cur.loc[common, 'y_um'] - prev.loc[common, 'y_um']).mean()
        else:
            dx, dy = 0.0, 0.0
        cum_dx += dx
        cum_dy += dy
        com_dx[f_cur] = cum_dx
        com_dy[f_cur] = cum_dy

    df['x_um'] = df.apply(lambda r: r['x_um'] - com_dx.get(r['frame'], 0.0), axis=1)
    df['y_um'] = df.apply(lambda r: r['y_um'] - com_dy.get(r['frame'], 0.0), axis=1)

    return df


# ---------------------------------------------------------------------------
# 6. Stokes-Einstein viscosity
# ---------------------------------------------------------------------------

def viscosity_from_diffusion(
    D_um2_per_s: float,
    bead_radius_um: float,
    temperature_C: float = 24.0,
) -> float:
    """
    Stokes-Einstein viscosity: η = kT / (6πRD).

    Parameters
    ----------
    D_um2_per_s : diffusion coefficient (µm²/s) from the MSD fit.
    bead_radius_um : probe bead radius in µm.
    temperature_C : temperature in Celsius.

    Returns
    -------
    eta : viscosity in Pa·s. Returns NaN if D or R is non-positive.

    Notes
    -----
    Unit handling: D is converted µm²/s → m²/s (×1e-12) and R µm → m
    (×1e-6). η = kT / (6πRD) then comes out in Pa·s directly. Equivalently,
    combining the constants gives the 1e18 prefactor seen in the manual
    workflow (1e-12 in D and 1e-6 in R together invert to 1e18 when the
    conversions are folded into a single constant on µm-based inputs).
    """
    if D_um2_per_s <= 0 or bead_radius_um <= 0:
        return float('nan')
    T = temperature_C + 273.15
    D_m2 = D_um2_per_s * 1e-12
    R_m  = bead_radius_um * 1e-6
    eta = _K_BOLTZMANN * T / (6.0 * np.pi * R_m * D_m2)
    return float(eta)


# ---------------------------------------------------------------------------
# Full pipeline orchestration (headless / batch-friendly)
# ---------------------------------------------------------------------------

def run_vpt_analysis(
    host_image: Optional[np.ndarray],
    bead_stack: np.ndarray,
    microns_per_pixel: float = 1.0,
    frame_interval_s: float = 0.1,
    bead_radius_um: float = 0.1,
    temperature_C: float = 24.0,
    erosion_px: int = 5,
    seg_method: str = 'otsu',
    bead_min_sigma: float = 1.0,
    bead_max_sigma: float = 5.0,
    bead_threshold: float = 0.02,
    bead_fit_quality: bool = True,
    exclude_aggregates: bool = True,
    recover_out_of_plane: bool = True,
    track_aggregates: bool = True,
    linker: str = 'trackmate',
    max_linking_distance_um: float = 2.0,
    max_frame_gap: int = 2,
    min_track_length: int = 5,
    progress_callback=None,
) -> dict:
    """
    End-to-end VPT microrheology from raw multichannel data.

    Returns
    -------
    dict with keys:
        host_mask         : eroded labeled host mask (2D int)
        detections_df     : raw per-frame bead detections
        tracks_df         : linked, drift-corrected trajectories
        msd_df            : ensemble MSD vs lag
        fit               : diffusion fit dict (D, alpha, ...)
        eta_Pa_s          : Stokes-Einstein viscosity
        n_tracks          : number of tracks used
    """
    from pycat.toolbox.condensate_physics_tools import (
        compute_msd, fit_anomalous_diffusion)

    # 1-2. Host segmentation + erosion.
    #      If host_image is None (e.g. a beads-in-glycerol control, or any data
    #      with no condensate boundary), skip host masking and track every bead
    #      across the full frame — the detection layer treats host_mask=None as
    #      "keep all beads".
    if host_image is None:
        host_eroded = None
    else:
        host_labeled = segment_host_condensate(host_image, method=seg_method)
        host_eroded  = erode_host_mask(host_labeled, erosion_px=erosion_px)

    # 3. Bead detection — keep ALL classes labelled so aggregates can be
    #    routed to a secondary population rather than discarded.
    detections = detect_beads_stack(
        bead_stack, host_mask=host_eroded,
        min_sigma=bead_min_sigma, max_sigma=bead_max_sigma,
        threshold=bead_threshold, microns_per_pixel=microns_per_pixel,
        fit_quality=bead_fit_quality,
        exclude_aggregates=False, recover_out_of_plane=True,
        progress_callback=progress_callback)

    if detections.empty:
        return dict(host_mask=host_eroded, detections_df=detections,
                    tracks_df=pd.DataFrame(), msd_df=pd.DataFrame(),
                    fit={}, eta_Pa_s=float('nan'), n_tracks=0,
                    aggregate_detections_df=pd.DataFrame(),
                    aggregate_tracks_df=pd.DataFrame(),
                    aggregate_stats_df=pd.DataFrame())

    # 3b. Split into primary probe population and aggregate secondary set
    pops = split_bead_populations(detections, recover_out_of_plane=recover_out_of_plane)
    primary = pops['primary']
    aggregates = pops['aggregate']
    # If the user chose NOT to exclude aggregates from the primary set, fold
    # them back in (they still also appear in the aggregate population).
    if bead_fit_quality and not exclude_aggregates and not aggregates.empty:
        primary = pd.concat([primary, aggregates], ignore_index=True)

    # 4-6. Primary population: link → drift-correct → MSD → viscosity
    tracks = _link(primary, linker, max_linking_distance_um,
                   max_frame_gap, microns_per_pixel)
    tracks = drift_correct_com(tracks)
    msd_df = compute_msd(
        tracks, frame_interval_s=frame_interval_s,
        min_track_length=min_track_length)
    fit = fit_anomalous_diffusion(msd_df)
    eta = viscosity_from_diffusion(
        fit.get('D_um2_per_s', float('nan')), bead_radius_um, temperature_C)

    # 3c/4b. Secondary aggregate population — tracked separately, plus a
    # per-frame aggregation readout (count, size, mobility over time).
    agg_tracks = pd.DataFrame()
    total_by_frame = detections.groupby('frame').size()
    agg_stats = aggregate_population_stats(aggregates, total_by_frame=total_by_frame)
    if track_aggregates and not aggregates.empty and len(aggregates) >= 2:
        try:
            agg_tracks = _link(aggregates, linker, max_linking_distance_um,
                               max_frame_gap, microns_per_pixel)
        except Exception as _e:
            napari_show_warning(f"Aggregate tracking skipped: {_e}")

    return dict(
        host_mask=host_eroded, detections_df=detections, tracks_df=tracks,
        msd_df=msd_df, fit=fit, eta_Pa_s=eta,
        n_tracks=int(tracks['track_id'].nunique()) if not tracks.empty else 0,
        aggregate_detections_df=aggregates,
        aggregate_tracks_df=agg_tracks,
        aggregate_stats_df=agg_stats,
        n_aggregate_tracks=int(agg_tracks['track_id'].nunique())
            if not agg_tracks.empty and 'track_id' in agg_tracks else 0)


def _link(detections, linker, max_dist_um, max_gap, mpp, progress_callback=None):
    """Route to the requested trajectory linker."""
    linker = (linker or 'trackmate').lower()
    if linker == 'trackmate':
        from pycat.toolbox.trackmate_bridge import (
            trackmate_bridge_available, run_trackmate_lap_tracking)
        if not trackmate_bridge_available():
            napari_show_warning(
                "TrackMate not available (pip install pycat-napari[trackmate] "
                "+ a JDK). Falling back to the Bayesian linker.")
            linker = 'bayesian'
        else:
            return run_trackmate_lap_tracking(
                detections, max_linking_distance_um=max_dist_um,
                max_frame_gap=max_gap, allow_merging=False,
                allow_splitting=False)
    if linker == 'bayesian':
        from pycat.toolbox.dynamic_spatial_tools import link_trajectories_bayesian
        return link_trajectories_bayesian(
            detections, max_displacement_um=max_dist_um, max_gap_frames=max_gap,
            progress_callback=progress_callback)
    from pycat.toolbox.dynamic_spatial_tools import link_trajectories
    return link_trajectories(detections, max_dist_um, max_gap)
