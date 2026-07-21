# Claude Code spec — Decompose `menu_manager.py`: separate menu wiring from policy

**Date:** 2026-07-20 · **Target tree:** 1.6.203 · Verified against the 1.6.203 tree. The engineering
audit's specific concern: *"the complexity may be migrating from one central module into another…
`menu_manager.py` should be audited for whether it is accumulating menu declarations, workflow
semantics, widget construction, layer resolution, operation dispatch, gating, and state management. A
menu manager should describe and connect UI actions, not contain the underlying workflow policy."*
Verified — it has. This extracts the policy, leaving a thin menu manager.

## Verified state (2,344 lines, 63 functions)
The audit is right — `menu_manager.py` holds far more than menu wiring:
```
325  _setup_menu_bar              (menu declaration — belongs here)
233  _add_toolbox_to_menu         (menu declaration — belongs here)
158  _open_session_loader         (POLICY — session loading orchestration)
151  _show_metadata_dialog        (POLICY — metadata dialog + comparison)
146  _disable_napari_open_actions (napari integration)
132  open_command_palette         (a whole feature — command palette)
121  open_tag_inspector           (a whole feature — tag inspector)
 85  _apply_managed_grid          (POLICY — grid view management)
 83  _toggle_grid_view            (grid)
```
Menu *declaration* (`_setup_menu_bar`, `_add_toolbox_to_menu`) legitimately belongs. The rest —
session-loading orchestration, metadata dialogs, the command palette, the tag inspector, grid
management, napari-menu manipulation — is **policy and features** that a menu manager should *invoke*,
not *contain*.

Coverage: 8 test files, including the menu-contract suite (built specifically before the earlier
MenuManager extraction) — a real net for the menu structure.

## Target — menu manager stays thin; policy moves to owners
```
ui/
    menu_manager.py            # menu DECLARATION + wiring only: _setup_menu_bar, _add_toolbox_to_menu,
                               # action connection. Invokes the features below; contains none of them.
    menus/
        napari_menus.py        # _hide_napari_native_menus, _disable_napari_open_actions, reorder/toggle
        grid_view.py           # _apply_managed_grid, _toggle_grid_view, grid state
        metadata_dialogs.py    # _show_metadata_dialog, comparison, diff-warning
    session_menu_actions.py    # _open_session_loader orchestration (or fold into the session module)
    command_palette.py         # open_command_palette (the feature)
    tag_inspector.py           # open_tag_inspector (the feature)
```
`menu_manager.py` retains menu structure + action wiring, delegating each action to its owner module.
Target: **≤ 900 lines** (−60%), holding declaration and delegation, no feature bodies.

## Method — the menu-contract test is the net
1. **The menu-contract suite must pass unmodified** at every step — it snapshots the menu tree (titles →
   action texts) and asserts actions resolve to callables. A moved feature that a menu still invokes
   correctly keeps this green; a dropped/renamed action fails it. This is exactly the net the earlier
   MenuManager extraction built, now reused.
2. **Move features whole, behind their existing action.** `open_command_palette` moves to
   `command_palette.py`; the menu action now calls `command_palette.open(...)`. The action text and
   position are unchanged (contract test proves it).
3. **The guarded installs** (`except`-wrapped setup in `_setup_menu_bar`) must still install — assert
   the resulting action/attribute exists (the 1.5.509-class bug: a guard silently no-op'ing).
4. **Move, don't rewrite** — cut/paste/fix-imports; Qt order and parenting preserved.

### Hard rules
- One feature per commit; menu-contract + `pytest -m core` green between each.
- No test edited to make a move pass.
- Re-export or re-wire so every menu action still resolves (the contract test enforces this).

## Why now
- The audit named it explicitly — policy accumulation in the menu manager is a real structural drift.
- Strong net (menu-contract suite) — safe to split.
- It is the second-largest UI file; pairs with the `ui_modules.py` split to finish the UI decomposition.
- Each extracted feature (command palette, tag inspector) becomes independently testable and reusable —
  the navigator UI / feature-registry work can then invoke them directly.

## Tests
- The menu-contract snapshot passes unmodified after every move (no action dropped/renamed/reordered).
- Each moved feature constructs/opens via its menu action exactly as before.
- Guarded installs still install (assert the resulting action exists).
- All 8 test files pass unmodified.
- Lower the `ui/menu_manager.py` line ratchet.

## Steps
1. Extract `menus/napari_menus.py`; re-wire actions; run menu-contract + core.
2. Extract `menus/grid_view.py`; run.
3. Extract `menus/metadata_dialogs.py`; run.
4. Extract `command_palette.py`; run.
5. Extract `tag_inspector.py`; run.
6. Move `_open_session_loader` orchestration to the session module (or `session_menu_actions.py`); run.
7. `menu_manager.py` retains declaration + wiring; lower ratchet.
8. Full `pytest -m core` + menu-contract green after each step.
9. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG before/after.

## Definition of done
- `menu_manager.py` ≤ ~900 lines, holding menu declaration + action wiring only.
- Session loading, metadata dialogs, grid, command palette, tag inspector, and napari-menu handling live
  in their own modules, invoked by the menu.
- The menu-contract suite passes unmodified; no action dropped/renamed/reordered.
- Line ratchet lowered; all tests pass unmodified.

## Cautions
- **The menu-contract snapshot is the net** — a dropped or reordered action is the likeliest regression
  and the hardest to spot by hand; it must pass unmodified.
- **Watch for `except`-wrapped guards that silently no-op** while moving (the 1.5.509 bug class) — assert
  the guard's result exists, and report any new instance found.
- **Move features whole** behind their existing action; do not partially relocate a feature.
- Preserve Qt order/parenting; do not tidy while moving.
- One feature per commit — a multi-feature sweep is un-bisectable.
