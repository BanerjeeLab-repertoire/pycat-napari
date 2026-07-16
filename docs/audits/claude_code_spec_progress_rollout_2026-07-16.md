# Claude Code spec — Progress-bar rollout to every widget that freezes on a big stack

## 🟡 STATUS — 9 of 14 sites wired, shipped in 1.6.81. 5 remain on a COUNTDOWN, each with a reason.
`pytest -m core`: **741 passed, 2 skipped** (was 718).

**The premise holds, and it was worth checking.** "Wire the callback and the bar moves" is only true
because `QProgressBar.setValue` calls `repaint()`, which is **synchronous** — measured at 50 paints
per 50 updates in a busy loop, against **0** for a control. A `QLabel` gives **0**: `setText` only
schedules an `update()` the blocked event loop never runs, so *a status label is not a progress
reporter here*, and the spec's suggestion to grep for "a status label" as the reporter would have
produced silent no-ops.

**The spec's table over-counts** (it counted mentions, not calls): the real totals are 3/3/2/2/2/1/1,
not 6/5/4/4/4/3/2. The *set* of widgets is right.

**`temperature_ui` is not the clean reference the spec says.** It is called "the ONE reference
implementation that does it right" with "do not change" — and it has an **unwired materialize of its
own** (`_get_stack`, a cached helper). It is right for its batch/export paths; this stack load was
missed. The ratchet found it on its first run. It is also not using `PhasedProgress` at all — it
passes raw `setValue` lambdas.

**Two things this pass found that the spec did not ask for:**
1. `frap_ui` materialised **every Image layer in the viewer** to test `ndim == 2`, discarding the
   stacks — decoding every open acquisition to answer a question the array already knew. Fixed by
   asking the shape first. A progress bar there would only have reported work that should not happen.
2. Wiring a section to a **sibling's** bar looks right and raises `NameError` on the first click. The
   repo's `test_no_undefined_names` caught it on `invitro_bf_ui._ivbf_focus_qc`. A bar is not in
   scope just because the section next door has one.

**The ratchet is a COUNTDOWN, not an allowlist** — and that mattered: the first draft used a
per-module excuse, and a mutation adding a *second* silent materialize to an already-excused module
**passed**. Counting the remaining sites per module catches it; the number only goes down.

**Remaining (the 5), each needing a bar ADDED — a UI change, not the one-line wiring this pass was:**
`condensate_physics_ui._on_fusion`, `invitro_bf_ui._ivbf_focus_qc` (no bar of their own, siblings
have one); `data_qc_ui`, `fusion_ui` (construct no bar at all); `temperature_ui._get_stack` (shared
cached helper — no single owning bar; wiring it means every caller passing a reporter down).

**And the honest limitation, as the spec itself insists:** the work is still synchronous. This makes
the freeze **visible**, it does not remove it. True responsiveness needs worker-thread
materialization — the same change the session loader wants, and worth doing once for both.

**Date:** 2026-07-16 · **Target tree:** 1.6.70 · Verified against the 1.6.70 tree. Contained UX pass:
wire the ALREADY-SHIPPED materialization progress into every widget that currently materializes a
stack synchronously and shows a frozen 0%. Touches `*_ui.py` widgets only — no `file_io.py`, no
science, no collision with the loader/brushing work.

## The problem (verified)
`materialize_stack(stack_like, progress_callback=...)` already invokes `progress_callback(done,
total)` **per frame** (`stack_access.py:35–41`), and `PhasedProgress` (`ui_utils.py:512`) already maps
a phase onto a 0→100% bar. This was applied to VPT bead detection and to `temperature_ui` — **the ONE
reference implementation that does it right** (`temperature_ui.py:844,911` pass a `progress_callback`
lambda into the work). Everywhere else, a widget calls bare `materialize_stack(layer.data)` on a big
lazy stack and the UI **freezes at 0%** while every frame decodes, with no indication it's working.

Verified freeze sites — widgets that call `materialize_stack`/`as_full_array`/`require_stack`
SYNCHRONOUSLY with **zero** `progress_callback` today:

| widget | materialize calls | has progress? |
|---|---|---|
| `frap_ui.py` | 6 | no |
| `invitro_fluor_ui.py` | 5 | no |
| `brightfield_ui.py` | 4 | no |
| `invitro_bf_ui.py` | 4 | no |
| `condensate_physics_ui.py` | 4 | no |
| `data_qc_ui.py` | 3 | no |
| `fusion_ui.py` | 2 | no |
| `temperature_ui.py` | 2 | **YES — the reference, do not change** |

(Note: `data_qc_ui` materializes 3× — the roadmap mislabelled it a "zero-bar" UI; it's actually a
materialize-freeze one and belongs in this group.)

## The pattern to replicate (copy temperature_ui, don't invent)
`temperature_ui.py` already shows the exact shape: build a `PhasedProgress` tied to the widget's
existing progress bar / status, and pass its `.callback` as `progress_callback=` into the
materialization. Read `temperature_ui.py:844` and `ui_utils.py:512` (`PhasedProgress`) first, then
apply the SAME wiring at each freeze site:

