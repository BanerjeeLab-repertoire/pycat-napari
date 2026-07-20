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
    """The declarative presentation spec. Mutating this and re-rendering never recomputes the data."""
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
    significance: str = 'none'         # 'none' | 'stars' | 'p_values'
    caveats_shown: bool = False        # render ontology caveats as a footnote


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

    for item in ([ax.title, ax.xaxis.label, ax.yaxis.label]
                 + ax.get_xticklabels() + ax.get_yticklabels()):
        item.set_fontsize(spec.font_size_pt)

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


# ── Spec JSON round-trip (regenerate the figure identically later) ──────────────────────────────
def spec_to_dict(spec) -> dict:
    d = dataclasses.asdict(spec)
    for k in ('figure_size_in', 'x_limits', 'y_limits'):
        if d.get(k) is not None:
            d[k] = list(d[k])
    return d


def spec_from_dict(d) -> FigureSpec:
    d = dict(d)
    for k in ('figure_size_in', 'x_limits', 'y_limits'):
        if d.get(k) is not None:
            d[k] = tuple(d[k])
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
