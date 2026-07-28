"""**A read-only biological object graph over existing tables — increment 1: the record + the graph.**

Pins the load-bearing behaviour: objects are keyed on the existing ``_pycat_entity_id`` (no parallel id
scheme); a flat table yields a flat graph of roots; a parent/child table yields the tree; an object naming a
parent NOT present lands in the explicit *unrooted* bucket (not silently dropped, not silently rerooted);
and the graph never mutates its source tables or re-runs analysis.
"""
import pandas as pd
import pytest

from pycat.utils.object_graph import (
    BiologicalObject, ObjectGraph, objects_from_table, build_object_graph,
    build_cell_puncta_graph)

pytestmark = pytest.mark.base


def _cells():
    return pd.DataFrame({
        '_pycat_entity_id': ['ds/op_cell/cell/0/1', 'ds/op_cell/cell/0/2'],
        'area': [100.0, 200.0], 'intensity_mean': [10.0, 20.0], 'qc_flags': ['', 'edge'],
    })


def _puncta():
    # two puncta in cell 1, one in cell 2 — parent_id points at the cell's entity id
    return pd.DataFrame({
        '_pycat_entity_id': ['ds/op_p/punctum/0/1/1', 'ds/op_p/punctum/0/1/2', 'ds/op_p/punctum/0/2/1'],
        'parent_entity_id': ['ds/op_cell/cell/0/1', 'ds/op_cell/cell/0/1', 'ds/op_cell/cell/0/2'],
        'area': [5.0, 6.0, 7.0], 'qc_flags': ['', '', ''],
    })


def test_objects_are_keyed_on_the_existing_entity_id_and_carry_measurements():
    objs = objects_from_table(_cells(), 'cell')
    g = build_object_graph(objs)
    assert len(g) == 2
    o = g.get('ds/op_cell/cell/0/1')
    assert o.entity_type == 'cell' and o.measurements['area'] == 100.0
    assert 'intensity_mean' in o.measurements
    assert '_pycat_entity_id' not in o.measurements        # the id column is not a measurement
    assert g.get('ds/op_cell/cell/0/2').qc_flags == 'edge'


def test_a_flat_table_yields_a_flat_graph_of_roots():
    g = build_object_graph(objects_from_table(_cells(), 'cell'))
    assert len(g.roots()) == 2 and g.unrooted() == []
    assert all(g.children_of(o.key) == [] for o in g)


def test_parent_child_edges_resolve_into_a_tree():
    objs = objects_from_table(_cells(), 'cell') + \
           objects_from_table(_puncta(), 'punctum', parent_id_col='parent_entity_id')
    g = build_object_graph(objs)
    assert len(g) == 5
    cell1 = 'ds/op_cell/cell/0/1'
    assert {c.key for c in g.children_of(cell1)} == {'ds/op_p/punctum/0/1/1', 'ds/op_p/punctum/0/1/2'}
    assert g.parent_of('ds/op_p/punctum/0/1/1').key == cell1
    assert {c.key for c in g.children_of('ds/op_cell/cell/0/2')} == {'ds/op_p/punctum/0/2/1'}
    # cells are roots; puncta are not
    assert {r.key for r in g.roots()} == {cell1, 'ds/op_cell/cell/0/2'}


def test_descendants_and_ancestors_walk_the_tree():
    # grandchild chain: cell -> punctum -> sub-object
    objs = [
        BiologicalObject(key='c', entity_type='cell'),
        BiologicalObject(key='p', entity_type='punctum', parent='c'),
        BiologicalObject(key='s', entity_type='subpunctum', parent='p'),
    ]
    g = build_object_graph(objs)
    assert [o.key for o in g.descendants('c')] == ['p', 's']
    assert [o.key for o in g.ancestors('s')] == ['p', 'c']
    assert g.descendants('s') == [] and g.ancestors('c') == []


def test_an_object_naming_a_MISSING_parent_is_unrooted_not_dropped_or_rerooted():
    objs = objects_from_table(_puncta(), 'punctum', parent_id_col='parent_entity_id')  # no cells present
    g = build_object_graph(objs)
    assert len(g) == 3                                      # nothing dropped
    assert {o.key for o in g.unrooted()} == {p.key for p in objs}
    assert g.roots() == []                                 # an orphan is NOT a root
    assert g.parent_of('ds/op_p/punctum/0/1/1') is None    # the named parent isn't in the graph