```python
# BEFORE (freezes at 0% on a big stack):
stack = materialize_stack(layer.data)

# AFTER (shows "Materializing…" 0→100% as frames decode):
pp = PhasedProgress(<the widget's progress reporter>, phases=[("Materializing frames", 1.0)])
stack = materialize_stack(layer.data, progress_callback=pp.callback)
```

Where a materialization is immediately followed by long analysis work (e.g. a per-frame worker), use
TWO phases so the ONE bar spans both (the pattern PhasedProgress exists for — materialize 0→X%, work
X→100%), exactly as the VPT detection and temperature paths do. Where materialization is the only slow
part, one phase is enough.

## Scope — do this per-widget, NOT as a blanket regex
Each widget reports progress through its own mechanism (a `QProgressBar`, a status label, a napari
`progress`), so the `PhasedProgress` construction differs per widget. Do them one at a time,
compiling after each, in this order (most materialize calls first):
1. `frap_ui.py` (6) — several materialize sites (prebleach stack, main stack, per-layer loop at
   ~443/493/597); wrap each user-triggered one. The per-layer loop at ~443 should show progress across
   layers too.
2. `invitro_fluor_ui.py` (5)
3. `brightfield_ui.py` (4)
4. `invitro_bf_ui.py` (4)
5. `condensate_physics_ui.py` (4)
6. `data_qc_ui.py` (3)
7. `fusion_ui.py` (2)

For each: find the widget's existing progress reporter (grep the file for `QProgressBar` /
`setValue` / `progress` / a status label). If the widget has NO progress UI element at all, add a
minimal indeterminate→determinate bar in the same style temperature_ui uses — don't invent a new
progress widget class; reuse `PhasedProgress` + whatever bar the sibling UIs use.

## Guard test
Add `tests/test_progress_rollout.py` (mark `core`, static/AST — no Qt): assert that every `*_ui.py`
which calls `materialize_stack`/`as_full_array` in a user-triggered handler passes a
`progress_callback=` (or wraps via `PhasedProgress`). This RATCHETS the rollout — a future widget that
materializes a stack without progress fails the test. Model it on how `test_silent_fallbacks.py`
statically checks UI-wide contracts (e.g. `test_every_UI_that_reports_a_LENGTH_has_the_pixel_size_gate`).
Allow an explicit opt-out set for any materialize call that is provably tiny/non-lazy, but default to
requiring the callback.

## Steps
1. Read `temperature_ui.py` (reference) + `PhasedProgress` in `ui_utils.py` + `materialize_stack` in
   `stack_access.py` — confirm the `callback(done, total)` signature.
2. Wire the 7 widgets in order, compiling after each. Preserve behaviour — the ONLY change is passing
   the callback; no analysis logic changes.
3. Add `test_progress_rollout.py` (the ratchet).
4. Full `pytest -m core` green — especially the complexity budget (these edits ADD a few lines per
   call site; if any handler crosses 120 lines, extract the materialize+progress into a small helper
   rather than bumping the ceiling — same rule as the ratchet fix).
5. Update `roadmap.rst`: mark the "progress-bar per-method rollout" item resolved for these widgets.
6. Ship: own version + PyPI push + commit (EXPLICIT filenames: the 7 `*_ui.py` + the test + roadmap +
   pyproject + CHANGELOG) + CHANGELOG entry listing which widgets got progress.

## Definition of done
- Every widget in the table (except temperature_ui) shows a moving "Materializing…" bar instead of a
  frozen 0% when materializing a large stack.
- The two-phase pattern is used where materialization is followed by long work, so the bar doesn't hit
  100% then restart.
- `test_progress_rollout.py` passes and would fail a future no-progress materialize.
- Full `pytest -m core` green; no complexity-ratchet regression.
- Behaviour-preserving — only progress reporting added.

## Cautions
- **Copy temperature_ui's pattern**; don't design a new progress abstraction. `PhasedProgress` +
  `materialize_stack(progress_callback=...)` is the whole toolkit.
- Per-widget judgment on the progress reporter — do NOT blanket-regex `materialize_stack(` → add arg,
  because the `PhasedProgress` target differs and some materialize calls are on tiny (already-2D)
  arrays where a bar is pointless. Wrap the ones on lazy STACKS in user-triggered handlers.
- Don't touch `temperature_ui` (already correct) or the science modules (this is a UI-reporting
  change only).
- Watch the complexity budget — extract a helper if a handler would cross 120 lines; never raise the
  ceiling.
- These are synchronous materializations on the Qt main thread — this spec makes the freeze VISIBLE
  (a moving bar), it does NOT move the work off-thread. True non-blocking (worker-thread
  materialization) is a bigger, separate change; note it in the CHANGELOG as the honest limitation so
  nobody thinks the UI is now fully responsive during load.
