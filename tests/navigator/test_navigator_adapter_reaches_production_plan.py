"""**Regression guard for the DORMANT execution-adapter finding.**

See `docs/audits/claude_code_spec_navigator_adapter_dormant_2026-07-27.md`.

A plan compiled the way the production dock compiles it — from a default `NavigatorSession()` (the op-catalog
registry) — must have at least ONE step that resolves to a real batch handler, or the "Run analysis" button
computes nothing. Today it resolves NONE: the adapters key on toolbox-module names (`segmentation_tools`,
`feature_analysis_tools`), but production plans carry op-ids (`subcellular_segment`,
`feature_analysis.cell_analysis`). So this is **xfail** — a live record of the bug at the test level.

**When the navigator-planning fix lands this flips to xpass** (pytest reports it) — remove the xfail marker
then. It is exactly the check every adapter test skipped: they build plans from hand-made module-name
`PlanStep`s / the workbook registry, never from a default session, which is why the dormancy went unseen.
"""
import pytest

pytestmark = pytest.mark.base


@pytest.mark.xfail(reason="execution-adapter layer is dormant in the default (op-catalog) session — see "
                          "docs/audits/claude_code_spec_navigator_adapter_dormant_2026-07-27.md",
                   strict=False)
def test_a_default_session_plan_has_at_least_one_runnable_adapter_step():
    from pycat.navigator.session import NavigatorSession
    from pycat.navigator.contracts import AnalysisIntent
    from pycat.navigator.execution import execution_order
    from pycat.navigator.executor import resolve_batch_step

    sess = NavigatorSession()          # exactly what the dock constructs (navigator_dock.py:427/463)
    sess.intent = AnalysisIntent(target="cell", observables=["count", "size"])
    plan = sess.planner.compile(sess.intent, sess.ctx, pins={})

    resolvable = [getattr(s, "name", "") for s in execution_order(plan)
                  if resolve_batch_step(getattr(s, "name", ""), sess.intent) is not None]
    assert resolvable, (
        "no step in a default-session plan resolves to a batch handler — the Run button computes nothing. "
        "Adapters key on module names but production plans carry op-ids.")
