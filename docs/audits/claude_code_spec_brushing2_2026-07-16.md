# Claude Code spec — Brushing increment 2: the entity-identity foundation

## ✅ STATUS — DONE, shipped in 1.6.74 (executed against the 1.6.73 tree)
`pytest -m core`: **640 passed, 2 skipped** (was 629). Definition of done met: the three dataclasses
exist and `ObjectRef` is untouched (adapters, not a rewrite); the 3 object tables carry
`_pycat_entity_id` + bbox; identity is stable across sort/filter; refs fill `source_layer_id` and
resolve to their own layer; degraded tables are flagged (`linkability_of`), not broken. Verified on
the **real** cell + puncta analysis, not just unit fixtures.

**Five premises that did not survive contact with the tree:**
1. **`entity_id = f"{frame}/{label}"` COLLIDES for puncta — the table people brush most.**
   `puncta_analysis_func` calls `sk.measure.label(...)` *inside* its per-cell loop, so punctum labels
   **restart at 1 in every cell**. Punctum 1 of cell 1 and punctum 1 of cell 2 would be the **same
   entity** — precisely the "same key → same object" guarantee this increment exists to provide. The
   parent cell is now part of the key. Confirmed on the real analysis: labels `[1,1,1,2,2,2]` → six
   distinct names.
2. **"Derive the layer id at the table-builder" is impossible — the layer does not exist yet.**
   `run_cell_analysis_func` calls `cell_analysis_func` (which returns the table) and only *then*
   `viewer.add_labels(...)`. Identity is stamped in two moves: entity id when the numbers are made
   (`stamp_entity_ids`), layer id when the layer is born (`attach_layer_id`). The three builders take
   arrays, not layers — no `.metadata` is reachable inside any of them.
3. **The puncta table must NOT get a layer id.** "Cell Labeled Puncta Mask" is painted with **cell**
   labels, not punctum labels, so pointing puncta refs at it would make a click on punctum 3
   highlight **cell 3** — increment 1's wrong-target bug, reintroduced by being helpful. Puncta stay
   bbox-resolvable (`crop_for_ref` / `resolve_offline` need no labels layer).
4. **"the puncta table already has `cell_label`" — it has `'cell label'`, with a SPACE.** So
   `ObjectRef.from_row`'s parent lookup (`cell_label`) has **always missed**, and every punctum ref
   has carried `parent_id=None`. `from_row` now accepts both spellings. The column itself was left
   alone — renaming it is a user-visible change to results tables and would silently activate
   `analysis_plots.py:1166`'s per-cell grouping, which currently never fires. **That mismatch is a
   live bug worth its own decision.**
5. **"exactly 3 `regionprops_table` chokepoints (verified)" — there are 4.**
   `segmentation_tools.py:1320` (`puncta_refinement_filtering_func`) is the same shape and even
   carries the same increment-1 bbox comment. **But its DataFrame never leaves the function** (a
   per-label lookup inside a filter loop; the function returns only `refined_puncta_mask`), so
   nothing brushes it and it needs no identity. The spec's "3 *brushable* tables" is right; its
   count of *calls* is not.

**Also worth knowing:** `measure_region_props` is not a fixed-schema site — the user picks the
properties from a dialog and may rename the columns, so `label` may be absent entirely. It is stamped
*before* the rename (the only point the column has a known name) and degrades visibly otherwise.
`dataset_id` had no existing referent; it is `data_repository['file_path']`, via one shared
`source_path_of`. And there are **two different uuids per layer** — `pycat_layer_id` (what
`source_layer_id` matches) and `pycat_tag_uid` (what `tag_registry.tags_for_plot` records as
`layer_tag_id`); anything matching a plot's recorded id against a ref's will not match.

`puncta_analysis_func` crossed the 120-line ceiling and was split (`_finalise_puncta_table`), per the
spec's own instruction not to raise it.

**Date:** 2026-07-16 · **Target tree:** verified against 1.6.70. **PREREQUISITE: brushing increment 1
must have landed** (`claude_code_spec_brushing1_2026-07-15.md`) — it introduces the `pycat_layer_id`
stamp in the tag hook and the optional `source_layer_id` field on `ObjectRef`. This increment FILLS
that field and adds the typed identity model. Re-validate the line numbers below against the tree when
you start — increment 1 will have shifted `object_ref.py`. Additive, no behaviour change. Touches
`utils/` + the 3 table-builders; not `file_io.py`.

## Why (the consolidation insight — NOT an 89-site sweep)
An audit framed entity identity as "add `_pycat_entity_id` to every object-level DataFrame" (89
regionprops sites). That's obsolete given the tag system. Verified in the tree:
- **Layer identity** is stamped once in the tag hook (`layer_tag_hook.py`) — increment 1's
  `pycat_layer_id`. One place, every layer.
