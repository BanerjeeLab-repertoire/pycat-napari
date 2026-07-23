"""**A seaborn hue split gets a VERIFIED per-artist entity map, or an honest refusal — never a guess.**

`backend_parity` §Part 1. When seaborn draws one artist per hue level, each artist holds a SUBSET of the
table, so an index into an artist is NOT an index into the table — a naive map resolves a click to the
wrong object. `plot_backends._seaborn_subset_mappings` reconstructs which rows each artist holds and
verifies it with the same point-count + coordinate check the single-artist path uses; if any artist can't
be matched to exactly one subset, the whole plot falls back to the refusal. This preserves the exact
scientific-safety property (refuse an unverifiable index map) while removing the refusal where it is
provably safe.

(Note: modern seaborn — 0.13.x here — keeps a hue plot in ONE artist in DataFrame order, so `scatter()`'s
single-artist path already brushes hue correctly and this multi-artist path is a verified fallback for
seaborn versions/plots that DO split. The unit tests drive the mapping logic directly with stand-in
artists; the last test proves a real seaborn hue plot is brushable, not refused.)
"""
import numpy as np
import pandas as pd
import pytest

from pycat.utils.plot_backends import _seaborn_subset_mappings


class _Artist:
    """A stand-in for a seaborn ``PathCollection`` — the mapping logic only ever calls ``get_offsets()``."""

    def __init__(self, xy):
        self._xy = np.asarray(xy, dtype=float)

    def get_offsets(self):
        return self._xy


def _table():
    # hue levels interleaved with uneven counts, so a subset is NOT a contiguous block of rows
    return pd.DataFrame(dict(
        x=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
        y=[10., 20., 30., 40., 50., 60., 70.],
        grp=['A', 'B', 'A', 'C', 'B', 'A', 'C'],
        label=[101, 102, 103, 104, 105, 106, 107]))


def _artist_for(df, level):
    sub = df[df['grp'] == level]
    return _Artist(np.column_stack([sub['x'].values, sub['y'].values]))


@pytest.mark.base
def test_a_verified_hue_split_maps_each_artist_to_its_subset():
    df = _table()
    artists = [_artist_for(df, c) for c in ['A', 'B', 'C']]
    ok, mappings, msg = _seaborn_subset_mappings(artists, df, 'x', 'y', 'grp')

    assert ok and msg == ''
    assert len(mappings) == 3
    # each artist's row_positions are exactly its level's rows, in DataFrame order
    for level, (_art, pos) in zip(['A', 'B', 'C'], mappings):
        assert list(pos) == list(np.where(df['grp'].values == level)[0])
    # and together the artists cover every row exactly once — no row lost, none double-claimed
    covered = sorted(int(i) for _art, pos in mappings for i in pos)
    assert covered == list(range(len(df)))


@pytest.mark.base
def test_a_picked_point_resolves_to_the_correct_entity_within_its_subset():
    df = _table()
    refs = list(df['label'])                       # stand-in ObjectRefs: one per row, in DataFrame order
    artists = [_artist_for(df, c) for c in ['A', 'B', 'C']]
    ok, mappings, _ = _seaborn_subset_mappings(artists, df, 'x', 'y', 'grp')
    assert ok

    _art_b, pos_b = mappings[1]                     # the 'B' artist: rows 1 and 4 -> labels 102, 105
    assert [refs[i] for i in pos_b] == [102, 105]
    # point j=1 of the B artist is the SECOND B row in DataFrame order -> label 105, not a table index
    assert refs[pos_b[1]] == 105


@pytest.mark.base
def test_a_point_count_mismatch_REFUSES_the_whole_plot():
    """The safety property: an artist whose point count matches no free subset refuses everything."""
    df = _table()
    artists = [_artist_for(df, 'A'), _artist_for(df, 'B'), _Artist([[0.0, 0.0]])]  # last: 1 pt, no 1-row subset
    ok, mappings, msg = _seaborn_subset_mappings(artists, df, 'x', 'y', 'grp')
    assert not ok and mappings is None and 'refused' in msg


@pytest.mark.base
def test_a_coordinate_mismatch_REFUSES_the_whole_plot():
    """Right count, wrong coordinates (seaborn reordered within a group) must also refuse — not guess."""
    df = _table()
    bogus = _Artist([[99.0, 99.0], [98.0, 98.0]])   # 2 pts, but matches neither the B nor the C subset
    artists = [_artist_for(df, 'A'), bogus, _artist_for(df, 'C')]
    ok, mappings, msg = _seaborn_subset_mappings(artists, df, 'x', 'y', 'grp')
    assert not ok and 'refused' in msg


@pytest.mark.base
def test_a_split_without_a_hue_column_is_refused():
    df = _table()
    ok, mappings, msg = _seaborn_subset_mappings([_artist_for(df, 'A')], df, 'x', 'y', None)
    assert not ok and mappings is None


@pytest.mark.base
def test_attach_brushing_wires_one_pickable_per_artist_for_a_split(monkeypatch):
    import pycat.utils.brushing as B
    calls = []
    monkeypatch.setattr(B, 'make_pickable',
                        lambda fig, art, refs, **kw: calls.append((art, list(refs))) or fig)

    df = _table()
    refs = list(df['label'])
    art_a, art_b = object(), object()
    out = B.attach_brushing('FIG', [(art_a, np.array([0, 2, 5])), (art_b, np.array([1, 4]))], refs)

    assert out == 'FIG'
    assert calls == [(art_a, [101, 103, 106]), (art_b, [102, 105])]   # each artist gets its own subset's refs


@pytest.mark.base
def test_attach_brushing_wires_a_single_artist_directly(monkeypatch):
    import pycat.utils.brushing as B
    calls = []
    monkeypatch.setattr(B, 'make_pickable',
                        lambda fig, art, refs, **kw: calls.append((art, list(refs))) or fig)

    B.attach_brushing('FIG', 'ARTIST', [11, 22, 33])
    assert calls == [('ARTIST', [11, 22, 33])]


@pytest.mark.base
def test_a_real_seaborn_hue_plot_is_brushable_not_refused():
    """The end-to-end guarantee on the installed seaborn: a hue scatter is brushable, never refused."""
    pytest.importorskip('seaborn')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from pycat.utils.plot_backends import scatter

    df = _table()
    fig, brushable, ok, msg = scatter(df, 'x', 'y', backend='seaborn', hue='grp')
    try:
        assert ok, f"a hue scatter must be brushable, got a refusal: {msg}"
        assert brushable is not None            # a single artist, or a verified list of (artist, positions)
    finally:
        plt.close(fig)
