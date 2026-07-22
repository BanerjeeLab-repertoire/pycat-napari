# Claude Code spec — Decompose `image_processing_tools.py` by algorithm (coverage-gated, extra care)

> **◐ STATUS — IN PROGRESS (characterization-FIRST). Steps 1-3 DONE (1.6.248-250): size_estimation, the shared
> foundation `_base.py`, and `deblur.py` (DPR) — all byte-identical, each pinned by a characterization test
> written first. image_processing_tools.py 2669 → 2141. Remaining (dependency-ordered): filters/enhancement
> (gabor/dog/laplace/gaussian/bilateral/peak_and_edge), then background (rb_gaussian/WBNS/soft_foreground),
> preprocessing (pre_process_image), and upscaling (run_upscaling_func).**

**Date:** 2026-07-20 · **Target tree:** 1.6.203 · Verified against the 1.6.203 tree. Fourth-largest file
(**2,669 lines**). Unlike segmentation/condensate-physics, its coverage is **thinner — only 6 test
files** — so this split needs the most characterization-test work up front and should be sequenced
after the well-covered ones. Behaviour-preserving; the discipline here is "no test, no move," strictly.

## Verified state
```
52 functions, 3 over 120 lines:
  186  run_upscaling_func
  144  _realness_weight
  143  pre_process_image
  110  deblur_by_pixel_reassignment
  102  wbns_func                          (wavelet background suppression)
   90  soft_foreground_suppression
   90  estimate_object_size_px
   83  run_enhanced_rb_gaussian_bg_removal
```
Only **6 test files** reference it — the weakest characterization net among the big files. So the risk
of an unnoticed numerical change is higher here, and the mitigation is to **write characterization tests
before moving**, not to assume coverage exists.

The functions are distinct **algorithms** that partition cleanly: upscaling, preprocessing, deblurring/
deconvolution, background removal (rolling-ball/Gaussian/WBNS), foreground suppression, and size
estimation.

## Target — an `image_processing/` package by algorithm
```
toolbox/image_processing/
    upscaling.py          # run_upscaling_func, _realness_weight
    preprocessing.py      # pre_process_image
    deblur.py             # deblur_by_pixel_reassignment
    background.py         # wbns_func, run_enhanced_rb_gaussian_bg_removal, soft_foreground_suppression
    size_estimation.py    # estimate_object_size_px
```
`image_processing_tools.py` becomes a thin re-export shim.

## Method — characterization FIRST, because coverage is thin
This is the key difference from the other decomposition specs:
1. **Before moving ANY function, write a characterization test** that pins its output on a synthetic
   image at `rtol=1e-9` (exact for integer/mask outputs). With only 6 existing test files, assume most
   functions are NOT pinned and verify each — do not trust that coverage exists.
2. `estimate_object_size_px` is used by the auto-object-size workflows (top-hat+Otsu) — it feeds
   downstream segmentation, so a change propagates. Pin it especially carefully.
3. `run_upscaling_func` + `_realness_weight` — the upscaling path; pin the upscaled output exactly.
4. Background-removal functions (WBNS, rolling-ball/Gaussian) directly shape every downstream intensity
   measurement — pin them on a known background field.
5. **Move, don't rewrite** — no reordered filter operations, no swapped kernel, no "equivalent" library
   call. A different implementation of the "same" algorithm changes the numbers.

### Hard rules
- **No characterization test, no move** — stricter here than elsewhere because the safety net is thin.
- One algorithm per commit; the new characterization tests + `pytest -m core` green between each.
- Re-export shim for every previously-public name; grep callers first (preprocessing/background are
  called by nearly every workflow).
- `materialize_stack` not `np.asarray` on any stack touched.

## Why do it, but sequence it last among the decompositions
- It is a large file and a clean algorithmic partition — worth doing.
- But its thin coverage makes it the **highest-risk** of the four remaining big-file splits, so do it
  **after** VPT/timeseries/segmentation/condensate-physics, when the pattern is well-practised and the
  characterization-test-first habit is established.
- The characterization tests written here are valuable independent of the split — they pin preprocessing
  and background-removal behaviour that is currently under-tested.

## Tests
- A characterization test exists and passes for every function before it moves; identical after.
- `estimate_object_size_px` recovers a known object size on a synthetic image (pinned) — before and
  after.
- Background removal on a known background field is byte-identical after moving.
- Upscaling output byte-identical after moving.
- All 6 existing test files pass unmodified.
- Re-export shim resolves every previously-public name.
- Lower `_MAX_LONG_FUNCTIONS` / per-file ratchet.

## Steps
1. Write characterization tests for the functions to be moved (this is the bulk of the work — do it
   first, commit the tests, confirm green on today's code).
2. Create `toolbox/image_processing/`; move `background.py` (highest downstream impact — pin first); run.
3. Move `preprocessing.py`; run.
4. Move `upscaling.py` (+_realness_weight); run.
5. Move `deblur.py`; run.
6. Move `size_estimation.py`; run.
7. `image_processing_tools.py` → re-export shim; lower ratchets.
8. Full `pytest -m core` + new characterization tests green after each step.
9. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG before/after.

## Definition of done
- `image_processing_tools.py` is a thin shim; algorithms live in `toolbox/image_processing/`.
- Every moved function has a characterization test written **before** the move; output identical after.
- All 6 existing test files pass unmodified; no numerical output changes.
- Ratchets lowered.

## Cautions
- **Coverage is thin — characterization-test-first is mandatory, not optional.** This is the one big
  file where you cannot assume the net exists; build it, then move.
- **Sequence last** among the big-file decompositions — highest risk, so do it once the habit is solid.
- **Move, don't reimplement** — a "cleaner equivalent" of a background algorithm changes the numbers;
  the whole point is byte-identical behaviour.
- `estimate_object_size_px` and background removal feed downstream measurement — pin them with extra
  care; a change propagates silently.
- Re-export shim mandatory; preprocessing/background are near-universally imported — grep every caller.
- One algorithm per commit.
