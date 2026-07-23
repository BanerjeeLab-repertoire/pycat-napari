"""**Drive the Navigator engine from a UI — ask → answer → compile — and make the plan renderable.**

The engine (`HybridQuestionEngine`), the planner (`Planner`), and the quality gates all exist and are tested;
what was missing is a thin, Qt-free surface a dock can drive and a way to turn a compiled `Plan` — with its
quality-gate verdicts — into a flat list a widget can render. That is this module (navigator increment 3).

`NavigatorSession` holds one run's engine + planner + intent + context: `next_question()` → `answer()` until
`is_ready()`, then `compile_plan()`. Editing is a **pin + recompile** — the planner re-validates every
contract, so an edited plan is as trustworthy as a generated one. `plan_rows()` renders a plan as ordered
rows with each step's gate verdict INLINE: a *blocked* step names why it cannot run, a *downgraded* step names
the caveat but stays runnable, and an *unknown* names the QC probe that will decide it — the whole point of
the increment-2 gate report is wasted if the plan is shown without those reasons."""
from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Optional

from .contracts import AnalysisIntent, GateStatus
from .context import AnalysisContext


class NavigatorSession:
    """One guided-analysis run. Drives the existing engine/planner; owns the intent + context it fills.

    ``registry``/``ctx`` are injectable for tests; by default a fresh operation registry and an empty context
    are used. The engine loads the scientific question tree from the shipped workbooks (needs ``openpyxl``)."""

    def __init__(self, registry=None, ctx: Optional[AnalysisContext] = None):
        from .op_catalog import build_operation_registry
        from .question_engine import HybridQuestionEngine
        from .planner import Planner

        self.registry = registry if registry is not None else build_operation_registry()
        self.engine = HybridQuestionEngine(self.registry)
        self.planner = Planner(self.registry)
        self.intent = AnalysisIntent()
        self.ctx = ctx if ctx is not None else AnalysisContext()
        self.pins: dict = {}

    # ── the question loop ──────────────────────────────────────────────────────
    def next_question(self):
        """The single next question to ask, or ``None`` when the intent is complete and a plan can compile."""
        return self.engine.next_question(self.intent, self.ctx)

    def answer(self, spec, value) -> None:
        """Record the user's choice for ``spec`` (advances the scientific tree or fills a context key)."""
        self.engine.answer(spec, value, self.intent, self.ctx)

    def is_ready(self) -> bool:
        """True when there are no more questions — ``compile_plan`` will produce a plan."""
        return self.next_question() is None

    # ── plan + editing (a pin is an edit; recompile re-validates) ───────────────
    def compile_plan(self):
        """Backward-chain the current intent/context into a runnable, quality-gated :class:`Plan`."""
        return self.planner.compile(self.intent, self.ctx, pins=dict(self.pins))

    def pin(self, representation_kind: str, module_name: str) -> None:
        """Change which module provides ``representation_kind`` (the editable-plan mechanism). The caller
        recompiles afterwards, so the swap is re-validated against every downstream contract."""
        self.pins[representation_kind] = module_name

    def unpin(self, representation_kind: str) -> None:
        self.pins.pop(representation_kind, None)


# ── the render model: a plan as ordered rows, quality-gate verdicts inline ───────────────────────────

@dataclasses.dataclass(frozen=True)
class GateNote:
    """One quality-gate verdict on a step. ``kind`` is ``'blocked'`` (a hard precondition VIOLATED — the step
    cannot run), ``'downgraded'`` (a soft gate VIOLATED — runnable, but the result is caveated), or
    ``'unknown'`` (an unassessed signal — a probe will decide it). ``reason`` is the human sentence."""
    kind: str
    reason: str
    gate_id: str = ""


@dataclasses.dataclass(frozen=True)
class PlanRow:
    """One row a plan widget renders, in execution order. ``kind`` is ``'probe'`` (a QC step the planner
    prepended) or ``'step'``. ``state`` is ``'ok' | 'downgraded' | 'blocked' | 'probe'``; ``runnable`` is
    False only when a blocker gate fired. ``gates`` are the inline verdicts (empty when the step is clean)."""
    name: str
    kind: str
    state: str
    runnable: bool
    reason: str
    gates: tuple = ()


_PROBE_REASON = "Quality probe — measures a signal a later step needs before its result can be trusted."


def plan_rows(plan) -> list:
    """Render ``plan`` as an ordered list of :class:`PlanRow` — QC probes first, then each step with its
    quality-gate verdicts folded in from ``plan.gate_report``. This is what makes increment 2's gate report
    visible: a plan shown without the reasons reduces the Navigator to a step generator."""
    by_module = defaultdict(list)
    for module_name, assumption, status in plan.gate_report:
        by_module[module_name].append((assumption, status))

    rows = []
    for probe in plan.probes:
        rows.append(PlanRow(name=probe.name, kind="probe", state="probe", runnable=True,
                            reason=_PROBE_REASON, gates=()))

    for step in plan.steps:
        notes = []
        blocked = downgraded = False
        for assumption, status in by_module.get(step.name, ()):
            reason = assumption.description or assumption.rationale or assumption.id
            if status is GateStatus.VIOLATED and assumption.severity == "blocker":
                blocked = True
                notes.append(GateNote("blocked", reason, assumption.id))
            elif status is GateStatus.VIOLATED:                    # warning severity → downgrade
                downgraded = True
                notes.append(GateNote("downgraded", reason, assumption.id))
            elif status is GateStatus.UNKNOWN:
                probe = assumption.probe_observable or "a QC measurement"
                notes.append(GateNote("unknown", f"unassessed — a probe measures {probe} first", assumption.id))

        state = "blocked" if blocked else ("downgraded" if downgraded else "ok")
        rows.append(PlanRow(name=step.name, kind="step", state=state, runnable=not blocked,
                            reason=step.reason, gates=tuple(notes)))
    return rows
