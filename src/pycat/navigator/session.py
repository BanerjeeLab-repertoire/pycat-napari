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
        self._plan = None       # the last compiled plan — re-gated (not recompiled) on a viewer state change

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
        """Backward-chain the current intent/context into a runnable, quality-gated :class:`Plan`. The plan is
        retained so a viewer state change can RE-GATE it (see :meth:`regate`) without recompiling."""
        self._plan = self.planner.compile(self.intent, self.ctx, pins=dict(self.pins))
        return self._plan

    def regate(self):
        """Re-evaluate the retained plan's gates/gaps against the CURRENT ``self.ctx`` (which a viewer event
        has just refreshed), without recompiling the structure — the cheap path that makes the plan track
        loaded data. Returns the re-gated plan, or ``None`` if nothing has been compiled yet."""
        from .planner import regate
        return regate(self._plan, self.ctx) if self._plan is not None else None

    def run_blocked_reason(self):
        """A short, user-language reason the plan cannot run yet, or ``None`` when it is runnable — so the run
        action is never a dead control. Derived from the retained plan's gaps against ``self.ctx``: no data →
        'Load an image first'; a calibration gap → set the scale; else the first concrete blocker."""
        plan = self._plan
        if plan is None:
            return "Answer the questions to build a plan."
        if not self.ctx.known("channels") and not self.ctx.known("axes") and not self.ctx.known("time_points"):
            return "Load an image first."
        for gap in plan.gaps:
            if gap.key in ("calibrated",) or "pixel" in gap.key or "calib" in gap.key:
                return "Set the pixel size (scale) — this measurement needs a calibrated image."
        blockers = plan.blockers()
        if blockers:
            return blockers[0]
        return None if plan.is_executable else "This plan cannot run yet."

    def pin(self, representation_kind: str, module_name: str) -> None:
        """Change which module provides ``representation_kind`` (the editable-plan mechanism). The caller
        recompiles afterwards, so the swap is re-validated against every downstream contract."""
        self.pins[representation_kind] = module_name

    def unpin(self, representation_kind: str) -> None:
        self.pins.pop(representation_kind, None)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def context_from_session(central_manager, ctx: Optional[AnalysisContext] = None) -> AnalysisContext:
    """Refresh an :class:`AnalysisContext` from the loaded image's metadata so the navigator's plan re-gates
    against live state (the fix for a plan that never tracked the data). **Metadata suggests, the user
    decides:** a fact already answered by the USER is never overwritten (only source=METADATA facts are
    (re)set). And it **never guesses dimensionality from array shape alone** — ``axes`` is set only from an
    explicit ``dimension_order``; a bare plane count leaves the Z/T question genuinely open (to ask), because a
    3-plane stack could be Z, T, or channels. Returns ``ctx``."""
    from .context import Source        # AnalysisContext is imported at module scope (used in the annotation)
    ctx = ctx if ctx is not None else AnalysisContext()
    dr = getattr(getattr(central_manager, "active_data_class", None), "data_repository", None)
    get = dr.get if hasattr(dr, "get") else (lambda k, d=None: d)
    common = ((get("file_metadata") or {}) or {}).get("common") or {}

    def _set(key, value):
        f = ctx.fact(key)
        if f is not None and f.source is Source.USER:
            return                          # a user answer outranks metadata — never overwrite
        ctx.set(key, value, source=Source.METADATA)

    if _num(common.get("n_channels")) is not None:
        _set("channels", int(common["n_channels"]))
    if _num(common.get("n_timepoints")) is not None:
        _set("time_points", int(common["n_timepoints"]))

    order = common.get("dimension_order")
    if isinstance(order, str) and order:        # an EXPLICIT layout — never inferred from a plane count
        up = order.upper()
        axes = []
        if (_num(common.get("n_timepoints")) or 1) > 1 and "T" in up:
            axes.append("time")
        if (_num(common.get("n_z")) or 1) > 1 and "Z" in up:
            axes.append("z")
        _set("axes", axes)

    px = _num(get("microns_per_pixel"))
    if px is None:
        sq = _num(get("microns_per_pixel_sq"))
        px = sq ** 0.5 if sq else None
    if px is None:
        px = _num(common.get("pixel_size_um"))
    if px is not None and abs(px - 1.0) > 1e-9:  # a real, non-sentinel calibration
        _set("pixel_size", px)                   # the pixel-size validity gate (an assumption) reads this
        _set("voxel_size", px)                   # the 'calibrated' context requirement reads this

    if common.get("modality"):
        _set("modality", str(common["modality"]))
    return ctx


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
