"""**Refine any PyCAT matplotlib figure to publication quality, without re-running the analysis.**

Increment 4, the polish layer. A figure holds its data; this holds an editable *spec* over it — labels,
limits, ticks, a colour-blind-safe theme, fonts, journal-column sizing, significance brackets — so a
panel is refined and exported without touching the pipeline that made it. matplotlib stays the
publication backend (PyQtGraph is for interactive explore; this is where "refine a matplotlib plot"
lives).

── The palette is computed, not chosen by eye ───────────────────────────────────────────────

``PUBLICATION_PALETTE`` is the Okabe-Ito colour-blind-safe categorical palette **minus its yellow**,
validated on a white publication surface with the dataviz validator (`scripts/validate_palette.py`):

    Lightness band   PASS  all 6 inside L 0.43-0.77
    Chroma floor     PASS  all >= 0.1
    CVD separation   PASS  worst adjacent deltaE 9.6 (deuteranopia) — above the 8 target
    Normal-vision    PASS  worst adjacent deltaE 20.0
    Contrast         WARN  orange/sky-blue < 3:1 vs white — relieved by the always-present legend

Okabe-Ito's yellow (#F0E442) was dropped because it **failed** the lightness band on white (L 0.90) —
the validator caught a legibility problem that "it's the standard palette" would have shipped. The
lesson the dataviz method insists on: never eyeball colour-blind-safety, run the check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Optional


# Okabe-Ito minus yellow. Fixed order — categorical hues are assigned in order, never cycled.
PUBLICATION_PALETTE = (
    '#0072B2',   # blue
    '#E69F00',   # orange
    '#009E73',   # bluish green
    '#D55E00',   # vermillion
    '#56B4E9',   # sky blue
    '#CC79A7',   # reddish purple
)

# Journal single/one-and-a-half/double column widths, millimetres (Nature/Cell conventions).
JOURNAL_COLUMN_MM = {'single': 89.0, 'onehalf': 120.0, 'double': 183.0}

#: Named themes. Each is a small dict of matplotlib rcParams-style knobs applied by `apply_spec`.
#: `colorblind_safe` is the default and the only palette that has been validated; others may add
#: variety but the default is the one to trust for a colour-blind reader.
THEMES = {
    'colorblind_safe': dict(palette=PUBLICATION_PALETTE, font_size=8, title_size=9,
                            spines=('left', 'bottom'), grid=False, line_width=1.0),
    'colorblind_safe_grid': dict(palette=PUBLICATION_PALETTE, font_size=8, title_size=9,
                                 spines=('left', 'bottom'), grid=True, line_width=1.0),
}


@dataclass
class FigureSpec:
    """**DEPRECATED — use ``figure_spec.FigureSpec`` (the canonical merged spec).**

    The two `FigureSpec` implementations were merged in 1.6.192: `figure_spec.FigureSpec` now carries every
    field this one had (journal column, mm height, theme, recolour, tick format, significance brackets), and
    `figure_spec.refine(fig, spec)` applies them by delegating to `apply_spec` below — so THIS class stays
    the validated rendering carrier while the canonical PUBLIC spec is `figure_spec.FigureSpec`. New code
    should build a `figure_spec.FigureSpec` and call `figure_spec.render`/`refine`; this remains for the
    existing consumers until they migrate.

    An editable description of a figure's presentation. Round-trips to a dict / JSON. Every field is
    optional: ``None`` means "leave what the figure already has", so applying an empty spec is a no-op.
    """
    title: Optional[str] = None
    xlabel: Optional[str] = None
    ylabel: Optional[str] = None
    xlim: Optional[tuple] = None
    ylim: Optional[tuple] = None
    theme: str = 'colorblind_safe'
    recolor: bool = False                  # opt-in — see `apply_spec`; default respects the figure's colours
    font_size: Optional[float] = None
    title_size: Optional[float] = None
    column: str = 'single'                 # 'single' | 'onehalf' | 'double'
    height_mm: Optional[float] = None       # None -> keep aspect from the current figure width ratio
    dpi: int = 300
    tick_format: Optional[str] = None       # e.g. '%.2f' applied to the y axis
    significance: list = field(default_factory=list)   # [{'x1','x2','y','label'}]

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "FigureSpec":
        known = {f for f in FigureSpec.__dataclass_fields__}
        return FigureSpec(**{k: v for k, v in (d or {}).items() if k in known})

    def save(self, path) -> None:
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'schema': 'pycat.figure_spec/1', **self.to_dict()}, fh, indent=2)

    @staticmethod
    def load(path) -> "FigureSpec":
        with open(path, 'r', encoding='utf-8') as fh:
            blob = json.load(fh)
        if blob.pop('schema', None) != 'pycat.figure_spec/1':
            raise ValueError("unrecognised figure-spec schema — cannot load safely")
        # tuples survive JSON as lists; restore the two that matter.
        for k in ('xlim', 'ylim'):
            if isinstance(blob.get(k), list):
                blob[k] = tuple(blob[k])
        return FigureSpec.from_dict(blob)


def apply_spec(fig, spec: FigureSpec):
    """Apply ``spec`` to a matplotlib figure in place, and return it.

    Presentation only — it never touches the plotted data, so a refined figure is the same measurement
    dressed differently.

    Recolouring is **opt-in** (``spec.recolor``), and off by default, deliberately. Most PyCAT figures
    already colour purposefully — the comparative figure makes the replicate means one colour so they
    read as "the units tested", and blindly re-assigning the palette in series order would scramble
    that meaning. So a refine pass adjusts fonts, spines, size and labels without hijacking colour;
    ``recolor=True`` opts a plain multi-series line plot into the validated palette.
    """
    theme = THEMES.get(spec.theme, THEMES['colorblind_safe'])
    font_size = spec.font_size or theme['font_size']
    title_size = spec.title_size or theme['title_size']

    for ax in fig.axes:
        if spec.title is not None:
            ax.set_title(spec.title, fontsize=title_size, loc='left')
        if spec.xlabel is not None:
            ax.set_xlabel(spec.xlabel, fontsize=font_size)
        if spec.ylabel is not None:
            ax.set_ylabel(spec.ylabel, fontsize=font_size)
        if spec.xlim is not None:
            ax.set_xlim(spec.xlim)
        if spec.ylim is not None:
            ax.set_ylim(spec.ylim)
        ax.tick_params(labelsize=font_size)
        for side in ('top', 'right', 'left', 'bottom'):
            ax.spines[side].set_visible(side in theme['spines'])
        # Only pass line properties when enabling — `grid(False, color=...)` perversely ENABLES it.
        if theme['grid']:
            ax.grid(True, color='0.9', linewidth=0.6)
        else:
            ax.grid(False)
        if spec.tick_format:
            import matplotlib.ticker as mticker
            ax.yaxis.set_major_formatter(mticker.FormatStrFormatter(spec.tick_format))
        if spec.recolor:
            _recolor_series(ax, theme['palette'])
        for ann in spec.significance:
            add_significance_bracket(ax, ann['x1'], ann['x2'], ann['y'],
                                     ann.get('label', ''), font_size=font_size)

    width_mm = JOURNAL_COLUMN_MM.get(spec.column, JOURNAL_COLUMN_MM['single'])
    width_in = width_mm / 25.4
    if spec.height_mm:
        fig.set_size_inches(width_in, spec.height_mm / 25.4)
    else:
        # keep the current aspect ratio at the journal width
        w0, h0 = fig.get_size_inches()
        fig.set_size_inches(width_in, width_in * (h0 / w0) if w0 else width_in * 0.75)
    fig.tight_layout()
    return fig


def _recolor_series(ax, palette):
    """Re-assign the palette, in fixed order, to the lines and scatter collections of an axis."""
    lines = [ln for ln in ax.get_lines() if ln.get_label() and not ln.get_label().startswith('_')]
    for i, ln in enumerate(lines):
        ln.set_color(palette[i % len(palette)])
    for i, coll in enumerate(ax.collections):
        try:
            coll.set_color(palette[i % len(palette)])
        except Exception:
            pass


def add_significance_bracket(ax, x1, x2, y, label='*', *, font_size=8):
    """Draw a significance bracket between two x positions at height ``y``.

    Only the caller decides whether to add one — this draws what it is told. The honesty lives
    upstream in `comparative_stats`, which is where the decision "is this significant, at the replicate
    level" is made; a bracket is never generated automatically from a pixel-level test.
    """
    h = (ax.get_ylim()[1] - ax.get_ylim()[0]) * 0.02
    ax.plot([x1, x1, x2, x2], [y, y + h, y + h, y], lw=1.0, color='0.2')
    ax.text((x1 + x2) / 2, y + h, label, ha='center', va='bottom', fontsize=font_size, color='0.2')


def export_figure(fig, path, *, fmt=None, dpi=None, column=None, spec: FigureSpec = None):
    """Export at publication settings: vector (PDF/SVG) or high-DPI raster, at a journal column width.

    Fonts are embedded in vector output (``pdf.fonttype=42`` / ``ps.fonttype=42`` = editable TrueType;
    SVG keeps text as text) so the file opens in Illustrator/Inkscape with live, editable labels rather
    than outlines. ``fmt`` defaults to the path's extension; ``dpi``/``column`` fall back to the spec.
    """
    import matplotlib
    import pathlib

    path = pathlib.Path(path)
    fmt = (fmt or path.suffix.lstrip('.') or 'pdf').lower()
    dpi = dpi or (spec.dpi if spec else 300)
    column = column or (spec.column if spec else 'single')

    if column:
        width_in = JOURNAL_COLUMN_MM.get(column, JOURNAL_COLUMN_MM['single']) / 25.4
        w0, h0 = fig.get_size_inches()
        fig.set_size_inches(width_in, width_in * (h0 / w0) if w0 else width_in * 0.75)

    with matplotlib.rc_context({'pdf.fonttype': 42, 'ps.fonttype': 42, 'svg.fonttype': 'none'}):
        fig.savefig(str(path), format=fmt, dpi=dpi, bbox_inches='tight')
    return str(path)
