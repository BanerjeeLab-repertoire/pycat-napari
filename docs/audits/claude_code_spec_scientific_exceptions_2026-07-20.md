# Claude Code spec — Scientific exception tightening: classify by what a handler returns

> **◐ STATUS — Part 2 (the guard) DONE + frap_tools classified (1.6.210); the other 4 modules are the
> follow-on.** The signature deliverable `tests/test_no_scientific_result_swallowing.py` is in place — an
> AST guard, scoped to the five fit/measure modules, that flags any broad `except` whose body directly
> `return`s a non-`None` value (a fabricated number/DataFrame/mask) without a `# broad-ok:` reason. It is a
> ratchet at today's value and includes a canary (a deliberately-introduced swallower is flagged; a
> re-raising / None-returning / annotated one passes). **Measured inventory: 15 result-swallowing handlers**
> (vpt_tools 3, condensate_physics_tools 5, frap_tools 4, invitro_tools 3, partition_enrichment_tools 0) —
> far fewer than the 51 raw broad handlers, because most re-raise or log. frap_tools (4) is classified and
> annotated: three return an all-NaN fit result AND warn the user (an honest missing value, not a fabricated
> default), one is a documented degraded baseline fallback; the ratchet dropped 15→11 and `toolbox`
> 514→509. **Remaining:** classify/convert the 11 handlers in vpt_tools / condensate_physics_tools /
> invitro_tools — annotate the honest NaN-returners, convert genuine fabricated-default swallowers to typed
> raises (`from exc`, narrowed catch), lowering the ratchet each time. One module per commit.

**Date:** 2026-07-20 · **Target tree:** 1.6.203 · Verified against the 1.6.203 tree. The engineering
audit's #4 recommendation, made concrete: the exception ratchet prevents *growth* but does not establish
that current behaviour is *safe*. The audit's precise next step — **no broad handler in a scientific
module may silently return a numerical result, empty DataFrame, mask, fit, or default calibration.**
This spec converts the handlers where a swallowed failure produces a wrong *number*, and adds a guard
test enforcing the rule going forward.

## Verified state
The scientific fit/measure modules carry the concentration of risk exactly where the audit predicted:
```
vpt_tools:                 19 broad handlers
condensate_physics_tools:  14
frap_tools:                 7
invitro_tools:              6
partition_enrichment_tools: 5
```
51 broad handlers in the five modules whose output *is* the published number. The `toolbox` ratchet is
514; this spec targets the subset where the audit's rule bites — the ones that return a scientific
result on failure.

## The rule (the audit's, made testable)
> A broad `except` in a scientific module must not silently return a numerical result, an empty
> DataFrame, a mask, a fit, or a default calibration.

The distinction the audit draws: a broad handler around a **Qt close event** is fine; a broad handler
around a **fit, a transform, or a calibration lookup** that then returns a plausible default is a silent
wrong-number generator. Classify by **what the handler returns on the failure path**, not by count.

## Part 1 — convert the return-a-result handlers
For each broad handler in the five modules, look at its failure-path return:
- **Returns a number / DataFrame / mask / fit / calibration → CONVERT.** Raise a typed error
  (`ScientificAssumptionError`, `InvalidCalibrationError`, `MetadataUnavailableError` from the existing
  family), narrow the caught exception type, and preserve `from exc`. The caller then decides — an
  honest NaN or a raised error, never a fabricated default.
- **Returns nothing / re-raises / logs-and-continues on a non-scientific path → annotate** `# broad-ok:
  <reason>`.
- **A genuine, correct fallback** (robust estimator → simpler estimator) → make it **explicit and
  recorded** via `ValidationLevel.DEGRADED` on the result, so the fallback is visible in the output, not
  silent.

Priority order (worst first): calibration/unit paths → fit routines (`viscosity_measurement`,
coarsening/fusion/FRAP fits) → partition/enrichment ratios → detection gates that can silently empty a
population.

## Part 2 — the guard test that enforces the rule
Add `test_no_scientific_result_swallowing.py`: an AST check over the five (extensible) scientific
modules that **fails if a broad `except` body returns a scientific-typed value** (a `return` of a
number/DataFrame/array/dict-of-measurements) without a `# broad-ok:` justification.

This is stricter than the existing exception-budget ratchet (which only counts) and complements
`test_no_silent_scientific_gates.py` / `test_silent_fallbacks.py` (which the audit notes move in this
direction). Where those check specific known gates, this checks the *return-value class* generically.

- The check is conservative: it flags a broad handler whose body contains a `return <non-None
  scientific value>`. A handler that re-raises, returns `None`, or is annotated `# broad-ok:` passes.
- Start it scoped to the five modules; widen as more are cleaned. Ratchet-style: the count of
  unannotated result-swallowing handlers may not grow.

## The discipline
- **Convert by consequence** — a fit that falls back to a plausible default is the target; a Qt/teardown
  handler is not. Do not chase the count; chase the wrong-number risk.
- **Narrow the catch** as you convert — `except Exception` around a fit hides `KeyboardInterrupt` and
  unrelated bugs too.
- **`from exc` always** — a typed error without its cause is harder to debug than the broad handler.
- **A correct fallback becomes visible, not removed** — `ValidationLevel.DEGRADED`, not silence.

## Tests
- Each converted handler: the failure input now raises the typed error (not a default).
- **The no-fabrication test:** a fit given un-fittable data raises or returns NaN, never a plausible
  default number (extend to the newly converted sites).
- Degraded fallbacks set `ValidationLevel` so the caller can detect them.
- The new AST guard fails on a deliberately-introduced result-swallowing broad handler and passes on the
  cleaned modules.
- Lower the `toolbox` exception ratchet by the number converted.
- No scientific function's *correct* output changes (only failure-path behaviour).

## Steps
1. Inventory the 51 handlers by failure-path return (scratch list; commit only the conversions).
2. Convert Part 1 by module, worst-first, `pytest -m core` per module.
3. Add `test_no_scientific_result_swallowing.py` (AST, scoped to the five modules, ratchet-style).
4. Annotate legitimate handlers `# broad-ok:`; record genuine fallbacks via `ValidationLevel`.
5. Lower the `toolbox` ratchet.
6. Full `pytest -m core` green.
7. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (scientific handlers that
   swallowed results now raise typed errors; guard test added).

## Definition of done
- No broad handler in the five scientific modules silently returns a number/DataFrame/mask/fit/
  calibration; each either raises typed, is annotated, or records a visible `DEGRADED` fallback.
- An AST guard test enforces the rule and ratchets.
- The `toolbox` exception ratchet is lowered by the conversions.
- No correct scientific output changes; only failure paths.
- Full `pytest -m core` green.

## Cautions
- **Classify by return value, not count** — the audit's whole point. A lower number from converting
  teardown handlers misses the risk; converting a result-swallowing fit handler is the win.
- **A correct fallback is made visible, not deleted** — `ValidationLevel.DEGRADED`, never silence.
- **Narrow the catch + `from exc`** — a typed raise that still catches everything, or that drops the
  cause, is half a fix.
- The guard test must be conservative (flag clear result-swallowing only) to avoid false positives that
  would tempt loosening it.
- One module per commit; 51 conversions in one commit is un-bisectable.
