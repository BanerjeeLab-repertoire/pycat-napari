"""
A genuine 1.0 um/px pixel size must be treated as a REAL calibration, not as a
missing-value sentinel.

The bug this guards (2026-07-15 file-I/O audit, finding #9)
----------------------------------------------------------
For years the calibration checks used ``abs(mpp - 1.0) > 1e-9`` to decide "does this image have a
real scale?" — using 1.0 um/px as a stand-in for "no scale". But downsampled, low-magnification,
derived, or synthetic images legitimately have a 1.0 um/px pixel size, and those got their
calibration silently thrown away (endless re-prompting; "px" scale bar on a calibrated image). The
fix decides from PROVENANCE (metadata-supplied, or explicitly user-set) rather than from the value,
falling back to the old value guess only when no provenance is recorded.

These tests exercise the pure decision logic headlessly (no napari / Qt).
"""

import pytest

pytestmark = pytest.mark.core


def _valid_scale(dr):
    """Mirror of field_status._valid_scale's decision (kept in lock-step)."""
    mpp = dr.get('microns_per_pixel_sq')
    if not bool(mpp):
        return False
    if bool(dr.get('pixel_size_from_metadata')) or bool(dr.get('pixel_size_confirmed')):
        return True
    return abs(float(mpp) - 1.0) > 1e-9


def test_no_scale_is_invalid():
    assert _valid_scale({}) is False


def test_real_scale_from_metadata_is_valid():
    assert _valid_scale({'microns_per_pixel_sq': 0.108 ** 2,
                         'pixel_size_from_metadata': True}) is True


def test_real_scale_user_set_is_valid():
    assert _valid_scale({'microns_per_pixel_sq': 0.108 ** 2,
                         'pixel_size_confirmed': True}) is True


def test_genuine_one_micron_from_metadata_is_valid():
    # THE BUG: 1.0 um/px is a legitimate calibration, not "missing".
    assert _valid_scale({'microns_per_pixel_sq': 1.0,
                         'pixel_size_from_metadata': True}) is True


def test_genuine_one_micron_user_set_is_valid():
    # THE BUG (manual-entry variant): user types 1.0, it must stick, not re-prompt.
    assert _valid_scale({'microns_per_pixel_sq': 1.0,
                         'pixel_size_confirmed': True}) is True


def test_one_micron_without_provenance_still_prompts():
    # Safe fallback: no provenance + value==1.0 → treat as unset and ask.
    # (A wrong 'False' only asks a question the user can answer.)
    assert _valid_scale({'microns_per_pixel_sq': 1.0}) is False


def test_non_unit_scale_without_provenance_uses_value_guess():
    # Behaviour for the ordinary (value != 1.0, no provenance) case is UNCHANGED.
    assert _valid_scale({'microns_per_pixel_sq': 0.25}) is True


def test_pre_change_layers_unchanged():
    # A layer loaded BEFORE this change carries no provenance flags. Its behaviour
    # must be byte-identical to the old value-based test for every value != 1.0,
    # and only differs (correctly) for an exact-1.0-with-provenance case.
    for mpp in (0.108 ** 2, 0.25, 4.0, 0.5):
        old = bool(mpp) and abs(float(mpp) - 1.0) > 1e-9
        new = _valid_scale({'microns_per_pixel_sq': mpp})  # no provenance keys
        assert old == new, (mpp, old, new)


# ── The production helper `pixel_size.has_real_pixel_size` IS the gate's decision now (field_status
# delegates to it). It must match `_valid_scale` above case-for-case, and its inverse names the
# placeholder — the "real-scale tag" that clears when a scale becomes real.

@pytest.mark.parametrize("dr,expected", [
    ({}, False),                                                         # nothing loaded
    ({'microns_per_pixel_sq': 1}, False),                               # the placeholder (no prov)
    ({'microns_per_pixel_sq': 1.0, 'pixel_size_from_metadata': True}, True),   # genuine 1 µm from file
    ({'microns_per_pixel_sq': 1.0, 'pixel_size_confirmed': True}, True),       # genuine 1 µm user-set
    ({'microns_per_pixel_sq': 0.108 ** 2, 'pixel_size_from_metadata': True}, True),
    ({'microns_per_pixel_sq': 0.25}, True),                             # value != 1, no prov → guess
    ({'microns_per_pixel_sq': None}, False),
])
def test_has_real_pixel_size_matches_the_gate_contract(dr, expected):
    from pycat.utils.pixel_size import has_real_pixel_size, pixel_size_is_placeholder
    assert has_real_pixel_size(dr) is expected
    assert pixel_size_is_placeholder(dr) is (not expected)
    # It IS the gate's mirror, case-for-case.
    assert has_real_pixel_size(dr) == _valid_scale(dr)
