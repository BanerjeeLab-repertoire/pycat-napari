# Claude Code spec — UI-builder split: the five biggest functions in the tree

**Date:** 2026-07-19, reconciled 2026-07-21 · **Target tree:** re-verified against **1.6.221**
(originally written against 1.6.156, updated against 1.6.176). This file reconciles the two earlier
`ui_builder_split` drafts into one. Continues the method the complexity-ratchet file already records as
successful, applied to the worst remaining offenders. **Behaviour-preserving; no science function is
touched.**

## Verified state (against 1.6.221)
The science-function split programme has been executed hard (condensate_physics and invitro decomposed
byte-identical through 1.6.221), so **the five largest functions in the entire codebase are now all UI
builders** — the dominant remaining complexity lever. All five are unchanged from when the drafts were
written:

| lines | function | file |
|---:|---|---|
| 638 | `_add_advanced_analysis` | `toolbox/advanced_analysis_ui.py` |
| 595 | `_add_condensate_physics` | `toolbox/condensate_physics_ui.py` |
| 549 | `_add_run_ts_cellpose` | `toolbox/ts_cellpose_tools.py` |
| 520 | `_add_lazy_preprocess_stack` | `toolbox/timeseries_condensate_tools.py` |
| 492 | `_add_run_timeseries_condensate_analysis` | `toolbox/timeseries_condensate_tools.py` |

The ratchet stands at `_MAX_LONG_FUNCTIONS = 120`, `_LONG_FUNCTION_LIMIT = 120` (both in
`tests/test_complexity_budget.py`). These five `_add_*` builders share the signature
`(ui_instance, layout=None, separate_widget=False)` — pure widget construction and signal wiring, no
numerics. Getting each under the limit is the biggest single ratchet move available.

## Why UI builders are the safe target (the ratchet file's own argument)
The complexity-budget test records the method that already worked: *"pure-Qt UI-builder functions
(`_add_*` / `_on_run` / `_on_finished`) were each split by extracting a contiguous widget block into a
helper, dropping the count 147 → 135. **No science function was touched.**"* These five are the same
species. And the ratchet's discipline is explicit about the ONLY acceptable response to drift:

> *"The honest response is the ratchet's whole point: **split the new work back out, don't raise the
> ceiling.** … The ceiling is lowered to the genuine new value — the ratchet moving DOWN, which is it
> working; **it is never raised to grandfather offenders.**"*

`_add_advanced_analysis` is the ideal first case: it builds a `QTabWidget` with tabs added at line 274
(`"Morphological"`) and 684 (`"Organizational"`). **It splits along its own tab boundaries** — each
tab's construction becomes a `_build_<tab>_tab(ui_instance)` helper and the outer function shrinks to
tab assembly plus cross-tab wiring. Almost no judgement required.

## Method — extract contiguous widget blocks, nothing else
For each of the five:
1. Find the contiguous blocks building one logical section (a tab, a group box, a parameter-row
   cluster, a button bar).
2. Extract each into a module-level `_build_<section>(ui_instance, parent)` that constructs and
   attaches its widgets **and sets the `ui_instance.<attr>` references the rest of the class reads**.
3. The original becomes a sequence of `_build_*` calls plus the wiring that genuinely spans sections.
4. **Do not** reorder construction, change parent/child relationships, rename `ui_instance` attributes,
   or alter signal connections. Qt is order- and parent-sensitive; a "tidy-up" here is how a widget
   silently stops appearing.

Target: each under 120 lines, then **lower** `_MAX_LONG_FUNCTIONS` by the number of builders that drop
under the limit.

## The safety net — the real risk is a dropped attribute
Pure-Qt code is weakly covered, so verification must be deliberate, and the contract test must exist
**before** any split (a test written afterward encodes whatever the refactor produced, bugs included):

- **The attribute-presence contract test already exists: `tests/test_ui_builder_split.py`** (a static
  AST guard, `core`-marked, currently passing). It captured — from the 1.6.182 tree, before any split —
  the `ui_instance.<attr> =` assignments each of the five builders makes, and asserts every one is
  still assigned *somewhere in the builder's module* after the split (i.e. it moved into a `_build_*`
  helper rather than vanishing), plus that each builder still exists by name. A silently missing
  attribute — a spin box or worker handle the run method later reads — is the realistic failure mode,
  and an import-only test misses it entirely. **Do not regenerate its reference sets from post-split
  code.** Step 1 below is therefore already done.
- `test_ui_structure.py` (menu-referenced methods exist, layouts present) must stay green.
- If a builder has a menu/registry contract (some `_add_*` register actions), assert that survives too
  — reuse the menu-contract pattern from 1.6.148.

## Steps
1. ~~Add the attribute-presence contract test for the five targets first~~ — **DONE**
   (`tests/test_ui_builder_split.py`, reference sets captured pre-split; keep it green through every
   step).
2. Split `_add_advanced_analysis` along its two tab boundaries; run tests.
3. Split `_add_condensate_physics`; run tests.
4. Split `_add_run_ts_cellpose`; run tests.
5. Split the two `timeseries_condensate_tools` builders; run tests.
6. Re-count; **lower `_MAX_LONG_FUNCTIONS`** to the achieved value, with a dated comment in the
   established style noting what was split and that no science function was touched.
7. Full `pytest -m core` green after each split.
8. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG reporting before/after count.

## Definition of done
- The five named builders are each under 120 lines, split only along widget-block boundaries.
- Every `ui_instance` attribute they previously set is still set (proven by `test_ui_builder_split.py`).
- `_MAX_LONG_FUNCTIONS` is lowered to the genuine new count, never raised.
- No science function modified; every pre-existing test passes unmodified.
- CHANGELOG reports the measured count change.

## Cautions
- **Never raise the ceiling.** If a split proves infeasible, record the count honestly and leave the
  ceiling where it is — do not grandfather.
- **The contract test is already written and captures pre-split behaviour** — do not regenerate its
  reference sets from post-split code.
- Preserve Qt construction order and parenting exactly; do not "tidy" while splitting.
- One builder per commit — a five-file sweep is un-bisectable if a test breaks.
- Do not touch science functions here. The remaining long ones (`fit_anomalous_diffusion`,
  `partition_coefficient_local`, …) are a different risk class — splitting numerical code needs
  behavioural tests first; that is the companion science-split spec's domain, not this one.
