# Claude Code spec — Focus selection: close the last debris gap (mask threading)

> **✅ STATUS — DONE, shipped in 1.6.142** (stamped 2026-07-20 from a CHANGELOG cross-reference). Optional `mask=` on `bf_analyse_focus_series` + `analyse_frame_quality`; `resolve_frame_mask`; `test_focus_debris.py`.

**Date:** 2026-07-19 · **Target tree:** 1.6.133 · Verified against the 1.6.133 tree. **This item is
much smaller than the roadmap implies** — most of the debris defense already shipped. This spec covers
only the verified remainder. Touches `brightfield_tools.py` and `condensate_physics_tools.py`.

## What already exists (verified — do NOT rebuild)
The roadmap rubric "Focus selection must not pick the sharpest DEBRIS" reads as an open bug, but two
of its three layers are already in the tree:

1. **`robust_focus_energy`** (`utils/math_utils.py:21`) — a trimmed mean that drops the top ~1% of
   per-pixel magnitudes. Its reasoning is exactly the debris argument: *"The discriminating fact is
   spatial extent, not magnitude. A real in-focus object lights up many pixels; a speck lights up
   few."* It is honest about its limit (defends against debris up to ~1% of frame area).
2. **It is applied comprehensively** — 9 call sites across `bf_focus_metric` (Brenner), Tenengrad,
   normalised variance, `bf_analyse_frame_quality`, and `analyse_frame_quality`'s Laplacian variance
   and gradient energy. Clean frames are unaffected (trimming a smooth distribution does not move the
   argmax); debris is trimmed.
3. **`bf_focus_metric` already accepts `mask=`** (`brightfield_tools.py:708`) with a docstring
   demonstrating the exact failure and stating `mask=None` preserves whole-frame behaviour.

So the "sharpest debris wins" failure is already substantially mitigated **statistically**. What is
missing is the **spatial** layer: actually restricting scoring to the biological region when one is
available.

## The remaining gap (verified)
- `bf_analyse_focus_series` (`brightfield_tools.py:759`) calls `bf_focus_metric(frame)` at **:784**
  and `bf_focus_metric(arr[i])` at **:846** — the `mask=` parameter exists but is **never passed**.
  The series scorer cannot use a mask even if the caller has one.
- `analyse_frame_quality` (`condensate_physics_tools.py`) takes `stack, frame_interval_s,
  threshold_fraction, entropy_bins, bleach_r2_min, drift_slope_threshold` — **no mask parameter at
  all**, so its Laplacian-variance/entropy scoring is whole-frame only.

Trimming defends against *small* debris. A large out-of-plane structure — the case
`robust_focus_energy`'s docstring explicitly declines to solve ("larger debris on a different plane is
a rarer, and genuinely harder, problem this does not claim to solve") — is only handled by scoring
inside the region you care about.

## Fix
1. **`bf_analyse_focus_series`**: add an optional `mask=` parameter (a single `(H, W)` boolean applied
   to every frame, or a per-frame stack matching `arr`). Thread it to both `bf_focus_metric` call
   sites. `mask=None` → byte-identical current behaviour.
2. **`analyse_frame_quality`**: add the same optional `mask=`. When supplied, compute Laplacian
   variance, entropy, and gradient energy **over the masked region only** (extract masked pixels
   before the statistic; do not zero-fill outside the mask — zeros create artificial edges at the mask
   boundary that are themselves high-gradient). Keep `robust_focus_energy` applied on top: mask and
   trimming are complementary, not alternatives.
3. **Callers with a mask should pass it.** Grep the callers of both functions; where a segmentation /
   cell / condensate mask is already in hand, thread it through. Where none exists, leave `None`.
   **Do not fabricate a mask** — a wrong mask is worse than whole-frame.

## Test
`tests/test_focus_debris.py` (mark `core`). The acceptance case the roadmap specifies, sized to defeat
the trimming so it tests the *mask*, not the trim:
- synthetic stack: frame A holds an in-focus, spatially-extended condensate; frame B holds **large**
  out-of-plane debris (well above the ~1% trim fraction — e.g. ≥5% of frame area) that is sharper.
- assert `mask=None` picks frame B (the failure the mask exists to fix — and proof the fixture is
  actually adversarial, since trimming alone does not save it);
- assert `mask=`condensate region picks frame A;
- assert on a CLEAN stack (no debris) that `mask=None` and `mask=` pick the SAME frame — the mask must
  not perturb good data;
- run all of the above for **both** `bf_analyse_focus_series` and `analyse_frame_quality`.

Also add a regression test that `robust_focus_energy` still defeats *small* debris without a mask, so
the two layers are covered independently.

## Steps
1. `mask=` on `bf_analyse_focus_series`, threaded to both call sites.
2. `mask=` on `analyse_frame_quality`, masked-pixel extraction (no zero-fill), trimming retained.
3. Thread masks from callers that have one.
4. `tests/test_focus_debris.py` — four assertions × two functions + the small-debris regression.
5. Full `pytest -m core` green (complexity budget).
6. Ship: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG. Update the roadmap rubric
   to RESOLVED and **state accurately** that the statistical layer (`robust_focus_energy`) shipped
   earlier and this closes the spatial layer — so the record doesn't imply the whole thing was broken.

## Definition of done
- Both focus-series scorers accept an optional mask and score inside it; `mask=None` is byte-identical.
- Masked scoring picks the in-focus condensate over large out-of-plane debris; unmasked does not.
- A clean stack yields the same chosen frame masked or unmasked.
- Small-debris trimming still covered independently.
- Full `pytest -m core` green.

## Cautions
- **Do not zero-fill outside the mask** — the mask boundary becomes a high-gradient artefact that
  inflates every focus metric. Extract masked pixels, then aggregate.
- Keep `robust_focus_energy` — mask and trim are complementary layers, not alternatives.
- `mask=None` must preserve current behaviour exactly; existing callers are unchanged.
- Do not fabricate masks to force the fix.
- Make the test debris LARGE enough to defeat the 1% trim, or the test passes for the wrong reason and
  proves nothing about the mask.
