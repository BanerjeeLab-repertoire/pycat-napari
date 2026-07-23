"""The frame-interval "unknown" warning must fire only when it can actually apply — on a loaded
time series. It must stay silent (a) with no image loaded at all, and (b) on a still 2-D image.

Why this matters: the dynamics panels seed their frame-interval spinbox at BUILD time, before any
file is opened. Firing the scary "every time-dependent result is out by a factor of two" warning
then — with nothing loaded — trains the user to scroll past it, so the one that fires on a real
movie (where a wrong interval IS a factor-of-two error) gets ignored too.
"""

import pytest

pytestmark = pytest.mark.base

from pycat.utils.frame_interval import has_time_axis


def test_no_image_loaded_is_silent():
    # a fresh, no-image data repository (defaults only) — nothing to analyse
    assert has_time_axis({'microns_per_pixel_sq': 1, 'metadata': {}}) is False


def test_non_dict_is_silent():
    assert has_time_axis(None) is False


def test_still_image_is_silent():
    # image loaded, single frame — no time axis
    assert has_time_axis({'file_metadata': {}, 'n_t': 1}) is False


def test_movie_warns():
    assert has_time_axis({'file_metadata': {}, 'n_t': 10}) is True


def test_image_loaded_unknown_frames_fails_loud():
    # image loaded but frame count not recorded — warn rather than stay silent
    assert has_time_axis({'file_metadata': {}}) is True


def test_recorded_nt_without_file_metadata_warns():
    # older session that recorded n_t but not file_metadata — still a movie
    assert has_time_axis({'n_t': 5}) is True
