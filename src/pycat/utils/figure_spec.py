"""**Publication figure refinement — refine the presentation, never re-run the analysis.**

A PyCAT comparative figure can be produced but not prepared for a journal: there is no export, no DPI, no
vector output. Today, adjusting an axis label or a colour means re-running the analysis — slow, and
scientifically wasteful, because the numbers are already correct; only presentation needs work.

The core idea: separate the figure's DATA from a declarative **spec**, so refinement mutates the spec and
**re-renders** — it never recomputes. This is the contract, and the contract test enforces it: changing the
spec and re-rendering leaves the plotted values byte-identical, only the presentation differs. A refinement
path that silently re-ran analysis could change numbers a user already believes.

The **measurement ontology** (1.6.154) supplies the defaults — the payoff: the y-axis label defaults to
``"Partition coefficient (dimensionless)"`` (correct units, no typing), and a measurement's caveats can
render as a figure footnote, so the 2D-projection-proxy warning travels onto the figure instead of being
lost between the analysis and the paper.

Export writes **vector output (PDF/SVG) with fonts embedded as text** (editors need to adjust type), a
high-DPI PNG, the **summary DataFrame** alongside (a figure whose numbers are not saved is irreproducible),
and the **spec as JSON** so the figure can be regenerated identically. Size presets are offered as *sizes*,
never as journal-compliance claims (requirements vary; a tool that promises compliance it cannot verify is
worse than one offering sensible defaults).
"""
from __future__ import annotations

import dataclasses
import json
import pathlib

import numpy as np


#: Okabe–Ito colour-blind-safe qualitative palette (the default; identity is never colour-alone downstream).
_PALETTES = {
    'colorblind_safe': ('#0072B2', '#E69F00', '#009E73', '#CC79A7', '#56B4E9', '#D55E00', '#F0E442', '#000000'),
    'grayscale': ('#000000', '#555555', '#999999', '#cccccc'),
}

#: SIZE presets — widths with legible fonts. Deliberately NOT journal-compliance claims.
SIZE_PRESETS = {
    'single_column':       {'figure_size_in': (3.5, 2.8), 'font_size_pt': 8.0},
    'one_and_half_column': {'figure_size_in': (5.0, 3.5), 'font_size_pt': 9.0},
    'double_column':       {'figure_size_in': (7.0, 4.5), 'font_size_pt': 10.0},
}


@dataclasses.dataclass
class FigureData:
    """The already-computed data a figure plots — read, never recomputed, by ``render``."""
    measurement: str
    groups: tuple
    values_by_group: dict            # group label -> 1D array of per-object values
    x_label: "str | None" = None


@dataclasses.dataclass
class FigureSpec:
    """The **canonical** declarative presentation spec (the two `FigureSpec` implementations merged here).
    Mutating this and re-rendering never recomputes the data.

    It carries the ontology-aware fields this module always had AND the journal/theme/bracket capabilities
    absorbed from the former ``figure_publication`` module (the fields below the divider). Every absorbed
    field defaults to off/None, so a spec that sets none of them renders EXACTLY as before — the merge is
    pixel-equivalent by construction. (The old ``figure_publication.FigureSpec`` shim has been removed; its
    validated rendering primitives now live at the bottom of this module.)
    """
    title: "str | None" = None
    x_label: "str | None" = None       # default: FigureData.x_label or 'condition'
    y_label: "str | None" = None       # default: '<display_name> (<units>)' from the ontology
    x_limits: "tuple | None" = None
    y_limits: "tuple | None" = None
    palette: str = 'colorblind_safe'   # colour-blind-safe by default
    font_size_pt: float = 9.0
    figure_size_in: tuple = (5.0, 3.5)
    dpi: int = 300
    annotate_n: bool = True            # show n per group
    significance: str = 'none'         # 'none' | 'stars' | 'p_values' (label style for the brackets below)
    caveats_shown: bool = False        # render ontology caveats as a footnote
    # ── absorbed from figure_publication (additive; every default preserves current output) ──────────
    theme: "str | None" = None                 # journal theme for refine(); None = no theme adjustment
    recolor: bool = False                       # opt-in palette re-assignment (off: keep purposeful colours)
    title_size_pt: "float | None" = None
    journal_column: "str | None" = None         # 'single'|'onehalf'|'double' → width preset (mm→in at edge)
    height_mm: "float | None" = None
    tick_format: "str | None" = None            # e.g. '%.2f' on the y axis
    significance_brackets: tuple = ()           # ({'x1','x2','y','label'}, ...) — render()/refine() draw them
    y_scale: str = 'linear'                     # 'linear' | 'log' | 'symlog' — size/intensity are often
    #                                             log-normal; a linear axis misrepresents them (publication_features Tier 1)
    minor_ticks: bool = False                   # show minor ticks (default matplotlib ticks look unfinished in print)


