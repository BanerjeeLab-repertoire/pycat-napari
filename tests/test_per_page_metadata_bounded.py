"""**Per-page TIFF metadata is sampled bounded at load, never scanned page-by-page (deep_metadata item 6).**

A time-lapse TIFF carries its true cadence in a per-page ``MicroManagerMetadata`` tag. Reading EVERY page of a
10k-frame stack just to open it is the cost this forbids: the load path samples a bounded prefix
(`_LOAD_PAGE_SAMPLE_CAP`), which gives the same median cadence for a constant-rate acquisition, while a caller
that genuinely needs every plane's record passes ``max_pages=None`` (the on-demand full read). These pin the
bound and that the bounded sample still recovers the interval.
"""
import numpy as np
import pytest

# Guarded import (kept out of module top-level) so the headless collector does not skip this module on the
# `pycat.file_io` prefix — metadata_extract is pure/headless-safe. Same pattern as test_metadata_merge.
try:
    from pycat.file_io.metadata_extract import (
        _extract_mm_frame_times_from_tiff, extract_tiff_metadata, _LOAD_PAGE_SAMPLE_CAP)
except Exception:      # pragma: no cover - only when the io stack is truly unavailable
    pytest.skip("pycat.file_io.metadata_extract unavailable", allow_module_level=True)

pytestmark = pytest.mark.core


def _write_mm_timelapse(path, n_pages, interval_ms=500.0):
    """A multipage TIFF whose pages carry a MicroManager ``ElapsedTime-ms`` at a constant cadence."""
    import json
    import tifffile
    with tifffile.TiffWriter(str(path)) as tw:
        for i in range(n_pages):
            mm = json.dumps({'ElapsedTime-ms': i * interval_ms, 'Exposure-ms': 50.0})
            tw.write(np.zeros((8, 8), np.uint16),
                     extratags=[(51123, 's', 0, mm, True)], contiguous=False)


def test_the_load_path_samples_a_bounded_prefix_not_every_page(tmp_path):
    p = tmp_path / 'big_timelapse.tif'
    n = _LOAD_PAGE_SAMPLE_CAP * 3 + 7                       # comfortably larger than the cap
    _write_mm_timelapse(p, n)
    raw = extract_tiff_metadata(str(p))['raw']
    assert int(raw['frame_times_pages_sampled']) == _LOAD_PAGE_SAMPLE_CAP    # bounded — did NOT read all n
    assert int(raw['frame_times_pages_total']) == n                          # the true page count is still known


def test_the_bounded_load_sample_still_recovers_the_cadence(tmp_path):
    p = tmp_path / 'cadence.tif'
    _write_mm_timelapse(p, _LOAD_PAGE_SAMPLE_CAP * 2, interval_ms=500.0)
    common = extract_tiff_metadata(str(p))['common']
    assert common['frame_interval_s'] == pytest.approx(0.5, rel=1e-6)         # 500 ms, from the prefix alone


def test_a_full_read_is_available_on_demand(tmp_path):
    p = tmp_path / 'full.tif'
    n = _LOAD_PAGE_SAMPLE_CAP * 2
    _write_mm_timelapse(p, n)
    full = _extract_mm_frame_times_from_tiff(str(p), max_pages=None)          # explicit: read everything
    assert full['pages_sampled'] == n and full['pages_total'] == n


def test_a_small_stack_reads_only_what_exists_not_the_whole_cap(tmp_path):
    p = tmp_path / 'small.tif'
    _write_mm_timelapse(p, 5)
    out = _extract_mm_frame_times_from_tiff(str(p), max_pages=_LOAD_PAGE_SAMPLE_CAP)
    assert out['pages_sampled'] == 5                        # min(5, cap) — never more pages than the file has
