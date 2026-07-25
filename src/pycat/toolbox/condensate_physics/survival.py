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

    Censoring:
      - Right-censored: a condensate still present at the last frame -- its death is not observed.
      - Left-censored: a condensate present at frame 0 -- its true birth (and thus true lifetime) is
        unknown. A full left-censored KM needs a reversed-time estimator; the honest, low-risk choice
        here is to treat frame-0 tracks as **censored**: they contribute to the risk set but are never
        counted as an observed death, because their observed duration is only a *minimum* lifetime.
      - Only condensates with both birth and death observed inside the movie are uncensored (events).

    Because the data are censored, the mean lifetime is reported as the **restricted mean survival time
    (RMST)** -- the area under the KM curve -- not the naive mean of durations, which averages
    right-censored (incomplete) durations as if the condensate died at the last observed frame and so
    biases the mean low. ``attrs['mean_lifetime_is_rmst']`` flags this for the UI.

    Parameters
    ----------
    tracks_df : linked trajectories DataFrame (track_id, frame columns)
    total_frames : total number of frames in the movie

    Returns
    -------
    DataFrame with columns: time_frames, survival_probability,
                             n_at_risk, n_events, n_censored
    Plus attrs: median_lifetime_frames, mean_lifetime_frames (RMST), mean_lifetime_is_rmst,
                n_left_censored, n_right_censored
    """
    lifetimes = []   # (duration, right_censored, left_censored)
    for tid, grp in tracks_df.groupby('track_id'):
        if tid < 0:
            continue
        grp = grp.sort_values('frame')
        t_start = int(grp['frame'].min())
        t_end   = int(grp['frame'].max())
        duration = t_end - t_start + 1
        alive_at_end     = (t_end >= total_frames - 1)   # right-censored death
        born_before_start = (t_start <= 0)               # left-censored birth
        right_censored = alive_at_end
        left_censored  = born_before_start and not alive_at_end
        lifetimes.append((duration, right_censored, left_censored))

    if not lifetimes:
        return pd.DataFrame()

    # KM estimator. A track is censored (never an observed death) if EITHER its death is unobserved
    # (right) or its birth is unobserved (left) -- see docstring.
    lifetimes.sort(key=lambda x: x[0])
    durations   = np.array([l[0] for l in lifetimes])
    right_cens  = np.array([l[1] for l in lifetimes])
    left_cens   = np.array([l[2] for l in lifetimes])
    is_censored = right_cens | left_cens

    unique_times = np.unique(durations[~is_censored])
    n_total      = len(lifetimes)

    S     = 1.0   # survival probability
    rows  = [{'time_frames': 0, 'survival_probability': 1.0,
               'n_at_risk': n_total, 'n_events': 0, 'n_censored': 0}]

    # n_at_risk at each event time is the number of tracks whose duration reaches it: sum(durations >= t).
    # The previous decrement (n_at_risk -= n_events + n_censored) only subtracted censored tracks whose
    # duration exactly equalled an event time, so a censored track that dropped out BETWEEN event times
    # was never removed -- overcounting the risk set and biasing survival high. sum(durations >= t) is
    # the canonical form and handles all censoring correctly.
    prev_t = 0
    for t in unique_times:
        n_at_risk  = int(np.sum(durations >= t))
        n_events   = int(np.sum((durations == t) & ~is_censored))
        # censored tracks that left the risk set in this interval (prev event, t] -- reporting only
        n_censored = int(np.sum(is_censored & (durations > prev_t) & (durations <= t)))
        if n_at_risk > 0 and n_events > 0:
            S *= (1 - n_events / n_at_risk)
        rows.append({'time_frames': int(t), 'survival_probability': S,
                      'n_at_risk': n_at_risk, 'n_events': n_events,
                      'n_censored': n_censored})
        prev_t = t

    df = pd.DataFrame(rows)

    # Median: time at which S drops below 0.5
    below = df[df['survival_probability'] <= 0.5]
    median_lt = float(below['time_frames'].iloc[0]) if len(below) else np.nan
    df.attrs['median_lifetime_frames'] = median_lt

    # Censored-aware mean via RMST (restricted mean survival time = area under the KM curve). This is
    # the statistically correct summary when the data are censored; durations.mean() would count
    # right-censored durations as complete deaths and bias the mean low.
    t = df['time_frames'].to_numpy(dtype=float)
    S = df['survival_probability'].to_numpy(dtype=float)
    df.attrs['mean_lifetime_frames'] = float(np.trapezoid(S, t))   # area under the KM curve
    df.attrs['mean_lifetime_is_rmst'] = True

    # Anti-black-box: surface how much of the population was censored (why the mean is an RMST).
    df.attrs['n_left_censored']  = int(left_cens.sum())
    df.attrs['n_right_censored'] = int(right_cens.sum())
    return df
