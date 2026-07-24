# -*- coding: utf-8 -*-
"""
Unified metadata extraction across microscopy file formats.

PyCAT reads several formats (TIFF/OME-TIFF, CZI, IMS, generic HDF5), each of
which stores acquisition metadata in a completely different structure:

    - TIFF: baseline TIFF tags (XResolution/ResolutionUnit/DateTime/Software/
      ImageDescription) plus optional ImageJ / OME-XML blocks.
    - CZI / OME-TIFF: OME-XML via AICSImage.
    - IMS (Imaris HDF5): DataSetInfo/Image HDF5 attributes (ExtMax/ExtMin
      extents, LensPower, NumericalAperture, MicroscopeMode, Excitation/
      EmissionWavelength, RecordingDate, Channels, ...).

This module normalises all of these into a single dict with two parts:

    {
      'common': {  # curated, comparable-across-formats fields
          'file_name', 'file_type', 'dimensions' {t,c,z,y,x},
          'pixel_size_um', 'pixel_size_source', 'bit_depth',
          'n_channels', 'n_timepoints', 'n_z',
          'objective', 'numerical_aperture', 'modality',
          'excitation_nm', 'emission_nm', 'acquisition_date', 'software',
      },
      'raw': { ... every key/value the file exposes, as strings ... },
    }

The 'common' block drives the default metadata display and the results-export
JSON; the 'raw' block backs the "show everything" toggle. Every extractor is
defensive: a missing or malformed field yields None rather than raising, so a
partial file still produces a usable record.
"""

import os
from pycat.file_io.image_reader import open_image

try:
    from pycat.utils.general_utils import debug_log
except Exception:  # pragma: no cover - fallback if utils unavailable
    def debug_log(context, exc=None):
        return


def _warn_frame_interval(message):
    """Surface a frame-interval inconsistency to the user, once. Reuses the
    frame_interval module's de-duped warning channel when available so a
    conflicting time axis is as loud as a missing one."""
    if not message:
        return
    try:
        from pycat.utils.frame_interval import _warn_once
        _warn_once(f"inconsistent:{message[:40]}", message)
        return
    except Exception:
        pass
    try:
        from napari.utils.notifications import show_warning
        show_warning(message)
    except Exception:
        print(f"[PyCAT] {message}")


def _safe_float(x):
    try:
        if x is None:
            return None
        if hasattr(x, 'tobytes'):
            x = x.tobytes()
        if isinstance(x, (bytes, bytearray)):
            x = x.decode('ascii', errors='ignore')
        s = str(x).strip().strip('\x00').strip()
        # Strip a trailing unit like "521 nm" -> "521"
        s = s.split()[0] if s and s.split() else s
        return float(s) if s else None
    except Exception:
        return None


def _safe_str(x):
    try:
        if x is None:
            return None
        if hasattr(x, 'tobytes'):
            x = x.tobytes()
        if isinstance(x, (bytes, bytearray)):
            x = x.decode('ascii', errors='ignore')
        s = str(x).strip().strip('\x00').strip()
        return s or None
    except Exception:
        return None


def _parse_ome_xml_scoped(xml_string):
    """Read OME attributes from the ELEMENT they belong to — ``Pixels/@Type`` (the pixel dtype), NOT the
    first ``Type=`` in the document, which on a real Zeiss LSM export is ``<Detector Type="PMT">`` and was
    silently recording a detector category as the pixel data type.

    Reads the first ``Pixels`` element's geometry/dtype/calibration attributes, the ``<AcquisitionDate>``
    child text (an element, not an attribute — the old regex never matched it), and the first ``<Plane>``'s
    exposure. Returns ``{attr: value}`` for what it finds, or ``{}`` if the XML cannot be parsed — the caller
    then falls back to the forgiving whole-string regex, so a malformed file never loses what the old parse
    got. Namespace-agnostic (OME tags carry a schema namespace)."""
    import xml.etree.ElementTree as _ET
    try:
        root = _ET.fromstring(xml_string)
    except Exception:      # broad-ok: optional_probe — unparseable OME → fall back to the regex, never crash
        return {}

    def _ln(tag):
        return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else tag

    out = {}
    pixels = next((e for e in root.iter() if _ln(e.tag) == 'Pixels'), None)
    if pixels is not None:
        for k in ('PhysicalSizeX', 'PhysicalSizeY', 'PhysicalSizeZ', 'SizeT', 'SizeC', 'SizeZ',
                  'Type', 'TimeIncrement', 'DimensionOrder'):
            if k in pixels.attrib:
                out[k] = pixels.attrib[k]
    acq = next((e for e in root.iter() if _ln(e.tag) == 'AcquisitionDate'), None)
    if acq is not None and (acq.text or '').strip():
        out['AcquisitionDate'] = acq.text.strip()
    plane = next((e for e in root.iter() if _ln(e.tag) == 'Plane'), None)
    if plane is not None and 'ExposureTime' in plane.attrib:
        out['ExposureTime'] = plane.attrib['ExposureTime']
    return out


#: Canonical per-channel keys, always present (None when the file is silent — "missing stays missing").
_OME_CHANNEL_KEYS = ('index', 'name', 'fluor', 'excitation_nm', 'emission_nm', 'contrast_method',
                     'acquisition_mode', 'detector_id', 'gain', 'offset', 'binning',
                     'amplification_gain', 'color')
#: Canonical instrument/objective keys, always present (None when absent).
_OME_INSTRUMENT_KEYS = ('lens_na', 'nominal_magnification', 'immersion', 'medium',
                        'refractive_index', 'dimension_order')


