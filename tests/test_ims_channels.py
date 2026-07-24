"""**The Imaris reader now emits the per-channel `channels` list in the same schema as OME (deep_metadata item 4).**

`extract_ims_metadata` flattened per-channel data into single top-level fields; every other consumer that reads
`raw['channels']` / `raw['instrument']` (e.g. metadata_contradictions) saw nothing for an .ims file. These pin
the reader parity: the Imaris `DataSetInfo/Channel N` groups become a `channels` list with the same keys the
OME reader produces (OME-only fields stay `None` — missing stays missing), an instrument block carries the NA
and magnification, and the top-level emission/excitation fill from channel 0 when the image header had none.
"""
import numpy as np
import pytest

# Guarded import (kept out of module top-level) so the headless collector does not skip this module on the
# `pycat.file_io` prefix — metadata_extract is pure/headless-safe. See test_metadata_merge.
try:
    from pycat.file_io.metadata_extract import extract_ims_metadata, _OME_CHANNEL_KEYS
except Exception:      # pragma: no cover - only when the io stack is truly unavailable
    pytest.skip("pycat.file_io.metadata_extract unavailable", allow_module_level=True)

h5py = pytest.importorskip("h5py", reason="Imaris metadata needs h5py")

pytestmark = pytest.mark.base


def _barr(s):
    """A byte-array attribute the way Imaris stores them (single-byte entries, with a ``.tobytes()``)."""
    return np.frombuffer(str(s).encode("ascii"), dtype="S1")


def _write_ims(path, channels, image_attrs=None):
    with h5py.File(str(path), "w") as f:
        info = f.create_group("DataSetInfo")
        img = info.create_group("Image")
        for k, v in (image_attrs or {}).items():
            img.attrs[k] = _barr(v)
        for i, ch in enumerate(channels):
            g = info.create_group(f"Channel {i}")
            for k, v in ch.items():
                g.attrs[k] = _barr(v)
    return str(path)


def test_ims_channels_list_mirrors_the_ome_schema(tmp_path):
    p = _write_ims(tmp_path / "a.ims", [
        {"Name": "GFP", "LSMEmissionWavelength": "525", "LSMExcitationWavelength": "488"},
        {"Name": "mCherry", "LSMEmissionWavelength": "610", "LSMExcitationWavelength": "587"},
    ])
    chans = extract_ims_metadata(p)["raw"]["channels"]
    assert len(chans) == 2
    assert set(chans[0]) == set(_OME_CHANNEL_KEYS)          # same shape as the OME channels list
    assert chans[0]["index"] == 0 and chans[0]["name"] == "GFP"
    assert chans[0]["emission_nm"] == 525 and chans[0]["excitation_nm"] == 488
    assert chans[1]["name"] == "mCherry" and chans[1]["emission_nm"] == 610
    assert chans[0]["fluor"] is None                        # an OME-only field stays missing, never defaulted


def test_top_level_emission_fills_from_channel_0_when_the_image_had_none(tmp_path):
    p = _write_ims(tmp_path / "b.ims", [{"Name": "DAPI", "EmissionWavelength": "461"}])
    common = extract_ims_metadata(p)["common"]
    assert common["emission_nm"] == 461                     # additive: filled from ch0, image had no top-level


def test_no_channels_group_is_safe_and_carries_no_channels_key(tmp_path):
    p = str(tmp_path / "c.ims")
    with h5py.File(p, "w") as f:
        f.create_group("DataSetInfo").create_group("Image")
    out = extract_ims_metadata(p)
    assert "channels" not in out["raw"]                     # nothing to add → key absent, not an empty list
    assert out["common"]["file_type"] == "ims"              # the rest of extraction is unaffected


def test_the_instrument_block_carries_na_and_magnification(tmp_path):
    p = _write_ims(tmp_path / "d.ims", [{"Name": "GFP"}],
                   image_attrs={"NumericalAperture": "1.4", "LensPower": "63"})
    inst = extract_ims_metadata(p)["raw"]["instrument"]
    assert inst["lens_na"] == 1.4 and inst["nominal_magnification"] == 63
    assert inst["immersion"] is None                        # an absent instrument field stays None
