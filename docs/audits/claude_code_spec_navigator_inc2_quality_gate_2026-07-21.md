# Claude Code spec — Navigator wiring increment 2: the planner consults the quality gate

> **✅ STATUS — DONE, shipped in 1.6.279.** The quality gate is wired in with **no change to `compile`** —
> the planner already had the machinery, it just needed the gates. A measurement's `QualityRequirement`
> becomes ordinary `Assumption`s on its `ModuleContract` (new `navigator/quality_gates.py`:
> `quality_assumptions` + `gate_context`), so the EXISTING loop evaluates them into `gate_report`, blocks
> the plan on a VIOLATED blocker, and prepends a QC probe for an UNKNOWN gate — reusing the structure, not a
> parallel one. Verdict→status: BLOCK→VIOLATED (not runnable, reason in `blockers()`), WARN→UNKNOWN
> (runnable; probes when a probe is named), DOWNGRADE→VIOLATED on a warning gate (runnable, reported),
> OK→SATISFIED. Attached to two representative measurements: `vpt.microrheology` (pixel-size blocker +
> reliability probe) and `partition_enrichment.client` (calibration blocker). `evaluate_quality` unchanged;
> non-measurement and quality-req-free plans untouched. New `tests/navigator/test_quality_gate_planning.py`
> (`core`, 7 tests); all existing navigator/gate tests pass unmodified; full core green (1704).
>
> **Note on the two representations:** the planner backward-chains over the CAPABILITY registry
> (`_measure_ops()` / `ModuleContract`), not the lightweight `operation_catalog.json` from increment 1 — so
> the `QualityRequirement`s were attached to the `_measure_ops()` entries the planner actually reaches. The
> remaining measurements can gain a `QualityRequirement` the same way as they're validated; more can be
> added op-by-op without touching the wiring.

**Date:** 2026-07-21 · **Target tree:** 1.6.269 · Verified against the 1.6.269 tree. **Increment 2 of
4.** Depends on increment 1 (measurement ops in the catalog). One function, one integration point —
deliberately small enough to ship alone.

## Verified state
`utils/quality_gate.py` is **fully built and has zero consumers**:
```python
class GateVerdict(str, enum.Enum): ...
@dataclass class SignalOutcome / GateResult / QualityRequirement
def _pixel_size_signal(context) / _calibration_signal(context) / _reliability_signal(context, requirement)
def evaluate_quality(objects, requirement: QualityRequirement, *, context=None) -> GateResult
```
`grep evaluate_quality src/pycat/navigator/` → **0 hits**. The gate composes calibration, pixel size,
and reliability into a verdict with reasons, and nothing asks it.

Meanwhile `navigator/planner.py` already has the right structure to receive it:
```python
gate_report: List[Tuple[str, Assumption, GateStatus]] = field(default_factory=list)
probes: List[PlanStep]        # QC probes prepended for UNKNOWN gates
def is_runnable(...)          # "no missing products and no VIOLATED requirement or blocker gate"
def compile(intent, ctx, ...) # backward-chains to a runnable plan
```
So the planner **already models** staged gating with probes and a report — the quality gate simply
isn't one of the signals feeding it.

## The change — one call, one reporting path
1. **Attach a `QualityRequirement` to measurement operations.** Increment 1 declared what each
   measurement *needs* via the requirement vocabulary; this increment expresses the ones that are
   quality conditions (calibration present, reliability assessed, minimum object count) as a
   `QualityRequirement` on the op.
2. **`compile` evaluates it for terminal measurement steps.** When the backward chain reaches a
   measurement op, call `evaluate_quality(objects, requirement, context=ctx)` and fold the `GateResult`
   into the existing `gate_report` — reusing the structure, not adding a parallel one.
3. **Map the verdict onto existing plan semantics:**
   - **blocked** → the step is not runnable; its reason travels in `gate_report` so
     `is_runnable`/`why_not` surfaces *"ΔG needs a calibrated pixel size — set the scale first"*.
   - **warn** → runnable, with the reason attached (visible, not obstructive).
   - **downgrade** → runnable, flagged reduced-confidence, reason attached.
   - **unknown / not assessed** → reuse the **existing probe mechanism**: prepend a QC probe rather
     than guessing. This is exactly what `probes` was built for.
4. **An unassessed signal is not a passing signal** — the gate already models this; the planner must
   preserve it rather than collapsing unknown into ok.

## Scope discipline
- **Do not change `evaluate_quality`.** It is built and tested; this increment is the caller.
- **Do not add a UI.** The plan carries reasons as data; surfacing them is increment 3.
- **Do not invent a second gate vocabulary.** Fold into `gate_report`/`GateStatus`; if a `GateVerdict`
  doesn't map cleanly onto an existing `GateStatus`, extend the existing enum with a documented value
  rather than running two systems.
- **Non-measurement ops are untouched** — no behaviour change for the 79 existing operations.

## Tests
- A measurement op whose requirement is unmet compiles to a plan where it is **not runnable**, and the
  reason names the unmet condition.
- The same op with the condition satisfied compiles to a runnable plan.
- A `warn` verdict yields a runnable plan **with** the reason attached (not silently dropped).
- An unknown/unassessed signal prepends a probe rather than passing or failing.
- `gate_report` carries quality verdicts alongside the existing assumption gates (one structure).
- Existing planner tests pass unmodified; plans for non-measurement intents are byte-identical.
- `evaluate_quality` itself is unchanged (its tests pass unmodified).

## Steps
1. Attach `QualityRequirement` to the measurement ops added in increment 1.
2. Call `evaluate_quality` for terminal measurement steps inside `compile`; fold into `gate_report`.
3. Map blocked/warn/downgrade/unknown onto runnability + probes.
4. Tests above.
5. Full `pytest -m core` green.
6. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG ("generated plans now state when
   a measurement cannot be trusted and why").

## Definition of done
- Terminal measurement steps are quality-gated; blocked steps carry a stated reason; warn/downgrade stay
  runnable with the reason attached; unknown prepends a probe.
- Verdicts live in the existing `gate_report`; no parallel mechanism.
- `evaluate_quality` unchanged; non-measurement planning unchanged.
- Full `pytest -m core` green.

## Cautions
- **Reuse `gate_report` and `probes`** — the planner already models staged gating; a second reporting
  path would fracture it.
- **Unknown is not ok.** Preserve the gate's distinction; probe instead of assuming.
- **Blocked must state WHY** in user-readable terms — a blocked step with no reason is worse than no
  gate at all.
- Increment 2 only: no UI, no changes to the gate's internals.
- Non-measurement plans must be byte-identical — assert it.