def _ome_num(v):
    """OME numeric attribute → int if integral, float if not, None if absent/unparseable. Never a default —
    an absent value stays None so it cannot masquerade as a real reading (the pixel-size 'unknown is NaN not
    one' contract, generalised)."""
    if v in (None, ''):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def parse_ome_channels_and_instrument(xml_string):
    """Parse the OME hierarchy that the flat schema cannot express: the **per-channel** acquisition parameters
    (Ch1 = 405/447 DAPI on Detector:1 at gain 600; Ch3 = a transmitted PMT at a different gain) and the
    **instrument/objective** block. Purely ADDITIVE — the flat `excitation_nm`/`emission_nm`/etc. stay as they
    are; this exposes the structure alongside them.

    Reads each ``<Channel>`` from the element it belongs to and resolves its detector: a ``<DetectorSettings>``
    child carries the per-channel gain/offset/binning, falling back to the referenced ``<Detector>`` element
    for anything the settings omit (gain lives on one or the other depending on the vendor export). The
    instrument block reads ``<Objective>`` (LensNA/NominalMagnification/Immersion) and ``<ObjectiveSettings>``
    (Medium/RefractiveIndex — recorded even when it CONTRADICTS the objective's immersion; contradictions are
    surfaced, never silently resolved) and the ``<Pixels>`` DimensionOrder.

    Returns ``{'channels': [...], 'instrument': {...}}`` — each channel dict and the instrument dict carry the
    full canonical key set with ``None`` for anything absent (missing stays missing). Returns empty channels /
    all-None instrument if the XML cannot be parsed; never raises. Namespace-agnostic. Qt-free, core-tested."""
    import xml.etree.ElementTree as _ET
    empty = {'channels': [], 'instrument': {k: None for k in _OME_INSTRUMENT_KEYS}}
    if not xml_string or not isinstance(xml_string, str):
        return empty
    try:
        root = _ET.fromstring(xml_string)
    except Exception:      # broad-ok: optional_probe — unparseable OME → empty structure, never crash
        return empty

    def _ln(tag):
        return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else tag

    def _find_all(name):
        return [e for e in root.iter() if _ln(e.tag) == name]

    def _find_first(name):
        return next((e for e in root.iter() if _ln(e.tag) == name), None)

    # Detector elements by ID — the fallback source for per-channel gain/offset/binning the settings omit.
    detectors = {}
    for det in _find_all('Detector'):
        did = det.attrib.get('ID')
        if did:
            detectors[did] = det.attrib

    channels = []
    for i, ch in enumerate(_find_all('Channel')):
        a = ch.attrib
        ds = next((c for c in ch if _ln(c.tag) == 'DetectorSettings'), None)
        ds_attr = ds.attrib if ds is not None else {}
        det_id = ds_attr.get('ID')
        det_attr = detectors.get(det_id, {})

        # Bind this channel's attrs as defaults so the closure captures THIS iteration's values, not the
        # loop variables (it is only called within the iteration, but binding makes that explicit — B023).
        def _pick(key, ds_attr=ds_attr, det_attr=det_attr):   # DetectorSettings wins; Detector fills the gap.
            return ds_attr.get(key) if key in ds_attr else det_attr.get(key)

        channels.append({
            'index': i,
            'name': a.get('Name'),
            'fluor': a.get('Fluor'),
            'excitation_nm': _ome_num(a.get('ExcitationWavelength')),
            'emission_nm': _ome_num(a.get('EmissionWavelength')),
            'contrast_method': a.get('ContrastMethod'),
            'acquisition_mode': a.get('AcquisitionMode'),
            'detector_id': det_id,
            'gain': _ome_num(_pick('Gain')),
            'offset': _ome_num(_pick('Offset')),
            'binning': _pick('Binning'),
            'amplification_gain': _ome_num(_pick('AmplificationGain')),
            'color': _ome_num(a.get('Color')),
        })

    obj = _find_first('Objective')
    obj_a = obj.attrib if obj is not None else {}
    settings = _find_first('ObjectiveSettings')
    set_a = settings.attrib if settings is not None else {}
    pixels = _find_first('Pixels')

    instrument = {
        'lens_na': _ome_num(obj_a.get('LensNA')),
        'nominal_magnification': _ome_num(obj_a.get('NominalMagnification')),
        'immersion': obj_a.get('Immersion'),
        'medium': set_a.get('Medium'),
        'refractive_index': _ome_num(set_a.get('RefractiveIndex')),
        'dimension_order': pixels.attrib.get('DimensionOrder') if pixels is not None else None,
    }
    return {'channels': channels, 'instrument': instrument}


def parse_description_blob(text):
    """Turn a metadata 'description' blob into a flat dict of fields, so a wall
    of unparsed text becomes queryable structured metadata.

    Handles the three dialects PyCAT actually meets, which otherwise sit in a
    single opaque `raw` entry: MicroManager summary JSON, ImageJ `key=value`
    ImageDescription, and (shallowly) OME-XML. Returns {} when nothing parses.
    Pure function — unit tested in the navigator package.
    """
    if not text or not isinstance(text, str):
        return {}
    s = text.strip()
    out = {}

    # MicroManager / JSON summary — flatten scalar top-level entries.
    if s[:1] in '{[':
        import json as _json
        try:
            data = _json.loads(s)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, (dict, list)):
                        continue   # skip nested containers in the flat view
                    # A present-but-meaningless value (empty, a placeholder like 'N/A', NaN) is worse than
                    # an absent one — it looks authoritative and suppresses the prompt. Keep only meaningful.
                    from pycat.utils.metadata_validity import is_meaningful
                    if is_meaningful(str(k), v):
                        out[str(k)] = v
                if out:
                    return out
        except Exception:
            pass

    # OME-XML — detected BEFORE the ImageJ key=value block, because XML whose attributes wrap across lines
    # would otherwise be mis-parsed as `key=value` (a continuation line like `PhysicalSizeX="0.1" SizeC="3"`
    # does not start with `<`). Read each attribute from the element it belongs to (fixes Type="PMT" from
    # <Detector> masquerading as the pixel dtype, and multi-image first-match cross-contamination). The
    # scoped value WINS; the old whole-string regex remains only as a gap-filler for anything the parse could
    # not reach, so no value that was already correct regresses.
    if s[:5].lower().startswith('<?xml') or '<ome' in s.lower() or '<image' in s.lower():
        import re as _re
        scoped = _parse_ome_xml_scoped(s)
        for attr in ('PhysicalSizeX', 'PhysicalSizeY', 'PhysicalSizeZ',
                     'TimeIncrement', 'SizeT', 'SizeC', 'SizeZ', 'Type',
                     'DimensionOrder', 'ExposureTime', 'AcquisitionDate'):
            if attr in scoped:
                out[attr] = scoped[attr]
            else:
                m = _re.search(rf'{attr}="([^"]+)"', s)
                if m:
                    out[attr] = m.group(1)
        return out

    # ImageJ key=value block (one per line).
    if '=' in s and ('ImageJ' in s or '\n' in s):
        for line in s.splitlines():
            line = line.strip()
            if line.startswith('<') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            k, v = k.strip(), v.strip()
            if k and v and len(k) < 64:
                out[k] = v
        if out:
            return out

    return out


def _empty_common(file_path):
    return {
        'file_name': os.path.basename(file_path) if file_path else None,
        'file_type': (os.path.splitext(file_path)[1].lower().lstrip('.')
                      if file_path else None),
        'dimensions': None,
        'pixel_size_um': None,
        'pixel_size_source': None,
        'bit_depth': None,
        'n_channels': None,
        'n_timepoints': None,
        'n_z': None,
        'objective': None,
        'numerical_aperture': None,
        'modality': None,
        'excitation_nm': None,
        'emission_nm': None,
        'acquisition_date': None,
        'software': None,
        # Temporal / acquisition timing — captured at load so any consumer
        # (VPT microrheology, kymographs, FRAP, time-series analyses) reads the
        # frame interval from one place instead of asking the user to re-enter
        # it. frame_interval_s is the primary value; exposure_s and the raw
        # per-plane times are kept for provenance and for cases where the
        # nominal interval and the actual elapsed times differ.
        'frame_interval_s': None,
        'frame_interval_source': None,
        'frame_interval_iqr_s': None,
        # The NOMINAL declared interval (OME TimeIncrement / MicroManager
        # Interval_ms), kept alongside the measured cadence so the two can be
        # compared. When per-frame timestamps exist, THEY win — a declared value
        # is a claim, not a measurement (see reconcile_frame_interval).
        'frame_interval_nominal_s': None,
        # True when a nominal interval and the timestamp-derived cadence disagree
        # beyond tolerance. Surfaced to the user because a wrong time axis scales
        # every dynamics result (a 0.5 s claim over a 0.1 s real cadence is a 5x
        # error in every diffusion coefficient) and nothing else looks wrong.
        'frame_interval_inconsistent': False,
        # Full per-frame inter-frame deltas (seconds), when the file records
        # per-frame acquisition times (e.g. MicroManager ElapsedTime-ms). Kept
        # so a consumer (VPT MSD fitting) can use the true, possibly non-uniform
        # cadence instead of a single median, and so the metadata viewer/export
        # can show the actual timing rather than a nominal declared value.
        'frame_deltas_s': None,
        'exposure_s': None,
        'z_step_um': None,
        'camera_name': None,
        'acquisition_start_time': None,
        'n_frames': None,
        # ── Scan-acquisition geometry (for the scan-aberration QC checks) ────────────────────────
        # None means "not recorded" — never guessed. A laser-scanning confocal builds a frame one line
        # at a time (line_time/dwell set the per-row timing that motion-shears a mobile object); a
        # spinning disk exposes the whole field through a rotating pinhole array (pinhole size + disk
        # period). These gate which scan-artifact checks even apply. Populated only where a format
        # exposes them (see `_fill_scan_acquisition_fields`), which most currently do not.
        'acquisition_mode': None,   # 'point-scanning' | 'spinning-disk' | 'widefield' | None
        'line_time_s': None,        # seconds per scanned line (point-scanning)
        'dwell_time_s': None,       # per-pixel dwell time (point-scanning)
        'pinhole_um': None,         # pinhole diameter (confocal / spinning disk)
    }


