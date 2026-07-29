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


def _feature_analysis_step(intent, state=None):
    """``feature_analysis_tools`` is coarse. For a **cell** target it is ``cell_analysis`` (measures the cell
    mask). For a **condensate** target the measurement depends on how the condensates were segmented:

    - **Fluorescence** condensates are puncta nested in cells, so segmentation wrote a ``puncta_mask`` and the
      measurement is ``condensate_analysis`` (``puncta_analysis_func``, measures puncta per cell).
    - **Brightfield** condensates are first-class labelled objects — ``bf_segment`` wrote them into
      ``bf_condensate_mask`` with no per-cell nesting — which neither fluorescence route measures correctly
      (``condensate_analysis`` needs a ``puncta_mask``; ``cell_analysis``'s cell-sized ``min_area`` filter
      discards condensate-sized objects). Their measurement is ``bf_condensate_analysis`` — per-condensate
      optical-density/area/shape via ``bf_condensate_metrics`` (the cell-less in-vitro path).

    Which case applies is knowable only at RUN time from the threaded ``state`` (the modality is not on the
    intent), so we dispatch on the mask the upstream segmenter actually produced. With no state (e.g. the
    parameter review just asking 'will this run'), the fluorescence default stands. Any other target has no
    measurement route yet → ``None`` (reported, never guessed)."""
    target = getattr(intent, "target", None)
    if target == "cell":
        return "cell_analysis"
    if target == "condensate":
        if isinstance(state, dict) and state.get("puncta_mask") is None:
            # a directly-labelled condensate mask (no per-cell puncta nesting): brightfield → its OD metrics;
            # in-vitro droplets → the field-summary/size-distribution measurement (staged — next increment)
            if state.get("bf_condensate_mask") is not None:
                return "bf_condensate_analysis"
            if state.get("ivf_droplet_mask") is not None:
                return "ivf_droplet_analysis"     # per-droplet MEASURE; the size-distribution INTERPRET follows
        return "condensate_analysis"
    return None


def _segmentation_step(intent, state=None):
    """``segmentation_tools`` is coarse. For a **cell** target it is single-frame Cellpose
    (``cellpose_segmentation``); for a **condensate** target it is ``condensate_segmentation``
    (``segment_subcellular_objects`` per cell, producing the ``puncta_mask``). Time-series cell segmentation
    (``ts_cellpose_tools``) is deliberately NOT mapped: its real operation is keyframe propagation across a
    stack (``replay_ts_cellpose_keyframe``), which is not route-proven — a single-frame stand-in would be
    wrong science, so it stays 'run from its panel'."""
    target = getattr(intent, "target", None)
    if target == "cell":
        return "cellpose_segmentation"
    if target == "condensate":
        return "condensate_segmentation"
    return None


#: The condensate-segmentation thresholds `replay_condensate_segmentation` reads from its params dict, with the
#: grounded defaults that equal `segment_subcellular_objects`' signature defaults. The param review
#: (`navigator/parameters._CONDENSATE_THRESHOLDS`) surfaces these same six for editing; keep the two in sync.
_CONDENSATE_SEG_DEFAULTS: dict = {
    "min_spot_radius": 2, "kurtosis_threshold": -3.0, "local_snr_threshold": 1.0,
    "global_snr_threshold": 1.0, "intensity_hwhm_scale": 1.17, "max_area_fraction": 0.25,
}


def _segmentation_params(intent, ctx, state, reviewed):
    """Cellpose (cell) needs the reviewed ``cell_diameter`` applied to the ``data_instance`` plus its
    method/refine flags. Condensate segmentation (``segment_subcellular_objects`` per cell) reads its six
    thresholds — ``min_spot_radius``, ``kurtosis_threshold``, ``local_snr_threshold``, ``global_snr_threshold``,
    ``intensity_hwhm_scale``, ``max_area_fraction`` — from the params dict: each takes the user-reviewed value
    when one was surfaced/edited, else the grounded default that equals the function's signature default. So an
    unedited run is bit-for-bit the manual default (the handler's own ``params.get(k, default)`` sees the same
    number), and an edited threshold provably reaches the per-cell computation."""
    if getattr(intent, "target", None) == "condensate":
        return {k: (reviewed.get(k) if reviewed and reviewed.get(k) is not None else d)
                for k, d in _CONDENSATE_SEG_DEFAULTS.items()}
    return _cellpose_params(intent, ctx, state, reviewed)


