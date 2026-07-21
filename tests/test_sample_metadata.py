"""**A condition label reaches an image three ways, behind one resolver, and never invents one.**

Nothing in PyCAT can be comparative without a way to say "this image is WT replicate 2 at 10 µM".
`utils/sample_metadata.py` supplies that from a sample sheet, a filename pattern, or an in-app tag,
with explicit precedence (sheet > in-app > filename > nothing) and a **field-by-field** merge, so a
sheet row can name the genotype while the filename fills the dose.

The contract these tests exist to hold: **an absent field stays absent.** No default, no guess. A
fabricated condition is worse than a missing one, because a comparison across it would be silently
wrong — the same honesty contract as the pixel-size gate.

All `core` (pure, headless). Increment 2's consolidated table joins on exactly this.
"""

# Standard library imports
import json

# Third party imports
import pandas as pd
import pytest

# Local application imports
from pycat.utils import sample_metadata as sm

pytestmark = pytest.mark.core


# ── filename parsing ───────────────────────────────────────────────────────────

def test_a_template_extracts_the_named_fields():
    got = sm.parse_filename("WT_rep2_10uM", "{genotype}_rep{replicate}_{dose}uM")
    assert got == {'genotype': 'WT', 'replicate': '2', 'dose': '10'}


def test_no_match_returns_EMPTY_not_a_guess():
    assert sm.parse_filename("unrelated_name", "{genotype}_rep{replicate}_{dose}uM") == {}


def test_the_literal_text_is_ESCAPED_not_treated_as_regex():
    """A real filename has dots and brackets. The template's literal segments must not act as regex,
    or `WT.2` would match `{a}.{b}` in ways nobody asked for."""
    # The '.' between fields is a literal dot, matched literally — 'WTX2' must NOT match.
    assert sm.parse_filename("WTX2", "{a}.{b}") == {}
    assert sm.parse_filename("WT.2", "{a}.{b}") == {'a': 'WT', 'b': '2'}


def test_a_BROKEN_pattern_returns_empty_and_warns_not_crashes(monkeypatch):
    warned = []
    monkeypatch.setattr(sm, '_warn', lambda m: warned.append(m))
    # duplicate field name is rejected by the compiler
    assert sm.parse_filename("a_a", "{x}_{x}") == {}
    assert warned, "a bad pattern should warn, not silently swallow"


def test_a_pattern_with_NO_placeholders_is_rejected(monkeypatch):
    monkeypatch.setattr(sm, '_warn', lambda m: None)
    assert sm.parse_filename("anything", "no_fields_here") == {}


# ── sample sheet ───────────────────────────────────────────────────────────────

def test_a_sheet_maps_stem_to_arbitrary_condition_fields(tmp_path):
    path = tmp_path / "sheet.csv"
    pd.DataFrame({'stem': ['imgA', 'imgB'], 'genotype': ['WT', 'mut'],
                  'dose': ['0', '10']}).to_csv(path, index=False)
    got = sm.load_sample_sheet(path)
    assert got == {'imgA': {'genotype': 'WT', 'dose': '0'},
                   'imgB': {'genotype': 'mut', 'dose': '10'}}


def test_a_FILENAME_column_is_reduced_to_its_stem(tmp_path):
    """A sheet written with extensions still joins — `imgA.tif` keys on `imgA`."""
    path = tmp_path / "sheet.csv"
    pd.DataFrame({'filename': ['imgA.tif'], 'genotype': ['WT']}).to_csv(path, index=False)
    assert sm.load_sample_sheet(path) == {'imgA': {'genotype': 'WT'}}


def test_a_BLANK_cell_is_an_absent_field_not_an_empty_string(tmp_path):
    path = tmp_path / "sheet.csv"
    pd.DataFrame({'stem': ['imgA'], 'genotype': ['WT'], 'dose': ['']}).to_csv(path, index=False)
    assert sm.load_sample_sheet(path) == {'imgA': {'genotype': 'WT'}}   # no 'dose'


