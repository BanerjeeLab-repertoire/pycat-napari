"""**A track selected from the table must be showable even when it isn't in the MSD sample.**

`plot_msd_trajectories` draws a fidelity-targeted representative SUBSET (~100 of N), so a track picked
in the table that wasn't sampled has no artist to highlight — the bidirectional brushing quietly
can't reach it. Interaction-layer Gap 3: the plot exposes `promote(tid)` to draw that track's curve on
demand, and `demote_line` to remove it when it's deselected — and it never removes a SAMPLE line, only
a promoted focus curve. Bounded rendering, full brushing.

These drive the real `plot_msd_trajectories` headlessly (Agg) and exercise the promote/demote hooks it
registers.
"""

import matplotlib
matplotlib.use("Agg")

import pandas as pd
import pytest

pytestmark = pytest.mark.core

_N = 6


def _per_track_df(n_tracks=_N, n_lags=5):
    rows = [{'track_id': tid, 'lag_s': 0.1 * k, 'msd_um2': 0.01 * k * tid}
            for tid in range(1, n_tracks + 1) for k in range(1, n_lags + 1)]
    return pd.DataFrame(rows)


def _draw():
    """Draw with max_tracks=2 so only 2 of the 6 tracks are sampled — the rest are promotable."""
    from pycat.toolbox.analysis_plots import plot_msd_trajectories
    reg = {}
    plot_msd_trajectories(_per_track_df(), interactive=False, on_pick_track=lambda t: None,
                          line_registry=reg, max_tracks=2)
    return reg


def _a_missing_tid(reg):
    return next(t for t in range(1, _N + 1) if t not in reg['lines'])


def test_only_the_sample_is_drawn_up_front():
    reg = _draw()
    assert len(reg['lines']) == 2, "max_tracks=2 should draw exactly two sample lines"
    assert reg['promoted'] == set()


def test_a_NON_SAMPLED_track_is_promoted_on_demand():
    reg = _draw()
    tid = _a_missing_tid(reg)
    ln = reg['promote'](tid)
    assert ln is not None
    assert tid in reg['lines'] and tid in reg['promoted'], "promotion did not add the track"


def test_promoting_a_SAMPLED_track_returns_its_existing_line():
    reg = _draw()
    tid = next(iter(reg['lines']))
    before = reg['lines'][tid]
    assert reg['promote'](tid) is before
    assert tid not in reg['promoted'], "a sampled track was wrongly marked as a promoted focus curve"


def test_demote_removes_a_PROMOTED_curve():
    reg = _draw()
    tid = _a_missing_tid(reg)
    line = reg['promote'](tid)
    assert reg['demote_line'](line) is True
    assert tid not in reg['lines'] and tid not in reg['promoted']


def test_demote_NEVER_removes_a_sample_line():
    reg = _draw()
    tid = next(iter(reg['lines']))
    sample_line = reg['lines'][tid]
    assert reg['demote_line'](sample_line) is False
    assert tid in reg['lines'], "a representative-sample line was removed"


def test_promoting_a_track_with_no_data_returns_None():
    reg = _draw()
    assert reg['promote'](9999) is None
