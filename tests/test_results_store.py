"""**Reopen a closed results dock from RETAINED results — never recompute (results_figure_reflow Part 2).**

The shared retain/reopen registry that every workflow uses so "Show results" is one mechanism. Pins: a
retained payload reopens by calling its rebuild (NEVER a recompute); an absent payload is a stated refusal,
not a silent re-run; re-registering supersedes and becomes most-recent; clearing forgets.
"""
import pytest

from pycat.utils.results_store import (
    retain_results, reopen_results, reopen_most_recent, has_results, results_label,
    disabled_reason, clear_results)

pytestmark = pytest.mark.core


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_results()
    yield
    clear_results()


def test_reopen_calls_the_retained_rebuild_and_never_recomputes():
    calls = {"rebuild": 0, "compute": 0}
    retain_results("vpt", lambda: calls.__setitem__("rebuild", calls["rebuild"] + 1), label="VPT")
    assert has_results("vpt")
    assert reopen_results("vpt") is True
    assert calls == {"rebuild": 1, "compute": 0}      # rebuilt from the payload; no compute path was touched


def test_no_payload_is_a_stated_refusal_not_a_reopen():
    assert reopen_results("vpt") is False
    assert has_results("vpt") is False
    assert "Run the analysis first" in disabled_reason("vpt")


def test_a_present_payload_is_enabled_with_a_label():
    retain_results("vpt", lambda: None, label="VPT microrheology")
    assert disabled_reason("vpt") is None
    assert results_label("vpt") == "VPT microrheology"


def test_reregistering_supersedes_and_becomes_most_recent():
    seen = []
    retain_results("a", lambda: seen.append("a1"), label="A")
    retain_results("b", lambda: seen.append("b"), label="B")
    retain_results("a", lambda: seen.append("a2"), label="A")   # a fresh run of A → now the most recent
    assert reopen_most_recent() is True
    assert seen == ["a2"]                                        # the LATEST A rebuild, not the stale one


def test_reopen_most_recent_with_nothing_retained_is_false():
    assert reopen_most_recent() is False


def test_reopening_twice_reinvokes_the_rebuild_each_time():
    n = {"r": 0}
    retain_results("vpt", lambda: n.__setitem__("r", n["r"] + 1), label="VPT")
    reopen_results("vpt")
    reopen_results("vpt")
    assert n["r"] == 2      # the store re-invokes; the rebuild itself removes the stale dock (no duplicates)


def test_clear_forgets_one_key_or_all():
    retain_results("a", lambda: None, label="A")
    retain_results("b", lambda: None, label="B")
    clear_results("a")
    assert not has_results("a") and has_results("b")
    clear_results()
    assert not has_results("b")


def test_retain_rejects_a_non_callable_rebuild():
    with pytest.raises(TypeError):
        retain_results("x", "not callable", label="X")
