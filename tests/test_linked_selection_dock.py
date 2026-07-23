"""**One dock that answers "what did I just click?" — instead of a pile of layers.**

Clicking a point added an image layer named `object <N>` holding that object's crop. It is reused for
the *same* object, so it is one layer per **distinct object clicked**: explore a scatter for a minute
and the layer list fills with crops to clean up by hand. And the name is keyed on `object_id` alone,
so `object 7` from two different masks **collide onto one layer** — clicking one segmentation
silently overwrites the other's crop.

These tests are QtWidgets-only: `QApplication` runs fine offscreen. It is `napari.Viewer` that cannot
(no GL context), so the dock's *contents* are tested here and its *docking* is not — that needs a
real viewer window. Flagged rather than faked.
"""

# Third party imports
import numpy as np
import pandas as pd
import pytest


pytestmark = pytest.mark.base


@pytest.fixture(scope='module')
def qapp():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _LazyStack:
    """A lazy stack with the real refusing `__array__` — the contract every PyCAT wrapper has."""

    def __init__(self, arr):
        self._a = arr
        self.shape = arr.shape
        self.ndim = arr.ndim
        self.dtype = np.dtype('float32')
        self.full_reads = 0

    def __getitem__(self, key):
        return self._a[key]

    def __len__(self):
        return self.shape[0]

    def __array__(self, dtype=None):
        self.full_reads += 1
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)


class _Layer:
    def __init__(self, name, data, layer_id=None):
        from pycat.utils.layer_tags import tag_layer
        self.name = name
        self.data = data
        self.metadata = {}
        if layer_id:
            self.metadata['pycat_layer_id'] = layer_id
        tag_layer(self, 'role', 'image', source='inferred')


class _Layers(list):
    def __init__(self, items=()):
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


class _Viewer:
    class _Dims:
        ndim = 3
        point = (0, 0, 0)
        current_step = (0, 0, 0)

    class _Cam:
        center = (0.0, 0.0, 0.0)

    def __init__(self, layers=()):
        self.layers = _Layers(layers)
        self.dims = self._Dims()
        self.camera = self._Cam()
        self.added = []

    def _add(self, data, **kw):
        layer = _Layer(kw.get('name'), data)
        self.added.append(kw.get('name'))
        self.layers.append(layer)
        return layer

    def add_shapes(self, data, **kw):
        return self._add(data, **kw)

    def add_points(self, data, **kw):
        return self._add(data, **kw)

    def add_image(self, data, **kw):
        return self._add(data, **kw)


def _ref(object_id=3, frame=2):
    from pycat.utils.object_ref import ObjectRef
    return ObjectRef(object_id=object_id, frame=frame, bbox=(10, 10, 18, 18),
                     source_path='C:/data/a.tif', parent_id=1,
                     tags={'target': 'cell'}, source_layer_id='lyr-abcdef12',
                     entity_id='C:/data/a.tif/cell_analysis/cell/2/3')


def _widget(qapp, viewer=None, cm=None):
    from pycat.ui.linked_selection_dock import LinkedSelectionWidget
    return LinkedSelectionWidget(viewer=viewer, central_manager=cm)


def _movie_viewer():
    stack = _LazyStack(np.random.default_rng(0).random((6, 64, 64)).astype(np.float32))
    return _Viewer([_Layer('movie', stack, layer_id='lyr-abcdef12')]), stack


def test_showing_an_object_does_NOT_add_a_layer(qapp):
    """**The whole point.** The dock updates in place; the layer list is left alone."""
    viewer, _stack = _movie_viewer()
    widget = _widget(qapp, viewer)

    before = len(viewer.layers)
    widget.show_ref(_ref())

    assert viewer.added == [], f"showing an object added layers: {viewer.added}"
    assert len(viewer.layers) == before


def test_the_preview_crop_does_NOT_materialise_the_acquisition(qapp):
    """It goes through increment 1's slice-before-materialize `crop_for_ref`, so previewing an
    object in a 6-frame movie reads one plane — not the movie."""
    viewer, stack = _movie_viewer()
    widget = _widget(qapp, viewer)

    widget.show_ref(_ref())

    assert stack.full_reads == 0, "the preview tried to read the whole stack"
    pixmap = widget.preview.pixmap()
    assert pixmap is not None and not pixmap.isNull(), "no crop was shown"


def test_the_dock_says_WHICH_object_it_is_showing(qapp):
    """A crop with no context is a picture of some pixels. The facts are what make it an answer."""
    viewer, _stack = _movie_viewer()
    widget = _widget(qapp, viewer)

    widget.show_ref(_ref(object_id=3, frame=2))

    assert widget.title.text() == 'cell 3'
    assert widget._fact_labels['Dataset'].text() == 'C:/data/a.tif'
    assert widget._fact_labels['Frame'].text() == '2'
    assert widget._fact_labels['Parent'].text() == '1'


