# Claude Code spec — PyCAT Validation Suite: a standing per-release regression benchmark

> **✅ STATUS — DONE (done-but-unstamped; verified 2026-07-22). The standing per-release regression bench
> exists and is green (`test_validation_suite`, 8 tests). No computed value changes.**

**Date:** 2026-07-19 · **Target tree:** 1.6.156 · Verified against the 1.6.156 tree. Turns the
existing scattered correctness tests into a **tracked, per-release benchmark** whose metrics are
compared across versions. The roadmap describes it as *"a standing per-release regression benchmark…
track metric drift across versions."*

## Why this is now worth building
PyCAT ships very frequently — **1.6.156 in a few weeks**, often several versions per day. The test
suite answers "did anything break?" (binary, per commit). It does not answer:

> *"Is segmentation quality on our canonical cases the same as it was ten releases ago?"*

That is a different question, and at this release cadence it is the one that catches slow degradation.
A change can keep every test green while moving Dice from 0.94 to 0.89 — nothing fails, and nobody
notices until a result looks wrong months later.

Verified: no validation-suite or metric-tracking infrastructure exists. But every ingredient does —
`benchmark_tools` (Dice/IoU/matched-detection/`basic_metrics`), `fixtures_synthetic`, the
route-equivalence harness, `filter_sensitivity`, and now `control_validation`.

## Design — measure, record, compare
### Part A — a canonical case set
A small, fixed set of synthetic cases with **known ground truth**, one per analysis family:
cells, condensates, puncta, fibrils, brightfield objects, a tracking case. Small, seeded, committed —
this is a *benchmark*, so the inputs must never change silently. Reuse `fixtures_synthetic`; if a case
needs a new generator, add it there.

**Ground truth must be constructed, not derived from a PyCAT run** — otherwise the benchmark measures
self-consistency and would happily track a drifting method as "stable."

### Part B — the recorded metrics
Per case, per release: Dice/IoU vs ground truth, matched-detection F1, object count error, key derived
measurements (partition coefficient, mean size), and runtime. Runtime is included deliberately —
performance regressions are real and currently invisible.

Write to a committed `benchmarks/results.jsonl` (append-only, one record per version). JSONL because it
diffs cleanly and never rewrites history.

### Part C — the comparison, and what fails
```python
def compare_to_baseline(current, baseline, *, tolerances) -> list[Regression]
```
- **Fail** on a metric moving beyond a declared tolerance in the *worse* direction.
- **Report, don't fail**, on improvement — but record it, because an unexplained improvement is also
  worth a look (it often means the ground truth or the case changed).
- Tolerances are **declared per metric with a justification** (e.g. Dice ±0.02 covers seeded-RNG
  variation; anything larger is a real change). Do not tune tolerances to make a run pass — that
  reproduces the exact error the filter-sensitivity programme exists to catch.

### Part D — how it runs
Mark `slow`/`benchmark`, **excluded from the default `pytest -m core`** run so it never slows the
per-change loop. Run it deliberately before a release, or on a schedule. One command should produce
the comparison table and the pass/fail verdict.

**This is not a CI gate on every commit.** At this release cadence that would be noise; the value is
in the trend across releases, not in blocking a single change.

## Part E — the artifact
A short report: metrics per case, delta vs the previous release, and delta vs a pinned reference
release. Over time this becomes a genuinely useful figure for the manuscript's rigor section —
*"segmentation quality on canonical cases across N releases"* is a claim very little academic software
can make.

## Tests
- The suite runs end to end on the canonical cases and writes a well-formed record.
- `compare_to_baseline` flags an injected regression (perturb a metric beyond tolerance) and passes an
  unchanged run.
- An improvement is reported, not failed.
- The results file is append-only — a rerun of the same version does not rewrite prior records.
- Ground-truth fixtures are deterministic under a fixed seed (run twice, identical metrics).

## Steps
1. `benchmarks/cases.py` — the canonical case set, built on `fixtures_synthetic`, with constructed
   ground truth.
2. `benchmarks/run_suite.py` — run all cases, compute metrics via `benchmark_tools`, append to
   `results.jsonl`.
3. `compare_to_baseline` + declared tolerances with justifications.
4. The report artifact (table + deltas).
5. Mark `benchmark`; exclude from `core`; document the command in CONTRIBUTING.
6. Tests above.
7. Full `pytest -m core` green (the suite itself does not run there).
8. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG, including the **first recorded
   baseline** — that record is the deliverable.

## Definition of done
- A fixed canonical case set with constructed ground truth.
- Per-release metrics recorded append-only, including runtime.
- Baseline comparison fails on worse-than-tolerance drift, reports improvements, and never
  silently absorbs a change.
- Tolerances declared with justifications.
- Excluded from the default test run; one command to execute and compare.
- Full `pytest -m core` green.

## Cautions
- **Ground truth must be constructed, not produced by PyCAT.** Otherwise the benchmark tracks the
  method drifting away from correctness and calls it stable.
- **Never tune a tolerance to make a run pass.** A metric moving beyond tolerance is a finding; record
  it and investigate.
- Keep the case set FIXED. Changing a case invalidates the history — if a case must change, version it
  as a new case rather than editing the old one, so prior records stay meaningful.
- Do not gate every commit on this; the value is the cross-release trend.
- Include runtime, but treat it advisory — sandbox/CI machines vary, so a runtime change is a prompt
  to look, not a failure.
