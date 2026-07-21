# Claude Code spec — Decompose `invitro_tools.py` by domain

> **◐ STATUS — Steps 1–3 (size_distribution, partition, field_summary) DONE (1.6.213–1.6.215); the small
> remaining sections remain.** Step 3: the whole-field summary (`field_summary` + `_field_summary_metrics`)
> moved to `toolbox/invitro/field_summary.py`, re-exported; byte-identical (`test_field_summary_is_byte_identical`
> + enrichment/halo tests pass). `invitro_tools` dropped 799→605; ceiling ratcheted to 605. **Remaining:**
> the smaller sections — coarsening_statistics, estimate_csat_lever_rule, estimate_contact_angle,
> detect_and_fit_fusions, detect_sedimentation — to a final `analysis.py`/domain module, then `invitro_tools`
> is a pure shim.
>
> _Steps 1–2 (1.6.213–1.6.214):_ Step 2: the calibration-sensitive partition-coefficient domain
> (`partition_coefficient_local` + `_pc_*` helpers + `partition_measurement` + `partition_coefficient_field`
> + `estimate_phase_boundary`) moved VERBATIM to `toolbox/invitro/partition.py`, re-exported for callers;
> byte-identical (test_partition* + the calibration/ΔG net pass). One test's monkeypatch target was updated
> to the moved module (assertions/values unchanged — the OVER-INCLUSIVE warning still fires). `invitro_tools`
> dropped 1623→799; the per-file ceiling ratcheted to 799. **Remaining:** field_summary + the smaller
> sections (coarsening, C_sat, contact-angle, fusion, sedimentation) — one domain per commit.
>
> _Step 1 (1.6.213):_ The `toolbox/invitro/` package exists; `size_distribution.py` holds the MLE model-selection
> path (`fit_size_distribution_mle` + `fit_size_distribution` + the phase helpers), moved VERBATIM from
> `invitro_tools`, which now re-exports the two public entry points so every caller (invitro UIs, batch
> steps, the op-catalog api string) is unchanged. Byte-identical — `test_size_distribution_mle_characterization`
> and the invitro size tests pass unmodified; the moved keys are recorded in the drop-guard's `_DELIBERATE`.
> `invitro_tools.py` dropped 2051→1623 and a NEW per-file ceiling was established there (1623) to lock the
> shrink and ratchet DOWN as the remaining domains move out. **Remaining:** partition (calibration-sensitive
> — the calibration/ΔG/partition tests are the net), field_summary (preserve the 2D projection behaviour),
> spatial, and the run_* orchestration — one domain per commit.

**Date:** 2026-07-20 · **Target tree:** 1.6.203 · Verified against the 1.6.203 tree. A well-covered
mid-size science file (**2,039 lines, 15 test files**) holding the in-vitro condensate analysis — the
partition/enrichment/size-distribution/volume-fraction path that is manuscript-facing. Splits cleanly by
analysis domain. Coverage-gated, behaviour-preserving.

## Verified state
2,039 lines, 30 functions, 5 over 120 lines. **15 test files** reference it — a strong net, including
the calibration/ΔG and partition tests. The functions cluster into: partition/enrichment measurement,
size-distribution fitting (`fit_size_distribution_mle`, already phase-split in 1.6.174),
volume-fraction/field-summary, spatial metrology hooks, and the segmentation-driven analysis entry.

## Target — an `invitro/` package by domain
```
toolbox/invitro/
    partition.py        # partition_coefficient_local + enrichment measurement (calibration-aware)
    size_distribution.py# fit_size_distribution_mle + size-distribution analysis
    field_summary.py     # field-level summaries, volume-fraction (2D-projection-aware — CondensateMode)
    spatial.py           # spatial-metrology hooks (Ripley/PCF/NN wiring for in-vitro)
    analysis.py          # the run_* orchestration entry points
    result_models.py     # result assembly (typed later)
```
`invitro_tools.py` becomes a thin re-export shim.

## Method — coverage-gated, calibration-sensitive
1. **Partition/enrichment is calibration-sensitive** — `partition_coefficient_local` (phase-split
   already) and the ΔG/concentration path depend on calibration validity. Confirm the calibration/ΔG
   tests pin the numbers before moving; they are the net.
2. **`fit_size_distribution_mle`** is already phase-split and byte-identical — moving it whole to
   `size_distribution.py` is low-risk; still pin-then-move.
3. **Volume-fraction / field-summary** — this is where the `CondensateMode` gating belongs (the 2D
   projection proxy). If the condensate-modes wiring (backlog B2/orphan) has landed, keep it; if not,
   leave the behaviour exactly as-is and note the future wiring point. Do not change the 2D numbers.
4. **Move, don't rewrite** — no changed background handling, no altered fit, no reordered measurement.

### Hard rules
- One domain per commit; the calibration/ΔG/partition tests + `pytest -m core` green between each.
- No test edited to make a move pass.
- Re-export shim for every previously-public name; grep callers first (invitro UIs, batch steps, and
  the comparative-phenotyping path call in).
- `materialize_stack` not `np.asarray` on any stack touched.

## Why now
- Well-covered (15 files) — safe.
- Manuscript-facing (partition/ΔG/enrichment) — a focused `partition.py` is easier to cite and verify.
- Partially decomposed already (`fit_size_distribution_mle`, `partition_coefficient_local` phase-split)
  — pattern proven on this file.
- Rounds out the science-file decomposition alongside the five big ones.

## Tests
- Calibration/ΔG/partition tests pass unmodified after the partition move.
- Size-distribution fit byte-identical after moving.
- Volume-fraction/field-summary output unchanged (2D projection behaviour preserved).
- All 15 test files pass unmodified.
- Re-export shim resolves every previously-public name.
- Lower `_MAX_LONG_FUNCTIONS` / per-file ratchet.

## Steps
1. Create `toolbox/invitro/`; move `partition.py`; run calibration/partition tests + core.
2. Move `size_distribution.py`; run.
3. Move `field_summary.py` (preserve CondensateMode/2D behaviour); run.
4. Move `spatial.py`; run.
5. Move `analysis.py` orchestration; run.
6. `invitro_tools.py` → re-export shim; lower ratchets.
7. Full `pytest -m core` green after each step.
8. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG before/after.

## Definition of done
- `invitro_tools.py` is a thin shim; domains live in `toolbox/invitro/`.
- Calibration/ΔG/partition tests pass unmodified; partition and size-distribution outputs identical.
- Volume-fraction 2D-projection behaviour preserved.
- All 15 test files pass unmodified; ratchets lowered.

## Cautions
- **Partition/ΔG is calibration-sensitive** — the calibration tests are the net; a wrong scale or
  background change corrupts K_p. Move structure only.
- **Preserve the 2D volume-fraction behaviour** — do not change the projected numbers or the mode gating
  in this split; that is separate (condensate-modes) work.
- **Move, don't improve** — no background/fit/measurement changes while relocating.
- Re-export shim mandatory; invitro UIs + batch + comparative-phenotyping import this — grep every caller.
- One domain per commit.
