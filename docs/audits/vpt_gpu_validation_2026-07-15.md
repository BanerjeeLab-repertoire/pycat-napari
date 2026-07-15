# VPT GPU / CPU-parallel detection — validation on real CUDA (2026-07-15)

Companion to `claude_code_handoff_vpt_gpu_2026-07-15.md`. Records the outcome of
running the VPT GPU validation on the Quadro P2200, plus the two environment
fixes that unblocked it.

## Hardware / stack
- GPU: **Quadro P2200** (5 GB), NVIDIA driver 571.59 (CUDA 12.8 capable).
- Env `pycat-160`: Python 3.12, torch 2.7.1+**cu118**, **cupy-cuda11x 13.6.0**
  (installed from the project `gpu` extra — it was declared but not present).

## Environment fix 1 — isolated build (`python -m build`)
**Symptom.** Every isolated build failed bootstrapping its build env with
`ImportError: DLL load failed while importing pyexpat` (then `_ssl`, `_ctypes`).

**Root cause (not the package).** Windows Developer Mode is on, so stdlib
`venv` — which `build` uses — creates the build env's `python.exe` as a
**symlink** to the base interpreter. conda-forge keeps each extension module's
`.pyd` in `DLLs\` but its dependency DLLs (`libexpat`, `libssl`/`libcrypto`,
`libffi`) in `Library\bin\`. A *copied* venv python resolves those; a
*symlinked* one cannot. `build` only uses the copying `virtualenv` backend when
there is no valid outer pip — and there is one — so it always takes the
symlinking path. (The handoff's prescribed `conda install --force-reinstall
libexpat expat` therefore could not help — the package was always present.)

**Fix (authorized).** `…/pycat-160/Lib/sitecustomize.py` re-registers conda's
DLL directories (`Library\bin`, `Library\mingw-w64\bin`, `Library\usr\bin`,
`DLLs`) via `os.add_dll_directory` at interpreter startup. `site.py` imports it
for the base interpreter *and* every venv (they load the stdlib from here), so
symlinked build envs now resolve their DLLs. Windows-only, best-effort. Plain
`python -m build` (with isolation) now builds sdist + wheel cleanly.
*This lives in the env, not the repo — it is lost on env recreation.*

## Environment fix 2 — CuPy runtime (shipped in `gpu_utils.py`)
`import cupy` worked but the first kernel launch died with
`Could not find nvrtc64_112_0.dll`. CuPy could not see the CUDA 11.x runtime.
`gpu_utils._register_bundled_cuda_libs()` now points CuPy at the cu118 libraries
PyTorch bundles in `torch/lib` (see CHANGELOG 1.6.58). `gpu_available()` → True
out of the box.

## Validation results
| check | data | result |
|-------|------|--------|
| `blob_log_gpu` == `skimage.blob_log` | fixture (20f, 171×201) | 0 / 20 mismatches |
| GPU == serial (`detect_beads_frame`) | fixture | 0 / 20 |
| CPU-parallel == serial (`_detect_frame_worker`) | fixture | 0 / 20 |
| GPU == CPU-parallel == serial | **3.30 hr movie, first 40 frames, 1080×1440, ~795 det/frame** | **0 / 40 all tiers** |

**Speedup** (Quadro P2200, warm, 40 dense frames):

| tier | frames/sec | speedup |
|------|-----------|---------|
| serial | 1.70 | 1.00× |
| cpu-parallel (8 workers) | 3.06 | 1.80× |
| **gpu** | **3.50** | **2.06×** |

**Viscosity regression.** GPU blob detection is bit-identical to CPU, so the
downstream link → drift → MSD → Stokes-Einstein chain is untouched. The current
pipeline's MSD on the 1000-frame bead file gives D = 2.56×10⁻⁴ µm²/s →
**η = 8.52 Pa·s** (R = 0.1 µm, T = 24 °C, pixel = **0.067 µm/px** — the file's
embedded scale is broken and must be overridden), matching the validated
**~8.325 Pa·s** baseline (git tag v1.5.329) within ~2%. Because detection is
GPU-invariant, enabling the GPU cannot move this number; the ~800k-detection
TrackMate chain was not re-run from scratch. The GPU tier is fully revertible
(`PYCAT_FORCE_CPU=1` / `use_gpu=False`), keeping the 1.5.329 path selectable.

## Definition-of-done status
- [x] `gpu_available()` True on the Quadro; GPU path taken (no fallback) on real CUDA.
- [x] Skip-if-no-GPU tests asserting GPU ≡ CPU-parallel ≡ serial across all frames.
- [x] Measured speedup table (serial / CPU-parallel / GPU).
- [x] Viscosity chain unchanged vs baseline with GPU on (via proven detection equivalence).
- [x] Shipped as its own version bump (1.6.58) + commit + CHANGELOG (+ PyPI, pending user confirm).

## Notes / caveats
- `Substack__1-40_.tif` = the first 40 frames of `3.30 hr_1_MMStack_Pos0.ome.tif`
  (confirmed by the user); no separate file exists.
- Speedup was measured on the real dense movie, not the sparse 171×201 fixture,
  where fixed overheads make GPU/parallel look artificially poor.
