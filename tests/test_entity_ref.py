"""**A plot point should mean an OBJECT, not a row number.**

Today a plot's points and a table's rows are matched by **position**. Sort the table, filter it, and
the correspondence is silently wrong — every point still highlights something, and nothing looks
broken. `EntityKey` is the name that survives that.

The name is built from facts that already exist rather than swept into 89 sites: the file, the
operation, and the label. See `pycat.utils.entity_ref`.

pycat imports live inside the test bodies (`conftest.py`'s `pytest_ignore_collect` drops modules
whose module-scope imports name `pycat.file_io` / other GUI-bound packages when the stack is absent).
"""

# Third party imports
import numpy as np
import pandas as pd
import pytest


pytestmark = pytest.mark.base


def _cells_table():
    return pd.DataFrame({
        'label': [1, 2, 3],
        'area': [10.0, 20.0, 30.0],
        'bbox_y0': [0, 5, 9], 'bbox_x0': [0, 5, 9],
        'bbox_y1': [4, 8, 12], 'bbox_x1': [4, 8, 12],
    })


class _Layer:
    def __init__(self, layer_id=None, name='layer'):
        from pycat.utils.layer_tags import tag_layer
        self.name = name
        self.metadata = {}
        self.selected_label = None
        self.show_selected_label = False
        tag_layer(self, 'role', 'labels', source='inferred')
        if layer_id:
            self.metadata['pycat_layer_id'] = layer_id


class _Layers(list):
    """A layer list that also answers by NAME, as napari's does."""

    def __init__(self, items):
        super().__init__(items)
        self.selection = set()

    def __contains__(self, key):
        if isinstance(key, str):
            return any(getattr(l, 'name', None) == key for l in self)
        return list.__contains__(self, key)

    def __getitem__(self, key):
        if isinstance(key, str):
            for layer in self:
                if getattr(layer, 'name', None) == key:
                    return layer
            raise KeyError(key)
        return list.__getitem__(self, key)

    def remove(self, key):
        list.remove(self, self[key] if isinstance(key, str) else key)


class _Overlay:
    def __init__(self, data, name=None, **kw):
        self.data = data
        self.name = name
        self.visible = True
        self.metadata = {}
        for k, v in kw.items():
            setattr(self, k, v)


class _Viewer:
    class _Dims:
        point = ()
        current_step = (0, 0, 0)
        ndim = 2

    class _Cam:
        center = (0.0, 0.0, 0.0)

    def __init__(self, layers):
        self.layers = _Layers(layers)
        self.dims = self._Dims()
        self.camera = self._Cam()

    def _add(self, data, **kw):
        layer = _Overlay(data, **kw)
        self.layers.append(layer)
        return layer

    def add_shapes(self, data, **kw):
        return self._add(data, **kw)

    def add_points(self, data, **kw):
        return self._add(data, **kw)


def test_the_key_is_the_SAME_object_however_the_table_is_SORTED_or_FILTERED():
    """**The property the whole increment exists for.**

    Row position is not identity: sort the table and row 0 is a different object, while every plot
    point still points at row 0. The key travels with the row.
    """
    from pycat.utils.entity_ref import ENTITY_ID_COLUMN, stamp_entity_ids

    df = stamp_entity_ids(_cells_table(), entity_type='cell',
                          source_path='C:/data/a.tif', operation_id='cell_analysis', frame=0)

    by_label = dict(zip(df['label'], df[ENTITY_ID_COLUMN]))

    shuffled = df.sort_values('area', ascending=False).reset_index(drop=True)
    filtered = df[df['area'] > 15].reset_index(drop=True)

    assert dict(zip(shuffled['label'], shuffled[ENTITY_ID_COLUMN])) == by_label, (
        "sorting the table changed which object a row names")
    for label, key in zip(filtered['label'], filtered[ENTITY_ID_COLUMN]):
        assert key == by_label[label], "filtering the table changed an object's name"

    # Row POSITION, by contrast, means nothing after a sort — which is the bug.
    assert shuffled.loc[0, 'label'] != df.loc[0, 'label']


def test_puncta_keys_do_NOT_COLLIDE_across_cells():
    """**The spec's `f"{frame}/{label}"` is wrong for the table people brush most.**

    `puncta_analysis_func` calls `sk.measure.label(...)` inside its per-cell loop, so punctum labels
    **restart at 1 in every cell**. Punctum 1 of cell 1 and punctum 1 of cell 2 are different
    objects with the same label — keyed on frame/label alone they would be the same entity, which is
    exactly the guarantee identity is supposed to provide.
    """
    from pycat.utils.entity_ref import ENTITY_ID_COLUMN, stamp_entity_ids

    # Note the column is 'cell label' WITH A SPACE — what the real puncta table emits.
    puncta = pd.DataFrame({'label': [1, 2, 1, 2], 'cell label': [1, 1, 2, 2]})
    puncta = stamp_entity_ids(puncta, entity_type='punctum', source_path='C:/data/a.tif',
                             operation_id='puncta_analysis', frame=0)

    keys = list(puncta[ENTITY_ID_COLUMN])
    assert len(set(keys)) == 4, (
        f"two different puncta share a name: {keys}. The parent cell has to be part of the key "
        f"because punctum labels restart per cell.")


