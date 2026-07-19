"""**Switching position rebinds the layer to the new scene — and marks what it left behind.**

QtWidgets-only (no napari GL): the switcher's `switch_to` is exercised against fake layers + a fake
reader. `run_with_progress` runs synchronously when there is no running Qt event loop, so the off-thread
switch path is testable here. What is asserted is the contract, not the pixels: the layer's data becomes
a `_SceneStack` pinned to the NEW scene, the layer is re-tagged, per-scene metadata is re-read, and a
derived layer is stamped with the position it was computed on so it cannot masquerade as current.
"""

import numpy as np
import pytest

pytestmark = pytest.mark.core


@pytest.fixture(scope='module')
def qapp():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Dims:
    def __init__(self, T=5, Z=1, C=1, Y=8, X=6):
        self.T, self.Z, self.C, self.Y, self.X = T, Z, C, Y, X


class _FakeReader:
    """A multi-scene reader the switcher can drive: names, a settable scene, per-scene dims."""
    def __init__(self):
        self.scenes = ['P0', 'P1']
        self.current_scene = 'P0'
        self.set_scene_calls = []
        self._dims = {'P0': _Dims(T=5), 'P1': _Dims(T=9)}

        class _DT:
            name = 'uint16'
        self.dtype = _DT()

    def set_scene(self, scene):
        self.set_scene_calls.append(scene)
        self.current_scene = scene

    @property
    def dims(self):
        return self._dims[self.current_scene]


class _Src:
    def __init__(self, reader):
        self.readers = [(reader, object())]      # the loader retains (reader, dask) tuples
        self.file_path = 'C:/data/multi.czi'


class _WrapperWithChannel:
    _ci = 1


class _Layer:
    def __init__(self, name, reader=None, scene=None, data=None):
        from pycat.utils.layer_tags import tag_layer
        self.name = name
        self.metadata = {}
        self.data = data
        if reader is not None:
            self.metadata['pycat_image_source'] = _Src(reader)
        if scene is not None:
            tag_layer(self, 'scene', scene, source='from_metadata')


class _Layers(list):
    pass


class _Viewer:
    def __init__(self, layers):
        self.layers = _Layers(layers)
        self.window = None


class _ADC:
    def __init__(self):
        self.update_metadata_calls = []

    def update_metadata(self, image):
        self.update_metadata_calls.append(getattr(image, 'current_scene', None))


class _CM:
    def __init__(self):
        self.active_data_class = _ADC()


def _widget(qapp, viewer, cm):
    from pycat.ui.scene_switcher import SceneSwitcherWidget
    return SceneSwitcherWidget(viewer=viewer, central_manager=cm)


def test_the_dropdown_lists_the_positions_and_marks_the_current_one(qapp):
    reader = _FakeReader()
    scene_layer = _Layer('multi.czi C0 Stack [P0]', reader=reader, scene='P0',
                         data=_WrapperWithChannel())
    widget = _widget(qapp, _Viewer([scene_layer]), _CM())

    assert [widget.combo.itemText(i) for i in range(widget.combo.count())] == ['P0', 'P1']
    assert widget.combo.currentText() == 'P0', "the dropdown must show the loaded position"


def test_switching_rebinds_the_layer_to_a_SceneStack_of_the_NEW_scene(qapp):
    from pycat.file_io.lazy_sources import _SceneStack
    from pycat.file_io.scenes import scene_of

    reader = _FakeReader()
    scene_layer = _Layer('multi.czi C0 Stack [P0]', reader=reader, scene='P0',
                         data=_WrapperWithChannel())
    cm = _CM()
    widget = _widget(qapp, _Viewer([scene_layer]), cm)

    widget.switch_to('P1')

    assert isinstance(scene_layer.data, _SceneStack), "the layer was not rebound to a scene wrapper"
    assert scene_layer.data.scene == 'P1', "the new data is not pinned to the target position"
    assert scene_layer.data.shape == (9, 8, 6), "dims must come from the NEW scene (T=9), not the old"
    assert scene_layer.data._ci == 1, "the layer's channel must be preserved across the switch"
    assert scene_of(scene_layer) == 'P1', "the layer was not re-tagged with the new position"
    assert cm.active_data_class.update_metadata_calls, "per-scene calibration was not re-read"


def test_a_derived_layer_is_STAMPED_with_the_position_it_was_computed_on(qapp):
    from pycat.utils.layer_tags import get_tag

    reader = _FakeReader()
    scene_layer = _Layer('multi.czi C0 Stack [P0]', reader=reader, scene='P0',
                         data=_WrapperWithChannel())
    derived = _Layer('Cellpose labels', data=np.zeros((8, 6), np.uint8))   # no scene tag → derived
    widget = _widget(qapp, _Viewer([scene_layer, derived]), _CM())

    widget.switch_to('P1')

    assert get_tag(derived, 'computed_on_scene') == 'P0', (
        "a layer computed on the previous position must be tagged with it, so it cannot masquerade as "
        "belonging to the new position")


def test_with_no_multiposition_layer_the_switcher_is_idle(qapp):
    plain = _Layer('some image', data=np.zeros((8, 6), np.float32))        # no scene tag
    widget = _widget(qapp, _Viewer([plain]), _CM())
    assert widget.combo.count() == 0
    assert not widget.combo.isEnabled()
    widget.switch_to('P1')          # must be a harmless no-op, not a crash
