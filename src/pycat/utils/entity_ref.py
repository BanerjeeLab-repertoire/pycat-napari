"""**One object, named the same way twice.**

``ObjectRef`` answers *"where is this object?"* — a file, a frame, a bbox. That is enough to turn a
plot point back into a picture, and it is what increment 1 fixed. It is **not** enough to say that
*this* row and *that* row are the same object, because it has no name for the object that survives
the table being sorted, filtered, or re-derived.

Today a plot's points and a table's rows are matched **by row position**. Sort the table and the
correspondence is silently wrong — every point still highlights something, and nothing looks broken.

``EntityKey`` is the name. It is deliberately a *composite of facts that already exist*:

* ``dataset_id`` — which acquisition (the file it came from);
* ``operation_id`` — which operation produced it (``__pycat_op__``, the ``@tags_layer`` decorator's
  own id — see ``tag_registry``);
* ``entity_type`` — cell / punctum / mask_object / …;
* ``entity_id`` — stable *within* that (dataset, operation): ``f"{frame}/{label}"``.

── Why this is not an 89-site sweep ────────────────────────────────────────────────────────

An audit framed entity identity as *"add ``_pycat_entity_id`` to every object-level DataFrame"* —
89 ``regionprops`` sites. **That is obsolete**, because the identity it wants is already stamped in
two places and only needs a third:

* **layer identity** — ``metadata['pycat_layer_id']``, stamped once in the viewer tag hook for
  every layer (brushing increment 1);
* **operation identity** — ``__pycat_op__``, already carried by every ``@tags_layer`` function;
* **entity identity** — this module, applied at the **object-table chokepoints**.

Identity rides the same seam the tag system and OperationSpec already consolidate. So: three
builders, not eighty-nine, and every other table keeps working exactly as it does today — matched by
row position, and **flagged as such** rather than silently trusted (see `linkability_of`).

── The compat contract ─────────────────────────────────────────────────────────────────────

``ObjectRef`` is untouched and stays the currency of the brushing path. This module ADDS a typed
identity beside it, with adapters both ways. Nothing is migrated: increment 1's fixes are validated
and shipping, and a foundation that breaks them is not a foundation.
"""

from __future__ import annotations

import dataclasses

from pycat.utils.general_utils import debug_log
from pycat.utils.object_ref import ObjectRef

# The hidden columns. Hidden because they are identity, not measurement: a user reading a results
# table should not have to scroll past them, and a plot must never offer them as an axis.
#
# **"Hidden" has to be enforced, not merely asserted.** When these were introduced (1.6.74) this
# comment was the only thing hiding them: `DataFrameModel` shows every column a df has, the plot's
# axis combos are filled from `df.columns`, and `to_csv` writes the lot. So for two versions every
# results dialog listed `_pycat_entity_id`, every axis dropdown offered it, and every saved CSV
# carried it into the user's spreadsheet. `visible_columns` / `without_identity` are what actually
# hide them; a doc comment is not a mechanism.
ENTITY_ID_COLUMN = '_pycat_entity_id'
LAYER_ID_COLUMN = '_pycat_layer_id'

#: Every column that is identity rather than measurement. The `_pycat_` prefix is the contract:
#: anything added here later is hidden everywhere at once.
HIDDEN_PREFIX = '_pycat_'

UNKNOWN = 'unknown'


@dataclasses.dataclass(frozen=True)
class EntityKey:
    """**The name of an object, stable across sorting, filtering and re-derivation.**

    Frozen and hashable on purpose: this is what a ``dict`` or a ``set`` is keyed on when two views
    have to agree about which object is which.
    """

    dataset_id: str
    operation_id: str        # from __pycat_op__ / OperationSpec
    entity_type: str         # "cell" | "punctum" | "mask_object" | "track" | ...
    entity_id: str           # stable within (dataset, operation): e.g. f"{frame}/{label}"

    def as_column_value(self) -> str:
        """The single string that rides in the ``_pycat_entity_id`` column.

        **Opaque by design — it is never parsed back.** ``dataset_id`` is a file path and paths
        contain the separator, so a round-trip would be ambiguous the moment someone opened
        ``a/b.tif``. The pieces that a caller has to *act* on (the labels layer) travel in their own
        column instead of being smuggled into this string. What this value is for is *equality*:
        the same object gets the same string, whatever order the rows are in.
        """
        return f"{self.dataset_id}/{self.operation_id}/{self.entity_type}/{self.entity_id}"


