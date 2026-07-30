"""
planner.py
==========

The workflow *compiler*. Turns an :class:`AnalysisIntent` + an
:class:`AnalysisContext` into a generated, editable workflow — by backward
chaining over module contracts, exactly like a package manager resolving
dependencies (PDF3).

The PDFs present this as "almost trivial." It is not (see the stress-test doc).
This implementation confronts the three things that make it non-trivial:

* **Non-unique providers** (#1). Many modules can ``provide`` the same product
  (segmentation: Cellpose vs watershed vs threshold). We resolve ambiguity with
  an explicit, deterministic *selection policy* (preference, then cost, then
  name) and allow the user to *pin* a choice — that is what "editing the
  generated workflow" means, and pins simply recompile.

* **Cycles / re-entry** (#3). Real pipelines loop (segment -> manual refine ->
  re-measure). A naive DAG resolver would either loop forever or refuse. We
  detect cycles on the resolution stack and skip providers that would close one.

* **Context vs product requirements**. ``requires_context`` (is this a time
  series?) is answered by the context or becomes a *question*; only
  ``requires_inputs`` become upstream steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .capabilities import Capability, Representation
from .context import AnalysisContext
from .contracts import (Assumption, AnalysisIntent, GateStatus, ModuleContract)
from .registry import ModuleRegistry


# --------------------------------------------------------------------------- #
# Plan data structures                                                        #
# --------------------------------------------------------------------------- #
@dataclass
class PlanStep:
    module: ModuleContract
    produces: Capability
    inputs: List[Capability] = field(default_factory=list)
    reason: str = ""
    depends_on: List[str] = field(default_factory=list)   # upstream module names

    @property
    def name(self) -> str:
        return self.module.name


@dataclass
class ContextGap:
    """A ``requires_context`` that the data/user hasn't answered -> a question."""
    key: str
    required_by: str
    status: GateStatus  # UNKNOWN (ask) or VIOLATED (path invalid)


@dataclass
class Plan:
    intent: AnalysisIntent
    steps: List[PlanStep] = field(default_factory=list)
    gaps: List[ContextGap] = field(default_factory=list)
    unresolved: List[Tuple[Capability, str]] = field(default_factory=list)
    gate_report: List[Tuple[str, Assumption, GateStatus]] = field(default_factory=list)
    bindings: list = field(default_factory=list)   # existing layers reused instead of planned
    probes: List[PlanStep] = field(default_factory=list)  # QC probes prepended for UNKNOWN gates

    @property
    def ordered_modules(self) -> List[str]:
        return [s.name for s in self.steps]

    @property
    def reused_layers(self) -> List[str]:
        return [b.layer_name for b in self.bindings]

    @property
    def is_executable(self) -> bool:
        """No missing products and no VIOLATED context requirement or blocker gate."""
        if self.unresolved:
            return False
        if any(g.status is GateStatus.VIOLATED for g in self.gaps):
            return False
        if any(status is GateStatus.VIOLATED and a.severity == "blocker"
               for _, a, status in self.gate_report):
            return False
        return True

    def estimated_seconds(self, ctx: AnalysisContext) -> float:
        return sum(s.module.cost.estimate(ctx) for s in self.steps)

    def blockers(self) -> List[str]:
        out = []
        out += [f"missing product: {cap} (needed by {why})" for cap, why in self.unresolved]
        out += [f"unmet data requirement: {g.key} (for {g.required_by})"
                for g in self.gaps if g.status is GateStatus.VIOLATED]
        out += [f"assumption violated: {a.id} on {mod}"
                for mod, a, status in self.gate_report
                if status is GateStatus.VIOLATED and a.severity == "blocker"]
        return out

    def open_questions(self) -> List[ContextGap]:
        return [g for g in self.gaps if g.status is GateStatus.UNKNOWN]


