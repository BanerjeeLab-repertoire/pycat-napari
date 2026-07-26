# Claude Code spec — Exception handler classification by context category

> **✅ Part 1 categorization sweep COMPLETE (git-only). Every `# broad-ok:` in the codebase now names WHAT it
> guards — 0 uncategorized codebase-wide.** Breakdown: `optional_probe` 158, `ui_cleanup` 100, `scientific_result`
> 27, `write` 13, `batch_step` 5. All per-package budgets are 0 (and unlisted packages have implicit 0), so a new
> *plain* broad-ok anywhere now fails the ratchet — the category is effectively mandatory. Increments 1–2 + the
> three swallow guards (scientific_result / write / batch_step) remain in force. **Remaining (separate):
> `BatchStepResult` adoption for the broader batch-step sweep (Part 3 batch half; the concrete `BatchWorker.run`
> offender is already guarded by `test_batch_step_visibility.py`).**
>
> **Part 1 (cont.) — `file_io` DONE, sweep CLOSED (comment/test-only, git-only, no bump).** The last package, all
> 64 categorized to 0: the metadata / format-read / Qt-layer-inspection / state-restore / migration / discovery
> probes (honest None / fallback / surfaced) → `optional_probe`; the CZI seam-QC (degrades to None = not
> assessed) → `scientific_result`; the two dialog storage/cache ops + the workflow persist → `write`; the
> method-widget / viewer clear → `ui_cleanup`. Budget file_io 64 → 0; uncategorized total 64 → 0.
>
> **Part 1 (cont.) — `toolbox` DONE (comment/test-only, git-only, no bump).** All 46 categorized to 0: the
> fit/QC/gate computations that report an honest failure (`fit_success=False`, all-NaN + a user warning, an
> assessable=False verdict, N/A, or a documented degraded fallback) — condensate-physics fits (intensity, msd,
> photobleaching, relaxation), FRAP fits, in-vitro boundary/partition fits, VPT Gaussian/ellipse fits,
> biological + reliability QC add-ons, the scan-QC drift check — → `scientific_result`; the headless-canvas /
> headless-viewer probes, the optional intensity-semantics module, and the CuPy backend version probe →
> `optional_probe`; matplotlib/dock/selection teardown + the data-QC UI gating-fail-open → `ui_cleanup`. Budget
> test + the three swallow guards green. Uncategorized total 110 → 64; **only `file_io` (64) remains.**
>
> **Part 1 (cont.) — `utils` DONE (comment/test-only, git-only, no bump).** All 45 categorized to 0:
> matplotlib/overlay teardown, subscriber-dispatch robustness, and cosmetic figure styling → `ui_cleanup`;
> headless-canvas / can't-subscribe / font-enumeration / version-metadata / registry+identity enrichment
> fallbacks → `optional_probe`; the additive biological-QC + reliability table columns, the calibration /
> reliability quality gates, and the operation-runner failure transport (surfaced to `on_error`) →
> `scientific_result`; the identity-store persist and the corrupt-settings quarantine → `write`. Budget test +
> the three swallow guards green. Uncategorized total 155 → 110; budgets: file_io 64, toolbox 46, **utils /
> batch / navigator / batch_processor.py / ui all 0**.
>
> **Part 1 (cont.) — navigator / batch / batch_processor.py DONE (comment/test-only, git-only, no bump).** The
> three small packages categorized to 0: `navigator` (3) — the `operation_spec` optional-dependency import
> probes (re-raise typed, or return not-importable / the missing dep name) → `optional_probe`. `batch` (4) —
> the brightfield cellpose-model fallback + the pixel-size/ball-radius metadata probes → `optional_probe`, the
> skipped BF-segmentation replay step → `batch_step`. `batch_processor.py` (2) — the additive provenance-sidecar
> write → `write`, the brushable-results dock open → `ui_cleanup`. Swallow guards + budget test green.
> Uncategorized total 164 → 155; budgets: file_io 64, toolbox 46, utils 45, **batch / navigator /
> batch_processor.py / ui all 0**.
>
> **Part 1 — categorization ratchet + `ui` package DONE (test/comment-only, git-only, no bump).** Added
> `test_exception_budget.py::test_no_package_GROWS_its_UNCATEGORIZED_broad_ok_count` — a per-package ratchet on
> the count of plain (uncategorized) `# broad-ok:` markers, pinned at the current counts and only ever going
> DOWN, so a new plain broad-ok in a package at budget fails and the sweep proceeds a package at a time. The
> `ui` package (41 handlers) was then categorized to **0**: cosmetic / teardown / dialog-robustness /
> gating-fail-open → `ui_cleanup`; external-capability probes (headless canvas, can't-subscribe, no-GPU, older
> napari API, unreadable file, predicate/metadata unavailable, layer-data read) → `optional_probe`. Comment +
> test only (no runtime change). Uncategorized total 205 → 164; budgets: file_io 64, toolbox 46, utils 45,
> batch 4, navigator 3, batch_processor.py 2, **ui 0**.
>
> **The batch_step rule is now STRUCTURALLY GUARDED (via close_partial_specs Part B; test-only guard).**
> `tests/test_no_silent_batch_step_swallowing.py` (`core`) flags a broad handler directly inside a multi-file
> batch loop that neither re-raises nor records the item's outcome (a `✗`/`⚠` row, a status flag, a
> `BatchStepResult`, or an assignment into the returned row), ratchet-pinned at 0. So all three swallow
> categories with no wrong number — scientific_result (`test_no_scientific_result_swallowing`), write
> (`test_no_silent_write_or_batch_swallowing`), and now batch_step — are guarded.
>
> **INCREMENT 2 — the write rule (Part 2 guard + writers-first Part 3 conversion), shipped 1.6.259.** New
> `test_no_silent_write_or_batch_swallowing.py` (`core`): an AST ratchet, conservative, scoped to broad
> handlers wrapped around a KNOWN persist call, that flags any which neither re-raise NOR surface (a notifier
> whose name carries `warn`/`error`/`critical`) NOR are annotated `# broad-ok: write` — budget pinned at **0**.
> Treating a user notification as compliant is what stops the many correct UI save handlers being
> false-positived. The one genuine silent loss — `write_session_outputs`' metadata-JSON + session-manifest
> sidecars, which only `debug_log`'d — was CONVERTED to surface via `notify.show_warning` (warn-and-continue;
> results themselves are safe). The genuinely-best-effort swallows were CATEGORIZED (Part 1, this slice):
> `local_cache` ×2 + `ts_cache_manager` cache writes (`write` — absence costs nothing), `temperature_tools` ×2
> batch exports (`write`/`batch_step` — error recorded into the returned row), `channel_designations._save`
> (`write` — returns False so the caller surfaces it). Ratchets tightened: `file_io` 239→237, `toolbox`
> 498→491, `utils` 114→113. Full core green (1631). **STILL REMAINING:** make the category MANDATORY on all
> ~166 annotated handlers (Part 1 in full — a per-handler judgement pass), and `BatchStepResult` adoption for
> the broader batch-step sweep (Part 3 batch half; the concrete `BatchWorker.run` offender is already guarded
> by `test_batch_step_visibility.py`).
>
> **INCREMENT 1 DONE (shipped 1.6.233): the concrete batch_step fix + the category vocabulary.** Delivered the spec's highest-value, stand-alone piece:
> the **batch cohort-corruption bug is fixed** — `BatchWorker.run` used to mark an image `✓` even when its
> consolidated-table append failed, silently dropping its rows from `consolidated_long.csv` (a 93-of-100
> cohort looking complete). The success mark is now gated on the append succeeding, with a visible `⚠`
> partial status otherwise (annotated `# broad-ok: batch_step — …`). And the **category vocabulary** is live:
> `test_exception_budget.py` now knows the five categories (ui_cleanup / optional_probe / scientific_result /
> write / batch_step) and validates the category WHEN a handler uses the `# broad-ok: <category> — <reason>`
> form (a typo'd category fails); legacy plain `# broad-ok: <reason>` handlers are unaffected. Guarded by
> `test_batch_step_visibility.py` (AST — the loop is a QThread) + the new budget-test category check.
> **Remaining (a large, careful follow-on, not to be rushed):** (Part 1) make the category MANDATORY on all
> ~166 annotated handlers — a per-handler judgement pass, since a wrong category mislabels intent; (Part 2)
> `test_no_silent_write_or_batch_swallowing.py`, the writer-scoped AST guard; (Part 3) convert the writer
> offenders (raise/surface on a failed save) and adopt `BatchStepResult` for the batch status. Writers-first,
> per the spec's damage ordering.

**Date:** 2026-07-21 · **Target tree:** 1.6.221 · Verified against the 1.6.221 tree. The latest audit's
#10 refinement. The scientific-result rule already shipped (1.6.210–211:
`test_no_scientific_result_swallowing.py`, toolbox ratchet 514→498, 15 handlers converted). This spec
extends it in the direction the audit asks: **classify broad handlers by what they GUARD, not only by
what they return**, so a failed *write* or a failed *batch step* is held to an appropriate standard —
not silently swallowed, but not treated identically to a UI-cleanup handler either.