@dataclasses.dataclass(frozen=True)
class EntityLocation:
    """**Where the object is** — enough to show it, in a session or from a cold start."""

    scene: int | str | None = None
    channel: int | None = None
    t: int | None = None
    z: int | None = None
    bbox_yx: tuple[int, int, int, int] | None = None
    centroid_yx: tuple[float, float] | None = None
    labels_layer_id: str | None = None       # == the layer's pycat_layer_id
    source_path: str | None = None


@dataclasses.dataclass(frozen=True)
class EntityRef:
    """**Identity + location.** The key says *which object*; the location says *where to look*."""

    key: EntityKey
    location: EntityLocation
    parent_keys: tuple[EntityKey, ...] = ()
    lineage_node_id: str | None = None

    def as_object_ref(self) -> ObjectRef:
        """The ``ObjectRef`` the brushing path already speaks.

        The adapters live on ``EntityRef`` rather than as ``ObjectRef.from_entity_ref`` (which the
        spec sketched) for one reason: ``object_ref`` must not import this module. It is the older,
        lower-level half — the whole brushing path depends on it — and pointing it at a module that
        imports it back is a circular import at load time.
        """
        label = None
        try:
            label = int(str(self.key.entity_id).rsplit('/', 1)[-1])
        except (TypeError, ValueError):
            pass

        return ObjectRef(
            object_id=label,
            frame=self.location.t if self.location.t is not None else self.location.z,
            bbox=self.location.bbox_yx,
            source_path=self.location.source_path,
            tags={'op': self.key.operation_id, 'target': self.key.entity_type},
            source_layer_id=self.location.labels_layer_id,
        )

    @classmethod
    def from_object_ref(cls, ref: ObjectRef, *, dataset_id=None, operation_id=None,
                        entity_type=None) -> 'EntityRef':
        """Lift an existing ``ObjectRef`` into the typed model, filling what it can.

        A legacy ref genuinely does not know its dataset or operation — this does not invent them.
        ``unknown`` is the honest value, and it is what makes such a ref compare unequal to a real
        one rather than collide with it.
        """
        tags = ref.tags or {}
        return cls(
            key=EntityKey(
                dataset_id=str(dataset_id or ref.source_path or UNKNOWN),
                operation_id=str(operation_id or tags.get('op') or UNKNOWN),
                entity_type=str(entity_type or tags.get('target') or UNKNOWN),
                entity_id=make_entity_id(ref.frame, ref.object_id),
            ),
            location=EntityLocation(
                t=ref.frame,
                bbox_yx=tuple(ref.bbox) if ref.bbox else None,
                labels_layer_id=ref.source_layer_id,
                source_path=ref.source_path,
            ),
        )


def make_entity_id(frame, label, parent=None) -> str:
    """``f"{frame}/{label}"`` — stable within one (dataset, operation).

    ``frame`` is None for a single 2-D image, and ``'-'`` rather than ``'None'`` keeps that case
    readable in a debug dump.

    ── ``parent`` is not decoration; without it the puncta key COLLIDES ────────────────────

    The spec's sketch is ``f"{frame}/{label}"``, which is right for cells and **wrong for puncta**:
    `puncta_analysis_func` calls `sk.measure.label(...)` *inside* its per-cell loop, so punctum
    labels **restart at 1 for every cell**. Punctum 1 in cell 1 and punctum 1 in cell 2 are
    different objects with the same label.

    Keyed on ``frame/label`` alone they would be **the same entity** — which is precisely the
    property ("same key → same object") that identity exists to guarantee, broken on the table
    people brush most.
    """
    frame_part = '-' if frame is None else int(frame)
    label_part = UNKNOWN if label is None else int(label)
    if parent is None:
        return f"{frame_part}/{label_part}"
    return f"{frame_part}/{int(parent)}/{label_part}"


