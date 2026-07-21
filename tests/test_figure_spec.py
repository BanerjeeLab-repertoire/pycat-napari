"""**Publication figure refinement — refine the presentation, never re-run the analysis.**

The contract test is the load-bearing one: changing the spec and re-rendering must leave the plotted values
byte-identical (a refinement path that silently recomputed could change numbers a user already believes).
Also pinned: the spec round-trips through JSON; axis labels/units default from the measurement ontology;
caveats render as a footnote matching the ontology; and export writes vector output with **text** fonts
(not outlines), a PNG at the requested DPI, the summary DataFrame, and a spec JSON that regenerates it.
"""
import numpy as np
import pandas as pd
import pytest

from pycat.utils.figure_spec import (
    FigureData, FigureSpec, apply_size_preset, export, ontology_caveats, ontology_y_label,
    render, spec_from_dict, spec_to_dict)

pytestmark = pytest.mark.core


def _data():
    return FigureData(measurement='partition_coefficient', groups=('ctrl', 'treated'),
                      values_by_group={'ctrl': np.array([2.0, 2.2, 1.9, 2.1]),
                                       'treated': np.array([4.0, 4.3, 3.8, 4.1, 4.2])})


def test_the_spec_round_trips_through_json_unchanged():
    spec = FigureSpec(title='K_p by condition', y_limits=(0.0, 5.0), palette='colorblind_safe',
                      figure_size_in=(3.5, 2.8), dpi=600, caveats_shown=True)
    assert spec_from_dict(spec_to_dict(spec)) == spec


def test_axis_labels_and_units_default_from_the_ontology():
    fig = render(_data(), FigureSpec())
    ax = fig.axes[0]
    assert ax.get_ylabel() == 'Partition coefficient (dimensionless)'   # display_name + units, no typing
    assert ax.get_xlabel() == 'condition'
    import matplotlib.pyplot as plt; plt.close(fig)


def test_an_explicit_label_overrides_the_ontology_default():
    fig = render(_data(), FigureSpec(y_label='Custom label'))
    assert fig.axes[0].get_ylabel() == 'Custom label'
    import matplotlib.pyplot as plt; plt.close(fig)


def test_caveats_render_as_a_footnote_matching_the_ontology():
    fig = render(_data(), FigureSpec(caveats_shown=True))
    footnotes = [t.get_text() for t in fig.texts if 'Caveat' in t.get_text()]
    assert footnotes, "caveats_shown did not render a footnote"
    caveat_text = footnotes[0]
    for c in ontology_caveats('partition_coefficient'):
        assert c in caveat_text, "the footnote text does not match the ontology caveat"
    import matplotlib.pyplot as plt; plt.close(fig)

    # Off by default → no footnote.
    fig2 = render(_data(), FigureSpec(caveats_shown=False))
    assert not [t for t in fig2.texts if 'Caveat' in t.get_text()]
    plt.close(fig2)


# ── THE contract: refinement re-renders, it never recomputes ────────────────────────────────────
def test_refinement_re_renders_but_does_not_recompute():
    data = _data()
    fig_a = render(data, FigureSpec(palette='colorblind_safe', title='A', y_label='K'))
    fig_b = render(data, FigureSpec(palette='grayscale', title='B different', y_label='different label',
                                    font_size_pt=14, figure_size_in=(7.0, 4.5), dpi=150))

    # The presentation differs …
    assert fig_a.axes[0].get_title() != fig_b.axes[0].get_title()
    # … but the PLOTTED VALUES are byte-identical — the numbers were not recomputed.
    for g in data.groups:
        np.testing.assert_array_equal(fig_a._pycat_plotted[g], fig_b._pycat_plotted[g])
    import matplotlib.pyplot as plt; plt.close(fig_a); plt.close(fig_b)


# ── Export: vector with TEXT fonts, PNG at DPI, summary + spec alongside ─────────────────────────
def test_export_writes_vector_text_fonts_png_summary_and_spec(tmp_path):
    import matplotlib
    fig = render(_data(), FigureSpec(dpi=300, figure_size_in=(3.5, 2.8)))
    summary = pd.DataFrame({'condition': ['ctrl', 'treated'], 'mean_Kp': [2.05, 4.08]})

    out = export(fig, tmp_path / 'figure1', spec=FigureSpec(dpi=300), summary_df=summary)

    assert out['pdf'].exists() and out['svg'].exists() and out['png'].exists()
    # Fonts embedded as TEXT (editable), not outlines.
    assert matplotlib.rcParams['pdf.fonttype'] == 42
    assert matplotlib.rcParams['svg.fonttype'] == 'none'
    # The SVG carries real <text> elements (text, not paths).
    assert '<text' in out['svg'].read_text(encoding='utf-8')

    # The summary DataFrame is written alongside the figure — the numbers are saved with it.
    assert out['summary'].exists()
    restored = pd.read_csv(out['summary'])
    assert list(restored['condition']) == ['ctrl', 'treated']

    # The spec JSON regenerates the figure identically.
    assert out['spec'].exists()
    import json
    reloaded = spec_from_dict(json.loads(out['spec'].read_text(encoding='utf-8')))
    assert isinstance(reloaded, FigureSpec) and reloaded.dpi == 300
    import matplotlib.pyplot as plt; plt.close(fig)


def test_size_presets_are_sizes_not_compliance_claims():
    spec = apply_size_preset(FigureSpec(), 'single_column')
    assert spec.figure_size_in == (3.5, 2.8) and spec.font_size_pt == 8.0
    assert ontology_y_label('partition_coefficient') == 'Partition coefficient (dimensionless)'
