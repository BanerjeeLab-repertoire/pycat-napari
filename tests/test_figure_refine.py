"""**The Explore→Refine→Export engine — refine never recomputes, export is WYSIWYG.**

Qt-free core tests of `FigureRefineController` (the dialog is a thin skin over it): mutating a spec field
restyles the SAME figure without touching a plotted value; a size preset drives the figure to its final
physical size; and export writes the bundle of exactly the refined state (same spec → same output).
"""
import json

import matplotlib
matplotlib.use('Agg', force=False)
import numpy as np
import pandas as pd
import pytest

from pycat.utils.comparative_figures import condition_comparison_figure
from pycat.utils.figure_spec import FigureSpec, SIZE_PRESETS
from pycat.utils.figure_refine import FigureRefineController

pytestmark = pytest.mark.core


def _fig():
    df = pd.DataFrame({
        'condition': ['WT'] * 6 + ['KO'] * 6,
        'replicate': (['r1', 'r1', 'r2', 'r2', 'r3', 'r3']) * 2,
        'value': [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
    })
    return condition_comparison_figure(df, 'partition_coefficient',
                                       condition_col='condition', replicate_col='replicate')


def _plotted(fig):
    snap = []
    for ax in fig.axes:
        for c in ax.collections:
            snap.append(np.asarray(c.get_offsets()).copy())
        for ln in ax.get_lines():
            snap.append(np.asarray(ln.get_ydata(), dtype=float).copy())
    return snap


def test_refining_a_field_restyles_without_recomputing():
    ctl = FigureRefineController(_fig(), FigureSpec())
    before = _plotted(ctl.fig)
    ctl.set(title='Refined', y_scale='log')
    assert any(ax.get_yscale() == 'log' for ax in ctl.fig.axes)
    assert any(ax.get_title(loc='left') == 'Refined' for ax in ctl.fig.axes)
    after = _plotted(ctl.fig)
    assert len(before) == len(after)
    for a, b in zip(before, after):
        assert np.array_equal(a, b), "refine moved a plotted value — it must be presentation-only"


def test_a_size_preset_drives_the_figure_to_its_final_physical_size():
    ctl = FigureRefineController(_fig(), FigureSpec())
    ctl.size_preset('single_column')
    expected = SIZE_PRESETS['single_column']['figure_size_in']
    assert ctl.spec.figure_size_in == tuple(expected)               # the spec drives export (exact → WYSIWYG)
    # the on-screen preview is driven to that final size (a constrained layout may nudge it by a hair)
    assert np.allclose(ctl.fig.get_size_inches(), expected, atol=0.1)


def test_export_bundle_is_wysiwyg_and_writes_the_full_set(tmp_path):
    summary = pd.DataFrame({'condition': ['WT', 'KO'], 'mean': [3.5, 4.5]})
    ctl = FigureRefineController(_fig(), FigureSpec(), summary_df=summary)
    ctl.set(title='Final', y_scale='log')
    out = ctl.export_bundle(tmp_path / 'fig.png')
    for k in ('pdf', 'svg', 'png', 'spec', 'summary'):
        assert out[k].exists()
    # WYSIWYG: the exported spec is exactly the refined preview's spec
    doc = json.loads(out['spec'].read_text(encoding='utf-8'))
    assert doc['title'] == 'Final' and doc['y_scale'] == 'log'
    assert ctl.fig.axes[0].get_yscale() == 'log'                    # and the figure still shows it
    assert pd.read_csv(out['summary'])['mean'].tolist() == [3.5, 4.5]


def test_set_is_chainable_and_returns_the_controller():
    ctl = FigureRefineController(_fig())
    assert ctl.set(legend=True).set(minor_ticks=True) is ctl
    assert ctl.spec.legend is True and ctl.spec.minor_ticks is True
