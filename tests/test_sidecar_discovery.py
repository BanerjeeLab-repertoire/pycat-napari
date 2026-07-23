"""**A sidecar file next to an image is discovered (bounded) and parsed — the ISS Vista case proves it.**

sidecar_metadata spec Parts 2–3. A plain 2-D TIFF from ISS Vista carries no channel identity, but a
companion `_fbs.xml` beside it does. Discovery finds it in the image's own directory (no recursion, capped),
and the ISS parser recovers per-channel emission bands + detectors + the objective — enough to name the
channels from their spectra and NEVER label them brightfield. Non-gating throughout.
"""
import pathlib

import numpy as np
import pytest

from pycat.file_io.sidecar_discovery import (discover_sidecar, sidecar_metadata_for,
                                             _iss_parse, _parse_sections)

pytestmark = pytest.mark.core

_FBS = """<Document>
<fromComments>
[Ch1]
Emission Filter  -   : #3 - 647/57 nm
Pinhole  -   : 100 um
Detector  -   : APD
Detector Module  -   : PerkinElmer SPCM-AQR-15
[Ch2]
Emission Filter  -   : #2 - 525/50 nm
Pinhole  -   : 0 um
Detector  -   : APD
[Excitation Laser]
Laser 1  -   : 488 nm @30%
Laser 2  -   : 635 nm @15%
[Microscope]
Microscope Objective Magnification  -   : 60
PixelDwellTime  -   : 0.1 ms
</fromComments>
</Document>
"""


def _make_case(tmp_path, subdir_sidecar=False):
    img = tmp_path / 'im-1-FUS-PLD-1_Ch1.tif'
    import tifffile
    tifffile.imwrite(str(img), np.zeros((8, 8), np.uint16))
    where = (tmp_path / 'sub') if subdir_sidecar else tmp_path
    where.mkdir(exist_ok=True)
    (where / 'im-1-FUS-PLD-1_fbs.xml').write_text(_FBS, encoding='utf-8')
    return img


# ── the ISS parser ──────────────────────────────────────────────────────────────────────────────
def test_the_iss_parser_recovers_per_channel_emission_detector_and_objective(tmp_path):
    fbs = tmp_path / 'x_fbs.xml'
    fbs.write_text(_FBS, encoding='utf-8')
    md = _iss_parse(fbs)

    chans = {c['name']: c for c in md['channels']}
    assert chans['Ch1']['emission_nm'] == 647 and chans['Ch1']['emission_bandwidth_nm'] == 57
    assert chans['Ch1']['pinhole_um'] == 100.0 and 'APD' in chans['Ch1']['detector']
    assert chans['Ch2']['emission_nm'] == 525 and chans['Ch2']['emission_bandwidth_nm'] == 50
    assert chans['Ch2']['pinhole_um'] == 0.0
    assert md['nominal_magnification'] == 60.0
    assert md['pixel_dwell_time_ms'] == 0.1
    assert set(md['excitation_lines_nm']) == {488, 635}


def test_the_iss_parser_calls_it_fluorescence_with_a_reason_not_brightfield(tmp_path):
    fbs = tmp_path / 'y_fbs.xml'
    fbs.write_text(_FBS, encoding='utf-8')
    md = _iss_parse(fbs)
    assert md['modality'] == 'fluorescence'
    assert 'emission filter' in md['modality_reason'].lower() and 'apd' in md['modality_reason'].lower()


def test_parse_sections_strips_the_key_noise():
    sections = _parse_sections("[Ch1]\nEmission Filter  -   : 647/57 nm\n[Sys]\nGain  : 600")
    assert sections['ch1']['emission filter'] == '647/57 nm'
    assert sections['sys']['gain'] == '600'


# ── discovery: bounded, non-gating ────────────────────────────────────────────────────────────────
def test_discovery_finds_the_sidecar_via_a_stripped_channel_suffix(tmp_path):
    img = _make_case(tmp_path)
    path, parser = discover_sidecar(img)               # im-1-FUS-PLD-1_Ch1 -> im-1-FUS-PLD-1_fbs
    assert path is not None and path.name == 'im-1-FUS-PLD-1_fbs.xml'
    assert parser.name == 'iss_vista_fbs'

    md = sidecar_metadata_for(img)
    assert md is not None and md['modality'] == 'fluorescence'


def test_discovery_does_NOT_recurse_into_subdirectories(tmp_path):
    img = _make_case(tmp_path, subdir_sidecar=True)    # the fbs is in tmp_path/sub, not beside the image
    path, _parser = discover_sidecar(img)
    assert path is None                                 # bounded to the image's own directory


def test_discovery_returns_nothing_and_never_raises_when_absent(tmp_path):
    import tifffile
    img = tmp_path / 'lonely.tif'
    tifffile.imwrite(str(img), np.zeros((8, 8), np.uint16))
    assert discover_sidecar(img) == (None, None)
    assert sidecar_metadata_for(img) is None
    assert discover_sidecar(pathlib.Path(tmp_path / 'does_not_exist.tif')) == (None, None)
