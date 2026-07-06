"""
PyCAT In Vitro Condensate Toolbox
====================================
Analysis functions specific to in vitro droplet / LLPS assays imaged on
a coverslip without cells.

Core differences from cellular analysis
-----------------------------------------
- No cell mask: the whole imaging field is the sample
- Background = buffer (very clean, uniform intensity baseline)
- Volume fraction (Φ) replaces condensate fraction (% of cell area)
- Partition coefficient = droplet intensity / bulk buffer intensity
- C_sat directly measurable from bulk fluorescence outside droplets
- Droplet size distributions follow polymer-physics scaling laws
- Coarsening / coalescence kinetics are much cleaner than in cells
- Contact angle (BF) characterises surface wetting behaviour
- Dilution-series experiments yield phase diagram tie-lines

All spatial, dynamic, morphological, and biophysical analyses from the
cellular toolkit apply identically after segmentation — they operate on
masks and centroids without caring about modality or biological context.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2025
"""

from __future__ import annotations

import warnings
import numpy as np
import pandas as pd
import skimage as sk
from scipy import ndimage, optimize, stats
from typing import Optional


# ---------------------------------------------------------------------------
# 1. Per-field summary (replaces per-cell summary for in vitro)
# ---------------------------------------------------------------------------

def field_summary(
    labeled_droplets: np.ndarray,
    image: np.ndarray,
    microns_per_pixel: float,
    field_area_um2: Optional[float] = None,
) -> dict:
    """
    Compute whole-field summary statistics for an in vitro droplet image.

    Parameters
    ----------
    labeled_droplets : (H, W) integer label mask (0 = background / buffer)
    image : (H, W) float32 fluorescence or OD image in [0, 1]
    microns_per_pixel : µm per pixel
    field_area_um2 : total imaged area in µm².  If None, computed from mask shape.

    Returns
    -------
    dict with keys:
        n_droplets              : number of detected droplets
        volume_fraction         : Φ = total droplet area / field area
        mean_radius_um          : mean droplet radius (from area)
        median_radius_um
        std_radius_um
        number_density_per_um2  : droplets per µm²
        mean_droplet_intensity  : mean image value inside droplets
        bulk_intensity          : mean image value outside droplets (= C_sat proxy)
        partition_coefficient   : mean_droplet_intensity / bulk_intensity
        total_droplet_area_um2
        field_area_um2
    """
    H, W = labeled_droplets.shape
    if field_area_um2 is None:
        field_area_um2 = H * W * microns_per_pixel**2

    props      = sk.measure.regionprops(labeled_droplets)
    n          = len(props)
    bg_mask    = labeled_droplets == 0
    cond_mask  = labeled_droplets > 0

    if n == 0:
        return dict(n_droplets=0, volume_fraction=0.0, mean_radius_um=0.0,
                    median_radius_um=0.0, std_radius_um=0.0,
                    number_density_per_um2=0.0,
                    mean_droplet_intensity=np.nan, bulk_intensity=float(image.mean()),
                    partition_coefficient=np.nan,
                    total_droplet_area_um2=0.0, field_area_um2=field_area_um2)

    areas_um2 = np.array([p.area * microns_per_pixel**2 for p in props])
    radii_um  = np.sqrt(areas_um2 / np.pi)
    total_area = float(areas_um2.sum())

    bulk_int  = float(image[bg_mask].mean())   if bg_mask.sum()  > 0 else np.nan
    cond_int  = float(image[cond_mask].mean()) if cond_mask.sum() > 0 else np.nan
    part      = (cond_int / max(bulk_int, 1e-9)) if (bulk_int and bulk_int > 0) else np.nan

    return dict(
        n_droplets=n,
        volume_fraction=total_area / field_area_um2,
        mean_radius_um=float(radii_um.mean()),
        median_radius_um=float(np.median(radii_um)),
        std_radius_um=float(radii_um.std()),
        number_density_per_um2=n / field_area_um2,
        mean_droplet_intensity=cond_int,
        bulk_intensity=bulk_int,
        partition_coefficient=part,
        total_droplet_area_um2=total_area,
        field_area_um2=field_area_um2,
    )


# ---------------------------------------------------------------------------
# 2. Size distribution analysis
# ---------------------------------------------------------------------------

