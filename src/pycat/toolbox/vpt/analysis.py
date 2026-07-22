"""VPT **pipeline orchestration** — split out of vpt_tools (1.6.239).

run_vpt_analysis is the headless/batch entry point: segment host -> erode -> detect beads -> link ->
drift-correct -> MSD -> Stokes-Einstein viscosity, wiring together the domain modules. _link dispatches to
the chosen linker (TrackMate LAP or a native PyCAT linker); compare_detection_variants sweeps detection
settings. Moved VERBATIM - no number changed; pinned by the VPT tests. With this move vpt_tools.py becomes
a PURE re-export shim over the toolbox/vpt/ package. The tools module re-exports the two public entry
points plus _link (imported by vpt_ui).
"""
from __future__ import annotations

from typing import Optional
import numpy as np
import pandas as pd
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.toolbox.condensate_physics_tools import MIN_TRACK_LENGTH_FRAMES
from pycat.toolbox.vpt.host import segment_host_condensate, erode_host_mask
from pycat.toolbox.vpt.detection import detect_beads_stack
from pycat.toolbox.vpt.populations import split_bead_populations, aggregate_population_stats
from pycat.toolbox.vpt.drift import drift_correct_com
from pycat.toolbox.vpt.viscosity import viscosity_from_diffusion


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
