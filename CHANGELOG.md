## [1.6.98] - 2026-07-17
### Added — **Comparative phenotyping increment 4: publication figure refinement.**
The polish layer. `utils/figure_publication.py` refines ANY PyCAT matplotlib figure to
publication quality without re-running the analysis — a figure holds its data, an editable `FigureSpec`
holds the presentation over it (title, labels, limits, ticks, theme, fonts, journal-column sizing,
significance brackets). matplotlib stays the publication backend.

- **`FigureSpec`** round-trips through a dict and versioned JSON — a spec is a set of overrides, so an
  empty spec is a no-op and a caller changes only what they name. `apply_spec` applies it **presentation
  only**; a test asserts the plotted data never moves.
- **Export** at publication settings: vector PDF/SVG + high-DPI PNG, sized to journal column widths
  (single 89 mm / one-and-a-half 120 mm / double 183 mm), fonts embedded editable (`pdf.fonttype=42`;
  SVG keeps text as text, not outlined paths). Tested: the requested format is produced, DPI scales the
  raster resolution, and the column width is honoured.
- **The colour-blind-safe palette is computed, not chosen by eye.** `PUBLICATION_PALETTE` is Okabe-Ito
  **minus its yellow**, validated with the dataviz validator on a white surface: worst adjacent CVD
  ΔE 9.6 (deuteranopia, above the 8 target), normal-vision ΔE 20. The validator *caught* Okabe-Ito's
  yellow failing the lightness band on white (L 0.90) — a legibility problem "it's the standard palette"
  would have shipped. A self-contained OKLab-lightness test guards against re-introducing exactly that.
### Notes
- **Recolouring is opt-in (`recolor=False` default), a correction rendering surfaced.** A blanket
  retheme repainted the comparative figure's *intentional* colours (the replicate means are one colour
  so they read as "the units tested"). A refine pass now adjusts fonts/spines/size/labels without
  hijacking colour meaning; `recolor=True` opts a plain multi-series plot into the palette. Verified by
  rendering the increment-3 figure through the refine pass and confirming its colours survive.
- Significance brackets draw what they are told; the honesty (is this significant *at the replicate
  level*) lives in `comparative_stats` — a bracket is never auto-generated from a pixel-level test.
- **The refinement UI is deferred** — it needs a viewer. The spec + export core ships and is usable
  today; the UI sits over it later. This completes the comparative-phenotyping arc's headless-buildable
  increments (1-4); the interactive layers (brushing, PyQtGraph, the batch wiring, the refine UI) await
  a viewer session.

