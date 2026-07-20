# Claude Code spec — Decompose `ui_modules.py`: verification FIRST, then `MenuManager`

**Date:** 2026-07-19 · **Target tree:** 1.6.144 · Verified against the 1.6.144 tree. Third and last
concentration point, after `vpt_ui.py` (2458 → 1138, −54%) and `file_io.py`. **This one is different
and must be approached differently** — the codebase's own ratchet file argues *against* naively
splitting it, and that argument is still substantially correct. This spec therefore builds the
verification first and only then moves the one region that is genuinely safe.

## Read this before starting: the standing argument against splitting
`tests/test_complexity_budget.py` opens with a deliberate warning about this exact file:

> *"Why not split it? **Because it cannot be verified.** `ui_modules` has ~17 % name-coverage in the
> test suite, and most of that is `__init__`. **A refactor whose only verification is 'it still
> imports' is a refactor that ships bugs** — and the value of splitting is preventing *future* bugs,
> while the cost would be *introducing* them today, blind."*

That reasoning is sound and was written by someone who had just found a real bug hiding in this file
(35 lines installing the pixel-size gate wrapped in `except Exception: pass`, so the gate could vanish
silently — fixed in 1.5.509).

**Verified: the coverage situation has barely improved.** The UI-specific tests are:
- `test_ui_structure.py` — **4 tests**, all AST/static (parses, menu-referenced methods exist, layouts
  present, classes present)
- `test_ui_smoke.py` — **3 tests** (central manager constructs, toolbox UI has core methods, menu
  manager constructs)
- `test_mixin_imports.py` — **2 tests** (mixin files exist, names resolve)

Nine tests, almost entirely "it imports and has the right attribute names." That is **not** a net that
can catch a behavioural regression in menu wiring or dialog logic.

**So the rule for this spec: do not move anything until it can be verified. Verification is the
deliverable; the line reduction is secondary.**

## What is already extracted (don't redo it)
`ui/` is not untouched — six mixins and eight helper modules already live outside `ui_modules.py`:
`ui_diagnostics_mixin` (52 KB), `ui_segmentation_mixin`, `ui_analysis_mixin`, `ui_imageops_mixin`,
`ui_labels_mixin`, `ui_filtering_mixin`, plus `field_status`, `brushable_table`,
`linked_selection_dock`, `scene_switcher`, `operation_gating`, `comparative_figures_ui`,
`coordinate_readout`, `workflow_checklist`. The widget-building bulk already moved. What remains is
mostly *class definitions and menu construction*.

## Verified structure (5572 lines)
| class | line | approx size |
|---|---:|---:|
| `_WheelScrollGuard`, `_FileDropFilter` | 100, 150 | ~230 |
| `BaseUIClass` | 330 | ~760 |
| `ToolboxFunctionsUI` | 1092 | ~713 |
| `AnalysisMethodsUI` + 7 subclasses (`Condensate`, `TimeSeries`, `ObjectColoc`, `PixelColoc`, `Colocalization`, `General`, `Fibril`) + `CollapsibleSection` | 1805–3408 | ~1600 |
| **`MenuManager`** | 3408 | **~2164** |

`MenuManager` alone is 39% of the file, across ~33 methods.

## Phase 1 — build the verification (do this first, ship it, stop)
Add `tests/test_menu_contract.py` (`core` where possible, Qt-smoke where not). The goal is a net that
would catch a *behavioural* break in menu wiring, not just an import:
1. **Every menu action resolves to a callable that exists** and has a compatible signature — extend
   the existing `test_every_menu_referenced_add_method_exists` from "the name exists" to "it is
   callable with the arguments the menu will pass" (the `make_lambda`/`_add_actions_to_menu` path at
   `MenuManager` +1193/+1213).
2. **The menu tree is stable**: snapshot the constructed menu structure (menu titles → action texts)
   and assert it against a committed reference. A refactor that silently drops or reorders an action
   fails. This is the single highest-value test here — it is exactly what a blind move breaks.
