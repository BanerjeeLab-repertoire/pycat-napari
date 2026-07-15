"""
Test suite for pycat_navigator.  Run:  pytest -q
"""
import pytest

from pycat.navigator import (AnalysisContext, AnalysisIntent, GateStatus,
                             Planner, QuestionEngine, Representation, Source,
                             TaggedLayerFactory, Resolver, build_registry, cap,
                             stage_gates, VALID_SOURCES)
from pycat.navigator.capabilities import representation_satisfies


# --------------------------------------------------------------------------- #
# capability lattice + wildcard matching                                      #
# --------------------------------------------------------------------------- #
def test_representation_lattice():
    # instance labels can serve where labels or a mask are required
    assert representation_satisfies("instance_labels", "labels")
    assert representation_satisfies("instance_labels", "mask")
    # but a binary mask is NOT instance labels
    assert not representation_satisfies("binary_mask", "instance_labels")
    # trajectories are coordinates-over-time
    assert representation_satisfies("trajectories", "coordinates")


def test_wildcard_target_match():
    req = cap(Representation.INTENSITY_FIELD, "target:condensate")
    provider = cap(Representation.INTENSITY_FIELD, "target:*")
    assert req.satisfied_by(provider)
    # a concrete non-matching target fails
    other = cap(Representation.INTENSITY_FIELD, "target:nucleus")
    assert not req.satisfied_by(other)


# --------------------------------------------------------------------------- #
# registry queries                                                            #
# --------------------------------------------------------------------------- #
def test_registry_query_by_observable():
    reg = build_registry()
    fusion = {m.name for m in reg.measuring("fusion")}
    assert "condensate_physics_tools" in fusion
    size = {m.name for m in reg.measuring("size")}
    assert "morphology_tools" in size


def test_new_module_appears_without_gui_edit():
    """PDF3: registering a module makes it available immediately."""
    reg = build_registry()
    before = len(reg.measuring("topology"))
    from pycat.navigator import ModuleContract, InformationRole
    reg.register(ModuleContract(
        name="persistent_homology_tools", info_role=InformationRole.MEASURE,
        requires_inputs=[cap(Representation.INSTANCE_LABELS, "target:*")],
        provides=[cap(Representation.MEASUREMENT_TABLE, "observable:*")],
        observables=["topology", "connectivity"]))
    assert len(reg.measuring("topology")) == before + 1


# --------------------------------------------------------------------------- #
# planner: the canonical condensate-fusion chain                              #
# --------------------------------------------------------------------------- #
def _ts_context():
    ctx = AnalysisContext()
    ctx.set("modality", "fluorescence", source=Source.METADATA)
    ctx.set("axes", ["time"], source=Source.METADATA)
    ctx.set("time_points", 120, source=Source.METADATA)
    ctx.set("channels", 2, source=Source.METADATA)
    return ctx


def test_fusion_workflow_is_generated():
    reg, ctx = build_registry(), _ts_context()
    intent = AnalysisIntent(question="fusion?", target="condensate",
                            observables=["fusion"])
    plan = Planner(reg).compile(intent, ctx)
    names = plan.ordered_modules
    # the whole chain must be present, in dependency order
    for m in ["acquisition", "segmentation_tools", "tracking_tools",
              "condensate_physics_tools"]:
        assert m in names, f"{m} missing from {names}"
    assert names.index("acquisition") < names.index("segmentation_tools")
    assert names.index("segmentation_tools") < names.index("tracking_tools")
    assert names.index("tracking_tools") < names.index("condensate_physics_tools")
    assert plan.is_executable


def test_canonical_workflow_regression():
    """Stress-test idea #12: the generator must reproduce a known-good,
    hand-authored workflow. If it can't, either a contract or the canonical
    workflow is wrong. This is the single most valuable consistency check."""
    reg, ctx = build_registry(), _ts_context()
    intent = AnalysisIntent(target="condensate", observables=["fusion", "coarsening"])
    plan = Planner(reg).compile(intent, ctx)
    got = set(plan.ordered_modules)
    canonical = {"acquisition", "segmentation_tools", "tracking_tools",
                 "condensate_physics_tools"}
    assert canonical.issubset(got), f"generated {got} lacks {canonical - got}"


def test_morphology_does_not_pull_tracking():
    """A pure size question must NOT drag in tracking (no over-planning)."""
    reg, ctx = build_registry(), _ts_context()
    intent = AnalysisIntent(target="condensate", observables=["size"])
    plan = Planner(reg).compile(intent, ctx)
    names = plan.ordered_modules
    assert "morphology_tools" in names
    assert "tracking_tools" not in names
    assert "condensate_physics_tools" not in names


def test_preprocessing_not_auto_inserted():
    """PDF4: preprocessing must not appear near the top of the tree by default."""
    reg, ctx = build_registry(), _ts_context()
    intent = AnalysisIntent(target="condensate", observables=["size"])
    plan = Planner(reg).compile(intent, ctx)
    assert "background_subtraction" not in plan.ordered_modules
    assert "clahe" not in plan.ordered_modules