def fit_size_distribution(
    radii_um: np.ndarray,
    n_bins: int = 30,
) -> dict:
    """
    Fit droplet size distribution to lognormal and power-law models.

    In LLPS / polymer-physics frameworks:
    - Lognormal: typical for nucleation-and-growth condensates with
      polydisperse nucleation (most common in practice)
    - Power law: expected for Ostwald ripening at steady state
      (P(R) ∝ R² for 3D Lifshitz-Slyozov distribution)

    Parameters
    ----------
    radii_um : 1D array of droplet radii in µm
    n_bins   : histogram bins for fitting

    Returns
    -------
    dict with keys:
        lognormal_mu, lognormal_sigma, lognormal_r2  (log-space mean, std)
        powerlaw_alpha, powerlaw_r2
        preferred_model : 'lognormal' | 'power_law'
        histogram_r      : bin centres for plotting
        histogram_counts : normalised counts for plotting
        fit_lognormal    : fitted lognormal PDF values
        fit_powerlaw     : fitted power-law values
    """
    r = np.asarray(radii_um)
    r = r[r > 0]
    if len(r) < 5:
        return dict(preferred_model='insufficient_data')

    counts, edges = np.histogram(r, bins=n_bins, density=True)
    centres       = 0.5 * (edges[:-1] + edges[1:])
    valid         = counts > 0

    # Lognormal fit in log-space (linear regression on log(r) vs log(count))
    ln_r = np.log(r)
    mu_ln  = float(ln_r.mean())
    sig_ln = float(ln_r.std())

    def lognormal_pdf(x, mu, sigma):
        return (1 / (x * sigma * np.sqrt(2*np.pi) + 1e-12) *
                np.exp(-0.5 * ((np.log(x) - mu) / max(sigma, 1e-9))**2))

    ln_fit = lognormal_pdf(centres[valid], mu_ln, sig_ln)
    ss_res_ln = np.sum((counts[valid] - ln_fit)**2)
    ss_tot    = np.sum((counts[valid] - counts[valid].mean())**2)
    r2_ln = float(1 - ss_res_ln / max(ss_tot, 1e-12))

    # Power-law fit: log(P(R)) = α·log(R) + const
    log_r   = np.log(centres[valid] + 1e-9)
    log_c   = np.log(counts[valid]  + 1e-12)
    slope, intercept, rval, _, _ = stats.linregress(log_r, log_c)
    alpha_pl = float(slope)
    pl_fit   = np.exp(intercept) * centres[valid]**alpha_pl
    ss_res_pl = np.sum((counts[valid] - pl_fit)**2)
    r2_pl = float(1 - ss_res_pl / max(ss_tot, 1e-12))

    preferred = 'lognormal' if r2_ln >= r2_pl else 'power_law'

    # Full arrays for plotting
    fit_ln = lognormal_pdf(centres, mu_ln, sig_ln)
    fit_pl = np.exp(intercept) * (centres + 1e-9)**alpha_pl

    return dict(
        lognormal_mu=mu_ln,
        lognormal_sigma=sig_ln,
        lognormal_r2=r2_ln,
        powerlaw_alpha=alpha_pl,
        powerlaw_r2=r2_pl,
        preferred_model=preferred,
        histogram_r=centres,
        histogram_counts=counts,
        fit_lognormal=fit_ln,
        fit_powerlaw=fit_pl,
        mean_radius_um=float(r.mean()),
        median_radius_um=float(np.median(r)),
        polydispersity_index=float(r.std() / max(r.mean(), 1e-9)),
    )


# ---------------------------------------------------------------------------
# 3. Coarsening statistics (per-frame)
# ---------------------------------------------------------------------------

