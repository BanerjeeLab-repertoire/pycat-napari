# Claude Code spec — The navigator execution-adapter layer is dormant in the live dock

> **STATUS — FIXED, 1.6.411 (2026-07-27).** All four root causes addressed in one change: (1) an op-id→module
> translation layer in the executor (`_OP_TO_ADAPTER_MODULE` / `_adapter_for`) so production op-id steps reach
> their module adapters; (2) target-aware dependency selection — `_resolve_module` narrows a wildcard `target:*`
> requirement to a propagated specific target *for provider lookup only*, so a `target:cell` plan is offered the
> cell segmenter; (3) modality/dimensionality-aware selection — `_context_score` in `_pick` ranks a
> context-matched specialist (brightfield op on brightfield, 3D op on a z-stack) above a generic, a
> context-unknown specialist below a no-gate generic, and a context-violated op out; (4) vocabulary correction —
> `brightfield`/`fluorescence` added to the requirements vocab and mapped to context predicates, `bf_segment`
> tagged `requirements=('brightfield',)`. The home-dock Run button now drives the same executor path as the menu.
> Verified: a default-session cell plan resolves `cellpose→cellpose_segmentation` + `cell_analysis`, a condensate
> plan `subcellular_segment→condensate_segmentation` + `condensate_analysis` (new guard
> `test_navigator_adapter_reaches_production_plan.py`); selection matrix correct across cell/2D, cell/3D,
> condensate/fluorescence, condensate/brightfield; full navigator suite + route-equivalence oracle green.

## The finding

