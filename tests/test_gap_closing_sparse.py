"""Bayesian gap-closing must scale to large fragment counts without allocating an n×n dense cost
matrix, and must still merge the physically-correct fragments.

The regression (2026-07-15): a VPT run produced ~286k fragmented tracks (a detection/linking blowup
on a mis-scaled file). `_close_gaps_bayesian` built `np.full((n_e+n_s, n_e+n_s), INF)` — a
572k×572k float64 matrix = **2.39 TiB** — and the load crashed with a MemoryError. The cost matrix
is ~99.9% INF (only end→start pairs within the gap-frame window AND max displacement are finite), so
it is now built as a SPARSE edge list via a per-gap KD-tree query and solved with sparse bipartite
matching.
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.base

from pycat.toolbox.dynamic_spatial_tools import _close_gaps_bayesian


def _track(tid, frames, y, x):
    return [dict(track_id=tid, frame=f, y_um=y, x_um=x, area_um2=1.0) for f in frames]


def test_near_fragment_merges_far_does_not():
    rows = []
    rows += _track(1, range(0, 6), 10.0, 10.0)      # ends frame 5 at (10,10)
    rows += _track(2, range(7, 12), 10.1, 10.1)     # starts frame 7 nearby (gap 2) → merge
    rows += _track(3, range(7, 12), 500.0, 500.0)   # starts frame 7 far → stay separate
    df = pd.DataFrame(rows)
    out = _close_gaps_bayesian(df.copy(), max_gap_frames=2,
                               max_displacement_um=1.0, sigma_um=0.5, area_weight=0.0)
    near = out[(out['x_um'] > 9) & (out['x_um'] < 11) & (out['frame'] >= 7)]
    far = out[out['x_um'] > 400]
    assert set(near['track_id'].unique()) == {1}      # near fragment merged into track 1
    assert set(far['track_id'].unique()) == {3}       # far fragment untouched


def test_large_fragment_count_does_not_allocate_dense_matrix():
    # 50k one/short fragments, none mergeable → the old code would try a 100k×100k
    # (~80 GB) allocation; the sparse path must complete quickly.
    N = 50000
    rows = [dict(track_id=i, frame=(i % 3), y_um=float(i * 1000), x_um=0.0, area_um2=1.0)
            for i in range(N)]
    df = pd.DataFrame(rows)
    out = _close_gaps_bayesian(df, max_gap_frames=2,
                               max_displacement_um=1.0, sigma_um=0.5, area_weight=0.0)
    # far-apart fragments must not merge
    assert out['track_id'].nunique() == N


def test_empty_and_single():
    empty = pd.DataFrame(columns=['track_id', 'frame', 'y_um', 'x_um', 'area_um2'])
    assert _close_gaps_bayesian(empty, 2, 1.0, 0.5, 0.0) is empty or len(_close_gaps_bayesian(empty, 2, 1.0, 0.5, 0.0)) == 0
    one = pd.DataFrame(_track(1, range(0, 5), 10.0, 10.0))
    out = _close_gaps_bayesian(one, 2, 1.0, 0.5, 0.0)
    assert set(out['track_id'].unique()) == {1}
