"""**MSD on a movie shorter than the minimum track length: a stated lag window, not a crash.**

The three Dynamics panels compute MSD with the 200-frame default. On a 40-frame movie no track could meet it, the
MSD came back with no columns and the fit raised ``KeyError: 'n_pairs'`` (found by the refactor verification on
the real VPT movie). Now the rule the 200 comes from -- a track spans >= 4x the longest lag -- is applied to the
movie that exists, and the result states its own lag window (frames, interval, duration, tau range, lag count,
and whether that reaches the ~30 lags where the CI on D is honest). A movie long enough for the requested minimum
computes exactly what it did before.
"""
import numpy as np
import pandas as pd
import pytest

from pycat.toolbox.condensate_physics.lag_budget import (
    HONEST_LAG_COUNT, effective_min_track_length, msd_lag_budget)
from pycat.toolbox.condensate_physics.msd import compute_msd, fit_anomalous_diffusion, msd_per_track

pytestmark = pytest.mark.base


def _tracks(n_frames, n_tracks=12, D=0.05, dt=0.05, seed=0):
    rng = np.random.default_rng(seed)
    step = np.sqrt(2 * D * dt)
    rows = []
    for tid in range(n_tracks):
        pos = rng.uniform(0, 20, 2)
        for f in range(n_frames):
            rows.append({'track_id': tid, 'frame': f, 'y_um': pos[0], 'x_um': pos[1]})
            pos = pos + rng.normal(0, step, 2)
    return pd.DataFrame(rows)


def test_a_movie_long_enough_computes_exactly_what_it_did():
    """At or above the requested minimum the adaptation is inert: same tracks, same lags, same numbers."""
    tracks = _tracks(240)
    msd = compute_msd(tracks, frame_interval_s=0.05)            # default minimum, 200 <= 240
    budget = msd.attrs['lag_budget']
    assert not budget['lag_limited'] and budget['effective_min_track_length'] == 200
    explicit = compute_msd(tracks, frame_interval_s=0.05, min_track_length=200)
    pd.testing.assert_frame_equal(msd, explicit, check_exact=True)
    assert budget['n_lags'] == 60 and budget['honest']


def test_a_short_movie_gives_a_lag_limited_msd_and_a_fit_that_says_so():
    tracks = _tracks(40)
    msd = compute_msd(tracks, frame_interval_s=0.05)            # 200 requested, 40 frames exist
    assert len(msd) == 10                                        # 40 // 4 lags
    b = msd.attrs['lag_budget']
    assert b['lag_limited'] and b['effective_min_track_length'] == 40 and not b['honest']
    assert b['duration_s'] == pytest.approx(2.0) and b['tau_max_s'] == pytest.approx(0.5)
    fit = fit_anomalous_diffusion(msd, frame_interval_s=0.05)
    assert np.isfinite(fit['D_um2_per_s'])
    assert fit['lag_limited'] is True and fit['lag_window_n_lags'] == 10
    assert 'longer than the movie' in fit['fit_window_warning'] and str(HONEST_LAG_COUNT) in fit['fit_window_warning']


def test_no_qualifying_track_is_an_explained_result_not_a_KeyError():
    """Long enough movie, but every track fragmented: nothing to fit -- reported, with the columns intact."""
    tracks = _tracks(240)
    tracks = tracks[tracks['frame'] % 50 != 0].copy()
    tracks['track_id'] = tracks['track_id'] * 100 + tracks['frame'] // 50      # cut every track into 49-frame pieces
    msd = compute_msd(tracks, frame_interval_s=0.05)
    assert msd.empty and 'n_pairs' in msd.columns
    fit = fit_anomalous_diffusion(msd, frame_interval_s=0.05)
    assert np.isnan(fit['D_um2_per_s']) and 'no track met the minimum' in fit['fit_window_warning']
    per_track = msd_per_track(tracks, 0.05)
    assert per_track.empty and 'D_um2_per_s' in per_track.columns


def test_per_track_msd_follows_the_same_rule_on_a_short_movie():
    per_track = msd_per_track(_tracks(40), 0.05)
    assert len(per_track) == 12 and per_track['D_um2_per_s'].notna().all()


@pytest.mark.parametrize('n_frames, requested, expected', [(40, 200, 40), (41, 200, 40), (12, 200, 12), (11, 200, 200), (7, 200, 200),
                                                           (200, 200, 200), (500, 200, 200), (40, 10, 10)])
def test_the_effective_minimum_is_four_times_the_longest_lag_the_movie_allows(n_frames, requested, expected):
    assert effective_min_track_length(n_frames, requested) == expected


def test_a_movie_too_short_for_any_slope_is_rejected_and_says_why():
    """Under 3 lags there is no slope: tracks are rejected as before, and the statement says the movie is too short."""
    msd = compute_msd(_tracks(10), frame_interval_s=0.05)
    assert msd.empty and msd.attrs['lag_budget']['too_short']
    fit = fit_anomalous_diffusion(msd, frame_interval_s=0.05)
    assert 'too short for an MSD' in fit['fit_window_warning'] and np.isnan(fit['alpha'])


def test_the_statement_names_duration_interval_and_tau_range():
    s = msd_lag_budget(40, 0.05, 200)['statement']
    assert '40 frames at 0.05 s (2 s)' in s and 'tau = 0.05-0.5 s' in s and '10 lag(s)' in s


def test_the_budget_describes_the_lags_actually_computed():
    """A 120-frame movie with a 10-frame minimum computes 30 lags; the statement must say 30, not 10 // 4."""
    msd = compute_msd(_tracks(120), frame_interval_s=0.05, min_track_length=10)
    b = msd.attrs['lag_budget']
    assert b['n_lags'] == len(msd) == 30 and b['tau_max_s'] == pytest.approx(1.5) and b['honest']
    assert fit_anomalous_diffusion(msd, frame_interval_s=0.05)['lag_window_n_lags'] == 30
