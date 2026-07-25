# Claude Code spec — Split `label_and_mask_tools.py`: physics does not belong in a masking module

> **◐ STATUS — the physics move DONE, shipped 1.6.336 (verified consistent with the 1.6.335 tree: the module
> was still 1,592 lines with all 7 named functions before this). Masking-half split (morphology / splitting /
> measurement) remains.**
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