def test_of_type_and_filter_and_rows_without_an_id_are_skipped():
    cells = _cells()
    cells.loc[len(cells)] = {'_pycat_entity_id': '', 'area': 1.0, 'intensity_mean': 1.0, 'qc_flags': ''}
    g = build_object_graph(
        objects_from_table(cells, 'cell') +
        objects_from_table(_puncta(), 'punctum', parent_id_col='parent_entity_id'))
    assert len(g.of_type('cell')) == 2                     # the id-less row was skipped
    assert len(g.of_type('punctum')) == 3
    assert {o.key for o in g.filter(lambda o: o.measurements.get('area', 0) >= 100)} == \
           {'ds/op_cell/cell/0/1', 'ds/op_cell/cell/0/2'}


def test_the_graph_does_not_mutate_the_source_table():
    df = _cells()
    before = df.copy()
    build_object_graph(objects_from_table(df, 'cell'))
    pd.testing.assert_frame_equal(df, before)


# ── increment 2: the schema-specific cell→puncta join ────────────────────────────────────────────

def _stamped_cells_and_puncta():
    """Real cell_df + puncta_df stamped the way PyCAT stamps them: cells keyed by their own `label`, puncta
    naming their parent cell by `'cell label'` (the parent-column spelling puncta use)."""
    from pycat.utils.entity_ref import stamp_entity_ids
    cells = pd.DataFrame({'label': [1, 2], 'area': [100.0, 200.0]})
    cells = stamp_entity_ids(cells, entity_type='cell', source_path='x.tif', operation_id='cell_analysis')
    # two puncta in cell 1, one in cell 2, and one orphan naming a cell (9) that isn't in cell_df
    puncta = pd.DataFrame({'label': [1, 2, 1, 1], 'cell label': [1, 1, 2, 9], 'area': [5.0, 6.0, 7.0, 8.0]})
    puncta = stamp_entity_ids(puncta, entity_type='punctum', source_path='x.tif', operation_id='puncta_analysis')
    return cells, puncta


def test_cell_puncta_join_links_each_punctum_to_its_cell():
    cells, puncta = _stamped_cells_and_puncta()
    g = build_cell_puncta_graph(cells, puncta)
    assert len(g.of_type('cell')) == 2 and len(g.of_type('punctum')) == 4
    cell1 = next(c for c in g.of_type('cell') if c.measurements['label'] == 1)
    cell2 = next(c for c in g.of_type('cell') if c.measurements['label'] == 2)
    # the graph reproduces the segmentation nesting: cell 1 has two puncta, cell 2 has one
    assert len(g.children_of(cell1.key)) == 2
    assert len(g.children_of(cell2.key)) == 1
    # a punctum's ancestor is its actual cell object (keyed on the existing _pycat_entity_id — no parallel id)
    punctum = g.children_of(cell1.key)[0]
    assert g.parent_of(punctum.key) is cell1


def test_cell_puncta_join_surfaces_an_orphan_punctum_not_silently_roots_it():
    cells, puncta = _stamped_cells_and_puncta()
    g = build_cell_puncta_graph(cells, puncta)
    # the cells are the roots; the punctum naming absent cell 9 is UNROOTED, not a root
    assert {c.measurements['label'] for c in g.roots()} == {1, 2}
    orphans = g.unrooted()
    assert len(orphans) == 1 and orphans[0].measurements['cell label'] == 9


def test_cell_puncta_join_changes_neither_table():
    cells, puncta = _stamped_cells_and_puncta()
    cb, pb = cells.copy(), puncta.copy()
    build_cell_puncta_graph(cells, puncta)
    pd.testing.assert_frame_equal(cells, cb)
    pd.testing.assert_frame_equal(puncta, pb)


def test_cell_puncta_join_with_no_parent_column_yields_a_flat_graph():
    from pycat.utils.entity_ref import stamp_entity_ids
    cells, _ = _stamped_cells_and_puncta()
    flat = pd.DataFrame({'label': [1, 2], 'area': [5.0, 6.0]})       # no 'cell label' column
    flat = stamp_entity_ids(flat, entity_type='punctum', source_path='x.tif', operation_id='puncta_analysis')
    g = build_cell_puncta_graph(cells, flat)
    assert len(g.of_type('punctum')) == 2
    assert all(g.parent_of(p.key) is None for p in g.of_type('punctum'))   # unlinked → roots, not orphans
