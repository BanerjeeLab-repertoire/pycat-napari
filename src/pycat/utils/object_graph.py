"""**A first-class biological object graph — every detected object as a persistent identity, not a mask
label plus a disconnected row.** *(Increment 1: the record and the parent/child graph. Read-only.)*

PyCAT already produces the raw material: object tables stamped with a stable ``_pycat_entity_id`` (the
canonical `EntityKey` string), and a parent/child relation that exists in the data (a punctum knows its
cell). What is missing is a place where those facts live as OBJECTS with a graph over them, instead of being
scattered across DataFrame columns and key strings. This assembles that view.

**It reuses the existing identity — no parallel id scheme.** A `BiologicalObject` is keyed on the exact
``_pycat_entity_id`` value (``EntityKey.as_column_value()``); the graph never invents a second id. It is a
**read-only view assembled from tables PyCAT already produces**: it changes no table and re-runs no
analysis. A flat table (no parent information) yields a flat graph of roots; an object that names a parent
not present in the tables lands in an explicit **unrooted** bucket rather than being silently dropped or
silently rooted.

Increment 1 is the record + graph only. The linked-navigation / state-vector vision, and the
schema-specific join that derives a punctum's parent-cell id from the cell-labelled-puncta convention, are
later increments — this layer is generic over "objects that carry their own id and (optionally) their
parent's id".
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class BiologicalObject:
    """One detected object as a persistent identity. ``key`` is its ``_pycat_entity_id`` value (the stable
    `EntityKey` string); ``parent`` is another object's ``key`` or ``None`` (a root). ``children`` is filled
    by :class:`ObjectGraph` at build time — do not set it by hand."""
    key: str
    entity_type: str
    measurements: dict = dataclasses.field(default_factory=dict)
    qc_flags: str = ''
    provenance: dict = dataclasses.field(default_factory=dict)
    parent: str | None = None
    children: list = dataclasses.field(default_factory=list)


class ObjectGraph:
    """A read-only parent/child graph over `BiologicalObject`s, keyed by ``_pycat_entity_id``.

    Built once from a collection of objects; parent→child edges are resolved at construction. An object
    whose ``parent`` names a key NOT in the collection is **unrooted** (an orphan) — surfaced explicitly via
    :meth:`unrooted`, never silently rerooted. Roots (``parent is None``) and orphans are distinct.
    """

    def __init__(self, objects):
        self._by_key = {}
        for o in objects:
            self._by_key[o.key] = o
            o.children = []                       # reset — the graph owns the child edges
        self._unrooted = []
        for o in self._by_key.values():
            if o.parent is None:
                continue
            parent = self._by_key.get(o.parent)
            if parent is None:
                self._unrooted.append(o.key)      # declared a parent that isn't here → orphan
            else:
                parent.children.append(o.key)

    # ── lookups ──────────────────────────────────────────────────────────────
    def __len__(self):
        return len(self._by_key)

    def __contains__(self, key):
        return key in self._by_key

    def __iter__(self):
        return iter(self._by_key.values())

    def get(self, key):
        """The object with ``key``, or ``None``."""
        return self._by_key.get(key)

    def parent_of(self, key):
        """The parent `BiologicalObject` of ``key``, or ``None`` (root, orphan, or unknown key)."""
        obj = self._by_key.get(key)
        return self._by_key.get(obj.parent) if obj is not None and obj.parent else None

    def children_of(self, key):
        """The immediate child objects of ``key`` (empty if it has none / is unknown)."""
        obj = self._by_key.get(key)
        return [self._by_key[c] for c in obj.children] if obj is not None else []

    def descendants(self, key):
        """Every object below ``key``, breadth-first (children, grandchildren, …); cycle-guarded."""
        obj = self._by_key.get(key)
        if obj is None:
            return []
        out, seen, queue = [], {key}, list(obj.children)
        while queue:
            k = queue.pop(0)
            if k in seen or k not in self._by_key:
                continue
            seen.add(k)
            out.append(self._by_key[k])
            queue.extend(self._by_key[k].children)
        return out

    def ancestors(self, key):
        """Every object above ``key``, nearest first (parent, grandparent, …); cycle-guarded."""
        out, seen = [], {key}
        obj = self._by_key.get(key)
        while obj is not None and obj.parent and obj.parent not in seen:
            seen.add(obj.parent)
            parent = self._by_key.get(obj.parent)
            if parent is None:
                break
            out.append(parent)
            obj = parent
        return out

    def roots(self):
        """Objects with no parent (``parent is None``) — the top of each tree. Orphans are NOT roots."""
        return [o for o in self._by_key.values() if o.parent is None]

    def unrooted(self):
        """Objects that named a parent NOT present in the graph — surfaced, not silently rerooted."""
        return [self._by_key[k] for k in self._unrooted]

    def of_type(self, entity_type):
        """Every object of a given ``entity_type`` (e.g. ``'cell'`` / ``'punctum'``)."""
        return [o for o in self._by_key.values() if o.entity_type == entity_type]

    def filter(self, predicate):
        """Every object for which ``predicate(object)`` is truthy."""
        return [o for o in self._by_key.values() if predicate(o)]


# ── assembly from tables PyCAT already produces ──────────────────────────────────────────────────

def objects_from_table(df, entity_type, *, id_col='_pycat_entity_id', parent_id_col=None,
                       measurement_cols=None, qc_col='qc_flags', provenance_cols=()):
    """Build `BiologicalObject`s from one object table — one per row that carries a non-empty ``id_col``.

    ``parent_id_col``, when given, is the column holding each row's PARENT id (another row's ``id_col``
    value); absent ⇒ every object is a root. ``measurement_cols`` defaults to every column that is not the
    id / parent / qc / provenance column. Rows without an id are skipped (an unstamped object has no stable
    identity to hang on the graph). Changes nothing about ``df``.
    """
    reserved = {id_col, parent_id_col, qc_col} | set(provenance_cols)
    cols = list(df.columns)
    if measurement_cols is None:
        measurement_cols = [c for c in cols if c not in reserved]
    objects = []
    for row in df.to_dict('records'):
        key = row.get(id_col)
        if key is None or (isinstance(key, str) and not key.strip()):
            continue
        parent = row.get(parent_id_col) if parent_id_col else None
        if isinstance(parent, str) and not parent.strip():
            parent = None
        objects.append(BiologicalObject(
            key=str(key),
            entity_type=str(entity_type),
            measurements={c: row.get(c) for c in measurement_cols},
            qc_flags=str(row.get(qc_col, '') or ''),
            provenance={c: row.get(c) for c in provenance_cols},
            parent=(str(parent) if parent is not None else None)))
    return objects


def build_object_graph(objects) -> ObjectGraph:
    """Assemble an :class:`ObjectGraph` from an iterable of `BiologicalObject`s (or several tables' worth,
    already concatenated). Parent/child edges resolve at construction; orphans surface via
    :meth:`ObjectGraph.unrooted`."""
    return ObjectGraph(list(objects))
