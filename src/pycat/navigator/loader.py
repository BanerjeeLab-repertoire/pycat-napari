"""
loader.py
=========

Ingests the three project workbooks directly into the engine's data structures,
so the workbooks are the single source of truth (Design Notes #6/#9: "serialize
answers... use stable response IDs behind editable wording"). Nothing here is
hand-transcribed prose; it is read from:

    data/PyCAT_module_information_contracts.xlsx     -> 75 module records
    data/PyCAT_layer_tag_hierarchy_and_module_flow.xlsx -> tag vocab, findings, resolver examples
    data/PyCAT_question_tree_and_method_mapping.xlsx -> question tree, 13 pipelines, ontology

For planning, the prose Input/Output columns are not machine-typed, so a small
**curated capability overlay** (`CAPABILITY_OVERLAYS`) assigns typed
requires/provides/observables to the planning-relevant modules, derived directly
from the real Input/Output/observable/prerequisite columns. Every overlay name
is asserted to exist in the workbook (`test_workbook.py`), so an overlay can
never silently reference a module that isn't real.

Requires ``openpyxl``. If the workbooks or openpyxl are missing, callers should
fall back to ``modules.build_registry()`` (the standalone seed).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .capabilities import InformationRole, Representation, cap
from .contracts import CostModel, ModuleContract
from .gates import probe_gate, static_gate

_DATA = os.path.join(os.path.dirname(__file__), "data")
WB_MODULES = "PyCAT_module_information_contracts.xlsx"
WB_TAGS = "PyCAT_layer_tag_hierarchy_and_module_flow.xlsx"
WB_QUESTIONS = "PyCAT_question_tree_and_method_mapping.xlsx"


def _rows(workbook: str, sheet: str) -> List[list]:
    import openpyxl
    path = os.path.join(_DATA, workbook)
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet]
    out = [["" if c is None else str(c).strip() for c in row]
           for row in ws.iter_rows(values_only=True)]
    wb.close()
    return out


def data_available() -> bool:
    return os.path.isdir(_DATA) and os.path.exists(os.path.join(_DATA, WB_MODULES))


# --------------------------------------------------------------------------- #
# Raw module records (full fidelity, all 75 modules)                          #
# --------------------------------------------------------------------------- #
@dataclass
class RawModule:
    name: str
    module_type: str
    info_role_text: str
    input: str
    output: str
    purpose: str
    observable: str
    prerequisites: str
    public_api: str
    source: str

    @property
    def is_ui(self) -> bool:
        return self.module_type.lower().startswith("ui") or self.name.endswith("_ui")

    @property
    def is_infrastructure(self) -> bool:
        return "infrastructure" in self.info_role_text.lower()


# map the sheet's free-text "Information role" onto the normative enum. The
# sheet uses composite roles like "Create + transform + measure"; we take the
# PRIMARY (first) role, which is what governs where the module sits in the graph.
_ROLE_WORDS = {
    "create": InformationRole.CREATE, "transform": InformationRole.TRANSFORM,
    "measure": InformationRole.MEASURE, "interpret": InformationRole.INTERPRET,
    "communicate": InformationRole.COMMUNICATE, "coordinate": InformationRole.COORDINATE,
    "infrastructure": InformationRole.INFRASTRUCTURE, "validate": InformationRole.INTERPRET,
    "guide": InformationRole.COORDINATE, "bridge": InformationRole.CREATE, "ui": InformationRole.COORDINATE,
}


def _primary_role(text: str) -> InformationRole:
    for token in text.replace("/", " ").replace("+", " ").split():
        t = token.strip().lower()
        if t in _ROLE_WORDS:
            return _ROLE_WORDS[t]
    return InformationRole.COORDINATE


def load_raw_modules() -> List[RawModule]:
    rows = _rows(WB_MODULES, "Module Capability Map")
    # find header row (starts with 'Module')
    header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "Module")
    out = []
    for r in rows[header_idx + 1:]:
        if not r or not r[0]:
            continue
        r = (r + [""] * 10)[:10]
        out.append(RawModule(*r))
    return out


# --------------------------------------------------------------------------- #
# Curated typed overlays for planning-relevant modules.                        #
# Derived from the real Input / Output / observable / prerequisite columns.    #
# Keys MUST be real module names (asserted in tests).                          #
# --------------------------------------------------------------------------- #
R = Representation


def _overlays() -> Dict[str, dict]:
    return {
        # loaded acquisition is not a module in the sheet; we add a synthetic
        # source so product chains can terminate (documented as synthetic).
        "acquisition": dict(
            info_role=InformationRole.INFRASTRUCTURE,
            provides=[cap(R.INTENSITY_FIELD, "target:*")], preference=0.9,
            purpose="Loaded image data (synthetic source; not a toolbox module).",
            synthetic=True),

        "data_qc_tools": dict(
            requires_inputs=[cap(R.INTENSITY_FIELD, "target:*")],
            provides=[cap(R.MEASUREMENT_TABLE, "kind:qc")],
            observables=["snr", "focus", "saturation", "sampling"],
            preference=0.6),

        "image_processing_tools": dict(
            requires_inputs=[cap(R.INTENSITY_FIELD, "target:*")],
            provides=[cap(R.INTENSITY_FIELD, "target:*", "state:corrected")],
            propagates_tags=frozenset({"target"}), preference=0.4,
            cost=CostModel(base_seconds=2, per_megapixel=0.3)),

        "segmentation_tools": dict(
            requires_inputs=[cap(R.INTENSITY_FIELD, "target:*")],
            provides=[cap(R.INSTANCE_LABELS, "target:*")],
            propagates_tags=frozenset({"target"}), preference=0.7,
            cost=CostModel(base_seconds=5, per_megapixel=1.5),
            probe=("seg.snr", "Segmentation needs adequate signal-to-noise.",
                   "snr", "snr", 3.0)),

        "ts_cellpose_tools": dict(
            requires_inputs=[cap(R.INTENSITY_FIELD, "target:cell")],
            requires_context=["time_series"],
            provides=[cap(R.INSTANCE_LABELS, "target:cell")],
            propagates_tags=frozenset({"target"}), preference=0.7,
            cost=CostModel(base_seconds=6, per_frame=0.3)),

        "feature_analysis_tools": dict(
            requires_inputs=[cap(R.INSTANCE_LABELS, "target:*")],
            provides=[cap(R.MEASUREMENT_TABLE, "target:*", "observable:*")],
            propagates_tags=frozenset({"target"}),
            observables=["count", "size", "shape", "intensity", "morphology", "texture"],
            preference=0.7),

        "timeseries_condensate_tools": dict(
            requires_inputs=[cap(R.INSTANCE_LABELS, "target:cell")],
            requires_context=["time_series"],
            provides=[cap(R.TRAJECTORIES, "target:condensate"),
                      cap(R.MEASUREMENT_TABLE, "target:condensate", "observable:*")],
            propagates_tags=frozenset({"target"}),
            observables=["count", "size", "intensity", "coarsening"],
            preference=0.75, cost=CostModel(base_seconds=8, per_frame=0.3)),

        "dynamic_spatial_tools": dict(
            requires_inputs=[cap(R.INSTANCE_LABELS, "target:*")],
            requires_context=["time_series"],
            provides=[cap(R.TRAJECTORIES, "target:*")],
            propagates_tags=frozenset({"target"}),
            observables=["motion", "fusion"], preference=0.65,
            cost=CostModel(base_seconds=5, per_frame=0.2)),

        "condensate_physics_tools": dict(
            requires_inputs=[cap(R.TRAJECTORIES, "target:*")],
            provides=[cap(R.MODEL_FIT, "target:*", "observable:*")],
            propagates_tags=frozenset({"target"}),
            observables=["fusion", "coarsening", "diffusion", "viscosity", "motion"],
            requires_context=[], preference=0.7,
            cost=CostModel(base_seconds=6, per_frame=0.1),
            gate=("phys.calibrated",
                  "Physical parameters (diffusion, viscosity) require calibration.",
                  "calibrated")),

        "fusion_tools": dict(
            requires_inputs=[cap(R.TRAJECTORIES, "target:*")],
            requires_context=["time_series"],
            provides=[cap(R.MODEL_FIT, "observable:fusion")],
            observables=["fusion"], preference=0.6),

        "pixel_wise_corr_analysis_tools": dict(
            requires_inputs=[cap(R.INTENSITY_FIELD, "target:*")],
            requires_context=["two_channels"],
            provides=[cap(R.MEASUREMENT_TABLE, "observable:colocalization")],
            observables=["colocalization"], preference=0.7),

        "obj_based_coloc_analysis_tools": dict(
            requires_inputs=[cap(R.INSTANCE_LABELS, "target:*")],
            requires_context=["two_channels"],
            provides=[cap(R.MEASUREMENT_TABLE, "observable:colocalization")],
            observables=["colocalization"], preference=0.65),

        "spatial_metrology_tools": dict(
            requires_inputs=[cap(R.INSTANCE_LABELS, "target:*")],
            provides=[cap(R.MEASUREMENT_TABLE, "target:*", "observable:*")],
            propagates_tags=frozenset({"target"}),
            observables=["clustering", "nearest_neighbor", "spatial_organization"],
            preference=0.6),

        "frap_tools": dict(
            requires_inputs=[cap(R.INTENSITY_FIELD, "target:*")],
            requires_context=["time_series"],
            provides=[cap(R.MODEL_FIT, "observable:mobile_fraction")],
            observables=["mobile_fraction", "diffusion"], preference=0.6),

        "vpt_tools": dict(
            requires_inputs=[cap(R.INTENSITY_FIELD, "target:bead")],
            requires_context=["time_series"],
            provides=[cap(R.TRAJECTORIES, "target:bead"),
                      cap(R.MODEL_FIT, "observable:viscosity")],
            observables=["motion", "diffusion", "viscosity"], preference=0.6),

        "morphological_complexity_tools": dict(
            requires_inputs=[cap(R.INSTANCE_LABELS, "target:*")],
            provides=[cap(R.MEASUREMENT_TABLE, "observable:topology")],
            observables=["topology", "connectivity", "morphology"], preference=0.55),

        "analysis_plots": dict(
            info_role=InformationRole.COMMUNICATE,
            requires_inputs=[cap(R.MEASUREMENT_TABLE)],
            provides=[cap("table", "kind:figure")], preference=0.5),
    }


def build_registry_from_workbook(include_all_modules: bool = True):
    """Return a ModuleRegistry populated from the real workbook. Planning-
    relevant modules get typed contracts from the overlays; the rest are
    registered with role + prose only (visible to the registry, inert to the
    planner until someone adds an overlay — which is pure data entry)."""
    from .registry import ModuleRegistry
    reg = ModuleRegistry()
    overlays = _overlays()
    raws = {m.name: m for m in load_raw_modules()}

    # synthetic + overlaid modules first
    for name, ov in overlays.items():
        raw = raws.get(name)
        contract = ModuleContract(
            name=name,
            info_role=ov.get("info_role") or (_primary_role(raw.info_role_text) if raw else InformationRole.COORDINATE),
            purpose=ov.get("purpose") or (raw.purpose if raw else ""),
            provides=ov.get("provides", []),
            requires_inputs=ov.get("requires_inputs", []),
            requires_context=ov.get("requires_context", []),
            observables=ov.get("observables", []),
            propagates_tags=ov.get("propagates_tags", frozenset()),
            preference=ov.get("preference", 0.5),
            cost=ov.get("cost", CostModel()),
            public_api=(raw.public_api if raw else ""),
            source=(raw.source if raw else "synthetic"),
        )
        if "probe" in ov:
            pid, desc, obs, key, mn = ov["probe"]
            contract.assumptions.append(probe_gate(pid, desc, observable=obs,
                                                    threshold_key=key, min_value=mn))
        if "gate" in ov:
            gid, desc, ckey = ov["gate"]
            contract.assumptions.append(static_gate(
                gid, desc, predicate=lambda ctx, k=ckey: ctx.context_requirement(k)))
        reg.register(contract)

    if include_all_modules:
        for name, raw in raws.items():
            if name in reg or raw.is_ui:      # skip UI wrappers (Coordinate shells)
                continue
            reg.register(ModuleContract(
                name=name, info_role=_primary_role(raw.info_role_text),
                purpose=raw.purpose, public_api=raw.public_api, source=raw.source,
                observables=[]))
    return reg


# --------------------------------------------------------------------------- #
# The 13 existing pipelines (regression oracle — Design Notes #10)             #
# --------------------------------------------------------------------------- #
@dataclass
class Pipeline:
    name: str
    steps: List[dict] = field(default_factory=list)   # {order, step_key, display, optional}


def load_pipelines() -> List[Pipeline]:
    rows = _rows(WB_QUESTIONS, "Existing Pipelines")
    header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "Pipeline")
    pipes: Dict[str, Pipeline] = {}
    for r in rows[header_idx + 1:]:
        if not r or not r[0]:
            continue
        name, order, key, display, optional = (r + [""] * 6)[:5]
        pipes.setdefault(name, Pipeline(name))
        pipes[name].steps.append(dict(order=order, step_key=key, display=display,
                                      optional=(optional.strip().lower() == "yes")))
    return list(pipes.values())


# --------------------------------------------------------------------------- #
# The deterministic question tree                                             #
# --------------------------------------------------------------------------- #
@dataclass
class QNode:
    qid: str
    parent: str
    stage: str
    question: str
    responses: List[dict] = field(default_factory=list)  # {response, next, outcome, tools, req, opt, why}


def load_question_tree() -> Dict[str, QNode]:
    rows = _rows(WB_QUESTIONS, "Question Tree")
    header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "Question ID")
    nodes: Dict[str, QNode] = {}
    for r in rows[header_idx + 1:]:
        if not r or not r[0]:
            continue
        r = (r + [""] * 13)[:13]
        qid, parent, stage, question, response, nxt, outcome, tools, req, opt, why, scope, ev = r
        if qid not in nodes:
            nodes[qid] = QNode(qid, parent, stage, question)
        nodes[qid].responses.append(dict(response=response, next=nxt, outcome=outcome,
                                         tools=tools, required=req, optional=opt, why=why))
    return nodes


# --------------------------------------------------------------------------- #
# Tag vocabularies straight from the workbook (for verification)              #
# --------------------------------------------------------------------------- #
def load_tag_vocab() -> Dict[str, List[str]]:
    rows = _rows(WB_TAGS, "Tag Hierarchy")
    header_idx = next(i for i, r in enumerate(rows) if len(r) > 1 and r[1] == "Tag key")
    vocab: Dict[str, List[str]] = {}
    for r in rows[header_idx + 1:]:
        if len(r) < 3 or not r[1]:
            continue
        key, meaning = r[1], r[2]
        # values are pipe-separated enumerations in the 'Meaning' column
        if "|" in meaning:
            vocab[key] = [v.strip() for v in meaning.split("|")]
    return vocab
