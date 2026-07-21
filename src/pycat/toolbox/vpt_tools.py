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

from pycat.utils.tag_registry import tags_layer
import pandas as pd

import skimage as sk
from pycat.utils.general_utils import remove_small_objects_compat as _remove_small_objects_compat
from pycat.utils.general_utils import debug_log
# The one place the minimum-track-length number lives, with the lag-window
# reasoning it is derived from. Imported rather than repeated: a second copy of a
# scientific default is a second thing to forget to change.
from pycat.toolbox.condensate_physics_tools import MIN_TRACK_LENGTH_FRAMES
import scipy.ndimage as ndi

# Notifications go through the shim so this module's PHYSICS (detection, MSD,
# diffusion fitting, viscosity) stays importable and testable without a GUI stack.
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning


# Boltzmann constant (J/K)


# ---------------------------------------------------------------------------
# 1-2. Host condensate segmentation + interface erosion  ->  moved to host.py (1.6.237)
# ---------------------------------------------------------------------------
from pycat.toolbox.vpt.host import (  # noqa: E402,F401
    segment_host_condensate, erode_host_mask, infer_host_from_beads)

# ---------------------------------------------------------------------------
# 3. Bead detection + linking-condition probes + GPU/parallel backends  ->  moved to detection.py (1.6.238)
# ---------------------------------------------------------------------------
from pycat.toolbox.vpt.detection import (  # noqa: E402,F401
    detect_beads_frame, blob_log_gpu, bead_half_from_size, build_airy_template, build_hot_pixel_mask, dedup_detections_ring_merge, dedup_detections, build_bead_template, score_beads_template, classify_beads, _read_frame_from_descriptor, _detect_frame_worker, assess_linking_conditions, estimate_linking_distance_um, gpu_matches_cpu, detect_beads_stack)

# ---------------------------------------------------------------------------
# 4b. Bead population routing (primary probes vs. aggregate secondary set)
# ---------------------------------------------------------------------------

def split_bead_populations(detections_df: pd.DataFrame,
                           recover_out_of_plane: bool = False) -> dict:
    """Separate classified detections into three NEVER-MIXED populations.

    The three bead classes are kept strictly separate so microrheology runs on a
    known, homogeneous probe population:

      singlet     (green)  — clean, in-focus single beads. The correct default
                             for Stokes-Einstein viscosity (known single-bead
                             size, reliable centroid).
      out_of_plane(yellow) — dim / out-of-focus beads. Position is less certain,
                             so they are NOT mixed into the singlet measurement
                             by default. They can be analysed ON THEIR OWN (to
                             check whether they give a consistent viscosity) and
                             only then, at the user's choice, combined with the
                             singlets.
      aggregate   (red)    — aggregates (and ambiguous). Their size biases
                             Stokes-Einstein, so they are ALWAYS a separate
                             readout (count / size / mobility), never in the
                             viscosity population.

    Returns a dict with 'singlet', 'out_of_plane', 'aggregate' DataFrames, plus
    'primary' for backward compatibility (singlets, or singlets+out_of_plane if
    recover_out_of_plane is True). Callers that want a specific population should
    read the named key directly rather than 'primary'.
    """
    if detections_df is None or detections_df.empty \
            or 'bead_class' not in detections_df.columns:
        empty = pd.DataFrame()
        base = detections_df if detections_df is not None else empty
        return dict(primary=base, singlet=base, out_of_plane=empty,
                    aggregate=empty)
    df = detections_df
    singlet = df[df['bead_class'].isin(['singlet', 'unfit'])].reset_index(drop=True)
    out_of_plane = df[df['bead_class'] == 'out_of_plane'].reset_index(drop=True)
    aggregate = df[df['bead_class'].isin(['aggregate', 'ambiguous'])].reset_index(drop=True)
    # 'primary' kept for backward compatibility with existing callers.
    if recover_out_of_plane and len(out_of_plane):
        primary = pd.concat([singlet, out_of_plane], ignore_index=True)
    else:
        primary = singlet
    return dict(primary=primary, singlet=singlet,
                out_of_plane=out_of_plane, aggregate=aggregate)


