"""**Characterization pins for the object-size estimators — written BEFORE they move.**

`estimate_object_size_px` (top-hat + Otsu → median equivalent diameter → ball_radius) feeds the batch
auto-object-size path that drives downstream segmentation, so a silent change propagates. Its coverage is
thin, so — per the image_processing decomposition discipline (**no characterization test, no move**) — this
pins its exact output on a fixed synthetic scene before `size_estimation.py` is split out. The brightfield
variant and the workflow-validity gate are pinned alongside.

The scene is deterministic and headless: seven bright radius-4 disks on a flat noisy background.

`estimate_bimodal_object_sizes` (the large/small preprocessing cascade's router) is pinned separately below.
It is now a SINGLE native-resolution pass (a two-pass design using a second, downsampled, big-tophat_radius
probe was retired -- real-world testing found dense puncta populations produced a smooth CONTINUUM of
apparent sizes under that pass's decimation, with no genuine gap to split at, rather than two discrete
populations). It does NOT attempt to test whether the image is genuinely bimodal (two earlier statistical
versions -- Otsu-vs-bootstrap-null, then GMM-vs-bootstrap-null -- were both retired after finding that a
real image's visually-obvious minority of larger objects was statistically indistinguishable from one
population's natural right-skewed tail, meaning a real second population went undetected). Instead it
unconditionally sorts the diameters, takes the MEAN of the lower half as r_small, and takes the MEAN of
only the objects ABOVE THE 90TH PERCENTILE of the full distribution as r_large -- deliberately not the
mean of the upper half (tried first, then upper-half-mean+0.5*std), because the upper half is mostly
mid-sized objects and averaging over it dilutes r_large toward the population's overall scale, undersizing
the genuinely large tail and hollowing big condensates into a "necklace" -- bright rim, dim/empty core --
instead of a filled disk under top-hat's opening. Restricting the mean to the top decile targets the large
subpopulation directly. This is a plain sort+percentile+mean with no randomness, so results are
deterministic and safe to pin exactly.
"""
import warnings

import numpy as np
import pytest
from skimage.draw import disk

pytestmark = pytest.mark.base


def _scene():
    rng = np.random.default_rng(0)
    img = np.full((128, 128), 50.0, np.float32)
    img += rng.normal(0, 2, (128, 128)).astype(np.float32)
    for (cy, cx) in [(32, 32), (32, 96), (96, 32), (96, 96), (64, 64), (32, 64), (96, 64)]:
        rr, cc = disk((cy, cx), 4, shape=img.shape)
        img[rr, cc] = 300.0
    return img


def test_estimate_object_size_px_is_pinned():
    from pycat.toolbox.image_processing_tools import estimate_object_size_px
    r = estimate_object_size_px(_scene(), tophat_radius=15)
    assert r['n_objects'] == 7
    # ball_radius = ceil(1.5 * (object_size_px / 2)) — the same formula the GUI's
    # Measure Line tool uses (BaseDataClass.calculate_sizes), not a plain halving.
    assert r['ball_radius'] == 6
    assert r['object_size_px'] == pytest.approx(7.569397566060481, rel=0, abs=1e-9)


def test_estimate_object_size_px_brightfield_is_pinned():
    from pycat.toolbox.image_processing_tools import estimate_object_size_px_brightfield
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        r = estimate_object_size_px_brightfield(_scene())
    assert r['n_objects'] == 7
    assert r['ball_radius'] == 5
    assert r['object_size_px'] == pytest.approx(9.373021315815206, rel=0, abs=1e-9)


def test_auto_object_size_validity_gate_is_pinned():
    from pycat.toolbox.image_processing_tools import (
        auto_object_size_valid, AUTO_OBJECT_SIZE_VALID_WORKFLOWS)
    assert set(AUTO_OBJECT_SIZE_VALID_WORKFLOWS) == {'condensate', 'invitro_fluor'}
    assert auto_object_size_valid('condensate') is True
    assert auto_object_size_valid('invitro_fluor') is True
    assert auto_object_size_valid('single_cell') is False


