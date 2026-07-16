# Claude Code spec — Brushing increment 5: the linked-selection inspector & table adapter (the payoff)

## 🟡 STATUS — Parts B + C DONE, shipped in 1.6.77 (part 1 of 2). Parts A/D/E OUTSTANDING.
`pytest -m core`: **680 passed, 2 skipped** (was 670). Split per Gable's decision: the two verifiable
parts first, the dock + interaction model as a second version.

**DONE:**
- **Part C — the brush-aware table** (`ui/brushable_table.py`). *The payoff of increments 1–4:*
  selection survives a sort, keyed on the increment-2 entity id, dispatched through the increment-3
  service. Mutation-checked — keying on the visual row (VPT's `row_for_id` bug) turns the sort tests
  red. Tables without ids work by position and report `linkability_of`.
- **Part B — overlay highlight** (`utils/selection_overlay.py`); `selected_label` is no longer
  touched by brushing.

**Four premises that did not survive the tree:**
1. **`show_dataframes_dialog` is already a `QTableView` + `QAbstractTableModel`** — not the eager
   `QTableWidget` the spec assumes. Rows were never populated eagerly, there is no sort/filter, and
   the work is a **proxy + wiring**, not a rewrite. The genuinely eager table is **VPT's**
   `QTableWidget` (one `QTableWidgetItem` per cell) — that swap is part 2.
2. **"the resolver spawns a new `object N` layer per click (layers accumulate)"** — it reuses the
   layer by name, so it is one per *distinct object clicked*. The sharper defect the spec misses is
   the **name collision**: `object 7` from two different masks share one layer, which the
   `pycat_layer_id` already on the ref would fix.
3. **Part E's premise does not exist.** No results table mixes aggregate rows with per-object rows —
   aggregates are separate single-row tables under their own titles (`condensate_physics_ui`,
   `vpt_ui`), and `data_viz_tools` already declines to brush a bbox-less table. Part E is **new
   behaviour, not a fix**; it needs confirming before building.
4. **There is no preference persistence** to hang "Follow selection in viewer" on.
   `persist_measurements` is a session-only attribute + checkbox; no QSettings, no prefs file. The
   toggle would be session-only, or disk persistence is new scope.

**And a regression of my own, found on the way and fixed here:** increment 2 (1.6.74) introduced
`_pycat_entity_id`/`_pycat_layer_id` with a comment calling them hidden **and nothing that hid them**.
For two versions they were listed in every results dialog, offered as plot axes, and **exported into
every saved results CSV**. `visible_columns` / `without_identity` now enforce it. *A doc comment is
not a mechanism* — and I wrote the comment.

**Outstanding for part 2:** Part A (the persistent Linked Selection dock, replacing the per-object
`object N` layers), Part D (hover/click/double-click/shift/Escape + the camera-follow toggle), and a
decision on Part E. Note the dock's napari integration (`add_dock_widget`) is **not verifiable in
this environment** — `napari.Viewer` needs a GL context that offscreen Qt cannot provide, which is
why `test_ui_smoke.py` errors here. Plain Qt model/view *is* testable headlessly (this increment's
tests are), so the dock's widget logic can be tested; its docking cannot.

**Date:** 2026-07-16 · **Target tree:** verified against 1.6.70. **PREREQUISITE: increments 1–4
landed** (identity + SelectionService + scaling). Re-validate when you start. The most user-facing
increment; sits on everything below. Touches UI (`ui_modules.py` dialog, a new dock, `vpt_ui.py`
table); not `file_io.py`.

## Goal
Turn "a click reveals label 4 in some layer" into "follow a point through its source image,
segmentation, parent, frame, measurements and lineage without losing context." Everything here rides
the increment-3 `SelectionService` + increment-2 identity.

## Part A — persistent "Linked Selection" dock (replaces per-click `object N` layers)
Today the resolver spawns a new `object N` image layer per click (layers accumulate). Replace with ONE
persistent dock subscribed to `SelectionService`:
- cropped source image (via increment-1's slice-before-materialize `crop_for_ref` — lazy, one crop);
- selected row's key metrics;
- dataset / frame / channel;
- parent cell or track (from `EntityRef.parent_keys`);
- analysis lineage breadcrumb (from the tag lineage);
- buttons: **Reveal in image**, **Show parent**, **Open source layer**, **Pin**.
Selecting anything (plot point, table row, viewer object) updates the dock; nothing spawns transient
layers.

## Part B — image highlight via a dedicated OVERLAY (not `selected_label`)
Stop setting `selected_label` on the analytical labels layer (increment 1 removed the wrong-target
resolve; finish the job). Highlight via dedicated overlay layers:
- one `Shapes` layer for the bbox / outline,
- one `Points` layer for the centroid.
Benefits: doesn't hijack napari's label-painting selection, works for objects with no live mask,
shows multiple selections, stable styling. Render the bbox immediately on click; compute a precise
mask outline only if needed.

## Part C — brush-aware table adapter (virtual `QTableView`)
Replace VPT's eager `QTableWidget` and wire `show_dataframes_dialog()` (`ui_modules.py`) to the
service when a table has `_pycat_entity_id`:
- back it with a virtual model + `QSortFilterProxyModel` so selection survives sort/filter (keyed by
  the increment-2 entity id, NOT row position);
- emit row selection into `SelectionService`; highlight rows on inbound selection;
- scroll only on explicit Reveal, not on every hover.
Tables WITHOUT entity ids keep working by position and show the increment-2 "by-position" flag.

## Part D — interaction model + Follow-selection preference
Implement the audit's interaction split (fixes "abrupt navigation"):
- hover → preview (dock updates, cheap);
- single click → pinned linked selection;
- double-click / Reveal → move camera + frame;
- shift-click → add to selection;
- Escape → clear.
Add a **"Follow selection in viewer"** preference gating the camera/frame jump (default OFF so
exploratory clicking doesn't yank the view).

## Part E — aggregate rows honestly
A per-cell mean / population-fit row summarizes many objects. Represent as
`EntitySelection(kind="query", members=(EntityKey, ...))`. Clicking an aggregate: highlight ALL
contributing objects (overlay), state "summarizes 42 objects", offer navigate-to-parent — rather than
resolving to one wrong object.

## Steps
1. Linked-selection dock subscribed to `SelectionService`; Reveal/Show-parent/Open-layer/Pin buttons.
2. Overlay-based highlight (Shapes bbox + Points centroid); remove any remaining `selected_label`
   highlight path.
3. Virtual `QTableView` adapter + `QSortFilterProxyModel`; wire `show_dataframes_dialog` + VPT table
   to the service; replace VPT's `QTableWidget`.
4. Interaction model + "Follow selection in viewer" preference.
5. Aggregate `EntitySelection(kind="query")` + highlight-all-members.
6. Tests: table selection survives a sort (entity id → same object); aggregate highlights N members;
   the dock updates on selection without spawning layers; overlay highlight doesn't touch the labels
   layer's `selected_label`. Mark `core` where Qt-free; UI-smoke where not.
7. Full `pytest -m core` green (complexity budget — the dock/table are new; keep functions <120,
   extract builders).
8. Ship: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (increment 5: linked
   inspector, brush-aware table, overlay highlight, interaction model, aggregate linking). Update
   `roadmap.rst` — brushing arc COMPLETE.

## Definition of done
- One persistent dock shows crop + metrics + lineage + actions; no more per-click `object N` layers.
- Highlight is an overlay; the analytical labels layer's selection state is never hijacked.
- Tables brush through sort/filter via stable identity; VPT table is the virtual view.
- Hover/click/double-click/shift/Escape behave per the model; camera-follow is opt-in.
- Aggregate rows link to their constituents honestly.
- Full `pytest -m core` green; roadmap brushing arc marked complete.

## Cautions
- Everything subscribes to the ONE increment-3 service — no second selection path.
- Highlight via overlay ONLY; never `selected_label` on the analytical layer.
- Camera-follow OFF by default — abrupt navigation was an explicit complaint.
- Keep new UI functions under the complexity ceiling; extract builders rather than raising it.
- Crops go through increment-1's lazy `crop_for_ref` — never materialize a stack for a preview.
