# Claude Code spec — Decompose `vpt_tools.py` scientific core by domain

**Date:** 2026-07-20 · **Target tree:** 1.6.203 · Verified against the 1.6.203 tree. The engineering
audit's other named domain-split target. At **2,834 lines** it is the largest file in the project. The
UI adaptation was already extracted into `vpt/` (adapters, panels, docks), but the **scientific core**
stayed monolithic — the audit's exact observation: *"UI adaptation has been separated while the
scientific implementation remains monolithic."* This splits the core, and it is unusually safe to do
because VPT has the strongest test coverage in the codebase.

## Verified state — and why it is the safest big split
- 55 functions, **only 3 over 120 lines** — so unlike `timeseries_condensate_tools`, this is not about
  taming giant functions; it is about separating a 2,834-line pile of *cohesive but co-located* domains.
- **38 test files** reference VPT — the most-covered subsystem in PyCAT — plus the validated **~8.325
  viscosity baseline** and the GPU/CPU/serial equivalence guards. The characterization net already
  exists in force.
- `detect_beads_stack` was already split by pipeline stage (1.6.183) and validated against the baseline
  — proving this file can be decomposed without disturbing the physics.

The functions already cluster by scientific domain; they just live in one file:
```
detection:    detect_beads_stack, detect_beads_frame, build_hot_pixel_mask, dedup_detections_ring_merge
host/ROI:     infer_host_from_beads
linking:      estimate_linking_distance_um, assess_linking_conditions
drift:        drift_correct_com
viscosity:    viscosity_measurement
orchestration:run_vpt_analysis
```

## Target — a `vpt/core/` layer alongside the existing `vpt/` adapters
```
toolbox/vpt/
    (existing adapters: msd_adapter, napari_adapter, panels, results_dock, table_adapter)
    detection.py     # detect_beads_stack/frame, hot-pixel mask, ring-merge dedup
    host.py          # infer_host_from_beads (the 3 host modes)
    linking.py       # estimate_linking_distance_um, assess_linking_conditions
    drift.py         # drift_correct_com
    viscosity.py     # viscosity_measurement (Stokes-Einstein, the physics)
    analysis.py      # run_vpt_analysis (orchestration)
```
`vpt_tools.py` becomes a thin re-export shim. This mirrors the audit's recommended VPT shape
(trajectories/drift/msd/fitting/viscosity/…), collapsed to the domains actually present.

## Method — move, prove against the baseline, never touch numerics
The VPT-specific discipline from the detect_beads split applies to the whole file:
1. **Every scientific function is pinned before it moves.** The 38 test files + the equivalence guards +
   the 8.325 baseline are the net. Before moving `viscosity_measurement`, confirm a test asserts its
   *number* on a known input; add a characterization test if coverage is only structural.
2. **`detect_beads_stack` stays byte-identical** — it is the validated detection path; moving it to
   `detection.py` must not change a single detection or its order (downstream linking is order-sensitive).
3. **`viscosity_measurement` is the canary** — after it moves to `viscosity.py`, the full-chain
   viscosity on the real bead file must still return ~8.325. Note this in the CHANGELOG as the revert
   condition, exactly as the standing VPT rule requires.
4. **One domain per commit**, equivalence guards + `pytest -m core` green between each.

### Hard rules
- **Move, don't rewrite.** No renumbering, no reordering detections, no "cleaner" fit — cut, paste, fix
  imports.
- **Re-export shim** from `vpt_tools` for every previously-public name; grep for direct imports first
  (the adapters in `vpt/` import from `vpt_tools` — check each).
- **The equivalence guards are the safety net** — if `test_vpt_gpu_equivalence` or the baseline check
  fails after a move, the move changed behaviour; revert, don't adjust.

## Why now
- Largest file in the project, and the audit's named target.
- Best-covered subsystem — the split is *safe*, which is rare for a 2,800-line science file.
- The pattern is proven (detect_beads already split against the baseline).
- Pairs with the timeseries split — the two biggest files, both domain-split, closes the audit's #11.

## Tests
- Characterization/equivalence pin every moved scientific function; identical after.
- The VPT GPU/CPU/serial equivalence guards pass unmodified.
- Full-chain viscosity on the reference input returns the baseline (~8.325) after the viscosity move.
- Detection output (coordinates, sigma, count, ORDER) unchanged after the detection move.
- The `vpt/` adapters still import and run (re-export shim resolves every name).
- Lower `_MAX_LONG_FUNCTIONS` and any per-file line ratchet.

## Steps
1. Create `vpt/detection.py`; move detection + hot-pixel + ring-merge; run equivalence guards.
2. `vpt/host.py` — infer_host_from_beads; run tests.
3. `vpt/linking.py` — linking distance + conditions; run tests.
4. `vpt/drift.py` — drift_correct_com; run tests.
5. `vpt/viscosity.py` — viscosity_measurement; **run the baseline check** (~8.325).
6. `vpt/analysis.py` — run_vpt_analysis orchestration; run full chain.
7. `vpt_tools.py` → re-export shim; lower ratchets.
8. Full `pytest -m core` + VPT equivalence guards green after each step.
9. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG with the baseline-revert note.

## Definition of done
- `vpt_tools.py` is a thin shim; the scientific core lives in `vpt/{detection,host,linking,drift,viscosity,analysis}.py`.
- Every moved function is proven behaviour-preserving by the existing guards + characterization tests.
- Full-chain viscosity still returns ~8.325; detection output and order unchanged.
- The `vpt/` adapters keep working via the shim.
- Ratchets lowered; all pre-existing tests pass unmodified.

## Cautions
- **The ~8.325 baseline is the canary** — if it moves, stop and revert. A decomposition must not change
  the physics. Flag the revert condition in the CHANGELOG.
- **Preserve detection order** — downstream linking depends on it; a reordered detection list is a
  silent regression the guards may not all catch.
- **Equivalence guards are law** — a failing guard after a "structural" move means behaviour changed.
- **Move, don't improve** — no fit tidying, no vectorising, no reordering while relocating.
- Re-export shim mandatory; the `vpt/` adapters import from `vpt_tools` — verify each resolves.
