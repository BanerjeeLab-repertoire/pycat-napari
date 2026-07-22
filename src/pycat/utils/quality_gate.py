"""**One gate that says whether a measurement may run — block, warn, or downgrade, with reasons.**

Every measurement has preconditions: a physical-unit result needs a real pixel size, a concentration needs a
valid calibration curve, a trusted number needs its reliability assessed. Those signals already exist
(`pixel_size`, `calibration.check_calibration_validity`, `reliability`), but nothing composes them into a
single yes/no/careful answer a navigator, batch run, QC advisor, or measurement UI can all consult. This is
that composer — **feature-agnostic**, and it **invents no new metric**; it only combines the signals that
are already there.

The rules it enforces, the same "refuse rather than lie" contract as the gates it composes:

* **Block ≠ warn ≠ downgrade.** BLOCK = a hard precondition is unmet, do not run. DOWNGRADE = run, but the
  claim must be reduced (the number is not trustworthy at face value). WARN = run, but a caveat travels
  with the result. OK = clear.
* **An unassessed signal is NOT a passing signal.** If a needed precondition could not be evaluated, that is
  a WARN ("could not confirm"), never a silent pass.
* **Report, don't drop.** Every signal's outcome is on the result, so the reason a run was blocked/flagged
  is inspectable — the overall verdict is the WORST of them.
"""
from __future__ import annotations

import dataclasses
import enum


class GateVerdict(str, enum.Enum):
    OK = 'ok'
    WARN = 'warn'
    DOWNGRADE = 'downgrade'
    BLOCK = 'block'

    @property
    def rank(self) -> int:
        return {'ok': 0, 'warn': 1, 'downgrade': 2, 'block': 3}[self.value]


@dataclasses.dataclass(frozen=True)
class SignalOutcome:
    """One precondition's verdict and why — kept on the result so nothing is silently dropped."""
    name: str
    verdict: GateVerdict
    reason: str


@dataclasses.dataclass(frozen=True)
class GateResult:
    """The composed answer: the worst signal verdict, plus every signal outcome for inspection."""
    verdict: GateVerdict
    signals: tuple = ()

    @property
    def runnable(self) -> bool:
        """True unless a hard precondition BLOCKs the run."""
        return self.verdict is not GateVerdict.BLOCK

    @property
    def reasons(self) -> tuple:
        """The reasons from every non-OK signal, worst first."""
        bad = [s for s in self.signals if s.verdict is not GateVerdict.OK]
        bad.sort(key=lambda s: s.verdict.rank, reverse=True)
        return tuple(s.reason for s in bad)


@dataclasses.dataclass(frozen=True)
class QualityRequirement:
    """What an operation needs of its inputs. All optional — a plain-geometry op requires none of it."""
    needs_pixel_size: bool = False        # a physical-unit (µm/µm²) result
    needs_calibration: bool = False       # a concentration / ΔG result
    min_reliability: str | None = None    # a grade floor: 'high' | 'moderate' | 'low'
    measurement_key: str = ''             # which measurement, for the reliability signal


_GRADE_RANK = {'unreliable': 0, 'low': 1, 'moderate': 2, 'high': 3}


def _pixel_size_signal(context) -> SignalOutcome:
    ok = context.get('pixel_size_ok')
    if ok is True:
        return SignalOutcome('pixel_size', GateVerdict.OK, 'a real pixel size is set.')
    if ok is False:
        return SignalOutcome('pixel_size', GateVerdict.BLOCK,
                             'no real pixel size — a physical-unit measurement would be in pixels labelled '
                             'as microns. Set the pixel size first.')
    return SignalOutcome('pixel_size', GateVerdict.WARN,
                         'pixel size was not assessed — cannot confirm the result is in physical units.')


def _calibration_signal(context) -> SignalOutcome:
    verdict = context.get('calibration_verdict')
    if verdict is None and context.get('calibration_curve') is not None:
        try:
            from pycat.utils.calibration import check_calibration_validity
            verdict = check_calibration_validity(context['calibration_curve'],
                                                 context.get('image_metadata') or {})
        except Exception:      # broad-ok: an unusable calibration input → treat as not-assessed, never crash
            verdict = None
    if verdict is None:
        return SignalOutcome('calibration', GateVerdict.WARN,
                             'calibration was not assessed — a concentration/ΔG cannot be confirmed valid.')
    if not getattr(verdict, 'valid', False):
        return SignalOutcome('calibration', GateVerdict.BLOCK,
                             f'calibration invalid: {getattr(verdict, "reason", "")}')
    if getattr(verdict, 'level', 'ok') == 'warn':
        return SignalOutcome('calibration', GateVerdict.WARN,
                             f'calibration caution: {getattr(verdict, "reason", "")}')
    return SignalOutcome('calibration', GateVerdict.OK, 'calibration valid for this image.')


def _reliability_signal(context, requirement) -> SignalOutcome:
    score = context.get('reliability_score')
    if score is None:
        try:
            from pycat.utils.reliability import reliability
            score = reliability(
                requirement.measurement_key or 'measurement',
                image_qc=context.get('image_qc'), object_flags=context.get('object_flags'),
                calibration=context.get('calibration'), sensitivity=context.get('sensitivity'),
                benchmark=context.get('benchmark'))
        except Exception:      # broad-ok: no assessable reliability signal → not-assessed, never crash
            score = None
    grade = getattr(score, 'grade', None)
    value = getattr(score, 'value', float('nan'))
    if score is None or grade is None or value != value:      # value != value ⇒ NaN ⇒ nothing assessed
        return SignalOutcome('reliability', GateVerdict.WARN,
                             'reliability could not be assessed — the number is not a passing one by default.')
    floor = _GRADE_RANK.get(requirement.min_reliability, -1)
    if grade == 'unreliable':
        return SignalOutcome('reliability', GateVerdict.DOWNGRADE,
                             'reliability is UNRELIABLE — do not report the value at face value.')
    if _GRADE_RANK.get(grade, 0) < floor:
        return SignalOutcome('reliability', GateVerdict.WARN,
                             f'reliability grade {grade!r} is below the required {requirement.min_reliability!r}.')
    return SignalOutcome('reliability', GateVerdict.OK, f'reliability grade {grade!r}.')


def evaluate_quality(objects, requirement: QualityRequirement, *, context=None) -> GateResult:
    """Compose the preconditions ``requirement`` declares into a single :class:`GateResult`.

    ``context`` supplies the signal inputs (``pixel_size_ok``, ``calibration_verdict`` or
    ``calibration_curve`` + ``image_metadata``, ``reliability_score`` or the raw reliability signals). Only
    the requested signals are evaluated; an unrequested one is not consulted. The overall verdict is the
    WORST signal outcome, and every outcome is returned for inspection. ``objects`` is accepted for callers
    that pass the measured table/mask; this composer reads only ``context`` and mutates nothing."""
    context = dict(context or {})
    signals = []
    if requirement.needs_pixel_size:
        signals.append(_pixel_size_signal(context))
    if requirement.needs_calibration:
        signals.append(_calibration_signal(context))
    if requirement.min_reliability is not None:
        signals.append(_reliability_signal(context, requirement))
    overall = GateVerdict.OK
    for s in signals:
        if s.verdict.rank > overall.rank:
            overall = s.verdict
    return GateResult(verdict=overall, signals=tuple(signals))
