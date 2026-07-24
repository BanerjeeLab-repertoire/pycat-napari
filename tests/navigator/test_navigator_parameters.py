"""**The parameter review is honest: seeded by declared precedence, edits tracked, values drive the run.**

Phase 2 of the execution-adapter layer. Before a guided run, each adapter-covered step surfaces its *material*
parameters, seeded preset → session value → grounded default (never invented). The user may edit any; the edit
is recorded as provenance (the `PresetApplication.record()` shape). These tests pin the seeding precedence, the
provenance shape, and — the load-bearing one — that a **reviewed value actually drives the computation**: a
run with an edited rolling-ball radius equals the manual operation at *that* radius, not the default.
"""
import numpy as np
import pytest

from tests.fixtures_synthetic import synthetic_puncta_image

from pycat.navigator.parameters import (
    build_param_review, material_params, ReviewedStep, StepParam, ParamReview)
from pycat.navigator.executor import run_plan
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


def _raw():
    image, _labels = synthetic_puncta_image(shape=(128, 128), n_puncta=20, seed=1)
    return np.asarray(image).astype(np.float64)


def _step(name, reason=""):
    return PlanStep(module=ModuleContract(name=name, info_role=InformationRole.TRANSFORM),
                    produces=None, inputs=[], reason=reason)


def _plan(*steps):
    return Plan(intent=AnalysisIntent(target="t", observables=["x"]), steps=list(steps))


# ── the declared precedence: preset → session value → grounded default ─────────────────────────────────

def test_background_removal_material_param_is_ball_radius_default_50():
    params = material_params("background_removal")
    assert [p.name for p in params] == ["ball_radius"]
    review = build_param_review(_plan(_step("background_removal")))
    step = review.step("background_removal")
    assert step.values["ball_radius"] == 50          # grounded default, no session value, no preset
    assert not step.is_modified


def test_a_session_value_seeds_over_the_default():
    review = build_param_review(_plan(_step("background_removal")), ctx={"ball_radius": 30})
    assert review.step("background_removal").values["ball_radius"] == 30


def test_a_step_with_no_adapter_or_no_material_params_carries_no_review():
    # subcellular_segment has no adapter yet; feature_measure has an... nothing. Either way, empty review.
    assert build_param_review(_plan(_step("subcellular_segment"))).is_empty
    assert build_param_review(_plan(_step("feature_measure"))).is_empty


# ── the load-bearing one: the reviewed value actually drives the computation ────────────────────────────

def test_an_edited_radius_drives_the_run_and_matches_manual_at_that_radius():
    from pycat.toolbox.image_processing_tools import rb_gaussian_bg_removal_with_edge_enhancement
    raw = _raw()
    edited = 15                                       # NOT the default 50

    review = build_param_review(_plan(_step("background_removal")))
    review.step("background_removal").set("ball_radius", edited)
    assert review.step("background_removal").is_modified

    state = {"image": raw, "preprocessed": raw, "data_instance": _DataInstance({"ball_radius": 50})}
    report = run_plan(_plan(_step("background_removal")), state,
                      params_by_step=review.params_by_step(),
                      provenance_by_step=review.provenance_by_step())
    guided = np.asarray(state["preprocessed"]).astype(np.float32)

    manual = rb_gaussian_bg_removal_with_edge_enhancement(raw, edited).astype(np.float32)
    assert [s.outcome for s in report.steps] == ["ran"]
    np.testing.assert_array_equal(guided, manual)     # the EDITED radius drove the run, not 50
    # and it does NOT equal the default-radius manual result (proves the edit was not ignored)
    assert not np.array_equal(guided, rb_gaussian_bg_removal_with_edge_enhancement(raw, 50).astype(np.float32))


# ── provenance: the PresetApplication.record() shape, preset or not ─────────────────────────────────────

def test_provenance_shape_matches_preset_application_record():
    from pycat.utils.analysis_presets import PresetApplication, ANALYSIS_PRESETS
    preset = ANALYSIS_PRESETS["condensate_snr_gate_validated"]
    reference = PresetApplication(preset).record()

    params = (StepParam("local_snr_threshold", "SNR", "float", 1.0),)
    step = ReviewedStep("some_step", params, preset=preset)
    prov = step.provenance()
    assert set(prov) == set(reference)                       # same keys as the recorded shape
    assert prov["preset_key"] == "condensate_snr_gate_validated"
    assert prov["is_modified"] is False and prov["modified_parameters"] == []


def test_preset_seeds_values_and_an_edit_is_recorded_as_modified():
    from pycat.utils.analysis_presets import ANALYSIS_PRESETS
    preset = ANALYSIS_PRESETS["condensate_snr_gate_validated"]     # local_snr_threshold=1.0
    params = (StepParam("local_snr_threshold", "Local SNR", "float", 0.0),)   # a default the preset overrides
    step = ReviewedStep("some_step", params, preset=preset)
    assert step.values["local_snr_threshold"] == 1.0             # seeded from the preset, not the 0.0 default

    step.set("local_snr_threshold", 2.5)
    prov = step.provenance()
    assert prov["is_modified"] and prov["modified_parameters"] == ["local_snr_threshold"]
    assert prov["parameters"]["local_snr_threshold"] == 2.5
    # setting it back to the preset value clears the modification — a result matching the seed is not 'modified'
    step.set("local_snr_threshold", 1.0)
    assert not step.provenance()["is_modified"]


def test_provenance_flows_onto_the_run_report():
    raw = _raw()
    review = build_param_review(_plan(_step("background_removal")))
    review.step("background_removal").set("ball_radius", 20)
    state = {"image": raw, "preprocessed": raw, "data_instance": _DataInstance({})}
    report = run_plan(_plan(_step("background_removal")), state,
                      params_by_step=review.params_by_step(),
                      provenance_by_step=review.provenance_by_step())
    prov = report.steps[0].provenance
    assert prov["is_modified"] and prov["parameters"]["ball_radius"] == 20 and prov["preset_key"] is None


def test_a_bad_edit_falls_back_to_the_default_kind_never_raises():
    step = ReviewedStep("s", (StepParam("ball_radius", "R", "int", 50),))
    step.set("ball_radius", "not a number")
    assert step.values["ball_radius"] == 50           # coerced back to the default, no exception


def test_empty_review_helpers_are_safe():
    empty = ParamReview([])
    assert empty.is_empty and empty.params_by_step() == {} and empty.provenance_by_step() == {}
    assert empty.step("anything") is None
