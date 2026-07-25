# Claude Code spec — The last two decompositions: `ui_modules.py` and `menu_manager.py`

> **◐ IN PROGRESS — Part 1 increment 1 DONE, 1.6.367 (2026-07-25).** `BaseUIClass` (761 lines) + the
> scroll-guard helpers moved VERBATIM to `ui/base_ui.py` — a **leaf** module (Qt/napari/debug_log only), so
> subclass modules can import it without a cycle. This is why the base moved **first** (deviating from the
> spec's "base last" ordering — the spec's rationale was verification confidence, not correctness; cycle-safety
> wins). `ui_modules.py` 3,266 → 2,410 lines, re-exports everything (`from ui_modules import BaseUIClass` /
> `guard_wheel` unchanged). Contract tests updated (`test_ui_structure`, `test_tag_resolver`). **NOTE the
> current tree has ~12 classes, not the 6 the "Verified structure" below lists** — the extra `AnalysisMethodsUI`
> subclasses (ObjectColocAnalysisUI, PixelColocAnalysisUI, ColocalizationAnalysisUI, GeneralAnalysisUI,
> FibrilAnalysisUI) go in `analysis_methods_ui.py` per the "and siblings" grouping.
>
> **◐ Increment 2 DONE, 1.6.368.** The whole `AnalysisMethodsUI` hierarchy (AnalysisMethodsUI + all 9
> subclasses + CollapsibleSection) moved VERBATIM to `analysis_methods_ui.py`. **Deviation from the spec:**
> kept TOGETHER (not a separate `timeseries_condensate_ui.py`) because TimeSeriesCondensateUI inherits
> AnalysisMethodsUI AND AnalysisMethodsUI references TimeSeriesCondensateUI (the analysis switcher) — a mutual
> dep; one module is cycle-free. `ui_modules.py` 2,410 → **816 lines** (from 3,266). **Remaining: ToolboxFunctionsUI
> → toolbox_functions_ui.py (Part 1 final), then Part 2 (menu_manager.py).**
>
> **✅ Part 1 COMPLETE — increment 3, 1.6.369.** `ToolboxFunctionsUI` (686 lines) → `toolbox_functions_ui.py`
> (verbatim, clean move — references nothing else in ui_modules). **`ui_modules.py` is now a 133-line thin
> re-export shim** (from 3,266). Only Part 2 (`menu_manager.py`, 2,344 lines) remains.

**Date:** 2026-07-23 · **Target tree:** 1.6.324 · Verified against the 1.6.324 tree. All six big science
files are now thin shims. These two are what remain of the decomposition programme — and `ui_modules.py`
is now **the largest file in the project**.

| file | lines |
|---|---|
| `ui/ui_modules.py` | **3,266** |
| `ui/menu_manager.py` | **2,344** |

Both are coverage-gated, behaviour-preserving moves. Ship them separately.

---

## Part 1 — `ui_modules.py`: one class per module

### Verified structure
```
BaseUIClass            761 lines   (shared base)
ToolboxFunctionsUI     686
TimeSeriesCondensateUI 509
AnalysisMethodsUI      200
CondensateAnalysisUI   131
_WheelScrollGuard       23
```

### Target
```
ui/
    base_ui.py                    # BaseUIClass + its helpers
    toolbox_functions_ui.py       # ToolboxFunctionsUI
    analysis_methods_ui.py        # AnalysisMethodsUI + CondensateAnalysisUI and siblings
    timeseries_condensate_ui.py   # TimeSeriesCondensateUI
    ui_modules.py                 # thin re-export shim
```

### Method
1. **Write the attribute-presence contract test FIRST**, on today's code: construct each class with a
   stub viewer and assert every `ui_instance.<attr>` it sets still exists. A silently missing widget
   attribute — one a run method reads later — is the realistic failure mode, and an import-only test
   misses it.
2. **Move `BaseUIClass` LAST.** Every UI class inherits it; relocate the subclasses first so the base's
   move is verified against already-moved children.
3. **Move whole classes.** Do not also split long builders in the same commit — one risk at a time.
4. **Move, don't rewrite.** Qt construction order and parenting preserved; no attribute renames.
5. Re-export shim for every class and previously-public name — `ui_modules` is imported very widely;
   grep every caller first.

### Tests
- Attribute-presence contract holds for each class after its move.
- `test_ui_structure` and the smoke tests pass **unmodified**.
- Each class constructs with a stub viewer after relocation.
- The shim resolves every previously-public name.
- Lower the per-file line ratchet.

---

## Part 2 — `menu_manager.py`: separate menu wiring from policy

### The audit's concern, verified
A menu manager should *describe and connect* UI actions, not contain the workflow policy. This one holds
session-loading orchestration, metadata dialogs, the command palette, the tag inspector, grid management,
and napari-menu manipulation.

### Target
```
ui/
    menu_manager.py        # menu DECLARATION + action wiring ONLY (target ≤ ~900 lines)
    menus/
        napari_menus.py    # hide/disable/reorder native menus
        grid_view.py       # managed grid apply/toggle + state
        metadata_dialogs.py# metadata dialog, comparison, contradiction listing
    command_palette.py     # the feature
    tag_inspector.py       # the feature
```
Session-loading orchestration moves to the session module (or a thin `session_menu_actions.py`).

### Method
1. **The menu-contract snapshot is the net** — it must pass **unmodified** at every step. It snapshots
   the menu tree (titles → action texts) and asserts actions resolve to callables, so a dropped, renamed
   or reordered action fails immediately. That is the likeliest regression and the hardest to spot by
   hand.
2. **Move features whole**, behind their existing action: `open_command_palette` moves to
   `command_palette.py`; the menu action calls into it. Action text and position unchanged.
3. **Guarded installs must still install** — assert the resulting action/attribute exists after the move
   (the class of bug where an `except`-wrapped setup silently no-ops).
4. One feature per commit; menu-contract + `pytest -m core` green between each.

### Tests
- Menu-contract snapshot passes unmodified after every move.
- Each moved feature opens via its menu action exactly as before.
- Guarded installs still produce their actions.
- Lower the per-file line ratchet.

### Payoff beyond size
The extracted command palette and tag inspector become independently testable and reusable — the
navigator/feature-registry work can invoke them directly rather than through the menu.

---

## Steps
1. Attribute-presence test → move `ToolboxFunctionsUI` → analysis classes → `TimeSeriesCondensateUI` →
   `BaseUIClass` → shim. One class per commit, tests green between.
2. Extract `napari_menus` → `grid_view` → `metadata_dialogs` → `command_palette` → `tag_inspector` →
   session actions. One feature per commit, menu-contract green between.
3. Lower ratchets; full `pytest -m core` green.
4. Ship each part as its own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG with
   before/after line counts.

## Definition of done
- `ui_modules.py` is a thin shim; each UI class lives in its own module with its attribute contract
  preserved.
- `menu_manager.py` holds declaration and wiring only (~≤900 lines); features live in their own modules.
- `test_ui_structure` and the menu-contract snapshot pass unmodified.
- Ratchets lowered; no behaviour changes.

## Cautions
- **Write the attribute-presence test before moving** — afterwards it encodes whatever the refactor
  produced, bugs included.
- **`BaseUIClass` moves last.**
- **The menu-contract snapshot must pass unmodified** — a reordered action is a real regression.
- **Move, don't improve.** No renames, no Qt reordering, no builder splitting in the same commit.
- Re-export shims are mandatory; both files are widely imported.
- One class / one feature per commit — a bulk sweep is un-bisectable, and mid-refactor sweeps touching
  many files have broken this build before.
