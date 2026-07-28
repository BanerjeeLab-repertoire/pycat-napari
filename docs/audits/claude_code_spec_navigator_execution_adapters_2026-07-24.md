# Claude Code spec — Navigator execution adapters: make "Run analysis" compute the plan

> **◕ STATUS — Phases 1–4 shipped (1.6.332–1.6.379). Phase 3 (more adapters) is the only open lane.**
> **Phase 4 — DONE (1.6.379).** `run_plan` gained two Qt-free hooks: `should_cancel()` is checked at each
> **step boundary** (before the step runs) — the first truthy check records the step `'cancelled'` and stops, so
> nothing runs on a cancelled/stale state (the blocker's stop discipline); `on_progress(done, total)` fires once
> per disposed step, **monotonic** 1..N. `test_navigator_cancel_progress.py` (`base`, 5) pins both, incl. a
> real-adapter case proving a cancelled step's computation did not run. The dock's Run drives a determinate
> `QProgressDialog` with a Cancel button over these hooks (best-effort; headless falls back to a plain run); a
> cancel stops at the next boundary and the summary says where. `test_navigator_dock` gained a wiring test.
> **Phase 3 — started (1.6.334).** The finding: Phase 1 proved the mechanism against a *synthetic* step name, but
> real plans emit toolbox-**module** names — so no adapter fired in production. Fixed by re-keying adapters on the
> real module names (`image_processing_tools` → `background_removal`, Phase 1's proof now live) and adding the next
> route-proven workflow: `segmentation_tools` (cell target) → `cellpose_segmentation`. A coarse module resolves its
> batch step from the intent (`resolve_batch_step` — the shared "will this run" authority); condensate segmentation
> (unproven batch route) and time-series keyframe cellpose are reported, never guessed. `params_from` gained the
> reviewed values so it applies each where the handler reads it (`data_instance` for `cell_diameter`).
> `test_navigator_cellpose_adapter.py` (`base`, 5): real-plan resolution guard + guided == manual at the reviewed
> diameter, bit for bit + registry guard.
> **Phase 3 (cont.) — cell analysis (1.6.335).** `feature_analysis_tools` (cell) → the `cell_analysis` batch step,
> proven without torch on a synthetic labelled mask (`test_navigator_cell_analysis_adapter.py`, `base`, 4: guided ==
> manual bit for bit). It reads the `cellpose_mask` segmentation writes, so a real cell plan now fires
> `segmentation_tools → feature_analysis_tools` end to end; no upstream mask → reported error, never a silent empty.
> **Phase 3 continues; Phase 4** (dock progress +
> cancel) remains.
> **Phase 3 (cont.) — condensate analysis (1.6.409).** `feature_analysis_tools` (condensate) → the
> `condensate_analysis` batch step, proven without torch on a synthetic puncta mask + labelled cells + a seeded
> per-cell table (`test_navigator_condensate_analysis_adapter.py`, `base`, 4: guided == manual bit for bit). It
> reads the `puncta_mask` condensate segmentation writes; with none, the batch handler now reports an error
> (segmentation must run first) instead of silently skipping — a dead `RuntimeError` behind a combined guard was
> made reachable. Condensate SEGMENTATION (`segmentation_tools` condensate → `condensate_segmentation`) is the
> next adapter, which produces that `puncta_mask`.
> **Phase 3 (cont.) — condensate segmentation (1.6.410).** `segmentation_tools` (condensate) →
> `condensate_segmentation` (`segment_subcellular_objects` per cell), flipping the documented gap. `params_from`
> branches on target (cellpose method/refine + diameter vs the condensate thresholds' grounded defaults). Proven
> without torch (`test_navigator_condensate_segmentation_adapter.py`, `base`, 4: guided == manual bit for bit,
> the per-cell loop replicated). **The condensate chain now runs end to end** — `segmentation_tools →
> feature_analysis_tools` for a condensate target — mirroring the cell chain. The six condensate thresholds take
> their validated defaults today; surfacing them in the Phase-2 param review (`parameters._MATERIAL` +
> `_PRESET_WORKFLOW`) is the natural follow-on.
> **Phase 3 (cont.) — condensate thresholds are now editable in the param review (1.6.412).** The follow-on above:
> `parameters._MATERIAL["segmentation_tools"]` became a target branch (cell → `cell_diameter`; condensate → the
> six thresholds, declared with the grounded signature defaults), and `_segmentation_params` now reads each
> reviewed threshold into the condensate params dict (was `{}`) where `replay_condensate_segmentation` reads it.
> `material_params` also gained the op-id → module translation (`executor.adapter_module_for`) it was missing —
> it was module-keyed while production plans are op-id-named, so the review surfaced nothing for a real session
> (the same dormancy the adapters had, fixed 2026-07-27). Gate (+2 in the adapter test): the review surfaces the
> six thresholds, and an edited `min_spot_radius` makes the guided mask equal the manual at that value, not the
> default. (Wiring a preset into `_PRESET_WORKFLOW` remains open — no condensate-segmentation preset applies yet.)
> **Phase 3 (cont.) — the brightfield condensate SEGMENTATION chain (1.6.414).** The first segmenter that requires
> preprocessing: brightfield dark-blob detection is meaningless on a raw image, so a new coarse `bf_preprocess` op
> (context-gated to `brightfield`, providing `state:enhanced`) is REQUIRED by `bf_segment` (`_REQUIRES_OVERRIDE`),
> which makes the planner auto-insert it — the one deliberate exception to "preprocessing is never auto-inserted"
> (Gable's call). Two adapters (`bf_preprocess` → `bf_preprocess`; `bf_segment` → `bf_condensate_segmentation`),
> both knobs surfaced in the param review; catalog 93 → 94. `run_plan`/`resolve_batch_step` now thread `state`
> into a coarse module's variant choice so `feature_analysis_tools` dispatches on the produced mask — brightfield
> condensates are first-class labels that neither `condensate_analysis` (needs a `puncta_mask`) nor `cell_analysis`
> (cell-sized min-area filter) measures right, so the measurement HONESTLY reports needs_panel (the brightfield
> measurement route is the next increment). Gate `test_navigator_brightfield_adapter.py` (`base`, 6): planner
> chains it on brightfield only, guided == manual bit for bit, edited `min_diameter_px`/`bg_kernel` each drive the
> run, analysis needs_panel for brightfield / `condensate_analysis` for fluorescence. **Brightfield CELL
> segmentation stays deferred** (`replay_bf_cell_segmentation` uses Cellpose/torch — not headlessly provable).
> **Phase 3 (cont.) — the brightfield condensate MEASUREMENT (1.6.415).** Completes the chain: the analysis step
> (deferred to needs_panel in 1.6.414) now runs `bf_condensate_metrics` — PyCAT's existing per-condensate OD/area/
> shape measurement ("brightfield equivalent of `puncta_analysis_func`"), in the cell-less form the in-vitro BF GUI
> uses (`bf_condensate_metrics(raw, mask, None, mpx)`), pure numpy/skimage. New batch handler
> `replay_bf_condensate_analysis` (runs on the RAW image — OD is `-log10(I/I0)`, a normalised image diverges); the
> run-time state dispatch (added 1.6.414) routes brightfield → `bf_condensate_analysis`, fluorescence →
> `condensate_analysis`. No op-graph change. Gate: full plan runs every step; guided `bf_condensate_df` == manual
> bit for bit. **The brightfield condensate workflow now runs end to end in the guided flow.**
> **Phase 3 (cont.) — in-vitro fluorescence droplet SEGMENTATION (1.6.416).** Droplets ≡ condensates (same target,
> per the science owner); in-vitro vs in-cell is a CONTEXT distinction (no cells → whole-field threshold), not a
> `droplet` target. New `in_vitro` context flag (requirement + `_req_in_vitro` predicate, mirroring the modality
> gate): `ivf_droplet_segment` (the extracted producer `segment_ivf_droplets`, `target='condensate'`) tagged
> `requirements=('in_vitro','fluorescence')` wins the segmenter slot on a condensate+in_vitro+fluorescence plan via
> context-score; in-cell keeps `subcellular_segment`, brightfield keeps `bf_segment`. New batch handler
> `replay_ivf_droplet_segment` → `ivf_droplet_mask`; method + min-area knobs in the param review. Measurement
> STAGED (needs_panel, state-dispatched) — field-summary/size-distribution is the next increment. Gate
> `test_navigator_invitro_adapter.py` (`base`, 5): context selects the droplet segmenter, guided == manual
> `segment_ivf_droplets` bit for bit, edited `min_area` drives the run. **Follow-on:** reconcile the canonical
> `target='droplet'` cases to condensate+in_vitro (they currently pass unchanged), and the in-vitro measurement.
> **Phase 1 — DONE (1.6.332).** `navigator/executor.py`: `run_plan(plan, state, …)` drives the batch `_STEP_MAP`
> handlers in `execution_order` order, threading `state`; `ExecAdapter` maps a plan step → batch handler +
> `params_from`; a step with no adapter is reported ('needs_panel'), never invoked with guessed args; gate
> semantics read from `execution_order` (blocker halts + state untouched, caveat runs, probes first). One
> proven adapter (`background_removal`) with the acceptance gate pinned — `test_navigator_executor.py`
> (`base`, 5): **guided == batch == manual, bit for bit**. Run button wired via `central_manager`.
> **Phase 2 — DONE (1.6.333).** `navigator/parameters.py`: each adapter-covered step declares its **material**
> params; `build_param_review(plan, ctx)` seeds them **preset → session value → grounded default** (never
> invented); `ReviewedStep` tracks edits as provenance in the exact `PresetApplication.record()` shape
> (`preset_key=None` when no preset). `run_plan(…, params_by_step=, provenance_by_step=)` merges reviewed values
> over the adapter's params and records provenance on each `StepOutcome`. `test_navigator_parameters.py`
> (`base`, 9): an edited rolling-ball radius makes the guided result equal the **manual op at that radius, not
> the default** — the edit provably reaches the computation. The dock renders an editable "Review parameters"
> form above Run (seeded, tooltip-documented) and passes the `ParamReview` through. **Remaining: Phase 3**
> (more adapters, one workflow per increment, each behind its own route-equivalence test), **Phase 4** (dock
> progress + cancel).

**Date:** 2026-07-24 · **Target tree:** 1.6.331 · Scoping spec for the layer deferred in
`selection_scale_and_guided_templates` Part 2. **This is a design + phasing document, not a one-shot build** —
the adapters land workflow by workflow, each proven output-identical to the manual and batch routes before the
next.

---

## The finding this addresses (verified in the tree)

`selection_scale` Part 2 shipped the gate-respecting execution **model** (`navigator/execution.py`:
`execution_order(plan)` → probes first, a blocker halts the run and skips the rest, a caveat runs with its
reason) and wired the dock to show the run order. It **did not** auto-run the plan, because:

- **There is no uniform "run this op".** The navigator plan's steps resolve to bespoke scientific functions —
  e.g. `subcellular_segment` → `segment_subcellular_objects(original_image, pre_processed_image, cell_mask,
  cell_label, ball_radius, kurtosis_threshold=-3.0, local_snr_threshold=1.0, …15 params)` — that need inputs
  threaded from prior steps and parameters the user sets. `navigator.operation_spec.resolve_operation(spec)`
  returns the callable, but **that callable is invoked nowhere in the codebase**: every method panel calls its
  operation itself, with panel-collected arguments. A generic `fn(image)` invocation would pass wrong
  arguments and produce **wrong science silently** — the one outcome worse than a disabled button.

So auto-execution is a **per-operation adapter layer**, and the spec's own phrase — "the same execution path
each method panel uses" — points at the piece the tree already has: the batch route.

## The route to reuse (not reinvent)

**`batch_step_registry._STEP_MAP` is the uniform, proven "same computation" route.** Its handlers
(`open_image`, `preprocessing`, `background_removal`, `cellpose_segmentation`, `cell_analysis`,
`condensate_segmentation`, `condensate_analysis`, `sacf_analysis`, `spatial_metrology`, …) all share one
signature — `(state, image_path, params, output_dir)` — and `test_route_equivalence` **already asserts they
compute byte-identically to the manual GUI route** across `rolling_ball`, `puncta`, `vpt_msd`, `colocalization`
and `time_series_condensate`. They run the real analysis functions; they are what a batch replay is.

The gap is narrow and specific: **the batch handlers consume recorded GUI `params`; the navigator has
answers, not params.** The adapter layer's whole job is to bridge that gap and drive these handlers.

---

## The design

### 1. The adapter registry — navigator step → batch handler + parameter source
A small, declared registry (one entry per supported plan step):

```python
@dataclass(frozen=True)
class ExecAdapter:
    plan_step: str          # the navigator plan step name (e.g. 'subcellular_segment')
    batch_step: str         # the _STEP_MAP key that computes it ('condensate_segmentation')
    params_from: Callable   # (intent, ctx, threaded_state) -> params dict for the handler
```

`params_from` is where the parameter story lives (see §3). The registry is the **only** place a plan step is
tied to a computation — a step with no adapter is "not yet auto-runnable" and the dock says so per step
(the honest per-step version of today's message), rather than the whole button being dead.

### 2. The executor — walk the gate order, run each handler off-thread, thread outputs
```python
def run_plan(plan, intent, central_manager, *, on_step=None, on_done=None, token=None)
```
- Walk `execution_order(plan)` (do NOT reimplement the ordering/gating): **probes first**, **stop at a
  blocker** with its reason (do not run it or anything after), **run a caveat step** and record the caveat on
  its result.
- For each runnable step, look up its `ExecAdapter`, build `params` via `params_from`, and invoke the batch
  handler through the **canonical `OperationRunner.execute(fn, state, image_path, params, output_dir,
  progress=…, on_result=…, on_error=…)`** — off the Qt thread, cancellable via the existing token, stale-safe.
- **Thread outputs as the batch does**: each handler writes its result into `state` (the data repository) —
  the produced layer / table / tags — so the next step reads them exactly as in a batch replay. The product
  graph the planner built (`provides`/`requires_inputs`) is the contract that these line up; assert it.
- Each step's output therefore **lands as it would from its own panel** — same layers, same tables, same tags
  — because it IS the batch handler, which is the manual computation.

### 3. The parameter story (the honest hard part)
The navigator asks *what* to do, not *with which parameters*. Three sources, in precedence:
1. **A matching `analysis_presets` preset** — if one `applies_to` this step's workflow, seed its parameters
   (the populate-but-never-lock `PresetApplication` already models this, incl. "modified from <preset>").
2. **The function's own defaults** — the batch handler already falls back to grounded defaults
   (`_get_data(data_instance, 'cell_diameter', 100)` etc.), so an un-set parameter is not invented.
3. **A minimal param review** — before a run, surface the handful of parameters that materially change the
   result (segmentation method, diameter, threshold) in a small editable panel, pre-filled from (1)/(2). The
   navigator is *guided*, not *parameter-free* — hiding a segmentation method choice would be dishonest.

**Record provenance.** The run records which preset (if any) seeded each step and what the user changed — the
same `PresetApplication.record()` shape — so a guided result states how it was parameterised, and a saved
template (Part 3) can carry those params.

### 4. Gate semantics — reuse, never duplicate
Blocked → the run stops at that step with the stated reason and reports it (nothing downstream runs).
Amber/caveat → runs, and the caveat is attached to the result (it travels into the recorded workflow, as the
reliability/quality caveats already do). Probe → runs first, in the order the planner placed it. All of this
is **read from `execution_order`**, not re-decided.

### 5. Route equivalence — guided is a fourth route
Add `guided` to `test_route_equivalence`: for a workflow with adapters, assert the guided run produces the
**same numbers** as the manual and batch routes on the same input. If they diverge, one is wrong — that is the
whole point of the test, and it is the acceptance gate for each adapter.

---

## Phasing (each phase ships independently)

1. **The executor + registry, one workflow end-to-end.** Pick the shortest real chain (e.g. cellular
   fluorescence: `open → preprocess → segment → cell_analysis`). Build its adapters, run it through the
   handlers via `OperationRunner`, and prove `guided == batch == manual` on a fixture. Wire the dock's Run
   button (via `central_manager`) to `run_plan` for adapter-covered plans; keep the per-step honest message
   for steps without an adapter yet. Ship.
2. **The parameter review panel** (§3.3) — the minimal pre-run editable set, preset-seeded, provenance-recorded.
   Ship.
3. **Expand adapters** — in-vitro fluorescence, VPT, condensate physics, one workflow per increment, each
   gated on its own route-equivalence test. Ship per workflow.
4. **Cancellation + progress in the dock** — a determinate bar over the plan, cancel via the token. Ship.

## Tests
- Route equivalence: `guided == batch == manual` per adapter-covered workflow (the acceptance gate).
- Gate-stop: a plan with a blocked step runs the steps before it, stops at it with the reason, and runs
  nothing after — asserted against `execution_order`.
- A caveat step runs and its caveat is recorded on the result.
- Parameter provenance: the run records the preset seeded and the user's edits (the `PresetApplication.record`
  shape); a template round-trips those params.
- A step with no adapter is reported as "run from its panel", not silently skipped or crashed.
- Cancellation stops the run at a step boundary; progress is monotonic.
- The executor never invokes an operation with guessed arguments — every call goes through a registered
  adapter (a guard test over the registry, like the batch-step-composition guard).

## Definition of done
- "Run analysis" executes an adapter-covered plan through the batch handlers off the Qt thread, respecting the
  computed gates, landing the same outputs as the manual route (asserted by route equivalence).
- Parameters are preset-seeded, user-reviewable, and provenance-recorded; a saved template carries them.
- Steps without an adapter are honestly labelled, never guessed at.
- Full `pytest -m core` (and the base lane) green.

## Cautions
- **Output-identical is the acceptance gate.** A guided run that differs from the manual/batch route is a bug
  in the adapter, not a new "guided answer" — `test_route_equivalence` is the law.
- **No generic invocation.** Every operation runs through a *registered* adapter that builds its real
  arguments; a `fn(image)` fallback would pass wrong args and produce wrong science silently. That is why this
  is an adapter layer and not a loop over `resolve_operation`.
- **Reuse the batch handlers and `OperationRunner`** — do not write a second execution engine or a second
  gating vocabulary (`execution_order` is the gate authority; `_STEP_MAP` is the computation).
- **Be honest about parameters.** A guided run that silently picks a segmentation method the user never saw is
  worse than asking. Surface the material choices; default the rest from presets/function defaults with
  provenance.
- **One workflow at a time.** Shipping all adapters at once, unproven, is how a wrong-argument bug reaches
  real data. Each adapter earns its place with a passing route-equivalence test.
- **The product graph is the wiring contract.** A step's `requires_inputs` must be produced by an earlier
  step's `provides`; if a threaded output is missing, stop with a stated reason rather than run on stale state.
