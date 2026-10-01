"""`merge_mean_color_fast` must reproduce skimage's hierarchical merge bit for bit."""
import numpy as np
import pytest
import scipy.ndimage as ndi
import skimage as sk

from pycat.toolbox.segmentation.fz import (_weight_mean_color, merge_mean_color,
                                           merge_mean_color_fast)

pytestmark = pytest.mark.base


def _case(seed):
    rng = np.random.default_rng(seed)
    img = ndi.gaussian_filter(rng.random((96, 96)), rng.uniform(0.5, 3)).astype(np.float32)
    img += (rng.random((96, 96)) > 0.99) * 0.5                    # bright puncta on texture
    return img, sk.segmentation.felzenszwalb(img, scale=7.0, sigma=0.5, min_size=2)


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('tol', [0.02, 0.05, 0.15])
def test_identical_to_skimage_merge_hierarchical(seed, tol):
    img, segs = _case(seed)
    thresh = tol * float(img.max() - img.min())
    fast = merge_mean_color_fast(segs, sk.graph.rag_mean_color(img, segs, mode='distance'), thresh)
    reference = sk.graph.merge_hierarchical(
        segs, sk.graph.rag_mean_color(img, segs, mode='distance'), thresh=thresh, rag_copy=False,
        in_place_merge=True, merge_func=merge_mean_color, weight_func=_weight_mean_color)
    assert fast is not None
    np.testing.assert_array_equal(fast, reference)


def test_unequal_channels_fall_back():
    img, segs = _case(0)
    g = sk.graph.rag_mean_color(img, segs, mode='distance')
    node = next(iter(g.nodes))
    g.nodes[node]['total color'] = np.array([1.0, 2.0, 3.0])          # a genuinely RGB node
    assert merge_mean_color_fast(segs, g, 0.05) is None
