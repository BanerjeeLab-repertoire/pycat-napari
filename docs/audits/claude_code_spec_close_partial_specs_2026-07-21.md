# Claude Code spec — Close the three partially-landed specs

> **✅ STATUS — ALL THREE PARTS DONE. Part A (backend_parity 2&3, 1.6.274/1.6.277); Part B (the batch_step
> guard, test-only); Part C (the stack-access axis, shipped 1.6.281).**
>
> **Part C — the stack-access axis — DONE.** The `np.asarray(layer.data)` → frame-0 collapse guard only
> caught variables literally named `…layer…`; four real frame-0 bugs held the layer in `active`/`lmask` and
> slipped through — brightfield best-slice (rejected a correct lazy stack as "needs a 3D stack", the N&B
> class), CLEAN spot detection, the `ui_modules` flatfield corrector, and the paired mask in
> `general_image_tools`. All four now route through `materialize_stack(active.data, dtype=None)` /
> `extract_2d_plane` — **output-identical on eager 2D/3D** (same array `np.asarray` returned) and correct on a
> lazy stack. The guard is **broadened** to flag `np.asarray(<var>.data)` for `active`/`image`/`mask`-named
> vars, so the bug class is caught regardless of variable name; `ui_diagnostics_mixin` (a diagnostic dump)
> stays allowlisted. The remaining `asarray(<x>.data)` sites are genuinely 2D (FRAP force/distance traces,
> etc.) and correct as-is. Full core green (1712).
>
> **Part B — the batch_step guard — DONE.** New `tests/test_no_silent_batch_step_swallowing.py` (`core`): an
> AST ratchet, sibling to the write-swallow guard, that flags a broad handler directly inside a MULTI-FILE
> batch loop (`for … in self.files` / `tiffs` / `image_paths` …) which neither re-raises nor records the
> item's outcome — a per-item result/marker (`✗`/`⚠`), a status flag, a `BatchStepResult`, or an assignment
> into the returned row (`row['status'] = …`). Budget pinned at **0**: the batch-item loops that exist
> (`BatchWorker.run` over `self.files`; `temperature_tools` over its tiffs) already record every failure, so
> a NEW silent drop — the 93-of-100 cohort that looks complete — is what it catches. No offenders to convert;
> the guard pins the compliant state. (`test_batch_step_visibility.py` still guards the concrete
> `BatchWorker.run` fix; this generalises the rule to any file/image batch loop.)

**Date:** 2026-07-21 · **Target tree:** 1.6.269 · Verified against the 1.6.269 tree. Three specs are
**half done** — each shipped its first part and stalled. Finishing them is cheap, and a half-finished
guard is worse than none because it implies coverage that isn't there. Independent parts; ship each
separately.

---

## Part A — backend_parity Parts 2 & 3
**Landed (1.6.260):** Part 1 — a seaborn `hue` split gets a verified per-artist entity mapping, with the
point-count check and the safe refusal fallback intact.

**Not landed:** Parts 2 and 3. Verified: no size threshold or default-backend selection exists in
`plot_backends.py`.

### Part 2 — PyQtGraph as the default for large interactive scatters
- Add a **size threshold** (start ~5,000 points, configurable via `user_settings` — which now exists).
  Above it, interactive scatter defaults to the PyQtGraph backend; below it, matplotlib.
- **Matplotlib remains the canonical publication backend.** The switch is interactive-only; export and
  figure refinement always route through matplotlib and the canonical `FigureSpec` (now complete after
  the publication-features work).
- The choice is **explicit and overridable**, and the chosen backend is **recorded** on the view for
  provenance and debugging.
- PyQtGraph's interaction tests are GUI-gated, so add what can run headlessly (data→coordinate mapping,
  `SelectionService` wiring) and mark true interaction tests Qt-smoke.

### Part 3 — scope Plotly honestly
- Detect QtWebEngine availability. **With** it: click-to-napari bridging enabled. **Without** it:
  hover/identity only — and say so in the UI rather than offering a dead button.
- Plotly stays an **optional** backend; matplotlib (publication) and PyQtGraph (interactive) are the two
  first-class routes.