def test_a_sheet_with_NO_key_column_warns_and_yields_nothing(tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr(sm, '_warn', lambda m: warned.append(m))
    path = tmp_path / "sheet.csv"
    pd.DataFrame({'genotype': ['WT']}).to_csv(path, index=False)
    assert sm.load_sample_sheet(path) == {}
    assert warned


# ── the resolver: precedence + field-level merge ───────────────────────────────

def test_SHEET_beats_filename_for_a_shared_field():
    """Explicit beats inferred. A sheet's genotype wins over the filename's."""
    r = sm.SampleMetadataResolver(filename_pattern="{genotype}_x")
    # The sheet is keyed on the image's STEM ('WT_from_name_x'), the same key `for_image` looks up.
    r._sheet = {'WT_from_name_x': {'genotype': 'WT_from_sheet'}}
    m = r.for_image("WT_from_name_x.tif")
    assert m.fields['genotype'] == 'WT_from_sheet'
    assert m.field_sources['genotype'] == 'sample_sheet'


def test_fields_MERGE_across_sources():
    """The whole point: a sheet supplies some fields, the filename fills others."""
    r = sm.SampleMetadataResolver(
        filename_pattern="{genotype}_rep{replicate}_{dose}uM",
        in_app_tags={'WT_rep2_10uM': {'treatment': 'drugX'}})
    r._sheet = {'WT_rep2_10uM': {'genotype': 'WT-confirmed'}}

    m = r.for_image("WT_rep2_10uM.tif")
    assert m.fields == {'genotype': 'WT-confirmed', 'treatment': 'drugX',
                        'replicate': '2', 'dose': '10'}
    assert m.field_sources['genotype'] == 'sample_sheet'    # sheet won the shared field
    assert m.field_sources['treatment'] == 'in_app'
    assert m.field_sources['dose'] == 'filename'


def test_IN_APP_beats_filename_but_loses_to_sheet():
    r = sm.SampleMetadataResolver(filename_pattern="{g}_x",
                                  in_app_tags={'a_x': {'g': 'from_app'}})
    r._sheet = {'a_x': {'g': 'from_sheet'}}
    assert r.for_image("a_x.tif").fields['g'] == 'from_sheet'

    r2 = sm.SampleMetadataResolver(filename_pattern="{g}_x",
                                   in_app_tags={'a_x': {'g': 'from_app'}})
    assert r2.for_image("a_x.tif").fields['g'] == 'from_app'   # no sheet -> app wins over filename


def test_NO_source_yields_none():
    m = sm.SampleMetadataResolver().for_image("whatever.tif")
    assert m.fields == {} and m.source == 'none' and m.field_sources == {}


def test_the_SOURCE_is_the_highest_precedence_contributor():
    r = sm.SampleMetadataResolver(filename_pattern="{dose}uM")
    r._sheet = {'10uM': {'genotype': 'WT'}}
    m = r.for_image("10uM.tif")
    assert m.source == 'sample_sheet'     # sheet contributed, so it is the winner even though
    assert 'dose' in m.fields             # the filename also contributed a field


def test_an_UNMATCHED_sheet_row_warns_after_the_batch(monkeypatch):
    warned = []
    monkeypatch.setattr(sm, '_warn', lambda m: warned.append(m))
    r = sm.SampleMetadataResolver()
    r._sheet = {'imgA': {'g': 'WT'}, 'typo_img': {'g': 'mut'}}
    r.for_image("imgA.tif")               # only imgA is seen
    r.warn_unmatched_sheet_rows()
    assert warned and 'typo_img' in warned[0]


# ── manifest persistence (in-app tags round-trip) ──────────────────────────────

def test_in_app_tags_ROUND_TRIP_through_the_manifest():
    """A tag set in the app must travel with the session."""
    tags = {'imgA': {'genotype': 'WT', 'dose': '10'}}
    extra = sm.tags_to_manifest_extra(tags)
    # simulate write_manifest merging `extra`, then read_manifest returning the blob
    manifest = {'manifest_version': 3, **extra}
    assert sm.tags_from_manifest(manifest) == tags


def test_a_manifest_WITHOUT_the_field_loads_as_no_tag():
    """Back-compat: a session saved before this feature is not an error."""
    assert sm.tags_from_manifest({'manifest_version': 2}) == {}
    assert sm.tags_from_manifest(None) == {}


def test_the_persisted_tags_feed_the_resolver():
    """End-to-end: persisted → resolver → resolved condition."""
    manifest = {**sm.tags_to_manifest_extra({'imgA': {'genotype': 'WT'}})}
    tags = sm.tags_from_manifest(manifest)
    r = sm.SampleMetadataResolver(in_app_tags=tags)
    assert r.for_image("imgA.tif").fields == {'genotype': 'WT'}
