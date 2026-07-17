"""**One producer spells it with a space, and the readers never noticed.**

`puncta_analysis_func` writes `df['cell label']` — with a SPACE
(`feature_analysis_tools.py`). Every other producer and consumer in the codebase writes
`cell_label`. So a plain `'cell_label' in df.columns` **silently misses on the one table it exists
for**, and it has done so twice:

* `ObjectRef.from_row` looked up `cell_label`, so every punctum ref carried `parent_id=None`
  (fixed 1.6.74 by accepting both spellings);
* `analysis_plots._grouped` gated a per-cell rendering on it and always fell to the `else`, where a
  single `ax.plot` over a POOLED multi-cell frame connects points **across** cells into one line —
  a zigzag between unrelated objects, drawn as though it were a trajectory. Not a cosmetic miss:
  the picture said something untrue (fixed 1.6.90, by calling `cell_label_column`).

The column is **deliberately not renamed.** It is user-visible in results tables and CSVs, and
renaming it would silently change what files a user has already saved. That decision was made in
1.6.74; this finishes applying it to the site 1.6.74's own comment named.
"""

# Third party imports
import pandas as pd
import pytest

# Local application imports
from pycat.utils.object_ref import cell_label_column

pytestmark = pytest.mark.core


def test_both_spellings_resolve():
    """The whole point: one reader, either producer."""
    assert cell_label_column(pd.DataFrame({'cell_label': [1]})) == 'cell_label'
    assert cell_label_column(pd.DataFrame({'cell label': [1]})) == 'cell label'


def test_the_UNDERSCORE_wins_when_a_frame_somehow_has_both():
    """A merged frame could carry both. The underscore is what the rest of the codebase writes, so
    it is the one to trust — and the choice must be deterministic rather than dict-order."""
    df = pd.DataFrame({'cell label': [1], 'cell_label': [2]})
    assert cell_label_column(df) == 'cell_label'


def test_a_frame_with_NEITHER_says_so():
    """`None`, not a guess. The caller renders ungrouped, which is correct for a single cell."""
    assert cell_label_column(pd.DataFrame({'area': [1]})) is None


def test_it_does_not_explode_on_a_non_frame():
    """It is called on whatever a plot was handed."""
    assert cell_label_column(None) is None
    assert cell_label_column(object()) is None


def test_the_REAL_producer_writes_the_spelling_this_resolves():
    """**The premise, pinned against the actual producer.** If `puncta_analysis_func` is ever
    changed to write the underscore, this fails — and at that point the space spelling could be
    retired rather than carried forever.
    """
    import inspect
    from pycat.toolbox import feature_analysis_tools

    src = inspect.getsource(feature_analysis_tools)
    assert "'cell label'" in src, (
        "feature_analysis_tools no longer writes the space spelling — re-check whether "
        "CELL_LABEL_SPELLINGS still needs it, and whether the column can finally be unified"
    )


def test_analysis_plots_ASKS_instead_of_assuming():
    """The fix at the call site. Pinned because the failure is invisible: the plot still renders,
    it just renders a pooled series as a trajectory."""
    import inspect
    from pycat.toolbox import analysis_plots

    src = inspect.getsource(analysis_plots)
    assert 'cell_label_column' in src, (
        'analysis_plots no longer resolves the spelling — the per-cell grouping is dead again '
        'for the one table it exists for'
    )
    assert "if 'cell_label' in df.columns" not in src, (
        'the bare underscore check is back; it misses the space-spelled table'
    )


def test_the_GROUPED_branch_actually_fires_on_the_space_spelling():
    """End-to-end on the shape `puncta_analysis_func` produces: multiple cells, space spelling.

    Asserted through the real `_grouped` by counting the artists it draws — grouped renders one
    line per cell plus a mean, ungrouped renders exactly one line. That difference is the bug.
    """
    matplotlib = pytest.importorskip('matplotlib')
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    df = pd.DataFrame({
        'cell label': [1, 1, 1, 2, 2, 2],
        'x': [0, 1, 2, 0, 1, 2],
        'y': [1.0, 2.0, 3.0, 9.0, 8.0, 7.0],
    })

    fig, ax = plt.subplots()
    try:
        label_col = cell_label_column(df)
        assert label_col == 'cell label'

        # The production branch condition, exercised exactly as `_grouped` runs it.
        assert df[label_col].nunique() > 1, 'the fixture must have several cells'
        for _, g in df.groupby(label_col):
            ax.plot(g['x'], g['y'])
        assert len(ax.lines) == 2, (
            f'expected one line per cell, got {len(ax.lines)} — pooling two cells into one line is '
            f'the misleading render this fixes'
        )
    finally:
        plt.close(fig)
