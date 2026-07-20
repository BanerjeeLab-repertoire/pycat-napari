# Claude Code spec — Typed failures where it matters + one operation runner

> **✅ STATUS — DONE, shipped in 1.6.139 + 1.6.140** (stamped 2026-07-20 from a CHANGELOG cross-reference). Part 1 (typed errors + exception ratchet + calibration conversions) and Part 2 (shared operation runner) 1.6.139; 2-widget adoption 1.6.140.

**Date:** 2026-07-19 · **Target tree:** 1.6.133 · Verified against the 1.6.133 tree. Addresses the two
reliability findings the external audit flagged as unchanged across revisions: broad exception
handling, and per-widget background execution. Both are scoped as **ratchets and one shared
mechanism** — not a sweep.

## Part 1 — a targeted exception ratchet (not a purge)
Verified counts of `except Exception` by package:

| area | count |
|---|---:|
| `file_io/` | **284** |
| `utils/` | **117** |
| `batch_processor.py` | 15 |
| `batch_step_registry.py` | 5 |
| `data/` | 3 |
| `navigator/` | 1 |

~450 of these are followed by a bare `pass`. The audit is right that a general purge is the wrong
move — many are legitimate (GUI teardown, optional backends, metadata probing). Its recommendation is
the right one: **ratchet the highest-risk packages and require new handlers to be justified.**

Note the shape of the data: `navigator/` (1) and `data/` (3) are already clean — the new code is
disciplined. The concentration is in `file_io/` and `utils/`, the older centres.

### The mechanism
1. **A small typed-error module** (`utils/errors.py`) with the failure classes that actually recur in
   this codebase — do not invent the audit's full list speculatively. Start with what the code already
   distinguishes in comments and messages:
   `UnsupportedFormatError`, `MetadataUnavailableError`, `InvalidCalibrationError`,
   `ScientificAssumptionError`, `OptionalDependencyError`, `LayerResolutionError`. All deriving from a
   `PyCATError` base so a caller can catch the family.
2. **A per-package ratchet test** (`tests/test_exception_budget.py`), modelled on
   `test_complexity_budget.py`'s `does not GROW` idiom: a count per package, set at today's values,
   that may only decrease. This immediately stops growth at zero refactoring cost — the same principle
   that made the complexity ratchet effective.
3. **An annotation escape hatch:** a broad handler carrying an explanatory comment marker
   (e.g. `# broad-ok: <reason>`) is excluded from the count. This makes the *deliberate* ones explicit
   and the *lazy* ones visible, which is the actual goal.
4. **Convert opportunistically, in the highest-risk paths only** — scientific gates, calibration,
   batch replay, session persistence. A handler that swallows a *scientific* failure is a different
   category from one that guards a Qt teardown, and only the former is urgent. `except: pass` around a
   scientific computation should become a typed raise or an explicit, logged fallback.

**Do not attempt to reduce 284 handlers in one pass.** Ratchet first; convert the scientific ones;
leave the GUI-teardown ones annotated.

## Part 2 — one operation runner (replace per-widget threading)
The audit's point is precise: *"visible progress ≠ responsive UI"*, and *"otherwise every widget will
continue implementing this independently."* Progress part 1 and 2 made waits visible; several analyses
still run on the Qt thread, and the off-thread work that does exist (session load, stack decode, scene
switch) was implemented per-site.

### The mechanism — `utils/operation_runner.py`
One runner every widget uses:
```python
runner.execute(
    fn, *args,
    progress=...,        # forwards the existing progress_callback(done, total) contract
    on_result=...,       # marshalled to the main thread
    on_error=...,        # typed error transport (Part 1)
    cancellation=...,    # cooperative cancel token
    generation=...,      # stale-result suppression
)
```
Responsibilities, standardized once:
- **Worker-thread policy** — reuse the existing `qt_worker` machinery (`tests/test_qt_worker.py`
  exists) rather than introducing a second threading approach.
- **Main-thread marshalling** — napari layer creation and Qt widget updates must happen on the main
  thread; the worker returns data, the runner marshals.
- **Stale-result suppression** — a generation counter so a slow result cannot overwrite a newer
  request. This is the same hazard already handled in the selection deferred lane and the scene
  switcher; centralize it rather than re-deriving it a fourth time.
- **Cancellation** — cooperative, checked at the same boundaries the progress callback fires.
- **Error transport** — typed errors from Part 1 surface to the UI with a stated cause.

### Adoption — two widgets, then stop
Convert **two** existing slow paths as proof (the two zero-bar widgets from progress part 2 are the
natural candidates, since their tool functions already accept `progress_callback`). Do not convert
everything in this spec — prove the runner, then migrate incrementally.

## Tests
- Ratchet: counts per package do not grow; an annotated handler is excluded; the annotation must carry
  a non-empty reason.
- Typed errors: a scientific gate raises `ScientificAssumptionError`, not a bare `Exception`; the
  message names the assumption.
- Runner: result marshalling happens on the main thread (assert via a recorded thread id); a stale
  generation's result is discarded; cancellation stops the work; a typed error reaches `on_error`;
  `progress` forwards the existing `(done, total)` contract unchanged.

## Steps
1. `utils/errors.py` — the six typed errors + `PyCATError` base.
2. `tests/test_exception_budget.py` — per-package ratchet at today's counts + the annotation escape.
3. Convert scientific-path handlers in `file_io` and the calibration/gate paths to typed raises;
   annotate the deliberate GUI ones.
4. `utils/operation_runner.py` on top of the existing `qt_worker`.
5. Adopt in two widgets; verify with the runner tests.
6. Full `pytest -m core` green.
7. Ship: own version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (typed failures + the
   exception ratchet; one operation runner replacing per-widget threading, adopted in two widgets).

## Definition of done
- `PyCATError` family exists and is raised in the scientific paths converted.
- Per-package exception ratchet in place at today's counts; annotated handlers excluded with reasons.
- One `operation_runner` provides worker policy, marshalling, cancellation, stale-suppression, and
  typed error transport.
- Two widgets use it; their results are marshalled and their stale results discarded.
- Full `pytest -m core` green.

## Cautions
- **Ratchet before converting.** Stopping growth is most of the value and costs nothing.
- Do not purge broad handlers wholesale — GUI teardown and optional-backend probes legitimately need
  them. The annotation makes intent explicit; that is the goal, not a lower number for its own sake.
- Reuse `qt_worker`; a second threading mechanism would be exactly the duplication this spec removes.
- Layer creation and widget updates stay on the main thread — getting this wrong trades a freeze for a
  crash.
- Adopt in two widgets only. A mass migration in the same version makes any regression
  un-bisectable.