def source_path_of(data_instance) -> str | None:
    """The file the active dataset came from, however the caller holds it.

    The same lookup `feature_analysis_tools` already does for its output naming. One copy, because
    two copies of "where did this come from" is how they end up disagreeing.
    """
    try:
        repo = getattr(data_instance, 'data_repository', None) or {}
        return repo.get('file_path') or getattr(data_instance, 'filePath', None) or None
    except Exception as exc:
        debug_log('source_path_of: could not read the source path', exc)
        return None


def dataset_id_for(source_path) -> str:
    """The dataset's identity — a **durable UUID** for a readable file, else the path.

    The file it came from is the stable thing (not the layer: layers close/rename/re-derive; the
    acquisition on disk does not). But the PATH breaks on move/remount/cross-platform, so a readable file
    is resolved to a persistent UUID (``dataset_identity`` registry) that survives a move. An absent or
    unreadable path (a test fixture, a batch replay of a relocated file, a registry outage) falls back to
    the path string — **backward-compatible**: nothing that stamped a non-existent path changes.
    """
    if not source_path:
        return UNKNOWN
    try:
        from pycat.utils.dataset_identity import uuid_for_path
        u = uuid_for_path(source_path)
        if u:
            return u
    except Exception:                # broad-ok: a durable id is a convenience; never cost the caller their table
        pass
    return str(source_path)


def entity_id_column(dataset_id, operation_id, entity_type, frame, label, parent=None) -> str:
    """The value for one row's ``_pycat_entity_id``."""
    return EntityKey(dataset_id=str(dataset_id or UNKNOWN),
                     operation_id=str(operation_id or UNKNOWN),
                     entity_type=str(entity_type or UNKNOWN),
                     entity_id=make_entity_id(frame, label, parent)).as_column_value()


def migrate_entity_id_dataset(df, old_dataset_id, new_dataset_id, *, column=ENTITY_ID_COLUMN) -> int:
    """Upgrade a pre-1.6.191 dataframe's ``_pycat_entity_id`` from a **path-based** ``dataset_id`` prefix to the
    durable **UUID** (dataset_identity_uuid migration, step 4).

    The id string is ``f"{dataset_id}/{operation_id}/{entity_type}/{entity_id}"`` and is **opaque — never
    parsed back** (a path ``dataset_id`` contains separators, so the composite cannot be split). Migration is a
    literal PREFIX swap: a row whose id begins ``f"{old_dataset_id}/"`` gets that prefix replaced with
    ``f"{new_dataset_id}/"``, leaving the rest (operation/type/frame/label) byte-identical. It is
    spelling-tolerant (``/`` vs ``\\`` inside the path) because the old prefix was ``str(source_path)`` on
    whatever platform stamped it. Rows not under the old prefix — and a df without the column — are left
    untouched; the swap is idempotent. Mutates the column in place and returns the number of rows migrated.

    This matters because both brushing and the entity registry match the WHOLE id string exactly: an
    un-migrated ``path/…`` id would never match a freshly-derived ``uuid/…`` id for the same object, so
    resolution would silently fail. After migration a restored id equals what a fresh stamp now produces.
    """
    try:
        if df is None or not old_dataset_id or not new_dataset_id:
            return 0
        if column not in getattr(df, 'columns', ()):
            return 0
        old = str(old_dataset_id)
        new = str(new_dataset_id)
        if old == new:
            return 0
        # The separator AFTER dataset_id in the composite is always '/'; only the path's INTERNAL spelling
        # varies by platform, so match both slash conventions.
        prefixes = {f"{p}/" for p in (old, old.replace("\\", "/"), old.replace("/", "\\"))}
        new_prefix = f"{new}/"
        n = 0

        def _swap(value):
            nonlocal n
            s = str(value)
            for pre in prefixes:
                if s.startswith(pre):
                    n += 1
                    return new_prefix + s[len(pre):]
            return value

        df[column] = df[column].map(_swap)
        return n
    except Exception as exc:      # broad-ok: a migration is best-effort; a failure must never break a session load
        debug_log("entity_ref: entity-id migration failed", exc)
        return 0


