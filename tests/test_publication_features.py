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
    FigureData, FigureSpec, render, refine, resolve_y_scale, group_error, spec_to_dict, spec_from_dict)

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
