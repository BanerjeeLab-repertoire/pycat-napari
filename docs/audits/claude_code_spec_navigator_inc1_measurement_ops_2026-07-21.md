# Claude Code spec — Navigator wiring increment 1: measurement operations in the catalog

**Date:** 2026-07-21 · **Target tree:** 1.6.269 · Verified against the 1.6.269 tree. **This is
increment 1 of 4**, deliberately re-cut into small independently-shippable pieces. The navigator arc
has been specced as one integration spec three times and has not moved in ~50 versions, while
self-contained build specs (publication features, decompositions) ship reliably. So this increment is
**data-only, headless, and independently testable** — no UI, no planner changes.

## Verified state
- `operation_catalog.json` holds **79 operations**: 63 `kind='toolbox'`, 16 `kind='ui'` — and every one
  produces an image or objects. There are **no measurement/interpretation operations**, so the planner
  can build the segmentation spine of a workflow but cannot reach the answer.
- The entry shape is clean and already carries everything needed:
  ```json
  {"op": "bandpass", "module": "fft_bandpass_tools", "function": "fft_bandpass",
   "kind": "toolbox", "inputs": ["image"], "produces": "image", "role": "image",
   "requirements": [], "target": null, "summary": "FFT bandpass filter", ...}
  ```
- Tests already guard the catalog hard: `test_operation_spec_matches_catalog` (the committed catalog
  must be the regeneration of the live spec), `test_operation_graph` (no dangling edges; every input
  role is produced or a root), `test_operation_requirements` (every requirement in the vocabulary has a
  human reason). **These are the safety net for this increment.**

## Scope — add measurement operations, nothing else
Add operations whose `produces` is a **measurement/result**, not an image or a label layer. Each binds
to a real function that already exists. Start with a focused set that closes the most-used question
paths:

| op | module → function | inputs | produces |
|---|---|---|---|
| partition coefficient | `invitro/partition` → the local partition measurement | objects + image | measurement |
| client enrichment | `partition_enrichment_tools` → enrichment | objects + image | measurement |
| coarsening fit | `condensate_physics/coarsening` → `fit_coarsening` | tracked objects | measurement |
| MSD / diffusion | `condensate_physics/msd` → `compute_msd`, `fit_anomalous_diffusion` | tracks | measurement |
| viscosity | `vpt/viscosity` → the Stokes-Einstein measurement | diffusion | measurement |
| FRAP recovery | `frap_tools` → `fit_frap_recovery` | intensity trace | measurement |
| colocalisation | `pixel_wise_corr_analysis_tools` → Pearson/Manders | two images + mask | measurement |
| spatial statistics | `spatial_metrology_tools` → `ripleys_l`, `pair_correlation_function` | objects | measurement |
| size distribution | `invitro/size_distribution` → the MLE fit | objects | measurement |
| region properties | `feature_analysis_tools` → the per-object table | objects + image | measurement |

**Bind to the real symbol.** Every entry's `module`/`function` must resolve — `test_lightweight_catalog`
already asserts `resolve_operation` returns a callable for a real op, so a wrong binding fails loudly.

**Declare `requirements` honestly.** Where a measurement needs calibration (ΔG, viscosity, any physical
unit), say so using the existing requirement vocabulary — `test_operation_requirements` enforces that
every requirement has a human-readable reason, which is exactly the message a user needs when the op is
unavailable. Do **not** invent a new gating mechanism here; increment 2 adds the quality gate.

**Extend the role vocabulary minimally.** These ops produce a *measurement*, which the current graph
vocabulary may not have. Add the one role needed and make sure `test_operation_graph`'s
"every input role is produced or a root" still holds — a measurement is a **terminal** product (nothing
consumes it), which the traversal test must tolerate.

## What this increment does NOT do
- No `evaluate_quality` call — that is increment 2.
- No UI, no dock, no `app_mode` — those are increments 3 and 4.
- No new science. Every op binds to a function that already exists and is already tested.

## Tests
- Every added op resolves to a real callable (`resolve_operation`).
- The committed catalog is still the regeneration of the live spec (existing test, unmodified).
- The operation graph has no dangling edges with measurements added; measurements are terminal and the
  traversal still passes.
- Every declared requirement is in the vocabulary and has a human reason.
- The op count rises from 79 by exactly the number added (a deliberate, reviewable delta).
- `test_the_composition_coverage_does_not_regress` still passes.
- No existing op's declaration changes.

## Steps
1. Add the measurement role to the vocabulary if absent; confirm the graph test tolerates terminal
   products.
2. Add the operations above to the spec, each with real module/function, inputs, produces, and honest
   requirements.
3. Regenerate the committed catalog from the spec (the tests demand they agree).
4. Run the navigator test suite; fix declarations, not tests.
5. Full `pytest -m core` green.
6. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG ("navigator vocabulary now
   includes measurement operations; planner can reach an answer, gating follows in the next step").

## Definition of done
- The catalog contains measurement operations bound to real functions, with honest requirements.
- All existing navigator tests pass unmodified; the graph remains traversable with terminal
  measurements.
- No UI, planner, or scientific behaviour changed.
- Full `pytest -m core` green.

## Cautions
- **Data-only increment.** Resist wiring the gate or a UI here — the whole point of the re-cut is that
  each piece ships alone.
- **Bind to real symbols**; a stale module path fails the resolve test, which is the intended guard.
- **Declare requirements honestly** — a measurement needing calibration must say so, so the reason
  reaches the user later.
- **Fix declarations, not tests.** The catalog tests are the net; if the graph test fails, the
  declaration is wrong.
- Keep the added set focused and reviewable — a hundred ops in one commit is unreviewable; ten that
  close real question paths is the right size.
