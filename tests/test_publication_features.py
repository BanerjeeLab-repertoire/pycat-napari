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


# ── Tier 2: validated fonts + transparent background ─────────────────────────────────────────────
def test_resolve_font_family_accepts_installed_and_warns_on_missing():
    from pycat.utils.figure_spec import resolve_font_family
    fam, warn = resolve_font_family('DejaVu Sans')          # ships with matplotlib
    assert fam == 'DejaVu Sans' and warn is None
    fam2, warn2 = resolve_font_family('No Such Font 9000')
    assert fam2 is None and warn2 and 'not installed' in warn2
    assert resolve_font_family(None) == (None, None)


def test_a_valid_font_family_is_applied_to_the_axis_text():
    fig = render(_data({'WT': np.array([1.0, 2.0, 3.0])}), FigureSpec(font_family='DejaVu Sans'))
    assert 'DejaVu Sans' in fig.axes[0].yaxis.label.get_fontfamily()


def test_a_missing_font_WARNS_and_falls_back_not_silently():
    with pytest.warns(UserWarning, match="not installed|Falling back"):
        render(_data({'WT': np.array([1.0, 2.0])}), FigureSpec(font_family='No Such Font 9000'))


def test_font_and_transparent_fields_round_trip():
    spec = FigureSpec(font_family='DejaVu Sans', transparent_background=True)
    back = spec_from_dict(spec_to_dict(spec))
    assert back.font_family == 'DejaVu Sans' and back.transparent_background is True and back == spec


def test_export_passes_transparent_to_savefig(tmp_path, monkeypatch):
    from pycat.utils import figure_spec as fs
    fig = render(_data({'WT': np.array([1.0, 2.0, 3.0])}), FigureSpec())
    seen = []
    monkeypatch.setattr(fig, 'savefig', lambda *a, **k: seen.append(k.get('transparent')))
    fs.export(fig, tmp_path / 'f.png', spec=FigureSpec(transparent_background=True))
    assert seen and all(t is True for t in seen), "every saved format must honour the transparent flag"


# ── Tier 3: semantic colour mapping + dense-scatter rasterization ────────────────────────────────
def test_semantic_colour_map_gives_each_group_its_assigned_colour():
    from matplotlib.colors import to_rgba
    d = FigureData(measurement='area', groups=('WT', 'KO'),
                   values_by_group={'WT': np.array([1.0, 2.0]), 'KO': np.array([3.0, 4.0])})
    fig = render(d, FigureSpec(color_map={'WT': 'blue', 'KO': 'red'}))
    cols = fig.axes[0].collections                          # [WT scatter, KO scatter], in plot order
    assert np.allclose(cols[0].get_facecolor()[0], to_rgba('blue'))
    assert np.allclose(cols[1].get_facecolor()[0], to_rgba('red'))


def test_a_group_keeps_its_colour_regardless_of_order():
    from matplotlib.colors import to_rgba
    cmap = {'WT': 'blue', 'KO': 'red'}
    # KO first this time — it must still be red (colour follows identity, not position)
    d = FigureData(measurement='area', groups=('KO', 'WT'),
                   values_by_group={'KO': np.array([3.0, 4.0]), 'WT': np.array([1.0, 2.0])})
    fig = render(d, FigureSpec(color_map=cmap))
    assert np.allclose(fig.axes[0].collections[0].get_facecolor()[0], to_rgba('red'))   # KO


def test_rasterize_points_flags_the_scatter_layer_only_when_requested():
    d = _data({'WT': np.array([1.0, 2.0, 3.0])})
    assert fig_scatter(render(d, FigureSpec())).get_rasterized() in (False, None)
    assert fig_scatter(render(d, FigureSpec(rasterize_points=True))).get_rasterized() is True


def fig_scatter(fig):
    return fig.axes[0].collections[0]


def test_colour_map_and_rasterize_round_trip():
    spec = FigureSpec(color_map={'WT': '#123456'}, rasterize_points=True)
    back = spec_from_dict(spec_to_dict(spec))
    assert back.color_map == {'WT': '#123456'} and back.rasterize_points is True and back == spec


# ── Tier 3: export metadata (reproducibility) ────────────────────────────────────────────────────
def test_figure_export_metadata_names_pycat_and_its_versions():
    from pycat.utils.figure_spec import figure_export_metadata
    meta, sw = figure_export_metadata(FigureSpec(title='Fig 1'))
    assert meta['Software'].startswith('pycat-napari') and meta['Title'] == 'Fig 1'
    assert isinstance(sw, dict)                              # the software-versions record (may include numpy, …)


