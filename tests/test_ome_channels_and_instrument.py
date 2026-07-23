"""**OME metadata is per-channel and hierarchical — the flat schema cannot say what this parses.**

A real Zeiss LSM export carries three channels with genuinely different acquisition parameters: Ch1 = 405/447
DAPI on Detector:1 at gain 600; Ch3 = a transmitted PMT (ContrastMethod "Other") at a different gain. The old
flat schema flattened all of that to one excitation/emission pair. `parse_ome_channels_and_instrument` reads
each `<Channel>` from its own element and resolves its detector (DetectorSettings child, falling back to the
referenced `<Detector>`), plus the instrument/objective block — INCLUDING the oil-immersion-vs-air-medium
contradiction that a real ZEN export writes (recorded, never silently resolved). Missing stays missing (None).
"""
import pytest

from pycat.file_io.metadata_extract import (
    parse_ome_channels_and_instrument, _OME_CHANNEL_KEYS, _OME_INSTRUMENT_KEYS)

pytestmark = pytest.mark.core

# A three-channel Zeiss-style OME: two fluorescence channels + one transmitted PMT, distinct gains, an
# objective whose Immersion="Oil" contradicts ObjectiveSettings Medium="Air" (RI 1.518 = oil) — a real export.
_OME = '''<?xml version="1.0"?>
<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">
  <Instrument ID="Instrument:0">
    <Objective ID="Objective:0" LensNA="1.4" NominalMagnification="63" Immersion="Oil"/>
    <Detector ID="Detector:1" Type="PMT" Gain="600" Offset="0" AmplificationGain="1.0"/>
    <Detector ID="Detector:2" Type="PMT" Gain="550" Offset="0" AmplificationGain="1.0"/>
    <Detector ID="Detector:3" Type="PMT" Gain="336.8" Offset="0" AmplificationGain="1.2"/>
  </Instrument>
  <Image ID="Image:0">
    <ObjectiveSettings ID="Objective:0" Medium="Air" RefractiveIndex="1.518"/>
    <Pixels ID="Pixels:0" Type="uint16" DimensionOrder="XYZTC" SizeC="3">
      <Channel ID="Channel:0:0" Name="Ch1-T1" Fluor="DAPI" ExcitationWavelength="405"
               EmissionWavelength="447" ContrastMethod="Fluorescence"
               AcquisitionMode="LaserScanningConfocalMicroscopy" Color="65535">
        <DetectorSettings ID="Detector:1" Binning="1x1"/>
      </Channel>
      <Channel ID="Channel:0:1" Name="Ch2-T2" Fluor="EGFP" ExcitationWavelength="488"
               EmissionWavelength="516" ContrastMethod="Fluorescence"
               AcquisitionMode="LaserScanningConfocalMicroscopy" Color="16711935">
        <DetectorSettings ID="Detector:2" Binning="1x1"/>
      </Channel>
      <Channel ID="Channel:0:2" Name="Ch3-T3" ContrastMethod="Other"
               AcquisitionMode="LaserScanningConfocalMicroscopy">
        <DetectorSettings ID="Detector:3" Gain="400" Binning="1x1"/>
      </Channel>
    </Pixels>
  </Image>
</OME>'''


def test_each_channel_is_read_from_its_own_element_not_flattened():
    chans = parse_ome_channels_and_instrument(_OME)['channels']
    assert len(chans) == 3
    assert [c['index'] for c in chans] == [0, 1, 2]
    assert chans[0]['fluor'] == 'DAPI' and chans[0]['excitation_nm'] == 405 and chans[0]['emission_nm'] == 447
    assert chans[1]['fluor'] == 'EGFP' and chans[1]['excitation_nm'] == 488
    # excitation is per-channel — the two are genuinely different, not one value copied
    assert chans[0]['excitation_nm'] != chans[1]['excitation_nm']


def test_the_transmitted_PMT_channel_is_distinguishable_by_contrast_method():
    ch3 = parse_ome_channels_and_instrument(_OME)['channels'][2]
    assert ch3['contrast_method'] == 'Other'          # identifies ch3 as the transmitted PMT, not fluorescence
    assert ch3['fluor'] is None and ch3['excitation_nm'] is None   # no dye — missing stays missing


def test_detector_gain_resolves_from_settings_then_the_referenced_detector():
    chans = parse_ome_channels_and_instrument(_OME)['channels']
    # Ch1/Ch2 DetectorSettings omit Gain → filled from the <Detector> element (the calibration fingerprint)
    assert chans[0]['detector_id'] == 'Detector:1' and chans[0]['gain'] == 600
    assert chans[1]['gain'] == 550
    # Ch3 DetectorSettings SET Gain="400" → the per-channel setting WINS over the Detector's 336.8
    assert chans[2]['gain'] == 400
    # amplification_gain (float, not integral) comes from the Detector element and stays a float
    assert chans[2]['amplification_gain'] == 1.2
    assert chans[0]['binning'] == '1x1'


def test_numeric_coercion_is_int_when_integral_float_otherwise_none_when_absent():
    chans = parse_ome_channels_and_instrument(_OME)['channels']
    assert isinstance(chans[0]['gain'], int) and chans[0]['gain'] == 600
    assert isinstance(chans[2]['amplification_gain'], float)
    assert chans[0]['color'] == 65535 and chans[2]['color'] is None   # ch3 has no Color → None, not 0


def test_the_instrument_block_records_the_oil_vs_air_contradiction_without_resolving_it():
    inst = parse_ome_channels_and_instrument(_OME)['instrument']
    assert inst['lens_na'] == 1.4 and inst['nominal_magnification'] == 63
    # BOTH sides of the contradiction are recorded — never silently pick one
    assert inst['immersion'] == 'Oil' and inst['medium'] == 'Air' and inst['refractive_index'] == 1.518
    assert inst['dimension_order'] == 'XYZTC'


def test_every_channel_and_instrument_dict_carries_the_full_canonical_key_set():
    res = parse_ome_channels_and_instrument(_OME)
    for c in res['channels']:
        assert set(c.keys()) == set(_OME_CHANNEL_KEYS)
    assert set(res['instrument'].keys()) == set(_OME_INSTRUMENT_KEYS)


def test_malformed_or_non_ome_input_yields_empty_channels_and_all_none_instrument():
    for bad in ('<OME><Pixels Type="uint16"', 'not xml at all', '', None):
        res = parse_ome_channels_and_instrument(bad)
        assert res['channels'] == []
        assert all(v is None for v in res['instrument'].values())
