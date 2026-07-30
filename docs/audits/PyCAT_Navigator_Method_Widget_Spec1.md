# PyCAT — Navigator → Generated Method Widget

**Spec 1 (build now) + roadmap for Specs 2–6**

*Written against the local tree at **1.6.422**. Note: the audit PDF describes a newer revision (12
Navigator adapters vs. the 8 in my tree, ~349 source files vs. 348), so adapter counts below may lag.
The architecture the spec depends on — the `_add_*` section vocabulary, the panel/dock lifecycle, the
menu tree, the op catalog — was read directly from source and is stable across that gap.*

---

## The problem, stated plainly

You're right about the navigator, and the audit over-rates it. What "▶ Run analysis" does today
(`ui/navigator_dock.py:375–420`):

1. Renders `plan_rows(plan, ctx)` as a **read-only list of labels** with quality caveats.
2. Offers a parameter review.
3. On Run: executes whichever steps have an `ExecAdapter`, then reports —
   verbatim from `navigator_dock.py:207` — *"Run these from their method panels, in order: X, Y, Z."*

So the artifact is **a list and an instruction to go do it yourself**. Nothing persists; close the dock
and the plan is gone (the template save keeps the *answers*, not a usable panel). For a scientist who
already knows their workflow it is slower than opening the panel directly, and for one who doesn't, it
hands them homework. Your assessment — nobody will use it — follows directly from that.

The audit reads the adapter work as the achievement. The adapters are genuinely good engineering, but
they answer *"can the planner execute?"* when the user's question is *"can I get a workflow I can
keep, inspect, adjust, and re-run?"* Those are different products.

## The finding that makes this tractable

**A PyCAT method panel is already just an ordered sequence of section builders.** From
`CondensateAnalysisUI.setup_ui`:

```python
self._add_workflow_header(self.condensate_layout, include_pixel_gate=True)
tfu._add_measure_line(layout=self.condensate_layout)
tfu._add_run_upscaling(layout=self.condensate_layout)
tfu._add_pre_process(layout=self.condensate_layout)
tfu._add_run_cellpose_segmentation(layout=self.condensate_layout)
tfu._add_run_cell_analysis_func(layout=self.condensate_layout)
tfu._add_run_segment_subcellular_objects(layout=self.condensate_layout)
tfu._add_run_puncta_analysis_func(layout=self.condensate_layout)
tfu._add_spatial_metrology(...); tfu._add_advanced_analysis(...); tfu._add_condensate_physics(...)
tfu._add_save_and_clear(layout=self.condensate_layout)
```

There are **66 such `_add_*` builders**, all with the uniform signature
`(self, layout=None, separate_widget=False)`. Each one already produces complete, working controls —
layer dropdowns (with the tag-resolver `binding=`), status circles, tooltips, and run buttons.

**Therefore: generating a method widget requires no new per-step UI code.** A plan is an ordered list of
steps; a panel is an ordered list of sections. The only missing piece is a mapping from one to the
other. That is the whole of Spec 1.

---

# SPEC 1 — Make the Navigator generate a real, dockable method widget

**Goal:** "Build method" produces an actual PyCAT analysis panel — indistinguishable from a hand-written
one — containing exactly the steps the plan selected, in execution order, docked through the normal
panel lifecycle.

**Explicitly out of scope for Spec 1** (they are Specs 2–5): saving it as a named method, the menu
entry, the pop-out guidance, live re-selection of tools. Spec 1 ends when the widget appears, works,
and can be used to complete the analysis by hand.

