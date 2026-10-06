"""Scale reconciliation: a coarse detection replaces its own object's primary view, never fuses two."""
import numpy as np
import pytest
import scipy.ndimage as ndi

from pycat.toolbox.segmentation.reconcile import reconcile_scales

pytestmark = pytest.mark.base

N = 120
YY, XX = np.mgrid[:N, :N]


def _disc(cy, cx, r):
    return (YY - cy) ** 2 + (XX - cx) ** 2 <= r * r


def _gauss(cy, cx, s, a):
    return a * np.exp(-((YY - cy) ** 2 + (XX - cx) ** 2) / (2 * s * s))


def _count(m):
    return ndi.label(m, structure=np.ones((3, 3)))[1]


def test_the_rim_and_the_disc_of_one_object_become_one_object():
    rim = _disc(60, 60, 14) & ~_disc(60, 60, 10)        # what the band-pass leaves of a large condensate
    disc = _disc(60, 60, 15)                            # the coarse pass's view of it
    out = reconcile_scales(rim, disc)
    assert _count(out) == 1 and np.array_equal(out, disc)


def test_a_double_seeded_object_becomes_one_object_without_a_seam():
    raw = 50 + _gauss(60, 60, 12, 300.0)                # one broad condensate
    raw += _gauss(60, 52, 2.5, 40.0) + _gauss(60, 68, 2.5, 40.0)    # two sub-peaks on its plateau
    primary = _disc(60, 52, 5) | _disc(60, 68, 5)       # the primary pass split it
    coarse = _disc(60, 60, 16)
    out = reconcile_scales(primary, coarse, raw)
    assert _count(out) == 1 and np.array_equal(out, coarse)


def test_two_puncta_the_coarse_scale_fuses_stay_two():
    raw = 50 + _gauss(60, 50, 3.0, 300.0) + _gauss(60, 70, 3.0, 300.0)   # a deep dip between them
    primary = _disc(60, 50, 5) | _disc(60, 70, 5)
    coarse = _disc(60, 60, 16)                          # the coarse blob over both
    out = reconcile_scales(primary, coarse, raw)
    assert _count(out) == 2 and np.array_equal(out, primary)
    assert np.array_equal(reconcile_scales(primary, coarse), primary)      # the rule without raw


def test_an_object_only_the_coarse_scale_sees_is_kept():
    primary = _disc(30, 30, 4)
    coarse = _disc(80, 80, 15)
    out = reconcile_scales(primary, coarse)
    assert _count(out) == 2 and out[80, 80] and np.array_equal(out & _disc(30, 30, 6), primary)


def test_nothing_is_ever_dropped_for_being_dim():
    primary = _disc(30, 30, 4) | _disc(90, 90, 4)
    out = reconcile_scales(primary, np.zeros_like(primary))
    assert np.array_equal(out, primary)


def test_the_library_default_is_off_and_the_2d_cellular_workflow_turns_it_on():
    import inspect
    from pycat.batch.steps.analysis_steps import _condensate_refit_kwargs
    from pycat.toolbox.segmentation.subcellular import (run_segment_subcellular_objects,
                                                         segment_subcellular_objects)
    assert inspect.signature(segment_subcellular_objects).parameters['scale_reconciliation'].default is False
    assert inspect.signature(run_segment_subcellular_objects).parameters['scale_reconciliation'].default is True
    assert _condensate_refit_kwargs({})['scale_reconciliation'] is True


# ── At the boundary: one condensate detected as two touching pieces is joined; two condensates are not ──

def _refit(img, seeds, join):
    from pycat.toolbox.segmentation.boundary_refit import refit_regional_boundaries
    return refit_regional_boundaries(img, seeds, np.ones(img.shape, bool), ball_radius=6,
                                     join_split_objects=join)


def _two_lobes(d, s, ratio, seed=0):
    """A condensate drawn as two lobes joined by a body (Meet's 'mask split into two'); the dip between
    the lobes is set by their spacing."""
    rng = np.random.default_rng(seed)
    g = lambda cy, cx, sy, sx, a: a * np.exp(-((YY - cy) ** 2 / (2 * sy ** 2) + (XX - cx) ** 2 / (2 * sx ** 2)))
    img = (60 + rng.normal(0, 2, (N, N)) + g(60, 60 - d / 2, s, s, 200) + g(60, 60 + d / 2, s, s, 200 * ratio)
           + g(60, 60, s, d / 2 + s, 60))
    return img, _disc(60, round(60 - d / 2), 2) | _disc(60, round(60 + d / 2), 2)


@pytest.mark.parametrize('d, s, ratio', [(10, 5.0, 0.7), (12, 5.0, 1.0), (10, 4.0, 1.0)])
def test_one_condensate_detected_as_two_pieces_is_joined(d, s, ratio):
    img, seeds = _two_lobes(d, s, ratio)
    assert _count(_refit(img, seeds, join=False)) == 2          # the split, as before
    assert _count(_refit(img, seeds, join=True)) == 1


@pytest.mark.parametrize('d, s', [(14, 4.0), (12, 4.0)])
def test_two_lobes_with_a_real_dip_stay_two(d, s):
    img, seeds = _two_lobes(d, s, 1.0)                           # dips 0.39 and 0.24
    assert _count(_refit(img, seeds, join=True)) == 2


@pytest.mark.parametrize('dim', [0.3, 0.6])
def test_a_dim_condensate_pressed_against_a_bright_one_is_never_joined(dim):
    rng = np.random.default_rng(1)
    body = ndi.gaussian_filter(_disc(60, 50, 8) * 1.0 + _disc(60, 62, 5) * dim * (~_disc(60, 50, 8)), 1.5)
    img = 60 + rng.normal(0, 2, (N, N)) + 250 * body
    assert _count(_refit(img, _disc(60, 50, 3) | _disc(60, 62, 2), join=True)) == 2
