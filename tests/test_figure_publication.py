"""**A refined figure round-trips its spec and exports at the requested DPI/format/size.**

Increment 4, the polish layer. The deliverable, verbatim from the roadmap, is the round-trip + export
test — both headless. The editable spec is presentation-only (it never touches the plotted data), so a
panel is refined and exported without re-running the analysis.

The one scientific-quality claim — the theme is **colour-blind-safe** — is not asserted by eye. The
palette was validated with the dataviz validator (`scripts/validate_palette.py`) on a white surface:
worst adjacent CVD ΔE 9.6 (deuteranopia), above the 8 target. That validation caught Okabe-Ito's
yellow failing the lightness band on white (L 0.90), which is why it is dropped. The lightness guard
below is a self-contained regression check against re-introducing exactly that failure.

The refinement **UI** and interactive editing are deferred — they need a viewer. What can be verified
without one is here.
"""

# Standard library imports
import json
import math

# Third party imports
import pytest

pytestmark = pytest.mark.core


@pytest.fixture(autouse=True)
def _agg():
    import matplotlib
    matplotlib.use('Agg')


def _fig():
    import matplotlib.pyplot as plt
    import numpy as np
    fig, ax = plt.subplots(figsize=(6, 4))
    for i in range(3):
        ax.plot(np.arange(10), np.arange(10) + 3 * i, label=f's{i}')
    ax.legend()
    return fig


# ── the spec round-trips ────────────────────────────────────────────────────────

def test_the_spec_round_trips_through_a_DICT():
    from pycat.utils.figure_publication import FigureSpec
    spec = FigureSpec(title='Fig 1', ylabel='area', ylim=(0, 100), column='double', dpi=600,
                      significance=[{'x1': 0, 'x2': 1, 'y': 90, 'label': '*'}])
    assert FigureSpec.from_dict(spec.to_dict()) == spec


def test_the_spec_round_trips_through_JSON(tmp_path):
    from pycat.utils.figure_publication import FigureSpec
    spec = FigureSpec(title='Panel A', xlim=(0, 5), theme='colorblind_safe', dpi=300)
    path = tmp_path / 'spec.json'
    spec.save(path)
    assert FigureSpec.load(path) == spec


def test_an_unknown_schema_is_REJECTED(tmp_path):
    from pycat.utils.figure_publication import FigureSpec
    path = tmp_path / 'bad.json'
    path.write_text(json.dumps({'schema': 'other/9', 'title': 'x'}), encoding='utf-8')
    with pytest.raises(ValueError, match='schema'):
        FigureSpec.load(path)


def test_from_dict_IGNORES_unknown_keys():
    """A spec saved by a newer version with extra fields still loads what it understands."""
    from pycat.utils.figure_publication import FigureSpec
    spec = FigureSpec.from_dict({'title': 'x', 'some_future_field': 42})
    assert spec.title == 'x'


# ── applying the spec changes presentation, not data ────────────────────────────

def test_apply_spec_sets_labels_limits_and_size():
    from pycat.utils.figure_publication import apply_spec, FigureSpec, JOURNAL_COLUMN_MM
    fig = _fig()
    apply_spec(fig, FigureSpec(title='T', xlabel='X', ylabel='Y', ylim=(0, 50), column='single'))
    ax = fig.axes[0]
    assert ax.get_title(loc='left') == 'T'
    assert ax.get_xlabel() == 'X' and ax.get_ylabel() == 'Y'
    assert ax.get_ylim() == (0, 50)
    assert abs(fig.get_size_inches()[0] * 25.4 - JOURNAL_COLUMN_MM['single']) < 0.5


def test_apply_spec_does_NOT_alter_the_plotted_data():
    """Refinement is presentation only — the y-values are the measurement and must not move."""
    from pycat.utils.figure_publication import apply_spec, FigureSpec
    fig = _fig()
    before = [ln.get_ydata().copy() for ln in fig.axes[0].get_lines()]
    apply_spec(fig, FigureSpec(title='T', ylim=(0, 5)))
    after = [ln.get_ydata() for ln in fig.axes[0].get_lines()]
    assert all((a == b).all() for a, b in zip(after, before))


def test_recolor_is_OPT_IN_and_leaves_a_figures_colours_alone_by_default():
    """The design correction rendering caught: a refine pass must not repaint a figure that coloured
    on purpose. Default `recolor=False` keeps the existing colours."""
    from pycat.utils.figure_publication import apply_spec, FigureSpec
    fig = _fig()
    fig.axes[0].get_lines()[0].set_color('#123456')       # a deliberate colour
    apply_spec(fig, FigureSpec())                          # default: no recolor
    assert fig.axes[0].get_lines()[0].get_color() == '#123456'


def test_recolor_TRUE_assigns_the_palette_in_FIXED_ORDER():
    """Opting in re-assigns the validated palette to series in order — for a plain multi-series plot
    that has no colour meaning of its own."""
    from pycat.utils.figure_publication import apply_spec, FigureSpec, PUBLICATION_PALETTE
    fig = _fig()
    apply_spec(fig, FigureSpec(recolor=True))
    colors = [ln.get_color() for ln in fig.axes[0].get_lines()]
    assert colors[:3] == list(PUBLICATION_PALETTE[:3])


