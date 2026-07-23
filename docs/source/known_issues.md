# Known issue — Python 3.13 and the cellpose 3 numpy pin

**Status:** blocked upstream · **Last verified:** 2026-07-23 · **Tree:** 1.6.297

PyCAT declares `requires-python = ">=3.12,<3.13"`, so `pip` refuses Python 3.13 cleanly with
*"Package 'pycat-napari' requires a different Python"*. That ceiling is deliberate: while `cellpose<4` is a
base dependency, a normal `pip install pycat-napari` on **Python 3.13** would otherwise fail three layers
down with a confusing numpy/meson source-build error. A clean refusal respects the user's time; the ceiling
was briefly `<3.14` (permitting an install that cannot succeed) and was reverted to match reality.
This document records why, what was measured, and the exact condition that unblocks it — so nobody has
to re-derive it.

---

## Summary

| | |
|---|---|
| **Symptom** | `pip install` on Python 3.13 tries to build numpy 2.0.2 from source and fails (no C compiler on a typical Windows machine). |
| **Root cause** | `cellpose 3.1.1.x` declares `numpy<2.1`. NumPy has **no cp313 wheels below 2.1**, so the resolver walks down to 2.0.2 and falls back to a source build. |
| **Is the pin real?** | **No — measured.** Cellpose 3 produces **byte-identical segmentation** on numpy 2.3.5 as on numpy 2.0.2. |
| **Why not cellpose 4?** | v4 removed the `cyto2` CNN models and replaces them with a ViT-L transformer that is very slow on CPU — unusable on the lab's GPU-less machines. This is a deliberate scientific choice, not inertia. |
| **Unblock condition** | Upstream relaxes the numpy upper bound on the cellpose 3.x line (see MouseLand/cellpose#1095), **or** PyCAT gains a default segmentation path that does not require cellpose 3. |

---

## The dependency conflict, precisely

```
cellpose 3.1.1.x  →  numpy < 2.1        (declared in its metadata)
Python 3.13       →  numpy >= 2.1       (wheel availability; nothing below 2.1 ships cp313)
```

These cannot both be satisfied. pip resolves numpy down to **2.0.2**, which has no cp313 wheel, so it
attempts a source build:

```
Collecting numpy>=1.22
  Downloading numpy-2.0.2.tar.gz (18.9 MB)
  Preparing metadata (pyproject.toml) ... error
  ..\meson.build:1:0: ERROR: Unknown compiler(s): [['icl'], ['cl'], ['cc'], ['gcc'], ...]
error: metadata-generation-failed
```

On a machine with MSVC installed the build might succeed, but that is not a reasonable expectation for
users and it would still be compiling numpy from source to reach a version cellpose 3 rejects anyway.

**numba is NOT the blocker.** Earlier notes in this repo attributed the ceiling to "cellpose + numba".
That is wrong as of 2026-07: **numba 0.66.0 installs and runs against numpy 2.3.5 on Python 3.13**
without complaint. Cellpose is the sole holdout.

---

## What was measured (2026-07-23)

PyCAT's own code is 3.13-ready: the tree compiles under 3.13, and the headless `core` suite passes on
3.12 with no numpy-2 removals anywhere (`test_numpy2_ready.py` guards this).

To determine whether cellpose 3's pin is *functional* or merely *declared*, cellpose 3 was installed on
Python 3.13 with the pin bypassed and run end-to-end against the same real image in two environments.

**Method:** `pip install "numpy==2.3.*"` → `pip install cellpose==3.1.1.1 --no-deps` → install the
runtime deps by hand → run `cyto2` on one 512×512 image, `gpu=False` in both environments so numpy is
the only relevant variable.

**Result — byte-identical:**

| | Env A | Env B |
|---|---|---|
| Python | 3.12.13 | 3.13.14 |
| numpy | 2.0.2 | **2.3.5** |
| cellpose | 3.1.1.3 | 3.1.1.1 |
| torch | 2.7.1+cu118 | 2.13.0+cpu |
| **n_masks** | 16 | 16 |
| **diam_used** | 62.137705820089174 | 62.137705820089174 |
| **total_area** | 50037 | 50037 |
| **mask_sha256** | `16e9c91b4abac858…d6f5600b` | `16e9c91b4abac858…d6f5600b` |

Identical SHA-256 of the mask array means **every pixel label matches**. The estimated diameter agrees
to 15 significant figures, i.e. the diameter-estimation path is bit-identical across the two numpy
versions. The comparison also crossed two other variables (torch cu118 vs cpu; cellpose 3.1.1.3 vs
3.1.1.1) and still matched.

Every dependency resolved to a real cp313 wheel — no source builds: numba 0.66.0, llvmlite 0.48.0,
fastremap 1.20.0, imagecodecs 2026.6.26, scipy 1.18.0, opencv-python-headless 5.0.0.93, torch 2.13.0.

**Scope caveat, stated honestly:** one image, one model (`cyto2`), CPU only. This shows the pin is not
blocking correct operation on that path. It is **not** a full compatibility audit across every model
and code path, and it is not a licence to ship on numpy ≥ 2.1 with cellpose 3.

### A false lead worth recording
An initial test used a synthetic field of Gaussian blobs and returned **0 masks** — which looked like a
numpy incompatibility. Running the *same* synthetic test on the working 3.12 environment also returned
0 masks, proving the test image was the problem (cyto2 does not segment featureless blobs), not numpy.
**Any future check of this kind must use a real image and must be run in both environments**, or a
null result will be misread as a failure.

---

## Upstream status

**MouseLand/cellpose#1095 — "Numpy 2.1 support"** (opened Jan 2025) requests exactly this. The
maintainer's replies there:

> this should be resolved now — the latest cellpose version pins numpy < 2.2

> we support up to version 2.1 with numpy. **the main limitation is numba — they now support 2.1 so we
> could increase the dependency upper bound.**

So the maintainer has already identified numba as the limiting factor and noted it is resolved — but
that was not carried back to the **3.x** line, which is the line PyCAT depends on. Another user in the
same thread is stuck for an unrelated reason (3.0.11 is the last version supporting the MPS backend),
which suggests a general pattern: users pinned to an older cellpose for functional reasons are all
blocked by the same stale bound.

---

## What would unblock this

Any one of:

1. **Upstream relaxes the numpy bound on cellpose 3.x.** The cleanest fix. Evidence has been reported
   to #1095.
2. **Cellpose 4 restores lightweight CNN models** (or SAM becomes cheap enough for CPU-only machines),
   removing the reason for `cellpose<4`.
3. **PyCAT gains a non-cellpose default segmentation path** good enough to be the recommended route.
   StarDist and Random Forest are already offered in the segmentation UI; whether either can carry the
   default is a separate scientific question.

### Re-enable procedure (when upstream unblocks)

When one of the unblock conditions lands, re-enabling 3.13 is a **deliberate, verified** change — not just
flipping the number back:

1. **Bump the ceiling** to `requires-python = ">=3.12,<3.14"` in `pyproject.toml`.
2. **Add the classifier** `Programming Language :: Python :: 3.13` (the ceiling/classifier agreement guard,
   `tests/test_python_version_ceiling.py`, requires the pair to move together — it will fail until both are
   updated, which is the intended signal).
3. **Re-run the byte-identical segmentation verification on real data** — the current evidence is one image,
   one model, on CPU; that is not enough to re-certify. This step is not optional.
4. **Add a 3.13 CI lane** so the support is tested, not just declared.

---

## What NOT to do

- **Do not ship a `--no-deps` workaround.** The environment used for the test above is not reproducible
  by a user running `pip install pycat-napari`. It was a probe, not a configuration.
- **Do not relax `cellpose<4`** to escape the numpy pin. That trades a packaging problem for a
  scientific one: it changes the segmentation model, invalidates existing baselines, and makes the
  GPU-less lab machines unusable.
- **Do not claim numba blocks 3.13.** It does not, and repeating it sends the next person down a dead
  end.
- **Do not trust a synthetic-image test** to decide whether cellpose works. See the false lead above.
