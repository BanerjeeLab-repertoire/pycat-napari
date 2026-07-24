"""**Qt-smoke: the workflow preset picker lists presets, greys the unrunnable, and applies populate-not-lock (analysis_presets Part B).**

Integration (needs Qt + qtbot). The preset objects, availability gating, and the populate-not-lock
`PresetApplication` are `core`-tested in `tests/test_analysis_presets.py`; this proves the picker is wired to
them — a runnable preset applies and hands the caller a `PresetApplication` seeded (not locked) with the
values, an unrunnable one is greyed with its reason, and a workflow with no presets yields no picker.
"""
import pytest

pytestmark = pytest.mark.integration


def test_the_picker_lists_a_preset_and_applying_it_seeds_a_populate_not_lock_application(qtbot):
    from pycat.ui.preset_picker import build_preset_picker

    applied = []
    picker = build_preset_picker("vpt_msd", available={"time_axis"}, on_apply=applied.append)
    assert picker is not None
    qtbot.addWidget(picker)

    assert "vpt_bead_tracking" in picker._apply_buttons
    btn = picker._apply_buttons["vpt_bead_tracking"]
    assert btn.isEnabled()                                  # the session provides time_axis → runnable

    btn.click()
    assert applied and applied[0].preset.key == "vpt_bead_tracking"
    assert applied[0].values == {"min_track_length": 200}   # seeded from the preset...
    assert applied[0].is_modified is False                  # ...but not locked, and unedited so far


def test_an_unrunnable_preset_is_greyed_with_its_reason(qtbot):
    from pycat.ui.preset_picker import build_preset_picker
    picker = build_preset_picker("vpt_msd", available=set())     # no time_axis → the preset can't run
    qtbot.addWidget(picker)
    assert picker._apply_buttons["vpt_bead_tracking"].isEnabled() is False


def test_a_workflow_without_presets_has_no_picker(qtbot):
    from pycat.ui.preset_picker import build_preset_picker
    assert build_preset_picker("no_such_workflow") is None      # nothing to offer → no widget
