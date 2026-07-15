"""
Phase 2 tests: operation-granularity registry, bootstrapped from the real
op-catalog (data/operation_catalog.json, extracted from @tags_layer decorators).
Skipped if the catalog isn't present.
"""
import pytest

from pycat.navigator import (AnalysisContext, AnalysisIntent, Planner, Source,
                             build_operation_registry, catalog_available,
                             load_operation_catalog)

pytestmark = pytest.mark.skipif(not catalog_available(),
                                reason="operation_catalog.json not present")


def _ts_ctx():
    c = AnalysisContext()
    c.set("axes", ["time"], source=Source.METADATA)
    c.set("time_points", 120, source=Source.METADATA)
    return c


# --------------------------------------------------------------------------- #
# catalog was extracted from the REAL decorators                              #
# --------------------------------------------------------------------------- #
def test_catalog_has_real_layer_operations():
    ops = {o["op"] for o in load_operation_catalog()}
    # these are real @tags_layer ops from the source
    for real in ["cellpose", "subcellular_segment", "watershed", "clahe",
                 "rolling_ball", "bandpass", "bead_detect"]:
        assert real in ops, f"{real} missing from extracted catalog"


def test_catalog_carries_produces_and_target_from_code():
    by = {o["op"]: o for o in load_operation_catalog()}
    # cellpose declares it produces cell labels — bootstrapped, not hand-typed
    assert by["cellpose"]["produces"] == "labels"
    assert by["cellpose"]["target"] == "cell"
    # a preprocessing op produces an image
    assert by["clahe"]["produces"] == "image"


# --------------------------------------------------------------------------- #
# operation-level registry + planning                                         #
# --------------------------------------------------------------------------- #
def test_registry_is_operation_granular():
    reg = build_operation_registry()
    # distinct condensate-physics FITS are separate contracts, not one file
    assert "condensate_physics.fit_coarsening" in reg
    assert "condensate_physics.fit_fusion_relaxation" in reg
    assert "condensate_physics.compute_msd" in reg
    # a whole-file contract must NOT exist
    assert "condensate_physics_tools" not in reg


def test_plan_stops_at_operation_not_file():
    reg = build_operation_registry()
    plan = Planner(reg).compile(
        AnalysisIntent(target="condensate", observables=["fusion"]), _ts_ctx())
    names = plan.ordered_modules
    assert "condensate_physics.fit_fusion_relaxation" in names
    # the terminal is a specific fit op bound to a real callable
    term = plan.steps[-1]
    assert term.module.public_api.startswith("condensate_physics_tools.")
    assert plan.is_executable


def test_coarsening_and_fusion_use_distinct_operations():
    reg = build_operation_registry()
    plan = Planner(reg).compile(
        AnalysisIntent(target="condensate", observables=["fusion", "coarsening"]), _ts_ctx())
    names = set(plan.ordered_modules)
    assert {"condensate_physics.fit_coarsening",
            "condensate_physics.fit_fusion_relaxation"}.issubset(names)


def test_segmenter_is_a_primary_not_an_editor():
    """The auto-selected object source must be a real segmenter, not a
    label-editing op like contour_refine/relabel."""
    reg = build_operation_registry()
    plan = Planner(reg).compile(
        AnalysisIntent(target="condensate", observables=["size"]), _ts_ctx())
    names = set(plan.ordered_modules)
    assert "subcellular_segment" in names
    for editor in ["contour_refine", "relabel", "expand_labels", "merge_mean_color"]:
        assert editor not in names


def test_pin_swaps_the_segmenter():
    reg = build_operation_registry()
    plan = Planner(reg).compile(
        AnalysisIntent(target="condensate", observables=["coarsening"]),
        _ts_ctx(), pins={"instance_labels": "watershed"})
    assert "watershed" in plan.ordered_modules
    assert "subcellular_segment" not in plan.ordered_modules


def test_preprocessing_not_auto_inserted_at_operation_level():
    reg = build_operation_registry()
    plan = Planner(reg).compile(
        AnalysisIntent(target="condensate", observables=["size"]), _ts_ctx())
    for pre in ["clahe", "gaussian", "rolling_ball", "bandpass", "dog"]:
        assert pre not in plan.ordered_modules


def test_colocalization_selects_pixel_or_object_operation():
    reg = build_operation_registry()
    c = AnalysisContext(); c.set("channels", 2, source=Source.METADATA)
    plan = Planner(reg).compile(
        AnalysisIntent(target="condensate", observables=["colocalization"]), c)
    names = set(plan.ordered_modules)
    assert names & {"pixel_wise_corr.pearson_manders", "obj_based_coloc.manders"}
