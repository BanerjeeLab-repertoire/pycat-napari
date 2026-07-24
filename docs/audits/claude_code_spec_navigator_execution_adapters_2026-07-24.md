# Claude Code spec — Navigator execution adapters: make "Run analysis" compute the plan

> **◐ STATUS — Phase 1 DONE (shipped 1.6.332). Phases 2–4 remain.**
> **Phase 1 — DONE.** `navigator/executor.py`: `run_plan(plan, state, …)` drives the batch `_STEP_MAP` handlers
> in `execution_order` order, threading `state`; `ExecAdapter` maps a plan step → batch handler + `params_from`;
> a step with no adapter is reported ('needs_panel'), never invoked with guessed args; gate semantics read from
> `execution_order` (blocker halts + state untouched, caveat runs, probes first). One proven adapter
> (`background_removal`) with the acceptance gate pinned — `tests/navigator/test_navigator_executor.py`
> (`base`, 5): **guided == batch == manual, bit for bit**, plus blocker/caveat/no-adapter. The Run button is
> wired via `central_manager` (`run_plan_via_central_manager` as `on_run`) — covered steps run, the rest are
> reported "run from their panels, in order". **Remaining: Phase 2** (parameter-review panel, preset-seeded,
> provenance-recorded), **Phase 3** (more adapters, one workflow per increment, each behind its own
> route-equivalence test), **Phase 4** (dock progress + cancel).

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
