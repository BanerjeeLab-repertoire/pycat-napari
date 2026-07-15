"""
question_engine.py
==================

The adaptive question layer — PDF1's "Scientific Navigator" that asks "only 2–4
adaptive questions before presenting a recommended pipeline."

The critical design choice (stress-test failure mode #6): **questions are
derived from the registry, not hand-authored as a parallel tree.** A static
91-branch question tree would rot the moment a module changes. Here, the set of
answerable questions is computed from (a) what the intent still lacks and (b)
which ``requires_context`` keys the currently-relevant modules need. A thin
template map supplies human phrasing and controlled responses; the *structure*
comes from the modules.

Each question carries a one-sentence rationale — PDF2's "(?) this determines
whether tracking and dynamic analyses are available."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .capabilities import Observable, Question
from .context import AnalysisContext, Source
from .contracts import AnalysisIntent
from .registry import ModuleRegistry


@dataclass
class Choice:
    label: str
    value: Any
    hint: str = ""


@dataclass
class QuestionSpec:
    id: str
    prompt: str
    choices: List[Choice]
    rationale: str
    kind: str                     # "intent" | "context" | "confirm"
    writes_key: Optional[str] = None       # context key it fills
    sets_intent: Optional[str] = None      # intent field it fills
    multi: bool = False
    gain: float = 0.0             # information gain (modules unblocked) — filled by engine


# --------------------------------------------------------------------------- #
# Human phrasing for context requirements. Structure (which of these to ask,   #
# and when) is decided by the engine from module needs; this map only supplies #
# words + controlled responses. Reconcile with the ontology sheet later.       #
# --------------------------------------------------------------------------- #
_CONTEXT_TEMPLATES: Dict[str, QuestionSpec] = {
    "time_series": QuestionSpec(
        id="q.time_series", prompt="Is your sample a time series?",
        choices=[Choice("Yes", True), Choice("No", False)],
        rationale="Determines whether tracking and dynamic analyses are available.",
        kind="context", writes_key="axes"),
    "two_channels": QuestionSpec(
        id="q.two_channels", prompt="Do you have two or more channels to relate?",
        choices=[Choice("Yes", 2), Choice("No", 1)],
        rationale="Colocalization needs at least two channels.",
        kind="context", writes_key="channels"),
    "fluorescence": QuestionSpec(
        id="q.modality", prompt="What microscopy modality is this?",
        choices=[Choice("Fluorescence", "fluorescence"), Choice("Brightfield", "brightfield"),
                 Choice("Phase", "phase"), Choice("DIC", "dic")],
        rationale="Modality changes which preprocessing is appropriate.",
        kind="context", writes_key="modality"),
    "calibrated": QuestionSpec(
        id="q.voxel", prompt="Do you have a pixel/voxel size (calibration)?",
        choices=[Choice("Yes", True), Choice("No", None)],
        rationale="Physical measurements (diffusion, size in µm) require calibration.",
        kind="context", writes_key="voxel_size"),
    "z_stack": QuestionSpec(
        id="q.zstack", prompt="Is this a 3D (z-stack) acquisition?",
        choices=[Choice("Yes", True), Choice("No", False)],
        rationale="Enables 3D segmentation and volumetric measures.",
        kind="context", writes_key="axes"),
}

# Root scientific-goal question (PDF1 Stage 1: "What are you trying to learn?").
# Each broad goal maps to a starting observable set the planner can refine.
_GOAL_QUESTION = QuestionSpec(
    id="q.goal", prompt="What are you trying to learn?",
    choices=[
        Choice("Quantify structures inside cells", [Observable.COUNT.value, Observable.SIZE.value,
               Observable.SHAPE.value], "morphology / organisation"),
        Choice("How something changes over time", [Observable.MOTION.value, Observable.FUSION.value,
               Observable.COARSENING.value], "dynamics"),
        Choice("Whether two things are spatially related", [Observable.COLOCALIZATION.value],
               "colocalization"),
        Choice("Measure phase separation / material properties",
               [Observable.DIFFUSION.value, Observable.PARTITIONING.value, Observable.VISCOSITY.value],
               "condensate physics"),
        Choice("Analyze unlabeled samples", [Observable.MORPHOLOGY.value], "brightfield"),
        Choice("I don't know what's interesting yet", [], "exploratory"),
    ],
    rationale="Fixes the scientific goal so PyCAT can work backward to a workflow.",
    kind="intent", sets_intent="observables", multi=False)


class QuestionEngine:
    def __init__(self, registry: ModuleRegistry):
        self.registry = registry
        # index: context key -> how many modules need it (information gain)
        self._need_count: Dict[str, int] = {}
        for m in registry.all():
            for c in m.requires_context:
                self._need_count[c] = self._need_count.get(c, 0) + 1

    # ------------------------------------------------------------------ #
    def next_questions(self, intent: AnalysisIntent, ctx: AnalysisContext,
                       max_questions: int = 4) -> List[QuestionSpec]:
        """Return the most valuable questions to ask *right now*, ranked."""
        out: List[QuestionSpec] = []

        # 1. If the goal isn't set, that dominates everything else.
        if not intent.observables and not intent.question:
            return [_GOAL_QUESTION]

        # 2. Which context keys do the currently-relevant modules need?
        relevant = self._relevant_modules(intent)
        needed_keys: Dict[str, int] = {}
        for m in relevant:
            for c in m.requires_context:
                if ctx.context_requirement(c) is None:   # unknown -> worth asking
                    needed_keys[c] = needed_keys.get(c, 0) + 1

        for key, local_gain in sorted(needed_keys.items(), key=lambda kv: (-kv[1], kv[0])):
            spec = _CONTEXT_TEMPLATES.get(key)
            if spec is None:
                continue
            q = QuestionSpec(**{**spec.__dict__})
            q.gain = float(local_gain)
            out.append(q)

        # 3. Confirm low-confidence inferred facts (failure mode #5) — lower priority.
        for key, fact in ctx.low_confidence_fields().items():
            out.append(QuestionSpec(
                id=f"q.confirm.{key}", prompt=f"I inferred {key} = {fact.value!r}. Is that right?",
                choices=[Choice("Yes", fact.value), Choice("No / let me set it", None)],
                rationale=f"Inferred from {fact.source.value} at {fact.confidence:.0%} confidence.",
                kind="confirm", writes_key=key, gain=0.1))

        return out[:max_questions]

    # ------------------------------------------------------------------ #
    def apply(self, spec: QuestionSpec, value: Any,
              intent: AnalysisIntent, ctx: AnalysisContext) -> None:
        """Fold an answer back into intent/context (PDF2: every answer updates
        the global context)."""
        if spec.sets_intent:
            cur = getattr(intent, spec.sets_intent)
            if isinstance(cur, list) and isinstance(value, list):
                setattr(intent, spec.sets_intent, list(dict.fromkeys(cur + value)))
            else:
                setattr(intent, spec.sets_intent, value)
        if spec.writes_key and value is not None:
            # special-case axes questions: they set a list membership, not a scalar
            if spec.writes_key == "axes" and isinstance(value, bool):
                axes = set(ctx.get("axes") or [])
                token = "time" if spec.id == "q.time_series" else "z"
                axes.add(token) if value else axes.discard(token)
                ctx.set("axes", sorted(axes), source=Source.USER)
            else:
                ctx.set(spec.writes_key, value, source=Source.USER)

    # ------------------------------------------------------------------ #
    def _relevant_modules(self, intent: AnalysisIntent):
        rel = []
        for obs in intent.observables:
            rel += self.registry.measuring(obs)
        if intent.question:
            rel += self.registry.answering(intent.question)
        # de-dup
        seen, uniq = set(), []
        for m in rel:
            if m.name not in seen:
                seen.add(m.name)
                uniq.append(m)
        return uniq


# --------------------------------------------------------------------------- #
# Hybrid engine: curated scientific tree  +  registry-derived context.        #
# --------------------------------------------------------------------------- #
class HybridQuestionEngine:
    """Two question classes, kept distinct (review #2/#3):

    * **Scientific branching** comes from the curated Q001–Q060 tree
      (``ScientificTree``). A broad answer selects a *branch*; observables are
      set only when a leaf is reached — so "change over time" no longer commits
      the user to motion+fusion+coarsening at once.
    * **Missing-context questions** come from the module registry, exactly as
      ``QuestionEngine`` does — but only *after* the scientific goal is fixed.

    The two never compete: the tree runs first and sets ``intent.observables``;
    the registry engine then fills whatever context the chosen operations still
    need.
    """

    def __init__(self, registry: ModuleRegistry, tree=None):
        from .scientific_tree import ScientificTree, TreeState
        self.registry = registry
        self.tree = tree if tree is not None else ScientificTree()
        self._ctx_engine = QuestionEngine(registry)
        self._TreeState = TreeState
        self.state = TreeState()

    # ------------------------------------------------------------------ #
    def _tree_question(self) -> Optional[QuestionSpec]:
        node = self.tree.node(self.state.qid)
        if node is None:
            return None
        choices = [Choice(r["response"], r["response"], hint=r.get("outcome", ""))
                   for r in node.responses]
        why = node.responses[0].get("why", "") if node.responses else ""
        return QuestionSpec(id=f"tree.{node.qid}", prompt=node.question, choices=choices,
                            rationale=why or "Selects the scientific branch.",
                            kind="scientific")

    def next_question(self, intent: AnalysisIntent, ctx: AnalysisContext) -> Optional[QuestionSpec]:
        """The single next question to ask, or None when ready to plan."""
        if not self.state.done:
            return self._tree_question()
        # scientific goal fixed -> hand off to registry-derived context questions
        qs = self._ctx_engine.next_questions(intent, ctx, max_questions=1)
        return qs[0] if qs else None

    def answer(self, spec: QuestionSpec, value, intent: AnalysisIntent,
               ctx: AnalysisContext) -> None:
        if spec.kind == "scientific":
            self.tree.advance(self.state, value)
            if self.state.done:
                # commit the reached observables + target into the intent
                for o in self.state.observables:
                    if o not in intent.observables:
                        intent.observables.append(o)
                if intent.target is None and self.state.target:
                    intent.target = self.state.target
                intent.question = intent.question or " > ".join(
                    r for _, r in self.state.trail)
        else:
            self._ctx_engine.apply(spec, value, intent, ctx)

    @property
    def scientific_done(self) -> bool:
        return self.state.done
