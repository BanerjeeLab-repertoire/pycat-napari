"""The punctate gate's transfected route: dense dim puncta in a bright cell pass; dark cells never do."""
import numpy as np
import pytest

from pycat.toolbox.segmentation.intensity import (cell_has_punctate_signal,
                                                  compute_image_intensity_stats)

pytestmark = pytest.mark.base

YY, XX = np.mgrid[:256, :256]
CELL = (YY - 128) ** 2 + (XX - 128) ** 2 <= 100 ** 2


def _field(bright_cell, puncta, texture=0.0, seed=0):
    """Image background ~0.002 (noise 0.0004); the cell sits at `bright_cell` above it, with
    spatially correlated nucleoplasm `texture` (SD) and dense dim puncta of amplitude `puncta`."""
    import scipy.ndimage as ndi
    rng = np.random.default_rng(seed)
    img = 0.002 + rng.normal(0, 0.0004, YY.shape)
    img[CELL] += bright_cell
    if texture:
        tex = ndi.gaussian_filter(rng.normal(0, 1, YY.shape), 4.0)
        img[CELL] += (tex / tex[CELL].std() * texture)[CELL]
    if puncta:
        for _ in range(140):
            cy, cx = rng.integers(40, 216, 2)
            if (cy - 128) ** 2 + (cx - 128) ** 2 > 90 ** 2:
                continue
            s = rng.uniform(1.5, 2.5)
            img += puncta * rng.uniform(0.6, 1.0) * np.exp(-((YY - cy) ** 2 + (XX - cx) ** 2) / (2 * s ** 2))
    return img.astype(np.float32)


def _gate(img):
    labels = CELL.astype(int)
    stats = compute_image_intensity_stats(img, labels, smooth_sigma=1.0)
    return cell_has_punctate_signal(img, CELL, image_stats=stats)


@pytest.mark.parametrize('seed', [0, 1])
def test_textured_transfected_cell_rejected_before_now_passes(seed):
    """The real failure (annotated Irregular fields): texture inflates the cell's own spread,
    the 5-sigma floor rises above the puncta, and the old route rejects the whole cell."""
    ok, info = _gate(_field(bright_cell=0.05, puncta=0.025, texture=0.006, seed=seed))
    assert info['largest_blob_px'] < info['min_area_px']          # the old route alone says no
    assert ok and info['transfected_route']                       # the transfected route says yes
    assert info['base_over_bg'] >= 10 and info['z_noise'] >= 10


def test_dark_untransfected_cell_never_takes_the_transfected_route():
    ok, info = _gate(_field(bright_cell=0.0, puncta=0.0))
    assert not ok
    assert not info.get('transfected_route', False)


def test_dark_cell_with_flicker_is_not_rescued():
    img = _field(bright_cell=0.0005, puncta=0.0)               # barely above background
    img[CELL] += np.random.default_rng(3).normal(0, 0.0006, CELL.sum()).astype(np.float32)
    ok, info = _gate(img)
    assert not info.get('transfected_route', False)


def test_without_image_stats_the_route_is_closed():
    img = _field(bright_cell=0.05, puncta=0.03)
    _ok, info = cell_has_punctate_signal(img, CELL, image_stats=None)
    assert 'transfected_route' not in info
