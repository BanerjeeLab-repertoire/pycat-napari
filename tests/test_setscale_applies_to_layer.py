"""Setting the pixel size (via the load-time Set-Scale dialog or the in-dock gate) must APPLY the
scale to the napari image layer, not merely write `microns_per_pixel_sq` to the data repository.

The bug (2026-07-15, spotted from the coordinate readout showing px-only with no µm): the gate wrote
the repo value and called `notify_data_changed()`, but never set `layer.scale`. `_align_layer_scales`
can only PROPAGATE a scale from an already-scaled reference layer — with nothing scaled yet it finds
no reference and does nothing — so the image layer stayed at scale 1.0, the µm cursor readout never
appeared, and every layer-scale consumer (including VPT's auto linking distance) ran uncalibrated.
The fix routes both set-scale paths through `_enable_auto_scale_bar`, which reads the repo value and
sets `layer.scale = sqrt(microns_per_pixel_sq)`.

This test exercises that mechanism (repo value → layer scale) headlessly with a fake layer/viewer.
"""

import numpy as np
import pytest

pytestmark = pytest.mark.core


class _FakeLayer:
    def __init__(self, shape=(64, 64)):
        self.data = np.zeros(shape)
        self.scale = [1.0, 1.0]
        self.rgb = False


class _FakeScaleBar:
    def __init__(self):
        self.unit = None
        self.visible = False


class _FakeViewer:
    def __init__(self, layers):
        self.layers = layers
        self.scale_bar = _FakeScaleBar()


def test_enable_auto_scale_bar_applies_repo_pixel_size_to_layer():
    # Skip cleanly if napari isn't importable in this environment.
    napari = pytest.importorskip("napari")
    from pycat.file_io.napari_adapter import _enable_auto_scale_bar

    layer = _FakeLayer()
    # napari's isinstance checks need real layer classes; use a real Image layer.
    img = napari.layers.Image(np.zeros((64, 64)))
    assert list(img.scale[-2:]) == [1.0, 1.0]

    viewer = _FakeViewer([img])

    class _DC:
        data_repository = {'microns_per_pixel_sq': 0.067 ** 2}

    class _CM:
        active_data_class = _DC()

    _enable_auto_scale_bar(viewer, _CM(), image_layer=img)

    # layer scale should now be 0.067 on both display axes (sqrt of the repo value)
    assert abs(img.scale[-1] - 0.067) < 1e-9
    assert abs(img.scale[-2] - 0.067) < 1e-9


def test_repo_value_is_square_of_pixel_size():
    # The gate stores microns_per_pixel_SQ; the layer scale is its square root.
    px = 0.067
    mpx_sq = px ** 2
    assert abs(np.sqrt(mpx_sq) - px) < 1e-12
