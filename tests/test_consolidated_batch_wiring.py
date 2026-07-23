"""**Comparative phenotyping inc 2, wired: the batch emits one tidy `consolidated_long.csv`.**

The `ConsolidatedLongWriter` (the long-format assembler) shipped with its own unit tests, but nothing
called it from the batch loop — so the keystone deliverable, "a study across N images is one top-level
table, not N folders joined by hand," was unbuilt. These tests cover the wiring: reading each image's
already-written per-image CSVs, streaming them into one table with condition + provenance columns per
row, and the batch loop actually invoking it.

Headless: the assembly is pure (temp dirs, no batch loop); the batch loop's call is AST-verified because
`batch_processor` imports Qt and cannot be imported in the core suite.
"""

import ast
import pathlib

import pandas as pd
import pytest

pytestmark = pytest.mark.base


def _write_cell_df(output_dir, stem):
    pd.DataFrame({'object_id': [1, 2], 'area': [100.0, 150.0],
                  'mean_intensity': [500.0, 600.0]}).to_csv(
        pathlib.Path(output_dir) / f"{stem}_cell_df.csv", index=False)


def test_records_from_output_dir_reads_the_per_image_csvs(tmp_path):
    from pycat.utils.consolidated_table import records_from_output_dir
    _write_cell_df(tmp_path, 'cellA')

    records = records_from_output_dir(tmp_path, 'cellA')
    assert len(records) == 1
    object_type, df = records[0]
    assert object_type == 'cell'                      # 'cell_df' -> 'cell'
    assert list(df.columns) == ['object_id', 'area', 'mean_intensity'] and len(df) == 2


def test_records_from_output_dir_skips_missing_and_empty(tmp_path):
    from pycat.utils.consolidated_table import records_from_output_dir
    # no CSVs at all -> no records, no crash
    assert records_from_output_dir(tmp_path, 'nope') == []
    # an empty puncta table is skipped, not melted into zero-row noise
    pd.DataFrame(columns=['object_id', 'x']).to_csv(tmp_path / 'cellA_puncta_df.csv', index=False)
    assert records_from_output_dir(tmp_path, 'cellA') == []


def test_condition_field_names_unions_sheet_and_pattern(tmp_path):
    from pycat.utils.sample_metadata import SampleMetadataResolver
    sheet = tmp_path / 'sheet.csv'
    sheet.write_text("stem,genotype,dose\ncellA,WT,10\n", encoding='utf-8')
    r = SampleMetadataResolver(sheet_path=str(sheet), filename_pattern='{genotype}_rep{replicate}')
    # sheet columns (genotype, dose) unioned with pattern fields (genotype, replicate), sorted.
    assert r.condition_field_names() == ['dose', 'genotype', 'replicate']


def test_the_batch_STREAMS_one_long_table_with_conditions_per_row(tmp_path):
    """The keystone: two images' per-object measurements become one long table, each row tagged with
    its image's condition — the manual N-folder join, done for you."""
    from pycat.utils.consolidated_table import ConsolidatedLongWriter, records_from_output_dir
    from pycat.utils.sample_metadata import SampleMetadataResolver

    sheet = tmp_path / 'sheet.csv'
    sheet.write_text("stem,genotype\ncellA,WT\ncellB,mut\n", encoding='utf-8')
    resolver = SampleMetadataResolver(sheet_path=str(sheet))

    for stem in ('cellA', 'cellB'):
        odir = tmp_path / stem
        odir.mkdir()
        _write_cell_df(odir, stem)

    writer = ConsolidatedLongWriter(tmp_path / 'consolidated_long.csv',
                                    resolver.condition_field_names())
    for stem in ('cellA', 'cellB'):
        writer.add_image(stem, records_from_output_dir(tmp_path / stem, stem),
                         sample_metadata=resolver.for_image(f"{stem}.tif"),
                         provenance={'pycat_version': '1.6.test'})

    out = pd.read_csv(tmp_path / 'consolidated_long.csv')
    assert len(out) == 8, "2 images × 2 objects × 2 measurements"          # streamed, not held
    assert set(out['measurement']) == {'area', 'mean_intensity'}
    assert set(out.loc[out.image_stem == 'cellA', 'genotype']) == {'WT'}
    assert set(out.loc[out.image_stem == 'cellB', 'genotype']) == {'mut'}
    assert list(out.columns)[:6] == ['image_stem', 'genotype', 'object_type', 'object_id',
                                     'entity_id', 'measurement']
    assert set(out['pycat_version']) == {'1.6.test'}                       # provenance per row


def test_an_image_with_no_object_tables_contributes_no_rows(tmp_path):
    from pycat.utils.consolidated_table import ConsolidatedLongWriter, records_from_output_dir
    (tmp_path / 'empty').mkdir()
    writer = ConsolidatedLongWriter(tmp_path / 'consolidated_long.csv', [])
    n = writer.add_image('empty', records_from_output_dir(tmp_path / 'empty', 'empty'))
    assert n == 0, "an image that produced no object tables adds no rows"


def test_the_batch_loop_WIRES_the_consolidated_writer():
    """AST: `batch_processor` (Qt-bound, not importable headless) must build the writer and stream each
    image into it — a writer that exists but is never called from the loop is the gap this closes."""
    src = (pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat'
           / 'batch_processor.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    names = {getattr(c.func, 'id', None) or getattr(c.func, 'attr', None)
             for c in ast.walk(tree) if isinstance(c, ast.Call)}
    assert 'ConsolidatedLongWriter' in names, "the batch loop never constructs the consolidated writer"
    assert 'records_from_output_dir' in names, "the batch loop never reads the per-image tables"
    assert 'add_image' in names, "the batch loop never streams an image into the consolidated table"


def test_records_have_scored_family_gates_the_reliability_qc():
    """Only images carrying a scored-family measurement (partition/concentration/ΔG) trigger the batch's
    imaging-QC computation — a non-partition batch pays no QC cost."""
    from pycat.utils.consolidated_table import records_have_scored_family
    partition = pd.DataFrame({'object_id': [1], 'partition_coefficient': [4.2]})
    plain = pd.DataFrame({'object_id': [1], 'area': [12.0], 'intensity': [100.0]})
    assert records_have_scored_family([('droplet', partition)]) is True
    assert records_have_scored_family([('cell', plain)]) is False
    assert records_have_scored_family([]) is False


def test_the_batch_loop_COMPUTES_reliability_only_for_scored_family_images():
    """AST: the batch consolidation must (a) gate on `records_have_scored_family`, (b) compute `run_full_qc`
    for those images, and (c) pass a `reliability_context` to `add_image` — so the reliability columns
    actually populate in real exports (not only when a test supplies a context)."""
    src = (pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat'
           / 'batch_processor.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    called = {getattr(c.func, 'id', None) or getattr(c.func, 'attr', None)
              for c in ast.walk(tree) if isinstance(c, ast.Call)}
    assert 'records_have_scored_family' in called, "reliability QC is not gated to scored-family images"
    assert 'run_full_qc' in called, "the batch never computes the imaging-QC reliability factor"
    add_image_calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call)
                       and getattr(c.func, 'attr', None) == 'add_image']
    assert any(k.arg == 'reliability_context' for c in add_image_calls for k in c.keywords), \
        "add_image is never passed a reliability_context — the columns stay blank in real batches"
