"""The MSD spaghetti plot draws a fidelity-targeted representative SAMPLE of tracks by default, not
every line — because the plot conveys the SPREAD (percentile band) of MSD curves, and that band
converges at a roughly constant number of tracks regardless of dataset size. `representative_track_sample`
picks the smallest sample reproducing the full band to a target fidelity; the full data is untouched
(the ensemble mean and fit still use every track).
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.core

from pycat.toolbox.analysis_plots import representative_track_sample


def _make_long(n_tracks, n_lags=25, seed=1):
    rng = np.random.default_rng(seed)
    lags = np.logspace(-1, 1, n_lags)
    rows = []
    for tid in range(n_tracks):
        D = 10 ** rng.normal(-1, 0.5)
        a = np.clip(rng.normal(0.9, 0.15), 0.3, 1.4)
        msd = 4 * D * lags ** a * np.exp(rng.normal(0, 0.15, n_lags))
        for l, m in zip(lags, msd):
            rows.append((tid, float(l), float(m)))
    return pd.DataFrame(rows, columns=['track_id', 'lag_s', 'msd_um2'])


def test_sample_is_smaller_than_full_and_meets_fidelity():
    df = _make_long(4000)
    ids, ntot, fid = representative_track_sample(df, target_fidelity=0.95)
    assert ntot == 4000
    assert len(ids) < ntot            # a real reduction
    assert fid >= 0.90                # close to target (tolerance for randomness)


def test_sample_size_roughly_constant_across_dataset_size():
    # The key property: #tracks needed for a given fidelity does NOT scale with N.
    _, _, f_small = representative_track_sample(_make_long(800), 0.95)
    ids_big, _, f_big = representative_track_sample(_make_long(8000), 0.95)
    assert f_big >= 0.90
    # the big set should not need ~10x more tracks than the small one
    assert len(ids_big) <= 800


def test_small_set_returns_all():
    df = _make_long(30)
    ids, ntot, fid = representative_track_sample(df, target_fidelity=0.95)
    assert ntot == 30
    assert len(ids) == 30             # below the smallest candidate → draw all
    assert fid == 1.0


def test_empty_is_safe():
    empty = pd.DataFrame(columns=['track_id', 'lag_s', 'msd_um2'])
    ids, ntot, fid = representative_track_sample(empty)
    assert ids == [] and ntot == 0 and fid == 1.0
