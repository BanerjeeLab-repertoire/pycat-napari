"""Regional boundaries (1.6.461): condensates reach their edge, puncta keep theirs, nothing fuses."""
import numpy as np
import pytest
import scipy.ndimage as ndi

from pycat.toolbox.segmentation.boundary_refit import (keep_objects_apart,
                                                       refit_object_boundaries,
                                                       refit_regional_boundaries)

pytestmark = pytest.mark.base

YY, XX = np.mgrid[:200, :200]


def _gauss(cy, cx, s, a):
    return a * np.exp(-((YY - cy) ** 2 + (XX - cx) ** 2) / (2 * s ** 2))


def _disc(cy, cx, r):
    return (YY - cy) ** 2 + (XX - cx) ** 2 <= r ** 2


def _field(*blobs, seed=0):
    rng = np.random.default_rng(seed)
    img = 60.0 + rng.normal(0, 2.0, YY.shape)
    for b in blobs:
        img += _gauss(*b)
    return img


def _count(mask):
    return ndi.label(mask)[1]


def test_keep_objects_apart_prevents_relabel_fusion():
    lab = np.zeros((20, 20), np.int32)
    lab[5:10, 2:10] = 1
    lab[5:10, 10:18] = 2                    # touching along a column
    assert _count(lab > 0) == 1             # what relabelling a boolean OR would do
    apart = keep_objects_apart(lab)
    assert _count(apart > 0) == 2
    assert (apart > 0).sum() >= (lab > 0).sum() - 2 * 5 * 2    # costs only the contact line


def test_large_condensate_grows_past_half_max_small_punctum_does_not_balloon():
    img = _field((70, 70, 9.0, 300.0), (150, 140, 1.8, 300.0))
    cell = np.ones(img.shape, bool)
    seeds = _disc(70, 70, 6) | _disc(150, 140, 1)          # both under-covering, as detections do
    half = refit_object_boundaries(img, seeds, cell, level=0.5)
    reg = refit_regional_boundaries(img, seeds, cell, ball_radius=8)
    lab_h, lab_r = ndi.label(half)[0], ndi.label(reg)[0]
    big_h, big_r = (lab_h == lab_h[70, 70]).sum(), (lab_r == lab_r[70, 70]).sum()
    small_h, small_r = (lab_h == lab_h[150, 140]).sum(), (lab_r == lab_r[150, 140]).sum()
    assert _count(reg) == 2                                  # identity preserved
    assert big_r > 1.3 * big_h                               # the condensate reaches further out
    assert small_r <= 2.5 * small_h                          # the growth guard holds


def test_neighbouring_condensates_stay_two_objects():
    img = _field((100, 80, 8.0, 300.0), (100, 118, 8.0, 300.0))     # skirts overlap
    cell = np.ones(img.shape, bool)
    seeds = _disc(100, 80, 5) | _disc(100, 118, 5)
    reg = refit_regional_boundaries(img, seeds, cell, ball_radius=8)
    assert _count(reg) == 2


def test_irregular_region_falls_back_to_its_contour():
    rng = np.random.default_rng(3)
    img = 60.0 + rng.normal(0, 2.0, YY.shape)
    img[98:103, 40:160] += 150.0                              # a long thin aggregate
    img = ndi.gaussian_filter(img, 1.0)
    img += _gauss(40, 40, 3.0, 250.0)                         # plus a compact object for contrast
    cell = np.ones(img.shape, bool)
    seeds = np.zeros(img.shape, bool)
    seeds[99:102, 95:105] = True
    seeds |= _disc(40, 40, 2)
    half = refit_object_boundaries(img, seeds, cell, level=0.5)
    reg = refit_regional_boundaries(img, seeds, cell, ball_radius=6)
    assert reg[100, 100]
    lab = ndi.label(reg)[0]
    assert (lab == lab[100, 100]).sum() <= 2.5 * max((ndi.label(half)[0] == ndi.label(half)[0][100, 100]).sum(), 1)


def test_level_mode_is_the_previous_behaviour():
    from pycat.toolbox.segmentation.subcellular import segment_subcellular_objects
    img = _field((100, 100, 5.0, 300.0), seed=5)
    cell = _disc(100, 100, 60)
    a, _ = segment_subcellular_objects(img, img, cell, 1, 8, boundary_mode='level', multiscale=False,
                                       punctate_gate=False)
    b, _ = segment_subcellular_objects(img, img, cell, 1, 8, boundary_mode='level', multiscale=False,
                                       punctate_gate=False)
    assert np.array_equal(a, b)


