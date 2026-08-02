"""**A fixed 1-4px probe cannot describe an object of arbitrary size.**

`local_intensity_condition` and `gradient_condition` compared an object's interior
(eroded 1px) against a band 1-4px outside it — regardless of the object's own size.
That geometry is right for a point-like punctum. It is wrong for a condensate
spanning tens to hundreds of px:

  * eroding 1px off a 30px-wide object removes almost nothing, so the "interior"
    sample is essentially the whole object, boundary included;
  * a 1-4px ring hugging a large object's edge sits INSIDE that object's own halo —
    the PSF tail and the real concentration gradient at its boundary both scale with
    the object — so the "background" is contaminated upward, the contrast is
    underestimated, and the checks reject real objects.

Two attempts, and why this is the second one
---------------------------------------------
The first was an EXEMPTION: skip those two checks for objects >= 150px. It worked
against the fast path, where the SNR gate was dead — but 1.6.86 made CNR live, and
CNR reads the same fixed rim, so exempting `local_intensity` merely handed the
object to `local_snr` and rescued nothing. Measured:

    exemption ON : dropped (900px): local_snr                  -> 0 px kept
    exemption OFF: dropped (900px): local_intensity, local_snr -> 0 px kept

A fixed pixel bar could not have been right anyway: in vitro condensates can exceed
a cell, so "large" has no fixed value, and ground truth showed real detections with
a MEDIAN area of 157px — a 150px bar sits at the median, not above the puncta.

So this fixes what the checks MEASURE instead of exempting objects from them.
Nothing needs exempting, and the same rule applies at every size.

The ceiling is physical: in cellulo the cell bounds the condensate and all but
extreme ones are at most ~25% of the cell DIAMETER, so 25% of the cell's equivalent
radius is the largest standoff that can be justified before the ring starts sampling
other cells. In vitro there is no cell, and `cell_area` is then the field.
"""

# Standard library imports
import math

# Third party imports
import numpy as np
import pytest

# Local application imports
from pycat.toolbox import segmentation_tools as seg

pytestmark = pytest.mark.base

_P = dict(kurtosis_threshold=-3.0, local_snr_threshold=1.0, global_snr_threshold=1.0,
          intensity_hwhm_scale=1.17, max_area_fraction=0.25)
_CELL_AREA = 7744            # ~a 96x96 cell -> equivalent radius ~50px


def _r_eq(area):
    return math.sqrt(area / math.pi)


# ── the radii ────────────────────────────────────────────────────────────────

def test_a_PUNCTUM_gets_exactly_the_old_fixed_geometry():
    """**The compatibility anchor.** `min_spot_radius=2` gives `min_area ~13px`, and
    the old code eroded 1px, dilated 1px and took a 3px band. If this changes, every
    punctum in every existing result moves — and that is not what this fix is for."""
    assert seg._local_ring_radii(13, _CELL_AREA) == (1, 1, 2)


def test_the_radii_GROW_with_the_object():
    """The whole point: the probe scales with what it is probing."""
    small = seg._local_ring_radii(13, _CELL_AREA)
    mid = seg._local_ring_radii(300, _CELL_AREA)
    big = seg._local_ring_radii(900, _CELL_AREA)

    assert small[0] < mid[0] < big[0], f'erosion did not scale: {small} {mid} {big}'
    assert small[2] < mid[2] <= big[2], f'band did not scale: {small} {mid} {big}'


def test_the_radii_track_the_EQUIVALENT_RADIUS_not_the_area():
    """Area scales as r^2, so scaling radii by area would explode. The geometry is
    linear in r_eq: erode/gap at ~0.5 r_eq, band at ~1.0 r_eq."""
    area = 900
    erode, gap, band = seg._local_ring_radii(area, 10 ** 6)   # cap out of the way
    r = _r_eq(area)

    assert abs(erode - 0.5 * r) <= 1.0, f'erode {erode} is not ~0.5*r_eq ({0.5*r:.1f})'
    assert abs(gap - 0.5 * r) <= 1.0
    assert abs(band - 1.0 * r) <= 1.0, f'band {band} is not ~1.0*r_eq ({r:.1f})'


def test_the_CELL_sets_the_ceiling():
    """In cellulo, all but extreme condensates are at most ~25% of the cell diameter.
    Past that the ring would sample other cells, so the standoff caps at 25% of the
    cell's equivalent radius."""
    cap = max(3, int(round(0.25 * _r_eq(_CELL_AREA))))
    for area in (5_000, 50_000, 500_000):
        assert max(seg._local_ring_radii(area, _CELL_AREA)) <= cap, (
            f'an {area}px object escaped the cell-derived cap of {cap}px')


def test_a_BIGGER_field_lifts_the_ceiling():
    """In vitro the condensate can be larger than a cell and there is no cell to
    bound it — `cell_area` is then the field, so the cap scales with that instead of
    pinning every experiment to one number."""
    in_cellulo = seg._local_ring_radii(50_000, _CELL_AREA)
    in_vitro = seg._local_ring_radii(50_000, 4096 * 4096)

    assert max(in_vitro) > max(in_cellulo), (
        'the cap ignored the field size — in vitro objects would be probed at '
        'in-cellulo scale')


