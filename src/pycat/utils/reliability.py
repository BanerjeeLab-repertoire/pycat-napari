"""**Measurement Reliability Index — every reported number carries a decomposable reliability score.**

The roadmap's unifying construct, buildable now that all its inputs exist: imaging QC (`data_qc_tools`),
biological plausibility (`biological_qc_tools`), parameter sensitivity (`measurement_stability`),
benchmark agreement (`benchmark_tools`), control separation (`control_validation`), and calibration
validity (`calibration`). **This is composition, not new science** — every factor comes from a module that
already measures it; inventing a new heuristic here would be unvalidated and would undermine the score.

Five rules keep it honest:

1. **An unmeasured factor is not a passing factor.** If a factor was not assessed it does NOT contribute —
   it goes in ``missing``, and the score says it was computed from fewer inputs. Silently treating an
   unmeasured factor as "fine" would make every score optimistic and the index worthless. *(The single
   most important rule.)*
2. **The score is decomposable.** ``contributions`` always shows which factor pulled the score down — a
   single opaque number is the black box PyCAT rejects.
3. **``reasons`` are ordered worst-first** and concrete ("3 of 42 objects touch the image border"), not
   "quality is low".
4. **Missing core evidence caps the grade.** A measurement with no QC or no calibration cannot be `high`,
   whatever the available factors say — absence of evidence is not evidence of reliability.
5. **The aggregation is stated, not hidden:** the score is the **product of the available factor scores**
   (each in 0..1), so any weak factor pulls the whole score down and the rule is explainable in a Methods
   section. **A refused calibration is a hard override to `unreliable`** — a number computed under an
   invalid calibration is not a weak measurement, it is not a measurement.

Reliability is REPORTED, never a silent filter — the user decides (the same contract as biological QC).
"""
from __future__ import annotations

import dataclasses

import numpy as np


#: The measurement family scored end-to-end in this increment (richest inputs, highest stakes). Extending
#: to other measurements is one entry each once the pattern is proven.
SCORED_FAMILY = ('partition_coefficient', 'client_enrichment', 'delta_g_transfer',
                 'dense_concentration', 'dilute_concentration', 'Kp_calibrated')

_ALL_FACTORS = ('image_qc', 'object_flags', 'calibration', 'sensitivity', 'benchmark')
#: Missing any of these core factors caps the grade below 'high' (rule 4).
_CORE_FACTORS = ('image_qc', 'calibration')


@dataclasses.dataclass(frozen=True)
class ReliabilityScore:
    value: float                      # 0..1 (nan when nothing could be assessed)
    grade: str                        # 'high' | 'moderate' | 'low' | 'unreliable'
    contributions: dict               # per-factor score in 0..1 — the score is decomposable
    reasons: tuple                    # human-readable, ordered worst-first
    missing: tuple                    # factors that could NOT be assessed (not treated as passing)


# ── Per-factor scoring: each reads a signal from its own module; None input → not assessed ───────
_QC_STATUS_SCORE = {'good': 1.0, 'warn': 0.6, 'bad': 0.2}


def _score_qc(qc_results):
    """From a list of `run_full_qc` result dicts → (score, reasons). Only assessable checks (good/warn/bad)
    count; an `na` check is not evidence either way. None if nothing was assessable."""
    assessable = [r for r in qc_results if r.get('status') in _QC_STATUS_SCORE]
    if not assessable:
        return None, []
    score = float(np.mean([_QC_STATUS_SCORE[r['status']] for r in assessable]))
    reasons = [f"QC {r.get('name', '?')}: {r.get('headline', r['status'])}"
               for r in assessable if r['status'] != 'good']
    return score, reasons


def _score_object_flags(object_flags):
    """From ``(n_flagged, n_total)`` (or a biological_qc result DataFrame) → (score, reasons). Score is the
    unflagged fraction. None if there are no objects.

    Also accepts a bare float in 0..1 — a **pre-computed per-object** unflagged-confidence (the consolidated
    table passes this per row: 1.0 for an object biological QC did not flag, lower when it did), so a single
    object's reliability reflects its own flag rather than the population rate."""
    if isinstance(object_flags, (int, float)) and not isinstance(object_flags, bool):
        s = float(object_flags)
        return s, ([] if s >= 0.85 else ['object flagged by biological QC'])
    if hasattr(object_flags, 'attrs') and hasattr(object_flags, 'columns'):
        n_total = len(object_flags)
        report = object_flags.attrs.get('qc_report', {})
        n_flagged = int(object_flags.get('qc_flags', '').astype(bool).sum()) if 'qc_flags' in object_flags \
            else int(sum(report.values()))
        detail = ", ".join(f"{k}={v}" for k, v in report.items() if v)
    else:
        n_flagged, n_total = int(object_flags[0]), int(object_flags[1])
        detail = f"{n_flagged} flagged"
    if n_total <= 0:
        return None, []
    score = 1.0 - n_flagged / n_total
    reasons = [f"{n_flagged} of {n_total} objects flagged ({detail})"] if n_flagged else []
    return float(score), reasons


def _score_calibration(calibration):
    """From a `ValidityVerdict` (or a dict with valid/level/reason) → (score, reasons, hard_unreliable).

    A **refused** calibration (valid=False) is a hard override to `unreliable`: a number computed under an
    invalid calibration is not a weak measurement, it is not a measurement."""
    valid = getattr(calibration, 'valid', None)
    level = getattr(calibration, 'level', None)
    reason = getattr(calibration, 'reason', None)
    if valid is None and isinstance(calibration, dict):
        valid, level, reason = calibration.get('valid'), calibration.get('level'), calibration.get('reason')
    if not valid:
        return 0.0, [f"calibration REFUSED: {reason}"], True
    if level == 'warn':
        return 0.6, [f"calibration warning: {reason}"], False
    return 1.0, [], False


