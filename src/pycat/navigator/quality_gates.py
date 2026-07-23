"""**The planner consults the quality gate** (navigator wiring increment 2).

`utils/quality_gate.evaluate_quality` composes calibration, pixel size, and reliability into a
block/warn/downgrade/ok verdict with reasons — but nothing asked it. This is the caller: it turns a
measurement operation's :class:`~pycat.utils.quality_gate.QualityRequirement` into ordinary planner
:class:`~pycat.navigator.contracts.Assumption` gates, so the planner's EXISTING machinery does the rest —
no parallel reporting path, no change to ``compile``:

- ``compile`` step 4 already evaluates every ``step.module.assumptions`` into ``gate_report``;
- ``is_executable`` / ``blockers`` already fail a plan on a VIOLATED **blocker** gate;
- ``required_probe_observables`` already prepends a QC probe for an UNKNOWN gate that names one.

So a measurement's quality conditions become one gate per signal, each with the right severity, and the
four verdicts map onto the three gate states the planner already understands:

| GateVerdict | GateStatus | on a *blocker* signal (pixel size / calibration) | on a *warning* signal (reliability) |
|---|---|---|---|
| OK        | SATISFIED | clear                                    | clear |
| BLOCK     | VIOLATED  | **not runnable** — reason in ``blockers()`` | (never emitted) |
| WARN      | UNKNOWN   | runnable, reported; probes if a probe is named | runnable; **prepends a QC probe** (reliability names one) |
| DOWNGRADE | VIOLATED  | (never emitted)                          | runnable, reported (warning severity ⇒ not a blocker) |

**Unknown is not ok** — an unassessed signal becomes an UNKNOWN gate (a probe or a caveat), never a silent
pass, exactly as ``evaluate_quality`` intends.
"""
from __future__ import annotations

from typing import List

from pycat.utils.quality_gate import (GateVerdict, QualityRequirement, _calibration_signal,
                                       _pixel_size_signal, _reliability_signal)

from .contracts import Assumption, GateStatus


def gate_context(ctx) -> dict:
    """Map an :class:`AnalysisContext` onto the ``context`` dict ``evaluate_quality``'s signals read.

    Only facts that are actually present are forwarded, so an *unasserted* signal stays unassessed
    (``None``) rather than being defaulted to a passing value — the gate treats that as a WARN, never an OK.
    """
    g: dict = {}
    if ctx.known('pixel_size'):
        ps = ctx.get('pixel_size')
        try:
            g['pixel_size_ok'] = ps is not None and float(ps) > 0
        except (TypeError, ValueError):
            g['pixel_size_ok'] = False
    else:
        # fall back to the context's own 'calibrated' predicate (a set voxel size ⇒ a physical scale)
        g['pixel_size_ok'] = ctx.context_requirement('calibrated')   # True / False / None
    for key in ('calibration_verdict', 'calibration_curve', 'image_metadata', 'reliability_score',
                'image_qc', 'object_flags', 'calibration', 'sensitivity', 'benchmark'):
        if ctx.known(key):
            g[key] = ctx.get(key)
    return g


def _status(verdict: GateVerdict) -> GateStatus:
    """Map a quality verdict onto the planner's three gate states (see the module table).

    BLOCK ⇒ VIOLATED (only a *blocker*-severity signal emits BLOCK, so this blocks the plan); WARN ⇒
    UNKNOWN (runnable, reported, and a probe if the gate names one — never a silent pass); DOWNGRADE ⇒
    VIOLATED (only reliability, a *warning*-severity gate, emits it, so it is reported but does not block);
    OK ⇒ SATISFIED."""
    if verdict is GateVerdict.OK:
        return GateStatus.SATISFIED
    if verdict is GateVerdict.BLOCK:
        return GateStatus.VIOLATED
    if verdict is GateVerdict.WARN:
        return GateStatus.UNKNOWN
    return GateStatus.VIOLATED           # DOWNGRADE


def quality_assumptions(requirement: QualityRequirement, op_name: str) -> List[Assumption]:
    """Build the planner gates that enforce ``requirement`` — one per requested signal.

    A hard precondition (pixel size, calibration) is a **blocker** — unmet, the plan is not runnable and
    ``blockers()`` names why. Reliability is a **warning** that names a QC probe (``snr``), so an
    unassessed reliability prepends a probe rather than passing or guessing.
    """
    out: List[Assumption] = []

    if requirement.needs_pixel_size:
        out.append(Assumption(
            id=f'quality:pixel_size:{op_name}',
            description='needs a real pixel size — without it a physical-unit result is pixels labelled as '
                        'microns; set the scale first',
            check=lambda ctx: _status(_pixel_size_signal(gate_context(ctx)).verdict),
            severity='blocker',
            rationale='A µm / µm² / viscosity number is meaningless without a set pixel size.'))

    if requirement.needs_calibration:
        out.append(Assumption(
            id=f'quality:calibration:{op_name}',
            description='needs a valid calibration curve — a concentration / ΔG cannot be confirmed without '
                        'one',
            check=lambda ctx: _status(_calibration_signal(gate_context(ctx)).verdict),
            severity='blocker',
            rationale='A concentration / ΔG depends on a validated calibration for this image.'))

    if requirement.min_reliability is not None:
        req = requirement
        out.append(Assumption(
            id=f'quality:reliability:{op_name}',
            description=f"reliability should reach grade '{requirement.min_reliability}'; below it the "
                        "number is reduced-confidence, and unassessed it must be probed, not assumed",
            check=lambda ctx, r=req: _status(_reliability_signal(gate_context(ctx), r).verdict),
            severity='warning',
            probe_observable='snr',
            rationale='Trust in the reported number depends on its assessed reliability.'))

    return out
