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
        fit = fit_gaussian_2d_spot(patch)
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


def classify_beads(beads_df: pd.DataFrame,
                   aggregate_intensity_factor: float = 1.6,
                   defocus_r2_max: float = 0.85,
                   sigma_outlier_factor: float = 1.5) -> pd.DataFrame:
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

    valid = df['r_squared'].notna() & df['integrated_intensity'].notna()
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
) -> pd.DataFrame:
    """
    Detect beads across all frames of a (T, H, W) stack.

    Returns
    -------
    props_df : DataFrame with columns frame, object_id, y_um, x_um, area_um2
        — the schema expected by the trajectory linkers (TrackMate bridge,
        link_trajectories, link_trajectories_bayesian). area_um2 is a nominal
        placeholder (beads are point-like); it carries a small constant so
        downstream code that reads it doesn't divide by zero.
    """
    stack = np.asarray(bead_stack)
    if stack.ndim == 2:
        stack = stack[np.newaxis, ...]

    rows = []
    n_frames = stack.shape[0]
    nominal_area = float(np.pi * (max_sigma * np.sqrt(2) * microns_per_pixel) ** 2)

    for t in range(n_frames):
        if not fit_quality:
            coords = detect_beads_frame(
                stack[t], min_sigma=min_sigma, max_sigma=max_sigma,
                num_sigma=num_sigma, threshold=threshold, host_mask=host_mask)
            for i, (y, x) in enumerate(coords):
                rows.append({
                    'frame': t, 'object_id': i,
                    'y_um': float(y) * microns_per_pixel,
                    'x_um': float(x) * microns_per_pixel,
                    'area_um2': nominal_area})
        else:
            beads = detect_beads_frame(
                stack[t], min_sigma=min_sigma, max_sigma=max_sigma,
                num_sigma=num_sigma, threshold=threshold, host_mask=host_mask,
                fit_quality=True, fit_window=fit_window)
            for i, b in enumerate(beads):
                # Area from the fitted PSF (pi * sqrt(2 ln2) FWHM area proxy):
                # use pi * sigma_x * sigma_y in physical units when available.
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
        if progress_callback is not None:
            progress_callback(t + 1, n_frames)

    if not rows:
        cols = ['frame', 'object_id', 'y_um', 'x_um', 'area_um2']
        if fit_quality:
            cols += ['sigma_x', 'sigma_y', 'sigma_mean', 'amplitude',
                     'integrated_intensity', 'r_squared', 'n_units_est',
                     'bead_class', 'singlet']
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rows)
    if not fit_quality:
        return df

    # Classify beads using the pooled population statistics
    df = classify_beads(df)

    # Optionally drop aggregates and/or exclude unrecoverable defocused beads
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
    aggregate = df[df['bead_class'] == 'aggregate'].reset_index(drop=True)
    return dict(primary=primary, aggregate=aggregate)


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
    out = pd.DataFrame({
        'n_aggregates': g.size(),
        'total_aggregated_units': g['n_units_est'].sum(min_count=1),
        'median_aggregate_units': g['n_units_est'].median(),
        'median_sigma': g['sigma_mean'].median(),
    })
    if total_by_frame is not None:
        out['aggregated_fraction'] = (out['n_aggregates']
                                      / total_by_frame.reindex(out.index)).astype(float)
    else:
        out['aggregated_fraction'] = np.nan
    return out.reset_index()


# ---------------------------------------------------------------------------
# 5. Ensemble center-of-mass drift correction
# ---------------------------------------------------------------------------

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


def _link(detections, linker, max_dist_um, max_gap, mpp):
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
            detections, max_displacement_um=max_dist_um, max_gap_frames=max_gap)
    from pycat.toolbox.dynamic_spatial_tools import link_trajectories
    return link_trajectories(detections, max_dist_um, max_gap)