def _score_sensitivity(sensitivity):
    """From a `StabilityResult`, a verdict string, or a 0..1 float → (score, reasons)."""
    verdict = getattr(sensitivity, 'verdict', None)
    if verdict is not None:
        from pycat.toolbox.measurement_stability import stability_factor
        score = stability_factor(sensitivity)
        if not np.isfinite(score):
            return None, []                       # 'population-change'/'undefined' → cannot assess
        reasons = [] if verdict == 'stable' else [f"parameter sensitivity: {verdict} "
                                                  f"({getattr(sensitivity, 'relative_range', 0):.0%} range)"]
        return float(score), reasons
    if isinstance(sensitivity, str):
        score = {'stable': 1.0, 'sensitive': 0.6, 'unstable': 0.2}.get(sensitivity)
        return (None, []) if score is None else (score, [] if sensitivity == 'stable'
                                                 else [f"parameter sensitivity: {sensitivity}"])
    score = float(sensitivity)
    return score, ([] if score >= 0.85 else [f"parameter sensitivity factor {score:.2f}"])


def _score_benchmark(benchmark):
    """From a benchmark agreement value (e.g. F1/Dice, 0..1) → (score, reasons)."""
    score = float(benchmark)
    return score, ([] if score >= 0.85 else [f"benchmark agreement {score:.2f}"])


_SCORERS = {
    'image_qc': lambda x: _score_qc(x) + (False,),
    'object_flags': lambda x: _score_object_flags(x) + (False,),
    'calibration': _score_calibration,
    'sensitivity': lambda x: _score_sensitivity(x) + (False,),
    'benchmark': lambda x: _score_benchmark(x) + (False,),
}


def reliability(measurement_key, *, image_qc=None, object_flags=None, calibration=None,
                sensitivity=None, benchmark=None) -> ReliabilityScore:
    """Compose the available reliability signals into a decomposable 0..1 score for ``measurement_key``.

    Each factor is scored only if its signal is supplied; an omitted factor goes in ``missing`` and does
    NOT contribute (rule 1). The value is the **product of the available factor scores** (rule 5). A
    refused calibration hard-overrides to `unreliable`. Missing a core factor (QC or calibration) caps the
    grade below `high` (rule 4). ``reasons`` are ordered worst-first."""
    supplied = {'image_qc': image_qc, 'object_flags': object_flags, 'calibration': calibration,
                'sensitivity': sensitivity, 'benchmark': benchmark}

    contributions, factor_reasons, missing, hard_unreliable = {}, {}, [], False
    for name in _ALL_FACTORS:
        value = supplied[name]
        if value is None:
            missing.append(name)
            continue
        score, reasons, hard = _SCORERS[name](value)
        if score is None:                          # supplied but nothing assessable → treat as missing
            missing.append(name)
            continue
        contributions[name] = float(score)
        factor_reasons[name] = reasons
        hard_unreliable = hard_unreliable or hard

    if not contributions:
        return ReliabilityScore(float('nan'), 'unreliable', {}, (
            'no reliability factor could be assessed',), tuple(missing))

    value = float(np.prod(list(contributions.values())))

    # reasons ordered worst-first: the factor that hurt the score most comes first.
    ordered = sorted(contributions, key=lambda k: contributions[k])
    reasons = [r for k in ordered for r in factor_reasons.get(k, [])]
    for k in missing:
        reasons.append(f"{k.replace('_', ' ')} not assessed")

    if hard_unreliable:
        grade = 'unreliable'
        value = 0.0
    else:
        capped = any(c in missing for c in _CORE_FACTORS)
        if value >= 0.85:
            grade = 'moderate' if capped else 'high'
        elif value >= 0.6:
            grade = 'moderate'
        elif value >= 0.3:
            grade = 'low'
        else:
            grade = 'unreliable'

    return ReliabilityScore(value=value, grade=grade, contributions=contributions,
                            reasons=tuple(reasons), missing=tuple(missing))


def format_with_reliability(name, value, units, score) -> str:
    """Extend the ``name = value units`` display with the reliability grade — e.g.
    ``K_p = 4.2 (reliability: moderate)``."""
    base = f"{name} = {value} {units}".strip()
    return f"{base} (reliability: {score.grade})"


def reliability_report_section(scored) -> str:
    """A QC-report section listing the scored measurements whose reliability grade is **capped below
    `high`, and WHY** — so the report says which numbers to trust less and what pulled them down.

    ``scored`` is an iterable of ``(label, ReliabilityScore)``. Returns a text block, or ``''`` when every
    scored measurement is `high` (nothing to flag). Each capped line names the grade, the missing factors
    that capped it (rule 4), and the single worst-first reason — concrete, not "quality is low"."""
    lines = []
    for label, score in scored:
        if getattr(score, 'grade', 'high') == 'high':
            continue
        cap = f" [capped: {', '.join(score.missing)} not assessed]" if score.missing else ''
        top = f" — {score.reasons[0]}" if score.reasons else ''
        lines.append(f"  {label}: {score.grade}{cap}{top}")
    if not lines:
        return ''
    return "Measurement reliability (capped below 'high'):\n" + "\n".join(lines)
