"""**publication_features Tier 1 — log / symlog y-scale on the canonical FigureSpec, validated not silent.**

Size and intensity distributions are often log-normal, so a linear axis misrepresents them. This pins the
`y_scale` field: render and refine honour it; a log request on data with non-positive values falls back to
**symlog with a warning** (never a silent substitution or a crash); the field round-trips through JSON; and
a bare spec still renders linear (publication-sane default). Matplotlib Agg, `core`.
"""
import warnings

import matplotlib
matplotlib.use('Agg', force=False)
import numpy as np
import pytest

from pycat.utils.figure_spec import (
    FigureData, FigureSpec, render, refine, render_multipanel, resolve_y_scale, group_error,
    spec_to_dict, spec_from_dict)

pytestmark = pytest.mark.core


def _data(values_by_group):
    return FigureData(measurement='area', groups=tuple(values_by_group),
                      values_by_group=values_by_group)


def test_default_is_linear_a_bare_spec_is_unchanged():
    fig = render(_data({'WT': np.array([1.0, 2.0, 3.0])}), FigureSpec())
    assert fig.axes[0].get_yscale() == 'linear'


def test_a_log_scale_is_actually_log_when_the_data_is_positive():
    fig = render(_data({'WT': np.array([1.0, 10.0, 100.0]), 'KO': np.array([2.0, 20.0])}),
                 FigureSpec(y_scale='log'))
    assert fig.axes[0].get_yscale() == 'log'


def test_symlog_is_honoured():
    fig = render(_data({'WT': np.array([-5.0, 0.0, 5.0])}), FigureSpec(y_scale='symlog'))
    assert fig.axes[0].get_yscale() == 'symlog'


def test_log_on_nonpositive_data_falls_back_to_symlog_WITH_a_warning_not_silently():
    d = _data({'WT': np.array([0.0, 1.0, 2.0]), 'KO': np.array([-1.0, 3.0])})   # crosses zero
    with pytest.warns(UserWarning, match="log y-scale.*non-positive|symlog"):
        fig = render(d, FigureSpec(y_scale='log'))
    assert fig.axes[0].get_yscale() == 'symlog'          # fell back, did not clip/crash/stay-log


def test_the_resolve_helper_is_pure_and_returns_the_consequence():
    scale, warn = resolve_y_scale('log', [np.array([1.0, 2.0])])          # all positive
    assert scale == 'log' and warn is None
    scale, warn = resolve_y_scale('log', [np.array([0.0, 5.0])])          # a zero
    assert scale == 'symlog' and warn and 'non-positive' in warn
    assert resolve_y_scale('linear', [np.array([-1.0])]) == ('linear', None)


def test_y_scale_round_trips_through_json():
    spec = FigureSpec(y_scale='log', title='t')
    back = spec_from_dict(spec_to_dict(spec))
    assert back.y_scale == 'log' and back == spec


def test_refine_applies_the_scale_without_recomputing():
    d = _data({'WT': np.array([1.0, 10.0, 100.0])})
    fig = render(d, FigureSpec())                         # rendered linear
    assert fig.axes[0].get_yscale() == 'linear'
    plotted_before = {k: v.copy() for k, v in fig._pycat_plotted.items()}
    refine(fig, FigureSpec(y_scale='log'))
    assert fig.axes[0].get_yscale() == 'log'
    # the data on the figure is untouched — refine is presentation-only
    for k, v in plotted_before.items():
        assert np.allclose(fig._pycat_plotted[k], v)


def test_refine_log_on_nonpositive_also_falls_back_with_a_warning():
    fig = render(_data({'WT': np.array([-2.0, 0.0, 4.0])}), FigureSpec())
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter('always')
        refine(fig, FigureSpec(y_scale='log'))
    assert fig.axes[0].get_yscale() == 'symlog' and any('non-positive' in str(x.message) for x in w)


