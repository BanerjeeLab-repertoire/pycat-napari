# Claude Code spec — Comparative phenotyping increment 3: cross-condition figures

> **✅ STATUS — DONE, shipped in 1.6.135 + 1.6.144** (stamped 2026-07-20 from a CHANGELOG cross-reference). Cross-condition figure library (Parts A–C) shipped 1.6.135; Part D brushing + Step-2 UI entry point 1.6.144; unit-marker cohort emit 1.6.151.

**Date:** 2026-07-19 · **Target tree:** 1.6.133 · Verified against the 1.6.133 tree. The visible
payoff of the comparative-phenotyping arc: figures that compare conditions, built on the consolidated
long table. **PREREQUISITES (both met):** increment 1 (`sample_metadata.py`, wired into batch + session
at 1.6.133) and increment 2 (`utils/consolidated_table.py` — `melt_object_measurements`,
`build_image_long_table`, `consolidated_columns`, `ConsolidatedLongWriter`). Touches a new
comparative-figures module + tests. Additive.

## Why this, why now
The substrate exists: a tidy long table of `object × measurement × condition × provenance`. Nothing
yet turns it into the figure a multi-mutant paper needs. Verified: `analysis_plots.py` has **no**
grouped/faceted plotting (no box, violin, strip, or facet functions) — every existing plot is
single-population. So comparative figures are genuinely new, not a refactor.

## The scientific requirement that shapes the design: replicate-honest statistics
PyCAT already understands pseudoreplication and says so in three places (`ui_analysis_mixin.py:43/57`,
`general_image_tools.py:839`, `ui_modules.py:5497`) — *"pixels are pseudoreplicated (error bars come
out too small)"*. The same trap is fatal one level up: **treating every object as an independent
sample across conditions inflates significance**. 500 condensates from 3 dishes is n=3, not n=500.

So this module must aggregate to the **biological unit** before any error bar or test:
```
objects → per-image (or per-replicate) summary → condition-level statistic
```
- The biological unit is declared, not guessed — a parameter (`unit='replicate'` / `'image'`), defaulting
  to the finest *declared* replicate field from `sample_metadata` if present, else the image.
- Plots show BOTH levels honestly: individual objects at low emphasis, replicate means as distinct
  markers, condition summary as the box/violin. This is the standard "superplot" convention and it
  makes the n visible rather than hidden.
- **Report n at every level** in the figure or its caption data (`n_objects`, `n_images`,
  `n_replicates`). A figure that shows error bars without saying what n they came from is the exact
  dishonesty this spec exists to prevent.

## Part A — the module (`utils/comparative_figures.py`, Qt-free)
Takes the consolidated long table + a grouping spec; returns a matplotlib figure plus the summary
frame behind it (so the numbers are inspectable, never figure-only):
```python
def aggregate_to_unit(long_df, *, measurement, unit_cols, condition_cols) -> pd.DataFrame
def condition_comparison(long_df, *, measurement, condition_cols, unit_cols=None,
                         kind='box', show_objects=True, show_units=True) -> (Figure, pd.DataFrame)
def dose_response(long_df, *, measurement, dose_col, condition_cols=None) -> (Figure, pd.DataFrame)
def measurement_matrix(long_df, *, measurements, condition_cols) -> (Figure, pd.DataFrame)
```
Keep it headless (`matplotlib` Agg-safe, no Qt import) so it is `core`-testable — the same contract the
science modules already satisfy.

## Part B — the figure types (start with three)
1. **Condition comparison** — box/violin per condition for one measurement, objects + replicate means
   overlaid (the superplot). The workhorse.
2. **Dose–response** — measurement vs a numeric condition field (e.g. `dose`), replicate means with
   an error band; fit only if the user asks, and label the model when fitted.
3. **Measurement × condition matrix** — small-multiples grid for scanning several measurements at once.
Defer heatmaps/PCA/clustering — those invite the phenotypic-profiling direction PyCAT deliberately did
not take.

## Part C — statistics: honest by default, opt-in for tests
- Default: **descriptive only** — plot the distribution, the replicate means, and report n. No p-values
  unless explicitly requested.
- When a test IS requested: operate on the **aggregated unit values**, name the test in the returned
  summary frame, and refuse (with a stated reason) when assumptions clearly fail — e.g. fewer than 3
  units per condition. Do not silently fall back to a different test.
- No significance stars without the underlying n and test name available in the summary frame.

This mirrors the existing codebase contract (`test_no_silent_scientific_gates`): a refusal must be
loud, never a plausible-looking number.

## Part D — brushing (cheap, since the contract exists)
Route point selection through the existing `SelectionView`/`SelectionService` contract so clicking an
object point in a comparative plot selects that entity, and clicking a replicate/condition marker
selects the **cohort** it summarizes. If cohort selection is not yet supported by the selection model,
implement single-entity selection now and note the cohort case as blocked on the deferred
cohort/typed-target work — **do not build a second selection path.**

## Steps
1. `utils/comparative_figures.py`: `aggregate_to_unit` + the three figure functions, each returning
   `(Figure, summary_df)`.
2. Wire a UI entry point that reads the consolidated table produced by increment 2.
3. Brushing via the existing contract (single-entity now; cohort noted if blocked).
4. Tests (`core`): on synthetic multi-condition data with a KNOWN effect —
   - the effect is recovered (condition means ordered correctly);
   - **aggregation is enforced**: with 500 objects from 3 replicates, the reported n is 3 and the
     error bar matches replicate-level spread, NOT object-level (assert it is materially wider than the
     naive object-level SEM — this is the anti-pseudoreplication test);
   - a null dataset yields no claimed significance;
   - a condition with <3 units gets a stated refusal, not a p-value;
   - the returned summary frame carries `n_objects`/`n_images`/`n_replicates`.
5. Full `pytest -m core` green (complexity budget).
6. Ship: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (comparative figures on the
   consolidated table, replicate-honest by construction).

## Definition of done
- Three comparative figure types render from the consolidated long table.
- Every figure aggregates to a declared biological unit and reports n at each level.
- Statistics are descriptive by default; tests run on aggregated units, are named, and refuse loudly
  when assumptions fail.
- Each function returns the summary frame behind the figure.
- Selection routes through the existing contract; no second selection path.
- Full `pytest -m core` green.

## Cautions
- **Pseudoreplication is the headline hazard.** Objects are not independent samples across conditions.
  Aggregate to the declared unit before any error bar or test — and prove it with the widened-error-bar
  test, not just a comment.
- The biological unit is DECLARED, never inferred from data shape.
- No p-value without a named test, an n, and satisfied assumptions.
- Return the summary frame — a figure whose numbers cannot be inspected is a black box, which is the
  opposite of PyCAT's philosophy.
- Keep the module Qt-free so it is `core`-testable.
- Don't build heatmap/PCA/clustering profiling here; that is a different (and deliberately declined)
  direction.