## What already exists (verified)
- `test_no_scientific_result_swallowing.py` — enforces "a broad handler in a scientific module may not
  silently return a number/DataFrame/mask/calibration."
- `test_exception_budget.py` — per-package ratchet (`toolbox: 498`, `ui: 252`, `file_io: 239`,
  `utils: 114`) with `# broad-ok: <reason>` escape hatch.
So **scientific result-swallowing is handled**. What is NOT yet distinguished: the audit's other
categories.

## The audit's distinction (its exact framing)
> A UI cleanup exception ≠ an optional metadata probe ≠ a failed scientific computation ≠ a failed write
> ≠ a failed batch step. Broad exceptions surrounding scientific result generation should be treated
> more strictly than those in shutdown or optional-notification paths.

Scientific result generation is done. The two remaining categories that deserve stricter handling than
"annotated broad-ok" are **failed writes** and **failed batch steps** — because a swallowed write loses
data silently, and a swallowed batch step silently drops a result from a cohort, both of which corrupt
the science downstream without any wrong *number* appearing (so the result-swallowing rule doesn't catch
them).

## The categories and their rules
Classify each annotated broad handler (the `# broad-ok:` ones) into a category, and hold each to its
standard:

| category | example | rule |
|---|---|---|
| **ui_cleanup** | Qt close/teardown, widget disposal | broad-ok is fine — swallowing is correct |
| **optional_probe** | metadata sniff, optional-backend detect | broad-ok fine — absence is a valid outcome |
| **scientific_result** | a fit, a measurement | already enforced (must raise typed / NaN, never default) |
| **write** | saving a table, figure, session, mask | **must not silently succeed-looking on failure** — a failed write must surface (raise or a visible error), never be swallowed into apparent success |
| **batch_step** | one item in a batch loop | **must record a failed status**, not silently skip — a dropped item must appear as `failed`/`skipped` in the batch result, never vanish |

