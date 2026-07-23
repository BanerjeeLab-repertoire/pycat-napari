# Claude Code spec — Measurement Reliability Index (MRI)

> **✅ STATUS — DONE for the batch as it exists. Core DONE; surfacing DONE (4a Measurement display 1.6.254;
> 4b/4c table columns + QC-report section 1.6.255); batch auto-population DONE (1.6.257: `_reliability_context_for`
> → `run_full_qc` on the already-materialised image, gated to scored-family images). Calibration is
> **correctly N/A** for the batch, not a gap: the batch's only scored measurement is the UNCALIBRATED
> intensity-ratio `partition_coefficient` (`replay_ivf_field_summary`), and the calibrated path
> (`client_enrichment` → concentrations/ΔG, which carries the `calibration_validity` verdict) is stubbed in
> headless mode. So calibration genuinely does not apply, and capping the uncalibrated proxy below `high` is
> the honest, desirable signal — never treat an uncalibrated Kp as high reliability. Threading a real
> calibration factor requires the deferred feature below (headless calibrated concentration), which is a new
> analysis capability, not a wiring task. Extending SCORED_FAMILY beyond partition/concentration/ΔG remains
> one registry entry each.**

## Roadmap (deferred): calibration in the batch — headless calibrated concentration

**Why deferred, captured so it is not re-investigated.** The reliability `calibration` factor
(`check_calibration_validity` → valid/level/reason) only has a source when a *calibrated* measurement runs.
That path does not run in the batch today, so there is nothing to thread. To close it, add headless
calibrated concentration to the batch, then thread its verdict. Everything needed:

- **The stub to replace.** `src/pycat/batch_step_registry.py:335` — `'client_enrichment'` is
  `lambda s,p,pa,o: print('… skipped in headless mode (interactive layer selection)')`. The interactive
  version is `_add_client_enrichment` (`toolbox/partition_enrichment_tools.py:431`); the pure analysis is
  `client_enrichment(...)` (line 75) which takes a `calibration_curve=None` (line 83) and, when given, calls
  `_calibrated_partition(curve, image_metadata, dense_c, dilute_c, T)` (line 261/268).
- **Where the verdict is produced.** `_calibrated_partition` (line 268) calls
  `check_calibration_validity(curve, image_metadata)` and returns `calibration_validity` = `{valid, level,
  reason}` plus `dense_concentration` / `dilute_concentration` / `Kp_calibrated` / `delta_g_transfer`. These
  are exactly the calibrated `SCORED_FAMILY` members (`utils/reliability.py:37`).
- **The curve is persistable/loadable.** `utils/calibration.py:336 save_curve` / `:343 load_curve`; the
  interactive UI already tracks a `_calibration_path` and loads via `load_curve` (`ui/ui_modules.py:~1588`,
  status label `:1547`). So the batch config could record a calibration-curve path (or a batch-level
  setting) and `load_curve` it per run.
- **What the headless step must do.** Get the dense/dilute intensities from the droplet mask (the invitro
  path already has `partition_coefficient_field` / `field_summary`), load the curve, call `client_enrichment`
  with `calibration_curve=`, write the calibrated columns to the per-image CSV, and stash the verdict into
  `state['_calibration_validity']`.
- **The threading hook already exists.** `BatchProcessor._reliability_context_for` (`batch_processor.py`)
  already builds the per-image `reliability_context` and is gated by `records_have_scored_family`. Once the
  verdict is in `state`, `_process_file` stashes it (mirroring `self._last_image`) and
  `_reliability_context_for` adds `calibration=self._last_calibration` alongside `image_qc`. The
  consolidated-table `_row_reliability_factory` already consumes `reliability_context['calibration']`
  (`utils/consolidated_table.py:165`) — no table change needed.
- **Caveat / correctness.** Only calibrated measurements should carry the calibration factor. The
  uncalibrated intensity-ratio `partition_coefficient` must stay capped (calibration in `missing`) — do not
  attach a curve's verdict to it. Consider a per-measurement "calibration applies?" flag if both calibrated
  and uncalibrated Kp coexist in one table.

**Date:** 2026-07-19 · **Target tree:** 1.6.156 · Verified against the 1.6.156 tree. The roadmap's
*"unifying construct"* — every reported measurement carries a reliability score, and clicking it
explains why. Deferred for good reason until its inputs existed. **They now all do**, which is what
makes this buildable rather than aspirational.

## Why now (verified prerequisites)
The roadmap defines MRI as combining image QC, segmentation confidence, parameter sensitivity,
benchmark agreement, and biological plausibility. Every one of those now exists:

