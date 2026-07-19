# Claude Code spec — Cross-route workflow equivalence matrix

**Date:** 2026-07-18 · **Target tree:** 1.6.121 · Verified against the 1.6.121 tree. The strongest
recommendation from the external architecture audit: stop adding isolated tests and start asserting
that the SAME workflow produces the SAME numbers through every route PyCAT offers. Test-only; no
production change unless a divergence is found (in which case the divergence is the finding). Touches
`tests/` and possibly small headless-entry-point exports.

## The gap (verified)
PyCAT has strong *pairwise* equivalence tests — `test_vpt_gpu_equivalence`,
`test_vpt_parallel_equivalence`, `test_refine_fast_slow_parity`, `test_batch_matches_the_recording`
(4 tests, all about preprocessing scale semantics). What does **not** exist is a matrix asserting, for
a canonical workflow, that:
```
interactive/UI execution ≈ headless function call ≈ batch replay ≈ reloaded-session rerun
```
This matters because PyCAT deliberately exposes each operation through several routes (UI, batch
recorder/replayer, Navigator, headless API, session restore). Each route assembles parameters
independently. The audit's point is precise: **isolated tests protect each route's internals; nothing
currently proves the routes agree with each other.** A divergence here is the highest-severity class
of bug PyCAT can have — the same analysis silently yielding different numbers depending on how it was
launched — and it is exactly what a reviewer would ask about reproducibility.

Precedent that this class of bug is real, not hypothetical: `test_batch_matches_the_recording` exists
because batch preprocessing was passing a *normalised* image where the UI passed **raw counts**, and
the rolling-ball radius is not scale-invariant. That was a genuine cross-route divergence — found by a
test written for exactly this reason, and only for one step.

## Design — a harness, then a small matrix
### Part A — `tests/route_equivalence.py` (the harness)
```python
def run_all_routes(workflow, image, params) -> dict[str, Any]:
    """Execute `workflow` through each available route; return {route_name: result}."""
    # 'headless'  — call the toolbox function(s) directly
    # 'batch'     — build a recorded step list, run it through the batch replayer
    # 'session'   — write a session, reload it, re-run
    # 'ui'        — only where a headless-callable UI entry point exists (see below)

def assert_routes_agree(results, *, compare, tol):
    """Compare each route against the reference route, naming WHICH route diverged and by how much."""
```
- `compare` is per-workflow: array equality for masks/labels (or Dice ≥ threshold if a documented
  non-determinism exists), `np.allclose` with an explicit tolerance for scalar measurements, DataFrame
  column-subset comparison for tables.
- **Tolerances must be justified, not tuned to pass.** If two routes differ, the default assumption is
  a bug in a route — not a tolerance that needs loosening. Any non-zero tolerance carries a comment
  saying what physical/numerical source justifies it (e.g. float32 accumulation order). A route that
  needs a *loose* tolerance to pass is a finding to report, not to absorb.
- The UI route is only included where a headless-callable entry point exists; **do not drive Qt**.
  Where the UI's parameter assembly is inseparable from the widget, assert the narrower thing: that
  the UI's parameter-building function produces the same params the other routes use. Record which
  workflows could not include a UI route — that list is itself a useful structural finding.

### Part B — the matrix (start at 3, not 15)
The audit proposes 10–15 workflows. **Start with three**, chosen for coverage of distinct data shapes
and failure modes, then grow:
1. **2D cellular segmentation + measurement** — the most-used path; exercises preprocessing scale
   semantics (the known-divergence area).
2. **Puncta analysis** — object detection + per-object measurement + a filtering gate (exercises the
   filter defaults the sensitivity harness covers).
3. **VPT tracking → MSD → viscosity** — the flagship numeric chain, with a validated reference value.
Add later, one per increment: colocalization, FRAP, in-vitro condensate, time-series segmentation, QC,
comparative-phenotype table.

Each entry declares: the fixture, the parameter set, the routes to compare, the comparison function,
and the justified tolerance. Growing the matrix should be one row.

### Part C — fixtures
Use the existing synthetic fixtures (`tests/fixtures_synthetic.py`) — small, deterministic, known
ground truth. **Do not** add large real files to the repo. Seed all randomness.

## Steps
1. `tests/route_equivalence.py`: `run_all_routes` + `assert_routes_agree` with per-route error
   reporting (name the diverging route and the magnitude).
2. `tests/test_route_equivalence.py`: the three workflows, parametrized over the matrix.
3. Where a route cannot currently be driven headlessly, record it explicitly (a documented gap in the
   matrix, not a silent omission) — that list tells you where the headless API is incomplete.
4. If a divergence is found: **report it, do not paper over it.** Add it to the CHANGELOG/roadmap as a
   finding; fixing it is separate work with its own spec.
5. Full `pytest -m core` green.
6. Ship: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (cross-route equivalence
   matrix: three canonical workflows asserted identical across headless/batch/session).

## Definition of done
- A reusable harness runs a workflow through every available route and compares results, naming the
  diverging route on failure.
- Three canonical workflows are asserted equivalent across at least headless, batch replay, and
  session reload.
- Routes that cannot be driven headlessly are documented, not skipped silently.
- Every tolerance has a written justification.
- Adding a fourth workflow is one row.
- Full `pytest -m core` green.

## Cautions
- **A divergence is a finding, not a tolerance problem.** Loosening a tolerance to make a route pass
  would defeat the entire purpose of this spec.
- Don't drive Qt. Compare parameter assembly where the UI can't be called headlessly.
- Start at three workflows. A 15-workflow matrix written at once will be abandoned; three that
  genuinely run will grow.
- Use small deterministic synthetic fixtures; seed randomness; no large binaries in the repo.
- This is test-only. If it reveals a product bug, that bug gets its own spec — do not fix production
  behaviour inside this one and thereby lose the record of what diverged.
