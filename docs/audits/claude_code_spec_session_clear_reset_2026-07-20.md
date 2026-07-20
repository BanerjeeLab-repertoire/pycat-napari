# Claude Code spec — Session load clears prior state; clear resets the method widget too

> **◐ STATUS — Bug 1 DONE (shipped 1.6.193); Bug 2 is a documented follow-on.**
> **Bug 1 (session load stacks) — FIXED.** Both "Load Session" handlers in `ui/menu_manager.py`
> (`_load_discovered_session` and the multi-stem folder loader `_on_load`) now call
> `clear_all_without_saving(...)` **before** `load_session(...)`, so a loaded session REPLACES the workspace
> instead of merging onto it. The clear is guarded: with layers present it prompts (the Clear button's
> discard warning), and `clear_all_without_saving` now **returns True/False** (cleared / cancelled) so the
> handler ABORTS the load on cancel — unsaved work is never silently discarded. Empty workspace → no prompt.
> `tests/test_session_clear_load.py` pins the return-contract and an AST guard that every `load_session`
> caller clears first.
> **Bug 2 (clear does not reset the method widget) — NOT done, deliberately.** The reset primitive exists
> (`field_status.py:201 reset_all` writes OPTIONAL/EXPERT fields back to defaults), but it is **called
> nowhere** and there is **no central registry of the per-widget field-status registries** — each toolbox
> UI builder creates its own registry locally. Wiring `_clear_everything` to reset the active method widget
> therefore needs new plumbing (register each widget's registry on `central_manager`, iterate on clear) plus
> a decision on which fields legitimately persist — a real Qt feature, not testable headless, so it is left
> as a scoped follow-on rather than shipped untested inside this correctness fix.

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. Two related
state-reset bugs: loading a session **stacks onto** the current workspace instead of replacing it, and
clearing state leaves the **method widget** populated. Both should return PyCAT to approximately its
fresh-open state. Behaviour fix; no new capability.

## Bug 1 — session load does not clear first (verified)
`session_loader.py:266` loads the session's source image via:
```python
file_io.open_image_auto(file_path=src_path, clear_first=False)
```
`clear_first=False` — so a session loaded on top of an existing workspace **adds** its layers and data
to whatever was already there. The user expects "load session" to *replace* the current state with the
saved one, the way opening a document does. Instead they get a merge: old layers linger, old dataframes
persist, and identity/layer references from two sessions coexist.

**Fix:** clear before loading. The mechanism already exists — `session.clear_all_without_saving` /
`_clear_everything` (`session.py:20`) does the workspace reset. `load_session` should call the clear
path **first** (with a save prompt if there is unsaved work — see below), then load. This is a
one-decision change with a correctness payoff, but it must be guarded so a user does not silently lose
unsaved analysis.

### The unsaved-work guard
Loading a session discards the current one. So before clearing:
- if the current workspace has unsaved analysis, **prompt**: Save current session / Discard / Cancel.
- Cancel aborts the load (current state untouched).
- This mirrors the existing Save-and-Clear affordance; reuse its logic rather than writing a parallel
  prompt.

## Bug 2 — clear does not reset the method widget (verified)
`_clear_everything` (`session.py:20`) resets: reader cache, layers, the data repository/dataframes, the
workflow checklist progress bar, and napari notifications. It does **not** reset the **method widget's
built parameter state** — the spin boxes, dropdowns, field-status circles, and any partially-filled
workflow the user had open. After a clear, a fresh workflow start still shows the previous workflow's
entered values and status markers.

The goal you stated: clearing should return the system to *approximately what a fresh PyCAT open looks
like*. That includes the method widget.

**Fix:** extend the clear path to reset the active method widget:
- reset the field-status registry (`field_status.py` has `reset_all` at :201 — call it for the active
  widget's registry) so status circles return to their initial state;
- reset user-entered parameter fields to their defaults (the widget's `_add_*` builders know the
  defaults; provide a `reset_to_defaults()` the clear path can call, or rebuild the widget);
- clear any cached per-widget selections/bindings tied to the now-removed layers.

Do this through a defined reset entry point on the widget, not by reaching into its internals from the
session module — the session code should call `active_widget.reset()`, and the widget owns what that
means.

## What "approximately fresh" means (scope the intent)
- **Reset:** layers, dataframes, data repository, checklist, method-widget fields + status, cached
  bindings, reader cache, notifications.
- **Preserve:** the app itself, the loaded module/data-class *choice* (the user is still in "Cell
  Analysis"; they just get it blank), window layout, and anything the user explicitly asked to persist
  (the `persist_measurements` path already preserves ball_radius/object_size/cell_diameter — keep that
  behaviour).
- **Not a process restart** — it is a workspace reset, matching the existing docstring's intent
  ("beginning-of-workflow state").

## Tests (`core` where possible; Qt-smoke for widget reset)
- **Load clears first:** loading a session into a non-empty workspace results in exactly the session's
  layers/data — no leftovers from the prior state (assert layer set and dataframe keys equal the
  session's, not the union).
- **Unsaved-work guard:** loading with unsaved analysis prompts; Cancel aborts and leaves current state
  intact; Discard proceeds.
- **Clear resets the method widget:** after clear, the active widget's field-status registry is at
  initial state and parameter fields are at defaults (not the previous values).
- **Preserved choices:** after clear, the active data-class/module is unchanged; the
  `persist_measurements` values survive if that flag is set.
- **Idempotent:** clearing an already-clear workspace is a no-op, not an error.

## Steps
1. `load_session` clears first (via the existing clear path) behind the unsaved-work prompt.
2. Add a `reset()` entry point to the method widget (field-status `reset_all` + fields to defaults +
   drop stale bindings); call it from `_clear_everything`.
3. Reuse the Save-and-Clear prompt logic for the load guard.
4. Tests above.
5. Full `pytest -m core` green.
6. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (session load now replaces
   prior state; clear also resets the method widget to defaults).

## Definition of done
- Loading a session replaces the current workspace (clears first), guarded by an unsaved-work prompt.
- Clearing resets the method widget's fields and status to fresh-open state, alongside the existing
  layer/data/checklist reset.
- Preserved: module choice, window, and explicitly-persisted measurement values.
- Clearing is idempotent.
- Full `pytest -m core` green.

## Cautions
- **Guard the load** — clearing-before-load discards the current session; never do it silently when
  there is unsaved work. Reuse the Save-and-Clear prompt.
- **The widget owns its reset** — call `active_widget.reset()`; do not reach into widget internals from
  the session module (that couples them and breaks when a widget changes).
- **Preserve, don't nuke** — the module choice and the `persist_measurements` values survive a clear;
  match the existing intent, don't regress it.
- This is a workspace reset, not a process restart — do not tear down the viewer or the app.
