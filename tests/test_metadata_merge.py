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
        merge_metadata_sources, extract_metadata_merged, _values_conflict)
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
