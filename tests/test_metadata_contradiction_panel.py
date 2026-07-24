"""**The metadata dialog's contradiction section: list them first + a reversible 'expected' control (tag_confidence Part 3).**

The engine (contradictions, severity, the per-pattern/fingerprint 'expected' store) and the button indicator
are done; this is the dialog surface. `contradiction_rows` (Qt-free render model) is `core`; the panel widget
is `integration` (Qt-smoke). No file-level marker — the file mixes both tiers.
"""
import pytest

from pycat.utils.user_settings import UserSettings

# A loaded {common, raw} dict whose instrument block carries the oil-immersion-vs-air-medium contradiction
# (RI 1.518 is oil's) — the engine's canonical CRITICAL case, read from raw['instrument'].
_OIL_AIR = {"raw": {"instrument": {"immersion": "Oil", "medium": "Air", "refractive_index": 1.518}}}


# ── the Qt-free render model ─────────────────────────────────────────────────────────────────────────

@pytest.mark.core
def test_rows_carry_original_severity_and_expected_status(tmp_path):
    from pycat.utils.metadata_contradictions import contradiction_rows, mark_expected
    store = UserSettings(path=tmp_path / "s.json")

    rows, fingerprint = contradiction_rows(_OIL_AIR, store=store)
    crit = next(r for r in rows if r.severity == "critical")
    assert crit.pattern == "immersion_vs_medium" and crit.expected is False
    assert "Oil immersion" in crit.message                      # concrete, not vague

    mark_expected(crit.pattern, fingerprint, store)             # user: "expected for this instrument"
    rows2, _ = contradiction_rows(_OIL_AIR, store=store)
    same = next(r for r in rows2 if r.pattern == crit.pattern)
    # ORIGINAL severity is preserved (so the dialog can say "this was critical, you marked it expected"),
    # and the expected flag flips — reversibly.
    assert same.severity == "critical" and same.expected is True


@pytest.mark.core
def test_a_clean_file_produces_no_rows(tmp_path):
    from pycat.utils.metadata_contradictions import contradiction_rows
    rows, _ = contradiction_rows({"common": {}, "raw": {}}, store=UserSettings(path=tmp_path / "s.json"))
    assert rows == ()                                            # cry-wolf: a clean file lists nothing


# ── the Qt panel (smoke) ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_the_panel_lists_a_contradiction_and_marking_it_persists_and_re_renders(qtbot, tmp_path):
    from pycat.ui.metadata_contradiction_panel import build_contradiction_panel
    from pycat.utils.metadata_contradictions import is_expected
    store = UserSettings(path=tmp_path / "s.json")

    changed = []
    panel = build_contradiction_panel(_OIL_AIR, store, on_change=lambda: changed.append(1))
    assert panel is not None
    qtbot.addWidget(panel)

    assert panel._buttons, "a critical contradiction must offer the 'expected' control"
    pattern, btn = panel._buttons[0]
    assert not is_expected(pattern, panel._fingerprint, store)
    assert "Expected" in btn.text()

    btn.click()                                                 # mark expected
    assert is_expected(pattern, panel._fingerprint, store)      # persisted, keyed to the fingerprint
    assert changed == [1]                                       # the caller's refresh hook fired
    _, btn2 = next(b for b in panel._buttons if b[0] == pattern)
    assert "Unmark" in btn2.text()                              # re-rendered: now offers the reversal

    btn2.click()                                                # unmark — reversible
    assert not is_expected(pattern, panel._fingerprint, store)


@pytest.mark.integration
def test_the_panel_is_none_for_a_clean_file(qtbot, tmp_path):
    from pycat.ui.metadata_contradiction_panel import build_contradiction_panel
    panel = build_contradiction_panel({"common": {}, "raw": {}}, UserSettings(path=tmp_path / "s.json"))
    assert panel is None                                        # nothing to say → no section in the dialog
