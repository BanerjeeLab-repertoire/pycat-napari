"""**Comparative phenotyping inc 1, Parts B & C: the resolver, wired.**

Part A (the `SampleMetadataResolver`) shipped and is tested in `test_sample_metadata.py`. These tests
cover the two integration points that make it *do* something:

- **Part B — batch.** `write_image_sample_metadata` attaches a per-image condition file when a source is
  configured, and is a strict no-op when none is (the additivity guarantee: a metadata-less batch writes
  exactly what it did before). The batch loop wiring is verified by AST, because `batch_processor`
  imports Qt and cannot be imported in the headless core suite.
- **Part C — session.** An in-app tag placed in the data repository round-trips through Save & Clear →
  Load Session, restored into a fresh repository; a manifest written before the field loads as "no tag".
"""

import ast
import json
import pathlib

import pytest

pytestmark = pytest.mark.core


# ── Part B: the per-image write helper + config-driven resolver ─────────────────────────────

def test_write_image_sample_metadata_records_the_condition(tmp_path):
    from pycat.utils.sample_metadata import SampleMetadataResolver, write_image_sample_metadata

    sheet = tmp_path / 'sheet.csv'
    sheet.write_text("stem,genotype,dose\ncellA,WT,10\n", encoding='utf-8')
    resolver = SampleMetadataResolver(sheet_path=str(sheet))

    out = write_image_sample_metadata(resolver, 'C:/data/cellA.tif', tmp_path)

    assert out is not None and out.name == 'cellA_sample_metadata.json'
    doc = json.loads(out.read_text(encoding='utf-8'))
    assert doc['fields'] == {'genotype': 'WT', 'dose': '10'}
    assert doc['source'] == 'sample_sheet'


def test_an_image_matching_no_row_still_writes_an_honest_empty_answer(tmp_path):
    from pycat.utils.sample_metadata import SampleMetadataResolver, write_image_sample_metadata

    sheet = tmp_path / 'sheet.csv'
    sheet.write_text("stem,genotype\ncellA,WT\n", encoding='utf-8')
    resolver = SampleMetadataResolver(sheet_path=str(sheet))

    out = write_image_sample_metadata(resolver, 'C:/data/cellZ.tif', tmp_path)   # not in the sheet
    doc = json.loads(out.read_text(encoding='utf-8'))
    assert doc['fields'] == {} and doc['source'] == 'none', (
        "an unmatched image must record 'matched nothing', not silently omit a file")


def test_no_resolver_writes_NOTHING_the_additivity_guarantee(tmp_path):
    from pycat.utils.sample_metadata import write_image_sample_metadata
    assert write_image_sample_metadata(None, 'C:/data/cellA.tif', tmp_path) is None
    assert list(tmp_path.iterdir()) == [], "a metadata-less batch must write no extra files"


def test_resolver_from_config_is_None_without_a_source_and_built_with_one(tmp_path):
    from pycat.utils.sample_metadata import resolver_from_config, SampleMetadataResolver
    assert resolver_from_config({'steps': []}) is None
    assert resolver_from_config({}) is None
    r = resolver_from_config({'sample_filename_pattern': '{genotype}_rep{replicate}'})
    assert isinstance(r, SampleMetadataResolver)


def test_the_batch_loop_calls_the_write_helper_when_a_source_is_configured():
    """AST: `batch_processor` (Qt-bound, not importable headless) must actually wire the resolver into
    its run loop — build it from the config and call the per-image writer. A helper that exists but is
    never called is not Part B."""
    src = (pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat'
           / 'batch_processor.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    called = {getattr(c.func, 'id', None) or getattr(c.func, 'attr', None)
              for c in ast.walk(tree) if isinstance(c, ast.Call)}
    assert 'resolver_from_config' in called, "the batch loop does not build a resolver from its config"
    assert 'write_image_sample_metadata' in called, (
        "the batch loop never calls write_image_sample_metadata — the resolver is unused")


# ── Part C: the in-app tag round-trips through Save & Load ──────────────────────────────────

class _ActiveDataClass:
    def __init__(self, repo=None):
        self.data_repository = dict(repo or {})


class _CentralManager:
    def __init__(self, repo=None):
        self.active_data_class = _ActiveDataClass(repo)


class _Viewer:
    def add_image(self, *a, **k):
        return None


def _save_session(tmp_path, repo):
    from pycat.file_io.writers import write_session_outputs
    sdir = tmp_path / 'session'
    sdir.mkdir()
    write_session_outputs(
        _CentralManager(repo), {}, selected_layers=[], selected_dataframes=[],
        dataframes={}, file_metadata=None, save_name=str(sdir / 'expt'),
        session_dir=sdir, source_path=None, stem='expt')
    return sdir


def test_in_app_tags_ROUND_TRIP_through_save_and_load(tmp_path):
    from pycat.file_io import session_loader as sl

    tags = {'cellA': {'genotype': 'WT', 'dose': '10'}, 'cellB': {'genotype': 'mut'}}
    sdir = _save_session(tmp_path, {'sample_metadata': tags})

    # The manifest carries the tags…
    from pycat.file_io.session_manifest import read_manifest
    assert read_manifest(sdir).get('sample_metadata') == tags

    # …and Load Session restores them into a FRESH repository.
    di = _ActiveDataClass()
    sl.load_session(sdir, _Viewer(), di)
    assert di.data_repository.get('sample_metadata') == tags, (
        "an in-app condition tag did not survive save → load")


def test_a_session_with_NO_tags_restores_no_tag_and_is_back_compat(tmp_path):
    from pycat.file_io import session_loader as sl

    sdir = _save_session(tmp_path, {})           # nothing tagged
    from pycat.file_io.session_manifest import read_manifest
    assert 'sample_metadata' not in read_manifest(sdir), (
        "an untagged session must not gain the field — the manifest stays byte-compatible")

    di = _ActiveDataClass()
    sl.load_session(sdir, _Viewer(), di)
    assert 'sample_metadata' not in di.data_repository