def test_estimate_object_size_px_rejects_an_invalid_workflow():
    """The validity gate: a workflow not in the valid set raises rather than returning a bogus size."""
    from pycat.toolbox.image_processing_tools import estimate_object_size_px
    with pytest.raises(ValueError):
        estimate_object_size_px(_scene(), workflow='single_cell', tophat_radius=15)


def test_estimate_bimodal_object_sizes_is_none_on_a_degenerate_scene():
    """On the pinned scene (7 near-identical disks), the lower-half-mean vs.
    above-90th-percentile-mean split must land on r_large <= r_small (both
    are drawn from essentially the same noise-level diameter distribution)
    and correctly fall back to None rather than fabricate a two-scale
    split."""
    from pycat.toolbox.image_processing_tools import estimate_bimodal_object_sizes
    assert estimate_bimodal_object_sizes(_scene()) is None


def _bimodal_scene(shape=(2048, 2048), seed=5):
    """A genuinely bimodal scene sized like a realistic large acquisition:
    small puncta (radius 8-12) and larger, but not dramatically larger,
    condensates (radius 18-24) -- both within reach of the SAME native-
    resolution top-hat pass (default tophat_radius caps at 25 for an image
    this size), so no downsampled second probe is needed to see the large
    population at all. Matches the real-world case that motivated retiring
    the two-pass design: small_scale~25px on real data, i.e. "large" objects
    that aren't dramatically bigger than "small" ones."""
    rng = np.random.default_rng(seed)
    img = np.full(shape, 50.0, np.float32)
    img += rng.normal(0, 3, shape).astype(np.float32)
    for _ in range(120):
        cy, cx = rng.integers(30, shape[0] - 30), rng.integers(30, shape[1] - 30)
        r = rng.integers(8, 12)
        rr, cc = disk((cy, cx), r, shape=shape)
        img[rr, cc] = 300.0
    for _ in range(20):
        cy, cx = rng.integers(60, shape[0] - 60), rng.integers(60, shape[1] - 60)
        r = rng.integers(18, 24)
        rr, cc = disk((cy, cx), r, shape=shape)
        img[rr, cc] = 280.0
    return img


def test_estimate_bimodal_object_sizes_is_pinned_on_a_genuine_bimodal_scene():
    """On a scene with two well-separated, native-resolution-visible size
    populations (120 small radius 8-12, 20 large radius 18-24), the
    lower-half-mean (r_small) / above-90th-percentile-mean (r_large) split
    has an exact, deterministic answer -- pin it. n_small lands near 67 (not
    120) for the same reason as the earlier mean-of-halves design: the split
    is by RANK (the sorted diameters' midpoint), not by which true
    population each object came from, so the upper half of the SMALL
    population's own spread gets folded in with the large population. This
    is the known, accepted trade-off of not testing for genuine bimodality
    (see module docstring): the split is unconditional and rank-based, not a
    classification of every object into its true population. n_large is now
    only 13 (not 68, and not 20) -- the top DECILE of the full distribution,
    not the top half -- and r_large=32 (vs. 20 for a plain upper-half mean,
    24 for upper-half-mean+0.5*std) because restricting the mean to that
    genuinely-large tail, instead of diluting it with the upper half's
    mostly mid-sized objects, pulls r_large much closer to the true large
    population's own scale."""
    from pycat.toolbox.image_processing_tools import estimate_bimodal_object_sizes
    r = estimate_bimodal_object_sizes(_bimodal_scene())
    assert r == {'r_large': 32, 'r_small': 13, 'n_large': 13, 'n_small': 67}


def test_estimate_bimodal_object_sizes_is_deterministic():
    """Plain sort+percentile+mean, no randomness -- re-running on the SAME
    image must give the exact SAME result every time."""
    from pycat.toolbox.image_processing_tools import estimate_bimodal_object_sizes
    img = _bimodal_scene()
    results = [estimate_bimodal_object_sizes(img) for _ in range(3)]
    assert results[0] is not None
    assert all(r == results[0] for r in results)
