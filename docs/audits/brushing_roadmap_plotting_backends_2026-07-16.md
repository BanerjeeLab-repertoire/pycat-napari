# Brushing arc — roadmap addendum: plotting backends & the object-model-first thesis

**Date:** 2026-07-16 · Companion to `claude_code_spec_brushing{1..5}`. Folds in a plotting-library
brainstorm (PyQtGraph vs Plotly vs matplotlib; object-registry + event-bus). **This is a roadmap note,
not a spec** — it records where the brainstorm CONFIRMS the arc already in flight, the ONE new idea
worth queuing, and an explicit warning against the redesign it frames.

## The brainstorm's core thesis is already the arc
The brainstorm's central claim — *"the plotting library is mainly responsible for rendering and
hit-testing; the real innovation is the object model and event system that tie every view back to the
same biological entity"* — is exactly what brushing 2–5 build. Mapping:

| brainstorm concept | already specced as |
|---|---|
| stable `object_id` every view shares | increment 2 — `EntityKey`/`EntityRef` on the tag seam |
| `SelectionChanged(object_id)` event bus, no searching/recompute | increment 3 — `SelectionService` (promoted from VPT's dispatcher, via the `_data_switch_callbacks` idiom) |
| "select a cell → outline glows, row scrolls, point enlarges, trajectory thickens" | increment 5 — linked-selection dock + overlay highlight + brush-aware table |
| plugin-style brushing API so future viz (UMAP, kymographs, network graphs) participates for free | the natural consequence of 2+3 — identity + a service anything subscribes to |

So the brainstorm is **independent validation** of the direction, not a new one. Nothing in the chain
changes because of it. Worth keeping as external corroboration for the manuscript's "synchronized
views of one biological object" framing.

## The one genuinely new idea: PyQtGraph as the INTERACTIVE backend
PyCAT already has a **three-backend plotting abstraction** (`utils/plot_backends.py`:
matplotlib / seaborn / plotly, one `scatter()` interface, the same brushing, with `_verify_row_order`
guarding artist↔row correspondence). The brainstorm didn't know this — and it actually argues AGAINST
the plotly path already in the tree (browser runtime, Qt→WebEngine→JS→callback plumbing, packaging
weight) and FOR **PyQtGraph** on grounds that fit PyCAT's architecture better than plotly does:
- everything is already Qt; selections are native Qt signals (no JS bridge);
- same event loop as napari (napari is Qt) — low latency, no cross-runtime sync;
- fast scatter for very large N (the increment-4 scaling concern);
- easy custom interactions (crosshairs, ROIs, linked views).

This is a real insight: for the INTERACTIVE exploration panels, a native-Qt backend is a cleaner fit
than the browser-based plotly already integrated. The honest division the brainstorm proposes —
**PyQtGraph for Explore, matplotlib for Export** — matches PyCAT's actual split (interactive analysis
vs publication figures).

### How it would slot in (future, AFTER the chain)
PyQtGraph becomes a **fourth backend in the existing `plot_backends` abstraction**, not a new plotting
system:
- add `'pyqtgraph'` to `BACKENDS`; implement `scatter(..., backend='pyqtgraph')` returning a Qt
  widget whose points map 1:1 to rows (the same `_verify_row_order` contract);
- its selection signal emits into the increment-3 `SelectionService` — so it participates in linked
  brushing for free, exactly like the plugin-style API the brainstorm wants;
- matplotlib stays the export/publication backend; plotly stays as-is (already integrated) for anyone
  who wants browser interactivity.
Because increments 2–3 make identity + selection backend-agnostic, adding PyQtGraph is an ADDITIVE
backend, not a rewrite — the whole point of having built the object model first.

## Sequencing — explicitly AFTER the chain, not now
The brainstorm frames this as *"if I were redesigning PyCAT today."* PyCAT is NOT being redesigned —
it's mid-build on an incremental chain that works. Do NOT introduce PyQtGraph now:
- it would collide with brushing 1–5 (which touch `brushing.py`/`object_ref.py`/`plot_backends`);
- the value of a native-Qt interactive backend is only REALIZED once the SelectionService (inc 3) and
  the scaling fixes (inc 4) exist — a fast backend with no shared identity is just a faster
  disconnected plot.
So: **PyQtGraph backend = a post-increment-5 candidate.** When the chain has landed, it becomes a
clean, well-scoped spec ("add a 4th plot backend that emits into the existing SelectionService"),
and by then the abstraction it plugs into is proven.

## What NOT to take from the brainstorm
- **Not a plotting-architecture switch.** The object model is the innovation (already being built);
  the backend is swappable and already abstracted. Don't let "PyQtGraph is the strongest candidate"
  become "rewrite the plotting layer."
- **Not a reason to rip out plotly.** It's already integrated honestly as the browser-bridge backend;
  it stays as an option. PyQtGraph is ADDITIVE.
- **Not a new registry.** The brainstorm's "ObjectRegistry" is the increment-2 identity model + the
  increment-3 service — don't build a parallel third thing; that would reintroduce exactly the
  five-parallel-registries tax the engineering audit flagged.

## Bottom line
The brainstorm is strong external validation that the brushing arc is aimed correctly, plus one
concrete future addition (PyQtGraph as a native-Qt interactive backend inside the existing
`plot_backends` abstraction, emitting into the increment-3 SelectionService). Queue it as a
**post-increment-5 backend spec**, keep matplotlib for export and plotly as the existing browser
option, and do not treat it as a redesign of a plotting layer that is already, deliberately, an
abstraction.
