"""
Phase 3 tests: hybrid question engine, real-layer binding, probe insertion,
and the 13-pipeline regression oracle. Skipped if the operation catalog is
absent (these exercise the operation-level registry).
"""
import pytest

from pycat.navigator import (AnalysisContext, AnalysisIntent, HybridQuestionEngine,
                             InMemoryLayerResolver, Planner, ScientificTree,
                             SessionLayer, Source, build_operation_registry,
                             catalog_available, capability_to_query, cap,
                             Representation)
from pycat.navigator.canonical import CANONICAL_CASES, check_case

pytestmark = pytest.mark.skipif(not catalog_available(),
                                reason="operation_catalog.json not present")


def _ts():
    c = AnalysisContext()
    c.set("axes", ["time"], source=Source.METADATA)
    c.set("time_points", 120, source=Source.METADATA)
    return c


# --------------------------------------------------------------------------- #
# scientific tree + hybrid engine                                             #
# --------------------------------------------------------------------------- #
def test_broad_answer_selects_branch_not_observables():
    """Review #3: 'change over time' must NOT commit to motion+fusion+coarsening."""
    t = ScientificTree()
    st = t.walk(["Change over time", "Fusion or post-merger relaxation"])
    assert st.done
    assert st.observables == ["fusion"]
    assert "motion" not in st.observables and "coarsening" not in st.observables


def test_tree_reaches_distinct_leaves():
    t = ScientificTree()
    coloc = t.walk(["Spatial relationship or organization",
                    "Do two channels occupy the same pixels?", "Symmetric co-variation"])
    assert coloc.observables == ["colocalization"]
    morph = t.walk(["Object abundance, morphology, or intensity", "Size or shape",
                    "Yes: area, perimeter, circularity, aspect ratio"])
    assert set(morph.observables) == {"size", "shape"}


def test_hybrid_engine_drives_a_plan():
    reg = build_operation_registry()
    eng = HybridQuestionEngine(reg)
    intent = AnalysisIntent(target="condensate")
    ctx = _ts()
    script = {"Q001": "Change over time", "Q030": "Fusion or post-merger relaxation"}
    for _ in range(10):
        q = eng.next_question(intent, ctx)
        if q is None:
            break
        if q.kind == "scientific":
            eng.answer(q, script[q.id.split(".")[1]], intent, ctx)
        else:
            eng.answer(q, q.choices[0].value, intent, ctx)
    assert intent.observables == ["fusion"]
    plan = Planner(reg).compile(intent, ctx)
    assert "condensate_physics.fit_fusion_relaxation" in plan.ordered_modules


# --------------------------------------------------------------------------- #
# layer binding                                                               #
# --------------------------------------------------------------------------- #
def test_capability_to_query_translation():
    q = capability_to_query(cap(Representation.INSTANCE_LABELS, "target:condensate"))
    assert q["representation"] == "instance_labels"
    assert q["role"] == "labels"
    assert q["target"] == "condensate"


def test_existing_layer_is_reused_not_replanned():
    reg = build_operation_registry()
    resolver = InMemoryLayerResolver([
        SessionLayer("Refined Condensate Labels", "instance_labels",
                     target="condensate", state="refined", quality_status="pass")])
    ctx = _ts(); ctx.set("snr", 6.0, source=Source.MODULE)
    plan = Planner(reg).compile(
        AnalysisIntent(target="condensate", observables=["coarsening"]),
        ctx, layer_resolver=resolver)
    assert "subcellular_segment" not in plan.ordered_modules
    assert "Refined Condensate Labels" in plan.reused_layers
    # tracking still planned on top of the reused labels
    assert "dynamic_spatial.link_trajectories" in plan.ordered_modules


def test_failed_qc_layer_is_not_reused():
    reg = build_operation_registry()
    resolver = InMemoryLayerResolver([
        SessionLayer("Bad Labels", "instance_labels", target="condensate",
                     state="refined", quality_status="fail")])
    ctx = _ts(); ctx.set("snr", 6.0, source=Source.MODULE)
    plan = Planner(reg).compile(
        AnalysisIntent(target="condensate", observables=["size"]),
        ctx, layer_resolver=resolver)
    assert not plan.reused_layers
    assert "subcellular_segment" in plan.ordered_modules   # had to segment


# --------------------------------------------------------------------------- #
# probe insertion                                                             #
# --------------------------------------------------------------------------- #
def test_unknown_snr_inserts_qc_probe():
    reg = build_operation_registry()
    plan = Planner(reg).compile(
        AnalysisIntent(target="condensate", observables=["coarsening"]), _ts())
    assert "data_qc.assess" in [s.name for s in plan.probes]
    # probe is ordered before the segmenter it gates
    names = plan.ordered_modules
    assert names.index("data_qc.assess") < names.index("subcellular_segment")


def test_known_snr_inserts_no_probe():
    reg = build_operation_registry()
    ctx = _ts(); ctx.set("snr", 6.0, source=Source.MODULE)
    plan = Planner(reg).compile(
        AnalysisIntent(target="condensate", observables=["coarsening"]), ctx)
    assert plan.probes == []


# --------------------------------------------------------------------------- #
# 13-pipeline regression oracle                                               #
# --------------------------------------------------------------------------- #
def test_all_thirteen_cases_present():
    assert len(CANONICAL_CASES) == 13


@pytest.mark.parametrize("case", [c for c in CANONICAL_CASES if c.status == "reproducible"],
                         ids=lambda c: c.pipeline_id)
def test_reproducible_pipeline(case):
    reg = build_operation_registry()
    r = check_case(reg, case)
    assert r["ok"], f"{case.pipeline_id}: missing={r['missing']} forbidden={r['forbidden_hit']}"


def test_needs_codebase_cases_are_documented():
    """No silent caps: every not-yet-reproducible pipeline carries a reason."""
    for c in CANONICAL_CASES:
        if c.status == "needs_codebase":
            assert c.note, f"{c.pipeline_id} lacks a reason"
