"""
gates.py
========

Helpers for building and *staging* validity gates — the scientific safety layer
that PDF4 calls "validity/assumption gates" and PDF2 renders as the confidence
sidebar ("Confidence 85% / ⚠ low temporal sampling").

The important idea the PDFs gloss over (stress-test failure mode #4): a gate can
be **static** (decidable from context right now, e.g. "voxel size is known") or
a **probe gate** (needs a measurement before it can be decided, e.g. "SNR is
adequate" — you must measure SNR first). This module distinguishes them and
tells the planner which probes to insert so gating is *staged*, not pretended to
be instantaneous.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from .context import AnalysisContext
from .contracts import Assumption, GateStatus


# -- constructors ----------------------------------------------------------- #
def static_gate(id: str, description: str, predicate: Callable[[AnalysisContext], Optional[bool]],
                severity: str = "warning", rationale: str = "") -> Assumption:
    """A gate decidable from context. ``predicate`` returns True/False/None
    (None -> UNKNOWN, treated as 'ask or probe')."""
    def _check(ctx: AnalysisContext) -> GateStatus:
        r = predicate(ctx)
        if r is True:
            return GateStatus.SATISFIED
        if r is False:
            return GateStatus.VIOLATED
        return GateStatus.UNKNOWN
    return Assumption(id=id, description=description, check=_check,
                      severity=severity, rationale=rationale)


def probe_gate(id: str, description: str, observable: str,
               threshold_key: str, min_value: float,
               severity: str = "warning", rationale: str = "") -> Assumption:
    """A gate that can only be decided once ``observable`` has been measured and
    written back into the context under ``threshold_key``. Until then it is
    UNKNOWN and advertises ``probe_observable`` so the planner can insert a probe
    (typically a QC module)."""
    def _check(ctx: AnalysisContext) -> GateStatus:
        if not ctx.known(threshold_key):
            return GateStatus.UNKNOWN
        return GateStatus.SATISFIED if ctx.get(threshold_key) >= min_value else GateStatus.VIOLATED
    return Assumption(id=id, description=description, check=_check, severity=severity,
                      probe_observable=observable, rationale=rationale)


# -- staging ---------------------------------------------------------------- #
@dataclass
class StagedGates:
    satisfied: List[Assumption]
    violated: List[Assumption]
    need_probe: List[Assumption]      # UNKNOWN + has a probe observable
    need_input: List[Assumption]      # UNKNOWN + no probe -> ask the user

    def confidence(self) -> float:
        """A crude overall confidence score for the sidebar. Violated blockers
        drive it to zero; warnings and unknowns discount it."""
        total = len(self.satisfied) + len(self.violated) + len(self.need_probe) + len(self.need_input)
        if total == 0:
            return 1.0
        if any(a.severity == "blocker" for a in self.violated):
            return 0.0
        penalty = 0.5 * len(self.violated) + 0.25 * len(self.need_probe) + 0.15 * len(self.need_input)
        return max(0.0, 1.0 - penalty / total)


def stage_gates(assumptions: List[Assumption], ctx: AnalysisContext) -> StagedGates:
    sat, vio, probe, ask = [], [], [], []
    for a in assumptions:
        status = a.evaluate(ctx)
        if status is GateStatus.SATISFIED:
            sat.append(a)
        elif status is GateStatus.VIOLATED:
            vio.append(a)
        elif a.probe_observable:
            probe.append(a)
        else:
            ask.append(a)
    return StagedGates(sat, vio, probe, ask)


def required_probe_observables(assumptions: List[Assumption], ctx: AnalysisContext) -> List[str]:
    """Observables the plan must measure up-front so its UNKNOWN gates become
    decidable. The planner can prepend a QC/probe step that provides these."""
    staged = stage_gates(assumptions, ctx)
    obs = []
    for a in staged.need_probe:
        if a.probe_observable and a.probe_observable not in obs:
            obs.append(a.probe_observable)
    return obs
