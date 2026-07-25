"""
**A perfectly aligned fibril bundle was reported as isotropic.**

The nematic order parameter is ``S = |<exp(2iθ)>| = sqrt(<cos2θ>² + <sin2θ>²)`` -- the magnitude
of the mean resultant of the doubled orientation angles, which is invariant to the choice of
director (reference axis). ``orientation_order_parameter`` instead returned ``|<cos2θ>|``, which is
referenced to the image x-axis: a bundle of objects all oriented at 45° gives ``cos(90°) = 0`` and
was reported as ``S = 0`` (isotropic) despite being perfectly aligned.

The correct value was already being computed one line below (``mean_resultant``); the fix uses its
magnitude. These tests pin the three regimes the fixed statistic must satisfy.
"""
import numpy as np
import pytest


def _fn():
    m = pytest.importorskip("pycat.toolbox.morphological_complexity_tools")
    return m.orientation_order_parameter


def _ellipse_field(orientations_rad, size=220, r_rad=13, c_rad=4):
    """A labels mask of elongated ellipses at the given orientations, laid on a non-overlapping grid."""
    from skimage.draw import ellipse
    mask = np.zeros((size, size), dtype=np.int32)
    n = len(orientations_rad)
    cols = int(np.ceil(np.sqrt(n)))
    step = size // (cols + 1)
    for i, rot in enumerate(orientations_rad):
        gr, gc = divmod(i, cols)
        cy = step * (gr + 1)
        cx = step * (gc + 1)
        rr, cc = ellipse(cy, cx, r_rad, c_rad, shape=(size, size), rotation=float(rot))
        mask[rr, cc] = i + 1
    return mask


@pytest.mark.core
def test_aligned_bundle_at_45deg_gives_S_near_one():
    """All objects at 45° -> S ~= 1. The old |<cos2θ>| code returned ~0 here (the smoking gun)."""
    fn = _fn()
    mask = _ellipse_field([np.pi / 4] * 16)
    out = fn(mask)
    assert out['S'] > 0.95, out['S']
    # old code: float(np.mean(np.cos(2*angles))) ~= cos(pi/2) ~= 0
    assert abs(np.mean(np.cos(2 * mask_angles(fn, mask)))) < 0.2


@pytest.mark.core
def test_crossed_orientations_give_low_S():
    """Half at 0°, half at 90° -> doubled angles cancel -> S ~= 0."""
    fn = _fn()
    mask = _ellipse_field([0.0] * 8 + [np.pi / 2] * 8)
    out = fn(mask)
    assert out['S'] < 0.2, out['S']


@pytest.mark.core
def test_uniform_random_orientations_give_low_S():
    """Many uniformly-random orientations -> S -> 0."""
    fn = _fn()
    rng = np.random.default_rng(1)
    angles = list(rng.uniform(-np.pi / 2, np.pi / 2, size=64))
    out = fn(_ellipse_field(angles))
    assert out['S'] < 0.25, out['S']


@pytest.mark.core
def test_circular_variance_is_one_minus_S():
    """circular_variance is exactly 1 − S under the resultant-magnitude definition."""
    fn = _fn()
    out = fn(_ellipse_field([np.pi / 6] * 12))
    assert abs(out['circular_variance'] - (1.0 - out['S'])) < 1e-9


def mask_angles(fn, mask):
    """Recover the per-object orientation angles the function measured (for the old-code contrast)."""
    return fn(mask)['per_object_df']['orientation_rad'].values