> **STATUS (1.6.432): 1.1 + 1.5 SHIPPED (the headlessly-verifiable foundation).** `section_bindings.json` (22
> verified bindings, schema_version 1), `navigator/sections.py` (`section_for` / `mapped_op_ids` /
> `builder_for`, refuse-to-guess), and `tests/navigator/test_section_coverage.py` (5 `base` tests: well-formed,
> builders-exist-via-AST, floor 22, `builder_for` contract, canonical-plan-ops-declared) are in. Keying is by the
> REAL id a `PlanStep` carries — catalog op-ids for enhancement/segmentation, measure-op ids
> (`feature_analysis.cell_analysis`, `pixel_wise_corr.pearson_manders`) for analysis — which corrected a few of
> this spec's example rows (`background_removal` → `rolling_ball`, `cell_analysis` → `feature_analysis.cell_analysis`)
> that predated the current catalog. `_KNOWN_GAPS` = {`data_qc.assess`, `acquisition`}. **Remaining in Spec 1:**
> 1.2 (`GeneratedMethodUI`), 1.3 (parameter seeding), 1.4 (dock "🛠 Build method panel"), 1.6 (acceptance) — all
> GUI-bound (Qt panel + dock lifecycle), so they build on this foundation but are not headlessly verifiable.
>
> **UPDATE (1.6.434): 1.2 + 1.3 + 1.4 SHIPPED.** `ui/generated_method_ui.py::GeneratedMethodUI` (subclasses
> `AnalysisMethodsUI`; `setup_ui` walks `resolve_plan_sections`, calls each builder, renders `placeholder_text`
> for gaps; seeds reviewed params into the repo before building; stores provenance). Dock: primary "🛠 Build
> method panel" (`build_method_panel_via_central_manager`), gated by `run_blocked_reason`; the executor path is
> now the secondary "▶ Run the steps that support it". `placeholder_text` is headlessly tested; both GUI modules
> compile + import. **STILL OPEN: 1.6 acceptance** — a manual napari run (answer → Build → confirm each section
> is live and matches the hand-written `CondensateAnalysisUI` outputs). That is the one piece I cannot verify
> headlessly; everything feeding it is tested.

## 1.1 — The missing mapping: op-id → section builder

This is the fifth naming system in the stack (the audit's point 7 is real: question-tree step IDs,
op-catalog IDs, module names, batch step IDs, adapter keys — and now UI sections). Do **not** add
another runtime-derived mapping. Make it **explicit data**, following the `layer_bindings.json`
precedent the resolver already uses.

Create `src/pycat/navigator/data/section_bindings.json`:

```jsonc
{
  "schema_version": 1,
  "sections": {
    "cellpose":              {"builder": "_add_run_cellpose_segmentation", "owner": "toolbox_functions_ui"},
    "subcellular_segment":   {"builder": "_add_run_segment_subcellular_objects", "owner": "toolbox_functions_ui"},
    "background_removal":    {"builder": "_add_run_rb_gaussian_background_removal", "owner": "toolbox_functions_ui"},
    "preprocess":            {"builder": "_add_pre_process", "owner": "toolbox_functions_ui"},
    "cell_analysis":         {"builder": "_add_run_cell_analysis_func", "owner": "toolbox_functions_ui"},
    "puncta_analysis":       {"builder": "_add_run_puncta_analysis_func", "owner": "toolbox_functions_ui"},
    "bandpass":              {"builder": "_add_run_fft_bandpass", "owner": "toolbox_functions_ui"},
    "clahe":                 {"builder": "_add_run_clahe", "owner": "toolbox_functions_ui"},
    "local_threshold":       {"builder": "_add_run_local_thresholding", "owner": "toolbox_functions_ui"}
    // … one row per op-id that has a UI section
  }
}
```

- **Keyed by op-catalog `op` id** (the 94 entries in `navigator/data/operation_catalog.json`), because
  that is what a `PlanStep` carries. Not module names — that indirection is what makes the current
  adapter keying brittle.
- `owner` names the object holding the builder (`toolbox_functions_ui`, or a mixin reachable from it),
  so the loader resolves `getattr(getattr(central_manager, owner), builder)`.
- **Schema-versioned from day one** (audit point 17). Every persisted structure this spec introduces
  carries `schema_version`.

Add `navigator/sections.py` with a small, defensive loader:
```python
def section_for(op_id: str) -> dict | None: ...          # None when unmapped — never guess
def builder_for(central_manager, op_id): ...             # resolves to a bound callable, or None
def mapped_op_ids() -> frozenset[str]: ...               # for the coverage ratchet (1.5)
```
`builder_for` must return `None` — never raise, never fall back to a name-similarity guess. The
adapter registry already refuses to guess execution signatures and the audit rightly praises that; the
same discipline applies here.

## 1.2 — The generated panel

Add `ui/analysis_panels/generated.py` (or `ui/generated_method_ui.py` while `analysis_methods_ui.py` is
still monolithic — see the decomposition spec):

```python
class GeneratedMethodUI(AnalysisMethodsUI):
    """A method panel assembled from a Navigator plan.

    A PyCAT method panel is an ordered sequence of `_add_*` section builders. A Navigator plan is an
    ordered sequence of steps. This class is the join: it walks the plan in execution order and calls
    the bound builder for each step into one layout. Because every builder already produces complete
    controls (layer dropdowns with tag bindings, status circles, tooltips, run buttons), the result is
    a fully functional panel with no per-step UI code.
    """
    def __init__(self, viewer, central_manager, plan, *, review=None, name=None): ...
    def setup_ui(self): ...
```

