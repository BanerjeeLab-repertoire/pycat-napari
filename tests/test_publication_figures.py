"""**publication_figures headline DoD — a REAL comparative figure refines + exports without recomputing.**

The `figure_spec` DoD contract tests (JSON round-trip, ontology defaults, caveats footnote, vector text
fonts, size presets, refine-not-recompute) already pass against synthetic `FigureData`. This closes the
spec's headline against the ACTUAL comparative figure the spec is about: a `condition_comparison_figure`
can be refined (labels/scale) and exported (vector + summary + spec JSON) with its plotted data untouched —
the whole point being that presentation work never re-runs the analysis. Matplotlib Agg, `core`, no Qt.
"""
import json

import matplotlib
matplotlib.use('Agg', force=False)
import numpy as np
import pandas as pd
import pytest

from pycat.utils.comparative_figures import condition_comparison_figure
from pycat.utils.figure_spec import FigureSpec, refine, export

pytestmark = pytest.mark.base


def _long_df():
    return pd.DataFrame({
        'condition': ['WT'] * 6 + ['KO'] * 6,
        'replicate': (['r1', 'r1', 'r2', 'r2', 'r3', 'r3']) * 2,
        'value': [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
    })


def _plotted_snapshot(fig):
    """Every plotted datum on the figure — collection offsets + line ydata — so 'nothing recomputed' is
    checkable byte-for-byte."""
    snap = []
    for ax in fig.axes:
        for c in ax.collections:
            off = c.get_offsets()
            snap.append(np.asarray(off).copy())
        for ln in ax.get_lines():
            snap.append(np.asarray(ln.get_ydata(), dtype=float).copy())
    return snap


def test_a_comparative_figure_REFINES_without_recomputing_its_data():
    fig = condition_comparison_figure(_long_df(), 'partition_coefficient',
                                      condition_col='condition', replicate_col='replicate')
    before = _plotted_snapshot(fig)
    refine(fig, FigureSpec(title='Refined', y_scale='log'))
    # presentation changed …
    assert any(ax.get_yscale() == 'log' for ax in fig.axes)
    assert any('Refined' == ax.get_title(loc='left') for ax in fig.axes)
    # … but not one plotted value moved (refine never recomputes)
    after = _plotted_snapshot(fig)
    assert len(before) == len(after)
    for a, b in zip(before, after):
        assert np.array_equal(a, b), "refine changed the plotted data — it must only change presentation"


def test_a_comparative_figure_EXPORTS_a_reproducible_bundle(tmp_path):
    fig = condition_comparison_figure(_long_df(), 'partition_coefficient',
                                      condition_col='condition', replicate_col='replicate')
    summary = pd.DataFrame({'condition': ['WT', 'KO'], 'mean': [3.5, 4.5]})
    out = export(fig, tmp_path / 'comp.png', spec=FigureSpec(title='Comparison'), summary_df=summary)
    for k in ('pdf', 'svg', 'png', 'spec', 'summary'):
        assert out[k].exists(), f"export must write the {k}"
    # vector fonts stay editable TEXT (fonttype 42/none), not outlines
    assert matplotlib.rcParams['pdf.fonttype'] == 42 and matplotlib.rcParams['svg.fonttype'] == 'none'
    # the spec JSON regenerates a spec, and the summary numbers are saved beside the figure
    doc = json.loads(out['spec'].read_text(encoding='utf-8'))
    assert doc['title'] == 'Comparison'
    assert pd.read_csv(out['summary'])['mean'].tolist() == [3.5, 4.5]
