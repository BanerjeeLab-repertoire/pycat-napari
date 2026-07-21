"""Condensate **survival analysis** — split out of condensate_physics_tools (1.6.219).

kaplan_meier_lifetimes: right-censored Kaplan-Meier survival of condensate lifetimes. Moved VERBATIM - no
number changed. The tools module re-exports it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 7. Survival analysis (Kaplan-Meier) for condensate lifetimes
# ---------------------------------------------------------------------------

def kaplan_meier_lifetimes(
    tracks_df: pd.DataFrame,
    total_frames: int,
) -> pd.DataFrame:
    """
    Kaplan-Meier survival curve for condensate lifetimes.

    Handles censoring:
      - Condensates present at frame 0 are left-censored (unknown birth)
      - Condensates still present at the last frame are right-censored
      - Only condensates with both birth and death observed are uncensored

    Parameters
    ----------
    tracks_df : linked trajectories DataFrame (track_id, frame columns)
    total_frames : total number of frames in the movie

    Returns
    -------
    DataFrame with columns: time_frames, survival_probability,
                             n_at_risk, n_events, n_censored
    Plus attrs: median_lifetime_frames, mean_lifetime_frames
    """
    lifetimes = []   # (duration, censored)
    for tid, grp in tracks_df.groupby('track_id'):
        if tid < 0:
            continue
        grp = grp.sort_values('frame')
        t_start = int(grp['frame'].min())
        t_end   = int(grp['frame'].max())
        duration = t_end - t_start + 1
        # Right-censored: track ends at last frame (may still be alive)
        censored = (t_end >= total_frames - 1)
        lifetimes.append((duration, censored))

    if not lifetimes:
        return pd.DataFrame()

    # KM estimator
    lifetimes.sort(key=lambda x: x[0])
    durations  = np.array([l[0] for l in lifetimes])
    is_censored = np.array([l[1] for l in lifetimes])

    unique_times = np.unique(durations[~is_censored])
    n_total      = len(lifetimes)

    S     = 1.0   # survival probability
    rows  = [{'time_frames': 0, 'survival_probability': 1.0,
               'n_at_risk': n_total, 'n_events': 0, 'n_censored': 0}]
    n_at_risk = n_total

    for t in unique_times:
        n_events   = int(np.sum((durations == t) & ~is_censored))
        n_censored = int(np.sum((durations == t) & is_censored))
        if n_at_risk > 0 and n_events > 0:
            S *= (1 - n_events / n_at_risk)
        rows.append({'time_frames': int(t), 'survival_probability': S,
                      'n_at_risk': n_at_risk, 'n_events': n_events,
                      'n_censored': n_censored})
        n_at_risk -= (n_events + n_censored)

    df = pd.DataFrame(rows)

    # Median: time at which S drops below 0.5
    below = df[df['survival_probability'] <= 0.5]
    median_lt = float(below['time_frames'].iloc[0]) if len(below) else np.nan
    df.attrs['median_lifetime_frames'] = median_lt
    df.attrs['mean_lifetime_frames']   = float(durations.mean())
    return df
