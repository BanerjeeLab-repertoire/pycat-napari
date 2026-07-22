# Claude Code spec — Decompose `timeseries_condensate_tools.py` by scientific domain

> **✅ STATUS — DONE (1.6.244-247). The whole file is decomposed into `toolbox/timeseries/` by domain —
> frame_access, correlation, analysis, execution (worker plumbing), preprocessing (science), and ui (the Qt
> builders). Every move byte-identical; the analysis entry point pinned by a characterization test WRITTEN
> FIRST (test_timeseries_analysis_characterization), the workers relocated behaviour-preserving with Qt kept
> function-scoped, and the UI builders' attribute contract (test_ui_builder_split) repointed unchanged.
> `timeseries_condensate_tools.py` went 2828 → 180 lines (-94%): a PURE re-export shim, no defs. NOTE: the
> preprocessing SCIENCE still embedded inside the 519-line _add_lazy_preprocess_stack UI builder was NOT
> extracted — that is a refactor (surgery on a Qt builder), not a verbatim move, and is left as a future
> refinement; the builder moved whole to ui.py.**

**Date:** 2026-07-20 · **Target tree:** 1.6.203 · Verified against the 1.6.203 tree. The scan's clearest
fresh target: at **2,828 lines / 70 functions** it is the second-largest file, and it is a *repeat
offender* — **five of the ten longest functions in the entire codebase live in this one file**. The
engineering audit named it a domain-split target; the science-split programme has the discipline to do
it safely. Coverage-gated, behaviour-preserving.

## Verified state
```
70 functions, 7 over 120 lines. The long ones:
  520  _add_lazy_preprocess_stack               (UI builder)
  492  _add_run_timeseries_condensate_analysis  (UI builder)
  403  _make__stackprocessworker                (worker construction)
  362  run_timeseries_condensate_analysis       (the scientific entry point)
  358  _on_build                                (UI)
  252  run                                      (execution)
  183  _ts_analyze_frame_worker                 (per-frame science)
```
Coverage is good: **9 test files** reference this module — so the characterization safety net the
science-split programme requires largely exists, and can be topped up where thin.

The file mixes four concerns that should be separate modules: **UI construction**, **worker/execution
plumbing**, **per-frame scientific analysis**, and **result assembly**. That mix is why it resists the
per-function splits done elsewhere — the fix is a domain split into a package.

## Target — a `timeseries/` package, split by scientific contract
Following the audit's recommended shape, adapted to what is actually in the file:
```
toolbox/timeseries/
    frame_access.py      # lazy stack access, materialization, frame iteration
    preprocessing.py     # the preprocess-stack logic (_add_lazy_preprocess_stack's SCIENCE half)
    analysis.py          # run_timeseries_condensate_analysis + _ts_analyze_frame_worker (per-frame)
    correlation.py       # estimate_temporal_correlation + _append_ripley_pcf_tables
    execution.py         # _make__stackprocessworker, _start_worker, run, worker plumbing
    result_models.py     # the result dict/table assembly (typed later, see result-models spec)
    ui.py                # the _add_* builders (or leave in the _ui module; see below)
```
`timeseries_condensate_tools.py` becomes a thin re-export shim so existing imports keep working.

## Method — coverage-gated, science untouched
The science-split discipline applies exactly:
1. **The scientific functions** (`run_timeseries_condensate_analysis`, `_ts_analyze_frame_worker`,
   `estimate_temporal_correlation`, `_append_ripley_pcf_tables`) — before moving, ensure a
   characterization test pins their output on a synthetic time-series stack (`rtol=1e-9`). 9 test files
   already reference the module; verify they cover these functions' *numbers*, and add a characterization
   test where they only cover structure. **No test, no move.**
2. **The UI builders** (`_add_*`, `_on_build`, `_on_run`) — pure Qt; split per the UI-builder discipline
   (attribute-presence test first), extracting contiguous widget blocks.
3. **The execution/worker plumbing** (`_make__stackprocessworker`, `run`, `_start_worker`) — this is the
   duplicated worker-lifecycle logic the audit flagged (#7 "worker lifecycle duplicated"). Move it to
   `execution.py` and align it to the shared `operation_runner`/qt_worker pattern where possible, but
   **behaviour-preserving** — do not change threading semantics in the same move as relocating.

### The hard rules (same as every prior split)
- **Move, don't rewrite.** Cut, paste, fix imports. Numerics unchanged.
- **One domain per commit**, `pytest -m core` + the characterization tests between each.
- **No test edited to make a move pass.**
- **`materialize_stack`, never `np.asarray`** on any stack touched during the move (the frame-0 landmine
  lives exactly here — this is a time-series module).
- Re-export from `timeseries_condensate_tools` so nothing downstream breaks; grep for direct imports of
  moved functions first.

## Why this one, now
- **Highest concentration of long functions** — fixing it moves the complexity ratchet (126) more than
  any other single file.
- **Well-covered** — the characterization net mostly exists, so it is *safely* splittable, unlike an
  uncovered monolith.
- **The audit explicitly named it** — and it pairs with the frame-0 sweep and the worker-lifecycle
  dedup, so one decomposition addresses three findings.

## Tests
- Characterization tests pin every scientific function's output before its move; identical after.
- UI attribute-presence tests pass before and after the builder splits.
- Execution/worker behaviour unchanged (the worker still runs, cancels, and reports as before).
- Every pre-existing test passes **unmodified**.
- `timeseries_condensate_tools` re-export shim resolves every previously-public name.
- Lower `_MAX_LONG_FUNCTIONS` toward the achieved value; lower any per-file line ratchet.

## Steps
1. Create `toolbox/timeseries/` with `frame_access.py`; move stack-access helpers; run tests.
2. Add/verify characterization tests for the four scientific functions.
3. Move preprocessing science → `preprocessing.py`; run tests.
4. Move analysis + per-frame worker → `analysis.py`; run tests.
5. Move correlation → `correlation.py`; run tests.
6. Move execution/worker plumbing → `execution.py` (behaviour-preserving); run tests.
7. Split the UI builders (attribute test first) → `ui.py` or the existing `_ui` module.
8. `timeseries_condensate_tools.py` → re-export shim; lower ratchets.
9. Full `pytest -m core` green after each step.
10. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG reporting before/after.

## Definition of done
- `timeseries_condensate_tools.py` is a thin shim; the domains live in `toolbox/timeseries/`.
- Every scientific function moved is proven byte-identical by a characterization test written first.
- UI builders split with attribute-presence tests; worker plumbing relocated behaviour-preserving.
- Complexity/line ratchets lowered; all pre-existing tests pass unmodified.
- No numerical output changes anywhere.

## Cautions
- **No test, no move** — the scientific functions need characterization pins before relocation; verify
  the 9 existing test files actually cover their numbers, don't assume.
- **Relocate worker plumbing behaviour-preserving** — do not "improve" threading while moving it; that
  is a separate change with its own risk.
- **`materialize_stack` only** — this is the time-series module; the frame-0 collapse landmine is here.
- Move by domain, one commit each; a whole-file split in one commit is un-bisectable.
- Re-export shim is mandatory — grep for direct imports of every moved function first.
