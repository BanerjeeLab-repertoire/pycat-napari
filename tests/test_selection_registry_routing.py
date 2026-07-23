"""**Resolution routes through the entity registry — views ask the ONE authority where an object is now.**

The identity-registry contract's consumer side: a view carries only the entity id and resolves location
through the registry, so a re-crop / layer re-add / frame reindex (an `update_location`) is followed by
every subsequent navigation — closing the "a ref carries correct identity with STALE location" divergence.
These pin: a stale ref is refreshed from the registry; an `update_location` propagates to resolution; an
unknown entity (or a ref with no id) is left unchanged (honest fallback, never an invented location); and
`resolve_offline` crops the registry's current location, not the stale bbox baked into the ref.
"""
import numpy as np
import pytest

from pycat.utils.entity_registry import EntityLocation, EntityRecord, default_registry
from pycat.utils.object_ref import ObjectRef, location_from_registry, resolve_offline

pytestmark = pytest.mark.base


@pytest.fixture
def registered():
    """Register records under unique ids in the shared registry; drop them afterwards (test isolation)."""
    ids = []

    def _register(entity_id, location, **kw):
        default_registry().register(EntityRecord(entity_id=entity_id, location=location, **kw))
        ids.append(entity_id)
    yield _register
    for eid in ids:
        default_registry()._records.pop(eid, None)


def test_a_stale_ref_is_refreshed_from_the_registry(registered):
    registered('reg_route_e1', EntityLocation(bbox=(0, 0, 4, 4), frame=3))
    stale = ObjectRef(entity_id='reg_route_e1', bbox=(100, 100, 104, 104), frame=99)
    fresh = location_from_registry(stale)
    assert fresh.bbox == (0, 0, 4, 4) and fresh.frame == 3, (
        "resolution did not consult the registry — it used the ref's stale location")


def test_an_update_location_propagates_to_resolution(registered):
    """The divergence closes: after `update_location`, every resolve sees the NEW place — no per-view
    stale cache to chase."""
    registered('reg_route_e2', EntityLocation(bbox=(0, 0, 4, 4)))
    default_registry().update_location('reg_route_e2', EntityLocation(bbox=(50, 50, 54, 54)))
    ref = ObjectRef(entity_id='reg_route_e2', bbox=(0, 0, 4, 4))       # the ref's own bbox is now stale
    assert location_from_registry(ref).bbox == (50, 50, 54, 54)


def test_an_unknown_entity_is_left_unchanged():
    """An honest fallback: the registry does not know this id (dataset closed / never registered), so the
    ref's last-known location is used — a wrong location is never invented."""
    ref = ObjectRef(entity_id='an_entity_the_registry_never_saw', bbox=(1, 2, 3, 4), frame=7)
    assert location_from_registry(ref) is ref or location_from_registry(ref).bbox == (1, 2, 3, 4)


def test_a_ref_without_an_entity_id_is_untouched():
    ref = ObjectRef(bbox=(1, 2, 3, 4))                                 # legacy ref, no name
    assert location_from_registry(ref) is ref


def test_a_registry_none_field_leaves_the_refs_own_value(registered):
    """Per field: the registry record has no frame, so the ref's own frame survives (only bbox is refreshed)."""
    registered('reg_route_e4', EntityLocation(bbox=(0, 0, 2, 2), frame=None))
    ref = ObjectRef(entity_id='reg_route_e4', bbox=(9, 9, 9, 9), frame=12)
    fresh = location_from_registry(ref)
    assert fresh.bbox == (0, 0, 2, 2) and fresh.frame == 12


# ── The offline crop follows the registry's CURRENT location, not the ref's stale bbox ──────────
def test_resolve_offline_crops_the_registry_location_not_the_stale_bbox(registered, tmp_path):
    import tifffile
    frame = np.zeros((32, 32), dtype=np.float32)
    frame[0:4, 0:4] = 100.0                                            # the object's real region
    path = tmp_path / 'field.tif'
    tifffile.imwrite(str(path), frame)

    # The registry knows the CURRENT location (0,0,4,4); the ref carries a STALE bbox pointing at empty space.
    registered('reg_route_e5', EntityLocation(bbox=(0, 0, 4, 4)))      # source None → keep the ref's path
    ref = ObjectRef(entity_id='reg_route_e5', bbox=(20, 20, 24, 24), source_path=str(path))

    crop, note = resolve_offline(ref, pad_px=0)
    assert crop is not None and crop.shape == (4, 4)
    assert float(crop.mean()) == 100.0, (
        "resolve_offline cropped the ref's STALE bbox (empty space) instead of the registry's current "
        "location — navigation did not route through the registry")
