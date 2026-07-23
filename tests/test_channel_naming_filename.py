"""**A channel's name must never come back WORSE than the file the user gave it** (deep_metadata_and_naming Part 3).

`channel_naming.identify_channel` never saw the filename, so a plain 2D TIFF — which usually carries no
channel metadata at all — had its fluorophore (in the name, `Image1-GFP.tif`) discarded in favour of a
generic pixel guess: both `Image1-GFP` and `Image1-DAPI` became `Image1-Fluorescence`, and the second
collided into `Fluorescence(1)`. This adds the filename as a naming tier (below real metadata, above the
pixel/position guess) and a never-worse-than-input rule.
"""
import numpy as np
import pytest

from pycat.utils.channel_naming import identify_channel

pytestmark = pytest.mark.core


def _fluorescence_frame():
    rng = np.random.default_rng(0)
    frame = rng.poisson(50, (48, 48)).astype('uint16')     # low, sparse background
    frame[18:28, 18:28] = 4000                              # a few bright puncta -> reads as fluorescence
    return frame


def test_a_fluorophore_in_the_filename_is_used_not_a_pixel_guess():
    info = identify_channel(channel_index=0, file_stem='Image1-GFP', pixel_frame=_fluorescence_frame())
    assert info['source'] == 'filename'
    assert info['label'] in ('GFP', 'EGFP')                 # the matcher's canonical label for the GFP family
    assert info['label'] != 'Fluorescence'


def test_two_files_named_by_fluorophore_get_distinct_names_no_disambiguator():
    gfp = identify_channel(channel_index=0, file_stem='Image1-GFP', pixel_frame=_fluorescence_frame())
    dapi = identify_channel(channel_index=0, file_stem='Image1-DAPI', pixel_frame=_fluorescence_frame())
    assert dapi['label'] == 'DAPI'
    assert gfp['label'] != dapi['label']                    # already distinct...
    assert '(1)' not in gfp['label'] and '(1)' not in dapi['label']   # ...so no meaningless suffix


def test_real_metadata_still_beats_the_filename():
    # the file says GFP but the OME Fluor says DAPI — trust the acquisition metadata, not the name
    info = identify_channel(channel_index=0, fluorophore_name='DAPI', file_stem='Image1-GFP')
    assert info['label'] == 'DAPI'
    assert info['source'] == 'name'


def test_a_stem_with_no_known_fluorophore_keeps_the_users_text_not_a_generic_word():
    info = identify_channel(channel_index=0, file_stem='Image1-myProtein', pixel_frame=_fluorescence_frame())
    assert info['source'] == 'filename'
    assert 'myProtein' in info['label']
    assert info['label'] != 'Fluorescence'


def test_a_purely_generic_stem_falls_through_to_the_pixel_classification():
    # 'Image1' distinguishes nothing, so the pixel guess is genuinely the best we have
    info = identify_channel(channel_index=0, file_stem='Image1', pixel_frame=_fluorescence_frame())
    assert info['source'] == 'pixels'
    assert info['label'] == 'Fluorescence'


def test_backward_compatible_without_a_stem_the_behaviour_is_unchanged():
    # existing callers pass no file_stem -> the pixel/position tiers behave exactly as before
    pix = identify_channel(channel_index=0, pixel_frame=_fluorescence_frame())
    assert pix['source'] == 'pixels' and pix['label'] == 'Fluorescence'
    pos = identify_channel(channel_index=1)
    assert pos['source'] == 'position'
