"""VPT ensemble **drift correction** — split out of vpt_tools (1.6.236).

drift_correct_com subtracts the common-mode (center-of-mass) motion of all tracks so stage/sample drift is
not read as bead diffusion; reclassify_by_temporal_stability flags tracks whose stability changes over time.
Moved VERBATIM - no number changed; pinned by the drift tests. The tools module re-exports both.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pycat.utils.tag_registry import tags_layer


# ---------------------------------------------------------------------------
# 5. Ensemble center-of-mass drift correction
# ---------------------------------------------------------------------------

def reclassify_by_temporal_stability(tracks_df, min_stable_len=5,
                                     max_gap_frac=0.25):
    """Promote STABLE dim tracks back to singlet after linking.

    Per-frame classification sends dim detections to out_of_plane (yellow),
    because a dim spot is usually a bead drifting out of the focal plane. But a
    dim spot that is actually a real in-focus bead will appear in (almost) every
    frame of its track — it is stable, not blinking. Once tracks exist we can
    tell the two apart:

      * a dim track that is LONG and has FEW gaps  → a real (if faint) bead →
        promote every detection in it to 'singlet';
      * a dim track that is SHORT or GAPPY (blinks in and out) → genuinely
        out-of-focus → leave as 'out_of_plane' (yellow).

    This is the temporal counterpart to the per-frame classifier: instantaneous
    features cannot distinguish a faint-but-real bead from an out-of-focus one,
    but persistence across frames can. Aggregates and normal singlets are left
    untouched.

    Parameters
    ----------
    tracks_df : linked detections with columns track_id, frame, bead_class.
    min_stable_len : minimum number of frames a dim track must span to be
        considered a stable (real) bead.
    max_gap_frac : maximum fraction of missing frames (gaps) within the track's
        span for it to count as stable rather than blinking.

    Returns the DataFrame with 'bead_class' updated in place (copy returned).
    """
    if tracks_df is None or tracks_df.empty or 'bead_class' not in tracks_df:
        return tracks_df
    if 'track_id' not in tracks_df or 'frame' not in tracks_df:
        return tracks_df
    df = tracks_df.copy()
    for tid, grp in df.groupby('track_id'):
        if tid == -1:
            continue
        classes = grp['bead_class']
        # Only consider tracks that are predominantly dim (out_of_plane).
        dim_frac = float((classes == 'out_of_plane').mean())
        if dim_frac < 0.5:
            continue
        frames = grp['frame'].to_numpy()
        n_present = len(frames)
        span = (frames.max() - frames.min() + 1) if n_present else 0
        gap_frac = 1.0 - (n_present / span) if span > 0 else 1.0
        if n_present >= min_stable_len and gap_frac <= max_gap_frac:
            # Stable, persistent dim track → a real bead. Promote its dim
            # detections to singlet (leave any aggregate frames alone).
            promote = grp.index[grp['bead_class'] == 'out_of_plane']
            df.loc[promote, 'bead_class'] = 'singlet'
    # keep the convenience flag consistent
    if 'singlet' in df.columns:
        df['singlet'] = df['bead_class'] == 'singlet'
    return df


@tags_layer('drift_correct', role='overlay', requirements=('time_axis',),
            summary='Common-mode drift correction of tracks')
def drift_correct_com(tracks_df: pd.DataFrame, mode: str = 'com',
                      immobile_fraction: float = 0.25) -> pd.DataFrame:
    """
    Remove global drift/flow from trajectories, with an EXPLICIT choice of how.

    Center-of-mass subtraction is standard for microrheology, but it is not
    free: subtracting the ensemble mean displacement also removes any REAL
    collective motion — internal flow, sedimentation, convection, bulk
    condensate translation. If that collective motion is part of the physics
    being studied, COM correction erases it. The mode is therefore explicit and
    recorded rather than always-on.

    Parameters
    ----------
    tracks_df : DataFrame with columns track_id, frame, y_um, x_um.
    mode : one of
        'none'                — no correction (raw positions; use when collective
                                flow IS the signal, e.g. internal-flow studies).
        'com'                 — subtract the ensemble center-of-mass displacement
                                per frame (the classic microrheology correction;
                                default, preserves prior behaviour). Removes stage
                                drift AND any bulk flow.
        'immobile_reference'  — estimate drift from only the most IMMOBILE tracks
                                (smallest total displacement), so genuinely
                                diffusing/flowing beads don't contribute to the
                                drift estimate. Safer when real motion is present
                                but a stationary sub-population exists (stuck
                                beads, fiducials).
    immobile_fraction : for 'immobile_reference', the fraction of tracks (by
        smallest net displacement) used as the drift reference (default 0.25).

    Returns
    -------
    corrected : same DataFrame with y_um, x_um corrected per the mode, plus the
        original values preserved in y_um_raw, x_um_raw, and a 'drift_mode'
        attribute recorded on the returned frame's .attrs.
    """
    if tracks_df.empty:
        return tracks_df

    df = tracks_df.sort_values(['track_id', 'frame']).copy()
    df['y_um_raw'] = df['y_um']
    df['x_um_raw'] = df['x_um']

    mode = (mode or 'com').lower()
    if mode == 'none':
        df.attrs['drift_mode'] = 'none'
        return df

    frames = np.sort(df['frame'].unique())

    # Choose which tracks define the drift reference.
    if mode == 'immobile_reference':
        # Net displacement per track; keep the most immobile fraction.
        net = {}
        for tid, g in df.groupby('track_id'):
            g = g.sort_values('frame')
            if len(g) < 2:
                continue
            dx = g['x_um'].iloc[-1] - g['x_um'].iloc[0]
            dy = g['y_um'].iloc[-1] - g['y_um'].iloc[0]
            net[tid] = float(np.hypot(dx, dy))
        if net:
            thresh = np.quantile(list(net.values()),
                                 max(0.01, min(1.0, immobile_fraction)))
            ref_ids = {tid for tid, d in net.items() if d <= thresh}
        else:
            ref_ids = set(df['track_id'].unique())
    else:  # 'com'
        ref_ids = None  # all tracks

    com_dx = {frames[0]: 0.0}
    com_dy = {frames[0]: 0.0}
    cum_dx, cum_dy = 0.0, 0.0

    for f_prev, f_cur in zip(frames[:-1], frames[1:]):
        prev = df[df['frame'] == f_prev].set_index('track_id')
        cur = df[df['frame'] == f_cur].set_index('track_id')
        common = prev.index.intersection(cur.index)
        if ref_ids is not None:
            common = common.intersection(ref_ids)
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
    df.attrs['drift_mode'] = mode

    return df
