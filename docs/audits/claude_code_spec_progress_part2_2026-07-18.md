# Claude Code spec — Progress, part 2: the ANALYSIS half (tool-level progress)

> **✅ STATUS — DONE.** Parts A/B/D/E shipped in **1.6.132** (tool `progress_callback` for the two
> zero-bar widgets + `QProgressBar` wiring + sweep + ratchet), then upgraded to the off-thread modal
> runner in **1.6.140** (which superseded the inline bars — responsive, not merely visible). **Part C**
> shipped in **1.6.171**: `cell_analysis_func` / `puncta_analysis_func` gain a batched `progress_callback`
> (`None` = no-op, result byte-identical — pinned), and `run_cell_analysis_func` / `run_puncta_analysis_func`
> route their compute through `run_with_progress` (the modal off-thread runner) so the countable per-cell
> loop drives a determinate bar. `test_progress_analysis_half.py` ratchets both the callback and the
> routing; `test_analysis_progress_callback.py` is the functional proof. Honest limit unchanged: the wait
> is VISIBLE and the window responsive, not shorter — the modal-bar advance needs an in-app glance.

**Date:** 2026-07-18 · **Target tree:** 1.6.121 · Verified against the 1.6.121 tree. The
materialization half of the progress work shipped (1.6.81/82, all 14 sites, ratcheted). This is the
**different problem** the roadmap parked: widgets whose slow work is *the analysis itself*, which
therefore need a `progress_callback` **on the tool function**, not just UI wiring. Touches
`contrast_cascade_tools.py`/`_ui.py`, `fd_curve_tools.py`/`_ui.py`, and core runners in `ui_modules`.
Not `file_io.py`.

## The verified gap
| widget | `QProgressBar` | `materialize_stack` | `progress_callback` | tool-side progress |
|---|---|---|---|---|
| `contrast_cascade_ui` | **0** | 0 | 0 | `contrast_cascade_tools`: **0** |
| `fd_curve_ui` | **0** | 0 | 0 | `fd_curve_tools`: **0** |

Neither materializes a stack, so the 1.6.81/82 rollout never touched them and
`tests/test_progress_rollout.py`'s ratchet does not cover them. They do slow work with **nothing on
screen**. Because the slowness is the analysis, adding a bar alone would give a bar with nothing to
drive it — the tool functions must report progress first.

## The Qt fact that constrains the design (measured, from the roadmap)
`QProgressBar.setValue` calls `repaint()` and is **synchronous** — a bar driven from a busy
main-thread loop genuinely moves (50 paints per 50 updates). A `QLabel` does **not**: `setText` only
schedules an `update()` that a blocked event loop never runs. **A status label is not a progress
reporter.** So: use a real `QProgressBar`, and do not "fix" a frozen UI by adding text.

## Part A — give the tool functions a `progress_callback`
For the slow entry points in `contrast_cascade_tools.py` and `fd_curve_tools.py`:
- add an optional `progress_callback=None` parameter, called as `progress_callback(done, total)` at a
  natural loop boundary (per cascade stage / per curve / per segment) — the SAME signature
  `materialize_stack` already uses, so `PhasedProgress` composes without adaptation;
- call it at a sane granularity: often enough to move, rarely enough not to dominate runtime (a
  repaint per iteration on a 10k-iteration loop is itself a slowdown — batch to ~100 updates total);
- `progress_callback=None` must be a complete no-op so headless/batch callers are unaffected.

**First, check the shape of the work.** If a function's cost is one opaque non-iterative call
(e.g. a single vectorised transform), a determinate bar is not honest — say so and use an
indeterminate/busy indicator for that step rather than faking progress.

## Part B — wire the two UIs
Add a `QProgressBar` to each widget and drive it from the tool callback via the existing
`PhasedProgress` (`ui_utils`), mirroring the pattern the 14 materialization sites now use. Where a
widget does materialization *and* analysis, use two phases on ONE bar (the reason `PhasedProgress`
exists) so the bar doesn't reach 100% twice.

## Part C — determinate bars in the core cell/condensate runners (`ui_modules`)
The roadmap flags these as under-covered: per-cell / per-object loops make progress genuinely
measurable, so replace indeterminate/absent feedback with determinate bars where a loop count exists.
Scope this to runners with a real countable loop — do not add bars to steps whose cost is one call.

## Part D — sweep for fake/indeterminate bars that could be determinate
Grep for indeterminate bars (`setRange(0, 0)`) and status-label-as-progress usage. For each: if a
countable loop is available, make it determinate; if not, leave it indeterminate but ensure it is a
`QProgressBar` (which repaints) and not a `QLabel` (which does not). Record findings in the CHANGELOG
so the sweep isn't repeated.

## Part E — extend the ratchet
`tests/test_progress_rollout.py` currently ratchets *materialization* sites at zero. Extend it (or add
a sibling test) to cover the analysis half: assert that the slow tool entry points touched here accept
`progress_callback`, and that the two widgets construct a `QProgressBar`. Ratchet so a future
zero-feedback slow widget fails.

## Steps
1. `contrast_cascade_tools` + `fd_curve_tools`: `progress_callback` at loop boundaries (batched).
2. `contrast_cascade_ui` + `fd_curve_ui`: `QProgressBar` + `PhasedProgress` wiring.
3. Determinate bars in the countable-loop core runners in `ui_modules`.
4. Sweep + fix fake/label-based progress; note results.
5. Extend `test_progress_rollout.py` to ratchet the analysis half.
6. Full `pytest -m core` green (complexity budget — extract helpers, don't raise the ceiling).
7. Ship: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (progress part 2: the
   analysis half — tool-level `progress_callback`, the two zero-bar widgets, determinate core runners).

## Definition of done
- `contrast_cascade_ui` and `fd_curve_ui` show a moving `QProgressBar` during their slow work, driven
  by a real tool-side callback.
- Slow tool entry points accept `progress_callback(done, total)`; `None` is a no-op.
- Countable-loop core runners show determinate progress.
- No `QLabel` is used as a progress reporter in a blocking path.
- The ratchet covers the analysis half.
- Full `pytest -m core` green.

## Cautions
- **A status label is not a progress reporter** — measured. Use `QProgressBar`.
- Don't fake determinate progress for non-iterative work; an honest indeterminate indicator is better
  than a bar that lies about how far along it is.
- Batch callback invocations (~100 total) — a repaint per iteration can dominate the runtime it's
  reporting.
- `progress_callback=None` must not change behaviour for headless/batch callers.
- Same honest limit as part 1: this makes the wait VISIBLE, not shorter. Off-thread execution is a
  separate, larger change — say so in the CHANGELOG.
