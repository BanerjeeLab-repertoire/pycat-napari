# Claude Code handoff — VPT GPU/CPU-parallel detection validation

## Setup (do this first)

Claude Code works in your repo with your shell, so it inherits the env you launch it from.

1. Open a terminal, activate the env, cd to the repo, launch Claude Code there:
   ```
   conda activate pycat-160
   cd C:\Users\Gable\Documents\GitHub\pycat-napari
   claude
   ```
   (Launching from the activated env is what gives it `pycat-160` — cupy, napari, pytest, your CUDA.)

2. First thing, have it fix the build-isolation pyexpat breakage so releases work again:
   ```
   conda install -n pycat-160 -c conda-forge --force-reinstall libexpat expat
   ```
   Then confirm a clean build: `python -m build` (plain, with isolation) should now succeed.
   Until then, the workaround is `pip install hatchling & python -m build --no-isolation`.

3. Point it at the repo's own context so it doesn't rediscover everything:
   - `docs/audits/` — the running architecture/decision log (session architecture, file-I/O
     decomposition roadmap, CZI, tagging status, codebase audit).
   - `CHANGELOG.md` — every change 1.6.42→1.6.56 with rationale.

## Durable project facts it must know

- Repo `C:\Users\Gable\Documents\GitHub\pycat-napari`, env `pycat-160` (Python 3.12, CUDA cu118,
  Quadro P2200 primary / P1000). cupy is `cupy-cuda11x` (already a dep in pyproject.toml).
- **VPT bead file pixel size = 0.067 µm/px (100× objective), NOT 0.67.** Test data on disk:
  `Substack__1-40_.tif` (40 frames, full-frame 1080×1440 dense, ~330–391 raw detections/frame — use
  for DETECTION work) and `3_30_hr_1_MMStack_Pos0_ome2.tif` (1000 frames, 171×201 sparse crop — use
  for linking/MSD/viscosity).
- **VPT validated baseline = git tag v1.5.329**: an experienced user validated viscosity ~8.325 through
  PyCAT's TrackMate linking on the real 1.5 GB bead file, "acceptable vs full FIJI plugin." Any VPT
  detection change must keep the 1.5.329 path selectable and be validated against 8.325 before trusting.
- Delivery discipline (keep it): every CODE change → own version bump + PyPI push + commit with
  EXPLICIT filenames (not `git add -A`) + CHANGELOG entry. Docs-only changes ride the next code commit.

## The task: validate VPT GPU + CPU-parallel detection on real CUDA

**Reframe from old notes:** this is NOT a build task — the code is already built. It's a
VALIDATION + MEASUREMENT task that needs real CUDA, which is exactly why it was blocked in the
sandbox (no cupy/GPU there).

### What already exists (verified in `src/pycat/toolbox/vpt_tools.py`)
- `blob_log_gpu()` (line ~458) — GPU LoG blob detection replicating `skimage.feature.blob_log`;
  falls back to CPU skimage if cupy/GPU unavailable. Previously verified 100% match on ONE real frame,
  but never run on actual CUDA.
- `detect_beads_frame()` (line ~341) — uses `blob_log_gpu` when `use_gpu=True`.
- `_bead_source_descriptor()` / `_detect_frame_worker()` + `ProcessPoolExecutor` (lines ~1220/1314/1741)
  — the CPU-parallel path (min(8, cpu-1) workers).
- **Tier selector** `detect_beads_stack()` (line ~1673): GPU > CPU-parallel > serial. GPU runs
  in-process (no pool — pool workers would contend for the one GPU).
- **Equivalence guard already in the selector** (~line 1690): before trusting GPU for the whole stack
  it runs `detect_beads_frame` CPU vs GPU on the first frame and compares sorted rounded (y,x) sets;
  on ANY disagreement it falls back to CPU so results are never silently wrong.
- GPU primitives in `src/pycat/toolbox/gpu_utils.py`: `gpu_available()`, `to_gpu()`, `to_cpu()`,
  `gpu_laplace_of_gaussian()`, etc.

### What Claude Code needs to actually do
1. **Confirm cupy sees the GPU:** `python -c "from pycat.toolbox.gpu_utils import gpu_available; print(gpu_available())"` → must be True on the P2200. If False, diagnose cupy-cuda11x / driver before anything else.
2. **Run the built-in equivalence guard for real:** call `detect_beads_stack` on `Substack__1-40_.tif`
   with `use_gpu='auto'`, `quality_mode='fast'`. The guard runs CPU-vs-GPU on frame 0 automatically —
   confirm it does NOT fall back (i.e. GPU and CPU blobs match on real CUDA, not just the one frame
   previously tested). If it falls back, capture WHY (the disagreement is the finding).
3. **Full-stack equivalence, not just frame 0:** extend the check — run all 40 frames through GPU and
   through serial CPU, assert identical blob sets per frame (sorted rounded coords). This is the test
   the sandbox couldn't run. Add it as `tests/test_vpt_gpu_equivalence.py` (skip-if-no-GPU marker).
4. **CPU-parallel equivalence:** assert the ProcessPool path (`parallel='process'`, GPU forced off)
   produces identical blobs to the serial path on the same 40 frames.
5. **Measure the speedup:** time serial vs CPU-parallel vs GPU on the 40-frame dense clip; report
   frames/sec for each. This is the number that justifies the whole feature — currently unmeasured on
   real hardware.
6. **Regression-guard against the baseline:** run the full detection→link→MSD→viscosity chain on
   `3_30_hr_1_MMStack_Pos0_ome2.tif` with GPU on, confirm it still lands near the validated ~8.325
   (through TrackMate) / the headless linker numbers. GPU must not change detection results.

### Definition of done
- `gpu_available()` True on the Quadro; GPU path taken (no fallback) on real CUDA.
- New skip-if-no-GPU tests asserting GPU ≡ CPU-parallel ≡ serial blobs across all 40 frames.
- A measured speedup table (serial / CPU-parallel / GPU frames-per-sec).
- Viscosity chain unchanged vs baseline with GPU on.
- Shipped as its own version bump + PyPI push + commit + CHANGELOG entry, per the discipline above.

### Cautions
- The GPU tier only engages for `quality_mode='fast'`. `ring_merge` and `hot_pixel_reject` variants run
  serially by design (they need per-detection sigma the parallel worker doesn't carry) — don't "fix"
  that; it's intentional.
- Keep the 1.5.329-validated path intact; GPU is additive and must be revertible by turning `use_gpu`
  off.
- Pixel size is 0.067 µm/px — a wrong scale corrupts viscosity; make sure any chain run uses it.
