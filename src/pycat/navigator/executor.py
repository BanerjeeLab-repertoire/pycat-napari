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
    """Bridges ONE plan step to the batch handler that computes it. ``plan_step`` is the **real navigator module
    name** (as `execution_order` reports it — e.g. ``segmentation_tools``, ``image_processing_tools``), not a
    synthetic label. ``batch_step`` is either the ``_STEP_MAP`` key that computes it, or a callable
    ``(intent) -> Optional[str]`` for a coarse module whose batch step depends on the target (e.g.
    ``segmentation_tools`` → ``cellpose_segmentation`` for a cell, ``None`` for a condensate whose batch route is
    an unproven gap). ``params_from(intent, ctx, state, reviewed)`` derives the handler's ``params`` from the
    answers and the user-reviewed values, applying each where the handler actually reads it."""
    plan_step: str
    batch_step: object
    params_from: Callable


def _background_removal_params(intent, ctx, state, reviewed):
    radius = reviewed.get("ball_radius") if reviewed else None
    if radius is None and ctx is not None:
        try:
            radius = ctx.get("ball_radius")
        except Exception:      # broad-ok: optional_probe — a context miss falls back to the grounded default
            radius = None
    return {"ball_radius": int(radius) if radius else 50, "active_layer": "segmentation image"}


def _cellpose_params(intent, ctx, state, reviewed):
    """Cellpose reads ``cell_diameter`` from the ``data_instance`` (NOT the params dict), so a reviewed/derived
    diameter is applied THERE — the params dict only carries the method + refine flag the handler reads."""
    diam = reviewed.get("cell_diameter") if reviewed else None
    if diam is None and ctx is not None:
        try:
            diam = ctx.get("cell_diameter")
        except Exception:      # broad-ok: optional_probe — no session diameter → the handler's grounded default (100)
            diam = None
    if diam is not None and isinstance(state, dict):
        di = state.get("data_instance")
        if di is not None and hasattr(di, "set_data"):
            try:
                di.set_data("cell_diameter", int(diam))
            except Exception:      # broad-ok: optional_probe — a bad diameter falls back to the handler default
                pass
    return {"method": "cellpose", "cellpose_refine": False}


def _cell_analysis_params(intent, ctx, state, reviewed):
    """Cell analysis takes no run-time knob of its own — it measures the mask segmentation produced, using the
    session measurements already on the ``data_instance`` (cell_diameter, object_size, ball_radius, pixel size).
    So the params dict is empty (image_layer/omit_layer default to the fallback + no omit mask headlessly)."""
    return {}


def _feature_analysis_step_if_cell(intent):
    """``feature_analysis_tools`` is coarse — for a cell target it is ``cell_analysis`` (measures the cell mask,
    the natural next step after segmentation). For a condensate target it would be ``condensate_analysis``, which
    depends on a ``puncta_mask`` from the unproven condensate-segmentation batch route — so it resolves to
    ``None`` (reported, never guessed) until that route is proven."""
    return "cell_analysis" if getattr(intent, "target", None) == "cell" else None


def _cellpose_step_if_cell(intent):
    """``segmentation_tools`` is coarse — for a cell target it is single-frame Cellpose (the ``cellpose_segmentation``
    batch step `test_route_equivalence` proves), but condensate segmentation is an unproven batch route (a
    documented route-equivalence gap), so it resolves to ``None`` (reported, never guessed). Time-series cell
    segmentation (``ts_cellpose_tools``) is deliberately NOT mapped: its real operation is keyframe propagation
    across a stack (``replay_ts_cellpose_keyframe``), which is not route-proven — a single-frame stand-in would
    be wrong science, so it stays 'run from its panel'."""
    return "cellpose_segmentation" if getattr(intent, "target", None) == "cell" else None


#: The declared adapters, keyed by the REAL navigator module name `execution_order` reports. The ONLY place a
#: plan step is tied to a computation — a step absent here (or one whose batch step resolves to ``None``) is
#: reported "run from its panel", never guessed at. Grows one workflow per phase, each behind a
#: route-equivalence test: ``background_removal`` (rolling-ball) and ``cellpose_segmentation`` are the batch
#: steps `test_route_equivalence` proves compute identically to the manual route.
_ADAPTERS: dict = {
    "image_processing_tools": ExecAdapter("image_processing_tools", "background_removal",
                                          _background_removal_params),
    "segmentation_tools": ExecAdapter("segmentation_tools", _cellpose_step_if_cell, _cellpose_params),
    "feature_analysis_tools": ExecAdapter("feature_analysis_tools", _feature_analysis_step_if_cell,
                                          _cell_analysis_params),
}