#: Brightfield preprocessing knobs `replay_bf_preprocess` reads, and the dark-blob thresholds
#: `replay_bf_condensate_segmentation` reads, with grounded defaults equal to each handler's `params.get`
#: fallbacks — so an unedited brightfield run is bit-for-bit the manual, and a reviewed knob reaches it.
_BF_PREPROCESS_DEFAULTS: dict = {"bg_kernel": 50, "halo_weight": 0.35}
_BF_CONDENSATE_SEG_DEFAULTS: dict = {"min_diameter_px": 3.0, "max_diameter_px": 50.0, "min_circularity": 0.5}


def _reviewed_or_default(reviewed, defaults):
    """Each knob takes the user-reviewed value when one was surfaced/edited, else the grounded default."""
    return {k: (reviewed.get(k) if reviewed and reviewed.get(k) is not None else d)
            for k, d in defaults.items()}


def _bf_preprocess_params(intent, ctx, state, reviewed):
    return _reviewed_or_default(reviewed, _BF_PREPROCESS_DEFAULTS)


def _bf_segment_params(intent, ctx, state, reviewed):
    return _reviewed_or_default(reviewed, _BF_CONDENSATE_SEG_DEFAULTS)


#: In-vitro fluorescence droplet segmentation knobs (`segment_ivf_droplets` via `replay_ivf_droplet_segment`),
#: with the grounded defaults the producer uses. Method 'otsu' is the pure-skimage default.
_IVF_SEG_DEFAULTS: dict = {"method": "otsu", "min_area": 6, "reject_nonround": False}


def _ivf_segment_params(intent, ctx, state, reviewed):
    return _reviewed_or_default(reviewed, _IVF_SEG_DEFAULTS)


def _ivf_size_dist_params(intent, ctx, state, reviewed):
    return _reviewed_or_default(reviewed, {"n_bins": 30})


def _pixel_coloc_params(intent, ctx, state, reviewed):
    return {}       # the two channels + the ROI are resolved from state; the raw coloc measures take no knob


#: VPT microrheology knobs the handler reads from `params` — the bead radius + temperature + min track length
#: for the Stokes–Einstein viscosity. Pixel size and frame interval come from the file metadata (the scale gate),
#: NOT from here — a viscosity in pixel units is refused, never guessed.
_VPT_MICRORHEOLOGY_DEFAULTS: dict = {"bead_radius_um": 0.5, "temperature_C": 24.0, "min_track_length": 10}


def _vpt_microrheology_params(intent, ctx, state, reviewed):
    return _reviewed_or_default(reviewed, _VPT_MICRORHEOLOGY_DEFAULTS)


def _spatial_metrology_params(intent, ctx, state, reviewed):
    return {}       # the object labels + cell ROIs come from state; Ripley/NN/radial run on grounded defaults


#: Dynamic-spatial linking/merge-fission knobs the handler reads from `params` — the max per-frame displacement,
#: the gap-bridging window, and the merge/fission proximity. The mask stack + pixel size come from state.
_DYNAMIC_SPATIAL_DEFAULTS: dict = {"max_displacement_um": 2.0, "max_gap_frames": 1, "proximity_um": 1.0}


def _dynamic_spatial_params(intent, ctx, state, reviewed):
    return _reviewed_or_default(reviewed, _DYNAMIC_SPATIAL_DEFAULTS)


#: MSD analysis knobs — the min track length to admit (the science default is 200 frames) and an optional lag cap.
#: The trajectory table comes from the upstream `dynamic_spatial` step; pixel size + frame interval (the scale
#: gate) come from the file metadata, never from here.
_MSD_DEFAULTS: dict = {"min_track_length": 200}


def _msd_params(intent, ctx, state, reviewed):
    return _reviewed_or_default(reviewed, _MSD_DEFAULTS)


