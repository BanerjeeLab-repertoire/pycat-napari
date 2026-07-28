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

The core (`BiologicalObject` / `ObjectGraph` / `objects_from_table` / `build_object_graph`) is **generic** over
"objects that carry their own id and (optionally) their parent's id" (increment 1). The **schema-specific**
join that derives a punctum's parent-cell id from the cell-labelled-puncta convention — so PyCAT's real
``cell_df`` + ``puncta_df`` assemble into one graph — is :func:`build_cell_puncta_graph` at the bottom
(increment 2). The graph then earns its keep: :meth:`ObjectGraph.aggregate_children` /
:meth:`ObjectGraph.child_summary` roll a child measurement up to its parent (puncta-per-cell counts, total /
mean punctum area), the first step of the state-vector direction (increment 3). Linked navigation is later still.
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

    # ── aggregation: a parent's state, rolled up from its children (increment 3) ───────────────────
    def aggregate_children(self, key, measurement=None, reduce='count'):
        """Reduce a ``measurement`` over the DIRECT children of ``key``. ``reduce`` is ``'count'`` (children,
        ``measurement`` ignored), or ``'sum'`` / ``'mean'`` / ``'min'`` / ``'max'`` over the numeric values of
        ``measurement`` — the graph-native "roll the children up to the parent" (e.g. total punctum area in a
        cell). Non-numeric and NaN values are skipped; ``count`` of no children is ``0``, and every other reducer
        over no numeric values is ``None`` (nothing to reduce) — never a guessed zero."""
        children = self.children_of(key)
        if reduce == 'count':
            return len(children)
        vals = [c.measurements.get(measurement) for c in children]
        vals = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v]
        if not vals:
            return None
        if reduce == 'sum':
            return sum(vals)
        if reduce == 'mean':
            return sum(vals) / len(vals)
        if reduce == 'min':
            return min(vals)
        if reduce == 'max':
            return max(vals)
        raise ValueError(f"unknown reduce {reduce!r} (count/sum/mean/min/max)")

    def child_summary(self, parent_type, *, measurements=(), reducers=('sum', 'mean')):
        """One row per parent object of ``parent_type`` — its ``key``, ``entity_type``, own measurements, the
        child count (``n_children``), and, for each named child ``measurement`` × ``reducer``, a
        ``f'{measurement}_{reducer}'`` field. The per-parent roll-up over the graph (e.g. puncta-per-cell counts
        and total / mean punctum area from a cell→puncta graph), general over any parent/child types. Returns a
        list of plain dicts (the caller may build a DataFrame); the graph and its source tables are untouched."""
        rows = []
        for p in self.of_type(parent_type):
            row = {'key': p.key, 'entity_type': p.entity_type,
                   'n_children': self.aggregate_children(p.key)}
            row.update(p.measurements)
            for m in measurements:
                for red in reducers:
                    row[f'{m}_{red}'] = self.aggregate_children(p.key, m, red)
            rows.append(row)
        return rows


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


# ── the schema-specific cell→puncta join (increment 2) ───────────────────────────────────────────
#
# The generic core above links objects whose parent's ID is already a column. PyCAT's real puncta table does
# NOT carry that: a punctum knows the LABEL of the cell it was segmented within (`make_entity_id` folds that
# label into the punctum's own id so per-cell labels don't collide), but not that cell's stable id. This
# derives it — the one place that knows the cell-labelled-puncta convention.

def build_cell_puncta_graph(cell_df, puncta_df, *, cell_label_col='label', frame_col=None) -> ObjectGraph:
    """Assemble a cell→puncta :class:`ObjectGraph` from PyCAT's real ``cell_df`` + ``puncta_df`` (increment 2).

    A punctum row names the cell it lives in by that cell's LABEL (in one of the parent-column spellings the
    codebase writes — ``'cell label'`` for puncta; see :data:`entity_ref._PARENT_COLUMNS`), but not the cell's
    stable id. This matches that label to the cell's own ``cell_label_col`` in ``cell_df`` to recover the parent
    cell's ``_pycat_entity_id``, so the two real tables assemble into ONE graph: cells as roots, their puncta as
    children. Reuses the existing ``_pycat_entity_id`` on both tables (invents no id) and changes neither table.

    A punctum whose named cell is NOT present in ``cell_df`` surfaces as **unrooted** — the explicit-orphan
    contract of the generic core, never silently rooted. ``frame_col``, when given, keys the join per frame too
    (a multi-frame table where a cell label recurs across frames as different cells); omit it for a single 2-D
    image, where every row shares the one (``None``) frame.
    """
    from pycat.utils.entity_ref import ENTITY_ID_COLUMN, _PARENT_COLUMNS
    id_col = ENTITY_ID_COLUMN

    def _frame(row):
        return row.get(frame_col) if frame_col else None

    cell_index = {}                                   # (frame, cell label) → that cell's entity id
    for row in cell_df.to_dict('records'):
        cid = row.get(id_col)
        if cid is None or (isinstance(cid, str) and not cid.strip()):
            continue
        cell_index[(_frame(row), row.get(cell_label_col))] = str(cid)

    cells = objects_from_table(cell_df, 'cell', id_col=id_col)

    parent_col = next((c for c in _PARENT_COLUMNS if c in getattr(puncta_df, 'columns', ())), None)
    if parent_col is None:                            # no parent-cell column → puncta cannot be linked (flat)
        return build_object_graph(cells + objects_from_table(puncta_df, 'punctum', id_col=id_col))

    def _parent(row):
        plabel = row.get(parent_col)
        pid = cell_index.get((_frame(row), plabel))
        if pid is not None:
            return pid                                # the real parent-cell entity id → a child edge
        if plabel is not None and str(plabel).strip():
            return f'cell-label={plabel}'             # named a cell absent from cell_df → surfaces as unrooted
        return None                                   # no cell named → a root

    derived = '__pycat_parent_entity_id__'
    pdf = puncta_df.copy()
    pdf[derived] = [_parent(row) for row in puncta_df.to_dict('records')]
    puncta = objects_from_table(pdf, 'punctum', id_col=id_col, parent_id_col=derived)
    return build_object_graph(cells + puncta)