def coarsening_statistics(
    mask_stack: np.ndarray,
    microns_per_pixel: float,
    frame_interval_s: float = 1.0,
) -> pd.DataFrame:
    """
    Compute per-frame coarsening statistics for an in vitro droplet time-series.

    Returns
    -------
    DataFrame with columns: frame, time_s, n_droplets, mean_radius_um,
        median_radius_um, volume_fraction, number_density, polydispersity,
        total_area_um2
    """
    rows = []
    n_frames = mask_stack.shape[0] if mask_stack.ndim == 3 else 1

    for t in range(n_frames):
        frame = mask_stack[t] if mask_stack.ndim == 3 else mask_stack
        labeled = sk.measure.label(frame > 0) if frame.max() <= 1 else frame

        props = sk.measure.regionprops(labeled)
        areas = np.array([p.area * microns_per_pixel**2 for p in props])
        radii = np.sqrt(areas / np.pi) if len(areas) > 0 else np.array([0.0])

        H, W = frame.shape
        field_area = H * W * microns_per_pixel**2
        total_area = float(areas.sum()) if len(areas) > 0 else 0.0

        rows.append({
            'frame':             t,
            'time_s':            t * frame_interval_s,
            'n_droplets':        len(props),
            'mean_radius_um':    float(radii.mean()) if len(radii) > 0 else 0.0,
            'median_radius_um':  float(np.median(radii)) if len(radii) > 0 else 0.0,
            'volume_fraction':   total_area / field_area,
            'number_density':    len(props) / field_area,
            'polydispersity':    float(radii.std() / max(radii.mean(), 1e-9)) if len(radii) > 1 else 0.0,
            'total_area_um2':    total_area,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 4. Critical concentration (C_sat) estimation from dilution series
# ---------------------------------------------------------------------------

def estimate_csat_lever_rule(
    concentrations: np.ndarray,
    volume_fractions: np.ndarray,
) -> dict:
    """
    Estimate C_sat and C_dense from a dilution series using the lever rule.

    The lever rule for two-phase coexistence:
        Φ_condensate = (C_total − C_sat) / (C_dense − C_sat)

    At concentrations below C_sat, Φ = 0 (no condensates).
    Above C_sat, Φ increases linearly with concentration.

    The x-intercept of the linear fit to Φ vs C_total gives C_sat.

    Parameters
    ----------
    concentrations   : 1D array of total protein concentrations (µM or a.u.)
    volume_fractions : 1D array of measured condensate volume fractions (Φ)

    Returns
    -------
    dict with keys:
        C_sat, C_dense, slope, r_squared,
        fit_success, C_sat_units (unknown if not provided)
    """
    c = np.asarray(concentrations, dtype=float)
    phi = np.asarray(volume_fractions, dtype=float)
    if len(c) < 3:
        return dict(fit_success=False)

    # Use only points where phi > 0 (above phase boundary)
    above = phi > 0
    if above.sum() < 2:
        return dict(fit_success=False, C_sat=float(c.max()), C_dense=np.nan)

    slope, intercept, r, _, se = stats.linregress(c[above], phi[above])
    if abs(slope) < 1e-12:
        return dict(fit_success=False)

    C_sat  = float(-intercept / slope)           # x-intercept
    C_dense = float(C_sat + 1.0 / slope)         # where Φ = 1 (theoretical dense phase)
    r2     = float(r**2)

    return dict(
        C_sat=C_sat,
        C_dense=C_dense,
        slope=float(slope),
        intercept=float(intercept),
        r_squared=r2,
        fit_success=r2 > 0.5 and C_sat > 0,
    )


# ---------------------------------------------------------------------------
# 5. Partition coefficient without cell mask
# ---------------------------------------------------------------------------

def partition_coefficient_field(
    image: np.ndarray,
    labeled_droplets: np.ndarray,
    percentile_bulk: float = 10.0,
) -> dict:
    """
    Compute the fluorescence partition coefficient for in vitro droplets.

    For in vitro data, the bulk (dilute phase) intensity is estimated
    from the background pixels. Using a low percentile (default 10th)
    avoids contamination from dim droplets just below the segmentation
    threshold.

    Parameters
    ----------
    image           : (H, W) float32 in [0, 1]
    labeled_droplets: (H, W) integer label mask
    percentile_bulk : percentile of background pixels to use as bulk estimate

    Returns
    -------
    dict with keys:
        c_sat_proxy       : bulk (dilute phase) intensity
        c_dense_proxy     : mean droplet interior intensity
        partition_coeff   : C_dense / C_sat
        enrichment        : (C_dense − C_sat) / C_sat
        per_droplet_df    : DataFrame with per-droplet partition coefficient
    """
    bg_mask   = labeled_droplets == 0
    cond_mask = labeled_droplets > 0

    # Bulk (dilute-phase) intensity estimate. The 10th percentile of background
    # can collapse to ~0 on dark fluorescence backgrounds (many near-zero
    # pixels), which then made per-droplet partition = intensity / ~0 explode to
    # ~1e8. Use a ROBUST bulk: the percentile, but floored to the background MEAN
    # if the percentile is degenerate (<=0 or a tiny fraction of the mean). This
    # keeps the per-droplet partition on the same scale as the field-level one.
    if bg_mask.sum() > 0:
        bg_vals   = image[bg_mask]
        bulk_pct  = float(np.percentile(bg_vals, percentile_bulk))
        bulk_mean = float(bg_vals.mean())
        # If the percentile is ~0 (dark background) fall back to the mean, which
        # is what the field-level summary uses.
        if bulk_pct <= 0 or (bulk_mean > 0 and bulk_pct < 0.05 * bulk_mean):
            bulk = bulk_mean
        else:
            bulk = bulk_pct
    else:
        bulk = 0.0
    # Final safety floor: never divide by (near-)zero.
    bulk_div = bulk if bulk > 1e-6 else (float(image.mean()) if image.mean() > 1e-6 else 1.0)

    dense  = float(image[cond_mask].mean()) if cond_mask.sum() > 0 else np.nan
    part   = dense / bulk_div
    enrich = (dense - bulk) / bulk_div

    rows = []
    for prop in sk.measure.regionprops(labeled_droplets, intensity_image=image):
        rows.append({
            'droplet_label':     prop.label,
            'mean_intensity':    float(prop.intensity_mean),
            'partition_coeff':   float(prop.intensity_mean / bulk_div),
            'area_um2':          np.nan,  # caller can fill from microns_per_pixel
        })

    return dict(
        c_sat_proxy=bulk,
        c_dense_proxy=dense,
        partition_coeff=part,
        enrichment=enrich,
        per_droplet_df=pd.DataFrame(rows),
    )


# ---------------------------------------------------------------------------
# 6. Contact angle estimation (brightfield in vitro)
# ---------------------------------------------------------------------------

def estimate_contact_angle(
    image: np.ndarray,
    droplet_mask: np.ndarray,
    droplet_label: int = 0,
) -> dict:
    """
    Estimate the contact angle of a sessile droplet on a coverslip from
    its brightfield silhouette.

    Method:
      1. Extract the droplet boundary from the labeled mask
      2. Find the base chord (widest horizontal extent — where droplet
         contacts the glass, typically at the bottom of the image)
      3. Fit a circle to the upper arc of the boundary
      4. Compute the contact angle from the circle radius and base half-width:
         θ = arcsin(a/R) where a = base half-width, R = fitted circle radius
         θ < 90° = partial wetting (hydrophilic),  θ > 90° = hydrophobic

    Parameters
    ----------
    image        : (H, W) float32 brightfield image
    droplet_mask : (H, W) binary or labeled mask; if labeled, provide label
    droplet_label: if droplet_mask is labeled, which label to use (0 = any)

    Returns
    -------
    dict with keys:
        contact_angle_deg   : estimated contact angle in degrees
        circle_radius_px    : fitted circle radius in pixels
        base_width_px       : droplet base chord width in pixels
        fit_success         : bool
        boundary_y, boundary_x : droplet boundary coordinates
    """
    from scipy.optimize import least_squares

    if droplet_label > 0:
        mask = (droplet_mask == droplet_label).astype(bool)
    else:
        mask = (droplet_mask > 0).astype(bool)

    if not mask.any():
        return dict(fit_success=False)

    # Extract boundary
    boundary = sk.segmentation.find_boundaries(mask, mode='inner')
    y_b, x_b = np.where(boundary)

    if len(y_b) < 10:
        return dict(fit_success=False)

    # Base: row with maximum width (widest horizontal span = contact line)
    rows_present = np.unique(y_b)
    widths = {r: x_b[y_b == r].max() - x_b[y_b == r].min() for r in rows_present}
    base_row   = max(widths, key=widths.get)
    base_width = widths[base_row]
    base_x_mid = float(x_b[y_b == base_row].mean())

    # Use upper arc (above base) for circle fit
    upper = y_b < base_row
    if upper.sum() < 5:
        return dict(fit_success=False)
    y_u, x_u = y_b[upper].astype(float), x_b[upper].astype(float)

    # Algebraic circle fit (Kasa method — fast and stable)
    def _circle_residuals(p, x, y):
        cx, cy, r = p
        return np.sqrt((x-cx)**2 + (y-cy)**2) - r

    cx0 = float(x_u.mean())
    cy0 = float(y_u.mean())
    r0  = float(base_width / 2)

    try:
        res = least_squares(_circle_residuals, [cx0, cy0, r0],
                             args=(x_u, y_u), method='lm')
        cx, cy, R = res.x
        if R < 1 or R > max(image.shape):
            return dict(fit_success=False)

        # Contact angle
        a   = base_width / 2
        sin_theta = min(1.0, a / max(R, 1e-6))
        theta = float(np.degrees(np.arcsin(sin_theta)))

        return dict(
            contact_angle_deg=theta,
            circle_radius_px=float(R),
            base_width_px=float(base_width),
            circle_centre_x=float(cx),
            circle_centre_y=float(cy),
            fit_success=True,
            boundary_y=y_b,
            boundary_x=x_b,
        )
    except Exception:
        return dict(fit_success=False)


# ---------------------------------------------------------------------------
# 7. Automatic fusion event detection and relaxation fitting
# ---------------------------------------------------------------------------

def detect_and_fit_fusions(
    mask_stack: np.ndarray,
    tracks_df: pd.DataFrame,
    image_stack: Optional[np.ndarray],
    microns_per_pixel: float,
    frame_interval_s: float = 1.0,
    match_radius_um: float = 3.0,
) -> pd.DataFrame:
    """
    Detect all merge events in an in vitro droplet stack and automatically
    fit the post-fusion aspect ratio relaxation.

    τ = ηR/γ gives the ratio of viscosity to surface tension.
    In vitro droplets typically have cleaner fusion events than cellular
    condensates because there is no cytoskeletal scaffold to interfere.

    Method
    ------
    1. detect_merge_fission() finds merge events directly from the mask
       stack (frame, centroid position) — it has no knowledge of track IDs.
    2. Each merge event is matched to the trajectory in tracks_df whose
       position at the merge frame is closest to the event centroid
       (within match_radius_um) — this identifies which track represents
       the newly-merged droplet going forward.
    3. The aspect ratio time series for that track, starting from the
       merge frame, is fit to the exponential relaxation model.

    Parameters
    ----------
    mask_stack : (T, H, W) label stack
    tracks_df  : output of link_trajectories_bayesian(), must contain
        columns track_id, frame, y_um, x_um, area_um2,
        major_axis_um, minor_axis_um (major/minor axis require the
        caller to have merged in shape data — see note below).
    image_stack: (T, H, W) optional fluorescence stack (currently unused;
        reserved for future intensity-based relaxation fitting)
    microns_per_pixel : µm per pixel
    frame_interval_s  : seconds per frame
    match_radius_um   : max distance between merge event centroid and a
        track's position at that frame to consider it a match

    Returns
    -------
    DataFrame with one row per detected fusion event:
        frame_event, track_merged, radius_um_post,
        tau_s, AR_0, r_squared, fit_success

    Notes
    -----
    tracks_df from extract_frame_properties() + link_trajectories_bayesian()
    contains major_axis_um/minor_axis_um only if extract_frame_properties()
    was called with a version that includes shape properties (current
    implementation does). If these columns are absent, aspect ratio
    defaults to 1.0 for all frames and the fit will not be meaningful —
    check for their presence before relying on fusion relaxation results.
    """
    from pycat.toolbox.dynamic_spatial_tools import detect_merge_fission
    from pycat.toolbox.condensate_physics_tools import fit_aspect_ratio_relaxation

    if mask_stack.ndim != 3:
        return pd.DataFrame()

    merge_df = detect_merge_fission(mask_stack, microns_per_pixel)
    if merge_df is None or merge_df.empty:
        return pd.DataFrame()

    merge_events = merge_df[merge_df['event_type'] == 'merge']
    if merge_events.empty:
        return pd.DataFrame()

    has_shape_cols = ('major_axis_um' in tracks_df.columns and
                       'minor_axis_um' in tracks_df.columns)

    rows = []
    for _, event in merge_events.iterrows():
        t0 = int(event['frame'])
        ey, ex = float(event['centroid_y_um']), float(event['centroid_x_um'])

        # Match merge event to the nearest track at this frame
        frame_tracks = tracks_df[tracks_df['frame'] == t0]
        if frame_tracks.empty:
            continue
        dists = np.sqrt((frame_tracks['y_um'] - ey)**2 +
                         (frame_tracks['x_um'] - ex)**2)
        if dists.min() > match_radius_um:
            continue
        t_merged = int(frame_tracks.loc[dists.idxmin(), 'track_id'])

        # Extract aspect ratio time series after the merge
        track_data = tracks_df[tracks_df['track_id'] == t_merged].sort_values('frame')
        post = track_data[track_data['frame'] >= t0]
        if len(post) < 4:
            continue

        if has_shape_cols:
            ar = (post['major_axis_um'] / post['minor_axis_um'].replace(0, np.nan)).values
            ar = np.nan_to_num(ar, nan=1.0)
        else:
            ar = np.ones(len(post))  # no shape data — fit will report fit_success=False

        t_arr = (post['frame'].values - t0) * frame_interval_s
        fit = fit_aspect_ratio_relaxation(t_arr, ar, t0_frame=0)

        r_post = (float(np.sqrt(post.iloc[0]['area_um2'] / np.pi))
                  if 'area_um2' in post.columns else np.nan)

        rows.append({
            'frame_event':    t0,
            'track_merged':   t_merged,
            'radius_um_post': r_post,
            'tau_s':          fit.get('tau_s', np.nan),
            'AR_0':           fit.get('AR_0', np.nan),
            'r_squared':      fit.get('r_squared', np.nan),
            'fit_success':    fit.get('fit_success', False) and has_shape_cols,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 8. Sedimentation correction for time-series
# ---------------------------------------------------------------------------

def detect_sedimentation(
    field_summary_df: pd.DataFrame,
) -> dict:
    """
    Detect sedimentation of droplets toward the coverslip in a time-series.

    Sedimentation manifests as an increase in volume fraction (Φ) over time
    as droplets settle into the focal plane. This can confound coarsening
    analysis by making it look like droplet growth when it is really more
    droplets entering the focal plane.

    Distinguishes sedimentation from coarsening:
    - Sedimentation: Φ ↑ AND number density ↑ (more droplets appearing)
    - Coarsening: mean radius ↑ AND number density ↓ (Ostwald / coalescence)
    - Both can occur simultaneously

    Parameters
    ----------
    field_summary_df : output of coarsening_statistics() with
        columns time_s, volume_fraction, n_droplets, mean_radius_um

    Returns
    -------
    dict with keys:
        sedimentation_detected  : bool
        coarsening_detected     : bool
        phi_slope               : rate of Φ increase (per second)
        n_slope                 : rate of droplet count increase
        radius_slope            : rate of mean radius increase
        dominant_process        : 'sedimentation' | 'coarsening' | 'both' | 'stable'
        recommendation          : string
    """
    df = field_summary_df.dropna()
    if len(df) < 4:
        return dict(dominant_process='insufficient_data')

    t   = df['time_s'].values
    phi = df['volume_fraction'].values
    n   = df['n_droplets'].values.astype(float)
    r   = df['mean_radius_um'].values

    def _slope_r2(x, y):
        s, _, r_val, _, _ = stats.linregress(x, y)
        return float(s), float(r_val**2)

    phi_s, phi_r2 = _slope_r2(t, phi)
    n_s,   n_r2   = _slope_r2(t, n)
    r_s,   r_r2   = _slope_r2(t, r)

    # Sedimentation: phi increasing AND n increasing
    sed = phi_s > 0 and phi_r2 > 0.3 and n_s > 0
    # Coarsening: radius increasing AND n decreasing
    coarse = r_s > 0 and r_r2 > 0.3 and n_s < 0

    if sed and coarse:
        dominant = 'both'
        rec = ('Both sedimentation and coarsening detected. '
               'Sediment corrected by subtracting linear Φ trend before '
               'fitting coarsening kinetics.')
    elif sed:
        dominant = 'sedimentation'
        rec = ('Sedimentation detected — droplets settling into focal plane. '
               'Consider using a top-mounted objective or imaging a fixed time '
               'after sample loading to allow equilibration.')
    elif coarse:
        dominant = 'coarsening'
        rec = 'Coarsening (Ostwald ripening or coalescence) detected — no sedimentation artefact.'
    else:
        dominant = 'stable'
        rec = 'No significant sedimentation or coarsening detected within the time series.'

    return dict(
        sedimentation_detected=sed,
        coarsening_detected=coarse,
        phi_slope=phi_s, phi_r2=phi_r2,
        n_slope=n_s,     n_r2=n_r2,
        radius_slope=r_s, radius_r2=r_r2,
        dominant_process=dominant,
        recommendation=rec,
    )
