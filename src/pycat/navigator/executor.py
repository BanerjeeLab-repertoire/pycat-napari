"""**Execute a compiled navigator plan by driving the BATCH step handlers** (execution-adapter layer, Phase 1).

`selection_scale` Part 2 established there is no uniform "run this op" — PyCAT operations have bespoke,
panel-collected signatures. But the **batch `_STEP_MAP` handlers** are the proven "same computation" route:
they share one signature — ``(state, image_path, params, output_dir)`` — and `test_route_equivalence` already
asserts they compute byte-identically to the manual GUI route. So the executor drives *those* handlers, in the
gate-respecting order (`execution.execution_order`), threading each step's output through a shared ``state``
dict exactly as a batch replay does.

The narrow bridge is per-step: an :class:`ExecAdapter` maps a plan step to a batch handler and derives that
handler's ``params`` from the answers. A plan step with **no adapter is reported** ("run it from its method
panel"), never invoked with guessed arguments — a `fn(image)` fallback would pass wrong args and produce wrong
science silently, which is the whole reason this is an adapter layer and not a loop over ``resolve_operation``.

Phase 1 ships the executor + registry + ONE proven adapter (``background_removal`` — the shortest chain
`test_route_equivalence` already covers), so `guided == batch == manual` is pinned; later phases add adapters
one workflow at a time, each earning its place with a route-equivalence test. Qt-free; a runner is optional
(off-thread when given, synchronous otherwise)."""
from __future__ import annotations

import dataclasses
import pathlib
import tempfile
from typing import Callable, Optional

from .execution import execution_order


@dataclasses.dataclass(frozen=True)
class ExecAdapter:
    """Bridges ONE plan step to the batch handler that computes it. ``params_from(intent, ctx, state)`` derives
    the handler's ``params`` from the answers (Phase 1: grounded defaults; a preset seed lands in Phase 2)."""
    plan_step: str
    batch_step: str
    params_from: Callable


def _background_removal_params(intent, ctx, state):
    radius = None
    try:
        radius = ctx.get("ball_radius") if ctx is not None else None
    except Exception:      # broad-ok: optional_probe — a context miss falls back to the grounded default
        radius = None
    return {"ball_radius": int(radius) if radius else 50, "active_layer": "segmentation image"}


#: The declared adapters. The ONLY place a plan step is tied to a computation — a step absent here is reported
#: as "run from its panel", never guessed at. Grows one workflow per phase (each behind a route-equivalence test).
_ADAPTERS: dict = {
    "background_removal": ExecAdapter("background_removal", "background_removal", _background_removal_params),
}


def has_adapter(step_name: str) -> bool:
    return step_name in _ADAPTERS


@dataclasses.dataclass(frozen=True)
class StepOutcome:
    """What happened to one step. ``outcome`` is ``'ran'`` / ``'ran_with_caveat'`` / ``'blocked'`` (the run
    stops here) / ``'skipped'`` (after a blocker) / ``'needs_panel'`` (no adapter yet) / ``'error'``."""
    name: str
    outcome: str
    detail: str = ""


@dataclasses.dataclass
class ExecReport:
    steps: list = dataclasses.field(default_factory=list)

    @property
    def ran(self) -> list:
        return [s for s in self.steps if s.outcome.startswith("ran")]

    @property
    def stopped(self) -> bool:
        return any(s.outcome in ("blocked", "error") for s in self.steps)

    @property
    def needs_panel(self) -> list:
        return [s for s in self.steps if s.outcome == "needs_panel"]


def _build_step_registry() -> dict:
    """The production batch step registry (name → replay handler), built the way `BatchProcessor` does. Only
    the import-clean `batch_step_registry` is touched (no Qt)."""
    registry: dict = {}

    class _Recorder:
        def register_step(self, name, fn):
            registry[name] = fn

    from pycat.batch_step_registry import register_all_steps
    register_all_steps(_Recorder())
    return registry


def run_plan(plan, state, *, intent=None, ctx=None, image_path=None, output_dir=None, runner=None,
             on_step: Optional[Callable] = None) -> ExecReport:
    """Execute ``plan``'s steps in gate order by driving the batch handlers, threading ``state`` (a dict the
    handlers read/write, exactly as a batch replay). Returns an :class:`ExecReport`.

    Gate semantics are READ from :func:`execution.execution_order`, never re-decided: a **blocker** stops the
    run at that step (nothing after it runs); a **caveat** runs with the caveat recorded; **probes** run first.
    A step with no :class:`ExecAdapter` is reported ``'needs_panel'`` — never invoked with guessed arguments.
    A step that raises halts the run (downstream depends on its output). ``runner`` (an ``OperationRunner``)
    runs each handler off the Qt thread when given; otherwise handlers run synchronously (headless / tests)."""
    intent = intent if intent is not None else getattr(plan, "intent", None)
    registry = _build_step_registry()
    report = ExecReport()

    tmp = None
    if output_dir is None:
        tmp = tempfile.TemporaryDirectory()
        output_dir = pathlib.Path(tmp.name)
    if image_path is None:
        image_path = pathlib.Path(output_dir) / "sample.tif"

    try:
        halted = False
        for es in execution_order(plan):
            if halted or es.status == "skipped":
                report.steps.append(StepOutcome(es.name, "skipped", es.reason))
                continue
            if es.status == "blocked":
                report.steps.append(StepOutcome(es.name, "blocked", es.reason))
                halted = True
                continue

            adapter = _ADAPTERS.get(es.name)
            if adapter is None:
                report.steps.append(StepOutcome(
                    es.name, "needs_panel", "no execution adapter yet — run this step from its method panel"))
                if on_step:
                    on_step(report.steps[-1])
                continue

            fn = registry.get(adapter.batch_step)
            if fn is None:
                report.steps.append(StepOutcome(es.name, "error",
                                                f"batch step {adapter.batch_step!r} is not registered"))
                halted = True
                continue

            params = adapter.params_from(intent, ctx, state)
            try:
                if runner is not None:
                    runner.execute(fn, state, image_path, params, output_dir)
                else:
                    fn(state, image_path, params, output_dir)
            except Exception as exc:      # broad-ok: scientific_result — a failed step halts; report it, never silently continue on stale state
                report.steps.append(StepOutcome(es.name, "error", f"{type(exc).__name__}: {exc}"))
                halted = True
                continue

            report.steps.append(StepOutcome(
                es.name, "ran_with_caveat" if es.status == "caveat" else "ran", es.reason))
            if on_step:
                on_step(report.steps[-1])
    finally:
        if tmp is not None:
            tmp.cleanup()
    return report