#: Raw-metadata key fragments (case-insensitive substring) → the curated scan field they fill. Formats
#: dump wildly different key names into the raw OME/CZI/IMS block; this reads the common ones without a
#: per-format parser. A value is taken only if the curated field is still None (never overwrites a
#: format-specific extractor that already set it) and parses as the expected type.
_SCAN_RAW_HINTS = {
    'line_time_s': ('linetime', 'line_time', 'lineduration', 'timeperline'),
    'dwell_time_s': ('dwelltime', 'dwell_time', 'pixeldwell', 'pixeltime'),
    'pinhole_um': ('pinholesize', 'pinhole_um', 'pinholediameter', 'pinhole'),
}
#: Substrings that identify the acquisition mode in a raw value or key.
_MODE_HINTS = (
    ('spinning', 'spinning-disk'), ('spinningdisk', 'spinning-disk'), ('csu', 'spinning-disk'),
    ('point', 'point-scanning'), ('laser scan', 'point-scanning'), ('lsm', 'point-scanning'),
    ('confocal', 'point-scanning'), ('widefield', 'widefield'), ('wide-field', 'widefield'),
)


def _fill_scan_acquisition_fields(result):
    """Opportunistically fill the scan-geometry fields from the raw block, format-agnostically. Honest by
    construction: a field stays None unless a raw key plausibly names it AND the value parses — a guessed
    scan mode is exactly what the QC gating refuses (a wrong-modality check gives a confident wrong answer)."""
    try:
        common = result.get('common', {})
        raw = result.get('raw', {}) if isinstance(result.get('raw'), dict) else {}

        def _as_float_um_or_s(v):
            try:
                return float(str(v).split()[0])
            except (TypeError, ValueError, IndexError):
                return None

        for field, fragments in _SCAN_RAW_HINTS.items():
            if common.get(field) is not None:
                continue
            for k, v in raw.items():
                kl = str(k).lower()
                if any(f in kl for f in fragments):
                    fv = _as_float_um_or_s(v)
                    if fv is not None and fv > 0:
                        common[field] = fv
                        break

        if common.get('acquisition_mode') is None:
            blob = ' '.join(f"{k}={v}" for k, v in raw.items()).lower()
            blob += ' ' + str(common.get('modality') or '').lower()
            for frag, mode in _MODE_HINTS:
                if frag in blob:
                    common['acquisition_mode'] = mode
                    break
    except Exception as _exc:  # broad-ok: opportunistic metadata probe over arbitrary raw keys; a parse failure must never break metadata extraction
        debug_log('metadata_extract: could not fill scan-acquisition fields', _exc)
    return result


# ---------------------------------------------------------------------------
# IMS (Imaris HDF5)
# ---------------------------------------------------------------------------

# DataSetInfo/Image attributes we surface into the curated 'common' block.
_IMS_IMAGE_ATTRS = (
    'RecordingDate', 'LensPower', 'NumericalAperture', 'MicroscopeMode',
    'ExcitationWavelength', 'EmissionWavelength', 'Name', 'Description',
    'Unit', 'ManufactorString', 'ManufactorType', 'Channels',
    'NumberOfTimePoints',
)


def extract_ims_metadata(file_path, reader=None, width_px=None):
    """Extract normalised metadata from an Imaris .ims file.

    If a live imaris_ims_file_reader ``reader`` is supplied its h5py handle is
    reused; otherwise the file is opened read-only via h5py.
    """
    common = _empty_common(file_path)
    common['file_type'] = 'ims'
    raw = {}

    hf = None
    opened_here = False
    try:
        hf = getattr(reader, 'hf', None) if reader is not None else None
        if hf is None:
            import h5py
            hf = h5py.File(file_path, 'r')
            opened_here = True

        # DataSetInfo/Image attributes.
        try:
            img = hf['DataSetInfo']['Image']
            attrs = img.attrs
            for k in attrs:
                raw[f'Image/{k}'] = _safe_str(attrs.get(k))
            # Curated fields.
            common['acquisition_date'] = _safe_str(attrs.get('RecordingDate'))
            lp = _safe_float(attrs.get('LensPower'))
            common['objective'] = (f"{lp:g}x" if lp else _safe_str(attrs.get('LensPower')))
            common['numerical_aperture'] = _safe_float(attrs.get('NumericalAperture'))
            common['modality'] = _safe_str(attrs.get('MicroscopeMode'))
            common['excitation_nm'] = _safe_float(attrs.get('ExcitationWavelength'))
            common['emission_nm'] = _safe_float(attrs.get('EmissionWavelength'))
            common['software'] = (_safe_str(attrs.get('ManufactorString'))
                                  or _safe_str(attrs.get('ManufactorType')))

            # Pixel size from extents: (ExtMax0 - ExtMin0) / width.
            ext_min = _safe_float(attrs.get('ExtMin0'))
            ext_max = _safe_float(attrs.get('ExtMax0'))
            # Prefer the passed width; else read ImageSizeX.
            w = width_px
            if w is None:
                w = _safe_float(attrs.get('X')) or _safe_float(attrs.get('ImageSizeX'))
            if (ext_min is not None and ext_max is not None
                    and w and w > 0):
                px = abs(ext_max - ext_min) / float(w)
                if 1e-4 < px < 1e4:
                    common['pixel_size_um'] = px
                    common['pixel_size_source'] = 'ims_extents'
        except Exception:
            pass

        # Channel / timepoint counts and dimensions from the reader if present.
        if reader is not None:
            try:
                common['n_channels'] = int(getattr(reader, 'Channels', None)) \
                    if getattr(reader, 'Channels', None) is not None else None
                common['n_timepoints'] = int(getattr(reader, 'TimePoints', None)) \
                    if getattr(reader, 'TimePoints', None) is not None else None
                shp = getattr(reader, 'shape', None)  # (T, C, Z, Y, X)
                if shp is not None and len(shp) == 5:
                    common['dimensions'] = {
                        't': int(shp[0]), 'c': int(shp[1]), 'z': int(shp[2]),
                        'y': int(shp[3]), 'x': int(shp[4]),
                    }
                    common['n_z'] = int(shp[2])
                dt = getattr(reader, 'dtype', None)
                if dt is not None:
                    import numpy as _np
                    common['bit_depth'] = int(_np.dtype(dt).itemsize * 8)
            except Exception:
                pass

    except Exception:
        pass
    finally:
        if opened_here and hf is not None:
            try:
                hf.close()
            except Exception:
                pass

    return {'common': common, 'raw': raw}


