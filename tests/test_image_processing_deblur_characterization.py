"""**Characterization pin for `deblur_by_pixel_reassignment` (DPR) — written BEFORE it moves.**

DPR (deblurring by pixel reassignment) sharpens by re-locating each pixel toward the local intensity
gradient; it is used by the deblur workflow. Its only in-file dependency (`upscale_image_interp`) now lives
in `_base`, so it moves to `image_processing/deblur.py` next. Per the image_processing discipline (**no
characterization test, no move**) this pins its exact two-array output on a fixed synthetic scene.
"""
import warnings

import numpy as np
import pytest
from skimage.draw import disk

pytestmark = pytest.mark.base


def _scene():
    rng = np.random.default_rng(0)
    img = rng.normal(100, 15, (48, 48)).astype(np.float32)
    for (cy, cx) in [(16, 16), (32, 32), (16, 32)]:
        rr, cc = disk((cy, cx), 3, shape=img.shape)
        img[rr, cc] += 150
    return img


def test_deblur_by_pixel_reassignment_is_pinned():
    from pycat.toolbox.image_processing_tools import deblur_by_pixel_reassignment
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        a, b = deblur_by_pixel_reassignment(_scene(), 4, 2, 5)
    a, b = np.asarray(a), np.asarray(b)
    # DPR magnifies 48 -> 60 (upscale x1.25 by the gain-driven grid)
    assert a.shape == (60, 60) and b.shape == (60, 60)
    assert float(a.sum()) == pytest.approx(226959.1601, rel=0, abs=1e-1)
    assert float(a.max()) == pytest.approx(623.406, rel=0, abs=1e-2)
    assert float(a[0, 0]) == pytest.approx(1.38468, abs=1e-4)
    assert float(a[24, 24]) == pytest.approx(22.216316, abs=1e-4)
    assert float(b.sum()) == pytest.approx(227045.0468, rel=0, abs=1e-1)
    assert float(b.max()) == pytest.approx(244.5204, rel=0, abs=1e-2)
