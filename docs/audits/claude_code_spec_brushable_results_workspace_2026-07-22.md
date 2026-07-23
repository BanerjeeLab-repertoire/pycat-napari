# Claude Code spec — Brushable results workspace (plots + tables + image), single-image and batch

**Date:** 2026-07-22 · **Target tree:** 1.6.281 · Requested by Meet Raval + Gable. Build one reusable
**brushable results workspace** — plots stacked on the left, tables stacked on the right, all cross-linked
to each other and to the napari image, modeled on the VPT brushing mechanism — and instantiate it for the
Cellular-fluorescence and In-vitro-fluorescence pipelines and for the end of a batch.

## The ask (verbatim intent)
- **Cellular object analysis (Fluorescence), single image:** left (stacked) — the **Csat plot**
  (`puncta_intensity_total` vs `intensity_total`) and the **dilute-vs-nucleus plot**
  (`cell_xor_puncta_int_total` vs `intensity_total`, homotypic vs heterotypic PS); right (stacked) — a
  **cell-wise table** and a **condensate-wise table**. **Two interleaved brushing tiers over one image:**
  clicking a *cell* brushes the cell table + the (cell-level) plots; clicking a *condensate* brushes the
  condensate table — and any table/plot click reveals the object in the image.
- **In-vitro 2D fluorescence, single image:** left (stacked) — **condensate intensity vs size** and
  **condensate intensity vs circularity**; right — the **condensate-properties table**. Table ↔ image ↔
  plots all brush together (single tier).
- **Batch:** the same combined plots+tables produced at the **end of a batch**, where brushing a point/row
  **pulls the originating image from the batch** and brushes it, exactly like the single-image case.

## Verified state — the rails already exist (from a 3-way code map)
**Selection / brushing infrastructure (reuse, do not reinvent):**
- `utils/selection_service.SelectionService` — the one app-wide dispatcher, owned by
  `central_manager.selection`, keyed on the stable `_pycat_entity_id` string. `SelectionView` protocol +
  `register_view`; echo-suppression via `source_view` + delayed busy release; `subscribe_deferred` for the
  expensive image-reading lane. **A different entity type (cell vs condensate) is a different key**, so the
  two tiers coexist in one service for free.
- `ui/brushable_table.BrushableTable` / `make_brushable(table_view, df, service, view_id)` — a sort-safe
  `QTableView` that is a `SelectionView` keyed on `_pycat_entity_id` (`entity_row_map`).
- `utils/brushing.make_pickable` / `attach_brushing(fig, artist, refs, viewer=, central_manager=)` +
  `object_ref.refs_from_dataframe(df, source_path=)` — a matplotlib scatter → object → image + other-views.
- `utils/selection_overlay.show_selection/clear_selection` + `object_ref.resolve_in_viewer` (live) /
  `resolve_offline` (opens `source_path`, reads `frame`, slices `bbox` — **no session, no re-segmentation**).
- `object_ref.ObjectRef.from_row(row, source_path=)` (reads `bbox_*`, `label`, `frame`, `source_layer_id`,
  `entity_id`); `is_resolvable_offline()` == `source_path and bbox` present.
- `entity_ref.finalize_entity_table(table, operation_id, source_path=)` — the identity chokepoint (stamps
  `_pycat_entity_id`); default specs already exist for `cell_analysis`, `puncta_analysis`,
  `condensate_analysis`. `attach_layer_id(df, layer)` binds rows to a labels layer.
- **Layout + lifecycle pattern:** `toolbox/vpt/results_dock.py` — a `QSplitter` in **one persistent**
  `add_dock_widget`, subscribe all views once, re-apply the live selection after any redraw; inbound renders
  under `ProgrammaticGuard.applying()`, outbound early-outs on `service.is_busy`. Verify new views with
  `tests/selection_view_contract.assert_selection_view_contract`.
- **Batch template already exists:** `utils/comparative_figures` + `ui/comparative_figures_ui` render
  brushable plots+tables from `consolidated_long.csv` through the same `SelectionService`;
  `ui/linked_selection_dock.LinkedSelectionDock` shows a clicked object's crop (live or offline).

**Pipeline A — Cellular fluorescence (`toolbox/feature_analysis_tools.py`):**
- `cell_df` (tier 1, keyed `label`): has `intensity_total` (:481), `puncta_intensity_total` (:672),
  `cell_xor_puncta_int_total` (:666/677). Brush-ready — bbox columns + `finalize_entity_table('cell_analysis')`
  (:508); layer id attached to the `'Labeled Cell Mask'` labels layer (:604).
- `puncta_df` (tier 2, keyed `label`, parent `'cell label'`): has `circularity` (:762), `micron area`,
  `ellipticity`. Brush-ready via `finalize_entity_table('puncta_analysis')`. Punctum labels **restart per
  cell** → identity is (frame, cell label, punctum label), handled by the entity spec.
