"""**The minimal parameter review — surface the handful of parameters that change the result, seeded honestly.**

Execution-adapter layer, Phase 2. The navigator asks *what* to do, not *with which parameters* — but a guided
run that silently picks a rolling-ball radius (or a segmentation method) the user never saw is worse than
asking. So before a run, each adapter-covered step surfaces its **material** parameters — the handful that
materially change the result — in a small editable set, seeded by a declared precedence:

1. **A matching preset** (:mod:`pycat.utils.analysis_presets`) — if one ``applies_to`` this step's workflow,
   its parameters seed the values (the populate-but-never-lock contract).
2. **A session value** — a measured/derived value already in the context (e.g. a ``ball_radius`` the user
   measured on their data) seeds an un-preset parameter.
3. **The grounded function default** — the same default the batch handler falls back to; never an invented one.

The user may edit any value; the edit is tracked so the run records **which preset (if any) seeded each step
and what the user changed** — the exact ``PresetApplication.record()`` shape, so a guided result states how it
was parameterised and a saved template can carry those params. Qt-free: this is the model the dock's editable
form and :func:`pycat.navigator.executor.run_plan` both read; the dock only builds widgets over it.

Phase 2 ships the model + the one shipped adapter's material set (``background_removal`` → ``ball_radius``);
later adapters declare their material params here as they land, each still behind a route-equivalence test."""
from __future__ import annotations

import dataclasses
from typing import Optional

from .execution import execution_order
from .executor import resolve_batch_step


@dataclasses.dataclass(frozen=True)
class StepParam:
    """One material parameter of a step — declared, so the review shows the same handful the science turns on
    (not every argument the function takes). ``kind`` is ``'int'`` / ``'float'`` / ``'choice'``; ``default`` is
    the grounded function default (what the batch handler falls back to), never an invented value."""
    name: str
    label: str
    kind: str
    default: object
    choices: tuple = ()
    help: str = ""
    minimum: object = None
    maximum: object = None


#: The material parameters per plan step, keyed by the REAL navigator module name. The ONLY place a step
#: declares which of its arguments are surfaced for review. Grows one adapter at a time (each behind its
#: route-equivalence test), mirroring ``executor._ADAPTERS``.
_CELL_DIAMETER = StepParam(
    "cell_diameter", "Cell diameter (px)", "int", 100, minimum=1,
    help="The approximate cell diameter in pixels that Cellpose sizes its search to. Measure it on your data — "
         "a wrong diameter is the most common cause of missed or merged cells. Default 100 px.")