### The write rule
A swallowed write is the quiet data-loss bug: the user thinks their table/figure/session saved, but the
`except` ate the error and the file isn't there (or is truncated). For broad handlers around write
operations:
- On failure → **raise** (typed: a `PyCATError` write subclass) or return a **visible failure** the
  caller surfaces to the user. Never log-and-continue as if it succeeded.
- Annotate genuinely-safe write handlers (e.g. "best-effort cache write, absence is fine") explicitly as
  `# broad-ok: optional cache write` so the intent is on the record.

### The batch-step rule
This composes with the typed-result-models spec's `BatchStepResult(status=...)`. A broad handler around
a single batch item must set that item's status to `failed` (with the error) — so the item appears in
the batch report as failed, not silently absent. A batch that processes 100 images and silently drops 7
produces a cohort of 93 that looks complete — a subtle scientific corruption. The handler must make the
drop **visible**.

## Part 1 — categorize the annotated handlers
Extend the `# broad-ok:` convention to carry a category:
```python
except Exception:   # broad-ok: ui_cleanup — widget already torn down
except Exception:   # broad-ok: write — best-effort cache, absence is fine
```
Add the category vocabulary (ui_cleanup / optional_probe / scientific_result / write / batch_step) and
make the guard test parse it. An unannotated broad handler still fails the ratchet as today; an
annotated one must now name a **valid category**.

