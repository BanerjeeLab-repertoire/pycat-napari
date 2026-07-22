"""VPT **host-condensate** segmentation + Mode-C host inference — split out of vpt_tools (1.6.237).

segment_host_condensate + erode_host_mask define the host droplet the beads sit in; infer_host_from_beads
reconstructs an unlabelled host from the bead distribution (Mode C). Moved VERBATIM - no number changed;
pinned by the VPT tests. The tools module re-exports the three public entry points.
"""
from __future__ import annotations

import numpy as np
import skimage as sk
import scipy.ndimage as ndi
from pycat.utils.tag_registry import tags_layer
from pycat.utils.general_utils import remove_small_objects_compat as _remove_small_objects_compat
from pycat.utils.notify import show_warning as napari_show_warning


# ---------------------------------------------------------------------------
# 1-2. Host condensate segmentation + interface erosion
# ---------------------------------------------------------------------------

@tags_layer('host_segment', role='mask', inputs=('image',),
            summary='Host condensate segmentation for VPT', target='condensate')
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
        labeled = _remove_small_objects_compat(labeled, min_area_px)
        labeled = sk.measure.label(labeled > 0)  # relabel contiguous

    return labeled.astype(np.int32)


@tags_layer('host_erode', role='mask',
            summary='Erode the host mask away from the interface', target='condensate')
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
    mask = _remove_small_objects_compat(mask, 5)
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
    except Exception:  # broad-ok: falls back to the already-measured equivalent radius (eqrad) when the ellipse fit fails — a real prior measurement, not a fabricated default
        return eqrad
