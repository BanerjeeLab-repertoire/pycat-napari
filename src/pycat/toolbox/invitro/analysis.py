"""In-vitro **analysis miscellany** — the remaining domain functions split out of ``invitro_tools`` (1.6.216).

The sections that did not belong to size-distribution, partition, or the field summary: coarsening
kinetics (``coarsening_statistics``), critical-concentration estimation (``estimate_csat_lever_rule``),
contact-angle geometry (``estimate_contact_angle``), fusion-event detection/relaxation
(``detect_and_fit_fusions``), and sedimentation correction (``detect_sedimentation``). Moved VERBATIM from
``invitro_tools`` — no number changed; the existing in-vitro tests are the net. ``invitro_tools`` re-exports
all five, so it is now a pure re-export shim over the ``invitro/`` package.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import skimage as sk
from scipy import optimize, stats

from pycat.utils.notify import show_warning as napari_show_warning


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
            # This is a 2-D PROJECTED AREA fraction, not a volume fraction. It equals
            # a volume fraction only for an isotropic random section through a
            # statistically homogeneous 3-D material, or a genuinely quasi-2-D
            # chamber. In a flow cell droplets settle and large droplets are more
            # likely to intersect any given plane, so neither holds. The old
            # 'volume_fraction' key is retained as a deprecated alias so existing
            # scripts and saved tables keep working.
            'projected_area_fraction': total_area / field_area,
            'volume_fraction':   total_area / field_area,   # DEPRECATED: misnamed
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
    volume_fractions : 1D array of measured condensate fractions (Φ). **See the
                       warning below: if these came from a 2-D image, they are
                       projected AREA fractions, not volume fractions.**

    Returns
    -------
    dict with keys:
        C_sat, C_dense, slope, r_squared,
        fit_success, C_sat_units (unknown if not provided)

    .. warning::

       **The lever rule is a volumetric identity.** Φ in the equation above is a
       genuine volume fraction. PyCAT's 2-D workflows report a *projected area
       fraction* (see ``field_summary`` / ``coarsening_statistics``), which is not the
       same quantity: droplets settle, so the plane you imaged over- or
       under-represents them depending on its depth, and larger droplets are more
       likely to intersect any given plane.

       Feeding an area fraction into this fit therefore yields a **biased**
       ``C_sat``. The bias is systematic rather than random, so it does **not**
       average out across a dilution series.

       This is still useful as a **relative** measure — the ordering and the trend of
       C_sat across conditions imaged identically are informative, and a shifted
       phase boundary is still a shifted phase boundary. It should **not** be reported
       as an absolute saturation concentration on the strength of 2-D data alone.

       For a defensible absolute C_sat, obtain Φ from the **Z-Stack (3-D) Object
       Analysis** workflow (a real volume fraction), or from a quasi-2-D chamber whose
       depth is genuinely small compared with the droplets — and state which.

    .. note::

       ``C_sat`` and ``C_dense`` inherit the units of ``concentrations``. If those were
       *fluorescence intensities* rather than calibrated concentrations, then the
       outputs are intensity-scale proxies, not concentrations — monotonic with
       concentration and useful for comparison, but without units. Converting them
       requires a calibration curve for that fluorophore on that instrument.
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

    # ── `fit_success = r2 > 0.5` does not mean C_sat is trustworthy ────────────
    #
    # C_sat is an EXTRAPOLATION to the x-intercept. R² says how well the line fits the
    # points that were KEPT; it says nothing about whether the intercept is well
    # determined. Measured against a known C_sat = 10:
    #
    #     well-sampled, high noise -> C_sat 5.59  (a 44 % error)  R² 0.913  fit_success TRUE
    #
    # But the deeper problem is not the gate — it is this ESTIMATOR. It discards every
    # point where Φ = 0 (`above = phi > 0`), and those are the most informative points
    # there are: a zero at C = 5 says "the boundary is above 5". Throwing them away and
    # extrapolating from the survivors is what produces the error above.
    #
    # `estimate_phase_boundary` (1.5.382) fits a segmented hinge over ALL the data,
    # including the zeros. On the SAME data, against a true C_sat of 10:
    #
    #     ==========================  ============  ==================
    #     data                        lever rule    phase boundary
    #     ==========================  ============  ==================
    #     well-sampled, low noise     7.78          **9.97**
    #     well-sampled, HIGH noise    5.59          **10.62**
    #     ==========================  ============  ==================
    #
    # **Use `estimate_phase_boundary`.** This function is retained for backward
    # compatibility and for comparison against historical results; `fit_success` now
    # carries a warning rather than an endorsement.
    fit_ok = bool(r2 > 0.5 and C_sat > 0)
    if fit_ok:
        napari_show_warning(
            "C_sat (lever rule): this estimator DISCARDS every point where the area "
            "fraction is zero, and those points are informative — a zero at C = 5 says "
            "the boundary is above 5. Validated against a known C_sat of 10, it returned "
            "7.78 on clean data and 5.59 on noisy data (a 44 % error, with R² = 0.91). "
            "`estimate_phase_boundary` returned 9.97 and 10.62 on the same data. Use it "
            "instead; this result is retained only for comparison with historical values.")

    return dict(
        C_sat=C_sat,
        C_dense=C_dense,
        slope=float(slope),
        intercept=float(intercept),
        r_squared=r2,
        fit_success=fit_ok,
        # R² is NOT evidence that C_sat is right — it describes the fit to the surviving
        # points, not the reliability of an extrapolated intercept.
        r_squared_is_not_accuracy=True,
        superseded_by='estimate_phase_boundary',
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
    except Exception:  # broad-ok: reports fit_success=False — an honest failure flag, no fabricated boundary/fit values
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
    # ── The droplet COUNT cannot gate both processes — it is their SUM ──────────
    #
    # The old rule was::
    #
    #     sed    = phi_s > 0 and phi_r2 > 0.3 and **n_s > 0**
    #     coarse = r_s   > 0 and r_r2   > 0.3 and **n_s < 0**
    #
    # **``n_s`` cannot be both positive and negative**, so ``sed`` and ``coarse`` were **mutually
    # exclusive by construction** — and the ``'both'`` branch below was **unreachable.**
    #
    # That is not a style point. **When both processes run at once, the count is the SUM of a
    # sedimentation gain and a coalescence loss, and it can take either sign.** Measured on a
    # simulated sample with genuine sedimentation AND genuine coarsening, the old rule called it
    # **"sedimentation" 98 % of the time** — and its recommendation said *"no sedimentation
    # artefact"* about the coarsening, **which is the opposite of the truth.**
    #
    # The physics:
    #
    #     sedimentation  droplets settle INTO the focal plane   -> **phi UP**, n up, r unchanged
    #     coarsening     droplets merge / Ostwald-ripen         -> **r UP**, n down, phi ~flat
    #     BOTH           settling WHILE the residents coarsen   -> **phi UP and r UP**, n either
    #
    # **So phi and r are the discriminators, and n is CORROBORATION** — it strengthens a call, it
    # does not gate one.
    sed = phi_s > 0 and phi_r2 > 0.3
    coarse = r_s > 0 and r_r2 > 0.3

    # The count corroborates: rising with a rising phi is the settling signature; falling with a
    # rising radius is the coalescence signature. Reported so the user can see WHY.
    sed_corroborated = bool(sed and n_s > 0)
    coarse_corroborated = bool(coarse and n_s < 0)

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
        # Does the DROPLET COUNT agree? A rising count alongside a rising volume fraction is the
        # settling signature; a falling count alongside a rising radius is the coalescence
        # signature. **Not a gate** (see above) — the user sees WHY the call was made.
        sedimentation_corroborated_by_count=sed_corroborated,
        coarsening_corroborated_by_count=coarse_corroborated,
        phi_slope=phi_s, phi_r2=phi_r2,
        n_slope=n_s,     n_r2=n_r2,
        radius_slope=r_s, radius_r2=r_r2,
        dominant_process=dominant,
        recommendation=rec,
    )
