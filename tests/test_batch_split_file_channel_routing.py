"""
**Batch preprocessed/background-removed the DAPI (segmentation) channel instead of the recorded
GFP (fluorescence/condensate-marker) channel, for a split-file recording — and Cellpose then ran
on the wrong channel entirely once that first bug was fixed.**

Reported by Meet Raval, twice, on a two-channel split-file condensate pipeline (separate
``*_DAPI.tif`` / ``*_GFP.tif`` files, each its own ``open_image`` step — no ``channel_assignment``,
so layers are named after the sample identity via ``derive_layer_name``: "In_Cell" / "In_Cell [1]",
never a generic "Segmentation"/"Fluorescence" placeholder).

Three compounding bugs, found in sequence
------------------------------------------
1. ``replay_preprocessing``/``replay_background_removal`` decided which channel to process with a
   single keyword test, ``'fluorescence' in active_layer_name``. A split-file recording's layer
   names never contain that word, so the test was always ``False`` — every preprocessing /
   background-removal step ran on ``state['image']`` (the DAPI/primary channel) regardless of what
   the GUI actually recorded.

2. ``_replay_split_file_companion`` stashed the companion (GFP) image ONLY in
   ``state['channels_by_name']``. ``state['fluorescence_image']`` was left as a bare alias of
   ``state['image']`` from the first ``open_image`` call — silently pointing at DAPI too, and never
   upscaled (``replay_upscaling`` skips anything it does not recognise as the CURRENT
   ``state['image']``), producing a shape-mismatch crash in ``cell_analysis``.

3. Fixing (2) exposed a THIRD bug: ``cellpose_segmentation``'s recorded ``image_layer`` was
   "Upscaled In_Cell" (the PRIMARY/DAPI channel's own recorded name — no keyword, no
   ``channels_by_name`` entry, since the split-file loader never adds the primary to that dict).
   Resolution failed and fell back to ``state['fluorescence_image']`` — which, once bug 2 was
   fixed, correctly pointed at GFP — so Cellpose ran on the WRONG (fluorescence) channel instead of
   DAPI. Tracking the primary's own recorded name (``state['_primary_channel_name']``) fixes the
   resolution directly, but naively substring-matching it reintroduces a FOURTH problem: napari
   disambiguates the duplicate base name with a ``" [N]"`` suffix, so "in_cell" (the primary) is
   *itself a substring of* "in_cell [1]" (the companion) — a plain substring test would misroute
   the companion's OWN recorded name back to the primary. The fix is a longest-match comparison:
   the companion's longer, more specific name always wins.

   Fixing THAT also surfaced a fifth issue: writing a processed (preprocessed/background-removed)
   result back into the SAME ``channels_by_name`` slot a raw-stage step (like ``cell_analysis``,
   recorded on "Upscaled In_Cell [1]" — no processing keyword) still needs to read would silently
   hand it the wrong stage. Raw and processed values for a named channel now live in two separate
   dicts (``channels_by_name`` / ``channels_processed_by_name``), mirroring the existing
   ``image``/``preprocessed`` and ``fluorescence_image``/``preprocessed_fluorescence`` pairs.
"""
from __future__ import annotations

import numpy as np
import pytest

pytestmark = pytest.mark.core


def test_split_file_preprocessing_and_bg_removal_target_the_companion_channel():
    """The exact reported scenario: a recorded active_layer like "Upscaled In_Cell [1]"
    (no "fluorescence" keyword) must still resolve to the companion (GFP) channel."""
    from pycat.batch.steps._common import _active_layer_channel_role

    dapi = np.zeros((8, 8), dtype=np.uint16)
    gfp = np.ones((8, 8), dtype=np.uint16)

    state = {
        'image': dapi,
        'fluorescence_image': gfp,     # what _replay_split_file_companion now sets
        'channels_by_name': {'In_Cell [1]': gfp},
        '_primary_channel_name': 'In_Cell',
    }

    on_fluor, key = _active_layer_channel_role(state, 'upscaled in_cell [1]')
    assert on_fluor is True
    assert key == 'In_Cell [1]'

    # A background-removal step recorded AFTER preprocessing carries the longer,
    # doubly-prefixed name -- must still match.
    on_fluor2, key2 = _active_layer_channel_role(
        state, 'pre-processed upscaled in_cell [1]')
    assert on_fluor2 is True
    assert key2 == 'In_Cell [1]'

    # And the PRIMARY's own recorded name ("Upscaled In_Cell", no "[1]") must NOT be
    # misrouted to the companion, even though "in_cell" is a substring of "in_cell [1]".
    on_fluor3, key3 = _active_layer_channel_role(state, 'upscaled in_cell')
    assert on_fluor3 is False
    assert key3 is None


