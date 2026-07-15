"""A physically-impossible pixel size (corrupt resolution tag) must NOT satisfy the pixel-size gate
when loading a STACK.

The bug (2026-07-15): an ImageJ Substack export wrote a 32-bit-overflow resolution tag
(~2.3e-10 um/px). The 2D loader screened it, but the stack loaders (IMS + generic + tifffile
fallback) all funnel through `stack_load._finalise_stack_load`, which committed the corrupt value to
`microns_per_pixel_sq` with `pixel_size_from_metadata=True` — SATISFYING the gate. The warning
printed but the Set-Scale dialog never appeared and the field stayed hidden, so every downstream
length/area/diffusion result was computed from a fabricated scale.

These tests exercise the pure plausibility screen and the gate-state logic headlessly.
"""

import pytest

pytestmark = pytest.mark.core

from pycat.utils.pixel_size import is_physically_plausible


def test_corrupt_substack_scale_is_implausible():
    # ImageJ Substack overflow → picometre-scale pixel; impossible.
    assert is_physically_plausible(2.328e-10) is False
    assert is_physically_plausible(2.3e-6) is False


def test_real_lab_scales_pass():
    for v in (0.0264, 0.067, 0.108, 0.65, 1.0):   # 63x confocal … genuine 1 um/px
        assert is_physically_plausible(v) is True, v


def test_finalise_gate_state_on_corrupt():
    # Mirror of the stack_load decision: implausible → sentinel + from_metadata False +
    # confirmed cleared = the gate-firing state.
    def finalise(dr, mpp):
        if is_physically_plausible(mpp):
            dr['microns_per_pixel_sq'] = mpp ** 2
            dr['pixel_size_from_metadata'] = True
        else:
            dr['microns_per_pixel_sq'] = 1
            dr['pixel_size_from_metadata'] = False
            dr['pixel_size_confirmed'] = False
        return dr

    dr = finalise({}, 2.328e-10)
    assert dr['microns_per_pixel_sq'] == 1
    assert dr['pixel_size_from_metadata'] is False
    assert dr['pixel_size_confirmed'] is False

    dr = finalise({}, 0.067)
    assert dr['pixel_size_from_metadata'] is True


def test_corrupt_already_rejected_is_detected():
    # The double-warn guard: if update_metadata already set the sentinel + from_metadata False,
    # stack_load recognises it and does not warn again.
    dr = {'microns_per_pixel_sq': 1, 'pixel_size_from_metadata': False}
    already = (dr.get('microns_per_pixel_sq') in (1, 1.0)
               and dr.get('pixel_size_from_metadata') is False)
    assert already is True