def test_batch_replay_defaults_match_the_gui():
    from pycat.batch.steps.analysis_steps import _condensate_refit_kwargs
    assert _condensate_refit_kwargs({}) == {'multiscale': True, 'boundary_refit': True,
                                            'refit_level': 0.5, 'boundary_mode': 'regional',
                                            'second_pass': True, 'transfected_route': True}
    assert _condensate_refit_kwargs({'boundary_mode': 'level'})['boundary_mode'] == 'level'


def test_every_object_records_which_boundary_it_kept():
    from pycat.toolbox.segmentation.boundary_refit import BOUNDARY_LEVEL, BOUNDARY_REGIONAL
    img = _field((70, 70, 9.0, 300.0), (150, 140, 1.8, 300.0))
    cell = np.ones(img.shape, bool)
    seeds = _disc(70, 70, 6) | _disc(150, 140, 1)
    source = np.zeros(img.shape, np.uint8)
    reg = refit_regional_boundaries(img, seeds, cell, ball_radius=8, source_out=source)
    assert np.array_equal(source > 0, reg)                         # every output pixel is attributed
    assert source[70, 70] == BOUNDARY_REGIONAL                     # the condensate took the regional edge
    assert set(np.unique(source[reg])) <= {BOUNDARY_REGIONAL, BOUNDARY_LEVEL}


def test_segmentation_fills_the_callers_provenance_map_in_both_modes():
    from pycat.toolbox.segmentation.subcellular import segment_subcellular_objects
    img = _field((100, 100, 5.0, 300.0), seed=5)
    cell = _disc(100, 100, 60)
    for mode in ('regional', 'level'):
        source = np.zeros(img.shape, np.uint8)
        refined, _ = segment_subcellular_objects(img, img, cell, 1, 8, boundary_mode=mode,
                                                 multiscale=False, punctate_gate=False,
                                                 boundary_source=source)
        assert np.array_equal(source > 0, refined.astype(bool)), mode



# A bright core and a dimmer neighbour on a dim irregular body: (seed, core amp, core sigma, neighbour dx,
# dy, amp, sigma, body amp, body sigma x, sigma y, body dx, dy). Found by search; before 1.6.470 each
# came out as a core plus a crescent (solidity 0.67-0.76), or split one detection in three.
_SHELL_SCENES = [
    (2, 165.1, 1.87, 4.83, -2.18, 165.5, 3.97, 139.9, 5.01, 8.31, 2.81, -3.74),
    (24, 462.5, 3.09, 5.31, 5.49, 246.9, 3.08, 79.8, 12.91, 9.35, 8.22, 0.63),
    (34, 294.4, 2.23, 4.11, 1.51, 163.8, 3.19, 131.7, 9.36, 8.94, -2.72, 3.67),
]


@pytest.mark.parametrize('scene', _SHELL_SCENES, ids=lambda s: f'scene{s[0]}')
def test_two_seeds_sharing_one_half_max_contour_never_leave_a_shell(scene):
    """Two detections whose blobs join at half height share one fallback contour. Taken whole, it let
    the first claim its neighbour's pixels, and the neighbour's region, written only where still free,
    came out as a crescent or shell around it -- the 'rings' around condensates. Each detection must
    now come out as one compact object."""
    t, a1, s1, dx, dy, a2, s2, ab, sbx, sby, bx, by = scene
    rng = np.random.default_rng(t)
    img = (60 + rng.normal(0, 2, YY.shape) + _gauss(100, 100, s1, a1) + _gauss(100 + dy, 100 + dx, s2, a2)
           + ab * np.exp(-((YY - 100 - by) ** 2 / (2 * sby ** 2) + (XX - 100 - bx) ** 2 / (2 * sbx ** 2))))
    seeds = _disc(100, 100, 2) | _disc(100 + round(dy), 100 + round(dx), 2)
    out = refit_regional_boundaries(img, seeds, np.ones(img.shape, bool), ball_radius=6)
    lab, n = ndi.label(out)
    assert n == 2
    from skimage.measure import regionprops
    assert min(p.solidity for p in regionprops(lab)) >= 0.8
