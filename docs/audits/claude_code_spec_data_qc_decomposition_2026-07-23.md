# Claude Code spec — Decompose `data_qc_tools.py` by check family

> **◐ STATUS — step 1 (report @1.6.347) + step 2a (shared `_base` primitives @1.6.348) DONE; module 1,949 →
> 1,532 lines. Remaining: the check families (exposure, focus, noise, illumination, aberration, stability,
> sampling) — each imports from `_base` — + the runner.**
>
> **Step 2a — _base.py — DONE, 1.6.348.** The check families share low-level helpers (`_to_float` ×10,
> `_not_applicable` ×10, `_mean_frame` ×6, `_dtype_max`, `_robust_noise_std`), so those primitives moved VERBATIM
> to `data_qc/_base.py` first (the image_processing `_base` pattern). `data_qc_tools` imports + re-exports them.
> This unblocks the per-family moves (each family module will `from data_qc._base import …`).
>
> **Step 1 — report.py — DONE, 1.6.347.** The 309-line `plot_qc_report` (presentation only) + its private
> `_STATUS_COLOR`/`_STATUS_LABEL` dicts (used only by it) moved VERBATIM to `data_qc/report.py`;
> `data_qc_tools.py` re-exports it and drops 1,949 → 1,633 lines. Pinned by the QC net (test_data_qc /
> test_qc_gallery / test_qc_ui_contract / test_biological_qc_surfaced). Recorded in `_DELIBERATE`.

**Date:** 2026-07-23 · **Target tree:** 1.6.324 · Verified against the 1.6.324 tree. **1,949 lines, 29
functions, 6 over the 120-line ratchet**, with 12 test files covering it. It mixes independent QC checks,
an orchestrator, and a 309-line plotting function — three different concerns in one module.

## Verified structure
```
   309  plot_qc_report            ← PRESENTATION, not science
   158  run_full_qc               ← orchestration
   137  qc_vibration
   135  qc_spherical_aberration
   134  qc_chromatic
   129  qc_vignetting
   104  _qc_focus_absolute
    93  qc_drift
```
Each `qc_*` is an **independent check** with its own inputs, its own verdict, and (per the QC design)
its own "did this check actually run?" reporting. That independence is what makes this the cleanest
remaining science split — the functions barely interact.

## Target — a `data_qc/` package
```
toolbox/data_qc/
    exposure.py      # saturation, clipping, dynamic range, bit-depth usage
    focus.py         # focus metrics, _qc_focus_absolute
    noise.py         # SNR, background variance
    illumination.py  # vignetting, flat-field uniformity
    aberration.py    # spherical aberration, chromatic shift
    stability.py     # drift, vibration, photobleaching
    sampling.py      # Nyquist / resolution adequacy
    report.py        # plot_qc_report — presentation only
    runner.py        # run_full_qc orchestration
```
`data_qc_tools.py` becomes a thin re-export shim.

## Method — coverage-gated; the checks are the easy part
1. **Pin each check before moving.** A QC check returns a verdict plus a "did it run" flag; the
   characterization test must assert **both** — a check that silently degrades to not-run would pass a
   verdict-only test while losing coverage.
2. **The cry-wolf tests are the net.** Clean data must raise **zero** flags after the split, exactly as
   before. If any check starts firing on clean input, the move changed behaviour.
3. **Separate presentation from science.** `plot_qc_report` (309 lines) is the largest function here and
   is pure rendering. Moving it to `report.py` isolates the biggest chunk immediately and makes the
   remaining science modules small. Consider routing it through the canonical `FigureSpec` afterwards —
   **but not in the same commit**; that is a behaviour change, this is a move.
4. **Move, don't rewrite.** No re-tuned thresholds, no "improved" metrics. The inverted-spherical-
   aberration bug in this file's history is exactly why: a small change here silently flips a verdict.
5. One family per commit; cry-wolf + `pytest -m core` green between each.
6. Re-export shim; grep callers first (the QC dashboard, the QC gallery, the navigator's quality gate,
   and the reliability index all consume these).

## Why now
- Largest remaining non-decomposed toolbox module after coloc.
- **Six functions over the ratchet** — a meaningful ratchet move.
- The checks are genuinely independent, so the split is low-risk.
- QC is manuscript-facing (it is the Figure 1 story); a focused module per check family is far easier to
  describe in Methods than a 1,949-line file.

## Tests
- Each moved check is byte-identical on its characterization input, **including its did-it-run flag**.
- Cry-wolf: clean data raises zero flags after every move.
- `run_full_qc` produces the same report structure and the same per-check verdicts.
- `plot_qc_report` renders the same figure content after moving.
- All 12 referencing test files pass unmodified.
- The shim resolves every previously-public name.
- Lower `_MAX_LONG_FUNCTIONS` and the per-file line ratchet.

## Steps
1. Move `report.py` (`plot_qc_report`) first — biggest single reduction, zero science risk.
2. Move the check families: `exposure` → `focus` → `noise` → `illumination` → `aberration` →
   `stability` → `sampling`, one commit each.
3. Move `runner.py` (`run_full_qc`) last, once its checks are all relocated.
4. `data_qc_tools.py` → re-export shim; lower ratchets.
5. Full `pytest -m core` + cry-wolf green after each step.
6. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG with before/after.

## Definition of done
- `data_qc_tools.py` is a thin shim; checks live in `toolbox/data_qc/` by family, with presentation and
  orchestration separated from the science.
- Every check's verdict **and** did-it-run flag are unchanged.
- Cry-wolf holds: clean data raises nothing.
- Ratchets lowered; all pre-existing tests pass unmodified.

## Cautions
- **Assert the did-it-run flag, not just the verdict** — a check that silently stops running would pass
  a verdict-only test.
- **Cry-wolf is the gate.** A check firing on clean data after a "structural" move means behaviour
  changed.
- **Do not re-tune any threshold while moving.** This file previously shipped an inverted spherical-
  aberration check; small changes here flip verdicts silently.
- **Do not route `plot_qc_report` through `FigureSpec` in the same commit** — that is a behaviour change
  and belongs in its own.
- Re-export shim mandatory; the QC dashboard, gallery, quality gate and reliability index all import
  these.