### Tests
- Above threshold → PyQtGraph selected; below → matplotlib; override respected; selection recorded.
- Export routes through matplotlib regardless of the interactive backend.
- QtWebEngine absent → Plotly reports hover-only, no dead affordance; present (mockable) → click enabled.
- Part 1's seaborn behaviour unchanged.

---

## Part B — exception_context: the batch_step category
**Landed (1.6.259):** Part 2 — a swallowed **write** now surfaces rather than looking like success.

**Not landed:** the **batch_step** category. Verified `batch_step` appears only twice in
`test_exception_budget.py` — the category is named but not enforced.

### The rule
A broad handler around a **single batch item** must record that item's status as `failed`/`skipped` —
never let it vanish. **A batch that processes 100 images and silently drops 7 yields a cohort of 93
that looks complete.** That is a silent scientific corruption with no wrong number anywhere, which is
why the result-swallowing guard doesn't catch it.

- Compose with **`BatchStepResult(status=...)`** — the typed result models landed, so use that status
  field rather than inventing a parallel scheme.
- Extend the guard test to flag a broad handler in a batch-item path that neither re-raises nor records
  a failed status.
- Conservative and ratchet-style, matching the existing guards.
- Convert the real offenders; lower the affected package ratchets by the number converted.

### Tests
- A simulated item failure produces a `failed` status entry, and the batch report counts it.
- The guard flags a deliberately-introduced silent drop.
- A successful batch is unchanged (cry-wolf).
- Write-category behaviour from 1.6.259 unchanged.

---

## Part C — redundancy_consolidation: the stack-access axis
**Landed:** the pixel-size accessor consolidation (1.6.212, with a single-accessor guard) and the UI
background-mechanics axis (1.6.258).

**Not landed:** the stack-access axis. Verified **27 `np.asarray(<x>.data)` sites remain**.

### The work
Route stack-consuming sites through `materialize_stack` (or explicit `iter_frames`) so there is one
stack-access path with one defused behaviour — this is the frame-0-collapse landmine.

- **Per-site judgement, not a blind sweep.** Most sites are 2D-only where `asarray` is correct; only
  genuine lazy-time-series consumers need `materialize_stack`. For a 2D site the two return the same
  array, so routing through the helper is output-identical and removes the footgun.
- **Output-identical is the law** (the redundancy spec's governing rule): characterize before and after;
  if a site's output differs, that site was a real bug — report it as a **finding**, don't silently
  "fix" it.
- The existing `test_silent_fallbacks::test_the_stack_helpers_have_ONE_implementation` and
  `test_time_series_analyses_do_not_collapse_a_lazy_stack_to_frame_zero` are the net.

### Tests
- Each converted site: output identical before/after (2D sites), or the frame-0 bug demonstrably fixed
  (lazy sites) — stated per site.
- The remaining-site count drops by the number converted; a guard prevents new bare
  `np.asarray(<layer>.data)` on a stack-consuming path.
- Existing lazy-stack tests pass unmodified.

---

## Steps
1. **Part A**: threshold + backend selection + recording; Plotly scoping; tests. Ship.
2. **Part B**: batch_step guard + conversions via `BatchStepResult`; lower ratchets; tests. Ship.
3. **Part C**: per-site stack-access conversion with characterization; tests. Ship.
Each part is its own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- Large interactive scatters default to PyQtGraph, publication stays matplotlib, Plotly honestly scoped.
- A dropped batch item is always visible as a `failed`/`skipped` status; the guard enforces it.
- Stack access routes through one helper; no output changes; the frame-0 footgun is removed from the
  converted sites.
- All existing tests pass unmodified.

## Cautions
- **Part A:** matplotlib stays the publication path — the default switch is interactive-only. No dead
  Plotly affordances.
- **Part B:** a silently dropped batch item corrupts a cohort with no wrong number — that is exactly why
  it needs its own rule. Use `BatchStepResult`, don't invent a status scheme.
- **Part C:** per-site judgement, output-identical, and a differing output is a **finding to report**,
  not something to quietly resolve.
- These are three independent parts — ship separately, don't bundle.
