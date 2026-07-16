# Claude Code spec — Filtering-defaults sensitivity harness (framework-first)

## ✅ STATUS — DONE, shipped in 1.6.80 (executed against the 1.6.79 tree)
`pytest -m core`: **718 passed, 2 skipped** (was 707). Definition of done met: `sweep_invariance` +
the three check types exist and drive the REAL functions; both known inverters have a passing
positive control and a negative control that proves detection; the seeded registry + parametrize
makes the next case one row; no production behaviour changed.

**The spec's premises held** — a first, across this batch of specs. Both cases are fixed exactly as
described (`r2_min=0.0` at `molecular_counting_tools.py:140`; the contrast-to-noise form
`(mean_cell - bg)/noise_sd` in `ts_cellpose_tools.py`), `fixtures_synthetic` / `imaging_realism`
exist, and `defocus_r2_max` is indeed deprecated.

**Both inversions reproduce on the real functions**, which is what makes the harness evidence:
- **r2_min:** dim traces fit at R²=0.9951, bright ones at R²=0.9985 — *higher purely because N is
  higher*, which is the mechanism, measured rather than asserted. The recovered mean then climbs
  **monotonically** with the gate: 41.3 → 51.3 → 64.4 (true 44). The spec's 0.999 → ~77 is the same
  family; on this fixture 0.999 keeps 1 trace of 20 and reports 272, so **0.998 is used as the
  primary negative control** — a realistic partial gate rather than a degenerate one.
- **pedestal:** reproduces the spec's table exactly — the old ratio keeps {3,4} at 0, {4} at 100, and
  **nothing** from 500 up; the current form is invariant at 0/100/500/2000.

**One correction to the fixture, worth knowing:** the old ratio form is degenerate at pedestal 0 if
the synthetic background is zero (bg → 0, ratio explodes; production guards this). A real sample has
background above zero, so the scene models a background floor of 20 counts with the pedestal *on top*
— which is also what makes the spec's own table (expr=0 → ratio 1.0) come out right.

**Found while building it:** `filter_cells_by_transfection`'s docstring **summary** still described
the removed ratio (*"SNR = mean(cell intensity) / background"*). The body and the parameter note were
fixed; the opening paragraph was left describing the bug as though it were the design. Corrected, and
now pinned by the pedestal test.

**The scale check** has no validated production case, so it is proved against an explicit
known-answer stand-in — a harness that has never been seen to fire is an intention, not machinery.

**Mutation-checked:** blinding `sweep_invariance` turns all three negative controls red.

