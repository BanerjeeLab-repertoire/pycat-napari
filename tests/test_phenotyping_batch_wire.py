"""**The batch wiring that connects the metadata model + the consolidated table into the loop.**

Increments 1 and 2 shipped their pure cores to main. This is the glue: `BatchWorker` builds a resolver
and a `ConsolidatedLongWriter`, and per image resolves the condition and appends the object tables.

It lives on branch `phenotyping-batch-wire` rather than main because it edits the Qt-bound
`BatchWorker`, and the *only* way to fully exercise it is a real batch run — a workflow config, images,
and the viewer — which the authoring session cannot drive. So the tests here cover as much as can be
covered without a live batch:

* `known_fields()` (pure, `core`) — the condition vocabulary the writer's schema is fixed from;
* the two wiring methods (`integration`, offscreen Qt) driven with a **fake `state`**, so the assembly
  from a data repository → consolidated CSV is proven without executing any analysis step.

What remains for a human: run an actual batch with a sample sheet and confirm `consolidated_long.csv`
appears alongside the per-image folders with the right rows. That is the check no fake `state` can make.
"""

# Standard library imports

# Third party imports
import pandas as pd
import pytest

pytestmark = pytest.mark.core       # module default; the Qt tests below override to integration


# ── pure: the vocabulary the writer's schema is built from ─────────────────────

def test_known_fields_is_the_union_of_all_sources():
    from pycat.utils.sample_metadata import SampleMetadataResolver
    r = SampleMetadataResolver(filename_pattern='{genotype}_{dose}uM',
                               in_app_tags={'x': {'treatment': 'drugX'}})
    r._sheet = {'imgA': {'genotype': 'WT', 'replicate': '1'}}
    assert r.known_fields() == ['dose', 'genotype', 'replicate', 'treatment']


def test_known_fields_is_empty_with_no_sources():
    from pycat.utils.sample_metadata import SampleMetadataResolver
    assert SampleMetadataResolver().known_fields() == []


# ── the wiring methods, driven with a fake state (no analysis executed) ────────

def _worker(qtbot, tmp_path, sheet=None, pattern=None, tags=None):
    from pycat.batch_processor import BatchWorker
    return BatchWorker(files=[], config={}, output_dir=tmp_path,
                       step_registry={}, sample_sheet_path=sheet,
                       filename_pattern=pattern, in_app_tags=tags)


class _FakeDataInstance:
    def __init__(self, repo):
        self.data_repository = repo


@pytest.mark.integration
def test_the_wiring_is_INERT_without_a_condition_source(qtbot, tmp_path):
    """No sheet/pattern/tag → no writer, no consolidated file, behaves exactly as before."""
    w = _worker(qtbot, tmp_path)
    resolver, writer = w._build_phenotyping(tmp_path)
    assert resolver is None and writer is None
    # _append is a no-op and must not raise on a None writer
    w._append_to_consolidated(None, None, __import__('pathlib').Path('imgA.tif'), {})
    assert not (tmp_path / 'consolidated_long.csv').exists()


@pytest.mark.integration
def test_a_batch_with_a_pattern_emits_the_consolidated_table(qtbot, tmp_path, monkeypatch):
    """The real path, minus step execution: a resolver + writer are built, and a fake per-image
    `state` is appended, producing `consolidated_long.csv` with the condition joined."""
    import pathlib
    w = _worker(qtbot, tmp_path, pattern='{genotype}_{dose}uM')
    resolver, writer = w._build_phenotyping(tmp_path)
    assert writer is not None

    for stem in ('WT_10uM', 'mut_10uM'):
        state = {'data_instance': _FakeDataInstance(
            {'puncta_df': pd.DataFrame({'object_id': [1, 2], 'area': [10.0, 20.0]}),
             'microns_per_pixel_sq': 0.108})}
        w._append_to_consolidated(writer, resolver, pathlib.Path(f'{stem}.tif'), state)

    path = tmp_path / 'consolidated_long.csv'
    assert path.exists()
    got = pd.read_csv(path)
    assert sorted(got['image_stem'].unique()) == ['WT_10uM', 'mut_10uM']
    assert sorted(got['genotype'].unique()) == ['WT', 'mut']
    assert (got['pixel_size_um'] == 0.108).all()
    assert set(got['object_type']) == {'puncta'}


@pytest.mark.integration
def test_an_image_with_NO_object_tables_is_skipped_cleanly(qtbot, tmp_path):
    """A step that produced no cell/puncta table must not crash the append or write a phantom row."""
    import pathlib
    w = _worker(qtbot, tmp_path, pattern='{genotype}_x')
    resolver, writer = w._build_phenotyping(tmp_path)

    w._append_to_consolidated(writer, resolver,
                              pathlib.Path('WT_x.tif'),
                              {'data_instance': _FakeDataInstance({})})   # empty repo
    assert writer.n_rows == 0


@pytest.mark.integration
def test_a_broken_append_does_NOT_fail_the_image(qtbot, tmp_path):
    """A consolidated-table hiccup must never take down an image that otherwise processed — the
    append swallows its own errors and prints, rather than raising into the batch loop."""
    import pathlib
    w = _worker(qtbot, tmp_path, pattern='{g}_x')
    resolver, writer = w._build_phenotyping(tmp_path)

    # a `state` whose data_instance is malformed — must not raise
    w._append_to_consolidated(writer, resolver, pathlib.Path('WT_x.tif'),
                              {'data_instance': object()})
    assert writer.n_rows == 0