- **Operation identity** already exists as `__pycat_op__` (the `@tags_layer` decorator = the
  `operation_id` the audit's `EntityKey` wants; OperationSpec formalizes it).
- **Object tables are born in exactly 3 `regionprops_table` chokepoints** (verified):
  `feature_analysis_tools.py:448` (cells), `:644` (puncta), `label_and_mask_tools.py:740` (masks).
  NOT 89. The other regionprops calls are per-object measurement, not table creation.

So identity rides the SAME seam OperationSpec consolidates: layer id (hook) + operation id
(`__pycat_op__`) + entity id (3 table-builders). Build on that; do NOT sweep 89 sites.

## Part A — the typed identity model (`utils/entity_ref.py`)
Define the audit's three frozen dataclasses:
```python
@dataclass(frozen=True)
class EntityKey:
    dataset_id: str
    operation_id: str        # from __pycat_op__ / OperationSpec
    entity_type: str         # "cell" | "punctum" | "mask_object" | "track" | ...
    entity_id: str           # stable within (dataset, operation): e.g. f"{frame}/{label}"

@dataclass(frozen=True)
class EntityLocation:
    scene: int | str | None = None
    channel: int | None = None
    t: int | None = None
    z: int | None = None
    bbox_yx: tuple[int, int, int, int] | None = None
    centroid_yx: tuple[float, float] | None = None
    labels_layer_id: str | None = None       # == the layer's pycat_layer_id
    source_path: str | None = None

@dataclass(frozen=True)
class EntityRef:
    key: EntityKey
    location: EntityLocation
    parent_keys: tuple[EntityKey, ...] = ()
    lineage_node_id: str | None = None
```
`ObjectRef` becomes a COMPAT SHIM over `EntityRef` — keep `ObjectRef` importable and constructable
(existing callers unchanged), but back it by an `EntityRef` internally, or provide
`EntityRef.as_object_ref()` / `ObjectRef.from_entity_ref()` adapters. Do NOT break any existing
`ObjectRef(...)` / `refs_from_dataframe` / `resolve_in_viewer` / `crop_for_ref` call.

## Part B — stamp identity at the 3 table-builders (SAFE-FIRST scope)
At each of the 3 `regionprops_table` chokepoints, add a hidden `_pycat_entity_id` column:
`f"{dataset_id}/{operation_id}/{frame}/{label}"`, and KEEP the bbox (regionprops already computes
`bbox`; add `'bbox'` to the `properties` tuple if absent — the audit found 25 files use regionprops
and only 1 keeps the bbox). Derive `dataset_id`/`operation_id` from the tags increment 1 + the tag
system already stamp on the source layer (`pycat_layer_id` + `__pycat_op__`), NOT from new inference.
- `feature_analysis_tools.py:448` → entity_type `cell`
- `feature_analysis_tools.py:644` → entity_type `punctum` (carry the parent cell_label into
  `parent_keys` — the puncta table already has `cell_label`)
- `label_and_mask_tools.py:740` → entity_type `mask_object`
Do the top-value tables first (these 3 cover the plots people actually brush). Other object tables
migrate opportunistically — a table without `_pycat_entity_id` still brushes by row position as today
(degraded, not broken).

## Part C — fill `source_layer_id` + build EntityRefs from the id column
- `refs_from_dataframe` (currently `iterrows()` → `ObjectRef` per row): when the df has
  `_pycat_entity_id`, populate each ref's `source_layer_id`/location from it (bbox, centroid,
  labels_layer_id). This CLOSES the loop increment 1 opened (it made `resolve_in_viewer` honour
  `source_layer_id`; now it gets filled). (Lazy construction — building refs only on click — is
  increment 4; here just fill the field when eagerly built.)
- Make "does this table have entity ids?" queryable so increment 5's "make linkability visible" can
  show Linked / by-position-only per table.

## Part D — make the degraded state VISIBLE
Per the audit's "make linkability visible": a table/plot backed by a df WITH `_pycat_entity_id` is
"Linked to image (stable identity)"; one WITHOUT is "Linked by row position (sort/filter unsafe)".
Surface this as a small state string the table/plot adapters (increment 5) can read. Just the
plumbing + the flag here.

## Steps
1. `utils/entity_ref.py`: the three dataclasses + the `ObjectRef` compat shim/adapters.
2. Stamp `_pycat_entity_id` + bbox at the 3 table-builders; wire puncta parent → `parent_keys`.
3. `refs_from_dataframe`: fill `source_layer_id`/location from `_pycat_entity_id` when present.
4. Add the linkability-state flag.
5. Tests (`core`): `test_entity_ref.py` — EntityKey stability across a sorted/filtered df (same key →
   same object); the 3 table-builders emit `_pycat_entity_id` + bbox; `ObjectRef` compat unchanged
   (existing `test_brushing.py` stays green); a ref built from a df resolves to its OWN layer via the
   filled `source_layer_id` (extends increment 1's wrong-target guard).
6. Full `pytest -m core` green (esp. `test_brushing.py`, and the complexity budget — extract helpers
   if a table-builder crosses 120 lines, don't raise the ceiling).
7. Ship: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (increment 2: identity
   foundation on the tag seam, 3 table-builders, not an 89-sweep).

## Definition of done
- `EntityKey`/`EntityLocation`/`EntityRef` exist; `ObjectRef` still works as a shim.
- The 3 object tables carry `_pycat_entity_id` + bbox; identity is stable across sort/filter.
- Refs fill `source_layer_id` from the id column; wrong-target resolution stays fixed.
- Degraded (by-position) tables are flagged, not broken.
- Full `pytest -m core` green.

## Cautions
- Additive only — every existing `ObjectRef`/brushing call must keep working. This is a foundation,
  not a migration of behaviour.
- 3 table-builders ONLY — do not sweep the 89 regionprops sites. Other tables degrade visibly.
- `dataset_id`/`operation_id` come from the tags already on the layer (increment 1 + `__pycat_op__`),
  not from new guessing at interaction time — that's the whole point of building on the seam.
- Don't build the SelectionService (increment 3) or lazy ref construction / overlay artist
  (increment 4) here.
