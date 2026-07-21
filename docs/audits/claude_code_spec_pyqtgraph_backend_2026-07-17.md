# Claude Code spec — PyQtGraph interactive plot backend

> **✅ STATUS — DONE, shipped in 1.6.122** (present in CHANGELOG; retrofit follow-up 1.6.123).
> `src/pycat/utils/plot_backend_pyqtgraph.py` provides `pyqtgraph_scatter` (a `PlotWidget` with the shared
> row-order guard, O(1) overlay-artist highlight under a `ProgrammaticGuard`, and `sigClicked` →
> `service.select(Selection(...))` into the SelectionService with echo-suppression, as a proper
> `SelectionView`). Registered as the 4th backend in `plot_backends.py`
> (`BACKENDS = ('matplotlib','seaborn','plotly','pyqtgraph')`) with a lazy-guarded branch and a graceful
> install message when absent. Additive, optional (`pip install pycat-napari[pyqtgraph]`); matplotlib stays
> default. Pinned by `tests/test_plot_backend_pyqtgraph.py`. Every Definition-of-done item met.

**Date:** 2026-07-17 · **Target tree:** 1.6.90 · Verified against the 1.6.90 tree. Adds PyQtGraph as a
FOURTH plot backend in the existing `plot_backends` abstraction — a native-Qt interactive scatter that
emits into the brushing `SelectionService`. Per the plotting-backend addendum
(`brushing_roadmap_plotting_backends_2026-07-16.md`): this is ADDITIVE, not a plotting-architecture
switch; matplotlib stays for export, plotly stays as-is. **PREREQUISITES:**
1. The brushing arc (inc 1–5) — landed (`SelectionService`, `make_pickable`, `EntityRef` all present).
2. **`claude_code_spec_interaction_layer_2026-07-17.md` MUST land FIRST.** That spec introduces the
   `SelectionView` adapter protocol, the programmatic-update guard, the `SelectionState`
   (hover/selected/pinned) model, and the shared adapter contract tests. Building this backend BEFORE
   it means writing a pyqtgraph adapter against the old bare-callback API and then rewriting it — and
   worse, a second selection path that skips the contract. The interaction-layer spec is what makes
   the brushing layer backend-NEUTRAL; this spec is the first consumer that proves it. Order matters. Touches
`plot_backends.py`, a new backend impl, an opt-in dep. Not `file_io.py`.

## Why PyQtGraph, why now
Verified: `plot_backends.py:51` already abstracts three backends
(`BACKENDS = ('matplotlib', 'seaborn', 'plotly')`) behind one `scatter(df, x_col, y_col, *,
backend=…)` (`:140`), with a `_verify_row_order` guard (`:90`) enforcing artist↔row 1:1
correspondence. The brushing arc landed: `SelectionService` (`selection_service.py:92`,
`select`/`subscribe`), and matplotlib emits selection via `make_pickable` → `hub.select(ref,
source='plot')` (`brushing.py:151/231`). So the seam PyQtGraph plugs into is proven and in use.

PyQtGraph fits PyCAT's architecture better than the already-integrated plotly for INTERACTIVE panels:
native Qt (napari is Qt) → same event loop, no Qt→WebEngine→JS bridge; selections are native Qt
signals; fast scatter at large N (the increment-4 scaling concern); low latency. The honest division:
**PyQtGraph for Explore, matplotlib for Export, plotly stays as the browser option.**

## Part A — the dependency (opt-in)
Add `pyqtgraph` as an OPTIONAL dependency (a `[pyqtgraph]` or fold into an existing interactive extra),
NOT a hard requirement — PyCAT must import and run headlessly without it (the `test_headless_science` /
core-import contract). Guard the import: the backend module imports pyqtgraph lazily inside its
functions, and `plot_backends` only offers `'pyqtgraph'` when it's installed.

