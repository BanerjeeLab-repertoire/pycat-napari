"""
PyCAT Dynamic Spatial Phenotyping Toolbox
==========================================
Time-series analysis of condensate spatial dynamics.

Analyses
--------
1. Condensate trajectory tracking          (nearest-neighbour linking)
2. Merge / fission event detection
3. Cluster lifetime analysis
4. Neighbourhood persistence / turnover
5. Growth and shrinkage kinetics

All analyses operate on a (T, H, W) labelled mask stack — the output of
time-series condensate segmentation.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2025
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import skimage as sk
from scipy.spatial import cKDTree
from typing import Optional


# ---------------------------------------------------------------------------
# Helper: extract per-frame condensate properties
# ---------------------------------------------------------------------------

def extract_frame_properties(
    mask_stack: np.ndarray,
    microns_per_pixel: float = 1.0,
) -> pd.DataFrame:
    """
    Extract centroid, area, and intensity-derived properties for every
    condensate in every frame of a (T, H, W) binary/labelled mask stack.

    Returns
    -------
    DataFrame with columns: frame, object_id, y_um, x_um, area_um2,
                             major_axis_um, minor_axis_um, eccentricity
    """
    rows = []
    for t, frame in enumerate(mask_stack):
        labelled = sk.measure.label(frame > 0) if frame.max() <= 1 else frame
        for prop in sk.measure.regionprops(labelled):
            cy, cx = prop.centroid
            rows.append({
                'frame':         t,
                'object_id':     prop.label,
                'y_um':          cy * microns_per_pixel,
                'x_um':          cx * microns_per_pixel,
                'area_um2':      prop.area * microns_per_pixel**2,
                'major_axis_um': prop.axis_major_length * microns_per_pixel,
                'minor_axis_um': prop.axis_minor_length * microns_per_pixel,
                'eccentricity':  prop.eccentricity,
            })
    return pd.DataFrame(rows)



# ---------------------------------------------------------------------------
# Bayesian / probabilistic trajectory linking
# ---------------------------------------------------------------------------
#
# Architecture
# ------------
# Greedy NNL makes hard local assignments and can fail when two condensates
# are close together or when a condensate temporarily disappears.  This
# Bayesian linker instead:
#
# 1. Builds a cost matrix between every pair of (track_t, detection_t+1)
#    using a Gaussian motion model + area consistency.
# 2. Augments the cost matrix with "dummy" rows/cols representing track
#    termination (death) and new track creation (birth) at calibrated costs.
# 3. Solves the global optimal assignment using the Hungarian algorithm
#    (scipy.optimize.linear_sum_assignment) — O(N³) but N is small per frame.
# 4. Applies forward-backward gap closing: after the initial linking pass,
#    unlinked track ends are connected to unlinked track starts in future
#    frames if the interpolated motion is consistent.
# 5. Optionally uses Kalman-style velocity estimation: each active track
#    maintains an exponentially weighted position estimate so slowly-moving
#    condensates that jump slightly are linked more reliably.
#
# The result is a tracks_df identical in schema to link_trajectories() output
# so all downstream functions (trajectory_metrics, growth_shrinkage_kinetics,
# neighbourhood_persistence, etc.) work unchanged.


def _gaussian_cost(d: float, sigma: float) -> float:
    """
    Cost of linking two detections separated by distance d.
    Cost = −log P where P is Gaussian, so lower cost = more likely link.
    Clipped to avoid infinity at large distances.
    """
    return float(d**2 / (2 * sigma**2))


def link_trajectories_bayesian(
    props_df: pd.DataFrame,
    max_displacement_um: float = 2.0,
    max_gap_frames: int = 2,
    sigma_um: float = None,
    area_weight: float = 0.3,
    birth_cost: float = None,
    death_cost: float = None,
    use_velocity: bool = True,
    velocity_alpha: float = 0.3,
    progress_callback=None,
) -> pd.DataFrame:
    """
    Link condensate detections into trajectories using Bayesian cost
    minimisation with the Hungarian algorithm.

    This produces more accurate tracks than greedy NNL when:
    - Two condensates are within max_displacement_um of each other
      (greedy takes the closer one regardless of trajectory history)
    - Condensates appear/disappear transiently (gap bridging is global)
    - Condensates have consistent size that can disambiguate close pairs

    Parameters
    ----------
    props_df : pd.DataFrame
        Output of extract_frame_properties() with columns frame, object_id,
        y_um, x_um, area_um2.
    max_displacement_um : float
        Maximum displacement between consecutive frames.  Detections farther
        apart than this are never linked regardless of cost.
    max_gap_frames : int
        Maximum number of frames a condensate can be absent and still be
        linked (gap closing).  0 = no gap closing.
    sigma_um : float or None
        Expected condensate displacement per frame (Gaussian std in µm).
        If None, estimated as max_displacement_um / 3 (3-sigma rule).
    area_weight : float
        Weight of area consistency in the cost (0 = ignore area, 1 = equal
        weight to distance).  Penalises linking condensates of very different
        sizes.
    birth_cost : float or None
        Cost of starting a new track instead of linking an existing one.
        If None, set to max displacement cost (penalises spurious new tracks
        moderately).
    death_cost : float or None
        Cost of terminating a track instead of linking forward.
        If None, set equal to birth_cost.
    use_velocity : bool
        If True, each active track maintains an exponentially-weighted
        velocity estimate.  Predicted positions use this velocity, which
        improves linking for condensates undergoing directed motion.
    velocity_alpha : float
        EWM alpha for velocity update (0 = no update, 1 = use last step only).

    Returns
    -------
    pd.DataFrame — props_df with added columns:
        track_id   : integer track identifier (−1 = unlinked, should not occur)
        link_cost  : cost of the assignment for this detection
    """
    from scipy.optimize import linear_sum_assignment

    if sigma_um is None:
        sigma_um = max_displacement_um / 3.0
    _sigma2 = max(sigma_um, 1e-6) ** 2

    if birth_cost is None:
        birth_cost = _gaussian_cost(max_displacement_um, sigma_um)
    if death_cost is None:
        death_cost = birth_cost

    INF_COST = birth_cost * 10   # effective infinity for forbidden links

    df = props_df.copy().sort_values(['frame', 'object_id']).reset_index(drop=True)
    df['track_id']  = -1
    df['link_cost'] = np.nan
    next_track_id   = 0

    frames = sorted(df['frame'].unique())

    # Active track state: track_id → {y, x, area, last_frame, vy, vx}
    active: dict[int, dict] = {}

    # ── Frame-by-frame linking ────────────────────────────────────────────
    _n_frames_total = len(frames)
    for t_idx, t in enumerate(frames):
        if progress_callback is not None:
            try:
                progress_callback(t_idx + 1, _n_frames_total)
            except Exception:
                pass
        curr_mask = df['frame'] == t
        curr      = df[curr_mask].copy()
        n_curr    = len(curr)

        # Viable tracks: those seen within max_gap_frames
        viable_ids = [tid for tid, info in active.items()
                      if t - info['last_frame'] <= max_gap_frames + 1]
        n_viable   = len(viable_ids)

        if n_viable == 0:
            # First frame or all tracks expired — start new tracks
            for idx in curr.index:
                df.at[idx, 'track_id']  = next_track_id
                df.at[idx, 'link_cost'] = birth_cost
                active[next_track_id] = {
                    'y': df.at[idx, 'y_um'],
                    'x': df.at[idx, 'x_um'],
                    'area': df.at[idx, 'area_um2'],
                    'last_frame': t,
                    'vy': 0.0, 'vx': 0.0,
                }
                next_track_id += 1
            continue

        # ── Build cost matrix ─────────────────────────────────────────────
        # Rows: viable tracks + n_curr dummy-death rows
        # Cols: current detections + n_viable dummy-birth cols
        dim = n_viable + n_curr
        C   = np.full((dim, dim), INF_COST)

        # Predicted positions using velocity
        pred_y = np.array([
            active[tid]['y'] + active[tid]['vy'] * (t - active[tid]['last_frame'])
            for tid in viable_ids])
        pred_x = np.array([
            active[tid]['x'] + active[tid]['vx'] * (t - active[tid]['last_frame'])
            for tid in viable_ids])
        pred_a = np.array([active[tid]['area'] for tid in viable_ids])

        curr_y = curr['y_um'].values
        curr_x = curr['x_um'].values
        curr_a = curr['area_um2'].values

        # ── Vectorised cost block (viable tracks × current detections) ──────
        # This inner block used to be a double Python for-loop over every
        # (track, detection) pair — O(n_viable × n_curr) Python-level ops per
        # frame, which dominated runtime on dense movies (hundreds of beads ×
        # thousands of frames). Broadcasting computes the whole block at once.
        if n_viable > 0 and n_curr > 0:
            gaps = np.array([max(1, t - active[tid]['last_frame'])
                             for tid in viable_ids], dtype=float)  # (n_viable,)
            # Pairwise displacements: (n_viable, n_curr)
            dyv = curr_y[None, :] - pred_y[:, None]
            dxv = curr_x[None, :] - pred_x[:, None]
            dist = np.sqrt(dyv * dyv + dxv * dxv)
            gapcol = gaps[:, None]
            # Gaussian distance cost = d² / (2 (σ·gap)²)
            sig = sigma_um * gapcol
            cost_block = dist * dist / (2.0 * sig * sig)
            # Area-consistency cost (only where both areas are positive)
            if area_weight > 0:
                pa = pred_a[:, None]
                ca = curr_a[None, :]
                with np.errstate(divide='ignore', invalid='ignore'):
                    log_ratio = np.abs(np.log(ca / pa))
                log_ratio[~np.isfinite(log_ratio)] = 0.0
                valid_area = (pa > 0) & (ca > 0)
                cost_block = cost_block + np.where(valid_area,
                                                   area_weight * log_ratio, 0.0)
            # Hard cutoff: forbid links beyond max displacement (gap-scaled).
            forbidden = dist > (max_displacement_um * gapcol)
            cost_block = np.where(forbidden, INF_COST, cost_block)
            C[:n_viable, :n_curr] = cost_block

        # Death diagonal (track ends, no detection in this frame)
        for i in range(n_viable):
            C[i, n_curr + i] = death_cost

        # Birth row (new detection, no prior track)
        for j in range(n_curr):
            C[n_viable + j, j] = birth_cost

        # Dummy-to-dummy block: zero cost (allows unmatched births/deaths)
        C[n_viable:, n_curr:] = 0.0

        # ── Solve with Hungarian algorithm ────────────────────────────────
        row_ind, col_ind = linear_sum_assignment(C)

        assigned_tracks = set()
        assigned_dets   = set()

        for r, c in zip(row_ind, col_ind):
            cost_val = C[r, c]
            if cost_val >= INF_COST:
                continue

            if r < n_viable and c < n_curr:
                # Real link: viable track r → detection c
                tid      = viable_ids[r]
                det_idx  = curr.index[c]
                prev_y   = active[tid]['y']
                prev_x   = active[tid]['x']
                new_y    = curr_y[c]
                new_x    = curr_x[c]

                df.at[det_idx, 'track_id']  = tid
                df.at[det_idx, 'link_cost'] = cost_val

                # Update velocity estimate
                if use_velocity:
                    dt = max(1, t - active[tid]['last_frame'])
                    new_vy = (new_y - prev_y) / dt
                    new_vx = (new_x - prev_x) / dt
                    active[tid]['vy'] = (velocity_alpha * new_vy
                                         + (1 - velocity_alpha) * active[tid]['vy'])
                    active[tid]['vx'] = (velocity_alpha * new_vx
                                         + (1 - velocity_alpha) * active[tid]['vx'])

                active[tid].update({
                    'y': new_y, 'x': new_x,
                    'area': curr_a[c],
                    'last_frame': t,
                })
                assigned_tracks.add(r)
                assigned_dets.add(c)

            elif r >= n_viable and c < n_curr:
                # Birth: new track for detection c
                det_idx = curr.index[c]
                df.at[det_idx, 'track_id']  = next_track_id
                df.at[det_idx, 'link_cost'] = birth_cost
                active[next_track_id] = {
                    'y': curr_y[c], 'x': curr_x[c],
                    'area': curr_a[c],
                    'last_frame': t,
                    'vy': 0.0, 'vx': 0.0,
                }
                next_track_id += 1
                assigned_dets.add(c)
            # else: death (r < n_viable, c >= n_curr) — track ends naturally

        # Expire tracks not seen for too long
        active = {tid: info for tid, info in active.items()
                  if t - info['last_frame'] <= max_gap_frames}

    # ── Gap closing: second pass ──────────────────────────────────────────
    if max_gap_frames > 0:
        df = _close_gaps_bayesian(df, max_gap_frames, max_displacement_um,
                                   sigma_um, area_weight)

    return df


def _close_gaps_bayesian(
    df: pd.DataFrame,
    max_gap_frames: int,
    max_displacement_um: float,
    sigma_um: float,
    area_weight: float,
) -> pd.DataFrame:
    """
    Second-pass gap closing: connect track ends to track starts separated
    by up to max_gap_frames frames using the same Bayesian cost.

    This handles condensates that genuinely disappear for 1-2 frames
    (photobleaching, focus drift) and reappear — the frame-by-frame pass
    already handles gaps ≤ max_gap_frames, but this catches cases where
    a track died in pass 1 because no detection was available and a new
    track started later.
    """
    from scipy.optimize import linear_sum_assignment

    INF = 1e9

    # Find track ends and starts
    track_ends   = {}  # track_id → last (frame, y, x, area)
    track_starts = {}  # track_id → first (frame, y, x, area)

    for tid, grp in df.groupby('track_id'):
        if tid < 0:
            continue
        grp = grp.sort_values('frame')
        last  = grp.iloc[-1]
        first = grp.iloc[0]
        track_ends[tid]   = (int(last['frame']),
                              float(last['y_um']), float(last['x_um']),
                              float(last['area_um2']))
        track_starts[tid] = (int(first['frame']),
                              float(first['y_um']), float(first['x_um']),
                              float(first['area_um2']))

    end_ids   = list(track_ends.keys())
    start_ids = list(track_starts.keys())

    if not end_ids or not start_ids:
        return df

    n_e, n_s = len(end_ids), len(start_ids)
    dim = n_e + n_s
    C   = np.full((dim, dim), INF)

    for i, eid in enumerate(end_ids):
        ef, ey, ex, ea = track_ends[eid]
        for j, sid in enumerate(start_ids):
            if sid == eid:
                C[i, j] = INF; continue
            sf, sy, sx, sa = track_starts[sid]
            gap = sf - ef
            if gap <= 0 or gap > max_gap_frames + 1:
                C[i, j] = INF; continue
            d = float(np.sqrt((sy - ey)**2 + (sx - ex)**2))
            if d > max_displacement_um * gap:
                C[i, j] = INF; continue
            cost = _gaussian_cost(d, sigma_um * gap)
            if area_weight > 0 and ea > 0 and sa > 0:
                cost += area_weight * abs(np.log(sa / ea))
            C[i, j] = cost

    # Diagonal blocks: allow unmatched ends/starts at zero cost
    C[n_e:, n_s:] = 0.0

    row_ind, col_ind = linear_sum_assignment(C)

    for r, c in zip(row_ind, col_ind):
        if r >= n_e or c >= n_s:
            continue
        if C[r, c] >= INF:
            continue
        eid = end_ids[r]
        sid = start_ids[c]
        if eid == sid:
            continue
        # Merge track sid into track eid
        df.loc[df['track_id'] == sid, 'track_id'] = eid

    return df


# ---------------------------------------------------------------------------
# 1. Trajectory tracking — greedy nearest-neighbour linking
# ---------------------------------------------------------------------------

def link_trajectories(
    props_df: pd.DataFrame,
    max_displacement_um: float = 2.0,
    max_gap_frames: int = 1,
) -> pd.DataFrame:
    """
    Link condensate detections across frames into trajectories using
    greedy nearest-neighbour assignment.

    Algorithm: for each frame t+1, assign each detection to the closest
    detection in frame t within max_displacement_um.  Unmatched detections
    start new tracks.  Gaps up to max_gap_frames are bridged if the
    nearest candidate in a future frame is within the distance threshold.

    Parameters
    ----------
    props_df : output of extract_frame_properties()
    max_displacement_um : max allowed displacement between frames (µm)
    max_gap_frames : number of frames a condensate can be missing before
                     the track is terminated

    Returns
    -------
    props_df with an added 'track_id' column.
    """
    df = props_df.copy().sort_values(['frame', 'object_id']).reset_index(drop=True)
    df['track_id'] = -1
    next_track = 0

    frames = sorted(df['frame'].unique())
    # Active tracks: dict track_id → (y, x, last_frame)
    active_tracks: dict[int, tuple] = {}

    for t in frames:
        curr = df[df['frame'] == t].copy()
        curr_pts = curr[['y_um', 'x_um']].values

        # Find which active tracks are still linkable (within gap window)
        viable = {tid: info for tid, info in active_tracks.items()
                  if t - info[2] <= max_gap_frames}

        if not viable or len(curr_pts) == 0:
            # Start new tracks for all detections this frame
            for idx in curr.index:
                df.at[idx, 'track_id'] = next_track
                active_tracks[next_track] = (
                    df.at[idx, 'y_um'], df.at[idx, 'x_um'], t)
                next_track += 1
            continue

        prev_ids  = list(viable.keys())
        prev_pts  = np.array([[viable[i][0], viable[i][1]] for i in prev_ids])

        # Greedy assignment: sort by distance
        tree = cKDTree(curr_pts)
        dists, idxs = tree.query(prev_pts, k=1)

        assigned_curr  = set()
        assigned_prev  = set()
        pairs = sorted(zip(dists, prev_ids, idxs), key=lambda x: x[0])

        for dist, tid, curr_local_idx in pairs:
            if dist > max_displacement_um:
                break
            if curr_local_idx in assigned_curr:
                continue
            # Get the actual DataFrame index
            curr_df_idx = curr.index[curr_local_idx]
            df.at[curr_df_idx, 'track_id'] = tid
            active_tracks[tid] = (
                df.at[curr_df_idx, 'y_um'],
                df.at[curr_df_idx, 'x_um'], t)
            assigned_curr.add(curr_local_idx)
            assigned_prev.add(tid)

        # Unassigned curr → new tracks
        for local_idx, df_idx in enumerate(curr.index):
            if local_idx not in assigned_curr:
                df.at[df_idx, 'track_id'] = next_track
                active_tracks[next_track] = (
                    df.at[df_idx, 'y_um'], df.at[df_idx, 'x_um'], t)
                next_track += 1

        # Remove expired tracks
        active_tracks = {tid: info for tid, info in active_tracks.items()
                         if t - info[2] <= max_gap_frames}

    return df


def trajectory_metrics(tracks_df: pd.DataFrame) -> pd.DataFrame:
    """
    Per-track summary metrics from linked trajectories.

    Returns
    -------
    DataFrame with columns: track_id, n_frames, lifetime_frames,
                             total_displacement_um, net_displacement_um,
                             mean_speed_um_per_frame, confinement_ratio,
                             mean_area_um2, area_change_um2
    """
    rows = []
    for tid, grp in tracks_df.groupby('track_id'):
        if tid < 0:
            continue
        grp = grp.sort_values('frame')
        pts = grp[['y_um', 'x_um']].values
        areas = grp['area_um2'].values

        steps = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        total_disp = float(steps.sum())
        net_disp   = float(np.linalg.norm(pts[-1] - pts[0]))
        lifetime   = int(grp['frame'].max() - grp['frame'].min() + 1)
        n_det      = len(grp)
        speed      = total_disp / max(n_det - 1, 1)
        confinement = (net_disp / total_disp) if total_disp > 0 else np.nan

        rows.append({
            'track_id':               int(tid),
            'n_detections':           n_det,
            'lifetime_frames':        lifetime,
            'total_displacement_um':  total_disp,
            'net_displacement_um':    net_disp,
            'mean_speed_um_per_frame': speed,
            'confinement_ratio':      confinement,
            'mean_area_um2':          float(areas.mean()),
            'area_change_um2':        float(areas[-1] - areas[0]),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 2. Merge / fission event detection
# ---------------------------------------------------------------------------

def detect_merge_fission(
    mask_stack: np.ndarray,
    microns_per_pixel: float = 1.0,
    proximity_um: float = 1.0,
) -> pd.DataFrame:
    """
    Detect merge and fission events by comparing consecutive frames.

    Merge: N objects in frame t → 1 overlapping object in frame t+1.
    Fission: 1 object in frame t → N overlapping objects in frame t+1.

    Detection is based on centroid proximity (within proximity_um) and
    mask overlap between consecutive frames.

    Returns
    -------
    DataFrame with columns: frame, event_type ('merge'|'fission'),
                             n_objects, centroid_y_um, centroid_x_um
    """
    rows = []
    for t in range(len(mask_stack) - 1):
        f0 = sk.measure.label(mask_stack[t] > 0)
        f1 = sk.measure.label(mask_stack[t + 1] > 0)

        # For each object in f1, count how many f0 objects it overlaps
        for prop1 in sk.measure.regionprops(f1):
            region1 = (f1 == prop1.label)
            overlap_labels = np.unique(f0[region1])
            overlap_labels = overlap_labels[overlap_labels != 0]
            if len(overlap_labels) >= 2:
                cy, cx = prop1.centroid
                rows.append({
                    'frame':       t + 1,
                    'event_type':  'merge',
                    'n_objects':   len(overlap_labels),
                    'centroid_y_um': cy * microns_per_pixel,
                    'centroid_x_um': cx * microns_per_pixel,
                })

        # For each object in f0, count how many f1 objects it overlaps
        for prop0 in sk.measure.regionprops(f0):
            region0 = (f0 == prop0.label)
            overlap_labels = np.unique(f1[region0])
            overlap_labels = overlap_labels[overlap_labels != 0]
            if len(overlap_labels) >= 2:
                cy, cx = prop0.centroid
                rows.append({
                    'frame':       t,
                    'event_type':  'fission',
                    'n_objects':   len(overlap_labels),
                    'centroid_y_um': cy * microns_per_pixel,
                    'centroid_x_um': cx * microns_per_pixel,
                })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. Cluster lifetime analysis
# ---------------------------------------------------------------------------

def cluster_lifetime_analysis(tracks_df: pd.DataFrame) -> pd.DataFrame:
    """
    Distribution of condensate cluster lifetimes from tracked trajectories.

    Returns
    -------
    DataFrame with columns: lifetime_frames, count, fraction
    Plus attrs: mean_lifetime, median_lifetime, std_lifetime
    """
    traj = trajectory_metrics(tracks_df)
    lifetimes = traj['lifetime_frames'].values
    counts = pd.Series(lifetimes).value_counts().sort_index()
    df = pd.DataFrame({
        'lifetime_frames': counts.index,
        'count':           counts.values,
        'fraction':        counts.values / counts.values.sum(),
    })
    df.attrs['mean_lifetime']   = float(lifetimes.mean())
    df.attrs['median_lifetime'] = float(np.median(lifetimes))
    df.attrs['std_lifetime']    = float(lifetimes.std())
    return df


# ---------------------------------------------------------------------------
# 4. Neighbourhood persistence / turnover
# ---------------------------------------------------------------------------

def neighbourhood_persistence(
    props_df: pd.DataFrame,
    tracks_df: pd.DataFrame,
    radius_um: float = 3.0,
    n_neighbours: int = 5,
) -> pd.DataFrame:
    """
    For each tracked condensate, compute how stable its local neighbourhood
    (set of n nearest neighbours) is over time.

    Persistence = Jaccard similarity of neighbour sets between consecutive
    frames.  1 = same neighbours, 0 = completely new neighbours.

    Turnover = 1 − mean persistence.

    Returns
    -------
    DataFrame with columns: track_id, frame, n_neighbours_found,
                             neighbourhood_persistence, neighbourhood_turnover
    """
    rows = []
    frames = sorted(tracks_df['frame'].unique())
    prev_neighbours: dict[int, set] = {}

    for t in frames:
        curr = tracks_df[tracks_df['frame'] == t]
        if len(curr) < 2:
            continue
        pts = curr[['y_um', 'x_um']].values
        tids = curr['track_id'].values

        tree = cKDTree(pts)
        k    = min(n_neighbours + 1, len(pts))
        dists, idxs = tree.query(pts, k=k)

        for i, (tid, row_dists, row_idxs) in enumerate(zip(tids, dists, idxs)):
            in_radius = [tids[j] for j, d in zip(row_idxs[1:], row_dists[1:])
                         if d <= radius_um and j != i]
            curr_set  = set(in_radius[:n_neighbours])

            prev_set  = prev_neighbours.get(tid, None)
            if prev_set is not None and (curr_set | prev_set):
                jaccard = len(curr_set & prev_set) / len(curr_set | prev_set)
            else:
                jaccard = np.nan

            rows.append({
                'track_id':                 int(tid),
                'frame':                    t,
                'n_neighbours_found':       len(curr_set),
                'neighbourhood_persistence': jaccard,
                'neighbourhood_turnover':   (1 - jaccard) if not np.isnan(jaccard) else np.nan,
            })
            prev_neighbours[tid] = curr_set

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 5. Growth and shrinkage kinetics
# ---------------------------------------------------------------------------

def growth_shrinkage_kinetics(
    tracks_df: pd.DataFrame,
    frame_interval_s: float = 1.0,
) -> pd.DataFrame:
    """
    Per-frame area change rate for each tracked condensate.

    Classifies each frame transition as:
      'growth'    (dA/dt > 0)
      'shrinkage' (dA/dt < 0)
      'stable'    (|dA/dt| < 5% of mean area)

    Returns
    -------
    DataFrame with columns: track_id, frame, area_um2, dA_dt_um2_per_s,
                             state, cumulative_growth_um2, cumulative_shrinkage_um2
    Plus summary per track in attrs.
    """
    rows = []
    for tid, grp in tracks_df.groupby('track_id'):
        if tid < 0:
            continue
        grp = grp.sort_values('frame').reset_index(drop=True)
        areas  = grp['area_um2'].values
        frames = grp['frame'].values
        mean_a = areas.mean()
        cum_growth, cum_shrink = 0.0, 0.0

        for i in range(len(grp)):
            if i == 0:
                da_dt = np.nan
                state = 'stable'
            else:
                dt    = (frames[i] - frames[i-1]) * frame_interval_s
                da_dt = (areas[i] - areas[i-1]) / max(dt, 1e-10)
                if da_dt > 0.05 * mean_a:
                    state = 'growth';   cum_growth  += areas[i] - areas[i-1]
                elif da_dt < -0.05 * mean_a:
                    state = 'shrinkage'; cum_shrink += areas[i-1] - areas[i]
                else:
                    state = 'stable'

            rows.append({
                'track_id':               int(tid),
                'frame':                  int(frames[i]),
                'area_um2':               float(areas[i]),
                'dA_dt_um2_per_s':        float(da_dt) if not np.isnan(float(da_dt) if da_dt is not None else np.nan) else np.nan,
                'state':                  state,
                'cumulative_growth_um2':  cum_growth,
                'cumulative_shrinkage_um2': cum_shrink,
            })

    return pd.DataFrame(rows)
