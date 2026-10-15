"""**"Build method panel" builds** (Completion Spec C3).

The navigator dock stores the parameter review as a ``ParamReview``; the generated panel did ``dict(review)``
on it -> ``TypeError: 'ParamReview' object is not iterable``, swallowed by the build's broad handler, so the
button silently did nothing. The panel must construct from what the dock holds and seed the reviewed values.
"""
from types import SimpleNamespace

import pytest

from pycat.navigator.capabilities import InformationRole
from pycat.navigator.contracts import AnalysisIntent, ModuleContract
from pycat.navigator.parameters import build_param_review
from pycat.navigator.planner import Plan, PlanStep

pytestmark = pytest.mark.integration


class _DataInstance:
    def __init__(self):
        self.data_repository = {}

    def get_data(self, key, default=None):
        return self.data_repository.get(key, default)

    def set_data(self, key, value):
        self.data_repository[key] = value


def _plan():
    step = PlanStep(module=ModuleContract(name="image_processing_tools", info_role=InformationRole.TRANSFORM),
                    produces=None, inputs=[], reason="")
    return Plan(intent=AnalysisIntent(target="t", observables=["x"]), steps=[step])


def test_a_generated_panel_builds_from_the_ParamReview_the_dock_holds(qtbot):
    from pycat.ui.generated_method_ui import GeneratedMethodUI
    data = _DataInstance()
    plan = _plan()
    review = build_param_review(plan, ctx={"ball_radius": 30})
    ui = GeneratedMethodUI(None, SimpleNamespace(active_data_class=data, viewer=None), plan, review=review)
    ui._seed_reviewed_parameters()
    assert data.data_repository["ball_radius"] == 30