# ---------------------------------------------------------------------------
# TIFF / OME-TIFF
# ---------------------------------------------------------------------------

def _parse_voxelsize(page_name):
    """Parse ``VoxelSize=0.0977x0.0977x19.0000`` (µm) out of a TIFF ``PageName`` tag. Returns
    ``(x, y, z)`` floats or ``None`` when the tag carries no VoxelSize. ISS Vista writes the z-step here,
    where the structured pixel-size object never sees it."""
    if not page_name:
        return None
    import re
    m = re.search(r'VoxelSize\s*=\s*([\d.]+)\s*[xX]\s*([\d.]+)\s*[xX]\s*([\d.]+)', str(page_name))
    if not m:
        return None
    try:
        return (float(m.group(1)), float(m.group(2)), float(m.group(3)))
    except ValueError:
        return None


def extract_tiff_metadata(file_path):
    """Extract normalised metadata from a TIFF / OME-TIFF using tifffile."""
    common = _empty_common(file_path)
    raw = {}
    try:
        import tifffile
    except Exception:
        return {'common': common, 'raw': raw}

    try:
        with tifffile.TiffFile(file_path) as t:
            page = t.pages[0]
            for tag in page.tags:
                raw[tag.name] = _safe_str(tag.value)

            # Dimensions / bit depth.
            w = _safe_float(page.tags.get('ImageWidth').value) if page.tags.get('ImageWidth') else None
            h = _safe_float(page.tags.get('ImageLength').value) if page.tags.get('ImageLength') else None
            bits = page.tags.get('BitsPerSample')
            if bits is not None:
                bv = bits.value
                common['bit_depth'] = int(bv[0]) if isinstance(bv, (tuple, list)) else int(bv)

            # Pixel size from XResolution/ResolutionUnit.
            xres = page.tags.get('XResolution')
            unit_tag = page.tags.get('ResolutionUnit')
            if xres is not None and xres.value is not None:
                val = xres.value
                if isinstance(val, (tuple, list)) and len(val) == 2 and val[1] != 0:
                    ppu = float(val[0]) / float(val[1])
                else:
                    ppu = float(val)
                if ppu > 0:
                    if unit_tag is not None and unit_tag.value is not None:
                        unit = int(unit_tag.value)
                    else:
                        unit = 2
                    microns_per_unit = {3: 10000.0, 2: 25400.0}.get(unit)
                    if microns_per_unit:
                        px = microns_per_unit / ppu
                        if 1e-4 < px < 1e4:
                            common['pixel_size_um'] = px
                            common['pixel_size_source'] = 'tiff_tags'

            # PageName may carry 'VoxelSize=X x Y x Z' (µm) — the z-step (which the structured pixel-size
            # object misses) plus a CROSS-CHECK on the in-plane size. Fill z_step_um if absent; reconcile X
            # against XResolution — agreement confirms it, a disagreement beyond tolerance is recorded as a
            # conflict rather than silently preferring one (per the metadata-validity rule).
            _pn = page.tags.get('PageName')
            _vox = _parse_voxelsize(_safe_str(_pn.value)) if _pn is not None else None
            if _vox is not None:
                _vx, _vy, _vz = _vox
                if _vz and _vz > 0 and common.get('z_step_um') is None:
                    common['z_step_um'] = _vz
                if _vx and _vx > 0:
                    _existing = common.get('pixel_size_um')
                    if _existing is None:
                        common['pixel_size_um'] = _vx
                        common['pixel_size_source'] = 'page_name_voxelsize'
                    elif abs(_vx - _existing) / max(_existing, 1e-9) > 0.02:
                        common.setdefault('conflicts', []).append(
                            f"pixel size: XResolution {_existing:.5f} µm/px vs PageName VoxelSize "
                            f"{_vx:.5f} µm/px disagree by more than 2%")

            # Curated string fields.
            def _tagval(name):
                tg = page.tags.get(name)
                return _safe_str(tg.value) if tg is not None else None
            common['software'] = _tagval('Software')
            common['acquisition_date'] = _tagval('DateTime')
            desc = _tagval('ImageDescription')
            if desc:
                # Parse the description into structured fields instead of
                # dropping the whole blob into one place.
                _parsed = parse_description_blob(desc)
                if _parsed:
                    raw['acquisition'] = _parsed
                    if common.get('exposure_s') is None:
                        _e = _safe_float(_parsed.get('Exposure-ms') or _parsed.get('ExposureTime'))
                        if _e is not None:
                            common['exposure_s'] = _e / 1e3 if _e > 5 else _e
                # modality should be a short descriptor — not a JSON/XML/ImageJ
                # blob. Only accept a short, plain token here.
                if len(desc) <= 40 and '=' not in desc and '{' not in desc and '<' not in desc:
                    common['modality'] = desc

            # Dimensions (single-page TIFF is 2D; series may add T/C/Z).
            n_pages = len(t.pages)
            common['dimensions'] = {
                't': None, 'c': None, 'z': None,
                'y': int(h) if h else None, 'x': int(w) if w else None,
            }
            if n_pages > 1:
                raw['n_pages'] = str(n_pages)

            # Measured per-frame acquisition timing from MicroManager page tags,
            # if present (authoritative cadence; see _extract_mm_frame_times_from_tiff).
            try:
                # Bounded at load — sampling a prefix, never scanning every page of a large stack (item 6).
                _mm = _extract_mm_frame_times_from_tiff(file_path, max_pages=_LOAD_PAGE_SAMPLE_CAP)
                if _mm:
                    if _mm.get('frame_interval_s'):
                        common['frame_interval_s'] = float(_mm['frame_interval_s'])
                        common['frame_interval_source'] = _mm.get('source')
                        common['frame_interval_iqr_s'] = _mm.get('frame_interval_iqr_s')
                        common['frame_deltas_s'] = _mm.get('frame_deltas_s')
                    if _mm.get('pages_sampled') is not None:
                        raw['frame_times_pages_sampled'] = str(_mm['pages_sampled'])
                        raw['frame_times_pages_total'] = str(_mm.get('pages_total'))
                    for _k in ('exposure_s', 'camera_name',
                               'acquisition_start_time', 'n_frames'):
                        if _mm.get(_k) is not None and common.get(_k) is None:
                            common[_k] = _mm[_k]
            except Exception:
                debug_log("metadata_extract: TIFF MicroManager frame-times read failed")
    except Exception:
        pass

    return {'common': common, 'raw': raw}


# ---------------------------------------------------------------------------
# AICSImage (CZI, OME-TIFF, and TIFFs AICSImage can parse)
# ---------------------------------------------------------------------------

# The load path samples at most this many page-tag records to estimate the cadence — reading EVERY page of a
# large stack at load is the thing deep_metadata item 6 forbids (a 10k-frame stack would parse 10k IFDs just to
# open). A bounded prefix gives the same median cadence for a constant-cadence acquisition; a caller that truly
# needs every plane's record passes ``max_pages=None`` (the on-demand/full read).
_LOAD_PAGE_SAMPLE_CAP = 64