| MRI input | where it lives | shipped |
|---|---|---|
| imaging QC | `data_qc_tools.run_full_qc` (12 checks, tiered) | earlier |
| biological plausibility | `biological_qc_tools` (object-level flags) | 1.6.152 |
| parameter sensitivity | `tests/filter_sensitivity.py` + `VALIDATED_CASES` | 1.6.130+ |
| benchmark agreement | `benchmark_tools` (`matched_detection`, `basic_metrics`) | earlier |
| control validation | `control_validation` (positive/negative separation) | 1.6.156 |
| measurement definition & caveats | `measurement_ontology` | 1.6.154 |
| units, uncertainty, validity | `measurement.Parameter` / `ValidationLevel` | earlier |
| calibration validity | `calibration.check_calibration_validity` | earlier |

MRI is therefore **composition, not new science** — which is exactly the condition under which it
should be built, and why attempting it earlier would have meant inventing its inputs.

## The construct
```python
@dataclass(frozen=True)
class ReliabilityScore:
    value: float                   # 0..1
    grade: str                     # 'high' | 'moderate' | 'low' | 'unreliable'
    contributions: dict[str, float]   # per-factor, so the score is decomposable
    reasons: tuple[str, ...]       # human-readable, ordered worst-first
    missing: tuple[str, ...]       # factors that could NOT be assessed

def reliability(measurement_key, *, image_qc=None, object_flags=None,
                calibration=None, sensitivity=None, benchmark=None) -> ReliabilityScore
```

### The design rules that make it honest
1. **Never invent a factor that wasn't measured.** If segmentation stability was not assessed, it does
   not contribute — it goes in `missing`, and the score states it was computed from fewer inputs.
   Silently treating an unmeasured factor as "fine" would make every score optimistic.
2. **The score is decomposable.** `contributions` must always let a user see *which* factor pulled the
   score down. A single opaque number is precisely the black box PyCAT rejects.
3. **`reasons` are ordered worst-first** and phrased concretely — *"boundary ambiguous: 31% of
   perimeter pixels are between-class"*, *"pixel size unverified"*, *"3 of 42 objects touch the image
   border"* — not *"quality is low"*.
4. **Missing inputs cap the grade.** A measurement with no QC and no calibration cannot be `high`, no
   matter what the available factors say. Absence of evidence is not evidence of reliability.
5. **The aggregation must be stated, not hidden.** Use a simple, explainable rule (e.g. weighted
   minimum, or a product of factor scores) and document it. A tuned ML-ish blend would be unexplainable
   and therefore unusable in a Methods section.

## Scope — one measurement family first
**Do not score every measurement in this increment.** Start with the family where all inputs are
richest and the stakes are highest: **partition coefficient / concentration / ΔG_transfer**. These
already carry calibration validity, have ontology entries with caveats, and are directly manuscript-
facing.

Extending to other measurements is one registry entry each once the pattern is proven.

## Surfacing it
- The `Parameter` display already renders `name = value units`; extend to optionally append the grade
  (`K_p = 4.2 (reliability: moderate)`).
- In the consolidated long table, add `reliability` and `reliability_reasons` columns so a comparative
  figure can be recomputed on high-reliability objects only — and the difference *shown*. That is the
  scientifically strongest use: *"the effect holds when restricted to high-reliability measurements."*
- In the QC report, a section listing the measurements whose reliability is capped and why.

## Tests (`core`, synthetic)
- A measurement with clean QC, valid calibration, no object flags, and validated parameters scores
  `high`.
- **Each factor, degraded individually, lowers the score and names itself in `reasons`** — one test
  per factor, so no contribution is silently ignored.
- **Missing factors cap the grade** and are listed in `missing` — never treated as passing.
- `contributions` sums/decomposes consistently with `value` under the documented rule.
- Reasons are ordered worst-first.
- A measurement whose calibration is **refused** (mismatched acquisition) scores `unreliable`, not
  merely low — a number computed under an invalid calibration is not a weak measurement, it is not a
  measurement.

## Steps
1. `utils/reliability.py` — `ReliabilityScore` + `reliability()` with a documented aggregation rule.
2. Adapters pulling each factor from its existing module (QC, bio-QC, calibration, sensitivity
   registry, benchmark).
3. Wire the partition/concentration/ΔG family.
4. `Parameter` display option; consolidated-table columns; QC report section.
5. Tests above.
6. Full `pytest -m core` green.
7. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- `reliability()` composes existing signals into a decomposable 0–1 score with a grade, per-factor
  contributions, worst-first reasons, and an explicit `missing` list.
- Missing factors cap the grade; a refused calibration yields `unreliable`.
- The partition/concentration/ΔG family is scored end to end.
- Reliability appears on `Parameter` display, in the consolidated table, and in the QC report.
- Full `pytest -m core` green.

## Cautions
- **Composition only — invent no new metric.** Every factor must come from a module that already
  measures it. A new heuristic invented here would be unvalidated and would undermine the whole score.
- **An unmeasured factor is not a passing factor.** This is the single most important rule; violating
  it makes every score optimistic and the index worthless.
- Keep the aggregation explainable — it may end up described in a Methods section.
- Do not let reliability silently filter anything. It is reported; the user decides (same contract as
  biological QC).
- One measurement family first. A half-scored codebase with a proven pattern beats a fully-scored one
  built on guesses.
