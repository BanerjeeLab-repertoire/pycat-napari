# Claude Code spec — Decompose `detect_beads_stack` (VPT detection), guard-anchored

> **✅ STATUS — DONE, shipped in 1.6.183.** `detect_beads_stack` split by pipeline stage (317 → 116 lines)
> into `_choose_detection_backend` (GPU/pool/serial tier), `_pool_predetect`, `_bead_hot_mask`,
> `_detect_all_frames` (+ `_fast_frame_rows` / `_precise_frame_rows`), and `_assemble_detections`.
> Guard-anchored: the existing VPT equivalence guards (`test_vpt_gpu_equivalence`,
> `test_vpt_parallel_equivalence`, the memo) pass unmodified, and a new serial-path characterization
> (`test_detect_beads_stack_characterization`) pins the exact detection table (coordinates, order-sensitive
> hash, area, counts) on a seeded synthetic stack — so the serial path is guarded even without a GPU.
> Detection numerics, path outcomes and output order untouched. Complexity ratchet 127 → 126.

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. The next science
function on the split list, singled out because it is both long (317 lines) **and** the most
scientifically load-bearing function in VPT — so it gets extra care and a specific existing anchor.

## Why this one needs its own spec
`detect_beads_stack` (`toolbox/vpt_tools.py`, 317 lines) is the shared detection stage feeding the
whole VPT viscosity chain. The memory of this project is explicit that VPT detection has a **validated
baseline** (viscosity ~8.325 through TrackMate on the real bead file at a pinned version), and that
detection changes must be revertible and validated against it.

Unlike the fit functions already split (byte-identical, pure numerics), this one has branches (GPU /
CPU-parallel / serial path selection) and is the exact code a prior audit flagged as regression-prone.
So the discipline is: **split it, but prove equivalence against the anchors that already exist.**

## The anchors (verified present)
Two tests already pin this function's behaviour:
- `tests/test_vpt_gpu_equivalence.py` — asserts GPU and CPU paths produce identical blobs to serial
  `skimage.blob_log` (rounded to absorb float noise).
- `tests/test_vpt_equivalence_guard_memo.py` — memoizes the guard so it runs once, not per call.

These are the characterization tests the science-split spec asks for — and they already exist. So this
split is **guard-anchored from the start**: any change that alters detection output fails them.

## Method — split by pipeline stage, preserve every path
`detect_beads_stack` is a pipeline: resolve parameters → select execution path (GPU/CPU-parallel/
serial) → detect per frame → assemble/label results. Those are natural, nameable seams.

1. Extract the **path-selection** logic into `_select_detection_backend(...)` returning the chosen
   callable — this isolates the branchy part that is hardest to read.
2. Extract **per-frame detection** into a helper already shaped for the worker pattern (the memory
   notes `_detect_frame_worker`/ProcessPool exists for the CPU-parallel path — align to it).
3. Extract **result assembly/labelling** into a pure helper.
4. The outer `detect_beads_stack` becomes: resolve → select → map over frames → assemble.

**Do not change:**
- the numerical detection (blob_log parameters, thresholds, sigma handling) — a single changed default
  moves the validated baseline;
- the path-selection *outcome* — GPU/CPU-parallel/serial must still produce identical blobs (the guard
  tests enforce this);
- the order of detections in the output (downstream linking is order-sensitive).

## Extra guard for this function specifically
Beyond the existing equivalence tests, add a **characterization test on a small seeded synthetic
stack**: run `detect_beads_stack` before the split, record the exact detection table (coordinates,
sigma, counts) as a reference at `rtol=1e-9` / exact for integer counts, then assert the split
reproduces it. This protects the serial path even on machines without a GPU where the GPU-equivalence
test skips.

Note in the CHANGELOG: *"detection decomposed, byte-identical; validated against VPT equivalence
guards; revert is a clean single-function rollback if the ~8.325 baseline regresses."* — matching the
project's standing rule for VPT detection changes.

## Steps
1. Add the small-synthetic-stack characterization test (serial path); confirm it passes on today's
   code.
2. Extract `_select_detection_backend`; run VPT equivalence + characterization tests.
3. Extract per-frame detection helper (aligned to the worker pattern); run tests.
4. Extract result assembly; run tests.
5. Confirm `detect_beads_stack` is under 120 lines; **lower `_MAX_LONG_FUNCTIONS`**.
6. Full `pytest -m core` green; VPT equivalence guards green.
7. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG with the VPT-baseline note.

## Definition of done
- `detect_beads_stack` split by pipeline stage into pure/worker helpers, under 120 lines.
- GPU/CPU-parallel/serial still produce identical blobs (existing guards pass unmodified).
- A serial-path characterization test proves the detection table is unchanged.
- Detection numerics, path outcomes, and output ordering are untouched.
- Ratchet lowered; full `pytest -m core` green.

## Cautions
- **This is the VPT-validated detection path.** Any numeric change — even a reordered detection list —
  risks the ~8.325 baseline. Split structure only; change no values.
- The existing equivalence guards are the safety net; if they fail, a "structural" move changed
  behaviour — revert, don't adjust the test.
- Add the serial-path characterization test because the GPU-equivalence test skips without a GPU, and
  the sandbox has none.
- Preserve detection output order — downstream linking depends on it.
- One extraction per commit; keep each revertible.