## Part 2 — the write + batch guard
Add `test_no_silent_write_or_batch_swallowing.py` (AST, scoped to the writer modules + batch modules):
- A broad handler in a **writer** path whose body returns/continues as success without re-raising or
  surfacing → fails the test unless annotated `# broad-ok: write` with a stated safe reason.
- A broad handler around a **batch item** that does not set a failed/skipped status → flagged.
Conservative (flag clear cases only), ratchet-style (count may not grow).

## Part 3 — convert the real offenders
Priority: **writers first** (data loss is worse than a dropped batch item that at least leaves the
others). Then batch steps. For each:
- writer: raise typed on failure or surface visibly; annotate the genuinely-optional ones.
- batch: record `failed` status via the batch result path.
Lower the `toolbox`/`file_io` ratchets by the number converted.

## Scope
- **Do not re-litigate scientific_result handlers** — they shipped; leave them.
- **Do not convert ui_cleanup / optional_probe** — swallowing is correct there; just categorize them.
- This is about the two categories that silently corrupt science *without* a wrong number: lost writes
  and dropped batch items.

## Tests
- The guard parses `# broad-ok: <category>` and rejects an invalid/absent category on an annotated
  handler.
- A writer broad handler that swallows a failure into apparent success fails the write guard; a
  raising/surfacing one passes.
- A batch-item broad handler that drops an item without a failed status is flagged; one that records
  `failed` passes.
- Converted writers raise typed on a simulated write failure (not silent success).
- Converted batch steps produce a `failed` status entry on a simulated item failure.
- `toolbox`/`file_io` ratchets lowered; no scientific output changes.

## Steps
1. Add the category vocabulary to the `# broad-ok:` convention + guard parsing.
2. Categorize the existing annotated handlers (mechanical, docs-like — one pass).
3. `test_no_silent_write_or_batch_swallowing.py` (writers + batch, conservative, ratchet).
4. Convert writers (raise/surface); then batch steps (failed status); lower ratchets.
5. Full `pytest -m core` green.
6. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (exception handlers
   categorized; failed writes and dropped batch steps no longer silently swallowed).

## Definition of done
- Broad handlers carry a category; the guard enforces a valid category on annotated ones.
- Failed writes raise/surface (no apparent-success-on-failure); failed batch items appear as `failed`,
  never silently dropped.
- New write/batch guard test enforces this and ratchets.
- Ratchets lowered; scientific_result and ui_cleanup handlers untouched.
- No scientific output changes.
- Full `pytest -m core` green.

## Cautions
- **Writers first** — a silently-lost save is the worst of these; a dropped batch item at least leaves a
  partial result. Prioritize by damage.
- **A dropped batch item is a silent cohort corruption** — 93-of-100 looking complete is the danger;
  make the drop visible via status.
- **Don't touch the shipped scientific_result handlers or the correct ui_cleanup/probe ones** — this is
  the two remaining categories, not a re-sweep.
- **Compose with `BatchStepResult`** (typed-result-models spec) for the batch status — don't invent a
  parallel status scheme.
- Conservative guard — flag clear swallowing only, to avoid false positives that tempt loosening.
