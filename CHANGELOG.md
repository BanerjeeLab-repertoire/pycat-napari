## [1.6.130] - 2026-07-19
### Added — **Multi-scene switcher: load one position at a time, lazily, and switch in place.**
A multi-position acquisition (CZI/IMS/OME-TIFF) used to load **every selected scene into memory at
once** — the load-everything profile the streaming work removed everywhere else, with no way to change
position without reopening. Now a multi-position file loads **exactly one scene, lazily**, and a dock
switches position in place.

- **`_SceneStack`** (`file_io/lazy_sources.py`) — the lazy (T, Y, X) wrapper for one scene. Reads one
  plane at a time from its **pinned** scene, refuses `__array__`, and — the headline hazard — re-pins
  the scene on **every** read, so a shared stateful reader can never serve a frame from another
  position. Switching builds a fresh wrapper, so no plane cache is shared across scenes: a stale
  previous-position frame cannot exist by construction.
- **`file_io/scenes.py`** (new, Qt-free) — `build_scene_stack`, `tag_scene_layer`/`scene_of` (scene
  identity as a queryable tag, joinable to the comparative-phenotyping sample metadata — a position is
  often a condition), `list_scenes`/`scene_index`.
- **Routing** (`file_io._open_stack_generic`) — a multi-position file now loads **one** scene (the
  first chosen, default scene 0) and tags each lazy layer with its position. **Single-scene files are
  untouched.** The several-scenes-overlaid memory footgun is gone rather than kept beside the default.
- **Per-scene calibration** (`data/data_modules.update_metadata`) — reads the **currently selected**
  scene's pixel size, not a fixed scene 0, so a switch cannot silently mis-scale (a position can
  legitimately differ).
- **The switcher dock** (`ui/scene_switcher.py`, new) — a position dropdown (File menu → "Switch
  Position / Scene"). Switching rebinds every scene layer to a fresh `_SceneStack` for the new position,
  warms the first (slow) frame **off the Qt thread** (`run_with_progress`, no "Not Responding"),
  re-reads per-scene calibration, re-tags the layers, and **stamps derived layers with the position
  they were computed on** so a mask from position 1 cannot masquerade as belonging to position 2.
### Notes
- Headless-tested (Qt-free foundation): `_SceneStack` contract + one-plane read + scene-provenance
  (`test_scene_stack.py`), the scene helpers + per-scene metadata (`test_scenes.py`), and the switcher's
  `switch_to` rebind/re-tag/stale-derived logic (`test_scene_switcher.py`, `run_with_progress` runs
  synchronously with no event loop). The AST eager-read guard auto-covers the new wrapper. Full
  `pytest -m core` green.
- **Needs in-app verification** (viewer-coupled, cannot be checked headlessly): opening a real
  multi-position file loads one position; the File-menu switcher changes it in place without a freeze or
  a stale frame. Held for that verification before release.
- Files: `src/pycat/file_io/lazy_sources.py`, `src/pycat/file_io/scenes.py`,
  `src/pycat/file_io/file_io.py`, `src/pycat/data/data_modules.py`, `src/pycat/ui/scene_switcher.py`,
  `src/pycat/ui/ui_modules.py`, `tests/test_scene_stack.py`, `tests/test_scenes.py`,
  `tests/test_scene_switcher.py`.

## [1.6.129] - 2026-07-19
### Added — **OperationSpec increment 5: `requirements` — runnability gating with a stated reason. (The OperationSpec arc is complete.)**
The last field of the OperationSpec roadmap. Increment 2's `inputs` said which *layers* an operation
consumes; increment 5 adds `requirements` — the *data/environment* preconditions it needs to be runnable
at all: a 3D z-stack, a time axis, a calibrated pixel size, two channels, a GPU. Declared on
`@tags_layer` from a controlled vocabulary, **with the validation that makes it real** — so a consumer
can gate an operation **before** the click and **say why** ("needs a 3D z-stack"), instead of letting it
fail at run time.

- **`tag_registry.REQUIREMENTS`** — a controlled name→reason map (the single source of the requirement
  vocabulary and its human-readable phrasing). `@tags_layer(requirements=…)` accepts values from it;
  an unregistered requirement is a hard error at import, like an unregistered tag.
- **`OperationSpec.requirements`** — surfaced, snapshotted into `operation_catalog.json` (regenerated),
  and covered by the drift guard / regeneration check.
- **`operation_spec.runnability(spec, available)`** → `(can_run, reason)`, plus `unmet_requirements()`
  — the gating helpers. `reason` names, in human terms, exactly what is missing, so the UI can grey the
  operation out with an explanation. `inputs` remain the *layer* preconditions (the Capability
  machinery); `requirements` are the *environmental* ones — the two are complementary.
- **`tests/navigator/test_operation_requirements.py`** — the validation: vocabulary agreement, every
  requirement carries a reason, gating returns `(False, "needs …")` when unmet and `(True, "")` once
  satisfied, and a **downward-only coverage floor** (≥ 8 ops).
- **Annotated the unambiguous tranche (8 ops):** the 3D ops need a z-stack (`gaussian_3d`, `gabor_3d`,
  `dog_3d`, `bg_subtract_3d`, `cellpose_3d`, `subcellular_segment_3d`), the temporal ops need a time
  axis (`temporal_enhance`, `drift_correct`).
### Notes
- **The OperationSpec roadmap is now complete** (increments 1–5): a typed, drift-guarded spec that is a
  graph (`inputs`/`produces`), against which batch replay is auditable (`_STEP_OPERATIONS`), that the
  Navigator is *generated from* (increment 4), and that now carries runnability gating. Wiring the gate
  into actual UI widgets, and the separate `tag_resolver` binding table, remain as follow-on consumers —
  noted, not built here.
- Staged population per the increment-2 discipline; a guessed requirement is worse than none. No
  behaviour change. Full `pytest -m core` green.
- Files: `src/pycat/utils/tag_registry.py`, `src/pycat/navigator/operation_spec.py`,
  `src/pycat/navigator/op_catalog.py`, `src/pycat/navigator/data/operation_catalog.json`, the 4
  annotated toolbox modules, `tests/navigator/test_operation_requirements.py`,
  `tests/navigator/test_operation_spec_matches_catalog.py`.

## [1.6.128] - 2026-07-19
### Changed — **OperationSpec increment 4: flip the Navigator catalog from validate-against-the-spec to GENERATE-from-the-spec.**
The payoff increment 1 was built for. Increment 1 made the operation catalog a *validated* committed
snapshot; increments 2–3 turned the vocabulary into a graph and made batch replay auditable against it.
Increment 4 does the flip on the lowest-risk consumer — the **Navigator catalog** — as a proven-safe
change: the operation set is now **generated from the live spec** (`iter_operation_specs()` → the
`@tags_layer`/UI decorators) instead of read from the committed `operation_catalog.json`. The decorators
are the runtime source of truth; the JSON becomes a reviewable, shippable *artifact*, no longer
authoritative at run time — the Navigator builds correctly even if the file is absent. One subsystem
only, to prove the pattern before touching UI or batch.

- **`build_catalog_document()`** (`navigator/op_catalog.py`) — new: builds the catalog document purely
  from the live spec, no file write. `regenerate_operation_catalog()` is now a thin writer over it.
- **`build_operation_registry(from_spec=True)`** — the flip: the Navigator's layer ops are generated
  from `build_catalog_document()` by default. `from_spec=False` still builds from the committed file (an
  escape hatch for tooling); the two are **equal by construction**, so this is not a behaviour change.
- **The drift guard becomes a regeneration check.** `test_operation_spec_matches_catalog.py` now asserts
  the committed JSON *equals* `build_catalog_document()` exactly — stronger than the field-by-field
  checks (it also catches provenance/ordering/field-set drift), and it proves the committed artifact is
  the generation and nothing else. The granular coverage/field tests are kept because they name *what*
  diverged. A new test proves the registry generated from the spec is identical to the one built from
  the file.
### Notes
- **No behaviour change and no JSON churn:** the committed `operation_catalog.json` already equalled the
  regeneration (it was regenerated in increment 2), so the flip changed the *source of truth*, not the
  numbers. Full `pytest -m core` green.
- Scope held to one subsystem per the roadmap — batch (`_STEP_MAP`) and UI are untouched. The remaining
  increment is **5** (`requirements`/runnability gating, wiring the spec to `capabilities.py`), plus the
  separate binding-table effort.
- Files: `src/pycat/navigator/op_catalog.py`, `src/pycat/navigator/__init__.py`,
  `tests/navigator/test_operation_spec_matches_catalog.py`.

## [1.6.127] - 2026-07-19
### Added — **OperationSpec increment 3: declare the batch-step → operation composition, so replay is auditable against the vocabulary.**
Increment 2 made the operation vocabulary a graph. Increment 3 connects the **batch replayer** to it.
The increment-2 survey measured a key fact: `batch_step_registry._STEP_MAP` (68 workflow steps) and the
operation catalog (79 layer ops) have **zero name overlap** — a *step* is a workflow stage
(`condensate_segmentation`), an *operation* is a layer-producing transform (`subcellular_segment`). That
is correct design, not drift, so the two vocabularies are **not merged**. The honest relationship is
**composition**: a step *invokes* one or more operations. That mapping cannot be inferred, so it is now
**declared** and **drift-guarded** — rename an operation and the build breaks here instead of replay
silently breaking at run time. This is the prerequisite for ever *generating* batch steps.

- **`_STEP_OPERATIONS`** in `batch_step_registry.py` — the declared step → operation-ids map, placed
  next to `_STEP_MAP` (declared at the code, not a side table). Public accessors `step_operations(name)`
  and `all_step_operations()`. Each mapping was **verified** against the step's replay function (its
  toolbox imports → the op that function declares), never guessed.