#: The six condensate-segmentation thresholds `segment_subcellular_objects` turns on — declared with the grounded
#: signature defaults `executor._CONDENSATE_SEG_DEFAULTS` mirrors, so a reviewed edit reaches the per-cell run and
#: an unedited run is bit-for-bit the manual default. These are what a CONDENSATE segmentation step surfaces
#: (where a cell step surfaces the cellpose diameter) — the target branch in `_MATERIAL` below.
_CONDENSATE_THRESHOLDS = (
    StepParam("min_spot_radius", "Min spot radius (px)", "int", 2, minimum=1,
              help="The smallest punctum radius kept, in pixels. Larger drops small/noise spots; too large "
                   "misses real small condensates. Default 2 px."),
    StepParam("kurtosis_threshold", "Kurtosis threshold", "float", -3.0,
              help="A cell's intensity kurtosis must exceed this for its puncta to be kept — the phantom-cell "
                   "gate. Lower is more permissive on flat/empty cells. Default -3.0."),
    StepParam("local_snr_threshold", "Local SNR threshold", "float", 1.0, minimum=0.0,
              help="Minimum local signal-to-noise for a punctum against its cell background. Higher is stricter. "
                   "Default 1.0."),
    StepParam("global_snr_threshold", "Global SNR threshold", "float", 1.0, minimum=0.0,
              help="Minimum signal-to-noise against the whole-image background. Higher is stricter. Default 1.0."),
    StepParam("intensity_hwhm_scale", "Intensity HWHM scale", "float", 1.17, minimum=0.0,
              help="Scales the half-width-half-max intensity cutoff that separates a punctum from its skirt. "
                   "Default 1.17."),
    StepParam("max_area_fraction", "Max area fraction", "float", 0.25, minimum=0.0, maximum=1.0,
              help="A punctum covering more than this fraction of its cell is rejected as over-segmentation. "
                   "Default 0.25."),
)
_MATERIAL: dict = {
    "image_processing_tools": (
        StepParam("ball_radius", "Rolling-ball radius (px)", "int", 50, minimum=1,
                  help="The rolling-ball structuring radius for background estimation. Larger keeps more "
                       "low-frequency structure; too small eats real signal. Default 50 px."),
    ),
    # segmentation_tools is TARGET-VARYING: a cell target surfaces the cellpose diameter, a condensate target the
    # six puncta thresholds — the same target branch the executor's `_segmentation_params` dispatches on.
    "segmentation_tools": lambda intent: (
        _CONDENSATE_THRESHOLDS if getattr(intent, "target", None) == "condensate" else (_CELL_DIAMETER,)),
    # The brightfield condensate chain (op-id-keyed): the preprocessing knobs and the dark-blob size/shape gates,
    # each read by its batch handler's `params.get(k, default)`, so a reviewed edit reaches the run.
    "bf_preprocess": (
        StepParam("bg_kernel", "Background kernel (px)", "int", 50, minimum=1,
                  help="Size of the smoothing kernel that estimates the slowly-varying brightfield background. "
                       "Should be a few times the largest condensate. Default 50 px."),
        StepParam("halo_weight", "Halo suppression", "float", 0.35, minimum=0.0, maximum=1.0,
                  help="Strength of the halo-ring suppression around dark spots. 0 = none, 0.5 = aggressive. "
                       "Default 0.35."),
    ),
    "bf_segment": (
        StepParam("min_diameter_px", "Min diameter (px)", "float", 3.0, minimum=0.0,
                  help="Smallest condensate diameter kept, in pixels. Larger drops small/noise blobs. Default 3."),
        StepParam("max_diameter_px", "Max diameter (px)", "float", 50.0, minimum=0.0,
                  help="Largest condensate diameter kept, in pixels. Smaller rejects merged/oversized blobs. "
                       "Default 50."),
        StepParam("min_circularity", "Min circularity", "float", 0.5, minimum=0.0, maximum=1.0,
                  help="Minimum circularity (1 = perfect circle) a blob must have to be kept. Default 0.5."),
    ),
    # In-vitro fluorescence droplet segmentation: the threshold method + the small-object cutoff.
    "ivf_droplet_segment": (
        StepParam("method", "Threshold method", "choice", "otsu",
                  choices=("otsu", "multiotsu", "sauvola"),
                  help="How the whole-field droplet threshold is chosen: otsu (global), multiotsu (multi-level), "
                       "or sauvola (local). Default otsu."),
        StepParam("min_area", "Min droplet area (px)", "int", 6, minimum=1,
                  help="Smallest droplet kept, in pixels of area. Larger drops small/noise objects. Default 6."),
    ),
}


#: Plan step → the ``analysis_presets`` workflow id whose presets seed it (preset precedence, source 1). Empty
#: for the shipped adapter (no preset applies to ``background_removal``); entries land with the adapters that a
#: preset actually configures (e.g. ``condensate_puncta_refinement``), one per phase.
_PRESET_WORKFLOW: dict = {}


def material_params(step_name: str, intent=None) -> tuple:
    """The declared material parameters of ``step_name`` (empty if none/unknown), resolved through the same
    op-id → module translation the adapters use so a production op-id step (``cellpose``, ``subcellular_segment``)
    finds its set. Where a module's material set depends on the target — segmentation surfaces the cellpose
    diameter for a cell and the six puncta thresholds for a condensate — ``intent`` selects the right one."""
    from .executor import adapter_module_for
    entry = _MATERIAL.get(adapter_module_for(step_name), ())
    return entry(intent) if callable(entry) else entry


def _coerce(param: StepParam, value):
    """Coerce a value to the parameter's kind, falling back to the default on a bad value (never raise into a
    run) — an int param must not carry a float that changes ``ceil`` downstream."""
    try:
        if param.kind == "int":
            return int(round(float(value)))
        if param.kind == "float":
            return float(value)
    except (TypeError, ValueError):
        return param.default
    return value


