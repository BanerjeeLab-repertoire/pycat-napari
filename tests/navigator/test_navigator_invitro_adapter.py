"""**Phase 3, next workflow: the IN-VITRO fluorescence droplet chain — a CONTEXT branch, not a new target.**

Droplets ≡ condensates (the same target). What distinguishes the in-vitro workflow from the in-cell one is the
**absence of cells**: in-vitro is a whole-field threshold segmentation (otsu/multiotsu/…) of droplets, where
in-cell is the per-cell puncta pipeline. So an ``in_vitro`` CONTEXT flag (mirroring how brightfield uses the
modality gate) selects ``ivf_droplet_segment`` — the extracted ``@tags_layer`` producer ``segment_ivf_droplets``,
the same function the panel uses — over ``subcellular_segment``; the target stays ``condensate`` throughout.

What this pins:
- **Context selects the workflow.** ``condensate`` + ``in_vitro`` + fluorescence → ``ivf_droplet_segment``; the
  in-cell fluorescence plan still gets ``subcellular_segment``, and brightfield still gets ``bf_segment`` (the
  ``fluorescence`` gate keeps the in-vitro fluorescence segmenter off brightfield).
- **Guided == manual, bit for bit.** The adapter drives ``segment_ivf_droplets`` (the panel's producer), so a
  guided run equals the manual call.
- **The reviewed knob drives the run.** An edited ``min_area`` makes the guided mask equal the manual at that
  value, not the default.
- **The measurement is staged.** The field-summary / size-distribution measurement is the next increment; until
  then the analysis step honestly reports 'run from its panel' (dispatched on the produced ``ivf_droplet_mask``).
"""
import numpy as np
import pytest

from pycat.navigator.executor import run_plan, resolve_batch_step, has_adapter
from pycat.navigator.parameters import build_param_review
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


def _scene():
    """A fluorescence field: BRIGHT droplets of varied size on a dark background."""
    img = np.full((80, 80), 0.05, np.float32)
    yy, xx = np.ogrid[:80, :80]
    for (cy, cx), r in [((25, 25), 7), ((25, 55), 5), ((55, 40), 3)]:
        img[(yy - cy) ** 2 + (xx - cx) ** 2 <= r ** 2] = 0.9
    img += np.random.default_rng(0).normal(0, 0.01, img.shape).astype(np.float32)
    return np.clip(img, 0, None).astype(np.float32)


def _manual_mask(img, *, method="otsu", min_area=6, reject_nonround=False):
    from pycat.toolbox.invitro.segmentation import segment_ivf_droplets
    from pycat.batch.steps._common import _normalize_to_float
    labeled, _u = segment_ivf_droplets(img, _normalize_to_float(img), method=method,
                                       min_area=min_area, reject_nonround=reject_nonround)
    return np.asarray(labeled).astype(np.int32)


def _step(name, role=InformationRole.CREATE):
    return PlanStep(module=ModuleContract(name=name, info_role=role), produces=None, inputs=[], reason="")


def _plan(*steps):
    return Plan(intent=AnalysisIntent(target="condensate", observables=["count", "size"]), steps=list(steps))


def _state(img):
    return {"image": img.copy(), "preprocessed": img.copy(),
            "data_instance": _DataInstance({"ball_radius": 15})}


# ── the in_vitro context selects the whole-field droplet segmenter, target stays condensate ─────────────

def test_ivf_droplet_segment_has_an_adapter_and_batch_step():
    assert has_adapter("ivf_droplet_segment")
    assert resolve_batch_step("ivf_droplet_segment") == "ivf_droplet_segment"


def test_in_vitro_context_selects_the_droplet_segmenter_target_stays_condensate():
    from pycat.navigator.session import NavigatorSession
    from pycat.navigator.execution import execution_order

    def _segmenter(modality=None, in_vitro=None):
        s = NavigatorSession()
        if modality:
            s.ctx.set("modality", modality)
        if in_vitro is not None:
            s.ctx.set("in_vitro", in_vitro)
        s.ctx.set("axes", ["y", "x"])
        s.intent = AnalysisIntent(target="condensate", observables=["count", "size"])
        names = [x.name for x in execution_order(s.planner.compile(s.intent, s.ctx, pins={}))]
        return [n for n in names if n in ("subcellular_segment", "ivf_droplet_segment", "bf_segment")]

    assert _segmenter("fluorescence", True) == ["ivf_droplet_segment"]     # in-vitro fluorescence → droplets
    assert _segmenter("fluorescence", None) == ["subcellular_segment"]     # in-cell fluorescence → puncta
    assert _segmenter("brightfield", True) == ["bf_segment"]               # in-vitro brightfield stays bf_segment


# ── guided == manual, bit for bit ──────────────────────────────────────────────────────────────────────

def test_guided_ivf_segmentation_equals_manual_bit_for_bit():
    img = _scene()
    state = _state(img)
    report = run_plan(_plan(_step("ivf_droplet_segment")), state)
    assert [s.outcome for s in report.steps] == ["ran"]
    guided = state.get("ivf_droplet_mask")
    assert guided is not None and int(guided.max()) > 0
    np.testing.assert_array_equal(guided, _manual_mask(img))               # the extracted producer, bit for bit


def test_an_edited_min_area_drives_the_run():
    img = _scene()
    edited = 50                                                            # NOT the default 6 → drops the smallest
    review = build_param_review(_plan(_step("ivf_droplet_segment")))
    assert "min_area" in [p.name for p in review.step("ivf_droplet_segment").params]
    review.step("ivf_droplet_segment").set("min_area", edited)
    assert review.step("ivf_droplet_segment").is_modified

    state = _state(img)
    run_plan(_plan(_step("ivf_droplet_segment")), state,
             params_by_step=review.params_by_step(), provenance_by_step=review.provenance_by_step())
    guided = state["ivf_droplet_mask"]
    np.testing.assert_array_equal(guided, _manual_mask(img, min_area=edited))   # the edited value drove the run
    assert not np.array_equal(guided, _manual_mask(img))                        # ≠ the default result


# ── the measurement is staged (dispatch on the produced mask) ──────────────────────────────────────────

def test_ivf_measurement_reports_needs_panel_for_now():
    img = _scene()
    state = _state(img)
    report = run_plan(_plan(_step("ivf_droplet_segment"),
                            _step("feature_analysis.cell_analysis", InformationRole.MEASURE)), state)
    outcomes = {s.name: s.outcome for s in report.steps}
    assert outcomes["ivf_droplet_segment"] == "ran"
    assert outcomes["feature_analysis.cell_analysis"] == "needs_panel"     # staged: field-summary/size next
    intent = _plan().intent
    assert resolve_batch_step("feature_analysis.cell_analysis", intent,
                              {"ivf_droplet_mask": np.ones((4, 4), int)}) is None
    assert resolve_batch_step("feature_analysis.cell_analysis", intent,
                              {"bf_condensate_mask": np.ones((4, 4), int)}) == "bf_condensate_analysis"
    assert resolve_batch_step("feature_analysis.cell_analysis", intent,
                              {"puncta_mask": np.zeros((4, 4))}) == "condensate_analysis"
