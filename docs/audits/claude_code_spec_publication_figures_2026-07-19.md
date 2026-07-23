# Claude Code spec — Comparative phenotyping increment 4: publication figure refinement

> **✅ STATUS — DONE at the module level (verified 1.6.270); the refinement UI panel is the
> `explore_refine_export` follow-on.** This spec is the ORIGIN of the `FigureSpec` architecture, and it
> shipped through `figurespec_merge` (1.6.192) + the `publication_features` tiers (1.6.262–269). Re-verified
> every Definition-of-Done item against the current tree: `FigureSpec` + `render` + `export` live in the
> Qt-free `utils/figure_spec.py`; axis labels/units default from the measurement ontology with a caveats
> footnote; size presets (single/1.5/double column, honestly labelled "sizes not compliance") and the
> colour-blind-safe palette exist; `export` writes vector PDF/SVG with **embedded text fonts**
> (`pdf.fonttype=42`, `svg.fonttype='none'`), a high-DPI PNG, the spec JSON (regenerates identically), and
> the summary CSV alongside; and refinement re-renders but **never recomputes** (the contract). All seven
> DoD contract tests pass (`test_figure_spec.py`), and `test_publication_figures.py` (new) pins the headline
> against a REAL `condition_comparison_figure`: it refines (title/log scale) with every plotted datum
> unchanged, and exports the full reproducible bundle. **Only remaining:** step 5 — the in-UI refinement
> panel (spec fields as controls + live preview + export) — which is exactly the `explore_refine_export`
> spec's Explore→Refine→Export workflow, tracked there.

**Date:** 2026-07-19 · **Target tree:** 1.6.156 · Verified against the 1.6.156 tree. The last
increment of the comparative-phenotyping arc: take any PyCAT figure and refine it to publication
quality **without re-running the analysis**. Prerequisites all landed — increment 1
(`sample_metadata`), 2 (`consolidated_table`), 3 (`comparative_figures`), and the measurement
ontology (1.6.154).

## The gap (verified)
`utils/comparative_figures.py` produces `condition_comparison_figure`, `dose_response_figure`, and the
replicate-honest aggregation behind them. Verified: **there is no `savefig`, no `dpi=`, and no export
function anywhere in the comparative-figures module or its UI.** A figure can be produced but not
prepared for a journal.

Today, adjusting an axis label or a colour means re-running the analysis. That is both slow and
scientifically wasteful — the numbers are already correct; only presentation needs work.

## Design — separate the figure SPEC from the figure
The core idea: a figure holds its data plus a **declarative spec**, so refinement mutates the spec and
re-renders, never recomputes.

```python
@dataclass
class FigureSpec:
    title: str | None
    x_label: str | None            # default: from the measurement ontology
    y_label: str | None            # default: '<display_name> (<units>)' from the ontology
    x_limits: tuple | None
    y_limits: tuple | None
    palette: str                   # colour-blind-safe by default
    font_size_pt: float
    figure_size_in: tuple[float, float]
    dpi: int
    annotate_n: bool               # show n per group
    significance: str              # 'none' | 'stars' | 'p_values'
    caveats_shown: bool            # render ontology caveats as a footnote

def render(fig_data, spec) -> Figure
def export(fig, path, *, spec) -> None    # vector + raster
```

### The ontology supplies the defaults (this is the payoff)
The measurement ontology (1.6.154) already holds `display_name`, `units`, `interpretation`, and
`caveats`. So:
- **y-axis label defaults to `"Partition coefficient (dimensionless)"`** — correct units, no typing;
- **caveats render as a figure footnote** when `caveats_shown` — e.g. the 2D-projection-proxy warning
  travels onto the figure instead of being lost between the analysis and the paper.
This is exactly what the ontology was seeded for; wire it as the default source, overridable per figure.

## Journal presets, honestly scoped
Provide a small set of size presets (single column ≈ 3.5 in, 1.5 column ≈ 5 in, double column ≈ 7 in)
with font sizes that remain legible at those widths. **Do not claim journal-specific compliance** —
requirements change and vary; call them size presets, not "Nature format". A tool that promises
compliance it cannot verify is worse than one that offers sensible defaults.

## Export requirements
- **Vector (PDF/SVG) as the primary output**, with fonts embedded as text (not outlines) so editors
  can adjust type — set the matplotlib `pdf.fonttype`/`svg.fonttype` accordingly.
- High-DPI PNG (≥300, configurable) for drafts and slides.
- Export **both the figure and its summary DataFrame** side by side (increment 3 already returns
  `(Figure, summary_df)`). A published figure whose numbers are not saved alongside it is
  irreproducible — this is a small step that materially strengthens the reproducibility story.
- Write the `FigureSpec` next to the outputs as JSON, so a figure can be **regenerated identically**
  later. That is the difference between "I made this figure once" and "this figure is reproducible."

## The refinement UI
A modest panel: the spec fields as controls, a live preview, and export. Re-render on change —
**never recompute**. If re-rendering proves slow for large object counts, cache the plotted arrays;
do not silently downsample (that would change what the figure shows).

## Tests (`core`, matplotlib Agg — no Qt)
- A `FigureSpec` round-trips through JSON unchanged.
- Ontology defaults populate axis labels and units for a known measurement.
- Caveats appear as a footnote when enabled, and the text matches the ontology entry.
- Export produces a PDF/SVG with **text-based fonts** (assert `fonttype` is set to 42/none, not
  outlines) and a PNG at the requested DPI and size.
- The summary DataFrame is written alongside the figure.
- **Refinement does not recompute:** changing the spec and re-rendering produces identical underlying
  data (assert the plotted values are unchanged), only presentation differs. This is the contract test.

## Steps
1. `FigureSpec` + `render` + `export` in `utils/figure_spec.py` (Qt-free).
2. Ontology-sourced defaults for labels, units, and caveats.
3. Size presets + colour-blind-safe palettes.
4. Vector/raster export with embedded text fonts; write spec JSON + summary CSV alongside.
5. Refinement panel in the comparative-figures UI.
6. Tests above.
7. Full `pytest -m core` green.
8. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- Any comparative figure can be refined (labels, limits, palette, fonts, size, DPI) and exported
  without re-running the analysis.
- Axis labels and units default from the measurement ontology; caveats can render as a footnote.
- Export produces vector output with embedded text fonts, plus the summary data and a spec JSON that
  regenerates the figure.
- Full `pytest -m core` green.

## Cautions
- **Refine, never recompute.** The contract test exists because a refinement path that silently
  re-runs analysis could change the numbers a user already believes.
- Do not claim journal compliance — offer size presets and say so.
- Embed fonts as text, not outlines; editors need to adjust type.
- Never silently downsample to make rendering fast — that changes what the figure asserts.
- Do not build the Measurement Reliability Index here; it is a separate construct.