def _applicable_preset(step_name: str):
    """The single preset that applies to this step's workflow, or ``None``. If more than one applies we return
    ``None`` (picking one silently would be dishonest — that is a preset-picker choice, not a default)."""
    workflow = _PRESET_WORKFLOW.get(step_name)
    if not workflow:
        return None
    try:
        from pycat.utils.analysis_presets import presets_for
        applicable = presets_for(workflow)
    except Exception:      # broad-ok: optional_probe — no preset registry → seed from defaults, not a crash
        return None
    return applicable[0] if len(applicable) == 1 else None


def _seed_value(param: StepParam, preset, ctx):
    """Seed one parameter by the declared precedence: preset → session value → grounded default."""
    if preset is not None and param.name in getattr(preset, "parameters", {}):
        return _coerce(param, preset.parameters[param.name])
    if ctx is not None:
        try:
            cv = ctx.get(param.name)
        except Exception:      # broad-ok: optional_probe — a non-mapping context just means no session value
            cv = None
        if cv is not None:
            return _coerce(param, cv)
    return param.default


class ReviewedStep:
    """One step's reviewable parameters: seeded honestly, editable, tracking the deviation from the seed. The
    populate-but-never-lock contract of :class:`PresetApplication`, generalised to also cover the no-preset
    case (``preset_key`` is ``None`` then) — because provenance is owed whether or not a preset seeded it."""

    def __init__(self, step_name: str, params, *, preset=None, ctx=None):
        self.step_name = step_name
        self.params = tuple(params)
        self.preset = preset
        self._seed = {p.name: _seed_value(p, preset, ctx) for p in self.params}
        self.values = dict(self._seed)
        self._modified: set = set()

    def _param(self, name) -> Optional[StepParam]:
        return next((p for p in self.params if p.name == name), None)

    def set(self, name, value):
        """Set a value (coerced to its kind); mark it modified iff it differs from the seed. Setting it back to
        the seeded value clears the modification — a result that matches the seed was not 'modified'."""
        param = self._param(name)
        if param is None:
            return self
        v = _coerce(param, value)
        if self._seed.get(name) != v:
            self._modified.add(name)
        else:
            self._modified.discard(name)
        self.values[name] = v
        return self

    @property
    def is_modified(self) -> bool:
        return bool(self._modified)

    def provenance(self) -> dict:
        """The recorded provenance — the exact :meth:`PresetApplication.record` shape, with ``preset_key``
        ``None`` when no preset seeded the step. Travels into the run report and a saved template."""
        return {"preset_key": self.preset.key if self.preset is not None else None,
                "modified_parameters": sorted(self._modified),
                "is_modified": self.is_modified,
                "parameters": dict(self.values)}


class ParamReview:
    """The reviewable parameter set for a whole plan — the adapter-covered, runnable steps that declare
    material params, each a :class:`ReviewedStep`. The dock renders an editable form over it; the executor
    reads :meth:`params_by_step` / :meth:`provenance_by_step`."""

    def __init__(self, steps):
        self.steps = list(steps)

    @property
    def is_empty(self) -> bool:
        return not self.steps

    def step(self, name) -> Optional[ReviewedStep]:
        return next((s for s in self.steps if s.step_name == name), None)

    def params_by_step(self) -> dict:
        return {s.step_name: dict(s.values) for s in self.steps}

    def provenance_by_step(self) -> dict:
        return {s.step_name: s.provenance() for s in self.steps}


def build_param_review(plan, ctx=None) -> ParamReview:
    """Build the reviewable parameter set for ``plan``: one :class:`ReviewedStep` per adapter-covered step that
    will actually run (``run`` / ``caveat`` in :func:`execution_order`) and declares material params, seeded by
    the declared precedence. Blocked/skipped steps carry no review (they will not run). Never invents a
    parameter — a step with no declared material params contributes nothing to review."""
    reviewed = []
    intent = getattr(plan, "intent", None)
    try:
        order = execution_order(plan)
    except Exception:      # broad-ok: optional_probe — an un-orderable plan yields an empty review, not a crash
        order = []
    for es in order:
        if es.status not in ("run", "caveat"):
            continue
        # Only steps that will ACTUALLY run get a review — a coarse module whose variant is not auto-runnable
        # (its batch step resolves to None) must not surface a parameter for a step that won't execute.
        if resolve_batch_step(es.name, intent) is None:
            continue
        params = material_params(es.name, intent)
        if not params:
            continue
        reviewed.append(ReviewedStep(es.name, params, preset=_applicable_preset(es.name), ctx=ctx))
    return ParamReview(reviewed)