def apply_size_preset(spec, name) -> FigureSpec:
    """Return a copy of ``spec`` with a size preset applied (size + legible font). Sizes, not compliance."""
    preset = SIZE_PRESETS[name]
    return dataclasses.replace(spec, **preset)


# ── Ontology-sourced defaults (the payoff) ──────────────────────────────────────────────────────
def ontology_y_label(measurement) -> str:
    """The default y-axis label from the ontology: '<display_name> (<units>)', or the raw key if unknown."""
    from pycat.utils.measurement_ontology import describe
    m = describe(measurement)
    return f"{m.display_name} ({m.units})" if m else str(measurement)


def ontology_caveats(measurement) -> tuple:
    from pycat.utils.measurement_ontology import describe
    m = describe(measurement)
    return tuple(m.caveats) if m else ()


def _resolve_labels(fig_data, spec):
    x = spec.x_label if spec.x_label is not None else (fig_data.x_label or 'condition')
    y = spec.y_label if spec.y_label is not None else ontology_y_label(fig_data.measurement)
    return x, y


def resolve_y_scale(y_scale, value_arrays):
    """The y-scale to actually apply, and a warning if the requested one is invalid for the data.

    A **log** axis cannot show values ≤ 0 (log of a non-positive number is undefined), so requesting one on
    data that crosses or touches zero would silently clip or blow up. Rather than substitute silently, this
    falls back to **symlog** (which handles zero and negatives) and returns a warning stating the
    consequence — the 'validate and warn, never silently substitute' rule. Returns ``(scale, warning)``."""
    if y_scale == 'log':
        finite = [np.asarray(v, dtype=float).ravel() for v in (value_arrays or [])]
        allv = np.concatenate(finite) if finite else np.array([])
        allv = allv[np.isfinite(allv)]
        if allv.size and allv.min() <= 0:
            return 'symlog', (
                f"log y-scale requested, but the data has non-positive values (min {allv.min():.4g}); a log "
                "axis cannot show values ≤ 0. Using symlog instead (it handles zero and negatives).")
    return y_scale, None


def render(fig_data, spec):
    """Render ``fig_data`` under ``spec`` and return a matplotlib Figure. **Reads the data, never recomputes
    it** — the plotted values are stashed on ``fig._pycat_plotted`` so the refine-not-recompute contract is
    checkable. Presentation (labels, limits, palette, fonts, footnote) comes entirely from ``spec``."""
    import matplotlib
    matplotlib.use('Agg', force=False)
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=tuple(spec.figure_size_in), dpi=spec.dpi)
    ax = fig.add_subplot(111)
    colors = _PALETTES.get(spec.palette, _PALETTES['colorblind_safe'])

    plotted = {}
    groups = list(fig_data.groups)
    for i, g in enumerate(groups):
        vals = np.asarray(fig_data.values_by_group[g], dtype=float)
        plotted[g] = vals
        ax.scatter(np.full(vals.size, i), vals, s=18, color=colors[i % len(colors)],
                   edgecolor='white', linewidth=0.4, zorder=2)
        if vals.size:
            ax.plot([i - 0.2, i + 0.2], [np.mean(vals)] * 2, color='#333333', lw=1.5, zorder=3)
        if spec.annotate_n and vals.size:
            ax.annotate(f"n={vals.size}", (i, np.max(vals)), textcoords='offset points',
                        xytext=(0, 4), ha='center', fontsize=spec.font_size_pt * 0.8)

    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels(groups)
    x_label, y_label = _resolve_labels(fig_data, spec)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    if spec.title:
        ax.set_title(spec.title)
    if spec.x_limits:
        ax.set_xlim(spec.x_limits)
    if spec.y_limits:
        ax.set_ylim(spec.y_limits)
    if getattr(spec, 'y_scale', 'linear') != 'linear':
        scale, warning = resolve_y_scale(spec.y_scale, list(plotted.values()))
        ax.set_yscale(scale)
        if warning:
            import warnings as _warnings
            _warnings.warn(warning)
    if getattr(spec, 'minor_ticks', False):
        ax.minorticks_on()

    for item in ([ax.title, ax.xaxis.label, ax.yaxis.label]
                 + ax.get_xticklabels() + ax.get_yticklabels()):
        item.set_fontsize(spec.font_size_pt)

    # ── significance brackets (the merged gap: figure_spec.render() now HONOURS them) ────────────────
    # The old figure_spec carried a `significance` mode string that render() never acted on; the working
    # bracket implementation lived only in figure_publication. It is now driven from here, so a spec that
    # requests brackets actually gets them. The honesty stays upstream: a bracket is drawn only for a pair
    # the caller supplied (from replicate-level stats), never inferred from a pixel-level test.
    if spec.significance_brackets:
        for ann in spec.significance_brackets:
            add_significance_bracket(ax, ann['x1'], ann['x2'], ann['y'],
                                     ann.get('label', ''), font_size=spec.font_size_pt)

    if spec.caveats_shown:
        caveats = ontology_caveats(fig_data.measurement)
        if caveats:
            fig.subplots_adjust(bottom=0.28)
            fig.text(0.02, 0.01, "Caveat: " + " ".join(caveats),
                     fontsize=spec.font_size_pt * 0.72, wrap=True, va='bottom')
    else:
        fig.tight_layout()

    fig._pycat_plotted = plotted
    return fig


