"""
op_catalog.py  —  Phase 2: planning at OPERATION granularity
============================================================

Review finding #6: planning at *module* (file) granularity stops the reasoning
chain at "Question -> Python file" instead of "Question -> observable ->
estimator -> algorithm". A toolbox file like ``condensate_physics_tools`` holds
MSD computation, anomalous-diffusion fitting, coarsening fitting, fusion-
relaxation fitting, survival analysis — distinct operations that answer distinct
questions. The registry unit must be the *operation*, not the file.

Two sources of truth, and they are legitimately disjoint
--------------------------------------------------------
Extracting the real code showed that ``@tags_layer`` decorates only the
**layer-producing** operations (segmentation, preprocessing, detection). Every
measurement/interpretation module is undecorated, because it emits tables/fits,
not layers. So:

* **Create / Transform operations** come from the op-registry, extracted from the
  real ``@tags_layer`` decorators into ``data/operation_catalog.json`` by
  ``tools/extract_operations.py``. ``provides`` and ``target`` are bootstrapped
  from the code that already declares them — NOT hand-typed. This is what makes
  "the code is the source of truth" actually true for the front half of a
  pipeline.

* **Measure / Interpret operations** come from the capability map's Public-API
  column (workbook truth), curated below with real function names. The op-
  registry cannot supply these because they don't make layers.

The result is an operation-level :class:`ModuleRegistry` the existing
:class:`Planner` consumes unchanged — it already plans over contracts; the
contracts are just finer-grained now, named e.g. ``subcellular_segment`` or
``condensate_physics.fit_fusion_relaxation``.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .capabilities import Capability, InformationRole, Representation, cap
from .contracts import CostModel, ModuleContract
from .gates import probe_gate
from .registry import ModuleRegistry

_DATA = os.path.join(os.path.dirname(__file__), "data")
CATALOG = "operation_catalog.json"
R = Representation


# --------------------------------------------------------------------------- #
# Role -> representation, and the DEFAULT requires rule.                        #
# These are role-level, documented, and few — not 79 per-op guesses. A curated #
# override can replace any of them (see MEASURE_OPS / _LAYER_OVERRIDES).        #
# --------------------------------------------------------------------------- #
_ROLE_TO_REPR = {
    "image": R.INTENSITY_FIELD.value,
    "mask": R.BINARY_MASK.value,
    "labels": R.INSTANCE_LABELS.value,
    "overlay": R.COORDINATES.value,      # points/tracks detections
    "result": R.MEASUREMENT_TABLE.value,
    "reference": R.INTENSITY_FIELD.value,
    "annotation": R.GEOMETRY.value,
}

# A layer op's produced role also implies what it consumes:
#   - a filtered image was made FROM an image        (transform)
#   - a mask/labels/detection was made FROM an image (segment/detect)
# reference/annotation have no computed input.
_REQUIRES_BY_ROLE = {
    "image": [cap(R.INTENSITY_FIELD, "target:*")],
    "mask": [cap(R.INTENSITY_FIELD, "target:*")],
    "labels": [cap(R.INTENSITY_FIELD, "target:*")],
    "overlay": [cap(R.INTENSITY_FIELD, "target:*")],
    "result": [cap(R.INTENSITY_FIELD, "target:*")],
    "reference": [],
    "annotation": [],
}

_INFO_ROLE_BY_ROLE = {
    "image": InformationRole.TRANSFORM,
    "mask": InformationRole.CREATE,
    "labels": InformationRole.CREATE,
    "overlay": InformationRole.CREATE,
    "result": InformationRole.MEASURE,
    "reference": InformationRole.TRANSFORM,
    "annotation": InformationRole.CREATE,
}

# operations whose produced role is 'image' are PREPROCESSING. Per PDF4 they must
# not be auto-inserted, so they advertise a *stateful* product (state:corrected/
# enhanced) that nothing requires — they stay available but inert to backward
# chaining. Only the acquisition provides a bare intensity_field.
_ENHANCE_OPS = {"clahe", "local_contrast", "tone_map", "ridge", "peak_edge",
                "log_enhance", "dpr"}

# The role-based `requires` rule assumes a labels/mask op segments an IMAGE. That
# is wrong for label/mask-EDITING ops, whose input is an existing labels/mask.
# These few are curated overrides (the requires that the produced role cannot
# imply). Everything else uses the role default. This is the honest boundary of
# what can be bootstrapped vs what needs curation.
_REQUIRES_OVERRIDE: Dict[str, List[Capability]] = {
    "contour_refine": [cap(R.INSTANCE_LABELS, "target:*")],
    "merge_mean_color": [cap(R.INSTANCE_LABELS, "target:*")],
    "split_watershed": [cap(R.INSTANCE_LABELS, "target:*")],
    "split_assessed": [cap(R.INSTANCE_LABELS, "target:*")],
    "expand_labels": [cap(R.INSTANCE_LABELS, "target:*")],
    "relabel": [cap(R.INSTANCE_LABELS, "target:*")],
    "two_layer_merge": [cap(R.INSTANCE_LABELS, "target:*")],
    "labels_to_mask": [cap(R.INSTANCE_LABELS, "target:*")],
    "label_mask": [cap(R.BINARY_MASK, "target:*")],
    "contour_filter": [cap(R.BINARY_MASK, "target:*")],
    "extend_edges": [cap(R.BINARY_MASK, "target:*")],
    "binary_open": [cap(R.BINARY_MASK, "target:*")],
    "binary_close": [cap(R.BINARY_MASK, "target:*")],
    "binary_morph": [cap(R.BINARY_MASK, "target:*")],
    "mask_merge": [cap(R.BINARY_MASK, "target:*")],
    "multi_merge": [cap(R.BINARY_MASK, "target:*")],
    "host_erode": [cap(R.BINARY_MASK, "target:*")],
    "drift_correct": [cap(R.COORDINATES, "target:*")],
    "void_detect": [cap(R.INSTANCE_LABELS, "target:*")],
    "topology_envelope": [cap(R.INTENSITY_FIELD, "target:*")],
    "optical_density": [cap(R.INSTANCE_LABELS, "target:*")],
}

# Primary image->objects segmenters: preferred providers of labels/masks. Label-
# editors and merges get a low preference so they are never the auto-selected
# object source (they refine an existing segmentation, they don't create one).
_PREF_OVERRIDE: Dict[str, float] = {
    "subcellular_segment": 0.66, "cellpose": 0.65, "cellpose_3d": 0.6,
    "subcellular_segment_3d": 0.6, "watershed": 0.55, "felzenszwalb": 0.5,
    "local_threshold": 0.5, "stardist": 0.55, "puncta_filter": 0.6,
    "bf_segment": 0.55, "clean": 0.55, "bead_detect": 0.6,
    # editors / merges — deliberately low
    "contour_refine": 0.2, "merge_mean_color": 0.2, "split_watershed": 0.25,
    "split_assessed": 0.25, "expand_labels": 0.15, "relabel": 0.1,
    "two_layer_merge": 0.2, "labels_to_mask": 0.15, "label_mask": 0.3,
    "mask_merge": 0.2, "multi_merge": 0.2, "contour_filter": 0.2,
    "extend_edges": 0.2, "binary_open": 0.2, "binary_close": 0.2, "binary_morph": 0.2,
}


def load_operation_catalog(path: Optional[str] = None) -> List[dict]:
    p = path or os.path.join(_DATA, CATALOG)
    with open(p) as fh:
        return json.load(fh)["operations"]


def catalog_available(path: Optional[str] = None) -> bool:
    return os.path.exists(path or os.path.join(_DATA, CATALOG))


# --------------------------------------------------------------------------- #
# Curated MEASURE / INTERPRET / TRACKING operations.                           #
# Real function names from the capability map's Public-API column. These are   #
# the objects->conclusions operations the op-registry can't supply.            #
# id = "<module_short>.<function>"; provides/requires/observables curated.      #
# --------------------------------------------------------------------------- #
def _measure_ops() -> List[dict]:
    T = "target:*"
    return [
        # ---- QC probe: image -> quality metrics (decides UNKNOWN probe gates) -
        dict(id="data_qc.assess", module="data_qc_tools",
             role=InformationRole.COORDINATE, provides=cap(R.MEASUREMENT_TABLE, "kind:qc"),
             requires=[cap(R.INTENSITY_FIELD, T)], context=[],
             observables=["snr", "focus", "saturation", "sampling"],
             propagate=False, preference=0.6,
             purpose="Assess focus/SNR/saturation/sampling suitability.",
             api="data_qc_tools.qc_snr"),

        # ---- tracking: labels(+time) -> trajectories ------------------------
        dict(id="dynamic_spatial.link_trajectories", module="dynamic_spatial_tools",
             role=InformationRole.CREATE, provides=cap(R.TRAJECTORIES, T),
             requires=[cap(R.INSTANCE_LABELS, T)], context=["time_series"],
             observables=["motion"], propagate=True, preference=0.7,
             purpose="Link objects across frames into trajectories.",
             api="dynamic_spatial_tools.link_trajectories"),
        dict(id="dynamic_spatial.detect_merge_fission", module="dynamic_spatial_tools",
             role=InformationRole.INTERPRET, provides=cap(R.MODEL_FIT, T, "observable:*"),
             requires=[cap(R.TRAJECTORIES, T)], context=["time_series"],
             observables=["fusion"], propagate=True, preference=0.65,
             purpose="Detect merge/fission events along trajectories.",
             api="dynamic_spatial_tools.detect_merge_fission"),
        dict(id="timeseries_condensate.link_condensates", module="timeseries_condensate_tools",
             role=InformationRole.CREATE, provides=cap(R.TRAJECTORIES, "target:condensate"),
             requires=[cap(R.INSTANCE_LABELS, "target:cell")], context=["time_series"],
             observables=[], propagate=False, preference=0.72,
             purpose="Per-cell condensate detection + linking across frames.",
             api="timeseries_condensate_tools.run_timeseries_condensate_analysis"),

        # ---- condensate physics: trajectories -> fits -----------------------
        dict(id="condensate_physics.compute_msd", module="condensate_physics_tools",
             role=InformationRole.INTERPRET, provides=cap(R.MODEL_FIT, T, "observable:diffusion"),
             requires=[cap(R.TRAJECTORIES, T)], context=["calibrated"],
             observables=["diffusion", "motion"], propagate=True, preference=0.7,
             purpose="Mean-squared-displacement transport analysis.",
             api="condensate_physics_tools.compute_msd"),
        dict(id="condensate_physics.fit_anomalous_diffusion", module="condensate_physics_tools",
             role=InformationRole.INTERPRET, provides=cap(R.MODEL_FIT, T, "observable:diffusion"),
             requires=[cap(R.TRAJECTORIES, T)], context=["calibrated"],
             observables=["diffusion", "viscosity"], propagate=True, preference=0.65,
             purpose="Fit anomalous-diffusion exponent / effective viscosity.",
             api="condensate_physics_tools.fit_anomalous_diffusion"),
        dict(id="condensate_physics.fit_coarsening", module="condensate_physics_tools",
             role=InformationRole.INTERPRET, provides=cap(R.MODEL_FIT, T, "observable:coarsening"),
             requires=[cap(R.TRAJECTORIES, T)], context=[],
             observables=["coarsening"], propagate=True, preference=0.75,
             purpose="Fit characteristic-size coarsening kinetics.",
             api="condensate_physics_tools.fit_coarsening"),
        dict(id="condensate_physics.fit_fusion_relaxation", module="condensate_physics_tools",
             role=InformationRole.INTERPRET, provides=cap(R.MODEL_FIT, T, "observable:fusion"),
             requires=[cap(R.TRAJECTORIES, T)], context=[],
             observables=["fusion"], propagate=True, preference=0.78,
             purpose="Fit post-merger aspect-ratio relaxation time.",
             api="condensate_physics_tools.fit_aspect_ratio_relaxation"),

        # ---- morphology / features: labels -> table -------------------------
        dict(id="feature_analysis.cell_analysis", module="feature_analysis_tools",
             role=InformationRole.MEASURE, provides=cap(R.MEASUREMENT_TABLE, T, "observable:*"),
             requires=[cap(R.INSTANCE_LABELS, T)], context=[],
             observables=["count", "size", "shape", "intensity", "morphology"],
             propagate=True, preference=0.72, purpose="Per-object/per-cell feature table.",
             api="feature_analysis_tools.run_cell_analysis_func"),
        dict(id="feature_analysis.texture", module="feature_analysis_tools",
             role=InformationRole.MEASURE, provides=cap(R.MEASUREMENT_TABLE, T, "observable:texture"),
             requires=[cap(R.INSTANCE_LABELS, T)], context=[],
             observables=["texture"], propagate=True, preference=0.6,
             purpose="GLCM / LBP texture features.",
             api="feature_analysis_tools.calculate_image_features"),
        dict(id="morphological_complexity.fractal", module="morphological_complexity_tools",
             role=InformationRole.MEASURE, provides=cap(R.MEASUREMENT_TABLE, T, "observable:topology"),
             requires=[cap(R.INSTANCE_LABELS, T)], context=[],
             observables=["topology", "connectivity", "morphology"], propagate=True,
             preference=0.55, purpose="Fractal dimension / lacunarity.",
             api="morphological_complexity_tools.fractal_dimension_box_counting"),

        # ---- spatial organization: labels/coords -> metrics -----------------
        dict(id="spatial_metrology.ripley", module="spatial_metrology_tools",
             role=InformationRole.MEASURE, provides=cap(R.MEASUREMENT_TABLE, T, "observable:*"),
             requires=[cap(R.INSTANCE_LABELS, T)], context=[],
             observables=["clustering", "nearest_neighbor", "spatial_organization"],
             propagate=True, preference=0.62, purpose="NND / Ripley / Voronoi / density.",
             api="spatial_metrology_tools.ripleys_l"),
        dict(id="organizational_metrics.spacing", module="organizational_metrics_tools",
             role=InformationRole.MEASURE, provides=cap(R.MEASUREMENT_TABLE, T, "observable:spatial_organization"),
             requires=[cap(R.INSTANCE_LABELS, T)], context=[],
             observables=["spatial_organization"], propagate=True, preference=0.55,
             purpose="Inter-condensate spacing / occupancy / entropy.",
             api="organizational_metrics_tools.run_all_organizational_metrics"),

        # ---- colocalization: two channels -> overlap ------------------------
        dict(id="pixel_wise_corr.pearson_manders", module="pixel_wise_corr_analysis_tools",
             role=InformationRole.MEASURE, provides=cap(R.MEASUREMENT_TABLE, "observable:colocalization"),
             requires=[cap(R.INTENSITY_FIELD, T)], context=["two_channels"],
             observables=["colocalization"], propagate=False, preference=0.7,
             purpose="Pixel Pearson / Manders / correlation length.",
             api="pixel_wise_corr_analysis_tools.pearsons_correlation"),
        dict(id="obj_based_coloc.manders", module="obj_based_coloc_analysis_tools",
             role=InformationRole.MEASURE, provides=cap(R.MEASUREMENT_TABLE, "observable:colocalization"),
             requires=[cap(R.INSTANCE_LABELS, T)], context=["two_channels"],
             observables=["colocalization"], propagate=False, preference=0.65,
             purpose="Object overlap / proximity / association.",
             api="obj_based_coloc_analysis_tools.manders_coloc"),

        # ---- biophysics: various -------------------------------------------
        dict(id="frap.fit_recovery", module="frap_tools",
             role=InformationRole.INTERPRET, provides=cap(R.MODEL_FIT, "observable:mobile_fraction"),
             requires=[cap(R.INTENSITY_FIELD, T)], context=["time_series"],
             observables=["mobile_fraction", "diffusion"], propagate=False, preference=0.6,
             purpose="FRAP recovery normalization + kinetic fit.",
             api="frap_tools.frap_recovery_model"),
        dict(id="vpt.microrheology", module="vpt_tools",
             role=InformationRole.INTERPRET, provides=cap(R.MODEL_FIT, "observable:viscosity"),
             requires=[cap(R.TRAJECTORIES, "target:bead")], context=["calibrated"],
             observables=["viscosity", "diffusion"], propagate=False, preference=0.6,
             purpose="Bead-tracking MSD -> viscosity / moduli.",
             api="vpt_tools.run_vpt_microrheology"),
        dict(id="partition_enrichment.client", module="partition_enrichment_tools",
             role=InformationRole.MEASURE, provides=cap(R.MEASUREMENT_TABLE, T, "observable:partitioning"),
             requires=[cap(R.INSTANCE_LABELS, T)], context=[],
             observables=["partitioning"], propagate=True, preference=0.6,
             purpose="Client enrichment / partition coefficient.",
             api="partition_enrichment_tools.client_enrichment"),
        dict(id="invitro.size_distribution", module="invitro_tools",
             role=InformationRole.INTERPRET, provides=cap(R.MODEL_FIT, T, "observable:size"),
             requires=[cap(R.INSTANCE_LABELS, T)], context=[],
             observables=["size", "count", "saturation_concentration"], propagate=True,
             preference=0.6, purpose="Field summary / size-distribution / C_sat.",
             api="invitro_tools.fit_size_distribution"),
    ]


# --------------------------------------------------------------------------- #
# Build the operation-level registry                                          #
# --------------------------------------------------------------------------- #
def _repr_for(role: str) -> str:
    return _ROLE_TO_REPR.get(role, R.INTENSITY_FIELD.value)


def _layer_op_contract(o: dict) -> ModuleContract:
    role = o.get("produces") or o.get("role")
    representation = _repr_for(role)
    target = o.get("target")

    # provides: a specific target if the op declares one, else target:* +
    # propagate (segments/filters whatever target the input image carried).
    if target:
        provides = [cap(representation, f"target:{target}")]
        propagate = frozenset()
    else:
        provides = [cap(representation, "target:*")]
        propagate = frozenset({"target"})

    # preprocessing (produces image) advertises a STATEFUL product so it is not
    # auto-inserted (PDF4). Nothing requires a state, so it stays inert.
    if role == "image":
        state = "enhanced" if o["op"] in _ENHANCE_OPS else "corrected"
        provides = [cap(representation, "target:*", f"state:{state}")]
        propagate = frozenset({"target"})

    requires = _REQUIRES_OVERRIDE.get(o["op"], list(_REQUIRES_BY_ROLE.get(role, [])))

    # primary image->objects segmenters carry an SNR probe gate, so an UNKNOWN
    # SNR triggers a QC probe insertion (staged gating). Label-editing ops (which
    # consume labels, not an image) do not.
    assumptions = []
    if role in ("labels", "mask") and o["op"] not in _REQUIRES_OVERRIDE:
        assumptions = [probe_gate(
            "seg.snr", "Segmentation needs adequate signal-to-noise.",
            observable="snr", threshold_key="snr", min_value=3.0,
            rationale="Below SNR≈3 object boundaries are unreliable.")]

    return ModuleContract(
        name=o["op"],
        info_role=_INFO_ROLE_BY_ROLE.get(role, InformationRole.TRANSFORM),
        purpose=o.get("summary", ""),
        provides=provides,
        requires_inputs=requires,
        propagates_tags=propagate,
        preference=_PREF_OVERRIDE.get(o["op"], 0.5),
        assumptions=assumptions,
        public_api=f"{o['module']}.{o['function']}",
        source=o.get("source", ""),
    )


def _measure_op_contract(o: dict) -> ModuleContract:
    provides = o["provides"]
    provides = provides if isinstance(provides, list) else [provides]
    return ModuleContract(
        name=o["id"],
        info_role=o["role"],
        purpose=o.get("purpose", ""),
        provides=provides,
        requires_inputs=list(o.get("requires", [])),
        requires_context=list(o.get("context", [])),
        observables=list(o.get("observables", [])),
        propagates_tags=frozenset({"target"}) if o.get("propagate") else frozenset(),
        preference=o.get("preference", 0.5),
        public_api=o.get("api", ""),
        source=f"src/pycat/toolbox/{o['module']}.py",
    )


def build_operation_registry(catalog_path: Optional[str] = None,
                             include_measure: bool = True,
                             from_spec: bool = True) -> ModuleRegistry:
    """Operation-granularity registry: layer ops, measure/interpret ops, and the acquisition source.

    **OperationSpec increment 4 — the flip from validate to generate.** The layer ops are now
    GENERATED from the live spec (``build_catalog_document`` → ``iter_operation_specs`` → the
    ``@tags_layer``/UI decorators) rather than READ from the committed ``operation_catalog.json``. The
    decorators are the runtime source of truth; the committed JSON is a reviewable, shippable *artifact*
    kept faithful by a regeneration guard, no longer authoritative at run time (the Navigator builds
    correctly even if the file is absent). Pass ``from_spec=False`` to build from the committed file
    instead — the two are equal by construction, so this is an escape hatch for tooling that wants the
    on-disk snapshot, not a behaviour difference.
    """
    reg = ModuleRegistry()

    # synthetic source of the loaded image
    reg.register(ModuleContract(
        name="acquisition", info_role=InformationRole.INFRASTRUCTURE,
        purpose="Loaded image data (synthetic source; not a toolbox op).",
        provides=[cap(R.INTENSITY_FIELD, "target:*")], preference=0.9,
        public_api="file_io.load()", source="synthetic"))

    layer_ops = (build_catalog_document(catalog_path)["operations"] if from_spec
                 else load_operation_catalog(catalog_path))

    seen = {"acquisition"}
    for o in layer_ops:
        if o["op"] in seen:            # op tags are globally unique by design
            continue
        seen.add(o["op"])
        reg.register(_layer_op_contract(o))

    if include_measure:
        for o in _measure_ops():
            if o["id"] in seen:
                continue
            seen.add(o["id"])
            reg.register(_measure_op_contract(o))
    return reg


# --------------------------------------------------------------------------- #
# Regeneration — the snapshot's fix-it button                                  #
#                                                                              #
# The drift guard (test_operation_spec_matches_catalog) fails LOUDLY when a    #
# @tags_layer decorator is added/removed/changed without regenerating this     #
# JSON. A guard that cannot be satisfied is a trap, so this is the one command #
# that rewrites the snapshot FROM the live decorators:                         #
#     python -m pycat.navigator.op_catalog --regenerate                        #
# The fix for legitimate drift is therefore "run the regen, commit the JSON".  #
# --------------------------------------------------------------------------- #
def _provenance_from_registered_by(registered_by: Optional[str]) -> dict:
    """Derive the snapshot's ``module`` / ``function`` / ``source`` / ``kind`` from a live op's
    ``registered_by`` (``module.qualname`` for a decorated fn, or the UI-op registrar marker).

    The layer-op contract reads ``module`` + ``function`` (``public_api``), so they must survive
    regeneration. Verified to reproduce all 79 current entries exactly.
    """
    rb = registered_by or ""
    if rb.endswith("(UI operation)"):
        return dict(module="tag_registry", function="_register_ui_operations",
                    source="src/pycat/utils/tag_registry.py", kind="ui")
    parts = rb.split(".")
    if len(parts) >= 2:
        return dict(module=parts[-2], function=parts[-1],
                    source="src/" + "/".join(parts[:-1]) + ".py", kind="toolbox")
    return dict(module="", function=rb, source="", kind="toolbox")


def build_catalog_document(path: Optional[str] = None) -> dict:
    """Build the catalog document **purely from the live spec** — ``iter_operation_specs()`` — with no
    file write. This is the "generate from the spec" half of OperationSpec increment 4: the document is
    a **deterministic function of the ``@tags_layer``/UI decorators**, not a hand-maintained file.

    One entry per layer op, id-sorted, provenance derived from each op's ``registered_by``.
    ``role_declared`` (informational — no consumer reads it) is preserved from the committed snapshot at
    ``path`` when present, else defaults to the op's role; that preservation is the document's only touch
    of the file, and it is idempotent, so ``build_catalog_document() == the committed JSON`` once
    regenerated (this is what the regeneration guard checks). Measure/interpret ops are NOT written: they
    are injected at build time by ``_measure_ops()`` and were never part of this JSON.
    """
    from .operation_spec import iter_operation_specs

    p = path or os.path.join(_DATA, CATALOG)

    # Preserve the (unread, informational) role_declared from the current snapshot if it exists.
    prior_role_declared: Dict[str, str] = {}
    try:
        with open(p) as fh:
            for o in json.load(fh).get("operations", []):
                if "role_declared" in o:
                    prior_role_declared[o["op"]] = o["role_declared"]
    except (FileNotFoundError, ValueError):
        pass

    operations = []
    for spec in iter_operation_specs():
        prov = _provenance_from_registered_by(spec.registered_by)
        operations.append({
            "op": spec.id,
            "role": spec.role,
            "produces": spec.produces,
            "target": spec.target,
            "inputs": list(spec.inputs),
            "summary": spec.summary,
            "aliases": list(spec.aliases),
            "module": prov["module"],
            "function": prov["function"],
            "source": prov["source"],
            "kind": prov["kind"],
            "role_declared": prior_role_declared.get(spec.id, spec.role),
        })

    operations.sort(key=lambda o: o["op"])
    return {"operations": operations, "count": len(operations)}


def regenerate_operation_catalog(path: Optional[str] = None) -> dict:
    """Rewrite ``operation_catalog.json`` from the LIVE registry, deterministically.

    A thin writer over :func:`build_catalog_document` — it builds the document from the spec and dumps
    it id-sorted with sorted keys, so the committed file is stable across runs and reviews. The fix for
    a failing regeneration guard is therefore "run this, commit the JSON". Returns the written document.
    """
    p = path or os.path.join(_DATA, CATALOG)
    doc = build_catalog_document(p)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return doc


if __name__ == "__main__":   # pragma: no cover - thin CLI wrapper
    import argparse

    ap = argparse.ArgumentParser(description="PyCAT Navigator operation-catalog tool")
    ap.add_argument("--regenerate", action="store_true",
                    help="Rewrite operation_catalog.json from the live @tags_layer/UI registry.")
    args = ap.parse_args()
    if args.regenerate:
        _doc = regenerate_operation_catalog()
        print(f"Regenerated {os.path.join(_DATA, CATALOG)} — {_doc['count']} operations.")
    else:
        ap.print_help()