def test_an_EMPTY_spec_is_a_no_op_on_labels():
    """A spec is a set of overrides; a None field leaves what the figure already has."""
    from pycat.utils.figure_publication import apply_spec, FigureSpec
    fig = _fig()
    fig.axes[0].set_xlabel('original')
    apply_spec(fig, FigureSpec(xlabel=None))
    assert fig.axes[0].get_xlabel() == 'original'


# ── export at the requested format / dpi / size ─────────────────────────────────

@pytest.mark.parametrize('ext', ['pdf', 'svg', 'png'])
def test_export_produces_the_requested_FORMAT(tmp_path, ext):
    from pycat.utils.figure_publication import export_figure
    fig = _fig()
    out = tmp_path / f'f.{ext}'
    export_figure(fig, out, dpi=300, column='single')
    assert out.exists() and out.stat().st_size > 1000
    if ext in ('pdf', 'svg'):
        head = out.read_bytes()[:512]
        assert (b'%PDF' in head) if ext == 'pdf' else (b'<svg' in head or b'<?xml' in head)


def test_a_higher_DPI_yields_a_higher_resolution_raster(tmp_path):
    """DPI is honoured: 600 dpi produces ~2× the pixels of 300 dpi for the same figure."""
    Image = pytest.importorskip('PIL.Image')
    from PIL import Image
    from pycat.utils.figure_publication import export_figure

    lo = tmp_path / 'lo.png'
    hi = tmp_path / 'hi.png'
    export_figure(_fig(), lo, dpi=150, column='single')
    export_figure(_fig(), hi, dpi=600, column='single')

    w_lo = Image.open(lo).size[0]
    w_hi = Image.open(hi).size[0]
    assert w_hi > 3.0 * w_lo          # 4x dpi -> ~4x pixels; >3x is a safe floor


def test_the_journal_COLUMN_width_is_honoured(tmp_path):
    from PIL import Image
    from pycat.utils.figure_publication import export_figure, JOURNAL_COLUMN_MM
    single = tmp_path / 's.png'
    double = tmp_path / 'd.png'
    export_figure(_fig(), single, dpi=300, column='single')
    export_figure(_fig(), double, dpi=300, column='double')
    # double column is ~183/89 ≈ 2.06× wider than single
    ratio = Image.open(double).size[0] / Image.open(single).size[0]
    assert 1.7 < ratio < 2.4


def test_vector_export_embeds_EDITABLE_fonts(tmp_path):
    """pdf.fonttype=42 embeds TrueType so labels stay editable in Illustrator — not outlined paths.
    An SVG keeps text as `<text>`, not `<path>`."""
    from pycat.utils.figure_publication import apply_spec, export_figure, FigureSpec
    fig = _fig()
    apply_spec(fig, FigureSpec(title='Editable Title', ylabel='signal'))
    svg = tmp_path / 'f.svg'
    export_figure(fig, svg)
    body = svg.read_text(encoding='utf-8')
    assert '<text' in body                 # text is text, not converted to paths


# ── the palette is colour-blind-safe, guarded ───────────────────────────────────

def _oklab_L(hexstr):
    """OKLab lightness of an sRGB hex (Björn Ottosson's transform). Self-contained so the guard needs
    no external validator at test time."""
    h = hexstr.lstrip('#')
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4) for c in rgb]
    r, g, b = lin
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    return 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_


def test_every_palette_colour_is_in_the_VALIDATED_lightness_band():
    """The regression guard for the exact failure the validator caught: Okabe-Ito's yellow at L 0.90
    was outside the band and dropped. If someone re-adds a too-light (or too-dark) colour, this fails
    and they must re-run the validator, not eyeball it."""
    from pycat.utils.figure_publication import PUBLICATION_PALETTE
    for hexstr in PUBLICATION_PALETTE:
        L = _oklab_L(hexstr)
        assert 0.43 <= L <= 0.77, f"{hexstr} has OKLab L={L:.3f}, outside the validated band 0.43-0.77"


def test_the_palette_is_the_frozen_validated_set():
    """Frozen to what was computed-validated. Changing it must be a deliberate act that re-runs the
    dataviz validator — not a silent edit."""
    from pycat.utils.figure_publication import PUBLICATION_PALETTE
    assert PUBLICATION_PALETTE == ('#0072B2', '#E69F00', '#009E73', '#D55E00', '#56B4E9', '#CC79A7')
    assert len(set(PUBLICATION_PALETTE)) == 6                 # all distinct


def test_significance_brackets_are_drawn_only_when_ASKED():
    """The bracket draws what it is told; the honesty (is this significant at the replicate level)
    lives in comparative_stats, never auto-generated here."""
    from pycat.utils.figure_publication import apply_spec, FigureSpec
    fig = _fig()
    before = len(fig.axes[0].texts)
    apply_spec(fig, FigureSpec(significance=[{'x1': 0, 'x2': 1, 'y': 20, 'label': '*'}]))
    assert len(fig.axes[0].texts) == before + 1