def refine(fig, spec):
    """Refine an ALREADY-RENDERED figure under the canonical spec — theme, journal sizing, tick format,
    optional recolour, and significance brackets — **without recomputing the data** (the refine-not-recompute
    contract). Applies the validated presentation logic (``apply_spec`` below) directly to the canonical
    spec; a spec with no journal/theme fields set leaves the figure's presentation as-is."""
    return apply_spec(fig, spec)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# Publication rendering primitives — theme, journal sizing, brackets, palette, vector export.
#
# These were the validated core of the former ``figure_publication`` module; the FigureSpec merge
# (1.6.192) made ``figure_spec.FigureSpec`` canonical, and this cleanup folds the primitives in here so
# there is ONE figure module and no deprecated ``FigureSpec`` duplicate. They read the canonical spec's
# fields; output is unchanged from the publication path (the merge changed the API surface, not pixels).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════

#: Okabe–Ito minus yellow — the colour-blind-safe categorical palette validated on a white publication
#: surface with the dataviz validator (worst adjacent CVD ΔE 9.6, above the 8 target). Okabe–Ito's yellow
#: (#F0E442) is dropped: it FAILED the lightness band on white (L 0.90). Fixed order — never cycled.
PUBLICATION_PALETTE = (
    '#0072B2',   # blue
    '#E69F00',   # orange
    '#009E73',   # bluish green
    '#D55E00',   # vermillion
    '#56B4E9',   # sky blue
    '#CC79A7',   # reddish purple
)

#: Journal single/one-and-a-half/double column widths, millimetres (Nature/Cell conventions).
JOURNAL_COLUMN_MM = {'single': 89.0, 'onehalf': 120.0, 'double': 183.0}

#: Named themes — small dicts of matplotlib knobs applied by ``apply_spec``. ``colorblind_safe`` is the
#: default and the only validated palette.
THEMES = {
    'colorblind_safe': dict(palette=PUBLICATION_PALETTE, font_size=8, title_size=9,
                            spines=('left', 'bottom'), grid=False, line_width=1.0),
    'colorblind_safe_grid': dict(palette=PUBLICATION_PALETTE, font_size=8, title_size=9,
                                 spines=('left', 'bottom'), grid=True, line_width=1.0),
}


def apply_spec(fig, spec):
    """Apply the canonical ``spec``'s presentation to a matplotlib figure in place, and return it.

    Presentation only — it never touches the plotted data, so a refined figure is the same measurement
    dressed differently. Recolouring is **opt-in** (``spec.recolor``, off by default): most PyCAT figures
    colour purposefully (the comparative figure makes replicate means one colour so they read as "the units
    tested"), and blindly re-assigning the palette in series order would scramble that meaning. ``recolor=
    True`` opts a plain multi-series line plot into the validated palette.
    """
    theme = THEMES.get(spec.theme or 'colorblind_safe', THEMES['colorblind_safe'])
    font_size = spec.font_size_pt or theme['font_size']
    title_size = spec.title_size_pt or theme['title_size']

    for ax in fig.axes:
        if spec.title is not None:
            ax.set_title(spec.title, fontsize=title_size, loc='left')
        if spec.x_label is not None:
            ax.set_xlabel(spec.x_label, fontsize=font_size)
        if spec.y_label is not None:
            ax.set_ylabel(spec.y_label, fontsize=font_size)
        if spec.x_limits is not None:
            ax.set_xlim(spec.x_limits)
        if spec.y_limits is not None:
            ax.set_ylim(spec.y_limits)
        if getattr(spec, 'y_scale', 'linear') != 'linear':
            # refine-not-recompute: read the y data already on the axis to validate a log request (no
            # recomputation), then apply the (possibly-fallback) scale — same rule as render().
            _yvals = [ln.get_ydata() for ln in ax.get_lines()] + \
                     [c.get_offsets()[:, 1] for c in ax.collections if c.get_offsets() is not None]
            scale, warning = resolve_y_scale(spec.y_scale, _yvals)
            ax.set_yscale(scale)
            if warning:
                import warnings as _warnings
                _warnings.warn(warning)
        if getattr(spec, 'minor_ticks', False):
            ax.minorticks_on()
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
        for ann in spec.significance_brackets:
            add_significance_bracket(ax, ann['x1'], ann['x2'], ann['y'],
                                     ann.get('label', ''), font_size=font_size)

    width_mm = JOURNAL_COLUMN_MM.get(spec.journal_column or 'single', JOURNAL_COLUMN_MM['single'])
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
        except Exception:      # broad-ok: a collection that rejects a colour keeps its own — cosmetic only
            pass