def test_a_cell_and_a_punctum_with_the_SAME_label_are_DIFFERENT_entities():
    """Label 1 exists in every table. The operation and the type are what separate them."""
    from pycat.utils.entity_ref import entity_id_column

    cell = entity_id_column('a.tif', 'cell_analysis', 'cell', 0, 1)
    punctum = entity_id_column('a.tif', 'puncta_analysis', 'punctum', 0, 1, 1)
    assert cell != punctum


def test_an_UNSTAMPABLE_table_is_left_ALONE_and_FLAGGED_not_half_marked():
    """`measure_region_props` lets the user pick which properties to measure, so `label` may not be
    there at all. A table with identity on *some* rows is worse than one with none — it looks
    linked. The honest outcome is: untouched, and visibly by-position."""
    from pycat.utils.entity_ref import (LINKED_BY_IDENTITY, LINKED_BY_POSITION, has_entity_ids,
                                        linkability_of, stamp_entity_ids)

    no_label = pd.DataFrame({'area': [1.0, 2.0]})
    out = stamp_entity_ids(no_label, entity_type='mask_object')

    assert list(out.columns) == ['area'], "a table with no label column was partially stamped"
    assert not has_entity_ids(out)
    assert linkability_of(out) == LINKED_BY_POSITION

    stamped = stamp_entity_ids(_cells_table(), entity_type='cell', source_path='a.tif',
                               operation_id='cell_analysis')
    assert linkability_of(stamped) == LINKED_BY_IDENTITY


def test_frame_column_gives_identity_PER_ROW_not_one_reference_frame():
    """A multi-frame table (the same label recurring across frames as DIFFERENT entities) must derive its
    frame per row from ``frame_column`` — stamping the whole series with one scalar frame collapses
    distinct entities onto one id."""
    from pycat.utils.entity_ref import ENTITY_ID_COLUMN, stamp_entity_ids

    df = pd.DataFrame({'label': [1, 1, 2, 2], 'frame': [0, 1, 0, 1]})
    out = stamp_entity_ids(df.copy(), entity_type='bead', source_path='m.tif',
                           operation_id='bead_detect', frame_column='frame')
    ids = list(out[ENTITY_ID_COLUMN])
    # label 1 at frame 0 and label 1 at frame 1 are DIFFERENT entities → different ids
    assert ids[0] != ids[1], "same label in different frames got the same id — frame is not per-row"
    assert ids[2] != ids[3]
    assert len(set(ids)) == 4, "four (label, frame) pairs must yield four distinct entity ids"


def test_without_frame_column_the_scalar_frame_is_used_unchanged():
    """Back-compat: a single-frame table with no frame_column stamps every row with the scalar frame,
    exactly as before."""
    from pycat.utils.entity_ref import ENTITY_ID_COLUMN, stamp_entity_ids

    df = pd.DataFrame({'label': [1, 2, 3]})
    a = stamp_entity_ids(df.copy(), entity_type='cell', source_path='a.tif', frame=0)
    b = stamp_entity_ids(df.copy(), entity_type='cell', source_path='a.tif', frame=0)
    assert list(a[ENTITY_ID_COLUMN]) == list(b[ENTITY_ID_COLUMN])   # deterministic, unchanged
    assert len(set(a[ENTITY_ID_COLUMN])) == 3                        # three labels → three ids


def test_a_ref_built_from_a_stamped_table_resolves_to_ITS_OWN_layer():
    """**This closes the loop increment 1 opened.** Increment 1 made `resolve_in_viewer` honour
    `source_layer_id`; nothing filled it, so every ref still took the announced guess. Now the table
    carries the layer and the ref inherits it.

    Extends increment 1's wrong-target guard: two masks open, and the object resolves to the one it
    actually came from.
    """
    from pycat.utils.entity_ref import attach_layer_id, stamp_entity_ids
    from pycat.utils.object_ref import refs_from_dataframe, resolve_in_viewer

    mask_a = _Layer('aaaa1111', 'Segmentation A')
    mask_b = _Layer('bbbb2222', 'Labeled Cell Mask')

    df = stamp_entity_ids(_cells_table(), entity_type='cell', source_path='C:/data/a.tif',
                          operation_id='cell_analysis', frame=0)
    df = attach_layer_id(df, mask_b)                     # the objects live in B

    refs = refs_from_dataframe(df, source_path='C:/data/a.tif')
    assert refs[1].source_layer_id == 'bbbb2222', "the ref did not inherit the table's layer"

    viewer = _Viewer([mask_a, mask_b])                   # A is FIRST — the old code would pick it
    assert resolve_in_viewer(refs[1], viewer, centre=False) is True
    assert viewer.layers.selection == {mask_b}, (
        "the cell did not resolve to the layer it came from — an unrelated segmentation was picked")
    # The highlight is an overlay; no layer's paint state is hijacked. See `selection_overlay`.
    assert mask_a.selected_label is None and mask_b.selected_label is None
    assert 'Selection' in viewer.layers, "no selection overlay was drawn"


