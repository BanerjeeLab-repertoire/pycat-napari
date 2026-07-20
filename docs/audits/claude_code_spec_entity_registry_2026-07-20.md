> **🟡 STATUS — authority DONE (1.6.189); population at the chokepoint DONE (1.6.197); SelectionService
> routing REMAINS.** `utils/entity_registry.py` — `EntityRecord` (id + `EntityLocation` + provenance +
> dataset in ONE record), `EntityRegistry` (register / resolve / update_location / invalidate_dataset) with
> honest `None` misses, and now a shared `default_registry()`. The **population wiring** landed once the
> auto-identity-stamping chokepoint existed (1.6.196): `entity_ref.populate_registry` runs at
> `finalize_entity_table`, so every chokepoint-stamped row registers its id → current location from ONE
> record (identity + location co-generated, per-row frames included). `tests/test_auto_identity_stamping.py`
> pins population; `tests/test_entity_registry.py` pins the authority + divergence test. **Remaining:** route
> `SelectionService` navigation through `resolve(id)` so views consult the registry as the location
> authority instead of reading columns off whatever table they hold — a consumer-side change on the
> brushing/selection resolution path, left as the next increment.

# Claude Code spec — Entity registry: separate identity from location resolution

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. The brushing
audit's §2 "identity and resolution can diverge" point. Today a row carries an opaque `_pycat_entity_id`
for equality **and** separate location columns (bbox, layer id, frame, source) for display — generated
independently, so a row can carry correct identity with stale location. This introduces a registry:
`entity id → current location/provenance record`, so views carry IDs and resolve location through one
authority.

## The problem (verified)
Verified: no entity registry exists (`grep entity_registry` → nothing). `_pycat_entity_id` establishes
equality; resolution relies on parallel columns generated elsewhere. The audit is precise: *"the two
must be generated together from one entity record. Otherwise a row can theoretically carry the correct
identity and stale location information."* The auto-identity-stamping spec co-generates them at stamp
time; this spec makes location **resolvable from the id afterward**, so a view holding only an id can
always find where to show the object — even if the table it came from is gone.

## Design
```python
@dataclass(frozen=True)
class EntityRecord:
    key: EntityKey
    location: EntityLocation          # bbox, layer id, frame — the current place to show it
    provenance: object | None         # the operation/lineage that produced it
    dataset: DatasetIdentity          # the durable dataset (UUID, from the dataset-identity spec)

class EntityRegistry:
    def register(self, record: EntityRecord) -> None
    def resolve(self, entity_id: str) -> EntityRecord | None   # id -> current location/provenance
    def update_location(self, entity_id, location) -> None     # e.g. layer re-added under a new id
    def invalidate_dataset(self, dataset_uuid) -> None         # dataset closed -> drop its records
```

### The contract that makes it worth building
- **Views carry only entity IDs.** A plot point, a table row, a VPT track reference all hold the opaque
  id and call `registry.resolve(id)` to get location when they need to display/navigate. They do **not**
  cache bbox/layer/frame independently — that is the divergence the audit names.
- **One source of truth for location.** When a labels layer is re-added, re-cropped, or a frame index
  changes, `update_location` updates the record and every view resolving through the registry sees the
  new location. No stale columns to hunt down.
- **Resolution can fail honestly.** `resolve` returns `None` when the entity's dataset is closed or the
  layer is gone — a view then shows "cannot locate" rather than navigating to a wrong place. A wrong
  location is worse than an admitted missing one.

## Migration — additive, alongside the columns
- Populate the registry at the same finalization point that stamps identity (the auto-stamping spec) —
  one entity record produces both the id and the registry entry, closing the divergence at the source.
- **Keep the location columns for now** (tables are still readable/exportable standalone), but make the
  registry the authority that brushing/navigation consult. The columns become a denormalized cache of
  the registry, not an independent source. Note this so a later increment can thin them if desired.
- `SelectionService` navigation resolves through the registry instead of reading columns off whichever
  table it was handed.

## Tests (`core`)
- Register then `resolve` returns the same record; an unknown id returns `None`.
- `update_location` changes what every subsequent `resolve` returns — a view holding only the id sees
  the new location without being touched.
- **The divergence test:** after a layer's location changes, resolving the id gives the NEW location,
  not a stale column value — proving the registry is the authority.
- `invalidate_dataset` drops that dataset's records; resolving them returns `None` (honest miss).
- A view carrying only an id can navigate correctly by resolving through the registry (no local
  bbox/layer cache).
- Registry population co-occurs with identity stamping (one record → id + registry entry).

## Steps
1. `EntityRecord` + `EntityRegistry` (resolve / update_location / invalidate_dataset).
2. Populate at the identity-stamping finalization point (one record → id + registry entry).
3. Route `SelectionService` navigation and view location lookups through `resolve`.
4. Keep location columns as a registry-backed cache; document the relationship.
5. Tests above, especially the divergence test.
6. Full `pytest -m core` green.
7. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (entity registry: id→location
   resolution; views carry ids and resolve through one authority).

## Definition of done
- An `EntityRegistry` resolves entity ids to current location/provenance.
- Views navigate by resolving ids, not by caching location independently.
- A location change is seen by every view through the registry (no stale-column divergence).
- Closed datasets resolve to `None` honestly.
- Registry entries are created together with identity stamps.
- Full `pytest -m core` green.

## Cautions
- **Depends on the auto-identity-stamping spec** (co-generation point) and benefits from the dataset-UUID
  spec (durable `dataset` field) — sequence after those.
- **Resolution failure must be honest** — return `None`, never a guessed/stale location. A wrong
  navigation target is worse than an admitted miss.
- Keep location columns as a cache during migration; do not rip them out and break standalone table
  export in the same step.
- Do not let views re-cache location independently — that reintroduces the exact divergence this closes.
