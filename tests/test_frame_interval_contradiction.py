"""**A frame interval that disagreed with the per-frame timestamps was never surfaced.**

The loader's ``reconcile_frame_interval`` already sets ``common['frame_interval_inconsistent']`` (and the
nominal vs timestamp-derived values), but nothing read the flag — so the user never learned that the
declared cadence disagreed with the timestamps, even though a wrong interval silently corrupts every
time-derived quantity (D, viscosity, MSD, the moduli frequency axis). This surfaces it through the
existing contradiction framework as a CRITICAL row (Outstanding-Work spec F2).
"""
import pytest


def _mod():
    return pytest.importorskip("pycat.utils.metadata_contradictions")


@pytest.mark.core
def test_inconsistent_frame_interval_is_a_critical_contradiction():
    m = _mod()
    md = {'frame_interval_inconsistent': True,
          'frame_interval_nominal_s': 0.1, 'frame_interval_s': 0.5}
    cons = m.detect_contradictions(md)
    fi = [c for c in cons if c.pattern == 'frame_interval_inconsistent']
    assert len(fi) == 1 and fi[0].severity == 'critical'
    assert m.has_critical(cons)


@pytest.mark.core
def test_consistent_file_raises_no_frame_interval_row_and_stays_cry_wolf_clean():
    m = _mod()
    md = {'frame_interval_inconsistent': False, 'frame_interval_s': 0.1}
    assert not any(c.pattern == 'frame_interval_inconsistent'
                   for c in m.detect_contradictions(md))
    # a fully clean file raises nothing at all
    assert m.detect_contradictions({}) == []


@pytest.mark.core
def test_flag_threads_through_the_panel_engine_input_from_common():
    """The panel feeds a {common, raw} dict; the reconciled flag must reach the engine from common."""
    m = _mod()
    file_md = {'common': {'frame_interval_inconsistent': True,
                          'frame_interval_nominal_s': 0.1, 'frame_interval_s': 0.5},
               'raw': {}}
    rows, _fp = m.contradiction_rows(file_md)
    assert any(r.pattern == 'frame_interval_inconsistent' and r.severity == 'critical'
               for r in rows)