def test_attach_layer_id_is_a_SECOND_step_because_the_layer_is_born_LAST():
    """`run_cell_analysis_func` builds the table and only THEN calls `add_labels` — at stamp time
    the layer does not exist. A table stamped without one is still valid; it just has no layer yet."""
    from pycat.utils.entity_ref import LAYER_ID_COLUMN, attach_layer_id, stamp_entity_ids

    df = stamp_entity_ids(_cells_table(), entity_type='cell', source_path='a.tif',
                          operation_id='cell_analysis')
    assert LAYER_ID_COLUMN in df.columns
    assert df[LAYER_ID_COLUMN].isna().all(), "a layer id appeared before any layer existed"

    df = attach_layer_id(df, _Layer('late-layer'))
    assert (df[LAYER_ID_COLUMN] == 'late-layer').all()


def test_attach_layer_id_REFUSES_a_table_that_was_never_stamped():
    """Half-identity is the state to avoid: a layer id with no entity id names nothing."""
    from pycat.utils.entity_ref import LAYER_ID_COLUMN, attach_layer_id

    plain = pd.DataFrame({'label': [1, 2]})
    out = attach_layer_id(plain, _Layer('some-layer'))
    assert LAYER_ID_COLUMN not in out.columns


def test_the_ObjectRef_compat_contract_is_UNCHANGED():
    """**Additive only.** `ObjectRef` is the currency of the validated brushing path; a foundation
    that breaks it is not a foundation."""
    from pycat.utils.entity_ref import EntityKey, EntityLocation, EntityRef
    from pycat.utils.object_ref import ObjectRef

    plain = ObjectRef(object_id=1, frame=0, bbox=(0, 0, 4, 4), source_path='x.tif')
    assert plain.source_layer_id is None

    entity = EntityRef(
        key=EntityKey(dataset_id='x.tif', operation_id='cell_analysis',
                      entity_type='cell', entity_id='0/7'),
        location=EntityLocation(t=0, bbox_yx=(1, 2, 3, 4), labels_layer_id='lyr',
                                source_path='x.tif'))

    ref = entity.as_object_ref()
    assert isinstance(ref, ObjectRef)
    assert (ref.object_id, ref.frame, ref.bbox) == (7, 0, (1, 2, 3, 4))
    assert ref.source_layer_id == 'lyr'
    assert ref.tags == {'op': 'cell_analysis', 'target': 'cell'}

    # ...and back again, losing nothing that was there.
    round_trip = EntityRef.from_object_ref(ref)
    assert round_trip.key.operation_id == 'cell_analysis'
    assert round_trip.key.entity_type == 'cell'
    assert round_trip.location.labels_layer_id == 'lyr'


def test_a_LEGACY_ref_says_UNKNOWN_rather_than_inventing_a_dataset():
    """A ref that never knew its dataset must not be given a plausible one — an invented id
    collides with a real one, which is worse than admitting ignorance."""
    from pycat.utils.entity_ref import UNKNOWN, EntityRef
    from pycat.utils.object_ref import ObjectRef

    lifted = EntityRef.from_object_ref(ObjectRef(object_id=1))
    assert lifted.key.dataset_id == UNKNOWN
    assert lifted.key.operation_id == UNKNOWN
    assert lifted.key.entity_type == UNKNOWN


def test_the_key_is_HASHABLE_so_two_views_can_agree_on_a_selection():
    """Frozen and hashable: this is what a set/dict is keyed on when a plot and a table have to
    mean the same object."""
    from pycat.utils.entity_ref import EntityKey

    a = EntityKey('a.tif', 'cell_analysis', 'cell', '0/1')
    b = EntityKey('a.tif', 'cell_analysis', 'cell', '0/1')
    c = EntityKey('a.tif', 'cell_analysis', 'cell', '0/2')

    assert a == b and hash(a) == hash(b)
    assert len({a, b, c}) == 2


def test_identity_NEVER_costs_the_user_their_numbers():
    """A results table is not a convenience; identity is. If stamping cannot work, the measurements
    still come back."""
    from pycat.utils.entity_ref import stamp_entity_ids

    class _Hostile:
        columns = ('label',)

        def __getitem__(self, k):
            raise RuntimeError("this table refuses to be read")

    out = stamp_entity_ids(_Hostile(), entity_type='cell')
    assert out is not None      # returned, not raised