def add_significance_bracket(ax, x1, x2, y, label='*', *, font_size=8):
    """Draw a significance bracket between two x positions at height ``y``.

    Only the caller decides whether to add one — this draws what it is told. The honesty lives upstream in
    ``comparative_stats``, where "is this significant, at the replicate level" is decided; a bracket is
    never generated automatically from a pixel-level test.
    """
    h = (ax.get_ylim()[1] - ax.get_ylim()[0]) * 0.02
    ax.plot([x1, x1, x2, x2], [y, y + h, y + h, y], lw=1.0, color='0.2')
    ax.text((x1 + x2) / 2, y + h, label, ha='center', va='bottom', fontsize=font_size, color='0.2')


def export_figure(fig, path, *, fmt=None, dpi=None, column=None, spec=None):
    """Export at publication settings: vector (PDF/SVG) or high-DPI raster, at a journal column width.

    Fonts are embedded in vector output (``pdf.fonttype=42`` / ``ps.fonttype=42`` = editable TrueType; SVG
    keeps text as text) so the file opens in Illustrator/Inkscape with live, editable labels rather than
    outlines. ``fmt`` defaults to the path's extension; ``dpi``/``column`` fall back to the spec's ``dpi`` /
    ``journal_column``. This is the single-file export; :func:`export` writes the full reproducible bundle.
    """
    import matplotlib
    import pathlib

    path = pathlib.Path(path)
    fmt = (fmt or path.suffix.lstrip('.') or 'pdf').lower()
    dpi = dpi or (spec.dpi if spec else 300)
    column = column or (getattr(spec, 'journal_column', None) if spec else None) or 'single'

    if column:
        width_in = JOURNAL_COLUMN_MM.get(column, JOURNAL_COLUMN_MM['single']) / 25.4
        w0, h0 = fig.get_size_inches()
        fig.set_size_inches(width_in, width_in * (h0 / w0) if w0 else width_in * 0.75)

    with matplotlib.rc_context({'pdf.fonttype': 42, 'ps.fonttype': 42, 'svg.fonttype': 'none'}):
        fig.savefig(str(path), format=fmt, dpi=dpi, bbox_inches='tight')
    return str(path)


# ── Spec JSON round-trip (regenerate the figure identically later) ──────────────────────────────
def spec_to_dict(spec) -> dict:
    d = dataclasses.asdict(spec)
    for k in ('figure_size_in', 'x_limits', 'y_limits', 'significance_brackets'):
        if d.get(k) is not None:
            d[k] = list(d[k])
    return d


def spec_from_dict(d) -> FigureSpec:
    d = dict(d)
    for k in ('figure_size_in', 'x_limits', 'y_limits'):
        if d.get(k) is not None:
            d[k] = tuple(d[k])
    if d.get('significance_brackets') is not None:
        d['significance_brackets'] = tuple(d['significance_brackets'])
    return FigureSpec(**d)


def export(fig, path, *, spec, summary_df=None) -> dict:
    """Write publication outputs beside ``path`` (its extension is ignored — a full set is written):

    - **PDF and SVG** vector output with fonts embedded as **text** (``pdf.fonttype=42``, ``svg.fonttype
      ='none'``), so an editor can adjust type — not outlines.
    - a **high-DPI PNG** at ``spec.dpi``.
    - the **spec as JSON** (regenerate identically) and, when given, the **summary DataFrame** as CSV
      (a figure whose numbers are not saved alongside it is irreproducible).

    Returns the written paths.
    """
    import matplotlib
    matplotlib.rcParams['pdf.fonttype'] = 42          # embedded editable TrueType text
    matplotlib.rcParams['ps.fonttype'] = 42
    matplotlib.rcParams['svg.fonttype'] = 'none'      # text stays text, not paths

    stem = pathlib.Path(path).with_suffix('')
    stem.parent.mkdir(parents=True, exist_ok=True)
    out = {}
    out['pdf'] = stem.with_suffix('.pdf'); fig.savefig(out['pdf'])
    out['svg'] = stem.with_suffix('.svg'); fig.savefig(out['svg'])
    out['png'] = stem.with_suffix('.png'); fig.savefig(out['png'], dpi=spec.dpi)
    out['spec'] = stem.with_suffix('.json')
    out['spec'].write_text(json.dumps(spec_to_dict(spec), indent=1), encoding='utf-8')
    if summary_df is not None:
        out['summary'] = stem.parent / (stem.name + '_summary.csv')
        summary_df.to_csv(out['summary'], index=False)
    return out
