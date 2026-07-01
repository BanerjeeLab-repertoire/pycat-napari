"""
PyCAT TrackMate Bridge
========================
Optional integration that hands PyCAT's condensate/cell detections directly
to real TrackMate (running inside an embedded ImageJ2/Fiji JVM via pyimagej)
for linking, instead of using PyCAT's own Bayesian/Hungarian tracker.

Why bridge to TrackMate rather than reimplement it
------------------------------------------------------
TrackMate's LAP tracker (Jaqaman et al. 2008) folds merging and splitting
directly into the same global linear-assignment optimisation used for
frame-to-frame linking and gap-closing, solved once across the whole
video. PyCAT's own tracker (dynamic_spatial_tools.link_trajectories_bayesian)
uses the same core Hungarian-algorithm foundation but handles merge/fission
as a separate, less-unified post-hoc step, and does gap-closing in two
passes rather than one global solve. TrackMate is also a decade-matured,
extensively validated tool with a real Kalman tracker, manual track editing
(TrackScheme), and community-standard ground-truth benchmarking behind it —
reimplementing all of that natively is a much larger undertaking than
calling the real thing.

Architecture
------------
Detection and tracking are decoupled in TrackMate by design. This bridge
exploits that: PyCAT's own segmentation already produced the condensate/
cell detections (via extract_frame_properties or an equivalent per-frame
regionprops pass) — TrackMate's detection step is skipped entirely, and
the pre-computed spots are injected directly into a TrackMate Model. Only
the LINKING step (TrackMate's actual strength) is delegated to Java; results
are converted back into the exact same tidy DataFrame schema PyCAT's own
trackers produce, so every downstream tool (MSD, fusion kinetics, Kaplan-
Meier, coarsening) works completely unchanged regardless of which tracker
produced the trajectories.

The JVM is started via pyimagej + a Maven-resolved Fiji distribution
('sc.fiji:fiji'), which is downloaded and cached on first use — this can
take several minutes and requires network access the first time only.

Headless-safety note
---------------------
TrackMate MUST be driven via its documented scripting API — constructing
Model/Settings/TrackMate objects directly — not invoked as a GUI plugin
command (ij.py.run_plugin("TrackMate", ...)), which requires an active
ImagePlus/WindowManager state that does not exist in headless mode and
fails with "Please open an image before running TrackMate" (a real,
reported failure mode). This module only ever uses the scripting API path.

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


# ---------------------------------------------------------------------------
# Availability check + lazy JVM singleton
# ---------------------------------------------------------------------------

_ij_gateway = None   # module-level singleton — JVM can only start once per process
_trackmate_classes = None


def trackmate_bridge_available() -> bool:
    """Check whether pyimagej is installed, without starting the JVM."""
    try:
        import imagej  # noqa: F401
        return True
    except ImportError:
        return False


def _get_gateway(fiji_endpoint: str = 'sc.fiji:fiji', heap_gb: int = 4):
    """
    Lazily initialise (once per process) the ImageJ2/Fiji gateway and
    resolve the TrackMate Java classes needed for scripted, headless
    tracking. Subsequent calls return the cached gateway/classes —
    the JVM cannot be restarted once started, so this MUST be a singleton.

    Parameters
    ----------
    fiji_endpoint : Maven coordinate or local Fiji.app path.
        Default downloads and caches a full Fiji distribution (which
        includes TrackMate) on first use — this can take several minutes
        and requires network access the first time only; subsequent runs
        reuse the local Maven/jgo cache and start in a few seconds.
    heap_gb : JVM max heap size in GB. Increase for very large tracking
        problems (many thousands of spots).

    Returns
    -------
    (ij, classes) where `ij` is the pyimagej gateway and `classes` is a
    dict of imported TrackMate Java classes.

    Raises
    ------
    ImportError if pyimagej is not installed.
    RuntimeError if JVM/Fiji initialisation fails for any other reason.
    """
    global _ij_gateway, _trackmate_classes

    if _ij_gateway is not None:
        return _ij_gateway, _trackmate_classes

    try:
        import imagej
        import scyjava as sj
    except ImportError as e:
        raise ImportError(
            "TrackMate bridge requires pyimagej. Install with:\n"
            "  pip install pycat-napari[trackmate]\n"
            "  (or directly: pip install pyimagej)\n"
            "and ensure a Java runtime (JDK 11+) is available on PATH — "
            "pip does not install Java itself; use your OS package manager, "
            "conda (conda install openjdk=11), or Adoptium/Oracle directly.\n"
            f"Original error: {e}"
        )

    try:
        sj.config.add_options(f'-Xmx{heap_gb}g')
        ij = imagej.init(fiji_endpoint, mode='headless')
    except Exception as e:
        raise RuntimeError(
            f"Failed to initialise ImageJ2/Fiji gateway (endpoint="
            f"'{fiji_endpoint}'). First-run downloads require network "
            f"access and can take several minutes. Original error: {e}"
        )

    # Resolve TrackMate classes via the scripting API (never the plugin
    # dispatcher — see module docstring for why this matters headless).
    # Try the modern package path first (post-refactor, TrackMate v7+),
    # falling back to the legacy 'sparselap' path for older installs.
    try:
        classes = dict(
            Model=sj.jimport('fiji.plugin.trackmate.Model'),
            Settings=sj.jimport('fiji.plugin.trackmate.Settings'),
            TrackMate=sj.jimport('fiji.plugin.trackmate.TrackMate'),
            Spot=sj.jimport('fiji.plugin.trackmate.Spot'),
            SpotCollection=sj.jimport('fiji.plugin.trackmate.SpotCollection'),
            Logger=sj.jimport('fiji.plugin.trackmate.Logger'),
        )
        try:
            classes['SparseLAPTrackerFactory'] = sj.jimport(
                'fiji.plugin.trackmate.tracking.jaqaman.SparseLAPTrackerFactory')
            classes['LAPUtils'] = sj.jimport(
                'fiji.plugin.trackmate.tracking.jaqaman.LAPUtils')
        except Exception:
            classes['SparseLAPTrackerFactory'] = sj.jimport(
                'fiji.plugin.trackmate.tracking.sparselap.SparseLAPTrackerFactory')
            classes['LAPUtils'] = sj.jimport(
                'fiji.plugin.trackmate.tracking.LAPUtils')
        try:
            classes['KalmanTrackerFactory'] = sj.jimport(
                'fiji.plugin.trackmate.tracking.kalman.KalmanTrackerFactory')
        except Exception:
            classes['KalmanTrackerFactory'] = None
    except Exception as e:
        raise RuntimeError(
            f"Fiji gateway started but TrackMate classes could not be "
            f"resolved — is TrackMate included in this Fiji installation? "
            f"Original error: {e}"
        )

    _ij_gateway = ij
    _trackmate_classes = classes
    return ij, classes


# ---------------------------------------------------------------------------
# Core bridge function
# ---------------------------------------------------------------------------

def run_trackmate_lap_tracking(
    props_df: pd.DataFrame,
    max_linking_distance_um: float = 2.0,
    max_gap_closing_distance_um: float = 3.0,
    max_frame_gap: int = 2,
    allow_merging: bool = True,
    allow_splitting: bool = True,
    use_kalman: bool = False,
    kalman_search_radius_um: float = 5.0,
    fiji_endpoint: str = 'sc.fiji:fiji',
) -> pd.DataFrame:
    """
    Link PyCAT-detected condensates/cells using real TrackMate, running in
    an embedded headless Fiji JVM.

    PyCAT's own detections (from extract_frame_properties or equivalent)
    are injected directly as TrackMate Spots — TrackMate's own detection
    step is never invoked, only its LAP tracker.

    Parameters
    ----------
    props_df : pd.DataFrame
        Output of extract_frame_properties() — must have columns
        frame, object_id, y_um, x_um, area_um2 (major_axis_um/
        minor_axis_um/eccentricity are carried through if present but
        not required).
    max_linking_distance_um : max frame-to-frame linking distance (µm).
    max_gap_closing_distance_um : max distance for gap-closing links (µm).
    max_frame_gap : max number of frames a track can vanish for and still
        be reconnected during gap-closing.
    allow_merging, allow_splitting : bool
        Whether TrackMate's LAP solve should model track merge/split
        events directly in the same global assignment — this is the
        headline difference from PyCAT's own tracker (see module docstring).
    use_kalman : bool
        Use TrackMate's Kalman tracker (true state-space velocity/
        uncertainty propagation) instead of the LAP tracker. Better for
        objects with persistent directed motion; worse for merge/split
        since the Kalman tracker in TrackMate does not model those.
    kalman_search_radius_um : search radius for the Kalman tracker (µm).
    fiji_endpoint : passed through to _get_gateway() — see its docstring.

    Returns
    -------
    pd.DataFrame with the SAME schema as props_df, plus a `track_id`
    column — a drop-in replacement for PyCAT's own
    link_trajectories_bayesian() / link_trajectories() output, usable by
    every downstream biophysics function (compute_msd, kaplan_meier_
    lifetimes, detect_and_fit_fusions, fit_coarsening, etc.) unchanged.

    Raises
    ------
    ImportError if pyimagej is not installed.
    RuntimeError if TrackMate tracking fails for any other reason (the
    original Java exception message is included).
    """
    if props_df.empty:
        return props_df.assign(track_id=pd.Series(dtype=int))

    ij, C = _get_gateway(fiji_endpoint)
    import scyjava as sj

    model = C['Model']()
    spots = C['SpotCollection']()

    # ── Inject PyCAT's detections directly as TrackMate Spots ────────────
    # Spot(x, y, z, radius, quality) — z=0 for 2D condensate/cell tracking;
    # radius derived from area (equivalent disc radius); quality unused by
    # the LAP cost function here (no per-spot filtering applied) but must
    # be a finite value for the Java constructor.
    has_major_axis = 'major_axis_um' in props_df.columns

    java_spots_by_row = {}
    for idx, row in props_df.iterrows():
        radius = float(np.sqrt(row['area_um2'] / np.pi)) if row['area_um2'] > 0 else 0.5
        spot = C['Spot'](float(row['x_um']), float(row['y_um']), 0.0,
                         max(radius, 0.1), 1.0)
        spot.putFeature('POSITION_T', float(row['frame']))
        if has_major_axis and not pd.isna(row.get('major_axis_um')):
            spot.putFeature('MAJOR_AXIS_LENGTH', float(row['major_axis_um']))
        spots.add(spot, int(row['frame']))
        java_spots_by_row[idx] = spot

    model.setSpots(spots, False)

    # ── Configure Settings + tracker ──────────────────────────────────────
    settings = C['Settings'](None)   # no source ImagePlus — spots are pre-supplied

    if use_kalman and C.get('KalmanTrackerFactory') is not None:
        settings.trackerFactory = C['KalmanTrackerFactory']()
        tracker_settings = settings.trackerFactory.getDefaultSettings()
        tracker_settings.put('KALMAN_SEARCH_RADIUS', float(kalman_search_radius_um))
        tracker_settings.put('LINKING_MAX_DISTANCE', float(max_linking_distance_um))
        tracker_settings.put('MAX_FRAME_GAP', int(max_frame_gap))
    else:
        settings.trackerFactory = C['SparseLAPTrackerFactory']()
        tracker_settings = C['LAPUtils'].getDefaultLAPSettingsMap()
        tracker_settings.put('LINKING_MAX_DISTANCE', float(max_linking_distance_um))
        tracker_settings.put('ALLOW_GAP_CLOSING', True)
        tracker_settings.put('GAP_CLOSING_MAX_DISTANCE', float(max_gap_closing_distance_um))
        tracker_settings.put('MAX_FRAME_GAP', int(max_frame_gap))
        tracker_settings.put('ALLOW_TRACK_MERGING', bool(allow_merging))
        tracker_settings.put('ALLOW_TRACK_SPLITTING', bool(allow_splitting))

    settings.trackerSettings = tracker_settings

    trackmate = C['TrackMate'](model, settings)

    # execTracking() only — detection is deliberately never invoked, spots
    # were supplied directly above.
    if not trackmate.execTracking():
        raise RuntimeError(
            f"TrackMate tracking failed: {trackmate.getErrorMessage()}")

    # ── Read back the track graph into PyCAT's tidy schema ───────────────
    track_model = model.getTrackModel()
    spot_to_track = {}
    for track_id in track_model.trackIDs(True):
        for spot in track_model.trackSpots(track_id):
            spot_to_track[spot.ID()] = int(track_id)

    result = props_df.copy()
    track_ids = []
    for idx, row in props_df.iterrows():
        spot = java_spots_by_row[idx]
        track_ids.append(spot_to_track.get(spot.ID(), -1))
    result['track_id'] = track_ids

    n_unlinked = int((result['track_id'] == -1).sum())
    if n_unlinked > 0:
        warnings.warn(
            f"{n_unlinked} of {len(result)} detections were not assigned "
            f"to any track by TrackMate (isolated single-frame spots with "
            f"no viable link within max_linking_distance_um).", stacklevel=2)

    return result


def shutdown_trackmate_bridge():
    """
    Explicitly note that the JVM cannot be restarted once started in this
    process. There is no real 'shutdown' available — pyimagej/JPype does
    not support tearing down and reinitialising a JVM within the same
    Python process. This function exists only to make that limitation
    discoverable rather than silently confusing; the gateway stays alive
    for the lifetime of the PyCAT session once first used.
    """
    if _ij_gateway is not None:
        warnings.warn(
            "The TrackMate/Fiji JVM cannot be shut down or restarted within "
            "the same PyCAT session once started — this is a hard limitation "
            "of the underlying JPype/pyimagej bridge, not a PyCAT bug. "
            "Restart PyCAT if you need a fresh JVM (e.g. after changing "
            "fiji_endpoint).", stacklevel=2)
