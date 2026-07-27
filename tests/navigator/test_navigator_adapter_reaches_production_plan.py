"""**Regression guard: the execution adapters reach a PRODUCTION plan.**

See `docs/audits/claude_code_spec_navigator_adapter_dormant_2026-07-27.md`.

A plan compiled the way the production dock compiles it — from a default `NavigatorSession()` (the op-catalog
registry, op-id step names like `cellpose` / `subcellular_segment`) — must have its segmentation and analysis
steps resolve to real batch handlers, or the "Run analysis" button computes nothing. This was DORMANT: the
adapters keyed on toolbox-module names while production plans carry op-ids, and the planner mis-selected the
segmenter (a `target:cell` plan chose the puncta segmenter). Fixed by target/modality-aware op selection plus an
op-id→module translation in the executor. This guard is the check every earlier adapter test skipped — they
built plans from hand-made module-name `PlanStep`s, never from a default session.
"""
import pytest

from pycat.navigator.session import NavigatorSession
from pycat.navigator.contracts import AnalysisIntent
from pycat.navigator.execution import execution_order
from pycat.navigator.executor import resolve_batch_step

pytestmark = pytest.mark.base


def _resolved(target):
    sess = NavigatorSession()                 # exactly what the dock constructs (navigator_dock.py:427/463)
    sess.intent = AnalysisIntent(target=target, observables=["count", "size"])
    plan = sess.planner.compile(sess.intent, sess.ctx, pins={})
    return {s.name: resolve_batch_step(s.name, sess.intent) for s in execution_order(plan)}


def test_a_default_session_cell_plan_runs_segmentation_and_analysis_via_adapters():
    res = _resolved("cell")
    runnable = {k: v for k, v in res.items() if v is not None}
    assert runnable, f"no cell-plan step resolves to a batch handler — Run computes nothing: {res}"
    # the planner picks the CELL segmenter (cellpose), not the puncta segmenter, and it maps to its batch step
    assert res.get("cellpose") == "cellpose_segmentation"
    assert res.get("feature_analysis.cell_analysis") == "cell_analysis"


def test_a_default_session_condensate_plan_runs_segmentation_and_analysis_via_adapters():
    res = _resolved("condensate")
    runnable = {k: v for k, v in res.items() if v is not None}
    assert runnable, f"no condensate-plan step resolves to a batch handler: {res}"
    assert res.get("subcellular_segment") == "condensate_segmentation"
    assert res.get("feature_analysis.cell_analysis") == "condensate_analysis"
