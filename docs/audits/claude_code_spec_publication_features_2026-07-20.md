# Claude Code spec — General publication-figure features (the workstation gap)

> **◐ TIER 1 STARTED (axis controls shipped 1.6.262). Rest of Tier 1 + Tiers 2–3 remain.** The prerequisite
> (FigureSpec merge) landed in 1.6.192, so these attach to the canonical `figure_spec.FigureSpec`. Shipped
> the first Tier-1 slice — the **axis controls reviewers notice immediately**: `y_scale` (`'linear'` |
> `'log'` | `'symlog'`) honoured by `render()` and `refine()`, and `minor_ticks`. **Validate-and-warn, not
> silent:** a `log` request on data with non-positive values falls back to `symlog` with a `UserWarning`
> stating the consequence (pure `resolve_y_scale` helper), never a silent clip or crash. Both fields
> round-trip through JSON (scalar → auto via `asdict`) and work through `refine` without recomputing.
> Publication-sane default preserved (a bare spec renders linear, unchanged). `tests/test_publication_
> features.py` (`core`, Agg). Error/CI representation also DONE (1.6.263): `error_type`
> (`none`/`sd`/`sem`/`ci95`) draws an error bar on each group mean and LABELS the type on the figure (pure
> `group_error` helper; SD ddof=1 / SEM / 1.96·SEM). **Remaining Tier 1:** tick scientific-notation/exponent
> control, significance bracket-placement UI exposure. **Tier 2:** multi-panel +
> panel labels DONE (1.6.264): `render_multipanel(panels, *, spec, n_cols, panel_labels)` grids several
> `FigureData` with bold A/B/C… labels (per-panel spec override; per-axis plotting factored into a shared
> `_render_on_axis`, so single-panel `render` is unchanged). Validated fonts + transparent background DONE
> (1.6.265): `font_family` (validated against installed fonts — a missing one warns + falls back, pure
> `resolve_font_family`) and `transparent_background` (honoured by `export`'s savefig). Semantic colour +
> dense-scatter rasterization DONE (1.6.266): `color_map` ({group: colour} tied to identity not position) and
> `rasterize_points` (dense scatter → raster layer inside vector output). Export metadata DONE (1.6.267):
> `export` embeds the PyCAT + dependency versions in the file (PNG `Software` / PDF-SVG `Creator`) and a
> `_provenance` block in the spec JSON (`spec_from_dict` now tolerates it). Exact regeneration DONE (1.6.268):
> `render` stashes the raw plotted data, `export` writes `<name>_data.json`, and `regenerate(data, spec)`
> reconstructs the exact figure (beyond the summary CSV). **Remaining Tier 2:** legend control. **Remaining
> Tier 3 (the last, largest, least headless-verifiable):**
> metadata, exact regeneration, image panels/scale bars. Ship each as its own version.

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. The brushing
audit's §8: the figure system is strong for simple grouped scatter/comparison plots but is *"not yet a
complete general publication-figure workstation."* This spec fills the missing controls. **It must land
after the FigureSpec merge** — these features attach to the canonical model, and building them against
two specs would double the work and the bugs.

## Prerequisite
The two `FigureSpec` implementations must be merged first (companion spec). These features extend the
canonical `FigureSpec`'s sub-specs (`layout`, `axes`, `marks`, `annotations`, `export`). Verified: the
current render path is single-axis (`figure_spec.py:107` — `fig.add_subplot(111)`), so multi-panel is
genuinely absent.

## The missing controls, grouped by sub-spec
Implement in priority tiers; each tier ships independently so value lands incrementally.

### Tier 1 — the ones reviewers notice immediately
- **Log / symlog scales** (`AxesSpec.scale`) — condensate size and intensity distributions are often
  log-normal; a linear axis misrepresents them. Include symlog for data crossing zero.
- **Major/minor tick control + scientific notation** with exponent positioning — currently absent;
  default matplotlib ticks look unfinished in print.
- **Consistent significance annotation** — the merge wires one implementation; this exposes bracket
  placement, comparison pairs, and stars-vs-p-values through the UI. (The audit notes the two modules
  disagree today; the merge fixes the model, this exposes the controls.)
- **Error / confidence representation** (`MarkSpec`) — error bars, CIs, SEM/SD choice with the choice
  labelled. A comparison figure without stated error is not publishable.

### Tier 2 — layout and legibility
- **Multi-panel layout + panel labels (A, B, C)** — the single-axis render becomes a grid; panel labels
  are the standard figure requirement. This is the biggest structural change (touches `LayoutSpec` and
  the render loop).
- **Legend placement/formatting** — position, columns, frame on/off.
- **Font family selection with availability validation** — offer families, but **validate the font is
  installed and warn/fall back** rather than silently substituting (a silent substitution changes the
  figure between machines).
- **Transparent vs white background** (`ExportSpec`).

### Tier 3 — polish and fidelity
- **Line/marker/error-bar specification** (`MarkSpec` detail).
- **Rasterize dense scatter inside vector output** — a 50k-point scatter as vector is a huge unusable
  PDF; rasterize the points layer while keeping axes/text vector. Important and specific.
- **Semantic colour mapping** — colour tied to group identity consistently across figures (a condition
  keeps its colour everywhere).
- **Arbitrary annotation/callout placement.**
- **Export metadata** — software/version + provenance embedded in the file metadata.
- **Exact regeneration from raw plotted data** (not only summary) — store enough to reproduce the exact
  figure, strengthening reproducibility beyond the summary CSV.
- **Image panels + scale bars + microscopy overlays** — for figures that combine a micrograph with
  plots (the biggest new surface; can be its own follow-on).

## Design discipline
- **Every feature is a field on a sub-spec + handling in `render`.** No feature bypasses the spec; the
  spec must remain the complete, serializable description of the figure (so `refine` and JSON
  round-trip keep working).
- **Defaults must be publication-sane** — a user who sets nothing still gets a clean figure. Features
  are opt-in refinements, not required knobs.
- **Validate and warn, don't silently substitute** — missing font, impossible axis limit, a log scale
  on data with zeros: warn with the consequence, fall back predictably.

## Tests (`core`, matplotlib Agg)
- Each Tier-1 feature: setting the field changes the rendered figure as specified (log scale actually
  log; error bars present with the stated type; significance brackets on the right pairs).
- Multi-panel: a 2×2 spec produces four panels with correct labels; a single-panel spec is unchanged.
- Font validation: an unavailable font warns and falls back deterministically (not silently).
- Rasterized dense scatter: the vector output embeds a raster points layer but vector axes/text (assert
  the artist rasterization flag).
- Spec round-trips through JSON with every new field.
- `refine` applies any new field without recomputing analysis (the retained contract).
- Export metadata contains the PyCAT version and provenance.

## Steps
1. (After the merge) Tier 1 fields + render handling; tests.
2. Tier 2 (multi-panel is the large one) + tests.
3. Tier 3 polish + tests, with image-panels/scale-bars as an optional final follow-on.
4. Full `pytest -m core` green after each tier.
5. Ship each tier as its own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- Log/symlog scales, tick control, consistent significance, and error representation exist (Tier 1).
- Multi-panel layout with labels, legend control, validated fonts, background choice (Tier 2).
- Dense-scatter rasterization, semantic colour, callouts, export metadata, exact-regeneration, and
  (optionally) image panels/scale bars (Tier 3).
- Every feature is a spec field honoured by `render`, round-trips through JSON, and works with `refine`.
- Full `pytest -m core` green.

## Cautions
- **Merge the two FigureSpecs first** — building these against two models doubles the work and the bugs.
- **Every feature goes through the spec** — nothing bypasses it, or `refine`/JSON round-trip breaks.
- **Validate and warn, never silently substitute** — a silently swapped font or clipped axis changes
  the figure invisibly between machines.
- Publication-sane defaults — features are opt-in; a bare spec still yields a clean figure.
- Ship in tiers; a single mega-commit adding a dozen controls is un-reviewable and un-bisectable.
