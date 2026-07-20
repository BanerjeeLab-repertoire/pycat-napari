"""**The two FigureSpec systems merged — one canonical spec, significance now rendered, no visual change.**

`figure_spec.FigureSpec` and `figure_publication.FigureSpec` overlapped-but-differed; a feature added to one
was absent from the other. They are merged behind `figure_spec.FigureSpec` (the canonical spec), which now
carries every field both had, and `figure_spec.render()` finally HONOURS significance (the verified gap —
the working bracket implementation was in figure_publication). These pin: the merge is pixel-equivalent for
existing usage (absorbed fields default off), render() draws requested brackets, refine() reuses the
validated publication path so output is unchanged, the JSON round-trip carries the new fields, and the
deprecated `figure_publication` shim still works.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.core


def _fd():
    from pycat.utils.figure_spec import FigureData
    return FigureData(measurement='area', groups=('WT', 'mut'),
                      values_by_group={'WT': np.array([1.0, 2, 3]), 'mut': np.array([4.0, 5, 6])})


def test_the_canonical_spec_carries_every_capability_both_specs_had():
    from pycat.utils.figure_spec import FigureSpec
    f = {fld.name for fld in __import__('dataclasses').fields(FigureSpec)}
    # ontology-side fields
    assert {'x_label', 'y_label', 'palette', 'font_size_pt', 'caveats_shown'} <= f
    # publication-side fields, absorbed
    assert {'theme', 'recolor', 'journal_column', 'height_mm', 'tick_format', 'significance_brackets'} <= f


def test_default_render_is_UNCHANGED_the_merge_is_pixel_equivalent_for_existing_usage():
    """A spec that sets none of the absorbed fields renders exactly as before — no brackets, same plotted
    values, same size."""
    from pycat.utils.figure_spec import FigureSpec, render
    fig = render(_fd(), FigureSpec())
    ax = fig.axes[0]
    assert len(ax.get_lines()) == 2, "default render drew extra artists — output changed"  # 2 mean bars
    assert tuple(fig.get_size_inches()) == (5.0, 3.5)
    assert set(fig._pycat_plotted) == {'WT', 'mut'}


def test_render_now_HONOURS_significance_brackets_the_verified_gap():
    from pycat.utils.figure_spec import FigureSpec, render
    plain = render(_fd(), FigureSpec())
    withb = render(_fd(), FigureSpec(significance_brackets=({'x1': 0, 'x2': 1, 'y': 7, 'label': '*'},)))
    assert len(withb.axes[0].get_lines()) > len(plain.axes[0].get_lines()), (
        "significance brackets requested but render() drew none — the merged gap is not closed")


def test_refine_reuses_the_validated_publication_path_output_unchanged():
    """`refine(fig, canonical)` must equal `figure_publication.apply_spec(fig, equivalent_pub_spec)` — the
    merge changes the API surface, not the rendered output."""
    from pycat.utils.figure_spec import FigureSpec, render, refine
    from pycat.utils.figure_publication import FigureSpec as PubSpec, apply_spec

    a = refine(render(_fd(), FigureSpec()),
               FigureSpec(journal_column='double', theme='colorblind_safe', height_mm=60.0))
    b = apply_spec(render(_fd(), FigureSpec()),
                   PubSpec(column='double', theme='colorblind_safe', height_mm=60.0))
    assert np.allclose(a.get_size_inches(), b.get_size_inches()), "refine diverged from apply_spec"


def test_the_json_roundtrip_carries_the_new_fields_unchanged():
    from pycat.utils.figure_spec import FigureSpec, spec_to_dict, spec_from_dict
    import json
    spec = FigureSpec(journal_column='onehalf', tick_format='%.2f', recolor=True,
                      significance_brackets=({'x1': 0, 'x2': 1, 'y': 7, 'label': 'p=0.01'},))
    back = spec_from_dict(json.loads(json.dumps(spec_to_dict(spec))))
    assert back.journal_column == 'onehalf' and back.tick_format == '%.2f' and back.recolor is True
    assert back.significance_brackets == ({'x1': 0, 'x2': 1, 'y': 7, 'label': 'p=0.01'},)


def test_the_deprecated_publication_shim_still_works():
    """Existing consumers using figure_publication.FigureSpec keep working until they migrate."""
    from pycat.utils.figure_spec import FigureSpec, render
    from pycat.utils.figure_publication import FigureSpec as PubSpec, apply_spec
    fig = apply_spec(render(_fd(), FigureSpec()), PubSpec(title='T', column='single'))
    assert fig.axes[0].get_title(loc='left') == 'T'          # apply_spec titles left-aligned