**Decisions, made rather than offered:**

1. **It subclasses `AnalysisMethodsUI`, not a bespoke container.** It inherits the workflow header, the
   pixel-size gate, the dock lifecycle, and the save/clear footer for free, and it docks through the
   same `_switch_analysis(DataClass, UIClass, ...)` path as every other panel — so nothing downstream
   needs to know it was generated.
2. **Order is the plan's execution order**, which the executor already computes. Do not re-derive it.
3. **The header and footer are always present**: `_add_workflow_header(..., include_pixel_gate=True)`
   first, `_add_save_and_clear(...)` last. Every hand-written panel has them and the generated one is
   not special.
4. **Unmapped steps render a visible placeholder**, never silently vanish:
   ```
   ⚠  Spatial metrology — no panel section is wired for this step yet.
       Run it from the Spatial Metrology panel, then continue here.
   ```
   The step name, the reason, and where to go. A generated panel that quietly drops a step the plan
   said was necessary would be a scientific-integrity failure, not a UI gap.
5. **The panel records its provenance**: the plan, the intent/answers, and the reviewed parameters are
   stored on the instance (`self._plan`, `self._intent`, `self._review`) — Spec 2 persists them, Spec 4
   uses them to rebuild in place. Store them now even though nothing reads them yet; retrofitting
   provenance is always worse.

## 1.3 — Parameter seeding

The plan's reviewed parameters (`_add_param_review`, already in the dock) must reach the generated
sections. The builders read their defaults from the data repository / `central_manager` state, so:

**Write the reviewed values into the repository *before* calling the builders**, then build. Each
section then constructs already seeded. Do not attempt to reach into constructed widgets and set values
afterward — that would require per-builder knowledge and would break the moment a builder changes.

Where a reviewed parameter has no repository home, skip it and log via `debug_log` — do not invent a
storage location. Note the gap; Spec 6 (typed results / operation service) is where parameter plumbing
gets done properly.

## 1.4 — Wiring the dock

In `navigator_dock.py`:

- **Primary action becomes `🛠 Build method panel`.** This replaces "▶ Run analysis" as the headline
  button, because building is the useful outcome and running-a-list is not.
- Keep the executor path as a **secondary** action, labelled honestly for what it does — e.g.
  `▶ Run the steps that support it` — with the existing `needs_panel` reporting. It is genuinely useful
  for the fully-adapted chains (cell analysis, brightfield, in-vitro) and there is no reason to delete
  working code. Spec 4 folds run-all *into* the generated widget, where it belongs.
- On Build: construct `GeneratedMethodUI`, dock it via the standard `_switch_analysis` path, and close
  or collapse the navigator dock (the user has what they came for).
- Keep `run_blocked_reason()` gating on Build too — if the plan can't be trusted (uncalibrated pixel
  size, missing channel), say why rather than building a panel that will produce a wrong number.

## 1.5 — Coverage ratchet

Not every one of the 94 catalog ops needs a section — some are batch-only or have no interactive form.
But the *mapped* fraction should only grow, and unmapped steps should be visible.

Add `tests/navigator/test_section_coverage.py` (mark `core`; pure JSON + AST, no Qt):
```python
_SECTION_COVERAGE_FLOOR = <count at landing>   # may only go UP

def test_every_mapped_builder_exists():
    """A binding naming a builder that no longer exists yields a silently missing step.
    The builder name is a contract; check it against the real methods."""
    # AST-scan ui/ for `def _add_*`; assert every section_bindings builder is among them.

def test_section_coverage_does_not_SHRINK():
    """Generated panels are only as good as the mapping. This is a ratchet, like
    test_complexity_budget: coverage may grow, never regress."""
    assert len(mapped_op_ids()) >= _SECTION_COVERAGE_FLOOR

def test_planner_ops_that_lack_sections_are_declared():
    """Any op a canonical plan can select but that has no section must appear in a KNOWN_GAPS
    list — so 'this step has no UI' is a recorded decision, not a surprise at build time."""
```
The third test is the important one and mirrors the route-equivalence harness's declared-gap
discipline, which the audit correctly calls out as one of PyCAT's strongest test ideas.

## 1.6 — Acceptance criteria

Spec 1 is done when, for the **cell/condensate** pipeline (the best-covered chain):

1. Answer the navigator questions → **Build method panel** → a docked panel appears.
2. It contains the workflow header + pixel gate, one section per planned step in execution order, and
   the save/clear footer.
