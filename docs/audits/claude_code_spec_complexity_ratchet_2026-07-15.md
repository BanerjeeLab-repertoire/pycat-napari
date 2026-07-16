# Claude Code spec — restore the complexity ratchet (CI is RED)

**Date:** 2026-07-15 · **Target tree:** 1.6.64 · Verified against the 1.6.64 tree.
**Priority: BLOCKING.** `pytest -m core` (the CI gate in `.github/workflows/core.yml:166`) is failing
on `test_complexity_budget.py::test_the_number_of_unreviewable_functions_does_not_GROW`. Every other
core test passes (570). Until this is green, CI blocks.

## The failure
```
AssertionError: 147 functions now exceed 120 lines (the ceiling is 139).
```
The ratchet (`tests/test_complexity_budget.py`) is a **downward-only** counter: `_MAX_LONG_FUNCTIONS
= 139` is a high-water mark that must only ever DECREASE. The count rose to 147 — **8 new functions
crossed 120 lines** since the ceiling was last set.

## ⛔ THE FORBIDDEN FIX — do NOT do this
**Do NOT raise `_MAX_LONG_FUNCTIONS` to 147** (or any higher number). That silently grandfathers the
new offenders and defeats the entire guard. The test's own docstring: *"It demands that there not be
a 137th."* The CHANGELOG records the correct behaviour when the count rose before (137→139): *"the
honest response is to record that they returned, not to shave them to squeeze back under… **that is
the ratchet working.**"* The ratchet firing means **new un-reviewable complexity was added and must
be split back out** — not that the limit is wrong. Raising the ceiling is the one move that makes this
test worthless.

## What did NOT cause it (verified — don't chase these)
The file-I/O decomposition arc REDUCED complexity: `_open_stack_generic` went 542→186 lines, and the
new reader modules (`stack_metadata.py`, `stack_layer_builders.py`, `ims_reader.py`,
`czi_bioformats.py`, `image_reader_2d.py`) contain **zero** functions over 120. So the 8 new offenders
came from other recent feature work, not the loader work.

## The fix: split ~10 functions back under 120, re-baseline the ceiling DOWN
Get the count to **≤ 137** (below the old 139 — honour the downward-only intent; don't just tie it).
That means splitting **at least 10** functions from >120 to ≤120. Then set `_MAX_LONG_FUNCTIONS` to the
**new, lower** actual count (whatever it is after the splits — e.g. if you split 10 and land at 137,
set it to 137). Lowering the ceiling to match a genuine reduction is the ratchet working; raising it is
not.

### Target the SAFE splits first — pure-Qt UI builders (zero science risk)
These are `_add_*` / `_on_*` widget-construction functions: layout, signal wiring, no numerical
science. Splitting them (extract a `_build_<subpanel>()` helper for a coherent block of widget
construction, call it from the parent) is mechanical and cannot change any measurement. Verified
candidates in the 125–145 band (pick ~10, prefer the longest):
- `_on_dynamic` (145) — toolbox/advanced_analysis_ui.py
- `_add_molecular_counting` (141) — toolbox/molecular_counting_tools.py
- `_add_run_cellpose_segmentation` (140) — ui/ui_segmentation_mixin.py
- `_on_finished` (140) — toolbox/timeseries_condensate_tools.py
- `_add_pipeline_snr_analysis` (136) — toolbox/pipeline_snr_tools.py
- `_add_ts_upscale_stack` (134) — toolbox/timeseries_condensate_tools.py
- `_add_run_sacf_analysis` (131) — toolbox/spatial_acf_tools.py
- `_add_spatial_randomness` (129) — toolbox/spatial_randomness_tools.py
- `_add_analysis` (129) — toolbox/frap_ui.py
- `_on_run` (127) — toolbox/invitro_fluor_ui.py
- `_on_run` (126) — toolbox/invitro_fluor_ui.py
- `_on_finished` (125) — toolbox/ts_cellpose_tools.py

**Do NOT split the SCIENCE functions** in that band (`number_and_brightness`, `partition_coefficient_field`,
`viscosity_measurement`, `fit_*`, `qc_*`, `coloc_significance`, `puncta_refinement_*`, `enhance_stack`,
`cell_analysis_func`, etc.) as part of this CI-unblock task. Splitting a science function risks changing
behaviour and needs its own careful pass with tests; that's the "break up long scientific functions"
roadmap item, not this. UI builders give all the headroom needed with none of the risk.

### How to split a UI builder safely
For each: find a self-contained block of widget construction (a labelled group, a row of controls, a
sub-section) and extract it into a private helper on the same class/module that takes the parent
container (and any needed refs) and appends its widgets. The parent calls the helper where the block
was. No behaviour change — same widgets, same order, same signals. Compile and, if the module has a UI
smoke path, confirm the widget still builds.

## Steps
1. Split ~10 UI-builder functions from the list above until the ratchet count is ≤ 137.
   Re-run `pytest tests/test_complexity_budget.py -v` after each few to watch the count drop.
2. Set `_MAX_LONG_FUNCTIONS` in `tests/test_complexity_budget.py` to the new actual count (≤137).
   Update the accompanying comment to note the reduction (date + "split N UI builders").
3. Run the full `pytest -m core` — all green, including `test_ui_modules_does_not_GROW` and
   `test_nothing_exceeds_the_ABSOLUTE_longest_function` (don't regress those).
4. Ship: own version bump + PyPI push + commit (EXPLICIT filenames — the touched UI files +
   `tests/test_complexity_budget.py` + pyproject + CHANGELOG) + CHANGELOG entry. The CHANGELOG entry
   states: the ratchet fired (147 > 139) because recent feature work added long functions; resolved by
   splitting N UI builders back under 120 and lowering the ceiling to <new>, NOT by raising it.

## Definition of done
- Count ≤ 137; `_MAX_LONG_FUNCTIONS` set to that lower number, not raised.
- Only UI builders split; no science function touched.
- Full `pytest -m core` green.
- Behaviour-preserving: every split UI still constructs the same widgets.
- Shipped with a CHANGELOG entry that explicitly records this was fixed by splitting, not by bumping.

## Cautions
- The ceiling moves DOWN only. If after splitting you land at 137, set 137 — never set it to the
  pre-split number and never above.
- Extract helpers, don't delete or reorder widgets. A UI builder split that changes tab order or drops
  a signal connection is a regression even though the science is untouched.
- If a chosen function turns out to be mostly one indivisible block (no clean sub-section), skip it and
  take the next UI builder on the list — don't force an ugly split.