# The parent column, by every name the codebase actually writes it under. `'cell label'` — with a
# SPACE — is what `puncta_analysis_func` emits; everything else uses the underscore. See the note in
# `ObjectRef.from_row`.
_PARENT_COLUMNS = ('cell_label', 'cell label', 'parent_id')


def stamp_entity_ids(df, *, entity_type, source_path=None, operation_id=None,
                     labels_layer=None, frame=None, label_column='label',
                     parent_column=None, frame_column=None):
    """**Give every row in an object table a name.** Returns the same df, with the hidden columns.

    Called at the table-building chokepoints. Additive and total: a df that cannot be stamped (no
    label column) comes back **untouched** rather than half-marked — a table with identity on *some*
    rows is worse than one with none, because it looks linked. That is the degraded state
    `linkability_of` exists to show, not a failure.

    ``parent_column`` is looked up automatically among the names the codebase uses; it matters for
    puncta, whose labels restart per cell (see `make_entity_id`).

    ``frame_column`` gives identity its frame **PER ROW** for a multi-frame table (a tracked-object or
    time-series table where the same label recurs across frames as DIFFERENT entities). When it is absent
    the scalar ``frame`` is used for every row — correct only for a genuinely single-frame table. Stamping
    a whole time-series with one reference frame (the old behaviour) collapsed distinct entities onto one
    id; a real ``frame_column`` fixes that.

    ``labels_layer`` is optional and usually absent here on purpose — the output labels layer is
    typically created *after* the table (see `attach_layer_id`). In batch there is no viewer at all,
    and such a table is still fully resolvable **offline** through ``source_path`` + bbox.
    """
    try:
        if df is None or label_column not in getattr(df, 'columns', ()):
            return df

        dataset = dataset_id_for(source_path)

        parents = None
        for name in ([parent_column] if parent_column else _PARENT_COLUMNS):
            if name and name in df.columns:
                parents = df[name]
                break

        # Per-row frame when a frame_column is present (a multi-frame table); else the scalar `frame`.
        per_frame = df[frame_column].to_numpy() if (frame_column and frame_column in df.columns) else None

        def _frame_at(i):
            return per_frame[i] if per_frame is not None else frame

        if parents is None:
            values = [entity_id_column(dataset, operation_id, entity_type, _frame_at(i), label)
                      for i, label in enumerate(df[label_column])]
        else:
            values = [entity_id_column(dataset, operation_id, entity_type, _frame_at(i), label, parent)
                      for i, (label, parent) in enumerate(zip(df[label_column], parents))]
        df[ENTITY_ID_COLUMN] = values

        layer_id = None
        if labels_layer is not None:
            layer_id = (getattr(labels_layer, 'metadata', None) or {}).get('pycat_layer_id')
        df[LAYER_ID_COLUMN] = layer_id
    except Exception as exc:
        # Identity is a convenience; a results table is not. Never cost the user their numbers.
        debug_log('stamp_entity_ids: could not stamp entity identity', exc)
    return df


