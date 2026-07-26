# Claude Code spec — Split `label_and_mask_tools.py`: physics does not belong in a masking module

> **✅ STATUS — COMPLETE (1.6.336–1.6.383). `label_and_mask_tools.py` is a 39-line re-export shim (from 1,592,
> −98%).** The condensate wetting physics moved to `condensate_physics/wetting.py` (Step 2, 1.6.336) and the
> masking halves to `toolbox/masks/{measurement (Step 3, 1.6.380), morphology (Step 4, 1.6.381), splitting
> (Step 5, 1.6.382), labels (Step 6, 1.6.383)}.py` — each a focused module, every move VERBATIM +
> characterization-pinned (tests written green pre-move, passing unchanged after via `__globals__` patching) +
> vanished-guard-recorded; mask outputs exactly equal; the `@tags_layer` operation-catalog provenance
> regenerated per move (provenance only). The per-file line ratchet is locked at the shim size (39). Physics no
> longer lives in a masking module; masking/splitting/measurement/labels are separate, focused modules.
>
> **Step 6 — labels.py + thin shim — DONE, 1.6.383.** The residual label-ops (`run_update_labels`/
> `run_convert_labels_to_mask`/`run_label_binary_mask`/`run_expand_labels`/`run_mask_logic_merge` + `_napari`)
> moved VERBATIM to `toolbox/masks/labels.py`; `tests/test_mask_labelops_characterization.py` (`base`, 9) pins
> them. `label_and_mask_tools.py` 266 → 39 (pure re-export shim); ratchet locked at 39.
>
> **Steps 1 + 3 — measurement.py — DONE, 1.6.380.** Characterization-test-first (coverage was thin):
> `tests/test_mask_measurement_characterization.py` (`base`, 6) pins `measure_region_props`,
> `run_measure_binary_mask`, `run_measure_region_props` at `rtol=1e-9` on synthetic inputs, committed green on
> the pre-move code, patching each function's own `__globals__` so it passes UNCHANGED after the move. Then the
> whole measurement concern (those three + the `MeasurementDialog` picker + its guarded Qt import block) moved
> VERBATIM to `toolbox/masks/measurement.py`; `label_and_mask_tools` re-exports the four public names (every
> caller unchanged). `label_and_mask_tools.py` 1,303 → 949; the move is recorded in `_DELIBERATE`.
>
> **Step 4 — morphology.py — DONE, 1.6.381.** The binary-morphology concern (structuring element, edge-extend,
> open/close, the `binary_morph_operation` orchestrator + its GUI wrapper, `opencv_contour_func`, and the
> watershed `split_touching_objects`) moved VERBATIM to `toolbox/masks/morphology.py`, characterization-first:
> `tests/test_mask_morphology_characterization.py` (`base`, 8) pins EXACT mask outputs (committed green pre-move,
> passing unchanged after via `__globals__` patching); `split_touching_objects` stays pinned by
> `test_group_c_geometry`. `label_and_mask_tools` re-exports the eight public names (every segmentation caller
> unchanged); `_napari()` copied (a staying label-op uses it). `label_and_mask_tools.py` 949 → 493; recorded in
> `_DELIBERATE`. 
>
> **Step 5 — splitting.py — DONE, 1.6.382.** `assess_and_split_touching` (228 lines — the assess-whether-to-split
> decision path, keyed on the neck ratio) moved VERBATIM to `toolbox/masks/splitting.py`. Already strongly pinned
> by `tests/test_group_c_geometry.py` (verdicts, resulting label counts, neck-ratio physics), so no new test was
> needed — those pins pass unchanged through the shim (byte-identical proof). `label_and_mask_tools` re-exports
> it; the `split_assessed` operation's catalog provenance was regenerated to `masks/splitting.py` (provenance
> only). `label_and_mask_tools.py` 493 → 266; recorded in `_DELIBERATE`. **Remaining: the residual label-ops
> (`run_update_labels`/`run_convert_labels_to_mask`/`run_label_binary_mask`/`run_expand_labels`/
> `run_mask_logic_merge` + the `_napari` helper) — the last masking concern to home — then the thin shim +
> ratchet (Step 6).**
>
> **Step 2 — the valuable physics move — DONE, 1.6.336.** `neck_geometry` + `fit_elastocapillary_length` (and
> its nested `_sigmoid`) moved **VERBATIM** to new `toolbox/condensate_physics/wetting.py`, beside the rest of
> the material-state work. Characterization was already in place — `tests/test_group_c_geometry.py` pins the
> neck-geometry sphere relation and the elastocapillary fit; it passes unmodified through the re-export, so the
> numbers are provably identical. `label_and_mask_tools.py` re-exports both public names (every caller
> unchanged; the module drops ~290 lines). The vanished-function guard records the move in `_DELIBERATE` with a
> reason. One dependency the physics used (`bbox_columns_from_regionprops`) was carried into the new module.
> **Remaining (Steps 3–5, separate commits):** `masks/morphology.py` (binary ops, `split_touching_objects`),
> `masks/measurement.py` (region props, binary-mask measures), `masks/splitting.py` (`assess_and_split_touching`).