def test_export_embeds_the_software_in_the_png_and_the_provenance_in_the_json(tmp_path):
    from pycat.utils import figure_spec as fs
    from PIL import Image
    import json as _json
    fig = render(_data({'WT': np.array([1.0, 2.0, 3.0])}), FigureSpec(title='T'))
    out = fs.export(fig, tmp_path / 'fig.png', spec=FigureSpec(title='T'))
    info = Image.open(out['png']).info                      # PNG tEXt chunks
    assert 'pycat-napari' in info.get('Software', ''), "the PNG must record the software that made it"
    doc = _json.loads(out['spec'].read_text(encoding='utf-8'))
    assert 'software' in doc['_provenance']                 # the versions ride in the spec bundle too


def test_the_exported_spec_json_still_regenerates_despite_the_provenance_key(tmp_path):
    from pycat.utils import figure_spec as fs
    import json as _json
    spec = FigureSpec(title='T', y_scale='log', error_type='sem')
    fig = render(_data({'WT': np.array([1.0, 2.0, 3.0])}), spec)
    out = fs.export(fig, tmp_path / 'fig.png', spec=spec)
    back = fs.spec_from_dict(_json.loads(out['spec'].read_text(encoding='utf-8')))
    assert back == spec                                     # _provenance is tolerated, the spec round-trips


# ── Tier 3: exact regeneration from raw plotted data ─────────────────────────────────────────────
def test_exact_regeneration_reproduces_the_plotted_values(tmp_path):
    from pycat.utils import figure_spec as fs
    d = fs.FigureData(measurement='area', groups=('WT', 'KO'),
                      values_by_group={'WT': np.array([1.5, 2.5, 9.0]), 'KO': np.array([3.0, 4.0])})
    spec = FigureSpec(y_scale='log', error_type='sd', title='Fig')
    fig = render(d, spec)
    out = fs.export(fig, tmp_path / 'fig.png', spec=spec)
    assert out['data'].exists()
    regen = fs.regenerate(out['data'], fs.spec_from_dict(
        __import__('json').loads(out['spec'].read_text(encoding='utf-8'))))
    # the regenerated figure plots the SAME raw values, and honours the same spec
    for g in ('WT', 'KO'):
        assert np.allclose(regen._pycat_plotted[g], d.values_by_group[g])
    assert regen.axes[0].get_yscale() == 'log'


def test_figdata_round_trips_through_its_dict():
    from pycat.utils.figure_spec import figdata_to_dict, figdata_from_dict, FigureData
    d = FigureData(measurement='viscosity', groups=('a', 'b'),
                   values_by_group={'a': np.array([1.0, 2.0]), 'b': np.array([3.0])}, x_label='cond')
    back = figdata_from_dict(figdata_to_dict(d))
    assert back.measurement == 'viscosity' and back.groups == ('a', 'b') and back.x_label == 'cond'
    assert np.allclose(back.values_by_group['a'], [1.0, 2.0])


# ── Tier 2: legend control ───────────────────────────────────────────────────────────────────────
def test_no_legend_by_default_and_one_entry_per_group_when_on():
    d = FigureData(measurement='area', groups=('WT', 'KO', 'DKO'),
                   values_by_group={'WT': np.array([1.0, 2.0]), 'KO': np.array([3.0]),
                                    'DKO': np.array([4.0, 5.0])})
    assert render(d, FigureSpec()).axes[0].get_legend() is None
    leg = render(d, FigureSpec(legend=True)).axes[0].get_legend()
    assert leg is not None and [t.get_text() for t in leg.get_texts()] == ['WT', 'KO', 'DKO']


def test_legend_frame_and_ncol_are_honoured():
    d = _data({'WT': np.array([1.0, 2.0]), 'KO': np.array([3.0, 4.0])})
    framed = render(d, FigureSpec(legend=True, legend_frame=True)).axes[0].get_legend()
    plain = render(d, FigureSpec(legend=True, legend_frame=False)).axes[0].get_legend()
    assert framed.get_frame_on() is True and plain.get_frame_on() is False
    leg2 = render(d, FigureSpec(legend=True, legend_ncol=2)).axes[0].get_legend()
    assert getattr(leg2, '_ncols', getattr(leg2, '_ncol', 1)) == 2


def test_legend_uses_the_semantic_colour_map():
    from matplotlib.colors import to_rgba
    d = FigureData(measurement='area', groups=('WT', 'KO'),
                   values_by_group={'WT': np.array([1.0]), 'KO': np.array([2.0])})
    leg = render(d, FigureSpec(legend=True, color_map={'WT': 'blue', 'KO': 'red'})).axes[0].get_legend()
    handle_colors = [h.get_markerfacecolor() for h in leg.legend_handles]
    assert to_rgba(handle_colors[0]) == to_rgba('blue') and to_rgba(handle_colors[1]) == to_rgba('red')


def test_legend_fields_round_trip():
    spec = FigureSpec(legend=True, legend_loc='upper left', legend_ncol=3, legend_frame=False)
    assert spec_from_dict(spec_to_dict(spec)) == spec
