# Claude Code spec — Decompose `batch_step_registry.py`: the fourth concentration point

**Date:** 2026-07-19 · **Target tree:** 1.6.144 · Verified against the 1.6.144 tree. The one tracked
concentration point never addressed — and the only one that **grew** across the audit window
(1613 → 1663). Behaviour-preserving; no new features. Lower risk than `ui_modules.py` because the
structure is already flat and regular.

## Verified structure (1663 lines)
The file is **26 top-level `replay_*` functions** plus ~10 shared helpers. There are no classes and no
deep nesting — it is a long, flat dispatch table:
```
helpers (~300 lines):  _get_data  _derive_split_companion_path  _source_path_for_recorded_channel
                       _load_image  _resolve_channel_for_layer  _save_array  _raw_counts
                       _normalize_to_float  _resolve_image_layer
replay_* (26):         open_image, preprocessing, cellpose_segmentation, condensate_analysis,
                       save_and_clear, upscaling, calibration_correction, background_removal,
                       measure_line, open_stack, ts_cellpose_keyframe, set_frame_range,
                       auto_crop_roi, bf_preprocess, bf_condensate_segmentation,
                       bf_cell_segmentation, ivf_preprocess, ivf_field_summary,
                       ivf_size_distribution, ivf_spatial_metrology, ivf_segmentation,
                       ivbf_preprocess, ivbf_segmentation, …
```
Every one has the identical signature `(state, image_path, params, output_dir)` — so they are
trivially relocatable, and the natural grouping is already encoded in their name prefixes.

## Why this is a good target
- **Flat and regular** — no inheritance to untangle, uniform signatures, name-prefixed families.
- **Real safety net** — `test_batch_matches_the_recording.py`, the batch-composition drift guard from
  OperationSpec increment 3 (every batch step's declared `operations` must exist in the catalog), and
  the **route-equivalence suite**, which exercises batch replay end-to-end against headless and
  session-reload results. A behaviour change fails loudly.
- It is the file that grew while everything else was being disciplined.

## Target
**`batch_step_registry.py` ≤ 700 lines** (−58%), retaining the `_STEP_MAP` dispatch table, the shared
helpers, and nothing else. Handlers move into a package.

## The decomposition — split by the prefixes the names already declare
Create `src/pycat/batch/steps/` and move handlers verbatim:
| module | handlers |
|---|---|
| `io_steps.py` | `open_image`, `open_stack`, `save_and_clear`, `set_frame_range`, `auto_crop_roi` |
| `preprocessing_steps.py` | `preprocessing`, `upscaling`, `calibration_correction`, `background_removal` |
| `segmentation_steps.py` | `cellpose_segmentation`, `ts_cellpose_keyframe` |
| `brightfield_steps.py` | `bf_preprocess`, `bf_condensate_segmentation`, `bf_cell_segmentation`, `ivbf_*` |
| `invitro_steps.py` | `ivf_preprocess`, `ivf_segmentation`, `ivf_field_summary`, `ivf_size_distribution`, `ivf_spatial_metrology` |
| `analysis_steps.py` | `condensate_analysis`, `measure_line`, and the remainder |

Shared helpers → `src/pycat/batch/steps/_common.py` (they are used across families; keep ONE copy and
import it — duplicating them is the failure mode to avoid).

`batch_step_registry.py` retains `_STEP_MAP`, importing the handlers. **The dispatch table must remain
in one place** — it is the thing the OperationSpec composition guard reads.

## Rules (identical to the two successful decompositions)
- **Move, don't rewrite.** Cut, paste, fix imports. Behaviour changes are separate commits.
- **One family per commit**, `pytest -m core` between each.
- **No test may be edited to make a move pass.**
- Preserve any import path other modules rely on — re-export from `batch_step_registry` if anything
  imports a handler directly (grep first).
- Convert broad handlers only in code you are already moving; annotate deliberate ones
  `# broad-ok: <reason>`. There are few here (5), so this is minor.

## Tests
- All existing batch tests pass **unmodified** — especially `test_batch_matches_the_recording.py` and
  the route-equivalence batch route.
- The OperationSpec batch-composition drift guard still passes (it reads `_STEP_MAP`).
- Add a small structural test: every key in `_STEP_MAP` resolves to an importable callable with the
  `(state, image_path, params, output_dir)` signature — cheap, and it catches a bad move instantly.
- **Lower the `batch_step_registry.py` line ratchet** (currently 1663) to the achieved value.

## Steps
1. Create `batch/steps/` with `_common.py` (shared helpers moved once).
2. Move `io_steps` family; run tests.
3. Move `preprocessing_steps`; run tests.
4. Move `segmentation_steps`; run tests.
5. Move `brightfield_steps`; run tests.
6. Move `invitro_steps`; run tests.
7. Move `analysis_steps` / remainder; run tests.
8. Add the `_STEP_MAP` signature test; lower the line ratchet.
9. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG reporting measured
   before/after.

## Definition of done
- `batch_step_registry.py` ≤ 700 lines, holding `_STEP_MAP` and imports.
- 26 handlers live in six prefix-named modules; shared helpers exist once in `_common.py`.
- Every pre-existing batch and route-equivalence test passes unmodified.
- `_STEP_MAP` signature test added; line ratchet lowered.
- CHANGELOG reports the measured reduction.

## Cautions
- **Keep `_STEP_MAP` in one place** — the OperationSpec composition guard reads it, and splitting the
  dispatch table would defeat the auditability that increment 3 established.
- **Do not duplicate the shared helpers** into each family module. One `_common.py`, imported.
- Route equivalence exercises batch replay — if it starts failing, a move changed behaviour. Revert
  rather than adjusting the test.
- Grep for direct imports of individual `replay_*` functions before moving; re-export if any exist.