def test_a_ref_that_cannot_be_shown_says_WHY(qapp):
    """*"Nothing happened" is the worst possible answer to a click.*"""
    from pycat.utils.object_ref import ObjectRef

    widget = _widget(qapp, _Viewer())
    widget.show_ref(ObjectRef(object_id=9, bbox=(1, 1, 4, 4), source_path='/gone/missing.tif'))

    pixmap = widget.preview.pixmap()
    assert pixmap is None or pixmap.isNull()
    assert widget.preview.text(), "the dock went blank instead of saying why"


def test_a_BY_POSITION_table_is_flagged_in_the_dock(qapp):
    """Increment 2's linkability, where the user can act on it: a ref with no stable name came from
    a table that sorting will silently mislink."""
    from pycat.utils.object_ref import ObjectRef

    viewer, _stack = _movie_viewer()
    widget = _widget(qapp, viewer)

    widget.show_ref(_ref())                                   # has an entity_id
    assert widget.linkability.text() == ''

    widget.show_ref(ObjectRef(object_id=1, frame=0, bbox=(0, 0, 4, 4)))   # legacy
    assert 'row position' in widget.linkability.text()


def test_REVEAL_is_the_only_thing_that_moves_the_view(qapp):
    """Clicking a point marks the object; going to it is a separate intention. This is the button
    that means "take me there" — and the double-click that does the same."""
    viewer, _stack = _movie_viewer()
    widget = _widget(qapp, viewer)

    widget.show_ref(_ref(frame=2))
    assert viewer.dims.current_step == (0, 0, 0), "showing an object moved the view"

    widget.reveal()
    assert viewer.dims.current_step[0] == 2, "Reveal did not go to the object's frame"


def test_PIN_keeps_the_dock_on_one_object_while_you_click_around(qapp):
    from pycat.utils.selection_service import Selection

    viewer, _stack = _movie_viewer()
    widget = _widget(qapp, viewer)
    widget.show_ref(_ref(object_id=3))

    widget.pin_button.setChecked(True)
    widget._on_selection(Selection(entity_ids=('other',), primary_id='other'))

    assert widget.title.text() == 'cell 3', "a pinned dock followed the selection anyway"


def test_the_dock_subscribes_to_the_DEFERRED_half_of_the_dispatcher(qapp):
    """Reading pixels is the expensive part, so a drag across a scatter must update this once — on
    the point the user stopped on — not once per point crossed (increment 4's debounce)."""
    from pycat.utils.selection_service import SelectionService

    flushes = []
    service = SelectionService(defer=lambda fn: fn(), debounce=lambda fn: flushes.append(fn))
    widget = _widget(qapp, _Viewer())
    widget.subscribe(service)

    assert 'linked_selection_dock' in service._deferred_subscribers, (
        "the dock subscribed to the CHEAP half — every hover would read pixels")
    assert 'linked_selection_dock' not in service._subscribers


def test_the_dock_is_usable_with_NO_viewer(qapp):
    """A batch/headless plot has no viewer window. The dock must not require one to exist."""
    widget = _widget(qapp, viewer=None)
    widget.show_ref(_ref())
    assert not widget.reveal_button.isEnabled(), "Reveal is meaningless with no viewer"
    widget.reveal()          # must not raise


def test_the_dock_is_a_RECEIVE_ONLY_SelectionView_that_unsubscribes_on_close(qapp):
    """Gap 5: the dock satisfies the SelectionView protocol (view_id / apply_selection / close). It is
    RECEIVE-ONLY — it renders a selection but never emits — so applying one emits no command; and
    close() unsubscribes, which the outer wrapper's close used to miss."""
    from pycat.utils.selection_service import (
        SelectionService, SelectionState, SelectionView)

    service = SelectionService(defer=lambda fn: fn(), debounce=lambda fn: fn())
    widget = _widget(qapp, _Viewer())
    widget.subscribe(service)

    assert isinstance(widget, SelectionView)
    assert widget.view_id == 'linked_selection_dock'

    emitted = []
    service.subscribe('probe', lambda st: emitted.append(st.source_view))
    widget.apply_selection(SelectionState(selected=frozenset({'no/such/entity/0/0'}),
                                          primary='no/such/entity/0/0'))
    assert emitted == [], "the dock emitted a command — it must be receive-only"

    widget.close()
    assert 'linked_selection_dock' not in service._deferred_subscribers, (
        "close() did not unsubscribe — a closed dock would still be driven")
