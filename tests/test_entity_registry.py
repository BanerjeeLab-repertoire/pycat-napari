"""**Entity registry — id → current location, one authority, honest misses.**

The registry closes the "identity and location can diverge" gap: a view holds only an entity id and
resolves location through the registry, so a location change is seen by everyone at once and a stale local
cache can't send a view to the wrong place. These pin the contract: register/resolve round-trip, an
unknown id resolves to None, `update_location` changes what every subsequent resolve returns (the
divergence test), a closed dataset's entities resolve to None, and identity+location travel in one record.
"""
import pytest

from pycat.utils.entity_registry import EntityRegistry, EntityRecord, EntityLocation

pytestmark = pytest.mark.core


def _rec(eid, *, bbox=(0, 0, 10, 10), layer='L1', frame=None, dataset='ds1'):
    return EntityRecord(entity_id=eid,
                        location=EntityLocation(bbox=bbox, layer_id=layer, frame=frame, source='a.tif'),
                        dataset=dataset)


def test_register_then_resolve_returns_the_record():
    reg = EntityRegistry()
    reg.register(_rec('e1'))
    got = reg.resolve('e1')
    assert got is not None and got.entity_id == 'e1' and got.location.bbox == (0, 0, 10, 10)


def test_an_unknown_id_resolves_to_None_an_honest_miss():
    reg = EntityRegistry()
    assert reg.resolve('nope') is None and 'nope' not in reg


def test_update_location_changes_what_every_subsequent_resolve_returns():
    """The divergence test: a view holding only the id sees the NEW location without being touched — the
    registry is the single authority, so there is no stale column to send it to the old place."""
    reg = EntityRegistry()
    reg.register(_rec('e1', bbox=(0, 0, 10, 10), layer='L1'))
    reg.update_location('e1', EntityLocation(bbox=(5, 5, 20, 20), layer_id='L2', source='a.tif'))
    got = reg.resolve('e1')
    assert got.location.bbox == (5, 5, 20, 20) and got.location.layer_id == 'L2'
    assert got.entity_id == 'e1', "identity must be preserved across a location update"


def test_update_location_on_an_unknown_id_is_a_noop():
    reg = EntityRegistry()
    reg.update_location('ghost', EntityLocation(bbox=(1, 1, 2, 2)))   # no crash, nothing registered
    assert reg.resolve('ghost') is None


def test_invalidate_dataset_drops_its_records_and_they_resolve_to_None():
    reg = EntityRegistry()
    reg.register(_rec('a', dataset='ds1'))
    reg.register(_rec('b', dataset='ds1'))
    reg.register(_rec('c', dataset='ds2'))
    reg.invalidate_dataset('ds1')
    assert reg.resolve('a') is None and reg.resolve('b') is None    # closed dataset → honest miss
    assert reg.resolve('c') is not None                             # other dataset untouched
    assert len(reg) == 1


def test_identity_and_location_travel_in_ONE_record():
    """Identity (entity_id) and location live in the same EntityRecord, so they cannot be generated
    independently and drift apart — the whole point."""
    rec = _rec('e1', frame=7)
    assert rec.entity_id == 'e1' and rec.location.frame == 7
    reg = EntityRegistry()
    reg.register(rec)
    assert reg.resolve('e1').location.frame == 7


def test_registering_the_same_id_replaces_the_record():
    reg = EntityRegistry()
    reg.register(_rec('e1', layer='L1'))
    reg.register(_rec('e1', layer='L2'))
    assert reg.resolve('e1').location.layer_id == 'L2' and len(reg) == 1
