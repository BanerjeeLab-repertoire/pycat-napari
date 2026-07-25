"""
**Kaplan-Meier survival of condensate lifetimes: censoring was mishandled two ways.**

`kaplan_meier_lifetimes` claimed to handle left-censoring (docstring) but only ever computed
right-censoring, and reported the mean lifetime as the naive `durations.mean()` -- which averages
right-censored (incomplete) durations as if the condensate died at the last observed frame, biasing
the mean low. A residual risk-set bug also remained: a censored track that dropped out *between*
event times was never removed from `n_at_risk`, overcounting the risk set and biasing survival high.

The fix: (1) `n_at_risk = sum(durations >= t)` (canonical, handles all censoring); (2) frame-0-born
tracks are treated as censored (never an observed death); (3) the mean is the RMST (area under the KM
curve). These tests pin all three.
"""
import numpy as np
import pandas as pd
import pytest


def _km():
    m = pytest.importorskip("pycat.toolbox.condensate_physics.survival")
    return m.kaplan_meier_lifetimes


def _tracks(spans):
    """Build a (track_id, frame) DataFrame from a list of (t_start, t_end) inclusive spans."""
    rows = []
    for i, (a, b) in enumerate(spans):
        for f in range(a, b + 1):
            rows.append({'track_id': i, 'frame': f})
    return pd.DataFrame(rows)


@pytest.mark.core
def test_risk_set_removes_between_event_censoring():
    """n_at_risk must equal sum(durations >= t) at every row, even for censoring between event times."""
    km = _km()
    total_frames = 100
    # durations 5, 10, 20 (uncensored deaths) + a duration-8 track right-censored at the last frame,
    # whose duration (8) falls between event times 5 and 10.
    spans = [(10, 14), (30, 39), (50, 69), (92, 99)]
    durations = np.array([5, 10, 20, 8])
    df = km(_tracks(spans), total_frames)

    na = df['n_at_risk'].to_numpy()
    assert np.all(np.diff(na) <= 0)                      # monotonic non-increasing
    for _, r in df.iterrows():
        assert r['n_at_risk'] == int(np.sum(durations >= r['time_frames']))
    # the between-event censored track has left the risk set by t=10 (old code kept it -> 3)
    row10 = df[df['time_frames'] == 10].iloc[0]
    assert row10['n_at_risk'] == 2


@pytest.mark.core
def test_rmst_mean_exceeds_naive_mean_under_heavy_right_censoring():
    """With heavy right-censoring the RMST mean must exceed the naive durations.mean()."""
    km = _km()
    total_frames = 100
    # Real (uncensored) deaths at LONG durations, so the KM curve extends far and stays high; plus many
    # tracks that start near the movie end and are right-censored with SHORT observed durations. The
    # naive mean counts those short truncated durations as complete deaths and is dragged low; the RMST
    # (area under the curve) correctly keeps survival high -> RMST >> naive.
    spans = [(10, 49), (10, 59), (10, 69)]                          # deaths at durations 40, 50, 60
    spans += [(95, 99)] * 10                                        # right-censored, dur 5 each
    df = km(_tracks(spans), total_frames)
    durations = np.array([40, 50, 60] + [5] * 10)

    assert df.attrs['mean_lifetime_is_rmst'] is True
    assert df.attrs['n_right_censored'] == 10
    assert df.attrs['mean_lifetime_frames'] > durations.mean()      # RMST ~40 vs naive ~15


@pytest.mark.core
def test_frame_zero_tracks_are_censored_not_events():
    """A frame-0-born track is left-censored -> it must not create a survival step-down at its duration."""
    km = _km()
    total_frames = 100
    # one clean death at duration 5, plus a frame-0-born track of duration 40 (left-censored)
    spans = [(20, 24), (0, 39)]
    df = km(_tracks(spans), total_frames)

    assert df.attrs['n_left_censored'] == 1
    # the only event time is 5; there is NO row (step) at duration 40
    assert 40 not in set(df['time_frames'])
    assert set(df[df['n_events'] > 0]['time_frames']) == {5}


@pytest.mark.core
def test_empty_input_returns_empty_frame():
    km = _km()
    out = km(pd.DataFrame({'track_id': [], 'frame': []}), 100)
    assert out.empty
