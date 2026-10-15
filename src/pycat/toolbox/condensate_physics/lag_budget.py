"""What lag window an acquisition can support, stated from its duration and frame rate.

An MSD is only defined over lags the movie can sample: the shortest is one frame interval (the high-frequency
cutoff), and the longest PyCAT computes is a quarter of the record (``_MAX_LAG_FRACTION``; beyond it too few
displacement pairs remain). A track must therefore span at least 4x the longest lag it contributes to --
the rule ``MIN_TRACK_LENGTH_FRAMES = 200`` is derived from (50 lags, past the ~30 at which the 95% CI on D was
measured honest).

When the MOVIE is shorter than the requested minimum, no track can ever pass it. That used to return an empty
MSD and crash the fit (``KeyError: 'n_pairs'``). The same rule is applied to the record that exists instead:
tracks must span 4x the longest lag the movie allows (i.e. effectively the whole movie), the MSD is computed
over that shorter window, and a warning states the physics -- duration, frame interval, the tau range and the
number of lags, and whether that is below the honest regime. Nothing changes for a movie at least as long as
the requested minimum.
"""
from __future__ import annotations

from pycat.utils.notify import show_warning

MAX_LAG_FRACTION = 4       # compute_msd's max_lag = n_frames // 4
HONEST_LAG_COUNT = 30      # lags at which the 95% CI on D was measured honest (4 lags: claims 95%, delivers 78%)
MIN_FIT_LAGS = 3           # fewer lags than this and there is no slope to fit (fit_anomalous_diffusion's own floor)


def effective_min_track_length(n_frames: int, min_track_length: int) -> int:
    """The requested minimum, or -- for a movie shorter than it -- 4x the longest lag the movie allows. A movie
    too short to give even ``MIN_FIT_LAGS`` lags is not adapted: there is no slope to fit, so its tracks are
    rejected (and reported) as before."""
    n_frames, min_track_length = int(n_frames), int(min_track_length)
    if n_frames >= min_track_length or n_frames // MAX_LAG_FRACTION < MIN_FIT_LAGS:
        return min_track_length
    return MAX_LAG_FRACTION * (n_frames // MAX_LAG_FRACTION)


def msd_lag_budget(n_frames: int, frame_interval_s, min_track_length: int, max_lag: int | None = None) -> dict:
    """The lag window this acquisition supports, with a plain statement of what it means for D and alpha.
    ``max_lag`` is the longest lag actually computed (default: a quarter of the qualifying track length)."""
    dt = float(frame_interval_s) if frame_interval_s and frame_interval_s > 0 else float('nan')
    eff = effective_min_track_length(n_frames, min_track_length)
    n_lags = max(1, min(int(max_lag), eff - 1) if max_lag else eff // MAX_LAG_FRACTION)
    too_short = n_frames < int(min_track_length) and n_frames // MAX_LAG_FRACTION < MIN_FIT_LAGS
    if too_short:
        n_lags = max(0, n_frames // MAX_LAG_FRACTION)
    budget = dict(n_frames=int(n_frames), frame_interval_s=dt, duration_s=n_frames * dt, too_short=too_short,
                  requested_min_track_length=int(min_track_length), effective_min_track_length=eff,
                  n_lags=n_lags, tau_min_s=dt, tau_max_s=n_lags * dt,
                  lag_limited=eff < int(min_track_length), honest=n_lags >= HONEST_LAG_COUNT)
    budget['statement'] = _statement(budget)
    return budget


def observed(budget: dict, msd_df) -> dict:
    """The budget restated from the MSD actually computed: its lag count and tau range, not the a-priori estimate.
    (The a-priori numbers only decide the effective minimum track length.)"""
    out = dict(budget)
    if msd_df is not None and len(msd_df) and 'lag_s' in getattr(msd_df, 'columns', ()):
        lags = msd_df['lag_s'].astype(float)
        out.update(n_lags=int(len(msd_df)), tau_min_s=float(lags.min()), tau_max_s=float(lags.max()))
        out['honest'] = out['n_lags'] >= HONEST_LAG_COUNT
    out['statement'] = _statement(out)
    return out


def _fmt(x):
    return 'unknown' if x != x else f'{x:.3g}'


def _statement(b) -> str:
    window = (f"{b['n_frames']} frames at {_fmt(b['frame_interval_s'])} s ({_fmt(b['duration_s'])} s) supports "
              f"{b['n_lags']} lag(s), tau = {_fmt(b['tau_min_s'])}-{_fmt(b['tau_max_s'])} s")
    parts = []
    if b['too_short']:
        return (f"The movie is too short for an MSD: {window}, and a slope needs >= {MIN_FIT_LAGS} lags (a track of "
                f">= {MIN_FIT_LAGS * MAX_LAG_FRACTION} frames). Its tracks are rejected; acquire >= "
                f"{HONEST_LAG_COUNT * MAX_LAG_FRACTION} frames for an honest D.")
    if b['lag_limited']:
        parts.append(f"The minimum track length ({b['requested_min_track_length']} frames) is longer than the movie, "
                     f"so no track could meet it. MSD uses tracks spanning >= {b['effective_min_track_length']} "
                     f"frames (4x the longest lag) instead: {window}.")
    else:
        parts.append(f"Lag window: {window}.")
    if not b['honest']:
        parts.append(f"That is fewer than the ~{HONEST_LAG_COUNT} lags at which the 95% CI on D was measured honest "
                     f"(at 4 lags it claims 95% and delivers 78%): D and alpha trade off over a short window, and the "
                     f"localisation offset cannot be separated from the slope. Report them as lag-limited, or "
                     f"acquire >= {HONEST_LAG_COUNT * MAX_LAG_FRACTION} frames.")
    return ' '.join(parts)


def warn_if_lag_limited(budget: dict) -> None:
    """Say it once, where the user will see it, when the window is short of the honest regime or was adapted."""
    if budget['lag_limited'] or budget['too_short'] or not budget['honest']:
        show_warning('MSD: ' + budget['statement'])


def fit_fields(budget) -> dict:
    """Scalar columns a fit result carries so a table states its own lag window."""
    if not budget:
        return {}
    return {'lag_window_n_lags': budget['n_lags'], 'lag_window_tau_max_s': budget['tau_max_s'],
            'lag_window_duration_s': budget['duration_s'], 'lag_limited': budget['lag_limited'],
            'lag_window_honest': budget['honest']}


def caveat(budget) -> str | None:
    """The statement to attach to a fit result, or None when the window is in the honest regime."""
    if not budget or (not budget.get('lag_limited') and not budget.get('too_short') and budget.get('honest')):
        return None
    return budget.get('statement')


__all__ = ['effective_min_track_length', 'msd_lag_budget', 'observed', 'warn_if_lag_limited', 'caveat', 'fit_fields',
           'MAX_LAG_FRACTION', 'HONEST_LAG_COUNT']