3. Every section is **fully functional** — dropdowns populated (tag-resolver bound), status circles
   live, run buttons work.
4. Running each section top-to-bottom completes the analysis and produces the same outputs as the
   hand-written `CondensateAnalysisUI` on the same data.
5. Steps with no section show the placeholder, naming the panel to use instead.
6. `test_section_coverage.py` passes; the coverage floor is recorded.

Point 4 is the real bar: **a generated panel and the equivalent hand-written panel must produce the same
result on the same input.** That is route equivalence applied to the generated route, and it belongs in
the existing harness as `manual ≈ generated`.

---

# Roadmap — Specs 2–6

Each is written thoroughly when you green-light it. Scoped here so the order is deliberate.

### Spec 2 — Persist the generated method; Custom Methods in the analysis tree
> **STATUS (1.6.437): persistence core SHIPPED.** `GuidedTemplate` already stored the answers + step/section list
> + reviewed parameters; this added the two Spec-2 pieces on top, headlessly verified: `schema_version`
> (`_SCHEMA_VERSION = 1`, written into every entry, backward-compatible read of pre-versioning saves) and
> `duplicate_template` (keep-the-original copy, refuses overwrite/missing/blank) — completing the Custom Methods
> CRUD (delete/rename existed).
> **UPDATE (1.6.438): Spec 2 COMPLETE.** `plan_from_saved_method` (session.py, headlessly tested) recompiles a
> saved method against live data via `context_from_session`; the Custom Methods submenu
> (`ui/custom_methods_menu.py` + a one-line `menu_manager` hook, kept out of that concentration point) lists saved
> methods dynamically (`aboutToShow`) and rebuilds each into a `GeneratedMethodUI`. The submenu shares the panel's
> 1.6 manual acceptance; its rebuild logic is fully tested. Remaining Method-Widget roadmap: Spec 3 (guidance
> content — needs the scientist's authoring), Spec 4 (embedded pop-out guidance), Spec 5 (comparative chooser),
> Spec 6 (execution kernel).
The saved artifact: plan + intent/answers + reviewed parameters + section list + `schema_version`.
Extends the existing `GuidedTemplate` machinery (`navigator/templates.py` already has
`save_template` / `list_templates` / `load_template`) rather than inventing a second store. Adds a
**`Custom Methods` submenu** to the analysis-methods tree — the tree is a static dict in
`menu_manager._setup_menu_bar` (line ~830), so this becomes the first *dynamically populated* submenu,
rebuilt from the saved-method store. Deleting/renaming/duplicating a saved method belongs here too.
**Depends on:** Spec 1.

### Spec 3 — The guidance content model *(the gating item — this is a curation project)*
> **STATUS (1.6.439): INFRASTRUCTURE SHIPPED; content is yours to author.** `operation_guidance.json`
> (schema-versioned, ships empty), `navigator/guidance.py` (reader `guidance_for` refuse-to-guess +
> `authored_op_ids`; authoring vehicle `generate_guidance_workbook` / `ingest_guidance_workbook`), and
> `tests/navigator/test_guidance_coverage.py` (5 `base` — well-formed, real-op + valid-field guard, refuse-to-guess,
> coverage ratchet floor 0, workbook round-trip). To author: run `generate_guidance_workbook`, fill the judgement
> columns (when_to_use / advantages / limitations / alternatives / not_applicable_when / references), then
> `ingest_guidance_workbook`, and raise the ratchet floor. The scientific content — the deliverable that gates
> Specs 4–5 — is yours; the machine will never invent it.
**Honest finding:** the op catalog's 94 entries carry only a terse one-line `summary` ("FFT bandpass
filter"). The content your vision needs — *when to use this, its advantages, its limitations, why it is
or isn't relevant here* — **does not exist anywhere in the codebase and must be authored.**

Spec 3 defines `navigator/data/operation_guidance.json` (schema-versioned):
```jsonc
"cellpose": {
  "when_to_use": "...", "advantages": ["..."], "limitations": ["..."],
  "alternatives": ["stardist", "local_threshold", "watershed"],
  "not_applicable_when": ["..."], "references": ["..."]
}
```
and the authoring vehicle: a curation workbook generated from the catalog (the same
fill-the-yellow-columns pattern used before), read back into the JSON. **This is your writing, not
mine** — the scientific judgment about when Cellpose beats thresholding is yours. I can generate the
workbook, define the schema, and wire the reader; the content is the deliverable you supply.
**Depends on:** nothing technical. Can start in parallel with Spec 1. **Gates Specs 4–5.**

### Spec 4 — The embedded navigator: pop-out guidance and live revision
The core of your vision. Each section in a generated panel gets an affordance (a `?`/`⚙` on the section
header) that pops out the relevant slice of the guide tree *in place*: for a segmentation step, the
available segmenters with advantages/limitations; for enhancement, every image operation with its
tradeoffs; for analysis, the applicable analyses and why each is or isn't relevant to the current data.
Choosing a different tool **revises the panel on the fly** — the section is replaced and the panel
rebuilt from the amended plan, preserving the rest. This is where the navigator stops being a wizard
you exit and becomes the panel's permanent editing surface.
**Depends on:** Specs 1, 2, 3.

### Spec 5 — Comparative step chooser ("why this and not that")
> **STATUS (1.6.440): the reasoning CORE shipped (headless).** `Planner.explain_terminal_choice(intent, ctx)`
> surfaces, per observable, the terminal candidates + their scores (in_vitro bonus, target specificity,
> preference) + the winner — the planner's own reasoning, reused from `_pick_terminal` so it cannot drift (guard
> test). It shows e.g. `vpt.microrheology` beating the higher-preference generic biophysics fit on bead
> specificity. Tests in `tests/navigator/test_navigator_selection_explain.py`. STILL OPEN: the GUI pop-out that
> renders this side-by-side (needs the Spec 4 surface), and enriching it with Spec 3's authored guidance once
> that content exists. This delivers the data; the presentation is the remaining GUI-bound part.
The narrower, higher-value slice of Spec 4: at a decision point, show the alternatives **side by side
with the planner's reason for its selection**, scored against the actual data context (dimensionality,
modality, calibration, SNR). This is the anti-black-box payoff — the user sees not just what was chosen
but the reasoning and the runner-up.
**Depends on:** Spec 3 (content), Spec 4 (pop-out surface).