def regate(plan: "Plan", ctx: AnalysisContext) -> "Plan":
    """Re-evaluate a COMPILED plan's context gaps and validity gates against a fresh ``ctx``, WITHOUT
    recompiling the plan structure — the steps and which module provides what are fixed, only the verdicts
    recompute. This is what lets loading data or setting a calibration flip a step from unknown/blocked to
    satisfied and re-enable the run action, cheaply, on a viewer event (the navigator-UX bug: the plan was
    evaluated once at compile and never tracked state). Recompiling instead could re-select modules (cost
    tie-breaks read ``ctx``) and silently change the plan under the user — hence re-gate, don't recompile.
    Mutates and returns ``plan``."""
    plan.gaps = []
    for step in plan.steps:
        for ckey in step.module.requires_context:
            status = ctx.context_requirement(ckey)
            if status is True:
                continue
            plan.gaps.append(ContextGap(
                ckey, step.module.name, GateStatus.VIOLATED if status is False else GateStatus.UNKNOWN))
    plan.gate_report = [(name, a, a.evaluate(ctx)) for (name, a, _old) in plan.gate_report]
    return plan


# --------------------------------------------------------------------------- #
# The planner                                                                 #
# --------------------------------------------------------------------------- #
SelectionPolicy = Callable[[List[ModuleContract], AnalysisContext], ModuleContract]


def _context_score(c: ModuleContract, ctx: AnalysisContext) -> int:
    """How well ``c``'s context requirements match the situation, ranking a provider against its rivals:
      +1  every requirement is SATISFIED and there is at least one — a context-matched SPECIALIST (a brightfield
          op on brightfield, a 3D op on a z-stack) that should beat a context-agnostic generic;
       0  NO requirements — a general op, applicable anywhere;
      -1  has requirements but one is UNKNOWN — a specialist whose context is unconfirmed; it ranks BELOW a
          no-gate general op (an unconfirmed time-series segmenter must not out-rank the plain one on a still-
          unknown context), but above one whose context is outright violated;
      -2  a requirement is VIOLATED (a brightfield op on fluorescence, a 3D op on a 2D image) — does not apply.
    This is what lets a `target:condensate` plan pick the general `subcellular_segment` on fluorescence yet
    `bf_segment` on brightfield, and a `target:cell` plan pick `cellpose_3d` on a z-stack yet `cellpose` on 2D,
    without a fragile per-op preference fudge. See the dormant-adapter fix, 2026-07-27."""
    reqs = list(getattr(c, "requires_context", []) or [])
    if not reqs:
        return 0
    results = []
    for key in reqs:
        try:
            results.append(ctx.context_requirement(key))
        except Exception:                  # broad-ok: optional_probe — an unknown gate key is treated as unknown
            results.append(None)
    if any(r is False for r in results):
        return -2
    if all(r is True for r in results):
        return 1
    return -1


def default_selection_policy(candidates: List[ModuleContract],
                             ctx: AnalysisContext) -> ModuleContract:
    """Deterministic: highest preference, then cheapest, then name.
    ``candidates`` arrives already sorted by (preference, name) from the
    registry; we refine by estimated cost as a tie-break within equal
    preference."""
    best = candidates[0]
    for c in candidates[1:]:
        if c.preference > best.preference:
            best = c
        elif c.preference == best.preference and c.cost.estimate(ctx) < best.cost.estimate(ctx):
            best = c
    return best


