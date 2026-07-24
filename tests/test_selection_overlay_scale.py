"""**The selection overlay is drawn in the source layer's coordinate space — box and camera both.**

The reported bug: brushing a cellular-object plot did not highlight or zoom to the cell on a calibrated /
upscaled image. Cause: the overlay layer was created at scale 1.0 while its bbox is in the source layer's
pixel grid, and the camera (world coordinates) centred on the raw pixel index. So on any non-unit scale the
box and the camera both land in the wrong place. These pin the fix — resolve the source layer by
`pycat_layer_id`, draw with its scale/translate, centre in world coordinates, report an honest miss when the
source layer is absent, and hide a mismatched-resolution image under the highlight (restoring it after).
The viewer/layers are duck-typed fakes, so this is Qt-free and napari-free.
"""
import types

import numpy as np
import pytest

from pycat.utils import selection_overlay as ov

pytestmark = pytest.mark.base


# ── a minimal duck-typed napari stand-in ─────────────────────────────────────────────────────────────

class _Layer:
    def __init__(self, name, *, scale=(1.0, 1.0), translate=(0.0, 0.0), layer_id=None,
                 shape=(1024, 1024), kind='image', visible=True):
        self.name = name
        self.scale = scale
        self.translate = translate
        self.metadata = {'pycat_layer_id': layer_id} if layer_id else {}
        self.data = np.zeros(shape) if kind == 'image' else np.zeros((0, 2))
        self._type_string = kind
        self.visible = visible


class _Layers(list):
    def __contains__(self, name):
        return any(getattr(l, 'name', None) == name for l in self)

    def __getitem__(self, key):
        if isinstance(key, str):
            for l in self:
                if l.name == key:
                    return l
            raise KeyError(key)
        return list.__getitem__(self, key)

    def remove(self, name):
        for l in list(self):
            if l.name == name:
                list.remove(self, l)
                return


class _Viewer:
    def __init__(self, layers=(), ndim=2):
        self.layers = _Layers(layers)
        self.dims = types.SimpleNamespace(ndim=ndim, current_step=(0,) * ndim, point=(0.0,) * ndim)
        self.camera = types.SimpleNamespace(center=(0.0, 0.0, 0.0))
        self.layers.selection = set()

    def _add(self, name, data, kind, scale, translate):
        # reuse an existing overlay layer of this name, else append
        if name in self.layers:
            l = self.layers[name]
            l.data, l.scale, l.translate, l.visible = data, scale, translate, True
            return l
        l = _Layer(name, scale=scale or (1.0, 1.0), translate=translate or (0.0, 0.0), kind=kind)
        l.data = data
        self.layers.append(l)
        return l

    def add_shapes(self, data, *, name, scale=None, translate=None):
        return self._add(name, data, 'shapes', scale, translate)

    def add_points(self, data, *, name, scale=None, translate=None):
        return self._add(name, data, 'points', scale, translate)


def _ref(bbox=(10, 20, 30, 40), frame=None, layer_id=None, entity_id=None):
    return types.SimpleNamespace(bbox=bbox, frame=frame, source_layer_id=layer_id, entity_id=entity_id)


def _overlay(viewer):
    return viewer.layers[ov.BBOX_LAYER]


# ── the box is drawn with the source layer's scale ───────────────────────────────────────────────────

def test_the_bbox_is_drawn_with_the_source_layers_scale():
    src = _Layer('Image', scale=(0.0977, 0.0977), layer_id='L1')
    viewer = _Viewer([src])
    assert ov.show_selection(viewer, [_ref(layer_id='L1')]) is True
    box = _overlay(viewer)
    assert tuple(box.scale) == (0.0977, 0.0977)          # NOT the default 1.0
    # the rectangle data is still in PIXEL space; scale maps it to world
    assert np.allclose(box.data[0][0], [10, 20])         # first corner = (y0, x0) pixel


def test_an_upscaled_source_layer_highlights_at_the_upscaled_scale():
    up = _Layer('Upscaled', scale=(0.5, 0.5), layer_id='UP', shape=(2048, 2048))
    viewer = _Viewer([up])
    ov.show_selection(viewer, [_ref(layer_id='UP')])
    assert tuple(_overlay(viewer).scale) == (0.5, 0.5)


def test_scale_one_is_unchanged_regression():
    src = _Layer('Image', scale=(1.0, 1.0), layer_id='L1')
    viewer = _Viewer([src])
    ov.show_selection(viewer, [_ref(layer_id='L1')])
    assert tuple(_overlay(viewer).scale) == (1.0, 1.0)


