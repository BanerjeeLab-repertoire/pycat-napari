"""
canonical.py  —  the 13 established workflows as a regression oracle
====================================================================

Review finding #5: the "regression oracle" was a smoke test. This makes it real.
Each of the 13 fixed workflows (from ``workflow_checklist.py``, loaded via
``loader.load_pipelines``) becomes a :class:`CanonicalCase`: an intent + context,
the **operation IDs it must reproduce**, and the ones it must **not** produce.
``check_case`` compiles the intent over the operation registry and verifies both.

Honesty about coverage (review's "no silent caps"):
* ``reproducible`` cases map cleanly to the curated observable vocabulary and are
  asserted in CI (``tests/test_canonical.py``).
* ``needs_codebase`` cases depend on things the navigator cannot yet decide
  standalone — modality-/dimensionality-aware provider selection (brightfield vs
  fluorescence segmenters, 2D vs 3D), or specialized instrument workflows
  (temperature, force-distance) whose outputs aren't in the observable vocab.
  These are listed explicitly, not silently dropped, and are what the exact
  step→handler binding from ``ui/ui_modules.py`` would unlock.

``required``/``forbidden`` are stated as operation IDs, per the review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set

from .contracts import AnalysisIntent
from .context import AnalysisContext, Source


@dataclass
class CanonicalCase:
    pipeline_id: str
    target: str
    observables: List[str]
    context: Dict[str, object]
    required_ops: Set[str] = field(default_factory=set)
    forbidden_ops: Set[str] = field(default_factory=set)
    status: str = "reproducible"     # "reproducible" | "needs_codebase"
    note: str = ""


# Context builders --------------------------------------------------------- #
def _ctx(**facts) -> AnalysisContext:
    c = AnalysisContext()
    for k, v in facts.items():
        c.set(k, v, source=Source.METADATA)
    return c


TS = dict(axes=["time"], time_points=120)
TWO = dict(channels=2)


CANONICAL_CASES: List[CanonicalCase] = [
    # ---- reproducible from the curated observable vocabulary ---------------
    CanonicalCase(
        "Time-Series Cellular Object Analysis", "condensate", ["coarsening"], TS,
        required_ops={"condensate_physics.fit_coarsening"},
        forbidden_ops={"frap.fit_recovery", "vpt.microrheology"},
        note="ts_cellpose -> timeseries condensate -> coarsening kinetics"),
    CanonicalCase(
        "Droplet Fusion", "condensate", ["fusion"], TS,
        required_ops={"condensate_physics.fit_fusion_relaxation"},
        forbidden_ops={"frap.fit_recovery", "pixel_wise_corr.pearson_manders"},
        note="build fusion signal -> fit relaxation tau"),
    CanonicalCase(
        "Colocalization Analysis", "condensate", ["colocalization"], TWO,
        required_ops={"pixel_wise_corr.pearson_manders", "obj_based_coloc.manders"},
        forbidden_ops={"condensate_physics.fit_coarsening", "frap.fit_recovery"},
        note="either pixel or object coloc satisfies; both are acceptable providers",
        # required here means 'at least one of' — see check_case's any-of handling
    ),
    CanonicalCase(
        "FRAP", "condensate", ["mobile_fraction"], TS,
        required_ops={"frap.fit_recovery"},
        forbidden_ops={"cellpose", "condensate_physics.fit_coarsening"},
        note="ROI recovery normalization + kinetic fit; no segmentation"),
    CanonicalCase(
        "Video Particle Tracking", "bead", ["viscosity"], dict(axes=["time"], time_points=200, voxel_size=0.1),
        required_ops={"vpt.microrheology"},
        forbidden_ops={"frap.fit_recovery", "cellpose"},
        note="segment host -> detect beads -> link -> MSD/viscosity"),
    CanonicalCase(
        "Cellular Object Analysis (Fluorescence)", "condensate", ["size"], {},
        required_ops={"feature_analysis.cell_analysis"},
        forbidden_ops={"frap.fit_recovery", "vpt.microrheology"},
        note="segment -> per-object morphology"),
    CanonicalCase(
        "In Vitro Fluorescence", "droplet", ["size"], {},
        required_ops={"feature_analysis.cell_analysis"},
        forbidden_ops={"frap.fit_recovery"},
        note="segment droplets -> field summary / size distribution"),

    # ---- need codebase-level selection or specialized ops ------------------
    CanonicalCase(
        "Cellular Brightfield", "condensate", ["morphology"], dict(modality="brightfield"),
        status="needs_codebase",
        note="requires modality-aware selection to prefer bf_segment/optical_density "
             "over fluorescence segmenters — needs the step->handler map in ui_modules.py"),
    CanonicalCase(
        "In Vitro Brightfield", "droplet", ["morphology"], dict(modality="brightfield"),
        status="needs_codebase",
        note="same modality-aware selection gap as Cellular Brightfield"),
    CanonicalCase(
        "Z-Stack (3D)", "condensate", ["size"], dict(axes=["z"], voxel_size=0.2),
        status="needs_codebase",
        note="requires dimensionality-aware selection to prefer *_3d segmenters"),
    CanonicalCase(
        "Fibril Analysis", "fibril", ["topology"], {},
        status="needs_codebase",
        note="fibril graph/skeleton ops (build_skeleton_graph, fibril_morphometry) "
             "are not yet curated as measure operations"),
    CanonicalCase(
        "Temperature-Dependent", "condensate", ["saturation_concentration"], dict(axes=["time"], time_points=100),
        status="needs_codebase",
        note="temperature_tools turbidity/transition ops need a controlled observable "
             "(cloud/clearing point) not yet in the vocabulary"),
    CanonicalCase(
        "Force-Distance Curve", "field", ["motion"], {},
        status="needs_codebase",
        note="fd_curve_tools force-domain ops (rips, WLC) are outside the imaging "
             "observable vocabulary; specialized instrument workflow"),
]


def check_case(registry, case: CanonicalCase) -> dict:
    """Compile the case's intent and check required/forbidden operations.
    Returns a result dict; ``ok`` is None for needs_codebase cases (not asserted)."""
    from .planner import Planner
    if case.status == "needs_codebase":
        return dict(pipeline=case.pipeline_id, status=case.status, ok=None,
                    note=case.note)

    c = _ctx(**case.context)
    plan = Planner(registry).compile(
        AnalysisIntent(target=case.target, observables=case.observables), c)
    produced = set(plan.ordered_modules)

    # 'required' with several entries for the SAME role is treated as any-of when
    # the entries are alternative providers of one capability (coloc pixel/object).
    if case.pipeline_id == "Colocalization Analysis":
        req_ok = bool(case.required_ops & produced)
        missing = set() if req_ok else case.required_ops
    else:
        missing = case.required_ops - produced
        req_ok = not missing
    forbidden_hit = case.forbidden_ops & produced

    return dict(pipeline=case.pipeline_id, status=case.status,
                ok=(req_ok and not forbidden_hit),
                produced=sorted(produced), missing=sorted(missing),
                forbidden_hit=sorted(forbidden_hit),
                executable=plan.is_executable, note=case.note)
