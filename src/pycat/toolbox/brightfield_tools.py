"""
PyCAT Brightfield Condensate Toolbox
======================================
Preprocessing and analysis tools for brightfield (transmitted light) images
of condensates / biomolecular droplets.

Brightfield vs fluorescence differences
-----------------------------------------
Condensates in brightfield appear as **dark spots** on a bright background
due to their higher refractive index scattering/absorbing transmitted light.
This inverts most image conventions used for fluorescence:

  Property               Fluorescence          Brightfield
  ─────────────────────  ────────────────────  ────────────────────────
  Condensate signal      Bright blob           Dark blob
  Background             Dark (low signal)     Bright (incident light)
  Intensity meaning      ∝ fluorophore conc.   ∝ transmittance (inverse OD)
  Concentration proxy    I_condensate           OD = −log(I / I_background)
  Segmentation target    Bright regions         Dark regions (inverted image)
  SNR metric             (I_spot−I_bg)/σ_bg    CNR = (I_bg−I_spot)/σ_bg
  Bleaching              Signal decays          Not applicable
  Focus artefact         Blurring of spots      Contrast loss + halo changes

Halo artefact
-------------
Brightfield images often show a bright halo surrounding dark condensate spots.
This is caused by light diffracted by the condensate interfering constructively
just outside the spot boundary (related to the phase-contrast effect for objects
with refractive index mismatch). The halo must be suppressed before segmentation
or it will cause:
  - Underestimation of spot area (halo appears as part of background)
  - Missed small spots adjacent to larger ones
  - Errors in optical density calculation

Analysis reuse
--------------
Once condensates are segmented (masks + centroids in µm), ALL of the following
PyCAT analyses run identically on brightfield data — they operate purely on
geometric/spatial data and do not care about the imaging modality:

  ✓ Spatial metrology (NND, Ripley's L, PCF, Voronoi, Delaunay, MST, convex hull)
  ✓ Trajectory tracking (Bayesian + greedy NNL)
  ✓ MSD / anomalous diffusion
  ✓ Merge/fission detection
  ✓ Morphological complexity (fractal D, lacunarity, tortuosity, orientation)
  ✓ Coarsening kinetics
  ✓ Kaplan-Meier survival
  ✓ Organizational metrics (entropy, DBSCAN, spacing, occupancy, boundary)
  ✓ Dynamic spatial phenotyping (growth/shrink kinetics on OD)
  ✓ Frame quality (focal drift via entropy — bleaching not applicable)

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2025
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import skimage as sk
from scipy import ndimage, optimize, stats
from typing import Optional


# ---------------------------------------------------------------------------
# 1. Brightfield preprocessing
# ---------------------------------------------------------------------------

def bf_flat_field_correction(
    image: np.ndarray,
    background_image: Optional[np.ndarray] = None,
    background_sigma: float = 30.0,
) -> np.ndarray:
    """
    Flat-field correct a brightfield image to remove uneven illumination.

    If a background (dark field + bright field reference) image is provided,
    uses standard flat-field formula:
        I_corrected = (I_raw − I_dark) / (I_flat − I_dark)

    If no reference is available, estimates the illumination field by heavily
    Gaussian-blurring the image (assumes condensates are much smaller than
    the illumination variation scale) and divides by it.

    Parameters
    ----------
    image : (H, W) float32 image in [0, 1]
    background_image : (H, W) float32 flat-field reference, or None
    background_sigma : sigma for Gaussian flat-field estimation when no
        reference is provided.  Should be >> condensate diameter in pixels.

    Returns
    -------
    (H, W) float32 corrected image, normalised to [0, 1]
    """
    img = image.astype(np.float32)
    if background_image is not None:
        bg = background_image.astype(np.float32)
        bg_mean = float(bg.mean())
        corrected = img / (bg / (bg_mean + 1e-9) + 1e-9)
    else:
        # Estimate illumination field from the image itself
        illum = ndimage.gaussian_filter(img, sigma=background_sigma)
        illum_mean = float(illum.mean())
        corrected = img / (illum / (illum_mean + 1e-9) + 1e-9)
    # Normalise to [0, 1]
    mn, mx = corrected.min(), corrected.max()
    return ((corrected - mn) / (mx - mn + 1e-8)).astype(np.float32)


def bf_background_subtract(
    image: np.ndarray,
    kernel_size: int = 50,
    method: str = 'uniform',
) -> np.ndarray:
    """
    Estimate and subtract the slowly-varying brightfield background.

    Brightfield background = local incident light intensity, which varies
    on the scale of the illumination NA and condenser alignment — typically
    much larger (>>50px) than individual condensates (3-20px).

    The background-subtracted image is background_estimate − image (positive
    where the condensate absorbs/scatters light).

    Parameters
    ----------
    image : (H, W) float32, values in [0, 1]
    kernel_size : size of the smoothing kernel in pixels.
        Should be at least 3-5× the largest expected condensate diameter.
    method : 'uniform' (fastest) or 'gaussian' (smoother edges)

    Returns
    -------
    (H, W) float32 background-subtracted image, clipped to [0, 1].
    Positive values = condensate (absorbing light), zero = background.
    """
    img = image.astype(np.float32)
    if method == 'gaussian':
        bg = ndimage.gaussian_filter(img, sigma=kernel_size / 3.0)
    else:
        bg = ndimage.uniform_filter(img, size=kernel_size)
    bg_sub = np.clip(bg - img, 0, None)
    mx = bg_sub.max()
    return (bg_sub / (mx + 1e-8)).astype(np.float32)


def bf_halo_correction(
    bg_subtracted: np.ndarray,
    halo_sigma_factor: float = 1.8,
    halo_weight: float = 0.4,
) -> np.ndarray:
    """
    Suppress the bright halo artefact surrounding condensate spots.

    The halo arises from diffraction at the condensate boundary and appears
    as a bright ring just outside each dark spot in the original image.
    After background subtraction the halo becomes a bright ring surrounding
    the dark-blob signal, which causes:
      - Area overestimation if included in the condensate mask
      - False connections between nearby condensates

    Method: subtract a smoothed copy of the signal (capturing the slow-
    varying halo envelope) at a downweighted fraction.

    Parameters
    ----------
    bg_subtracted : (H, W) background-subtracted image (output of bf_background_subtract)
    halo_sigma_factor : halo_sigma = mean_object_radius × this factor.
        Controls the spatial scale of halo suppression.
    halo_weight : fraction of the smoothed image to subtract.
        0.0 = no correction, 0.5 = aggressive.

    Returns
    -------
    (H, W) halo-corrected image, non-negative, normalised to [0, 1]
    """
    img = bg_subtracted.astype(np.float32)
    # Estimate object radius from the threshold of the BG-subtracted image
    thresh = float(sk.filters.threshold_otsu(img[img > 0.01]) * 0.5
                   if img[img > 0.01].size > 10 else 0.05)
    binary = img > thresh
    if binary.sum() > 0:
        labeled = sk.measure.label(binary)
        areas   = [p.area for p in sk.measure.regionprops(labeled)]
        mean_r  = float(np.sqrt(np.mean(areas) / np.pi)) if areas else 5.0
    else:
        mean_r = 5.0

    sigma = max(1.0, mean_r * halo_sigma_factor)
    smoothed = ndimage.gaussian_filter(img, sigma=sigma)
    corrected = np.clip(img - halo_weight * smoothed, 0, None)
    mx = corrected.max()
    return (corrected / (mx + 1e-8)).astype(np.float32)


def bf_enhance_contrast(
    image: np.ndarray,
    local_kernel: int = 20,
) -> np.ndarray:
    """
    Compute local Michelson contrast: C = (I_bg − I) / (I_bg + ε).

    Amplifies dark spots relative to local background, normalises out
    global illumination gradients, and makes condensate detection less
    sensitive to absolute intensity.

    Parameters
    ----------
    image : (H, W) float32 in [0, 1]
    local_kernel : size of neighbourhood for local background estimation

    Returns
    -------
    (H, W) float32 local contrast image, clipped to [0, 1]
    """
    img = image.astype(np.float32)
    local_bg = ndimage.uniform_filter(img, size=local_kernel)
    contrast = (local_bg - img) / (local_bg + 0.01)
    return np.clip(contrast, 0, 1).astype(np.float32)


def preprocess_brightfield(
    image: np.ndarray,
    bg_kernel: int = 50,
    halo_weight: float = 0.35,
    clahe_kernel: int = 64,
    background_image: Optional[np.ndarray] = None,
) -> dict:
    """
    Full brightfield preprocessing pipeline.

    Steps:
      1. Flat-field correction (if reference available, else estimate)
      2. Background subtraction (large-kernel uniform filter)
      3. Halo artefact suppression
      4. Local contrast enhancement
      5. CLAHE for final contrast optimisation

    Parameters
    ----------
    image : (H, W) float32 in [0, 1]
    bg_kernel : background estimation kernel size (pixels)
    halo_weight : halo suppression strength (0 = none, 0.5 = strong)
    clahe_kernel : CLAHE tile size
    background_image : optional flat-field reference

    Returns
    -------
    dict with keys:
        flat_corrected    : after flat-field correction
        bg_subtracted     : dark-blob signal after BG removal
        halo_corrected    : after halo suppression
        enhanced          : final enhanced image for segmentation
    """
    flat = bf_flat_field_correction(image, background_image)
    bgsub = bf_background_subtract(flat, kernel_size=bg_kernel)
    halo  = bf_halo_correction(bgsub, halo_weight=halo_weight)
    enh   = sk.exposure.equalize_adapthist(halo, kernel_size=clahe_kernel,
                                            clip_limit=0.02, nbins=128)
    return dict(
        flat_corrected=flat,
        bg_subtracted=bgsub,
        halo_corrected=halo,
        enhanced=enh.astype(np.float32),
    )


# ---------------------------------------------------------------------------
# 2. Brightfield condensate segmentation
# ---------------------------------------------------------------------------

def _remove_small(binary, min_area):
    """Version-compatible remove_small_objects.

    scikit-image 0.26 deprecated ``min_size`` in favour of ``max_size`` (which
    removes objects whose size is <= the value). Older versions only accept
    ``min_size``. Try the new signature first, fall back to the old one, so the
    deprecation warning doesn't fire on new skimage while old skimage still works.
    """
    if int(min_area) <= 0:
        return binary
    try:
        # New API (skimage >= 0.26): removes objects with size <= max_size,
        # so pass max_size = min_area - 1 to match the old "< min_area" removal.
        return sk.morphology.remove_small_objects(binary, max_size=int(min_area) - 1)
    except TypeError:
        return sk.morphology.remove_small_objects(binary, min_size=int(min_area))


def _watershed_split(binary, min_diameter_px, split_touching, _skfeat, _skseg):
    """Label a binary mask, optionally watershed-splitting touching objects on
    the distance transform. Shared by the texture and DoG methods."""
    if not split_touching:
        return sk.measure.label(binary)
    dist = ndimage.distance_transform_edt(binary)
    try:
        coords = _skfeat.peak_local_max(
            dist, min_distance=max(2, int(min_diameter_px)), labels=binary)
        seeds = np.zeros(dist.shape, dtype=bool)
        seeds[tuple(coords.T)] = True
        markers = sk.measure.label(seeds)
        return _skseg.watershed(-dist, markers, mask=binary)
    except Exception:
        return sk.measure.label(binary)


def segment_bf_condensates(
    enhanced_image: np.ndarray,
    min_diameter_px: float = 3.0,
    max_diameter_px: float = 50.0,
    threshold_method: str = 'multi_otsu',
    min_circularity: float = 0.5,
    method: str = 'intensity',
    texture_window: int = 9,
    split_touching: bool = True,
) -> np.ndarray:
    """
    Segment condensate spots from a brightfield-preprocessed image.

    Two segmentation strategies:

    - ``method='intensity'`` (default, legacy): threshold the enhanced image,
      where condensates appear as bright blobs. Works when preprocessing has
      made droplets uniformly brighter than background.
    - ``method='texture'``: segment by LOCAL INTENSITY VARIATION rather than
      absolute intensity. Brightfield/phase droplets — especially out-of-focus
      ones — appear as rings (dark rim + bright centre) with little net
      brightness difference from the mid-grey background, so intensity
      thresholding merges background or misses them. The local standard
      deviation is high wherever there's a droplet edge/ring; thresholding it,
      filling holes (ring → disk), and optionally watershed-splitting dense
      touching droplets segments these robustly. Best for dense fields of small
      defocused condensates.

    Parameters
    ----------
    enhanced_image : (H, W) float32
        For 'intensity': the enhanced image from preprocess_brightfield.
        For 'texture': works directly on the raw/enhanced brightfield image.
    min_diameter_px, max_diameter_px : accepted object diameter range (px).
    threshold_method : 'multi_otsu' or 'otsu' (intensity method only).
    min_circularity : minimum 4π·A/P² to accept (filters debris/scratches).
    method : 'intensity', 'texture', 'dog', or 'invert_reconcile'.
    texture_window : local-std window in px (texture method).
    split_touching : watershed-split touching droplets (texture method).

    Returns
    -------
    (H, W) int32 labeled mask (0 = background, 1..N = condensates)
    """
    img = enhanced_image.astype(np.float32)
    min_area = int(np.pi * (min_diameter_px / 2)**2)
    max_area = int(np.pi * (max_diameter_px / 2)**2)

    if method == 'texture':
        import skimage.feature as _skfeat
        import skimage.segmentation as _skseg
        # Normalize, then local standard deviation (texture / edge energy).
        _lo, _hi = float(img.min()), float(img.max())
        imn = (img - _lo) / (_hi - _lo) if _hi > _lo else img
        w = max(3, int(texture_window))
        local_mean = ndimage.uniform_filter(imn, w)
        local_sqm  = ndimage.uniform_filter(imn**2, w)
        local_std  = np.sqrt(np.maximum(local_sqm - local_mean**2, 0.0))
        # LOCAL-ADAPTIVE threshold on the texture map, not a single global Otsu.
        # A global threshold made the method inconsistent across regions of the
        # SAME texture: dense areas fused into one giant blob (over-threshold)
        # while others of identical texture dropped out entirely (under). A
        # local threshold judges each neighbourhood against its own surroundings
        # so uniform-texture regions break into individual droplets consistently.
        try:
            block = max(31, int(w) * 8 | 1)   # odd block ~ several droplet widths
            lt = sk.filters.threshold_local(local_std, block_size=block,
                                            method='gaussian')
            binary = local_std > lt
        except Exception:
            try:
                binary = local_std > sk.filters.threshold_otsu(local_std)
            except Exception:
                binary = local_std > float(local_std.mean())
        binary = sk.morphology.binary_closing(binary, sk.morphology.disk(1))
        binary = ndimage.binary_fill_holes(binary)   # rings → filled discs
        binary = _remove_small(binary, max(4, min_area))
        labeled = _watershed_split(binary, min_diameter_px, split_touching,
                                   _skfeat, _skseg)

    elif method == 'dog':
        import skimage.feature as _skfeat
        import skimage.segmentation as _skseg
        # Difference-of-Gaussians blob detection: responds to individual
        # droplet-scale blobs (local extrema at a target size) rather than
        # thresholding connected high-texture regions, so it CANNOT produce the
        # "one giant blob" undersegmentation and stays consistent across regions
        # of the same texture. Best for dense fields where per-droplet output is
        # wanted. sigmas scaled to the expected droplet radius.
        _lo, _hi = float(img.min()), float(img.max())
        imn = (img - _lo) / (_hi - _lo) if _hi > _lo else img
        r = max(1.0, float(min_diameter_px) / 2.0)
        sig_lo = max(0.8, r * 0.6)
        sig_hi = sig_lo * 2.2
        dog = np.abs(sk.filters.gaussian(imn, sig_lo) -
                     sk.filters.gaussian(imn, sig_hi))
        try:
            t = sk.filters.threshold_otsu(dog)
        except Exception:
            t = float(dog.mean())
        binary = dog > t
        binary = ndimage.binary_fill_holes(binary)
        binary = _remove_small(binary, max(4, min_area))
        labeled = _watershed_split(binary, min_diameter_px, split_touching,
                                   _skfeat, _skseg)

    elif method == 'invert_reconcile':
        import skimage.feature as _skfeat
        import skimage.segmentation as _skseg
        # Brightfield/phase condensates flip contrast depending on which side of
        # focus they're on: some are bright-centred, others dark-centred. A
        # single polarity misses roughly half. This method runs a polarity-
        # SPECIFIC detector (white top-hat = bright features smaller than the
        # structuring element) on BOTH the image and its inversion, unions the
        # two masks to catch condensates of either contrast, then drops anything
        # too large (merged background / debris) via max_diameter_px.
        _lo, _hi = float(img.min()), float(img.max())
        imn = (img - _lo) / (_hi - _lo) if _hi > _lo else img
        radius = max(3, int(min_diameter_px * 2))

        def _tophat_mask(a):
            th = sk.morphology.white_tophat(a, sk.morphology.disk(radius))
            try:
                t = sk.filters.threshold_otsu(th)
            except Exception:
                t = float(th.mean())
            m = th > t
            m = ndimage.binary_fill_holes(m)
            return _remove_small(m, max(4, min_area))

        m_bright = _tophat_mask(imn)          # bright-centred droplets
        m_dark   = _tophat_mask(1.0 - imn)    # dark-centred droplets (inverted)
        binary = m_bright | m_dark            # reconcile both polarities
        labeled = _watershed_split(binary, min_diameter_px, split_touching,
                                   _skfeat, _skseg)
        # "Drop anything too large": remove objects above the max diameter, which
        # are merged-background regions rather than real condensates.
        _keep = np.zeros_like(labeled)
        _nl = 1
        for _p in sk.measure.regionprops(labeled):
            if _p.area < 5 or _p.area > max_area:
                continue
            _keep[labeled == _p.label] = _nl
            _nl += 1
        labeled = _keep

    else:
        # Intensity thresholding (legacy path).
        if threshold_method == 'multi_otsu':
            try:
                thresholds = sk.filters.threshold_multiotsu(img, classes=3)
                thresh = thresholds[0]
            except Exception:
                thresh = sk.filters.threshold_otsu(img)
        else:
            thresh = sk.filters.threshold_otsu(img)
        binary = img > thresh
        binary = ndimage.binary_fill_holes(binary)
        binary = _remove_small(binary, min_area)
        labeled = sk.measure.label(binary)

    # Filter by area, circularity, and roundness
    final = np.zeros_like(labeled)
    new_label = 1
    for prop in sk.measure.regionprops(labeled):
        if prop.area < min_area or prop.area > max_area:
            continue
        # Circularity
        perim = prop.perimeter
        circ  = (4 * np.pi * prop.area / (perim**2 + 1e-9))
        if circ < min_circularity:
            continue
        final[labeled == prop.label] = new_label
        new_label += 1

    return final.astype(np.int32)


# ---------------------------------------------------------------------------
# 3. Optical density and CNR metrics
# ---------------------------------------------------------------------------

def compute_optical_density(
    image: np.ndarray,
    background_image: Optional[np.ndarray] = None,
    bg_kernel: int = 50,
) -> np.ndarray:
    """
    Convert brightfield intensity to optical density (OD / absorbance).

    Beer-Lambert law: OD = −log₁₀(I / I₀)
    where I₀ is the local background (incident light estimate).

    OD is directly proportional to condensate concentration × path length
    (thickness), making it the brightfield equivalent of fluorescence
    intensity as a concentration proxy.

    Parameters
    ----------
    image : (H, W) float32 in [0, 1]
    background_image : (H, W) float32 flat-field reference, or None
    bg_kernel : kernel size for background estimation if no reference

    Returns
    -------
    (H, W) float32 OD image, clipped to [0, 3]
    """
    img = np.asarray(image).astype(np.float64)
    if background_image is not None:
        I0 = background_image.astype(np.float64)
    else:
        I0 = ndimage.uniform_filter(img, size=bg_kernel)

    with np.errstate(divide='ignore', invalid='ignore'):
        od = -np.log10(img / (I0 + 1e-10))

    return np.clip(od, 0, 3).astype(np.float32)


def bf_condensate_metrics(
    image: np.ndarray,
    labeled_condensates: np.ndarray,
    labeled_cells: Optional[np.ndarray],
    microns_per_pixel: float,
    background_image: Optional[np.ndarray] = None,
    bg_kernel: int = 50,
) -> pd.DataFrame:
    """
    Per-condensate morphological and optical density metrics for brightfield.

    This is the brightfield equivalent of puncta_analysis_func(), replacing
    intensity-based metrics with optical-density-based ones.

    Returns
    -------
    DataFrame with one row per condensate and columns:
        condensate_label, cell_label,
        area_px, area_um2,
        mean_od, max_od, integrated_od,   (optical density metrics)
        major_axis_um, minor_axis_um, eccentricity, circularity,
        cnr,                               (contrast-to-noise ratio)
        od_partition_coeff,                (dense OD / dilute OD)
        x_um, y_um                         (centroid in µm)
    """
    od_image = compute_optical_density(image, background_image, bg_kernel)

    # Background OD (dilute phase) — median of non-condensate pixels
    bg_mask   = labeled_condensates == 0
    if labeled_cells is not None:
        # Restrict background to within cells
        bg_mask = bg_mask & (labeled_cells > 0)
    bg_od     = float(np.median(od_image[bg_mask])) if bg_mask.sum() > 0 else 0.0
    bg_std    = float(od_image[bg_mask].std()) if bg_mask.sum() > 0 else 0.01

    # Raw image background stats for CNR
    raw_bg_mean = float(image[bg_mask].mean()) if bg_mask.sum() > 0 else 0.85
    raw_bg_std  = float(image[bg_mask].std())  if bg_mask.sum() > 0 else 0.01

    rows = []
    for prop in sk.measure.regionprops(labeled_condensates,
                                        intensity_image=od_image):
        cy, cx = prop.centroid
        cell_lbl = int(labeled_cells[int(cy), int(cx)]) \
                   if labeled_cells is not None else 0
        area_um2 = prop.area * microns_per_pixel**2
        mean_od  = float(prop.intensity_mean)
        max_od   = float(od_image[labeled_condensates == prop.label].max())
        integ_od = mean_od * prop.area * microns_per_pixel**2   # OD × area

        perim = prop.perimeter
        circ  = float(4 * np.pi * prop.area / (perim**2 + 1e-9))

        # CNR: (bg_intensity − spot_intensity) / bg_std
        spot_raw = float(image[labeled_condensates == prop.label].mean())
        cnr = (raw_bg_mean - spot_raw) / max(raw_bg_std, 1e-9)

        # Optical density partition coefficient: condensate OD / background OD
        od_part = mean_od / max(bg_od, 1e-9)

        rows.append({
            'condensate_label':   prop.label,
            'cell_label':         cell_lbl,
            'area_px':            prop.area,
            'area_um2':           area_um2,
            'mean_od':            mean_od,
            'max_od':             max_od,
            'integrated_od':      integ_od,
            'major_axis_um':      prop.axis_major_length * microns_per_pixel,
            'minor_axis_um':      prop.axis_minor_length * microns_per_pixel,
            'eccentricity':       prop.eccentricity,
            'circularity':        circ,
            'cnr':                cnr,
            'od_partition_coeff': od_part,
            'dilute_od':          bg_od,
            'y_um':               cy * microns_per_pixel,
            'x_um':               cx * microns_per_pixel,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 4. Brightfield focus quality
# ---------------------------------------------------------------------------

def bf_focus_metric(image: np.ndarray) -> float:
    """
    Compute Brenner gradient — a fast, reliable autofocus metric for brightfield.

    Brenner = Σ (I(x+2,y) − I(x,y))² across all pixels.
    Higher = sharper (better focus).  More robust than Laplacian variance
    for brightfield where dark spots can make Laplacian unreliable.

    Can be computed in a thin strip through the image centre for speed.
    """
    img = image.astype(np.float32)
    diff = img[:, 2:] - img[:, :-2]
    return float((diff**2).mean())


def bf_analyse_focus_series(
    stack: np.ndarray,
    threshold_fraction: float = 0.3,
) -> pd.DataFrame:
    """
    Assess focus quality for each frame in a brightfield time-series.

    Uses three complementary metrics:
      - Brenner gradient (horizontal high-frequency content)
      - Tenengrad (Sobel gradient magnitude) — sensitive to edge sharpness
      - Normalised variance — simple and fast

    A frame is flagged if the majority of metrics fall below their threshold.

    Returns
    -------
    DataFrame with columns: frame, brenner, tenengrad, norm_variance,
                             focus_score (mean normalised metric),
                             is_defocused
    """
    n = stack.shape[0]
    brenners, tenens, norms = [], [], []

    for t in range(n):
        frame = stack[t].astype(np.float32)
        brenners.append(bf_focus_metric(frame))

        gy = ndimage.sobel(frame, axis=0)
        gx = ndimage.sobel(frame, axis=1)
        tenens.append(float((gy**2 + gx**2).mean()))

        m = frame.mean()
        norms.append(float(frame.var() / (m**2 + 1e-9)))

    b, te, nv = np.array(brenners), np.array(tenens), np.array(norms)

    def _norm(arr):
        med = np.median(arr)
        return arr / max(med, 1e-12)

    b_n, te_n, nv_n = _norm(b), _norm(te), _norm(nv)
    focus_score = (b_n + te_n + nv_n) / 3.0

    threshold = threshold_fraction  # fraction of median
    is_defocused = focus_score < threshold

    return pd.DataFrame({
        'frame':         np.arange(n),
        'brenner':       b,
        'tenengrad':     te,
        'norm_variance': nv,
        'focus_score':   focus_score,
        'is_defocused':  is_defocused,
    })


def select_best_slice(stack: np.ndarray, method: str = 'std') -> int:
    """
    Select the "best" slice of a Z- or T-stack by an information/focus proxy.

    For nuclei/DAPI and many transmitted-light stacks the most informative
    plane is the one with the highest intensity spread or sharpness. This
    returns the index of that slice — useful for reducing a stack to a single
    representative plane before 2D segmentation (the max-std heuristic from the
    classic DAPI border pipeline).

    Parameters
    ----------
    stack : (N, H, W) image stack.
    method : slice-scoring metric —
        'std'      : maximum standard deviation (max intensity spread; the
                     original DAPI heuristic — highest ≈ max-information plane)
        'brenner'  : maximum Brenner gradient (sharpest focus)
        'tenengrad': maximum Sobel gradient magnitude (sharpest edges)

    Returns
    -------
    int : index of the selected slice. Returns 0 for a 2D input.
    """
    arr = np.asarray(stack)
    if arr.ndim == 2:
        return 0
    if arr.ndim != 3:
        raise ValueError(f"select_best_slice expects a 2D or 3D array, got ndim={arr.ndim}")

    m = method.lower()
    if m == 'brenner':
        scores = np.array([bf_focus_metric(arr[i]) for i in range(arr.shape[0])])
    elif m == 'tenengrad':
        scores = []
        for i in range(arr.shape[0]):
            f = arr[i].astype(np.float32)
            gy = ndimage.sobel(f, axis=0); gx = ndimage.sobel(f, axis=1)
            scores.append(float((gy ** 2 + gx ** 2).mean()))
        scores = np.array(scores)
    else:  # 'std'
        scores = arr.reshape(arr.shape[0], -1).std(axis=1)

    return int(np.argmax(scores))


def run_best_slice(method_dropdown, viewer):
    """
    Extract the best slice of the active Z/T-stack as a new 2D layer.

    `method_dropdown` is a QComboBox with 'std' / 'brenner' / 'tenengrad'.
    Adds the selected 2D plane as a new image layer.
    """
    import napari
    from napari.utils.notifications import show_info as _info, show_warning as _warn
    active = viewer.layers.selection.active
    if active is None or not isinstance(active, napari.layers.Image):
        _warn("Select an active image layer first.")
        return
    data = np.asarray(active.data)
    if data.ndim != 3:
        _warn("Best-slice needs a 3D (Z/T, H, W) stack.")
        return
    method = method_dropdown.currentText() if hasattr(method_dropdown, 'currentText') else 'std'
    idx = select_best_slice(data, method=method)
    viewer.add_image(data[idx], name=f"{active.name} best slice [{method} #{idx}]")
    _info(f"Best slice by {method}: index {idx} of {data.shape[0]}.")


# ---------------------------------------------------------------------------
# 5. OD-based coarsening / growth kinetics (brightfield time-series)
# ---------------------------------------------------------------------------

def bf_od_kinetics(
    stack: np.ndarray,
    labeled_condensates_stack: np.ndarray,
    microns_per_pixel: float,
    frame_interval_s: float = 1.0,
    background_image: Optional[np.ndarray] = None,
    bg_kernel: int = 50,
) -> pd.DataFrame:
    """
    Compute per-frame OD-based condensate kinetics for a brightfield time-series.

    Equivalent to run_timeseries_condensate_analysis() but using optical
    density instead of fluorescence intensity.

    Returns
    -------
    DataFrame with columns: frame, time_s, n_condensates,
                             mean_area_um2, total_od, mean_od,
                             mean_radius_um (from area), od_fraction
    """
    n_frames = stack.shape[0]
    rows = []
    for t in range(n_frames):
        frame     = stack[t].astype(np.float32)
        label_map = labeled_condensates_stack[t] if labeled_condensates_stack.ndim == 3 \
                    else labeled_condensates_stack

        od = compute_optical_density(frame, background_image, bg_kernel)
        bg_mask = label_map == 0
        bg_od   = float(np.median(od[bg_mask])) if bg_mask.sum() > 0 else 0.0

        n_cond   = int(label_map.max())
        areas    = []
        total_od = 0.0
        for lbl in range(1, n_cond + 1):
            m = label_map == lbl
            if m.sum() == 0:
                continue
            areas.append(m.sum() * microns_per_pixel**2)
            total_od += float(od[m].mean() - bg_od) * m.sum() * microns_per_pixel**2

        mean_area = float(np.mean(areas)) if areas else 0.0
        mean_r    = float(np.sqrt(mean_area / np.pi)) if mean_area > 0 else 0.0
        cell_area = float((label_map >= 0).sum()) * microns_per_pixel**2

        rows.append({
            'frame':        t,
            'time_s':       t * frame_interval_s,
            'n_condensates': n_cond,
            'mean_area_um2': mean_area,
            'total_od':      total_od,
            'mean_od':       float(od[label_map > 0].mean()) if (label_map>0).sum() else 0.0,
            'mean_radius_um':mean_r,
            'od_fraction':   total_od / max(cell_area, 1e-9),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 6. Per-cell summary (brightfield equivalent of puncta_analysis_func)
# ---------------------------------------------------------------------------

def bf_per_cell_summary(
    condensate_metrics_df: pd.DataFrame,
    labeled_cells: np.ndarray,
    microns_per_pixel: float,
) -> pd.DataFrame:
    """
    Aggregate per-condensate OD metrics to per-cell summary statistics.

    Brightfield equivalent of the per-cell summary from puncta_analysis_func.
    Replaces intensity-based metrics with OD-based ones.

    Parameters
    ----------
    condensate_metrics_df : output of bf_condensate_metrics()
    labeled_cells : (H, W) labeled cell mask (0 = background)
    microns_per_pixel : µm per pixel

    Returns
    -------
    DataFrame with one row per cell:
        cell_label, cell_area_um2,
        n_condensates, total_condensate_area_um2, condensate_coverage_fraction,
        mean_od, total_integrated_od, mean_cnr, mean_circularity,
        mean_radius_um, od_partition_coeff
    """
    rows = []
    for prop in sk.measure.regionprops(labeled_cells):
        cell_df = condensate_metrics_df[
            condensate_metrics_df['cell_label'] == prop.label]

        cell_area_um2 = prop.area * microns_per_pixel**2
        n = len(cell_df)

        if n == 0:
            rows.append({
                'cell_label': prop.label,
                'cell_area_um2': cell_area_um2,
                'n_condensates': 0,
                'total_condensate_area_um2': 0.0,
                'condensate_coverage_fraction': 0.0,
                'mean_od': 0.0,
                'total_integrated_od': 0.0,
                'mean_cnr': 0.0,
                'mean_circularity': np.nan,
                'mean_radius_um': 0.0,
                'od_partition_coeff': np.nan,
            })
            continue

        total_area = cell_df['area_um2'].sum()
        mean_r = float(np.sqrt(cell_df['area_um2'].mean() / np.pi)) if n > 0 else 0.0

        rows.append({
            'cell_label':                  prop.label,
            'cell_area_um2':               cell_area_um2,
            'n_condensates':               n,
            'total_condensate_area_um2':   total_area,
            'condensate_coverage_fraction':total_area / max(cell_area_um2, 1e-9),
            'mean_od':                     float(cell_df['mean_od'].mean()),
            'total_integrated_od':         float(cell_df['integrated_od'].sum()),
            'mean_cnr':                    float(cell_df['cnr'].mean()),
            'mean_circularity':            float(cell_df['circularity'].mean()),
            'mean_radius_um':              mean_r,
            'od_partition_coeff':          float(cell_df['od_partition_coeff'].mean()),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 7. Brightfield texture features on OD image
# ---------------------------------------------------------------------------

def bf_texture_features(
    od_image: np.ndarray,
    condensate_mask: np.ndarray,
    labeled_cells: Optional[np.ndarray] = None,
) -> pd.DataFrame:
    """
    Compute texture / information-content features on the optical density
    image within condensate regions and the surrounding cytoplasm.

    Unlike raw intensity texture (which mixes illumination with structure),
    OD texture reflects true material heterogeneity.

    Features per cell (or whole image if labeled_cells is None):
        od_entropy          : Shannon entropy of OD distribution in cell
        od_kurtosis         : excess kurtosis (peakedness) of OD within condensates
        od_condensate_std   : std of OD within condensates (internal heterogeneity)
        od_cytoplasm_std    : std of OD in non-condensate cytoplasm
        od_skewness         : skewness of whole-cell OD (positive = heavy high-OD tail)
        condensate_fraction : fraction of cell pixels with OD above threshold
    """
    from scipy.stats import kurtosis as sp_kurt, skew as sp_skew

    def _feats(od, cond_mask, cytoplasm_mask):
        all_px   = od[cytoplasm_mask | cond_mask] if (cytoplasm_mask | cond_mask).any() else od.ravel()
        cond_px  = od[cond_mask]  if cond_mask.any()  else np.array([0.0])
        cyto_px  = od[cytoplasm_mask] if cytoplasm_mask.any() else np.array([0.0])

        counts, _ = np.histogram(all_px, bins=64, range=(0, all_px.max()+1e-9))
        p = counts / (counts.sum() + 1e-12)
        p = p[p > 0]
        entropy = float(-np.sum(p * np.log2(p)))

        return {
            'od_entropy':           entropy,
            'od_kurtosis':          float(sp_kurt(cond_px, fisher=True)) if len(cond_px) > 3 else np.nan,
            'od_condensate_std':    float(cond_px.std()),
            'od_cytoplasm_std':     float(cyto_px.std()),
            'od_skewness':          float(sp_skew(all_px)) if len(all_px) > 3 else np.nan,
            'condensate_fraction':  float(cond_mask.sum()) / max(len(all_px), 1),
        }

    rows = []
    if labeled_cells is not None:
        for prop in sk.measure.regionprops(labeled_cells):
            cell_m = labeled_cells == prop.label
            cond_m = (condensate_mask > 0) & cell_m
            cyto_m = cell_m & (condensate_mask == 0)
            feats  = _feats(od_image, cond_m, cyto_m)
            feats['cell_label'] = prop.label
            rows.append(feats)
    else:
        cond_m = condensate_mask > 0
        cyto_m = ~cond_m
        feats  = _feats(od_image, cond_m, cyto_m)
        feats['cell_label'] = 0
        rows.append(feats)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 8. Brightfield frame quality (no bleaching — drift + contrast only)
# ---------------------------------------------------------------------------

def bf_analyse_frame_quality(
    stack: np.ndarray,
    frame_interval_s: float = 1.0,
    threshold_fraction: float = 0.4,
) -> dict:
    """
    Frame quality analysis for brightfield time-series.

    Brightfield-specific version of analyse_frame_quality():
    - No bleaching analysis (no fluorophore)
    - Focus assessed via Brenner gradient, Tenengrad, and normalised variance
    - Contrast assessed via mean CNR of the background vs local dark regions
    - Classifies each frame as 'ok', 'defocused', or 'low_contrast'

    Returns
    -------
    dict with keys:
        per_frame_df : DataFrame (frame, time_s, brenner, tenengrad,
                       norm_variance, focus_score, mean_dark_fraction,
                       is_defocused, cause)
        summary      : dict with dominant_cause and recommendation
    """
    from scipy.stats import linregress

    n = stack.shape[0]
    brenners, tenens, norm_vars, dark_fracs = [], [], [], []

    for t in range(n):
        frame = stack[t].astype(np.float32)
        # Normalise to [0,1]
        mn, mx = frame.min(), frame.max()
        frame_n = (frame - mn) / (mx - mn + 1e-8)

        # Brenner gradient
        brenners.append(float(((frame_n[:, 2:] - frame_n[:, :-2])**2).mean()))

        # Tenengrad
        gy = ndimage.sobel(frame_n, axis=0)
        gx = ndimage.sobel(frame_n, axis=1)
        tenens.append(float((gy**2 + gx**2).mean()))

        # Normalised variance
        m = frame_n.mean()
        norm_vars.append(float(frame_n.var() / max(m**2, 1e-9)))

        # Fraction of dark pixels (condensate presence indicator)
        dark_fracs.append(float((frame_n < 0.5).mean()))

    b  = np.array(brenners)
    te = np.array(tenens)
    nv = np.array(norm_vars)

    def _norm(arr):
        med = np.median(arr)
        return arr / max(med, 1e-12)

    focus_score = (_norm(b) + _norm(te) + _norm(nv)) / 3.0
    is_defocused = focus_score < threshold_fraction

    # Detect progressive drift: significant negative slope in focus_score
    t_arr = np.arange(n, dtype=float)
    slope, _, r, _, _ = linregress(t_arr, focus_score)
    has_drift = slope < -0.002 and r**2 > 0.3  # >0.2%/frame decline, R²>0.3

    causes = ['defocused' if d else 'ok' for d in is_defocused]
    dominant = 'focal_drift' if has_drift else ('defocused_frames' if is_defocused.any() else 'clean')

    recs = {
        'clean':           'No focus issues detected.',
        'defocused_frames':'Some frames are out of focus. Exclude is_defocused==True frames.',
        'focal_drift':     'Progressive focal drift detected. Consider hardware refocus or '
                           'z-correction in acquisition settings.',
    }

    per_frame_df = pd.DataFrame({
        'frame':           np.arange(n),
        'time_s':          t_arr * frame_interval_s,
        'brenner':         b,
        'tenengrad':       te,
        'norm_variance':   nv,
        'focus_score':     focus_score,
        'dark_fraction':   np.array(dark_fracs),
        'is_defocused':    is_defocused,
        'cause':           causes,
    })

    summary = dict(
        dominant_cause=dominant,
        focus_slope_per_frame=float(slope),
        n_defocused_frames=int(is_defocused.sum()),
        recommendation=recs[dominant],
    )

    return dict(per_frame_df=per_frame_df, summary=summary)