#: The declared adapters — the ONLY place a plan step is tied to a computation. A step absent here (or one whose
#: batch step resolves to ``None``) is reported "run from its panel", never guessed at. Grows one workflow per
#: phase, each behind a route-equivalence test: ``background_removal`` (rolling-ball) and ``cellpose_segmentation``
#: are the batch steps `test_route_equivalence` proves compute identically to the manual route.
#:
#: KEYING (N5b) — a key may be EITHER a module name (resolved for an op-id step via _OP_TO_ADAPTER_MODULE) OR an
#: op-id matched directly. _adapter_for tries the step name directly first, then the op->module translation.
#: When you add an adapter: if plan_step is an op-id with no module indirection, key it directly here (e.g.
#: "vpt.microrheology", "spatial_metrology.ripley"); if it's a coarse module fronting several ops, key it by
#: module (e.g. "segmentation_tools") and add the op->module rows to _OP_TO_ADAPTER_MODULE below.
_ADAPTERS: dict = {
    "image_processing_tools": ExecAdapter("image_processing_tools", "background_removal",
                                          _background_removal_params),
    "segmentation_tools": ExecAdapter("segmentation_tools", _segmentation_step, _segmentation_params),
    "feature_analysis_tools": ExecAdapter("feature_analysis_tools", _feature_analysis_step,
                                          _cell_analysis_params),
    # The brightfield condensate chain (op-id-keyed — these steps are only ever named by op-id): the planner
    # auto-inserts `bf_preprocess` (→ the enhanced image) before `bf_segment` (→ dark-blob `bf_condensate_mask`,
    # which condensate analysis then reads as its mask). See the brightfield-preprocessing exception.
    "bf_preprocess": ExecAdapter("bf_preprocess", "bf_preprocess", _bf_preprocess_params),
    "bf_segment": ExecAdapter("bf_segment", "bf_condensate_segmentation", _bf_segment_params),
    # In-vitro fluorescence droplet segmentation (droplets ≡ condensates; the in-vitro CONTEXT selects it over the
    # in-cell puncta segmenter). The op runs the extracted producer `segment_ivf_droplets` → `ivf_droplet_mask`.
    "ivf_droplet_segment": ExecAdapter("ivf_droplet_segment", "ivf_droplet_segment", _ivf_segment_params),
    # The in-vitro measure→interpret chain's INTERPRET: `invitro.size_distribution` fits the droplet size
    # distribution / C_sat. (The MEASURE, `feature_analysis.cell_analysis` → `ivf_droplet_analysis` on an in-vitro
    # mask, runs first via the state dispatch above.)
    "invitro.size_distribution": ExecAdapter("invitro.size_distribution", "ivf_size_distribution",
                                             _ivf_size_dist_params),
    # Two-channel colocalization WITHIN the segmentation ROI (Pearson + Manders) — the op requires a mask so the
    # planner chains a segmenter, and the correlation runs inside objects, not whole-frame.
    "pixel_wise_corr.pearson_manders": ExecAdapter("pixel_wise_corr.pearson_manders", "pixel_colocalization",
                                                   _pixel_coloc_params),
    # VPT microrheology (Gable's flagship): the `vpt.microrheology` INTERPRET terminal runs the whole
    # detect→link→MSD→fit→Stokes-Einstein chain in `replay_vpt_microrheology` (self-contained from the raw bead
    # stack). The op declares `needs_pixel_size`, so the planner blocks it without calibration; the handler's
    # scale gate refuses a pixel-unit viscosity as a second line of defence. See spec N2b-1.
    "vpt.microrheology": ExecAdapter("vpt.microrheology", "vpt_microrheology", _vpt_microrheology_params),
    # Spatial metrology (spec N2b-2): the `spatial_metrology.ripley` MEASURE runs Ripley's L / nearest-neighbour /
    # radial density PER CELL on the segmented objects' centroids (`replay_spatial_metrology` → the shared
    # `run_all_spatial_metrics`). It requires INSTANCE_LABELS, so the planner chains a segmenter first; the metrics
    # run within each cell ROI, never whole-frame. Replaces the old headless skip-stub.
    "spatial_metrology.ripley": ExecAdapter("spatial_metrology.ripley", "spatial_metrology",
                                            _spatial_metrology_params),
    # Dynamic spatial (spec N2b-3): BOTH the motion op (`dynamic_spatial.link_trajectories`, CREATE) and the
    # fusion op (`dynamic_spatial.detect_merge_fission`, INTERPRET) resolve to the one self-contained handler,
    # which runs extract_frame_properties -> link + merge/fission over the segmented (T,H,W) stack. A plan holding
    # both ops only tracks once (the handler's `_dynamic_spatial_done` guard). No 3-D stack in state -> clear skip.
    "dynamic_spatial.link_trajectories": ExecAdapter("dynamic_spatial.link_trajectories", "dynamic_spatial",
                                                     _dynamic_spatial_params),
    "dynamic_spatial.detect_merge_fission": ExecAdapter("dynamic_spatial.detect_merge_fission", "dynamic_spatial",
                                                        _dynamic_spatial_params),
    # Condensate MSD (spec N2b-4): the stack-level branch of the msd_analysis decision. BOTH diffusion ops
    # (`condensate_physics.compute_msd` + `.fit_anomalous_diffusion`, each requiring TRAJECTORIES) resolve to the
    # one handler, which reads the trajectory table `dynamic_spatial` linked, computes the ensemble MSD and fits
    # anomalous diffusion. A `_msd_done` guard keeps a plan holding both from fitting twice; the handler's scale
    # gate refuses a pixel^2/frame "D" (needs a calibrated pixel size + frame interval), like VPT.
    "condensate_physics.compute_msd": ExecAdapter("condensate_physics.compute_msd", "msd_analysis", _msd_params),
    "condensate_physics.fit_anomalous_diffusion": ExecAdapter("condensate_physics.fit_anomalous_diffusion",
                                                              "msd_analysis", _msd_params),
}


