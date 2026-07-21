# Claude Code spec — Ratchet the complexity budget DOWN: split the biggest UI builders

**Date:** 2026-07-19 · **Target tree:** 1.6.156 · Verified against the 1.6.156 tree. Continues the
method the ratchet file already records as successful, applied to the worst remaining offenders.
**Behaviour-preserving; no science function is touched.**

## The situation (verified)
`_MAX_LONG_FUNCTIONS = 135`, `_LONG_FUNCTION_LIMIT = 120`. A fresh AST count over `src/pycat` finds
**138 functions over 120 lines** — so the count has drifted up again since the ceiling was last
lowered (the four decompositions moved code between files without reducing per-function length).

The ratchet file states the correct response, and it is worth quoting because it is the discipline this
spec follows:

> *"The honest response is the ratchet's whole point: **split the new work back out, don't raise the
> ceiling.** … The ceiling is lowered to the genuine new value — the ratchet moving DOWN, which is it
> working; **it is never raised to grandfather offenders.**"*

It also records exactly how this was done safely before: *"12 pure-Qt UI-BUILDER functions (`_add_*` /
`_on_run` / `_on_finished`) were each split by extracting a contiguous widget block into a helper,
dropping the count 147 → 135. **No science function was touched.**"*

## The targets — verified as pure UI builders
The five longest functions in the tree are all `_add_*` widget constructors with the signature
`(ui_instance, layout=None, separate_widget=False)`:

| lines | function | file |
|---:|---|---|
| 638 | `_add_advanced_analysis` | `toolbox/advanced_analysis_ui.py` |
| 595 | `_add_condensate_physics` | `toolbox/condensate_physics_ui.py` |
| 549 | `_add_run_ts_cellpose` | `toolbox/ts_cellpose_tools.py` |
| 520 | `_add_lazy_preprocess_stack` | `toolbox/timeseries_condensate_tools.py` |
| 492 | `_add_run_timeseries_condensate_analysis` | `toolbox/timeseries_condensate_tools.py` |

Verified by reading their openings: `_add_advanced_analysis` is a *"tabbed widget with three
sections"*; `_add_condensate_physics` starts with `QVBoxLayout()` and the pixel-size gate. These are
widget construction and signal wiring — the safest possible refactoring target, and the same category
already split successfully once.

`_add_advanced_analysis` is the ideal first case: a three-tab widget splits along tab boundaries with
almost no judgement required.

## Method — extract contiguous widget blocks, nothing more
For each target:
1. Identify contiguous blocks that build one logical section (a tab, a group box, a parameter row
   cluster, a button bar).
2. Extract each into a module-level helper `_build_<section>(ui_instance, parent_layout)` that
   constructs and returns/attaches its widgets.
3. The original function becomes a sequence of calls plus the wiring that genuinely spans sections.
4. **Do not** reorder widget construction, change parent/child relationships, rename attributes on
   `ui_instance`, or alter signal connections. Qt is order- and parent-sensitive; a "tidy-up" here is
   how a widget silently stops appearing.

Target: each of the five under 120 lines, which alone moves the count from 138 to ~133 and lowers the
ceiling accordingly.

## The safety net — this is the part to get right
Pure-Qt code is weakly covered, so verification must be deliberate:
- **`test_ui_structure.py` must stay green** — it asserts menu-referenced methods exist and layouts are
  present.
- **The menu-contract suite from 1.6.148** covers `MenuManager`; these five are toolbox widgets, so
  extend the same idea: for each split widget, assert it **constructs** with a stub `ui_instance` and
  that the attributes downstream code reads off `ui_instance` (the spin boxes, combos, and buttons
  other methods reference) still exist afterward. A silently missing attribute is the realistic
  failure mode here, and it is exactly what an import-only test misses.
- Grep each target for the `ui_instance.<attr> =` assignments it makes, and assert every one is still
  set after the split. This is mechanical and catches the real risk.

## Steps
1. Add the attribute-presence contract test for the five targets **first** (before any split), so it
   captures today's behaviour as the reference.
2. Split `_add_advanced_analysis` (tab boundaries — the easiest); run tests.
3. Split `_add_condensate_physics`; run tests.
4. Split `_add_run_ts_cellpose`; run tests.
5. Split the two `timeseries_condensate_tools` builders; run tests.
6. Re-count and **lower `_MAX_LONG_FUNCTIONS`** to the achieved value, adding a dated comment in the
   established style explaining what was split and that no science function was touched.
7. Full `pytest -m core` green after each split.
8. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG reporting before/after count.

## Definition of done
- The five named builders are each under 120 lines, split only along widget-block boundaries.
- Every `ui_instance` attribute they previously set is still set (proven by the new contract test).
- `_MAX_LONG_FUNCTIONS` is lowered to the genuine new count, never raised.
- No science function modified; every pre-existing test passes unmodified.
- CHANGELOG reports the measured count change.

## Cautions
- **Never raise the ceiling.** If a split proves impossible, record the count honestly and leave the
  ceiling where it is — do not grandfather.
- **Write the attribute-presence test before splitting**, not after. A test written afterward encodes
  whatever the refactor produced, including its bugs.
- Do not touch science functions in this spec. `fit_anomalous_diffusion` (394) and
  `partition_coefficient_local` (394) are long, but splitting numerical code without behavioural tests
  is a different risk class — see the companion spec.
- Preserve Qt construction order and parenting exactly.
- One builder per commit; a five-file sweep is un-bisectable.
