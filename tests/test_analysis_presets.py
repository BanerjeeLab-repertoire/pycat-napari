"""**Analysis presets stay honest — mandatory provenance, no orphan keys, no decorative 'validated'.**

A preset must never smuggle an unaudited parameter set past a user. These tests pin the invariants that
prevent that: every preset declares non-empty provenance; a preset's parameter keys are all REAL parameters
of the workflow it claims (the drift guard); `validated=True` requires a linked validation record; applying
a preset populates but never locks (deviation is tracked and recorded); and an unmet requirement greys a
preset out with a stated reason (reusing the one requirements gate).
"""
import pytest

from pycat.utils.analysis_presets import (
    ANALYSIS_PRESETS, AnalysisPreset, PresetApplication, orphan_parameter_keys,
    preset_availability, presets_for)

pytestmark = pytest.mark.core


def test_presets_are_uniquely_keyed_and_every_one_declares_provenance():
    keys = [p.key for p in ANALYSIS_PRESETS.values()]
    assert len(keys) == len(set(keys)) == len(ANALYSIS_PRESETS)
    for p in ANALYSIS_PRESETS.values():
        assert (p.provenance or '').strip(), f"{p.key} has empty provenance — a preset must say where its numbers came from"
        assert len(p.provenance) > 30


def test_no_preset_sets_a_parameter_the_workflow_does_not_have():
    """The drift guard: a preset key that is not a real parameter of its workflow means the preset has
    rotted out of sync with the function it configures."""
    for p in ANALYSIS_PRESETS.values():
        orphans = orphan_parameter_keys(p)
        assert not orphans, (
            f"preset {p.key!r} sets parameters {orphans} that are not real parameters of workflow "
            f"{p.applies_to!r} — the preset has drifted from the function it configures")


def test_validated_true_requires_a_linked_validation_record():
    """'validated' must mean the set passed the sensitivity harness, not that it looked reasonable — so it
    cannot be a decorative flag."""
    for p in ANALYSIS_PRESETS.values():
        if p.validated:
            assert p.validation_ref, f"{p.key} is validated=True but links no validation record"
    # And a validated preset's refs point at real VALIDATED_CASES ids.
    from tests.filter_sensitivity import VALIDATED_CASES
    case_ids = {c['id'] for c in VALIDATED_CASES}
    for p in ANALYSIS_PRESETS.values():
        for ref in p.validation_ref:
            assert ref in case_ids, f"{p.key} references validation record {ref!r} that does not exist"


def test_constructing_a_validated_preset_without_a_ref_is_refused_at_import():
    """The registry validator rejects a validated-but-unlinked preset — proven by triggering it directly."""
    from pycat.utils import analysis_presets as ap
    bad = AnalysisPreset(key='x', display_name='x', applies_to='vpt_msd', description='x',
                         parameters={}, provenance='p', validated=True, validation_ref=())
    saved = dict(ap.ANALYSIS_PRESETS)
    ap.ANALYSIS_PRESETS['x'] = bad
    try:
        with pytest.raises(ValueError, match='validation_ref'):
            ap._validate_registry()
    finally:
        ap.ANALYSIS_PRESETS.clear(); ap.ANALYSIS_PRESETS.update(saved)


# ── Populate, never lock: deviation is tracked and recorded ─────────────────────────────────────
def test_applying_a_preset_then_editing_marks_it_modified():
    preset = ANALYSIS_PRESETS['condensate_snr_gate_validated']
    app = PresetApplication(preset)
    assert not app.is_modified and app.state_label() == preset.key

    app.set('local_snr_threshold', 2.0)                  # the user edits a value
    assert app.is_modified and app.state_label() == f"modified from {preset.key}"
    assert 'local_snr_threshold' in app.record()['modified_parameters']

    # Setting it back to the preset value clears the modification — the result IS the preset's again.
    app.set('local_snr_threshold', preset.parameters['local_snr_threshold'])
    assert not app.is_modified


def test_the_applied_preset_and_modification_state_are_recorded():
    app = PresetApplication(ANALYSIS_PRESETS['vpt_bead_tracking'])
    app.set('min_track_length', 100)
    rec = app.record()
    assert rec['preset_key'] == 'vpt_bead_tracking'
    assert rec['is_modified'] is True and rec['modified_parameters'] == ['min_track_length']
    assert rec['parameters']['min_track_length'] == 100    # the actual values used, for the record


# ── Requirements gating reuses the one runnability() gate ────────────────────────────────────────
def test_an_unmet_requirement_greys_the_preset_out_with_a_reason():
    vpt = ANALYSIS_PRESETS['vpt_bead_tracking']          # requires a time axis
    can_run, reason = preset_availability(vpt, available=set())
    assert not can_run and 'time' in reason.lower()

    can_run2, reason2 = preset_availability(vpt, available={'time_axis'})
    assert can_run2 and reason2 == ''


def test_presets_for_filters_by_workflow():
    condensate = presets_for('condensate_puncta_refinement')
    assert {p.key for p in condensate} == {'condensate_snr_gate_validated',
                                           'invitro_condensate_confocal_63x'}
    assert presets_for('nonexistent_workflow') == []
