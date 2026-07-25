# Claude Code spec — Sub-split `vpt/detection.py` (the VPT decomposition left one giant)

> **◐ IN PROGRESS — steps 1+2 DONE, 1.6.360 (2026-07-25).** `vpt/linking.py` (estimate_linking_distance_um,
> assess_linking_conditions — the two misfiled linking fns) + `vpt/artifacts.py` (build_hot_pixel_mask,
> dedup_detections_ring_merge) created; detection.py 1,773 → 1,340 lines, re-exports all four. Equivalence
> guards + detect-beads order + ~8.325 viscosity chain all green. **Remaining: step 3 — blob_log_gpu + GPU
> dispatch → vpt/gpu.py (run the equivalence guards specifically); then the shim/ratchet.** Verified premise
> (1,773 lines, both linking fns present, no vpt/linking.py) against the 1.6.359 tree.

**Date:** 2026-07-23 · **Target tree:** 1.6.324 · Verified against the 1.6.324 tree. The VPT
decomposition succeeded — `vpt_tools.py` is a 95-line shim — but it produced **one oversized module**:
`toolbox/vpt/detection.py` at **1,773 lines / 40 functions**, larger than several files that were
themselves decomposition targets.

This is a normal outcome of a domain split (detection genuinely *is* the biggest VPT domain), not a
mistake. But 1,773 lines in one module is worth a second pass, and it is unusually safe here: **51 test
files** reference the VPT surface, plus the GPU/CPU equivalence guards and the validated ~8.325 viscosity
baseline.

## Verified structure
```
40 functions, 1 over the 120 ratchet
   151  estimate_linking_distance_um     ← LINKING, not detection
   116  detect_beads_stack
   111  detect_beads_frame
   110  dedup_detections_ring_merge
    91  build_hot_pixel_mask
    84  assess_linking_conditions        ← LINKING, not detection
    68  classify_beads
    65  blob_log_gpu
```

**Two functions are misfiled.** `estimate_linking_distance_um` and `assess_linking_conditions` are
**linking** concerns that ended up in the detection module — and a `vpt/linking.py` is their
natural home (create it if absent — check the current `vpt/` module list first). Moving them is both a size reduction and a correctness-of-organisation fix.

## Target
```
toolbox/vpt/
    detection.py     # detect_beads_frame/stack, classify_beads (the core detection path)
    artifacts.py     # build_hot_pixel_mask, dedup_detections_ring_merge (rejection/merge)
    gpu.py           # blob_log_gpu and the GPU/CPU dispatch
    linking.py       # estimate_linking_distance_um, assess_linking_conditions
                     #   (create this module if it does not already exist)
```

## Method — the equivalence guards are the net
1. **`detect_beads_stack` must stay byte-identical.** It is the validated detection path; moving anything
   around it must not change a single detection **or its order** — downstream linking is order-sensitive,
   and a reordered detection list is a silent regression the guards may not all catch.
2. **The GPU/CPU/serial equivalence tests must pass unmodified** after `blob_log_gpu` moves. They are the
   strongest net in the codebase for this module.
3. **The ~8.325 viscosity baseline is the end-to-end canary.** Run the full chain after the moves; if it
   shifts, revert. Flag this as the revert condition in the CHANGELOG, per the standing VPT rule.
4. **Move the two linking functions first** — they are the clearest win (correct home + size), and they
   exercise the shim/import path before anything sensitive moves.
5. Re-export from `detection.py` for every previously-public name; the `vpt/` adapters and `vpt_tools`
   shim both import from here — grep every caller.

## Why now
- Largest remaining module in an otherwise-decomposed subsystem.
- **51 test files** plus equivalence guards plus a numeric end-to-end baseline — the safest split
  available anywhere in the codebase.
- Two functions are demonstrably in the wrong module, which is a correctness-of-structure issue
  independent of size.

## Tests
- The VPT GPU/CPU/serial equivalence guards pass **unmodified**.
- `detect_beads_stack` output — coordinates, sigma, count, **and order** — unchanged.
- Full-chain viscosity returns the baseline (~8.325) after all moves.
- `estimate_linking_distance_um` and `assess_linking_conditions` behave identically from `linking.py`.
- All 51 referencing test files pass unmodified.
- `detection.py` re-export shim resolves every previously-public name.
- Lower the per-file line ratchet.

## Steps
1. Move `estimate_linking_distance_um` + `assess_linking_conditions` → `linking.py`; run guards.
2. Move `build_hot_pixel_mask` + `dedup_detections_ring_merge` → `artifacts.py`; run guards.
3. Move `blob_log_gpu` + dispatch → `gpu.py`; run the **equivalence guards specifically**.
4. Re-export shim in `detection.py`; lower ratchets.
5. Run the full-chain viscosity check; confirm the baseline.
6. Full `pytest -m core` green after each step.
7. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG with the baseline-revert note.

## Definition of done
- `vpt/detection.py` holds the core detection path only; artifacts, GPU dispatch, and the two linking
  functions live in their proper modules.
- Equivalence guards and the viscosity baseline are unchanged.
- Detection output and order are identical.
- Ratchets lowered; all pre-existing tests pass unmodified.

## Cautions
- **Detection order is behaviour** — downstream linking depends on it. Assert order, not just contents.
- **The ~8.325 baseline is the canary.** A decomposition must not change the physics.
- **Equivalence guards are law** — a failing guard after a structural move means behaviour changed.
- **Move, don't improve.** No re-tuned detection parameters, no vectorising while relocating.
- Re-export shim mandatory; the adapters and the `vpt_tools` shim both import from `detection`.