The Navigator's "Run analysis" execution-adapter layer (`navigator/executor.py`, Phases 1–4 of
`claude_code_spec_navigator_execution_adapters_2026-07-24.md`; adapters shipped 1.6.332–1.6.410) **does not
fire in the production dock.** When a user clicks Run, every step reports `needs_panel` ("run this from its
method panel") and the plan computes nothing — for every workflow, cell and condensate alike, including the
two condensate adapters shipped in 1.6.409/1.6.410.

**Verified end-to-end.** The dock builds its plan from `NavigatorSession()` (session.py:34 → default
`build_operation_registry()`), whose plan step names are **op-ids**. A real cell plan:

```
['data_qc.assess', 'acquisition', 'data_qc.assess', 'subcellular_segment', 'feature_analysis.cell_analysis']
```

`run_plan` looks up `_ADAPTERS.get(es.name)`, but every `ExecAdapter` is keyed by **module name**
(`segmentation_tools`, `feature_analysis_tools`, `image_processing_tools`). `resolve_batch_step` returns
`None` for **every** production step → nothing runs.

## Root causes (layered — each must be addressed for the adapter layer to work)

**1. Namespace mismatch (adapters can't be found).** Three namespaces are in play:
- navigator **op-ids** — `cellpose`, `subcellular_segment`, `feature_analysis.cell_analysis` — what the
  production op-catalog registry names plan steps.
- toolbox **module names** — `segmentation_tools`, `feature_analysis_tools` — what the adapters key on.
- batch **`_STEP_MAP` keys** — `cellpose_segmentation`, `condensate_segmentation`, `cell_analysis` — what the
  handlers compute.

The adapters map module → batch key; production plans are op-ids. A translation layer (op-id → adapter) is
*necessary but not sufficient* — see (2)/(3).

**2. Target-blind dependency selection.** `Planner._pick` → `default_selection_policy` picks by
`(preference, cost, name)` only. Terminal selection (`_pick_terminal`) has a target-specificity bonus, but
dependency (segmenter) selection does not. So for a `target:cell` goal, the wildcard `subcellular_segment`
(preference 0.66, `target=*`) beats the target-specific `cellpose` (0.65, `target=cell`) — **every** plan
segments with the puncta segmenter. Worse, the measure op `feature_analysis.cell_analysis` declares its input
as `instance_labels, target=*` (generic), so `with_tags(target:cell)` leaves the subgoal carrying BOTH
`target:*` and `target:cell`, `subgoal.target()` returns `*`, and `providers_of` never even lists `cellpose`.
A localized fix (narrow the subgoal to the propagated specific target + add specificity to `_pick`) makes
cell→`cellpose` — **but see (3).**

**3. The op vocabulary is under-specified (a partial fix ships WRONG SCIENCE).** With (2) fixed,
`condensate` planned `bf_segment` (brightfield condensate segmentation) — because `bf_segment` declares
`target=condensate` but has **no modality requirement** (`requires_context=[]`), so it out-specifies the
generic `subcellular_segment` (`target=*`) for *every* condensate, fluorescence included. That is wrong for
the common fluorescence case, and `bf_segment` doesn't map to the condensate adapter. So the specificity fix
traded a cell bug for a condensate bug. Correct selection needs **modality (and dimensionality)** constraints
on the ops (`bf_segment` requires brightfield; the fluorescence puncta segmenter should carry a
condensate-relevant target), not just target specificity.

**4. Neither registry produces adapter-matching plans.** Switching the dock to the workbook (module) registry
(`build_registry_from_workbook`) does not rescue it: a plain **cell** intent then plans `ts_cellpose_tools`
+ `timeseries_condensate_tools` (time-series ops for a non-time-series cell — matches no adapter); condensate
matches `segmentation_tools` but terminates in `timeseries_condensate_tools`, not `feature_analysis_tools`.
Both registries mis-select under-constrained intents.

## Secondary finding
`home_dock.py:88` wires the navigator with `on_run=getattr(central_manager, "run_navigator_plan", None)`, and
**no `run_navigator_plan` method exists** on central_manager — so that surface's Run is always disabled. (The
menu-action surface IS wired, via `run_plan_via_central_manager`.)

## Why the tests didn't catch it
Every adapter test (`test_navigator_executor`, `test_navigator_cellpose_adapter`,
`test_navigator_cell_analysis_adapter`, and the two 1.6.409/1.6.410 condensate tests) builds plans with
hand-constructed **module-name** `PlanStep`s (or the workbook registry). **None compiles a plan from a default
`NavigatorSession()` and runs it** — so the production op-id path was never exercised. A regression test that
does exactly that (compile from `NavigatorSession()`, assert ≥1 step resolves to a batch handler) is the guard
this needs.

## The fix is a dedicated effort (options + blast radius)
- **Full concrete-op path:** (a) translation layer op-id → adapter; (b) target-specificity in dependency
  selection; (c) modality/dimensionality constraints on the ops (`bf_segment` → brightfield, etc.);
  (d) verify across targets × modalities. **Blast radius: the 13-pipeline oracle + all navigator planning
  tests** (selection changes ripple), plus catalog regen for op-declaration changes.
- **Workbook-registry path:** make the dock plan at module granularity + fix the coarse planner's
  target/dimensionality selection so cell→`segmentation_tools`→(cellpose) and the terminal is
  `feature_analysis_tools`. Also broad; tension with the op-catalog "source of truth" flip (increment 4).
- **A regression guard first** (compile-from-default-session-and-run) so whichever path is taken is proven
  live in production, not just in hand-built tests.

**Recommendation:** treat this as a navigator-planning-correctness increment of its own, starting from the
regression guard, then the target/modality/dimensionality selection + vocabulary corrections, gated on the
13-pipeline oracle. Do NOT ship the specificity tweak alone — it makes fluorescence condensate segmentation
wrong.

## Implementation design (worked out 2026-07-27; staged so each stage is oracle-verifiable)

The correct selection rule, from the segmenter landscape: a **general** segmenter for a target beats a
**specialised** one unless the specialisation's context matches. Cell's general segmenter is `cellpose`
(target=cell, no context req). Condensate's general segmenter is `subcellular_segment` (target=*, pref 0.66 —
the highest). The specialised condensate ops are workflow/modality-bound: `bf_segment` (brightfield),
`ivf_droplet_segment` (in-vitro fluorescence), `ts_droplet_segment` (time-series), `host_segment`/`host_erode`
(VPT host). So the rule is: **prefer a target-specific provider over the generic one ONLY when its context
requirements are SATISFIED** (an op with no context req is trivially satisfied — that's `cellpose`).

- cell → target-specific & context-satisfied = `cellpose` (no req) → beats generic. ✓
- condensate, fluorescence → every specialised condensate op's context is unsatisfied → none preferred →
  generic `subcellular_segment` wins. ✓ (and it maps to the `condensate_segmentation` adapter/batch step)
- condensate, brightfield → `bf_segment`'s brightfield context satisfied → it wins. ✓

**Prerequisite discovered:** `requires_context` today only knows `{time_series, calibrated, two_channels}` —
there is **no modality (brightfield/fluorescence) gate**, and no op carries a modality tag. `ctx` DOES hold a
`modality` (set by `context_from_session`), so the gate is buildable, but it is a **new capability**, not a
tweak. Also: `@tags_layer`'s `requirements=(...)` (e.g. `z_stack` on `cellpose_3d`) does NOT currently surface
as `requires_context` (cellpose_3d shows `requires_context=[]`) — that mapping must be added for dimensionality
gating too.

### Stages (each ends green + oracle-checked)
1. **Modality/dimensionality gating capability.** Add a modality context key (brightfield/fluorescence) that
   `ctx.modality` answers, and surface `@tags_layer` `requirements` (`z_stack`, `time_series`, …) as
   `requires_context`. No behaviour change yet — just the capability + tests.
2. **Declare the specialised segmenters' contexts.** `bf_segment`→brightfield, `ivf_droplet_segment`→in-vitro
   fluorescence, `ts_droplet_segment`→time-series, `host_segment`/`host_erode`→VPT host, the `*_3d` ops→z_stack.
   Regenerate the catalog. Verify the op-graph guard.
3. **Context-satisfied target-specificity in dependency selection.** Narrow the subgoal to the propagated
   specific target (drop `target:*`), then among providers prefer target-specific ones whose context is
   satisfied; else the base policy. Verify the matrix: cell→cellpose, fluor-condensate→subcellular_segment,
   bf-condensate→bf_segment, across 2d/3d/time-series. **This is the stage that moves the 13-pipeline oracle
   — expect and reconcile oracle shifts here.**
4. **Translation layer.** `run_plan` resolves the adapter from the op-id step's toolbox module (a small
   op-id→module map, since op.module is a dotted path like `pycat.toolbox.segmentation.cellpose`), so the
   module-keyed adapters fire and dispatch on `intent.target`. Flip the xfail guard to a real assertion.
5. **Fix the `home_dock` `run_navigator_plan` wiring gap.**

This is multi-part with real blast radius at stage 3; it should be executed stage-by-stage with the gate +
oracle checked between stages, not in one pass.