def test_a_DEGENERATE_cell_cannot_collapse_the_ring():
    """A tiny or empty cell mask must not produce a zero-width ring — that would
    sample no background at all and the checks would divide by nothing."""
    for cell_area in (0, 1, 4):
        erode, gap, band = seg._local_ring_radii(13, cell_area)
        assert erode >= 1 and gap >= 1 and band >= 2


def test_the_radii_are_INTEGERS():
    """They index morphological footprints. A float radius is a TypeError waiting for
    whoever changes the fractions."""
    for area in (13, 157, 900, 5000):
        assert all(isinstance(v, int) for v in seg._local_ring_radii(area, _CELL_AREA))


# ── the geometry those radii produce ─────────────────────────────────────────

def test_the_ring_does_not_TOUCH_the_object():
    """The gap exists so the ring clears the object's own halo. If the ring abutted
    the boundary it would sample the thing it is supposed to be a contrast against —
    which is the bug this fixes."""
    obj = np.zeros((80, 80), dtype=bool)
    obj[30:50, 30:50] = True
    _, dilated, local_bg = seg._ring_masks(obj, 4, 4, 8)

    assert not (local_bg & obj).any(), 'the background ring overlaps the object'
    assert not (local_bg & dilated).any(), 'the ring is inside the standoff'
    assert local_bg.sum() > 0, 'the ring is empty'


def test_the_INTERIOR_is_a_real_core_for_a_large_object():
    """Eroding 1px off a 400px object left the "interior" as basically the whole
    object. A scaled erosion actually gets inside it."""
    obj = np.zeros((80, 80), dtype=bool)
    obj[30:50, 30:50] = True                       # 400px
    erode_r, gap_r, band_r = seg._local_ring_radii(int(obj.sum()), _CELL_AREA)
    interior, _, _ = seg._ring_masks(obj, erode_r, gap_r, band_r)
    interior_1px, _, _ = seg._ring_masks(obj, 1, 1, 2)

    assert interior.sum() < 0.75 * obj.sum(), (
        'the scaled interior is still nearly the whole object')
    assert interior_1px.sum() > 0.75 * obj.sum(), (
        'premise check: a 1px erosion should barely dent a 400px object')


# ── the behaviour it buys ────────────────────────────────────────────────────

def _halo_scene(size=96):
    """A large object whose 1-4px rim sits inside its own bright halo — the case the
    fixed geometry rejected and the exemption was invented to rescue."""
    rng = np.random.default_rng(0)
    img = rng.normal(120, 4, (size, size)).astype(np.float32)
    obj = np.zeros((size, size), dtype=bool)
    obj[30:60, 30:60] = True
    halo = np.zeros((size, size), dtype=bool)
    halo[24:66, 24:66] = True
    img[halo & ~obj] += 60
    img[obj] += 25
    cell = np.zeros((size, size), dtype=int)
    cell[4:size-4, 4:size-4] = 1
    return img, cell, obj, obj.astype(int)


@pytest.mark.parametrize('impl', ['puncta_refinement_filtering_func',
                                  'puncta_refinement_filtering_func_fast'])
def test_a_large_object_with_a_HALO_now_survives_on_its_MERITS(impl):
    """**No exemption anywhere.** The ring clears the halo, so the contrast is
    measured instead of swallowed, and the object passes the same checks a punctum
    passes. Both implementations, because they must agree."""
    img, cell, obj, lab = _halo_scene()
    out = getattr(seg, impl)(img, img.copy(), obj, cell, lab, 2, **_P)

    assert out.sum() > 0, (
        'a real large object was still rejected — the ring is not clearing its halo')


def test_a_NOISE_blob_is_still_rejected():
    """Scaling the rim must not become a way for noise to pass. A zero-contrast
    detection has nothing to measure at any radius."""
    img = np.random.default_rng(1).normal(120, 4, (96, 96)).astype(np.float32)
    obj = np.zeros((96, 96), dtype=bool)
    obj[46:52, 46:52] = True
    cell = np.zeros((96, 96), dtype=int)
    cell[4:92, 4:92] = 1

    out = seg.puncta_refinement_filtering_func(
        img, img.copy(), obj, cell, obj.astype(int), 2, **_P)
    assert out.sum() == 0, 'a pure-noise blob survived the scaled ring'


def test_a_bright_PUNCTUM_is_unaffected():
    """The regression that matters most: puncta get radii 1/1/2, the old geometry, so
    existing punctate results do not move."""
    img = np.random.default_rng(1).normal(120, 4, (96, 96)).astype(np.float32)
    obj = np.zeros((96, 96), dtype=bool)
    obj[47:51, 47:51] = True
    img[obj] += 60
    cell = np.zeros((96, 96), dtype=int)
    cell[4:92, 4:92] = 1

    assert seg._local_ring_radii(int(obj.sum()), int(cell.sum()))[:2] == (1, 1)
    out = seg.puncta_refinement_filtering_func(
        img, img.copy(), obj, cell, obj.astype(int), 2, **_P)
    assert out.sum() == int(obj.sum()), 'a bright punctum was rejected'


