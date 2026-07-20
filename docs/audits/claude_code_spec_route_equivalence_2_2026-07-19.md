# Claude Code spec — Route equivalence: expand the matrix to the canonical workflows

**Date:** 2026-07-19 · **Target tree:** 1.6.144 · Verified against the 1.6.144 tree. The
cross-route equivalence matrix shipped at **three** workflows by design ("not the fifteen an audit
proposed — three that genuinely run will grow"). Growing it is the highest-value reproducibility work
outstanding, and it is the one the external audit called *"the beginning of the most important
validation program in PyCAT."* Test-only unless a divergence is found — **in which case the divergence
is the finding, not a tolerance to loosen.**

## Current state (verified)
`tests/test_route_equivalence.py` + `tests/route_equivalence.py` implement the harness and three
workflows: `rolling_ball`, `puncta`, `vpt_msd`, each asserted identical across **headless / batch
replay / session reload**. Tolerances carry written justifications (e.g. *"1-ULP CSV decimal
round-trip"*). The `_WORKFLOWS` dict makes adding one a single entry.

## What to add, in priority order
Add **three per increment**, not all at once. Each must genuinely run before the next is started.

**Increment A (this spec):**
1. **Cellpose segmentation** — the most-used path and the one with the most parameter surface.
   If Cellpose is unavailable in CI, gate with the existing optional-dependency skip pattern rather
   than weakening the test.
2. **Colocalization** — two-channel input; exercises channel assignment, which is a distinct
   parameter-assembly risk from single-channel work.
3. **Time-series condensate analysis** — a lazy stack through the whole chain; the route most likely
   to diverge because batch and interactive materialize differently (this is precisely the class of
   bug `test_batch_matches_the_recording` was written for).

**Later increments (note, don't build):** FRAP, in-vitro condensate, batch cellular analysis, QC,
comparative-phenotype table generation.

## What to compare — go beyond the arrays
The audit's sharpest point: *"Two routes can produce numerically similar arrays while differing in
scientifically important metadata."* So extend the comparison for the new workflows (and retrofit to
the existing three if cheap) to include, where each is meaningful:
- output **DataFrame schema** (column names and order) and **units** columns;
- **NaN policy** — a route that emits 0.0 where another emits NaN is a real divergence;
- **layer names** and **semantic tags** (`role`, `target`) on produced layers;
- **pixel size / calibration** carried into results;
- recorded **provenance** / operation ids.

Implement as an optional `compare_metadata=` in the harness so existing workflows are unaffected until
retrofitted. Do NOT block this increment on a full `ArtifactSnapshot` abstraction — add the fields that
are cheap and meaningful now.

## Steps
1. Add the three workflows to `_WORKFLOWS`, each with fixture, params, routes, comparison fn, and a
   **justified** tolerance.
2. Extend the harness with optional metadata comparison (schema, units, NaN policy, tags).
3. Where a route cannot be driven for a workflow, record it explicitly in the matrix docstring — a
   documented gap, never a silent skip. That list is itself a finding about the headless API.
4. If any divergence appears: **report it in the CHANGELOG and roadmap as a finding**; fixing it is
   separate work with its own spec.
5. Full `pytest -m core` green.
6. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG stating which workflows now
   have route equivalence and what (if anything) diverged.

## Definition of done
- Six canonical workflows asserted equivalent across every route that can run them.
- Metadata (schema/units/NaN/tags) compared, not just arrays, for the new workflows.
- Undriveable routes documented, not skipped silently.
- Every tolerance justified in a comment.
- Full `pytest -m core` green.

## Cautions
- **A divergence is a finding.** Loosening a tolerance to make a route pass defeats the entire point.
- Don't drive Qt; where the UI route can't be called headlessly, compare parameter assembly instead.
- Use small deterministic synthetic fixtures; seed randomness; no large binaries.
- Three per increment. A twelve-workflow expansion attempted at once will stall.
