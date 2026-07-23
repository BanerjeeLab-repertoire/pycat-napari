# Claude Code spec — Give results docks room: collapse the method widget when analysis output mounts

> **● STATUS — DONE via TABIFY (the spec-permitted alternative), shipped 1.6.297.** Implemented the reflow in
> the shared `utils/dock_space.py` (Qt-free; napari window duck-typed): `add_results_dock` mounts a results
> dock with napari 0.7.1's `tabify=True` so it tabs alongside the method panel — full height, one tab-click to
> the parameters, reversible, obvious. Chose tabify over the recommended collapse-first because Qt has **no
> native collapse-to-titlebar primitive** (a hand-rolled one fights napari 0.7.x — the exact caution in this
> spec), whereas tabify satisfies every constraint AND guarantees state preservation by construction: it never
> reparents/rebuilds the method widget, so parameters + status markers survive untouched (the flagged main
> risk, eliminated rather than asserted-around). Preference `ui.results_dock_reflow` (`'tabify'` default,
> `'stack'` = today) is a MODE string so `'collapse'` can be added later; mount is idempotent + headless-safe +
> falls back to a plain mount if `tabify` is unavailable. Routed through all four results mounts (VPT, batch,
> cellular-object, IVF-droplet). `tests/test_dock_space.py` (`core`, 9 tests). Full core green (1759).
> **Follow-ons:** ✅ Qt-smoke test of the real tabbed mount (1.6.298 — a live `QMainWindow` verifies tabify
> genuinely tabs + method state survives, and collapse invokes real `resizeDocks`). ✅ `'collapse'` mode
> (1.6.298 — a real third mode via `QMainWindow.resizeDocks`, grows the results dock so the method panel
> shrinks while staying open/reversible/state-preserving). ✅ **Settings-UI exposure — DONE (1.6.299):**
> built the small preferences panel that was missing (Qt-free `utils/preferences.py` registry + thin
> `ui/preferences_dialog.py` + a '⚙ Preferences' menu action installed from `central_manager`, NOT the
> line-capped menu god-file). Results-dock placement (tabify/collapse/stack) is the second control there,
> alongside the interface level. **All three follow-ons are now complete.**
>
> **Note:** the spec's line references (`ui_modules.py:762…`, `vpt/results_dock.py:182`) are from the 1.6.281
> tree; the results mounts now live under `toolbox/` — the shared-helper fix is location-independent.

**Date:** 2026-07-23 · **Target tree:** 1.6.281+ · Verified against the 1.6.281 tree. Reported from the
GUI: when a brushable results dock mounts, it is appended **below** the method widget in the right-hand
dock area and gets almost no vertical space. On VPT — whose method panel is very tall — the results are
effectively invisible. The plots and linked table are the payoff of the whole brushing programme, and
right now the user has to scroll past a long parameter panel to find them.

## Verified state
Every dock goes to the same area with no space management:
```python
# vpt/results_dock.py:182
self._vpt_results_dock = self.viewer.window.add_dock_widget(
    splitter, name="VPT Results", area='right')
```
and the method widgets themselves mount the same way (`ui_modules.py:762, 1046, 1988, 2093, 2603, 2692,
2824, 3115, 3252` — all `add_dock_widget(..., area='right')`). Qt stacks same-area docks vertically and
splits the height between them; a tall method panel therefore starves whatever mounts afterwards.

`BrushableWorkspace` (`ui/brushable_workspace.py`) is the shared results container, so a fix there plus
the VPT dock covers the existing cases, and any future method that mounts results inherits it.

## The behaviour to implement
When a results dock mounts for the current method:

1. **Collapse the method widget** to its title bar (or a small fixed height), so the results dock gets
   the freed vertical space. The method panel is not *closed* — the user has finished configuring and
   pressed Run; its parameters are still there, one click away.
2. **Restore on demand** — clicking the collapsed method dock's title expands it again, and collapsing
   the results dock returns space to the method widget. This must be reversible and obvious.
3. **Remember the choice** per method via the general `user_settings` service (now shipped): a user who
   prefers the method panel to stay expanded gets that respected on the next run. Namespaced key, e.g.
   `ui.collapse_method_on_results`.
4. **Apply generally.** Implement once in the shared mount path — not per method. Every method that
   mounts a results dock (VPT today; cellular/batch workspaces; anything added later) gets the same
   behaviour without bespoke wiring. This is the same "fix the mechanism, not the instance" discipline
   used for the status markers.

### Alternative worth considering — tabify instead of stack
Qt supports `tabifyDockWidget`, which puts two docks in the same space as tabs rather than splitting the
height. That gives the results dock the **full** panel height and makes switching back to parameters a
single tab click — arguably better than collapsing, and it sidesteps the "how much height does each get"
question entirely.

**Recommendation:** implement collapse first (simpler, preserves the current mental model of stacked
panels), and evaluate tabify as a follow-on — or expose it as the `user_settings` choice
(`stack` / `collapse` / `tabify`). Do **not** build both behaviours at once.

## Constraints
- **Never lose user state.** Collapsing must not reset, rebuild, or clear the method widget's entered
  parameters — the field values and status markers must survive collapse/expand untouched. This is the
  main risk in the change.
- **Never trap the user.** A collapsed panel must always be re-openable, and the affordance must be
  visible (title bar remains, cursor/tooltip indicates it expands).
- **Don't fight napari.** Use napari's dock/Qt APIs rather than manually reparenting widgets; a
  hand-rolled layout will break on napari 0.7.x updates.
- **Headless-safe.** The logic must no-op cleanly when there is no Qt window (the mount helpers are
  exercised in tests without a viewer).

## Tests (Qt-smoke where a widget is needed; `core` for the policy)
- Mounting a results dock collapses the active method dock; the results dock's height increases.
- Expanding the method dock restores it; **its parameter values and field-status markers are unchanged**
  (the state-preservation test — this is the one that matters).
- The preference persists via `user_settings` and is honoured on the next mount; the default is
  documented.
- With the preference off, current stacking behaviour is unchanged (backward compatible).
- The mount path no-ops without a Qt window (headless).
- A second results dock mounting does not re-collapse an already-collapsed method dock (idempotent).

## Steps
1. Add the collapse/restore helper to the shared results-mount path (`brushable_workspace` +
   the VPT results dock's mount).
2. Wire the `user_settings` preference with a documented default.
3. Route VPT and the cellular/batch workspaces through the shared path.
4. Tests above.
5. Full `pytest -m core` green.
6. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (results docks now get room:
   the method panel collapses when analysis output mounts; preference remembered).

## Definition of done
- Mounting a results dock gives it usable vertical space by collapsing the method widget.
- Restore is one click; parameters and status markers survive collapse/expand intact.
- The behaviour is implemented once in the shared path and applies to every method that mounts results,
  present and future.
- The preference persists per user; default documented; opting out restores today's behaviour.
- Headless-safe; full `pytest -m core` green.

## Cautions
- **Parameter state must survive collapse.** If collapsing rebuilds the widget, the user loses entered
  values — that would be a worse bug than the one being fixed. Assert it.
- **Implement in the shared mount path**, not per method; a per-method fix guarantees the next method
  forgets it.
- **Use napari/Qt dock APIs**, not manual reparenting — the drag-drop and canvas work already showed how
  version-specific this layer is (napari 0.7.x).
- Pick collapse **or** tabify for the first increment; shipping both doubles the surface with no extra
  benefit.
- Default should favour visibility (collapse on), since the reported problem is that results are
  invisible — but make it opt-out, not mandatory.