**Next increment** (needs Gable's prioritisation first, per the spec): the
select-for-the-measured-quantity class — segmentation's `local`/`global_snr_threshold` (~10 sites)
and condensate `bleach_r2_min`, which share the `r2_min` shape.

**Date:** 2026-07-16 · **Target tree:** 1.6.70 · Verified against the 1.6.70 tree. Builds the
*machinery* that catches "a silent filter default that inverts a scientific result," validated on the
TWO cases already proven to do so. This is increment 1 of a larger audit — it establishes the harness
and its contract; later increments point it at the remaining dangerous defaults. Touches `tests/` +
possibly a small `utils/` helper; no production behaviour change. Manuscript-rigor work.

## Why framework-first
The roadmap's "115 filtering defaults" audit names two DEMONSTRATED inverters. Verified in the tree,
**both have already been fixed** — which is exactly why they make perfect harness fixtures (a fix to
lock in, and a known-bad value to prove the harness catches):
- `molecular_counting_tools.count_molecules_single(r2_min=…)` — now defaults `r2_min=0.0`
  (`molecular_counting_tools.py:140`), with a docstring warning that a high R² gate selects for
  brightness not correctness (the `0.999` case reported a population mean of 77 vs true 44).
- `ts_cellpose_tools.filter_cells_by_transfection(snr_threshold=…)` — now a **contrast-to-noise**
  form (`(mean_cell − bg)/noise_sd`), pedestal-invariant, "verified identical at 0, 50, 100, 500,
  2000 counts" (`ts_cellpose_tools.py:365`+). The old **ratio** form with default 2.0 called every
  transfected cell untransfected on a 500-count pedestal.

So the harness gets a POSITIVE control (the fixed default passes) and a NEGATIVE control (the old bad
default fails) for each — you can trust the machinery before aiming it at unknowns.

## The invariant the harness enforces
For a filter default, construct data where the true answer is KNOWN, then vary the parameter (and/or
the nuisance variable it should be invariant to) and assert the recovered scientific answer does not
silently INVERT. Three failure signatures (build all three as reusable check types):
1. **Selection bias** — the gate correlates with the very quantity being measured, so filtering shifts
   the population statistic (the `r2_min` case: R² of a bleaching fit rises with N, so the gate
   selects for brightness → mean 77 vs 44).
2. **Offset sensitivity** — the gate breaks under a camera pedestal / background offset (the SNR-ratio
   case: pedestal in numerator and denominator drags a ratio toward 1).
3. **Scale sensitivity** — a gate expressed in PIXELS silently excludes populations when pixel size
   differs from what it was tuned on (the brightfield `min_diameter_px` class — for a LATER increment,
   but build the check type now so it's ready).

## Part A — the harness helper (`tests/filter_sensitivity.py`, importable by tests)
A small reusable framework, mirroring the known-answer style of `imaging_realism.py` /
`test_batch_matches_the_recording.py` and reusing `tests/fixtures_synthetic.py`:
```python
def sweep_invariance(run, *, param, values, truth, tol, invariant_to=None):
    """Call run(param=v [, nuisance=n]) across `values` (and optional nuisance levels),
    return the recovered answer at each, and assert it stays within `tol` of `truth`
    — i.e. the parameter (or the nuisance) does NOT flip the result."""
```
Plus the three named check types (`assert_no_selection_bias`, `assert_offset_invariant`,
`assert_scale_invariant`) expressed on top of `sweep_invariance`. The helper drives the REAL
production functions (no reimplementation) with synthetic data whose truth is known.

## Part B — validate the harness on the 2 known inverters (`tests/test_filter_sensitivity.py`)
For EACH proven case, two tests — positive + negative control:
1. **molecular_counting / r2_min (selection bias):**
   - Build a synthetic cell population with a KNOWN mean molecule count (e.g. true N=44), mixing
     low-N (low-R²) and high-N (high-R²) traces via `fixtures_synthetic`.
   - POSITIVE: with the current default `r2_min=0.0`, `count_molecules_single` over the population
     recovers ~44 (within tol) — the fixed default passes.
   - NEGATIVE: with `r2_min=0.999`, the recovered mean is biased HIGH (~77) — assert the harness
     `assert_no_selection_bias` FAILS on this value. (This proves the harness detects the inversion.)
2. **filter_cells_by_transfection / pedestal (offset sensitivity):**
   - Build labeled cells with known transfected/untransfected truth on a fluor frame; add a camera
     pedestal at 0, 100, 500, 2000 counts.
   - POSITIVE: the current contrast-to-noise form calls the same cells transfected at every pedestal
     (`assert_offset_invariant` passes).
   - NEGATIVE: a mean/background RATIO with threshold 2.0 (reconstruct the old form as a local lambda
     — do NOT reintroduce it into production) flips to "all untransfected" at pedestal 500 — assert
     the harness catches it.

Marking: `core` (pure numpy/skimage, no Qt/napari — these are science functions; confirm they import
headlessly, they're in the `test_headless_science` set).

## Part C — the registry stub for later increments
Add a small declarative list the later increments will grow — each entry = (function, param, check
type, how to build known-answer data). Seed it with the 2 validated cases. This is where increment 2
(the SNR/R² select-for-measured-quantity class: segmentation `local/global_snr_threshold=1.0` ×~10,
condensate `bleach_r2_min`) gets appended. Do NOT test those here — just leave the seeded registry +
a `@pytest.mark.parametrize` over it so adding a case is one row. Note in a comment that VPT's
`defocus_r2_max` is DEPRECATED/unused (`vpt_tools.py:950`) and must NOT be added.

## Steps
1. `tests/filter_sensitivity.py`: `sweep_invariance` + the three check types.
2. `tests/test_filter_sensitivity.py`: the 4 tests (positive+negative for each of the 2 inverters),
   using `fixtures_synthetic`.
3. The seeded registry + parametrize scaffold for future cases.
4. Full `pytest -m core` green — the 2 positives pass, the 2 negatives are asserted-to-fail-the-check
   (i.e. the test asserts the harness raises on the bad value).
5. Ship: own version + PyPI push + commit (EXPLICIT filenames: the two new test files + any
   `fixtures_synthetic` additions + pyproject + CHANGELOG) + CHANGELOG entry (filtering-defaults
   sensitivity harness, framework validated on the 2 known inverters, registry ready for the SNR/R²
   class next).

## Definition of done
- `sweep_invariance` + selection-bias / offset / scale check types exist and drive REAL functions.
- Both known inverters have a passing positive control and a negative control proving the harness
  DETECTS the inversion.
- A seeded registry + parametrize makes adding the next dangerous default one row.
- Full `pytest -m core` green; no production code changed (tests + optional fixtures only).

## Cautions
- The harness drives the REAL production functions with known-answer synthetic data — never
  reimplement the science being tested.
- Do NOT reintroduce the old bad forms into production; reconstruct them as LOCAL test lambdas only,
  to prove the harness catches them.
- Keep it headless (`core`) — these are science functions in the `test_headless_science` set; no Qt.
- Scope is the framework + the 2 validated cases + the registry scaffold. Do NOT sweep the other
  ~37–113 defaults here — that's the next increment, and it needs Gable's prioritization (the SNR/R²
  class is next).
- Watch the complexity budget on the helper; keep functions small.
