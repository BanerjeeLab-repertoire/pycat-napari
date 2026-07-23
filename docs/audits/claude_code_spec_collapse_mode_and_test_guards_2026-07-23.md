# Claude Code spec — Implement `collapse` reflow mode, and guard against spec-drift test failures

> **● STATUS — DONE.** Part 1 (`collapse` mode) was already implemented in 1.6.298 (stacked mount +
> `resizeDocks`, headless-safe, failure-tolerant, never rebuilds the method widget) and hardened in 1.6.303
> (the qtpy-absent no-op fix — `_apply_collapse` falls back to the raw `Qt.Vertical` value 2 in the minimal
> core lane). Parts 2–3 + the discipline shipped 1.6.304: **Guard A** (`test_dock_space::test_guard_A_*` — every
> `VALID_MODES` entry is settable, reachable from `plan_results_mount`, and equals the preference-registry
> options exactly), **Guard B** (`test_ci_dependencies::test_no_test_exercises_an_option_the_module_does_not_declare`
> — an AST scan that flags a string literal passed to a `VALID_*`-backed option setter unless it is declared
> or inside `with pytest.raises(...)`; verified to bite on a synthetic violation and exempt the rejection
> test), a never-lose-the-dock-on-resize-failure test, and a DEV_NOTES §8 descoping-discipline entry. Full core
> green (1816).

**Date:** 2026-07-23 · **Target tree:** 1.6.297+ · Verified against the 1.6.297 tree and the CI log.
One failing test, plus two guards for the *class* of failure it belongs to.

---

## Part 1 — The failure: `collapse` is tested but not implemented

```
tests/test_dock_space.py::test_collapse_mode_mounts_stacked_then_grows_the_results_dock FAILED
    assert len(win._qt_window.resizes) == 1
E   assert 0 == 1
```

**Verified state at 1.6.297** — `utils/dock_space.py` declares:
```python
DEFAULT_MODE = 'tabify'
VALID_MODES = ('tabify', 'stack')
```
and its module docstring says:

> Chosen tabify over collapse: Qt has no native "collapse a dock to its title bar" primitive… **The
> preference is a MODE string so `'collapse'` can be added later.**

So `collapse` was deliberately deferred, and the tests for it were written anyway. `set_reflow_mode`
raises `ValueError` on any mode outside `VALID_MODES`, and `plan_results_mount` has no `collapse`
branch — hence zero `resizeDocks` calls.

**Decision: `collapse` IS supported.** Implement it rather than deleting the tests.

### What `collapse` must do
Per the failing test, collapse mounts **stacked** (`tabify=False`) and then **grows the results dock**
via `resizeDocks`, so the tall method panel shrinks to give it room:

1. Add `'collapse'` to `VALID_MODES`.
2. `plan_results_mount` returns `'collapse'` when the mode is collapse and the same safety conditions
   that gate tabify hold (a results widget exists, a method panel exists to shrink, not already
   reflowed) — otherwise `'stack'`, matching the existing fallbacks exactly.
3. `add_results_dock` for `'collapse'`: mount with `tabify=False`, then call `resizeDocks` on the Qt
   window to give the results dock the larger share.
4. **Headless-safe** — no Qt window means no resize; the mount still succeeds and the helper returns the
   dock (the existing `test_collapse_mode_is_a_clean_noop_without_a_qt_window` already passes and must
   continue to).
5. **Never lose the dock** — if `resizeDocks` raises (older napari, Qt hiccup), the dock stays mounted
   stacked. Same guarantee the tabify path already gives.
6. **Never rebuild the method widget.** Collapse resizes the *dock area*; it must not reparent, rebuild,
   or clear the method panel — its entered values and field-status markers survive untouched. This is
   the constraint the whole module was designed around.