# ── Automatic stamping via declaration (the finalization chokepoint) ─────────────────────────────
#
# Manual `stamp_entity_ids` reaches only the 3 sites that remember to call it; every new analysis is one
# forgotten call away from silent row-position linking. The fix is to make an operation DECLARE how its
# rows are identified, once, and stamp automatically at the result-finalization chokepoint — so coverage
# grows by DECLARATION, not by scattering more stamping calls. `operation_id` comes from the declaration,
# not a hard-coded string at the call site, which is the interactive/batch divergence the audit named.
@dataclasses.dataclass(frozen=True)
class EntitySpec:
    """How an operation's object-table rows are identified. Declared once per operation via
    ``register_entity_spec``; the finalization chokepoint reads it to stamp identity + location together."""
    entity_type: str
    label_column: str = 'label'
    parent_column: "str | None" = None      # None → auto-detect among the known parent-column spellings
    frame_column: "str | None" = None        # None → single-frame (scalar frame); set for multi-frame tables


_ENTITY_SPECS: dict = {}


def register_entity_spec(operation_id, spec):
    """Declare that ``operation_id`` produces the entities described by ``spec``. Idempotent."""
    _ENTITY_SPECS[str(operation_id)] = spec
    return spec


def entity_spec_for(operation_id):
    """The `EntitySpec` declared for ``operation_id``, or ``None`` (an undeclared operation stays honestly
    row-position-linked — never silently mis-stamped)."""
    return _ENTITY_SPECS.get(str(operation_id)) if operation_id is not None else None


def finalize_entity_table(table, operation_id, *, source_path=None, frame=None, labels_layer=None):
    """**The identity chokepoint.** If ``operation_id`` declares an `EntitySpec`, stamp the table's identity
    AND location in one pass, using the operation's real id and the declared columns. An operation that
    declares nothing is returned untouched (honestly row-position-linked). **Idempotent** — a table already
    carrying ``_pycat_entity_id`` is returned unchanged, so the automatic runner path and a manual call can
    never double-stamp. This is what lets a new producer gain identity by declaring a spec, not by adding a
    stamping call."""
    spec = entity_spec_for(operation_id)
    if spec is None or table is None:
        return table
    already_stamped = ENTITY_ID_COLUMN in getattr(table, 'columns', ())
    stamped = table if already_stamped else stamp_entity_ids(
        table, entity_type=spec.entity_type, source_path=source_path, operation_id=operation_id,
        labels_layer=labels_layer, frame=frame, label_column=spec.label_column,
        parent_column=spec.parent_column, frame_column=spec.frame_column)
    # Identity and LOCATION are registered together from one record — the chokepoint that closes the
    # "identity can carry stale location" divergence. Additive; never costs the caller their table.
    if not already_stamped:
        populate_registry(stamped, source_path=source_path, operation_id=operation_id)
    return stamped


def populate_registry(table, *, registry=None, source_path=None, operation_id=None):
    """Populate the entity registry from a STAMPED table — one row → one `EntityRecord` binding the id to
    its CURRENT location (bbox / layer id / frame / source), provenance, and dataset. Called at the same
    finalization point that stamps identity, so the two are generated together and cannot drift. Rows
    without an id are skipped; unstamped tables do nothing. Never raises — the registry is a convenience."""
    from pycat.utils.entity_registry import EntityLocation, EntityRecord, default_registry
    reg = registry if registry is not None else default_registry()
    try:
        if table is None or ENTITY_ID_COLUMN not in getattr(table, 'columns', ()):
            return reg
        dataset = dataset_id_for(source_path)
        src = str(source_path) if source_path is not None else None
        for _, row in table.iterrows():
            eid = row.get(ENTITY_ID_COLUMN)
            if not eid:
                continue
            ref = ObjectRef.from_row(row, source_path=source_path)
            reg.register(EntityRecord(
                entity_id=str(eid),
                location=EntityLocation(bbox=ref.bbox, layer_id=row.get(LAYER_ID_COLUMN),
                                        frame=ref.frame, source=src),
                provenance=operation_id, dataset=dataset))
    except Exception as exc:  # broad-ok: registry population is additive; it must never break the analysis result
        debug_log('entity_ref: could not populate the entity registry', exc)
    return reg


