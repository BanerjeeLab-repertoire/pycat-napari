"""**The Refine panel drives the controller over one figure (explore_refine_export Part A).**

The workflow contract — refine-not-recompute + WYSIWYG bundle — is pinned Qt-free in `test_figure_refine`.
This pins that the comparative dialog's Refine row is wired to it: changing a control mutates the spec and
restyles the SAME figure. Integration-marked (skips headless).
"""
import matplotlib
matplotlib.use('Agg', force=False)
import pandas as pd
import pytest


def _comparative_fig():
    from pycat.utils.comparative_figures import condition_comparison_figure
    df = pd.DataFrame({
        'condition': ['WT'] * 6 + ['KO'] * 6,
        'replicate': (['r1', 'r1', 'r2', 'r2', 'r3', 'r3']) * 2,
        'value': [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
    })
    return condition_comparison_figure(df, 'partition_coefficient',
                                       condition_col='condition', replicate_col='replicate')


@pytest.mark.integration
def test_refine_controls_mutate_the_spec_and_restyle_the_same_figure(qtbot):
    from pycat.ui.comparative_figures_ui import _build_refine_row
    from pycat.utils.figure_refine import FigureRefineController
    from pycat.utils.figure_spec import FigureSpec

    fig = _comparative_fig()
    ctl = FigureRefineController(fig, FigureSpec(figure_size_in=tuple(fig.get_size_inches())))
    redraws = []
    row = _build_refine_row(ctl, lambda: redraws.append(1), None)
    c = row._controls

    c['yscale'].setCurrentText('log')
    assert ctl.spec.y_scale == 'log' and any(ax.get_yscale() == 'log' for ax in fig.axes)

    c['legend'].setChecked(True)
    assert ctl.spec.legend is True

    c['title'].setText('My Figure'); c['title'].editingFinished.emit()
    assert ctl.spec.title == 'My Figure'
    assert any(ax.get_title(loc='left') == 'My Figure' for ax in fig.axes)

    c['size'].setCurrentText('single_column')
    assert ctl.spec.figure_size_in == (3.5, 2.8)                 # preview driven to final print size
    assert redraws, "every refine change must trigger a redraw"


@pytest.mark.integration
def test_the_export_button_writes_the_bundle(qtbot, tmp_path, monkeypatch):
    from pycat.ui.comparative_figures_ui import _build_refine_row
    from pycat.utils.figure_refine import FigureRefineController
    from pycat.utils.figure_spec import FigureSpec
    from PyQt5.QtWidgets import QFileDialog

    fig = _comparative_fig()
    ctl = FigureRefineController(fig, FigureSpec(figure_size_in=tuple(fig.get_size_inches())),
                                 summary_df=pd.DataFrame({'condition': ['WT'], 'mean': [3.5]}))
    row = _build_refine_row(ctl, lambda: None, None)
    target = tmp_path / 'out.png'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', staticmethod(lambda *a, **k: (str(target), '')))
    row._controls['export'].click()
    assert target.with_suffix('.pdf').exists() and target.with_suffix('.json').exists()
