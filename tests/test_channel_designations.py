"""The persistent, opt-in channel-designation store must remember "which channel is the
condensate" per acquisition layout, recall it for same-layout files, and NEVER guess when nothing
is designated (returns None).

This is what lets fluorescence-pipeline layer selection stop depending on load order: a designated
channel is tagged target=condensate at load, so the resolver picks it regardless of which channel
loaded first.
"""

import os
import tempfile

import pytest

pytestmark = pytest.mark.core


@pytest.fixture
def store(tmp_path, monkeypatch):
    import pycat.utils.channel_designations as cd
    monkeypatch.setattr(cd, "_store_path", lambda: str(tmp_path / "chdes.json"))
    cd._reset_cache_for_tests()
    return cd


DAPI_GFP = [{"label": "DAPI", "bucket": "blue"}, {"label": "EGFP", "bucket": "green"}]
SINGLE_GREEN = [{"label": "EGFP", "bucket": "green"}]
METADATA_POOR = [{"label": "Fluorescence Image", "bucket": "unknown"},
                 {"label": "Fluorescence Image", "bucket": "unknown"}]


def test_signature_distinguishes_layouts(store):
    assert store.acquisition_signature(DAPI_GFP) != store.acquisition_signature(SINGLE_GREEN)


def test_empty_store_never_guesses(store):
    assert store.recall_designation(DAPI_GFP) is None


def test_remember_and_recall(store):
    assert store.remember_designation(DAPI_GFP, 1)
    assert store.recall_designation(DAPI_GFP) == 1


def test_designation_is_layout_specific(store):
    store.remember_designation(DAPI_GFP, 1)
    # a different acquisition layout must not inherit the designation
    assert store.recall_designation(SINGLE_GREEN) is None


def test_persists_across_sessions(store):
    store.remember_designation(DAPI_GFP, 1)
    store._reset_cache_for_tests()          # simulate a new session (reload from disk)
    assert store.recall_designation(DAPI_GFP) == 1


def test_out_of_range_stored_index_is_guarded(store):
    store._CACHE = {store.acquisition_signature(DAPI_GFP): {"condensate_channel_index": 5}}
    assert store.recall_designation(DAPI_GFP) is None


def test_forget(store):
    store.remember_designation(DAPI_GFP, 1)
    assert store.forget_designation(DAPI_GFP)
    assert store.recall_designation(DAPI_GFP) is None


def test_metadata_poor_channels_share_signature(store):
    # two identically-named unknown channels can't be told apart from tags — same signature,
    # so recall stays None until the user designates (honest, not a guess).
    sig = store.acquisition_signature(METADATA_POOR)
    assert "labels:" in sig  # falls back to labels when all buckets unknown