def test_split_file_companion_load_sets_fluorescence_image_not_a_stale_alias():
    """_replay_split_file_companion must give state['fluorescence_image'] the companion
    array — not leave it aliased to the primary (DAPI) image from the first open_image call."""
    from pycat.batch.steps.io_steps import _replay_split_file_companion

    dapi = np.zeros((8, 8), dtype=np.uint16)

    state = {
        '_primary_open_image_stem': 'In Cell 1-DAPI',
        '_open_image_calls': 1,          # the primary has already loaded
        'image': dapi,
        'fluorescence_image': dapi,      # the stale alias replay_open_image leaves behind
    }

    def _fake_derive(image_path, primary_stem, companion_stem):
        return image_path.parent / f"{image_path.stem.replace('DAPI', 'GFP')}.tif"

    def _fake_load(path, channel=0):
        return np.ones((8, 8), dtype=np.uint16), 1.0

    import pycat.batch.steps.io_steps as io_steps
    orig_derive = io_steps._derive_split_companion_path
    orig_load = io_steps._load_image
    io_steps._derive_split_companion_path = _fake_derive
    io_steps._load_image = _fake_load
    try:
        from pathlib import Path
        params = {'file_path': 'C:/x/In Cell 1-GFP.tif',
                  '_active_layer_at_record': 'In_Cell [1]'}
        handled = _replay_split_file_companion(
            state, Path('C:/x/In Cell 1-DAPI.tif'), params)
    finally:
        io_steps._derive_split_companion_path = orig_derive
        io_steps._load_image = orig_load

    assert handled is True
    assert state['fluorescence_image'] is not dapi
    assert state['fluorescence_image'] is state['channels_by_name']['In_Cell [1]']


def test_open_image_records_primary_channel_name_on_the_first_call():
    """_replay_split_file_companion must stamp state['_primary_channel_name'] from the
    FIRST open_image step's own recorded name -- the fact _resolve_image_layer /
    _active_layer_channel_role need to recognise "Upscaled In_Cell" as the primary."""
    from pycat.batch.steps.io_steps import _replay_split_file_companion
    from pathlib import Path

    state = {'_primary_open_image_stem': 'In Cell 1-DAPI'}
    params = {'file_path': 'C:/x/In Cell 1-DAPI.tif',
              '_active_layer_at_record': 'In_Cell'}
    handled = _replay_split_file_companion(state, Path('C:/x/In Cell 1-DAPI.tif'), params)

    assert handled is False        # falls through: caller loads the primary normally
    assert state['_primary_channel_name'] == 'In_Cell'