#: The production planner names plan steps by OP-ID (`cellpose`, `subcellular_segment`,
#: `feature_analysis.cell_analysis`, `rolling_ball`), but the adapters are keyed by the toolbox MODULE.
#: Translate the op-id to its module so the module adapter — which dispatches on `intent.target` — fires. Only
#: the ops backing a proven adapter route are listed; anything else stays "run from its panel". The planner
#: selects the op by target × modality (fixed 2026-07-27), so an op here is only planned for the target its
#: adapter expects (cellpose→cell, subcellular_segment→condensate). See the dormant-adapter fix.
_OP_TO_ADAPTER_MODULE: dict = {
    "rolling_ball": "image_processing_tools",
    "cellpose": "segmentation_tools",
    "subcellular_segment": "segmentation_tools",
    "feature_analysis.cell_analysis": "feature_analysis_tools",
}


def _adapter_for(step_name: str):
    """The ExecAdapter for a plan step, whether it is named by module (`segmentation_tools`, from the
    workbook registry / hand-built plans) or by op-id (`cellpose`, from the production op-catalog session)."""
    return _ADAPTERS.get(step_name) or _ADAPTERS.get(_OP_TO_ADAPTER_MODULE.get(step_name, ""))


def has_adapter(step_name: str) -> bool:
    return _adapter_for(step_name) is not None


def adapter_module_for(step_name: str) -> str:
    """The toolbox MODULE an op-id/module step resolves to for material-param lookup (`cellpose` and
    `subcellular_segment` → `segmentation_tools`), or the step name unchanged if it backs no adapter. The param
    review keys its material set by module, but production plans name steps by op-id — so it was op-id-blind the
    same way the adapters were (fixed 2026-07-27); this is the shared translation."""
    a = _adapter_for(step_name)
    return a.plan_step if a is not None else step_name


def resolve_batch_step(step_name: str, intent=None, state=None) -> Optional[str]:
    """The batch step ``step_name`` will actually run for ``intent``, or ``None`` if it has no adapter or its
    variant is not auto-runnable yet (a coarse module whose target has no proven batch route). The single
    authority for 'will this step run', shared by the executor and the parameter review. ``state`` (the run's
    threaded dict, when available) lets a coarse module pick its variant from what upstream actually produced —
    e.g. brightfield vs fluorescence condensate measurement; with no state the fluorescence default stands."""
    adapter = _adapter_for(step_name)
    if adapter is None:
        return None
    bs = adapter.batch_step
    return bs(intent, state) if callable(bs) else bs


