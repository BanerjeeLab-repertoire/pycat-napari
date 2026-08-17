"""**Characterization pins for the preprocessing + flatfield science — written BEFORE they move.**

`pre_process_image` is the composite preprocessing pipeline (blob enhancement → WBNS → CLAHE → background
suppression → normalisation) that most workflows run before segmentation; the flatfield/background
corrections are the simpler shading fixes. Per the discipline (**no characterization test, no move**) this
pins their exact output on the fixed background-field scene.

The blob-enhancement step inside `pre_process_image` was later restored to v1.0.0's White Top-Hat +
fixed-sigma(3) LoG-mask recipe, replacing the separable-LoG-direct-image approach it had been changed to —
Meet Raval reported v1.0.0's recipe measurably preserves large condensates better (see
`pre_process_image`'s inline comment in `image_processing/preprocessing.py` for the full mechanism).
`pre_process_image`'s pin below was updated for that switch (123.062 -> 187.017 on this scene); the
flatfield/background-subtraction pins are untouched, unrelated functions.
"""
import warnings

import numpy as np
import pytest
from skimage.draw import disk

pytestmark = pytest.mark.base


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
        _c(ip.pre_process_image(im, 6, 15), (64, 64), 187.017, 0.0, 1.0)
        flat = np.linspace(0.8, 1.2, 64 * 64).reshape(64, 64).astype(np.float32)
        _c(ip.apply_flatfield_correction(im, flat), (64, 64), 286343.969, 45.7702, 265.6617)
        _c(ip.apply_background_subtraction(im, np.full_like(im, 45)), (64, 64), 102403.078, 0.0, 212.5509)
