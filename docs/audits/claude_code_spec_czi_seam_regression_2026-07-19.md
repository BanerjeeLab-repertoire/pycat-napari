# Claude Code spec — Turn the CZI seam defect into a measurable regression test

**Date:** 2026-07-19 · **Target tree:** 1.6.144 · Verified against the 1.6.144 tree. The one prior-audit
priority that has been carried across **three consecutive audits without being closed**. Both external
reviews said the same thing: the CZI work is *"architecturally improved but not fully validated against
the reported defect."* This spec converts a visual observation into a measurement.

## Why this keeps not getting done, and why it matters
The reported symptom is a **left-side column discontinuity** in a real streaming CZI — a visible seam.
Everything built since (BioFormats routing, off-thread load, LRU + direction-aware prefetch, the scene
stack) plausibly *helps*, and the unit tests around them pass. But the audit's distinction is exact:

> *"scene-switching correctness ≠ within-plane mosaic assembly correctness"*

Verified: `tests/test_czi_bioformats_reader.py` contains **no seam or discontinuity assertion**. So
nothing in CI would notice if the defect returned — or tell you whether it is already gone. Every
subsequent refactor of the CZI path is therefore unguarded against the exact bug the path was rewritten
for.

**A visual bug with no measurement is a bug that cannot be closed.**

## The measurement
A seam is a *spatial* discontinuity: pixel statistics change abruptly across one column boundary in a
way they do not across neighbouring boundaries. That is directly measurable without knowing the cause.

```python
def column_seam_score(frame, x):
    """How discontinuous is the image across the vertical boundary at column x?

    Compare the mean absolute difference of the adjacent column pair straddling x
    against the distribution of that statistic for nearby column pairs.
    A seam is a boundary whose step is an outlier among its neighbours.
    """
    step_at_x   = np.abs(frame[:, x] - frame[:, x-1]).mean()
    neighbours  = [np.abs(frame[:, i] - frame[:, i-1]).mean()
                   for i in range(x-16, x+17) if i != x and i-1 >= 0]
    return (step_at_x - np.median(neighbours)) / (np.std(neighbours) + 1e-12)
```
A z-score ≳ 5 at a tile boundary is a seam; natural image structure does not produce that
systematically at one fixed column across many frames. **Scoring across many frames is what separates
a seam from a coincidence** — real structure moves, a tile boundary does not.

## Part A — the fixture problem (decide this first)
The real file is large and cannot go in the repo. Three options, in order of preference:
1. **Synthesize a mosaic CZI-like case** — construct a known multi-tile frame with a deliberate
   1-pixel offset in one tile, assemble it through the same code path, and assert the score detects
   it. This tests the *assembly logic* without the real file and can live in CI permanently.
2. **A tiny real crop** — if a small region spanning the suspect column can be extracted from the real
   file into a few-hundred-KB fixture, commit that. Best evidence, if the format permits cropping
   while preserving the tile structure that causes the seam.
3. **An opt-in local test** — marked `slow`/`local`, skipped unless an env var points at Gable's real
   file. Weakest (never runs in CI) but still converts "it looks wrong" into "run this and see the
   number."

**Do 1 unconditionally. Do 2 if feasible. Add 3 regardless**, because only the real file can confirm
the *reported* defect is gone.

## Part B — what the test asserts
1. **A clean synthetic mosaic scores low** everywhere (no false seam) — establishes the metric doesn't
   cry wolf on ordinary structure.
2. **A deliberately misassembled mosaic scores high at the injected boundary** — proves the metric
   detects the defect class.
3. **A frame read through the real CZI path scores low at every tile boundary** — the actual
   regression assertion, run against whichever fixture is available.
4. **Stability across frames** — the score at a given boundary does not spike on some frames and not
   others (which would indicate stale partial-frame assembly rather than a fixed offset).

## Part C — the diagnostic the audit asked for
The reviews listed the measurements they wanted alongside the seam check. Add a small
`scripts/czi_diagnostics.py` (a run-once tool, not CI) reporting for a given file:
- random-frame, forward-sequential, and alternating-frame read latency;
- cache hit rate and `openBytes`-equivalent call count;
- whether a stale request can complete after a newer one (generation check);
- the per-boundary seam scores for a sampled set of frames.
This is the artifact that makes the defect discussable with numbers, and it is reusable next time a
reader misbehaves.

## Steps
1. `column_seam_score` in a test helper (pure numpy, `core`).
2. Synthetic clean + injected-offset mosaic cases (assertions 1–2).
3. Wire assertion 3 to whichever real fixture is obtainable; add the opt-in env-var path.
4. Assertion 4 across a frame sample.
5. `scripts/czi_diagnostics.py`.
6. Full `pytest -m core` green.
7. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG. **Report the measured seam
   score on the real file if it was run** — that number either closes the defect or reopens it with
   evidence.

## Definition of done
- A seam metric exists, is unit-tested against clean and defective synthetic mosaics.
- The CZI read path is asserted seam-free on the best available fixture.
- An opt-in path exists to run the assertion against the real problematic file.
- A diagnostics script reports latency, cache behaviour, staleness, and seam scores.
- Full `pytest -m core` green.

## Cautions
- **Score across many frames.** A single frame cannot distinguish a tile seam from image content; a
  boundary that is anomalous on *every* frame is a seam.
- Do not commit the large real file. Synthesize, or crop small, or gate behind an env var.
- The metric must be normalized against neighbouring boundaries, not an absolute threshold —
  absolute pixel steps vary enormously with sample and exposure.
- **This spec does not fix the seam**; it measures it. If the measurement shows the defect is still
  present, that is a finding for a separate fix spec — with, for the first time, a number to verify
  against.