# ── the ring must exclude NEIGHBOURING puncta, not merely resist them statistically ──
#
# A dense field (hundreds of puncta tiling a nucleus) can put a NEIGHBOUR's own bright
# pixels inside this object's local-background ring. `_robust_bg`'s median resists
# outliers up to ~50% contamination; a tiling field can exceed that. `local_intensity_
# condition` and `gradient_condition` do not even use `_robust_bg` -- they take the
# ring's plain mean/std -- so they are contaminated by ANY neighbour overlap, not just
# a majority. Excluding neighbours from the ring outright fixes both.

def test_ring_excludes_NEIGHBOURING_puncta_pixels():
    """Premise + fix, in one test: the naive ring overlaps a neighbour; the
    ``exclude_mask`` ring does not."""
    size = 40
    obj = np.zeros((size, size), dtype=bool)
    obj[18:22, 18:22] = True
    neighbour = np.zeros((size, size), dtype=bool)
    neighbour[18:22, 24:28] = True          # close enough to reach into obj's ring
    puncta_mask = obj | neighbour

    _, _, local_bg_naive = seg._ring_masks(obj, 1, 1, 2)
    _, _, local_bg_clean = seg._ring_masks(obj, 1, 1, 2, exclude_mask=puncta_mask)

    assert (local_bg_naive & neighbour).any(), "premise failed: the naive ring does not reach the neighbour"
    assert not (local_bg_clean & neighbour).any(), "the cleaned ring still contains neighbour pixels"
    assert local_bg_clean.sum() > 0, "excluding the neighbour left no background at all"


def test_ring_GROWS_when_neighbours_CROWD_IT_OUT():
    """If exclusion leaves too few clean pixels to measure anything, the ring widens
    instead of silently running the checks on a handful of pixels (or none)."""
    size = 60
    obj = np.zeros((size, size), dtype=bool)
    obj[28:32, 28:32] = True
    puncta_mask = obj.copy()
    for dy, dx in [(-4, 0), (4, 0), (0, -4), (0, 4), (-3, -3), (-3, 3), (3, -3), (3, 3)]:
        cy, cx = 30 + dy, 30 + dx
        puncta_mask[cy - 1:cy + 2, cx - 1:cx + 2] = True

    _, _, local_bg = seg._ring_masks(obj, 1, 1, 2, exclude_mask=puncta_mask, min_bg_px=8)
    assert local_bg.sum() >= 8, "the ring did not grow far enough to find clean background"
    assert not (local_bg & puncta_mask).any(), "the grown ring still includes excluded (punctum) pixels"


def _dense_field_scene(size=96, n_ring=8, ring_radius=5, amp=60.0, seed=3):
    """A real, bright punctum at the centre of a ring of equally-real neighbours —
    the geometry that put neighbour pixels inside the centre object's local-
    background ring. All puncta are equally real; only the centre one is labelled,
    so the test isolates its own verdict."""
    rng = np.random.default_rng(seed)
    img = rng.normal(120.0, 4.0, (size, size)).astype(np.float32)
    cy, cx = size // 2, size // 2
    obj = np.zeros((size, size), dtype=bool)
    obj[cy - 2:cy + 2, cx - 2:cx + 2] = True
    puncta_mask = obj.copy()
    for k in range(n_ring):
        theta = 2 * np.pi * k / n_ring
        ny = cy + int(round(ring_radius * np.sin(theta)))
        nx = cx + int(round(ring_radius * np.cos(theta)))
        neighbour = np.zeros((size, size), dtype=bool)
        neighbour[ny - 2:ny + 2, nx - 2:nx + 2] = True
        puncta_mask |= neighbour
        img[neighbour] += amp
    img[obj] += amp
    labeled = np.zeros((size, size), dtype=int)
    labeled[obj] = 1
    cell = np.ones((size, size), dtype=int)
    return img, cell, puncta_mask, labeled


@pytest.mark.parametrize('impl', ['puncta_refinement_filtering_func',
                                  'puncta_refinement_filtering_func_fast'])
def test_a_real_punctum_in_a_DENSE_FIELD_of_real_neighbours_is_kept(impl):
    """The failure mode this fixes: a real, high-contrast punctum surrounded by
    equally-real neighbours whose own pixels sit inside its ring. Both
    implementations, because they must agree (this exercises the fast path's
    matching window pad, not just the shared `_ring_masks`)."""
    img, cell, puncta_mask, labeled = _dense_field_scene()
    out = getattr(seg, impl)(img, img.copy(), puncta_mask, cell, labeled, 2, **_P)
    assert out.sum() > 0, (
        "a real, bright punctum was rejected because equally-real NEIGHBOURS "
        "contaminated its local-background ring -- exactly the dense-field failure "
        "`exclude_mask` exists to fix")
