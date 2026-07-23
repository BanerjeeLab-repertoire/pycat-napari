"""**A present-but-meaningless metadata value is filtered, so it can't masquerade as real** (tag_confidence Part 2).

`is_meaningful(field, value)` is the one shared validity filter: empty / placeholder / non-finite / a
field-specific sentinel → rejected (the field becomes None, which correctly triggers the gates). The
field-aware part is precise: a pixel_size of 1.0 and a gain/magnification/NA of 0 are sentinels, but the
number 1 (binning, amplification_gain) is never blanket-rejected.
"""
import math

import pytest

from pycat.utils.metadata_validity import is_meaningful, rejection_reason

pytestmark = pytest.mark.core


@pytest.mark.parametrize("value", ['', '   ', '\t', 'Unknown', 'unknown', 'N/A', 'n/a', 'NA', 'none',
                                   'NULL', 'undefined', '<none>', 'default', '-', '?', '--', 'TBD'])
def test_empty_and_placeholder_strings_are_rejected(value):
    assert is_meaningful('detector_model', value) is False


@pytest.mark.parametrize("value", ['APD', 'EGFP', 'ISS Vista', '0.0977', 'Fluorescence'])
def test_real_strings_are_kept(value):
    assert is_meaningful('anything', value) is True


def test_non_finite_numbers_are_rejected():
    assert is_meaningful('position_x', float('nan')) is False
    assert is_meaningful('position_x', float('inf')) is False
    assert is_meaningful('position_x', 12.5) is True


# ── the field-aware sentinels — the precise part ──────────────────────────────────────────────────
def test_pixel_size_one_is_the_sentinel_but_other_ones_are_not():
    assert is_meaningful('pixel_size_um', 1.0) is False        # the no-metadata sentinel
    assert is_meaningful('pixel_size_um', 0.0977) is True
    assert is_meaningful('binning', 1) is True                 # the number 1 is NOT blanket-rejected
    assert is_meaningful('amplification_gain', 1.0) is True


def test_zero_is_impossible_for_gain_magnification_and_NA():
    assert is_meaningful('gain', 0) is False
    assert is_meaningful('detector_gain', 0.0) is False
    assert is_meaningful('nominal_magnification', 0) is False
    assert is_meaningful('lens_na', 0.0) is False
    assert is_meaningful('numerical_aperture', 0) is False
    # ...but real values are kept
    assert is_meaningful('gain', 600) is True
    assert is_meaningful('nominal_magnification', 60) is True
    assert is_meaningful('lens_na', 1.4) is True


def test_the_zero_rule_is_precise_not_a_substring_smear():
    # 'channel_name' contains 'na' but is not a numerical-aperture field — a 0 here is kept, not a sentinel
    assert is_meaningful('channel_name', 0) is True
    # amplification_gain is explicitly excluded from the gain==0 rule (its 1.0 is legitimate)
    assert is_meaningful('amplification_gain', 0.0) is True


def test_rejection_reason_explains_a_discard_and_is_none_when_meaningful():
    assert rejection_reason('detector_model', '') == 'empty string'
    assert 'placeholder' in rejection_reason('detector_model', 'N/A')
    assert 'non-finite' in rejection_reason('position_x', float('nan'))
    assert 'sentinel' in rejection_reason('pixel_size_um', 1.0)
    assert 'impossible' in rejection_reason('gain', 0)
    assert rejection_reason('gain', 600) is None                # meaningful → no reason


# ── the application: the metadata write guard drops placeholders ──────────────────────────────────
def test_parse_description_blob_drops_placeholder_values():
    from pycat.file_io.metadata_extract import parse_description_blob
    blob = '{"Camera": "Zyla", "Model": "", "Binning": "N/A", "Exposure-ms": 50, "Note": "real"}'
    out = parse_description_blob(blob)
    assert out.get('Camera') == 'Zyla' and out.get('Exposure-ms') == 50 and out.get('Note') == 'real'
    assert 'Model' not in out and 'Binning' not in out         # the placeholders were filtered