def test_segmentation_channel_still_recognised_after_upscaling_makes_identity_stale():
    """Regression guard: replay_upscaling deliberately leaves a channel_assignment-based
    recording's OWN segmentation-channel channels_by_name entry pointing at the pre-upscale
    array (object identity against a NEW state['image'] would then look 'foreign'). The
    segmentation channel must still be recognised as such, by name, not misrouted to the
    fluorescence branch post-upscale."""
    from pycat.batch.steps._common import _active_layer_channel_role

    seg_stale = np.zeros((4, 4), dtype=np.float32)     # pre-upscale object, left behind
    fluor_up = np.ones((8, 8), dtype=np.float32)        # genuinely re-upscaled

    state = {
        'image': np.zeros((8, 8), dtype=np.float32),    # a DIFFERENT (upscaled) object
        'channels_by_name': {
            'Segmentation Image': seg_stale,
            'Fluorescence Image': fluor_up,
        },
        '_primary_channel_name': 'Segmentation Image',
    }

    on_fluor_seg, _ = _active_layer_channel_role(state, 'upscaled segmentation image')
    assert on_fluor_seg is False

    on_fluor_fl, key = _active_layer_channel_role(state, 'upscaled fluorescence image')
    assert on_fluor_fl is True
    # The literal 'fluorescence' keyword wins here (a generic-named config) — key is
    # None by design; the caller uses the fixed preprocessed_fluorescence slot, not a
    # per-name dict, for this case.
    assert key is None


def test_resolve_image_layer_gives_cellpose_the_primary_not_the_fallback():
    """The THIRD bug: cellpose_segmentation's recorded image_layer ("Upscaled In_Cell") has no
    keyword and no channels_by_name entry (the split-file loader only ever adds the companion) —
    resolution must succeed via _primary_channel_name, not silently fall back to
    state['fluorescence_image'] (the companion, wrong channel for a cell-segmentation step)."""
    from pycat.batch.steps._common import _resolve_image_layer

    dapi = np.full((8, 8), 1, dtype=np.float32)
    gfp = np.full((8, 8), 2, dtype=np.float32)

    state = {
        'image': dapi,
        'fluorescence_image': gfp,
        'channels_by_name': {'In_Cell [1]': gfp},
        '_primary_channel_name': 'In_Cell',
    }

    resolved = _resolve_image_layer(
        state, 'Upscaled In_Cell',
        fallback=state.get('fluorescence_image', state['image']))
    assert resolved is dapi


def test_resolve_image_layer_disambiguates_primary_from_companion_by_longest_match():
    """"In_Cell" (primary) is a literal substring of "In_Cell [1]" (companion) — napari's own
    duplicate-name suffix. The companion's longer recorded name must win when resolving ITS
    OWN name, even though the shorter primary name also technically matches."""
    from pycat.batch.steps._common import _resolve_image_layer

    dapi = np.full((8, 8), 1, dtype=np.float32)
    gfp = np.full((8, 8), 2, dtype=np.float32)

    state = {
        'image': dapi,
        'fluorescence_image': gfp,
        'channels_by_name': {'In_Cell [1]': gfp},
        '_primary_channel_name': 'In_Cell',
    }

    assert _resolve_image_layer(state, 'Upscaled In_Cell [1]') is gfp
    assert _resolve_image_layer(state, 'Upscaled In_Cell') is dapi


def test_resolve_image_layer_keeps_raw_and_processed_named_channels_separate():
    """The FIFTH bug: cell_analysis is recorded on the RAW name ("Upscaled In_Cell [1]", no
    processing keyword) but runs AFTER preprocessing/background_removal already overwrote the
    companion's channels_by_name entry with the enhanced result -- a single shared slot per name
    cannot tell "raw" and "processed" apart. Raw and processed must resolve independently."""
    from pycat.batch.steps._common import _resolve_image_layer

    raw_gfp = np.full((8, 8), 1.0, dtype=np.float32)
    enhanced_gfp = np.full((8, 8), 99.0, dtype=np.float32)

    state = {
        'image': np.zeros((8, 8), dtype=np.float32),
        '_primary_channel_name': 'In_Cell',
        'channels_by_name': {'In_Cell [1]': raw_gfp},                    # never mutated
        'channels_processed_by_name': {'In_Cell [1]': enhanced_gfp},     # written by bg_removal
    }

    # cell_analysis's recorded name: raw, no processing keyword.
    assert _resolve_image_layer(state, 'Upscaled In_Cell [1]') is raw_gfp
    # condensate_segmentation's recorded name: processed.
    assert _resolve_image_layer(
        state, 'Enhanced Background Removed Pre-Processed Upscaled In_Cell [1]'
    ) is enhanced_gfp