## Part B — the backend (mirror the existing contract exactly)
Add `'pyqtgraph'` to `BACKENDS` and a branch in `scatter(...)` (`:140`). Implement it in a new
`utils/plot_backend_pyqtgraph.py` (keep `plot_backends.py` from growing past the complexity ceiling):
- `scatter(df, x_col, y_col, backend='pyqtgraph', ...)` returns a pyqtgraph `PlotWidget` (a Qt widget)
  whose `ScatterPlotItem` points map **1:1 to df rows in order** — and run the SAME `_verify_row_order`
  check the other backends do (`:90`), so a reordered artist is REFUSED not silently mismapped
  (`test_plot_backends` already asserts this for the others — extend it to pyqtgraph).
- `hue` support parity with the matplotlib/seaborn path (per-group colour, still one artist in row
  order — mirror `test_the_matplotlib_and_seaborn_scatters_map_1_to_1_to_the_rows`).

## Part C — emit into the SelectionService (brush parity)
The pyqtgraph scatter must participate in linked brushing exactly like the matplotlib one:
- connect `ScatterPlotItem.sigClicked` (native Qt signal) → resolve the clicked point index → the
  `EntityRef`/`ObjectRef` for that row (via `refs_from_dataframe`, the same source `make_pickable`
  uses) → `service.select(Selection(..., source_view='pyqtgraph.plot'))`.
- subscribe the scatter to the service so an INBOUND selection highlights the corresponding point
  (use the increment-4 overlay-artist approach — a second highlight point, O(1) — not a full re-colour
  of N points).
- honour the same echo-suppression (`source_view`) and the opt-in camera-follow the arc established —
  a pyqtgraph click must not loop (the VPT-rework P3 lesson: don't let a reveal re-enter selection).

## Part D — wire it as an OPTION, not a default
`scatter(...)` default stays `'matplotlib'`. PyQtGraph is chosen explicitly (a backend arg / a user
preference for interactive panels). Export paths keep using matplotlib. Do NOT switch any existing
plot to pyqtgraph by default — this is a new capability users opt into.

## Steps
1. Optional `pyqtgraph` dep + lazy import guard; `plot_backends` only offers it when installed.
2. `utils/plot_backend_pyqtgraph.py`: the `scatter` impl returning a `PlotWidget`, row-order-verified,
   hue parity.
3. Wire `sigClicked` → `service.select(...)`; subscribe for inbound highlight via the overlay artist;
   echo-suppression + opt-in follow.
4. Tests: extend `test_plot_backends.py` — pyqtgraph maps 1:1 to rows, a reordered artist is refused,
   hue keeps row order; a click emits one selection (no loop); an inbound selection highlights via the
   overlay not a full recolour. Mark `core` where Qt-free; guard/skip the pyqtgraph-requiring parts
   when pyqtgraph isn't installed (like the plotly-skip pattern in the existing test).
5. Full `pytest -m core` green (headless import must still pass WITHOUT pyqtgraph; complexity budget).
6. Ship: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (pyqtgraph interactive
   backend, additive, emits into SelectionService; matplotlib stays export, plotly stays).

## Definition of done
- `scatter(..., backend='pyqtgraph')` returns a native-Qt interactive scatter, row-order-verified,
  hue-capable.
- A click brushes through the SelectionService (one selection, no loop); inbound selection highlights
  O(1) via the overlay.
- pyqtgraph is optional; PyCAT imports/runs headlessly without it.
- matplotlib remains the default + export backend; plotly unchanged.
- Full `pytest -m core` green.

## Cautions
- ADDITIVE — do not switch existing plots or the default to pyqtgraph; it's an opt-in interactive
  backend.
- Run `_verify_row_order` for pyqtgraph too — the whole point of the abstraction is that identity is
  backend-independent; a fast backend that mismaps rows is worse than a slow correct one.
- Reuse `refs_from_dataframe` + the SelectionService + the overlay artist — do NOT build a second
  selection/identity path (that reintroduces the parallel-registries tax the audit flagged).
- Keep pyqtgraph imports lazy/guarded — the headless-import contract is non-negotiable.
- Mind the click-loop lesson (VPT P3): a pyqtgraph reveal must not re-enter selection; honour opt-in
  camera-follow.
