# Claude Code spec — Merge the two `FigureSpec` systems

> **✅ STATUS — COMPLETE. Merge DONE (1.6.192); consumer migration + shim removal DONE (1.6.203).**
> The consolidation follow-on is finished: the deprecated `figure_publication.FigureSpec` class is gone,
> and its validated rendering primitives (`apply_spec`, `add_significance_bracket`, `export_figure`,
> `PUBLICATION_PALETTE`, `JOURNAL_COLUMN_MM`, `THEMES`, `_recolor_series`) are folded into the canonical
> `figure_spec.py`, reading the canonical spec's field names directly. `figure_spec.refine()` now calls
> `apply_spec(fig, spec)` on the canonical spec (no field-name mapping through a deprecated shim). The sole
> external consumer (`plot_backend_pyqtgraph`'s `PUBLICATION_PALETTE` import) is repointed; the
> comparative-figures UI never referenced it. `figure_publication.py` is DELETED. Tests migrated:
> `test_figure_publication.py` → `test_figure_spec_primitives.py` (repointed to the canonical spec; the
> deprecated class's redundant JSON-serialization tests dropped — the canonical round-trip is covered in
> `test_figure_spec.py`), and `test_figurespec_merge.py`'s shim tests replaced with direct assertions.
> Full `pytest -m core` green; output is byte-equivalent (the merge changed the API surface, not pixels).
> This unblocks `publication_features` and `explore_refine_export` (not yet written).
>
> _Merge (1.6.192):_ `figure_spec.FigureSpec`
> is now the canonical spec, absorbing `figure_publication`'s fields (column, height_mm, theme, recolor,
> tick_format, significance_brackets, title_size) as additive, off-by-default options — so existing figures
> render pixel-identically. `figure_spec.render()` now HONOURS significance (the verified gap); new
> `figure_spec.refine()` applies the journal/theme/bracket refinements by REUSING the validated
> `figure_publication.apply_spec` (output byte-for-byte unchanged). `figure_publication.FigureSpec` is marked
> deprecated but fully functional. `tests/test_figurespec_merge.py` pins the union of capabilities,
> unchanged default render, render-honours-brackets, refine==apply_spec, JSON round-trip, and the shim;
> `test_figure_spec` / `test_figure_publication` pass unmodified. **Remaining (follow-on):** migrate the two
> consumers (`plot_backend_pyqtgraph`, the comparative-figures UI) off the deprecated shim onto the canonical
> spec, then remove the shim — nothing breaks meanwhile. This unblocks `publication_features` and
> `explore_refine_export`, which attach to the canonical model.

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. The brushing/plot
audit's largest design issue, and confirmed: **two distinct `FigureSpec` implementations exist** —
`utils/figure_spec.py` and `utils/figure_publication.py` — with overlapping-but-different fields. A
feature added to one is absent from the other, and no module knows which to use. This spec unifies them
behind one canonical model without losing either's capabilities.

## Verified state
- `utils/figure_spec.py::FigureSpec` — `FigureData`, palette, size in inches, ontology labels, caveat
  footnotes, annotate-n, significance mode, multi-file export.
- `utils/figure_publication.py::FigureSpec` — journal column widths, height in mm, theme, recoloring,
  tick formatting, significance brackets.

Both shipped from separate specs (the publication-figure spec and comparative phenotyping) that did not
know the other would grow a `FigureSpec`. Consumers are thin — a manageable merge, not a rewrite of
callers.

## The canonical model
Compose the union as sub-specs so nothing is lost and future additions have an obvious home:
```python
@dataclass(frozen=True)
class FigureSpec:
    layout: LayoutSpec        # size (ONE unit internally), panels, dpi
    typography: TypographySpec# font family/size, embedded-text export
    axes: AxesSpec            # labels (ontology-sourced), limits, scale (lin/log/symlog), ticks
    palette: PaletteSpec      # colourblind-safe default, semantic group→colour
    marks: MarkSpec           # marker/line/error-bar, point size/opacity, replicate-mean styling
    annotations: AnnotationSpec # significance (ONE implementation), caveats, callouts, annotate-n
    export: ExportSpec        # PDF/SVG/PNG, dpi, bundle (spec JSON + summary CSV)
```
One rendering contract:
```python
render(figure_data: FigureData, spec: FigureSpec) -> Figure
refine(existing_figure, spec: FigureSpec) -> Figure    # never recomputes analysis
export(figure, spec, bundle) -> None
```

