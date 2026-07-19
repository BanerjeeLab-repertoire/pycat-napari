"""
pycat_navigator
===============

A framework-agnostic reference implementation of the *question-driven,
capability-based* architecture proposed in the PyCAT brainstorming PDFs. Pure
Python, no GUI dependencies — a napari/Qt "methods widget" binds to this engine
rather than containing the logic.

Pipeline of ideas, end to end:

    scientist question
        -> AnalysisIntent            (contracts.AnalysisIntent)
        -> QuestionEngine            (adaptive, registry-derived questions)
        -> Planner.compile           (backward-chained, editable workflow)
        -> validity gates            (staged: static + probe)
        -> TaggedLayerFactory        (single tagging choke point)
        -> Resolver                  (tag-based layer selection)

See ``docs/PyCAT_Scientific_Navigator_Architecture.md`` for the stress-test and
the mapping back to each PDF.
"""
from .capabilities import (Capability, InformationRole, Observable, Question,
                           Representation, cap, representation_satisfies)
from .context import AnalysisContext, Fact, Source
from .contracts import (AnalysisIntent, Assumption, CostModel, GateStatus,
                        ModuleContract)
from .gates import (StagedGates, probe_gate, required_probe_observables,
                    stage_gates, static_gate)
from .modules import build_registry
from .loader import (build_registry_from_workbook, data_available,
                     load_pipelines, load_question_tree, load_raw_modules,
                     load_tag_vocab, Pipeline, QNode, RawModule)
from .op_catalog import (build_catalog_document, build_operation_registry,
                         catalog_available, load_operation_catalog)
from .planner import ContextGap, Plan, Planner, PlanStep, default_selection_policy
from .adapters import (InMemoryLayerResolver, LayerBinding, LayerResolverProtocol,
                       SessionLayer, capability_to_query)
from .question_engine import Choice, HybridQuestionEngine, QuestionEngine, QuestionSpec
from .scientific_tree import ScientificTree, TreeState
from .registry import ModuleRegistry
from .tags import (Layer, Resolver, TaggedLayerFactory, TagSet,
                   LINEAGE_RELATIONS, STATE_ORDER, VALID_SOURCES)

__all__ = [
    "Capability", "InformationRole", "Observable", "Question", "Representation",
    "cap", "representation_satisfies", "AnalysisContext", "Fact", "Source",
    "AnalysisIntent", "Assumption", "CostModel", "GateStatus", "ModuleContract",
    "StagedGates", "probe_gate", "required_probe_observables", "stage_gates",
    "static_gate", "build_registry", "ContextGap", "Plan", "Planner", "PlanStep",
    "default_selection_policy", "Choice", "QuestionEngine", "QuestionSpec",
    "ModuleRegistry", "Layer", "Resolver", "TaggedLayerFactory", "TagSet",
    "LINEAGE_RELATIONS", "STATE_ORDER", "VALID_SOURCES",
    "build_registry_from_workbook", "data_available", "load_pipelines",
    "load_question_tree", "load_raw_modules", "load_tag_vocab",
    "Pipeline", "QNode", "RawModule",
    "build_catalog_document", "build_operation_registry", "catalog_available",
    "load_operation_catalog",
    "HybridQuestionEngine", "ScientificTree", "TreeState",
    "InMemoryLayerResolver", "LayerBinding", "LayerResolverProtocol",
    "SessionLayer", "capability_to_query",
]

__version__ = "0.1.0"
