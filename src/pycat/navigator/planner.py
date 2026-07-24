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
        return self.select(candidates, ctx)

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
        tgt = intent.target

        def specificity(m: ModuleContract) -> int:
            if not tgt:
                return 0
            caps = list(m.requires_inputs) + list(m.provides)
            return 1 if any(c.target() == tgt for c in caps) else 0

        best = max(candidates, key=lambda m: (specificity(m), m.preference, -ord(m.name[0])))
        # deterministic: if several share the top (specificity, preference), the
        # base policy (preference, cost, name) breaks the tie.
        top = [m for m in candidates if specificity(m) == specificity(best)]
        return self.select(top, ctx)

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

            providers = self.registry.providers_of(subgoal)
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
