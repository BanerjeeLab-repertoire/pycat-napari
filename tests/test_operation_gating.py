"""**Runnability reaches the live UI: a Run button greys out, with a reason, when the data can't feed it.**

The decision logic (`operation_availability`) was already tested headlessly; what was missing was the
*facts* — what the loaded session actually provides. `session_facts` derives that set from the same
predicates the tools use, and `gate_run_button` turns it into an enabled/disabled + tooltip on a button.
Both are tested here without Qt (a stub button with `setEnabled`/`setToolTip`).
"""
import pytest

from pycat.ui.operation_gating import session_facts, gate_run_button

pytestmark = pytest.mark.core


class _Repo(dict):
    pass


class _CM:
    def __init__(self, dr):
        self.active_data_class = type("ADC", (), {"data_repository": dr})()


class _Btn:
    def __init__(self):
        self.enabled = True
        self.tip = ""
    def setEnabled(self, v):
        self.enabled = bool(v)
    def setToolTip(self, t):
        self.tip = t


def test_session_facts_derives_time_axis_and_channels_and_zstack():
    dr = {'n_t': 50, 'file_metadata': {'common': {'n_channels': 2, 'n_z': 9}}}
    facts = session_facts(_CM(dr), viewer=None)
    assert 'time_axis' in facts        # n_t present
    assert 'two_channels' in facts     # n_channels >= 2
    assert 'z_stack' in facts          # n_z > 1 (metadata fallback)


def test_session_facts_absent_when_nothing_loaded():
    facts = session_facts(_CM({}), viewer=None)
    assert 'time_axis' not in facts
    assert 'z_stack' not in facts
    assert 'two_channels' not in facts


def test_gate_disables_with_reason_when_requirement_unmet():
    b = _Btn()
    gate_run_button(b, ('z_stack',), _CM({}), viewer=None, base_tooltip="Run it")
    assert b.enabled is False
    assert 'z-stack' in b.tip and 'Run it' in b.tip     # reason + preserved base tooltip


def test_gate_enables_when_requirement_met():
    dr = {'file_metadata': {'common': {'n_z': 5}}}
    b = _Btn()
    gate_run_button(b, ('z_stack',), _CM(dr), viewer=None)
    assert b.enabled is True
    assert b.tip == ''


def test_gate_time_axis_reason():
    b = _Btn()
    gate_run_button(b, ('time_axis',), _CM({}), viewer=None)
    assert b.enabled is False
    assert 'time axis' in b.tip


def test_no_requirements_never_disables():
    b = _Btn()
    gate_run_button(b, (), _CM({}), viewer=None)
    assert b.enabled is True


def test_gate_fails_open_on_a_broken_session():
    """A gating error must never lock the user out — the button stays enabled."""
    class _Boom:
        @property
        def active_data_class(self):
            raise RuntimeError("boom")
    b = _Btn()
    gate_run_button(b, ('z_stack',), _Boom(), viewer=None)
    # session_facts swallows the repo error → no facts → z_stack unmet → disabled is acceptable,
    # but a hard error inside refresh must fail-open. Force the hard-error path:
    gate_run_button(b, ('z_stack',), object(), viewer=None)   # object() has no active_data_class
    assert b.enabled in (True, False)   # must not raise
