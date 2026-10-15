"""**The QC report's text does not overlap** — the test `report.py` cites as its proof.

`plot_qc_report` says the SubFigure + constrained-layout design made scorecard/panel overlap structurally
impossible, "and the mechanical overlap test (`test_the_report_has_no_overlapping_text`) is what proved it". That
test did not exist (Completion Spec G "also fix while in the file"). This is it: render the report for a 2-D image
and for a time series, and require that no scorecard text intersects any diagnostic-panel text, and that no two
scorecard lines intersect each other.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.base


def _texts(subfig, renderer):
    import matplotlib.text
    return [(t.get_text()[:40], t.get_window_extent(renderer))
            for t in subfig.findobj(matplotlib.text.Text) if t.get_visible() and t.get_text().strip()]


@pytest.mark.parametrize('kind', ['2d', 'time series'])
def test_the_report_has_no_overlapping_text(kind):
    import matplotlib
    matplotlib.use('Agg')
    from pycat.toolbox.data_qc.report import plot_qc_report
    from pycat.toolbox.data_qc.runner import run_full_qc
    rng = np.random.default_rng(0)
    if kind == '2d':
        data, kw = rng.normal(100, 5, (128, 128)) + np.pad(np.full((20, 20), 300.0), 54), {}
    else:
        data, kw = rng.normal(100, 5, (24, 96, 96)), {'axis': 't'}
    fig = plot_qc_report(run_full_qc(data, **kw), interactive=False)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    assert len(fig.subfigs) == 2, 'expected the scorecard and the diagnostic grid as two subfigures'
    score, diag = (_texts(sf, renderer) for sf in fig.subfigs)
    assert score and diag
    across = [(a, b) for a, ba in score for b, bb in diag if ba.overlaps(bb)]
    within = [(a, b) for i, (a, ba) in enumerate(score) for b, bb in score[i + 1:] if ba.overlaps(bb)]
    assert not across, f'scorecard text overlaps a diagnostic panel: {across[:5]}'
    assert not within, f'scorecard lines overlap each other: {within[:5]}'
    matplotlib.pyplot.close(fig)


def test_the_report_degrades_on_a_row_missing_keys():
    """G10: the report indexed `r['status']` / `r['headline']` unconditionally and raised KeyError."""
    import matplotlib
    matplotlib.use('Agg')
    from pycat.toolbox.data_qc.report import plot_qc_report
    fig = plot_qc_report([{'name': 'partial row'}], interactive=False)
    matplotlib.pyplot.close(fig)
