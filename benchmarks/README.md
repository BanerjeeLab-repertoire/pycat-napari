# PyCAT Validation Suite

A standing **per-release regression benchmark**. The unit test suite answers *"did anything break?"* per
commit; this answers a different question that the release cadence makes important: *"is segmentation
quality on our canonical cases the same as it was ten releases ago?"* A change can keep every test green
while moving Dice from 0.94 to 0.89 — this is what catches that slow drift.

## Run it

```
python -m benchmarks.run_suite <version>
```

It runs every canonical case (`benchmarks/cases.py`) against its **constructed** ground truth, prints the
metrics and the deltas vs the last recorded baseline, and **appends** one record to
`benchmarks/results.jsonl`. Exit code is non-zero if any metric moved beyond its declared tolerance in the
worse direction. This is **not** a per-commit CI gate — the value is the cross-release trend.

## Rules

- **Ground truth is constructed, never produced by a PyCAT run** — otherwise the suite would track a
  drifting method as "stable."
- **The case set is FIXED.** Changing a case invalidates the recorded history; add a new case (new name)
  instead of editing an old one.
- **Tolerances are declared with justifications** (`run_suite._TOLERANCES`). Never tune a tolerance to make
  a run pass — a metric beyond tolerance is a finding to record and investigate.
- `results.jsonl` is **append-only**. It diffs cleanly and never rewrites history.
- Runtime is recorded but **advisory** — machines vary, so a runtime change is a prompt to look, not a
  failure.

The machinery is unit-tested in `tests/test_validation_suite.py` (marked `core`).
