"""**Entity registry — one authority resolving an entity id to its CURRENT location.**

Today a row carries an opaque `_pycat_entity_id` for equality AND separate location columns (bbox, layer
id, frame, source) for display, generated independently — so a row can carry the correct identity with
STALE location. This registry closes that divergence: `entity id → EntityRecord`, so a view holds only the
id and resolves location through one authority, and a location change (a labels layer re-added, a re-crop,
a frame reindex) is seen by every view at once.

**The contract that makes it worth building:**
- **Views carry only entity IDs** and call `resolve(id)` for location — they do NOT cache bbox/layer/frame
  independently (that is the divergence this closes).
- **One source of truth for location.** `update_location(id, ...)` updates the record; every subsequent
  `resolve` sees the new place. No stale columns to hunt down.
- **Resolution fails HONESTLY.** `resolve` returns `None` when the entity's dataset is closed or its layer
  is gone — a view then shows "cannot locate" rather than navigating to a wrong place. **A wrong location
  is worse than an admitted missing one.**

Migration is additive: the location columns stay (tables remain standalone-readable/exportable) but become
a registry-backed cache, not an independent source; brushing/navigation consult the registry. Populating
the registry belongs at the same finalization point that stamps identity (the auto-identity-stamping
spec), so one entity record produces both the id and the registry entry — that wiring is the dependent
follow-on; this module is the authority it will populate.
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class EntityLocation:
    """Where to SHOW an entity right now — decoupled from its identity. Any field may be ``None`` (an
    offline table has no live layer; a single-frame object has no frame)."""
    bbox: "tuple | None" = None            # (y0, x0, y1, x1) in the source image
    layer_id: "str | None" = None          # the live labels/points layer, when one exists
    frame: "int | None" = None             # the frame/slice index for a time-series/z-stack object
    source: "str | None" = None            # the source file/path (a LOCATION attribute, not identity)


@dataclasses.dataclass(frozen=True)
class EntityRecord:
    """The registry's unit: an entity's durable id bound to its current location + provenance + dataset.
    Identity (``entity_id``) and location (``location``) live in ONE record so they cannot drift apart."""
    entity_id: str
    location: EntityLocation
    provenance: object | None = None
    dataset: "str | None" = None           # the durable dataset id (a UUID, once the dataset-UUID spec lands)


class EntityRegistry:
    """``entity id → EntityRecord``. The single authority a view resolves location through, so identity and
    location can never diverge and a location change propagates to every view at once."""

    def __init__(self):
        self._records: dict = {}

    def register(self, record: EntityRecord) -> None:
        """Bind an entity id to its record (replacing any prior record for that id)."""
        self._records[str(record.entity_id)] = record

    def resolve(self, entity_id) -> "EntityRecord | None":
        """The current record for ``entity_id``, or ``None`` — an HONEST miss (dataset closed / layer
        gone). A view shows 'cannot locate' rather than navigating to a guessed, wrong place."""
        return self._records.get(str(entity_id))

    def update_location(self, entity_id, location: EntityLocation) -> None:
        """Point an entity at a NEW location (a layer re-added under a new id, a re-crop, a frame reindex).
        Every subsequent ``resolve`` sees it — no per-view stale cache to update. No-op for an unknown id."""
        rec = self._records.get(str(entity_id))
        if rec is not None:
            self._records[str(entity_id)] = dataclasses.replace(rec, location=location)

    def invalidate_dataset(self, dataset) -> None:
        """Drop every record for a dataset that has closed, so its entities resolve to ``None`` (honest
        miss) rather than to a location that no longer exists."""
        dead = [eid for eid, rec in self._records.items() if rec.dataset == dataset]
        for eid in dead:
            self._records.pop(eid, None)

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, entity_id) -> bool:
        return str(entity_id) in self._records


# The shared authority. Populated automatically at the identity-stamping chokepoint (see
# ``entity_ref.populate_registry``), so a view holding only an id resolves its CURRENT location through
# this one registry rather than caching bbox/layer/frame off whatever table it was handed.
_DEFAULT_REGISTRY = EntityRegistry()


def default_registry() -> EntityRegistry:
    """The process-wide entity registry the finalization chokepoint populates and views resolve through."""
    return _DEFAULT_REGISTRY
