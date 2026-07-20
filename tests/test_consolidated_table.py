"""**One tidy table for a whole batch — the comparative-phenotyping keystone.**

Increment 2. Batch produced one folder per image and nothing at the top level, so a comparative study
was N folders joined by hand. This builds one long-format table with the condition labels (increment
1) and provenance joined per row, streamed so a big batch holds no image but the current one.

These pin the properties the roadmap's deliverable names: **long (tidy) format** (one
measurement/value/units triple per row), **correct condition join**, **provenance per row**, **N
images → one table**, and **no memory blowup** (the writer accumulates counters, not rows). The pure
builder is tested without a disk or a batch loop, because assembly correctness is the part that must
not be trusted to a GUI run.
"""

# Standard library imports

# Third party imports
import numpy as np
import pandas as pd
import pytest

# Local application imports
from pycat.utils.consolidated_table import (melt_object_measurements, build_image_long_table,
                                            consolidated_columns, ConsolidatedLongWriter)
from pycat.utils.sample_metadata import SampleMetadata

pytestmark = pytest.mark.core


def _wide(n=2):
    return pd.DataFrame({'object_id': list(range(1, n + 1)),
                         'area': [10.0 * i for i in range(1, n + 1)],
                         'intensity': [100.0 * i for i in range(1, n + 1)]})


# ── melt: wide → long ──────────────────────────────────────────────────────────

def test_melt_gives_ONE_row_per_object_per_measurement():
    """Tidy: 2 objects × 2 measurements = 4 rows, each a single value."""
    long = melt_object_measurements(_wide(2), 'punctum')
    assert len(long) == 4
    assert set(long['measurement']) == {'area', 'intensity'}
    assert list(long.columns) == ['object_type', 'object_id', 'entity_id', 'measurement', 'value', 'units']


def test_units_are_carried_and_UNLISTED_ones_stay_blank():
    """An unlisted measurement gets '', not a guessed unit."""
    long = melt_object_measurements(_wide(1), 'punctum', units={'area': 'um2'})
    assert long.loc[long.measurement == 'area', 'units'].iloc[0] == 'um2'
    assert long.loc[long.measurement == 'intensity', 'units'].iloc[0] == ''


def test_only_the_named_VALUE_COLS_are_melted():
    """A bbox coordinate is numeric but not a measurement; the caller can exclude it."""
    df = _wide(2).assign(bbox_x0=[5, 6])
    long = melt_object_measurements(df, 'punctum', value_cols=['area', 'intensity'])
    assert 'bbox_x0' not in set(long['measurement'])


def test_an_EMPTY_frame_melts_to_no_rows_not_an_error():
    long = melt_object_measurements(pd.DataFrame(), 'punctum')
    assert len(long) == 0 and 'measurement' in long.columns


# ── build_image_long_table: condition + provenance join ────────────────────────

def test_the_CONDITION_labels_are_joined_per_row():
    sm = SampleMetadata(fields={'genotype': 'WT', 'dose': '10'}, source='sample_sheet')
    tbl = build_image_long_table([('punctum', _wide(2))], image_stem='imgA',
                                 sample_metadata=sm, condition_fields=['genotype', 'dose'])
    assert (tbl['genotype'] == 'WT').all()
    assert (tbl['dose'] == '10').all()
    assert (tbl['image_stem'] == 'imgA').all()


def test_an_ABSENT_condition_field_is_BLANK_not_fabricated():
    """The honesty contract, in the table: an image with no dose has a blank dose cell, never a 0."""
    sm = SampleMetadata(fields={'genotype': 'WT'}, source='filename')      # no dose
    tbl = build_image_long_table([('punctum', _wide(1))], image_stem='x',
                                 sample_metadata=sm, condition_fields=['genotype', 'dose'])
    assert (tbl['dose'] == '').all()


def test_PROVENANCE_travels_per_row():
    tbl = build_image_long_table([('punctum', _wide(2))], image_stem='x',
                                 provenance={'pixel_size_um': 0.108, 'pycat_version': '1.6.95'},
                                 condition_fields=[])
    assert (tbl['pixel_size_um'] == 0.108).all()
    assert (tbl['pycat_version'] == '1.6.95').all()


def test_the_column_ORDER_is_canonical_and_stable():
    cols = consolidated_columns(['genotype', 'dose'])
    tbl = build_image_long_table([('punctum', _wide(1))], image_stem='x',
                                 sample_metadata=SampleMetadata({'genotype': 'WT', 'dose': '1'}),
                                 condition_fields=['genotype', 'dose'])
    assert list(tbl.columns) == cols
    assert cols[:3] == ['image_stem', 'genotype', 'dose']
    assert 'measurement' in cols and 'value' in cols and 'units' in cols


def test_MULTIPLE_object_types_land_in_one_table():
    """Cells and puncta from one image are rows in the same table, distinguished by object_type."""
    cells = pd.DataFrame({'object_id': [1], 'cell_area': [500.0]})
    tbl = build_image_long_table([('punctum', _wide(2)), ('cell', cells)],
                                 image_stem='x', condition_fields=[])
    assert set(tbl['object_type']) == {'punctum', 'cell'}


# ── the streaming writer: N images → one table, no blowup ──────────────────────