def _extract_mm_frame_times_from_tiff(file_path, max_pages=None):
    """Read per-frame acquisition timing directly from a (MicroManager) TIFF.

    MicroManager writes a per-page ``MicroManagerMetadata`` tag containing
    ``ElapsedTime-ms`` (a real timestamp) and ``Exposure-ms``. This is the
    ground-truth cadence — it reflects what the camera actually did, unlike the
    nominal ``Interval_ms`` in the summary (often 0 / unset) or a free-text OME
    ``<Description>`` like "500ms interval" (which the hardware may ignore).

    ``max_pages`` bounds how many page-tag records are read: the load path passes
    ``_LOAD_PAGE_SAMPLE_CAP`` so opening a huge stack never parses every IFD, while
    ``None`` reads all pages on demand. The returned dict reports ``pages_sampled``
    (records read) and ``pages_total`` so the bound is inspectable.

    Returns a dict with keys (any of which may be absent):
      frame_interval_s, frame_interval_iqr_s, frame_deltas_s (list),
      exposure_s, camera_name, acquisition_start_time, n_frames,
      pages_sampled, pages_total, source
    or ``None`` if no per-frame timing is found.
    """
    try:
        import tifffile
        import numpy as _np
    except Exception:
        return None

    elapsed_ms = []
    exposure_ms = None
    camera_name = None
    start_time = None
    n_pages = 0
    pages_read = 0
    try:
        with tifffile.TiffFile(file_path) as t:
            n_pages = len(t.pages)
            limit = n_pages if max_pages is None else min(n_pages, max_pages)
            pages_read = limit                            # the bound actually applied (≤ max_pages)
            for i in range(limit):
                pg = t.pages[i]
                tag = pg.tags.get('MicroManagerMetadata')
                if tag is None or not isinstance(tag.value, dict):
                    continue
                mm = tag.value
                et = mm.get('ElapsedTime-ms')
                if et is not None:
                    try:
                        elapsed_ms.append(float(et))
                    except (TypeError, ValueError):
                        pass
                if exposure_ms is None and mm.get('Exposure-ms') is not None:
                    try:
                        exposure_ms = float(mm.get('Exposure-ms'))
                    except (TypeError, ValueError):
                        pass
                if start_time is None and mm.get('ReceivedTime'):
                    start_time = _safe_str(mm.get('ReceivedTime'))
                if camera_name is None:
                    # The camera-specific keys are prefixed with the device
                    # name, e.g. 'Blackfly S BFS-U3-16S2M-Exposure Mode'.
                    for k in mm.keys():
                        if k.endswith('-Exposure Mode'):
                            camera_name = k.rsplit('-Exposure Mode', 1)[0]
                            break
    except Exception:
        return None

    if len(elapsed_ms) < 2:
        # Still return exposure/camera if we found them on a single page.
        if exposure_ms is not None or camera_name is not None:
            out = {'source': 'micromanager_page_tags', 'n_frames': n_pages or None,
                   'pages_sampled': pages_read, 'pages_total': n_pages}
            if exposure_ms is not None:
                out['exposure_s'] = exposure_ms / 1e3
            if camera_name is not None:
                out['camera_name'] = camera_name
            if start_time is not None:
                out['acquisition_start_time'] = start_time
            return out
        return None

    arr = _np.asarray(elapsed_ms, dtype=float)
    deltas_ms = _np.diff(arr)
    deltas_ms = deltas_ms[deltas_ms > 0]
    if deltas_ms.size == 0:
        return None
    deltas_s = deltas_ms / 1e3
    median_s = float(_np.median(deltas_s))
    q1, q3 = _np.percentile(deltas_s, [25, 75])
    iqr_s = float(q3 - q1)

    out = {
        'frame_interval_s': median_s,
        'frame_interval_iqr_s': iqr_s,
        'frame_deltas_s': [float(x) for x in deltas_s],
        'n_frames': n_pages or (len(elapsed_ms)),
        'pages_sampled': pages_read,
        'pages_total': n_pages,
        'source': 'micromanager_elapsedtime',
    }
    if exposure_ms is not None:
        out['exposure_s'] = exposure_ms / 1e3
    if camera_name is not None:
        out['camera_name'] = camera_name
    if start_time is not None:
        out['acquisition_start_time'] = start_time
    return out


def reconcile_frame_interval(nominal_s, nominal_source, derived_s, derived_source,
                             derived_iqr_s=None, rel_tol=0.15):
    """Decide the frame interval from a NOMINAL declared value and a DERIVED
    (per-frame-timestamp) cadence, preferring the measurement and flagging a
    conflict. Pure function — unit tested in the navigator package.

    Rules (the module's own stated principle, made operational):
      * Per-frame timestamps win whenever present. A declared interval is a
        claim; the timestamps are what the microscope actually did.
      * If both exist and disagree by more than ``rel_tol`` (relative to the
        derived value), or the nominal lies outside the derived value's IQR band,
        mark it inconsistent and keep the derived value.
      * If only one exists, use it.

    Returns a dict with frame_interval_s, frame_interval_source,
    frame_interval_nominal_s, frame_interval_inconsistent, and a message
    (non-empty only when inconsistent).
    """
    def _pos(x):
        try:
            x = float(x)
            return x if (x > 0 and x == x) else None
        except (TypeError, ValueError):
            return None

    nominal_s = _pos(nominal_s)
    derived_s = _pos(derived_s)
    derived_iqr_s = _pos(derived_iqr_s)

    result = dict(frame_interval_s=None, frame_interval_source=None,
                  frame_interval_nominal_s=nominal_s,
                  frame_interval_inconsistent=False, message='')

    if derived_s is not None:
        result['frame_interval_s'] = derived_s
        result['frame_interval_source'] = derived_source
        if nominal_s is not None:
            # Relative disagreement is the criterion. (IQR is retained for
            # provenance but deliberately NOT used as a second trigger: a very
            # regular cadence has a tiny IQR, which would fire on a harmless
            # rounding-level nominal mismatch and train the warning away.)
            rel = abs(derived_s - nominal_s) / derived_s
            if rel > rel_tol:
                result['frame_interval_inconsistent'] = True
                result['message'] = (
                    f"Frame-interval mismatch: the file declares "
                    f"{nominal_s:g} s/frame ({nominal_source or 'nominal'}), but the "
                    f"per-frame timestamps imply {derived_s:g} s/frame "
                    f"({derived_source or 'measured'}). Using the measured "
                    f"{derived_s:g} s. Every dynamics result scales with this — "
                    f"set it manually in the panel to override.")
    elif nominal_s is not None:
        result['frame_interval_s'] = nominal_s
        result['frame_interval_source'] = nominal_source
    return result