class Planner:
    def __init__(self, registry: ModuleRegistry,
                 selection_policy: SelectionPolicy = default_selection_policy):
        self.registry = registry
        self.select = selection_policy

    # ------------------------------------------------------------------ #
    def compile(self, intent: AnalysisIntent, ctx: AnalysisContext,
                pins: Optional[Dict[str, str]] = None,
                layer_resolver=None) -> Plan:
        """Backward-chain from the intent's observables to a runnable plan.

        ``pins`` maps a representation kind (e.g. ``"instance_labels"``) to a
        module name, letting the user override the auto-selected provider. This
        is the mechanism behind the editable widget: an edit is a pin + recompile
        (stress-test #7), which keeps every contract re-validated.

        ``layer_resolver`` (optional, a ``LayerResolverProtocol``) lets the
        planner **reuse an existing session layer** instead of planning a
        producer for it (review #8): before planning e.g. a segmenter, it asks
        "is there already a suitable condensate-labels layer?" In real PyCAT this
        is backed by ``pycat.utils.tag_resolver``; standalone it is
        ``adapters.InMemoryLayerResolver``.
        """
        pins = pins or {}
        self._layer_resolver = layer_resolver
        plan = Plan(intent=intent)
        memo: Dict[str, PlanStep] = {}          # keyed by str(capability) to dedupe shared deps
        stack: List[str] = []                    # module names currently resolving (cycle guard)

        # 1. terminal goals: a measure/interpret module per requested observable
        target_tags = frozenset([f"target:{intent.target}"]) if intent.target else frozenset()
        for obs in intent.observables:
            terminals = self.registry.measuring(obs)
            if not terminals:
                plan.unresolved.append((Capability(Representation.MEASUREMENT_TABLE, target_tags),
                                        f"observable:{obs}"))
                continue
            terminal = self._pick_terminal(terminals, ctx, pins, intent)
            goal = Capability(Representation.MEASUREMENT_TABLE, target_tags | frozenset([f"observable:{obs}"]))
            self._resolve_module(terminal, goal, f"answers '{obs}'", ctx, plan, memo, stack, pins)

        # 2. staged gating: prepend a QC probe for any UNKNOWN probe-gate so it
        #    can be decided at runtime (review #9). The probe op is resolved into
        #    the same memo, so shared deps (acquisition) dedupe and it orders
        #    first naturally.
        from .gates import required_probe_observables
        probe_names = set()
        assumptions = [a for st in memo.values() for a in st.module.assumptions]
        for obs in required_probe_observables(assumptions, ctx):
            providers = self.registry.measuring(obs)
            if not providers:
                continue
            pm = self._pick(providers, ctx, pins,
                            kind_hint=Representation.MEASUREMENT_TABLE.value)
            goal = Capability(Representation.MEASUREMENT_TABLE,
                              frozenset([f"observable:{obs}"]))
            self._resolve_module(pm, goal,
                                 f"QC probe so the gate on '{obs}' can be decided",
                                 ctx, plan, memo, stack, pins)
            probe_names.add(pm.name)

        # 3. order steps by dependency (stable topological sort)
        plan.steps = self._toposort(list(memo.values()))
        plan.probes = [s for s in plan.steps if s.name in probe_names]
        if plan.probes:
            # hoist probes to just after acquisition — they gate downstream steps
            infra = [s for s in plan.steps if s.module.info_role.value == "infrastructure"]
            rest = [s for s in plan.steps
                    if s.module.info_role.value != "infrastructure" and s.name not in probe_names]
            plan.steps = infra + plan.probes + rest

        # 4. evaluate validity gates across the whole plan
        for step in plan.steps:
            for a in step.module.assumptions:
                plan.gate_report.append((step.name, a, a.evaluate(ctx)))
        return plan

    # ------------------------------------------------------------------ #
    def _pick(self, candidates, ctx, pins, kind_hint) -> ModuleContract:
        pinned = pins.get(kind_hint)
        if pinned:
            for c in candidates:
                if c.name == pinned:
                    return c
        # DEPENDENCY (e.g. segmenter) selection is context-aware: a context-matched specialist (a brightfield
        # op on brightfield, a 3D op on a z-stack) beats a context-agnostic generic, and a context-VIOLATED op
        # (a brightfield op on fluorescence, a 3D op on 2D) drops below the generic — so a cell plan picks
        # cellpose, a fluorescence-condensate plan the general subcellular_segment, a brightfield one bf_segment.
        # Scoped here (not the global policy) so terminal/interpret selection is unchanged. See the 2026-07-27 fix.
        best_cs = max((_context_score(c, ctx) for c in candidates), default=0)
        top = [c for c in candidates if _context_score(c, ctx) == best_cs] or candidates
        return self.select(top, ctx)

    def _pick_terminal(self, candidates, ctx, pins, intent) -> ModuleContract:
        """Terminal selection is TARGET-AWARE: an operation specialised to the
        intent's target (e.g. ``vpt.microrheology`` consuming bead trajectories)
        beats a generic one (``condensate_physics.fit_anomalous_diffusion``) when
        the intent is about beads. This is the bead-vs-object distinction the
        question tree draws at Q042, made operational in the planner so it holds
        even without the tree. Falls back to the normal preference policy."""
        pinned = pins.get(Representation.MEASUREMENT_TABLE.value)
        if pinned:
            for c in candidates:
                if c.name == pinned:
                    return c
        best = max(candidates, key=lambda m: (self._terminal_ctx_bonus(m, ctx),
                                              self._terminal_specificity(m, intent),
                                              m.preference, -ord(m.name[0])))
        # deterministic: if several share the top (context bonus, specificity), the base policy
        # (preference, cost, name) breaks the tie.
        top = [m for m in candidates
               if self._terminal_ctx_bonus(m, ctx) == self._terminal_ctx_bonus(best, ctx)
               and self._terminal_specificity(m, intent) == self._terminal_specificity(best, intent)]
        return self.select(top, ctx)

    def _terminal_specificity(self, m: ModuleContract, intent) -> int:
        """1 if the terminal op is specialised to the intent's target (its inputs/outputs carry ``target:<tgt>``),
        else 0 — the bead-vs-object distinction made operational. Shared by ``_pick_terminal`` and
        ``explain_terminal_choice`` so the surfaced reasoning cannot drift from the actual pick."""
        tgt = intent.target
        if not tgt:
            return 0
        caps = list(m.requires_inputs) + list(m.provides)
        return 1 if any(c.target() == tgt for c in caps) else 0

    def _terminal_ctx_bonus(self, m: ModuleContract, ctx: AnalysisContext) -> int:
        """+1 for a terminal gated on the WORKFLOW-SELECTING ``in_vitro`` context when it is confirmed (so the
        in-vitro size-distribution wins the 'size' terminal on an in-vitro plan); 0 otherwise. Restricted to
        ``in_vitro`` on purpose: a workflow flag selects WHICH analysis to run, whereas quality/dimensionality
        gates (``calibrated``, ``z_stack``, ``time_series``) only gate a chosen op — promoting those would
        re-order the msd/coarsening/fusion terminals. Shared by ``_pick_terminal`` and ``explain_terminal_choice``."""
        try:
            if "in_vitro" in getattr(m, "requires_context", ()) \
                    and ctx.context_requirement("in_vitro") is True:
                return 1
        except Exception:      # broad-ok: optional_probe — an unknown gate key just yields no bonus
            pass
        return 0

    def explain_terminal_choice(self, intent, ctx, pins=None) -> dict:
        """Anti-black-box (Method-Widget Spec 5 core): for each requested observable, the terminal ops the planner
        CONSIDERED, each with its selection scores (``in_vitro`` context bonus, target specificity, preference),
        and which one it chose. This is the planner's OWN reasoning, surfaced — the winner comes from
        ``_pick_terminal`` and the per-candidate scores from the same shared helpers, so the explanation can never
        drift from the actual pick. Returns ``{observable: {"chosen": name, "candidates": [{name, context_bonus,
        target_specificity, preference, chosen}]}}`` (candidates ordered winner-first)."""
        pins = pins or {}
        report = {}
        for obs in getattr(intent, "observables", ()) or ():
            candidates = self.registry.measuring(obs)
            if not candidates:
                continue
            chosen = self._pick_terminal(candidates, ctx, pins, intent)
            report[obs] = {
                "chosen": chosen.name,
                "candidates": sorted(
                    ({"name": m.name,
                      "context_bonus": self._terminal_ctx_bonus(m, ctx),
                      "target_specificity": self._terminal_specificity(m, intent),
                      "preference": m.preference,
                      "chosen": m.name == chosen.name} for m in candidates),
                    key=lambda d: (not d["chosen"], -d["context_bonus"], -d["target_specificity"],
                                   -d["preference"], d["name"])),
            }
        return report

    def explain_provider_choice(self, goal, ctx, pins=None):
        """The dependency-layer analogue of :meth:`explain_terminal_choice`: for a resolution GOAL (a Capability
        — e.g. the instance-labels a segmenter must provide), the candidate providers the planner weighs, each
        with its CONTEXT SCORE (+1 context-matched specialist / 0 general / -1 unconfirmed specialist / -2 context
        violated) and preference, and which one ``_pick`` selects. Reuses ``_pick`` for the winner and the shared
        ``_context_score`` for the scores, so it cannot drift from the actual dependency choice. ``None`` if the
        goal has no provider. Candidates ordered winner-first."""
        pins = pins or {}
        providers = self.registry.providers_of(goal)
        if not providers:
            return None
        kind_hint = getattr(goal, "kind", None)   # a Capability's representation-kind IS its pin key
        chosen = self._pick(providers, ctx, pins, kind_hint=kind_hint)
        return {
            "goal": str(goal),
            "chosen": chosen.name,
            "candidates": sorted(
                ({"name": p.name,
                  "context_score": _context_score(p, ctx),
                  "preference": p.preference,
                  "chosen": p.name == chosen.name} for p in providers),
                key=lambda d: (not d["chosen"], -d["context_score"], -d["preference"], d["name"])),
        }

    def explain_segmentation_choice(self, intent, ctx, pins=None):
        """Why THIS segmenter, not the others: the provider choice for the instance-labels of the intent's target
        (e.g. ``cellpose`` for cells on 2D, ``cellpose_3d`` on a z-stack, ``bf_segment`` for brightfield
        condensates). A convenience over :meth:`explain_provider_choice` that builds the segmentation goal.
        ``None`` when the intent has no target."""
        if not getattr(intent, "target", None):
            return None
        goal = Capability(Representation.INSTANCE_LABELS, frozenset([f"target:{intent.target}"]))
        return self.explain_provider_choice(goal, ctx, pins)

    def _resolve_module(self, module: ModuleContract, produces_goal: Capability,
                        reason: str, ctx: AnalysisContext, plan: Plan,
                        memo: Dict[str, PlanStep], stack: List[str],
                        pins: Dict[str, str]) -> Optional[PlanStep]:
        key = module.name
        if key in memo:                      # already planned (shared dependency)
            return memo[key]
        if module.name in stack:             # cycle guard (#3)
            return None
        stack.append(module.name)

        step = PlanStep(module=module,
                        produces=module.provides_capability(produces_goal) or produces_goal,
                        reason=reason)

        # context requirements -> either satisfied silently, asked, or block
        for ckey in module.requires_context:
            status = ctx.context_requirement(ckey)
            if status is True:
                continue
            gstat = GateStatus.VIOLATED if status is False else GateStatus.UNKNOWN
            plan.gaps.append(ContextGap(ckey, module.name, gstat))

        # product requirements -> upstream modules (with tag propagation)
        propagated = frozenset(t for t in produces_goal.tags
                               if t.split(":", 1)[0] in module.propagates_tags)
        for req in module.requires_inputs:
            subgoal = req.with_tags(propagated)

            # reuse an EXISTING layer if one already satisfies this product
            # (review #8): bind it and do not plan a producer.
            if getattr(self, "_layer_resolver", None) is not None:
                binding = self._layer_resolver.find(subgoal)
                if binding.usable:
                    plan.bindings.append(binding)
                    step.inputs.append(subgoal)
                    step.depends_on.append(f"layer:{binding.layer_name}")
                    continue

            # A propagated SPECIFIC target (e.g. target:cell) narrows the requirement's wildcard target:* — FOR
            # PROVIDER LOOKUP ONLY — so a target-specialised producer (cellpose for a cell, bf_segment for a
            # brightfield condensate) is offered and can win, without changing the subgoal the layer-resolver,
            # recursion, and step inputs see. Without it, providers_of never lists the specialised op and a cell
            # plan silently segments with the puncta segmenter. See the 2026-07-27 fix.
            provider_goal = subgoal
            _specific = {t for t in propagated if t.startswith("target:") and t != "target:*"}
            if _specific and "target:*" in subgoal.tags:
                provider_goal = Capability(subgoal.kind, frozenset(subgoal.tags) - {"target:*"})

            providers = self.registry.providers_of(provider_goal)
            providers = [p for p in providers if p.name not in stack]  # avoid cycles
            if not providers:
                plan.unresolved.append((subgoal, module.name))
                continue
            chosen = self._pick(providers, ctx, pins, kind_hint=subgoal.kind)
            sub = self._resolve_module(chosen, subgoal,
                                       f"provides {subgoal} for {module.name}",
                                       ctx, plan, memo, stack, pins)
            if sub is not None:
                step.inputs.append(subgoal)
                step.depends_on.append(sub.name)

        memo[key] = step
        stack.pop()
        return step

    @staticmethod
    def _toposort(steps: List[PlanStep]) -> List[PlanStep]:
        by_name = {s.name: s for s in steps}
        seen: Dict[str, bool] = {}
        order: List[PlanStep] = []

        def visit(s: PlanStep):
            if seen.get(s.name):
                return
            seen[s.name] = True
            for dep in s.depends_on:
                if dep in by_name:
                    visit(by_name[dep])
            order.append(s)

        # deterministic: visit in registration-ish (name) order
        for s in sorted(steps, key=lambda x: x.name):
            visit(s)
        return order
