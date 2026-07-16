"""Session save/load as one unit: the manifest references the source image (never copies it),
consolidates artifacts into one session folder, restores analysis dataframes (incl. vpt_tracks), and
the smart default selection excludes the source image and pure-interpolation upscales.
"""

import os

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.core

from pycat.file_io import session_manifest as sm


def test_default_session_dir_is_consolidated(tmp_path):
    d = sm.default_session_dir(tmp_path, "3.30 hr_1_MMStack_Pos0.ome")
    assert d.name.startswith("session_")
    assert str(tmp_path) in str(d)


def test_manifest_references_source_not_copies(tmp_path):
    src = tmp_path / "img.ome.tif"
    src.write_bytes(b"not a real tiff but a real file")
    sdir = tmp_path / "session_x"
    dr = {'microns_per_pixel_sq': 0.067 ** 2,
          'file_metadata': {'common': {'frame_interval_s': 0.5}}}
    sm.write_manifest(sdir, str(src), dr, layer_entries=[], dataframe_entries=[])
    m = sm.read_manifest(sdir)
    assert m['source_image']['path'] == str(src)
    assert m['source_image']['exists'] is True
    assert abs(m['acquisition']['microns_per_pixel_sq'] - 0.067 ** 2) < 1e-12
    assert abs(m['acquisition']['frame_interval_s'] - 0.5) < 1e-12
    # crucially, the source file itself is NOT inside the session folder
    assert not (sdir / "img.ome.tif").exists()


def test_restore_dataframes(tmp_path):
    sdir = tmp_path / "session_y"
    sdir.mkdir()
    tracks = pd.DataFrame({'track_id': [0, 0, 1], 'frame': [0, 1, 0],
                           'y_um': [1.0, 1.1, 5.0], 'x_um': [2.0, 2.1, 6.0]})
    tracks.to_csv(sdir / "stem_vpt_tracks.csv", index=True)
    sm.write_manifest(sdir, None, {}, layer_entries=[],
                      dataframe_entries=[{'key': 'vpt_tracks',
                                          'file': 'stem_vpt_tracks.csv'}])
    m = sm.read_manifest(sdir)
    repo = {}
    restored = sm.restore_dataframes_from_manifest(m, sdir, repo)
    assert 'vpt_tracks' in restored and 'vpt_tracks' in repo
    assert len(repo['vpt_tracks']) == 3


# The class NAME matters: default_session_selection / _is_source_image_layer /
# _is_reconstructable branch on `type(layer).__name__` ('Image' vs 'Labels' etc.), which is how
# they tell a source Image from a derived mask. napari's real layers are classes literally named
# `Image` / `Labels` / `Tracks`, so the fakes must be too — a fake named `_Img` reads as
# `__name__ == '_Img'`, never matches 'Image', and the source layer is silently never excluded.
class Image:
    def __init__(self, n): self.name = n
class Labels:
    def __init__(self, n): self.name = n
class Tracks:
    def __init__(self, n): self.name = n


def test_default_selection_excludes_source_and_upscale():
    layers = [Image("3.30 hr_1_MMStack_Pos0.ome C0-blue Stack"),
              Labels("Cell Mask"),
              Tracks("Bead Trajectories"),
              Image("Pre-Processed Fluorescence Image"),
              Image("Upscaled Image")]
    keepL, keepD = sm.default_session_selection(
        layers, ['vpt_tracks', 'cell_df'], "3.30 hr_1_MMStack_Pos0.ome")
    assert "3.30 hr_1_MMStack_Pos0.ome C0-blue Stack" not in keepL   # source
    assert "Upscaled Image" not in keepL                            # upscale
    assert "Cell Mask" in keepL and "Bead Trajectories" in keepL
    assert set(keepD) == {'vpt_tracks', 'cell_df'}                  # all dfs
