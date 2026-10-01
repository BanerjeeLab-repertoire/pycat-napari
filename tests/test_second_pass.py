"""The second segmentation pass recovers dim objects a bright neighbour hid, and adds nothing else."""
import numpy as np
import pytest
import scipy.ndimage as ndi

from pycat.toolbox.segmentation.fz import (felzenszwalb_segmentation_and_merging,
                                           fz_segmentation_and_binarization,
                                           fz_segmentation_with_second_pass)

pytestmark = pytest.mark.base

YY, XX = np.mgrid[:160, :160]
CELL = (YY - 80) ** 2 + (XX - 80) ** 2 <= 70 ** 2


def _crop(dim_amplitude, seed, texture=0.0):
    rng = np.random.default_rng(seed)
    raw = 0.05 + rng.normal(0, 0.004, YY.shape)
    if texture:
        tex = ndi.gaussian_filter(rng.normal(0, 1, YY.shape), 4.0)
        raw += tex / tex.std() * texture
    raw += 1.0 * np.exp(-((YY - 70) ** 2 + (XX - 70) ** 2) / (2 * 6.0 ** 2))      # bright condensate
    if dim_amplitude:
        raw += dim_amplitude * np.exp(-((YY - 100) ** 2 + (XX - 105) ** 2) / (2 * 2.0 ** 2))
    raw = raw.astype(np.float32)
    img = np.clip((raw - 0.05) / (raw.max() - 0.05), 0, 1).astype(np.float32)    # bg-removed-like
    return img, raw


@pytest.mark.parametrize('seed', [0, 1])
def test_a_shadowed_dim_punctum_is_recovered(seed):
    img, raw = _crop(dim_amplitude=0.03, seed=seed)       # 3% of the condensate: below merge_tol
    single = fz_segmentation_with_second_pass(img, CELL, 6, raw_img=raw, second_pass=False)
    double = fz_segmentation_with_second_pass(img, CELL, 6, raw_img=raw, second_pass=True)
    assert not single[100, 105] and double[100, 105]
    assert ndi.label(double)[1] == ndi.label(single)[1] + 1


@pytest.mark.parametrize('seed', [0, 1, 2])
def test_noise_and_texture_alone_add_nothing(seed):
    img, raw = _crop(dim_amplitude=0.0, seed=seed, texture=0.006)
    single = fz_segmentation_with_second_pass(img, CELL, 6, raw_img=raw, second_pass=False)
    double = fz_segmentation_with_second_pass(img, CELL, 6, raw_img=raw, second_pass=True)
    np.testing.assert_array_equal(single, double)


def test_off_is_exactly_the_single_pass():
    img, raw = _crop(dim_amplitude=0.03, seed=0)
    np.testing.assert_array_equal(
        fz_segmentation_with_second_pass(img, CELL, 6, raw_img=raw, second_pass=False),
        fz_segmentation_and_binarization(img, CELL, 6, raw_img=raw).astype(bool))


def test_a_flat_crop_no_longer_crashes_the_merge():
    flat = np.full((40, 40), 0.25, dtype=np.float32)
    out = felzenszwalb_segmentation_and_merging(flat)
    assert out.shape == flat.shape and np.allclose(out, 0.25)
