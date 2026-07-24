"""**Metadata comes from wherever it parses best — not from whichever reader shows the pixels best.**

Part 2 of deep_metadata_and_naming. `merge_metadata_sources` merges several metadata sources by field-level
precedence, recording WHERE each field came from and every DISAGREEMENT (a two-source conflict is a finding —
both values + winner + reason, never silently resolved; a `pixel_size_um` conflict is surfaced so the
pixel-size gate sees it). `extract_metadata_merged` orchestrates it and — the point of the exercise — skips a
FAILING source with a recorded reason instead of the old bare `except: pass` that swallowed it.
"""
import pytest

# Guarded import (kept out of module top-level) so the headless core collector does not skip this module on
# the `pycat.file_io` prefix — metadata_extract is pure/headless-safe. See test_ome_xml_scoped_parse.
try:
    from pycat.file_io.metadata_extract import (
        merge_metadata_sources, extract_metadata_merged, extract_metadata,
        _default_metadata_sources, _values_conflict)
except Exception:      # pragma: no cover - only when the io stack is truly unavailable
    pytest.skip("pycat.file_io.metadata_extract unavailable", allow_module_level=True)

pytestmark = pytest.mark.core


def test_field_level_precedence_first_meaningful_source_wins_and_is_recorded():
    # tifffile has the pixel size; the OME reader has the NA. Each field takes its best source.
    tiff = {'pixel_size_um': 0.1035, 'numerical_aperture': None, 'objective': None}
    ome = {'pixel_size_um': None, 'numerical_aperture': 1.4, 'objective': 'Plan-Apo 63x'}
    res = merge_metadata_sources([('tifffile', tiff), ('ome', ome)])
    assert res['common']['pixel_size_um'] == 0.1035 and res['sources']['pixel_size_um'] == 'tifffile'
    assert res['common']['numerical_aperture'] == 1.4 and res['sources']['numerical_aperture'] == 'ome'
    assert res['common']['objective'] == 'Plan-Apo 63x' and res['sources']['objective'] == 'ome'
    assert res['conflicts'] == []          # no field had two DIFFERENT meaningful values


def test_a_placeholder_is_not_meaningful_so_the_next_source_wins():
    # 'N/A' looks authoritative but says nothing — the meaningful value from the lower source must win.
    a = {'objective': 'N/A'}
    b = {'objective': '63x Oil'}
    res = merge_metadata_sources([('a', a), ('b', b)])
    assert res['common']['objective'] == '63x Oil' and res['sources']['objective'] == 'b'


def test_a_two_source_conflict_is_recorded_with_both_values_winner_and_reason():
    a = {'pixel_size_um': 0.1035}
    b = {'pixel_size_um': 0.2070}          # a genuinely different scale — a finding, not a merge
    res = merge_metadata_sources([('a', a), ('b', b)])
    assert res['common']['pixel_size_um'] == 0.1035           # precedence still resolves the value
    assert len(res['conflicts']) == 1
    c = res['conflicts'][0]
    assert c['field'] == 'pixel_size_um' and c['winner'] == 0.1035 and c['winner_source'] == 'a'
    assert c['values'] == {'a': 0.1035, 'b': 0.2070}
    assert "'a'" in c['reason'] and "'b'" in c['reason']
    assert c['surfaced'] is True                              # pixel size conflict must reach the gate


def test_rounding_differences_are_not_a_conflict():
    a = {'pixel_size_um': 0.1035}
    b = {'pixel_size_um': 0.10350001}      # the same reading to any physical precision
    res = merge_metadata_sources([('a', a), ('b', b)])
    assert res['conflicts'] == []


def test_a_non_surfaced_field_conflict_is_recorded_but_not_surfaced():
    a = {'objective': '63x Oil'}
    b = {'objective': '20x Air'}
    res = merge_metadata_sources([('a', a), ('b', b)])
    assert len(res['conflicts']) == 1 and res['conflicts'][0]['surfaced'] is False