### Resolve the concrete conflicts the audit named
1. **Units: inches vs mm.** Pick ONE internal unit (inches, matplotlib-native), and accept mm at the
   API boundary with conversion. Store one; convert at the edge. Journal width presets map to the
   internal unit.
2. **Significance: mode string vs brackets.** `figure_spec` has `significance: 'none'|'stars'|'p_values'`
   that `render()` **does not act on** (verified gap); `figure_publication` has working brackets.
   Keep the working bracket implementation, drive it from the `AnnotationSpec.significance` field, and
   make `render()` honour it. One implementation, wired.
3. **Labels: ontology-sourced vs manual.** Keep ontology-derived defaults (from `figure_spec`) with
   manual override — the richer behaviour wins.
4. **Export: single vs multi-file bundle.** Keep the bundle (spec JSON + summary CSV alongside the
   figure) — the reproducibility-stronger behaviour wins.

## Migration — behaviour-preserving, one consumer at a time
1. Build the canonical `FigureSpec` in `figure_spec.py` (the ontology-aware module), composing the
   sub-specs and absorbing `figure_publication`'s journal/theme/bracket capabilities.
2. Keep `figure_publication.FigureSpec` as a **thin deprecated shim** that constructs the canonical one,
   so nothing breaks on day one. Mark it deprecated in the docstring.
3. Migrate each consumer to the canonical model, one commit each, running the figure tests between.
   (Verified consumers are few — `plot_backend_pyqtgraph` references one; comparative-figures UI uses
   the other.)
4. Once all consumers are migrated, remove the shim in a final commit.

**Do not** change any figure's visual output during the merge — this is consolidation, not restyling.
A rendered figure before and after must be pixel-equivalent for the same inputs (assert via a
structural comparison, or a hash of the serialized artists, on a synthetic figure).

## Tests (`core`, matplotlib Agg)
- The canonical `FigureSpec` round-trips through JSON unchanged.
- Every capability from BOTH old specs is exercised: journal width preset, mm input, theme, tick
  formatting, significance brackets, ontology labels, caveat footnote, annotate-n, bundle export.
- **Significance is now honoured by `render()`** — a spec requesting brackets produces them (the wired
  gap).
- **Equivalence:** a figure built via the deprecated shim renders identically to one built via the
  canonical spec (same inputs → same output).
- `refine()` changes presentation without recomputing (the existing no-recompute contract, retained).
- Export bundle writes figure + spec JSON + summary CSV.

## Steps
1. Canonical `FigureSpec` (composed sub-specs) in `figure_spec.py`, absorbing publication capabilities.
2. `render`/`refine`/`export` honour every field, including significance.
3. `figure_publication.FigureSpec` → deprecated shim constructing the canonical one.
4. Migrate consumers one commit each; figure tests green between.
5. Remove the shim.
6. Full `pytest -m core` green.
7. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (two FigureSpecs merged;
   significance now rendered; no visual change).

## Definition of done
- One canonical `FigureSpec` carries every capability both old specs had.
- `render()` acts on significance (the verified gap closed).
- The deprecated shim keeps callers working, then is removed after migration.
- No figure's visual output changes for the same inputs (equivalence test).
- Full `pytest -m core` green.

## Cautions
- **No restyling during the merge.** Consolidation only; a visual change hides whether the merge
  preserved behaviour. Assert equivalence.
- Pick one internal unit; convert mm at the boundary — do not carry two unit systems forward.
- Keep the richer behaviour on every conflict (ontology labels, bundle export, working brackets).
- Migrate one consumer per commit behind the shim; a big-bang swap is un-bisectable.
- Do not expand into the missing publication features (multi-panel, log scale, etc.) here — that is
  section 8 of the audit and a separate spec. This is the merge only.