def select_bead_population(detections_df: pd.DataFrame, which: str = 'singlet') -> pd.DataFrame:
    """Return one (or a deliberate combination) of the bead populations for
    microrheology, by name.

    which : 'singlet' (green, default) | 'out_of_plane' (yellow) |
            'singlet+out_of_plane' (green+yellow, opt-in) | 'aggregate' (red).
    Populations are never mixed except the explicit 'singlet+out_of_plane'.
    """
    pops = split_bead_populations(detections_df)
    if which == 'singlet+out_of_plane':
        parts = [pops['singlet'], pops['out_of_plane']]
        parts = [p for p in parts if p is not None and len(p)]
        return pd.concat(parts, ignore_index=True) if parts else pops['singlet']
    return pops.get(which, pops['singlet'])


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
# 5. Ensemble center-of-mass drift correction  ->  moved to drift.py (1.6.236)
# ---------------------------------------------------------------------------
from pycat.toolbox.vpt.drift import (  # noqa: E402,F401
    reclassify_by_temporal_stability, drift_correct_com)

# ---------------------------------------------------------------------------
# 6. Stokes-Einstein viscosity  ->  moved to viscosity.py (1.6.235)
# ---------------------------------------------------------------------------
from pycat.toolbox.vpt.viscosity import (  # noqa: E402,F401
    viscosity_measurement, viscosity_interval_from_diffusion, viscosity_from_diffusion)

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
    min_track_length: int = MIN_TRACK_LENGTH_FRAMES,
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


def compare_detection_variants(
    bead_stack,
    variant_a: str = 'baseline',
    variant_b: str = 'baseline',
    microns_per_pixel: float = 1.0,
    max_frames: Optional[int] = None,
    **detect_kwargs,
) -> dict:
    """
    Run bead detection under TWO variants on the SAME stack and report how the
    detections/classifications differ. This is the staging safety net for the
    detection rework: every proposed change is measured against the
    1.5.329-validated baseline before it is trusted, and a regression is visible
    immediately rather than only surfacing in the final viscosity.

    Parameters
    ----------
    bead_stack : (T, H, W) stack (lazy or array).
    variant_a, variant_b : detection_variant names to compare (default both
        'baseline'; pass e.g. variant_b='ring_merge' to A/B a new variant).
    microns_per_pixel : pixel size (passed through to detection).
    max_frames : cap the number of frames for a fast comparison (None = all).
    **detect_kwargs : forwarded to detect_beads_stack (quality_mode, parallel…).

    Returns
    -------
    dict with:
        counts_a / counts_b   : total detections per variant
        class_counts_a / _b   : bead_class value-counts per variant
        n_frames              : frames compared
        summary               : human-readable one-line diff
        det_a / det_b         : the two detection DataFrames (for deeper analysis)
    """
    import numpy as np
    frame_indices = None
    if max_frames is not None:
        try:
            T = int(np.asarray(bead_stack).shape[0]) if not hasattr(bead_stack, 'shape') \
                else int(bead_stack.shape[0])
            frame_indices = list(range(min(T, int(max_frames))))
        except Exception:
            frame_indices = None

    det_a = detect_beads_stack(
        bead_stack, microns_per_pixel=microns_per_pixel,
        frame_indices=frame_indices, detection_variant=variant_a, **detect_kwargs)
    det_b = detect_beads_stack(
        bead_stack, microns_per_pixel=microns_per_pixel,
        frame_indices=frame_indices, detection_variant=variant_b, **detect_kwargs)

    cc_a = (det_a['bead_class'].value_counts().to_dict()
            if 'bead_class' in det_a else {})
    cc_b = (det_b['bead_class'].value_counts().to_dict()
            if 'bead_class' in det_b else {})
    n_frames = int(det_a['frame'].nunique()) if 'frame' in det_a else 0

    summary = (
        f"[{variant_a}] {len(det_a)} dets {cc_a}  vs  "
        f"[{variant_b}] {len(det_b)} dets {cc_b}  over {n_frames} frames")

    return dict(
        counts_a=len(det_a), counts_b=len(det_b),
        class_counts_a=cc_a, class_counts_b=cc_b,
        n_frames=n_frames, summary=summary,
        det_a=det_a, det_b=det_b)
