"""**A channel with no recoverable identity gets a last-resort, remembered prompt (sidecar_metadata Part 4).**

When metadata, filename, and the pixel classifier all fail, a channel falls to the position guess
('C0-Blue'). Part 4 asks the user — only then, optionally — and remembers the answer for future same-layout
files (extending the signature-keyed `channel_designations` store, never a second one). The decision logic and
persistence are `core`; the dialog is `integration` (Qt-smoke). No file-level marker — the file mixes tiers.
"""
import pytest


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    from pycat.utils import channel_designations as cd
    monkeypatch.setattr(cd, "_store_path", lambda: str(tmp_path / "cd.json"))
    monkeypatch.setattr(cd, "_CACHE", None)
    return cd


# ── the decision logic ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.core
def test_channel_needs_identity_only_for_the_position_guess():
    from pycat.utils.channel_naming import channel_needs_identity
    assert channel_needs_identity({"source": "position"}) is True
    for src in ("name", "filename", "wavelength", "pixels"):
        assert channel_needs_identity({"source": src}) is False    # identified from real evidence → never ask
    assert channel_needs_identity({}) is True
    assert channel_needs_identity(None) is True


# ── the persistence (extends the signature-keyed store) ────────────────────────────────────────────────

@pytest.mark.base
def test_identity_answers_round_trip_and_are_reversible(isolated_store):
    cd = isolated_store
    infos = [{"bucket": "blue"}, {"bucket": "green"}]
    assert cd.recall_channel_identities(infos) == {}
    assert cd.remember_channel_identity(infos, 1, "FUS-GFP") is True
    assert cd.recall_channel_identities(infos) == {1: "FUS-GFP"}
    assert cd.remember_channel_identity(infos, 0, "   ") is False   # a blank answer is opt-in → not stored
    assert cd.forget_channel_identity(infos, 1) is True             # reversible
    assert cd.recall_channel_identities(infos) == {}


@pytest.mark.base
def test_identity_and_a_condensate_designation_coexist_either_order(isolated_store):
    cd = isolated_store
    infos = [{"bucket": "blue"}, {"bucket": "green"}]
    cd.remember_channel_identity(infos, 0, "DAPI")                  # identity first...
    cd.remember_designation(infos, 1)                              # ...then a condensate designation
    assert cd.recall_designation(infos) == 1                       # neither clobbers the other
    assert cd.recall_channel_identities(infos) == {0: "DAPI"}


@pytest.mark.base
def test_recall_is_guarded_to_the_acquisition_layout(isolated_store):
    cd = isolated_store
    cd.remember_channel_identity([{"bucket": "blue"}, {"bucket": "green"}, {"bucket": "red"}], 2, "X")
    assert cd.recall_channel_identities([{"bucket": "blue"}]) == {}  # a different layout → no stale recall


# ── the Qt prompt ──────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_the_dialog_asks_only_unidentified_channels_and_harvests_trimmed_answers(qtbot):
    from pycat.ui.channel_identity_dialog import build_channel_identity_dialog
    infos = [{"source": "name", "label": "DAPI"}, {"source": "position", "label": "C1-Green"}]
    dlg = build_channel_identity_dialog(infos)
    assert dlg is not None
    qtbot.addWidget(dlg)
    assert set(dlg._fields.keys()) == {1}                          # only the position-guess channel is asked
    dlg._fields[1].setText("  FUS-GFP  ")
    assert dlg.harvest() == {1: "FUS-GFP"}                         # trimmed, index-keyed; a blank stays unset


@pytest.mark.integration
def test_no_dialog_when_every_channel_is_identified(qtbot):
    from pycat.ui.channel_identity_dialog import build_channel_identity_dialog
    assert build_channel_identity_dialog([{"source": "name"}, {"source": "wavelength"}]) is None