def test_group_error_computes_sd_sem_and_ci_and_needs_two_points():
    v = np.array([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])                 # sd = 2.138 (ddof=1)
    sd = group_error(v, 'sd')
    assert abs(sd - float(np.std(v, ddof=1))) < 1e-9
    assert abs(group_error(v, 'sem') - sd / np.sqrt(v.size)) < 1e-9
    assert abs(group_error(v, 'ci95') - 1.96 * sd / np.sqrt(v.size)) < 1e-9
    assert group_error(np.array([3.0]), 'sd') == 0.0                       # one point → no spread
    assert group_error(v, 'bogus') == 0.0


def test_error_bars_are_drawn_only_when_requested_and_the_type_is_LABELLED():
    d = _data({'WT': np.array([1.0, 2.0, 3.0, 4.0]), 'KO': np.array([2.0, 4.0, 6.0])})
    none = render(d, FigureSpec())
    assert not none.axes[0].containers                                    # no error bars by default
    sem = render(d, FigureSpec(error_type='sem'))
    assert sem.axes[0].containers                                         # an ErrorbarContainer is present
    labels = [t.get_text() for t in sem.axes[0].texts]
    assert any('SEM' in t for t in labels), "the error type must be stated on the figure"
    assert spec_from_dict(spec_to_dict(FigureSpec(error_type='ci95'))).error_type == 'ci95'


def test_minor_ticks_are_off_by_default_and_on_when_requested():
    d = _data({'WT': np.array([1.0, 2.0, 3.0])})
    off = render(d, FigureSpec())
    on = render(d, FigureSpec(minor_ticks=True))
    # matplotlib reports minor ticks only once they are enabled and the axis has range
    assert len(on.axes[0].yaxis.get_minorticklocs()) > len(off.axes[0].yaxis.get_minorticklocs())
    assert spec_from_dict(spec_to_dict(FigureSpec(minor_ticks=True))).minor_ticks is True


# ── Tier 2: multi-panel layout + panel labels ────────────────────────────────────────────────────
def _panels(n):
    return [FigureData(measurement='area', groups=('WT', 'KO'),
                       values_by_group={'WT': np.array([1.0, 2.0, 3.0]),
                                        'KO': np.array([2.0, 4.0])}) for _ in range(n)]


def test_multipanel_makes_one_axis_per_panel_labelled_A_B_C():
    fig = render_multipanel(_panels(3))
    assert len(fig.axes) == 3
    corner = [t.get_text() for ax in fig.axes for t in ax.texts]
    assert 'A' in corner and 'B' in corner and 'C' in corner


def test_multipanel_respects_n_cols_for_the_grid():
    fig = render_multipanel(_panels(4), n_cols=2)
    # a 2x2 grid: every axis spans one cell of a 2-col, 2-row gridspec
    geoms = {ax.get_subplotspec().get_geometry()[:2] for ax in fig.axes}
    assert geoms == {(2, 2)}                                # (nrows, ncols) == 2x2 for all four


def test_panel_labels_can_be_turned_off():
    # the n= annotations are always present; assert only that no A/B/… panel label was added
    fig = render_multipanel(_panels(2), panel_labels=False)
    assert not any(t.get_text() in ('A', 'B') for ax in fig.axes for t in ax.texts)


def test_a_per_panel_spec_overrides_the_shared_one():
    panels = [(_panels(1)[0], FigureSpec(y_scale='log')), _panels(1)[0]]
    fig = render_multipanel(panels, spec=FigureSpec())
    assert fig.axes[0].get_yscale() == 'log' and fig.axes[1].get_yscale() == 'linear'


def test_multipanel_stashes_per_panel_data_and_recomputes_nothing():
    fig = render_multipanel(_panels(2))
    assert isinstance(fig._pycat_plotted, list) and len(fig._pycat_plotted) == 2
    assert set(fig._pycat_plotted[0]) == {'WT', 'KO'}


def test_a_single_panel_grid_still_renders():
    fig = render_multipanel(_panels(1))
    assert len(fig.axes) == 1


def test_panel_labels_extend_past_Z():
    from pycat.utils.figure_spec import _panel_label
    assert (_panel_label(0), _panel_label(25), _panel_label(26)) == ('A', 'Z', 'AA')


def test_empty_panels_is_refused():
    with pytest.raises(ValueError, match='at least one panel'):
        render_multipanel([])
