"""**The MSD spaghetti background is ONE LineCollection; selection is drawn as overlays.**

Interaction-layer Gap 4: hundreds of individual `Line2D` are slow and force a per-artist restyle on
every selection. The background representative curves collapse into a single `LineCollection`, and a
selection (or a track brushed in from the table — Gap 3) is drawn as an OVERLAY `Line2D` on top. The
background collection is never touched. Hit-testing reads the coordinate ARRAYS, so this costs nothing
for interaction.

These drive the real `plot_msd_trajectories` headlessly (Agg) and check the artist structure + the
promote/demote hooks it registers.
"""

import matplotlib
matplotlib.use("Agg")

import numpy as np
import pandas as pd
import pytest
from matplotlib.collections import LineCollection

pytestmark = pytest.mark.core

_N = 6


def _per_track_df(n_tracks=_N, n_lags=5):
    rows = [{'track_id': tid, 'lag_s': 0.1 * k, 'msd_um2': 0.01 * k * tid}
            for tid in range(1, n_tracks + 1) for k in range(1, n_lags + 1)]
    return pd.DataFrame(rows)


def _draw():
    """max_tracks=2 → only 2 of the 6 tracks are in the background; the rest are promotable."""
    from pycat.toolbox.analysis_plots import plot_msd_trajectories
    reg = {}
    fig = plot_msd_trajectories(_per_track_df(), interactive=False, on_pick_track=lambda t: None,
                                line_registry=reg, max_tracks=2)
    return fig, reg


def _collections(fig):
    return [c for c in fig.axes[0].collections if isinstance(c, LineCollection)]


def _a_missing_tid(reg):
    return next(t for t in range(1, _N + 1) if t not in reg['coords'])


def test_the_background_is_ONE_LineCollection_not_N_lines():
    fig, reg = _draw()
    assert len(_collections(fig)) == 1, "the background should be exactly one LineCollection"
    assert reg['lines'] == {}, "no overlays should exist before any selection"
    assert len(reg['coords']) == 2, "both sampled tracks' geometry should be captured"


def test_selecting_a_track_adds_ONE_overlay_and_leaves_the_collection_UNTOUCHED():
    fig, reg = _draw()
    before = _collections(fig)
    tid = next(iter(reg['coords']))
    ln = reg['promote'](tid)
    assert ln is not None and tid in reg['lines']
    assert _collections(fig) == before, "drawing a selection overlay altered the background collection"


def test_a_NON_SAMPLED_track_promotes_from_the_full_frame():
    fig, reg = _draw()
    tid = _a_missing_tid(reg)
    ln = reg['promote'](tid)
    assert ln is not None
    assert tid in reg['lines'] and tid in reg['coords'], "a non-sampled track was not promoted"


def test_demote_removes_the_overlay_but_NOT_the_collection():
    fig, reg = _draw()
    tid = next(iter(reg['coords']))
    ln = reg['promote'](tid)
    coll = _collections(fig)
    assert reg['demote_line'](ln) is True
    assert tid not in reg['lines']
    assert _collections(fig) == coll, "demote removed the background collection, not just the overlay"


def test_promoting_a_track_with_no_data_returns_None():
    fig, reg = _draw()
    assert reg['promote'](9999) is None


def test_the_log_axes_still_FRAME_the_data():
    """A LineCollection does not autoscale like a Line2D on log axes — check the framing survived."""
    fig, reg = _draw()
    ax = fig.axes[0]
    xy = np.vstack(list(reg['coords'].values()))
    xlo, xhi = ax.get_xlim(); ylo, yhi = ax.get_ylim()
    assert xlo <= xy[:, 0].min() and xhi >= xy[:, 0].max(), "x-axis clipped the track data"
    assert ylo <= xy[:, 1].min() and yhi >= xy[:, 1].max(), "y-axis clipped the track data"


def test_coords_hit_test_is_point_to_SEGMENT_in_display_pixels():
    """The array-based hit-tester (no per-line artist) must give the same point-to-segment distance
    the Line2D version does — an identity transform makes display px == data units here."""
    from matplotlib.transforms import IdentityTransform
    from pycat.toolbox.analysis_plots import _segment_distance_px_coords
    t = IdentityTransform()
    seg = np.array([[0.0, 0.0], [10.0, 0.0]])          # a horizontal segment
    assert _segment_distance_px_coords(seg, t, 5.0, 0.0) == pytest.approx(0.0)   # on the line
    assert _segment_distance_px_coords(seg, t, 5.0, 4.0) == pytest.approx(4.0)   # perpendicular
    assert _segment_distance_px_coords(seg, t, 13.0, 4.0) == pytest.approx(5.0)  # past the end (3,4)
    assert _segment_distance_px_coords(np.empty((0, 2)), t, 1.0, 1.0) == float('inf')