- **`tests/navigator/test_batch_step_composition.py`** — the validation: every declared op exists in
  the operation vocabulary (catalog layer ops ∪ curated measure ops), every declared step is a real
  `_STEP_MAP` key, no empty declarations, ids are a deduplicated lower-case set, and a **downward-only
  coverage floor** (≥ 10 steps mapped). Undeclared steps are *reported*, not hidden.
- **Declared the unambiguous tranche (10 steps):** `preprocessing`→`preprocess`, `upscaling`→`upscale`,
  `calibration_correction`→`flatfield`/`bg_subtract_clear`, `auto_crop_roi`→`multi_otsu`,
  `cellpose_segmentation`→`cellpose`/`stardist`, `condensate_segmentation` & `ivf_segmentation`→
  `mask_stretch`/`subcellular_segment`, `bf_condensate_segmentation` & `ivbf_segmentation`→`bf_segment`,
  `ivf_size_distribution`→`invitro.size_distribution` (a measure op).
### Notes
- **Staged population, deliberately** (the increment-2 discipline). Steps that invoke only an *untagged*
  composite (e.g. `background_removal`'s rolling-ball path has no op id) or only file I/O
  (`open_image`, `save_and_clear`) are **left undeclared**, not mapped to a guess — a guessed mapping is
  drift with extra steps. The ratchet floor captures progress; later work raises it. `_STEP_MAP` and the
  catalog remain separate vocabularies by design (composition, not merger).
- No behaviour change. Full `pytest -m core` green.
- Files: `src/pycat/batch_step_registry.py`, `tests/navigator/test_batch_step_composition.py`.

## [1.6.126] - 2026-07-18
### Added — **OperationSpec increment 2: `inputs` on the decorator — the operation vocabulary is now a GRAPH.**
Increment 1 defined `OperationSpec` as a typed, drift-guarded view over the `@tags_layer` registry and
deferred the richer fields with an explicit rule: *a field nothing checks is exactly the drift this
effort exists to prevent.* Increment 2 adds the first of those fields — `inputs`, the layer
role(s)/target(s) an operation **consumes** — **with the validation that makes it real**. Together with
the existing `produces`, `inputs` turns the flat list of 79 operations into a directed graph
(`op_a.produces → op_b.inputs`). Increments 3–5 (batch composition, subsystem generation, runnability
gating) all need that graph. Additive; no behaviour change.

- **`@tags_layer(inputs=…)`** (`utils/tag_registry.py`) — optional, drawn from the **same** `ROLES` /
  `TARGETS` vocabularies (never a third one); an unregistered input value is a hard error at import,
  exactly as an unregistered tag already is. An operation that declares nothing is a *root* (it loads
  or creates a layer from a file). `_register_ui_operations()` gained the same ability.
- **`OperationSpec.inputs: tuple[str, ...] = ()`** (`navigator/operation_spec.py`) — populated from the
  registry in `iter_operation_specs()`, added to `operation_catalog.json` (regenerated), and covered by
  the existing drift guard (`test_catalog_fields_match_the_live_declaration` now also compares `inputs`,
  so a declared-vs-snapshot divergence fails like any other field).
- **`tests/navigator/test_operation_graph.py`** — the validation: no dangling edges (every declared
  role input is produced by some op or is the root role `image`), vocabulary agreement, traversability
  from the roots (an unreachable input-bearing op is *reported*, not failed), a real op→op edge exists,
  and a **downward-only coverage floor** (≥ 23 ops declare `inputs`) so the declarations populate
  incrementally without silently regressing.
- **Annotated the unambiguous image-consuming tranche (23 ops):** the filters that consume an image and
  return one (`bandpass`, `dog`, `log`, `rolling_ball`, `gaussian`, `bilateral`, `gabor`, `invert`,
  `wbns`, `bg_subtract`, `tone_map`, `local_contrast`, `clahe`) and the primary segmenters/detectors
  that consume an image (`cellpose`, `subcellular_segment`, `local_threshold`, `felzenszwalb`,
  `felzenszwalb_binary`, `multi_otsu`, `clean`, `bead_detect`, `bf_segment`, `host_segment`) — all
  `inputs=('image',)`.
### Notes
- **Staged population is deliberate.** The remaining ~56 ops (label-editors, merges, ambiguous-input
  ops) are **not** annotated: declaring an input you had to guess is drift with extra steps. The ratchet
  floor captures progress; later work raises it. `batch_step_registry._STEP_MAP` was left untouched — it
  names workflow steps, not layer operations, and has zero overlap with the catalog (composition is
  increment 3).
- No `parameters` / `batchable` / `requirements` in this increment. Full `pytest -m core` green.
- Files: `utils/tag_registry.py`, `navigator/operation_spec.py`, `navigator/op_catalog.py`,
  `navigator/data/operation_catalog.json`, the 8 annotated toolbox modules,
  `tests/navigator/test_operation_graph.py`, `tests/navigator/test_operation_spec_matches_catalog.py`.

## [1.6.125] - 2026-07-18
### Added — **Cross-route workflow-equivalence matrix: the same workflow must give the same numbers through every route.**
The strongest recommendation from the external architecture audit: stop adding isolated per-route
tests and start asserting that a canonical workflow produces the **same numbers** through headless call,
batch replay, and session reload. PyCAT exposes each operation through several routes, each of which
assembles its parameters independently — and a disagreement is the highest-severity class of bug PyCAT
can have (the same analysis silently yielding different numbers depending on how it was launched). This
is not hypothetical: batch preprocessing once passed a *normalised* image where the interactive path
passed **raw counts**, and the rolling-ball radius is not scale-invariant. This generalises the test
written for that one step into a matrix.

- **`tests/route_equivalence.py`** — the reusable harness. `run_all_routes(workflow)` drives a workflow
  through each route; `assert_routes_agree(...)` compares each route against a reference and **names
  which route diverged and by how much**. A route that cannot be driven headlessly is declared an
  `Unavailable` **documented gap** — and the harness fails if a gap closes or a route vanishes without
  the record being updated, so nothing is skipped silently. Comparators default to **exact** equality.
- **`tests/test_route_equivalence.py`** — three canonical workflows, distinct data shapes:
  1. **Rolling-ball background removal** (the known-divergence scale-semantics path) — runs all three
     routes and they are **bit-identical**. Both headless and batch reduce to the same toolbox call on
     the same *raw counts*; if the batch replay ever reverts to normalising first, this names it.
  2. **Puncta detection + measurement** — headless ≈ session (the per-object table survives Save &
     Clear → reopen unchanged). Batch is a **declared gap**: its replay needs a cellpose cell mask
     upstream (torch), absent in the headless `core` env.
  3. **VPT tracks → MSD → viscosity** — headless ≈ session, plus an **end-to-end check that the chain
     recovers the known Stokes–Einstein viscosity** (η = kT/6πRD; true D=0.05 → η recovered within
     15% on the noisy synthetic chain). Batch is a declared gap: VPT/MSD replay steps are deliberate
     skip-stubs (time-series, not per-image).
- Adding a fourth workflow is one `Workflow(...)` entry.
### Notes
- **Finding (minor, not fixed here — test-only spec):** a session-restored DataFrame comes back with an
  extra `Unnamed: 0` column — `write_session_outputs` writes the DataFrame index into the CSV, which
  reloads as a phantom column. The scientific numbers are untouched (the harness compares the named
  columns), but the schema gains a column across a save/load. Logged for a future writer fix
  (`to_csv(index=False)`); it is not a numeric divergence.
- **Justified tolerance:** the session route is a *decimal* (CSV) serialization, so a float64 can return
  differing in its last bit (~1 ULP, ~2e-16). That is the text format's precision, not a route
  computing a different number, so the DataFrame comparator allows 1-ULP-scale (rtol 1e-12, atol 1e-15)
  — far below any scientific significance. The array routes (rolling-ball) are compared **exactly**.
- No production code changed. If a real divergence is ever found, it is a finding to report with its own
  spec — not something to absorb by loosening a tolerance.

## [1.6.124] - 2026-07-18
### Fixed — **Tag discovery: kill the stale TEST loader (the "registry regressed" false alarm) and carry a declared `produces` onto the layer.**
An external audit reported the operation registry had *regressed* — "only 42 of ~100 operations
register," "`log` is missing," "duplicate registration is not rejected." **Three of those four findings
were artefacts of a defect in the test, not the product.** `tests/test_tag_registry.py` reimplemented
operation discovery with a hardcoded list of **11** module names inside `except Exception: pass`, missed
**7** decorated modules, counted 42 instead of the real **79**, and swallowed the import failures that
would have said why. Meanwhile `operation_spec._populate_registry()` already AST-discovers every
`@tags_layer` module correctly — the test had a second, worse loader.

- **Fix 1 (the real defect).** The tag tests now discover through the one mechanism —
  `_populate_registry()` — and an unimportable decorated module is a **loud failure**, not a quiet
  undercount. The `>= 50` floor is ratcheted to the live count (**79**). Routed both
  `test_tag_registry.py` and `test_tag_resolver.py` through it; deleted their private loaders. The four
  reported failures pass **with no product change** — the proof that findings 1–3 were test artefacts.
- **Fix 2 (the one genuine gap).** `layer_tag_hook` already copies a known op's declared `target` onto
  the layer (so `role=labels, target=cell` finds Cellpose output). It now also honours a declared
  `produces`: an op that **declares** it makes a mask is tagged `role=mask` even when the returned
  array's values happen to look like labels — the declaration is definitional, the data-shape is a
  guess, and declared beats inferred.
### Added
- An **end-to-end** acceptance test (`operation → layer → tags → resolver`) over six canonical ops —
  `cellpose`, `watershed`, `log`, `dog`, `rolling_ball`, `mask_merge` — each found by a query written in
  its *declared* semantics. `mask_merge` is fed labels-like data on purpose, so only the declared
  `produces='mask'` makes the `role=mask` query match.
### Notes
- **The record, stated plainly:** the "42 operations / missing `log` / no `TagCollision`" findings were a
  stale test loader. The registry holds 79 operations, `log` is present (in `image_processing_tools`,
  alias `laplacian_of_gaussian`), and `register_operation` does raise `TagCollision`. No operations were
  added to "fix" the count — that would have created the real collision the audit wrongly reported.
- Files: `src/pycat/utils/layer_tag_hook.py`, `tests/test_tag_registry.py`, `tests/test_tag_resolver.py`.

## [1.6.123] - 2026-07-18
### Changed — **Retrofit the linked-selection dock to the SelectionView contract.**
Continuing the Gap 5 retrofit (the table and the new pyqtgraph backend already conform): the dock's
`LinkedSelectionWidget` is now a `SelectionView` — `view_id`, `apply_selection(state)` (renamed from
`_on_selection`, alias kept), and a `close()` that **unsubscribes** and then Qt-closes. It is a
*receive-only* view (it renders the selected object's crop but never emits a command), so it needs no
programmatic guard.

- **Fixes a small lingering-subscription gap**: the outer wrapper's `close()` only removed the dock
  widget; the deferred subscription lived on (held weakly, so eventually GC'd, but never explicitly
  dropped). The wrapper now closes the widget, which unsubscribes.
### Notes
- Headless-tested: the widget satisfies the `SelectionView` protocol, applying a selection emits no
  command (receive-only), and `close()` unsubscribes. Existing dock tests green.
- Retrofit status: the emitting adapters (**table**, **pyqtgraph**) and the receive-only **dock** now
  conform to and are tested against the shared contract. The MSD-plot brushing works correctly but is
  registry/handler-based (rewritten in 1.6.120); formalizing it as a `SelectionView` class is a larger
  restructure of just-rewritten code — deferred rather than churned.

## [1.6.122] - 2026-07-18
### Added — **PyQtGraph 'explore' plot backend, built on the SelectionView contract.**
A fourth plot backend (alongside matplotlib/seaborn/plotly): a native-Qt interactive scatter. napari
is Qt, so a click is a Qt signal in the same event loop — no WebEngine bridge, low latency at large N.
matplotlib stays the export/publication backend; this is the interactive *explore* one.

- **`scatter(df, x_col, y_col, backend='pyqtgraph')`** returns a `PlotWidget` + `ScatterPlotItem`
  whose points map **1:1 to df rows in order** — it runs the same `_verify_row_order` guard the other
  backends do and REFUSES (`ok=False`) rather than wire a click that could land on the wrong object.
  `hue` colours per group but keeps ONE scatter item in row order (never the seaborn split-into-artists
  trap). `'pyqtgraph'` is in `BACKENDS` and `available_backends()`; the seam degrades with a message,
  not a crash, when the extra is absent.
- **Brushing is a proper `SelectionView`** (`PyQtGraphScatterView`): a click emits one command
  (`source_view`), an inbound selection highlights the matching points on a *separate overlay* item
  (O(1), not a recolour of N) under the `ProgrammaticGuard`, and `register_view` pushes current state
  on open. It passes the **same shared adapter contract** the table and the reference view pass — no
  second selection path. Camera-follow stays opt-in (the VPT-P3 no-loop lesson).
- **Optional dependency** `pip install pycat-napari[pyqtgraph]`; imported lazily, so PyCAT still
  imports and runs headlessly without it (the headless-import contract holds).
### Notes
- Rebuilt on `main` against the interaction-layer contract rather than merging the stale
  `pyqtgraph-backend` branch (which predated `SelectionView` and used the old bare-callback API — the
  exact rewrite the spec's ordering was meant to avoid). That branch is now superseded.
- Headless-tested: availability, backend registration, graceful absence, row-order 1:1, hue single-
  artist; and with Qt: the scatter maps to rows, an inbound selection highlights the overlay, and the
  adapter passes the SelectionView contract. Core: 1018 passed. **A live interactive click is worth a
  viewer once a UI opts a panel into this backend** — nothing uses it by default yet.

## [1.6.121] - 2026-07-18
### Added — **Interaction layer 5: a `SelectionView` adapter contract.**
Linked views used to be bare callbacks with no shared contract, so each re-invented apply / suppress /
cleanup and they drifted. This adds the contract, its mechanism, and a reusable test — the piece the
pyqtgraph plot backend should be built against.

- **`SelectionView` protocol** (`selection_service.py`): `view_id`, `apply_selection(state)`, `close()`.
- **`ProgrammaticGuard`** — the *primary* echo defence: `with guard.applying(): <render>` marks a
  programmatic update, and the view's outbound handler checks `is_applying` and bails, so rendering a
  selection never emits a new command (the service's `source_view` suppression stays as the second
  line). Re-entrant.
- **`register_view(service, view)`** subscribes the view AND pushes the current state, so a view opened
  while something is already selected reflects it — the initial apply is programmatic (emits nothing).
- **A reusable contract** (`tests/selection_view_contract.py`): programmatic apply emits no command, a
  user action emits exactly one, an unknown entity is safe, and `close()` unsubscribes. A reference
  adapter and the **retrofitted `BrushableTable`** (now a `SelectionView`: `apply_selection` + `close`,
  using the shared guard + `register_view`) both pass it. `detach()` stays as a back-compat alias.
### Notes
- Headless-tested: the guard is re-entrant, `register_view` pushes current state without bouncing a
  command, and both the reference adapter and the real Qt `BrushableTable` pass the shared contract.
  All existing selection/brushing tests green.
- Completes the interaction-layer spec's mechanism. Remaining, additive: retrofit the other views (MSD
  plot, VPT handlers, napari overlay, dock) to the protocol, then build/merge the pyqtgraph backend
  adapter against this contract.

## [1.6.120] - 2026-07-18
### Changed — **Interaction layer 4: MSD spaghetti background is one `LineCollection`; selection is an overlay.**
Hundreds of individual `Line2D` are slow to draw and force a per-artist style restore on every
selection. Both MSD renderers — the standalone `plot_msd_trajectories` and the consolidated panel's
`_draw_msd_into` — now draw the representative background as a **single `LineCollection`**, and a
selection (or a track brushed in from the table, Gap 3) is drawn as an **overlay `Line2D`** on top.
The background collection is never touched.

- **Hit-testing is on the coordinate ARRAYS**, not per-line artists (`_connect_nearest_curve_click_coords`
  + `_segment_distance_px_coords`), so collapsing the background costs nothing for interaction — same
  nearest-curve + click-to-cycle behaviour as before.
- **One implementation, shared:** the new `_msd_overlay_hooks` sets up promote/demote + the click
  hit-tester for BOTH renderers, so the standalone plot and the consolidated panel brush identically
  instead of from two divergent copies (the panel's bespoke blit + apply-pick + connect are gone).
  With one background artist a redraw is cheap, so selection uses a plain `draw_idle` — no blit
  bookkeeping. VPT's `_highlight_track_in_plot` was already overlay-aware (1.6.119) and needs no change.
### Notes
- Headless-tested: the background is exactly one `LineCollection` (not N `Line2D`); selecting a track
  adds one overlay and leaves the collection untouched; demote removes only the overlay; a non-sampled
  track promotes from the full frame; the log axes still frame the data (the `LineCollection`/log
  autoscale pitfall is checked); and the coords hit-tester matches the Line2D point-to-segment
  geometry. Consolidated-panel path smoke-checked end-to-end. **The live feel needs a viewer:** confirm
  clicking/​table-selecting a track in both the standalone and consolidated MSD plots still highlights
  correctly and feels responsive.
- Completes the interaction-layer spec's structural gaps (1=state 1.6.118, 3=promotion 1.6.119, 4=this;
  2=honest hit-testing was 1.6.100). Gap 5 (the `SelectionView` adapter contract, which lets the
  pyqtgraph backend be built correctly) remains as a separate pass.

## [1.6.119] - 2026-07-18
### Added — **Interaction layer 3: a track selected from the table shows even if it isn't in the MSD sample.**
The MSD spaghetti plot draws a fidelity-targeted representative subset (~100 of N), so a track picked
in the table that wasn't sampled had no curve to highlight — the bidirectional brushing quietly
couldn't reach it. Now the plot promotes it on demand.

- `plot_msd_trajectories` registers `promote(tid)` — draws a non-sampled track's curve on demand and
  returns its line — and `demote_line(line)` — removes it when it's deselected. A **sample line is
  never removed**; only a promoted focus curve is. The displayed set is effectively
  `representative_sample ∪ selected`.
- **VPT's `_highlight_track_in_plot` uses them**: when a selected track has no line it promotes one;
  when the previously-highlighted track was a promoted curve it demotes (removes) it instead of
  restyling. A promote/demote changes the line set, so it does a full redraw there rather than the
  blit fast-path (which assumes a fixed set); highlighting a sampled track still blits.
### Notes
- Headless-tested against the real `plot_msd_trajectories`: only the sample is drawn up front, a
  non-sampled track promotes (and is marked a focus curve), a sampled track's promote returns its
  existing line, demote removes a promoted curve but NEVER a sample line, and a track with no data
  promotes to nothing. **The live feel needs a viewer:** confirm selecting a table row for a track
  outside the sample now highlights its curve, and deselecting removes the promoted curve.
- Builds on 1.6.118's `SelectionState`. Gap 4 (`LineCollection` background) and Gap 5 (the
  `SelectionView` adapter contract) remain as separate additive passes.

## [1.6.118] - 2026-07-18
### Added — **Interaction layer 1: selection is now a hover / selected / pinned STATE.**
First increment of the interaction-layer spec. Selection was a single object — no multi-select, no
pinning while exploring, no independent hover. `SelectionService` now holds a `SelectionState`
(`selected: frozenset`, `primary`, `hovered`, `pinned: frozenset`, `generation`) and publishes the
whole state per change, with commands that produce a new one:

- `toggle(entity, source)` — ctrl-click to build a comparison set; `select_entity` — single select;
  `hover(entity, source)` — independent of selection; `pin`/`unpin` — survive a clear;
  `clear_selection(source)` — clears selected + hovered but **keeps pins** (Escape's semantics).
- **Back-compat is total.** `SelectionState` quacks like the old `Selection` (`entity_ids`,
  `primary_id`, `source_view`, `is_empty`), so every existing subscriber (the dock, the VPT views, the
  plots) and every existing test keeps working unchanged — the dispatch core (busy-guard, delayed
  release, deferred-debounce) is untouched, just extracted into `_publish` and shared by the old
  `select(Selection)` entry and the new commands.
### Notes
- Headless-tested: toggle add/remove, clear keeps pins, hover doesn't disturb selection, one command =
  one generation = one publish, a command reaches old subscribers via the back-compat interface, and
  the source view is skipped. All 72 existing selection/brushing tests still green.
- This is the keystone the pyqtgraph plot backend should be built against (its adapter must speak this
  state, not the old bare callback). Remaining interaction-layer increments (honest hit-testing —
  largely done in 1.6.100 via click-cycling; non-sampled track promotion; `LineCollection` background;
  the `SelectionView` adapter contract) are separate, additive passes.

## [1.6.117] - 2026-07-18
### Fixed — **CZI exit hang: force the exit from `atexit`, not `aboutToQuit` (which never fired).**
1.6.116's `QApplication.aboutToQuit` hook did not fix the hang — it is installed from the CZI-open
**worker thread**, where a cross-thread Qt `connect` is unreliable, and the hang is at Python
interpreter shutdown *after* `napari.run()` returns. Moved the guarantee to an **`atexit`** handler:
it runs on the main thread right before Python joins the JVM's non-daemon threads (exactly where it
hangs), so `os._exit(0)` there terminates cleanly. `aboutToQuit` is kept as a best-effort earlier
trigger.
- The handler prints `[PyCAT CZI] BioFormats JVM was open — forcing a clean process exit…` when it
  runs, so it is visible whether the fix engaged. Verified at the process level: the standalone reader
  fires the handler and exits `0`.
- Only the welcome-logo temp-file cleanup atexit is pre-empted (harmless), and only in a CZI session.
### Notes
- **Needs a viewer:** open the streaming `.czi`, close PyCAT — you should see that force-exit line and
  get the prompt back. If you close and do NOT see the line, `napari.run()` isn't returning on close
  and I'll hook the viewer's window-close event instead.

## [1.6.116] - 2026-07-18
### Fixed — **Closing PyCAT after a CZI now returns the terminal (force a clean exit).**
Headless mode (1.6.115) was not enough: something in the napari/Qt + BioFormats-JVM combination still
keeps the process alive at teardown — the window closes but the terminal never comes back. It cannot
be reproduced outside the GUI (a plain script exits fine), so rather than keep chasing which Java/Qt
thread refuses to die, PyCAT now forces a clean termination at the app's quit point: **once a CZI has
started the JVM**, `QApplication.aboutToQuit` flushes the streams and calls `os._exit(0)`. This only
arms in a CZI session (the JVM-start path) — every other session exits normally — and it runs after
other quit handlers, so it is the last thing before the process would otherwise hang.
### Notes
- **Needs a viewer:** confirm closing PyCAT after opening the streaming `.czi` returns the prompt.
- Keeps the 1.6.115 headless start (good practice regardless) and the scrubbing findings from
  1.6.113–115 (the stutters are inherent BioFormats seek latency).

## [1.6.115] - 2026-07-18
### Fixed — **Closing PyCAT after opening a CZI no longer hangs the terminal.**
Long-standing: after opening a streaming CZI, closing PyCAT left the process alive — the window shut
but the terminal never returned. Reading a CZI can make BioFormats touch Java AWT (colour models /
thumbnails), which spawns a **non-daemon AWT thread** that keeps the JVM — and the whole Python
process — running at shutdown. (A plain script exits fine because it never triggers AWT the way the Qt
app does, which is why it only bit inside `run-pycat`.) The JVM is now started **headless**
(`scyjava.config.enable_headless_mode()` → `-Djava.awt.headless=true`), so no AWT thread is ever
created; BioFormats reads pixels and metadata without it (verified: the reader still opens/reads the
real 8 GB file and the process exits cleanly).
### Diagnostics
- The `PYCAT_CZI_TRACE=1` readout now breaks latency into **worst lock-wait** and **worst openBytes**.
  On the real file this settled the scrubbing question: worst lock-wait **1–2 ms** (the prefetcher is
  not blocking foreground reads) and worst openBytes **~400 ms** — i.e. the intermittent stutters are
  BioFormats **seeking to distant frames**, an inherent random-access cost of this streaming CZI that
  caching cannot remove. The prefetch (1.6.114) is correct and harmless but only helps when scrubbing
  pauses or revisits cached frames; it cannot get ahead of a continuous drag through new frames.
### Notes
- **Needs a viewer:** confirm that opening the streaming `.czi` and then closing PyCAT returns the
  terminal prompt (no more reopening the terminal).

## [1.6.114] - 2026-07-18
### Changed — **CZI prefetch: foreground-priority + direction-aware (fixes back-and-forth scrubbing).**
An audit of 1.6.113's prefetch found it structurally wrong for anything but forward playback: it
published the current frame to the prefetcher only AFTER reading it, prefetched forward-only, and could
hold the reader's lock on obsolete frames while the UI waited on the one frame the user actually moved
to. Redesigned per that audit:

- **Foreground priority.** A read now publishes its request (target + a monotonic generation) and
  raises `_fg_pending` *before* it reads, so the background thread never starts a read while the UI is
  waiting, and abandons an obsolete read-ahead pass the moment a newer request arrives.
- **Direction-aware read-ahead.** The prefetcher follows the scrub: forward for a forward scrub,
  **backward for a backward scrub** (previously all-misses), a symmetric neighbourhood when direction
  is unknown or the frame is held, and a shallow ±2 on a large jump (no far speculation).
- **Buffer-layout guard.** `_read_plane_raw` now asserts the BioFormats plane byte-count matches
  `H·W·itemsize` and reports series/RGB/interleaved on mismatch — a wrong series or layout can be the
  wrong size in a way that still reshapes to a shifted image; this fails loudly instead.
### Notes
- Correctness investigation (the reported "seam"): BioFormats reports the streaming file as a **single
  series, single resolution, 500×500 uint16, non-RGB, non-interleaved**, and plane 0's buffer is
  **exactly** 500·500·2 bytes — so the reader selects the right series and the byte→array reshape is
  correct. The residual ~1.5% column-12 step and the anomalous row 0 are constant across frames and
  are in the acquisition, not the decode. No pixel-decoding defect.
- Benchmarked on the real 8 GB file at 25 fps: forward **40/40**, backward **40/40**, oscillate
  **36/36** frames served from cache (0 ms). **Needs a viewer:** confirm scrubbing actually feels
  smooth — set `PYCAT_CZI_TRACE=1` before `run-pycat` to print the real per-scrub cache hit-rate and
  read latency. If the trace shows high hit-rate but the viewer still lags, the bottleneck is napari's
  render path, not the reader.
- Deferred (audit #3): caching native uint16 for display and normalising to float32 only for analysis
  — would roughly double the cached temporal span, but it splits the display/analysis representation
  and cuts against PyCAT's uniform `[0,1]` loader contract, so it wants its own pass.

## [1.6.113] - 2026-07-18
### Changed — **Streaming CZI scrubbing is smooth: an LRU cache + background read-ahead.**
The direct BioFormats reader decodes ~5 ms/plane, which showed as intermittent stalls scrubbing the
15,766-frame movie frame by frame. The reader now caches planes and reads AHEAD.

- **Byte-budgeted LRU cache** (256 MB → 268 planes at 500², 16 at 2048²) so repeats and small back-and-
  forth scrubs are instant.
- **Background read-ahead**: a single worker thread decodes the next few frames (`_PREFETCH_AHEAD = 8`)
  ahead of the frame last accessed, and bails the moment the user moves on, so a forward scrub lands on
  already-decoded planes. Measured on the real 8 GB file: a 25 fps forward scrub was served **30/30
  from cache (0 ms)**, versus ~5 ms/plane cold.
- Every read (foreground + prefetch) is serialised on one lock — a loci `ImageReader` is not safe for
  concurrent `openBytes` — held per plane (~5 ms), so a foreground miss never waits long.
- **The prefetch thread detaches from the JVM whenever it goes idle.** A JNI thread that attached (via
  `openBytes`) and never detached blocks `DestroyJavaVM`, hanging the whole process at exit — found and
  fixed here; the process now exits cleanly.
### Notes
- Headless-tested: cache hit on repeat, read-ahead caches the frames ahead, the cache is byte-budgeted,
  and close stops the prefetcher. The reader was also run end-to-end on the real 8 GB file (opens,
  reads, prefetches frame 101 after frame 100, exits cleanly). **Needs a viewer:** confirm scrubbing
  the streaming `.czi` is now smooth with no intermittent stalls.
- The `@integration` real-file test hit an intermittent jpype `startJVM` access violation in this
  session's harness (unrelated to this change — the prefetch thread starts *after* JVM init, and it is
  deselected from the core suite); the reader is verified via the standalone benchmark above.

## [1.6.112] - 2026-07-18
### Fixed — **The CZI open no longer cancels itself.**
A regression in 1.6.111 (unreleased): opening the streaming CZI reported "CZI open cancelled" and
aborted on its own, with no user interaction. `QProgressDialog.close()` **emits `canceled`**, so when
the dialog closed on *normal completion*, the cancel handler fired and marked the load cancelled.

- The finish handler now marks completion (`done`), and the cancel handler ignores the
  `canceled` that `close()` emits once the work is done — only a real "Give up" click (or Escape/X)
  *before* completion cancels. Regression-tested (`test_busy_progress.py`, real Qt loop): a successful
  call returns its value instead of raising the cancellation.
### Notes
- Rolls up with 1.6.110 (dedupe + off-thread libCZI probe) and 1.6.111 (dialog auto-closes + "Give
  up"). **Needs a viewer:** confirm the streaming `.czi` now opens to completion on its own, and "Give
  up" still cancels cleanly.

## [1.6.111] - 2026-07-18
### Fixed — **The CZI "indexing" dialog now closes itself, and "Give up" actually works.**
From the viewer, on the streaming-CZI open dialog: it stayed open with the elapsed counter frozen, and
only advanced when the user X'd it out; there was no cancel button; and X-ing out early hung the UI.
All three are the same worker-dialog helper (`_run_with_busy_progress`), which had the exact bug the
newer `qt_worker` was built to avoid.

- **It closes when the work finishes.** `worker.finished` is emitted from the worker thread, and the
  old finish handler was a plain function — so Qt ran it *on the worker*, and `dlg.reset()` from there
  never ended the main thread's modal loop. The dialog hung open (frozen elapsed = work already done)
  until the user dismissed it. The handler is now a `QObject` slot that runs on the main thread (queued
  delivery), ending a `QEventLoop` with `loop.quit()`.
- **A "Give up" button that frees the UI.** The BioFormats index parse is a single uninterruptible JVM
  call, so cancel **detaches**: it stops waiting and lets the orphaned worker finish in the background
  (result dropped), instead of `thread.wait()` blocking the UI until the parse completes — which was
  the hang when X-ing out. The detached thread is retained until it finishes so it can't crash by being
  garbage-collected mid-run. Both CZI open sites report "CZI open cancelled." and abort cleanly.
### Notes
- Same fix benefits both CZI busy dialogs (the libCZI index probe and the BioFormats reader open).
  **Needs a viewer:** confirm the indexing dialog now closes on its own and the layer appears without
  X-ing out, and that "Give up" dismisses it and frees the window immediately.
- Still open, deliberately (secondary): the occasional scrubbing latency on the streaming movie —
  that's the prefetch/cache task (read T±k around the current frame), separate from this dialog fix.

## [1.6.110] - 2026-07-18
### Changed — **Opening a big streaming CZI no longer freezes the UI on the libCZI probe.**
The streaming-CZI reader (BioFormats, shipped 1.6.61) already opened its Java reader off-thread — but
the libCZI **metadata** open that routes to it ran on the Qt main thread, and for a 15,766-frame movie
parsing every subblock offset is ~11 s. Worse, it ran **twice**: once to decide the file needs
BioFormats, then again inside the streaming loader for pixel size / channel names. So ~20 s of "Not
Responding" preceded the (already responsive) BioFormats indexing dialog.

- **The two libCZI opens are deduplicated.** The routing probe (`probe_libczi`) now returns the libCZI
  image alongside its can-read verdict, and the streaming loader reuses it instead of re-opening —
  the multi-second subblock parse is paid once.
- **For a large CZI the probe runs off the Qt thread** behind the existing busy dialog, so even the
  first parse stays responsive. A small confocal/widefield CZI (a few MB, parses in milliseconds) still
  probes inline — a worker dialog would only flash. The gate is file size (`_CZI_OFFTHREAD_BYTES`,
  256 MB), which sits far below any streaming movie and far above any normal CZI.
### Notes
- No change to the reader itself or to normal-CZI behaviour (still libCZI, fast, no JVM). Verified: the
  streaming reader still opens and reads the real 8 GB file (integration test), and `probe_libczi`
  returns the image even when the pixel read fails (headless tests). **Needs a viewer:** confirm
  opening the streaming `.czi` shows the responsive indexing dialog from the start, with no initial
  freeze.
- Housekeeping: the CZI reader was fully built and shipped in 1.6.61 but never got a CHANGELOG entry;
  this documents the format is supported (confocal/widefield via libCZI; Zeiss fast-streaming via the
  opt-in `[bioformats]` extra).

## [1.6.109] - 2026-07-18
### Fixed — **QC on a long movie no longer OOMs; it assesses a bounded sample.**
With the IMS decode fixed (1.6.108), QC got further and then hit a *second* out-of-memory: `run_full_qc`
upcast the whole stack to **float64** (18.8 GiB for a 600×2048² movie), and even at float32 the
per-metric transients (`qc_snr`'s `np.diff` over every frame) are multi-GiB. Both were pre-existing and
independent of the off-thread work. Three parts:

- **QC now assesses an evenly-spaced sample of a long time series**, capped at `QC_MAX_FRAMES` (64).
  The UI reads **only those frames** off disk (`materialize_stack(max_frames=…)` indexes them via
  `__getitem__`), so a 600-frame movie costs ~1 GiB instead of ~18 GiB. QC is a health check, so an
  evenly-spaced sample across the acquisition answers it — and the report now carries a **"Frames
  assessed: N of M"** row that says so, and flags that the sampling lowers the rate the vibration check
  sees (drift, bleaching and focus are sampled across the whole run and unaffected).
- **`_to_float` casts to float32, not float64** — ample precision for every QC metric, half the memory,
  and a no-op (no copy) for a stack already decoded as float32.
- **The 3-D check reads the SHAPE, not a `.ndim` attribute.** The IMS readers advertise a `(T, Y, X)`
  shape but no `ndim`, so `getattr(_layer_data, 'ndim', 2)` read them as 2-D and fell to
  `np.asarray(wrapper)` — the lazy-guard refusal, i.e. the original crash. QC now derives 3-D from the
  shape and takes the decode path.
### Notes
- Headless-tested: `materialize_stack(max_frames=…)` returns evenly-spaced frames and reads ONLY those
  (endpoints included); `_to_float` is float32 and copy-free; the QC report adds the sampling note only
  when it actually subsampled. **Needs a viewer:** confirm QC now completes on the 600-frame .ims with
  the report showing "64 of 600 frames".
- **Judgment call worth your eye:** the sample is *strided* (spans the whole acquisition), which keeps
  drift/bleaching/focus honest but lowers the vibration check's frequency range. If you'd rather QC use
  a contiguous native-rate window (vibration correct, drift only over the window) or raise the 64-frame
  cap, say so — both are one-line changes.

## [1.6.108] - 2026-07-18
### Fixed — **`materialize_stack` could not read the IMS readers (QC crashed on an .ims stack).**
Running QC (or any full-stack analysis) on a lazy `.ims` movie raised
`RuntimeError: An implicit full-stack read was attempted on _ImsReaderTYX`. **A pre-existing bug the
1.6.107 off-thread change surfaced** by re-raising it cleanly instead of swallowing it: the old QC
code called the same `materialize_stack`.

- `materialize_stack` is the *sanctioned* full-read path, but for a lazy wrapper without
  `as_full_array` it fell through to `np.asarray(stack_like)` — and the IMS readers' `__array__` now
  **refuses** an implicit full read (`lazy_guard.refuse_implicit_full_read`) rather than truncating to
  one frame, so the blessed reader raised the very error it exists to prevent. It now reads any 3-D
  indexable wrapper **frame by frame via `__getitem__`** (guard-safe, the same access the guard's own
  message points to), keyed on shape before it ever touches `np.asarray`. Plain numpy / dask /
  `as_full_array` wrappers are unchanged. Regression-tested with a wrapper that refuses `__array__`.
### Changed — **Data Quality Control moved to the top level of Analysis Methods.**
It was tucked inside **Toolbox → Data Visualization**, which is both hard to find and conceptually
wrong — QC is the first thing you do to a dataset, not a plot. It is now a top-level item in the
**Analysis Methods** menu, next to Exploratory Analysis. (Per-frame **Frame Quality / Focus QC** stays
under Data Visualization; that is the different, per-frame scorer.)
### Notes
- Headless-tested: `materialize_stack` reads a guard-refusing 3-D wrapper frame-by-frame, preserves
  label dtype, and drives the progress callback. **Needs a viewer:** confirm QC now runs on the .ims
  stack (with the modal decode dialog), and that Data Quality Control appears at the top of Analysis
  Methods.

## [1.6.107] - 2026-07-18
### Changed — **Every widget's stack decode runs off the Qt thread now.**
The other half of 1.6.106. Fourteen sites across eight widgets decoded a lazy stack with
`materialize_stack` on the Qt main thread — the 1.6.81/82 progress bars made that wait visible (a
synchronous `repaint()` advances the bar) without making it shorter, so the window could still say
"Not Responding" while the bar moved. All fourteen now decode through a worker.

- **New `qt_worker.materialize_off_thread(layer.data, viewer=…, **kw)`** wraps `materialize_stack` in
  `run_with_progress`: the decode runs on a `QThread` behind a modal dialog, and the array comes back
  on the caller's thread — safe to hand straight to analysis, exactly as before. `dtype=` and any other
  kwargs pass through unchanged.
- **Converted:** FRAP (recovery + pre-bleach), condensate-physics (fusion + QC), data-QC,
  brightfield (dynamics + focus-QC), in-vitro fluorescence (dynamics + intensity + QC), in-vitro
  brightfield (dynamics + focus-QC), fusion (image mode), and the temperature module's shared cached
  `_get_stack` (which froze once, on whichever section was clicked first). The inline `PhasedProgress`
  bars for the decode phase are retired in favour of the modal dialog.
- **Not converted:** FRAP's 2-D per-candidate scan (`_offer_stack_2d_images`) — it decodes single 2-D
  frames in a loop, where an off-thread dialog would flash once per candidate. It stays synchronous and
  is the one excused entry.
### Notes
- The progress-rollout ratchet (`test_progress_rollout.py`) is rewritten for the new contract: a
  `*_ui.py` that decodes a stack **directly** (synchronously, on the Qt thread — bar or no bar) now
  fails; the way to pass is to route it through `materialize_off_thread`. The countdown is at zero.
- Headless-tested: the helper decodes via `materialize_stack` on the worker, passes kwargs and a
  callable progress callback, and survives a viewer with no Qt window; plus the five real-thread
  integration tests (work off-main, value back on-main, progress crosses to main, errors re-raise,
  threads cleaned up). **The per-widget feel needs a viewer** — confirm a dynamics/QC/FRAP run on a
  long stack shows the modal dialog and no longer says "Not Responding".

## [1.6.106] - 2026-07-18
### Changed — **Session load runs off the Qt thread — no more "Not Responding".**
Loading a session lagged the UI (you reported it; Windows shows "Python is not responding" on a longer
one) because `load_session` did its slow work — `tifffile.imread` per derived layer, `pd.read_csv` per
table — on the Qt main thread. The 1.6.81/82 progress bars made that wait *visible* without making it
*shorter*. This is the other half: the read moves to a worker thread while a modal dialog keeps the
window painting.

- **`load_session` is split into a read half and an apply half.** `_read_session_payload` does the
  decode and the CSV reads and touches **no viewer** (structurally — it has no viewer parameter), so it
  is safe to run on a `QThread`. `_apply_session_payload` creates the napari layers and writes the data
  repository, always on the caller's thread — because `viewer.add_*` off the main thread is a crash,
  not a freeze. `load_session` orchestrates the two via `pycat.utils.qt_worker.run_with_progress`.
- **The UI wiring** (`_open_session_loader`, and the quick "restore latest" path) passes
  `use_worker=True`. The worker owns a modal `QProgressDialog`; the old in-dialog progress bar is
  retired so there aren't two bars for one operation. Headless callers and tests default to
  `use_worker=False` (synchronous) and are unaffected — `run_with_progress` also falls back to
  synchronous when there is no running Qt app.
- **`qt_worker.run_with_progress`** (new, `pycat/utils/qt_worker.py`) runs a function on a `QThread` and
  returns its value **on the caller's thread**, re-raising exceptions there so existing `try/except`
  still works. It deliberately refuses a callback/future API so nobody is tempted to create a layer in
  the worker. Two subtle bugs in the pattern it replaces are fixed in it: a fast worker finishing before
  `exec_()` is entered (deadlock), and a signal-to-plain-function running the slot on the worker thread
  (off-main widget touch). Both were headlessly tested.
### Notes
- Headless-tested: the read half takes no viewer and decodes into a payload; the apply half is the only
  half that calls `viewer.add_*`; the synchronous round trip is unchanged; the worker helper's deadlock
  and thread-affinity fixes. **The off-thread feel needs a viewer** — confirm a real multi-file session
  restore no longer says "Not Responding" and the modal progress dialog advances while the window stays
  responsive.
- This also stages `load_session` (was 149 lines, over the complexity ceiling) into per-phase helpers —
  the prerequisite the roadmap called out either way. The same `qt_worker` helper now exists to move the
  per-widget `materialize_stack` freezes off-thread next (the other half of the same fix).

## [1.6.105] - 2026-07-18
### Changed — **The picked-track highlight is a Tracks layer at 2× the base width.**
From the viewer: after zooming to the bead, the picked-track line was still too thick to read the
trajectory's detail. The cause was a unit mismatch — the highlight was a Shapes path whose width is in
**data units**, so it ballooned as the new zoom-to-bead magnified the view, while the base "Bead
Trajectories" layer (a napari Tracks layer) has its width in **screen pixels** and stays constant.

- **The picked track is now a Tracks layer**, the same type as the base, so its `tail_width` is in
  screen pixels and no longer fattens at deep zoom. The width is exactly **2× the base**
  (`_PICKED_TRACK_TAIL_WIDTH = 2 · _BASE_TRACK_TAIL_WIDTH`, both new constants) — bold enough to stand
  out, thin enough to read the detail — which is what the user asked for by eye.
- **Still orange, still a separate overlay.** It colours via a registered flat-orange colormap
  (`#ff8c00`) rather than recolouring the base layer, so a user's own track colouring is never
  clobbered by a pick. `tail_length`/`head_length` span the whole track so it draws fully at any
  frame, including the bead's first frame. Falls back to a thin Shapes path only if `add_tracks` is
  unavailable.
### Notes
- Headless-tested: the picked track is a Tracks layer at 2× the base width, orange, and spans its full
  frame range. **The zoom-stable feel is UI-coupled** — confirm the line reads well at the zoom-to-bead.

## [1.6.104] - 2026-07-18
### Changed — **A VPT plot click now goes to the bead; the pulse is gone.**
From the viewer, on the picked track: the opacity slider oscillated continuously with no visible glow,
the highlight line was too bold to see detail through, and a click should take the stack to the bead's
z-slice and zoom in. Three fixes.

- **A plot click navigates to the bead — on by default.** `_navigate_to_bead` steps to the bead's
  frame, centres on it, and **zooms** so a small window (`_BEAD_ZOOM_WINDOW_PX = 80 px`) around it fills
  the view. Navigation was gated off while the plot-click loop existed; with one `button_press` per
  click (1.6.100) and the `_revealing` re-entrancy guard, the camera move is safe, so going to the bead
  — what the user asked a click to do — is the default now. VPT's now-unused `_follow_enabled` wrapper
  was removed; the generic brushing path keeps its own for the `follow_selection`/double-click case.
- **The pulsing ring was removed.** `_pulse_layer` armed a QTimer that oscillated the ring's
  size/opacity. But the ring is per-frame — present only on the bead's own frame — so scrubbing away
  left nothing to pulse while the opacity slider churned on for nothing. The ring is a static hollow
  marker now (`size=12, opacity=0.9`); the zoom-to-bead navigation is what draws the eye.
- **The picked-track highlight was thinned**, `_PICKED_TRACK_WIDTH_PX` 1.0 → 0.4, so the trace no
  longer obscures the trajectory detail underneath it.
### Notes
- Headless-tested: the pick navigates (steps + centres) and marks the track, the reveal stays
  re-entrant-guarded so navigating cannot loop, the ring is static with no timer armed, and the removed
  symbols are recorded in `_DELIBERATE`. **The zoom-to-bead feel is UI-coupled and needs a viewer** —
  confirm a plot click lands on the bead at a sensible zoom and the thinner line reads well.

## [1.6.103] - 2026-07-18
### Added — **Session auto-restore: a load reopens the analysis method and rebuilds its view.**
Loading a session restored the dataframes into the repository but left an empty panel — the user had
to reopen the method and re-Compute by hand. Now a load lands back at the working state.

- **The active method is recorded on save.** The manifest gains `active_method` (the open analysis
  UI's class name), written by `write_session_outputs`.
- **The loader surfaces it**, and `_on_load` reopens that method via its `_switch_to_*` handler.
  Switching methods **preserves the data repository**, so the reopened method sees the restored data.
  A session saved before this was recorded has no `active_method`; the method is then inferred from a
  signature dataframe (`vpt_tracks` → VPT), so existing sessions restore too.
- **The reopened method rebuilds its view.** `VideoParticleTrackingUI.restore_session_view` rebuilds
  the trajectory + pickable layers and calls `_on_rheology` — the exact handler the **Compute MSD &
  Viscosity** button runs, which reads `vpt_tracks` from the repository — so the MSD/moduli plots come
  back through the one real render path, not a divergent copy. The slow part of VPT (detection +
  linking) is not redone; recomputing the MSD from the restored tracks is seconds.
### Notes
- Headless-tested: the manifest records/surfaces `active_method`, back-compat returns None (inferred
  from data), the method registry wires VPT correctly, and the restore hook exists. **The end-to-end
  reopen → rebuild → plots is UI-coupled and needs a viewer** — this is the part to confirm: load the
  session and check the VPT method reopens with its tracks clickable and its plots drawn.
- Parameters return at their defaults (frame interval auto-fills from the source metadata); a user who
  needs the session's exact bead radius/temperature sets them and re-Computes. Restoring the exact
  recorded parameters is a later refinement.
- Only VPT has a `restore_session_view` so far; other methods reopen (data preserved) and show a
  "reopen to rebuild" toast until they gain the same hook — additive, method by method.

