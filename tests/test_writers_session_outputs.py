"""Pure test for the Save & Clear write half: ``writers.write_session_outputs``.

This is the Qt-free output-writing loop lifted out of ``save_and_clear_all``
(File-I/O decomposition #4). No Qt, no napari viewer, no dialogs — just fake
layer objects (exposing ``.name`` / ``.data`` / ``.metadata``), a couple of
small DataFrames, and a temp dir. We assert the expected files land (layer
files, ``_<df>.csv`` with correct row counts, ``_metadata.json``, and the
session manifest) and that the returned manifest entries match.
"""

import json
import os

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.core

from pycat.file_io import session_manifest as sm
from pycat.file_io.writers import write_session_outputs


# ── Fake layers: type(layer).__name__ IS the napari layer_type _save_layer
# branches on, so the class names must be exactly 'Image' / 'Labels'. ──
class Image:
    def __init__(self, name, data, metadata=None):
        self.name = name
        self.data = data
        self.metadata = metadata or {}


class Labels:
    def __init__(self, name, data, metadata=None):
        self.name = name
        self.data = data
        self.metadata = metadata or {}


class _ActiveDataClass:
    def __init__(self, repo):
        self.data_repository = repo


class _CentralManager:
    def __init__(self, repo=None):
        self.active_data_class = _ActiveDataClass(repo or {})


def test_write_session_outputs_lands_all_files(tmp_path):
    session_dir = tmp_path / "session_x"
    session_dir.mkdir()
    stem = "expt1"
    save_name = str(session_dir / stem)

    img = Image("My Image", np.arange(64, dtype=np.uint16).reshape(8, 8))
    mask = Labels("Cell Mask", (np.arange(64).reshape(8, 8) % 3).astype(np.uint8))
    layers_by_name = {img.name: img, mask.name: mask}

    df_a = pd.DataFrame({'area': [1.0, 2.0, 3.0, 4.0, 5.0]})   # 5 rows
    df_b = pd.DataFrame({'x': [10, 20, 30]})                   # 3 rows
    dataframes = {'cell_df': df_a, 'vpt_tracks': df_b}

    file_metadata = {'common': {'frame_interval_s': 0.5}, 'note': 'provenance'}

    cm = _CentralManager()
    src = tmp_path / "source.ome.tif"
    src.write_bytes(b"a real file referenced by the manifest")

    result = write_session_outputs(
        cm, layers_by_name,
        selected_layers=["My Image", "Cell Mask"],
        selected_dataframes=["cell_df", "vpt_tracks"],
        dataframes=dataframes,
        file_metadata=file_metadata,
        save_name=save_name,
        session_dir=session_dir,
        source_path=str(src),
        stem=stem)

    # ── Layer files landed with the expected right-sized formats ──
    assert (session_dir / "expt1_my_image.tiff").exists()   # 2D image → tiff
    assert (session_dir / "expt1_cell_mask.png").exists()   # 2D labels → png

    # ── Dataframe CSVs landed with correct row counts ──
    csv_a = session_dir / "expt1_cell_df.csv"
    csv_b = session_dir / "expt1_vpt_tracks.csv"
    assert csv_a.exists() and csv_b.exists()
    assert len(pd.read_csv(csv_a)) == 5
    assert len(pd.read_csv(csv_b)) == 3

    # ── Metadata JSON landed and round-trips ──
    meta_path = session_dir / "expt1_metadata.json"
    assert meta_path.exists()
    with open(meta_path, encoding='utf-8') as f:
        assert json.load(f)['note'] == 'provenance'

    # ── Manifest landed, references the source (not copies it), knows the layers/dfs ──
    m = sm.read_manifest(session_dir)
    assert m is not None
    assert m['source_image']['path'] == str(src)
    assert not (session_dir / "source.ome.tif").exists()   # referenced, never copied

    # ── Returned manifest entries match what was written ──
    assert result['manifest_layers'] == [
        {'name': "My Image", 'layer_type': 'Image', 'safe_name': 'my_image'},
        {'name': "Cell Mask", 'layer_type': 'Labels', 'safe_name': 'cell_mask'},
    ]
    assert result['manifest_dfs'] == [
        {'key': 'cell_df', 'file': 'expt1_cell_df.csv'},
        {'key': 'vpt_tracks', 'file': 'expt1_vpt_tracks.csv'},
    ]


def test_write_session_outputs_honours_selection(tmp_path):
    """Only SELECTED layers/dataframes are written; the rest are left alone."""
    session_dir = tmp_path / "session_y"
    session_dir.mkdir()
    save_name = str(session_dir / "s")

    keep = Image("Keep", np.zeros((4, 4), dtype=np.uint16))
    drop = Image("Drop", np.ones((4, 4), dtype=np.uint16))
    layers_by_name = {keep.name: keep, drop.name: drop}

    dataframes = {'wanted': pd.DataFrame({'a': [1, 2]}),
                  'unwanted': pd.DataFrame({'b': [9]})}

    result = write_session_outputs(
        _CentralManager(), layers_by_name,
        selected_layers=["Keep"],
        selected_dataframes=["wanted"],
        dataframes=dataframes,
        file_metadata=None,          # no metadata → no _metadata.json
        save_name=save_name,
        session_dir=session_dir,
        source_path=None,
        stem="s")

    assert (session_dir / "s_keep.tiff").exists()
    assert not (session_dir / "s_drop.tiff").exists()
    assert (session_dir / "s_wanted.csv").exists()
    assert not (session_dir / "s_unwanted.csv").exists()
    assert not (session_dir / "s_metadata.json").exists()

    assert [e['name'] for e in result['manifest_layers']] == ["Keep"]
    assert [e['key'] for e in result['manifest_dfs']] == ["wanted"]


def test_write_session_outputs_skips_manifest_without_session_dir(tmp_path):
    """No session dir → files still write at save_name, but no manifest step."""
    save_name = str(tmp_path / "flat")
    img = Image("A", np.zeros((4, 4), dtype=np.uint16))

    result = write_session_outputs(
        _CentralManager(), {img.name: img},
        selected_layers=["A"],
        selected_dataframes=[],
        dataframes={},
        file_metadata=None,
        save_name=save_name,
        session_dir=None,
        source_path=None,
        stem="flat")

    assert (tmp_path / "flat_a.tiff").exists()
    assert result['manifest_layers'] == [
        {'name': "A", 'layer_type': 'Image', 'safe_name': 'a'}]
    assert result['manifest_dfs'] == []