def _extract_frame_interval_s(image):
    """Best-effort frame interval from an AICSImage's OME model.

    Returns a dict separating the NOMINAL declared interval from the DERIVED
    per-frame cadence, so the caller can reconcile them:
      nominal_s / nominal_source : OME Pixels TimeIncrement, or MicroManager
        Interval_ms (>0) — a declared claim.
      derived_s / derived_source / derived_iqr_s : median of consecutive
        per-plane DeltaT values — the actual cadence, which is authoritative.

    Historically this returned TimeIncrement FIRST and only fell back to DeltaT,
    which contradicted the rule below and reported a nominal 0.5 s over a real
    0.1 s cadence. Both are now extracted and the caller prefers the timestamps.

    **Only PER-FRAME TIMESTAMPS can be trusted. Everything else can lie, and on real files
    it does.**

    A worked example, from ``3_30_hr_1_MMStack_Pos0_ome2.tif`` — a MicroManager acquisition
    that was subsequently opened and re-saved in ImageJ. Its surviving metadata contains
    **two different, both-wrong answers**, and no right one:

    * ``"Interval_ms": 0.0`` — the field that is *supposed* to hold the cadence. It is
      **zero**. A scraper that trusts it gets a meaningless interval; that is why source (3)
      above rejects a non-positive value, and it must never be relaxed.
    * ``"Acquisition comments: 500ms interval"`` — a **free-text human comment**. It *reads*
      as authoritative, it is the only number in the file that looks like an interval, and
      **it is wrong**: the true cadence was 100 ms. It is a note somebody typed, not a
      measurement the microscope made.
    * ``"CustomIntervals_ms": []`` — empty.

    The real per-frame ``ElapsedTime-ms`` values were in MicroManager's per-image metadata,
    and **ImageJ stripped them on re-save** (``tf.is_micromanager`` is False for this file
    even though it came from MicroManager). What survives is a 1070-byte *summary* blob.

    **So: a plausible-looking interval from a summary field or a comment is not evidence.**
    If per-frame timestamps are absent, this function returns ``(None, None)`` — and the
    caller must ASK, not assume. A wrong frame interval scales the diffusion coefficient
    linearly and the viscosity inversely: reading 500 ms where the truth is 100 ms inflates
    the reported viscosity **five-fold**.
    """
    import re as _re

    def _to_seconds(val, unit):
        if val is None:
            return None
        v = float(val)
        u = (unit or 's').lower()
        if u in ('ms', 'millisecond', 'milliseconds'):
            return v / 1e3
        if u in ('us', 'µs', 'microsecond', 'microseconds'):
            return v / 1e6
        if u in ('min', 'minute', 'minutes'):
            return v * 60.0
        return v  # seconds (default)

    out = dict(nominal_s=None, nominal_source=None,
               derived_s=None, derived_source=None, derived_iqr_s=None)

    # Structured OME model via the ome-types object, if present.
    try:
        ome = getattr(image, 'ome_metadata', None)
        if ome is not None and hasattr(ome, 'images') and ome.images:
            px = ome.images[0].pixels
            # NOMINAL: declared TimeIncrement.
            ti = getattr(px, 'time_increment', None)
            tiu = getattr(px, 'time_increment_unit', None)
            s = _to_seconds(ti, getattr(tiu, 'value', tiu) if tiu else None)
            if s and s > 0:
                out['nominal_s'] = s
                out['nominal_source'] = 'ome_time_increment'
            # DERIVED: actual cadence from per-plane DeltaT timestamps.
            planes = getattr(px, 'planes', None) or []
            deltas = [getattr(p, 'delta_t', None) for p in planes]
            deltas = [float(d) for d in deltas if d is not None]
            if len(deltas) >= 2:
                import numpy as _np
                arr = _np.sort(_np.asarray(deltas, dtype=float))
                diffs = _np.diff(arr)
                diffs = diffs[diffs > 0]
                if diffs.size:
                    out['derived_s'] = float(_np.median(diffs))
                    out['derived_source'] = 'ome_delta_t'
                    if diffs.size >= 4:
                        q1, q3 = _np.percentile(diffs, [25, 75])
                        out['derived_iqr_s'] = float(q3 - q1)
    except Exception:
        pass

    # MicroManager Interval_ms in the raw metadata string — a declared value, so
    # it is treated as NOMINAL (only used if no OME nominal was found).
    if out['nominal_s'] is None:
        try:
            md = image.metadata
            s = str(md) if md is not None else ''
            m = _re.search(r'"?Interval_ms"?\s*[:=]\s*([0-9.]+)', s)
            if m:
                _iv = float(m.group(1)) / 1e3
                # Interval_ms is often 0 / unset; a zero interval is meaningless.
                if _iv > 0:
                    out['nominal_s'] = _iv
                    out['nominal_source'] = 'micromanager_interval_ms'
        except Exception:
            pass

    return out