- **Caveat:** the `'Cell Labeled Puncta Mask'` layer is painted with **cell** labels (a punctum shows its
  cell's label) and is **not** `attach_layer_id`'d — clicking it yields a cell, not a punctum. Individual-
  condensate image picking needs its own pick target (see Decision 2).
- Cell↔condensate link: `puncta_df['cell label'] == cell_df['label']`.
- Results shown today by `show_dataframes_dialog(...)` at `feature_analysis_tools.py:620` & `:960`; buttons
  in `ui/ui_analysis_mixin.py:70` (cell) & `:109` (condensate).

**Pipeline B — In-vitro fluorescence (`toolbox/invitro_fluor_ui.py`, `toolbox/invitro/partition.py`):**
- `per_droplet_df` from `partition_coefficient_local` (partition.py:280): keyed `droplet_label`; has
  `partition_coefficient`, `I_dense`, …; the UI appends `area_um2` (size) **positionally** (:773). **No
  bbox, no circularity, not entity-finalized** — rows are position/label-linked, not brush-ready.
  `partition_coefficient_field` (partition.py:716) DOES emit bbox + `area_um2`.
- Layer `'IVF Droplet Mask (n droplets)'` (:504) — **not** `attach_layer_id`'d.
- Results shown by `_show(...)`→`show_dataframes_dialog` at `invitro_fluor_ui.py:776`; button handler
  `_on_run` (:705).

**Batch (`batch_processor.py`, `utils/consolidated_table.py`):**
- `consolidated_long.csv` carries `entity_id` (cross-view brushing already works) but **not** `source_path`
  or a resolvable `bbox` (bbox columns are melted into `measurement`/`value` rows). Per-image
  `<stem>_cell_df.csv` / `<stem>_puncta_df.csv` in `output_dir/<stem>/` retain `bbox_*` + `_pycat_entity_id`.
- The **offline-crop seam** the batch case must add: join a consolidated/per-image row back to bbox +
  reconstruct `source_path` (batch input folder + `image_stem`, or the `dataset_id` UUID via
  `dataset_identity`), then `ObjectRef.from_row → resolve_offline`. Every other piece already exists.

## Design — one reusable `BrushableWorkspace`
A single widget (new `ui/brushable_workspace.py`) generalizing `vpt/results_dock`:

`QSplitter(Horizontal)`: **left** = a vertical stack of brushable plot canvases; **right** = a vertical
stack of `BrushableTable`s. It takes a declarative config and wires everything to
`central_manager.selection`:

```
WorkspaceSpec(
    plots=[PlotSpec(df, x_col, y_col, entity_view_id, title), ...],   # left, top→bottom
    tables=[TableSpec(df, entity_view_id, title), ...],               # right, top→bottom
    image_tiers=[ImageTier(labels_layer | points_picker, entity_view_id), ...],  # interleaved pick targets
)
```
- Each plot: `attach_brushing` on the scatter + a `SelectionView` that re-emphasises the point on inbound
  selection. Each table: `make_brushable`. Each image tier: a pick handler that maps click→`_pycat_entity_id`
  and emits `service.select(source_view=...)`, and a deferred subscriber that reveals via
  `selection_overlay` / `resolve_in_viewer` (live) or `resolve_offline` (batch).
- **Two interleaved tiers = two image pick targets + two entity view-ids** over one image. The service keys
  on entity type, so a cell selection lights the cell plots+table and a condensate selection lights the
  condensate table, with no special-casing.
- One persistent dock; subscribe once; re-apply live selection after redraw; contract-verified.

## Phases (each independently shippable)
1. **`BrushableWorkspace` core — DONE (shipped 1.6.282).** `ui/brushable_workspace.py`: `BrushablePlot` (a
   scatter promoted to the `SelectionView` contract — `comparative_figures._attach_object_brushing` as a
   class, with `_object_points`/`_draw` overridable so a VPT custom painter plugs into the same
   click→select / select→ring machinery) + `BrushableWorkspace` (a `QSplitter`: plots stacked left via
   `add_plot`, brushable tables stacked right via `add_table`, all on `central_manager.selection`, keyed on
   `_pycat_entity_id`; identity columns hidden from display but kept for brushing; `detach()` unsubscribes
   all). `tests/test_brushable_workspace.py` (`core`, 5 tests): the plot passes
   `assert_selection_view_contract`; a plot click selects everywhere; an inbound selection rings without
   emitting; a plot + table over one df brush together; two entity *types* (cell vs condensate) are
   independent tiers on one service. No pipeline wired yet; no image tier yet (lands with a real layer in
   Phase 2/3).
2. **In-vitro (Pipeline B) — DONE (shipped 1.6.283).** `_finalize_droplet_table` makes `per_droplet_df`
   brush-ready additively (a copy; `area_um2` + `circularity` + bbox keyed by `droplet_label`, then
   `finalize_entity_table('condensate_analysis')` + `attach_layer_id`); `_mount_droplet_workspace` docks a
   `BrushableWorkspace` — plots (intensity-vs-size, intensity-vs-circularity) left, per-droplet + field-stats
   tables right, and the droplet mask as an image tier (the new `BrushableImageTier`: click a droplet ↔ the
   plots/tables ↔ the image reveal). Wired at `invitro_fluor_ui._on_run` (falls back to the old dialog if
   there is no selection service). `tests/test_invitro_brushable.py` (`core`, 3 tests): the augmentation adds
   size/circularity/bbox/identity without changing any droplet number; a click on a droplet selects its
   entity; the image tier passes `assert_selection_view_contract`. Full core green (1712).
3. **Cellular (Pipeline A)** — the two-tier instantiation. `cell_df`/`puncta_df` are already brush-ready;
   add a per-punctum **condensate pick target** (Decision 2); mount at `feature_analysis_tools.py:960` with
   2 cell plots + cell table + condensate table + two image tiers (cell labels, condensate picker).
4. **Batch** — feed the workspace from batch outputs at run end; add the offline-crop seam (per-image-CSV
   bbox join + `source_path` reconstruction → `resolve_offline`). Reuse `comparative_figures` where it fits.

## Decisions — RESOLVED by Gable (2026-07-22)
1. **Build order:** Core → in-vitro → cellular → batch. **Added constraint:** VPT's brushing IS the
   template, and the core must be designed so **`vpt/results_dock` can later be refactored onto it** — i.e.
   the `BrushableWorkspace` abstraction must be general enough to express VPT's 2×2 custom-painter panels +
   paged track table + bead-picker image tier, not only simple scatters. Phase 1 implements the scatter+table
   path but structures the plot view around a `render(ax)`/`refs` interface a custom painter (VPT) can plug
   into later.
2. **In-vitro data model:** **augment `per_droplet_df` additively** — regionprops `bbox` + `circularity`
   (perimeter/area) + `finalize_entity_table('condensate_analysis')` + `attach_layer_id`. No existing droplet
   number changes.
3. **Cellular condensate image-picking:** build off the **per-punctum segmentation mask that already exists**
   (the puncta are individually masked during segmentation before being repainted with cell labels) — expose
   a **per-punctum labels layer** for native napari picking, **carrying stable punctum identity that
   survives save→load** (the same punctum resolves to the same entity id in a later session). This means the
   per-punctum layer is `attach_layer_id`'d and its entity ids are persisted via the session manifest /
   `dataset_id` UUID, and the (frame, cell label, punctum label) key stays stable across reload.
4. **Batch image recovery (Phase 4):** reconstruct `source_path` from the batch input folder + `image_stem`,
   with a `dataset_id`-UUID registry fallback for moved files.

## Tests (`core` where possible; qtbot for Qt assembly)
- Each new view kind passes `assert_selection_view_contract` (cell-plot, condensate-plot, cell-table,
  condensate-table, image-tier).
- A cell selection highlights the cell table + both cell plots and reveals the cell; a condensate selection
  highlights the condensate table + reveals the condensate — one does not fire the other's views.
- In-vitro: `per_droplet_df` is entity-finalized (has `_pycat_entity_id`) and carries bbox + circularity; a
  droplet plot click reveals the droplet; the two axis columns exist.
- Batch: a consolidated/per-image row resolves to an `ObjectRef` with `is_resolvable_offline()` True; a
  batch plot click produces the correct offline crop; the source-path reconstruction is unit-tested.
- No existing analysis value changes (cell_df/puncta_df numbers unchanged; additive columns only).

## Definition of done
- One reusable brushable workspace; three instantiations (in-vitro, cellular, batch) with plots-left /
  tables-right and full cross-view + image brushing; the cellular case has two interleaved (cell,
  condensate) tiers; batch brushing pulls the originating image.
- No scientific output changes; existing brushing/selection tests pass unmodified; full `pytest -m core`
  green.

## Cautions
- **Reuse the one `SelectionService`** (`central_manager.selection`) — never a second hub. Key everything on
  `_pycat_entity_id`.
- **Don't resolve a punctum against the cell-labeled mask** — it yields the wrong object; use the condensate
  pick target / bbox.
- **Additive only** on the pipelines' dataframes — the cell/puncta/droplet numbers must not change; the
  pixel-size and identity guards must stay green.
- Every inbound render under `ProgrammaticGuard`; every outbound early-outs on `is_busy`; every view
  `unsubscribe`s on teardown (the guards VPT already proves).
- Ship phase by phase; the workspace core lands first and is contract-verified before any pipeline depends
  on it.
