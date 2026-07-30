"""**The planner explains its terminal choice — 'why this op, not that' (Method-Widget Spec 5 core).**

`Planner.explain_terminal_choice` surfaces, per requested observable, the terminal ops the planner CONSIDERED
with their selection scores (in_vitro context bonus, target specificity, preference) and which it chose. It is
the planner's OWN reasoning made legible — the anti-black-box payoff. Two guarantees pinned here:

- **It cannot drift from the actual pick.** The winner comes from `_pick_terminal` (the same call `compile` uses)
  and the per-candidate scores from the same shared helpers, so the explanation's `chosen` is exactly the op the
  compiled plan runs.
- **It exposes the real reason.** For a bead/viscosity intent, `vpt.microrheology` is chosen over the generic
  `condensate_physics.fit_anomalous_diffusion` even though the generic has HIGHER preference — because it is
  target-specific. The scores make that visible (specificity 1 vs 0), which is the whole point.
"""
import pytest

from pycat.navigator.planner import Planner
from pycat.navigator.op_catalog import build_operation_registry
from pycat.navigator.context import AnalysisContext
from pycat.navigator.contracts import AnalysisIntent
from pycat.navigator.execution import execution_order

pytestmark = pytest.mark.base


def _planner():
    return Planner(build_operation_registry())


def test_explanation_names_the_considered_terminals_and_the_winner():
    pl = _planner()
    intent = AnalysisIntent(target="bead", observables=["viscosity"])
    report = pl.explain_terminal_choice(intent, AnalysisContext())

    assert "viscosity" in report
    entry = report["viscosity"]
    names = [c["name"] for c in entry["candidates"]]
    # the planner really did weigh both the bead-specific terminal and the generic biophysics fit
    assert entry["chosen"] == "vpt.microrheology"
    assert "condensate_physics.fit_anomalous_diffusion" in names
    # winner-first ordering
    assert entry["candidates"][0]["name"] == entry["chosen"] and entry["candidates"][0]["chosen"] is True


def test_the_explanation_exposes_WHY_the_winner_won_target_specificity_over_preference():
    """The anti-black-box insight: vpt.microrheology wins viscosity even though the generic biophysics fit has a
    HIGHER preference — because it is specialised to the bead target. The surfaced scores show exactly that."""
    pl = _planner()
    intent = AnalysisIntent(target="bead", observables=["viscosity"])
    cands = {c["name"]: c for c in pl.explain_terminal_choice(intent, AnalysisContext())["viscosity"]["candidates"]}
    winner = cands["vpt.microrheology"]
    generic = cands["condensate_physics.fit_anomalous_diffusion"]
    assert winner["target_specificity"] == 1 and generic["target_specificity"] == 0
    assert generic["preference"] > winner["preference"]        # preference alone would have picked the generic
    # so specificity is the deciding factor, and the explanation makes it legible


def test_the_explained_choice_matches_the_compiled_plan_no_drift():
    """Drift guard: the explanation's `chosen` terminal is exactly the op the compiled plan runs — the
    explanation reuses `_pick_terminal`, so it can never disagree with the planner."""
    pl = _planner()
    for target, obs in [("bead", "viscosity"), ("condensate", "size")]:
        ctx = AnalysisContext()
        intent = AnalysisIntent(target=target, observables=[obs])
        chosen = pl.explain_terminal_choice(intent, ctx)[obs]["chosen"]
        plan_steps = [s.name for s in execution_order(pl.compile(intent, ctx))]
        assert chosen in plan_steps, f"explained terminal {chosen!r} is not in the compiled plan {plan_steps}"


def test_an_observable_with_no_terminal_is_simply_absent_never_guessed():
    pl = _planner()
    report = pl.explain_terminal_choice(AnalysisIntent(target="cell", observables=["not_a_real_observable"]),
                                        AnalysisContext())
    assert "not_a_real_observable" not in report          # no candidates -> no fabricated entry


# ── the dependency layer: why THIS segmenter, not the others ─────────────────────────────────────────────

def _fluor_ctx():
    from pycat.navigator.context import Source
    c = AnalysisContext()
    c.set("modality", "fluorescence", source=Source.USER)
    return c


def _brightfield_ctx():
    from pycat.navigator.context import Source
    c = AnalysisContext()
    c.set("modality", "brightfield", source=Source.USER)
    return c


def test_segmentation_explanation_picks_by_preference_when_context_is_neutral():
    pl = _planner()
    report = pl.explain_segmentation_choice(AnalysisIntent(target="cell", observables=["count"]), _fluor_ctx())
    assert report["chosen"] == "cellpose"
    assert report["candidates"][0]["name"] == "cellpose" and report["candidates"][0]["chosen"] is True
    # a real field of alternatives was weighed
    names = {c["name"] for c in report["candidates"]}
    assert {"subcellular_segment", "watershed", "stardist"} <= names


def test_segmentation_explanation_shows_a_context_SPECIALIST_beating_a_higher_preference_generic():
    """The anti-black-box insight at the dependency layer: on brightfield, bf_segment (a context-matched
    specialist, score +1) wins over the generic subcellular_segment even though the generic has HIGHER
    preference. The surfaced context_score shows exactly why."""
    pl = _planner()
    report = pl.explain_segmentation_choice(AnalysisIntent(target="condensate", observables=["count"]),
                                            _brightfield_ctx())
    cands = {c["name"]: c for c in report["candidates"]}
    assert report["chosen"] == "bf_segment"
    assert cands["bf_segment"]["context_score"] == 1                     # a context-matched specialist
    assert cands["subcellular_segment"]["context_score"] == 0
    assert cands["subcellular_segment"]["preference"] > cands["bf_segment"]["preference"]  # generic is preferred
    # ...yet the specialist won — so the context score is the deciding factor, made legible


def test_segmentation_explanation_matches_the_compiled_plan_no_drift():
    pl = _planner()
    ctx = _brightfield_ctx()
    intent = AnalysisIntent(target="condensate", observables=["count"])
    chosen = pl.explain_segmentation_choice(intent, ctx)["chosen"]
    plan_steps = [s.name for s in execution_order(pl.compile(intent, ctx))]
    assert chosen in plan_steps, f"explained segmenter {chosen!r} not in the compiled plan {plan_steps}"


def test_segmentation_choice_is_none_without_a_target():
    # no target → no segmentation goal to explain; None, never a fabricated one
    pl = _planner()
    assert pl.explain_segmentation_choice(AnalysisIntent(target=None, observables=["count"]),
                                          AnalysisContext()) is None