def test_values_conflict_numeric_string_and_other():
    assert _values_conflict(0.1, 0.2) and not _values_conflict(1.0, 1.0)
    assert not _values_conflict('Oil', 'oil ') and _values_conflict('Oil', 'Air')
    assert not _values_conflict(0, 0)


# ── orchestration: failing source recorded, not swallowed ─────────────────────────────────────────

def test_a_failing_source_is_skipped_with_a_recorded_reason_the_others_still_merge():
    def good():
        return {'common': {'pixel_size_um': 0.1035}, 'raw': {'tag': 1}}

    def broken():
        raise ValueError("reader could not open the file")

    res = extract_metadata_merged('x.tif', sources=[('good', good), ('broken', broken)])
    assert res['common']['pixel_size_um'] == 0.1035                    # the surviving source still lands
    fails = res['raw']['source_failures']
    assert len(fails) == 1 and fails[0]['source'] == 'broken'
    assert 'ValueError' in fails[0]['error'] and 'could not open' in fails[0]['error']


def test_the_merge_trail_and_per_source_raw_are_preserved():
    def tiff():
        return {'common': {'pixel_size_um': 0.1035}, 'raw': {'ImageDescription': 'ImageJ'}}

    def ome():
        return {'common': {'numerical_aperture': 1.4}, 'raw': {'ome_metadata': '<OME/>'}}

    res = extract_metadata_merged('x.tif', sources=[('tiff', tiff), ('ome', ome)])
    assert res['raw']['metadata_sources']['pixel_size_um'] == 'tiff'
    assert res['raw']['metadata_sources']['numerical_aperture'] == 'ome'
    assert res['raw']['raw_by_source']['tiff']['ImageDescription'] == 'ImageJ'
    assert res['raw']['raw_by_source']['ome']['ome_metadata'] == '<OME/>'
    assert 'source_failures' not in res['raw']                         # no failures → key absent, not empty


# ── the dispatcher now routes through the merge (the Part 2 wiring the built machinery was missing) ──────

def test_merged_raw_preserves_top_level_source_keys_for_consumers():
    # metadata_contradictions reads raw['channels'] / raw['instrument'] at top level, and
    # _fill_scan_acquisition_fields scans flat raw tags — the merge must carry them, not bury them.
    def structured():
        return {'common': {}, 'raw': {'channels': [{'index': 0}], 'instrument': {'lens_na': 1.4}}}

    def tags():
        return {'common': {}, 'raw': {'ImageDescription': 'ImageJ'}}

    res = extract_metadata_merged('x.tif', sources=[('structured', structured), ('tags', tags)])
    assert res['raw']['channels'] == [{'index': 0}]                    # top-level, where the consumer reads it
    assert res['raw']['instrument'] == {'lens_na': 1.4}
    assert res['raw']['ImageDescription'] == 'ImageJ'
    assert res['raw']['raw_by_source']['structured']['channels']       # still available per-source too


def test_source_precedence_preserves_the_open_readers_metadata_as_primary():
    # The pixel-size caution: with an open handle the reader's own metadata must stay HIGHEST precedence, so
    # no currently-correct value can move; tifffile is only a gap-filler behind it.
    with_handle = [n for n, _ in _default_metadata_sources('x.tif', image=object())]
    assert with_handle[0] == 'ome_structured' and 'tifffile' in with_handle
    no_handle = [n for n, _ in _default_metadata_sources('x.tif')]
    assert no_handle[0] == 'tifffile'                                  # no handle → tifffile primary (as before)
    assert [n for n, _ in _default_metadata_sources('x.czi')] == ['ome_structured']
    assert [n for n, _ in _default_metadata_sources('x.ims')] == ['ims_hdf5']


def test_the_dispatcher_routes_through_the_merge_and_records_failures_not_swallows():
    # extract_metadata (every load path's entry) now goes through the merge: its result carries the merge
    # trail, and a source that fails on a missing file is RECORDED, not swallowed by a bare except.
    res = extract_metadata('this_file_does_not_exist.tif')
    assert 'metadata_sources' in res['raw']                            # proof it went through the merge
    assert 'raw_by_source' in res['raw']
