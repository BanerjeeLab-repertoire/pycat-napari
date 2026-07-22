# Claude Code spec — Decompose `segmentation_tools.py` by domain

> **✅ STATUS — DONE (1.6.240-243). The whole segmentation scientific core is decomposed into
> `toolbox/segmentation/` by family — _common, local_thresholding, watershed, morphology, intensity, fz,
> cellpose, puncta_refinement, subcellular. Every move byte-identical; the filter-sensitivity invariances,
> the fast/slow parity, and the cellpose optional-dep guard all intact (tests pass, patch/source targets
> repointed to the moved modules — never adjusted to pass). `segmentation_tools.py` went 2692 → 148 lines
> (-95%): a PURE re-export shim, no function defs. Dependency-ordered so family modules import each other,
> never segmentation_tools.**

**Date:** 2026-07-20 · **Target tree:** 1.6.203 · Verified against the 1.6.203 tree. Third-largest file
(**2,692 lines**), and the **best-covered decomposition target in the codebase — 41 test files**. That
coverage makes it the safest of the remaining large-file splits. Behaviour-preserving, coverage-gated.

## Verified state
```
36 functions, 6 over 120 lines:
  223  puncta_refinement_filtering_func       (the SNR/kurtosis gate — filter-sensitivity covered)
  189  segment_subcellular_objects
  182  fz_segmentation_and_binarization
  158  puncta_refinement_filtering_func_fast
  146  cell_mask_stretching
  137  run_segment_subcellular_objects
  114  cellpose_segmentation
  110  opencv_watershed_func
```
41 test files reference segmentation — including `test_filter_sensitivity.py` (which pins the
puncta-refinement gate's invariances) and `test_headless_science.py`. The characterization net is
strong and specific.

The file mixes distinct segmentation **families** that should be separate modules: puncta refinement,
subcellular-object segmentation, the FZ (Felzenszwalb) path, watershed, cellpose wrapping, and mask
morphology.

## Target — a `segmentation/` package by family
```
toolbox/segmentation/
    puncta_refinement.py   # puncta_refinement_filtering_func (+_fast) — the filter-sensitivity gate
    subcellular.py         # segment_subcellular_objects, run_segment_subcellular_objects
    fz.py                  # fz_segmentation_and_binarization
    watershed.py           # opencv_watershed_func
    cellpose.py            # cellpose_segmentation (the optional-dep wrapper)
    morphology.py          # cell_mask_stretching + mask helpers
```
`segmentation_tools.py` becomes a thin re-export shim.

## Method — coverage-gated, science untouched
1. **Puncta refinement is the most sensitive move** — it is the gate `test_filter_sensitivity.py`
   guards for offset/scale/selection-bias invariance. Before moving it, confirm those tests exercise
   the function through its new location; they are the characterization net and must pass **unmodified**.
2. **`cellpose_segmentation`** wraps an optional dependency — preserve the optional-import guard exactly;
   moving it must not change when cellpose is required vs absent.
3. Every other function: confirm a test pins its output (41 files — coverage is likely there; verify per
   function, add a characterization test where only structural).
4. **Move, don't rewrite** — no threshold changes, no "cleaner" morphology, no reordered operations.
   The filter-sensitivity programme exists precisely because a small change here shifts which objects
   survive.

### Hard rules
- One family per commit; `pytest -m core` + `test_filter_sensitivity` green between each.
- No test edited to make a move pass.
- Re-export shim for every previously-public name; grep for direct imports first (segmentation is
  imported widely — cell/puncta/invitro/brightfield workflows all call it).
- `materialize_stack` not `np.asarray` on any stack touched.

## Why now
- **Best-covered large file** — safest domain split available.
- Clean family boundaries — the functions already partition by segmentation type.
- Pairs with the VPT + timeseries splits to close the engineering audit's big-file findings.
- The puncta-refinement gate becomes a focused, testable module — easier to reason about the
  filter-sensitivity invariances when it is not buried in a 2,700-line file.

## Tests
- `test_filter_sensitivity` passes unmodified through the new puncta-refinement location.
- Each moved function is pinned by a characterization/existing test; identical output after.
- Cellpose optional-dependency behaviour unchanged (required when used, absent-safe otherwise).
- All 41 referencing test files pass unmodified.
- Re-export shim resolves every previously-public name.
- Lower `_MAX_LONG_FUNCTIONS` and any per-file line ratchet.

## Steps
1. Create `toolbox/segmentation/`; move `puncta_refinement` (+_fast); run filter-sensitivity + core.
2. Move `subcellular` (+run_); run tests.
3. Move `fz`; run tests.
4. Move `watershed`; run tests.
5. Move `cellpose` (preserve optional guard); run tests.
6. Move `morphology` (mask_stretching + helpers); run tests.
7. `segmentation_tools.py` → re-export shim; lower ratchets.
8. Full `pytest -m core` green after each step.
9. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG before/after.

## Definition of done
- `segmentation_tools.py` is a thin shim; families live in `toolbox/segmentation/`.
- The puncta-refinement gate's filter-sensitivity invariances still hold (tests unmodified).
- Cellpose optional-dep behaviour unchanged; all 41 test files pass unmodified.
- Ratchets lowered; no numerical/mask output changes.

## Cautions
- **Puncta refinement is filter-sensitivity-gated** — its invariance tests are the net; if they fail
  after the move, behaviour changed. Never adjust them to pass.
- **Preserve the cellpose optional guard** — moving must not change when the dependency is required.
- **Move, don't improve** — a changed threshold or reordered morphology shifts which objects survive;
  that is a silent regression the whole filter-sensitivity programme exists to catch.
- Re-export shim mandatory — segmentation is imported by many workflows; grep every caller.
- One family per commit.
