# Claude Code spec — Decompose `ui_modules.py` by UI class

**Date:** 2026-07-20 · **Target tree:** 1.6.203 · Verified against the 1.6.203 tree. Still the **largest
file in the project (3,268 lines)** even after the MenuManager extraction. What remains is a stack of
distinct UI classes co-located in one module; they split cleanly by class into their own files. Behaviour-
preserving; the ui-structure + attribute-presence net makes it safe.

## Verified state
```
classes (post-MenuManager extraction):
  761  BaseUIClass              (the shared base — stays, but can shed helpers)
  687  ToolboxFunctionsUI
  509  TimeSeriesCondensateUI
  221  GeneralAnalysisUI
  200  AnalysisMethodsUI
  153  ColocalizationAnalysisUI
longest functions:
  399  _add_reference_frame_selector
  259  _add_measure_line
  180  setup_ui
```
Coverage: 10 test files (`test_ui_structure`, smoke tests, mixin-import tests) — a real structural net.

The earlier work already extracted MenuManager and six mixins. What is left is **several full UI classes
in one file** — the natural split is one class (or class family) per module.

## Target — one class per module
```
ui/
    base_ui.py                    # BaseUIClass (the shared base + its helpers)
    toolbox_functions_ui.py       # ToolboxFunctionsUI
    analysis_methods_ui.py        # AnalysisMethodsUI + the analysis subclasses:
                                  #   GeneralAnalysisUI, ColocalizationAnalysisUI
    timeseries_condensate_ui.py   # TimeSeriesCondensateUI
    ui_modules.py                 # thin re-export shim (many modules import from here)
```
`ui_modules.py` becomes a re-export shim. Target: the shim + `BaseUIClass` well under the ratchet, each
class in a focused file.

## Method — attribute-presence tests, class by class
The `ui_modules.py` decomposition discipline (from the earlier spec) applies:
1. **Before moving a class, capture its attribute contract** — the `ui_instance.<attr>` a class sets and
   downstream code reads. Assert every one still exists after the move (construct with a stub, check
   attributes). A silently missing attribute — a widget the run method later reads — is the realistic
   failure mode and an import-only test misses it.
2. **`test_ui_structure` must pass unmodified** (menu-referenced methods exist, layouts present, classes
   present).
3. **The two long builders** (`_add_reference_frame_selector` 399, `_add_measure_line` 259) can be split
   along widget-block boundaries as part of moving their class — or left intact and moved whole. Prefer
   moving whole first (lower risk), then splitting in a follow-up if the ratchet needs it.
4. **Move, don't rewrite** — Qt construction order and parenting preserved; no attribute renames.

### The BaseUIClass consideration
`BaseUIClass` (761 lines) is the shared base — moving it is the highest-impact and highest-risk step
because every UI class inherits it. Move it **last**, after the subclasses are in their own files and
proven, so the base's move is verified against already-relocated children. Keep its public surface
identical.

### Hard rules
- One class (or tight family) per commit; ui-structure + attribute-presence + `pytest -m core` green
  between each.
- No test edited to make a move pass.
- Re-export shim from `ui_modules` for every class + previously-public name; grep imports first
  (`ui_modules` is imported very widely).

## Why now
- Largest file in the project — the biggest single ratchet move available.
- Real structural net (ui-structure + smoke + mixin tests) — safe.
- Pairs with the menu_manager split to finish the UI-layer decomposition the audit tracked.
- Focused per-class files make the beginner-mode/feature-registry UI work far easier to build against.

## Tests
- Attribute-presence contract for each class: every `ui_instance` attribute it set is still set after
  the move.
- `test_ui_structure` + smoke tests pass unmodified.
- Each UI class constructs with a stub viewer after relocation.
- The re-export shim resolves every class and previously-public name (widely imported).
- Lower `_MAX_LONG_FUNCTIONS` / the `ui/ui_modules.py` line ratchet.

## Steps
1. Write the attribute-presence contract test for the classes to be moved (first, on today's code).
2. Move `ToolboxFunctionsUI` → `toolbox_functions_ui.py`; run structure + attribute + core.
3. Move the analysis classes (`AnalysisMethodsUI` + `General`/`Colocalization`) → `analysis_methods_ui.py`; run.
4. Move `TimeSeriesCondensateUI` → `timeseries_condensate_ui.py`; run.
5. Move `BaseUIClass` → `base_ui.py` (LAST — verified against relocated children); run.
6. `ui_modules.py` → re-export shim; lower ratchets.
7. Full `pytest -m core` + ui-structure green after each step.
8. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG before/after.

## Definition of done
- Each UI class lives in its own module; `ui_modules.py` is a thin re-export shim.
- Every class's attribute contract is preserved (proven by the attribute-presence test written first).
- `test_ui_structure` + smoke tests pass unmodified.
- Line ratchets lowered.

## Cautions
- **Write the attribute-presence test before moving** — afterward it encodes whatever the refactor
  produced, bugs included. A missing widget attribute is the realistic failure mode here.
- **Move `BaseUIClass` last** — it is the shared base; relocate the subclasses first so its move is
  verified against already-moved children.
- **Move whole classes first**; split the long builders (`_add_reference_frame_selector`,
  `_add_measure_line`) only as a follow-up if the ratchet still needs it — one risk at a time.
- Preserve Qt order/parenting; no attribute renames.
- Re-export shim mandatory — `ui_modules` is imported very widely; grep every caller.