@dataclasses.dataclass(frozen=True)
class StepOutcome:
    """What happened to one step. ``outcome`` is ``'ran'`` / ``'ran_with_caveat'`` / ``'blocked'`` (the run
    stops here) / ``'skipped'`` (after a blocker or a cancel) / ``'needs_panel'`` (no adapter yet) / ``'error'``
    / ``'cancelled'`` (the user cancelled at this step's boundary — it and everything after it did not run).
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

    @property
    def cancelled(self) -> bool:
        """True when the run was cancelled at a step boundary (a ``'cancelled'`` outcome is present)."""
        return any(s.outcome == "cancelled" for s in self.steps)


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
             on_step: Optional[Callable] = None, should_cancel: Optional[Callable] = None,
             on_progress: Optional[Callable] = None) -> ExecReport:
    """Execute ``plan``'s steps in gate order by driving the batch handlers, threading ``state`` (a dict the
    handlers read/write, exactly as a batch replay). Returns an :class:`ExecReport`.

    Gate semantics are READ from :func:`execution.execution_order`, never re-decided: a **blocker** stops the
    run at that step (nothing after it runs); a **caveat** runs with the caveat recorded; **probes** run first.
    A step with no :class:`ExecAdapter` is reported ``'needs_panel'`` — never invoked with guessed arguments.
    A step that raises halts the run (downstream depends on its output). ``runner`` (an ``OperationRunner``)
    runs each handler off the Qt thread when given; otherwise handlers run synchronously (headless / tests).

    ``params_by_step`` (Phase 2) overrides the adapter's derived params per step with the user-reviewed values
    (:mod:`pycat.navigator.parameters`); ``provenance_by_step`` records how each ran step was parameterised
    (the ``PresetApplication.record()`` shape) onto its :class:`StepOutcome`.

    ``should_cancel`` / ``on_progress`` (Phase 4) make a run cancellable and observable. ``should_cancel()`` is
    checked at **each step boundary** (before the step runs): the first time it returns truthy the current step
    is recorded ``'cancelled'`` and it — and everything after it — does not run (identical stop semantics to a
    blocker, so no step ever runs on cancelled/stale state). ``on_progress(done, total)`` fires once per step
    after its outcome is recorded, with ``done`` the count of steps disposed so far and ``total`` the plan's
    step count — **monotonically increasing**, ending at ``total`` unless cancelled earlier."""
    intent = intent if intent is not None else getattr(plan, "intent", None)
    registry = _build_step_registry()
    report = ExecReport()

    order = list(execution_order(plan))
    total = len(order)

    def _record(outcome):
        """Append one step outcome and fire the monotonic progress tick (done = steps disposed so far)."""
        report.steps.append(outcome)
        if on_progress:
            on_progress(len(report.steps), total)
        return outcome

    tmp = None
    if output_dir is None:
        tmp = tempfile.TemporaryDirectory()
        output_dir = pathlib.Path(tmp.name)
    if image_path is None:
        image_path = pathlib.Path(output_dir) / "sample.tif"

    try:
        halted = False
        for es in order:
            # Cancellation is checked at the step boundary, BEFORE the step runs — the current step and
            # everything after it are left un-run (the same stop discipline as a blocker: never run on a
            # cancelled/stale state). Only the first boundary records 'cancelled'; the rest are 'skipped'.
            if not halted and should_cancel and should_cancel():
                _record(StepOutcome(es.name, "cancelled", "run cancelled"))
                halted = True
                continue

            if halted or es.status == "skipped":
                _record(StepOutcome(es.name, "skipped", es.reason))
                continue
            if es.status == "blocked":
                _record(StepOutcome(es.name, "blocked", es.reason))
                halted = True
                continue

            adapter = _adapter_for(es.name)
            # The coarse callable may dispatch on the threaded state (what upstream steps actually produced —
            # e.g. a brightfield vs fluorescence condensate mask), so pass it: by this point every earlier step
            # has run and written its output.
            batch_step = adapter.batch_step(intent, state) if (adapter and callable(adapter.batch_step)) \
                else (adapter.batch_step if adapter else None)
            if batch_step is None:
                # No adapter, or a coarse module whose variant has no proven batch route yet — report it,
                # never invoke with guessed arguments.
                detail = ("no execution adapter yet — run this step from its method panel" if adapter is None
                          else "this variant isn't auto-runnable yet — run this step from its method panel")
                _record(StepOutcome(es.name, "needs_panel", detail))
                if on_step:
                    on_step(report.steps[-1])
                continue

            fn = registry.get(batch_step)
            if fn is None:
                _record(StepOutcome(es.name, "error", f"batch step {batch_step!r} is not registered"))
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
                _record(StepOutcome(es.name, "error", f"{type(exc).__name__}: {exc}"))
                halted = True
                continue

            prov = (provenance_by_step or {}).get(es.name, {})
            _record(StepOutcome(
                es.name, "ran_with_caveat" if es.status == "caveat" else "ran", es.reason, prov))
            if on_step:
                on_step(report.steps[-1])
    finally:
        if tmp is not None:
            tmp.cleanup()
    return report