def extract_reader_metadata(file_path, image=None):
    """Extract normalised metadata from an AICSImage object (CZI/OME-TIFF).

    Falls back to opening the file if no image is supplied. The OME model
    exposes structured pixel size, dimensions, and channel info.
    """
    common = _empty_common(file_path)
    raw = {}
    try:
        if image is None:
            image = open_image(file_path)

        # Dimensions.
        try:
            dims = image.dims
            common['dimensions'] = {
                't': int(getattr(dims, 'T', 1)),
                'c': int(getattr(dims, 'C', 1)),
                'z': int(getattr(dims, 'Z', 1)),
                'y': int(getattr(dims, 'Y', 0)) or None,
                'x': int(getattr(dims, 'X', 0)) or None,
            }
            common['n_channels'] = common['dimensions']['c']
            common['n_timepoints'] = common['dimensions']['t']
            common['n_z'] = common['dimensions']['z']
        except Exception:
            pass

        # Pixel size.
        try:
            px = image.physical_pixel_sizes
            y = getattr(px, 'Y', None)
            if y:
                common['pixel_size_um'] = float(y)
                common['pixel_size_source'] = 'ome_metadata'
            # Z step (µm) from the same structured pixel-size object.
            z = getattr(px, 'Z', None)
            if z:
                common['z_step_um'] = float(z)
        except Exception:
            debug_log("metadata_extract: OME pixel-size / z-step read failed")

        # Frame interval (seconds). Precedence, most authoritative first:
        #   1. Measured per-frame MicroManager ElapsedTime-ms deltas (the actual
        #      cadence the camera achieved — reads the TIFF page tags directly).
        #   2. OME structured TimeIncrement.
        #   3. OME per-plane DeltaT differences.
        #   4. MicroManager Interval_ms (only if > 0).
        # Free-text OME <Description> is never parsed for timing. The measured
        # deltas, IQR, exposure, camera, start time and frame count are all kept
        # for provenance and for MSD fitting against the true (non-uniform) cadence.
        # Gather BOTH a measured cadence (from per-frame timestamps) and any
        # nominal declared interval, then reconcile — timestamps win, and a
        # disagreement is flagged rather than silently resolved.
        _derived_s = _derived_src = _derived_iqr = None
        _nominal_s = _nominal_src = None
        try:
            # Bounded at load (item 6) — a prefix sample, not every page of a large stack.
            _mm = _extract_mm_frame_times_from_tiff(file_path, max_pages=_LOAD_PAGE_SAMPLE_CAP)
            if _mm and _mm.get('frame_interval_s'):
                _derived_s = float(_mm['frame_interval_s'])
                _derived_src = _mm.get('source')
                _derived_iqr = _mm.get('frame_interval_iqr_s')
                common['frame_interval_iqr_s'] = _mm.get('frame_interval_iqr_s')
                common['frame_deltas_s'] = _mm.get('frame_deltas_s')
            if _mm:
                for _k in ('exposure_s', 'camera_name',
                           'acquisition_start_time', 'n_frames'):
                    if _mm.get(_k) is not None and common.get(_k) is None:
                        common[_k] = _mm[_k]
        except Exception:
            debug_log("metadata_extract: MicroManager frame-times read failed")
        try:
            _ome = _extract_frame_interval_s(image)
            _nominal_s, _nominal_src = _ome.get('nominal_s'), _ome.get('nominal_source')
            # If no per-frame timestamps came from the TIFF pages, use the OME
            # DeltaT cadence as the measured value.
            if _derived_s is None and _ome.get('derived_s'):
                _derived_s = _ome['derived_s']
                _derived_src = _ome.get('derived_source')
                _derived_iqr = _ome.get('derived_iqr_s')
        except Exception:
            debug_log("metadata_extract: OME frame-interval read failed")

        _rec = reconcile_frame_interval(_nominal_s, _nominal_src,
                                        _derived_s, _derived_src, _derived_iqr)
        if _rec['frame_interval_s'] is not None:
            common['frame_interval_s'] = _rec['frame_interval_s']
            common['frame_interval_source'] = _rec['frame_interval_source']
        common['frame_interval_nominal_s'] = _rec['frame_interval_nominal_s']
        common['frame_interval_inconsistent'] = _rec['frame_interval_inconsistent']
        if _rec['frame_interval_inconsistent']:
            _warn_frame_interval(_rec['message'])

        # Channel names.
        try:
            ch = list(getattr(image, 'channel_names', []) or [])
            if ch:
                raw['channel_names'] = ', '.join(str(c) for c in ch)
        except Exception:
            pass

        # Bit depth from dtype.
        try:
            import numpy as _np
            common['bit_depth'] = int(_np.dtype(image.dtype).itemsize * 8)
        except Exception:
            pass

        # Raw OME metadata as a string (best-effort) — AND a structured parse of
        # it, so the panel/export get queryable fields instead of one text blob.
        try:
            md = image.metadata
            if md is not None:
                _blob = str(md)
                raw['ome_metadata'] = _blob[:20000]
                _parsed = parse_description_blob(_blob)
                if _parsed:
                    raw['acquisition'] = _parsed
                    if common.get('exposure_s') is None:
                        _exp = _parsed.get('ExposureTime') or _parsed.get('Exposure-ms')
                        _e = _safe_float(_exp)
                        if _e is not None:
                            common['exposure_s'] = _e / 1e3 if _e > 5 else _e
                # Per-channel + instrument hierarchy the flat schema cannot express (additive — the flat
                # fields above are untouched). Only attach non-empty structure so a non-OME blob adds nothing.
                if _blob.lstrip()[:5].lower().startswith('<?xml') or '<ome' in _blob.lower():
                    _struct = parse_ome_channels_and_instrument(_blob)
                    if _struct['channels']:
                        raw['channels'] = _struct['channels']
                    _inst = {k: v for k, v in _struct['instrument'].items() if v is not None}
                    if _inst:
                        raw['instrument'] = _inst
                        # Lift the objective NA into the flat schema when it was otherwise unknown (a real
                        # consumer: the Nyquist/diffraction-limit QC that had numerical_aperture=None).
                        if common.get('numerical_aperture') is None and _inst.get('lens_na') is not None:
                            common['numerical_aperture'] = _inst['lens_na']
        except Exception:
            pass
    except Exception:
        pass

    return {'common': common, 'raw': raw}


# ---------------------------------------------------------------------------
# Reader-independent metadata merge (Part 2 — the metadata source is not the pixel reader)
# ---------------------------------------------------------------------------

#: Numeric fields where a small relative disagreement between sources is rounding, not a real conflict.
_MERGE_NUMERIC_TOL = 1e-3
#: Fields whose conflict must SURFACE loudly (a wrong scale corrupts every physical measurement downstream).
_MERGE_SURFACE_CONFLICT = ('pixel_size_um',)


def _values_conflict(a, b, *, tol=_MERGE_NUMERIC_TOL):
    """Whether two meaningful values disagree. Numbers within a relative tolerance do NOT conflict (0.1035 vs
    0.10350001 is the same reading); strings compare case-insensitively trimmed; everything else by equality."""
    fa, fb = _safe_float(a), _safe_float(b)
    if fa is not None and fb is not None:
        if fa == 0 and fb == 0:
            return False
        denom = max(abs(fa), abs(fb)) or 1.0
        return abs(fa - fb) / denom > tol
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() != b.strip().lower()
    return a != b


def merge_metadata_sources(sources, *, surface_conflict_fields=_MERGE_SURFACE_CONFLICT):
    """Merge several metadata sources by **field-level precedence**, recording where each field came from and
    every disagreement — so the metadata source is decoupled from the pixel reader (the best reader for pixels
    is not always the best for metadata).

    ``sources`` is an ordered list of ``(source_name, common_dict)``, **highest precedence first**. For each
    field the first source with a *meaningful* value (``metadata_validity.is_meaningful``) wins. When a
    later source offers a DIFFERENT meaningful value that is a **finding**, not something to resolve quietly:
    it is recorded with both values, the winner, and the reason. A ``pixel_size_um`` conflict is marked
    ``surfaced`` so the pixel-size gate sees it rather than it being buried.

    Returns ``{'common': {field: winner}, 'sources': {field: source_name}, 'conflicts': [...]}``. Pure and
    Qt-free — the orchestration that decides WHICH readers to ask lives in ``extract_metadata_merged``."""
    from pycat.utils.metadata_validity import is_meaningful

    merged, provenance, conflicts = {}, {}, []
    all_fields = []
    for _name, common in sources:
        for f in (common or {}):
            if f not in all_fields:
                all_fields.append(f)

    for field in all_fields:
        # Every source that offers a meaningful value for this field, in precedence order.
        offered = [(name, (common or {})[field]) for name, common in sources
                   if field in (common or {}) and is_meaningful(field, (common or {})[field])]
        if not offered:
            continue
        winner_name, winner_val = offered[0]
        merged[field] = winner_val
        provenance[field] = winner_name

        disagreeing = [(n, v) for n, v in offered[1:] if _values_conflict(winner_val, v)]
        if disagreeing:
            conflicts.append({
                'field': field,
                'values': {winner_name: winner_val, **{n: v for n, v in disagreeing}},
                'winner': winner_val,
                'winner_source': winner_name,
                'reason': f"field-level precedence: '{winner_name}' over "
                          + ', '.join(f"'{n}'" for n, _ in disagreeing),
                'surfaced': field in surface_conflict_fields,
            })
    return {'common': merged, 'sources': provenance, 'conflicts': conflicts}


def _default_metadata_sources(file_path, *, image=None, reader=None, width_px=None):
    """The ordered ``(name, callable)`` metadata sources to try for ``file_path``, independent of which reader
    displays the pixels best. Each callable returns a ``{'common','raw'}`` result (or raises — the caller
    records the failure with a reason, never swallows it). Ordered highest-precedence first per format."""
    ext = os.path.splitext(file_path)[1].lower() if file_path else ''
    if ext == '.ims':
        return [('ims_hdf5', lambda: extract_ims_metadata(file_path, reader=reader, width_px=width_px))]
    structured = ('ome_structured', lambda: extract_reader_metadata(file_path, image=image))
    tiff = ('tifffile', lambda: extract_tiff_metadata(file_path))
    if image is not None:
        # An already-open pixel reader was handed in: its own structured metadata stays HIGHEST precedence, so
        # no value that is already correct on the live load path can move. For a TIFF we also ask tifffile —
        # it reads baseline tags the structured reader skips, but as the lower-precedence source it only FILLS
        # fields the reader left None, it never overrides them.
        return [structured, tiff] if ext in ('.tif', '.tiff') else [structured]
    if ext in ('.tif', '.tiff'):
        # No open handle: tifffile is primary (it reads the baseline tags), the structured/OME reader fills
        # what tifffile cannot — the same precedence the dispatcher used for its pixel-size fallback.
        return [tiff, structured]
    return [structured]


