"""**Characterization pins for the preprocessing + flatfield science — written BEFORE they move.**

`pre_process_image` is the composite preprocessing pipeline (background suppression → WBNS → CLAHE →
normalisation) that most workflows run before segmentation; the flatfield/background corrections are the
simpler shading fixes. They move to `image_processing/preprocessing.py` last. Per the discipline (**no
characterization test, no move**) this pins their exact output on the fixed background-field scene.
"""
import warnings

import numpy as np
import pytest
from skimage.draw import disk

pytestmark = pytest.mark.core


def _scene():
    yy, xx = np.mgrid[0:64, 0:64]
    bg = (40 + 0.4 * yy + 0.3 * xx).astype(np.float32)
    img = bg + np.random.default_rng(0).normal(0, 2, (64, 64)).astype(np.float32)
    for (cy, cx) in [(20, 20), (20, 44), (44, 20), (44, 44)]:
        rr, cc = disk((cy, cx), 4, shape=img.shape)
        img[rr, cc] += 180
    return img.astype(np.float32)


def _c(arr, shape, total, lo, hi):
    arr = np.asarray(arr)
    assert arr.shape == shape and str(arr.dtype) == 'float32'
    assert float(arr.sum()) == pytest.approx(total, rel=0, abs=max(abs(total) * 1e-6, 1e-2))
    assert float(arr.min()) == pytest.approx(lo, rel=0, abs=1e-3)
    assert float(arr.max()) == pytest.approx(hi, rel=0, abs=1e-3)


def test_preprocessing_science_is_pinned():
    import pycat.toolbox.image_processing_tools as ip
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        im = _scene()
        _c(ip.pre_process_image(im, 6, 15), (64, 64), 123.062, 0.0, 1.0)
        flat = np.linspace(0.8, 1.2, 64 * 64).reshape(64, 64).astype(np.float32)
        _c(ip.apply_flatfield_correction(im, flat), (64, 64), 286343.969, 45.7702, 265.6617)
        _c(ip.apply_background_subtraction(im, np.full_like(im, 45)), (64, 64), 102403.078, 0.0, 212.5509)