def test_N_images_become_ONE_table_with_the_right_join(tmp_path):
    path = tmp_path / 'consolidated_long.csv'
    w = ConsolidatedLongWriter(path, condition_fields=['genotype'])
    for stem, geno in (('imgA', 'WT'), ('imgB', 'mut'), ('imgC', 'WT')):
        w.add_image(stem, [('punctum', _wide(2))],
                    sample_metadata=SampleMetadata({'genotype': geno}))

    got = pd.read_csv(path)
    assert sorted(got['image_stem'].unique()) == ['imgA', 'imgB', 'imgC']
    assert sorted(got['genotype'].unique()) == ['WT', 'mut']
    # each image: 2 objects × 2 measurements = 4 rows -> 12 total
    assert len(got) == 12
    # the join is correct: every imgB row is 'mut'
    assert (got.loc[got.image_stem == 'imgB', 'genotype'] == 'mut').all()


def test_the_writer_holds_NO_image_in_memory(tmp_path):
    """'no memory blowup': the writer keeps counters, not the rows. A 200-image batch must not
    accumulate. Asserted structurally — there is no row buffer to grow."""
    w = ConsolidatedLongWriter(tmp_path / 'c.csv', condition_fields=['g'])
    for i in range(5):
        w.add_image(f'img{i}', [('punctum', _wide(3))], sample_metadata=SampleMetadata({'g': 'x'}))

    # No attribute holds accumulated rows/frames — only scalar counters.
    for attr, val in vars(w).items():
        assert not isinstance(val, pd.DataFrame), f"the writer buffers a DataFrame in {attr}"
    assert w.n_images == 5 and w.n_rows == 5 * 3 * 2


def test_the_HEADER_is_written_once(tmp_path):
    path = tmp_path / 'c.csv'
    w = ConsolidatedLongWriter(path, condition_fields=['g'])
    w.add_image('a', [('punctum', _wide(1))], sample_metadata=SampleMetadata({'g': 'x'}))
    w.add_image('b', [('punctum', _wide(1))], sample_metadata=SampleMetadata({'g': 'y'}))

    lines = path.read_text().strip().splitlines()
    assert lines[0].startswith('image_stem,')
    assert sum(1 for l in lines if l.startswith('image_stem,')) == 1     # header once only


def test_the_SCHEMA_is_stable_even_when_an_image_lacks_a_field(tmp_path):
    """Streaming append is only safe if columns never drift. An image missing a declared condition
    field still writes the full schema (blank cell), so row counts and columns line up."""
    path = tmp_path / 'c.csv'
    w = ConsolidatedLongWriter(path, condition_fields=['genotype', 'dose'])
    w.add_image('a', [('punctum', _wide(1))],
                sample_metadata=SampleMetadata({'genotype': 'WT', 'dose': '10'}))
    w.add_image('b', [('punctum', _wide(1))],
                sample_metadata=SampleMetadata({'genotype': 'mut'}))     # no dose

    got = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert list(got.columns) == consolidated_columns(['genotype', 'dose'])
    assert got.loc[got.image_stem == 'b', 'dose'].iloc[0] == ''          # blank, aligned


def test_it_is_ADDITIVE_a_measurement_VALUE_survives_the_round_trip(tmp_path):
    """The numbers must arrive intact — this table is what a manuscript reads."""
    path = tmp_path / 'c.csv'
    w = ConsolidatedLongWriter(path, condition_fields=['g'])
    w.add_image('a', [('punctum', _wide(2))], sample_metadata=SampleMetadata({'g': 'x'}))

    got = pd.read_csv(path)
    areas = got.loc[got.measurement == 'area', 'value'].tolist()
    assert sorted(areas) == [10.0, 20.0]      # the two objects' areas, intact


# ── the pure extractor the batch loop will call ────────────────────────────────

def test_records_from_repository_finds_the_object_tables():
    from pycat.utils.consolidated_table import records_from_data_repository
    repo = {'cell_df': pd.DataFrame({'object_id': [1], 'cell_area': [500.0]}),
            'puncta_df': _wide(2),
            'timing_df': pd.DataFrame({'step': ['a'], 'seconds': [1.0]})}   # NOT an object table
    recs = records_from_data_repository(repo)
    types = {t for t, _ in recs}
    assert types == {'cell', 'puncta'}          # timing_df excluded (not on the allowlist)


def test_records_skips_absent_and_empty_tables():
    from pycat.utils.consolidated_table import records_from_data_repository
    assert records_from_data_repository({}) == []
    assert records_from_data_repository({'cell_df': pd.DataFrame()}) == []   # empty -> skipped


def test_the_extractor_feeds_the_writer_end_to_end(tmp_path):
    """The exact path the batch loop takes: repository -> records -> add_image."""
    from pycat.utils.consolidated_table import records_from_data_repository
    repo = {'puncta_df': _wide(2)}
    path = tmp_path / 'c.csv'
    w = ConsolidatedLongWriter(path, condition_fields=['genotype'])
    w.add_image('imgA', records_from_data_repository(repo),
                sample_metadata=SampleMetadata({'genotype': 'WT'}))

    got = pd.read_csv(path)
    assert len(got) == 4 and (got['genotype'] == 'WT').all()
    assert set(got['object_type']) == {'puncta'}
