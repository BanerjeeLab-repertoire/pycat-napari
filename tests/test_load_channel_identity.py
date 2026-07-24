"""**Channel identity on load: sidecar enrichment + remembered identities (sidecar_metadata Part 4/5 wiring).**

The live 2D loader calls `resolve_channel_identity_on_load` after building its per-channel identity dicts, and
`remember_user_channel_names` when the user names an unidentified channel. Those orchestration functions are
Qt-free and deterministic, so they are tested here headlessly. The enrichment tier is numpy-only (`core`); the
persistence store reaches the scientific stack through `channel_designations`, so its tests are `base`.
"""
import pytest

# Guarded import (kept out of module top-level) so the headless collector does not skip this module on the
# `pycat.file_io` prefix — load_channel_identity is Qt-free/headless-safe. See test_metadata_merge.
try:
    from pycat.file_io.load_channel_identity import (
        enrich_with_sidecar,
        enrich_channel_from_sidecar,
        apply_recalled_identities,
        remember_user_channel_names,
    )
except Exception:      # pragma: no cover - only when the io stack is truly unavailable
    pytest.skip("pycat.file_io.load_channel_identity unavailable", allow_module_level=True)


# ── Sidecar enrichment (numpy-only) ────────────────────────────────────────────────────────────────────

@pytest.mark.core
def test_a_sidecar_emission_names_a_position_channel_and_never_brightfield():
    # The reported ISS case: both channels fell to a pixel/position guess (one mislabelled 'Brightfield'),
    # but the _fbs.xml carries their emission filters — 647 nm (far-red) and 525 nm (green).
    info = [
        {"source": "pixels", "bucket": "transmitted", "label": "Brightfield", "layer_name": "C0-Brightfield"},
        {"source": "position", "bucket": "green", "label": "Green", "layer_name": "C1-Green"},
    ]
    sidecar = {"channels": [{"index": 0, "emission_nm": 647}, {"index": 1, "emission_nm": 525}]}
    out = enrich_with_sidecar(info, sidecar)
    assert out[0]["source"] == "wavelength" and out[0]["bucket"] == "far_red"
    assert out[0]["label"] != "Brightfield"                      # the reported bug: no longer brightfield
    assert out[1]["source"] == "wavelength" and out[1]["bucket"] == "green"


@pytest.mark.core
def test_a_sidecar_never_overwrites_real_in_file_metadata():
    info = [{"source": "name", "bucket": "blue", "label": "DAPI", "layer_name": "C0-DAPI"}]
    out = enrich_with_sidecar(info, {"channels": [{"index": 0, "emission_nm": 647}]})
    assert out[0]["label"] == "DAPI" and out[0]["source"] == "name"   # metadata wins over a sidecar


@pytest.mark.core
def test_enrichment_is_non_gating_without_a_sidecar_or_channels():
    info = [{"source": "position", "bucket": "blue", "label": "DAPI"}]
    assert enrich_with_sidecar(info, None) is info                    # no sidecar → unchanged, no crash
    assert enrich_with_sidecar(info, {"channels": []}) is info        # empty → unchanged
    assert enrich_with_sidecar(info, {"channels": [{"index": 0}]}) == info  # no emission → unchanged


# ── Per-channel enrichment (the stack back-ends name each layer in a loop, not from a list) ─────────────

@pytest.mark.core
def test_a_single_channel_is_named_from_the_sidecar_at_its_own_index():
    # A stack back-end enriches channel 1 (a position guess) from the sidecar entry whose index is 1.
    sidecar = {"channels": [{"index": 0, "emission_nm": 525}, {"index": 1, "emission_nm": 647}]}
    weak = {"source": "position", "bucket": "green", "label": "Green", "layer_name": "C1-Green"}
    out = enrich_channel_from_sidecar(weak, sidecar, 1)
    assert out["source"] == "wavelength" and out["bucket"] == "far_red"    # named from ch1's 647 nm, not ch0's


@pytest.mark.core
def test_a_single_channel_with_real_metadata_is_left_alone():
    named = {"source": "name", "bucket": "blue", "label": "DAPI", "layer_name": "C0-DAPI"}
    assert enrich_channel_from_sidecar(named, {"channels": [{"index": 0, "emission_nm": 647}]}, 0) is named


@pytest.mark.core
def test_single_channel_enrichment_is_non_gating():
    weak = {"source": "position", "bucket": "blue", "label": "DAPI"}
    assert enrich_channel_from_sidecar(weak, None, 0) is weak                 # no sidecar → unchanged
    assert enrich_channel_from_sidecar(weak, {"channels": [{"index": 2, "emission_nm": 647}]}, 0) is weak  # no entry for idx 0


# ── Remembered identities (reach the scientific stack via channel_designations) ─────────────────────────

@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    from pycat.utils import channel_designations as cd
    monkeypatch.setattr(cd, "_store_path", lambda: str(tmp_path / "cd.json"))
    monkeypatch.setattr(cd, "_CACHE", None)
    return cd


@pytest.mark.base
def test_a_remembered_name_recalls_onto_a_weak_channel_as_a_user_identity(isolated_store):
    info = [{"source": "position", "bucket": "far_red", "label": "FarRed"},
            {"source": "wavelength", "bucket": "green", "label": "Green"}]
    isolated_store.remember_channel_identity(info, 0, "FUS-mCherry")
    out = apply_recalled_identities(info)
    assert out[0]["source"] == "user" and out[0]["label"] == "FUS-mCherry"
    assert out[0]["bucket"] == "far_red"                             # colour preserved
    assert out[1] == info[1]                                         # a metadata channel is untouched


@pytest.mark.base
def test_a_typed_name_for_an_unidentified_channel_round_trips_through_recall(isolated_store):
    info = [{"source": "position", "bucket": "far_red", "label": "FarRed", "layer_name": "C0-FarRed"},
            {"source": "name", "bucket": "blue", "label": "DAPI", "layer_name": "C0-DAPI"}]
    remembered = remember_user_channel_names(info, ["FUS-mCherry", "DAPI"])
    assert remembered == [0]                                         # only the unidentified channel is stored
    assert isolated_store.recall_channel_identities(info) == {0: "FUS-mCherry"}


@pytest.mark.base
def test_a_blank_or_default_answer_is_not_remembered(isolated_store):
    info = [{"source": "position", "bucket": "far_red", "label": "FarRed", "layer_name": "C0-FarRed"}]
    assert remember_user_channel_names(info, ["   "]) == []          # blank → nothing
    assert remember_user_channel_names(info, ["C0-FarRed"]) == []    # left at the default → nothing
    assert isolated_store.recall_channel_identities(info) == {}


@pytest.mark.base
def test_recall_only_applies_to_weak_channels_never_overwriting_metadata(isolated_store):
    # A remembered answer keyed to a layout must not clobber a channel that a later file DID identify.
    weak = [{"source": "position", "bucket": "green", "label": "Green"}]
    isolated_store.remember_channel_identity(weak, 0, "GFP")
    named = [{"source": "name", "bucket": "green", "label": "EGFP"}]   # same signature, but real metadata now
    out = apply_recalled_identities(named)
    assert out[0]["label"] == "EGFP" and out[0]["source"] == "name"    # metadata is not overwritten
