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

## The scale benchmark

`benchmarks/condensate_scale.py` answers a different question again: **does segmentation quality
depend on how big the objects are?** It matters because every scale in the condensate path descends
from `ball_radius`, and `ball_radius` descends from one line the user drew across one object — so the
whole chain is a single band-pass centred on a single hand-measured scale.

```
python -m benchmarks.condensate_scale
```

Four regimes — `small` (radii 3-8 px), `irregular` (major axis 3-20 px), `large` (radii 10-20 px) and
`mixed` (small AND large in the same field) — each with CONSTRUCTED ground truth placed before the PSF
blur, on the same terms as `cases.py`. Results are reported per true-radius bin and split three ways
(**detected**, **coverage**, **area ratio**) rather than as one false-negative number, because a single
figure mixes "missed the object" with "found it and drew it too small" — different causes, different
fixes, and in the `mixed` regime the first dominates while the headline number hides it.

This is what `toolbox/segmentation/object_scale.py` and `boundary_refit.py` were built against; the
numbers quoted in their docstrings come from here.
