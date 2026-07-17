"""**The fast filter is the DEFAULT, and it was running different science.**

`puncta_refinement_filtering_func_fast` is documented as bit-for-bit identical to
`puncta_refinement_filtering_func`, and `test_segmentation_refine.py` asserts it.
It was not true:

    slow (the 1.5.416 fix): local_cnr = (dilated_mean - loc_med) / loc_sd
    fast (the dead ratio):  dilated_mean / (img_local_bg_std + eps)

The CNR fix went into the slow path and never into the fast one — and
`_PYCAT_REFINE_FAST = True`, so the fast one is what every user runs. The gate the
slow path's own comment calls dead ("these two conditions have never rejected
anything") was still live in the default, and the ground-truth calibration
justifying its threshold described code nobody executed. The fast path also carried
NONE of the always-on drop reporting: `napari_show_info` appeared twice in the slow
filter and zero times in the fast one.

Why the existing equivalence test passed anyway
-----------------------------------------------
`local_intensity_condition` decides first in its fixture and masks the difference —
measured, puncta amplitudes 8→25 flip together at 11, so the SNR gate never gets a
vote. The test is real; its fixture simply cannot reach the divergence. These tests
reach it deliberately.

What the divergence cost, measured against ground truth
--------------------------------------------------------
`synthetic_puncta_image`, 3 amplitudes x 3 seeds: CNR removed **0 real puncta and
128 spurious** ones. The fast path was keeping 12-30 noise blobs per field and
counting them.
"""

# Third party imports
import numpy as np
import pytest

# Local application imports
from pycat.toolbox import segmentation_tools as seg

pytestmark = pytest.mark.core

_P = dict(kurtosis_threshold=-3.0, local_snr_threshold=1.0, global_snr_threshold=1.0,
          intensity_hwhm_scale=1.17, max_area_fraction=0.25)
_SL = (slice(30, 60), slice(30, 60))       # a 900px object


@pytest.fixture
def notices(monkeypatch):
    said = []
    monkeypatch.setattr(seg, 'napari_show_info', lambda m: said.append(m))
    monkeypatch.setattr(seg, 'napari_show_warning', lambda m: said.append(m))
    return said


def _scene(kind, size=96):
    """`halo_annulus` is the scene that exposed the divergence: a bright rim OUTSIDE
    the object, so `local_intensity` fires and — once it does — the SNR gate is what
    decides. That is where slow and fast parted company."""
    rng = np.random.default_rng(0)
    img = rng.normal(120, 4, (size, size)).astype(np.float32)
    obj = np.zeros((size, size), dtype=bool)
    obj[_SL] = True
    if kind == 'uniform_bright':
        img[obj] += 40
    elif kind == 'halo_annulus':
        halo = np.zeros((size, size), dtype=bool)
        halo[24:66, 24:66] = True
        img[halo & ~obj] += 60
        img[obj] += 25
    cell = np.zeros((size, size), dtype=int)
    cell[4:size-4, 4:size-4] = 1
    return img, cell, obj, obj.astype(int)


def _both(scene):
    img, cell, obj, lab = scene
    slow = seg.puncta_refinement_filtering_func(img, img.copy(), obj, cell, lab, 2, **_P)
    fast = seg.puncta_refinement_filtering_func_fast(img, img.copy(), obj, cell, lab, 2, **_P)
    return slow, fast


# ── the SNR formula ──────────────────────────────────────────────────────────

def test_the_two_filters_share_ONE_snr_implementation():
    """Not "the same formula in two places" — literally the same function. Two copies
    is how they diverged, and copying the fix across would have set up the next one."""
    import inspect
    for name in ('puncta_refinement_filtering_func',
                 'puncta_refinement_filtering_func_fast'):
        src = inspect.getsource(getattr(seg, name))
        assert '_snr_conditions(' in src, f'{name} does not call the shared helper'
        assert 'img_local_bg_std+np.finfo' not in src.replace(' ', ''), (
            f'{name} still computes the bare pedestal-scaled ratio inline')


def test_the_SNR_gate_is_CNR_and_can_actually_fire():
    """The bare ratio could not reject anything: with a pedestal of 120 and bg_std 5,
    `object_mean/bg_std` is ~24 against a threshold of 1.0. CNR subtracts the
    background first, so a zero-contrast object scores 0 and is rejected."""
    bg = np.random.default_rng(0).normal(120, 5, 400)

    noise_like, _ = seg._snr_conditions(120.0, bg, bg, 1.0, 1.0)
    real_like, _ = seg._snr_conditions(160.0, bg, bg, 1.0, 1.0)

    # `bool(...)`, not `is True`: these come back as numpy scalars, and `np.True_ is
    # True` is False — an identity check here tests numpy, not the gate.
    assert bool(noise_like), 'a zero-contrast object survived the SNR gate'
    assert not bool(real_like), 'a bright, real object was rejected by the SNR gate'


