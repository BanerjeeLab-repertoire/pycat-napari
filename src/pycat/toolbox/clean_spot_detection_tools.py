"""
PyCAT CLEAN Spot Detection
===========================

STATUS: intentionally NOT registered in the Toolbox menu (as of v1.5.31).
This module is validated and kept in the tree deliberately — it is held back
from the user-facing menu until the smFISH pipeline it was written for is
added, at which point it will be wired into that pipeline's dock with
FISH-appropriate defaults rather than exposed as a loose general-purpose tool.
CLEAN assumes spots match the model PSF (true for smFISH), so it should not be
offered for arbitrary images. Do NOT re-add a menu entry without that context.
(If you are looking for this file expecting it to be missing — it was removed
from the menu on purpose, not dropped by accident.)
Iterative PSF-subtraction spot detection, adapted from the CLEAN algorithm of
radio astronomy (Högbom 1974) to fluorescence puncta detection.

Motivation
----------
Standard blob detectors (LoG / DoG / peak-local-max) struggle when spots
overlap or sit on a bright skirt: they merge close spots into one and mis-
estimate intensity from overlapping halos. CLEAN handles crowded fields
naturally:

  1. Find the brightest voxel in the (optionally masked) image.
  2. Record it as a spot and add an increment of intensity to it.
  3. Subtract a scaled model PSF centred on that voxel from the residual,
     removing the spot's contribution INCLUDING its skirt.
  4. Repeat until the brightest residual voxel falls below a threshold.

Because each spot's PSF (and its overlapping tail) is removed before the next
peak is sought, closely-spaced puncta are resolved individually and each
spot's flux is accumulated from the subtracted PSF amplitude. Repeated hits at
the same location (from partial damping) are consolidated into single spots
with summed intensity and an intensity-weighted centroid.

This is a direct descendant of the author's yeast TLC1 RNA-cluster analysis;
no equivalent CLEAN-based fluorescence spot detector is in the codebase.

Author
------
    Original algorithm: Gable Wadsworth (TLC1 cluster analysis)
    PyCAT port: Banerjee Lab, SUNY Buffalo, 2026
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

# Via the notification shim: keeps the array functions importable with no GUI stack.
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning


# ---------------------------------------------------------------------------
# PSF models
# ---------------------------------------------------------------------------

def gaussian_psf_2d(size: int = 11, sigma: float = 2.5) -> np.ndarray:
    """2D Gaussian PSF model, peak-normalised to 1."""
    r = size // 2
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    psf = np.exp(-(x ** 2 + y ** 2) / (2.0 * sigma ** 2))
    return psf / psf.max()


def gaussian_psf_3d(size_xy: int = 11, size_z: int = 13,
                    sigma_xy: float = 2.5, sigma_z: float = 4.0) -> np.ndarray:
    """
    3D Gaussian PSF model (anisotropic — axial sigma usually larger), peak-
    normalised to 1. Mirrors the separable xy·z construction of the original
    (hgauss ⊗ z).
    """
    rxy = size_xy // 2
    rz = size_z // 2
    yy, xx = np.mgrid[-rxy:rxy + 1, -rxy:rxy + 1]
    hxy = np.exp(-(xx ** 2 + yy ** 2) / (2.0 * sigma_xy ** 2))
    z = np.exp(-(np.arange(-rz, rz + 1) ** 2) / (2.0 * sigma_z ** 2))
    psf = hxy[None, :, :] * z[:, None, None]      # (Z, Y, X)
    return psf / psf.max()


# ---------------------------------------------------------------------------
# CLEAN core
# ---------------------------------------------------------------------------

def clean_detect(
    image: np.ndarray,
    psf: Optional[np.ndarray] = None,
    max_threshold: float = None,
    damping: float = 0.45,
    max_iterations: int = 20000,
    mask: Optional[np.ndarray] = None,
    merge_radius: int = 3,
    psf_sigma: float = 2.5,
    psf_size: int = 11,
    psf_sigma_z: float = 4.0,
    psf_size_z: int = 13,
) -> pd.DataFrame:
    """
    Detect spots by iterative PSF subtraction (CLEAN).

    Parameters
    ----------
    image : 2D (H, W) or 3D (Z, H, W) intensity image.
    psf : model PSF. If None, a Gaussian PSF is built from psf_* parameters
        (2D or 3D to match the image).
    max_threshold : stop when the brightest residual voxel is at or below this.
        If None, defaults to the image mean + 3·std (a data-driven floor).
    damping : fraction of each peak subtracted per iteration (the CLEAN "loop
        gain"). Smaller = gentler, more accurate flux but more iterations.
        The original used 0.45.
    max_iterations : hard cap on iterations (safety).
    mask : optional boolean mask; detection is restricted to True voxels
        (e.g. a single cell). Voxels outside are ignored.
    merge_radius : hits within this Chebyshev distance (px) are consolidated
        into one spot with summed intensity and an intensity-weighted centroid.
    psf_size, psf_sigma, psf_size_z, psf_sigma_z : Gaussian PSF parameters
        used when psf is None.

    Returns
    -------
    DataFrame with one row per detected spot:
        for 2D: y, x, intensity, n_hits
        for 3D: z, y, x, intensity, n_hits
    """
    img = np.asarray(image, dtype=float)
    is_3d = img.ndim == 3
    if img.ndim not in (2, 3):
        raise ValueError(f"clean_detect expects a 2D or 3D image, got ndim={img.ndim}")

    if psf is None:
        psf = (gaussian_psf_3d(psf_size, psf_size_z, psf_sigma, psf_sigma_z)
               if is_3d else gaussian_psf_2d(psf_size, psf_sigma))
    psf = np.asarray(psf, dtype=float)
    psf_flux = float(psf.sum())

    if max_threshold is None:
        vals = img[mask > 0] if mask is not None else img
        max_threshold = float(vals.mean() + 3.0 * vals.std())

    residual = img.copy()
    # Restrict search to the mask by driving outside-mask voxels below threshold
    if mask is not None:
        m = np.asarray(mask) > 0
        residual = np.where(m, residual, -np.inf)
    else:
        m = None

    radii = [s // 2 for s in psf.shape]
    shape = img.shape
    hits = []  # (coord tuple, intensity increment)

    for _ in range(max_iterations):
        idx = int(np.argmax(residual))
        coord = np.unravel_index(idx, shape)
        peak = residual[coord]
        if not np.isfinite(peak) or peak <= max_threshold:
            break

        # Bounds check for full PSF placement
        ok = all(radii[d] <= coord[d] < shape[d] - radii[d] for d in range(img.ndim))
        if not ok:
            # Can't place a full PSF here — suppress this voxel and continue
            residual[coord] = -np.inf if m is None else (
                residual[coord] - abs(peak) - 1.0)
            residual[coord] = min(residual[coord], max_threshold)
            continue

        inc = peak * damping
        hits.append((coord, inc * psf_flux))

        slicer = tuple(slice(coord[d] - radii[d], coord[d] + radii[d] + 1)
                       for d in range(img.ndim))
        residual[slicer] -= inc * psf

    if not hits:
        cols = (['z', 'y', 'x'] if is_3d else ['y', 'x']) + ['intensity', 'n_hits']
        return pd.DataFrame(columns=cols)

    # Consolidate nearby hits (partial-damping re-detections of the same spot)
    spots = _consolidate(hits, merge_radius, is_3d)

    if is_3d:
        df = pd.DataFrame(spots, columns=['z', 'y', 'x', 'intensity', 'n_hits'])
    else:
        df = pd.DataFrame(spots, columns=['y', 'x', 'intensity', 'n_hits'])
    return df.sort_values('intensity', ascending=False).reset_index(drop=True)


def _consolidate(hits, merge_radius, is_3d):
    """Merge hits within merge_radius (Chebyshev) into single spots."""
    coords = np.array([h[0] for h in hits], dtype=float)
    ints = np.array([h[1] for h in hits], dtype=float)
    used = np.zeros(len(hits), dtype=bool)
    out = []
    for i in range(len(hits)):
        if used[i]:
            continue
        # Chebyshev distance to all remaining hits
        d = np.max(np.abs(coords - coords[i]), axis=1)
        cluster = (~used) & (d <= merge_radius)
        used |= cluster
        tot = ints[cluster].sum()
        w = ints[cluster]
        centroid = (coords[cluster] * w[:, None]).sum(axis=0) / tot
        n = int(cluster.sum())
        if is_3d:
            out.append((centroid[0], centroid[1], centroid[2], tot, n))
        else:
            out.append((centroid[0], centroid[1], tot, n))
    return out


# ---------------------------------------------------------------------------
# Per-cell / per-region spot counting
# ---------------------------------------------------------------------------

def clean_spots_per_region(
    image: np.ndarray,
    label_mask: np.ndarray,
    **clean_kwargs,
) -> pd.DataFrame:
    """
    Run CLEAN spot detection separately within each labeled region (e.g. each
    cell) and return per-region spot counts and summed intensities — the
    per-cell tabulation from the original TLC1 workflow.

    Parameters
    ----------
    image : 2D or 3D intensity image.
    label_mask : integer label image (same shape) — one label per region.
    clean_kwargs : passed through to clean_detect().

    Returns
    -------
    DataFrame: region_label, n_spots, total_intensity, mean_spot_intensity
    """
    labels = np.asarray(label_mask)
    rows = []
    for lbl in np.unique(labels):
        if lbl == 0:
            continue
        region = labels == lbl
        spots = clean_detect(image, mask=region, **clean_kwargs)
        n = len(spots)
        tot = float(spots['intensity'].sum()) if n else 0.0
        rows.append({
            'region_label': int(lbl),
            'n_spots': n,
            'total_intensity': tot,
            'mean_spot_intensity': (tot / n) if n else 0.0,
        })
    return pd.DataFrame(rows)

# ---------------------------------------------------------------------------
# napari run-wrapper
# ---------------------------------------------------------------------------

def run_clean_detection(threshold_input, damping_input, sigma_input,
                        mask_dropdown, viewer):
    """
    Run CLEAN spot detection on the active image layer and add the detected
    spots as a points layer. Optionally restrict to a labels mask layer.

    threshold_input, damping_input, sigma_input : QLineEdit widgets.
    mask_dropdown : QComboBox of labels layers ('None' allowed).
    """
    import napari
    active = viewer.layers.selection.active
    if active is None or not isinstance(active, napari.layers.Image):
        napari_show_warning("Select an active image layer first.")
        return
    data = np.asarray(active.data)
    if data.ndim not in (2, 3):
        napari_show_warning("CLEAN needs a 2D or 3D image.")
        return

    try:
        thresh = float(threshold_input.text()) if threshold_input.text() else None
        damping = float(damping_input.text()) if damping_input.text() else 0.45
        sigma = float(sigma_input.text()) if sigma_input.text() else 2.5
    except ValueError:
        napari_show_warning("Threshold, damping, and sigma must be numbers.")
        return

    mask = None
    mname = mask_dropdown.currentText() if hasattr(mask_dropdown, 'currentText') else 'None'
    if mname != 'None' and mname in [l.name for l in viewer.layers]:
        mask = np.asarray(viewer.layers[mname].data) > 0

    df = clean_detect(data, max_threshold=thresh, damping=damping,
                      psf_sigma=sigma, mask=mask)
    if len(df) == 0:
        napari_show_warning(
            "CLEAN found no spots above threshold. Lower the threshold or the "
            "damping, or check the mask.")
        return

    if data.ndim == 3:
        coords = df[['z', 'y', 'x']].values
    else:
        coords = df[['y', 'x']].values
    name = f"{active.name} CLEAN spots"
    if name in [l.name for l in viewer.layers]:
        viewer.layers.remove(name)
    viewer.add_points(coords, name=name, size=6, face_color='#ff2d55',
                      border_color='white', opacity=0.8)
    napari_show_info(
        f"CLEAN detected {len(df)} spots "
        f"(total intensity {df['intensity'].sum():.3g}).")

