"""**Characterization pins for the shared image-processing primitives — written BEFORE they move.**

`apply_rescale_intensity`, `invert_image`, `upscale_image_interp`, `_safe_equalize_adapthist` and
`pseudo3d_tri_planar_filter` are the low-level primitives the background / preprocessing / upscaling /
deblur algorithms all build on. They move to `image_processing/_base.py` first (dependency-ordered), so —
per the image_processing decomposition discipline (**no characterization test, no move**) — this pins their
exact output on fixed synthetic inputs before the move. `apply_rescale_intensity` and `upscale_image_interp`
especially: they shape nearly every downstream intensity measurement.
"""
import warnings

import numpy as np
import pytest

pytestmark = pytest.mark.base


def _img():
    return np.random.default_rng(0).normal(100, 20, (32, 32)).astype(np.float32)


def test_apply_rescale_intensity_is_pinned():
    from pycat.toolbox.image_processing_tools import apply_rescale_intensity
    r = apply_rescale_intensity(_img())
    assert r.dtype == np.float32 and r.shape == (32, 32)
    assert float(r.min()) == 0.0 and float(r.max()) == 1.0
    assert float(r.sum()) == pytest.approx(566.026855, rel=0, abs=1e-4)
    assert float(r[0, 0]) == pytest.approx(0.577873170375824, abs=1e-9)
    assert float(r[15, 15]) == pytest.approx(0.2887207865715027, abs=1e-9)


def test_invert_image_is_pinned():
    from pycat.toolbox.image_processing_tools import invert_image
    inv = invert_image(_img().astype(np.uint16))
    assert inv.dtype == np.uint16
    assert int(inv.min()) == 65374 and int(inv.max()) == 65513
    assert int(inv.sum()) == 67006954


def test_upscale_image_interp_is_pinned():
    from pycat.toolbox.image_processing_tools import upscale_image_interp
    up = upscale_image_interp(_img(), 32, 32, upscale_factor=2)
    assert up.shape == (64, 64)
    assert float(up.sum()) == pytest.approx(405613.3364, rel=0, abs=1e-2)
    assert float(up[0, 0]) == pytest.approx(102.51460266113281, abs=1e-6)
    assert float(up[63, 63]) == pytest.approx(93.31334686279297, abs=1e-6)


def test_safe_equalize_adapthist_is_pinned():
    from pycat.toolbox.image_processing_tools import apply_rescale_intensity, _safe_equalize_adapthist
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        se = _safe_equalize_adapthist(apply_rescale_intensity(_img()))
    assert se.shape == (32, 32)
    assert float(se.sum()) == pytest.approx(537.125, rel=0, abs=1e-3)
    assert float(se[0, 0]) == pytest.approx(0.5483871102333069, abs=1e-9)


def test_pseudo3d_tri_planar_filter_is_pinned():
    from pycat.toolbox.image_processing_tools import pseudo3d_tri_planar_filter, gaussian_smooth_2d
    vol = np.random.default_rng(0).normal(0, 1, (4, 16, 16)).astype(np.float32)
    p3 = pseudo3d_tri_planar_filter(vol, gaussian_smooth_2d, sigma=1.0)
    assert p3.shape == (4, 16, 16)
    assert float(np.asarray(p3).sum()) == pytest.approx(-50.370758056640625, rel=0, abs=1e-4)
