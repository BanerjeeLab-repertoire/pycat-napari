"""**Characterization pins for the filter/enhancement family — written BEFORE they move.**

The 2D/pseudo-3D filters and enhancement functions (Gaussian smoothing, Gabor, difference-of-Gaussian blob
enhancement, Laplacian-of-Gaussian, bilateral, and the peak/edge enhancer) move to
`image_processing/filters.py` next. Per the image_processing discipline (**no characterization test, no
move**) this pins each one's exact output (shape / dtype / sum / min / max) on fixed synthetic inputs, so the
relocation is provably byte-identical. They build on the `_base` primitives.
"""
import warnings

import numpy as np
import pytest
from skimage.draw import disk

pytestmark = pytest.mark.core


def _img2d():
    rng = np.random.default_rng(0)
    a = rng.normal(100, 15, (48, 48)).astype(np.float32)
    for (cy, cx) in [(16, 16), (32, 32), (16, 32)]:
        rr, cc = disk((cy, cx), 3, shape=a.shape)
        a[rr, cc] += 150
    return a


def _vol3d():
    return np.random.default_rng(1).normal(50, 8, (5, 32, 32)).astype(np.float32)


def _check(arr, shape, dtype, total, lo, hi):
    arr = np.asarray(arr)
    assert arr.shape == shape and str(arr.dtype) == dtype
    assert float(arr.sum()) == pytest.approx(total, rel=0, abs=max(abs(total) * 1e-6, 1e-3))
    assert float(arr.min()) == pytest.approx(lo, rel=0, abs=1e-4)
    assert float(arr.max()) == pytest.approx(hi, rel=0, abs=1e-4)


def test_filter_science_is_pinned():
    import pycat.toolbox.image_processing_tools as ip
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        im, v = _img2d(), _vol3d()
        _check(ip.gaussian_smooth_2d(im, 1.5), (48, 48), 'float32', 240769.5312, 86.46545, 227.65135)
        _check(ip.dog_blob_enhance_2d(im), (48, 48), 'float32', 74.3097, 0.0, 1.0)
        _check(ip.gabor_filter_func(im), (48, 48), 'float32', 194167.0625, 31.73462, 288.35144)
        _check(ip.peak_and_edge_enhancement_func(im, 5), (48, 48), 'float32', 256.0165, 0.0, 1.0)
        _check(ip.apply_laplace_of_gauss_filter(im), (48, 48), 'float32', -23.3093, -10.24936, 2.3406)
        _check(ip.apply_laplace_of_gauss_enhancement(im), (2, 48, 48), 'float32', 185176.7969, 0.9, 284.52341)
        _check(ip.apply_bilateral_filter(im, 3), (48, 48), 'float32', 198210.8438, 73.21671, 247.83177)
        _check(ip.gaussian_smooth_3d_pseudo(v, 1.5), (5, 32, 32), 'float32', 255387.1094, 44.20555, 55.10072)
        _check(ip.gabor_filter_3d_pseudo(v), (5, 32, 32), 'float32', 227187.9375, 16.20717, 81.40273)
        _check(ip.dog_blob_enhance_3d_pseudo(v), (5, 32, 32), 'float32', 749.3254, 0.0, 0.8962)