### Spec 6 — Converge on one execution kernel *(your batch point)*
> **STATUS (1.6.442): STARTED — first family migrated, proof shipped, stopping for review.**
> `pycat.kernel.OperationService.execute(op_id, inputs, params) -> AnalysisResult` is the kernel; the first
> family, background removal (rolling-ball), is migrated and its route-equivalence row extended with a `kernel`
> route asserted bit-identical to `headless ≈ batch ≈ session` (Workflow 1). An unmigrated op raises rather than
> reroutes. `tests/test_operation_service.py` pins the contract. Next families each register a kernel and close
> their own `≈ kernel` row (cellpose segmentation, cell_analysis, …), one per increment — reviewing this proof
> before continuing the migration.
I agree with the audit here and so do you. `OperationService.execute(...) → AnalysisResult`, sitting
**below** batch, Navigator, generated panels, manual panels, and headless. Batch handlers keep
workflow/persistence concerns (paths, output dirs, naming) and stop being the de facto scientific API.

Two things this spec should say that the audit doesn't:
- **The generated widget is the forcing function.** Once a panel can be *generated* from a plan, the
  gap between "what the UI does" and "what the adapter does" becomes intolerable and visible — the same
  step must behave identically in both. That pressure is what makes the kernel migration concrete
  rather than aspirational.
- **Do it per operation family, behind route-equivalence tests**, not as a big-bang refactor. The
  existing harness already encodes `manual ≈ batch ≈ Navigator`; add `≈ generated` and let each
  migrated operation close its row.
**Depends on:** Spec 1 (for the pressure); otherwise independent and can proceed in parallel.

---

## Sequencing

| Order | Spec | Why here |
|---|---|---|
| 1 | **Spec 1** — generate the widget | The unlock. Turns a list into an artifact. Small, because the 66 builders already exist. |
| 1b | **Spec 3 content authoring** (parallel) | Long lead time, needs your scientific judgment, gates 4–5. Start the workbook while Spec 1 is built. |
| 2 | **Spec 2** — persist + Custom Methods menu | Makes the generated panel a durable object rather than a session artifact. |
| 3 | **Spec 4** — embedded pop-out guidance | The vision. Needs 1+2+3 in place. |
| 4 | **Spec 5** — comparative chooser | Refines 4 with the reasoning surface. |
| — | **Spec 6** — execution kernel | Parallel track; per-family, test-gated. |

Say the word and I'll write Spec 2 (or Spec 3's schema + the curation workbook, since that one's
content has the longest lead time) in the same depth as Spec 1.