Update the module docstring: it currently says collapse was *not* chosen and could be added later. It
now exists — say what it does and why a user might prefer it (keeps both panels visible at once, at the
cost of height, versus tabify's full-height-but-one-at-a-time).

---

## Part 2 — Guard A: the declared option set must match what is implemented and tested

**Why:** the failure is a *mode declared in tests but absent from `VALID_MODES`*. Nothing checked that
the option vocabulary agreed across the three places it appears (module constant, preference registry,
tests).

You already have the pattern — `test_preferences.py::test_options_are_stable_and_cover_each_owning_modules_valid_set`
asserts the preference registry's options match each owning module's valid set. Extend it to close the
loop:

- Every mode in `VALID_MODES` is reachable through `set_reflow_mode` without raising.
- Every mode in `VALID_MODES` has a `plan_results_mount` branch that can return it (no declared-but-dead
  option).
- The preference registry's options for `ui.results_dock_reflow` equal `VALID_MODES` exactly.

A mode that is declared but unimplemented, or implemented but undeclared, fails the guard.

---

## Part 3 — Guard B: a test may not assert behaviour the module does not declare

**The recurring pattern.** Three consecutive CI failures were **test defects, not product bugs**:

| failure | cause |
|---|---|
| `test_unmixing::negative_fraction` | the test's own numeric assumption was wrong |
| 4× `qtbot` collection errors | Qt-requiring tests marked `core`, in a lane without pytest-qt |
| `test_dock_space::collapse_mode` | tested a mode the module never declared |

The qtbot one already produced the right kind of fix — `test_ci_dependencies` gained
`test_no_core_marked_test_requests_a_qt_fixture`, a guard for the *class*. Do the same here.

**The guard:** for modules that expose a declared option vocabulary (a `VALID_*` tuple or an enum), a
test that exercises an option **not in that vocabulary** fails. Concretely: scan test files for string
literals passed to a `set_*_mode`-style setter and assert each is in the owning module's declared set.

Keep it narrow and mechanical — the aim is to catch "tests a rejected/deferred alternative", not to
police test content generally. Scope it to the option-setter functions that already have a `VALID_*`
constant; that is a small, well-defined surface and covers the failure mode observed.

### The accompanying discipline (docs, not code)
Add to `DEV_NOTES.md`: **when a spec descopes an alternative, say so explicitly in the spec's test
section** ("do not write tests for X"). This failure originated in a spec that named tabify as the
decision and collapse as a possible follow-on — the implementation honoured that, the tests did not.

---

## Tests
- `collapse` mounts stacked and calls `resizeDocks` once (the currently-failing test passes).
- `collapse` is a clean no-op without a Qt window (existing test still passes).
- A `resizeDocks` failure leaves the dock mounted (never-lose-the-dock).
- The method widget's attributes and field-status markers are unchanged after a collapse mount.
- `tabify` and `stack` behaviour is **unchanged** (regression — the existing dock_space tests pass
  unmodified).
- Guard A: every `VALID_MODES` entry is settable and reachable from `plan_results_mount`; the preference
  registry options equal `VALID_MODES`.
- Guard B: a test exercising an undeclared option is flagged; the current suite passes.

## Steps
1. Add `'collapse'` to `VALID_MODES`; add the `plan_results_mount` branch with the same safety gates.
2. Implement the collapse path in `add_results_dock` (stacked mount + `resizeDocks`, failure-tolerant).
3. Update the module docstring to describe collapse as implemented.
4. Guard A in `test_preferences` (or alongside the dock-space tests).
5. Guard B as an AST/scan test over the option-setter surface.
6. DEV_NOTES entry on descoping discipline.
7. Full `pytest -m core` green.
8. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- `collapse` is a real, declared, tested mode; the failing test passes.
- `tabify`/`stack` unchanged; the method widget is never rebuilt by a collapse mount.
- Guard A prevents a declared-but-unimplemented (or implemented-but-undeclared) option.
- Guard B prevents a test asserting an option the module does not declare.
- Full `pytest -m core` green.

## Cautions
- **Collapse resizes the dock area, never the widget.** Reparenting or rebuilding the method panel would
  lose the user's entered parameters — the exact failure the module was built to avoid.
- **Keep the existing safety gates.** Collapse must fall back to `'stack'` under the same conditions
  tabify does (no widget, no method panel, already reflowed) — do not invent a different set.
- **Failure-tolerant.** A `resizeDocks` that raises must not lose the dock.
- Guard B should stay **narrow and mechanical** — scoped to option setters with a declared `VALID_*`
  set. A broad "tests must match specs" check would produce false positives and get disabled.
- Neither guard is a substitute for the descoping discipline in the spec itself; the guards catch what
  slips through.
