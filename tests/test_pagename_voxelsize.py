"""**A z-step hiding in a TIFF PageName is recovered, with a pixel-size cross-check** (sidecar_metadata 1b).

`PageName = "…, VoxelSize=0.0977x0.0977x19.0000"` carries the 19 µm z-step (and confirms the in-plane
size) that the structured pixel-size object never sees, so `z_step_um` came back `None`. Now it is parsed;
the in-plane size cross-checks XResolution, and a disagreement beyond tolerance is recorded as a conflict
rather than silently resolved.
"""
import numpy as np
import pytest

from pycat.file_io.metadata_extract import _parse_voxelsize, extract_tiff_metadata

pytestmark = pytest.mark.core


def test_parse_voxelsize_from_the_iss_pagename():
    pn = "Page 1, S=1, P=1, T=1, Z=1, VoxelSize=0.0977x0.0977x19.0000"
    assert _parse_voxelsize(pn) == (0.0977, 0.0977, 19.0)


def test_parse_voxelsize_is_none_when_absent():
    assert _parse_voxelsize("Page 1, S=1, Z=1") is None
    assert _parse_voxelsize(None) is None


def _write_tiff(path, page_name, resolution=None):
    import tifffile
    extratags = [(285, 's', 0, page_name, True)]          # 285 = PageName
    kw = {'extratags': extratags}
    if resolution is not None:
        kw['resolution'] = resolution
        kw['resolutionunit'] = 3                            # centimetre
    tifffile.imwrite(str(path), np.zeros((16, 16), np.uint16), **kw)


def test_the_z_step_and_pixel_size_are_recovered_from_pagename(tmp_path):
    p = tmp_path / 'vox.tif'
    _write_tiff(p, "Page 1, VoxelSize=0.0977x0.0977x19.0000")     # no XResolution written
    common = extract_tiff_metadata(str(p))['common']
    assert common['z_step_um'] == pytest.approx(19.0)
    assert common['pixel_size_um'] == pytest.approx(0.0977, rel=1e-3)
    assert common['pixel_size_source'] == 'page_name_voxelsize'


def test_a_disagreeing_xresolution_is_flagged_not_silently_resolved(tmp_path):
    p = tmp_path / 'vox_conflict.tif'
    res_per_cm = 10000.0 / 0.0977                            # XResolution for 0.0977 µm/px
    _write_tiff(p, "Page 1, VoxelSize=0.2000x0.2000x19.0000", resolution=(res_per_cm, res_per_cm))
    common = extract_tiff_metadata(str(p))['common']
    assert common['pixel_size_source'] == 'tiff_tags'        # XResolution wins the value
    assert common['z_step_um'] == pytest.approx(19.0)        # ...but the z-step still comes from PageName
    assert any('disagree' in c for c in common.get('conflicts', [])), common.get('conflicts')