3. **The guarded installs actually install**: assert the pixel-size gate and any other
   `except`-wrapped setup in `_setup_menu_bar` (+388) genuinely ran — i.e. the 1.5.509 class of bug
   cannot silently return. Check for a resulting attribute/action, not for the absence of an
   exception.
4. **Session/dialog entry points construct**: `_open_session_loader` (+952),
   `_show_recorded_steps_dialog` (+1112), `open_tag_inspector` (+1629), `open_command_palette`
   (+1751), `_open_scene_switcher` (+1274) — each constructs without error given a stub viewer.

**Ship Phase 1 as its own version.** It has standalone value (it protects the file as it is today) and
it is the precondition for Phase 2. If Phase 1 proves hard to write, that is itself the finding —
report it and **stop**, rather than moving code without a net.

## Phase 2 — extract `MenuManager` (only after Phase 1 is green)
Move `MenuManager` to `src/pycat/ui/menu_manager.py` **verbatim**, then split it internally along the
seams its own method names already suggest:
- `menu_manager.py` — the class, construction, `_setup_menu_bar`, action wiring
- `ui/menus/napari_menus.py` — the napari-native menu hiding/visibility/reordering block
  (`_hide_napari_native_menus`, `_set_napari_menus_visible`, `_toggle_napari_menus`,
  `_reorder_pycat_menu_bar`, `_disable_napari_open_actions`) — ~300 lines, self-contained
- `ui/menus/grid_view.py` — the managed-grid block (`_apply_managed_grid`, `_grid_tileable_visible`,
  `_annotation_layers`, `_restore_grid_removed_layers`, `_on_grid_*`) — ~200 lines, self-contained
- `ui/menus/metadata_dialogs.py` — `_show_metadata_dialog`, `_gather_compared_metadata`,
  `_maybe_warn_metadata_diff`, `_show_metadata_comparison` — ~250 lines

Target: **`ui_modules.py` ≤ 3600 lines** (−35%), `MenuManager` reduced to wiring plus delegation.

**Do not attempt `BaseUIClass`, `ToolboxFunctionsUI`, or the `AnalysisMethodsUI` family in this
spec.** Those are the shared base and the analysis-panel hierarchy; moving them without behavioural
tests is precisely the blind refactor the ratchet warns about. If Phase 2 goes well and the menu
contract test proves its worth, a later spec can extend the same method to them.

## Rules
- **Move, don't rewrite.** Cut, paste, fix imports. Behaviour changes are separate commits.
- **One move per commit**, full `pytest -m core` between each, plus the new menu-contract test.
- **No test may be edited to make a move pass.**
- Convert broad handlers only where a move already touches them; annotate the deliberate Qt-teardown
  ones `# broad-ok: <reason>`. `ui_modules.py` is the largest single holder of these — but converting
  them is *not* this spec's goal and must not become a second, unverified change surface.
- Lower the `ui/ui_modules.py` line ratchet (currently 5573) to the achieved value.

## Definition of done
**Phase 1:** a menu-contract test suite that fails if an action is dropped, renamed, made
uncallable, or if a guarded install silently no-ops. Shipped independently.
**Phase 2:** `MenuManager` lives in its own module(s); `ui_modules.py` ≤ 3600 lines; every
pre-existing test passes unmodified; the new contract tests pass; line ratchet lowered; CHANGELOG
reports measured before/after.

## Cautions
- **Phase 1 is not optional and not reorderable.** The whole reason this file was left alone is that
  moving it blind ships bugs. If you cannot build the net, do not make the move.
- The menu-structure snapshot is the test that matters most — a dropped action is the likeliest
  regression and the hardest to notice by hand.
- Watch for the `except Exception: pass`-around-a-guard pattern while moving. If you find another one,
  **report it** — that is a real bug, and it is the exact species already found here once.
- Do not touch the mixins; they are already extracted and out of scope.
- Do not start `BaseUIClass` / `AnalysisMethodsUI` decomposition here. One region, verified, then stop.
