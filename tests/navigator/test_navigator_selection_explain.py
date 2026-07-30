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
