"""
PyCAT FRAP File I/O
===================
Readers for FRAP acquisition formats:

  - Lumicks C-Trap  (.h5 via lumicks.pylake) — confocal scans with embedded
    timestamps and bleach/recovery scan center points.
  - Andor Dragonfly / Fusion (.ims) — Mosaic / MicroPoint photostimulation
    with one or two cameras. Loaded through the existing IMS reader; this
    module extracts the timing needed for FRAP.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2026
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from pycat.utils.general_utils import debug_log

from napari.utils.notifications import show_info as napari_show_info
from napari.utils.notifications import show_warning as napari_show_warning


# ---------------------------------------------------------------------------
# Lumicks C-Trap .h5
# ---------------------------------------------------------------------------

def lumicks_available() -> bool:
    try:
        import lumicks.pylake  # noqa: F401
        return True
    except ImportError:
        return False


def load_lumicks_frap(
    h5_path: str,
    channel: str = 'green',
    recovery_scan_index: int = 0,
    trim_last_frames: int = 0,
) -> dict:
    """
    Load a Lumicks C-Trap FRAP recovery scan from an .h5 file.

    Mirrors the manual pylake workflow: open the file, pull the recovery
    scan image stack for the chosen channel, and compute the frame interval
    (imagetime) from the scan timestamps.

    Parameters
    ----------
    h5_path : path to the Lumicks .h5 file.
    channel : 'green' | 'red' | 'blue' — confocal photon-count channel.
    recovery_scan_index : which scan in the file is the recovery movie
        (file.scans is ordered; the recovery scan is typically index 0).
    trim_last_frames : drop this many trailing frames (the manual workflow
        trims a couple of incomplete frames at the end of some scans).

    Returns
    -------
    dict with:
        stack             : (T, H, W) recovery movie (float32)
        frame_interval_s  : time between recovery frames (imagetime)
        center_um         : (x, y) scan center in µm (for ROI placement)
        channel           : the channel loaded
        n_frames          : number of frames
    """
    try:
        import lumicks.pylake as pylake
    except ImportError:
        raise ImportError(
            "lumicks.pylake not installed. Run: pip install lumicks.pylake")

    f = pylake.File(h5_path)
    scan_names = list(f.scans)
    if not scan_names:
        raise ValueError(f"No scans found in {h5_path}")
    if recovery_scan_index >= len(scan_names):
        recovery_scan_index = 0
    scan = f.scans[scan_names[recovery_scan_index]]

    stack = np.asarray(scan.get_image(channel)).astype(np.float32)
    if trim_last_frames > 0 and stack.shape[0] > trim_last_frames:
        stack = stack[:-trim_last_frames]

    # Frame interval from timestamps: difference between the first pixel
    # timestamp of consecutive frames, in seconds (timestamps are in ns).
    frame_interval_s = 1.0
    try:
        ts = scan.timestamps
        # ts[frame][row][col] style indexing in the manual workflow:
        # imagetime = (t_frame1_start - t_frame0_start) / 1e9
        t0 = ts[0][0][0] if np.ndim(ts) >= 3 else ts[0]
        t1 = ts[1][0][0] if np.ndim(ts) >= 3 else ts[1]
        frame_interval_s = abs(float(t1) - float(t0)) / 1e9
    except Exception as _e:
        debug_log("frap_io: reading frame interval from timestamps "
                  "(recovery timing may be off)", _e)
        pass

    center_um = (np.nan, np.nan)
    try:
        cp = scan.center_point_um
        center_um = (float(cp['x']), float(cp['y']))
    except Exception as _e:
        debug_log("frap_io: reading bleach center_point_um "
                  "(ROI placement may be off)", _e)
        pass

    return dict(
        stack=stack,
        frame_interval_s=frame_interval_s,
        center_um=center_um,
        channel=channel,
        n_frames=int(stack.shape[0]),
    )


def compute_lumicks_timelag(
    h5_path: str,
    bleach_scan_index: int = 1,
    recovery_scan_index: int = 0,
) -> float:
    """
    Compute the bleach→recovery time lag (s) from Lumicks scan timestamps:
    the gap between the end of the bleach scan and the start of the first
    recovery frame.

    Returns the lag in seconds, or 0.0 if it cannot be determined.
    """
    try:
        import lumicks.pylake as pylake
        f = pylake.File(h5_path)
        scan_names = list(f.scans)
        rec = f.scans[scan_names[recovery_scan_index]]
        bl  = f.scans[scan_names[bleach_scan_index]]
        bl_end  = bl.timestamps[-1][-1][-1] if np.ndim(bl.timestamps) >= 3 else bl.timestamps[-1]
        rec_start = rec.timestamps[0][0][0] if np.ndim(rec.timestamps) >= 3 else rec.timestamps[0]
        return abs(float(rec_start) - float(bl_end)) / 1e9
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Andor Dragonfly / Fusion .ims photostimulation metadata
# ---------------------------------------------------------------------------

def extract_andor_photostim_rois(ims_path: str) -> list:
    """
    Attempt to read photostimulation ROI definitions from an Andor Fusion
    .ims file (Mosaic / MicroPoint).

    Fusion stores stimulation ROI geometry in the Imaris HDF5 custom
    attributes, but the exact location varies by Fusion version and is not
    part of the standard Imaris schema. This function searches the common
    locations and returns whatever ROI centres/sizes it can find; if none
    are found it returns an empty list and the user should draw the ROIs
    manually (which is always supported).

    Returns
    -------
    list of dicts, each {'center_yx': (y, x), 'radius_px': r} in pixels,
    or [] if no photostim metadata could be parsed.
    """
    rois = []
    try:
        import h5py
        with h5py.File(ims_path, 'r') as h5:
            # Search for any group/attr mentioning stimulation / bleach / ROI
            candidates = []

            def _visit(name, obj):
                lname = name.lower()
                if any(k in lname for k in
                       ('stimul', 'bleach', 'photo', 'roi', 'mosaic', 'micropoint')):
                    candidates.append(name)
            h5.visititems(_visit)

            for cand in candidates:
                obj = h5[cand]
                attrs = dict(obj.attrs) if hasattr(obj, 'attrs') else {}
                cx = cy = r = None
                for k, v in attrs.items():
                    kl = k.lower()
                    try:
                        val = float(np.asarray(v).ravel()[0])
                    except Exception:
                        continue
                    if 'centerx' in kl or 'center_x' in kl or kl.endswith('x'):
                        cx = val
                    elif 'centery' in kl or 'center_y' in kl or kl.endswith('y'):
                        cy = val
                    elif 'radius' in kl or 'size' in kl:
                        r = val
                if cx is not None and cy is not None:
                    rois.append({'center_yx': (cy, cx),
                                 'radius_px': r if r is not None else 20.0})
    except Exception:
        pass

    if not rois:
        napari_show_info(
            "No photostimulation ROI metadata found in the .ims file "
            "(Fusion stores this in a version-specific location). Draw the "
            "bleach ROIs manually — multiple ROIs are supported.")
    return rois

# ---------------------------------------------------------------------------
# Lumicks C-Trap force traces (droplet fusion)
# ---------------------------------------------------------------------------

def load_lumicks_fusion(h5_path: str) -> dict:
    """
    Load C-Trap force traces for a droplet-fusion experiment.

    Two optically-trapped droplets are brought into contact; the coalescence
    relaxation is recorded on the trapped beads as force transients. This
    reads the high-frequency force channels (Force 1x/1y/2x/2y) used in the
    manual fusion workflow.

    Parameters
    ----------
    h5_path : path to the Lumicks .h5 file.

    Returns
    -------
    dict with:
        forces        : {'F1x','F1y','F2x','F2y'} → 1D arrays (whichever exist)
        sample_rate_hz: force sampling rate (Hz) if available, else None
        n_samples     : length of the force traces
    """
    try:
        import lumicks.pylake as pylake
    except ImportError:
        raise ImportError(
            "lumicks.pylake not installed. Run: pip install lumicks.pylake")

    f = pylake.File(h5_path)
    forces = {}
    sample_rate = None
    channel_map = {
        'F1x': ('Force HF', 'Force 1x'),
        'F1y': ('Force HF', 'Force 1y'),
        'F2x': ('Force HF', 'Force 2x'),
        'F2y': ('Force HF', 'Force 2y'),
    }
    for key, (grp, ch) in channel_map.items():
        try:
            chan = f[grp][ch]
            forces[key] = np.asarray(chan.data)
            if sample_rate is None:
                try:
                    sample_rate = float(chan.sample_rate)
                except Exception:
                    pass
        except Exception:
            continue

    if not forces:
        # Fall back to low-frequency force if HF is absent
        for key, ch in [('F1x', 'Force 1x'), ('F1y', 'Force 1y'),
                        ('F2x', 'Force 2x'), ('F2y', 'Force 2y')]:
            try:
                chan = f['Force LF'][ch]
                forces[key] = np.asarray(chan.data)
                if sample_rate is None:
                    sample_rate = float(getattr(chan, 'sample_rate', 0)) or None
            except Exception:
                continue

    if not forces:
        raise ValueError(
            f"No force channels found in {h5_path} (looked for Force HF/LF "
            "Force 1x/1y/2x/2y).")

    n = len(next(iter(forces.values())))
    return dict(forces=forces, sample_rate_hz=sample_rate, n_samples=int(n))

# ---------------------------------------------------------------------------
# Lumicks C-Trap force-distance curves (DNA tethering)
# ---------------------------------------------------------------------------

def load_lumicks_fd(h5_path: str,
                    force_channel: str = None,
                    distance_channel: str = None) -> dict:
    """
    Load a force-distance (FD) trace for a DNA-tethering C-Trap experiment.

    Two beads are joined by a molecular tether; repeated stretch/relax cycles
    reveal rips and unzips in the force-vs-distance curve. This reads the
    paired Distance and Force channels from the .h5 (preferring low-frequency
    'Force LF' which is downsampled to the distance rate), auto-detecting the
    available channel names.

    Parameters
    ----------
    h5_path : path to the Lumicks .h5 file.
    force_channel : explicit force channel name under 'Force LF' (e.g.
        'Trap 2', 'Force 2x'). If None, the first available is used.
    distance_channel : explicit distance channel name under 'Distance' (e.g.
        'Distance 1'). If None, the first available is used.

    Returns
    -------
    dict with:
        force            : 1D force array (pN)
        distance         : 1D distance array (µm, as stored)
        time_s           : 1D time array (s) if derivable, else sample index
        force_channel    : the force channel actually used
        distance_channel : the distance channel actually used
        available_force  : list of force channels found
        available_distance : list of distance channels found
        sample_rate_hz   : force sample rate if available
    """
    try:
        import lumicks.pylake as pylake
    except ImportError:
        raise ImportError(
            "lumicks.pylake not installed. Run: pip install lumicks.pylake")

    f = pylake.File(h5_path)

    # Enumerate available distance and force channels
    def _list_group(group_name):
        try:
            return list(f[group_name])
        except Exception:
            return []

    dist_channels = _list_group('Distance')
    # Force: prefer LF (matched to distance sampling), fall back to HF
    force_group = 'Force LF' if _list_group('Force LF') else 'Force HF'
    force_channels = _list_group(force_group)

    if not dist_channels:
        raise ValueError(f"No Distance channels found in {h5_path}.")
    if not force_channels:
        raise ValueError(f"No Force channels found in {h5_path}.")

    dch = distance_channel if (distance_channel in dist_channels) else dist_channels[0]
    fch = force_channel if (force_channel in force_channels) else force_channels[0]

    dist_obj = f['Distance'][dch]
    force_obj = f[force_group][fch]
    distance = np.asarray(dist_obj.data)
    force = np.asarray(force_obj.data)

    # Align lengths (distance and LF force are usually matched; guard anyway)
    n = min(len(distance), len(force))
    distance, force = distance[:n], force[:n]

    sample_rate = None
    try:
        sample_rate = float(force_obj.sample_rate)
    except Exception:
        pass

    # Build a time axis from timestamps if available, else sample index
    try:
        ts = np.asarray(force_obj.timestamps[:n], dtype=float)
        time_s = (ts - ts[0]) / 1e9   # ns → s
    except Exception:
        if sample_rate:
            time_s = np.arange(n) / sample_rate
        else:
            time_s = np.arange(n, dtype=float)

    return dict(
        force=force, distance=distance, time_s=time_s,
        force_channel=fch, distance_channel=dch,
        available_force=force_channels, available_distance=dist_channels,
        sample_rate_hz=sample_rate)