def has_adapter(step_name: str) -> bool:
    return step_name in _ADAPTERS


def resolve_batch_step(step_name: str, intent=None) -> Optional[str]:
    """The batch step ``step_name`` will actually run for ``intent``, or ``None`` if it has no adapter or its
    variant is not auto-runnable yet (a coarse module whose target has no proven batch route). The single
    authority for 'will this step run', shared by the executor and the parameter review."""
    adapter = _ADAPTERS.get(step_name)
    if adapter is None:
        return None
    bs = adapter.batch_step
    return bs(intent) if callable(bs) else bs


@dataclasses.dataclass(frozen=True)
class StepOutcome:
    """What happened to one step. ``outcome`` is ``'ran'`` / ``'ran_with_caveat'`` / ``'blocked'`` (the run
    stops here) / ``'skipped'`` (after a blocker) / ``'needs_panel'`` (no adapter yet) / ``'error'``.
    ``provenance`` (Phase 2) records how the step was parameterised — the ``PresetApplication.record()`` shape
    (which preset, if any, seeded it and what the user changed) — empty when the step carried no review."""
    name: str
    outcome: str
    detail: str = ""
    provenance: dict = dataclasses.field(default_factory=dict)


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
             params_by_step: Optional[dict] = None, provenance_by_step: Optional[dict] = None,
             on_step: Optional[Callable] = None) -> ExecReport:
    """Execute ``plan``'s steps in gate order by driving the batch handlers, threading ``state`` (a dict the
    handlers read/write, exactly as a batch replay). Returns an :class:`ExecReport`.

    Gate semantics are READ from :func:`execution.execution_order`, never re-decided: a **blocker** stops the
    run at that step (nothing after it runs); a **caveat** runs with the caveat recorded; **probes** run first.
    A step with no :class:`ExecAdapter` is reported ``'needs_panel'`` — never invoked with guessed arguments.
    A step that raises halts the run (downstream depends on its output). ``runner`` (an ``OperationRunner``)
    runs each handler off the Qt thread when given; otherwise handlers run synchronously (headless / tests).

    ``params_by_step`` (Phase 2) overrides the adapter's derived params per step with the user-reviewed values
    (:mod:`pycat.navigator.parameters`); ``provenance_by_step`` records how each ran step was parameterised
    (the ``PresetApplication.record()`` shape) onto its :class:`StepOutcome`."""
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
            batch_step = adapter.batch_step(intent) if (adapter and callable(adapter.batch_step)) \
                else (adapter.batch_step if adapter else None)
            if batch_step is None:
                # No adapter, or a coarse module whose variant has no proven batch route yet — report it,
                # never invoke with guessed arguments.
                detail = ("no execution adapter yet — run this step from its method panel" if adapter is None
                          else "this variant isn't auto-runnable yet — run this step from its method panel")
                report.steps.append(StepOutcome(es.name, "needs_panel", detail))
                if on_step:
                    on_step(report.steps[-1])
                continue

            fn = registry.get(batch_step)
            if fn is None:
                report.steps.append(StepOutcome(es.name, "error",
                                                f"batch step {batch_step!r} is not registered"))
                halted = True
                continue

            # Reviewed values reach the adapter, which applies each where the handler actually reads it (the
            # params dict, or the data_instance for cellpose's cell_diameter) — the executor never guesses.
            reviewed = (params_by_step or {}).get(es.name, {})
            params = adapter.params_from(intent, ctx, state, reviewed)
            try:
                if runner is not None:
                    runner.execute(fn, state, image_path, params, output_dir)
                else:
                    fn(state, image_path, params, output_dir)
            except Exception as exc:      # broad-ok: scientific_result — a failed step halts; report it, never silently continue on stale state
                report.steps.append(StepOutcome(es.name, "error", f"{type(exc).__name__}: {exc}"))
                halted = True
                continue

            prov = (provenance_by_step or {}).get(es.name, {})
            report.steps.append(StepOutcome(
                es.name, "ran_with_caveat" if es.status == "caveat" else "ran", es.reason, prov))
            if on_step:
                on_step(report.steps[-1])
    finally:
        if tmp is not None:
            tmp.cleanup()
    return report
