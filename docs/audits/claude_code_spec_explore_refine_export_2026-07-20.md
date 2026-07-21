# Claude Code spec — Comparative figure UX: one Explore → Refine → Export workflow

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. The brushing
audit's §9: the comparative-figure dialog is scientifically thoughtful but behaves like a *plotting
dialog*, not a *publication editor* — the publication utilities exist but are not exposed through one
visible refinement workflow. This spec wires them together.

## The gap (verified)
The comparative-figure path (replicate-aware, unit selection, object-point brushing, light-points/
dark-means styling) is strong analytically. But the publication utilities (FigureSpec refinement,
size presets, palettes, bundle export) are not reachable from it as a workflow — a user explores, then
has no in-place path to refine to final size and export a bundle. The audit's desired flow:
```
Explore → Refine → Preview at final physical size → Export figure bundle
```
does not exist as a connected experience.

## Prerequisite
Best built **after the FigureSpec merge** (so refinement targets one canonical spec) and it pairs with
the publication-features spec (the controls it exposes). It can start against the current spec and gain
controls as those land, but note the dependency.

## Design — one panel, three modes over the same figure
The comparative-figure dialog gains a refinement panel that operates on the **canonical `FigureSpec`**
for the current figure — never recomputing the analysis (the existing contract).

### Explore (the current behaviour, kept)
The analytical view: object points, replicate means, replicate-level testing, unit selection, brushing.
This is where the science happens and it stays exactly as is.

### Refine (the new surface)
A visible panel exposing the spec fields the audit lists:
- size preset (single/1.5/double column) with **live preview at final physical size** — the user sees
  the figure at the width it will print, where font sizes and point density actually matter;
- axis labels (ontology-defaulted) and limits;
- palette (colourblind-safe default) + point size/opacity;
- replicate-mean styling (the dark-means emphasis is a control, not a hardcode);
- annotation selection (significance brackets, n, caveats);
- everything mutates the `FigureSpec` and re-renders — **no recompute**.

### Export (the bundle)
One action produces the figure bundle: vector (PDF/SVG, embedded text) + high-DPI PNG + the spec JSON
+ the summary CSV. The audit's "export figure bundle" as a single button.

## The workflow contract
- **The three modes are views of one figure**, not three separate figures. Refining does not re-run the
  comparison; exporting captures exactly what the preview shows. The preview at final physical size IS
  the exported figure — WYSIWYG, or the refinement is pointless.
- Brushing survives into refine mode — a user can still click an object point while refining, so
  identity/selection is not lost when moving from explore to polish.
- The spec is saved with the bundle, so reopening restores the refined state (reproducible figure).

## Scope discipline
- This is **UX wiring over existing utilities**, not new figure capability — the capability is the
  publication-features spec. Where a control's backing feature hasn't landed yet, grey it out with a
  note rather than faking it.
- Keep the analytical Explore mode untouched; this adds Refine/Export around it, it does not rebuild it.

## Tests (`core` where possible; Qt-smoke for the panel)
- Refining a spec field re-renders without recomputing the comparison (assert the underlying summary
  data is unchanged — the retained no-recompute contract).
- **WYSIWYG:** the exported figure matches the final-size preview (same spec → same output).
- The export bundle contains figure + spec JSON + summary CSV.
- Reopening a saved bundle restores the refined spec state.
- Brushing works in both explore and refine modes (selection survives the mode switch).
- A control whose backing feature is absent is disabled with a note, not silently broken.

## Steps
1. Add the refinement panel to the comparative-figure dialog, bound to the canonical `FigureSpec`.
2. Live preview at final physical size (the size preset drives an accurate on-screen scale).
3. Wire the export-bundle action (vector + PNG + spec JSON + summary CSV).
4. Preserve brushing across explore/refine; save/restore the spec with the bundle.
5. Grey out controls whose backing features are not yet available.
6. Tests above.
7. Full `pytest -m core` green.
8. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (comparative figures now have a
   visible Explore→Refine→Export workflow).

## Definition of done
- The comparative-figure dialog exposes Explore, Refine (spec controls + final-size preview), and
  Export (bundle) over one figure.
- Refinement never recomputes; export is WYSIWYG against the preview.
- Brushing survives the mode switch; the refined spec saves and restores.
- Controls without backing features are disabled honestly.
- Full `pytest -m core` green.

## Cautions
- **WYSIWYG or bust** — if the export does not match the final-size preview, refinement is theatre. Same
  spec must produce the same figure in preview and export.
- **Never recompute on refine** — the analysis is done; refinement is presentation only (the existing
  contract).
- This is wiring, not new capability — grey out controls whose features (publication-features spec)
  haven't landed; do not fake them.
- Do not touch the analytical Explore mode's science — add around it.
- Best sequenced after the FigureSpec merge; starting earlier means re-targeting the panel later.
