"""**OME-XML metadata is read from the RIGHT element — `Pixels/@Type`, not the first `Type=` in the file.**

The old parse regexed the whole document for 10 fixed names and took the first match, so on a real Zeiss LSM
export `Type` resolved to `PMT` (from `<Detector Type="PMT">`, which precedes `<Pixels Type="uint16">`) —
the pixel dtype silently recorded as a detector category. These pin the element-scoped fix: the pixel dtype
is `uint16`, the pixel-size/geometry attributes are unchanged (nothing already-correct regresses), the
`<AcquisitionDate>` child element is now read, and malformed XML falls back to the forgiving regex.
"""
import pytest

# Guarded so the import is NOT at module top-level: the headless core collector
# (conftest.pytest_ignore_collect) skips any module that imports `pycat.file_io` at module scope when the
# optional stack is thinned, and metadata_extract is pure/headless-safe — so this keeps the tests collectable
# (and cleanly skips only where the io stack genuinely can't import).
try:
    from pycat.file_io.metadata_extract import parse_description_blob, _parse_ome_xml_scoped
except Exception:      # pragma: no cover - only when the io stack is truly unavailable
    pytest.skip("pycat.file_io.metadata_extract unavailable", allow_module_level=True)

pytestmark = pytest.mark.core

_OME = '''<?xml version="1.0"?>
<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">
  <Instrument><Detector ID="Detector:1" Type="PMT" Gain="600"/></Instrument>
  <Image ID="Image:0">
    <AcquisitionDate>2020-05-01T12:00:00</AcquisitionDate>
    <Pixels ID="Pixels:0" Type="uint16" DimensionOrder="XYZTC"
            PhysicalSizeX="0.1035" PhysicalSizeY="0.1035" PhysicalSizeZ="0.5"
            SizeT="1" SizeC="3" SizeZ="20" TimeIncrement="0.0">
      <Plane TheC="0" ExposureTime="0.05"/>
    </Pixels>
  </Image>
</OME>'''


def test_Type_is_the_PIXEL_dtype_not_the_first_Type_in_the_document():
    out = parse_description_blob(_OME)
    assert out['Type'] == 'uint16', "Type must come from <Pixels>, not <Detector Type='PMT'>"


def test_pixel_size_and_geometry_are_read_correctly_and_unchanged():
    out = parse_description_blob(_OME)
    assert out['PhysicalSizeX'] == '0.1035' and out['PhysicalSizeZ'] == '0.5'
    assert out['SizeC'] == '3' and out['SizeZ'] == '20' and out['DimensionOrder'] == 'XYZTC'
    assert out['ExposureTime'] == '0.05'


def test_the_acquisition_date_child_element_is_now_read():
    # AcquisitionDate is an ELEMENT, not an attribute — the old regex (AcquisitionDate=") never matched it
    assert parse_description_blob(_OME)['AcquisitionDate'] == '2020-05-01T12:00:00'


def test_the_scoped_parser_reads_the_first_Pixels_element():
    scoped = _parse_ome_xml_scoped(_OME)
    assert scoped['Type'] == 'uint16' and scoped['PhysicalSizeX'] == '0.1035'


def test_malformed_xml_returns_empty_from_the_scoped_parser_and_falls_back():
    assert _parse_ome_xml_scoped('<OME><Pixels Type="uint16"') == {}   # truncated → unparseable
    # the whole blob path still recovers the value via the regex fallback (no crash, no loss)
    out = parse_description_blob('<?xml ?><OME garbage PhysicalSizeX="0.2" Type="uint16"></broken')
    assert out.get('PhysicalSizeX') == '0.2'


def test_a_non_ome_blob_is_untouched():
    assert parse_description_blob('just some text, not xml') == {}