def _register_default_entity_specs():
    """Declare the highest-value object-producing operations (increment 1). The three existing manual sites
    (cell / puncta / region props) migrate to these declarations — identical output, sourced from one place
    — and the top previously-unstamped producers (condensate, tracks, colocalized objects) now gain identity
    by declaration. Parent columns are left None where the codebase auto-detects the spelling (puncta emit
    ``'cell label'`` with a space); frame_column is set only for genuinely multi-frame tables (tracks)."""
    register_entity_spec('cell_analysis', EntitySpec('cell', label_column='label'))
    register_entity_spec('puncta_analysis', EntitySpec('punctum', label_column='label'))
    register_entity_spec('measure_region_props', EntitySpec('mask_object', label_column='label'))
    register_entity_spec('condensate_analysis', EntitySpec('condensate', label_column='label'))
    register_entity_spec('vpt_tracks', EntitySpec('bead', label_column='track_id', frame_column='frame'))
    register_entity_spec('object_colocalization', EntitySpec('colocalized_object', label_column='label'))


_register_default_entity_specs()


def attach_layer_id(df, labels_layer):
    """Fill in ``_pycat_layer_id`` once the labels layer actually **exists**.

    ── Why this is a second step ──────────────────────────────────────────────────────────

    The obvious design — "stamp the layer id where you stamp the entity id" — cannot work, and the
    code says so plainly: `run_cell_analysis_func` builds the table (`cell_analysis_func`, which
    returns `cell_df`) and only **then** calls `viewer.add_labels(labeled_cell_masks, ...)`. **At
    table-build time the layer a cell ref must point at has not been created yet.**

    And it must be the *output* labels layer, not the input mask: `source_layer_id` exists so that
    `selected_label = object_id` lands on the layer whose label values those ids actually are.

    So identity is stamped in two moves, both additive: the entity id when the numbers are made, the
    layer id when the layer is born. A table that never gets step two is still fully resolvable
    **offline** (source_path + bbox) — which is exactly the batch case, where there is no viewer at
    all.
    """
    try:
        if df is None or ENTITY_ID_COLUMN not in getattr(df, 'columns', ()):
            return df
        layer_id = (getattr(labels_layer, 'metadata', None) or {}).get('pycat_layer_id')
        if layer_id:
            df[LAYER_ID_COLUMN] = layer_id
    except Exception as exc:
        debug_log('attach_layer_id: could not attach the labels-layer id', exc)
    return df


def visible_columns(df):
    """The columns a USER should see: measurements, not identity."""
    try:
        return [c for c in df.columns if not str(c).startswith(HIDDEN_PREFIX)]
    except Exception:
        return []


def without_identity(df):
    """``df`` with the identity columns dropped — for display, for plotting, and for export.

    Identity is machinery. It belongs on the object, not in the scientist's results table: a CSV that
    lands in someone's spreadsheet with a ``_pycat_entity_id`` column is noise at best and a question
    at worst ("is this a measurement?"). Nothing reads these back from a CSV — session restore goes
    through the manifest — so exporting them buys nothing and costs clarity.
    """
    try:
        hidden = [c for c in df.columns if str(c).startswith(HIDDEN_PREFIX)]
        return df.drop(columns=hidden) if hidden else df
    except Exception:
        return df


def has_entity_ids(df) -> bool:
    """Does this table carry stable identity, or is it matched by row position?"""
    try:
        return ENTITY_ID_COLUMN in getattr(df, 'columns', ())
    except Exception:
        return False


# ── Linkability: the degraded state has to be VISIBLE ──────────────────────────────────────
#
# A table matched by row position still brushes, and brushes WRONG the moment it is sorted or
# filtered — every point highlights something, and nothing looks broken. That is the failure mode
# that has to be shown rather than discovered.
LINKED_BY_IDENTITY = 'Linked to image (stable identity)'
LINKED_BY_POSITION = 'Linked by row position (sort/filter unsafe)'


def linkability_of(df) -> str:
    """The state string a table/plot adapter shows. See increment 5."""
    return LINKED_BY_IDENTITY if has_entity_ids(df) else LINKED_BY_POSITION
