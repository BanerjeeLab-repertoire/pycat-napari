"""**Phase 3: adapters fire on REAL compiled-plan steps, and the cell-segmentation adapter is route-proven.**

Phase 1 proved the executor *mechanism* against a synthetic step name (`background_removal`), but real navigator
plans emit toolbox-module step names (`segmentation_tools`, `image_processing_tools`, …) — so that adapter never
fired in production. Phase 3 keys the adapters on the real module names and adds the next proven workflow:
single-frame cell segmentation (`segmentation_tools` for a cell → the `cellpose_segmentation` batch step that
`test_route_equivalence` proves computes identically to the manual route).

Two guarantees:
- **Adapters key on real names.** A real cell plan's segmentation step resolves to a batch handler — the
  regression guard for the synthetic-name gap. Condensate segmentation stays reported (its batch route is an
  unproven documented gap), and time-series cell segmentation stays reported (keyframe propagation is not the
  single-frame step) — never guessed.
- **Guided == manual, at the reviewed diameter.** Driving `cellpose_segmentation` through the adapter with a
  reviewed `cell_diameter` equals a direct Cellpose call at that diameter, bit for bit — so the reviewed value
  provably reaches the computation (the batch route's manual-equivalence is proven separately by workflow 4).
"""
import numpy as np
import pytest

from pycat.navigator.executor import run_plan, resolve_batch_step, has_adapter
from pycat.navigator.planner import Plan, PlanStep
from pycat.navigator.contracts import ModuleContract, AnalysisIntent
from pycat.navigator.capabilities import InformationRole

pytestmark = pytest.mark.base


class _DataInstance:
    def __init__(self, repo=None):
        self.data_repository = dict(repo or {})

    def set_data(self, key, value):
        self.data_repository[key] = value

    def get_data(self, key, default=None):
        return self.data_repository.get(key, default)


def _step(name):
    return PlanStep(module=ModuleContract(name=name, info_role=InformationRole.CREATE),
                    produces=None, inputs=[], reason="")


def _plan(*steps, target="cell"):
    return Plan(intent=AnalysisIntent(target=target, observables=["count"]), steps=list(steps))


# ── adapters key on real plan step names (the fix for Phase 1's synthetic-name gap) ─────────────────────

def test_a_real_cell_plan_has_a_segmentation_step_that_now_resolves_to_a_batch_handler():
    from pycat.navigator.planner import Planner
    from pycat.navigator.loader import build_registry_from_workbook
    from pycat.navigator.context import AnalysisContext
    from pycat.navigator.execution import execution_order

    planner = Planner(build_registry_from_workbook())
    plan = planner.compile(AnalysisIntent(target="cell", observables=["morphology"]), AnalysisContext())
    names = [es.name for es in execution_order(plan)]
    resolved = {n: resolve_batch_step(n, plan.intent) for n in names}
    # the real cell chain now fires end to end: segment the cells, then measure them
    assert "cellpose_segmentation" in resolved.values(), f"no cellpose step in a real cell plan — {names}"
    assert "cell_analysis" in resolved.values(), f"no cell-analysis step in a real cell plan — {names}"


def test_condensate_and_timeseries_segmentation_are_reported_not_guessed():
    # segmentation_tools HAS an adapter, but only the cell variant is a proven batch route
    assert resolve_batch_step("segmentation_tools",
                              AnalysisIntent(target="cell", observables=["x"])) == "cellpose_segmentation"
    assert resolve_batch_step("segmentation_tools",
                              AnalysisIntent(target="condensate", observables=["x"])) is None
    # time-series cell segmentation is keyframe propagation, not the single-frame step — no adapter at all
    assert not has_adapter("ts_cellpose_tools")


def test_a_condensate_segmentation_step_runs_nothing_and_is_reported():
    state = {"image": np.zeros((8, 8), np.float32), "data_instance": _DataInstance()}
    report = run_plan(_plan(_step("segmentation_tools"), target="condensate"), state)
    assert [s.outcome for s in report.steps] == ["needs_panel"]
    assert "variant" in report.steps[0].detail and "cellpose_mask" not in state   # nothing invoked


def test_every_adapter_targets_a_real_registered_batch_step():
    # the guard the spec asks for: a typo'd batch_step key would silently become a runtime 'error', so pin
    # that every batch step an adapter can resolve to is a real handler in the production registry.
    from pycat.navigator.executor import _build_step_registry, _ADAPTERS
    registry = _build_step_registry()
    targets = set()
    for name in _ADAPTERS:
        for target in ("cell", "condensate"):
            bs = resolve_batch_step(name, AnalysisIntent(target=target, observables=["x"]))
            if bs is not None:
                targets.add(bs)
    assert targets == {"background_removal", "cellpose_segmentation", "cell_analysis", "condensate_analysis"}
    missing = [bs for bs in targets if bs not in registry]
    assert not missing, f"adapters target batch steps that are not registered: {missing}"


# ── the acceptance gate: guided == manual, at the reviewed diameter ─────────────────────────────────────

def test_guided_cellpose_equals_manual_at_the_reviewed_diameter():
    pytest.importorskip("cellpose", reason="cellpose/torch not installed in this environment")
    from pycat.toolbox.segmentation_tools import cellpose_segmentation
    from pycat.batch.steps._common import _normalize_to_float
    from tests.fixtures_synthetic import synthetic_puncta_image

    image, _labels = synthetic_puncta_image(shape=(128, 128), n_puncta=20, seed=5)
    raw = np.asarray(image).astype(np.float64)
    diameter = 14                                     # NOT the grounded default (100)

    # manual: the direct Cellpose call on the shared production normalisation (workflow 4's reference)
    manual = cellpose_segmentation(_normalize_to_float(raw), diameter, postprocess=False)
    manual = np.asarray(manual[0] if isinstance(manual, (tuple, list)) else manual).astype(np.float32)

    # guided: run_plan drives cellpose_segmentation via the segmentation_tools adapter; the reviewed diameter
    # is applied to the data_instance (where the handler reads it), NOT the params dict.
    state = {"image": raw, "data_instance": _DataInstance()}
    report = run_plan(_plan(_step("segmentation_tools"), target="cell"), state,
                      params_by_step={"segmentation_tools": {"cell_diameter": diameter}})
    guided = np.asarray(state["cellpose_mask"]).astype(np.float32)

    assert [s.outcome for s in report.steps] == ["ran"]
    np.testing.assert_array_equal(guided, manual)     # the reviewed diameter drove the run, bit for bit
    assert state["data_instance"].get_data("cell_diameter") == diameter   # applied where the handler reads it
