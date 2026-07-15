"""
contracts.py
============

The information contract every PyCAT module advertises (PDF7:
"Module -> type -> information role -> input -> output -> purpose -> observable
-> prerequisites -> public API -> source", expressed as *semantic* contracts).

This is the heart of the capability-based architecture. A module never appears
in a menu; it appears in a *generated* workflow because its contract says it can
help answer the scientist's question.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional, Sequence

from .capabilities import Capability, InformationRole
from .context import AnalysisContext


# --------------------------------------------------------------------------- #
# Validity / assumption gates.                                                #
#                                                                             #
# The scientific core of the whole idea (PDF4: "validity/assumption gates").   #
# The subtlety the PDFs under-state (stress-test failure mode #4): some        #
# assumptions cannot be checked before running anything — you can't know the   #
# SNR is adequate until you've measured the SNR. So a gate resolves to one of  #
# three states, and an UNKNOWN gate may require a *probe* (a cheap measurement #
# module) before it can be decided.                                           #
# --------------------------------------------------------------------------- #
class GateStatus(str, Enum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNKNOWN = "unknown"   # needs a probe or user input before it can be decided


@dataclass
class Assumption:
    """A validity gate attached to a module.

    ``check`` inspects the context and returns a GateStatus. If it returns
    UNKNOWN and ``probe_observable`` is set, the planner knows a probe step that
    measures that observable would let the gate be decided at runtime.
    """
    id: str
    description: str
    check: Callable[[AnalysisContext], GateStatus]
    severity: str = "warning"          # "warning" | "blocker"
    probe_observable: Optional[str] = None
    rationale: str = ""                # one-sentence "why am I being asked this"

    def evaluate(self, ctx: AnalysisContext) -> GateStatus:
        return self.check(ctx)


@dataclass
class CostModel:
    """Rough runtime estimate so the widget can show 'Estimated runtime: 2 min'
    (PDF1). Cost is base_seconds + per_megapixel * size. Deliberately crude;
    calibration is a known cost (stress-test #11)."""
    base_seconds: float = 1.0
    per_megapixel: float = 0.0
    per_frame: float = 0.0

    def estimate(self, ctx: AnalysisContext) -> float:
        mp = (ctx.get("megapixels") or 1.0)
        frames = (ctx.get("time_points") or 1)
        return self.base_seconds + self.per_megapixel * mp + self.per_frame * frames


@dataclass
class ModuleContract:
    """Everything the workflow generator needs to know about a module without
    ever importing the module's implementation."""
    name: str
    info_role: InformationRole
    purpose: str = ""

    # product graph
    provides: List[Capability] = field(default_factory=list)
    requires_inputs: List[Capability] = field(default_factory=list)

    # context graph (facts about the data, answered by AnalysisContext / user)
    requires_context: List[str] = field(default_factory=list)

    # scientific mapping
    questions: List[str] = field(default_factory=list)     # controlled Question / free intents
    observables: List[str] = field(default_factory=list)   # controlled Observable values

    # validity
    assumptions: List[Assumption] = field(default_factory=list)

    # planning hints
    propagates_tags: frozenset = field(default_factory=frozenset)  # qualifier tags that flow in->out
    preference: float = 0.5   # 0..1, higher = preferred provider when several compete
    cost: CostModel = field(default_factory=CostModel)

    # bookkeeping (from PDF7 table)
    public_api: str = ""
    source: str = ""

    def can_answer(self, question: str) -> bool:
        return question in self.questions

    def measures(self, observable: str) -> bool:
        return observable in self.observables

    def provides_capability(self, required: Capability) -> Optional[Capability]:
        """Return the concrete provided capability that satisfies ``required``,
        or None."""
        for p in self.provides:
            if required.satisfied_by(p):
                return p
        return None

    def __repr__(self) -> str:
        return f"<Module {self.name} [{self.info_role.value}]>"


@dataclass
class AnalysisIntent:
    """PDF3's proposed central object: the analysis *intent*, not a method call.

        intent = {"system": "cells",
                  "question": "How do condensates change after stimulation?",
                  "observables": ["fusion", "coarsening", "size"],
                  "time_series": True}

    Everything downstream (workflow, gates, outputs) derives from this.
    """
    question: str = ""
    observables: List[str] = field(default_factory=list)
    target: Optional[str] = None            # biological target, e.g. "condensate"
    desired_outputs: List[str] = field(default_factory=list)
    notes: str = ""
