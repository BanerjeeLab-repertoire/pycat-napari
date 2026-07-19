"""**The multi-scene data layer: enumerate positions, build one lazily, tag which one it is, and
re-read calibration PER scene.**

Qt-free helpers (`pycat.file_io.scenes`) plus the one behaviour change in `update_metadata`: a
multi-position file must read the **currently selected** scene's pixel size, not a fixed scene 0 —
otherwise a position switch silently mis-scales everything downstream.
"""

import numpy as np
import pytest

pytestmark = pytest.mark.core

from pycat.file_io import scenes as S


class _Dims:
    def __init__(self, T=1, Z=1, C=1, Y=8, X=6):
        self.T, self.Z, self.C, self.Y, self.X = T, Z, C, Y, X


class _FakeImage:
    """A minimal multi-scene reader: names, a settable current scene, and per-scene dims."""

    def __init__(self, scenes, dims_by_scene, dtype='uint16'):
        self.scenes = list(scenes)
        self._dims_by_scene = dims_by_scene
        self.current_scene = self.scenes[0]
        self.set_scene_calls = []

        class _DT:
            name = dtype
        self.dtype = _DT()

    def set_scene(self, scene):
        self.set_scene_calls.append(scene)
        self.current_scene = scene

    @property
    def dims(self):
        return self._dims_by_scene[self.current_scene]


class _FakeLayer:
    def __init__(self, name='img'):
        self.name = name
        self.metadata = {}


def test_list_scenes_and_scene_index():
    img = _FakeImage(['P0', 'P1', 'P2'], {s: _Dims() for s in ['P0', 'P1', 'P2']})
    assert S.list_scenes(img) == ['P0', 'P1', 'P2']
    assert S.scene_index(img, 'P2') == 2
    assert S.scene_index(img, 'nope') == 0          # unknown → 0, never an exception
    assert S.list_scenes(object()) == []            # single-scene/no-scenes reader → []


def test_build_scene_stack_pins_the_scene_and_reads_only_its_dims():
    dims = {'P0': _Dims(T=10, Y=8, X=6), 'P1': _Dims(T=4, Y=32, X=16)}
    img = _FakeImage(['P0', 'P1'], dims)

    reader_calls = []

    def fake_reader(image, *, scene, t, c, z):
        reader_calls.append((scene, t))
        return np.zeros((image.dims.Y, image.dims.X), np.uint16)

    stack = S.build_scene_stack(img, 'P1', plane_reader=fake_reader)

    assert img.set_scene_calls == ['P1'], "building the stack must pin the reader to the scene"
    assert stack.scene == 'P1'
    assert stack.shape == (4, 32, 16), "dims must come from the selected scene, not scene 0"
    assert reader_calls == [], "building the stack must NOT read any pixels"


def test_tag_scene_layer_and_scene_of_round_trip():
    layer = _FakeLayer()
    assert S.tag_scene_layer(layer, 'Well_B3') is True
    assert S.scene_of(layer) == 'Well_B3'
    # A None scene is a no-op, not a written empty tag.
    plain = _FakeLayer()
    assert S.tag_scene_layer(plain, None) is False
    assert S.scene_of(plain) is None


def test_update_metadata_reads_the_CURRENT_scene_not_scene_0():
    """The behaviour change: a multi-position file re-reads the *selected* scene's pixel size. If it
    kept reading scene 0, switching to a differently-calibrated position would mis-scale silently."""
    data_modules = pytest.importorskip("pycat.data.data_modules")

    class _PhysSize:
        def __init__(self, xy):
            self.X = xy
            self.Y = xy

    class _Scene:
        def __init__(self, xy):
            self.physical_pixel_sizes = _PhysSize(xy)
            self.metadata = {}

    class _MultiSceneImage:
        # P0 is 0.1 µm/px, P1 is 0.5 µm/px — deliberately different calibrations.
        scenes = ['P0', 'P1']
        _by_index = {0: _Scene(0.1), 1: _Scene(0.5)}

        def __init__(self):
            self.current_scene = 'P1'          # the switcher selected position 1

        def get_scene(self, idx):
            return self._by_index[idx]

    bdc = data_modules.BaseDataClass()
    bdc.update_metadata(_MultiSceneImage())

    # P1's 0.5 × 0.5 = 0.25, NOT P0's 0.1 × 0.1 = 0.01.
    assert bdc.data_repository['microns_per_pixel_sq'] == pytest.approx(0.25), (
        "update_metadata read scene 0's calibration instead of the current scene's")
    assert bdc.data_repository.get('pixel_size_from_metadata') is True
