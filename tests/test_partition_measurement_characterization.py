"""**Characterization net for `partition_measurement` — pins the background-subtracted assumption logic.**

`partition_measurement` was a 191-line function whose bulk is the background-subtracted assessment: the
image cannot tell a camera pedestal from a genuine dilute phase, so the tool asks (or resolves it with a
dark reference) and records the assumption as checked/holds/detail rather than guessing. Extracting that
phase into a helper is only safe if the split is **byte-identical**: which branch fires, and the exact
checked/holds verdict and detail wording of the `background_subtracted` assumption, must be unchanged
across every input (dark reference / not-stated / stated-true / stated-false), and the other assumptions
and the measurement identity must be untouched.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.core

_H = _W = 120


@pytest.fixture
def _field(monkeypatch):
    import pycat.toolbox.invitro_tools as it
    monkeypatch.setattr(it, 'napari_show_warning', lambda *a, **k: None)
    monkeypatch.setattr(it, 'napari_show_info', lambda *a, **k: None)
    yy, xx = np.mgrid[0:_H, 0:_W]
    img = np.full((_H, _W), 600.0)
    for cy, cx in [(40, 40), (80, 80)]:
        img += 2400.0 * (np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2) < 12)
    img += np.random.default_rng(0).normal(0, 3, (_H, _W))
    lab = np.zeros((_H, _W), int)
    for i, (cy, cx) in enumerate([(40, 40), (80, 80)], 1):
        lab[np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2) < 12] = i
    return img, lab


def _bg(m):
    return next(a for a in m.assumptions if a.name == 'background_subtracted')


def test_a_dark_reference_RESOLVES_the_background_assumption(_field):
    from pycat.toolbox.invitro_tools import partition_measurement
    m = partition_measurement(*_field, dark_reference=500.0)
    b = _bg(m)
    assert b.checked is True and b.holds is True
    assert b.detail.startswith('RESOLVED by a dark reference')
    assert 'nothing about the segmentation' in b.detail       # the in-claim scope note is preserved


def test_no_statement_records_the_assumption_as_NOT_CHECKED(_field):
    from pycat.toolbox.invitro_tools import partition_measurement
    b = _bg(partition_measurement(*_field))                    # background_subtracted is None
    assert b.checked is False and b.holds is None
    assert b.detail.startswith('NOT CHECKED') and 'DARK REFERENCE' in b.detail


def test_caller_stating_subtracted_holds_the_assumption(_field):
    from pycat.toolbox.invitro_tools import partition_measurement
    b = _bg(partition_measurement(*_field, background_subtracted=True))
    assert b.checked is True and b.holds is True
    assert b.detail == 'the caller states the background was subtracted'


def test_caller_stating_NOT_subtracted_fails_the_assumption(_field):
    from pycat.toolbox.invitro_tools import partition_measurement
    b = _bg(partition_measurement(*_field, background_subtracted=False))
    assert b.checked is True and b.holds is False
    assert 'not interpretable' in b.detail


def test_the_other_assumptions_and_measurement_identity_are_unchanged(_field):
    from pycat.toolbox.invitro_tools import partition_measurement
    m = partition_measurement(*_field, dark_reference=500.0)
    names = {a.name for a in m.assumptions}
    assert names == {'no_saturation', 'background_subtracted', 'dilute_phase_measured_locally'}
    assert m.name == 'partition coefficient' and m.units == 'dimensionless'
    assert {p.name for p in m.parameters} == {'percentile_bulk', 'saturation_level'}
