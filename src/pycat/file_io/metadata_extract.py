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

try:
    from pycat.utils.general_utils import debug_log
except Exception:  # pragma: no cover - fallback if utils unavailable
    def debug_log(context, exc=None):
        return


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
    }


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

            # Curated string fields.
            def _tagval(name):
                tg = page.tags.get(name)
                return _safe_str(tg.value) if tg is not None else None
            common['software'] = _tagval('Software')
            common['acquisition_date'] = _tagval('DateTime')
            desc = _tagval('ImageDescription')
            if desc:
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
                _mm = _extract_mm_frame_times_from_tiff(file_path)
                if _mm:
                    if _mm.get('frame_interval_s'):
                        common['frame_interval_s'] = float(_mm['frame_interval_s'])
                        common['frame_interval_source'] = _mm.get('source')
                        common['frame_interval_iqr_s'] = _mm.get('frame_interval_iqr_s')
                        common['frame_deltas_s'] = _mm.get('frame_deltas_s')
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

def _extract_mm_frame_times_from_tiff(file_path, max_pages=None):
    """Read per-frame acquisition timing directly from a (MicroManager) TIFF.

    MicroManager writes a per-page ``MicroManagerMetadata`` tag containing
    ``ElapsedTime-ms`` (a real timestamp) and ``Exposure-ms``. This is the
    ground-truth cadence — it reflects what the camera actually did, unlike the
    nominal ``Interval_ms`` in the summary (often 0 / unset) or a free-text OME
    ``<Description>`` like "500ms interval" (which the hardware may ignore).

    Returns a dict with keys (any of which may be absent):
      frame_interval_s, frame_interval_iqr_s, frame_deltas_s (list),
      exposure_s, camera_name, acquisition_start_time, n_frames, source
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
    try:
        with tifffile.TiffFile(file_path) as t:
            n_pages = len(t.pages)
            limit = n_pages if max_pages is None else min(n_pages, max_pages)
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
            out = {'source': 'micromanager_page_tags', 'n_frames': n_pages or None}
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
        'source': 'micromanager_elapsedtime',
    }
    if exposure_ms is not None:
        out['exposure_s'] = exposure_ms / 1e3
    if camera_name is not None:
        out['camera_name'] = camera_name
    if start_time is not None:
        out['acquisition_start_time'] = start_time
    return out


def _extract_frame_interval_s(image):
    """Best-effort frame interval (seconds) from an AICSImage's OME model.

    Tries several sources and returns (interval_s, source_str) or (None, None):
      1. OME Pixels TimeIncrement (with TimeIncrementUnit) — the nominal interval.
      2. Median of consecutive per-plane DeltaT values — the actual cadence.
      3. MicroManager 'Interval_ms' in the raw metadata string (only if > 0).
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

    # 1 & 2: structured OME model via the ome-types object, if present.
    try:
        ome = getattr(image, 'ome_metadata', None)
        if ome is not None and hasattr(ome, 'images') and ome.images:
            px = ome.images[0].pixels
            ti = getattr(px, 'time_increment', None)
            tiu = getattr(px, 'time_increment_unit', None)
            s = _to_seconds(ti, getattr(tiu, 'value', tiu) if tiu else None)
            if s and s > 0:
                return s, 'ome_time_increment'
            # per-plane DeltaT
            planes = getattr(px, 'planes', None) or []
            deltas = [getattr(p, 'delta_t', None) for p in planes]
            deltas = [float(d) for d in deltas if d is not None]
            if len(deltas) >= 2:
                import numpy as _np
                diffs = _np.diff(_np.sort(_np.asarray(deltas)))
                diffs = diffs[diffs > 0]
                if diffs.size:
                    # DeltaT unit is usually seconds in OME; assume s.
                    return float(_np.median(diffs)), 'ome_delta_t'
    except Exception:
        pass

    # 3: MicroManager Interval_ms in the raw metadata string.
    try:
        md = image.metadata
        s = str(md) if md is not None else ''
        m = _re.search(r'"?Interval_ms"?\s*[:=]\s*([0-9.]+)', s)
        if m:
            _iv = float(m.group(1)) / 1e3
            # Interval_ms is often 0 / unset in MicroManager summaries; a zero
            # interval is meaningless and must not be reported as a real cadence.
            if _iv > 0:
                return _iv, 'micromanager_interval_ms'
    except Exception:
        pass

    return None, None


def extract_aicsimage_metadata(file_path, image=None):
    """Extract normalised metadata from an AICSImage object (CZI/OME-TIFF).

    Falls back to opening the file if no image is supplied. The OME model
    exposes structured pixel size, dimensions, and channel info.
    """
    common = _empty_common(file_path)
    raw = {}
    try:
        if image is None:
            from aicsimageio import AICSImage
            image = AICSImage(file_path)

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
        try:
            _mm = _extract_mm_frame_times_from_tiff(file_path)
            if _mm:
                if _mm.get('frame_interval_s'):
                    common['frame_interval_s'] = float(_mm['frame_interval_s'])
                    common['frame_interval_source'] = _mm.get('source')
                    common['frame_interval_iqr_s'] = _mm.get('frame_interval_iqr_s')
                    common['frame_deltas_s'] = _mm.get('frame_deltas_s')
                for _k in ('exposure_s', 'camera_name',
                           'acquisition_start_time', 'n_frames'):
                    if _mm.get(_k) is not None and common.get(_k) is None:
                        common[_k] = _mm[_k]
        except Exception:
            debug_log("metadata_extract: MicroManager frame-times read failed")
        # If the measured cadence was unavailable, fall back to the OME model.
        if common.get('frame_interval_s') is None:
            try:
                _fi, _src = _extract_frame_interval_s(image)
                if _fi and _fi > 0:
                    common['frame_interval_s'] = float(_fi)
                    common['frame_interval_source'] = _src
            except Exception:
                debug_log("metadata_extract: OME frame-interval fallback failed")

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

        # Raw OME metadata as a string (best-effort).
        try:
            md = image.metadata
            if md is not None:
                raw['ome_metadata'] = str(md)[:20000]
        except Exception:
            pass
    except Exception:
        pass

    return {'common': common, 'raw': raw}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def extract_metadata(file_path, reader=None, image=None, width_px=None):
    """Return normalised {'common', 'raw'} metadata for any supported file.

    Routes by extension. ``reader`` (IMS) and ``image`` (AICSImage) let callers
    reuse an already-open handle instead of re-opening the file.
    """
    ext = os.path.splitext(file_path)[1].lower() if file_path else ''
    if ext == '.ims':
        return extract_ims_metadata(file_path, reader=reader, width_px=width_px)
    if image is not None:
        return extract_aicsimage_metadata(file_path, image=image)
    if ext in ('.tif', '.tiff'):
        # Prefer tifffile for plain TIFFs (reads baseline tags AICSImage skips).
        result = extract_tiff_metadata(file_path)
        # If pixel size still missing, try AICSImage as a secondary source.
        if result['common'].get('pixel_size_um') is None:
            try:
                aics = extract_aicsimage_metadata(file_path)
                if aics['common'].get('pixel_size_um') is not None:
                    result['common']['pixel_size_um'] = aics['common']['pixel_size_um']
                    result['common']['pixel_size_source'] = aics['common']['pixel_size_source']
            except Exception:
                pass
        return result
    # Fallback: try AICSImage for anything else (czi, lif, nd2, ...).
    return extract_aicsimage_metadata(file_path, image=image)
