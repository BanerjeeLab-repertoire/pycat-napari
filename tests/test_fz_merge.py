"""**Felzenszwalb region merging actually runs — the RAG is a distance graph, not a similarity graph** (1.6.376 S4).

`felzenszwalb_segmentation_and_merging` built its region-adjacency graph in `mode='similarity'` (edge weight
large = alike) but thresholded with `merge_hierarchical`, which merges edges BELOW the threshold — correct only
for a distance graph (small = alike) — and used a threshold of `std(img)**2 / 2`, a variance in the wrong units.
The two mistakes cancelled into a silent no-op: the advertised merge step essentially never fired. The fix builds
the graph in `mode='distance'` and sets the threshold to `merge_tol * dynamic_range`, in the same
mean-intensity-difference units as the edge weights. These tests pin that the merge now (a) reduces the region
count below the initial over-segmentation and (b) is monotonic in `merge_tol`. The count-reduction assertion
fails on the old code, where the merge was a no-op and the count stayed equal.
"""
import numpy as np
import pytest
import skimage as sk

from pycat.toolbox.segmentation.fz import felzenszwalb_segmentation_and_merging

pytestmark = pytest.mark.base      # scikit-image / scipy stack

_SCALE, _SIGMA, _MIN = 7.0, 0.5, 2


def _two_flat_regions_with_texture():
    """Two flat half-planes (0.3 / 0.7) plus fine texture — felzenszwalb over-segments each half into many
    superpixels of near-identical mean intensity, which a working merge must fold back together."""
    rng = np.random.default_rng(0)
    h = w = 64
    img = np.full((h, w), 0.3, np.float32)
    img[:, w // 2:] = 0.7
    img += rng.normal(0.0, 0.02, (h, w)).astype(np.float32)
    return np.clip(img, 0.0, 1.0).astype(np.float32)


def _n_regions(avg_img):
    # felzenszwalb_segmentation_and_merging returns a mean-intensity image (each region painted its own mean);
    # distinct rounded intensities count the surviving regions.
    return len(np.unique(np.round(avg_img.astype(np.float64), 5)))


def test_merge_reduces_the_region_count_below_the_initial_oversegmentation():
    img = _two_flat_regions_with_texture()
    n_initial = len(np.unique(sk.segmentation.felzenszwalb(img, scale=_SCALE, sigma=_SIGMA, min_size=_MIN)))
    n_merged = _n_regions(felzenszwalb_segmentation_and_merging(
        img, scale=_SCALE, sigma=_SIGMA, min_size=_MIN, merge_tol=0.05))
    # Old code (similarity graph + variance threshold) merged nothing → n_merged == n_initial.
    assert n_merged < n_initial


def test_merge_tolerance_is_monotonic_more_tolerance_merges_more():
    img = _two_flat_regions_with_texture()
    n = {t: _n_regions(felzenszwalb_segmentation_and_merging(
        img, scale=_SCALE, sigma=_SIGMA, min_size=_MIN, merge_tol=t)) for t in (0.0, 0.02, 0.05, 0.2)}
    assert n[0.2] <= n[0.05] <= n[0.02] <= n[0.0]
    assert n[0.2] < n[0.0]                       # the merge genuinely does something across the range


def test_a_large_tolerance_collapses_the_two_flat_regions():
    # With a tolerance spanning most of the dynamic range, the whole image should reduce to a handful of
    # regions (the two half-planes, plus at most a boundary sliver) — proof the merge reaches completion.
    img = _two_flat_regions_with_texture()
    n_merged = _n_regions(felzenszwalb_segmentation_and_merging(
        img, scale=_SCALE, sigma=_SIGMA, min_size=_MIN, merge_tol=0.2))
    assert n_merged <= 6


def test_zero_tolerance_leaves_the_oversegmentation_essentially_intact():
    img = _two_flat_regions_with_texture()
    n_initial = len(np.unique(sk.segmentation.felzenszwalb(img, scale=_SCALE, sigma=_SIGMA, min_size=_MIN)))
    n_merged = _n_regions(felzenszwalb_segmentation_and_merging(
        img, scale=_SCALE, sigma=_SIGMA, min_size=_MIN, merge_tol=0.0))
    assert n_merged >= 0.9 * n_initial           # merge_tol=0 disables merging