**Date:** 2026-07-23 · **Target tree:** 1.6.324 · Verified against the 1.6.324 tree. **1,592 lines, 27
functions, 3 over the ratchet** — but the size is secondary. The real finding is that this module mixes
two unrelated concerns, and one of them is manuscript-facing physics filed under "masking."

## Verified structure
```
   228  assess_and_split_touching        ← mask morphology
   162  neck_geometry                    ← CONDENSATE PHYSICS
   130  fit_elastocapillary_length       ← CONDENSATE PHYSICS
   105  split_touching_objects           ← mask morphology
    74  run_measure_region_props         ← measurement
    73  run_measure_binary_mask          ← measurement
    72  run_binary_morph_operation       ← mask morphology
```

**`neck_geometry` and `fit_elastocapillary_length` are condensate physics.** Neck geometry and
elastocapillary length are fusion/wetting measurements — they belong with the material-state work in
`toolbox/condensate_physics/`, not in a module about labels and binary masks. Someone reading the
Methods section, or looking for the fusion physics, would not think to open `label_and_mask_tools.py`.

Coverage is thinner here — **6 test files** — so this needs characterization-test-first discipline
rather than relying on an existing net.

## Target
```
toolbox/
    masks/
        morphology.py    # binary morph ops, split_touching_objects
        splitting.py     # assess_and_split_touching (the 228-line watershed/decision path)
        measurement.py   # run_measure_region_props, run_measure_binary_mask
    condensate_physics/
        wetting.py       # neck_geometry, fit_elastocapillary_length  ← MOVED HOME
```
`label_and_mask_tools.py` becomes a thin re-export shim.

## Method — characterization first, because coverage is thin
1. **Write characterization tests before moving anything.** With only 6 test files, assume most functions
   are not pinned numerically. Pin each at `rtol=1e-9` (exact for masks/labels) on a synthetic input.
   **No test, no move.**
2. **The physics move is the valuable one — and needs the most care.** `neck_geometry` and
   `fit_elastocapillary_length` produce numbers that could reach a figure. Pin them, move them, verify
   identical. Check whether `condensate_physics/` already has related fusion code they should sit beside.
3. **`assess_and_split_touching` (228 lines) is a decision path** — it assesses *whether* to split, then
   splits. Those are two responsibilities; splitting the function is optional here but the assess/act
   seam is the natural one if the ratchet needs it.
4. **Mask outputs must be exact.** A one-pixel difference in a split mask changes every downstream
   object measurement. Assert label-array equality, not approximate agreement.
5. Move, don't rewrite; one concern per commit; re-export shim with callers grepped first.

## Why now
- **A physics measurement filed under masking is a discoverability and Methods-writing problem**,
  independent of line count.
- Three functions over the ratchet.
- The masking half becomes a clean, focused module once the physics leaves.

## Tests
- Characterization tests exist and pass for every function **before** it moves; identical after.
- Mask/label outputs are **exactly** equal after moves (not approximately).
- `neck_geometry` and `fit_elastocapillary_length` produce identical numbers from their new home.
- The shim resolves every previously-public name.
- Lower `_MAX_LONG_FUNCTIONS` and the per-file line ratchet.

## Steps
1. Write characterization tests for all functions to be moved; commit them green on current code.
2. Move `neck_geometry` + `fit_elastocapillary_length` → `condensate_physics/wetting.py`; verify numbers.
3. Move `measurement.py` (region props, binary mask measures); run.
4. Move `morphology.py` (binary ops, split_touching_objects); run.
5. Move `splitting.py` (assess_and_split_touching); run.
6. `label_and_mask_tools.py` → re-export shim; lower ratchets.
7. Full `pytest -m core` green after each step.
8. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG noting the physics relocation.

## Definition of done
- Condensate wetting physics lives in `condensate_physics/`, not in a masking module.
- Masking, splitting, and measurement are separate, focused modules; the old file is a shim.
- Every moved function is pinned by a characterization test written first; mask outputs exactly equal.
- Ratchets lowered; all pre-existing tests pass unmodified.

## Cautions
- **Coverage is thin (6 files) — characterization-test-first is mandatory here**, not optional.
- **Mask equality must be exact.** A single differing pixel propagates into every object measurement.
- **The physics functions may feed figures** — pin their numbers with extra care before moving.
- **Move, don't improve.** No re-tuned split criteria; `assess_and_split_touching` decides which objects
  get separated, and a small change silently alters object counts.
- Re-export shim mandatory; masking is imported broadly across the segmentation workflows.