def test_context_gap_becomes_a_question():
    """Without knowing it's a time series, a fusion request should surface an
    open question rather than silently planning tracking on a single frame."""
    reg = build_registry()
    ctx = AnalysisContext()   # nothing known about time
    intent = AnalysisIntent(target="condensate", observables=["fusion"])
    plan = Planner(reg).compile(intent, ctx)
    open_keys = {g.key for g in plan.open_questions()}
    assert "time_series" in open_keys


def test_violated_context_blocks_plan():
    reg = build_registry()
    ctx = AnalysisContext()
    ctx.set("axes", [], source=Source.USER)         # explicitly NOT a time series
    ctx.set("time_points", 1, source=Source.USER)
    intent = AnalysisIntent(target="condensate", observables=["fusion"])
    plan = Planner(reg).compile(intent, ctx)
    assert not plan.is_executable
    assert any(g.key == "time_series" and g.status is GateStatus.VIOLATED
               for g in plan.gaps)


# --------------------------------------------------------------------------- #
# staged validity gates                                                       #
# --------------------------------------------------------------------------- #
def test_probe_gate_staging():
    reg, ctx = build_registry(), _ts_context()
    intent = AnalysisIntent(target="condensate", observables=["fusion"])
    plan = Planner(reg).compile(intent, ctx)
    assumptions = [a for s in plan.steps for a in s.module.assumptions]
    staged = stage_gates(assumptions, ctx)
    # SNR gate is unknown until we measure SNR -> it's a probe gate
    assert any(a.id == "seg.snr" for a in staged.need_probe)
    # now measure it
    ctx.set("snr", 6.0, source=Source.MODULE)
    staged2 = stage_gates(assumptions, ctx)
    assert any(a.id == "seg.snr" for a in staged2.satisfied)
    assert staged2.confidence() >= staged.confidence()


# --------------------------------------------------------------------------- #
# question engine                                                             #
# --------------------------------------------------------------------------- #
def test_goal_question_comes_first():
    reg = build_registry()
    ctx = AnalysisContext()
    intent = AnalysisIntent()          # nothing chosen yet
    qs = QuestionEngine(reg).next_questions(intent, ctx)
    assert qs[0].id == "q.goal"


def test_questions_are_registry_derived():
    """Colocalization intent should surface the two-channels question because a
    relevant module needs it — not because it was hand-listed."""
    reg = build_registry()
    ctx = AnalysisContext()
    intent = AnalysisIntent(observables=["colocalization"])
    qs = QuestionEngine(reg).next_questions(intent, ctx)
    assert any(q.writes_key == "channels" for q in qs)


def test_low_confidence_field_is_offered_for_confirmation():
    reg = build_registry()
    ctx = AnalysisContext()
    ctx.set("channel_labels", ["DAPI", "GFP"], source=Source.INFERRED, confidence=0.5)
    intent = AnalysisIntent(observables=["size"])
    qs = QuestionEngine(reg).next_questions(intent, ctx)
    assert any(q.kind == "confirm" for q in qs)


# --------------------------------------------------------------------------- #
# tag / lineage resolver                                                      #
# --------------------------------------------------------------------------- #
def test_pipeline_source_is_valid_now():
    """PDF8 finding #6: 'pipeline' must be a valid, high-confidence source."""
    assert "pipeline" in VALID_SOURCES
    f = TaggedLayerFactory()
    layer = f.create("<x>", source="pipeline", role="labels",
                     representation="instance_labels", target="condensate")
    assert layer.tags.source == "pipeline"        # not silently downgraded
    assert layer.tags.confidence >= 0.85


def test_invalid_source_still_downgrades_but_visibly():
    f = TaggedLayerFactory()
    layer = f.create("<x>", source="not_a_real_source", role="labels")
    assert layer.tags.source == "inferred"


def test_resolver_prefers_refined_labels():
    f = TaggedLayerFactory()
    r = Resolver(f)
    raw_img = f.create("<img>", source="metadata", channel_label="GFP",
                       target="condensate", role="image",
                       representation="intensity_field", state="raw")
    seg = f.tag_from_operation("<seg>", raw_img, op="segment", state="segmented",
                               representation="instance_labels", role="labels",
                               purpose="measurement_input")
    refined = f.tag_from_operation("<ref>", seg, op="hand_painted", state="refined",
                                   representation="instance_labels", role="labels",
                                   purpose="measurement_input", supersedes=True)
    r.annotate_qc(seg, "pass")
    r.annotate_qc(refined, "pass")
    chosen = r.labels_for_measurement("condensate")
    assert chosen.id == refined.id            # refined beats segmented
    assert chosen.tags.state == "refined"


def test_resolver_excludes_failed_qc():
    f = TaggedLayerFactory()
    r = Resolver(f)
    img = f.create("<img>", target="condensate", role="image",
                   representation="intensity_field", state="raw")
    bad = f.tag_from_operation("<seg>", img, op="segment", state="refined",
                               representation="instance_labels", role="labels",
                               purpose="measurement_input")
    r.annotate_qc(bad, "fail")
    assert r.labels_for_measurement("condensate") is None


def test_display_name_generated_from_tags():
    f = TaggedLayerFactory()
    layer = f.create("<x>", channel_label="GFP", target="condensate",
                     representation="instance_labels", state="refined")
    assert layer.display_name == "GFP · condensate · Labels · Refined"