def extract_metadata_merged(file_path, *, image=None, reader=None, width_px=None, sources=None):
    """Metadata pulled from wherever it parses best, independent of the pixel reader (Part 2). Asks each
    available source, merges by field-level precedence with recorded per-field provenance and reported
    conflicts, and — critically — **skips a failing source with a recorded reason instead of a bare
    ``except: pass``**. The pixel reader is unaffected; display still goes through whichever reader handles
    the pixels best.

    ``sources`` (an ordered list of ``(name, callable)`` each returning ``{'common','raw'}``) is injectable
    for testing; by default it is resolved from the extension. Returns the usual ``{'common','raw'}`` with the
    merge trail under ``raw['metadata_sources']`` / ``raw['metadata_conflicts']`` / ``raw['source_failures']``,
    and the winning-source raw blobs preserved under ``raw['raw_by_source']``."""
    srcs = sources if sources is not None else _default_metadata_sources(
        file_path, image=image, reader=reader, width_px=width_px)

    commons, raw_by_source, failures = [], {}, []
    for name, fn in srcs:
        try:
            res = fn() or {}
            commons.append((name, res.get('common') or {}))
            raw_by_source[name] = res.get('raw') or {}
        except Exception as exc:      # broad-ok: optional_probe — one source failing must not lose the others
            failures.append({'source': name, 'error': f"{type(exc).__name__}: {exc}"})

    merged = merge_metadata_sources(commons)

    # Preserve each source's top-level raw keys (highest-precedence source last, so it wins) BEFORE attaching
    # the merge trail — consumers read raw['channels'] / raw['instrument'] (metadata_contradictions) and
    # _fill_scan_acquisition_fields scans flat raw tags, so a merged result must carry them, not bury them
    # under raw_by_source. The trail keys are attached last and never collide with real metadata fields.
    raw = {}
    for name, _fn in reversed(srcs):
        raw.update(raw_by_source.get(name, {}))
    raw['raw_by_source'] = raw_by_source
    raw['metadata_sources'] = merged['sources']
    raw['metadata_conflicts'] = merged['conflicts']
    if failures:
        raw['source_failures'] = failures
    return _fill_scan_acquisition_fields({'common': merged['common'], 'raw': raw})


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def extract_metadata(file_path, reader=None, image=None, width_px=None):
    """Return normalised {'common', 'raw'} metadata for any supported file.

    Routes through the reader-independent merge (Part 2): each metadata source for this file/handle is asked,
    the results are merged by field-level precedence with recorded per-field provenance and reported conflicts,
    and a failing source is skipped with a recorded reason instead of a swallowed ``except``. The source
    ordering (``_default_metadata_sources``) keeps the pixel reader's own metadata highest precedence whenever a
    handle is passed, so **no value that is already correct on the live load path moves**; a lower-precedence
    source only fills fields the primary left empty. ``reader`` (IMS) and ``image`` (AICSImage) let callers
    reuse an already-open handle instead of re-opening the file.
    """
    return extract_metadata_merged(file_path, image=image, reader=reader, width_px=width_px)


# ---------------------------------------------------------------------------
# Acquisition metadata comparison (multi-image side-by-side trust check)
# ---------------------------------------------------------------------------

# Fields that affect whether a QUANTITATIVE comparison between images is valid,
# with a severity: 'critical' differences undermine intensity/size comparison
# and warrant a strong warning; 'info' differences are worth noting but don't
# by themselves invalidate a comparison. (label, key, severity)
_COMPARE_FIELDS = [
    ('Objective',          'objective',           'critical'),
    ('Numerical aperture', 'numerical_aperture',  'critical'),
    ('Pixel size (um)',    'pixel_size_um',       'critical'),
    ('Exposure (s)',       'exposure_s',          'critical'),
    ('Excitation (nm)',    'excitation_nm',       'critical'),
    ('Emission (nm)',      'emission_nm',         'critical'),
    ('Bit depth',          'bit_depth',           'critical'),
    ('Modality',           'modality',            'critical'),
    ('Camera',             'camera_name',         'info'),
    ('Z step (um)',        'z_step_um',           'info'),
    ('Frame interval (s)', 'frame_interval_s',    'info'),
    ('Channels',           'n_channels',          'info'),
    ('Z planes',           'n_z',                 'info'),
    ('Software',           'software',            'info'),
]


def _values_differ(values):
    """True if the non-None values are not all equal. All-None (unknown) is not
    a difference. A mix of None and a value IS flagged (one file lacks the info),
    but only among the values that are present do we compare equality."""
    present = [v for v in values if v is not None]
    if len(present) <= 1:
        return False
    first = present[0]
    # Numeric compare with tolerance; else exact.
    def _eq(a, b):
        try:
            return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(a)))
        except (TypeError, ValueError):
            return a == b
    return not all(_eq(first, v) for v in present)


def compare_acquisition_metadata(metadata_list, names=None):
    """Diff acquisition metadata across several images to flag whether a
    side-by-side comparison is trustworthy.

    Parameters
    ----------
    metadata_list : list[dict]
        One 'common' metadata dict per image (as produced by extract_metadata,
        i.e. the ['common'] block). Missing/None values are treated as unknown.
    names : list[str] or None
        Display names per image (e.g. layer or file names). Defaults to
        Image 1..N.

    Returns
    -------
    dict with:
      'names'          : the per-image names
      'rows'           : list of {label, key, severity, values, differs}
      'n_critical_diff': count of critical fields that differ
      'n_info_diff'    : count of info fields that differ
      'any_diff'       : bool
      'summary'        : one-line human-readable verdict
    """
    n = len(metadata_list)
    if names is None:
        names = [f"Image {i + 1}" for i in range(n)]
    rows = []
    n_crit = 0
    n_info = 0
    for label, key, severity in _COMPARE_FIELDS:
        values = [(_m or {}).get(key) for _m in metadata_list]
        differs = _values_differ(values)
        if differs:
            if severity == 'critical':
                n_crit += 1
            else:
                n_info += 1
        rows.append({'label': label, 'key': key, 'severity': severity,
                     'values': values, 'differs': differs})

    any_diff = (n_crit + n_info) > 0
    if n < 2:
        summary = "Need at least two images to compare."
    elif n_crit > 0:
        summary = (f"\u26a0 {n_crit} acquisition setting(s) differ that can make "
                   f"a quantitative comparison untrustworthy (intensity / size / "
                   f"resolution may not be directly comparable).")
    elif n_info > 0:
        summary = (f"{n_info} setting(s) differ, but none that critically affect "
                   f"quantitative comparison. Review the flagged rows.")
    else:
        summary = ("Acquisition settings match across the images \u2014 "
                   "comparison is on equal footing.")
    return {'names': names, 'rows': rows, 'n_critical_diff': n_crit,
            'n_info_diff': n_info, 'any_diff': any_diff, 'summary': summary}
