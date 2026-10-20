"""VPT step 4, the science of it: split the detections into populations, link the microrheology population, measure
the drift, promote stable dim tracks, and link the aggregates on their own.

It was the body of the panel's worker closure (``vpt_ui._on_link``), so batch could not replay the step at all. Now
the panel and ``replay_vpt_link_trajectories`` both run it through ``OperationService`` ('vpt.link_beads').
"""
from __future__ import annotations


def link_bead_populations(detections, linker='bayesian', max_linking_distance_um=1.0, max_frame_gap=2,
                          microns_per_pixel=1.0, population='singlet', promote_stable_to_singlet=True,
                          progress_callback=None):
    """``{primary, aggregate_tracks, aggregate_stats, aggregates, drift}`` for one movie's bead detections.

    ``primary`` are the linked tracks of the chosen ``population`` (the microrheology set) with the measured drift
    stored BESIDE the centroids (``attach_drift``); with ``promote_stable_to_singlet`` a dim track that persists
    stably is reclassified as a real bead. Aggregates are always linked separately and never enter the viscosity
    set. Detections without a ``bead_class`` column are all primary."""
    from pycat.toolbox.vpt.measured_drift import attach_drift, measure_drift
    from pycat.toolbox.vpt_tools import (
        _link,
        aggregate_population_stats,
        reclassify_by_temporal_stability,
        select_bead_population,
        split_bead_populations,
    )
    det = detections
    if 'bead_class' in det.columns:
        pops = split_bead_populations(det)
        primary = select_bead_population(det, population)
        aggregates = pops['aggregate']
    else:
        primary, aggregates = det, det.iloc[0:0]
    linked = _link(primary, linker, max_linking_distance_um, max_frame_gap, microns_per_pixel,
                   progress_callback=progress_callback)
    drift = measure_drift(linked, microns_per_pixel)
    ptracks = attach_drift(linked, drift, microns_per_pixel)
    if promote_stable_to_singlet:
        ptracks = reclassify_by_temporal_stability(ptracks)
    atracks = None
    if len(aggregates) >= 2:
        try:
            atracks = _link(aggregates, linker, max_linking_distance_um, max_frame_gap, microns_per_pixel)
        except Exception:  # broad-ok: optional_probe — the aggregate population is secondary; the primary stands
            atracks = None
    total_by_frame = det.groupby('frame').size()
    astats = (aggregate_population_stats(aggregates, total_by_frame=total_by_frame) if len(aggregates) else None)
    return dict(primary=ptracks, aggregate_tracks=atracks, aggregate_stats=astats, aggregates=aggregates,
                drift=drift)
