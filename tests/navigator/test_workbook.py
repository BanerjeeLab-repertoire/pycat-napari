"""
Tests that verify the engine is faithful to the REAL project workbooks
(data/*.xlsx). These are skipped automatically if openpyxl or the data files
are unavailable, so the standalone tests still run anywhere.
"""
import pytest

openpyxl = pytest.importorskip("openpyxl")
from pycat.navigator import (AnalysisContext, AnalysisIntent, Planner, Source,
                             build_registry_from_workbook, data_available,
                             load_pipelines, load_question_tree, load_raw_modules,
                             load_tag_vocab)
from pycat.navigator.tags import (VALID_ROLES, VALID_REPRESENTATIONS, STATE_ORDER,
                                  VALID_QUALITY_STATUS, VALID_ANALYSIS_READY_FOR)
from pycat.navigator.loader import _overlays

pytestmark = pytest.mark.skipif(not data_available(),
                                reason="workbooks not present in data/")


# --------------------------------------------------------------------------- #
# fidelity to the module capability map                                       #
# --------------------------------------------------------------------------- #
def test_all_75_modules_load():
    raws = load_raw_modules()
    # the sheet has 75 modules (incl. UI wrappers and infrastructure)
    assert len(raws) == 75
    names = {m.name for m in raws}
    for expected in ["condensate_physics_tools", "frap_tools", "segmentation_tools",
                     "spatial_metrology_tools", "vpt_tools", "timeseries_condensate_tools"]:
        assert expected in names


def test_overlay_names_are_real():
    """Every curated overlay must reference a real module (or the documented
    synthetic 'acquisition'). Guards against typo'd module names."""
    real = {m.name for m in load_raw_modules()} | {"acquisition"}
    for name in _overlays():
        assert name in real, f"overlay {name!r} is not a real module"


def test_registry_from_workbook_registers_science_modules():
    reg = build_registry_from_workbook()
    # UI wrappers are skipped; scientific + overlaid modules present
    assert "condensate_physics_tools" in reg
    assert "segmentation_tools" in reg
    assert "advanced_analysis_ui" not in reg          # UI shell skipped
    assert len(reg) > 40


# --------------------------------------------------------------------------- #
# tag vocabularies match the workbook exactly                                 #
# --------------------------------------------------------------------------- #
def test_tag_vocabularies_match_workbook():
    vocab = load_tag_vocab()
    assert set(vocab["role"]) == set(VALID_ROLES)
    assert set(vocab["representation"]) == set(VALID_REPRESENTATIONS)
    assert set(vocab["state"]) == set(STATE_ORDER)
    assert set(vocab["quality_status"]) == set(VALID_QUALITY_STATUS)
    assert set(vocab["analysis_ready_for"]) == set(VALID_ANALYSIS_READY_FOR)


# --------------------------------------------------------------------------- #
# the 13 pipelines load (regression oracle)                                   #
# --------------------------------------------------------------------------- #
def test_thirteen_pipelines_load():
    pipes = load_pipelines()
    assert len(pipes) == 13
    names = {p.name for p in pipes}
    for expected in ["Cellular Object Analysis (Fluorescence)",
                     "Time-Series Cellular Object Analysis", "FRAP",
                     "Video Particle Tracking", "Droplet Fusion"]:
        assert expected in names
    # every pipeline has ordered steps
    for p in pipes:
        assert len(p.steps) >= 3


def test_planner_reproduces_timeseries_condensate_spine():
    """Design Notes #10: the generator should reproduce the scientific spine of
    a canonical pipeline. For 'Time-Series Cellular Object Analysis' the spine is
    cell segmentation -> time-series condensate analysis -> physics."""
    reg = build_registry_from_workbook()
    ctx = AnalysisContext()
    ctx.set("modality", "fluorescence", source=Source.METADATA)
    ctx.set("axes", ["time"], source=Source.METADATA)
    ctx.set("time_points", 120, source=Source.METADATA)
    intent = AnalysisIntent(target="condensate", observables=["fusion", "coarsening"])
    plan = Planner(reg).compile(intent, ctx)
    names = set(plan.ordered_modules)
    # the physics interpreter and a real time-series producer must appear,
    # using REAL module names from the workbook
    assert "condensate_physics_tools" in names
    assert names & {"timeseries_condensate_tools", "dynamic_spatial_tools"}
    assert "acquisition" in names


def test_colocalization_uses_real_module():
    reg = build_registry_from_workbook()
    ctx = AnalysisContext()
    ctx.set("channels", 2, source=Source.METADATA)
    intent = AnalysisIntent(target="condensate", observables=["colocalization"])
    plan = Planner(reg).compile(intent, ctx)
    names = set(plan.ordered_modules)
    assert names & {"pixel_wise_corr_analysis_tools", "obj_based_coloc_analysis_tools"}


# --------------------------------------------------------------------------- #
# the question tree loads with the real root                                  #
# --------------------------------------------------------------------------- #
def test_question_tree_loads_with_root_intent():
    tree = load_question_tree()
    assert "Q001" in tree
    root = tree["Q001"]
    assert root.stage == "Intent"
    assert "What are you trying to determine?" in root.question
    # root offers the five intents from the Response Ontology
    responses = " ".join(r["response"].lower() for r in root.responses)
    for intent_word in ["morphology", "spatial", "time", "physical", "suitability"]:
        assert intent_word in responses
