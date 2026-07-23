"""**Characterization pins for the background-removal family — written BEFORE it moves.**

Background removal directly shapes every downstream intensity measurement, so the spec calls for pinning it
with extra care on a **known background field** before `background.py` splits out. This fixes the exact
output of the rolling-ball / Gaussian background estimators, the WBNS wavelet background+noise separation,
the realness-weighted soft foreground suppression, and the combined edge-enhanced remover on a synthetic
scene: four bright disks on a smooth intensity gradient with added noise.
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


def _c(arr, shape, total, hi):
    arr = np.asarray(arr)
    assert arr.shape == shape and str(arr.dtype) == 'float32'
    assert float(arr.sum()) == pytest.approx(total, rel=0, abs=max(abs(total) * 1e-6, 1e-2))
    assert float(arr.max()) == pytest.approx(hi, rel=0, abs=1e-3)


def test_background_removal_is_pinned():
    import pycat.toolbox.image_processing_tools as ip
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        im = _scene()
        bg = ip.compute_rolling_ball_background(im, 6)
        _c(bg, (64, 64), 242399.156, 78.069)
        _c(ip.subtract_background(im, bg), (64, 64), 99536.688, 199.2674)
        mask = im > im.mean() + 20
        _c(ip.background_inpainting_func(im, mask, 6), (64, 64), 585901.562, 252.2762)
        _c(ip.rb_gaussian_background_removal(im, 6), (64, 64), 128.805, 0.6888)
        _c(ip.rb_gaussian_bg_removal_with_edge_enhancement(im, 6), (64, 64), 173.635, 1.0)
        _c(ip._realness_weight(ip.apply_rescale_intensity(im), 6), (64, 64), 288.756, 0.9999)
        _c(ip.soft_foreground_suppression(im, 6), (64, 64), 94631.852, 255.5697)

        wl = ip.wavelet_bg_and_noise_calculation(im, 3, 1)
        assert len(wl) == 3
        _c(wl[0], (64, 64), 286424.562, 90.0441)
        _c(wl[2], (64, 64), 286424.562, 241.4494)

        wb = ip.wbns_func(im, 4, 1)
        assert len(wb) == 2
        _c(wb[0], (64, 64), 100138.828, 175.9094)
        _c(wb[1], (64, 64), 283646.594, 255.2088)