def test_the_gate_is_PEDESTAL_INVARIANT():
    """The whole point of the fix. The same physical contrast must score the same
    whatever the camera offset — the old ratio reported 115 vs 416 for one punctum."""
    rng = np.random.default_rng(1)
    verdicts = set()
    for pedestal in (0.0, 100.0, 500.0, 2000.0):
        bg = rng.normal(pedestal, 5, 400)
        verdicts.add(seg._snr_conditions(pedestal + 8.0, bg, bg, 1.0, 1.0)[0])
    assert len(verdicts) == 1, 'the SNR verdict moved with the camera pedestal'


def test_the_background_estimate_is_ROBUST_to_neighbours():
    """A plain std is contaminated by neighbouring puncta in the local ring: measured,
    a bright punctum with 3 neighbours had its ring_std inflated 5 -> 18, collapsing
    its CNR 6.7 -> 1.7. The metric would report crowding, not contrast."""
    rng = np.random.default_rng(2)
    clean = rng.normal(120, 5, 400)
    crowded = clean.copy()
    crowded[:12] += 200.0                      # three bright neighbours in the ring

    _, sd_clean = seg._robust_bg(clean)
    _, sd_crowded = seg._robust_bg(crowded)

    assert sd_crowded < 2.0 * sd_clean, (
        f'MAD went {sd_clean:.1f} -> {sd_crowded:.1f} with neighbours present — '
        f'that is crowding leaking into the contrast metric')


# ── parity, on the scene that broke it ───────────────────────────────────────

@pytest.mark.parametrize('kind', ['uniform_bright', 'halo_annulus'])
def test_slow_and_fast_AGREE(kind):
    """`halo_annulus` is the case the existing equivalence fixture cannot reach:
    slow rejected the object on local_snr while fast kept all 900px."""
    slow, fast = _both(_scene(kind))
    assert np.array_equal(slow, fast), (
        f'the two filters disagree on {kind} — the default path is running '
        f'different science from the one that is tested')


def test_the_default_path_is_still_the_FAST_one():
    """The premise of every test here. If this flips, the parity matters less but the
    performance story changes — either way, someone should notice."""
    assert seg._PYCAT_REFINE_FAST is True


# ── the reporting ────────────────────────────────────────────────────────────

def test_the_FAST_path_reports_its_drops(notices):
    """It reported nothing at all. The always-on summary exists so that puncta cannot
    vanish silently — and it was absent from the only path that runs by default."""
    img, cell, obj, lab = _scene('halo_annulus')
    seg.puncta_refinement_filtering_func_fast(img, img.copy(), obj, cell, lab, 2, **_P)

    hits = [m for m in notices if 'rejected' in m.lower()]
    assert hits, f'the default filter dropped an object and said nothing: {notices}'


def test_the_fast_report_NAMES_the_reason(notices):
    """A count alone does not help. "everything dropped on area" is what tells a user
    their min_spot_radius is wrong for this pixel size."""
    img, cell, obj, lab = _scene('halo_annulus')
    seg.puncta_refinement_filtering_func_fast(img, img.copy(), obj, cell, lab, 2, **_P)

    hits = [m for m in notices if 'reasons:' in m.lower()]
    assert hits, f'the drop report does not say why: {notices}'


def test_BOTH_paths_report_the_same_way(notices):
    """One reporter, so the two cannot describe themselves differently."""
    import inspect
    for name in ('puncta_refinement_filtering_func',
                 'puncta_refinement_filtering_func_fast'):
        assert '_report_refinement_drops(' in inspect.getsource(getattr(seg, name)), (
            f'{name} does not use the shared reporter')


def test_EVERYTHING_rejected_escalates_to_a_warning(notices):
    """Losing every detection usually means a threshold is wrong for the data, not
    that the puncta are all spurious — and it should not read as a quiet info line."""
    img, cell, obj, lab = _scene('halo_annulus')
    seg.puncta_refinement_filtering_func_fast(img, img.copy(), obj, cell, lab, 2, **_P)

    assert [m for m in notices if 'every detection was rejected' in m.lower()], (
        f'total rejection was not escalated: {notices}')


def test_a_filter_that_drops_NOTHING_stays_quiet(notices):
    """A report on every run is noise, and noise is how the real ones get ignored."""
    img, cell, obj, lab = _scene('uniform_bright')
    seg.puncta_refinement_filtering_func_fast(img, img.copy(), obj, cell, lab, 2, **_P)

    assert not notices, f'nothing was dropped but it spoke anyway: {notices}'
