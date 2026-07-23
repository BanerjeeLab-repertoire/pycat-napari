"""**Metadata contradictions — detected, severity-graded, cry-wolf-clean, and anti-numbing per pattern.**

Pins the tag_confidence Part 3/4 engine: an oil-vs-air objective inconsistency is a CRITICAL contradiction
named concretely; a declared-vs-pixel modality mismatch is INFO (metadata wins, recorded); **a clean file
raises ZERO** (the cry-wolf contract); and the anti-numbing store demotes a pattern the user marked
"expected" — keyed to the acquisition fingerprint, never the file, reversibly, per-pattern only, with a
developer precision signal when a rule is dismissed across many fingerprints.
"""
import pytest

from pycat.utils.user_settings import UserSettings
from pycat.utils.metadata_contradictions import (
    Contradiction, detect_contradictions, has_critical, acquisition_fingerprint,
    is_expected, mark_expected, apply_expectations, rules_dismissed_across_many_fingerprints)

pytestmark = pytest.mark.core


def test_oil_vs_air_immersion_is_a_CRITICAL_contradiction_named_concretely():
    md = {'immersion': 'Oil', 'medium': 'Air', 'refractive_index': 1.518}
    cs = detect_contradictions(md)
    assert len(cs) == 1 and cs[0].pattern == 'immersion_vs_medium' and cs[0].severity == 'critical'
    assert 'Oil immersion' in cs[0].message and 'Air medium' in cs[0].message
    assert 'RI 1.518 indicates oil' in cs[0].message           # the RI cross-check names the real one
    assert has_critical(cs)


def test_a_declared_vs_pixel_modality_mismatch_is_INFO_metadata_wins():
    cs = detect_contradictions({'modality': 'Widefield Fluorescence'}, pixel_modality='brightfield')
    assert len(cs) == 1 and cs[0].pattern == 'modality_vs_pixels' and cs[0].severity == 'info'
    assert 'Metadata wins' in cs[0].message
    assert not has_critical(cs)                                # info alone never raises the red flag


def test_a_CLEAN_file_raises_ZERO_contradictions_the_cry_wolf_test():
    clean = {'immersion': 'Oil', 'medium': 'Oil', 'refractive_index': 1.518,
             'modality': 'Fluorescence', 'objective': '63x'}
    assert detect_contradictions(clean, pixel_modality='fluorescence') == []
    # partial/absent fields must not manufacture a contradiction either
    assert detect_contradictions({}) == []
    assert detect_contradictions({'immersion': 'Oil'}) == []   # no medium to disagree with


def test_wording_differences_are_not_a_modality_contradiction():
    # 'widefield fluorescence' vs 'fluorescence' are the SAME optical category — no cry-wolf
    assert detect_contradictions({'modality': 'widefield fluorescence'},
                                 pixel_modality='fluorescence') == []


def _store(tmp_path):
    return UserSettings(path=tmp_path / 's.json')


def test_marking_a_pattern_expected_demotes_it_to_info_reversibly(tmp_path):
    store = _store(tmp_path)
    md = {'immersion': 'Oil', 'medium': 'Air', 'refractive_index': 1.518,
          'instrument': 'Zeiss', 'software': 'ZEN', 'objective': '63x'}
    fp = acquisition_fingerprint(md)
    cs = detect_contradictions(md)
    assert has_critical(cs)                                    # critical before marking

    mark_expected('immersion_vs_medium', fp, store)
    assert is_expected('immersion_vs_medium', fp, store)
    demoted = apply_expectations(cs, fp, store)
    assert not has_critical(demoted)                           # demoted to info → no red flag
    assert 'expected for this instrument' in demoted[0].message
    # reversible
    mark_expected('immersion_vs_medium', fp, store, expected=False)
    assert has_critical(apply_expectations(detect_contradictions(md), fp, store))


def test_expected_is_keyed_to_the_fingerprint_NOT_the_file():
    # two files from the SAME acquisition share a fingerprint; a different objective is a different one
    a = acquisition_fingerprint({'instrument': 'Zeiss', 'software': 'ZEN', 'objective': '63x'})
    b = acquisition_fingerprint({'instrument': 'Zeiss', 'software': 'ZEN', 'objective': '63x'})
    c = acquisition_fingerprint({'instrument': 'Zeiss', 'software': 'ZEN', 'objective': '20x'})
    assert a == b and a != c


def test_suppression_is_per_pattern_a_second_pattern_still_fires(tmp_path):
    store = _store(tmp_path)
    md = {'immersion': 'Oil', 'medium': 'Air', 'refractive_index': 1.518,
          'modality': 'Fluorescence', 'instrument': 'Z', 'software': 'ZEN', 'objective': '63x'}
    fp = acquisition_fingerprint(md)
    mark_expected('immersion_vs_medium', fp, store)
    cs = apply_expectations(detect_contradictions(md, pixel_modality='brightfield'), fp, store)
    # the immersion one is demoted, but the modality one is untouched (no 'ignore all')
    by = {c.pattern: c.severity for c in cs}
    assert by['immersion_vs_medium'] == 'info' and by['modality_vs_pixels'] == 'info'
    assert {c.pattern for c in detect_contradictions(md, pixel_modality='brightfield')} == \
           {'immersion_vs_medium', 'modality_vs_pixels'}       # both still detected pre-demotion


def test_a_rule_dismissed_across_many_fingerprints_is_flagged_to_the_developer(tmp_path):
    store = _store(tmp_path)
    for obj in ('63x', '20x', '40x', '100x'):
        fp = acquisition_fingerprint({'instrument': 'Zeiss', 'software': 'ZEN', 'objective': obj})
        mark_expected('immersion_vs_medium', fp, store)
    flagged = rules_dismissed_across_many_fingerprints(store, threshold=3)
    assert flagged.get('immersion_vs_medium') == 4            # a probable rule bug, surfaced not absorbed
    assert 'modality_vs_pixels' not in flagged
