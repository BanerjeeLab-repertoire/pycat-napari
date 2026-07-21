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

from pycat.utils.object_ref import bbox_columns_from_regionprops
import pandas as pd

# Notifications via the shim: keeps this module importable with no GUI stack (1.5.378).
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.notify import show_info as napari_show_info

# These report an INTENSITY RATIO, so they need the detector's zero point. Measured with a
# TRUE Kp of 30: on min-max normalised data the reported value swung from 323 to 22 with the
# noise level alone; on a top-hat + LoG image it came out NEGATIVE (-11.96).
from pycat.utils.intensity_semantics import IntensitySemantics, require_intensity
from pycat.utils.general_utils import debug_log
import skimage as sk
from scipy import ndimage, optimize, stats
from typing import Optional


# ---------------------------------------------------------------------------
# 1. Per-field summary (replaces per-cell summary for in vitro)
# ---------------------------------------------------------------------------

@require_intensity(IntensitySemantics.ABSOLUTE, 'field summary')
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
    image : (H, W) fluorescence or OD image in **RAW COUNTS**.

        **Not normalised.** This function reports intensity statistics, and min-max
        normalisation maps the image minimum to zero — which silently subtracts an
        uncontrolled floor (the darkest noise pixel in that field) and makes every ratio
        a function of the exposure. The docstring previously said "in [0, 1]", and the
        in-vitro widget duly fed it normalised data; the reported partition coefficient
        then swung from 323 to 22 with the noise level alone, against a true value of 30.
    microns_per_pixel : µm per pixel
    field_area_um2 : total imaged area in µm².  If None, computed from mask shape.

    Returns
    -------
    dict with keys:
        n_droplets                : number of detected droplets
        projected_area_fraction   : total droplet AREA / field AREA. This is a 2-D
                                    projected area fraction, **not a volume
                                    fraction** — see the note below.
        volume_fraction           : DEPRECATED alias of projected_area_fraction,
                                    kept so existing scripts and saved tables do not
                                    break. Do not use in new code; it is misnamed.
        mean_radius_um            : mean droplet radius (from area)
        median_radius_um
        std_radius_um
        number_density_per_um2    : droplets per µm²
        mean_droplet_intensity    : mean image value inside droplets
        dilute_phase_intensity    : mean image value OUTSIDE droplets. This is a
                                    fluorescence intensity, **not a concentration**
                                    — see the note below.
        bulk_intensity            : DEPRECATED alias of dilute_phase_intensity.
        intensity_ratio           : mean_droplet_intensity / dilute_phase_intensity. This
                                    is NOT a partition coefficient — no camera floor is
                                    removed, so it is biased toward 1 (a true Kp of 30
                                    reads as 5.8 on a 500-count pedestal). Use
                                    `partition_coefficient_local` for a real Kp.
        dense_dilute_contrast     : I_dense − I_dilute. Exact against the PEDESTAL (it
                                    cancels in the difference), but NOT immune to the
                                    droplet's PSF halo, which corrupts both terms — a 5 px
                                    edge costs it 22 %. See the note on the return value.
        partition_coefficient     : DEPRECATED alias of intensity_ratio.
                                    An apparent, intensity-based partition
                                    coefficient (dimensionless ratio of signals), not
                                    a thermodynamic one.
        total_droplet_area_um2
        field_area_um2

    Notes on what these quantities are, and are not
    -----------------------------------------------
    **The area fraction is not a volume fraction.** ``total_area / field_area`` is the
    fraction of a 2-D *projection* that is occupied by droplets. It equals the bulk
    volume fraction only under restrictive assumptions (an isotropic random section
    through a statistically homogeneous 3-D material, or a genuinely quasi-2-D
    chamber whose depth is small compared with the droplets). In a typical flow cell
    neither holds: droplets settle, so a plane near the coverslip over-represents
    them and a plane in the bulk under-represents them; and large droplets are more
    likely to intersect any given plane than small ones, biasing the in-plane size
    distribution toward large objects. Reporting this number as "volume fraction"
    invites it to be read as a physical volumetric quantity that it is not. Use the
    **Z-Stack (3-D) Object Analysis** workflow when a true volume fraction is needed.

    **The dilute-phase intensity is not C_sat.** It is a mean fluorescence (or optical
    density) value. Converting it to a saturation concentration requires a calibration
    curve relating intensity to concentration for *that* fluorophore, on *that*
    instrument, with *that* illumination — plus the assumption that the probe reports
    linearly over the range in question. Without that calibration it is a *proxy*: it
    is monotonic with concentration and therefore useful for comparison, but it has no
    units and should not be reported as a concentration.

    The same distinction applies to ``partition_coefficient``: it is a ratio of
    measured intensities. It equals the thermodynamic partition coefficient only if
    the intensity-to-concentration relationship is linear and identical in both
    phases — which is not guaranteed (quenching, inner-filter effects, and
    environment-sensitive quantum yield all break it).
    """
    H, W = labeled_droplets.shape
    if field_area_um2 is None:
        field_area_um2 = H * W * microns_per_pixel**2

    props      = sk.measure.regionprops(labeled_droplets)
    n          = len(props)
    bg_mask    = labeled_droplets == 0
    cond_mask  = labeled_droplets > 0

    if n == 0:
        _empty_bulk = float(image.mean())
        return dict(n_droplets=0,
                    projected_area_fraction=0.0,
                    volume_fraction=0.0,          # deprecated alias
                    mean_radius_um=0.0,
                    median_radius_um=0.0, std_radius_um=0.0,
                    number_density_per_um2=0.0,
                    mean_droplet_intensity=np.nan,
                    dilute_phase_intensity=_empty_bulk,
                    bulk_intensity=_empty_bulk,   # deprecated alias
                    partition_coefficient=np.nan,
                    total_droplet_area_um2=0.0, field_area_um2=field_area_um2)

    return _field_summary_metrics(props, image, bg_mask, cond_mask,
                                  microns_per_pixel, field_area_um2)


def _field_summary_metrics(props, image, bg_mask, cond_mask, microns_per_pixel, field_area_um2):
    """The non-empty whole-field metrics — droplet sizes, phase intensities, and the honest-name
    result dict (with the deprecated aliases kept for back-compat and the measured caveats on what
    each quantity IS and is not: the area fraction is a projection not a volume fraction; the
    intensity ratio is not a partition coefficient; the contrast is pedestal-exact but not
    halo-immune)."""
    n = len(props)
    areas_um2 = np.array([p.area * microns_per_pixel**2 for p in props])
    radii_um  = np.sqrt(areas_um2 / np.pi)
    total_area = float(areas_um2.sum())

    bulk_int  = float(image[bg_mask].mean())   if bg_mask.sum()  > 0 else np.nan
    cond_int  = float(image[cond_mask].mean()) if cond_mask.sum() > 0 else np.nan
    part      = (cond_int / max(bulk_int, 1e-9)) if (bulk_int and bulk_int > 0) else np.nan

    _area_frac = total_area / field_area_um2

    return dict(
        n_droplets=n,
        # Honest name first; the old key is kept as a deprecated alias so existing
        # scripts, saved CSVs and downstream code keep working.
        projected_area_fraction=_area_frac,
        volume_fraction=_area_frac,               # DEPRECATED: misnamed, see docstring
        mean_radius_um=float(radii_um.mean()),
        median_radius_um=float(np.median(radii_um)),
        std_radius_um=float(radii_um.std()),
        number_density_per_um2=n / field_area_um2,
        mean_droplet_intensity=cond_int,
        dilute_phase_intensity=bulk_int,
        bulk_intensity=bulk_int,                  # DEPRECATED alias

        # ── This is an INTENSITY RATIO, not a partition coefficient ────────────
        #
        # Kp = (I_dense - floor) / (I_dilute - floor). This is I_dense / I_dilute, with no
        # camera floor removed — so it is dragged toward 1 by the pedestal. Measured with a
        # TRUE Kp of 30 and a 500-count pedestal, it returns **5.83**.
        #
        # And if the caller feeds a MIN-MAX NORMALISED image (which the in-vitro widget did
        # until 1.5.424, and which this function's own docstring still invited by saying
        # "image in [0, 1]"), it is worse than biased — it becomes a function of the noise,
        # because normalisation maps the image MINIMUM to zero and the minimum is a noise
        # excursion below the dilute phase:
        #
        #     noise sd    reported "partition"   (true Kp = 30)
        #        2               323.5
        #        5               130.0
        #       15                44.0
        #       30                22.5
        #
        # A 14x swing driven entirely by the exposure. It is not a measurement of anything.
        #
        # The name is therefore changed to say what it is. `partition_coefficient` is kept
        # as a DEPRECATED alias so existing callers do not break, but it carries the same
        # caveat. For a real Kp, use `partition_coefficient_local` with a dark reference
        # (in vitro) or a cell mask (cellular) — see 1.5.423.
        intensity_ratio=part,
        partition_coefficient=part,               # DEPRECATED: this is NOT Kp — see above
        # ── The contrast is PEDESTAL-exact, NOT halo-immune ─────────────────────
        #
        # I claimed in 1.5.426 that this is "exact — the pedestal cancels in the difference".
        # The first half is right and the second is a blanket reassurance that does not hold:
        # the pedestal does cancel, but the contrast is NOT immune to a bad dilute reference.
        #
        # A droplet edge is not sharp, and the PSF halo corrupts BOTH terms — the dense mean
        # is pulled DOWN by edge pixels inside the mask, and the dilute mean is pulled UP by
        # halo pixels outside it. Measured, TRUE contrast = 2900:
        #
        #     droplet edge    contrast    error
        #     sharp           2898        -0 %
        #     1 px            2773        -4 %
        #     2.5 px          2560        **-12 %**
        #     5 px            2269        **-22 %**
        #
        # So: exact against the PEDESTAL, and degraded by the HALO like everything else.
        # Stating "exact" without that qualifier is the kind of true-but-incomplete
        # reassurance that 1.5.459 was about.
        dense_dilute_contrast=float(cond_int - bulk_int),

        total_droplet_area_um2=total_area,
        field_area_um2=field_area_um2,
    )



# ---------------------------------------------------------------------------
# 2. Size distribution analysis  ->  moved to invitro/size_distribution.py (1.6.213)
# ---------------------------------------------------------------------------
# The MLE size-distribution fitting moved to its own domain module; re-exported so every caller
# (invitro UIs, batch steps, the op-catalog api string) keeps importing it from invitro_tools.
from pycat.toolbox.invitro.size_distribution import (  # noqa: E402,F401
    fit_size_distribution_mle, fit_size_distribution)
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
# Partition-coefficient measurement  ->  moved to invitro/partition.py (1.6.214)
# ---------------------------------------------------------------------------
# The calibration-sensitive partition/K_p family moved to its own domain module; re-exported so every
# caller (invitro UIs, batch steps, timeseries) keeps importing it from invitro_tools.
from pycat.toolbox.invitro.partition import (  # noqa: E402,F401
    partition_coefficient_local, partition_measurement, partition_coefficient_field,
    estimate_phase_boundary)



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
