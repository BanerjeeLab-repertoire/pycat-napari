"""**Live plan revision — swap a section's op and recompile (Method-Widget Spec 4, the editing surface).**

`revise_plan(session, current_op, new_op)` is the headless core of the pop-out's "use this instead": it pins the
chosen op at its role and recompiles, so the planner re-satisfies the new op's requirements and PRESERVES the
rest of the plan. This pins that swapping the segmenter changes only the segmenter, that the swap actually takes,
and that an undeterminable role recompiles unchanged rather than guessing.
"""
import pytest

from pycat.navigator.session import (
    NavigatorSession, revise_plan, provided_representation, alternatives_for_op, segmentation_scores,
)
from pycat.navigator.execution import execution_order
from pycat.navigator.contracts import AnalysisIntent

pytestmark = pytest.mark.base


def _cell_session():
    s = NavigatorSession()
    s.intent = AnalysisIntent(target="cell", observables=["morphology"])
    return s


def test_provided_representation_is_the_pin_key():
    s = _cell_session()
    # a segmenter's role is the instance-labels it produces — the key you pin to swap it
    assert provided_representation(s.registry, "cellpose") == "instance_labels"
    assert provided_representation(s.registry, "watershed") == "instance_labels"
    assert provided_representation(s.registry, "not_a_real_op") is None


def test_alternatives_are_peer_segmenters_not_label_transforms():
    s = _cell_session()
    alts = alternatives_for_op(s, "cellpose")
    # real from-scratch segmenters that consume an intensity field, like cellpose does
    assert "watershed" in alts and "stardist" in alts
    assert "cellpose" not in alts                       # never itself
    # label→label transforms provide instance_labels too, but consume labels — offering one would silently
    # change what the step eats, so the peer filter must exclude them
    for transform in ("relabel", "expand_labels", "label_mask", "merge_mean_color"):
        assert transform not in alts, f"{transform} is a label transform, not a peer segmenter"
    # every offered alternative actually swaps in cleanly (planner re-satisfies it)
    for a in alts[:4]:
        after = [x.name for x in execution_order(revise_plan(_cell_session(), "cellpose", a))]
        assert a in after and "cellpose" not in after

    assert alternatives_for_op(s, "not_a_real_op") == []


def test_revising_swaps_only_the_segmenter_and_preserves_the_rest():
    s = _cell_session()
    before = [x.name for x in execution_order(s.compile_plan())]
    assert "cellpose" in before and "feature_analysis.cell_analysis" in before

    amended = revise_plan(s, "cellpose", "watershed")
    after = [x.name for x in execution_order(amended)]
    assert "watershed" in after and "cellpose" not in after      # the segmenter swapped
    assert "feature_analysis.cell_analysis" in after             # ...and the analysis + QC are preserved
    assert [n for n in before if n != "cellpose"] == [n for n in after if n != "watershed"]


def test_revision_persists_and_can_be_revised_again():
    s = _cell_session()
    revise_plan(s, "cellpose", "watershed")
    # a fresh recompile still honours the pin (the panel would stay on watershed)
    assert "watershed" in [x.name for x in execution_order(s.compile_plan())]
    # revise again to a third segmenter
    after = [x.name for x in execution_order(revise_plan(s, "watershed", "subcellular_segment"))]
    assert "subcellular_segment" in after and "watershed" not in after


def test_segmentation_scores_justify_the_default_and_follow_the_pin():
    s = _cell_session()
    scores = segmentation_scores(s)
    # the default segmenter is marked chosen; with no context confirmed the context scores tie at 0, so the pick
    # is justified by PREFERENCE — cellpose is the preferred cell segmenter and outranks its peers there
    assert scores["cellpose"]["chosen"] is True
    assert scores["watershed"]["chosen"] is False
    assert scores["cellpose"]["preference"] == max(v["preference"] for v in scores.values())
    assert scores["cellpose"]["preference"] > scores["watershed"]["preference"]

    # after a revision pins watershed, the reasoning must follow the pin — the whole point of the kind_hint fix:
    # a Capability's pin key is its .kind, so explain_segmentation_choice now honors the segmenter pin
    revise_plan(s, "cellpose", "watershed")
    after = segmentation_scores(s)
    assert after["watershed"]["chosen"] is True
    assert after["cellpose"]["chosen"] is False


def test_segmentation_scores_empty_without_a_target():
    s = NavigatorSession()  # no intent target
    assert segmentation_scores(s) == {}


def test_an_undeterminable_role_recompiles_unchanged_never_guesses():
    s = _cell_session()
    before = [x.name for x in execution_order(s.compile_plan())]
    # neither id resolves to a registry role → no pin, plan is unchanged (not a silent wrong swap)
    after = [x.name for x in execution_order(revise_plan(s, "not_a_real_op", "also_not_real"))]
    assert after == before