def test_a_ref_with_no_source_layer_draws_unscaled_legacy():
    viewer = _Viewer([])
    assert ov.show_selection(viewer, [_ref(layer_id=None)]) is True
    # no source recorded → overlay at unit scale (the old behaviour, preserved)
    assert tuple(_overlay(viewer).scale) == (1.0, 1.0)


def test_a_missing_source_layer_reports_and_draws_nothing():
    # the ref records a source that is NOT open → an honest miss, no unverified box
    viewer = _Viewer([_Layer('SomethingElse', layer_id='OTHER')])
    assert ov.show_selection(viewer, [_ref(layer_id='GONE')]) is False
    assert ov.BBOX_LAYER not in viewer.layers or _overlay(viewer).visible is False


def test_multiple_refs_all_land_at_the_source_scale():
    src = _Layer('Image', scale=(0.2, 0.2), layer_id='L1')
    viewer = _Viewer([src])
    refs = [_ref(bbox=(0, 0, 5, 5), layer_id='L1'), _ref(bbox=(50, 50, 60, 60), layer_id='L1')]
    assert ov.show_selection(viewer, refs) is True
    box = _overlay(viewer)
    assert len(box.data) == 2 and tuple(box.scale) == (0.2, 0.2)


# ── the camera centres in world coordinates ──────────────────────────────────────────────────────────

def test_the_camera_centres_in_world_coordinates():
    from pycat.utils.object_ref import resolve_in_viewer
    src = _Layer('Image', scale=(0.0977, 0.0977), translate=(0.0, 0.0), layer_id='L1')
    viewer = _Viewer([src])
    ref = _ref(bbox=(10, 20, 30, 40), layer_id='L1')     # pixel centre = (20, 30)
    resolve_in_viewer(ref, viewer, centre=True)
    # world = pixel-centre * scale (+ translate)
    assert viewer.camera.center[1] == pytest.approx(20 * 0.0977)
    assert viewer.camera.center[2] == pytest.approx(30 * 0.0977)


def test_the_camera_falls_back_to_pixels_when_the_source_layer_is_absent():
    from pycat.utils.object_ref import resolve_in_viewer
    viewer = _Viewer([])
    resolve_in_viewer(_ref(bbox=(10, 20, 30, 40), layer_id=None), viewer, centre=True)
    assert viewer.camera.center == pytest.approx((0.0, 20.0, 30.0))   # raw pixel centre, scale 1


# ── hiding a mismatched-resolution image under the highlight ─────────────────────────────────────────

def test_a_dimension_mismatched_image_is_hidden_and_restored():
    target = _Layer('Upscaled', layer_id='UP', shape=(2048, 2048))
    source = _Layer('Source', shape=(1024, 1024))         # different grid, visible
    viewer = _Viewer([target, source])
    ov.show_selection(viewer, [_ref(layer_id='UP')])
    assert source.visible is False and target.visible is True     # mismatched source hidden, target shown
    ov.clear_selection(viewer)
    assert source.visible is True                                 # restored on clear


def test_a_layer_the_user_already_hid_is_not_turned_back_on():
    target = _Layer('Upscaled', layer_id='UP', shape=(2048, 2048))
    user_hidden = _Layer('Source', shape=(1024, 1024), visible=False)   # user turned it off
    viewer = _Viewer([target, user_hidden])
    ov.show_selection(viewer, [_ref(layer_id='UP')])
    ov.clear_selection(viewer)
    assert user_hidden.visible is False                          # PyCAT never recorded it, never restores it


def test_matching_images_stay_visible_and_non_images_are_never_hidden():
    target = _Layer('A', layer_id='A', shape=(1024, 1024))
    same = _Layer('B', shape=(1024, 1024))                        # matching grid → stays visible
    labels = _Layer('Labels', shape=(2048, 2048), kind='labels')  # mismatched but not an image
    viewer = _Viewer([target, same, labels])
    ov.show_selection(viewer, [_ref(layer_id='A')])
    assert same.visible is True and labels.visible is True


def test_hiding_that_would_leave_no_visible_image_hides_nothing():
    # the target is a LABELS layer (not an image); the only image mismatches → hiding it leaves no image
    target = _Layer('Mask', layer_id='M', shape=(1024, 1024), kind='labels')
    only_image = _Layer('Img', shape=(2048, 2048))
    viewer = _Viewer([target, only_image])
    ov.show_selection(viewer, [_ref(layer_id='M')])
    assert only_image.visible is True                            # not hidden — would leave no visible image
